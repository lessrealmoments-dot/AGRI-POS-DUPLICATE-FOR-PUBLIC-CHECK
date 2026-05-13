"""
br_bt — Branch Transfer stock-movement invariants (Phase 0 baseline).

Purpose
-------
Lock down the invariant that **inventory mutates in exactly one place** —
`_apply_receipt()` in `routes/branch_transfers.py` — and only when called
from either the exact-match `/receive` path or the variance
`/accept-receipt` path. Every other step in the lifecycle (request
creation, generate-transfer prefill, BTO draft, send, variance
`received_pending`, dispute) MUST leave inventory untouched.

This file is the prerequisite for the upcoming Stock-Request
Preparation/Confirmation feature: before we add `approved_qty` to
purchase_orders.items we need a hard regression net proving that no
existing flow moves stock outside `_apply_receipt`. If a future
refactor breaks the invariant, one of these tests will fail.

Reads behaviour from these live routes (not from documentation):
    * `routes/purchase_orders.create_purchase_order`         — request creation
    * `routes/purchase_orders.generate_branch_transfer_from_request` — prefill
    * `routes/branch_transfers.create_transfer`              — BTO draft
    * `routes/branch_transfers.send_transfer`                — dispatch
    * `routes/branch_transfers.receive_transfer`             — receive
    * `routes/branch_transfers.accept_receipt`               — variance accept
    * `routes/branch_transfers.dispute_receipt`              — variance dispute
    * `routes/branch_transfers.cancel_transfer`              — cancel

Zero-footprint policy
---------------------
Re-uses the module-scoped `tenant` fixture from conftest.py — same
throw-away org as br3/br5/br_prep. `cleanup_business_tenant()` purges
every row carrying our `organization_id` plus FK-linked rows in
`internal_invoices` / `doc_codes` on file teardown.

Idempotency
-----------
Every test creates its own product + transfer so re-running the file
is safe. None of the tests share mutable state with each other.
"""
import os
import sys

import pytest
from fastapi import HTTPException

BACKEND = os.path.join(os.path.dirname(__file__), "..", "..")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from config import _raw_db  # noqa: E402
from tests.business_regression._fixtures import seed_product  # noqa: E402
from routes.branch_transfers import (  # noqa: E402
    create_transfer,
    send_transfer,
    receive_transfer,
    accept_receipt,
    dispute_receipt,
    cancel_transfer,
)
from routes.purchase_orders import (  # noqa: E402
    create_purchase_order,
    generate_branch_transfer_from_request,
)


# ─────────────────────────────────────────────────────────────────────
# Local helpers — file-scoped, mirror those in br3 but adapted for
# request-PO + variance flows.
# ─────────────────────────────────────────────────────────────────────
async def _inv_qty(branch_id, product_id):
    row = await _raw_db.inventory.find_one(
        {"product_id": product_id, "branch_id": branch_id},
        {"_id": 0, "quantity": 1},
    )
    return float(row["quantity"]) if row else 0.0


async def _transfer_row(transfer_id):
    return await _raw_db.branch_transfer_orders.find_one(
        {"id": transfer_id}, {"_id": 0}
    ) or {}


async def _po_row(po_id):
    return await _raw_db.purchase_orders.find_one(
        {"id": po_id}, {"_id": 0}
    ) or {}


async def _movements_for_transfer(transfer_id):
    return await _raw_db.movements.find(
        {"reference_id": transfer_id}, {"_id": 0}
    ).to_list(None)


def _request_po_payload(requesting_branch_id, supply_branch_id,
                        product_id, product_name, qty, unit_price=60.0):
    """Body shape that `create_purchase_order` consumes for a
    branch_request PO (po_type=branch_request). Mirrors what
    `BranchTransferPage.handleSendRequest` posts."""
    return {
        "po_type": "branch_request",
        "branch_id": requesting_branch_id,        # WHO is asking
        "supply_branch_id": supply_branch_id,     # WHO supplies
        "vendor": "Internal — Branch Stock Request",
        "items": [{
            "product_id": product_id,
            "product_name": product_name,
            "unit": "pc",
            "quantity": qty,
            "unit_price": unit_price,
        }],
        "show_retail": True,
        "notes": "br_bt regression — created via create_purchase_order direct call",
    }


def _bto_payload(from_branch_id, to_branch_id,
                 product_id, product_name,
                 *, qty, capital=60.0, retail=100.0,
                 request_po_id="", request_po_number=""):
    """Body shape that `create_transfer` consumes — includes optional
    request linkage so we can prove BR3-style fulfillment status update
    when the BTO is born from a stock request."""
    return {
        "from_branch_id": from_branch_id,
        "to_branch_id": to_branch_id,
        "items": [{
            "product_id": product_id,
            "product_name": product_name,
            "sku": f"SKU-{product_id[-6:]}",
            "unit": "pc",
            "qty": qty,
            "branch_capital": capital,
            "transfer_capital": capital,
            "branch_retail": retail,
        }],
        "min_margin": 20,
        "request_po_id": request_po_id,
        "request_po_number": request_po_number,
        "notes": "br_bt regression",
    }


# ─────────────────────────────────────────────────────────────────────
# 1. Stock-request creation does NOT move inventory.
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_br_bt1_create_request_does_not_move_stock(
    tenant, record_result
):
    """
    POST /purchase-orders with po_type=branch_request should:
      * Create a PO row with status='requested', no inventory side-effect.
      * Leave both source (supply) and destination (requesting) stock
        completely untouched.

    Both branches' inventory must be invariant — this is what protects
    the "request is intent, not commitment" rule.
    """
    org_id     = tenant["org_id"]
    main       = tenant["branches"]["main"]    # supply branch (Branch B)
    b2         = tenant["branches"]["b2"]      # requesting branch (Branch A)
    owner_user = tenant["users"]["owner"]

    START_STOCK_SUPPLY = 50

    product_id = await seed_product(
        org_id, main, name="br_bt1 Product",
        price=100, stock=START_STOCK_SUPPLY, cost=60,
    )

    src_before = await _inv_qty(main, product_id)
    dst_before = await _inv_qty(b2,   product_id)

    payload = _request_po_payload(
        requesting_branch_id=b2, supply_branch_id=main,
        product_id=product_id, product_name="br_bt1 Product", qty=20,
    )
    po = await create_purchase_order(payload, user=owner_user)

    src_after = await _inv_qty(main, product_id)
    dst_after = await _inv_qty(b2,   product_id)
    po_row    = await _po_row(po["id"])

    base_ev = {
        "org_id": org_id,
        "source_branch_id": main,
        "destination_branch_id": b2,
        "request_po_id": po["id"],
        "request_po_number": po.get("po_number"),
        "product_id": product_id,
        "source_stock_before": src_before,
        "source_stock_after": src_after,
        "destination_stock_before": dst_before,
        "destination_stock_after": dst_after,
    }

    record_result(
        scenario="br_bt.1_create_request_does_not_move_stock",
        step="po_row_persisted_as_requested",
        expected={
            "po_created": True,
            "po_type": "branch_request",
            "status": "requested",
            "supply_branch_id": main,
        },
        actual={
            "po_created": bool(po_row),
            "po_type": po_row.get("po_type"),
            "status": po_row.get("status"),
            "supply_branch_id": po_row.get("supply_branch_id"),
        },
        evidence=base_ev,
    )
    record_result(
        scenario="br_bt.1_create_request_does_not_move_stock",
        step="source_supply_branch_stock_unchanged",
        expected={"stock": src_before},
        actual={"stock": src_after},
        evidence=base_ev,
    )
    record_result(
        scenario="br_bt.1_create_request_does_not_move_stock",
        step="destination_requesting_branch_stock_unchanged",
        expected={"stock": dst_before},
        actual={"stock": dst_after},
        evidence=base_ev,
    )

    assert po_row, "br_bt1: PO row not persisted"
    assert po_row.get("po_type") == "branch_request"
    assert po_row.get("status") == "requested", (
        f"br_bt1: expected status 'requested', got {po_row.get('status')!r}"
    )
    assert src_after == src_before, (
        f"br_bt1 INVARIANT BUG — supply stock moved on request creation: "
        f"{src_before} → {src_after}"
    )
    assert dst_after == dst_before, (
        f"br_bt1 INVARIANT BUG — requesting branch stock moved on request "
        f"creation: {dst_before} → {dst_after}"
    )


# ─────────────────────────────────────────────────────────────────────
# 2. Generate-transfer (PO → BTO prefill) does NOT move inventory.
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_br_bt2_generate_transfer_does_not_move_stock(
    tenant, record_result
):
    """
    POST /purchase-orders/{po_id}/generate-branch-transfer should:
      * Return a prefilled transfer payload (items with requested_qty,
        available_stock, default send qty, capital, retail).
      * Flip the PO status to 'in_progress' (re-entrant — idempotent).
      * Leave both source and destination inventory untouched. No BTO
        is created at this step yet — that's STEP 3's job.
    """
    org_id     = tenant["org_id"]
    main       = tenant["branches"]["main"]
    b2         = tenant["branches"]["b2"]
    owner_user = tenant["users"]["owner"]

    product_id = await seed_product(
        org_id, main, name="br_bt2 Product",
        price=100, stock=40, cost=60,
    )
    src_before = await _inv_qty(main, product_id)
    dst_before = await _inv_qty(b2,   product_id)

    po = await create_purchase_order(
        _request_po_payload(b2, main, product_id, "br_bt2 Product", qty=15),
        user=owner_user,
    )
    src_after_request = await _inv_qty(main, product_id)
    dst_after_request = await _inv_qty(b2,   product_id)

    prefill = await generate_branch_transfer_from_request(
        po["id"], user=owner_user
    )
    src_after_gen = await _inv_qty(main, product_id)
    dst_after_gen = await _inv_qty(b2,   product_id)
    po_row = await _po_row(po["id"])

    # No BTO should exist yet — generate-branch-transfer is a *prefill*,
    # not a commit. A BTO only gets a row when create_transfer is called.
    btos = await _raw_db.branch_transfer_orders.find(
        {"organization_id": org_id, "request_po_id": po["id"]},
        {"_id": 0, "id": 1},
    ).to_list(None)

    base_ev = {
        "org_id": org_id,
        "source_branch_id": main,
        "destination_branch_id": b2,
        "request_po_id": po["id"],
        "request_po_number": po.get("po_number"),
        "product_id": product_id,
        "source_stock_before": src_before,
        "source_stock_after": src_after_gen,
        "destination_stock_before": dst_before,
        "destination_stock_after": dst_after_gen,
        "po_status_after_generate": po_row.get("status"),
        "prefill_items_count": len(prefill.get("items", [])),
    }

    record_result(
        scenario="br_bt.2_generate_transfer_does_not_move_stock",
        step="po_status_remains_requested_until_bto_created",
        expected={"status": "requested"},
        actual={"status": po_row.get("status")},
        evidence=base_ev,
    )
    record_result(
        scenario="br_bt.2_generate_transfer_does_not_move_stock",
        step="prefill_contains_requested_and_available",
        expected={"items": 1, "has_requested_qty": True,
                  "has_available_stock": True, "has_send_qty": True},
        actual={
            "items": len(prefill.get("items", [])),
            "has_requested_qty":  bool(prefill.get("items") and
                "requested_qty" in prefill["items"][0]),
            "has_available_stock": bool(prefill.get("items") and
                "available_stock" in prefill["items"][0]),
            "has_send_qty": bool(prefill.get("items") and
                "qty" in prefill["items"][0]),
        },
        evidence=base_ev,
    )
    record_result(
        scenario="br_bt.2_generate_transfer_does_not_move_stock",
        step="source_supply_stock_unchanged_through_generate",
        expected={"stock": src_before},
        actual={"stock": src_after_gen},
        evidence=base_ev,
    )
    record_result(
        scenario="br_bt.2_generate_transfer_does_not_move_stock",
        step="destination_stock_unchanged_through_generate",
        expected={"stock": dst_before},
        actual={"stock": dst_after_gen},
        evidence=base_ev,
    )
    record_result(
        scenario="br_bt.2_generate_transfer_does_not_move_stock",
        step="generate_does_not_create_bto",
        expected={"bto_count": 0},
        actual={"bto_count": len(btos)},
        evidence=base_ev,
    )

    assert po_row.get("status") == "requested", (
        f"br_bt2 (post-Phase-0.5): PO status should stay 'requested' after "
        f"generate-transfer prefill (BTO has not been created yet), got "
        f"{po_row.get('status')!r}"
    )
    assert src_after_request == src_before, (
        "br_bt2 INVARIANT BUG — request creation moved supply stock"
    )
    assert dst_after_request == dst_before, (
        "br_bt2 INVARIANT BUG — request creation moved requesting stock"
    )
    assert src_after_gen == src_before, (
        f"br_bt2 INVARIANT BUG — generate-transfer moved supply stock "
        f"{src_before} → {src_after_gen}"
    )
    assert dst_after_gen == dst_before, (
        f"br_bt2 INVARIANT BUG — generate-transfer moved destination stock "
        f"{dst_before} → {dst_after_gen}"
    )
    assert len(btos) == 0, (
        "br_bt2 INVARIANT BUG — generate-transfer accidentally created a "
        "BTO row (it should be prefill-only)"
    )


# ─────────────────────────────────────────────────────────────────────
# 3. BTO draft creation does NOT move inventory.
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_br_bt3_bto_draft_create_does_not_move_stock(
    tenant, record_result
):
    """
    POST /branch-transfers creates a BTO with status='draft'. Inventory
    must stay invariant. Internal-invoice mirror is auto-created here
    (br3 already proves that — we only verify stock untouched).
    """
    org_id     = tenant["org_id"]
    main       = tenant["branches"]["main"]
    b2         = tenant["branches"]["b2"]
    owner_user = tenant["users"]["owner"]

    product_id = await seed_product(
        org_id, main, name="br_bt3 Product",
        price=100, stock=40, cost=60,
    )
    src_before = await _inv_qty(main, product_id)
    dst_before = await _inv_qty(b2,   product_id)

    draft = await create_transfer(
        _bto_payload(main, b2, product_id, "br_bt3 Product", qty=12),
        user=owner_user,
    )
    src_after = await _inv_qty(main, product_id)
    dst_after = await _inv_qty(b2,   product_id)
    bto_row   = await _transfer_row(draft["id"])
    movements = await _movements_for_transfer(draft["id"])

    base_ev = {
        "org_id": org_id,
        "source_branch_id": main,
        "destination_branch_id": b2,
        "transfer_id": draft["id"],
        "order_number": draft.get("order_number"),
        "product_id": product_id,
        "source_stock_before": src_before,
        "source_stock_after": src_after,
        "destination_stock_before": dst_before,
        "destination_stock_after": dst_after,
        "status_after": bto_row.get("status"),
        "movement_count": len(movements),
    }

    record_result(
        scenario="br_bt.3_bto_draft_create_does_not_move_stock",
        step="bto_persisted_as_draft",
        expected={"status": "draft"},
        actual={"status": bto_row.get("status")},
        evidence=base_ev,
    )
    record_result(
        scenario="br_bt.3_bto_draft_create_does_not_move_stock",
        step="source_stock_unchanged_after_draft",
        expected={"stock": src_before},
        actual={"stock": src_after},
        evidence=base_ev,
    )
    record_result(
        scenario="br_bt.3_bto_draft_create_does_not_move_stock",
        step="destination_stock_unchanged_after_draft",
        expected={"stock": dst_before},
        actual={"stock": dst_after},
        evidence=base_ev,
    )
    record_result(
        scenario="br_bt.3_bto_draft_create_does_not_move_stock",
        step="no_movement_rows_logged_on_draft",
        expected={"movement_count": 0},
        actual={"movement_count": len(movements)},
        evidence=base_ev,
    )

    assert bto_row.get("status") == "draft"
    assert src_after == src_before, (
        f"br_bt3 INVARIANT BUG — BTO draft created moved source stock "
        f"{src_before} → {src_after}"
    )
    assert dst_after == dst_before, (
        f"br_bt3 INVARIANT BUG — BTO draft created moved destination stock "
        f"{dst_before} → {dst_after}"
    )
    assert len(movements) == 0, (
        f"br_bt3 INVARIANT BUG — {len(movements)} movement rows logged "
        f"for a draft BTO (expected 0)"
    )


# ─────────────────────────────────────────────────────────────────────
# 4. Send does NOT move inventory; status flips to 'sent'.
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_br_bt4_send_does_not_move_stock(tenant, record_result):
    """
    POST /branch-transfers/{id}/send flips status draft → sent and emits
    a notification to the destination, but inventory MUST stay
    invariant until the destination receives.
    """
    org_id     = tenant["org_id"]
    main       = tenant["branches"]["main"]
    b2         = tenant["branches"]["b2"]
    owner_user = tenant["users"]["owner"]

    product_id = await seed_product(
        org_id, main, name="br_bt4 Product",
        price=100, stock=40, cost=60,
    )
    src_before = await _inv_qty(main, product_id)
    dst_before = await _inv_qty(b2,   product_id)

    draft = await create_transfer(
        _bto_payload(main, b2, product_id, "br_bt4 Product", qty=10),
        user=owner_user,
    )
    status_before_send = (await _transfer_row(draft["id"])).get("status")

    await send_transfer(draft["id"], user=owner_user)
    sent_row = await _transfer_row(draft["id"])
    src_after = await _inv_qty(main, product_id)
    dst_after = await _inv_qty(b2,   product_id)
    movements = await _movements_for_transfer(draft["id"])

    base_ev = {
        "org_id": org_id,
        "source_branch_id": main,
        "destination_branch_id": b2,
        "transfer_id": draft["id"],
        "order_number": sent_row.get("order_number"),
        "product_id": product_id,
        "source_stock_before": src_before,
        "source_stock_after": src_after,
        "destination_stock_before": dst_before,
        "destination_stock_after": dst_after,
        "status_before": status_before_send,
        "status_after": sent_row.get("status"),
        "movement_count": len(movements),
    }

    record_result(
        scenario="br_bt.4_send_does_not_move_stock",
        step="status_transitions_draft_to_sent",
        expected={"status_before": "draft", "status_after": "sent"},
        actual={"status_before": status_before_send,
                "status_after": sent_row.get("status")},
        evidence=base_ev,
    )
    record_result(
        scenario="br_bt.4_send_does_not_move_stock",
        step="source_stock_unchanged_on_send",
        expected={"stock": src_before},
        actual={"stock": src_after},
        evidence=base_ev,
    )
    record_result(
        scenario="br_bt.4_send_does_not_move_stock",
        step="destination_stock_unchanged_on_send",
        expected={"stock": dst_before},
        actual={"stock": dst_after},
        evidence=base_ev,
    )
    record_result(
        scenario="br_bt.4_send_does_not_move_stock",
        step="no_movement_rows_logged_on_send",
        expected={"movement_count": 0},
        actual={"movement_count": len(movements)},
        evidence=base_ev,
    )

    assert status_before_send == "draft"
    assert sent_row.get("status") == "sent", (
        f"br_bt4: status should flip to 'sent', got "
        f"{sent_row.get('status')!r}"
    )
    assert src_after == src_before, (
        f"br_bt4 INVARIANT BUG — /send moved source stock "
        f"{src_before} → {src_after}"
    )
    assert dst_after == dst_before, (
        f"br_bt4 INVARIANT BUG — /send moved destination stock "
        f"{dst_before} → {dst_after}"
    )
    assert len(movements) == 0


# ─────────────────────────────────────────────────────────────────────
# 5. Exact-match receive moves stock ONCE; re-call blocked.
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_br_bt5_exact_receive_moves_stock_once(tenant, record_result):
    """
    Exact-match /receive path: inventory moves exactly once.
      * source       -= qty
      * destination  += qty
      * 2 movement rows (transfer_out @ src, transfer_in @ dst)
      * Re-calling /receive on the same id is rejected by the
        "Transfer is not in a receivable state" status guard.
    """
    org_id     = tenant["org_id"]
    main       = tenant["branches"]["main"]
    b2         = tenant["branches"]["b2"]
    owner_user = tenant["users"]["owner"]

    QTY = 8
    product_id = await seed_product(
        org_id, main, name="br_bt5 Product",
        price=100, stock=30, cost=60,
    )
    src_before = await _inv_qty(main, product_id)
    dst_before = await _inv_qty(b2,   product_id)

    draft = await create_transfer(
        _bto_payload(main, b2, product_id, "br_bt5 Product", qty=QTY),
        user=owner_user,
    )
    await send_transfer(draft["id"], user=owner_user)

    await receive_transfer(
        draft["id"], data={"skip_receipt_check": True}, user=owner_user
    )
    src_after = await _inv_qty(main, product_id)
    dst_after = await _inv_qty(b2,   product_id)
    movements = await _movements_for_transfer(draft["id"])
    transfer_row = await _transfer_row(draft["id"])

    # Try to receive again — must be rejected.
    second_status = None
    second_detail = None
    try:
        await receive_transfer(
            draft["id"], data={"skip_receipt_check": True}, user=owner_user
        )
    except HTTPException as e:
        second_status = e.status_code
        second_detail = e.detail

    src_after_second = await _inv_qty(main, product_id)
    dst_after_second = await _inv_qty(b2,   product_id)
    movements_after_second = await _movements_for_transfer(draft["id"])

    base_ev = {
        "org_id": org_id,
        "source_branch_id": main,
        "destination_branch_id": b2,
        "transfer_id": draft["id"],
        "order_number": transfer_row.get("order_number"),
        "product_id": product_id,
        "transfer_qty": QTY,
        "source_stock_before": src_before,
        "source_stock_after": src_after,
        "source_stock_after_second_call": src_after_second,
        "destination_stock_before": dst_before,
        "destination_stock_after": dst_after,
        "destination_stock_after_second_call": dst_after_second,
        "status_after_first": transfer_row.get("status"),
        "movement_count_after_first": len(movements),
        "movement_count_after_second": len(movements_after_second),
        "second_call_status_code": second_status,
        "second_call_detail": second_detail,
    }

    record_result(
        scenario="br_bt.5_exact_receive_moves_stock_once",
        step="first_receive_decrements_source",
        expected={"stock": src_before - QTY},
        actual={"stock": src_after},
        evidence=base_ev,
    )
    record_result(
        scenario="br_bt.5_exact_receive_moves_stock_once",
        step="first_receive_increments_destination",
        expected={"stock": dst_before + QTY},
        actual={"stock": dst_after},
        evidence=base_ev,
    )
    record_result(
        scenario="br_bt.5_exact_receive_moves_stock_once",
        step="first_receive_logs_two_movements",
        expected={"movement_count": 2,
                  "out_qty": -float(QTY), "in_qty": float(QTY)},
        actual={
            "movement_count": len(movements),
            "out_qty": (float(next(
                (m["quantity_change"] for m in movements
                 if m["type"] == "transfer_out"), 0))),
            "in_qty": (float(next(
                (m["quantity_change"] for m in movements
                 if m["type"] == "transfer_in"), 0))),
        },
        evidence=base_ev,
    )
    record_result(
        scenario="br_bt.5_exact_receive_moves_stock_once",
        step="first_receive_status_received",
        expected={"status": "received"},
        actual={"status": transfer_row.get("status")},
        evidence=base_ev,
    )
    record_result(
        scenario="br_bt.5_exact_receive_moves_stock_once",
        step="second_receive_rejected_no_stock_move",
        expected={"rejected": True, "movement_count_after": 2,
                  "source_stock_after": src_before - QTY,
                  "destination_stock_after": dst_before + QTY},
        actual={
            "rejected": second_status is not None,
            "movement_count_after": len(movements_after_second),
            "source_stock_after": src_after_second,
            "destination_stock_after": dst_after_second,
        },
        evidence=base_ev,
    )

    assert src_after == src_before - QTY, (
        f"br_bt5: source stock should be {src_before - QTY}, got {src_after}"
    )
    assert dst_after == dst_before + QTY, (
        f"br_bt5: destination stock should be {dst_before + QTY}, got "
        f"{dst_after}"
    )
    assert transfer_row.get("status") == "received"
    assert len(movements) == 2, (
        f"br_bt5: expected 2 movement rows, got {len(movements)}"
    )
    assert second_status is not None, (
        "br_bt5 IDEMPOTENCY BUG — second /receive call did not raise"
    )
    assert src_after_second == src_after, (
        f"br_bt5 DOUBLE-MOVEMENT BUG — source stock moved AGAIN on second "
        f"call: {src_after} → {src_after_second}"
    )
    assert dst_after_second == dst_after, (
        f"br_bt5 DOUBLE-MOVEMENT BUG — destination stock moved AGAIN on "
        f"second call: {dst_after} → {dst_after_second}"
    )
    assert len(movements_after_second) == len(movements), (
        f"br_bt5 DOUBLE-MOVEMENT BUG — extra movement rows logged on "
        f"second call ({len(movements)} → {len(movements_after_second)})"
    )


# ─────────────────────────────────────────────────────────────────────
# 6. Variance receive does NOT move stock; variance data stored.
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_br_bt6_variance_receive_does_not_move_stock(
    tenant, record_result
):
    """
    Variance /receive path (destination submits qty_received != qty):
      * Status flips to 'received_pending'.
      * Inventory is NOT touched (the route's explicit comment:
        "PENDING PATH: store claim, do NOT touch inventory yet").
      * pending_items, shortages, excesses persisted on the BTO.
    """
    org_id     = tenant["org_id"]
    main       = tenant["branches"]["main"]
    b2         = tenant["branches"]["b2"]
    owner_user = tenant["users"]["owner"]

    SENT_QTY     = 10
    RECEIVED_QTY = 8     # 2-unit shortage claim

    product_id = await seed_product(
        org_id, main, name="br_bt6 Product",
        price=100, stock=30, cost=60,
    )
    src_before = await _inv_qty(main, product_id)
    dst_before = await _inv_qty(b2,   product_id)

    draft = await create_transfer(
        _bto_payload(main, b2, product_id, "br_bt6 Product", qty=SENT_QTY),
        user=owner_user,
    )
    await send_transfer(draft["id"], user=owner_user)

    await receive_transfer(
        draft["id"],
        data={
            "skip_receipt_check": True,
            "items": [{
                "product_id": product_id,
                "qty_received": RECEIVED_QTY,
            }],
        },
        user=owner_user,
    )

    src_after = await _inv_qty(main, product_id)
    dst_after = await _inv_qty(b2,   product_id)
    movements = await _movements_for_transfer(draft["id"])
    bto_row = await _transfer_row(draft["id"])

    pending_items = bto_row.get("pending_items", [])
    shortages = bto_row.get("shortages", [])
    excesses = bto_row.get("excesses", [])

    base_ev = {
        "org_id": org_id,
        "source_branch_id": main,
        "destination_branch_id": b2,
        "transfer_id": draft["id"],
        "order_number": bto_row.get("order_number"),
        "product_id": product_id,
        "sent_qty": SENT_QTY,
        "received_qty": RECEIVED_QTY,
        "variance": SENT_QTY - RECEIVED_QTY,
        "source_stock_before": src_before,
        "source_stock_after": src_after,
        "destination_stock_before": dst_before,
        "destination_stock_after": dst_after,
        "status_after": bto_row.get("status"),
        "movement_count": len(movements),
        "shortages_recorded": len(shortages),
        "excesses_recorded": len(excesses),
    }

    record_result(
        scenario="br_bt.6_variance_receive_does_not_move_stock",
        step="status_flips_to_received_pending",
        expected={"status": "received_pending"},
        actual={"status": bto_row.get("status")},
        evidence=base_ev,
    )
    record_result(
        scenario="br_bt.6_variance_receive_does_not_move_stock",
        step="source_stock_unchanged_on_variance",
        expected={"stock": src_before},
        actual={"stock": src_after},
        evidence=base_ev,
    )
    record_result(
        scenario="br_bt.6_variance_receive_does_not_move_stock",
        step="destination_stock_unchanged_on_variance",
        expected={"stock": dst_before},
        actual={"stock": dst_after},
        evidence=base_ev,
    )
    record_result(
        scenario="br_bt.6_variance_receive_does_not_move_stock",
        step="variance_data_persisted",
        expected={
            "pending_items_count": 1,
            "shortages_count": 1, "excesses_count": 0,
            "has_shortage": True,
        },
        actual={
            "pending_items_count": len(pending_items),
            "shortages_count": len(shortages),
            "excesses_count": len(excesses),
            "has_shortage": bto_row.get("has_shortage", False),
        },
        evidence=base_ev,
    )
    record_result(
        scenario="br_bt.6_variance_receive_does_not_move_stock",
        step="no_movement_rows_logged_on_variance",
        expected={"movement_count": 0},
        actual={"movement_count": len(movements)},
        evidence=base_ev,
    )

    assert bto_row.get("status") == "received_pending", (
        f"br_bt6: status should be 'received_pending', got "
        f"{bto_row.get('status')!r}"
    )
    assert src_after == src_before, (
        f"br_bt6 INVARIANT BUG — variance /receive moved source stock "
        f"{src_before} → {src_after}"
    )
    assert dst_after == dst_before, (
        f"br_bt6 INVARIANT BUG — variance /receive moved destination stock "
        f"{dst_before} → {dst_after}"
    )
    assert len(movements) == 0, (
        f"br_bt6 INVARIANT BUG — variance /receive logged movement rows "
        f"({len(movements)})"
    )
    assert len(shortages) == 1
    assert len(excesses) == 0
    assert bto_row.get("has_shortage") is True


# ─────────────────────────────────────────────────────────────────────
# 7. Accept-receipt after variance moves stock ONCE; re-call blocked.
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_br_bt7_accept_receipt_moves_stock_once(
    tenant, record_result
):
    """
    /accept-receipt closes a variance flow:
      * Moves inventory at the RECEIVED quantity (not the sent quantity).
      * Source -= qty_received; destination += qty_received.
      * Status flips received_pending → received.
      * Re-call is rejected ("Transfer is not pending receipt confirmation").
    """
    org_id     = tenant["org_id"]
    main       = tenant["branches"]["main"]
    b2         = tenant["branches"]["b2"]
    owner_user = tenant["users"]["owner"]

    SENT_QTY     = 10
    RECEIVED_QTY = 7   # 3-unit shortage — accepted as-is

    product_id = await seed_product(
        org_id, main, name="br_bt7 Product",
        price=100, stock=30, cost=60,
    )
    src_before = await _inv_qty(main, product_id)
    dst_before = await _inv_qty(b2,   product_id)

    draft = await create_transfer(
        _bto_payload(main, b2, product_id, "br_bt7 Product", qty=SENT_QTY),
        user=owner_user,
    )
    await send_transfer(draft["id"], user=owner_user)
    await receive_transfer(
        draft["id"],
        data={
            "skip_receipt_check": True,
            "items": [{
                "product_id": product_id, "qty_received": RECEIVED_QTY,
            }],
        },
        user=owner_user,
    )
    # confirm we're in variance state with stock untouched
    assert (await _transfer_row(draft["id"])).get("status") == "received_pending"
    assert await _inv_qty(main, product_id) == src_before
    assert await _inv_qty(b2,   product_id) == dst_before

    # Source admin accepts variance — inventory moves now, at received qty.
    await accept_receipt(
        draft["id"], data={"action": "accept", "note": "br_bt7 accept"},
        user=owner_user,
    )

    src_after = await _inv_qty(main, product_id)
    dst_after = await _inv_qty(b2,   product_id)
    movements = await _movements_for_transfer(draft["id"])
    bto_row = await _transfer_row(draft["id"])

    # Re-call accept — must be rejected.
    second_status = None
    second_detail = None
    try:
        await accept_receipt(
            draft["id"], data={"action": "accept", "note": "duplicate"},
            user=owner_user,
        )
    except HTTPException as e:
        second_status = e.status_code
        second_detail = e.detail

    src_after_second = await _inv_qty(main, product_id)
    dst_after_second = await _inv_qty(b2,   product_id)
    movements_after_second = await _movements_for_transfer(draft["id"])

    base_ev = {
        "org_id": org_id,
        "source_branch_id": main,
        "destination_branch_id": b2,
        "transfer_id": draft["id"],
        "order_number": bto_row.get("order_number"),
        "product_id": product_id,
        "sent_qty": SENT_QTY,
        "received_qty": RECEIVED_QTY,
        "source_stock_before": src_before,
        "source_stock_after": src_after,
        "source_stock_after_second_call": src_after_second,
        "destination_stock_before": dst_before,
        "destination_stock_after": dst_after,
        "destination_stock_after_second_call": dst_after_second,
        "status_after": bto_row.get("status"),
        "movement_count_after_first": len(movements),
        "movement_count_after_second": len(movements_after_second),
        "second_call_status_code": second_status,
        "second_call_detail": second_detail,
    }

    # _apply_receipt uses qty_received from pending_items — so the move
    # MUST be at RECEIVED_QTY, not SENT_QTY.
    record_result(
        scenario="br_bt.7_accept_receipt_moves_stock_once",
        step="source_decremented_by_received_qty",
        expected={"stock": src_before - RECEIVED_QTY,
                  "delta": -float(RECEIVED_QTY)},
        actual={"stock": src_after,
                "delta": float(src_after - src_before)},
        evidence=base_ev,
    )
    record_result(
        scenario="br_bt.7_accept_receipt_moves_stock_once",
        step="destination_incremented_by_received_qty",
        expected={"stock": dst_before + RECEIVED_QTY},
        actual={"stock": dst_after},
        evidence=base_ev,
    )
    record_result(
        scenario="br_bt.7_accept_receipt_moves_stock_once",
        step="status_received_with_shortage_flag",
        expected={"status": "received", "has_shortage": True},
        actual={"status": bto_row.get("status"),
                "has_shortage": bto_row.get("has_shortage", False)},
        evidence=base_ev,
    )
    record_result(
        scenario="br_bt.7_accept_receipt_moves_stock_once",
        step="movements_logged_once",
        expected={"movement_count": 2},
        actual={"movement_count": len(movements)},
        evidence=base_ev,
    )
    record_result(
        scenario="br_bt.7_accept_receipt_moves_stock_once",
        step="second_accept_rejected_no_double_movement",
        expected={
            "rejected": True,
            "source_stock_after": src_before - RECEIVED_QTY,
            "destination_stock_after": dst_before + RECEIVED_QTY,
            "movement_count_after": 2,
        },
        actual={
            "rejected": second_status is not None,
            "source_stock_after": src_after_second,
            "destination_stock_after": dst_after_second,
            "movement_count_after": len(movements_after_second),
        },
        evidence=base_ev,
    )

    assert src_after == src_before - RECEIVED_QTY, (
        f"br_bt7: source stock should be {src_before - RECEIVED_QTY} "
        f"(received_qty, not sent_qty), got {src_after}"
    )
    assert dst_after == dst_before + RECEIVED_QTY, (
        f"br_bt7: destination stock should be {dst_before + RECEIVED_QTY}, "
        f"got {dst_after}"
    )
    assert bto_row.get("status") == "received"
    assert bto_row.get("has_shortage") is True
    assert len(movements) == 2
    assert second_status is not None, (
        "br_bt7 IDEMPOTENCY BUG — second /accept-receipt did not raise"
    )
    assert src_after_second == src_after, (
        f"br_bt7 DOUBLE-MOVEMENT BUG — source moved again on second accept "
        f"({src_after} → {src_after_second})"
    )
    assert dst_after_second == dst_after, (
        f"br_bt7 DOUBLE-MOVEMENT BUG — destination moved again on second "
        f"accept ({dst_after} → {dst_after_second})"
    )
    assert len(movements_after_second) == len(movements)


# ─────────────────────────────────────────────────────────────────────
# 8. Dispute does NOT move stock; status flips to 'disputed'.
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_br_bt8_dispute_does_not_move_stock(tenant, record_result):
    """
    /dispute-receipt closes a variance flow WITHOUT moving inventory.
    Status flips received_pending → disputed. Destination must re-count.
    """
    org_id     = tenant["org_id"]
    main       = tenant["branches"]["main"]
    b2         = tenant["branches"]["b2"]
    owner_user = tenant["users"]["owner"]

    SENT_QTY     = 10
    RECEIVED_QTY = 6

    product_id = await seed_product(
        org_id, main, name="br_bt8 Product",
        price=100, stock=30, cost=60,
    )
    src_before = await _inv_qty(main, product_id)
    dst_before = await _inv_qty(b2,   product_id)

    draft = await create_transfer(
        _bto_payload(main, b2, product_id, "br_bt8 Product", qty=SENT_QTY),
        user=owner_user,
    )
    await send_transfer(draft["id"], user=owner_user)
    await receive_transfer(
        draft["id"],
        data={
            "skip_receipt_check": True,
            "items": [{"product_id": product_id, "qty_received": RECEIVED_QTY}],
        },
        user=owner_user,
    )
    await dispute_receipt(
        draft["id"], data={"note": "br_bt8 source disputes the short count"},
        user=owner_user,
    )

    src_after = await _inv_qty(main, product_id)
    dst_after = await _inv_qty(b2,   product_id)
    movements = await _movements_for_transfer(draft["id"])
    bto_row = await _transfer_row(draft["id"])

    base_ev = {
        "org_id": org_id,
        "source_branch_id": main,
        "destination_branch_id": b2,
        "transfer_id": draft["id"],
        "order_number": bto_row.get("order_number"),
        "product_id": product_id,
        "sent_qty": SENT_QTY,
        "received_qty": RECEIVED_QTY,
        "source_stock_before": src_before,
        "source_stock_after": src_after,
        "destination_stock_before": dst_before,
        "destination_stock_after": dst_after,
        "status_after": bto_row.get("status"),
        "movement_count": len(movements),
        "dispute_note": bto_row.get("dispute_note"),
    }

    record_result(
        scenario="br_bt.8_dispute_does_not_move_stock",
        step="status_flips_to_disputed",
        expected={"status": "disputed"},
        actual={"status": bto_row.get("status")},
        evidence=base_ev,
    )
    record_result(
        scenario="br_bt.8_dispute_does_not_move_stock",
        step="source_stock_unchanged_after_dispute",
        expected={"stock": src_before},
        actual={"stock": src_after},
        evidence=base_ev,
    )
    record_result(
        scenario="br_bt.8_dispute_does_not_move_stock",
        step="destination_stock_unchanged_after_dispute",
        expected={"stock": dst_before},
        actual={"stock": dst_after},
        evidence=base_ev,
    )
    record_result(
        scenario="br_bt.8_dispute_does_not_move_stock",
        step="no_movement_rows_logged_on_dispute",
        expected={"movement_count": 0},
        actual={"movement_count": len(movements)},
        evidence=base_ev,
    )
    record_result(
        scenario="br_bt.8_dispute_does_not_move_stock",
        step="dispute_note_persisted",
        expected={"has_note": True},
        actual={"has_note": bool(bto_row.get("dispute_note"))},
        evidence=base_ev,
    )

    assert bto_row.get("status") == "disputed", (
        f"br_bt8: status should be 'disputed', got "
        f"{bto_row.get('status')!r}"
    )
    assert src_after == src_before, (
        f"br_bt8 INVARIANT BUG — /dispute-receipt moved source stock "
        f"{src_before} → {src_after}"
    )
    assert dst_after == dst_before, (
        f"br_bt8 INVARIANT BUG — /dispute-receipt moved destination stock "
        f"{dst_before} → {dst_after}"
    )
    assert len(movements) == 0
    assert bto_row.get("dispute_note")


# ─────────────────────────────────────────────────────────────────────
# 9. Insufficient source stock — pre-flight atomic rejection.
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_br_bt9_insufficient_source_stock_atomic(
    tenant, record_result
):
    """
    Pre-flight in `_apply_receipt`:
    A multi-item transfer where ONE item exceeds source stock must abort
    the whole transfer WITHOUT mutating any item's inventory.
    The route raises 400 "Insufficient stock for '<name>'..."
    """
    org_id     = tenant["org_id"]
    main       = tenant["branches"]["main"]
    b2         = tenant["branches"]["b2"]
    owner_user = tenant["users"]["owner"]

    # Two products: one with plenty, one short. The "short" product
    # MUST be the second item so we'd see partial mutation if the
    # pre-flight guard were broken.
    p_ok    = await seed_product(org_id, main, name="br_bt9 OK",
                                  price=100, stock=20, cost=60)
    p_short = await seed_product(org_id, main, name="br_bt9 Short",
                                  price=100, stock=2,  cost=60)

    p_ok_src_before    = await _inv_qty(main, p_ok)
    p_short_src_before = await _inv_qty(main, p_short)
    p_ok_dst_before    = await _inv_qty(b2,   p_ok)
    p_short_dst_before = await _inv_qty(b2,   p_short)

    payload = {
        "from_branch_id": main,
        "to_branch_id": b2,
        "items": [
            {
                "product_id": p_ok,
                "product_name": "br_bt9 OK",
                "sku": f"SKU-{p_ok[-6:]}", "unit": "pc",
                "qty": 5,
                "branch_capital": 60.0, "transfer_capital": 60.0,
                "branch_retail": 100.0,
            },
            {
                "product_id": p_short,
                "product_name": "br_bt9 Short",
                "sku": f"SKU-{p_short[-6:]}", "unit": "pc",
                "qty": 8,   # source only has 2 → must fail pre-flight
                "branch_capital": 60.0, "transfer_capital": 60.0,
                "branch_retail": 100.0,
            },
        ],
        "min_margin": 20, "notes": "br_bt9",
    }
    draft = await create_transfer(payload, user=owner_user)
    await send_transfer(draft["id"], user=owner_user)

    status_code = None
    detail = None
    try:
        await receive_transfer(
            draft["id"], data={"skip_receipt_check": True}, user=owner_user
        )
    except HTTPException as e:
        status_code = e.status_code
        detail = e.detail

    p_ok_src_after    = await _inv_qty(main, p_ok)
    p_short_src_after = await _inv_qty(main, p_short)
    p_ok_dst_after    = await _inv_qty(b2,   p_ok)
    p_short_dst_after = await _inv_qty(b2,   p_short)
    movements         = await _movements_for_transfer(draft["id"])
    bto_row           = await _transfer_row(draft["id"])

    base_ev = {
        "org_id": org_id,
        "source_branch_id": main,
        "destination_branch_id": b2,
        "transfer_id": draft["id"],
        "order_number": bto_row.get("order_number"),
        "p_ok_product_id": p_ok, "p_short_product_id": p_short,
        "p_ok_source_before": p_ok_src_before,
        "p_ok_source_after": p_ok_src_after,
        "p_short_source_before": p_short_src_before,
        "p_short_source_after": p_short_src_after,
        "p_ok_destination_before": p_ok_dst_before,
        "p_ok_destination_after": p_ok_dst_after,
        "p_short_destination_before": p_short_dst_before,
        "p_short_destination_after": p_short_dst_after,
        "error_status": status_code,
        "error_detail": detail,
        "status_after": bto_row.get("status"),
        "movement_count": len(movements),
    }

    record_result(
        scenario="br_bt.9_insufficient_source_stock_atomic",
        step="receive_rejected_400",
        expected={"status_code": 400, "rejected": True},
        actual={"status_code": status_code,
                "rejected": status_code is not None},
        evidence=base_ev,
    )
    # CRITICAL — the OK item MUST NOT have been partially mutated even
    # though its row was processed first. This guards the pre-flight
    # snapshot logic that prevents the BTO-20260503-0003 incident.
    record_result(
        scenario="br_bt.9_insufficient_source_stock_atomic",
        step="ok_item_source_not_partially_decremented",
        expected={"stock": p_ok_src_before},
        actual={"stock": p_ok_src_after},
        evidence=base_ev,
    )
    record_result(
        scenario="br_bt.9_insufficient_source_stock_atomic",
        step="ok_item_destination_not_partially_incremented",
        expected={"stock": p_ok_dst_before},
        actual={"stock": p_ok_dst_after},
        evidence=base_ev,
    )
    record_result(
        scenario="br_bt.9_insufficient_source_stock_atomic",
        step="short_item_source_unchanged",
        expected={"stock": p_short_src_before},
        actual={"stock": p_short_src_after},
        evidence=base_ev,
    )
    record_result(
        scenario="br_bt.9_insufficient_source_stock_atomic",
        step="short_item_destination_unchanged",
        expected={"stock": p_short_dst_before},
        actual={"stock": p_short_dst_after},
        evidence=base_ev,
    )
    record_result(
        scenario="br_bt.9_insufficient_source_stock_atomic",
        step="no_movement_rows_logged_on_rejection",
        expected={"movement_count": 0},
        actual={"movement_count": len(movements)},
        evidence=base_ev,
    )

    assert status_code == 400, (
        f"br_bt9: expected HTTP 400 (insufficient stock), got "
        f"{status_code} (detail={detail!r})"
    )
    assert p_ok_src_after == p_ok_src_before, (
        f"br_bt9 ATOMIC BUG — OK item source stock changed despite "
        f"rejected transfer: {p_ok_src_before} → {p_ok_src_after}"
    )
    assert p_ok_dst_after == p_ok_dst_before, (
        f"br_bt9 ATOMIC BUG — OK item destination stock changed despite "
        f"rejected transfer: {p_ok_dst_before} → {p_ok_dst_after}"
    )
    assert p_short_src_after == p_short_src_before
    assert p_short_dst_after == p_short_dst_before
    assert len(movements) == 0


# ─────────────────────────────────────────────────────────────────────
# 10. Cancel is blocked after inventory moved.
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_br_bt10_cancel_blocks_after_inventory_moved(
    tenant, record_result
):
    """
    DELETE /branch-transfers/{id} (`cancel_transfer`) is blocked once
    status is in {received, received_pending, disputed, sent_to_terminal}.
    For a 'received' BTO: cancel raises 400 AND inventory stays in
    its post-receive state (not rolled back).
    """
    org_id     = tenant["org_id"]
    main       = tenant["branches"]["main"]
    b2         = tenant["branches"]["b2"]
    owner_user = tenant["users"]["owner"]

    QTY = 5
    product_id = await seed_product(
        org_id, main, name="br_bt10 Product",
        price=100, stock=20, cost=60,
    )
    src_before = await _inv_qty(main, product_id)
    dst_before = await _inv_qty(b2,   product_id)

    draft = await create_transfer(
        _bto_payload(main, b2, product_id, "br_bt10 Product", qty=QTY),
        user=owner_user,
    )
    await send_transfer(draft["id"], user=owner_user)
    await receive_transfer(
        draft["id"], data={"skip_receipt_check": True}, user=owner_user
    )

    src_after_receive = await _inv_qty(main, product_id)
    dst_after_receive = await _inv_qty(b2,   product_id)

    status_code = None
    detail = None
    try:
        await cancel_transfer(draft["id"], user=owner_user)
    except HTTPException as e:
        status_code = e.status_code
        detail = e.detail

    src_after_cancel = await _inv_qty(main, product_id)
    dst_after_cancel = await _inv_qty(b2,   product_id)
    bto_row = await _transfer_row(draft["id"])

    base_ev = {
        "org_id": org_id,
        "source_branch_id": main,
        "destination_branch_id": b2,
        "transfer_id": draft["id"],
        "order_number": bto_row.get("order_number"),
        "product_id": product_id,
        "transfer_qty": QTY,
        "source_stock_before": src_before,
        "source_stock_after_receive": src_after_receive,
        "source_stock_after_cancel_attempt": src_after_cancel,
        "destination_stock_before": dst_before,
        "destination_stock_after_receive": dst_after_receive,
        "destination_stock_after_cancel_attempt": dst_after_cancel,
        "status_after_cancel_attempt": bto_row.get("status"),
        "error_status": status_code,
        "error_detail": detail,
    }

    record_result(
        scenario="br_bt.10_cancel_blocks_after_inventory_moved",
        step="cancel_rejected_400",
        expected={"status_code": 400, "rejected": True},
        actual={"status_code": status_code,
                "rejected": status_code is not None},
        evidence=base_ev,
    )
    record_result(
        scenario="br_bt.10_cancel_blocks_after_inventory_moved",
        step="bto_status_remains_received",
        expected={"status": "received"},
        actual={"status": bto_row.get("status")},
        evidence=base_ev,
    )
    record_result(
        scenario="br_bt.10_cancel_blocks_after_inventory_moved",
        step="source_stock_not_rolled_back_by_blocked_cancel",
        expected={"stock": src_after_receive},
        actual={"stock": src_after_cancel},
        evidence=base_ev,
    )
    record_result(
        scenario="br_bt.10_cancel_blocks_after_inventory_moved",
        step="destination_stock_not_rolled_back_by_blocked_cancel",
        expected={"stock": dst_after_receive},
        actual={"stock": dst_after_cancel},
        evidence=base_ev,
    )

    assert status_code == 400, (
        f"br_bt10: expected HTTP 400 from cancel, got {status_code} "
        f"(detail={detail!r})"
    )
    assert bto_row.get("status") == "received", (
        f"br_bt10: BTO should remain 'received', got "
        f"{bto_row.get('status')!r}"
    )
    assert src_after_cancel == src_after_receive, (
        f"br_bt10 ROLLBACK BUG — blocked cancel rolled back source stock "
        f"({src_after_receive} → {src_after_cancel})"
    )
    assert dst_after_cancel == dst_after_receive, (
        f"br_bt10 ROLLBACK BUG — blocked cancel rolled back destination "
        f"stock ({dst_after_receive} → {dst_after_cancel})"
    )

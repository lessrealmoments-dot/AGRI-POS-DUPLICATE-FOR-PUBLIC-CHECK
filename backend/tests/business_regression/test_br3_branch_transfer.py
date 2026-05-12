"""
br3 — Branch transfer (Main → Branch B) invariants.

Discovered behaviour (read from `routes/branch_transfers.py` and
`routes/internal_invoices.py` BEFORE writing this test, NOT assumed):

  Lifecycle (the "exact match" happy-path used by br3):
      POST  /branch-transfers              → draft   (no inventory move)
      POST  /branch-transfers/{id}/send    → sent    (still no inventory move,
                                              destination just gets a notice)
      POST  /branch-transfers/{id}/receive → received (inventory shifts here,
                                              skip_receipt_check bypasses
                                              the 1-photo upload guard)

  Inventory side-effects on /receive (exact-match path, `_apply_receipt`):
    * `inventory[source]`        -= qty_received                      (decrement)
    * `inventory[destination]`   += qty_received                      (upsert)
    * `branch_prices[dest]`      upserted with cost_price + retail
    * `branch_transfer_price_memory[dest]` upserted
    * `capital_changes`          insert (1 row per item)
    * `movements` insert (2 rows per item: transfer_out @ src, transfer_in @ dest)
    * `internal_invoices`        1 row inserted at transfer CREATION (not
                                  on receive), linking the transfer to an
                                  internal AP/AR slip between the branches.

  Pre-flight stock guard (the safety net br3.b verifies):
    * Before mutating ANY row, the route walks every item and bails out
      with HTTP 400 "Insufficient stock for '<name>' in source branch:
      have X, need Y. No inventory changed." → so a single under-stocked
      line aborts the whole transfer without partial deltas.

  Branch isolation guard (br3.c):
    * `create_transfer` calls `assert_branch_access(user, from_branch_id)`
      AND `assert_branch_access(user, to_branch_id)`. Managers/cashiers
      with `branch_ids=[main]` get a 403 the moment `to_branch_id=b2`.

  Money / wallet side-effects:
    * NONE on the transfer itself. The internal_invoice is an info-only
      AP/AR ledger between branches. No `fund_wallets` mutation.
      → assertion family in br3.a explicitly proves wallets stay at 0.

Scope intentionally NOT covered here (deferred):
    * Variance path (`received_pending`)        — belongs to br3b / future BR.
    * Terminal-finalise receive                 — needs terminal_sessions plumbing.
    * Pending-approval workflow                 — phase2d permission tests cover it.
    * Internal-invoice paydown / financial drain — belongs to br4 (AP/AR).
    * Repack price updates on receive           — br5 (repack scope).
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
)


# ─────────────────────────────────────────────────────────────────────
# Local helpers — kept file-local; lift into _fixtures.py only when br5
# (repack) or br6 needs them too.
# ─────────────────────────────────────────────────────────────────────
def _transfer_payload(from_branch_id, to_branch_id,
                      product_id, product_name,
                      *, qty, capital=60.0, retail=100.0):
    """Mirror the body shape `create_transfer` actually consumes."""
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
        "notes": "br3 regression",
    }


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


async def _movements_for_transfer(transfer_id):
    return await _raw_db.movements.find(
        {"reference_id": transfer_id}, {"_id": 0}
    ).to_list(None)


async def _wallet_balances(branch_id):
    rows = await _raw_db.fund_wallets.find(
        {"branch_id": branch_id}, {"_id": 0, "type": 1, "balance": 1}
    ).to_list(None)
    return {r["type"]: float(r["balance"]) for r in rows}


# ─────────────────────────────────────────────────────────────────────
# A. Happy path — main → b2 transfer (draft → send → receive)
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_br3a_main_to_b2_full_receive_moves_stock(tenant, record_result):
    org_id     = tenant["org_id"]
    main       = tenant["branches"]["main"]
    b2         = tenant["branches"]["b2"]
    owner_user = tenant["users"]["owner"]

    START_STOCK   = 50
    TRANSFER_QTY  = 20
    TRANSFER_CAP  = 60.0
    TRANSFER_RET  = 100.0

    # Product T — exists at main with stock, ABSENT at b2 (no inventory row).
    product_id = await seed_product(
        org_id, main, name="BR3 Product T",
        price=TRANSFER_RET, stock=START_STOCK, cost=TRANSFER_CAP,
    )

    # ── pre-state
    src_before   = await _inv_qty(main, product_id)
    dst_before   = await _inv_qty(b2,   product_id)
    total_before = src_before + dst_before
    wallets_main_before = await _wallet_balances(main)
    wallets_b2_before   = await _wallet_balances(b2)

    assert src_before == START_STOCK, (
        f"br3a setup: main start stock expected {START_STOCK}, got {src_before}"
    )
    assert dst_before == 0.0

    # ── Step 1: create draft
    payload = _transfer_payload(main, b2, product_id, "BR3 Product T",
                                qty=TRANSFER_QTY, capital=TRANSFER_CAP,
                                retail=TRANSFER_RET)
    draft = await create_transfer(payload, user=owner_user)
    transfer_id = draft["id"]

    after_create_src = await _inv_qty(main, product_id)
    after_create_dst = await _inv_qty(b2,   product_id)
    assert after_create_src == src_before, (
        "br3a BUG: draft creation moved source stock"
    )
    assert after_create_dst == dst_before, (
        "br3a BUG: draft creation created destination stock"
    )
    assert draft.get("status") == "draft"

    record_result(
        scenario="br3.a_main_to_b2_full_receive",
        step="draft_creation_does_not_move_stock",
        expected={"src": src_before, "dst": dst_before,
                  "status": "draft"},
        actual={"src": after_create_src, "dst": after_create_dst,
                "status": draft.get("status")},
        evidence={"org_id": org_id, "transfer_id": transfer_id,
                  "order_number": draft.get("order_number"),
                  "product_id": product_id,
                  "from_branch_id": main, "to_branch_id": b2},
    )

    # ── Step 2: send (notify dest, still no inventory move)
    await send_transfer(transfer_id, user=owner_user)
    after_send_src = await _inv_qty(main, product_id)
    after_send_dst = await _inv_qty(b2,   product_id)
    sent_row = await _transfer_row(transfer_id)

    assert after_send_src == src_before
    assert after_send_dst == dst_before
    assert sent_row.get("status") == "sent"

    record_result(
        scenario="br3.a_main_to_b2_full_receive",
        step="send_does_not_move_stock",
        expected={"src": src_before, "dst": dst_before, "status": "sent"},
        actual={"src": after_send_src, "dst": after_send_dst,
                "status": sent_row.get("status")},
        evidence={"transfer_id": transfer_id,
                  "order_number": sent_row.get("order_number")},
    )

    # ── Step 3: receive (exact-match path, bypass receipt-photo guard)
    await receive_transfer(
        transfer_id,
        data={"skip_receipt_check": True},
        user=owner_user,
    )
    received_row = await _transfer_row(transfer_id)
    src_after  = await _inv_qty(main, product_id)
    dst_after  = await _inv_qty(b2,   product_id)
    total_after = src_after + dst_after
    movements = await _movements_for_transfer(transfer_id)
    wallets_main_after = await _wallet_balances(main)
    wallets_b2_after   = await _wallet_balances(b2)
    dest_branch_price = await _raw_db.branch_prices.find_one(
        {"product_id": product_id, "branch_id": b2}, {"_id": 0}
    ) or {}
    cap_changes = await _raw_db.capital_changes.find(
        {"product_id": product_id, "source_ref": received_row.get("order_number")},
        {"_id": 0},
    ).to_list(None)
    internal_inv = await _raw_db.internal_invoices.find_one(
        {"transfer_id": transfer_id}, {"_id": 0}
    ) or {}

    exp_src     = src_before - TRANSFER_QTY        # 30
    exp_dst     = dst_before + TRANSFER_QTY        # 20
    exp_total   = total_before                     # 50 (conservation)

    base_ev = {
        "org_id": org_id, "transfer_id": transfer_id,
        "order_number": received_row.get("order_number"),
        "product_id": product_id,
        "from_branch_id": main, "to_branch_id": b2,
        "start_src": src_before, "start_dst": dst_before,
        "transfer_qty": TRANSFER_QTY,
        "transfer_capital": TRANSFER_CAP,
        "branch_retail": TRANSFER_RET,
    }

    # Source decremented
    record_result(
        scenario="br3.a_main_to_b2_full_receive",
        step="source_stock_decremented",
        expected={"src": exp_src, "delta": -TRANSFER_QTY},
        actual={"src": src_after, "delta": src_after - src_before},
        evidence=base_ev,
    )
    # Destination incremented (upsert path)
    record_result(
        scenario="br3.a_main_to_b2_full_receive",
        step="destination_stock_incremented_via_upsert",
        expected={"dst": exp_dst, "delta": +TRANSFER_QTY},
        actual={"dst": dst_after, "delta": dst_after - dst_before},
        evidence=base_ev,
    )
    # Conservation
    record_result(
        scenario="br3.a_main_to_b2_full_receive",
        step="total_stock_conserved_across_branches",
        expected={"total": exp_total},
        actual={"total": total_after},
        evidence={**base_ev, "src_after": src_after, "dst_after": dst_after},
    )
    # Status
    record_result(
        scenario="br3.a_main_to_b2_full_receive",
        step="transfer_status_received",
        expected={"status": "received"},
        actual={"status": received_row.get("status")},
        evidence=base_ev,
    )
    # Two movement rows, one transfer_out @ src, one transfer_in @ dst
    out_rows = [m for m in movements if m["type"] == "transfer_out"]
    in_rows  = [m for m in movements if m["type"] == "transfer_in"]
    record_result(
        scenario="br3.a_main_to_b2_full_receive",
        step="movements_logged_out_and_in",
        expected={
            "total": 2,
            "transfer_out": {"count": 1, "branch_id": main,
                             "qty_change": -float(TRANSFER_QTY)},
            "transfer_in":  {"count": 1, "branch_id": b2,
                             "qty_change": +float(TRANSFER_QTY)},
        },
        actual={
            "total": len(movements),
            "transfer_out": {
                "count": len(out_rows),
                "branch_id": out_rows[0]["branch_id"] if out_rows else None,
                "qty_change": (float(out_rows[0]["quantity_change"])
                               if out_rows else None),
            },
            "transfer_in": {
                "count": len(in_rows),
                "branch_id": in_rows[0]["branch_id"] if in_rows else None,
                "qty_change": (float(in_rows[0]["quantity_change"])
                               if in_rows else None),
            },
        },
        evidence={**base_ev,
                  "movement_ids": [m["id"] for m in movements]},
    )
    # Destination capital + retail propagated
    record_result(
        scenario="br3.a_main_to_b2_full_receive",
        step="destination_branch_price_set",
        expected={"cost_price": TRANSFER_CAP, "retail": TRANSFER_RET,
                  "source": "branch_transfer"},
        actual={
            "cost_price": float(dest_branch_price.get("cost_price") or 0),
            "retail": float(((dest_branch_price.get("prices") or {})
                             .get("retail")) or 0),
            "source": dest_branch_price.get("source"),
        },
        evidence={**base_ev,
                  "transfer_order_on_row": dest_branch_price.get("transfer_order")},
    )
    # Capital-change audit row (admin-visible)
    record_result(
        scenario="br3.a_main_to_b2_full_receive",
        step="capital_change_audit_row_recorded",
        expected={"count": 1, "new_capital": TRANSFER_CAP,
                  "source_type": "branch_transfer"},
        actual={
            "count": len(cap_changes),
            "new_capital": (float(cap_changes[0]["new_capital"])
                            if cap_changes else None),
            "source_type": (cap_changes[0]["source_type"]
                            if cap_changes else None),
        },
        evidence={**base_ev,
                  "method": (cap_changes[0]["method"]
                             if cap_changes else None)},
    )
    # Internal invoice auto-generated at creation
    record_result(
        scenario="br3.a_main_to_b2_full_receive",
        step="internal_invoice_auto_created",
        expected={"created": True,
                  "from_branch_id": main, "to_branch_id": b2,
                  "grand_total": float(TRANSFER_QTY * TRANSFER_CAP)},
        actual={
            "created": bool(internal_inv),
            "from_branch_id": internal_inv.get("from_branch_id"),
            "to_branch_id": internal_inv.get("to_branch_id"),
            "grand_total": float(internal_inv.get("grand_total") or 0),
        },
        evidence={**base_ev,
                  "internal_invoice_number": internal_inv.get("invoice_number")},
    )
    # Wallets untouched (transfer is inventory-only)
    record_result(
        scenario="br3.a_main_to_b2_full_receive",
        step="branch_transfer_is_inventory_only_no_wallet_move",
        expected={"main_wallets": wallets_main_before,
                  "b2_wallets":   wallets_b2_before},
        actual={"main_wallets": wallets_main_after,
                "b2_wallets":   wallets_b2_after},
        evidence={**base_ev,
                  "note": "branch transfer never moves fund_wallets; "
                          "money side lives on internal_invoices instead"},
    )

    # Hard asserts (business-readable failure messages)
    assert src_after == exp_src, (
        f"br3a source stock — expected {exp_src}, got {src_after} "
        f"(transfer={received_row.get('order_number')})"
    )
    assert dst_after == exp_dst, (
        f"br3a destination stock — expected {exp_dst}, got {dst_after}"
    )
    assert total_after == exp_total, (
        f"br3a CONSERVATION BUG — total stock changed from {exp_total} to "
        f"{total_after} (src={src_after}, dst={dst_after})"
    )
    assert received_row.get("status") == "received", (
        f"br3a transfer status — expected 'received', got "
        f"{received_row.get('status')!r}"
    )
    assert len(movements) == 2, (
        f"br3a movements count — expected 2 (one out, one in), "
        f"got {len(movements)}"
    )
    assert len(out_rows) == 1 and out_rows[0]["branch_id"] == main
    assert float(out_rows[0]["quantity_change"]) == -float(TRANSFER_QTY)
    assert len(in_rows) == 1 and in_rows[0]["branch_id"] == b2
    assert float(in_rows[0]["quantity_change"]) == +float(TRANSFER_QTY)
    assert dest_branch_price.get("source") == "branch_transfer"
    assert float(dest_branch_price.get("cost_price") or 0) == TRANSFER_CAP
    assert len(cap_changes) == 1
    assert cap_changes[0]["source_type"] == "branch_transfer"
    assert internal_inv, "br3a internal invoice was not auto-created"
    assert internal_inv["from_branch_id"] == main
    assert internal_inv["to_branch_id"] == b2
    assert float(internal_inv["grand_total"]) == float(TRANSFER_QTY * TRANSFER_CAP)
    assert wallets_main_after == wallets_main_before
    assert wallets_b2_after   == wallets_b2_before


# ─────────────────────────────────────────────────────────────────────
# B. Insufficient stock — pre-flight guard prevents any mutation
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_br3b_transfer_exceeding_stock_aborts_without_mutation(
    tenant, record_result
):
    org_id     = tenant["org_id"]
    main       = tenant["branches"]["main"]
    b2         = tenant["branches"]["b2"]
    owner_user = tenant["users"]["owner"]

    SRC_START   = 5
    REQUEST_QTY = 8     # > available

    product_id = await seed_product(
        org_id, main, name="BR3 Product Short",
        price=100, stock=SRC_START, cost=60,
    )

    src_before   = await _inv_qty(main, product_id)
    dst_before   = await _inv_qty(b2,   product_id)
    total_before = src_before + dst_before

    draft = await create_transfer(
        _transfer_payload(main, b2, product_id, "BR3 Product Short",
                          qty=REQUEST_QTY),
        user=owner_user,
    )
    transfer_id = draft["id"]
    await send_transfer(transfer_id, user=owner_user)

    status_code = None
    detail = None
    try:
        await receive_transfer(
            transfer_id, data={"skip_receipt_check": True},
            user=owner_user,
        )
    except HTTPException as e:
        status_code = e.status_code
        detail = e.detail

    src_after   = await _inv_qty(main, product_id)
    dst_after   = await _inv_qty(b2,   product_id)
    total_after = src_after + dst_after
    movements   = await _movements_for_transfer(transfer_id)
    transfer_row = await _transfer_row(transfer_id)

    base_ev = {
        "org_id": org_id, "transfer_id": transfer_id,
        "product_id": product_id,
        "from_branch_id": main, "to_branch_id": b2,
        "src_start": src_before, "request_qty": REQUEST_QTY,
        "received_status_code": status_code,
        "received_detail": detail,
    }

    record_result(
        scenario="br3.b_insufficient_stock_pre_flight_guard",
        step="receive_rejected_400",
        expected={"status_code": 400, "rejected": True},
        actual={"status_code": status_code,
                "rejected": status_code is not None},
        evidence=base_ev,
    )
    record_result(
        scenario="br3.b_insufficient_stock_pre_flight_guard",
        step="source_stock_unchanged",
        expected={"src": src_before},
        actual={"src": src_after},
        evidence=base_ev,
    )
    record_result(
        scenario="br3.b_insufficient_stock_pre_flight_guard",
        step="destination_stock_unchanged",
        expected={"dst": dst_before},
        actual={"dst": dst_after},
        evidence=base_ev,
    )
    record_result(
        scenario="br3.b_insufficient_stock_pre_flight_guard",
        step="no_movement_rows_logged",
        expected={"movement_count": 0},
        actual={"movement_count": len(movements)},
        evidence={**base_ev,
                  "movement_ids": [m.get("id") for m in movements]},
    )
    record_result(
        scenario="br3.b_insufficient_stock_pre_flight_guard",
        step="transfer_status_not_received",
        expected={"status_not_received": True},
        actual={"status_not_received":
                transfer_row.get("status") != "received"},
        evidence={**base_ev,
                  "current_transfer_status": transfer_row.get("status")},
    )

    assert status_code == 400, (
        f"br3b expected HTTP 400 (pre-flight stock guard), got {status_code} "
        f"(detail={detail!r})"
    )
    assert src_after == src_before, (
        f"br3b BUG — source stock changed despite pre-flight rejection "
        f"(was {src_before}, now {src_after})"
    )
    assert dst_after == dst_before, (
        f"br3b BUG — destination stock changed despite pre-flight rejection "
        f"(was {dst_before}, now {dst_after})"
    )
    assert total_after == total_before, (
        f"br3b CONSERVATION BUG — total stock drifted from "
        f"{total_before} to {total_after}"
    )
    assert movements == [], (
        f"br3b BUG — {len(movements)} movement rows recorded for a "
        f"rejected transfer"
    )
    assert transfer_row.get("status") != "received"


# ─────────────────────────────────────────────────────────────────────
# C. Branch isolation — manager assigned only to main cannot transfer to b2
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_br3c_manager_blocked_from_unauthorized_destination_branch(
    tenant, record_result
):
    """`make_business_day_tenant()` gives the manager `branch_ids=[main]`,
    so attempting to address `to_branch_id=b2` via `create_transfer` must
    be rejected by `assert_branch_access` BEFORE any DB row is created."""
    org_id       = tenant["org_id"]
    main         = tenant["branches"]["main"]
    b2           = tenant["branches"]["b2"]
    manager_user = tenant["users"]["manager"]

    product_id = await seed_product(
        org_id, main, name="BR3 Product Locked",
        price=100, stock=10, cost=60,
    )
    src_before = await _inv_qty(main, product_id)
    dst_before = await _inv_qty(b2,   product_id)
    transfers_before = await _raw_db.branch_transfer_orders.count_documents(
        {"organization_id": org_id}
    )

    status_code = None
    detail = None
    try:
        await create_transfer(
            _transfer_payload(main, b2, product_id, "BR3 Product Locked",
                              qty=3),
            user=manager_user,
        )
    except HTTPException as e:
        status_code = e.status_code
        detail = e.detail

    src_after        = await _inv_qty(main, product_id)
    dst_after        = await _inv_qty(b2,   product_id)
    transfers_after  = await _raw_db.branch_transfer_orders.count_documents(
        {"organization_id": org_id}
    )

    base_ev = {
        "org_id": org_id, "product_id": product_id,
        "manager_id": manager_user["id"],
        "manager_branch_ids": manager_user.get("branch_ids"),
        "from_branch_id": main, "to_branch_id": b2,
        "status_code": status_code, "detail": detail,
    }
    record_result(
        scenario="br3.c_branch_isolation_guard",
        step="manager_create_rejected_403",
        expected={"status_code": 403, "rejected": True},
        actual={"status_code": status_code,
                "rejected": status_code is not None},
        evidence=base_ev,
    )
    record_result(
        scenario="br3.c_branch_isolation_guard",
        step="no_transfer_row_created",
        expected={"new_rows": 0},
        actual={"new_rows": transfers_after - transfers_before},
        evidence=base_ev,
    )
    record_result(
        scenario="br3.c_branch_isolation_guard",
        step="stock_unchanged_on_both_branches",
        expected={"src": src_before, "dst": dst_before},
        actual={"src": src_after, "dst": dst_after},
        evidence=base_ev,
    )

    assert status_code == 403, (
        f"br3c expected HTTP 403 (assert_branch_access), got {status_code} "
        f"(detail={detail!r})"
    )
    assert transfers_after == transfers_before, (
        f"br3c BUG — transfer row leaked despite 403 "
        f"(before={transfers_before}, after={transfers_after})"
    )
    assert src_after == src_before
    assert dst_after == dst_before


# TODO (later business_regression prompts):
#   * br3.d — variance receive → `received_pending` path (claim row, no
#       inventory move) + source confirms or disputes.
#   * br3.e — Smart-rule capital fallback when transfer_capital < dest
#       current capital → `method == "moving_average"` & destination
#       cost_price defended.
#   * br3.f — repack price updates propagated on receive (pairs with br5).
#   * br3.g — internal-invoice paydown / financial drain → br4 (AP/AR).

"""
br2 — Purchase Order receive → inventory / payable invariants.

Discovered behaviour (read from `routes/purchase_orders.py` before writing
the test, NOT assumed):

  * PO types
        draft         — saved only; no inventory or AP change at creation.
        cash          — inventory +qty AND wallet -₱ at creation. No payable.
        terms         — inventory +qty AND payable row CREATED at creation
                        (status="received", payment_status="unpaid",
                        balance=grand_total).
        branch_request— inter-branch request; not exercised here.

  * `POST /purchase-orders/{id}/receive` is reserved for draft/ordered POs.
    It (a) requires at least one uploaded receipt session — bypassed in
    tests with `skip_receipt_check: True` — and (b) flips status to
    `received` while running `_apply_po_inventory`. It does NOT create a
    payable; payable creation happens ONLY at PO creation for terms POs.

  * Receive endpoint blocks a second call ("PO already received"), so it is
    duplicate-safe for the simple repeat case. Not a true idempotency_key
    flow (that lives on creation), but enough to guarantee no stock double-up.

  * There is no native partial-receive route. The closest analogue is
    a terminal-finalise pass that rewrites `items[].quantity` down before
    the receive call; that path is too heavy to assert in br2 and is
    documented as a TODO at the bottom of this file.

  * Inventory mutations are recorded in `db.movements` (type="purchase").
    AP rows live in `db.payables`. Both are tenant-scoped and cleaned up
    by `cleanup_business_tenant()`.

Scope kept inside this file:
  A. Full receive — TERMS PO   (stock + payable created at creation).
  B. Full receive — DRAFT then /receive (stock at receive, no payable).
  C. Duplicate-receive guard on B (no stock double-up).
  D. Invalid-PO guard (receive against an id that doesn't exist).

Scope deferred (logged as TODO):
  * True partial-receive (system does not expose a half-receive route).
  * Cash PO end-to-end (requires pre-funded wallets + closed-day setup).
  * PO payment / `adjust-payment` to drain a payable → belongs to br4.
"""
import os
import sys
import copy

import pytest
from fastapi import HTTPException

BACKEND = os.path.join(os.path.dirname(__file__), "..", "..")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from config import _raw_db  # noqa: E402
from tests.business_regression._fixtures import seed_product, seed_supplier  # noqa: E402
from routes.purchase_orders import (  # noqa: E402
    create_purchase_order,
    receive_purchase_order,
)


# ─────────────────────────────────────────────────────────────────────
# Local helpers — kept inline; no new shared fixture warranted yet.
# Future BR files (br4 PO payment, br6 vendor pricing) can lift these
# into `_fixtures.py` if they need them.
# ─────────────────────────────────────────────────────────────────────
def _po_payload(branch_id, supplier_name, product_id, product_name,
                *, po_type, qty, unit_price):
    """Mirror the body shape `create_purchase_order` actually consumes:
      vendor   — string, NOT supplier_id
      items[*] — uses `unit_price` (NOT `unit_cost`)
    """
    return {
        "branch_id": branch_id,
        "vendor": supplier_name,
        "po_type": po_type,
        "purchase_date": "",   # let route fill in today_local
        "items": [{
            "product_id": product_id,
            "product_name": product_name,
            "unit": "pc",
            "quantity": qty,
            "unit_price": unit_price,
            "discount_type": "amount",
            "discount_value": 0,
        }],
    }


async def _po_snapshot(po_id):
    return await _raw_db.purchase_orders.find_one({"id": po_id}, {"_id": 0}) or {}


async def _payables_for_po(po_id):
    return await _raw_db.payables.find({"po_id": po_id}, {"_id": 0}).to_list(None)


async def _movement_rows(product_id, branch_id, ref_id):
    return await _raw_db.movements.find(
        {"product_id": product_id, "branch_id": branch_id,
         "reference_id": ref_id},
        {"_id": 0},
    ).to_list(None)


# ─────────────────────────────────────────────────────────────────────
# A. Full receive via TERMS PO — inventory + payable at creation
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_br2a_full_receive_terms_po_creates_stock_and_ap(tenant, record_result):
    org_id     = tenant["org_id"]
    branch_id  = tenant["branches"]["main"]
    owner_user = tenant["users"]["owner"]

    # Supplier (vendor is referenced by NAME in payload).
    supplier_id = await seed_supplier(org_id, name="BR2 Vendor Terms")
    supplier_row = await _raw_db.suppliers.find_one(
        {"id": supplier_id}, {"_id": 0, "name": 1}
    )
    vendor_name = supplier_row["name"]

    # Product A — starting stock 0.
    product_id = await seed_product(
        org_id, branch_id,
        name="BR2 Product A", price=120, stock=0, cost=60,
    )

    QTY        = 20
    UNIT_PRICE = 60
    EXPECTED_GRAND_TOTAL = QTY * UNIT_PRICE   # 1200

    # ── Act
    start_stock = await _raw_db.inventory.find_one(
        {"product_id": product_id, "branch_id": branch_id}, {"_id": 0, "quantity": 1}
    )
    start_qty = float(start_stock["quantity"]) if start_stock else 0.0

    payload = _po_payload(
        branch_id, vendor_name, product_id, "BR2 Product A",
        po_type="terms", qty=QTY, unit_price=UNIT_PRICE,
    )
    res = await create_purchase_order(payload, user=owner_user)
    po_id = res["id"]

    # ── Read final state
    po = await _po_snapshot(po_id)
    end_inv = await _raw_db.inventory.find_one(
        {"product_id": product_id, "branch_id": branch_id}, {"_id": 0, "quantity": 1}
    )
    end_qty = float(end_inv["quantity"]) if end_inv else 0.0
    payables = await _payables_for_po(po_id)
    movements = await _movement_rows(product_id, branch_id, po_id)

    expected_stock      = start_qty + QTY                       # 20
    expected_status     = "received"
    expected_pay_status = "unpaid"
    expected_ap_amount  = float(EXPECTED_GRAND_TOTAL)            # 1200.0
    expected_ap_balance = float(EXPECTED_GRAND_TOTAL)            # 1200.0
    expected_mvmt_count = 1
    expected_mvmt_type  = "purchase"

    base_ev = {
        "org_id": org_id, "branch_id": branch_id,
        "supplier_id": supplier_id, "vendor": vendor_name,
        "product_id": product_id,
        "po_id": po_id, "po_number": po.get("po_number"),
        "starting_stock": start_qty,
        "ordered_qty": QTY, "unit_price": UNIT_PRICE,
    }

    record_result(
        scenario="br2.a_terms_po_full_receive",
        step="inventory_increased_by_ordered_qty",
        expected={"end_stock": expected_stock, "delta": QTY},
        actual={"end_stock": end_qty, "delta": end_qty - start_qty},
        evidence=base_ev,
    )
    record_result(
        scenario="br2.a_terms_po_full_receive",
        step="po_status_and_payment_status",
        expected={"status": expected_status, "payment_status": expected_pay_status,
                  "balance": EXPECTED_GRAND_TOTAL},
        actual={"status": po.get("status"),
                "payment_status": po.get("payment_status"),
                "balance": float(po.get("balance") or 0)},
        evidence={**base_ev, "grand_total": po.get("grand_total")},
    )
    record_result(
        scenario="br2.a_terms_po_full_receive",
        step="payable_row_created_at_full_amount",
        expected={"payable_count": 1, "amount": expected_ap_amount,
                  "balance": expected_ap_balance, "status": "pending"},
        actual={"payable_count": len(payables),
                "amount": float(payables[0]["amount"]) if payables else None,
                "balance": float(payables[0]["balance"]) if payables else None,
                "status": payables[0]["status"] if payables else None},
        evidence={**base_ev,
                  "payable_ids": [p.get("id") for p in payables]},
    )
    record_result(
        scenario="br2.a_terms_po_full_receive",
        step="movement_row_logged",
        expected={"count": expected_mvmt_count, "type": expected_mvmt_type,
                  "quantity_change": float(QTY)},
        actual={"count": len(movements),
                "type": movements[0]["type"] if movements else None,
                "quantity_change": float(movements[0]["quantity_change"])
                                   if movements else None},
        evidence={**base_ev,
                  "movement_ids": [m.get("id") for m in movements]},
    )

    # Hard asserts with business-readable messages.
    assert end_qty == expected_stock, (
        f"br2a stock mismatch — expected {expected_stock}, got {end_qty} "
        f"(po={po.get('po_number')})"
    )
    assert po.get("status") == expected_status, (
        f"br2a PO status — expected {expected_status!r}, got {po.get('status')!r}"
    )
    assert po.get("payment_status") == expected_pay_status, (
        f"br2a PO payment_status — expected {expected_pay_status!r}, "
        f"got {po.get('payment_status')!r}"
    )
    assert float(po.get("balance") or 0) == EXPECTED_GRAND_TOTAL, (
        f"br2a PO balance — expected {EXPECTED_GRAND_TOTAL}, "
        f"got {po.get('balance')}"
    )
    assert len(payables) == 1, (
        f"br2a payable_count — expected 1, got {len(payables)} for po={po_id}"
    )
    assert float(payables[0]["amount"]) == expected_ap_amount, (
        f"br2a AP amount mismatch — expected {expected_ap_amount}, "
        f"got {payables[0]['amount']}"
    )
    assert float(payables[0]["balance"]) == expected_ap_balance, (
        f"br2a AP balance mismatch — expected {expected_ap_balance}, "
        f"got {payables[0]['balance']}"
    )
    assert payables[0]["status"] == "pending", (
        f"br2a AP status — expected 'pending', got {payables[0]['status']!r}"
    )
    assert len(movements) == expected_mvmt_count, (
        f"br2a movements count — expected {expected_mvmt_count}, got {len(movements)}"
    )
    assert movements[0]["type"] == expected_mvmt_type
    assert float(movements[0]["quantity_change"]) == float(QTY)


# ─────────────────────────────────────────────────────────────────────
# B. Draft PO → /receive — inventory updated at receive, no payable
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_br2b_draft_then_receive_updates_stock_no_payable(tenant, record_result):
    org_id     = tenant["org_id"]
    branch_id  = tenant["branches"]["main"]
    owner_user = tenant["users"]["owner"]

    supplier_id = await seed_supplier(org_id, name="BR2 Vendor Draft")
    supplier_row = await _raw_db.suppliers.find_one(
        {"id": supplier_id}, {"_id": 0, "name": 1}
    )
    vendor_name = supplier_row["name"]

    product_id = await seed_product(
        org_id, branch_id,
        name="BR2 Product B", price=80, stock=0, cost=40,
    )

    QTY        = 10
    UNIT_PRICE = 40
    EXPECTED_GRAND_TOTAL = QTY * UNIT_PRICE   # 400

    # ── Step 1: create DRAFT → no inventory, no payable, status=draft
    draft_res = await create_purchase_order(
        _po_payload(branch_id, vendor_name, product_id, "BR2 Product B",
                    po_type="draft", qty=QTY, unit_price=UNIT_PRICE),
        user=owner_user,
    )
    po_id = draft_res["id"]
    po_after_create = await _po_snapshot(po_id)
    inv_after_create = await _raw_db.inventory.find_one(
        {"product_id": product_id, "branch_id": branch_id}, {"_id": 0, "quantity": 1}
    )
    qty_after_create = float(inv_after_create["quantity"]) if inv_after_create else 0.0
    payables_after_create = await _payables_for_po(po_id)
    movements_after_create = await _movement_rows(product_id, branch_id, po_id)

    record_result(
        scenario="br2.b_draft_then_receive",
        step="draft_creation_does_not_move_stock_or_ap",
        expected={"status": "draft", "stock": 0.0, "payable_count": 0,
                  "movement_count": 0},
        actual={"status": po_after_create.get("status"),
                "stock": qty_after_create,
                "payable_count": len(payables_after_create),
                "movement_count": len(movements_after_create)},
        evidence={"org_id": org_id, "po_id": po_id,
                  "po_number": po_after_create.get("po_number"),
                  "product_id": product_id, "branch_id": branch_id},
    )
    assert po_after_create.get("status") == "draft"
    assert qty_after_create == 0.0
    assert payables_after_create == []
    assert movements_after_create == []

    # ── Step 2: /receive with skip_receipt_check bypass
    await receive_purchase_order(po_id, data={"skip_receipt_check": True},
                                 user=owner_user)
    po_after_recv = await _po_snapshot(po_id)
    inv_after_recv = await _raw_db.inventory.find_one(
        {"product_id": product_id, "branch_id": branch_id}, {"_id": 0, "quantity": 1}
    )
    qty_after_recv = float(inv_after_recv["quantity"]) if inv_after_recv else 0.0
    payables_after_recv = await _payables_for_po(po_id)
    movements_after_recv = await _movement_rows(product_id, branch_id, po_id)

    base_ev = {
        "org_id": org_id, "po_id": po_id,
        "po_number": po_after_recv.get("po_number"),
        "product_id": product_id, "branch_id": branch_id,
        "ordered_qty": QTY, "unit_price": UNIT_PRICE,
        "grand_total": EXPECTED_GRAND_TOTAL,
    }
    record_result(
        scenario="br2.b_draft_then_receive",
        step="receive_moves_stock_by_full_qty",
        expected={"end_stock": float(QTY), "delta": float(QTY)},
        actual={"end_stock": qty_after_recv,
                "delta": qty_after_recv - qty_after_create},
        evidence=base_ev,
    )
    record_result(
        scenario="br2.b_draft_then_receive",
        step="receive_flips_status_to_received",
        expected={"status": "received"},
        actual={"status": po_after_recv.get("status")},
        evidence=base_ev,
    )
    record_result(
        scenario="br2.b_draft_then_receive",
        step="draft_path_never_creates_payable",
        expected={"payable_count": 0},
        actual={"payable_count": len(payables_after_recv)},
        evidence={**base_ev,
                  "rationale": "AP is created only for terms POs at creation"},
    )
    record_result(
        scenario="br2.b_draft_then_receive",
        step="single_purchase_movement_logged",
        expected={"count": 1, "type": "purchase",
                  "quantity_change": float(QTY)},
        actual={"count": len(movements_after_recv),
                "type": (movements_after_recv[0]["type"]
                         if movements_after_recv else None),
                "quantity_change": (float(movements_after_recv[0]["quantity_change"])
                                    if movements_after_recv else None)},
        evidence=base_ev,
    )

    assert qty_after_recv == float(QTY), (
        f"br2b stock mismatch — expected {QTY}, got {qty_after_recv}"
    )
    assert po_after_recv.get("status") == "received"
    assert payables_after_recv == [], (
        f"br2b unexpected payable rows on draft path: {payables_after_recv}"
    )
    assert len(movements_after_recv) == 1
    assert movements_after_recv[0]["type"] == "purchase"
    assert float(movements_after_recv[0]["quantity_change"]) == float(QTY)


# ─────────────────────────────────────────────────────────────────────
# C. Duplicate-receive guard — second /receive call rejected, stock stable
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_br2c_duplicate_receive_is_rejected(tenant, record_result):
    org_id     = tenant["org_id"]
    branch_id  = tenant["branches"]["main"]
    owner_user = tenant["users"]["owner"]

    supplier_id = await seed_supplier(org_id, name="BR2 Vendor Dup")
    supplier_row = await _raw_db.suppliers.find_one(
        {"id": supplier_id}, {"_id": 0, "name": 1}
    )
    vendor_name = supplier_row["name"]

    product_id = await seed_product(
        org_id, branch_id, name="BR2 Product C", price=90, stock=0, cost=45,
    )

    QTY = 7
    draft_res = await create_purchase_order(
        _po_payload(branch_id, vendor_name, product_id, "BR2 Product C",
                    po_type="draft", qty=QTY, unit_price=45),
        user=owner_user,
    )
    po_id = draft_res["id"]
    await receive_purchase_order(po_id, data={"skip_receipt_check": True},
                                 user=owner_user)

    inv_first = await _raw_db.inventory.find_one(
        {"product_id": product_id, "branch_id": branch_id}, {"_id": 0, "quantity": 1}
    )
    qty_first = float(inv_first["quantity"])
    movs_first = await _movement_rows(product_id, branch_id, po_id)

    # Second call MUST be rejected — capture HTTP status & message.
    second_status_code = None
    second_detail = None
    try:
        await receive_purchase_order(po_id, data={"skip_receipt_check": True},
                                     user=owner_user)
    except HTTPException as e:
        second_status_code = e.status_code
        second_detail = e.detail

    inv_second = await _raw_db.inventory.find_one(
        {"product_id": product_id, "branch_id": branch_id}, {"_id": 0, "quantity": 1}
    )
    qty_second = float(inv_second["quantity"])
    movs_second = await _movement_rows(product_id, branch_id, po_id)

    base_ev = {
        "org_id": org_id, "po_id": po_id, "product_id": product_id,
        "branch_id": branch_id, "ordered_qty": QTY,
        "stock_after_first_receive":  qty_first,
        "stock_after_second_attempt": qty_second,
        "movement_count_first":  len(movs_first),
        "movement_count_second": len(movs_second),
        "second_call_status_code": second_status_code,
        "second_call_detail": second_detail,
    }
    record_result(
        scenario="br2.c_duplicate_receive_guard",
        step="second_receive_is_rejected_400",
        expected={"status_code": 400, "rejected": True},
        actual={"status_code": second_status_code,
                "rejected": second_status_code is not None},
        evidence=base_ev,
    )
    record_result(
        scenario="br2.c_duplicate_receive_guard",
        step="stock_does_not_double_up",
        expected={"stock": float(QTY)},
        actual={"stock": qty_second},
        evidence=base_ev,
    )
    record_result(
        scenario="br2.c_duplicate_receive_guard",
        step="no_extra_movement_row_logged",
        expected={"movement_count": 1},
        actual={"movement_count": len(movs_second)},
        evidence=base_ev,
    )

    assert second_status_code == 400, (
        f"br2c expected HTTP 400 on duplicate receive, got {second_status_code} "
        f"(detail={second_detail!r})"
    )
    assert qty_second == float(QTY), (
        f"br2c BUG — stock doubled to {qty_second} after rejected receive "
        f"(should stay {QTY})"
    )
    assert len(movs_second) == 1, (
        f"br2c BUG — movement row count grew to {len(movs_second)} "
        f"after rejected receive (should stay 1)"
    )


# ─────────────────────────────────────────────────────────────────────
# D. Invalid-PO guard — receiving an id that doesn't exist → 404
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_br2d_receive_unknown_po_returns_404(tenant, record_result):
    owner_user = tenant["users"]["owner"]
    bogus_id = "br2-nonexistent-po-zzzz"

    status_code = None
    detail = None
    try:
        await receive_purchase_order(bogus_id,
                                     data={"skip_receipt_check": True},
                                     user=owner_user)
    except HTTPException as e:
        status_code = e.status_code
        detail = e.detail

    record_result(
        scenario="br2.d_invalid_po_guard",
        step="unknown_po_returns_404",
        expected={"status_code": 404},
        actual={"status_code": status_code},
        evidence={"org_id": tenant["org_id"],
                  "attempted_po_id": bogus_id,
                  "detail": detail},
    )
    assert status_code == 404, (
        f"br2d expected HTTP 404 for unknown PO, got {status_code} "
        f"(detail={detail!r})"
    )


# TODO (later business_regression prompts):
#   * br2.e — true partial receive: requires the terminal-finalise flow
#       that overwrites `items[].quantity` before /receive. Not exposed
#       as a single-step API and not safe to fabricate in br2.
#   * br2.f — cash PO end-to-end: needs pre-funded `cashier` wallet,
#       closed-day-guard interactions, and expense-row assertions.
#       Will pair naturally with br4 (PO payment lifecycle).
#   * br2.g — pay-down of the br2.a payable via `/adjust-payment` →
#       belongs to br4 too.

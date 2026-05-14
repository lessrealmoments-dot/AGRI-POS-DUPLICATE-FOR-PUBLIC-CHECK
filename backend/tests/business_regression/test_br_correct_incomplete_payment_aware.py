"""
br_correct_incomplete_payment_aware — end-to-end tests for the
payment-aware refund routing inside `POST /invoices/{id}/correct-incomplete-stock`.

The legacy correction endpoint debited the cashier wallet for every refund
regardless of how the customer paid. This silently created phantom cashier
shortages on credit/digital/split sales. These tests pin the new behaviour:

    * Cash sale       → cashier debited
    * Credit (unpaid) → AR reduced only, NO wallet movement
    * Digital sale    → digital wallet debited (cashier UNTOUCHED)
    * Split sale      → digital reversed first, then cashier for residual
    * Partial-credit  → AR consumed first, residual hits cashier

Plus: day-closed corrections are now ALLOWED when no cash refund is required
(AR-only / digital-only). This unblocks the very real "I forgot to refund
yesterday but Z-report is closed" workflow without touching the closed day's
cashflow.
"""
import pytest

from config import _raw_db
from routes.invoice_corrections import (
    correct_incomplete_stock, IncompleteStockCorrection, IncompleteStockItem,
)


# ── Helpers ──────────────────────────────────────────────────────────────────
async def _seed_invoice(*, org_id: str, branch_id: str, payment_type: str,
                        grand_total: float, amount_paid: float,
                        items: list, payment_method: str = "Cash",
                        digital_platform: str = "", digital_amount: float = 0.0,
                        cash_amount: float = 0.0, customer_id: str = "",
                        payments: list | None = None, order_date: str = "2026-01-01",
                        tag: str = ""):
    import uuid
    iid = f"inv-{org_id[-4:]}-{payment_type}-{int(grand_total)}-{tag or uuid.uuid4().hex[:6]}"
    balance = round(grand_total - amount_paid, 2)
    doc = {
        "id": iid, "organization_id": org_id, "branch_id": branch_id,
        "invoice_number": f"INV-{iid[-6:]}",
        "customer_id": customer_id, "customer_name": "Walk-in",
        "order_date": order_date, "invoice_date": order_date,
        "created_at": f"{order_date}T08:00:00+00:00",
        "payment_type": payment_type, "payment_method": payment_method,
        "digital_platform": digital_platform,
        "cash_amount": cash_amount, "digital_amount": digital_amount,
        "grand_total": grand_total, "amount_paid": amount_paid, "balance": balance,
        "status": "paid" if balance == 0 else ("partial" if amount_paid > 0 else "credit"),
        "items": items, "subtotal": grand_total, "overall_discount": 0, "freight": 0,
        "payments": payments or [],
    }
    await _raw_db.invoices.insert_one(doc)
    return iid, doc


async def _seed_cashier(*, branch_id: str, balance: float):
    """Ensure the branch's cashier wallet exists with the desired balance.
    Uses upsert because BR tenant fixture already seeds wallets at 0."""
    await _raw_db.fund_wallets.update_one(
        {"branch_id": branch_id, "type": "cashier"},
        {"$set": {"balance": float(balance), "name": "Cashier", "active": True}},
        upsert=True,
    )


async def _cashier_balance(branch_id: str) -> float:
    w = await _raw_db.fund_wallets.find_one(
        {"branch_id": branch_id, "type": "cashier", "active": True}, {"_id": 0}
    )
    return float(w["balance"]) if w else 0.0


async def _digital_balance(branch_id: str) -> float:
    w = await _raw_db.fund_wallets.find_one(
        {"branch_id": branch_id, "type": "digital", "active": True}, {"_id": 0}
    )
    return float(w.get("balance", 0)) if w else 0.0


async def _customer_balance(customer_id: str) -> float:
    c = await _raw_db.customers.find_one({"id": customer_id}, {"_id": 0})
    return float(c.get("balance", 0)) if c else 0.0


async def _seed_manager_pin(tenant) -> str:
    """Return the manager PIN already created by the BR tenant fixture."""
    mgr_user = tenant["users"]["manager"]
    row = await _raw_db.users.find_one({"id": mgr_user["id"]}, {"_id": 0})
    return row.get("manager_pin", "")


_ITEM = [{"product_id": "p1", "product_name": "Item A",
          "quantity": 2, "rate": 500.0, "total": 1000.0, "unit": "pc"}]


# ═════════════════════════════════════════════════════════════════════
# Test 1 — Cash invoice baseline: refund debits cashier (regression).
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_pa_correct_1_cash_baseline_debits_cashier(tenant, record_result):
    org_id = tenant["org_id"]
    branch_id = tenant["branches"]["main"]
    pin = await _seed_manager_pin(tenant)
    await _seed_cashier(branch_id=branch_id, balance=10000.0)

    inv_id, _ = await _seed_invoice(
        org_id=org_id, branch_id=branch_id, payment_type="cash",
        grand_total=1000.0, amount_paid=1000.0, items=_ITEM,
    )

    before_cash = await _cashier_balance(branch_id)
    res = await correct_incomplete_stock(
        inv_id,
        IncompleteStockCorrection(
            items=[IncompleteStockItem(
                product_id="p1", product_name="Item A",
                original_qty=2, actual_qty=1, rate=500.0, unit="pc",
            )],
            manager_pin=pin, reprint_receipt=False, notes="test",
        ),
    )
    after_cash = await _cashier_balance(branch_id)

    record_result(
        scenario="br_correct_pa.1_cash_baseline",
        step="cash_sale_correction_debits_cashier_500",
        expected={"refund": 500.0, "cash_delta": -500.0,
                  "ar_reduction": 0.0,
                  "digital_count": 0},
        actual={"refund": res["refund_amount"],
                "cash_delta": round(after_cash - before_cash, 2),
                "ar_reduction": res["refund_allocation"]["ar_reduction"],
                "digital_count": len(res["refund_allocation"]["digital_refunds"])},
    )
    assert res["refund_amount"] == 500.0
    assert round(after_cash - before_cash, 2) == -500.0
    assert res["refund_allocation"]["cash_refund"] == 500.0


# ═════════════════════════════════════════════════════════════════════
# Test 2 — Credit (unpaid) invoice: cashier UNTOUCHED, AR reduced.
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_pa_correct_2_credit_unpaid_no_cashier_debit(tenant, record_result):
    org_id = tenant["org_id"]
    branch_id = tenant["branches"]["main"]
    pin = await _seed_manager_pin(tenant)
    cust_id = tenant["customers"]["credit"]
    await _seed_cashier(branch_id=branch_id, balance=10000.0)

    # Pre-set customer balance to mirror an unpaid credit invoice
    await _raw_db.customers.update_one({"id": cust_id}, {"$set": {"balance": 1000.0}})

    inv_id, _ = await _seed_invoice(
        org_id=org_id, branch_id=branch_id, payment_type="credit",
        grand_total=1000.0, amount_paid=0.0, items=_ITEM,
        customer_id=cust_id,
    )

    before_cash = await _cashier_balance(branch_id)
    before_ar = await _customer_balance(cust_id)
    res = await correct_incomplete_stock(
        inv_id,
        IncompleteStockCorrection(
            items=[IncompleteStockItem(
                product_id="p1", product_name="Item A",
                original_qty=2, actual_qty=1, rate=500.0, unit="pc",
            )],
            manager_pin=pin,
        ),
    )
    after_cash = await _cashier_balance(branch_id)
    after_ar = await _customer_balance(cust_id)

    record_result(
        scenario="br_correct_pa.2_credit_unpaid",
        step="credit_correction_only_reduces_ar_cashier_unchanged",
        expected={"refund": 500.0, "cash_delta": 0.0, "ar_delta": -500.0,
                  "ar_reduction": 500.0, "cash_refund": 0.0},
        actual={"refund": res["refund_amount"],
                "cash_delta": round(after_cash - before_cash, 2),
                "ar_delta": round(after_ar - before_ar, 2),
                "ar_reduction": res["refund_allocation"]["ar_reduction"],
                "cash_refund": res["refund_allocation"]["cash_refund"]},
        evidence={"inv_id": inv_id, "customer_id": cust_id},
    )
    assert round(after_cash - before_cash, 2) == 0.0
    assert round(after_ar - before_ar, 2) == -500.0
    assert res["refund_allocation"]["ar_reduction"] == 500.0
    assert res["refund_allocation"]["cash_refund"] == 0.0


# ═════════════════════════════════════════════════════════════════════
# Test 3 — Digital sale (GCash, fully paid): digital wallet debited,
# cashier wallet untouched.
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_pa_correct_3_digital_routes_to_digital_wallet(tenant, record_result):
    org_id = tenant["org_id"]
    branch_id = tenant["branches"]["main"]
    pin = await _seed_manager_pin(tenant)
    await _seed_cashier(branch_id=branch_id, balance=10000.0)
    # Seed digital wallet w/ balance 5000
    await _raw_db.fund_wallets.insert_one({
        "id": f"w-dig-{branch_id[-6:]}", "branch_id": branch_id,
        "type": "digital", "active": True, "balance": 5000.0, "name": "Digital",
    })

    inv_id, _ = await _seed_invoice(
        org_id=org_id, branch_id=branch_id, payment_type="digital",
        grand_total=1000.0, amount_paid=1000.0, items=_ITEM,
        payment_method="GCash", digital_platform="GCash",
    )

    before_cash = await _cashier_balance(branch_id)
    before_dig = await _digital_balance(branch_id)
    res = await correct_incomplete_stock(
        inv_id,
        IncompleteStockCorrection(
            items=[IncompleteStockItem(
                product_id="p1", product_name="Item A",
                original_qty=2, actual_qty=1, rate=500.0, unit="pc",
            )],
            manager_pin=pin,
        ),
    )
    after_cash = await _cashier_balance(branch_id)
    after_dig = await _digital_balance(branch_id)

    record_result(
        scenario="br_correct_pa.3_digital_invoice",
        step="digital_correction_routes_to_digital_wallet_not_cash",
        expected={"refund": 500.0, "cash_delta": 0.0, "digital_delta": -500.0,
                  "digital_refund_count": 1},
        actual={"refund": res["refund_amount"],
                "cash_delta": round(after_cash - before_cash, 2),
                "digital_delta": round(after_dig - before_dig, 2),
                "digital_refund_count": len(res["refund_allocation"]["digital_refunds"])},
    )
    assert round(after_cash - before_cash, 2) == 0.0
    assert round(after_dig - before_dig, 2) == -500.0


# ═════════════════════════════════════════════════════════════════════
# Test 4 — Split sale: refund > digital_paid → both wallets debited,
# digital first then cashier residual.
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_pa_correct_4_split_overshoot_digital_then_cash(tenant, record_result):
    org_id = tenant["org_id"]
    branch_id = tenant["branches"]["main"]
    pin = await _seed_manager_pin(tenant)
    await _seed_cashier(branch_id=branch_id, balance=10000.0)
    await _raw_db.fund_wallets.insert_one({
        "id": f"w-dig-{branch_id[-6:]}-4", "branch_id": branch_id,
        "type": "digital", "active": True, "balance": 5000.0, "name": "Digital",
    })

    # ₱2000 split sale: ₱500 cash + ₱1500 GCash, items = 2 × ₱1000 = 2000
    items = [{"product_id": "p1", "product_name": "Item A",
              "quantity": 2, "rate": 1000.0, "total": 2000.0, "unit": "pc"}]
    inv_id, _ = await _seed_invoice(
        org_id=org_id, branch_id=branch_id, payment_type="split",
        grand_total=2000.0, amount_paid=2000.0, items=items,
        payment_method="GCash", digital_platform="GCash",
        cash_amount=500.0, digital_amount=1500.0,
    )

    before_cash = await _cashier_balance(branch_id)
    before_dig = await _digital_balance(branch_id)
    # Refund 1 unit = ₱1000. Allocator: AR=0, digital=1000 (still has room),
    # cash=0.
    res = await correct_incomplete_stock(
        inv_id,
        IncompleteStockCorrection(
            items=[IncompleteStockItem(
                product_id="p1", product_name="Item A",
                original_qty=2, actual_qty=1, rate=1000.0, unit="pc",
            )],
            manager_pin=pin,
        ),
    )
    after_cash = await _cashier_balance(branch_id)
    after_dig = await _digital_balance(branch_id)

    record_result(
        scenario="br_correct_pa.4_split_within_digital",
        step="split_correction_within_digital_budget_does_not_touch_cash",
        expected={"refund": 1000.0, "cash_delta": 0.0, "digital_delta": -1000.0},
        actual={"refund": res["refund_amount"],
                "cash_delta": round(after_cash - before_cash, 2),
                "digital_delta": round(after_dig - before_dig, 2)},
    )
    assert round(after_dig - before_dig, 2) == -1000.0
    assert round(after_cash - before_cash, 2) == 0.0


# ═════════════════════════════════════════════════════════════════════
# Test 5 — Partial-credit invoice: AR consumed first, then cashier.
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_pa_correct_5_partial_credit_ar_then_cash(tenant, record_result):
    org_id = tenant["org_id"]
    branch_id = tenant["branches"]["main"]
    pin = await _seed_manager_pin(tenant)
    cust_id = tenant["customers"]["credit"]
    await _seed_cashier(branch_id=branch_id, balance=10000.0)
    await _raw_db.customers.update_one({"id": cust_id}, {"$set": {"balance": 700.0}})

    # 2 items @ ₱500 = ₱1000 invoice. ₱300 paid cash, ₱700 still on AR.
    inv_id, _ = await _seed_invoice(
        org_id=org_id, branch_id=branch_id, payment_type="partial",
        grand_total=1000.0, amount_paid=300.0, items=_ITEM,
        payment_method="Cash", customer_id=cust_id,
    )

    before_cash = await _cashier_balance(branch_id)
    before_ar = await _customer_balance(cust_id)
    # Refund both items = ₱1000.
    # Allocator: ar=700, cash=300 (capped at cash_paid).
    res = await correct_incomplete_stock(
        inv_id,
        IncompleteStockCorrection(
            items=[IncompleteStockItem(
                product_id="p1", product_name="Item A",
                original_qty=2, actual_qty=0, rate=500.0, unit="pc",
            )],
            manager_pin=pin,
        ),
    )
    after_cash = await _cashier_balance(branch_id)
    after_ar = await _customer_balance(cust_id)

    record_result(
        scenario="br_correct_pa.5_partial_credit",
        step="partial_correction_drains_ar_first_then_cash",
        expected={"ar_reduction": 700.0, "cash_refund": 300.0,
                  "cash_delta": -300.0, "ar_delta": -700.0},
        actual={"ar_reduction": res["refund_allocation"]["ar_reduction"],
                "cash_refund":   res["refund_allocation"]["cash_refund"],
                "cash_delta":    round(after_cash - before_cash, 2),
                "ar_delta":      round(after_ar - before_ar, 2)},
    )
    assert res["refund_allocation"]["ar_reduction"] == 700.0
    assert res["refund_allocation"]["cash_refund"]  == 300.0
    assert round(after_cash - before_cash, 2) == -300.0


# ═════════════════════════════════════════════════════════════════════
# Test 6 — Day-closed gate: AR-only corrections are now ALLOWED.
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_pa_correct_6_day_closed_ar_only_allowed(tenant, record_result):
    org_id = tenant["org_id"]
    branch_id = tenant["branches"]["main"]
    pin = await _seed_manager_pin(tenant)
    cust_id = tenant["customers"]["credit"]
    await _seed_cashier(branch_id=branch_id, balance=10000.0)
    await _raw_db.customers.update_one({"id": cust_id}, {"$set": {"balance": 1000.0}})

    closed_day = "2025-12-31"
    inv_id, _ = await _seed_invoice(
        org_id=org_id, branch_id=branch_id, payment_type="credit",
        grand_total=1000.0, amount_paid=0.0, items=_ITEM,
        customer_id=cust_id, order_date=closed_day,
    )
    await _raw_db.daily_closings.insert_one({
        "id": f"close-{branch_id[-4:]}", "branch_id": branch_id,
        "organization_id": org_id, "date": closed_day, "status": "closed",
    })

    res = await correct_incomplete_stock(
        inv_id,
        IncompleteStockCorrection(
            items=[IncompleteStockItem(
                product_id="p1", product_name="Item A",
                original_qty=2, actual_qty=1, rate=500.0, unit="pc",
            )],
            manager_pin=pin,
        ),
    )
    record_result(
        scenario="br_correct_pa.6_day_closed_ar_only",
        step="day_closed_ar_only_correction_allowed",
        expected={"success": True, "cash_refund": 0.0, "ar_reduction": 500.0},
        actual={"success": res["success"],
                "cash_refund": res["refund_allocation"]["cash_refund"],
                "ar_reduction": res["refund_allocation"]["ar_reduction"]},
    )
    assert res["success"] is True
    assert res["refund_allocation"]["cash_refund"] == 0.0


# ═════════════════════════════════════════════════════════════════════
# Test 7 — Day-closed gate: cash correction is ALLOWED (Feb 2026 owner
# decision). The closed day's Z-report stays untouched; refund impact
# lives in TODAY's wallet_movements + expenses + inventory reversal,
# so count-sheets still catch any physical-vs-system mismatch.
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_pa_correct_7_day_closed_cash_now_allowed(tenant, record_result):
    org_id = tenant["org_id"]
    branch_id = tenant["branches"]["main"]
    pin = await _seed_manager_pin(tenant)
    await _seed_cashier(branch_id=branch_id, balance=10000.0)

    closed_day = "2025-11-30"
    inv_id, _ = await _seed_invoice(
        org_id=org_id, branch_id=branch_id, payment_type="cash",
        grand_total=1000.0, amount_paid=1000.0, items=_ITEM,
        order_date=closed_day, tag="day-closed-cash",
    )
    await _raw_db.daily_closings.insert_one({
        "id": f"close-{branch_id[-4:]}-2", "branch_id": branch_id,
        "organization_id": org_id, "date": closed_day, "status": "closed",
    })

    before_cash = await _cashier_balance(branch_id)
    res = await correct_incomplete_stock(
        inv_id,
        IncompleteStockCorrection(
            items=[IncompleteStockItem(
                product_id="p1", product_name="Item A",
                original_qty=2, actual_qty=1, rate=500.0, unit="pc",
            )],
            manager_pin=pin,
        ),
    )
    after_cash = await _cashier_balance(branch_id)

    # The closed day's Z-report stays untouched (we don't write back to
    # that date); the cash refund hits TODAY's cashier wallet.
    record_result(
        scenario="br_correct_pa.7_day_closed_cash_allowed",
        step="day_closed_cash_correction_succeeds_with_today_dated_audit",
        expected={"success": True, "cash_refund": 500.0,
                  "cash_delta": -500.0, "items_returned": 1},
        actual={"success": res["success"],
                "cash_refund": res["refund_allocation"]["cash_refund"],
                "cash_delta": round(after_cash - before_cash, 2),
                "items_returned": res["items_returned"]},
    )
    assert res["success"] is True
    assert res["refund_allocation"]["cash_refund"] == 500.0
    assert round(after_cash - before_cash, 2) == -500.0
    assert res["items_returned"] == 1  # inventory reversal happened


# ═════════════════════════════════════════════════════════════════════
# Test 8 — Inventory reversal is the audit floor: even if the product
# row is missing/zero in the branch inventory (e.g. the stock truly
# wasn't available), the correction still bumps `inventory.quantity`
# upward. Count-sheets later flag the physical-vs-system gap and the
# owner has the paper trail to investigate.
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_pa_correct_8_inventory_reversal_always_applies(tenant, record_result):
    org_id = tenant["org_id"]
    branch_id = tenant["branches"]["main"]
    pin = await _seed_manager_pin(tenant)
    await _seed_cashier(branch_id=branch_id, balance=10000.0)

    # Wipe any pre-existing inventory row for p1 in this branch so we
    # start from "no row" — the correction must upsert it.
    await _raw_db.inventory.delete_many(
        {"branch_id": branch_id, "product_id": "p-audit"}
    )

    items = [{"product_id": "p-audit", "product_name": "Audit Item",
              "quantity": 5, "rate": 100.0, "total": 500.0, "unit": "pc"}]
    inv_id, _ = await _seed_invoice(
        org_id=org_id, branch_id=branch_id, payment_type="cash",
        grand_total=500.0, amount_paid=500.0, items=items, tag="audit",
    )

    res = await correct_incomplete_stock(
        inv_id,
        IncompleteStockCorrection(
            items=[IncompleteStockItem(
                product_id="p-audit", product_name="Audit Item",
                original_qty=5, actual_qty=3, rate=100.0, unit="pc",
            )],
            manager_pin=pin,
        ),
    )

    inv_row = await _raw_db.inventory.find_one(
        {"branch_id": branch_id, "product_id": "p-audit"}, {"_id": 0}
    )
    movement = await _raw_db.inventory_movements.find_one(
        {"branch_id": branch_id, "product_id": "p-audit",
         "movement_type": "incomplete_stock_return"},
        {"_id": 0}, sort=[("created_at", -1)],
    )

    record_result(
        scenario="br_correct_pa.8_inventory_reversal_always",
        step="inventory_bumped_and_movement_logged_even_with_no_prior_row",
        expected={"inventory_qty": 2.0, "movement_recorded": True,
                  "items_returned": 1, "refund": 200.0},
        actual={"inventory_qty": float(inv_row["quantity"]) if inv_row else None,
                "movement_recorded": movement is not None,
                "items_returned": res["items_returned"],
                "refund": res["refund_amount"]},
        evidence={"inv_row": inv_row, "movement_qty": (movement or {}).get("quantity")},
    )
    assert inv_row is not None and float(inv_row["quantity"]) == 2.0
    assert movement is not None and float(movement["quantity"]) == 2.0
    assert res["refund_amount"] == 200.0

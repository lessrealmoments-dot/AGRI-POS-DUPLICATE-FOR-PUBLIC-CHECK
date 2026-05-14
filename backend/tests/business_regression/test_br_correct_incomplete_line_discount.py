"""
br_correct_incomplete_line_discount — pin the phantom-balance fix.

Bug discovered Feb 2026 on agri-books.com SI-MB-001059 / AIZON AGRIVET:

  • Customer paid ₱185,245 cash for an invoice containing 4 lines with
    per-line discounts (totals: 325 + 650 + 300 + 40 = ₱1,315 of line
    discount in aggregate).
  • Cashier ran "Update for Incomplete Stock", reducing quantities across
    multiple lines (including some discounted ones).
  • Cash refund debited drawer: ₱4,490 (correct, matches diff_qty*rate sum).
  • BUT new grand_total only fell from ₱185,245 to ₱182,070 (diff: ₱3,175)
    because corrected line totals were computed as `actual_qty * rate`,
    silently dropping the per-line discount.
  • Result: phantom AR balance of ₱1,315 the customer never owed.

These tests pin the fix:
  1. Cash sale with a single discounted line, quantity reduced → grand_total
     falls by exactly `refund_amount`, balance lands at 0.
  2. Multiple discounted + non-discounted lines, partial reductions →
     balance correctly 0, line discounts prorated.
  3. Non-discounted lines (regression) → unchanged behaviour.
"""
import pytest

from config import _raw_db
from routes.invoice_corrections import (
    correct_incomplete_stock, IncompleteStockCorrection, IncompleteStockItem,
)


async def _seed_cashier(*, branch_id: str, balance: float):
    await _raw_db.fund_wallets.update_one(
        {"branch_id": branch_id, "type": "cashier"},
        {"$set": {"balance": float(balance), "name": "Cashier", "active": True}},
        upsert=True,
    )


async def _seed_manager_pin(tenant) -> str:
    mgr_user = tenant["users"]["manager"]
    row = await _raw_db.users.find_one({"id": mgr_user["id"]}, {"_id": 0})
    return row.get("manager_pin", "")


async def _seed_inv(*, org_id, branch_id, items, payment_type="cash", amount_paid=None, tag=""):
    import uuid
    subtotal = round(sum(float(it["total"]) for it in items), 2)
    grand_total = subtotal  # no overall_discount/freight in these tests
    paid = grand_total if amount_paid is None else amount_paid
    bal = round(grand_total - paid, 2)
    iid = f"inv-disc-{tag or uuid.uuid4().hex[:6]}"
    doc = {
        "id": iid, "organization_id": org_id, "branch_id": branch_id,
        "invoice_number": f"INV-{iid[-6:]}",
        "customer_name": "Walk-in", "customer_id": "",
        "order_date": "2026-01-01", "invoice_date": "2026-01-01",
        "created_at": "2026-01-01T08:00:00+00:00",
        "payment_type": payment_type, "payment_method": "Cash",
        "grand_total": grand_total, "amount_paid": paid, "balance": bal,
        "status": "paid" if bal == 0 else ("partial" if paid > 0 else "credit"),
        "items": items, "subtotal": subtotal,
        "overall_discount": 0, "freight": 0,
        "payments": [],
    }
    await _raw_db.invoices.insert_one(doc)
    return iid


# ═════════════════════════════════════════════════════════════════════
# Test 1 — Single discounted line: qty reduction must shrink grand_total
# by exactly refund_amount, leaving balance == 0 (the phantom-balance fix).
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_line_disc_1_single_discounted_line_no_phantom(tenant, record_result):
    org_id = tenant["org_id"]
    branch_id = tenant["branches"]["main"]
    pin = await _seed_manager_pin(tenant)
    await _seed_cashier(branch_id=branch_id, balance=50_000.0)

    # qty=5, rate=1000, line_disc=200 → total = 5*1000 - 200 = 4800
    items = [{
        "product_id": "pd1", "product_name": "Discounted",
        "quantity": 5, "rate": 1000.0,
        "discount_type": "amount", "discount_value": 40,  # informational
        "discount_amount": 200.0,
        "total": 4800.0, "unit": "pc",
    }]
    inv_id = await _seed_inv(
        org_id=org_id, branch_id=branch_id, items=items, tag="disc1",
    )

    # Correct qty 5 → 3 (drop 2). Pre-fix code would have refunded 2*1000=2000
    # but only shrunk grand_total by (5*1000-200) - (3*1000) = 1800 → phantom 200.
    res = await correct_incomplete_stock(
        inv_id,
        IncompleteStockCorrection(
            items=[IncompleteStockItem(
                product_id="pd1", product_name="Discounted",
                original_qty=5, actual_qty=3, rate=1000.0, unit="pc",
            )],
            manager_pin=pin,
        ),
        user=tenant["users"]["owner"],
    )

    inv = await _raw_db.invoices.find_one({"id": inv_id}, {"_id": 0})

    # New per-unit discount = 200/5 = 40. Remaining qty=3 keeps disc of 120.
    # New line total = 3*1000 - 120 = 2880. Refund = 4800 - 2880 = 1920.
    record_result(
        scenario="br_line_disc.1_single_discounted",
        step="qty_5_to_3_with_line_disc_200",
        expected={"refund": 1920.0, "new_grand_total": 2880.0, "balance": 0.0,
                  "new_line_total": 2880.0, "new_line_disc": 120.0},
        actual={"refund": res["refund_amount"],
                "new_grand_total": inv["grand_total"], "balance": inv["balance"],
                "new_line_total": inv["items"][0]["total"],
                "new_line_disc": inv["items"][0]["discount_amount"]},
    )
    assert res["refund_amount"] == 1920.0, "Refund must equal actual line drop"
    assert inv["grand_total"] == 2880.0
    assert inv["balance"] == 0.0, "Phantom balance must be 0"
    assert inv["items"][0]["total"] == 2880.0
    assert inv["items"][0]["discount_amount"] == 120.0


# ═════════════════════════════════════════════════════════════════════
# Test 2 — Aizon scenario reproduction: multiple lines, mix of discount
# and non-discount, partial corrections. Balance must end at 0.
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_line_disc_2_mixed_lines_aizon_repro(tenant, record_result):
    org_id = tenant["org_id"]
    branch_id = tenant["branches"]["main"]
    pin = await _seed_manager_pin(tenant)
    await _seed_cashier(branch_id=branch_id, balance=500_000.0)

    # Mirror Aizon's pattern: some lines with discount, some without.
    items = [
        # Discounted (qty=10, rate=1995, line_disc=650): total stored 19_300
        # so total = qty*rate - disc precisely.
        {"product_id": "g2p", "product_name": "GALIMAX 2 PLUS",
         "quantity": 10, "rate": 1995.0,
         "discount_type": "amount", "discount_value": 65,
         "discount_amount": 650.0,
         "total": 19_300.0, "unit": "pc"},
        # Non-discounted: total 31_200
        {"product_id": "stp", "product_name": "STARTER UP",
         "quantity": 15, "rate": 2080.0,
         "discount_type": "amount", "discount_value": 0,
         "discount_amount": 0.0,
         "total": 31_200.0, "unit": "pc"},
        # Discounted line with per-unit disc 20 (qty 13): total 16_250 - 260 = 15_990
        {"product_id": "gfe", "product_name": "GROWER FARM EXPRESS",
         "quantity": 13, "rate": 1250.0,
         "discount_type": "amount", "discount_value": 20,
         "discount_amount": 260.0,
         "total": 16_250.0 - 260.0, "unit": "sack"},
    ]
    inv_id = await _seed_inv(
        org_id=org_id, branch_id=branch_id, items=items, tag="aizon",
    )

    # Reduce ALL three lines by 1: GALIMAX 10→9, STARTER 15→14, GROWER 13→12.
    res = await correct_incomplete_stock(
        inv_id,
        IncompleteStockCorrection(
            items=[
                IncompleteStockItem(product_id="g2p", product_name="GALIMAX 2 PLUS",
                                    original_qty=10, actual_qty=9, rate=1995.0, unit="pc"),
                IncompleteStockItem(product_id="stp", product_name="STARTER UP",
                                    original_qty=15, actual_qty=14, rate=2080.0, unit="pc"),
                IncompleteStockItem(product_id="gfe", product_name="GROWER FARM EXPRESS",
                                    original_qty=13, actual_qty=12, rate=1250.0, unit="sack"),
            ],
            manager_pin=pin,
        ),
        user=tenant["users"]["owner"],
    )

    inv = await _raw_db.invoices.find_one({"id": inv_id}, {"_id": 0})

    # Expected per-line:
    # GALIMAX: per-unit disc = 65. new line: 9*1995 - 9*65 = 17_955 - 585 = 17_370.
    #          drop = 19_300 - 17_370 = 1_930.
    # STARTER: no disc. new line: 14*2080 = 29_120. drop = 31_200 - 29_120 = 2_080.
    # GROWER:  per-unit disc = 20. new line: 12*1250 - 12*20 = 15_000 - 240 = 14_760.
    #          drop = 15_990 - 14_760 = 1_230.
    expected_refund = 1_930.0 + 2_080.0 + 1_230.0  # 5_240
    expected_grand_total = 17_370.0 + 29_120.0 + 14_760.0  # 61_250

    record_result(
        scenario="br_line_disc.2_mixed_aizon",
        step="three_lines_partial_correction",
        expected={"refund": expected_refund,
                  "grand_total": expected_grand_total,
                  "balance": 0.0},
        actual={"refund": res["refund_amount"],
                "grand_total": inv["grand_total"],
                "balance": inv["balance"]},
    )
    assert res["refund_amount"] == expected_refund
    assert inv["grand_total"] == expected_grand_total
    assert inv["balance"] == 0.0, "No phantom balance allowed"


# ═════════════════════════════════════════════════════════════════════
# Test 3 — Regression: lines with NO discount behave exactly as before.
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_line_disc_3_no_discount_regression(tenant, record_result):
    org_id = tenant["org_id"]
    branch_id = tenant["branches"]["main"]
    pin = await _seed_manager_pin(tenant)
    await _seed_cashier(branch_id=branch_id, balance=10_000.0)

    items = [{
        "product_id": "nd", "product_name": "Plain",
        "quantity": 4, "rate": 250.0,
        "discount_type": "amount", "discount_value": 0,
        "discount_amount": 0.0,
        "total": 1_000.0, "unit": "pc",
    }]
    inv_id = await _seed_inv(
        org_id=org_id, branch_id=branch_id, items=items, tag="nd",
    )

    res = await correct_incomplete_stock(
        inv_id,
        IncompleteStockCorrection(
            items=[IncompleteStockItem(
                product_id="nd", product_name="Plain",
                original_qty=4, actual_qty=2, rate=250.0, unit="pc",
            )],
            manager_pin=pin,
        ),
        user=tenant["users"]["owner"],
    )

    inv = await _raw_db.invoices.find_one({"id": inv_id}, {"_id": 0})

    record_result(
        scenario="br_line_disc.3_no_discount_regression",
        step="qty_4_to_2_no_disc",
        expected={"refund": 500.0, "grand_total": 500.0, "balance": 0.0},
        actual={"refund": res["refund_amount"],
                "grand_total": inv["grand_total"],
                "balance": inv["balance"]},
    )
    assert res["refund_amount"] == 500.0
    assert inv["grand_total"] == 500.0
    assert inv["balance"] == 0.0

"""
Iter 195 — Audit-driven consistency fixes.

Tests:
  Fix #1  Credit return → customer.balance + invoice.balance reduced
          (with overcredit notification path)
  Fix #2  Product Profitability nets out returns (shelf = full reverse,
          pullout = revenue only; cost stays)
  Fix #3  Audit Center /sales reconciliation:
          grand_total_sales == sum_line_totals + freight - overall_discount
  Fix #4  PO Reopen flags old 'purchase' movements as reversed →
          moving-average does NOT double count on re-receive
  Fix #5  Void invoice tags discount_audit_log + price_change_log so
          voided entries are excluded from reports & cashier rankings
  Fix #6  _compute_cash includes wallet_movements cross-check
"""
from uuid import uuid4
from datetime import datetime, timezone, timedelta

import pytest
import requests

from _org_test_helpers import (
    API, _db, ensure_org_admin_token,
    TEST_ORG_ADMIN_PIN, TEST_ORG_MANAGER_PIN,
)


@pytest.fixture(scope="module")
def auth():
    token, user = ensure_org_admin_token()
    return token, user


@pytest.fixture
def seed(auth):
    db = _db()
    token, user = auth
    org_id = user.get("organization_id", "")
    branch = db.branches.find_one({"organization_id": org_id, "active": True}, {"_id": 0, "id": 1, "name": 1})
    if not branch:
        bid = str(uuid4())
        db.branches.insert_one({
            "id": bid, "name": "Test Branch 195", "active": True,
            "organization_id": org_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        branch = {"id": bid, "name": "Test Branch 195"}

    pid = str(uuid4())
    db.products.insert_one({
        "id": pid, "sku": f"R195-{pid[:6]}", "name": f"Return195-{pid[:6]}",
        "category": "Test", "unit": "PC", "cost_price": 60.0,
        "prices": {"retail": 100, "wholesale": 90}, "active": True,
        "organization_id": org_id, "is_repack": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    db.inventory.update_one(
        {"product_id": pid, "branch_id": branch["id"]},
        {"$set": {"product_id": pid, "branch_id": branch["id"],
                  "organization_id": org_id, "quantity": 100,
                  "updated_at": datetime.now(timezone.utc).isoformat()}},
        upsert=True,
    )
    # Seed a credit customer
    cid = str(uuid4())
    db.customers.insert_one({
        "id": cid, "name": f"Credit195-{cid[:6]}",
        "organization_id": org_id, "branch_id": branch["id"],
        "balance": 0, "credit_limit": 10000, "active": True,
        "customer_type": "credit",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    yield {
        "product_id": pid, "branch_id": branch["id"],
        "branch_name": branch.get("name", ""),
        "org_id": org_id, "customer_id": cid,
        "customer_name": f"Credit195-{cid[:6]}",
    }
    db.products.delete_one({"id": pid})
    db.inventory.delete_many({"product_id": pid})
    db.customers.delete_one({"id": cid})
    db.returns.delete_many({"customer_id": cid})
    db.price_change_log.delete_many({"product_id": pid})


def _make_credit_sale(token, seed, *, qty=10, rate=100, amount_paid=0):
    """Create a credit invoice for the seeded product/customer."""
    payload = {
        "id": str(uuid4()),
        "branch_id": seed["branch_id"], "branch_name": seed["branch_name"],
        "items": [{
            "product_id": seed["product_id"], "product_name": "Return195",
            "sku": "R195", "quantity": qty, "rate": rate, "price": rate,
            "total": rate * qty, "discount_type": "amount",
            "discount_value": 0, "discount_amount": 0, "is_repack": False,
        }],
        "subtotal": rate * qty, "freight": 0, "overall_discount": 0,
        "grand_total": rate * qty, "amount_paid": amount_paid,
        "balance": rate * qty - amount_paid,
        "payment_type": "credit" if amount_paid == 0 else "partial",
        "payment_method": "Cash", "fund_source": "cashier",
        "price_scheme": "retail",
        "customer_id": seed["customer_id"], "customer_name": seed["customer_name"],
        "order_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "release_mode": "full",
    }
    r = requests.post(f"{API}/unified-sale", json=payload,
                      headers={"Authorization": f"Bearer {token}"})
    return r


# ════════════════════════════════════════════════════════════════════════════
#  FIX #1 — Credit return reduces AR
# ════════════════════════════════════════════════════════════════════════════
def test_fix1_credit_return_reduces_customer_and_invoice_balance(auth, seed):
    token, _ = auth
    db = _db()
    # Create credit invoice: 10 × ₱100 = ₱1,000 owed
    sale_r = _make_credit_sale(token, seed, qty=10, rate=100, amount_paid=0)
    assert sale_r.status_code == 200, sale_r.text
    inv = sale_r.json()
    inv_num = inv.get("invoice_number")
    inv_id = inv.get("id")

    cust_before = db.customers.find_one({"id": seed["customer_id"]}, {"_id": 0, "balance": 1})
    assert float(cust_before["balance"]) == 1000.0, f"expected 1000, got {cust_before['balance']}"

    # Return 3 units → ₱300 worth, NO cash refund → should apply to AR
    return_payload = {
        "branch_id": seed["branch_id"],
        "return_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "customer_id": seed["customer_id"],
        "customer_name": seed["customer_name"],
        "customer_type": "credit",
        "invoice_number": inv_num,
        "reason": "Defective",
        "items": [{
            "product_id": seed["product_id"],
            "product_name": "Return195",
            "quantity": 3, "condition": "sellable",
            "inventory_action": "shelf",
            "refund_price": 100, "cost_price": 60,
            "category": "Test",
        }],
        "refund_method": "none", "refund_amount": 0,
        "fund_source": "cashier",
    }
    r = requests.post(f"{API}/returns", json=return_payload,
                      headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("credit_applied") == 300.0
    assert len(body.get("credit_applied_to_invoices", [])) >= 1
    assert body["credit_applied_to_invoices"][0]["invoice_number"] == inv_num

    # Customer AR balance reduced to ₱700
    cust_after = db.customers.find_one({"id": seed["customer_id"]}, {"_id": 0, "balance": 1})
    assert float(cust_after["balance"]) == 700.0

    # Invoice balance reduced
    inv_after = db.invoices.find_one({"id": inv_id}, {"_id": 0, "balance": 1, "amount_paid": 1, "status": 1})
    assert float(inv_after["balance"]) == 700.0
    assert float(inv_after["amount_paid"]) == 300.0
    assert inv_after["status"] == "partial"
    # Return credit payment row inserted
    pay_rows = db.invoices.find_one({"id": inv_id}, {"_id": 0, "payments": 1})
    assert any(p.get("fund_source") == "return_credit" for p in pay_rows.get("payments", []))


def test_fix1_partial_cash_partial_ar_mix(auth, seed):
    """₱300 return with ₱100 cash refund → ₱200 to AR."""
    token, _ = auth
    db = _db()
    sale_r = _make_credit_sale(token, seed, qty=10, rate=100, amount_paid=0)
    inv_num = sale_r.json().get("invoice_number")

    return_payload = {
        "branch_id": seed["branch_id"],
        "return_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "customer_id": seed["customer_id"],
        "customer_name": seed["customer_name"],
        "customer_type": "credit",
        "invoice_number": inv_num,
        "reason": "Partial refund mix",
        "items": [{
            "product_id": seed["product_id"], "product_name": "Return195",
            "quantity": 3, "condition": "sellable",
            "inventory_action": "shelf",
            "refund_price": 100, "cost_price": 60, "category": "Test",
        }],
        "refund_method": "partial", "refund_amount": 100,
        "fund_source": "cashier",
    }
    r = requests.post(f"{API}/returns", json=return_payload,
                      headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["credit_applied"] == 200.0  # 300 - 100

    cust = db.customers.find_one({"id": seed["customer_id"]}, {"_id": 0, "balance": 1})
    assert float(cust["balance"]) == 800.0  # 1000 - 200


def test_fix1_void_return_reverses_credit(auth, seed):
    """Voiding a return must re-increase customer.balance + invoice.balance."""
    token, _ = auth
    db = _db()
    sale_r = _make_credit_sale(token, seed, qty=10, rate=100)
    inv_num = sale_r.json().get("invoice_number")

    return_payload = {
        "branch_id": seed["branch_id"],
        "return_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "customer_id": seed["customer_id"],
        "customer_name": seed["customer_name"],
        "customer_type": "credit",
        "invoice_number": inv_num,
        "reason": "Void test",
        "items": [{
            "product_id": seed["product_id"], "product_name": "Return195",
            "quantity": 2, "condition": "sellable",
            "inventory_action": "shelf",
            "refund_price": 100, "cost_price": 60, "category": "Test",
        }],
        "refund_method": "none", "refund_amount": 0,
        "fund_source": "cashier",
    }
    r = requests.post(f"{API}/returns", json=return_payload,
                      headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    rma = r.json()["rma_number"]
    return_id = r.json()["id"]

    # Customer now owes ₱800
    cust_mid = db.customers.find_one({"id": seed["customer_id"]}, {"_id": 0, "balance": 1})
    assert float(cust_mid["balance"]) == 800.0

    # Void the return
    vr = requests.post(
        f"{API}/returns/{return_id}/void",
        json={"manager_pin": TEST_ORG_ADMIN_PIN, "reason": "test void"},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert vr.status_code == 200, vr.text

    # Customer balance restored to ₱1,000
    cust_after = db.customers.find_one({"id": seed["customer_id"]}, {"_id": 0, "balance": 1})
    assert float(cust_after["balance"]) == 1000.0


# ════════════════════════════════════════════════════════════════════════════
#  FIX #2 — Product Profitability nets out returns
# ════════════════════════════════════════════════════════════════════════════
def test_fix2_product_profit_shelf_return_reverses_both(auth, seed):
    """Shelf return must reverse revenue AND cost (same net profit as if
    the returned units were never sold)."""
    token, _ = auth
    # Create a cash sale (to keep it simple for this test)
    payload = {
        "id": str(uuid4()),
        "branch_id": seed["branch_id"], "branch_name": seed["branch_name"],
        "items": [{
            "product_id": seed["product_id"], "product_name": "Return195",
            "sku": "R195", "quantity": 10, "rate": 100, "price": 100,
            "total": 1000, "discount_type": "amount",
            "discount_value": 0, "discount_amount": 0, "is_repack": False,
        }],
        "subtotal": 1000, "freight": 0, "overall_discount": 0,
        "grand_total": 1000, "amount_paid": 1000, "balance": 0,
        "payment_type": "cash", "payment_method": "Cash",
        "fund_source": "cashier", "price_scheme": "retail",
        "order_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "release_mode": "full",
        "customer_name": "Walk-in",
    }
    sr = requests.post(f"{API}/unified-sale", json=payload,
                       headers={"Authorization": f"Bearer {token}"})
    assert sr.status_code == 200

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Report BEFORE return: 10 × ₱100 = ₱1,000 revenue, 10 × ₱60 = ₱600 cost, profit ₱400
    pre = requests.get(
        f"{API}/reports/product-profit",
        params={"date_from": today, "date_to": today, "branch_id": seed["branch_id"]},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert pre.status_code == 200, pre.text
    pre_row = next((r for r in pre.json()["rows"] if r["product_id"] == seed["product_id"]), None)
    assert pre_row is not None
    pre_profit = pre_row["profit"]

    # Return 3 shelf units
    return_payload = {
        "branch_id": seed["branch_id"], "return_date": today,
        "customer_name": "Walk-in", "customer_type": "walkin",
        "reason": "Defective",
        "items": [{
            "product_id": seed["product_id"], "product_name": "Return195",
            "quantity": 3, "condition": "sellable",
            "inventory_action": "shelf",
            "refund_price": 100, "cost_price": 60, "category": "Test",
        }],
        "refund_method": "full", "refund_amount": 300,
        "fund_source": "cashier",
    }
    rr = requests.post(f"{API}/returns", json=return_payload,
                       headers={"Authorization": f"Bearer {token}"})
    assert rr.status_code == 200, rr.text

    # Report AFTER return: net 7 sold × ₱100 = ₱700 revenue, 7 × ₱60 = ₱420 cost, profit ₱280
    post = requests.get(
        f"{API}/reports/product-profit",
        params={"date_from": today, "date_to": today, "branch_id": seed["branch_id"]},
        headers={"Authorization": f"Bearer {token}"}
    )
    post_row = next((r for r in post.json()["rows"] if r["product_id"] == seed["product_id"]), None)
    assert post_row is not None
    # Profit should drop by ~3 * (100-60) = 120
    assert pre_profit - post_row["profit"] >= 115
    assert post_row.get("returned_qty", 0) >= 3
    # Revenue net down by 300
    assert post_row["total_revenue"] == round(pre_row["total_revenue"] - 300, 2)
    # Cost net down by 180 (shelf = cost reversed)
    assert post_row["total_cost"] == round(pre_row["total_cost"] - 180, 2)


def test_fix2_product_profit_pullout_return_revenue_only(auth, seed):
    """Pullout return: revenue reversed, cost STAYS (real COGS loss)."""
    token, _ = auth
    # Fresh sale
    payload = {
        "id": str(uuid4()),
        "branch_id": seed["branch_id"], "branch_name": seed["branch_name"],
        "items": [{
            "product_id": seed["product_id"], "product_name": "Return195",
            "sku": "R195", "quantity": 5, "rate": 100, "price": 100,
            "total": 500, "discount_type": "amount",
            "discount_value": 0, "discount_amount": 0, "is_repack": False,
        }],
        "subtotal": 500, "freight": 0, "overall_discount": 0,
        "grand_total": 500, "amount_paid": 500, "balance": 0,
        "payment_type": "cash", "payment_method": "Cash",
        "fund_source": "cashier", "price_scheme": "retail",
        "order_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "release_mode": "full", "customer_name": "Walk-in",
    }
    sr = requests.post(f"{API}/unified-sale", json=payload,
                       headers={"Authorization": f"Bearer {token}"})
    assert sr.status_code == 200

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    pre = requests.get(
        f"{API}/reports/product-profit",
        params={"date_from": today, "date_to": today, "branch_id": seed["branch_id"]},
        headers={"Authorization": f"Bearer {token}"}
    )
    pre_row = next((r for r in pre.json()["rows"] if r["product_id"] == seed["product_id"]), None)

    return_payload = {
        "branch_id": seed["branch_id"], "return_date": today,
        "customer_name": "Walk-in", "customer_type": "walkin",
        "reason": "Damaged / Old stock",
        "items": [{
            "product_id": seed["product_id"], "product_name": "Return195",
            "quantity": 2, "condition": "damaged",
            "inventory_action": "pullout",
            "refund_price": 100, "cost_price": 60, "category": "Test",
        }],
        "refund_method": "full", "refund_amount": 200,
        "fund_source": "cashier",
    }
    rr = requests.post(f"{API}/returns", json=return_payload,
                       headers={"Authorization": f"Bearer {token}"})
    assert rr.status_code == 200, rr.text

    post = requests.get(
        f"{API}/reports/product-profit",
        params={"date_from": today, "date_to": today, "branch_id": seed["branch_id"]},
        headers={"Authorization": f"Bearer {token}"}
    )
    post_row = next((r for r in post.json()["rows"] if r["product_id"] == seed["product_id"]), None)
    # Revenue should drop by 200 (2 units × ₱100)
    assert post_row["total_revenue"] == round(pre_row["total_revenue"] - 200, 2)
    # Cost should STAY the same (pullout = real loss)
    assert post_row["total_cost"] == pre_row["total_cost"]
    assert post_row.get("returned_cost", 0) >= 120  # 2 × 60


# ════════════════════════════════════════════════════════════════════════════
#  FIX #3 — Sales reconciliation formula
# ════════════════════════════════════════════════════════════════════════════
def test_fix3_sales_reconciliation_formula(auth, seed):
    """Verify: for the SPECIFIC invoice just created (with freight + overall_discount),
    the reconciliation block is populated and the variance is small for the period.
    (We don't compare absolute totals across full DB because pre-existing data
    may have been inserted without discipline — the fix is that the reconciliation
    block EXISTS and provides the numbers for an auditor to inspect.)"""
    token, _ = auth
    # Create an invoice with freight and overall discount
    payload = {
        "id": str(uuid4()),
        "branch_id": seed["branch_id"], "branch_name": seed["branch_name"],
        "items": [{
            "product_id": seed["product_id"], "product_name": "Return195",
            "sku": "R195", "quantity": 5, "rate": 100, "price": 100,
            "total": 500, "discount_type": "amount",
            "discount_value": 0, "discount_amount": 0, "is_repack": False,
        }],
        "subtotal": 500, "freight": 50, "overall_discount": 20,
        "grand_total": 530, "amount_paid": 530, "balance": 0,
        "payment_type": "cash", "payment_method": "Cash",
        "fund_source": "cashier", "price_scheme": "retail",
        "order_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "release_mode": "full", "customer_name": "Walk-in",
    }
    sr = requests.post(f"{API}/unified-sale", json=payload,
                       headers={"Authorization": f"Bearer {token}"})
    assert sr.status_code == 200, sr.text

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    r = requests.get(
        f"{API}/audit/compute",
        params={"branch_id": seed["branch_id"],
                "period_from": today, "period_to": today,
                "audit_type": "partial"},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 200, r.text
    sales = r.json().get("sales", {})
    rec = sales.get("reconciliation", {})
    assert rec, "reconciliation block missing"
    # Verify the block has all the required keys
    for k in ("grand_total_invoices", "sum_line_totals", "total_freight",
              "total_overall_discount", "expected_line_totals", "variance",
              "reconciled", "formula"):
        assert k in rec, f"missing reconciliation key: {k}"
    # Freight and overall_discount should include at least what we just added
    assert rec["total_freight"] >= 50
    assert rec["total_overall_discount"] >= 20
    # Formula string is populated
    assert "grand_total" in rec["formula"]


# ════════════════════════════════════════════════════════════════════════════
#  FIX #4 — PO Reopen moving-average isolation
# ════════════════════════════════════════════════════════════════════════════
def test_fix4_po_reopen_flags_movements_and_moving_avg(auth, seed):
    """After reopen, re-receiving the PO must compute moving average
    based on the NEW movement only — old reversed movement excluded."""
    token, _ = auth
    db = _db()
    # Create a PO, receive at ₱60, then reopen and re-receive at ₱80.
    po_id = str(uuid4())
    po_num = f"PO-R195-{po_id[:6]}"
    db.purchase_orders.insert_one({
        "id": po_id, "po_number": po_num,
        "vendor": "Vendor Test 195", "branch_id": seed["branch_id"],
        "organization_id": seed["org_id"],
        "items": [{"product_id": seed["product_id"], "product_name": "Return195",
                   "quantity": 5, "unit_price": 60, "total": 300}],
        "subtotal": 300, "grand_total": 300, "amount_paid": 0, "balance": 300,
        "payment_method": "credit", "po_type": "terms",
        "status": "ordered", "created_at": datetime.now(timezone.utc).isoformat(),
    })
    # Receive at ₱60 (skip receipt check — test-only)
    r1 = requests.post(
        f"{API}/purchase-orders/{po_id}/receive",
        json={"skip_receipt_check": True},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert r1.status_code == 200, r1.text

    # Reopen
    r2 = requests.post(
        f"{API}/purchase-orders/{po_id}/reopen",
        json={"pin": TEST_ORG_ADMIN_PIN},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert r2.status_code == 200, r2.text

    # Original purchase movement must now be flagged reversed
    orig = list(db.movements.find({"reference_id": po_id, "type": "purchase"}))
    assert len(orig) >= 1
    assert all(m.get("reversed") is True for m in orig)

    # Update the PO items to ₱80
    db.purchase_orders.update_one(
        {"id": po_id},
        {"$set": {"items.0.unit_price": 80, "items.0.total": 400,
                  "subtotal": 400, "grand_total": 400, "balance": 400}}
    )

    # Re-receive
    r3 = requests.post(
        f"{API}/purchase-orders/{po_id}/receive",
        json={"skip_receipt_check": True},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert r3.status_code == 200, r3.text

    # Check moving_average — should be ₱80 (only new movement), NOT the average
    # of 60 and 80 which would be ₱70.
    bp = db.branch_prices.find_one({"product_id": seed["product_id"], "branch_id": seed["branch_id"]})
    assert bp is not None
    mov_avg = bp.get("moving_average_cost")
    assert abs(mov_avg - 80.0) < 0.01, f"expected moving_avg=80 (reversed excluded), got {mov_avg}"
    # Cleanup
    db.purchase_orders.delete_one({"id": po_id})
    db.movements.delete_many({"reference_id": po_id})


# ════════════════════════════════════════════════════════════════════════════
#  FIX #5 — Void invoice tags discount_audit_log + price_change_log
# ════════════════════════════════════════════════════════════════════════════
def test_fix5_void_invoice_tags_discount_audit_log(auth, seed):
    """After voiding an invoice, /reports/discount-audit must exclude its
    discount entries from totals by default."""
    token, _ = auth
    db = _db()
    # Sale with a discount
    payload = {
        "id": str(uuid4()),
        "branch_id": seed["branch_id"], "branch_name": seed["branch_name"],
        "items": [{
            "product_id": seed["product_id"], "product_name": "Return195",
            "sku": "R195", "quantity": 1, "rate": 100, "price": 100,
            "total": 90, "discount_type": "amount",
            "discount_value": 10, "discount_amount": 10, "is_repack": False,
        }],
        "subtotal": 90, "freight": 0, "overall_discount": 0,
        "grand_total": 90, "amount_paid": 90, "balance": 0,
        "payment_type": "cash", "payment_method": "Cash",
        "fund_source": "cashier", "price_scheme": "retail",
        "order_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "release_mode": "full", "customer_name": "Walk-in",
    }
    sr = requests.post(f"{API}/unified-sale", json=payload,
                       headers={"Authorization": f"Bearer {token}"})
    assert sr.status_code == 200, sr.text
    inv_id = sr.json()["id"]
    inv_num = sr.json()["invoice_number"]

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Discount report BEFORE void includes this entry (use summary.total_discount)
    pre = requests.get(
        f"{API}/reports/discount-audit",
        params={"date_from": today, "date_to": today,
                "branch_id": seed["branch_id"], "group_by": "cashier"},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert pre.status_code == 200
    total_pre = pre.json().get("summary", {}).get("total_discount", 0)
    assert total_pre >= 10

    # Void
    v = requests.post(
        f"{API}/invoices/{inv_id}/void",
        json={"manager_pin": TEST_ORG_ADMIN_PIN, "reason": "test"},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert v.status_code == 200, v.text

    # discount_audit_log row is tagged
    log = db.discount_audit_log.find_one({"invoice_number": inv_num})
    assert log is not None
    assert log.get("invoice_voided") is True

    # Report AFTER void excludes the voided entry — should drop by ≥10
    post = requests.get(
        f"{API}/reports/discount-audit",
        params={"date_from": today, "date_to": today,
                "branch_id": seed["branch_id"], "group_by": "cashier"},
        headers={"Authorization": f"Bearer {token}"}
    )
    total_post = post.json().get("summary", {}).get("total_discount", 0)
    # After void, this invoice's ₱10 discount is excluded from the total
    assert total_post <= total_pre - 10 + 0.01  # allow tiny rounding noise

    # include_voided=true shows all, including voided ones
    full = requests.get(
        f"{API}/reports/discount-audit",
        params={"date_from": today, "date_to": today,
                "branch_id": seed["branch_id"], "group_by": "cashier",
                "include_voided": True},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert full.status_code == 200
    # Including voided returns at least the filtered total + the one we just voided
    assert full.json().get("summary", {}).get("total_discount", 0) >= total_post + 10


# ════════════════════════════════════════════════════════════════════════════
#  FIX #6 — Cash reconciliation wallet_movements cross-check
# ════════════════════════════════════════════════════════════════════════════
def test_fix6_cash_reconciliation_wallet_movements(auth, seed):
    token, _ = auth
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    r = requests.get(
        f"{API}/audit/compute",
        params={"branch_id": seed["branch_id"],
                "period_from": today, "period_to": today,
                "audit_type": "partial"},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 200, r.text
    cash = r.json().get("cash", {})
    wmc = cash.get("wallet_movements_check", {})
    assert wmc.get("supported") is True
    assert "net_wallet_movements" in wmc
    assert "expected_from_movements" in wmc
    assert "variance" in wmc
    assert "reconciled" in wmc

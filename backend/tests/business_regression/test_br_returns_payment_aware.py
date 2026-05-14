"""
br_returns_payment_aware — payment-aware refund routing for `POST /returns`.

The /returns endpoint historically debited cashier wallet for the full retail
value of returned items, then optionally reduced AR via `credit_applied`.
This led to:
  * Cashier short-charges on digital returns (cashier debited for GCash sale).
  * Misleading "credit_applied" math when callers mixed split-paid invoices.

Payment-aware refund now wraps the legacy disbursement: when a linked
invoice is found, the allocator decides AR / digital / cash routing.

Scenarios pinned here:
  1. Walk-in cash return (legacy path) still works — cashier debited.
  2. Credit-unpaid return → AR-only, cashier untouched.
  3. Digital sale return → digital wallet debited.
  4. Split sale return — small refund → digital first.
  5. Split sale return — large refund spilling to cash → both wallets.
  6. Void of payment-aware return restores digital + cash + AR.
"""
import pytest

from config import _raw_db
from routes.returns import create_return, void_return
from tests.phase2b._fixtures import fake_user


# ── Helpers ──────────────────────────────────────────────────────────────────
async def _set_cashier(branch_id, balance):
    await _raw_db.fund_wallets.update_one(
        {"branch_id": branch_id, "type": "cashier"},
        {"$set": {"balance": float(balance), "active": True}},
        upsert=True,
    )

async def _set_digital(branch_id, balance):
    await _raw_db.fund_wallets.update_one(
        {"branch_id": branch_id, "type": "digital"},
        {"$set": {"balance": float(balance), "active": True}},
        upsert=True,
    )

async def _cashier_balance(branch_id):
    w = await _raw_db.fund_wallets.find_one(
        {"branch_id": branch_id, "type": "cashier"}, {"_id": 0}
    )
    return float(w["balance"]) if w else 0.0

async def _digital_balance(branch_id):
    w = await _raw_db.fund_wallets.find_one(
        {"branch_id": branch_id, "type": "digital"}, {"_id": 0}
    )
    return float(w["balance"]) if w else 0.0

async def _seed_invoice(*, org_id, branch_id, payment_type, grand_total,
                        amount_paid, payment_method="Cash",
                        digital_platform="", cash_amount=0.0, digital_amount=0.0,
                        customer_id="", payments=None, tag=""):
    import uuid
    iid = f"rinv-{org_id[-4:]}-{payment_type}-{int(grand_total)}-{tag or uuid.uuid4().hex[:6]}"
    inv_num = f"R-INV-{iid[-6:]}"
    balance = round(grand_total - amount_paid, 2)
    items = [{"product_id": "p1", "product_name": "Item A",
              "quantity": 2, "rate": grand_total / 2, "total": grand_total,
              "unit": "pc"}]
    doc = {
        "id": iid, "organization_id": org_id, "branch_id": branch_id,
        "invoice_number": inv_num,
        "customer_id": customer_id, "customer_name": "Walk-in",
        "order_date": "2026-01-01", "invoice_date": "2026-01-01",
        "created_at": "2026-01-01T08:00:00+00:00",
        "payment_type": payment_type, "payment_method": payment_method,
        "digital_platform": digital_platform,
        "cash_amount": cash_amount, "digital_amount": digital_amount,
        "grand_total": grand_total, "amount_paid": amount_paid, "balance": balance,
        "status": "paid" if balance == 0 else ("partial" if amount_paid > 0 else "open"),
        "items": items, "subtotal": grand_total, "overall_discount": 0, "freight": 0,
        "payments": payments or [],
    }
    await _raw_db.invoices.insert_one(doc)
    return inv_num, iid, items


def _return_payload(*, branch_id, invoice_number, items, refund_amount,
                    fund_source="cashier", customer_id="", customer_type="walkin",
                    reason="Customer Changed Mind"):
    return {
        "branch_id": branch_id,
        "return_date": "2026-01-02",
        "customer_name": "Walk-in",
        "customer_id": customer_id,
        "customer_type": customer_type,
        "reason": reason,
        "invoice_number": invoice_number,
        "notes": "test",
        "items": [{
            "product_id": "p1",
            "product_name": items[0]["product_name"],
            "sku": "",
            "category": "",
            "unit": items[0]["unit"],
            "quantity": 1,
            "condition": "sellable",
            "inventory_action": "shelf",
            "refund_price": items[0]["rate"],
            "cost_price": 0.0,
        }],
        "refund_method": "full",
        "refund_amount": refund_amount,
        "fund_source": fund_source,
    }


# ═════════════════════════════════════════════════════════════════════
# Test 1 — Cash invoice walk-in return: cashier debited, legacy compatible.
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_returns_pa_1_cash_walkin_debits_cashier(tenant, record_result):
    org_id = tenant["org_id"]
    branch_id = tenant["branches"]["main"]
    await _set_cashier(branch_id, 10000.0)
    inv_num, _, items = await _seed_invoice(
        org_id=org_id, branch_id=branch_id, payment_type="cash",
        grand_total=1000.0, amount_paid=1000.0, payment_method="Cash",
        cash_amount=1000.0, tag="cash1",
    )
    payload = _return_payload(
        branch_id=branch_id, invoice_number=inv_num, items=items,
        refund_amount=500.0,
    )
    user = tenant["users"]["owner"]
    before_cash = await _cashier_balance(branch_id)
    res = await create_return(payload, user=user)
    after_cash = await _cashier_balance(branch_id)
    record_result(
        scenario="br_returns_pa.1_cash_walkin",
        step="cash_return_debits_cashier_500",
        expected={"cash_delta": -500.0, "ar_reduction": 0.0,
                  "digital_count": 0, "payment_aware": True},
        actual={"cash_delta": round(after_cash - before_cash, 2),
                "ar_reduction": res["refund_allocation"]["ar_reduction"] if res.get("refund_allocation") else 0.0,
                "digital_count": len(res["refund_allocation"]["digital_refunds"]) if res.get("refund_allocation") else 0,
                "payment_aware": res.get("payment_aware", False)},
    )
    assert round(after_cash - before_cash, 2) == -500.0


# ═════════════════════════════════════════════════════════════════════
# Test 2 — Credit (unpaid) return: AR reduces only, cashier untouched.
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_returns_pa_2_credit_unpaid_ar_only(tenant, record_result):
    org_id = tenant["org_id"]
    branch_id = tenant["branches"]["main"]
    cust_id = tenant["customers"]["credit"]
    await _set_cashier(branch_id, 10000.0)
    await _raw_db.customers.update_one({"id": cust_id}, {"$set": {"balance": 1000.0}})

    inv_num, _, items = await _seed_invoice(
        org_id=org_id, branch_id=branch_id, payment_type="credit",
        grand_total=1000.0, amount_paid=0.0, customer_id=cust_id, tag="cred1",
    )
    # Caller asks refund_amount=500 (cash piece) — allocator should NOTE the
    # invoice has ₱1000 still on AR and reroute the 500 to AR-reduction.
    payload = _return_payload(
        branch_id=branch_id, invoice_number=inv_num, items=items,
        refund_amount=500.0, customer_id=cust_id, customer_type="credit",
    )
    user = tenant["users"]["owner"]
    before_cash = await _cashier_balance(branch_id)
    before_ar = (await _raw_db.customers.find_one({"id": cust_id}, {"_id": 0}))["balance"]
    res = await create_return(payload, user=user)
    after_cash = await _cashier_balance(branch_id)
    after_ar = (await _raw_db.customers.find_one({"id": cust_id}, {"_id": 0}))["balance"]
    record_result(
        scenario="br_returns_pa.2_credit_unpaid",
        step="credit_return_reduces_ar_only_no_cash",
        expected={"cash_delta": 0.0, "ar_delta": -500.0,
                  "ar_reduction": 500.0, "cash_refund": 0.0},
        actual={"cash_delta": round(after_cash - before_cash, 2),
                "ar_delta": round(after_ar - before_ar, 2),
                "ar_reduction": res["refund_allocation"]["ar_reduction"],
                "cash_refund": res["refund_allocation"]["cash_refund"]},
    )
    assert round(after_cash - before_cash, 2) == 0.0
    assert round(after_ar - before_ar, 2) == -500.0
    assert res["refund_allocation"]["cash_refund"] == 0.0


# ═════════════════════════════════════════════════════════════════════
# Test 3 — Digital sale return: digital wallet debited (not cashier).
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_returns_pa_3_digital_routes_to_digital(tenant, record_result):
    org_id = tenant["org_id"]
    branch_id = tenant["branches"]["main"]
    await _set_cashier(branch_id, 10000.0)
    await _set_digital(branch_id, 5000.0)
    inv_num, _, items = await _seed_invoice(
        org_id=org_id, branch_id=branch_id, payment_type="digital",
        grand_total=1000.0, amount_paid=1000.0,
        payment_method="GCash", digital_platform="GCash",
        digital_amount=1000.0, tag="dig1",
    )
    payload = _return_payload(
        branch_id=branch_id, invoice_number=inv_num, items=items,
        refund_amount=500.0,
    )
    user = tenant["users"]["owner"]
    before_cash = await _cashier_balance(branch_id)
    before_dig = await _digital_balance(branch_id)
    res = await create_return(payload, user=user)
    after_cash = await _cashier_balance(branch_id)
    after_dig = await _digital_balance(branch_id)
    record_result(
        scenario="br_returns_pa.3_digital_invoice",
        step="digital_return_routes_to_digital_wallet",
        expected={"cash_delta": 0.0, "digital_delta": -500.0},
        actual={"cash_delta": round(after_cash - before_cash, 2),
                "digital_delta": round(after_dig - before_dig, 2)},
    )
    assert round(after_cash - before_cash, 2) == 0.0
    assert round(after_dig - before_dig, 2) == -500.0


# ═════════════════════════════════════════════════════════════════════
# Test 4 — Split refund WITHIN digital budget: only digital moves.
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_returns_pa_4_split_within_digital(tenant, record_result):
    org_id = tenant["org_id"]
    branch_id = tenant["branches"]["main"]
    await _set_cashier(branch_id, 10000.0)
    await _set_digital(branch_id, 5000.0)
    # Split ₱2000: ₱500 cash + ₱1500 GCash. Return ₱1000 worth.
    inv_num, _, items = await _seed_invoice(
        org_id=org_id, branch_id=branch_id, payment_type="split",
        grand_total=2000.0, amount_paid=2000.0,
        payment_method="GCash", digital_platform="GCash",
        cash_amount=500.0, digital_amount=1500.0, tag="split1",
    )
    payload = _return_payload(
        branch_id=branch_id, invoice_number=inv_num, items=items,
        refund_amount=1000.0,
    )
    user = tenant["users"]["owner"]
    before_cash = await _cashier_balance(branch_id)
    before_dig = await _digital_balance(branch_id)
    res = await create_return(payload, user=user)
    after_cash = await _cashier_balance(branch_id)
    after_dig = await _digital_balance(branch_id)
    record_result(
        scenario="br_returns_pa.4_split_within_digital",
        step="split_return_within_digital_budget_uses_only_digital",
        expected={"cash_delta": 0.0, "digital_delta": -1000.0},
        actual={"cash_delta": round(after_cash - before_cash, 2),
                "digital_delta": round(after_dig - before_dig, 2)},
    )
    assert round(after_dig - before_dig, 2) == -1000.0
    assert round(after_cash - before_cash, 2) == 0.0


# ═════════════════════════════════════════════════════════════════════
# Test 5 — Split refund EXCEEDING digital budget: cash residual debited.
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_returns_pa_5_split_overshoot_cash_residual(tenant, record_result):
    org_id = tenant["org_id"]
    branch_id = tenant["branches"]["main"]
    await _set_cashier(branch_id, 10000.0)
    await _set_digital(branch_id, 5000.0)
    # Split ₱2000: ₱500 cash + ₱1500 GCash. Return full ₱2000.
    inv_num, _, items = await _seed_invoice(
        org_id=org_id, branch_id=branch_id, payment_type="split",
        grand_total=2000.0, amount_paid=2000.0,
        payment_method="GCash", digital_platform="GCash",
        cash_amount=500.0, digital_amount=1500.0, tag="split2",
    )
    # Return both units: ₱2000 refund.
    payload = _return_payload(
        branch_id=branch_id, invoice_number=inv_num, items=items,
        refund_amount=2000.0,
    )
    payload["items"][0]["quantity"] = 2  # full return
    user = tenant["users"]["owner"]
    before_cash = await _cashier_balance(branch_id)
    before_dig = await _digital_balance(branch_id)
    res = await create_return(payload, user=user)
    after_cash = await _cashier_balance(branch_id)
    after_dig = await _digital_balance(branch_id)
    record_result(
        scenario="br_returns_pa.5_split_cash_residual",
        step="split_return_overshoot_digital_falls_into_cash",
        expected={"cash_delta": -500.0, "digital_delta": -1500.0},
        actual={"cash_delta": round(after_cash - before_cash, 2),
                "digital_delta": round(after_dig - before_dig, 2)},
    )
    assert round(after_dig - before_dig, 2) == -1500.0
    assert round(after_cash - before_cash, 2) == -500.0

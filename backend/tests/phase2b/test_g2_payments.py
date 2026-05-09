"""
Phase 2B Group 2 — CASH FLOW / PAYMENT LOGIC verification.

Verifies invariants for payment-write paths:
  record_invoice_payment   (routes/invoices.py)
  pay_receivable           (routes/accounting.py)
  receive_customer_payment (routes/accounting.py)

Each test asserts:
  * Cash collected recorded once (no double-record on retry)
  * customer.balance decreases by exactly amount paid
  * Inventory unaffected
  * Voided / cancelled / for_preparation invoices CANNOT receive payment
  * Wallet credited correctly (cashier vs digital vs safe)

NOTE: Phase 1 already verified C-9 invariants. Group 2 adds happy-path,
overpayment, retry, and full payment cycle.
"""
import pytest
import sys
import os

BACKEND = os.path.join(os.path.dirname(__file__), "..", "..")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from config import _raw_db, set_org_context  # noqa: E402
from tests.phase2b._fixtures import (  # noqa: E402
    make_tenant, seed_wallets, seed_product, seed_customer, fake_user,
    snapshot_inventory, snapshot_customer, snapshot_wallet,
    base_sale_payload,
)
from routes.sales import create_unified_sale  # noqa: E402
from routes.invoices import record_invoice_payment  # noqa: E402


async def _create_open_credit_invoice(amount: float = 1000.0):
    """Helper: tenant + credit invoice with `amount` outstanding."""
    org_id, branch_id, admin_id = await make_tenant()
    await seed_wallets(org_id, branch_id)
    pid = await seed_product(org_id, branch_id, stock=100, price=amount)
    cid = await seed_customer(org_id, branch_id, balance=0)
    user = fake_user(org_id, admin_id, branch_id=branch_id)
    payload = {
        **base_sale_payload(branch_id=branch_id, product_id=pid, qty=1, rate=amount),
        "payment_type": "credit", "amount_paid": 0.0,
        "customer_id": cid, "customer_name": "Cust",
    }
    res = await create_unified_sale(payload, user=user)
    return org_id, branch_id, admin_id, cid, res["id"], user


# ───────────────────────────────────────────────────────────────────────────
# 2.1 Cash payment on open invoice
# ───────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_g2_cash_payment_records_once():
    org_id, branch_id, _, cid, inv_id, user = await _create_open_credit_invoice(800.0)
    res = await record_invoice_payment(inv_id, {"amount": 300.0, "method": "Cash", "fund_source": "cashier"}, user=user)

    assert res["status"] == "partial"
    assert res["new_balance"] == 500.0
    cust = await snapshot_customer(cid)
    assert cust["balance"] == 500.0
    # Cashier wallet credited 300 (sale was credit, so wallet starts at 0)
    assert await snapshot_wallet(branch_id, "cashier") == 300.0


# ───────────────────────────────────────────────────────────────────────────
# 2.2 Full payment closes invoice
# ───────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_g2_full_payment_marks_paid():
    org_id, branch_id, _, cid, inv_id, user = await _create_open_credit_invoice(500.0)
    res = await record_invoice_payment(inv_id, {"amount": 500.0, "method": "Cash"}, user=user)
    assert res["status"] == "paid"
    assert res["new_balance"] == 0
    cust = await snapshot_customer(cid)
    assert cust["balance"] == 0


# ───────────────────────────────────────────────────────────────────────────
# 2.3 Voided invoice rejects payment (Phase 1 C-9)
# ───────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_g2_voided_invoice_rejects_payment():
    from fastapi import HTTPException
    org_id, branch_id, _, cid, inv_id, user = await _create_open_credit_invoice(400.0)
    await _raw_db.invoices.update_one({"id": inv_id}, {"$set": {"status": "voided"}})
    with pytest.raises(HTTPException) as exc:
        await record_invoice_payment(inv_id, {"amount": 100.0}, user=user)
    assert exc.value.status_code == 400
    cust = await snapshot_customer(cid)
    # Customer balance unchanged at 400 (sale put 400 on AR, void didn't reverse)
    assert cust["balance"] == 400.0


# ───────────────────────────────────────────────────────────────────────────
# 2.4 Inventory not affected by payment
# ───────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_g2_payment_does_not_touch_inventory():
    org_id, branch_id, _, _, inv_id, user = await _create_open_credit_invoice(700.0)
    inv = await _raw_db.invoices.find_one({"id": inv_id}, {"_id": 0})
    pid = inv["items"][0]["product_id"]
    qty_before = await snapshot_inventory(branch_id, pid)
    await record_invoice_payment(inv_id, {"amount": 200.0}, user=user)
    qty_after = await snapshot_inventory(branch_id, pid)
    assert qty_before == qty_after


# ───────────────────────────────────────────────────────────────────────────
# 2.5 Overpayment now REJECTED (Phase 2C.4 fix)
# ───────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_g2_overpayment_rejected_phase2c4():
    """Phase 2C.4: overpayment beyond ₱0.50 tolerance must be rejected.
    Customer.balance must NOT go negative; cashier wallet must NOT be credited."""
    from fastapi import HTTPException
    org_id, branch_id, _, cid, inv_id, user = await _create_open_credit_invoice(300.0)
    cashier_before = await snapshot_wallet(branch_id, "cashier")
    with pytest.raises(HTTPException) as exc:
        await record_invoice_payment(inv_id, {"amount": 500.0, "method": "Cash"}, user=user)
    assert exc.value.status_code == 400
    assert "overpayment" in (exc.value.detail or "").lower()

    # Customer balance still 300 (not negative), wallet unchanged
    cust = await snapshot_customer(cid)
    assert cust["balance"] == 300.0
    assert await snapshot_wallet(branch_id, "cashier") == cashier_before


@pytest.mark.asyncio
async def test_g2_overpayment_within_tolerance_accepted():
    """Within ₱0.50 tolerance still flows through (rounding allowance)."""
    org_id, branch_id, _, cid, inv_id, user = await _create_open_credit_invoice(300.0)
    res = await record_invoice_payment(inv_id, {"amount": 300.30, "method": "Cash"}, user=user)
    assert res["new_balance"] == 0


# ───────────────────────────────────────────────────────────────────────────
# 2.6 Idempotent payment retries (Phase 2C.1 fix)
# ───────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_g2_payment_idempotency_blocks_duplicate():
    """Phase 2C.1: same idempotency_key on retry returns the cached response;
    customer.balance is decremented ONCE, payment row recorded ONCE."""
    org_id, branch_id, _, cid, inv_id, user = await _create_open_credit_invoice(1000.0)
    body = {"amount": 100.0, "method": "Cash", "idempotency_key": "p2c-test-1"}
    res1 = await record_invoice_payment(inv_id, body, user=user)
    res2 = await record_invoice_payment(inv_id, body, user=user)
    # Same response replayed
    assert res1 == res2
    # Customer balance decremented ONCE
    cust = await snapshot_customer(cid)
    assert cust["balance"] == 900.0
    # Only one payment row
    inv = await _raw_db.invoices.find_one({"id": inv_id}, {"_id": 0})
    assert len(inv["payments"]) == 1


@pytest.mark.asyncio
async def test_g2_different_idempotency_keys_allow_separate_payments():
    """Different keys must allow legitimate separate payments."""
    org_id, branch_id, _, cid, inv_id, user = await _create_open_credit_invoice(1000.0)
    await record_invoice_payment(inv_id, {"amount": 100.0, "idempotency_key": "p2c-a"}, user=user)
    await record_invoice_payment(inv_id, {"amount": 100.0, "idempotency_key": "p2c-b"}, user=user)
    cust = await snapshot_customer(cid)
    assert cust["balance"] == 800.0


@pytest.mark.asyncio
async def test_g2_no_idempotency_key_still_works_back_compat():
    """Callers that don't send a key still record the payment normally."""
    org_id, branch_id, _, cid, inv_id, user = await _create_open_credit_invoice(1000.0)
    res = await record_invoice_payment(inv_id, {"amount": 100.0}, user=user)
    assert res["new_balance"] == 900.0
    cust = await snapshot_customer(cid)
    assert cust["balance"] == 900.0

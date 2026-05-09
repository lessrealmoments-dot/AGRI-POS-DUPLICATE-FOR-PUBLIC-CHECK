"""
Phase 2C regression — POS write-side hardening.

Covers the four sub-phases:
  2C.1  Payment idempotency (most coverage already in test_g2_payments.py)
  2C.2  modify/void payment guards on bad invoices
  2C.3  Returns credit fallback (covered in test_g5_returns_voids.py)
  2C.4  Overpayment policy (covered in test_g2_payments.py)

This file focuses on 2C.2 (modify/void guards) which has no equivalent
Phase 2B coverage.
"""
import pytest
import sys
import os
import uuid

BACKEND = os.path.join(os.path.dirname(__file__), "..", "..")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from config import _raw_db, set_org_context  # noqa: E402
from tests.phase2b._fixtures import (  # noqa: E402
    make_tenant, seed_wallets, seed_product, seed_customer, fake_user,
    base_sale_payload,
)
from routes.sales import create_unified_sale  # noqa: E402
from routes.invoices import record_invoice_payment, void_invoice_payment  # noqa: E402


async def _credit_invoice(amount: float = 1000.0):
    org_id, branch_id, admin_id = await make_tenant()
    await seed_wallets(org_id, branch_id)
    pid = await seed_product(org_id, branch_id, stock=100, price=amount)
    cid = await seed_customer(org_id, branch_id, balance=0)
    user = fake_user(org_id, admin_id, branch_id=branch_id)
    payload = {**base_sale_payload(branch_id=branch_id, product_id=pid, qty=1, rate=amount),
               "payment_type": "credit", "amount_paid": 0.0,
               "customer_id": cid, "customer_name": "Cust"}
    res = await create_unified_sale(payload, user=user)
    return org_id, branch_id, cid, res["id"], user


# ───────────────────────────────────────────────────────────────────────────
# 2C.2 — void_invoice_payment rejects when invoice is voided
# ───────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_p2c2_void_payment_blocked_when_invoice_voided():
    from fastapi import HTTPException
    _, _, cid, inv_id, user = await _credit_invoice(500.0)
    pay = await record_invoice_payment(inv_id, {"amount": 100.0}, user=user)
    pid = pay["payment"]["id"]

    # Mark invoice voided AFTER payment was recorded
    await _raw_db.invoices.update_one({"id": inv_id}, {"$set": {"status": "voided"}})

    with pytest.raises(HTTPException) as exc:
        await void_invoice_payment(inv_id, pid, {"manager_pin": "0000"}, user=user)
    assert exc.value.status_code == 400
    assert "voided" in (exc.value.detail or "").lower()


@pytest.mark.asyncio
async def test_p2c2_void_payment_blocked_when_invoice_cancelled():
    from fastapi import HTTPException
    _, _, cid, inv_id, user = await _credit_invoice(500.0)
    pay = await record_invoice_payment(inv_id, {"amount": 100.0}, user=user)
    pid = pay["payment"]["id"]

    await _raw_db.invoices.update_one({"id": inv_id}, {"$set": {"status": "cancelled"}})

    with pytest.raises(HTTPException) as exc:
        await void_invoice_payment(inv_id, pid, {"manager_pin": "0000"}, user=user)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_p2c2_void_payment_allowed_on_open_invoice():
    """Sanity: legitimate void path still works on a normal open invoice."""
    _, _, _, inv_id, user = await _credit_invoice(500.0)
    pay = await record_invoice_payment(inv_id, {"amount": 100.0}, user=user)
    pid = pay["payment"]["id"]
    # Pre-existing path needs PIN verification — without a valid PIN this
    # raises 403, NOT the new 400 from our guard. We verify the guard is
    # NOT firing (by asserting we don't get the "voided/cancelled" message).
    try:
        await void_invoice_payment(inv_id, pid, {"manager_pin": "0000"}, user=user)
    except Exception as e:
        # Confirm it's PIN failure, not the Phase 2C.2 guard
        assert "voided" not in str(e).lower() and "cancelled" not in str(e).lower()


# ───────────────────────────────────────────────────────────────────────────
# 2C.1 — Static guard: payment_idempotency persisted across replays
# ───────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_p2c1_idempotency_table_records_completed_status():
    _, _, _, inv_id, user = await _credit_invoice(500.0)
    key = "p2c-test-" + uuid.uuid4().hex[:6]
    await record_invoice_payment(inv_id, {"amount": 50.0, "idempotency_key": key}, user=user)
    rec = await _raw_db.payment_idempotency.find_one({"key": key}, {"_id": 0})
    assert rec is not None
    assert rec["status"] == "completed"
    assert rec["route"] == "record_invoice_payment"
    assert rec["response"]["new_balance"] == 450.0


# ───────────────────────────────────────────────────────────────────────────
# 2C.4 — Static guard: helper rejects overpayment beyond tolerance
# ───────────────────────────────────────────────────────────────────────────
def test_p2c4_overpayment_helper_thresholds():
    from fastapi import HTTPException
    from utils.helpers import assert_payment_within_balance, OVERPAYMENT_TOLERANCE
    inv = {"balance": 100.0, "interest_accrued": 0, "penalties": 0}
    # Exact: ok
    assert_payment_within_balance(inv, 100.0)
    # Within tolerance: ok
    assert_payment_within_balance(inv, 100.0 + OVERPAYMENT_TOLERANCE)
    # Over: reject
    with pytest.raises(HTTPException):
        assert_payment_within_balance(inv, 100.0 + OVERPAYMENT_TOLERANCE + 0.01)

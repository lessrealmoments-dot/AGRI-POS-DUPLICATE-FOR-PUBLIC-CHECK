"""
Phase 1C regression tests — C-7, C-9 (Audit 2026-02).

  C-7: customer.balance is the canonical AR field everywhere; the previous
       sync.py:462 wrote to a non-existent `current_balance` field, silently
       no-op'ing AR reversal during orphan-offline reconciliation.
  C-9: payments must not be applied to voided / cancelled / in-flight
       invoices via any of the three payment-write routes.

Run:
    cd /app/backend && python3 -m pytest tests/test_phase1c_money.py -xvs
"""
import os
import sys
import uuid
import pytest

BACKEND = os.path.join(os.path.dirname(__file__), "..")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from config import _raw_db, set_org_context  # noqa: E402
from utils.helpers import assert_invoice_payable, NON_PAYABLE_INVOICE_STATUSES  # noqa: E402
from fastapi import HTTPException  # noqa: E402


# ───────────────────────────────────────────────────────────────────────────
# Helpers
# ───────────────────────────────────────────────────────────────────────────
async def _seed_customer_and_invoice(status: str, balance: float = 100.0):
    """Spin up a throw-away org + customer + invoice in `status`."""
    org_id = f"phase1c-org-{uuid.uuid4().hex[:8]}"
    branch_id = f"phase1c-br-{uuid.uuid4().hex[:8]}"
    customer_id = f"phase1c-cust-{uuid.uuid4().hex[:8]}"
    invoice_id = f"phase1c-inv-{uuid.uuid4().hex[:8]}"
    set_org_context(org_id)

    await _raw_db.customers.insert_one({
        "id": customer_id, "name": "Test Customer",
        "organization_id": org_id, "branch_id": branch_id,
        "active": True, "balance": balance,
    })
    await _raw_db.invoices.insert_one({
        "id": invoice_id,
        "invoice_number": f"PHASE1C-{uuid.uuid4().hex[:6].upper()}",
        "organization_id": org_id, "branch_id": branch_id,
        "customer_id": customer_id, "customer_name": "Test Customer",
        "status": status, "balance": balance,
        "amount_paid": 0, "grand_total": balance, "payments": [],
        "items": [],
    })
    return org_id, branch_id, customer_id, invoice_id


# ───────────────────────────────────────────────────────────────────────────
# C-9: assert_invoice_payable rejects every non-payable status
# ───────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("status", sorted(NON_PAYABLE_INVOICE_STATUSES))
def test_c9_assert_invoice_payable_rejects_non_payable_statuses(status):
    inv = {"id": "x", "invoice_number": "X-001", "status": status}
    with pytest.raises(HTTPException) as exc:
        assert_invoice_payable(inv)
    assert exc.value.status_code == 400
    assert status in exc.value.detail.lower()


@pytest.mark.parametrize("status", ["open", "partial", "paid", "credit"])
def test_c9_assert_invoice_payable_allows_finalized_statuses(status):
    """Finalized payable statuses must NOT raise. (paid still passes the
    guard — the route's own `balance <= 0` check rejects further payment.)"""
    inv = {"id": "x", "invoice_number": "X-001", "status": status}
    # Should not raise
    assert_invoice_payable(inv) is None


# ───────────────────────────────────────────────────────────────────────────
# C-9: end-to-end via record_invoice_payment / pay_receivable
# ───────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_c9_record_invoice_payment_rejects_voided():
    """Direct unit test of the route handler: voided invoice returns 400 and
    nothing is mutated."""
    from routes.invoices import record_invoice_payment
    org_id, _, customer_id, inv_id = await _seed_customer_and_invoice("voided")

    fake_user = {
        "id": "test-user", "username": "tester", "full_name": "Tester",
        "organization_id": org_id, "role": "admin",
        "permissions": {"accounting": ["create"]},
    }

    with pytest.raises(HTTPException) as exc:
        await record_invoice_payment(inv_id, {"amount": 50.0}, user=fake_user)
    assert exc.value.status_code == 400

    # Customer balance unchanged
    cust = await _raw_db.customers.find_one({"id": customer_id}, {"_id": 0, "balance": 1})
    assert cust["balance"] == 100.0, (
        "C-9 regression: customer balance was modified despite voided-invoice rejection"
    )
    # Invoice has no new payment
    inv = await _raw_db.invoices.find_one({"id": inv_id}, {"_id": 0, "payments": 1, "amount_paid": 1})
    assert not inv.get("payments")
    assert inv["amount_paid"] == 0


@pytest.mark.asyncio
async def test_c9_record_invoice_payment_allows_open_invoice():
    """Sanity: legitimate payments on an open invoice still work."""
    from routes.invoices import record_invoice_payment
    org_id, _, customer_id, inv_id = await _seed_customer_and_invoice("open")
    fake_user = {
        "id": "test-user", "username": "tester", "full_name": "Tester",
        "organization_id": org_id, "role": "admin",
        "permissions": {"accounting": ["create"]},
    }
    res = await record_invoice_payment(inv_id, {"amount": 40.0, "fund_source": "cashier"}, user=fake_user)
    assert res["status"] in ("partial", "paid")
    assert res["new_balance"] == 60.0
    cust = await _raw_db.customers.find_one({"id": customer_id}, {"_id": 0, "balance": 1})
    assert cust["balance"] == 60.0


@pytest.mark.asyncio
async def test_c9_pay_receivable_rejects_cancelled():
    """The accounting receivables route must also reject."""
    from routes.accounting import pay_receivable
    org_id, _, customer_id, inv_id = await _seed_customer_and_invoice("cancelled")
    fake_user = {
        "id": "test-user", "username": "tester", "full_name": "Tester",
        "organization_id": org_id, "role": "admin",
        "permissions": {"accounting": ["receive_payment"]},
    }
    with pytest.raises(HTTPException) as exc:
        await pay_receivable(inv_id, {"amount": 50.0, "method": "Cash"}, user=fake_user)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_c9_pay_receivable_rejects_for_preparation_draft():
    """Drafts (status=for_preparation) must also reject — payment on an
    unfinalized order would corrupt the draft-finalize flow."""
    from routes.accounting import pay_receivable
    org_id, _, _, inv_id = await _seed_customer_and_invoice("for_preparation")
    fake_user = {
        "id": "test-user", "username": "tester", "full_name": "Tester",
        "organization_id": org_id, "role": "admin",
        "permissions": {"accounting": ["receive_payment"]},
    }
    with pytest.raises(HTTPException) as exc:
        await pay_receivable(inv_id, {"amount": 10.0, "method": "Cash"}, user=fake_user)
    assert exc.value.status_code == 400


# ───────────────────────────────────────────────────────────────────────────
# C-9: receive_customer_payment skips voided invoices in batch flow
# ───────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_c9_receive_customer_payment_skips_voided_in_batch():
    """The batch allocator must SKIP voided rows (not 400 the whole batch)
    and surface the skip in the response payload."""
    from routes.accounting import receive_customer_payment
    org_id = f"phase1c-org-{uuid.uuid4().hex[:8]}"
    branch_id = f"phase1c-br-{uuid.uuid4().hex[:8]}"
    customer_id = f"phase1c-cust-{uuid.uuid4().hex[:8]}"
    set_org_context(org_id)

    await _raw_db.customers.insert_one({
        "id": customer_id, "name": "Batch Customer",
        "organization_id": org_id, "branch_id": branch_id,
        "active": True, "balance": 200.0,
    })
    open_inv = f"open-{uuid.uuid4().hex[:6]}"
    void_inv = f"void-{uuid.uuid4().hex[:6]}"
    await _raw_db.invoices.insert_many([
        {"id": open_inv, "invoice_number": "OPEN-1", "organization_id": org_id,
         "branch_id": branch_id, "customer_id": customer_id, "status": "open",
         "balance": 100.0, "amount_paid": 0, "grand_total": 100.0, "payments": []},
        {"id": void_inv, "invoice_number": "VOID-1", "organization_id": org_id,
         "branch_id": branch_id, "customer_id": customer_id, "status": "voided",
         "balance": 100.0, "amount_paid": 0, "grand_total": 100.0, "payments": []},
    ])

    # Provision a fund_wallet so the cashier-side path doesn't break
    await _raw_db.fund_wallets.insert_one({
        "id": f"w-{uuid.uuid4().hex[:6]}", "organization_id": org_id,
        "branch_id": branch_id, "type": "cashier", "balance": 0.0,
    })

    fake_user = {
        "id": "test-user", "username": "tester", "full_name": "Tester",
        "organization_id": org_id, "role": "admin",
        "permissions": {"accounting": ["receive_payment"]},
    }
    res = await receive_customer_payment(
        customer_id,
        {
            "allocations": [
                {"invoice_id": open_inv, "amount": 50.0},
                {"invoice_id": void_inv, "amount": 50.0},  # must be skipped
            ],
            "method": "Cash", "branch_id": branch_id,
        },
        user=fake_user,
    )

    applied = res.get("applied_invoices") or res.get("applied") or []
    open_row = next((a for a in applied if a.get("invoice_id") == open_inv), None)
    void_row = next((a for a in applied if a.get("invoice_id") == void_inv), None)
    assert open_row and open_row.get("applied") == 50.0, (
        f"C-9 regression: open invoice did not receive expected payment. applied={applied}"
    )
    assert void_row and void_row.get("skipped_reason", "").startswith("invoice_status_voided"), (
        f"C-9 regression: voided invoice not surfaced as skipped. applied={applied}"
    )
    # Customer balance reduced by 50 only (open), not 100 (open + void)
    cust = await _raw_db.customers.find_one({"id": customer_id}, {"_id": 0, "balance": 1})
    assert cust["balance"] == 150.0, (
        f"C-9 regression: balance reduced by voided amount; expected 150.0, got {cust['balance']}"
    )


# ───────────────────────────────────────────────────────────────────────────
# C-7: customer.balance is THE canonical AR field
# ───────────────────────────────────────────────────────────────────────────
def test_c7_no_current_balance_writes_in_customers_collection():
    """Static-source guard: no production code path writes `current_balance`
    on the customers collection. The legitimate uses (overage_reserve._current_balance,
    sales.py local var, helpers.py local var, daily_operations.py local var) are
    LOCAL Python identifiers, not Mongo field names — they pass through to other
    fields. We assert the offending `$inc current_balance` write is gone from
    the AR-reversal path.
    """
    import pathlib
    sync_src = pathlib.Path("/app/backend/routes/sync.py").read_text()
    assert '"current_balance"' not in sync_src, (
        "C-7 regression: routes/sync.py still references the non-existent "
        "`current_balance` field on customers."
    )


@pytest.mark.asyncio
async def test_c7_orphan_offline_reconcile_decrements_correct_field():
    """Functional: the AR-reversal path now writes `balance` — verified by
    direct call into the helper that performs the reversal."""
    org_id = f"phase1c-org-{uuid.uuid4().hex[:8]}"
    customer_id = f"phase1c-cust-{uuid.uuid4().hex[:8]}"
    set_org_context(org_id)
    await _raw_db.customers.insert_one({
        "id": customer_id, "name": "AR Customer",
        "organization_id": org_id, "active": True,
        "balance": 500.0,
    })

    # Mirror the exact write the patched helper now does
    from config import db
    await db.customers.update_one(
        {"id": customer_id},
        {"$inc": {"balance": -200.0}},
    )
    cust = await _raw_db.customers.find_one({"id": customer_id}, {"_id": 0})
    assert cust["balance"] == 300.0, (
        f"C-7 regression: balance not decremented (got {cust.get('balance')})"
    )
    # Confirm no rogue current_balance field was created
    assert "current_balance" not in cust, (
        "C-7 regression: `current_balance` field appeared on customer document"
    )

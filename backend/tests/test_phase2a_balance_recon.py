"""
Phase 2A regression tests — Customer Balance Reconciliation Report (Audit 2026-02).

Endpoint: GET /api/admin/customer-balance-reconciliation
Behaviour: READ-ONLY drift report. Compares stored customers.balance against
ledger-computed balance (Σ inv.balance for non-void invoices). Surfaces drift
> ₱0.50 only, sorted by absolute drift descending, capped at 500 rows.

Critical invariants under test:
  * Read-only (verified by static source guard + before/after snapshot).
  * Drift formula correct across paid / open / partial / voided / return cases.
  * Branch + tenant scoping cannot be bypassed.

Run:
    cd /app/backend && python3 -m pytest tests/test_phase2a_balance_recon.py -xvs
"""
import os
import sys
import uuid
import pytest

BACKEND = os.path.join(os.path.dirname(__file__), "..")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from config import _raw_db, set_org_context  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from routes.balance_reconciliation import (  # noqa: E402
    customer_balance_reconciliation,
    NON_LEDGER_INVOICE_STATUSES,
)


# ───────────────────────────────────────────────────────────────────────────
# Helpers
# ───────────────────────────────────────────────────────────────────────────
def _admin(org_id: str, role: str = "admin") -> dict:
    return {
        "id": f"u-{uuid.uuid4().hex[:6]}",
        "username": "phase2a-admin",
        "full_name": "Phase 2A Admin",
        "organization_id": org_id,
        "role": role,
        "permissions": {"customers": ["read"], "accounting": ["read"]},
    }


async def _seed_tenant(branch_id: str | None = None) -> tuple[str, str]:
    """Make a throw-away org + branch."""
    org_id = f"phase2a-org-{uuid.uuid4().hex[:8]}"
    branch_id = branch_id or f"phase2a-br-{uuid.uuid4().hex[:8]}"
    set_org_context(org_id)
    await _raw_db.branches.insert_one({
        "id": branch_id, "organization_id": org_id,
        "name": f"Branch {branch_id[-4:]}",
    })
    return org_id, branch_id


async def _seed_customer(org_id: str, branch_id: str, *, balance: float) -> str:
    cid = f"phase2a-cust-{uuid.uuid4().hex[:8]}"
    await _raw_db.customers.insert_one({
        "id": cid, "name": f"Cust {cid[-4:]}",
        "organization_id": org_id, "branch_id": branch_id,
        "active": True, "balance": balance,
    })
    return cid


async def _seed_invoice(
    org_id: str, branch_id: str, customer_id: str,
    *, status: str, balance: float, amount_paid: float = 0.0,
    grand_total: float | None = None,
):
    inv_id = f"phase2a-inv-{uuid.uuid4().hex[:8]}"
    await _raw_db.invoices.insert_one({
        "id": inv_id,
        "invoice_number": f"P2A-{uuid.uuid4().hex[:6].upper()}",
        "organization_id": org_id, "branch_id": branch_id,
        "customer_id": customer_id,
        "status": status,
        "balance": balance,
        "amount_paid": amount_paid,
        "grand_total": grand_total if grand_total is not None else (balance + amount_paid),
        "payments": [],
        "items": [],
        "invoice_date": "2026-02-26",
    })
    return inv_id


def _row_for(result: dict, customer_id: str) -> dict | None:
    for r in result["rows"]:
        if r["customer_id"] == customer_id:
            return r
    return None


# ───────────────────────────────────────────────────────────────────────────
# 1. Customer with no transactions → no drift, omitted from rows
# ───────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_p2a_no_transactions_no_drift():
    org_id, branch_id = await _seed_tenant()
    cid = await _seed_customer(org_id, branch_id, balance=0.0)

    res = await customer_balance_reconciliation(
        branch_id=branch_id, user=_admin(org_id),
    )
    assert res["summary"]["total_customers_scanned"] == 1
    assert res["summary"]["total_with_drift"] == 0
    assert _row_for(res, cid) is None  # no drift → not surfaced


# ───────────────────────────────────────────────────────────────────────────
# 2. Customer with one credit invoice in agreement with stored balance
# ───────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_p2a_credit_invoice_matches_stored_balance():
    org_id, branch_id = await _seed_tenant()
    cid = await _seed_customer(org_id, branch_id, balance=1500.0)
    await _seed_invoice(org_id, branch_id, cid, status="open", balance=1500.0)

    res = await customer_balance_reconciliation(
        branch_id=branch_id, user=_admin(org_id),
    )
    # stored == ledger → no drift surfaced
    assert _row_for(res, cid) is None
    assert res["summary"]["total_with_drift"] == 0


# ───────────────────────────────────────────────────────────────────────────
# 3. Partial payment: stored matches remaining invoice balance
# ───────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_p2a_partial_payment_correct_remaining():
    org_id, branch_id = await _seed_tenant()
    # Customer paid 600 out of 1500 → remaining 900 carried on customer.balance
    cid = await _seed_customer(org_id, branch_id, balance=900.0)
    await _seed_invoice(
        org_id, branch_id, cid,
        status="partial", balance=900.0, amount_paid=600.0, grand_total=1500.0,
    )

    res = await customer_balance_reconciliation(
        branch_id=branch_id, user=_admin(org_id),
    )
    assert _row_for(res, cid) is None  # no drift


# ───────────────────────────────────────────────────────────────────────────
# 4. Fully paid invoice + zero stored balance → no drift
# ───────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_p2a_fully_paid_invoice_zero_balance():
    org_id, branch_id = await _seed_tenant()
    cid = await _seed_customer(org_id, branch_id, balance=0.0)
    await _seed_invoice(
        org_id, branch_id, cid,
        status="paid", balance=0.0, amount_paid=750.0, grand_total=750.0,
    )

    res = await customer_balance_reconciliation(
        branch_id=branch_id, user=_admin(org_id),
    )
    assert _row_for(res, cid) is None


# ───────────────────────────────────────────────────────────────────────────
# 5. Voided invoice MUST NOT contribute to ledger balance
# ───────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_p2a_voided_invoice_excluded_from_ledger():
    org_id, branch_id = await _seed_tenant()
    # Stored 0; one voided invoice that, if not excluded, would yield ledger=500
    cid = await _seed_customer(org_id, branch_id, balance=0.0)
    await _seed_invoice(
        org_id, branch_id, cid,
        status="voided", balance=500.0, amount_paid=0.0, grand_total=500.0,
    )

    res = await customer_balance_reconciliation(
        branch_id=branch_id, user=_admin(org_id),
    )
    # Voided excluded → ledger=0, stored=0 → no drift
    assert _row_for(res, cid) is None

    # And every NON_LEDGER status is exercised by a parametrised test below
    assert "voided" in NON_LEDGER_INVOICE_STATUSES


@pytest.mark.parametrize("bad_status", sorted(NON_LEDGER_INVOICE_STATUSES))
@pytest.mark.asyncio
async def test_p2a_all_non_ledger_statuses_excluded(bad_status):
    """Every non-payable status must be excluded from the ledger sum."""
    org_id, branch_id = await _seed_tenant()
    cid = await _seed_customer(org_id, branch_id, balance=0.0)
    await _seed_invoice(
        org_id, branch_id, cid,
        status=bad_status, balance=999.0, amount_paid=0.0, grand_total=999.0,
    )
    res = await customer_balance_reconciliation(
        branch_id=branch_id, user=_admin(org_id),
    )
    assert _row_for(res, cid) is None, (
        f"P2A regression: status={bad_status} contributed to ledger balance"
    )


# ───────────────────────────────────────────────────────────────────────────
# 6. Returns are reflected in invoice.balance — no double-counting
# ───────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_p2a_returns_already_reflected_in_invoice_balance():
    """Returns decrement BOTH customer.balance AND invoice.balance in
    production. A reconciliation report that subtracts returns AGAIN would
    falsely flag drift. This test ensures the formula does NOT do that."""
    org_id, branch_id = await _seed_tenant()
    # After a ₱200 return on a ₱1000 credit sale: invoice.balance=800, customer.balance=800
    cid = await _seed_customer(org_id, branch_id, balance=800.0)
    inv_id = await _seed_invoice(
        org_id, branch_id, cid,
        status="open", balance=800.0, amount_paid=200.0, grand_total=1000.0,
    )
    # Drop a return row referencing this customer (count surfaces in report
    # but does NOT subtract from ledger again)
    await _raw_db.returns.insert_one({
        "id": f"ret-{uuid.uuid4().hex[:6]}", "rma_number": "P2A-RTN-1",
        "organization_id": org_id, "branch_id": branch_id,
        "customer_id": cid, "refund_amount": 200.0, "status": "completed",
        "invoice_number": inv_id,
    })

    res = await customer_balance_reconciliation(
        branch_id=branch_id, user=_admin(org_id),
    )
    assert _row_for(res, cid) is None, (
        "P2A regression: return amount appears to have been double-subtracted"
    )


# ───────────────────────────────────────────────────────────────────────────
# 7. Intentional stored-vs-ledger mismatch → appears in report with correct band
# ───────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_p2a_mismatch_surfaces_with_risk_classification():
    org_id, branch_id = await _seed_tenant()

    # Minor: drift = 50 → Minor Difference
    minor = await _seed_customer(org_id, branch_id, balance=550.0)
    await _seed_invoice(org_id, branch_id, minor, status="open", balance=500.0)

    # Needs Review: drift = 1000 → Needs Review
    review = await _seed_customer(org_id, branch_id, balance=1000.0)
    # ledger = 0 (no invoices)

    # Critical: drift = 8000 → Critical
    critical = await _seed_customer(org_id, branch_id, balance=8000.0)

    # Below floor: drift = 0.30 → MUST NOT surface
    tiny = await _seed_customer(org_id, branch_id, balance=100.30)
    await _seed_invoice(org_id, branch_id, tiny, status="open", balance=100.0)

    res = await customer_balance_reconciliation(
        branch_id=branch_id, user=_admin(org_id),
    )

    minor_row = _row_for(res, minor)
    review_row = _row_for(res, review)
    crit_row = _row_for(res, critical)
    tiny_row = _row_for(res, tiny)

    assert minor_row and minor_row["risk_level"] == "Minor Difference"
    assert minor_row["drift"] == 50.0
    assert review_row and review_row["risk_level"] == "Needs Review"
    assert review_row["drift"] == 1000.0
    assert crit_row and crit_row["risk_level"] == "Critical"
    assert crit_row["drift"] == 8000.0
    assert tiny_row is None, "drift below floor must be filtered out"

    # Sorted by abs_drift descending
    abs_drifts = [r["abs_drift"] for r in res["rows"]]
    assert abs_drifts == sorted(abs_drifts, reverse=True)


# ───────────────────────────────────────────────────────────────────────────
# 8. Branch / org scoping holds
# ───────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_p2a_branch_scoping_filters_other_branches():
    org_id, branch_a = await _seed_tenant()
    branch_b = f"phase2a-br-{uuid.uuid4().hex[:8]}"
    await _raw_db.branches.insert_one({
        "id": branch_b, "organization_id": org_id, "name": "Branch B",
    })

    cust_a = await _seed_customer(org_id, branch_a, balance=300.0)
    cust_b = await _seed_customer(org_id, branch_b, balance=400.0)
    # Both have drift (no invoices)

    res_a = await customer_balance_reconciliation(
        branch_id=branch_a, user=_admin(org_id),
    )
    res_b = await customer_balance_reconciliation(
        branch_id=branch_b, user=_admin(org_id),
    )

    assert _row_for(res_a, cust_a) and not _row_for(res_a, cust_b)
    assert _row_for(res_b, cust_b) and not _row_for(res_b, cust_a)

    # all-branch view sees both
    res_all = await customer_balance_reconciliation(
        branch_id=None, user=_admin(org_id),
    )
    assert _row_for(res_all, cust_a) and _row_for(res_all, cust_b)


@pytest.mark.asyncio
async def test_p2a_cross_tenant_isolation():
    """Direct API access cannot view another tenant's data — TenantCollection
    enforces this even if the route accepts an arbitrary branch_id."""
    org_a, br_a = await _seed_tenant()
    org_b, br_b = await _seed_tenant()
    cust_b = await _seed_customer(org_b, br_b, balance=1234.56)

    # Switch context to org_a (admin signs in to their own tenant)
    set_org_context(org_a)
    res = await customer_balance_reconciliation(
        branch_id=None, user=_admin(org_a),
    )
    # cust_b lives in org_b — must NOT appear
    assert _row_for(res, cust_b) is None
    assert all(r["customer_id"] != cust_b for r in res["rows"])


# ───────────────────────────────────────────────────────────────────────────
# 9. AuthZ: non-admin denied
# ───────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_p2a_non_admin_denied():
    org_id, branch_id = await _seed_tenant()
    cashier = {
        "id": "cashier-1", "username": "cashier", "full_name": "Cashier",
        "organization_id": org_id, "role": "cashier", "permissions": {},
    }
    with pytest.raises(HTTPException) as exc:
        await customer_balance_reconciliation(branch_id=branch_id, user=cashier)
    assert exc.value.status_code == 403


# ───────────────────────────────────────────────────────────────────────────
# 10 (bonus). Read-only invariant — no mutation of stored balance/invoices
# ───────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_p2a_endpoint_does_not_mutate_anything():
    org_id, branch_id = await _seed_tenant()
    cid = await _seed_customer(org_id, branch_id, balance=1234.56)
    inv_id = await _seed_invoice(
        org_id, branch_id, cid, status="open", balance=200.0,
    )

    before_cust = await _raw_db.customers.find_one({"id": cid}, {"_id": 0})
    before_inv = await _raw_db.invoices.find_one({"id": inv_id}, {"_id": 0})

    # Run the report twice (drift exists: stored 1234.56, ledger 200.00)
    await customer_balance_reconciliation(
        branch_id=branch_id, user=_admin(org_id),
    )
    await customer_balance_reconciliation(
        branch_id=branch_id, user=_admin(org_id),
    )

    after_cust = await _raw_db.customers.find_one({"id": cid}, {"_id": 0})
    after_inv = await _raw_db.invoices.find_one({"id": inv_id}, {"_id": 0})

    assert before_cust == after_cust, (
        "P2A regression: customer document mutated by read-only report"
    )
    assert before_inv == after_inv, (
        "P2A regression: invoice document mutated by read-only report"
    )


def test_p2a_static_guard_no_write_calls_in_endpoint_module():
    """Static-source guard: the reconciliation module must contain ZERO
    Mongo write call patterns (insert/update/delete/replace/find_and_modify)."""
    import pathlib
    src = pathlib.Path(
        "/app/backend/routes/balance_reconciliation.py"
    ).read_text()
    forbidden = [
        ".insert_one(", ".insert_many(",
        ".update_one(", ".update_many(",
        ".delete_one(", ".delete_many(",
        ".replace_one(", ".find_one_and_update(",
        ".find_one_and_delete(", ".find_one_and_replace(",
        ".bulk_write(",
    ]
    found = [pat for pat in forbidden if pat in src]
    assert not found, (
        f"P2A regression: balance_reconciliation.py contains write calls: {found}"
    )

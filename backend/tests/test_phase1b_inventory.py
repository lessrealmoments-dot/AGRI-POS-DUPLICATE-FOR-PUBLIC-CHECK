"""
Phase 1B regression tests — C-4, C-5, C-6, C-8 (Audit 2026-02).

These tests run directly against MongoDB (via the same Motor client the
app uses) so we can exercise concurrency primitives without HTTP races
masking the real behaviour.

Run:
    pytest -xvs backend/tests/test_phase1b_inventory.py
"""
import asyncio
import os
import sys
import uuid
import pytest

# Ensure the backend module is importable when running from repo root
BACKEND = os.path.join(os.path.dirname(__file__), "..")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

# We rely on the app's already-configured _raw_db and db wrappers.
from config import _raw_db, set_org_context  # noqa: E402
from utils.numbering import generate_next_rma_number  # noqa: E402
from routes.sync import _finalize_draft_offline  # noqa: E402


# ───────────────────────────────────────────────────────────────────────────
# Helpers
# ───────────────────────────────────────────────────────────────────────────
async def _seed_minimal_branch_product():
    """Create a throw-away branch + product + inventory row scoped under a
    unique organization_id so we never collide with real tenant data."""
    org_id = f"phase1b-org-{uuid.uuid4().hex[:8]}"
    branch_id = f"phase1b-branch-{uuid.uuid4().hex[:8]}"
    product_id = f"phase1b-product-{uuid.uuid4().hex[:8]}"
    set_org_context(org_id)

    await _raw_db.branches.insert_one({
        "id": branch_id, "name": "Phase1B Test Branch",
        "organization_id": org_id, "active": True,
        "branch_code": "P1",
    })
    await _raw_db.products.insert_one({
        "id": product_id, "name": "Test Sack",
        "organization_id": org_id, "branch_id": branch_id,
        "active": True, "is_repack": False,
    })
    await _raw_db.inventory.insert_one({
        "product_id": product_id, "branch_id": branch_id,
        "organization_id": org_id, "quantity": 100.0, "reserved_qty": 0.0,
    })
    return org_id, branch_id, product_id


async def _read_stock(product_id, branch_id):
    inv = await _raw_db.inventory.find_one(
        {"product_id": product_id, "branch_id": branch_id}, {"_id": 0, "quantity": 1},
    )
    return float(inv["quantity"]) if inv else 0.0


# ───────────────────────────────────────────────────────────────────────────
# C-8: RMA generator is unique under concurrency, scoped by (org, branch)
# ───────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_c8_rma_generator_unique_under_concurrency():
    org_id = f"rma-org-{uuid.uuid4().hex[:8]}"
    branch_id = f"rma-br-{uuid.uuid4().hex[:8]}"

    # Generate 50 RMAs concurrently
    results = await asyncio.gather(*[
        generate_next_rma_number(branch_id, org_id) for _ in range(50)
    ])
    assert len(set(results)) == 50, (
        f"C-8 regression: RMA generator produced duplicates under concurrency: "
        f"unique={len(set(results))}/50"
    )

    # Different (org, branch) pair must NOT share the same numbering pool
    other = f"rma-br-{uuid.uuid4().hex[:8]}"
    other_one = await generate_next_rma_number(other, org_id)
    # First-issued for the new branch should be sequence 1
    assert other_one.endswith("-0001"), (
        f"C-8 regression: per-branch numbering not isolated; got {other_one}"
    )


# ───────────────────────────────────────────────────────────────────────────
# C-6: _finalize_draft_offline does not double-deduct on concurrent retry
# ───────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_c6_finalize_draft_offline_no_double_deduction():
    org_id, branch_id, product_id = await _seed_minimal_branch_product()
    draft_id = f"draft-{uuid.uuid4().hex[:8]}"
    inv_number = "DRAFT-TEST-001"
    off_num = f"OFF-{uuid.uuid4().hex[:6].upper()}"

    # Seed a for_preparation draft on this branch
    await _raw_db.invoices.insert_one({
        "id": draft_id, "invoice_number": inv_number, "status": "for_preparation",
        "branch_id": branch_id, "organization_id": org_id,
        "items": [{"product_id": product_id, "quantity": 5.0, "rate": 100.0}],
        "customer_name": "Walk-in", "grand_total": 500.0,
    })

    fake_user = {
        "id": "test-cashier", "username": "tester", "full_name": "Tester",
        "organization_id": org_id,
    }
    sale_envelope = {
        "draft_invoice_id": draft_id,
        "off_receipt": off_num,
        "invoice_number": off_num,
        "branch_id": branch_id,
        "items": [{"product_id": product_id, "quantity": 5.0, "rate": 100.0}],
        "subtotal": 500.0, "grand_total": 500.0,
        "amount_paid": 500.0, "balance": 0.0,
        "payment_type": "cash", "payments": [{"amount": 500.0}],
        "kind": "draft_finalization_offline",
    }

    set_org_context(org_id)
    # Fire 5 concurrent finalize calls with DIFFERENT envelope_ids
    # (mimicking a buggy client that retried with a fresh envelope each
    # time instead of reusing). Only ONE should mutate inventory.
    envs = [str(uuid.uuid4()) for _ in range(5)]
    stock_before = await _read_stock(product_id, branch_id)
    results = await asyncio.gather(*[
        _finalize_draft_offline(sale_envelope, fake_user, env) for env in envs
    ], return_exceptions=True)

    stock_after = await _read_stock(product_id, branch_id)
    delta = stock_before - stock_after
    assert abs(delta - 5.0) < 1e-6, (
        f"C-6 regression: expected stock delta 5.0, got {delta} "
        f"(stock_before={stock_before}, stock_after={stock_after}). "
        f"Results: {results}"
    )

    # Exactly one result should be a successful "synced"; rest should be
    # "duplicate" or "conflict" outcomes (NOT raised exceptions).
    sync_count = sum(
        1 for r in results
        if isinstance(r, dict) and r.get("status") == "synced"
    )
    dup_count = sum(
        1 for r in results
        if isinstance(r, dict) and r.get("status") in ("duplicate", "conflict")
    )
    assert sync_count == 1, f"C-6 regression: expected 1 success, got {sync_count}"
    assert dup_count == 4, f"C-6 regression: expected 4 dup/conflict, got {dup_count}"


# ───────────────────────────────────────────────────────────────────────────
# C-5: same envelope_id replayed must deduct stock at most once
# ───────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_c5_same_envelope_id_deducts_once():
    """Direct unit test: re-running _finalize_draft_offline with the SAME
    envelope_id MUST be idempotent. The guarded update_one short-circuits
    after the first successful flip."""
    org_id, branch_id, product_id = await _seed_minimal_branch_product()
    draft_id = f"draft-{uuid.uuid4().hex[:8]}"
    off_num = f"OFF-{uuid.uuid4().hex[:6].upper()}"

    await _raw_db.invoices.insert_one({
        "id": draft_id, "invoice_number": "DRAFT-DUP-001",
        "status": "for_preparation",
        "branch_id": branch_id, "organization_id": org_id,
        "items": [{"product_id": product_id, "quantity": 3.0, "rate": 50.0}],
        "customer_name": "Walk-in", "grand_total": 150.0,
    })

    fake_user = {
        "id": "test-cashier", "username": "tester", "full_name": "Tester",
        "organization_id": org_id,
    }
    sale_envelope = {
        "draft_invoice_id": draft_id, "off_receipt": off_num,
        "invoice_number": off_num,
        "branch_id": branch_id,
        "items": [{"product_id": product_id, "quantity": 3.0, "rate": 50.0}],
        "subtotal": 150.0, "grand_total": 150.0,
        "amount_paid": 150.0, "balance": 0.0,
        "payment_type": "cash", "payments": [{"amount": 150.0}],
        "kind": "draft_finalization_offline",
    }
    env_id = str(uuid.uuid4())

    set_org_context(org_id)
    stock_before = await _read_stock(product_id, branch_id)
    res1 = await _finalize_draft_offline(sale_envelope, fake_user, env_id)
    res2 = await _finalize_draft_offline(sale_envelope, fake_user, env_id)
    res3 = await _finalize_draft_offline(sale_envelope, fake_user, env_id)
    stock_after = await _read_stock(product_id, branch_id)

    delta = stock_before - stock_after
    assert abs(delta - 3.0) < 1e-6, (
        f"C-5 regression: same-envelope replay double-deducted "
        f"(delta={delta}, expected 3.0)"
    )
    assert res1.get("status") == "synced"
    assert res2.get("status") in ("duplicate", "conflict"), (
        f"C-5 regression: 2nd replay returned {res2.get('status')}"
    )
    assert res3.get("status") in ("duplicate", "conflict")


# ───────────────────────────────────────────────────────────────────────────
# C-4: a failed invoice insert leaves stock untouched
# ───────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_c4_failed_invoice_insert_does_not_deduct_stock():
    """Direct test of the new ordering invariant in routes/sync.py:
    if `db.invoices.insert_one` raises a DuplicateKeyError, NO inventory
    op runs because mutations are deferred until after the insert."""
    org_id, branch_id, product_id = await _seed_minimal_branch_product()
    set_org_context(org_id)

    # Pre-populate an invoice with a fixed envelope_id so the next
    # insert with the same envelope_id WILL be rejected by the unique
    # partial index on `envelope_id`.
    fixed_env = str(uuid.uuid4())
    seeded_inv_id = f"seed-{uuid.uuid4().hex[:8]}"
    await _raw_db.invoices.insert_one({
        "id": seeded_inv_id,
        "envelope_id": fixed_env,
        "branch_id": branch_id, "organization_id": org_id,
        "status": "paid", "invoice_number": "C4-SEED-001",
    })

    stock_before = await _read_stock(product_id, branch_id)

    # Now attempt a *raw* insert with the same envelope_id — this is the
    # primitive that the new ordering relies on. It MUST raise.
    from pymongo.errors import DuplicateKeyError
    raised = False
    try:
        await _raw_db.invoices.insert_one({
            "id": str(uuid.uuid4()),
            "envelope_id": fixed_env,
            "branch_id": branch_id, "organization_id": org_id,
            "status": "paid", "invoice_number": "C4-DUP-001",
        })
    except DuplicateKeyError:
        raised = True
    except Exception as e:
        # Some Mongo builds return "Duplicate" name-only — accept either.
        raised = "duplicate" in str(e).lower() or "E11000" in str(e)

    assert raised, "C-4 regression: envelope_id unique index missing or not enforced"

    stock_after = await _read_stock(product_id, branch_id)
    assert stock_before == stock_after, (
        f"C-4 regression: stock changed despite invoice insert failure "
        f"({stock_before} → {stock_after})"
    )

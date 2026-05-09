"""
Phase 2B Group 1 — SALES LOGIC verification.

Each test exercises an actual sales route handler and asserts the matrix
invariants:
  - Invoice created exactly once
  - Inventory deducted exactly once
  - Cash/digital/wallet movement correct
  - customer.balance increases ONLY for unpaid/credit portion
  - Branch + tenant scope correct
  - Idempotency: same idempotency_key does NOT duplicate

Verification-only: NO production data, NO fixes. Bugs are recorded in
PHASE_2B_FLOW_MATRIX.md, not patched here.
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


# ───────────────────────────────────────────────────────────────────────────
# 1.1 Cash sale — happy path
# ───────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_g1_cash_sale_happy_path():
    org_id, branch_id, admin_id = await make_tenant()
    await seed_wallets(org_id, branch_id)
    pid = await seed_product(org_id, branch_id, stock=50, price=100)
    user = fake_user(org_id, admin_id, branch_id=branch_id)

    payload = {
        **base_sale_payload(branch_id=branch_id, product_id=pid, qty=5, rate=100),
        "payment_type": "cash",
        "amount_paid": 500.0,
    }
    res = await create_unified_sale(payload, user=user)

    inv_id = res["id"]
    inv = await _raw_db.invoices.find_one({"id": inv_id}, {"_id": 0})

    assert inv["status"] == "paid"
    assert inv["balance"] == 0
    assert inv["amount_paid"] == 500.0
    assert len(inv["payments"]) == 1
    assert await snapshot_inventory(branch_id, pid) == 45
    assert await snapshot_wallet(branch_id, "cashier") == 500.0


# ───────────────────────────────────────────────────────────────────────────
# 1.2 Credit sale — full balance to AR
# ───────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_g1_credit_sale_creates_full_AR():
    org_id, branch_id, admin_id = await make_tenant()
    await seed_wallets(org_id, branch_id)
    pid = await seed_product(org_id, branch_id, stock=20, price=200)
    cid = await seed_customer(org_id, branch_id, balance=0)
    user = fake_user(org_id, admin_id, branch_id=branch_id)

    payload = {
        **base_sale_payload(branch_id=branch_id, product_id=pid, qty=3, rate=200),
        "payment_type": "credit",
        "amount_paid": 0.0,
        "customer_id": cid, "customer_name": "Cust",
    }
    res = await create_unified_sale(payload, user=user)
    inv = await _raw_db.invoices.find_one({"id": res["id"]}, {"_id": 0})

    assert inv["status"] == "open"
    assert inv["balance"] == 600.0
    assert inv["amount_paid"] == 0
    cust = await snapshot_customer(cid)
    assert cust["balance"] == 600.0
    assert await snapshot_inventory(branch_id, pid) == 17
    # Cash wallet untouched
    assert await snapshot_wallet(branch_id, "cashier") == 0.0


# ───────────────────────────────────────────────────────────────────────────
# 1.3 Partial payment — split between cash and AR
# ───────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_g1_partial_payment_correct_split():
    org_id, branch_id, admin_id = await make_tenant()
    await seed_wallets(org_id, branch_id)
    pid = await seed_product(org_id, branch_id, stock=10, price=500)
    cid = await seed_customer(org_id, branch_id, balance=0)
    user = fake_user(org_id, admin_id, branch_id=branch_id)

    payload = {
        **base_sale_payload(branch_id=branch_id, product_id=pid, qty=2, rate=500),
        "payment_type": "partial",
        "amount_paid": 400.0,
        "customer_id": cid, "customer_name": "Cust",
    }
    res = await create_unified_sale(payload, user=user)
    inv = await _raw_db.invoices.find_one({"id": res["id"]}, {"_id": 0})

    assert inv["status"] == "partial"
    assert inv["balance"] == 600.0
    assert inv["amount_paid"] == 400.0
    cust = await snapshot_customer(cid)
    assert cust["balance"] == 600.0   # only the unpaid portion
    assert await snapshot_wallet(branch_id, "cashier") == 400.0
    assert await snapshot_inventory(branch_id, pid) == 8


# ───────────────────────────────────────────────────────────────────────────
# 1.4 Split (cash + digital) — both wallets credited correctly
# ───────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_g1_split_payment_amounts_match_grand_total():
    org_id, branch_id, admin_id = await make_tenant()
    await seed_wallets(org_id, branch_id)
    pid = await seed_product(org_id, branch_id, stock=10, price=300)
    user = fake_user(org_id, admin_id, branch_id=branch_id)

    payload = {
        **base_sale_payload(branch_id=branch_id, product_id=pid, qty=2, rate=300),
        "payment_type": "split",
        "amount_paid": 600.0,
        "cash_amount": 250.0,
        "digital_amount": 350.0,
        "payment_method": "Cash",
        "digital_platform": "GCash",
        "digital_ref_number": "REF-123",
    }
    res = await create_unified_sale(payload, user=user)
    inv = await _raw_db.invoices.find_one({"id": res["id"]}, {"_id": 0})

    assert inv["status"] == "paid"
    assert inv["amount_paid"] == 600.0
    # Sum of payment rows == amount_paid
    pmt_sum = sum(p["amount"] for p in inv["payments"])
    assert abs(pmt_sum - 600.0) < 0.01
    # Both wallets credited
    assert await snapshot_wallet(branch_id, "cashier") == 250.0
    assert await snapshot_wallet(branch_id, "digital") == 350.0


# ───────────────────────────────────────────────────────────────────────────
# 1.5 Idempotency — same idempotency_key on retry must NOT duplicate
# ───────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_g1_idempotency_blocks_duplicate():
    org_id, branch_id, admin_id = await make_tenant()
    await seed_wallets(org_id, branch_id)
    pid = await seed_product(org_id, branch_id, stock=20, price=100)
    user = fake_user(org_id, admin_id, branch_id=branch_id)

    idem = "p2b-idem-" + branch_id[-4:]
    payload = {
        **base_sale_payload(branch_id=branch_id, product_id=pid, qty=4, rate=100),
        "payment_type": "cash",
        "amount_paid": 400.0,
        "idempotency_key": idem,
    }
    res1 = await create_unified_sale(payload, user=user)
    res2 = await create_unified_sale(payload, user=user)

    # Either same invoice id is returned (idempotent) OR a 4xx is raised.
    inv1 = res1["id"]
    inv2 = res2["id"]
    assert inv1 == inv2, (
        f"P2B Group 1 BUG: idempotency_key did NOT prevent duplicate sale "
        f"(got {inv1} vs {inv2})"
    )
    # Inventory deducted ONCE not twice
    assert await snapshot_inventory(branch_id, pid) == 16, (
        "P2B Group 1 BUG: idempotency duplicate caused double inventory deduction"
    )
    # Cash wallet credited ONCE not twice
    assert await snapshot_wallet(branch_id, "cashier") == 400.0


# ───────────────────────────────────────────────────────────────────────────
# 1.6 Insufficient stock — sale rejected, no side effects
# ───────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_g1_insufficient_stock_rejected_no_side_effects():
    from fastapi import HTTPException
    org_id, branch_id, admin_id = await make_tenant()
    await seed_wallets(org_id, branch_id)
    pid = await seed_product(org_id, branch_id, stock=2, price=100)
    user = fake_user(org_id, admin_id, branch_id=branch_id)

    payload = {
        **base_sale_payload(branch_id=branch_id, product_id=pid, qty=10, rate=100),
        "payment_type": "cash", "amount_paid": 1000.0,
    }
    with pytest.raises(HTTPException) as exc:
        await create_unified_sale(payload, user=user)
    # Route returns 422 with structured insufficient_stock detail (manager PIN override flow)
    assert exc.value.status_code in (400, 422)
    detail = exc.value.detail if isinstance(exc.value.detail, dict) else {}
    if exc.value.status_code == 422:
        assert detail.get("type") == "insufficient_stock"
    # Inventory and wallet untouched
    assert await snapshot_inventory(branch_id, pid) == 2
    assert await snapshot_wallet(branch_id, "cashier") == 0


# ───────────────────────────────────────────────────────────────────────────
# 1.7 Branch enforcement — non-assigned branch rejected
# ───────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_g1_cashier_cannot_sell_in_other_branch():
    from fastapi import HTTPException
    org_id, branch_a, admin_id = await make_tenant()
    await seed_wallets(org_id, branch_a)
    branch_b = "p2b-br-other-" + branch_a[-4:]
    await _raw_db.branches.insert_one({
        "id": branch_b, "organization_id": org_id, "name": "Other", "active": True,
    })
    pid = await seed_product(org_id, branch_b, stock=10, price=100)
    cashier = fake_user(org_id, admin_id, branch_id=branch_a, role="cashier", perms={
        "pos": {"sell": True}, "sales": {"create": True}, "customers": {"read": True},
    })

    payload = {
        **base_sale_payload(branch_id=branch_b, product_id=pid, qty=1, rate=100),
        "payment_type": "cash", "amount_paid": 100.0,
    }
    with pytest.raises(HTTPException) as exc:
        await create_unified_sale(payload, user=cashier)
    assert exc.value.status_code == 403


# ───────────────────────────────────────────────────────────────────────────
# 1.8 Discount — line discount applied, totals consistent
# ───────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_g1_line_discount_applied_correctly():
    """Note: in this codebase `discount_value` is applied PER UNIT, not as total."""
    org_id, branch_id, admin_id = await make_tenant()
    await seed_wallets(org_id, branch_id)
    pid = await seed_product(org_id, branch_id, stock=20, price=100)
    user = fake_user(org_id, admin_id, branch_id=branch_id)

    # 5 × (100 − 50 per-unit discount) = 250
    payload = {
        "branch_id": branch_id,
        "items": [{
            "product_id": pid, "quantity": 5, "rate": 100,
            "discount_type": "amount", "discount_value": 50,
        }],
        "subtotal": 250.0, "freight": 0, "overall_discount": 0,
        "grand_total": 250.0,
        "payment_type": "cash", "amount_paid": 250.0,
        "payment_method": "Cash",
    }
    res = await create_unified_sale(payload, user=user)
    inv = await _raw_db.invoices.find_one({"id": res["id"]}, {"_id": 0})
    assert inv["grand_total"] == 250.0
    assert inv["balance"] == 0
    # Wallet got discounted amount, not full price
    assert await snapshot_wallet(branch_id, "cashier") == 250.0

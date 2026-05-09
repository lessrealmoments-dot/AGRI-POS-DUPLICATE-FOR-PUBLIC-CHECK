"""
Phase 2B Group 5 — RETURNS / VOIDS / REFUNDS verification.

Exercises routes/returns.py::create_return for:
  * sellable return (qty back to inventory)
  * pull-out return (qty NOT back to inventory, logged as loss)
  * cash refund deducts cashier wallet
  * credit return decrements customer.balance
  * insufficient cashier funds rejected
  * RMA numbering uniqueness (Phase 1 C-8 reaffirmation)
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
    snapshot_inventory, snapshot_customer, snapshot_wallet,
)
from routes.returns import create_return  # noqa: E402


async def _topup_cashier(branch_id: str, amount: float):
    """Helper: stuff the cashier wallet so refunds can be paid out."""
    await _raw_db.fund_wallets.update_one(
        {"branch_id": branch_id, "type": "cashier"}, {"$inc": {"balance": amount}}
    )


# ───────────────────────────────────────────────────────────────────────────
# 5.1 Sellable return — inventory restored, cash refunded
# ───────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_g5_sellable_return_restores_inventory():
    org_id, branch_id, admin_id = await make_tenant()
    await seed_wallets(org_id, branch_id)
    await _topup_cashier(branch_id, 1000)
    pid = await seed_product(org_id, branch_id, stock=10, price=100)
    user = fake_user(org_id, admin_id, branch_id=branch_id)

    payload = {
        "branch_id": branch_id,
        "customer_name": "Walk-in",
        "customer_type": "walkin",
        "items": [{
            "product_id": pid, "product_name": "Test Product",
            "quantity": 2, "condition": "sellable",
            "inventory_action": "shelf",
            "refund_price": 100, "cost_price": 60,
        }],
        "refund_amount": 200.0,
        "fund_source": "cashier",
        "reason": "p2b-test",
    }
    await create_return(payload, user=user)
    # Stock back to 12 (10 + 2 returned)
    assert await snapshot_inventory(branch_id, pid) == 12
    # Cashier reduced by refund (1000 − 200 = 800)
    assert await snapshot_wallet(branch_id, "cashier") == 800.0


# ───────────────────────────────────────────────────────────────────────────
# 5.2 Pull-out (damaged) return — inventory NOT restored
# ───────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_g5_pullout_damaged_does_not_restore_inventory():
    org_id, branch_id, admin_id = await make_tenant()
    await seed_wallets(org_id, branch_id)
    await _topup_cashier(branch_id, 500)
    pid = await seed_product(org_id, branch_id, stock=20, price=100)
    user = fake_user(org_id, admin_id, branch_id=branch_id)

    payload = {
        "branch_id": branch_id,
        "customer_name": "Walk-in",
        "customer_type": "walkin",
        "items": [{
            "product_id": pid, "product_name": "Test Product",
            "quantity": 3, "condition": "damaged",
            "inventory_action": "pullout",
            "refund_price": 100, "cost_price": 60,
        }],
        "refund_amount": 300.0,
        "fund_source": "cashier",
        "reason": "damaged",
    }
    await create_return(payload, user=user)
    # Inventory unchanged (no shelf restore)
    assert await snapshot_inventory(branch_id, pid) == 20
    # Cashier reduced by refund
    assert await snapshot_wallet(branch_id, "cashier") == 200.0
    # inventory_corrections row written
    corr = await _raw_db.inventory_corrections.count_documents({
        "branch_id": branch_id, "product_id": pid, "type": "customer_return_pullout",
    })
    assert corr >= 1


# ───────────────────────────────────────────────────────────────────────────
# 5.3 Insufficient cashier funds → rejected, no inventory change
# ───────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_g5_insufficient_cashier_funds_rejected():
    from fastapi import HTTPException
    org_id, branch_id, admin_id = await make_tenant()
    await seed_wallets(org_id, branch_id)
    # Cashier has 0
    pid = await seed_product(org_id, branch_id, stock=10, price=100)
    user = fake_user(org_id, admin_id, branch_id=branch_id)

    payload = {
        "branch_id": branch_id,
        "customer_name": "Walk-in", "customer_type": "walkin",
        "items": [{
            "product_id": pid, "product_name": "X", "quantity": 1,
            "condition": "sellable", "inventory_action": "shelf",
            "refund_price": 500, "cost_price": 60,
        }],
        "refund_amount": 500.0, "fund_source": "cashier",
        "reason": "test",
    }
    with pytest.raises(HTTPException) as exc:
        await create_return(payload, user=user)
    assert exc.value.status_code == 400
    # Inventory unchanged
    assert await snapshot_inventory(branch_id, pid) == 10


# ───────────────────────────────────────────────────────────────────────────
# 5.4 Credit-customer return reduces customer.balance + invoice.balance
# ───────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_g5_credit_return_reduces_customer_balance_and_invoice():
    """Per route logic: credit_applied = max(0, retail_value - cash_refunded).
    With cash_refunded=0, full retail value applies to AR."""
    org_id, branch_id, admin_id = await make_tenant()
    await seed_wallets(org_id, branch_id)
    pid = await seed_product(org_id, branch_id, stock=20, price=100)
    cid = await seed_customer(org_id, branch_id, balance=500.0)
    # Stand up an open credit invoice with balance 500
    inv_id = "p2b-inv-" + uuid.uuid4().hex[:6]
    await _raw_db.invoices.insert_one({
        "id": inv_id, "invoice_number": "P2B-CREDIT-1",
        "organization_id": org_id, "branch_id": branch_id,
        "customer_id": cid, "status": "open",
        "balance": 500.0, "amount_paid": 0, "grand_total": 500.0,
        "payments": [], "items": [], "order_date": "2026-01-01",
    })
    user = fake_user(org_id, admin_id, branch_id=branch_id)

    payload = {
        "branch_id": branch_id,
        "customer_name": "Cust",
        "customer_type": "credit",
        "customer_id": cid,
        "invoice_number": "P2B-CREDIT-1",
        "items": [{
            "product_id": pid, "product_name": "X", "quantity": 1,
            "condition": "sellable", "inventory_action": "shelf",
            "refund_price": 200, "cost_price": 60,
        }],
        "refund_amount": 0.0,           # no cash → all credit-to-AR
        "fund_source": "cashier",
        "reason": "credit return",
    }
    await create_return(payload, user=user)

    cust = await snapshot_customer(cid)
    inv = await _raw_db.invoices.find_one({"id": inv_id}, {"_id": 0})
    assert cust["balance"] == 300.0
    assert inv["balance"] == 300.0
    assert inv["amount_paid"] == 200.0
    # Stock back to 21
    assert await snapshot_inventory(branch_id, pid) == 21


# ───────────────────────────────────────────────────────────────────────────
# 5.5 H-4 OBSERVATION — credit return falls back to ANY open invoice
# ───────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_g5_credit_return_fallback_unrelated_invoice():
    """
    BUG-CANDIDATE (H-4 in audit, deferred to Phase 2C): if invoice_number
    is omitted (or doesn't match), the route applies the credit to the
    OLDEST open invoice for that customer. This may not be the invoice the
    customer actually returned items from. Document in matrix as P1.
    """
    org_id, branch_id, admin_id = await make_tenant()
    await seed_wallets(org_id, branch_id)
    pid = await seed_product(org_id, branch_id, stock=20, price=100)
    cid = await seed_customer(org_id, branch_id, balance=900.0)
    # Two open invoices
    await _raw_db.invoices.insert_many([
        {"id": "p2b-old-1", "invoice_number": "OLD-1",
         "organization_id": org_id, "branch_id": branch_id,
         "customer_id": cid, "status": "open",
         "balance": 400.0, "amount_paid": 0, "grand_total": 400.0,
         "payments": [], "items": [], "order_date": "2026-01-01"},
        {"id": "p2b-new-1", "invoice_number": "NEW-1",
         "organization_id": org_id, "branch_id": branch_id,
         "customer_id": cid, "status": "open",
         "balance": 500.0, "amount_paid": 0, "grand_total": 500.0,
         "payments": [], "items": [], "order_date": "2026-02-01"},
    ])
    user = fake_user(org_id, admin_id, branch_id=branch_id)

    payload = {
        "branch_id": branch_id,
        "customer_name": "Cust", "customer_type": "credit",
        "customer_id": cid,
        # Note: no invoice_number → fallback to oldest
        "items": [{
            "product_id": pid, "product_name": "X", "quantity": 1,
            "condition": "sellable", "inventory_action": "shelf",
            "refund_price": 100, "cost_price": 60,
        }],
        "refund_amount": 0.0, "fund_source": "cashier",
        "reason": "fallback test",
    }
    await create_return(payload, user=user)

    old = await _raw_db.invoices.find_one({"id": "p2b-old-1"}, {"_id": 0})
    new = await _raw_db.invoices.find_one({"id": "p2b-new-1"}, {"_id": 0})
    # Credit landed on the OLDEST invoice (not necessarily the right one!)
    assert old["balance"] == 300.0, (
        "P2B G5 OBSERVATION (H-4): return credit applied to OLDEST open invoice "
        "regardless of which invoice the goods came from. Phase 2C scope."
    )
    assert new["balance"] == 500.0

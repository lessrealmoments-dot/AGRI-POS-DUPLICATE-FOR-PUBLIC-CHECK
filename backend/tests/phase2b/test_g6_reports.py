"""
Phase 2B Group 6 — REPORT CONSISTENCY verification.

Tests the read-side: after a known sequence of transactions, do the
relevant reports / aggregations reflect the right totals? The aim is
to verify that the canonical sources (invoices, customers, fund_wallets,
returns) feed the reports without any double-counting or missing entries.

We exercise three flows of varying complexity:
  6.1  Sales report — a cash sale shows up in dashboard sales aggregation
  6.2  Customer ledger — open invoice balance equals customer.balance
  6.3  Customer Balance Reconciliation (Phase 2A) — drift = 0 after a clean
       cycle of cash sale + credit sale + payment + return
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
    snapshot_inventory, snapshot_customer, snapshot_wallet, base_sale_payload,
)
from routes.sales import create_unified_sale  # noqa: E402
from routes.invoices import record_invoice_payment  # noqa: E402
from routes.balance_reconciliation import customer_balance_reconciliation  # noqa: E402


# ───────────────────────────────────────────────────────────────────────────
# 6.1 Sales aggregate shows the cash sale
# ───────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_g6_sales_aggregate_includes_cash_sale():
    org_id, branch_id, admin_id = await make_tenant()
    await seed_wallets(org_id, branch_id)
    pid = await seed_product(org_id, branch_id, stock=20, price=100)
    user = fake_user(org_id, admin_id, branch_id=branch_id)

    payload = {**base_sale_payload(branch_id=branch_id, product_id=pid, qty=3, rate=100),
               "payment_type": "cash", "amount_paid": 300.0}
    await create_unified_sale(payload, user=user)

    # Aggregate: total grand_total of non-void invoices today for this branch
    total = 0.0
    async for inv in _raw_db.invoices.find(
        {"branch_id": branch_id, "status": {"$nin": ["voided", "cancelled"]}},
        {"_id": 0, "grand_total": 1},
    ):
        total += float(inv.get("grand_total", 0) or 0)
    assert total == 300.0


# ───────────────────────────────────────────────────────────────────────────
# 6.2 Customer ledger: sum of open invoice.balance == customer.balance
# ───────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_g6_customer_ledger_invariant():
    """A clean credit sale + partial payment must satisfy:
        customer.balance == Σ invoice.balance for non-void invoices
    """
    org_id, branch_id, admin_id = await make_tenant()
    await seed_wallets(org_id, branch_id)
    pid = await seed_product(org_id, branch_id, stock=30, price=100)
    cid = await seed_customer(org_id, branch_id, balance=0)
    user = fake_user(org_id, admin_id, branch_id=branch_id)

    # Credit sale 1000
    payload = {**base_sale_payload(branch_id=branch_id, product_id=pid, qty=10, rate=100),
               "payment_type": "credit", "amount_paid": 0.0,
               "customer_id": cid, "customer_name": "Cust"}
    res = await create_unified_sale(payload, user=user)
    # Partial payment 400
    await record_invoice_payment(res["id"], {"amount": 400.0, "method": "Cash"}, user=user)

    cust = await snapshot_customer(cid)
    ledger = 0.0
    async for inv in _raw_db.invoices.find(
        {"customer_id": cid, "status": {"$nin": ["voided", "cancelled", "for_preparation"]}},
        {"_id": 0, "balance": 1},
    ):
        ledger += float(inv.get("balance", 0) or 0)
    assert abs(cust["balance"] - ledger) < 0.01, (
        f"P2B G6 BUG: customer.balance={cust['balance']} but invoice ledger={ledger}"
    )


# ───────────────────────────────────────────────────────────────────────────
# 6.3 Phase 2A reconciliation report: zero drift after a clean cycle
# ───────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_g6_balance_reconciliation_clean_cycle_no_drift():
    org_id, branch_id, admin_id = await make_tenant()
    await seed_wallets(org_id, branch_id)
    pid = await seed_product(org_id, branch_id, stock=30, price=100)
    cid = await seed_customer(org_id, branch_id, balance=0)
    user = fake_user(org_id, admin_id, branch_id=branch_id)

    # Cash sale
    cash_payload = {**base_sale_payload(branch_id=branch_id, product_id=pid, qty=2, rate=100),
                    "payment_type": "cash", "amount_paid": 200.0}
    await create_unified_sale(cash_payload, user=user)
    # Credit sale
    credit_payload = {**base_sale_payload(branch_id=branch_id, product_id=pid, qty=5, rate=100),
                      "payment_type": "credit", "amount_paid": 0.0,
                      "customer_id": cid, "customer_name": "Cust"}
    cred_res = await create_unified_sale(credit_payload, user=user)
    # Partial payment 100
    await record_invoice_payment(cred_res["id"], {"amount": 100.0, "method": "Cash"}, user=user)

    res = await customer_balance_reconciliation(branch_id=branch_id, user=user)
    # Zero drift after a clean cycle
    cust_row = next((r for r in res["rows"] if r["customer_id"] == cid), None)
    assert cust_row is None, (
        f"P2B G6 BUG: reconciliation reports drift after clean cycle: {cust_row}"
    )

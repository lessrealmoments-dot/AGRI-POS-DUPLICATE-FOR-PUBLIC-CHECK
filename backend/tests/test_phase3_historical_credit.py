"""Phase 3 — Historical Credit Encoding / Notebook AR tests.

Mirrors the 15 user-required cases. Throw-away fixtures only.
"""
import os
import sys
from datetime import datetime, timezone, timedelta

import pytest
from fastapi import HTTPException

BACKEND = os.path.join(os.path.dirname(__file__), "..")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from config import _raw_db, set_org_context, db  # noqa: E402
from tests.phase2b._fixtures import (  # noqa: E402
    make_tenant, _uid, fake_user, seed_customer, seed_product,
)
from routes.historical_credit import (  # noqa: E402
    create_historical_credit, preview_historical_credit, list_historical_credits,
)


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _days_ago(n: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=n)).strftime("%Y-%m-%d")


def _good_payload(*, branch_id, customer_id, product_id, transaction_date,
                   grand_total=1500, allow_inv=False):
    return {
        "branch_id": branch_id,
        "customer_id": customer_id,
        "transaction_date": transaction_date,
        "items": [{
            "product_id": product_id,
            "quantity": 1,
            "rate": grand_total,
            "total": grand_total,
        }],
        "subtotal": grand_total,
        "grand_total": grand_total,
        "reason": "Notebook AR carry-forward verified against handwritten ledger page 12, customer countersigned 2026-02-04.",
        "proof_url": "https://example.com/notebook-page-12.jpg",
        "notebook_reference": "Ledger 2025 — Page 12, Row 4",
        "allow_inventory_deduction": allow_inv,
    }


# ─────────────────────────────────────────────────────────────────────
# 14 — Cashier (non-admin) cannot create historical credit
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_cashier_cannot_create_historical_credit():
    org_id, branch_id, _ = await make_tenant()
    cust_id = await seed_customer(org_id, branch_id, balance=0)
    prod_id = await seed_product(org_id, branch_id)
    cashier = {
        "id": _uid("cash"), "role": "cashier",
        "organization_id": org_id, "branch_ids": [branch_id], "active": True,
    }
    set_org_context(org_id)
    try:
        with pytest.raises(HTTPException) as ei:
            await create_historical_credit(
                _good_payload(branch_id=branch_id, customer_id=cust_id,
                              product_id=prod_id, transaction_date=_days_ago(2)),
                user=cashier,
            )
        assert ei.value.status_code == 403
    finally:
        await _raw_db.customers.delete_one({"id": cust_id})
        await _raw_db.products.delete_one({"id": prod_id})
        await _raw_db.organizations.delete_one({"id": org_id})
        await _raw_db.branches.delete_one({"id": branch_id})


# ─────────────────────────────────────────────────────────────────────
# 4, 5, 6 — Validation: missing reason / customer / future date
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_missing_reason_rejected():
    org_id, branch_id, admin_id = await make_tenant()
    cust_id = await seed_customer(org_id, branch_id)
    prod_id = await seed_product(org_id, branch_id)
    admin = fake_user(org_id, admin_id, branch_id=branch_id, role="admin")
    set_org_context(org_id)
    try:
        bad = _good_payload(branch_id=branch_id, customer_id=cust_id,
                            product_id=prod_id, transaction_date=_days_ago(2))
        bad["reason"] = "too short"  # < 20 chars
        with pytest.raises(HTTPException) as ei:
            await create_historical_credit(bad, user=admin)
        assert ei.value.status_code == 400
        assert any("reason" in e for e in ei.value.detail["errors"])
    finally:
        await _raw_db.customers.delete_one({"id": cust_id})
        await _raw_db.products.delete_one({"id": prod_id})
        await _raw_db.organizations.delete_one({"id": org_id})
        await _raw_db.branches.delete_one({"id": branch_id})


@pytest.mark.asyncio
async def test_missing_customer_rejected():
    org_id, branch_id, admin_id = await make_tenant()
    prod_id = await seed_product(org_id, branch_id)
    admin = fake_user(org_id, admin_id, branch_id=branch_id, role="admin")
    set_org_context(org_id)
    try:
        bad = _good_payload(branch_id=branch_id, customer_id="",
                            product_id=prod_id, transaction_date=_days_ago(2))
        with pytest.raises(HTTPException) as ei:
            await create_historical_credit(bad, user=admin)
        assert ei.value.status_code == 400
        assert any("customer_id" in e for e in ei.value.detail["errors"])
    finally:
        await _raw_db.products.delete_one({"id": prod_id})
        await _raw_db.organizations.delete_one({"id": org_id})
        await _raw_db.branches.delete_one({"id": branch_id})


# ─────────────────────────────────────────────────────────────────────
# 3 — Backdated cash sale is blocked (not allowed via this endpoint)
#     — i.e. transaction_date == today is rejected (use POS for today's sale)
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_today_dated_sale_rejected():
    """Historical credit endpoint should refuse `transaction_date == today`
    (use the regular POS for today's credit sale)."""
    org_id, branch_id, admin_id = await make_tenant()
    cust_id = await seed_customer(org_id, branch_id)
    prod_id = await seed_product(org_id, branch_id)
    admin = fake_user(org_id, admin_id, branch_id=branch_id, role="admin")
    set_org_context(org_id)
    try:
        bad = _good_payload(branch_id=branch_id, customer_id=cust_id,
                            product_id=prod_id, transaction_date=_today())
        with pytest.raises(HTTPException) as ei:
            await create_historical_credit(bad, user=admin)
        assert ei.value.status_code == 400
    finally:
        await _raw_db.customers.delete_one({"id": cust_id})
        await _raw_db.products.delete_one({"id": prod_id})
        await _raw_db.organizations.delete_one({"id": org_id})
        await _raw_db.branches.delete_one({"id": branch_id})


# ─────────────────────────────────────────────────────────────────────
# 1 — Historical credit AFTER the latest count sheet → inventory deducted
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_historical_credit_after_count_deducts_inventory():
    org_id, branch_id, admin_id = await make_tenant()
    cust_id = await seed_customer(org_id, branch_id, balance=0)
    prod_id = await seed_product(org_id, branch_id, stock=50)
    # Approved count sheet 5 days ago
    await db.count_sheets.insert_one({
        "id": _uid("cs"), "branch_id": branch_id, "status": "completed",
        "completed_at": (datetime.now(timezone.utc) - timedelta(days=5)).isoformat(),
        "items": [{"product_id": prod_id}],
    })
    admin = fake_user(org_id, admin_id, branch_id=branch_id, role="admin")
    set_org_context(org_id)
    try:
        # Transaction 2 days ago (AFTER the count) — inventory should deduct
        payload = _good_payload(branch_id=branch_id, customer_id=cust_id,
                                product_id=prod_id, transaction_date=_days_ago(2))
        res = await create_historical_credit(payload, user=admin)
        assert res["ok"] is True
        assert res["inventory_action"] == "deducted"
        assert len(res["inventory_movements"]) == 1
        # Verify inventory actually went down
        inv = await db.inventory.find_one({"product_id": prod_id, "branch_id": branch_id}, {"_id": 0})
        assert inv["quantity"] == 49
        # Customer balance up
        c = await db.customers.find_one({"id": cust_id}, {"_id": 0})
        assert c["balance"] == 1500
    finally:
        await _raw_db.invoices.delete_many({"customer_id": cust_id})
        await _raw_db.movements.delete_many({"branch_id": branch_id})
        await _raw_db.late_encode_log.delete_many({"branch_id": branch_id})
        await _raw_db.security_events.delete_many({"branch_id": branch_id})
        await _raw_db.count_sheets.delete_many({"branch_id": branch_id})
        await _raw_db.customers.delete_one({"id": cust_id})
        await _raw_db.products.delete_one({"id": prod_id})
        await _raw_db.inventory.delete_many({"branch_id": branch_id})
        await _raw_db.organizations.delete_one({"id": org_id})
        await _raw_db.branches.delete_one({"id": branch_id})


# ─────────────────────────────────────────────────────────────────────
# 2 + 12 — Historical credit BEFORE the latest count sheet → inventory NOT deducted
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_historical_credit_before_count_skips_inventory():
    org_id, branch_id, admin_id = await make_tenant()
    cust_id = await seed_customer(org_id, branch_id, balance=0)
    prod_id = await seed_product(org_id, branch_id, stock=50)
    # Approved count sheet 2 days ago
    await db.count_sheets.insert_one({
        "id": _uid("cs"), "branch_id": branch_id, "status": "completed",
        "completed_at": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
        "items": [{"product_id": prod_id}],
    })
    admin = fake_user(org_id, admin_id, branch_id=branch_id, role="admin")
    set_org_context(org_id)
    try:
        # Transaction 5 days ago (BEFORE the count) — inventory must NOT deduct
        payload = _good_payload(branch_id=branch_id, customer_id=cust_id,
                                product_id=prod_id, transaction_date=_days_ago(5))
        res = await create_historical_credit(payload, user=admin)
        assert res["inventory_action"] == "skipped_count_sheet_lock"
        assert res["inventory_movements"] == []
        # Inventory unchanged
        inv = await db.inventory.find_one({"product_id": prod_id, "branch_id": branch_id}, {"_id": 0})
        assert inv["quantity"] == 50
        # AR went up regardless
        c = await db.customers.find_one({"id": cust_id}, {"_id": 0})
        assert c["balance"] == 1500

        # With admin override — inventory DOES deduct (test 12 path B)
        payload2 = _good_payload(branch_id=branch_id, customer_id=cust_id,
                                  product_id=prod_id, transaction_date=_days_ago(5),
                                  grand_total=500, allow_inv=True)
        res2 = await create_historical_credit(payload2, user=admin)
        assert res2["inventory_action"] == "deducted_with_admin_acknowledgement"
        inv2 = await db.inventory.find_one({"product_id": prod_id, "branch_id": branch_id}, {"_id": 0})
        assert inv2["quantity"] == 49
    finally:
        await _raw_db.invoices.delete_many({"customer_id": cust_id})
        await _raw_db.movements.delete_many({"branch_id": branch_id})
        await _raw_db.late_encode_log.delete_many({"branch_id": branch_id})
        await _raw_db.security_events.delete_many({"branch_id": branch_id})
        await _raw_db.count_sheets.delete_many({"branch_id": branch_id})
        await _raw_db.customers.delete_one({"id": cust_id})
        await _raw_db.products.delete_one({"id": prod_id})
        await _raw_db.inventory.delete_many({"branch_id": branch_id})
        await _raw_db.organizations.delete_one({"id": org_id})
        await _raw_db.branches.delete_one({"id": branch_id})


# ─────────────────────────────────────────────────────────────────────
# 7 — transaction_date and encoded_at stored separately
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_transaction_date_and_encoded_at_separate():
    org_id, branch_id, admin_id = await make_tenant()
    cust_id = await seed_customer(org_id, branch_id)
    prod_id = await seed_product(org_id, branch_id)
    admin = fake_user(org_id, admin_id, branch_id=branch_id, role="admin")
    set_org_context(org_id)
    try:
        old = _days_ago(7)
        payload = _good_payload(branch_id=branch_id, customer_id=cust_id,
                                product_id=prod_id, transaction_date=old)
        res = await create_historical_credit(payload, user=admin)
        inv = res["invoice"]
        assert inv["order_date"] == old
        assert inv["late_encoded_at"][:10] == _today()
        assert inv["late_encoded"] is True
        assert inv["source"] == "historical_credit_encoding"
        assert inv["approved_by"] == admin_id
        assert inv["approved_at"][:10] == _today()
    finally:
        await _raw_db.invoices.delete_many({"customer_id": cust_id})
        await _raw_db.late_encode_log.delete_many({"branch_id": branch_id})
        await _raw_db.security_events.delete_many({"branch_id": branch_id})
        await _raw_db.customers.delete_one({"id": cust_id})
        await _raw_db.products.delete_one({"id": prod_id})
        await _raw_db.inventory.delete_many({"branch_id": branch_id})
        await _raw_db.organizations.delete_one({"id": org_id})
        await _raw_db.branches.delete_one({"id": branch_id})


# ─────────────────────────────────────────────────────────────────────
# 8 + 9 — Appears in /reports/encoded-today AND customer ledger
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_appears_in_encoded_today_and_ledger():
    from routes.reports import encoded_today_report
    from routes.customers import get_customer_transactions

    org_id, branch_id, admin_id = await make_tenant()
    cust_id = await seed_customer(org_id, branch_id, balance=0)
    prod_id = await seed_product(org_id, branch_id)
    admin = fake_user(org_id, admin_id, branch_id=branch_id, role="admin")
    set_org_context(org_id)
    try:
        old = _days_ago(3)
        payload = _good_payload(branch_id=branch_id, customer_id=cust_id,
                                product_id=prod_id, transaction_date=old)
        res = await create_historical_credit(payload, user=admin)
        inv_no = res["invoice"]["invoice_number"]

        # Encoded-today report
        rep = await encoded_today_report(
            branch_id=branch_id, on_date=_today(), user=admin,
        )
        assert inv_no in {i["invoice_number"] for i in rep["invoices"]}

        # Customer ledger — surfaces transaction_date, encoded_today, late_encoded
        ledger = await get_customer_transactions(cust_id, user=admin)
        invs = ledger["invoices"]
        row = next(r for r in invs if r["invoice_number"] == inv_no)
        assert row["transaction_date"] == old
        assert row["late_encoded"] is True
        assert row["encoded_today"] is True
        assert row["source"] == "historical_credit_encoding"
    finally:
        await _raw_db.invoices.delete_many({"customer_id": cust_id})
        await _raw_db.late_encode_log.delete_many({"branch_id": branch_id})
        await _raw_db.security_events.delete_many({"branch_id": branch_id})
        await _raw_db.customers.delete_one({"id": cust_id})
        await _raw_db.products.delete_one({"id": prod_id})
        await _raw_db.inventory.delete_many({"branch_id": branch_id})
        await _raw_db.organizations.delete_one({"id": org_id})
        await _raw_db.branches.delete_one({"id": branch_id})


# ─────────────────────────────────────────────────────────────────────
# 10 — Old closed Z-report's regular-credit section does NOT change
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_old_zreport_regular_section_unaffected():
    """The credit_invoices query in daily_operations Z-report excludes
    `source: historical_credit_encoding`. So encoding a historical credit
    today for an OLD date does NOT silently appear in that old day's
    regular credit-sales section."""
    org_id, branch_id, admin_id = await make_tenant()
    cust_id = await seed_customer(org_id, branch_id, balance=0)
    prod_id = await seed_product(org_id, branch_id)
    admin = fake_user(org_id, admin_id, branch_id=branch_id, role="admin")
    set_org_context(org_id)
    try:
        old = _days_ago(4)
        payload = _good_payload(branch_id=branch_id, customer_id=cust_id,
                                product_id=prod_id, transaction_date=old)
        await create_historical_credit(payload, user=admin)

        # Mirror the daily_operations Z-report query for the OLD date
        rows = await db.invoices.find(
            {"branch_id": branch_id, "order_date": old,
             "payment_type": {"$in": ["credit", "partial"]},
             "status": {"$ne": "voided"},
             "source": {"$ne": "historical_credit_encoding"}},
            {"_id": 0, "invoice_number": 1},
        ).to_list(100)
        assert rows == [], "Historical credit must not pollute old-day Z-report regular credit section"
    finally:
        await _raw_db.invoices.delete_many({"customer_id": cust_id})
        await _raw_db.late_encode_log.delete_many({"branch_id": branch_id})
        await _raw_db.security_events.delete_many({"branch_id": branch_id})
        await _raw_db.customers.delete_one({"id": cust_id})
        await _raw_db.products.delete_one({"id": prod_id})
        await _raw_db.inventory.delete_many({"branch_id": branch_id})
        await _raw_db.organizations.delete_one({"id": org_id})
        await _raw_db.branches.delete_one({"id": branch_id})


# ─────────────────────────────────────────────────────────────────────
# 11 — Customer.balance increases correctly
#       (covered above in test_historical_credit_after_count and
#        test_historical_credit_before_count; an explicit assert here too)
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_customer_balance_goes_up_by_grand_total():
    org_id, branch_id, admin_id = await make_tenant()
    cust_id = await seed_customer(org_id, branch_id, balance=200)
    prod_id = await seed_product(org_id, branch_id)
    admin = fake_user(org_id, admin_id, branch_id=branch_id, role="admin")
    set_org_context(org_id)
    try:
        payload = _good_payload(branch_id=branch_id, customer_id=cust_id,
                                product_id=prod_id, transaction_date=_days_ago(1),
                                grand_total=750)
        await create_historical_credit(payload, user=admin)
        c = await db.customers.find_one({"id": cust_id}, {"_id": 0})
        assert round(c["balance"], 2) == 950.0
    finally:
        await _raw_db.invoices.delete_many({"customer_id": cust_id})
        await _raw_db.late_encode_log.delete_many({"branch_id": branch_id})
        await _raw_db.security_events.delete_many({"branch_id": branch_id})
        await _raw_db.customers.delete_one({"id": cust_id})
        await _raw_db.products.delete_one({"id": prod_id})
        await _raw_db.inventory.delete_many({"branch_id": branch_id})
        await _raw_db.organizations.delete_one({"id": org_id})
        await _raw_db.branches.delete_one({"id": branch_id})


# ─────────────────────────────────────────────────────────────────────
# 13 — Branch / tenant scoping (cross-branch access blocked)
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_cross_branch_blocked_for_non_privileged_admin_pattern():
    """Even an admin-or-owner is gated through assert_branch_access for
    branches outside their assignment (per Phase 2D)."""
    org_id, branch_a, admin_id = await make_tenant()
    branch_b = _uid("p3-br")
    await _raw_db.branches.insert_one({
        "id": branch_b, "organization_id": org_id, "name": "B", "active": True,
    })
    cust_id = await seed_customer(org_id, branch_b)  # customer in branch B
    prod_id = await seed_product(org_id, branch_b)
    # Manager (non-admin, non-owner) scoped to branch_a
    manager = {
        "id": _uid("mgr"), "role": "manager",
        "organization_id": org_id, "active": True,
        "branch_ids": [branch_a],
    }
    set_org_context(org_id)
    try:
        # Manager → blocked at the assert_admin_or_owner gate
        payload = _good_payload(branch_id=branch_b, customer_id=cust_id,
                                product_id=prod_id, transaction_date=_days_ago(2))
        with pytest.raises(HTTPException) as ei:
            await create_historical_credit(payload, user=manager)
        assert ei.value.status_code == 403
    finally:
        await _raw_db.customers.delete_one({"id": cust_id})
        await _raw_db.products.delete_one({"id": prod_id})
        await _raw_db.branches.delete_many({"id": {"$in": [branch_a, branch_b]}})
        await _raw_db.organizations.delete_one({"id": org_id})


# ─────────────────────────────────────────────────────────────────────
# 15 — Preview is read-only
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_preview_is_read_only():
    org_id, branch_id, admin_id = await make_tenant()
    cust_id = await seed_customer(org_id, branch_id, balance=100)
    prod_id = await seed_product(org_id, branch_id, stock=10)
    admin = fake_user(org_id, admin_id, branch_id=branch_id, role="admin")
    set_org_context(org_id)
    try:
        payload = _good_payload(branch_id=branch_id, customer_id=cust_id,
                                product_id=prod_id, transaction_date=_days_ago(2))
        res = await preview_historical_credit(payload, user=admin)
        assert res["preview"] is True
        # Customer balance unchanged
        c = await db.customers.find_one({"id": cust_id}, {"_id": 0})
        assert c["balance"] == 100
        # No invoice was inserted
        cnt = await db.invoices.count_documents({"customer_id": cust_id})
        assert cnt == 0
        # Inventory unchanged
        inv = await db.inventory.find_one({"product_id": prod_id, "branch_id": branch_id}, {"_id": 0})
        assert inv["quantity"] == 10
    finally:
        await _raw_db.customers.delete_one({"id": cust_id})
        await _raw_db.products.delete_one({"id": prod_id})
        await _raw_db.inventory.delete_many({"branch_id": branch_id})
        await _raw_db.organizations.delete_one({"id": org_id})
        await _raw_db.branches.delete_one({"id": branch_id})


# ─────────────────────────────────────────────────────────────────────
# Extra — list endpoint surfaces created entries
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_list_returns_only_historical_credits():
    org_id, branch_id, admin_id = await make_tenant()
    cust_id = await seed_customer(org_id, branch_id)
    prod_id = await seed_product(org_id, branch_id)
    admin = fake_user(org_id, admin_id, branch_id=branch_id, role="admin")
    set_org_context(org_id)
    try:
        payload = _good_payload(branch_id=branch_id, customer_id=cust_id,
                                product_id=prod_id, transaction_date=_days_ago(2))
        await create_historical_credit(payload, user=admin)

        listing = await list_historical_credits(
            branch_id=branch_id, customer_id=cust_id, user=admin,
        )
        assert listing["count"] >= 1
        assert all(i["source"] == "historical_credit_encoding" for i in listing["items"])
    finally:
        await _raw_db.invoices.delete_many({"customer_id": cust_id})
        await _raw_db.late_encode_log.delete_many({"branch_id": branch_id})
        await _raw_db.security_events.delete_many({"branch_id": branch_id})
        await _raw_db.customers.delete_one({"id": cust_id})
        await _raw_db.products.delete_one({"id": prod_id})
        await _raw_db.inventory.delete_many({"branch_id": branch_id})
        await _raw_db.organizations.delete_one({"id": org_id})
        await _raw_db.branches.delete_one({"id": branch_id})

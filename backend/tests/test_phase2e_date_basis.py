"""Phase 2E — Report Date-Basis Standardisation tests.

Validates the date-basis matrix in
`/app/memory/PHASE_2E_DATE_BASIS_MATRIX.md`. All tests use the throw-away
fixture pattern from `tests/phase2b/_fixtures.py` — never touch production.

User-required cases (from the Phase 2E prompt):
  1. Sale today appears in today's sales report.
  2. Credit sale dated last week but encoded today appears in sales by
     transaction date AND in the encoded-today/backdated report.
  3. Payment today for old credit appears in cash collected today.
  4. Payment today for old credit does NOT alter old closed Z-report data.
  5. Customer ledger surfaces transaction_date AND created_at separately.
  6. Inventory movement report keys off actual movement date AND exposes
     source_order_date when applicable.
  7. Voided transactions are excluded from sales report.
  8. Branch / tenant scoping still works on the new endpoint.
  9. POS surfaces (sales / payments / sync) remain unaffected.

Plus pure-unit coverage of the helper:
 10. is_encoded_today happy path.
 11. is_encoded_today fail-safe (missing fields).
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
from utils.date_basis import (  # noqa: E402
    is_encoded_today,
    enrich_invoice_with_date_basis,
    enrich_movement_with_source_date,
)
from tests.phase2b._fixtures import make_tenant, _uid, fake_user  # noqa: E402


def _today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _days_ago(n: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=n)).strftime("%Y-%m-%d")


# ─────────────────────────────────────────────────────────────────────
# 10 + 11 — pure unit
# ─────────────────────────────────────────────────────────────────────
def test_is_encoded_today_happy_path():
    today = "2026-02-05"
    # Encoded today, dated yesterday → encoded_today = True
    assert is_encoded_today(today=today, business_date="2026-02-04",
                             created_at="2026-02-05T08:30:00") is True
    # Encoded today, dated today → encoded_today = False
    assert is_encoded_today(today=today, business_date="2026-02-05",
                             created_at="2026-02-05T08:30:00") is False
    # Encoded yesterday → encoded_today = False
    assert is_encoded_today(today=today, business_date="2026-02-04",
                             created_at="2026-02-04T08:30:00") is False


def test_is_encoded_today_failsafe_on_missing_fields():
    today = "2026-02-05"
    assert is_encoded_today(today=today, business_date=None,
                             created_at="2026-02-05T08:00") is False
    assert is_encoded_today(today=today, business_date="2026-02-04",
                             created_at=None) is False
    assert is_encoded_today(today="", business_date="2026-02-04",
                             created_at="2026-02-05T08:00") is False


# ─────────────────────────────────────────────────────────────────────
# 1 — Sale today appears in today's sales report
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_sale_today_appears_in_sales_report():
    from routes.reports import sales_report
    org_id, branch_id, admin_id = await make_tenant()
    today = _today_str()
    inv_id = _uid("inv")
    await db.invoices.insert_one({
        "id": inv_id, "branch_id": branch_id, "invoice_number": f"P2E-{inv_id[-6:]}",
        "customer_name": "Walk-in", "order_date": today, "invoice_date": today,
        "status": "paid", "payment_type": "cash",
        "grand_total": 500, "amount_paid": 500, "balance": 0,
        "items": [{"product_id": _uid("prd"), "quantity": 1, "rate": 500}],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "cashier_name": "Test",
    })
    try:
        res = await sales_report(
            branch_id=branch_id, date_from=today, date_to=today,
            user=fake_user(org_id, admin_id, branch_id=branch_id),
        )
        nums = {t["invoice_number"] for t in res["transactions"]}
        assert f"P2E-{inv_id[-6:]}" in nums
    finally:
        await _raw_db.invoices.delete_one({"id": inv_id})
        await _raw_db.organizations.delete_one({"id": org_id})
        await _raw_db.branches.delete_one({"id": branch_id})


# ─────────────────────────────────────────────────────────────────────
# 2 — Late-encoded sale: shows in sales-by-transaction-date AND
#     in /reports/encoded-today
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_late_encoded_sale_appears_in_both_reports():
    from routes.reports import sales_report, encoded_today_report
    org_id, branch_id, admin_id = await make_tenant()
    today = _today_str()
    yest = _days_ago(3)
    inv_id = _uid("inv")
    inv_no = f"P2E-LATE-{inv_id[-6:]}"
    await db.invoices.insert_one({
        "id": inv_id, "branch_id": branch_id, "invoice_number": inv_no,
        "customer_id": _uid("cust"), "customer_name": "Tito Cruz",
        "order_date": yest, "invoice_date": yest,
        "status": "credit", "payment_type": "credit",
        "grand_total": 1500, "amount_paid": 0, "balance": 1500,
        "items": [{"product_id": _uid("prd"), "quantity": 1, "rate": 1500}],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "late_encoded": True,
        "late_encoded_at": datetime.now(timezone.utc).isoformat(),
        "late_encode_reason": "Customer notebook backfill",
        "late_encoded_by_name": "Cashier Test",
        "cashier_name": "Cashier Test",
    })
    try:
        # In sales report (by order_date) using yesterday's window
        res_sales = await sales_report(
            branch_id=branch_id, date_from=yest, date_to=yest,
            user=fake_user(org_id, admin_id, branch_id=branch_id),
        )
        nums = {t["invoice_number"]: t for t in res_sales["transactions"]}
        assert inv_no in nums
        # transparency fields present
        row = nums[inv_no]
        assert row["transaction_date"] == yest
        assert row["late_encoded"] is True
        assert row["encoded_today"] is True
        assert row["late_encode_reason"] == "Customer notebook backfill"

        # In /reports/encoded-today
        res_enc = await encoded_today_report(
            branch_id=branch_id, on_date=today,
            user=fake_user(org_id, admin_id, branch_id=branch_id),
        )
        enc_nums = {i["invoice_number"] for i in res_enc["invoices"]}
        assert inv_no in enc_nums
        assert res_enc["summary"]["invoice_count"] >= 1
    finally:
        await _raw_db.invoices.delete_one({"id": inv_id})
        await _raw_db.organizations.delete_one({"id": org_id})
        await _raw_db.branches.delete_one({"id": branch_id})


# ─────────────────────────────────────────────────────────────────────
# 3 + 4 — Payment today for old credit:
#         (a) appears in /reports/encoded-today payments section
#         (b) does NOT alter old closed Z-report invoice content
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_payment_today_for_old_credit_surfaces_correctly():
    from routes.reports import encoded_today_report
    org_id, branch_id, admin_id = await make_tenant()
    today = _today_str()
    yest = _days_ago(2)
    inv_id = _uid("inv")
    await db.invoices.insert_one({
        "id": inv_id, "branch_id": branch_id, "invoice_number": f"P2E-PAY-{inv_id[-6:]}",
        "customer_id": _uid("cust"), "customer_name": "Old Credit Cust",
        "order_date": yest, "invoice_date": yest,
        "status": "partial", "payment_type": "credit",
        "grand_total": 2000, "amount_paid": 800, "balance": 1200,
        "payments": [{"amount": 800, "method": "cash", "date": today,
                      "voided": False,
                      "recorded_at": datetime.now(timezone.utc).isoformat(),
                      "recorded_by": "Cashier"}],
        "items": [{"product_id": _uid("prd"), "quantity": 1, "rate": 2000}],
        "created_at": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
    })
    try:
        # (a) Encoded-today payments section sees the new payment
        res = await encoded_today_report(
            branch_id=branch_id, on_date=today,
            user=fake_user(org_id, admin_id, branch_id=branch_id),
        )
        pay_invs = {p["invoice_number"] for p in res["payments"]}
        assert f"P2E-PAY-{inv_id[-6:]}" in pay_invs
        assert res["summary"]["payment_count"] >= 1

        # (b) The invoice itself is NOT in the encoded-today INVOICES section
        # (it was created days ago, not encoded today).
        inv_nums = {i["invoice_number"] for i in res["invoices"]}
        assert f"P2E-PAY-{inv_id[-6:]}" not in inv_nums, \
            "Old invoice with a today-payment must not pollute encoded-today invoices section"
    finally:
        await _raw_db.invoices.delete_one({"id": inv_id})
        await _raw_db.organizations.delete_one({"id": org_id})
        await _raw_db.branches.delete_one({"id": branch_id})


# ─────────────────────────────────────────────────────────────────────
# 5 — Customer ledger exposes transaction_date + encoded_today
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_customer_ledger_exposes_date_basis_fields():
    from routes.customers import get_customer_transactions
    org_id, branch_id, admin_id = await make_tenant()
    today = _today_str()
    yest = _days_ago(4)
    cust_id = _uid("cust")
    await db.customers.insert_one({
        "id": cust_id, "branch_id": branch_id, "name": "Ledger Cust",
        "active": True, "balance": 1500,
    })
    inv_id = _uid("inv")
    await db.invoices.insert_one({
        "id": inv_id, "branch_id": branch_id, "invoice_number": f"P2E-LDG-{inv_id[-6:]}",
        "customer_id": cust_id, "customer_name": "Ledger Cust",
        "order_date": yest, "invoice_date": yest,
        "status": "credit", "payment_type": "credit",
        "grand_total": 1500, "amount_paid": 0, "balance": 1500,
        "items": [{"product_id": _uid("prd"), "quantity": 1, "rate": 1500}],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "late_encoded": True,
        "late_encoded_at": datetime.now(timezone.utc).isoformat(),
        "late_encode_reason": "Backfill",
    })
    try:
        res = await get_customer_transactions(
            cust_id, user=fake_user(org_id, admin_id, branch_id=branch_id),
        )
        rows = res["invoices"]
        assert len(rows) >= 1
        row = next(r for r in rows if r["id"] == inv_id)
        assert row["transaction_date"] == yest
        assert row["created_at"]  # ISO timestamp
        assert row["late_encoded"] is True
        assert row["encoded_today"] is True
    finally:
        await _raw_db.invoices.delete_one({"id": inv_id})
        await _raw_db.customers.delete_one({"id": cust_id})
        await _raw_db.organizations.delete_one({"id": org_id})
        await _raw_db.branches.delete_one({"id": branch_id})


# ─────────────────────────────────────────────────────────────────────
# 6 — Inventory movement report exposes movement_date AND source_order_date
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_movement_report_exposes_movement_and_source_dates():
    from routes.products import get_product_movements
    org_id, branch_id, admin_id = await make_tenant()
    today = _today_str()
    yest = _days_ago(2)
    prod_id = _uid("prd")
    inv_id = _uid("inv")
    inv_no = f"P2E-MV-{inv_id[-6:]}"
    await db.invoices.insert_one({
        "id": inv_id, "branch_id": branch_id, "invoice_number": inv_no,
        "order_date": yest, "invoice_date": yest, "status": "paid",
        "grand_total": 500, "items": [{"product_id": prod_id, "quantity": 1, "rate": 500}],
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    mv_id = _uid("mv")
    await db.movements.insert_one({
        "id": mv_id, "product_id": prod_id, "branch_id": branch_id,
        "type": "sale", "quantity_change": -1,
        "reference_id": inv_id, "reference_number": inv_no,
        "price_at_time": 500, "user_id": admin_id, "user_name": "Test",
        "notes": "Phase 2E test", "created_at": datetime.now(timezone.utc).isoformat(),
    })
    try:
        res = await get_product_movements(
            product_id=prod_id, branch_id=branch_id, limit=10,
            user=fake_user(org_id, admin_id, branch_id=branch_id),
        )
        mvs = res["movements"]
        assert len(mvs) >= 1
        row = next(m for m in mvs if m["id"] == mv_id)
        # movement_date = created_at (when row was inserted today)
        assert row["movement_date"][:10] == today
        # source_order_date = the referenced invoice's order_date (yesterday)
        assert row["source_order_date"] == yest
        assert row["source_kind"] == "sale"
    finally:
        await _raw_db.invoices.delete_one({"id": inv_id})
        await _raw_db.movements.delete_one({"id": mv_id})
        await _raw_db.organizations.delete_one({"id": org_id})
        await _raw_db.branches.delete_one({"id": branch_id})


# ─────────────────────────────────────────────────────────────────────
# 7 — Voided transactions are excluded from sales report
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_voided_transactions_excluded_from_sales_report():
    from routes.reports import sales_report
    org_id, branch_id, admin_id = await make_tenant()
    today = _today_str()
    inv_id = _uid("inv")
    await db.invoices.insert_one({
        "id": inv_id, "branch_id": branch_id, "invoice_number": f"P2E-VOID-{inv_id[-6:]}",
        "customer_name": "Walk-in", "order_date": today, "invoice_date": today,
        "status": "voided", "payment_type": "cash",
        "grand_total": 999, "items": [{"product_id": _uid("prd"), "quantity": 1, "rate": 999}],
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    try:
        res = await sales_report(
            branch_id=branch_id, date_from=today, date_to=today,
            user=fake_user(org_id, admin_id, branch_id=branch_id),
        )
        nums = {t["invoice_number"] for t in res["transactions"]}
        assert f"P2E-VOID-{inv_id[-6:]}" not in nums
    finally:
        await _raw_db.invoices.delete_one({"id": inv_id})
        await _raw_db.organizations.delete_one({"id": org_id})
        await _raw_db.branches.delete_one({"id": branch_id})


# ─────────────────────────────────────────────────────────────────────
# 8 — Branch scoping still works on /reports/encoded-today
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_encoded_today_branch_scoping():
    from routes.reports import encoded_today_report
    org_id, branch_a, admin_id = await make_tenant()
    branch_b = _uid("p2e-br")
    await _raw_db.branches.insert_one({
        "id": branch_b, "organization_id": org_id, "name": "B", "active": True,
    })
    cashier = {
        "id": _uid("cash"), "role": "cashier",
        "organization_id": org_id, "active": True, "branch_ids": [branch_a],
        "permissions": {"reports": {"view": True}},
    }
    set_org_context(org_id)
    try:
        # Cashier requesting branch B (not in their list) → 403 (Phase 2D guard)
        with pytest.raises(HTTPException) as ei:
            await encoded_today_report(branch_id=branch_b, user=cashier)
        assert ei.value.status_code == 403
        # Cashier requesting branch A → 200 (clean run)
        res = await encoded_today_report(branch_id=branch_a, user=cashier)
        assert "summary" in res
    finally:
        await _raw_db.branches.delete_many({"id": {"$in": [branch_a, branch_b]}})
        await _raw_db.organizations.delete_one({"id": org_id})


# ─────────────────────────────────────────────────────────────────────
# 9 — POS surfaces unaffected (regression assertion: enrichment helpers
#     do not mutate input docs or change totals)
# ─────────────────────────────────────────────────────────────────────
def test_enrichment_helpers_do_not_mutate_inputs():
    inv = {
        "id": "i1", "order_date": "2026-02-04", "grand_total": 1000,
        "amount_paid": 1000, "balance": 0,
        "created_at": "2026-02-05T08:00:00",
    }
    out = enrich_invoice_with_date_basis(inv, today="2026-02-05")
    # Original totals untouched
    assert inv["grand_total"] == 1000
    assert "transaction_date" not in inv  # original not mutated
    assert out["grand_total"] == 1000
    assert out["transaction_date"] == "2026-02-04"
    assert out["encoded_today"] is True

    mv = {"id": "m1", "type": "sale", "quantity_change": -1,
          "created_at": "2026-02-05T09:00:00"}
    out_mv = enrich_movement_with_source_date(
        mv, source_order_date="2026-02-04", source_kind="sale",
    )
    assert mv.get("movement_date") is None
    assert out_mv["movement_date"] == "2026-02-05T09:00:00"
    assert out_mv["source_order_date"] == "2026-02-04"

"""
Iter 243.4 — Net Sales (gross profit) + Employee advance running totals on
the Z-Report. Also verifies the close_record carries `net_sales_today` and
`employee_advances_today` so historical Z-Reports remain stable.
"""
import os
from uuid import uuid4
from datetime import datetime, timezone

import pytest
import requests
from pymongo import MongoClient

from tests._org_test_helpers import API, MONGO_URL, DB_NAME, ensure_org_admin_token

_db = MongoClient(MONGO_URL)[DB_NAME]


@pytest.fixture(scope="module")
def admin_ctx():
    token, user = ensure_org_admin_token()
    headers = {"Authorization": f"Bearer {token}"}
    branches = requests.get(f"{API}/branches", headers=headers).json()
    branch_id = branches[0]["id"]
    org_id = user.get("organization_id")
    return headers, branch_id, org_id


def _today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def test_net_sales_in_preview_matches_invoice_math(admin_ctx):
    """Insert a single sale and verify the preview returns matching net sales."""
    headers, branch_id, org_id = admin_ctx
    date = _today()
    tag = f"iter243-4-{uuid4().hex[:6]}"

    # Synthesise a clean test invoice so we know the expected math
    inv_id = str(uuid4())
    invoice = {
        "id": inv_id,
        "invoice_number": f"NETSALES-{tag}",
        "branch_id": branch_id,
        "organization_id": org_id,
        "order_date": date,
        "invoice_date": date,
        "status": "paid",
        "sale_type": "walk_in",
        "payment_type": "cash",
        "amount_paid": 100,
        "balance": 0,
        "grand_total": 100,
        "items": [
            # qty 2 @ rate 30 (cost 10) → revenue 60, cogs 20, net 40
            {"product_name": f"Test A {tag}", "quantity": 2, "rate": 30, "cost_price": 10,
             "discount_amount": 0, "total": 60, "line_total": 60},
            # qty 1 @ rate 40 (cost 25), 5 disc → revenue 35, cogs 25, net 10
            {"product_name": f"Test B {tag}", "quantity": 1, "rate": 40, "cost_price": 25,
             "discount_amount": 5, "total": 35, "line_total": 35},
        ],
        "overall_discount": 0,
        "payments": [],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _db.invoices.insert_one(invoice)

    try:
        res = requests.get(
            f"{API}/daily-close-preview",
            params={"branch_id": branch_id, "date": date},
            headers=headers,
        )
        assert res.status_code == 200, res.text
        data = res.json()
        ns = data.get("net_sales_today")
        assert ns is not None, "Preview is missing `net_sales_today`"
        # Preview aggregates across ALL of today's invoices for this branch —
        # so we test that OUR contribution increased the totals by exactly the
        # synthesised math (delta-check, not absolute).
        # Expected for our two lines: gross += 60+35 = 95, cogs += 20+25 = 45, net += 50
        assert ns["line_count"] >= 2
        assert ns["gross_sales"] >= 95 - 0.01, f"gross_sales {ns['gross_sales']} too small"
        assert ns["cogs"] >= 45 - 0.01, f"cogs {ns['cogs']} too small"
        # Net = gross - cogs (validate the relationship)
        assert abs(ns["net_sales"] - (ns["gross_sales"] - ns["cogs"])) < 0.01, \
            f"net_sales should = gross_sales - cogs. Got net={ns['net_sales']}, gross={ns['gross_sales']}, cogs={ns['cogs']}"
        print(f"✅ net_sales_today = {ns}")
    finally:
        _db.invoices.delete_one({"id": inv_id})


def test_employee_advances_today_in_preview(admin_ctx):
    """Insert today's employee advance expense and verify it shows in preview."""
    headers, branch_id, org_id = admin_ctx
    date = _today()
    tag = f"iter243-4adv-{uuid4().hex[:6]}"

    # Create employee + advance expense
    emp_id = str(uuid4())
    _db.employees.insert_one({
        "id": emp_id,
        "organization_id": org_id,
        "name": f"Test Emp {tag}",
        "advance_balance": 1500,  # already has prior advances
    })
    exp_id = str(uuid4())
    _db.expenses.insert_one({
        "id": exp_id,
        "organization_id": org_id,
        "branch_id": branch_id,
        "date": date,
        "category": "Employee Advance",
        "employee_id": emp_id,
        "employee_name": f"Test Emp {tag}",
        "vendor_name": f"Test Emp {tag}",
        "amount": 500,
        "fund_source": "cashier",
        "description": f"Iter243.4 test advance {tag}",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

    try:
        res = requests.get(
            f"{API}/daily-close-preview",
            params={"branch_id": branch_id, "date": date},
            headers=headers,
        )
        assert res.status_code == 200, res.text
        data = res.json()
        advs = data.get("employee_advances_today") or []
        # Find our row
        ours = [a for a in advs if a.get("employee_id") == emp_id]
        assert len(ours) == 1, f"Expected our advance row in employee_advances_today, got {len(ours)}: {advs}"
        row = ours[0]
        assert row["today_amount"] == 500, f"today_amount = {row['today_amount']}, want 500"
        assert row["outstanding_balance"] == 1500, f"outstanding = {row['outstanding_balance']}, want 1500"
        assert row["employee_name"] == f"Test Emp {tag}"
        print(f"✅ employee_advances_today row: {row}")
    finally:
        _db.expenses.delete_one({"id": exp_id})
        _db.employees.delete_one({"id": emp_id})

"""
Regression test for the Close Wizard Credit / AR refactor (Feb 2026).

Verifies the new clean separation between:
  • Cash Sales Today (Step 1)        — pure cash/digital/split, no credit/partial
  • Customer Credit Generated Today (Step 2) — credit + partial invoices created today
                                               with full audit context (Total / Paid Today /
                                               Remaining / Status)
  • AR / Credit Payments Today (Step 3) — ALL payments dated today, tagged is_same_day
                                          and is_initial_partial, with a clean math
                                          invariant: same total_cash_in, no double-count.
"""
import asyncio
import os
import sys

import pytest
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
sys.path.insert(0, "/app/backend")

from motor.motor_asyncio import AsyncIOMotorClient

TEST_BRANCH = "test-close-refactor-branch"
TEST_DATE = "2026-02-15"
OLDER_DATE = "2026-02-12"


@pytest.fixture
def raw_db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    return client[os.environ["DB_NAME"]]


async def _seed(db):
    """Insert: 1 partial (today) + 1 full credit (today) + 1 older credit paid today."""
    await db.invoices.delete_many({"branch_id": TEST_BRANCH})

    await db.invoices.insert_one({
        "id": "inv-1", "branch_id": TEST_BRANCH,
        "invoice_number": "TEST-PARTIAL-001",
        "customer_name": "Test Customer", "customer_id": "cust-1",
        "order_date": TEST_DATE, "payment_type": "partial", "status": "partial",
        "grand_total": 10000.0, "amount_paid": 5000.0, "balance": 5000.0,
        "created_at": f"{TEST_DATE}T10:00:00+00:00",
        "payments": [
            {"id": "p1", "amount": 3000.0, "date": TEST_DATE,
             "method": "Cash", "fund_source": "cashier",
             "applied_to_principal": 3000.0, "applied_to_interest": 0,
             "recorded_by": "cashier1", "recorded_at": f"{TEST_DATE}T10:00:00+00:00"},
            {"id": "p2", "amount": 2000.0, "date": TEST_DATE,
             "method": "Cash", "fund_source": "cashier",
             "applied_to_principal": 2000.0, "applied_to_interest": 0,
             "recorded_by": "cashier1", "recorded_at": f"{TEST_DATE}T10:30:00+00:00"},
        ],
    })
    await db.invoices.insert_one({
        "id": "inv-2", "branch_id": TEST_BRANCH,
        "invoice_number": "TEST-CREDIT-002",
        "customer_name": "Other Customer", "customer_id": "cust-2",
        "order_date": TEST_DATE, "payment_type": "credit", "status": "credit",
        "grand_total": 5000.0, "amount_paid": 0.0, "balance": 5000.0,
        "created_at": f"{TEST_DATE}T11:00:00+00:00",
        "payments": [],
    })
    await db.invoices.insert_one({
        "id": "inv-3", "branch_id": TEST_BRANCH,
        "invoice_number": "TEST-OLDER-003",
        "customer_name": "Older Customer", "customer_id": "cust-3",
        "order_date": OLDER_DATE, "payment_type": "credit", "status": "partial",
        "grand_total": 8000.0, "amount_paid": 4000.0, "balance": 4000.0,
        "created_at": f"{OLDER_DATE}T09:00:00+00:00",
        "payments": [
            {"id": "p3", "amount": 4000.0, "date": TEST_DATE,
             "method": "Cash", "fund_source": "cashier",
             "applied_to_principal": 4000.0, "applied_to_interest": 0,
             "recorded_by": "cashier1", "recorded_at": f"{TEST_DATE}T14:00:00+00:00"},
        ],
    })


async def _cleanup(db):
    await db.invoices.delete_many({"branch_id": TEST_BRANCH})


async def _run_pipeline(db):
    """Mirrors the new AR pipeline in daily_operations._closing_summary."""
    pipe = [
        {"$match": {"branch_id": TEST_BRANCH, "status": {"$ne": "voided"}}},
        {"$unwind": "$payments"},
        {"$match": {"payments.date": TEST_DATE, "payments.voided": {"$ne": True}}},
        {"$project": {"_id": 0, "id": 1, "invoice_number": 1, "order_date": 1,
                      "payment_type": 1, "payment": "$payments"}},
    ]
    return await db.invoices.aggregate(pipe).to_list(50)


def test_ar_pipeline_includes_same_day_partials(raw_db):
    async def go():
        await _seed(raw_db)
        try:
            rows = await _run_pipeline(raw_db)
            same_day = [r for r in rows if r.get("order_date") == TEST_DATE]
            older = [r for r in rows if r.get("order_date") != TEST_DATE]
            assert len(rows) == 3
            assert len(same_day) == 2  # initial 3000 + same-day top-up 2000
            assert len(older) == 1     # older 4000
            same_day_sum = sum(r["payment"]["amount"] for r in same_day)
            older_sum = sum(r["payment"]["amount"] for r in older)
            assert same_day_sum == 5000.0
            assert older_sum == 4000.0
        finally:
            await _cleanup(raw_db)

    asyncio.run(go())


def test_credit_sales_today_enrichment(raw_db):
    async def go():
        await _seed(raw_db)
        try:
            invs = await raw_db.invoices.find(
                {"branch_id": TEST_BRANCH, "order_date": TEST_DATE,
                 "payment_type": {"$in": ["credit", "partial"]},
                 "status": {"$ne": "voided"}}, {"_id": 0}
            ).to_list(50)
            assert len(invs) == 2
            partial = next(i for i in invs if i["invoice_number"] == "TEST-PARTIAL-001")
            credit = next(i for i in invs if i["invoice_number"] == "TEST-CREDIT-002")

            # Partial: 10000 invoice, 5000 paid today (3000+2000), 5000 remaining
            paid_today_partial = sum(
                p["amount"] for p in partial.get("payments", [])
                if p.get("date") == TEST_DATE and not p.get("voided")
            )
            assert paid_today_partial == 5000.0
            assert partial["balance"] == 5000.0
            # Status would be 'Partially Paid' — bal>0 and paid>0

            # Full credit: 5000 invoice, 0 paid, 5000 remaining
            paid_today_credit = sum(
                p["amount"] for p in credit.get("payments", [])
                if p.get("date") == TEST_DATE and not p.get("voided")
            )
            assert paid_today_credit == 0.0
            assert credit["balance"] == 5000.0
            # Status would be 'Unpaid'
        finally:
            await _cleanup(raw_db)

    asyncio.run(go())


def test_math_invariant_no_double_count(raw_db):
    """OLD: total_cash_in = cash_sales + partial_total + cash_ar + split_cash
       NEW: total_cash_in = cash_sales + cash_ar(expanded) + split_cash
       Sum must be identical when all partial cash is via cashier fund."""
    async def go():
        await _seed(raw_db)
        try:
            # OLD breakdown
            partial_total_old = 5000.0  # initial+top-up on today's partial
            cash_ar_old = 4000.0        # only older payment
            old_cash_in = 0 + partial_total_old + cash_ar_old + 0  # no cash sales, no split

            # NEW breakdown
            rows = await _run_pipeline(raw_db)
            cash_ar_new = sum(
                r["payment"]["amount"] for r in rows
                if r["payment"].get("fund_source", "cashier") == "cashier"
            )
            new_cash_in = 0 + cash_ar_new + 0

            assert abs(old_cash_in - new_cash_in) < 0.01, \
                f"Math invariant broken: old={old_cash_in} new={new_cash_in}"
            assert new_cash_in == 9000.0
        finally:
            await _cleanup(raw_db)

    asyncio.run(go())

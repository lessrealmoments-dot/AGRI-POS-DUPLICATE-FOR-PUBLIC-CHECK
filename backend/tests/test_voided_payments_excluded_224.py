"""
Iter 224 — Voided payments must NOT appear in Close Wizard AR Payments.

Live report: Customer DANIELES ROSEMARRIE appeared twice in Step 3 AR
Payments of the Close Wizard with overlapping amounts (₱42,295 + ₱38,995
totalling ₱81,290 against a single invoice whose real net collection was
₱43,105). Root cause: the AR-payments aggregation pipeline unwound every
payment on every invoice and matched on date — voided payments slipped in
silently. Fixed in daily_operations.py / audit.py / dashboard.py by adding
`payments.voided: {$ne: True}` to every unwind match.
"""
import os
import sys
import uuid
from datetime import datetime, timezone

import pytest
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests._org_test_helpers import ensure_org_admin_token  # noqa: E402

BASE_URL = os.environ.get("BACKEND_BASE_URL", "http://localhost:8001")
API = f"{BASE_URL}/api"


def _run(coro):
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _db():
    from motor.motor_asyncio import AsyncIOMotorClient
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


@pytest.fixture(scope="module")
def headers():
    token, _ = ensure_org_admin_token()
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def test_context(headers):
    """Seed a fake AR invoice with ONE voided payment + ONE live payment both
    dated today. The Close Wizard preview should report ONLY the live payment."""

    async def _seed():
        db = _db()
        u = await db.users.find_one({"email": "test_org_admin@regression.local"}, {"_id": 0, "organization_id": 1})
        org_id = u["organization_id"]
        branch = await db.branches.find_one({"organization_id": org_id}, {"_id": 0, "id": 1})
        branch_id = branch["id"]

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        yesterday = "2024-12-31"  # safely not-today

        inv_id = str(uuid.uuid4())
        voided_pay_id = str(uuid.uuid4())
        live_pay_id = str(uuid.uuid4())

        await db.invoices.insert_one({
            "id": inv_id,
            "organization_id": org_id,
            "branch_id": branch_id,
            "invoice_number": f"TEST-VOID-{inv_id[:6]}",
            "customer_id": "test-void-customer",
            "customer_name": "VOID TEST CUSTOMER",
            "order_date": yesterday,
            "grand_total": 10000.0,
            "amount_paid": 3000.0,
            "balance": 7000.0,
            "status": "partial",
            "payment_type": "credit",
            "sale_type": "regular",
            "payments": [
                {
                    "id": voided_pay_id,
                    "amount": 5000.0,
                    "method": "Cash",
                    "fund_source": "cashier",
                    "date": today,
                    "recorded_at": datetime.now(timezone.utc).isoformat(),
                    "recorded_by": "Test",
                    "voided": True,
                    "void_reason": "Modified payment",
                },
                {
                    "id": live_pay_id,
                    "amount": 3000.0,
                    "method": "Cash",
                    "fund_source": "cashier",
                    "date": today,
                    "recorded_at": datetime.now(timezone.utc).isoformat(),
                    "recorded_by": "Test",
                    "voided": False,
                },
            ],
        })
        return org_id, branch_id, inv_id, today

    ctx = _run(_seed())

    yield {
        "org_id": ctx[0],
        "branch_id": ctx[1],
        "invoice_id": ctx[2],
        "today": ctx[3],
    }

    async def _cleanup():
        db = _db()
        await db.invoices.delete_one({"id": ctx[2]})

    _run(_cleanup())


def test_close_preview_excludes_voided_ar_payment(headers, test_context):
    """The ₱5,000 voided payment must NOT appear; only the ₱3,000 live one should."""
    r = requests.get(
        f"{API}/daily-close-preview",
        headers=headers,
        params={"branch_id": test_context["branch_id"], "date": test_context["today"]},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    rows = [
        p for p in data.get("ar_payments", [])
        if p.get("invoice_id") == test_context["invoice_id"]
    ]
    assert len(rows) == 1, (
        f"Expected exactly 1 AR row (only the non-voided payment), got {len(rows)}: "
        f"{rows}"
    )
    assert rows[0]["amount_paid"] == 3000.0, f"Wrong amount: {rows[0]}"
    # The voided ₱5,000 must NOT appear anywhere in the list.
    assert all(p["amount_paid"] != 5000.0 or p["invoice_id"] != test_context["invoice_id"]
               for p in data.get("ar_payments", []))


def test_close_preview_batch_excludes_voided_ar_payment(headers, test_context):
    """Batch preview's total_ar_received must NOT include the ₱5,000 voided payment."""
    # Baseline: call with a date that has only the voided payment (and
    # possibly unrelated real ones). The test_context seed put both payments
    # on "today" — the voided one should be filtered out.
    r = requests.get(
        f"{API}/daily-close-preview/batch",
        headers=headers,
        params={
            "branch_id": test_context["branch_id"],
            "dates": test_context["today"],
        },
    )
    assert r.status_code == 200, r.text
    data = r.json()

    # Query the DB directly for the baseline: sum of non-voided payments
    # on today for the branch, excluding same-day invoices.
    from motor.motor_asyncio import AsyncIOMotorClient
    import asyncio

    async def _expected():
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        pipeline = [
            {"$match": {
                "branch_id": test_context["branch_id"],
                "status": {"$ne": "voided"},
                "order_date": {"$nin": [test_context["today"]]},
            }},
            {"$unwind": "$payments"},
            {"$match": {
                "payments.date": test_context["today"],
                "payments.voided": {"$ne": True},
            }},
            {"$group": {"_id": None, "total": {"$sum": "$payments.amount"}}},
        ]
        r = await db.invoices.aggregate(pipeline).to_list(1)
        client.close()
        return round(r[0]["total"] if r else 0, 2)

    loop = asyncio.new_event_loop()
    try:
        expected = loop.run_until_complete(_expected())
    finally:
        loop.close()

    assert data["total_ar_received"] == expected, (
        f"total_ar_received {data['total_ar_received']} != expected (non-voided) {expected}. "
        f"Voided ₱5,000 should be excluded."
    )

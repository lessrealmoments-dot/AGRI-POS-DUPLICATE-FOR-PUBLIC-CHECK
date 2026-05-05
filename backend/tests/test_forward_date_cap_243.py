"""
Iter 243 — Forward-date cap regression tests.

Guards against "forward-dated stock laundering":
  - Cashier sets order_date far in the future
  - Stock deducts from inventory today (real-time wallet)
  - Sale never appears on any Z-Report until that future date rolls around
  - Cashier can walk with stock, owner doesn't notice for weeks

The backend should reject `order_date > today+1` unconditionally, unless an
admin PIN override is attached (audited).

Also validates the legitimate path: if today is closed, +1 day is ALLOWED
(this is the "sold after mid-day close" flow — those sales legitimately
flow into tomorrow's Z-Report).
"""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
import requests
from pymongo import MongoClient

from tests._org_test_helpers import (
    API, MONGO_URL, DB_NAME,
    ensure_org_admin_token, TEST_ORG_MANAGER_PIN,
)

_db = MongoClient(MONGO_URL)[DB_NAME]


@pytest.fixture(scope="module")
def admin_ctx():
    token, _ = ensure_org_admin_token()
    headers = {"Authorization": f"Bearer {token}"}
    branches = requests.get(f"{API}/branches", headers=headers).json()
    branch_id = branches[0]["id"]
    return headers, branch_id


def _today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _server_today(headers, branch_id):
    """Get the server's timezone-aware concept of 'today' for this branch.
    Matches what sales.py uses internally via `today_local(organization_id)`.
    """
    res = requests.get(
        f"{API}/daily-close/unclosed-days",
        params={"branch_id": branch_id},
        headers=headers,
    )
    if res.status_code == 200:
        return res.json().get("today") or _today()
    return _today()


def _days_from(base_date, delta):
    return (datetime.strptime(base_date, "%Y-%m-%d") + timedelta(days=delta)).strftime("%Y-%m-%d")


def _days_from_today(delta):
    return (datetime.now(timezone.utc) + timedelta(days=delta)).strftime("%Y-%m-%d")


def _minimal_sale(branch_id, order_date):
    return {
        "branch_id": branch_id,
        "order_date": order_date,
        "invoice_date": order_date,
        "customer_name": "Walk-in",
        "payment_type": "cash",
        "payment_method": "Cash",
        "items": [{
            "product_name": "Iter243 test item", "quantity": 1, "rate": 1.0,
            "price": 1.0, "total": 1.0, "line_total": 1.0,
            "discount_type": "amount", "discount_value": 0, "discount_amount": 0,
        }],
        "subtotal": 1.0, "overall_discount": 0, "grand_total": 1.0,
        "amount_paid": 1.0, "balance": 0,
        "fund_source": "cashier", "sale_type": "walk_in",
        "mode": "quick",
        "idempotency_key": f"iter243-{uuid4().hex}",
    }


def test_reject_far_future_date(admin_ctx):
    """A sale dated a week in the future must be rejected with 403."""
    headers, branch_id = admin_ctx
    far_future = _days_from_today(7)
    res = requests.post(f"{API}/unified-sale", json=_minimal_sale(branch_id, far_future), headers=headers)
    assert res.status_code == 403, (
        f"Expected 403 for far-future date {far_future}, got {res.status_code}: {res.text}"
    )
    assert "forward-date" in res.text.lower() or "maximum allowed" in res.text.lower(), (
        f"Error message should mention the forward-date guard. Got: {res.text}"
    )
    print(f"✅ Far-future date {far_future} correctly rejected")


def test_reject_next_year_date(admin_ctx):
    """A sale dated 1 year out — the exact attack vector — must be rejected."""
    headers, branch_id = admin_ctx
    next_year = _days_from_today(365)
    res = requests.post(f"{API}/unified-sale", json=_minimal_sale(branch_id, next_year), headers=headers)
    assert res.status_code == 403, f"Expected 403 for {next_year}, got {res.status_code}"
    print(f"✅ Next-year date {next_year} correctly rejected")


def test_allow_today(admin_ctx):
    """A sale dated today should proceed (assuming today is not closed)."""
    headers, branch_id = admin_ctx
    today = _today()
    # Skip if today is already closed for this branch — test not applicable
    closed = _db.daily_closings.find_one(
        {"branch_id": branch_id, "date": today, "status": "closed"}
    )
    if closed:
        pytest.skip(f"Today ({today}) is already closed for branch {branch_id}; covered by test_allow_tomorrow_when_today_closed")
    res = requests.post(f"{API}/unified-sale", json=_minimal_sale(branch_id, today), headers=headers)
    # 200 = created, 400/403 could be for other guards (stock, etc) — the
    # point is NOT the forward-date guard
    if res.status_code in (400, 403):
        assert "forward-date" not in res.text.lower() and "maximum allowed" not in res.text.lower(), (
            f"Today should NOT trip forward-date guard. Error: {res.text}"
        )
    print(f"✅ Today ({today}) not blocked by forward-date guard — status {res.status_code}")


def test_allow_tomorrow_when_today_closed(admin_ctx):
    """When today is already closed, tomorrow is the legitimate next open
    business day and must NOT be blocked."""
    headers, branch_id = admin_ctx
    today = _server_today(headers, branch_id)
    tomorrow = _days_from(today, 1)

    # Ensure today is marked closed — synthesise a daily_closings row
    # NOTE: daily_closings is a TenantCollection — must include organization_id
    # or the backend's implicit tenant filter won't see our row. Pull org from
    # the branch record.
    branch_doc = _db.branches.find_one({"id": branch_id}, {"_id": 0, "organization_id": 1})
    org_id = branch_doc.get("organization_id") if branch_doc else None
    existing = _db.daily_closings.find_one({"branch_id": branch_id, "date": today})
    inserted = False
    if not existing:
        _db.daily_closings.insert_one({
            "id": f"iter243-close-{uuid4().hex[:8]}",
            "organization_id": org_id,
            "branch_id": branch_id,
            "date": today,
            "status": "closed",
            "closed_at": datetime.now(timezone.utc).isoformat(),
            "closed_by": "iter243-test",
            # Minimal fields — this row is purely for the forward-date-cap check
        })
        inserted = True
    elif existing.get("status") != "closed":
        _db.daily_closings.update_one(
            {"branch_id": branch_id, "date": today},
            {"$set": {"status": "closed", "_iter243_flipped": True}},
        )

    try:
        res = requests.post(
            f"{API}/unified-sale",
            json=_minimal_sale(branch_id, tomorrow),
            headers=headers,
        )
        # Must NOT fail with the forward-date message (a 403 on closed-day
        # grounds would be a different bug — not what this test covers)
        if res.status_code == 403:
            assert (
                "forward-date" not in res.text.lower()
                and "maximum allowed" not in res.text.lower()
            ), (
                f"Tomorrow ({tomorrow}) should NOT be blocked as forward-dated "
                f"when today ({today}) is closed. Got: {res.text}"
            )
        print(
            f"✅ Tomorrow ({tomorrow}) not blocked by forward-date guard "
            f"when today is closed — status {res.status_code}"
        )
    finally:
        # Cleanup
        if inserted:
            _db.daily_closings.delete_one({"branch_id": branch_id, "date": today, "status": "closed"})
        else:
            _db.daily_closings.update_one(
                {"branch_id": branch_id, "date": today, "_iter243_flipped": True},
                {"$unset": {"_iter243_flipped": ""}},
            )


def test_reject_day_after_tomorrow_even_when_today_closed(admin_ctx):
    """Cap is +1 open day. Even if today is closed, +2 must still be rejected."""
    headers, branch_id = admin_ctx
    today = _server_today(headers, branch_id)
    day_after_tomorrow = _days_from(today, 2)

    branch_doc = _db.branches.find_one({"id": branch_id}, {"_id": 0, "organization_id": 1})
    org_id = branch_doc.get("organization_id") if branch_doc else None
    existing = _db.daily_closings.find_one({"branch_id": branch_id, "date": today})
    inserted = False
    if not existing:
        _db.daily_closings.insert_one({
            "id": f"iter243-close-{uuid4().hex[:8]}",
            "organization_id": org_id,
            "branch_id": branch_id,
            "date": today,
            "status": "closed",
            "closed_at": datetime.now(timezone.utc).isoformat(),
            "closed_by": "iter243-test",
        })
        inserted = True

    try:
        res = requests.post(
            f"{API}/unified-sale",
            json=_minimal_sale(branch_id, day_after_tomorrow),
            headers=headers,
        )
        assert res.status_code == 403, (
            f"Day-after-tomorrow {day_after_tomorrow} must be rejected even when "
            f"today is closed. Got {res.status_code}: {res.text}"
        )
        assert (
            "forward-date" in res.text.lower() or "maximum allowed" in res.text.lower()
        ), f"Expected forward-date error, got: {res.text}"
        print(f"✅ Day-after-tomorrow {day_after_tomorrow} correctly rejected")
    finally:
        if inserted:
            _db.daily_closings.delete_one({"branch_id": branch_id, "date": today, "status": "closed"})

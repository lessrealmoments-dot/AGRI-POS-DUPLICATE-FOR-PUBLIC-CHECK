"""
Backend regression for /api/customers/receivables-summary
Iteration 164: verifies new field oldest_overdue_due_date is returned per row,
and existing fields are preserved.
"""
import os
import re
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://bto-phase-rollout.preview.emergentagent.com").rstrip("/")
ADMIN_EMAIL = "janmarkeahig@gmail.com"
ADMIN_PASS = "Aa@58798546521325"
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@pytest.fixture(scope="module")
def auth_token():
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"email": ADMIN_EMAIL, "password": ADMIN_PASS}, timeout=20)
    if r.status_code != 200:
        pytest.skip(f"Login failed: {r.status_code} {r.text[:200]}")
    return r.json().get("token") or r.json().get("access_token")


@pytest.fixture(scope="module")
def hdrs(auth_token):
    return {"Authorization": f"Bearer {auth_token}"}


@pytest.fixture(scope="module")
def summary(hdrs):
    r = requests.get(f"{BASE_URL}/api/customers/receivables-summary", headers=hdrs, timeout=30)
    assert r.status_code == 200, f"Endpoint failed: {r.status_code} {r.text[:200]}"
    body = r.json()
    assert isinstance(body, list), "Expected list response"
    return body


# ── Receivables summary — schema/shape ──
def test_summary_returns_list(summary):
    assert len(summary) > 0, "Expected non-empty summary list (test data has 39 customers w/ balances)"


def test_existing_fields_unchanged(summary):
    required = {"id", "name", "balance", "overdue_balance",
                "invoice_count", "overdue_count",
                "interest_rate", "grace_period", "credit_limit"}
    sample = summary[0]
    missing = required - set(sample.keys())
    assert not missing, f"Missing existing fields: {missing}. Got keys: {list(sample.keys())}"


def test_new_oldest_overdue_due_date_field_present(summary):
    for row in summary:
        assert "oldest_overdue_due_date" in row, \
            f"Row missing 'oldest_overdue_due_date': {row.get('id')}/{row.get('name')}"


def test_oldest_overdue_due_date_format(summary):
    for row in summary:
        v = row["oldest_overdue_due_date"]
        if v is None:
            continue
        assert isinstance(v, str), f"Expected str/null, got {type(v)} for {row['name']}"
        assert ISO_DATE_RE.match(v), f"Expected YYYY-MM-DD, got '{v}' for {row['name']}"


def test_null_when_no_overdue(summary):
    """Customer with no overdue balance should have oldest_overdue_due_date = null."""
    not_overdue = [r for r in summary if (r.get("overdue_balance") or 0) <= 0.005]
    if not not_overdue:
        pytest.skip("No customers without overdue in dataset; skipping null check")
    for r in not_overdue:
        assert r["oldest_overdue_due_date"] is None, \
            f"{r['name']} has no overdue_balance but oldest_overdue_due_date={r['oldest_overdue_due_date']}"


def test_present_when_overdue(summary):
    """Customer with overdue_balance > 0 should have a non-null ISO date."""
    overdue = [r for r in summary if (r.get("overdue_balance") or 0) > 0.005]
    if not overdue:
        pytest.skip("No overdue customers in dataset")
    missing = [r for r in overdue if not r["oldest_overdue_due_date"]]
    assert not missing, \
        f"Customers with overdue_balance > 0 but no oldest_overdue_due_date: " \
        f"{[(r['name'], r['overdue_balance']) for r in missing[:3]]}"


def test_oldest_due_date_in_past(summary):
    """oldest_overdue_due_date should be in the past (overdue means past today)."""
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    overdue_with_date = [r for r in summary if r["oldest_overdue_due_date"]]
    if not overdue_with_date:
        pytest.skip("No overdue rows with date")
    bad = [r for r in overdue_with_date if r["oldest_overdue_due_date"] >= today]
    assert not bad, f"oldest_overdue_due_date must be < today ({today}); got: {[(r['name'], r['oldest_overdue_due_date']) for r in bad[:3]]}"


def test_data_types(summary):
    sample = summary[0]
    assert isinstance(sample["id"], str)
    assert isinstance(sample["name"], str)
    assert isinstance(sample["balance"], (int, float))
    assert isinstance(sample["overdue_balance"], (int, float))
    assert isinstance(sample["invoice_count"], int)
    assert isinstance(sample["overdue_count"], int)

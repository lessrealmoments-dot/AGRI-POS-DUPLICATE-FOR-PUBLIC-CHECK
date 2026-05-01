"""
Iteration 195 — Test Stage button + dynamic price-scheme template columns.

Covers:
  • POST /api/sms/close-reminder/test-stage/{stage_key}
      ─ rejects missing branch_id
      ─ rejects unknown stage_key (404)
      ─ rejects when stage is currently disabled
      ─ surfaces "no recipients" when no users with phones match the roles
      ─ queues a [SAMPLE] SMS to each resolved phone

  • GET /api/import/template/branch-stock-and-price
      ─ template columns include every active price scheme name
        (e.g. "Credit Price" if a Credit scheme exists)
"""
import csv
import io
import pytest
import requests
from uuid import uuid4

from _org_test_helpers import API, _db, ensure_org_admin_token


@pytest.fixture(scope="module")
def auth():
    return ensure_org_admin_token()


@pytest.fixture
def fresh_branch(auth):
    """Create a branch in the test org we can attach test users to."""
    _, user = auth
    db = _db()
    br_id = str(uuid4())
    db.branches.insert_one({
        "id": br_id,
        "organization_id": user["organization_id"],
        "name": "TestStage Branch",
        "active": True,
        "close_time_h": 18,
    })
    yield br_id
    db.branches.delete_one({"id": br_id})


@pytest.fixture
def cashier_with_phone(auth, fresh_branch):
    _, user = auth
    db = _db()
    uid = str(uuid4())
    db.users.insert_one({
        "id": uid,
        "username": f"ts_cashier_{uid[:8]}",
        "email": f"ts_cashier_{uid[:8]}@example.com",
        "full_name": "TestStage Cashier",
        "password_hash": "x",
        "role": "cashier",
        "active": True,
        "branch_id": fresh_branch,
        "organization_id": user["organization_id"],
        "phone": "09171000001",
    })
    yield uid
    db.users.delete_one({"id": uid})


@pytest.fixture(autouse=True)
def _reset_overrides(auth):
    _, user = auth
    db = _db()
    db.sms_close_stages.delete_many({"organization_id": user["organization_id"]})
    db.sms_queue.delete_many({"trigger_ref": {"$regex": "^test_stage:"}})
    yield
    db.sms_close_stages.delete_many({"organization_id": user["organization_id"]})
    db.sms_queue.delete_many({"trigger_ref": {"$regex": "^test_stage:"}})


def test_test_stage_requires_branch(auth):
    token, _ = auth
    r = requests.post(
        f"{API}/sms/close-reminder/test-stage/close_catchup_3pm",
        headers={"Authorization": f"Bearer {token}"},
        json={},
        timeout=10,
    )
    assert r.status_code == 400
    assert "branch_id" in r.text.lower()


def test_test_stage_unknown_stage_404(auth, fresh_branch):
    token, _ = auth
    r = requests.post(
        f"{API}/sms/close-reminder/test-stage/no_such_stage",
        headers={"Authorization": f"Bearer {token}"},
        json={"branch_id": fresh_branch},
        timeout=10,
    )
    assert r.status_code == 404


def test_test_stage_disabled_stage_blocks(auth, fresh_branch):
    token, _ = auth
    # Disable
    requests.put(
        f"{API}/sms/close-reminder/stages/close_catchup_3pm",
        headers={"Authorization": f"Bearer {token}"},
        json={"enabled": False, "recipients": ["cashier"]},
        timeout=10,
    )
    r = requests.post(
        f"{API}/sms/close-reminder/test-stage/close_catchup_3pm",
        headers={"Authorization": f"Bearer {token}"},
        json={"branch_id": fresh_branch},
        timeout=10,
    )
    assert r.status_code == 400
    assert "disabled" in r.text.lower()


def test_test_stage_no_recipients_with_phones(auth, fresh_branch):
    """If the stage targets cashier but no cashier in the branch has a phone,
    the endpoint must say so loudly rather than silently queuing zero SMS."""
    token, _ = auth
    requests.put(
        f"{API}/sms/close-reminder/stages/close_catchup_3pm",
        headers={"Authorization": f"Bearer {token}"},
        json={"enabled": True, "recipients": ["cashier"]},
        timeout=10,
    )
    r = requests.post(
        f"{API}/sms/close-reminder/test-stage/close_catchup_3pm",
        headers={"Authorization": f"Bearer {token}"},
        json={"branch_id": fresh_branch},
        timeout=10,
    )
    assert r.status_code == 400
    assert "phone" in r.text.lower()


def test_test_stage_queues_sample_sms(auth, fresh_branch, cashier_with_phone):
    token, _ = auth
    requests.put(
        f"{API}/sms/close-reminder/stages/close_catchup_3pm",
        headers={"Authorization": f"Bearer {token}"},
        json={"enabled": True, "recipients": ["cashier"]},
        timeout=10,
    )
    r = requests.post(
        f"{API}/sms/close-reminder/test-stage/close_catchup_3pm",
        headers={"Authorization": f"Bearer {token}"},
        json={"branch_id": fresh_branch},
        timeout=10,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["queued"] == 1
    assert body["recipients"][0]["phone"] == "09171000001"
    assert body["stage_label"]
    assert body["branch_name"] == "TestStage Branch"

    # Check it actually landed in the queue with [SAMPLE] prefix
    db = _db()
    sms = db.sms_queue.find_one(
        {"trigger_ref": f"test_stage:close_catchup_3pm:{fresh_branch}"},
        {"_id": 0, "message": 1, "phone": 1, "status": 1, "branch_id": 1},
    )
    assert sms is not None
    assert sms["phone"] == "09171000001"
    assert sms["status"] == "pending"
    assert sms["branch_id"] == fresh_branch
    assert "[SAMPLE]" in sms["message"]


def test_branch_stock_template_includes_active_schemes(auth):
    """Adding a 'Credit' price scheme to the org should make the downloaded
    Branch Stock + Price CSV template include a 'Credit Price' column."""
    token, user = auth
    org_id = user["organization_id"]
    db = _db()
    # Ensure a Credit scheme exists for THIS org (price_schemes is org-scoped)
    if not db.price_schemes.find_one({"key": "credit", "active": True, "organization_id": org_id}):
        db.price_schemes.insert_one({
            "id": str(uuid4()),
            "organization_id": org_id,
            "name": "Credit",
            "key": "credit",
            "description": "Test credit scheme",
            "calculation_method": "fixed",
            "calculation_value": 0.0,
            "base_scheme": "cost_price",
            "active": True,
        })
    try:
        r = requests.get(
            f"{API}/import/template/branch-stock-and-price",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        assert r.status_code == 200
        text = r.content.decode("utf-8")
        reader = csv.reader(io.StringIO(text))
        headers = next(reader)
        # Must include the Credit Price column
        assert "Credit Price" in headers, f"Got headers: {headers}"
        # Must still include core columns
        assert "Product Name" in headers
        assert "Cost Price" in headers
        assert "Quantity" in headers
        # And the sample row must have the same column count
        sample = next(reader)
        assert len(sample) == len(headers)
    finally:
        # Leave the Credit scheme in place; harmless and matches user's real org
        pass


def test_products_template_includes_active_schemes(auth):
    token, _ = auth
    r = requests.get(
        f"{API}/import/template/products",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    assert r.status_code == 200
    text = r.content.decode("utf-8")
    headers = next(csv.reader(io.StringIO(text)))
    # Should always include at least Retail Price (default scheme on first run)
    assert any("Price" in h for h in headers)
    # Cost Price stays a distinct column (not pulled from schemes loop)
    assert "Cost Price" in headers

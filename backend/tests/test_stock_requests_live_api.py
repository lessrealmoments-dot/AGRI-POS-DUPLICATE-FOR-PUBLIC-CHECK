"""
Live API smoke tests for the Stock Requests feature (iter NEW).
These hit the public preview URL. They verify endpoint wiring only —
deep business logic is already covered by the BR suite
(/app/backend/tests/business_regression/test_br_stock_request_triage.py).

Pre-requisite: regression org admin must be loggable (plan != "suspended").
"""
import os
import pytest
import requests

BASE = os.environ.get("API_URL", "https://po-capital-fix.preview.emergentagent.com").rstrip("/")
EMAIL = "test_org_admin@regression.local"
PASSWORD = "RegressionPass!2026"


@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{BASE}/api/auth/login", json={"email": EMAIL, "password": PASSWORD}, timeout=15)
    if r.status_code == 403 and "suspended" in r.text.lower():
        pytest.skip("Org subscription suspended — skipping live HTTP tests")
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    return r.json()["token"]


@pytest.fixture(scope="module")
def headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


# --- list endpoint -----------------------------------------------------
def test_list_requesting(headers):
    r = requests.get(f"{BASE}/api/stock-requests?role=requesting", headers=headers, timeout=15)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "items" in body
    assert isinstance(body["items"], list)


def test_list_supplying(headers):
    r = requests.get(f"{BASE}/api/stock-requests?role=supplying", headers=headers, timeout=15)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "items" in body
    assert isinstance(body["items"], list)


# --- products-lookup ---------------------------------------------------
def test_products_lookup_requires_branch_params(headers):
    r = requests.get(f"{BASE}/api/stock-requests/products-lookup", headers=headers, timeout=15)
    # 422 -> pydantic validation; required query params
    assert r.status_code == 422, r.text


def test_products_lookup_returns_both_branch_qtys(headers):
    # Use arbitrary branch ids — endpoint should still return empty list shape
    r = requests.get(
        f"{BASE}/api/stock-requests/products-lookup",
        params={"requesting_branch_id": "br-a", "supplying_branch_id": "br-b"},
        headers=headers,
        timeout=15,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "items" in body
    assert isinstance(body["items"], list)


# --- create / send / cancel roundtrip ----------------------------------
def test_create_send_cancel_roundtrip(headers):
    payload = {
        "requesting_branch_id": "br-live-test-req",
        "supplying_branch_id": "br-live-test-sup",
        "items": [
            {"product_id": "p-live-1", "product_name": "TEST_Live Item", "qty": 2, "unit": "", "notes": ""}
        ],
        "notes": "TEST_live smoke",
    }
    r = requests.post(f"{BASE}/api/stock-requests", json=payload, headers=headers, timeout=15)
    assert r.status_code in (200, 201), r.text
    created = r.json()
    req_id = created.get("id")
    assert req_id, f"no id in create response: {created}"
    assert created.get("status") == "draft"
    assert created.get("request_number", "").startswith("SR-")

    # GET verifies persistence
    r2 = requests.get(f"{BASE}/api/stock-requests/{req_id}", headers=headers, timeout=15)
    assert r2.status_code == 200, r2.text
    assert r2.json().get("id") == req_id

    # send
    r3 = requests.post(f"{BASE}/api/stock-requests/{req_id}/send", json={}, headers=headers, timeout=15)
    assert r3.status_code == 200, r3.text
    assert r3.json().get("status") == "sent"

    # cancel (cascades draft POs only — there are none here)
    r4 = requests.post(f"{BASE}/api/stock-requests/{req_id}/cancel", json={}, headers=headers, timeout=15)
    assert r4.status_code == 200, r4.text
    assert r4.json().get("status") == "cancelled"


# --- triage requires PIN ----------------------------------------------
def test_triage_without_pin_rejected(headers):
    # create a fresh request
    payload = {
        "requesting_branch_id": "br-live-test-req",
        "supplying_branch_id": "br-live-test-sup",
        "items": [{"product_id": "p-x", "product_name": "TEST_X", "qty": 1}],
    }
    r = requests.post(f"{BASE}/api/stock-requests", json=payload, headers=headers, timeout=15)
    assert r.status_code in (200, 201), r.text
    req_id = r.json()["id"]
    requests.post(f"{BASE}/api/stock-requests/{req_id}/send", json={}, headers=headers, timeout=15)

    # triage with missing pin should fail (422 missing or 401/403 PIN invalid)
    r2 = requests.post(
        f"{BASE}/api/stock-requests/{req_id}/triage",
        json={"assignments": [{"item_id": "fake", "fulfillment_type": "cannot_fulfill"}]},
        headers=headers,
        timeout=15,
    )
    assert r2.status_code in (400, 401, 403, 422), f"unexpected status {r2.status_code}: {r2.text}"

    # cleanup
    requests.post(f"{BASE}/api/stock-requests/{req_id}/cancel", json={}, headers=headers, timeout=15)

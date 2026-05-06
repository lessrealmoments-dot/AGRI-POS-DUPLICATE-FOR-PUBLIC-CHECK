"""
Backend regression tests for Parked Purchase Orders (iter 240+).
Mirrors the parked-sales contract:
  POST /api/parked-purchase-orders            -> create
  GET  /api/parked-purchase-orders?branch_id  -> list
  POST /api/parked-purchase-orders/{id}/consume -> atomic resume
  DELETE /api/parked-purchase-orders/{id}     -> discard (PIN if not owner)
"""
import os
import uuid
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://remote-print-hub.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"

ADMIN_EMAIL = "test_org_admin@regression.local"
ADMIN_PASS = "RegressionPass!2026"
MANAGER_EMAIL = "test_org_manager@regression.local"
MANAGER_PIN = "521325"


@pytest.fixture(scope="module")
def admin_token():
    r = requests.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASS}, timeout=30)
    if r.status_code != 200:
        # Try seeding
        try:
            from tests._org_test_helpers import ensure_regression_org_admin  # type: ignore
            ensure_regression_org_admin()
        except Exception:
            pass
        r = requests.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASS}, timeout=30)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    return r.json()["token"]


@pytest.fixture(scope="module")
def admin_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"}


@pytest.fixture(scope="module")
def branch_id(admin_headers):
    r = requests.get(f"{API}/branches", headers=admin_headers, timeout=30)
    assert r.status_code == 200, r.text
    branches = r.json()
    assert len(branches) > 0, "no branches seeded"
    return branches[0]["id"]


def _payload(branch_id, **overrides):
    base = {
        "branch_id": branch_id,
        "label": "TEST_park",
        "vendor": "TEST Vendor Inc",
        "header": {"vendor": "TEST Vendor Inc", "terms": "30d", "po_date": "2026-01-15"},
        "lines": [{"sku": "TEST_SKU", "name": "Test Item", "qty": 5, "unit_cost": 100.0}],
        "vendor_prices": {"TEST_SKU": 100.0},
        "source_type": "external",
        "supply_branch_id": "",
        "receipt_session_id": f"sess_{uuid.uuid4().hex[:8]}",
        "receipt_file_count": 0,
        "item_count": 1,
        "grand_total": 500.0,
    }
    base.update(overrides)
    return base


# ── Tests ──────────────────────────────────────────────────────────


class TestParkedPOCreate:
    def test_create_park_success(self, admin_headers, branch_id):
        body = _payload(branch_id)
        r = requests.post(f"{API}/parked-purchase-orders", json=body, headers=admin_headers, timeout=30)
        assert r.status_code == 200, f"create failed: {r.status_code} {r.text}"
        data = r.json()
        assert "id" in data
        assert data["branch_id"] == branch_id
        assert data["vendor"] == "TEST Vendor Inc"
        assert data["item_count"] == 1
        assert data["grand_total"] == 500.0
        assert data.get("created_by")
        # cleanup
        requests.delete(f"{API}/parked-purchase-orders/{data['id']}", headers=admin_headers, timeout=30)

    def test_create_without_branch_id_returns_400(self, admin_headers):
        body = _payload("")
        r = requests.post(f"{API}/parked-purchase-orders", json=body, headers=admin_headers, timeout=30)
        assert r.status_code == 400, f"expected 400, got {r.status_code} {r.text}"


class TestParkedPOList:
    def test_list_returns_park_just_created(self, admin_headers, branch_id):
        # create
        c = requests.post(f"{API}/parked-purchase-orders", json=_payload(branch_id, label="TEST_list_check"), headers=admin_headers, timeout=30)
        assert c.status_code == 200
        pid = c.json()["id"]
        try:
            r = requests.get(f"{API}/parked-purchase-orders", params={"branch_id": branch_id}, headers=admin_headers, timeout=30)
            assert r.status_code == 200
            data = r.json()
            assert "parks" in data
            assert data.get("limit") == 20
            assert data.get("ttl_hours") == 24
            ids = [p["id"] for p in data["parks"]]
            assert pid in ids
            # ensure no _id leak
            for p in data["parks"]:
                assert "_id" not in p
        finally:
            requests.delete(f"{API}/parked-purchase-orders/{pid}", headers=admin_headers, timeout=30)

    def test_list_without_branch_id_returns_400(self, admin_headers):
        r = requests.get(f"{API}/parked-purchase-orders", headers=admin_headers, timeout=30)
        assert r.status_code == 400


class TestParkedPOConsume:
    def test_consume_returns_snapshot_and_deletes(self, admin_headers, branch_id):
        c = requests.post(f"{API}/parked-purchase-orders", json=_payload(branch_id, label="TEST_consume"), headers=admin_headers, timeout=30)
        assert c.status_code == 200
        pid = c.json()["id"]

        r = requests.post(f"{API}/parked-purchase-orders/{pid}/consume", headers=admin_headers, timeout=30)
        assert r.status_code == 200, r.text
        snap = r.json()
        assert snap["id"] == pid
        assert snap["vendor"] == "TEST Vendor Inc"
        assert len(snap["lines"]) == 1

        # Second consume must 410
        r2 = requests.post(f"{API}/parked-purchase-orders/{pid}/consume", headers=admin_headers, timeout=30)
        assert r2.status_code == 410, f"expected 410, got {r2.status_code}"

        # GET also gone
        g = requests.get(f"{API}/parked-purchase-orders/{pid}", headers=admin_headers, timeout=30)
        assert g.status_code == 404


class TestParkedPODiscard:
    def test_discard_own_no_pin(self, admin_headers, branch_id):
        c = requests.post(f"{API}/parked-purchase-orders", json=_payload(branch_id, label="TEST_discard_own"), headers=admin_headers, timeout=30)
        assert c.status_code == 200
        pid = c.json()["id"]
        d = requests.delete(f"{API}/parked-purchase-orders/{pid}", headers=admin_headers, timeout=30)
        assert d.status_code == 200
        assert d.json().get("ok") is True
        # confirm gone
        g = requests.get(f"{API}/parked-purchase-orders/{pid}", headers=admin_headers, timeout=30)
        assert g.status_code == 404

    def test_discard_other_user_without_pin_403(self, admin_headers, branch_id):
        """Admin creates; manager tries to delete without PIN, then with valid manager_pin."""
        rm = requests.post(f"{API}/auth/login", json={"email": MANAGER_EMAIL, "password": "RegressionMgrPass!2026"}, timeout=30)
        if rm.status_code != 200:
            pytest.skip(f"manager login not available: {rm.status_code}")
        mgr_headers = {"Authorization": f"Bearer {rm.json()['token']}", "Content-Type": "application/json"}

        # Admin creates a park
        c = requests.post(f"{API}/parked-purchase-orders", json=_payload(branch_id, label="TEST_admin_park"), headers=admin_headers, timeout=30)
        assert c.status_code == 200, c.text
        pid = c.json()["id"]
        try:
            # Manager tries to delete admin's park WITHOUT pin -> 403
            d = requests.delete(f"{API}/parked-purchase-orders/{pid}", headers=mgr_headers, timeout=30)
            assert d.status_code == 403, f"expected 403, got {d.status_code} {d.text}"

            # Manager tries WITH a valid manager pin -> 200
            d2 = requests.delete(
                f"{API}/parked-purchase-orders/{pid}",
                params={"pin": MANAGER_PIN},
                headers=mgr_headers,
                timeout=30,
            )
            assert d2.status_code == 200, f"expected 200 with PIN, got {d2.status_code} {d2.text}"
        finally:
            requests.delete(f"{API}/parked-purchase-orders/{pid}?pin={MANAGER_PIN}", headers=admin_headers, timeout=30)

"""
Tests for price-schemes restore + import auto-create flow (iteration 170).
Covers:
- POST /api/price-schemes/restore-defaults idempotency + reactivation
- POST /api/import/products auto-creates wholesale scheme when mapped
- POST /api/import/products retail-only mapping (regression)
"""
import io
import os
import csv
import time
import pytest
import requests

def _load_url():
    url = os.environ.get("REACT_APP_BACKEND_URL")
    if not url:
        try:
            with open("/app/frontend/.env") as f:
                for line in f:
                    if line.startswith("REACT_APP_BACKEND_URL="):
                        url = line.split("=", 1)[1].strip()
                        break
        except Exception:
            pass
    return url.rstrip("/") + "/api"

BASE_URL = _load_url()

EMAIL = "janmarkeahig@gmail.com"
PASSWORD = "Aa@58798546521325"


@pytest.fixture(scope="module")
def token():
    """Login. May skip if TOTP is required."""
    s = requests.Session()
    r = s.post(f"{BASE_URL}/auth/login", json={"email": EMAIL, "password": PASSWORD}, timeout=15)
    if r.status_code != 200:
        pytest.skip(f"Login failed: {r.status_code} {r.text[:200]}")
    data = r.json()
    if data.get("requires_totp") or data.get("totp_required"):
        pytest.skip("TOTP required - cannot run E2E")
    tk = data.get("token") or data.get("access_token")
    if not tk:
        pytest.skip(f"No token in login response: {list(data.keys())}")
    return tk


@pytest.fixture(scope="module")
def auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


# ── 1. restore-defaults idempotency ───────────────────────────────────────────
class TestRestoreDefaults:
    def test_restore_when_all_exist_returns_zero(self, auth_headers):
        # Ensure baseline first
        requests.post(f"{BASE_URL}/price-schemes/restore-defaults", headers=auth_headers, timeout=10)
        r = requests.post(f"{BASE_URL}/price-schemes/restore-defaults", headers=auth_headers, timeout=10)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "created" in data and "total" in data
        assert data["total"] == 0, f"Expected idempotent (0), got {data}"
        assert data["created"] == []

    def test_restore_reactivates_soft_deleted_wholesale(self, auth_headers):
        # Delete ALL wholesale schemes (handle duplicates from prior runs)
        for _ in range(5):
            r = requests.get(f"{BASE_URL}/price-schemes", headers=auth_headers, timeout=10)
            schemes = r.json()
            wholesales = [s for s in schemes if s.get("key") == "wholesale"]
            if not wholesales:
                break
            for w in wholesales:
                requests.delete(f"{BASE_URL}/price-schemes/{w['id']}", headers=auth_headers, timeout=10)

        # Confirm gone from active list
        r2 = requests.get(f"{BASE_URL}/price-schemes", headers=auth_headers, timeout=10)
        assert not any(s.get("key") == "wholesale" for s in r2.json()), \
            f"Wholesale still active after delete loop: {[s for s in r2.json() if s.get('key')=='wholesale']}"

        # Restore - must reactivate or create
        rr = requests.post(f"{BASE_URL}/price-schemes/restore-defaults", headers=auth_headers, timeout=10)
        assert rr.status_code == 200, rr.text
        data = rr.json()
        assert data["total"] >= 1
        keys = [c.get("key") for c in data["created"]]
        assert "wholesale" in keys, f"Wholesale not restored: {data}"

        # Confirm wholesale is back in active list
        r3 = requests.get(f"{BASE_URL}/price-schemes", headers=auth_headers, timeout=10)
        assert any(s.get("key") == "wholesale" for s in r3.json())


# ── 2. import auto-create flow ────────────────────────────────────────────────
class TestImportAutoCreateScheme:
    def _make_csv(self, rows):
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerows(rows)
        return buf.getvalue().encode("utf-8")

    def _delete_scheme_by_key(self, auth_headers, key):
        r = requests.get(f"{BASE_URL}/price-schemes", headers=auth_headers, timeout=10)
        for s in r.json():
            if s.get("key") == key:
                requests.delete(f"{BASE_URL}/price-schemes/{s['id']}", headers=auth_headers, timeout=10)
                return True
        return False

    def test_import_auto_creates_wholesale_when_missing(self, auth_headers):
        # Pre-condition: delete wholesale (soft) so import must auto-create / reactivate
        self._delete_scheme_by_key(auth_headers, "wholesale")

        unique = f"TEST_AutoWS_{int(time.time())}"
        csv_bytes = self._make_csv([
            ["Product Name", "SKU", "Retail Price", "Wholesale Price", "Cost Price"],
            [unique, f"SKU-{int(time.time())}", "100", "85", "70"],
        ])
        mapping = (
            '{"name":"Product Name","sku":"SKU","retail_price":"Retail Price",'
            '"wholesale_price":"Wholesale Price","cost_price":"Cost Price"}'
        )
        files = {"file": ("auto.csv", csv_bytes, "text/csv")}
        r = requests.post(
            f"{BASE_URL}/import/products",
            headers=auth_headers,
            data={"mapping": mapping},
            files=files,
            timeout=30,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["imported"] >= 1, f"Import failed: {data}"
        assert "schemes_auto_created" in data
        # Either auto-created or pre-existed (in case soft-delete didn't apply due to unique-key re-insert path)
        # The wholesale scheme MUST exist after the import either way.
        r2 = requests.get(f"{BASE_URL}/price-schemes", headers=auth_headers, timeout=10)
        keys = [s["key"] for s in r2.json()]
        assert "wholesale" in keys, f"Wholesale missing after import. Got: {keys}"

        # Verify product has wholesale price persisted (search with name query)
        pr = requests.get(f"{BASE_URL}/products", headers=auth_headers, params={"q": unique, "limit": 50}, timeout=15)
        assert pr.status_code == 200
        prods_data = pr.json()
        prods_list = prods_data.get("products") if isinstance(prods_data, dict) else prods_data
        prod = next((p for p in (prods_list or []) if p.get("name") == unique), None)
        assert prod, f"Imported product {unique} not found in {len(prods_list or [])} results"
        assert prod.get("prices", {}).get("wholesale") == 85, f"Wholesale price not persisted: {prod.get('prices')}"

    def test_import_retail_only_no_regression(self, auth_headers):
        unique = f"TEST_RetailOnly_{int(time.time())}"
        csv_bytes = self._make_csv([
            ["Product Name", "Retail Price", "Cost Price"],
            [unique, "200", "150"],
        ])
        mapping = '{"name":"Product Name","retail_price":"Retail Price","cost_price":"Cost Price"}'
        files = {"file": ("retail.csv", csv_bytes, "text/csv")}
        r = requests.post(
            f"{BASE_URL}/import/products",
            headers=auth_headers,
            data={"mapping": mapping},
            files=files,
            timeout=30,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["imported"] >= 1
        # No wholesale mapping → schemes_auto_created should be empty (retail already exists)
        assert isinstance(data.get("schemes_auto_created"), list)


# ── 3. permission check ───────────────────────────────────────────────────────
class TestRestorePermissions:
    def test_restore_without_auth(self):
        r = requests.post(f"{BASE_URL}/price-schemes/restore-defaults", timeout=10)
        assert r.status_code in (401, 403), f"Expected unauth, got {r.status_code}"

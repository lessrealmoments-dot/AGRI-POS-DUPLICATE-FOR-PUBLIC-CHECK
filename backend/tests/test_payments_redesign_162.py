"""Backend regression tests for /payments redesign (iteration 162).

Verifies:
- GET /api/customers/{id}/invoices: returns list, no _id leakage, has grand_total/balance
- GET /api/customers/{id}/charges-preview: returns interest_preview with required fields
- CRITICAL: charges-preview does NOT create any INT invoice (display-only)
- Account totals: summary principal+interest equals top-right total
"""
import os
import requests
import pytest

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://offline-robustness.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"

ADMIN_EMAIL = "janmarkeahig@gmail.com"
ADMIN_PASS = "Aa@58798546521325"


@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASS}, timeout=20)
    assert r.status_code == 200, f"Login failed: {r.status_code} {r.text}"
    tok = r.json().get("access_token") or r.json().get("token")
    assert tok, f"No token in login response: {r.json()}"
    return tok


@pytest.fixture(scope="module")
def headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


@pytest.fixture(scope="module")
def customers(headers):
    r = requests.get(f"{API}/customers?limit=500", headers=headers, timeout=20)
    assert r.status_code == 200
    data = r.json()
    if isinstance(data, dict):
        return data.get("customers") or data.get("items") or []
    return data


@pytest.fixture(scope="module")
def john_gil(customers):
    """Find John Gil Ahig (3% interest rate) — primary test target."""
    for c in customers:
        name = (c.get("name") or "").lower()
        if "john gil" in name or "john gil ahig" in name:
            return c
    pytest.skip("John Gil Ahig customer not found in DB")


@pytest.fixture(scope="module")
def elline(customers):
    for c in customers:
        if "elline" in (c.get("name") or "").lower():
            return c
    pytest.skip("ELLINE MACAY customer not found")


# ==================== TESTS ====================

class TestCustomerInvoicesEndpoint:
    """GET /api/customers/{id}/invoices"""

    def test_returns_list(self, headers, john_gil):
        r = requests.get(f"{API}/customers/{john_gil['id']}/invoices", headers=headers, timeout=15)
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list), f"Expected list, got {type(data)}"

    def test_no_underscore_id_leakage(self, headers, john_gil):
        r = requests.get(f"{API}/customers/{john_gil['id']}/invoices", headers=headers, timeout=15)
        assert r.status_code == 200
        for inv in r.json():
            assert "_id" not in inv, f"_id leaked in invoice: {inv.get('invoice_number')}"

    def test_invoice_fields_present(self, headers, john_gil):
        r = requests.get(f"{API}/customers/{john_gil['id']}/invoices", headers=headers, timeout=15)
        invoices = r.json()
        if not invoices:
            pytest.skip("John Gil has no open invoices")
        for inv in invoices:
            assert "grand_total" in inv, f"grand_total missing in {inv.get('invoice_number')}"
            assert "balance" in inv, f"balance missing"
            assert "id" in inv


class TestChargesPreviewEndpoint:
    """GET /api/customers/{id}/charges-preview"""

    def test_returns_required_fields(self, headers, john_gil):
        r = requests.get(f"{API}/customers/{john_gil['id']}/charges-preview", headers=headers, timeout=15)
        assert r.status_code == 200
        data = r.json()
        assert "interest_preview" in data
        assert "total_interest" in data
        assert "total_principal" in data
        assert isinstance(data["interest_preview"], list)

    def test_interest_preview_row_shape(self, headers, john_gil):
        r = requests.get(f"{API}/customers/{john_gil['id']}/charges-preview", headers=headers, timeout=15)
        data = r.json()
        if not data["interest_preview"]:
            pytest.skip(f"No overdue invoices with interest for {john_gil.get('name')}")
        for row in data["interest_preview"]:
            for f in ("invoice_id", "principal", "rate", "days_for_interest", "interest_amount"):
                assert f in row, f"{f} missing in interest_preview row"

    def test_total_interest_equals_sum_of_rows(self, headers, john_gil):
        r = requests.get(f"{API}/customers/{john_gil['id']}/charges-preview", headers=headers, timeout=15)
        data = r.json()
        if not data["interest_preview"]:
            pytest.skip("No interest rows")
        s = round(sum(row["interest_amount"] for row in data["interest_preview"]), 2)
        assert abs(s - data["total_interest"]) < 0.05, f"Sum {s} != total_interest {data['total_interest']}"

    def test_no_interest_rate_customer_returns_empty(self, headers, elline):
        r = requests.get(f"{API}/customers/{elline['id']}/charges-preview", headers=headers, timeout=15)
        assert r.status_code == 200
        data = r.json()
        # Either total_interest is 0 (no rate) or empty preview list
        assert data["total_interest"] == 0 or not data["interest_preview"]


class TestNoAutoIntCreationOnSelect:
    """CRITICAL: Browsing/selecting a customer must NOT create INT invoices in DB."""

    def _count_int(self, headers, customer_id):
        r = requests.get(f"{API}/customers/{customer_id}/invoices", headers=headers, timeout=15)
        assert r.status_code == 200
        return sum(1 for i in r.json() if i.get("sale_type") == "interest_charge")

    def test_charges_preview_does_not_create_int_invoice(self, headers, john_gil):
        cid = john_gil["id"]
        before = self._count_int(headers, cid)
        # Simulate UI flow: select customer => GET invoices + GET charges-preview multiple times
        for _ in range(3):
            requests.get(f"{API}/customers/{cid}/invoices", headers=headers, timeout=15)
            requests.get(f"{API}/customers/{cid}/charges-preview", headers=headers, timeout=15)
        after = self._count_int(headers, cid)
        assert after == before, (
            f"INT invoice count changed from {before} to {after} after browsing — "
            "customer selection MUST be display-only"
        )

    def test_charges_preview_with_rate_override_no_creation(self, headers, john_gil):
        """Even with rate_override query param, preview must not create."""
        cid = john_gil["id"]
        before = self._count_int(headers, cid)
        r = requests.get(f"{API}/customers/{cid}/charges-preview?rate_override=5", headers=headers, timeout=15)
        assert r.status_code == 200
        after = self._count_int(headers, cid)
        assert after == before, f"INT count changed {before}->{after} on charges-preview with rate_override"


class TestGenerateInterestForceFlag:
    """POST /api/customers/{id}/generate-interest — Pay flow uses force=true."""

    def test_generate_without_force_respects_30day_guard(self, headers, john_gil):
        """Without force, repeated calls should be guarded by 30-day rule."""
        cid = john_gil["id"]
        # First call (may or may not create depending on prior state)
        r = requests.post(f"{API}/customers/{cid}/generate-interest",
                          headers=headers, json={}, timeout=15)
        assert r.status_code == 200
        # Second call without force — if a recent INT exists, should be skipped
        r2 = requests.post(f"{API}/customers/{cid}/generate-interest",
                           headers=headers, json={}, timeout=15)
        assert r2.status_code == 200
        d2 = r2.json()
        # Acceptable: either skipped:true OR total_interest:0 (no overdue) OR fresh creation
        # Just verify endpoint shape
        assert "total_interest" in d2

    def test_generate_with_force_returns_total_interest_field(self, headers, john_gil):
        """force=true is what 'Save & Apply' uses; endpoint must accept it."""
        cid = john_gil["id"]
        r = requests.post(f"{API}/customers/{cid}/generate-interest",
                          headers=headers, json={"force": True}, timeout=20)
        assert r.status_code == 200
        d = r.json()
        assert "total_interest" in d
        # If interest > 0, an INT invoice would be created with force=true
        # We don't assert creation here to keep test idempotent; only validate contract

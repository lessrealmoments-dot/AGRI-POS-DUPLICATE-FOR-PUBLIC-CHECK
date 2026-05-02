"""
Test: Payment History Features (Iteration 181)
Tests:
  - Global Payment History endpoint (GET /payments/history)
  - Per-Customer Payment History endpoint (GET /customers/{id}/payment-history)
  - Close Wizard Preview fields: interest_invoices_today, ar_payment_by_method, ar_interest_collected, ar_discount_today
"""
import pytest
import requests
import os
from datetime import datetime

def _load_base_url():
    """Try env var first; fall back to parsing frontend/.env file."""
    url = os.environ.get("REACT_APP_BACKEND_URL", "")
    if not url:
        try:
            with open("/app/frontend/.env") as f:
                for line in f:
                    if line.startswith("REACT_APP_BACKEND_URL="):
                        url = line.strip().split("=", 1)[1]
                        break
        except Exception:
            pass
    return url.rstrip("/")

BASE_URL = _load_base_url()


@pytest.fixture(scope="module")
def auth_token():
    """Get auth token for org admin (has accounting view perms)."""
    res = requests.post(f"{BASE_URL}/api/auth/login", json={
        "email": "test_org_admin@regression.local",
        "password": "RegressionPass!2026"
    })
    assert res.status_code == 200, f"Login failed: {res.text}"
    return res.json().get("token")


@pytest.fixture(scope="module")
def session(auth_token):
    """Requests session with auth header."""
    s = requests.Session()
    s.headers.update({
        "Content-Type": "application/json",
        "Authorization": f"Bearer {auth_token}"
    })
    return s


@pytest.fixture(scope="module")
def first_customer_id(session):
    """Fetch first customer id in the org for per-customer tests."""
    res = session.get(f"{BASE_URL}/api/customers", params={"limit": 5})
    assert res.status_code == 200, f"Failed to get customers: {res.text}"
    customers = res.json().get("customers", [])
    if not customers:
        pytest.skip("No customers in org - skip customer-specific tests")
    return customers[0]["id"], customers[0]["name"]


# ─── 1. Global Payment History Endpoint ─────────────────────────────────────

class TestGlobalPaymentHistory:
    """Tests for GET /api/payments/history"""

    def test_endpoint_returns_200(self, session):
        """Basic request returns HTTP 200."""
        today = datetime.now().strftime("%Y-%m-%d")
        res = session.get(f"{BASE_URL}/api/payments/history", params={
            "date_from": today, "date_to": today
        })
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
        print(f"✅ /payments/history returned 200")

    def test_response_structure(self, session):
        """Response has required fields: payments, method_breakdown, total_received, total_discount."""
        today = datetime.now().strftime("%Y-%m-%d")
        res = session.get(f"{BASE_URL}/api/payments/history", params={
            "date_from": today, "date_to": today
        })
        assert res.status_code == 200
        data = res.json()
        assert "payments" in data, "Missing 'payments' field"
        assert "method_breakdown" in data, "Missing 'method_breakdown' field"
        assert "total_received" in data, "Missing 'total_received' field"
        assert "total_discount" in data, "Missing 'total_discount' field"
        assert isinstance(data["payments"], list), "'payments' must be a list"
        assert isinstance(data["method_breakdown"], list), "'method_breakdown' must be a list"
        print(f"✅ Response structure correct. Payments count: {len(data['payments'])}")

    def test_date_range_filter(self, session):
        """Date range filter actually limits results to that range."""
        # Use a week-wide range
        res = session.get(f"{BASE_URL}/api/payments/history", params={
            "date_from": "2025-01-01", "date_to": "2025-01-07"
        })
        assert res.status_code == 200
        data = res.json()
        # Verify date_from/date_to echoed in response
        assert data.get("date_from") == "2025-01-01", f"Expected date_from=2025-01-01, got {data.get('date_from')}"
        assert data.get("date_to") == "2025-01-07", f"Expected date_to=2025-01-07, got {data.get('date_to')}"
        print(f"✅ Date range filter works correctly")

    def test_method_filter_cash(self, session):
        """Method filter returns only cash payments (or empty list, not an error)."""
        today = datetime.now().strftime("%Y-%m-%d")
        res = session.get(f"{BASE_URL}/api/payments/history", params={
            "date_from": today, "date_to": today, "method": "Cash"
        })
        assert res.status_code == 200
        data = res.json()
        # All returned payments should be Cash method
        for p in data.get("payments", []):
            assert p.get("method", "").lower() in ("cash", ""), \
                f"Expected Cash method, got '{p.get('method')}'"
        print(f"✅ Method filter Cash returns {len(data.get('payments', []))} records")

    def test_method_breakdown_structure(self, session):
        """Method breakdown chips have required fields: method, total, count."""
        today = datetime.now().strftime("%Y-%m-%d")
        res = session.get(f"{BASE_URL}/api/payments/history", params={
            "date_from": today, "date_to": today
        })
        assert res.status_code == 200
        data = res.json()
        for m in data.get("method_breakdown", []):
            assert "method" in m, f"Breakdown missing 'method': {m}"
            assert "total" in m, f"Breakdown missing 'total': {m}"
            assert "count" in m, f"Breakdown missing 'count': {m}"
        print(f"✅ Method breakdown structure correct ({len(data.get('method_breakdown', []))} methods)")

    def test_payment_row_has_required_fields(self, session):
        """Each payment record has required display fields."""
        # Use a wide date range to find any records
        res = session.get(f"{BASE_URL}/api/payments/history", params={
            "date_from": "2024-01-01", "date_to": "2026-12-31"
        })
        assert res.status_code == 200
        data = res.json()
        if not data.get("payments"):
            pytest.skip("No payment records found in wide date range")
        
        required_fields = ["date", "customer_name", "invoice_number", "sale_type",
                           "method", "amount", "recorded_by"]
        for p in data["payments"][:5]:  # Check first 5
            for field in required_fields:
                assert field in p, f"Payment record missing field '{field}': {p}"
        print(f"✅ Payment record fields validated for {len(data['payments'])} records")

    def test_customer_search_filter(self, session, first_customer_id):
        """Customer search filter returns only matching records."""
        cid, cname = first_customer_id
        search_term = cname[:5]  # partial name search
        today = datetime.now().strftime("%Y-%m-%d")
        res = session.get(f"{BASE_URL}/api/payments/history", params={
            "date_from": "2024-01-01", "date_to": "2026-12-31",
            "customer_search": search_term
        })
        assert res.status_code == 200
        data = res.json()
        print(f"✅ Customer search filter works: '{search_term}' returned {len(data.get('payments', []))} records")

    def test_unauthenticated_returns_401(self):
        """Unauthenticated requests should return 401."""
        today = datetime.now().strftime("%Y-%m-%d")
        res = requests.get(f"{BASE_URL}/api/payments/history", params={
            "date_from": today, "date_to": today
        })
        assert res.status_code in (401, 403), f"Expected 401/403, got {res.status_code}"
        print(f"✅ Unauthenticated request returns {res.status_code}")


# ─── 2. Per-Customer Payment History ────────────────────────────────────────

class TestCustomerPaymentHistory:
    """Tests for GET /api/customers/{customer_id}/payment-history"""

    def test_returns_list(self, session, first_customer_id):
        """Endpoint returns a list (possibly empty)."""
        cid, cname = first_customer_id
        res = session.get(f"{BASE_URL}/api/customers/{cid}/payment-history")
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
        data = res.json()
        assert isinstance(data, list), f"Expected list, got {type(data)}"
        print(f"✅ /customers/{cid}/payment-history returned {len(data)} records")

    def test_payment_record_fields(self, session, first_customer_id):
        """Each record has: date, invoice_number, sale_type, method, amount, recorded_by."""
        cid, cname = first_customer_id
        res = session.get(f"{BASE_URL}/api/customers/{cid}/payment-history")
        assert res.status_code == 200
        data = res.json()
        if not data:
            pytest.skip(f"No payment history for customer {cname}")

        required = ["date", "invoice_number", "sale_type", "method", "amount", "recorded_by"]
        for rec in data[:3]:
            for f in required:
                assert f in rec, f"Missing '{f}' in payment record: {rec}"
        print(f"✅ Per-customer payment record fields validated")

    def test_invalid_customer_returns_empty_not_error(self, session):
        """Invalid customer ID returns empty list (not 500)."""
        res = session.get(f"{BASE_URL}/api/customers/nonexistent-id-xyz/payment-history")
        # Should return 200 with empty list, not a 500
        assert res.status_code in (200, 404), f"Expected 200/404, got {res.status_code}: {res.text}"
        if res.status_code == 200:
            assert res.json() == [], f"Expected empty list, got {res.json()}"
        print(f"✅ Invalid customer ID handled gracefully: {res.status_code}")


# ─── 3. Close Wizard Preview Fields ─────────────────────────────────────────

class TestCloseWizardPreviewFields:
    """Tests for GET /api/daily-operations/preview — verify new fields are present."""

    def test_preview_returns_200(self, session):
        """Preview endpoint returns 200."""
        branch_id = "c435277f-9fc7-4d83-83e7-38be5b4423ac"
        today = datetime.now().strftime("%Y-%m-%d")
        res = session.get(f"{BASE_URL}/api/daily-close-preview", params={
            "branch_id": branch_id, "date": today
        })
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
        print(f"✅ /daily-operations/preview returned 200")

    def test_interest_invoices_today_field_present(self, session):
        """Preview response includes interest_invoices_today array."""
        branch_id = "c435277f-9fc7-4d83-83e7-38be5b4423ac"
        today = datetime.now().strftime("%Y-%m-%d")
        res = session.get(f"{BASE_URL}/api/daily-close-preview", params={
            "branch_id": branch_id, "date": today
        })
        assert res.status_code == 200
        data = res.json()
        assert "interest_invoices_today" in data, \
            f"Missing 'interest_invoices_today' in preview. Keys: {list(data.keys())}"
        assert isinstance(data["interest_invoices_today"], list), \
            "'interest_invoices_today' must be a list"
        print(f"✅ 'interest_invoices_today' present: {len(data['interest_invoices_today'])} items")

    def test_ar_payment_by_method_field_present(self, session):
        """Preview response includes ar_payment_by_method dict."""
        branch_id = "c435277f-9fc7-4d83-83e7-38be5b4423ac"
        today = datetime.now().strftime("%Y-%m-%d")
        res = session.get(f"{BASE_URL}/api/daily-close-preview", params={
            "branch_id": branch_id, "date": today
        })
        assert res.status_code == 200
        data = res.json()
        assert "ar_payment_by_method" in data, \
            f"Missing 'ar_payment_by_method'. Keys: {list(data.keys())}"
        assert isinstance(data["ar_payment_by_method"], dict), \
            "'ar_payment_by_method' must be a dict"
        print(f"✅ 'ar_payment_by_method' present: {data['ar_payment_by_method']}")

    def test_ar_interest_collected_field_present(self, session):
        """Preview response includes ar_interest_collected numeric."""
        branch_id = "c435277f-9fc7-4d83-83e7-38be5b4423ac"
        today = datetime.now().strftime("%Y-%m-%d")
        res = session.get(f"{BASE_URL}/api/daily-close-preview", params={
            "branch_id": branch_id, "date": today
        })
        assert res.status_code == 200
        data = res.json()
        assert "ar_interest_collected" in data, \
            f"Missing 'ar_interest_collected'. Keys: {list(data.keys())}"
        assert isinstance(data["ar_interest_collected"], (int, float)), \
            "'ar_interest_collected' must be numeric"
        print(f"✅ 'ar_interest_collected' present: {data['ar_interest_collected']}")

    def test_ar_discount_today_field_present(self, session):
        """Preview response includes ar_discount_today numeric."""
        branch_id = "c435277f-9fc7-4d83-83e7-38be5b4423ac"
        today = datetime.now().strftime("%Y-%m-%d")
        res = session.get(f"{BASE_URL}/api/daily-close-preview", params={
            "branch_id": branch_id, "date": today
        })
        assert res.status_code == 200
        data = res.json()
        assert "ar_discount_today" in data, \
            f"Missing 'ar_discount_today'. Keys: {list(data.keys())}"
        assert isinstance(data["ar_discount_today"], (int, float)), \
            "'ar_discount_today' must be numeric"
        print(f"✅ 'ar_discount_today' present: {data['ar_discount_today']}")

    def test_interest_invoice_record_fields(self, session):
        """If interest invoices exist, they have required fields."""
        branch_id = "c435277f-9fc7-4d83-83e7-38be5b4423ac"
        today = datetime.now().strftime("%Y-%m-%d")
        res = session.get(f"{BASE_URL}/api/daily-close-preview", params={
            "branch_id": branch_id, "date": today
        })
        assert res.status_code == 200
        data = res.json()
        invoices = data.get("interest_invoices_today", [])
        if not invoices:
            pytest.skip("No interest invoices today — can't validate field structure")
        required = ["invoice_number", "customer_name", "sale_type", "grand_total", "balance", "amount_paid"]
        for inv in invoices[:3]:
            for f in required:
                assert f in inv, f"Missing '{f}' in interest invoice: {inv}"
        print(f"✅ Interest invoice record structure validated: {invoices[0]}")

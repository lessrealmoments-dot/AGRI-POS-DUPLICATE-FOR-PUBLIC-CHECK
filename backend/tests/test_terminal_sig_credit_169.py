"""
Iteration 169: Terminal Signature Dialog fix + Inline Credit Type Selection tests.
Tests:
1. POST /api/signatures/session without auth returns 401/403
2. POST /api/signatures/session with valid auth but missing credit_context returns 400
3. POST /api/signatures/session with valid auth and credit_context returns 200 (session created)
4. GET /api/signatures/status/{token} with valid auth and valid token returns session
"""

import pytest
import requests
import os

# Load from frontend .env if not set in environment
_raw = os.environ.get('REACT_APP_BACKEND_URL', '')
if not _raw:
    try:
        with open('/app/frontend/.env') as f:
            for line in f:
                line = line.strip()
                if line.startswith('REACT_APP_BACKEND_URL='):
                    _raw = line.split('=', 1)[1].strip()
                    break
    except Exception:
        pass

BASE_URL = _raw.rstrip('/')


@pytest.fixture(scope="module")
def api_client():
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    return session


@pytest.fixture(scope="module")
def auth_token(api_client):
    """Authenticate as super admin"""
    response = api_client.post(f"{BASE_URL}/api/auth/login", json={
        "email": "janmarkeahig@gmail.com",
        "password": "Aa@58798546521325"
    })
    if response.status_code == 200:
        data = response.json()
        return data.get("access_token") or data.get("token")
    pytest.skip(f"Authentication failed: {response.status_code} {response.text}")


@pytest.fixture(scope="module")
def authenticated_client(api_client, auth_token):
    api_client.headers.update({"Authorization": f"Bearer {auth_token}"})
    return api_client


# ─── Backend auth check ───

class TestSignatureSessionAuth:
    """POST /api/signatures/session authentication checks"""

    def test_session_without_auth_returns_401_or_403(self, api_client):
        """Backend POST /api/signatures/session without auth must return 401 or 403"""
        # Remove auth header if present
        headers = {"Content-Type": "application/json"}
        response = requests.post(
            f"{BASE_URL}/api/signatures/session",
            json={
                "linked_record_type": "invoice",
                "linked_record_id": "test-id",
                "branch_id": "test-branch",
                "credit_context": {"customer_name": "Test", "amount": 100}
            },
            headers=headers
        )
        print(f"No-auth response: {response.status_code} {response.text[:200]}")
        assert response.status_code in (401, 403), (
            f"Expected 401 or 403 for unauthenticated request, got {response.status_code}"
        )
        data = response.json()
        # Check for "Not authenticated" message
        detail = data.get("detail", "")
        print(f"Error detail: {detail}")
        assert detail, "Response should contain a detail/error message"

    def test_session_without_auth_response_detail(self, api_client):
        """Confirm 'Not authenticated' message in unauthenticated response"""
        headers = {"Content-Type": "application/json"}
        response = requests.post(
            f"{BASE_URL}/api/signatures/session",
            json={"credit_context": {"customer_name": "Test", "amount": 100}},
            headers=headers
        )
        print(f"Status: {response.status_code}")
        assert response.status_code in (401, 403)
        data = response.json()
        detail = str(data.get("detail", "")).lower()
        print(f"Detail: {detail}")
        # Accept either 'not authenticated' or 'not enough permissions' or similar
        assert any(kw in detail for kw in ["not authenticated", "not enough", "unauthorized", "credentials", "permission"]), (
            f"Unexpected error detail: {detail}"
        )


class TestSignatureSessionValidation:
    """POST /api/signatures/session validation when authenticated"""

    def test_session_missing_credit_context_returns_400(self, authenticated_client):
        """Authenticated request without credit_context must return 400"""
        response = authenticated_client.post(
            f"{BASE_URL}/api/signatures/session",
            json={
                "linked_record_type": "invoice",
                "linked_record_id": "test-id",
                "branch_id": "c435277f-9fc7-4d83-83e7-38be5b4423ac",
                # Missing credit_context
            }
        )
        print(f"Missing credit_context response: {response.status_code} {response.text[:200]}")
        assert response.status_code == 400, (
            f"Expected 400 for missing credit_context, got {response.status_code}"
        )
        data = response.json()
        assert "credit_context" in str(data.get("detail", "")).lower(), (
            f"Error should mention credit_context, got: {data}"
        )

    def test_session_empty_credit_context_returns_400(self, authenticated_client):
        """Authenticated request with empty credit_context dict must return 400"""
        response = authenticated_client.post(
            f"{BASE_URL}/api/signatures/session",
            json={
                "linked_record_type": "invoice",
                "linked_record_id": "test-id",
                "branch_id": "c435277f-9fc7-4d83-83e7-38be5b4423ac",
                "credit_context": {}  # Empty
            }
        )
        print(f"Empty credit_context response: {response.status_code} {response.text[:200]}")
        assert response.status_code == 400, (
            f"Expected 400 for empty credit_context, got {response.status_code}"
        )

    def test_session_with_valid_payload_returns_200(self, authenticated_client):
        """Authenticated request with valid credit_context returns 200 and token"""
        response = authenticated_client.post(
            f"{BASE_URL}/api/signatures/session",
            json={
                "linked_record_type": "invoice",
                "linked_record_id": "test-invoice-169",
                "branch_id": "c435277f-9fc7-4d83-83e7-38be5b4423ac",
                "credit_context": {
                    "customer_name": "TEST_SignatureCustomer",
                    "amount": 500.00,
                    "credit_type": "by_term",
                    "date": "2026-02-15",
                    "branch_name": "Branch 1",
                    "description": "Test session iteration 169",
                    "invoice_number": "TEST-169",
                    "items": [
                        {"product_name": "Rice", "quantity": 10, "unit": "kg", "rate": 50, "total": 500}
                    ],
                    "subtotal": 500.0,
                    "discount": 0,
                    "partial_paid": 0
                }
            }
        )
        print(f"Valid session response: {response.status_code} {response.text[:300]}")
        assert response.status_code == 200, f"Expected 200 for valid session, got {response.status_code}: {response.text}"
        data = response.json()
        assert "token" in data, "Response should have 'token' field"
        assert "id" in data, "Response should have 'id' field"
        assert "signing_url" in data, "Response should have 'signing_url' field"
        assert data["status"] == "pending", f"Session status should be 'pending', got {data.get('status')}"
        assert data["signing_url"].startswith("/sign/"), f"signing_url should start with /sign/, got {data['signing_url']}"
        print(f"Session created: token={data['token'][:8]}... id={data['id']}")
        return data  # Will be used by subsequent tests

    def test_session_status_endpoint(self, authenticated_client):
        """GET /api/signatures/status/{token} returns session status"""
        # First create a session
        create_response = authenticated_client.post(
            f"{BASE_URL}/api/signatures/session",
            json={
                "linked_record_type": "invoice",
                "linked_record_id": "test-status-169",
                "branch_id": "c435277f-9fc7-4d83-83e7-38be5b4423ac",
                "credit_context": {
                    "customer_name": "TEST_StatusCustomer",
                    "amount": 250.00,
                    "credit_type": "charged_to_crop",
                    "date": "2026-02-15",
                    "branch_name": "Test Branch",
                    "description": "Status test session",
                    "invoice_number": "TEST-STATUS-169",
                    "items": [],
                    "subtotal": 250.0,
                    "discount": 0,
                    "partial_paid": 0
                }
            }
        )
        assert create_response.status_code == 200
        token = create_response.json()["token"]

        # Now poll status
        status_response = authenticated_client.get(
            f"{BASE_URL}/api/signatures/status/{token}"
        )
        print(f"Status response: {status_response.status_code} {status_response.text[:200]}")
        assert status_response.status_code == 200, f"Expected 200 for status poll, got {status_response.status_code}"
        status_data = status_response.json()
        assert status_data["status"] == "pending", f"New session should be 'pending', got {status_data.get('status')}"
        assert "expired" in status_data, "Status response should include 'expired' field"
        assert status_data["expired"] is False, "Freshly created session should not be expired"
        print(f"Session status: {status_data['status']}, expired: {status_data['expired']}")

    def test_session_status_without_auth_returns_401_or_403(self):
        """GET /api/signatures/status/{token} without auth returns 401/403"""
        response = requests.get(
            f"{BASE_URL}/api/signatures/status/fake-token-test",
            headers={"Content-Type": "application/json"}
        )
        print(f"Unauthenticated status response: {response.status_code}")
        assert response.status_code in (401, 403), (
            f"Expected 401/403 for unauthenticated status poll, got {response.status_code}"
        )

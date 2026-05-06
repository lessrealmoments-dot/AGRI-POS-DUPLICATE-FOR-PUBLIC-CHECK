"""
Test PIN Verification Fix - Iteration 90
Tests the centralized _resolve_pin function that checks all 4 PIN types:
1. Owner PIN (system_settings.admin_pin - hashed)
2. Manager/Admin PIN (user.manager_pin - plain text)
3. TOTP (6-digit code)
4. Auditor PIN (user.auditor_pin)
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', 'https://remote-print-hub.preview.emergentagent.com')

# Test credentials
SUPER_ADMIN_EMAIL = "janmarkeahig@gmail.com"
SUPER_ADMIN_PASSWORD = "Aa@58798546521325"
MANAGER_PIN = "521325"
OWNER_PIN = "1234"


@pytest.fixture(scope="module")
def auth_token():
    """Get authentication token for super admin."""
    response = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": SUPER_ADMIN_EMAIL, "password": SUPER_ADMIN_PASSWORD}
    )
    assert response.status_code == 200, f"Login failed: {response.text}"
    return response.json()["token"]


@pytest.fixture(scope="module")
def auth_headers(auth_token):
    """Return headers with authorization token."""
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {auth_token}"
    }


class TestPinVerification:
    """Test centralized PIN verification via /auth/verify-manager-pin endpoint."""
    
    def test_owner_pin_accepted(self, auth_headers):
        """Owner PIN (1234) stored in system_settings.admin_pin should be accepted."""
        response = requests.post(
            f"{BASE_URL}/api/auth/verify-manager-pin",
            headers=auth_headers,
            json={"pin": OWNER_PIN}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is True
        assert data["manager_id"] == "system_admin"
        assert data["manager_name"] == "Admin"
        assert data["role"] == "admin_pin"
    
    def test_manager_pin_accepted(self, auth_headers):
        """Manager PIN (521325) stored on user document should be accepted."""
        response = requests.post(
            f"{BASE_URL}/api/auth/verify-manager-pin",
            headers=auth_headers,
            json={"pin": MANAGER_PIN}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is True
        assert data["manager_id"] is not None
        assert data["manager_name"] is not None
        assert data["role"] == "manager_pin"
    
    def test_invalid_pin_rejected(self, auth_headers):
        """Invalid PIN should be rejected."""
        response = requests.post(
            f"{BASE_URL}/api/auth/verify-manager-pin",
            headers=auth_headers,
            json={"pin": "999999"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is False
    
    def test_empty_pin_rejected(self, auth_headers):
        """Empty PIN should be rejected with 400 status."""
        response = requests.post(
            f"{BASE_URL}/api/auth/verify-manager-pin",
            headers=auth_headers,
            json={"pin": ""}
        )
        assert response.status_code == 400
        data = response.json()
        assert "detail" in data
    
    def test_pin_with_context_accepted(self, auth_headers):
        """PIN with context (for notification) should be accepted."""
        response = requests.post(
            f"{BASE_URL}/api/auth/verify-manager-pin",
            headers=auth_headers,
            json={
                "pin": OWNER_PIN,
                "context": {
                    "type": "credit_sale",
                    "description": "Test credit sale approval"
                }
            }
        )
        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is True


class TestVerifyTransactionEndpoint:
    """Test /verify endpoints that use _resolve_pin for transaction verification."""
    
    def test_public_verify_endpoint_structure(self, auth_headers):
        """Verify the public transaction verify endpoint exists."""
        # This endpoint doesn't require auth but validates PIN
        # We test with an invalid doc to verify endpoint is accessible
        response = requests.post(
            f"{BASE_URL}/api/verify/public/purchase_order/nonexistent-id",
            json={"pin": OWNER_PIN}
        )
        # Should return 404 for nonexistent doc, not 500
        assert response.status_code == 404


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

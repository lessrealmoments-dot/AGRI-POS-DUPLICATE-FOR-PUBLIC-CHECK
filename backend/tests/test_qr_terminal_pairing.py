"""
QR Terminal Pairing Tests - Iteration 114
==========================================
Tests for the new QR code terminal login feature:
1. POST /api/terminal/initiate-qr-pairing - generates QR pair token (requires auth)
2. POST /api/terminal/qr-pair - auto-pairs terminal with token (no auth required)
3. Validates existing 6-digit manual pairing still works
"""
import pytest
import requests
import os
import time

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials
TEST_EMAIL = "testadmin@test.com"
TEST_PASSWORD = "Test@123"
TEST_BRANCH_ID = "c435277f-9fc7-4d83-83e7-38be5b4423ac"
TEST_BRANCH_NAME = "Branch 1"


@pytest.fixture(scope="module")
def auth_token():
    """Login and get auth token"""
    response = requests.post(f"{BASE_URL}/api/auth/login", json={
        "email": TEST_EMAIL,
        "password": TEST_PASSWORD
    })
    assert response.status_code == 200, f"Login failed: {response.text}"
    data = response.json()
    assert "token" in data, "No token in login response"
    return data["token"]


@pytest.fixture
def auth_headers(auth_token):
    """Auth headers for authenticated requests"""
    return {
        "Authorization": f"Bearer {auth_token}",
        "Content-Type": "application/json"
    }


class TestQRPairingInitiation:
    """Tests for POST /api/terminal/initiate-qr-pairing"""

    def test_initiate_qr_pairing_success(self, auth_headers):
        """Test successful QR pair token generation"""
        response = requests.post(
            f"{BASE_URL}/api/terminal/initiate-qr-pairing",
            headers=auth_headers,
            json={"branch_id": TEST_BRANCH_ID}
        )
        assert response.status_code == 200, f"QR pairing initiation failed: {response.text}"
        data = response.json()
        
        # Verify response structure
        assert "token" in data, "No token in response"
        assert "branch_name" in data, "No branch_name in response"
        assert "expires_in" in data, "No expires_in in response"
        
        # Verify token format (urlsafe base64)
        assert len(data["token"]) > 20, "Token too short"
        assert data["branch_name"] == TEST_BRANCH_NAME, f"Expected branch name '{TEST_BRANCH_NAME}', got '{data['branch_name']}'"
        assert data["expires_in"] == 600, f"Expected 600 seconds expiry, got {data['expires_in']}"
        
        print(f"✓ QR token generated: {data['token'][:20]}... for {data['branch_name']}")

    def test_initiate_qr_pairing_without_branch_id(self, auth_headers):
        """Test QR pairing initiation fails without branch_id"""
        response = requests.post(
            f"{BASE_URL}/api/terminal/initiate-qr-pairing",
            headers=auth_headers,
            json={}
        )
        assert response.status_code == 400, f"Expected 400, got {response.status_code}"
        data = response.json()
        assert "detail" in data
        print(f"✓ Correctly rejected missing branch_id: {data['detail']}")

    def test_initiate_qr_pairing_invalid_branch(self, auth_headers):
        """Test QR pairing fails with invalid branch_id"""
        response = requests.post(
            f"{BASE_URL}/api/terminal/initiate-qr-pairing",
            headers=auth_headers,
            json={"branch_id": "nonexistent-branch-id"}
        )
        assert response.status_code == 404, f"Expected 404, got {response.status_code}"
        data = response.json()
        assert "detail" in data
        print(f"✓ Correctly rejected invalid branch: {data['detail']}")

    def test_initiate_qr_pairing_no_auth(self):
        """Test QR pairing initiation fails without auth"""
        response = requests.post(
            f"{BASE_URL}/api/terminal/initiate-qr-pairing",
            headers={"Content-Type": "application/json"},
            json={"branch_id": TEST_BRANCH_ID}
        )
        # Should fail with 401 or 403
        assert response.status_code in [401, 403], f"Expected 401/403, got {response.status_code}"
        print(f"✓ Correctly rejected unauthenticated request: {response.status_code}")


class TestQRPairTerminal:
    """Tests for POST /api/terminal/qr-pair"""

    def test_qr_pair_success(self, auth_headers):
        """Test successful terminal pairing via QR token"""
        # Step 1: Generate QR token
        init_response = requests.post(
            f"{BASE_URL}/api/terminal/initiate-qr-pairing",
            headers=auth_headers,
            json={"branch_id": TEST_BRANCH_ID}
        )
        assert init_response.status_code == 200, f"Token generation failed: {init_response.text}"
        token = init_response.json()["token"]
        
        # Step 2: Pair terminal using token (NO AUTH required)
        pair_response = requests.post(
            f"{BASE_URL}/api/terminal/qr-pair",
            headers={"Content-Type": "application/json"},
            json={"token": token}
        )
        assert pair_response.status_code == 200, f"QR pairing failed: {pair_response.text}"
        data = pair_response.json()
        
        # Verify response structure
        assert data.get("status") == "paired", f"Expected status='paired', got '{data.get('status')}'"
        assert "token" in data, "No session token in response"
        assert "terminal_id" in data, "No terminal_id in response"
        assert "branch_id" in data, "No branch_id in response"
        assert "branch_name" in data, "No branch_name in response"
        
        # Verify data values
        assert data["branch_id"] == TEST_BRANCH_ID
        assert data["branch_name"] == TEST_BRANCH_NAME
        
        print(f"✓ Terminal paired successfully: terminal_id={data['terminal_id']}, branch={data['branch_name']}")

    def test_qr_pair_invalid_token(self):
        """Test QR pairing fails with invalid token"""
        response = requests.post(
            f"{BASE_URL}/api/terminal/qr-pair",
            headers={"Content-Type": "application/json"},
            json={"token": "invalid-token-value-123"}
        )
        assert response.status_code == 404, f"Expected 404, got {response.status_code}"
        data = response.json()
        assert "detail" in data
        print(f"✓ Correctly rejected invalid token: {data['detail']}")

    def test_qr_pair_missing_token(self):
        """Test QR pairing fails without token"""
        response = requests.post(
            f"{BASE_URL}/api/terminal/qr-pair",
            headers={"Content-Type": "application/json"},
            json={}
        )
        assert response.status_code == 400, f"Expected 400, got {response.status_code}"
        data = response.json()
        assert "detail" in data
        print(f"✓ Correctly rejected missing token: {data['detail']}")

    def test_qr_pair_token_already_used(self, auth_headers):
        """Test QR pairing fails when token is already used"""
        # Generate token
        init_response = requests.post(
            f"{BASE_URL}/api/terminal/initiate-qr-pairing",
            headers=auth_headers,
            json={"branch_id": TEST_BRANCH_ID}
        )
        token = init_response.json()["token"]
        
        # First pair - should succeed
        first_pair = requests.post(
            f"{BASE_URL}/api/terminal/qr-pair",
            json={"token": token}
        )
        assert first_pair.status_code == 200
        
        # Second pair with same token - should fail
        second_pair = requests.post(
            f"{BASE_URL}/api/terminal/qr-pair",
            json={"token": token}
        )
        assert second_pair.status_code in [404, 409, 410], f"Expected 404/409/410, got {second_pair.status_code}"
        print(f"✓ Correctly rejected reused token: {second_pair.status_code}")


class TestManualPairingStillWorks:
    """Verify existing 6-digit code pairing still functions"""

    def test_generate_code_endpoint(self):
        """Test code generation endpoint still works"""
        response = requests.post(f"{BASE_URL}/api/terminal/generate-code")
        assert response.status_code == 200, f"Code generation failed: {response.text}"
        data = response.json()
        
        assert "code" in data, "No code in response"
        assert "expires_in" in data, "No expires_in in response"
        assert len(data["code"]) == 6, f"Expected 6-char code, got {len(data['code'])}"
        
        print(f"✓ Generated 6-digit code: {data['code']}")

    def test_manual_pair_endpoint(self, auth_headers):
        """Test manual pairing with 6-digit code still works"""
        # Generate code
        code_response = requests.post(f"{BASE_URL}/api/terminal/generate-code")
        assert code_response.status_code == 200
        code = code_response.json()["code"]
        
        # Pair using code (authenticated)
        pair_response = requests.post(
            f"{BASE_URL}/api/terminal/pair",
            headers=auth_headers,
            json={"code": code, "branch_id": TEST_BRANCH_ID}
        )
        assert pair_response.status_code == 200, f"Manual pairing failed: {pair_response.text}"
        data = pair_response.json()
        
        assert "message" in data
        assert "terminal_id" in data
        print(f"✓ Manual pairing works: {data['message']}")


class TestQRURLFormat:
    """Test that QR code encodes the correct URL format"""

    def test_qr_token_url_format(self, auth_headers):
        """Verify token can be used in URL format: {terminal_url}?pair={token}"""
        # Generate token
        response = requests.post(
            f"{BASE_URL}/api/terminal/initiate-qr-pairing",
            headers=auth_headers,
            json={"branch_id": TEST_BRANCH_ID}
        )
        assert response.status_code == 200
        token = response.json()["token"]
        
        # Verify token is URL-safe (no special chars that need encoding)
        import re
        assert re.match(r'^[A-Za-z0-9_-]+$', token), f"Token contains non-URL-safe chars: {token}"
        
        # Construct URL (this is what QR encodes)
        terminal_url = f"https://pos-refund-engine.preview.emergentagent.com/terminal?pair={token}"
        print(f"✓ QR URL format valid: {terminal_url[:80]}...")


class TestActiveTerminals:
    """Test active terminal listing"""

    def test_list_active_terminals(self, auth_headers):
        """Test listing active terminals endpoint"""
        response = requests.get(
            f"{BASE_URL}/api/terminal/active",
            headers=auth_headers
        )
        assert response.status_code == 200, f"Failed to get active terminals: {response.text}"
        data = response.json()
        assert isinstance(data, list), "Expected list of terminals"
        print(f"✓ Active terminals: {len(data)} found")
        for t in data[:3]:  # Show first 3
            print(f"  - {t.get('branch_name', 'Unknown')} paired by {t.get('user_name', 'Unknown')}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

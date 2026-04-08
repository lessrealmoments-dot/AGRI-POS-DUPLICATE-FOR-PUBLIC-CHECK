"""
Test Terminal POS Features - Iteration 159
Tests for:
1. Price scheme switcher with PIN gate for non-retail schemes
2. Total discount input at checkout with amount/percentage toggle
3. Smart profit guard with margin threshold configuration

Endpoints tested:
- GET /api/settings/sales-config
- PUT /api/settings/sales-config
- POST /api/verify/verify-pin-action
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials from test_credentials.md
ADMIN_EMAIL = "janmarkeahig@gmail.com"
ADMIN_PASSWORD = "Aa@58798546521325"
MANAGER_PIN = "521325"
STAFF_PIN = "8888"
BRANCH_1_ID = "c435277f-9fc7-4d83-83e7-38be5b4423ac"


@pytest.fixture(scope="module")
def auth_token():
    """Get authentication token for admin user"""
    response = requests.post(f"{BASE_URL}/api/auth/login", json={
        "email": ADMIN_EMAIL,
        "password": ADMIN_PASSWORD
    })
    if response.status_code != 200:
        pytest.skip(f"Authentication failed: {response.status_code} - {response.text}")
    return response.json().get("token")


@pytest.fixture(scope="module")
def auth_headers(auth_token):
    """Headers with auth token"""
    return {"Authorization": f"Bearer {auth_token}", "Content-Type": "application/json"}


class TestSalesConfig:
    """Tests for GET/PUT /api/settings/sales-config"""

    def test_get_sales_config_returns_defaults(self, auth_headers):
        """GET /api/settings/sales-config returns min_margin_percent and margin_warning_enabled defaults"""
        response = requests.get(f"{BASE_URL}/api/settings/sales-config", headers=auth_headers)
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        
        # Verify default fields exist
        assert "min_margin_percent" in data, "Response should contain min_margin_percent"
        assert "margin_warning_enabled" in data, "Response should contain margin_warning_enabled"
        
        # Verify default values
        assert isinstance(data["min_margin_percent"], (int, float)), "min_margin_percent should be numeric"
        assert isinstance(data["margin_warning_enabled"], bool), "margin_warning_enabled should be boolean"
        
        print(f"✓ GET /api/settings/sales-config returns: min_margin_percent={data['min_margin_percent']}, margin_warning_enabled={data['margin_warning_enabled']}")

    def test_get_sales_config_requires_auth(self):
        """GET /api/settings/sales-config requires authentication"""
        response = requests.get(f"{BASE_URL}/api/settings/sales-config")
        
        assert response.status_code in [401, 403], f"Expected 401/403 without auth, got {response.status_code}"
        print("✓ GET /api/settings/sales-config requires authentication")

    def test_put_sales_config_updates_margin_threshold(self, auth_headers):
        """PUT /api/settings/sales-config updates margin threshold (admin only)"""
        # First get current config
        get_response = requests.get(f"{BASE_URL}/api/settings/sales-config", headers=auth_headers)
        original_config = get_response.json()
        
        # Update with new values
        new_config = {
            "min_margin_percent": 5,
            "margin_warning_enabled": True
        }
        response = requests.put(f"{BASE_URL}/api/settings/sales-config", json=new_config, headers=auth_headers)
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        
        # Verify update was applied
        assert data.get("min_margin_percent") == 5, f"Expected min_margin_percent=5, got {data.get('min_margin_percent')}"
        assert data.get("margin_warning_enabled") == True, f"Expected margin_warning_enabled=True, got {data.get('margin_warning_enabled')}"
        
        # Verify GET returns updated values
        verify_response = requests.get(f"{BASE_URL}/api/settings/sales-config", headers=auth_headers)
        verify_data = verify_response.json()
        assert verify_data["min_margin_percent"] == 5, "GET should return updated min_margin_percent"
        
        # Restore original config
        requests.put(f"{BASE_URL}/api/settings/sales-config", json=original_config, headers=auth_headers)
        
        print("✓ PUT /api/settings/sales-config updates margin threshold successfully")

    def test_put_sales_config_clamps_margin_percent(self, auth_headers):
        """PUT /api/settings/sales-config clamps min_margin_percent between 0 and 50"""
        # Test upper bound
        response = requests.put(f"{BASE_URL}/api/settings/sales-config", json={
            "min_margin_percent": 100,
            "margin_warning_enabled": True
        }, headers=auth_headers)
        
        assert response.status_code == 200
        data = response.json()
        assert data["min_margin_percent"] <= 50, f"min_margin_percent should be clamped to max 50, got {data['min_margin_percent']}"
        
        # Test lower bound
        response = requests.put(f"{BASE_URL}/api/settings/sales-config", json={
            "min_margin_percent": -10,
            "margin_warning_enabled": True
        }, headers=auth_headers)
        
        assert response.status_code == 200
        data = response.json()
        assert data["min_margin_percent"] >= 0, f"min_margin_percent should be clamped to min 0, got {data['min_margin_percent']}"
        
        # Restore default
        requests.put(f"{BASE_URL}/api/settings/sales-config", json={
            "min_margin_percent": 1,
            "margin_warning_enabled": True
        }, headers=auth_headers)
        
        print("✓ PUT /api/settings/sales-config clamps margin percent correctly")


class TestVerifyPinAction:
    """Tests for POST /api/verify/verify-pin-action"""

    def test_verify_pin_action_with_valid_manager_pin(self, auth_headers):
        """POST /api/verify/verify-pin-action with valid manager PIN returns verified:true"""
        response = requests.post(f"{BASE_URL}/api/verify/verify-pin-action", json={
            "pin": MANAGER_PIN,
            "action": "terminal_wholesale_switch",
            "branch_id": BRANCH_1_ID
        }, headers=auth_headers)
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        
        assert data.get("verified") == True, f"Expected verified=True, got {data.get('verified')}"
        print(f"✓ POST /api/verify/verify-pin-action with valid manager PIN returns verified=True, verified_by={data.get('verified_by')}")

    def test_verify_pin_action_with_wrong_pin(self, auth_headers):
        """POST /api/verify/verify-pin-action with wrong PIN returns 403"""
        response = requests.post(f"{BASE_URL}/api/verify/verify-pin-action", json={
            "pin": "000000",
            "action": "terminal_wholesale_switch",
            "branch_id": BRANCH_1_ID
        }, headers=auth_headers)
        
        assert response.status_code == 403, f"Expected 403, got {response.status_code}: {response.text}"
        print("✓ POST /api/verify/verify-pin-action with wrong PIN returns 403")

    def test_verify_pin_action_missing_pin(self, auth_headers):
        """POST /api/verify/verify-pin-action with missing pin returns 400"""
        response = requests.post(f"{BASE_URL}/api/verify/verify-pin-action", json={
            "action": "terminal_wholesale_switch",
            "branch_id": BRANCH_1_ID
        }, headers=auth_headers)
        
        assert response.status_code == 400, f"Expected 400, got {response.status_code}: {response.text}"
        print("✓ POST /api/verify/verify-pin-action with missing pin returns 400")

    def test_verify_pin_action_missing_action(self, auth_headers):
        """POST /api/verify/verify-pin-action with missing action returns 400"""
        response = requests.post(f"{BASE_URL}/api/verify/verify-pin-action", json={
            "pin": MANAGER_PIN,
            "branch_id": BRANCH_1_ID
        }, headers=auth_headers)
        
        assert response.status_code == 400, f"Expected 400, got {response.status_code}: {response.text}"
        print("✓ POST /api/verify/verify-pin-action with missing action returns 400")

    def test_verify_pin_action_requires_auth(self):
        """POST /api/verify/verify-pin-action requires authentication"""
        response = requests.post(f"{BASE_URL}/api/verify/verify-pin-action", json={
            "pin": MANAGER_PIN,
            "action": "terminal_wholesale_switch"
        })
        
        assert response.status_code in [401, 403], f"Expected 401/403 without auth, got {response.status_code}"
        print("✓ POST /api/verify/verify-pin-action requires authentication")

    def test_verify_pin_action_with_staff_pin_fails_for_wholesale(self, auth_headers):
        """POST /api/verify/verify-pin-action with staff PIN fails for terminal_wholesale_switch (requires manager/admin)"""
        response = requests.post(f"{BASE_URL}/api/verify/verify-pin-action", json={
            "pin": STAFF_PIN,
            "action": "terminal_wholesale_switch",
            "branch_id": BRANCH_1_ID
        }, headers=auth_headers)
        
        # Staff PIN should not be authorized for wholesale switch (policy requires admin_pin, manager_pin, or totp)
        assert response.status_code == 403, f"Expected 403 for staff PIN on wholesale switch, got {response.status_code}: {response.text}"
        print("✓ POST /api/verify/verify-pin-action with staff PIN fails for terminal_wholesale_switch (correct policy enforcement)")

    def test_verify_pin_action_with_empty_pin(self, auth_headers):
        """POST /api/verify/verify-pin-action with empty pin returns 400"""
        response = requests.post(f"{BASE_URL}/api/verify/verify-pin-action", json={
            "pin": "",
            "action": "terminal_wholesale_switch",
            "branch_id": BRANCH_1_ID
        }, headers=auth_headers)
        
        assert response.status_code == 400, f"Expected 400 for empty pin, got {response.status_code}: {response.text}"
        print("✓ POST /api/verify/verify-pin-action with empty pin returns 400")


class TestTerminalWholesaleSwitchPolicy:
    """Tests for terminal_wholesale_switch PIN policy configuration"""

    def test_terminal_wholesale_switch_policy_exists(self, auth_headers):
        """Verify terminal_wholesale_switch action exists in PIN policies"""
        response = requests.get(f"{BASE_URL}/api/settings/pin-policies", headers=auth_headers)
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        
        # Check that terminal_wholesale_switch is in the actions list
        actions = data.get("actions", [])
        action_keys = [a["key"] for a in actions]
        
        assert "terminal_wholesale_switch" in action_keys, f"terminal_wholesale_switch should be in PIN policy actions. Found: {action_keys}"
        
        # Find the action and verify its defaults
        wholesale_action = next((a for a in actions if a["key"] == "terminal_wholesale_switch"), None)
        assert wholesale_action is not None
        assert "admin_pin" in wholesale_action["defaults"], "terminal_wholesale_switch should allow admin_pin"
        assert "manager_pin" in wholesale_action["defaults"], "terminal_wholesale_switch should allow manager_pin"
        assert "totp" in wholesale_action["defaults"], "terminal_wholesale_switch should allow totp"
        
        print(f"✓ terminal_wholesale_switch policy exists with defaults: {wholesale_action['defaults']}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

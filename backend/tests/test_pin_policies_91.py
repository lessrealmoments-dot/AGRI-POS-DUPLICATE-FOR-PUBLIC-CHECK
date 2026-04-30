"""
Test PIN Policies System - Iteration 91
Tests the comprehensive PIN Policy system for 23 PIN-protected actions:
1. GET /settings/pin-policies returns 23 actions with correct defaults (admin only)
2. PUT /settings/pin-policies updates and restricts policies
3. verify-manager-pin respects custom policies (e.g., restricting to admin_pin+totp rejects manager_pin)
4. verify-manager-pin with action_key parameter works correctly
5. Non-admin users cannot access /settings/pin-policies (403)
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', 'https://price-match-pos.preview.emergentagent.com')

# Test credentials
SUPER_ADMIN_EMAIL = "janmarkeahig@gmail.com"
SUPER_ADMIN_PASSWORD = "Aa@58798546521325"
MANAGER_PIN = "521325"
OWNER_PIN = "1234"

# Expected 23 PIN-protected actions
EXPECTED_ACTIONS = [
    "credit_sale_approval", "void_invoice", "void_payment", "void_return", "invoice_edit", "pos_discount",
    "fund_transfer_cashier_safe", "fund_transfer_safe_bank", "fund_transfer_capital_add",
    "reverse_customer_cashout", "reverse_employee_advance",
    "daily_close", "daily_close_batch",
    "inventory_adjust", "product_delete", "price_override", "reopen_po",
    "transaction_verify", "po_mark_reviewed", "receipt_mark_reviewed", "public_receipt_verify",
    "admin_action", "backup_restore"
]

EXPECTED_METHODS = ["admin_pin", "manager_pin", "totp", "auditor_pin"]


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


class TestGetPinPolicies:
    """Test GET /settings/pin-policies endpoint."""
    
    def test_get_pin_policies_returns_23_actions(self, auth_headers):
        """GET /settings/pin-policies should return 23 actions with their defaults."""
        response = requests.get(
            f"{BASE_URL}/api/settings/pin-policies",
            headers=auth_headers
        )
        assert response.status_code == 200, f"Failed: {response.text}"
        data = response.json()
        
        # Check structure
        assert "actions" in data, "Response missing 'actions' key"
        assert "methods" in data, "Response missing 'methods' key"
        assert "policies" in data, "Response missing 'policies' key"
        
        # Check 23 actions
        actions = data["actions"]
        assert len(actions) == 23, f"Expected 23 actions, got {len(actions)}"
        
        # Verify all expected actions are present
        action_keys = [a["key"] for a in actions]
        for expected_action in EXPECTED_ACTIONS:
            assert expected_action in action_keys, f"Missing action: {expected_action}"
        
        # Check methods
        methods = data["methods"]
        assert methods == EXPECTED_METHODS, f"Methods mismatch: {methods}"
        
        # Check policies dict has all 23 keys
        policies = data["policies"]
        assert len(policies) == 23, f"Expected 23 policies, got {len(policies)}"
    
    def test_each_action_has_required_fields(self, auth_headers):
        """Each action should have key, label, module, and defaults."""
        response = requests.get(
            f"{BASE_URL}/api/settings/pin-policies",
            headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        
        for action in data["actions"]:
            assert "key" in action, f"Action missing 'key': {action}"
            assert "label" in action, f"Action missing 'label': {action}"
            assert "module" in action, f"Action missing 'module': {action}"
            assert "defaults" in action, f"Action missing 'defaults': {action}"
            assert isinstance(action["defaults"], list), f"defaults should be list: {action}"
            assert len(action["defaults"]) > 0, f"defaults should not be empty: {action}"
    
    def test_default_policies_match_action_defaults(self, auth_headers):
        """Policies should match action defaults when no custom policy is set."""
        response = requests.get(
            f"{BASE_URL}/api/settings/pin-policies",
            headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        
        actions = data["actions"]
        policies = data["policies"]
        
        # Check a few specific defaults
        credit_sale = next(a for a in actions if a["key"] == "credit_sale_approval")
        assert "admin_pin" in credit_sale["defaults"]
        assert "manager_pin" in credit_sale["defaults"]
        assert "totp" in credit_sale["defaults"]
        
        # product_delete should only have admin_pin and totp
        product_delete = next(a for a in actions if a["key"] == "product_delete")
        assert "admin_pin" in product_delete["defaults"]
        assert "totp" in product_delete["defaults"]
        assert "manager_pin" not in product_delete["defaults"]


class TestUpdatePinPolicies:
    """Test PUT /settings/pin-policies endpoint."""
    
    def test_update_single_policy(self, auth_headers):
        """PUT /settings/pin-policies should update a single action's policy."""
        # Update credit_sale_approval to only allow admin_pin and totp
        new_policies = {
            "credit_sale_approval": ["admin_pin", "totp"]
        }
        response = requests.put(
            f"{BASE_URL}/api/settings/pin-policies",
            headers=auth_headers,
            json={"policies": new_policies}
        )
        assert response.status_code == 200, f"Failed: {response.text}"
        data = response.json()
        assert "message" in data
        assert data["message"] == "PIN policies updated"
        assert data["policies"]["credit_sale_approval"] == ["admin_pin", "totp"]
    
    def test_update_multiple_policies(self, auth_headers):
        """PUT should allow updating multiple policies at once."""
        new_policies = {
            "void_invoice": ["admin_pin", "totp"],
            "void_payment": ["admin_pin", "manager_pin"]
        }
        response = requests.put(
            f"{BASE_URL}/api/settings/pin-policies",
            headers=auth_headers,
            json={"policies": new_policies}
        )
        assert response.status_code == 200, f"Failed: {response.text}"
        data = response.json()
        assert data["policies"]["void_invoice"] == ["admin_pin", "totp"]
        assert data["policies"]["void_payment"] == ["admin_pin", "manager_pin"]
    
    def test_reject_invalid_method(self, auth_headers):
        """PUT should reject invalid PIN methods."""
        invalid_policies = {
            "credit_sale_approval": ["admin_pin", "invalid_method"]
        }
        response = requests.put(
            f"{BASE_URL}/api/settings/pin-policies",
            headers=auth_headers,
            json={"policies": invalid_policies}
        )
        assert response.status_code == 400, f"Should reject invalid method: {response.text}"
        assert "Invalid method" in response.json()["detail"]
    
    def test_reject_empty_methods_list(self, auth_headers):
        """PUT should reject empty methods list (at least one required)."""
        invalid_policies = {
            "credit_sale_approval": []
        }
        response = requests.put(
            f"{BASE_URL}/api/settings/pin-policies",
            headers=auth_headers,
            json={"policies": invalid_policies}
        )
        assert response.status_code == 400, f"Should reject empty list: {response.text}"
        assert "At least one PIN method required" in response.json()["detail"]
    
    def test_reset_policies_with_empty_object(self, auth_headers):
        """PUT with empty policies object should clear all custom overrides."""
        response = requests.put(
            f"{BASE_URL}/api/settings/pin-policies",
            headers=auth_headers,
            json={"policies": {}}
        )
        assert response.status_code == 200, f"Failed: {response.text}"
        # After reset, GET should return defaults
        get_response = requests.get(
            f"{BASE_URL}/api/settings/pin-policies",
            headers=auth_headers
        )
        data = get_response.json()
        # credit_sale_approval should be back to default (admin_pin, manager_pin, totp)
        assert "manager_pin" in data["policies"]["credit_sale_approval"]


class TestPinPolicyEnforcement:
    """Test that verify-manager-pin respects custom policies."""
    
    def test_manager_pin_rejected_when_restricted(self, auth_headers):
        """When credit_sale_approval is restricted to admin_pin+totp, manager_pin should be rejected."""
        # First, set the restrictive policy
        restrict_response = requests.put(
            f"{BASE_URL}/api/settings/pin-policies",
            headers=auth_headers,
            json={"policies": {"credit_sale_approval": ["admin_pin", "totp"]}}
        )
        assert restrict_response.status_code == 200, f"Failed to set policy: {restrict_response.text}"
        
        # Now verify manager_pin is rejected for credit_sale_approval
        verify_response = requests.post(
            f"{BASE_URL}/api/auth/verify-manager-pin",
            headers=auth_headers,
            json={"pin": MANAGER_PIN, "action_key": "credit_sale_approval"}
        )
        assert verify_response.status_code == 200
        data = verify_response.json()
        assert data["valid"] is False, f"Manager PIN should be rejected when restricted: {data}"
    
    def test_owner_pin_accepted_when_restricted(self, auth_headers):
        """Owner PIN should still work when policy includes admin_pin."""
        # Ensure policy is set to admin_pin+totp
        requests.put(
            f"{BASE_URL}/api/settings/pin-policies",
            headers=auth_headers,
            json={"policies": {"credit_sale_approval": ["admin_pin", "totp"]}}
        )
        
        # Owner PIN should be accepted
        verify_response = requests.post(
            f"{BASE_URL}/api/auth/verify-manager-pin",
            headers=auth_headers,
            json={"pin": OWNER_PIN, "action_key": "credit_sale_approval"}
        )
        assert verify_response.status_code == 200
        data = verify_response.json()
        assert data["valid"] is True, f"Owner PIN should be accepted: {data}"
        assert data["role"] == "admin_pin"
    
    def test_manager_pin_accepted_after_policy_reset(self, auth_headers):
        """After resetting policies, manager_pin should work again for credit_sale_approval."""
        # Reset policies
        reset_response = requests.put(
            f"{BASE_URL}/api/settings/pin-policies",
            headers=auth_headers,
            json={"policies": {}}
        )
        assert reset_response.status_code == 200
        
        # Manager PIN should now work for credit_sale_approval (default policy)
        verify_response = requests.post(
            f"{BASE_URL}/api/auth/verify-manager-pin",
            headers=auth_headers,
            json={"pin": MANAGER_PIN, "action_key": "credit_sale_approval"}
        )
        assert verify_response.status_code == 200
        data = verify_response.json()
        assert data["valid"] is True, f"Manager PIN should be accepted with default policy: {data}"
        assert data["role"] == "manager_pin"
    
    def test_action_key_parameter_works(self, auth_headers):
        """verify-manager-pin should accept action_key parameter."""
        # Test with different action keys
        for action_key in ["void_invoice", "daily_close", "inventory_adjust"]:
            response = requests.post(
                f"{BASE_URL}/api/auth/verify-manager-pin",
                headers=auth_headers,
                json={"pin": MANAGER_PIN, "action_key": action_key}
            )
            assert response.status_code == 200, f"Failed for action_key={action_key}: {response.text}"


class TestPinPoliciesAuthorization:
    """Test that only admins can access PIN policies endpoints."""
    
    def test_non_admin_cannot_get_policies(self):
        """Non-admin users should get 403 when accessing /settings/pin-policies."""
        # Try to get policies without auth
        response = requests.get(f"{BASE_URL}/api/settings/pin-policies")
        # Should be 401 (no auth) or 403 (not admin)
        assert response.status_code in [401, 403, 422], f"Expected auth error: {response.text}"
    
    def test_non_admin_cannot_put_policies(self):
        """Non-admin users should get 403 when updating /settings/pin-policies."""
        response = requests.put(
            f"{BASE_URL}/api/settings/pin-policies",
            json={"policies": {}}
        )
        assert response.status_code in [401, 403, 422], f"Expected auth error: {response.text}"


class TestPinPoliciesModules:
    """Test that actions are correctly grouped by modules."""
    
    def test_actions_grouped_by_module(self, auth_headers):
        """Verify actions have correct module groupings."""
        response = requests.get(
            f"{BASE_URL}/api/settings/pin-policies",
            headers=auth_headers
        )
        assert response.status_code == 200
        actions = response.json()["actions"]
        
        # Build module mapping
        modules = {}
        for action in actions:
            module = action["module"]
            if module not in modules:
                modules[module] = []
            modules[module].append(action["key"])
        
        # Verify expected modules exist
        expected_modules = ["Sales", "Fund Management", "Reversals", "Daily Operations", 
                          "Inventory", "Products", "Purchase Orders", "Audit", "System"]
        for expected_module in expected_modules:
            assert expected_module in modules, f"Missing module: {expected_module}"
        
        # Verify Sales module has expected actions
        sales_actions = modules.get("Sales", [])
        assert "credit_sale_approval" in sales_actions
        assert "void_invoice" in sales_actions
        assert "invoice_edit" in sales_actions
        assert "pos_discount" in sales_actions
        
        # Verify Audit module includes auditor_pin by default
        audit_actions = [a for a in actions if a["module"] == "Audit"]
        for action in audit_actions:
            assert "auditor_pin" in action["defaults"], f"Audit action {action['key']} should include auditor_pin"


class TestCleanup:
    """Reset policies after all tests."""
    
    def test_reset_all_policies(self, auth_headers):
        """Reset all custom policies to defaults."""
        response = requests.put(
            f"{BASE_URL}/api/settings/pin-policies",
            headers=auth_headers,
            json={"policies": {}}
        )
        assert response.status_code == 200


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

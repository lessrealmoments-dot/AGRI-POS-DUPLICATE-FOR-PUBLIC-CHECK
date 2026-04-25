"""
Test Suite for SMS Engine and Terminal Credential-Pair Features (Iteration 150)

Tests:
1. SMS Templates API - GET /api/sms/templates (10 templates)
2. SMS Templates Update - PUT /api/sms/templates/{id}
3. SMS Settings API - GET /api/sms/settings
4. SMS Settings Update - PUT /api/sms/settings/{trigger_key}
5. SMS Queue API - GET /api/sms/queue
6. SMS Queue Pending - GET /api/sms/queue/pending
7. SMS Manual Send - POST /api/sms/send
8. SMS Queue Mark Sent - PATCH /api/sms/queue/{id}/mark-sent
9. SMS Queue Mark Failed - PATCH /api/sms/queue/{id}/mark-failed
10. SMS Queue Retry - POST /api/sms/queue/{id}/retry
11. SMS Queue Skip - POST /api/sms/queue/{id}/skip
12. SMS Blast - POST /api/sms/blast
13. SMS Stats - GET /api/sms/stats
14. Terminal Credential Pair - Admin without branch (select_branch)
15. Terminal Credential Pair - Admin with branch_id (paired)
16. Terminal Credential Pair - Invalid credentials (401)
"""

import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', 'https://crop-credit-pos.preview.emergentagent.com').rstrip('/')

# Test credentials
SUPER_ADMIN_EMAIL = "janmarkeahig@gmail.com"
SUPER_ADMIN_PASSWORD = "Aa@58798546521325"


@pytest.fixture(scope="module")
def auth_token():
    """Get authentication token for super admin."""
    response = requests.post(f"{BASE_URL}/api/auth/login", json={
        "email": SUPER_ADMIN_EMAIL,
        "password": SUPER_ADMIN_PASSWORD
    })
    assert response.status_code == 200, f"Login failed: {response.text}"
    data = response.json()
    return data["token"]


@pytest.fixture(scope="module")
def auth_headers(auth_token):
    """Return headers with auth token."""
    return {
        "Authorization": f"Bearer {auth_token}",
        "Content-Type": "application/json"
    }


class TestSMSTemplates:
    """SMS Templates API tests."""

    def test_get_templates_returns_10_templates(self, auth_headers):
        """GET /api/sms/templates returns 10 templates with correct keys."""
        response = requests.get(f"{BASE_URL}/api/sms/templates", headers=auth_headers)
        assert response.status_code == 200, f"Failed: {response.text}"
        templates = response.json()
        
        assert isinstance(templates, list), "Response should be a list"
        assert len(templates) == 10, f"Expected 10 templates, got {len(templates)}"
        
        # Check expected template keys
        expected_keys = [
            "credit_new", "reminder_15day", "reminder_7day", "overdue_notice",
            "payment_received", "charge_applied", "delivery_ready", "promo_blast",
            "monthly_summary", "custom"
        ]
        actual_keys = [t["key"] for t in templates]
        for key in expected_keys:
            assert key in actual_keys, f"Missing template key: {key}"
        
        # Check template structure
        for t in templates:
            assert "id" in t, "Template missing 'id'"
            assert "key" in t, "Template missing 'key'"
            assert "name" in t, "Template missing 'name'"
            assert "body" in t, "Template missing 'body'"
            assert "placeholders" in t, "Template missing 'placeholders'"
            assert "trigger" in t, "Template missing 'trigger'"
            assert "active" in t, "Template missing 'active'"
        
        print(f"✓ GET /api/sms/templates returned {len(templates)} templates with correct structure")

    def test_update_template_body_and_active(self, auth_headers):
        """PUT /api/sms/templates/{id} can update template body and active status."""
        # First get templates to find one to update
        response = requests.get(f"{BASE_URL}/api/sms/templates", headers=auth_headers)
        templates = response.json()
        
        # Find the custom template
        custom_template = next((t for t in templates if t["key"] == "custom"), None)
        assert custom_template is not None, "Custom template not found"
        
        template_id = custom_template["id"]
        original_body = custom_template["body"]
        
        # Update the template
        new_body = "<message> - Test Update"
        response = requests.put(
            f"{BASE_URL}/api/sms/templates/{template_id}",
            headers=auth_headers,
            json={"body": new_body, "active": True}
        )
        assert response.status_code == 200, f"Update failed: {response.text}"
        updated = response.json()
        assert updated["body"] == new_body, "Body not updated"
        
        # Restore original
        requests.put(
            f"{BASE_URL}/api/sms/templates/{template_id}",
            headers=auth_headers,
            json={"body": original_body}
        )
        
        print(f"✓ PUT /api/sms/templates/{template_id} successfully updated template")


class TestSMSSettings:
    """SMS Settings API tests."""

    def test_get_settings_returns_merged_with_defaults(self, auth_headers):
        """GET /api/sms/settings returns merged settings with template defaults."""
        response = requests.get(f"{BASE_URL}/api/sms/settings", headers=auth_headers)
        assert response.status_code == 200, f"Failed: {response.text}"
        settings = response.json()
        
        assert isinstance(settings, list), "Response should be a list"
        assert len(settings) == 10, f"Expected 10 settings (one per template), got {len(settings)}"
        
        # Check structure
        for s in settings:
            assert "trigger_key" in s, "Setting missing 'trigger_key'"
            assert "template_name" in s, "Setting missing 'template_name'"
            assert "enabled" in s, "Setting missing 'enabled'"
        
        print(f"✓ GET /api/sms/settings returned {len(settings)} settings with correct structure")

    def test_update_setting_enable_disable(self, auth_headers):
        """PUT /api/sms/settings/{trigger_key} can enable/disable triggers."""
        trigger_key = "promo_blast"
        
        # Disable the trigger
        response = requests.put(
            f"{BASE_URL}/api/sms/settings/{trigger_key}",
            headers=auth_headers,
            json={"enabled": False}
        )
        assert response.status_code == 200, f"Disable failed: {response.text}"
        result = response.json()
        assert result["trigger_key"] == trigger_key
        assert result["enabled"] == False
        
        # Re-enable the trigger
        response = requests.put(
            f"{BASE_URL}/api/sms/settings/{trigger_key}",
            headers=auth_headers,
            json={"enabled": True}
        )
        assert response.status_code == 200, f"Enable failed: {response.text}"
        result = response.json()
        assert result["enabled"] == True
        
        print(f"✓ PUT /api/sms/settings/{trigger_key} successfully toggled enabled status")


class TestSMSQueue:
    """SMS Queue API tests."""

    def test_get_queue_returns_entries(self, auth_headers):
        """GET /api/sms/queue returns queue entries with status filter."""
        response = requests.get(f"{BASE_URL}/api/sms/queue", headers=auth_headers)
        assert response.status_code == 200, f"Failed: {response.text}"
        data = response.json()
        
        assert "items" in data, "Response missing 'items'"
        assert "total" in data, "Response missing 'total'"
        assert isinstance(data["items"], list), "'items' should be a list"
        
        print(f"✓ GET /api/sms/queue returned {data['total']} total entries")

    def test_get_queue_with_status_filter(self, auth_headers):
        """GET /api/sms/queue?status=pending filters correctly."""
        response = requests.get(f"{BASE_URL}/api/sms/queue?status=pending", headers=auth_headers)
        assert response.status_code == 200, f"Failed: {response.text}"
        data = response.json()
        
        # All items should have status=pending
        for item in data["items"]:
            assert item["status"] == "pending", f"Item has wrong status: {item['status']}"
        
        print(f"✓ GET /api/sms/queue?status=pending returned {len(data['items'])} pending items")

    def test_get_pending_sms(self, auth_headers):
        """GET /api/sms/queue/pending returns only pending SMS."""
        response = requests.get(f"{BASE_URL}/api/sms/queue/pending", headers=auth_headers)
        assert response.status_code == 200, f"Failed: {response.text}"
        items = response.json()
        
        assert isinstance(items, list), "Response should be a list"
        for item in items:
            assert item["status"] == "pending", f"Item has wrong status: {item['status']}"
        
        print(f"✓ GET /api/sms/queue/pending returned {len(items)} pending items")


class TestSMSManualSend:
    """SMS Manual Send and Queue Operations tests."""

    @pytest.fixture
    def created_sms_id(self, auth_headers):
        """Create a test SMS and return its ID."""
        response = requests.post(
            f"{BASE_URL}/api/sms/send",
            headers=auth_headers,
            json={
                "customer_id": "test-customer-id",
                "customer_name": "Test Customer",
                "phone": "+639123456789",
                "message": "Test SMS message for iteration 150",
                "branch_id": "",
                "branch_name": ""
            }
        )
        assert response.status_code == 200, f"Create SMS failed: {response.text}"
        data = response.json()
        return data["id"]

    def test_send_manual_sms_creates_pending(self, auth_headers):
        """POST /api/sms/send creates a manual SMS in pending queue."""
        response = requests.post(
            f"{BASE_URL}/api/sms/send",
            headers=auth_headers,
            json={
                "customer_id": "test-customer-123",
                "customer_name": "Test Customer Manual",
                "phone": "+639987654321",
                "message": "Manual test SMS message",
                "branch_id": "",
                "branch_name": ""
            }
        )
        assert response.status_code == 200, f"Failed: {response.text}"
        data = response.json()
        
        assert "id" in data, "Response missing 'id'"
        assert data["status"] == "pending", f"Expected pending status, got {data['status']}"
        assert data["template_key"] == "custom", f"Expected custom template, got {data['template_key']}"
        assert data["trigger"] == "manual", f"Expected manual trigger, got {data['trigger']}"
        assert data["phone"] == "+639987654321"
        assert data["message"] == "Manual test SMS message"
        
        print(f"✓ POST /api/sms/send created SMS with id={data['id']} in pending status")
        return data["id"]

    def test_send_sms_requires_phone_and_message(self, auth_headers):
        """POST /api/sms/send returns 400 if phone or message missing."""
        # Missing phone
        response = requests.post(
            f"{BASE_URL}/api/sms/send",
            headers=auth_headers,
            json={"message": "Test"}
        )
        assert response.status_code == 400, f"Expected 400, got {response.status_code}"
        
        # Missing message
        response = requests.post(
            f"{BASE_URL}/api/sms/send",
            headers=auth_headers,
            json={"phone": "+639123456789"}
        )
        assert response.status_code == 400, f"Expected 400, got {response.status_code}"
        
        print("✓ POST /api/sms/send correctly validates required fields")

    def test_mark_sms_sent(self, auth_headers, created_sms_id):
        """PATCH /api/sms/queue/{id}/mark-sent changes status to sent."""
        response = requests.patch(
            f"{BASE_URL}/api/sms/queue/{created_sms_id}/mark-sent",
            headers=auth_headers
        )
        assert response.status_code == 200, f"Failed: {response.text}"
        data = response.json()
        assert data["status"] == "sent"
        
        print(f"✓ PATCH /api/sms/queue/{created_sms_id}/mark-sent changed status to sent")

    def test_mark_sms_failed(self, auth_headers):
        """PATCH /api/sms/queue/{id}/mark-failed changes status to failed."""
        # Create a new SMS first
        response = requests.post(
            f"{BASE_URL}/api/sms/send",
            headers=auth_headers,
            json={
                "phone": "+639111222333",
                "message": "Test for mark-failed"
            }
        )
        sms_id = response.json()["id"]
        
        # Mark as failed
        response = requests.patch(
            f"{BASE_URL}/api/sms/queue/{sms_id}/mark-failed",
            headers=auth_headers,
            json={"error": "Test error message"}
        )
        assert response.status_code == 200, f"Failed: {response.text}"
        data = response.json()
        assert data["status"] == "failed"
        
        print(f"✓ PATCH /api/sms/queue/{sms_id}/mark-failed changed status to failed")
        return sms_id

    def test_retry_failed_sms(self, auth_headers):
        """POST /api/sms/queue/{id}/retry re-queues a failed SMS."""
        # Create and fail an SMS
        response = requests.post(
            f"{BASE_URL}/api/sms/send",
            headers=auth_headers,
            json={"phone": "+639444555666", "message": "Test for retry"}
        )
        sms_id = response.json()["id"]
        
        # Mark as failed
        requests.patch(
            f"{BASE_URL}/api/sms/queue/{sms_id}/mark-failed",
            headers=auth_headers,
            json={"error": "Simulated failure"}
        )
        
        # Retry
        response = requests.post(
            f"{BASE_URL}/api/sms/queue/{sms_id}/retry",
            headers=auth_headers
        )
        assert response.status_code == 200, f"Failed: {response.text}"
        data = response.json()
        assert data["status"] == "pending"
        
        print(f"✓ POST /api/sms/queue/{sms_id}/retry re-queued SMS to pending")

    def test_skip_pending_sms(self, auth_headers):
        """POST /api/sms/queue/{id}/skip marks SMS as skipped."""
        # Create an SMS
        response = requests.post(
            f"{BASE_URL}/api/sms/send",
            headers=auth_headers,
            json={"phone": "+639777888999", "message": "Test for skip"}
        )
        sms_id = response.json()["id"]
        
        # Skip it
        response = requests.post(
            f"{BASE_URL}/api/sms/queue/{sms_id}/skip",
            headers=auth_headers
        )
        assert response.status_code == 200, f"Failed: {response.text}"
        data = response.json()
        assert data["status"] == "skipped"
        
        print(f"✓ POST /api/sms/queue/{sms_id}/skip marked SMS as skipped")


class TestSMSBlast:
    """SMS Blast API tests."""

    def test_blast_requires_message(self, auth_headers):
        """POST /api/sms/blast returns 400 if message missing."""
        response = requests.post(
            f"{BASE_URL}/api/sms/blast",
            headers=auth_headers,
            json={"customer_ids": []}
        )
        assert response.status_code == 400, f"Expected 400, got {response.status_code}"
        
        print("✓ POST /api/sms/blast correctly validates message required")

    def test_blast_queues_messages(self, auth_headers):
        """POST /api/sms/blast queues messages to multiple customers."""
        response = requests.post(
            f"{BASE_URL}/api/sms/blast",
            headers=auth_headers,
            json={
                "message": "Test promo blast message <customer_name>!",
                "filter": {"min_balance": 99999999}  # High balance to avoid spamming real customers
            }
        )
        assert response.status_code == 200, f"Failed: {response.text}"
        data = response.json()
        
        assert "queued" in data, "Response missing 'queued'"
        assert "total_customers" in data, "Response missing 'total_customers'"
        assert "skipped_no_phone" in data, "Response missing 'skipped_no_phone'"
        
        print(f"✓ POST /api/sms/blast queued {data['queued']} messages to {data['total_customers']} customers")


class TestSMSStats:
    """SMS Stats API tests."""

    def test_get_stats_returns_counts(self, auth_headers):
        """GET /api/sms/stats returns correct counts."""
        response = requests.get(f"{BASE_URL}/api/sms/stats", headers=auth_headers)
        assert response.status_code == 200, f"Failed: {response.text}"
        stats = response.json()
        
        assert "pending" in stats, "Stats missing 'pending'"
        assert "sent" in stats, "Stats missing 'sent'"
        assert "failed" in stats, "Stats missing 'failed'"
        assert "skipped" in stats, "Stats missing 'skipped'"
        assert "total" in stats, "Stats missing 'total'"
        
        # Verify total is sum of all statuses
        calculated_total = stats["pending"] + stats["sent"] + stats["failed"] + stats["skipped"]
        assert stats["total"] == calculated_total, f"Total mismatch: {stats['total']} != {calculated_total}"
        
        print(f"✓ GET /api/sms/stats returned: pending={stats['pending']}, sent={stats['sent']}, failed={stats['failed']}, skipped={stats['skipped']}, total={stats['total']}")


class TestTerminalCredentialPair:
    """Terminal Credential-Pair API tests."""

    def test_credential_pair_invalid_credentials_returns_401(self):
        """POST /api/terminal/credential-pair with invalid credentials returns 401."""
        response = requests.post(
            f"{BASE_URL}/api/terminal/credential-pair",
            json={
                "email": "invalid@example.com",
                "password": "wrongpassword"
            }
        )
        assert response.status_code == 401, f"Expected 401, got {response.status_code}: {response.text}"
        
        print("✓ POST /api/terminal/credential-pair returns 401 for invalid credentials")

    def test_credential_pair_missing_fields_returns_400(self):
        """POST /api/terminal/credential-pair with missing fields returns 400."""
        # Missing password
        response = requests.post(
            f"{BASE_URL}/api/terminal/credential-pair",
            json={"email": "test@example.com"}
        )
        assert response.status_code == 400, f"Expected 400, got {response.status_code}"
        
        # Missing email
        response = requests.post(
            f"{BASE_URL}/api/terminal/credential-pair",
            json={"password": "test123"}
        )
        assert response.status_code == 400, f"Expected 400, got {response.status_code}"
        
        print("✓ POST /api/terminal/credential-pair returns 400 for missing fields")

    def test_credential_pair_admin_no_branch_returns_select_branch(self):
        """POST /api/terminal/credential-pair with admin credentials (no branch) returns select_branch."""
        response = requests.post(
            f"{BASE_URL}/api/terminal/credential-pair",
            json={
                "email": SUPER_ADMIN_EMAIL,
                "password": SUPER_ADMIN_PASSWORD
            }
        )
        assert response.status_code == 200, f"Failed: {response.text}"
        data = response.json()
        
        assert data["status"] == "select_branch", f"Expected select_branch status, got {data.get('status')}"
        assert "branches" in data, "Response missing 'branches'"
        assert isinstance(data["branches"], list), "'branches' should be a list"
        assert "user_name" in data, "Response missing 'user_name'"
        assert data.get("is_admin") == True, "Expected is_admin=True"
        
        print(f"✓ POST /api/terminal/credential-pair (admin, no branch) returned select_branch with {len(data['branches'])} branches")
        return data["branches"]

    def test_credential_pair_admin_with_branch_returns_paired(self):
        """POST /api/terminal/credential-pair with admin + branch_id returns paired status."""
        # First get branches
        response = requests.post(
            f"{BASE_URL}/api/terminal/credential-pair",
            json={
                "email": SUPER_ADMIN_EMAIL,
                "password": SUPER_ADMIN_PASSWORD
            }
        )
        branches = response.json().get("branches", [])
        
        if not branches:
            pytest.skip("No branches available for testing")
        
        branch_id = branches[0]["id"]
        
        # Now pair with branch
        response = requests.post(
            f"{BASE_URL}/api/terminal/credential-pair",
            json={
                "email": SUPER_ADMIN_EMAIL,
                "password": SUPER_ADMIN_PASSWORD,
                "branch_id": branch_id
            }
        )
        assert response.status_code == 200, f"Failed: {response.text}"
        data = response.json()
        
        assert data["status"] == "paired", f"Expected paired status, got {data.get('status')}"
        assert "token" in data, "Response missing 'token'"
        assert "terminal_id" in data, "Response missing 'terminal_id'"
        assert data["branch_id"] == branch_id, f"Branch ID mismatch"
        assert "branch_name" in data, "Response missing 'branch_name'"
        assert "user_name" in data, "Response missing 'user_name'"
        
        print(f"✓ POST /api/terminal/credential-pair (admin + branch) returned paired with terminal_id={data['terminal_id']}")


class TestExistingPairingCodeFlow:
    """Test existing pairing code flow still works (no regression)."""

    def test_generate_code_works(self):
        """POST /api/terminal/generate-code still works."""
        response = requests.post(f"{BASE_URL}/api/terminal/generate-code")
        assert response.status_code == 200, f"Failed: {response.text}"
        data = response.json()
        
        assert "code" in data, "Response missing 'code'"
        assert len(data["code"]) == 6, f"Code should be 6 chars, got {len(data['code'])}"
        assert "expires_in" in data, "Response missing 'expires_in'"
        
        print(f"✓ POST /api/terminal/generate-code returned code={data['code']}")
        return data["code"]

    def test_poll_code_works(self):
        """GET /api/terminal/poll/{code} still works."""
        # Generate a code first
        response = requests.post(f"{BASE_URL}/api/terminal/generate-code")
        code = response.json()["code"]
        
        # Poll it
        response = requests.get(f"{BASE_URL}/api/terminal/poll/{code}")
        assert response.status_code == 200, f"Failed: {response.text}"
        data = response.json()
        
        assert "status" in data, "Response missing 'status'"
        assert data["status"] == "pending", f"Expected pending status, got {data['status']}"
        
        print(f"✓ GET /api/terminal/poll/{code} returned status=pending")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

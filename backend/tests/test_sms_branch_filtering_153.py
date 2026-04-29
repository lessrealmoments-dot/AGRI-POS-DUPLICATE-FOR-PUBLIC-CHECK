"""
Test Suite for SMS Branch-Level Filtering Features (Iteration 153)

Tests:
1. GET /api/sms/conversations with branch_id param — filters correctly (phones branch has messaged)
2. GET /api/sms/conversations without branch_id — admin consolidated view shows all
3. POST /api/sms/send — auto-appends server-side signature '\\n\\n- CompanyName | BranchName'
4. POST /api/sms/send — stores sent_by_name (logged-in user's full_name)
5. GET /api/sms/conversation/{phone} — returns branch_id, branch_name, sent_by_name on outgoing messages
6. Branch filtering: conversations filtered by branch show only branch-scoped phones
7. Branch filtering: admin consolidated view includes all phones across branches
"""

import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', 'https://agribooks-pos-1.preview.emergentagent.com').rstrip('/')

SUPER_ADMIN_EMAIL = "janmarkeahig@gmail.com"
SUPER_ADMIN_PASSWORD = "Aa@58798546521325"

BRANCH_1_ID = "c435277f-9fc7-4d83-83e7-38be5b4423ac"
BRANCH_1_NAME = "Branch 1"
COMPANY_NAME = "Sibugay Agricultural Supply"

# Test phone number (Branch 1 already has messages to these phones)
BRANCH1_KNOWN_PHONE = "09952568450"


@pytest.fixture(scope="module")
def auth_token():
    """Get authentication token for super admin."""
    response = requests.post(f"{BASE_URL}/api/auth/login", json={
        "email": SUPER_ADMIN_EMAIL,
        "password": SUPER_ADMIN_PASSWORD
    })
    assert response.status_code == 200, f"Login failed: {response.text}"
    return response.json()["token"]


@pytest.fixture(scope="module")
def auth_headers(auth_token):
    """Return headers with auth token."""
    return {
        "Authorization": f"Bearer {auth_token}",
        "Content-Type": "application/json"
    }


# ── Conversations Filtering Tests ─────────────────────────────────────────────

class TestConversationsFiltering:
    """GET /api/sms/conversations branch_id filtering tests."""

    def test_conversations_no_branch_id_returns_all(self, auth_headers):
        """GET /api/sms/conversations without branch_id returns all conversations (admin consolidated)."""
        response = requests.get(f"{BASE_URL}/api/sms/conversations", headers=auth_headers)
        assert response.status_code == 200, f"Failed: {response.text}"
        data = response.json()

        assert isinstance(data, list), "Response should be a list"
        print(f"✓ GET /api/sms/conversations (no branch_id) returned {len(data)} conversations")

        # Verify each conversation has expected fields
        for convo in data:
            assert "phone" in convo, "Conversation missing 'phone'"
            assert "customer_name" in convo, "Conversation missing 'customer_name'"
            assert "last_message" in convo, "Conversation missing 'last_message'"
            assert "branch_ids" in convo, "Conversation missing 'branch_ids'"
            assert "branch_names" in convo, "Conversation missing 'branch_names'"

    def test_conversations_with_branch_id_filters_correctly(self, auth_headers):
        """GET /api/sms/conversations?branch_id=X only returns phones that branch has messaged."""
        # Without filter
        all_resp = requests.get(f"{BASE_URL}/api/sms/conversations", headers=auth_headers)
        all_convos = all_resp.json()
        all_phones = {c["phone"] for c in all_convos}

        # With branch filter
        branch_resp = requests.get(
            f"{BASE_URL}/api/sms/conversations",
            headers=auth_headers,
            params={"branch_id": BRANCH_1_ID}
        )
        assert branch_resp.status_code == 200, f"Failed: {branch_resp.text}"
        branch_convos = branch_resp.json()
        branch_phones = {c["phone"] for c in branch_convos}

        print(f"  All phones: {all_phones}")
        print(f"  Branch 1 phones: {branch_phones}")

        # Branch filtered should be a subset of all
        assert branch_phones.issubset(all_phones), "Branch phones should be subset of all phones"
        # Branch 1 phones should be fewer or equal to all (can be equal if all messages are from branch 1)
        assert len(branch_convos) <= len(all_convos), "Branch filtered list should not exceed all conversations"

        print(f"✓ GET /api/sms/conversations?branch_id={BRANCH_1_ID} returned {len(branch_convos)} filtered conversations")

    def test_conversations_branch_filter_only_shows_branch_phones(self, auth_headers):
        """Branch filtered conversations only contain phones that Branch 1 has messaged."""
        branch_resp = requests.get(
            f"{BASE_URL}/api/sms/conversations",
            headers=auth_headers,
            params={"branch_id": BRANCH_1_ID}
        )
        assert branch_resp.status_code == 200, f"Failed: {branch_resp.text}"
        branch_convos = branch_resp.json()

        # Verify all returned conversations include branch_ids containing BRANCH_1_ID
        for convo in branch_convos:
            branch_ids_in_convo = convo.get("branch_ids", [])
            assert BRANCH_1_ID in branch_ids_in_convo, \
                f"Conversation for phone={convo['phone']} should include Branch 1 ID in branch_ids, got: {branch_ids_in_convo}"

        print(f"✓ All {len(branch_convos)} branch-filtered conversations include BRANCH_1_ID in branch_ids")

    def test_conversations_unknown_branch_returns_empty(self, auth_headers):
        """GET /api/sms/conversations?branch_id=nonexistent returns empty list."""
        response = requests.get(
            f"{BASE_URL}/api/sms/conversations",
            headers=auth_headers,
            params={"branch_id": "nonexistent-branch-id-xyz"}
        )
        assert response.status_code == 200, f"Failed: {response.text}"
        data = response.json()

        assert data == [], f"Expected empty list for unknown branch, got: {data}"
        print("✓ GET /api/sms/conversations?branch_id=nonexistent returned empty list")

    def test_conversations_has_branch_names_field(self, auth_headers):
        """Conversations list includes branch_ids and branch_names for multi-branch badge rendering."""
        branch_resp = requests.get(
            f"{BASE_URL}/api/sms/conversations",
            headers=auth_headers,
            params={"branch_id": BRANCH_1_ID}
        )
        assert branch_resp.status_code == 200, f"Failed: {branch_resp.text}"
        branch_convos = branch_resp.json()

        for convo in branch_convos:
            assert isinstance(convo.get("branch_ids"), list), "branch_ids should be a list"
            assert isinstance(convo.get("branch_names"), list), "branch_names should be a list"

        print(f"✓ Conversations have branch_ids and branch_names list fields for badge rendering")


# ── Manual Send Signature Tests ───────────────────────────────────────────────

class TestManualSendSignature:
    """POST /api/sms/send auto-signature and sent_by_name tests."""

    def test_send_auto_appends_signature_with_branch(self, auth_headers):
        """POST /api/sms/send appends '\\n\\n- CompanyName | BranchName' to message when branch provided."""
        original_msg = "Test message for signature test"
        response = requests.post(
            f"{BASE_URL}/api/sms/send",
            headers=auth_headers,
            json={
                "phone": "09123456789",
                "message": original_msg,
                "branch_id": BRANCH_1_ID,
                "branch_name": BRANCH_1_NAME,
                "customer_name": "TEST Customer",
                "customer_id": ""
            }
        )
        assert response.status_code == 200, f"Failed: {response.text}"
        data = response.json()

        stored_message = data["message"]
        expected_suffix = f"\n\n- {COMPANY_NAME} | {BRANCH_1_NAME}"

        assert stored_message.startswith(original_msg), \
            f"Message should start with original text. Got: {stored_message}"
        assert stored_message.endswith(expected_suffix), \
            f"Message should end with '{expected_suffix}'. Got: '{stored_message}'"
        assert stored_message == original_msg + expected_suffix, \
            f"Full message mismatch. Expected: '{original_msg + expected_suffix}', Got: '{stored_message}'"

        print(f"✓ POST /api/sms/send correctly appended signature: '{expected_suffix}'")

    def test_send_auto_appends_signature_without_branch(self, auth_headers):
        """POST /api/sms/send appends '\\n\\n- CompanyName' when no branch_name provided."""
        original_msg = "Test message no branch"
        response = requests.post(
            f"{BASE_URL}/api/sms/send",
            headers=auth_headers,
            json={
                "phone": "09123456799",
                "message": original_msg,
                "branch_id": "",
                "branch_name": "",
                "customer_name": "TEST No Branch",
                "customer_id": ""
            }
        )
        assert response.status_code == 200, f"Failed: {response.text}"
        data = response.json()

        stored_message = data["message"]
        expected_suffix = f"\n\n- {COMPANY_NAME}"

        assert stored_message.endswith(expected_suffix), \
            f"Message should end with company-only signature '{expected_suffix}'. Got: '{stored_message}'"

        print(f"✓ POST /api/sms/send correctly appended company-only signature: '{expected_suffix}'")

    def test_send_stores_sent_by_name(self, auth_headers):
        """POST /api/sms/send stores sent_by_name as the logged-in user's full_name."""
        response = requests.post(
            f"{BASE_URL}/api/sms/send",
            headers=auth_headers,
            json={
                "phone": "09111222333",
                "message": "Test sent_by_name",
                "branch_id": BRANCH_1_ID,
                "branch_name": BRANCH_1_NAME,
                "customer_name": "TEST Customer 2",
                "customer_id": ""
            }
        )
        assert response.status_code == 200, f"Failed: {response.text}"
        data = response.json()

        # sent_by_name should be present and non-empty (should be "Platform Admin" or email)
        assert "sent_by_name" in data, "Response missing 'sent_by_name' field"
        assert data["sent_by_name"], "sent_by_name should not be empty"

        # Super admin's full_name is "Platform Admin"
        sent_by = data["sent_by_name"]
        assert isinstance(sent_by, str), "sent_by_name should be a string"
        assert len(sent_by) > 0, "sent_by_name should not be empty"

        print(f"✓ POST /api/sms/send stored sent_by_name='{sent_by}'")

    def test_send_stores_branch_id_and_name(self, auth_headers):
        """POST /api/sms/send stores branch_id and branch_name correctly."""
        response = requests.post(
            f"{BASE_URL}/api/sms/send",
            headers=auth_headers,
            json={
                "phone": "09222333444",
                "message": "Test branch storage",
                "branch_id": BRANCH_1_ID,
                "branch_name": BRANCH_1_NAME,
                "customer_name": "TEST Customer 3",
                "customer_id": ""
            }
        )
        assert response.status_code == 200, f"Failed: {response.text}"
        data = response.json()

        assert data["branch_id"] == BRANCH_1_ID, f"branch_id should be '{BRANCH_1_ID}', got: {data.get('branch_id')}"
        assert data["branch_name"] == BRANCH_1_NAME, f"branch_name should be '{BRANCH_1_NAME}', got: {data.get('branch_name')}"

        print(f"✓ POST /api/sms/send stored branch_id='{BRANCH_1_ID}' and branch_name='{BRANCH_1_NAME}'")

    def test_send_requires_phone_and_message(self, auth_headers):
        """POST /api/sms/send returns 400 if phone or message missing."""
        response = requests.post(
            f"{BASE_URL}/api/sms/send",
            headers=auth_headers,
            json={"message": "Test without phone"}
        )
        assert response.status_code == 400, f"Expected 400, got {response.status_code}"

        response = requests.post(
            f"{BASE_URL}/api/sms/send",
            headers=auth_headers,
            json={"phone": "09123456789"}
        )
        assert response.status_code == 400, f"Expected 400, got {response.status_code}"
        print("✓ POST /api/sms/send returns 400 for missing phone/message")


# ── Conversation Thread Tests ─────────────────────────────────────────────────

class TestConversationThread:
    """GET /api/sms/conversation/{phone} returns branch_id, branch_name, sent_by_name."""

    def test_conversation_thread_has_branch_fields_on_outgoing(self, auth_headers):
        """GET /api/sms/conversation/{phone} returns branch_id, branch_name, sent_by_name on outgoing messages."""
        # First send a message with branch info
        test_phone = "09333444555"
        original_msg = "Test thread branch fields"
        send_resp = requests.post(
            f"{BASE_URL}/api/sms/send",
            headers=auth_headers,
            json={
                "phone": test_phone,
                "message": original_msg,
                "branch_id": BRANCH_1_ID,
                "branch_name": BRANCH_1_NAME,
                "customer_name": "TEST Thread Customer",
                "customer_id": ""
            }
        )
        assert send_resp.status_code == 200, f"Send failed: {send_resp.text}"

        # Now fetch the thread
        thread_resp = requests.get(
            f"{BASE_URL}/api/sms/conversation/{test_phone}",
            headers=auth_headers
        )
        assert thread_resp.status_code == 200, f"Thread fetch failed: {thread_resp.text}"
        data = thread_resp.json()

        assert "messages" in data, "Response missing 'messages'"
        assert "phone" in data, "Response missing 'phone'"
        assert "customer_name" in data, "Response missing 'customer_name'"

        # Find our outgoing message
        out_msgs = [m for m in data["messages"] if m["direction"] == "out"]
        assert len(out_msgs) > 0, "Should have at least one outgoing message"

        # Check the most recent outgoing message has branch fields
        latest = out_msgs[-1]
        assert "branch_id" in latest, "Outgoing message missing 'branch_id'"
        assert "branch_name" in latest, "Outgoing message missing 'branch_name'"
        assert "sent_by_name" in latest, "Outgoing message missing 'sent_by_name'"

        assert latest["branch_id"] == BRANCH_1_ID, f"Expected branch_id={BRANCH_1_ID}, got: {latest.get('branch_id')}"
        assert latest["branch_name"] == BRANCH_1_NAME, f"Expected branch_name={BRANCH_1_NAME}, got: {latest.get('branch_name')}"

        print(f"✓ GET /api/sms/conversation/{test_phone} outgoing messages have branch_id, branch_name, sent_by_name")
        print(f"  branch_id: {latest['branch_id']}, branch_name: {latest['branch_name']}, sent_by_name: {latest.get('sent_by_name')}")

    def test_conversation_thread_signature_in_message(self, auth_headers):
        """Outgoing message in thread contains auto-appended signature."""
        test_phone = "09444555666"
        original_msg = "Thread signature verification"
        send_resp = requests.post(
            f"{BASE_URL}/api/sms/send",
            headers=auth_headers,
            json={
                "phone": test_phone,
                "message": original_msg,
                "branch_id": BRANCH_1_ID,
                "branch_name": BRANCH_1_NAME,
                "customer_name": "TEST Thread Sig",
                "customer_id": ""
            }
        )
        assert send_resp.status_code == 200, f"Send failed: {send_resp.text}"

        thread_resp = requests.get(
            f"{BASE_URL}/api/sms/conversation/{test_phone}",
            headers=auth_headers
        )
        data = thread_resp.json()
        out_msgs = [m for m in data["messages"] if m["direction"] == "out"]
        latest = out_msgs[-1]

        expected_suffix = f"\n\n- {COMPANY_NAME} | {BRANCH_1_NAME}"
        assert expected_suffix in latest["message"], \
            f"Message should contain signature '{expected_suffix}'. Got: '{latest['message']}'"

        print(f"✓ Thread outgoing message contains auto-signature: '{expected_suffix}'")

    def test_conversation_thread_branch1_known_phone(self, auth_headers):
        """GET /api/sms/conversation/{phone} for known Branch 1 phone returns outgoing with branch fields."""
        # Use a phone we know Branch 1 has messaged
        thread_resp = requests.get(
            f"{BASE_URL}/api/sms/conversation/{BRANCH1_KNOWN_PHONE}",
            headers=auth_headers
        )
        assert thread_resp.status_code == 200, f"Thread fetch failed: {thread_resp.text}"
        data = thread_resp.json()

        assert len(data["messages"]) > 0, f"Should have messages for {BRANCH1_KNOWN_PHONE}"

        out_msgs = [m for m in data["messages"] if m["direction"] == "out"]
        assert len(out_msgs) > 0, f"Should have outgoing messages for {BRANCH1_KNOWN_PHONE}"

        for m in out_msgs:
            assert "branch_id" in m, f"Outgoing message missing branch_id: {m}"
            assert "branch_name" in m, f"Outgoing message missing branch_name: {m}"

        print(f"✓ GET /api/sms/conversation/{BRANCH1_KNOWN_PHONE} outgoing messages have branch fields")


# ── Settings / Company Name Tests ─────────────────────────────────────────────

class TestSettingsAndCompanyName:
    """Verify settings endpoints for company name used in signatures."""

    def test_business_info_endpoint_returns_company_name(self, auth_headers):
        """GET /api/settings/business-info returns business_name for signature hint."""
        response = requests.get(f"{BASE_URL}/api/settings/business-info", headers=auth_headers)
        assert response.status_code == 200, f"Failed: {response.text}"
        data = response.json()

        # Check for business_name field (used by frontend for signature hint)
        assert "business_name" in data, f"Response missing 'business_name'. Got keys: {list(data.keys())}"
        business_name = data.get("business_name", "")
        print(f"✓ GET /api/settings/business-info returned business_name='{business_name}'")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

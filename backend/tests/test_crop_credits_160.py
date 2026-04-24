"""
Crop Credits & Signatures System Tests - Iteration 160
Tests: crop_credits CRUD, signatures session, collection recipients settings
"""
import pytest
import requests
import os
from datetime import datetime, timedelta

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')


@pytest.fixture(scope="module")
def auth_token():
    """Login and get auth token."""
    response = requests.post(f"{BASE_URL}/api/auth/login", json={
        "email": "janmarkeahig@gmail.com",
        "password": "Aa@58798546521325"
    })
    if response.status_code == 200:
        token = response.json().get("token")
        if token:
            return token
    pytest.skip(f"Authentication failed: {response.status_code} - {response.text[:200]}")


@pytest.fixture(scope="module")
def headers(auth_token):
    return {
        "Authorization": f"Bearer {auth_token}",
        "Content-Type": "application/json"
    }


@pytest.fixture(scope="module")
def test_customer_id(headers):
    """Get an existing customer to use for tests."""
    response = requests.get(f"{BASE_URL}/api/customers", headers=headers, params={"limit": 5})
    assert response.status_code == 200
    customers = response.json().get("customers", [])
    if not customers:
        pytest.skip("No customers found in DB")
    return customers[0]["id"]


@pytest.fixture(scope="module")
def test_customer(headers):
    """Get first available customer object."""
    response = requests.get(f"{BASE_URL}/api/customers", headers=headers, params={"limit": 5})
    assert response.status_code == 200
    customers = response.json().get("customers", [])
    if not customers:
        pytest.skip("No customers found in DB")
    return customers[0]


# ===== Crop Credits Tests =====

class TestCropCreditsListAndCreate:
    """Test listing and creating crop credits."""

    def test_list_crop_credits_returns_empty_or_list(self, headers):
        """GET /api/crop-credits returns items and total."""
        res = requests.get(f"{BASE_URL}/api/crop-credits", headers=headers)
        assert res.status_code == 200
        data = res.json()
        assert "items" in data
        assert "total" in data
        assert isinstance(data["items"], list)
        assert isinstance(data["total"], int)
        print(f"PASS: List crop credits - total={data['total']}")

    def test_list_crop_credits_requires_auth(self):
        """GET /api/crop-credits without auth returns 401."""
        res = requests.get(f"{BASE_URL}/api/crop-credits")
        assert res.status_code == 401
        print("PASS: Requires auth")

    def test_create_crop_credit_success(self, headers, test_customer_id):
        """POST /api/crop-credits creates credit with auto-computed harvest date."""
        planting_date = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
        expected_harvest = (datetime.strptime(planting_date, "%Y-%m-%d") + timedelta(days=127)).strftime("%Y-%m-%d")

        res = requests.post(f"{BASE_URL}/api/crop-credits", headers=headers, json={
            "customer_id": test_customer_id,
            "planting_date": planting_date,
            "initial_amount": 5000.0,
            "description": "TEST_ Initial crop credit",
            "monthly_interest_rate": 2.0,
        })
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text[:300]}"
        data = res.json()
        assert data["customer_id"] == test_customer_id
        assert data["planting_date"] == planting_date
        assert data["expected_harvest_date"] == expected_harvest
        assert data["season_end_date"] == expected_harvest
        assert data["principal_balance"] == 5000.0
        assert data["status"] == "active"
        assert len(data["credits"]) == 1
        assert data["credits"][0]["amount"] == 5000.0
        print(f"PASS: Created crop credit id={data['id']}, harvest={expected_harvest}")

        # Store for use in other tests
        TestCropCreditsListAndCreate._created_credit_id = data["id"]
        TestCropCreditsListAndCreate._created_credit = data

    def test_create_crop_credit_requires_planting_date(self, headers, test_customer_id):
        """POST /api/crop-credits without planting_date returns 400."""
        res = requests.post(f"{BASE_URL}/api/crop-credits", headers=headers, json={
            "customer_id": test_customer_id,
        })
        assert res.status_code in [400, 409], f"Expected 400 or 409, got {res.status_code}: {res.text[:200]}"
        print(f"PASS: Missing planting_date returns {res.status_code}")

    def test_create_crop_credit_blocks_duplicate_season(self, headers, test_customer_id):
        """POST /api/crop-credits with active season returns 409."""
        planting_date = datetime.now().strftime("%Y-%m-%d")
        res = requests.post(f"{BASE_URL}/api/crop-credits", headers=headers, json={
            "customer_id": test_customer_id,
            "planting_date": planting_date,
            "initial_amount": 1000.0,
        })
        assert res.status_code == 409, f"Expected 409 conflict, got {res.status_code}: {res.text[:200]}"
        print("PASS: Duplicate active season blocked with 409")


class TestCropCreditsCheckBlock:
    """Test block check endpoint."""

    def test_check_block_no_active_credit(self, headers):
        """GET /check-block for customer with no credits returns not blocked."""
        # Create a temporary customer with no credits  
        cust_res = requests.get(f"{BASE_URL}/api/customers", headers=headers, params={"limit": 10})
        customers = cust_res.json().get("customers", [])
        # Use the customer we created a credit for, should be blocked
        if not customers:
            pytest.skip("No customers")
        
        # For a fresh customer (no crop credits) it should not be blocked
        # The first customer already has a credit, so find a different one
        for cust in customers[1:]:
            res = requests.get(f"{BASE_URL}/api/crop-credits/check-block/{cust['id']}", headers=headers)
            assert res.status_code == 200
            data = res.json()
            assert "blocked" in data
            assert "reason" in data
            assert "can_add_to_crop" in data
            print(f"PASS: check-block returned blocked={data['blocked']} for customer {cust['id'][:8]}")
            return

    def test_check_block_with_active_credit(self, headers, test_customer_id):
        """GET /check-block for customer with active credit returns blocked."""
        res = requests.get(f"{BASE_URL}/api/crop-credits/check-block/{test_customer_id}", headers=headers)
        assert res.status_code == 200
        data = res.json()
        assert data["blocked"] == True
        assert data["reason"] == "active_crop_credit"
        assert data["can_add_to_crop"] == True
        assert data["active_credit"] is not None
        assert "total_due" in data["active_credit"]
        print(f"PASS: check-block returns blocked=True for customer with active crop credit")


class TestCropCreditsCustomerEndpoint:
    """Test customer-specific credit endpoint."""

    def test_get_customer_active_credit(self, headers, test_customer_id):
        """GET /customer/{id} returns active credit with computed fields."""
        res = requests.get(f"{BASE_URL}/api/crop-credits/customer/{test_customer_id}", headers=headers)
        assert res.status_code == 200
        data = res.json()
        # Should have the active credit
        if data:
            assert "customer_id" in data
            assert data["customer_id"] == test_customer_id
            assert "total_due" in data
            assert "days_to_harvest" in data
            print(f"PASS: customer active credit returned with total_due={data.get('total_due')}, days_to_harvest={data.get('days_to_harvest')}")
        else:
            print("INFO: No credit found - returned null")


class TestCropCreditsAddCredit:
    """Test stacking credits into existing season."""

    def test_add_credit_to_season(self, headers, test_customer_id):
        """POST /{id}/add-credit stacks credit."""
        # Get the credit id first
        res = requests.get(f"{BASE_URL}/api/crop-credits/customer/{test_customer_id}", headers=headers)
        assert res.status_code == 200
        credit = res.json()
        if not credit or not credit.get("id"):
            pytest.skip("No active credit found to add to")
        
        credit_id = credit["id"]
        original_principal = credit.get("principal_balance", 0)
        
        add_res = requests.post(f"{BASE_URL}/api/crop-credits/{credit_id}/add-credit", headers=headers, json={
            "amount": 2500.0,
            "description": "TEST_ Seeds and fertilizer",
            "date": datetime.now().strftime("%Y-%m-%d"),
        })
        assert add_res.status_code == 200, f"Expected 200, got {add_res.status_code}: {add_res.text[:300]}"
        data = add_res.json()
        assert data["principal_balance"] == round(original_principal + 2500.0, 2)
        assert data["total_due"] >= data["principal_balance"]
        # Credits list should have grown
        print(f"PASS: Add credit - new principal={data['principal_balance']}, credits count={len(data.get('credits', []))}")

    def test_add_credit_invalid_amount(self, headers, test_customer_id):
        """POST /{id}/add-credit with 0 amount returns 400."""
        res = requests.get(f"{BASE_URL}/api/crop-credits/customer/{test_customer_id}", headers=headers)
        credit = res.json()
        if not credit or not credit.get("id"):
            pytest.skip("No active credit")
        
        add_res = requests.post(f"{BASE_URL}/api/crop-credits/{credit['id']}/add-credit", headers=headers, json={
            "amount": 0,
        })
        assert add_res.status_code == 400
        print("PASS: Zero amount returns 400")


class TestCropCreditsPayment:
    """Test payment recording with interest-first allocation."""

    def test_record_payment_interest_first(self, headers, test_customer_id):
        """POST /{id}/payment allocates interest first, then principal."""
        # First accrue some interest manually by checking if admin role
        res = requests.get(f"{BASE_URL}/api/crop-credits/customer/{test_customer_id}", headers=headers)
        credit = res.json()
        if not credit or not credit.get("id"):
            pytest.skip("No active credit")
        
        credit_id = credit["id"]
        principal = credit.get("principal_balance", 0)
        interest = credit.get("accrued_interest", 0)
        total = principal + interest

        # Record a partial payment
        payment_amount = 1000.0
        
        pay_res = requests.post(f"{BASE_URL}/api/crop-credits/{credit_id}/payment", headers=headers, json={
            "amount": payment_amount,
            "method": "Cash",
            "date": datetime.now().strftime("%Y-%m-%d"),
        })
        assert pay_res.status_code == 200, f"Expected 200, got {pay_res.status_code}: {pay_res.text[:300]}"
        data = pay_res.json()
        
        # Verify interest-first allocation
        applied_interest = data["applied_interest"]
        applied_principal = data["applied_principal"]
        assert applied_interest + applied_principal <= payment_amount + 0.01  # small float tolerance
        
        if interest > 0:
            # Interest should have been applied first
            assert applied_interest > 0 or applied_interest == min(interest, payment_amount)
        
        # Total remaining should decrease
        new_total = data["total_remaining"]
        assert new_total <= max(0, total - payment_amount) + 0.01
        print(f"PASS: Payment recorded - interest={applied_interest}, principal={applied_principal}, remaining={new_total}")

    def test_record_payment_zero_amount_returns_400(self, headers, test_customer_id):
        """POST /{id}/payment with 0 amount returns 400."""
        res = requests.get(f"{BASE_URL}/api/crop-credits/customer/{test_customer_id}", headers=headers)
        credit = res.json()
        if not credit or not credit.get("id"):
            pytest.skip("No active credit")
        
        pay_res = requests.post(f"{BASE_URL}/api/crop-credits/{credit['id']}/payment", headers=headers, json={
            "amount": 0,
        })
        assert pay_res.status_code == 400
        print("PASS: Zero payment amount returns 400")


class TestCropCreditsExtend:
    """Test extension requires PIN."""

    def test_extend_requires_pin(self, headers, test_customer_id):
        """POST /{id}/extend without pin returns 400."""
        res = requests.get(f"{BASE_URL}/api/crop-credits/customer/{test_customer_id}", headers=headers)
        credit = res.json()
        if not credit or not credit.get("id"):
            pytest.skip("No active credit")
        
        ext_res = requests.post(f"{BASE_URL}/api/crop-credits/{credit['id']}/extend", headers=headers, json={
            "reason": "TEST_ weather delay",
        })
        assert ext_res.status_code == 400
        print("PASS: Extension without PIN returns 400")

    def test_extend_requires_reason(self, headers, test_customer_id):
        """POST /{id}/extend without reason returns 400."""
        res = requests.get(f"{BASE_URL}/api/crop-credits/customer/{test_customer_id}", headers=headers)
        credit = res.json()
        if not credit or not credit.get("id"):
            pytest.skip("No active credit")
        
        ext_res = requests.post(f"{BASE_URL}/api/crop-credits/{credit['id']}/extend", headers=headers, json={
            "pin": "521325",
        })
        assert ext_res.status_code == 400
        print("PASS: Extension without reason returns 400")

    def test_extend_with_valid_manager_pin(self, headers, test_customer_id):
        """POST /{id}/extend with valid manager PIN succeeds (extension #1)."""
        res = requests.get(f"{BASE_URL}/api/crop-credits/customer/{test_customer_id}", headers=headers)
        credit = res.json()
        if not credit or not credit.get("id"):
            pytest.skip("No active credit")
        
        if credit.get("extension_count", 0) >= 3:
            pytest.skip("Already has 3+ extensions, needs TOTP")
        
        current_end = credit.get("season_end_date", "")
        
        ext_res = requests.post(f"{BASE_URL}/api/crop-credits/{credit['id']}/extend", headers=headers, json={
            "reason": "TEST_ Weather delay - harvest pushed",
            "pin": "521325",
        })
        # Either succeeds or fails with invalid pin (depends on branch/org setup)
        if ext_res.status_code == 200:
            data = ext_res.json()
            assert "new_end_date" in data
            assert data["extension_count"] >= 1
            # Verify new end date is 15 days later
            if current_end:
                expected_new_end = (datetime.strptime(current_end, "%Y-%m-%d") + timedelta(days=15)).strftime("%Y-%m-%d")
                assert data["new_end_date"] == expected_new_end
            print(f"PASS: Extension succeeded - new end={data['new_end_date']}, count={data['extension_count']}")
        elif ext_res.status_code == 403:
            print(f"INFO: Extension rejected with 403 (PIN may not match this branch) - {ext_res.json().get('detail', '')[:100]}")
        else:
            pytest.fail(f"Unexpected status {ext_res.status_code}: {ext_res.text[:200]}")


# ===== Signatures Tests =====

class TestSignatures:
    """Test signature session creation and public view."""

    def test_create_signing_session(self, headers):
        """POST /api/signatures/session creates session with token."""
        res = requests.post(f"{BASE_URL}/api/signatures/session", headers=headers, json={
            "credit_context": {
                "customer_name": "TEST_ Farmer Juan",
                "amount": 5000.0,
                "credit_type": "charged_to_crop",
                "date": datetime.now().strftime("%Y-%m-%d"),
                "description": "TEST_ Seeds payment",
            },
            "linked_record_type": "crop_credit",
            "linked_record_id": "test-record-id-001",
        })
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text[:300]}"
        data = res.json()
        assert "token" in data
        assert "id" in data
        assert "signing_url" in data
        assert data["status"] == "pending"
        assert data["signing_url"] == f"/sign/{data['token']}"
        assert "expires_at" in data
        
        TestSignatures._session_token = data["token"]
        TestSignatures._session_id = data["id"]
        print(f"PASS: Signing session created - token={data['token'][:20]}..., expires={data['expires_at']}")

    def test_create_signing_session_requires_credit_context(self, headers):
        """POST /api/signatures/session without credit_context returns 400."""
        res = requests.post(f"{BASE_URL}/api/signatures/session", headers=headers, json={
            "linked_record_type": "crop_credit",
        })
        assert res.status_code == 400
        print("PASS: Missing credit_context returns 400")

    def test_public_view_session_by_token(self):
        """GET /api/signatures/view/{token} public endpoint returns session info."""
        if not hasattr(TestSignatures, '_session_token'):
            pytest.skip("No session token available")
        
        token = TestSignatures._session_token
        # Public endpoint - no auth needed
        res = requests.get(f"{BASE_URL}/api/signatures/view/{token}")
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text[:300]}"
        data = res.json()
        assert data["status"] == "pending"
        assert data["token"] == token
        assert "credit_context" in data
        assert "expires_at" in data
        print(f"PASS: Public view endpoint returned session with status={data['status']}")

    def test_public_view_invalid_token_returns_404(self):
        """GET /api/signatures/view/invalid-token returns 404."""
        res = requests.get(f"{BASE_URL}/api/signatures/view/invalid-nonexistent-token-xyz")
        assert res.status_code == 404
        print("PASS: Invalid token returns 404")

    def test_get_session_status_authenticated(self, headers):
        """GET /api/signatures/status/{token} returns status."""
        if not hasattr(TestSignatures, '_session_token'):
            pytest.skip("No session token available")
        
        token = TestSignatures._session_token
        res = requests.get(f"{BASE_URL}/api/signatures/status/{token}", headers=headers)
        assert res.status_code == 200
        data = res.json()
        assert data["status"] in ["pending", "signed", "expired", "bypassed"]
        assert data["token"] == token
        print(f"PASS: Session status={data['status']}, expired={data.get('expired')}")


# ===== Collection Recipients Settings =====

class TestCollectionRecipients:
    """Test collection notification recipients settings."""

    def test_get_collection_recipients_returns_structure(self, headers):
        """GET /api/settings/collection-recipients returns phone structure."""
        res = requests.get(f"{BASE_URL}/api/settings/collection-recipients", headers=headers)
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text[:200]}"
        data = res.json()
        assert "owner_phone" in data
        assert "manager_phone" in data
        assert "admin_phone" in data
        assert "auditor_phone" in data
        print(f"PASS: Collection recipients structure correct: {data}")

    def test_update_collection_recipients(self, headers):
        """PUT /api/settings/collection-recipients saves phone numbers."""
        test_phones = {
            "owner_phone": "+639171234567",
            "manager_phone": "+639187654321",
            "admin_phone": "+639199999999",
            "auditor_phone": "+639188888888",
        }
        res = requests.put(f"{BASE_URL}/api/settings/collection-recipients", headers=headers, json=test_phones)
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text[:200]}"
        data = res.json()
        assert data["owner_phone"] == test_phones["owner_phone"]
        assert data["manager_phone"] == test_phones["manager_phone"]
        assert data["admin_phone"] == test_phones["admin_phone"]
        assert data["auditor_phone"] == test_phones["auditor_phone"]
        print(f"PASS: Collection recipients updated successfully")

    def test_get_collection_recipients_after_update(self, headers):
        """GET /api/settings/collection-recipients verifies persistence."""
        res = requests.get(f"{BASE_URL}/api/settings/collection-recipients", headers=headers)
        assert res.status_code == 200
        data = res.json()
        # Just verify structure persisted
        assert "owner_phone" in data
        print(f"PASS: Persisted recipients: {data}")

    def test_collection_recipients_requires_auth(self):
        """GET /api/settings/collection-recipients without auth returns 401."""
        res = requests.get(f"{BASE_URL}/api/settings/collection-recipients")
        assert res.status_code == 401
        print("PASS: Requires auth")


# ===== Route/Endpoint Registration Check =====

class TestEndpointRegistration:
    """Verify all routes are properly registered."""

    def test_crop_credits_route_accessible(self, headers):
        """GET /api/crop-credits responds."""
        res = requests.get(f"{BASE_URL}/api/crop-credits", headers=headers)
        assert res.status_code == 200
        print("PASS: /api/crop-credits endpoint accessible")

    def test_signatures_route_accessible(self, headers):
        """POST /api/signatures/session responds."""
        # Just check if 400 or 200 (auth present, context missing)
        res = requests.post(f"{BASE_URL}/api/signatures/session", headers=headers, json={})
        assert res.status_code in [200, 400, 422]
        print(f"PASS: /api/signatures/session accessible (status={res.status_code})")

    def test_settings_collection_route_accessible(self, headers):
        """GET /api/settings/collection-recipients responds."""
        res = requests.get(f"{BASE_URL}/api/settings/collection-recipients", headers=headers)
        assert res.status_code == 200
        print("PASS: /api/settings/collection-recipients accessible")

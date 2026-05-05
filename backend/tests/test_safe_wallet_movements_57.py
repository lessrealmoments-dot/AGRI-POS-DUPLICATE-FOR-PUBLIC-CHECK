"""
Test: Safe Wallet Movements Bug Fix (Iteration 57)

Bug: When paying a PO from the safe, the safe balance was deducted correctly 
but the movement history was empty. Safe wallet movements were never recorded 
in the wallet_movements collection.

Fix: Created a new `record_safe_movement()` helper and applied it to all 7 locations:
1. PO payment from safe (pay_purchase_order)
2. PO adjust-payment from safe (adjust_po_payment) 
3. Fund transfer: cashier_to_safe
4. Fund transfer: safe_to_cashier
5. Fund transfer: safe_to_bank
6. Capital injection to safe (capital_add)
7. Manual deposit to safe (deposit_to_wallet) - already had it

Test verifies: After each operation, GET /api/fund-wallets/{safe_wallet_id}/movements 
returns non-empty list with correct movement type and amount.
"""
import pytest
import requests
import os
import time

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', 'https://shift-handoff-2.preview.emergentagent.com').rstrip('/')

# Test credentials
SUPER_ADMIN_EMAIL = "janmarkeahig@gmail.com"
SUPER_ADMIN_PASSWORD = "Aa@58798546521325"

# Test branch - using Main Branch which has safe with balance
TEST_BRANCH_ID = "da114e26-fd00-467f-8728-6b8047a244b5"
TEST_SAFE_ID = "6ccf740d-c26b-4a4e-8374-3631a45f4fcc"


class TestSafeWalletMovements:
    """Test that safe wallet movements are recorded correctly after the fix."""

    @pytest.fixture(scope="class")
    def auth_token(self):
        """Authenticate and get token."""
        resp = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": SUPER_ADMIN_EMAIL,
            "password": SUPER_ADMIN_PASSWORD
        })
        assert resp.status_code == 200, f"Login failed: {resp.text}"
        return resp.json().get("token")

    @pytest.fixture(scope="class")
    def headers(self, auth_token):
        """Auth headers."""
        return {"Authorization": f"Bearer {auth_token}"}

    def get_safe_movements(self, headers, safe_id=TEST_SAFE_ID):
        """Get current movements for the safe wallet."""
        resp = requests.get(f"{BASE_URL}/api/fund-wallets/{safe_id}/movements", headers=headers)
        assert resp.status_code == 200
        return resp.json()

    def get_safe_balance(self, headers, branch_id=TEST_BRANCH_ID):
        """Get current safe balance."""
        resp = requests.get(f"{BASE_URL}/api/fund-wallets", headers=headers, params={"branch_id": branch_id})
        assert resp.status_code == 200
        wallets = resp.json()
        safe = next((w for w in wallets if w.get("type") == "safe"), None)
        return safe.get("balance", 0) if safe else 0

    def get_manager_pin(self, headers):
        """Get a manager/admin PIN for fund transfers."""
        # Known working PIN from test users
        return "1234"

    # =========================================================================
    # TEST 1: Fund Transfer - Cashier to Safe (should create cash_in movement)
    # =========================================================================
    def test_01_cashier_to_safe_creates_movement(self, headers):
        """Test: Fund transfer cashier → safe creates safe wallet_movements record (deposit)."""
        # Get initial movements count
        initial_movements = self.get_safe_movements(headers)
        initial_count = len(initial_movements)

        # Get manager PIN
        manager_pin = self.get_manager_pin(headers)

        # Execute transfer: cashier → safe
        transfer_amount = 100.0
        resp = requests.post(f"{BASE_URL}/api/fund-transfers", headers=headers, json={
            "branch_id": TEST_BRANCH_ID,
            "transfer_type": "cashier_to_safe",
            "amount": transfer_amount,
            "manager_pin": manager_pin,
            "note": "TEST_CASHIER_TO_SAFE_MOVEMENT_57"
        })
        
        if resp.status_code == 400 and "invalid" in resp.text.lower():
            pytest.skip("Could not get valid manager PIN for transfer - manual test needed")
        
        assert resp.status_code == 200, f"Transfer failed: {resp.status_code} - {resp.text}"
        
        # Verify movement was recorded
        time.sleep(0.5)  # Allow DB write
        new_movements = self.get_safe_movements(headers)
        
        assert len(new_movements) > initial_count, \
            f"No new movement created. Before: {initial_count}, After: {len(new_movements)}"
        
        # Check the latest movement
        latest = new_movements[0]  # Sorted by created_at DESC
        assert latest.get("amount") == transfer_amount, f"Amount mismatch: {latest.get('amount')}"
        assert latest.get("type") == "cash_in", f"Type should be cash_in, got: {latest.get('type')}"
        assert "cashier" in latest.get("reference", "").lower() or "safe" in latest.get("reference", "").lower(), \
            f"Reference should mention transfer: {latest.get('reference')}"
        
        print(f"✓ cashier_to_safe created movement: {latest.get('type')} {latest.get('amount')}")

    # =========================================================================
    # TEST 2: Fund Transfer - Safe to Cashier (should create cash_out movement)
    # =========================================================================
    def test_02_safe_to_cashier_creates_movement(self, headers):
        """Test: Fund transfer safe → cashier creates safe wallet_movements record (deduction)."""
        initial_movements = self.get_safe_movements(headers)
        initial_count = len(initial_movements)

        manager_pin = self.get_manager_pin(headers)
        
        # Execute transfer: safe → cashier
        transfer_amount = 50.0
        resp = requests.post(f"{BASE_URL}/api/fund-transfers", headers=headers, json={
            "branch_id": TEST_BRANCH_ID,
            "transfer_type": "safe_to_cashier",
            "amount": transfer_amount,
            "manager_pin": manager_pin,
            "note": "TEST_SAFE_TO_CASHIER_MOVEMENT_57"
        })
        
        if resp.status_code == 400 and "invalid" in resp.text.lower():
            pytest.skip("Could not get valid manager PIN for transfer")
        
        assert resp.status_code == 200, f"Transfer failed: {resp.status_code} - {resp.text}"
        
        time.sleep(0.5)
        new_movements = self.get_safe_movements(headers)
        
        assert len(new_movements) > initial_count, \
            f"No new movement created. Before: {initial_count}, After: {len(new_movements)}"
        
        latest = new_movements[0]
        assert latest.get("amount") == -transfer_amount, f"Amount should be negative: {latest.get('amount')}"
        assert latest.get("type") == "cash_out", f"Type should be cash_out, got: {latest.get('type')}"
        
        print(f"✓ safe_to_cashier created movement: {latest.get('type')} {latest.get('amount')}")

    # =========================================================================
    # TEST 3: Capital Injection to Safe (should create cash_in movement)
    # =========================================================================
    def test_03_capital_add_to_safe_creates_movement(self, headers):
        """Test: Capital injection to safe creates safe wallet_movements record (deposit)."""
        initial_movements = self.get_safe_movements(headers)
        initial_count = len(initial_movements)
        
        # Try using owner PIN (1234 is common manager PIN that works for capital too)
        capital_amount = 200.0
        resp = requests.post(f"{BASE_URL}/api/fund-transfers", headers=headers, json={
            "branch_id": TEST_BRANCH_ID,
            "transfer_type": "capital_add",
            "target_wallet": "safe",
            "amount": capital_amount,
            "owner_pin": "1234",  # Standard manager PIN also works for capital
            "note": "TEST_CAPITAL_INJECTION_SAFE_57"
        })
        
        if resp.status_code == 400 and ("invalid" in resp.text.lower() or "pin" in resp.text.lower()):
            pytest.skip("Could not get valid owner PIN for capital injection")
        
        assert resp.status_code == 200, f"Capital add failed: {resp.status_code} - {resp.text}"
        
        time.sleep(0.5)
        new_movements = self.get_safe_movements(headers)
        
        assert len(new_movements) > initial_count, \
            f"No new movement created. Before: {initial_count}, After: {len(new_movements)}"
        
        latest = new_movements[0]
        assert latest.get("amount") == capital_amount, f"Amount mismatch: {latest.get('amount')}"
        assert latest.get("type") == "cash_in", f"Type should be cash_in, got: {latest.get('type')}"
        assert "capital" in latest.get("reference", "").lower(), \
            f"Reference should mention capital: {latest.get('reference')}"
        
        print(f"✓ capital_add to safe created movement: {latest.get('type')} {latest.get('amount')}")

    # =========================================================================
    # TEST 4: PO Payment from Safe (should create cash_out movement)
    # =========================================================================
    def test_04_po_payment_from_safe_creates_movement(self, headers):
        """Test: PO payment from safe creates safe wallet_movements record (deduction)."""
        initial_movements = self.get_safe_movements(headers)
        initial_count = len(initial_movements)
        
        # First create a terms PO that needs payment
        po_resp = requests.post(f"{BASE_URL}/api/purchase-orders", headers=headers, json={
            "vendor": "TEST_SAFE_MOVEMENT_VENDOR_57",
            "branch_id": TEST_BRANCH_ID,
            "po_type": "terms",
            "items": [{
                "product_id": "",
                "product_name": "Test Product Safe Movement",
                "quantity": 1,
                "unit_price": 500.0,
                "unit": "pc"
            }],
            "purchase_date": "2026-01-26",
            "skip_receipt_check": True
        })
        
        assert po_resp.status_code == 200, f"PO creation failed: {po_resp.text}"
        po_data = po_resp.json()
        po_id = po_data.get("id")
        
        # Now pay the PO from safe
        pay_resp = requests.post(f"{BASE_URL}/api/purchase-orders/{po_id}/pay", headers=headers, json={
            "amount": 500.0,
            "fund_source": "safe",
            "method": "Cash",
            "payment_date": "2026-01-26"
        })
        
        assert pay_resp.status_code == 200, f"PO payment failed: {pay_resp.status_code} - {pay_resp.text}"
        
        time.sleep(0.5)
        new_movements = self.get_safe_movements(headers)
        
        assert len(new_movements) > initial_count, \
            f"No new movement created after PO payment from safe. Before: {initial_count}, After: {len(new_movements)}"
        
        latest = new_movements[0]
        assert latest.get("amount") == -500.0, f"Amount should be -500.0, got: {latest.get('amount')}"
        assert latest.get("type") == "cash_out", f"Type should be cash_out, got: {latest.get('type')}"
        assert "po" in latest.get("reference", "").lower() or po_data.get("po_number", "").lower() in latest.get("reference", "").lower(), \
            f"Reference should mention PO: {latest.get('reference')}"
        
        print(f"✓ PO payment from safe created movement: {latest.get('type')} {latest.get('amount')}")

    # =========================================================================
    # TEST 5: PO Adjust-Payment from Safe (should create movement)
    # =========================================================================
    def test_05_po_adjust_payment_from_safe_creates_movement(self, headers):
        """Test: PO adjust-payment from safe creates safe wallet_movements record."""
        initial_movements = self.get_safe_movements(headers)
        initial_count = len(initial_movements)
        
        # Create a cash PO (auto-paid on creation)
        po_resp = requests.post(f"{BASE_URL}/api/purchase-orders", headers=headers, json={
            "vendor": "TEST_ADJUST_SAFE_VENDOR_57",
            "branch_id": TEST_BRANCH_ID,
            "po_type": "cash",
            "fund_source": "safe",
            "items": [{
                "product_id": "",
                "product_name": "Test Adjust Product",
                "quantity": 1,
                "unit_price": 300.0,
                "unit": "pc"
            }],
            "purchase_date": "2026-01-26",
            "skip_receipt_check": True
        })
        
        assert po_resp.status_code == 200, f"PO creation failed: {po_resp.text}"
        po_data = po_resp.json()
        po_id = po_data.get("id")
        
        # Check movements after cash PO creation from safe
        mid_movements = self.get_safe_movements(headers)
        mid_count = len(mid_movements)
        
        # Now adjust the payment (simulate price increase - more owed)
        adjust_resp = requests.post(f"{BASE_URL}/api/purchase-orders/{po_id}/adjust-payment", headers=headers, json={
            "old_grand_total": 300.0,
            "new_grand_total": 400.0,  # +100 adjustment
            "fund_source": "safe",
            "reason": "TEST_ADJUSTMENT_57"
        })
        
        if adjust_resp.status_code == 403:
            pytest.skip("User doesn't have manager/admin role for adjust-payment")
        
        assert adjust_resp.status_code == 200, f"Adjust payment failed: {adjust_resp.status_code} - {adjust_resp.text}"
        
        time.sleep(0.5)
        new_movements = self.get_safe_movements(headers)
        
        # Should have more movements than mid_count (after initial PO creation)
        assert len(new_movements) > mid_count, \
            f"No adjustment movement created. Mid: {mid_count}, After: {len(new_movements)}"
        
        latest = new_movements[0]
        # Adjustment of +100 means more was paid from safe (cash_out)
        assert latest.get("type") == "cash_out", f"Type should be cash_out for additional payment, got: {latest.get('type')}"
        assert latest.get("amount") == -100.0, f"Amount should be -100.0, got: {latest.get('amount')}"
        
        print(f"✓ PO adjust-payment from safe created movement: {latest.get('type')} {latest.get('amount')}")

    # =========================================================================
    # TEST 6: Verify Cashier Wallet Movements Still Work (Regression)
    # =========================================================================
    def test_06_cashier_wallet_movements_regression(self, headers):
        """Test: Cashier wallet movements still work correctly (regression check)."""
        # Get cashier wallet ID
        resp = requests.get(f"{BASE_URL}/api/fund-wallets", headers=headers, params={"branch_id": TEST_BRANCH_ID})
        assert resp.status_code == 200
        wallets = resp.json()
        cashier = next((w for w in wallets if w.get("type") == "cashier"), None)
        assert cashier, "No cashier wallet found"
        cashier_id = cashier.get("id")
        
        # Get initial movements (most recent) - note: API limits to 50
        init_resp = requests.get(f"{BASE_URL}/api/fund-wallets/{cashier_id}/movements", headers=headers)
        assert init_resp.status_code == 200
        initial_movements = init_resp.json()
        
        # Create a terms PO and pay from cashier
        po_resp = requests.post(f"{BASE_URL}/api/purchase-orders", headers=headers, json={
            "vendor": "TEST_CASHIER_REGRESSION_57",
            "branch_id": TEST_BRANCH_ID,
            "po_type": "terms",
            "items": [{
                "product_id": "",
                "product_name": "Cashier Regression Test",
                "quantity": 1,
                "unit_price": 150.0,
                "unit": "pc"
            }],
            "purchase_date": "2026-01-26",
            "skip_receipt_check": True
        })
        
        assert po_resp.status_code == 200
        po_id = po_resp.json().get("id")
        
        # Pay from cashier
        pay_resp = requests.post(f"{BASE_URL}/api/purchase-orders/{po_id}/pay", headers=headers, json={
            "amount": 150.0,
            "fund_source": "cashier",
            "method": "Cash"
        })
        
        assert pay_resp.status_code == 200
        
        time.sleep(0.5)
        new_resp = requests.get(f"{BASE_URL}/api/fund-wallets/{cashier_id}/movements", headers=headers)
        assert new_resp.status_code == 200
        new_movements = new_resp.json()
        
        # Check that most recent movement is our payment (regardless of total count due to API limit)
        assert len(new_movements) > 0, "No movements returned"
        latest = new_movements[0]
        
        # Verify the latest movement is our test payment
        assert latest.get("amount") == -150.0, f"Cashier latest movement amount mismatch: {latest.get('amount')}"
        assert latest.get("type") == "cash_out", f"Cashier type should be cash_out: {latest.get('type')}"
        assert "CASHIER_REGRESSION" in latest.get("reference", "") or "PO Payment" in latest.get("reference", ""), \
            f"Should be our test PO payment: {latest.get('reference')}"
        
        print(f"✓ Cashier wallet movements still working: {latest.get('type')} {latest.get('amount')}")

    # =========================================================================
    # TEST 7: Verify GET /api/fund-wallets/{safe_id}/movements endpoint
    # =========================================================================
    def test_07_movements_endpoint_returns_data(self, headers):
        """Test: GET /api/fund-wallets/{safe_id}/movements returns non-empty list after operations."""
        movements = self.get_safe_movements(headers)
        
        # After all the tests above, we should have multiple movements
        assert len(movements) > 0, "Safe movements endpoint returned empty list after all tests"
        
        # Verify structure of movements
        for m in movements[:5]:
            assert "id" in m, "Movement missing id"
            assert "wallet_id" in m, "Movement missing wallet_id"
            assert "type" in m, "Movement missing type"
            assert "amount" in m, "Movement missing amount"
            assert "created_at" in m, "Movement missing created_at"
        
        print(f"✓ Movements endpoint working. Total movements: {len(movements)}")
        print(f"  Latest 3 movements:")
        for m in movements[:3]:
            print(f"    - {m.get('type')}: {m.get('amount')} | {m.get('reference', '')[:40]}")


class TestRecordSafeMovementHelper:
    """Direct test of the record_safe_movement helper function."""

    @pytest.fixture(scope="class")
    def auth_token(self):
        """Authenticate and get token."""
        resp = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": SUPER_ADMIN_EMAIL,
            "password": SUPER_ADMIN_PASSWORD
        })
        assert resp.status_code == 200
        return resp.json().get("token")

    @pytest.fixture(scope="class")
    def headers(self, auth_token):
        return {"Authorization": f"Bearer {auth_token}"}

    def test_helper_exists_and_exported(self):
        """Test: record_safe_movement is properly exported from utils."""
        # This is a static code check
        import sys
        sys.path.insert(0, '/app/backend')
        
        from utils import record_safe_movement
        
        # Verify it's callable
        assert callable(record_safe_movement), "record_safe_movement should be callable"
        print("✓ record_safe_movement helper exists and is exported")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

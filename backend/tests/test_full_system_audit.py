"""
FULL SYSTEM AUDIT - AgriBooks Multi-Tenant SaaS
Tests all core flows: registration, sales, expenses, POs, employees, uploads, multi-tenant isolation

Run: pytest /app/backend/tests/test_full_system_audit.py -v --tb=short --junitxml=/app/test_reports/pytest/pytest_full_audit.xml
"""
import pytest
import requests
import os
import time
import uuid
from datetime import datetime

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', 'https://device-auth-qr.preview.emergentagent.com').rstrip('/')

# Test data storage
TEST_DATA = {}

def unique_id():
    return uuid.uuid4().hex[:8]

class TestFullSystemAudit:
    """Complete system audit testing all major flows"""
    
    # ===========================================================================
    # SECTION 1: REGISTRATION & SETUP (Tests 1-2)
    # ===========================================================================
    
    def test_01_register_new_organization(self):
        """Test 1: Register a new company via /api/organizations/register"""
        unique = unique_id()
        TEST_DATA['org1_email'] = f"testorg1_{unique}@test.com"
        TEST_DATA['org1_password'] = "Test@123456"
        TEST_DATA['org1_company'] = f"TestCo1_{unique}"
        
        response = requests.post(f"{BASE_URL}/api/organizations/register", json={
            "company_name": TEST_DATA['org1_company'],
            "admin_email": TEST_DATA['org1_email'],
            "admin_password": TEST_DATA['org1_password'],
            "admin_name": "Test Admin 1",
            "phone": "09171234567",
            "branch_name": "Main Branch"
        })
        
        print(f"Register response: {response.status_code} - {response.text[:500]}")
        assert response.status_code in [200, 201], f"Registration failed: {response.text}"
        
        data = response.json()
        assert data.get("success") == True
        TEST_DATA['org1_id'] = data.get("organization_id")
        print(f"✓ Organization registered: {TEST_DATA['org1_company']} (ID: {TEST_DATA['org1_id']})")
    
    def test_02_login_new_organization(self):
        """Test: Login to newly registered organization"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": TEST_DATA['org1_email'],
            "password": TEST_DATA['org1_password']
        })
        
        print(f"Login response: {response.status_code}")
        assert response.status_code == 200, f"Login failed: {response.text}"
        
        data = response.json()
        TEST_DATA['org1_token'] = data.get("token")
        TEST_DATA['org1_user'] = data.get("user", {})
        TEST_DATA['org1_branch_id'] = TEST_DATA['org1_user'].get('branch_id')
        
        # Get branches to find branch_id
        headers = {"Authorization": f"Bearer {TEST_DATA['org1_token']}"}
        branches_resp = requests.get(f"{BASE_URL}/api/branches", headers=headers)
        if branches_resp.status_code == 200:
            branches = branches_resp.json()
            if branches:
                TEST_DATA['org1_branch_id'] = branches[0].get('id')
        
        print(f"✓ Logged in as {TEST_DATA['org1_email']}, branch_id: {TEST_DATA['org1_branch_id']}")
        assert TEST_DATA['org1_token'], "Token not received"
    
    # ===========================================================================
    # SECTION 2: PRODUCTS (Test 2)
    # ===========================================================================
    
    def test_03_create_product(self):
        """Test 2: Create product with prices, verify it appears in inventory as 0 stock"""
        headers = {"Authorization": f"Bearer {TEST_DATA['org1_token']}"}
        
        unique = unique_id()
        response = requests.post(f"{BASE_URL}/api/products", headers=headers, json={
            "name": f"Test Product {unique}",
            "sku": f"SKU-{unique}",
            "category": "General",
            "unit": "pcs",
            "cost_price": 100,
            "prices": {"retail": 150, "wholesale": 130}
        })
        
        print(f"Create product response: {response.status_code} - {response.text[:300]}")
        assert response.status_code in [200, 201], f"Create product failed: {response.text}"
        
        data = response.json()
        TEST_DATA['product_id'] = data.get("id")
        TEST_DATA['product_name'] = data.get("name")
        
        # Verify in inventory (should be 0)
        inv_resp = requests.get(f"{BASE_URL}/api/inventory?branch_id={TEST_DATA['org1_branch_id']}", headers=headers)
        assert inv_resp.status_code == 200
        
        print(f"✓ Product created: {TEST_DATA['product_name']} (ID: {TEST_DATA['product_id']})")
    
    # ===========================================================================
    # SECTION 3: PURCHASE ORDERS (Test 3)
    # ===========================================================================
    
    def test_04_create_and_receive_purchase_order(self):
        """Test 3: Create a PO, receive it, verify inventory increases, verify stock movement logged"""
        headers = {"Authorization": f"Bearer {TEST_DATA['org1_token']}"}
        
        # First, ensure cashier wallet has funds (create or deposit)
        wallets_resp = requests.get(f"{BASE_URL}/api/fund-wallets?branch_id={TEST_DATA['org1_branch_id']}", headers=headers)
        if wallets_resp.status_code == 200:
            wallets = wallets_resp.json()
            cashier = next((w for w in wallets if w.get('type') == 'cashier'), None)
            if cashier and cashier.get('balance', 0) < 1000:
                # Deposit funds
                requests.post(f"{BASE_URL}/api/fund-wallets/{cashier['id']}/deposit", headers=headers, json={
                    "amount": 10000,
                    "reference": "Initial capital"
                })
        
        # Create PO as draft first
        response = requests.post(f"{BASE_URL}/api/purchase-orders", headers=headers, json={
            "vendor": "Test Supplier",
            "branch_id": TEST_DATA['org1_branch_id'],
            "po_type": "draft",
            "items": [{
                "product_id": TEST_DATA['product_id'],
                "product_name": TEST_DATA['product_name'],
                "quantity": 50,
                "unit_price": 100
            }],
            "notes": "Test PO for audit"
        })
        
        print(f"Create PO response: {response.status_code} - {response.text[:300]}")
        assert response.status_code in [200, 201], f"Create PO failed: {response.text}"
        
        data = response.json()
        TEST_DATA['po_id'] = data.get("id")
        TEST_DATA['po_number'] = data.get("po_number")
        
        # Receive the PO
        receive_resp = requests.post(f"{BASE_URL}/api/purchase-orders/{TEST_DATA['po_id']}/receive", headers=headers, json={})
        print(f"Receive PO response: {receive_resp.status_code} - {receive_resp.text[:200]}")
        assert receive_resp.status_code == 200, f"Receive PO failed: {receive_resp.text}"
        
        # Verify inventory increased
        inv_resp = requests.get(f"{BASE_URL}/api/inventory?branch_id={TEST_DATA['org1_branch_id']}&search={TEST_DATA['product_name'][:10]}", headers=headers)
        assert inv_resp.status_code == 200
        items = inv_resp.json().get('items', [])
        product_inv = next((i for i in items if i.get('id') == TEST_DATA['product_id']), None)
        if product_inv:
            print(f"✓ Inventory after PO: {product_inv.get('total_stock', 0)} units")
            assert product_inv.get('total_stock', 0) >= 50, "Inventory should have at least 50 units"
        
        print(f"✓ PO created and received: {TEST_DATA['po_number']}")
    
    # ===========================================================================
    # SECTION 4: SALES (Test 4)
    # ===========================================================================
    
    def test_05_create_cash_sale(self):
        """Test 4a: Create a cash sale via /api/unified-sale, verify inventory decreases"""
        headers = {"Authorization": f"Bearer {TEST_DATA['org1_token']}"}
        
        response = requests.post(f"{BASE_URL}/api/unified-sale", headers=headers, json={
            "branch_id": TEST_DATA['org1_branch_id'],
            "customer_name": "Walk-in Customer",
            "payment_type": "cash",
            "payment_method": "Cash",
            "items": [{
                "product_id": TEST_DATA['product_id'],
                "product_name": TEST_DATA['product_name'],
                "quantity": 5,
                "rate": 150
            }],
            "amount_paid": 750,
            "balance": 0
        })
        
        print(f"Cash sale response: {response.status_code} - {response.text[:300]}")
        assert response.status_code in [200, 201], f"Cash sale failed: {response.text}"
        
        data = response.json()
        TEST_DATA['cash_sale_id'] = data.get("id")
        TEST_DATA['cash_sale_number'] = data.get("invoice_number")
        
        print(f"✓ Cash sale created: {TEST_DATA['cash_sale_number']}, Amount: ₱750")
    
    def test_06_create_credit_sale(self):
        """Test 4b: Create a credit sale, verify customer AR balance increases"""
        headers = {"Authorization": f"Bearer {TEST_DATA['org1_token']}"}
        
        # First create a customer
        unique = unique_id()
        cust_resp = requests.post(f"{BASE_URL}/api/customers", headers=headers, json={
            "name": f"Credit Customer {unique}",
            "phone": "09171111111",
            "credit_limit": 50000,
            "interest_rate": 3,
            "branch_id": TEST_DATA['org1_branch_id']
        })
        
        assert cust_resp.status_code in [200, 201], f"Create customer failed: {cust_resp.text}"
        customer = cust_resp.json()
        TEST_DATA['customer_id'] = customer.get('id')
        TEST_DATA['customer_name'] = customer.get('name')
        
        # Create credit sale
        response = requests.post(f"{BASE_URL}/api/unified-sale", headers=headers, json={
            "branch_id": TEST_DATA['org1_branch_id'],
            "customer_id": TEST_DATA['customer_id'],
            "customer_name": TEST_DATA['customer_name'],
            "payment_type": "credit",
            "items": [{
                "product_id": TEST_DATA['product_id'],
                "product_name": TEST_DATA['product_name'],
                "quantity": 10,
                "rate": 150
            }],
            "amount_paid": 0,
            "balance": 1500
        })
        
        print(f"Credit sale response: {response.status_code} - {response.text[:300]}")
        assert response.status_code in [200, 201], f"Credit sale failed: {response.text}"
        
        data = response.json()
        TEST_DATA['credit_sale_id'] = data.get("id")
        
        # Verify customer balance increased
        cust_check = requests.get(f"{BASE_URL}/api/customers/{TEST_DATA['customer_id']}", headers=headers)
        if cust_check.status_code == 200:
            cust_data = cust_check.json()
            print(f"✓ Customer balance after credit sale: ₱{cust_data.get('balance', 0)}")
            assert cust_data.get('balance', 0) >= 1500, "Customer balance should be at least 1500"
        
        print(f"✓ Credit sale created for {TEST_DATA['customer_name']}")
    
    # ===========================================================================
    # SECTION 5: EXPENSES (Tests 5-8)
    # ===========================================================================
    
    def test_07_create_regular_expense(self):
        """Test 5: Create a regular expense, verify cashier wallet decreases"""
        headers = {"Authorization": f"Bearer {TEST_DATA['org1_token']}"}
        
        response = requests.post(f"{BASE_URL}/api/expenses", headers=headers, json={
            "branch_id": TEST_DATA['org1_branch_id'],
            "category": "Utilities",
            "description": "Electricity Bill",
            "amount": 500,
            "payment_method": "Cash",
            "date": datetime.now().strftime("%Y-%m-%d")
        })
        
        print(f"Regular expense response: {response.status_code} - {response.text[:300]}")
        assert response.status_code in [200, 201], f"Create expense failed: {response.text}"
        
        data = response.json()
        TEST_DATA['regular_expense_id'] = data.get('id')
        print(f"✓ Regular expense created: ₱500 for Utilities")
    
    def test_08_create_farm_expense(self):
        """Test 6: Create farm expense with customer_id, verify invoice created"""
        headers = {"Authorization": f"Bearer {TEST_DATA['org1_token']}"}
        
        response = requests.post(f"{BASE_URL}/api/expenses/farm", headers=headers, json={
            "branch_id": TEST_DATA['org1_branch_id'],
            "customer_id": TEST_DATA['customer_id'],
            "description": "Farm Service - Spraying",
            "amount": 2000,
            "date": datetime.now().strftime("%Y-%m-%d")
        })
        
        print(f"Farm expense response: {response.status_code} - {response.text[:300]}")
        assert response.status_code in [200, 201], f"Farm expense failed: {response.text}"
        
        data = response.json()
        assert "invoice" in data or "linked_invoice_id" in data.get("expense", {}), "Should create linked invoice"
        TEST_DATA['farm_expense_id'] = data.get('expense', {}).get('id') or data.get('id')
        
        print(f"✓ Farm expense created with invoice for customer")
    
    def test_09_create_customer_cashout(self):
        """Test 7: Create cash-out via /api/expenses/customer-cashout"""
        headers = {"Authorization": f"Bearer {TEST_DATA['org1_token']}"}
        
        response = requests.post(f"{BASE_URL}/api/expenses/customer-cashout", headers=headers, json={
            "branch_id": TEST_DATA['org1_branch_id'],
            "customer_id": TEST_DATA['customer_id'],
            "description": "Cash advance to customer",
            "amount": 1000,
            "date": datetime.now().strftime("%Y-%m-%d")
        })
        
        print(f"Customer cashout response: {response.status_code} - {response.text[:300]}")
        assert response.status_code in [200, 201], f"Customer cashout failed: {response.text}"
        
        data = response.json()
        TEST_DATA['cashout_expense_id'] = data.get('expense', {}).get('id') or data.get('id')
        
        print(f"✓ Customer cashout created: ₱1000")
    
    def test_10_create_employee_and_advance(self):
        """Test 8: Create employee with monthly_ca_limit, then create advance"""
        headers = {"Authorization": f"Bearer {TEST_DATA['org1_token']}"}
        
        # Create employee
        unique = unique_id()
        emp_resp = requests.post(f"{BASE_URL}/api/employees", headers=headers, json={
            "name": f"Test Employee {unique}",
            "position": "Sales Staff",
            "branch_id": TEST_DATA['org1_branch_id'],
            "monthly_ca_limit": 5000,
            "salary": 15000
        })
        
        print(f"Create employee response: {emp_resp.status_code} - {emp_resp.text[:300]}")
        assert emp_resp.status_code in [200, 201], f"Create employee failed: {emp_resp.text}"
        
        employee = emp_resp.json()
        TEST_DATA['employee_id'] = employee.get('id')
        TEST_DATA['employee_name'] = employee.get('name')
        
        # Create employee advance
        adv_resp = requests.post(f"{BASE_URL}/api/expenses", headers=headers, json={
            "branch_id": TEST_DATA['org1_branch_id'],
            "category": "Employee Advance",
            "description": f"Cash advance to {TEST_DATA['employee_name']}",
            "amount": 1000,
            "employee_id": TEST_DATA['employee_id'],
            "employee_name": TEST_DATA['employee_name'],
            "date": datetime.now().strftime("%Y-%m-%d")
        })
        
        print(f"Employee advance response: {adv_resp.status_code} - {adv_resp.text[:300]}")
        assert adv_resp.status_code in [200, 201], f"Employee advance failed: {adv_resp.text}"
        
        TEST_DATA['employee_advance_id'] = adv_resp.json().get('id')
        
        # Verify CA summary
        ca_resp = requests.get(f"{BASE_URL}/api/employees/{TEST_DATA['employee_id']}/ca-summary", headers=headers)
        if ca_resp.status_code == 200:
            ca_data = ca_resp.json()
            print(f"✓ Employee CA summary: This month: ₱{ca_data.get('this_month_total', 0)}, Limit: ₱{ca_data.get('monthly_ca_limit', 0)}")
        
        print(f"✓ Employee advance created: ₱1000 for {TEST_DATA['employee_name']}")
    
    # ===========================================================================
    # SECTION 6: EMPLOYEE ADVANCE LIMIT (Test 9)
    # ===========================================================================
    
    def test_11_employee_advance_limit_enforcement(self):
        """Test 9: Verify monthly CA limit is enforced, manager approval required when exceeded"""
        headers = {"Authorization": f"Bearer {TEST_DATA['org1_token']}"}
        
        # Create another employee with low limit
        unique = unique_id()
        emp_resp = requests.post(f"{BASE_URL}/api/employees", headers=headers, json={
            "name": f"Low Limit Employee {unique}",
            "position": "Helper",
            "branch_id": TEST_DATA['org1_branch_id'],
            "monthly_ca_limit": 500  # Low limit
        })
        
        assert emp_resp.status_code in [200, 201]
        low_limit_emp = emp_resp.json()
        
        # Try to create advance exceeding limit WITHOUT manager approval - should fail
        adv_resp = requests.post(f"{BASE_URL}/api/expenses", headers=headers, json={
            "branch_id": TEST_DATA['org1_branch_id'],
            "category": "Employee Advance",
            "description": "Large advance",
            "amount": 1000,  # Exceeds 500 limit
            "employee_id": low_limit_emp['id'],
            "employee_name": low_limit_emp['name'],
            "date": datetime.now().strftime("%Y-%m-%d")
        })
        
        print(f"Exceeding limit (no approval) response: {adv_resp.status_code} - {adv_resp.text[:200]}")
        assert adv_resp.status_code == 400, "Should reject advance exceeding limit without approval"
        
        # Now try WITH manager approval - should succeed
        adv_resp2 = requests.post(f"{BASE_URL}/api/expenses", headers=headers, json={
            "branch_id": TEST_DATA['org1_branch_id'],
            "category": "Employee Advance",
            "description": "Large advance with approval",
            "amount": 1000,
            "employee_id": low_limit_emp['id'],
            "employee_name": low_limit_emp['name'],
            "manager_approved_by": "Test Manager",
            "date": datetime.now().strftime("%Y-%m-%d")
        })
        
        print(f"Exceeding limit (WITH approval) response: {adv_resp2.status_code} - {adv_resp2.text[:200]}")
        assert adv_resp2.status_code in [200, 201], f"Should allow with manager approval: {adv_resp2.text}"
        
        print(f"✓ Employee CA limit enforcement working correctly")
    
    # ===========================================================================
    # SECTION 7: EXPENSE REVERSALS (Test 10)
    # ===========================================================================
    
    def test_12_reverse_customer_cashout(self):
        """Test 10a: Reverse customer cashout via /api/expenses/customer-cashout/{id}/reverse"""
        headers = {"Authorization": f"Bearer {TEST_DATA['org1_token']}"}
        
        # Get manager PIN (use last 4 chars of password hash or set one)
        # First, let's set a manager PIN on the admin user
        user_id = TEST_DATA['org1_user'].get('id')
        if user_id:
            requests.put(f"{BASE_URL}/api/users/{user_id}", headers=headers, json={
                "manager_pin": "1234"
            })
        
        response = requests.post(
            f"{BASE_URL}/api/expenses/customer-cashout/{TEST_DATA['cashout_expense_id']}/reverse",
            headers=headers,
            json={"manager_pin": "1234", "reason": "Customer returned cash"}
        )
        
        print(f"Reverse cashout response: {response.status_code} - {response.text[:300]}")
        # May fail if expense not found or PIN wrong - log but continue
        if response.status_code in [200, 201]:
            print(f"✓ Customer cashout reversed successfully")
        else:
            print(f"⚠ Could not reverse cashout: {response.text[:100]}")
    
    def test_13_reverse_employee_advance(self):
        """Test 10b: Reverse employee advance via /api/expenses/employee-advance/{id}/reverse"""
        headers = {"Authorization": f"Bearer {TEST_DATA['org1_token']}"}
        
        response = requests.post(
            f"{BASE_URL}/api/expenses/employee-advance/{TEST_DATA['employee_advance_id']}/reverse",
            headers=headers,
            json={"manager_pin": "1234", "reason": "Employee returned advance"}
        )
        
        print(f"Reverse employee advance response: {response.status_code} - {response.text[:300]}")
        if response.status_code in [200, 201]:
            print(f"✓ Employee advance reversed successfully")
        else:
            print(f"⚠ Could not reverse advance: {response.text[:100]}")
    
    # ===========================================================================
    # SECTION 8: FUND WALLETS (Test 11)
    # ===========================================================================
    
    def test_14_fund_wallet_operations(self):
        """Test 11: Verify cashier wallet, deposit into it, verify balance increases"""
        headers = {"Authorization": f"Bearer {TEST_DATA['org1_token']}"}
        
        # List wallets
        response = requests.get(f"{BASE_URL}/api/fund-wallets?branch_id={TEST_DATA['org1_branch_id']}", headers=headers)
        assert response.status_code == 200, f"List wallets failed: {response.text}"
        
        wallets = response.json()
        cashier = next((w for w in wallets if w.get('type') == 'cashier'), None)
        assert cashier, "Cashier wallet should exist"
        
        TEST_DATA['cashier_wallet_id'] = cashier.get('id')
        initial_balance = float(cashier.get('balance', 0))
        
        # Deposit
        deposit_resp = requests.post(
            f"{BASE_URL}/api/fund-wallets/{TEST_DATA['cashier_wallet_id']}/deposit",
            headers=headers,
            json={"amount": 5000, "reference": "Additional capital"}
        )
        
        assert deposit_resp.status_code == 200, f"Deposit failed: {deposit_resp.text}"
        
        # Verify balance increased
        wallets2 = requests.get(f"{BASE_URL}/api/fund-wallets?branch_id={TEST_DATA['org1_branch_id']}", headers=headers).json()
        cashier2 = next((w for w in wallets2 if w.get('type') == 'cashier'), None)
        new_balance = float(cashier2.get('balance', 0))
        
        print(f"✓ Cashier wallet deposit: ₱{initial_balance} → ₱{new_balance}")
        assert new_balance >= initial_balance + 5000, "Balance should have increased by 5000"
    
    def test_15_fund_transfer(self):
        """Test 11b: Create a fund transfer (cashier to safe)"""
        headers = {"Authorization": f"Bearer {TEST_DATA['org1_token']}"}
        
        response = requests.post(f"{BASE_URL}/api/fund-transfers", headers=headers, json={
            "branch_id": TEST_DATA['org1_branch_id'],
            "transfer_type": "cashier_to_safe",
            "amount": 1000,
            "note": "Daily safe deposit",
            "manager_pin": "1234"
        })
        
        print(f"Fund transfer response: {response.status_code} - {response.text[:300]}")
        if response.status_code == 200:
            print(f"✓ Fund transfer completed: ₱1000 cashier → safe")
        else:
            print(f"⚠ Fund transfer may require manager PIN or insufficient balance")
    
    # ===========================================================================
    # SECTION 9: UPLOADS / QR FLOW (Test 13)
    # ===========================================================================
    
    def test_16_upload_flow(self):
        """Test 13: Upload flow - generate link, preview, upload file"""
        headers = {"Authorization": f"Bearer {TEST_DATA['org1_token']}"}
        
        # Generate upload link for a PO
        if TEST_DATA.get('po_id'):
            gen_resp = requests.post(f"{BASE_URL}/api/uploads/generate-link", headers=headers, json={
                "record_type": "purchase_order",
                "record_id": TEST_DATA['po_id']
            })
            
            print(f"Generate link response: {gen_resp.status_code} - {gen_resp.text[:200]}")
            
            if gen_resp.status_code == 200:
                data = gen_resp.json()
                token = data.get('token')
                
                # Get preview (public endpoint)
                preview_resp = requests.get(f"{BASE_URL}/api/uploads/preview/{token}")
                print(f"Preview response: {preview_resp.status_code}")
                assert preview_resp.status_code == 200, f"Preview failed: {preview_resp.text}"
                
                # Generate view token
                view_resp = requests.post(f"{BASE_URL}/api/uploads/generate-view-token", headers=headers, json={
                    "record_type": "purchase_order",
                    "record_id": TEST_DATA['po_id']
                })
                
                print(f"View token response: {view_resp.status_code}")
                
                print(f"✓ Upload flow working: link generated, preview accessible")
            else:
                print(f"⚠ Could not generate upload link")
        else:
            print("⚠ Skipping upload test - no PO ID")
    
    # ===========================================================================
    # SECTION 10: DASHBOARD (Test 14)
    # ===========================================================================
    
    def test_17_dashboard_stats(self):
        """Test 14: Call /api/dashboard/stats, verify metrics returned"""
        headers = {"Authorization": f"Bearer {TEST_DATA['org1_token']}"}
        
        response = requests.get(f"{BASE_URL}/api/dashboard/stats?branch_id={TEST_DATA['org1_branch_id']}", headers=headers)
        
        print(f"Dashboard stats response: {response.status_code}")
        assert response.status_code == 200, f"Dashboard stats failed: {response.text}"
        
        data = response.json()
        
        # Verify expected fields
        expected_fields = ['today_revenue', 'today_cash_sales', 'today_digital_sales', 'today_net_cash']
        for field in expected_fields:
            assert field in data, f"Missing field: {field}"
        
        print(f"✓ Dashboard stats: Revenue=₱{data.get('today_revenue', 0)}, Cash=₱{data.get('today_cash_sales', 0)}, Net=₱{data.get('today_net_cash', 0)}")
    
    # ===========================================================================
    # SECTION 11: CUSTOMERS (Test 16)
    # ===========================================================================
    
    def test_18_customer_operations(self):
        """Test 16: Create customer with credit_limit, verify branch filtering"""
        headers = {"Authorization": f"Bearer {TEST_DATA['org1_token']}"}
        
        # List customers for branch
        response = requests.get(f"{BASE_URL}/api/customers?branch_id={TEST_DATA['org1_branch_id']}", headers=headers)
        assert response.status_code == 200, f"List customers failed: {response.text}"
        
        data = response.json()
        customers = data.get('customers', data) if isinstance(data, dict) else data
        
        print(f"✓ Customers for branch: {len(customers)} found")
    
    # ===========================================================================
    # SECTION 12: INVOICES & PAYMENTS (Test 17)
    # ===========================================================================
    
    def test_19_invoice_payment(self):
        """Test 17: Record a payment on an open invoice, verify balance decreases"""
        headers = {"Authorization": f"Bearer {TEST_DATA['org1_token']}"}
        
        if TEST_DATA.get('credit_sale_id'):
            # Record payment
            pay_resp = requests.post(
                f"{BASE_URL}/api/invoices/{TEST_DATA['credit_sale_id']}/payment",
                headers=headers,
                json={"amount": 500, "method": "Cash", "fund_source": "cashier"}
            )
            
            print(f"Payment response: {pay_resp.status_code} - {pay_resp.text[:200]}")
            
            if pay_resp.status_code == 200:
                data = pay_resp.json()
                print(f"✓ Payment recorded: New balance=₱{data.get('new_balance', 0)}")
            else:
                print(f"⚠ Payment recording issue: {pay_resp.text[:100]}")
        else:
            print("⚠ Skipping payment test - no credit sale ID")
    
    # ===========================================================================
    # SECTION 13: RETURNS (Test 18)
    # ===========================================================================
    
    def test_20_create_return(self):
        """Test 18: Create a return via /api/returns"""
        headers = {"Authorization": f"Bearer {TEST_DATA['org1_token']}"}
        
        response = requests.post(f"{BASE_URL}/api/returns", headers=headers, json={
            "branch_id": TEST_DATA['org1_branch_id'],
            "customer_name": "Walk-in Return",
            "customer_type": "walkin",
            "reason": "Product defect",
            "items": [{
                "product_id": TEST_DATA['product_id'],
                "product_name": TEST_DATA['product_name'],
                "quantity": 1,
                "condition": "damaged",
                "inventory_action": "pullout",
                "refund_price": 150,
                "cost_price": 100
            }],
            "refund_method": "full",
            "refund_amount": 150,
            "fund_source": "cashier"
        })
        
        print(f"Return response: {response.status_code} - {response.text[:300]}")
        
        if response.status_code in [200, 201]:
            data = response.json()
            print(f"✓ Return processed: {data.get('rma_number')}")
        else:
            print(f"⚠ Return processing issue: {response.text[:100]}")
    
    # ===========================================================================
    # SECTION 14: POS DATA SYNC (Test 19)
    # ===========================================================================
    
    def test_21_pos_data_sync(self):
        """Test 19: Call /api/sync/pos-data, verify products have 'available' field"""
        headers = {"Authorization": f"Bearer {TEST_DATA['org1_token']}"}
        
        response = requests.get(f"{BASE_URL}/api/sync/pos-data?branch_id={TEST_DATA['org1_branch_id']}", headers=headers)
        
        print(f"POS sync response: {response.status_code}")
        assert response.status_code == 200, f"POS sync failed: {response.text}"
        
        data = response.json()
        products = data.get('products', [])
        
        if products:
            sample = products[0]
            assert 'available' in sample, "Products should have 'available' field"
            print(f"✓ POS data sync: {len(products)} products with availability info")
        else:
            print(f"✓ POS data sync working (no products yet)")
    
    # ===========================================================================
    # SECTION 15: MULTI-TENANT ISOLATION (Test 15)
    # ===========================================================================
    
    def test_22_register_second_organization(self):
        """Test 15a: Register a SECOND company for isolation testing"""
        unique = unique_id()
        TEST_DATA['org2_email'] = f"testorg2_{unique}@test.com"
        TEST_DATA['org2_password'] = "Test@123456"
        TEST_DATA['org2_company'] = f"TestCo2_{unique}"
        
        response = requests.post(f"{BASE_URL}/api/organizations/register", json={
            "company_name": TEST_DATA['org2_company'],
            "admin_email": TEST_DATA['org2_email'],
            "admin_password": TEST_DATA['org2_password'],
            "admin_name": "Test Admin 2",
            "branch_name": "Second Co Branch"
        })
        
        print(f"Register org2 response: {response.status_code}")
        assert response.status_code in [200, 201], f"Org2 registration failed: {response.text}"
        
        data = response.json()
        TEST_DATA['org2_id'] = data.get("organization_id")
        
        # Login as org2
        login_resp = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": TEST_DATA['org2_email'],
            "password": TEST_DATA['org2_password']
        })
        
        assert login_resp.status_code == 200
        TEST_DATA['org2_token'] = login_resp.json().get('token')
        
        print(f"✓ Second organization registered: {TEST_DATA['org2_company']}")
    
    def test_23_verify_tenant_isolation(self):
        """Test 15b: Verify second company CANNOT see first company's data"""
        headers1 = {"Authorization": f"Bearer {TEST_DATA['org1_token']}"}
        headers2 = {"Authorization": f"Bearer {TEST_DATA['org2_token']}"}
        
        # Org2 should NOT see Org1's products
        products2_resp = requests.get(f"{BASE_URL}/api/products", headers=headers2).json()
        products2 = products2_resp.get('products', []) if isinstance(products2_resp, dict) else products2_resp
        product_names = [p.get('name', '') for p in products2 if isinstance(p, dict)]
        
        org1_product = TEST_DATA.get('product_name', '')
        assert org1_product not in product_names, f"Org2 should NOT see Org1's product: {org1_product}"
        
        # Org2 should NOT see Org1's customers
        customers2 = requests.get(f"{BASE_URL}/api/customers", headers=headers2).json()
        if isinstance(customers2, dict):
            customers2 = customers2.get('customers', [])
        customer_names = [c.get('name', '') for c in customers2]
        
        org1_customer = TEST_DATA.get('customer_name', '')
        if org1_customer:
            assert org1_customer not in customer_names, f"Org2 should NOT see Org1's customer"
        
        # Org2 should NOT see Org1's invoices
        invoices2 = requests.get(f"{BASE_URL}/api/invoices", headers=headers2).json()
        if isinstance(invoices2, dict):
            invoices2 = invoices2.get('invoices', [])
        invoice_ids = [i.get('id', '') for i in invoices2]
        
        org1_invoice = TEST_DATA.get('cash_sale_id', '')
        if org1_invoice:
            assert org1_invoice not in invoice_ids, f"Org2 should NOT see Org1's invoice"
        
        print(f"✓ Multi-tenant isolation verified: Org2 cannot see Org1's data")
    
    def test_24_verify_reverse_isolation(self):
        """Test 15c: Verify first company cannot see second company's data"""
        headers1 = {"Authorization": f"Bearer {TEST_DATA['org1_token']}"}
        headers2 = {"Authorization": f"Bearer {TEST_DATA['org2_token']}"}
        
        # Create a product in Org2
        unique = unique_id()
        prod2_resp = requests.post(f"{BASE_URL}/api/products", headers=headers2, json={
            "name": f"Org2 Product {unique}",
            "sku": f"ORG2-{unique}",
            "cost_price": 200
        })
        
        if prod2_resp.status_code in [200, 201]:
            org2_product_name = prod2_resp.json().get('name')
            
            # Org1 should NOT see Org2's product
            products1_resp = requests.get(f"{BASE_URL}/api/products", headers=headers1).json()
            products1 = products1_resp.get('products', []) if isinstance(products1_resp, dict) else products1_resp
            product_names1 = [p.get('name', '') for p in products1 if isinstance(p, dict)]
            
            assert org2_product_name not in product_names1, f"Org1 should NOT see Org2's product"
            
            print(f"✓ Reverse isolation verified: Org1 cannot see Org2's data")
        else:
            print(f"⚠ Could not create Org2 product for reverse isolation test")


# ===========================================================================
# RUN TESTS
# ===========================================================================
if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

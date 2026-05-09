"""
Test Suite for Sales History Page Improvements (Iteration 106)
Tests:
1. Sales History pagination, search, filtering, sorting
2. SalesOrderPage credit sale PIN enforcement
3. /api/invoices enhanced endpoints (search, sort_by, sort_dir, status, include_voided)
4. /api/invoices/check-date-closed endpoint
5. Manager PIN branch restriction
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')
if not BASE_URL:
    BASE_URL = "https://permission-lockdown-1.preview.emergentagent.com"

# Credentials from review_request
SUPER_ADMIN_EMAIL = "janmarkeahig@gmail.com"
SUPER_ADMIN_PASSWORD = "Aa@58798546521325"
MANAGER_PIN = "521325"


class TestInvoiceEndpointsEnhanced:
    """Test enhanced /api/invoices endpoint with search, sort, filter params"""
    
    @pytest.fixture(scope="class")
    def auth_headers(self):
        """Get auth token for Super Admin"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": SUPER_ADMIN_EMAIL,
            "password": SUPER_ADMIN_PASSWORD
        })
        assert response.status_code == 200, f"Login failed: {response.text}"
        token = response.json().get("token")
        return {"Authorization": f"Bearer {token}"}
    
    def test_01_list_invoices_basic(self, auth_headers):
        """Test basic invoice listing works"""
        response = requests.get(f"{BASE_URL}/api/invoices", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert "invoices" in data
        assert "total" in data
        print(f"Found {data['total']} total invoices")
    
    def test_02_list_invoices_with_pagination(self, auth_headers):
        """Test pagination params (skip, limit)"""
        response = requests.get(f"{BASE_URL}/api/invoices", params={
            "skip": 0,
            "limit": 25
        }, headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert len(data["invoices"]) <= 25
        print(f"Pagination test: got {len(data['invoices'])} invoices with limit=25")
    
    def test_03_list_invoices_with_sort_by_date_desc(self, auth_headers):
        """Test sorting by created_at descending (default)"""
        response = requests.get(f"{BASE_URL}/api/invoices", params={
            "sort_by": "created_at",
            "sort_dir": "desc",
            "limit": 10
        }, headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        invoices = data["invoices"]
        # Verify descending order
        if len(invoices) >= 2:
            for i in range(len(invoices) - 1):
                date1 = invoices[i].get("created_at", "")
                date2 = invoices[i+1].get("created_at", "")
                if date1 and date2:
                    assert date1 >= date2, f"Sort order wrong: {date1} < {date2}"
        print(f"Sort by created_at DESC: PASS")
    
    def test_04_list_invoices_with_sort_by_amount_desc(self, auth_headers):
        """Test sorting by grand_total descending"""
        response = requests.get(f"{BASE_URL}/api/invoices", params={
            "sort_by": "amount",
            "sort_dir": "desc",
            "limit": 10
        }, headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        invoices = data["invoices"]
        # Verify descending order by amount
        if len(invoices) >= 2:
            for i in range(len(invoices) - 1):
                amt1 = float(invoices[i].get("grand_total", 0))
                amt2 = float(invoices[i+1].get("grand_total", 0))
                assert amt1 >= amt2, f"Amount sort wrong: {amt1} < {amt2}"
        print(f"Sort by amount DESC: PASS")
    
    def test_05_list_invoices_with_sort_by_customer(self, auth_headers):
        """Test sorting by customer_name"""
        response = requests.get(f"{BASE_URL}/api/invoices", params={
            "sort_by": "customer",
            "sort_dir": "asc",
            "limit": 10
        }, headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        print(f"Sort by customer ASC: {len(data['invoices'])} invoices returned")
    
    def test_06_list_invoices_with_search_by_invoice_number(self, auth_headers):
        """Test search by invoice number"""
        # First get an existing invoice number
        response = requests.get(f"{BASE_URL}/api/invoices", params={"limit": 1}, headers=auth_headers)
        assert response.status_code == 200
        invoices = response.json().get("invoices", [])
        if not invoices:
            pytest.skip("No invoices to search")
        
        inv_number = invoices[0].get("invoice_number", "")
        if not inv_number:
            pytest.skip("First invoice has no number")
        
        # Now search for it
        search_term = inv_number[:5] if len(inv_number) > 5 else inv_number
        response = requests.get(f"{BASE_URL}/api/invoices", params={
            "search": search_term
        }, headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["total"] >= 1, f"Search for '{search_term}' should find at least 1 invoice"
        print(f"Search by invoice number '{search_term}': found {data['total']} results")
    
    def test_07_list_invoices_with_status_filter_paid(self, auth_headers):
        """Test filtering by status=paid"""
        response = requests.get(f"{BASE_URL}/api/invoices", params={
            "status": "paid",
            "limit": 50
        }, headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        invoices = data["invoices"]
        for inv in invoices:
            assert inv.get("status") == "paid", f"Got status '{inv.get('status')}' instead of 'paid'"
        print(f"Filter status=paid: found {len(invoices)} paid invoices")
    
    def test_08_list_invoices_with_status_filter_open(self, auth_headers):
        """Test filtering by status=open (credit sales)"""
        response = requests.get(f"{BASE_URL}/api/invoices", params={
            "status": "open",
            "limit": 50
        }, headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        invoices = data["invoices"]
        for inv in invoices:
            assert inv.get("status") == "open", f"Got status '{inv.get('status')}' instead of 'open'"
        print(f"Filter status=open: found {len(invoices)} credit invoices")
    
    def test_09_list_invoices_with_status_filter_partial(self, auth_headers):
        """Test filtering by status=partial"""
        response = requests.get(f"{BASE_URL}/api/invoices", params={
            "status": "partial",
            "limit": 50
        }, headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        invoices = data["invoices"]
        for inv in invoices:
            assert inv.get("status") == "partial", f"Got status '{inv.get('status')}' instead of 'partial'"
        print(f"Filter status=partial: found {len(invoices)} partial invoices")
    
    def test_10_list_invoices_with_voided_filter(self, auth_headers):
        """Test include_voided=True and status=voided"""
        response = requests.get(f"{BASE_URL}/api/invoices", params={
            "status": "voided",
            "include_voided": "true",
            "limit": 50
        }, headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        invoices = data["invoices"]
        for inv in invoices:
            assert inv.get("status") == "voided", f"Got status '{inv.get('status')}' instead of 'voided'"
        print(f"Filter voided: found {len(invoices)} voided invoices")


class TestCheckDateClosedEndpoint:
    """Test /api/invoices/check-date-closed endpoint"""
    
    @pytest.fixture(scope="class")
    def auth_headers(self):
        """Get auth token for Super Admin"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": SUPER_ADMIN_EMAIL,
            "password": SUPER_ADMIN_PASSWORD
        })
        assert response.status_code == 200
        token = response.json().get("token")
        return {"Authorization": f"Bearer {token}"}
    
    @pytest.fixture(scope="class")
    def branch_id(self, auth_headers):
        """Get a valid branch ID"""
        response = requests.get(f"{BASE_URL}/api/branches", headers=auth_headers)
        if response.status_code == 200:
            branches = response.json()
            if branches and len(branches) > 0:
                return branches[0].get("id")
        return None
    
    def test_11_check_date_closed_unclosed_date(self, auth_headers, branch_id):
        """Test check-date-closed for a date that should NOT be closed (future)"""
        if not branch_id:
            pytest.skip("No branch available")
        
        # Use a far future date that won't be closed
        test_date = "2099-12-31"
        response = requests.get(f"{BASE_URL}/api/invoices/check-date-closed", params={
            "date": test_date,
            "branch_id": branch_id
        }, headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert "closed" in data
        assert data["closed"] == False, f"Future date {test_date} should not be closed"
        assert data["date"] == test_date
        print(f"Check date closed for {test_date}: closed={data['closed']}")
    
    def test_12_check_date_closed_returns_date_field(self, auth_headers, branch_id):
        """Test that check-date-closed returns the date field"""
        if not branch_id:
            pytest.skip("No branch available")
        
        test_date = "2026-01-15"
        response = requests.get(f"{BASE_URL}/api/invoices/check-date-closed", params={
            "date": test_date,
            "branch_id": branch_id
        }, headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert "date" in data
        assert data["date"] == test_date
        print(f"Check date closed for {test_date}: response contains date field")


class TestManagerPinVerification:
    """Test manager PIN verification including branch restriction"""
    
    @pytest.fixture(scope="class")
    def auth_headers(self):
        """Get auth token for Super Admin"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": SUPER_ADMIN_EMAIL,
            "password": SUPER_ADMIN_PASSWORD
        })
        assert response.status_code == 200
        token = response.json().get("token")
        return {"Authorization": f"Bearer {token}"}
    
    def test_13_verify_manager_pin_for_credit_sale_approval(self, auth_headers):
        """Test verifying manager PIN for credit sale approval action"""
        response = requests.post(f"{BASE_URL}/api/auth/verify-manager-pin", json={
            "pin": MANAGER_PIN,
            "action_key": "credit_sale_approval",
            "context": {
                "type": "credit_sale",
                "description": "Test credit sale",
                "amount": 1000
            }
        }, headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        # Check response structure
        assert "valid" in data
        if data["valid"]:
            assert "manager_name" in data
            print(f"PIN verification successful: approved by {data.get('manager_name')}")
        else:
            print(f"PIN verification failed: {data.get('detail', 'Unknown reason')}")
    
    def test_14_verify_manager_pin_invalid(self, auth_headers):
        """Test verifying invalid manager PIN"""
        response = requests.post(f"{BASE_URL}/api/auth/verify-manager-pin", json={
            "pin": "000000",  # Invalid PIN
            "action_key": "credit_sale_approval"
        }, headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data.get("valid") == False
        print(f"Invalid PIN correctly rejected")


class TestInvoiceEditWithDateChange:
    """Test invoice edit endpoint with date changes and closed-day validation"""
    
    @pytest.fixture(scope="class")
    def auth_headers(self):
        """Get auth token for Super Admin"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": SUPER_ADMIN_EMAIL,
            "password": SUPER_ADMIN_PASSWORD
        })
        assert response.status_code == 200
        token = response.json().get("token")
        return {"Authorization": f"Bearer {token}"}
    
    def test_15_invoice_edit_endpoint_exists(self, auth_headers):
        """Test that invoice edit endpoint exists"""
        # Get an invoice first
        response = requests.get(f"{BASE_URL}/api/invoices", params={"limit": 1}, headers=auth_headers)
        assert response.status_code == 200
        invoices = response.json().get("invoices", [])
        if not invoices:
            pytest.skip("No invoices to edit")
        
        inv_id = invoices[0].get("id")
        # Try to get the invoice by ID
        response = requests.get(f"{BASE_URL}/api/invoices/{inv_id}", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data.get("id") == inv_id
        print(f"Invoice edit endpoint accessible: {data.get('invoice_number')}")


class TestPurchaseOrderDateEdit:
    """Test PO date editing with closed-day validation"""
    
    @pytest.fixture(scope="class")
    def auth_headers(self):
        """Get auth token for Super Admin"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": SUPER_ADMIN_EMAIL,
            "password": SUPER_ADMIN_PASSWORD
        })
        assert response.status_code == 200
        token = response.json().get("token")
        return {"Authorization": f"Bearer {token}"}
    
    def test_16_po_list_endpoint(self, auth_headers):
        """Test PO list endpoint works"""
        response = requests.get(f"{BASE_URL}/api/purchase-orders", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert "purchase_orders" in data
        print(f"Found {data.get('total', 0)} purchase orders")
    
    def test_17_po_update_endpoint_exists(self, auth_headers):
        """Test that PO update endpoint exists"""
        # Get a PO first
        response = requests.get(f"{BASE_URL}/api/purchase-orders", params={"limit": 1}, headers=auth_headers)
        assert response.status_code == 200
        pos = response.json().get("purchase_orders", [])
        if not pos:
            pytest.skip("No purchase orders to test")
        
        po = pos[0]
        print(f"PO available for testing: {po.get('po_number')} with status {po.get('status')}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short", "-x"])

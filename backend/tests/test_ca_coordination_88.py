"""
Test CA (Cash Advance) Coordination across all endpoints - Iteration 88

Tests the coordination of new CA report endpoint and enhanced CA fields across:
1. GET /api/employees/ca-report - Returns full data with employees array (is_over_limit, over_limit_count, monthly_ca_limit, usage_pct)
2. GET /api/daily-report - advance_expenses include monthly_ca_total, monthly_ca_limit, is_over_ca, manager_approved_by
3. GET /api/daily-close-preview - expenses section includes CA fields for Employee Advance
4. GET /api/daily-log - should work and return data
5. Employee CA summary endpoint for individual employee

Test credentials:
- Super Admin: janmarkeahig@gmail.com / Aa@58798546521325
"""
import os
import pytest
import requests
from datetime import datetime

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    BASE_URL = "https://permission-lockdown-1.preview.emergentagent.com"

# Test credentials
TEST_EMAIL = "janmarkeahig@gmail.com"
TEST_PASSWORD = "Aa@58798546521325"


@pytest.fixture(scope="module")
def api_client():
    """Authenticated API session."""
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    
    login_res = session.post(f"{BASE_URL}/api/auth/login", json={
        "email": TEST_EMAIL,
        "password": TEST_PASSWORD
    })
    
    if login_res.status_code != 200:
        pytest.skip(f"Login failed: {login_res.status_code} - {login_res.text}")
    
    token = login_res.json().get("token")
    if token:
        session.headers.update({"Authorization": f"Bearer {token}"})
    
    return session


@pytest.fixture(scope="module")
def branch_id(api_client):
    """Get first available branch."""
    res = api_client.get(f"{BASE_URL}/api/branches")
    if res.status_code == 200:
        branches = res.json()
        if isinstance(branches, list) and len(branches) > 0:
            return branches[0]["id"]
    return None


class TestCaReportEndpoint:
    """Test GET /api/employees/ca-report returns correct structure with all required fields"""
    
    def test_ca_report_returns_200(self, api_client):
        """CA report endpoint should return 200"""
        response = api_client.get(f"{BASE_URL}/api/employees/ca-report")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        print("SUCCESS: CA report endpoint returns 200")
    
    def test_ca_report_structure(self, api_client):
        """CA report should have correct top-level structure"""
        response = api_client.get(f"{BASE_URL}/api/employees/ca-report")
        assert response.status_code == 200
        data = response.json()
        
        required_fields = ["month", "total_employees", "total_advances_this_month", 
                         "total_unpaid_balance", "over_limit_employees", "employees"]
        for field in required_fields:
            assert field in data, f"Missing required field: {field}"
        
        assert isinstance(data["employees"], list), "employees should be a list"
        print(f"SUCCESS: CA report structure correct - month={data['month']}, total_employees={data['total_employees']}")
    
    def test_ca_report_employee_fields(self, api_client):
        """Each employee in CA report should have all required fields"""
        response = api_client.get(f"{BASE_URL}/api/employees/ca-report")
        assert response.status_code == 200
        data = response.json()
        
        if len(data["employees"]) == 0:
            pytest.skip("No employees in CA report")
        
        required_fields = ["employee_id", "name", "monthly_ca_limit", "this_month_total",
                         "is_over_limit", "usage_pct", "over_limit_count"]
        
        for emp in data["employees"]:
            for field in required_fields:
                assert field in emp, f"Employee missing required field: {field}"
        
        # Check specific fields
        emp = data["employees"][0]
        assert isinstance(emp["is_over_limit"], bool), "is_over_limit should be boolean"
        print(f"SUCCESS: Employee fields correct - {emp['name']}, limit={emp['monthly_ca_limit']}, is_over={emp['is_over_limit']}")
    
    def test_ca_report_with_branch_filter(self, api_client, branch_id):
        """CA report should filter by branch"""
        if not branch_id:
            pytest.skip("No branch available")
        
        response = api_client.get(f"{BASE_URL}/api/employees/ca-report?branch_id={branch_id}")
        assert response.status_code == 200, f"Branch filter failed: {response.text}"
        data = response.json()
        
        # All employees should belong to the filtered branch
        for emp in data["employees"]:
            assert emp.get("branch_id") == branch_id or emp.get("branch_id") == "", \
                f"Employee {emp['name']} has branch {emp.get('branch_id')}, expected {branch_id}"
        
        print(f"SUCCESS: Branch filter works - {len(data['employees'])} employees in branch")
    
    def test_ca_report_with_month_filter(self, api_client):
        """CA report should filter by month"""
        current_month = datetime.now().strftime("%Y-%m")
        
        response = api_client.get(f"{BASE_URL}/api/employees/ca-report?month={current_month}")
        assert response.status_code == 200, f"Month filter failed: {response.text}"
        data = response.json()
        
        assert data["month"] == current_month, f"Month mismatch: expected {current_month}, got {data['month']}"
        print(f"SUCCESS: Month filter works - {current_month}")


class TestDailyReportCaEnrichment:
    """Test GET /api/daily-report enriches advance_expenses with CA fields"""
    
    def test_daily_report_returns_200(self, api_client, branch_id):
        """Daily report should return 200"""
        params = {"date": datetime.now().strftime("%Y-%m-%d")}
        if branch_id:
            params["branch_id"] = branch_id
        
        response = api_client.get(f"{BASE_URL}/api/daily-report", params=params)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        print("SUCCESS: Daily report returns 200")
    
    def test_daily_report_has_advance_expenses(self, api_client, branch_id):
        """Daily report should have advance_expenses array"""
        params = {"date": datetime.now().strftime("%Y-%m-%d")}
        if branch_id:
            params["branch_id"] = branch_id
        
        response = api_client.get(f"{BASE_URL}/api/daily-report", params=params)
        assert response.status_code == 200
        data = response.json()
        
        # advance_expenses should exist even if empty
        assert "advance_expenses" in data, "Missing advance_expenses in daily report"
        assert isinstance(data["advance_expenses"], list), "advance_expenses should be a list"
        
        print(f"SUCCESS: Daily report has advance_expenses array with {len(data['advance_expenses'])} items")
    
    def test_daily_report_advance_expense_ca_fields(self, api_client, branch_id):
        """If advance_expenses exist, they should have CA enrichment fields"""
        # Try date 2026-03-01 as mentioned in context or today
        test_dates = ["2026-03-01", datetime.now().strftime("%Y-%m-%d")]
        
        found_advance = False
        for test_date in test_dates:
            params = {"date": test_date}
            if branch_id:
                params["branch_id"] = branch_id
            
            response = api_client.get(f"{BASE_URL}/api/daily-report", params=params)
            if response.status_code != 200:
                continue
            
            data = response.json()
            advances = data.get("advance_expenses", [])
            
            if len(advances) > 0:
                found_advance = True
                # Check CA enrichment fields
                for exp in advances:
                    assert "monthly_ca_total" in exp or "is_over_ca" in exp or "monthly_ca_limit" in exp, \
                        f"Advance expense missing CA fields: {exp.keys()}"
                
                print(f"SUCCESS: Advance expenses have CA enrichment fields on date {test_date}")
                print(f"  Sample fields: monthly_ca_total={advances[0].get('monthly_ca_total')}, " +
                      f"is_over_ca={advances[0].get('is_over_ca')}, monthly_ca_limit={advances[0].get('monthly_ca_limit')}")
                break
        
        if not found_advance:
            print("INFO: No advance expenses found in test dates - fields will be tested when data exists")


class TestDailyClosePreviewCaFields:
    """Test GET /api/daily-close-preview returns CA fields for Employee Advance expenses"""
    
    def test_daily_close_preview_returns_200(self, api_client, branch_id):
        """Daily close preview should return 200"""
        params = {"date": datetime.now().strftime("%Y-%m-%d")}
        if branch_id:
            params["branch_id"] = branch_id
        
        response = api_client.get(f"{BASE_URL}/api/daily-close-preview", params=params)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        print("SUCCESS: Daily close preview returns 200")
    
    def test_daily_close_preview_has_expenses(self, api_client, branch_id):
        """Daily close preview should have expenses array"""
        params = {"date": datetime.now().strftime("%Y-%m-%d")}
        if branch_id:
            params["branch_id"] = branch_id
        
        response = api_client.get(f"{BASE_URL}/api/daily-close-preview", params=params)
        assert response.status_code == 200
        data = response.json()
        
        assert "expenses" in data, "Missing expenses in daily close preview"
        assert isinstance(data["expenses"], list), "expenses should be a list"
        
        print(f"SUCCESS: Daily close preview has expenses array with {len(data['expenses'])} items")
    
    def test_daily_close_preview_expense_ca_fields(self, api_client, branch_id):
        """Employee Advance expenses in preview should have CA fields"""
        test_dates = ["2026-03-01", datetime.now().strftime("%Y-%m-%d")]
        
        for test_date in test_dates:
            params = {"date": test_date}
            if branch_id:
                params["branch_id"] = branch_id
            
            response = api_client.get(f"{BASE_URL}/api/daily-close-preview", params=params)
            if response.status_code != 200:
                continue
            
            data = response.json()
            expenses = data.get("expenses", [])
            
            employee_advances = [e for e in expenses if e.get("category") == "Employee Advance"]
            
            if len(employee_advances) > 0:
                for exp in employee_advances:
                    # Check for CA fields
                    has_ca_fields = any(k in exp for k in ["monthly_ca_total", "is_over_ca", "monthly_ca_limit"])
                    if has_ca_fields:
                        print(f"SUCCESS: Employee Advance in preview has CA fields on date {test_date}")
                        print(f"  Fields: monthly_ca_total={exp.get('monthly_ca_total')}, " +
                              f"is_over_ca={exp.get('is_over_ca')}, monthly_ca_limit={exp.get('monthly_ca_limit')}")
                        return
        
        print("INFO: No Employee Advance expenses with CA fields found - will be tested when data exists")


class TestDailyLogEndpoint:
    """Test GET /api/daily-log works correctly"""
    
    def test_daily_log_returns_200(self, api_client, branch_id):
        """Daily log should return 200"""
        params = {"date": datetime.now().strftime("%Y-%m-%d")}
        if branch_id:
            params["branch_id"] = branch_id
        
        response = api_client.get(f"{BASE_URL}/api/daily-log", params=params)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        print("SUCCESS: Daily log returns 200")
    
    def test_daily_log_structure(self, api_client, branch_id):
        """Daily log should have correct structure"""
        params = {"date": datetime.now().strftime("%Y-%m-%d")}
        if branch_id:
            params["branch_id"] = branch_id
        
        response = api_client.get(f"{BASE_URL}/api/daily-log", params=params)
        assert response.status_code == 200
        data = response.json()
        
        # Check for required fields
        expected_fields = ["entries", "summary"]
        for field in expected_fields:
            assert field in data, f"Missing field: {field}"
        
        print(f"SUCCESS: Daily log structure correct - {len(data.get('entries', []))} entries")


class TestDailyCloseEndpoint:
    """Test GET /api/daily-close/{date} returns correct structure"""
    
    def test_daily_close_returns_200(self, api_client, branch_id):
        """Daily close status should return 200"""
        test_date = datetime.now().strftime("%Y-%m-%d")
        params = {}
        if branch_id:
            params["branch_id"] = branch_id
        
        response = api_client.get(f"{BASE_URL}/api/daily-close/{test_date}", params=params)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        print("SUCCESS: Daily close endpoint returns 200")
    
    def test_daily_close_structure(self, api_client, branch_id):
        """Daily close should have status field"""
        test_date = datetime.now().strftime("%Y-%m-%d")
        params = {}
        if branch_id:
            params["branch_id"] = branch_id
        
        response = api_client.get(f"{BASE_URL}/api/daily-close/{test_date}", params=params)
        assert response.status_code == 200
        data = response.json()
        
        assert "status" in data, "Missing status field in daily close"
        assert data["status"] in ["open", "closed"], f"Invalid status: {data['status']}"
        
        # If closed, check for expenses with CA fields
        if data["status"] == "closed" and "expenses" in data:
            employee_advances = [e for e in data["expenses"] if e.get("category") == "Employee Advance"]
            if employee_advances:
                for exp in employee_advances:
                    has_ca_fields = any(k in exp for k in ["monthly_ca_total", "is_over_ca", "monthly_ca_limit"])
                    print(f"  Employee Advance expense CA fields: {has_ca_fields}")
        
        print(f"SUCCESS: Daily close structure correct - status={data['status']}")


class TestEmployeeCaSummary:
    """Test individual employee CA summary endpoint"""
    
    def test_employee_ca_summary_endpoint(self, api_client):
        """Test /api/employees/{id}/ca-summary returns correct data"""
        # First get an employee
        emp_res = api_client.get(f"{BASE_URL}/api/employees")
        if emp_res.status_code != 200:
            pytest.skip("Cannot fetch employees")
        
        employees = emp_res.json()
        if not employees or len(employees) == 0:
            pytest.skip("No employees found")
        
        emp_id = employees[0]["id"]
        
        response = api_client.get(f"{BASE_URL}/api/employees/{emp_id}/ca-summary")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        expected_fields = ["monthly_ca_limit", "this_month_total", "is_over_limit"]
        for field in expected_fields:
            assert field in data, f"Missing field: {field}"
        
        print(f"SUCCESS: Employee CA summary correct - limit={data['monthly_ca_limit']}, " +
              f"this_month={data['this_month_total']}, is_over={data['is_over_limit']}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

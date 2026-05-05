"""
Backend tests for Daily Close Wizard endpoints:
- GET /low-stock-alert
- GET /supplier-payables
- GET /daily-close-preview
- GET /daily-log
- GET /daily-close/{date}
"""

import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', 'https://timezone-fix-30.preview.emergentagent.com').rstrip('/')

# Main Branch - Downtown
BRANCH_ID = "da114e26-fd00-467f-8728-6b8047a244b5"
TODAY = None  # Will be set dynamically

import datetime
TODAY = datetime.date.today().strftime("%Y-%m-%d")


@pytest.fixture(scope="module")
def auth_token():
    """Get owner token"""
    response = requests.post(f"{BASE_URL}/api/auth/login", json={
        "username": "owner",
        "password": "521325"
    })
    assert response.status_code == 200, f"Login failed: {response.text}"
    data = response.json()
    return data.get("access_token") or data.get("token")


@pytest.fixture(scope="module")
def auth_headers(auth_token):
    return {"Authorization": f"Bearer {auth_token}"}


class TestLowStockAlert:
    """Tests for GET /api/low-stock-alert"""

    def test_low_stock_returns_200(self, auth_headers):
        """Low stock alert endpoint returns 200"""
        resp = requests.get(
            f"{BASE_URL}/api/low-stock-alert",
            params={"branch_id": BRANCH_ID},
            headers=auth_headers
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        print(f"PASS: /api/low-stock-alert 200 OK")

    def test_low_stock_returns_list(self, auth_headers):
        """Low stock alert returns a list"""
        resp = requests.get(
            f"{BASE_URL}/api/low-stock-alert",
            params={"branch_id": BRANCH_ID},
            headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list), f"Expected list, got {type(data)}: {data}"
        print(f"PASS: /api/low-stock-alert returns list with {len(data)} items")

    def test_low_stock_item_structure(self, auth_headers):
        """Low stock items have required fields"""
        resp = requests.get(
            f"{BASE_URL}/api/low-stock-alert",
            params={"branch_id": BRANCH_ID},
            headers=auth_headers
        )
        data = resp.json()
        # Main Branch - Downtown has 3 inventory records, 2 have negative qty → should show
        print(f"Low stock items: {data}")
        if len(data) > 0:
            item = data[0]
            assert "name" in item, "Missing 'name' field"
            assert "sku" in item, "Missing 'sku' field"
            assert "current_qty" in item, "Missing 'current_qty' field"
            assert "status" in item, "Missing 'status' field"
            assert "unit" in item, "Missing 'unit' field"
            assert "category" in item, "Missing 'category' field"
            # Status must be out_of_stock or low_stock
            assert item["status"] in ["out_of_stock", "low_stock"], f"Invalid status: {item['status']}"
            print(f"PASS: low stock item structure valid: {item['name']} ({item['status']}, qty={item['current_qty']})")
        else:
            print("INFO: No low stock items returned (may be all stocked)")

    def test_low_stock_out_of_stock_sorted_first(self, auth_headers):
        """Out-of-stock items should come before low-stock items"""
        resp = requests.get(
            f"{BASE_URL}/api/low-stock-alert",
            params={"branch_id": BRANCH_ID},
            headers=auth_headers
        )
        data = resp.json()
        if len(data) >= 2:
            statuses = [item["status"] for item in data]
            # out_of_stock items should come before low_stock items
            if "out_of_stock" in statuses and "low_stock" in statuses:
                first_low = statuses.index("low_stock")
                last_oos = len(statuses) - 1 - statuses[::-1].index("out_of_stock")
                assert last_oos < first_low, "out_of_stock items should come before low_stock items"
                print("PASS: out_of_stock items sorted before low_stock items")
        else:
            print(f"INFO: Only {len(data)} items, skipping sort check")

    def test_low_stock_no_branch_returns_empty_or_200(self, auth_headers):
        """Without branch_id (non-owner user) returns gracefully"""
        resp = requests.get(
            f"{BASE_URL}/api/low-stock-alert",
            headers=auth_headers
        )
        # Owner has no branch_id → returns [] per code logic
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        print(f"PASS: /api/low-stock-alert without branch_id returns 200 with list")


class TestSupplierPayables:
    """Tests for GET /api/supplier-payables"""

    def test_supplier_payables_returns_200(self, auth_headers):
        """Supplier payables endpoint returns 200"""
        resp = requests.get(
            f"{BASE_URL}/api/supplier-payables",
            params={"branch_id": BRANCH_ID},
            headers=auth_headers
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        print(f"PASS: /api/supplier-payables 200 OK")

    def test_supplier_payables_returns_list(self, auth_headers):
        """Supplier payables returns a list"""
        resp = requests.get(
            f"{BASE_URL}/api/supplier-payables",
            params={"branch_id": BRANCH_ID},
            headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list), f"Expected list, got {type(data)}"
        print(f"PASS: /api/supplier-payables returns list with {len(data)} items")

    def test_supplier_payables_item_structure(self, auth_headers):
        """Supplier payables items have required fields including urgency flags"""
        resp = requests.get(
            f"{BASE_URL}/api/supplier-payables",
            params={"branch_id": BRANCH_ID},
            headers=auth_headers
        )
        data = resp.json()
        print(f"Payables: {data[:2]}")
        if len(data) > 0:
            item = data[0]
            required_fields = ["id", "po_number", "vendor", "subtotal", "balance",
                               "status", "payment_status", "is_overdue", "is_urgent",
                               "days_until_due"]
            for field in required_fields:
                assert field in item, f"Missing field '{field}' in payable item"
            # Booleans
            assert isinstance(item["is_overdue"], bool), "is_overdue must be bool"
            assert isinstance(item["is_urgent"], bool), "is_urgent must be bool"
            print(f"PASS: Payable item structure valid: {item['vendor']} (overdue={item['is_overdue']}, urgent={item['is_urgent']})")
        else:
            print("INFO: No payables found for branch")

    def test_supplier_payables_overdue_sorted_first(self, auth_headers):
        """Overdue payables should come before non-overdue"""
        resp = requests.get(
            f"{BASE_URL}/api/supplier-payables",
            params={"branch_id": BRANCH_ID},
            headers=auth_headers
        )
        data = resp.json()
        if len(data) >= 2:
            overdue_indices = [i for i, p in enumerate(data) if p["is_overdue"]]
            urgent_indices = [i for i, p in enumerate(data) if p["is_urgent"] and not p["is_overdue"]]
            pending_indices = [i for i, p in enumerate(data) if not p["is_overdue"] and not p["is_urgent"]]
            # Overdue should come before urgent, urgent before pending
            if overdue_indices and urgent_indices:
                assert max(overdue_indices) < min(urgent_indices), "Overdue items should come before urgent items"
            if urgent_indices and pending_indices:
                assert max(urgent_indices) < min(pending_indices), "Urgent items should come before pending items"
            print("PASS: Supplier payables sorted by urgency correctly")
        else:
            print(f"INFO: Only {len(data)} items, skipping sort check")

    def test_supplier_payables_all_branches(self, auth_headers):
        """Supplier payables without branch_id (all branches) returns 200"""
        resp = requests.get(
            f"{BASE_URL}/api/supplier-payables",
            headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        print(f"PASS: /api/supplier-payables all branches returns {len(data)} items")


class TestDailyClosePreview:
    """Tests for GET /api/daily-close-preview"""

    def test_preview_returns_200(self, auth_headers):
        resp = requests.get(
            f"{BASE_URL}/api/daily-close-preview",
            params={"branch_id": BRANCH_ID, "date": TODAY},
            headers=auth_headers
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        print(f"PASS: /api/daily-close-preview 200 OK")

    def test_preview_structure(self, auth_headers):
        resp = requests.get(
            f"{BASE_URL}/api/daily-close-preview",
            params={"branch_id": BRANCH_ID, "date": TODAY},
            headers=auth_headers
        )
        data = resp.json()
        required = ["date", "branch_id", "starting_float", "total_cash_sales",
                    "total_ar_received", "total_expenses", "expected_counter",
                    "ar_payments", "expenses"]
        for field in required:
            assert field in data, f"Missing field: {field}"
        assert data["date"] == TODAY
        assert data["branch_id"] == BRANCH_ID
        print(f"PASS: Preview structure valid. Expected counter: {data['expected_counter']}, Expenses: {data['total_expenses']}")


class TestDailyLog:
    """Tests for GET /api/daily-log"""

    def test_daily_log_returns_200(self, auth_headers):
        resp = requests.get(
            f"{BASE_URL}/api/daily-log",
            params={"branch_id": BRANCH_ID, "date": TODAY},
            headers=auth_headers
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        print(f"PASS: /api/daily-log 200 OK")

    def test_daily_log_structure(self, auth_headers):
        resp = requests.get(
            f"{BASE_URL}/api/daily-log",
            params={"branch_id": BRANCH_ID, "date": TODAY},
            headers=auth_headers
        )
        data = resp.json()
        required = ["entries", "cash_entries", "credit_invoices", "date", "count", "summary"]
        for field in required:
            assert field in data, f"Missing field: {field}"
        summary = data["summary"]
        assert "total_cash" in summary
        assert "total_credit" in summary
        assert "cash_count" in summary
        print(f"PASS: Daily log structure valid. Cash entries: {data['summary']['cash_count']}")


class TestDailyCloseStatus:
    """Tests for GET /api/daily-close/{date}"""

    def test_close_status_returns_200(self, auth_headers):
        resp = requests.get(
            f"{BASE_URL}/api/daily-close/{TODAY}",
            params={"branch_id": BRANCH_ID},
            headers=auth_headers
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        print(f"PASS: /api/daily-close/{TODAY} status: {data.get('status', 'open')}")

    def test_close_status_open_or_closed(self, auth_headers):
        resp = requests.get(
            f"{BASE_URL}/api/daily-close/{TODAY}",
            params={"branch_id": BRANCH_ID},
            headers=auth_headers
        )
        data = resp.json()
        # Should have a status field
        assert "date" in data, "Missing 'date' field in close status"
        status = data.get("status", "open")
        assert status in ["open", "closed"], f"Unexpected status: {status}"
        print(f"PASS: Close status for {TODAY}: {status}")


class TestManagerPinVerification:
    """Tests for POST /api/auth/verify-manager-pin"""

    def test_verify_pin_valid(self, auth_headers):
        """Valid manager pin (owner's pin = 521325) should be verified"""
        resp = requests.post(
            f"{BASE_URL}/api/auth/verify-manager-pin",
            json={
                "pin": "521325",
                "required_level": "manager",
                "context": f"Daily close test"
            },
            headers=auth_headers
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert data.get("valid") == True, f"Expected valid=True, got: {data}"
        print(f"PASS: Manager PIN verified for {data.get('manager_name')}")

    def test_verify_pin_invalid(self, auth_headers):
        """Invalid pin should return valid=False or 4xx"""
        resp = requests.post(
            f"{BASE_URL}/api/auth/verify-manager-pin",
            json={
                "pin": "000000",
                "required_level": "manager",
                "context": "test"
            },
            headers=auth_headers
        )
        # Could be 200 with valid=False, or 400/403
        if resp.status_code == 200:
            data = resp.json()
            assert data.get("valid") == False, f"Expected valid=False for wrong PIN, got: {data}"
            print("PASS: Invalid PIN returns valid=False")
        else:
            assert resp.status_code in [400, 403], f"Unexpected status: {resp.status_code}"
            print(f"PASS: Invalid PIN returns status {resp.status_code}")

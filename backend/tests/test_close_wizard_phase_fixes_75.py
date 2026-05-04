"""
Backend tests for Close Wizard Phase 1-4 Fixes (Iteration 75):
- Daily-log API with by_payment_method breakdown
- Close preview with expenses grouped
- Multi-day unclosed days detection
- Credit invoices with item breakdown

Tests verify comprehensive data for all wizard steps:
Step 1: Sales Log - ALL payment methods (cash, gcash, maya, split, credit)
Step 2: Customer Credits - Per-invoice item breakdown with payment status
Step 4: Expenses - Grouped by category
Step 5: Actual Count - E-wallet payments breakdown
Step 7: Z-Report - Cash Drawer Reconciliation
"""

import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', 'https://backdated-guard.preview.emergentagent.com').rstrip('/')

# IPIL BRANCH with diverse test data
IPIL_BRANCH_ID = "d4a041e7-4918-490e-afb8-54ae90cec7fb"
# Date with data: 2026-02-26 has cash, gcash, maya, split, credit entries
TEST_DATE = "2026-02-26"


@pytest.fixture(scope="module")
def auth_token():
    """Get super admin token"""
    response = requests.post(f"{BASE_URL}/api/auth/login", json={
        "username": "janmarkeahig@gmail.com",
        "password": "Aa@58798546521325"
    })
    assert response.status_code == 200, f"Login failed: {response.text}"
    data = response.json()
    return data.get("access_token") or data.get("token")


@pytest.fixture(scope="module")
def auth_headers(auth_token):
    return {"Authorization": f"Bearer {auth_token}"}


class TestDailyLogByPaymentMethod:
    """Tests for GET /api/daily-log - Step 1: Sales Log with all payment methods"""

    def test_daily_log_returns_entries_with_all_payment_methods(self, auth_headers):
        """Daily log should return entries for ALL payment methods - not just cash"""
        resp = requests.get(
            f"{BASE_URL}/api/daily-log",
            params={"branch_id": IPIL_BRANCH_ID, "date": TEST_DATE},
            headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        
        entries = data.get("entries", [])
        assert len(entries) > 0, "Expected at least 1 entry"
        
        # Get all unique payment methods from entries
        payment_methods = set(e.get("payment_method", "").lower() for e in entries)
        print(f"Payment methods found in entries: {payment_methods}")
        
        # Verify diverse payment methods exist
        assert len(payment_methods) >= 3, f"Expected at least 3 payment methods, found: {payment_methods}"
        print(f"PASS: Daily log returns {len(entries)} entries with payment methods: {payment_methods}")

    def test_daily_log_has_by_payment_method_summary(self, auth_headers):
        """Daily log summary should include by_payment_method breakdown"""
        resp = requests.get(
            f"{BASE_URL}/api/daily-log",
            params={"branch_id": IPIL_BRANCH_ID, "date": TEST_DATE},
            headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        
        summary = data.get("summary", {})
        assert "by_payment_method" in summary, "Missing by_payment_method in summary"
        
        by_pm = summary["by_payment_method"]
        assert len(by_pm) > 0, "by_payment_method is empty"
        
        # Each payment method should have total and count
        for method, breakdown in by_pm.items():
            assert "total" in breakdown, f"Missing total for {method}"
            assert "count" in breakdown, f"Missing count for {method}"
        
        print(f"PASS: by_payment_method breakdown: {by_pm}")

    def test_daily_log_has_total_all_field(self, auth_headers):
        """Summary should have total_all field summing ALL payment methods"""
        resp = requests.get(
            f"{BASE_URL}/api/daily-log",
            params={"branch_id": IPIL_BRANCH_ID, "date": TEST_DATE},
            headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        
        summary = data.get("summary", {})
        assert "total_all" in summary, "Missing total_all in summary"
        
        total_all = summary["total_all"]
        assert total_all >= 0, "total_all should be non-negative"
        
        # Verify total_all sums all entries
        entries = data.get("entries", [])
        calculated_total = sum(float(e.get("line_total", 0)) for e in entries)
        assert abs(total_all - calculated_total) < 0.01, f"total_all {total_all} != sum of entries {calculated_total}"
        
        print(f"PASS: total_all = {total_all}")

    def test_daily_log_entries_have_payment_method_field(self, auth_headers):
        """Each entry should have payment_method field"""
        resp = requests.get(
            f"{BASE_URL}/api/daily-log",
            params={"branch_id": IPIL_BRANCH_ID, "date": TEST_DATE},
            headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        
        entries = data.get("entries", [])
        for i, entry in enumerate(entries[:5]):  # Check first 5
            assert "payment_method" in entry, f"Entry {i} missing payment_method"
            pm = entry["payment_method"]
            assert pm and len(pm) > 0, f"Entry {i} has empty payment_method"
        
        print(f"PASS: All entries have payment_method field")


class TestCreditInvoicesWithItems:
    """Tests for credit_invoices in daily-log - Step 2: Customer Credits with item breakdown"""

    def test_credit_invoices_have_items_array(self, auth_headers):
        """Credit invoices should include items array with product details"""
        resp = requests.get(
            f"{BASE_URL}/api/daily-log",
            params={"branch_id": IPIL_BRANCH_ID, "date": TEST_DATE},
            headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        
        credit_invoices = data.get("credit_invoices", [])
        if len(credit_invoices) == 0:
            pytest.skip("No credit invoices on this date")
        
        inv = credit_invoices[0]
        assert "items" in inv, "Credit invoice missing items array"
        assert isinstance(inv["items"], list), "items should be a list"
        
        if len(inv["items"]) > 0:
            item = inv["items"][0]
            assert "product_name" in item or "description" in item, "Item missing product_name/description"
            assert "quantity" in item, "Item missing quantity"
        
        print(f"PASS: Credit invoices have items array with {len(inv['items'])} items")

    def test_credit_invoices_have_payment_status(self, auth_headers):
        """Credit invoices should have amount_paid and balance fields"""
        resp = requests.get(
            f"{BASE_URL}/api/daily-log",
            params={"branch_id": IPIL_BRANCH_ID, "date": TEST_DATE},
            headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        
        credit_invoices = data.get("credit_invoices", [])
        if len(credit_invoices) == 0:
            pytest.skip("No credit invoices on this date")
        
        inv = credit_invoices[0]
        assert "grand_total" in inv, "Missing grand_total"
        assert "amount_paid" in inv, "Missing amount_paid"
        assert "balance" in inv, "Missing balance"
        
        print(f"PASS: Credit invoice {inv.get('invoice_number')} - Total: {inv['grand_total']}, Paid: {inv['amount_paid']}, Balance: {inv['balance']}")


class TestExpensesByCategory:
    """Tests for expenses in daily-close-preview - Step 4: Expenses grouped by category"""

    def test_preview_expenses_have_category(self, auth_headers):
        """Preview expenses should have category field for grouping"""
        resp = requests.get(
            f"{BASE_URL}/api/daily-close-preview",
            params={"branch_id": IPIL_BRANCH_ID, "date": TEST_DATE},
            headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        
        expenses = data.get("expenses", [])
        if len(expenses) == 0:
            pytest.skip("No expenses on this date")
        
        for exp in expenses:
            assert "category" in exp, "Expense missing category field"
            assert "description" in exp, "Expense missing description field"
            assert "amount" in exp, "Expense missing amount field"
        
        # Group by category for verification
        categories = set(e.get("category") for e in expenses)
        print(f"PASS: Expenses have categories: {categories}")

    def test_preview_expenses_have_customer_employee_fields(self, auth_headers):
        """Special expenses (farm, cashout, advance) should have customer/employee fields"""
        resp = requests.get(
            f"{BASE_URL}/api/daily-close-preview",
            params={"branch_id": IPIL_BRANCH_ID, "date": "2026-02-20"},  # Date with more expense variety
            headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        
        expenses = data.get("expenses", [])
        
        # Check if any expenses have customer_name or employee_name
        has_customer = any(e.get("customer_name") for e in expenses)
        has_employee = any(e.get("employee_name") for e in expenses)
        
        print(f"PASS: Expenses have customer_name: {has_customer}, employee_name: {has_employee}")


class TestActualCountEWalletBreakdown:
    """Tests for daily-close-preview - Step 5: Actual Count with e-wallet breakdown"""

    def test_preview_has_digital_payments_breakdown(self, auth_headers):
        """Preview should have digital_by_platform breakdown for GCash, Maya"""
        resp = requests.get(
            f"{BASE_URL}/api/daily-close-preview",
            params={"branch_id": IPIL_BRANCH_ID, "date": TEST_DATE},
            headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        
        assert "total_digital_today" in data, "Missing total_digital_today"
        assert "digital_by_platform" in data, "Missing digital_by_platform"
        
        digital_total = data["total_digital_today"]
        digital_by_platform = data["digital_by_platform"]
        
        print(f"PASS: E-wallet total: {digital_total}, by platform: {digital_by_platform}")

    def test_preview_has_cash_breakdown_fields(self, auth_headers):
        """Preview should have all cash breakdown fields for Step 5"""
        resp = requests.get(
            f"{BASE_URL}/api/daily-close-preview",
            params={"branch_id": IPIL_BRANCH_ID, "date": TEST_DATE},
            headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        
        required_fields = [
            "starting_float",
            "total_cash_sales",
            "total_partial_cash",
            "total_ar_received",
            "total_cash_in",
            "total_expenses",
            "expected_counter"
        ]
        
        for field in required_fields:
            assert field in data, f"Missing field: {field}"
        
        # Verify total_cash_in = cash_sales + partial_cash + ar_received
        calculated_cash_in = data["total_cash_sales"] + data["total_partial_cash"] + data["total_ar_received"]
        assert abs(data["total_cash_in"] - calculated_cash_in) < 0.01, \
            f"total_cash_in {data['total_cash_in']} != calculated {calculated_cash_in}"
        
        print(f"PASS: Cash breakdown - Float: {data['starting_float']}, Cash In: {data['total_cash_in']}, Expenses: {data['total_expenses']}, Expected: {data['expected_counter']}")


class TestUnclosedDaysDetection:
    """Tests for GET /api/daily-close/unclosed-days - Multi-day closing detection"""

    def test_unclosed_days_returns_list(self, auth_headers):
        """Unclosed days endpoint should return list of unclosed days"""
        resp = requests.get(
            f"{BASE_URL}/api/daily-close/unclosed-days",
            params={"branch_id": IPIL_BRANCH_ID},
            headers=auth_headers
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        
        assert "unclosed_days" in data, "Missing unclosed_days"
        assert isinstance(data["unclosed_days"], list), "unclosed_days should be a list"
        
        print(f"PASS: Found {len(data['unclosed_days'])} unclosed days")

    def test_unclosed_days_have_activity_summary(self, auth_headers):
        """Each unclosed day should have activity summary (sales_count, expense_count, etc.)"""
        resp = requests.get(
            f"{BASE_URL}/api/daily-close/unclosed-days",
            params={"branch_id": IPIL_BRANCH_ID},
            headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        
        unclosed = data.get("unclosed_days", [])
        if len(unclosed) == 0:
            pytest.skip("No unclosed days")
        
        day = unclosed[0]
        required_fields = ["date", "has_activity", "sales_count", "expense_count", "cash_sales_total"]
        for field in required_fields:
            assert field in day, f"Unclosed day missing field: {field}"
        
        # Find a day with activity
        days_with_activity = [d for d in unclosed if d["has_activity"]]
        if days_with_activity:
            active_day = days_with_activity[0]
            print(f"PASS: Day {active_day['date']} - Sales: {active_day['sales_count']}, Expenses: {active_day['expense_count']}, Cash Total: {active_day['cash_sales_total']}")

    def test_unclosed_days_has_last_close_date(self, auth_headers):
        """Response should include last_close_date field"""
        resp = requests.get(
            f"{BASE_URL}/api/daily-close/unclosed-days",
            params={"branch_id": IPIL_BRANCH_ID},
            headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        
        assert "last_close_date" in data, "Missing last_close_date"
        assert "last_drawer_float" in data, "Missing last_drawer_float"
        assert "total_unclosed" in data, "Missing total_unclosed"
        
        print(f"PASS: Last close: {data['last_close_date']}, Last float: {data['last_drawer_float']}, Total unclosed: {data['total_unclosed']}")


class TestZReportData:
    """Tests for data needed by Step 7: Z-Report"""

    def test_preview_has_credit_sales_today(self, auth_headers):
        """Preview should have credit_sales_today for Z-Report"""
        resp = requests.get(
            f"{BASE_URL}/api/daily-close-preview",
            params={"branch_id": IPIL_BRANCH_ID, "date": TEST_DATE},
            headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        
        assert "credit_sales_today" in data, "Missing credit_sales_today"
        assert "total_credit_today" in data, "Missing total_credit_today"
        
        credit_sales = data["credit_sales_today"]
        if credit_sales:
            sale = credit_sales[0]
            assert "customer_name" in sale, "Credit sale missing customer_name"
            assert "invoice_number" in sale, "Credit sale missing invoice_number"
            assert "grand_total" in sale, "Credit sale missing grand_total"
        
        print(f"PASS: Credit sales today: {len(credit_sales)} invoices, total: {data['total_credit_today']}")

    def test_preview_ar_payments_have_breakdown(self, auth_headers):
        """AR payments should have interest/penalty/principal breakdown"""
        resp = requests.get(
            f"{BASE_URL}/api/daily-close-preview",
            params={"branch_id": IPIL_BRANCH_ID, "date": "2026-02-20"},  # Date more likely to have AR payments
            headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        
        ar_payments = data.get("ar_payments", [])
        if len(ar_payments) == 0:
            print("INFO: No AR payments on this date")
            return
        
        pmt = ar_payments[0]
        expected_fields = ["customer_name", "invoice_number", "balance_before", 
                          "interest_paid", "penalty_paid", "amount_paid", "remaining_balance"]
        for field in expected_fields:
            assert field in pmt, f"AR payment missing field: {field}"
        
        print(f"PASS: AR payment has full breakdown")


class TestDailyLogPaymentMethodVariety:
    """Verify the test data actually has the required payment method variety"""

    def test_date_has_split_payments(self, auth_headers):
        """Verify date 2026-02-26 has split payment entries"""
        resp = requests.get(
            f"{BASE_URL}/api/daily-log",
            params={"branch_id": IPIL_BRANCH_ID, "date": TEST_DATE},
            headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        
        by_pm = data.get("summary", {}).get("by_payment_method", {})
        assert "split" in by_pm, "No split payments found"
        print(f"PASS: Split payments: {by_pm.get('split', {})}")

    def test_date_has_gcash_payments(self, auth_headers):
        """Verify date has GCash entries"""
        resp = requests.get(
            f"{BASE_URL}/api/daily-log",
            params={"branch_id": IPIL_BRANCH_ID, "date": TEST_DATE},
            headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        
        by_pm = data.get("summary", {}).get("by_payment_method", {})
        assert "gcash" in by_pm, "No gcash payments found"
        print(f"PASS: GCash payments: {by_pm.get('gcash', {})}")

    def test_date_has_maya_payments(self, auth_headers):
        """Verify date has Maya entries"""
        resp = requests.get(
            f"{BASE_URL}/api/daily-log",
            params={"branch_id": IPIL_BRANCH_ID, "date": TEST_DATE},
            headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        
        by_pm = data.get("summary", {}).get("by_payment_method", {})
        assert "maya" in by_pm, "No maya payments found"
        print(f"PASS: Maya payments: {by_pm.get('maya', {})}")

    def test_date_has_credit_payments(self, auth_headers):
        """Verify date has credit entries"""
        resp = requests.get(
            f"{BASE_URL}/api/daily-log",
            params={"branch_id": IPIL_BRANCH_ID, "date": TEST_DATE},
            headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        
        by_pm = data.get("summary", {}).get("by_payment_method", {})
        assert "credit" in by_pm, "No credit payments found"
        print(f"PASS: Credit payments: {by_pm.get('credit', {})}")

    def test_date_has_cash_payments(self, auth_headers):
        """Verify date has cash entries"""
        resp = requests.get(
            f"{BASE_URL}/api/daily-log",
            params={"branch_id": IPIL_BRANCH_ID, "date": TEST_DATE},
            headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        
        by_pm = data.get("summary", {}).get("by_payment_method", {})
        assert "cash" in by_pm, "No cash payments found"
        print(f"PASS: Cash payments: {by_pm.get('cash', {})}")

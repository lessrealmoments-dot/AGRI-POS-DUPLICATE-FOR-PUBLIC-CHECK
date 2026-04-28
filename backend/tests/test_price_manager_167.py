"""
Backend tests for Price Manager feature (iteration 167):
- GET /api/products/price-audit-summary
- POST /api/products/bulk-price-update
- POST /api/branch-prices/bulk-update
- GET /api/permissions/modules (branch_transfers module)
- GET /api/permissions/presets/manager (branch_transfers + customers.manage_credit)
- GET /api/permissions/presets/cashier (branch_transfers all false)
"""
import pytest
import requests
import os

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")

# ── Auth helper ─────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def auth_token():
    """Login with super admin and return token."""
    resp = requests.post(f"{BASE_URL}/api/auth/login", json={
        "email": "janmarkeahig@gmail.com",
        "password": "Aa@58798546521325"
    })
    assert resp.status_code == 200, f"Login failed: {resp.text}"
    return resp.json()["token"]


@pytest.fixture(scope="module")
def authed(auth_token):
    """Session with auth header."""
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {auth_token}", "Content-Type": "application/json"})
    return s


# ── Price Audit Summary ───────────────────────────────────────────────────────

class TestPriceAuditSummary:
    """GET /api/products/price-audit-summary"""

    def test_audit_summary_returns_200(self, authed):
        """Endpoint returns 200 OK."""
        resp = authed.get(f"{BASE_URL}/api/products/price-audit-summary")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    def test_audit_summary_has_missing_capital(self, authed):
        """Response contains missing_capital with count and products list."""
        resp = authed.get(f"{BASE_URL}/api/products/price-audit-summary")
        data = resp.json()
        assert "missing_capital" in data, "missing_capital key not found in response"
        mc = data["missing_capital"]
        assert "count" in mc, "missing_capital.count missing"
        assert "products" in mc, "missing_capital.products missing"
        assert isinstance(mc["count"], int), "missing_capital.count should be int"
        assert isinstance(mc["products"], list), "missing_capital.products should be list"

    def test_audit_summary_has_low_margin(self, authed):
        """Response contains low_margin with count and products list."""
        resp = authed.get(f"{BASE_URL}/api/products/price-audit-summary")
        data = resp.json()
        assert "low_margin" in data, "low_margin key not found in response"
        lm = data["low_margin"]
        assert "count" in lm, "low_margin.count missing"
        assert "products" in lm, "low_margin.products missing"
        assert isinstance(lm["count"], int), "low_margin.count should be int"
        assert isinstance(lm["products"], list), "low_margin.products should be list"

    def test_audit_summary_count_matches_list(self, authed):
        """Count in missing_capital should be >= len(products) (capped at 100)."""
        resp = authed.get(f"{BASE_URL}/api/products/price-audit-summary")
        data = resp.json()
        mc = data["missing_capital"]
        assert mc["count"] >= len(mc["products"]), "count should be >= len of products list"
        lm = data["low_margin"]
        assert lm["count"] >= len(lm["products"]), "low_margin count >= len of products list"

    def test_audit_summary_requires_auth(self):
        """Without auth, returns 401 or 403."""
        resp = requests.get(f"{BASE_URL}/api/products/price-audit-summary")
        assert resp.status_code in [401, 403], f"Expected 401/403 without auth, got {resp.status_code}"


# ── Bulk Price Update ─────────────────────────────────────────────────────────

class TestBulkPriceUpdate:
    """POST /api/products/bulk-price-update"""

    def test_bulk_price_update_empty_returns_400(self, authed):
        """POST with items=[] returns 400."""
        resp = authed.post(f"{BASE_URL}/api/products/bulk-price-update", json={"items": []})
        assert resp.status_code == 400, f"Expected 400 for empty items, got {resp.status_code}: {resp.text}"

    def test_bulk_price_update_missing_items_returns_400(self, authed):
        """POST with no items key returns 400."""
        resp = authed.post(f"{BASE_URL}/api/products/bulk-price-update", json={})
        assert resp.status_code == 400, f"Expected 400 for missing items, got {resp.status_code}: {resp.text}"

    def test_bulk_price_update_valid_item(self, authed):
        """POST with valid product_id updates price and returns updated count."""
        # First, find a product to use
        products_resp = authed.get(f"{BASE_URL}/api/products", params={"limit": 1})
        assert products_resp.status_code == 200
        products_data = products_resp.json()
        products_list = products_data if isinstance(products_data, list) else products_data.get("products", [])
        if not products_list:
            pytest.skip("No products in database for price update test")

        product = products_list[0]
        pid = product.get("id")
        assert pid, "Product has no id"

        # Get existing prices to restore later
        existing_prices = product.get("prices", {}) or {}

        # Update with a test price for any available scheme
        test_scheme = list(existing_prices.keys())[0] if existing_prices else "retail"
        test_price = 99.99

        resp = authed.post(f"{BASE_URL}/api/products/bulk-price-update", json={
            "items": [{"product_id": pid, "prices": {test_scheme: test_price}}]
        })
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert "updated" in data, "Response missing 'updated' key"
        assert data["updated"] >= 1, f"Expected at least 1 updated, got {data['updated']}"

        # Verify update persisted (GET product and check price)
        get_resp = authed.get(f"{BASE_URL}/api/products/{pid}")
        assert get_resp.status_code == 200
        updated_product = get_resp.json()
        updated_prices = updated_product.get("prices", {}) or {}
        assert float(updated_prices.get(test_scheme, 0)) == test_price, f"Price not updated in DB: got {updated_prices}"

    def test_bulk_price_update_requires_auth(self):
        """Without auth returns 401 or 403."""
        resp = requests.post(f"{BASE_URL}/api/products/bulk-price-update", json={"items": [{"product_id": "x", "prices": {"retail": 10}}]})
        assert resp.status_code in [401, 403], f"Expected 401/403 without auth, got {resp.status_code}"


# ── Branch Prices Bulk Update ─────────────────────────────────────────────────

class TestBranchPricesBulkUpdate:
    """POST /api/branch-prices/bulk-update"""

    def test_bulk_update_empty_returns_400(self, authed):
        """POST with items=[] returns 400."""
        resp = authed.post(f"{BASE_URL}/api/branch-prices/bulk-update", json={"items": []})
        assert resp.status_code == 400, f"Expected 400 for empty items, got {resp.status_code}: {resp.text}"

    def test_bulk_update_missing_items_returns_400(self, authed):
        """POST with no items key returns 400."""
        resp = authed.post(f"{BASE_URL}/api/branch-prices/bulk-update", json={})
        assert resp.status_code == 400, f"Expected 400 for missing items, got {resp.status_code}: {resp.text}"

    def test_bulk_update_valid_item_upserts(self, authed):
        """POST with valid product_id and branch_id upserts branch price."""
        # Find a product
        products_resp = authed.get(f"{BASE_URL}/api/products", params={"limit": 1})
        assert products_resp.status_code == 200
        products_data = products_resp.json()
        products_list = products_data if isinstance(products_data, list) else products_data.get("products", [])
        if not products_list:
            pytest.skip("No products in database for branch price test")

        # Find a branch
        branches_resp = authed.get(f"{BASE_URL}/api/branches")
        assert branches_resp.status_code == 200
        branches = branches_resp.json()
        if isinstance(branches, dict):
            branches = branches.get("branches", [])
        if not branches:
            pytest.skip("No branches in database for branch price test")

        product = products_list[0]
        pid = product.get("id")
        branch_id = branches[0].get("id")

        # Upsert branch price
        resp = authed.post(f"{BASE_URL}/api/branch-prices/bulk-update", json={
            "items": [{"product_id": pid, "branch_id": branch_id, "prices": {"retail": 55.50}}]
        })
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert "updated" in data, "Response missing 'updated' key"
        assert data["updated"] >= 1, f"Expected at least 1 updated, got {data['updated']}"

        # Verify it was persisted by reading back
        get_resp = authed.get(f"{BASE_URL}/api/branch-prices", params={"product_id": pid, "branch_id": branch_id})
        assert get_resp.status_code == 200
        overrides = get_resp.json()
        found = next((o for o in overrides if o.get("branch_id") == branch_id and o.get("product_id") == pid), None)
        assert found is not None, "Branch price override not found after bulk update"
        assert float(found.get("prices", {}).get("retail", 0)) == 55.50, f"Price not persisted: {found}"

    def test_bulk_update_requires_auth(self):
        """Without auth returns 401 or 403."""
        resp = requests.post(f"{BASE_URL}/api/branch-prices/bulk-update", json={"items": [{"product_id": "x", "branch_id": "y"}]})
        assert resp.status_code in [401, 403], f"Expected 401/403 without auth, got {resp.status_code}"


# ── Permissions Modules ───────────────────────────────────────────────────────

class TestPermissionsModules:
    """GET /api/permissions/modules"""

    def test_modules_returns_200(self, authed):
        """Endpoint returns 200 OK."""
        resp = authed.get(f"{BASE_URL}/api/permissions/modules")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    def test_modules_includes_branch_transfers(self, authed):
        """Response includes branch_transfers module."""
        resp = authed.get(f"{BASE_URL}/api/permissions/modules")
        data = resp.json()
        assert "branch_transfers" in data, f"branch_transfers not in modules: {list(data.keys())}"

    def test_branch_transfers_module_has_view_action(self, authed):
        """branch_transfers module has 'view' action."""
        resp = authed.get(f"{BASE_URL}/api/permissions/modules")
        data = resp.json()
        bt = data.get("branch_transfers", {})
        actions = bt.get("actions", {})
        assert "view" in actions, f"branch_transfers.view missing, actions: {list(actions.keys())}"

    def test_branch_transfers_module_has_create_action(self, authed):
        """branch_transfers module has 'create' action."""
        resp = authed.get(f"{BASE_URL}/api/permissions/modules")
        data = resp.json()
        bt = data.get("branch_transfers", {})
        actions = bt.get("actions", {})
        assert "create" in actions, f"branch_transfers.create missing, actions: {list(actions.keys())}"

    def test_branch_transfers_module_has_receive_action(self, authed):
        """branch_transfers module has 'receive' action."""
        resp = authed.get(f"{BASE_URL}/api/permissions/modules")
        data = resp.json()
        bt = data.get("branch_transfers", {})
        actions = bt.get("actions", {})
        assert "receive" in actions, f"branch_transfers.receive missing, actions: {list(actions.keys())}"

    def test_modules_requires_auth(self):
        """Without auth returns 401 or 403."""
        resp = requests.get(f"{BASE_URL}/api/permissions/modules")
        assert resp.status_code in [401, 403], f"Expected 401/403 without auth, got {resp.status_code}"


# ── Permissions Presets ───────────────────────────────────────────────────────

class TestPermissionsPresets:
    """GET /api/permissions/presets/{preset_key}"""

    def test_manager_preset_returns_200(self, authed):
        """Manager preset endpoint returns 200."""
        resp = authed.get(f"{BASE_URL}/api/permissions/presets/manager")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    def test_manager_preset_branch_transfers_all_true(self, authed):
        """Manager preset: branch_transfers view/create/receive all true."""
        resp = authed.get(f"{BASE_URL}/api/permissions/presets/manager")
        data = resp.json()
        perms = data.get("permissions", {})
        bt = perms.get("branch_transfers", {})
        assert bt.get("view") is True, f"manager branch_transfers.view should be True, got: {bt}"
        assert bt.get("create") is True, f"manager branch_transfers.create should be True, got: {bt}"
        assert bt.get("receive") is True, f"manager branch_transfers.receive should be True, got: {bt}"

    def test_manager_preset_customers_manage_credit_true(self, authed):
        """Manager preset: customers.manage_credit is true."""
        resp = authed.get(f"{BASE_URL}/api/permissions/presets/manager")
        data = resp.json()
        perms = data.get("permissions", {})
        customers = perms.get("customers", {})
        assert customers.get("manage_credit") is True, f"manager customers.manage_credit should be True, got: {customers}"

    def test_cashier_preset_returns_200(self, authed):
        """Cashier preset endpoint returns 200."""
        resp = authed.get(f"{BASE_URL}/api/permissions/presets/cashier")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    def test_cashier_preset_branch_transfers_all_false(self, authed):
        """Cashier preset: branch_transfers all actions false."""
        resp = authed.get(f"{BASE_URL}/api/permissions/presets/cashier")
        data = resp.json()
        perms = data.get("permissions", {})
        bt = perms.get("branch_transfers", {})
        assert "branch_transfers" in perms, f"cashier preset missing branch_transfers: {list(perms.keys())}"
        assert bt.get("view") is False, f"cashier branch_transfers.view should be False, got: {bt}"
        assert bt.get("create") is False, f"cashier branch_transfers.create should be False, got: {bt}"
        assert bt.get("receive") is False, f"cashier branch_transfers.receive should be False, got: {bt}"

    def test_invalid_preset_returns_404(self, authed):
        """Invalid preset key returns 404."""
        resp = authed.get(f"{BASE_URL}/api/permissions/presets/nonexistent_role")
        assert resp.status_code == 404, f"Expected 404 for invalid preset, got {resp.status_code}"

    def test_presets_requires_auth(self):
        """Without auth returns 401 or 403."""
        resp = requests.get(f"{BASE_URL}/api/permissions/presets/manager")
        assert resp.status_code in [401, 403], f"Expected 401/403 without auth, got {resp.status_code}"

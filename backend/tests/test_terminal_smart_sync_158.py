"""
Test Terminal Smart Sync Features - Iteration 158
Tests for:
1. GET /api/sync/pos-data (full sync - no last_sync param)
2. GET /api/sync/pos-data with last_sync param (delta sync)
3. GET /api/sync/pos-data with future last_sync (zero changes)
4. GET /api/sync/pos-data delta returns deleted_ids for deactivated products
5. GET /api/sync/inventory-pulse endpoint
"""
import pytest
import requests
import os
from datetime import datetime, timedelta

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')
BRANCH_ID = "c435277f-9fc7-4d83-83e7-38be5b4423ac"  # Branch 1
BRANCH_ID_2 = "18c02daa-bce0-45de-860a-70ccc6ed6c6d"  # Branch 2


class TestSyncEndpoints:
    """Test sync API endpoints for Terminal Smart Sync"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Get auth token for tests"""
        # Login as super admin
        login_res = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": "janmarkeahig@gmail.com",
            "password": "Aa@58798546521325"
        })
        assert login_res.status_code == 200, f"Login failed: {login_res.text}"
        self.token = login_res.json().get("token")
        self.headers = {"Authorization": f"Bearer {self.token}"}
    
    # ========== Full Sync Tests (no last_sync param) ==========
    
    def test_full_sync_returns_all_data(self):
        """GET /api/sync/pos-data without last_sync returns full data"""
        res = requests.get(
            f"{BASE_URL}/api/sync/pos-data",
            params={"branch_id": BRANCH_ID},
            headers=self.headers
        )
        assert res.status_code == 200, f"Full sync failed: {res.text}"
        data = res.json()
        
        # Verify response structure
        assert "products" in data, "Missing products in response"
        assert "customers" in data, "Missing customers in response"
        assert "inventory" in data, "Missing inventory in response"
        assert "branch_prices" in data, "Missing branch_prices in response"
        assert "deleted_ids" in data, "Missing deleted_ids in response"
        assert "sync_time" in data, "Missing sync_time in response"
        assert "is_delta" in data, "Missing is_delta in response"
        
        # Full sync should have is_delta=false
        assert data["is_delta"] == False, f"Expected is_delta=False for full sync, got {data['is_delta']}"
        
        # Should have products (assuming there are products in the system)
        assert isinstance(data["products"], list), "products should be a list"
        assert isinstance(data["customers"], list), "customers should be a list"
        assert isinstance(data["inventory"], list), "inventory should be a list"
        
        print(f"Full sync returned: {len(data['products'])} products, {len(data['customers'])} customers, {len(data['inventory'])} inventory items")
    
    def test_full_sync_products_have_available_field(self):
        """Full sync products should have 'available' field (enriched with inventory)"""
        res = requests.get(
            f"{BASE_URL}/api/sync/pos-data",
            params={"branch_id": BRANCH_ID},
            headers=self.headers
        )
        assert res.status_code == 200
        data = res.json()
        
        if len(data["products"]) > 0:
            product = data["products"][0]
            assert "available" in product, f"Product missing 'available' field: {product.get('name')}"
            assert isinstance(product["available"], (int, float)), "available should be numeric"
            print(f"Sample product: {product.get('name')} - available: {product.get('available')}")
    
    def test_full_sync_without_branch_aggregates_inventory(self):
        """Full sync without branch_id should aggregate inventory across all branches"""
        res = requests.get(
            f"{BASE_URL}/api/sync/pos-data",
            headers=self.headers
        )
        assert res.status_code == 200
        data = res.json()
        
        # Without branch_id, inventory should be aggregated
        assert "inventory" in data
        assert data["is_delta"] == False
        print(f"Full sync (no branch): {len(data['products'])} products, {len(data['inventory'])} aggregated inventory items")
    
    # ========== Delta Sync Tests (with last_sync param) ==========
    
    def test_delta_sync_returns_is_delta_true(self):
        """GET /api/sync/pos-data with last_sync returns is_delta=true"""
        # Use a past date to get some delta results
        past_date = (datetime.now() - timedelta(days=30)).isoformat()
        
        res = requests.get(
            f"{BASE_URL}/api/sync/pos-data",
            params={"branch_id": BRANCH_ID, "last_sync": past_date},
            headers=self.headers
        )
        assert res.status_code == 200, f"Delta sync failed: {res.text}"
        data = res.json()
        
        # Delta sync should have is_delta=true
        assert data["is_delta"] == True, f"Expected is_delta=True for delta sync, got {data['is_delta']}"
        assert "deleted_ids" in data, "Delta sync should include deleted_ids"
        
        print(f"Delta sync (30 days): {len(data['products'])} products, {len(data['deleted_ids'])} deleted_ids")
    
    def test_delta_sync_returns_fewer_products_than_full(self):
        """Delta sync should return fewer or equal products compared to full sync"""
        # Full sync
        full_res = requests.get(
            f"{BASE_URL}/api/sync/pos-data",
            params={"branch_id": BRANCH_ID},
            headers=self.headers
        )
        full_data = full_res.json()
        full_count = len(full_data["products"])
        
        # Delta sync with recent date
        recent_date = (datetime.now() - timedelta(hours=1)).isoformat()
        delta_res = requests.get(
            f"{BASE_URL}/api/sync/pos-data",
            params={"branch_id": BRANCH_ID, "last_sync": recent_date},
            headers=self.headers
        )
        delta_data = delta_res.json()
        delta_count = len(delta_data["products"])
        
        # Delta should have fewer or equal products
        assert delta_count <= full_count, f"Delta ({delta_count}) should be <= full ({full_count})"
        print(f"Full sync: {full_count} products, Delta sync (1 hour): {delta_count} products")
    
    def test_future_last_sync_returns_zero_changes(self):
        """GET /api/sync/pos-data with future last_sync returns 0 products"""
        # Use a future date - nothing should have changed
        future_date = (datetime.now() + timedelta(days=365*5)).isoformat()  # 5 years in future
        
        res = requests.get(
            f"{BASE_URL}/api/sync/pos-data",
            params={"branch_id": BRANCH_ID, "last_sync": future_date},
            headers=self.headers
        )
        assert res.status_code == 200, f"Future sync failed: {res.text}"
        data = res.json()
        
        # Should return 0 products, 0 customers, 0 inventory (nothing changed since future date)
        assert len(data["products"]) == 0, f"Expected 0 products for future date, got {len(data['products'])}"
        assert len(data["customers"]) == 0, f"Expected 0 customers for future date, got {len(data['customers'])}"
        assert len(data["inventory"]) == 0, f"Expected 0 inventory for future date, got {len(data['inventory'])}"
        assert data["is_delta"] == True, "Should still be delta sync"
        
        print(f"Future sync: {len(data['products'])} products, {len(data['customers'])} customers, {len(data['inventory'])} inventory (all 0 as expected)")
    
    def test_delta_sync_includes_deleted_ids(self):
        """Delta sync should include deleted_ids array"""
        past_date = (datetime.now() - timedelta(days=365)).isoformat()  # 1 year ago
        
        res = requests.get(
            f"{BASE_URL}/api/sync/pos-data",
            params={"branch_id": BRANCH_ID, "last_sync": past_date},
            headers=self.headers
        )
        assert res.status_code == 200
        data = res.json()
        
        # deleted_ids should be present and be a list
        assert "deleted_ids" in data, "Delta sync must include deleted_ids"
        assert isinstance(data["deleted_ids"], list), "deleted_ids should be a list"
        
        print(f"Delta sync deleted_ids: {len(data['deleted_ids'])} items")
    
    # ========== Inventory Pulse Tests ==========
    
    def test_inventory_pulse_with_branch_id(self):
        """GET /api/sync/inventory-pulse with branch_id returns inventory items"""
        res = requests.get(
            f"{BASE_URL}/api/sync/inventory-pulse",
            params={"branch_id": BRANCH_ID},
            headers=self.headers
        )
        assert res.status_code == 200, f"Inventory pulse failed: {res.text}"
        data = res.json()
        
        # Verify response structure
        assert "items" in data, "Missing items in response"
        assert "pulse_time" in data, "Missing pulse_time in response"
        assert isinstance(data["items"], list), "items should be a list"
        
        # Check item structure if items exist
        if len(data["items"]) > 0:
            item = data["items"][0]
            assert "product_id" in item, "Item missing product_id"
            assert "quantity" in item, "Item missing quantity"
            print(f"Sample inventory item: product_id={item['product_id']}, quantity={item['quantity']}")
        
        print(f"Inventory pulse returned: {len(data['items'])} items, pulse_time={data['pulse_time']}")
    
    def test_inventory_pulse_without_branch_id_returns_empty(self):
        """GET /api/sync/inventory-pulse without branch_id returns empty items"""
        res = requests.get(
            f"{BASE_URL}/api/sync/inventory-pulse",
            headers=self.headers
        )
        assert res.status_code == 200, f"Inventory pulse failed: {res.text}"
        data = res.json()
        
        # Without branch_id, should return empty items
        assert "items" in data, "Missing items in response"
        assert len(data["items"]) == 0, f"Expected empty items without branch_id, got {len(data['items'])}"
        assert "pulse_time" in data, "Missing pulse_time in response"
        
        print(f"Inventory pulse (no branch): {len(data['items'])} items (empty as expected)")
    
    def test_inventory_pulse_with_future_since_returns_zero(self):
        """GET /api/sync/inventory-pulse with future since returns 0 items"""
        future_date = (datetime.now() + timedelta(days=365*5)).isoformat()
        
        res = requests.get(
            f"{BASE_URL}/api/sync/inventory-pulse",
            params={"branch_id": BRANCH_ID, "since": future_date},
            headers=self.headers
        )
        assert res.status_code == 200, f"Inventory pulse failed: {res.text}"
        data = res.json()
        
        # With future since, should return 0 items (nothing changed)
        assert len(data["items"]) == 0, f"Expected 0 items for future since, got {len(data['items'])}"
        assert data.get("is_delta") == True, "Should be delta mode with since param"
        
        print(f"Inventory pulse (future since): {len(data['items'])} items (0 as expected)")
    
    def test_inventory_pulse_with_past_since_returns_changes(self):
        """GET /api/sync/inventory-pulse with past since returns changed items"""
        past_date = (datetime.now() - timedelta(days=30)).isoformat()
        
        res = requests.get(
            f"{BASE_URL}/api/sync/inventory-pulse",
            params={"branch_id": BRANCH_ID, "since": past_date},
            headers=self.headers
        )
        assert res.status_code == 200, f"Inventory pulse failed: {res.text}"
        data = res.json()
        
        # Should have is_delta=true
        assert data.get("is_delta") == True, "Should be delta mode with since param"
        assert "total" in data, "Should include total count"
        
        print(f"Inventory pulse (30 days): {len(data['items'])} items, total={data.get('total')}")
    
    def test_inventory_pulse_response_structure(self):
        """Verify inventory pulse response has correct structure"""
        res = requests.get(
            f"{BASE_URL}/api/sync/inventory-pulse",
            params={"branch_id": BRANCH_ID},
            headers=self.headers
        )
        assert res.status_code == 200
        data = res.json()
        
        # Required fields
        required_fields = ["items", "pulse_time"]
        for field in required_fields:
            assert field in data, f"Missing required field: {field}"
        
        # Optional fields that should be present
        assert "total" in data, "Should include total count"
        
        print(f"Inventory pulse structure verified: items={len(data['items'])}, total={data.get('total')}, pulse_time={data['pulse_time']}")
    
    # ========== Authentication Tests ==========
    
    def test_sync_pos_data_requires_auth(self):
        """GET /api/sync/pos-data requires authentication"""
        res = requests.get(f"{BASE_URL}/api/sync/pos-data")
        assert res.status_code in [401, 403], f"Expected 401/403 without auth, got {res.status_code}"
    
    def test_inventory_pulse_requires_auth(self):
        """GET /api/sync/inventory-pulse requires authentication"""
        res = requests.get(f"{BASE_URL}/api/sync/inventory-pulse")
        assert res.status_code in [401, 403], f"Expected 401/403 without auth, got {res.status_code}"
    
    # ========== Branch-specific Tests ==========
    
    def test_sync_with_different_branches(self):
        """Sync should work with different branch IDs"""
        # Branch 1
        res1 = requests.get(
            f"{BASE_URL}/api/sync/pos-data",
            params={"branch_id": BRANCH_ID},
            headers=self.headers
        )
        assert res1.status_code == 200
        
        # Branch 2
        res2 = requests.get(
            f"{BASE_URL}/api/sync/pos-data",
            params={"branch_id": BRANCH_ID_2},
            headers=self.headers
        )
        assert res2.status_code == 200
        
        data1 = res1.json()
        data2 = res2.json()
        
        print(f"Branch 1: {len(data1['products'])} products, {len(data1['inventory'])} inventory")
        print(f"Branch 2: {len(data2['products'])} products, {len(data2['inventory'])} inventory")


class TestSyncEstimate:
    """Test sync estimate endpoint"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Get auth token for tests"""
        login_res = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": "janmarkeahig@gmail.com",
            "password": "Aa@58798546521325"
        })
        assert login_res.status_code == 200
        self.token = login_res.json().get("token")
        self.headers = {"Authorization": f"Bearer {self.token}"}
    
    def test_sync_estimate_returns_counts(self):
        """GET /api/sync/estimate returns product/customer/inventory counts"""
        res = requests.get(
            f"{BASE_URL}/api/sync/estimate",
            params={"branch_id": BRANCH_ID},
            headers=self.headers
        )
        assert res.status_code == 200, f"Sync estimate failed: {res.text}"
        data = res.json()
        
        # Verify response structure
        assert "products" in data, "Missing products count"
        assert "customers" in data, "Missing customers count"
        assert "inventory" in data, "Missing inventory count"
        assert "estimated_kb" in data, "Missing estimated_kb"
        
        # All should be non-negative integers
        assert isinstance(data["products"], int) and data["products"] >= 0
        assert isinstance(data["customers"], int) and data["customers"] >= 0
        assert isinstance(data["inventory"], int) and data["inventory"] >= 0
        assert isinstance(data["estimated_kb"], (int, float)) and data["estimated_kb"] >= 0
        
        print(f"Sync estimate: {data['products']} products, {data['customers']} customers, {data['inventory']} inventory, ~{data['estimated_kb']}KB")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

"""
Repack Inventory System Tests
=============================
Tests for the new repack inventory logic where:
1. Repacks do NOT have their own inventory
2. Repack available stock is calculated from parent (parent_stock × units_per_parent)
3. When repack sells, only parent inventory is deducted
4. Cannot manually adjust repack inventory

Test credentials: admin/admin123
"""

import pytest
import requests
import os
from datetime import datetime

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')
if not BASE_URL:
    BASE_URL = "https://branch-pricing-hub.preview.emergentagent.com"

# Module-level constants for test data
BRANCH_ID = "9599a32f-722d-4824-9af8-b0217ca78523"  # Main Branch


@pytest.fixture(scope="module")
def auth_token():
    """Get authentication token"""
    response = requests.post(f"{BASE_URL}/api/auth/login", json={
        "username": "admin",
        "password": "admin123"
    })
    assert response.status_code == 200, f"Login failed: {response.text}"
    return response.json()["token"]


@pytest.fixture(scope="module")
def auth_headers(auth_token):
    """Return headers with auth token"""
    return {"Authorization": f"Bearer {auth_token}", "Content-Type": "application/json"}


@pytest.fixture(scope="module")
def test_parent_product(auth_headers):
    """Create a test parent product for repack testing"""
    product_data = {
        "name": f"TEST_PARENT_REPACK_{datetime.now().strftime('%H%M%S')}",
        "sku": f"TEST-PAR-{datetime.now().strftime('%H%M%S')}",
        "category": "Test Category",
        "unit": "Box",
        "cost_price": 100,
        "prices": {"retail": 150, "wholesale": 130}
    }
    response = requests.post(f"{BASE_URL}/api/products", headers=auth_headers, json=product_data)
    assert response.status_code == 200, f"Failed to create parent product: {response.text}"
    product = response.json()
    
    # Set initial inventory for the parent product
    inv_data = {
        "product_id": product["id"],
        "branch_id": BRANCH_ID,
        "quantity": 10,  # 10 boxes
        "reason": "Initial stock for testing"
    }
    inv_response = requests.post(f"{BASE_URL}/api/inventory/adjust", headers=auth_headers, json=inv_data)
    assert inv_response.status_code == 200, f"Failed to set inventory: {inv_response.text}"
    
    yield product
    
    # Cleanup - delete the product
    requests.delete(f"{BASE_URL}/api/products/{product['id']}", headers=auth_headers)


@pytest.fixture(scope="module")
def test_repack_product(auth_headers, test_parent_product):
    """Create a repack from the test parent product"""
    repack_data = {
        "name": f"TEST_REPACK_PIECE_{datetime.now().strftime('%H%M%S')}",
        "units_per_parent": 10,  # 10 pieces per box
        "unit": "Piece",
        "prices": {"retail": 20, "wholesale": 17}
    }
    response = requests.post(
        f"{BASE_URL}/api/products/{test_parent_product['id']}/generate-repack",
        headers=auth_headers,
        json=repack_data
    )
    assert response.status_code == 200, f"Failed to create repack: {response.text}"
    return response.json()


class TestInventoryEndpointWithRepacks:
    """Test /api/inventory endpoint returns correct derived quantities for repacks"""
    
    def test_inventory_shows_repacks_with_derived_label(self, auth_headers, test_repack_product):
        """Inventory endpoint should return repacks with derived_from_parent=true"""
        response = requests.get(
            f"{BASE_URL}/api/inventory",
            headers=auth_headers,
            params={"branch_id": BRANCH_ID, "search": "TEST_REPACK"}
        )
        assert response.status_code == 200
        data = response.json()
        
        # Find our test repack in the results
        repack_items = [i for i in data["items"] if i["id"] == test_repack_product["id"]]
        assert len(repack_items) == 1, "Test repack should be in inventory results"
        
        repack = repack_items[0]
        assert repack.get("derived_from_parent") is True, "Repack should have derived_from_parent=true"
        assert repack.get("is_repack") is True
        print(f"✓ Inventory shows repack with derived_from_parent=true")
    
    def test_inventory_shows_repack_parent_info(self, auth_headers, test_repack_product, test_parent_product):
        """Inventory endpoint should show parent name for repacks"""
        response = requests.get(
            f"{BASE_URL}/api/inventory",
            headers=auth_headers,
            params={"branch_id": BRANCH_ID, "search": "TEST_REPACK"}
        )
        assert response.status_code == 200
        data = response.json()
        
        repack_items = [i for i in data["items"] if i["id"] == test_repack_product["id"]]
        repack = repack_items[0]
        
        assert repack.get("parent_name") == test_parent_product["name"], "Repack should show parent name"
        assert repack.get("parent_stock") == 10, "Repack should show parent stock"
        print(f"✓ Inventory shows parent info: {repack.get('parent_name')}, stock: {repack.get('parent_stock')}")
    
    def test_repack_calculated_available(self, auth_headers, test_repack_product, test_parent_product):
        """Repack available = parent_stock × units_per_parent"""
        response = requests.get(
            f"{BASE_URL}/api/inventory",
            headers=auth_headers,
            params={"branch_id": BRANCH_ID, "search": "TEST_REPACK"}
        )
        assert response.status_code == 200
        data = response.json()
        
        repack_items = [i for i in data["items"] if i["id"] == test_repack_product["id"]]
        repack = repack_items[0]
        
        # Parent has 10 boxes, repack is 10 pieces per box, so should have 100 pieces available
        expected_stock = 10 * 10  # parent_stock × units_per_parent
        assert repack.get("total_stock") == expected_stock, f"Repack stock should be {expected_stock}, got {repack.get('total_stock')}"
        print(f"✓ Repack calculated stock: {repack.get('total_stock')} (10 parent × 10 units_per_parent)")


class TestProductSearchWithRepacks:
    """Test /api/products/search-detail endpoint for repacks"""
    
    def test_search_returns_calculated_available_for_repacks(self, auth_headers, test_repack_product):
        """Product search should return calculated available for repacks"""
        response = requests.get(
            f"{BASE_URL}/api/products/search-detail",
            headers=auth_headers,
            params={"q": "TEST_REPACK", "branch_id": BRANCH_ID}
        )
        assert response.status_code == 200
        data = response.json()
        
        # Find our test repack
        repack_results = [p for p in data if p["id"] == test_repack_product["id"]]
        assert len(repack_results) == 1, "Test repack should be in search results"
        
        repack = repack_results[0]
        expected_available = 10 * 10  # parent_stock × units_per_parent
        assert repack.get("available") == expected_available, f"Available should be {expected_available}"
        print(f"✓ Product search returns calculated available: {repack.get('available')}")
    
    def test_search_shows_derived_from_parent_true_for_repacks(self, auth_headers, test_repack_product):
        """Product search should show derived_from_parent=true for repacks"""
        response = requests.get(
            f"{BASE_URL}/api/products/search-detail",
            headers=auth_headers,
            params={"q": "TEST_REPACK", "branch_id": BRANCH_ID}
        )
        assert response.status_code == 200
        data = response.json()
        
        repack_results = [p for p in data if p["id"] == test_repack_product["id"]]
        repack = repack_results[0]
        
        assert repack.get("derived_from_parent") is True
        assert repack.get("parent_name") is not None
        assert repack.get("parent_stock") is not None
        print(f"✓ Product search shows derived_from_parent=true, parent: {repack.get('parent_name')}")


class TestRepackInventoryAdjustmentBlocked:
    """Test that adjusting repack inventory is blocked"""
    
    def test_adjust_repack_inventory_returns_error(self, auth_headers, test_repack_product):
        """Trying to adjust repack inventory should return 400 error"""
        adjust_data = {
            "product_id": test_repack_product["id"],
            "branch_id": BRANCH_ID,
            "quantity": 5,
            "reason": "Test adjustment"
        }
        response = requests.post(
            f"{BASE_URL}/api/inventory/adjust",
            headers=auth_headers,
            json=adjust_data
        )
        
        assert response.status_code == 400, f"Should return 400, got {response.status_code}"
        data = response.json()
        assert "Cannot adjust repack inventory" in data.get("detail", ""), f"Error message should mention cannot adjust repack, got: {data}"
        assert "parent" in data.get("detail", "").lower(), "Error should mention adjusting parent instead"
        print(f"✓ Repack inventory adjustment correctly blocked: {data.get('detail')}")
    
    def test_adjust_parent_inventory_allowed(self, auth_headers, test_parent_product):
        """Adjusting parent product inventory should work normally"""
        adjust_data = {
            "product_id": test_parent_product["id"],
            "branch_id": BRANCH_ID,
            "quantity": 2,  # Add 2 more boxes
            "reason": "Test parent adjustment"
        }
        response = requests.post(
            f"{BASE_URL}/api/inventory/adjust",
            headers=auth_headers,
            json=adjust_data
        )
        
        assert response.status_code == 200, f"Parent adjustment should work: {response.text}"
        data = response.json()
        assert data.get("new_quantity") == 12, f"Parent should now have 12 boxes (10+2), got {data.get('new_quantity')}"
        print(f"✓ Parent inventory adjusted successfully: new quantity = {data.get('new_quantity')}")


class TestRepackSalesDeductFromParent:
    """Test that selling repacks deducts from parent inventory only"""
    
    def test_repack_sale_deducts_from_parent(self, auth_headers, test_repack_product, test_parent_product):
        """Selling repack should deduct from parent inventory, not repack"""
        # Get parent inventory before sale
        inv_response = requests.get(
            f"{BASE_URL}/api/inventory",
            headers=auth_headers,
            params={"branch_id": BRANCH_ID, "search": test_parent_product["name"]}
        )
        assert inv_response.status_code == 200
        items = inv_response.json()["items"]
        parent_items = [i for i in items if i["id"] == test_parent_product["id"]]
        parent_stock_before = parent_items[0]["total_stock"] if parent_items else 12  # Should be 12 from previous test
        
        # Create a sale invoice with the repack
        invoice_data = {
            "branch_id": BRANCH_ID,
            "customer_name": "TEST Walk-in Customer",
            "items": [{
                "product_id": test_repack_product["id"],
                "product_name": test_repack_product["name"],
                "quantity": 5,  # Sell 5 pieces
                "rate": test_repack_product["prices"]["retail"],
                "is_repack": True
            }],
            "payment_method": "Cash",
            "amount_paid": 5 * test_repack_product["prices"]["retail"]
        }
        
        sale_response = requests.post(
            f"{BASE_URL}/api/invoices",
            headers=auth_headers,
            json=invoice_data
        )
        assert sale_response.status_code == 200, f"Sale should succeed: {sale_response.text}"
        
        # Verify parent inventory was deducted
        # 5 pieces sold / 10 units_per_parent = 0.5 parent units deducted
        inv_response2 = requests.get(
            f"{BASE_URL}/api/inventory",
            headers=auth_headers,
            params={"branch_id": BRANCH_ID, "search": test_parent_product["name"]}
        )
        assert inv_response2.status_code == 200
        items2 = inv_response2.json()["items"]
        parent_items2 = [i for i in items2 if i["id"] == test_parent_product["id"]]
        parent_stock_after = parent_items2[0]["total_stock"] if parent_items2 else 0
        
        expected_stock = parent_stock_before - (5 / 10)  # 5 pieces sold / 10 per parent = 0.5 deducted
        assert abs(parent_stock_after - expected_stock) < 0.01, \
            f"Parent stock should be {expected_stock}, got {parent_stock_after}"
        print(f"✓ Repack sale correctly deducted from parent: {parent_stock_before} -> {parent_stock_after}")


class TestExistingRepacks:
    """Test with existing repacks in the system"""
    
    def test_existing_repack_shows_derived_quantities(self, auth_headers):
        """Verify existing repacks in inventory show derived quantities"""
        # R Lannate 250g - known repack with parent_id
        response = requests.get(
            f"{BASE_URL}/api/inventory",
            headers=auth_headers,
            params={"branch_id": BRANCH_ID, "search": "R Lannate"}
        )
        assert response.status_code == 200
        data = response.json()
        
        repacks = [i for i in data["items"] if i.get("is_repack") is True]
        if repacks:
            repack = repacks[0]
            assert repack.get("derived_from_parent") is True
            assert repack.get("parent_name") is not None
            assert repack.get("units_per_parent") is not None
            print(f"✓ Existing repack '{repack['name']}' shows derived_from_parent=true")
            print(f"  Parent: {repack.get('parent_name')}, units_per_parent: {repack.get('units_per_parent')}")
        else:
            pytest.skip("No existing repacks found in search")
    
    def test_existing_repack_inventory_adjustment_blocked(self, auth_headers):
        """Verify existing repacks cannot be adjusted"""
        # Get an existing repack
        response = requests.get(
            f"{BASE_URL}/api/products",
            headers=auth_headers,
            params={"is_repack": True, "limit": 1}
        )
        assert response.status_code == 200
        products = response.json()["products"]
        
        if not products:
            pytest.skip("No existing repacks found")
        
        repack = products[0]
        
        # Try to adjust
        adjust_response = requests.post(
            f"{BASE_URL}/api/inventory/adjust",
            headers=auth_headers,
            json={
                "product_id": repack["id"],
                "branch_id": BRANCH_ID,
                "quantity": 1,
                "reason": "Test"
            }
        )
        
        assert adjust_response.status_code == 400
        assert "Cannot adjust repack" in adjust_response.json().get("detail", "")
        print(f"✓ Existing repack '{repack['name']}' inventory adjustment correctly blocked")


class TestRepackBadgeMultiplier:
    """Test that repack badge shows correct multiplier"""
    
    def test_repack_has_units_per_parent(self, auth_headers, test_repack_product):
        """Repack should have units_per_parent field for badge display"""
        response = requests.get(
            f"{BASE_URL}/api/inventory",
            headers=auth_headers,
            params={"branch_id": BRANCH_ID, "search": "TEST_REPACK"}
        )
        assert response.status_code == 200
        data = response.json()
        
        repack_items = [i for i in data["items"] if i["id"] == test_repack_product["id"]]
        repack = repack_items[0]
        
        assert repack.get("units_per_parent") == 10, "units_per_parent should be 10"
        print(f"✓ Repack has units_per_parent={repack.get('units_per_parent')} for badge (×10)")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

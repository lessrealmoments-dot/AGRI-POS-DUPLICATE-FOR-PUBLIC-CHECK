"""
Test Terminal Barcode Scanner Features - Iteration 157
Tests:
1. Backend barcode-lookup API: GET /api/products/barcode-lookup/{barcode}
2. Backend credential-pair API: POST /api/terminal/credential-pair
3. Terminal session validation
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials from test_credentials.md
SUPER_ADMIN_EMAIL = "janmarkeahig@gmail.com"
SUPER_ADMIN_PASSWORD = "Aa@58798546521325"
MANAGER_PIN = "521325"
BRANCH_1_ID = "c435277f-9fc7-4d83-83e7-38be5b4423ac"


@pytest.fixture(scope="module")
def auth_token():
    """Get authentication token via credential-pair (terminal login)"""
    response = requests.post(f"{BASE_URL}/api/terminal/credential-pair", json={
        "email": SUPER_ADMIN_EMAIL,
        "password": SUPER_ADMIN_PASSWORD,
        "branch_id": BRANCH_1_ID
    })
    if response.status_code == 200:
        data = response.json()
        if data.get("status") == "paired":
            return data.get("token")
        elif data.get("status") == "select_branch":
            # Admin needs to select branch - retry with branch_id
            response2 = requests.post(f"{BASE_URL}/api/terminal/credential-pair", json={
                "email": SUPER_ADMIN_EMAIL,
                "password": SUPER_ADMIN_PASSWORD,
                "branch_id": BRANCH_1_ID
            })
            if response2.status_code == 200:
                return response2.json().get("token")
    pytest.skip(f"Authentication failed: {response.status_code} - {response.text}")


@pytest.fixture(scope="module")
def api_client():
    """Shared requests session"""
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    return session


@pytest.fixture(scope="module")
def authenticated_client(api_client, auth_token):
    """Session with auth header"""
    api_client.headers.update({"Authorization": f"Bearer {auth_token}"})
    return api_client


class TestCredentialPairAPI:
    """Tests for POST /api/terminal/credential-pair endpoint"""
    
    def test_credential_pair_missing_email(self, api_client):
        """Test credential-pair with missing email returns 400"""
        response = api_client.post(f"{BASE_URL}/api/terminal/credential-pair", json={
            "password": SUPER_ADMIN_PASSWORD,
            "branch_id": BRANCH_1_ID
        })
        assert response.status_code == 400
        assert "email" in response.json().get("detail", "").lower() or "required" in response.json().get("detail", "").lower()
        print("PASS: Missing email returns 400")
    
    def test_credential_pair_missing_password(self, api_client):
        """Test credential-pair with missing password returns 400"""
        response = api_client.post(f"{BASE_URL}/api/terminal/credential-pair", json={
            "email": SUPER_ADMIN_EMAIL,
            "branch_id": BRANCH_1_ID
        })
        assert response.status_code == 400
        assert "password" in response.json().get("detail", "").lower() or "required" in response.json().get("detail", "").lower()
        print("PASS: Missing password returns 400")
    
    def test_credential_pair_invalid_credentials(self, api_client):
        """Test credential-pair with wrong password returns 401"""
        response = api_client.post(f"{BASE_URL}/api/terminal/credential-pair", json={
            "email": SUPER_ADMIN_EMAIL,
            "password": "wrongpassword123",
            "branch_id": BRANCH_1_ID
        })
        assert response.status_code == 401
        assert "invalid" in response.json().get("detail", "").lower()
        print("PASS: Invalid credentials returns 401")
    
    def test_credential_pair_nonexistent_user(self, api_client):
        """Test credential-pair with non-existent email returns 401"""
        response = api_client.post(f"{BASE_URL}/api/terminal/credential-pair", json={
            "email": "nonexistent@example.com",
            "password": "anypassword",
            "branch_id": BRANCH_1_ID
        })
        assert response.status_code == 401
        print("PASS: Non-existent user returns 401")
    
    def test_credential_pair_admin_without_branch_returns_select_branch(self, api_client):
        """Test admin login without branch_id returns select_branch status"""
        response = api_client.post(f"{BASE_URL}/api/terminal/credential-pair", json={
            "email": SUPER_ADMIN_EMAIL,
            "password": SUPER_ADMIN_PASSWORD
            # No branch_id
        })
        assert response.status_code == 200
        data = response.json()
        assert data.get("status") == "select_branch"
        assert "branches" in data
        assert isinstance(data["branches"], list)
        assert data.get("is_admin") == True
        print(f"PASS: Admin without branch_id returns select_branch with {len(data['branches'])} branches")
    
    def test_credential_pair_admin_with_branch_returns_paired(self, api_client):
        """Test admin login with branch_id returns paired status"""
        response = api_client.post(f"{BASE_URL}/api/terminal/credential-pair", json={
            "email": SUPER_ADMIN_EMAIL,
            "password": SUPER_ADMIN_PASSWORD,
            "branch_id": BRANCH_1_ID
        })
        assert response.status_code == 200
        data = response.json()
        assert data.get("status") == "paired"
        assert "token" in data
        assert "terminal_id" in data
        assert data.get("branch_id") == BRANCH_1_ID
        assert "branch_name" in data
        assert "user_name" in data
        print(f"PASS: Admin with branch_id returns paired - terminal_id: {data['terminal_id'][:8]}...")
    
    def test_credential_pair_invalid_branch_returns_404(self, api_client):
        """Test credential-pair with invalid branch_id returns 404"""
        response = api_client.post(f"{BASE_URL}/api/terminal/credential-pair", json={
            "email": SUPER_ADMIN_EMAIL,
            "password": SUPER_ADMIN_PASSWORD,
            "branch_id": "nonexistent-branch-id-12345"
        })
        assert response.status_code == 404
        assert "branch" in response.json().get("detail", "").lower()
        print("PASS: Invalid branch_id returns 404")


class TestBarcodeLookupAPI:
    """Tests for GET /api/products/barcode-lookup/{barcode} endpoint"""
    
    def test_barcode_lookup_nonexistent_barcode(self, authenticated_client):
        """Test barcode lookup with non-existent barcode returns 404"""
        response = authenticated_client.get(f"{BASE_URL}/api/products/barcode-lookup/NONEXISTENT123456")
        assert response.status_code == 404
        assert "barcode" in response.json().get("detail", "").lower() or "product" in response.json().get("detail", "").lower()
        print("PASS: Non-existent barcode returns 404")
    
    def test_barcode_lookup_requires_auth(self, api_client):
        """Test barcode lookup without auth returns 401/403"""
        # Remove auth header if present
        headers = {"Content-Type": "application/json"}
        response = requests.get(f"{BASE_URL}/api/products/barcode-lookup/ANYBARCODE", headers=headers)
        assert response.status_code in [401, 403]
        print("PASS: Barcode lookup requires authentication")
    
    def test_barcode_lookup_with_branch_id(self, authenticated_client):
        """Test barcode lookup with branch_id parameter"""
        # This should return 404 for non-existent barcode but accept the branch_id param
        response = authenticated_client.get(
            f"{BASE_URL}/api/products/barcode-lookup/TESTBARCODE123",
            params={"branch_id": BRANCH_1_ID}
        )
        # Should be 404 (barcode not found) not 400 (bad request)
        assert response.status_code == 404
        print("PASS: Barcode lookup accepts branch_id parameter")


class TestProductsWithBarcodes:
    """Tests to find and verify products with barcodes exist"""
    
    def test_list_products_endpoint_works(self, authenticated_client):
        """Test that products list endpoint works"""
        response = authenticated_client.get(f"{BASE_URL}/api/products", params={"limit": 10})
        assert response.status_code == 200
        data = response.json()
        assert "products" in data
        assert "total" in data
        print(f"PASS: Products list works - {data['total']} total products")
    
    def test_find_product_with_barcode(self, authenticated_client):
        """Find a product with a barcode and test lookup"""
        # Get products list
        response = authenticated_client.get(f"{BASE_URL}/api/products", params={"limit": 100})
        assert response.status_code == 200
        products = response.json().get("products", [])
        
        # Find a product with a barcode
        product_with_barcode = None
        for p in products:
            if p.get("barcode") and len(p.get("barcode", "")) > 0:
                product_with_barcode = p
                break
        
        if not product_with_barcode:
            pytest.skip("No products with barcodes found in database")
        
        barcode = product_with_barcode["barcode"]
        print(f"Found product with barcode: {barcode} - {product_with_barcode['name']}")
        
        # Now test the barcode lookup
        lookup_response = authenticated_client.get(f"{BASE_URL}/api/products/barcode-lookup/{barcode}")
        assert lookup_response.status_code == 200
        lookup_data = lookup_response.json()
        
        # Verify response structure
        assert lookup_data.get("id") == product_with_barcode["id"]
        assert lookup_data.get("name") == product_with_barcode["name"]
        assert lookup_data.get("barcode") == barcode
        assert "available" in lookup_data  # Stock info should be included
        assert "prices" in lookup_data
        
        print(f"PASS: Barcode lookup returns correct product - {lookup_data['name']}, available: {lookup_data.get('available', 0)}")
    
    def test_barcode_lookup_with_branch_price_override(self, authenticated_client):
        """Test barcode lookup includes branch-specific prices when branch_id provided"""
        # Get products list
        response = authenticated_client.get(f"{BASE_URL}/api/products", params={"limit": 100})
        products = response.json().get("products", [])
        
        # Find a product with a barcode
        product_with_barcode = None
        for p in products:
            if p.get("barcode") and len(p.get("barcode", "")) > 0:
                product_with_barcode = p
                break
        
        if not product_with_barcode:
            pytest.skip("No products with barcodes found")
        
        barcode = product_with_barcode["barcode"]
        
        # Lookup with branch_id
        lookup_response = authenticated_client.get(
            f"{BASE_URL}/api/products/barcode-lookup/{barcode}",
            params={"branch_id": BRANCH_1_ID}
        )
        assert lookup_response.status_code == 200
        lookup_data = lookup_response.json()
        
        # Should have prices (may be merged with branch overrides)
        assert "prices" in lookup_data
        assert "available" in lookup_data
        
        print(f"PASS: Barcode lookup with branch_id works - prices: {lookup_data.get('prices', {})}")


class TestTerminalSession:
    """Tests for terminal session management"""
    
    def test_terminal_session_endpoint(self, authenticated_client):
        """Test GET /api/terminal/session returns session info"""
        response = authenticated_client.get(f"{BASE_URL}/api/terminal/session")
        # May return 404 if no active session, or 200 with session data
        assert response.status_code in [200, 404]
        
        if response.status_code == 200:
            data = response.json()
            assert "terminal_id" in data
            assert "branch_id" in data
            assert "branch_name" in data
            print(f"PASS: Terminal session active - branch: {data.get('branch_name')}")
        else:
            print("PASS: Terminal session endpoint works (no active session)")
    
    def test_terminal_branches_list(self, authenticated_client):
        """Test GET /api/terminal/branches returns branch list"""
        response = authenticated_client.get(f"{BASE_URL}/api/terminal/branches")
        assert response.status_code == 200
        branches = response.json()
        assert isinstance(branches, list)
        if len(branches) > 0:
            assert "id" in branches[0]
            assert "name" in branches[0]
        print(f"PASS: Terminal branches list works - {len(branches)} branches")


class TestScanQuantityDialogElements:
    """Verify the scan quantity dialog data-testid elements exist in the component"""
    
    def test_terminal_sales_component_has_required_testids(self):
        """Verify TerminalSales.jsx has the required data-testid attributes"""
        # Read the component file
        component_path = "/app/frontend/src/pages/terminal/TerminalSales.jsx"
        with open(component_path, 'r') as f:
            content = f.read()
        
        # Check for required data-testid attributes
        required_testids = [
            'data-testid="terminal-search-input"',
            'data-testid="scan-qty-input"',
            'data-testid="scan-qty-confirm-btn"',
            'data-testid="scan-qty-auto-btn"',
            'data-testid="scan-qty-title"',
        ]
        
        missing = []
        for testid in required_testids:
            if testid not in content:
                missing.append(testid)
        
        if missing:
            pytest.fail(f"Missing data-testid attributes in TerminalSales.jsx: {missing}")
        
        print(f"PASS: All {len(required_testids)} required data-testid attributes found in TerminalSales.jsx")
        
        # Also verify scan detection constants
        assert "SCAN_CHAR_SPEED = 50" in content, "SCAN_CHAR_SPEED constant not found"
        assert "SCAN_MIN_CHARS = 4" in content, "SCAN_MIN_CHARS constant not found"
        assert "SCAN_COOLDOWN = 1500" in content, "SCAN_COOLDOWN constant not found"
        
        print("PASS: Scan detection constants (SCAN_CHAR_SPEED=50, SCAN_MIN_CHARS=4, SCAN_COOLDOWN=1500) found")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

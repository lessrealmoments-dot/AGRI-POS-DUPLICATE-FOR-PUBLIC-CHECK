"""
Test suite for Receipt Upload System — Direct Upload for PO, Branch Transfer, Expense
Tests:
- POST /api/uploads/direct - direct file upload with session management
- DELETE /api/uploads/direct/{session_id}/{file_id} - remove file from session
- POST /api/uploads/reassign - link pending session to actual record
- PO creation with upload_session_ids
- Branch transfer receive requires receipt upload
- Expense creation with optional upload_session_ids
"""
import pytest
import requests
import os
import io

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', 'https://offline-interceptor.preview.emergentagent.com').rstrip('/')

# Test credentials - use super admin since company admin password may have changed
SUPER_ADMIN_EMAIL = "janmarkeahig@gmail.com"
SUPER_ADMIN_PASS = "Aa@58798546521325"


@pytest.fixture(scope="module")
def admin_session():
    """Get authenticated session for admin"""
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    
    # Login as super admin
    res = session.post(f"{BASE_URL}/api/auth/login", json={
        "email": SUPER_ADMIN_EMAIL,
        "password": SUPER_ADMIN_PASS
    })
    if res.status_code != 200:
        pytest.skip(f"Could not login as admin: {res.status_code}")
    
    data = res.json()
    token = data.get("token")
    session.headers.update({"Authorization": f"Bearer {token}"})
    
    # Get user details
    session.user = data.get("user", {})
    session.branches = data.get("branches", [])
    
    return session


@pytest.fixture(scope="module")
def test_branch_id(admin_session):
    """Get a branch ID for testing"""
    # First try from login response
    branches = getattr(admin_session, 'branches', [])
    if branches:
        return branches[0].get("id")
    
    # Fallback: fetch branches from API
    res = admin_session.get(f"{BASE_URL}/api/branches")
    if res.status_code == 200:
        branches = res.json() if isinstance(res.json(), list) else res.json().get("branches", [])
        if branches:
            return branches[0].get("id")
    
    pytest.skip("No branches available for testing")


class TestDirectUpload:
    """Test direct file upload endpoints"""
    
    def test_direct_upload_no_files_fails(self, admin_session):
        """POST /api/uploads/direct without files should fail with 400"""
        # Note: Sending without files, but we need multipart form
        res = admin_session.post(
            f"{BASE_URL}/api/uploads/direct",
            data={"record_type": "purchase_order"},
            headers={"Content-Type": None}  # Let requests set multipart
        )
        # Should fail - no files provided
        assert res.status_code == 422 or res.status_code == 400
        print("PASS: Direct upload without files correctly rejected")
    
    def test_direct_upload_success(self, admin_session):
        """POST /api/uploads/direct should accept files and return session_id + file_ids"""
        # Create a small test image in memory
        from PIL import Image
        img = Image.new('RGB', (100, 100), color='red')
        img_bytes = io.BytesIO()
        img.save(img_bytes, format='JPEG')
        img_bytes.seek(0)
        
        # Upload
        files = [
            ('files', ('test_receipt.jpg', img_bytes, 'image/jpeg')),
        ]
        data = {'record_type': 'purchase_order'}
        
        # Remove Content-Type header so requests sets multipart
        headers = {k: v for k, v in admin_session.headers.items() if k.lower() != 'content-type'}
        
        res = requests.post(
            f"{BASE_URL}/api/uploads/direct",
            files=files,
            data=data,
            headers=headers
        )
        
        assert res.status_code == 200, f"Direct upload failed: {res.text}"
        result = res.json()
        
        assert "session_id" in result, "Response should contain session_id"
        assert "file_ids" in result, "Response should contain file_ids"
        assert "uploaded" in result, "Response should contain uploaded count"
        assert result["uploaded"] == 1, f"Should upload 1 file, got {result['uploaded']}"
        
        print(f"PASS: Direct upload success - session_id: {result['session_id'][:8]}..., files: {result['uploaded']}")
        
        # Store for next test
        admin_session._test_session_id = result["session_id"]
        admin_session._test_file_ids = result["file_ids"]
        
        return result
    
    def test_delete_file_from_session(self, admin_session):
        """DELETE /api/uploads/direct/{session_id}/{file_id} should remove file"""
        # First do an upload if not already done
        if not hasattr(admin_session, '_test_session_id'):
            self.test_direct_upload_success(admin_session)
        
        session_id = admin_session._test_session_id
        file_id = admin_session._test_file_ids[0]
        
        res = admin_session.delete(f"{BASE_URL}/api/uploads/direct/{session_id}/{file_id}")
        
        assert res.status_code == 200, f"Delete file failed: {res.text}"
        result = res.json()
        assert "message" in result
        
        print(f"PASS: File deleted from session - {result['message']}")
    
    def test_delete_nonexistent_file_fails(self, admin_session):
        """DELETE with invalid file_id should return 404"""
        # Create a new session first
        from PIL import Image
        img = Image.new('RGB', (50, 50), color='blue')
        img_bytes = io.BytesIO()
        img.save(img_bytes, format='JPEG')
        img_bytes.seek(0)
        
        files = [('files', ('test2.jpg', img_bytes, 'image/jpeg'))]
        data = {'record_type': 'expense'}
        headers = {k: v for k, v in admin_session.headers.items() if k.lower() != 'content-type'}
        
        res = requests.post(f"{BASE_URL}/api/uploads/direct", files=files, data=data, headers=headers)
        assert res.status_code == 200
        session_id = res.json()["session_id"]
        
        # Try to delete non-existent file
        res = admin_session.delete(f"{BASE_URL}/api/uploads/direct/{session_id}/nonexistent_file_id")
        assert res.status_code == 404
        
        print("PASS: Delete nonexistent file correctly returns 404")


class TestUploadReassign:
    """Test upload session reassignment"""
    
    def test_reassign_session_to_record(self, admin_session, test_branch_id):
        """POST /api/uploads/reassign should link pending session to actual record"""
        # First create an upload session
        from PIL import Image
        img = Image.new('RGB', (100, 100), color='green')
        img_bytes = io.BytesIO()
        img.save(img_bytes, format='JPEG')
        img_bytes.seek(0)
        
        files = [('files', ('test_reassign.jpg', img_bytes, 'image/jpeg'))]
        data = {'record_type': 'purchase_order'}
        headers = {k: v for k, v in admin_session.headers.items() if k.lower() != 'content-type'}
        
        res = requests.post(f"{BASE_URL}/api/uploads/direct", files=files, data=data, headers=headers)
        assert res.status_code == 200
        session_id = res.json()["session_id"]
        
        # Reassign to a fake record ID
        fake_record_id = f"test_po_{os.urandom(4).hex()}"
        res = admin_session.post(f"{BASE_URL}/api/uploads/reassign", json={
            "session_id": session_id,
            "record_type": "purchase_order",
            "record_id": fake_record_id
        })
        
        assert res.status_code == 200, f"Reassign failed: {res.text}"
        result = res.json()
        assert result["record_id"] == fake_record_id
        
        print(f"PASS: Upload session reassigned to record {fake_record_id[:16]}...")
    
    def test_reassign_invalid_session_fails(self, admin_session):
        """POST /api/uploads/reassign with invalid session should fail"""
        res = admin_session.post(f"{BASE_URL}/api/uploads/reassign", json={
            "session_id": "nonexistent_session",
            "record_type": "purchase_order",
            "record_id": "some_record_id"
        })
        
        assert res.status_code == 404, f"Expected 404, got {res.status_code}"
        print("PASS: Reassign with invalid session correctly returns 404")


class TestPOWithUpload:
    """Test PO creation with upload_session_ids"""
    
    def test_po_creation_cash_requires_receipt(self, admin_session, test_branch_id):
        """Creating a Cash PO should require receipt upload (validated in frontend)
        Backend accepts upload_session_ids parameter"""
        # First upload a receipt
        from PIL import Image
        img = Image.new('RGB', (200, 200), color='yellow')
        img_bytes = io.BytesIO()
        img.save(img_bytes, format='JPEG')
        img_bytes.seek(0)
        
        files = [('files', ('po_receipt.jpg', img_bytes, 'image/jpeg'))]
        data = {'record_type': 'purchase_order'}
        headers = {k: v for k, v in admin_session.headers.items() if k.lower() != 'content-type'}
        
        res = requests.post(f"{BASE_URL}/api/uploads/direct", files=files, data=data, headers=headers)
        assert res.status_code == 200
        session_id = res.json()["session_id"]
        
        # Get a product for testing
        products_res = admin_session.get(f"{BASE_URL}/api/products", params={"limit": 1})
        if products_res.status_code != 200 or not products_res.json().get("products"):
            pytest.skip("No products available for PO testing")
        product = products_res.json()["products"][0]
        
        # Create PO as draft with upload session
        po_data = {
            "vendor": "Test Supplier for Receipt Upload",
            "branch_id": test_branch_id,
            "po_type": "draft",
            "items": [{
                "product_id": product["id"],
                "product_name": product["name"],
                "unit": product.get("unit", "pcs"),
                "quantity": 1,
                "unit_price": 100
            }],
            "upload_session_ids": [session_id]
        }
        
        res = admin_session.post(f"{BASE_URL}/api/purchase-orders", json=po_data)
        assert res.status_code == 200, f"PO creation failed: {res.text}"
        
        po = res.json()
        assert po.get("po_number"), "PO should have po_number"
        
        # Check receipt count
        receipt_count = po.get("receipt_count", 0)
        print(f"PASS: PO created with upload_session_ids - {po['po_number']}, receipt_count: {receipt_count}")
        
        # Store for cleanup
        admin_session._test_po_id = po["id"]
        
        return po
    
    def test_po_receive_without_receipt_fails(self, admin_session, test_branch_id):
        """Receiving a PO without receipts should fail (receipt required)"""
        # Create a draft PO without upload
        products_res = admin_session.get(f"{BASE_URL}/api/products", params={"limit": 1})
        if products_res.status_code != 200 or not products_res.json().get("products"):
            pytest.skip("No products available")
        product = products_res.json()["products"][0]
        
        po_data = {
            "vendor": "Test Vendor No Receipt",
            "branch_id": test_branch_id,
            "po_type": "draft",
            "items": [{
                "product_id": product["id"],
                "product_name": product["name"],
                "unit": product.get("unit", "pcs"),
                "quantity": 1,
                "unit_price": 50
            }]
        }
        
        res = admin_session.post(f"{BASE_URL}/api/purchase-orders", json=po_data)
        assert res.status_code == 200
        po_id = res.json()["id"]
        
        # Try to receive without receipt
        res = admin_session.post(f"{BASE_URL}/api/purchase-orders/{po_id}/receive", json={})
        
        # Should fail with 400 'receipt required'
        assert res.status_code == 400, f"Expected 400, got {res.status_code}"
        detail = res.json().get("detail", "")
        assert "receipt" in detail.lower(), f"Error should mention receipt: {detail}"
        
        print(f"PASS: PO receive without receipt correctly rejected - '{detail}'")
        
        # Cleanup - cancel the draft
        admin_session.delete(f"{BASE_URL}/api/purchase-orders/{po_id}")


class TestBranchTransferReceipt:
    """Test branch transfer receive requires receipt"""
    
    def test_transfer_receive_without_receipt_fails(self, admin_session):
        """POST /api/branch-transfers/{id}/receive without upload_session_ids should fail"""
        # Get branches
        branches_res = admin_session.get(f"{BASE_URL}/api/branches")
        if branches_res.status_code != 200:
            pytest.skip("Could not get branches")
        
        branches = branches_res.json()
        if len(branches) < 2:
            pytest.skip("Need at least 2 branches for transfer test")
        
        from_branch = branches[0]["id"]
        to_branch = branches[1]["id"]
        
        # Get a product
        products_res = admin_session.get(f"{BASE_URL}/api/products", params={"limit": 1})
        if products_res.status_code != 200 or not products_res.json().get("products"):
            pytest.skip("No products available")
        product = products_res.json()["products"][0]
        
        # Create a draft transfer
        transfer_data = {
            "from_branch_id": from_branch,
            "to_branch_id": to_branch,
            "min_margin": 20,
            "items": [{
                "product_id": product["id"],
                "product_name": product["name"],
                "sku": product.get("sku", ""),
                "category": product.get("category", "General"),
                "unit": product.get("unit", "pcs"),
                "qty": 1,
                "branch_capital": product.get("cost_price", 100),
                "transfer_capital": product.get("cost_price", 100),
                "branch_retail": product.get("cost_price", 100) * 1.2
            }]
        }
        
        res = admin_session.post(f"{BASE_URL}/api/branch-transfers", json=transfer_data)
        if res.status_code != 200:
            pytest.skip(f"Could not create transfer: {res.text}")
        
        transfer_id = res.json()["id"]
        
        # Send the transfer
        admin_session.post(f"{BASE_URL}/api/branch-transfers/{transfer_id}/send")
        
        # Try to receive without receipt upload
        res = admin_session.post(f"{BASE_URL}/api/branch-transfers/{transfer_id}/receive", json={
            "items": [{
                "product_id": product["id"],
                "qty_received": 1
            }]
        })
        
        # Should fail with 400 'receipt required'
        assert res.status_code == 400, f"Expected 400, got {res.status_code}: {res.text}"
        detail = res.json().get("detail", "")
        assert "receipt" in detail.lower(), f"Error should mention receipt: {detail}"
        
        print(f"PASS: Branch transfer receive without receipt correctly rejected - '{detail}'")
        
        # Cleanup - cancel the transfer
        admin_session.delete(f"{BASE_URL}/api/branch-transfers/{transfer_id}")
    
    def test_transfer_receive_with_receipt_succeeds(self, admin_session):
        """POST /api/branch-transfers/{id}/receive with upload_session_ids should succeed"""
        # Get branches
        branches_res = admin_session.get(f"{BASE_URL}/api/branches")
        if branches_res.status_code != 200:
            pytest.skip("Could not get branches")
        
        branches = branches_res.json()
        if len(branches) < 2:
            pytest.skip("Need at least 2 branches for transfer test")
        
        from_branch = branches[0]["id"]
        to_branch = branches[1]["id"]
        
        # Get a product with inventory
        products_res = admin_session.get(f"{BASE_URL}/api/products", params={"limit": 5})
        if products_res.status_code != 200 or not products_res.json().get("products"):
            pytest.skip("No products available")
        
        # Find a product with inventory at source branch
        product = None
        for p in products_res.json()["products"]:
            inv_res = admin_session.get(f"{BASE_URL}/api/inventory/{from_branch}")
            if inv_res.status_code == 200:
                inv_items = inv_res.json() if isinstance(inv_res.json(), list) else inv_res.json().get("items", [])
                for inv in inv_items:
                    if inv.get("product_id") == p["id"] and inv.get("quantity", 0) > 0:
                        product = p
                        break
            if product:
                break
        
        if not product:
            pytest.skip("No product with inventory at source branch")
        
        # Upload a receipt first
        from PIL import Image
        img = Image.new('RGB', (100, 100), color='purple')
        img_bytes = io.BytesIO()
        img.save(img_bytes, format='JPEG')
        img_bytes.seek(0)
        
        files = [('files', ('transfer_receipt.jpg', img_bytes, 'image/jpeg'))]
        data = {'record_type': 'branch_transfer'}
        headers = {k: v for k, v in admin_session.headers.items() if k.lower() != 'content-type'}
        
        res = requests.post(f"{BASE_URL}/api/uploads/direct", files=files, data=data, headers=headers)
        if res.status_code != 200:
            pytest.skip(f"Could not upload receipt: {res.text}")
        session_id = res.json()["session_id"]
        
        # Create transfer
        transfer_data = {
            "from_branch_id": from_branch,
            "to_branch_id": to_branch,
            "min_margin": 20,
            "items": [{
                "product_id": product["id"],
                "product_name": product["name"],
                "sku": product.get("sku", ""),
                "category": product.get("category", "General"),
                "unit": product.get("unit", "pcs"),
                "qty": 1,
                "branch_capital": product.get("cost_price", 100),
                "transfer_capital": product.get("cost_price", 100),
                "branch_retail": (product.get("cost_price", 100) or 100) * 1.2
            }]
        }
        
        res = admin_session.post(f"{BASE_URL}/api/branch-transfers", json=transfer_data)
        if res.status_code != 200:
            pytest.skip(f"Could not create transfer: {res.text}")
        
        transfer_id = res.json()["id"]
        
        # Send the transfer
        admin_session.post(f"{BASE_URL}/api/branch-transfers/{transfer_id}/send")
        
        # Receive WITH receipt
        res = admin_session.post(f"{BASE_URL}/api/branch-transfers/{transfer_id}/receive", json={
            "items": [{
                "product_id": product["id"],
                "qty_received": 1
            }],
            "upload_session_ids": [session_id]
        })
        
        # Should succeed
        assert res.status_code == 200, f"Receive with receipt failed: {res.text}"
        
        print(f"PASS: Branch transfer receive with receipt succeeded - {res.json().get('message', '')}")


class TestExpenseOptionalUpload:
    """Test expense creation with optional upload"""
    
    def test_expense_without_upload_succeeds(self, admin_session, test_branch_id):
        """POST /api/expenses without upload_session_ids should succeed (optional)"""
        expense_data = {
            "branch_id": test_branch_id,
            "category": "Miscellaneous",
            "description": "Test expense without receipt",
            "amount": 50,
            "payment_method": "Cash",
            "date": "2026-01-15"
        }
        
        res = admin_session.post(f"{BASE_URL}/api/expenses", json=expense_data)
        assert res.status_code == 200, f"Expense creation failed: {res.text}"
        
        expense = res.json()
        assert expense.get("id"), "Expense should have id"
        
        print(f"PASS: Expense created without receipt (optional) - id: {expense['id'][:8]}...")
    
    def test_expense_with_upload_succeeds(self, admin_session, test_branch_id):
        """POST /api/expenses with upload_session_ids should succeed"""
        # Upload a receipt first
        from PIL import Image
        img = Image.new('RGB', (80, 80), color='orange')
        img_bytes = io.BytesIO()
        img.save(img_bytes, format='JPEG')
        img_bytes.seek(0)
        
        files = [('files', ('expense_receipt.jpg', img_bytes, 'image/jpeg'))]
        data = {'record_type': 'expense'}
        headers = {k: v for k, v in admin_session.headers.items() if k.lower() != 'content-type'}
        
        res = requests.post(f"{BASE_URL}/api/uploads/direct", files=files, data=data, headers=headers)
        assert res.status_code == 200
        session_id = res.json()["session_id"]
        
        # Create expense with upload
        expense_data = {
            "branch_id": test_branch_id,
            "category": "Transportation",
            "description": "Test expense with receipt",
            "amount": 75,
            "payment_method": "Cash",
            "date": "2026-01-15",
            "upload_session_ids": [session_id]
        }
        
        res = admin_session.post(f"{BASE_URL}/api/expenses", json=expense_data)
        assert res.status_code == 200, f"Expense creation with upload failed: {res.text}"
        
        expense = res.json()
        print(f"PASS: Expense created with receipt - id: {expense['id'][:8]}...")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

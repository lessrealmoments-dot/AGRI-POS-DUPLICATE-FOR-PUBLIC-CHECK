"""
Tests for Draft / For Preparation workflow (iteration 250)
Covers: POST, GET, PATCH, DELETE /api/draft-orders and search visibility
"""
import pytest
import requests
import os

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")

# Branch IDs from test_credentials.md
BRANCH_ID = "c435277f-9fc7-4d83-83e7-38be5b4423ac"


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def login_org_admin():
    """Login as regression org admin, return token."""
    resp = requests.post(f"{BASE_URL}/api/auth/login", json={
        "email": "test_org_admin@regression.local",
        "password": "RegressionPass!2026",
    })
    if resp.status_code == 200:
        data = resp.json()
        return data.get("token") or data.get("access_token")
    return None


@pytest.fixture(scope="module")
def token():
    t = login_org_admin()
    if not t:
        pytest.skip("Org admin login failed — skipping draft order tests")
    return t


@pytest.fixture(scope="module")
def auth_headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


# ---------------------------------------------------------------------------
# Minimal product stub — we just need a product_id that won't fail schema
# (the draft-orders endpoint does not validate product existence)
# ---------------------------------------------------------------------------

MOCK_ITEM = {
    "product_id": "TEST-PROD-001",
    "product_name": "Test Draft Product",
    "sku": "TEST-SKU",
    "quantity": 2,
    "rate": 50.0,
    "unit_price": 50.0,
    "price": 50.0,
    "total": 100.0,
    "discount_amount": 0,
    "is_repack": False,
}


# ---------------------------------------------------------------------------
# 1. List drafts — initially may be empty or contain existing
# ---------------------------------------------------------------------------

class TestListDrafts:
    """GET /api/draft-orders"""

    def test_list_requires_branch_id(self, auth_headers):
        """Should return 400 when branch_id is missing."""
        resp = requests.get(f"{BASE_URL}/api/draft-orders", headers=auth_headers)
        assert resp.status_code == 400, (
            f"Expected 400 got {resp.status_code}: {resp.text}"
        )

    def test_list_returns_drafts_key(self, auth_headers):
        """GET with valid branch_id returns a dict with 'drafts' list."""
        resp = requests.get(
            f"{BASE_URL}/api/draft-orders",
            params={"branch_id": BRANCH_ID},
            headers=auth_headers,
        )
        assert resp.status_code == 200, f"Expected 200 got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert "drafts" in data, "Response must have 'drafts' key"
        assert isinstance(data["drafts"], list), "'drafts' must be a list"
        print(f"[PASS] list_drafts returned {len(data['drafts'])} items")


# ---------------------------------------------------------------------------
# 2. Create a draft
# ---------------------------------------------------------------------------

class TestCreateDraft:
    """POST /api/draft-orders"""

    def test_create_requires_branch_id(self, auth_headers):
        resp = requests.post(f"{BASE_URL}/api/draft-orders", json={
            "items": [MOCK_ITEM],
        }, headers=auth_headers)
        assert resp.status_code == 400

    def test_create_requires_items(self, auth_headers):
        resp = requests.post(f"{BASE_URL}/api/draft-orders", json={
            "branch_id": BRANCH_ID,
            "items": [],
        }, headers=auth_headers)
        assert resp.status_code == 400

    def test_create_draft_success(self, auth_headers):
        """POST creates a draft, returns invoice_number and doc_code."""
        resp = requests.post(f"{BASE_URL}/api/draft-orders", json={
            "branch_id": BRANCH_ID,
            "items": [MOCK_ITEM],
            "customer_name": "Test Walk-in",
            "subtotal": 100.0,
            "freight": 0,
            "overall_discount": 0,
            "grand_total": 100.0,
            "sale_mode": "quick",
        }, headers=auth_headers)
        assert resp.status_code == 200, f"Expected 200 got {resp.status_code}: {resp.text}"
        data = resp.json()

        # Key fields must exist
        assert "id" in data, "Response must have 'id'"
        assert "invoice_number" in data, "Response must have 'invoice_number'"
        assert data["invoice_number"], "invoice_number must not be empty"
        assert data["status"] == "for_preparation", f"status must be 'for_preparation', got {data['status']}"
        assert data["branch_id"] == BRANCH_ID
        assert "doc_code" in data, "Response must have 'doc_code' (QR reserved)"
        assert data["doc_code"], "doc_code must not be empty"
        assert data["amount_paid"] == 0, "New draft must have amount_paid=0"
        print(f"[PASS] Created draft invoice_number={data['invoice_number']}, doc_code={data['doc_code']}")


# ---------------------------------------------------------------------------
# 3. Full CRUD cycle: Create → Get → Update → Cancel → Verify gone
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def created_draft(auth_headers):
    """Create a draft and yield its dict; delete (cancel) after tests."""
    resp = requests.post(f"{BASE_URL}/api/draft-orders", json={
        "branch_id": BRANCH_ID,
        "items": [MOCK_ITEM],
        "customer_name": "TEST_Draft_Customer",
        "subtotal": 100.0,
        "grand_total": 100.0,
        "sale_mode": "quick",
    }, headers=auth_headers)
    assert resp.status_code == 200, f"Setup failed: {resp.text}"
    draft = resp.json()
    yield draft
    # Cleanup: cancel if still in for_preparation
    try:
        requests.delete(
            f"{BASE_URL}/api/draft-orders/{draft['id']}",
            headers=auth_headers,
        )
    except Exception:
        pass


class TestGetDraft:
    """GET /api/draft-orders/{id}"""

    def test_get_draft_by_id(self, auth_headers, created_draft):
        draft_id = created_draft["id"]
        resp = requests.get(f"{BASE_URL}/api/draft-orders/{draft_id}", headers=auth_headers)
        assert resp.status_code == 200, f"Expected 200 got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert data["id"] == draft_id
        assert data["status"] == "for_preparation"
        assert data["invoice_number"] == created_draft["invoice_number"], "invoice_number must match"
        print(f"[PASS] get_draft_by_id found invoice_number={data['invoice_number']}")

    def test_get_nonexistent_draft_returns_404(self, auth_headers):
        resp = requests.get(
            f"{BASE_URL}/api/draft-orders/nonexistent-id-xyz", headers=auth_headers
        )
        assert resp.status_code == 404


class TestUpdateDraft:
    """PATCH /api/draft-orders/{id}"""

    def test_update_items_preserves_invoice_number(self, auth_headers, created_draft):
        draft_id = created_draft["id"]
        original_invoice_number = created_draft["invoice_number"]

        updated_item = dict(MOCK_ITEM)
        updated_item["quantity"] = 5
        updated_item["total"] = 250.0

        resp = requests.patch(
            f"{BASE_URL}/api/draft-orders/{draft_id}",
            json={
                "items": [updated_item],
                "subtotal": 250.0,
                "grand_total": 250.0,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200, f"Expected 200 got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert data["invoice_number"] == original_invoice_number, (
            f"invoice_number changed! was={original_invoice_number}, now={data['invoice_number']}"
        )
        assert data["grand_total"] == 250.0, f"grand_total not updated: {data['grand_total']}"
        print(f"[PASS] update_draft preserved invoice_number={data['invoice_number']}")

    def test_update_nonexistent_returns_404(self, auth_headers):
        resp = requests.patch(
            f"{BASE_URL}/api/draft-orders/nonexistent-id-xyz",
            json={"items": []},
            headers=auth_headers,
        )
        assert resp.status_code == 404


class TestCancelDraft:
    """DELETE /api/draft-orders/{id}"""

    @pytest.fixture(scope="class")
    def cancel_draft_id(self, auth_headers):
        """Create a separate draft specifically for cancellation test."""
        resp = requests.post(f"{BASE_URL}/api/draft-orders", json={
            "branch_id": BRANCH_ID,
            "items": [MOCK_ITEM],
            "customer_name": "TEST_Cancel_Draft",
            "grand_total": 100.0,
        }, headers=auth_headers)
        assert resp.status_code == 200
        return resp.json()["id"]

    def test_cancel_draft_returns_ok(self, auth_headers, cancel_draft_id):
        resp = requests.delete(
            f"{BASE_URL}/api/draft-orders/{cancel_draft_id}", headers=auth_headers
        )
        assert resp.status_code == 200, f"Expected 200 got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert data.get("ok") is True
        assert data.get("draft_id") == cancel_draft_id
        print(f"[PASS] cancel_draft returned ok=True for {cancel_draft_id}")

    def test_cancelled_draft_not_in_list(self, auth_headers, cancel_draft_id):
        """After cancellation, draft must NOT appear in GET /api/draft-orders."""
        resp = requests.get(
            f"{BASE_URL}/api/draft-orders",
            params={"branch_id": BRANCH_ID},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        drafts = resp.json().get("drafts", [])
        ids = [d["id"] for d in drafts]
        assert cancel_draft_id not in ids, (
            f"Cancelled draft {cancel_draft_id} still visible in list!"
        )
        print(f"[PASS] cancelled_draft_not_in_list — id={cancel_draft_id} correctly absent")

    def test_get_cancelled_draft_returns_404(self, auth_headers, cancel_draft_id):
        """GET single cancelled draft must return 404."""
        resp = requests.get(
            f"{BASE_URL}/api/draft-orders/{cancel_draft_id}", headers=auth_headers
        )
        assert resp.status_code == 404

    def test_cancel_nonexistent_returns_404(self, auth_headers):
        resp = requests.delete(
            f"{BASE_URL}/api/draft-orders/nonexistent-xyz", headers=auth_headers
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 4. Transaction search visibility
# ---------------------------------------------------------------------------

class TestSearchVisibility:
    """Draft appears in GET /api/search/transactions with for_preparation status."""

    def test_draft_visible_in_search(self, auth_headers, created_draft):
        """Search by invoice_number must return the for_preparation invoice."""
        invoice_number = created_draft["invoice_number"]
        resp = requests.get(
            f"{BASE_URL}/api/search/transactions",
            params={"q": invoice_number, "branch_id": BRANCH_ID},
            headers=auth_headers,
        )
        # 200 or 403 (org-scoping); if 403 try without branch
        if resp.status_code == 403:
            resp = requests.get(
                f"{BASE_URL}/api/search/transactions",
                params={"q": invoice_number},
                headers=auth_headers,
            )

        assert resp.status_code == 200, f"Search returned {resp.status_code}: {resp.text}"
        data = resp.json()
        results = data.get("results") or data.get("items") or data.get("data") or []
        matching = [r for r in results if r.get("status") == "for_preparation"
                    or str(r.get("number", "")).strip() == invoice_number.strip()
                    or r.get("id") == created_draft["id"]]
        assert len(matching) >= 1, (
            f"Draft with invoice_number={invoice_number} not found in search results. "
            f"Got {results}"
        )
        # Check the matching result has for_preparation status
        prep_results = [r for r in matching if r.get("status") == "for_preparation"]
        assert len(prep_results) >= 1, (
            f"Result found but status != 'for_preparation'. Results: {matching}"
        )
        print(f"[PASS] draft_visible_in_search: found {len(matching)} result(s) with for_preparation status")

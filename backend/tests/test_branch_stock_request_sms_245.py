"""
Backend tests for Branch Stock Request SMS recipients & public doc view.
Covers iteration 245 review_request scope:
  - GET/PUT /api/sms/recipients/branch_stock_request
  - Permission gating on PUT (admin OK, manager 403)
  - Invalid trigger_key → 400
  - POST /api/purchase-orders with po_type='branch_request' fires SMS queue
  - SMS body contains public view link (/doc/<code>)
  - GET /api/doc-lookup/view/{doc_code} for branch_request PO returns
    is_branch_request=true and branch names (no auth)
"""
import os
import time
import uuid
import pytest
import requests
from pymongo import MongoClient

import sys
sys.path.insert(0, os.path.dirname(__file__))
from _org_test_helpers import (
    ensure_org_admin_token,
    TEST_ORG_ADMIN_EMAIL,
    TEST_ORG_ADMIN_PASSWORD,
)

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://pos-refund-engine.preview.emergentagent.com").rstrip("/")
API = BASE_URL + "/api"
MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "test_database")


@pytest.fixture(scope="module")
def admin_ctx():
    token, user = ensure_org_admin_token()
    return {"token": token, "user": user, "org_id": user.get("organization_id")}


@pytest.fixture(scope="module")
def db():
    return MongoClient(MONGO_URL)[DB_NAME]


@pytest.fixture(scope="module")
def admin_session(admin_ctx):
    s = requests.Session()
    s.headers.update({
        "Authorization": f"Bearer {admin_ctx['token']}",
        "Content-Type": "application/json",
    })
    return s


# ── Test 1: GET /sms/recipients defaults ─────────────────────────────────────
def test_get_recipient_config_returns_defaults(admin_session, db):
    # Clean the stored config so we get pure defaults
    db.sms_recipient_config.delete_many({"trigger_key": "branch_stock_request"})
    r = admin_session.get(f"{API}/sms/recipients/branch_stock_request")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["trigger_key"] == "branch_stock_request"
    assert data["include_admins"] is True
    assert data["include_supply_manager"] is True
    assert data["include_supply_auditor"] is False
    assert data["include_all_supply_users"] is False


# ── Test 2: PUT updates and persists ────────────────────────────────────────
def test_put_recipient_config_updates_and_persists(admin_session):
    payload = {
        "include_admins": False,
        "include_supply_manager": True,
        "include_supply_auditor": True,
        "include_all_supply_users": False,
    }
    r = admin_session.put(f"{API}/sms/recipients/branch_stock_request", json=payload)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["include_admins"] is False
    assert data["include_supply_auditor"] is True
    # GET to verify persistence
    g = admin_session.get(f"{API}/sms/recipients/branch_stock_request")
    assert g.status_code == 200
    gd = g.json()
    assert gd["include_admins"] is False
    assert gd["include_supply_auditor"] is True
    # Restore defaults for downstream tests
    admin_session.put(f"{API}/sms/recipients/branch_stock_request", json={
        "include_admins": True,
        "include_supply_manager": True,
        "include_supply_auditor": False,
        "include_all_supply_users": False,
    })


# ── Test 3: Invalid trigger_key → 400 ────────────────────────────────────────
def test_put_invalid_trigger_key_returns_400(admin_session):
    r = admin_session.put(f"{API}/sms/recipients/not_a_real_trigger", json={"include_admins": True})
    assert r.status_code == 400, r.text


# ── Test 4: PUT with manager role → 403 (settings:edit required) ─────────────
def test_put_requires_settings_edit_permission(db, admin_ctx):
    # Login as manager (regression manager has manager_pin but no settings:edit perm)
    mgr_email = "test_org_manager@regression.local"
    mgr_pwd = "RegressionMgrPass!2026"
    # Ensure manager exists in same org as admin
    org_id = admin_ctx["org_id"]
    db.users.update_one(
        {"email": mgr_email},
        {"$set": {"organization_id": org_id, "active": True, "role": "manager",
                  "permissions": {}}}
    )
    # Set/reset password
    import bcrypt
    db.users.update_one(
        {"email": mgr_email},
        {"$set": {"password_hash": bcrypt.hashpw(mgr_pwd.encode(), bcrypt.gensalt()).decode()}}
    )
    r = requests.post(f"{API}/auth/login", json={"email": mgr_email, "password": mgr_pwd}, timeout=15)
    if r.status_code != 200:
        pytest.skip(f"Manager login failed: {r.status_code} {r.text}")
    mgr_token = r.json()["token"]
    rr = requests.put(
        f"{API}/sms/recipients/branch_stock_request",
        headers={"Authorization": f"Bearer {mgr_token}", "Content-Type": "application/json"},
        json={"include_admins": False},
    )
    # Manager without settings:edit perm should be forbidden
    assert rr.status_code in (401, 403), f"Expected 403, got {rr.status_code}: {rr.text}"


# ── Test 5: Create branch_request PO → SMS queue fires for admins ────────────
@pytest.fixture(scope="module")
def created_branch_request_po(admin_session, admin_ctx, db):
    """Create a branch_request PO and return its dict + doc_code."""
    org_id = admin_ctx["org_id"]
    # Pick two distinct branches in this org
    branches = list(db.branches.find({"organization_id": org_id}, {"_id": 0, "id": 1, "name": 1}).limit(5))
    if len(branches) < 2:
        # Fallback: any 2 branches if org filter empty
        branches = list(db.branches.find({}, {"_id": 0, "id": 1, "name": 1}).limit(2))
    assert len(branches) >= 2, "Need at least 2 branches for branch_request test"
    requesting = branches[0]
    supply = branches[1]

    # Ensure an admin user with phone exists in org so SMS recipients > 0
    db.users.update_one(
        {"email": TEST_ORG_ADMIN_EMAIL},
        {"$set": {"phone": "+639170000245"}}
    )

    # Pick any product
    product = db.products.find_one({}, {"_id": 0, "id": 1, "name": 1})
    assert product, "No product available for PO"

    payload = {
        "vendor": "TEST_BranchRequest",
        "branch_id": requesting["id"],
        "supply_branch_id": supply["id"],
        "po_type": "branch_request",
        "items": [{
            "product_id": product["id"],
            "product_name": product.get("name", "TEST Product"),
            "quantity": 5,
            "rate": 10.0,
            "unit_price": 10.0,
        }],
        "purchase_date": "2026-01-15",
        "notes": "TEST_245 branch stock request",
    }
    r = admin_session.post(f"{API}/purchase-orders", json=payload)
    assert r.status_code in (200, 201), f"PO create failed: {r.status_code} {r.text}"
    po = r.json()
    assert po.get("po_type") == "branch_request"
    assert po.get("status") == "requested"
    # Allow background SMS queueing
    time.sleep(1.0)
    # Re-fetch to get doc_code if present
    po_doc = db.purchase_orders.find_one({"id": po["id"]}, {"_id": 0})
    yield {"po": po_doc or po, "requesting": requesting, "supply": supply}
    # Cleanup
    db.purchase_orders.delete_one({"id": po["id"]})
    db.sms_queue.delete_many({"trigger_ref": po["id"]})
    db.notifications.delete_many({"metadata.po_id": po["id"]})


def test_branch_request_po_creates_sms_queue_entries(admin_session, created_branch_request_po, db):
    po_id = created_branch_request_po["po"]["id"]
    # Verify queue entries exist for this PO
    entries = list(db.sms_queue.find({"trigger_ref": po_id, "template_key": "branch_stock_request"}, {"_id": 0}))
    assert len(entries) >= 1, f"Expected SMS queue entries for branch_request PO, found {len(entries)}"
    # Verify via API too
    r = admin_session.get(f"{API}/sms/queue", params={"template_key": "branch_stock_request", "trigger_ref": po_id})
    if r.status_code == 200:
        api_data = r.json()
        items = api_data if isinstance(api_data, list) else api_data.get("items") or api_data.get("queue") or []
        assert len(items) >= 1


def test_sms_queue_message_contains_public_view_link(created_branch_request_po, db):
    po_id = created_branch_request_po["po"]["id"]
    entries = list(db.sms_queue.find({"trigger_ref": po_id, "template_key": "branch_stock_request"}, {"_id": 0}))
    assert entries, "no queue entries to inspect"
    # Look at message_text / body / message field
    found_link = False
    for e in entries:
        body = (e.get("message_text") or e.get("body") or e.get("message") or "")
        if "/doc/" in body:
            found_link = True
            break
    assert found_link, f"No /doc/<code> link found in queued SMS bodies. Sample: {entries[0]}"


# ── Test 6: Public doc view returns branch_request fields (no auth) ─────────
def test_public_doc_view_returns_branch_request_metadata(created_branch_request_po, db):
    po_id = created_branch_request_po["po"]["id"]
    # Fetch doc_code from PO or doc_codes collection
    po_doc = db.purchase_orders.find_one({"id": po_id}, {"_id": 0, "doc_code": 1})
    doc_code = (po_doc or {}).get("doc_code")
    if not doc_code:
        dc = db.doc_codes.find_one({"doc_id": po_id, "doc_type": "purchase_order"}, {"_id": 0, "code": 1})
        doc_code = (dc or {}).get("code")
    assert doc_code, "doc_code missing for branch_request PO"
    # No auth header
    r = requests.get(f"{API}/doc/view/{doc_code}", timeout=15)
    assert r.status_code == 200, f"Public view failed: {r.status_code} {r.text}"
    data = r.json()
    assert data["doc_type"] == "purchase_order"
    assert data.get("is_branch_request") is True
    assert data.get("po_type") == "branch_request"
    assert data.get("supply_branch_name") == created_branch_request_po["supply"]["name"]
    assert data.get("requesting_branch_name") == created_branch_request_po["requesting"]["name"]
    # notes field should be present (added in iteration)
    assert "notes" in data

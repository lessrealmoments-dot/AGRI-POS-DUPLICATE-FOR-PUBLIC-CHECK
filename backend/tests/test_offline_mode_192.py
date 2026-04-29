"""
Iter 192 — Offline Mode Robustness Overhaul tests.

Validates:
  1. /sync/pos-data enrichment (search_blob, MA/LP, customer credit fields, admin_pin_hash)
  2. /sales/sync handles offline_bypass payload — creates signature_session(status=bypassed)
     + back-links to invoice
  3. envelope_id duplicate handling (idempotency)
  4. /sync/offline-summary returns correct shape
"""
from datetime import datetime, timezone
from uuid import uuid4

import pytest
import requests

from _org_test_helpers import API, _db, ensure_org_admin_token, TEST_ORG_ADMIN_PIN


@pytest.fixture(scope="module")
def auth():
    token, user = ensure_org_admin_token()
    return token, user


@pytest.fixture
def branch_id(auth):
    """Find or create a branch in the test org."""
    token, user = auth
    org_id = user.get("organization_id")
    db = _db()
    br = db.branches.find_one({"organization_id": org_id, "active": True}, {"_id": 0, "id": 1, "name": 1})
    if not br:
        # Seed a branch
        bid = str(uuid4())
        db.branches.insert_one({
            "id": bid, "name": "Test Branch", "active": True,
            "organization_id": org_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        return bid
    return br["id"]


def test_sync_posdata_enrichment(auth, branch_id):
    """search_blob + admin_pin_hash exposed; customer credit fields explicit."""
    token, _ = auth
    h = {"Authorization": f"Bearer {token}"}
    r = requests.get(f"{API}/sync/pos-data", params={"branch_id": branch_id}, headers=h, timeout=30)
    assert r.status_code == 200, r.text
    data = r.json()

    assert "products" in data
    assert "customers" in data
    # Phase 1: admin_pin_hash exposed (may be None if not seeded)
    assert "admin_pin_hash" in data
    # We seed admin_pin in _org_test_helpers — should be a bcrypt hash starting with $
    assert data["admin_pin_hash"], "admin_pin_hash should be cached"
    assert data["admin_pin_hash"].startswith("$2"), "should be bcrypt hash"

    # Phase 1.5: offline_pin_grants — branch-scoped manager + admin/owner PINs
    assert "offline_pin_grants" in data
    grants = data["offline_pin_grants"]
    assert isinstance(grants, list)
    # The test_org_admin user has owner_pin set → should appear in grants
    pins = [g.get("pin") for g in grants]
    assert TEST_ORG_ADMIN_PIN in pins, f"Expected admin PIN in grants, got: {grants}"
    # Manager PIN seeded by helper (521325) should also appear
    assert any(g.get("method") == "manager_pin" for g in grants), \
        "At least one manager_pin grant should be present"

    # search_blob present on each product
    if data["products"]:
        for p in data["products"][:5]:
            assert "search_blob" in p, f"search_blob missing on {p.get('name')}"
            blob = p["search_blob"]
            if p.get("name"):
                assert p["name"].lower() in blob

    # Customers carry credit fields explicitly
    if data["customers"]:
        c = data["customers"][0]
        assert "credit_limit" in c
        assert "credit_blocked_at" in c


def test_sales_sync_offline_bypass_creates_signature_session(auth, branch_id):
    """Offline credit sale + bypass → invoice + signature_session(bypassed) + back-linked."""
    token, user = auth
    org_id = user.get("organization_id")
    h = {"Authorization": f"Bearer {token}"}
    db = _db()

    # Seed a product
    pid = str(uuid4())
    db.products.insert_one({
        "id": pid, "name": "Off Test Item", "sku": f"OFFT-{pid[:6]}",
        "cost_price": 50, "prices": {"retail": 100}, "active": True,
        "organization_id": org_id, "is_repack": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    db.inventory.update_one(
        {"product_id": pid, "branch_id": branch_id},
        {"$setOnInsert": {"product_id": pid, "branch_id": branch_id, "organization_id": org_id, "quantity": 100}},
        upsert=True,
    )
    # Seed a customer
    cid = str(uuid4())
    db.customers.insert_one({
        "id": cid, "name": "Off Test Customer", "active": True,
        "organization_id": org_id, "branch_id": branch_id,
        "credit_limit": 5000, "balance": 0,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

    envelope_id = str(uuid4())
    sale_id = str(uuid4())
    sale = {
        "id": sale_id,
        "envelope_id": envelope_id,
        "branch_id": branch_id,
        "customer_id": cid,
        "customer_name": "Off Test Customer",
        "items": [{
            "product_id": pid,
            "product_name": "Off Test Item",
            "quantity": 1,
            "rate": 100,
            "price": 100,
            "total": 100,
        }],
        "subtotal": 100, "freight": 0, "overall_discount": 0,
        "grand_total": 100, "amount_paid": 0, "balance": 100,
        "status": "unpaid",
        "payment_type": "credit",
        "payment_method": "Credit",
        "invoice_number": f"OFFTEST-{sale_id[:6]}",
        "prefix": "OFFTEST",
        "order_date": datetime.now(timezone.utc).date().isoformat(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "offline_bypass": {
            "method": "admin_pin",
            "by_name": "Manager",
            "reason": "Customer in a hurry, paper slip signed",
            "at": datetime.now(timezone.utc).isoformat(),
            "credit_type": "by_term",
            "branch_name": "Test Branch",
        },
    }

    r = requests.post(f"{API}/sales/sync", json={"sales": [sale]}, headers=h, timeout=30)
    assert r.status_code == 200, r.text
    result = r.json()
    assert result["total_synced"] == 1, result

    # Verify invoice
    inv = db.invoices.find_one({"id": sale_id}, {"_id": 0})
    assert inv, "Invoice should be created"
    assert inv.get("offline_signature_origin") is True
    assert inv.get("signature_session_id"), "Bypass session should be back-linked"

    # Verify session
    sess_id = inv["signature_session_id"]
    sess = db.signature_sessions.find_one({"id": sess_id}, {"_id": 0})
    assert sess, "signature_session should exist"
    assert sess["status"] == "bypassed"
    assert sess["linked_record_type"] == "invoice"
    assert sess["linked_record_id"] == sale_id
    assert "hurry" in (sess.get("bypass_reason") or "").lower()
    assert sess.get("offline_origin") is True


def test_envelope_id_duplicate_returns_duplicate_status(auth, branch_id):
    """Re-posting the same envelope_id is idempotent (no dupe insert)."""
    token, user = auth
    org_id = user.get("organization_id")
    h = {"Authorization": f"Bearer {token}"}
    db = _db()

    pid = str(uuid4())
    db.products.insert_one({
        "id": pid, "name": "Dup Test Item", "sku": f"DUPT-{pid[:6]}",
        "cost_price": 25, "prices": {"retail": 50}, "active": True,
        "organization_id": org_id, "is_repack": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    db.inventory.update_one(
        {"product_id": pid, "branch_id": branch_id},
        {"$setOnInsert": {"product_id": pid, "branch_id": branch_id, "organization_id": org_id, "quantity": 100}},
        upsert=True,
    )

    envelope_id = str(uuid4())
    sale_id = str(uuid4())
    sale = {
        "id": sale_id,
        "envelope_id": envelope_id,
        "branch_id": branch_id,
        "items": [{"product_id": pid, "product_name": "Dup Test Item", "quantity": 1, "rate": 50, "total": 50}],
        "subtotal": 50, "grand_total": 50, "amount_paid": 50, "balance": 0,
        "status": "paid", "payment_type": "cash",
        "invoice_number": f"DUP-{sale_id[:6]}",
        "prefix": "DUP",
        "order_date": datetime.now(timezone.utc).date().isoformat(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    # First post — should sync
    r1 = requests.post(f"{API}/sales/sync", json={"sales": [sale]}, headers=h, timeout=30)
    assert r1.status_code == 200, r1.text
    res1 = r1.json()
    assert res1["total_synced"] == 1

    # Second post — same envelope_id, different sale_id, must be duplicate
    sale2 = {**sale, "id": str(uuid4()), "invoice_number": f"DUP2-{uuid4().hex[:6]}"}
    r2 = requests.post(f"{API}/sales/sync", json={"sales": [sale2]}, headers=h, timeout=30)
    assert r2.status_code == 200, r2.text
    res2 = r2.json()
    statuses = [s.get("status") for s in res2.get("results", [])]
    assert "duplicate" in statuses, res2

    # Only one invoice in DB with this envelope_id
    count = db.invoices.count_documents({"envelope_id": envelope_id})
    assert count == 1, f"Expected exactly 1 invoice, found {count}"


def test_offline_sync_summary_endpoint(auth, branch_id):
    """/sync/offline-summary returns shape with expected keys."""
    token, _ = auth
    h = {"Authorization": f"Bearer {token}"}
    r = requests.get(f"{API}/sync/offline-summary?days=7&branch_id={branch_id}", headers=h, timeout=30)
    assert r.status_code == 200, r.text
    data = r.json()
    assert "total_synced" in data
    assert "warned_count" in data
    assert "offline_credit_count" in data
    assert "samples" in data
    assert isinstance(data["samples"], list)
    # Days clamping
    r = requests.get(f"{API}/sync/offline-summary?days=999", headers=h, timeout=30)
    assert r.status_code == 200
    assert r.json()["period_days"] == 90  # clamped

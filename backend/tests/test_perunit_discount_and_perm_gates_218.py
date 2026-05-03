"""
Iter 218 — Per-unit discount math + permission gating regression tests.

Fixes covered:
  • Sales Order Mode: amount discount is now ₱X per unit × qty (not flat per-line).
  • /api/inventory/admin-adjust: rejects users without inventory.adjust perm.
  • /api/products (create/edit/delete) still gated (proves baseline unchanged).
"""
import os
import uuid
from datetime import datetime, timezone

import requests
from pymongo import MongoClient

from _org_test_helpers import ensure_org_admin_token, API

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "test_database")


def _db():
    return MongoClient(MONGO_URL)[DB_NAME]


def _h(token):
    return {"Authorization": f"Bearer {token}"}


def _make_product(db, org_id, name, cost=50.0, price=100.0):
    pid = str(uuid.uuid4())
    db.products.insert_one({
        "id": pid, "sku": f"T-{pid[:6]}", "name": name,
        "category": "Test", "unit": "pc", "cost_price": cost,
        "prices": {"retail": price}, "active": True,
        "is_repack": False, "organization_id": org_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    return pid


def _make_branch(db, org_id):
    br = db.branches.find_one({"organization_id": org_id, "active": True}, {"_id": 0, "id": 1, "name": 1})
    if br:
        return br["id"], br["name"]
    bid = str(uuid.uuid4())
    db.branches.insert_one({
        "id": bid, "name": "Test Branch 218", "active": True,
        "organization_id": org_id,
    })
    return bid, "Test Branch 218"


def _seed_stock(db, product_id, branch_id, qty, org_id):
    db.inventory.update_one(
        {"product_id": product_id, "branch_id": branch_id},
        {"$set": {"quantity": qty, "updated_at": datetime.now(timezone.utc).isoformat(),
                  "product_id": product_id, "branch_id": branch_id,
                  "organization_id": org_id},
         "$setOnInsert": {"id": str(uuid.uuid4())}},
        upsert=True,
    )


# ══════════════════════════════════════════════════════════════════════════
#  TEST 1 — Per-unit amount discount: ₱5 disc × qty 10 = ₱50 total discount
# ══════════════════════════════════════════════════════════════════════════
def test_amount_discount_is_per_unit_not_flat_per_line():
    token, admin_user = ensure_org_admin_token()
    db = _db()
    org_id = admin_user.get("organization_id") or db.organizations.find_one({}, {"id": 1})["id"]
    branch_id, branch_name = _make_branch(db, org_id)
    product_id = _make_product(db, org_id, "PerUnitDisc218", cost=20.0, price=100.0)
    _seed_stock(db, product_id, branch_id, 50, org_id)

    sale_payload = {
        "id": str(uuid.uuid4()),
        "branch_id": branch_id, "branch_name": branch_name,
        "items": [{
            "product_id": product_id, "product_name": "PerUnitDisc218",
            "sku": "PUD218",
            "quantity": 10, "rate": 100.0, "price": 100.0,
            "discount_type": "amount",
            "discount_value": 5,  # ₱5 per-unit
            # total: 10 * 100 - 10*5 = 950
        }],
        "subtotal": 950, "freight": 0, "overall_discount": 0,
        "grand_total": 950, "amount_paid": 950, "balance": 0,
        "payment_type": "cash", "payment_method": "Cash",
        "fund_source": "cashier", "price_scheme": "retail",
        "order_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "release_mode": "full", "customer_name": "Walk-in",
        "mode": "order",
    }
    r = requests.post(f"{API}/unified-sale", json=sale_payload, headers=_h(token), timeout=20)
    assert r.status_code == 200, f"Sale failed: {r.text}"
    sale = r.json()

    # Fetch the saved invoice via API — backend's own line_total must reflect per-unit math
    inv_id = sale["id"]
    r2 = requests.get(f"{API}/invoices/{inv_id}", headers=_h(token), timeout=15)
    assert r2.status_code == 200, f"Get invoice failed: {r2.text}"
    inv = r2.json()
    assert len(inv["items"]) == 1
    line = inv["items"][0]
    # discount_amount should be 10 units × ₱5 = ₱50 (not ₱5 flat)
    assert abs(line["discount_amount"] - 50.0) < 0.01, (
        f"Expected per-unit discount_amount=50 (10×5), got {line['discount_amount']}"
    )
    # line total = 10*100 - 50 = 950
    assert abs(line["total"] - 950.0) < 0.01, (
        f"Expected line total=950, got {line['total']}"
    )
    assert abs(inv["subtotal"] - 950.0) < 0.01


# ══════════════════════════════════════════════════════════════════════════
#  TEST 2 — Percent discount unchanged (still % of line total)
# ══════════════════════════════════════════════════════════════════════════
def test_percent_discount_math_unchanged():
    token, admin_user = ensure_org_admin_token()
    db = _db()
    org_id = admin_user.get("organization_id") or db.organizations.find_one({}, {"id": 1})["id"]
    branch_id, branch_name = _make_branch(db, org_id)
    product_id = _make_product(db, org_id, "PercentDisc218", cost=20.0, price=100.0)
    _seed_stock(db, product_id, branch_id, 50, org_id)

    sale_payload = {
        "id": str(uuid.uuid4()),
        "branch_id": branch_id, "branch_name": branch_name,
        "items": [{
            "product_id": product_id, "product_name": "PercentDisc218",
            "sku": "PCD218",
            "quantity": 10, "rate": 100.0, "price": 100.0,
            "discount_type": "percent",
            "discount_value": 10,  # 10% of line (10*100 = 1000) = 100
        }],
        "subtotal": 900, "freight": 0, "overall_discount": 0,
        "grand_total": 900, "amount_paid": 900, "balance": 0,
        "payment_type": "cash", "payment_method": "Cash",
        "fund_source": "cashier", "price_scheme": "retail",
        "order_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "release_mode": "full", "customer_name": "Walk-in",
        "mode": "order",
    }
    r = requests.post(f"{API}/unified-sale", json=sale_payload, headers=_h(token), timeout=20)
    assert r.status_code == 200, f"Sale failed: {r.text}"
    inv_id = r.json()["id"]
    r2 = requests.get(f"{API}/invoices/{inv_id}", headers=_h(token), timeout=15)
    assert r2.status_code == 200, r2.text
    inv = r2.json()
    line = inv["items"][0]
    assert abs(line["discount_amount"] - 100.0) < 0.01, (
        f"Expected percent discount_amount=100, got {line['discount_amount']}"
    )
    assert abs(line["total"] - 900.0) < 0.01


# ══════════════════════════════════════════════════════════════════════════
#  TEST 3 — /inventory/admin-adjust blocked without inventory.adjust perm
# ══════════════════════════════════════════════════════════════════════════
def test_admin_adjust_blocks_manager_without_inventory_adjust_perm():
    token, admin_user = ensure_org_admin_token()
    db = _db()
    org_id = admin_user.get("organization_id") or db.organizations.find_one({}, {"id": 1})["id"]
    branch_id, _ = _make_branch(db, org_id)
    product_id = _make_product(db, org_id, "PermGated218")

    # Create a manager user without inventory.adjust permission
    mgr_email = f"mgr_no_inv_{uuid.uuid4().hex[:6]}@regression.local"
    mgr_password = "MgrPass!2026"
    import bcrypt
    db.users.insert_one({
        "id": str(uuid.uuid4()),
        "username": mgr_email.split("@")[0],
        "email": mgr_email,
        "full_name": "No-Inv Manager 218",
        "password_hash": bcrypt.hashpw(mgr_password.encode(), bcrypt.gensalt()).decode(),
        "role": "manager",
        "active": True,
        "branch_id": branch_id,
        "organization_id": org_id,
        "permissions": {
            "dashboard": {"view": True},
            "products": {"view": True, "edit": True},
            "inventory": {"view": True, "adjust": False, "transfer": False},
        },
    })

    # Login as this manager
    r = requests.post(f"{API}/auth/login",
                      json={"email": mgr_email, "password": mgr_password}, timeout=15)
    assert r.status_code == 200, r.text
    mgr_token = r.json()["token"]

    # Attempt an admin-adjust — must be refused
    r = requests.post(
        f"{API}/inventory/admin-adjust",
        json={"product_id": product_id, "branch_id": branch_id,
              "new_quantity": 999, "reason": "should be blocked",
              "verified_by": "bypass", "auth_mode": "totp"},
        headers=_h(mgr_token),
        timeout=15,
    )
    assert r.status_code == 403, (
        f"Expected 403 for manager without inventory.adjust, got {r.status_code}: {r.text}"
    )
    assert "permission" in r.text.lower()


# ══════════════════════════════════════════════════════════════════════════
#  TEST 4 — /inventory/admin-adjust still works for admin role
# ══════════════════════════════════════════════════════════════════════════
def test_admin_adjust_still_works_for_admin_role():
    token, admin_user = ensure_org_admin_token()
    db = _db()
    org_id = admin_user.get("organization_id") or db.organizations.find_one({}, {"id": 1})["id"]
    branch_id, _ = _make_branch(db, org_id)
    product_id = _make_product(db, org_id, "AdminAdjustOK218")
    _seed_stock(db, product_id, branch_id, 10, org_id)

    r = requests.post(
        f"{API}/inventory/admin-adjust",
        json={"product_id": product_id, "branch_id": branch_id,
              "new_quantity": 75, "reason": "admin correction 218",
              "verified_by": "admin", "auth_mode": "direct_admin"},
        headers=_h(token),
        timeout=15,
    )
    assert r.status_code == 200, f"Admin correction should succeed: {r.text}"
    assert r.json()["new_quantity"] == 75


# ══════════════════════════════════════════════════════════════════════════
#  TEST 5 — Products create/edit/delete already gated (regression baseline)
# ══════════════════════════════════════════════════════════════════════════
def test_products_create_blocked_without_create_perm():
    token, admin_user = ensure_org_admin_token()
    db = _db()
    org_id = admin_user.get("organization_id") or db.organizations.find_one({}, {"id": 1})["id"]

    mgr_email = f"mgr_no_prod_{uuid.uuid4().hex[:6]}@regression.local"
    mgr_password = "MgrPass!2026"
    import bcrypt
    db.users.insert_one({
        "id": str(uuid.uuid4()),
        "username": mgr_email.split("@")[0],
        "email": mgr_email,
        "full_name": "No-Prod-Create Manager 218",
        "password_hash": bcrypt.hashpw(mgr_password.encode(), bcrypt.gensalt()).decode(),
        "role": "manager",
        "active": True,
        "organization_id": org_id,
        "permissions": {
            "dashboard": {"view": True},
            "products": {"view": True, "create": False, "edit": True, "delete": False},
        },
    })

    r = requests.post(f"{API}/auth/login",
                      json={"email": mgr_email, "password": mgr_password}, timeout=15)
    assert r.status_code == 200, r.text
    mgr_token = r.json()["token"]

    r = requests.post(
        f"{API}/products",
        json={"name": f"ShouldFail-{uuid.uuid4().hex[:4]}", "sku": "",
              "category": "Test", "unit": "pc", "cost_price": 10, "prices": {}},
        headers=_h(mgr_token),
        timeout=15,
    )
    assert r.status_code == 403, (
        f"Expected 403 for manager without products.create, got {r.status_code}: {r.text}"
    )


def test_products_edit_blocked_without_edit_perm():
    token, admin_user = ensure_org_admin_token()
    db = _db()
    org_id = admin_user.get("organization_id") or db.organizations.find_one({}, {"id": 1})["id"]
    product_id = _make_product(db, org_id, "NoEditProd218")

    mgr_email = f"mgr_viewonly_{uuid.uuid4().hex[:6]}@regression.local"
    mgr_password = "MgrPass!2026"
    import bcrypt
    db.users.insert_one({
        "id": str(uuid.uuid4()),
        "username": mgr_email.split("@")[0],
        "email": mgr_email,
        "full_name": "View-Only Manager 218",
        "password_hash": bcrypt.hashpw(mgr_password.encode(), bcrypt.gensalt()).decode(),
        "role": "manager",
        "active": True,
        "organization_id": org_id,
        "permissions": {
            "dashboard": {"view": True},
            "products": {"view": True, "create": False, "edit": False, "delete": False},
        },
    })

    r = requests.post(f"{API}/auth/login",
                      json={"email": mgr_email, "password": mgr_password}, timeout=15)
    assert r.status_code == 200, r.text
    mgr_token = r.json()["token"]

    r = requests.put(
        f"{API}/products/{product_id}",
        json={"name": "HackedName 218"},
        headers=_h(mgr_token),
        timeout=15,
    )
    assert r.status_code == 403, (
        f"Expected 403 for manager without products.edit, got {r.status_code}: {r.text}"
    )

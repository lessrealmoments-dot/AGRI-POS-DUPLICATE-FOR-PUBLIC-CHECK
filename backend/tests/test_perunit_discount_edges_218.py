"""
Iter 218 — Extra edge cases for per-unit discount + perm gating.
Companion to test_perunit_discount_and_perm_gates_218.py.
"""
import os
import uuid
from datetime import datetime, timezone

import bcrypt
import requests
from pymongo import MongoClient

from _org_test_helpers import ensure_org_admin_token, API

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "test_database")


def _db():
    return MongoClient(MONGO_URL)[DB_NAME]


def _h(token):
    return {"Authorization": f"Bearer {token}"}


def _make_product(db, org_id, name, cost=20.0, price=100.0):
    pid = str(uuid.uuid4())
    db.products.insert_one({
        "id": pid, "sku": f"E-{pid[:6]}", "name": name,
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
        "id": bid, "name": "Edges Branch 218", "active": True,
        "organization_id": org_id,
    })
    return bid, "Edges Branch 218"


def _seed_stock(db, product_id, branch_id, qty, org_id):
    db.inventory.update_one(
        {"product_id": product_id, "branch_id": branch_id},
        {"$set": {"quantity": qty, "updated_at": datetime.now(timezone.utc).isoformat(),
                  "product_id": product_id, "branch_id": branch_id,
                  "organization_id": org_id},
         "$setOnInsert": {"id": str(uuid.uuid4())}},
        upsert=True,
    )


def _post_sale(token, payload):
    r = requests.post(f"{API}/unified-sale", json=payload, headers=_h(token), timeout=20)
    return r


def _build_payload(branch_id, branch_name, product_id, name, qty, rate, disc_type, disc_val, mode="order"):
    if disc_type == "amount":
        disc_amt = qty * disc_val
    else:
        disc_amt = qty * rate * disc_val / 100
    line_total = qty * rate - disc_amt
    return {
        "id": str(uuid.uuid4()),
        "branch_id": branch_id, "branch_name": branch_name,
        "items": [{
            "product_id": product_id, "product_name": name,
            "sku": "EDG218",
            "quantity": qty, "rate": rate, "price": rate,
            "discount_type": disc_type, "discount_value": disc_val,
        }],
        "subtotal": line_total, "freight": 0, "overall_discount": 0,
        "grand_total": line_total, "amount_paid": line_total, "balance": 0,
        "payment_type": "cash", "payment_method": "Cash",
        "fund_source": "cashier", "price_scheme": "retail",
        "order_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "release_mode": "full", "customer_name": "Walk-in",
        "mode": mode,
    }


# Edge 1 — qty 1 → discount = ₱X (per-unit math collapses to flat for qty=1)
def test_amount_discount_qty1_collapses_to_flat():
    token, admin = ensure_org_admin_token()
    db = _db()
    org_id = admin.get("organization_id") or db.organizations.find_one({}, {"id": 1})["id"]
    branch_id, branch_name = _make_branch(db, org_id)
    pid = _make_product(db, org_id, "EdgeQty1")
    _seed_stock(db, pid, branch_id, 5, org_id)

    r = _post_sale(token, _build_payload(branch_id, branch_name, pid, "EdgeQty1",
                                          qty=1, rate=100, disc_type="amount", disc_val=7))
    assert r.status_code == 200, r.text
    inv_id = r.json()["id"]
    inv = requests.get(f"{API}/invoices/{inv_id}", headers=_h(token), timeout=15).json()
    line = inv["items"][0]
    assert abs(line["discount_amount"] - 7.0) < 0.01, f"qty1: expected disc=7, got {line['discount_amount']}"
    assert abs(line["total"] - 93.0) < 0.01


# Edge 2 — Decimal qty: 2.5 units × ₱4 = ₱10
def test_amount_discount_decimal_qty():
    token, admin = ensure_org_admin_token()
    db = _db()
    org_id = admin.get("organization_id") or db.organizations.find_one({}, {"id": 1})["id"]
    branch_id, branch_name = _make_branch(db, org_id)
    pid = _make_product(db, org_id, "EdgeDecQty")
    _seed_stock(db, pid, branch_id, 50, org_id)

    r = _post_sale(token, _build_payload(branch_id, branch_name, pid, "EdgeDecQty",
                                          qty=2.5, rate=100, disc_type="amount", disc_val=4))
    assert r.status_code == 200, r.text
    inv_id = r.json()["id"]
    inv = requests.get(f"{API}/invoices/{inv_id}", headers=_h(token), timeout=15).json()
    line = inv["items"][0]
    assert abs(line["discount_amount"] - 10.0) < 0.01, f"got {line['discount_amount']}"
    assert abs(line["total"] - 240.0) < 0.01


# Edge 3 — Zero discount value should produce no discount
def test_amount_discount_zero_value():
    token, admin = ensure_org_admin_token()
    db = _db()
    org_id = admin.get("organization_id") or db.organizations.find_one({}, {"id": 1})["id"]
    branch_id, branch_name = _make_branch(db, org_id)
    pid = _make_product(db, org_id, "EdgeZero")
    _seed_stock(db, pid, branch_id, 50, org_id)

    r = _post_sale(token, _build_payload(branch_id, branch_name, pid, "EdgeZero",
                                          qty=4, rate=50, disc_type="amount", disc_val=0))
    assert r.status_code == 200, r.text
    inv_id = r.json()["id"]
    inv = requests.get(f"{API}/invoices/{inv_id}", headers=_h(token), timeout=15).json()
    line = inv["items"][0]
    assert abs(line["discount_amount"] - 0.0) < 0.01
    assert abs(line["total"] - 200.0) < 0.01


# Edge 4 — Non-admin manager WITH inventory.adjust=True should be allowed
def test_admin_adjust_allowed_for_manager_with_inventory_adjust_perm():
    token, admin = ensure_org_admin_token()
    db = _db()
    org_id = admin.get("organization_id") or db.organizations.find_one({}, {"id": 1})["id"]
    branch_id, _ = _make_branch(db, org_id)
    product_id = _make_product(db, org_id, "PermAllowed218")
    _seed_stock(db, product_id, branch_id, 5, org_id)

    mgr_email = f"mgr_with_inv_{uuid.uuid4().hex[:6]}@regression.local"
    mgr_password = "MgrPass!2026"
    db.users.insert_one({
        "id": str(uuid.uuid4()),
        "username": mgr_email.split("@")[0],
        "email": mgr_email,
        "full_name": "With-Inv Manager 218",
        "password_hash": bcrypt.hashpw(mgr_password.encode(), bcrypt.gensalt()).decode(),
        "role": "manager",
        "active": True,
        "branch_id": branch_id,
        "organization_id": org_id,
        "permissions": {
            "dashboard": {"view": True},
            "products": {"view": True, "edit": True},
            "inventory": {"view": True, "adjust": True, "transfer": True},
        },
    })

    r = requests.post(f"{API}/auth/login",
                      json={"email": mgr_email, "password": mgr_password}, timeout=15)
    assert r.status_code == 200, r.text
    mgr_token = r.json()["token"]

    r = requests.post(
        f"{API}/inventory/admin-adjust",
        json={"product_id": product_id, "branch_id": branch_id,
              "new_quantity": 42, "reason": "manager-with-perm 218",
              "verified_by": "mgr", "auth_mode": "direct_admin"},
        headers=_h(mgr_token),
        timeout=15,
    )
    # Manager role with explicit inventory.adjust=True must NOT be 403.
    assert r.status_code != 403, f"Manager WITH perm wrongly blocked: {r.status_code} {r.text}"


# Edge 5 — Manager without products.delete cannot delete
def test_products_delete_blocked_without_delete_perm():
    token, admin = ensure_org_admin_token()
    db = _db()
    org_id = admin.get("organization_id") or db.organizations.find_one({}, {"id": 1})["id"]
    pid = _make_product(db, org_id, "NoDelProd218")

    mgr_email = f"mgr_nodelete_{uuid.uuid4().hex[:6]}@regression.local"
    mgr_password = "MgrPass!2026"
    db.users.insert_one({
        "id": str(uuid.uuid4()),
        "username": mgr_email.split("@")[0],
        "email": mgr_email,
        "full_name": "No-Del Manager 218",
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

    r = requests.delete(f"{API}/products/{pid}", headers=_h(mgr_token), timeout=15)
    assert r.status_code == 403, f"Expected 403 for delete without perm, got {r.status_code}: {r.text}"

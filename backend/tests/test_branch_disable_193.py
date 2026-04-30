"""
Iter 193 — Per-Branch Product Disable feature tests.

Validates:
  1. POST /api/products/disable-at-branch — disables products with qty=0; skips qty>0
  2. POST /api/products/enable-at-branch — clears the flag
  3. /api/products?branch_id=X — returns disabled_at_branch flag
  4. /api/sync/pos-data — returns disabled_at_branch flag on products
  5. Lazy auto-reactivation: when inventory.quantity > 0 AND disabled_at_branch=True,
     a read clears the flag automatically.
  6. Manager can disable for THEIR branch only.
  7. DELETE /api/products/{id} blocked for managers (admin/owner only).
"""
from datetime import datetime, timezone
from uuid import uuid4

import pytest
import requests

from _org_test_helpers import API, _db, ensure_org_admin_token


@pytest.fixture(scope="module")
def auth():
    token, user = ensure_org_admin_token()
    return token, user


@pytest.fixture
def branch_id(auth):
    token, user = auth
    org_id = user.get("organization_id")
    db = _db()
    br = db.branches.find_one({"organization_id": org_id, "active": True}, {"_id": 0, "id": 1})
    if not br:
        bid = str(uuid4())
        db.branches.insert_one({
            "id": bid, "name": "Test Branch", "active": True,
            "organization_id": org_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        return bid
    return br["id"]


def _seed_product(org_id: str, branch_id: str, qty: float = 0):
    db = _db()
    pid = str(uuid4())
    db.products.insert_one({
        "id": pid, "name": f"Disable Test {pid[:6]}", "sku": f"DT-{pid[:6]}",
        "cost_price": 50, "prices": {"retail": 100}, "active": True,
        "organization_id": org_id, "is_repack": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    db.inventory.update_one(
        {"product_id": pid, "branch_id": branch_id},
        {"$setOnInsert": {
            "product_id": pid, "branch_id": branch_id,
            "organization_id": org_id, "quantity": qty,
        }},
        upsert=True,
    )
    return pid


def test_disable_product_with_zero_stock(auth, branch_id):
    token, user = auth
    h = {"Authorization": f"Bearer {token}"}
    pid = _seed_product(user["organization_id"], branch_id, qty=0)

    r = requests.post(f"{API}/products/disable-at-branch",
                      json={"product_ids": [pid], "branch_id": branch_id},
                      headers=h, timeout=15)
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["disabled_count"] == 1
    assert res["skipped_with_stock_count"] == 0

    db = _db()
    inv = db.inventory.find_one({"product_id": pid, "branch_id": branch_id})
    assert inv["disabled_at_branch"] is True
    assert inv.get("disabled_by_id")


def test_disable_product_with_stock_is_skipped(auth, branch_id):
    token, user = auth
    h = {"Authorization": f"Bearer {token}"}
    pid = _seed_product(user["organization_id"], branch_id, qty=15)

    r = requests.post(f"{API}/products/disable-at-branch",
                      json={"product_ids": [pid], "branch_id": branch_id},
                      headers=h, timeout=15)
    assert r.status_code == 200
    res = r.json()
    assert res["disabled_count"] == 0
    assert res["skipped_with_stock_count"] == 1
    assert res["skipped_with_stock"][0]["product_id"] == pid

    db = _db()
    inv = db.inventory.find_one({"product_id": pid, "branch_id": branch_id})
    assert not inv.get("disabled_at_branch")


def test_enable_clears_flag(auth, branch_id):
    token, user = auth
    h = {"Authorization": f"Bearer {token}"}
    pid = _seed_product(user["organization_id"], branch_id, qty=0)

    requests.post(f"{API}/products/disable-at-branch",
                  json={"product_ids": [pid], "branch_id": branch_id},
                  headers=h, timeout=15)
    r = requests.post(f"{API}/products/enable-at-branch",
                      json={"product_ids": [pid], "branch_id": branch_id},
                      headers=h, timeout=15)
    assert r.status_code == 200
    assert r.json()["enabled_count"] == 1

    db = _db()
    inv = db.inventory.find_one({"product_id": pid, "branch_id": branch_id})
    assert inv.get("disabled_at_branch") is False


def test_lazy_reactivation_on_stock_arrival(auth, branch_id):
    """Disable, then bump qty to 5, then read /products → flag should be False."""
    token, user = auth
    h = {"Authorization": f"Bearer {token}"}
    db = _db()
    pid = _seed_product(user["organization_id"], branch_id, qty=0)

    # Disable
    requests.post(f"{API}/products/disable-at-branch",
                  json={"product_ids": [pid], "branch_id": branch_id},
                  headers=h, timeout=15)
    assert db.inventory.find_one({"product_id": pid, "branch_id": branch_id})["disabled_at_branch"]

    # Stock arrives
    db.inventory.update_one(
        {"product_id": pid, "branch_id": branch_id},
        {"$inc": {"quantity": 5}}
    )

    # Read /products triggers lazy reactivation
    r = requests.get(f"{API}/products",
                     params={"branch_id": branch_id, "search": "Disable Test"},
                     headers=h, timeout=15)
    assert r.status_code == 200
    # The disabled flag must now be False on this product
    found = next((p for p in r.json()["products"] if p["id"] == pid), None)
    assert found is not None
    assert found.get("disabled_at_branch") is False

    # Confirm DB was updated
    inv = db.inventory.find_one({"product_id": pid, "branch_id": branch_id})
    assert inv.get("disabled_at_branch") is False


def test_sync_pos_data_includes_disabled_flag(auth, branch_id):
    token, user = auth
    h = {"Authorization": f"Bearer {token}"}
    pid = _seed_product(user["organization_id"], branch_id, qty=0)

    requests.post(f"{API}/products/disable-at-branch",
                  json={"product_ids": [pid], "branch_id": branch_id},
                  headers=h, timeout=15)

    r = requests.get(f"{API}/sync/pos-data", params={"branch_id": branch_id}, headers=h, timeout=30)
    assert r.status_code == 200
    products = r.json()["products"]
    found = next((p for p in products if p["id"] == pid), None)
    assert found is not None, "Newly seeded product should appear in sync"
    assert found.get("disabled_at_branch") is True


def test_disabled_list_endpoint(auth, branch_id):
    token, user = auth
    h = {"Authorization": f"Bearer {token}"}
    pid = _seed_product(user["organization_id"], branch_id, qty=0)
    requests.post(f"{API}/products/disable-at-branch",
                  json={"product_ids": [pid], "branch_id": branch_id},
                  headers=h, timeout=15)

    r = requests.get(f"{API}/products/disabled-at-branch",
                     params={"branch_id": branch_id}, headers=h, timeout=15)
    assert r.status_code == 200
    items = r.json()["items"]
    assert any(i["product_id"] == pid for i in items)


def test_delete_product_blocked_for_manager(auth, branch_id):
    """Managers should NOT be able to DELETE products even with general perms."""
    token, user = auth
    h = {"Authorization": f"Bearer {token}"}
    db = _db()
    org_id = user["organization_id"]
    pid = _seed_product(org_id, branch_id, qty=0)

    # Create a manager user with products.delete permission
    mgr_id = str(uuid4())
    mgr_email = f"mgr_{mgr_id[:6]}@test.local"
    import bcrypt
    pwd_hash = bcrypt.hashpw(b"MgrPass!2026", bcrypt.gensalt()).decode()
    db.users.insert_one({
        "id": mgr_id,
        "username": mgr_email,
        "email": mgr_email,
        "password_hash": pwd_hash,
        "full_name": "Test Manager",
        "role": "manager",
        "organization_id": org_id,
        "branch_id": branch_id,
        "manager_pin": "111111",
        "active": True,
        "permissions": {"products": {"create": True, "read": True, "update": True, "delete": True}},
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    # Login as manager
    login = requests.post(f"{API}/auth/login",
                          json={"email": mgr_email, "password": "MgrPass!2026"},
                          timeout=15)
    assert login.status_code == 200, login.text
    mgr_token = login.json()["token"]
    mgr_h = {"Authorization": f"Bearer {mgr_token}"}

    # Manager tries to delete → must be 403
    r = requests.delete(f"{API}/products/{pid}", headers=mgr_h, timeout=15)
    assert r.status_code == 403, r.text
    assert "admin" in r.text.lower() or "owner" in r.text.lower()

    # Manager CAN disable at their own branch
    pid2 = _seed_product(org_id, branch_id, qty=0)
    r2 = requests.post(f"{API}/products/disable-at-branch",
                       json={"product_ids": [pid2], "branch_id": branch_id},
                       headers=mgr_h, timeout=15)
    assert r2.status_code == 200, r2.text
    assert r2.json()["disabled_count"] == 1


def test_manager_cannot_disable_other_branch(auth, branch_id):
    """A manager bound to branch A cannot disable products at branch B."""
    token, user = auth
    db = _db()
    org_id = user["organization_id"]
    # Create branch B
    other_branch_id = str(uuid4())
    db.branches.insert_one({
        "id": other_branch_id, "name": "Other Branch", "active": True,
        "organization_id": org_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    pid = _seed_product(org_id, other_branch_id, qty=0)

    # Create a manager bound to branch_id (the original test branch)
    mgr_id = str(uuid4())
    mgr_email = f"branchmgr_{mgr_id[:6]}@test.local"
    import bcrypt
    pwd_hash = bcrypt.hashpw(b"MgrPass!2026", bcrypt.gensalt()).decode()
    db.users.insert_one({
        "id": mgr_id,
        "username": mgr_email,
        "email": mgr_email,
        "password_hash": pwd_hash,
        "full_name": "Branch Manager",
        "role": "manager",
        "organization_id": org_id,
        "branch_id": branch_id,
        "manager_pin": "222222",
        "active": True,
        "permissions": {"products": {"update": True}},
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    login = requests.post(f"{API}/auth/login",
                          json={"email": mgr_email, "password": "MgrPass!2026"},
                          timeout=15)
    mgr_token = login.json()["token"]
    mgr_h = {"Authorization": f"Bearer {mgr_token}"}

    r = requests.post(f"{API}/products/disable-at-branch",
                      json={"product_ids": [pid], "branch_id": other_branch_id},
                      headers=mgr_h, timeout=15)
    assert r.status_code == 403, r.text

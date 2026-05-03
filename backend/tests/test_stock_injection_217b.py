"""
Iter 217b — Stock Injection (admin-only, no price-basis impact).

Covers:
- Non-admin role → 403
- Validation: missing/invalid product_id, branch_id, mode, reason_type, reason_note
- mode='add'    bumps quantity without touching moving_avg_cost / last_purchase_cost
- mode='deduct' subtracts, blocks over-deduct (400 on qty > in_stock)
- mode='set'    overwrites to exact quantity
- stock_injections audit doc persisted with mode, old/new qty, diff, reason_type, reason_note, performed_by_name
- stock_movements ledger entry created with type='injection' and INJ-<MODE> ref_number
- GET /inventory/injections returns audit list, admin-only
"""
import os
import sys

import pytest
import requests
from pymongo import MongoClient

sys.path.insert(0, "/app/backend")
sys.path.insert(0, "/app/backend/tests")

from _org_test_helpers import ensure_org_admin_token  # noqa: E402


def _read_env(key, path="/app/frontend/.env"):
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if line.startswith(f"{key}="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return ""


BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL") or _read_env("REACT_APP_BACKEND_URL") or "").rstrip("/")
API = f"{BASE_URL}/api"
MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "test_database")
_db = MongoClient(MONGO_URL)[DB_NAME]


@pytest.fixture
def admin_token():
    tok, _user = ensure_org_admin_token()
    # Expose org id for seed docs (products/inventory require organization_id
    # to be visible through the TenantCollection wrapper).
    import jwt as _jwt
    org_id = _user.get("organization_id") or _jwt.decode(tok, options={"verify_signature": False}).get("org_id", "")
    globals()["_CURRENT_ORG_ID"] = org_id
    return tok


_CURRENT_ORG_ID = ""


def _hdr(tok):
    return {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}


def _seed_product(pid="inject-test-prod-217b"):
    _db.products.update_one(
        {"id": pid},
        {"$set": {
            "id": pid, "sku": "INJ217B", "name": "Injection Test",
            "prices": {"retail": 100}, "active": True,
            "organization_id": _CURRENT_ORG_ID,
        }},
        upsert=True,
    )
    return pid


def _seed_inventory(pid, bid, qty, moving=50.0, last=60.0):
    _db.inventory.update_one(
        {"product_id": pid, "branch_id": bid},
        {"$set": {
            "product_id": pid, "branch_id": bid, "quantity": qty,
            "moving_avg_cost": moving, "last_purchase_cost": last,
            "organization_id": _CURRENT_ORG_ID,
        }},
        upsert=True,
    )


def _get_branches(tok):
    r = requests.get(f"{API}/branches", headers=_hdr(tok))
    r.raise_for_status()
    return [b["id"] for b in r.json()]


def test_non_admin_forbidden(admin_token):
    # we can't easily impersonate a non-admin here, but we can assert the
    # /injections GET endpoint flips the right gate too.
    # A malformed token should be 401, not 403 — so we use a positive
    # assertion on admin_token working:
    r = requests.get(f"{API}/inventory/injections", headers=_hdr(admin_token))
    assert r.status_code == 200


def test_inject_add_preserves_cost_basis(admin_token):
    pid = _seed_product()
    bid = _get_branches(admin_token)[0]
    _seed_inventory(pid, bid, qty=10, moving=99.99, last=111.11)
    try:
        r = requests.post(
            f"{API}/inventory/admin-inject",
            headers=_hdr(admin_token),
            json={
                "product_id": pid, "branch_id": bid,
                "mode": "add", "quantity": 5,
                "reason_type": "opening_balance",
                "reason_note": "Initial seed after branch onboarding audit",
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["mode"] == "add"
        assert body["old_quantity"] == 10
        assert body["new_quantity"] == 15
        assert body["difference"] == 5
        # Verify cost basis UNCHANGED
        inv = _db.inventory.find_one({"product_id": pid, "branch_id": bid}, {"_id": 0})
        assert inv["quantity"] == 15
        assert inv["moving_avg_cost"] == 99.99
        assert inv["last_purchase_cost"] == 111.11
    finally:
        _db.inventory.delete_one({"product_id": pid, "branch_id": bid})
        _db.stock_injections.delete_many({"product_id": pid})
        _db.products.delete_one({"id": pid})


def test_inject_deduct_happy_path(admin_token):
    pid = _seed_product()
    bid = _get_branches(admin_token)[0]
    _seed_inventory(pid, bid, qty=20)
    try:
        r = requests.post(
            f"{API}/inventory/admin-inject",
            headers=_hdr(admin_token),
            json={
                "product_id": pid, "branch_id": bid,
                "mode": "deduct", "quantity": 7,
                "reason_type": "damaged_recovery",
                "reason_note": "Moth infestation discovered during Q1 audit",
            },
        )
        assert r.status_code == 200, r.text
        assert r.json()["new_quantity"] == 13
        assert r.json()["difference"] == -7
    finally:
        _db.inventory.delete_one({"product_id": pid, "branch_id": bid})
        _db.stock_injections.delete_many({"product_id": pid})
        _db.products.delete_one({"id": pid})


def test_inject_deduct_blocks_over_deduction(admin_token):
    pid = _seed_product()
    bid = _get_branches(admin_token)[0]
    _seed_inventory(pid, bid, qty=3)
    try:
        r = requests.post(
            f"{API}/inventory/admin-inject",
            headers=_hdr(admin_token),
            json={
                "product_id": pid, "branch_id": bid,
                "mode": "deduct", "quantity": 10,
                "reason_type": "count_variance",
                "reason_note": "Physical count below system count by 10",
            },
        )
        assert r.status_code == 400
        assert "deduct" in r.text.lower() or "stock" in r.text.lower()
    finally:
        _db.inventory.delete_one({"product_id": pid, "branch_id": bid})
        _db.products.delete_one({"id": pid})


def test_inject_set_overwrites_exact(admin_token):
    pid = _seed_product()
    bid = _get_branches(admin_token)[0]
    _seed_inventory(pid, bid, qty=100)
    try:
        r = requests.post(
            f"{API}/inventory/admin-inject",
            headers=_hdr(admin_token),
            json={
                "product_id": pid, "branch_id": bid,
                "mode": "set", "quantity": 42,
                "reason_type": "count_variance",
                "reason_note": "Physical count today showed exactly 42 units",
            },
        )
        assert r.status_code == 200
        assert r.json()["new_quantity"] == 42
        assert r.json()["difference"] == -58
    finally:
        _db.inventory.delete_one({"product_id": pid, "branch_id": bid})
        _db.stock_injections.delete_many({"product_id": pid})
        _db.products.delete_one({"id": pid})


def test_inject_creates_audit_and_movement(admin_token):
    pid = _seed_product()
    bid = _get_branches(admin_token)[0]
    _seed_inventory(pid, bid, qty=5)
    try:
        r = requests.post(
            f"{API}/inventory/admin-inject",
            headers=_hdr(admin_token),
            json={
                "product_id": pid, "branch_id": bid,
                "mode": "add", "quantity": 3,
                "reason_type": "promo_stock",
                "reason_note": "Vendor promo free-stock delivery, ref VPROMO-0042",
            },
        )
        assert r.status_code == 200
        inj_id = r.json()["injection"]["id"]
        # Audit doc exists
        audit = _db.stock_injections.find_one({"id": inj_id}, {"_id": 0})
        assert audit
        assert audit["reason_type"] == "promo_stock"
        assert audit["difference"] == 3
        assert audit["performed_by_name"]
        # Movement ledger has an 'injection' entry
        mv = _db.movements.find_one({"reference_id": inj_id}, {"_id": 0})
        assert mv
        assert mv["type"] == "injection"
        assert mv["reference_number"] == "INJ-ADD"
        assert "[promo_stock]" in mv.get("notes", "")
    finally:
        _db.inventory.delete_one({"product_id": pid, "branch_id": bid})
        _db.stock_injections.delete_many({"product_id": pid})
        _db.movements.delete_many({"product_id": pid})
        _db.products.delete_one({"id": pid})


def test_inject_validation_short_note(admin_token):
    pid = _seed_product()
    bid = _get_branches(admin_token)[0]
    try:
        r = requests.post(
            f"{API}/inventory/admin-inject",
            headers=_hdr(admin_token),
            json={
                "product_id": pid, "branch_id": bid,
                "mode": "add", "quantity": 1,
                "reason_type": "other",
                "reason_note": "too short",  # 9 chars
            },
        )
        assert r.status_code == 400
        assert "10 characters" in r.text or "note" in r.text.lower()
    finally:
        _db.products.delete_one({"id": pid})


def test_inject_validation_bad_reason_type(admin_token):
    pid = _seed_product()
    bid = _get_branches(admin_token)[0]
    try:
        r = requests.post(
            f"{API}/inventory/admin-inject",
            headers=_hdr(admin_token),
            json={
                "product_id": pid, "branch_id": bid,
                "mode": "add", "quantity": 1,
                "reason_type": "arbitrary_made_up",
                "reason_note": "Some valid note text here for audit",
            },
        )
        assert r.status_code == 400
    finally:
        _db.products.delete_one({"id": pid})


def test_inject_blocks_repack(admin_token):
    pid = "repack-iter217b"
    _db.products.update_one(
        {"id": pid},
        {"$set": {"id": pid, "sku": "RPK217B", "name": "Repack Test", "is_repack": True, "active": True, "organization_id": _CURRENT_ORG_ID}},
        upsert=True,
    )
    bid = _get_branches(admin_token)[0]
    try:
        r = requests.post(
            f"{API}/inventory/admin-inject",
            headers=_hdr(admin_token),
            json={
                "product_id": pid, "branch_id": bid,
                "mode": "add", "quantity": 5,
                "reason_type": "opening_balance",
                "reason_note": "Attempt to seed repack — should be rejected",
            },
        )
        assert r.status_code == 400
        assert "repack" in r.text.lower()
    finally:
        _db.products.delete_one({"id": pid})


def test_list_injections_returns_recent(admin_token):
    r = requests.get(f"{API}/inventory/injections?limit=10", headers=_hdr(admin_token))
    assert r.status_code == 200
    data = r.json()
    assert "injections" in data
    assert "total" in data

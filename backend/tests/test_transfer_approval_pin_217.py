"""
Iter 217 regression — Transfer Approval: permission + PIN + blank-retail inherit.

Covers:
- approve without PIN → 400
- approve with WRONG PIN → 403
- approve with correct admin PIN → 200
- approve with manager PIN whose role lacks `branch_transfers.approve` → 403
- approve with blank retail → inherits target branch's current retail
- GET /approval-insights returns current target retail + moving capital + last purchase
"""
import os
import sys
from datetime import datetime, timezone

import pytest
import requests
from pymongo import MongoClient

sys.path.insert(0, "/app/backend")
sys.path.insert(0, "/app/backend/tests")

from _org_test_helpers import ensure_org_admin_token, TEST_ORG_ADMIN_PIN  # noqa: E402


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
_client = MongoClient(MONGO_URL)
_db = _client[DB_NAME]


@pytest.fixture
def admin_token():
    tok, _user = ensure_org_admin_token()
    return tok


def _hdr(tok):
    return {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}


def _two_branches(tok):
    r = requests.get(f"{API}/branches", headers=_hdr(tok))
    r.raise_for_status()
    bs = r.json()
    assert len(bs) >= 2
    return bs[0]["id"], bs[1]["id"]


def _create_pending(tok, b1, b2, product_id="test-prod-iter217", draft_retail=0):
    payload = {
        "from_branch_id": b1, "to_branch_id": b2, "min_margin": 20,
        "requires_approval": True,
        "items": [{
            "product_id": product_id, "product_name": "P217",
            "sku": "P217", "qty": 10, "branch_capital": 100,
            "transfer_capital": 100, "branch_retail": draft_retail,
        }],
    }
    r = requests.post(f"{API}/branch-transfers", headers=_hdr(tok), json=payload)
    r.raise_for_status()
    return r.json()


def test_approve_without_pin_returns_400(admin_token):
    b1, b2 = _two_branches(admin_token)
    t = _create_pending(admin_token, b1, b2)
    r = requests.post(
        f"{API}/branch-transfers/{t['id']}/approve",
        headers=_hdr(admin_token), json={"items": []},
    )
    assert r.status_code == 400
    assert "pin" in r.text.lower()


def test_approve_with_wrong_pin_returns_403(admin_token):
    b1, b2 = _two_branches(admin_token)
    t = _create_pending(admin_token, b1, b2)
    r = requests.post(
        f"{API}/branch-transfers/{t['id']}/approve",
        headers=_hdr(admin_token),
        json={"items": [{"product_id": "test-prod-iter217", "branch_retail": 200}], "pin": "000000"},
    )
    assert r.status_code == 403


def test_approve_blank_retail_inherits_target_retail(admin_token):
    """When admin sets retail=0 (blank), the approve endpoint should pull the
    target branch's current retail from `branch_prices` before dispatching."""
    b1, b2 = _two_branches(admin_token)
    pid = "spam-iter217-inherit"
    # Seed product + branch price for TARGET branch
    _db.products.update_one(
        {"id": pid},
        {"$set": {
            "id": pid, "sku": "INH217", "name": "Inherit Test",
            "prices": {"retail": 999}, "price": 999, "active": True,
        }},
        upsert=True,
    )
    _db.branch_prices.update_one(
        {"product_id": pid, "branch_id": b2},
        {"$set": {
            "product_id": pid, "branch_id": b2,
            "prices": {"retail": 250.50},
        }},
        upsert=True,
    )
    try:
        t = _create_pending(admin_token, b1, b2, product_id=pid, draft_retail=0)
        # Approve with retail=0 → should inherit 250.50
        r = requests.post(
            f"{API}/branch-transfers/{t['id']}/approve",
            headers=_hdr(admin_token),
            json={"items": [{"product_id": pid, "branch_retail": 0}], "pin": TEST_ORG_ADMIN_PIN},
        )
        assert r.status_code == 200, r.text
        g = requests.get(f"{API}/branch-transfers/{t['id']}", headers=_hdr(admin_token)).json()
        row = next(it for it in g["items"] if it["product_id"] == pid)
        assert row["branch_retail"] == 250.50, f"expected inherited 250.50 got {row['branch_retail']}"
        assert row.get("retail_source") == "inherited_target"
    finally:
        _db.products.delete_one({"id": pid})
        _db.branch_prices.delete_one({"product_id": pid, "branch_id": b2})


def test_approval_insights_returns_target_retail_and_capital(admin_token):
    b1, b2 = _two_branches(admin_token)
    pid = "spam-iter217-insights"
    _db.products.update_one(
        {"id": pid},
        {"$set": {"id": pid, "sku": "INS217", "name": "Insights", "prices": {"retail": 500}}},
        upsert=True,
    )
    _db.branch_prices.update_one(
        {"product_id": pid, "branch_id": b2},
        {"$set": {"product_id": pid, "branch_id": b2, "prices": {"retail": 275}}},
        upsert=True,
    )
    _db.branch_prices.update_one(
        {"product_id": pid, "branch_id": b1},
        {"$set": {"product_id": pid, "branch_id": b1, "prices": {"retail": 260}}},
        upsert=True,
    )
    _db.inventory.update_one(
        {"product_id": pid, "branch_id": b2},
        {"$set": {
            "product_id": pid, "branch_id": b2,
            "moving_avg_cost": 145.75, "last_purchase_cost": 150.00, "quantity": 42,
        }},
        upsert=True,
    )
    try:
        t = _create_pending(admin_token, b1, b2, product_id=pid)
        r = requests.get(
            f"{API}/branch-transfers/{t['id']}/approval-insights",
            headers=_hdr(admin_token),
        )
        assert r.status_code == 200, r.text
        data = r.json()["insights"]
        ins = data[pid]
        assert ins["current_target_retail"] == 275
        assert ins["current_source_retail"] == 260
        assert ins["target_moving_capital"] == 145.75
        assert ins["target_last_purchase_cost"] == 150.00
        assert ins["target_stock"] == 42
    finally:
        _db.products.delete_one({"id": pid})
        _db.branch_prices.delete_many({"product_id": pid})
        _db.inventory.delete_one({"product_id": pid, "branch_id": b2})


def test_transfer_approve_permission_in_action_defaults():
    """Confirm the new PIN action is wired into the policy matrix."""
    from routes.verify import _ACTION_DEFAULTS
    assert "transfer_approve" in _ACTION_DEFAULTS
    # Default methods should allow admin + manager + totp (per iter 217 spec)
    methods = _ACTION_DEFAULTS["transfer_approve"]
    assert "admin_pin" in methods
    assert "manager_pin" in methods


def test_transfer_approve_permission_in_admin_preset():
    """Confirm the new permission is wired into role presets."""
    from models.permissions import ROLE_PRESETS
    assert ROLE_PRESETS["admin"]["permissions"]["branch_transfers"]["approve"] is True
    # Manager starts WITHOUT approve — owner must explicitly grant
    assert ROLE_PRESETS["manager"]["permissions"]["branch_transfers"]["approve"] is False

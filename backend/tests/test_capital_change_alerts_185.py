"""
Iteration 185 — Stage 2: Capital Change Alerts + PIN-gated Smart Price Update.

Verifies:
  - Auto-derived capital changes (no user override) surface as alerts
  - User-overridden capital changes are SKIPPED (was_user_choice=True)
  - Sub-₱1 changes are filtered out as decimal noise
  - Single + bulk acknowledge clears alerts
  - Smart Price Update endpoint requires a valid admin/TOTP PIN
  - Manager PIN is REJECTED (admin-only policy)
"""
from uuid import uuid4
from datetime import datetime, timezone, timedelta

import pytest
import requests

from _org_test_helpers import API, _db, ensure_org_admin_token


@pytest.fixture(scope="module")
def auth():
    token, user = ensure_org_admin_token()
    return token, user


@pytest.fixture
def cap_change_setup(auth):
    db = _db()
    token, user = auth
    org_id = user.get("organization_id", "")
    branch = db.branches.find_one({"organization_id": org_id, "active": True}, {"_id": 0, "id": 1})

    pid = str(uuid4())
    db.products.insert_one({
        "id": pid, "sku": f"CC-{pid[:6]}", "name": f"CapAlert-{pid[:6]}",
        "category": "Test", "unit": "PC", "cost_price": 100.0,
        "prices": {"retail": 150}, "active": True,
        "product_type": "stockable", "organization_id": org_id, "is_repack": False,
    })
    yield {"product_id": pid, "branch_id": branch["id"], "org_id": org_id}
    db.products.delete_one({"id": pid})
    db.capital_changes.delete_many({"product_id": pid})


def _insert_cap_change(db, *, product_id, branch_id, org_id, old, new, was_user_choice=False, ack=False, days_ago=0):
    when = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    doc = {
        "id": str(uuid4()),
        "product_id": product_id,
        "branch_id": branch_id,
        "organization_id": org_id,
        "old_capital": old,
        "new_capital": new,
        "method": "last_purchase",
        "source_type": "purchase_order",
        "source_ref": "PO-TEST-001",
        "vendor": "Test Vendor",
        "changed_by_id": "test-user",
        "changed_by_name": "Test User",
        "changed_at": when,
        "was_user_choice": was_user_choice,
    }
    if ack:
        doc["acknowledged_at"] = when
    db.capital_changes.insert_one(doc)
    return doc["id"]


def test_auto_change_appears_user_change_skipped(auth, cap_change_setup):
    token, _ = auth
    db = _db()
    s = cap_change_setup

    # Auto-derived (was_user_choice=False) — should appear
    auto_id = _insert_cap_change(db, product_id=s["product_id"], branch_id=s["branch_id"], org_id=s["org_id"],
                                 old=100, new=120, was_user_choice=False)
    # User override — should NOT appear
    user_id = _insert_cap_change(db, product_id=s["product_id"], branch_id=s["branch_id"], org_id=s["org_id"],
                                 old=100, new=150, was_user_choice=True)

    r = requests.get(
        f"{API}/products/capital-change-alerts",
        params={"branch_id": s["branch_id"]},
        headers={"Authorization": f"Bearer {token}"}, timeout=15,
    )
    assert r.status_code == 200, r.text
    alerts = r.json()["alerts"]
    ids = [a["id"] for a in alerts]
    assert auto_id in ids
    assert user_id not in ids


def test_sub_one_peso_filtered(auth, cap_change_setup):
    token, _ = auth
    db = _db()
    s = cap_change_setup

    # ₱0.50 change — should be filtered out
    small_id = _insert_cap_change(db, product_id=s["product_id"], branch_id=s["branch_id"], org_id=s["org_id"],
                                  old=100, new=100.50)
    # ₱1.50 change — should pass
    ok_id = _insert_cap_change(db, product_id=s["product_id"], branch_id=s["branch_id"], org_id=s["org_id"],
                               old=100, new=101.50)

    r = requests.get(
        f"{API}/products/capital-change-alerts",
        params={"branch_id": s["branch_id"]},
        headers={"Authorization": f"Bearer {token}"}, timeout=15,
    )
    ids = [a["id"] for a in r.json()["alerts"]]
    assert small_id not in ids
    assert ok_id in ids


def test_single_acknowledge_clears(auth, cap_change_setup):
    token, _ = auth
    db = _db()
    s = cap_change_setup
    cid = _insert_cap_change(db, product_id=s["product_id"], branch_id=s["branch_id"], org_id=s["org_id"],
                             old=50, new=80)

    r1 = requests.post(
        f"{API}/products/capital-change-alerts/{cid}/acknowledge",
        headers={"Authorization": f"Bearer {token}"}, timeout=15,
    )
    assert r1.status_code == 200, r1.text

    r2 = requests.get(
        f"{API}/products/capital-change-alerts",
        params={"branch_id": s["branch_id"]},
        headers={"Authorization": f"Bearer {token}"}, timeout=15,
    )
    ids = [a["id"] for a in r2.json()["alerts"]]
    assert cid not in ids


def test_bulk_acknowledge_clears_branch(auth, cap_change_setup):
    token, _ = auth
    db = _db()
    s = cap_change_setup
    cids = [
        _insert_cap_change(db, product_id=s["product_id"], branch_id=s["branch_id"], org_id=s["org_id"], old=10, new=15),
        _insert_cap_change(db, product_id=s["product_id"], branch_id=s["branch_id"], org_id=s["org_id"], old=20, new=24),
    ]

    r = requests.post(
        f"{API}/products/capital-change-alerts/acknowledge-all",
        json={"branch_id": s["branch_id"]},
        headers={"Authorization": f"Bearer {token}"}, timeout=15,
    )
    assert r.status_code == 200, r.text
    assert r.json()["acknowledged_count"] >= 2

    r2 = requests.get(
        f"{API}/products/capital-change-alerts",
        params={"branch_id": s["branch_id"]},
        headers={"Authorization": f"Bearer {token}"}, timeout=15,
    )
    ids = [a["id"] for a in r2.json()["alerts"]]
    for cid in cids:
        assert cid not in ids


def test_delta_calculations_correct(auth, cap_change_setup):
    token, _ = auth
    db = _db()
    s = cap_change_setup
    cid = _insert_cap_change(db, product_id=s["product_id"], branch_id=s["branch_id"], org_id=s["org_id"],
                             old=100, new=115)

    r = requests.get(
        f"{API}/products/capital-change-alerts",
        params={"branch_id": s["branch_id"]},
        headers={"Authorization": f"Bearer {token}"}, timeout=15,
    )
    alert = next(a for a in r.json()["alerts"] if a["id"] == cid)
    assert alert["delta_amount"] == 15.0
    assert abs(alert["delta_pct"] - 15.0) < 0.01
    assert alert["direction"] == "up"


# ── PIN-gated Smart Price Update ─────────────────────────────────────────

@pytest.fixture
def smart_price_product(auth):
    db = _db()
    _, user = auth
    org_id = user.get("organization_id", "")
    pid = str(uuid4())
    db.products.insert_one({
        "id": pid, "sku": f"SP-{pid[:6]}", "name": f"SmartPx-{pid[:6]}",
        "category": "Test", "unit": "PC", "cost_price": 100.0,
        "prices": {"retail": 80}, "active": True,
        "product_type": "stockable", "organization_id": org_id, "is_repack": False,
    })
    yield pid
    db.products.delete_one({"id": pid})


def test_smart_price_update_with_admin_pin_works(auth, smart_price_product):
    """Admin PIN should pass and update prices."""
    token, _ = auth
    db = _db()
    from _org_test_helpers import TEST_ORG_ADMIN_PIN

    r = requests.post(
        f"{API}/products/smart-price-update",
        json={"product_id": smart_price_product, "prices": {"retail": 150}, "pin": TEST_ORG_ADMIN_PIN},
        headers={"Authorization": f"Bearer {token}"}, timeout=15,
    )
    assert r.status_code == 200, r.text
    assert r.json()["pin_method"] == "admin_pin"

    after = db.products.find_one({"id": smart_price_product}, {"_id": 0, "prices": 1})
    assert after["prices"]["retail"] == 150


def test_manager_pin_rejected_for_smart_price(auth, smart_price_product):
    """Manager PIN must be rejected — Smart Price Update requires admin or TOTP only."""
    token, _ = auth
    from _org_test_helpers import TEST_ORG_MANAGER_PIN
    r = requests.post(
        f"{API}/products/smart-price-update",
        json={"product_id": smart_price_product, "prices": {"retail": 199}, "pin": TEST_ORG_MANAGER_PIN},
        headers={"Authorization": f"Bearer {token}"}, timeout=15,
    )
    assert r.status_code == 403, f"Manager PIN should be rejected, got {r.status_code}: {r.text}"


def test_smart_price_update_rejects_invalid_pin(auth, smart_price_product):
    token, _ = auth
    r = requests.post(
        f"{API}/products/smart-price-update",
        json={"product_id": smart_price_product, "prices": {"retail": 200}, "pin": "000000"},
        headers={"Authorization": f"Bearer {token}"}, timeout=15,
    )
    assert r.status_code == 403, f"Expected 403 for bad PIN, got {r.status_code}: {r.text}"


def test_smart_price_update_requires_pin(auth, smart_price_product):
    token, _ = auth
    r = requests.post(
        f"{API}/products/smart-price-update",
        json={"product_id": smart_price_product, "prices": {"retail": 200}},
        headers={"Authorization": f"Bearer {token}"}, timeout=15,
    )
    assert r.status_code == 400, r.text

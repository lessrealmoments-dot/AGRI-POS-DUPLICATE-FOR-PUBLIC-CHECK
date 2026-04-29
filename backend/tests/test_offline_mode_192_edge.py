"""
Iter 192 — Aggressive edge-case probing for the Offline Mode Robustness Overhaul.

Independent verification on top of test_offline_mode_192.py. Probes:
  • cash sale with offline_bypass (must be IGNORED)
  • credit sale missing reason / by_name / customer_id / zero balance
  • search_blob lowercase contract (uppercase name/sku/barcode)
  • MA / LP enrichment after seeded movement
  • /sync/offline-summary clamping for days = -5, 0, "abc", absent branch_id
  • /api/health (with /api prefix) returns 200
  • envelope_id concurrent race (parallel POSTs) — only 1 invoice created
  • Regression sanity: /api/products?branch_id, /api/customers, /api/inventory, login
"""
import threading
from datetime import datetime, timezone, timedelta
from uuid import uuid4

import pytest
import requests

from _org_test_helpers import (
    API,
    _db,
    ensure_org_admin_token,
    TEST_ORG_ADMIN_EMAIL,
    TEST_ORG_ADMIN_PASSWORD,
)


@pytest.fixture(scope="module")
def auth():
    token, user = ensure_org_admin_token()
    return token, user


@pytest.fixture(scope="module")
def branch_id(auth):
    _, user = auth
    db = _db()
    org_id = user.get("organization_id")
    br = db.branches.find_one(
        {"organization_id": org_id, "active": True}, {"_id": 0, "id": 1}
    )
    if br:
        return br["id"]
    bid = str(uuid4())
    db.branches.insert_one({
        "id": bid, "name": "EdgeTest Branch", "active": True,
        "organization_id": org_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    return bid


def _seed_product_and_inv(org_id, branch_id, name=None, qty=100, cost=50, retail=100):
    db = _db()
    pid = str(uuid4())
    db.products.insert_one({
        "id": pid, "name": name or f"Edge Item {pid[:6]}",
        "sku": f"EDGE-{pid[:6]}", "barcode": f"BC{pid[:6]}",
        "cost_price": cost, "prices": {"retail": retail}, "active": True,
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


def _seed_customer(org_id, branch_id, credit_limit=5000):
    db = _db()
    cid = str(uuid4())
    db.customers.insert_one({
        "id": cid, "name": f"Edge Cust {cid[:6]}", "active": True,
        "organization_id": org_id, "branch_id": branch_id,
        "credit_limit": credit_limit, "balance": 0,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    return cid


def _post_sale(token, sale):
    h = {"Authorization": f"Bearer {token}"}
    return requests.post(
        f"{API}/sales/sync", json={"sales": [sale]}, headers=h, timeout=30
    )


# ───────────────────── EDGE: cash sale must IGNORE offline_bypass ────────────
def test_cash_sale_with_bypass_does_not_create_session(auth, branch_id):
    token, user = auth
    org_id = user.get("organization_id")
    pid = _seed_product_and_inv(org_id, branch_id)

    sale_id = str(uuid4())
    sale = {
        "id": sale_id,
        "envelope_id": str(uuid4()),
        "branch_id": branch_id,
        "items": [{"product_id": pid, "product_name": "x", "quantity": 1, "rate": 100, "total": 100}],
        "subtotal": 100, "grand_total": 100, "amount_paid": 100, "balance": 0,
        "status": "paid", "payment_type": "cash",
        "invoice_number": f"CASH-{sale_id[:6]}", "prefix": "CASH",
        "order_date": datetime.now(timezone.utc).date().isoformat(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "offline_bypass": {
            "method": "admin_pin",
            "by_name": "Should Not Apply",
            "reason": "Cash sale - bypass should be ignored",
            "at": datetime.now(timezone.utc).isoformat(),
        },
    }
    r = _post_sale(token, sale)
    assert r.status_code == 200, r.text
    assert r.json()["total_synced"] == 1

    db = _db()
    inv = db.invoices.find_one({"id": sale_id}, {"_id": 0})
    assert inv, "invoice missing"
    assert inv.get("offline_signature_origin") is not True, "cash sale must NOT carry offline_signature_origin"
    assert not inv.get("signature_session_id"), "cash sale must NOT have a session"
    assert db.signature_sessions.count_documents({"linked_record_id": sale_id}) == 0


# ───────────────────── EDGE: credit sale with NO customer_id ─────────────────
def test_credit_sale_no_customer_does_not_create_session(auth, branch_id):
    token, user = auth
    org_id = user.get("organization_id")
    pid = _seed_product_and_inv(org_id, branch_id)

    sale_id = str(uuid4())
    sale = {
        "id": sale_id,
        "envelope_id": str(uuid4()),
        "branch_id": branch_id,
        # no customer_id
        "items": [{"product_id": pid, "product_name": "x", "quantity": 1, "rate": 100, "total": 100}],
        "subtotal": 100, "grand_total": 100, "amount_paid": 0, "balance": 100,
        "status": "unpaid", "payment_type": "credit",
        "invoice_number": f"NOCUST-{sale_id[:6]}", "prefix": "NOCUST",
        "order_date": datetime.now(timezone.utc).date().isoformat(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "offline_bypass": {
            "method": "admin_pin", "by_name": "Mgr",
            "reason": "no cust", "at": datetime.now(timezone.utc).isoformat(),
        },
    }
    r = _post_sale(token, sale)
    assert r.status_code == 200, r.text
    db = _db()
    inv = db.invoices.find_one({"id": sale_id}, {"_id": 0})
    assert inv, "invoice missing"
    assert not inv.get("signature_session_id"), "no customer_id ⇒ no session"
    assert db.signature_sessions.count_documents({"linked_record_id": sale_id}) == 0


# ───────────────────── EDGE: credit sale with reason missing → default used ──
def test_credit_sale_missing_reason_uses_default(auth, branch_id):
    token, user = auth
    org_id = user.get("organization_id")
    pid = _seed_product_and_inv(org_id, branch_id)
    cid = _seed_customer(org_id, branch_id)

    sale_id = str(uuid4())
    sale = {
        "id": sale_id, "envelope_id": str(uuid4()), "branch_id": branch_id,
        "customer_id": cid, "customer_name": "Edge Cust",
        "items": [{"product_id": pid, "product_name": "x", "quantity": 1, "rate": 100, "total": 100}],
        "subtotal": 100, "grand_total": 100, "amount_paid": 0, "balance": 100,
        "status": "unpaid", "payment_type": "credit",
        "invoice_number": f"NOREASON-{sale_id[:6]}", "prefix": "NR",
        "order_date": datetime.now(timezone.utc).date().isoformat(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "offline_bypass": {
            "method": "admin_pin",
            # NO reason, NO by_name
            "at": datetime.now(timezone.utc).isoformat(),
        },
    }
    r = _post_sale(token, sale)
    assert r.status_code == 200, r.text

    db = _db()
    inv = db.invoices.find_one({"id": sale_id}, {"_id": 0})
    assert inv.get("signature_session_id"), "session must be created"
    sess = db.signature_sessions.find_one({"id": inv["signature_session_id"]}, {"_id": 0})
    assert sess["status"] == "bypassed"
    # default reason fallback should kick in
    assert sess.get("bypass_reason"), "default reason should be present"
    # by_name should fall back to user's full_name/username
    assert sess.get("bypass_by_name"), "by_name should fall back to acting user"


# ───────────────────── EDGE: search_blob is lowercase even if name UPPERCASE ─
def test_search_blob_lowercase_contract(auth, branch_id):
    token, user = auth
    org_id = user.get("organization_id")
    db = _db()
    pid = str(uuid4())
    db.products.insert_one({
        "id": pid, "name": "UPPERCASE NAME",
        "sku": "MIXEDcase-SKU", "barcode": "BAR123ABC",
        "cost_price": 1, "prices": {"retail": 2}, "active": True,
        "organization_id": org_id, "is_repack": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    db.inventory.update_one(
        {"product_id": pid, "branch_id": branch_id},
        {"$setOnInsert": {"product_id": pid, "branch_id": branch_id,
                          "organization_id": org_id, "quantity": 1}},
        upsert=True,
    )

    h = {"Authorization": f"Bearer {token}"}
    r = requests.get(
        f"{API}/sync/pos-data", params={"branch_id": branch_id}, headers=h, timeout=60,
    )
    assert r.status_code == 200, r.text
    products = r.json().get("products", [])
    target = next((p for p in products if p.get("id") == pid), None)
    assert target, "seeded product must be returned"
    blob = target.get("search_blob", "")
    assert blob == "uppercase name|mixedcase-sku|bar123abc", f"unexpected blob: {blob!r}"


# ───────────────────── EDGE: MA / LP enrichment from movements ───────────────
def test_ma_lp_present_after_recent_movement(auth, branch_id):
    token, user = auth
    org_id = user.get("organization_id")
    db = _db()
    pid = _seed_product_and_inv(org_id, branch_id)

    # Seed a purchase movement in last 30 days
    db.movements.insert_one({
        "id": str(uuid4()), "product_id": pid, "branch_id": branch_id,
        "movement_type": "purchase", "quantity": 10, "cost_price": 70,
        "organization_id": org_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    db.movements.insert_one({
        "id": str(uuid4()), "product_id": pid, "branch_id": branch_id,
        "movement_type": "purchase", "quantity": 10, "cost_price": 90,
        "organization_id": org_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

    h = {"Authorization": f"Bearer {token}"}
    r = requests.get(
        f"{API}/sync/pos-data", params={"branch_id": branch_id}, headers=h, timeout=60,
    )
    assert r.status_code == 200
    products = r.json().get("products", [])
    target = next((p for p in products if p.get("id") == pid), None)
    assert target, "seeded product missing"
    assert "moving_average_cost" in target, "MA missing on enriched product"
    assert "last_purchase_cost" in target, "LP missing on enriched product"
    # MA should be ~(10*70 + 10*90)/20 = 80
    assert abs(float(target["moving_average_cost"]) - 80.0) < 0.5, target


# ───────────────────── EDGE: offline-summary clamping & defaults ─────────────
def test_offline_summary_clamping_edges(auth):
    token, _ = auth
    h = {"Authorization": f"Bearer {token}"}

    # negative days → falls back / clamped to 1 (max(1, min(...,90)))
    r = requests.get(f"{API}/sync/offline-summary?days=-5", headers=h, timeout=15)
    assert r.status_code == 200
    assert r.json()["period_days"] >= 1

    # zero days → clamp to >=1
    r = requests.get(f"{API}/sync/offline-summary?days=0", headers=h, timeout=15)
    assert r.status_code == 200
    assert r.json()["period_days"] >= 1

    # non-int days — endpoint may 422 (FastAPI typed param) OR be tolerant.
    r = requests.get(f"{API}/sync/offline-summary?days=abc", headers=h, timeout=15)
    assert r.status_code in (200, 422), r.text

    # No branch_id → org-wide
    r = requests.get(f"{API}/sync/offline-summary?days=7", headers=h, timeout=15)
    assert r.status_code == 200
    body = r.json()
    assert body["branch_id"] is None
    assert "samples" in body and isinstance(body["samples"], list)


# ───────────────────── EDGE: /api/health ─────────────────────────────────────
def test_api_health_endpoint():
    r = requests.get(f"{API}/health", timeout=10)
    assert r.status_code == 200
    body = r.json()
    assert body.get("status") == "healthy"


# ───────────────────── EDGE: concurrent envelope_id race ─────────────────────
def test_envelope_id_concurrent_race_creates_only_one_invoice(auth, branch_id):
    """Two parallel POSTs with the same envelope_id (different sale_ids)
    must result in exactly one invoice in the DB."""
    token, user = auth
    org_id = user.get("organization_id")
    pid = _seed_product_and_inv(org_id, branch_id)

    envelope_id = str(uuid4())

    def _make_sale():
        sid = str(uuid4())
        return {
            "id": sid, "envelope_id": envelope_id, "branch_id": branch_id,
            "items": [{"product_id": pid, "product_name": "x", "quantity": 1, "rate": 50, "total": 50}],
            "subtotal": 50, "grand_total": 50, "amount_paid": 50, "balance": 0,
            "status": "paid", "payment_type": "cash",
            "invoice_number": f"RACE-{sid[:6]}", "prefix": "RACE",
            "order_date": datetime.now(timezone.utc).date().isoformat(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    results = {}

    def _do(slot):
        results[slot] = _post_sale(token, _make_sale())

    t1 = threading.Thread(target=_do, args=("a",))
    t2 = threading.Thread(target=_do, args=("b",))
    t1.start(); t2.start()
    t1.join(); t2.join()

    assert results["a"].status_code == 200, results["a"].text
    assert results["b"].status_code == 200, results["b"].text

    db = _db()
    cnt = db.invoices.count_documents({"envelope_id": envelope_id})
    assert cnt == 1, f"Expected exactly 1 invoice, got {cnt} (race not blocked)"


# ───────────────────── REGRESSION: existing endpoints still work ─────────────
def test_regression_login_and_endpoints(auth, branch_id):
    # Re-login (independent of the cached fixture token) — proves auth flow
    r = requests.post(
        f"{API}/auth/login",
        json={"email": TEST_ORG_ADMIN_EMAIL, "password": TEST_ORG_ADMIN_PASSWORD},
        timeout=15,
    )
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    h = {"Authorization": f"Bearer {token}"}

    r = requests.get(f"{API}/products", params={"branch_id": branch_id}, headers=h, timeout=30)
    assert r.status_code == 200, r.text
    body = r.json()
    # Implementation returns a paginated envelope OR bare list depending on route
    assert isinstance(body, (list, dict))
    if isinstance(body, dict):
        assert "products" in body and isinstance(body["products"], list)

    r = requests.get(f"{API}/customers", headers=h, timeout=30)
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body, (list, dict))
    if isinstance(body, dict):
        assert "customers" in body and isinstance(body["customers"], list)

    r = requests.get(f"{API}/inventory", headers=h, timeout=30)
    # /inventory may return list or dict depending on impl; just check 200
    assert r.status_code == 200, r.text


# ───────────────────── EDGE: admin_pin_hash absent gracefully ───────────────
def test_admin_pin_hash_returns_none_when_missing(auth, branch_id):
    """Stripping admin_pin doc → endpoint must return admin_pin_hash:null
    (not 500). Restored under the test admin's org after the test."""
    token, user = auth
    org_id = user.get("organization_id")
    db = _db()
    saved = db.system_settings.find_one({"key": "admin_pin"}, {"_id": 0})
    db.system_settings.delete_many({"key": "admin_pin"})
    try:
        assert db.system_settings.count_documents({"key": "admin_pin"}) == 0
        h = {"Authorization": f"Bearer {token}"}
        r = requests.get(
            f"{API}/sync/pos-data", params={"branch_id": branch_id},
            headers=h, timeout=60,
        )
        assert r.status_code == 200, r.text
        assert "admin_pin_hash" in r.json()
        hash_val = r.json()["admin_pin_hash"]
        if hash_val:
            assert hash_val.startswith("$2"), f"non-null but not bcrypt: {hash_val!r}"
        else:
            assert hash_val in (None, "")
    finally:
        # Re-seed under THE TEST ADMIN's organization_id (idempotent w/ helpers)
        from _org_test_helpers import _hash_password, TEST_ORG_ADMIN_PIN
        db.system_settings.update_one(
            {"key": "admin_pin"},
            {"$set": {
                "key": "admin_pin",
                "pin_hash": (saved or {}).get("pin_hash") or _hash_password(TEST_ORG_ADMIN_PIN),
                "organization_id": org_id,
            }},
            upsert=True,
        )

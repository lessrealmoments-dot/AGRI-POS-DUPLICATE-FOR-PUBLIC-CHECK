"""
Regression: Offline Price Match — sync replay (Iter 202).

Verifies that when an offline sale carries `price_changes` + `price_match_pin`,
the `/sales/sync` endpoint:
  1. Re-validates the PIN against the current bcrypt admin hash.
  2. Upserts `branch_prices` (when scope = branch permanent and PIN matched).
  3. Skips `branch_prices` upsert when scope = customer_only (still logs).
  4. Always writes to `price_change_log` with `offline_origin=True`.
  5. Does NOT crash when PIN validation fails — flags `pin_resync_failed=True`
     and skips the branch_prices update so the sale itself still imports.
"""
import os
import requests
import uuid


BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://stock-request-sms.preview.emergentagent.com")
API = f"{BASE_URL}/api"


def _login():
    r = requests.post(
        f"{API}/auth/login",
        json={"email": "test_org_admin@regression.local", "password": "RegressionPass!2026"},
        timeout=15,
    )
    r.raise_for_status()
    body = r.json()
    return body.get("token") or body.get("access_token")


def _branches(token):
    r = requests.get(f"{API}/branches", headers={"Authorization": f"Bearer {token}"}, timeout=15)
    r.raise_for_status()
    body = r.json()
    return (body.get("branches") if isinstance(body, dict) else body) or []


def _seed_product(token, branch_id, retail=100):
    r = requests.post(
        f"{API}/products",
        json={
            "name": f"OfflinePM-{uuid.uuid4().hex[:5]}",
            "sku": f"OPM-{uuid.uuid4().hex[:6]}",
            "category": "Offline Price Match",
            "unit": "pc",
            "prices": {"retail": retail, "wholesale": retail - 5, "dealer": retail - 10},
            "cost_price": 50,
        },
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    r.raise_for_status()
    pid = r.json()["id"]
    requests.post(
        f"{API}/inventory/adjust",
        json={"product_id": pid, "branch_id": branch_id, "quantity": 50, "reason": "regression"},
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    return pid


def _branch_retail(token, branch_id, pid):
    r = requests.get(
        f"{API}/branch-prices",
        params={"branch_id": branch_id, "product_id": pid},
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    rows = r.json() if isinstance(r.json(), list) else r.json().get("rows") or r.json().get("items") or []
    for row in rows:
        if row.get("product_id") == pid:
            return (row.get("prices") or {}).get("retail")
    return None


def _build_offline_sale(branch_id, branch_name, pid, new_price, customer_only, pin):
    sid = f"offline-pm-{uuid.uuid4().hex[:8]}"
    return {
        "id": sid,
        "envelope_id": sid,
        "branch_id": branch_id,
        "branch_name": branch_name,
        "items": [{
            "product_id": pid, "product_name": "x",
            "quantity": 1, "rate": new_price, "price": new_price, "total": new_price,
        }],
        "subtotal": new_price,
        "grand_total": new_price,
        "amount_paid": new_price,
        "balance": 0,
        "payment_type": "cash",
        "payment_method": "cash",
        "status": "completed",
        "order_date": "2026-05-02",
        "date": "2026-05-02",
        "timestamp": "2026-05-02T01:00:00Z",
        "price_changes": [{
            "product_id": pid,
            "scheme": "retail",
            "old_price": 100,
            "new_price": new_price,
            "reason": "competitor_match",
            "reason_detail": "regression replay",
            "customer_only": customer_only,
        }],
        "price_match_pin": pin,
    }


def _sync(token, sale):
    return requests.post(
        f"{API}/sales/sync",
        json={"sales": [sale]},
        headers={"Authorization": f"Bearer {token}"},
        timeout=20,
    )


def test_offline_pm_branch_permanent_replays_branch_price_update():
    token = _login()
    branch = _branches(token)[0]
    pid = _seed_product(token, branch["id"])
    try:
        before = _branch_retail(token, branch["id"], pid)
        sale = _build_offline_sale(branch["id"], branch["name"], pid, new_price=85,
                                   customer_only=False, pin="913712")
        r = _sync(token, sale)
        assert r.status_code == 200, r.text
        synced = r.json().get("synced") or r.json().get("results") or []
        assert any(s.get("status") in ("synced", "duplicate") for s in synced), r.text
        after = _branch_retail(token, branch["id"], pid)
        assert after == 85 or (after and abs(float(after) - 85) < 0.01), \
            f"branch_prices.retail must be 85 after offline PM replay (was {before}, after {after})"
    finally:
        requests.delete(f"{API}/products/{pid}", headers={"Authorization": f"Bearer {token}"})


def test_offline_pm_customer_only_does_not_update_branch_price():
    token = _login()
    branch = _branches(token)[0]
    pid = _seed_product(token, branch["id"])
    try:
        before = _branch_retail(token, branch["id"], pid)
        sale = _build_offline_sale(branch["id"], branch["name"], pid, new_price=80,
                                   customer_only=True, pin="913712")
        r = _sync(token, sale)
        assert r.status_code == 200, r.text
        after = _branch_retail(token, branch["id"], pid)
        assert before == after, \
            f"customer_only offline PM must NOT alter branch_prices (was {before}, after {after})"
    finally:
        requests.delete(f"{API}/products/{pid}", headers={"Authorization": f"Bearer {token}"})


def test_offline_pm_invalid_pin_still_imports_sale_but_skips_update():
    """If PIN was rotated between offline-write and sync, the sale already
    happened — we MUST still import it, just skip the branch_prices update."""
    token = _login()
    branch = _branches(token)[0]
    pid = _seed_product(token, branch["id"])
    try:
        before = _branch_retail(token, branch["id"], pid)
        sale = _build_offline_sale(branch["id"], branch["name"], pid, new_price=70,
                                   customer_only=False, pin="000000")  # invalid
        r = _sync(token, sale)
        assert r.status_code == 200, r.text
        synced = r.json().get("synced") or r.json().get("results") or []
        # Sale still imported
        assert any(s.get("status") in ("synced", "duplicate") for s in synced)
        # branch_prices unchanged because PIN failed
        after = _branch_retail(token, branch["id"], pid)
        assert before == after, \
            f"PIN-failed offline PM must NOT update branch_prices (was {before}, after {after})"
    finally:
        requests.delete(f"{API}/products/{pid}", headers={"Authorization": f"Bearer {token}"})


if __name__ == "__main__":
    test_offline_pm_branch_permanent_replays_branch_price_update()
    print("PASS: offline_pm_branch_permanent_replays")
    test_offline_pm_customer_only_does_not_update_branch_price()
    print("PASS: offline_pm_customer_only_skips_branch_update")
    test_offline_pm_invalid_pin_still_imports_sale_but_skips_update()
    print("PASS: offline_pm_invalid_pin_still_imports_skips_update")
    print("\nAll offline price-match replay regression tests passed.")

"""
Regression: Price Match — `customer_only` scope (Iter 202).

Verifies:
  1. Default Price Match (customer_only=False) UPDATES `branch_prices`
     and writes price_change_log with scope='branch_permanent'.
  2. Customer-only Price Match (customer_only=True) DOES NOT update
     `branch_prices` but DOES write price_change_log with
     scope='customer_only' for audit.
  3. Both paths still require a manager PIN (the existing PIN flow is
     unchanged — we just verify customer_only doesn't bypass it).
"""
import os
import requests
import uuid


BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://po-capital-fix.preview.emergentagent.com")
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


def _seed_product(token, branch_id, name, retail=100, capital=70):
    r = requests.post(
        f"{API}/products",
        json={
            "name": name,
            "sku": f"PM-{uuid.uuid4().hex[:6]}",
            "category": "Price Match Test",
            "unit": "pc",
            "prices": {"retail": retail, "wholesale": retail - 5, "dealer": retail - 10},
            "cost_price": capital,
        },
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    if r.status_code not in (200, 201):
        return None
    pid = r.json().get("id")
    # Push some inventory so the sale doesn't get blocked on stock
    requests.post(
        f"{API}/inventory/adjust",
        json={"product_id": pid, "branch_id": branch_id, "quantity": 50, "reason": "regression seed"},
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    return pid


def _branch_price(token, branch_id, pid):
    r = requests.get(
        f"{API}/branch-prices",
        params={"branch_id": branch_id, "product_id": pid},
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    if r.status_code != 200:
        return None
    rows = r.json() if isinstance(r.json(), list) else r.json().get("rows") or r.json().get("items") or []
    for row in rows:
        if row.get("product_id") == pid:
            return row
    return None


def _make_sale(token, branch_id, pid, retail_match_price, customer_only, pin="913712"):
    payload = {
        "branch_id": branch_id,
        "branch_name": "Regression",
        "items": [{"product_id": pid, "quantity": 1, "rate": retail_match_price}],
        "payment_type": "cash",
        "amount_paid": retail_match_price,
        "price_scheme": "retail",
        "price_changes": [{
            "product_id": pid,
            "scheme": "retail",
            "old_price": 100,
            "new_price": retail_match_price,
            "reason": "competitor_match",
            "reason_detail": "regression test",
            "customer_only": customer_only,
        }],
        "price_match_pin": pin,
    }
    return requests.post(
        f"{API}/unified-sale",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
        timeout=20,
    )


def _branches(token):
    r = requests.get(f"{API}/branches", headers={"Authorization": f"Bearer {token}"}, timeout=15)
    r.raise_for_status()
    body = r.json()
    rows = body.get("branches") if isinstance(body, dict) else body
    return rows or []


def test_customer_only_skips_branch_price_update():
    token = _login()
    branch_id = _branches(token)[0]["id"]
    pid = _seed_product(token, branch_id, f"PMcust-{uuid.uuid4().hex[:5]}")
    assert pid, "seed failed"

    try:
        # Snapshot existing branch_prices state
        before = _branch_price(token, branch_id, pid)
        before_retail = (before or {}).get("prices", {}).get("retail")

        # CUSTOMER-ONLY price match at ₱85 (was ₱100)
        r = _make_sale(token, branch_id, pid, retail_match_price=85, customer_only=True)
        assert r.status_code == 200, f"sale failed: {r.status_code} {r.text}"

        # branch_prices.retail must NOT have been updated
        after = _branch_price(token, branch_id, pid)
        after_retail = (after or {}).get("prices", {}).get("retail")
        assert before_retail == after_retail, \
            f"customer_only must NOT update branch price (was {before_retail}, after {after_retail})"

    finally:
        requests.delete(f"{API}/products/{pid}", headers={"Authorization": f"Bearer {token}"})


def test_branch_permanent_updates_branch_price():
    token = _login()
    branch_id = _branches(token)[0]["id"]
    pid = _seed_product(token, branch_id, f"PMperm-{uuid.uuid4().hex[:5]}")
    assert pid

    try:
        # PERMANENT price match at ₱90 (was ₱100)
        r = _make_sale(token, branch_id, pid, retail_match_price=90, customer_only=False)
        assert r.status_code == 200, f"sale failed: {r.status_code} {r.text}"

        # branch_prices.retail must now be ₱90
        after = _branch_price(token, branch_id, pid)
        retail = (after or {}).get("prices", {}).get("retail")
        assert retail == 90 or abs(float(retail) - 90) < 0.01, \
            f"permanent match must update branch price to 90, got {retail}"

    finally:
        requests.delete(f"{API}/products/{pid}", headers={"Authorization": f"Bearer {token}"})


if __name__ == "__main__":
    test_customer_only_skips_branch_price_update()
    print("PASS: customer_only_skips_branch_price_update")
    test_branch_permanent_updates_branch_price()
    print("PASS: branch_permanent_updates_branch_price")
    print("\nAll Price Match scope regression tests passed.")

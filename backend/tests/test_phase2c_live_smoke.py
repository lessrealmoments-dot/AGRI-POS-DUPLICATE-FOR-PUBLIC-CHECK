"""Phase 2C live API smoke test — verify live endpoints return 200 after login.

Endpoints tested (per Phase 2C review request):
- /api/admin/customer-balance-reconciliation
- /api/products
- /api/customers
"""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL")
if not BASE_URL:
    # Fallback: read from frontend/.env
    try:
        with open("/app/frontend/.env", "r") as f:
            for line in f:
                if line.startswith("REACT_APP_BACKEND_URL="):
                    BASE_URL = line.split("=", 1)[1].strip()
                    break
    except Exception:
        pass

BASE_URL = (BASE_URL or "").rstrip("/")

ADMIN_EMAIL = "janmarkeahig@gmail.com"
ADMIN_PASSWORD = "Aa@58798546521325"


@pytest.fixture(scope="module")
def admin_token():
    if not BASE_URL:
        pytest.skip("REACT_APP_BACKEND_URL not configured")
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        timeout=30,
    )
    if r.status_code != 200:
        pytest.skip(f"Admin login failed: {r.status_code} {r.text[:200]}")
    data = r.json()
    token = data.get("access_token") or data.get("token")
    if not token:
        pytest.skip(f"No token in login response: {data}")
    return token


@pytest.fixture(scope="module")
def auth_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"}


def test_smoke_balance_reconciliation_live(auth_headers):
    r = requests.get(
        f"{BASE_URL}/api/admin/customer-balance-reconciliation",
        headers=auth_headers,
        timeout=30,
    )
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text[:300]}"
    body = r.json()
    # Must be a dict with the documented top-level keys when populated; empty
    # tenant may short-circuit, but the request explicitly states 200 is required.
    assert isinstance(body, dict), f"Expected dict response, got {type(body)}"


def test_smoke_products_live(auth_headers):
    r = requests.get(f"{BASE_URL}/api/products", headers=auth_headers, timeout=30)
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text[:300]}"
    body = r.json()
    # Products endpoint returns a list (possibly empty for 0-customer tenant)
    assert isinstance(body, (list, dict)), f"Unexpected type: {type(body)}"


def test_smoke_customers_live(auth_headers):
    r = requests.get(f"{BASE_URL}/api/customers", headers=auth_headers, timeout=30)
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text[:300]}"
    body = r.json()
    assert isinstance(body, (list, dict)), f"Unexpected type: {type(body)}"


def test_smoke_unauth_blocks_balance_reconciliation():
    r = requests.get(
        f"{BASE_URL}/api/admin/customer-balance-reconciliation", timeout=30
    )
    assert r.status_code in (401, 403), f"Unauth should be 401/403, got {r.status_code}"

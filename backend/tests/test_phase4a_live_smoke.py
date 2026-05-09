"""Phase 4A live API smoke tests against REACT_APP_BACKEND_URL.

Covers the BACKEND-1..BACKEND-6 cases from the iter256 review request:
- /api/historical-credit/preview soft-floor (days_back<=7)
- /api/historical-credit commit soft-floor + approval_code gate
- /api/historical-credit list (admin only)
- /api/unified-sale today's normal cash sale (no regression)
"""

from __future__ import annotations
import os
import uuid
from datetime import date, timedelta
import pytest
import requests

API_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
ADMIN_EMAIL = "test_org_admin@regression.local"
ADMIN_PASS = "RegressionPass!2026"


def _login(session: requests.Session) -> str:
    r = session.post(
        f"{API_URL}/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASS},
        timeout=20,
    )
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    body = r.json()
    token = body.get("token") or body.get("access_token")
    assert token, f"no token in login response: {body}"
    return token


@pytest.fixture(scope="module")
def admin_client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    token = _login(s)
    s.headers.update({"Authorization": f"Bearer {token}"})
    return s


@pytest.fixture(scope="module")
def sample_context(admin_client):
    """Resolve a customer + product + branch the admin can use."""
    customers = admin_client.get(f"{API_URL}/api/customers", timeout=20)
    assert customers.status_code == 200, customers.text
    cust_list = customers.json()
    if isinstance(cust_list, dict):
        cust_list = cust_list.get("customers") or cust_list.get("items") or []
    assert cust_list, "no customers available for live smoke"
    customer = cust_list[0]

    products = admin_client.get(f"{API_URL}/api/products", timeout=20)
    assert products.status_code == 200, products.text
    plist = products.json()
    if isinstance(plist, dict):
        plist = plist.get("products") or plist.get("items") or []
    assert plist, "no products available for live smoke"
    product = plist[0]

    branch_id = customer.get("branch_id") or product.get("branch_id")
    return {"customer": customer, "product": product, "branch_id": branch_id}


def _payload(sample, days_back: int, include_approval=False, approval_code=None):
    tx = (date.today() - timedelta(days=days_back)).isoformat()
    unit = float(sample["product"].get("price") or 100.0)
    qty = 1
    body = {
        "customer_id": sample["customer"]["id"],
        "branch_id": sample["branch_id"] or sample["customer"].get("branch_id"),
        "transaction_date": tx,
        "grand_total": round(unit * qty, 2),
        "items": [
            {
                "product_id": sample["product"]["id"],
                "quantity": qty,
                "unit_price": unit,
                "total": round(unit * qty, 2),
            }
        ],
        "reason": (
            "Notebook AR carry-forward verified against ledger page 12 — "
            "live smoke test phase4a iter256 backdated entry."
        ),
    }
    if include_approval:
        body["approval_code"] = approval_code or "000000"
    return body


# ---- BACKEND-1 ----------------------------------------------------------
def test_b1_preview_soft_floor_3_days(admin_client, sample_context):
    body = _payload(sample_context, days_back=3)
    r = admin_client.post(
        f"{API_URL}/api/historical-credit/preview", json=body, timeout=20
    )
    assert r.status_code == 400, f"expected 400, got {r.status_code}: {r.text}"
    detail = r.json().get("detail", {})
    assert isinstance(detail, dict), f"detail not dict: {detail}"
    assert detail.get("error") == "use_regular_late_encode", detail


# ---- BACKEND-2 ----------------------------------------------------------
def test_b2_commit_soft_floor_3_days(admin_client, sample_context):
    body = _payload(sample_context, days_back=3, include_approval=True,
                    approval_code="000000")
    r = admin_client.post(
        f"{API_URL}/api/historical-credit", json=body, timeout=20
    )
    assert r.status_code == 400, f"expected 400, got {r.status_code}: {r.text}"
    detail = r.json().get("detail", {})
    assert detail.get("error") == "use_regular_late_encode", detail


# ---- BACKEND-3 ----------------------------------------------------------
def test_b3_commit_missing_approval_code(admin_client, sample_context):
    body = _payload(sample_context, days_back=10)  # no approval_code
    r = admin_client.post(
        f"{API_URL}/api/historical-credit", json=body, timeout=20
    )
    assert r.status_code == 400, f"expected 400, got {r.status_code}: {r.text}"
    detail = r.json().get("detail", {})
    assert detail.get("error") == "approval_code_required", detail


# ---- BACKEND-4 ----------------------------------------------------------
def test_b4_commit_wrong_approval_code(admin_client, sample_context):
    body = _payload(sample_context, days_back=10, include_approval=True,
                    approval_code="000000")
    r = admin_client.post(
        f"{API_URL}/api/historical-credit", json=body, timeout=20
    )
    assert r.status_code == 403, f"expected 403, got {r.status_code}: {r.text}"
    detail = r.json().get("detail", {})
    assert detail.get("error") == "approval_invalid", detail


# ---- BACKEND-5 ----------------------------------------------------------
def test_b5_list_returns_200(admin_client):
    r = admin_client.get(f"{API_URL}/api/historical-credit", timeout=20)
    assert r.status_code == 200, f"expected 200, got {r.status_code}: {r.text}"
    body = r.json()
    # list shape can be either array or {items: [...]}
    assert isinstance(body, (list, dict)), f"unexpected body: {body}"


# ---- BACKEND-6 (no regression: today's cash sale on /api/unified-sale) --
def test_b6_today_unified_sale_cash_no_regression(admin_client, sample_context):
    p = sample_context["product"]
    qty = 1
    unit = float(p.get("price") or 100.0)
    body = {
        "customer_id": sample_context["customer"]["id"],
        "branch_id": sample_context["branch_id"]
        or sample_context["customer"].get("branch_id"),
        "items": [
            {
                "product_id": p["id"],
                "quantity": qty,
                "unit_price": unit,
            }
        ],
        "payment_type": "cash",
        "amount_paid": unit * qty,
        "idempotency_key": f"phase4a-live-smoke-{uuid.uuid4().hex[:10]}",
    }
    r = admin_client.post(f"{API_URL}/api/unified-sale", json=body, timeout=30)
    # Accept 200/201; if backend returns a domain-validation error, surface the
    # text so we can distinguish a phase-4a regression from unrelated noise.
    assert r.status_code in (200, 201), (
        f"unified-sale today regression? {r.status_code}: {r.text[:400]}"
    )

"""Iteration 165 — signature session API regression."""
import os
import base64
import requests
import pytest

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://crop-credit-pos.preview.emergentagent.com").rstrip("/")
ADMIN_EMAIL = "janmarkeahig@gmail.com"
ADMIN_PASS = "Aa@58798546521325"
MANAGER_PIN = "521325"

# 1x1 transparent PNG
TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4"
    "nGNgYGBgAAAABQABh6FO1AAAAABJRU5ErkJggg=="
)
# Bigger valid PNG (>100 bytes after decode) — repeat the 1x1 to pass size check
BIG_PNG_BYTES = base64.b64decode(TINY_PNG_B64) * 20
BIG_PNG_B64 = base64.b64encode(BIG_PNG_BYTES).decode("ascii")


@pytest.fixture(scope="module")
def auth_token():
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASS}, timeout=15)
    assert r.status_code == 200, r.text
    return r.json().get("access_token") or r.json()["token"]


@pytest.fixture(scope="module")
def headers(auth_token):
    return {"Authorization": f"Bearer {auth_token}", "Content-Type": "application/json"}


def _make_session(headers, **overrides):
    payload = {
        "linked_record_type": "sale",
        "linked_record_id": "",
        "branch_id": "",
        "credit_context": {
            "customer_name": "TEST_Sig Customer",
            "amount": 1234.56,
            "credit_type": "by_term",
            "date": "2026-01-15",
            "branch_name": "Main Branch",
            "description": "Test credit",
            "invoice_number": "TEST-INV-165",
            "items": [
                {"product_name": "Urea 46-0-0", "quantity": 2, "unit": "bag", "rate": 1500, "total": 3000},
                {"product_name": "Pesticide", "quantity": 1, "unit": "L",   "rate": 500,  "total": 500},
            ],
            "subtotal": 3500,
            "discount": 100,
            "partial_paid": 2165.44,
        },
    }
    payload.update(overrides)
    r = requests.post(f"{BASE_URL}/api/signatures/session", json=payload, headers=headers, timeout=15)
    return r


# ── 1. Session create accepts items, totals, invoice_number ──
def test_create_session_persists_items_and_totals(headers):
    r = _make_session(headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "pending"
    assert "token" in body and len(body["token"]) > 16
    assert body["signing_url"].startswith("/sign/")
    ctx = body["credit_context"]
    assert ctx["invoice_number"] == "TEST-INV-165"
    assert isinstance(ctx["items"], list) and len(ctx["items"]) == 2
    assert ctx["items"][0]["product_name"] == "Urea 46-0-0"
    assert ctx["subtotal"] == 3500
    assert ctx["discount"] == 100
    assert ctx["partial_paid"] == 2165.44
    assert ctx["amount"] == 1234.56


# ── 2. status returns signature_url field key (null while pending) ──
def test_status_includes_signature_url_field(headers):
    r = _make_session(headers)
    token = r.json()["token"]
    s = requests.get(f"{BASE_URL}/api/signatures/status/{token}", headers=headers, timeout=10)
    assert s.status_code == 200, s.text
    body = s.json()
    assert "signature_url" in body
    assert body["status"] == "pending"
    assert body["signature_url"] is None
    assert body["expired"] is False
    assert "credit_context" in body


# ── 3. Public submit endpoint marks signed; status returns signature_url ──
def test_submit_then_status_returns_signature_url():
    # login
    _r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASS}, timeout=15).json()
    tok = _r.get("access_token") or _r.get("token")
    h = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}
    r = _make_session(h)
    token = r.json()["token"]

    # Public submit (no auth)
    sub = requests.post(
        f"{BASE_URL}/api/signatures/submit/{token}",
        json={"signature": f"data:image/png;base64,{BIG_PNG_B64}"},
        timeout=15,
    )
    assert sub.status_code == 200, sub.text
    assert sub.json()["status"] == "signed"

    # Verify status now reports signed (signature_url may be present if R2 configured, may be None otherwise)
    s = requests.get(f"{BASE_URL}/api/signatures/status/{token}", headers=h, timeout=10)
    assert s.status_code == 200
    body = s.json()
    assert body["status"] == "signed"
    assert "signature_url" in body  # field always present (may be None if R2 misconfigured)
    assert body["signed_at"] is not None


# ── 4. Submit empty signature should 400 ──
def test_submit_empty_signature_rejected(headers):
    r = _make_session(headers)
    token = r.json()["token"]
    sub = requests.post(f"{BASE_URL}/api/signatures/submit/{token}", json={"signature": ""}, timeout=10)
    assert sub.status_code == 400


# ── 5. Bypass with manager PIN ──
def test_bypass_with_manager_pin(headers):
    r = _make_session(headers)
    session_id = r.json()["id"]
    bp = requests.post(
        f"{BASE_URL}/api/signatures/bypass/{session_id}",
        json={"pin": MANAGER_PIN, "reason": "TEST_165 customer rushed off"},
        headers=headers, timeout=15,
    )
    assert bp.status_code == 200, bp.text
    assert bp.json()["status"] == "bypassed"

    # Status should reflect bypass
    token = r.json()["token"]
    s = requests.get(f"{BASE_URL}/api/signatures/status/{token}", headers=headers, timeout=10).json()
    assert s["status"] == "bypassed"
    assert s["bypass_method"] in ("pin", "totp", "manager_pin", "admin_pin")


# ── 6. Bypass with bad PIN should 403 ──
def test_bypass_wrong_pin(headers):
    r = _make_session(headers)
    session_id = r.json()["id"]
    bp = requests.post(
        f"{BASE_URL}/api/signatures/bypass/{session_id}",
        json={"pin": "000000", "reason": "TEST_165 wrong"},
        headers=headers, timeout=15,
    )
    assert bp.status_code == 403


# ── 7. Regression: receivables-summary still includes oldest_overdue_due_date ──
def test_receivables_summary_regression(headers):
    r = requests.get(f"{BASE_URL}/api/customers/receivables-summary", headers=headers, timeout=15)
    assert r.status_code == 200, r.text
    rows = r.json()
    assert isinstance(rows, list)
    if rows:
        row = rows[0]
        # Ensure expected fields present
        for f in ["id", "name", "balance", "overdue_balance", "oldest_overdue_due_date"]:
            assert f in row, f"Missing field {f} in receivables-summary row"


# ── 8. Public view endpoint returns context for pending session ──
def test_public_view_endpoint(headers):
    r = _make_session(headers)
    token = r.json()["token"]
    v = requests.get(f"{BASE_URL}/api/signatures/view/{token}", timeout=10)
    assert v.status_code == 200, v.text
    body = v.json()
    assert body["status"] == "pending"
    assert body["credit_context"]["customer_name"] == "TEST_Sig Customer"
    assert len(body["credit_context"]["items"]) == 2

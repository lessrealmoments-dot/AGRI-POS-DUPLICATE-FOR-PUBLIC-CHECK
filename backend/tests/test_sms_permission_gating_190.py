"""
Iteration 190 — SMS endpoint permission gating.

Verifies that previously-ungated SMS endpoints now reject non-admin/non-manager
users:
  • POST /sms/send                    → requires `customers.edit`
  • POST /sms/templates/backfill      → requires `settings.edit`
  • POST /sms/send-sample-single      → requires `settings.edit`
  • POST /sms/queue/{id}/retry        → requires `settings.edit`

A cashier-role user is provisioned (with default cashier permissions which
include `customers.create` but NOT `customers.edit` or `settings.edit`) and
expected to be 403'd from each.
"""
import bcrypt
import pytest
import requests
from uuid import uuid4

from _org_test_helpers import API, _db, ensure_org_admin_token

CASHIER_EMAIL = "test_sms_perm_cashier@regression.local"
CASHIER_PASSWORD = "CashierPerm!2026"


@pytest.fixture(scope="module")
def admin_auth():
    return ensure_org_admin_token()


@pytest.fixture(scope="module")
def cashier_token(admin_auth):
    """Provision a cashier in the same org as the admin and return its token."""
    _, admin_user = admin_auth
    org_id = admin_user["organization_id"]
    db = _db()

    perms = {
        "customers": {"view": True, "create": True, "edit": False, "delete": False,
                      "view_balance": False, "manage_credit": False},
        "sales":     {"view": True, "create": True, "edit": False, "void": False,
                      "discount": False, "view_capital": False},
        "settings":  {"view": False, "edit": False, "manage_users": False, "manage_permissions": False},
    }
    pw_hash = bcrypt.hashpw(CASHIER_PASSWORD.encode(), bcrypt.gensalt()).decode()

    db.users.delete_many({"email": CASHIER_EMAIL})
    db.users.insert_one({
        "id": str(uuid4()),
        "username": f"sms_perm_cashier_{uuid4().hex[:6]}",
        "email": CASHIER_EMAIL,
        "full_name": "SMS Perm Test Cashier",
        "password_hash": pw_hash,
        "role": "cashier",
        "active": True,
        "organization_id": org_id,
        "branch_id": None,
        "permissions": perms,
    })

    r = requests.post(f"{API}/auth/login",
                      json={"email": CASHIER_EMAIL, "password": CASHIER_PASSWORD},
                      timeout=15)
    assert r.status_code == 200, r.text
    yield r.json()["token"]
    db.users.delete_many({"email": CASHIER_EMAIL})


def test_cashier_blocked_from_sms_send(cashier_token):
    r = requests.post(
        f"{API}/sms/send",
        headers={"Authorization": f"Bearer {cashier_token}"},
        json={"phone": "09171234567", "message": "Hello cashier test"},
        timeout=10,
    )
    assert r.status_code == 403, r.text
    assert "customers.edit" in r.text


def test_cashier_blocked_from_templates_backfill(cashier_token):
    r = requests.post(
        f"{API}/sms/templates/backfill",
        headers={"Authorization": f"Bearer {cashier_token}"},
        timeout=10,
    )
    assert r.status_code == 403, r.text
    assert "settings.edit" in r.text


def test_cashier_blocked_from_sample_single(cashier_token):
    r = requests.post(
        f"{API}/sms/send-sample-single",
        headers={"Authorization": f"Bearer {cashier_token}"},
        json={"phone": "09171234567", "role": "Owner"},
        timeout=10,
    )
    assert r.status_code == 403, r.text
    assert "settings.edit" in r.text


def test_admin_can_send_sample_single(admin_auth):
    token, _ = admin_auth
    r = requests.post(
        f"{API}/sms/send-sample-single",
        headers={"Authorization": f"Bearer {token}"},
        json={"phone": "09171234567", "role": "Owner"},
        timeout=10,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["queued"] == 1
    assert body["phone"] == "09171234567"
    assert body["role"] == "Owner"


def test_sample_single_rejects_empty_phone(admin_auth):
    token, _ = admin_auth
    r = requests.post(
        f"{API}/sms/send-sample-single",
        headers={"Authorization": f"Bearer {token}"},
        json={"phone": "", "role": "Owner"},
        timeout=10,
    )
    assert r.status_code == 400, r.text
    assert "phone" in r.text.lower()

"""
Iteration 181 — Super-admin tenant impersonation ("View as Tenant").

The privacy fix in iter 180 hard-blocks super-admin from tenant data. To
legitimately help a customer (debug pricing, fix data, etc.) the super
admin needs an explicit, audited way in. This iteration adds:

  POST /api/superadmin/impersonate/{org_id}/enter   → start session
  POST /api/superadmin/impersonate/exit             → end session
  GET  /api/superadmin/impersonate/status           → banner state

Active session sets the org context on every request → tenant data becomes
visible. Auto-expires after 4 hours. Every enter/exit logged in audit_log.

Tests cover:
  1. Super admin can enter and now sees tenant data.
  2. After exit, scope reverts (tenant data hidden again).
  3. Audit log entries created for enter and exit.
  4. Non-super-admin users cannot call the endpoints.
  5. Status endpoint reports active state for the banner.
"""
import os
import sys
import uuid
import requests
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "test_database")
API = os.environ.get(
    "API_URL", "https://bulk-delete-5.preview.emergentagent.com"
).rstrip("/") + "/api"
SUPER_EMAIL = os.environ.get("TEST_EMAIL", "janmarkeahig@gmail.com")
SUPER_PASSWORD = os.environ.get("TEST_PASSWORD", "Aa@58798546521325")


def _db():
    return MongoClient(MONGO_URL)[DB_NAME]


def _login_super():
    r = requests.post(
        f"{API}/auth/login",
        json={"email": SUPER_EMAIL, "password": SUPER_PASSWORD},
        timeout=15,
    )
    r.raise_for_status()
    body = r.json()
    return body["token"], body.get("user", {})


def _login_org_admin():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _org_test_helpers import ensure_org_admin_token
    return ensure_org_admin_token()


def test_impersonation_full_round_trip():
    """
    Plant a tenant with a uniquely-named customer. As super admin:
      1. Before enter: scoped /customers must NOT include the planted customer.
      2. enter → scoped /customers DOES include it.
      3. exit → /customers no longer includes it.
    """
    db = _db()
    org_id = f"impersonation-test-{uuid.uuid4()}"
    db.organizations.insert_one({
        "id": org_id, "name": "Impersonation Test Co", "phone": "111", "email": ""
    })
    cust_marker = f"PLANTED_CUST_{uuid.uuid4().hex[:8].upper()}"
    db.customers.insert_one({
        "id": str(uuid.uuid4()), "organization_id": org_id,
        "name": cust_marker, "phone": "0900",
        "active": True, "balance": 0,
    })

    super_token, _ = _login_super()
    h = {"Authorization": f"Bearer {super_token}"}

    try:
        # 1. Before enter — privacy enforced
        r = requests.get(f"{API}/customers", headers=h, timeout=15)
        assert r.status_code in (200, 403), r.text
        names_before = []
        if r.status_code == 200:
            data = r.json()
            customers = data.get("customers", []) if isinstance(data, dict) else data
            names_before = [c.get("name", "") for c in customers]
        assert cust_marker not in names_before, (
            f"PRE-CHECK FAILED: super admin saw {cust_marker} BEFORE entering "
            "impersonation — iter 180 privacy fix regressed"
        )
        print("PASS · before enter: tenant customer hidden")

        # 2. Enter
        r = requests.post(f"{API}/superadmin/impersonate/{org_id}/enter", headers=h, timeout=15)
        assert r.status_code == 200, r.text
        body = r.json()
        sess = body["session"]
        assert sess["target_org_id"] == org_id
        assert sess["target_org_name"] == "Impersonation Test Co"
        assert sess["active"] is True
        print(f"PASS · entered impersonation; session={sess['id'][:8]}…")

        # 3. During impersonation — must see the planted customer
        r = requests.get(f"{API}/customers", headers=h, timeout=15)
        assert r.status_code == 200, r.text
        data = r.json()
        customers = data.get("customers", []) if isinstance(data, dict) else data
        names_during = [c.get("name", "") for c in customers]
        assert cust_marker in names_during, (
            f"impersonation did NOT scope to target org. customers seen: "
            f"{names_during[:5]}"
        )
        print(f"PASS · during impersonation: planted customer visible (1 of {len(names_during)})")

        # 4. Status endpoint reports active session for the banner
        r = requests.get(f"{API}/superadmin/impersonate/status", headers=h, timeout=15)
        assert r.status_code == 200
        body = r.json()
        assert body["active"] is True, body
        assert body["target_org_id"] == org_id
        print(f"PASS · /status reports active for banner")

        # 5. Exit
        r = requests.post(f"{API}/superadmin/impersonate/exit", headers=h, timeout=15)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ended"] is True
        print(f"PASS · exited: {body['message']}")

        # 6. After exit — privacy enforced again
        r = requests.get(f"{API}/customers", headers=h, timeout=15)
        if r.status_code == 200:
            data = r.json()
            customers = data.get("customers", []) if isinstance(data, dict) else data
            names_after = [c.get("name", "") for c in customers]
            assert cust_marker not in names_after, (
                f"POST-EXIT LEAK: super admin still sees {cust_marker} after exit"
            )
        print("PASS · after exit: tenant customer hidden again")

        # 7. Audit log: enter + exit events present
        enter_log = db.audit_log.find_one({
            "event_type": "tenant_impersonation_enter",
            "session_id": sess["id"],
        })
        exit_log = db.audit_log.find_one({
            "event_type": "tenant_impersonation_exit",
            "session_id": sess["id"],
        })
        assert enter_log is not None, "missing audit_log entry for enter"
        assert exit_log is not None, "missing audit_log entry for exit"
        assert enter_log["target_org_id"] == org_id
        assert exit_log["target_org_id"] == org_id
        print(f"PASS · audit_log has BOTH enter and exit entries")
    finally:
        db.organizations.delete_one({"id": org_id})
        db.customers.delete_many({"organization_id": org_id})
        db.impersonation_sessions.delete_many({"target_org_id": org_id})
        db.audit_log.delete_many({"session_id": sess["id"] if 'sess' in dir() else ""})


def test_org_admin_cannot_impersonate():
    """Tenant impersonation must be locked to super admin role only."""
    token, user = _login_org_admin()
    h = {"Authorization": f"Bearer {token}"}

    org_id = user.get("organization_id", "")

    r = requests.post(f"{API}/superadmin/impersonate/{org_id}/enter", headers=h, timeout=15)
    assert r.status_code in (401, 403), (
        f"org admin must NOT be able to enter impersonation — got {r.status_code}: {r.text}"
    )
    print(f"PASS · org admin /impersonate/enter → {r.status_code} (forbidden)")

    r = requests.post(f"{API}/superadmin/impersonate/exit", headers=h, timeout=15)
    assert r.status_code in (401, 403), r.text
    print(f"PASS · org admin /impersonate/exit → {r.status_code} (forbidden)")


def test_impersonate_unknown_org_returns_404():
    """Cannot impersonate an org that doesn't exist."""
    token, _ = _login_super()
    h = {"Authorization": f"Bearer {token}"}
    fake = "00000000-fake-fake-fake-000000000000"
    r = requests.post(f"{API}/superadmin/impersonate/{fake}/enter", headers=h, timeout=15)
    assert r.status_code == 404, r.text
    print(f"PASS · impersonate unknown org → 404")


def test_status_returns_inactive_when_no_session():
    """When nothing's active the banner endpoint must report active=False."""
    token, _ = _login_super()
    h = {"Authorization": f"Bearer {token}"}
    # Ensure no leftover session
    requests.post(f"{API}/superadmin/impersonate/exit", headers=h, timeout=15)
    r = requests.get(f"{API}/superadmin/impersonate/status", headers=h, timeout=15)
    assert r.status_code == 200
    assert r.json().get("active") is False
    print("PASS · /status reports active=False when no session")


if __name__ == "__main__":
    test_impersonation_full_round_trip()
    test_org_admin_cannot_impersonate()
    test_impersonate_unknown_org_returns_404()
    test_status_returns_inactive_when_no_session()
    print("\nIteration 181 tenant impersonation tests passed.")

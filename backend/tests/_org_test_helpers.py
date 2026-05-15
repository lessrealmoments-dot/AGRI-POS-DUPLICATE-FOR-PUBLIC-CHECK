"""
Shared test helpers for org-scoped HTTP tests.

After the privacy fix (config.TenantCollection fail-closed), super-admin
tokens can no longer access tenant data via scoped endpoints. This helper
ensures an org-admin user exists with a known password and returns a
logged-in token. Idempotent — reuses the user if it already exists.
"""
import os
import requests
import bcrypt
from pymongo import MongoClient

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "test_database")
API = os.environ.get(
    "API_URL", "https://po-capital-fix.preview.emergentagent.com"
).rstrip("/") + "/api"

TEST_ORG_ADMIN_EMAIL = "test_org_admin@regression.local"
TEST_ORG_ADMIN_PASSWORD = "RegressionPass!2026"
TEST_ORG_ADMIN_PIN = "913712"
TEST_ORG_MANAGER_PIN = "521325"


def _db():
    return MongoClient(MONGO_URL)[DB_NAME]


def _hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def ensure_org_admin_token():
    """
    Returns (token, user_dict) for an org admin in any active organization.

    Picks the first non-empty organization, ensures a known test user exists
    in it with admin role + active=True + password = TEST_ORG_ADMIN_PASSWORD.
    Also ensures a manager user with PIN 521325 exists in the same org so
    PIN-based tests (signature bypass etc.) work. Idempotent across runs.
    """
    db = _db()
    org = db.organizations.find_one({}, {"_id": 0, "id": 1, "name": 1})
    if not org:
        raise RuntimeError("No organizations in test DB — cannot create org admin")
    org_id = org["id"]

    # Upsert the admin user
    existing = db.users.find_one({"email": TEST_ORG_ADMIN_EMAIL}, {"_id": 0})
    if existing:
        db.users.update_one(
            {"email": TEST_ORG_ADMIN_EMAIL},
            {"$set": {
                "password_hash": _hash_password(TEST_ORG_ADMIN_PASSWORD),
                "active": True,
                "role": "admin",
                "organization_id": org_id,
                "owner_pin": TEST_ORG_ADMIN_PIN,
            }},
        )
    else:
        from uuid import uuid4
        db.users.insert_one({
            "id": str(uuid4()),
            "username": "regression_admin",
            "email": TEST_ORG_ADMIN_EMAIL,
            "full_name": "Regression Test Admin",
            "password_hash": _hash_password(TEST_ORG_ADMIN_PASSWORD),
            "role": "admin",
            "active": True,
            "owner_pin": TEST_ORG_ADMIN_PIN,
            "branch_id": None,
            "organization_id": org_id,
            "permissions": {},
        })

    # Ensure a manager user with PIN 521325 exists in this org so PIN-bypass
    # tests work — pre-fix, these tests succeeded by leaking another org's
    # manager. We now plant the same fixture in our test org explicitly.
    # Note: the verify.py PIN check reads PLAIN TEXT `manager_pin` / `owner_pin`
    # fields on user docs (not bcrypt'd) — match that contract.
    mgr_email = "test_org_manager@regression.local"
    mgr_pin = "521325"
    if not db.users.find_one({"email": mgr_email, "organization_id": org_id}):
        from uuid import uuid4
        db.users.insert_one({
            "id": str(uuid4()),
            "username": "regression_manager",
            "email": mgr_email,
            "full_name": "Regression Test Manager",
            "password_hash": _hash_password("RegressionMgrPass!2026"),
            "manager_pin": mgr_pin,
            "role": "manager",
            "active": True,
            "branch_id": None,
            "organization_id": org_id,
            "permissions": {},
        })
    else:
        # Ensure the PIN is set even on stale rows
        db.users.update_one(
            {"email": mgr_email, "organization_id": org_id},
            {"$set": {"manager_pin": mgr_pin, "active": True, "role": "manager"}},
        )

    # Seed system-wide admin_pin (bcrypt'd) so PIN-gated actions whose policy
    # only allows "admin_pin" or "totp" can be tested. Idempotent.
    db.system_settings.update_one(
        {"key": "admin_pin"},
        {"$set": {"key": "admin_pin", "pin_hash": _hash_password(TEST_ORG_ADMIN_PIN)}},
        upsert=True,
    )

    r = requests.post(
        f"{API}/auth/login",
        json={"email": TEST_ORG_ADMIN_EMAIL, "password": TEST_ORG_ADMIN_PASSWORD},
        timeout=15,
    )
    if r.status_code != 200:
        raise RuntimeError(
            f"Failed to login as org admin: {r.status_code} {r.text}"
        )
    body = r.json()
    return body["token"], body.get("user", {})

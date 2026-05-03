"""
Seeds a manager user in the same organization as test_org_admin@regression.local
with EMPTY permissions so UI button-gating tests can verify hidden buttons.

Idempotent — safe to re-run.
"""
import os
import bcrypt
from uuid import uuid4
from pymongo import MongoClient

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "test_database")

LIMITED_MGR_EMAIL = "test_limited_mgr@regression.local"
LIMITED_MGR_PASSWORD = "LimitedMgr!2026"


def main():
    db = MongoClient(MONGO_URL)[DB_NAME]
    admin = db.users.find_one({"email": "test_org_admin@regression.local"})
    if not admin:
        raise SystemExit("Admin not seeded yet. Run _org_test_helpers.ensure_org_admin_token() first.")
    org_id = admin["organization_id"]

    pw_hash = bcrypt.hashpw(LIMITED_MGR_PASSWORD.encode(), bcrypt.gensalt()).decode()
    empty_perms = {
        "suppliers": {"create": False, "edit": False, "delete": False, "view": True},
        "accounting": {
            "create_expense": False, "edit_expense": False,
            "receive_payment": False, "view": True,
        },
        "products": {"view": True, "create": False, "edit": False, "delete": False},
    }
    payload = {
        "username": "test_limited_mgr",
        "full_name": "Limited Manager (Regression)",
        "password_hash": pw_hash,
        "role": "manager",
        "active": True,
        "branch_id": None,
        "organization_id": org_id,
        "permissions": empty_perms,
    }
    existing = db.users.find_one({"email": LIMITED_MGR_EMAIL})
    if existing:
        db.users.update_one({"email": LIMITED_MGR_EMAIL}, {"$set": payload})
        print(f"Updated existing limited manager (id={existing.get('id')}) in org={org_id}")
    else:
        payload.update({"id": str(uuid4()), "email": LIMITED_MGR_EMAIL})
        db.users.insert_one(payload)
        print(f"Inserted limited manager id={payload['id']} in org={org_id}")
    print(f"Email: {LIMITED_MGR_EMAIL}")
    print(f"Password: {LIMITED_MGR_PASSWORD}")


if __name__ == "__main__":
    main()

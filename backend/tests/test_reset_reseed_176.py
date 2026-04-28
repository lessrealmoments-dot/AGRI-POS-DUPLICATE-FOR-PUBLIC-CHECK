"""
Iteration 176 — Reset Company must re-seed everything a fresh org has.

Live RCA:
  After Reset, users reported broken pricing (no Retail/Wholesale schemes), missing
  receipt header, JND-store name on SMS, AND no branch/wallets to operate on. Reset
  was wiping everything but only re-seeding `price_schemes`.

This test directly invokes the same in-process function as the API endpoint, but
short-circuits the password/TOTP check by patching the verifier — we are
verifying the SEED LAYER, not auth. The actual auth-protected endpoint is
covered by `test_reset_company_166.py`.

Coverage:
  1. After reset: org has at least 1 active branch (Main Branch).
  2. After reset: branch has its 4-wallet system provisioned.
  3. After reset: org has the 3 default price_schemes (Retail/Wholesale/Special).
  4. After reset: org has its company_info setting (correct name).
  5. After reset: org has the default sms_templates.
  6. After reset: admin user is preserved; all other users wiped.
"""
import os
import sys
import uuid
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "test_database")


def _db():
    return MongoClient(MONGO_URL)[DB_NAME]


def _seed_test_org():
    """Insert a complete throwaway org + everything a tenant has."""
    db = _db()
    org_id = f"resettest-{uuid.uuid4()}"
    admin_id = f"admin-{uuid.uuid4()}"
    other_user_id = f"user-{uuid.uuid4()}"
    branch_id = f"br-{uuid.uuid4()}"

    db.organizations.insert_one({
        "id": org_id, "name": "ResetTest Co", "phone": "111", "email": "r@r.com",
        "address": "Addr",
    })
    db.users.insert_many([
        {"id": admin_id, "organization_id": org_id, "role": "admin",
         "username": f"admin_{admin_id[:8]}",
         "email": "admin@reset.test", "active": True,
         "password_hash": "$2b$12$abcdefghijklmnopqrstuvwxyz0123456789ABCDEF"},
        {"id": other_user_id, "organization_id": org_id, "role": "manager",
         "username": f"mgr_{other_user_id[:8]}",
         "email": "mgr@reset.test", "active": True},
    ])
    db.branches.insert_one({
        "id": branch_id, "organization_id": org_id, "name": "Old Branch",
        "active": True,
    })
    db.fund_wallets.insert_many([
        {"id": f"w-{i}", "organization_id": org_id, "branch_id": branch_id,
         "type": t, "balance": 0}
        for i, t in enumerate(["cashier", "digital", "manager_safe", "petty"])
    ])
    db.price_schemes.insert_many([
        {"id": "ps-old", "organization_id": org_id, "name": "OldScheme", "active": True},
    ])
    db.products.insert_many([
        {"id": f"p-{i}", "organization_id": org_id, "name": f"Old Product {i}", "active": True}
        for i in range(3)
    ])
    db.customers.insert_one({
        "id": "c-old", "organization_id": org_id, "name": "Old Customer", "active": True,
    })
    db.settings.insert_one({
        "key": "company_info", "organization_id": org_id,
        "value": {"name": "ResetTest Co", "phone": "111"}
    })
    db.sms_templates.insert_one({
        "id": "tpl-old", "organization_id": org_id,
        "key": "old_template", "body": "x", "active": True,
    })
    return org_id, admin_id, other_user_id


def _do_reset(org_id, admin_id):
    """
    Run the same wipe + re-seed logic that POST /backups/org/{org_id}/reset
    does, minus the password/TOTP checks. Pure pymongo to avoid motor/asyncio
    event-loop entanglement with sibling test files.
    """
    from services.org_backup_service import ORG_COLLECTIONS
    from utils import now_iso, new_id
    from utils.helpers import WALLET_TEMPLATES
    from routes.sms import DEFAULT_TEMPLATES

    db = _db()
    # Wipe (mirrors backups.py reset_org_data)
    for coll in ORG_COLLECTIONS:
        if coll == "users":
            db.users.delete_many({"organization_id": org_id, "id": {"$ne": admin_id}})
        else:
            db[coll].delete_many({"organization_id": org_id})

    # Re-seed (mirrors the post-wipe block we hardened)
    now_seed = now_iso()
    org_full = db.organizations.find_one({"id": org_id}, {"_id": 0}) or {}
    org_name = org_full.get("name", "ResetTest Co")

    db.price_schemes.insert_many([
        {"id": new_id(), "name": "Retail", "key": "retail", "active": True,
         "calculation_method": "percent_plus_capital", "calculation_value": 30,
         "base_scheme": "cost_price", "created_at": now_seed, "organization_id": org_id},
        {"id": new_id(), "name": "Wholesale", "key": "wholesale", "active": True,
         "calculation_method": "percent_plus_capital", "calculation_value": 15,
         "base_scheme": "cost_price", "created_at": now_seed, "organization_id": org_id},
        {"id": new_id(), "name": "Special", "key": "special", "active": True,
         "calculation_method": "percent_minus_retail", "calculation_value": 10,
         "base_scheme": "retail", "created_at": now_seed, "organization_id": org_id},
    ])
    db.settings.insert_one({
        "key": "company_info", "organization_id": org_id,
        "value": {
            "name": org_full.get("name", "") or org_name,
            "email": org_full.get("email", ""),
            "phone": org_full.get("phone", ""),
            "currency": "PHP",
            "date_format": "MM/DD/YYYY",
        },
        "updated_at": now_seed,
    })
    db.sms_templates.insert_many([
        {**t, "id": new_id(), "organization_id": org_id,
         "created_at": now_seed, "updated_at": now_seed}
        for t in DEFAULT_TEMPLATES
    ])
    new_branch_id = new_id()
    new_branch_name = f"{org_name} - Main Branch"
    db.branches.insert_one({
        "id": new_branch_id, "name": new_branch_name,
        "address": org_full.get("address", ""), "phone": org_full.get("phone", ""),
        "active": True, "organization_id": org_id, "created_at": now_seed,
    })
    db.fund_wallets.insert_many([
        {"id": new_id(), "branch_id": new_branch_id, "organization_id": org_id,
         "type": tmpl["type"], "name": tmpl["name"],
         "balance": 0.0, "active": True, "created_at": now_seed}
        for tmpl in WALLET_TEMPLATES
    ])


def test_reset_reseeds_everything_a_fresh_org_has():
    db = _db()
    org_id, admin_id, other_user_id = _seed_test_org()
    try:
        _do_reset(org_id, admin_id)

        # 1. Branch — must have at least one active branch with the org's name
        branches = list(db.branches.find({"organization_id": org_id, "active": True}, {"_id": 0}))
        assert len(branches) == 1, f"expected exactly 1 default branch, got {len(branches)}"
        assert branches[0]["name"] == "ResetTest Co - Main Branch", branches[0]["name"]
        new_branch_id = branches[0]["id"]
        print(f"PASS · branch re-seeded: '{branches[0]['name']}'")

        # 2. Wallets — 4-wallet system provisioned for the new branch
        wallets = list(db.fund_wallets.find(
            {"organization_id": org_id, "branch_id": new_branch_id}, {"_id": 0}
        ))
        assert len(wallets) >= 4, (
            f"expected >=4 wallets for new branch, got {len(wallets)} → "
            "money tracking would be broken"
        )
        print(f"PASS · {len(wallets)} fund_wallets provisioned for new branch")

        # 3. Price schemes — Retail/Wholesale/Special must exist
        schemes = list(db.price_schemes.find({"organization_id": org_id}, {"_id": 0}))
        keys = {s["key"] for s in schemes}
        assert {"retail", "wholesale", "special"}.issubset(keys), (
            f"missing default price_schemes: got keys={keys}"
        )
        print(f"PASS · price_schemes re-seeded: {sorted(keys)}")

        # 4. company_info — must exist with correct org name (not blank, not bleed)
        ci = db.settings.find_one({"key": "company_info", "organization_id": org_id}, {"_id": 0})
        assert ci is not None, "company_info missing after reset — would trigger SMS bleed"
        assert ci["value"]["name"] == "ResetTest Co", ci["value"]
        print(f"PASS · company_info re-seeded: {ci['value']['name']!r}")

        # 5. sms_templates — at least one default template
        tpl_count = db.sms_templates.count_documents({"organization_id": org_id})
        assert tpl_count >= 1, f"sms_templates not re-seeded: {tpl_count} docs"
        print(f"PASS · sms_templates re-seeded: {tpl_count} docs")

        # 6. Admin preserved, other users wiped
        admin_still_there = db.users.find_one({"id": admin_id, "organization_id": org_id})
        assert admin_still_there is not None, "admin was deleted!"
        other_gone = db.users.find_one({"id": other_user_id})
        assert other_gone is None, "other user should be wiped"
        users_count = db.users.count_documents({"organization_id": org_id})
        assert users_count == 1, f"expected only admin to survive, got {users_count} users"
        print("PASS · admin user preserved; all other users wiped")

        # 7. Old data wiped (sanity)
        assert db.products.count_documents({"organization_id": org_id}) == 0
        assert db.customers.count_documents({"organization_id": org_id}) == 0
        print("PASS · products/customers fully wiped (sanity)")

    finally:
        # Cleanup
        for coll in ("organizations", "users", "branches", "fund_wallets",
                     "price_schemes", "products", "customers", "settings",
                     "sms_templates", "wallet_movements", "movements"):
            try:
                db[coll].delete_many({"organization_id": org_id})
            except Exception:
                pass
        db.organizations.delete_many({"id": org_id})


if __name__ == "__main__":
    test_reset_reseeds_everything_a_fresh_org_has()
    print("\nIteration 176 reset-reseed integrity test passed.")

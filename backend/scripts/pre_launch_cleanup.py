"""
Pre-Launch Data Cleanup Script
================================
Clears ALL test company/tenant data from the database.

KEEPS:
  - Super admin user (is_super_admin: True)
  - platform_settings
  - app_downloads (uploaded APKs)

DELETES:
  - All organizations
  - All non-super-admin users
  - All branches, terminal sessions, pairing tokens
  - All tenant-scoped data (products, inventory, sales, customers, etc.)

Usage (run on your VPS):
  cd /var/www/agribooks/backend
  python3 scripts/pre_launch_cleanup.py
"""

import asyncio
import os
import sys
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv()

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME   = os.environ.get("DB_NAME", "test_database")

# All per-tenant collections — will be fully cleared
TENANT_COLLECTIONS = [
    'users', 'branches', 'products', 'inventory', 'customers',
    'invoices', 'sales', 'purchase_orders', 'suppliers', 'employees',
    'movements', 'fund_wallets', 'wallet_movements', 'fund_transfers',
    'expenses', 'branch_prices', 'branch_transfer_orders',
    'count_sheets', 'daily_closings', 'sales_log', 'returns',
    'discrepancy_log', 'notifications', 'view_tokens', 'safe_lots',
    'price_schemes', 'settings', 'system_settings', 'accounts_payable',
    'capital_changes', 'security_events', 'pin_attempt_log',
    'payables', 'receivables', 'product_vendors', 'invoice_edits',
    'inventory_corrections', 'inventory_adjustments', 'inventory_logs',
    'employee_advance_logs', 'safe_lot_usages',
    'branch_transfer_price_memory', 'branch_transfer_templates',
    'audits', 'upload_sessions', 'business_documents', 'doc_upload_tokens',
    'sms_queue', 'sms_templates', 'sms_settings', 'sms_inbox',
    'product_categories',
    # invoice_corrections collection
    'invoice_corrections',
    # sale reservations (partial release)
    'sale_reservations',
    # discount audit
    'discount_audit_log',
    # custom roles
    'custom_roles',
    # journal entries
    'journal_entries',
    # incident tickets
    'incident_tickets',
]

# Global collections scoped by org — clear everything
GLOBAL_ORG_COLLECTIONS = [
    'organizations',
    'terminal_sessions',
    'terminal_codes',
    'qr_pair_tokens',
    'sms_gateway_logs',
    'payment_submissions',
    'doc_codes',
    'qr_action_logs',
    'pin_attempt_log',
    'zreports',
]

# These are kept — global platform data
KEEP_COLLECTIONS = {'platform_settings', 'app_downloads'}


async def main():
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]

    print("\n" + "="*60)
    print("  AgriBooks — Pre-Launch Data Cleanup")
    print("="*60)
    print(f"  Database : {DB_NAME}")
    print(f"  Mongo URL: {MONGO_URL[:40]}...")
    print("="*60)

    # ── Preview what will be deleted ──────────────────────────────
    print("\n[1/3] Scanning database...\n")

    org_count  = await db.organizations.count_documents({})
    user_count = await db.users.count_documents({"is_super_admin": {"$ne": True}})
    super_admins = await db.users.find(
        {"is_super_admin": True}, {"_id": 0, "email": 1, "full_name": 1}
    ).to_list(10)

    print(f"  Organizations to delete : {org_count}")
    print(f"  Regular users to delete : {user_count}")
    print(f"  Super admin(s) to KEEP  : {len(super_admins)}")
    for sa in super_admins:
        print(f"    - {sa.get('email')} ({sa.get('full_name', 'N/A')})")

    print("\n  Tenant collection counts (sample):")
    sample_cols = ['products', 'inventory', 'customers', 'invoices', 'sales',
                   'purchase_orders', 'employees', 'movements', 'settings']
    for col_name in sample_cols:
        try:
            col = db[col_name]
            count = await col.count_documents({})
            if count > 0:
                print(f"    {col_name:<30} {count:>6} documents")
        except Exception:
            pass

    # ── Confirm ───────────────────────────────────────────────────
    print("\n" + "="*60)
    print("  WARNING: This will permanently delete ALL company data.")
    print("  This action CANNOT be undone.")
    print("="*60)
    confirm = input("\n  Type 'DELETE ALL DATA' to proceed: ").strip()
    if confirm != "DELETE ALL DATA":
        print("\n  Aborted. Nothing was changed.\n")
        sys.exit(0)

    # ── Execute cleanup ───────────────────────────────────────────
    print("\n[2/3] Deleting data...\n")
    total_deleted = 0

    # 1. Delete all organizations
    r = await db.organizations.delete_many({})
    print(f"  organizations            : {r.deleted_count} deleted")
    total_deleted += r.deleted_count

    # 2. Delete all non-super-admin users
    r = await db.users.delete_many({"is_super_admin": {"$ne": True}})
    print(f"  users (non-super-admin)  : {r.deleted_count} deleted")
    total_deleted += r.deleted_count

    # 3. Global org-scoped collections
    for col_name in GLOBAL_ORG_COLLECTIONS:
        try:
            r = await db[col_name].delete_many({})
            if r.deleted_count > 0:
                print(f"  {col_name:<30}: {r.deleted_count} deleted")
            total_deleted += r.deleted_count
        except Exception as e:
            print(f"  {col_name:<30}: skip ({e})")

    # 4. All tenant-scoped collections
    for col_name in TENANT_COLLECTIONS:
        if col_name in KEEP_COLLECTIONS:
            continue
        try:
            r = await db[col_name].delete_many({})
            if r.deleted_count > 0:
                print(f"  {col_name:<30}: {r.deleted_count} deleted")
            total_deleted += r.deleted_count
        except Exception as e:
            print(f"  {col_name:<30}: skip ({e})")

    # 5. Extra sweep — delete anything with an organization_id field
    #    (catches any collection we may have missed)
    all_cols = await db.list_collection_names()
    for col_name in all_cols:
        if col_name in KEEP_COLLECTIONS or col_name.startswith('system.'):
            continue
        try:
            # Check if it still has org-scoped documents
            sample = await db[col_name].find_one({"organization_id": {"$exists": True}})
            if sample:
                r = await db[col_name].delete_many({"organization_id": {"$exists": True}})
                if r.deleted_count > 0:
                    print(f"  {col_name:<30}: {r.deleted_count} remaining org docs deleted")
                    total_deleted += r.deleted_count
        except Exception:
            pass

    # ── Verify ────────────────────────────────────────────────────
    print("\n[3/3] Verifying...\n")

    orgs_remaining   = await db.organizations.count_documents({})
    users_remaining  = await db.users.count_documents({})
    super_remaining  = await db.users.count_documents({"is_super_admin": True})

    print(f"  Organizations remaining : {orgs_remaining}  (expected 0)")
    print(f"  Total users remaining   : {users_remaining}  (should be super admins only)")
    print(f"  Super admin(s) intact   : {super_remaining}")

    sa_check = await db.users.find(
        {"is_super_admin": True}, {"_id": 0, "email": 1}
    ).to_list(10)
    for sa in sa_check:
        print(f"    ✓ {sa.get('email')}")

    print(f"\n  Total documents deleted : {total_deleted}")

    if orgs_remaining == 0 and super_remaining > 0:
        print("\n  ✓ Database is clean and ready for launch.\n")
    else:
        print("\n  ⚠  Review the counts above — something may need attention.\n")

    client.close()


if __name__ == "__main__":
    asyncio.run(main())

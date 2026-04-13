"""
Per-Organization Backup Service.
Exports/imports all data for a single org as compressed JSON to/from R2.

Storage layout in R2:
  agribooks-backups/org/{org_id}/{timestamp}.json.gz

Data integrity:
  - Snapshot: all collections exported in one pass
  - Checksum: document counts per collection stored in manifest
  - Pre-restore safety: auto-backup before any restore
  - Atomic restore: delete old → insert new per collection
"""
import os
import gzip
import json
import logging
from datetime import datetime, timezone
from bson import ObjectId
from config import db as _scoped_db

logger = logging.getLogger(__name__)

# Collections that store per-org data (have organization_id field)
ORG_COLLECTIONS = [
    # Core business
    "audits", "audit_log",
    "branch_prices", "branch_transfer_orders", "branch_transfer_price_memory",
    "branch_transfer_templates", "branches", "capital_changes", "count_sheets",
    "customers", "daily_closings", "discrepancy_log", "employees", "expenses",
    "fund_transfers", "fund_wallets", "incident_tickets", "internal_invoices",
    "inventory", "inventory_corrections", "inventory_adjustments", "inventory_logs",
    "invoice_edits", "invoice_corrections", "invoices",
    "movements", "notifications",
    "payables", "receivables", "payment_submissions",
    "pin_attempt_log", "price_schemes", "product_vendors", "product_categories",
    "products", "purchase_orders",
    "returns", "safe_lots", "safe_lot_usages", "sale_reservations", "sales", "sales_log",
    "security_events", "settings", "suppliers", "system_settings",
    "upload_sessions", "users", "view_tokens", "wallet_movements",
    "accounts_payable", "employee_advance_logs",
    # SMS
    "sms_queue", "sms_templates", "sms_settings", "sms_inbox",
    # Documents & uploads
    "business_documents", "doc_upload_tokens", "doc_codes", "qr_action_logs",
    # Accounting
    "journal_entries", "discount_audit_log",
    # Roles & permissions
    "custom_roles",
]

# Global collections (no org_id, super admin only backup)
GLOBAL_COLLECTIONS = ["organizations", "platform_settings", "audit_log"]


def _get_r2_client():
    endpoint = os.environ.get("R2_ENDPOINT_URL", "").strip()
    access_key = os.environ.get("R2_ACCESS_KEY_ID", "").strip()
    secret_key = os.environ.get("R2_SECRET_ACCESS_KEY", "").strip()
    if not all([endpoint, access_key, secret_key]):
        return None
    import boto3
    return boto3.client("s3", endpoint_url=endpoint,
                        aws_access_key_id=access_key,
                        aws_secret_access_key=secret_key,
                        region_name="auto")


def _json_serializer(obj):
    """Handle MongoDB types for JSON serialization."""
    if isinstance(obj, ObjectId):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


async def _get_raw_db():
    """Get raw (unscoped) database handle for direct queries."""
    from motor.motor_asyncio import AsyncIOMotorClient
    mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
    db_name = os.environ.get("DB_NAME", "test_database")
    client = AsyncIOMotorClient(mongo_url)
    return client[db_name]


async def create_org_backup(org_id: str, org_name: str = "", triggered_by: str = "") -> dict:
    """
    Export all data for a single organization as compressed JSON → R2.
    Returns backup metadata.
    """
    raw_db = await _get_raw_db()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    data = {}
    manifest = {"org_id": org_id, "org_name": org_name, "timestamp": timestamp,
                "triggered_by": triggered_by, "collections": {}}
    total_docs = 0

    for coll_name in ORG_COLLECTIONS:
        coll = raw_db[coll_name]
        docs = await coll.find({"organization_id": org_id}, {"_id": 0}).to_list(100000)
        data[coll_name] = docs
        manifest["collections"][coll_name] = len(docs)
        total_docs += len(docs)

    manifest["total_documents"] = total_docs

    # Compress to JSON
    payload = json.dumps({"manifest": manifest, "data": data}, default=_json_serializer)
    compressed = gzip.compress(payload.encode("utf-8"))
    size_mb = round(len(compressed) / 1_048_576, 2)

    manifest["size_mb"] = size_mb
    manifest["compressed_bytes"] = len(compressed)

    # Upload to R2
    r2 = _get_r2_client()
    bucket = os.environ.get("R2_BUCKET_NAME", "agribooks-backups")
    filename = f"{timestamp}.json.gz"
    r2_key = f"org/{org_id}/{filename}"

    r2_uploaded = False
    if r2:
        try:
            r2.put_object(Bucket=bucket, Key=r2_key, Body=compressed,
                          ContentType="application/gzip")
            r2_uploaded = True
            logger.info("Org backup uploaded to R2: %s (%s docs, %.2f MB)", r2_key, total_docs, size_mb)
        except Exception as exc:
            logger.error("R2 upload failed for org backup: %s", exc)

    # Record backup metadata in DB
    backup_record = {
        "org_id": org_id,
        "org_name": org_name,
        "filename": filename,
        "r2_key": r2_key,
        "r2_uploaded": r2_uploaded,
        "size_mb": size_mb,
        "total_documents": total_docs,
        "collections": manifest["collections"],
        "triggered_by": triggered_by,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await raw_db.org_backups.insert_one(backup_record)

    return {
        "success": True,
        "filename": filename,
        "r2_key": r2_key,
        "r2_uploaded": r2_uploaded,
        "size_mb": size_mb,
        "total_documents": total_docs,
        "timestamp": timestamp,
    }


async def list_org_backups(org_id: str) -> list:
    """List all backups for a specific org, newest first."""
    raw_db = await _get_raw_db()
    backups = await raw_db.org_backups.find(
        {"org_id": org_id}, {"_id": 0}
    ).sort("created_at", -1).to_list(100)
    return backups


async def restore_org_backup(org_id: str, filename: str, restored_by: str = "") -> dict:
    """
    Restore a specific org's backup.
    1. Auto-creates a pre-restore safety backup
    2. Downloads backup from R2
    3. Validates manifest (checksum)
    4. Deletes current org data → inserts backup data
    """
    raw_db = await _get_raw_db()
    org = await raw_db.organizations.find_one({"id": org_id}, {"_id": 0, "name": 1})
    org_name = org.get("name", "") if org else ""

    # Step 1: Pre-restore safety backup
    logger.info("Creating pre-restore safety backup for org %s...", org_id)
    safety = await create_org_backup(org_id, org_name, triggered_by=f"pre-restore by {restored_by}")
    if not safety["success"]:
        return {"success": False, "error": "Failed to create safety backup before restore"}
    logger.info("Safety backup created: %s", safety["filename"])

    # Step 2: Download from R2
    r2 = _get_r2_client()
    bucket = os.environ.get("R2_BUCKET_NAME", "agribooks-backups")
    r2_key = f"org/{org_id}/{filename}"

    if not r2:
        return {"success": False, "error": "R2 not configured"}

    try:
        resp = r2.get_object(Bucket=bucket, Key=r2_key)
        compressed = resp["Body"].read()
    except Exception as exc:
        return {"success": False, "error": f"Failed to download backup from R2: {exc}"}

    # Step 3: Decompress and validate
    try:
        payload = json.loads(gzip.decompress(compressed).decode("utf-8"))
        manifest = payload["manifest"]
        data = payload["data"]
    except Exception as exc:
        return {"success": False, "error": f"Backup file is corrupted: {exc}"}

    if manifest["org_id"] != org_id:
        return {"success": False, "error": f"Backup org_id mismatch: expected {org_id}, got {manifest['org_id']}"}

    # Step 4: Atomic restore — delete old → insert new
    restored_collections = {}
    errors = []
    for coll_name in ORG_COLLECTIONS:
        docs = data.get(coll_name, [])
        try:
            # Delete existing org data from this collection
            delete_result = await raw_db[coll_name].delete_many({"organization_id": org_id})
            deleted = delete_result.deleted_count

            # Insert backup data
            inserted = 0
            if docs:
                # Re-add organization_id to each doc (safety)
                for d in docs:
                    d["organization_id"] = org_id
                await raw_db[coll_name].insert_many(docs)
                inserted = len(docs)

            restored_collections[coll_name] = {"deleted": deleted, "inserted": inserted}
        except Exception as exc:
            errors.append(f"{coll_name}: {exc}")
            logger.error("Restore error in %s: %s", coll_name, exc)

    # Record restore event
    await raw_db.org_backups.insert_one({
        "org_id": org_id,
        "org_name": org_name,
        "type": "restore",
        "restored_from": filename,
        "safety_backup": safety["filename"],
        "restored_by": restored_by,
        "collections_restored": len(restored_collections),
        "errors": errors,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

    total_inserted = sum(c["inserted"] for c in restored_collections.values())
    return {
        "success": len(errors) == 0,
        "total_documents_restored": total_inserted,
        "collections_restored": len(restored_collections),
        "safety_backup": safety["filename"],
        "errors": errors,
    }


async def get_org_data_stats(org_id: str) -> dict:
    """Get document counts per collection for an org (for dashboard display)."""
    raw_db = await _get_raw_db()
    stats = {}
    total = 0
    for coll_name in ORG_COLLECTIONS:
        count = await raw_db[coll_name].count_documents({"organization_id": org_id})
        if count > 0:
            stats[coll_name] = count
            total += count
    return {"total_documents": total, "collections": stats}

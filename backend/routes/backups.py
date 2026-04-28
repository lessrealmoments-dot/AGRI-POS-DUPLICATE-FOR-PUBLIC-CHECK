"""
Backup management routes — full-site + per-org backups.
Super admin: full site backup/restore, all org backups, schedule config.
Company owner: their org backup/restore only.
"""
from fastapi import APIRouter, Depends, HTTPException
from typing import Optional
from config import db
from utils import get_current_user, now_iso, new_id
from services.backup_service import create_backup, list_backups, restore_backup
from services.org_backup_service import (
    create_org_backup, list_org_backups, restore_org_backup, get_org_data_stats,
    ORG_COLLECTIONS
)
import os
import pyotp

router = APIRouter(prefix="/backups", tags=["Backups"])

DB_NAME = os.environ.get("DB_NAME", "test_database")


# ── Full Site Backup (Super Admin) ────────────────────────────────────────────

@router.post("/site/trigger")
async def trigger_site_backup(user=Depends(get_current_user)):
    """Manually trigger a full database backup. Super admin only."""
    if not user.get("is_super_admin") and user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin required")
    result = await create_backup(DB_NAME)
    return result


@router.get("/site/list")
async def list_site_backups(user=Depends(get_current_user)):
    """List all full-site backups."""
    if not user.get("is_super_admin") and user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin required")
    backups = await list_backups(DB_NAME)
    return {"backups": backups, "count": len(backups)}


@router.post("/site/restore/{filename}")
async def restore_site_backup(filename: str, user=Depends(get_current_user)):
    """Restore full database from a specific backup. Super admin only."""
    if not user.get("is_super_admin"):
        raise HTTPException(status_code=403, detail="Super admin required")
    result = await restore_backup(DB_NAME, filename)
    return result


# ── Per-Org Backup ────────────────────────────────────────────────────────────

@router.post("/org/{org_id}/trigger")
async def trigger_org_backup(org_id: str, user=Depends(get_current_user)):
    """Trigger a backup for a specific organization."""
    # Super admin can backup any org. Company admin can only backup their own.
    if not user.get("is_super_admin"):
        if user.get("organization_id") != org_id:
            raise HTTPException(status_code=403, detail="You can only backup your own organization")
        if user.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Admin required")

    from motor.motor_asyncio import AsyncIOMotorClient
    raw_db = AsyncIOMotorClient(os.environ.get("MONGO_URL"))[DB_NAME]
    org = await raw_db.organizations.find_one({"id": org_id}, {"_id": 0, "name": 1})
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    result = await create_org_backup(
        org_id,
        org_name=org.get("name", ""),
        triggered_by=user.get("full_name", user.get("username", ""))
    )
    return result


@router.get("/org/{org_id}/list")
async def list_org_backup_history(org_id: str, user=Depends(get_current_user)):
    """List all backups for a specific organization."""
    if not user.get("is_super_admin"):
        if user.get("organization_id") != org_id and user.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Access denied")

    backups = await list_org_backups(org_id)
    return {"backups": backups, "count": len(backups)}


@router.post("/org/{org_id}/restore/{filename}")
async def restore_org_from_backup(org_id: str, filename: str, data: dict = None, user=Depends(get_current_user)):
    """Restore an org from a specific backup point. Requires admin."""
    if data is None:
        data = {}
    if not user.get("is_super_admin"):
        if user.get("organization_id") != org_id:
            raise HTTPException(status_code=403, detail="You can only restore your own organization")
        if user.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Admin required")

    # PIN verification for dangerous operation
    pin = str(data.get("pin", ""))
    if pin:
        from routes.verify import verify_pin_for_action
        verifier = await verify_pin_for_action(pin, "backup_restore")
        if not verifier:
            raise HTTPException(status_code=403, detail="Invalid PIN")

    result = await restore_org_backup(
        org_id, filename,
        restored_by=user.get("full_name", user.get("username", ""))
    )
    return result


@router.get("/org/{org_id}/stats")
async def get_org_backup_stats(org_id: str, user=Depends(get_current_user)):
    """Get data size stats for an org (document counts per collection)."""
    if not user.get("is_super_admin"):
        if user.get("organization_id") != org_id and user.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Access denied")
    stats = await get_org_data_stats(org_id)
    return stats


# ── All Orgs Summary (Super Admin) ───────────────────────────────────────────

@router.get("/org-summary")
async def get_all_orgs_backup_summary(user=Depends(get_current_user)):
    """Get backup summary for all organizations. Super admin only."""
    if not user.get("is_super_admin") and user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin required")

    from motor.motor_asyncio import AsyncIOMotorClient
    raw_db = AsyncIOMotorClient(os.environ.get("MONGO_URL"))[DB_NAME]

    orgs = await raw_db.organizations.find({}, {"_id": 0, "id": 1, "name": 1, "plan": 1}).to_list(500)
    result = []
    for org in orgs:
        org_id = org["id"]
        # Get last backup
        last_backup = await raw_db.org_backups.find_one(
            {"org_id": org_id, "type": {"$exists": False}},
            {"_id": 0}, sort=[("created_at", -1)]
        )
        # Get doc count
        stats = await get_org_data_stats(org_id)
        result.append({
            "org_id": org_id,
            "org_name": org.get("name", ""),
            "plan": org.get("plan", ""),
            "total_documents": stats["total_documents"],
            "last_backup_at": last_backup.get("created_at") if last_backup else None,
            "last_backup_size_mb": last_backup.get("size_mb") if last_backup else None,
            "last_backup_docs": last_backup.get("total_documents") if last_backup else None,
        })

    return {"organizations": result, "total": len(result)}


# ── Backup Schedule Config ────────────────────────────────────────────────────

@router.get("/schedule")
async def get_backup_schedule(user=Depends(get_current_user)):
    """Get current backup schedule configuration."""
    if not user.get("is_super_admin") and user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin required")

    from motor.motor_asyncio import AsyncIOMotorClient
    raw_db = AsyncIOMotorClient(os.environ.get("MONGO_URL"))[DB_NAME]
    config = await raw_db.system_settings.find_one({"key": "backup_schedule"}, {"_id": 0})
    if not config:
        config = {
            "key": "backup_schedule",
            "site_backup_hours": [1],
            "org_backup_hours": [1, 7, 13, 19],
            "org_backup_enabled": False,
        }
    return config


@router.put("/schedule")
async def update_backup_schedule(data: dict, user=Depends(get_current_user)):
    """Update backup schedule. Restarts scheduler jobs."""
    if not user.get("is_super_admin") and user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin required")

    site_hours = data.get("site_backup_hours", [1])
    org_hours = data.get("org_backup_hours", [1, 7, 13, 19])
    org_enabled = data.get("org_backup_enabled", False)

    from motor.motor_asyncio import AsyncIOMotorClient
    raw_db = AsyncIOMotorClient(os.environ.get("MONGO_URL"))[DB_NAME]
    await raw_db.system_settings.update_one(
        {"key": "backup_schedule"},
        {"$set": {
            "key": "backup_schedule",
            "site_backup_hours": site_hours,
            "org_backup_hours": org_hours,
            "org_backup_enabled": org_enabled,
            "updated_at": now_iso(),
        }},
        upsert=True,
    )
    return {"message": "Schedule updated. Restart backend to apply new schedule.",
            "site_backup_hours": site_hours, "org_backup_hours": org_hours}


# ── Reset Company Data ────────────────────────────────────────────────────────

@router.post("/org/{org_id}/reset")
async def reset_org_data(org_id: str, data: dict, user=Depends(get_current_user)):
    """
    Wipe all company data back to zero.
    Keeps only the owner/admin account. Requires password + TOTP verification.
    Automatically creates a backup before deleting.
    """
    # Only company admin can reset their own org (or super admin)
    if not user.get("is_super_admin"):
        if user.get("organization_id") != org_id:
            raise HTTPException(status_code=403, detail="Access denied")
        if user.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Admin access required")

    password = data.get("password", "").strip()
    totp_code = str(data.get("totp_code", "")).strip()
    confirmation = data.get("confirmation", "").strip()

    if not password:
        raise HTTPException(status_code=400, detail="Password required")
    if not totp_code or len(totp_code) != 6:
        raise HTTPException(status_code=400, detail="6-digit TOTP code required")
    if not confirmation:
        raise HTTPException(status_code=400, detail="Confirmation text required")

    from motor.motor_asyncio import AsyncIOMotorClient
    from utils.auth import verify_password
    raw_db = AsyncIOMotorClient(os.environ.get("MONGO_URL"))[DB_NAME]

    # Verify confirmation text matches "[Org Name] Reset"
    org = await raw_db.organizations.find_one({"id": org_id}, {"_id": 0, "name": 1})
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    org_name = org.get("name", "")
    expected = f"{org_name} Reset"
    if confirmation != expected:
        raise HTTPException(status_code=400, detail=f"Confirmation must be exactly: {expected}")

    # Verify admin password
    admin_user = await raw_db.users.find_one({"id": user["id"]}, {"_id": 0})
    if not admin_user or not verify_password(password, admin_user.get("password_hash", "")):
        raise HTTPException(status_code=403, detail="Incorrect password")

    # Verify TOTP (check all TOTP-enabled users in the org)
    totp_users = await raw_db.users.find(
        {"organization_id": org_id, "totp_enabled": True, "totp_secret": {"$exists": True}},
        {"_id": 0, "totp_secret": 1}
    ).to_list(20)

    totp_verified = False
    for tu in totp_users:
        secret = tu.get("totp_secret")
        if secret:
            if pyotp.TOTP(secret).verify(totp_code, valid_window=1):
                totp_verified = True
                break

    if not totp_verified:
        raise HTTPException(status_code=403, detail="Invalid TOTP code")

    # Step 1: Auto-backup BEFORE wiping
    triggered_by = user.get("full_name") or user.get("username", "admin")
    backup = await create_org_backup(
        org_id,
        org_name=org_name,
        triggered_by=f"pre-reset by {triggered_by}"
    )

    # Step 2: Wipe all org data, keeping the admin user
    total_deleted = 0
    for coll_name in ORG_COLLECTIONS:
        if coll_name == "users":
            # Keep only the owner/admin, delete everyone else
            result = await raw_db.users.delete_many({
                "organization_id": org_id,
                "id": {"$ne": user["id"]}
            })
        else:
            result = await raw_db[coll_name].delete_many({"organization_id": org_id})
        total_deleted += result.deleted_count

    # Step 2b: Re-seed default price schemes (Retail / Wholesale / Special)
    # Reset must not leave the org without any pricing tiers — that breaks Sales/Terminal.
    now_seed = now_iso()
    default_schemes = [
        {"id": new_id(), "name": "Retail", "key": "retail", "description": "Standard retail price",
         "calculation_method": "percent_plus_capital", "calculation_value": 30,
         "base_scheme": "cost_price", "active": True, "created_at": now_seed, "organization_id": org_id},
        {"id": new_id(), "name": "Wholesale", "key": "wholesale", "description": "Wholesale price",
         "calculation_method": "percent_plus_capital", "calculation_value": 15,
         "base_scheme": "cost_price", "active": True, "created_at": now_seed, "organization_id": org_id},
        {"id": new_id(), "name": "Special", "key": "special", "description": "Special customer price",
         "calculation_method": "percent_minus_retail", "calculation_value": 10,
         "base_scheme": "retail", "active": True, "created_at": now_seed, "organization_id": org_id},
    ]
    await raw_db.price_schemes.insert_many(default_schemes)

    # Step 2c: Re-seed `company_info` from the immutable organizations row.
    # If we don't, the SMS/print signature lookup falls back to '' and (historically)
    # bled to another tenant's company_info — see RCA for "JND store" leak.
    org_full = await raw_db.organizations.find_one({"id": org_id}, {"_id": 0}) or {}
    await raw_db.settings.insert_one({
        "key": "company_info",
        "organization_id": org_id,
        "value": {
            "name": org_full.get("name", "") or org_name,
            "email": org_full.get("email", ""),
            "phone": org_full.get("phone", ""),
            "currency": "PHP",
            "date_format": "MM/DD/YYYY",
        },
        "updated_at": now_seed,
    })

    # Step 2d: Re-seed SMS templates so post-reset orgs can still send messages.
    # Templates live globally as "DEFAULT_TEMPLATES" in routes/sms.py — copy each
    # missing one into this org. Safe even if some still survived (idempotent).
    try:
        from routes.sms import DEFAULT_TEMPLATES
        existing_keys = {
            d["key"] async for d in raw_db.sms_templates.find(
                {"organization_id": org_id}, {"_id": 0, "key": 1}
            )
        }
        missing_tpls = [
            {**t, "id": new_id(), "organization_id": org_id,
             "created_at": now_seed, "updated_at": now_seed}
            for t in DEFAULT_TEMPLATES if t["key"] not in existing_keys
        ]
        if missing_tpls:
            await raw_db.sms_templates.insert_many(missing_tpls)
    except Exception as tpl_err:
        import logging
        logging.getLogger("reset").error(f"sms_templates re-seed failed: {tpl_err}")

    # Step 3: Log the reset event
    await raw_db.audit_log.insert_one({
        "organization_id": org_id,
        "action": "company_reset",
        "performed_by": triggered_by,
        "user_id": user["id"],
        "backup_filename": backup.get("filename"),
        "backup_size_mb": backup.get("size_mb"),
        "total_deleted": total_deleted,
        "created_at": now_iso(),
    })

    return {
        "success": True,
        "backup": backup,
        "total_deleted": total_deleted,
        "message": "All company data has been reset. Owner account preserved.",
    }


# ── Download Backup (Presigned URL) ──────────────────────────────────────────

@router.get("/org/{org_id}/download/{filename}")
async def download_org_backup(org_id: str, filename: str, user=Depends(get_current_user)):
    """Generate a 1-hour presigned download URL for an org backup file stored in R2."""
    if not user.get("is_super_admin"):
        if user.get("organization_id") != org_id:
            raise HTTPException(status_code=403, detail="Access denied")
        if user.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Admin required")

    endpoint = os.environ.get("R2_ENDPOINT_URL", "").strip()
    access_key = os.environ.get("R2_ACCESS_KEY_ID", "").strip()
    secret_key = os.environ.get("R2_SECRET_ACCESS_KEY", "").strip()
    bucket = os.environ.get("R2_BUCKET_NAME", "agribooks-backups")

    if not all([endpoint, access_key, secret_key]):
        raise HTTPException(status_code=503, detail="R2 storage not configured")

    import boto3
    r2 = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="auto",
    )
    r2_key = f"org/{org_id}/{filename}"
    try:
        url = r2.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": r2_key},
            ExpiresIn=3600,
        )
        return {"download_url": url, "filename": filename, "expires_in_seconds": 3600}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to generate download URL: {exc}")


# ── Legacy endpoints (backward compat) ───────────────────────────────────────

@router.post("/trigger")
async def trigger_backup_legacy(user=Depends(get_current_user)):
    """Legacy: trigger full-site backup."""
    return await trigger_site_backup(user=user)


@router.get("")
async def list_backups_legacy(user=Depends(get_current_user)):
    """Legacy: list full-site backups."""
    return await list_site_backups(user=user)


@router.post("/restore/{filename}")
async def restore_backup_legacy(filename: str, user=Depends(get_current_user)):
    """Legacy: restore full-site backup."""
    return await restore_site_backup(filename, user=user)

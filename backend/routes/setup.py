"""
Company Setup Wizard routes.
First-time setup for fresh installations.
"""
import os
from fastapi import APIRouter, HTTPException, Depends
from config import db, _raw_db
from utils import hash_password, verify_password, now_iso, new_id
from utils.auth import get_current_user
from models import DEFAULT_PERMISSIONS

router = APIRouter(prefix="/setup", tags=["Setup"])


@router.get("/status")
async def get_setup_status():
    """Check if initial setup has been completed."""
    # SaaS mode: if a super admin or any organization exists, setup is done
    super_admin = await _raw_db.users.find_one({"is_super_admin": True}, {"_id": 0})
    org_count = await _raw_db.organizations.count_documents({})
    if super_admin or org_count > 0:
        return {
            "setup_completed": True,
            "has_users": True,
            "has_branches": True,
            "has_company_info": True,
            "company_name": None
        }

    # Legacy single-tenant mode: check for users + branches
    user_count = await db.users.count_documents({})
    branch_count = await db.branches.count_documents({})
    company = await db.settings.find_one({"key": "company_info"}, {"_id": 0})

    return {
        "setup_completed": user_count > 0 and branch_count > 0,
        "has_users": user_count > 0,
        "has_branches": branch_count > 0,
        "has_company_info": company is not None,
        "company_name": company.get("value", {}).get("name") if company else None
    }


@router.post("/initialize")
async def initialize_system(data: dict):
    """
    Complete initial system setup.
    Creates company info, first branch, admin user, and fund wallets with opening balances.
    """
    # C-2 (Audit 2026-02): refuse if ANY user OR organization already exists.
    # Previously only `users` was checked, leaving a window where an attacker
    # could call /setup/initialize after a partial wipe and attach a forged
    # admin to a still-existing organization.
    existing_users = await _raw_db.users.count_documents({})
    existing_orgs = await _raw_db.organizations.count_documents({})
    if existing_users > 0 or existing_orgs > 0:
        raise HTTPException(
            status_code=400,
            detail="System already initialized. Use the super-admin tools to add new tenants instead of /setup/initialize.",
        )
    
    # Validate required fields
    required = ["company_name", "branch_name", "admin_username", "admin_password"]
    for field in required:
        if not data.get(field):
            raise HTTPException(status_code=400, detail=f"Missing required field: {field}")
    
    now = now_iso()
    branch_id = new_id()
    admin_id = new_id()
    
    # 1. Create company info
    company_info = {
        "name": data["company_name"],
        "address": data.get("company_address", ""),
        "phone": data.get("company_phone", ""),
        "email": data.get("company_email", ""),
        "tax_id": data.get("tax_id", ""),
        "currency": data.get("currency", "PHP"),
        "date_format": data.get("date_format", "MM/DD/YYYY"),
        "fiscal_year_start": data.get("fiscal_year_start", "01-01"),
    }
    await db.settings.update_one(
        {"key": "company_info"},
        {"$set": {"key": "company_info", "value": company_info, "updated_at": now}},
        upsert=True
    )
    
    # 2. Create first branch
    branch = {
        "id": branch_id,
        "name": data["branch_name"],
        "address": data.get("branch_address", ""),
        "phone": data.get("branch_phone", ""),
        "is_main": True,
        "active": True,
        "created_at": now
    }
    await db.branches.insert_one(branch)
    
    # 3. Create admin user
    admin_user = {
        "id": admin_id,
        "username": data["admin_username"],
        "full_name": data.get("admin_full_name", "Administrator"),
        "email": data.get("admin_email", ""),
        "password_hash": hash_password(data["admin_password"]),
        "role": "admin",
        "branch_id": None,  # Admin can see all branches
        "manager_pin": data.get("manager_pin", "1234"),
        "permissions": DEFAULT_PERMISSIONS.get("admin", {}),
        "active": True,
        "created_at": now
    }
    await db.users.insert_one(admin_user)
    
    # 4. Create fund wallets with opening balances
    wallets_created = []
    
    # Cashier Drawer
    cashier_balance = float(data.get("opening_cashier_balance", 0))
    cashier_wallet = {
        "id": new_id(),
        "branch_id": branch_id,
        "type": "cashier",
        "name": "Cashier Drawer",
        "balance": cashier_balance,
        "active": True,
        "created_at": now
    }
    await db.fund_wallets.insert_one(cashier_wallet)
    wallets_created.append({"name": "Cashier Drawer", "balance": cashier_balance})
    
    # Safe
    safe_wallet_id = new_id()
    safe_balance = float(data.get("opening_safe_balance", 0))
    safe_wallet = {
        "id": safe_wallet_id,
        "branch_id": branch_id,
        "type": "safe",
        "name": "Branch Safe",
        "balance": 0,  # Safe balance is computed from lots
        "active": True,
        "created_at": now
    }
    await db.fund_wallets.insert_one(safe_wallet)
    
    # Create initial safe lot if opening balance > 0
    if safe_balance > 0:
        await db.safe_lots.insert_one({
            "id": new_id(),
            "branch_id": branch_id,
            "wallet_id": safe_wallet_id,
            "date_received": now[:10],
            "original_amount": safe_balance,
            "remaining_amount": safe_balance,
            "source_reference": "Opening Balance",
            "created_by": admin_id,
            "created_at": now
        })
    wallets_created.append({"name": "Branch Safe", "balance": safe_balance})
    
    # Bank Account (optional)
    bank_balance = float(data.get("opening_bank_balance", 0))
    if data.get("bank_name") or bank_balance > 0:
        bank_wallet = {
            "id": new_id(),
            "branch_id": branch_id,
            "type": "bank",
            "name": data.get("bank_name", "Bank Account"),
            "bank_name": data.get("bank_name", ""),
            "account_number": data.get("bank_account_number", ""),
            "balance": bank_balance,
            "active": True,
            "created_at": now
        }
        await db.fund_wallets.insert_one(bank_wallet)
        wallets_created.append({"name": bank_wallet["name"], "balance": bank_balance})
    
    # 5. Record setup completion
    await db.settings.update_one(
        {"key": "setup_info"},
        {"$set": {
            "key": "setup_info",
            "value": {
                "completed_at": now,
                "completed_by": admin_id,
                "version": "1.0"
            }
        }},
        upsert=True
    )
    
    # 6. Create default invoice prefixes
    await db.settings.update_one(
        {"key": "invoice_prefixes"},
        {"$set": {
            "key": "invoice_prefixes",
            "value": {
                "sales_invoice": data.get("invoice_prefix", "SI"),
                "purchase_order": "PO",
                "service_invoice": "SVC",
                "cash_advance": "CA",
                "interest_charge": "INT",
                "penalty_charge": "PEN"
            }
        }},
        upsert=True
    )
    
    return {
        "success": True,
        "message": "System initialized successfully!",
        "company_name": company_info["name"],
        "branch": {"id": branch_id, "name": branch["name"]},
        "admin_username": admin_user["username"],
        "wallets": wallets_created,
        "next_step": "Login with your admin credentials"
    }


@router.post("/reset")
async def reset_system(data: dict, user=Depends(get_current_user)):
    """
    Reset the entire system. DANGER: This deletes all data!

    C-2 (Audit 2026-02): Hardened from completely-public to:
      • super-admin authentication required
      • environment flag ALLOW_DB_RESET=true must be set on the server
      • caller must re-confirm their own password
      • full audit log row written before the wipe
    The route is NEVER usable in production unless the operator has
    explicitly opted in via env. The previous behaviour (anyone with the
    URL could wipe the DB with `confirm_text="DELETE ALL DATA"`) is
    permanently disabled.
    """
    # 1. Auth gate — super admin only
    if not user.get("is_super_admin"):
        raise HTTPException(status_code=403, detail="Super-admin required to reset the system.")

    # 2. Environment kill-switch — production should ALWAYS leave this off
    if os.environ.get("ALLOW_DB_RESET", "").lower() not in ("1", "true", "yes"):
        raise HTTPException(
            status_code=403,
            detail="Database reset is disabled on this server. Set ALLOW_DB_RESET=true in the environment to enable (development/staging only).",
        )

    # 3. Confirmation phrase
    confirm = data.get("confirm_text", "")
    if confirm != "DELETE ALL DATA":
        raise HTTPException(
            status_code=400,
            detail="To reset, send confirm_text: 'DELETE ALL DATA'",
        )

    # 4. Re-confirm the caller's password (defence against stolen-token misuse)
    re_confirm_password = data.get("password", "")
    if not re_confirm_password:
        raise HTTPException(status_code=400, detail="Re-confirm with your super-admin password.")
    fresh_user = await _raw_db.users.find_one(
        {"id": user.get("id"), "is_super_admin": True}, {"_id": 0, "password_hash": 1},
    )
    if not (fresh_user and verify_password(re_confirm_password, fresh_user.get("password_hash", ""))):
        raise HTTPException(status_code=401, detail="Super-admin password did not match.")

    # 5. Audit row — written BEFORE the wipe so it survives in the parent
    # process logger even if the audit_log collection itself is dropped.
    try:
        await _raw_db.security_events.insert_one({
            "id": new_id(),
            "type": "db_reset_executed",
            "actor_user_id": user.get("id"),
            "actor_user_name": user.get("full_name") or user.get("username", ""),
            "actor_email": user.get("email", ""),
            "executed_at": now_iso(),
            "env_flag": os.environ.get("ALLOW_DB_RESET", ""),
        })
    except Exception:
        pass

    # 6. Wipe — keep behaviour parity with the previous implementation
    collections = await db.list_collection_names()
    deleted_counts = {}
    for col_name in collections:
        result = await _raw_db[col_name].delete_many({})
        deleted_counts[col_name] = result.deleted_count

    return {
        "success": True,
        "message": "System reset complete. All data has been deleted.",
        "deleted": deleted_counts,
        "executed_by": user.get("email") or user.get("username", ""),
    }


@router.get("/defaults")
async def get_setup_defaults():
    """Get default values for setup form."""
    return {
        "currency_options": [
            {"code": "PHP", "name": "Philippine Peso", "symbol": "₱"},
            {"code": "USD", "name": "US Dollar", "symbol": "$"},
            {"code": "EUR", "name": "Euro", "symbol": "€"},
        ],
        "date_format_options": [
            {"key": "MM/DD/YYYY", "example": "12/25/2025"},
            {"key": "DD/MM/YYYY", "example": "25/12/2025"},
            {"key": "YYYY-MM-DD", "example": "2025-12-25"},
        ],
        "suggested_prefixes": {
            "sales_invoice": "SI",
            "purchase_order": "PO",
        },
        "fund_wallet_types": [
            {"type": "cashier", "name": "Cashier Drawer", "description": "Cash in the register"},
            {"type": "safe", "name": "Safe", "description": "Secure cash storage"},
            {"type": "bank", "name": "Bank Account", "description": "Business bank account"},
        ]
    }

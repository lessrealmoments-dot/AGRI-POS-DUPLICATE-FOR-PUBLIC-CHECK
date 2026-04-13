"""
Super Admin routes: platform-level management across all organizations.
Access: janmarkeahig@gmail.com only (is_super_admin=True).
"""
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from config import _raw_db
from utils import now_iso, new_id
from utils.auth import get_current_user
from routes.organizations import PLAN_LIMITS, PLAN_PRICING, get_effective_plan
from datetime import datetime, timezone, timedelta

router = APIRouter(prefix="/superadmin", tags=["Super Admin"])


def require_super_admin(user=Depends(get_current_user)):
    if not user.get("is_super_admin"):
        raise HTTPException(status_code=403, detail="Super admin access required")
    return user


# ---------------------------------------------------------------------------
# Platform Stats
# ---------------------------------------------------------------------------
@router.get("/stats")
async def platform_stats(user=Depends(require_super_admin)):
    now = datetime.now(timezone.utc)
    total_orgs = await _raw_db.organizations.count_documents({})
    trial_orgs = await _raw_db.organizations.count_documents({"plan": "trial"})
    active_orgs = await _raw_db.organizations.count_documents({"subscription_status": "active"})
    founders_orgs = await _raw_db.organizations.count_documents({"plan": "founders"})
    suspended_orgs = await _raw_db.organizations.count_documents({"plan": "suspended"})
    total_users = await _raw_db.users.count_documents({"active": True, "is_super_admin": {"$ne": True}})

    # Expiring trials in next 7 days
    week_out = (now + timedelta(days=7)).isoformat()
    expiring_soon = await _raw_db.organizations.count_documents({
        "plan": "trial",
        "trial_ends_at": {"$lt": week_out, "$gt": now.isoformat()}
    })

    plan_counts = {}
    for plan in ["basic", "standard", "pro", "founders"]:
        plan_counts[plan] = await _raw_db.organizations.count_documents({"plan": plan})

    return {
        "total_organizations": total_orgs,
        "trial": trial_orgs,
        "active": active_orgs,
        "founders": founders_orgs,
        "suspended": suspended_orgs,
        "total_users": total_users,
        "expiring_soon": expiring_soon,
        "by_plan": plan_counts,
    }


# ---------------------------------------------------------------------------
# Organizations
# ---------------------------------------------------------------------------
@router.get("/organizations")
async def list_organizations(user=Depends(require_super_admin)):
    orgs = await _raw_db.organizations.find({}, {"_id": 0}).to_list(500)
    result = []
    for org in orgs:
        branch_count = await _raw_db.branches.count_documents({"organization_id": org["id"], "active": True})
        user_count = await _raw_db.users.count_documents({"organization_id": org["id"], "active": True})
        org["branch_count"] = branch_count
        org["user_count"] = user_count
        org["effective_plan"] = get_effective_plan(org)
        result.append(org)
    return result


@router.get("/organizations/{org_id}")
async def get_organization(org_id: str, user=Depends(require_super_admin)):
    org = await _raw_db.organizations.find_one({"id": org_id}, {"_id": 0})
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    branch_count = await _raw_db.branches.count_documents({"organization_id": org_id, "active": True})
    user_count = await _raw_db.users.count_documents({"organization_id": org_id, "active": True})
    org["branch_count"] = branch_count
    org["user_count"] = user_count
    org["effective_plan"] = get_effective_plan(org)
    return org


@router.get("/organizations/{org_id}/branches")
async def get_org_branches(org_id: str, user=Depends(require_super_admin)):
    """List all branches for an organization."""
    branches = await _raw_db.branches.find(
        {"organization_id": org_id},
        {"_id": 0, "id": 1, "name": 1, "address": 1, "active": 1, "is_main": 1, "created_at": 1}
    ).to_list(100)
    return branches


@router.get("/organizations/{org_id}/users")
async def get_org_users(org_id: str, user=Depends(require_super_admin)):
    """List all users for an organization."""
    users = await _raw_db.users.find(
        {"organization_id": org_id},
        {"_id": 0, "id": 1, "username": 1, "full_name": 1, "email": 1, "role": 1, "active": 1, "created_at": 1}
    ).to_list(100)
    return users


@router.put("/organizations/{org_id}/subscription")
async def update_subscription(org_id: str, data: dict, user=Depends(require_super_admin)):
    """Update an organization's plan/subscription."""
    plan = data.get("plan")
    valid_plans = ["trial", "basic", "standard", "pro", "founders", "suspended"]
    if plan and plan not in valid_plans:
        raise HTTPException(status_code=400, detail=f"Invalid plan. Choose: {valid_plans}")

    existing = await _raw_db.organizations.find_one({"id": org_id}, {"_id": 0})
    if not existing:
        raise HTTPException(status_code=404, detail="Organization not found")

    update = {"updated_at": now_iso()}

    effective_plan = plan or existing.get("plan", "basic")
    if plan:
        update["plan"] = plan
        if plan == "founders":
            update["subscription_status"] = "active"
            update["subscription_expires_at"] = None   # Founders never expires
            update["max_branches"] = 0                 # Unlimited
            update["max_users"] = 0
            update["extra_branches"] = 0
        elif plan == "suspended":
            update["subscription_status"] = "suspended"
        elif plan == "trial":
            update["subscription_status"] = "trial"
        else:
            update["subscription_status"] = "active"

    # Extra branches (skip for founders — unlimited)
    if effective_plan != "founders":
        extra = int(data.get("extra_branches", existing.get("extra_branches", 0)))
        base_limit = PLAN_LIMITS.get(effective_plan, PLAN_LIMITS["basic"])["max_branches"]
        update["max_branches"] = base_limit + extra
        update["extra_branches"] = extra
        update["max_users"] = PLAN_LIMITS.get(effective_plan, PLAN_LIMITS["basic"])["max_users"]

    # Trial extension
    if "trial_days" in data and int(data.get("trial_days", 0)) > 0:
        trial_end = (datetime.now(timezone.utc) + timedelta(days=int(data["trial_days"]))).isoformat()
        update["trial_ends_at"] = trial_end
        update["plan"] = "trial"
        update["subscription_status"] = "trial"

    # Subscription expiry for paid plans
    # If admin provides a date, use it. Otherwise auto-set 30 days when activating a paid plan.
    if "subscription_expires_at" in data:
        update["subscription_expires_at"] = data["subscription_expires_at"] or None
    elif plan in ("basic", "standard", "pro") and not existing.get("subscription_expires_at"):
        # Auto-set 30 days from today as default when first activating a paid plan
        auto_expiry = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        update["subscription_expires_at"] = auto_expiry

    if "notes" in data:
        update["admin_notes"] = data["notes"]

    await _raw_db.organizations.update_one({"id": org_id}, {"$set": update})
    updated = await _raw_db.organizations.find_one({"id": org_id}, {"_id": 0})

    # Email notification
    if updated and updated.get("owner_email"):
        import asyncio
        from services.email_service import send_subscription_activated
        new_plan = update.get("plan", updated.get("plan", "basic"))
        if new_plan not in ("suspended", "trial"):
            asyncio.create_task(send_subscription_activated(
                updated["owner_email"], updated["name"], new_plan,
                data.get("expires_display", "")
            ))

    updated["branch_count"] = await _raw_db.branches.count_documents({"organization_id": org_id, "active": True})
    updated["user_count"] = await _raw_db.users.count_documents({"organization_id": org_id, "active": True})
    updated["effective_plan"] = get_effective_plan(updated)
    return updated


@router.put("/organizations/{org_id}/info")
async def update_org_info(org_id: str, data: dict, user=Depends(require_super_admin)):
    """Update organization name/contact info."""
    allowed = ["name", "owner_email", "phone", "address", "admin_notes"]
    update = {k: v for k, v in data.items() if k in allowed}
    update["updated_at"] = now_iso()
    await _raw_db.organizations.update_one({"id": org_id}, {"$set": update})
    return await _raw_db.organizations.find_one({"id": org_id}, {"_id": 0})


# ---------------------------------------------------------------------------
# Feature Flags Management
# ---------------------------------------------------------------------------
@router.get("/settings/features")
async def get_feature_flags(user=Depends(require_super_admin)):
    """Get current feature flags for all plans."""
    from routes.organizations import FEATURE_DEFINITIONS, DEFAULT_FEATURE_FLAGS, get_live_feature_flags
    flags = await get_live_feature_flags()
    return {
        "feature_definitions": FEATURE_DEFINITIONS,
        "flags": {
            "basic": flags.get("basic", DEFAULT_FEATURE_FLAGS["basic"]),
            "standard": flags.get("standard", DEFAULT_FEATURE_FLAGS["standard"]),
            "pro": flags.get("pro", DEFAULT_FEATURE_FLAGS["pro"]),
        },
        "last_updated": (await _raw_db.platform_settings.find_one(
            {"key": "feature_flags"}, {"_id": 0, "updated_at": 1}
        ) or {}).get("updated_at"),
    }


@router.put("/settings/features")
async def update_feature_flags(data: dict, user=Depends(require_super_admin)):
    """Save updated feature flags. Trial always mirrors Pro automatically."""
    from routes.organizations import DEFAULT_FEATURE_FLAGS
    flags = data.get("flags", {})
    # Trial always = Pro
    flags["trial"] = flags.get("pro", DEFAULT_FEATURE_FLAGS["pro"])
    # Grace period = Pro (full access during grace)
    flags["grace_period"] = flags.get("pro", DEFAULT_FEATURE_FLAGS["pro"])
    flags["expired"] = flags.get("basic", DEFAULT_FEATURE_FLAGS["basic"])

    await _raw_db.platform_settings.update_one(
        {"key": "feature_flags"},
        {"$set": {
            "key": "feature_flags",
            "value": flags,
            "updated_at": now_iso(),
            "updated_by": user.get("email"),
        }},
        upsert=True
    )
    return {"success": True, "flags": flags, "message": "Feature flags updated and live immediately."}
@router.get("/settings/payment")
async def get_payment_settings(user=Depends(require_super_admin)):
    """Get platform payment configuration."""
    setting = await _raw_db.platform_settings.find_one({"key": "payment_methods"}, {"_id": 0})
    if not setting:
        return {"key": "payment_methods", "value": {}}
    # Strip QR base64 preview from response for speed — frontend requests full when needed
    return setting


@router.put("/settings/payment")
async def update_payment_settings(data: dict, user=Depends(require_super_admin)):
    """Update platform payment configuration (QR codes, account numbers)."""
    now = now_iso()
    await _raw_db.platform_settings.update_one(
        {"key": "payment_methods"},
        {"$set": {
            "key": "payment_methods",
            "value": data.get("value", {}),
            "updated_at": now,
            "updated_by": user.get("email"),
        }},
        upsert=True
    )
    result = await _raw_db.platform_settings.find_one({"key": "payment_methods"}, {"_id": 0})
    return result


# ---------------------------------------------------------------------------
# Public: Payment info for Upgrade page
# ---------------------------------------------------------------------------
@router.get("/public/payment-info")
async def get_public_payment_info():
    """Public endpoint: returns payment methods for the upgrade page."""
    setting = await _raw_db.platform_settings.find_one({"key": "payment_methods"}, {"_id": 0})
    if not setting:
        return {"configured": False, "methods": {}}
    return {"configured": True, "methods": setting.get("value", {})}


# ---------------------------------------------------------------------------
# Approve / Reject Subscriptions (with email notification)
# ---------------------------------------------------------------------------
@router.post("/organizations/{org_id}/approve-subscription")
async def approve_subscription(org_id: str, data: dict, user=Depends(require_super_admin)):
    """
    Approve a pending subscription: activate the plan, send approval email to customer.
    Body: { plan, extra_branches, subscription_expires_at (optional), note }
    """
    import asyncio
    from services.email_service import send_subscription_activated

    org = await _raw_db.organizations.find_one({"id": org_id}, {"_id": 0})
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    plan = data.get("plan", org.get("plan", "basic"))
    extra = int(data.get("extra_branches", 0))
    base_limit = PLAN_LIMITS.get(plan, PLAN_LIMITS["basic"])["max_branches"]

    auto_expiry = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    expires_at = data.get("subscription_expires_at") or auto_expiry

    update = {
        "plan": plan,
        "subscription_status": "active",
        "subscription_expires_at": expires_at if plan not in ("founders",) else None,
        "max_branches": 0 if plan == "founders" else base_limit + extra,
        "max_users": 0 if plan == "founders" else PLAN_LIMITS.get(plan, PLAN_LIMITS["basic"])["max_users"],
        "extra_branches": extra,
        "admin_notes": data.get("note", ""),
        "approved_at": now_iso(),
        "approved_by": user.get("email", ""),
        "rejection_reason": None,
        "updated_at": now_iso(),
    }
    await _raw_db.organizations.update_one({"id": org_id}, {"$set": update})

    # Mark any pending payment submissions as approved
    await _raw_db.payment_submissions.update_many(
        {"organization_id": org_id, "status": "pending"},
        {"$set": {"status": "approved", "reviewed_at": now_iso(), "reviewed_by": user.get("email", "")}}
    )

    owner_email = org.get("owner_email", "")
    if owner_email:
        expires_display = expires_at[:10] if expires_at else ""
        asyncio.create_task(send_subscription_activated(owner_email, org["name"], plan, expires_display))

    updated = await _raw_db.organizations.find_one({"id": org_id}, {"_id": 0})
    return updated


@router.post("/organizations/{org_id}/reject-subscription")
async def reject_subscription(org_id: str, data: dict, user=Depends(require_super_admin)):
    """
    Reject a pending subscription payment. Sends rejection email with reason.
    Body: { reason, plan (the plan they were trying to get) }
    """
    import asyncio
    from services.email_service import send_subscription_rejected

    org = await _raw_db.organizations.find_one({"id": org_id}, {"_id": 0})
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    reason = data.get("reason", "").strip()
    if not reason:
        raise HTTPException(status_code=400, detail="Rejection reason is required")

    plan = data.get("plan", org.get("plan", "trial"))

    await _raw_db.organizations.update_one({"id": org_id}, {"$set": {
        "rejection_reason": reason,
        "rejected_at": now_iso(),
        "rejected_by": user.get("email", ""),
        "updated_at": now_iso(),
    }})

    # Mark pending submissions as rejected
    await _raw_db.payment_submissions.update_many(
        {"organization_id": org_id, "status": "pending"},
        {"$set": {"status": "rejected", "reviewed_at": now_iso(), "reviewed_by": user.get("email", ""), "rejection_reason": reason}}
    )

    owner_email = org.get("owner_email", "")
    if owner_email:
        asyncio.create_task(send_subscription_rejected(owner_email, org["name"], plan, reason))

    return {"message": f"Subscription rejected. Email sent to {owner_email}."}


# ---------------------------------------------------------------------------
# Payment Proof Submissions
# ---------------------------------------------------------------------------
@router.get("/payment-submissions")
async def list_payment_submissions(
    status: str = "pending",
    user=Depends(require_super_admin)
):
    """List payment proof submissions from customers."""
    query = {}
    if status and status != "all":
        query["status"] = status

    subs = await _raw_db.payment_submissions.find(query, {"_id": 0}).sort("submitted_at", -1).to_list(100)

    # Enrich with org info
    for s in subs:
        org = await _raw_db.organizations.find_one({"id": s.get("organization_id")}, {"_id": 0, "name": 1, "owner_email": 1, "plan": 1, "subscription_status": 1})
        if org:
            s["org_name"] = org.get("name", "")
            s["owner_email"] = org.get("owner_email", "")
            s["current_plan"] = org.get("plan", "")
            s["subscription_status"] = org.get("subscription_status", "")

    return {"submissions": subs, "total": len(subs)}



# ---------------------------------------------------------------------------
# Data Migration: Fix Partial Invoices with Wrong fund_source
# ---------------------------------------------------------------------------
@router.post("/migrations/fix-partial-fund-source")
async def fix_partial_fund_source(data: dict = None, user=Depends(require_super_admin)):
    """
    One-time migration: fix partial invoices that were incorrectly assigned
    fund_source='digital' due to is_digital_payment('Partial') returning True.

    For each corrupted invoice:
    1. Set fund_source → 'cashier'
    2. Remove incorrect digital_platform and receipt_status fields
    3. Move cash (amount_paid) from digital wallet → cashier wallet
    4. Log every change for audit trail

    Safe to run multiple times (idempotent).
    """
    dry_run = (data or {}).get("dry_run", False)
    fixes = []

    # Find all corrupted partial invoices across the entire database
    corrupted = await _raw_db.invoices.find(
        {
            "payment_type": "partial",
            "fund_source": "digital",
            "digital_platform": "Partial",
        },
        {"_id": 0}
    ).to_list(1000)

    if not corrupted:
        return {"message": "No corrupted partial invoices found. Nothing to fix.", "fixed": 0}

    for inv in corrupted:
        inv_id = inv.get("id", "")
        inv_num = inv.get("invoice_number", "")
        branch_id = inv.get("branch_id", "")
        amount_paid = float(inv.get("amount_paid", 0))
        org_id = inv.get("_org_id", "unknown")

        fix_record = {
            "invoice_id": inv_id,
            "invoice_number": inv_num,
            "branch_id": branch_id,
            "org_id": org_id,
            "amount_paid": amount_paid,
            "action": "dry_run" if dry_run else "fixed",
        }

        if not dry_run:
            # 1. Fix the invoice record
            await _raw_db.invoices.update_one(
                {"id": inv_id},
                {
                    "$set": {"fund_source": "cashier"},
                    "$unset": {"digital_platform": "", "receipt_status": ""},
                }
            )

            # 2. Fix the payment records inside the invoice
            await _raw_db.invoices.update_one(
                {"id": inv_id},
                {"$set": {"payments.$[elem].fund_source": "cashier"}},
                array_filters=[{"elem.fund_source": "digital"}],
            )

            # 3. Move cash from digital wallet → cashier wallet
            if amount_paid > 0 and branch_id:
                ref_text = f"Migration fix: partial invoice {inv_num} — cash moved from digital to cashier"

                # Deduct from digital wallet (search by branch_id only)
                digital_wallet = await _raw_db.fund_wallets.find_one(
                    {"branch_id": branch_id, "type": "digital", "active": True},
                    {"_id": 0}
                )
                if digital_wallet:
                    await _raw_db.fund_wallets.update_one(
                        {"id": digital_wallet["id"]},
                        {"$inc": {"balance": -round(amount_paid, 2)}}
                    )
                    await _raw_db.wallet_movements.insert_one({
                        "id": new_id(),
                        "wallet_id": digital_wallet["id"],
                        "branch_id": branch_id,
                        "type": "migration_correction",
                        "amount": -round(amount_paid, 2),
                        "reference": ref_text,
                        "created_at": now_iso(),
                    })

                # Add to cashier wallet (search by branch_id only)
                cashier_wallet = await _raw_db.fund_wallets.find_one(
                    {"branch_id": branch_id, "type": "cashier", "active": True},
                    {"_id": 0}
                )
                if cashier_wallet:
                    await _raw_db.fund_wallets.update_one(
                        {"id": cashier_wallet["id"]},
                        {"$inc": {"balance": round(amount_paid, 2)}}
                    )
                    await _raw_db.wallet_movements.insert_one({
                        "id": new_id(),
                        "wallet_id": cashier_wallet["id"],
                        "branch_id": branch_id,
                        "type": "migration_correction",
                        "amount": round(amount_paid, 2),
                        "reference": ref_text,
                        "created_at": now_iso(),
                    })

                fix_record["wallet_moved"] = True
                fix_record["digital_wallet_found"] = bool(digital_wallet)
                fix_record["cashier_wallet_found"] = bool(cashier_wallet)

        fixes.append(fix_record)

    return {
        "message": f"{'DRY RUN — ' if dry_run else ''}Fixed {len(fixes)} corrupted partial invoice(s)",
        "dry_run": dry_run,
        "fixed": len(fixes),
        "details": fixes,
    }


@router.post("/migrations/fix-partial-wallets")
async def fix_partial_wallets(data: dict, user=Depends(require_super_admin)):
    """
    Fix wallet balances for a specific invoice that was already corrected
    but whose wallet transfer failed (e.g., due to _org_id mismatch).
    Requires: invoice_id and amount.
    """
    invoice_id = data.get("invoice_id", "")
    amount = float(data.get("amount", 0))
    branch_id = data.get("branch_id", "")

    if not invoice_id or not amount or not branch_id:
        raise HTTPException(status_code=400, detail="invoice_id, amount, and branch_id are required")

    ref_text = f"Wallet correction: partial invoice {invoice_id} — ₱{amount:,.2f} digital→cashier"
    results = {"invoice_id": invoice_id, "amount": amount, "branch_id": branch_id}

    # Deduct from digital wallet
    dw = await _raw_db.fund_wallets.find_one(
        {"branch_id": branch_id, "type": "digital", "active": True}, {"_id": 0}
    )
    if dw:
        await _raw_db.fund_wallets.update_one({"id": dw["id"]}, {"$inc": {"balance": -round(amount, 2)}})
        await _raw_db.wallet_movements.insert_one({
            "id": new_id(), "wallet_id": dw["id"], "branch_id": branch_id,
            "type": "migration_correction", "amount": -round(amount, 2),
            "reference": ref_text, "created_at": now_iso(),
        })
        results["digital_deducted"] = True
        results["digital_old_balance"] = dw.get("balance", 0)
    else:
        results["digital_deducted"] = False
        results["note"] = "No digital wallet found — cash may not have been deposited there"

    # Add to cashier wallet
    cw = await _raw_db.fund_wallets.find_one(
        {"branch_id": branch_id, "type": "cashier", "active": True}, {"_id": 0}
    )
    if cw:
        await _raw_db.fund_wallets.update_one({"id": cw["id"]}, {"$inc": {"balance": round(amount, 2)}})
        await _raw_db.wallet_movements.insert_one({
            "id": new_id(), "wallet_id": cw["id"], "branch_id": branch_id,
            "type": "migration_correction", "amount": round(amount, 2),
            "reference": ref_text, "created_at": now_iso(),
        })
        results["cashier_added"] = True
        results["cashier_old_balance"] = cw.get("balance", 0)
    else:
        results["cashier_added"] = False

    return {"message": "Wallet correction complete", "results": results}


# ── Delete Organization ─────────────────────────────────────────────────────

# All collections that hold per-org data
_ORG_DATA_COLLECTIONS = [
    "users", "branches", "products", "inventory", "customers",
    "invoices", "sales", "purchase_orders", "suppliers", "employees",
    "movements", "fund_wallets", "wallet_movements", "fund_transfers",
    "expenses", "branch_prices", "branch_transfer_orders",
    "count_sheets", "daily_closings", "sales_log", "returns",
    "discrepancy_log", "notifications", "view_tokens", "safe_lots",
    "price_schemes", "settings", "system_settings", "accounts_payable",
    "capital_changes", "security_events", "pin_attempt_log",
    "payables", "receivables", "product_vendors", "invoice_edits",
    "inventory_corrections", "inventory_adjustments", "inventory_logs",
    "employee_advance_logs", "safe_lot_usages",
    "branch_transfer_price_memory", "branch_transfer_templates",
    "audits", "upload_sessions", "business_documents", "doc_upload_tokens",
    "sms_queue", "sms_templates", "sms_settings", "sms_inbox",
    "product_categories", "invoice_corrections", "sale_reservations",
    "discount_audit_log", "custom_roles", "journal_entries",
    "incident_tickets", "doc_codes", "qr_action_logs",
    "internal_invoices", "receivables",
]

# Global (not org_id scoped) collections that also store org-linked data by reference
_ORG_GLOBAL_COLLECTIONS = [
    "terminal_sessions", "terminal_codes", "qr_pair_tokens",
    "sms_gateway_logs", "payment_submissions",
]


@router.post("/organizations/{org_id}/backup")
async def backup_organization(org_id: str, user=Depends(require_super_admin)):
    """Trigger a manual backup of a single organization's data."""
    org = await _raw_db.organizations.find_one({"id": org_id}, {"_id": 0})
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    from services.org_backup_service import create_org_backup
    result = await create_org_backup(
        org_id=org_id,
        org_name=org.get("name", ""),
        triggered_by=user.get("email", "super_admin"),
    )
    return result


@router.delete("/organizations/{org_id}")
async def delete_organization(org_id: str, user=Depends(require_super_admin)):
    """
    Backup then permanently delete an organization and ALL its data.
    Steps: 1) verify  2) backup to R2  3) delete tenant data  4) delete org record
    """
    org = await _raw_db.organizations.find_one({"id": org_id}, {"_id": 0})
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    org_name = org.get("name", "")

    # ── Step 1: Backup ────────────────────────────────────────────────────────
    from services.org_backup_service import create_org_backup
    try:
        backup_result = await create_org_backup(
            org_id=org_id,
            org_name=org_name,
            triggered_by=f"pre_delete by {user.get('email', 'super_admin')}",
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Backup failed — deletion aborted for safety: {exc}"
        )

    # ── Step 2: Delete tenant-scoped collections ──────────────────────────────
    deleted_counts = {}
    for col_name in _ORG_DATA_COLLECTIONS:
        try:
            r = await _raw_db[col_name].delete_many({"organization_id": org_id})
            if r.deleted_count:
                deleted_counts[col_name] = r.deleted_count
        except Exception:
            pass

    # ── Step 3: Delete global collections that reference this org ─────────────
    for col_name in _ORG_GLOBAL_COLLECTIONS:
        try:
            r = await _raw_db[col_name].delete_many({"organization_id": org_id})
            if r.deleted_count:
                deleted_counts[col_name] = r.deleted_count
        except Exception:
            pass

    # ── Step 4: Delete the organization record itself ─────────────────────────
    await _raw_db.organizations.delete_one({"id": org_id})

    # ── Step 5: Record audit log + store org_doc for later restore ────────────
    await _raw_db.org_backups.update_one(
        {"r2_key": backup_result.get("r2_key")},
        {"$set": {
            "deletion_completed_at": now_iso(),
            "deleted_by": user.get("email"),
            "org_doc": org,          # Full org record — used to recreate org on restore
            "backup_type": "pre_delete",
        }}
    )

    total_deleted = sum(deleted_counts.values())
    return {
        "message": f"Organization '{org_name}' deleted successfully",
        "org_name": org_name,
        "backup": backup_result,
        "total_documents_deleted": total_deleted,
        "collections_cleared": deleted_counts,
    }

# ── Backups & Restore ───────────────────────────────────────────────────────

@router.get("/all-backups")
async def list_all_backups(user=Depends(require_super_admin)):
    """List all org backup records across all organisations, newest first."""
    backups = await _raw_db.org_backups.find(
        {}
    ).sort("created_at", -1).to_list(500)
    result = []
    for b in backups:
        b["_id"] = str(b["_id"])   # ObjectId → string
        result.append(b)
    return result


async def _do_restore(org_id: str, compressed_bytes: bytes,
                      org_doc: dict | None, restored_by: str) -> dict:
    """
    Core restore logic shared by both R2 and upload paths.
    1. Decompress + validate backup
    2. Upsert org record into organizations
    3. Purge + re-insert every tenant collection
    """
    import gzip, json
    from services.org_backup_service import ORG_COLLECTIONS

    try:
        payload = json.loads(gzip.decompress(compressed_bytes).decode("utf-8"))
        manifest = payload["manifest"]
        data = payload["data"]
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Backup file is corrupt or unreadable: {exc}")

    backup_org_id = manifest.get("org_id")
    if not backup_org_id:
        raise HTTPException(status_code=400, detail="Backup file has no org_id in manifest")

    # Allow cross-org_id override only when org_doc is supplied (upload path)
    if org_id and org_id != backup_org_id:
        raise HTTPException(
            status_code=400,
            detail=f"org_id mismatch: backup contains {backup_org_id}, expected {org_id}"
        )
    org_id = backup_org_id

    # ── Re-create / update org record ────────────────────────────────────────
    if org_doc:
        # Full org record stored during pre-delete backup
        restore_doc = {**org_doc, "restored_at": now_iso(), "restored_by": restored_by}
    else:
        # Best-effort: preserve existing if it exists, otherwise create minimal
        existing_org = await _raw_db.organizations.find_one({"id": org_id}, {"_id": 0})
        if existing_org:
            restore_doc = existing_org
        else:
            restore_doc = {
                "id": org_id,
                "name": manifest.get("org_name", "Restored Company"),
                "plan": "trial",
                "subscription_status": "active",
                "created_at": now_iso(),
                "restored_at": now_iso(),
                "restored_by": restored_by,
            }

    await _raw_db.organizations.replace_one({"id": org_id}, restore_doc, upsert=True)

    # ── Restore tenant collections ────────────────────────────────────────────
    restored_collections = {}
    errors = []
    for coll_name in ORG_COLLECTIONS:
        docs = data.get(coll_name, [])
        try:
            await _raw_db[coll_name].delete_many({"organization_id": org_id})
            if docs:
                for d in docs:
                    d["organization_id"] = org_id
                await _raw_db[coll_name].insert_many(docs)
            restored_collections[coll_name] = len(docs)
        except Exception as exc:
            errors.append(f"{coll_name}: {exc}")

    total_docs = sum(restored_collections.values())

    # ── Log restore event ─────────────────────────────────────────────────────
    await _raw_db.org_backups.insert_one({
        "org_id": org_id,
        "org_name": restore_doc.get("name", ""),
        "backup_type": "restore",
        "restored_by": restored_by,
        "total_documents": total_docs,
        "errors": errors,
        "created_at": now_iso(),
    })

    return {
        "success": len(errors) == 0,
        "org_id": org_id,
        "org_name": restore_doc.get("name", ""),
        "total_documents_restored": total_docs,
        "collections_restored": len(restored_collections),
        "errors": errors,
    }


@router.post("/restore/{backup_id}")
async def restore_from_r2(backup_id: str, user=Depends(require_super_admin)):
    """Restore a company from an existing R2 backup record."""
    import os, boto3
    from botocore.config import Config
    from bson import ObjectId

    # Look up backup record
    try:
        record = await _raw_db.org_backups.find_one({"_id": ObjectId(backup_id)}, {"_id": 0})
    except Exception:
        record = None
    if not record:
        raise HTTPException(status_code=404, detail="Backup record not found")
    if not record.get("r2_key"):
        raise HTTPException(status_code=400, detail="This backup record has no R2 file attached")

    # Download from R2
    r2 = boto3.client(
        "s3",
        endpoint_url=os.environ.get("R2_ENDPOINT_URL"),
        aws_access_key_id=os.environ.get("R2_ACCESS_KEY_ID"),
        aws_secret_access_key=os.environ.get("R2_SECRET_ACCESS_KEY"),
        region_name="auto",
        config=Config(signature_version="s3v4"),
    )
    bucket = os.environ.get("R2_BUCKET_NAME", "agribooks-backups")
    try:
        resp = r2.get_object(Bucket=bucket, Key=record["r2_key"])
        compressed = resp["Body"].read()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to download from R2: {exc}")

    org_doc = record.get("org_doc")  # May be None for scheduled/manual backups
    return await _do_restore(
        org_id=record["org_id"],
        compressed_bytes=compressed,
        org_doc=org_doc,
        restored_by=user.get("email", "super_admin"),
    )


@router.post("/restore-upload")
async def restore_from_upload(
    file: UploadFile = File(...),
    user=Depends(require_super_admin),
):

    if not (file.filename or "").endswith(".gz") and file.content_type not in (
        "application/gzip", "application/x-gzip", "application/octet-stream"
    ):
        raise HTTPException(status_code=400, detail="File must be a .json.gz backup archive")

    content = await file.read()
    if len(content) > 500 * 1024 * 1024:  # 500 MB hard cap
        raise HTTPException(status_code=400, detail="File too large (max 500 MB)")

    # Try to find stored org_doc in DB using org_id from manifest
    import gzip, json
    try:
        manifest = json.loads(gzip.decompress(content))["manifest"]
        org_id = manifest.get("org_id", "")
    except Exception:
        org_id = ""

    # Look for org_doc in any matching backup record (pre_delete backups have it)
    org_doc = None
    if org_id:
        rec = await _raw_db.org_backups.find_one(
            {"org_id": org_id, "org_doc": {"$exists": True}},
            {"_id": 0, "org_doc": 1}
        )
        if rec:
            org_doc = rec.get("org_doc")

    return await _do_restore(
        org_id=org_id,
        compressed_bytes=content,
        org_doc=org_doc,
        restored_by=user.get("email", "super_admin"),
    )

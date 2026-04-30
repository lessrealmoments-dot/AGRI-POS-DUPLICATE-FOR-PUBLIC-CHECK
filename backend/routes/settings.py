"""
Settings routes: Invoice prefixes, terms options, system settings, PIN policies.
"""
from fastapi import APIRouter, Depends, HTTPException
from config import db
from utils import get_current_user, check_perm, now_iso

router = APIRouter(prefix="/settings", tags=["Settings"])


# ── PIN Policies ─────────────────────────────────────────────────────────────

@router.get("/pin-policies")
async def get_pin_policies(user=Depends(get_current_user)):
    """Get PIN policy configuration. Admin only."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    from routes.verify import PIN_POLICY_ACTIONS, PIN_METHODS, _get_pin_policy
    custom = await _get_pin_policy()
    # Merge defaults with custom overrides
    policies = {}
    for action in PIN_POLICY_ACTIONS:
        policies[action["key"]] = custom.get(action["key"], action["defaults"])
    return {"actions": PIN_POLICY_ACTIONS, "methods": PIN_METHODS, "policies": policies}


@router.put("/pin-policies")
async def update_pin_policies(data: dict, user=Depends(get_current_user)):
    """Update PIN policies. Admin only."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    from routes.verify import PIN_METHODS
    policies = data.get("policies", {})
    # Validate: each value must be a list of valid methods
    valid_methods = set(PIN_METHODS)
    for key, methods in policies.items():
        if not isinstance(methods, list):
            raise HTTPException(status_code=400, detail=f"Invalid policy for {key}")
        if not all(m in valid_methods for m in methods):
            raise HTTPException(status_code=400, detail=f"Invalid method in policy for {key}")
        if len(methods) == 0:
            raise HTTPException(status_code=400, detail=f"At least one PIN method required for {key}")
    await db.system_settings.update_one(
        {"key": "pin_policies"},
        {"$set": {"key": "pin_policies", "policies": policies, "updated_at": now_iso(), "updated_by": user["id"]}},
        upsert=True
    )
    return {"message": "PIN policies updated", "policies": policies}


# ── Legacy TOTP Controls (kept for backward compatibility) ───────────────────

# All sensitive actions that can be protected by TOTP
TOTP_PROTECTED_ACTIONS = [
    {"key": "inventory_adjust",    "label": "Direct Inventory Correction",   "module": "Inventory"},
    {"key": "close_day",           "label": "Close Day (Z-Report)",           "module": "Daily Operations"},
    {"key": "invoice_edit",        "label": "Edit Posted Invoice",            "module": "Sales"},
    {"key": "invoice_void",        "label": "Void Invoice",                   "module": "Sales"},
    {"key": "product_delete",      "label": "Delete Product",                 "module": "Products"},
    {"key": "price_override",      "label": "Override Branch Price",          "module": "Products"},
    {"key": "reopen_po",           "label": "Reopen Purchase Order",          "module": "Purchase Orders"},
    {"key": "manage_users",        "label": "Create / Edit / Delete Users",   "module": "Settings"},
    {"key": "manage_permissions",  "label": "Manage User Permissions",        "module": "Settings"},
]

DEFAULT_TOTP_ACTIONS = ["inventory_adjust", "close_day"]


@router.get("/totp-controls")
async def get_totp_controls(user=Depends(get_current_user)):
    """Get which sensitive actions are protected by TOTP."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    doc = await db.system_settings.find_one({"key": "totp_controls"}, {"_id": 0})
    enabled = doc.get("enabled_actions", DEFAULT_TOTP_ACTIONS) if doc else DEFAULT_TOTP_ACTIONS
    return {"actions": TOTP_PROTECTED_ACTIONS, "enabled_actions": enabled}


@router.put("/totp-controls")
async def update_totp_controls(data: dict, user=Depends(get_current_user)):
    """Update which actions require TOTP verification."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    enabled = data.get("enabled_actions", [])
    await db.system_settings.update_one(
        {"key": "totp_controls"},
        {"$set": {"key": "totp_controls", "enabled_actions": enabled, "updated_at": now_iso()}},
        upsert=True
    )
    return {"message": "TOTP controls updated", "enabled_actions": enabled}


@router.get("/invoice-prefixes")
async def get_invoice_prefixes(user=Depends(get_current_user)):
    """Get invoice prefix settings."""
    s = await db.settings.find_one({"key": "invoice_prefixes"}, {"_id": 0})
    return s.get("value", {}) if s else {
        "sales_invoice": "SI",
        "purchase_order": "PO",
        "service_invoice": "SVC",
        "cash_advance": "CA",
        "interest_charge": "INT",
        "penalty_charge": "PEN",
    }


@router.put("/invoice-prefixes")
async def update_invoice_prefixes(data: dict, user=Depends(get_current_user)):
    """Update invoice prefix settings."""
    check_perm(user, "settings", "edit")
    await db.settings.update_one(
        {"key": "invoice_prefixes"},
        {"$set": {"key": "invoice_prefixes", "value": data, "updated_at": now_iso()}},
        upsert=True
    )
    return data


@router.get("/terms-options")
async def get_terms_options(user=Depends(get_current_user)):
    """Get available payment terms options."""
    return [
        {"key": "COD", "label": "Cash on Delivery", "days": 0},
        {"key": "NET7", "label": "Net 7 Days", "days": 7},
        {"key": "NET15", "label": "Net 15 Days", "days": 15},
        {"key": "NET30", "label": "Net 30 Days", "days": 30},
        {"key": "NET45", "label": "Net 45 Days", "days": 45},
        {"key": "NET60", "label": "Net 60 Days", "days": 60},
        {"key": "NET90", "label": "Net 90 Days", "days": 90},
    ]


# ── Business Info ────────────────────────────────────────────────────────────

@router.get("/business-info")
async def get_business_info(user=Depends(get_current_user)):
    """Get business info for receipts/printing."""
    doc = await db.settings.find_one({"key": "company_info"}, {"_id": 0})
    base = doc.get("value", {}) if doc else {}
    # Merge with print-specific fields
    print_doc = await db.settings.find_one({"key": "business_print_info"}, {"_id": 0})
    print_info = print_doc.get("value", {}) if print_doc else {}
    return {
        "business_name": base.get("name", ""),
        "address": print_info.get("address", ""),
        "phone": base.get("phone", "") or print_info.get("phone", ""),
        "tin": print_info.get("tin", ""),
        "email": base.get("email", ""),
        "trust_receipt_terms": print_info.get("trust_receipt_terms",
            "Received the above item in good condition and in trust from {business_name} (trustor) "
            "to be sold and the proceeds delivered to the said trustor on or before the due date. "
            "In case of non-payment, the trustee (buyer) shall pay: an interest of not less than 3% "
            "per month from the due date and Atty's fees equal to 25% of the amount due plus the cost of suit."
        ),
        "receipt_footer": print_info.get("receipt_footer", "This is not an official receipt."),
        "thermal_width": print_info.get("thermal_width", "58mm"),
    }


@router.put("/business-info")
async def update_business_info(data: dict, user=Depends(get_current_user)):
    """Update business info for receipts/printing. Business name required."""
    check_perm(user, "settings", "edit")
    business_name = data.get("business_name", "").strip()
    if not business_name:
        from fastapi import HTTPException as HE
        raise HE(status_code=400, detail="Business name is required")

    # Update the company_info name
    await db.settings.update_one(
        {"key": "company_info"},
        {"$set": {"value.name": business_name, "value.phone": data.get("phone", ""), "updated_at": now_iso()}},
        upsert=True
    )
    # Store print-specific info separately
    await db.settings.update_one(
        {"key": "business_print_info"},
        {"$set": {
            "key": "business_print_info",
            "value": {
                "address": data.get("address", ""),
                "phone": data.get("phone", ""),
                "tin": data.get("tin", ""),
                "trust_receipt_terms": data.get("trust_receipt_terms", ""),
                "receipt_footer": data.get("receipt_footer", "This is not an official receipt."),
                "thermal_width": data.get("thermal_width", "58mm"),
            },
            "updated_at": now_iso(),
            "updated_by": user["id"],
        }},
        upsert=True
    )
    return {"message": "Business info updated"}


# ── Legacy TOTP Controls kept in lines 69-90 above ──────────────────────────



# ── Sales Settings (margin threshold, etc.) ──────────────────────────────────

@router.get("/sales-config")
async def get_sales_config(user=Depends(get_current_user)):
    """Get sales configuration (margin threshold, etc.)."""
    doc = await db.system_settings.find_one({"key": "sales_config"}, {"_id": 0})
    defaults = {"min_margin_percent": 1, "margin_warning_enabled": True}
    if doc:
        return {**defaults, **doc.get("config", {})}
    return defaults


@router.put("/sales-config")
async def update_sales_config(data: dict, user=Depends(get_current_user)):
    """Update sales configuration. Admin only."""
    check_perm(user, "settings", "edit")
    config = {
        "min_margin_percent": max(0, min(50, float(data.get("min_margin_percent", 1)))),
        "margin_warning_enabled": bool(data.get("margin_warning_enabled", True)),
    }
    await db.system_settings.update_one(
        {"key": "sales_config"},
        {"$set": {"key": "sales_config", "config": config, "updated_at": now_iso(), "updated_by": user["id"]}},
        upsert=True
    )
    return {"message": "Sales config updated", **config}


# ── Collection Notification Recipients ───────────────────────────────────────

@router.get("/collection-recipients")
async def get_collection_recipients(user=Depends(get_current_user)):
    """Get phone numbers for collection notifications.
    owner/admin are global (all branches).
    manager/auditor support per-branch overrides via branch_phones dict."""
    doc = await db.system_settings.find_one(
        {"key": "collection_notification_recipients"}, {"_id": 0}
    )
    if not doc:
        return {"owner_phone": "", "admin_phone": "", "manager_phone": "",
                "auditor_phone": "", "branch_phones": {}}
    return {
        "owner_phone":   doc.get("owner_phone", ""),
        "admin_phone":   doc.get("admin_phone", ""),
        "manager_phone": doc.get("manager_phone", ""),
        "auditor_phone": doc.get("auditor_phone", ""),
        "branch_phones": doc.get("branch_phones", {}),
    }


@router.put("/collection-recipients")
async def update_collection_recipients(data: dict, user=Depends(get_current_user)):
    """Update collection notification phone numbers. Admin only.
    branch_phones format: {branch_id: {manager_phone, auditor_phone}}"""
    check_perm(user, "settings", "edit")
    recipients = {
        "owner_phone":   data.get("owner_phone", "").strip(),
        "admin_phone":   data.get("admin_phone", "").strip(),
        "manager_phone": data.get("manager_phone", "").strip(),
        "auditor_phone": data.get("auditor_phone", "").strip(),
        "branch_phones": {
            bid: {
                "manager_phone": (v.get("manager_phone") or "").strip(),
                "auditor_phone": (v.get("auditor_phone") or "").strip(),
            }
            for bid, v in (data.get("branch_phones") or {}).items()
            if isinstance(v, dict)
        },
    }
    await db.system_settings.update_one(
        {"key": "collection_notification_recipients"},
        {"$set": {
            "key": "collection_notification_recipients",
            **recipients,
            "updated_at": now_iso(),
            "updated_by": user["id"],
        }},
        upsert=True
    )
    return {"message": "Collection recipients updated", **recipients}


# ── Company Info Self-Heal ───────────────────────────────────────────────────
# After Reset Company (or any state where settings.company_info is missing),
# SMS signatures degrade to "- MAIN BRANCH" only and the Dashboard reads no
# brand. These two endpoints power a "Restore my Company Info" banner that
# rebuilds the setting from the immutable `organizations` row in one tap.

@router.get("/company-info-status")
async def get_company_info_status(user=Depends(get_current_user)):
    """Tell the dashboard banner whether company_info is missing and what we
    can pre-fill from `organizations` if so."""
    from config import _raw_db, get_org_context
    org_id = user.get("organization_id") or get_org_context()
    has = bool(await db.settings.find_one({"key": "company_info"}, {"_id": 0}))
    suggested = {}
    if org_id:
        org = await _raw_db.organizations.find_one(
            {"id": org_id}, {"_id": 0, "name": 1, "phone": 1, "email": 1, "address": 1}
        )
        if org:
            suggested = {
                "name": org.get("name", ""),
                "phone": org.get("phone", ""),
                "email": org.get("email", ""),
                "address": org.get("address", ""),
            }
    return {"has_company_info": has, "suggested": suggested}


@router.post("/restore-company-info")
async def restore_company_info(user=Depends(get_current_user)):
    """One-tap self-heal: rebuild settings.company_info from the immutable
    `organizations` row. Idempotent; only writes when missing or empty so it
    never overwrites a user-edited value.

    Self-heals two failure modes that previously surfaced as toast errors:
      1. The organization row was deleted from `organizations` (orphan tenant).
         We treat this as a brand-new company and recreate the org row + a
         default branch using the user's name/email so the user can keep
         working without contacting support.
      2. The user's JWT carries no organization_id. This usually means the
         account predates multi-tenant auth — we cannot safely auto-create a
         tenant for them without knowing intent, so we surface a clear
         instruction (logout + log back in to refresh the token).
    """
    check_perm(user, "settings", "edit")
    from datetime import datetime, timezone, timedelta
    from config import _raw_db, get_org_context, set_org_context
    from utils import new_id

    org_id = user.get("organization_id") or get_org_context()
    if not org_id:
        raise HTTPException(
            status_code=400,
            detail=("Your session has no organization context. Please log out "
                    "and sign in again to refresh your account, then retry."),
        )

    org = await _raw_db.organizations.find_one({"id": org_id}, {"_id": 0})
    recreated = False

    if not org:
        # Treat as a deleted/new company — recreate the organization row so
        # the rest of the self-heal can continue. Name preference order:
        # existing settings.company_info.name → "<full_name>'s Company" → fallback.
        existing_settings = await _raw_db.settings.find_one(
            {"key": "company_info", "organization_id": org_id}, {"_id": 0}
        )
        existing_name = ((existing_settings or {}).get("value") or {}).get("name", "").strip()
        full_name = (user.get("full_name") or "").strip()
        company_name = (
            existing_name
            or (f"{full_name}'s Company" if full_name else "")
            or (user.get("email", "").split("@")[0].strip() + "'s Company"
                if user.get("email") else "")
            or "My Company"
        )

        from routes.organizations import PLAN_LIMITS  # local import avoids cycle
        now = now_iso()
        trial_end = (datetime.now(timezone.utc) + timedelta(days=14)).isoformat()
        new_org = {
            "id": org_id,
            "name": company_name,
            "owner_email": user.get("email", ""),
            "phone": "",
            "address": "",
            "plan": "trial",
            "subscription_status": "trial",
            "trial_ends_at": trial_end,
            "max_branches": PLAN_LIMITS["trial"]["max_branches"],
            "max_users": PLAN_LIMITS["trial"]["max_users"],
            "extra_branches": 0,
            "annual_billing": False,
            "is_demo": False,
            "created_at": now,
            "self_healed_at": now,
        }
        await _raw_db.organizations.insert_one(new_org)
        org = new_org
        recreated = True

        # Recreate a default branch + seed SMS templates so the rest of the
        # app works immediately after the heal.
        set_org_context(org_id)
        try:
            existing_branch = await _raw_db.branches.find_one(
                {"organization_id": org_id}, {"_id": 0, "id": 1}
            )
            if not existing_branch:
                branch_id = new_id()
                await _raw_db.branches.insert_one({
                    "id": branch_id,
                    "name": f"{company_name} - Main Branch",
                    "address": "",
                    "phone": "",
                    "active": True,
                    "organization_id": org_id,
                    "created_at": now,
                })
                try:
                    from routes.organizations import provision_branch_wallets
                    await provision_branch_wallets(branch_id, f"{company_name} - Main Branch")
                except Exception:
                    pass  # wallet provisioning is best-effort during heal
            try:
                from routes.sms import _ensure_templates
                await _ensure_templates()
            except Exception:
                pass  # template seeding is best-effort during heal
        finally:
            set_org_context(None)

    existing = await db.settings.find_one({"key": "company_info"}, {"_id": 0})
    existing_name = (existing or {}).get("value", {}).get("name", "").strip()
    if existing_name and not recreated:
        # Already populated and we didn't just rebuild — return as-is so we
        # never clobber a user-edited value.
        return {"restored": False, "reason": "already_set", "value": existing["value"]}

    value = {
        "name": org.get("name", ""),
        "phone": org.get("phone", ""),
        "email": org.get("email", "") or user.get("email", ""),
        "address": org.get("address", ""),
        "currency": (existing or {}).get("value", {}).get("currency", "PHP"),
        "date_format": (existing or {}).get("value", {}).get("date_format", "MM/DD/YYYY"),
    }
    await db.settings.update_one(
        {"key": "company_info"},
        {"$set": {"key": "company_info", "value": value, "updated_at": now_iso()}},
        upsert=True,
    )
    return {"restored": True, "recreated": recreated, "value": value}

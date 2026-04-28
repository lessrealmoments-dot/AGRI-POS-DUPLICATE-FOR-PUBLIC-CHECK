"""
================================================================================
AgriPOS API Server - Modular Architecture
================================================================================
Multi-branch Inventory, POS & Accounting System for Agricultural Retail

This is the production entry point that imports all modular routes from /routes.

Version: 3.0 (Dec 2025) - Complete Modular Refactor
================================================================================
"""

from fastapi import FastAPI, APIRouter, HTTPException, WebSocket
from fastapi.responses import FileResponse
from starlette.middleware.cors import CORSMiddleware
import os
from datetime import datetime, timezone, timedelta

# APScheduler for daily backup
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

# Import from local modules
from config import db, client, logger, JWT_SECRET
from utils import hash_password, now_iso, new_id
from models import DEFAULT_PERMISSIONS

_scheduler = AsyncIOScheduler()
# Import all route modules
from routes import (
    auth_router, branches_router, users_router, products_router,
    customers_router, inventory_router, price_schemes_router, invoices_router,
    sales_router, purchase_orders_router, dashboard_router, accounting_router,
    daily_ops_router, suppliers_router, sync_router, employees_router,
    settings_router, count_sheets_router, setup_router, branch_prices_router,
    import_router, notifications_router, branch_transfers_router, reports_router,
    returns_router, audit_router, uploads_router, verify_router,
    organizations_router, superadmin_router, admin_auth_router,
    terminal_router, roles_router,
)
from routes.backups import router as backups_router
from routes.internal_invoices import router as internal_invoices_router
from routes.incident_tickets import router as incident_tickets_router
from routes.scanner import router as scanner_router, ws_desktop, ws_phone
from routes.search import router as search_router
from routes.zreport_pdf import router as zreport_pdf_router
from routes.journal_entries import router as journal_entries_router
from routes.doc_lookup import router as doc_lookup_router
from routes.stock_releases import router as stock_releases_router
from routes.qr_actions import router as qr_actions_router
from routes.documents import router as documents_router
from routes.sms import router as sms_router
from routes.invoice_corrections import router as invoice_corrections_router
from routes.app_downloads import router as app_downloads_router
from routes.crop_credits import router as crop_credits_router
from routes.signatures import router as signatures_router

# =============================================================================
# APP SETUP
# =============================================================================
app = FastAPI(
    title="AgriPOS API",
    description="Multi-branch Inventory, POS & Accounting System",
    version="3.0"
)

# ── Global exception handler: catch ALL unhandled errors with real messages ──
from fastapi.requests import Request
from fastapi.responses import JSONResponse
import traceback as _tb

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch any unhandled exception. Log full detail server-side; return a
    generic message to the client so internals are never exposed."""
    if isinstance(exc, HTTPException):
        raise exc  # Let FastAPI handle HTTP exceptions normally
    error_detail = f"{type(exc).__name__}: {str(exc)}"
    tb_str = _tb.format_exc()
    # Full trace stays in supervisor logs only — never sent to client
    print(f"[ERROR] {request.method} {request.url.path}: {error_detail}\n{tb_str}", flush=True)
    logger.error(f"Unhandled error on {request.method} {request.url.path}: {error_detail}\n{tb_str}")
    return JSONResponse(
        status_code=500,
        content={"detail": "An unexpected server error occurred. Please try again or contact support."}
    )

# Main API router
api_router = APIRouter(prefix="/api")

# =============================================================================
# INCLUDE ALL MODULAR ROUTES
# =============================================================================
# Auth & Users
api_router.include_router(auth_router)
api_router.include_router(users_router)

# Core Business
api_router.include_router(branches_router)
api_router.include_router(products_router)
api_router.include_router(customers_router)
api_router.include_router(inventory_router)
api_router.include_router(price_schemes_router)

# Sales & Invoicing
api_router.include_router(invoices_router)
api_router.include_router(sales_router)
api_router.include_router(purchase_orders_router)

# Accounting & Finance
api_router.include_router(accounting_router)

# Operations
api_router.include_router(daily_ops_router)
api_router.include_router(suppliers_router)
api_router.include_router(employees_router)

# System
api_router.include_router(sync_router)
api_router.include_router(settings_router)
api_router.include_router(dashboard_router)

# Audit / Count Sheets
api_router.include_router(count_sheets_router)

# Branch-specific Pricing
api_router.include_router(branch_prices_router)

# Branch Transfers (inter-branch supply orders)
api_router.include_router(branch_transfers_router)

# Import Center
api_router.include_router(import_router)

# Notifications
api_router.include_router(notifications_router)

# Setup Wizard (no auth required)
api_router.include_router(setup_router)

# Backups
api_router.include_router(backups_router)

# Reports
api_router.include_router(reports_router)

# Returns & Refunds
api_router.include_router(returns_router)

# Invoice Corrections (Incomplete Stock)
api_router.include_router(invoice_corrections_router)

# Audit Center
api_router.include_router(audit_router)

# Receipt Uploads
api_router.include_router(uploads_router)

# Transaction Verification
api_router.include_router(verify_router)

# Organizations & Subscriptions
api_router.include_router(organizations_router)

# Super Admin (platform management)
api_router.include_router(superadmin_router)

# Internal Invoices (branch-to-branch billing)
api_router.include_router(internal_invoices_router)

# Incident Tickets (transfer variance investigation)
api_router.include_router(incident_tickets_router)

# Scanner Sessions (linked phone barcode scanner)
api_router.include_router(scanner_router)

# Admin Portal Auth (separate login)
api_router.include_router(admin_auth_router)

# Universal Transaction Search
api_router.include_router(search_router)

# AgriSmart Terminal (mobile POS pairing & sessions)
api_router.include_router(terminal_router)

# Custom Roles management
api_router.include_router(roles_router)

# Z-Report PDF
api_router.include_router(zreport_pdf_router)

# Journal Entries
api_router.include_router(journal_entries_router)

# Document Lookup
api_router.include_router(doc_lookup_router)
api_router.include_router(stock_releases_router)
api_router.include_router(qr_actions_router)

# Business Documents (AgriDocs)
api_router.include_router(documents_router)

# SMS Engine
api_router.include_router(sms_router)
api_router.include_router(app_downloads_router)

# Crop Credits (Charged-to-Crop)
api_router.include_router(crop_credits_router)

# Digital Signatures
api_router.include_router(signatures_router)

# =============================================================================
# WEBSOCKET ROUTES (must be on app directly with /api prefix)
# =============================================================================
@app.websocket("/api/scanner/ws/desktop/{session_id}")
async def websocket_desktop(websocket: WebSocket, session_id: str):
    await ws_desktop(websocket, session_id)

@app.websocket("/api/scanner/ws/phone/{session_id}")
async def websocket_phone(websocket: WebSocket, session_id: str):
    await ws_phone(websocket, session_id)

# =============================================================================
# STARTUP & SHUTDOWN
# =============================================================================
@app.on_event("startup")
async def startup():
    """Initialize database indexes and run multi-tenancy migration."""
    from config import _raw_db, TENANT_COLLECTIONS

    # ── Security check ────────────────────────────────────────────────────────
    if len(JWT_SECRET) < 32:
        logger.warning(
            "⚠️  JWT_SECRET is too short (%d chars). "
            "Generate a strong key with: openssl rand -hex 32",
            len(JWT_SECRET)
        )

    # ── Multi-tenancy migration ───────────────────────────────────────────────
    # If existing data has no organization_id, migrate it to a default org
    default_org = await _raw_db.organizations.find_one({"is_default": True}, {"_id": 0})
    if not default_org:
        user_count = await _raw_db.users.count_documents({"is_super_admin": {"$ne": True}})
        if user_count > 0:
            default_org_id = new_id()
            default_org_doc = {
                "id": default_org_id,
                "name": "AgriBooks (Default)",
                "owner_email": "",
                "plan": "pro",
                "subscription_status": "active",
                "trial_ends_at": None,
                "max_branches": 999,
                "max_users": 0,
                "extra_branches": 0,
                "annual_billing": False,
                "is_demo": False,
                "is_default": True,
                "created_at": now_iso(),
            }
            await _raw_db.organizations.insert_one(default_org_doc)
            # Tag all existing data with this org_id
            migrated = 0
            for col_name in TENANT_COLLECTIONS:
                result = await _raw_db[col_name].update_many(
                    {"organization_id": {"$exists": False}},
                    {"$set": {"organization_id": default_org_id}}
                )
                migrated += result.modified_count
            logger.info("Multi-tenancy migration: %d documents tagged with default org", migrated)

    # ── Super Admin creation ──────────────────────────────────────────────────
    SUPER_ADMIN_EMAIL = os.environ.get("SUPER_ADMIN_EMAIL", "")
    SUPER_ADMIN_PASS = os.environ.get("SUPER_ADMIN_PASS", "")
    if not SUPER_ADMIN_EMAIL or not SUPER_ADMIN_PASS:
        logger.warning("SUPER_ADMIN_EMAIL / SUPER_ADMIN_PASS not set in env — skipping super admin seed")
    existing_super = await _raw_db.users.find_one({"is_super_admin": True}, {"_id": 0})
    if not existing_super and SUPER_ADMIN_EMAIL and SUPER_ADMIN_PASS:
        await _raw_db.users.insert_one({
            "id": new_id(),
            "username": "superadmin",
            "full_name": "Platform Admin",
            "email": SUPER_ADMIN_EMAIL,
            "password_hash": hash_password(SUPER_ADMIN_PASS),
            "role": "admin",
            "is_super_admin": True,
            "organization_id": None,
            "active": True,
            "created_at": now_iso(),
        })
        logger.info("Super admin created: %s", SUPER_ADMIN_EMAIL)

    # Check if setup is needed (no auto-creation of users)
    user_count = await db.users.count_documents({})
    if user_count == 0:
        logger.info("No users found - system needs setup via /api/setup/initialize")

    # Create default price schemes if none exist (for fresh installs only)
    schemes = await _raw_db.price_schemes.count_documents({})
    if schemes == 0:
        default_schemes = [
            {
                "id": new_id(),
                "name": "Retail",
                "key": "retail",
                "description": "Standard retail price",
                "calculation_method": "percent_plus_capital",
                "calculation_value": 30,
                "base_scheme": "cost_price",
                "active": True,
                "created_at": now_iso()
            },
            {
                "id": new_id(),
                "name": "Wholesale",
                "key": "wholesale",
                "description": "Wholesale / bulk price",
                "calculation_method": "percent_plus_capital",
                "calculation_value": 15,
                "base_scheme": "cost_price",
                "active": True,
                "created_at": now_iso()
            },
            {
                "id": new_id(),
                "name": "Special",
                "key": "special",
                "description": "Special customer price",
                "calculation_method": "percent_minus_retail",
                "calculation_value": 10,
                "base_scheme": "retail",
                "active": True,
                "created_at": now_iso()
            },
            {
                "id": new_id(),
                "name": "Government",
                "key": "government",
                "description": "Government rate",
                "calculation_method": "fixed",
                "calculation_value": 0,
                "base_scheme": "cost_price",
                "active": True,
                "created_at": now_iso()
            },
        ]
        await _raw_db.price_schemes.insert_many(default_schemes)
        logger.info("Default price schemes created")

    # Create indexes
    try:
        await _raw_db.users.create_index("username", unique=True, sparse=True)
    except Exception:
        pass
    try:
        await _raw_db.users.create_index("email", sparse=True)
    except Exception:
        pass
    await _raw_db.users.create_index("id", unique=True)
    await _raw_db.products.create_index("sku")
    await _raw_db.products.create_index("id", unique=True)
    await _raw_db.products.create_index("parent_id")
    # Text index for fast product search (name + sku + barcode)
    try:
        await _raw_db.products.create_index(
            [("name", "text"), ("sku", "text"), ("barcode", "text")],
            name="product_text_search", default_language="none"
        )
    except Exception:
        pass  # Index may already exist with different options
    await _raw_db.inventory.create_index([("product_id", 1), ("branch_id", 1)], unique=True)
    await _raw_db.sales.create_index("branch_id")
    await _raw_db.sales.create_index("created_at")
    await _raw_db.invoices.create_index("branch_id")
    await _raw_db.invoices.create_index("customer_id")
    await _raw_db.invoices.create_index("created_at")
    # Idempotency: prevents duplicate invoices when Terminal retries on lag/timeout.
    # partialFilterExpression is used instead of `sparse` because legacy invoices
    # have idempotency_key:null which would collide on a sparse-unique index.
    try:
        await _raw_db.invoices.create_index(
            "idempotency_key",
            unique=True,
            partialFilterExpression={
                "idempotency_key": {"$exists": True, "$type": "string"}
            },
        )
    except Exception:
        pass  # Index may already exist with different options
    await _raw_db.customers.create_index("id", unique=True)
    await _raw_db.customers.create_index("branch_id")
    await _raw_db.branches.create_index("id", unique=True)
    await _raw_db.movements.create_index([("product_id", 1), ("branch_id", 1)])
    await _raw_db.movements.create_index("created_at")
    await _raw_db.branch_prices.create_index([("product_id", 1), ("branch_id", 1)], unique=True)
    await _raw_db.notifications.create_index("created_at")
    await _raw_db.notifications.create_index("target_user_ids")
    logger.info("Database indexes created")

    # ── Provision 4-wallet system for all existing branches ──────────────────
    from utils import provision_branch_wallets
    branches = await _raw_db.branches.find({"active": True}, {"_id": 0, "id": 1, "name": 1}).to_list(500)
    provisioned = 0
    for branch in branches:
        await provision_branch_wallets(branch["id"], branch.get("name", ""))
        provisioned += 1
    if provisioned:
        logger.info("Wallet provisioning complete — %d branches checked", provisioned)

    # ── One-shot orphan-settings sweep ────────────────────────────────────────
    # Settings docs whose organization_id no longer matches any organization are
    # the source of cross-tenant `company_info` bleed (live: "JND store" appearing
    # on Sibugay's SMS). Sweep silently on every boot — idempotent and only
    # touches settings docs that point to deleted orgs.
    try:
        live_org_ids = {
            o["id"] async for o in _raw_db.organizations.find({}, {"_id": 0, "id": 1})
        }
        if live_org_ids:
            orphan_q = {
                "organization_id": {"$exists": True, "$nin": list(live_org_ids)}
            }
            for coll in ("settings", "sms_settings", "sms_templates", "system_settings"):
                res = await _raw_db[coll].delete_many(orphan_q)
                if res.deleted_count:
                    logger.warning(
                        "Orphan-settings sweep: removed %d ghost docs from %s",
                        res.deleted_count, coll,
                    )
    except Exception as sweep_err:
        logger.error("Orphan-settings sweep failed (non-fatal): %s", sweep_err)

    # ── Daily backup scheduler ────────────────────────────────────────────────
    from services.backup_service import create_backup, delete_old_local_backups
    db_name = os.environ.get("DB_NAME", "agripos_production")
    backup_hour = int(os.environ.get("BACKUP_SCHEDULE_HOUR", "1"))
    retain_days = int(os.environ.get("BACKUP_RETENTION_DAYS", "30"))

    async def _daily_backup_job():
        logger.info("Running scheduled daily backup...")
        result = await create_backup(db_name)
        delete_old_local_backups(db_name, retain_days)
        if result["success"]:
            logger.info("Scheduled backup complete: %s (%.2f MB)", result["filename"], result["size_mb"])
        else:
            logger.error("Scheduled backup FAILED: %s", result.get("error"))

    _scheduler.add_job(
        _daily_backup_job,
        CronTrigger(hour=backup_hour, minute=0),
        id="daily_backup",
        replace_existing=True,
    )

    # ── Per-org backup scheduler (every 6 hours by default) ───────────────────
    from services.org_backup_service import create_org_backup

    async def _org_backup_job():
        """Backup all active organizations to R2."""
        orgs = await _raw_db.organizations.find(
            {"plan": {"$ne": "suspended"}},
            {"_id": 0, "id": 1, "name": 1}
        ).to_list(500)
        logger.info("Running scheduled org backups for %d organizations...", len(orgs))
        for org in orgs:
            try:
                result = await create_org_backup(org["id"], org.get("name", ""), "scheduled")
                if result["success"]:
                    logger.info("Org backup OK: %s (%d docs, %.2f MB)",
                                org.get("name", org["id"][:8]), result["total_documents"], result["size_mb"])
            except Exception as exc:
                logger.error("Org backup FAILED for %s: %s", org.get("name", org["id"][:8]), exc)

    for hour in [1, 7, 13, 19]:
        _scheduler.add_job(
            _org_backup_job,
            CronTrigger(hour=hour, minute=15),
            id=f"org_backup_{hour}",
            replace_existing=True,
        )
    # ── Update owner email ────────────────────────────────────────────────────
    await _raw_db.users.update_one(
        {"username": "owner", "is_super_admin": {"$ne": True}},
        {"$set": {"email": "sibugayagrivetsupply@gmail.com"}}
    )

    # ── Daily subscription notification job ───────────────────────────────────
    async def _daily_subscription_check():
        """Check all orgs, send expiry warnings and lock expired accounts."""
        from routes.organizations import get_effective_plan, GRACE_PERIOD_DAYS
        from services.email_service import (
            send_trial_warning, send_grace_period_warning,
            send_account_locked
        )
        now = datetime.now(timezone.utc)
        orgs = await _raw_db.organizations.find(
            {"is_default": {"$ne": True}, "plan": {"$ne": "suspended"}},
            {"_id": 0}
        ).to_list(500)

        for org in orgs:
            if not org.get("owner_email"):
                continue
            email = org["owner_email"]
            name = org["name"]
            plan = org.get("plan", "basic")
            effective = get_effective_plan(org)

            # Determine reference date (trial_ends_at or subscription_expires_at)
            ref_date_str = org.get("trial_ends_at") if plan == "trial" else org.get("subscription_expires_at")
            if not ref_date_str:
                continue
            try:
                ref_dt = datetime.fromisoformat(ref_date_str.replace("Z", "+00:00"))
            except Exception:
                continue

            days_until_expiry = (ref_dt - now).days

            # Expiry warnings (7, 3, 1 days before)
            if effective in ("trial", "basic", "standard", "pro") and days_until_expiry in (7, 3, 1):
                await send_trial_warning(email, name, days_until_expiry, ref_dt.strftime("%B %d, %Y"))

            # Grace period warnings
            elif effective == "grace_period":
                grace_ends = ref_dt + timedelta(days=GRACE_PERIOD_DAYS)
                grace_days_left = max(0, (grace_ends - now).days)
                await send_grace_period_warning(email, name, grace_days_left, grace_ends.strftime("%B %d, %Y"))

            # Lock expired accounts
            elif effective == "expired":
                current_status = org.get("subscription_status")
                if current_status != "expired":
                    await _raw_db.organizations.update_one(
                        {"id": org["id"]},
                        {"$set": {"subscription_status": "expired", "plan": "suspended"}}
                    )
                    await send_account_locked(email, name)

        logger.info("Subscription check complete — %d orgs checked", len(orgs))

    _scheduler.add_job(
        _daily_subscription_check,
        CronTrigger(hour=9, minute=0),  # 9 AM daily
        id="daily_subscription_check",
        replace_existing=True,
    )

    # ── Internal invoice due date checker ─────────────────────────────────
    async def _daily_invoice_check():
        from routes.internal_invoices import check_due_invoices
        try:
            await check_due_invoices()
            logger.info("Internal invoice due date check completed")
        except Exception as e:
            logger.error(f"Invoice check failed: {e}")

    _scheduler.add_job(
        _daily_invoice_check,
        CronTrigger(hour=8, minute=0),  # 8 AM daily
        id="daily_invoice_check",
        replace_existing=True,
    )

    # ── Daily reservation expiry check ────────────────────────────────────────
    from routes.qr_actions import process_expired_reservations
    _scheduler.add_job(
        process_expired_reservations,
        CronTrigger(hour=7, minute=30),  # 7:30 AM daily
        id="reservation_expiry_check",
        replace_existing=True,
    )

    # ── Daily compliance deadline check ──────────────────────────────────────
    async def _daily_compliance_check():
        """Fire compliance_deadline notifications for expiring/expired docs and missing monthly filings."""
        from routes.notifications import create_notification as _create_notif
        now = datetime.now(timezone.utc)
        today = now.strftime("%Y-%m-%d")
        current_year = now.year
        current_month = now.strftime("%Y-%m")
        day_of_month = now.day
        thirty_days_out = (now + timedelta(days=30)).strftime("%Y-%m-%d")

        MONTHLY_FILINGS = [
            ("sss_contributions",       "SSS Contributions"),
            ("philhealth_contributions", "PhilHealth Remittance"),
            ("pagibig_contributions",    "Pag-IBIG Remittance"),
            ("1601c",                    "BIR 1601-C"),
            ("0619e",                    "BIR 0619-E"),
            ("2550m",                    "BIR 2550M"),
        ]

        orgs = await _raw_db.organizations.find(
            {"plan": {"$ne": "suspended"}},
            {"_id": 0, "id": 1, "name": 1}
        ).to_list(500)

        total_fired = 0
        for org in orgs:
            org_id = org["id"]
            admins = await _raw_db.users.find(
                {"organization_id": org_id, "role": {"$in": ["admin", "owner"]}, "active": True},
                {"_id": 0, "id": 1}
            ).to_list(50)
            if not admins:
                continue
            admin_ids = [a["id"] for a in admins]

            branches = await _raw_db.branches.find(
                {"organization_id": org_id, "active": True},
                {"_id": 0, "id": 1, "name": 1}
            ).to_list(100)

            for branch in branches:
                branch_id = branch["id"]
                branch_name = branch.get("name", "")

                # ── Expired documents ──────────────────────────────────────
                expired_docs = await _raw_db.business_documents.find(
                    {
                        "branch_id": branch_id,
                        "valid_until": {"$lt": today, "$ne": ""},
                        "period_type": {"$in": ["validity", "annual"]},
                    },
                    {"_id": 0, "sub_category": 1, "sub_category_label": 1, "valid_until": 1}
                ).to_list(50)

                for doc in expired_docs:
                    dedup_key = f"cdl_{branch_id}_{doc['sub_category']}_expired_{today}"
                    if await _raw_db.notifications.find_one({"metadata.dedup_key": dedup_key}):
                        continue
                    label = doc.get("sub_category_label") or doc["sub_category"]
                    await _create_notif(
                        type_key="compliance_deadline",
                        title="Document Expired",
                        message=f"{label} has EXPIRED at {branch_name}. Immediate renewal required.",
                        target_user_ids=admin_ids,
                        branch_id=branch_id,
                        branch_name=branch_name,
                        metadata={
                            "dedup_key": dedup_key,
                            "sub_category": doc["sub_category"],
                            "sub_category_label": label,
                            "valid_until": doc["valid_until"],
                            "status": "expired",
                        },
                        organization_id=org_id,
                        severity_override="critical",
                    )
                    total_fired += 1

                # ── Expiring within 30 days ────────────────────────────────
                expiring_docs = await _raw_db.business_documents.find(
                    {
                        "branch_id": branch_id,
                        "valid_until": {"$gte": today, "$lte": thirty_days_out, "$ne": ""},
                        "period_type": {"$in": ["validity", "annual"]},
                    },
                    {"_id": 0, "sub_category": 1, "sub_category_label": 1, "valid_until": 1}
                ).to_list(50)

                for doc in expiring_docs:
                    days_left = (
                        datetime.fromisoformat(doc["valid_until"]) - datetime.fromisoformat(today)
                    ).days
                    # Fire once per doc validity date (not every day)
                    dedup_key = f"cdl_{branch_id}_{doc['sub_category']}_expiring_{doc['valid_until']}"
                    if await _raw_db.notifications.find_one({"metadata.dedup_key": dedup_key}):
                        continue
                    label = doc.get("sub_category_label") or doc["sub_category"]
                    await _create_notif(
                        type_key="compliance_deadline",
                        title="Document Expiring Soon",
                        message=f"{label} expires in {days_left} day{'s' if days_left != 1 else ''} at {branch_name}.",
                        target_user_ids=admin_ids,
                        branch_id=branch_id,
                        branch_name=branch_name,
                        metadata={
                            "dedup_key": dedup_key,
                            "sub_category": doc["sub_category"],
                            "sub_category_label": label,
                            "valid_until": doc["valid_until"],
                            "days_left": days_left,
                            "status": "expiring",
                        },
                        organization_id=org_id,
                        severity_override="warning",
                    )
                    total_fired += 1

                # ── Missing monthly filings (check from the 15th onwards) ──
                if day_of_month >= 15:
                    for sub_key, label in MONTHLY_FILINGS:
                        filed = await _raw_db.business_documents.find_one({
                            "branch_id": branch_id,
                            "sub_category": sub_key,
                            "coverage_months": current_month,
                        })
                        if filed:
                            continue
                        dedup_key = f"cdl_{branch_id}_{sub_key}_{current_month}_missing"
                        if await _raw_db.notifications.find_one({"metadata.dedup_key": dedup_key}):
                            continue
                        await _create_notif(
                            type_key="compliance_deadline",
                            title="Monthly Filing Missing",
                            message=f"{label} for {current_month} has not been filed at {branch_name}.",
                            target_user_ids=admin_ids,
                            branch_id=branch_id,
                            branch_name=branch_name,
                            metadata={
                                "dedup_key": dedup_key,
                                "sub_category": sub_key,
                                "sub_category_label": label,
                                "month": current_month,
                                "status": "missing_filing",
                            },
                            organization_id=org_id,
                            severity_override="warning",
                        )
                        total_fired += 1

        logger.info("Compliance check complete — %d notifications fired", total_fired)

    _scheduler.add_job(
        _daily_compliance_check,
        CronTrigger(hour=8, minute=30),  # 8:30 AM daily
        id="daily_compliance_check",
        replace_existing=True,
    )

    # ── SMS Reminder Scheduler (daily at 8:00 AM) ────────────────────────────
    async def _daily_sms_reminders():
        """Scan invoices per org for 15-day, 7-day, and overdue windows. Queue SMS reminders.
        Customers with an ACTIVE crop credit season are skipped here — they receive
        harvest-date reminders from run_harvest_reminders instead."""
        from routes.sms import queue_sms
        from routes.sms_hooks import get_company_name, get_branch_name
        from config import set_org_context
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        today_dt = datetime.strptime(today, "%Y-%m-%d")
        day_15 = (today_dt + timedelta(days=15)).strftime("%Y-%m-%d")
        day_7 = (today_dt + timedelta(days=7)).strftime("%Y-%m-%d")

        # Iterate per active organization
        orgs = await _raw_db.organizations.find({"active": True}, {"_id": 0, "id": 1}).to_list(500)
        total_queued = 0

        for org in orgs:
            org_id = org["id"]
            try:
                set_org_context(org_id)
                company_name = await get_company_name(org_id)

                # Build set of customer IDs who have an active crop credit season
                # — they get harvest reminders instead of per-invoice term reminders
                crop_customers = await _raw_db.crop_credits.find(
                    {"organization_id": org_id, "status": {"$in": ["active", "extended"]}},
                    {"_id": 0, "customer_id": 1}
                ).to_list(5000)
                crop_customer_ids = {cc["customer_id"] for cc in crop_customers if cc.get("customer_id")}

                # Load collection staff phones once per org (used for CC on 7-day + overdue)
                # branch-specific lookup happens per-invoice using _get_cc_phones
                from routes.sms_hooks import _get_cc_phones as _sms_get_cc

                invoices = await _raw_db.invoices.find(
                    {"organization_id": org_id,
                     "status": {"$nin": ["voided", "paid"]}, "balance": {"$gt": 0},
                     "due_date": {"$ne": None}, "sale_type": {"$nin": ["interest_charge", "penalty_charge"]}},
                    {"_id": 0}
                ).to_list(5000)

                queued = 0
                for inv in invoices:
                    cust_id = inv.get("customer_id")
                    if not cust_id:
                        continue

                    # Skip — crop credit customers receive harvest reminders instead
                    if cust_id in crop_customer_ids:
                        continue

                    customer = await _raw_db.customers.find_one({"id": cust_id, "organization_id": org_id}, {"_id": 0})
                    if not customer:
                        continue
                    phone = customer.get("phone", "")
                    if not phone:
                        continue

                    due_date = inv.get("due_date", "")
                    balance = inv.get("balance", 0)
                    branch_id = inv.get("branch_id", "")
                    branch_name = await get_branch_name(branch_id)
                    interest_rate = customer.get("interest_rate", 0)

                    # Auto-generate interest before overdue SMS so /payments is always the truth.
                    # Uses 30-day guard (force=False) — prevents INT invoice accumulation.
                    if due_date < today and interest_rate > 0:
                        try:
                            from routes.accounting import generate_interest_invoice as _gen_int
                            # Build a minimal mock user for the call
                            _mock_user = {
                                "id": "scheduler", "role": "admin",
                                "organization_id": org_id,
                                "permissions": {"accounting": {"generate_interest": True}},
                                "username": "scheduler",
                            }
                            await _gen_int(cust_id, {
                                "as_of_date": today,
                                "rate_override": interest_rate,
                                "force": False,
                            }, user=_mock_user)
                        except Exception:
                            pass  # guard failed silently — SMS still sends with live balance

                    # Compute live balance from invoices — avoids stale customer.balance field
                    live_bal_pipeline = [
                        {"$match": {"customer_id": cust_id,
                                    "status": {"$nin": ["voided", "paid"]},
                                    "balance": {"$gt": 0},
                                    "organization_id": org_id}},
                        {"$group": {"_id": None, "total": {"$sum": "$balance"}}}
                    ]
                    live_res = await _raw_db.invoices.aggregate(live_bal_pipeline).to_list(1)
                    total_bal = round(live_res[0]["total"], 2) if live_res else 0

                    # Actual interest owed = sum of open INT invoices for this customer
                    int_bal_res = await _raw_db.invoices.aggregate([
                        {"$match": {"customer_id": cust_id, "sale_type": "interest_charge",
                                    "status": {"$nin": ["voided", "paid"]},
                                    "balance": {"$gt": 0}}},
                        {"$group": {"_id": None, "total": {"$sum": "$balance"}}}
                    ]).to_list(1)
                    actual_interest = round(int_bal_res[0]["total"], 2) if int_bal_res else 0
                    # est_interest: only used in 7-day template; use actual if available, else estimate
                    est_interest = actual_interest if actual_interest > 0 else (
                        round(balance * (interest_rate / 100), 2) if interest_rate > 0 else 0
                    )

                    if due_date == day_15:
                        # 15-day reminder — customer only (too early for staff action)
                        await queue_sms(
                            template_key="reminder_15day",
                            customer_id=cust_id,
                            customer_name=customer.get("name", ""),
                            phone=phone,
                            variables={
                                "customer_name": customer.get("name", ""),
                                "total_balance": f"{total_bal:,.2f}",
                                "company_name": company_name,
                                "amount_due_soon": f"{balance:,.2f}",
                                "due_date": due_date,
                            },
                            organization_id=org_id,
                            branch_id=branch_id,
                            branch_name=branch_name,
                            trigger="scheduled",
                            trigger_ref=inv.get("id", ""),
                            dedup_key=f"reminder_15day:{inv.get('id', '')}:{today}",
                        )
                        queued += 1

                    elif due_date == day_7:
                        # 7-day reminder — customer + manager CC
                        sms_vars_7d = {
                            "customer_name": customer.get("name", ""),
                            "amount_due_soon": f"{balance:,.2f}",
                            "company_name": company_name,
                            "due_date": due_date,
                            "est_interest": f"{est_interest:,.2f}",
                            "interest_rate": str(interest_rate),
                            "total_balance": f"{total_bal:,.2f}",
                        }
                        await queue_sms(
                            template_key="reminder_7day",
                            customer_id=cust_id,
                            customer_name=customer.get("name", ""),
                            phone=phone,
                            variables=sms_vars_7d,
                            organization_id=org_id,
                            branch_id=branch_id,
                            branch_name=branch_name,
                            trigger="scheduled",
                            trigger_ref=inv.get("id", ""),
                            dedup_key=f"reminder_7day:{inv.get('id', '')}:{today}",
                        )
                        # CC to manager — branch-specific
                        cc = await _sms_get_cc(org_id, branch_id, {"manager"})
                        manager_phone = cc.get("manager", "")
                        if manager_phone:
                            staff_msg_7d = (
                                f"[7-Day Due] {customer.get('name','')} — "
                                f"P{balance:,.2f} due {due_date}. "
                                f"Total balance: P{total_bal:,.2f}. "
                                f"Prepare for collection."
                            )
                            await _raw_db.sms_queue.insert_one({
                                "id": new_id(), "organization_id": org_id,
                                "template_key": "reminder_7day_staff",
                                "customer_id": cust_id, "customer_name": customer.get("name", ""),
                                "phone": manager_phone, "message": staff_msg_7d,
                                "status": "pending", "trigger": "scheduled",
                                "trigger_ref": inv.get("id", ""),
                                "dedup_key": f"reminder_7day_mgr:{inv.get('id', '')}:{today}",
                                "branch_id": branch_id, "branch_name": branch_name,
                                "created_at": now_iso(),
                                "sent_at": None, "failed_at": None, "error": None, "retry_count": 0,
                            })
                        queued += 1

                    elif due_date < today:
                        days_overdue = (today_dt - datetime.strptime(due_date, "%Y-%m-%d")).days
                        if days_overdue > 0 and days_overdue % 7 == 0:
                            # Every 7 days for customer
                            await queue_sms(
                                template_key="overdue_notice",
                                customer_id=cust_id,
                                customer_name=customer.get("name", ""),
                                phone=phone,
                                variables={
                                    "customer_name": customer.get("name", ""),
                                    "amount_overdue": f"{balance:,.2f}",
                                    "company_name": company_name,
                                    "days_overdue": str(days_overdue),
                                    "interest_rate": str(interest_rate),
                                    "total_balance": f"{total_bal:,.2f}",
                                },
                                organization_id=org_id,
                                branch_id=branch_id,
                                branch_name=branch_name,
                                trigger="scheduled",
                                trigger_ref=inv.get("id", ""),
                                dedup_key=f"overdue:{inv.get('id', '')}:{today}",
                            )
                            # Staff CC: first occurrence (day 7) + every 14 days
                            is_first = days_overdue == 7
                            is_biweekly_escalation = days_overdue % 14 == 0
                            if is_first or is_biweekly_escalation:
                                staff_overdue_msg = (
                                    f"[{'1st ' if is_first else ''}Overdue {days_overdue}d] "
                                    f"{customer.get('name','')} — "
                                    f"P{balance:,.2f} overdue since {due_date}. "
                                    f"Total balance: P{total_bal:,.2f}."
                                )
                                # owner = global all-branch; manager = branch-specific
                                cc = await _sms_get_cc(org_id, branch_id, {"owner", "manager"})
                                for role_key, rphone in cc.items():
                                    if rphone:
                                        await _raw_db.sms_queue.insert_one({
                                            "id": new_id(), "organization_id": org_id,
                                            "template_key": "overdue_notice_staff",
                                            "customer_id": cust_id, "customer_name": customer.get("name", ""),
                                            "phone": rphone, "message": staff_overdue_msg,
                                            "status": "pending", "trigger": "scheduled",
                                            "trigger_ref": inv.get("id", ""),
                                            "dedup_key": f"overdue_staff:{role_key}:{inv.get('id', '')}:{today}",
                                            "branch_id": branch_id, "branch_name": branch_name,
                                            "created_at": now_iso(),
                                            "sent_at": None, "failed_at": None, "error": None, "retry_count": 0,
                                        })
                            queued += 1

                total_queued += queued
            except Exception as e:
                logger.error(f"SMS reminders failed for org {org_id}: {e}")

        set_org_context(None)
        logger.info("SMS reminders: %d messages queued across %d orgs", total_queued, len(orgs))

    # Monthly summary — 1st of each month
    async def _monthly_sms_summary():
        """Send monthly balance summary per org to all customers with outstanding balance."""
        from routes.sms import queue_sms
        from routes.sms_hooks import get_company_name
        from config import set_org_context
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        orgs = await _raw_db.organizations.find({"active": True}, {"_id": 0, "id": 1}).to_list(500)
        total_queued = 0

        for org in orgs:
            org_id = org["id"]
            try:
                company_name = await get_company_name(org_id)

                customers = await _raw_db.customers.find(
                    {"organization_id": org_id, "active": True, "balance": {"$gt": 0}}, {"_id": 0}
                ).to_list(5000)

                queued = 0
                for c in customers:
                    phone = c.get("phone", "")
                    if not phone:
                        continue
                    # Use live invoice sum — avoids stale customer.balance
                    live_res = await _raw_db.invoices.aggregate([
                        {"$match": {"customer_id": c["id"], "organization_id": org_id,
                                    "status": {"$nin": ["voided", "paid"]}, "balance": {"$gt": 0}}},
                        {"$group": {"_id": None, "total": {"$sum": "$balance"},
                                    "overdue": {"$sum": {"$cond": [{"$lt": ["$due_date", today]}, "$balance", 0]}}}}
                    ]).to_list(1)
                    live_total = round(live_res[0]["total"], 2) if live_res else 0
                    overdue_total = round(live_res[0]["overdue"], 2) if live_res else 0
                    if live_total <= 0:
                        continue  # balance cleared since last customer.balance check

                    await queue_sms(
                        template_key="monthly_summary",
                        customer_id=c["id"],
                        customer_name=c.get("name", ""),
                        phone=phone,
                        variables={
                            "customer_name": c.get("name", ""),
                            "company_name": company_name,
                            "total_balance": f"{live_total:,.2f}",
                            "overdue_amount": f"{overdue_total:,.2f}",
                        },
                        organization_id=org_id,
                        branch_id=c.get("branch_id", ""),
                        trigger="scheduled",
                        trigger_ref=f"monthly:{today}",
                        dedup_key=f"monthly:{c['id']}:{today[:7]}",
                    )
                    queued += 1
                total_queued += queued
            except Exception as e:
                logger.error(f"Monthly SMS summary failed for org {org_id}: {e}")

        logger.info("Monthly SMS summary: %d messages queued across %d orgs", total_queued, len(orgs))

    _scheduler.add_job(
        _daily_sms_reminders,
        CronTrigger(hour=8, minute=0),
        id="daily_sms_reminders",
        replace_existing=True,
    )

    _scheduler.add_job(
        _monthly_sms_summary,
        CronTrigger(day=1, hour=9, minute=0),
        id="monthly_sms_summary",
        replace_existing=True,
    )

    # ── Crop Credit — Harvest Reminders (daily at 7 AM) ─────────────────────
    # Interest accrual via scheduler removed — interest is now managed exclusively
    # through /payments Generate Interest (creates INT-XXXXX invoices).
    from routes.crop_credits import run_harvest_reminders

    _scheduler.add_job(
        run_harvest_reminders,
        CronTrigger(hour=7, minute=0),
        id="crop_harvest_reminders",
        replace_existing=True,
    )

    _scheduler.start()
    logger.info("Backup scheduler started — daily at %02d:00", backup_hour)


@app.on_event("shutdown")
async def shutdown_db_client():
    """Close database connection on shutdown."""
    _scheduler.shutdown(wait=False)
    client.close()


# =============================================================================
# INCLUDE ROUTER & MIDDLEWARE
# =============================================================================
app.include_router(api_router)
app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"]
)


# =============================================================================
# HEALTH CHECK
# =============================================================================
@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/api/reports/test-report-v2")
async def download_test_report_v2():
    """Download the AgriBooks SaaS Platform Test Report v2.0 PDF."""
    path = "/app/AgriBooks_SaaS_Test_Report_v2.pdf"
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Test report not found. Run generate_test_report.py first.")
    return FileResponse(
        path,
        media_type="application/pdf",
        filename="AgriBooks_SaaS_Test_Report_v2.pdf",
        headers={"Content-Disposition": "attachment; filename=AgriBooks_SaaS_Test_Report_v2.pdf"}
    )

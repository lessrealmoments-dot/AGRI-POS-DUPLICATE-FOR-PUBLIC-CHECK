"""
Transaction Verification routes.
Allows authorized personnel to verify POs, Expenses, and Branch Transfers.

PIN hierarchy (in order of check):
  1. Admin PIN  — stored in system_settings as hashed value
  2. Admin TOTP — admin's Google Authenticator code
  3. Auditor PIN — any user with is_auditor=True and auditor_pin set

A verified transaction shows a badge. Discrepancies are noted (not immediately
applied to inventory). They accumulate in the discrepancy_log and are resolved
at the end of the audit via "Apply Correction" or "Dismiss".
"""
from fastapi import APIRouter, Depends, HTTPException
from typing import Optional
from datetime import datetime, timezone
import logging
import pyotp
from config import db
from utils import get_current_user, now_iso, new_id, hash_password, verify_password
from utils.security import log_failed_pin_attempt

logger = logging.getLogger("pin_verify")

router = APIRouter(prefix="/verify", tags=["Verification"])

VERIFIABLE_TYPES = {
    "purchase_order": "db.purchase_orders",
    "expense": "db.expenses",
    "branch_transfer": "db.branch_transfer_orders",
}

COLLECTION_MAP = {
    "purchase_order": "purchase_orders",
    "expense": "expenses",
    "branch_transfer": "branch_transfer_orders",
}


# ─────────────────────────────────────────────────────────────────────────────
#  PIN Verification helper
# ─────────────────────────────────────────────────────────────────────────────

async def _resolve_pin(pin: str, allowed_methods: list = None, branch_id: str = None) -> Optional[dict]:
    """
    Returns verifier info dict if pin matches any of the allowed methods.
    Methods: admin_pin, manager_pin, totp, auditor_pin
    If allowed_methods is None, all methods are checked (backward compatible).

    branch_id: When provided, manager PINs are restricted to managers
    assigned to that branch. Admin PIN, TOTP, and Auditor PIN work on all branches.
    """
    if not pin:
        return None

    pin = str(pin).strip()  # Ensure string and strip whitespace
    check_all = allowed_methods is None
    methods = set(allowed_methods or [])
    logger.info(f"PIN verify: len={len(pin)}, methods={methods or 'ALL'}, branch={branch_id or 'any'}")

    # 1. System Admin PIN (hashed in system_settings) — works on ALL branches
    if check_all or "admin_pin" in methods:
        admin_pin_doc = await db.system_settings.find_one({"key": "admin_pin"}, {"_id": 0})
        if admin_pin_doc:
            stored = admin_pin_doc.get("pin_hash", "")
            if stored and verify_password(pin, stored):
                logger.info("PIN matched: admin_pin (system_settings)")
                return {"verifier_id": "system_admin", "verifier_name": "Admin", "method": "admin_pin"}
            else:
                logger.info(f"Admin PIN check: hash_exists={bool(stored)}, match=False")
        else:
            logger.info("Admin PIN check: no admin_pin doc in system_settings")

    # 2. Manager/Admin PIN (on user documents)
    # Admin/Owner PINs work on ALL branches. Manager PINs only on assigned branch.
    managers = None
    if check_all or "manager_pin" in methods:
        managers = await db.users.find(
            {"role": {"$in": ["admin", "manager", "owner"]}, "active": True}, {"_id": 0}
        ).to_list(50)
        logger.info(f"Manager PIN check: found {len(managers)} admin/manager/owner users")
        for mgr in managers:
            mgr_pin = str(mgr.get("manager_pin", "") or mgr.get("owner_pin", "") or "").strip()
            if mgr_pin and pin == mgr_pin:
                # Branch restriction: manager role PINs only work on their assigned branch
                if branch_id and mgr.get("role") == "manager":
                    mgr_branch = mgr.get("branch_id", "")
                    if mgr_branch and mgr_branch != branch_id:
                        logger.info(f"Manager PIN matched but branch mismatch: mgr_branch={mgr_branch}, requested={branch_id}")
                        continue  # Skip — manager not assigned to this branch
                logger.info(f"PIN matched: manager_pin for user {mgr.get('full_name', mgr.get('username', '?'))}")
                return {
                    "verifier_id": mgr["id"],
                    "verifier_name": mgr.get("full_name", mgr["username"]),
                    "method": "manager_pin",
                }

    # 3. TOTP (6-digit code) — any TOTP-enabled admin/manager/owner user
    if (check_all or "totp" in methods) and len(pin) == 6 and pin.isdigit():
        if managers is None:
            managers = await db.users.find(
                {"role": {"$in": ["admin", "manager", "owner"]}, "active": True}, {"_id": 0}
            ).to_list(50)
        totp_users = [m for m in managers if m.get("totp_secret") and m.get("totp_enabled")]
        logger.info(f"TOTP check: {len(totp_users)} users have TOTP enabled")
        for mgr in totp_users:
            secret = mgr.get("totp_secret")
            totp = pyotp.TOTP(secret)
            if totp.verify(pin, valid_window=1):
                logger.info(f"PIN matched: totp for user {mgr.get('full_name', mgr.get('username', '?'))}")
                return {
                    "verifier_id": mgr["id"],
                    "verifier_name": mgr.get("full_name", mgr["username"]),
                    "method": "totp",
                }
    elif (check_all or "totp" in methods):
        logger.info(f"TOTP skipped: len={len(pin)}, isdigit={pin.isdigit()}")

    # 4. Auditor PIN
    if check_all or "auditor_pin" in methods:
        auditors = await db.users.find(
            {"is_auditor": True, "active": True}, {"_id": 0}
        ).to_list(50)
        for auditor in auditors:
            auditor_pin = str(auditor.get("auditor_pin", "") or "").strip()
            if auditor_pin and pin == auditor_pin:
                return {
                    "verifier_id": auditor["id"],
                    "verifier_name": auditor.get("full_name", auditor["username"]),
                    "method": "auditor_pin",
                }

    # 5. Staff PIN (inventory clerks, warehouse staff, limited permissions)
    if check_all or "staff_pin" in methods:
        staff_users = await db.users.find(
            {
                "role": {"$in": ["staff", "inventory_clerk", "inventory", "cashier"]},
                "active": True,
                "staff_pin": {"$exists": True, "$ne": ""}
            },
            {"_id": 0}
        ).to_list(100)
        logger.info(f"Staff PIN check: found {len(staff_users)} users with staff_pin")
        for staff in staff_users:
            staff_pin = str(staff.get("staff_pin", "") or "").strip()
            if staff_pin and pin == staff_pin:
                # Branch restriction: staff PINs only work on their assigned branch
                if branch_id and staff.get("branch_id"):
                    if staff["branch_id"] != branch_id:
                        logger.info(f"Staff PIN matched but branch mismatch: staff_branch={staff['branch_id']}, requested={branch_id}")
                        continue  # Skip — staff not assigned to this branch
                logger.info(f"PIN matched: staff_pin for user {staff.get('full_name', staff.get('username', '?'))}")
                return {
                    "verifier_id": staff["id"],
                    "verifier_name": staff.get("full_name", staff["username"]),
                    "method": "staff_pin",
                }

    logger.warning(f"PIN verify FAILED: no match found (pin_len={len(pin)}, checked_methods={methods or 'ALL'})")
    return None


# ─────────────────────────────────────────────────────────────────────────────
#  PIN Policy definitions & resolver
# ─────────────────────────────────────────────────────────────────────────────

PIN_METHODS = ["admin_pin", "manager_pin", "totp", "auditor_pin", "staff_pin"]

PIN_POLICY_ACTIONS = [
    # Sales & Invoicing
    {"key": "credit_sale_approval",   "label": "Credit / Partial Sale Approval",  "module": "Sales",             "defaults": ["admin_pin", "manager_pin", "totp"]},
    {"key": "void_invoice",           "label": "Void Invoice",                    "module": "Sales",             "defaults": ["admin_pin", "manager_pin", "totp"]},
    {"key": "void_payment",           "label": "Void Payment on Invoice",         "module": "Sales",             "defaults": ["admin_pin", "manager_pin", "totp"]},
    {"key": "void_return",            "label": "Void Return",                     "module": "Sales",             "defaults": ["admin_pin", "manager_pin", "totp"]},
    {"key": "invoice_edit",           "label": "Edit Posted Invoice",             "module": "Sales",             "defaults": ["admin_pin", "manager_pin", "totp"]},
    {"key": "pos_discount",           "label": "POS Discount / Price Override",   "module": "Sales",             "defaults": ["admin_pin", "manager_pin", "totp"]},
    # Fund Management
    {"key": "fund_transfer_cashier_safe", "label": "Cashier / Safe Transfer",     "module": "Fund Management",   "defaults": ["admin_pin", "manager_pin", "totp"]},
    {"key": "fund_transfer_safe_bank",    "label": "Safe to Bank Deposit",        "module": "Fund Management",   "defaults": ["admin_pin", "totp"]},
    {"key": "fund_transfer_capital_add",  "label": "Capital Injection",           "module": "Fund Management",   "defaults": ["admin_pin", "totp"]},
    # Reversals
    {"key": "reverse_customer_cashout",   "label": "Reverse Customer Cash-Out",   "module": "Reversals",         "defaults": ["admin_pin", "manager_pin", "totp"]},
    {"key": "reverse_employee_advance",   "label": "Reverse Employee Advance",    "module": "Reversals",         "defaults": ["admin_pin", "manager_pin", "totp"]},
    # Daily Operations
    {"key": "daily_close",            "label": "Close Day (Z-Report)",            "module": "Daily Operations",  "defaults": ["admin_pin", "manager_pin", "totp"]},
    {"key": "daily_close_batch",      "label": "Batch Close Days",               "module": "Daily Operations",  "defaults": ["admin_pin", "manager_pin", "totp"]},
    # Inventory & Products
    {"key": "stock_negative_override", "label": "Override Insufficient Stock (Negative Inventory)", "module": "Inventory", "defaults": ["admin_pin", "manager_pin", "totp"]},
    {"key": "inventory_adjust",       "label": "Direct Inventory Correction",     "module": "Inventory",         "defaults": ["admin_pin", "manager_pin", "totp"]},
    {"key": "product_delete",         "label": "Delete Product",                  "module": "Products",          "defaults": ["admin_pin", "totp"]},
    {"key": "price_override",         "label": "Override Branch Price",           "module": "Products",          "defaults": ["admin_pin", "manager_pin", "totp"]},
    {"key": "reopen_po",              "label": "Reopen Purchase Order",           "module": "Purchase Orders",   "defaults": ["admin_pin", "manager_pin", "totp"]},
    {"key": "cancel_po",              "label": "Cancel Purchase Order",           "module": "Purchase Orders",   "defaults": ["admin_pin", "manager_pin", "totp"]},
    # Incident Tickets
    {"key": "incident_resolve",        "label": "Resolve Incident Ticket",         "module": "Incidents",         "defaults": ["admin_pin", "manager_pin", "totp"]},
    # Expenses
    {"key": "void_expense",           "label": "Void / Delete Expense",           "module": "Expenses",          "defaults": ["admin_pin", "manager_pin", "totp"]},
    # Audit & Verification
    {"key": "transaction_verify",     "label": "Verify Transaction (PO/Expense)", "module": "Audit",             "defaults": ["admin_pin", "manager_pin", "totp", "auditor_pin"]},
    {"key": "po_mark_reviewed",       "label": "PO Receipt Review",              "module": "Audit",             "defaults": ["admin_pin", "manager_pin", "totp", "auditor_pin"]},
    {"key": "receipt_mark_reviewed",  "label": "Expense/Transfer Receipt Review", "module": "Audit",             "defaults": ["admin_pin", "manager_pin", "totp", "auditor_pin"]},
    {"key": "public_receipt_verify",  "label": "Public Receipt Verification",    "module": "Audit",             "defaults": ["admin_pin", "manager_pin", "totp", "auditor_pin"]},
    # System
    {"key": "section_override",       "label": "Section Access Override",         "module": "System",            "defaults": ["admin_pin", "manager_pin", "totp"]},
    {"key": "admin_action",           "label": "Admin Action (Bulk Ops)",         "module": "System",            "defaults": ["admin_pin", "totp"]},
    {"key": "backup_restore",         "label": "Restore Backup",                  "module": "System",            "defaults": ["admin_pin", "totp"]},
    # Kiosk / Budget Checker
    {"key": "kiosk_unlock",           "label": "Unlock Kiosk (Budget Checker)",   "module": "System",            "defaults": ["admin_pin", "manager_pin", "totp"]},
    {"key": "kiosk_cost_reveal",      "label": "Reveal Cost in Kiosk",            "module": "System",            "defaults": ["admin_pin", "manager_pin", "totp"]},
    # QR Operational Workflows
    {"key": "qr_release_stocks",      "label": "QR — Release Stocks",            "module": "QR Operations",     "defaults": ["admin_pin", "manager_pin", "totp", "staff_pin"]},
    {"key": "qr_receive_payment",     "label": "QR — Receive Payment",           "module": "QR Operations",     "defaults": ["admin_pin", "manager_pin", "totp"]},
    {"key": "qr_view_payment_history","label": "QR — View Payment History",      "module": "QR Operations",     "defaults": ["admin_pin", "manager_pin", "totp"]},
    {"key": "qr_po_receive",          "label": "QR — Receive Purchase Order",    "module": "QR Operations",     "defaults": ["admin_pin", "manager_pin", "totp"]},
    {"key": "qr_transfer_receive",    "label": "QR — Receive Branch Transfer",   "module": "QR Operations",     "defaults": ["admin_pin", "manager_pin", "totp"]},
    # Cross-branch QR action — TOTP only (no static manager PIN), prevents PIN leakage
    {"key": "qr_cross_branch_action", "label": "QR — Cross-Branch Action (TOTP only)", "module": "QR Operations", "defaults": ["totp"]},
    # Terminal Operations
    {"key": "terminal_pull",          "label": "Terminal — Pull PO or Transfer", "module": "Terminal",          "defaults": ["admin_pin", "manager_pin", "totp"]},
    {"key": "terminal_doc_upload",    "label": "Terminal — Upload Document",     "module": "Terminal",          "defaults": ["admin_pin", "manager_pin", "totp"]},
    # Supplier Payments — fund-source-aware PIN security
    {"key": "pay_po_standard",        "label": "Pay Supplier Invoice (Cash / Safe)", "module": "Purchase Orders", "defaults": ["admin_pin", "manager_pin", "totp"]},
    {"key": "pay_po_bank",            "label": "Pay Supplier Invoice (Bank / Digital)", "module": "Purchase Orders", "defaults": ["admin_pin", "totp"]},
]

# Build quick lookup: action_key → default methods
_ACTION_DEFAULTS = {a["key"]: a["defaults"] for a in PIN_POLICY_ACTIONS}


async def _get_pin_policy() -> dict:
    """Load custom PIN policies from DB, falling back to defaults."""
    doc = await db.system_settings.find_one({"key": "pin_policies"}, {"_id": 0})
    return doc.get("policies", {}) if doc else {}


async def verify_pin_for_action(pin: str, action_key: str, branch_id: str = None) -> Optional[dict]:
    """
    Verify a PIN against the configured policy for a specific action.
    Loads the policy from DB (or uses defaults) and calls _resolve_pin
    with only the allowed methods for that action.

    branch_id: When provided, manager PINs are restricted to managers
    assigned to that branch (admin/owner/TOTP/auditor still work on all branches).
    """
    if not pin:
        return None
    custom = await _get_pin_policy()
    allowed = custom.get(action_key, _ACTION_DEFAULTS.get(action_key))
    if not allowed:
        allowed = ["admin_pin", "manager_pin", "totp"]
    return await _resolve_pin(pin, allowed_methods=allowed, branch_id=branch_id)


# ─────────────────────────────────────────────────────────────────────────────
#  Admin PIN management
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/admin-pin/set")
async def set_admin_pin(data: dict, user=Depends(get_current_user)):
    """Set or change the admin verification PIN (admin only)."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")

    pin = str(data.get("pin", ""))
    if len(pin) < 4:
        raise HTTPException(status_code=400, detail="PIN must be at least 4 digits")

    await db.system_settings.update_one(
        {"key": "admin_pin"},
        {"$set": {
            "key": "admin_pin",
            "pin_hash": hash_password(pin),
            "set_by": user["id"],
            "set_at": now_iso(),
        }},
        upsert=True,
    )
    return {"message": "Admin PIN set successfully"}


@router.get("/admin-pin/status")
async def admin_pin_status(user=Depends(get_current_user)):
    """Check if admin PIN has been set."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    doc = await db.system_settings.find_one({"key": "admin_pin"}, {"_id": 0})
    return {"configured": bool(doc and doc.get("pin_hash"))}


# ─────────────────────────────────────────────────────────────────────────────
#  Verify a transaction
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/{doc_type}/{doc_id}")
async def verify_transaction(
    doc_type: str,
    doc_id: str,
    data: dict,
    user=Depends(get_current_user),
):
    """
    Verify a transaction. Requires valid admin/auditor PIN.
    Optionally records a discrepancy with expected vs found values.
    """
    if doc_type not in COLLECTION_MAP:
        raise HTTPException(status_code=400, detail=f"Invalid document type: {doc_type}. Must be one of: {list(COLLECTION_MAP.keys())}")

    collection = getattr(db, COLLECTION_MAP[doc_type])
    doc = await collection.find_one({"id": doc_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    pin = str(data.get("pin", ""))
    verifier = await verify_pin_for_action(pin, "transaction_verify")
    if not verifier:
        # Log the failed attempt silently — alert admin if threshold exceeded
        doc_label = doc.get("po_number") or doc.get("reference_number") or doc_id[:8]
        await log_failed_pin_attempt(
            user, f"Verify {doc_type}: {doc_label}", "transaction_verify",
            doc_id=doc_id, doc_type=doc_type,
        )
        raise HTTPException(status_code=400, detail="Invalid PIN — not recognized as admin PIN, TOTP, or auditor PIN")

    has_discrepancy = bool(data.get("has_discrepancy", False))
    discrepancy_note = data.get("discrepancy_note", "")
    expected_qty = data.get("expected_qty")
    found_qty = data.get("found_qty")
    unit = data.get("unit", "")
    unit_cost = float(data.get("unit_cost", 0))
    item_description = data.get("item_description", "")

    # Compute value impact
    value_impact = None
    if has_discrepancy and expected_qty is not None and found_qty is not None:
        variance = float(found_qty) - float(expected_qty)
        value_impact = round(variance * unit_cost, 2)

    verification = {
        "verified": True,
        "verified_by_id": verifier["verifier_id"],
        "verified_by_name": verifier["verifier_name"],
        "verified_method": verifier["method"],
        "verified_at": now_iso(),
        "verification_status": "discrepancy" if has_discrepancy else "clean",
        "has_discrepancy": has_discrepancy,
        "discrepancy": {
            "note": discrepancy_note,
            "item_description": item_description,
            "expected_qty": expected_qty,
            "found_qty": found_qty,
            "unit": unit,
            "unit_cost": unit_cost,
            "value_impact": value_impact,
            "resolved": False,
            "resolution": None,
            "resolved_at": None,
            "resolved_by": None,
        } if has_discrepancy else None,
    }

    await collection.update_one({"id": doc_id}, {"$set": verification})

    # Also update receipt_review_status so it stays in sync
    upload_sessions = await db.upload_sessions.find(
        {"record_type": doc_type, "record_id": doc_id, "is_pending": {"$ne": True}},
        {"_id": 0, "file_count": 1}
    ).to_list(20)
    if sum(s.get("file_count", 0) for s in upload_sessions) > 0:
        await collection.update_one({"id": doc_id}, {"$set": {
            "receipt_review_status": "reviewed",
            "receipt_reviewed_by_id": verifier["verifier_id"],
            "receipt_reviewed_by_name": verifier["verifier_name"],
            "receipt_reviewed_at": now_iso(),
        }})

    # Log discrepancy separately for quick querying
    if has_discrepancy:
        branch_id = doc.get("branch_id", "")
        # Get doc date for the report
        doc_date = (
            doc.get("purchase_date") or doc.get("date") or
            doc.get("created_at", "")[:10]
        )
        doc_number = (
            doc.get("po_number") or doc.get("order_number") or
            doc.get("id", "")
        )
        doc_title = (
            doc.get("vendor") or doc.get("description") or doc.get("order_number", "")
        )

        log_entry = {
            "id": new_id(),
            "doc_type": doc_type,
            "doc_id": doc_id,
            "doc_number": doc_number,
            "doc_title": doc_title,
            "doc_date": doc_date,
            "branch_id": branch_id,
            "item_description": item_description,
            "expected_qty": expected_qty,
            "found_qty": found_qty,
            "unit": unit,
            "unit_cost": unit_cost,
            "value_impact": value_impact,
            "note": discrepancy_note,
            "verified_by_name": verifier["verifier_name"],
            "verified_at": now_iso(),
            "resolved": False,
            "resolution": None,
            "resolved_at": None,
            "resolved_by": None,
        }
        await db.discrepancy_log.insert_one(log_entry)

    return {
        "message": "Transaction verified",
        "verified_by": verifier["verifier_name"],
        "method": verifier["method"],
        "status": "discrepancy" if has_discrepancy else "clean",
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Public verify (from phone via view token — no auth required)
# ─────────────────────────────────────────────────────────────────────────────
@router.post("/public/{doc_type}/{doc_id}")
async def verify_transaction_public(
    doc_type: str,
    doc_id: str,
    data: dict,
):
    """
    Public verify endpoint — called from phone's ViewReceiptsPage.
    Validates the admin PIN/TOTP same as the authenticated version,
    but doesn't require login. Security is ensured via the PIN/TOTP itself.
    """
    if doc_type not in COLLECTION_MAP:
        raise HTTPException(status_code=400, detail=f"Invalid document type: {doc_type}")

    collection = getattr(db, COLLECTION_MAP[doc_type])
    doc = await collection.find_one({"id": doc_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    pin = str(data.get("pin", ""))
    verifier = await verify_pin_for_action(pin, "public_receipt_verify")
    if not verifier:
        raise HTTPException(status_code=400, detail="Invalid PIN — not recognized as admin PIN, TOTP, or auditor PIN")

    has_discrepancy = bool(data.get("has_discrepancy", False))
    discrepancy_note = data.get("discrepancy_note", "")
    expected_qty = data.get("expected_qty")
    found_qty = data.get("found_qty")
    unit_var = data.get("unit", "")
    unit_cost = float(data.get("unit_cost", 0))
    item_description = data.get("item_description", "")

    value_impact = None
    if has_discrepancy and expected_qty is not None and found_qty is not None:
        variance = float(found_qty) - float(expected_qty)
        value_impact = round(variance * unit_cost, 2)

    verification = {
        "verified": True,
        "verified_by_id": verifier["verifier_id"],
        "verified_by_name": verifier["verifier_name"],
        "verified_method": verifier["method"],
        "verified_at": now_iso(),
        "verification_status": "discrepancy" if has_discrepancy else "clean",
        "has_discrepancy": has_discrepancy,
        "discrepancy": {
            "note": discrepancy_note,
            "item_description": item_description,
            "expected_qty": expected_qty,
            "found_qty": found_qty,
            "unit": unit_var,
            "unit_cost": unit_cost,
            "value_impact": value_impact,
            "resolved": False,
            "resolution": None,
            "resolved_at": None,
            "resolved_by": None,
        } if has_discrepancy else None,
    }

    await collection.update_one({"id": doc_id}, {"$set": verification})

    # Also update receipt_review_status so desktop shows this as reviewed
    # (bridges the gap between "verify transaction" and "review receipts")
    upload_sessions = await db.upload_sessions.find(
        {"record_type": doc_type, "record_id": doc_id, "is_pending": {"$ne": True}},
        {"_id": 0, "file_count": 1}
    ).to_list(20)
    if sum(s.get("file_count", 0) for s in upload_sessions) > 0:
        await collection.update_one({"id": doc_id}, {"$set": {
            "receipt_review_status": "reviewed",
            "receipt_reviewed_by_id": verifier["verifier_id"],
            "receipt_reviewed_by_name": verifier["verifier_name"],
            "receipt_reviewed_at": now_iso(),
        }})

    # Log discrepancy
    if has_discrepancy:
        branch_id = doc.get("branch_id", "")
        doc_date = doc.get("purchase_date") or doc.get("date") or doc.get("created_at", "")[:10]
        doc_number = doc.get("po_number") or doc.get("order_number") or doc.get("id", "")
        doc_title = doc.get("vendor") or doc.get("description") or doc.get("order_number", "")
        log_entry = {
            "id": new_id(),
            "doc_type": doc_type,
            "doc_id": doc_id,
            "doc_number": doc_number,
            "doc_title": doc_title,
            "doc_date": doc_date,
            "branch_id": branch_id,
            "item_description": item_description,
            "expected_qty": expected_qty,
            "found_qty": found_qty,
            "unit": unit_var,
            "unit_cost": unit_cost,
            "value_impact": value_impact,
            "note": discrepancy_note,
            "verified_by_name": verifier["verifier_name"],
            "verified_at": now_iso(),
            "resolved": False,
        }
        await db.discrepancy_log.insert_one(log_entry)

    return {
        "message": "Transaction verified",
        "verified_by": verifier["verifier_name"],
        "method": verifier["method"],
        "status": "discrepancy" if has_discrepancy else "clean",
    }


@router.delete("/{doc_type}/{doc_id}")
async def unverify_transaction(
    doc_type: str,
    doc_id: str,
    user=Depends(get_current_user),
):
    """Remove verification from a transaction (admin only)."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    if doc_type not in COLLECTION_MAP:
        raise HTTPException(status_code=400, detail="Invalid document type")

    collection = getattr(db, COLLECTION_MAP[doc_type])
    await collection.update_one(
        {"id": doc_id},
        {"$unset": {
            "verified": "", "verified_by_id": "", "verified_by_name": "",
            "verified_method": "", "verified_at": "", "verification_status": "",
            "has_discrepancy": "", "discrepancy": "",
        }}
    )
    # Remove from discrepancy log
    await db.discrepancy_log.delete_many({"doc_id": doc_id, "resolved": False})
    return {"message": "Verification removed"}


# ─────────────────────────────────────────────────────────────────────────────
#  Discrepancy Report
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/discrepancies")
async def list_discrepancies(
    branch_id: Optional[str] = None,
    resolved: Optional[bool] = None,
    doc_type: Optional[str] = None,
    user=Depends(get_current_user),
):
    """List all discrepancy log entries."""
    query = {}
    if branch_id:
        query["branch_id"] = branch_id
    elif user.get("branch_id") and user.get("role") not in ["admin"]:
        query["branch_id"] = user["branch_id"]
    if resolved is not None:
        query["resolved"] = resolved
    if doc_type:
        query["doc_type"] = doc_type

    entries = await db.discrepancy_log.find(query, {"_id": 0}).sort("verified_at", -1).to_list(500)
    return {"discrepancies": entries, "total": len(entries)}


@router.post("/discrepancies/{log_id}/resolve")
async def resolve_discrepancy(
    log_id: str,
    data: dict,
    user=Depends(get_current_user),
):
    """
    Resolve a discrepancy:
      action='apply'  → creates an inventory correction (if applicable)
      action='dismiss' → marks as dismissed with justification
    """
    entry = await db.discrepancy_log.find_one({"id": log_id}, {"_id": 0})
    if not entry:
        raise HTTPException(status_code=404, detail="Discrepancy log entry not found")
    if entry.get("resolved"):
        raise HTTPException(status_code=400, detail="Already resolved")

    action = data.get("action", "dismiss")  # 'apply' | 'dismiss'
    justification = data.get("justification", "")

    resolution_note = f"{action.upper()}: {justification}" if justification else action.upper()

    await db.discrepancy_log.update_one(
        {"id": log_id},
        {"$set": {
            "resolved": True,
            "resolution": action,
            "resolution_note": resolution_note,
            "resolved_at": now_iso(),
            "resolved_by": user.get("full_name", user["username"]),
        }}
    )

    # Also update the parent document's discrepancy.resolved flag
    if entry.get("doc_type") and entry.get("doc_id") and entry["doc_type"] in COLLECTION_MAP:
        collection = getattr(db, COLLECTION_MAP[entry["doc_type"]])
        await collection.update_one(
            {"id": entry["doc_id"]},
            {"$set": {
                "discrepancy.resolved": True,
                "discrepancy.resolution": resolution_note,
                "discrepancy.resolved_at": now_iso(),
                "discrepancy.resolved_by": user.get("full_name", user["username"]),
                "verification_status": "resolved",
            }}
        )

    return {"message": f"Discrepancy {action}d successfully"}

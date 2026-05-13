"""Historical Supplier PO Encoding — Pre-system AP Carry-Forward.

Admin/Owner-only workflow for safely encoding supplier debts that existed
BEFORE the org went live on AgriBooks. Mirrors the customer-side
`historical_credit_encoding` pattern but for the AP/supplier side.

Why a dedicated collection (not a regular PO)?
─────────────────────────────────────────────
A regular PO with a real product line either inflates inventory (if the
SKU is stockable) or pollutes COGS / expense reports for the current
period — both create false reports. A separate `historical_supplier_pos`
collection keeps the liability visible on AP without any side effects on
inventory or P&L.

Lifecycle
─────────
  outstanding  — created with `amount` owed; balance = amount.
  partial      — at least one payment recorded; 0 < balance < amount.
  paid         — balance == 0.
  voided       — admin-deleted; balance frozen at last value.

Auth model
──────────
  * Role: admin / owner / super_admin only (`assert_admin_or_owner`).
  * PIN: `historical_supplier_po_add` policy in `verify.py`, defaults to
    `["admin_pin", "totp"]` — NO `manager_pin`. This is the user's
    explicit rule: "managers can't do this, they need to show me proof
    first." Even if a manager's role is somehow elevated, the PIN gate
    hard-stops them.
  * Payments: `historical_supplier_po_pay` policy, same gate.
  * Voids: `historical_supplier_po_void` policy, same gate.

Reports
───────
This collection is intentionally NOT included in expense / COGS / sales
reports. It is surfaced only on:
  * `GET /api/historical-supplier-pos` (admin AP review)
  * The `accounts_payable_summary` dashboard endpoint (additive entries
    alongside regular POs so the owner sees the FULL real-world AP).

See `/app/memory/PRD.md` Phase 3.2 entry for the spec.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from config import db
from utils import (
    get_current_user, now_iso, new_id, today_local,
    assert_branch_access, assert_admin_or_owner,
)
from routes.verify import verify_pin_for_action
from routes.accounting import deduct_from_fund_source, derive_fund_source
from utils.numbering import generate_next_number

router = APIRouter(prefix="/historical-supplier-pos",
                    tags=["Historical Supplier PO"])


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────
async def _verify_admin_pin(action_key: str, code: str, branch_id: str):
    """Verify TOTP or admin PIN — NEVER manager PIN. Returns verifier
    dict or raises 400/403 with actionable detail."""
    code = (code or "").strip()
    if not code:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "approval_code_required",
                "message": "Admin PIN or TOTP is required. Managers cannot "
                           "create or modify historical supplier POs — ask "
                           "the company owner / admin to enter their PIN.",
            },
        )
    verifier = await verify_pin_for_action(code, action_key, branch_id=branch_id)
    if not verifier:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "approval_invalid",
                "message": "Invalid PIN. This action requires the admin "
                           "PIN or a TOTP code from an owner / admin / "
                           "super-admin. Manager PINs are not accepted.",
            },
        )
    return verifier


def _doc_to_summary(doc: dict) -> dict:
    """Shape an HS-PO doc for list/summary responses (excludes `_id` and
    payments[] detail to keep responses small)."""
    return {
        "id": doc.get("id", ""),
        "reference_number": doc.get("reference_number", ""),
        "supplier_name": doc.get("supplier_name", ""),
        "branch_id": doc.get("branch_id", ""),
        "branch_name": doc.get("branch_name", ""),
        "pre_system_date": doc.get("pre_system_date", ""),
        "amount": float(doc.get("amount", 0) or 0),
        "amount_paid": float(doc.get("amount_paid", 0) or 0),
        "balance": float(doc.get("balance", 0) or 0),
        "status": doc.get("status", ""),
        "description": doc.get("description", ""),
        "created_at": doc.get("created_at", ""),
        "created_by_name": doc.get("created_by_name", ""),
        "approval_method": doc.get("approval_method", ""),
        "approved_by_name": doc.get("approved_by_name", ""),
        "payment_count": len(doc.get("payments", []) or []),
    }


# ─────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────
@router.post("")
async def create_historical_supplier_po(
    data: dict, user=Depends(get_current_user)
):
    """Encode a pre-system supplier PO that has an outstanding balance.

    Required body:
      supplier_name, branch_id, pre_system_date (YYYY-MM-DD), amount, pin
    Optional:
      reference_number, description (≥10 chars recommended for audit)

    The entry shows on the AP dashboard but does NOT touch inventory,
    COGS, or current-period expense. Cash flow happens only when the
    user records a payment via `/pay`.
    """
    assert_admin_or_owner(user)

    supplier_name = (data.get("supplier_name") or "").strip()
    branch_id = (data.get("branch_id") or "").strip()
    pre_system_date = (data.get("pre_system_date") or "").strip()
    reference_number = (data.get("reference_number") or "").strip()
    description = (data.get("description") or "").strip()

    if not supplier_name:
        raise HTTPException(status_code=400, detail="supplier_name is required")
    if not branch_id:
        raise HTTPException(status_code=400, detail="branch_id is required")
    if not pre_system_date:
        raise HTTPException(
            status_code=400,
            detail="pre_system_date (YYYY-MM-DD) is required",
        )
    try:
        amount = round(float(data.get("amount") or 0), 2)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="amount must be a number")
    if amount <= 0:
        raise HTTPException(status_code=400, detail="amount must be > 0")

    # Soft-floor — pre_system_date must actually be BEFORE today (else
    # the user is just trying to make a regular PO with this flow).
    today = await today_local(user.get("organization_id") or "")
    if pre_system_date >= today:
        raise HTTPException(
            status_code=400,
            detail=(
                f"pre_system_date {pre_system_date} must be before today "
                f"({today}). This flow is for OLD supplier debt only — "
                f"use a regular Purchase Order for current purchases."
            ),
        )

    assert_branch_access(user, branch_id)

    # Admin PIN / TOTP — manager PIN is rejected.
    verifier = await _verify_admin_pin(
        "historical_supplier_po_add",
        data.get("pin"),
        branch_id=branch_id,
    )

    branch = await db.branches.find_one({"id": branch_id}, {"_id": 0, "name": 1})
    branch_name = branch.get("name", "") if branch else ""

    # Auto-generate a standard reference_number when the operator leaves it
    # blank. Format mirrors regular POs (`PO-{BC}-NNNNNN`) but uses the
    # distinct `HPO` prefix so historical (pre-system) entries are visually
    # distinguishable from active POs on every screen / report — and they
    # share no sequence with active POs (own collection, own counter).
    if not reference_number:
        reference_number = await generate_next_number("HPO", branch_id)

    doc = {
        "id": new_id(),
        "reference_number": reference_number,
        "supplier_name": supplier_name,
        "branch_id": branch_id,
        "branch_name": branch_name,
        "pre_system_date": pre_system_date,
        "amount": amount,
        "amount_paid": 0.0,
        "balance": amount,
        "status": "outstanding",
        "description": description,
        "payments": [],
        "created_at": now_iso(),
        "created_by_id": user.get("id", ""),
        "created_by_name": user.get("full_name") or user.get("username", ""),
        "approval_method": verifier.get("method", ""),
        "approved_by_id": verifier.get("verifier_id", ""),
        "approved_by_name": verifier.get("verifier_name", ""),
    }
    await db.historical_supplier_pos.insert_one(doc)
    del doc["_id"]

    # Audit trail (auto-tenant-scoped via TENANT_COLLECTIONS).
    await db.audit_log.insert_one({
        "id": new_id(),
        "type": "historical_supplier_po_created",
        "entity_type": "historical_supplier_po",
        "entity_id": doc["id"],
        "description": (
            f"Historical supplier PO encoded: {supplier_name} "
            f"₱{amount:,.2f} dated {pre_system_date} "
            f"by {doc['created_by_name']} via {verifier.get('method', '')}"
        ),
        "metadata": {
            "supplier_name": supplier_name,
            "amount": amount,
            "pre_system_date": pre_system_date,
            "reference_number": reference_number,
            "approved_by_id": verifier.get("verifier_id", ""),
            "approved_by_name": verifier.get("verifier_name", ""),
            "approval_method": verifier.get("method", ""),
        },
        "branch_id": branch_id,
        "user_id": user.get("id", ""),
        "user_name": doc["created_by_name"],
        "created_at": now_iso(),
    })

    return _doc_to_summary(doc)


@router.get("")
async def list_historical_supplier_pos(
    status: Optional[str] = None,
    branch_id: Optional[str] = None,
    supplier_name: Optional[str] = None,
    user=Depends(get_current_user),
):
    """List historical supplier POs visible to the user. Admins see all
    branches; non-admins see only their assigned branches."""
    assert_admin_or_owner(user)
    q: dict = {}
    if status:
        q["status"] = status
    if branch_id:
        assert_branch_access(user, branch_id)
        q["branch_id"] = branch_id
    if supplier_name:
        q["supplier_name"] = {"$regex": supplier_name.strip(), "$options": "i"}
    rows = await db.historical_supplier_pos.find(q, {"_id": 0}).sort(
        "created_at", -1).to_list(500)

    outstanding_total = round(sum(
        float(r.get("balance", 0) or 0) for r in rows
        if r.get("status") in ("outstanding", "partial")
    ), 2)
    return {
        "rows": [_doc_to_summary(r) for r in rows],
        "outstanding_total": outstanding_total,
        "outstanding_count": sum(1 for r in rows
                                  if r.get("status") in ("outstanding", "partial")),
    }


@router.get("/{po_id}")
async def get_historical_supplier_po(
    po_id: str, user=Depends(get_current_user)
):
    """Get a single historical supplier PO with full payment history."""
    assert_admin_or_owner(user)
    doc = await db.historical_supplier_pos.find_one({"id": po_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Historical PO not found")
    assert_branch_access(user, doc.get("branch_id", ""))
    return doc


@router.post("/{po_id}/pay")
async def pay_historical_supplier_po(
    po_id: str, data: dict, user=Depends(get_current_user)
):
    """Record a payment against a historical supplier PO. Deducts from
    the chosen fund wallet (cashier / safe / digital / bank) using the
    same `deduct_from_fund_source` helper that the regular supplier-PO
    `/pay` endpoint uses.

    Body: pin, amount, payment_method, fund_source?, reference?, note?
    """
    assert_admin_or_owner(user)

    doc = await db.historical_supplier_pos.find_one({"id": po_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Historical PO not found")
    if doc.get("status") in ("paid", "voided"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot pay a historical PO in status '{doc.get('status')}'",
        )
    assert_branch_access(user, doc.get("branch_id", ""))

    try:
        amount = round(float(data.get("amount") or 0), 2)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="amount must be a number")
    if amount <= 0:
        raise HTTPException(status_code=400, detail="amount must be > 0")
    current_balance = float(doc.get("balance", 0) or 0)
    if amount > current_balance + 0.01:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Payment ₱{amount:,.2f} exceeds outstanding balance "
                f"₱{current_balance:,.2f}."
            ),
        )

    payment_method = (data.get("payment_method") or "Cash").strip()
    fund_source = derive_fund_source(payment_method, data.get("fund_source"))
    reference = (data.get("reference") or "").strip()
    note = (data.get("note") or "").strip()

    verifier = await _verify_admin_pin(
        "historical_supplier_po_pay",
        data.get("pin"),
        branch_id=doc.get("branch_id", ""),
    )

    ref_text = (
        f"Historical PO {doc.get('reference_number') or doc['id'][:8]} — "
        f"{doc.get('supplier_name', '')} (pre-system)"
    )
    # Deducts from fund wallet — raises 400 on insufficient funds.
    await deduct_from_fund_source(
        doc.get("branch_id", ""),
        fund_source,
        amount,
        ref_text,
        payment_method=payment_method,
    )

    new_amount_paid = round(float(doc.get("amount_paid", 0) or 0) + amount, 2)
    new_balance = round(float(doc.get("amount", 0) or 0) - new_amount_paid, 2)
    new_status = "paid" if new_balance <= 0.005 else "partial"

    payment_row = {
        "id": new_id(),
        "amount": amount,
        "payment_method": payment_method,
        "fund_source": fund_source,
        "reference": reference,
        "note": note,
        "paid_at": now_iso(),
        "paid_by_id": user.get("id", ""),
        "paid_by_name": user.get("full_name") or user.get("username", ""),
        "approval_method": verifier.get("method", ""),
        "approved_by_id": verifier.get("verifier_id", ""),
        "approved_by_name": verifier.get("verifier_name", ""),
    }
    await db.historical_supplier_pos.update_one(
        {"id": po_id},
        {
            "$set": {
                "amount_paid": new_amount_paid,
                "balance": new_balance,
                "status": new_status,
                "updated_at": now_iso(),
            },
            "$push": {"payments": payment_row},
        },
    )

    await db.audit_log.insert_one({
        "id": new_id(),
        "type": "historical_supplier_po_paid",
        "entity_type": "historical_supplier_po",
        "entity_id": po_id,
        "description": (
            f"Paid ₱{amount:,.2f} on historical PO "
            f"{doc.get('reference_number') or po_id[:8]} "
            f"({doc.get('supplier_name', '')}) via {payment_method}/{fund_source} "
            f"by {payment_row['paid_by_name']}. Remaining: ₱{new_balance:,.2f}"
        ),
        "metadata": {
            "po_id": po_id,
            "supplier_name": doc.get("supplier_name", ""),
            "amount": amount,
            "payment_method": payment_method,
            "fund_source": fund_source,
            "new_balance": new_balance,
            "new_status": new_status,
            "approval_method": verifier.get("method", ""),
            "approved_by_id": verifier.get("verifier_id", ""),
            "approved_by_name": verifier.get("verifier_name", ""),
        },
        "branch_id": doc.get("branch_id", ""),
        "user_id": user.get("id", ""),
        "user_name": payment_row["paid_by_name"],
        "created_at": now_iso(),
    })

    updated = await db.historical_supplier_pos.find_one(
        {"id": po_id}, {"_id": 0})
    return _doc_to_summary(updated)


@router.post("/{po_id}/void")
async def void_historical_supplier_po(
    po_id: str, data: dict, user=Depends(get_current_user)
):
    """Soft-delete a historical supplier PO (data-entry error etc.).
    Cannot be applied to a fully-paid PO."""
    assert_admin_or_owner(user)
    doc = await db.historical_supplier_pos.find_one({"id": po_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Historical PO not found")
    if doc.get("status") == "paid":
        raise HTTPException(
            status_code=400,
            detail="Cannot void a fully-paid historical PO (would erase a real payment trail).",
        )
    if doc.get("status") == "voided":
        raise HTTPException(status_code=400, detail="Already voided.")
    assert_branch_access(user, doc.get("branch_id", ""))

    reason = (data.get("reason") or "").strip()
    if len(reason) < 10:
        raise HTTPException(
            status_code=400,
            detail="reason (≥10 chars) is required to void.",
        )

    verifier = await _verify_admin_pin(
        "historical_supplier_po_void",
        data.get("pin"),
        branch_id=doc.get("branch_id", ""),
    )

    await db.historical_supplier_pos.update_one(
        {"id": po_id},
        {"$set": {
            "status": "voided",
            "void_reason": reason,
            "voided_at": now_iso(),
            "voided_by_id": user.get("id", ""),
            "voided_by_name": user.get("full_name") or user.get("username", ""),
            "void_approval_method": verifier.get("method", ""),
            "void_approved_by_id": verifier.get("verifier_id", ""),
            "void_approved_by_name": verifier.get("verifier_name", ""),
            "updated_at": now_iso(),
        }},
    )
    await db.audit_log.insert_one({
        "id": new_id(),
        "type": "historical_supplier_po_voided",
        "entity_type": "historical_supplier_po",
        "entity_id": po_id,
        "description": (
            f"Voided historical PO {doc.get('reference_number') or po_id[:8]} "
            f"({doc.get('supplier_name', '')}): {reason}"
        ),
        "metadata": {
            "po_id": po_id,
            "reason": reason,
            "approval_method": verifier.get("method", ""),
            "approved_by_name": verifier.get("verifier_name", ""),
        },
        "branch_id": doc.get("branch_id", ""),
        "user_id": user.get("id", ""),
        "user_name": user.get("full_name") or user.get("username", ""),
        "created_at": now_iso(),
    })

    return {"id": po_id, "status": "voided"}

"""Phase 3 — Historical Credit Encoding / Notebook AR.

Admin-only workflow for safely reconstructing customer credit sales that
were originally written in a notebook but never encoded into the POS.
This is NOT a parallel sales system — it produces a regular `invoice`
document (so customer ledger / receivables / payment receipt / Z-report
late-encode section all work unchanged) but tags it with
`source = "historical_credit_encoding"` and the full audit context
required by the Phase 3 business rules.

See `/app/memory/PHASE_3_HISTORICAL_CREDIT.md` for the workflow spec.
"""
from datetime import datetime, timezone
from typing import Optional, Any

from fastapi import APIRouter, Depends, HTTPException

from config import db
from utils import (
    get_current_user, now_iso, new_id, today_local,
    assert_branch_access, assert_admin_or_owner,
    generate_next_number,
)

router = APIRouter(prefix="/historical-credit", tags=["Historical Credit"])

REASON_MIN_LENGTH = 20
SOURCE_TAG = "historical_credit_encoding"


# ─────────────────────────────────────────────────────────────────────
# Validation
# ─────────────────────────────────────────────────────────────────────
def _validate_payload(data: dict, today: str) -> dict:
    """Raise 400 with a clear message if the payload is invalid.

    Returns a normalised dict on success. Does NOT touch the DB.
    """
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Body must be a JSON object")

    customer_id = (data.get("customer_id") or "").strip()
    branch_id = (data.get("branch_id") or "").strip()
    transaction_date = (data.get("transaction_date") or "").strip()
    reason = (data.get("reason") or "").strip()
    grand_total = data.get("grand_total")
    items = data.get("items") or []

    errors: list[str] = []

    if not customer_id:
        errors.append("customer_id is required")
    if not branch_id:
        errors.append("branch_id is required")
    if not transaction_date:
        errors.append("transaction_date is required")
    elif len(transaction_date) < 10 or transaction_date[4] != "-" or transaction_date[7] != "-":
        errors.append("transaction_date must be YYYY-MM-DD")
    elif transaction_date[:10] > today[:10]:
        errors.append("transaction_date cannot be in the future")
    elif transaction_date[:10] == today[:10]:
        errors.append(
            "transaction_date is today — historical credit encoding is for "
            "PAST notebook entries; use the normal POS for today's credit sale.")

    if len(reason) < REASON_MIN_LENGTH:
        errors.append(
            f"reason must be at least {REASON_MIN_LENGTH} characters "
            f"(got {len(reason)}). Describe why this notebook AR was not "
            "encoded earlier and how it was verified."
        )

    try:
        gt = float(grand_total)
    except (TypeError, ValueError):
        gt = -1.0
    if gt <= 0:
        errors.append("grand_total must be a positive number")

    if not isinstance(items, list) or not items:
        errors.append("items must be a non-empty list")

    if errors:
        raise HTTPException(status_code=400, detail={"errors": errors})

    return {
        "customer_id": customer_id,
        "branch_id": branch_id,
        "transaction_date": transaction_date[:10],
        "reason": reason,
        "grand_total": round(gt, 2),
        "items": items,
        "proof_url": (data.get("proof_url") or "").strip() or None,
        "notebook_reference": (data.get("notebook_reference") or "").strip() or None,
        "allow_inventory_deduction": bool(data.get("allow_inventory_deduction", False)),
        "subtotal": float(data.get("subtotal") or gt),
        "freight": float(data.get("freight") or 0),
        "overall_discount": float(data.get("overall_discount") or 0),
    }


# ─────────────────────────────────────────────────────────────────────
# Count-sheet stopper
# ─────────────────────────────────────────────────────────────────────
async def _count_sheet_stopper(branch_id: str, transaction_date: str,
                                 product_ids: list[str]) -> dict:
    """Determine whether deducting inventory is safe given the latest
    completed (= "approved") physical count sheet for this branch.

    Returns a dict:
      {
        "latest_count_at": ISO or None,
        "latest_count_date": YYYY-MM-DD or None,
        "transaction_before_or_on_count": bool,
        "blocked_product_ids": [list of products that were on the latest sheet],
      }

    Caller decides what to do:
      * `transaction_before_or_on_count == False` → safe to deduct.
      * `transaction_before_or_on_count == True`  →
            do NOT silently deduct unless the admin opts in via
            `allow_inventory_deduction=true`.
    """
    latest = await db.count_sheets.find_one(
        {"branch_id": branch_id, "status": "completed"},
        {"_id": 0, "completed_at": 1, "items": 1},
        sort=[("completed_at", -1)],
    )
    if not latest:
        return {
            "latest_count_at": None,
            "latest_count_date": None,
            "transaction_before_or_on_count": False,
            "blocked_product_ids": [],
            "no_count_sheet": True,
        }
    completed_at = latest.get("completed_at") or ""
    completed_date = completed_at[:10]
    is_blocked = bool(completed_date and transaction_date <= completed_date)
    sheet_pids = {
        i.get("product_id") for i in (latest.get("items") or [])
        if i.get("product_id")
    }
    blocked = [pid for pid in product_ids if pid in sheet_pids] if is_blocked else []
    return {
        "latest_count_at": completed_at,
        "latest_count_date": completed_date,
        "transaction_before_or_on_count": is_blocked,
        "blocked_product_ids": blocked,
        "no_count_sheet": False,
    }


def _classify_inventory_action(stopper: dict, allow_override: bool) -> str:
    """Map the stopper result to the canonical action tag stored on the
    invoice. One of:
      * `deducted`                            — transaction is after the
        latest count or no count sheet exists; inventory deducted.
      * `skipped_count_sheet_lock`            — transaction predates the
        latest count and admin did NOT opt in; inventory NOT deducted.
        Pure AR reconstruction.
      * `deducted_with_admin_acknowledgement` — transaction predates the
        latest count and admin explicitly opted in via
        `allow_inventory_deduction=true`. Inventory deducted with audit.
    """
    if not stopper["transaction_before_or_on_count"]:
        return "deducted"
    return "deducted_with_admin_acknowledgement" if allow_override else "skipped_count_sheet_lock"


# ─────────────────────────────────────────────────────────────────────
# Preview (no mutation)
# ─────────────────────────────────────────────────────────────────────
@router.post("/preview")
async def preview_historical_credit(data: dict, user=Depends(get_current_user)):
    """Dry-run: validate payload, run the count-sheet stopper, return the
    proposed effects. NEVER mutates state."""
    assert_admin_or_owner(user)
    today = await today_local(user.get("organization_id") or "")
    payload = _validate_payload(data, today)
    assert_branch_access(user, payload["branch_id"])

    customer = await db.customers.find_one({"id": payload["customer_id"]}, {"_id": 0})
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    if customer.get("branch_id") and customer["branch_id"] != payload["branch_id"]:
        raise HTTPException(
            status_code=400,
            detail="Customer is assigned to a different branch.",
        )

    pids = [i.get("product_id") for i in payload["items"] if i.get("product_id")]
    stopper = await _count_sheet_stopper(
        payload["branch_id"], payload["transaction_date"], pids,
    )
    action = _classify_inventory_action(stopper, payload["allow_inventory_deduction"])

    return {
        "ok": True,
        "preview": True,
        "customer": {
            "id": customer.get("id"),
            "name": customer.get("name"),
            "current_balance": float(customer.get("balance", 0)),
            "projected_balance": round(
                float(customer.get("balance", 0)) + payload["grand_total"], 2
            ),
        },
        "transaction_date": payload["transaction_date"],
        "encoded_at": now_iso(),
        "grand_total": payload["grand_total"],
        "count_sheet_stopper": stopper,
        "inventory_action": action,
        "report_effect": {
            "today_cash_collected": "no change (this is AR, not cash)",
            "today_z_report_late_encoded_section": "will appear",
            "old_closed_z_report": "no change "
                "(historical_credit_encoding is filtered out of the regular "
                "sales section; appears only in the late-encoded section "
                "of the current open day)",
            "encoded_today_report": "will appear",
            "customer_ledger": "will appear with transaction_date and encoded_at",
        },
        "audit_log": {
            "encoded_by": user.get("id"),
            "encoded_by_name": user.get("full_name") or user.get("username"),
            "reason": payload["reason"],
            "proof_url": payload["proof_url"],
            "notebook_reference": payload["notebook_reference"],
        },
    }


# ─────────────────────────────────────────────────────────────────────
# Commit
# ─────────────────────────────────────────────────────────────────────
@router.post("")
async def create_historical_credit(data: dict, user=Depends(get_current_user)):
    """Commit a historical credit encoding entry.

    Effects:
      1. Insert an invoice with `source = "historical_credit_encoding"`,
         `late_encoded = True`, status = `credit`, payment_type = `credit`.
      2. Increment `customer.balance` by `grand_total`.
      3. Apply (or skip) inventory deduction per the count-sheet stopper.
      4. Write `late_encode_log` row + `security_events` row.
      5. Return the created invoice + audit metadata.
    """
    assert_admin_or_owner(user)
    today = await today_local(user.get("organization_id") or "")
    payload = _validate_payload(data, today)
    assert_branch_access(user, payload["branch_id"])

    customer = await db.customers.find_one({"id": payload["customer_id"]}, {"_id": 0})
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    if customer.get("branch_id") and customer["branch_id"] != payload["branch_id"]:
        raise HTTPException(status_code=400,
                            detail="Customer is assigned to a different branch.")

    pids = [i.get("product_id") for i in payload["items"] if i.get("product_id")]
    stopper = await _count_sheet_stopper(
        payload["branch_id"], payload["transaction_date"], pids,
    )
    action = _classify_inventory_action(stopper, payload["allow_inventory_deduction"])

    invoice_id = new_id()
    invoice_number = await generate_next_number(payload["branch_id"], "SI")
    encoded_at = now_iso()
    encoded_by = user.get("id")
    encoded_by_name = user.get("full_name") or user.get("username") or ""

    invoice: dict[str, Any] = {
        "id": invoice_id,
        "invoice_number": invoice_number,
        "branch_id": payload["branch_id"],
        "customer_id": payload["customer_id"],
        "customer_name": customer.get("name") or "Customer",
        "order_date": payload["transaction_date"],
        "invoice_date": payload["transaction_date"],
        "due_date": payload["transaction_date"],
        "items": payload["items"],
        "subtotal": round(payload["subtotal"], 2),
        "freight": round(payload["freight"], 2),
        "overall_discount": round(payload["overall_discount"], 2),
        "grand_total": payload["grand_total"],
        "amount_paid": 0.0,
        "balance": payload["grand_total"],
        "payment_type": "credit",
        "status": "credit",
        "sale_type": "historical_credit",
        "payments": [],
        # Phase 3 audit context
        "source": SOURCE_TAG,
        "late_encoded": True,
        "late_encoded_at": encoded_at,
        "late_encode_reason": payload["reason"],
        "late_encoded_by": encoded_by,
        "late_encoded_by_name": encoded_by_name,
        "approved_by": encoded_by,
        "approved_by_name": encoded_by_name,
        "approved_at": encoded_at,
        "historical_credit_proof_url": payload["proof_url"],
        "historical_credit_notebook_ref": payload["notebook_reference"],
        "historical_credit_inventory_action": action,
        "historical_credit_count_sheet_anchor": stopper.get("latest_count_at"),
        "cashier_id": encoded_by,
        "cashier_name": encoded_by_name,
        "created_at": encoded_at,
    }

    await db.invoices.insert_one(invoice)
    invoice.pop("_id", None)

    # 2 — Increment customer balance (AR up)
    await db.customers.update_one(
        {"id": payload["customer_id"]},
        {"$inc": {"balance": payload["grand_total"]},
         "$set": {"updated_at": encoded_at}},
    )

    # 3 — Inventory deduction (only when allowed)
    inventory_movements: list[dict] = []
    if action in ("deducted", "deducted_with_admin_acknowledgement"):
        for item in payload["items"]:
            pid = item.get("product_id")
            qty = float(item.get("quantity") or 0)
            if not pid or qty <= 0:
                continue
            await db.inventory.update_one(
                {"product_id": pid, "branch_id": payload["branch_id"]},
                {"$inc": {"quantity": -qty},
                 "$set": {"updated_at": encoded_at}},
                upsert=True,
            )
            mv_id = new_id()
            await db.movements.insert_one({
                "id": mv_id,
                "product_id": pid,
                "branch_id": payload["branch_id"],
                "type": "historical_credit_sale",
                "quantity_change": -qty,
                "reference_id": invoice_id,
                "reference_number": invoice_number,
                "price_at_time": float(item.get("rate") or item.get("price") or 0),
                "user_id": encoded_by,
                "user_name": encoded_by_name,
                "notes": (
                    f"Historical credit encoding "
                    f"({'admin override' if action == 'deducted_with_admin_acknowledgement' else 'after count'})"
                ),
                "created_at": encoded_at,
            })
            inventory_movements.append({"product_id": pid, "qty": -qty, "movement_id": mv_id})

    # 4 — Audit logs (use existing late_encode_log + security_events)
    await db.late_encode_log.insert_one({
        "id": new_id(),
        "branch_id": payload["branch_id"],
        "invoice_id": invoice_id,
        "invoice_number": invoice_number,
        "original_date": payload["transaction_date"],
        "encoded_at": encoded_at,
        "grand_total": payload["grand_total"],
        "payment_type": "credit",
        "customer_id": payload["customer_id"],
        "customer_name": customer.get("name"),
        "reason": payload["reason"],
        "encoded_by": encoded_by,
        "encoded_by_name": encoded_by_name,
        "verifier_id": encoded_by,
        "verifier_name": encoded_by_name,
        "source": SOURCE_TAG,
        "proof_url": payload["proof_url"],
        "notebook_reference": payload["notebook_reference"],
        "inventory_action": action,
    })
    await db.security_events.insert_one({
        "id": new_id(),
        "type": "historical_credit_encoded",
        "branch_id": payload["branch_id"],
        "invoice_id": invoice_id,
        "invoice_number": invoice_number,
        "customer_id": payload["customer_id"],
        "customer_name": customer.get("name"),
        "transaction_date": payload["transaction_date"],
        "encoded_at": encoded_at,
        "encoded_by": encoded_by,
        "encoded_by_name": encoded_by_name,
        "amount": payload["grand_total"],
        "reason": payload["reason"],
        "proof_url": payload["proof_url"],
        "notebook_reference": payload["notebook_reference"],
        "inventory_action": action,
        "at": encoded_at,
    })

    return {
        "ok": True,
        "invoice": invoice,
        "inventory_action": action,
        "inventory_movements": inventory_movements,
        "count_sheet_stopper": stopper,
    }


# ─────────────────────────────────────────────────────────────────────
# List
# ─────────────────────────────────────────────────────────────────────
@router.get("")
async def list_historical_credits(
    branch_id: Optional[str] = None,
    customer_id: Optional[str] = None,
    limit: int = 100,
    user=Depends(get_current_user),
):
    """List historical credit encoding entries. Admin / owner only."""
    assert_admin_or_owner(user)
    query: dict[str, Any] = {"source": SOURCE_TAG}
    if branch_id:
        assert_branch_access(user, branch_id)
        query["branch_id"] = branch_id
    if customer_id:
        query["customer_id"] = customer_id
    rows = await db.invoices.find(query, {"_id": 0}).sort("late_encoded_at", -1).to_list(
        min(max(limit, 1), 500)
    )
    return {"items": rows, "count": len(rows)}

"""
Draft / For Preparation Orders.

A "Prepare Order" is an invoice reserved with an invoice number and QR code
but NOT yet paid and NOT yet stocked out. It lives in the invoices collection
with status="for_preparation" so it is immediately visible in:
  - Find Transaction (Ctrl+K / /find-transaction)
  - Doc Viewer (/doc/<code>) — QR scan shows FOR PREPARATION badge
  - Sales History (as a distinct status row)

Endpoints:
  POST   /api/draft-orders              → create draft (invoice # + QR reserved)
  GET    /api/draft-orders              → list all for_preparation for a branch
  GET    /api/draft-orders/{id}         → get single draft
  PATCH  /api/draft-orders/{id}         → update items / customer / totals
  DELETE /api/draft-orders/{id}         → cancel draft (status → cancelled_draft)

Finalization:
  Frontend calls the normal processSale() flow but adds draft_invoice_id to
  the payload. sales.py detects this, skips generate_next_number(), and
  UPDATES the existing invoice instead of inserting a new one.
"""
from fastapi import APIRouter, Depends, HTTPException
from typing import Optional, Dict, Any
from config import db
from utils import (
    get_current_user, check_perm, now_iso, new_id,
    generate_next_number, today_local,
)
from routes.doc_lookup import auto_generate_doc_code

router = APIRouter(prefix="/draft-orders", tags=["Draft Orders"])


def _strip(doc: dict) -> dict:
    doc.pop("_id", None)
    return doc


@router.post("")
async def create_draft(payload: Dict[str, Any], user=Depends(get_current_user)):
    """
    Create a draft/for_preparation invoice.
    Generates invoice number + QR code immediately.
    Does NOT deduct stock. Does NOT create fund movements.
    """
    check_perm(user, "pos", "sell")
    branch_id = (payload.get("branch_id") or "").strip()
    if not branch_id:
        raise HTTPException(status_code=400, detail="branch_id required")

    items = payload.get("items") or []
    if not items:
        raise HTTPException(status_code=400, detail="No items in order")

    # Invoice number — same atomic counter as regular sales
    settings = await db.settings.find_one({"key": "invoice_prefixes"}, {"_id": 0})
    prefix = payload.get("prefix") or (
        (settings.get("value", {}).get("sales_invoice", "SI") if settings else "SI")
    )
    inv_number = await generate_next_number(prefix, branch_id)

    order_date = payload.get("order_date") or await today_local(
        user.get("organization_id") or ""
    )

    draft_id = new_id()
    now = now_iso()

    subtotal = float(payload.get("subtotal") or 0)
    freight = float(payload.get("freight") or 0)
    overall_discount = float(payload.get("overall_discount") or 0)
    grand_total = float(payload.get("grand_total") or (subtotal + freight - overall_discount))

    invoice = {
        "id": draft_id,
        "invoice_number": inv_number,
        "prefix": prefix,
        "branch_id": branch_id,
        "order_date": order_date,
        "created_at": now,
        "updated_at": now,
        "status": "for_preparation",
        "customer_id": payload.get("customer_id") or None,
        "customer_name": payload.get("customer_name") or "Walk-in",
        "customer_phone": payload.get("customer_phone") or "",
        "customer_address": payload.get("customer_address") or "",
        "cashier_id": user["id"],
        "cashier_name": user.get("full_name") or user.get("name") or user.get("username") or "",
        "items": items,
        "subtotal": subtotal,
        "freight": freight,
        "overall_discount": overall_discount,
        "grand_total": grand_total,
        "amount_paid": 0,
        "balance": grand_total,
        "payment_type": "cash",
        "payment_method": "Cash",
        "fund_source": "cashier",
        "sale_mode": payload.get("sale_mode") or "quick",
        "active_scheme": payload.get("active_scheme") or "retail",
        "notes": payload.get("notes") or "",
        "voided": False,
    }

    await db.invoices.insert_one(invoice)
    invoice.pop("_id", None)

    # Reserve QR code immediately — idempotent, writes doc_code back to invoice
    org_id = user.get("org_id") or user.get("organization_id") or ""
    doc_code = await auto_generate_doc_code(
        "invoice", draft_id, org_id=org_id, created_by=user["id"]
    )
    await db.invoices.update_one({"id": draft_id}, {"$set": {"doc_code": doc_code}})
    invoice["doc_code"] = doc_code

    return invoice


@router.get("")
async def list_drafts(
    branch_id: Optional[str] = None,
    user=Depends(get_current_user),
):
    """List all for_preparation invoices for a branch (newest first)."""
    if not branch_id:
        raise HTTPException(status_code=400, detail="branch_id required")
    rows = await db.invoices.find(
        {"branch_id": branch_id, "status": "for_preparation", "voided": {"$ne": True}},
        {"_id": 0},
    ).sort("created_at", -1).to_list(100)
    return {"drafts": [_strip(r) for r in rows]}


@router.get("/{draft_id}")
async def get_draft(draft_id: str, user=Depends(get_current_user)):
    doc = await db.invoices.find_one(
        {"id": draft_id, "status": "for_preparation"}, {"_id": 0}
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Draft order not found")
    return _strip(doc)


@router.patch("/{draft_id}")
async def update_draft(
    draft_id: str, payload: Dict[str, Any], user=Depends(get_current_user)
):
    """Update items / customer / totals on an existing draft. Invoice number is preserved."""
    check_perm(user, "pos", "sell")
    doc = await db.invoices.find_one(
        {"id": draft_id, "status": "for_preparation"}, {"_id": 0, "id": 1}
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Draft not found or already finalized")

    allowed = {
        "items", "customer_id", "customer_name", "customer_phone", "customer_address",
        "subtotal", "freight", "overall_discount", "grand_total", "notes",
        "active_scheme", "sale_mode",
    }
    updates = {k: v for k, v in payload.items() if k in allowed}
    updates["updated_at"] = now_iso()
    if "grand_total" in updates:
        updates["balance"] = float(updates["grand_total"])

    await db.invoices.update_one({"id": draft_id}, {"$set": updates})
    updated = await db.invoices.find_one({"id": draft_id}, {"_id": 0})
    return _strip(updated)


@router.delete("/{draft_id}")
async def cancel_draft(draft_id: str, user=Depends(get_current_user)):
    """Cancel / void a draft order (preserves the record for audit)."""
    check_perm(user, "pos", "sell")
    doc = await db.invoices.find_one(
        {"id": draft_id, "status": "for_preparation"}, {"_id": 0, "id": 1}
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Draft not found or already finalized")

    await db.invoices.update_one(
        {"id": draft_id},
        {"$set": {
            "status": "cancelled_draft",
            "voided": True,
            "cancelled_at": now_iso(),
            "cancelled_by": user["id"],
            "cancelled_by_name": user.get("full_name") or user.get("username") or "",
        }},
    )
    return {"ok": True, "draft_id": draft_id}

"""
Price Changes Audit
===================
Tracks competitor "price match" events captured during POS sales.
Distinct from `discount_audit_log` (one-time discounts) — this collection
records permanent branch price changes triggered from the cart, with reason
and approver PIN attestation. Each entry is 1 product × 1 branch × 1 scheme.
"""
from fastapi import APIRouter, Depends, HTTPException
from datetime import datetime, timezone
from typing import Optional
from config import db
from utils import get_current_user, check_perm, today_local

router = APIRouter(prefix="/price-changes", tags=["Price Changes"])


# Standard reasons (UI uses same set, ranked by frequency)
PRICE_MATCH_REASONS = [
    {"key": "competitor_match",       "label": "Competitor price match"},
    {"key": "loyal_customer",         "label": "Bulk / Loyal customer"},
    {"key": "promotional_offer",      "label": "Promotional offer"},
    {"key": "old_stock_clearance",    "label": "Damaged / Old stock clearance"},
    {"key": "other",                  "label": "Other (specify)"},
]


@router.get("/reasons")
async def list_reasons(_user=Depends(get_current_user)):
    """Static list of supported price-match reason keys."""
    return PRICE_MATCH_REASONS


@router.get("")
async def list_price_changes(
    branch_id: Optional[str] = None,
    product_id: Optional[str] = None,
    reason: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    include_voided: bool = False,
    user=Depends(get_current_user),
):
    """
    List price-change events with optional filters.
    Returns rows sorted by date desc.
    """
    check_perm(user, "reports", "view")

    today = await today_local(user.get("organization_id") or "")
    first_of_month = datetime.now(timezone.utc).replace(day=1).strftime("%Y-%m-%d")
    d_from = date_from or first_of_month
    d_to = date_to or today

    query = {"date": {"$gte": d_from, "$lte": d_to}}
    if branch_id:
        query["branch_id"] = branch_id
    if product_id:
        query["product_id"] = product_id
    if reason:
        query["reason"] = reason
    if not include_voided:
        query["invoice_voided"] = {"$ne": True}

    rows = await db.price_change_log.find(query, {"_id": 0}).sort("created_at", -1).to_list(2000)

    # Summary
    total_changes = len(rows)
    avg_delta_pct = 0.0
    total_drop = 0.0
    total_raise = 0.0
    for r in rows:
        try:
            old = float(r.get("old_price") or 0)
            new = float(r.get("new_price") or 0)
            if old > 0:
                avg_delta_pct += ((new - old) / old) * 100
            if new < old:
                total_drop += old - new
            elif new > old:
                total_raise += new - old
        except Exception:
            continue
    avg_delta_pct = round(avg_delta_pct / total_changes, 2) if total_changes else 0

    return {
        "rows": rows,
        "summary": {
            "total_changes": total_changes,
            "average_delta_pct": avg_delta_pct,
            "total_drop_amount": round(total_drop, 2),
            "total_raise_amount": round(total_raise, 2),
            "period": {"from": d_from, "to": d_to},
        },
    }


@router.get("/product/{product_id}")
async def product_price_history(
    product_id: str,
    branch_id: Optional[str] = None,
    user=Depends(get_current_user),
):
    """
    Full price-change timeline for one product.
    Used by ProductDetailPage for a per-product drill-down.
    """
    check_perm(user, "products", "view")
    query = {"product_id": product_id}
    if branch_id:
        query["branch_id"] = branch_id
    rows = await db.price_change_log.find(query, {"_id": 0}).sort("created_at", -1).to_list(500)
    return {"rows": rows, "total": len(rows)}

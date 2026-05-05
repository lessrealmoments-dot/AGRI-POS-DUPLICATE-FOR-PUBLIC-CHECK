"""
Parked / Draft Purchase Orders (a.k.a. "Hold PO").

Mirrors `parked_sales.py` — buyer hits "Park" → current vendor / lines /
header / receipt-upload session gets stashed under their branch so they
can resume later (or a colleague at the same branch can pick it up).
Inventory is NOT touched while parked — same model as a draft PO.

Auto-purge: any park older than 24 h is deleted opportunistically on
every list call.

Permissions:
  • Anyone with `purchase_orders.create` can park / resume.
  • Discarding your OWN park: no PIN.
  • Discarding someone else's: requires manager/admin PIN
    (verified through `verify_pin_for_action`).

Multi-tenant: org-scoped via the `db` proxy. Limit: 20 active parks
per branch (warn at 15 client-side; hard 409 above 20).
"""
from fastapi import APIRouter, Depends, HTTPException
from typing import Optional, Dict, Any
from datetime import datetime, timedelta, timezone
from config import db
from utils import get_current_user, check_perm, now_iso, new_id
from routes.verify import verify_pin_for_action

router = APIRouter(prefix="/parked-purchase-orders", tags=["Parked Purchase Orders"])

PARK_LIMIT_PER_BRANCH = 20
PARK_TTL_HOURS = 24


# ── helpers ──────────────────────────────────────────────────────────


async def _purge_stale(branch_id: str) -> int:
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=PARK_TTL_HOURS)).isoformat()
        res = await db.parked_purchase_orders.delete_many({
            "branch_id": branch_id,
            "created_at": {"$lt": cutoff},
        })
        return getattr(res, "deleted_count", 0) or 0
    except Exception:
        return 0


def _strip_internal(doc: dict) -> dict:
    doc.pop("_id", None)
    doc.pop("organization_id", None)
    return doc


# ── routes ───────────────────────────────────────────────────────────


@router.post("")
async def create_park(payload: Dict[str, Any], user=Depends(get_current_user)):
    """Park the current PO draft. Body must include:
      branch_id, header (vendor, dates, terms, etc.), lines,
      grand_total (denormalized for list), item_count.
    """
    check_perm(user, "purchase_orders", "create")
    branch_id = (payload.get("branch_id") or "").strip()
    if not branch_id:
        raise HTTPException(status_code=400, detail="branch_id required")

    # Branch limit
    count = await db.parked_purchase_orders.count_documents({"branch_id": branch_id})
    if count >= PARK_LIMIT_PER_BRANCH:
        raise HTTPException(
            status_code=409,
            detail=f"Branch already has {PARK_LIMIT_PER_BRANCH} parked POs — resume or discard one before parking another.",
        )

    pid = payload.get("id") or new_id()
    now = now_iso()
    doc = {
        "id": pid,
        "branch_id": branch_id,
        "created_by": user["id"],
        "created_by_name": user.get("name") or user.get("email") or "",
        "created_at": payload.get("created_at") or now,
        "updated_at": now,
        "label": (payload.get("label") or "").strip(),
        "vendor": (payload.get("vendor") or "").strip(),
        "header": payload.get("header") or {},
        "lines": payload.get("lines") or [],
        "vendor_prices": payload.get("vendor_prices") or {},
        "source_type": payload.get("source_type") or "external",
        "supply_branch_id": payload.get("supply_branch_id") or "",
        "receipt_session_id": payload.get("receipt_session_id") or "",
        "receipt_file_count": int(payload.get("receipt_file_count") or 0),
        # Helpful denormalized totals for the list dialog
        "item_count": int(payload.get("item_count") or 0),
        "grand_total": float(payload.get("grand_total") or 0),
    }

    # Idempotent upsert on `id`
    await db.parked_purchase_orders.update_one({"id": pid}, {"$set": doc}, upsert=True)
    return _strip_internal(doc)


@router.get("")
async def list_parks(
    branch_id: Optional[str] = None,
    user=Depends(get_current_user),
):
    """List all active (≤24 h) parked POs for a branch."""
    if not branch_id:
        raise HTTPException(status_code=400, detail="branch_id required")
    await _purge_stale(branch_id)
    rows = await db.parked_purchase_orders.find(
        {"branch_id": branch_id}, {"_id": 0}
    ).sort("created_at", -1).to_list(PARK_LIMIT_PER_BRANCH * 2)
    for r in rows:
        r.pop("organization_id", None)
    return {"parks": rows, "limit": PARK_LIMIT_PER_BRANCH, "ttl_hours": PARK_TTL_HOURS}


@router.get("/{park_id}")
async def get_park(park_id: str, user=Depends(get_current_user)):
    doc = await db.parked_purchase_orders.find_one({"id": park_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Parked PO not found")
    return _strip_internal(doc)


@router.delete("/{park_id}")
async def discard_park(
    park_id: str,
    pin: Optional[str] = None,
    user=Depends(get_current_user),
):
    """Discard a parked PO. Manager PIN required for other-user parks."""
    doc = await db.parked_purchase_orders.find_one({"id": park_id}, {"_id": 0})
    if not doc:
        return {"ok": True, "already_deleted": True}

    if doc.get("created_by") != user.get("id"):
        if not pin:
            raise HTTPException(
                status_code=403,
                detail="Manager PIN required to discard another user's parked PO.",
            )
        result = await verify_pin_for_action(pin, "parked_po.discard_other", branch_id=doc.get("branch_id"))
        if not result:
            raise HTTPException(status_code=403, detail="Invalid PIN.")

    await db.parked_purchase_orders.delete_one({"id": park_id})
    return {"ok": True}


@router.post("/{park_id}/consume")
async def consume_park(park_id: str, user=Depends(get_current_user)):
    """Atomically fetch + delete a parked PO ("Resume")."""
    check_perm(user, "purchase_orders", "create")
    doc = await db.parked_purchase_orders.find_one_and_delete({"id": park_id})
    if not doc:
        raise HTTPException(
            status_code=410,
            detail="This parked PO was already resumed or discarded by someone else.",
        )
    doc.pop("_id", None)
    doc.pop("organization_id", None)
    return doc

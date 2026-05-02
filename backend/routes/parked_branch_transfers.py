"""
Parked / Draft Branch Transfers.

Manager hits "Park" mid-way through composing a transfer order → current
rows + branch selection + markup template gets stashed under the from-branch
so they can resume it later (or a colleague at the same branch can pick it up).

Mirrors the parked_sales pattern:
  • Auto-purge: parks older than 24 h are best-effort deleted on every list call.
  • Branch-shared: any user on duty at the FROM branch can resume.
  • Discarding your OWN park: no PIN. Discarding someone else's: manager PIN.
  • Stock is NOT reserved while parked (transfer hasn't been committed yet).
"""
from fastapi import APIRouter, Depends, HTTPException
from typing import Optional, Dict, Any
from datetime import datetime, timedelta, timezone
from config import db
from utils import get_current_user, check_perm, now_iso, new_id
from routes.verify import verify_pin_for_action

router = APIRouter(prefix="/parked-branch-transfers", tags=["Parked Branch Transfers"])

PARK_LIMIT_PER_BRANCH = 10
PARK_TTL_HOURS = 24


async def _purge_stale(branch_id: str) -> int:
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=PARK_TTL_HOURS)).isoformat()
        res = await db.parked_branch_transfers.delete_many({
            "from_branch_id": branch_id,
            "created_at": {"$lt": cutoff},
        })
        return getattr(res, "deleted_count", 0) or 0
    except Exception:
        return 0


def _strip_internal(doc: dict) -> dict:
    doc.pop("_id", None)
    doc.pop("organization_id", None)
    return doc


@router.post("")
async def create_park(payload: Dict[str, Any], user=Depends(get_current_user)):
    """Park the current branch transfer draft.
    Body: from_branch_id, to_branch_id, label?, rows[], min_margin, category_markups[].
    """
    check_perm(user, "branch_transfers", "create")
    from_branch_id = (payload.get("from_branch_id") or "").strip()
    if not from_branch_id:
        raise HTTPException(status_code=400, detail="from_branch_id required")

    count = await db.parked_branch_transfers.count_documents({"from_branch_id": from_branch_id})
    if count >= PARK_LIMIT_PER_BRANCH:
        raise HTTPException(
            status_code=409,
            detail=f"Branch already has {PARK_LIMIT_PER_BRANCH} parked transfers — resume or discard one before parking another.",
        )

    pid = payload.get("id") or new_id()
    now = now_iso()
    doc = {
        "id": pid,
        "from_branch_id": from_branch_id,
        "to_branch_id": payload.get("to_branch_id") or "",
        "created_by": user["id"],
        "created_by_name": user.get("name") or user.get("full_name") or user.get("email") or "",
        "created_at": payload.get("created_at") or now,
        "updated_at": now,
        "label": (payload.get("label") or "").strip(),
        "rows": payload.get("rows") or [],
        "min_margin": float(payload.get("min_margin") or 20),
        "category_markups": payload.get("category_markups") or [],
        # Denormalized for fast list rendering
        "item_count": int(payload.get("item_count") or 0),
    }
    await db.parked_branch_transfers.update_one({"id": pid}, {"$set": doc}, upsert=True)
    return _strip_internal(doc)


@router.get("")
async def list_parks(from_branch_id: Optional[str] = None, user=Depends(get_current_user)):
    """List all active (≤24 h) parked transfers for a from-branch."""
    if not from_branch_id:
        raise HTTPException(status_code=400, detail="from_branch_id required")
    await _purge_stale(from_branch_id)
    rows = await db.parked_branch_transfers.find(
        {"from_branch_id": from_branch_id}, {"_id": 0}
    ).sort("updated_at", -1).to_list(PARK_LIMIT_PER_BRANCH * 2)
    for r in rows:
        r.pop("organization_id", None)
    return {"parks": rows, "limit": PARK_LIMIT_PER_BRANCH, "ttl_hours": PARK_TTL_HOURS}


@router.get("/{park_id}")
async def get_park(park_id: str, user=Depends(get_current_user)):
    doc = await db.parked_branch_transfers.find_one({"id": park_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Parked transfer not found")
    return _strip_internal(doc)


@router.delete("/{park_id}")
async def discard_park(
    park_id: str,
    pin: Optional[str] = None,
    user=Depends(get_current_user),
):
    """Discard. Owner: no PIN. Other users: manager PIN required."""
    doc = await db.parked_branch_transfers.find_one({"id": park_id}, {"_id": 0})
    if not doc:
        return {"ok": True, "already_deleted": True}

    if doc.get("created_by") != user.get("id"):
        if not pin:
            raise HTTPException(
                status_code=403,
                detail="Manager PIN required to discard another user's parked transfer.",
            )
        result = await verify_pin_for_action(pin, "parked_sale.discard_other", branch_id=doc.get("from_branch_id"))
        if not result:
            raise HTTPException(status_code=403, detail="Invalid PIN.")

    await db.parked_branch_transfers.delete_one({"id": park_id})
    return {"ok": True}


@router.post("/{park_id}/consume")
async def consume_park(park_id: str, user=Depends(get_current_user)):
    """Atomically fetch + delete a parked transfer (the Resume action).
    Returns 410 Gone if another device already consumed it (race condition)."""
    check_perm(user, "branch_transfers", "create")
    doc = await db.parked_branch_transfers.find_one_and_delete({"id": park_id})
    if not doc:
        raise HTTPException(
            status_code=410,
            detail="This parked transfer was already resumed or discarded by someone else.",
        )
    doc.pop("_id", None)
    doc.pop("organization_id", None)
    return doc

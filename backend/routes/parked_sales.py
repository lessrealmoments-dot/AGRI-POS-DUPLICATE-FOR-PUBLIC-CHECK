"""
Parked / Draft Sales (a.k.a. "Hold Sale" / "Park Sale").

Cashier hits "Park" → current cart/lines/customer/header gets stashed
under the cashier's branch so they can serve the next customer
immediately. They (or a colleague at the same branch) can resume it
later. Stock is NOT reserved while parked — same model as the live
cart.

Auto-purge: any park older than 24 h is deleted opportunistically on
every list call so they never accumulate.

Permissions:
  • Anyone with `sales.create` can park / resume.
  • Discarding your OWN park: no PIN.
  • Discarding someone else's park: requires manager/admin PIN
    (verified through `verify_pin_for_action`) so a junior cashier
    cannot accidentally trash a colleague's pending sale.

Multi-tenant: org-scoped via the `db` proxy. Limit: 20 active parks
per branch (warn at 15 client-side; hard 409 above 20).
"""
from fastapi import APIRouter, Depends, HTTPException
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta, timezone
from config import db
from utils import get_current_user, check_perm, has_perm, now_iso, new_id
from routes.verify import verify_pin_for_action

router = APIRouter(prefix="/parked-sales", tags=["Parked Sales"])

PARK_LIMIT_PER_BRANCH = 20
PARK_TTL_HOURS = 24


# ── helpers ──────────────────────────────────────────────────────────


async def _purge_stale(branch_id: str) -> int:
    """Best-effort purge of parks older than 24 h for this branch.
    Runs on every list call so we stay self-cleaning without an extra
    cron job. Failures are silent — never block the caller."""
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=PARK_TTL_HOURS)).isoformat()
        res = await db.parked_sales.delete_many({
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
    """Park the current sale. Body must include:
      branch_id, mode ("quick"|"order"), label (optional),
      cart, lines, header, customer (snapshot), active_scheme.
    """
    check_perm(user, "sales", "create")
    branch_id = (payload.get("branch_id") or "").strip()
    if not branch_id:
        raise HTTPException(status_code=400, detail="branch_id required")
    mode = payload.get("mode", "quick")
    if mode not in ("quick", "order"):
        raise HTTPException(status_code=400, detail="mode must be quick or order")

    # Branch limit
    count = await db.parked_sales.count_documents({"branch_id": branch_id})
    if count >= PARK_LIMIT_PER_BRANCH:
        raise HTTPException(
            status_code=409,
            detail=f"Branch already has {PARK_LIMIT_PER_BRANCH} parked sales — resume or discard one before parking another.",
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
        "mode": mode,
        "customer": payload.get("customer"),     # snapshot {id, name, phone, scheme}
        "active_scheme": payload.get("active_scheme") or "retail",
        "cart": payload.get("cart") or [],
        "lines": payload.get("lines") or [],
        "header": payload.get("header") or {},
        # Helpful denormalized totals so the list dialog can render counts /
        # subtotals without re-deriving from cart math.
        "item_count": int(payload.get("item_count") or 0),
        "subtotal": float(payload.get("subtotal") or 0),
    }

    # Upsert to make the endpoint idempotent — same `id` from the offline
    # outbox shouldn't error out and shouldn't create duplicates.
    await db.parked_sales.update_one({"id": pid}, {"$set": doc}, upsert=True)
    return _strip_internal(doc)


@router.get("")
async def list_parks(
    branch_id: Optional[str] = None,
    user=Depends(get_current_user),
):
    """List all active (≤24 h) parks for a branch. Branch-shared so any
    cashier on duty can pick up a colleague's parked sale."""
    if not branch_id:
        raise HTTPException(status_code=400, detail="branch_id required")
    await _purge_stale(branch_id)
    rows = await db.parked_sales.find(
        {"branch_id": branch_id}, {"_id": 0}
    ).sort("created_at", -1).to_list(PARK_LIMIT_PER_BRANCH * 2)
    # Hide org_id even though it's already excluded above — defense in depth.
    for r in rows:
        r.pop("organization_id", None)
    return {"parks": rows, "limit": PARK_LIMIT_PER_BRANCH, "ttl_hours": PARK_TTL_HOURS}


@router.get("/{park_id}")
async def get_park(park_id: str, user=Depends(get_current_user)):
    doc = await db.parked_sales.find_one({"id": park_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Parked sale not found")
    return _strip_internal(doc)


@router.delete("/{park_id}")
async def discard_park(
    park_id: str,
    pin: Optional[str] = None,
    user=Depends(get_current_user),
):
    """Discard a parked sale. If the current user did not create it,
    a manager/admin PIN is required so juniors don't accidentally
    trash a colleague's pending sale. PIN is passed as a query string
    so the DELETE body stays empty (some clients drop bodies on DELETE)."""
    doc = await db.parked_sales.find_one({"id": park_id}, {"_id": 0})
    if not doc:
        # Idempotent — already gone, treat as success
        return {"ok": True, "already_deleted": True}

    if doc.get("created_by") != user.get("id"):
        # Other-user discard — require manager/admin PIN
        if not pin:
            raise HTTPException(
                status_code=403,
                detail="Manager PIN required to discard another cashier's parked sale.",
            )
        result = await verify_pin_for_action(pin, "parked_sale.discard_other", branch_id=doc.get("branch_id"))
        if not result:
            raise HTTPException(status_code=403, detail="Invalid PIN.")

    await db.parked_sales.delete_one({"id": park_id})
    return {"ok": True}


@router.post("/{park_id}/consume")
async def consume_park(park_id: str, user=Depends(get_current_user)):
    """Atomically fetch + delete a parked sale.

    This is the "Resume" action. Unlike discard, no PIN is required
    even when consuming another cashier's park — that's the whole
    point of the branch-shared model: any cashier on duty can pick up
    a colleague's pending sale. The snapshot returns to the caller so
    they can rehydrate their cart, and the row vanishes from the list
    so no one else can resume the same draft (and the original cashier
    isn't confused into thinking the customer never came back).

    Idempotent: a 404 just means another device already consumed it
    (race), so we return 410 Gone with a clear message instead of a
    confusing 404.
    """
    check_perm(user, "sales", "create")
    doc = await db.parked_sales.find_one_and_delete({"id": park_id})
    if not doc:
        raise HTTPException(
            status_code=410,
            detail="This parked sale was already resumed or discarded by someone else.",
        )
    doc.pop("_id", None)
    doc.pop("organization_id", None)
    return doc

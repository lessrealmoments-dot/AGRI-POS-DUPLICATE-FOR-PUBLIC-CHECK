"""
Branch-scoped product visibility (disable / enable).

Concept:
- A product can be globally active in the catalog but **disabled at a specific
  branch** so cashiers there cannot sell it. The product still appears in POS
  search results greyed-out so cashiers know it exists.
- Disable is only allowed when the inventory at that branch is 0 (i.e. there's
  nothing to sell anyway). Once stock arrives at the branch (PO, transfer-in,
  return, manual adjustment), the product is automatically re-enabled.

Auto-reactivation is performed *lazily on read* — at the start of every list
endpoint that consumes branch inventory data, we run a single bulk update
clearing `disabled_at_branch=True` on inventory rows where `quantity > 0`. This
avoids touching every $inc call site across the codebase.

Permissions:
- Admin/Owner: may disable/enable at any branch.
- Manager: may disable/enable only at their assigned branch. Cannot delete.
- Cashier and below: read-only.
"""
from fastapi import APIRouter, Depends, HTTPException
from typing import Optional, List
from pydantic import BaseModel

from config import db
from utils import get_current_user, now_iso

router = APIRouter()


class BranchProductRequest(BaseModel):
    product_ids: List[str]
    branch_id: str


def _ensure_can_toggle_branch(user: dict, branch_id: str):
    """Admin/Owner can toggle any branch; Manager only their own."""
    if user.get("is_super_admin"):
        return
    role = user.get("role", "")
    if role in ("admin", "owner"):
        return
    if role == "manager":
        user_branch = user.get("branch_id") or ""
        if user_branch and user_branch == branch_id:
            return
        # Multi-branch managers (no branch_id) — allow as long as they have
        # products.update permission
        if not user_branch and user.get("permissions", {}).get("products", {}).get("update"):
            return
        raise HTTPException(
            status_code=403,
            detail="Managers can only disable/enable products for their own branch",
        )
    raise HTTPException(status_code=403, detail="Not authorized to toggle branch products")


async def reactivate_in_stock(branch_id: str) -> int:
    """Lazy auto-reactivation. Clears disabled_at_branch=True on rows where
    inventory has come back in stock. Returns count cleared.
    Cheap to call at the top of read endpoints — single bulk update.
    """
    if not branch_id:
        return 0
    try:
        result = await db.inventory.update_many(
            {"branch_id": branch_id, "disabled_at_branch": True, "quantity": {"$gt": 0}},
            {"$set": {
                "disabled_at_branch": False,
                "auto_reactivated_at": now_iso(),
            }},
        )
        return result.modified_count
    except Exception:
        return 0


@router.post("/products/disable-at-branch")
async def disable_at_branch(payload: BranchProductRequest, user=Depends(get_current_user)):
    """Disable a list of products at a branch.

    Skips products that have inventory > 0 at that branch (you can't disable
    something there's stock of — it would have to be transferred or sold first).
    Returns a per-product breakdown so the UI can surface which were skipped.
    """
    if not payload.branch_id:
        raise HTTPException(status_code=400, detail="branch_id is required")
    if not payload.product_ids:
        raise HTTPException(status_code=400, detail="product_ids is required")
    _ensure_can_toggle_branch(user, payload.branch_id)

    disabled = []
    skipped_with_stock = []
    not_found = []

    for pid in payload.product_ids:
        # Verify the product exists and is active in this org (defense-in-depth
        # against branch_id collisions across orgs)
        prod = await db.products.find_one(
            {"id": pid, "active": True}, {"_id": 0, "id": 1, "name": 1}
        )
        if not prod:
            not_found.append({"product_id": pid})
            continue

        inv = await db.inventory.find_one(
            {"product_id": pid, "branch_id": payload.branch_id},
            {"_id": 0, "quantity": 1},
        )
        qty = float((inv or {}).get("quantity") or 0)
        if qty > 0:
            skipped_with_stock.append({
                "product_id": pid,
                "product_name": prod.get("name", ""),
                "quantity": qty,
            })
            continue

        # Upsert the inventory row with disabled_at_branch=True
        await db.inventory.update_one(
            {"product_id": pid, "branch_id": payload.branch_id},
            {"$set": {
                "disabled_at_branch": True,
                "disabled_at": now_iso(),
                "disabled_by_id": user["id"],
                "disabled_by_name": user.get("full_name") or user.get("username") or "",
                "updated_at": now_iso(),
            },
             "$setOnInsert": {
                 "product_id": pid,
                 "branch_id": payload.branch_id,
                 "organization_id": user.get("organization_id", ""),
                 "quantity": 0,
             }},
            upsert=True,
        )
        disabled.append({"product_id": pid, "product_name": prod.get("name", "")})

    return {
        "disabled_count": len(disabled),
        "disabled": disabled,
        "skipped_with_stock_count": len(skipped_with_stock),
        "skipped_with_stock": skipped_with_stock,
        "not_found_count": len(not_found),
        "not_found": not_found,
    }


@router.post("/products/enable-at-branch")
async def enable_at_branch(payload: BranchProductRequest, user=Depends(get_current_user)):
    """Manually re-enable a list of products at a branch (clear the disabled flag)."""
    if not payload.branch_id:
        raise HTTPException(status_code=400, detail="branch_id is required")
    if not payload.product_ids:
        raise HTTPException(status_code=400, detail="product_ids is required")
    _ensure_can_toggle_branch(user, payload.branch_id)

    result = await db.inventory.update_many(
        {"product_id": {"$in": payload.product_ids}, "branch_id": payload.branch_id, "disabled_at_branch": True},
        {"$set": {
            "disabled_at_branch": False,
            "enabled_at": now_iso(),
            "enabled_by_id": user["id"],
            "enabled_by_name": user.get("full_name") or user.get("username") or "",
            "updated_at": now_iso(),
        }},
    )
    return {"enabled_count": result.modified_count}


@router.get("/products/disabled-at-branch")
async def list_disabled_at_branch(branch_id: str, user=Depends(get_current_user)):
    """List products currently disabled at a given branch.

    Auto-reactivates first so the response is always accurate.
    """
    if not branch_id:
        raise HTTPException(status_code=400, detail="branch_id is required")
    await reactivate_in_stock(branch_id)
    rows = await db.inventory.find(
        {"branch_id": branch_id, "disabled_at_branch": True},
        {"_id": 0, "product_id": 1, "disabled_at": 1, "disabled_by_name": 1},
    ).to_list(5000)
    # Hydrate with product names
    pids = [r["product_id"] for r in rows]
    if not pids:
        return {"items": []}
    prods = await db.products.find(
        {"id": {"$in": pids}, "active": True},
        {"_id": 0, "id": 1, "name": 1, "sku": 1, "is_repack": 1},
    ).to_list(5000)
    pmap = {p["id"]: p for p in prods}
    items = []
    for r in rows:
        p = pmap.get(r["product_id"])
        if not p:
            continue
        items.append({
            "product_id": r["product_id"],
            "name": p.get("name", ""),
            "sku": p.get("sku", ""),
            "is_repack": p.get("is_repack", False),
            "disabled_at": r.get("disabled_at"),
            "disabled_by_name": r.get("disabled_by_name", ""),
        })
    return {"items": items}

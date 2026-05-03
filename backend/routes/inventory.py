"""
Inventory management routes: stock levels, adjustments, transfers.
Supports multi-branch data isolation.
"""
from fastapi import APIRouter, Depends, HTTPException
from typing import Optional
from config import db
from utils import (
    get_current_user, check_perm, now_iso, new_id, log_movement,
    get_branch_filter, apply_branch_filter, ensure_branch_access, get_default_branch,
    mark_price_reviewed,
)

router = APIRouter(prefix="/inventory", tags=["Inventory"])


@router.get("")
async def list_inventory(
    user=Depends(get_current_user),
    branch_id: Optional[str] = None,
    search: Optional[str] = None,
    low_stock: Optional[bool] = None,
    include_repacks: Optional[bool] = True,
    sort_by: Optional[str] = "name",   # "name" | "type" | "grouped"
    skip: int = 0,
    limit: int = 50
):
    """List inventory with stock levels, including derived repack quantities."""
    # Base query - only get non-repack products for direct inventory
    base_match = {"active": True}
    if not include_repacks:
        base_match["is_repack"] = {"$ne": True}
    
    pipeline = [
        {"$match": base_match},
        {"$lookup": {
            "from": "inventory",
            "localField": "id",
            "foreignField": "product_id",
            "as": "stock_records"
        }},
    ]
    
    if branch_id:
        pipeline.append({"$addFields": {
            "stock_records": {
                "$filter": {
                    "input": "$stock_records",
                    "as": "s",
                    "cond": {"$eq": ["$$s.branch_id", branch_id]}
                }
            }
        }})
    
    pipeline.append({"$addFields": {
        "total_stock": {"$sum": "$stock_records.quantity"},
        "branch_stock": {
            "$arrayToObject": {
                "$map": {
                    "input": "$stock_records",
                    "as": "s",
                    "in": {"k": "$$s.branch_id", "v": "$$s.quantity"}
                }
            }
        },
        # Per-branch price-reviewed status (used for the "Global Price" badge).
        # When branch_id filter is applied, stock_records is already scoped
        # to that branch, so the first element is the relevant one.
        "branch_price_review": {
            "$arrayToObject": {
                "$map": {
                    "input": "$stock_records",
                    "as": "s",
                    "in": {
                        "k": "$$s.branch_id",
                        "v": {"$cond": [
                            {"$ifNull": ["$$s.price_reviewed_at", False]},
                            "reviewed",
                            "pending",
                        ]},
                    }
                }
            }
        }
    }})
    
    if search:
        pipeline.insert(1, {"$match": {"$or": [
            {"name": {"$regex": search, "$options": "i"}},
            {"sku": {"$regex": search, "$options": "i"}}
        ]}})
    
    if low_stock:
        pipeline.append({"$match": {"total_stock": {"$lte": 10}}})
    
    pipeline.append({"$project": {"_id": 0, "stock_records": 0}})

    # Sorting
    if sort_by == "grouped":
        pipeline.append({"$lookup": {
            "from": "products",
            "localField": "parent_id",
            "foreignField": "id",
            "as": "_parent_doc"
        }})
        pipeline.append({"$addFields": {
            "_sort_key": {
                "$cond": {
                    "if": {"$eq": ["$is_repack", True]},
                    "then": {"$toLower": {"$ifNull": [{"$arrayElemAt": ["$_parent_doc.name", 0]}, "$name"]}},
                    "else": {"$toLower": "$name"}
                }
            },
            "_is_repack_int": {"$cond": [{"$eq": ["$is_repack", True]}, 1, 0]}
        }})
        pipeline.append({"$sort": {"_sort_key": 1, "_is_repack_int": 1, "name": 1}})
        pipeline.append({"$project": {"_parent_doc": 0, "_sort_key": 0, "_is_repack_int": 0}})
    elif sort_by == "type":
        pipeline.append({"$addFields": {"_is_repack_int": {"$cond": [{"$eq": ["$is_repack", True]}, 1, 0]}}})
        pipeline.append({"$sort": {"_is_repack_int": 1, "name": 1}})
        pipeline.append({"$project": {"_is_repack_int": 0}})
    else:
        pipeline.append({"$sort": {"name": 1}})
    
    count_pipeline = pipeline + [{"$count": "total"}]
    count_result = await db.products.aggregate(count_pipeline).to_list(1)
    total = count_result[0]["total"] if count_result else 0
    
    pipeline.extend([{"$skip": skip}, {"$limit": limit}])
    items = await db.products.aggregate(pipeline).to_list(limit)
    
    # For repacks, calculate derived stock from parent
    enriched_items = []
    for item in items:
        if item.get("is_repack") and item.get("parent_id"):
            # Get parent stock
            parent_inv = await db.inventory.find_one(
                {"product_id": item["parent_id"], "branch_id": branch_id} if branch_id 
                else {"product_id": item["parent_id"]},
                {"_id": 0}
            )
            parent_stock = parent_inv["quantity"] if parent_inv else 0
            units_per_parent = item.get("units_per_parent", 1)
            
            # Calculate derived stock
            item["total_stock"] = parent_stock * units_per_parent
            item["derived_from_parent"] = True
            item["parent_stock"] = parent_stock
            
            # Get parent name
            parent = await db.products.find_one(
                {"id": item["parent_id"]},
                {"_id": 0, "name": 1, "unit": 1}
            )
            item["parent_name"] = parent["name"] if parent else ""
            item["parent_unit"] = parent["unit"] if parent else ""

            # Repack capital: derive live from parent's branch capital so the
            # Inventory page shows real numbers instead of the stored ₱0.
            from utils.helpers import get_repack_capital
            cap = await get_repack_capital(item, branch_id or "")
            item["cost_price"] = round(cap, 4)
        enriched_items.append(item)
    
    return {"items": enriched_items, "total": total}


# ── Global Price Badge endpoints ─────────────────────────────────────────────

@router.post("/mark-reviewed")
async def mark_inventory_reviewed(data: dict, user=Depends(get_current_user)):
    """
    One-click mark a product's pricing as reviewed for a branch — clears the
    "Global Price" badge.
    Body: { product_id, branch_id }
    """
    check_perm(user, "products", "edit")
    pid = (data.get("product_id") or "").strip()
    bid = (data.get("branch_id") or "").strip()
    if not pid or not bid:
        raise HTTPException(status_code=400, detail="product_id and branch_id are required")
    await mark_price_reviewed(pid, bid, source="manual_ack")
    return {"ok": True, "product_id": pid, "branch_id": bid, "reviewed_at": now_iso()}


@router.post("/mark-all-reviewed")
async def mark_all_reviewed(data: dict, user=Depends(get_current_user)):
    """
    Bulk-mark every inventory row for a branch as reviewed — clears all
    "Global Price" badges at this branch in one call.
    Body: { branch_id }
    Admin only.
    """
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    bid = (data.get("branch_id") or "").strip()
    if not bid:
        raise HTTPException(status_code=400, detail="branch_id is required")
    res = await db.inventory.update_many(
        {"branch_id": bid, "$or": [
            {"price_reviewed_at": {"$exists": False}},
            {"price_reviewed_at": None},
        ]},
        {"$set": {
            "price_reviewed_at": now_iso(),
            "last_price_review_source": "bulk_ack",
        }},
    )
    return {"ok": True, "branch_id": bid, "marked_count": res.modified_count}


@router.get("/pending-review-count")
async def pending_review_count(branch_id: str, user=Depends(get_current_user)):
    """Return how many products at this branch still need price review (badge count)."""
    n = await db.inventory.count_documents({
        "branch_id": branch_id,
        "$or": [
            {"price_reviewed_at": {"$exists": False}},
            {"price_reviewed_at": None},
        ],
    })
    return {"branch_id": branch_id, "pending": n}


@router.get("/pending-review-ids")
async def pending_review_ids(branch_id: str, user=Depends(get_current_user)):
    """
    Return the set of product_ids at this branch still pending price review.
    Frontend uses this to render the "Global Price" badge inline on POS line
    items (single fetch on POS open).
    """
    docs = await db.inventory.find(
        {
            "branch_id": branch_id,
            "$or": [
                {"price_reviewed_at": {"$exists": False}},
                {"price_reviewed_at": None},
            ],
        },
        {"_id": 0, "product_id": 1},
    ).to_list(50000)
    return {"branch_id": branch_id, "product_ids": [d["product_id"] for d in docs if d.get("product_id")]}


@router.post("/adjust")
async def adjust_inventory(data: dict, user=Depends(get_current_user)):
    """Adjust inventory quantity (add or subtract)."""
    check_perm(user, "inventory", "adjust")
    
    product_id = data["product_id"]
    branch_id = data["branch_id"]
    quantity = float(data["quantity"])
    reason = data.get("reason", "Manual adjustment")
    
    # Check if this is a repack - cannot adjust repack inventory directly
    product = await db.products.find_one({"id": product_id}, {"_id": 0})
    if product and product.get("is_repack"):
        raise HTTPException(
            status_code=400,
            detail="Cannot adjust repack inventory directly. Adjust the parent product instead. Repack stock is derived from parent."
        )
    
    existing = await db.inventory.find_one(
        {"product_id": product_id, "branch_id": branch_id},
        {"_id": 0}
    )
    
    if existing:
        new_qty = existing["quantity"] + quantity
        await db.inventory.update_one(
            {"product_id": product_id, "branch_id": branch_id},
            {"$set": {"quantity": new_qty, "updated_at": now_iso()}}
        )
    else:
        await db.inventory.insert_one({
            "id": new_id(),
            "product_id": product_id,
            "branch_id": branch_id,
            "quantity": quantity,
            "updated_at": now_iso()
        })
    
    log = {
        "id": new_id(),
        "product_id": product_id,
        "branch_id": branch_id,
        "quantity_change": quantity,
        "reason": reason,
        "user_id": user["id"],
        "created_at": now_iso()
    }
    await db.inventory_logs.insert_one(log)
    
    await log_movement(
        product_id, branch_id, "adjustment", quantity,
        log["id"], "ADJ", 0, user["id"],
        user.get("full_name", user["username"]), reason
    )
    
    return {
        "message": "Inventory adjusted",
        "new_quantity": (existing["quantity"] + quantity) if existing else quantity
    }


@router.post("/transfer")
async def transfer_inventory(data: dict, user=Depends(get_current_user)):
    """Transfer inventory between branches."""
    check_perm(user, "inventory", "transfer")
    
    product_id = data["product_id"]
    from_branch = data["from_branch_id"]
    to_branch = data["to_branch_id"]
    quantity = float(data["quantity"])
    
    source = await db.inventory.find_one(
        {"product_id": product_id, "branch_id": from_branch},
        {"_id": 0}
    )
    
    if not source or source["quantity"] < quantity:
        raise HTTPException(status_code=400, detail="Insufficient stock in source branch")
    
    # Deduct from source
    await db.inventory.update_one(
        {"product_id": product_id, "branch_id": from_branch},
        {"$inc": {"quantity": -quantity}, "$set": {"updated_at": now_iso()}}
    )
    
    # Add to destination
    dest = await db.inventory.find_one(
        {"product_id": product_id, "branch_id": to_branch},
        {"_id": 0}
    )
    
    if dest:
        await db.inventory.update_one(
            {"product_id": product_id, "branch_id": to_branch},
            {"$inc": {"quantity": quantity}, "$set": {"updated_at": now_iso()}}
        )
    else:
        await db.inventory.insert_one({
            "id": new_id(),
            "product_id": product_id,
            "branch_id": to_branch,
            "quantity": quantity,
            "updated_at": now_iso()
        })
    
    # Log the transfer
    log = {
        "id": new_id(),
        "type": "transfer",
        "product_id": product_id,
        "from_branch_id": from_branch,
        "to_branch_id": to_branch,
        "quantity": quantity,
        "user_id": user["id"],
        "created_at": now_iso()
    }
    await db.inventory_logs.insert_one(log)
    
    return {"message": "Transfer complete"}


@router.post("/set")
async def set_inventory(data: dict, user=Depends(get_current_user)):
    """Set inventory to a specific quantity."""
    check_perm(user, "inventory", "adjust")
    
    product_id = data["product_id"]
    branch_id = data["branch_id"]
    quantity = float(data["quantity"])

    # Repack guard — repack stock is derived from parent; cannot be set directly
    product = await db.products.find_one({"id": product_id}, {"_id": 0})
    if product and product.get("is_repack"):
        raise HTTPException(
            status_code=400,
            detail="Cannot set repack inventory directly. Adjust the parent product instead."
        )
    
    existing = await db.inventory.find_one(
        {"product_id": product_id, "branch_id": branch_id}
    )
    
    if existing:
        await db.inventory.update_one(
            {"product_id": product_id, "branch_id": branch_id},
            {"$set": {"quantity": quantity, "updated_at": now_iso()}}
        )
    else:
        await db.inventory.insert_one({
            "id": new_id(),
            "product_id": product_id,
            "branch_id": branch_id,
            "quantity": quantity,
            "updated_at": now_iso()
        })
    
    return {"message": "Inventory set", "quantity": quantity}


@router.post("/admin-adjust")
async def admin_adjust_inventory(data: dict, user=Depends(get_current_user)):
    """
    Admin inventory correction — sets stock to exact new_quantity to fix counting errors.
    Requires PIN verification per policy. Creates a full audit log.
    """
    # PIN enforcement for inventory adjustment
    pin = data.get("pin", "")
    if pin:
        from routes.verify import verify_pin_for_action
        verifier = await verify_pin_for_action(pin, "inventory_adjust")
        if not verifier:
            from fastapi import HTTPException
            raise HTTPException(status_code=403, detail="Invalid PIN")

    product_id = data["product_id"]
    branch_id = data["branch_id"]
    new_quantity = float(data["new_quantity"])
    reason = data.get("reason", "Admin correction")
    verified_by = data.get("verified_by", "")    # name of admin who verified
    auth_mode = data.get("auth_mode", "totp")    # "totp", "password", or "direct_admin"

    # Repack guard
    product = await db.products.find_one({"id": product_id}, {"_id": 0})
    if product and product.get("is_repack"):
        from fastapi import HTTPException
        raise HTTPException(
            status_code=400,
            detail="Cannot adjust repack inventory directly. Adjust the parent product."
        )

    existing = await db.inventory.find_one(
        {"product_id": product_id, "branch_id": branch_id}, {"_id": 0}
    )
    old_quantity = existing["quantity"] if existing else 0

    if existing:
        await db.inventory.update_one(
            {"product_id": product_id, "branch_id": branch_id},
            {"$set": {"quantity": new_quantity, "updated_at": now_iso()}}
        )
    else:
        await db.inventory.insert_one({
            "id": new_id(),
            "product_id": product_id,
            "branch_id": branch_id,
            "quantity": new_quantity,
            "updated_at": now_iso()
        })

    diff = new_quantity - old_quantity

    # Full audit record
    correction_id = new_id()
    correction = {
        "id": correction_id,
        "product_id": product_id,
        "branch_id": branch_id,
        "old_quantity": old_quantity,
        "new_quantity": new_quantity,
        "difference": diff,
        "reason": reason,
        "performed_by_id": user["id"],
        "performed_by_name": user.get("full_name", user["username"]),
        "authorized_by": verified_by,
        "auth_mode": auth_mode,
        "created_at": now_iso(),
    }
    await db.inventory_corrections.insert_one(correction)
    del correction["_id"]

    # Movement history entry
    await log_movement(
        product_id, branch_id, "correction", diff,
        correction_id, "CORR", 0, user["id"],
        user.get("full_name", user["username"]), reason
    )

    return {
        "message": "Inventory corrected",
        "old_quantity": old_quantity,
        "new_quantity": new_quantity,
        "difference": diff,
        "correction": correction,
    }


@router.post("/admin-inject")
async def admin_inject_inventory(data: dict, user=Depends(get_current_user)):
    """
    Admin-only stock injection (Iter 217b).

    Supports three modes:
      • "add"    — bump current quantity by +N
      • "deduct" — reduce current quantity by N  (safe-guarded: can't go negative)
      • "set"    — overwrite quantity to exactly N (like the old /admin-adjust)

    Does NOT touch moving_avg_cost, last_purchase_cost, branch_prices,
    or any pricing fields. Intended for opening balance seeds, physical-count
    variances, vendor returns, promo stock — scenarios where the cost basis
    should be preserved.

    Strict admin role gate — no `inventory.inject` permission. A trusted
    manager can NOT do this even via permission grant. (Per user's Iter 217b
    spec: "only admin can, admin can login to any branch anyway".)

    Full audit trail: `stock_injections` collection + `stock_movements` entry
    with type="injection" so it surfaces in Stock Movements view and Audit
    Pulse's red-flag card.
    """
    # 1. Strict admin role gate
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin role required for stock injection")

    product_id = (data.get("product_id") or "").strip()
    branch_id = (data.get("branch_id") or "").strip()
    mode = (data.get("mode") or "").strip().lower()
    qty = data.get("quantity")
    reason_type = (data.get("reason_type") or "").strip()
    reason_note = (data.get("reason_note") or "").strip()

    # 2. Input validation
    if not product_id or not branch_id:
        raise HTTPException(status_code=400, detail="product_id and branch_id required")
    if mode not in ("add", "deduct", "set"):
        raise HTTPException(status_code=400, detail="mode must be 'add', 'deduct', or 'set'")
    try:
        qty = float(qty)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="quantity must be a number")
    if qty < 0:
        raise HTTPException(status_code=400, detail="quantity must be ≥ 0 — use mode='deduct' to subtract")
    allowed_reasons = {
        "opening_balance", "count_variance", "damaged_recovery",
        "promo_stock", "vendor_return", "other",
    }
    if reason_type not in allowed_reasons:
        raise HTTPException(
            status_code=400,
            detail=f"reason_type must be one of: {', '.join(sorted(allowed_reasons))}"
        )
    if len(reason_note) < 10:
        raise HTTPException(
            status_code=400,
            detail="reason_note is required and must be ≥ 10 characters for the audit trail"
        )

    # 3. Repack guard
    product = await db.products.find_one({"id": product_id}, {"_id": 0})
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    if product.get("is_repack"):
        raise HTTPException(
            status_code=400,
            detail="Cannot inject into a repack product — inject the parent product instead."
        )

    # 4. Compute new quantity
    existing = await db.inventory.find_one(
        {"product_id": product_id, "branch_id": branch_id}, {"_id": 0}
    )
    old_quantity = float((existing or {}).get("quantity") or 0)
    if mode == "add":
        new_quantity = old_quantity + qty
    elif mode == "deduct":
        if qty > old_quantity:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot deduct {qty} — branch only has {old_quantity} in stock"
            )
        new_quantity = old_quantity - qty
    else:  # set
        new_quantity = qty
    diff = new_quantity - old_quantity

    # 5. Persist inventory (NO moving_avg_cost / last_purchase_cost touch)
    if existing:
        await db.inventory.update_one(
            {"product_id": product_id, "branch_id": branch_id},
            {"$set": {"quantity": new_quantity, "updated_at": now_iso()}},
        )
    else:
        await db.inventory.insert_one({
            "id": new_id(),
            "product_id": product_id,
            "branch_id": branch_id,
            "quantity": new_quantity,
            "updated_at": now_iso(),
        })

    # 6. Audit record
    injection_id = new_id()
    injection = {
        "id": injection_id,
        "product_id": product_id,
        "product_name": product.get("name", ""),
        "sku": product.get("sku", ""),
        "branch_id": branch_id,
        "mode": mode,
        "quantity": qty,
        "old_quantity": old_quantity,
        "new_quantity": new_quantity,
        "difference": diff,
        "reason_type": reason_type,
        "reason_note": reason_note,
        "performed_by_id": user["id"],
        "performed_by_name": user.get("full_name", user["username"]),
        "created_at": now_iso(),
    }
    await db.stock_injections.insert_one(injection)
    del injection["_id"]

    # 7. Stock-movement ledger entry — tagged 'injection' so Stock Movements
    #    view + Audit Pulse red-flag card can filter on it.
    await log_movement(
        product_id, branch_id, "injection", diff,
        injection_id, f"INJ-{mode.upper()}", 0, user["id"],
        user.get("full_name", user["username"]),
        f"[{reason_type}] {reason_note}",
    )

    return {
        "message": "Stock injection recorded",
        "mode": mode,
        "old_quantity": old_quantity,
        "new_quantity": new_quantity,
        "difference": diff,
        "injection": injection,
    }


@router.get("/injections")
async def list_stock_injections(
    product_id: str = "",
    branch_id: str = "",
    limit: int = 50,
    user=Depends(get_current_user),
):
    """List recent stock injections for audit view. Filters: product_id, branch_id."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    q: dict = {}
    if product_id:
        q["product_id"] = product_id
    if branch_id:
        q["branch_id"] = branch_id
    rows = await db.stock_injections.find(q, {"_id": 0}).sort("created_at", -1).limit(limit).to_list(limit)
    return {"injections": rows, "total": len(rows)}


@router.get("/corrections/{product_id}")
async def get_inventory_corrections(product_id: str, user=Depends(get_current_user)):
    """Get correction history for a specific product."""
    corrections = await db.inventory_corrections.find(
        {"product_id": product_id}, {"_id": 0}
    ).sort("created_at", -1).to_list(100)
    return corrections

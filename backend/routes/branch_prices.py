"""
Branch Price Overrides
======================
Stores per-branch price overrides for products.
Falls back to product.prices (global default) when no override exists.

Schema per document:
  { product_id, branch_id, prices: {scheme_key: price}, cost_price, ... }

Fallback chain: branch_prices → product.prices (global)
"""
import math
from fastapi import APIRouter, Depends, HTTPException
from typing import Optional
from config import db
from utils import get_current_user, check_perm, now_iso, new_id

router = APIRouter(prefix="/branch-prices", tags=["Branch Prices"])


@router.get("")
async def list_branch_prices(
    branch_id: Optional[str] = None,
    product_id: Optional[str] = None,
    user=Depends(get_current_user)
):
    """
    Flexible filter: by branch_id, product_id, or both.
    - branch_id only  → all overrides for a branch (for bulk sync / BranchPricesPage)
    - product_id only → all overrides across branches for one product (ProductDetailPage)
    - both            → single record lookup
    """
    query = {}
    if branch_id:
        query["branch_id"] = branch_id
    if product_id:
        query["product_id"] = product_id

    overrides = await db.branch_prices.find(query, {"_id": 0}).to_list(10000)
    return overrides


@router.put("/{product_id}")
async def upsert_branch_price(product_id: str, data: dict, user=Depends(get_current_user)):
    """
    Set or update branch-specific prices for a product.
    Body: { branch_id, prices: {scheme_key: price}, cost_price?, pin? }
    """
    check_perm(user, "products", "edit")

    # PIN enforcement for price override
    pin = data.get("pin", "")
    if pin:
        from routes.verify import verify_pin_for_action
        verifier = await verify_pin_for_action(pin, "price_override")
        if not verifier:
            raise HTTPException(status_code=403, detail="Invalid PIN")

    branch_id = data.get("branch_id")
    if not branch_id:
        raise HTTPException(status_code=400, detail="branch_id is required")

    product = await db.products.find_one({"id": product_id, "active": True}, {"_id": 0})
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    # Validate prices > 0 (can be 0 to clear, but not negative)
    prices = data.get("prices", {})
    for scheme, price in prices.items():
        if float(price) < 0:
            raise HTTPException(status_code=400, detail=f"Price for {scheme} cannot be negative")

    existing = await db.branch_prices.find_one(
        {"product_id": product_id, "branch_id": branch_id}, {"_id": 0}
    )

    update_doc = {
        "product_id": product_id,
        "branch_id": branch_id,
        "prices": prices,
        "updated_at": now_iso(),
        "updated_by_id": user["id"],
        "updated_by_name": user.get("full_name", user["username"]),
    }
    # Optional branch-specific cost (landed cost may differ by transport)
    if "cost_price" in data and data["cost_price"] is not None:
        update_doc["cost_price"] = float(data["cost_price"])

    if existing:
        await db.branch_prices.update_one(
            {"product_id": product_id, "branch_id": branch_id},
            {"$set": update_doc}
        )
    else:
        update_doc["id"] = new_id()
        update_doc["created_at"] = now_iso()
        await db.branch_prices.insert_one(update_doc)

    result = await db.branch_prices.find_one(
        {"product_id": product_id, "branch_id": branch_id}, {"_id": 0}
    )
    return result


@router.delete("/{product_id}")
async def delete_branch_price(
    product_id: str,
    branch_id: str,
    user=Depends(get_current_user)
):
    """Remove branch price override — product reverts to global default prices."""
    check_perm(user, "products", "edit")

    result = await db.branch_prices.delete_one(
        {"product_id": product_id, "branch_id": branch_id}
    )
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="No override found for this product/branch")

    return {"message": "Override removed — product now uses global default prices"}



@router.post("/bulk-update")
async def bulk_update_branch_prices(data: dict, user=Depends(get_current_user)):
    """
    Batch upsert branch price overrides for multiple products.
    Body: { items: [{product_id, branch_id, prices: {scheme_key: price}, cost_price?, set_manual?}] }
    - If set_manual=True: sets capital_method="manual" on the global product doc
    - prices and cost_price are each optional — only provided keys are written
    """
    check_perm(user, "products", "edit")
    items = data.get("items", [])
    if not items:
        raise HTTPException(status_code=400, detail="No items provided")

    updated = 0
    errors = []

    for item in items:
        pid = item.get("product_id")
        bid = item.get("branch_id")
        new_prices = item.get("prices") or {}
        new_capital = item.get("cost_price")
        set_manual = item.get("set_manual", False)

        if not pid or not bid:
            continue

        try:
            existing = await db.branch_prices.find_one(
                {"product_id": pid, "branch_id": bid}, {"_id": 0}
            )
            set_doc = {"updated_at": now_iso(),
                       "updated_by_id": user["id"],
                       "updated_by_name": user.get("full_name", user["username"])}

            if new_prices:
                base = (existing.get("prices") or {}) if existing else {}
                merged = {**base, **{k: float(v) for k, v in new_prices.items() if v is not None}}
                set_doc["prices"] = merged
            if new_capital is not None:
                set_doc["cost_price"] = float(new_capital)
                set_doc["source"] = "manual_override"

            if existing:
                await db.branch_prices.update_one(
                    {"product_id": pid, "branch_id": bid}, {"$set": set_doc}
                )
            else:
                set_doc.update({"id": new_id(), "product_id": pid, "branch_id": bid,
                                "created_at": now_iso(),
                                "prices": set_doc.get("prices", {})})
                await db.branch_prices.insert_one(set_doc)

            # If set_manual: flag global product as manually managed capital
            if set_manual:
                await db.products.update_one(
                    {"id": pid},
                    {"$set": {"capital_method": "manual", "updated_at": now_iso()}}
                )
            updated += 1
        except Exception as e:
            errors.append(f"{pid}/{bid}: {e}")

    return {"updated": updated, "errors": errors}


async def get_capital_summary(source_branch_id: str, user=Depends(get_current_user)):
    """
    Return per-category capital stats from the source branch (for reference display only).
    Effective cost per product = branch override if set, else product.cost_price.
    """
    products = await db.products.find(
        {"active": True, "is_repack": {"$ne": True}},
        {"_id": 0, "id": 1, "category": 1, "cost_price": 1}
    ).to_list(10000)

    overrides = await db.branch_prices.find(
        {"branch_id": source_branch_id},
        {"_id": 0, "product_id": 1, "cost_price": 1}
    ).to_list(10000)
    override_map = {o["product_id"]: o["cost_price"] for o in overrides if o.get("cost_price") is not None}

    categories = {}
    for p in products:
        cat = p.get("category", "General")
        cost = float(override_map.get(p["id"], p.get("cost_price") or 0))
        if cat not in categories:
            categories[cat] = {"category": cat, "count": 0, "costs": []}
        categories[cat]["count"] += 1
        if cost > 0:
            categories[cat]["costs"].append(cost)

    result = []
    for cat, d in sorted(categories.items()):
        costs = d["costs"]
        result.append({
            "category": cat,
            "product_count": d["count"],
            "min_cost": round(min(costs), 2) if costs else 0,
            "max_cost": round(max(costs), 2) if costs else 0,
            "avg_cost": round(sum(costs) / len(costs), 2) if costs else 0,
        })
    return result


@router.post("/quick-fill")
async def quick_fill_branch_capital(data: dict, user=Depends(get_current_user)):
    """
    Bulk-set cost_price for a target branch.

    For each selected category:
      new_cost = source_product_cost + add_on  (flat or percent)

    source_product_cost = branch_prices.cost_price at source_branch (if override exists)
                          else product.cost_price (global)

    Rules:
      - Skip products that already have a cost_price override at the target branch.
      - Return skipped items in report so user can review.
    """
    check_perm(user, "products", "edit")

    source_branch_id = data.get("source_branch_id")
    target_branch_id = data.get("target_branch_id")
    rules_raw = data.get("category_rules", [])

    if not source_branch_id or not target_branch_id:
        raise HTTPException(status_code=400, detail="source_branch_id and target_branch_id required")
    if source_branch_id == target_branch_id:
        raise HTTPException(status_code=400, detail="Source and target must be different branches")

    # Only process selected categories
    category_rules = {
        r["category"]: r
        for r in rules_raw
        if r.get("selected") and r.get("add_on_value") is not None
    }
    if not category_rules:
        raise HTTPException(status_code=400, detail="No categories selected")

    # Load all parent products in selected categories
    products = await db.products.find(
        {"active": True, "is_repack": {"$ne": True}, "category": {"$in": list(category_rules.keys())}},
        {"_id": 0, "id": 1, "name": 1, "sku": 1, "category": 1, "cost_price": 1}
    ).to_list(10000)

    # Source branch cost overrides (for effective capital at source)
    src_overrides = await db.branch_prices.find(
        {"branch_id": source_branch_id}, {"_id": 0, "product_id": 1, "cost_price": 1}
    ).to_list(10000)
    src_cost_map = {o["product_id"]: o["cost_price"] for o in src_overrides if o.get("cost_price") is not None}

    # Target branch existing cost overrides (products to SKIP)
    tgt_overrides = await db.branch_prices.find(
        {"branch_id": target_branch_id}, {"_id": 0, "product_id": 1, "cost_price": 1}
    ).to_list(10000)
    tgt_has_cost = {o["product_id"]: o["cost_price"] for o in tgt_overrides if o.get("cost_price") is not None}

    applied = []
    skipped = []

    for p in products:
        cat = p.get("category", "General")
        rule = category_rules.get(cat)
        if not rule:
            continue

        # Effective source cost: branch override first, then global product cost
        source_cost = float(src_cost_map.get(p["id"]) if p["id"] in src_cost_map else (p.get("cost_price") or 0))

        # Skip if target already has a manual cost override for this product
        if p["id"] in tgt_has_cost:
            skipped.append({
                "name": p["name"], "sku": p.get("sku", ""), "category": cat,
                "source_cost": source_cost,
                "existing_cost": tgt_has_cost[p["id"]],
                "reason": "Already has custom cost — not overwritten",
            })
            continue

        # Compute new cost: per-product source cost + category add-on
        add_on_type = rule.get("add_on_type", "flat")
        add_on_value = float(rule.get("add_on_value", 0))
        if add_on_type == "percent":
            new_cost = round(source_cost * (1 + add_on_value / 100), 2)
            add_on_display = f"+{add_on_value}%"
        else:
            new_cost = round(source_cost + add_on_value, 2)
            add_on_display = f"+₱{add_on_value:.2f}"

        # Upsert: create record or update existing (which only had prices{}, not cost_price)
        existing_tgt = await db.branch_prices.find_one(
            {"product_id": p["id"], "branch_id": target_branch_id}, {"_id": 0}
        )
        if existing_tgt:
            await db.branch_prices.update_one(
                {"product_id": p["id"], "branch_id": target_branch_id},
                {"$set": {"cost_price": new_cost, "updated_at": now_iso(),
                           "updated_by_name": user.get("full_name", user["username"])}}
            )
        else:
            await db.branch_prices.insert_one({
                "id": new_id(), "product_id": p["id"], "branch_id": target_branch_id,
                "prices": {}, "cost_price": new_cost,
                "created_at": now_iso(), "updated_at": now_iso(),
                "updated_by_id": user["id"],
                "updated_by_name": user.get("full_name", user["username"]),
            })

        applied.append({
            "name": p["name"], "sku": p.get("sku", ""), "category": cat,
            "source_cost": source_cost,
            "add_on": add_on_display,
            "new_cost": new_cost,
        })

    return {
        "applied": len(applied),
        "skipped": len(skipped),
        "applied_items": applied,
        "skipped_items": skipped,
        "summary": f"Set capital for {len(applied)} products. Skipped {len(skipped)} (already had custom prices).",
    }

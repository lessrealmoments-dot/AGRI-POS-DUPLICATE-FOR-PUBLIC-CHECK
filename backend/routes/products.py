"""
Product management routes: CRUD, repacks, pricing, search, barcodes.
"""
from fastapi import APIRouter, Depends, HTTPException
from typing import Optional, List
from config import db
from utils import get_current_user, check_perm, has_perm, now_iso, new_id, get_product_price
import random

router = APIRouter(prefix="/products", tags=["Products"])


@router.get("")
async def list_products(
    user=Depends(get_current_user),
    search: Optional[str] = None,
    category: Optional[str] = None,
    is_repack: Optional[bool] = None,
    parent_id: Optional[str] = None,
    sort_by: Optional[str] = "name",   # "name" | "type" | "grouped"
    skip: int = 0,
    limit: int = 50
):
    """List products with optional filters.
    sort_by:
      name    — alphabetical by product name (default)
      type    — parents first (A-Z), then repacks (A-Z)
      grouped — parents A-Z, each parent's repacks immediately below (tree order)
    """
    query = {"active": True}
    if search:
        query["$or"] = [
            {"name": {"$regex": search, "$options": "i"}},
            {"sku": {"$regex": search, "$options": "i"}},
            {"barcode": {"$regex": search, "$options": "i"}},
        ]
    if category:
        query["category"] = category
    if is_repack is not None:
        query["is_repack"] = is_repack
    if parent_id:
        query["parent_id"] = parent_id

    if sort_by == "grouped":
        # Aggregation: lookup parent name → sort by [parent_name_or_own, is_repack, name]
        pipeline = [
            {"$match": query},
            {"$lookup": {
                "from": "products",
                "localField": "parent_id",
                "foreignField": "id",
                "as": "_parent_doc"
            }},
            {"$addFields": {
                "_sort_key": {
                    "$cond": {
                        "if": {"$eq": ["$is_repack", True]},
                        "then": {"$toLower": {"$ifNull": [{"$arrayElemAt": ["$_parent_doc.name", 0]}, "$name"]}},
                        "else": {"$toLower": "$name"}
                    }
                },
                "_is_repack_int": {"$cond": [{"$eq": ["$is_repack", True]}, 1, 0]}
            }},
            {"$sort": {"_sort_key": 1, "_is_repack_int": 1, "name": 1}},
            {"$project": {"_id": 0, "_parent_doc": 0, "_sort_key": 0, "_is_repack_int": 0}},
        ]
        count_pipeline = [{"$match": query}, {"$count": "total"}]
        count_result = await db.products.aggregate(count_pipeline).to_list(1)
        total = count_result[0]["total"] if count_result else 0
        pipeline += [{"$skip": skip}, {"$limit": limit}]
        products = await db.products.aggregate(pipeline).to_list(limit)
        return {"products": products, "total": total, "skip": skip, "limit": limit}

    elif sort_by == "type":
        mongo_sort = [("is_repack", 1), ("name", 1)]   # parents (False=0) before repacks (True=1)
    else:
        mongo_sort = [("name", 1)]  # default: alphabetical

    total = await db.products.count_documents(query)
    products = await db.products.find(query, {"_id": 0}).sort(mongo_sort).skip(skip).limit(limit).to_list(limit)
    return {"products": products, "total": total, "skip": skip, "limit": limit}


@router.post("")
async def create_product(data: dict, user=Depends(get_current_user)):
    """Create a new product."""
    check_perm(user, "products", "create")

    # Prevent duplicate product names (case-insensitive)
    name = data.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Product name is required")
    name_conflict = await db.products.find_one(
        {"name": {"$regex": f"^{name}$", "$options": "i"}, "active": True}, {"_id": 0, "name": 1}
    )
    if name_conflict:
        raise HTTPException(status_code=400, detail=f"A product named \"{name_conflict['name']}\" already exists. Product names must be unique.")

    sku = data.get("sku", "").strip()
    if sku:
        existing = await db.products.find_one({"sku": sku, "active": True}, {"_id": 0})
        if existing:
            raise HTTPException(status_code=400, detail="SKU already exists")
    else:
        sku = f"P-{new_id()[:8].upper()}"
    
    product = {
        "id": new_id(),
        "sku": sku,
        "name": name,
        "category": data.get("category", "General"),
        "description": data.get("description", ""),
        "unit": data.get("unit", "Piece"),
        "cost_price": float(data.get("cost_price", 0)),
        "prices": data.get("prices", {}),
        "parent_id": data.get("parent_id", None),
        "is_repack": bool(data.get("is_repack", False)),
        "units_per_parent": data.get("units_per_parent", None),
        "repack_unit": data.get("repack_unit", None),
        "barcode": data.get("barcode", ""),
        "product_type": data.get("product_type", "stockable"),
        "capital_method": data.get("capital_method", "last_purchase"),
        "reorder_point": float(data.get("reorder_point", 0)),
        "reorder_quantity": float(data.get("reorder_quantity", 0)),
        "unit_of_measurement": data.get("unit_of_measurement", data.get("unit", "Piece")),
        "last_vendor": data.get("last_vendor", ""),
        "active": True,
        "created_at": now_iso(),
    }
    await db.products.insert_one(product)
    del product["_id"]
    return product


@router.get("/search-detail")
async def search_products_detail(q: str = "", branch_id: Optional[str] = None, also_branch_id: Optional[str] = None, user=Depends(get_current_user)):
    """Enhanced product search with stock, branch prices, and capital reference data.
    also_branch_id: optional second branch to include stock levels for (used by Request Stock form)."""
    if not q or len(q) < 1:
        return []
    
    query = {"active": True, "$or": [
        {"name": {"$regex": q, "$options": "i"}},
        {"sku": {"$regex": q, "$options": "i"}},
        {"barcode": {"$regex": q, "$options": "i"}},
    ]}
    products = await db.products.find(query, {"_id": 0}).limit(10).to_list(10)
    results = []
    
    for p in products:
        # Apply branch price overrides
        if branch_id:
            override = await db.branch_prices.find_one(
                {"product_id": p["id"], "branch_id": branch_id}, {"_id": 0}
            )
            if override and override.get("prices"):
                merged_prices = {**(p.get("prices") or {}), **override["prices"]}
                p = {**p, "prices": merged_prices}
                if override.get("cost_price") is not None:
                    p = {**p, "cost_price": override["cost_price"]}

        # ── Capital reference data (moving average + last purchase from PO history)
        # Used as reference info in the POS price editor
        lookup_id = p.get("parent_id") if p.get("is_repack") and p.get("parent_id") else p["id"]

        # Last acquisition price — branch-specific, includes POs + transfers
        acq_query = {"product_id": lookup_id, "type": {"$in": ["purchase", "transfer_in"]}, "quantity_change": {"$gt": 0}}
        if branch_id:
            acq_query["branch_id"] = branch_id
        last_acq = await db.movements.find_one(acq_query, {"_id": 0}, sort=[("created_at", -1)])
        last_purchase_cost = float(last_acq.get("price_at_time", 0)) if last_acq else 0.0
        if last_purchase_cost > 0 and p.get("is_repack") and p.get("units_per_parent", 1) > 1:
            last_purchase_cost = round(last_purchase_cost / p["units_per_parent"], 4)

        # Moving average cost — branch-specific, includes POs + transfers
        all_acqs = await db.movements.find(acq_query, {"_id": 0}).to_list(10000)
        total_acq_qty = sum(m["quantity_change"] for m in all_acqs)
        total_acq_cost = sum(m["quantity_change"] * m.get("price_at_time", 0) for m in all_acqs)
        if total_acq_qty > 0:
            moving_average_cost = round(total_acq_cost / total_acq_qty, 4)
            if p.get("is_repack") and p.get("units_per_parent", 1) > 1:
                moving_average_cost = round(moving_average_cost / p["units_per_parent"], 4)
        else:
            moving_average_cost = float(p.get("cost_price", 0))

        # Effective capital = the cost the system uses for below-capital validation
        capital_method = p.get("capital_method", "manual")
        if capital_method == "moving_average":
            effective_capital = moving_average_cost
        elif capital_method == "last_purchase":
            effective_capital = last_purchase_cost or float(p.get("cost_price", 0))
        else:
            effective_capital = float(p.get("cost_price", 0))

        capital_data = {
            "moving_average_cost": moving_average_cost,
            "last_purchase_cost": last_purchase_cost,
            "effective_capital": effective_capital,
            "capital_method": capital_method,
        }

        # Stock & availability
        if p.get("is_repack") and p.get("parent_id"):
            parent = await db.products.find_one({"id": p["parent_id"]}, {"_id": 0})
            if branch_id:
                pinv = await db.inventory.find_one(
                    {"product_id": p["parent_id"], "branch_id": branch_id}, {"_id": 0}
                )
                parent_stock = float(pinv["quantity"]) if pinv else 0
            else:
                # No branch — sum all branches
                agg = await db.inventory.aggregate([
                    {"$match": {"product_id": p["parent_id"]}},
                    {"$group": {"_id": None, "total": {"$sum": "$quantity"}}}
                ]).to_list(1)
                parent_stock = float(agg[0]["total"]) if agg else 0
            units_per_parent = p.get("units_per_parent", 1)
            result = {
                **p, **capital_data,
                "available": parent_stock * units_per_parent,
                "reserved": 0, "coming": 0,
                "parent_name": parent["name"] if parent else "",
                "parent_stock": parent_stock,
                "parent_unit": parent["unit"] if parent else "",
                "derived_from_parent": True,
            }
        else:
            if branch_id:
                inv = await db.inventory.find_one(
                    {"product_id": p["id"], "branch_id": branch_id}, {"_id": 0}
                )
                available = float(inv["quantity"]) if inv else 0
            else:
                agg = await db.inventory.aggregate([
                    {"$match": {"product_id": p["id"]}},
                    {"$group": {"_id": None, "total": {"$sum": "$quantity"}}}
                ]).to_list(1)
                available = float(agg[0]["total"]) if agg else 0

            coming_r = await db.purchase_orders.aggregate([
                {"$match": {"status": {"$in": ["ordered", "draft"]}, **({"branch_id": branch_id} if branch_id else {})}},
                {"$unwind": "$items"},
                {"$match": {"items.product_id": p["id"]}},
                {"$group": {"_id": None, "t": {"$sum": "$items.quantity"}}}
            ]).to_list(1)

            reserved_r = await db.sale_reservations.aggregate([
                {"$match": {"product_id": p["id"], "qty_remaining": {"$gt": 0}, **({"branch_id": branch_id} if branch_id else {})}},
                {"$group": {"_id": None, "t": {"$sum": "$qty_remaining"}}}
            ]).to_list(1)

            result = {
                **p, **capital_data,
                "available": available,
                "reserved": reserved_r[0]["t"] if reserved_r else 0,
                "coming": coming_r[0]["t"] if coming_r else 0,
            }
        # ── Also-branch stock (for Request Stock dual-view) ──────────────
        if also_branch_id and also_branch_id != branch_id:
            also_inv = await db.inventory.find_one(
                {"product_id": p["id"], "branch_id": also_branch_id}, {"_id": 0}
            )
            result["also_branch_stock"] = float(also_inv["quantity"]) if also_inv else 0

        results.append(result)
    
    return results



# ── Barcode helpers ─────────────────────────────────────────────────────────
async def _generate_unique_barcode() -> str:
    """Generate a unique barcode with AG prefix + 8-digit number."""
    for _ in range(100):  # max retries
        num = random.randint(10000000, 99999999)
        code = f"AG{num}"
        exists = await db.products.find_one({"barcode": code}, {"_id": 0, "id": 1})
        if not exists:
            return code
    raise HTTPException(status_code=500, detail="Could not generate unique barcode after 100 attempts")


@router.get("/barcode-lookup/{barcode}")
async def barcode_lookup(barcode: str, branch_id: Optional[str] = None, user=Depends(get_current_user)):
    """Look up a product by its barcode. Returns enriched data similar to search-detail."""
    product = await db.products.find_one({"barcode": barcode, "active": True}, {"_id": 0})
    if not product:
        raise HTTPException(status_code=404, detail="No product found with this barcode")

    p = product
    # Apply branch price overrides
    if branch_id:
        override = await db.branch_prices.find_one(
            {"product_id": p["id"], "branch_id": branch_id}, {"_id": 0}
        )
        if override and override.get("prices"):
            merged_prices = {**(p.get("prices") or {}), **override["prices"]}
            p = {**p, "prices": merged_prices}
            if override.get("cost_price") is not None:
                p = {**p, "cost_price": override["cost_price"]}

    # Stock
    if branch_id:
        inv = await db.inventory.find_one(
            {"product_id": p["id"], "branch_id": branch_id}, {"_id": 0}
        )
        available = float(inv["quantity"]) if inv else 0
    else:
        agg = await db.inventory.aggregate([
            {"$match": {"product_id": p["id"]}},
            {"$group": {"_id": None, "total": {"$sum": "$quantity"}}}
        ]).to_list(1)
        available = float(agg[0]["total"]) if agg else 0

    return {**p, "available": available}


@router.post("/{product_id}/generate-barcode")
async def generate_barcode(product_id: str, user=Depends(get_current_user)):
    """Generate a unique barcode for a product that doesn't have one."""
    check_perm(user, "products", "edit")
    product = await db.products.find_one({"id": product_id, "active": True}, {"_id": 0})
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    if product.get("barcode"):
        return {"barcode": product["barcode"], "already_existed": True}

    barcode = await _generate_unique_barcode()
    await db.products.update_one({"id": product_id}, {"$set": {"barcode": barcode, "updated_at": now_iso()}})
    return {"barcode": barcode, "already_existed": False}


@router.post("/generate-barcodes-bulk")
async def generate_barcodes_bulk(user=Depends(get_current_user)):
    """Generate barcodes for all parent products that don't have one yet."""
    check_perm(user, "products", "edit")
    # Only target parent products (not repacks) without a barcode
    products = await db.products.find(
        {"active": True, "is_repack": {"$ne": True}, "$or": [{"barcode": ""}, {"barcode": None}, {"barcode": {"$exists": False}}]},
        {"_id": 0, "id": 1, "name": 1}
    ).to_list(10000)

    generated = []
    for p in products:
        barcode = await _generate_unique_barcode()
        await db.products.update_one({"id": p["id"]}, {"$set": {"barcode": barcode, "updated_at": now_iso()}})
        generated.append({"id": p["id"], "name": p["name"], "barcode": barcode})

    return {"generated": len(generated), "products": generated}


@router.post("/barcode-check")
async def barcode_check(data: dict, user=Depends(get_current_user)):
    """Check if a barcode is already in use. Returns the product if found."""
    barcode = data.get("barcode", "").strip()
    if not barcode:
        raise HTTPException(status_code=400, detail="Barcode is required")
    exclude_product_id = data.get("exclude_product_id", "")
    query = {"barcode": barcode, "active": True}
    if exclude_product_id:
        query["id"] = {"$ne": exclude_product_id}
    existing = await db.products.find_one(query, {"_id": 0, "id": 1, "name": 1, "sku": 1, "barcode": 1})
    if existing:
        return {"duplicate": True, "product": existing}
    return {"duplicate": False}



@router.get("/barcode-inventory/{branch_id}")
async def barcode_inventory_for_print(branch_id: str, user=Depends(get_current_user)):
    """Get parent products with barcodes that have inventory in the given branch, with stock counts."""
    # Get all inventory for this branch
    inv_list = await db.inventory.find(
        {"branch_id": branch_id, "quantity": {"$gt": 0}}, {"_id": 0}
    ).to_list(10000)
    inv_map = {i["product_id"]: float(i["quantity"]) for i in inv_list}

    if not inv_map:
        return {"products": []}

    # Get parent products with barcodes that have inventory
    products = await db.products.find(
        {"id": {"$in": list(inv_map.keys())}, "active": True, "is_repack": {"$ne": True},
         "barcode": {"$exists": True, "$nin": ["", None]}},
        {"_id": 0, "id": 1, "name": 1, "sku": 1, "barcode": 1, "category": 1}
    ).to_list(10000)

    result = []
    for p in products:
        if p.get("barcode"):
            p["stock"] = inv_map.get(p["id"], 0)
            result.append(p)

    return {"products": result}




@router.get("/export-csv")
async def export_products_csv(user=Depends(get_current_user)):
    """
    Export all active products as a CSV file with import-compatible columns.
    The output can be edited in Excel and re-uploaded via /import (Update Existing mode)
    to apply targeted bulk changes — perfect for migration cleanup.
    """
    check_perm(user, "products", "view")

    import io
    import csv as _csv
    from fastapi.responses import StreamingResponse

    schemes = await db.price_schemes.find({"active": True}, {"_id": 0, "key": 1, "name": 1}).to_list(50)
    products = await db.products.find(
        {"active": True}, {"_id": 0}
    ).sort("name", 1).to_list(length=None)

    # Header row
    headers = [
        "Product Name", "SKU", "Category", "Unit", "Description",
        "Type", "Cost Price", "Reorder Point", "Barcode",
    ]
    # One column per active scheme — column header doubles as label and import-mapping hint
    for s in schemes:
        headers.append(f"{s['name']} Price")

    buf = io.StringIO()
    writer = _csv.writer(buf, quoting=_csv.QUOTE_MINIMAL)
    writer.writerow(headers)

    for p in products:
        prices = p.get("prices") or {}
        row = [
            p.get("name", ""),
            p.get("sku", ""),
            p.get("category", ""),
            p.get("unit", ""),
            p.get("description", ""),
            p.get("product_type", "stockable"),
            p.get("cost_price", 0) or "",
            p.get("reorder_point", 0) or "",
            p.get("barcode", ""),
        ]
        for s in schemes:
            v = prices.get(s["key"])
            row.append(v if (v is not None and v != 0) else "")
        writer.writerow(row)

    buf.seek(0)
    return StreamingResponse(
        io.BytesIO(buf.getvalue().encode("utf-8-sig")),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=agribooks_products_{now_iso()[:10]}.csv"},
    )


@router.get("/categories")
async def list_categories(user=Depends(get_current_user)):
    """Get all product categories — merges categories from existing products + manually defined ones."""
    from_products = await db.products.distinct("category", {"active": True})
    manual_cursor = db.product_categories.find({}, {"_id": 0, "name": 1})
    manual_docs = await manual_cursor.to_list(length=None)
    manual = [d["name"] for d in manual_docs if d.get("name")]
    merged = sorted(set([c for c in (from_products + manual) if c]))
    return merged


@router.post("/categories")
async def create_category(data: dict, user=Depends(get_current_user)):
    """Add a new custom category for this organisation."""
    check_perm(user, "products", "edit")
    name = (data.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Category name is required")
    # Idempotent — don't duplicate
    existing = await db.product_categories.find_one({"name": name})
    if not existing:
        await db.product_categories.insert_one({"name": name, "created_at": now_iso()})
    return {"name": name}


@router.delete("/categories/{name}")
async def delete_category(name: str, user=Depends(get_current_user)):
    """Remove a custom category. Fails if any active products still use it."""
    check_perm(user, "products", "edit")
    in_use = await db.products.count_documents({"active": True, "category": name})
    if in_use:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot delete: {in_use} product(s) still use this category. Reassign them first."
        )
    await db.product_categories.delete_many({"name": name})
    return {"deleted": name}


@router.get("/pricing-scan")
async def pricing_scan(
    branch_id: Optional[str] = None,
    notify: bool = False,
    user=Depends(get_current_user),
):
    """
    Scan for products where any price scheme is below cost.
    Returns list of issues with product details, cost references, and current prices.
    If notify=true, creates a system notification for admins + branch managers.
    """
    # Load all price schemes to know the keys
    schemes = await db.price_schemes.find({"active": True}, {"_id": 0}).to_list(50)
    scheme_keys = [s["key"] for s in schemes]

    # Pre-load branch_prices for the branch
    bp_map = {}
    if branch_id:
        bp_docs = await db.branch_prices.find(
            {"branch_id": branch_id}, {"_id": 0}
        ).to_list(5000)
        bp_map = {d["product_id"]: d for d in bp_docs}

    # Load all active non-repack products
    products = await db.products.find(
        {"active": True, "is_repack": {"$ne": True}},
        {"_id": 0}
    ).to_list(5000)

    # Load repack products separately (cost derived from parent)
    repacks = await db.products.find(
        {"active": True, "is_repack": True},
        {"_id": 0}
    ).to_list(5000)

    # Build parent cost map for repack cost derivation
    parent_ids = list({r["parent_id"] for r in repacks if r.get("parent_id")})
    parent_cost_map = {}
    if parent_ids:
        parents = await db.products.find(
            {"id": {"$in": parent_ids}, "active": True},
            {"_id": 0, "id": 1, "cost_price": 1, "name": 1}
        ).to_list(len(parent_ids))
        parent_cost_map = {p["id"]: p for p in parents}

    # Compute effective cost for repacks and add to products list
    for r in repacks:
        parent = parent_cost_map.get(r.get("parent_id"))
        if not parent:
            continue
        parent_cost = float(parent.get("cost_price", 0))
        # Check if branch has a cost override for the parent
        if branch_id:
            bp_parent = bp_map.get(r.get("parent_id"))
            if bp_parent and bp_parent.get("cost_price") is not None:
                parent_cost = float(bp_parent["cost_price"])
        units = max(r.get("units_per_parent", 1), 1)
        r["_derived_cost"] = round(parent_cost / units, 4)
        r["_parent_name"] = parent.get("name", "")
        products.append(r)

    issues = []
    for p in products:
        is_repack = p.get("is_repack", False)

        # Effective cost: use derived cost for repacks, or branch/global cost for parents
        if is_repack and "_derived_cost" in p:
            effective_cost = p["_derived_cost"]
        else:
            effective_cost = float(p.get("cost_price", 0))
            bp = bp_map.get(p["id"])
            if bp and bp.get("cost_price") is not None:
                effective_cost = float(bp["cost_price"])

        if effective_cost <= 0:
            continue

        bp = bp_map.get(p["id"])
        global_prices = {k: float(v or 0) for k, v in (p.get("prices") or {}).items()}
        effective_prices = dict(global_prices)
        if bp and bp.get("prices"):
            for k, v in bp["prices"].items():
                effective_prices[k] = float(v or 0)

        problem_schemes = []
        critical_keys = ('retail',) if is_repack else ('retail', 'wholesale')
        for key in scheme_keys:
            price_val = effective_prices.get(key, 0)
            if price_val > 0 and price_val < effective_cost:
                is_critical = key in critical_keys
                problem_schemes.append({
                    "scheme_key": key,
                    "scheme_name": next((s["name"] for s in schemes if s["key"] == key), key),
                    "current_price": price_val,
                    "deficit": round(effective_cost - price_val, 2),
                    "is_critical": is_critical,
                })

        # Only flag the product if at least one CRITICAL scheme (retail/wholesale) is below cost
        critical_problems = [ps for ps in problem_schemes if ps["is_critical"]]
        if not critical_problems:
            continue

        # For repacks, look up parent's purchase history for moving avg
        lookup_id = p.get("parent_id") if is_repack and p.get("parent_id") else p["id"]
        units_per = max(p.get("units_per_parent", 1), 1) if is_repack else 1

        acq_query = {"product_id": lookup_id, "type": {"$in": ["purchase", "transfer_in"]}, "quantity_change": {"$gt": 0}}
        if branch_id:
            acq_query["branch_id"] = branch_id
        all_acqs = await db.movements.find(
            acq_query,
            {"_id": 0, "quantity_change": 1, "price_at_time": 1, "created_at": 1}
        ).to_list(1000)
        total_qty = sum(m["quantity_change"] for m in all_acqs)
        total_cost_val = sum(m["quantity_change"] * m.get("price_at_time", 0) for m in all_acqs)
        moving_avg = round(total_cost_val / total_qty / units_per, 2) if total_qty > 0 else effective_cost

        last_acq_entry = await db.movements.find_one(
            acq_query,
            {"_id": 0, "price_at_time": 1},
            sort=[("created_at", -1)]
        )
        last_purchase = round(float(last_acq_entry.get("price_at_time", effective_cost)) / units_per, 2) if last_acq_entry else effective_cost

        issue_entry = {
            "product_id": p["id"],
            "product_name": p["name"],
            "sku": p.get("sku", ""),
            "category": p.get("category", ""),
            "unit": p.get("unit", p.get("repack_unit", "")),
            "effective_cost": effective_cost,
            "global_cost": float(p.get("cost_price", 0)),
            "moving_average": moving_avg,
            "last_purchase": last_purchase,
            "prices": effective_prices,
            "problem_schemes": problem_schemes,
            "critical_count": len(critical_problems),
            "is_branch_specific_cost": bp is not None and bp.get("cost_price") is not None,
            "is_repack": is_repack,
        }
        if is_repack:
            issue_entry["parent_name"] = p.get("_parent_name", "")
            issue_entry["units_per_parent"] = p.get("units_per_parent", 1)
        issues.append(issue_entry)

    if notify and issues:
        admins = await db.users.find({"role": "admin", "active": True}, {"_id": 0, "id": 1}).to_list(50)
        managers = []
        if branch_id:
            managers = await db.users.find(
                {"branch_id": branch_id, "active": True, "role": {"$in": ["manager"]}},
                {"_id": 0, "id": 1}
            ).to_list(50)
        target_ids = list({u["id"] for u in admins + managers})
        branch_doc = await db.branches.find_one({"id": branch_id}, {"_id": 0, "name": 1}) if branch_id else None
        branch_name = branch_doc.get("name", branch_id) if branch_doc else "All Branches"
        await db.notifications.insert_one({
            "id": new_id(),
            "type": "pricing_issue",
            "title": f"Pricing Issue Detected — {len(issues)} product(s)",
            "message": (
                f"{len(issues)} product(s) in {branch_name} have prices below capital. "
                f"Products: {', '.join(i['product_name'] for i in issues[:3])}"
                f"{' and more...' if len(issues) > 3 else '.'}"
            ),
            "branch_id": branch_id,
            "branch_name": branch_name,
            "metadata": {"issue_count": len(issues), "product_ids": [i["product_id"] for i in issues]},
            "target_user_ids": target_ids,
            "read_by": [],
            "created_at": now_iso(),
        })

    return {
        "issues": issues,
        "total": len(issues),
        "critical_total": sum(i["critical_count"] > 0 for i in issues),
        "branch_id": branch_id,
        "scanned_at": now_iso(),
        "schemes": [{"key": s["key"], "name": s["name"]} for s in schemes],
    }


@router.get("/price-audit-summary")
async def price_audit_summary(user=Depends(get_current_user)):
    """
    Returns counts (and first 100 IDs) for:
    - Products with no capital (cost_price == 0 or null)
    - Products with low margin across any price scheme (< ₱20 AND < 5%)
    Used by the Price Manager page smart prompts.
    """
    schemes = await db.price_schemes.find({"active": True}, {"_id": 0, "key": 1}).to_list(50)
    scheme_keys = [s["key"] for s in schemes]

    all_products = await db.products.find(
        {"active": True, "is_repack": {"$ne": True}},
        {"_id": 0, "id": 1, "name": 1, "sku": 1, "category": 1, "cost_price": 1, "prices": 1}
    ).to_list(5000)

    missing_capital = []
    low_margin = []

    for p in all_products:
        capital = float(p.get("cost_price") or 0)
        prices = p.get("prices") or {}

        if capital <= 0:
            missing_capital.append({"id": p["id"], "name": p["name"], "sku": p.get("sku", ""), "category": p.get("category", "")})
            continue

        # Check any scheme price for low margin
        flagged = False
        for key in scheme_keys:
            price = float(prices.get(key) or 0)
            if price <= 0:
                continue
            markup = price - capital
            pct = markup / capital if capital > 0 else 0
            if markup < 20 and pct < 0.05:
                flagged = True
                break
        if flagged:
            low_margin.append({"id": p["id"], "name": p["name"], "sku": p.get("sku", ""), "category": p.get("category", "")})

    return {
        "missing_capital": {"count": len(missing_capital), "products": missing_capital[:100]},
        "low_margin": {"count": len(low_margin), "products": low_margin[:100]},
    }


@router.post("/bulk-price-update")
async def bulk_price_update(data: dict, user=Depends(get_current_user)):
    """
    Batch update global product prices (products.prices).
    Body: { items: [{product_id, prices: {scheme_key: price}}] }
    Does NOT touch cost_price — selling prices only.
    """
    check_perm(user, "products", "edit")
    items = data.get("items", [])
    if not items:
        raise HTTPException(status_code=400, detail="No items provided")

    updated = 0
    errors = []
    for item in items:
        pid = item.get("product_id")
        new_prices = item.get("prices", {})
        if not pid or not new_prices:
            continue
        try:
            product = await db.products.find_one({"id": pid, "active": True}, {"_id": 0, "prices": 1})
            if not product:
                errors.append(f"{pid}: not found")
                continue
            merged = {**(product.get("prices") or {}), **{k: float(v) for k, v in new_prices.items() if v is not None}}
            await db.products.update_one(
                {"id": pid},
                {"$set": {"prices": merged, "updated_at": now_iso()}}
            )
            updated += 1
        except Exception as e:
            errors.append(f"{pid}: {e}")

    return {"updated": updated, "errors": errors}


@router.get("/{product_id}")
async def get_product(product_id: str, user=Depends(get_current_user)):
    """Get a single product by ID."""
    product = await db.products.find_one({"id": product_id, "active": True}, {"_id": 0})
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return product


@router.get("/{product_id}/detail")
async def get_product_detail(product_id: str, branch_id: Optional[str] = None, user=Depends(get_current_user)):
    """Get comprehensive product details including repacks, inventory, and vendors.
    
    When branch_id is provided, coming/reserved counts are filtered to that branch.
    """
    product = await db.products.find_one({"id": product_id, "active": True}, {"_id": 0})
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    
    # Get repacks
    repacks = await db.products.find({"parent_id": product_id, "active": True}, {"_id": 0}).to_list(100)
    
    # Get inventory across all branches
    inv_records = await db.inventory.find({"product_id": product_id}, {"_id": 0}).to_list(100)
    
    # Build on_hand map by branch
    on_hand = {}
    total_qty = 0
    for inv in inv_records:
        bid = inv.get("branch_id")
        qty = inv.get("quantity", 0)
        on_hand[bid] = qty
        total_qty += qty
    
    # Calculate coming (from purchase orders) — scoped to branch when provided
    coming_match = {"status": {"$in": ["ordered", "draft"]}}
    if branch_id:
        coming_match["branch_id"] = branch_id
    coming_r = await db.purchase_orders.aggregate([
        {"$match": coming_match},
        {"$unwind": "$items"},
        {"$match": {"items.product_id": product_id}},
        {"$group": {"_id": None, "t": {"$sum": "$items.quantity"}}}
    ]).to_list(1)
    coming = coming_r[0]["t"] if coming_r else 0
    
    # Calculate reserved — qty still pending customer pickup (from sale_reservations)
    # sale_reservations.qty_remaining > 0 means stock is physically on shelf but belongs to a customer
    reserved_match = {"qty_remaining": {"$gt": 0}}
    if branch_id:
        reserved_match["branch_id"] = branch_id
    reserved_r = await db.sale_reservations.aggregate([
        {"$match": {**reserved_match, "product_id": product_id}},
        {"$group": {"_id": None, "t": {"$sum": "$qty_remaining"}}}
    ]).to_list(1)
    reserved = reserved_r[0]["t"] if reserved_r else 0
    
    # Get vendors — filtered by branch when provided, enriched with supplier details
    vendor_query = {"product_id": product_id}
    if branch_id:
        vendor_query["$or"] = [
            {"branch_id": branch_id},
            {"branch_id": ""},
            {"branch_id": {"$exists": False}},
        ]
    raw_vendors = await db.product_vendors.find(vendor_query, {"_id": 0}).to_list(50)
    # Enrich with supplier details if supplier_id is present
    vendors = []
    for v in raw_vendors:
        if v.get("supplier_id"):
            supplier = await db.suppliers.find_one({"id": v["supplier_id"]}, {"_id": 0})
            if supplier:
                v["vendor_name"] = supplier.get("name", v.get("vendor_name", ""))
                v["vendor_contact"] = supplier.get("phone", v.get("vendor_contact", ""))
                v["supplier_email"] = supplier.get("email", "")
                v["supplier_address"] = supplier.get("address", "")
        vendors.append(v)
    
    # Get parent product if this is a repack
    parent = None
    if product.get("parent_id"):
        parent = await db.products.find_one({"id": product["parent_id"]}, {"_id": 0})
    
    # Get cost info — moving average and last acquisition from movements (POs + transfers)
    # BRANCH-SPECIFIC: filter by branch_id when provided
    acq_query = {"product_id": product_id, "type": {"$in": ["purchase", "transfer_in"]}, "quantity_change": {"$gt": 0}}
    if branch_id:
        acq_query["branch_id"] = branch_id
    all_acqs = await db.movements.find(acq_query, {"_id": 0}).to_list(10000)
    total_acq_qty = sum(m["quantity_change"] for m in all_acqs)
    total_acq_cost = sum(m["quantity_change"] * m.get("price_at_time", 0) for m in all_acqs)
    moving_average = round(total_acq_cost / total_acq_qty, 2) if total_acq_qty > 0 else float(product.get("cost_price", 0))

    last_acq = await db.movements.find_one(acq_query, {"_id": 0}, sort=[("created_at", -1)])
    last_purchase = float(last_acq.get("price_at_time", 0)) if last_acq else 0.0

    capital_method = product.get("capital_method", "last_purchase")
    # Branch-specific cost override (set when transfer is received)
    branch_prices_doc = None
    branch_cost_price = None
    branch_retail_prices = None
    if branch_id:
        branch_prices_doc = await db.branch_prices.find_one(
            {"product_id": product_id, "branch_id": branch_id}, {"_id": 0}
        )
        if branch_prices_doc:
            branch_cost_price = branch_prices_doc.get("cost_price")
            branch_retail_prices = branch_prices_doc.get("prices", {})

    cost = {
        "cost_price": float(product.get("cost_price", 0)),          # global
        "branch_cost_price": branch_cost_price,                      # branch-specific (None if not set)
        "branch_retail_prices": branch_retail_prices,                # branch-specific retail prices
        "is_branch_specific": branch_cost_price is not None,
        "cost_source": branch_prices_doc.get("source", "manual") if branch_prices_doc else "manual",
        "cost_transfer_order": branch_prices_doc.get("transfer_order", "") if branch_prices_doc else "",
        "capital_method": capital_method,
        "method": capital_method,
        "moving_average": moving_average,
        "last_purchase": last_purchase,
        "last_purchase_warning": last_purchase > 0 and moving_average > 0 and last_purchase < moving_average,
    }
    
    return {
        "product": product,
        "repacks": repacks,
        "inventory": {
            "on_hand": on_hand,
            "total": total_qty,
            "coming": coming,
            "reserved": reserved
        },
        "cost": cost,
        "vendors": vendors,
        "parent": parent
    }


@router.put("/{product_id}")
async def update_product(product_id: str, data: dict, user=Depends(get_current_user)):
    """Update product details."""
    check_perm(user, "products", "edit")

    allowed = ["name", "category", "description", "unit", "cost_price", "prices", "barcode",
               "units_per_parent", "repack_unit", "product_type", "capital_method",
               "reorder_point", "reorder_quantity", "unit_of_measurement", "last_vendor"]
    update = {k: v for k, v in data.items() if k in allowed}

    # Prevent duplicate product names on update (case-insensitive, exclude self)
    if "name" in update:
        new_name = update["name"].strip()
        if not new_name:
            raise HTTPException(status_code=400, detail="Product name cannot be empty")
        name_conflict = await db.products.find_one(
            {"name": {"$regex": f"^{new_name}$", "$options": "i"}, "active": True, "id": {"$ne": product_id}},
            {"_id": 0, "name": 1}
        )
        if name_conflict:
            raise HTTPException(status_code=400, detail=f"A product named \"{name_conflict['name']}\" already exists. Product names must be unique.")
        update["name"] = new_name

    if "cost_price" in update:
        # Separate permission required to change capital/cost
        if not has_perm(user, "products", "edit_cost"):
            raise HTTPException(
                status_code=403,
                detail="No permission to edit capital/cost price. You can still edit prices."
            )
        update["cost_price"] = float(update["cost_price"])

    update["updated_at"] = now_iso()

    # Log capital change if cost_price is being updated
    if "cost_price" in update:
        product_before = await db.products.find_one({"id": product_id}, {"_id": 0})
        old_capital = float(product_before.get("cost_price", 0)) if product_before else 0
        await db.capital_changes.insert_one({
            "id": new_id(),
            "product_id": product_id,
            "old_capital": old_capital,
            "new_capital": update["cost_price"],
            "method": "manual",
            "source_type": "manual_edit",
            "source_ref": "",
            "changed_by_id": user["id"],
            "changed_by_name": user.get("full_name", user.get("username", "")),
            "changed_at": now_iso(),
        })

    await db.products.update_one({"id": product_id}, {"$set": update})
    product = await db.products.find_one({"id": product_id}, {"_id": 0})
    return product


@router.get("/{product_id}/capital-history")
async def get_capital_history(product_id: str, branch_id: Optional[str] = None, limit: int = 50, user=Depends(get_current_user)):
    """Return the capital change log for a product, newest first. Filtered by branch when provided."""
    if branch_id:
        # Show records tagged with this branch_id, plus old records without branch_id (backward compat)
        query = {"product_id": product_id, "$or": [
            {"branch_id": branch_id},
            {"branch_id": {"$exists": False}},
        ]}
    else:
        query = {"product_id": product_id}
    history = await db.capital_changes.find(
        query, {"_id": 0}
    ).sort("changed_at", -1).limit(limit).to_list(limit)
    return history


@router.delete("/{product_id}")
async def delete_product(product_id: str, user=Depends(get_current_user), pin: str = ""):
    """Soft delete a product and its repacks."""
    check_perm(user, "products", "delete")

    # PIN enforcement for product deletion
    if pin:
        from routes.verify import verify_pin_for_action
        verifier = await verify_pin_for_action(pin, "product_delete")
        if not verifier:
            raise HTTPException(status_code=403, detail="Invalid PIN")

    await db.products.update_one({"id": product_id}, {"$set": {"active": False}})
    repacks = await db.products.find({"parent_id": product_id, "active": True}, {"_id": 0}).to_list(1000)
    for r in repacks:
        await db.products.update_one({"id": r["id"]}, {"$set": {"active": False}})
    
    return {"message": "Product and repacks deleted"}


@router.post("/{product_id}/generate-repack")
async def generate_repack(product_id: str, data: dict, user=Depends(get_current_user)):
    """Generate a repack product from a parent product."""
    check_perm(user, "products", "create")
    
    parent = await db.products.find_one({"id": product_id, "active": True}, {"_id": 0})
    if not parent:
        raise HTTPException(status_code=404, detail="Parent product not found")
    if parent.get("is_repack"):
        raise HTTPException(status_code=400, detail="Cannot create repack from a repack")
    
    repack_sku = f"R-{parent['sku']}"
    existing = await db.products.find_one({"sku": repack_sku, "active": True}, {"_id": 0})
    if existing:
        count = await db.products.count_documents({"parent_id": product_id, "active": True})
        repack_sku = f"R-{parent['sku']}-{count + 1}"
    
    repack_name = data.get("name", f"R {parent['name']}")
    units = int(data.get("units_per_parent", 1))
    add_on_cost = float(data.get("add_on_cost", 0))
    auto_cost = (parent.get("cost_price", 0) / units) + add_on_cost if units > 0 else 0
    
    repack = {
        "id": new_id(),
        "sku": repack_sku,
        "name": repack_name,
        "category": parent["category"],
        "description": f"Repack from {parent['name']} ({parent['unit']})",
        "unit": data.get("unit", "Piece"),
        "cost_price": float(data.get("cost_price", 0)) if float(data.get("cost_price", 0)) > 0 else round(auto_cost, 2),
        "prices": data.get("prices", {}),
        "parent_id": product_id,
        "is_repack": True,
        "units_per_parent": units,
        "add_on_cost": add_on_cost,
        "repack_unit": data.get("unit", "Piece"),
        "barcode": data.get("barcode", ""),
        "product_type": "stockable",
        "capital_method": "manual",
        "reorder_point": 0,
        "reorder_quantity": 0,
        "unit_of_measurement": data.get("unit", "Piece"),
        "last_vendor": "",
        "active": True,
        "created_at": now_iso(),
    }
    await db.products.insert_one(repack)
    del repack["_id"]
    return repack


@router.get("/{product_id}/repacks")
async def get_repacks(product_id: str, user=Depends(get_current_user)):
    """Get all repacks for a product."""
    repacks = await db.products.find({"parent_id": product_id, "active": True}, {"_id": 0}).to_list(100)
    return repacks


@router.get("/{product_id}/movements")
async def get_product_movements(product_id: str, branch_id: Optional[str] = None, limit: int = 50, user=Depends(get_current_user)):
    """Get stock movements for a product, optionally filtered by branch."""
    query = {"product_id": product_id}
    if branch_id:
        query["branch_id"] = branch_id
    total = await db.movements.count_documents(query)
    movements = await db.movements.find(
        query, 
        {"_id": 0}
    ).sort("created_at", -1).limit(limit).to_list(limit)
    return {"movements": movements, "total": total}


@router.get("/{product_id}/orders")
async def get_product_orders(product_id: str, branch_id: Optional[str] = None, limit: int = 50, user=Depends(get_current_user)):
    """Get order history for a product (POs + Sales), optionally filtered by branch."""
    results = []

    # --- Purchase Orders ---
    po_match = {"items.product_id": product_id}
    if branch_id:
        po_match["branch_id"] = branch_id
    pos = await db.purchase_orders.find(po_match, {"_id": 0}).sort("created_at", -1).limit(limit).to_list(limit)
    for po in pos:
        for item in po.get("items", []):
            if item.get("product_id") == product_id:
                results.append({
                    "date": po.get("created_at", ""),
                    "type": "purchase",
                    "reference": po.get("po_number", ""),
                    "party": po.get("vendor", po.get("supplier_name", "")),
                    "quantity": item.get("quantity", 0),
                    "price": item.get("unit_price") or item.get("cost") or item.get("unit_cost") or 0,
                    "total": round((item.get("quantity", 0)) * float(item.get("unit_price") or item.get("cost") or item.get("unit_cost") or 0), 2),
                    "status": po.get("status", ""),
                    "branch_id": po.get("branch_id", ""),
                })
                break

    # --- Sales ---
    sale_match = {"items.product_id": product_id}
    if branch_id:
        sale_match["branch_id"] = branch_id
    sales = await db.sales.find(sale_match, {"_id": 0}).sort("created_at", -1).limit(limit).to_list(limit)
    for sale in sales:
        for item in sale.get("items", []):
            if item.get("product_id") == product_id:
                qty = float(item.get("quantity", 0))
                price = float(item.get("rate") or item.get("price") or 0)
                results.append({
                    "date": sale.get("created_at", sale.get("date", "")),
                    "type": "sale",
                    "reference": sale.get("invoice_number", ""),
                    "party": sale.get("customer_name", "Walk-in"),
                    "quantity": qty,
                    "price": price,
                    "total": round(qty * price, 2),
                    "status": sale.get("status", "completed"),
                    "branch_id": sale.get("branch_id", ""),
                })
                break

    # Sort combined results by date descending
    results.sort(key=lambda x: x.get("date", ""), reverse=True)
    return {"orders": results[:limit]}


@router.post("/{product_id}/vendors")
async def add_product_vendor(product_id: str, data: dict, user=Depends(get_current_user)):
    """Add a vendor for a product, optionally scoped to a branch. Can link to a supplier via supplier_id."""
    check_perm(user, "products", "edit")

    supplier_id = data.get("supplier_id", "")
    vendor_name = data.get("vendor_name", "")
    vendor_contact = data.get("vendor_contact", "")

    # If supplier_id is provided, look up name/contact from the supplier record
    if supplier_id:
        supplier = await db.suppliers.find_one({"id": supplier_id}, {"_id": 0})
        if supplier:
            vendor_name = supplier.get("name", vendor_name)
            vendor_contact = supplier.get("phone", vendor_contact)

    vendor = {
        "id": new_id(),
        "product_id": product_id,
        "branch_id": data.get("branch_id", ""),
        "supplier_id": supplier_id,
        "vendor_name": vendor_name,
        "vendor_contact": vendor_contact,
        "last_price": float(data.get("last_price", 0)),
        "is_preferred": data.get("is_preferred", False),
        "created_at": now_iso()
    }
    await db.product_vendors.insert_one(vendor)
    del vendor["_id"]
    return vendor


@router.put("/{product_id}/update-price")
async def update_product_price(product_id: str, data: dict, user=Depends(get_current_user)):
    """Update a specific price scheme for a product."""
    check_perm(user, "products", "edit")
    
    scheme = data.get("scheme")
    new_price = float(data.get("price", 0))
    
    product = await db.products.find_one({"id": product_id}, {"_id": 0})
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    
    # Hard rule: cannot set price below cost
    if new_price < product.get("cost_price", 0) and new_price > 0:
        raise HTTPException(
            status_code=400,
            detail=f"Price ₱{new_price:.2f} is below capital ₱{product['cost_price']:.2f}"
        )
    
    prices = product.get("prices", {})
    prices[scheme] = new_price
    await db.products.update_one({"id": product_id}, {"$set": {"prices": prices, "updated_at": now_iso()}})
    
    return {"message": f"{scheme} price updated to ₱{new_price:.2f}", "prices": prices}

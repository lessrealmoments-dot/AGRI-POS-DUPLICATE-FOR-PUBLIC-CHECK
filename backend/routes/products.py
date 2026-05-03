"""
Product management routes: CRUD, repacks, pricing, search, barcodes.
"""
from fastapi import APIRouter, Depends, HTTPException
from typing import Optional, List
from config import db
from utils import get_current_user, check_perm, has_perm, now_iso, new_id, get_product_price, get_repack_capital, mark_price_reviewed
import random

router = APIRouter(prefix="/products", tags=["Products"])


@router.get("")
async def list_products(
    user=Depends(get_current_user),
    search: Optional[str] = None,
    category: Optional[str] = None,
    is_repack: Optional[bool] = None,
    parent_id: Optional[str] = None,
    branch_id: Optional[str] = None,    # when provided, repack capital is branch-aware
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
    # When a search term is provided we score-rank results so the most
    # relevant matches surface first instead of "alphabetical contains".
    # Order: name == query > name starts-with > word starts-with >
    #        sku/barcode contains > name contains. (See _score_match below.)
    search_active = bool(search and search.strip())
    if search_active:
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
        products = await _enrich_repacks_with_live_capital(products, branch_id)
        products = await _enrich_with_branch_overrides(products, branch_id)
        products = await _enrich_with_disabled_at_branch(products, branch_id)
        products = await _enrich_with_stock(products, branch_id)
        return {"products": products, "total": total, "skip": skip, "limit": limit}

    elif sort_by == "type":
        mongo_sort = [("is_repack", 1), ("name", 1)]   # parents (False=0) before repacks (True=1)
    else:
        mongo_sort = [("name", 1)]  # default: alphabetical

    total = await db.products.count_documents(query)
    if search_active:
        # Pull a wider candidate pool, rerank by relevance, then trim to limit.
        # Cap at 200 to keep the in-memory rerank cheap.
        pool_size = max(limit * 4, 50)
        pool_size = min(pool_size, 200)
        products = await db.products.find(query, {"_id": 0}).sort(mongo_sort).limit(pool_size).to_list(pool_size)
        products = _rank_by_search_relevance(products, search.strip())
        # Apply skip/limit AFTER reranking
        products = products[skip: skip + limit]
    else:
        products = await db.products.find(query, {"_id": 0}).sort(mongo_sort).skip(skip).limit(limit).to_list(limit)
    products = await _enrich_repacks_with_live_capital(products, branch_id)
    products = await _enrich_with_branch_overrides(products, branch_id)
    products = await _enrich_with_disabled_at_branch(products, branch_id)
    products = await _enrich_with_stock(products, branch_id)
    return {"products": products, "total": total, "skip": skip, "limit": limit}


async def _enrich_with_stock(products: list, branch_id: Optional[str]) -> list:
    """Inject `stock_on_hand` on each product so the list view can show
    current inventory at-a-glance without navigating into each product.

    - If branch_id is provided and != "all": stock at THAT branch only.
    - Else: sum across ALL branches.
    - Repacks: derived from parent stock × units_per_parent (matching the
      search-detail endpoint's logic so the list view and POS agree).

    One aggregation per call (not per-product) — cheap even at limit=50.
    """
    if not products:
        return products

    parent_ids = [p["id"] for p in products if not p.get("is_repack") and p.get("id")]
    repack_parent_ids = [p["parent_id"] for p in products if p.get("is_repack") and p.get("parent_id")]
    all_pids = list({*parent_ids, *repack_parent_ids})
    if not all_pids:
        for p in products:
            p["stock_on_hand"] = 0
        return products

    specific_branch = bool(branch_id and branch_id != "all")
    match = {"product_id": {"$in": all_pids}}
    if specific_branch:
        match["branch_id"] = branch_id

    pipeline = [
        {"$match": match},
        {"$group": {"_id": "$product_id", "total": {"$sum": "$quantity"}}},
    ]
    rows = await db.inventory.aggregate(pipeline).to_list(len(all_pids))
    stock_by_pid = {r["_id"]: float(r.get("total") or 0) for r in rows}

    for p in products:
        if p.get("is_repack") and p.get("parent_id"):
            parent_stock = stock_by_pid.get(p["parent_id"], 0)
            upp = p.get("units_per_parent") or 1
            p["stock_on_hand"] = parent_stock * upp
        else:
            p["stock_on_hand"] = stock_by_pid.get(p.get("id"), 0)
    return products


async def _enrich_with_branch_overrides(products: list, branch_id: Optional[str]) -> list:
    """Merge per-branch `branch_prices` overrides into each product's `prices`
    map and `cost_price`, and tag each row with `price_source`.

    Without this, the Products List always reflected the master catalog —
    branch overrides imported via "Branch Stock + Price" or set per-branch
    in the dialog were invisible from the list, which led admins to
    re-edit the master thinking they were editing a branch (and thus
    accidentally clobber every other branch's price).

    Skips repacks: those are already handled in `_enrich_repacks_with_live_capital`.
    """
    if not branch_id or branch_id == "all" or not products:
        # No branch context → return as-is, but still tag price_source so the
        # frontend can render the same UI both ways without conditionals.
        for p in products or []:
            p.setdefault("price_source", "global")
        return products

    pids = [p["id"] for p in products if p.get("id") and not p.get("is_repack")]
    if not pids:
        return products
    bp_docs = await db.branch_prices.find(
        {"branch_id": branch_id, "product_id": {"$in": pids}}, {"_id": 0}
    ).to_list(len(pids))
    bp_map = {d["product_id"]: d for d in bp_docs}

    for p in products:
        if p.get("is_repack"):
            # Repack rows already carry merged prices via the repack helper.
            # Just tag them so the frontend can show the same chip.
            bp = bp_map.get(p.get("id"))
            p["price_source"] = "branch_override" if bp else "global"
            continue
        bp = bp_map.get(p.get("id"))
        if not bp:
            p["price_source"] = "global"
            continue
        # Override wins per-key; missing keys keep the master value.
        merged = {**(p.get("prices") or {}), **(bp.get("prices") or {})}
        p["prices"] = merged
        if bp.get("cost_price") is not None:
            p["cost_price"] = bp["cost_price"]
        p["price_source"] = "branch_override"
    return products


async def _enrich_with_disabled_at_branch(products: list, branch_id: Optional[str]) -> list:
    """Inject `disabled_at_branch` flag on each product based on the inventory
    row at the requested branch. Also lazily auto-reactivates rows where stock
    has come back in — single bulk update, no per-row hooks needed.
    Skipped when branch_id is not provided.
    """
    if not branch_id or not products:
        return products
    # Lazy reactivation — clear the flag wherever stock has returned
    try:
        await db.inventory.update_many(
            {"branch_id": branch_id, "disabled_at_branch": True, "quantity": {"$gt": 0}},
            {"$set": {"disabled_at_branch": False, "auto_reactivated_at": now_iso()}},
        )
    except Exception:
        pass
    pids = [p["id"] for p in products if p.get("id")]
    if not pids:
        return products
    inv_rows = await db.inventory.find(
        {"branch_id": branch_id, "product_id": {"$in": pids}},
        {"_id": 0, "product_id": 1, "disabled_at_branch": 1, "quantity": 1},
    ).to_list(len(pids))
    inv_map = {r["product_id"]: r for r in inv_rows}
    for p in products:
        row = inv_map.get(p.get("id"), {})
        p["disabled_at_branch"] = bool(row.get("disabled_at_branch", False))
    return products


def _rank_by_search_relevance(products: list, query: str) -> list:
    """Score-rank products by how strongly they match the query.

    Higher score = more relevant. Tie-breaker: alphabetical name.

    Heuristic (purely positional, no fuzzy matching):
      120 — name OR sku OR barcode is exactly the query
      100 — name starts with the query (prefix match)
       90 — sku starts with the query
       80 — any word in name starts with the query (e.g. "promix" in
            "STARTER PLUS - PROMIX 50KG" → token boundary)
       50 — sku contains the query
       40 — barcode contains the query
       20 — name contains the query (worst — pure substring)
       10 — token start match late in the string (deprioritises long names)
    """
    q = (query or "").lower().strip()
    if not q:
        return products

    def score(p):
        name = (p.get("name") or "").lower()
        sku = (p.get("sku") or "").lower()
        barcode = (p.get("barcode") or "").lower()
        s = 0
        if name == q or sku == q or barcode == q:
            s = max(s, 120)
        if name.startswith(q):
            s = max(s, 100)
        if sku and sku.startswith(q):
            s = max(s, 90)
        # Token-boundary match in the name
        if q in name:
            tokens = name.replace("-", " ").replace("/", " ").split()
            if any(t.startswith(q) for t in tokens):
                s = max(s, 80)
            else:
                s = max(s, 20)
        if sku and q in sku:
            s = max(s, 50)
        if barcode and q in barcode:
            s = max(s, 40)
        return s

    # Sort by (-score, name) so most relevant first, alphabetical within ties
    return sorted(products, key=lambda p: (-score(p), (p.get("name") or "").lower()))


async def _enrich_repacks_with_live_capital(products: list, branch_id: Optional[str]) -> list:
    """Inject live parent-derived capital and branch retail into repack rows.

    For repacks, `product.cost_price` was set to 0 at creation (we no longer
    store it). We compute it on-the-fly from the parent's branch cost so the
    Products list/detail UIs show a real number instead of ₱0.
    """
    repack_rows = [p for p in products if p.get("is_repack") and p.get("parent_id")]
    if not repack_rows:
        return products

    # Bulk-fetch branch_prices for these repacks (retail) at the given branch
    repack_ids = [p["id"] for p in repack_rows]
    bp_map = {}
    if branch_id:
        bp_docs = await db.branch_prices.find(
            {"product_id": {"$in": repack_ids}, "branch_id": branch_id}, {"_id": 0}
        ).to_list(len(repack_ids))
        bp_map = {d["product_id"]: d for d in bp_docs}

    enriched = []
    for p in products:
        if p.get("is_repack") and p.get("parent_id"):
            cap = await get_repack_capital(p, branch_id or "")
            new_p = {**p, "cost_price": round(cap, 4)}
            bp = bp_map.get(p["id"])
            if bp and bp.get("prices"):
                # Surface branch retail in the prices map so list views read it
                new_p["prices"] = {**(p.get("prices") or {}), **bp["prices"]}
            enriched.append(new_p)
        else:
            enriched.append(p)
    return enriched


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
    also_branch_id: optional second branch to include stock levels for (used by Request Stock form).

    Token rules (mirror of the frontend Quick-mode filter so server-side
    SmartProductSearch results stay consistent with the grid):
      • 1–3 digit pure numbers ("1", "14", "200") must PREFIX-match a whole
        word in the product NAME — uses a Mongo regex anchored on
        non-word/start so they cannot match random SKU suffixes. So "2"
        matches "Galimax 2" / "Galimax 21" but not "Galimax 1" or
        "P-FINEX-2281".
      • Everything else (alphanumeric, longer numbers, alpha words) does the
        usual case-insensitive substring match across name + SKU + barcode.
    Order-independent: each token is its own AND condition.

    Fuzzy fallback (when strict pass returns 0):
      • RapidFuzz token_set_ratio + partial_ratio against name field.
      • 80% similarity threshold (configurable).
      • Numeric/short tokens still required to prefix-match exactly so
        "1 kg" never silently maps to "2 kg".
      • Response includes `_fuzzy_hint = {query, count}` on first item
        when fallback fired, so the dropdown can render "Did you mean".
    """
    if not q or len(q) < 1:
        return []

    import re
    tokens = [t for t in re.split(r"[\s\-/,]+", q.strip()) if t]
    if not tokens:
        return []

    def _esc(t: str) -> str:
        return re.escape(t)

    def _token_clause(tok: str) -> dict:
        """Return the Mongo $or clause for a single token, applying the
        short-numeric prefix-of-word rule when applicable."""
        if re.fullmatch(r"[0-9]{1,3}", tok):
            # Match `tok` only when it appears at the start of a name word.
            # Names are space/dash/slash/comma separated, so we anchor on
            # either start-of-string or one of those separators.
            pattern = r"(?:^|[\s\-/,])" + _esc(tok)
            return {"name": {"$regex": pattern, "$options": "i"}}
        return {"$or": [
            {"name": {"$regex": _esc(tok), "$options": "i"}},
            {"sku": {"$regex": _esc(tok), "$options": "i"}},
            {"barcode": {"$regex": _esc(tok), "$options": "i"}},
        ]}

    query: dict = {"active": True}
    if len(tokens) == 1:
        clause = _token_clause(tokens[0])
        # Flatten the single-clause case so we don't end up with a
        # double-nested $or / $and that confuses the query planner.
        if "$or" in clause:
            query["$or"] = clause["$or"]
        else:
            query.update(clause)
    else:
        query["$and"] = [_token_clause(t) for t in tokens]

    products = await db.products.find(query, {"_id": 0}).limit(20).to_list(20)

    # ── Fuzzy fallback (typo tolerance) ─────────────────────────────
    # Trigger only when strict search returned 0 hits AND the query has
    # at least one fuzzable token (alpha, ≥4 chars). Numeric / short
    # tokens MUST still prefix-match a name word literally so unit
    # numbers ("1 kg" vs "2 kg") never get silently swapped.
    fuzzy_hint = None
    if not products:
        fuzzable_tokens = [
            t for t in tokens
            if len(t) >= 4 and not re.fullmatch(r"[0-9]+", t)
        ]
        exact_required = [t for t in tokens if t not in fuzzable_tokens]
        if fuzzable_tokens:
            from rapidfuzz import fuzz
            # Pull a candidate pool — capped at 1000 active rows for speed.
            # For each candidate, compute token_set_ratio against the full
            # query; keep ≥80% similarity, then enforce that every
            # exact-required token still matches literally.
            candidate_query = {"active": True}
            if exact_required:
                # Apply literal token clauses to narrow the pool first
                exact_clauses = [_token_clause(t) for t in exact_required]
                if len(exact_clauses) == 1:
                    c = exact_clauses[0]
                    if "$or" in c:
                        candidate_query["$or"] = c["$or"]
                    else:
                        candidate_query.update(c)
                else:
                    candidate_query["$and"] = exact_clauses
            candidates = await db.products.find(
                candidate_query, {"_id": 0}
            ).limit(1000).to_list(1000)

            full_q = q.strip().lower()
            scored = []
            for p in candidates:
                name = (p.get("name") or "").lower()
                if not name:
                    continue
                # Use the higher of token_set_ratio (order-independent) and
                # partial_ratio (handles "promex" vs "promix 50kg"). Both
                # are 0-100; we want ≥80.
                score = max(
                    fuzz.token_set_ratio(full_q, name),
                    fuzz.partial_ratio(full_q, name),
                )
                if score >= 80:
                    scored.append((score, p))
            if scored:
                scored.sort(key=lambda s: (-s[0], len((s[1].get("name") or ""))))
                products = [p for _, p in scored[:10]]
                fuzzy_hint = {"query": q.strip(), "count": len(scored)}

    # Rank: prefer rows where the full phrase appears as a name prefix (most
    # specific), then a contiguous substring, then loose token-only hits.
    # Tiebreak by name length so shorter, more specific names win.
    full_phrase = q.strip().lower()
    def _rank(p):
        name = (p.get("name") or "").lower()
        if name.startswith(full_phrase):
            r = 0
        elif full_phrase in name:
            r = 1
        else:
            r = 2
        return (r, len(name))
    products.sort(key=_rank)
    products = products[:10]
    results = []
    
    for p in products:
        # Apply branch price overrides — track which schemes are branch-set
        # vs falling back to the global product.prices map. Frontend uses this
        # to decide between green / amber-Global-Price / red No-Retail badges.
        branch_set_scheme_keys = []
        if branch_id:
            override = await db.branch_prices.find_one(
                {"product_id": p["id"], "branch_id": branch_id}, {"_id": 0}
            )
            if override and override.get("prices"):
                branch_set_scheme_keys = list(override["prices"].keys())
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

        # For repacks: derive capital live from parent's branch capital
        # so it always reflects current PO/transfer/manual updates.
        if p.get("is_repack") and p.get("parent_id"):
            repack_capital = await get_repack_capital(p, branch_id or "")
        else:
            repack_capital = None

        # Effective capital = the cost the system uses for below-capital validation
        capital_method = p.get("capital_method", "manual")
        if capital_method == "moving_average":
            effective_capital = moving_average_cost
        elif capital_method == "last_purchase":
            effective_capital = last_purchase_cost or (repack_capital if repack_capital is not None else float(p.get("cost_price", 0)))
        else:
            effective_capital = repack_capital if repack_capital is not None else float(p.get("cost_price", 0))

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
                "branch_set_scheme_keys": branch_set_scheme_keys,
            }
            # Override cost_price with live parent-derived capital for repacks.
            # This is the value frontend uses for capital display & validation.
            if repack_capital is not None:
                result["cost_price"] = repack_capital
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
                "branch_set_scheme_keys": branch_set_scheme_keys,
            }
        # ── Also-branch stock (for Request Stock dual-view) ──────────────
        if also_branch_id and also_branch_id != branch_id:
            also_inv = await db.inventory.find_one(
                {"product_id": p["id"], "branch_id": also_branch_id}, {"_id": 0}
            )
            result["also_branch_stock"] = float(also_inv["quantity"]) if also_inv else 0

        results.append(result)
    
    # Surface the fuzzy hint on every result so callers can detect it
    # without changing the response shape (still a list of products).
    if fuzzy_hint:
        for r in results:
            r["_fuzzy_hint"] = fuzzy_hint
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



# ── Capital Change Alerts (Stage 2 of Smart Price) ─────────────────────────
# Surfaces recent capital-cost changes from POs and branch transfers, with
# noise filtered out:
#   • Skips changes where |delta| < ₱1 (decimal noise / vendor adjustments)
#   • Skips changes flagged was_user_choice=True (admin already decided)
#   • Skips changes already acknowledged
# All pre-existing rows treated as already-acknowledged via startup migration.

CAPITAL_DELTA_FLOOR = 1.0  # absolute peso threshold


@router.get("/capital-change-alerts")
async def capital_change_alerts(
    branch_id: Optional[str] = None,
    days: int = 14,
    user=Depends(get_current_user),
):
    """
    Return unacknowledged capital changes worth surfacing as alerts.
    Default window: 14 days back.
    """
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max(1, int(days)))).isoformat()

    q = {
        "changed_at": {"$gte": cutoff},
        "$or": [
            {"acknowledged_at": {"$exists": False}},
            {"acknowledged_at": None},
        ],
        "was_user_choice": {"$ne": True},
    }
    if branch_id:
        q["branch_id"] = branch_id

    rows = await db.capital_changes.find(q, {"_id": 0}).sort("changed_at", -1).to_list(500)

    # Enrich + filter on delta floor
    alerts = []
    for r in rows:
        old_cap = float(r.get("old_capital") or 0)
        new_cap = float(r.get("new_capital") or 0)
        delta_amount = round(new_cap - old_cap, 4)
        if abs(delta_amount) < CAPITAL_DELTA_FLOOR:
            continue
        delta_pct = (delta_amount / old_cap * 100.0) if old_cap > 0 else None

        # Pull product + branch names cheaply
        product = await db.products.find_one(
            {"id": r.get("product_id")},
            {"_id": 0, "id": 1, "name": 1, "sku": 1, "category": 1, "unit": 1},
        )
        branch = await db.branches.find_one(
            {"id": r.get("branch_id")},
            {"_id": 0, "id": 1, "name": 1},
        )
        alerts.append({
            "id": r["id"],
            "product_id": r.get("product_id"),
            "product_name": (product or {}).get("name", "Unknown product"),
            "sku": (product or {}).get("sku", ""),
            "category": (product or {}).get("category", ""),
            "unit": (product or {}).get("unit", ""),
            "branch_id": r.get("branch_id"),
            "branch_name": (branch or {}).get("name", ""),
            "old_capital": old_cap,
            "new_capital": new_cap,
            "delta_amount": delta_amount,
            "delta_pct": delta_pct,
            "direction": "up" if delta_amount > 0 else "down",
            "method": r.get("method", ""),
            "source_type": r.get("source_type", ""),
            "source_ref": r.get("source_ref", ""),
            "vendor": r.get("vendor", ""),
            "from_branch": r.get("from_branch", ""),
            "to_branch": r.get("to_branch", ""),
            "changed_by_name": r.get("changed_by_name", ""),
            "changed_at": r.get("changed_at", ""),
        })

    return {
        "alerts": alerts,
        "count": len(alerts),
        "branch_id": branch_id,
        "days": days,
        "threshold_amount": CAPITAL_DELTA_FLOOR,
    }


@router.post("/capital-change-alerts/{change_id}/acknowledge")
async def acknowledge_capital_change(change_id: str, user=Depends(get_current_user)):
    """Dismiss a single capital change alert."""
    res = await db.capital_changes.update_one(
        {"id": change_id},
        {"$set": {
            "acknowledged_at": now_iso(),
            "acknowledged_by_id": user["id"],
            "acknowledged_by_name": user.get("full_name", user.get("username", "")),
        }},
    )
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Alert not found")
    return {"ok": True, "id": change_id}


@router.post("/capital-change-alerts/acknowledge-all")
async def acknowledge_all_capital_changes(data: dict, user=Depends(get_current_user)):
    """
    Bulk-dismiss alerts. Admin only.
    Body: { branch_id?: str }  — if omitted, applies tenant-wide.
    """
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    q = {
        "$or": [
            {"acknowledged_at": {"$exists": False}},
            {"acknowledged_at": None},
        ],
    }
    bid = (data.get("branch_id") or "").strip()
    if bid:
        q["branch_id"] = bid
    res = await db.capital_changes.update_many(
        q,
        {"$set": {
            "acknowledged_at": now_iso(),
            "acknowledged_by_id": user["id"],
            "acknowledged_by_name": user.get("full_name", user.get("username", "")),
        }},
    )
    return {"ok": True, "acknowledged_count": res.modified_count}


# ── Smart Price PIN-gated update ──────────────────────────────────────────
# Used by the Smart Price Checker dialog to apply admin-approved price fixes.
# Always requires a PIN that resolves to admin or TOTP — managers/cashiers
# cannot push price updates through this path even if they have edit perms.

@router.post("/smart-price-update")
async def smart_price_update(data: dict, user=Depends(get_current_user)):
    """
    PIN-gated price update from Smart Price Checker.
    Body: { product_id, prices: {scheme: amount}, pin }
    """
    pid = (data.get("product_id") or "").strip()
    prices = data.get("prices") or {}
    pin = str(data.get("pin") or "")
    if not pid or not prices:
        raise HTTPException(status_code=400, detail="product_id and prices are required")
    if not pin:
        raise HTTPException(status_code=400, detail="Admin PIN is required")

    # Hard PIN gate — admin or TOTP only (no manager/cashier)
    from routes.verify import verify_pin_for_action
    verifier = await verify_pin_for_action(pin, "smart_price_update")
    if not verifier:
        raise HTTPException(status_code=403, detail="Invalid PIN — admin or TOTP required")

    # Validate prices > 0
    cleaned = {}
    for k, v in prices.items():
        try:
            num = float(v)
        except Exception:
            continue
        if num > 0:
            cleaned[k] = num
    if not cleaned:
        raise HTTPException(status_code=400, detail="No valid prices provided")

    product = await db.products.find_one({"id": pid, "active": True}, {"_id": 0})
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    merged = dict(product.get("prices") or {})
    merged.update(cleaned)

    await db.products.update_one(
        {"id": pid},
        {"$set": {"prices": merged, "updated_at": now_iso()}},
    )

    # Same hook as the regular product edit: clear Global Price badge across
    # branches that hold inventory (manual price decision).
    inv_rows = await db.inventory.find(
        {"product_id": pid}, {"_id": 0, "branch_id": 1}
    ).to_list(500)
    for inv in inv_rows:
        await mark_price_reviewed(pid, inv["branch_id"], source="smart_price")

    return {"ok": True, "product_id": pid, "prices": merged,
            "pin_method": verifier.get("method", "")}


# ── PIN-gated bulk cost-details lookup ────────────────────────────────────
# Returns effective branch cost, last-purchase cost, and moving-average cost
# for a list of products at a branch. Capital is sensitive — every call
# requires a PIN that satisfies the `view_capital_costs` policy
# (admin/manager/TOTP by default). Even admins must re-PIN when reopening
# the Sales screen, so a logged-in account left unattended doesn't leak
# margin info.

@router.post("/cost-details")
async def cost_details_bulk(data: dict, user=Depends(get_current_user)):
    """
    Body: { branch_id, product_ids: [str, ...], pin: str }
    Returns: {
      product_id: {
        effective_cost: float,   # branch override or global
        last_purchase: float,    # most recent purchase/transfer-in cost
        moving_average: float,   # weighted avg over last 30d
      }
    }
    """
    branch_id = (data.get("branch_id") or "").strip()
    product_ids = data.get("product_ids") or []
    pin = str(data.get("pin") or "")
    if not branch_id:
        raise HTTPException(status_code=400, detail="branch_id is required")
    if not isinstance(product_ids, list) or not product_ids:
        raise HTTPException(status_code=400, detail="product_ids[] is required")
    if not pin:
        raise HTTPException(status_code=400, detail="PIN is required to view capital")

    from routes.verify import verify_pin_for_action
    verifier = await verify_pin_for_action(pin, "view_capital_costs")
    if not verifier:
        raise HTTPException(status_code=403, detail="Invalid PIN — admin or manager required")

    # Cap input length defensively
    product_ids = [str(p) for p in product_ids if p][:1000]

    # 1) Effective cost per product (branch override → global fallback)
    #    Repacks: derive on-the-fly from parent's branch capital.
    products = await db.products.find(
        {"id": {"$in": product_ids}},
        {"_id": 0, "id": 1, "cost_price": 1, "is_repack": 1, "parent_id": 1, "units_per_parent": 1, "add_on_cost": 1}
    ).to_list(2000)
    global_cost = {p["id"]: float(p.get("cost_price") or 0) for p in products}

    overrides = await db.branch_prices.find(
        {"product_id": {"$in": product_ids}, "branch_id": branch_id, "cost_price": {"$exists": True}},
        {"_id": 0, "product_id": 1, "cost_price": 1},
    ).to_list(2000)
    branch_cost = {o["product_id"]: float(o.get("cost_price") or 0) for o in overrides}

    # Compute repack capital live (parent-aware) for any repack in the list
    repack_cost = {}
    for p in products:
        if p.get("is_repack") and p.get("parent_id"):
            repack_cost[p["id"]] = await get_repack_capital(p, branch_id)

    # 2) Last purchase + moving average via movements
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()

    pipeline = [
        {"$match": {
            "product_id": {"$in": product_ids},
            "branch_id": branch_id,
            "type": {"$in": ["purchase", "transfer_in"]},
            "created_at": {"$gte": cutoff},
        }},
        {"$sort": {"created_at": -1}},
        {"$group": {
            "_id": "$product_id",
            "last_purchase": {"$first": "$cost_price"},
            "total_qty": {"$sum": "$quantity"},
            "weighted_cost": {"$sum": {"$multiply": ["$quantity", "$cost_price"]}},
        }},
    ]
    rows = await db.movements.aggregate(pipeline).to_list(2000)
    movement_map = {}
    for r in rows:
        ma = (r["weighted_cost"] / r["total_qty"]) if r["total_qty"] else 0
        movement_map[r["_id"]] = {
            "last_purchase": float(r.get("last_purchase") or 0),
            "moving_average": round(float(ma), 4),
        }

    out = {}
    for pid in product_ids:
        # Repack: live parent-derived cost wins
        if pid in repack_cost:
            eff = repack_cost[pid]
        else:
            eff = branch_cost.get(pid, global_cost.get(pid, 0))
        mv = movement_map.get(pid, {})
        out[pid] = {
            "effective_cost": eff,
            "last_purchase": mv.get("last_purchase", 0),
            "moving_average": mv.get("moving_average", 0),
        }

    return {"branch_id": branch_id, "costs": out, "pin_method": verifier.get("method", "")}


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


# ── Repack Pricing Manager ──────────────────────────────────────────────────

@router.get("/repack-pricing/grid")
async def repack_pricing_grid(
    branch_ids: str = "",                  # comma-separated branch IDs (required, at least one)
    with_inventory_only: bool = True,      # only repacks whose parent has stock in any selected branch
    missing_only: bool = False,            # only rows that have at least one branch missing retail
    user=Depends(get_current_user),
):
    """Build the Repack Pricing Manager grid.

    Returns one row per repack × branch with:
      - capital (computed live via get_repack_capital)
      - current_retail (from branch_prices.prices.retail; null if not set)
      - has_parent_stock (parent inventory.quantity > 0 in that branch)
    """
    if user.get("role") not in ("admin", "manager"):
        raise HTTPException(status_code=403, detail="Admin or manager required")

    branch_id_list = [b.strip() for b in (branch_ids or "").split(",") if b.strip()]
    if not branch_id_list:
        raise HTTPException(status_code=400, detail="At least one branch_id required")

    # Load branches (for name in the response)
    branches = await db.branches.find(
        {"id": {"$in": branch_id_list}}, {"_id": 0, "id": 1, "name": 1}
    ).to_list(100)
    branch_map = {b["id"]: b for b in branches}

    # All active repacks
    repacks = await db.products.find(
        {"active": True, "is_repack": True},
        {"_id": 0}
    ).to_list(10000)

    if not repacks:
        return {"branches": branches, "rows": []}

    parent_ids = list({r["parent_id"] for r in repacks if r.get("parent_id")})
    repack_ids = [r["id"] for r in repacks]

    # Pre-fetch parent inventory in selected branches
    parent_inv = await db.inventory.find(
        {"product_id": {"$in": parent_ids}, "branch_id": {"$in": branch_id_list}},
        {"_id": 0, "product_id": 1, "branch_id": 1, "quantity": 1}
    ).to_list(50000)
    parent_stock_map = {(i["product_id"], i["branch_id"]): float(i.get("quantity", 0) or 0) for i in parent_inv}

    # Pre-fetch branch_prices for repacks in selected branches
    bp_docs = await db.branch_prices.find(
        {"product_id": {"$in": repack_ids}, "branch_id": {"$in": branch_id_list}},
        {"_id": 0}
    ).to_list(50000)
    bp_map = {(d["product_id"], d["branch_id"]): d for d in bp_docs}

    # Pre-fetch parent products (for name + global prices fallback display)
    parents = await db.products.find(
        {"id": {"$in": parent_ids}}, {"_id": 0, "id": 1, "name": 1, "prices": 1}
    ).to_list(10000)
    parent_map = {p["id"]: p for p in parents}

    rows = []
    for r in repacks:
        parent = parent_map.get(r.get("parent_id"))
        # Determine if this repack qualifies for inclusion
        if with_inventory_only:
            has_any_parent_stock = any(
                parent_stock_map.get((r["parent_id"], bid), 0) > 0 for bid in branch_id_list
            )
            if not has_any_parent_stock:
                continue

        branches_data = []
        any_missing = False
        for bid in branch_id_list:
            capital = await get_repack_capital(r, bid)
            bp = bp_map.get((r["id"], bid))
            current_retail = None
            if bp and bp.get("prices") and bp["prices"].get("retail") is not None:
                current_retail = float(bp["prices"]["retail"])

            global_retail = None
            if r.get("prices") and r["prices"].get("retail") is not None:
                global_retail = float(r["prices"]["retail"])

            parent_stock = parent_stock_map.get((r["parent_id"], bid), 0)
            has_parent_stock = parent_stock > 0

            if current_retail is None:
                any_missing = True

            branches_data.append({
                "branch_id": bid,
                "branch_name": branch_map.get(bid, {}).get("name", ""),
                "capital": round(capital, 2),
                "current_retail": current_retail,
                "global_retail": global_retail,
                "has_parent_stock": has_parent_stock,
                "parent_stock": parent_stock,
            })

        if missing_only and not any_missing:
            continue

        rows.append({
            "repack_id": r["id"],
            "repack_name": r["name"],
            "repack_sku": r.get("sku", ""),
            "repack_unit": r.get("unit", ""),
            "units_per_parent": r.get("units_per_parent", 1),
            "parent_id": r.get("parent_id"),
            "parent_name": parent.get("name", "") if parent else "",
            "branches": branches_data,
        })

    # Sort rows by repack name
    rows.sort(key=lambda r: r["repack_name"].lower())

    return {"branches": branches, "rows": rows}


@router.post("/repack-pricing/bulk-save")
async def repack_pricing_bulk_save(data: dict, user=Depends(get_current_user)):
    """Save retail prices for many repack × branch cells. PIN-gated.

    Body:
      {
        "pin": "...",
        "updates": [
          {"repack_id": "...", "branch_id": "...", "retail": 200.0}
        ]
      }
    """
    if user.get("role") not in ("admin", "manager"):
        raise HTTPException(status_code=403, detail="Admin or manager required")

    pin = (data.get("pin") or "").strip()
    if not pin:
        raise HTTPException(status_code=400, detail="PIN required to save retail prices")

    from routes.verify import verify_pin_for_action
    verifier = await verify_pin_for_action(pin, "repack_retail_save")
    if not verifier:
        raise HTTPException(status_code=403, detail="Invalid PIN — admin or manager required")

    updates = data.get("updates", []) or []
    if not updates:
        return {"saved": 0}

    saved = 0
    skipped = 0
    for u in updates:
        repack_id = (u.get("repack_id") or "").strip()
        branch_id = (u.get("branch_id") or "").strip()
        try:
            retail = float(u.get("retail"))
        except (TypeError, ValueError):
            skipped += 1
            continue
        if not repack_id or not branch_id or retail <= 0:
            skipped += 1
            continue

        # Confirm this is actually a repack
        prod = await db.products.find_one({"id": repack_id, "is_repack": True}, {"_id": 0, "id": 1})
        if not prod:
            skipped += 1
            continue

        # Read existing to merge other scheme prices
        existing = await db.branch_prices.find_one(
            {"product_id": repack_id, "branch_id": branch_id}, {"_id": 0}
        )
        existing_prices = (existing or {}).get("prices", {}) or {}
        existing_prices["retail"] = retail

        await db.branch_prices.update_one(
            {"product_id": repack_id, "branch_id": branch_id},
            {
                "$set": {
                    "prices": existing_prices,
                    "updated_at": now_iso(),
                    "updated_by": user.get("full_name", user.get("username", "")),
                    "source": "repack_pricing_manager",
                },
                "$setOnInsert": {
                    "id": new_id(),
                    "product_id": repack_id,
                    "branch_id": branch_id,
                    "created_at": now_iso(),
                },
            },
            upsert=True,
        )
        saved += 1

    return {"saved": saved, "skipped": skipped, "pin_method": verifier.get("method", "")}




@router.get("/{product_id}")
async def get_product(product_id: str, branch_id: Optional[str] = None, user=Depends(get_current_user)):
    """Get a single product by ID. For repacks, capital is computed live
    from the parent's branch cost when branch_id is provided."""
    product = await db.products.find_one({"id": product_id, "active": True}, {"_id": 0})
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    if product.get("is_repack") and product.get("parent_id"):
        cap = await get_repack_capital(product, branch_id or "")
        product = {**product, "cost_price": round(cap, 4)}
        # Merge branch retail prices for this repack at the branch (if any)
        if branch_id:
            bp = await db.branch_prices.find_one(
                {"product_id": product_id, "branch_id": branch_id}, {"_id": 0}
            )
            if bp and bp.get("prices"):
                product["prices"] = {**(product.get("prices") or {}), **bp["prices"]}
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

    # Repacks: capital is derived live from parent's branch capital. Surface
    # the computed value so the Product Detail page shows it (and the global
    # capital field on the parent isn't 0 just because we no longer store it).
    if product.get("is_repack") and product.get("parent_id"):
        repack_capital = await get_repack_capital(product, branch_id or "")
        cost["repack_capital"] = round(repack_capital, 4)
        cost["cost_price"] = round(repack_capital, 4)
        if branch_cost_price is None:
            # No branch override → use the live derived value
            cost["branch_cost_price"] = round(repack_capital, 4)
            cost["is_branch_specific"] = True
            cost["cost_source"] = "derived_from_parent"
        # Also override product.cost_price in the response so frontend list/detail
        # views reading product.cost_price see the live value, not the stored 0.
        product = {**product, "cost_price": round(repack_capital, 4)}
    
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
async def update_product(
    product_id: str,
    data: dict,
    branch_id: Optional[str] = None,
    user=Depends(get_current_user),
):
    """Update product details.

    When `branch_id` is supplied (and is a real branch — not "all"), price-
    related edits (`cost_price`, `prices`) are routed to the per-branch
    `branch_prices` override collection instead of the master product doc.
    Catalog-level fields (name, category, description, unit, etc.) ALWAYS
    write to the master regardless — those are tenant-wide attributes that
    don't make sense per-branch.

    Without `branch_id` (or with branch_id == "all"): all edits hit the
    master product, preserving the legacy global-edit flow.
    """
    check_perm(user, "products", "edit")

    # Normalize branch_id — UI passes "all" for the All-Branches view; treat
    # that the same as "no branch" so we keep the historic global behaviour.
    branch_scoped = bool(branch_id) and branch_id != "all"

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

    # ── Branch-scoped path ──────────────────────────────────────────────
    # Route price/cost into branch_prices; let everything else fall through
    # to the master update below. This is the user-facing fix for the
    # "I edited Branch 1 prices and accidentally clobbered every branch"
    # bug — the master is now untouchable from a branch view.
    if branch_scoped and ("prices" in update or "cost_price" in update):
        # Confirm the branch exists and belongs to this user's org
        br = await db.branches.find_one(
            {"id": branch_id, "active": {"$ne": False}}, {"_id": 0, "id": 1}
        )
        if not br:
            raise HTTPException(status_code=404, detail="Branch not found")

        # Merge new prices on top of any existing override (override wins
        # per-key; missing keys keep prior values intact).
        existing_override = await db.branch_prices.find_one(
            {"product_id": product_id, "branch_id": branch_id}, {"_id": 0}
        )
        merged_prices = dict((existing_override or {}).get("prices") or {})
        if "prices" in update and isinstance(update["prices"], dict):
            for k, v in update["prices"].items():
                try:
                    merged_prices[k] = float(v)
                except (TypeError, ValueError):
                    # Ignore garbage cells; never crash the save flow.
                    continue

        bp_doc = {
            "product_id": product_id,
            "branch_id": branch_id,
            "prices": merged_prices,
            "updated_at": now_iso(),
            "updated_by_id": user["id"],
            "updated_by_name": user.get("full_name", user.get("username", "")),
        }
        if "cost_price" in update:
            bp_doc["cost_price"] = float(update["cost_price"])

        if existing_override:
            await db.branch_prices.update_one(
                {"product_id": product_id, "branch_id": branch_id},
                {"$set": bp_doc},
            )
        else:
            bp_doc["id"] = new_id()
            bp_doc["created_at"] = now_iso()
            await db.branch_prices.insert_one(bp_doc)

        # Mark the branch override as reviewed so the "Global Price" alert
        # banner clears on this branch.
        try:
            await mark_price_reviewed(product_id, branch_id, source="manual")
        except Exception:
            pass

        # Now strip the branch-scoped fields so they don't ALSO leak into
        # the master update path below. Catalog fields (name/category/etc.)
        # in `update` will still flow through.
        update.pop("prices", None)
        update.pop("cost_price", None)

    # Nothing left to write to the master? Return the (effective) product.
    if not update:
        product = await db.products.find_one({"id": product_id}, {"_id": 0})
        return await _apply_branch_override(product, branch_id) if branch_scoped else product

    update["updated_at"] = now_iso()

    # Log capital change if cost_price is being updated (master path only —
    # branch-scoped cost edits are tracked via branch_prices.updated_at and
    # already audited per-row by the import flow).
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

    # Manual price/capital edit on the global product = treated as an explicit
    # decision. Clear "Global Price" badge on every branch that has inventory
    # of this product. Cheap: typically <30 branches.
    if any(k in update for k in ("cost_price", "prices")):
        inv_rows = await db.inventory.find(
            {"product_id": product_id}, {"_id": 0, "branch_id": 1}
        ).to_list(500)
        for inv in inv_rows:
            await mark_price_reviewed(product_id, inv["branch_id"], source="manual")

    product = await db.products.find_one({"id": product_id}, {"_id": 0})
    return await _apply_branch_override(product, branch_id) if branch_scoped else product


async def _apply_branch_override(product: dict, branch_id: Optional[str]) -> dict:
    """Helper used by the update endpoint to return what the BRANCH actually
    sees (master ⊕ override) when the caller edited under a branch context.
    Mirrors the same merge logic used by GET /products."""
    if not product or not branch_id or branch_id == "all":
        return product
    override = await db.branch_prices.find_one(
        {"product_id": product["id"], "branch_id": branch_id}, {"_id": 0}
    )
    if not override:
        return {**product, "price_source": "global"}
    merged = {**(product.get("prices") or {}), **(override.get("prices") or {})}
    out = {**product, "prices": merged, "price_source": "branch_override"}
    if override.get("cost_price") is not None:
        out["cost_price"] = override["cost_price"]
    return out


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
    """Soft delete a product and its repacks. Admin/Owner only — managers
    cannot delete products even if they have a generic `products.delete`
    permission. They should disable-at-branch instead."""
    role = user.get("role", "")
    if role not in ("admin", "owner") and not user.get("is_super_admin"):
        raise HTTPException(
            status_code=403,
            detail="Only Admin or Owner can delete products. Managers can disable products at their branch instead.",
        )
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
    """Generate a repack product from a parent product.

    Branch-scoped pricing: caller MUST pass `branch_id`. The repack catalog row is
    global (name/SKU/units_per_parent/add_on_cost), but cost_price is NOT stored
    (always computed on-the-fly via get_repack_capital), and any retail prices
    submitted are written to branch_prices for the selected branch only.
    """
    check_perm(user, "products", "create")

    parent = await db.products.find_one({"id": product_id, "active": True}, {"_id": 0})
    if not parent:
        raise HTTPException(status_code=404, detail="Parent product not found")
    if parent.get("is_repack"):
        raise HTTPException(status_code=400, detail="Cannot create repack from a repack")

    branch_id = (data.get("branch_id") or "").strip()
    if not branch_id:
        raise HTTPException(
            status_code=400,
            detail="Please select a branch first to set repack price"
        )

    repack_sku = f"R-{parent['sku']}"
    existing = await db.products.find_one({"sku": repack_sku, "active": True}, {"_id": 0})
    if existing:
        count = await db.products.count_documents({"parent_id": product_id, "active": True})
        repack_sku = f"R-{parent['sku']}-{count + 1}"

    repack_name = data.get("name", f"R {parent['name']}")
    units = int(data.get("units_per_parent", 1))
    if units <= 0:
        units = 1
    add_on_cost = float(data.get("add_on_cost", 0) or 0)
    submitted_prices = data.get("prices", {}) or {}
    # Filter retail prices that are > 0
    submitted_prices = {k: float(v) for k, v in submitted_prices.items() if float(v or 0) > 0}

    repack = {
        "id": new_id(),
        "sku": repack_sku,
        "name": repack_name,
        "category": parent["category"],
        "description": f"Repack from {parent['name']} ({parent['unit']})",
        "unit": data.get("unit", "Piece"),
        # cost_price is NOT stored — derived on-the-fly per branch via get_repack_capital()
        # Keep field at 0 for legacy reads; runtime always uses get_repack_capital().
        "cost_price": 0,
        # prices stay empty globally — branch retail lives in branch_prices
        "prices": {},
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

    # Write retail prices to branch_prices for the selected branch
    if submitted_prices:
        await db.branch_prices.update_one(
            {"product_id": repack["id"], "branch_id": branch_id},
            {
                "$set": {
                    "prices": submitted_prices,
                    "updated_at": now_iso(),
                    "updated_by": user.get("full_name", user.get("username", "")),
                    "source": "repack_create",
                },
                "$setOnInsert": {
                    "id": new_id(),
                    "product_id": repack["id"],
                    "branch_id": branch_id,
                    "created_at": now_iso(),
                },
            },
            upsert=True,
        )

    repack.pop("_id", None)
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

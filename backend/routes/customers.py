"""
Customer management routes with multi-branch support.
"""
from fastapi import APIRouter, Depends, HTTPException
from typing import Optional
from datetime import datetime, timezone
import re
from difflib import SequenceMatcher
from config import db, _raw_db
from utils import (
    get_current_user, check_perm, now_iso, new_id,
    get_default_branch, ensure_branch_access
)
from routes.verify import verify_pin_for_action

router = APIRouter(prefix="/customers", tags=["Customers"])


# =============================================================================
# Fuzzy-name / phone helpers (duplicated from import_data on purpose to avoid
# circular import — keep in sync if you tweak thresholds).
# =============================================================================
_NORM_RE = re.compile(r"[^a-z0-9 ]+")
_DEDUP_SIMILARITY_THRESHOLD = 0.85


def _norm_name(s: str) -> str:
    return _NORM_RE.sub(" ", (s or "").lower()).strip()


def _token_sort(s: str) -> str:
    return " ".join(sorted(_norm_name(s).split()))


def _phone_tail(p: str, n: int = 9) -> str:
    digits = re.sub(r"\D", "", p or "")
    return digits[-n:] if len(digits) >= n else digits


def _name_similar(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, _token_sort(a), _token_sort(b)).ratio()


def _norm_phone(p: str) -> str:
    """Normalize phone to local 09... format."""
    n = p.strip().lstrip("+")
    if n.startswith("63") and len(n) > 10:
        n = "0" + n[2:]
    return n


async def _auto_migrate_sms(phones: list, customer_id: str, customer_name: str, branch_id: str):
    """When a customer is created/updated with phone(s), auto-migrate any Unknown
    sms_inbox records for those numbers into this customer's conversation.
    """
    if not phones:
        return 0
    all_variants = set()
    for p in phones:
        if not p:
            continue
        normalized = _norm_phone(p)
        all_variants.add(p)
        all_variants.add(normalized)
        if normalized.startswith("09") and len(normalized) == 11:
            all_variants.add("+63" + normalized[1:])

    result = await _raw_db.sms_inbox.update_many(
        {"phone": {"$in": list(all_variants)}, "customer_id": ""},
        {"$set": {
            "customer_id": customer_id,
            "customer_name": customer_name,
            "branch_id": branch_id,
            "registered": True,
        }}
    )
    return result.modified_count


@router.get("")
async def list_customers(
    user=Depends(get_current_user),
    search: Optional[str] = None,
    branch_id: Optional[str] = None,
    skip: int = 0,
    limit: int = 50
):
    """List customers filtered by branch.
    - Specific branch_id provided → only that branch's customers
    - Admin with no branch_id → all customers (consolidated owner view)
    - Non-admin with no branch_id → their assigned branch only
    """
    query = {"active": True}

    if branch_id:
        # Explicit branch requested — verify access and filter
        await ensure_branch_access(user, branch_id)
        query["branch_id"] = branch_id
    elif user.get("role") != "admin":
        # Non-admin: restrict to their assigned branch
        user_branch = user.get("branch_id")
        if user_branch:
            query["branch_id"] = user_branch
        else:
            # No branch assigned — show nothing
            return {"customers": [], "total": 0}
    # Admin with no branch_id → no filter (sees all, for consolidated view)

    if search:
        query["$or"] = [
            {"name": {"$regex": search, "$options": "i"}},
            {"phone": {"$regex": search, "$options": "i"}}
        ]

    total = await db.customers.count_documents(query)
    customers = await db.customers.find(query, {"_id": 0}).skip(skip).limit(limit).to_list(limit)
    return {"customers": customers, "total": total}


@router.post("")
async def create_customer(data: dict, user=Depends(get_current_user)):
    """Create a new customer. Accepts phones[] array or a single phone string."""
    check_perm(user, "customers", "create")
    
    branch_id = data.get("branch_id")
    if not branch_id:
        branch_id = await get_default_branch(user)

    # Build unified phones list — deduplicated, normalized
    phones_raw = data.get("phones") or []
    if data.get("phone") and data["phone"].strip():
        phones_raw = [data["phone"]] + [p for p in phones_raw if p != data["phone"]]
    phones = list(dict.fromkeys(_norm_phone(p) for p in phones_raw if p.strip()))
    phone_primary = phones[0] if phones else ""

    customer = {
        "id": new_id(),
        "name": data["name"],
        "phone": phone_primary,
        "phones": phones,
        "email": data.get("email", ""),
        "address": data.get("address", ""),
        "price_scheme": data.get("price_scheme", "retail"),
        "credit_limit": float(data.get("credit_limit", 0)),
        "interest_rate": float(data.get("interest_rate", 0)),
        "grace_period": int(data.get("grace_period", 7)),
        "balance": 0.0,
        "branch_id": branch_id,
        "active": True,
        "created_at": now_iso(),
    }
    await db.customers.insert_one(customer)
    del customer["_id"]

    # Auto-migrate Unknown SMS for all registered phones
    if phones:
        await _auto_migrate_sms(phones, customer["id"], customer["name"], branch_id or "")

    return customer


@router.get("/receivables-summary")
async def customer_receivables_summary(
    branch_id: Optional[str] = None,
    include_zero: bool = False,
    user=Depends(get_current_user),
):
    """Aggregate open invoices per customer: total balance, overdue balance, invoice count."""
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    inv_match = {"status": {"$nin": ["voided", "paid"]}, "balance": {"$gt": 0}}
    if branch_id:
        inv_match["branch_id"] = branch_id

    pipeline = [
        {"$match": inv_match},
        {
            "$addFields": {
                "is_overdue": {
                    "$cond": [
                        {"$and": [
                            {"$ne": [{"$ifNull": ["$due_date", None]}, None]},
                            {"$lt": ["$due_date", today_str]},
                        ]},
                        True,
                        False,
                    ]
                }
            }
        },
        {
            "$group": {
                "_id": "$customer_id",
                "total_balance": {"$sum": "$balance"},
                "overdue_balance": {
                    "$sum": {"$cond": ["$is_overdue", "$balance", 0]}
                },
                "invoice_count": {"$sum": 1},
                "overdue_count": {
                    "$sum": {"$cond": ["$is_overdue", 1, 0]}
                },
                "oldest_overdue_due_date": {
                    "$min": {"$cond": ["$is_overdue", "$due_date", None]}
                },
            }
        },
        {"$sort": {"total_balance": -1}},
    ]

    agg_results = await db.invoices.aggregate(pipeline).to_list(5000)

    cust_ids = [r["_id"] for r in agg_results if r["_id"]]
    agg_map = {r["_id"]: r for r in agg_results}

    cust_query = {"active": True}
    if branch_id:
        cust_query["branch_id"] = branch_id

    if include_zero:
        customers = await db.customers.find(cust_query, {"_id": 0}).to_list(5000)
    else:
        cust_query["id"] = {"$in": cust_ids}
        customers = await db.customers.find(cust_query, {"_id": 0}).to_list(5000)

    result = []
    for c in customers:
        agg = agg_map.get(c["id"], {})
        result.append({
            "id": c["id"],
            "name": c.get("name", ""),
            "phone": c.get("phone", ""),
            "balance": round(agg.get("total_balance", 0), 2),
            "overdue_balance": round(agg.get("overdue_balance", 0), 2),
            "invoice_count": agg.get("invoice_count", 0),
            "overdue_count": agg.get("overdue_count", 0),
            "oldest_overdue_due_date": agg.get("oldest_overdue_due_date"),
            "interest_rate": c.get("interest_rate", 0),
            "grace_period": c.get("grace_period", 7),
            "credit_limit": c.get("credit_limit", 0),
        })

    return result


@router.get("/orphan-receivables")
async def list_orphan_receivables(user=Depends(get_current_user)):
    """
    Surface invoices whose customer_id no longer resolves to an active customer.
    Groups by orphan customer_id and aggregates name, count, and outstanding balance.
    Use the reattach-orphans endpoint to fix.
    """
    check_perm(user, "customers", "view")

    pipeline = [
        {"$match": {"status": {"$nin": ["paid", "voided"]}, "customer_id": {"$nin": [None, ""]}}},
        {"$group": {
            "_id": "$customer_id",
            "name": {"$first": "$customer_name"},
            "branch_id": {"$first": "$branch_id"},
            "invoice_count": {"$sum": 1},
            "total_balance": {"$sum": "$balance"},
            "newest": {"$max": "$created_at"},
        }},
        {"$sort": {"total_balance": -1}},
    ]
    rows = await db.invoices.aggregate(pipeline).to_list(500)

    orphans = []
    for r in rows:
        cust = await db.customers.find_one({"id": r["_id"], "active": True}, {"_id": 0, "id": 1})
        if not cust:
            orphans.append({
                "customer_id": r["_id"],
                "customer_name": r.get("name") or "(unknown)",
                "branch_id": r.get("branch_id"),
                "invoice_count": r["invoice_count"],
                "total_balance": float(r["total_balance"] or 0),
                "newest": r.get("newest"),
            })
    return {"orphans": orphans, "total": len(orphans)}


@router.get("/{customer_id}")
async def get_customer(customer_id: str, user=Depends(get_current_user)):
    """Get customer details."""
    customer = await db.customers.find_one({"id": customer_id}, {"_id": 0})
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    return customer


@router.put("/{customer_id}")
async def update_customer(customer_id: str, data: dict, user=Depends(get_current_user)):
    """Update customer details. Supports phones[] array or single phone."""
    check_perm(user, "customers", "edit")
    
    allowed = ["name", "email", "address", "price_scheme",
               "credit_limit", "interest_rate", "grace_period"]
    update = {k: v for k, v in data.items() if k in allowed}

    # Handle phones update
    old_phones = []
    if "phones" in data or "phone" in data:
        existing = await db.customers.find_one({"id": customer_id}, {"_id": 0, "phones": 1, "phone": 1, "branch_id": 1, "name": 1})
        old_phones = (existing or {}).get("phones") or []
        phones_raw = data.get("phones") or []
        if data.get("phone") and data["phone"].strip():
            phones_raw = [data["phone"]] + [p for p in phones_raw if p != data["phone"]]
        if not phones_raw and old_phones:
            phones_raw = old_phones
        phones = list(dict.fromkeys(_norm_phone(p) for p in phones_raw if p.strip()))
        update["phones"] = phones
        update["phone"] = phones[0] if phones else ""

    update["updated_at"] = now_iso()
    await db.customers.update_one({"id": customer_id}, {"$set": update})
    customer = await db.customers.find_one({"id": customer_id}, {"_id": 0})

    # Auto-migrate Unknown SMS for any newly added phones
    if customer and update.get("phones"):
        new_phones = [p for p in update["phones"] if p not in old_phones]
        if new_phones:
            await _auto_migrate_sms(new_phones, customer["id"], customer["name"], customer.get("branch_id", ""))

    return customer


@router.post("/{customer_id}/phones")
async def add_customer_phone(customer_id: str, data: dict, user=Depends(get_current_user)):
    """Add a phone number to an existing customer's phones list."""
    check_perm(user, "customers", "edit")
    phone = _norm_phone((data.get("phone") or "").strip())
    if not phone:
        raise HTTPException(status_code=400, detail="Phone number required")

    customer = await db.customers.find_one({"id": customer_id}, {"_id": 0})
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    existing_phones = customer.get("phones") or ([customer["phone"]] if customer.get("phone") else [])
    if phone in existing_phones:
        return customer  # Already registered — no-op

    new_phones = existing_phones + [phone]
    await db.customers.update_one(
        {"id": customer_id},
        {"$set": {"phones": new_phones, "phone": new_phones[0], "updated_at": now_iso()}}
    )
    # Migrate Unknown SMS for the new phone
    await _auto_migrate_sms([phone], customer_id, customer["name"], customer.get("branch_id", ""))
    return await db.customers.find_one({"id": customer_id}, {"_id": 0})


@router.delete("/{customer_id}/phones/{phone_num}")
async def remove_customer_phone(customer_id: str, phone_num: str, user=Depends(get_current_user)):
    """Remove a phone number from a customer's phones list."""
    check_perm(user, "customers", "edit")
    phone = _norm_phone(phone_num)
    customer = await db.customers.find_one({"id": customer_id}, {"_id": 0})
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    phones = [p for p in (customer.get("phones") or []) if p != phone]
    await db.customers.update_one(
        {"id": customer_id},
        {"$set": {"phones": phones, "phone": phones[0] if phones else "", "updated_at": now_iso()}}
    )
    return await db.customers.find_one({"id": customer_id}, {"_id": 0})


@router.delete("/{customer_id}")
async def delete_customer(customer_id: str, force: bool = False, user=Depends(get_current_user)):
    """
    Soft-delete a customer. Refuses if the customer has open invoices or a
    non-zero balance, unless ?force=true is passed by an admin.
    Sets deactivated_at so the sync API can tell terminals to purge cache.
    """
    check_perm(user, "customers", "delete")

    customer = await db.customers.find_one({"id": customer_id, "active": True}, {"_id": 0})
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    # Guard against orphaning data
    if not force:
        balance = float(customer.get("balance", 0) or 0)
        if balance > 0:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Cannot delete '{customer.get('name')}' — outstanding balance of ₱{balance:.2f}. "
                    "Settle the balance first, or pass ?force=true to delete anyway (orphan invoices will remain)."
                ),
            )
        open_count = await db.invoices.count_documents({
            "customer_id": customer_id,
            "status": {"$nin": ["paid", "voided"]},
        })
        if open_count > 0:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Cannot delete '{customer.get('name')}' — {open_count} open invoice(s) still pending. "
                    "Settle or void them first, or pass ?force=true to override."
                ),
            )
        # Admin-only override
        if force and user.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Only admins can force-delete customers")

    await db.customers.update_one(
        {"id": customer_id},
        {"$set": {"active": False, "deactivated_at": now_iso(), "updated_at": now_iso()}},
    )
    return {"message": "Customer deleted", "id": customer_id}


@router.post("/reattach-orphans")
async def reattach_orphan_invoices(data: dict, user=Depends(get_current_user)):
    """
    Reattach invoices whose customer_id no longer resolves to an active customer.
    Body: { "to_customer_id": "<existing_active_customer_id>", "from_customer_ids": ["<orphan_id>", ...] }
    Updates all matched invoices' customer_id and customer_name. Recomputes the
    target customer's balance as the sum of (open invoice balance) afterwards.
    """
    check_perm(user, "customers", "edit")
    to_id = (data.get("to_customer_id") or "").strip()
    from_ids = data.get("from_customer_ids") or []
    if not to_id or not from_ids:
        raise HTTPException(status_code=400, detail="to_customer_id and from_customer_ids are required")

    target = await db.customers.find_one({"id": to_id, "active": True}, {"_id": 0})
    if not target:
        raise HTTPException(status_code=404, detail="Target customer not found")

    result = await db.invoices.update_many(
        {"customer_id": {"$in": from_ids}},
        {"$set": {
            "customer_id": to_id,
            "customer_name": target.get("name", ""),
            "reattached_from": from_ids,
            "reattached_at": now_iso(),
        }},
    )

    # Recompute balance from open invoices
    pipeline = [
        {"$match": {"customer_id": to_id, "status": {"$nin": ["paid", "voided"]}}},
        {"$group": {"_id": None, "balance": {"$sum": "$balance"}}},
    ]
    agg = await db.invoices.aggregate(pipeline).to_list(1)
    new_balance = float(agg[0]["balance"]) if agg else 0.0
    await db.customers.update_one(
        {"id": to_id},
        {"$set": {"balance": new_balance, "updated_at": now_iso()}},
    )

    return {"reattached": result.modified_count, "new_balance": new_balance, "to_customer_id": to_id}


@router.get("/{customer_id}/transactions")
async def get_customer_transactions(customer_id: str, user=Depends(get_current_user)):
    """Get all transactions for a customer (invoices + payments + receivables)."""
    # Get all invoices for customer
    invoices = await db.invoices.find(
        {"customer_id": customer_id},
        {"_id": 0}
    ).sort("created_at", -1).to_list(500)
    
    # Get all receivables (from old POS system)
    receivables = await db.receivables.find(
        {"customer_id": customer_id},
        {"_id": 0}
    ).sort("created_at", -1).to_list(500)
    
    # Get customer info
    customer = await db.customers.find_one({"id": customer_id}, {"_id": 0})
    
    # Calculate totals
    total_invoiced = sum(inv.get("grand_total", 0) for inv in invoices if inv.get("status") != "voided")
    total_paid = sum(inv.get("amount_paid", 0) for inv in invoices if inv.get("status") != "voided")
    total_balance = sum(inv.get("balance", 0) for inv in invoices if inv.get("status") not in ["paid", "voided"])
    
    # Add receivables totals
    total_receivables = sum(r.get("balance", 0) for r in receivables if r.get("status") != "paid")
    
    return {
        "customer": customer,
        "invoices": invoices,
        "receivables": receivables,
        "summary": {
            "total_invoiced": total_invoiced,
            "total_paid": total_paid,
            "total_balance": total_balance + total_receivables,
            "invoice_count": len(invoices),
            "open_invoices": len([i for i in invoices if i.get("status") not in ["paid", "voided"]]),
        }
    }


# =============================================================================
# Customer Duplicate Detection & Merge (post-import dedupe tool)
# =============================================================================

@router.get("/-/duplicates")
async def list_duplicate_clusters(
    branch_id: Optional[str] = None,
    user=Depends(get_current_user),
):
    """
    Scan active customers in the branch and return clusters of likely duplicates.

    Two customers are linked if ANY of:
      - normalized names exactly match
      - token-sorted name similarity >= 0.85  (handles "James Ahig" ≡ "Ahig James")
      - they share a phone tail (last 9 digits)

    Pairs the user has explicitly marked "distinct" (via /mark-distinct) are
    excluded from the returned clusters.

    Used by the Customer Dedupe popup (behaves like Smart Price Checker).
    """
    check_perm(user, "customers", "view")

    # Resolve branch scope — respect existing access-control patterns
    query = {"active": True}
    if branch_id:
        await ensure_branch_access(user, branch_id)
        query["branch_id"] = branch_id
    elif user.get("role") != "admin":
        user_branch = user.get("branch_id")
        if user_branch:
            query["branch_id"] = user_branch
        else:
            return {"clusters": [], "total_clusters": 0, "total_customers": 0}

    customers = await db.customers.find(query, {"_id": 0}).to_list(20000)
    if len(customers) < 2:
        return {"clusters": [], "total_clusters": 0, "total_customers": 0}

    # Load remembered "distinct" pairwise decisions for this branch scope
    distinct_query = {}
    if branch_id:
        distinct_query["branch_id"] = branch_id
    distinct_docs = await db.customer_dedupe_decisions.find(
        distinct_query, {"_id": 0, "pair": 1}
    ).to_list(20000)
    distinct_pairs = set()
    for d in distinct_docs:
        pair = d.get("pair") or []
        if len(pair) == 2:
            distinct_pairs.add(tuple(sorted(pair)))

    # Union-Find over customers — link any two that look like duplicates
    n = len(customers)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    norms = [_token_sort(c.get("name", "")) for c in customers]
    raw_norms = [_norm_name(c.get("name", "")) for c in customers]
    phone_sets = []
    for c in customers:
        tails = set()
        for p in (c.get("phones") or ([c.get("phone")] if c.get("phone") else [])):
            t = _phone_tail(p)
            if t:
                tails.add(t)
        phone_sets.append(tails)

    # O(n²) — fine for a branch of up to ~5k customers. If this grows, switch
    # to a blocking strategy (first-letter + length bucket) before the pairwise.
    for i in range(n):
        for j in range(i + 1, n):
            pair_key = tuple(sorted([customers[i]["id"], customers[j]["id"]]))
            if pair_key in distinct_pairs:
                continue
            # Exact normalized-name match
            if raw_norms[i] and raw_norms[i] == raw_norms[j]:
                union(i, j)
                continue
            # Phone-tail overlap
            if phone_sets[i] and phone_sets[j] and phone_sets[i] & phone_sets[j]:
                union(i, j)
                continue
            # Token-sorted similarity
            if norms[i] and norms[j]:
                ratio = SequenceMatcher(None, norms[i], norms[j]).ratio()
                if ratio >= _DEDUP_SIMILARITY_THRESHOLD:
                    union(i, j)

    # Collect clusters of size ≥ 2
    groups: dict = {}
    for i in range(n):
        r = find(i)
        groups.setdefault(r, []).append(i)

    # For each clustered customer, fetch open-invoice summary (one aggregate for all)
    cluster_member_ids = []
    for idxs in groups.values():
        if len(idxs) >= 2:
            for i in idxs:
                cluster_member_ids.append(customers[i]["id"])

    inv_summary: dict = {}
    if cluster_member_ids:
        pipeline = [
            {"$match": {"customer_id": {"$in": cluster_member_ids}}},
            {
                "$group": {
                    "_id": "$customer_id",
                    "invoice_count": {"$sum": 1},
                    "open_balance": {
                        "$sum": {
                            "$cond": [
                                {"$in": ["$status", ["paid", "voided"]]},
                                0,
                                "$balance",
                            ]
                        }
                    },
                }
            },
        ]
        for row in await db.invoices.aggregate(pipeline).to_list(20000):
            inv_summary[row["_id"]] = {
                "invoice_count": row["invoice_count"],
                "open_balance": round(float(row["open_balance"] or 0), 2),
            }

    clusters = []
    for idxs in groups.values():
        if len(idxs) < 2:
            continue
        members = []
        for i in idxs:
            c = customers[i]
            summ = inv_summary.get(c["id"], {"invoice_count": 0, "open_balance": 0.0})
            members.append({
                "id": c["id"],
                "name": c.get("name", ""),
                "phone": c.get("phone", ""),
                "phones": c.get("phones") or [],
                "email": c.get("email", ""),
                "address": c.get("address", ""),
                "price_scheme": c.get("price_scheme", "retail"),
                "credit_limit": float(c.get("credit_limit", 0) or 0),
                "interest_rate": float(c.get("interest_rate", 0) or 0),
                "grace_period": c.get("grace_period", 7),
                "balance": float(c.get("balance", 0) or 0),
                "open_balance": summ["open_balance"],
                "invoice_count": summ["invoice_count"],
                "created_at": c.get("created_at", ""),
                "imported_from": c.get("imported_from", ""),
            })
        # Sort most-active first so user tends to pick a rich record as master
        members.sort(key=lambda m: (m["invoice_count"], m["open_balance"]), reverse=True)
        # Similarity to the top member — gives user a confidence hint
        top_name = members[0]["name"]
        for m in members:
            m["similarity"] = round(_name_similar(top_name, m["name"]), 3) if m["name"] != top_name else 1.0
        clusters.append({
            "id": f"cluster_{members[0]['id']}",
            "members": members,
            "member_count": len(members),
        })

    # Rank clusters by combined open balance — put the highest-stakes ones first
    clusters.sort(key=lambda c: sum(m["open_balance"] for m in c["members"]), reverse=True)

    return {
        "clusters": clusters,
        "total_clusters": len(clusters),
        "total_customers": sum(c["member_count"] for c in clusters),
        "branch_id": branch_id or (user.get("branch_id") or ""),
    }


@router.post("/mark-distinct")
async def mark_customers_distinct(data: dict, user=Depends(get_current_user)):
    """
    Remember that a set of customer ids are NOT duplicates of each other.
    Persists every pairwise combination so the dedupe scan won't flag them
    again — UNTIL a NEW customer joins the cluster (a new pair is fresh).
    """
    check_perm(user, "customers", "edit")
    ids = data.get("customer_ids") or []
    if len(ids) < 2:
        raise HTTPException(status_code=400, detail="At least 2 customer ids required")

    existing = await db.customers.find(
        {"id": {"$in": ids}, "active": True},
        {"_id": 0, "id": 1, "branch_id": 1},
    ).to_list(1000)
    if len(existing) != len(set(ids)):
        raise HTTPException(status_code=404, detail="One or more customers not found or inactive")

    branches = {c.get("branch_id") or "" for c in existing}
    if len(branches) > 1:
        raise HTTPException(status_code=400, detail="All customers must be in the same branch")
    branch_id = next(iter(branches))

    recorded = 0
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            pair = sorted([ids[i], ids[j]])
            existing_pair = await db.customer_dedupe_decisions.find_one(
                {"pair": pair}, {"_id": 0, "id": 1}
            )
            if existing_pair:
                continue
            await db.customer_dedupe_decisions.insert_one({
                "id": new_id(),
                "branch_id": branch_id,
                "pair": pair,
                "decision": "distinct",
                "decided_by_id": user["id"],
                "decided_by_name": user.get("full_name", user.get("username", "")),
                "created_at": now_iso(),
            })
            recorded += 1

    return {"recorded": recorded, "total_pairs": len(ids) * (len(ids) - 1) // 2}


@router.post("/merge")
async def merge_customers(data: dict, user=Depends(get_current_user)):
    """
    Merge one or more duplicate customers INTO a master customer.

    Rules (confirmed with user):
      - Master's non-empty fields WIN.
      - If master's field is empty/zero and a duplicate has a value, copy it over.
      - Phones: concat + dedupe (master's order preserved, duplicates' new numbers appended).
      - Invoices / receivables / sms_inbox entries get their customer_id re-pointed to master.
      - Duplicates soft-deleted (active=false, merged_into=<master_id>, merged_at=...).
      - Master's balance recomputed from open invoices.
      - Full audit trail in customer_merges collection.

    Body: {
      "master_id": "<id>",
      "duplicate_ids": ["<id1>", "<id2>"],
      "canonical_name": "Ahig Janmark"   # optional — renames master
    }
    """
    check_perm(user, "customers", "edit")

    master_id = (data.get("master_id") or "").strip()
    dup_ids = [str(x).strip() for x in (data.get("duplicate_ids") or []) if x]
    canonical_name = (data.get("canonical_name") or "").strip()

    if not master_id or not dup_ids:
        raise HTTPException(status_code=400, detail="master_id and duplicate_ids are required")
    if master_id in dup_ids:
        raise HTTPException(status_code=400, detail="master_id cannot also appear in duplicate_ids")

    master = await db.customers.find_one({"id": master_id, "active": True}, {"_id": 0})
    if not master:
        raise HTTPException(status_code=404, detail="Master customer not found")

    duplicates = await db.customers.find(
        {"id": {"$in": dup_ids}, "active": True}, {"_id": 0}
    ).to_list(100)
    if len(duplicates) != len(dup_ids):
        raise HTTPException(status_code=404, detail="One or more duplicate customers not found or already merged")

    # Enforce same-branch merge (prevents cross-branch corruption)
    master_branch = master.get("branch_id", "")
    for d in duplicates:
        if d.get("branch_id", "") != master_branch:
            raise HTTPException(status_code=400, detail="All customers must belong to the same branch")

    master_before_name = master.get("name", "")

    # Merge fields per rule: master wins if has value, else take first non-empty dup value
    updates: dict = {}

    def _fill_if_empty(field: str, default=""):
        master_val = master.get(field)
        # treat empty string, None, and 0 (for numeric fields) as empty
        is_empty = master_val in (None, "", 0, 0.0)
        if is_empty:
            for d in duplicates:
                d_val = d.get(field)
                if d_val not in (None, "", 0, 0.0):
                    updates[field] = d_val
                    return

    for f in ("email", "address", "price_scheme"):
        _fill_if_empty(f, "")
    for f in ("credit_limit", "interest_rate", "grace_period"):
        _fill_if_empty(f, 0)

    # Phones — master first, then all dup phones, dedup
    merged_phones = []
    seen_phones = set()
    for p in (master.get("phones") or ([master.get("phone")] if master.get("phone") else [])):
        if p and p not in seen_phones:
            merged_phones.append(p)
            seen_phones.add(p)
    for d in duplicates:
        for p in (d.get("phones") or ([d.get("phone")] if d.get("phone") else [])):
            if p and p not in seen_phones:
                merged_phones.append(p)
                seen_phones.add(p)
    if merged_phones != (master.get("phones") or []):
        updates["phones"] = merged_phones
        updates["phone"] = merged_phones[0] if merged_phones else master.get("phone", "")

    # Canonical name — optional rename of the master
    if canonical_name and canonical_name != master.get("name", ""):
        updates["name"] = canonical_name
    master_after_name = canonical_name or master_before_name

    # Re-point invoices
    inv_result = await db.invoices.update_many(
        {"customer_id": {"$in": dup_ids}},
        {"$set": {
            "customer_id": master_id,
            "customer_name": master_after_name,
            "merged_from_customer_ids": dup_ids,
            "merged_at": now_iso(),
        }},
    )

    # Re-point receivables (legacy POS collection)
    rec_result = await db.receivables.update_many(
        {"customer_id": {"$in": dup_ids}},
        {"$set": {
            "customer_id": master_id,
            "customer_name": master_after_name,
            "merged_from_customer_ids": dup_ids,
            "merged_at": now_iso(),
        }},
    )

    # Re-point SMS inbox
    sms_result = await _raw_db.sms_inbox.update_many(
        {"customer_id": {"$in": dup_ids}},
        {"$set": {
            "customer_id": master_id,
            "customer_name": master_after_name,
        }},
    )

    # Apply master-field updates
    if updates:
        updates["updated_at"] = now_iso()
        await db.customers.update_one({"id": master_id}, {"$set": updates})

    # Soft-delete duplicates
    await db.customers.update_many(
        {"id": {"$in": dup_ids}},
        {"$set": {
            "active": False,
            "merged_into": master_id,
            "merged_at": now_iso(),
            "deactivated_at": now_iso(),
            "updated_at": now_iso(),
        }},
    )

    # Recompute master balance from open invoices
    pipeline = [
        {"$match": {"customer_id": master_id, "status": {"$nin": ["paid", "voided"]}}},
        {"$group": {"_id": None, "balance": {"$sum": "$balance"}}},
    ]
    agg = await db.invoices.aggregate(pipeline).to_list(1)
    new_balance = float(agg[0]["balance"]) if agg else 0.0
    await db.customers.update_one(
        {"id": master_id},
        {"$set": {"balance": round(new_balance, 2), "updated_at": now_iso()}},
    )

    # Audit trail
    await db.customer_merges.insert_one({
        "id": new_id(),
        "master_id": master_id,
        "master_name_before": master_before_name,
        "master_name_after": master_after_name,
        "merged_ids": dup_ids,
        "merged_names": [d.get("name", "") for d in duplicates],
        "invoices_moved": inv_result.modified_count,
        "receivables_moved": rec_result.modified_count,
        "sms_moved": sms_result.modified_count,
        "fields_copied_to_master": list(updates.keys()),
        "balance_after": round(new_balance, 2),
        "branch_id": master_branch,
        "performed_by_id": user["id"],
        "performed_by_name": user.get("full_name", user.get("username", "")),
        "created_at": now_iso(),
    })

    master_after = await db.customers.find_one({"id": master_id}, {"_id": 0})
    return {
        "merged": True,
        "master": master_after,
        "duplicates_merged": len(dup_ids),
        "invoices_moved": inv_result.modified_count,
        "receivables_moved": rec_result.modified_count,
        "sms_moved": sms_result.modified_count,
        "balance_after": round(new_balance, 2),
    }


# =============================================================================
# Bulk Delete (PIN-gated — used to clean up bad imports)
# =============================================================================

@router.post("/bulk-delete")
async def bulk_delete_customers(data: dict, user=Depends(get_current_user)):
    """
    Bulk soft-delete customers. PIN required (policy: customer_bulk_delete).

    Body: {
      "customer_ids": ["<id1>", ...],
      "pin": "1234",
      "force": false     # if true, also deletes customers with open invoices / balance>0
    }

    Per-row result is returned — rows blocked by guards are reported, not raised.
    """
    check_perm(user, "customers", "delete")

    ids = [str(x).strip() for x in (data.get("customer_ids") or []) if x]
    pin = str(data.get("pin") or "").strip()
    force = bool(data.get("force", False))

    if not ids:
        raise HTTPException(status_code=400, detail="customer_ids required")
    if not pin:
        raise HTTPException(status_code=400, detail="PIN required for bulk delete")

    # Verify PIN against 'customer_bulk_delete' policy
    verifier = await verify_pin_for_action(pin, "customer_bulk_delete")
    if not verifier:
        raise HTTPException(status_code=403, detail="Invalid PIN or not authorized for bulk delete")

    # Force-delete is admin/owner only, regardless of PIN tier
    if force and user.get("role") not in ("admin", "owner") and not user.get("is_super_admin"):
        raise HTTPException(status_code=403, detail="Only admin/owner can force-delete customers with open invoices")

    deleted: list[dict] = []
    blocked: list[dict] = []

    for cid in ids:
        try:
            c = await db.customers.find_one({"id": cid, "active": True}, {"_id": 0})
            if not c:
                blocked.append({"id": cid, "name": "(not found)", "reason": "not_found"})
                continue

            if not force:
                balance = float(c.get("balance", 0) or 0)
                if balance > 0:
                    blocked.append({
                        "id": cid, "name": c.get("name", ""),
                        "reason": "has_balance", "balance": round(balance, 2),
                    })
                    continue
                open_count = await db.invoices.count_documents({
                    "customer_id": cid,
                    "status": {"$nin": ["paid", "voided"]},
                })
                if open_count > 0:
                    blocked.append({
                        "id": cid, "name": c.get("name", ""),
                        "reason": "has_open_invoices", "open_invoices": open_count,
                    })
                    continue

            await db.customers.update_one(
                {"id": cid},
                {"$set": {
                    "active": False,
                    "deactivated_at": now_iso(),
                    "updated_at": now_iso(),
                    "bulk_deleted_by_id": user["id"],
                    "bulk_deleted_by_name": user.get("full_name", user.get("username", "")),
                    "bulk_deleted_verifier": verifier.get("verifier_name", ""),
                }},
            )
            deleted.append({"id": cid, "name": c.get("name", "")})
        except Exception as e:
            blocked.append({"id": cid, "name": "", "reason": "error", "error": str(e)})

    return {
        "deleted_count": len(deleted),
        "blocked_count": len(blocked),
        "deleted": deleted,
        "blocked": blocked,
        "verified_by": verifier.get("verifier_name", ""),
        "method": verifier.get("method", ""),
    }

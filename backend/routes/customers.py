"""
Customer management routes with multi-branch support.
"""
from fastapi import APIRouter, Depends, HTTPException
from typing import Optional
from datetime import datetime, timezone
from config import db, _raw_db
from utils import (
    get_current_user, check_perm, now_iso, new_id,
    get_default_branch, ensure_branch_access
)

router = APIRouter(prefix="/customers", tags=["Customers"])


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
async def delete_customer(customer_id: str, user=Depends(get_current_user)):
    """Soft delete a customer."""
    check_perm(user, "customers", "delete")
    await db.customers.update_one({"id": customer_id}, {"$set": {"active": False}})
    return {"message": "Customer deleted"}


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

"""
Reports routes: AR Aging, Sales Report, Expense Report.
"""
from fastapi import APIRouter, Depends
from typing import Optional
from datetime import datetime, timezone, timedelta
from config import db
from utils import get_current_user, check_perm, get_branch_filter, apply_branch_filter, today_local

router = APIRouter(tags=["Reports"])


# ==================== AR AGING REPORT ====================
@router.get("/reports/ar-aging")
async def ar_aging_report(
    branch_id: Optional[str] = None,
    user=Depends(get_current_user),
):
    """
    AR Aging Report: open invoice balances bucketed by age.
    Buckets: Current (0-30d), 31-60d, 61-90d, 90+d from today.
    """
    check_perm(user, "reports", "view")

    query = {
        "customer_id": {"$ne": None},
        "balance": {"$gt": 0},
        "status": {"$nin": ["voided", "paid"]},
    }
    branch_filter = await get_branch_filter(user, branch_id)
    query = apply_branch_filter(query, branch_filter)

    invoices = await db.invoices.find(query, {"_id": 0}).to_list(2000)

    today = datetime.strptime(await today_local(user.get("organization_id") or ""), "%Y-%m-%d").date()

    # Per-customer map: { customer_id: { name, buckets, invoices } }
    customers = {}

    totals = {"current": 0.0, "b31_60": 0.0, "b61_90": 0.0, "b90plus": 0.0, "total": 0.0}

    for inv in invoices:
        cid = inv.get("customer_id", "")
        cname = inv.get("customer_name", "Unknown")

        # Use invoice_date or order_date to compute age
        date_str = inv.get("invoice_date") or inv.get("order_date") or inv.get("created_at", "")[:10]
        try:
            inv_date = datetime.strptime(date_str[:10], "%Y-%m-%d").date()
            days_old = (today - inv_date).days
        except (ValueError, TypeError):
            days_old = 0

        balance = float(inv.get("balance", 0))

        # Bucket assignment
        if days_old <= 30:
            bucket = "current"
        elif days_old <= 60:
            bucket = "b31_60"
        elif days_old <= 90:
            bucket = "b61_90"
        else:
            bucket = "b90plus"

        if cid not in customers:
            customers[cid] = {
                "customer_id": cid,
                "customer_name": cname,
                "current": 0.0,
                "b31_60": 0.0,
                "b61_90": 0.0,
                "b90plus": 0.0,
                "total": 0.0,
                "invoices": [],
            }

        customers[cid][bucket] = round(customers[cid][bucket] + balance, 2)
        customers[cid]["total"] = round(customers[cid]["total"] + balance, 2)
        customers[cid]["invoices"].append({
            "invoice_number": inv.get("invoice_number", ""),
            "invoice_date": date_str[:10],
            "due_date": inv.get("due_date", ""),
            "grand_total": inv.get("grand_total", 0),
            "amount_paid": inv.get("amount_paid", 0),
            "balance": balance,
            "days_old": days_old,
            "bucket": bucket,
            "sale_type": inv.get("sale_type", "walk_in"),
        })

        totals[bucket] = round(totals[bucket] + balance, 2)
        totals["total"] = round(totals["total"] + balance, 2)

    rows = sorted(customers.values(), key=lambda x: x["total"], reverse=True)

    return {
        "as_of_date": today.isoformat(),
        "totals": totals,
        "rows": rows,
        "branch_filter": branch_id,
    }


# ==================== SALES REPORT ====================
@router.get("/reports/sales")
async def sales_report(
    branch_id: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    user=Depends(get_current_user),
):
    """
    Sales Report: totals by category, by payment type, and per-transaction.
    Uses sales_log for category breakdown. Uses invoices for transaction list.
    """
    check_perm(user, "reports", "view")

    today = await today_local(user.get("organization_id") or "")
    if not date_from:
        # Default: first day of current month
        date_from = datetime.now(timezone.utc).strftime("%Y-%m-01")
    if not date_to:
        date_to = today

    # ── Category breakdown from sales_log ─────────────────────────────────
    log_query = {"date": {"$gte": date_from, "$lte": date_to}}
    if branch_id:
        log_query["branch_id"] = branch_id

    cat_pipeline = [
        {"$match": log_query},
        {"$group": {
            "_id": {"category": "$category", "payment_method": "$payment_method"},
            "total": {"$sum": "$line_total"},
            "qty": {"$sum": "$quantity"},
            "count": {"$sum": 1},
        }},
        {"$sort": {"total": -1}},
    ]
    cat_results = await db.sales_log.aggregate(cat_pipeline).to_list(500)

    # Restructure: { category -> { total, qty, by_payment } }
    cat_map = {}
    payment_totals = {}
    for r in cat_results:
        cat = r["_id"]["category"] or "General"
        pay = (r["_id"]["payment_method"] or "cash").lower()
        total = round(r["total"], 2)
        qty = r["qty"]

        if cat not in cat_map:
            cat_map[cat] = {"category": cat, "total": 0.0, "qty": 0, "by_payment": {}}
        cat_map[cat]["total"] = round(cat_map[cat]["total"] + total, 2)
        cat_map[cat]["qty"] += qty
        cat_map[cat]["by_payment"][pay] = round(cat_map[cat]["by_payment"].get(pay, 0) + total, 2)

        payment_totals[pay] = round(payment_totals.get(pay, 0) + total, 2)

    categories = sorted(cat_map.values(), key=lambda x: x["total"], reverse=True)
    grand_total = round(sum(c["total"] for c in categories), 2)

    # ── Daily breakdown ────────────────────────────────────────────────────
    day_pipeline = [
        {"$match": log_query},
        {"$group": {"_id": "$date", "total": {"$sum": "$line_total"}, "count": {"$sum": 1}}},
        {"$sort": {"_id": 1}},
    ]
    day_results = await db.sales_log.aggregate(day_pipeline).to_list(365)
    daily = [{"date": r["_id"], "total": round(r["total"], 2), "count": r["count"]} for r in day_results]

    # ── Transaction list from invoices ─────────────────────────────────────
    inv_query = {
        "order_date": {"$gte": date_from, "$lte": date_to},
        "status": {"$ne": "voided"},
        "sale_type": {"$nin": ["interest_charge", "penalty_charge"]},
    }
    if branch_id:
        inv_query["branch_id"] = branch_id

    invoices = await db.invoices.find(inv_query, {"_id": 0}).sort("order_date", -1).to_list(1000)
    transactions = []
    for inv in invoices:
        transactions.append({
            "invoice_number": inv.get("invoice_number", ""),
            "date": inv.get("order_date", ""),
            "customer_name": inv.get("customer_name", "Walk-in"),
            "payment_type": inv.get("payment_type", "cash"),
            "grand_total": inv.get("grand_total", 0),
            "amount_paid": inv.get("amount_paid", 0),
            "balance": inv.get("balance", 0),
            "status": inv.get("status", ""),
            "items_count": len(inv.get("items", [])),
            "cashier_name": inv.get("cashier_name", ""),
            "sale_type": inv.get("sale_type", "walk_in"),
        })

    return {
        "date_from": date_from,
        "date_to": date_to,
        "grand_total": grand_total,
        "categories": categories,
        "payment_totals": payment_totals,
        "daily": daily,
        "transactions": transactions,
    }


# ==================== EXPENSE REPORT ====================
@router.get("/reports/expenses")
async def expense_report(
    branch_id: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    category: Optional[str] = None,
    user=Depends(get_current_user),
):
    """
    Expense Report: totals by category and per-transaction detail.
    """
    check_perm(user, "reports", "view")

    today = await today_local(user.get("organization_id") or "")
    if not date_from:
        date_from = datetime.now(timezone.utc).strftime("%Y-%m-01")
    if not date_to:
        date_to = today

    query = {"date": {"$gte": date_from, "$lte": date_to}, "voided": {"$ne": True}}
    if branch_id:
        query["branch_id"] = branch_id
    if category:
        query["category"] = category

    # ── Category totals ────────────────────────────────────────────────────
    cat_pipeline = [
        {"$match": query},
        {"$group": {
            "_id": "$category",
            "total": {"$sum": "$amount"},
            "count": {"$sum": 1},
        }},
        {"$sort": {"total": -1}},
    ]
    cat_results = await db.expenses.aggregate(cat_pipeline).to_list(100)
    categories = [
        {"category": r["_id"] or "Miscellaneous", "total": round(r["total"], 2), "count": r["count"]}
        for r in cat_results
    ]
    grand_total = round(sum(c["total"] for c in categories), 2)

    # ── Daily breakdown ────────────────────────────────────────────────────
    day_pipeline = [
        {"$match": query},
        {"$group": {"_id": "$date", "total": {"$sum": "$amount"}, "count": {"$sum": 1}}},
        {"$sort": {"_id": 1}},
    ]
    day_results = await db.expenses.aggregate(day_pipeline).to_list(365)
    daily = [{"date": r["_id"], "total": round(r["total"], 2), "count": r["count"]} for r in day_results]

    # ── Transaction detail ─────────────────────────────────────────────────
    expenses = await db.expenses.find(query, {"_id": 0}).sort("date", -1).to_list(2000)

    return {
        "date_from": date_from,
        "date_to": date_to,
        "grand_total": grand_total,
        "categories": categories,
        "daily": daily,
        "expenses": expenses,
    }



# ==================== DISCOUNT & PRICE OVERRIDE REPORT ====================
@router.get("/reports/discount-audit")
async def discount_audit_report(
    branch_id: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    group_by: Optional[str] = "customer",  # customer | cashier | detail
    include_voided: bool = False,
    user=Depends(get_current_user),
):
    """
    Discount & Price Override audit report.
    Groups by customer or cashier, or returns full detail list.
    """
    check_perm(user, "reports", "view")

    today = await today_local(user.get("organization_id") or "")
    first_of_month = datetime.now(timezone.utc).replace(day=1).strftime("%Y-%m-%d")
    d_from = date_from or first_of_month
    d_to = date_to or today

    query = {"date": {"$gte": d_from, "$lte": d_to}}
    if branch_id:
        query["branch_id"] = branch_id
    # Exclude voided-invoice entries from totals/rankings (Fix #5).
    # Callers that need voided entries for forensic review can pass
    # include_voided=true.
    if not include_voided:
        query["invoice_voided"] = {"$ne": True}

    logs = await db.discount_audit_log.find(query, {"_id": 0}).sort("date", -1).to_list(2000)

    if group_by == "detail":
        # Flatten: one row per item per invoice
        rows = []
        for log in logs:
            for item in log.get("items", []):
                rows.append({
                    "date": log["date"],
                    "invoice_number": log["invoice_number"],
                    "customer_name": log["customer_name"],
                    "customer_id": log.get("customer_id"),
                    "cashier_name": log["cashier_name"],
                    "cashier_id": log["cashier_id"],
                    "product_name": item["product_name"],
                    "original_price": item.get("original_price", 0),
                    "sold_price": item.get("sold_price", 0),
                    "discount_amount": item.get("discount_amount", 0),
                    "quantity": item.get("quantity", 0),
                    "net_per_unit": item.get("net_per_unit", 0),
                    "capital": item.get("capital", 0),
                    "type": item.get("type", ""),
                })
        return {"rows": rows, "total_rows": len(rows)}

    # Group by customer or cashier
    key_field = "customer_name" if group_by == "customer" else "cashier_name"
    id_field = "customer_id" if group_by == "customer" else "cashier_id"
    groups = {}
    for log in logs:
        k = log.get(key_field, "Unknown")
        kid = log.get(id_field)
        if k not in groups:
            groups[k] = {"name": k, "id": kid, "total_discount": 0, "total_price_diff": 0, "transaction_count": 0, "invoices": []}
        groups[k]["total_discount"] += log.get("total_discount", 0)
        groups[k]["total_price_diff"] += log.get("total_price_override_diff", 0)
        groups[k]["transaction_count"] += 1
        groups[k]["invoices"].append({
            "invoice_number": log["invoice_number"],
            "date": log["date"],
            "total_discount": log.get("total_discount", 0),
            "grand_total": log.get("grand_total", 0),
        })

    # Sort by total discount descending
    sorted_groups = sorted(groups.values(), key=lambda g: g["total_discount"], reverse=True)
    for g in sorted_groups:
        g["total_discount"] = round(g["total_discount"], 2)
        g["total_price_diff"] = round(g["total_price_diff"], 2)

    grand_total_discount = round(sum(g["total_discount"] for g in sorted_groups), 2)
    grand_total_price_diff = round(sum(g["total_price_diff"] for g in sorted_groups), 2)

    return {
        "groups": sorted_groups,
        "summary": {
            "total_discount": grand_total_discount,
            "total_price_overrides": grand_total_price_diff,
            "total_transactions": len(logs),
            "period": {"from": d_from, "to": d_to},
        },
    }



# ==================== PRODUCT PROFIT REPORT ====================
@router.get("/reports/product-profit")
async def product_profit_report(
    branch_id: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    user=Depends(get_current_user),
):
    """
    Product Profitability Report: revenue, cost, profit, and margin per product.
    Uses invoice items with their recorded cost_price at time of sale.
    """
    check_perm(user, "reports", "view_profit")

    today = await today_local(user.get("organization_id") or "")
    if not date_from:
        date_from = datetime.now(timezone.utc).strftime("%Y-%m-01")
    if not date_to:
        date_to = today

    inv_query = {
        "order_date": {"$gte": date_from, "$lte": date_to},
        "status": {"$ne": "voided"},
        "sale_type": {"$nin": ["interest_charge", "penalty_charge"]},
    }
    branch_filter = await get_branch_filter(user, branch_id)
    inv_query = apply_branch_filter(inv_query, branch_filter)

    invoices = await db.invoices.find(
        inv_query, {"_id": 0, "items": 1, "invoice_number": 1, "order_date": 1}
    ).to_list(5000)

    # Aggregate per product
    product_map = {}
    for inv in invoices:
        for item in inv.get("items", []):
            pid = item.get("product_id") or item.get("product_name", "unknown")
            qty = float(item.get("quantity", 0))
            revenue = float(item.get("total", 0))
            cost_per_unit = float(item.get("cost_price", 0))
            total_cost = round(cost_per_unit * qty, 2)

            if pid not in product_map:
                product_map[pid] = {
                    "product_id": pid,
                    "product_name": item.get("product_name", "Unknown"),
                    "category": item.get("category", ""),
                    "is_repack": item.get("is_repack", False),
                    "total_qty": 0,
                    "total_revenue": 0.0,
                    "total_cost": 0.0,
                    "transactions": 0,
                    "returned_qty": 0,
                    "returned_revenue": 0.0,
                    "returned_cost": 0.0,
                }

            product_map[pid]["total_qty"] += qty
            product_map[pid]["total_revenue"] = round(product_map[pid]["total_revenue"] + revenue, 2)
            product_map[pid]["total_cost"] = round(product_map[pid]["total_cost"] + total_cost, 2)
            product_map[pid]["transactions"] += 1

    # ── Net out returns (Fix #2) ─────────────────────────────────────────────
    # Return semantics:
    #   - Shelf return: goods back in stock → subtract BOTH revenue and cost
    #     (no net effect on profit; neutralises the original sale portion)
    #   - Pullout return: goods destroyed → subtract revenue only; cost STAYS
    #     because the inventory is gone (real loss, real COGS)
    ret_query = {
        "return_date": {"$gte": date_from, "$lte": date_to},
        "voided": {"$ne": True},
    }
    ret_query = apply_branch_filter(ret_query, branch_filter)
    returns = await db.returns.find(
        ret_query, {"_id": 0, "items": 1, "rma_number": 1, "return_date": 1}
    ).to_list(2000)
    for ret in returns:
        for item in ret.get("items", []):
            pid = item.get("product_id") or item.get("product_name", "unknown")
            qty = float(item.get("quantity", 0))
            refund_price = float(item.get("refund_price", 0))
            cost_per_unit = float(item.get("cost_price", 0))
            inventory_action = item.get("inventory_action", "shelf")
            rev_back = round(refund_price * qty, 2)
            cost_back = round(cost_per_unit * qty, 2)
            if pid not in product_map:
                product_map[pid] = {
                    "product_id": pid,
                    "product_name": item.get("product_name", "Unknown"),
                    "category": item.get("category", ""),
                    "is_repack": item.get("is_repack", False),
                    "total_qty": 0,
                    "total_revenue": 0.0,
                    "total_cost": 0.0,
                    "transactions": 0,
                    "returned_qty": 0,
                    "returned_revenue": 0.0,
                    "returned_cost": 0.0,
                }
            product_map[pid]["total_qty"] -= qty
            product_map[pid]["total_revenue"] = round(product_map[pid]["total_revenue"] - rev_back, 2)
            product_map[pid]["returned_qty"] += qty
            product_map[pid]["returned_revenue"] = round(product_map[pid]["returned_revenue"] + rev_back, 2)
            if inventory_action == "shelf":
                # Goods back on shelf — cost reversed
                product_map[pid]["total_cost"] = round(product_map[pid]["total_cost"] - cost_back, 2)
            else:
                # Pullout: cost stays (real COGS loss), captured as returned_cost
                product_map[pid]["returned_cost"] = round(product_map[pid]["returned_cost"] + cost_back, 2)

    # Calculate profit and margin
    rows = []
    grand_revenue = 0.0
    grand_cost = 0.0
    grand_profit = 0.0

    for p in product_map.values():
        profit = round(p["total_revenue"] - p["total_cost"], 2)
        margin = round((profit / p["total_revenue"]) * 100, 2) if p["total_revenue"] > 0 else 0.0
        rows.append({
            **p,
            "profit": profit,
            "margin_pct": margin,
        })
        grand_revenue += p["total_revenue"]
        grand_cost += p["total_cost"]
        grand_profit += profit

    # Sort by profit desc
    rows.sort(key=lambda x: x["profit"], reverse=True)

    grand_margin = round((grand_profit / grand_revenue) * 100, 2) if grand_revenue > 0 else 0.0

    return {
        "date_from": date_from,
        "date_to": date_to,
        "rows": rows,
        "summary": {
            "total_revenue": round(grand_revenue, 2),
            "total_cost": round(grand_cost, 2),
            "total_profit": round(grand_profit, 2),
            "overall_margin_pct": grand_margin,
            "product_count": len(rows),
            "loss_making_count": sum(1 for r in rows if r["profit"] < 0),
        },
    }

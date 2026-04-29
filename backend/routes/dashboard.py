"""
Dashboard routes: statistics, summaries, multi-branch overview.
Supports multi-branch data isolation with owner cross-branch views.
"""
from fastapi import APIRouter, Depends
from typing import Optional
from datetime import datetime, timezone, timedelta
from config import db
from utils import (
    get_current_user, get_branch_filter, apply_branch_filter,
    get_user_branches, get_default_branch
)

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


async def _compute_inventory_value(branch_id: str) -> dict:
    """
    Calculate stock value for a branch:
      - capital_value  = sum(qty × cost_price)   [non-repack only — repacks are
                          derived from parent stock so they'd double-count]
      - retail_value   = sum(qty × retail_price)
      - potential_margin = retail_value - capital_value

    Branch-aware: branch_prices.cost_price + prices override product globals.
    """
    pipeline = [
        {"$match": {"branch_id": branch_id}},
        {"$lookup": {
            "from": "products",
            "localField": "product_id",
            "foreignField": "id",
            "as": "product"
        }},
        {"$unwind": "$product"},
        {"$match": {
            "product.active": True,
            "product.is_repack": {"$ne": True},
            "quantity": {"$gt": 0}
        }},
        {"$project": {
            "_id": 0,
            "product_id": "$product.id",
            "quantity": 1,
            "cost_price": "$product.cost_price",
            "prices": "$product.prices",
        }}
    ]
    items = await db.inventory.aggregate(pipeline).to_list(10000)

    # Branch overrides — single bulk fetch
    pids = [i["product_id"] for i in items]
    bp_docs = await db.branch_prices.find(
        {"branch_id": branch_id, "product_id": {"$in": pids}}, {"_id": 0}
    ).to_list(10000)
    bp_map = {d["product_id"]: d for d in bp_docs}

    capital_value = 0.0
    retail_value = 0.0
    sku_count = len(items)

    for item in items:
        qty = float(item.get("quantity", 0))
        bp = bp_map.get(item["product_id"]) or {}
        # Branch-aware cost
        cost = float(bp["cost_price"]) if bp.get("cost_price") is not None else float(item.get("cost_price", 0))
        capital_value += qty * cost

        # Merge global prices with branch override
        prices = {**(item.get("prices") or {}), **(bp.get("prices") or {})}
        retail = next(
            (float(v) for k, v in prices.items() if k.lower() == "retail"),
            cost
        )
        retail_value += qty * retail

    return {
        "capital_value": round(capital_value, 2),
        "retail_value": round(retail_value, 2),
        "potential_margin": round(retail_value - capital_value, 2),
        "margin_pct": round((retail_value - capital_value) / capital_value * 100, 1) if capital_value > 0 else 0,
        "sku_count_in_stock": sku_count,
    }


async def _compute_ar_aging(branch_filter):
    """Compute AR aging buckets across all outstanding invoices."""
    today = datetime.now(timezone.utc).date()
    match = {"status": {"$nin": ["paid", "voided"]}, "balance": {"$gt": 0}}
    match = apply_branch_filter(match, branch_filter)
    invoices = await db.invoices.find(match, {"_id": 0, "balance": 1, "order_date": 1, "customer_name": 1}).to_list(5000)
    buckets = {"current": 0, "days_31_60": 0, "days_61_90": 0, "over_90": 0, "total": 0}
    for inv in invoices:
        bal = float(inv.get("balance", 0))
        buckets["total"] = round(buckets["total"] + bal, 2)
        try:
            age = (today - datetime.strptime(inv["order_date"], "%Y-%m-%d").date()).days
        except Exception:
            age = 0
        if age <= 30:
            buckets["current"] = round(buckets["current"] + bal, 2)
        elif age <= 60:
            buckets["days_31_60"] = round(buckets["days_31_60"] + bal, 2)
        elif age <= 90:
            buckets["days_61_90"] = round(buckets["days_61_90"] + bal, 2)
        else:
            buckets["over_90"] = round(buckets["over_90"] + bal, 2)
    return buckets


async def _get_top_debtors(branch_filter, limit=5):
    """Top customers by outstanding AR balance."""
    match = {"status": {"$nin": ["paid", "voided"]}, "balance": {"$gt": 0}}
    match = apply_branch_filter(match, branch_filter)
    pipeline = [
        {"$match": match},
        {"$group": {"_id": "$customer_name", "customer_id": {"$first": "$customer_id"},
                    "total_balance": {"$sum": "$balance"}, "invoice_count": {"$sum": 1}}},
        {"$sort": {"total_balance": -1}},
        {"$limit": limit},
    ]
    rows = await db.invoices.aggregate(pipeline).to_list(limit)
    return [{"customer": r["_id"], "balance": round(r["total_balance"], 2), "invoices": r["invoice_count"]} for r in rows]


@router.get("/stats")
async def dashboard_stats(
    user=Depends(get_current_user),
    branch_id: Optional[str] = None
):
    """
    Get comprehensive dashboard statistics. Respects branch isolation.
    Returns KPIs, cash position, AR aging, today's credits, last close date.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    now_dt = datetime.now(timezone.utc)

    branch_filter = await get_branch_filter(user, branch_id)
    effective_branch_id = branch_id or (user.get("branch_id") if user.get("role") != "admin" else None)

    # ── Today's invoices ─────────────────────────────────────────────────────
    invoice_query = {"status": {"$ne": "voided"}, "order_date": today}
    invoice_query = apply_branch_filter(invoice_query, branch_filter)
    today_invoices = await db.invoices.find(invoice_query, {"_id": 0}).to_list(10000)
    today_revenue = round(sum(inv.get("grand_total", 0) for inv in today_invoices), 2)
    today_count = len(today_invoices)
    today_cash_sales = round(sum(
        inv.get("grand_total", 0) for inv in today_invoices
        if inv.get("payment_type") == "cash"
    ), 2)
    today_credit_sales = round(sum(
        inv.get("grand_total", 0) for inv in today_invoices
        if inv.get("payment_type") in ("credit", "partial")
    ), 2)
    today_digital_sales = round(sum(
        inv.get("grand_total", 0) for inv in today_invoices
        if inv.get("payment_type") == "digital"
    ), 2)
    # Split payments: cash portion → cash, digital portion → digital
    for inv in today_invoices:
        if inv.get("payment_type") == "split":
            today_cash_sales = round(today_cash_sales + float(inv.get("cash_amount", 0)), 2)
            today_digital_sales = round(today_digital_sales + float(inv.get("digital_amount", 0)), 2)

    # Customers who got credit today
    credit_customers_today = [
        {"customer_name": inv.get("customer_name", "Walk-in"),
         "invoice_number": inv.get("invoice_number", ""),
         "amount": round(float(inv.get("grand_total", 0)), 2),
         "balance": round(float(inv.get("balance", inv.get("grand_total", 0))), 2),
         "type": inv.get("sale_type", "credit"),
         "created_at": inv.get("created_at", "")[:16]}
        for inv in today_invoices if inv.get("payment_type") in ("credit", "partial")
    ]

    # ── AR collected today (payments on older invoices) ───────────────────────
    ar_pay_pipeline = [
        {"$match": apply_branch_filter({"status": {"$ne": "voided"}, "order_date": {"$ne": today}}, branch_filter)},
        {"$unwind": "$payments"},
        {"$match": {"payments.date": today}},
        {"$group": {"_id": None, "total": {"$sum": "$payments.amount"},
                    "payments": {"$push": {
                        "customer_name": "$customer_name",
                        "invoice_number": "$invoice_number",
                        "amount": "$payments.amount",
                        "date": "$payments.date"
                    }}}}
    ]
    ar_pay_result = await db.invoices.aggregate(ar_pay_pipeline).to_list(1)
    today_ar_collected = round(ar_pay_result[0]["total"] if ar_pay_result else 0, 2)
    recent_ar_payments = (ar_pay_result[0].get("payments", []) if ar_pay_result else [])[:5]

    # ── Today's expenses ──────────────────────────────────────────────────────
    expense_query = {"date": today}
    expense_query = apply_branch_filter(expense_query, branch_filter)
    today_expenses_list = await db.expenses.find(expense_query, {"_id": 0}).to_list(1000)
    today_expense_total = round(sum(e.get("amount", 0) for e in today_expenses_list), 2)

    # ── Cash position ─────────────────────────────────────────────────────────
    cashier_balance = 0.0
    safe_balance = 0.0
    if effective_branch_id:
        wallet = await db.fund_wallets.find_one(
            {"branch_id": effective_branch_id, "type": "cashier", "active": True}, {"_id": 0}
        )
        cashier_balance = float(wallet["balance"]) if wallet else 0.0
        safe_w = await db.fund_wallets.find_one(
            {"branch_id": effective_branch_id, "type": "safe", "active": True}, {"_id": 0}
        )
        if safe_w:
            lots = await db.safe_lots.find(
                {"wallet_id": safe_w["id"], "remaining_amount": {"$gt": 0}}, {"_id": 0}
            ).to_list(500)
            safe_balance = sum(lot.get("remaining_amount", 0) for lot in lots)

    # ── AR aging ──────────────────────────────────────────────────────────────
    ar_aging = await _compute_ar_aging(branch_filter)
    top_debtors = await _get_top_debtors(branch_filter)

    # ── Inventory value ───────────────────────────────────────────────────────
    inventory_value = None
    if effective_branch_id:
        inventory_value = await _compute_inventory_value(effective_branch_id)

    # ── Last close date ───────────────────────────────────────────────────────
    close_query = {"status": "closed"}
    if effective_branch_id:
        close_query["branch_id"] = effective_branch_id
    last_close = await db.daily_closings.find_one(close_query, {"_id": 0, "date": 1}, sort=[("date", -1)])
    last_close_date = last_close["date"] if last_close else None
    days_since_close = None
    if last_close_date:
        try:
            days_since_close = (datetime.now(timezone.utc).date() -
                                datetime.strptime(last_close_date, "%Y-%m-%d").date()).days
        except Exception:
            pass

    # ── Total products / low stock ────────────────────────────────────────────
    total_products = await db.products.count_documents({"active": True})
    low_stock_pipeline = [
        {"$match": {"active": True}},
        {"$lookup": {
            "from": "inventory",
            "let": {"pid": "$id"},
            "pipeline": [
                {"$match": {"$expr": {"$eq": ["$product_id", "$$pid"]}}},
                *([{"$match": branch_filter}] if branch_filter else [])
            ],
            "as": "inv"
        }},
        {"$addFields": {"total_stock": {"$sum": "$inv.quantity"}}},
        {"$match": {"total_stock": {"$lte": 10}}},
        {"$count": "total"}
    ]
    low_stock_result = await db.products.aggregate(low_stock_pipeline).to_list(1)
    low_stock_count = low_stock_result[0]["total"] if low_stock_result else 0

    # ── Total customers ───────────────────────────────────────────────────────
    customer_query = {"active": True}
    if branch_filter:
        customer_query = apply_branch_filter(customer_query, branch_filter)
    total_customers = await db.customers.count_documents(customer_query)

    # ── Recent sales (invoices) ───────────────────────────────────────────────
    recent_invoice_query = {"status": {"$ne": "voided"}}
    recent_invoice_query = apply_branch_filter(recent_invoice_query, branch_filter)
    recent_invoices = await db.invoices.find(
        recent_invoice_query, {"_id": 0}
    ).sort("created_at", -1).limit(8).to_list(8)
    for inv in recent_invoices:
        inv["sale_number"] = inv.get("invoice_number", "")
        inv["total"] = inv.get("grand_total", 0)

    # ── Top products ──────────────────────────────────────────────────────────
    top_inv_pipeline = [
        {"$match": apply_branch_filter({"status": {"$ne": "voided"}}, branch_filter)},
        {"$unwind": "$items"},
        {"$group": {"_id": "$items.product_name",
                    "total_qty": {"$sum": "$items.quantity"},
                    "total_revenue": {"$sum": "$items.total"}}},
        {"$sort": {"total_revenue": -1}},
        {"$limit": 5}
    ]
    top_products_data = await db.invoices.aggregate(top_inv_pipeline).to_list(5)
    top_products = [
        {"name": p["_id"], "quantity": p["total_qty"], "revenue": round(p["total_revenue"], 2)}
        for p in top_products_data if p["_id"]
    ]

    # ── Net cash flow today ───────────────────────────────────────────────────
    # Cash inflow = cash sales + digital sales + AR collected (actual money received)
    # Credit sales are receivables, not cash — exclude from cash flow
    today_cash_inflow = round(today_cash_sales + today_digital_sales + today_ar_collected, 2)
    today_net_cash = round(today_cash_inflow - today_expense_total, 2)

    # ── Branches ─────────────────────────────────────────────────────────────
    user_branches = await get_user_branches(user)
    branches = await db.branches.find(
        {"active": True, "id": {"$in": user_branches}} if user_branches else {"active": True},
        {"_id": 0}
    ).to_list(100)

    # ── Last audit session ────────────────────────────────────────────────────
    audit_query = {"status": "completed"}
    if effective_branch_id:
        audit_query["branch_id"] = effective_branch_id
    last_audit = await db.audits.find_one(
        audit_query, {"_id": 0, "overall_score": 1, "created_at": 1, "audit_type": 1,
                      "sections_status": 1, "period_from": 1, "period_to": 1},
        sort=[("created_at", -1)]
    )
    days_since_audit = None
    if last_audit:
        try:
            days_since_audit = (datetime.now(timezone.utc).date() -
                                datetime.strptime(last_audit["created_at"][:10], "%Y-%m-%d").date()).days
        except Exception:
            pass

    # ── Price issue count (retail/wholesale below cost) ───────────────────────
    price_issue_count = 0
    if effective_branch_id:
        bp_list = await db.branch_prices.find(
            {"branch_id": effective_branch_id}, {"_id": 0, "product_id": 1, "cost_price": 1, "prices": 1}
        ).to_list(5000)
        bp_map = {d["product_id"]: d for d in bp_list}
        prods_for_scan = await db.products.find(
            {"active": True, "is_repack": {"$ne": True}},
            {"_id": 0, "id": 1, "cost_price": 1, "prices": 1}
        ).to_list(5000)
        for p in prods_for_scan:
            bp = bp_map.get(p["id"]) or {}
            # Branch-aware cost
            cost = float(bp["cost_price"]) if bp.get("cost_price") is not None else float(p.get("cost_price", 0))
            if cost <= 0:
                continue
            prices = {k: float(v or 0) for k, v in (p.get("prices") or {}).items()}
            if bp.get("prices"):
                prices.update({k: float(v or 0) for k, v in bp["prices"].items()})
            for key in ("retail", "wholesale"):
                val = prices.get(key, 0)
                if 0 < val < cost:
                    price_issue_count += 1
                    break

    return {
        # Meta
        "today": today,
        "day_of_week": now_dt.strftime("%A"),
        "last_close_date": last_close_date,
        "days_since_close": days_since_close,
        # Audit
        "last_audit": last_audit,
        "days_since_audit": days_since_audit,
        "price_issue_count": price_issue_count,
        # Sales
        "today_revenue": today_revenue,
        "today_sales_count": today_count,
        "today_cash_sales": today_cash_sales,
        "today_credit_sales": today_credit_sales,
        "today_digital_sales": today_digital_sales,
        "today_ar_collected": today_ar_collected,
        "today_cash_inflow": today_cash_inflow,
        "today_net_cash": today_net_cash,
        "today_expenses": today_expense_total,
        # Cash position
        "cashier_balance": round(cashier_balance, 2),
        "safe_balance": round(safe_balance, 2),
        "total_cash_position": round(cashier_balance + safe_balance, 2),
        # AR
        "total_receivables": ar_aging["total"],
        "ar_aging": ar_aging,
        "top_debtors": top_debtors,
        "credit_customers_today": credit_customers_today,
        "recent_ar_payments": recent_ar_payments,
        # Inventory
        "total_products": total_products,
        "low_stock_count": low_stock_count,
        "total_customers": total_customers,
        "inventory_value": inventory_value,
        # Lists
        "recent_sales": recent_invoices,
        "top_products": top_products,
        "branches": branches,
        "current_branch_filter": branch_id or "all",
        "is_multi_branch_view": user.get("role") == "admin" and not branch_id,
    }


@router.get("/branch-summary")
async def branch_summary(user=Depends(get_current_user)):
    """
    Get summary for all branches the user can access.
    Useful for owner/admin dashboard showing all branches at once.
    """
    user_branches = await get_user_branches(user)
    if not user_branches:
        return {"branches": [], "totals": {}}
    
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    summaries = []
    
    for branch_id in user_branches:
        branch = await db.branches.find_one({"id": branch_id}, {"_id": 0})
        if not branch:
            continue
        
        # Today's revenue for this branch (by order_date)
        today_invoices = await db.invoices.find(
            {"branch_id": branch_id, "status": {"$ne": "voided"}, "order_date": today},
            {"_id": 0, "grand_total": 1, "payment_type": 1, "cash_amount": 1, "digital_amount": 1}
        ).to_list(10000)
        today_revenue = round(sum(inv.get("grand_total", 0) for inv in today_invoices), 2)
        today_sales_count = len(today_invoices)
        today_cash_sales = round(sum(inv.get("grand_total", 0) for inv in today_invoices if inv.get("payment_type") == "cash"), 2)
        today_new_credit = round(sum(inv.get("grand_total", 0) for inv in today_invoices if inv.get("payment_type") in ("credit", "partial")), 2)
        today_digital_sales = round(sum(inv.get("grand_total", 0) for inv in today_invoices if inv.get("payment_type") == "digital"), 2)
        # Split payments: cash portion → cash, digital portion → digital
        for inv in today_invoices:
            if inv.get("payment_type") == "split":
                today_cash_sales = round(today_cash_sales + float(inv.get("cash_amount", 0)), 2)
                today_digital_sales = round(today_digital_sales + float(inv.get("digital_amount", 0)), 2)

        # AR collected today (payments on older invoices)
        ar_today = await db.invoices.aggregate([
            {"$match": {"branch_id": branch_id, "status": {"$ne": "voided"}, "order_date": {"$ne": today}}},
            {"$unwind": "$payments"},
            {"$match": {"payments.date": today}},
            {"$group": {"_id": None, "total": {"$sum": "$payments.amount"}}}
        ]).to_list(1)
        ar_collected_today = round(ar_today[0]["total"] if ar_today else 0, 2)
        
        # Outstanding receivables for this branch
        rec_result = await db.invoices.aggregate([
            {"$match": {"branch_id": branch_id, "status": {"$nin": ["paid", "voided"]}, "balance": {"$gt": 0}}},
            {"$group": {"_id": None, "total": {"$sum": "$balance"}}}
        ]).to_list(1)
        receivables = rec_result[0]["total"] if rec_result else 0
        
        # Today's expenses
        exp_result = await db.expenses.aggregate([
            {"$match": {"branch_id": branch_id, "date": today}},
            {"$group": {"_id": None, "total": {"$sum": "$amount"}}}
        ]).to_list(1)
        expenses = exp_result[0]["total"] if exp_result else 0
        
        # Cashier wallet balance
        wallet = await db.fund_wallets.find_one(
            {"branch_id": branch_id, "type": "cashier", "active": True},
            {"_id": 0, "balance": 1}
        )
        cashier_balance = wallet.get("balance", 0) if wallet else 0
        
        # Safe balance
        safe = await db.fund_wallets.find_one(
            {"branch_id": branch_id, "type": "safe", "active": True},
            {"_id": 0, "id": 1}
        )
        safe_balance = 0
        if safe:
            lots = await db.safe_lots.find(
                {"wallet_id": safe["id"], "remaining_amount": {"$gt": 0}},
                {"_id": 0, "remaining_amount": 1}
            ).to_list(500)
            safe_balance = sum(lot.get("remaining_amount", 0) for lot in lots)

        # Low stock count for this branch
        low_stock_pipeline = [
            {"$match": {"active": True, "is_repack": {"$ne": True}}},
            {"$lookup": {
                "from": "inventory",
                "let": {"product_id": "$id"},
                "pipeline": [
                    {"$match": {
                        "$expr": {"$eq": ["$product_id", "$$product_id"]},
                        "branch_id": branch_id
                    }}
                ],
                "as": "inv"
            }},
            {"$addFields": {"stock": {"$ifNull": [{"$arrayElemAt": ["$inv.quantity", 0]}, 0]}}},
            {"$match": {"$and": [{"stock": {"$lte": 10}}, {"stock": {"$gte": 0}}]}},
            {"$count": "total"}
        ]
        low_stock_result = await db.products.aggregate(low_stock_pipeline).to_list(1)
        low_stock_count = low_stock_result[0]["total"] if low_stock_result else 0
        
        # Last close date for this branch
        last_close = await db.daily_closings.find_one(
            {"branch_id": branch_id, "status": "closed"}, {"_id": 0, "date": 1},
            sort=[("date", -1)]
        )
        last_close_date = last_close["date"] if last_close else None

        # Inventory value for this branch
        inv_val = await _compute_inventory_value(branch_id)

        # Determine status
        status = "good"
        if low_stock_count > 10:
            status = "warning"
        if low_stock_count > 20:
            status = "critical"

        summaries.append({
            "id": branch_id,
            "name": branch.get("name", ""),
            "today_revenue": today_revenue,
            "today_sales_count": today_sales_count,
            "today_cash_sales": today_cash_sales,
            "today_new_credit": today_new_credit,
            "today_digital_sales": today_digital_sales,
            "ar_collected_today": ar_collected_today,
            "receivables": round(receivables, 2),
            "today_expenses": round(expenses, 2),
            "today_cash_inflow": round(today_cash_sales + today_digital_sales + ar_collected_today, 2),
            "net_today": round(today_cash_sales + today_digital_sales + ar_collected_today - expenses, 2),
            "cashier_balance": round(cashier_balance, 2),
            "safe_balance": round(safe_balance, 2),
            "total_cash": round(cashier_balance + safe_balance, 2),
            "low_stock_count": low_stock_count,
            "last_close_date": last_close_date,
            "inventory_value": inv_val,
            "status": status,
        })
    total_revenue = sum(s["today_revenue"] for s in summaries)
    total_receivables = sum(s["receivables"] for s in summaries)
    total_expenses = sum(s["today_expenses"] for s in summaries)
    total_cash = sum(s["total_cash"] for s in summaries)
    total_low_stock = sum(s["low_stock_count"] for s in summaries)
    
    return {
        "branches": summaries,
        "totals": {
            "today_revenue": total_revenue,
            "total_receivables": total_receivables,
            "today_expenses": total_expenses,
            "net_today": sum(s["net_today"] for s in summaries),
            "total_cash": total_cash,
            "low_stock_count": total_low_stock,
            "branch_count": len(summaries),
        }
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Pending Reviews endpoint
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/pending-reviews")
async def get_pending_reviews(
    user=Depends(get_current_user),
    branch_id: Optional[str] = None,
):
    """
    Returns records that have upload sessions but haven't been reviewed yet.
    Covers: purchase_orders, branch_transfers, expenses.
    Admin/owner: all branches (grouped by branch). Branch user: own branch only.
    """
    is_admin = user.get("role") in ("admin", "owner", "manager")
    user_branch_ids = await get_user_branches(user)

    # Determine which branches to query
    if branch_id and not is_admin:
        target_branches = [branch_id] if branch_id in user_branch_ids else []
    elif branch_id:
        target_branches = [branch_id]
    elif is_admin:
        target_branches = user_branch_ids
    else:
        # Regular user: their assigned branch
        default = await get_default_branch(user)
        target_branches = [default] if default else user_branch_ids[:1]

    if not target_branches:
        return {"items": [], "total_count": 0, "by_branch": {}}

    # Build branch name lookup
    all_branches = await db.branches.find(
        {"id": {"$in": target_branches}}, {"_id": 0, "id": 1, "name": 1}
    ).to_list(100)
    branch_names = {b["id"]: b["name"] for b in all_branches}

    items = []

    # ── POs with receipts not reviewed ────────────────────────────────────
    # Only show POs that have been received/processed — not drafts or pending orders
    po_query = {
        "branch_id": {"$in": target_branches},
        "status": {"$in": ["received", "partially_fulfilled", "fulfilled", "in_progress", "sent_to_terminal"]},
        "receipt_review_status": {"$ne": "reviewed"},
    }
    pos = await db.purchase_orders.find(po_query, {"_id": 0}).sort("created_at", -1).to_list(200)
    # Filter: only POs that have at least one upload session
    for po in pos:
        sessions = await db.upload_sessions.find(
            {"record_type": "purchase_order", "record_id": po["id"], "is_pending": {"$ne": True}},
            {"_id": 0, "file_count": 1, "created_at": 1, "created_by_name": 1}
        ).to_list(20)
        total_files = sum(s.get("file_count", 0) for s in sessions)
        if total_files > 0:
            items.append({
                "id": po["id"],
                "record_type": "purchase_order",
                "record_number": po.get("po_number", ""),
                "description": f"{po.get('vendor', 'Unknown Supplier')} - {po.get('item_count', len(po.get('items', [])))} items",
                "amount": po.get("grand_total", 0),
                "branch_id": po.get("branch_id", ""),
                "branch_name": branch_names.get(po.get("branch_id", ""), ""),
                "receipt_count": total_files,
                "submitted_by": po.get("created_by_name", ""),
                "submitted_at": po.get("created_at", ""),
                "status": po.get("status", ""),
            })

    # ── Branch transfers with receipts not reviewed ───────────────────────
    bt_query = {
        "$or": [
            {"from_branch_id": {"$in": target_branches}},
            {"to_branch_id": {"$in": target_branches}},
        ],
        "status": {"$in": ["received", "completed", "received_pending"]},
        "receipt_review_status": {"$ne": "reviewed"},
    }
    bts = await db.branch_transfer_orders.find(bt_query, {"_id": 0}).sort("created_at", -1).to_list(200)
    for bt in bts:
        sessions = await db.upload_sessions.find(
            {"record_type": "branch_transfer", "record_id": bt["id"], "is_pending": {"$ne": True}},
            {"_id": 0, "file_count": 1}
        ).to_list(20)
        total_files = sum(s.get("file_count", 0) for s in sessions)
        if total_files > 0:
            from_name = branch_names.get(bt.get("from_branch_id", ""), bt.get("from_branch_id", ""))
            to_name = branch_names.get(bt.get("to_branch_id", ""), bt.get("to_branch_id", ""))
            items.append({
                "id": bt["id"],
                "record_type": "branch_transfer",
                "record_number": bt.get("order_number", ""),
                "description": f"{from_name} → {to_name}",
                "amount": bt.get("total_capital", 0),
                "branch_id": bt.get("to_branch_id", bt.get("from_branch_id", "")),
                "branch_name": to_name,
                "receipt_count": total_files,
                "submitted_by": bt.get("received_by_name", bt.get("created_by_name", "")),
                "submitted_at": bt.get("received_at", bt.get("created_at", "")),
                "status": bt.get("status", ""),
            })

    # ── Expenses with receipts not reviewed ───────────────────────────────
    exp_query = {
        "branch_id": {"$in": target_branches},
        "receipt_review_status": {"$ne": "reviewed"},
    }
    exps = await db.expenses.find(exp_query, {"_id": 0}).sort("created_at", -1).to_list(200)
    for exp in exps:
        sessions = await db.upload_sessions.find(
            {"record_type": "expense", "record_id": exp["id"], "is_pending": {"$ne": True}},
            {"_id": 0, "file_count": 1}
        ).to_list(20)
        total_files = sum(s.get("file_count", 0) for s in sessions)
        if total_files > 0:
            items.append({
                "id": exp["id"],
                "record_type": "expense",
                "record_number": exp.get("reference_number", exp.get("id", "")[:8]),
                "description": f"{exp.get('category', '')} - {exp.get('description', '')}",
                "amount": exp.get("amount", 0),
                "branch_id": exp.get("branch_id", ""),
                "branch_name": branch_names.get(exp.get("branch_id", ""), ""),
                "receipt_count": total_files,
                "submitted_by": exp.get("created_by_name", ""),
                "submitted_at": exp.get("created_at", ""),
                "status": "recorded",
            })

    # Sort by submitted_at descending
    items.sort(key=lambda x: x.get("submitted_at", ""), reverse=True)

    # Group by branch for admin view
    by_branch = {}
    for item in items:
        bid = item["branch_id"]
        bname = item["branch_name"] or bid
        if bname not in by_branch:
            by_branch[bname] = {"branch_id": bid, "branch_name": bname, "count": 0, "items": []}
        by_branch[bname]["count"] += 1
        by_branch[bname]["items"].append(item)

    return {
        "items": items,
        "total_count": len(items),
        "by_branch": by_branch,
    }



# ── Sales Analytics ───────────────────────────────────────────────────────────

PERIOD_MAP = {
    "this_month": lambda now: (now.replace(day=1).strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d")),
    "last_month": lambda now: (
        (now.replace(day=1) - timedelta(days=1)).replace(day=1).strftime("%Y-%m-%d"),
        (now.replace(day=1) - timedelta(days=1)).strftime("%Y-%m-%d"),
    ),
    "this_quarter": lambda now: (
        datetime(now.year, ((now.month - 1) // 3) * 3 + 1, 1).strftime("%Y-%m-%d"),
        now.strftime("%Y-%m-%d"),
    ),
    "last_quarter": lambda now: (
        datetime(now.year if now.month > 3 else now.year - 1,
                 ((now.month - 1) // 3) * 3 - 2 if now.month > 3 else 10, 1).strftime("%Y-%m-%d"),
        (datetime(now.year, ((now.month - 1) // 3) * 3 + 1, 1) - timedelta(days=1)).strftime("%Y-%m-%d"),
    ),
    "this_year": lambda now: (f"{now.year}-01-01", now.strftime("%Y-%m-%d")),
    "last_year": lambda now: (f"{now.year - 1}-01-01", f"{now.year - 1}-12-31"),
}


@router.get("/sales-analytics")
async def sales_analytics(
    period: str = "this_month",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    branch_id: Optional[str] = None,
    user=Depends(get_current_user),
):
    """
    Aggregated sales analytics for charts.
    Returns daily totals + branch breakdown for the selected period.
    """
    now = datetime.now(timezone.utc)
    if period == "custom" and start_date and end_date:
        d_start, d_end = start_date, end_date
    elif period in PERIOD_MAP:
        d_start, d_end = PERIOD_MAP[period](now)
    else:
        d_start = now.replace(day=1).strftime("%Y-%m-%d")
        d_end = now.strftime("%Y-%m-%d")

    branch_filter = await get_branch_filter(user, branch_id)
    match = {"status": {"$ne": "voided"}, "order_date": {"$gte": d_start, "$lte": d_end}}
    match = apply_branch_filter(match, branch_filter)

    # Daily totals
    daily_pipeline = [
        {"$match": match},
        {"$group": {
            "_id": "$order_date",
            "revenue": {"$sum": "$grand_total"},
            "cash": {"$sum": {"$cond": [{"$eq": ["$payment_type", "cash"]}, "$grand_total", 0]}},
            "digital": {"$sum": {"$cond": [{"$eq": ["$payment_type", "digital"]}, "$grand_total", 0]}},
            "credit": {"$sum": {"$cond": [{"$in": ["$payment_type", ["credit", "partial"]]}, "$grand_total", 0]}},
            "split_cash": {"$sum": {"$cond": [{"$eq": ["$payment_type", "split"]}, {"$ifNull": ["$cash_amount", 0]}, 0]}},
            "split_digital": {"$sum": {"$cond": [{"$eq": ["$payment_type", "split"]}, {"$ifNull": ["$digital_amount", 0]}, 0]}},
            "count": {"$sum": 1},
        }},
        {"$sort": {"_id": 1}},
    ]
    daily_raw = await db.invoices.aggregate(daily_pipeline).to_list(400)
    daily = []
    for d in daily_raw:
        daily.append({
            "date": d["_id"],
            "revenue": round(d["revenue"], 2),
            "cash": round(d["cash"] + d["split_cash"], 2),
            "digital": round(d["digital"] + d["split_digital"], 2),
            "credit": round(d["credit"], 2),
            "count": d["count"],
        })

    # Branch breakdown
    branch_pipeline = [
        {"$match": match},
        {"$group": {
            "_id": "$branch_id",
            "revenue": {"$sum": "$grand_total"},
            "count": {"$sum": 1},
        }},
        {"$sort": {"revenue": -1}},
    ]
    branch_raw = await db.invoices.aggregate(branch_pipeline).to_list(100)

    branch_ids = [b["_id"] for b in branch_raw if b["_id"]]
    branch_docs = {}
    if branch_ids:
        for bdoc in await db.branches.find({"id": {"$in": branch_ids}}, {"_id": 0, "id": 1, "name": 1}).to_list(100):
            branch_docs[bdoc["id"]] = bdoc["name"]

    branches = [{
        "branch_id": b["_id"],
        "name": branch_docs.get(b["_id"], b["_id"] or "Unknown"),
        "revenue": round(b["revenue"], 2),
        "count": b["count"],
    } for b in branch_raw if b["_id"]]

    # Summary
    total_revenue = sum(d["revenue"] for d in daily)
    total_count = sum(d["count"] for d in daily)
    total_cash = sum(d["cash"] for d in daily)
    total_digital = sum(d["digital"] for d in daily)
    total_credit = sum(d["credit"] for d in daily)

    return {
        "period": period,
        "start_date": d_start,
        "end_date": d_end,
        "daily": daily,
        "branches": branches,
        "summary": {
            "total_revenue": round(total_revenue, 2),
            "total_transactions": total_count,
            "avg_transaction": round(total_revenue / total_count, 2) if total_count else 0,
            "total_cash": round(total_cash, 2),
            "total_digital": round(total_digital, 2),
            "total_credit": round(total_credit, 2),
            "days_with_sales": len([d for d in daily if d["revenue"] > 0]),
        },
    }


@router.get("/accounts-payable")
async def accounts_payable_summary(
    branch_id: Optional[str] = None,
    user=Depends(get_current_user),
):
    """Accounts payable summary — all unpaid supplier POs with outstanding balance."""
    branch_filter = await get_branch_filter(user, branch_id)
    # Include all supplier PO types (terms, cash, etc.) — exclude only internal branch_requests and inactive records
    match = {
        "payment_status": {"$nin": ["paid", "voided"]},
        "status": {"$nin": ["cancelled", "voided", "draft"]},
        "po_type": {"$nin": ["branch_request"]},
    }
    match = apply_branch_filter(match, branch_filter)

    pos = await db.purchase_orders.find(match, {
        "_id": 0, "id": 1, "po_number": 1, "vendor": 1, "balance": 1,
        "due_date": 1, "branch_id": 1, "created_at": 1,
        "receipt_review_status": 1, "grand_total": 1, "total_paid": 1,
    }).to_list(500)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    overdue = []
    due_this_week = []
    upcoming = []
    total = 0

    for po in pos:
        bal = float(po.get("balance", 0))
        # Fallback: compute balance from grand_total - total_paid if balance not set
        if bal <= 0 and po.get("grand_total"):
            bal = float(po.get("grand_total", 0)) - float(po.get("total_paid", 0))
        if bal <= 0:
            continue
        total += bal
        due = po.get("due_date", "")
        days_left = (datetime.strptime(due, "%Y-%m-%d") - datetime.strptime(today, "%Y-%m-%d")).days if due else None
        entry = {
            "po_id": po["id"], "po_number": po["po_number"],
            "vendor": po.get("vendor", "Unknown Supplier"), "balance": round(bal, 2),
            "due_date": due, "days_left": days_left,
            "branch_id": po.get("branch_id"),
            "receipt_review_status": po.get("receipt_review_status", ""),
        }
        if days_left is not None and days_left < 0:
            overdue.append(entry)
        elif days_left is not None and days_left <= 7:
            due_this_week.append(entry)
        else:
            upcoming.append(entry)

    return {
        "total_payable": round(total, 2),
        "overdue_total": round(sum(e["balance"] for e in overdue), 2),
        "overdue_count": len(overdue),
        "due_this_week_total": round(sum(e["balance"] for e in due_this_week), 2),
        "due_this_week": due_this_week,
        "overdue": overdue,
        "upcoming_count": len(upcoming),
        "upcoming_total": round(sum(e["balance"] for e in upcoming), 2),
    }


@router.get("/review-detail/{record_type}/{record_id}")
async def get_review_detail(record_type: str, record_id: str, user=Depends(get_current_user)):
    """
    Fetch full record detail for the review panel on the dashboard.
    Returns enriched data including items, supplier, dates, payment status, receipt files.
    Supports: purchase_order, branch_transfer, expense
    """
    from utils import now_iso

    result = {"record_type": record_type, "record_id": record_id}

    if record_type == "purchase_order":
        po = await db.purchase_orders.find_one({"id": record_id}, {"_id": 0})
        if not po:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Purchase order not found")

        # Resolve branch name
        branch_id_po = po.get("branch_id", "")
        branch = await db.branches.find_one({"id": branch_id_po}, {"_id": 0, "name": 1})

        # ── Use stored financial fields directly — handles both old (subtotal) and new (grand_total) schemas ──
        # Some older POs use 'subtotal', newer ones use 'grand_total'. The pay endpoint always
        # keeps 'balance' and 'amount_paid' fields accurate after each payment.
        grand_total = float(po.get("grand_total") or po.get("subtotal", 0))
        total_paid = float(po.get("amount_paid", 0))
        balance = float(po.get("balance", 0))
        # If balance not stored, calculate from grand_total and amount_paid
        if balance == 0 and grand_total > 0 and total_paid < grand_total:
            balance = round(grand_total - total_paid, 2)
        payment_status = po.get("payment_status") or ("paid" if balance <= 0 else "partial" if total_paid > 0 else "unpaid")
        # Payment history: newer POs use payment_history[], older POs may use payments[]
        payment_history = po.get("payment_history", po.get("payments", []))

        # ── Fetch wallet balances for the Pay Now panel ──
        wallet_balances = {}
        if branch_id_po:
            wallets = await db.fund_wallets.find(
                {"branch_id": branch_id_po, "active": True},
                {"_id": 0, "type": 1, "name": 1, "balance": 1, "id": 1}
            ).to_list(10)
            for w in wallets:
                wtype = w["type"]
                bal = float(w.get("balance", 0))
                if wtype == "safe":
                    # Safe balance comes from safe_lots, not the wallet balance field
                    lots = await db.safe_lots.find(
                        {"wallet_id": w["id"], "remaining_amount": {"$gt": 0}},
                        {"_id": 0, "remaining_amount": 1}
                    ).to_list(500)
                    bal = round(sum(float(lot["remaining_amount"]) for lot in lots), 2)
                wallet_balances[wtype] = {"balance": round(bal, 2), "name": w.get("name", wtype)}

        result.update({
            "record_number": po.get("po_number", ""),
            "supplier": po.get("vendor", "Unknown"),
            "supplier_contact": po.get("vendor_contact", ""),
            "branch_name": branch.get("name", "") if branch else "",
            "branch_id": branch_id_po,
            "date": po.get("purchase_date", po.get("created_at", "")),
            "due_date": po.get("due_date", ""),
            "status": po.get("status", ""),
            "grand_total": grand_total,
            "total_paid": total_paid,
            "balance": balance,
            "payment_status": payment_status,
            "payment_history": payment_history,
            "wallet_balances": wallet_balances,
            "items": [{
                "product_name": i.get("product_name", i.get("name", "?")),
                "sku": i.get("sku", ""),
                "quantity": float(i.get("quantity", 0)),
                "unit": i.get("unit", ""),
                "unit_price": float(i.get("unit_price", 0)),
                "total": round(float(i.get("quantity", 0)) * float(i.get("unit_price", 0)), 2),
            } for i in po.get("items", [])],
            "notes": po.get("notes", ""),
            "created_by": po.get("created_by_name", ""),
            "received_by": po.get("received_by_name", ""),
            "received_at": po.get("received_at", ""),
            "verified": po.get("verified", False),
            "verified_by": po.get("verified_by_name", ""),
            "verified_at": po.get("verified_at", ""),
            "receipt_review_status": po.get("receipt_review_status", ""),
            "receipt_reviewed_by": po.get("receipt_reviewed_by_name", ""),
        })

    elif record_type == "branch_transfer":
        bt = await db.branch_transfer_orders.find_one({"id": record_id}, {"_id": 0})
        if not bt:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Branch transfer not found")

        from_branch = await db.branches.find_one({"id": bt.get("from_branch_id")}, {"_id": 0, "name": 1})
        to_branch = await db.branches.find_one({"id": bt.get("to_branch_id")}, {"_id": 0, "name": 1})

        result.update({
            "record_number": bt.get("order_number", ""),
            "from_branch": from_branch.get("name", "") if from_branch else "",
            "to_branch": to_branch.get("name", "") if to_branch else "",
            "branch_id": bt.get("to_branch_id", bt.get("from_branch_id", "")),
            "status": bt.get("status", ""),
            "date": bt.get("created_at", ""),
            "sent_at": bt.get("sent_at", ""),
            "received_at": bt.get("received_at", ""),
            "received_by": bt.get("received_by_name", ""),
            "grand_total": float(bt.get("total_at_transfer_capital", 0)),
            "retail_total": float(bt.get("total_at_branch_retail", 0)),
            "has_shortage": bt.get("has_shortage", False),
            "invoice_number": bt.get("invoice_number", ""),
            "items": [{
                "product_name": i.get("product_name", "?"),
                "sku": i.get("sku", ""),
                "quantity": float(i.get("qty", 0)),
                "qty_received": float(i.get("qty_received", i.get("qty", 0))),
                "unit": i.get("unit", ""),
                "transfer_capital": float(i.get("transfer_capital", 0)),
                "branch_retail": float(i.get("branch_retail", 0)),
            } for i in bt.get("items", [])],
            "notes": bt.get("notes", bt.get("receive_notes", "")),
            "created_by": bt.get("created_by_name", ""),
            "receipt_review_status": bt.get("receipt_review_status", ""),
        })

    elif record_type == "expense":
        exp = await db.expenses.find_one({"id": record_id}, {"_id": 0})
        if not exp:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Expense not found")

        branch = await db.branches.find_one({"id": exp.get("branch_id")}, {"_id": 0, "name": 1})

        result.update({
            "record_number": exp.get("expense_number", exp.get("id", "")[:8]),
            "category": exp.get("category", ""),
            "description": exp.get("description", ""),
            "branch_name": branch.get("name", "") if branch else "",
            "branch_id": exp.get("branch_id", ""),
            "date": exp.get("date", exp.get("created_at", "")),
            "grand_total": float(exp.get("amount", 0)),
            "payment_method": exp.get("payment_method", ""),
            "vendor": exp.get("vendor", exp.get("payee", "")),
            "notes": exp.get("notes", ""),
            "created_by": exp.get("created_by_name", ""),
            "receipt_review_status": exp.get("receipt_review_status", ""),
        })

    else:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=f"Unsupported record type: {record_type}")

    # Fetch receipt files — include shared receipt context for audit trail
    upload_sessions = await db.upload_sessions.find(
        {"record_type": record_type, "record_id": record_id, "is_pending": {"$ne": True}},
        {"_id": 0}
    ).to_list(20)

    # Batch-resolve any shared_from PO numbers (avoid N+1 queries)
    shared_source_ids = {
        s["shared_from_record_id"]
        for s in upload_sessions
        if s.get("is_shared") and s.get("shared_from_record_id")
    }
    shared_source_numbers = {}
    if shared_source_ids:
        source_pos = await db.purchase_orders.find(
            {"id": {"$in": list(shared_source_ids)}},
            {"_id": 0, "id": 1, "po_number": 1, "vendor": 1}
        ).to_list(len(shared_source_ids))
        shared_source_numbers = {p["id"]: p for p in source_pos}

    files = []
    for s in upload_sessions:
        is_shared = s.get("is_shared", False)
        shared_from_id = s.get("shared_from_record_id", "")
        source_po = shared_source_numbers.get(shared_from_id, {}) if is_shared else {}

        for f in s.get("files", []):
            files.append({
                "id": f.get("id", ""),
                "filename": f.get("filename", ""),
                "content_type": f.get("content_type", ""),
                "uploaded_at": s.get("created_at", ""),
                "uploaded_by": s.get("created_by_name", ""),
                # Shared receipt provenance
                "is_shared": is_shared,
                "shared_from_record_id": shared_from_id,
                "shared_from_po_number": source_po.get("po_number", ""),
                "shared_from_vendor": source_po.get("vendor", ""),
            })

    result["receipt_files"] = files
    result["receipt_count"] = len(files)
    # Flag: true if ALL files came from a shared collection receipt (helps UI decide label)
    result["all_receipts_shared"] = bool(files) and all(f["is_shared"] for f in files)

    return result

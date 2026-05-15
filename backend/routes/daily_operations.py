"""
Daily operations routes: Sales log, daily reports, close accounts.
"""
from fastapi import APIRouter, Depends, HTTPException
from typing import Optional
from datetime import datetime, timezone, timedelta
from config import db
from utils import get_current_user, check_perm, now_iso, new_id, get_branch_filter, apply_branch_filter, assert_branch_access, today_local

router = APIRouter(tags=["Daily Operations"])


# ── Iter 243.4 — Net Sales (gross profit) for today ─────────────────────
# Owners want to see the actual margin earned today, not just gross sales.
# We sum (selling_price − cost_price) × quantity across every sold line item
# in non-voided, non-cash-out invoices for the branch+date.
#
# Cost basis: each sale line stores `cost_price` at the time of sale (a
# branch-aware capital snapshot — see sales.py:663). Never recomputed later,
# so historic Z-Reports stay stable even when product capitals are updated.
async def _compute_net_sales_today(branch_id: str, date: str) -> dict:
    """Return {gross_sales, cogs, net_sales, line_count} for today."""
    # Filter: same-day invoices, NOT voided, NOT cashout/farm (those aren't sales).
    invoices = await db.invoices.find(
        {
            "branch_id": branch_id,
            "order_date": date,
            "status": {"$ne": "voided"},
            "sale_type": {"$nin": ["cash_advance", "farm_expense", "interest_charge", "penalty_charge"]},
        },
        {"_id": 0, "items": 1, "overall_discount": 1},
    ).to_list(2000)
    gross = 0.0
    cogs = 0.0
    line_count = 0
    for inv in invoices:
        for line in (inv.get("items") or []):
            qty = float(line.get("quantity", 0))
            rate = float(line.get("rate") or line.get("unit_price") or line.get("price") or 0)
            disc = float(line.get("discount_amount", 0))
            cost = float(line.get("cost_price", 0))
            line_revenue = qty * rate - disc
            line_cogs = qty * cost
            gross += line_revenue
            cogs += line_cogs
            line_count += 1
        # Subtract the receipt-level overall discount from gross revenue
        gross -= float(inv.get("overall_discount", 0))
    return {
        "gross_sales": round(gross, 2),
        "cogs": round(cogs, 2),
        "net_sales": round(gross - cogs, 2),
        "line_count": line_count,
    }


# ── Iter 243.4 — Employee cash advance running totals ───────────────────
# Owners want to see how much each employee has TAKEN today AND their CURRENT
# outstanding balance, right under cashier expenses in the Z-Report. This
# surfaces a hidden risk: an employee can rack up advances that show as
# routine "Employee Advance" expenses, without anyone seeing the total.
async def _compute_employee_advances_today(branch_id: str, date: str) -> list:
    """Return list of {employee_id, employee_name, today_amount, outstanding_balance}
    for any employee who took an advance today.
    """
    advances_today = await db.expenses.find(
        {
            "branch_id": branch_id,
            "date": date,
            "category": "Employee Advance",
        },
        {"_id": 0, "employee_id": 1, "employee_name": 1, "amount": 1, "vendor_name": 1, "description": 1},
    ).to_list(200)
    if not advances_today:
        return []
    # Aggregate today's advances per employee
    by_emp: dict = {}
    for adv in advances_today:
        emp_id = adv.get("employee_id") or ""
        if not emp_id:
            # Some legacy rows store the name only — group by the display name.
            emp_id = f"_name:{adv.get('vendor_name') or adv.get('employee_name') or 'Unknown'}"
        rec = by_emp.setdefault(emp_id, {
            "employee_id": adv.get("employee_id") or "",
            "employee_name": adv.get("employee_name") or adv.get("vendor_name") or "",
            "today_amount": 0.0,
        })
        rec["today_amount"] += float(adv.get("amount", 0))
    # Look up running balance for each employee with a real id
    real_ids = [v["employee_id"] for v in by_emp.values() if v["employee_id"]]
    balances: dict = {}
    if real_ids:
        emps = await db.employees.find(
            {"id": {"$in": real_ids}},
            {"_id": 0, "id": 1, "name": 1, "advance_balance": 1},
        ).to_list(200)
        for e in emps:
            balances[e["id"]] = {
                "name": e.get("name", ""),
                "balance": float(e.get("advance_balance", 0)),
            }
    out = []
    for v in by_emp.values():
        bal_info = balances.get(v["employee_id"]) if v["employee_id"] else None
        out.append({
            "employee_id": v["employee_id"],
            "employee_name": (bal_info["name"] if bal_info else v["employee_name"]) or "Unknown",
            "today_amount": round(v["today_amount"], 2),
            # Outstanding balance INCLUDES today's advances since `advance_balance`
            # is updated when the advance is recorded.
            "outstanding_balance": round(bal_info["balance"], 2) if bal_info else None,
        })
    # Sort largest-balance first (or largest-today if no balance)
    out.sort(key=lambda r: -(r["outstanding_balance"] or r["today_amount"]))
    return out


# ── Iter 240 fix: write a proper audit trail when closing a day ────────
# Before this iter: close_day just $set the cashier wallet balance and
# inserted a safe_lots row. No `wallet_movements` and no `fund_transfers`
# row was written, so Fund Management → Cashier Drawer / Safe history
# saw money silently appearing/disappearing on close.
#
# This helper is called both by close_day (single) and batch_close_days
# so the ledger logic stays in lock-step and idempotent (each entry is
# tagged `daily_close_ref = closing.id`, and we never re-create when
# replaying via the backfill script — see admin_backfill_241).
async def _write_close_ledger_entries(
    *, closing_id: str, branch_id: str, date: str,
    cashier_wallet: dict, safe_wallet: Optional[dict],
    actual_cash: float, cash_to_safe: float, cash_to_drawer: float,
    over_short: float, variance_notes: str,
    user: dict,
) -> dict:
    """Write the day-close ledger trail. Returns counts for verification.

    Idempotency: each inserted doc has `daily_close_ref = closing_id` so
    we can detect prior runs (used by the backfill).
    """
    inserted = {"wallet_movements": 0, "fund_transfers": 0}
    user_name = user.get("full_name") or user.get("name") or user.get("username") or ""

    if cashier_wallet:
        # 1) Over/short variance — only when |delta| > 1 cent
        if abs(over_short) > 0.005:
            await db.wallet_movements.insert_one({
                "id": new_id(),
                "wallet_id": cashier_wallet["id"],
                "branch_id": branch_id,
                "type": "over_short_adjust",
                "amount": round(over_short, 2),
                "reference": f"Day close {date} — variance",
                "description": variance_notes or ("Cash overage" if over_short > 0 else "Cash shortage"),
                "user_id": user["id"],
                "user_name": user_name,
                "date": date,
                "daily_close_ref": closing_id,
                "created_at": now_iso(),
            })
            inserted["wallet_movements"] += 1

        # 2) Cashier → Safe transfer (only when amount > 0)
        if cash_to_safe > 0 and safe_wallet:
            await db.fund_transfers.insert_one({
                "id": new_id(), "branch_id": branch_id,
                "transfer_type": "cashier_to_safe",
                "amount": round(cash_to_safe, 2),
                "from_wallet_id": cashier_wallet["id"],
                "to_wallet_id": safe_wallet["id"],
                "target_wallet": "safe",
                "note": f"Day close {date} — vault transfer",
                "authorized_by": user_name,
                "user_id": user["id"],
                "date": date,
                "daily_close_ref": closing_id,
                "created_at": now_iso(),
            })
            inserted["fund_transfers"] += 1

            await db.wallet_movements.insert_one({
                "id": new_id(),
                "wallet_id": cashier_wallet["id"],
                "branch_id": branch_id,
                "type": "transfer_out",
                "amount": -round(cash_to_safe, 2),
                "reference": f"Day close {date} → Safe",
                "description": "End-of-day vault transfer",
                "user_id": user["id"],
                "user_name": user_name,
                "date": date,
                "daily_close_ref": closing_id,
                "created_at": now_iso(),
            })
            inserted["wallet_movements"] += 1

            await db.wallet_movements.insert_one({
                "id": new_id(),
                "wallet_id": safe_wallet["id"],
                "branch_id": branch_id,
                "type": "transfer_in",
                "amount": round(cash_to_safe, 2),
                "reference": f"Day close {date} from cashier",
                "description": "End-of-day cashier deposit",
                "user_id": user["id"],
                "user_name": user_name,
                "date": date,
                "daily_close_ref": closing_id,
                "created_at": now_iso(),
            })
            inserted["wallet_movements"] += 1

    return inserted



@router.get("/daily-close/unclosed-days")
async def get_unclosed_days(
    user=Depends(get_current_user),
    branch_id: Optional[str] = None,
):
    """
    Find all unclosed business days since the last closing until today.
    Returns dates with basic summaries so the user can close them one-by-one.
    """
    check_perm(user, "reports", "view")
    if not branch_id:
        branch_id = user.get("branch_id")
    if not branch_id:
        raise HTTPException(status_code=400, detail="branch_id required")

    today = await today_local(user.get("organization_id") or "")

    # Find last closed day for this branch
    last_close = await db.daily_closings.find_one(
        {"branch_id": branch_id, "status": "closed"},
        {"_id": 0, "date": 1, "cash_to_drawer": 1, "closed_at": 1},
        sort=[("date", -1)],
    )

    if last_close:
        start_date = (datetime.strptime(last_close["date"], "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        last_close_date = last_close["date"]
        last_drawer = float(last_close.get("cash_to_drawer", 0))
    else:
        # No closings ever — find the earliest transaction or default to 7 days ago
        earliest_sale = await db.sales_log.find_one(
            {"branch_id": branch_id}, {"_id": 0, "date": 1}, sort=[("date", 1)]
        )
        earliest_expense = await db.expenses.find_one(
            {"branch_id": branch_id}, {"_id": 0, "date": 1}, sort=[("date", 1)]
        )
        dates = []
        if earliest_sale and earliest_sale.get("date"):
            dates.append(earliest_sale["date"])
        if earliest_expense and earliest_expense.get("date"):
            dates.append(earliest_expense["date"])
        if dates:
            start_date = min(dates)
        else:
            start_date = today
        last_close_date = None
        last_drawer = 0

    # Build list of unclosed days from start_date to today
    unclosed = []
    current = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(today, "%Y-%m-%d")

    while current <= end:
        d = current.strftime("%Y-%m-%d")

        # Check if any activity exists for this day
        sales_count = await db.sales_log.count_documents({"branch_id": branch_id, "date": d, "voided": {"$ne": True}})
        expense_count = await db.expenses.count_documents({"branch_id": branch_id, "date": d, "voided": {"$ne": True}})
        invoice_count = await db.invoices.count_documents({
            "branch_id": branch_id, "order_date": d, "status": {"$ne": "voided"}
        })

        has_activity = sales_count > 0 or expense_count > 0 or invoice_count > 0

        # Quick totals
        cash_total = 0
        if sales_count > 0:
            agg = await db.sales_log.aggregate([
                # B-3 fix: exclude partial-line rows from this summary too,
                # to keep the "unclosed-days" cash preview consistent with
                # the close-day mutating path.
                {"$match": {"branch_id": branch_id, "date": d, "voided": {"$ne": True},
                            "payment_method": {"$regex": "^cash$", "$options": "i"},
                            "partial_grand_total": {"$exists": False}}},
                {"$group": {"_id": None, "total": {"$sum": "$line_total"}}}
            ]).to_list(1)
            cash_total = round(agg[0]["total"], 2) if agg else 0

        expense_total = 0
        if expense_count > 0:
            agg = await db.expenses.aggregate([
                {"$match": {"branch_id": branch_id, "date": d, "voided": {"$ne": True}}},
                {"$group": {"_id": None, "total": {"$sum": "$amount"}}}
            ]).to_list(1)
            expense_total = round(agg[0]["total"], 2) if agg else 0

        unclosed.append({
            "date": d,
            "has_activity": has_activity,
            "sales_count": sales_count,
            "expense_count": expense_count,
            "invoice_count": invoice_count,
            "cash_sales_total": cash_total,
            "expense_total": expense_total,
        })

        current += timedelta(days=1)

    # Compute the earliest operational date (system floor) for this branch.
    # Sales cannot be encoded before this date — the system didn't exist yet.
    floor_date = None
    earliest_dates = []
    earliest_sale_any = await db.sales_log.find_one(
        {"branch_id": branch_id}, {"_id": 0, "date": 1}, sort=[("date", 1)]
    )
    earliest_expense_any = await db.expenses.find_one(
        {"branch_id": branch_id}, {"_id": 0, "date": 1}, sort=[("date", 1)]
    )
    earliest_invoice_any = await db.invoices.find_one(
        {"branch_id": branch_id}, {"_id": 0, "order_date": 1}, sort=[("order_date", 1)]
    )
    if earliest_sale_any and earliest_sale_any.get("date"):
        earliest_dates.append(earliest_sale_any["date"])
    if earliest_expense_any and earliest_expense_any.get("date"):
        earliest_dates.append(earliest_expense_any["date"])
    if earliest_invoice_any and earliest_invoice_any.get("order_date"):
        earliest_dates.append(earliest_invoice_any["order_date"])
    if earliest_dates:
        floor_date = min(earliest_dates)

    return {
        "last_close_date": last_close_date,
        "last_drawer_float": last_drawer,
        "unclosed_days": unclosed,
        "total_unclosed": len(unclosed),
        "today": today,
        "floor_date": floor_date,
    }



async def _get_pending_releases_summary(branch_id: str) -> dict:
    """Return a summary of invoices with unreleased partial stock for the Z-report warning."""
    q = {"release_mode": "partial", "stock_release_status": {"$in": ["not_released", "partially_released"]}}
    if branch_id:
        q["branch_id"] = branch_id
    count = await db.invoices.count_documents(q)
    res_q = {"qty_remaining": {"$gt": 0}}
    if branch_id:
        res_q["branch_id"] = branch_id
    res_agg = await db.sale_reservations.aggregate([
        {"$match": res_q},
        {"$group": {"_id": None, "total": {"$sum": "$qty_remaining"}}}
    ]).to_list(1)
    from datetime import datetime, timezone
    now_str = datetime.now(timezone.utc).isoformat()
    overdue = await db.sale_reservations.count_documents({**res_q, "expires_at": {"$lt": now_str}})
    return {
        "pending_invoice_count": count,
        "total_reserved_qty": round(float(res_agg[0]["total"]), 2) if res_agg else 0,
        "overdue_reservations": overdue,
        "has_overdue": overdue > 0,
    }


async def _get_negative_stock_summary(branch_id: str) -> dict:
    """Return items with negative inventory for the Z-report warning."""
    q = {"quantity": {"$lt": 0}}
    if branch_id:
        q["branch_id"] = branch_id
    neg_records = await db.inventory.find(q, {"_id": 0, "product_id": 1, "quantity": 1}).to_list(200)
    if not neg_records:
        return {"count": 0, "items": []}
    pids = [r["product_id"] for r in neg_records]
    products = await db.products.find({"id": {"$in": pids}}, {"_id": 0, "id": 1, "name": 1}).to_list(200)
    pmap = {p["id"]: p["name"] for p in products}
    items = [
        {"product_id": r["product_id"], "product_name": pmap.get(r["product_id"], r["product_id"]),
         "quantity": round(float(r["quantity"]), 4)}
        for r in neg_records
    ]
    return {"count": len(items), "items": items}


async def _get_discount_breakdown(branch_id: str, date: str) -> dict:
    """
    Return per-product discount breakdown for the day.
    Aggregates `discount_audit_log` entries on the given date+branch into:
      [{product_name, units_sold, total_discount, avg_discount_per_unit}]
    Plus totals for the closing wizard.
    """
    q = {"date": date, "invoice_voided": {"$ne": True}}
    if branch_id:
        q["branch_id"] = branch_id
    logs = await db.discount_audit_log.find(q, {"_id": 0}).to_list(2000)
    by_product = {}
    overall_disc_total = 0.0
    txn_count = 0
    for log in logs:
        txn_count += 1
        for item in log.get("items", []):
            t = item.get("type", "")
            if t == "overall_discount":
                overall_disc_total += float(item.get("discount_amount", 0) or 0)
                continue
            disc_amt = float(item.get("discount_amount", 0) or 0)
            if disc_amt <= 0:
                continue
            pid = item.get("product_id") or item.get("product_name", "Unknown")
            if pid not in by_product:
                by_product[pid] = {
                    "product_id": item.get("product_id"),
                    "product_name": item.get("product_name", "Unknown"),
                    "units_sold": 0,
                    "total_discount": 0,
                }
            by_product[pid]["units_sold"] += float(item.get("quantity", 0) or 0)
            by_product[pid]["total_discount"] += disc_amt
    products_list = []
    for v in by_product.values():
        units = v["units_sold"] or 0
        total = round(v["total_discount"], 2)
        products_list.append({
            "product_id": v["product_id"],
            "product_name": v["product_name"],
            "units_sold": round(units, 2),
            "total_discount": total,
            "avg_discount_per_unit": round(total / units, 2) if units > 0 else 0,
        })
    products_list.sort(key=lambda x: x["total_discount"], reverse=True)
    line_disc_total = round(sum(p["total_discount"] for p in products_list), 2)
    return {
        "products": products_list,
        "total_line_discounts": line_disc_total,
        "total_overall_discount": round(overall_disc_total, 2),
        "total_discount": round(line_disc_total + overall_disc_total, 2),
        "transaction_count": txn_count,
    }


async def _get_price_changes_today(branch_id: str, date: str) -> dict:
    """Return summary of all permanent price changes (Price Match) for the day.

    Each row is enriched with the product's `category`, branch-specific
    `moving_average_cost`, and `last_purchase_cost` so the Z-Report can
    show how the new price compares against capital. Rows are sorted
    alphabetically by (category, product_name) so the Z-Report can group
    them per category for at-a-glance review.
    """
    q = {"date": date, "invoice_voided": {"$ne": True}}
    if branch_id:
        q["branch_id"] = branch_id
    rows = await db.price_change_log.find(
        q, {"_id": 0, "product_id": 1, "product_name": 1,
            "old_price": 1, "new_price": 1, "scheme": 1,
            "reason": 1, "approver_name": 1, "cashier_name": 1,
            "invoice_number": 1, "branch_id": 1}
    ).to_list(500)

    # Cache per-product enrichment so we don't repeatedly hit movements/products
    # for the same SKU (multi-line/multi-invoice price changes are common).
    enrich_cache: dict = {}

    async def _enrich(pid: str, br_id: str) -> dict:
        key = (pid, br_id)
        if key in enrich_cache:
            return enrich_cache[key]
        product = await db.products.find_one(
            {"id": pid},
            {"_id": 0, "category": 1, "is_repack": 1, "parent_id": 1,
             "units_per_parent": 1, "cost_price": 1},
        ) or {}
        category = product.get("category") or "Uncategorized"

        lookup_id = product.get("parent_id") if product.get("is_repack") and product.get("parent_id") else pid
        acq_query = {
            "product_id": lookup_id,
            "type": {"$in": ["purchase", "transfer_in"]},
            "quantity_change": {"$gt": 0},
        }
        if br_id:
            acq_query["branch_id"] = br_id

        last_acq = await db.movements.find_one(
            acq_query, {"_id": 0, "price_at_time": 1}, sort=[("created_at", -1)]
        )
        last_purchase = float(last_acq.get("price_at_time", 0)) if last_acq else 0.0

        all_acqs = await db.movements.find(
            acq_query, {"_id": 0, "price_at_time": 1, "quantity_change": 1}
        ).to_list(10000)
        total_qty = sum(float(m.get("quantity_change", 0)) for m in all_acqs)
        total_cost = sum(float(m.get("quantity_change", 0)) * float(m.get("price_at_time", 0)) for m in all_acqs)
        # Moving average is STRICTLY branch-specific. No fallback — if this
        # branch has no purchase/transfer-in history the MA is 0 and the UI
        # renders a dash.
        ma = round(total_cost / total_qty, 4) if total_qty > 0 else 0.0

        # For repacks, scale parent-level cost down to the repack unit so the
        # capital reference is comparable to the line price the cashier sees.
        if product.get("is_repack") and float(product.get("units_per_parent") or 0) > 1:
            upp = float(product["units_per_parent"])
            last_purchase = round(last_purchase / upp, 4) if last_purchase else last_purchase
            ma = round(ma / upp, 4) if ma else ma

        # Last Purchase fallback — only when this branch has zero acquisition
        # history for the SKU (brand new product, or legacy data imported
        # without movement rows), fall back to the product's global cost_price
        # so the Z-Report still shows a meaningful capital reference.
        last_purchase_source = "branch"
        if last_purchase <= 0:
            global_cost = float(product.get("cost_price", 0) or 0)
            if global_cost > 0:
                last_purchase = global_cost
                last_purchase_source = "global"
            else:
                last_purchase_source = "none"

        enriched = {
            "category": category,
            "last_purchase_cost": last_purchase,
            "last_purchase_source": last_purchase_source,
            "moving_average_cost": ma,
        }
        enrich_cache[key] = enriched
        return enriched

    enriched_rows = []
    for r in rows:
        pid = r.get("product_id") or ""
        br_id = r.get("branch_id") or branch_id or ""
        info = await _enrich(pid, br_id) if pid else {
            "category": "Uncategorized",
            "last_purchase_cost": 0.0,
            "last_purchase_source": "none",
            "moving_average_cost": 0.0,
        }
        old_p = float(r.get("old_price") or 0)
        new_p = float(r.get("new_price") or 0)
        enriched_rows.append({
            **r,
            "category": info["category"],
            "last_purchase_cost": info["last_purchase_cost"],
            "last_purchase_source": info.get("last_purchase_source", "branch"),
            "moving_average_cost": info["moving_average_cost"],
            "delta": round(new_p - old_p, 2),
            "delta_pct": round((new_p - old_p) / old_p * 100, 2) if old_p > 0 else 0,
        })

    enriched_rows.sort(key=lambda r: (
        (r.get("category") or "").lower(),
        (r.get("product_name") or "").lower(),
    ))
    return {"count": len(enriched_rows), "rows": enriched_rows}


@router.get("/daily-close-preview")
async def get_daily_close_preview(
    user=Depends(get_current_user),
    branch_id: Optional[str] = None,
    date: Optional[str] = None
):
    """
    Full Z-Report preview data for day close.
    Returns all sections needed for the cash reconciliation form.
    """
    check_perm(user, "reports", "view")
    if not date:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if not branch_id:
        branch_id = user.get("branch_id")

    month_prefix = date[:7]  # "YYYY-MM"

    # ── Starting float: last closed day's cash_to_drawer ───────────────────────────
    prev_close = await db.daily_closings.find_one(
        {"branch_id": branch_id, "date": {"$lt": date}, "status": "closed"},
        {"_id": 0},
        sort=[("date", -1)]
    )
    has_prev_close = bool(prev_close)
    if prev_close:
        starting_float = float(prev_close.get("cash_to_drawer", 0))
    else:
        wallet = await db.fund_wallets.find_one(
            {"branch_id": branch_id, "type": "cashier", "active": True}, {"_id": 0}
        )
        starting_float = float(wallet["balance"]) if wallet else 0.0

    # ── Safe balance (informational) ─────────────────────────────────────────
    safe = await db.fund_wallets.find_one(
        {"branch_id": branch_id, "type": "safe", "active": True}, {"_id": 0}
    )
    safe_balance = 0.0
    if safe:
        lots = await db.safe_lots.find(
            {"wallet_id": safe["id"], "remaining_amount": {"$gt": 0}}, {"_id": 0}
        ).to_list(500)
        safe_balance = sum(l["remaining_amount"] for l in lots)

    # ── Cash sales today (payment_type=cash, by category) ────────────────────
    # B-3 fix: exclude partial-sale line rows here — their cash portion is
    # already captured by `ar_pipeline` (payments[].date==today on credit/
    # partial invoices). Including the full partial line_total here as well
    # would double-count by (line_total - partial_cash_amount) per partial
    # sale, falsely shorting the cashier at close.
    cash_sales_pipeline = [
        {"$match": {"branch_id": branch_id, "date": date, "voided": {"$ne": True},
                    "payment_method": {"$regex": "^cash$", "$options": "i"},
                    "partial_grand_total": {"$exists": False}}},
        {"$group": {"_id": "$category", "total": {"$sum": "$line_total"}, "qty": {"$sum": "$quantity"}}},
        {"$sort": {"total": -1}},
    ]
    cat_results = await db.sales_log.aggregate(cash_sales_pipeline).to_list(100)
    cash_sales_by_category = [
        {"category": r["_id"] or "General", "total": round(r["total"], 2), "qty": r["qty"]}
        for r in cat_results
    ]
    total_cash_sales = round(sum(c["total"] for c in cash_sales_by_category), 2)

    # Also include partial-sale cash received today (amount_paid from partial invoices)
    # Phase 3: exclude historical_credit_encoding so re-querying an OLD closed
    # day's Z-report stays stable when notebook AR is encoded today.
    partial_invoices = await db.invoices.find(
        {"branch_id": branch_id, "order_date": date,
         "payment_type": "partial", "status": {"$ne": "voided"},
         "source": {"$ne": "historical_credit_encoding"}},
        {"_id": 0, "customer_name": 1, "invoice_number": 1, "amount_paid": 1, "grand_total": 1}
    ).to_list(500)
    total_partial_cash = round(sum(float(inv.get("amount_paid", 0)) for inv in partial_invoices), 2)

    # ── New credit sales today (info only — not cash) ─────────────────────────
    # Enrich each row with full audit context for the Closing Wizard "Customer
    # Credit Generated Today" section: invoice total, paid_today (cash leg
    # received today on this invoice), remaining balance, and a status badge.
    # Phase 3: exclude historical_credit_encoding so historical notebook AR
    # encoded TODAY for an OLD date does NOT show up as "credit generated on
    # the old date" when re-querying that old day's Z-report. It surfaces in
    # the late-encoded section of the CURRENT day instead.
    credit_invoices = await db.invoices.find(
        {"branch_id": branch_id, "order_date": date,
         "payment_type": {"$in": ["credit", "partial"]}, "status": {"$ne": "voided"},
         "source": {"$ne": "historical_credit_encoding"}},
        {"_id": 0, "customer_name": 1, "invoice_number": 1, "grand_total": 1,
         "balance": 1, "payment_type": 1, "amount_paid": 1, "payments": 1, "id": 1}
    ).to_list(500)
    credit_sales_today = []
    for inv in credit_invoices:
        gt = float(inv.get("grand_total", 0))
        bal = float(inv.get("balance", 0))
        # Sum of payments dated today (matches what we will surface in Step 3)
        paid_today = round(sum(
            float(p.get("amount", 0))
            for p in (inv.get("payments") or [])
            if p.get("date") == date and not p.get("voided")
        ), 2)
        if bal <= 0.005:
            status = "Fully Paid Same-Day"
        elif paid_today > 0 or float(inv.get("amount_paid", 0)) > 0:
            status = "Partially Paid"
        else:
            status = "Unpaid"
        credit_sales_today.append({
            "customer_name": inv.get("customer_name", ""),
            "invoice_number": inv.get("invoice_number", ""),
            "grand_total": round(gt, 2),
            "balance": round(bal, 2),
            "remaining_balance": round(bal, 2),
            "payment_type": inv.get("payment_type", "credit"),
            "amount_paid": round(float(inv.get("amount_paid", 0)), 2),
            "paid_today": paid_today,
            "status": status,
        })
    total_credit_today = round(sum(c["balance"] for c in credit_sales_today), 2)
    total_credit_invoice_value = round(sum(c["grand_total"] for c in credit_sales_today), 2)

    # ── AR / Credit Payments Today (ALL payments dated today) ────────────────
    # Includes: (a) initial cash leg of partial sales created today,
    #           (b) same-day extra payments on today's credit/partial sales,
    #           (c) payments today on older credit invoices,
    #           (d) interest/penalty invoice payments.
    # Each payment is tagged `is_same_day` so the UI can split the display
    # into "Same-Day Credit Payments" vs "Older Credit Payments" without
    # affecting the underlying cash-in math.
    #
    # CRITICAL FILTER: only include invoices with AR semantics (credit /
    # partial / interest / penalty). Pure cash, digital, and split sales
    # also push a payment record with today's date but they are NOT AR
    # collections — they are already counted in Step 1's `total_cash_sales`
    # (and `total_split_cash`). Without this filter every full-cash invoice
    # would double-count into `total_cash_ar` and inflate `expected_counter`.
    ar_pipeline = [
        {"$match": {
            "branch_id": branch_id,
            "status": {"$ne": "voided"},
            "$or": [
                {"payment_type": {"$in": ["credit", "partial"]}},
                {"sale_type": {"$in": ["interest_charge", "penalty_charge"]}},
            ],
        }},
        {"$unwind": "$payments"},
        {"$match": {
            "payments.date": date,
            "payments.voided": {"$ne": True},
        }},
        {"$project": {
            "_id": 0,
            "id": 1, "customer_name": 1, "invoice_number": 1,
            "customer_id": 1, "sale_type": 1,
            "order_date": 1, "payment_type": 1,
            "balance": 1,  # current balance after all payments
            "grand_total": 1, "amount_paid": 1,
            "payment": "$payments"
        }}
    ]
    ar_payments_raw = await db.invoices.aggregate(ar_pipeline).to_list(500)
    ar_payments = []
    for p in ar_payments_raw:
        pmt = p.get("payment", {})
        amount = float(pmt.get("amount", 0))
        sale_type = p.get("sale_type", "")
        # For interest/penalty invoice payments, the whole amount IS interest/penalty.
        # The `/customers/.../receive-payment` endpoint hardcodes applied_to_interest=0
        # for all payments (even on interest invoices), so we classify by invoice type here.
        if sale_type == "interest_charge":
            interest_paid = amount
            penalty_paid = 0.0
            principal_paid = 0.0
        elif sale_type == "penalty_charge":
            interest_paid = 0.0
            penalty_paid = amount
            principal_paid = 0.0
        else:
            interest_paid = float(pmt.get("applied_to_interest", 0))
            penalty_paid = float(pmt.get("applied_to_penalty", 0))
            principal_paid = float(pmt.get("applied_to_principal", amount - interest_paid - penalty_paid))
        current_bal = float(p.get("balance", 0))
        order_date_inv = p.get("order_date", "")
        is_same_day = (order_date_inv == date)
        payment_type_inv = p.get("payment_type", "")
        # First payment chronologically on today's partial sale = "initial cash leg"
        is_initial_partial = (
            is_same_day
            and payment_type_inv == "partial"
            and pmt.get("recorded_at", "")
        )
        ar_payments.append({
            "invoice_id": p.get("id", ""),
            "customer_id": p.get("customer_id", ""),
            "customer_name": p.get("customer_name", ""),
            "invoice_number": p.get("invoice_number", ""),
            "sale_type": sale_type,
            "order_date": order_date_inv,
            "payment_type_invoice": payment_type_inv,
            "is_same_day": is_same_day,
            "is_initial_partial": bool(is_initial_partial),
            "invoice_total": round(float(p.get("grand_total", 0)), 2),
            "balance_before": round(current_bal + amount, 2),
            "interest_paid": round(interest_paid, 2),
            "penalty_paid": round(penalty_paid, 2),
            "principal_paid": round(principal_paid, 2),
            "amount_paid": round(amount, 2),
            "remaining_balance": round(current_bal, 2),
            "fund_source": pmt.get("fund_source", "cashier"),
            "method": pmt.get("method", "Cash"),
            "recorded_by": pmt.get("recorded_by", ""),
            "recorded_at": pmt.get("recorded_at", ""),
            "reference": pmt.get("reference", ""),
        })
    # Sort: same-day first (initial partial first per invoice), then by recorded_at
    ar_payments.sort(key=lambda p: (
        not p.get("is_same_day", False),
        p.get("recorded_at", ""),
    ))
    total_ar_received = round(sum(p["amount_paid"] for p in ar_payments), 2)
    total_ar_same_day = round(sum(
        p["amount_paid"] for p in ar_payments if p.get("is_same_day")
    ), 2)
    total_ar_older = round(total_ar_received - total_ar_same_day, 2)

    # ── Expenses today — split by fund source for accurate reconciliation ────
    expenses_raw = await db.expenses.find(
        {"branch_id": branch_id, "date": date, "voided": {"$ne": True}}, {"_id": 0}
    ).to_list(500)

    # For Employee Advance expenses: add monthly running total and limit info
    expenses = []
    _emp_limit_cache = {}
    for e in expenses_raw:
        exp = dict(e)
        if e.get("category") == "Employee Advance" and e.get("employee_id"):
            month_total_pipeline = [
                {"$match": {
                    "branch_id": branch_id,
                    "category": "Employee Advance",
                    "employee_id": e["employee_id"],
                    "date": {"$gte": f"{month_prefix}-01", "$lte": f"{month_prefix}-31"}
                }},
                {"$group": {"_id": None, "total": {"$sum": "$amount"}}}
            ]
            month_res = await db.expenses.aggregate(month_total_pipeline).to_list(1)
            exp["monthly_ca_total"] = round(month_res[0]["total"] if month_res else 0, 2)
            # Fetch employee's monthly limit (cached)
            eid = e["employee_id"]
            if eid not in _emp_limit_cache:
                emp_doc = await db.employees.find_one({"id": eid}, {"_id": 0, "monthly_ca_limit": 1})
                _emp_limit_cache[eid] = float(emp_doc.get("monthly_ca_limit", 0)) if emp_doc else 0
            exp["monthly_ca_limit"] = _emp_limit_cache[eid]
            exp["is_over_ca"] = exp["monthly_ca_limit"] > 0 and exp["monthly_ca_total"] > exp["monthly_ca_limit"]
        expenses.append(exp)

    total_expenses = round(sum(float(e.get("amount", 0)) for e in expenses), 2)
    # Only cashier-sourced expenses affect the drawer; safe and digital don't
    total_cashier_expenses = round(sum(
        float(e.get("amount", 0)) for e in expenses
        if e.get("fund_source", "cashier") == "cashier"
    ), 2)
    total_safe_expenses = round(sum(
        float(e.get("amount", 0)) for e in expenses
        if e.get("fund_source") == "safe"
    ), 2)
    total_digital_expenses = round(total_expenses - total_cashier_expenses - total_safe_expenses, 2)

    # ── Digital payments today (GCash, Maya, etc.) ───────────────────────────
    digital_invoices = await db.invoices.find(
        {"branch_id": branch_id, "order_date": date,
         "fund_source": {"$in": ["digital", "split"]}, "status": {"$ne": "voided"}},
        {"_id": 0, "customer_name": 1, "invoice_number": 1, "grand_total": 1,
         "amount_paid": 1, "digital_amount": 1, "digital_platform": 1,
         "digital_ref_number": 1, "digital_sender": 1, "fund_source": 1}
    ).to_list(500)
    digital_by_platform = {}
    for inv in digital_invoices:
        # For split payments, only count the digital portion
        amt = float(
            inv.get("digital_amount", 0)
            if inv.get("fund_source") == "split" and inv.get("digital_amount")
            else inv.get("amount_paid", 0)
        )
        platform = inv.get("digital_platform", "Digital") or "Digital"
        digital_by_platform[platform] = round(digital_by_platform.get(platform, 0) + amt, 2)
    total_digital_today = round(
        sum(
            float(inv.get("digital_amount", 0) if inv.get("fund_source") == "split" and inv.get("digital_amount") else inv.get("amount_paid", 0))
            for inv in digital_invoices
        ), 2
    )

    # ── Expected counter ──────────────────────────────────────────────────────
    # starting_float + all_cash_received_today - cashier_expenses_only
    # + fund transfers that affect the cashier drawer
    # Include split payment cash portions (stored on invoices, not in sales_log)
    split_invoices = await db.invoices.find(
        {"branch_id": branch_id, "order_date": date,
         "fund_source": "split", "status": {"$ne": "voided"}},
        {"_id": 0, "cash_amount": 1, "digital_amount": 1, "grand_total": 1}
    ).to_list(500)
    total_split_cash = round(sum(float(inv.get("cash_amount", 0)) for inv in split_invoices), 2)

    # Separate AR payments: only cash AR affects the drawer
    total_cash_ar = round(sum(
        p["amount_paid"] for p in ar_payments
        if p.get("fund_source", "cashier") == "cashier"
    ), 2)
    total_digital_ar = round(total_ar_received - total_cash_ar, 2)

    # ── Fund transfers that affect cashier drawer today ─────────────────────
    ft_query = {"branch_id": branch_id, "$or": [
        {"date": date},
        {"date": {"$exists": False}, "created_at": {"$gte": f"{date}T00:00:00", "$lt": f"{date}T23:59:59"}}
    ]}
    fund_transfers_today = await db.fund_transfers.find(ft_query, {"_id": 0}).to_list(200)

    # Capital injections to cashier (+ to drawer)
    capital_to_cashier = round(sum(
        float(ft.get("amount", 0)) for ft in fund_transfers_today
        if ft.get("transfer_type") == "capital_add" and ft.get("target_wallet", "cashier") == "cashier"
    ), 2)
    # Safe → Cashier transfers (+ to drawer)
    safe_to_cashier = round(sum(
        float(ft.get("amount", 0)) for ft in fund_transfers_today
        if ft.get("transfer_type") == "safe_to_cashier"
    ), 2)
    # Cashier → Safe transfers (- from drawer)
    cashier_to_safe = round(sum(
        float(ft.get("amount", 0)) for ft in fund_transfers_today
        if ft.get("transfer_type") == "cashier_to_safe"
    ), 2)
    # Net cashier fund movement
    net_fund_transfers = round(capital_to_cashier + safe_to_cashier - cashier_to_safe, 2)

    total_cash_in = total_cash_sales + total_cash_ar + total_split_cash
    if has_prev_close:
        expected_counter = round(starting_float + total_cash_in + net_fund_transfers - total_cashier_expenses, 2)
    else:
        # First-ever close: wallet balance is real-time truth
        wallet_now = await db.fund_wallets.find_one(
            {"branch_id": branch_id, "type": "cashier", "active": True}, {"_id": 0}
        )
        current_balance = round(float(wallet_now["balance"]) if wallet_now else 0, 2)
        expected_counter = current_balance
        # Reverse today's cash movements to compute the TRUE opening float
        # opening = current_balance − all_cash_in − fund_transfers_in + expenses_out + transfers_out
        starting_float = round(
            current_balance
            - total_cash_in
            - net_fund_transfers
            + total_cashier_expenses,
            2
        )

    return {
        "date": date,
        "branch_id": branch_id,
        # Opening
        "starting_float": starting_float,
        "safe_balance": round(safe_balance, 2),
        # Cash inflows
        "cash_sales_by_category": cash_sales_by_category,
        "total_cash_sales": total_cash_sales,
        "total_split_cash": total_split_cash,
        "partial_invoices": [
            {"customer_name": inv["customer_name"],
             "invoice_number": inv["invoice_number"],
             "amount_paid": round(float(inv.get("amount_paid", 0)), 2),
             "grand_total": round(float(inv.get("grand_total", 0)), 2)}
            for inv in partial_invoices
        ],
        "total_partial_cash": total_partial_cash,
        # Digital payments today
        "digital_sales_today": [
            {"invoice_number": inv.get("invoice_number"), "customer_name": inv.get("customer_name"),
             "amount": round(float(inv.get("digital_amount", 0) if inv.get("fund_source") == "split" and inv.get("digital_amount") else inv.get("amount_paid", 0)), 2),
             "platform": inv.get("digital_platform", "Digital"),
             "ref_number": inv.get("digital_ref_number", ""),
             "fund_source": inv.get("fund_source", "digital")}
            for inv in digital_invoices
        ],
        "digital_by_platform": {k: round(v, 2) for k, v in digital_by_platform.items()},
        "total_digital_today": total_digital_today,
        # AR collections — split by fund source AND by same-day vs older
        "ar_payments": ar_payments,
        "total_ar_received": total_ar_received,
        "total_ar_same_day": total_ar_same_day,
        "total_ar_older": total_ar_older,
        "total_cash_ar": total_cash_ar,
        "total_digital_ar": total_digital_ar,
        # Credit today (info)
        "credit_sales_today": credit_sales_today,
        "total_credit_today": total_credit_today,
        "total_credit_invoice_value": total_credit_invoice_value,
        # Expenses — split by fund source
        "expenses": expenses,
        "total_expenses": total_expenses,
        "total_cashier_expenses": total_cashier_expenses,
        "total_safe_expenses": total_safe_expenses,
        "total_digital_expenses": total_digital_expenses,
        "total_cash_in": round(total_cash_in, 2),
        "expected_counter": expected_counter,
        # Fund transfers affecting cashier
        "fund_transfers_today": [
            {"type": ft["transfer_type"], "amount": float(ft.get("amount", 0)),
             "note": ft.get("note", ""), "authorized_by": ft.get("authorized_by", ""),
             "target_wallet": ft.get("target_wallet", ""),
             "date": ft.get("date", ""),
             "time": ft.get("created_at", "")[-19:-10] if ft.get("created_at") else ""}
            for ft in fund_transfers_today
            if ft.get("transfer_type") in ("capital_add", "safe_to_cashier", "cashier_to_safe")
        ],
        "capital_to_cashier": capital_to_cashier,
        "safe_to_cashier": safe_to_cashier,
        "cashier_to_safe": cashier_to_safe,
        "net_fund_transfers": net_fund_transfers,
        # ── Iter 243.4: Net sales (gross profit) ─────────────────────────────
        "net_sales_today": await _compute_net_sales_today(branch_id, date),
        # ── Iter 243.4: Employee advance running totals ───────────────────────
        "employee_advances_today": await _compute_employee_advances_today(branch_id, date),
        # ── Pending stock releases (informational warning) ─────────────────
        "pending_stock_releases": await _get_pending_releases_summary(branch_id),
        # ── Negative stock warning ───────────────────────────────────────────
        "negative_stock": await _get_negative_stock_summary(branch_id),
        # ── Discount breakdown by product (today) ──────────────────────────
        "discount_breakdown": await _get_discount_breakdown(branch_id, date),
        # ── Price change events today (Price Match) ─────────────────────────
        "price_changes_today": await _get_price_changes_today(branch_id, date),
        # ── Interest / Penalty invoices created today ────────────────────────
        "interest_invoices_today": [
            {
                "invoice_number": inv.get("invoice_number", ""),
                "customer_name": inv.get("customer_name", ""),
                "customer_id": inv.get("customer_id", ""),
                "sale_type": inv.get("sale_type", ""),
                "grand_total": round(float(inv.get("grand_total", 0)), 2),
                "balance": round(float(inv.get("balance", 0)), 2),
                "amount_paid": round(float(inv.get("amount_paid", 0)), 2),
                "manual_interest": inv.get("manual_interest", False),
            }
            for inv in await db.invoices.find(
                {"branch_id": branch_id, "order_date": date,
                 "sale_type": {"$in": ["interest_charge", "penalty_charge"]},
                 "status": {"$ne": "voided"}},
                {"_id": 0, "invoice_number": 1, "customer_name": 1, "customer_id": 1,
                 "sale_type": 1, "grand_total": 1, "balance": 1, "amount_paid": 1, "manual_interest": 1}
            ).to_list(200)
        ],
        # ── AR payment stats (method breakdown + interest collected + discounts) ─
        "ar_payment_by_method": {
            m: {"total": round(sum(p["amount_paid"] for p in ar_payments if p.get("method") == m), 2),
                "count": sum(1 for p in ar_payments if p.get("method") == m)}
            for m in {p.get("method", "Cash") for p in ar_payments if p.get("fund_source") not in ("discount",)}
        },
        "ar_interest_collected": round(sum(p.get("interest_paid", 0) for p in ar_payments), 2),
        "ar_discount_today": round(sum(
            p["amount_paid"] for p in ar_payments if p.get("fund_source") == "discount"
        ), 2),
    }


@router.get("/daily-log")
async def get_daily_log(user=Depends(get_current_user), branch_id: Optional[str] = None, date: Optional[str] = None):
    """
    Get daily sales log split into:
      - cash_entries: sequential cash sales with cash-only running total
      - credit_invoices: today's credit/partial invoices with full item details (for AR section)
      - summary: totals by section and category
    """
    if not date:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    query = {"date": date, "voided": {"$ne": True}}
    if branch_id:
        query["branch_id"] = branch_id

    all_entries = await db.sales_log.find(query, {"_id": 0}).sort("sequence", 1).to_list(10000)

    # Credit/partial invoices with full item details (fetch early for backward compat lookup)
    inv_query = {"order_date": date, "payment_type": {"$in": ["credit", "partial"]}, "status": {"$ne": "voided"}}
    if branch_id:
        inv_query["branch_id"] = branch_id
    credit_invoices = await db.invoices.find(inv_query, {"_id": 0}).sort("created_at", 1).to_list(500)

    # Build invoice lookup for backward compat — old partial entries without metadata
    partial_invoice_lookup = {}
    for inv in credit_invoices:
        if inv.get("payment_type") == "partial":
            partial_invoice_lookup[inv.get("invoice_number", "")] = {
                "amount_paid": float(inv.get("amount_paid", 0)),
                "grand_total": float(inv.get("grand_total", 0)),
                "balance": float(inv.get("balance", 0)),
            }

    # Separate cash vs credit entries (split cash portion counts as cash, partial cash portion counts as cash)
    cash_entries = []
    for e in all_entries:
        pm = (e.get("payment_method") or "cash").lower()
        if pm == "cash":
            cash_entries.append(e)
        elif pm == "split":
            # Split: compute cash portion of this item
            gt = float(e.get("split_grand_total", 0))
            cash_ratio = float(e.get("split_cash_amount", 0)) / gt if gt > 0 else 0
            cash_portion = round(float(e.get("line_total", 0)) * cash_ratio, 2)
            e["_split_cash_portion"] = cash_portion
            cash_entries.append(e)
        elif pm == "partial":
            # Partial: compute cash portion of this item (rest goes to credit/AR)
            gt = float(e.get("partial_grand_total", 0))
            cash_amt = float(e.get("partial_cash_amount", 0))
            if gt <= 0:
                # Backward compat: look up from invoice data
                inv_data = partial_invoice_lookup.get(e.get("invoice_number", ""))
                if inv_data and inv_data["grand_total"] > 0:
                    gt = inv_data["grand_total"]
                    cash_amt = inv_data["amount_paid"]
            cash_ratio = cash_amt / gt if gt > 0 else 0
            cash_portion = round(float(e.get("line_total", 0)) * cash_ratio, 2)
            e["_partial_cash_portion"] = cash_portion
            e["_partial_credit_portion"] = round(float(e.get("line_total", 0)) - cash_portion, 2)
            cash_entries.append(e)

    # Compute cash-only running total
    cash_running = 0.0
    for e in cash_entries:
        pm = (e.get("payment_method") or "cash").lower()
        if pm == "split":
            cash_running += float(e.get("_split_cash_portion", 0))
        elif pm == "partial":
            cash_running += float(e.get("_partial_cash_portion", 0))
        else:
            cash_running += float(e.get("line_total", 0))
        e["cash_running_total"] = round(cash_running, 2)

    # Cash by category
    cash_by_category = {}
    for e in cash_entries:
        cat = e.get("category") or "General"
        pm = (e.get("payment_method") or "cash").lower()
        if pm == "split":
            amt = float(e.get("_split_cash_portion", 0))
        elif pm == "partial":
            amt = float(e.get("_partial_cash_portion", 0))
        else:
            amt = float(e.get("line_total", 0))
        cash_by_category[cat] = round(cash_by_category.get(cat, 0.0) + amt, 2)
    cash_by_category = dict(sorted(cash_by_category.items(), key=lambda x: -x[1]))

    # For partial invoices: split amount_paid (cash) and balance (credit)
    total_partial_cash = round(sum(
        float(inv.get("amount_paid", 0))
        for inv in credit_invoices if inv.get("payment_type") == "partial"
    ), 2)
    total_credit_balance = round(sum(
        float(inv.get("balance", 0))
        for inv in credit_invoices
    ), 2)

    total_cash = round(sum(
        float(e.get("_split_cash_portion", 0)) if (e.get("payment_method") or "cash").lower() == "split"
        else float(e.get("_partial_cash_portion", 0)) if (e.get("payment_method") or "cash").lower() == "partial"
        else float(e.get("line_total", 0))
        for e in cash_entries
    ), 2)
    total_credit = round(sum(float(inv.get("balance", 0)) for inv in credit_invoices), 2)
    total_all = round(sum(float(e.get("line_total", 0)) for e in all_entries), 2)

    # Payment method breakdown — decompose "split" into cash + digital, "partial" into cash + credit
    by_payment_method = {}
    for e in all_entries:
        pm = (e.get("payment_method") or "cash").lower()
        lt = float(e.get("line_total", 0))
        if pm == "split":
            # Decompose into cash and digital portions
            gt = float(e.get("split_grand_total", 0))
            cash_ratio = float(e.get("split_cash_amount", 0)) / gt if gt > 0 else 0.5
            cash_portion = round(lt * cash_ratio, 2)
            digital_portion = round(lt - cash_portion, 2)
            dp = (e.get("split_digital_platform") or "digital").lower()
            # Cash portion
            if "cash" not in by_payment_method:
                by_payment_method["cash"] = {"total": 0.0, "count": 0}
            by_payment_method["cash"]["total"] = round(by_payment_method["cash"]["total"] + cash_portion, 2)
            by_payment_method["cash"]["count"] += 1
            # Digital portion
            if dp not in by_payment_method:
                by_payment_method[dp] = {"total": 0.0, "count": 0}
            by_payment_method[dp]["total"] = round(by_payment_method[dp]["total"] + digital_portion, 2)
            by_payment_method[dp]["count"] += 1
        elif pm == "partial":
            # Decompose into cash and credit portions
            gt = float(e.get("partial_grand_total", 0))
            cash_amt = float(e.get("partial_cash_amount", 0))
            if gt <= 0:
                # Backward compat: look up from invoice data
                inv_data = partial_invoice_lookup.get(e.get("invoice_number", ""))
                if inv_data and inv_data["grand_total"] > 0:
                    gt = inv_data["grand_total"]
                    cash_amt = inv_data["amount_paid"]
            cash_ratio = cash_amt / gt if gt > 0 else 0
            cash_portion = round(lt * cash_ratio, 2)
            credit_portion = round(lt - cash_portion, 2)
            # Cash portion → counted as cash
            if cash_portion > 0:
                if "cash" not in by_payment_method:
                    by_payment_method["cash"] = {"total": 0.0, "count": 0}
                by_payment_method["cash"]["total"] = round(by_payment_method["cash"]["total"] + cash_portion, 2)
                by_payment_method["cash"]["count"] += 1
            # Credit portion → counted as credit
            if credit_portion > 0:
                if "credit" not in by_payment_method:
                    by_payment_method["credit"] = {"total": 0.0, "count": 0}
                by_payment_method["credit"]["total"] = round(by_payment_method["credit"]["total"] + credit_portion, 2)
                by_payment_method["credit"]["count"] += 1
        else:
            if pm not in by_payment_method:
                by_payment_method[pm] = {"total": 0.0, "count": 0}
            by_payment_method[pm]["total"] = round(by_payment_method[pm]["total"] + lt, 2)
            by_payment_method[pm]["count"] += 1

    return {
        "entries": all_entries,
        "cash_entries": cash_entries,
        "credit_invoices": credit_invoices,
        "date": date,
        "count": len(all_entries),
        "summary": {
            "total_cash": total_cash,
            "total_partial_cash": total_partial_cash,
            "total_cash_all": round(total_cash + total_partial_cash, 2),
            "total_credit": total_credit,
            "total_credit_balance": total_credit_balance,
            "total_all": total_all,
            "grand_total": total_all,
            "cash_count": len(cash_entries),
            "credit_invoice_count": len(credit_invoices),
            "cash_by_category": cash_by_category,
            "by_payment_method": by_payment_method,
        },
    }


@router.get("/daily-report")
async def get_daily_report(user=Depends(get_current_user), branch_id: Optional[str] = None, date: Optional[str] = None):
    """Get daily profit report."""
    check_perm(user, "reports", "view")
    
    if not date:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    
    log_query = {"date": date, "voided": {"$ne": True}}
    if branch_id:
        log_query["branch_id"] = branch_id
    
    # Sales by category
    cat_pipeline = [
        {"$match": log_query},
        {"$group": {"_id": "$category", "total": {"$sum": "$line_total"}, "count": {"$sum": "$quantity"}}}
    ]
    cat_results = await db.sales_log.aggregate(cat_pipeline).to_list(100)
    sales_by_category = {r["_id"] or "Uncategorized": {"total": r["total"], "count": r["count"]} for r in cat_results}
    total_revenue = sum(r["total"] for r in cat_results)
    
    # COGS - get cost prices for all sold products
    log_entries = await db.sales_log.find(log_query, {"_id": 0}).to_list(10000)
    total_cogs = 0
    for entry in log_entries:
        if entry.get("product_id"):
            prod = await db.products.find_one({"id": entry["product_id"]}, {"_id": 0, "cost_price": 1})
            if prod:
                total_cogs += (prod.get("cost_price", 0) * entry.get("quantity", 0))
    total_cogs = round(total_cogs, 2)
    
    # Expenses — split into real P&L expenses vs credit-generating items vs inventory purchases
    # Credit-generating categories create AR invoices (receivables, not real losses):
    #   "Farm Expense" → farm service billed to customer (invoice created)
    #   "Customer Cash-out" / "Customer Cash Out" → cash given to customer (invoice created)
    #   "Employee Advance" → advance to employee (to be deducted from salary)
    # Inventory purchases are balance-sheet movements (cash → inventory asset):
    #   "Purchase Payment" → PO cash payment (inventory bought, NOT an operating expense)
    #   "Supplier Payment" → AP payment on terms PO (same)
    # These must NOT be in P&L because COGS already captures inventory cost when items are sold.

    exp_query = {"date": date, "voided": {"$ne": True}}
    if branch_id:
        exp_query["branch_id"] = branch_id
    expenses_raw = await db.expenses.find(exp_query, {"_id": 0}).to_list(500)

    # Enrich Employee Advance expenses with CA limit info
    _emp_limit_cache_report = {}
    month_prefix = date[:7]
    expenses = []
    for e in expenses_raw:
        exp = dict(e)
        if e.get("category") == "Employee Advance" and e.get("employee_id"):
            month_res = await db.expenses.aggregate([
                {"$match": {"category": "Employee Advance", "employee_id": e["employee_id"],
                            "date": {"$gte": f"{month_prefix}-01", "$lte": f"{month_prefix}-31"}}},
                {"$group": {"_id": None, "total": {"$sum": "$amount"}}}
            ]).to_list(1)
            exp["monthly_ca_total"] = round(month_res[0]["total"] if month_res else 0, 2)
            eid = e["employee_id"]
            if eid not in _emp_limit_cache_report:
                emp_doc = await db.employees.find_one({"id": eid}, {"_id": 0, "monthly_ca_limit": 1})
                _emp_limit_cache_report[eid] = float(emp_doc.get("monthly_ca_limit", 0)) if emp_doc else 0
            exp["monthly_ca_limit"] = _emp_limit_cache_report[eid]
            exp["is_over_ca"] = exp["monthly_ca_limit"] > 0 and exp["monthly_ca_total"] > exp["monthly_ca_limit"]
        expenses.append(exp)

    real_expenses = []         # Actual P&L expenses (utilities, rent, misc — reduces net profit)
    credit_expenses = []       # Credits extended to customers (AR — money comes back)
    advance_expenses = []      # Employee advances (asset — comes back via salary deduction)
    inventory_expenses = []    # Inventory purchases (balance sheet — NOT P&L, COGS covers this)

    for e in expenses:
        cat = (e.get("category") or "").lower().strip()
        if "employee advance" in cat:
            advance_expenses.append(e)
        elif "farm expense" in cat or "customer cash" in cat:
            credit_expenses.append(e)
        elif "purchase payment" in cat or "supplier payment" in cat:
            inventory_expenses.append(e)
        else:
            real_expenses.append(e)

    total_real_expenses = round(sum(float(e.get("amount", 0)) for e in real_expenses), 2)
    total_credit_expenses = round(sum(float(e.get("amount", 0)) for e in credit_expenses), 2)
    total_advance_expenses = round(sum(float(e.get("amount", 0)) for e in advance_expenses), 2)
    total_inventory_expenses = round(sum(float(e.get("amount", 0)) for e in inventory_expenses), 2)
    # Legacy field: sum of ALL for backward compat
    total_expenses = round(total_real_expenses + total_credit_expenses + total_advance_expenses + total_inventory_expenses, 2)
    
    # Also fetch today's AR invoices created from cash outs / farm expenses (for display)
    ar_today_query = {
        "order_date": date,
        "sale_type": {"$in": ["cash_advance", "farm_expense"]},
        "status": {"$ne": "voided"}
    }
    if branch_id:
        ar_today_query["branch_id"] = branch_id
    ar_credits_today = await db.invoices.find(ar_today_query, {"_id": 0,
        "invoice_number": 1, "customer_name": 1, "sale_type": 1, "grand_total": 1, "items": 1}).to_list(200)
    total_ar_credits_today = round(sum(float(inv.get("grand_total", 0)) for inv in ar_credits_today), 2)
    
    # Credit collections: only payments on OLD invoices (not today's new sales)
    # Excludes voided payments so modify/void cycles don't double-count.
    pay_pipeline = [
        {"$match": {"status": {"$ne": "voided"}}},
        {"$unwind": "$payments"},
        {"$match": {
            "payments.date": date,
            "payments.voided": {"$ne": True},
        }},
    ]
    if branch_id:
        pay_pipeline[0]["$match"]["branch_id"] = branch_id
    pay_pipeline.append({"$project": {"_id": 0, "invoice_number": 1, "customer_name": 1, "order_date": 1, "payment": "$payments"}})
    all_payments_today = await db.invoices.aggregate(pay_pipeline).to_list(500)
    
    # Only count payments on older invoices as credit collections (NOT same-day sales)
    credit_collections = [p for p in all_payments_today if p.get("order_date") != date]
    total_credit_collections = sum(p["payment"]["amount"] for p in credit_collections)
    
    # Total cash from all invoice payments (for cash flow)
    total_cash_from_invoices = sum(p["payment"]["amount"] for p in all_payments_today)
    
    gross_profit = round(total_revenue - total_cogs, 2)
    # Net profit = Sales - COGS - Operating Expenses
    net_profit = round(gross_profit - total_real_expenses, 2)
    
    # Get real-time cashier wallet balance
    wallet_query = {"type": "cashier", "active": True}
    if branch_id:
        wallet_query["branch_id"] = branch_id
    cashier_wallet = await db.fund_wallets.find_one(wallet_query, {"_id": 0})
    cashier_balance = cashier_wallet["balance"] if cashier_wallet else 0
    
    return {
        "date": date,
        "new_sales_today": total_revenue,
        "total_cogs": total_cogs,
        "gross_profit": gross_profit,
        # Correct net profit — only real expenses subtracted
        "total_expenses": total_real_expenses,
        "net_profit": net_profit,
        "sales_by_category": sales_by_category,
        # Split expense lists
        "expenses": real_expenses,
        "credit_expenses": credit_expenses,           # Farm + Cash-out (AR — NOT a loss)
        "advance_expenses": advance_expenses,          # Employee advances (asset)
        "inventory_expenses": inventory_expenses,      # PO/Supplier payments (balance sheet — NOT P&L)
        "total_credit_expenses": total_credit_expenses,
        "total_advance_expenses": total_advance_expenses,
        "total_inventory_expenses": total_inventory_expenses,
        # AR credits created today from cash outs / farm service (invoice-backed)
        "ar_credits_today": ar_credits_today,
        "total_ar_credits_today": total_ar_credits_today,
        # Legacy sum (all expenses) for any existing UI that needs it
        "total_all_expenses": total_expenses,
        "credit_collections": credit_collections,
        "total_credit_collections": total_credit_collections,
        "total_cash_from_invoices": total_cash_from_invoices,
        "cashier_wallet_balance": round(cashier_balance, 2),
        "transaction_count": len(log_entries),
    }


@router.get("/daily-close/{date}")
async def get_daily_close(date: str, user=Depends(get_current_user), branch_id: Optional[str] = None):
    """Get status of daily close for a date.

    Response is augmented with any non-voided reconciliation adjustments so
    the on-screen Z-Report and the Z-Report PDF render `adjusted_over_short`
    in addition to the original `over_short`. The original closing values
    (`expected_counter`, `actual_cash`, `over_short`) are NEVER mutated —
    corrections live exclusively in `daily_closing_adjustments`.
    """
    query = {"date": date}
    if branch_id:
        query["branch_id"] = branch_id
    closing = await db.daily_closings.find_one(query, {"_id": 0})
    if not closing:
        return {"status": "open", "date": date}

    adjustments = await db.daily_closing_adjustments.find(
        {"closing_id": closing["id"], "voided": {"$ne": True}},
        {"_id": 0},
    ).sort("created_at", 1).to_list(50)
    adj_total = round(sum(float(a.get("amount", 0)) for a in adjustments), 2)
    closing["adjustments"] = adjustments
    closing["adjustment_total"] = adj_total
    closing["adjusted_over_short"] = round(float(closing.get("over_short", 0)) + adj_total, 2)
    return closing


# ── Reconciliation Adjustments on Closed Days ────────────────────────────────
# Admin-only override to correct a closed day's Net Over/Short when the
# original Expected/Actual values are themselves wrong (classic case:
# migration carry-over contaminating early-period drawer math). Rather than
# mutate the historical closing record, we add an immutable adjustment
# entry that shifts the effective Over/Short. Every create and void is
# audit-logged; the original closing doc remains verbatim so auditors can
# reconstruct both the raw-as-recorded numbers and the corrected view.
@router.get("/daily-closings/{closing_id}/adjustments")
async def list_closing_adjustments(closing_id: str, user=Depends(get_current_user)):
    check_perm(user, "reports", "view")
    closing = await db.daily_closings.find_one({"id": closing_id}, {"_id": 0, "id": 1})
    if not closing:
        raise HTTPException(404, "Closing not found")
    rows = await db.daily_closing_adjustments.find(
        {"closing_id": closing_id}, {"_id": 0},
    ).sort("created_at", 1).to_list(100)
    return {"adjustments": rows}


@router.post("/daily-closings/{closing_id}/adjustments")
async def create_closing_adjustment(
    closing_id: str, data: dict, user=Depends(get_current_user),
):
    """Create a reconciliation adjustment on a closed day (admin-only)."""
    if user.get("role") != "admin":
        raise HTTPException(403, "Only admins can add closing adjustments")

    try:
        amount = float(data.get("amount", 0))
    except (TypeError, ValueError):
        raise HTTPException(400, "Invalid amount")
    if amount == 0:
        raise HTTPException(400, "Adjustment amount must be non-zero")

    reason = (data.get("reason") or "").strip()
    if len(reason) < 3:
        raise HTTPException(400, "Reason is required (min 3 characters) for audit trail")

    closing = await db.daily_closings.find_one({"id": closing_id}, {"_id": 0})
    if not closing:
        raise HTTPException(404, "Closing not found")

    adj = {
        "id": new_id(),
        "closing_id": closing_id,
        "branch_id": closing.get("branch_id"),
        "date": closing.get("date"),
        "amount": round(amount, 2),
        "reason": reason,
        "original_over_short": float(closing.get("over_short", 0)),
        "original_expected_counter": float(closing.get("expected_counter", 0)),
        "original_actual_cash": float(closing.get("actual_cash", 0)),
        "created_by": user["id"],
        "created_by_name": user.get("full_name", user.get("username", "")),
        "created_at": now_iso(),
        "voided": False,
    }
    await db.daily_closing_adjustments.insert_one(adj)
    adj.pop("_id", None)

    await db.audit_log.insert_one({
        "id": new_id(),
        "type": "closing_adjustment_created",
        "entity_type": "daily_closing",
        "entity_id": closing_id,
        "description": (
            f"Closing adjustment +P{amount:,.2f} on {closing.get('date')} "
            f"(original over/short P{closing.get('over_short', 0):,.2f}): {reason}"
        ),
        "metadata": {
            "adjustment_id": adj["id"],
            "amount": adj["amount"],
            "reason": reason,
            "date": closing.get("date"),
            "branch_id": closing.get("branch_id"),
            "original_over_short": adj["original_over_short"],
            "new_adjusted_over_short": round(adj["original_over_short"] + amount, 2),
        },
        "user_id": user["id"],
        "user_name": adj["created_by_name"],
        "created_at": now_iso(),
    })
    return adj


@router.post("/daily-closings/{closing_id}/adjustments/{adj_id}/void")
async def void_closing_adjustment(
    closing_id: str, adj_id: str, data: dict, user=Depends(get_current_user),
):
    """Void a previously-created adjustment (admin-only). Original record
    is retained with `voided=True` so the history is never destroyed."""
    if user.get("role") != "admin":
        raise HTTPException(403, "Only admins can void closing adjustments")

    reason = (data.get("reason") or "").strip()
    if len(reason) < 3:
        raise HTTPException(400, "Reason is required (min 3 characters)")

    adj = await db.daily_closing_adjustments.find_one({"id": adj_id, "closing_id": closing_id}, {"_id": 0})
    if not adj:
        raise HTTPException(404, "Adjustment not found")
    if adj.get("voided"):
        raise HTTPException(400, "Adjustment already voided")

    await db.daily_closing_adjustments.update_one(
        {"id": adj_id},
        {"$set": {
            "voided": True,
            "voided_by": user["id"],
            "voided_by_name": user.get("full_name", user.get("username", "")),
            "voided_at": now_iso(),
            "voided_reason": reason,
        }},
    )
    await db.audit_log.insert_one({
        "id": new_id(),
        "type": "closing_adjustment_voided",
        "entity_type": "daily_closing",
        "entity_id": closing_id,
        "description": (
            f"Closing adjustment P{float(adj.get('amount', 0)):,.2f} on "
            f"{adj.get('date')} voided: {reason}"
        ),
        "metadata": {
            "adjustment_id": adj_id,
            "void_reason": reason,
            "voided_amount": float(adj.get("amount", 0)),
        },
        "user_id": user["id"],
        "user_name": user.get("full_name", user.get("username", "")),
        "created_at": now_iso(),
    })
    return {"voided": True}




@router.get("/daily-close-preview/batch")
async def batch_close_preview(
    user=Depends(get_current_user),
    branch_id: Optional[str] = None,
    dates: Optional[str] = None,
):
    """Preview aggregated data across multiple dates for batch closing.
    dates is a comma-separated list of dates: 2026-01-01,2026-01-02,2026-01-03"""
    check_perm(user, "reports", "view")
    if not branch_id or not dates:
        raise HTTPException(status_code=400, detail="branch_id and dates required")

    date_list = sorted([d.strip() for d in dates.split(",") if d.strip()])
    first_date = date_list[0]
    last_date = date_list[-1]
    from datetime import timedelta
    month_prefix = first_date[:7]
    date_filter = {"$in": date_list}

    # Starting float
    #
    # CLOSE-WIZARD FIX (Feb 2026): use the most-recent closed day BEFORE
    # `first_date` — not exactly `first_date - 1`. Group closings exist
    # precisely because users skipped one or more closes; demanding an
    # exact day-before match falls through to `wallet.balance`, and if
    # the wallet has drifted (manual reset, missed sync, fresh branch)
    # the batch preview reports expected_counter=0 for a ₱1.5M batch.
    # Mirroring the single-day query keeps the anchor stable across
    # arbitrary skip-gaps and matches users' mental model: "the last
    # known good cash baseline."
    prev_close = await db.daily_closings.find_one(
        {"branch_id": branch_id, "date": {"$lt": first_date}, "status": "closed"},
        {"_id": 0},
        sort=[("date", -1)],
    )
    has_prev_close = bool(prev_close)
    wallet = await db.fund_wallets.find_one({"branch_id": branch_id, "type": "cashier", "active": True}, {"_id": 0})
    starting_float = float(prev_close.get("cash_to_drawer", 0)) if prev_close else float(wallet["balance"] if wallet else 0)

    safe = await db.fund_wallets.find_one({"branch_id": branch_id, "type": "safe", "active": True}, {"_id": 0})
    safe_balance = 0.0
    if safe:
        lots = await db.safe_lots.find({"wallet_id": safe["id"], "remaining_amount": {"$gt": 0}}, {"_id": 0}).to_list(500)
        safe_balance = sum(l["remaining_amount"] for l in lots)

    # Cash sales
    cash_sales_agg = await db.sales_log.aggregate([
        # B-3 fix: exclude partial-line rows; partial cash is captured via
        # ar_pipeline / partial_total path below.
        {"$match": {"branch_id": branch_id, "date": date_filter, "voided": {"$ne": True},
                    "payment_method": {"$regex": "^cash$", "$options": "i"},
                    "partial_grand_total": {"$exists": False}}},
        {"$group": {"_id": "$category", "total": {"$sum": "$line_total"}}}
    ]).to_list(100)
    sales_by_category = {r["_id"] or "General": round(r["total"], 2) for r in cash_sales_agg}
    total_cash_sales = round(sum(sales_by_category.values()), 2)

    # Partial payments
    partial_invoices = await db.invoices.find(
        {"branch_id": branch_id, "order_date": date_filter, "payment_type": "partial", "status": {"$ne": "voided"}},
        {"_id": 0, "amount_paid": 1}
    ).to_list(500)
    partial_total = round(sum(float(inv.get("amount_paid", 0)) for inv in partial_invoices), 2)

    # AR collections — need per-payment detail to split cash vs digital.
    # Exclude voided payments so modify/void cycles don't inflate totals.
    # CLOSE WIZARD REFACTOR (Feb 2026): include ALL payments dated in this
    # batch, even on invoices created within the batch (initial partial-sale
    # cash legs). The expanded `total_cash_ar` subsumes `partial_total`,
    # which is dropped from the cash_in math below.
    # CRITICAL FILTER: restrict to AR-semantic invoices only (credit /
    # partial / interest / penalty). Pure cash/digital/split sales also push
    # payment records dated today but those are already in `total_cash_sales`
    # / `total_split_cash`; including them here would double-count.
    ar_pipeline = [
        {"$match": {
            "branch_id": branch_id,
            "status": {"$ne": "voided"},
            "$or": [
                {"payment_type": {"$in": ["credit", "partial"]}},
                {"sale_type": {"$in": ["interest_charge", "penalty_charge"]}},
            ],
        }},
        {"$unwind": "$payments"},
        {"$match": {
            "payments.date": date_filter,
            "payments.voided": {"$ne": True},
        }},
        {"$project": {"_id": 0, "payment": "$payments", "order_date": 1, "payment_type": 1}}
    ]
    ar_payments_raw = await db.invoices.aggregate(ar_pipeline).to_list(500)
    total_ar_received = round(sum(float(p.get("payment", {}).get("amount", 0)) for p in ar_payments_raw), 2)
    total_cash_ar = round(sum(
        float(p.get("payment", {}).get("amount", 0)) for p in ar_payments_raw
        if p.get("payment", {}).get("fund_source", "cashier") == "cashier"
    ), 2)
    total_digital_ar = round(total_ar_received - total_cash_ar, 2)

    # Expenses — split by fund source
    expenses_raw = await db.expenses.find({"branch_id": branch_id, "date": date_filter, "voided": {"$ne": True}}, {"_id": 0}).to_list(500)
    expenses = []
    _emp_limit_cache = {}
    for e in expenses_raw:
        exp = dict(e)
        if e.get("category") == "Employee Advance" and e.get("employee_id"):
            month_res = await db.expenses.aggregate([
                {"$match": {"branch_id": branch_id, "category": "Employee Advance",
                            "employee_id": e["employee_id"],
                            "date": {"$gte": f"{month_prefix}-01", "$lte": f"{month_prefix}-31"}}},
                {"$group": {"_id": None, "total": {"$sum": "$amount"}}}
            ]).to_list(1)
            exp["monthly_ca_total"] = round(month_res[0]["total"] if month_res else 0, 2)
            eid = e["employee_id"]
            if eid not in _emp_limit_cache:
                emp_doc = await db.employees.find_one({"id": eid}, {"_id": 0, "monthly_ca_limit": 1})
                _emp_limit_cache[eid] = float(emp_doc.get("monthly_ca_limit", 0)) if emp_doc else 0
            exp["monthly_ca_limit"] = _emp_limit_cache[eid]
            exp["is_over_ca"] = exp["monthly_ca_limit"] > 0 and exp["monthly_ca_total"] > exp["monthly_ca_limit"]
        expenses.append(exp)
    total_expenses = round(sum(float(e.get("amount", 0)) for e in expenses), 2)
    total_cashier_expenses = round(sum(
        float(e.get("amount", 0)) for e in expenses
        if e.get("fund_source", "cashier") == "cashier"
    ), 2)
    total_safe_expenses = round(sum(
        float(e.get("amount", 0)) for e in expenses
        if e.get("fund_source") == "safe"
    ), 2)
    total_digital_expenses = round(total_expenses - total_cashier_expenses - total_safe_expenses, 2)
    split_invs_batch = await db.invoices.find(
        {"branch_id": branch_id, "order_date": date_filter,
         "fund_source": "split", "status": {"$ne": "voided"}},
        {"_id": 0, "cash_amount": 1}
    ).to_list(500)
    total_split_cash = round(sum(float(inv.get("cash_amount", 0)) for inv in split_invs_batch), 2)

    # ── Fund transfers affecting cashier drawer ─────────────────────────
    ft_query_batch = {"branch_id": branch_id, "$or": [
        {"date": date_filter},
        {"date": {"$exists": False}, "created_at": {"$gte": f"{first_date}T00:00:00", "$lt": f"{last_date}T23:59:59"}}
    ]}
    fund_transfers_batch = await db.fund_transfers.find(ft_query_batch, {"_id": 0}).to_list(200)
    capital_to_cashier = round(sum(
        float(ft.get("amount", 0)) for ft in fund_transfers_batch
        if ft.get("transfer_type") == "capital_add" and ft.get("target_wallet", "cashier") == "cashier"
    ), 2)
    safe_to_cashier = round(sum(
        float(ft.get("amount", 0)) for ft in fund_transfers_batch
        if ft.get("transfer_type") == "safe_to_cashier"
    ), 2)
    cashier_to_safe = round(sum(
        float(ft.get("amount", 0)) for ft in fund_transfers_batch
        if ft.get("transfer_type") == "cashier_to_safe"
    ), 2)
    net_fund_transfers = round(capital_to_cashier + safe_to_cashier - cashier_to_safe, 2)

    # CLOSE WIZARD REFACTOR (Feb 2026): same-day partial cash now lives in
    # total_cash_ar via the expanded payments-today aggregation.
    total_cash_in = total_cash_sales + total_cash_ar + total_split_cash
    if has_prev_close:
        expected_counter = round(starting_float + total_cash_in + net_fund_transfers - total_cashier_expenses, 2)
    else:
        expected_counter = round(float(wallet["balance"]) if wallet else 0, 2)

    # Digital payments
    digital_invs = await db.invoices.find(
        {"branch_id": branch_id, "order_date": date_filter,
         "fund_source": {"$in": ["digital", "split"]}, "status": {"$ne": "voided"}},
        {"_id": 0, "digital_amount": 1, "digital_platform": 1, "fund_source": 1, "amount_paid": 1,
         "invoice_number": 1, "customer_name": 1, "digital_ref_number": 1}
    ).to_list(500)
    digital_by_platform = {}
    total_digital = 0.0
    digital_sales_list = []
    for inv in digital_invs:
        amt = float(inv.get("digital_amount", 0) if inv.get("fund_source") == "split" and inv.get("digital_amount") else inv.get("amount_paid", 0))
        platform = inv.get("digital_platform", "Digital") or "Digital"
        digital_by_platform[platform] = round(digital_by_platform.get(platform, 0) + amt, 2)
        total_digital = round(total_digital + amt, 2)
        digital_sales_list.append({
            "invoice_number": inv.get("invoice_number"), "customer_name": inv.get("customer_name"),
            "platform": platform, "ref_number": inv.get("digital_ref_number", ""), "amount": amt
        })

    # Credit sales
    credit_invoices = await db.invoices.find(
        {"branch_id": branch_id, "order_date": date_filter,
         "payment_type": {"$in": ["credit", "partial"]}, "status": {"$ne": "voided"}},
        {"_id": 0, "customer_name": 1, "invoice_number": 1, "grand_total": 1, "balance": 1, "payment_type": 1, "sale_type": 1}
    ).to_list(500)
    total_credit_today = round(sum(float(inv.get("balance", 0)) for inv in credit_invoices), 2)

    # Per-day breakdown
    per_day_sales = await db.sales_log.aggregate([
        {"$match": {"branch_id": branch_id, "date": date_filter, "voided": {"$ne": True}}},
        {"$group": {"_id": {"date": "$date", "payment_method": "$payment_method"},
                    "total": {"$sum": "$line_total"}, "count": {"$sum": 1}}}
    ]).to_list(500)
    daily_breakdown = {}
    for r in per_day_sales:
        d = r["_id"]["date"]
        pm = r["_id"]["payment_method"]
        if d not in daily_breakdown:
            daily_breakdown[d] = {"sales_by_method": {}, "sales_total": 0, "expenses_total": 0}
        daily_breakdown[d]["sales_by_method"][pm] = round(r["total"], 2)
        daily_breakdown[d]["sales_total"] = round(daily_breakdown[d]["sales_total"] + r["total"], 2)
    for d in date_list:
        if d not in daily_breakdown:
            daily_breakdown[d] = {"sales_by_method": {}, "sales_total": 0, "expenses_total": 0}
        daily_breakdown[d]["expenses_total"] = round(
            sum(float(e.get("amount", 0)) for e in expenses if e.get("date") == d), 2)

    return {
        "dates": date_list,
        "date_from": first_date,
        "date_to": last_date,
        "starting_float": starting_float,
        "safe_balance": round(safe_balance, 2),
        "sales_by_category": sales_by_category,
        "total_cash_sales": total_cash_sales,
        "total_split_cash": total_split_cash,
        "total_partial_cash": partial_total,
        "total_ar_received": total_ar_received,
        "total_cash_ar": total_cash_ar,
        "total_digital_ar": total_digital_ar,
        "expenses": expenses,
        "total_expenses": total_expenses,
        "total_cashier_expenses": total_cashier_expenses,
        "total_safe_expenses": total_safe_expenses,
        "total_digital_expenses": total_digital_expenses,
        "total_cash_in": round(total_cash_in, 2),
        "capital_to_cashier": capital_to_cashier,
        "safe_to_cashier": safe_to_cashier,
        "cashier_to_safe": cashier_to_safe,
        "net_fund_transfers": net_fund_transfers,
        "fund_transfers_today": [
            {"type": ft["transfer_type"], "amount": float(ft.get("amount", 0)),
             "note": ft.get("note", ""), "authorized_by": ft.get("authorized_by", ""),
             "target_wallet": ft.get("target_wallet", ""),
             "date": ft.get("date", ""),
             "time": ft.get("created_at", "")[-19:-10] if ft.get("created_at") else ""}
            for ft in fund_transfers_batch
            if ft.get("transfer_type") in ("capital_add", "safe_to_cashier", "cashier_to_safe")
        ],
        "total_digital_today": total_digital,
        "digital_by_platform": digital_by_platform,
        "digital_sales_today": digital_sales_list,
        "total_credit_today": total_credit_today,
        "credit_invoices": credit_invoices,
        "daily_breakdown": daily_breakdown,
        # CLOSE WIZARD FIX (Feb 2026): expose the canonical reconciliation
        # numbers the wizard needs. Previously the batch preview computed
        # `expected_counter` then silently dropped it, leaving the frontend
        # to either invent a value or fall back to wallet.balance (which
        # produced "expected = 0" on ₱1.5M batches when the wallet had
        # drifted). The single-day preview already returns these — parity.
        "expected_counter": expected_counter,
        "has_prev_close":   has_prev_close,
    }


@router.post("/daily-close")
async def close_day(data: dict, user=Depends(get_current_user)):
    """Close accounts for a day. Requires admin PIN verification."""
    check_perm(user, "reports", "close_day")

    date = data["date"]
    branch_id = data["branch_id"]
    assert_branch_access(user, branch_id)

    # Admin PIN required for day close
    admin_pin = data.get("admin_pin", "")
    if user.get("role") != "admin":
        if not admin_pin:
            raise HTTPException(status_code=403, detail="Admin PIN required to close the day")
        from routes.verify import verify_pin_for_action
        verifier = await verify_pin_for_action(admin_pin, "daily_close")
        if not verifier:
            raise HTTPException(status_code=403, detail="Invalid admin PIN")

    existing = await db.daily_closings.find_one(
        {"date": date, "branch_id": branch_id, "status": "closed"}, {"_id": 0}
    )
    if existing:
        raise HTTPException(status_code=400, detail="Day already closed")

    # Pull all data (reuse preview logic)
    from datetime import timedelta
    month_prefix = date[:7]
    prev_close = await db.daily_closings.find_one(
        {"branch_id": branch_id, "date": {"$lt": date}, "status": "closed"},
        {"_id": 0},
        sort=[("date", -1)]
    )
    wallet = await db.fund_wallets.find_one({"branch_id": branch_id, "type": "cashier", "active": True}, {"_id": 0})
    starting_float = float(prev_close.get("cash_to_drawer", 0)) if prev_close else float(wallet["balance"] if wallet else 0)

    safe = await db.fund_wallets.find_one({"branch_id": branch_id, "type": "safe", "active": True}, {"_id": 0})
    safe_balance = 0.0
    if safe:
        lots = await db.safe_lots.find({"wallet_id": safe["id"], "remaining_amount": {"$gt": 0}}, {"_id": 0}).to_list(500)
        safe_balance = sum(l["remaining_amount"] for l in lots)

    cash_sales_agg = await db.sales_log.aggregate([
        # B-3 fix: exclude partial-line rows; partial cash is captured via
        # ar_pipeline / partial_total path below (mutating close_day path).
        {"$match": {"branch_id": branch_id, "date": date, "voided": {"$ne": True},
                    "payment_method": {"$regex": "^cash$", "$options": "i"},
                    "partial_grand_total": {"$exists": False}}},
        {"$group": {"_id": "$category", "total": {"$sum": "$line_total"}}}
    ]).to_list(100)
    sales_by_category = {r["_id"] or "General": round(r["total"], 2) for r in cash_sales_agg}
    total_cash_sales = round(sum(sales_by_category.values()), 2)

    partial_total = 0.0
    partial_invoices = await db.invoices.find(
        {"branch_id": branch_id, "order_date": date, "payment_type": "partial", "status": {"$ne": "voided"}},
        {"_id": 0, "amount_paid": 1}
    ).to_list(500)
    partial_total = round(sum(float(inv.get("amount_paid", 0)) for inv in partial_invoices), 2)

    # CLOSE WIZARD REFACTOR (Feb 2026): Include ALL payments dated today —
    # same-day partial-sale cash legs, same-day extra payments, AND payments
    # on older credit invoices. The expanded `total_cash_ar` now subsumes
    # what `partial_total` used to represent (we drop partial_total from the
    # cash_in math below to avoid double-counting).
    # CRITICAL FILTER: restrict to AR-semantic invoices only. Pure cash /
    # digital / split sales push payments dated today but those are already
    # in `total_cash_sales` / `total_split_cash`; including them here would
    # double-count and inflate `expected_counter`.
    ar_pipeline = [
        {"$match": {
            "branch_id": branch_id,
            "status": {"$ne": "voided"},
            "$or": [
                {"payment_type": {"$in": ["credit", "partial"]}},
                {"sale_type": {"$in": ["interest_charge", "penalty_charge"]}},
            ],
        }},
        {"$unwind": "$payments"},
        {"$match": {
            "payments.date": date,
            "payments.voided": {"$ne": True},
        }},
        {"$project": {"_id": 0, "customer_name": 1, "invoice_number": 1,
                      "sale_type": 1, "balance": 1, "order_date": 1,
                      "payment_type": 1, "grand_total": 1,
                      "payment": "$payments"}}
    ]
    ar_raw = await db.invoices.aggregate(ar_pipeline).to_list(500)
    credit_collections = []
    for p in ar_raw:
        pmt = p.get("payment", {})
        amount = float(pmt.get("amount", 0))
        sale_type = p.get("sale_type", "")
        if sale_type == "interest_charge":
            interest_paid, penalty_paid, principal_paid = amount, 0.0, 0.0
        elif sale_type == "penalty_charge":
            interest_paid, penalty_paid, principal_paid = 0.0, amount, 0.0
        else:
            interest_paid = float(pmt.get("applied_to_interest", 0))
            penalty_paid = float(pmt.get("applied_to_penalty", 0))
            principal_paid = float(pmt.get("applied_to_principal", amount - interest_paid - penalty_paid))
        order_date_inv = p.get("order_date", "")
        is_same_day = (order_date_inv == date)
        credit_collections.append({
            "customer": p.get("customer_name", ""),
            "invoice": p.get("invoice_number", ""),
            "sale_type": sale_type,
            "order_date": order_date_inv,
            "is_same_day": is_same_day,
            "payment_type_invoice": p.get("payment_type", ""),
            "invoice_total": round(float(p.get("grand_total", 0)), 2),
            "balance_before": round(float(p.get("balance", 0)) + amount, 2),
            "interest_paid": round(interest_paid, 2),
            "penalty_paid": round(penalty_paid, 2),
            "principal_paid": round(principal_paid, 2),
            "total_paid": round(amount, 2),
            "balance": round(float(p.get("balance", 0)), 2),
            "fund_source": pmt.get("fund_source", "cashier"),
            "method": pmt.get("method", "Cash"),
        })
    total_ar_received = round(sum(c["total_paid"] for c in credit_collections), 2)
    total_cash_ar = round(sum(c["total_paid"] for c in credit_collections if c.get("fund_source", "cashier") == "cashier"), 2)
    total_digital_ar = round(total_ar_received - total_cash_ar, 2)
    total_ar_same_day = round(sum(c["total_paid"] for c in credit_collections if c.get("is_same_day")), 2)
    total_ar_older = round(total_ar_received - total_ar_same_day, 2)

    expenses_raw = await db.expenses.find({"branch_id": branch_id, "date": date, "voided": {"$ne": True}}, {"_id": 0}).to_list(500)
    expenses = []
    _emp_limit_cache = {}
    for e in expenses_raw:
        exp = dict(e)
        if e.get("category") == "Employee Advance" and e.get("employee_id"):
            month_res = await db.expenses.aggregate([
                {"$match": {"branch_id": branch_id, "category": "Employee Advance",
                            "employee_id": e["employee_id"],
                            "date": {"$gte": f"{month_prefix}-01", "$lte": f"{month_prefix}-31"}}},
                {"$group": {"_id": None, "total": {"$sum": "$amount"}}}
            ]).to_list(1)
            exp["monthly_ca_total"] = round(month_res[0]["total"] if month_res else 0, 2)
            eid = e["employee_id"]
            if eid not in _emp_limit_cache:
                emp_doc = await db.employees.find_one({"id": eid}, {"_id": 0, "monthly_ca_limit": 1})
                _emp_limit_cache[eid] = float(emp_doc.get("monthly_ca_limit", 0)) if emp_doc else 0
            exp["monthly_ca_limit"] = _emp_limit_cache[eid]
            exp["is_over_ca"] = exp["monthly_ca_limit"] > 0 and exp["monthly_ca_total"] > exp["monthly_ca_limit"]
        expenses.append(exp)
    total_expenses = round(sum(float(e.get("amount", 0)) for e in expenses), 2)
    total_cashier_expenses = round(sum(
        float(e.get("amount", 0)) for e in expenses
        if e.get("fund_source", "cashier") == "cashier"
    ), 2)
    total_safe_expenses = round(sum(
        float(e.get("amount", 0)) for e in expenses
        if e.get("fund_source") == "safe"
    ), 2)
    total_digital_expenses = round(total_expenses - total_cashier_expenses - total_safe_expenses, 2)

    # Split payment cash portions
    split_close = await db.invoices.find(
        {"branch_id": branch_id, "order_date": date,
         "fund_source": "split", "status": {"$ne": "voided"}},
        {"_id": 0, "cash_amount": 1}
    ).to_list(500)
    total_split_cash = round(sum(float(inv.get("cash_amount", 0)) for inv in split_close), 2)

    # ── Fund transfers affecting cashier drawer today ─────────────────────
    ft_query = {"branch_id": branch_id, "$or": [
        {"date": date},
        {"date": {"$exists": False}, "created_at": {"$gte": f"{date}T00:00:00", "$lt": f"{date}T23:59:59"}}
    ]}
    fund_transfers_today = await db.fund_transfers.find(ft_query, {"_id": 0}).to_list(200)
    capital_to_cashier = round(sum(
        float(ft.get("amount", 0)) for ft in fund_transfers_today
        if ft.get("transfer_type") == "capital_add" and ft.get("target_wallet", "cashier") == "cashier"
    ), 2)
    safe_to_cashier = round(sum(
        float(ft.get("amount", 0)) for ft in fund_transfers_today
        if ft.get("transfer_type") == "safe_to_cashier"
    ), 2)
    cashier_to_safe = round(sum(
        float(ft.get("amount", 0)) for ft in fund_transfers_today
        if ft.get("transfer_type") == "cashier_to_safe"
    ), 2)
    net_fund_transfers = round(capital_to_cashier + safe_to_cashier - cashier_to_safe, 2)

    # CLOSE WIZARD REFACTOR (Feb 2026): partial_total dropped — same-day
    # partial-sale cash legs are now part of total_cash_ar.
    total_cash_in = total_cash_sales + total_cash_ar + total_split_cash
    expected_counter = round(starting_float + total_cash_in + net_fund_transfers - total_cashier_expenses, 2)

    actual_cash = float(data.get("actual_cash", 0))
    cash_to_safe = float(data.get("cash_to_safe", 0))
    cash_to_drawer = float(data.get("cash_to_drawer", 0))
    over_short = round(actual_cash - expected_counter, 2)
    variance_notes = data.get("variance_notes", "").strip()

    # ── Credit sales today (new AR created) ───────────────────────────────────
    credit_invoices_today = await db.invoices.find(
        {"branch_id": branch_id, "order_date": date,
         "payment_type": {"$in": ["credit", "partial"]}, "status": {"$ne": "voided"}},
        {"_id": 0, "customer_name": 1, "invoice_number": 1, "grand_total": 1,
         "balance": 1, "payment_type": 1, "sale_type": 1}
    ).to_list(500)
    total_new_credit = round(sum(float(inv.get("balance", 0)) for inv in credit_invoices_today), 2)

    # Also get cashouts/farm AR credits
    ar_credits_today = await db.invoices.find(
        {"branch_id": branch_id, "order_date": date,
         "sale_type": {"$in": ["cash_advance", "farm_expense"]}, "status": {"$ne": "voided"}},
        {"_id": 0, "customer_name": 1, "invoice_number": 1, "grand_total": 1, "sale_type": 1, "items": 1}
    ).to_list(200)

    # ── Total outstanding AR at time of close ─────────────────────────────────
    ar_total_result = await db.invoices.aggregate([
        {"$match": {"branch_id": branch_id, "status": {"$nin": ["paid", "voided"]}, "balance": {"$gt": 0}}},
        {"$group": {"_id": None, "total": {"$sum": "$balance"}}}
    ]).to_list(1)
    total_ar_at_close = round(ar_total_result[0]["total"] if ar_total_result else 0, 2)

    # ── Digital payments today ──────────────────────────────────────────────
    digital_invs_today = await db.invoices.find(
        {"branch_id": branch_id, "order_date": date,
         "fund_source": {"$in": ["digital", "split"]}, "status": {"$ne": "voided"}},
        {"_id": 0, "invoice_number": 1, "customer_name": 1, "amount_paid": 1,
         "digital_amount": 1, "digital_platform": 1, "digital_ref_number": 1, "fund_source": 1}
    ).to_list(500)
    digital_by_platform: dict = {}
    total_digital_today = 0.0
    for inv in digital_invs_today:
        # For split payments, only count the digital portion
        amt = float(inv.get("digital_amount", 0) if inv.get("fund_source") == "split" and inv.get("digital_amount") else inv.get("amount_paid", 0))
        platform = inv.get("digital_platform", "Digital") or "Digital"
        digital_by_platform[platform] = round(digital_by_platform.get(platform, 0) + amt, 2)
        total_digital_today = round(total_digital_today + amt, 2)

    close_record = {
        "id": new_id(), "branch_id": branch_id, "date": date, "status": "closed",
        "starting_float": starting_float,
        "safe_balance": round(safe_balance, 2),
        "sales_by_category": sales_by_category,
        "total_cash_sales": total_cash_sales,
        "total_split_cash": total_split_cash,
        "total_partial_cash": partial_total,
        "credit_collections": credit_collections,
        "total_ar_received": total_ar_received,
        "total_ar_same_day": total_ar_same_day,
        "total_ar_older": total_ar_older,
        "total_cash_ar": total_cash_ar,
        "total_digital_ar": total_digital_ar,
        "total_expenses": total_expenses,
        "total_cashier_expenses": total_cashier_expenses,
        "total_safe_expenses": total_safe_expenses,
        "total_digital_expenses": total_digital_expenses,
        "expenses": expenses,
        "total_cash_in": round(total_cash_in, 2),
        "expected_counter": expected_counter,
        "actual_cash": actual_cash,
        "over_short": over_short,
        "variance_notes": variance_notes,
        "cash_to_safe": cash_to_safe,
        "cash_to_drawer": cash_to_drawer,
        # Fund transfers affecting cashier
        "fund_transfers_today": [
            {"type": ft["transfer_type"], "amount": float(ft.get("amount", 0)),
             "note": ft.get("note", ""), "authorized_by": ft.get("authorized_by", ""),
             "target_wallet": ft.get("target_wallet", ""),
             "time": ft.get("created_at", "")}
            for ft in fund_transfers_today
            if ft.get("transfer_type") in ("capital_add", "safe_to_cashier", "cashier_to_safe")
        ],
        "capital_to_cashier": capital_to_cashier,
        "safe_to_cashier": safe_to_cashier,
        "cashier_to_safe": cashier_to_safe,
        "net_fund_transfers": net_fund_transfers,
        # Iter 243.4 — store net sales + employee advances on the close record
        # so the Z-Report PDF and the Z-Report Archive show the same numbers
        # months later, even after employee advance balances change.
        "net_sales_today": await _compute_net_sales_today(branch_id, date),
        "employee_advances_today": await _compute_employee_advances_today(branch_id, date),
        # Digital payments today (separate from cashier reconciliation)
        "total_digital_today": total_digital_today,
        "digital_by_platform": digital_by_platform,
        "digital_transactions": [
            {"invoice_number": inv.get("invoice_number"), "customer_name": inv.get("customer_name"),
             "platform": inv.get("digital_platform", "Digital"),
             "ref_number": inv.get("digital_ref_number", ""),
             "amount": float(inv.get("digital_amount", 0) if inv.get("fund_source") == "split" and inv.get("digital_amount") else inv.get("amount_paid", 0))}
            for inv in digital_invs_today
        ],
        # New credit extended today
        "credit_sales_today": [
            {"customer_name": inv["customer_name"], "invoice_number": inv["invoice_number"],
             "grand_total": inv.get("grand_total", 0), "balance": inv.get("balance", 0),
             "type": inv.get("sale_type", "credit")}
            for inv in credit_invoices_today
        ],
        "ar_credits_today": [
            {"customer_name": inv["customer_name"], "invoice_number": inv["invoice_number"],
             "grand_total": inv.get("grand_total", 0), "type": inv.get("sale_type", ""),
             "description": inv.get("items", [{}])[0].get("product_name", "") if inv.get("items") else ""}
            for inv in ar_credits_today
        ],
        "total_new_credit": total_new_credit,
        "total_ar_at_close": total_ar_at_close,
        "closed_by": user["id"],
        "closed_by_name": user.get("full_name", user["username"]),
        "closed_at": now_iso(),
    }

    await db.daily_closings.insert_one(close_record)
    del close_record["_id"]

    # ── Iter 240: write proper ledger trail for the day-close ──────────
    # Order matters: write ledger BEFORE updating wallet balance so the
    # wallet_movements timestamps reflect the close moment, and so a
    # crash here leaves a partial trail rather than a silently-mutated
    # balance.
    await _write_close_ledger_entries(
        closing_id=close_record["id"], branch_id=branch_id, date=date,
        cashier_wallet=wallet, safe_wallet=safe,
        actual_cash=actual_cash, cash_to_safe=cash_to_safe,
        cash_to_drawer=cash_to_drawer,
        over_short=over_short, variance_notes=variance_notes,
        user=user,
    )

    # Overage Reserve auto-hook — pool positive over_short, track negative
    # as deficit. Idempotent via (source_id, auto_credit).
    try:
        from routes.overage_reserve import record_daily_close_variance
        await record_daily_close_variance(close_record, user=user)
    except Exception as _reserve_err:
        import logging
        logging.getLogger(__name__).error("Reserve hook failed for daily close: %s", _reserve_err)

    # Z-Report Finalized SMS — fire-and-forget
    try:
        from routes.close_reminder import send_zreport_finalized
        import asyncio as _asyncio
        _asyncio.create_task(send_zreport_finalized(close_record, user=user))
    except Exception as _zr_err:
        import logging
        logging.getLogger(__name__).error("Z-report SMS hook failed: %s", _zr_err)

    # Update cashier wallet to new float
    if wallet:
        await db.fund_wallets.update_one({"id": wallet["id"]}, {"$set": {"balance": cash_to_drawer}})

    # Add cash_to_safe to safe
    if cash_to_safe > 0 and safe:
        await db.safe_lots.insert_one({
            "id": new_id(), "branch_id": branch_id, "wallet_id": safe["id"],
            "date_received": date, "original_amount": cash_to_safe,
            "remaining_amount": cash_to_safe,
            "source_reference": f"Day close {date}",
            "created_by": user["id"], "created_at": now_iso()
        })

    return close_record



@router.post("/daily-close/batch")
async def batch_close_days(data: dict, user=Depends(get_current_user)):
    """Close multiple days as a single grouped closing. Combines all sales, credits,
    expenses across the selected dates into one closing record."""
    check_perm(user, "reports", "close_day")

    branch_id = data["branch_id"]
    dates = sorted(data.get("dates", []))  # e.g., ["2026-01-01","2026-01-02",...]
    reason = data.get("reason", "").strip()
    admin_pin = data.get("admin_pin", "")
    actual_cash = float(data.get("actual_cash", 0))
    cash_to_safe = float(data.get("cash_to_safe", 0))
    cash_to_drawer = float(data.get("cash_to_drawer", 0))
    variance_notes = data.get("variance_notes", "").strip()

    if len(dates) < 2:
        raise HTTPException(status_code=400, detail="Batch close requires 2+ dates. Use regular close for single day.")

    # PIN verification
    if user.get("role") != "admin":
        if not admin_pin:
            raise HTTPException(status_code=403, detail="Admin PIN required for batch close")
        from routes.verify import verify_pin_for_action
        verifier = await verify_pin_for_action(admin_pin, "daily_close_batch")
        if not verifier:
            raise HTTPException(status_code=403, detail="Invalid admin PIN")

    # Check none of the dates are already closed
    already_closed = await db.daily_closings.find(
        {"branch_id": branch_id, "date": {"$in": dates}, "status": "closed"}, {"_id": 0, "date": 1}
    ).to_list(100)
    if already_closed:
        closed_dates = [c["date"] for c in already_closed]
        raise HTTPException(status_code=400, detail=f"Already closed: {', '.join(closed_dates)}")

    first_date = dates[0]
    last_date = dates[-1]
    from datetime import timedelta
    month_prefix = first_date[:7]

    # Starting float — anchor to the most-recent closed day before
    # `first_date`. Same fix as `/daily-close-preview/batch`: an exact
    # `first_date - 1` lookup misses every batch where the user skipped
    # one or more close-days (which is the whole reason batch close
    # exists). Wallet-balance fallback is preserved for the genuine
    # "first close ever" case.
    prev_close = await db.daily_closings.find_one(
        {"branch_id": branch_id, "date": {"$lt": first_date}, "status": "closed"},
        {"_id": 0},
        sort=[("date", -1)],
    )
    has_prev_close = bool(prev_close)
    wallet = await db.fund_wallets.find_one({"branch_id": branch_id, "type": "cashier", "active": True}, {"_id": 0})
    starting_float = float(prev_close.get("cash_to_drawer", 0)) if prev_close else float(wallet["balance"] if wallet else 0)

    safe = await db.fund_wallets.find_one({"branch_id": branch_id, "type": "safe", "active": True}, {"_id": 0})
    safe_balance = 0.0
    if safe:
        lots = await db.safe_lots.find({"wallet_id": safe["id"], "remaining_amount": {"$gt": 0}}, {"_id": 0}).to_list(500)
        safe_balance = sum(l["remaining_amount"] for l in lots)

    # Aggregate across ALL dates
    date_filter = {"$in": dates}

    # Cash sales — exclude partial-sale line rows. Their cash portion is
    # already captured by `total_cash_ar` via the payments-today
    # aggregation below; including the full partial line_total here as
    # well would double-count by (line_total - partial_cash_amount) per
    # partial sale. (Single-day preview/POST and the batch preview all
    # apply the same `partial_grand_total` filter; pre-Feb-2026 the
    # batch POST was the only path missing it, silently inflating
    # `total_cash_sales` and `expected_counter` on every batch close
    # that contained partial-payment sales.)
    cash_sales_agg = await db.sales_log.aggregate([
        {"$match": {"branch_id": branch_id, "date": date_filter, "voided": {"$ne": True},
                    "payment_method": {"$regex": "^cash$", "$options": "i"},
                    "partial_grand_total": {"$exists": False}}},
        {"$group": {"_id": "$category", "total": {"$sum": "$line_total"}}}
    ]).to_list(100)
    sales_by_category = {r["_id"] or "General": round(r["total"], 2) for r in cash_sales_agg}
    total_cash_sales = round(sum(sales_by_category.values()), 2)

    # Per-day sales breakdown
    per_day_sales = await db.sales_log.aggregate([
        {"$match": {"branch_id": branch_id, "date": date_filter, "voided": {"$ne": True}}},
        {"$group": {"_id": {"date": "$date", "payment_method": "$payment_method"},
                    "total": {"$sum": "$line_total"}, "count": {"$sum": 1}}}
    ]).to_list(500)
    daily_breakdown = {}
    for r in per_day_sales:
        d = r["_id"]["date"]
        pm = r["_id"]["payment_method"]
        if d not in daily_breakdown:
            daily_breakdown[d] = {"sales_by_method": {}, "total": 0}
        daily_breakdown[d]["sales_by_method"][pm] = round(r["total"], 2)
        daily_breakdown[d]["total"] = round(daily_breakdown[d]["total"] + r["total"], 2)

    # Partial payments
    partial_invoices = await db.invoices.find(
        {"branch_id": branch_id, "order_date": date_filter, "payment_type": "partial", "status": {"$ne": "voided"}},
        {"_id": 0, "amount_paid": 1}
    ).to_list(500)
    partial_total = round(sum(float(inv.get("amount_paid", 0)) for inv in partial_invoices), 2)

    # AR collections across all dates
    # CLOSE WIZARD REFACTOR (Feb 2026): include ALL payments dated within
    # the batch — same-day partial-sale cash legs and same-day extra
    # payments. The expanded `total_cash_ar` subsumes `partial_total`,
    # which is dropped from the cash_in math below.
    # CRITICAL FILTER: restrict to AR-semantic invoices only. Pure cash /
    # digital / split sales would otherwise leak in via their payment record
    # and double-count against `total_cash_sales`.
    ar_pipeline = [
        {"$match": {
            "branch_id": branch_id,
            "status": {"$ne": "voided"},
            "$or": [
                {"payment_type": {"$in": ["credit", "partial"]}},
                {"sale_type": {"$in": ["interest_charge", "penalty_charge"]}},
            ],
        }},
        {"$unwind": "$payments"},
        {"$match": {
            "payments.date": date_filter,
            "payments.voided": {"$ne": True},
        }},
        {"$project": {"_id": 0, "customer_name": 1, "invoice_number": 1,
                      "sale_type": 1, "balance": 1, "order_date": 1,
                      "payment_type": 1, "grand_total": 1,
                      "payment": "$payments"}}
    ]
    ar_raw = await db.invoices.aggregate(ar_pipeline).to_list(500)
    credit_collections = []
    for p in ar_raw:
        pmt = p.get("payment", {})
        amount = float(pmt.get("amount", 0))
        sale_type = p.get("sale_type", "")
        if sale_type == "interest_charge":
            interest_paid, penalty_paid, principal_paid = amount, 0.0, 0.0
        elif sale_type == "penalty_charge":
            interest_paid, penalty_paid, principal_paid = 0.0, amount, 0.0
        else:
            interest_paid = float(pmt.get("applied_to_interest", 0))
            penalty_paid = float(pmt.get("applied_to_penalty", 0))
            principal_paid = float(pmt.get("applied_to_principal", amount - interest_paid - penalty_paid))
        order_date_inv = p.get("order_date", "")
        is_same_day_batch = order_date_inv in dates
        credit_collections.append({
            "customer": p.get("customer_name", ""),
            "invoice": p.get("invoice_number", ""),
            "sale_type": sale_type,
            "order_date": order_date_inv,
            "is_same_day": is_same_day_batch,
            "payment_type_invoice": p.get("payment_type", ""),
            "invoice_total": round(float(p.get("grand_total", 0)), 2),
            "balance_before": round(float(p.get("balance", 0)) + amount, 2),
            "interest_paid": round(interest_paid, 2),
            "penalty_paid": round(penalty_paid, 2),
            "principal_paid": round(principal_paid, 2),
            "total_paid": round(amount, 2),
            "balance": round(float(p.get("balance", 0)), 2),
            "fund_source": pmt.get("fund_source", "cashier"),
            "method": pmt.get("method", "Cash"),
        })
    total_ar_received = round(sum(c["total_paid"] for c in credit_collections), 2)
    total_cash_ar = round(sum(c["total_paid"] for c in credit_collections if c.get("fund_source", "cashier") == "cashier"), 2)
    total_digital_ar = round(total_ar_received - total_cash_ar, 2)

    # Expenses across all dates — split by fund source
    expenses_raw = await db.expenses.find({"branch_id": branch_id, "date": date_filter, "voided": {"$ne": True}}, {"_id": 0}).to_list(500)
    expenses = []
    _emp_limit_cache = {}
    for e in expenses_raw:
        exp = dict(e)
        if e.get("category") == "Employee Advance" and e.get("employee_id"):
            month_res = await db.expenses.aggregate([
                {"$match": {"branch_id": branch_id, "category": "Employee Advance",
                            "employee_id": e["employee_id"],
                            "date": {"$gte": f"{month_prefix}-01", "$lte": f"{month_prefix}-31"}}},
                {"$group": {"_id": None, "total": {"$sum": "$amount"}}}
            ]).to_list(1)
            exp["monthly_ca_total"] = round(month_res[0]["total"] if month_res else 0, 2)
            eid = e["employee_id"]
            if eid not in _emp_limit_cache:
                emp_doc = await db.employees.find_one({"id": eid}, {"_id": 0, "monthly_ca_limit": 1})
                _emp_limit_cache[eid] = float(emp_doc.get("monthly_ca_limit", 0)) if emp_doc else 0
            exp["monthly_ca_limit"] = _emp_limit_cache[eid]
            exp["is_over_ca"] = exp["monthly_ca_limit"] > 0 and exp["monthly_ca_total"] > exp["monthly_ca_limit"]
        expenses.append(exp)
    total_expenses = round(sum(float(e.get("amount", 0)) for e in expenses), 2)
    total_cashier_expenses = round(sum(
        float(e.get("amount", 0)) for e in expenses
        if e.get("fund_source", "cashier") == "cashier"
    ), 2)
    total_safe_expenses = round(sum(
        float(e.get("amount", 0)) for e in expenses
        if e.get("fund_source") == "safe"
    ), 2)
    total_digital_expenses = round(total_expenses - total_cashier_expenses - total_safe_expenses, 2)
    for d in dates:
        if d not in daily_breakdown:
            daily_breakdown[d] = {"sales_by_method": {}, "total": 0}
        day_exp = round(sum(float(e.get("amount", 0)) for e in expenses if e.get("date") == d), 2)
        daily_breakdown[d]["expenses"] = day_exp

    # Split payment cash portions
    split_invs_bc = await db.invoices.find(
        {"branch_id": branch_id, "order_date": date_filter,
         "fund_source": "split", "status": {"$ne": "voided"}},
        {"_id": 0, "cash_amount": 1}
    ).to_list(500)
    total_split_cash = round(sum(float(inv.get("cash_amount", 0)) for inv in split_invs_bc), 2)

    # ── Fund transfers affecting cashier across all dates ─────────────────
    ft_query = {"branch_id": branch_id, "$or": [
        {"date": date_filter},
        {"date": {"$exists": False}, "created_at": {"$gte": f"{first_date}T00:00:00", "$lt": f"{last_date}T23:59:59"}}
    ]}
    fund_transfers_batch = await db.fund_transfers.find(ft_query, {"_id": 0}).to_list(500)
    capital_to_cashier = round(sum(
        float(ft.get("amount", 0)) for ft in fund_transfers_batch
        if ft.get("transfer_type") == "capital_add" and ft.get("target_wallet", "cashier") == "cashier"
    ), 2)
    safe_to_cashier = round(sum(
        float(ft.get("amount", 0)) for ft in fund_transfers_batch
        if ft.get("transfer_type") == "safe_to_cashier"
    ), 2)
    cashier_to_safe_ft = round(sum(
        float(ft.get("amount", 0)) for ft in fund_transfers_batch
        if ft.get("transfer_type") == "cashier_to_safe"
    ), 2)
    net_fund_transfers = round(capital_to_cashier + safe_to_cashier - cashier_to_safe_ft, 2)

    # CLOSE WIZARD REFACTOR (Feb 2026): partial_total dropped — same-day
    # partial-sale cash legs are now part of total_cash_ar.
    # Feb 2026 FIX: mirror single-day `has_prev_close` guard. For a
    # batch that has NO prior closed day at all (genuine first-ever
    # close on a fresh wallet), the formula would double-count cash
    # that's already reflected in wallet.balance; in that case the
    # current wallet balance IS the expected counter. For every other
    # batch (the common case) we use the canonical formula.
    total_cash_in = total_cash_sales + total_cash_ar + total_split_cash
    if has_prev_close:
        expected_counter = round(starting_float + total_cash_in + net_fund_transfers - total_cashier_expenses, 2)
    else:
        expected_counter = round(float(wallet["balance"]) if wallet else 0, 2)
    over_short = round(actual_cash - expected_counter, 2)

    # Credit sales across all dates
    credit_invoices_today = await db.invoices.find(
        {"branch_id": branch_id, "order_date": date_filter,
         "payment_type": {"$in": ["credit", "partial"]}, "status": {"$ne": "voided"}},
        {"_id": 0, "customer_name": 1, "invoice_number": 1, "grand_total": 1,
         "balance": 1, "payment_type": 1, "sale_type": 1}
    ).to_list(500)
    total_new_credit = round(sum(float(inv.get("balance", 0)) for inv in credit_invoices_today), 2)

    # Cashouts/farm AR
    ar_credits_today = await db.invoices.find(
        {"branch_id": branch_id, "order_date": date_filter,
         "sale_type": {"$in": ["cash_advance", "farm_expense"]}, "status": {"$ne": "voided"}},
        {"_id": 0, "customer_name": 1, "invoice_number": 1, "grand_total": 1, "sale_type": 1, "items": 1}
    ).to_list(200)

    # Total outstanding AR
    ar_total_result = await db.invoices.aggregate([
        {"$match": {"branch_id": branch_id, "status": {"$nin": ["paid", "voided"]}, "balance": {"$gt": 0}}},
        {"$group": {"_id": None, "total": {"$sum": "$balance"}}}
    ]).to_list(1)
    total_ar_at_close = round(ar_total_result[0]["total"] if ar_total_result else 0, 2)

    # Digital payments
    digital_invs = await db.invoices.find(
        {"branch_id": branch_id, "order_date": date_filter,
         "fund_source": {"$in": ["digital", "split"]}, "status": {"$ne": "voided"}},
        {"_id": 0, "invoice_number": 1, "customer_name": 1, "amount_paid": 1,
         "digital_amount": 1, "digital_platform": 1, "digital_ref_number": 1, "fund_source": 1}
    ).to_list(500)
    digital_by_platform = {}
    total_digital = 0.0
    for inv in digital_invs:
        amt = float(inv.get("digital_amount", 0) if inv.get("fund_source") == "split" and inv.get("digital_amount") else inv.get("amount_paid", 0))
        platform = inv.get("digital_platform", "Digital") or "Digital"
        digital_by_platform[platform] = round(digital_by_platform.get(platform, 0) + amt, 2)
        total_digital = round(total_digital + amt, 2)

    # Build the batch close record — one record covering all dates
    batch_id = new_id()
    close_record = {
        "id": batch_id, "branch_id": branch_id,
        "date": last_date,  # The closing is dated as the last day
        "date_from": first_date, "date_to": last_date,
        "dates_covered": dates,
        "is_batch": True,
        "batch_reason": reason,
        "status": "closed",
        "starting_float": starting_float,
        "safe_balance": round(safe_balance, 2),
        "sales_by_category": sales_by_category,
        "total_cash_sales": total_cash_sales,
        "total_split_cash": total_split_cash,
        "total_partial_cash": partial_total,
        "credit_collections": credit_collections,
        "total_ar_received": total_ar_received,
        "total_cash_ar": total_cash_ar,
        "total_digital_ar": total_digital_ar,
        "total_expenses": total_expenses,
        "total_cashier_expenses": total_cashier_expenses,
        "total_safe_expenses": total_safe_expenses,
        "total_digital_expenses": total_digital_expenses,
        "expenses": expenses,
        "total_cash_in": round(total_cash_in, 2),
        "expected_counter": expected_counter,
        "actual_cash": actual_cash,
        "over_short": over_short,
        "variance_notes": variance_notes,
        "cash_to_safe": cash_to_safe,
        "cash_to_drawer": cash_to_drawer,
        # Fund transfers affecting cashier
        "fund_transfers_today": [
            {"type": ft["transfer_type"], "amount": float(ft.get("amount", 0)),
             "note": ft.get("note", ""), "authorized_by": ft.get("authorized_by", ""),
             "target_wallet": ft.get("target_wallet", ""),
             "time": ft.get("created_at", "")}
            for ft in fund_transfers_batch
            if ft.get("transfer_type") in ("capital_add", "safe_to_cashier", "cashier_to_safe")
        ],
        "capital_to_cashier": capital_to_cashier,
        "safe_to_cashier": safe_to_cashier,
        "cashier_to_safe": cashier_to_safe_ft,
        "net_fund_transfers": net_fund_transfers,
        "total_digital_today": total_digital,
        "digital_by_platform": digital_by_platform,
        "digital_transactions": [
            {"invoice_number": inv.get("invoice_number"), "customer_name": inv.get("customer_name"),
             "platform": inv.get("digital_platform", "Digital"),
             "ref_number": inv.get("digital_ref_number", ""),
             "amount": float(inv.get("digital_amount", 0) if inv.get("fund_source") == "split" and inv.get("digital_amount") else inv.get("amount_paid", 0))}
            for inv in digital_invs
        ],
        "credit_sales_today": [
            {"customer_name": inv["customer_name"], "invoice_number": inv["invoice_number"],
             "grand_total": inv.get("grand_total", 0), "balance": inv.get("balance", 0),
             "type": inv.get("sale_type", "credit")}
            for inv in credit_invoices_today
        ],
        "ar_credits_today": [
            {"customer_name": inv["customer_name"], "invoice_number": inv["invoice_number"],
             "grand_total": inv.get("grand_total", 0), "type": inv.get("sale_type", ""),
             "description": inv.get("items", [{}])[0].get("product_name", "") if inv.get("items") else ""}
            for inv in ar_credits_today
        ],
        "total_new_credit": total_new_credit,
        "total_ar_at_close": total_ar_at_close,
        "daily_breakdown": daily_breakdown,
        "closed_by": user["id"],
        "closed_by_name": user.get("full_name", user["username"]),
        "closed_at": now_iso(),
    }

    await db.daily_closings.insert_one(close_record)
    del close_record["_id"]

    # ── Iter 240: ledger trail for batch close ──────────────────────────
    # over_short for batch close — same calc as the single close path
    over_short_batch = round(actual_cash - close_record.get("expected_counter", 0), 2)
    await _write_close_ledger_entries(
        closing_id=close_record["id"], branch_id=branch_id, date=last_date,
        cashier_wallet=wallet, safe_wallet=safe,
        actual_cash=actual_cash, cash_to_safe=cash_to_safe,
        cash_to_drawer=cash_to_drawer,
        over_short=over_short_batch, variance_notes=variance_notes,
        user=user,
    )

    # Overage Reserve auto-hook (batch-close path)
    try:
        from routes.overage_reserve import record_daily_close_variance
        await record_daily_close_variance(close_record, user=user)
    except Exception as _reserve_err:
        import logging
        logging.getLogger(__name__).error("Reserve hook failed for batch close: %s", _reserve_err)

    # Also insert placeholder records for each individual date so they're marked as closed
    for d in dates:
        if d == last_date:
            continue  # The main record already covers last_date
        placeholder = {
            "id": new_id(), "branch_id": branch_id, "date": d,
            "status": "closed", "is_batch_member": True,
            "batch_id": batch_id, "batch_date_range": f"{first_date} to {last_date}",
            "closed_by": user["id"], "closed_at": now_iso(),
        }
        await db.daily_closings.insert_one(placeholder)

    # Update cashier wallet
    if wallet:
        await db.fund_wallets.update_one({"id": wallet["id"]}, {"$set": {"balance": cash_to_drawer}})

    # Add cash to safe
    if cash_to_safe > 0 and safe:
        await db.safe_lots.insert_one({
            "id": new_id(), "branch_id": branch_id, "wallet_id": safe["id"],
            "date_received": last_date, "original_amount": cash_to_safe,
            "remaining_amount": cash_to_safe,
            "source_reference": f"Batch close {first_date} to {last_date}",
            "created_by": user["id"], "created_at": now_iso()
        })

    return close_record


@router.get("/daily-variance-history")
async def get_variance_history(
    user=Depends(get_current_user),
    branch_id: Optional[str] = None,
    limit: int = 60,
    skip: int = 0
):
    """
    Historical record of daily over/short (cash variances) for audit purposes.
    Positive over_short = extra cash (possible unrecorded sales).
    Negative over_short = cash short (possible theft, unrecorded expense, or error).
    """
    check_perm(user, "reports", "view")

    query = {"status": "closed"}
    if branch_id:
        query["branch_id"] = branch_id

    total = await db.daily_closings.count_documents(query)
    records = await db.daily_closings.find(
        query,
        {"_id": 0, "id": 1, "date": 1, "branch_id": 1,
         "expected_counter": 1, "actual_cash": 1, "over_short": 1,
         "variance_notes": 1, "closed_by_name": 1, "closed_at": 1,
         "total_cash_sales": 1, "total_ar_received": 1, "total_expenses": 1,
         "starting_float": 1}
    ).sort("date", -1).skip(skip).limit(limit).to_list(limit)

    # Attach branch name
    branch_names = {}
    for r in records:
        bid = r.get("branch_id")
        if bid and bid not in branch_names:
            b = await db.branches.find_one({"id": bid}, {"_id": 0, "name": 1})
            branch_names[bid] = b["name"] if b else bid
        r["branch_name"] = branch_names.get(bid, "")

    # ── Attach reconciliation adjustment totals ──────────────────────────────
    # Aggregate non-voided adjustments per closing so the archive grid and
    # the top-of-page "Net Over/Short" KPI reflect the corrected values
    # without mutating the historical `over_short` field on daily_closings.
    closing_ids = [r["id"] for r in records if r.get("id")]
    if closing_ids:
        adj_pipeline = [
            {"$match": {"closing_id": {"$in": closing_ids}, "voided": {"$ne": True}}},
            {"$group": {"_id": "$closing_id",
                        "total": {"$sum": "$amount"},
                        "count": {"$sum": 1}}},
        ]
        adj_by_closing = {
            a["_id"]: {"total": round(float(a["total"]), 2), "count": a["count"]}
            async for a in db.daily_closing_adjustments.aggregate(adj_pipeline)
        }
        for r in records:
            adj = adj_by_closing.get(r.get("id"), {"total": 0.0, "count": 0})
            r["adjustment_total"] = adj["total"]
            r["adjustment_count"] = adj["count"]
            r["adjusted_over_short"] = round(float(r.get("over_short", 0)) + adj["total"], 2)

    return {"records": records, "total": total}



@router.get("/low-stock-alert")
async def get_low_stock_alert(
    user=Depends(get_current_user),
    branch_id: Optional[str] = None,
):
    """
    Products that ever had inventory added for this branch and are now
    at 0 OR at/below their reorder_point.
    """
    check_perm(user, "inventory", "view")
    if not branch_id:
        branch_id = user.get("branch_id")
    if not branch_id:
        return []

    inv_records = await db.inventory.find(
        {"branch_id": branch_id}, {"_id": 0, "product_id": 1, "quantity": 1}
    ).to_list(10000)

    low_stock = []
    for inv in inv_records:
        qty = float(inv.get("quantity", 0))
        product = await db.products.find_one(
            {"id": inv["product_id"], "active": True, "is_repack": {"$ne": True}},
            {"_id": 0, "id": 1, "name": 1, "sku": 1, "unit": 1, "category": 1,
             "reorder_point": 1, "reorder_quantity": 1}
        )
        if not product:
            continue
        reorder_pt = float(product.get("reorder_point", 0))
        if qty <= 0 or (reorder_pt > 0 and qty <= reorder_pt):
            low_stock.append({
                "product_id": product["id"],
                "sku": product["sku"],
                "name": product["name"],
                "unit": product["unit"],
                "category": product.get("category", "General"),
                "current_qty": qty,
                "reorder_point": reorder_pt,
                "reorder_quantity": product.get("reorder_quantity", 0),
                "status": "negative_stock" if qty < 0 else ("out_of_stock" if qty == 0 else "low_stock"),
            })

    low_stock.sort(key=lambda x: (0 if x["status"] == "negative_stock" else 1 if x["status"] == "out_of_stock" else 2, x["name"]))
    return low_stock


@router.get("/supplier-payables")
async def get_supplier_payables(
    user=Depends(get_current_user),
    branch_id: Optional[str] = None,
):
    """All outstanding unpaid/partially-paid purchase orders, sorted by urgency."""
    check_perm(user, "purchase_orders", "view")
    if not branch_id:
        branch_id = user.get("branch_id")

    query = {
        "payment_status": {"$ne": "paid"},
        "status": {"$in": ["received", "partial", "ordered"]},
    }
    if branch_id:
        query["branch_id"] = branch_id

    pos = await db.purchase_orders.find(query, {"_id": 0}).sort("due_date", 1).to_list(500)
    today = datetime.now(timezone.utc).date()
    result = []
    for po in pos:
        due_str = po.get("due_date", "")
        days_until_due = None
        is_overdue = False
        is_urgent = False
        if due_str:
            try:
                due_d = datetime.strptime(due_str, "%Y-%m-%d").date()
                days_until_due = (due_d - today).days
                is_overdue = days_until_due < 0
                is_urgent = days_until_due < 7
            except Exception:
                pass

        result.append({
            "id": po.get("id", ""),
            "po_number": po.get("po_number", po.get("id", "")[:8].upper()),
            "vendor": po.get("vendor", "Unknown"),
            "purchase_date": po.get("purchase_date", ""),
            "due_date": due_str,
            "subtotal": float(po.get("subtotal", 0)),
            "balance": float(po.get("balance", po.get("subtotal", 0))),
            "status": po.get("status", ""),
            "payment_status": po.get("payment_status", "unpaid"),
            "days_until_due": days_until_due,
            "is_overdue": is_overdue,
            "is_urgent": is_urgent,
        })

    # Sort: overdue first, then by urgency, then by days_until_due
    result.sort(key=lambda x: (
        0 if x["is_overdue"] else (1 if x["is_urgent"] else 2),
        x["days_until_due"] if x["days_until_due"] is not None else 9999
    ))
    return result


# ── Resend Z-Report SMS for a previously closed day ──────────────────────
@router.post("/daily-close/{closing_id}/resend-sms")
async def resend_zreport_sms(closing_id: str, user=Depends(get_current_user)):
    """Admin-only: re-fire the Z-Report finalized SMS for a past closing.

    Useful for testing the SMS content (especially the share link) without
    waiting for a new closing day.
    """
    check_perm(user, "daily_operations", "close")

    close_record = await db.daily_closings.find_one(
        {"id": closing_id, "status": "closed"}, {"_id": 0}
    )
    if not close_record:
        raise HTTPException(status_code=404, detail="Closed day record not found")

    from routes.close_reminder import send_zreport_finalized
    results = await send_zreport_finalized(close_record, user=user, is_resend=True)

    queued = results.get("queued", 0)
    skipped = results.get("skipped", 0)
    total = results.get("total_recipients", 0)

    if total == 0:
        msg = "No recipients with phone numbers (manager/owner/auditor) configured for this branch — nothing was sent."
    elif queued == 0:
        msg = f"All {total} recipient(s) were skipped — check Settings → Messages and the SMS queue for details."
    else:
        msg = f"Z-Report SMS re-queued for {queued}/{total} recipient(s) on {close_record.get('date')}."

    return {
        "ok": queued > 0,
        "queued": queued,
        "skipped": skipped,
        "total_recipients": total,
        "recipients": results.get("recipients", []),
        "message": msg,
    }

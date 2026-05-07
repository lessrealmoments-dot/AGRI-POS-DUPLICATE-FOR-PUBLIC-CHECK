"""
Sync routes: Offline POS data sync.
"""
from fastapi import APIRouter, Depends, HTTPException
from datetime import datetime, timezone, timedelta
from config import db, _raw_db
from utils import get_current_user, now_iso, new_id, log_movement, log_sale_items, update_cashier_wallet, get_active_date, today_local

router = APIRouter(tags=["Sync"])


# ─────────────────────────────────────────────────────────────────────────────
# Linked Offline Draft Finalization (Feb 2026)
# ─────────────────────────────────────────────────────────────────────────────
# Sync helper: finalize an existing for_preparation draft from an offline
# envelope. Preserves the canonical invoice_number; stamps the OFF number as
# linked_offline_receipt_number for bidirectional lookup. Idempotent.
# Conflicts (already paid / cancelled / not found) push to
# `offline_reconciliation_queue` for manager review.
# ─────────────────────────────────────────────────────────────────────────────
async def _finalize_draft_offline(sale: dict, user: dict, envelope_id: str) -> dict:
    draft_id = (sale.get("draft_invoice_id") or "").strip()
    off_num = sale.get("invoice_number") or sale.get("offline_receipt_number") or ""
    branch_id = sale.get("branch_id", "")

    # Idempotency canary — if this OFF number was already linked, no-op.
    already = await db.invoices.find_one(
        {"linked_offline_receipt_number": off_num, "id": draft_id} if draft_id and off_num
        else {"linked_offline_receipt_number": off_num} if off_num else {"_id": "__none__"},
        {"_id": 0, "id": 1, "invoice_number": 1, "status": 1},
    )
    if already:
        return {
            "id": draft_id or already.get("id"),
            "envelope_id": envelope_id,
            "status": "duplicate",
            "official_invoice": already.get("invoice_number"),
            "kind": "draft_finalization_offline",
        }

    # Locate the draft.
    draft = None
    if draft_id:
        draft = await db.invoices.find_one({"id": draft_id}, {"_id": 0})
    if not draft:
        # Try by draft invoice_number as fallback
        dnum = sale.get("draft_invoice_number") or ""
        if dnum:
            draft = await db.invoices.find_one(
                {"invoice_number": dnum, "branch_id": branch_id}, {"_id": 0}
            )
    if not draft:
        await _push_reconciliation(sale, "draft_not_found", envelope_id, user)
        return {
            "id": draft_id, "envelope_id": envelope_id,
            "status": "conflict", "reason": "draft_not_found",
            "off_receipt": off_num, "kind": "draft_finalization_offline",
        }

    canonical_id = draft["id"]
    canonical_num = draft["invoice_number"]
    cur_status = draft.get("status", "")

    if cur_status != "for_preparation":
        await _push_reconciliation(sale, f"draft_already_{cur_status}", envelope_id, user,
                                   canonical_invoice_number=canonical_num)
        return {
            "id": canonical_id, "envelope_id": envelope_id,
            "status": "conflict", "reason": f"draft_already_{cur_status}",
            "official_invoice": canonical_num, "off_receipt": off_num,
            "kind": "draft_finalization_offline",
        }

    # ── Build finalize payload from the offline envelope ──────────────────
    items = sale.get("items", []) or draft.get("items", [])
    subtotal = float(sale.get("subtotal") or sum(float(i.get("total", 0)) for i in items))
    grand_total = float(sale.get("grand_total") or
                        subtotal + float(sale.get("freight", 0))
                        - float(sale.get("overall_discount", 0)))
    amount_paid = float(sale.get("amount_paid", grand_total))
    balance = float(sale.get("balance", max(0.0, grand_total - amount_paid)))
    payment_type = sale.get("payment_type", "cash")
    payments = sale.get("payments") or []
    sale_date = sale.get("date") or draft.get("order_date") \
        or await today_local(user.get("organization_id") or "")

    # ── Items / payment divergence flags (offline copy wins) ──────────────
    draft_items_sig = _items_signature(draft.get("items", []))
    offline_items_sig = _items_signature(items)
    items_diverged = draft_items_sig != offline_items_sig

    draft_total = float(draft.get("grand_total", 0))
    payment_diverged = abs(grand_total - draft_total) > 0.01 if draft_total else False

    # ── Apply inventory deduction ONCE (mirrors the offline-sync code path
    # used for brand-new sales). Stock warnings are appended to the invoice
    # record so managers can review negative-stock situations afterwards.
    stock_warnings = []
    for item in items:
        qty = float(item.get("quantity", 0))
        rate = float(item.get("rate", item.get("price", 0)))
        product = await db.products.find_one({"id": item.get("product_id")}, {"_id": 0})
        if not product:
            continue
        if product.get("is_repack") and product.get("parent_id"):
            units_per_parent = product.get("units_per_parent", 1)
            parent_deduction = qty / units_per_parent
            parent_inv = await db.inventory.find_one(
                {"product_id": product["parent_id"], "branch_id": branch_id}, {"_id": 0}
            )
            current_stock = float(parent_inv["quantity"]) if parent_inv else 0.0
            if current_stock < parent_deduction:
                stock_warnings.append(
                    f"{product['name']}: need {parent_deduction:.4f} boxes, "
                    f"have {current_stock:.4f} — inventory will go negative"
                )
            await db.inventory.update_one(
                {"product_id": product["parent_id"], "branch_id": branch_id},
                {"$inc": {"quantity": -parent_deduction},
                 "$set": {"updated_at": now_iso()}},
                upsert=True,
            )
            await log_movement(
                product["parent_id"], branch_id, "sale", -parent_deduction,
                canonical_id, canonical_num, rate * units_per_parent,
                user["id"], user.get("full_name", user.get("username", "")),
                f"Offline draft finalization (synced): {product['name']} x {qty}",
            )
        else:
            inv = await db.inventory.find_one(
                {"product_id": item.get("product_id"), "branch_id": branch_id}, {"_id": 0}
            )
            current_stock = float(inv["quantity"]) if inv else 0.0
            if current_stock < qty:
                stock_warnings.append(
                    f"{product['name']}: need {qty}, have {current_stock:.2f} — inventory will go negative"
                )
            await db.inventory.update_one(
                {"product_id": item.get("product_id"), "branch_id": branch_id},
                {"$inc": {"quantity": -qty}, "$set": {"updated_at": now_iso()}},
                upsert=True,
            )
            await log_movement(
                item.get("product_id"), branch_id, "sale", -qty,
                canonical_id, canonical_num, rate,
                user["id"], user.get("full_name", user.get("username", "")),
                "Offline draft finalization (synced)",
            )

    # ── Flip the draft from for_preparation to paid/credit/partial ────────
    update_fields = {
        "items": items,
        "subtotal": round(subtotal, 2),
        "freight": float(sale.get("freight", 0)),
        "overall_discount": float(sale.get("overall_discount", 0)),
        "grand_total": round(grand_total, 2),
        "amount_paid": round(amount_paid, 2),
        "balance": round(balance, 2),
        "payment_type": payment_type,
        "payments": payments,
        "status": _final_status_from_payment_type(payment_type, balance),
        "envelope_id": envelope_id,
        "linked_offline_receipt_number": off_num,
        "finalized_from_draft_offline": True,
        "offline_completion_synced_at": now_iso(),
        "original_draft_invoice_number": canonical_num,
        "synced_from_offline": True,
        "stock_warnings": stock_warnings,
        "offline_items_diverged": items_diverged,
        "payment_diverged": payment_diverged,
        "finalized_by_id": user["id"],
        "finalized_by_name": user.get("full_name", user.get("username", "")),
        "order_date": sale_date,
        "updated_at": now_iso(),
    }
    await db.invoices.update_one(
        {"id": canonical_id, "status": "for_preparation"},  # guard race condition
        {"$set": update_fields},
    )
    await log_sale_items(
        branch_id, sale_date, items, canonical_num,
        draft.get("customer_name", "Walk-in"),
        payment_type,
        user.get("full_name", user.get("username", "")),
    )

    return {
        "id": canonical_id,
        "envelope_id": envelope_id,
        "status": "synced",
        "official_invoice": canonical_num,
        "off_receipt": off_num,
        "items_diverged": items_diverged,
        "payment_diverged": payment_diverged,
        "stock_warnings": stock_warnings,
        "kind": "draft_finalization_offline",
    }


def _items_signature(items: list) -> tuple:
    """Order-independent fingerprint of (product_id, qty) pairs for comparison."""
    return tuple(sorted(
        (i.get("product_id", ""), round(float(i.get("quantity", 0)), 4))
        for i in (items or [])
    ))


def _final_status_from_payment_type(payment_type: str, balance: float) -> str:
    pt = (payment_type or "cash").lower()
    if pt == "credit":
        return "credit" if balance > 0.005 else "paid"
    if pt == "partial":
        return "partial" if balance > 0.005 else "paid"
    return "paid"


async def _push_reconciliation(sale: dict, reason: str, envelope_id: str,
                               user: dict, canonical_invoice_number: str = "") -> None:
    """Insert a row into offline_reconciliation_queue for manager review."""
    try:
        await db.offline_reconciliation_queue.insert_one({
            "id": new_id(),
            "envelope_id": envelope_id,
            "reason": reason,
            "off_receipt": sale.get("invoice_number")
                or sale.get("offline_receipt_number") or "",
            "draft_invoice_id": sale.get("draft_invoice_id") or "",
            "draft_invoice_number": sale.get("draft_invoice_number") or "",
            "canonical_invoice_number": canonical_invoice_number,
            "branch_id": sale.get("branch_id", ""),
            "customer_name": sale.get("customer_name", ""),
            "customer_id": sale.get("customer_id", ""),
            "amount": float(sale.get("grand_total", 0)),
            "snapshot": sale,
            "cashier_id": user.get("id", ""),
            "cashier_name": user.get("full_name", user.get("username", "")),
            "status": "open",
            "created_at": now_iso(),
        })
    except Exception:
        # Don't let queue write failure block sync response
        pass


@router.get("/sync/offline-reconciliation")
async def list_offline_reconciliation(branch_id: str = "", status: str = "open",
                                      limit: int = 100,
                                      user=Depends(get_current_user)):
    """
    List entries in the offline_reconciliation_queue for the Audit Center tab.
    Admins see all branches; non-admins are scoped to their branches.
    """
    q = {"status": status} if status else {}
    if branch_id:
        q["branch_id"] = branch_id
    elif user.get("role") not in ("admin", "owner"):
        # Scope to assigned branches
        bids = user.get("branch_ids") or ([user.get("branch_id")] if user.get("branch_id") else [])
        if bids:
            q["branch_id"] = {"$in": bids}
    rows = await db.offline_reconciliation_queue.find(
        q, {"_id": 0}
    ).sort("created_at", -1).to_list(min(max(limit, 1), 500))
    return {"items": rows, "count": len(rows)}


@router.post("/sync/offline-reconciliation/{rec_id}/resolve")
async def resolve_offline_reconciliation(rec_id: str, body: dict,
                                         user=Depends(get_current_user)):
    """Mark a reconciliation row as resolved with a manager note."""
    if user.get("role") not in ("admin", "owner", "manager"):
        raise HTTPException(403, "Manager or admin role required")
    note = (body.get("note") or "").strip()
    action = (body.get("action") or "manual").strip()
    upd = await db.offline_reconciliation_queue.update_one(
        {"id": rec_id, "status": "open"},
        {"$set": {
            "status": "resolved",
            "resolved_action": action,
            "resolved_note": note,
            "resolved_by_id": user.get("id", ""),
            "resolved_by_name": user.get("full_name", user.get("username", "")),
            "resolved_at": now_iso(),
        }},
    )
    if upd.matched_count == 0:
        raise HTTPException(404, "Reconciliation entry not found or already resolved")
    return {"ok": True}


# ─────────────────────────────────────────────────────────────────────────────
# Recovery endpoint — orphan offline-completion reconciliation
# ─────────────────────────────────────────────────────────────────────────────
# For pre-fix duplicates: an OFF receipt was created as an independent sale
# while the original draft remained `for_preparation`. This endpoint cleanly
# voids the OFF, reverses its inventory + payment + sales-log effects, and
# leaves the draft ready to be re-finalized through the normal flow.
#
# Safety:
#   • Defaults to dry-run (returns the planned actions, mutates nothing).
#   • Caller MUST pass `confirm: true` AND a valid Owner PIN to execute.
#   • Idempotent: a second confirmed call after success returns "already_done".
# ─────────────────────────────────────────────────────────────────────────────
@router.post("/admin/reconcile-orphan-offline-draft")
async def reconcile_orphan_offline_draft(body: dict, user=Depends(get_current_user)):
    if user.get("role") not in ("admin", "owner"):
        raise HTTPException(403, "Admin or owner role required")

    off_num = (body.get("off_invoice_number") or "").strip()
    draft_num = (body.get("draft_invoice_number") or "").strip()
    admin_pin = (body.get("admin_pin") or "").strip()
    confirm = bool(body.get("confirm"))

    if not off_num or not draft_num:
        raise HTTPException(400, "off_invoice_number and draft_invoice_number are required")

    # Locate both invoices
    off_inv = await db.invoices.find_one({"invoice_number": off_num}, {"_id": 0})
    draft_inv = await db.invoices.find_one({"invoice_number": draft_num}, {"_id": 0})
    if not off_inv:
        raise HTTPException(404, f"OFF invoice '{off_num}' not found")
    if not draft_inv:
        raise HTTPException(404, f"Draft invoice '{draft_num}' not found")

    # Sanity checks
    issues = []
    if off_inv.get("status") == "voided":
        issues.append(f"OFF invoice {off_num} is already voided")
    if draft_inv.get("status") != "for_preparation":
        issues.append(
            f"Draft invoice {draft_num} is not in 'for_preparation' state "
            f"(current: {draft_inv.get('status')})"
        )
    if off_inv.get("branch_id") != draft_inv.get("branch_id"):
        issues.append("OFF and draft invoices belong to different branches")

    # Plan inventory reversals
    inventory_plan = []
    for item in (off_inv.get("items") or []):
        product_id = item.get("product_id")
        qty = float(item.get("quantity", 0))
        if not product_id or qty <= 0:
            continue
        product = await db.products.find_one({"id": product_id}, {"_id": 0})
        if not product:
            continue
        if product.get("is_repack") and product.get("parent_id"):
            units_per_parent = product.get("units_per_parent", 1)
            inventory_plan.append({
                "product_id": product["parent_id"],
                "product_name": product.get("name", ""),
                "delta": qty / units_per_parent,
                "via": "parent_repack",
            })
        else:
            inventory_plan.append({
                "product_id": product_id,
                "product_name": product.get("name", ""),
                "delta": qty,
                "via": "direct",
            })

    payment_plan = {
        "amount_paid": float(off_inv.get("amount_paid", 0)),
        "balance": float(off_inv.get("balance", 0)),
        "payments": off_inv.get("payments") or [],
        "customer_id": off_inv.get("customer_id"),
    }

    plan = {
        "off_invoice": {
            "id": off_inv["id"], "invoice_number": off_num,
            "status": off_inv.get("status"), "amount": off_inv.get("grand_total"),
            "items_count": len(off_inv.get("items") or []),
        },
        "draft_invoice": {
            "id": draft_inv["id"], "invoice_number": draft_num,
            "status": draft_inv.get("status"), "amount": draft_inv.get("grand_total"),
        },
        "actions": [
            f"Reverse inventory ({len(inventory_plan)} line items)",
            "Reverse customer balance impact (if any)",
            f"Void OFF invoice {off_num} with audit reason",
            f"Stamp draft {draft_num} with recovery note",
            "Leave draft as 'for_preparation' for clean re-finalization",
        ],
        "inventory_plan": inventory_plan,
        "payment_plan": payment_plan,
        "issues": issues,
        "dry_run": not confirm,
    }

    # Idempotency: if OFF is already voided AND tagged with a recovery flag,
    # treat as already-done.
    if off_inv.get("status") == "voided" and off_inv.get("reconciled_into_draft"):
        return {**plan, "result": "already_done"}

    if not confirm:
        # Dry-run: return plan without mutating anything.
        return {**plan, "result": "dry_run", "next_step": "Resubmit with confirm=true and admin_pin"}

    # ── Confirmation path ────────────────────────────────────────────────────
    # Verify admin PIN
    if not admin_pin:
        raise HTTPException(400, "admin_pin required for confirmed execution")
    pin_doc = await db.system_settings.find_one({"key": "admin_pin"}, {"_id": 0})
    from utils import verify_password
    if not (pin_doc and pin_doc.get("pin_hash")
            and verify_password(admin_pin, pin_doc["pin_hash"])):
        raise HTTPException(401, "Invalid admin PIN")
    if issues:
        raise HTTPException(409, {"issues": issues, "plan": plan})

    branch_id = off_inv.get("branch_id", "")

    # 1) Reverse inventory (add back what OFF deducted)
    for plan_row in inventory_plan:
        await db.inventory.update_one(
            {"product_id": plan_row["product_id"], "branch_id": branch_id},
            {"$inc": {"quantity": plan_row["delta"]},
             "$set": {"updated_at": now_iso()}},
            upsert=True,
        )
        await log_movement(
            plan_row["product_id"], branch_id, "void_reversal", plan_row["delta"],
            off_inv["id"], off_num, 0,
            user["id"], user.get("full_name", user.get("username", "")),
            f"Reconciled into draft {draft_num} via post-bug recovery",
        )

    # 2) Reverse customer balance for credit/partial payments on OFF
    cust_id = off_inv.get("customer_id")
    bal_owed = float(off_inv.get("balance", 0))
    if cust_id and bal_owed > 0:
        await db.customers.update_one(
            {"id": cust_id},
            {"$inc": {"current_balance": -bal_owed},
             "$set": {"updated_at": now_iso()}},
        )

    # 3) Void OFF invoice with full audit trail
    await db.invoices.update_one(
        {"id": off_inv["id"]},
        {"$set": {
            "status": "voided",
            "void_reason": f"Reconciled into draft {draft_num} via post-bug recovery",
            "voided_by_id": user["id"],
            "voided_by_name": user.get("full_name", user.get("username", "")),
            "voided_at": now_iso(),
            "reconciled_into_draft": draft_num,
            "reconciled_into_draft_id": draft_inv["id"],
        }},
    )
    # Mark daily_sales_log entries as voided (best-effort; the close wizard
    # already excludes status=voided invoices from cash totals).
    try:
        await db.daily_sales_log.update_many(
            {"invoice_id": off_inv["id"]},
            {"$set": {"voided": True, "voided_at": now_iso(),
                      "void_reason": f"Reconciled into draft {draft_num}"}},
        )
    except Exception:
        pass

    # 4) Stamp draft with recovery note
    await db.invoices.update_one(
        {"id": draft_inv["id"]},
        {"$set": {
            "recovery_note": (
                f"Manually reconciled with voided OFF receipt {off_num} on "
                f"{now_iso()} by {user.get('full_name', user.get('username', ''))}. "
                f"Re-finalize via normal flow."
            ),
            "updated_at": now_iso(),
        }},
    )

    return {**plan, "result": "executed", "off_voided": off_num,
            "draft_ready": draft_num}


@router.get("/sync/estimate")
async def get_sync_estimate(user=Depends(get_current_user), branch_id: str = None):
    """
    Quick pre-download count estimate — no heavy data fetching.
    Used by the frontend to show: "~2.4 MB · 3,241 products · 152 customers"
    before the user clicks Download.
    """
    product_count = await db.products.count_documents({"active": True})

    customer_q = {"active": True}
    if branch_id:
        customer_q["branch_id"] = branch_id
    customer_count = await db.customers.count_documents(customer_q)

    inventory_count = 0
    if branch_id:
        inventory_count = await db.inventory.count_documents({"branch_id": branch_id})

    # Rough KB estimate: products ~1.5KB, customers ~0.5KB, inventory ~0.1KB
    estimated_kb = round(product_count * 1.5 + customer_count * 0.5 + inventory_count * 0.1)

    return {
        "products": product_count,
        "customers": customer_count,
        "inventory": inventory_count,
        "estimated_kb": estimated_kb,
    }


@router.get("/sync/pos-data")
async def get_pos_sync_data(user=Depends(get_current_user), branch_id: str = None, last_sync: str = None):
    """Get data for offline POS sync — includes branch-specific prices.
    If last_sync is provided, only returns records updated since that timestamp (delta sync).
    """
    is_delta = bool(last_sync)
    product_query = {"active": True}
    customer_query = {"active": True}

    # Delta sync: only fetch records updated since last_sync
    time_filter = None
    if last_sync:
        time_filter = {"$or": [
            {"updated_at": {"$gte": last_sync}},
            {"created_at": {"$gte": last_sync}},
        ]}
        product_query = {**product_query, **time_filter}
        customer_query = {**customer_query, **time_filter}

    # Products catalog (global)
    products = await db.products.find(product_query, {"_id": 0}).to_list(10000)
    
    # Customers (branch-scoped) — apply delta filter if available
    customers = await db.customers.find(customer_query, {"_id": 0}).to_list(5000)
    
    # Price schemes (global) — always full (tiny collection)
    schemes = await db.price_schemes.find({"active": True}, {"_id": 0}).to_list(50)
    
    # Inventory quantities for branch — apply delta filter
    inventory = []
    if branch_id:
        inv_query = {"branch_id": branch_id}
        if time_filter:
            inv_query = {**inv_query, **time_filter}
        inventory = await db.inventory.find(inv_query, {"_id": 0}).to_list(10000)
    elif not is_delta:
        # Full sync without branch — aggregate total stock across all branches
        agg = await db.inventory.aggregate([
            {"$group": {"_id": "$product_id", "quantity": {"$sum": "$quantity"}}}
        ]).to_list(10000)
        inventory = [{"product_id": r["_id"], "quantity": r["quantity"]} for r in agg]
    
    # Branch price overrides — apply delta filter
    branch_prices = []
    if branch_id:
        bp_query = {"branch_id": branch_id}
        if time_filter:
            bp_query = {**bp_query, **time_filter}
        branch_prices = await db.branch_prices.find(bp_query, {"_id": 0}).to_list(10000)

    # ── Critical fix (price drift): on delta sync, also include products whose
    # branch_prices row changed since last_sync — even if the master products
    # row didn't change. Otherwise the terminal's cached `product.prices` map
    # is never re-merged and the cashier sees the stale (global) price.
    # See: branch retail "Galimax 1" updated on web → terminal kept old global.
    if is_delta and branch_id and branch_prices:
        bp_pids_changed = list({bp.get("product_id") for bp in branch_prices if bp.get("product_id")})
        existing_pids = {p.get("id") for p in products}
        missing_pids = [pid for pid in bp_pids_changed if pid and pid not in existing_pids]
        if missing_pids:
            extra_products = await db.products.find(
                {"id": {"$in": missing_pids}, "active": True}, {"_id": 0}
            ).to_list(len(missing_pids))
            products.extend(extra_products)
        # Also re-emit repack children of any parent whose branch_prices.cost_price
        # changed, since their derived capital/cost depends on parent's branch cost.
        parent_pids = [
            bp.get("product_id") for bp in branch_prices
            if bp.get("product_id") and (bp.get("cost_price") is not None)
        ]
        if parent_pids:
            repack_children = await db.products.find(
                {"parent_id": {"$in": parent_pids}, "is_repack": True, "active": True},
                {"_id": 0}
            ).to_list(10000)
            for rc in repack_children:
                if rc.get("id") not in {p.get("id") for p in products}:
                    products.append(rc)

    # Deleted/deactivated products since last_sync (for delta cache cleanup)
    deleted_ids = []
    deleted_customer_ids = []
    if last_sync:
        deactivated = await db.products.find(
            {"active": False, "$or": [
                {"updated_at": {"$gte": last_sync}},
                {"deactivated_at": {"$gte": last_sync}},
            ]},
            {"_id": 0, "id": 1}
        ).to_list(1000)
        deleted_ids = [d["id"] for d in deactivated]

        # Customers deleted/deactivated since last_sync — terminal needs to purge cache
        deactivated_customers = await db.customers.find(
            {"active": False, "$or": [
                {"updated_at": {"$gte": last_sync}},
                {"deactivated_at": {"$gte": last_sync}},
            ]},
            {"_id": 0, "id": 1}
        ).to_list(5000)
        deleted_customer_ids = [d["id"] for d in deactivated_customers]

    # For delta sync: we need ALL inventory for enrichment (product.available field)
    # because even unchanged products need current stock levels
    all_inv_map = {}
    all_bp_map = {}
    disabled_at_branch_set = set()
    if is_delta and branch_id:
        # Fetch full inventory + branch prices for enrichment of delta products
        all_inv = await db.inventory.find({"branch_id": branch_id}, {"_id": 0}).to_list(10000)
        all_inv_map = {inv["product_id"]: float(inv.get("quantity", 0)) for inv in all_inv}
        # Track disabled-at-branch products so the Terminal/POS can grey them
        disabled_at_branch_set = {
            inv["product_id"] for inv in all_inv if inv.get("disabled_at_branch")
        }
        all_bp = await db.branch_prices.find({"branch_id": branch_id}, {"_id": 0}).to_list(10000)
        all_bp_map = {bp["product_id"]: bp for bp in all_bp}
    else:
        all_inv_map = {inv["product_id"]: float(inv.get("quantity", 0)) for inv in inventory}
        disabled_at_branch_set = {
            inv["product_id"] for inv in inventory if inv.get("disabled_at_branch")
        }
        all_bp_map = {bp["product_id"]: bp for bp in branch_prices}

    # Lazy auto-reactivation: clear disabled_at_branch flag wherever stock has
    # come back. Single bulk update — no per-row hooks needed.
    if branch_id:
        try:
            await db.inventory.update_many(
                {"branch_id": branch_id, "disabled_at_branch": True, "quantity": {"$gt": 0}},
                {"$set": {"disabled_at_branch": False, "auto_reactivated_at": now_iso()}},
            )
            # Also drop reactivated products from the local set
            disabled_at_branch_set = {
                pid for pid in disabled_at_branch_set
                if all_inv_map.get(pid, 0) <= 0
            }
        except Exception:
            pass

    enriched_products = []
    # ── Offline analytics: pre-compute moving_average + last_purchase per product ──
    # Server-aggregates last 30 days of movements for the branch so the Terminal
    # POS can show capital reveal data even when offline.
    ma_lp_map = {}
    if branch_id:
        thirty_days_ago = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        try:
            mv_pipeline = [
                {"$match": {
                    "branch_id": branch_id,
                    "type": {"$in": ["purchase", "transfer_in"]},
                    "created_at": {"$gte": thirty_days_ago},
                    "reversed": {"$ne": True},  # Exclude reversed movements (Fix #4)
                }},
                # Sort newest-first so $first picks the latest record per product
                # (replaces non-deterministic $last accumulator).
                {"$sort": {"created_at": -1}},
                {"$group": {
                    "_id": "$product_id",
                    "total_qty": {"$sum": {"$abs": "$quantity_change"}},
                    "total_cost": {"$sum": {"$multiply": [
                        {"$abs": "$quantity_change"},
                        {"$ifNull": ["$price_at_time", 0]}
                    ]}},
                    "last_cost": {"$first": {"$ifNull": ["$price_at_time", 0]}},
                    "last_date": {"$first": "$created_at"},
                }},
            ]
            for r in await db.movements.aggregate(mv_pipeline).to_list(20000):
                qty = float(r.get("total_qty") or 0)
                tc = float(r.get("total_cost") or 0)
                ma = round(tc / qty, 4) if qty > 0 else 0
                ma_lp_map[r["_id"]] = {
                    "moving_average": ma,
                    "last_purchase": float(r.get("last_cost") or 0),
                }
        except Exception:
            ma_lp_map = {}

    for p in products:
        p = dict(p)
        if p.get("is_repack") and p.get("parent_id"):
            parent_qty = all_inv_map.get(p["parent_id"], 0)
            units = p.get("units_per_parent", 1) or 1
            p["available"] = round(parent_qty * units, 4)
            # Derive live capital from parent's branch capital so Terminal POS
            # offline cache shows real numbers (parent's branch_prices.cost_price
            # is what the parent has after PO/transfer/manual updates).
            parent_bp = all_bp_map.get(p["parent_id"]) or {}
            parent_cost = float(parent_bp.get("cost_price")) if parent_bp.get("cost_price") is not None else None
            if parent_cost is None:
                # fallback: read parent's global cost from products list
                parent_doc = next((q for q in products if q.get("id") == p["parent_id"]), None)
                if parent_doc:
                    parent_cost = float(parent_doc.get("cost_price") or 0)
            if parent_cost is not None and units > 0:
                add_on = float(p.get("add_on_cost") or 0)
                p["cost_price"] = round(parent_cost / units + add_on, 4)
        else:
            p["available"] = all_inv_map.get(p["id"], 0)
        if p["id"] in all_bp_map:
            bp = all_bp_map[p["id"]]
            if bp.get("prices"):
                p["prices"] = {**(p.get("prices") or {}), **bp["prices"]}
            if bp.get("cost_price") is not None:
                # For repacks: branch_prices.cost_price doesn't exist (we don't
                # write it); the live derivation above is the source of truth.
                # So we only apply branch_prices.cost_price for non-repacks.
                if not p.get("is_repack"):
                    p["cost_price"] = bp["cost_price"]
        # ── Phase 1: Pre-built search index + offline analytics ──
        # Lowercase concat used by Terminal search filter — single string compare
        # instead of 3 separate lowercase ops per item per keystroke.
        p["search_blob"] = f"{(p.get('name') or '').lower()}|{(p.get('sku') or '').lower()}|{(p.get('barcode') or '').lower()}"
        # Branch-scoped disable flag — POS greys these out (cashier sees but can't sell)
        p["disabled_at_branch"] = p["id"] in disabled_at_branch_set
        ma_lp = ma_lp_map.get(p["id"])
        if ma_lp:
            p["moving_average_cost"] = ma_lp["moving_average"]
            p["last_purchase_cost"] = ma_lp["last_purchase"]
        enriched_products.append(p)
    
    # ── Phase 1: Cache admin_pin bcrypt hash for offline manager bypass ──
    # This is the bcrypt hash (one-way) — even if device is stolen, attacker
    # cannot reverse it. Local bcrypt.compareSync() validates an entered PIN
    # without requiring the server.
    admin_pin_hash = None
    try:
        admin_pin_doc = await db.system_settings.find_one(
            {"key": "admin_pin"}, {"_id": 0, "pin_hash": 1}
        )
        if admin_pin_doc:
            admin_pin_hash = admin_pin_doc.get("pin_hash")
    except Exception:
        admin_pin_hash = None

    # ── Phase 1.5: Cache branch-scoped manager + admin/owner PINs ──
    # Real-world: admin/owner is rarely in-store; managers run the POS.
    # We ship plain-text PINs for managers assigned to THIS branch + all
    # admin/owner users in the org (admin PIN works on all branches).
    # Frontend matches locally when offline. PINs are cached in IndexedDB
    # under the same trust boundary as customer balances and product
    # catalog — no additional risk.
    offline_pin_grants = []
    try:
        # Pull all admin/owner/manager candidates; we apply branch scoping below
        # to mirror routes/verify.py:_resolve_pin behaviour exactly:
        #   - admin/owner PINs work on every branch
        #   - manager PINs work on assigned branch OR if mgr has no branch_id
        users = await db.users.find(
            {
                "active": True,
                "$or": [
                    {"role": {"$in": ["admin", "owner"]}},
                    {"role": "manager"},
                    {"pin_tier": "manager"},
                ],
            },
            {"_id": 0, "id": 1, "full_name": 1, "username": 1, "role": 1,
             "manager_pin": 1, "owner_pin": 1, "branch_id": 1},
        ).to_list(50)
        for u in users:
            role = u.get("role", "")
            pin_value = ""
            method = "manager_pin"
            if role in ("admin", "owner"):
                pin_value = str(u.get("owner_pin") or u.get("manager_pin") or "").strip()
                method = "admin_pin" if role == "admin" else "owner_pin"
            else:
                pin_value = str(u.get("manager_pin") or u.get("owner_pin") or "").strip()
                # Manager scoping: if a branch was requested AND this manager
                # is bound to a different branch, skip them (matches verify.py).
                # Managers with no branch_id are multi-branch.
                mgr_branch = u.get("branch_id") or ""
                if branch_id and mgr_branch and mgr_branch != branch_id:
                    continue
            if not pin_value:
                continue
            offline_pin_grants.append({
                "verifier_id": u.get("id"),
                "verifier_name": u.get("full_name") or u.get("username") or "Manager",
                "pin": pin_value,
                "method": method,
                "role": role,
            })
    except Exception:
        offline_pin_grants = []

    # ── Phase 1: Customers enriched with credit_limit + credit_blocked_at ──
    # Already on the document; ensure the fields are explicit.
    enriched_customers = []
    for c in customers:
        c = dict(c)
        c["credit_limit"] = float(c.get("credit_limit") or 0)
        c["credit_blocked_at"] = c.get("credit_blocked_at")
        enriched_customers.append(c)

    return {
        "products": enriched_products,
        "customers": enriched_customers,
        "price_schemes": schemes,
        "inventory": inventory,
        "branch_prices": branch_prices,
        "deleted_ids": deleted_ids,
        "deleted_customer_ids": deleted_customer_ids,
        "admin_pin_hash": admin_pin_hash,
        "offline_pin_grants": offline_pin_grants,
        "sync_time": now_iso(),
        "is_delta": is_delta,
    }


@router.get("/sync/inventory-pulse")
async def get_inventory_pulse(user=Depends(get_current_user), branch_id: str = None, since: str = None):
    """Lightweight endpoint — returns only inventory quantities changed since `since`.
    Used by terminal for frequent stock-level polling (every 60s).
    """
    if not branch_id:
        return {"items": [], "pulse_time": now_iso()}
    
    query = {"branch_id": branch_id}
    if since:
        query["$or"] = [
            {"updated_at": {"$gte": since}},
        ]
    
    items = await db.inventory.find(query, {
        "_id": 0, "product_id": 1, "quantity": 1, "updated_at": 1
    }).to_list(10000)
    
    return {
        "items": items,
        "total": len(items),
        "pulse_time": now_iso(),
        "is_delta": bool(since),
    }


@router.post("/sales/sync")
async def sync_offline_sales(data: dict, user=Depends(get_current_user)):
    """
    Sync offline sales to server.
    Fixes applied vs original:
      - Calls log_movement after each inventory deduction (populates movement history)
      - Calls log_sale_items after invoice creation (populates daily sales log)
      - Warns (does not crash) when stock would go negative, but still processes sale
        so offline work isn't lost; adds a flag to the synced record for review.
    """
    sales = data.get("sales", [])
    synced = []
    errors = []

    for sale in sales:
        try:
            sale_id = sale.get("id", new_id())
            branch_id = sale.get("branch_id", "")

            # Idempotency — skip if already synced by id OR envelope_id
            envelope_id = sale.get("envelope_id", sale_id)
            existing = await db.invoices.find_one(
                {"$or": [{"id": sale_id}, {"envelope_id": envelope_id}]},
                {"_id": 0, "id": 1}
            )
            if existing:
                synced.append({"id": sale_id, "envelope_id": envelope_id, "status": "duplicate"})
                continue

            # ── Linked Offline Draft Finalization (Feb 2026) ──────────────────
            # If the offline envelope carries a draft_invoice_id, this is NOT a
            # brand-new sale — it's a finalization of an existing for_preparation
            # draft that was reserved online. Route to a dedicated handler that
            # locates the draft, applies payment & inventory ONCE, and stamps
            # `linked_offline_receipt_number` so both the OFF number and the
            # canonical draft number resolve to the same record.
            draft_invoice_id = (sale.get("draft_invoice_id") or "").strip()
            if draft_invoice_id or sale.get("kind") == "draft_finalization_offline":
                try:
                    res = await _finalize_draft_offline(sale, user, envelope_id)
                    synced.append(res)
                except HTTPException:
                    raise
                except Exception as _dexc:
                    errors.append({
                        "id": sale_id, "envelope_id": envelope_id,
                        "error": f"Draft finalization sync failed: {_dexc}",
                    })
                continue

            items = sale.get("items", [])
            subtotal = 0
            sale_date = sale.get("date") or await today_local(user.get("organization_id") or "")
            inv_number = sale.get("invoice_number", f"SYNC-{sale_id[:8]}")
            stock_warnings = []

            for item in items:
                qty = float(item.get("quantity", 0))
                rate = float(item.get("rate", item.get("price", 0)))
                # ── Iter 240 fix: respect per-line discounts ──────────────
                # Previously: line_total = qty * rate (ignoring discount).
                # That overwrote the correct discounted `total` the
                # frontend sent and persisted an inflated value on the
                # saved invoice (e.g. retail 510, discount 20 → was
                # stored as 510 instead of 490). Mirrors the same math
                # routes/sales.py uses on the online path.
                disc_type = item.get("discount_type", "amount")
                disc_val = float(item.get("discount_value", 0))
                if disc_type == "percent":
                    disc_amt = round(qty * rate * disc_val / 100, 2)
                else:
                    disc_amt = round(qty * disc_val, 2)
                line_total = round(qty * rate - disc_amt, 2)
                item["discount_type"] = disc_type
                item["discount_value"] = disc_val
                item["discount_amount"] = disc_amt
                item["total"] = line_total
                subtotal += line_total

                product = await db.products.find_one({"id": item.get("product_id")}, {"_id": 0})
                if not product:
                    continue

                if product.get("is_repack") and product.get("parent_id"):
                    units_per_parent = product.get("units_per_parent", 1)
                    parent_deduction = qty / units_per_parent

                    # Check stock — warn if would go negative but don't block (offline work must not be lost)
                    parent_inv = await db.inventory.find_one(
                        {"product_id": product["parent_id"], "branch_id": branch_id}, {"_id": 0}
                    )
                    current_stock = float(parent_inv["quantity"]) if parent_inv else 0.0
                    if current_stock < parent_deduction:
                        stock_warnings.append(
                            f"{product['name']}: need {parent_deduction:.4f} boxes, "
                            f"have {current_stock:.4f} — inventory will go negative"
                        )

                    await db.inventory.update_one(
                        {"product_id": product["parent_id"], "branch_id": branch_id},
                        {"$inc": {"quantity": -parent_deduction}, "$set": {"updated_at": now_iso()}},
                        upsert=True
                    )
                    # ── FIX: log movement (was missing) ──────────────────────────
                    await log_movement(
                        product["parent_id"], branch_id, "sale", -parent_deduction,
                        sale_id, inv_number, rate * units_per_parent,
                        user["id"], user.get("full_name", user["username"]),
                        f"Offline sale (synced): {product['name']} x {qty}"
                    )
                else:
                    # Regular product
                    inv = await db.inventory.find_one(
                        {"product_id": item.get("product_id"), "branch_id": branch_id}, {"_id": 0}
                    )
                    current_stock = float(inv["quantity"]) if inv else 0.0
                    if current_stock < qty:
                        stock_warnings.append(
                            f"{product['name']}: need {qty}, have {current_stock:.2f} — inventory will go negative"
                        )

                    await db.inventory.update_one(
                        {"product_id": item.get("product_id"), "branch_id": branch_id},
                        {"$inc": {"quantity": -qty}, "$set": {"updated_at": now_iso()}},
                        upsert=True
                    )
                    # ── FIX: log movement (was missing) ──────────────────────────
                    await log_movement(
                        item.get("product_id"), branch_id, "sale", -qty,
                        sale_id, inv_number, rate,
                        user["id"], user.get("full_name", user["username"]),
                        "Offline sale (synced)"
                    )

            # Create invoice
            invoice = {
                "id": sale_id,
                "envelope_id": envelope_id,  # for idempotent re-sync detection
                "invoice_number": inv_number,
                "prefix": sale.get("prefix", "SYNC"),
                "customer_id": sale.get("customer_id"),
                "customer_name": sale.get("customer_name", "Walk-in"),
                "branch_id": branch_id,
                "order_date": sale_date,
                "invoice_date": sale_date,
                "due_date": sale_date,
                "items": items,
                "subtotal": subtotal,
                "freight": float(sale.get("freight", 0)),
                "overall_discount": float(sale.get("overall_discount", 0)),
                "grand_total": round(subtotal + float(sale.get("freight", 0)) - float(sale.get("overall_discount", 0)), 2),
                "amount_paid": float(sale.get("amount_paid", subtotal)),
                "balance": float(sale.get("balance", 0)),
                "status": sale.get("status", "paid"),
                "payment_type": sale.get("payment_type", "cash"),
                "payments": sale.get("payments", []),
                "cashier_id": user["id"],
                "cashier_name": user.get("full_name", user["username"]),
                "synced_from_offline": True,
                "offline_timestamp": sale.get("timestamp", ""),
                "stock_warnings": stock_warnings,   # attached for audit review
                "created_at": now_iso(),
            }

            try:
                await db.invoices.insert_one(invoice)
            except Exception as _dup_err:
                # Defense-in-depth: unique index on envelope_id can reject a true
                # network-race retry. Treat as duplicate (not a hard error) so the
                # client clears the pending entry instead of looping forever.
                err_name = type(_dup_err).__name__
                if "Duplicate" in err_name or "duplicate" in str(_dup_err).lower() or "E11000" in str(_dup_err):
                    synced.append({"id": sale_id, "envelope_id": envelope_id, "status": "duplicate"})
                    continue
                raise
            invoice.pop("_id", None)

            # ── Phase 2: Retroactive signature_session for offline credit sales ──
            # When credit sale was captured offline, the cashier could not create a
            # server-side signature_session. Instead they collected a Manager PIN
            # bypass locally with a reason. Now (during sync replay) we create
            # the session with status=bypassed and link it to the invoice for audit.
            offline_bypass = sale.get("offline_bypass") or None
            # Defensive: only operate on dict payloads — a malformed type would
            # otherwise raise AttributeError below (swallowed but noisy in logs).
            if not isinstance(offline_bypass, dict):
                offline_bypass = None
            if offline_bypass and invoice.get("balance", 0) > 0 and invoice.get("customer_id"):
                try:
                    sig_session_id = new_id()
                    sig_session = {
                        "id": sig_session_id,
                        "token": new_id(),  # not used for verification (offline path)
                        "organization_id": user.get("organization_id", ""),
                        "branch_id": branch_id,
                        "credit_context": {
                            "customer_name": invoice.get("customer_name", ""),
                            "amount": float(invoice.get("balance") or 0),
                            "credit_type": offline_bypass.get("credit_type", "by_term"),
                            "date": invoice.get("order_date") or await today_local(user.get("organization_id") or ""),
                            "branch_name": offline_bypass.get("branch_name", ""),
                            "description": "Offline credit sale (manager PIN bypass)",
                            "invoice_number": inv_number,
                            "items": invoice.get("items", []),
                            "subtotal": invoice.get("subtotal"),
                            "discount": invoice.get("overall_discount"),
                            "partial_paid": invoice.get("amount_paid"),
                        },
                        "linked_record_type": "invoice",
                        "linked_record_id": sale_id,
                        "status": "bypassed",
                        "signature_r2_key": None,
                        "signature_url": None,
                        "signed_at": None,
                        "signer_info": None,
                        "bypass_method": offline_bypass.get("method", "manager_pin"),
                        "bypass_by_id": offline_bypass.get("by_id") or user["id"],
                        "bypass_by_name": offline_bypass.get("by_name") or user.get("full_name", user.get("username", "")),
                        "bypass_reason": (offline_bypass.get("reason") or "Offline credit sale - customer unable to sign").strip(),
                        "bypassed_at": offline_bypass.get("at") or sale.get("timestamp") or now_iso(),
                        "expires_at": now_iso(),
                        "created_by_id": user["id"],
                        "created_by_name": user.get("full_name", user.get("username", "")),
                        "created_at": offline_bypass.get("at") or sale.get("timestamp") or now_iso(),
                        "offline_origin": True,
                    }
                    await db.signature_sessions.insert_one(sig_session)
                    # Back-link to invoice
                    await db.invoices.update_one(
                        {"id": sale_id},
                        {"$set": {
                            "signature_session_id": sig_session_id,
                            "signature_bypass_method": sig_session["bypass_method"],
                            "signature_bypass_reason": sig_session["bypass_reason"],
                            "signature_signed_at": sig_session["bypassed_at"],
                            "offline_signature_origin": True,
                        }},
                    )
                except Exception as _e:
                    # Non-blocking — sale is already saved; log for audit follow-up
                    import logging
                    logging.getLogger(__name__).warning(f"Offline bypass session create failed for {sale_id}: {_e}")

            if invoice["amount_paid"] > 0:
                await update_cashier_wallet(branch_id, invoice["amount_paid"], f"Synced sale {inv_number}")

            if invoice.get("customer_id") and invoice["balance"] > 0:
                await db.customers.update_one(
                    {"id": invoice["customer_id"]},
                    {"$inc": {"balance": invoice["balance"]}}
                )

            # ── FIX: log to daily sales log (was missing) ────────────────────
            log_date = sale.get("order_date") or invoice.get("order_date") or await today_local(user.get("organization_id") or "")
            enriched = []
            for item in items:
                prod = await db.products.find_one({"id": item.get("product_id")}, {"_id": 0, "category": 1})
                enriched.append({**item, "category": prod.get("category", "General") if prod else "General"})

            payment_method = sale.get("payment_method", "cash" if invoice["payment_type"] == "cash" else "credit")
            await log_sale_items(
                branch_id, log_date, enriched, inv_number,
                invoice["customer_name"], payment_method,
                user.get("full_name", user["username"])
            )

            # ── Phase 2-cont'd: Replay offline price-match changes ──────────
            # When a cashier offline-changes a price, the sale is queued with
            # `price_changes` + `price_match_pin`. We re-validate the PIN
            # against the CURRENT bcrypt hash here, then upsert
            # `branch_prices` (unless `customer_only=True`) and append a
            # `price_change_log` entry. PIN failures don't block the sale —
            # the goods are already gone — but we flag the row so admins
            # can review.
            offline_price_changes = sale.get("price_changes") or []
            if offline_price_changes:
                from routes.verify import verify_pin_for_action
                pm_pin = (sale.get("price_match_pin") or "").strip()
                pm_verifier = None
                if pm_pin:
                    try:
                        pm_verifier = await verify_pin_for_action(
                            pm_pin, "price_match", branch_id=branch_id
                        )
                    except Exception:
                        pm_verifier = None
                pm_failed_resync = pm_pin and not pm_verifier
                approver_name = (pm_verifier or {}).get("verifier_name") or (pm_verifier or {}).get("full_name") or ""
                approver_method = (pm_verifier or {}).get("method", "")
                approver_id = (pm_verifier or {}).get("verifier_id") or (pm_verifier or {}).get("id") or ""

                for pc in offline_price_changes:
                    pid = (pc.get("product_id") or "").strip()
                    if not pid:
                        continue
                    try:
                        new_p = float(pc.get("new_price"))
                    except (TypeError, ValueError):
                        continue
                    if new_p <= 0:
                        continue
                    scheme = (pc.get("scheme") or "retail").strip()
                    is_customer_only = bool(pc.get("customer_only", False))
                    # Re-fetch the server-trusted old_price the same way
                    # the live /unified-sale path does (branch override → global).
                    bp_doc = await db.branch_prices.find_one(
                        {"product_id": pid, "branch_id": branch_id}, {"_id": 0}
                    ) or {}
                    bp_prices = bp_doc.get("prices") or {}
                    server_old = bp_prices.get(scheme)
                    if server_old is None:
                        prod_doc = await db.products.find_one({"id": pid}, {"_id": 0, "name": 1, "sku": 1, "prices": 1}) or {}
                        server_old = (prod_doc.get("prices") or {}).get(scheme, 0)
                    else:
                        prod_doc = await db.products.find_one({"id": pid}, {"_id": 0, "name": 1, "sku": 1}) or {}
                    try:
                        server_old = float(server_old or 0)
                    except (TypeError, ValueError):
                        server_old = 0
                    # Upsert branch_prices ONLY when:
                    #   (a) PIN re-verified successfully, AND
                    #   (b) the change is NOT customer-only
                    if (not pm_failed_resync) and (not is_customer_only) and server_old != new_p:
                        existing_bp = bp_doc or {}
                        merged_prices = (existing_bp.get("prices") or {})
                        merged_prices[scheme] = new_p
                        await db.branch_prices.update_one(
                            {"product_id": pid, "branch_id": branch_id},
                            {
                                "$set": {
                                    "prices": merged_prices,
                                    "updated_at": now_iso(),
                                    "updated_by_id": user["id"],
                                    "updated_by_name": user.get("full_name", user.get("username", "")),
                                    "source": "pos_price_match_offline_replay",
                                },
                                "$setOnInsert": {
                                    "id": new_id(),
                                    "product_id": pid,
                                    "branch_id": branch_id,
                                    "created_at": now_iso(),
                                },
                            },
                            upsert=True,
                        )
                    # Always log to price_change_log so the audit trail is complete
                    await db.price_change_log.insert_one({
                        "id": new_id(),
                        "product_id": pid,
                        "product_name": prod_doc.get("name", ""),
                        "sku": prod_doc.get("sku", ""),
                        "branch_id": branch_id,
                        "branch_name": sale.get("branch_name", ""),
                        "scheme": scheme,
                        "old_price": server_old,
                        "client_old_price_hint": float(pc.get("old_price") or 0),
                        "new_price": new_p,
                        "delta": round(new_p - server_old, 2),
                        "delta_pct": round((new_p - server_old) / server_old * 100, 2) if server_old > 0 else 0,
                        "reason": (pc.get("reason") or "").strip(),
                        "reason_detail": (pc.get("reason_detail") or "").strip(),
                        "customer_only": is_customer_only,
                        "scope": "customer_only" if is_customer_only else "branch_permanent",
                        "invoice_id": sale_id,
                        "invoice_number": inv_number,
                        "customer_id": invoice.get("customer_id"),
                        "customer_name": invoice.get("customer_name", ""),
                        "cashier_id": user["id"],
                        "cashier_name": user.get("full_name", user.get("username", "")),
                        "approver_id": approver_id,
                        "approver_name": approver_name,
                        "approver_method": approver_method,
                        "offline_origin": True,
                        "pin_resync_failed": pm_failed_resync,
                        "date": log_date,
                        "created_at": now_iso(),
                        "organization_id": user.get("organization_id"),
                    })

            result = {"id": sale_id, "status": "synced", "invoice_number": inv_number}
            if stock_warnings:
                result["stock_warnings"] = stock_warnings
            synced.append(result)

            # SMS hook: notify customer on credit sale (synced from offline)
            if invoice.get("balance", 0) > 0 and invoice.get("customer_id") and invoice.get("sale_type") not in ("interest_charge", "penalty_charge"):
                try:
                    from routes.sms_hooks import on_credit_sale_created
                    await on_credit_sale_created(invoice)
                except Exception:
                    pass

        except Exception as e:
            errors.append({"id": sale.get("id"), "error": str(e)})

    return {
        "synced": synced,
        "errors": errors,
        "total_synced": len([s for s in synced if s.get("status") == "synced"]),
        "total_errors": len(errors),
        "results": synced,
    }



@router.get("/sync/offline-summary")
async def get_offline_sync_summary(user=Depends(get_current_user), branch_id: str = None, days: str = "7"):
    """Phase 3: Stock warning summary for offline-synced sales.

    Returns counts of sales that synced from offline mode and how many had
    stock warnings (inventory went negative). Used by Dashboard widget to
    surface offline operations needing manager review.

    `days` is accepted as string so we can clamp invalid values gracefully
    instead of returning a hard 422 from FastAPI's int coercion.
    """
    try:
        days_i = max(1, min(int(days or 7), 90))
    except Exception:
        days_i = 7
    days = days_i
    cutoff_iso = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    base_q = {
        "synced_from_offline": True,
        "created_at": {"$gte": cutoff_iso},
    }
    if branch_id:
        base_q["branch_id"] = branch_id

    total = await db.invoices.count_documents(base_q)

    warn_q = dict(base_q)
    warn_q["stock_warnings.0"] = {"$exists": True}  # array has at least 1 element
    warned = await db.invoices.count_documents(warn_q)

    # Recent samples for surfacing
    samples = await db.invoices.find(
        warn_q,
        {"_id": 0, "id": 1, "invoice_number": 1, "customer_name": 1,
         "branch_id": 1, "stock_warnings": 1, "created_at": 1, "grand_total": 1},
    ).sort("created_at", -1).to_list(20)

    # Offline credit sales (manager-PIN bypass) needing review
    offline_credit_q = {
        "synced_from_offline": True,
        "offline_signature_origin": True,
        "created_at": {"$gte": cutoff_iso},
    }
    if branch_id:
        offline_credit_q["branch_id"] = branch_id
    offline_credit_count = await db.invoices.count_documents(offline_credit_q)

    return {
        "period_days": days,
        "branch_id": branch_id,
        "total_synced": total,
        "warned_count": warned,
        "offline_credit_count": offline_credit_count,
        "samples": samples,
    }

"""
Unified Sales endpoint: handles cash, partial, credit, digital, and split sales.
"""
from fastapi import APIRouter, Depends, HTTPException
from datetime import datetime, timezone, timedelta
from config import db
from utils import (
    get_current_user, check_perm, has_perm, now_iso, new_id,
    log_movement, log_sale_items, update_cashier_wallet,
    update_digital_wallet, is_digital_payment, get_branch_cost,
    generate_next_number, check_idempotency, ensure_org_context,
)

router = APIRouter(tags=["Sales"])


@router.post("/unified-sale")
async def create_unified_sale(data: dict, user=Depends(get_current_user)):
    """
    Unified sales endpoint that handles all sale types:
    - Cash sales (immediate payment)
    - Partial payment (creates invoice with balance)
    - Credit sales (creates invoice, full balance to AR)
    
    Always creates an invoice record for proper tracking.
    """
    check_perm(user, "pos", "sell")
    
    branch_id = data.get("branch_id")
    if not branch_id:
        raise HTTPException(status_code=400, detail="Branch ID required")

    # Ensure org context for super admin
    await ensure_org_context(branch_id=branch_id)

    # ── Closed-day guard (with Late-Encode escape valve) ─────────────────────
    # Block sales encoding on days that have already been formally closed.
    # A closed day's Z-report is final — new sales there would be invisible.
    # EXCEPT: credit / partial sales can be late-encoded with manager PIN +
    # reason — those flow into the NEXT open day's Z-report as a clearly-
    # tagged carryover line (the closed Z-report itself stays untouched).
    order_date = data.get("order_date", now_iso()[:10])
    late_encode = data.get("late_encode") or {}
    closed_day = await db.daily_closings.find_one(
        {"branch_id": branch_id, "date": order_date, "status": "closed"},
        {"_id": 0, "date": 1}
    )
    if closed_day:
        # Determine payment type early so we can check if late-encode applies
        _ptype_check = (data.get("payment_type") or "").lower()
        _fsrc_check = (data.get("fund_source") or "").lower()
        is_credit_or_partial = _ptype_check in ("credit", "partial")

        if not late_encode:
            raise HTTPException(
                status_code=403,
                detail=f"Cannot encode sales for {order_date} — this day is already closed. For forgotten credit sales, use the 'Encode for past date' flow."
            )

        # Late-encode path — validate hard constraints
        if not is_credit_or_partial or _fsrc_check == "digital":
            raise HTTPException(
                status_code=403,
                detail="Late-encode is only permitted for credit / partial sales. Cash and digital transactions cannot be backdated to a closed day."
            )

        # 7-day backdate cap
        from datetime import date as _date
        try:
            _order_d = datetime.strptime(order_date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid order_date")
        days_back = (_date.today() - _order_d).days
        if days_back > 7:
            raise HTTPException(
                status_code=403,
                detail=f"Late-encode limited to the last 7 days ({days_back} days back requested). For older entries, use a journal adjustment with your accountant."
            )
        if days_back < 0:
            raise HTTPException(status_code=400, detail="Cannot late-encode a future date.")

        # Cross-month block — protects VAT filings
        if _order_d.month != _date.today().month or _order_d.year != _date.today().year:
            raise HTTPException(
                status_code=403,
                detail="Late-encode cannot cross a month boundary (protects VAT filing integrity). Use a journal adjustment instead."
            )

        # Reason required, min 10 chars
        le_reason = (late_encode.get("reason") or "").strip()
        if len(le_reason) < 10:
            raise HTTPException(status_code=400, detail="Late-encode reason must be at least 10 characters.")

        # Manager / admin PIN required
        le_pin = str(late_encode.get("pin", ""))
        if not le_pin:
            raise HTTPException(status_code=400, detail="Manager PIN is required for late-encode.")
        from routes.verify import verify_pin_for_action
        le_verifier = await verify_pin_for_action(le_pin, "transaction_verify", branch_id=branch_id)
        if not le_verifier:
            raise HTTPException(status_code=403, detail="Invalid PIN — manager or admin required for late-encode.")

        # Daily cap — max 5 late-encodes per branch per day
        today_str = _date.today().strftime("%Y-%m-%d")
        todays_count = await db.invoices.count_documents({
            "branch_id": branch_id, "late_encoded": True,
            "late_encoded_at": {"$gte": f"{today_str}T00:00:00", "$lt": f"{today_str}T23:59:59"},
        })
        if todays_count >= 5:
            raise HTTPException(
                status_code=403,
                detail="Daily late-encode limit reached (5/day). If this is routine, restructure your workflow — the system is telling you something."
            )

        # Stash context for downstream tagging
        data["_late_encode_ctx"] = {
            "reason": le_reason,
            "verifier": le_verifier,
            "days_back": days_back,
        }

    # ── Floor-date guard ──────────────────────────────────────────────────────
    # Block sales on dates before the system's first operational day for this
    # branch. This prevents encoding to dates when the system didn't exist yet.
    earliest_dates = []
    for coll, field in [(db.sales_log, "date"), (db.expenses, "date"), (db.invoices, "order_date")]:
        doc = await coll.find_one({"branch_id": branch_id}, {"_id": 0, field: 1}, sort=[(field, 1)])
        if doc and doc.get(field):
            earliest_dates.append(doc[field])
    if earliest_dates:
        floor_date = min(earliest_dates)
        if order_date < floor_date:
            raise HTTPException(
                status_code=403,
                detail=f"Cannot encode sales for {order_date} — this date is before the system start date ({floor_date})."
            )
    
    # Idempotency check — prevent duplicate transactions from offline sync
    idem_key = data.get("idempotency_key")
    if idem_key:
        existing = await check_idempotency("invoices", idem_key)
        if existing:
            return existing
    
    items = data.get("items", [])
    if not items:
        raise HTTPException(status_code=400, detail="No items in sale")

    # ── Permission guard: discounts ──────────────────────────────────────────
    can_discount = has_perm(user, "sales", "give_discount")
    if not can_discount:
        overall_disc_val = float(data.get("overall_discount", 0))
        if overall_disc_val > 0:
            raise HTTPException(status_code=403, detail="You do not have permission to apply discounts.")
        for item in items:
            if float(item.get("discount_value", 0)) > 0:
                raise HTTPException(status_code=403, detail=f"You do not have permission to apply discounts (item: {item.get('product_name', '')}).")

    # ── Permission guard: sell below cost ────────────────────────────────────
    can_sell_below = has_perm(user, "sales", "sell_below_cost")

    # Release mode: "full" (deduct immediately) or "partial" (reserve stock)
    release_mode = data.get("release_mode", "full")

    # PIN enforcement for discounted items (pos_discount policy)
    has_discount = any(float(item.get("discount_value", 0)) > 0 for item in items)
    discount_pin = data.get("discount_pin", "")
    if has_discount and discount_pin:
        from routes.verify import verify_pin_for_action
        verifier = await verify_pin_for_action(discount_pin, "pos_discount")
        if not verifier:
            raise HTTPException(status_code=403, detail="Invalid PIN for discount authorization")

    customer_id = data.get("customer_id")
    customer_name = data.get("customer_name", "Walk-in")
    payment_type = data.get("payment_type", "cash")  # cash, partial, credit
    
    # Validate customer_id resolves to an active customer for credit/partial sales.
    # Prevents orphan invoices when a customer was deleted but still cached client-side.
    if payment_type in ["partial", "credit"] and customer_id:
        customer = await db.customers.find_one({"id": customer_id, "active": True}, {"_id": 0})
        if not customer:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Customer '{customer_name}' no longer exists in the system "
                    "(they may have been deleted). Please refresh your customer list "
                    "and re-select the customer, or process this as a cash sale."
                ),
            )
        current_balance = customer.get("balance", 0)
        credit_limit = customer.get("credit_limit", 0)
        balance_due = float(data.get("balance", 0))

        # Only block if credit limit is set and exceeded (unless manager approved)
        if credit_limit > 0 and not data.get("approved_by"):
            new_total = current_balance + balance_due
            if new_total > credit_limit:
                raise HTTPException(
                    status_code=400,
                    detail=f"Credit limit exceeded. Limit: ₱{credit_limit:.2f}, Current: ₱{current_balance:.2f}, This sale: ₱{balance_due:.2f}"
                )
    elif customer_id:
        # Cash sale with a customer_id — silently strip if customer doesn't exist
        # so the sale completes as a clean walk-in instead of attaching to a ghost.
        ghost = await db.customers.find_one({"id": customer_id, "active": True}, {"_id": 0, "id": 1})
        if not ghost:
            customer_id = None
    
    # Get prefix settings
    settings = await db.settings.find_one({"key": "invoice_prefixes"}, {"_id": 0})
    prefix = data.get("prefix", settings.get("value", {}).get("sales_invoice", "SI") if settings else "SI")
    
    # Generate invoice number (atomic, branch-specific)
    inv_number = await generate_next_number(prefix, branch_id)
    
    # ── Batch-fetch all products upfront — eliminates N+1 queries ─────────────
    product_ids = list({item["product_id"] for item in items})
    products_list = await db.products.find(
        {"id": {"$in": product_ids}, "active": True}, {"_id": 0}
    ).to_list(len(product_ids))
    products_map = {p["id"]: p for p in products_list}

    # ── Just-In-Time (JIT) retail prices for repacks ──────────────────────────
    # The Sales UI may surface an inline retail input for repack lines whose
    # branch has no retail price set. Those entries arrive as `jit_retail_prices`
    # and are persisted to branch_prices after Owner PIN verification.
    jit_retail_prices = data.get("jit_retail_prices", []) or []
    jit_owner_pin = (data.get("jit_owner_pin") or "").strip()
    jit_persisted = []
    if jit_retail_prices:
        # Filter to actual repack products
        valid_jit = []
        for entry in jit_retail_prices:
            pid = (entry.get("product_id") or "").strip()
            try:
                rp = float(entry.get("retail"))
            except (TypeError, ValueError):
                continue
            if rp <= 0 or not pid:
                continue
            prod = products_map.get(pid)
            if not prod or not prod.get("is_repack"):
                continue
            valid_jit.append({"product_id": pid, "retail": rp})
        if valid_jit:
            if not jit_owner_pin:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "type": "jit_retail_pin_required",
                        "items": valid_jit,
                        "message": f"Owner PIN required to save {len(valid_jit)} new repack retail price(s).",
                    }
                )
            from routes.verify import verify_pin_for_action
            jit_verifier = await verify_pin_for_action(jit_owner_pin, "repack_retail_save")
            if not jit_verifier:
                raise HTTPException(status_code=403, detail="Invalid Owner PIN — cannot save repack retail prices")
            for v in valid_jit:
                existing = await db.branch_prices.find_one(
                    {"product_id": v["product_id"], "branch_id": branch_id}, {"_id": 0}
                )
                merged_prices = (existing or {}).get("prices", {}) or {}
                merged_prices["retail"] = v["retail"]
                await db.branch_prices.update_one(
                    {"product_id": v["product_id"], "branch_id": branch_id},
                    {
                        "$set": {
                            "prices": merged_prices,
                            "updated_at": now_iso(),
                            "updated_by": user.get("full_name", user.get("username", "")),
                            "source": "sales_jit",
                        },
                        "$setOnInsert": {
                            "id": new_id(),
                            "product_id": v["product_id"],
                            "branch_id": branch_id,
                            "created_at": now_iso(),
                        },
                    },
                    upsert=True,
                )
                jit_persisted.append(v)

    # ── Price Match (permanent branch price change) — pre-validate + PIN ─────
    # Each entry: {product_id, scheme, old_price, new_price, reason, reason_detail?}
    # On approval: upsert branch_prices.prices[scheme] AND write to price_change_log.
    # IMPORTANT: old_price for the audit log is read SERVER-SIDE from
    # branch_prices.prices[scheme] (fall back to products.prices[scheme]) — the
    # client-supplied old_price is treated as a hint only. This prevents a
    # malicious client from logging a misleading old_price.
    price_changes_in = data.get("price_changes", []) or []
    valid_price_changes = []
    if price_changes_in:
        active_scheme = data.get("price_scheme", "retail")
        # Bulk-fetch existing branch_prices for this branch+products in one query
        pm_pids = [str(pc.get("product_id") or "").strip() for pc in price_changes_in if pc.get("product_id")]
        existing_bp = {}
        if pm_pids:
            bp_rows = await db.branch_prices.find(
                {"product_id": {"$in": pm_pids}, "branch_id": branch_id}, {"_id": 0}
            ).to_list(len(pm_pids))
            existing_bp = {bp["product_id"]: bp for bp in bp_rows}
        for pc in price_changes_in:
            pid = (pc.get("product_id") or "").strip()
            if not pid:
                continue
            try:
                new_p = float(pc.get("new_price"))
            except (TypeError, ValueError):
                continue
            if new_p <= 0:
                continue
            prod = products_map.get(pid)
            if not prod:
                continue
            scheme = (pc.get("scheme") or active_scheme).strip()
            # Server-trusted old_price: branch override first, else global
            bp_doc = existing_bp.get(pid) or {}
            bp_prices = bp_doc.get("prices") or {}
            server_old = bp_prices.get(scheme)
            if server_old is None:
                server_old = (prod.get("prices") or {}).get(scheme, 0)
            try:
                server_old = float(server_old or 0)
            except (TypeError, ValueError):
                server_old = 0
            # No price change if server price already equals new price
            if server_old > 0 and abs(server_old - new_p) < 0.001:
                continue
            reason = (pc.get("reason") or "").strip()
            if not reason:
                raise HTTPException(
                    status_code=400,
                    detail=f"Price change reason missing for '{prod['name']}'"
                )
            valid_price_changes.append({
                "product_id": pid,
                "product_name": prod["name"],
                "sku": prod.get("sku", ""),
                "scheme": scheme,
                "old_price": server_old,  # server-trusted
                "new_price": new_p,
                "client_old_price_hint": float(pc.get("old_price") or 0),
                "reason": reason,
                "reason_detail": (pc.get("reason_detail") or "").strip(),
            })
        if valid_price_changes:
            pm_pin = (data.get("price_match_pin") or "").strip()
            if not pm_pin:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "type": "price_match_pin_required",
                        "items": valid_price_changes,
                        "message": f"Manager / Admin PIN required to approve {len(valid_price_changes)} price change(s).",
                    }
                )
            from routes.verify import verify_pin_for_action
            pm_verifier = await verify_pin_for_action(pm_pin, "price_match", branch_id=branch_id)
            if not pm_verifier:
                raise HTTPException(status_code=403, detail="Invalid PIN — price match not authorized")
            # stash verifier on payload for later persistence
            data["_price_match_verifier"] = pm_verifier

    # Process items and compute totals
    sale_items = []
    subtotal = 0
    reservations_to_create = []   # populated only for partial release
    discount_audit_entries = []   # populated for discount/price override audit trail

    # ── Manager Override: pre-validate stock before the main loop ─────────────
    # If any item has insufficient stock, return a structured error so the frontend
    # can show the override modal. If manager_override_pin is provided, verify it
    # once here and allow all items to proceed (inventory goes negative — visible,
    # auditable, and self-healed when the missing PO is later encoded).
    override_pin = data.get("manager_override_pin", "").strip()
    override_verifier = None
    insufficient_items = []

    for item in items:
        product = products_map.get(item["product_id"])
        if not product:
            continue
        qty = float(item.get("quantity", 0))
        if product.get("is_repack") and product.get("parent_id"):
            parent_inv = await db.inventory.find_one(
                {"product_id": product["parent_id"], "branch_id": branch_id}, {"_id": 0}
            )
            parent_stock = float(parent_inv["quantity"]) if parent_inv else 0
            units_per_parent = product.get("units_per_parent", 1)
            if (parent_stock * units_per_parent) < qty:
                insufficient_items.append({
                    "product_id": product["id"],
                    "product_name": product["name"],
                    "system_qty": round(parent_stock * units_per_parent, 2),
                    "needed_qty": qty,
                })
        else:
            inv = await db.inventory.find_one(
                {"product_id": item["product_id"], "branch_id": branch_id}, {"_id": 0}
            )
            current_stock = float(inv["quantity"]) if inv else 0
            if current_stock < qty:
                insufficient_items.append({
                    "product_id": item["product_id"],
                    "product_name": product["name"],
                    "system_qty": round(current_stock, 2),
                    "needed_qty": qty,
                })

    if insufficient_items:
        if not override_pin:
            raise HTTPException(
                status_code=422,
                detail={
                    "type": "insufficient_stock",
                    "items": insufficient_items,
                    "message": f"Insufficient stock for {len(insufficient_items)} item(s). Manager PIN required to override.",
                }
            )
        from routes.verify import verify_pin_for_action
        override_verifier = await verify_pin_for_action(override_pin, "stock_negative_override", branch_id=branch_id)
        if not override_verifier:
            raise HTTPException(status_code=403, detail="Invalid override PIN — stock exception denied")

    for item in items:
        product = products_map.get(item["product_id"])
        if not product:
            raise HTTPException(status_code=400, detail=f"Product not found: {item['product_id']}")
        
        qty = float(item.get("quantity", 0))
        rate = float(item.get("rate", item.get("price", 0)))
        
        # Check capital rule using branch-specific cost (falls back to global cost)
        branch_cost = await get_branch_cost(product, branch_id)
        if not can_sell_below and rate > 0 and rate < branch_cost:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot sell '{product['name']}' at ₱{rate:.2f} — below capital ₱{branch_cost:.2f}"
            )
        
        disc_type = item.get("discount_type", "amount")
        disc_val = float(item.get("discount_value", 0))
        disc_amt = disc_val if disc_type == "amount" else round(qty * rate * disc_val / 100, 2)
        line_total = round(qty * rate - disc_amt, 2)

        # Check capital rule AFTER discount — net price per unit must not fall below capital
        if not can_sell_below and qty > 0 and branch_cost > 0:
            net_per_unit = line_total / qty
            if net_per_unit < branch_cost:
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot sell '{product['name']}' — after discount, net price ₱{net_per_unit:.2f}/unit is below capital ₱{branch_cost:.2f}"
                )
        
        # Track discount/price override data for audit log
        scheme_price = product.get("prices", {}).get(data.get("price_scheme", "retail"), rate)
        # If this product has a Price Match entry, treat the MATCHED price as
        # the effective scheme_price so the discount_audit_log reads cleanly
        # (e.g. after matching ₱100→₱95 with a ₱5 discount: audit says
        # original=95, sold=95, discount=5, type=line_discount — NOT a
        # confused "discount_and_override"). The true price change is already
        # logged separately in price_change_log.
        pc_match = next((pc for pc in valid_price_changes if pc["product_id"] == item["product_id"]), None)
        has_price_match = pc_match is not None
        effective_scheme_price = pc_match["new_price"] if has_price_match else scheme_price
        if disc_amt > 0 or (rate != effective_scheme_price and not has_price_match):
            discount_audit_entries.append({
                "product_id": item["product_id"],
                "product_name": product["name"],
                "original_price": effective_scheme_price,
                "sold_price": rate,
                "discount_type": disc_type,
                "discount_value": disc_val,
                "discount_amount": disc_amt,
                "quantity": qty,
                "net_per_unit": round(line_total / qty, 2) if qty > 0 else 0,
                "capital": branch_cost,
                "type": "price_override" if rate != effective_scheme_price and disc_amt == 0
                    else "line_discount" if disc_amt > 0 and rate == effective_scheme_price
                    else "discount_and_override",
            })
        
        # ── Inventory: deduct immediately for BOTH full and partial release ────
        # Partial release: also moves qty into reserved_qty bucket on same record.
        # quantity = available to sell. reserved_qty = customer's stock, pending pickup.
        # quantity + reserved_qty = total physical on shelf (always accurate).
        if product.get("is_repack") and product.get("parent_id"):
            units_per_parent = product.get("units_per_parent", 1)
            parent_deduction = qty / units_per_parent
            parent_inv = await db.inventory.find_one(
                {"product_id": product["parent_id"], "branch_id": branch_id}, {"_id": 0}
            )
            parent_stock = float(parent_inv["quantity"]) if parent_inv else 0
            available = parent_stock * units_per_parent
            if available < qty and not override_verifier:
                raise HTTPException(
                    status_code=400,
                    detail=f"Insufficient stock for {product['name']}: have {available:.0f}, need {qty:.0f}"
                )
            # Deduct from quantity — same for full and partial
            inv_update = {"$inc": {"quantity": -parent_deduction}, "$set": {"updated_at": now_iso()}}
            if release_mode == "partial":
                inv_update["$inc"]["reserved_qty"] = parent_deduction
            await db.inventory.update_one(
                {"product_id": product["parent_id"], "branch_id": branch_id},
                inv_update, upsert=True
            )
            await log_movement(
                product["parent_id"], branch_id, "sale", -parent_deduction, "", inv_number,
                rate * units_per_parent, user["id"], user.get("full_name", user["username"]),
                f"Sold as repack: {product['name']} x {qty}"
            )
            if release_mode == "partial":
                parent = await db.products.find_one({"id": product["parent_id"]}, {"_id": 0})
                reservations_to_create.append({
                    "product_id": product["parent_id"],
                    "product_name": parent["name"] if parent else "",
                    "sold_product_id": product["id"],
                    "sold_product_name": product["name"],
                    "sold_qty_ordered": qty,
                    "sold_qty_released": 0.0,
                    "sold_qty_remaining": qty,
                    "sold_unit": product.get("repack_unit", product.get("unit", "")),
                    "qty_reserved": parent_deduction,
                    "qty_released": 0.0,
                    "qty_remaining": parent_deduction,
                    "units_per_parent": units_per_parent,
                    "is_repack": True,
                })
        else:
            inv = await db.inventory.find_one(
                {"product_id": item["product_id"], "branch_id": branch_id}, {"_id": 0}
            )
            current_stock = float(inv["quantity"]) if inv else 0
            if current_stock < qty and not override_verifier:
                raise HTTPException(
                    status_code=400,
                    detail=f"Insufficient stock for {product['name']}: have {current_stock:.0f}, need {qty:.0f}"
                )
            inv_update = {"$inc": {"quantity": -qty}, "$set": {"updated_at": now_iso()}}
            if release_mode == "partial":
                inv_update["$inc"]["reserved_qty"] = qty
            await db.inventory.update_one(
                {"product_id": item["product_id"], "branch_id": branch_id},
                inv_update, upsert=True
            )
            await log_movement(
                item["product_id"], branch_id, "sale", -qty, "", inv_number,
                rate, user["id"], user.get("full_name", user["username"])
            )
            if release_mode == "partial":
                reservations_to_create.append({
                    "product_id": item["product_id"],
                    "product_name": product["name"],
                    "sold_product_id": item["product_id"],
                    "sold_product_name": product["name"],
                    "sold_qty_ordered": qty,
                    "sold_qty_released": 0.0,
                    "sold_qty_remaining": qty,
                    "sold_unit": product.get("unit", ""),
                    "qty_reserved": qty,
                    "qty_released": 0.0,
                    "qty_remaining": qty,
                    "units_per_parent": 1.0,
                    "is_repack": False,
                })
        
        sale_items.append({
            "product_id": item["product_id"],
            "product_name": product["name"],
            "sku": product.get("sku", ""),
            "description": item.get("description", ""),
            "quantity": qty,
            "rate": rate,
            "discount_type": disc_type,
            "discount_value": disc_val,
            "discount_amount": disc_amt,
            "total": line_total,
            "is_repack": product.get("is_repack", False),
            # Live branch-aware capital (parent-derived for repacks). This is the
            # historical snapshot stored on the sale line — never recomputed later.
            "cost_price": branch_cost,
        })
        subtotal += line_total
    
    # Calculate totals
    freight = float(data.get("freight", 0))
    overall_disc = float(data.get("overall_discount", 0))
    grand_total = round(subtotal + freight - overall_disc, 2)
    amount_paid = float(data.get("amount_paid", 0))
    balance = round(grand_total - amount_paid, 2)
    
    # Determine status
    if balance <= 0:
        status = "paid"
    elif amount_paid > 0:
        status = "partial"
    else:
        status = "open"
    
    # Get customer interest rate
    customer = await db.customers.find_one({"id": customer_id}, {"_id": 0}) if customer_id else None
    interest_rate = float(data.get("interest_rate", customer.get("interest_rate", 0) if customer else 0))
    
    # Compute due date
    terms_days = int(data.get("terms_days", 0))
    order_date = data.get("order_date", now_iso()[:10])
    if terms_days > 0:
        od = datetime.strptime(order_date, "%Y-%m-%d")
        due_date = (od + timedelta(days=terms_days)).strftime("%Y-%m-%d")
    else:
        due_date = order_date
    
    # Determine payment routing
    is_split = payment_type == "split"
    payment_method = data.get("payment_method", "Cash")
    is_digital = is_split or is_digital_payment(payment_method)

    # Split amounts
    cash_amount = float(data.get("cash_amount", 0)) if is_split else (
        amount_paid if not is_digital_payment(payment_method) else 0
    )
    digital_amount = float(data.get("digital_amount", 0)) if is_split else (
        amount_paid if is_digital_payment(payment_method) else 0
    )

    # Digital payment metadata
    digital_meta = {}
    if is_digital or is_split:
        digital_meta = {
            "digital_platform": data.get("digital_platform", payment_method if not is_split else "GCash"),
            "digital_ref_number": data.get("digital_ref_number", ""),
            "digital_sender": data.get("digital_sender", ""),
        }
        if is_split:
            digital_meta["cash_amount"] = cash_amount
            digital_meta["digital_amount"] = digital_amount

    # Determine fund_source
    fund_source = "split" if is_split else ("digital" if is_digital else data.get("fund_source", "cashier"))

    # Create invoice record
    invoice = {
        "id": data.get("id", new_id()),
        "invoice_number": inv_number,
        "prefix": prefix,
        "customer_id": customer_id,
        "customer_name": customer_name,
        "customer_contact": data.get("customer_contact", ""),
        "customer_phone": data.get("customer_phone", ""),
        "customer_address": data.get("customer_address", ""),
        "terms": data.get("terms", "COD"),
        "terms_days": terms_days,
        "customer_po": data.get("customer_po", ""),
        "sales_rep_id": data.get("sales_rep_id"),
        "sales_rep_name": data.get("sales_rep_name", ""),
        "branch_id": branch_id,
        "order_date": order_date,
        "invoice_date": data.get("invoice_date", order_date),
        "due_date": due_date,
        "items": sale_items,
        "subtotal": subtotal,
        "freight": freight,
        "overall_discount": overall_disc,
        "grand_total": grand_total,
        "amount_paid": amount_paid,
        "balance": max(0, balance),
        "interest_rate": interest_rate,
        "interest_accrued": 0,
        "penalties": 0,
        "last_interest_date": None,
        "sale_type": data.get("sale_type", "walk_in"),
        "payment_type": payment_type,
        "payment_method": payment_method,
        "fund_source": fund_source,
        **digital_meta,
        "status": status,
        "payments": [],
        "approved_by": data.get("approved_by"),
        "mode": data.get("mode", "quick"),
        "cashier_id": user["id"],
        "cashier_name": user.get("full_name", user["username"]),
        "idempotency_key": idem_key,
        "created_at": now_iso(),
        # Stock release tracking
        "release_mode": release_mode,
        "stock_release_status": "na" if release_mode == "full" else "not_released",
        "stock_releases": [],
    }

    # ── Pre-invoice signature attach (credit/partial) ──────────────────────
    # If the cashier captured a signature (or manager bypass) BEFORE invoice
    # creation, attach the signature data here so the printed receipt shows it
    # and the audit trail is complete from t=0.
    sig_data = data.get("signature") or {}
    if sig_data:
        invoice["signature_url"] = sig_data.get("url") or sig_data.get("signature_url") or None
        invoice["signature_signed_at"] = sig_data.get("signed_at") or None
        invoice["signature_verification_token"] = sig_data.get("verification_token") or None
        invoice["signature_bypass_method"] = sig_data.get("bypass_method") or None
        invoice["signature_session_id"] = sig_data.get("session_id") or None

    # Mark digital/split invoices as needing receipt upload
    if is_digital or is_split:
        invoice["receipt_status"] = "pending"

    # Record initial payment + route to correct wallet(s)
    if amount_paid > 0:
        if is_split:
            # Split: two payment entries — cash to cashier, digital to digital wallet
            if cash_amount > 0:
                invoice["payments"].append({
                    "id": new_id(), "amount": cash_amount, "date": order_date,
                    "method": "Cash", "fund_source": "cashier",
                    "applied_to_interest": 0, "applied_to_principal": cash_amount,
                    "recorded_by": user.get("full_name", user["username"]), "recorded_at": now_iso(),
                })
                await update_cashier_wallet(branch_id, cash_amount, f"Split sale cash portion {inv_number}")
            if digital_amount > 0:
                invoice["payments"].append({
                    "id": new_id(), "amount": digital_amount, "date": order_date,
                    "method": digital_meta.get("digital_platform", "Digital"),
                    "fund_source": "digital",
                    "digital_platform": digital_meta.get("digital_platform", ""),
                    "digital_ref_number": digital_meta.get("digital_ref_number", ""),
                    "digital_sender": digital_meta.get("digital_sender", ""),
                    "applied_to_interest": 0, "applied_to_principal": digital_amount,
                    "recorded_by": user.get("full_name", user["username"]), "recorded_at": now_iso(),
                })
                await update_digital_wallet(
                    branch_id, digital_amount,
                    reference=f"Split sale digital portion {inv_number}",
                    platform=digital_meta.get("digital_platform", ""),
                    ref_number=digital_meta.get("digital_ref_number", ""),
                    sender=digital_meta.get("digital_sender", ""),
                )
        else:
            invoice["payments"].append({
                "id": new_id(),
                "amount": amount_paid,
                "date": order_date,
                "method": payment_method,
                "fund_source": fund_source,
                "digital_platform": digital_meta.get("digital_platform", ""),
                "digital_ref_number": digital_meta.get("digital_ref_number", ""),
                "digital_sender": digital_meta.get("digital_sender", ""),
                "reference": digital_meta.get("digital_ref_number", ""),
                "applied_to_interest": 0,
                "applied_to_principal": amount_paid,
                "recorded_by": user.get("full_name", user["username"]),
                "recorded_at": now_iso(),
            })
            if is_digital:
                await update_digital_wallet(
                    branch_id, amount_paid,
                    reference=f"Invoice {inv_number}",
                    platform=digital_meta.get("digital_platform", payment_method),
                    sender=digital_meta.get("digital_sender", ""),
                    ref_number=digital_meta.get("digital_ref_number", ""),
                )
            elif fund_source == "cashier":
                await update_cashier_wallet(branch_id, amount_paid, f"Sale payment {inv_number}")
    
    # Tag late-encoded invoices so UI / audits / Z-report carryover can filter.
    _le_ctx = data.get("_late_encode_ctx")
    if _le_ctx:
        invoice["late_encoded"] = True
        invoice["late_encoded_at"] = now_iso()
        invoice["late_encode_reason"] = _le_ctx["reason"]
        invoice["late_encoded_by"] = user.get("id")
        invoice["late_encoded_by_name"] = user.get("full_name") or user.get("username")
        invoice["late_encode_verifier_id"] = _le_ctx["verifier"].get("verifier_id", "")
        invoice["late_encode_verifier_name"] = _le_ctx["verifier"].get("verifier_name", "")
        invoice["late_encode_days_back"] = _le_ctx["days_back"]
        # Also write a dedicated audit-trail row
        await db.late_encode_log.insert_one({
            "id": new_id(),
            "branch_id": branch_id,
            "invoice_id": invoice["id"],
            "invoice_number": invoice.get("invoice_number"),
            "original_date": order_date,
            "encoded_at": invoice["late_encoded_at"],
            "grand_total": invoice.get("grand_total"),
            "payment_type": invoice.get("payment_type"),
            "customer_id": invoice.get("customer_id"),
            "customer_name": invoice.get("customer_name"),
            "reason": _le_ctx["reason"],
            "encoded_by": user.get("id"),
            "encoded_by_name": user.get("full_name") or user.get("username"),
            "verifier_id": _le_ctx["verifier"].get("verifier_id", ""),
            "verifier_name": _le_ctx["verifier"].get("verifier_name", ""),
            "days_back": _le_ctx["days_back"],
        })

    # Race-safe insert: if a parallel retry already inserted (idempotency_key
    # unique index trips), return the previously-saved invoice instead of 500.
    try:
        await db.invoices.insert_one(invoice)
    except Exception as ins_err:
        from pymongo.errors import DuplicateKeyError
        if isinstance(ins_err, DuplicateKeyError) and idem_key:
            existing = await check_idempotency("invoices", idem_key)
            if existing:
                return existing
        raise
    invoice.pop("_id", None)

    # ── Link pre-commit signature session (Terminal credit/partial flow) ──────
    # Terminal/Web captures the signature BEFORE creating the invoice (legal sequence).
    # Now that the invoice exists, back-link the session to it.
    sig_session_id = data.get("signature_session_id") or sig_data.get("session_id")
    if sig_session_id:
        try:
            await db.signature_sessions.update_one(
                {"id": sig_session_id},
                {"$set": {
                    "linked_record_type": "invoice",
                    "linked_record_id": invoice["id"],
                    "credit_context.invoice_number": invoice["invoice_number"],
                }},
            )
        except Exception as link_err:
            import logging
            logging.getLogger("signatures").error(
                f"Failed to link signature_session {sig_session_id} -> invoice {invoice['id']}: {link_err}"
            )

    # ── Auto-generate doc code for every invoice (QR ready at print time) ─────
    from routes.doc_lookup import auto_generate_doc_code
    doc_code = await auto_generate_doc_code(
        "invoice", invoice["id"],
        org_id=user.get("org_id", user.get("organization_id", "")),
        created_by=user.get("id", ""),
    )
    invoice["doc_code"] = doc_code

    # ── Partial release: create sale_reservations ─────────────────────────────
    if release_mode == "partial" and reservations_to_create:
        expires_at = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        for r in reservations_to_create:
            await db.sale_reservations.insert_one({
                "id": new_id(),
                "invoice_id": invoice["id"],
                "invoice_number": invoice["invoice_number"],
                "branch_id": branch_id,
                **r,
                "created_at": now_iso(),
                "expires_at": expires_at,
            })
    
    # Update customer balance for credit portion
    if customer_id and balance > 0:
        await db.customers.update_one(
            {"id": customer_id},
            {"$inc": {"balance": balance}}
        )
    
    # Log to sequential sales log — reuse products_map (no extra DB calls)
    # Use the same order_date the invoice uses so sales_log matches
    log_date = data.get("order_date", now_iso()[:10])
    for item in sale_items:
        product = products_map.get(item["product_id"])
        item["category"] = product.get("category", "General") if product else "General"

    # For split sales, pass split metadata so daily log can decompose into cash + digital
    split_meta = None
    if is_split:
        split_meta = {
            "cash_amount": cash_amount,
            "digital_amount": digital_amount,
            "digital_platform": digital_meta.get("digital_platform", "GCash"),
            "grand_total": grand_total,
        }

    # For partial sales, pass partial metadata so daily log can decompose into cash + credit
    partial_meta = None
    if payment_type == "partial":
        partial_meta = {
            "cash_amount": amount_paid,
            "credit_amount": balance,
            "grand_total": grand_total,
        }

    await log_sale_items(
        branch_id, log_date, sale_items, inv_number,
        customer_name, "split" if is_split else data.get("payment_method", "Cash"),
        user.get("full_name", user["username"]),
        split_meta=split_meta,
        partial_meta=partial_meta,
    )

    # SMS hook: notify customer on credit sale
    if balance > 0 and customer_id and invoice.get("sale_type") not in ("interest_charge", "penalty_charge"):
        try:
            from routes.sms_hooks import on_credit_sale_created
            await on_credit_sale_created(invoice)
        except Exception as sms_err:
            import logging
            logging.getLogger("sms").error(f"SMS hook error in unified-sale: {sms_err}", exc_info=True)

    # ── Discount / Price Override Audit Log ───────────────────────────────────
    overall_disc = float(data.get("overall_discount", 0))
    if overall_disc > 0:
        discount_audit_entries.append({
            "product_id": None, "product_name": "(Overall Discount)",
            "original_price": 0, "sold_price": 0,
            "discount_type": "amount", "discount_value": overall_disc,
            "discount_amount": overall_disc, "quantity": 1,
            "net_per_unit": 0, "capital": 0,
            "type": "overall_discount",
        })
    if discount_audit_entries:
        cashier_id = user["id"]
        cashier_name = user.get("full_name", user.get("username", ""))
        customer_id = data.get("customer_id")
        total_discount = sum(e["discount_amount"] for e in discount_audit_entries)
        total_price_diff = sum(abs(e["original_price"] - e["sold_price"]) * e["quantity"]
                               for e in discount_audit_entries if e["type"] != "overall_discount")
        await db.discount_audit_log.insert_one({
            "id": new_id(),
            "invoice_id": invoice["id"],
            "invoice_number": inv_number,
            "branch_id": branch_id,
            "date": log_date,
            "customer_id": customer_id,
            "customer_name": customer_name,
            "cashier_id": cashier_id,
            "cashier_name": cashier_name,
            "items": discount_audit_entries,
            "total_discount": round(total_discount, 2),
            "total_price_override_diff": round(total_price_diff, 2),
            "grand_total": grand_total,
            "created_at": now_iso(),
        })

        # ── Fire discount/price-override notification to admin ──────────────
        from routes.notifications import create_notification
        admins = await db.users.find({"role": "admin", "active": True}, {"_id": 0, "id": 1}).to_list(50)
        admin_ids = [a["id"] for a in admins]
        if admin_ids:
            # Separate discount vs below-cost types
            has_below_cost = any(
                e.get("net_per_unit", e.get("sold_price", 0)) < e.get("capital", 0)
                for e in discount_audit_entries
            )
            notif_type = "below_cost_sale" if has_below_cost else "discount_given"
            items_summary = "; ".join(
                f"{e['product_name']} −{e['discount_value']}"
                f"{'%' if e.get('discount_type') == 'percent' else '₱'}"
                for e in discount_audit_entries[:3] if e.get("discount_amount", 0) > 0
            )
            if len(discount_audit_entries) > 3:
                items_summary += f" +{len(discount_audit_entries)-3} more"
            title = "Below-Cost Sale Detected" if has_below_cost else f"Discount Applied — {inv_number}"
            # Count how many discounts this cashier gave this week (repeat-offender check)
            week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
            repeat_count = await db.discount_audit_log.count_documents({
                "cashier_id": cashier_id,
                "date": {"$gte": week_ago},
            })
            repeat_note = f" ({repeat_count} discount{'s' if repeat_count != 1 else ''} this week by {cashier_name})"
            await create_notification(
                type_key=notif_type,
                title=title,
                message=f"{cashier_name} gave discount on {inv_number} (customer: {customer_name}). "
                        f"Total discounted: ₱{total_discount:.2f}.{repeat_note} Items: {items_summary}",
                target_user_ids=admin_ids,
                branch_id=branch_id,
                branch_name=data.get("branch_name", ""),
                metadata={
                    "invoice_id": invoice["id"],
                    "invoice_number": inv_number,
                    "cashier_id": cashier_id,
                    "cashier_name": cashier_name,
                    "customer_name": customer_name,
                    "total_discount": round(total_discount, 2),
                    "total_price_override_diff": round(total_price_diff, 2),
                    "grand_total": grand_total,
                    "items": discount_audit_entries,
                    "cashier_discounts_this_week": repeat_count,
                    "has_below_cost": has_below_cost,
                },
                organization_id=user.get("organization_id"),
            )

    # ── Price Match (Permanent Branch Price Change) — persist & log ──────────
    if valid_price_changes:
        from utils.helpers import mark_price_reviewed
        pm_verifier = data.get("_price_match_verifier") or {}
        approver_name = pm_verifier.get("verifier_name") or pm_verifier.get("full_name") or pm_verifier.get("username") or ""
        approver_method = pm_verifier.get("method", "")
        approver_id = pm_verifier.get("verifier_id") or pm_verifier.get("id") or ""
        for pc in valid_price_changes:
            # Upsert branch_prices for this product/branch — ONLY this scheme
            existing = await db.branch_prices.find_one(
                {"product_id": pc["product_id"], "branch_id": branch_id}, {"_id": 0}
            )
            merged_prices = (existing or {}).get("prices", {}) or {}
            merged_prices[pc["scheme"]] = pc["new_price"]
            await db.branch_prices.update_one(
                {"product_id": pc["product_id"], "branch_id": branch_id},
                {
                    "$set": {
                        "prices": merged_prices,
                        "updated_at": now_iso(),
                        "updated_by_id": user["id"],
                        "updated_by_name": user.get("full_name", user.get("username", "")),
                        "source": "pos_price_match",
                    },
                    "$setOnInsert": {
                        "id": new_id(),
                        "product_id": pc["product_id"],
                        "branch_id": branch_id,
                        "created_at": now_iso(),
                    },
                },
                upsert=True,
            )
            # Clear "Global Price" badge — an explicit Price Match counts as
            # a reviewed price for this branch/product.
            await mark_price_reviewed(pc["product_id"], branch_id, source="pos_price_match")
            # Audit log entry
            await db.price_change_log.insert_one({
                "id": new_id(),
                "product_id": pc["product_id"],
                "product_name": pc["product_name"],
                "sku": pc.get("sku", ""),
                "branch_id": branch_id,
                "branch_name": data.get("branch_name", ""),
                "scheme": pc["scheme"],
                "old_price": pc["old_price"],            # server-trusted
                "client_old_price_hint": pc.get("client_old_price_hint", 0),  # for forensics
                "new_price": pc["new_price"],
                "delta": round(pc["new_price"] - pc["old_price"], 2),
                "delta_pct": round((pc["new_price"] - pc["old_price"]) / pc["old_price"] * 100, 2)
                              if pc["old_price"] > 0 else 0,
                "reason": pc["reason"],
                "reason_detail": pc.get("reason_detail", ""),
                "invoice_id": invoice["id"],
                "invoice_number": inv_number,
                "customer_id": data.get("customer_id"),
                "customer_name": customer_name,
                "cashier_id": user["id"],
                "cashier_name": user.get("full_name", user.get("username", "")),
                "approver_id": approver_id,
                "approver_name": approver_name,
                "approver_method": approver_method,
                "date": log_date,
                "created_at": now_iso(),
                "organization_id": user.get("organization_id"),
            })

        # ── Notify admins of price match events ─────────────────────────────
        try:
            from routes.notifications import create_notification
            admins_pm = await db.users.find({"role": "admin", "active": True}, {"_id": 0, "id": 1}).to_list(50)
            admin_ids_pm = [a["id"] for a in admins_pm]
            if admin_ids_pm:
                items_summary = "; ".join(
                    f"{pc['product_name']}: ₱{pc['old_price']:.2f}→₱{pc['new_price']:.2f}"
                    for pc in valid_price_changes[:3]
                )
                if len(valid_price_changes) > 3:
                    items_summary += f" +{len(valid_price_changes)-3} more"
                await create_notification(
                    type_key="discount_given",
                    title=f"Price Match Approved — {inv_number}",
                    message=(
                        f"{user.get('full_name', user.get('username', ''))} matched price on "
                        f"{len(valid_price_changes)} product(s) — approved by {approver_name}. {items_summary}"
                    ),
                    target_user_ids=admin_ids_pm,
                    branch_id=branch_id,
                    branch_name=data.get("branch_name", ""),
                    metadata={
                        "invoice_id": invoice["id"],
                        "invoice_number": inv_number,
                        "approver_name": approver_name,
                        "items": valid_price_changes,
                    },
                    organization_id=user.get("organization_id"),
                )
        except Exception as nerr:
            import logging
            logging.getLogger("notifications").error(f"price-match notification failed: {nerr}", exc_info=True)

    # ── Auto-create incident tickets for negative-stock overrides ─────────────
    if override_verifier and insufficient_items:
        for bad in insufficient_items:
            ticket_number = await generate_next_number("IT", branch_id)
            await db.incident_tickets.insert_one({
                "id": new_id(),
                "ticket_number": ticket_number,
                "ticket_type": "negative_stock_override",
                "status": "open",
                "branch_id": branch_id,
                "product_id": bad["product_id"],
                "product_name": bad["product_name"],
                "qty_before_sale": bad["system_qty"],
                "qty_sold": bad["needed_qty"],
                "qty_after_sale": round(bad["system_qty"] - bad["needed_qty"], 4),
                "invoice_id": invoice["id"],
                "invoice_number": inv_number,
                "override_by_id": override_verifier["verifier_id"],
                "override_by_name": override_verifier["verifier_name"],
                "override_method": override_verifier["method"],
                "cashier_id": user["id"],
                "cashier_name": user.get("full_name", user["username"]),
                "timeline": [{
                    "action": "created",
                    "by_id": user["id"],
                    "by_name": user.get("full_name", user["username"]),
                    "detail": f"Auto-generated: negative stock override on {inv_number}. "
                              f"System had {bad['system_qty']}, sold {bad['needed_qty']}. "
                              f"Approved by {override_verifier['verifier_name']} ({override_verifier['method']}). "
                              f"Investigate: unencoded PO, wrong item, count error, or shrinkage.",
                    "at": now_iso(),
                }],
                "created_at": now_iso(),
                "updated_at": now_iso(),
            })

        # ── Notify admin of negative stock override ────────────────────────
        from routes.notifications import create_notification
        admins = await db.users.find({"role": "admin", "active": True}, {"_id": 0, "id": 1}).to_list(50)
        admin_ids = [a["id"] for a in admins]
        if admin_ids:
            items_list = ", ".join(f"{b['product_name']} ({b['system_qty']:g}→{round(b['system_qty']-b['needed_qty'],4):g})" for b in insufficient_items[:3])
            await create_notification(
                type_key="negative_stock_override",
                title=f"Negative Stock Override — {inv_number}",
                message=f"{override_verifier['verifier_name']} approved selling below-zero stock on {inv_number}. "
                        f"Cashier: {user.get('full_name', user['username'])}. "
                        f"Items: {items_list}. Investigation ticket(s) created.",
                target_user_ids=admin_ids,
                branch_id=branch_id,
                metadata={
                    "invoice_id": invoice["id"],
                    "invoice_number": inv_number,
                    "approved_by": override_verifier["verifier_name"],
                    "cashier_name": user.get("full_name", user["username"]),
                    "items": [{"product_name": b["product_name"], "qty_before": b["system_qty"], "qty_sold": b["needed_qty"]} for b in insufficient_items],
                },
                organization_id=user.get("organization_id"),
            )

    if jit_persisted:
        invoice["jit_persisted_count"] = len(jit_persisted)

    return invoice



@router.get("/pending-receipt-uploads")
async def get_pending_receipt_uploads(user=Depends(get_current_user)):
    """Return invoices with receipt_status='pending' for the current user's branch."""
    branch_id = user.get("branch_id")
    query = {"receipt_status": "pending", "voided": {"$ne": True}}
    if branch_id:
        query["branch_id"] = branch_id
    invoices = await db.invoices.find(
        query,
        {"_id": 0, "id": 1, "invoice_number": 1, "grand_total": 1, "fund_source": 1, "digital_platform": 1}
    ).sort("created_at", -1).to_list(10)
    return invoices



@router.get("/sales/late-encoded-since-close")
async def late_encoded_since_close(
    branch_id: str,
    user=Depends(get_current_user),
):
    """
    List every late-encoded invoice that has been created since the last close
    for this branch. Powers the Close Wizard's 'Late-Encoded Credits' carryover
    section — these invoices are dated to prior closed days but appear on the
    CURRENT open Z-report for transparency.
    """
    if not branch_id:
        raise HTTPException(status_code=400, detail="branch_id is required")

    last_close = await db.daily_closings.find_one(
        {"branch_id": branch_id, "status": "closed"},
        {"_id": 0, "closed_at": 1, "date": 1},
        sort=[("closed_at", -1)],
    )
    cutoff = (last_close or {}).get("closed_at")

    q = {"branch_id": branch_id, "late_encoded": True, "status": {"$ne": "voided"}}
    if cutoff:
        q["late_encoded_at"] = {"$gt": cutoff}

    rows = await db.invoices.find(
        q,
        {
            "_id": 0, "id": 1, "invoice_number": 1, "order_date": 1,
            "late_encoded_at": 1, "late_encode_reason": 1,
            "late_encoded_by_name": 1, "late_encode_verifier_name": 1,
            "late_encode_days_back": 1, "customer_name": 1, "payment_type": 1,
            "grand_total": 1, "balance": 1,
        },
    ).sort("late_encoded_at", 1).to_list(200)

    return {
        "last_close_at": cutoff,
        "entries": rows,
        "count": len(rows),
        "total_amount": round(sum(r.get("grand_total", 0) or 0 for r in rows), 2),
    }

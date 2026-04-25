"""
Invoice and Sales Order routes: CRUD, payments, interest/penalty, editing.
Supports multi-branch data isolation.
"""
from fastapi import APIRouter, Depends, HTTPException
from typing import Optional
from datetime import datetime, timezone, timedelta
from config import db
from utils import (
    get_current_user, check_perm, now_iso, new_id,
    log_movement, log_sale_items, update_cashier_wallet,
    update_digital_wallet, is_digital_payment,
    get_branch_filter, apply_branch_filter, ensure_branch_access,
    generate_next_number, check_idempotency,
)

router = APIRouter(tags=["Invoices"])


# ==================== INVOICE CRUD ====================
@router.get("/invoices")
async def list_invoices(
    user=Depends(get_current_user),
    status: Optional[str] = None,
    customer_id: Optional[str] = None,
    branch_id: Optional[str] = None,
    search: Optional[str] = None,
    sort_by: Optional[str] = "created_at",
    sort_dir: Optional[str] = "desc",
    include_voided: bool = False,
    skip: int = 0,
    limit: int = 50
):
    """List invoices with optional filters, search, and sorting. Respects branch isolation."""
    query = {}
    if not include_voided:
        query["status"] = {"$ne": "voided"}
    
    # Apply branch filter for data isolation
    branch_filter = await get_branch_filter(user, branch_id)
    query = apply_branch_filter(query, branch_filter)
    
    if status:
        if status == "voided":
            query["status"] = "voided"
        else:
            query["status"] = status
    if customer_id:
        query["customer_id"] = customer_id
    
    # Search by invoice number or customer name
    if search:
        search_regex = {"$regex": search, "$options": "i"}
        query["$or"] = [
            {"invoice_number": search_regex},
            {"customer_name": search_regex},
        ]
    
    # Sorting
    sort_field_map = {
        "created_at": "created_at",
        "date": "order_date",
        "customer": "customer_name",
        "amount": "grand_total",
        "number": "invoice_number",
        "status": "status",
    }
    sort_field = sort_field_map.get(sort_by, "created_at")
    sort_direction = -1 if sort_dir == "desc" else 1
    
    total = await db.invoices.count_documents(query)
    items = await db.invoices.find(query, {"_id": 0}).sort(sort_field, sort_direction).skip(skip).limit(limit).to_list(limit)

    # Annotate each invoice with whether a customer signature exists (signed or bypassed)
    if items:
        ids = [it.get("id") for it in items if it.get("id")]
        if ids:
            sig_rows = await db.signature_sessions.find(
                {
                    "linked_record_id": {"$in": ids},
                    "linked_record_type": {"$in": ["sale", "invoice"]},
                    "status": {"$in": ["signed", "bypassed"]},
                },
                {"_id": 0, "linked_record_id": 1, "status": 1},
            ).to_list(length=len(ids) * 2)
            sig_status_by_id = {}
            for sr in sig_rows:
                rid = sr.get("linked_record_id")
                if not rid:
                    continue
                # Prefer 'signed' over 'bypassed' if both exist for the same id
                if sig_status_by_id.get(rid) != "signed":
                    sig_status_by_id[rid] = sr.get("status")
            for it in items:
                st = sig_status_by_id.get(it.get("id"))
                it["signature_status"] = st  # 'signed' | 'bypassed' | None
                it["has_signature"] = bool(st)

    return {"invoices": items, "total": total}


@router.get("/invoices/check-date-closed")
async def check_date_closed(
    date: str,
    branch_id: str,
    user=Depends(get_current_user),
):
    """Check if a specific date has been closed for a branch."""
    doc = await db.daily_closings.find_one(
        {"branch_id": branch_id, "date": date, "status": "closed"}, {"_id": 0, "date": 1}
    )
    return {"closed": bool(doc), "date": date}


@router.post("/invoices")
async def create_invoice(data: dict, user=Depends(get_current_user)):
    """Create a new invoice/sales order."""
    check_perm(user, "pos", "sell")
    
    # Idempotency check
    idem_key = data.get("idempotency_key")
    if idem_key:
        existing = await check_idempotency("invoices", idem_key)
        if existing:
            return existing
    
    # Get prefix settings
    settings = await db.settings.find_one({"key": "invoice_prefixes"}, {"_id": 0})
    prefix = data.get("prefix", settings.get("value", {}).get("sales_invoice", "SI") if settings else "SI")
    
    # Generate invoice number (atomic, branch-specific)
    branch_id = data.get("branch_id", "")
    inv_number = await generate_next_number(prefix, branch_id)
    
    # Compute due date
    terms_days = int(data.get("terms_days", 0))
    order_date = data.get("order_date", now_iso()[:10])
    if terms_days > 0:
        od = datetime.strptime(order_date, "%Y-%m-%d")
        due_date = (od + timedelta(days=terms_days)).strftime("%Y-%m-%d")
    else:
        due_date = order_date
    
    # Compute line items
    items = []
    subtotal = 0
    for item in data.get("items", []):
        qty = float(item.get("quantity", 0))
        rate = float(item.get("rate", 0))
        
        # HARD RULE: Never sell below capital
        if item.get("product_id"):
            prod_check = await db.products.find_one(
                {"id": item["product_id"]},
                {"_id": 0, "cost_price": 1, "name": 1}
            )
            if prod_check and rate < prod_check.get("cost_price", 0) and rate > 0:
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot sell '{prod_check.get('name', '')}' at ₱{rate:.2f} — below capital ₱{prod_check['cost_price']:.2f}"
                )
        
        disc_type = item.get("discount_type", "amount")
        disc_val = float(item.get("discount_value", 0))
        disc_amt = disc_val if disc_type == "amount" else round(qty * rate * disc_val / 100, 2)
        line_total = round(qty * rate - disc_amt, 2)
        
        items.append({
            "product_id": item.get("product_id", ""),
            "product_name": item.get("product_name", ""),
            "description": item.get("description", ""),
            "quantity": qty,
            "rate": rate,
            "discount_type": disc_type,
            "discount_value": disc_val,
            "discount_amount": disc_amt,
            "total": line_total,
            "is_repack": item.get("is_repack", False),
        })
        subtotal += line_total
    
    freight = float(data.get("freight", 0))
    overall_disc = float(data.get("overall_discount", 0))
    grand_total = round(subtotal + freight - overall_disc, 2)
    amount_paid = float(data.get("amount_paid", 0))
    balance = round(grand_total - amount_paid, 2)
    
    # Get customer interest rate
    customer = await db.customers.find_one({"id": data.get("customer_id")}, {"_id": 0}) if data.get("customer_id") else None
    interest_rate = float(data.get("interest_rate", customer.get("interest_rate", 0) if customer else 0))
    
    status = "paid" if balance <= 0 else ("partial" if amount_paid > 0 else "open")
    sale_type = data.get("sale_type", "walk_in")
    payment_method = data.get("payment_method", "Cash")
    is_split = data.get("payment_type") == "split"
    digital = is_split or is_digital_payment(payment_method)

    # Derive payment_type consistently (matches unified-sale behavior)
    payment_type = data.get("payment_type")
    if not payment_type:
        if is_split:
            payment_type = "split"
        elif digital:
            payment_type = "digital"
        elif balance <= 0:
            payment_type = "cash"
        elif amount_paid > 0:
            payment_type = "partial"
        else:
            payment_type = "credit"

    # Split payment: part cash + part digital
    cash_amount = float(data.get("cash_amount", 0)) if is_split else (amount_paid if not is_digital_payment(payment_method) else 0)
    digital_amount = float(data.get("digital_amount", 0)) if is_split else (amount_paid if is_digital_payment(payment_method) else 0)

    # Digital payment metadata (reference #, platform, sender name)
    digital_meta = {}
    if digital or is_split:
        digital_meta = {
            "digital_platform": data.get("digital_platform", payment_method if not is_split else "GCash"),
            "digital_ref_number": data.get("digital_ref_number", ""),
            "digital_sender": data.get("digital_sender", ""),
        }
        if is_split:
            digital_meta["cash_amount"] = cash_amount
            digital_meta["digital_amount"] = digital_amount

    invoice = {
        "id": new_id(),
        "invoice_number": inv_number,
        "prefix": prefix,
        "customer_id": data.get("customer_id"),
        "customer_name": data.get("customer_name", "Walk-in"),
        "customer_contact": data.get("customer_contact", ""),
        "customer_phone": data.get("customer_phone", ""),
        "customer_address": data.get("customer_address", ""),
        "terms": data.get("terms", "COD"),
        "terms_days": terms_days,
        "customer_po": data.get("customer_po", ""),
        "sales_rep_id": data.get("sales_rep_id"),
        "sales_rep_name": data.get("sales_rep_name", ""),
        "branch_id": data.get("branch_id", ""),
        "order_date": order_date,
        "invoice_date": data.get("invoice_date", order_date),
        "due_date": due_date,
        "items": items,
        "subtotal": subtotal,
        "freight": freight,
        "overall_discount": overall_disc,
        "grand_total": grand_total,
        "amount_paid": amount_paid,
        "balance": balance,
        "interest_rate": interest_rate,
        "interest_accrued": 0,
        "penalties": 0,
        "last_interest_date": None,
        "sale_type": sale_type,
        "payment_type": payment_type,
        "payment_method": payment_method,
        "fund_source": "split" if is_split else ("digital" if digital else data.get("fund_source", "cashier")),
        **digital_meta,
        "status": status,
        "payments": [],
        "cashier_id": user["id"],
        "cashier_name": user.get("full_name", user["username"]),
        "idempotency_key": idem_key,
        "created_at": now_iso(),
    }
    
    # Handle inventory deduction
    branch_id = data.get("branch_id", "")
    if sale_type != "delivery":
        for item in items:
            if item["product_id"]:
                product = await db.products.find_one({"id": item["product_id"]}, {"_id": 0})
                
                # For repacks: only deduct from parent
                if product and product.get("is_repack") and product.get("parent_id"):
                    units_per_parent = product.get("units_per_parent", 1)
                    parent_deduction = item["quantity"] / units_per_parent
                    await db.inventory.update_one(
                        {"product_id": product["parent_id"], "branch_id": branch_id},
                        {"$inc": {"quantity": -parent_deduction}, "$set": {"updated_at": now_iso()}},
                        upsert=True
                    )
                    await log_movement(
                        product["parent_id"], branch_id, "sale", -parent_deduction,
                        invoice["id"], inv_number, item["rate"] * units_per_parent,
                        user["id"], user.get("full_name", user["username"]),
                        f"Sold as repack: {product['name']} x {item['quantity']}"
                    )
                else:
                    # Regular product: deduct from its own inventory
                    await db.inventory.update_one(
                        {"product_id": item["product_id"], "branch_id": branch_id},
                        {"$inc": {"quantity": -item["quantity"]}, "$set": {"updated_at": now_iso()}},
                        upsert=True
                    )
                    await log_movement(
                        item["product_id"], branch_id, "sale", -item["quantity"],
                        invoice["id"], inv_number, item["rate"],
                        user["id"], user.get("full_name", user["username"])
                    )
    else:
        invoice["status"] = "reserved" if balance > 0 else "paid"
    
    # Record initial payment + route to correct wallet(s)
    if amount_paid > 0:
        if is_split:
            # Split: record two payment entries
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
                "id": new_id(), "amount": amount_paid, "date": order_date,
                "method": payment_method,
                "fund_source": "digital" if digital else data.get("fund_source", "cashier"),
                "digital_platform": digital_meta.get("digital_platform", ""),
                "digital_ref_number": digital_meta.get("digital_ref_number", ""),
                "digital_sender": digital_meta.get("digital_sender", ""),
                "reference": digital_meta.get("digital_ref_number", ""),
                "applied_to_interest": 0, "applied_to_principal": amount_paid,
                "recorded_by": user.get("full_name", user["username"]), "recorded_at": now_iso(),
            })
            if digital:
                await update_digital_wallet(
                    branch_id, amount_paid, reference=f"Invoice {inv_number}",
                    platform=digital_meta.get("digital_platform", payment_method),
                    sender=digital_meta.get("digital_sender", ""),
                    ref_number=digital_meta.get("digital_ref_number", ""),
                )
            else:
                fund_source = data.get("fund_source", "cashier")
                if fund_source == "cashier":
                    await update_cashier_wallet(branch_id, amount_paid, f"Invoice payment {inv_number}")
    
    await db.invoices.insert_one(invoice)
    del invoice["_id"]
    
    # Log to sequential sales log — use order_date for consistency with invoice
    log_date = data.get("order_date", now_iso()[:10])
    for item in items:
        if item["product_id"]:
            prod = await db.products.find_one({"id": item["product_id"]}, {"_id": 0, "category": 1})
            item["category"] = prod.get("category", "General") if prod else "General"
    
    # Build metadata for split/partial decomposition in daily log
    split_meta_arg = None
    partial_meta_arg = None
    if is_split:
        split_meta_arg = {
            "cash_amount": cash_amount,
            "digital_amount": digital_amount,
            "digital_platform": digital_meta.get("digital_platform", ""),
            "grand_total": grand_total,
        }
    elif payment_type == "partial":
        partial_meta_arg = {
            "cash_amount": amount_paid,
            "credit_amount": balance,
            "grand_total": grand_total,
        }

    await log_sale_items(
        branch_id, log_date, items, inv_number,
        data.get("customer_name", "Walk-in"),
        data.get("payment_method", "Cash"),
        user.get("full_name", user["username"]),
        split_meta=split_meta_arg,
        partial_meta=partial_meta_arg,
    )
    
    # SMS hook: notify customer on credit sale
    if balance > 0 and data.get("customer_id") and sale_type not in ("interest_charge", "penalty_charge"):
        from routes.sms_hooks import on_credit_sale_created
        await on_credit_sale_created(invoice)
    
    return invoice


@router.get("/invoices/{inv_id}")
async def get_invoice(inv_id: str, user=Depends(get_current_user)):
    """Get invoice by ID with edit history."""
    inv = await db.invoices.find_one({"id": inv_id}, {"_id": 0})
    if not inv:
        inv = await db.sales.find_one({"id": inv_id}, {"_id": 0})
    if not inv:
        inv = await db.purchase_orders.find_one({"id": inv_id}, {"_id": 0})
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    
    # Include edit history and count
    edit_history = await db.invoice_edits.find(
        {"invoice_id": inv_id}, {"_id": 0}
    ).sort("edited_at", -1).to_list(100)
    inv["edit_history"] = edit_history
    inv["edit_count"] = len(edit_history)

    # Annotate signature status (matches both legacy 'sale' and current 'invoice' types)
    sig = await db.signature_sessions.find_one(
        {
            "linked_record_id": inv_id,
            "linked_record_type": {"$in": ["sale", "invoice"]},
            "status": {"$in": ["signed", "bypassed"]},
        },
        {"_id": 0, "status": 1},
        sort=[("status", -1)],  # 'signed' sorts after 'bypassed' alphabetically — prefer signed
    )
    inv["signature_status"] = sig.get("status") if sig else None
    inv["has_signature"] = bool(sig)

    return inv


@router.get("/invoices/by-number/{invoice_number}")
async def get_invoice_by_number(invoice_number: str, user=Depends(get_current_user)):
    """Get invoice by invoice number (searches all collections)."""
    # Search in invoices
    invoice = await db.invoices.find_one({"invoice_number": invoice_number}, {"_id": 0})
    if invoice:
        invoice["_collection"] = "invoices"
        return invoice
    
    # Search in sales
    sale = await db.sales.find_one({"sale_number": invoice_number}, {"_id": 0})
    if sale:
        sale["_collection"] = "sales"
        return sale
    
    # Search in purchase orders
    po = await db.purchase_orders.find_one({"po_number": invoice_number}, {"_id": 0})
    if po:
        po["_collection"] = "purchase_orders"
        return po
    
    raise HTTPException(status_code=404, detail="Invoice not found")


# ==================== INVOICE PAYMENTS ====================
@router.post("/invoices/{inv_id}/payment")
async def record_invoice_payment(inv_id: str, data: dict, user=Depends(get_current_user)):
    """Record a payment on an invoice."""
    check_perm(user, "accounting", "create")
    
    inv = await db.invoices.find_one({"id": inv_id}, {"_id": 0})
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    
    amount = float(data["amount"])
    fund_source = data.get("fund_source", "cashier")
    branch_id = inv.get("branch_id", "")
    
    # Interest & penalties first, then principal
    interest_owed = inv.get("interest_accrued", 0) + inv.get("penalties", 0)
    applied_interest = min(amount, interest_owed)
    applied_principal = amount - applied_interest
    new_interest = max(0, round(inv.get("interest_accrued", 0) - applied_interest, 2))
    new_balance = round(inv["balance"] - amount, 2)
    new_paid = round(inv["amount_paid"] + amount, 2)
    new_status = "paid" if new_balance <= 0 else "partial"
    
    payment = {
        "id": new_id(),
        "amount": amount,
        "date": data.get("date", now_iso()[:10]),
        "method": data.get("method", "Cash"),
        "fund_source": fund_source,
        "reference": data.get("reference", ""),
        "applied_to_interest": applied_interest,
        "applied_to_principal": applied_principal,
        "recorded_by": user.get("full_name", user["username"]),
        "recorded_at": now_iso(),
    }
    
    await db.invoices.update_one({"id": inv_id}, {
        "$set": {
            "balance": max(0, new_balance),
            "amount_paid": new_paid,
            "interest_accrued": new_interest,
            "status": new_status
        },
        "$push": {"payments": payment}
    })
    
    # Add to wallet
    wallet = await db.fund_wallets.find_one({"branch_id": branch_id, "type": fund_source}, {"_id": 0})
    if wallet:
        if fund_source == "safe":
            await db.safe_lots.insert_one({
                "id": new_id(),
                "branch_id": branch_id,
                "wallet_id": wallet["id"],
                "date_received": data.get("date", now_iso()[:10]),
                "original_amount": amount,
                "remaining_amount": amount,
                "source_reference": f"Payment from {inv.get('customer_name', '')} - {inv['invoice_number']}",
                "created_by": user["id"],
                "created_at": now_iso()
            })
        else:
            await db.fund_wallets.update_one({"id": wallet["id"]}, {"$inc": {"balance": amount}})
    
    # Update customer balance
    if inv.get("customer_id"):
        await db.customers.update_one({"id": inv["customer_id"]}, {"$inc": {"balance": -amount}})
    
    return {
        "message": "Payment recorded",
        "new_balance": max(0, new_balance),
        "status": new_status,
        "payment": payment
    }


@router.post("/invoices/{inv_id}/compute-interest")
async def compute_interest(inv_id: str, user=Depends(get_current_user)):
    """Compute interest on an overdue invoice."""
    inv = await db.invoices.find_one({"id": inv_id}, {"_id": 0})
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    
    if inv["balance"] <= 0 or inv["interest_rate"] <= 0:
        return {"interest": 0, "message": "No interest applicable"}
    
    due = datetime.strptime(inv["due_date"], "%Y-%m-%d")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    
    if now <= due:
        return {"interest": 0, "message": "Not yet overdue"}
    
    last_date = datetime.strptime(inv["last_interest_date"], "%Y-%m-%d") if inv.get("last_interest_date") else due
    days_overdue = (now - last_date).days
    
    if days_overdue <= 0:
        return {"interest": 0, "message": "Already computed today"}
    
    monthly_rate = inv["interest_rate"] / 100
    daily_rate = monthly_rate / 30
    principal_balance = inv["balance"] - inv.get("interest_accrued", 0) - inv.get("penalties", 0)
    new_interest = round(max(0, principal_balance) * daily_rate * days_overdue, 2)
    total_interest = round(inv.get("interest_accrued", 0) + new_interest, 2)
    new_balance = round(inv["balance"] + new_interest, 2)
    
    await db.invoices.update_one({"id": inv_id}, {"$set": {
        "interest_accrued": total_interest,
        "balance": new_balance,
        "last_interest_date": now.strftime("%Y-%m-%d"),
        "status": "overdue"
    }})
    
    return {
        "interest_added": new_interest,
        "total_interest": total_interest,
        "new_balance": new_balance,
        "days": days_overdue
    }


# ==================== INVOICE EDITING ====================
@router.put("/invoices/{invoice_id}/edit")
async def edit_invoice(invoice_id: str, data: dict, user=Depends(get_current_user)):
    """Edit an invoice with reason and optional proof. Handles inventory adjustments."""
    check_perm(user, "pos", "edit")

    # PIN enforcement for invoice edit
    pin = data.get("pin", "")
    branch_id_for_pin = data.get("branch_id") or ""
    if pin:
        from routes.verify import verify_pin_for_action
        verifier = await verify_pin_for_action(pin, "invoice_edit", branch_id=branch_id_for_pin)
        if not verifier:
            raise HTTPException(status_code=403, detail="Invalid PIN")

    reason = data.get("reason", "").strip()
    if not reason:
        raise HTTPException(status_code=400, detail="Edit reason is required")
    
    # Find the invoice
    collection_name = data.get("_collection", "invoices")
    if collection_name == "invoices":
        invoice = await db.invoices.find_one({"id": invoice_id}, {"_id": 0})
        collection = db.invoices
        id_field = "invoice_number"
    elif collection_name == "sales":
        invoice = await db.sales.find_one({"id": invoice_id}, {"_id": 0})
        collection = db.sales
        id_field = "sale_number"
    elif collection_name == "purchase_orders":
        invoice = await db.purchase_orders.find_one({"id": invoice_id}, {"_id": 0})
        collection = db.purchase_orders
        id_field = "po_number"
    else:
        raise HTTPException(status_code=400, detail="Invalid collection type")
    
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    
    if invoice.get("status") == "voided":
        raise HTTPException(status_code=400, detail="Cannot edit voided invoice")
    
    # Store original state for history
    original_state = {
        "items": invoice.get("items", []),
        "customer_name": invoice.get("customer_name", ""),
        "customer_id": invoice.get("customer_id"),
        "subtotal": invoice.get("subtotal", 0),
        "grand_total": invoice.get("grand_total", 0),
        "notes": invoice.get("notes", ""),
    }
    
    branch_id = invoice.get("branch_id", "")
    changes_made = []
    inventory_adjustments = []
    
    # Handle item changes
    new_items = data.get("items")
    if new_items is not None:
        old_items = {item.get("product_id"): item for item in invoice.get("items", [])}
        new_items_map = {item.get("product_id"): item for item in new_items}
        
        # Check for removed or reduced items (return to inventory)
        for prod_id, old_item in old_items.items():
            new_item = new_items_map.get(prod_id)
            old_qty = old_item.get("quantity", 0)
            new_qty = new_item.get("quantity", 0) if new_item else 0
            qty_diff = old_qty - new_qty
            
            if qty_diff > 0:
                product = await db.products.find_one({"id": prod_id}, {"_id": 0})
                if product:
                    if product.get("is_repack") and product.get("parent_id"):
                        units_per_parent = product.get("units_per_parent", 1)
                        parent_return = qty_diff / units_per_parent
                        await db.inventory.update_one(
                            {"product_id": product["parent_id"], "branch_id": branch_id},
                            {"$inc": {"quantity": parent_return}},
                            upsert=True
                        )
                        inventory_adjustments.append({
                            "product_id": product["parent_id"],
                            "change": parent_return,
                            "reason": f"Edit return from repack {product['name']}"
                        })
                    else:
                        await db.inventory.update_one(
                            {"product_id": prod_id, "branch_id": branch_id},
                            {"$inc": {"quantity": qty_diff}},
                            upsert=True
                        )
                        inventory_adjustments.append({
                            "product_id": prod_id,
                            "change": qty_diff,
                            "reason": "Edit return"
                        })
                changes_made.append(f"Reduced {old_item.get('product_name', prod_id)} qty: {old_qty} → {new_qty}")
        
        # Check for added or increased items (deduct from inventory)
        for prod_id, new_item in new_items_map.items():
            old_item = old_items.get(prod_id)
            old_qty = old_item.get("quantity", 0) if old_item else 0
            new_qty = new_item.get("quantity", 0)
            qty_diff = new_qty - old_qty
            
            if qty_diff > 0:
                product = await db.products.find_one({"id": prod_id}, {"_id": 0})
                if product:
                    if product.get("is_repack") and product.get("parent_id"):
                        units_per_parent = product.get("units_per_parent", 1)
                        parent_deduction = qty_diff / units_per_parent
                        await db.inventory.update_one(
                            {"product_id": product["parent_id"], "branch_id": branch_id},
                            {"$inc": {"quantity": -parent_deduction}},
                            upsert=True
                        )
                        inventory_adjustments.append({
                            "product_id": product["parent_id"],
                            "change": -parent_deduction,
                            "reason": f"Edit deduction for repack {product['name']}"
                        })
                    else:
                        await db.inventory.update_one(
                            {"product_id": prod_id, "branch_id": branch_id},
                            {"$inc": {"quantity": -qty_diff}},
                            upsert=True
                        )
                        inventory_adjustments.append({
                            "product_id": prod_id,
                            "change": -qty_diff,
                            "reason": "Edit deduction"
                        })
                
                if old_qty == 0:
                    changes_made.append(f"Added {new_item.get('product_name', prod_id)} qty: {new_qty}")
                else:
                    changes_made.append(f"Increased {new_item.get('product_name', prod_id)} qty: {old_qty} → {new_qty}")
            
            # Check for price changes
            if old_item:
                old_rate = old_item.get("rate", 0)
                new_rate = new_item.get("rate", 0)
                if old_rate != new_rate:
                    changes_made.append(f"Price changed for {new_item.get('product_name', prod_id)}: ₱{old_rate} → ₱{new_rate}")
                    
                    # For POs, update product cost
                    if collection_name == "purchase_orders" and new_rate > 0:
                        await db.products.update_one(
                            {"id": prod_id},
                            {"$set": {"cost_price": new_rate, "updated_at": now_iso()}}
                        )
                        changes_made.append(f"Updated product cost to ₱{new_rate}")
    
    # Prepare update data
    update_data = {}
    
    # Allowed editable fields
    editable_fields = ["customer_name", "customer_id", "notes", "terms", "customer_po",
                       "sales_rep_name", "sales_rep_id", "freight", "overall_discount"]
    for field in editable_fields:
        if field in data:
            old_val = invoice.get(field)
            new_val = data[field]
            if old_val != new_val:
                update_data[field] = new_val
                changes_made.append(f"{field}: '{old_val}' → '{new_val}'")
    
    # Date editing — only allowed if target date is not closed
    date_fields = ["order_date", "invoice_date"]
    for df in date_fields:
        if df in data and data[df] != invoice.get(df):
            new_date = data[df]
            inv_branch = invoice.get("branch_id", "")
            # Check if the NEW date is closed
            closed_check = await db.daily_closings.find_one(
                {"branch_id": inv_branch, "date": new_date, "status": "closed"}, {"_id": 0}
            )
            if closed_check:
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot change {df} to {new_date} — that date is already closed. Use a journal entry instead."
                )
            changes_made.append(f"{df}: '{invoice.get(df)}' → '{new_date}'")
            update_data[df] = new_date
            # Also update due_date if order_date changed and terms exist
            if df == "order_date" and invoice.get("terms_days", 0) > 0:
                od = datetime.strptime(new_date, "%Y-%m-%d")
                new_due = (od + timedelta(days=invoice["terms_days"])).strftime("%Y-%m-%d")
                update_data["due_date"] = new_due
                changes_made.append(f"due_date recalculated: '{invoice.get('due_date')}' → '{new_due}'")
    
    # Check if this invoice belongs to a closed day (requires manager PIN for any edit)
    inv_date = invoice.get("order_date") or invoice.get("invoice_date", "")
    inv_branch = invoice.get("branch_id", "")
    is_on_closed_day = False
    if inv_date and inv_branch:
        closed_day = await db.daily_closings.find_one(
            {"branch_id": inv_branch, "date": inv_date, "status": "closed"}, {"_id": 0}
        )
        if closed_day:
            is_on_closed_day = True
            if not pin:
                raise HTTPException(
                    status_code=400,
                    detail="This transaction belongs to a closed day. Manager PIN is required to edit."
                )
    
    # Update items if provided
    if new_items is not None:
        subtotal = 0
        for item in new_items:
            qty = item.get("quantity", 0)
            rate = item.get("rate", 0)
            disc_type = item.get("discount_type", "amount")
            disc_val = item.get("discount_value", 0)
            disc_amt = disc_val if disc_type == "amount" else round(qty * rate * disc_val / 100, 2)
            item["discount_amount"] = disc_amt
            item["total"] = round(qty * rate - disc_amt, 2)
            subtotal += item["total"]
        
        freight = data.get("freight", invoice.get("freight", 0))
        overall_discount = data.get("overall_discount", invoice.get("overall_discount", 0))
        grand_total = round(subtotal + freight - overall_discount, 2)
        
        amount_paid = invoice.get("amount_paid", 0)
        new_balance = max(0, grand_total - amount_paid)
        
        update_data["items"] = new_items
        update_data["subtotal"] = subtotal
        update_data["grand_total"] = grand_total
        update_data["balance"] = new_balance
        
        if new_balance <= 0:
            update_data["status"] = "paid"
        elif amount_paid > 0:
            update_data["status"] = "partial"
        else:
            update_data["status"] = "open"
        
        # Update customer balance if changed
        if invoice.get("customer_id"):
            old_balance = invoice.get("balance", 0)
            balance_diff = new_balance - old_balance
            if balance_diff != 0:
                await db.customers.update_one(
                    {"id": invoice["customer_id"]},
                    {"$inc": {"balance": balance_diff}}
                )
    
    if not update_data and not changes_made:
        return {"message": "No changes made", "invoice": invoice}
    
    # Mark as edited
    update_data["edited"] = True
    update_data["last_edited_at"] = now_iso()
    update_data["last_edited_by"] = user.get("full_name", user["username"])
    update_data["updated_at"] = now_iso()
    
    await collection.update_one({"id": invoice_id}, {"$set": update_data})
    
    # Create edit history record
    edit_record = {
        "id": new_id(),
        "invoice_id": invoice_id,
        "invoice_number": invoice.get(id_field, ""),
        "collection": collection_name,
        "edited_by_id": user["id"],
        "edited_by_name": user.get("full_name", user["username"]),
        "edited_at": now_iso(),
        "reason": reason,
        "proof_url": data.get("proof_url"),
        "changes": changes_made,
        "original_state": original_state,
        "inventory_adjustments": inventory_adjustments,
    }
    await db.invoice_edits.insert_one(edit_record)
    
    # Auto-create journal entry if editing a closed-day transaction with financial changes
    if is_on_closed_day and update_data.get("grand_total") is not None:
        old_total = invoice.get("grand_total", 0)
        new_total = update_data["grand_total"]
        diff = round(new_total - old_total, 2)
        if abs(diff) > 0.01:
            journal_entry = {
                "id": new_id(),
                "branch_id": inv_branch,
                "date": now_iso()[:10],
                "description": f"Adjustment for edited {invoice.get(id_field, '')} (closed day {inv_date}): {reason}",
                "entries": [
                    {"account": "Sales Adjustment", "debit": max(0, diff), "credit": max(0, -diff)},
                    {"account": "Accounts Receivable" if invoice.get("balance", 0) > 0 else "Cash", "debit": max(0, -diff), "credit": max(0, diff)},
                ],
                "reference_type": collection_name,
                "reference_id": invoice_id,
                "reference_number": invoice.get(id_field, ""),
                "total": abs(diff),
                "auto_generated": True,
                "source": "closed_day_edit",
                "created_by_id": user["id"],
                "created_by_name": user.get("full_name", user["username"]),
                "created_at": now_iso(),
            }
            await db.journal_entries.insert_one(journal_entry)
            del journal_entry["_id"]
            changes_made.append(f"Auto journal entry created: ₱{abs(diff):.2f} adjustment")
    
    updated_invoice = await collection.find_one({"id": invoice_id}, {"_id": 0})
    
    return {
        "message": "Invoice updated successfully",
        "changes": changes_made,
        "inventory_adjustments": inventory_adjustments,
        "invoice": updated_invoice
    }


@router.get("/invoices/{invoice_id}/edit-history")
async def get_invoice_edit_history(invoice_id: str, user=Depends(get_current_user)):
    """Get edit history for an invoice."""
    history = await db.invoice_edits.find(
        {"invoice_id": invoice_id}, {"_id": 0}
    ).sort("edited_at", -1).to_list(100)
    return history


# ==================== VOID / REOPEN ====================

@router.get("/invoices/history/by-date")
async def get_invoices_by_date(
    date: Optional[str] = None,
    branch_id: Optional[str] = None,
    search: Optional[str] = None,
    include_voided: bool = False,
    user=Depends(get_current_user),
):
    """
    Get all invoices for a specific date (defaults today).
    Used by the Sales History tab for quick cashier review.
    Returns running totals alongside the list.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    target_date = date or today

    query: dict = {}
    if not include_voided:
        query["status"] = {"$ne": "voided"}
    # Match by order_date OR invoice_date
    date_conditions = [
        {"order_date": target_date},
        {"invoice_date": target_date},
    ]

    branch_filter = await get_branch_filter(user, branch_id)
    query = apply_branch_filter(query, branch_filter)

    if search:
        # Combine date filter AND search filter using $and
        search_conditions = [
            {"invoice_number": {"$regex": search, "$options": "i"}},
            {"customer_name": {"$regex": search, "$options": "i"}},
        ]
        query["$and"] = [
            {"$or": date_conditions},
            {"$or": search_conditions},
        ]
    else:
        query["$or"] = date_conditions

    invoices = await db.invoices.find(query, {"_id": 0}).sort("created_at", -1).to_list(500)

    # Compute running totals
    cash_total = sum(
        float(inv.get("amount_paid", 0))
        for inv in invoices
        if inv.get("status") != "voided"
        and inv.get("payment_type") in ("cash", None)
        and inv.get("sale_type") != "cash_advance"
    )
    digital_total = sum(
        float(inv.get("grand_total", 0))
        for inv in invoices
        if inv.get("status") != "voided"
        and inv.get("payment_type") == "digital"
    )
    credit_total = sum(
        float(inv.get("balance", 0))
        for inv in invoices
        if inv.get("status") != "voided"
        and inv.get("payment_type") in ("credit", "partial")
    )
    # Partial payments: cash portion → cash total
    for inv in invoices:
        if inv.get("status") == "voided" or inv.get("payment_type") != "partial":
            continue
        cash_total += float(inv.get("amount_paid", 0))
    # Split payments: cash portion → cash, digital portion → digital
    for inv in invoices:
        if inv.get("status") == "voided" or inv.get("payment_type") != "split":
            continue
        cash_total += float(inv.get("cash_amount", 0))
        digital_total += float(inv.get("digital_amount", 0))
    grand_total = sum(
        float(inv.get("grand_total", 0))
        for inv in invoices
        if inv.get("status") != "voided"
    )
    voided_count = sum(1 for inv in invoices if inv.get("status") == "voided")

    return {
        "invoices": invoices,
        "totals": {
            "cash": round(cash_total, 2),
            "digital": round(digital_total, 2),
            "credit": round(credit_total, 2),
            "grand_total": round(grand_total, 2),
            "count": len([i for i in invoices if i.get("status") != "voided"]),
            "voided_count": voided_count,
        },
        "date": target_date,
    }


@router.post("/invoices/{inv_id}/void")
async def void_invoice(inv_id: str, data: dict, user=Depends(get_current_user)):
    """
    Void an invoice with manager PIN authorization.
    Reverses: inventory, cashflow, customer AR balance.
    The original invoice_date is preserved in the void snapshot for interest continuity.
    """
    check_perm(user, "sales", "void")

    # 1. Verify manager PIN
    manager_pin = data.get("manager_pin", "") or data.get("pin", "")
    if not manager_pin:
        raise HTTPException(status_code=400, detail="Manager PIN is required")

    from routes.verify import verify_pin_for_action
    verifier = await verify_pin_for_action(manager_pin, "void_invoice")
    if not verifier:
        raise HTTPException(status_code=403, detail="Invalid manager PIN")
    authorized_manager = {"id": verifier["verifier_id"], "full_name": verifier["verifier_name"], "username": verifier["verifier_name"]}

    # 2. Get invoice
    inv = await db.invoices.find_one({"id": inv_id}, {"_id": 0})
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if inv.get("status") == "voided":
        raise HTTPException(status_code=400, detail="Invoice is already voided")

    branch_id = inv.get("branch_id", "")
    reason = data.get("reason", "Voided by manager")

    # 3. Reverse inventory
    for item in inv.get("items", []):
        pid = item.get("product_id")
        if not pid:
            continue
        qty = float(item.get("quantity", 0))
        product = await db.products.find_one({"id": pid}, {"_id": 0})
        if not product:
            continue

        # For partial-release invoices: only the qty_remaining in reservations
        # was never physically received by the customer — add that back.
        # Qty already released (qty_reserved - qty_remaining) is gone (customer has it).
        # For full-release invoices: all qty goes back (existing behavior).
        is_partial_release = inv.get("release_mode") == "partial"

        if product.get("is_repack") and product.get("parent_id"):
            units_per_parent = product.get("units_per_parent", 1) or 1
            parent_return = qty / units_per_parent

            if is_partial_release:
                # Only return what's still in reserved_qty (not yet picked up)
                res = await db.sale_reservations.find_one(
                    {"invoice_id": inv_id, "product_id": product["parent_id"]}, {"_id": 0}
                )
                qty_still_reserved = float(res["qty_remaining"]) if res else 0
                if qty_still_reserved > 0:
                    await db.inventory.update_one(
                        {"product_id": product["parent_id"], "branch_id": branch_id},
                        {"$inc": {"quantity": qty_still_reserved, "reserved_qty": -qty_still_reserved},
                         "$set": {"updated_at": now_iso()}},
                        upsert=True,
                    )
                    await log_movement(
                        product["parent_id"], branch_id, "void_return", qty_still_reserved,
                        inv_id, inv["invoice_number"], 0,
                        user["id"], user.get("full_name", user["username"]),
                        f"Void (unreleased reserved stock): {inv['invoice_number']} — {reason}",
                    )
                # Clear the reservation record
                await db.sale_reservations.delete_many({"invoice_id": inv_id})
            else:
                await db.inventory.update_one(
                    {"product_id": product["parent_id"], "branch_id": branch_id},
                    {"$inc": {"quantity": parent_return}, "$set": {"updated_at": now_iso()}},
                    upsert=True,
                )
                await log_movement(
                    product["parent_id"], branch_id, "void_return", parent_return,
                    inv_id, inv["invoice_number"], 0,
                    user["id"], user.get("full_name", user["username"]),
                    f"Void: {inv['invoice_number']} — {reason}",
                )
        else:
            if is_partial_release:
                res = await db.sale_reservations.find_one(
                    {"invoice_id": inv_id, "product_id": pid}, {"_id": 0}
                )
                qty_still_reserved = float(res["qty_remaining"]) if res else 0
                if qty_still_reserved > 0:
                    await db.inventory.update_one(
                        {"product_id": pid, "branch_id": branch_id},
                        {"$inc": {"quantity": qty_still_reserved, "reserved_qty": -qty_still_reserved},
                         "$set": {"updated_at": now_iso()}},
                        upsert=True,
                    )
                    await log_movement(
                        pid, branch_id, "void_return", qty_still_reserved,
                        inv_id, inv["invoice_number"], 0,
                        user["id"], user.get("full_name", user["username"]),
                        f"Void (unreleased reserved stock): {inv['invoice_number']} — {reason}",
                    )
                await db.sale_reservations.delete_many({"invoice_id": inv_id})
            else:
                await db.inventory.update_one(
                    {"product_id": pid, "branch_id": branch_id},
                    {"$inc": {"quantity": qty}, "$set": {"updated_at": now_iso()}},
                    upsert=True,
                )
                await log_movement(
                    pid, branch_id, "void_return", qty,
                    inv_id, inv["invoice_number"], 0,
                    user["id"], user.get("full_name", user["username"]),
                    f"Void: {inv['invoice_number']} — {reason}",
                )

    # 4. Reverse cashflow — properly handle ALL payments (initial + subsequent AR)
    # Walk through each payment record to reverse from the correct wallet
    payments = inv.get("payments", [])
    total_cash_reversed = 0.0
    total_digital_reversed = 0.0
    reversed_payments = []

    for pmt in payments:
        if pmt.get("voided"):
            continue  # Skip already-voided individual payments
        pmt_amount = float(pmt.get("amount", 0))
        if pmt_amount <= 0:
            continue
        pmt_fund_source = pmt.get("fund_source", "cashier")
        pmt_method = pmt.get("method", "Cash")

        if pmt_fund_source == "digital" or is_digital_payment(pmt_method):
            await update_digital_wallet(
                branch_id, -pmt_amount,
                reference=f"VOID {inv['invoice_number']} (payment reversal)",
                platform=pmt.get("digital_platform", pmt_method),
                ref_number=pmt.get("digital_ref_number", ""),
            )
            total_digital_reversed += pmt_amount
        else:
            await update_cashier_wallet(
                branch_id, -pmt_amount,
                reference=f"VOID {inv['invoice_number']} (payment reversal)",
                allow_negative=True
            )
            total_cash_reversed += pmt_amount
        reversed_payments.append({
            "payment_id": pmt.get("id", ""),
            "amount": pmt_amount,
            "fund_source": pmt_fund_source,
            "method": pmt_method,
        })

    # 4b. Mark sales_log entries as voided
    await db.sales_log.update_many(
        {"branch_id": branch_id, "invoice_number": inv["invoice_number"]},
        {"$set": {"voided": True, "voided_at": now_iso()}}
    )

    # 5. Reverse FULL customer AR balance (original balance + any remaining)
    # The customer's balance was incremented by the original credit amount,
    # then decremented by each AR payment. We need to reverse the remaining balance.
    balance_owed = float(inv.get("balance", 0))
    if balance_owed > 0 and inv.get("customer_id"):
        await db.customers.update_one(
            {"id": inv["customer_id"]},
            {"$inc": {"balance": -balance_owed}}
        )

    # 6. Mark invoice as voided — preserve original_invoice_date for interest continuity
    await db.invoices.update_one(
        {"id": inv_id},
        {"$set": {
            "status": "voided",
            "voided_at": now_iso(),
            "voided_by_id": user["id"],
            "voided_by_name": user.get("full_name", user["username"]),
            "void_reason": reason,
            "void_authorized_by": authorized_manager.get("full_name", authorized_manager["username"]),
            "void_authorized_by_id": authorized_manager["id"],
            "original_invoice_date": inv.get("invoice_date", inv.get("order_date", "")),
        }}
    )

    # 7. Log void in invoice_edits for audit trail
    amount_paid = float(inv.get("amount_paid", 0))
    await db.invoice_edits.insert_one({
        "id": new_id(),
        "invoice_id": inv_id,
        "invoice_number": inv["invoice_number"],
        "collection": "invoices",
        "edited_by_id": user["id"],
        "edited_by_name": user.get("full_name", user["username"]),
        "edited_at": now_iso(),
        "reason": f"VOID: {reason}",
        "authorized_by": authorized_manager.get("full_name", authorized_manager["username"]),
        "changes": [
            "status: active → voided",
            f"cash reversed: ₱{total_cash_reversed:,.2f}",
            f"digital reversed: ₱{total_digital_reversed:,.2f}",
            f"payments reversed: {len(reversed_payments)}",
        ],
        "original_state": {
            "status": inv.get("status"),
            "grand_total": inv.get("grand_total"),
            "amount_paid": amount_paid,
            "balance": balance_owed,
            "items": inv.get("items", []),
            "payments": payments,
        },
        "reversed_payments": reversed_payments,
        "inventory_adjustments": [
            {"product_id": i.get("product_id"), "quantity_returned": i.get("quantity")}
            for i in inv.get("items", [])
        ],
    })

    return {
        "message": "Invoice voided",
        "invoice_number": inv["invoice_number"],
        "authorized_by": authorized_manager.get("full_name", authorized_manager["username"]),
        "original_invoice_date": inv.get("invoice_date", inv.get("order_date", "")),
        "snapshot": {
            "items": inv.get("items", []),
            "customer_id": inv.get("customer_id"),
            "customer_name": inv.get("customer_name"),
            "customer_contact": inv.get("customer_contact", ""),
            "grand_total": inv.get("grand_total"),
            "invoice_date": inv.get("invoice_date", inv.get("order_date", "")),
            "order_date": inv.get("order_date", ""),
            "terms": inv.get("terms", "COD"),
            "terms_days": inv.get("terms_days", 0),
            "interest_rate": inv.get("interest_rate", 0),
        },
    }


# ── Fix #10: Void a single payment record on an invoice ─────────────────────

@router.post("/invoices/{inv_id}/void-payment/{payment_id}")
async def void_invoice_payment(inv_id: str, payment_id: str, data: dict, user=Depends(get_current_user)):
    """
    Void a single payment record on an invoice.
    Reverses: the fund wallet movement + invoice balance/amount_paid + customer AR.
    Requires manager PIN for authorization.
    """
    from utils import verify_password

    check_perm(user, "accounting", "edit_expense")

    # Verify manager PIN
    manager_pin = data.get("manager_pin", "")
    if not manager_pin:
        raise HTTPException(status_code=400, detail="Manager PIN required")
    from routes.verify import verify_pin_for_action
    verifier = await verify_pin_for_action(manager_pin, "void_payment")
    if not verifier:
        raise HTTPException(status_code=403, detail="Invalid manager PIN")
    authorized_manager = {"id": verifier["verifier_id"], "full_name": verifier["verifier_name"], "username": verifier["verifier_name"]}

    inv = await db.invoices.find_one({"id": inv_id}, {"_id": 0})
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")

    payment = next((p for p in inv.get("payments", []) if p.get("id") == payment_id), None)
    if not payment:
        raise HTTPException(status_code=404, detail="Payment record not found")
    if payment.get("voided"):
        raise HTTPException(status_code=400, detail="Payment already voided")

    branch_id = inv.get("branch_id", "")
    amount = float(payment.get("amount", 0))
    fund_source = payment.get("fund_source", "cashier")
    reason = data.get("reason", "Payment reversed by manager")
    ref_text = f"Payment voided: {inv.get('invoice_number', '')} — {reason}"

    # Reverse the fund movement (take money back from the wallet it was deposited to)
    if amount > 0:
        pmt_method = payment.get("method", "Cash")
        if fund_source == "digital" or is_digital_payment(pmt_method):
            await update_digital_wallet(
                branch_id, -amount,
                reference=ref_text,
                platform=payment.get("digital_platform", pmt_method),
                ref_number=payment.get("digital_ref_number", ""),
            )
        elif fund_source == "safe":
            safe_wallet = await db.fund_wallets.find_one(
                {"branch_id": branch_id, "type": "safe", "active": True}, {"_id": 0}
            )
            if safe_wallet:
                remaining = amount
                for lot in await db.safe_lots.find(
                    {"wallet_id": safe_wallet["id"], "remaining_amount": {"$gt": 0}}, {"_id": 0}
                ).sort("remaining_amount", -1).to_list(500):
                    if remaining <= 0: break
                    take = min(lot["remaining_amount"], remaining)
                    await db.safe_lots.update_one({"id": lot["id"]}, {"$inc": {"remaining_amount": -take}})
                    remaining -= take
        else:
            await update_cashier_wallet(branch_id, -amount, ref_text)

    # Recalculate invoice balance and status
    new_paid = max(0, round(float(inv.get("amount_paid", 0)) - amount, 2))
    grand_total = float(inv.get("grand_total", 0))
    new_balance = round(grand_total - new_paid, 2)
    new_status = "paid" if new_balance <= 0 else ("partial" if new_paid > 0 else "open")

    await db.invoices.update_one(
        {"id": inv_id, "payments.id": payment_id},
        {"$set": {
            "amount_paid": new_paid,
            "balance": max(0, new_balance),
            "status": new_status,
            "payments.$.voided": True,
            "payments.$.voided_at": now_iso(),
            "payments.$.void_reason": reason,
            "payments.$.voided_by": user.get("full_name", user["username"]),
            "payments.$.void_authorized_by": authorized_manager.get("full_name", authorized_manager["username"]),
        }}
    )

    # Restore customer AR balance (payment reversal = they owe again)
    if inv.get("customer_id") and amount > 0:
        await db.customers.update_one({"id": inv["customer_id"]}, {"$inc": {"balance": amount}})

    return {
        "message": f"Payment of ₱{amount:,.2f} voided. Invoice balance: ₱{max(0, new_balance):,.2f}.",
        "new_balance": max(0, new_balance),
        "new_status": new_status,
        "authorized_by": authorized_manager.get("full_name", authorized_manager["username"]),
    }

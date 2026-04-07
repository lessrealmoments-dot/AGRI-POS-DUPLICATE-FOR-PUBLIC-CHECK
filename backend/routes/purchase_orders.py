"""
Purchase Order routes: CRUD, receiving, payments.
Supports multi-branch data isolation.
"""
from fastapi import APIRouter, Depends, HTTPException
from typing import Optional
from datetime import datetime, timezone, timedelta
import uuid
import logging
import traceback
from config import db
from utils import (
    get_current_user, check_perm, now_iso, new_id, 
    log_movement, update_cashier_wallet, update_digital_wallet, record_safe_movement,
    get_branch_filter, apply_branch_filter, ensure_branch_access,
    generate_next_number, check_idempotency, ensure_org_context,
)

logger = logging.getLogger("purchase_orders")

router = APIRouter(prefix="/purchase-orders", tags=["Purchase Orders"])


# ── Shared inventory-receive helper ──────────────────────────────────────────
async def _apply_po_inventory(po: dict, user: dict, capital_choices: dict = None):
    """
    Update inventory + product costs from a PO's items.
    capital_choices: dict of {product_id: "last_purchase"|"moving_average"}
    """
    if capital_choices is None:
        capital_choices = {}
    branch_id = po.get("branch_id", "")

    # Ensure org context for super admin
    await ensure_org_context(branch_id=branch_id, org_id=po.get("organization_id"))
    po_number = po.get("po_number", "unknown")

    for idx, item in enumerate(po.get("items", [])):
        pid = item.get("product_id")
        if not pid:
            continue
        qty = float(item.get("quantity", 0))
        price = float(item.get("unit_price", 0))
        product_name = item.get("product_name", pid)

        try:
            # Step 1: Update inventory
            existing = await db.inventory.find_one({"product_id": pid, "branch_id": branch_id})
            if existing:
                await db.inventory.update_one(
                    {"product_id": pid, "branch_id": branch_id},
                    {"$inc": {"quantity": qty}, "$set": {"updated_at": now_iso()}}
                )
            else:
                await db.inventory.insert_one({
                    "id": new_id(), "product_id": pid, "branch_id": branch_id,
                    "quantity": qty, "updated_at": now_iso()
                })

            # Step 2: Log movement
            await log_movement(
                pid, branch_id, "purchase", qty, po["id"], po_number,
                price, user["id"], user.get("full_name", user["username"]),
                f"PO received from {po['vendor']}"
            )

            # Step 3: Calculate moving average — BRANCH-SPECIFIC
            branch_acq_query = {"product_id": pid, "type": {"$in": ["purchase", "transfer_in"]}, "quantity_change": {"$gt": 0}}
            if branch_id:
                branch_acq_query["branch_id"] = branch_id
            all_acquisitions = await db.movements.find(
                branch_acq_query, {"_id": 0}
            ).to_list(10000)
            total_pqty = sum(float(m.get("quantity_change") or 0) for m in all_acquisitions)
            total_pcost = sum(float(m.get("quantity_change") or 0) * float(m.get("price_at_time") or 0) for m in all_acquisitions)
            moving_avg = round(total_pcost / total_pqty, 2) if total_pqty > 0 else price

            # Step 4: Fetch old capital
            product = await db.products.find_one({"id": pid}, {"_id": 0})
            global_capital = float(product.get("cost_price") or 0) if product else 0
            old_capital = global_capital
            if branch_id:
                bp_doc = await db.branch_prices.find_one(
                    {"product_id": pid, "branch_id": branch_id}, {"_id": 0}
                )
                if bp_doc and bp_doc.get("cost_price") is not None:
                    old_capital = float(bp_doc["cost_price"])

            # Step 5: Determine new capital
            explicit_choice = capital_choices.get(pid)
            if explicit_choice:
                choice = explicit_choice
            elif price < old_capital and old_capital > 0 and price > 0:
                choice = "moving_average"
            else:
                choice = "last_purchase"
            new_capital = moving_avg if choice == "moving_average" else price

            # Step 6: Update global reference fields only (cost_price is branch-specific)
            product_update = {
                "last_vendor": po["vendor"],
                "moving_average_cost": moving_avg,
            }
            await db.products.update_one({"id": pid}, {"$set": product_update})

            # Step 7: Update branch-specific cost
            if branch_id:
                await db.branch_prices.update_one(
                    {"product_id": pid, "branch_id": branch_id},
                    {"$set": {
                        "cost_price": new_capital,
                        "moving_average_cost": moving_avg,
                        "source": "purchase_order",
                        "updated_at": now_iso(),
                    }},
                    upsert=True
                )

            # Step 8: Log capital change
            await db.capital_changes.insert_one({
                "id": new_id(),
                "product_id": pid,
                "branch_id": branch_id,
                "old_capital": old_capital,
                "new_capital": new_capital,
                "method": choice,
                "source_type": "purchase_order",
                "source_ref": po_number,
                "vendor": po.get("vendor", ""),
                "changed_by_id": user["id"],
                "changed_by_name": user.get("full_name", user.get("username", "")),
                "changed_at": now_iso(),
            })

            # Step 9: Update vendor last_price — BRANCH-SPECIFIC
            await db.product_vendors.update_one(
                {"product_id": pid, "vendor_name": po["vendor"], "branch_id": branch_id},
                {"$set": {"last_price": price, "last_order_date": now_iso()[:10]},
                 "$setOnInsert": {"id": new_id(), "created_at": now_iso()}},
                upsert=True
            )

        except Exception as e:
            logger.error(
                f"PO {po_number} — inventory update FAILED for item #{idx+1} "
                f"'{product_name}' (pid={pid}): {str(e)}\n{traceback.format_exc()}"
            )
            raise HTTPException(
                status_code=500,
                detail=f"Failed on item '{product_name}': {str(e)}"
            )


# ── Fund balance helper ────────────────────────────────────────────────────────
async def _get_fund_balances(branch_id: str) -> dict:
    cashier_wallet = await db.fund_wallets.find_one({"branch_id": branch_id, "type": "cashier", "active": True}, {"_id": 0})
    cashier_balance = float(cashier_wallet.get("balance", 0)) if cashier_wallet else 0.0
    cashier_id = cashier_wallet.get("id") if cashier_wallet else None

    safe_wallet = await db.fund_wallets.find_one({"branch_id": branch_id, "type": "safe", "active": True}, {"_id": 0})
    safe_balance = 0.0
    safe_id = safe_wallet.get("id") if safe_wallet else None
    if safe_wallet:
        lots = await db.safe_lots.find(
            {"wallet_id": safe_wallet["id"], "remaining_amount": {"$gt": 0}}, {"_id": 0}
        ).to_list(500)
        safe_balance = sum(lot["remaining_amount"] for lot in lots)

    return {
        "cashier": round(cashier_balance, 2), "cashier_id": cashier_id,
        "safe": round(safe_balance, 2), "safe_id": safe_id,
        "cashier_is_negative": cashier_balance < 0,
        "cashier_warning": f"Cashier is at ₱{cashier_balance:,.2f} (negative). Use the Safe instead." if cashier_balance < 0 else None,
    }


@router.get("/fund-balances")
async def get_fund_balances(branch_id: str = "", user=Depends(get_current_user)):
    """Get live fund balances (cashier + safe) for a branch. Used before PO payment."""
    return await _get_fund_balances(branch_id)



@router.get("/incoming-requests")
async def get_incoming_requests(
    user=Depends(get_current_user),
    branch_id: Optional[str] = None,
):
    """Get stock requests directed TO this branch (supply_branch_id = branch_id)."""
    supply_branch = branch_id or user.get("branch_id", "")
    if not supply_branch and user.get("role") != "admin":
        return []

    query = {"po_type": "branch_request", "status": {"$nin": ["cancelled"]}}
    if supply_branch:
        query["supply_branch_id"] = supply_branch
    # Admin sees all
    if user.get("role") == "admin" and not supply_branch:
        query.pop("supply_branch_id", None)

    requests = await db.purchase_orders.find(query, {"_id": 0}).sort("created_at", -1).to_list(200)
    return {"requests": requests, "total": len(requests)}


@router.get("")
async def list_purchase_orders(
    user=Depends(get_current_user),
    status: Optional[str] = None,
    branch_id: Optional[str] = None,
    po_type: Optional[str] = None,
    skip: int = 0,
    limit: int = 50
):
    """List all purchase orders with optional status/type filter. Respects branch isolation."""
    query = {}
    
    # Apply branch filter for data isolation
    branch_filter = await get_branch_filter(user, branch_id)
    query = apply_branch_filter(query, branch_filter)
    
    if status:
        query["status"] = status
    if po_type:
        query["po_type"] = po_type
    
    total = await db.purchase_orders.count_documents(query)
    items = await db.purchase_orders.find(query, {"_id": 0}).sort("created_at", -1).skip(skip).limit(limit).to_list(limit)

    # Enrich with receipt count for POs that don't have it stored yet
    po_ids = [po["id"] for po in items]
    if po_ids:
        receipt_agg = await db.upload_sessions.aggregate([
            {"$match": {"record_type": "purchase_order", "record_id": {"$in": po_ids}}},
            {"$group": {"_id": "$record_id", "count": {"$sum": "$file_count"}}}
        ]).to_list(len(po_ids))
        receipt_map = {r["_id"]: r["count"] for r in receipt_agg}
        for po in items:
            if "receipt_count" not in po:
                po["receipt_count"] = receipt_map.get(po["id"], 0)

    return {"purchase_orders": items, "total": total}





@router.post("")
async def create_purchase_order(data: dict, user=Depends(get_current_user)):
    """
    Create a purchase order.
    po_type:
      'draft'  — save only, no inventory/fund change (goods not yet arrived)
      'cash'   — receive immediately + deduct from fund + create expense
      'terms'  — receive immediately + create accounts payable
    Legacy: payment_method='cash'|'credit' still accepted for backward compatibility.
    """
    check_perm(user, "purchase_orders", "create")

    branch_id = data.get("branch_id", "") or user.get("branch_id", "")
    
    # Idempotency check — prevent duplicate POs from offline sync
    idem_key = data.get("idempotency_key")
    if idem_key:
        existing = await check_idempotency("purchase_orders", idem_key)
        if existing:
            return existing

    po_type = data.get("po_type", None)

    # Backward-compat: map old payment_method to po_type
    if po_type is None:
        pm = data.get("payment_method", "cash")
        po_type = "cash" if pm == "cash" else "terms"

    # ── Compute line-item totals ───────────────────────────────────────────
    purchase_date = data.get("purchase_date", now_iso()[:10])
    items_raw = data.get("items", [])
    items = []
    line_subtotal = 0.0
    for i in items_raw:
        qty = float(i.get("quantity", 0))
        unit_price = float(i.get("unit_price", 0))
        disc_type = i.get("discount_type", "amount")
        disc_val = float(i.get("discount_value", 0))
        disc_amt = round(qty * unit_price * disc_val / 100, 2) if disc_type == "percent" else round(disc_val, 2)
        total = round(qty * unit_price - disc_amt, 2)
        items.append({
            "product_id": i.get("product_id", ""),
            "product_name": i.get("product_name", ""),
            "unit": i.get("unit", ""),
            "description": i.get("description", ""),
            "quantity": qty,
            "unit_price": unit_price,
            "discount_type": disc_type,
            "discount_value": disc_val,
            "discount_amount": disc_amt,
            "total": total,
        })
        line_subtotal += total

    # ── Overall discount ───────────────────────────────────────────────────
    od_type = data.get("overall_discount_type", "amount")
    od_val = float(data.get("overall_discount_value", 0))
    overall_disc_amt = round(line_subtotal * od_val / 100, 2) if od_type == "percent" else round(od_val, 2)
    after_discount = round(line_subtotal - overall_disc_amt, 2)

    # ── Freight + Tax ──────────────────────────────────────────────────────
    freight = float(data.get("freight", 0))
    pre_tax = round(after_discount + freight, 2)
    tax_rate = float(data.get("tax_rate", 0))
    tax_amount = round(pre_tax * tax_rate / 100, 2) if tax_rate > 0 else 0.0
    grand_total = round(pre_tax + tax_amount, 2)

    # ── Due date ───────────────────────────────────────────────────────────
    terms_days = int(data.get("terms_days", 0))
    if terms_days > 0:
        pd = datetime.strptime(purchase_date, "%Y-%m-%d")
        due_date = (pd + timedelta(days=terms_days)).strftime("%Y-%m-%d")
    else:
        due_date = data.get("due_date", "")

    # ── Determine status ───────────────────────────────────────────────────
    if po_type == "draft":
        status = "draft"
        payment_status = "unpaid"
        amount_paid = 0.0
        balance = grand_total
    elif po_type == "cash":
        status = "received"
        payment_status = "paid"
        amount_paid = grand_total
        balance = 0.0
    elif po_type == "branch_request":
        status = "requested"
        payment_status = "n/a"
        amount_paid = 0.0
        balance = 0.0
    else:  # terms
        status = "received"
        payment_status = "unpaid"
        amount_paid = 0.0
        balance = grand_total

    po = {
        "id": new_id(),
        "po_number": data.get("po_number", "").strip() or await generate_next_number("PO", branch_id),
        "vendor": data["vendor"],
        "dr_number": data.get("dr_number", ""),
        "branch_id": branch_id,
        "items": items,
        "line_subtotal": round(line_subtotal, 2),
        "subtotal": round(line_subtotal, 2),       # backward compat alias
        "overall_discount_type": od_type,
        "overall_discount_value": od_val,
        "overall_discount_amount": overall_disc_amt,
        "freight": freight,
        "tax_rate": tax_rate,
        "tax_amount": tax_amount,
        "grand_total": grand_total,
        "status": status,
        "po_type": po_type,
        "payment_method": data.get("payment_method", "cash" if po_type == "cash" else "credit"),
        "payment_method_detail": data.get("payment_method_detail", "Cash"),
        "payment_status": payment_status,
        "amount_paid": amount_paid,
        "balance": balance,
        "purchase_date": purchase_date,
        "due_date": due_date,
        "terms_days": terms_days,
        "terms_label": data.get("terms_label", ""),
        "received_date": now_iso() if po_type in ("cash", "terms") else None,
        "notes": data.get("notes", ""),
        # Branch request fields
        "supply_branch_id": data.get("supply_branch_id", ""),  # which branch will supply
        "show_retail": data.get("show_retail", True),           # whether to show retail price suggestion
        "created_by": user["id"],
        "created_by_name": user.get("full_name", user["username"]),
        "idempotency_key": idem_key,
        "created_at": now_iso(),
    }

    try:
        await db.purchase_orders.insert_one(po)
    except Exception as e:
        logger.error(f"Failed to insert PO: {str(e)}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Failed to save PO: {str(e)}")
    del po["_id"]

    # Auto-generate doc code so QR is ready on the printed PO slip
    from routes.doc_lookup import auto_generate_doc_code
    try:
        doc_code = await auto_generate_doc_code(
            "purchase_order", po["id"],
            org_id=user.get("org_id", user.get("organization_id", "")),
            created_by=user.get("id", ""),
        )
        po["doc_code"] = doc_code
    except Exception:
        pass  # Non-critical

    # ── Branch request: notify supply branch ──────────────────────────────
    if po_type == "branch_request":
        supply_branch_id = data.get("supply_branch_id", "")
        requesting_branch = await db.branches.find_one({"id": branch_id}, {"_id": 0, "name": 1})
        req_name = requesting_branch.get("name", branch_id) if requesting_branch else branch_id
        supply_users = await db.users.find(
            {"branch_id": supply_branch_id, "active": True}, {"_id": 0, "id": 1}
        ).to_list(50)
        admins = await db.users.find({"role": "admin", "active": True}, {"_id": 0, "id": 1}).to_list(50)
        target_ids = list({u["id"] for u in supply_users + admins})
        item_summary = ", ".join(f"{i['product_name']} ×{i['quantity']}" for i in items[:3])
        if len(items) > 3:
            item_summary += f" +{len(items)-3} more"
        await db.notifications.insert_one({
            "id": new_id(),
            "type": "branch_stock_request",
            "title": f"Stock Request from {req_name}",
            "message": f"{req_name} requested: {item_summary}. Generate a Branch Transfer to fulfill.",
            "branch_id": supply_branch_id,
            "branch_name": req_name,
            "metadata": {
                "po_id": po["id"],
                "po_number": po["po_number"],
                "requesting_branch_id": branch_id,
                "requesting_branch_name": req_name,
                "item_count": len(items),
            },
            "target_user_ids": target_ids,
            "read_by": [],
            "created_at": now_iso(),
        })

    # ── Cash: validate fund + deduct + expense (BEFORE upload linking) ────
    if po_type == "cash" and grand_total > 0:
        fund_source = data.get("fund_source", "cashier")
        balances = await _get_fund_balances(branch_id)

        if fund_source == "safe" and balances["safe"] < grand_total:
            await db.purchase_orders.delete_one({"id": po["id"]})
            raise HTTPException(status_code=400, detail={
                "type": "insufficient_funds",
                "message": f"Safe has only ₱{balances['safe']:,.2f}. Need ₱{grand_total:,.2f}.",
                "cashier_balance": balances["cashier"], "safe_balance": balances["safe"],
                "shortfall": round(grand_total - balances["safe"], 2),
            })
        if fund_source != "safe" and balances["cashier"] < grand_total:
            await db.purchase_orders.delete_one({"id": po["id"]})
            raise HTTPException(status_code=400, detail={
                "type": "insufficient_funds",
                "message": f"Cashier has only ₱{balances['cashier']:,.2f}. Need ₱{grand_total:,.2f}.",
                "cashier_balance": balances["cashier"], "safe_balance": balances["safe"],
                "shortfall": round(grand_total - balances["cashier"], 2),
                "can_use_safe": balances["safe"] >= grand_total,
            })

        ref_text = f"PO {po['po_number']} — {data['vendor']}"
        if data.get("check_number"):
            ref_text += f" | Check #{data['check_number']}"

        try:
            if fund_source == "safe" and balances["safe_id"]:
                remaining = grand_total
                for lot in await db.safe_lots.find(
                    {"wallet_id": balances["safe_id"], "remaining_amount": {"$gt": 0}}, {"_id": 0}
                ).sort("remaining_amount", -1).to_list(500):
                    if remaining <= 0: break
                    take = min(lot["remaining_amount"], remaining)
                    await db.safe_lots.update_one({"id": lot["id"]}, {"$inc": {"remaining_amount": -take}})
                    remaining -= take
            else:
                await update_cashier_wallet(branch_id, -grand_total, ref_text)
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"PO {po['po_number']} — fund deduction failed: {str(e)}\n{traceback.format_exc()}")
            raise HTTPException(status_code=500, detail=f"Fund deduction failed: {str(e)}")

        payment_record = {
            "id": new_id(), "amount": grand_total,
            "date": purchase_date,
            "method": data.get("payment_method_detail", "Cash"),
            "fund_source": fund_source,
            "check_number": data.get("check_number", ""),
            "reference": data.get("dr_number", ""),
            "recorded_by": user.get("full_name", user["username"]),
            "recorded_at": now_iso(),
        }
        await db.purchase_orders.update_one(
            {"id": po["id"]},
            {"$push": {"payment_history": payment_record},
             "$set": {"fund_source": fund_source}}
        )

        await db.expenses.insert_one({
            "id": new_id(), "branch_id": branch_id,
            "category": "Purchase Payment",
            "description": f"PO {po['po_number']} — {data['vendor']}",
            "notes": f"DR#{data.get('dr_number','')} | {data.get('notes','')}".strip(" |"),
            "amount": grand_total,
            "payment_method": data.get("payment_method_detail", "Cash"),
            "reference_number": po["po_number"],
            "date": purchase_date,
            "po_id": po["id"], "po_number": po["po_number"], "vendor": data["vendor"],
            "fund_source": fund_source,
            "created_by": user["id"], "created_by_name": user.get("full_name", user["username"]),
            "created_at": now_iso(),
        })

    # ── Terms: create accounts payable ────────────────────────────────────
    if po_type == "terms" and grand_total > 0:
        await db.payables.insert_one({
            "id": new_id(),
            "supplier": data["vendor"],
            "branch_id": branch_id,
            "description": f"Purchase Order {po['po_number']}",
            "po_id": po["id"],
            "amount": grand_total,
            "paid": 0,
            "balance": grand_total,
            "due_date": due_date,
            "terms_days": terms_days,
            "terms_label": data.get("terms_label", ""),
            "status": "pending",
            "created_at": now_iso(),
        })

    # ── Receive inventory for cash + terms ────────────────────────────────
    if po_type in ("cash", "terms"):
        capital_choices = data.get("capital_choices", {})
        try:
            await _apply_po_inventory(po, user, capital_choices)
        except HTTPException:
            raise
        except Exception as e:
            import traceback
            traceback.print_exc()
            raise HTTPException(
                status_code=500,
                detail=f"PO created but inventory update failed: {str(e)}. "
                       f"PO {po['po_number']} saved as '{status}'. Please contact admin."
            )

    # ── Link upload sessions LAST (non-critical — must not block PO) ──────
    upload_session_ids = data.get("upload_session_ids", [])
    if upload_session_ids:
        from pathlib import Path
        upload_dir = Path("/app/uploads")
        for sid in upload_session_ids:
            try:
                session = await db.upload_sessions.find_one({"id": sid}, {"_id": 0})
                if not session:
                    continue
                old_record_id = session.get("record_id", "")
                new_dir = upload_dir / "purchase_order" / po["id"]
                new_dir.mkdir(parents=True, exist_ok=True)
                updated_files = []
                for f in session.get("files", []):
                    stored = f.get("stored_path", "")
                    if not stored or stored == ".":
                        updated_files.append(f)
                        continue
                    old_path = Path(stored)
                    if old_path.is_file():
                        try:
                            new_path = new_dir / old_path.name
                            old_path.rename(new_path)
                            f["stored_path"] = str(new_path)
                        except OSError:
                            pass
                    updated_files.append(f)
                if old_record_id:
                    old_dir = upload_dir / "purchase_order" / old_record_id
                    if old_dir.is_dir() and not any(old_dir.iterdir()):
                        try:
                            old_dir.rmdir()
                        except Exception:
                            pass
                await db.upload_sessions.update_one(
                    {"id": sid},
                    {"$set": {
                        "record_type": "purchase_order",
                        "record_id": po["id"],
                        "is_pending": False,
                        "reassigned_at": now_iso(),
                        "files": updated_files,
                    }}
                )
            except Exception as e:
                logger.error(f"PO {po['po_number']} — upload session linking failed (non-critical): {str(e)}")
        # Update PO with receipt count
        total_receipts = 0
        for sid in upload_session_ids:
            session_doc = await db.upload_sessions.find_one({"id": sid}, {"_id": 0, "file_count": 1})
            if session_doc:
                total_receipts += session_doc.get("file_count", 0)
        if total_receipts > 0:
            await db.purchase_orders.update_one(
                {"id": po["id"]},
                {"$set": {"receipt_count": total_receipts}}
            )
            po["receipt_count"] = total_receipts

    return po


@router.put("/{po_id}")
async def update_purchase_order(po_id: str, data: dict, user=Depends(get_current_user)):
    """
    Edit a PO. Only reopened (ordered/draft) POs can be edited.
    For reopened POs (was received, then reopened): auto-generates a change log.
    Manager reason required.
    """
    if user.get("role") not in ["admin", "manager"]:
        raise HTTPException(status_code=403, detail="Manager or admin required")

    po = await db.purchase_orders.find_one({"id": po_id}, {"_id": 0})
    if not po:
        raise HTTPException(status_code=404, detail="PO not found")
    if po.get("status") == "sent_to_terminal":
        raise HTTPException(status_code=423, detail="PO is locked — currently being checked on a terminal. Finalize it on the terminal first.")
    if po["status"] not in ("draft", "ordered", "in_progress"):
        raise HTTPException(status_code=400, detail="Only draft or reopened (ordered) POs can be edited")

    # ── Build change log for reopened POs ────────────────────────────────
    change_log = None
    was_reopened = po.get("status") == "ordered" and po.get("reopened_at")
    if was_reopened:
        changes = []
        old_map = {i["product_id"]: i for i in po.get("items", [])}
        for new_i in data.get("items", po.get("items", [])):
            pid = new_i.get("product_id", "")
            old_i = old_map.get(pid)
            if old_i:
                old_qty = float(old_i.get("quantity", 0))
                new_qty = float(new_i.get("quantity", old_qty))
                old_price = float(old_i.get("unit_price", 0))
                new_price = float(new_i.get("unit_price", old_price))
                if old_qty != new_qty:
                    changes.append({"field": "quantity", "product": new_i.get("product_name", pid),
                                    "old": old_qty, "new": new_qty,
                                    "display": f"{new_i.get('product_name', pid)}: qty {old_qty} → {new_qty}"})
                if round(old_price, 4) != round(new_price, 4):
                    changes.append({"field": "unit_price", "product": new_i.get("product_name", pid),
                                    "old": old_price, "new": new_price,
                                    "display": f"{new_i.get('product_name', pid)}: ₱{old_price:.2f} → ₱{new_price:.2f}"})
            else:
                changes.append({"field": "item_added", "product": new_i.get("product_name", pid),
                                 "new": new_i.get("quantity"),
                                 "display": f"Added: {new_i.get('product_name', pid)} × {new_i.get('quantity')}"})
        if po.get("dr_number") != data.get("dr_number", po.get("dr_number")):
            changes.append({"field": "dr_number", "old": po.get("dr_number", ""), "new": data.get("dr_number", ""),
                             "display": f"DR# {po.get('dr_number') or 'none'} → {data.get('dr_number')}"})
        if changes:
            change_log = {"changed_at": now_iso(), "changed_by": user.get("full_name", user["username"]),
                          "reason": data.get("edit_reason", "No reason provided"),
                          "changes": changes,
                          "change_summary": "; ".join(c["display"] for c in changes[:5])}

    # ── Recompute totals ─────────────────────────────────────────────────
    items_raw = data.get("items", po.get("items", []))
    items = []
    line_subtotal = 0.0
    for i in items_raw:
        qty = float(i.get("quantity", 0))
        unit_price = float(i.get("unit_price", 0))
        disc_type = i.get("discount_type", "amount")
        disc_val = float(i.get("discount_value", 0))
        disc_amt = round(qty * unit_price * disc_val / 100, 2) if disc_type == "percent" else round(disc_val, 2)
        total = round(qty * unit_price - disc_amt, 2)
        items.append({**i, "quantity": qty, "unit_price": unit_price, "discount_amount": disc_amt, "total": total})
        line_subtotal += total

    od_type = data.get("overall_discount_type", po.get("overall_discount_type", "amount"))
    od_val = float(data.get("overall_discount_value", po.get("overall_discount_value", 0)))
    overall_disc = round(line_subtotal * od_val / 100, 2) if od_type == "percent" else round(od_val, 2)
    freight = float(data.get("freight", po.get("freight", 0)))
    tax_rate = float(data.get("tax_rate", po.get("tax_rate", 0)))
    pre_tax = round(line_subtotal - overall_disc + freight, 2)
    tax_amount = round(pre_tax * tax_rate / 100, 2)
    grand_total = round(pre_tax + tax_amount, 2)

    update = {
        "items": items,
        "line_subtotal": round(line_subtotal, 2),
        "subtotal": round(line_subtotal, 2),
        "overall_discount_type": od_type,
        "overall_discount_value": od_val,
        "overall_discount_amount": overall_disc,
        "freight": freight,
        "tax_rate": tax_rate,
        "tax_amount": tax_amount,
        "grand_total": grand_total,
        # FIX #3: always recalculate balance from current amount_paid, never assume paid=0 balance
        "balance": max(0, round(grand_total - float(po.get("amount_paid", 0)), 2)),
        "dr_number": data.get("dr_number", po.get("dr_number", "")),
        "notes": data.get("notes", po.get("notes", "")),
        "updated_at": now_iso(),
        "updated_by": user.get("full_name", user["username"]),
    }

    # Date editing — only if target date is not closed
    new_purchase_date = data.get("purchase_date")
    if new_purchase_date and new_purchase_date != po.get("purchase_date"):
        po_branch = po.get("branch_id", "")
        closed_check = await db.daily_closings.find_one(
            {"branch_id": po_branch, "date": new_purchase_date, "status": "closed"}, {"_id": 0}
        )
        if closed_check:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot change purchase date to {new_purchase_date} — that date is already closed."
            )
        update["purchase_date"] = new_purchase_date
        if change_log is None:
            change_log = {"changed_at": now_iso(), "changed_by": user.get("full_name", user["username"]),
                          "reason": data.get("edit_reason", "Date change"),
                          "changes": [{"field": "purchase_date", "old": po.get("purchase_date", ""), "new": new_purchase_date,
                                       "display": f"Date: {po.get('purchase_date', '')} → {new_purchase_date}"}],
                          "change_summary": f"Date changed: {po.get('purchase_date', '')} → {new_purchase_date}"}

    if change_log:
        await db.purchase_orders.update_one({"id": po_id}, {"$set": update, "$push": {"edit_history": change_log}})
    else:
        await db.purchase_orders.update_one({"id": po_id}, {"$set": update})

    return await db.purchase_orders.find_one({"id": po_id}, {"_id": 0})


@router.post("/{po_id}/adjust-payment")
async def adjust_po_payment(po_id: str, data: dict, user=Depends(get_current_user)):
    """
    Adjust a PO's payment status after an edit changed the grand_total.

    Standard ERP adjustment pattern:
      Δ = new_grand_total - old_grand_total

      If Δ > 0 (total increased — more owed):
        - Cash PO: deduct Δ from fund, create additional expense record
        - Terms PO: increase balance by Δ (auto, no fund movement)

      If Δ < 0 (total decreased — overpaid):
        - Cash PO: refund |Δ| back to fund, create credit expense record
        - Terms PO: reduce balance by |Δ| (auto, no fund movement)

    This creates a full audit trail: original payment + adjustment record.
    Requires manager or admin.
    """
    if user.get("role") not in ["admin", "manager"]:
        raise HTTPException(status_code=403, detail="Manager or admin required")

    po = await db.purchase_orders.find_one({"id": po_id}, {"_id": 0})
    if not po:
        raise HTTPException(status_code=404, detail="PO not found")

    new_total = float(data.get("new_grand_total", po.get("grand_total", po.get("subtotal", 0))))
    old_total = float(data.get("old_grand_total", po.get("grand_total", po.get("subtotal", 0))))
    amount_paid = float(po.get("amount_paid", 0))
    delta = round(new_total - old_total, 2)
    reason = data.get("reason", "PO edit — payment adjusted")
    fund_source = data.get("fund_source", "cashier")
    branch_id = po.get("branch_id", "")
    po_type = po.get("po_type", po.get("payment_method", ""))

    if delta == 0:
        return {"message": "No payment adjustment needed (amount unchanged)", "delta": 0}

    adjustment_entry = {
        "id": new_id(),
        "amount": abs(delta),
        "delta": delta,
        "date": now_iso()[:10],
        "method": data.get("payment_method", "Cash"),
        "fund_source": fund_source,
        "type": "additional_payment" if delta > 0 else "overpayment_refund",
        "reason": reason,
        "recorded_by": user.get("full_name", user["username"]),
        "recorded_at": now_iso(),
    }

    # ── Terms PO: just update balance (no fund movement) ─────────────────
    if po_type in ("terms", "credit") or po.get("payment_method") == "credit":
        new_balance = round(max(0, new_total - amount_paid), 2)
        new_payment_status = "paid" if new_balance == 0 else ("partial" if amount_paid > 0 else "unpaid")
        await db.purchase_orders.update_one(
            {"id": po_id},
            {"$set": {
                "grand_total": new_total, "subtotal": new_total,
                "balance": new_balance,
                "payment_status": new_payment_status,
            }, "$push": {"payment_adjustments": adjustment_entry}}
        )
        return {
            "message": f"Payable {'increased' if delta > 0 else 'reduced'} by ₱{abs(delta):.2f}. New balance: ₱{new_balance:.2f}",
            "delta": delta,
            "new_balance": new_balance,
            "payment_status": new_payment_status,
        }

    # ── Cash PO: move funds ────────────────────────────────────────────────
    balances = await _get_fund_balances(branch_id)
    ref_text = f"PO {po['po_number']} payment adjustment — {po['vendor']} (reason: {reason})"

    if delta > 0:
        # More owed — check fund has enough
        avail = balances["safe"] if fund_source == "safe" else balances["cashier"]
        if avail < delta:
            raise HTTPException(status_code=400, detail={
                "type": "insufficient_funds",
                "message": f"{fund_source.title()} has ₱{avail:,.2f}, need ₱{delta:,.2f} more to settle the adjustment.",
                "shortfall": round(delta - avail, 2),
            })
        # Deduct from fund
        if fund_source == "safe" and balances["safe_id"]:
            remaining = delta
            for lot in await db.safe_lots.find(
                {"wallet_id": balances["safe_id"], "remaining_amount": {"$gt": 0}}, {"_id": 0}
            ).sort("remaining_amount", -1).to_list(500):
                if remaining <= 0: break
                take = min(lot["remaining_amount"], remaining)
                await db.safe_lots.update_one({"id": lot["id"]}, {"$inc": {"remaining_amount": -take}})
                remaining -= take
            await record_safe_movement(branch_id, -delta, ref_text)
        else:
            await update_cashier_wallet(branch_id, -delta, ref_text)

        # Create additional expense
        await db.expenses.insert_one({
            "id": new_id(), "branch_id": branch_id,
            "category": "Purchase Payment",
            "description": f"Additional payment — PO {po['po_number']} — {po['vendor']}",
            "notes": f"Reason: {reason} | Δ +₱{delta:.2f}",
            "amount": delta, "payment_method": data.get("payment_method", "Cash"),
            "fund_source": fund_source, "reference_number": po["po_number"],
            "date": now_iso()[:10], "po_id": po_id,
            "created_by": user["id"], "created_by_name": user.get("full_name", user["username"]),
            "created_at": now_iso(),
        })

        new_amount_paid = round(amount_paid + delta, 2)
        new_balance = max(0, round(new_total - new_amount_paid, 2))

    else:
        # Overpaid — refund |delta| back to fund
        if fund_source == "safe" and balances["safe_id"]:
            await db.safe_lots.insert_one({
                "id": new_id(), "branch_id": branch_id,
                "wallet_id": balances["safe_id"],
                "date_received": now_iso()[:10],
                "original_amount": abs(delta), "remaining_amount": abs(delta),
                "source_reference": ref_text,
                "created_by": user["id"],
                "created_at": now_iso(),
            })
            await record_safe_movement(branch_id, abs(delta), ref_text)
        else:
            await update_cashier_wallet(branch_id, abs(delta), ref_text)

        # Create credit/refund expense (negative amount adjusts the books)
        await db.expenses.insert_one({
            "id": new_id(), "branch_id": branch_id,
            "category": "Purchase Payment",
            "description": f"Overpayment refund — PO {po['po_number']} — {po['vendor']}",
            "notes": f"Reason: {reason} | Δ ₱{delta:.2f} (refund)",
            "amount": delta,  # negative amount
            "payment_method": data.get("payment_method", "Cash"),
            "fund_source": fund_source, "reference_number": po["po_number"],
            "date": now_iso()[:10], "po_id": po_id,
            "created_by": user["id"], "created_by_name": user.get("full_name", user["username"]),
            "created_at": now_iso(),
        })

        new_amount_paid = round(amount_paid + delta, 2)  # delta is negative
        new_balance = max(0, round(new_total - new_amount_paid, 2))

    new_payment_status = "paid" if new_balance == 0 else "partial"
    await db.purchase_orders.update_one(
        {"id": po_id},
        {"$set": {
            "grand_total": new_total, "subtotal": new_total,
            "amount_paid": new_amount_paid,
            "balance": new_balance,
            "payment_status": new_payment_status,
        }, "$push": {"payment_adjustments": adjustment_entry}}
    )

    direction = "additional payment collected" if delta > 0 else "overpayment refunded"
    return {
        "message": f"₱{abs(delta):.2f} {direction} {'from' if delta > 0 else 'to'} {fund_source}. New status: {new_payment_status}.",
        "delta": delta,
        "new_amount_paid": new_amount_paid,
        "new_balance": new_balance,
        "payment_status": new_payment_status,
    }



@router.post("/{po_id}/receive")
async def receive_purchase_order(po_id: str, data: dict = None, user=Depends(get_current_user)):
    """Receive a purchase order (Draft/Ordered) and update inventory.
    Accepts optional body: { capital_choices: { product_id: "last_purchase"|"moving_average" } }
    Receipt upload is mandatory — at least 1 file must be attached before receiving.
    """
    if data is None:
        data = {}
    check_perm(user, "purchase_orders", "receive")

    po = await db.purchase_orders.find_one({"id": po_id}, {"_id": 0})
    if not po:
        raise HTTPException(status_code=404, detail="PO not found")
    if po["status"] == "received":
        raise HTTPException(status_code=400, detail="PO already received")

    # ── Mandatory receipt check ───────────────────────────────────────────
    upload_sessions = await db.upload_sessions.find(
        {"record_type": "purchase_order", "record_id": po_id},
        {"_id": 0, "file_count": 1}
    ).to_list(20)
    total_receipts = sum(s.get("file_count", 0) for s in upload_sessions)
    if total_receipts == 0 and not data.get("skip_receipt_check"):
        raise HTTPException(
            status_code=400,
            detail="Receipt upload required. Please upload at least 1 receipt photo before receiving this PO."
        )

    capital_choices = data.get("capital_choices", {})
    try:
        await _apply_po_inventory(po, user, capital_choices)
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update inventory: {str(e)}. PO remains in current state."
        )

    await db.purchase_orders.update_one(
        {"id": po_id},
        {"$set": {
            "status": "received",
            "received_date": now_iso(),
            "receipt_count": total_receipts,
            "receipt_review_status": "pending",
        }}
    )

    # ── Notify owner/admin that PO was received and needs review ──────────
    po_number = po.get("po_number", "")
    vendor = po.get("vendor", "")
    admins = await db.users.find(
        {"role": "admin", "active": True}, {"_id": 0, "id": 1}
    ).to_list(10)
    admin_ids = [a["id"] for a in admins]
    if admin_ids:
        notification = {
            "id": new_id(),
            "type": "po_receipt_review",
            "title": f"PO {po_number} received — review receipts",
            "message": f"{user.get('full_name', user['username'])} received PO {po_number} from {vendor} with {total_receipts} receipt photo(s). Please review.",
            "target_user_ids": admin_ids,
            "record_type": "purchase_order",
            "record_id": po_id,
            "read_by": [],
            "created_at": now_iso(),
        }
        await db.notifications.insert_one(notification)

    return {"message": "PO received, inventory updated", "receipt_count": total_receipts}


# ── Terminal Integration: Send to Terminal / Terminal Finalize ──────────────

@router.post("/{po_id}/send-to-terminal")
async def send_po_to_terminal(po_id: str, user=Depends(get_current_user)):
    """
    Mark a PO as 'sent_to_terminal' — locks it on PC for terminal checking.
    The terminal will verify quantities and finalize.
    """
    if user.get("role") not in ["admin", "manager"]:
        raise HTTPException(status_code=403, detail="Manager or admin required")

    po = await db.purchase_orders.find_one({"id": po_id}, {"_id": 0})
    if not po:
        raise HTTPException(status_code=404, detail="PO not found")
    if po["status"] not in ("draft", "ordered", "in_progress"):
        raise HTTPException(status_code=400, detail=f"Cannot send PO with status '{po['status']}' to terminal")

    await db.purchase_orders.update_one(
        {"id": po_id},
        {"$set": {
            "status": "sent_to_terminal",
            "sent_to_terminal_at": now_iso(),
            "sent_to_terminal_by": user.get("full_name", user.get("username", "")),
        }}
    )

    # Notify terminals for this branch via WebSocket
    branch_id = po.get("branch_id")
    if branch_id:
        try:
            from routes.terminal_ws import terminal_ws_manager
            from config import _raw_db
            terminals = await _raw_db.terminal_sessions.find(
                {"branch_id": branch_id, "status": "active"}, {"_id": 0, "terminal_id": 1}
            ).to_list(20)
            for t in terminals:
                await terminal_ws_manager.notify_terminal(t["terminal_id"], "po_assigned", {
                    "po_id": po_id,
                    "po_number": po.get("po_number", ""),
                    "vendor": po.get("vendor", ""),
                    "item_count": len(po.get("items", [])),
                })
        except Exception as e:
            logger.warning(f"Failed to notify terminals: {e}")

    return {"message": f"PO {po.get('po_number', '')} sent to terminal for checking"}


@router.post("/{po_id}/terminal-finalize")
async def terminal_finalize_po(po_id: str, data: dict, user=Depends(get_current_user)):
    """
    Terminal finalizes a PO after verifying quantities.
    Updates items with received quantities and changes status back to 'ordered'.
    The PC user can then proceed with the normal receive flow.
    Body: { items: [{product_id, qty_received, ...}], terminal_id, notes }
    """
    po = await db.purchase_orders.find_one({"id": po_id}, {"_id": 0})
    if not po:
        raise HTTPException(status_code=404, detail="PO not found")
    if po["status"] != "sent_to_terminal":
        raise HTTPException(status_code=400, detail="PO is not in terminal checking mode")

    terminal_items = data.get("items", [])
    terminal_id = data.get("terminal_id", "")
    notes = data.get("notes", "")

    # Build updated items with received quantities
    old_items = {i.get("product_id"): i for i in po.get("items", [])}
    updated_items = []
    variances = []

    for ti in terminal_items:
        pid = ti.get("product_id")
        old_item = old_items.get(pid, {})
        ordered_qty = float(old_item.get("quantity", 0))
        received_qty = float(ti.get("qty_received", ordered_qty))

        updated_item = {**old_item, "quantity": received_qty}
        if "original_ordered" not in updated_item:
            updated_item["original_ordered"] = ordered_qty

        # Recompute total
        unit_price = float(updated_item.get("unit_price", 0))
        disc_amt = float(updated_item.get("discount_amount", 0))
        updated_item["total"] = round(received_qty * unit_price - disc_amt, 2)
        updated_items.append(updated_item)

        if ordered_qty != received_qty:
            variances.append({
                "product_id": pid,
                "product_name": old_item.get("product_name", ""),
                "ordered": ordered_qty,
                "received": received_qty,
                "difference": received_qty - ordered_qty,
            })

    # Recompute totals
    subtotal = sum(i.get("total", 0) for i in updated_items)
    overall_disc = float(po.get("overall_discount", 0))
    grand_total = round(subtotal - overall_disc, 2)

    update_doc = {
        "items": updated_items,
        "subtotal": subtotal,
        "grand_total": grand_total,
        "status": "ordered",  # Unlock — back to ordered so PC can receive
        "terminal_verified": True,
        "terminal_verified_at": now_iso(),
        "terminal_verified_by": user.get("full_name", user.get("username", "")),
        "terminal_id": terminal_id,
        "terminal_variances": variances,
        "terminal_notes": notes,
    }

    await db.purchase_orders.update_one({"id": po_id}, {"$set": update_doc})

    return {
        "message": f"PO {po.get('po_number', '')} verified by terminal",
        "variances": len(variances),
        "variance_details": variances,
    }


@router.get("/{po_id}/capital-preview")
async def get_capital_preview(po_id: str, user=Depends(get_current_user)):
    """
    Preview the capital impact of receiving a PO.
    Returns each item with: current_capital, new_price, projected_moving_avg, needs_warning.
    needs_warning=True when new_price < current_capital (price dropped).
    """
    po = await db.purchase_orders.find_one({"id": po_id}, {"_id": 0})
    if not po:
        raise HTTPException(status_code=404, detail="PO not found")

    po_branch_id = po.get("branch_id", "")
    items_preview = []
    for item in po.get("items", []):
        pid = item.get("product_id")
        if not pid:
            continue
        new_price = float(item.get("unit_price") or 0)
        qty = float(item.get("quantity") or 0)

        product = await db.products.find_one({"id": pid}, {"_id": 0})
        if not product:
            continue

        # Use branch-specific cost if available, else global
        global_capital = float(product.get("cost_price") or 0)
        current_capital = global_capital
        if po_branch_id:
            bp_doc = await db.branch_prices.find_one(
                {"product_id": pid, "branch_id": po_branch_id}, {"_id": 0}
            )
            if bp_doc and bp_doc.get("cost_price") is not None:
                current_capital = float(bp_doc["cost_price"])

        # Historical moving average — BRANCH-SPECIFIC (POs + transfers)
        acq_query = {"product_id": pid, "type": {"$in": ["purchase", "transfer_in"]}, "quantity_change": {"$gt": 0}}
        if po_branch_id:
            acq_query["branch_id"] = po_branch_id
        all_acquisitions = await db.movements.find(
            acq_query, {"_id": 0}
        ).to_list(10000)
        total_pqty = sum(float(m.get("quantity_change") or 0) for m in all_acquisitions)
        total_pcost = sum(float(m.get("quantity_change") or 0) * float(m.get("price_at_time") or 0) for m in all_acquisitions)
        current_moving_avg = round(total_pcost / total_pqty, 2) if total_pqty > 0 else current_capital

        # Projected moving average AFTER this purchase
        proj_qty = total_pqty + qty
        proj_cost = total_pcost + qty * new_price
        projected_moving_avg = round(proj_cost / proj_qty, 2) if proj_qty > 0 else new_price

        needs_warning = new_price < current_capital and new_price > 0 and current_capital > 0
        price_drop_pct = round((current_capital - new_price) / current_capital * 100, 1) if needs_warning else 0

        items_preview.append({
            "product_id": pid,
            "product_name": item.get("product_name", product.get("name", "")),
            "sku": product.get("sku", ""),
            "qty": qty,
            "unit": item.get("unit", product.get("unit", "")),
            "new_price": new_price,
            "current_capital": current_capital,
            "current_moving_avg": current_moving_avg,
            "projected_moving_avg": projected_moving_avg,
            "needs_warning": needs_warning,
            "price_drop_pct": price_drop_pct,
        })

    has_warnings = any(i["needs_warning"] for i in items_preview)
    return {
        "po_number": po.get("po_number", ""),
        "vendor": po.get("vendor", ""),
        "has_warnings": has_warnings,
        "items": items_preview,
    }


@router.post("/{po_id}/generate-branch-transfer")
async def generate_branch_transfer_from_request(po_id: str, user=Depends(get_current_user)):
    """
    Convert a branch_request PO into a Branch Transfer order.
    Pre-loads all items from the PO with requested qty + available stock from source branch.
    Returns the pre-filled transfer data for the frontend to open in Branch Transfer form.
    """
    if user.get("role") not in ["admin", "manager"]:
        raise HTTPException(status_code=403, detail="Manager or admin required")

    po = await db.purchase_orders.find_one({"id": po_id}, {"_id": 0})
    if not po:
        raise HTTPException(status_code=404, detail="PO not found")
    if po.get("po_type") != "branch_request":
        raise HTTPException(status_code=400, detail="Only branch request POs can be converted to transfers")
    if po.get("status") not in ("requested", "draft"):
        raise HTTPException(status_code=400, detail="Request has already been processed")

    # The supply branch is the "from" branch, the requesting branch is "to" branch
    from_branch_id = po.get("supply_branch_id", "")
    to_branch_id = po.get("branch_id", "")

    if not from_branch_id or not to_branch_id:
        raise HTTPException(status_code=400, detail="Branch IDs missing from request")

    # Get branch names for context
    from_branch = await db.branches.find_one({"id": from_branch_id}, {"_id": 0, "name": 1})
    to_branch = await db.branches.find_one({"id": to_branch_id}, {"_id": 0, "name": 1})

    # Build pre-filled transfer items — fetch branch capital + available stock for each product
    transfer_items = []
    for item in po.get("items", []):
        product_id = item.get("product_id", "")
        product = await db.products.find_one({"id": product_id}, {"_id": 0})
        if not product:
            continue

        # Get effective cost from source branch
        bp = await db.branch_prices.find_one(
            {"product_id": product_id, "branch_id": from_branch_id}, {"_id": 0}
        )
        branch_capital = float(bp["cost_price"]) if bp and bp.get("cost_price") else float(product.get("cost_price", 0))

        # Get available stock at source branch
        inv = await db.inventory.find_one(
            {"product_id": product_id, "branch_id": from_branch_id}, {"_id": 0}
        )
        available_stock = float(inv["quantity"]) if inv else 0.0

        # Get last retail at destination (price memory)
        mem = await db.branch_transfer_price_memory.find_one(
            {"product_id": product_id, "branch_id": to_branch_id}, {"_id": 0}
        )

        requested_qty = float(item.get("quantity", 1))
        send_qty = min(requested_qty, available_stock)  # default = min of requested & available

        transfer_items.append({
            "product_id": product_id,
            "product_name": product.get("name", item.get("product_name", "")),
            "sku": product.get("sku", ""),
            "category": product.get("category", "General"),
            "unit": product.get("unit", item.get("unit", "")),
            "requested_qty": requested_qty,
            "available_stock": available_stock,
            "qty": send_qty,
            "branch_capital": branch_capital,
            "transfer_capital": branch_capital,  # default = source cost, manager can adjust
            "branch_retail": mem.get("last_retail_price") if mem else 0.0,
            "last_branch_retail": mem.get("last_retail_price") if mem else None,
            "show_retail": po.get("show_retail", True),
        })

    # Mark the PO as "in_progress"
    await db.purchase_orders.update_one(
        {"id": po_id},
        {"$set": {"status": "in_progress", "fulfillment_started_at": now_iso(),
                  "fulfillment_started_by": user.get("full_name", user["username"])}}
    )

    return {
        "message": "Transfer pre-filled from request",
        "po_id": po_id,
        "po_number": po["po_number"],
        "from_branch_id": from_branch_id,
        "to_branch_id": to_branch_id,
        "from_branch_name": from_branch.get("name", "") if from_branch else "",
        "to_branch_name": to_branch.get("name", "") if to_branch else "",
        "show_retail": po.get("show_retail", True),
        "items": transfer_items,
        "notes": po.get("notes", ""),
    }


@router.delete("/{po_id}")
async def cancel_purchase_order(po_id: str, data: dict = None, user=Depends(get_current_user)):
    """Cancel a purchase order. Requires PIN verification. Cannot cancel received POs — use Reopen instead."""
    check_perm(user, "purchase_orders", "delete")

    # PIN verification
    pin = (data or {}).get("pin", "")
    if not pin:
        raise HTTPException(status_code=400, detail="PIN is required to cancel a PO")
    from routes.verify import verify_pin_for_action
    verifier = await verify_pin_for_action(pin, "cancel_po")
    if not verifier:
        raise HTTPException(status_code=403, detail="Invalid PIN")

    po = await db.purchase_orders.find_one({"id": po_id}, {"_id": 0})
    if not po:
        raise HTTPException(status_code=404, detail="PO not found")
    if po.get("status") == "received":
        raise HTTPException(
            status_code=400,
            detail="Cannot cancel a received PO — inventory has already been added. Use 'Reopen' to correct it."
        )
    if po.get("status") == "cancelled":
        raise HTTPException(status_code=400, detail="PO is already cancelled")
    await db.purchase_orders.update_one({"id": po_id}, {"$set": {"status": "cancelled"}})
    return {"message": "PO cancelled"}


@router.post("/{po_id}/pay")
async def pay_purchase_order(po_id: str, data: dict, user=Depends(get_current_user)):
    """
    Record a payment on a purchase order.
    Requires PIN (manager/admin/TOTP for cashier+safe; admin/TOTP only for bank+digital).
    Routes payment to the correct wallet, creates expense record (Z-report/Close Wizard),
    and auto-generates a double-entry journal for bank/digital payments.
    """
    check_perm(user, "accounting", "create")

    po = await db.purchase_orders.find_one({"id": po_id}, {"_id": 0})
    if not po:
        raise HTTPException(status_code=404, detail="PO not found")
    if po.get("payment_status") == "paid":
        raise HTTPException(status_code=400, detail="PO already paid in full")

    fund_source = data.get("fund_source", "cashier")  # cashier | safe | bank | digital
    branch_id = po.get("branch_id", "")

    # ── PIN Verification (before any financial operation) ─────────────────────
    pin = str(data.get("pin", ""))
    if not pin:
        raise HTTPException(status_code=400, detail="PIN or TOTP is required to record a payment")

    from routes.verify import verify_pin_for_action
    policy_key = "pay_po_bank" if fund_source in ("bank", "digital") else "pay_po_standard"
    verifier = await verify_pin_for_action(pin, policy_key, branch_id=branch_id)
    if not verifier:
        raise HTTPException(status_code=403, detail="Invalid PIN or TOTP — payment not recorded")

    # ── Amount — use stored balance as authoritative source ───────────────────
    po_total = float(po.get("grand_total") or po.get("subtotal", 0))
    stored_balance = float(po.get("balance", 0))
    # If balance not stored, derive it
    if stored_balance <= 0 and po_total > 0:
        stored_balance = max(0, po_total - float(po.get("amount_paid", 0)))
    amount = float(data.get("amount", stored_balance))
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Payment amount must be greater than zero")
    if amount > stored_balance + 0.01:  # 1-cent tolerance for float drift
        raise HTTPException(status_code=400, detail=f"Payment ₱{amount:.2f} exceeds outstanding balance ₱{stored_balance:.2f}")

    # ── Build reference text ──────────────────────────────────────────────────
    ref_parts = [f"PO Payment {po['po_number']} - {po.get('vendor', '')}"]
    if data.get("check_number"):
        ref_parts.append(f"Check #{data['check_number']}")
    if data.get("reference"):
        ref_parts.append(data["reference"])
    ref_text = " | ".join(ref_parts)

    # ── Fund balance checks ───────────────────────────────────────────────────
    cashier_wallet = await db.fund_wallets.find_one({"branch_id": branch_id, "type": "cashier", "active": True}, {"_id": 0})
    cashier_balance = float(cashier_wallet.get("balance", 0)) if cashier_wallet else 0

    safe_wallet = await db.fund_wallets.find_one({"branch_id": branch_id, "type": "safe", "active": True}, {"_id": 0})
    safe_balance = 0
    if safe_wallet:
        lots = await db.safe_lots.find({"wallet_id": safe_wallet["id"], "remaining_amount": {"$gt": 0}}, {"_id": 0}).to_list(500)
        safe_balance = sum(float(lot["remaining_amount"]) for lot in lots)

    bank_wallet = await db.fund_wallets.find_one({"branch_id": branch_id, "type": "bank", "active": True}, {"_id": 0})
    bank_balance = float(bank_wallet.get("balance", 0)) if bank_wallet else 0

    digital_wallet = await db.fund_wallets.find_one({"branch_id": branch_id, "type": "digital", "active": True}, {"_id": 0})
    digital_balance = float(digital_wallet.get("balance", 0)) if digital_wallet else 0

    if fund_source == "cashier" and cashier_balance < amount:
        raise HTTPException(status_code=400, detail={
            "type": "insufficient_funds",
            "message": f"Cashier has ₱{cashier_balance:.2f}. Short by ₱{amount - cashier_balance:.2f}.",
            "cashier_balance": cashier_balance, "safe_balance": safe_balance,
            "shortfall": round(amount - cashier_balance, 2),
            "can_use_safe": safe_balance >= amount,
        })
    if fund_source == "safe" and safe_balance < amount:
        raise HTTPException(status_code=400, detail={
            "type": "insufficient_funds",
            "message": f"Safe has ₱{safe_balance:.2f}. Short by ₱{amount - safe_balance:.2f}.",
            "cashier_balance": cashier_balance, "safe_balance": safe_balance,
            "shortfall": round(amount - safe_balance, 2),
        })
    if fund_source == "bank":
        if not bank_wallet:
            raise HTTPException(status_code=400, detail="No bank wallet configured for this branch")
        if bank_balance < amount:
            raise HTTPException(status_code=400, detail={
                "type": "insufficient_funds",
                "message": f"Bank account has ₱{bank_balance:.2f}. Short by ₱{amount - bank_balance:.2f}.",
                "bank_balance": bank_balance, "shortfall": round(amount - bank_balance, 2),
            })
    if fund_source == "digital":
        if not digital_wallet:
            raise HTTPException(status_code=400, detail="No digital wallet configured for this branch")
        if digital_balance < amount:
            raise HTTPException(status_code=400, detail={
                "type": "insufficient_funds",
                "message": f"Digital wallet has ₱{digital_balance:.2f}. Short by ₱{amount - digital_balance:.2f}.",
                "digital_balance": digital_balance, "shortfall": round(amount - digital_balance, 2),
            })

    # ── Deduct from selected fund ─────────────────────────────────────────────
    if fund_source == "safe" and safe_wallet:
        remaining = amount
        for lot in await db.safe_lots.find(
            {"wallet_id": safe_wallet["id"], "remaining_amount": {"$gt": 0}},
            {"_id": 0}
        ).sort("remaining_amount", -1).to_list(500):
            if remaining <= 0:
                break
            take = min(float(lot["remaining_amount"]), remaining)
            await db.safe_lots.update_one({"id": lot["id"]}, {"$inc": {"remaining_amount": -take}})
            remaining -= take
        await record_safe_movement(branch_id, -amount, ref_text)

    elif fund_source == "digital":
        await update_digital_wallet(branch_id, -amount, ref_text)

    elif fund_source == "bank":
        new_bank_bal = round(bank_balance - amount, 2)
        await db.fund_wallets.update_one({"id": bank_wallet["id"]}, {"$inc": {"balance": -round(amount, 2)}})
        await db.wallet_movements.insert_one({
            "id": new_id(), "wallet_id": bank_wallet["id"], "branch_id": branch_id,
            "type": "bank_out", "amount": -round(amount, 2),
            "reference": ref_text, "balance_after": new_bank_bal, "created_at": now_iso(),
        })

    else:  # cashier (default)
        await update_cashier_wallet(branch_id, -amount, ref_text)

    # ── Compute new PO balance ────────────────────────────────────────────────
    new_paid = float(po.get("amount_paid", 0)) + amount
    new_balance = max(0, round(po_total - new_paid, 2)) if po_total > 0 else max(0, round(stored_balance - amount, 2))
    new_status = "paid" if new_balance <= 0 else "partial"

    payment_record = {
        "id": new_id(), "amount": round(amount, 2),
        "date": data.get("payment_date", now_iso()[:10]),
        "check_number": data.get("check_number", ""),
        "check_date": data.get("check_date", ""),
        "method": data.get("method", "Cash"),
        "fund_source": fund_source,
        "reference": data.get("reference", ""),
        "recorded_by": verifier["verifier_name"],
        "recorded_by_id": verifier["verifier_id"],
        "auth_method": verifier.get("method", ""),
        "recorded_at": now_iso(),
    }

    await db.purchase_orders.update_one({"id": po_id}, {
        "$set": {"amount_paid": round(new_paid, 2), "balance": new_balance, "payment_status": new_status},
        "$push": {"payment_history": payment_record}
    })

    # ── Update payable record if one exists ───────────────────────────────────
    payable = await db.payables.find_one({"po_id": po_id}, {"_id": 0})
    if payable:
        pay_new_paid = float(payable.get("paid", 0)) + amount
        pay_new_balance = max(0, round(float(payable["amount"]) - pay_new_paid, 2))
        await db.payables.update_one({"po_id": po_id}, {
            "$set": {
                "paid": round(pay_new_paid, 2),
                "balance": pay_new_balance,
                "status": "paid" if pay_new_balance <= 0 else "partial",
            }
        })

    # ── Expense record — picked up by Z-report and Close Wizard ──────────────
    # fund_source stored so Z-report can correctly split cashier vs safe vs digital/bank
    await db.expenses.insert_one({
        "id": new_id(), "branch_id": branch_id,
        "organization_id": po.get("organization_id"),
        "category": "Purchase Payment",
        "description": f"PO {po['po_number']} — {po.get('vendor', '')}",
        "notes": (f"Check #{data['check_number']}" if data.get("check_number") else data.get("reference", "")),
        "amount": round(amount, 2),
        "payment_method": data.get("method", "Cash"),
        "reference_number": data.get("check_number") or data.get("reference", ""),
        "date": data.get("payment_date", now_iso()[:10]),
        "fund_source": fund_source,
        "po_id": po_id, "po_number": po["po_number"], "vendor": po.get("vendor", ""),
        "created_by": verifier["verifier_id"],
        "created_by_name": verifier["verifier_name"],
        "created_at": now_iso(),
    })

    # ── Smart double-entry journal for bank/digital payments ──────────────────
    # Cashier/Safe: wallet movements + expense record already serve as the audit trail.
    # Bank/Digital: require a formal AP journal entry (Debit AP / Credit Bank or Digital).
    if fund_source in ("bank", "digital"):
        account_credit_code = "1030" if fund_source == "bank" else "1020"
        account_credit_name = "Cash - Bank Account" if fund_source == "bank" else "Digital Wallet (GCash/Maya)"
        je_number = await generate_next_number("JE", branch_id)
        je_doc = {
            "id": new_id(),
            "je_number": je_number,
            "entry_type": "ap_payment",
            "entry_type_label": "Accounts Payable Payment",
            "branch_id": branch_id,
            "organization_id": po.get("organization_id"),
            "effective_date": data.get("payment_date", now_iso()[:10]),
            "posted_date": now_iso()[:10],
            "memo": f"Supplier payment — {po['po_number']} · {po.get('vendor', '')} via {fund_source}",
            "reference_number": po["po_number"],
            "reference_type": "purchase_order",
            "lines": [
                {
                    "account_code": "2000",
                    "account_name": "Accounts Payable",
                    "debit": round(amount, 2), "credit": 0.0,
                    "memo": f"Pay {po.get('vendor', '')} — {po['po_number']}",
                },
                {
                    "account_code": account_credit_code,
                    "account_name": account_credit_name,
                    "debit": 0.0, "credit": round(amount, 2),
                    "memo": f"Paid from {fund_source} wallet",
                },
            ],
            "total_amount": round(amount, 2),
            "status": "posted",
            "auto_generated": True,
            "authorized_by_id": verifier["verifier_id"],
            "authorized_by_name": verifier["verifier_name"],
            "authorized_method": verifier.get("method", ""),
            "created_by_id": verifier["verifier_id"],
            "created_by_name": verifier["verifier_name"],
            "created_at": now_iso(),
            "voided": False, "void_reason": "", "voided_at": "", "voided_by": "",
        }
        await db.journal_entries.insert_one(je_doc)

    # ── Notify admin of payment ───────────────────────────────────────────────
    await _notify_ap_payment(po, amount, fund_source, new_balance, new_status, verifier, po.get("organization_id"))

    return {
        "message": f"Payment of ₱{amount:.2f} recorded from {fund_source}",
        "new_balance": new_balance,
        "payment_status": new_status,
        "fund_source": fund_source,
        "authorized_by": verifier["verifier_name"],
    }


async def _notify_ap_payment(po, amount, fund_source, new_balance, new_status, verifier, org_id):
    """Fire notification after a supplier payment is recorded."""
    from routes.notifications import create_notification
    admins = await db.users.find({"role": "admin", "active": True}, {"_id": 0, "id": 1}).to_list(50)
    admin_ids = [a["id"] for a in admins]
    if not admin_ids:
        return
    status_note = "— fully paid" if new_status == "paid" else f"— ₱{new_balance:,.2f} remaining"
    await create_notification(
        type_key="ap_payment",
        title=f"Supplier Payment — {po.get('po_number')}",
        message=f"{verifier['verifier_name']} paid ₱{amount:,.2f} to {po.get('vendor', 'supplier')} "
                f"from {fund_source} ({po.get('po_number')}) {status_note}.",
        target_user_ids=admin_ids,
        branch_id=po.get("branch_id", ""),
        metadata={
            "po_id": po.get("id"),
            "po_number": po.get("po_number"),
            "vendor": po.get("vendor"),
            "amount": round(amount, 2),
            "fund_source": fund_source,
            "new_balance": new_balance,
            "payment_status": new_status,
            "authorized_by": verifier["verifier_name"],
        },
        organization_id=org_id,
    )


@router.get("/vendors")
async def list_po_vendors(user=Depends(get_current_user)):
    """Get unique vendor names from purchase orders."""
    vendors = await db.purchase_orders.distinct("vendor", {"status": {"$ne": "cancelled"}})
    return sorted(vendors)



@router.get("/vendor-prices")
async def get_vendor_prices(
    vendor: str = "",
    branch_id: str = "",
    user=Depends(get_current_user),
):
    """
    Get product last-prices for a vendor at a specific branch.
    Used by PO form to auto-fill unit prices when a supplier is selected.
    Returns a dict: { product_id: last_price }
    """
    if not vendor:
        return {}
    query = {"vendor_name": vendor}
    if branch_id:
        query["branch_id"] = branch_id
    prices = await db.product_vendors.find(query, {"_id": 0}).to_list(5000)
    return {pv["product_id"]: pv.get("last_price", 0) for pv in prices if pv.get("last_price", 0) > 0}



@router.get("/unpaid-summary")
async def get_unpaid_po_summary(user=Depends(get_current_user), branch_id: Optional[str] = None):
    """Get unpaid POs ranked by urgency: overdue > due soon > later. For dashboard widget."""
    from datetime import timedelta
    query = {"payment_status": {"$in": ["unpaid", "partial"]}, "status": {"$ne": "cancelled"}}
    if branch_id:
        query["branch_id"] = branch_id
    pos = await db.purchase_orders.find(query, {"_id": 0}).sort("due_date", 1).to_list(500)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    soon = (datetime.now(timezone.utc) + timedelta(days=7)).strftime("%Y-%m-%d")

    overdue, due_soon, later = [], [], []
    for po in pos:
        balance = po.get("balance", po.get("subtotal", 0))
        item = {"id": po["id"], "po_number": po["po_number"], "vendor": po["vendor"],
                "balance": balance, "due_date": po.get("due_date", ""),
                "purchase_date": po.get("purchase_date", ""), "status": po.get("status", "")}
        due = po.get("due_date", "")
        if due and due < today:
            overdue.append(item)
        elif due and due <= soon:
            due_soon.append(item)
        else:
            later.append(item)

    return {
        "total_unpaid": round(sum(po.get("balance", po.get("subtotal", 0)) for po in pos), 2),
        "overdue": overdue, "due_soon": due_soon, "later": later,
        "total_count": len(pos),
    }



@router.post("/{po_id}/mark-reviewed")
async def mark_po_reviewed(po_id: str, data: dict, user=Depends(get_current_user)):
    """Mark a PO's receipts as reviewed. Requires admin PIN, manager PIN, or TOTP."""
    po = await db.purchase_orders.find_one({"id": po_id}, {"_id": 0})
    if not po:
        raise HTTPException(status_code=404, detail="PO not found")

    pin = str(data.get("pin", ""))
    if not pin:
        raise HTTPException(status_code=400, detail="Admin PIN or TOTP required")

    # Use policy-aware PIN resolver
    from routes.verify import verify_pin_for_action
    verifier = await verify_pin_for_action(pin, "po_mark_reviewed")
    if not verifier:
        raise HTTPException(status_code=403, detail="Invalid PIN or TOTP")

    review_notes = data.get("notes", "")
    await db.purchase_orders.update_one({"id": po_id}, {"$set": {
        "receipt_review_status": "reviewed",
        "receipt_reviewed_by_id": verifier["verifier_id"],
        "receipt_reviewed_by_name": verifier["verifier_name"],
        "receipt_reviewed_at": now_iso(),
        "receipt_review_notes": review_notes,
    }})

    return {
        "message": f"PO {po.get('po_number')} receipts marked as reviewed",
        "reviewed_by": verifier["verifier_name"],
    }


@router.get("/payables-by-supplier")
async def get_payables_by_supplier(user=Depends(get_current_user), branch_id: Optional[str] = None):
    """Get unpaid POs grouped by supplier for Pay Supplier page."""
    query = {"payment_status": {"$in": ["unpaid", "partial"]}, "status": {"$ne": "cancelled"}}
    if branch_id:
        query["branch_id"] = branch_id
    pos = await db.purchase_orders.find(query, {"_id": 0}).sort("due_date", 1).to_list(1000)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    by_vendor: dict = {}
    for po in pos:
        v = po["vendor"]
        if v not in by_vendor:
            by_vendor[v] = {"vendor": v, "total_owed": 0, "pos": [], "has_overdue": False}
        balance = po.get("balance", po.get("grand_total", po.get("subtotal", 0)))
        by_vendor[v]["total_owed"] = round(by_vendor[v]["total_owed"] + balance, 2)
        by_vendor[v]["pos"].append(po)
        due = po.get("due_date", "")
        if due and due < today:
            by_vendor[v]["has_overdue"] = True

    return sorted(by_vendor.values(), key=lambda x: (not x["has_overdue"], x["vendor"]))


@router.post("/{po_id}/reopen")
async def reopen_purchase_order(po_id: str, data: dict = None, user=Depends(get_current_user)):
    """
    Reopen a received PO: reverses inventory AND fully reverses the payment.
    """
    check_perm(user, "purchase_orders", "edit")

    # PIN enforcement for PO reopen (mandatory)
    pin = (data or {}).get("pin", "")
    if not pin:
        raise HTTPException(status_code=400, detail="PIN is required to reopen a PO")
    from routes.verify import verify_pin_for_action
    verifier = await verify_pin_for_action(pin, "reopen_po")
    if not verifier:
        raise HTTPException(status_code=403, detail="Invalid PIN")

    po = await db.purchase_orders.find_one({"id": po_id}, {"_id": 0})
    if not po:
        raise HTTPException(status_code=404, detail="PO not found")
    if po["status"] != "received":
        raise HTTPException(status_code=400, detail="Only received POs can be reopened")

    branch_id = po.get("branch_id", "")
    po_type = po.get("po_type", po.get("payment_method", ""))
    is_cash = po_type == "cash" or po.get("payment_method") == "cash"
    is_terms = po_type in ("terms", "credit") or po.get("payment_method") == "credit"
    fund_source = po.get("fund_source", "cashier")
    amount_paid = float(po.get("amount_paid", 0))
    grand_total = float(po.get("grand_total", po.get("subtotal", 0)))

    # ── 1. Reverse inventory ─────────────────────────────────────────────────
    for item in po.get("items", []):
        pid = item.get("product_id", "")
        if not pid:
            continue
        qty = float(item.get("quantity", 0))
        await db.inventory.update_one(
            {"product_id": pid, "branch_id": branch_id},
            {"$inc": {"quantity": -qty}, "$set": {"updated_at": now_iso()}}
        )
        await log_movement(
            pid, branch_id, "po_reopen", -qty, po["id"], po["po_number"],
            item.get("unit_price", 0), user["id"], user.get("full_name", user["username"]),
            f"PO reopened for correction — {po['vendor']}"
        )

    # ── 2. Reverse cash payment ───────────────────────────────────────────────
    if is_cash and amount_paid > 0:
        ref_text = f"Reversal — PO {po['po_number']} reopened — {po['vendor']}"

        if fund_source == "safe":
            safe_wallet = await db.fund_wallets.find_one(
                {"branch_id": branch_id, "type": "safe", "active": True}, {"_id": 0}
            )
            if safe_wallet:
                await db.safe_lots.insert_one({
                    "id": new_id(), "branch_id": branch_id,
                    "wallet_id": safe_wallet["id"],
                    "date_received": now_iso()[:10],
                    "original_amount": amount_paid,
                    "remaining_amount": amount_paid,
                    "source_reference": ref_text,
                    "created_by": user["id"],
                    "created_at": now_iso(),
                })
        else:
            await update_cashier_wallet(branch_id, amount_paid, ref_text)

        # Void original expense records and create an audit reversal entry
        await db.expenses.update_many(
            {"po_id": po_id, "category": "Purchase Payment", "voided": {"$ne": True}},
            {"$set": {
                "voided": True, "voided_at": now_iso(),
                "void_reason": "PO reopened for correction",
                "voided_by": user.get("full_name", user["username"]),
            }}
        )
        await db.expenses.insert_one({
            "id": new_id(), "branch_id": branch_id,
            "category": "Purchase Payment",
            "description": f"REVERSAL — PO {po['po_number']} — {po['vendor']}",
            "notes": f"PO reopened for correction. ₱{amount_paid:,.2f} returned to {fund_source}.",
            "amount": -amount_paid,  # negative = fund returned
            "payment_method": po.get("payment_method_detail", "Cash"),
            "fund_source": fund_source,
            "reference_number": po["po_number"],
            "date": now_iso()[:10],
            "po_id": po_id,
            "is_reversal": True,
            "created_by": user["id"],
            "created_by_name": user.get("full_name", user["username"]),
            "created_at": now_iso(),
        })

    # ── 3. Void accounts payable for Terms POs ───────────────────────────────
    if is_terms:
        await db.payables.update_many(
            {"po_id": po_id, "status": {"$ne": "voided"}},
            {"$set": {
                "status": "voided",
                "voided_at": now_iso(),
                "void_reason": "PO reopened for correction",
                "voided_by": user.get("full_name", user["username"]),
            }}
        )

    # ── 4. Reset PO — fresh start for re-receive ─────────────────────────────
    await db.purchase_orders.update_one({"id": po_id}, {"$set": {
        "status": "ordered",
        "received_date": None,
        "reopened_by": user.get("full_name", user["username"]),
        "reopened_at": now_iso(),
        # Full payment reversal — must pay again on re-receive
        "payment_status": "unpaid",
        "amount_paid": 0,
        "balance": grand_total,
    }})

    msg_parts = ["PO reopened. Inventory reversed."]
    if is_cash and amount_paid > 0:
        msg_parts.append(f"₱{amount_paid:,.2f} returned to {fund_source}.")
    if is_terms:
        msg_parts.append("Accounts payable voided.")
    msg_parts.append("Edit and receive again to finalise.")
    return {"message": " ".join(msg_parts)}


@router.get("/by-vendor")
async def get_vendor_pos(vendor: str, user=Depends(get_current_user)):
    """Get all POs for a vendor, unpaid ones first."""
    pos = await db.purchase_orders.find(
        {"vendor": vendor, "status": {"$ne": "cancelled"}}, {"_id": 0}
    ).sort("created_at", -1).to_list(500)
    return pos


@router.get("/{po_id}")
async def get_purchase_order(po_id: str, user=Depends(get_current_user)):
    """Get a single purchase order by ID."""
    po = await db.purchase_orders.find_one({"id": po_id}, {"_id": 0})
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found")
    return po


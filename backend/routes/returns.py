"""
Customer Return & Refund routes.
Handles the complete return workflow: product receipt, condition check,
inventory action (return to shelf / pull out as loss), and cash refund.
"""
from fastapi import APIRouter, Depends, HTTPException
from typing import Optional
from datetime import datetime, timezone
from config import db
from utils import (
    get_current_user, check_perm, now_iso, new_id,
    log_movement, update_cashier_wallet, today_local,
)

router = APIRouter(prefix="/returns", tags=["Returns"])


@router.get("")
async def list_returns(
    user=Depends(get_current_user),
    branch_id: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
):
    """List return transactions with filters."""
    query = {}
    if branch_id:
        query["branch_id"] = branch_id
    elif user.get("branch_id"):
        query["branch_id"] = user["branch_id"]
    if date_from:
        query["return_date"] = {"$gte": date_from}
    if date_to:
        query.setdefault("return_date", {})["$lte"] = date_to

    total = await db.returns.count_documents(query)
    items = await db.returns.find(query, {"_id": 0}).sort("created_at", -1).skip(skip).limit(limit).to_list(limit)
    return {"returns": items, "total": total}


@router.get("/{return_id}")
async def get_return(return_id: str, user=Depends(get_current_user)):
    """Get a single return transaction by ID."""
    ret = await db.returns.find_one({"id": return_id}, {"_id": 0})
    if not ret:
        raise HTTPException(status_code=404, detail="Return not found")
    return ret


@router.post("")
async def create_return(data: dict, user=Depends(get_current_user)):
    """
    Process a complete customer return transaction.

    data fields:
      branch_id, return_date,
      customer_name, customer_type (walkin | credit),
      reason, invoice_number (optional), notes,
      items: [{product_id, product_name, sku, category, unit, quantity, condition,
               inventory_action (shelf | pullout), refund_price, cost_price}],
      refund_method (full | partial | none),
      refund_amount,
      fund_source (cashier | safe),
      cashier_id, cashier_name
    """
    check_perm(user, "pos", "sell")

    branch_id = data.get("branch_id", user.get("branch_id", ""))
    if not branch_id:
        raise HTTPException(status_code=400, detail="Branch ID required")

    return_date = data.get("return_date") or await today_local(user.get("organization_id") or "")
    items = data.get("items", [])
    if not items:
        raise HTTPException(status_code=400, detail="No items in return")

    refund_amount = float(data.get("refund_amount", 0))
    fund_source = data.get("fund_source", "cashier")

    # ── Validate fund balance if issuing refund ────────────────────────────
    if refund_amount > 0:
        cashier_wallet = await db.fund_wallets.find_one(
            {"branch_id": branch_id, "type": "cashier", "active": True}, {"_id": 0}
        )
        cashier_balance = float(cashier_wallet.get("balance", 0)) if cashier_wallet else 0.0

        safe_wallet = await db.fund_wallets.find_one(
            {"branch_id": branch_id, "type": "safe", "active": True}, {"_id": 0}
        )
        safe_balance = 0.0
        if safe_wallet:
            lots = await db.safe_lots.find(
                {"wallet_id": safe_wallet["id"], "remaining_amount": {"$gt": 0}}, {"_id": 0}
            ).to_list(500)
            safe_balance = sum(lot["remaining_amount"] for lot in lots)

        if fund_source == "safe" and safe_balance < refund_amount:
            raise HTTPException(status_code=400, detail=f"Safe has ₱{safe_balance:.2f}, need ₱{refund_amount:.2f}")
        if fund_source == "cashier" and cashier_balance < refund_amount:
            raise HTTPException(status_code=400, detail=f"Cashier has ₱{cashier_balance:.2f}, need ₱{refund_amount:.2f}")

    # ── C-8 (Audit 2026-02): atomic org+branch-scoped RMA number ──────────
    # Replaces the previous `count_documents({})+1` pattern which produced
    # duplicate RMAs under concurrent return creation and leaked numbering
    # across tenants. Counter is keyed by (org, branch, date).
    from utils.numbering import generate_next_rma_number
    rma_number = await generate_next_rma_number(
        branch_id,
        user.get("organization_id") or user.get("org_id") or "",
    )

    # ── Process each item ──────────────────────────────────────────────────
    processed_items = []
    total_loss_value = 0.0
    total_refund_retail = 0.0
    pulled_out_items = []

    for item in items:
        product_id = item.get("product_id", "")
        qty = float(item.get("quantity", 0))
        condition = item.get("condition", "sellable")
        inventory_action = item.get("inventory_action", "shelf")
        cost_price = float(item.get("cost_price", 0))
        refund_price = float(item.get("refund_price", 0))
        category = item.get("category", "")

        # Veterinary items MUST be pulled out
        if category.lower() == "veterinary":
            inventory_action = "pullout"

        item_loss_value = cost_price * qty
        total_refund_retail += refund_price * qty

        if inventory_action == "shelf" and qty > 0 and product_id:
            # Return to shelf: add back to inventory
            await db.inventory.update_one(
                {"product_id": product_id, "branch_id": branch_id},
                {"$inc": {"quantity": qty}, "$set": {"updated_at": now_iso()}},
                upsert=True
            )
            await log_movement(
                product_id, branch_id, "return_to_shelf", qty,
                "", rma_number, cost_price,
                user["id"], user.get("full_name", user["username"]),
                f"Customer return — {data.get('reason', '')} — {rma_number}"
            )
        elif inventory_action == "pullout" and qty > 0 and product_id:
            # Pull out: do NOT add back to inventory, log as loss
            total_loss_value += item_loss_value
            pulled_out_items.append(item)
            await log_movement(
                product_id, branch_id, "return_pullout", -0,  # qty 0 = informational
                "", rma_number, cost_price,
                user["id"], user.get("full_name", user["username"]),
                f"Customer return PULL OUT — {condition} — {data.get('reason', '')} — {rma_number}"
            )
            # Record in inventory_corrections for audit
            current_inv = await db.inventory.find_one(
                {"product_id": product_id, "branch_id": branch_id}, {"_id": 0}
            )
            await db.inventory_corrections.insert_one({
                "id": new_id(),
                "product_id": product_id,
                "product_name": item.get("product_name", ""),
                "branch_id": branch_id,
                "old_qty": current_inv.get("quantity", 0) if current_inv else 0,
                "new_qty": current_inv.get("quantity", 0) if current_inv else 0,  # no change
                "qty_pulled_out": qty,
                "reason": f"Customer return pull-out: {condition} — {data.get('reason', '')}",
                "rma_number": rma_number,
                "loss_value": round(item_loss_value, 2),
                "corrected_by": user["id"],
                "corrected_by_name": user.get("full_name", user["username"]),
                "created_at": now_iso(),
                "type": "customer_return_pullout",
            })

        processed_items.append({
            **item,
            "inventory_action": inventory_action,
            "loss_value": round(item_loss_value, 2) if inventory_action == "pullout" else 0,
        })

    # ── Credit customer AR reduction (Fix #1) ───────────────────────────────
    # If the customer is on credit, the return reduces their Accounts Receivable.
    # Semantics:
    #   total_return_value = retail value of goods returned (sum of refund_price × qty)
    #   cash_refunded      = refund_amount (cash given back now)
    #   credit_applied     = max(0, total_return_value - cash_refunded)
    # `credit_applied` is subtracted from:
    #   (a) customer.balance (AR ledger)
    #   (b) original invoice.balance (if invoice_number provided) — applied to newest
    #       open invoice for that customer, falling back if the linked invoice is paid/voided
    customer_id = data.get("customer_id")
    customer_type = (data.get("customer_type") or "walkin").lower()
    credit_applied = 0.0
    credit_applied_to_invoices = []
    if customer_type == "credit" and customer_id:
        credit_applied = round(max(0, total_refund_retail - refund_amount), 2)
        if credit_applied > 0:
            # Resolve the target customer
            cust = await db.customers.find_one({"id": customer_id}, {"_id": 0})
            if cust:
                # ── Phase 2C.3 (Audit 2026-02 H-4): require explicit invoice link.
                # Old behaviour silently fell back to the OLDEST open invoice for
                # this customer, which could land credit on a completely
                # unrelated transaction. We now require the caller to either:
                #   1. Pass `invoice_number` of an open invoice this credit
                #      should reduce, OR
                #   2. Refund the full retail value as cash (so credit_applied
                #      becomes 0 and this branch is skipped), OR
                #   3. Mark the credit `pending_review` for an admin to assign.
                remaining_to_apply = credit_applied
                target_invs = []
                linked_inv_num = (data.get("invoice_number") or "").strip()
                allow_pending = bool(data.get("allow_pending_credit"))

                if linked_inv_num:
                    linked = await db.invoices.find_one(
                        {"invoice_number": linked_inv_num, "customer_id": customer_id,
                         "balance": {"$gt": 0},
                         "status": {"$nin": ["voided", "paid", "cancelled",
                                              "cancelled_draft", "deleted",
                                              "for_preparation", "error_partial_write"]}},
                        {"_id": 0, "id": 1, "invoice_number": 1, "balance": 1}
                    )
                    if not linked:
                        raise HTTPException(
                            status_code=400,
                            detail=(
                                f"Return credit cannot be applied: invoice "
                                f"'{linked_inv_num}' is not an open AR invoice "
                                f"for this customer. Provide a different open "
                                f"invoice_number, refund as cash, or set "
                                f"allow_pending_credit=true to mark for review."
                            ),
                        )
                    target_invs.append(linked)
                elif not allow_pending:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            "Return credit requires invoice_number for credit "
                            "customers. Provide the original invoice this credit "
                            "should reduce, refund as cash, or set "
                            "allow_pending_credit=true to mark for admin review."
                        ),
                    )

                for tgt in target_invs:
                    if remaining_to_apply <= 0:
                        break
                    apply = round(min(remaining_to_apply, float(tgt["balance"])), 2)
                    if apply <= 0:
                        continue
                    new_bal = round(float(tgt["balance"]) - apply, 2)
                    new_status = "paid" if new_bal <= 0.005 else "partial"
                    await db.invoices.update_one(
                        {"id": tgt["id"]},
                        {"$inc": {"balance": -apply, "amount_paid": apply},
                         "$set": {"status": new_status, "updated_at": now_iso()},
                         "$push": {"payments": {
                             "id": new_id(),
                             "amount": apply,
                             "method": "Return Credit",
                             "fund_source": "return_credit",
                             "reference": rma_number,
                             "date": return_date,
                             "recorded_by": user["id"],
                             "recorded_by_name": user.get("full_name", user.get("username", "")),
                             "created_at": now_iso(),
                             "note": f"Credit applied from return {rma_number}",
                         }}}
                    )
                    credit_applied_to_invoices.append({
                        "invoice_id": tgt["id"],
                        "invoice_number": tgt["invoice_number"],
                        "amount": apply,
                    })
                    remaining_to_apply -= apply

                # Reduce the customer's overall balance by the amount actually applied
                applied_total = round(credit_applied - max(0, remaining_to_apply), 2)
                if applied_total > 0:
                    await db.customers.update_one(
                        {"id": customer_id},
                        {"$inc": {"balance": -applied_total}}
                    )
                # If pending or excess remains, log to admins for manual handling.
                if remaining_to_apply > 0.005:
                    await db.notifications.insert_one({
                        "id": new_id(),
                        "type": "return_overcredit"
                                if not allow_pending else "return_pending_credit",
                        "title": (
                            "Customer return exceeded AR balance"
                            if not allow_pending
                            else "Return credit pending admin assignment"
                        ),
                        "message": (
                            f"Customer {cust.get('name','')} returned ₱{credit_applied:.2f} worth of goods "
                            f"but only ₱{applied_total:.2f} was applied. "
                            f"Pending ₱{remaining_to_apply:.2f} — assign to a specific open invoice or refund as cash."
                        ),
                        "branch_id": branch_id,
                        "metadata": {
                            "rma_number": rma_number,
                            "pending_amount": round(remaining_to_apply, 2),
                            "customer_id": customer_id,
                        },
                        "target_user_ids": [
                            a["id"] for a in await db.users.find(
                                {"role": "admin", "active": True}, {"_id": 0, "id": 1}
                            ).to_list(50)
                        ],
                        "read_by": [],
                        "created_at": now_iso(),
                    })

    # ── Record refund as expense ────────────────────────────────────────────
    if refund_amount > 0:
        ref_text = f"Customer Return Refund — {rma_number} — {data.get('customer_name', 'Walk-in')} — {data.get('reason', '')}"
        if fund_source == "safe":
            safe_wallet = await db.fund_wallets.find_one(
                {"branch_id": branch_id, "type": "safe", "active": True}, {"_id": 0}
            )
            if safe_wallet:
                remaining = refund_amount
                for lot in await db.safe_lots.find(
                    {"wallet_id": safe_wallet["id"], "remaining_amount": {"$gt": 0}}, {"_id": 0}
                ).sort("remaining_amount", -1).to_list(500):
                    if remaining <= 0: break
                    take = min(lot["remaining_amount"], remaining)
                    await db.safe_lots.update_one({"id": lot["id"]}, {"$inc": {"remaining_amount": -take}})
                    remaining -= take
        else:
            await update_cashier_wallet(branch_id, -refund_amount, ref_text)

        await db.expenses.insert_one({
            "id": new_id(),
            "branch_id": branch_id,
            "category": "Customer Return Refund",
            "description": f"Refund — {rma_number} — {data.get('customer_name', 'Walk-in')}",
            "notes": (
                f"Reason: {data.get('reason', '')} | "
                f"Items: {', '.join(i.get('product_name','') for i in items)} | "
                f"Invoice: {data.get('invoice_number', 'N/A')}"
            ),
            "amount": refund_amount,
            "payment_method": "Cash",
            "fund_source": fund_source,
            "reference_number": rma_number,
            "date": return_date,
            "rma_number": rma_number,
            "created_by": user["id"],
            "created_by_name": user.get("full_name", user["username"]),
            "created_at": now_iso(),
        })

    # ── Notify owner of pull-out losses ────────────────────────────────────
    if pulled_out_items and total_loss_value > 0:
        admins = await db.users.find(
            {"role": "admin", "active": True}, {"_id": 0, "id": 1}
        ).to_list(50)
        branch_doc = await db.branches.find_one({"id": branch_id}, {"_id": 0, "name": 1})
        branch_name = branch_doc.get("name", branch_id) if branch_doc else branch_id

        await db.notifications.insert_one({
            "id": new_id(),
            "type": "return_pullout_loss",
            "title": f"Stock Loss — Customer Return Pull-Out",
            "message": (
                f"{branch_name}: {len(pulled_out_items)} item(s) pulled out from customer return {rma_number}. "
                f"Loss value: ₱{total_loss_value:.2f}. "
                f"Reason: {data.get('reason', 'N/A')}. "
                f"Processed by: {user.get('full_name', user['username'])}"
            ),
            "branch_id": branch_id,
            "branch_name": branch_name,
            "metadata": {
                "rma_number": rma_number,
                "loss_value": round(total_loss_value, 2),
                "items": pulled_out_items,
                "reason": data.get("reason", ""),
            },
            "target_user_ids": [a["id"] for a in admins],
            "read_by": [],
            "created_at": now_iso(),
        })

    # ── Save return record ─────────────────────────────────────────────────
    return_doc = {
        "id": new_id(),
        "rma_number": rma_number,
        "branch_id": branch_id,
        "return_date": return_date,
        "customer_name": data.get("customer_name", "Walk-in"),
        "customer_id": data.get("customer_id", ""),
        "customer_type": data.get("customer_type", "walkin"),
        "reason": data.get("reason", ""),
        "invoice_number": data.get("invoice_number", ""),
        "notes": data.get("notes", ""),
        "items": processed_items,
        "refund_method": data.get("refund_method", "full"),
        "refund_amount": refund_amount,
        "fund_source": fund_source if refund_amount > 0 else "",
        "total_loss_value": round(total_loss_value, 2),
        "has_pullout": len(pulled_out_items) > 0,
        "credit_applied": round(credit_applied, 2),
        "credit_applied_to_invoices": credit_applied_to_invoices,
        "status": "completed",
        "processed_by": user["id"],
        "processed_by_name": user.get("full_name", user["username"]),
        "created_at": now_iso(),
    }
    await db.returns.insert_one(return_doc)
    del return_doc["_id"]

    # Iter 244 — notify customer (credit/known customers only) that their return was processed
    if return_doc.get("customer_id"):
        try:
            from routes.sms_hooks import on_refund_processed
            await on_refund_processed(return_doc)
        except Exception as e:
            import logging
            logging.getLogger("sms").error(f"on_refund_processed dispatch failed: {e}")

    return {
        **return_doc,
        "message": f"Return {rma_number} processed successfully",
    }


@router.post("/{return_id}/void")
async def void_return(return_id: str, data: dict, user=Depends(get_current_user)):
    """
    Void a processed return. Requires manager PIN.
    Reverses: inventory (takes back items that were restocked), refund (re-deducts from fund).
    Pull-out items cannot be physically un-pulled, so inventory is not restored for those.
    """
    from utils import verify_password

    check_perm(user, "pos", "void")

    ret = await db.returns.find_one({"id": return_id}, {"_id": 0})
    if not ret:
        raise HTTPException(status_code=404, detail="Return not found")
    if ret.get("voided"):
        raise HTTPException(status_code=400, detail="Return is already voided")

    # Verify manager PIN
    manager_pin = data.get("manager_pin", "")
    reason = data.get("reason", "Return voided")
    if not manager_pin:
        raise HTTPException(status_code=400, detail="Manager PIN required")

    from routes.verify import verify_pin_for_action
    verifier = await verify_pin_for_action(manager_pin, "void_return")
    if not verifier:
        raise HTTPException(status_code=403, detail="Invalid manager PIN")
    authorized_manager = {"id": verifier["verifier_id"], "full_name": verifier["verifier_name"], "username": verifier["verifier_name"]}

    branch_id = ret.get("branch_id", "")

    # Reverse inventory: take back shelf-restocked items
    for item in ret.get("items", []):
        if item.get("inventory_action") == "shelf" and item.get("product_id"):
            qty = float(item.get("quantity", 0))
            await db.inventory.update_one(
                {"product_id": item["product_id"], "branch_id": branch_id},
                {"$inc": {"quantity": -qty}, "$set": {"updated_at": now_iso()}},
                upsert=True,
            )
            from utils import log_movement
            await log_movement(
                item["product_id"], branch_id, "return_void", -qty,
                return_id, ret["rma_number"], 0,
                user["id"], user.get("full_name", user["username"]),
                f"Return voided: {ret['rma_number']} — {reason}",
            )

    # Reverse refund: ADD money BACK to the original fund source
    # (The original return took money OUT to refund the customer; voiding means that
    #  refund is reversed, so money comes back into the fund.)
    refund_amount = float(ret.get("refund_amount", 0))
    fund_source = ret.get("fund_source", "cashier")
    if refund_amount > 0:
        ref_text = f"Return void — refund reversed — {ret['rma_number']}"
        if fund_source == "safe":
            safe_wallet = await db.fund_wallets.find_one(
                {"branch_id": branch_id, "type": "safe", "active": True}, {"_id": 0}
            )
            if safe_wallet:
                await db.safe_lots.insert_one({
                    "id": new_id(), "branch_id": branch_id,
                    "wallet_id": safe_wallet["id"],
                    "date_received": await today_local(user.get("organization_id") or ""),
                    "original_amount": refund_amount,
                    "remaining_amount": refund_amount,
                    "source_reference": ref_text,
                    "created_by": user["id"],
                    "created_at": now_iso(),
                })
        else:
            await update_cashier_wallet(branch_id, refund_amount, ref_text)

        # Void the expense record for the refund
        await db.expenses.update_many(
            {"rma_number": ret["rma_number"], "voided": {"$ne": True}},
            {"$set": {"voided": True, "voided_at": now_iso(), "void_reason": reason,
                      "voided_by": user.get("full_name", user["username"])}}
        )

    # Reverse credit applied to customer AR (Fix #1)
    credit_applied = float(ret.get("credit_applied", 0) or 0)
    applied_invs = ret.get("credit_applied_to_invoices", []) or []
    if credit_applied > 0 and ret.get("customer_id"):
        # Re-increase each invoice balance that was reduced
        for inv_entry in applied_invs:
            amt = float(inv_entry.get("amount", 0) or 0)
            if amt <= 0:
                continue
            # Remove the specific Return Credit payment row and re-inc balance
            await db.invoices.update_one(
                {"id": inv_entry["invoice_id"]},
                {"$inc": {"balance": amt, "amount_paid": -amt},
                 "$set": {"updated_at": now_iso()},
                 "$pull": {"payments": {"reference": ret["rma_number"], "fund_source": "return_credit"}}}
            )
            # Recompute status after re-inc
            inv_now = await db.invoices.find_one(
                {"id": inv_entry["invoice_id"]},
                {"_id": 0, "balance": 1, "amount_paid": 1, "grand_total": 1}
            )
            if inv_now:
                new_status = "paid" if float(inv_now.get("balance", 0)) <= 0.005 \
                    else "partial" if float(inv_now.get("amount_paid", 0)) > 0 else "open"
                await db.invoices.update_one(
                    {"id": inv_entry["invoice_id"]}, {"$set": {"status": new_status}}
                )
        # Re-increase customer balance
        applied_total = round(sum(float(i.get("amount", 0)) for i in applied_invs), 2)
        if applied_total > 0:
            await db.customers.update_one(
                {"id": ret["customer_id"]},
                {"$inc": {"balance": applied_total}}
            )

    await db.returns.update_one(
        {"id": return_id},
        {"$set": {
            "voided": True, "voided_at": now_iso(),
            "void_reason": reason,
            "voided_by": user.get("full_name", user["username"]),
            "void_authorized_by": authorized_manager.get("full_name", authorized_manager["username"]),
        }}
    )
    return {
        "message": f"Return {ret['rma_number']} voided. Inventory reversed for shelf items. ₱{refund_amount:,.2f} returned to {fund_source}.",
        "authorized_by": authorized_manager.get("full_name", authorized_manager["username"]),
    }

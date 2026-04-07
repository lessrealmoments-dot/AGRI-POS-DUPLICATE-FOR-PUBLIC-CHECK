"""
Incomplete Stock Correction Endpoint

Allows correcting invoice when items were printed on receipt but not physically given.
Updates original invoice, returns stock to shelves, refunds money, creates audit trail.
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timezone
from utils import get_current_user
import os

router = APIRouter()

# Models
class IncompleteStockItem(BaseModel):
    product_id: str
    product_name: str
    original_qty: float
    actual_qty: float
    rate: float
    unit: str = "unit"

class IncompleteStockCorrection(BaseModel):
    items: List[IncompleteStockItem]
    manager_pin: str
    reprint_receipt: bool = False
    notes: str = ""


def new_id():
    from uuid import uuid4
    return str(uuid4())


def now_iso():
    return datetime.now(timezone.utc).isoformat()


async def update_cashier_wallet(db, branch_id: str, amount: float, reference: str, allow_negative: bool = False):
    """Update cashier wallet balance"""
    wallet = await db.fund_wallets.find_one({"branch_id": branch_id, "type": "cashier"}, {"_id": 0})
    if not wallet:
        raise HTTPException(400, "Cashier wallet not found")
    
    new_balance = (wallet.get("balance", 0) or 0) + amount
    
    if not allow_negative and new_balance < 0:
        raise HTTPException(400, f"Insufficient cashier balance. Available: ₱{wallet.get('balance', 0):,.2f}")
    
    await db.fund_wallets.update_one(
        {"id": wallet["id"]},
        {"$set": {"balance": new_balance, "updated_at": now_iso()}}
    )
    
    # Log movement
    await db.wallet_movements.insert_one({
        "id": new_id(),
        "wallet_id": wallet["id"],
        "branch_id": branch_id,
        "type": "cashier",
        "amount": amount,
        "balance_after": new_balance,
        "reference": reference,
        "created_at": now_iso()
    })


async def log_inventory_movement(
    db, product_id: str, branch_id: str, movement_type: str,
    quantity: float, from_loc: str, to_loc: str, cost_price: float,
    user_id: str, user_name: str, notes: str
):
    """Log inventory movement for audit trail"""
    await db.inventory_movements.insert_one({
        "id": new_id(),
        "product_id": product_id,
        "branch_id": branch_id,
        "movement_type": movement_type,
        "quantity": quantity,
        "from_location": from_loc,
        "to_location": to_loc,
        "cost_price": cost_price,
        "user_id": user_id,
        "user_name": user_name,
        "notes": notes,
        "created_at": now_iso()
    })


@router.post("/invoices/{invoice_id}/correct-incomplete-stock")
async def correct_incomplete_stock(
    invoice_id: str,
    data: IncompleteStockCorrection
):
    """
    Correct invoice when items weren't physically given.
    Updates receipt, returns stock, refunds money, creates audit trail.
    """
    from config import db
    from routes.verify import verify_pin_for_action
    
    # Get user from PIN verification (no auth token required, PIN is enough)
    user = {"id": "terminal", "full_name": "Terminal User", "email": "terminal@system"}
    
    # 1. Fetch invoice
    invoice = await db.invoices.find_one({"id": invoice_id}, {"_id": 0})
    if not invoice:
        raise HTTPException(404, "Invoice not found")
    
    # 2. Check if day is closed
    closed_doc = await db.daily_closings.find_one({
        "branch_id": invoice["branch_id"],
        "date": invoice.get("order_date"),
        "status": "closed"
    })
    
    if closed_doc:
        raise HTTPException(
            400,
            f"Cannot update receipt - day {invoice.get('order_date')} is already closed. "
            f"Please use Return & Refund instead."
        )
    
    # 3. Verify Manager PIN
    verifier = await verify_pin_for_action(data.manager_pin, "manager", branch_id=invoice["branch_id"])
    if not verifier:
        # Log failed attempt
        await db.pin_attempts.insert_one({
            "id": new_id(),
            "action": "correct_incomplete_stock",
            "invoice_id": invoice_id,
            "success": False,
            "created_at": now_iso()
        })
        raise HTTPException(403, "Invalid PIN. Authorization failed.")
    
    # 4. Calculate differences and build corrected items
    corrected_items = []
    items_to_return = []
    refund_amount = 0.0
    
    # Create a map of corrections by product_id
    corrections_map = {item.product_id: item for item in data.items}
    
    for original_item in invoice.get("items", []):
        product_id = original_item.get("product_id", "")
        
        if product_id in corrections_map:
            correction = corrections_map[product_id]
            orig_qty = float(correction.original_qty)
            actual_qty = float(correction.actual_qty)
            diff_qty = orig_qty - actual_qty
            rate = float(correction.rate)
            
            if diff_qty > 0:  # Some items not given
                refund_amount += diff_qty * rate
                items_to_return.append({
                    "product_id": product_id,
                    "product_name": correction.product_name,
                    "quantity": diff_qty,
                    "rate": rate,
                    "unit": correction.unit
                })
            
            # Build corrected item
            corrected_items.append({
                **original_item,
                "quantity": actual_qty,
                "total": actual_qty * rate
            })
        else:
            # Item not in correction list, keep original
            corrected_items.append(original_item)
    
    # Calculate new totals
    new_subtotal = sum(item.get("total", 0) for item in corrected_items)
    discount = invoice.get("overall_discount", 0) or 0
    freight = invoice.get("freight", 0) or 0
    new_grand_total = new_subtotal - discount + freight
    
    # Calculate new balance
    amount_paid = invoice.get("amount_paid", 0) or 0
    new_balance = max(0, new_grand_total - amount_paid)
    
    # 5. Create correction audit record BEFORE updating invoice
    correction_id = new_id()
    correction_record = {
        "id": correction_id,
        "invoice_id": invoice["id"],
        "invoice_number": invoice.get("invoice_number", ""),
        "correction_type": "incomplete_stock",
        "branch_id": invoice["branch_id"],
        "customer_id": invoice.get("customer_id"),
        "customer_name": invoice.get("customer_name"),
        "order_date": invoice.get("order_date"),
        "original_items": invoice.get("items", []),
        "corrected_items": corrected_items,
        "items_returned_to_shelf": items_to_return,
        "original_subtotal": invoice.get("subtotal", 0),
        "original_grand_total": invoice.get("grand_total", 0),
        "corrected_subtotal": new_subtotal,
        "corrected_grand_total": new_grand_total,
        "refund_amount": refund_amount,
        "corrected_by_id": user.get("id"),
        "corrected_by_name": user.get("full_name", ""),
        "authorized_by": verifier.get("verifier_name", ""),
        "manager_pin_verified": True,
        "notes": data.notes,
        "created_at": now_iso()
    }
    
    await db.invoice_corrections.insert_one(correction_record)
    
    # 6. Return stock to shelves
    for item in items_to_return:
        # Increment inventory
        await db.inventory.update_one(
            {"product_id": item["product_id"], "branch_id": invoice["branch_id"]},
            {
                "$inc": {"quantity": item["quantity"]},
                "$set": {"updated_at": now_iso()}
            },
            upsert=True
        )
        
        # Log movement
        await log_inventory_movement(
            db,
            item["product_id"],
            invoice["branch_id"],
            "incomplete_stock_return",
            item["quantity"],
            "",
            invoice.get("invoice_number", ""),
            0,
            user.get("id"),
            user.get("full_name", ""),
            f"Receipt correction - items not given - {invoice.get('invoice_number', '')}"
        )
    
    # 7. Update invoice
    await db.invoices.update_one(
        {"id": invoice_id},
        {"$set": {
            "items": corrected_items,
            "subtotal": new_subtotal,
            "grand_total": new_grand_total,
            "balance": new_balance,
            "correction_applied": True,
            "correction_id": correction_id,
            "updated_at": now_iso(),
            "updated_by": user.get("id")
        }}
    )
    
    # 8. Refund money from cashier wallet
    if refund_amount > 0:
        ref_text = (
            f"Refund incomplete stock - {invoice.get('invoice_number', '')} - "
            f"{invoice.get('customer_name', 'Walk-in')} - "
            f"{len(items_to_return)} items not given"
        )
        
        await update_cashier_wallet(
            db,
            invoice["branch_id"],
            -refund_amount,
            ref_text,
            allow_negative=False
        )
        
        # Record as expense for Z-report
        await db.expenses.insert_one({
            "id": new_id(),
            "branch_id": invoice["branch_id"],
            "category": "Customer Return Refund",
            "description": f"Incomplete stock refund - {invoice.get('invoice_number', '')}",
            "notes": data.notes or "Receipt corrected for items not physically given",
            "amount": refund_amount,
            "payment_method": "Cash",
            "fund_source": "cashier",
            "reference_number": invoice.get("invoice_number", ""),
            "date": now_iso()[:10],
            "invoice_id": invoice_id,
            "correction_id": correction_id,
            "created_by": user.get("id"),
            "created_by_name": user.get("full_name", ""),
            "created_at": now_iso(),
            "voided": False
        })
    
    # 9. Update customer balance (if credit customer)
    if invoice.get("customer_id") and refund_amount > 0:
        await db.customers.update_one(
            {"id": invoice["customer_id"]},
            {"$inc": {"balance": -refund_amount}}
        )
    
    # 10. Send SMS notification (if credit customer)
    if invoice.get("customer_id") and refund_amount > 0:
        try:
            from routes.sms_hooks import on_payment_received
            
            customer = await db.customers.find_one(
                {"id": invoice["customer_id"]}, {"_id": 0}
            )
            
            if customer:
                await on_payment_received(
                    invoice["customer_id"],
                    refund_amount,
                    "Cash",
                    f"Receipt correction - {invoice.get('invoice_number', '')}",
                    customer.get("balance", 0),
                    invoice["branch_id"],
                    db
                )
        except Exception as e:
            # Don't fail the whole operation if SMS fails
            print(f"SMS notification failed: {e}")
    
    # 11. Fetch updated invoice for response
    updated_invoice = await db.invoices.find_one({"id": invoice_id}, {"_id": 0})
    
    return {
        "success": True,
        "message": "Receipt corrected successfully",
        "invoice": updated_invoice,
        "correction_id": correction_id,
        "refund_amount": refund_amount,
        "items_returned": len(items_to_return),
        "items_returned_to_shelf": items_to_return,
        "reprint": data.reprint_receipt
    }

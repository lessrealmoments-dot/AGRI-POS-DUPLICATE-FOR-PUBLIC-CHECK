"""
Incomplete Stock Correction Endpoint

Allows correcting an invoice when items were printed on receipt but not
physically given. Updates original invoice, returns stock to shelves, refunds
money via PAYMENT-AWARE ROUTING (AR → digital → cash), and creates an audit
trail.

Payment-aware routing (2026 audit fix):
  Before: every correction debited cashier wallet. This silently created phantom
          shortages for credit/digital/split invoices because cashier never
          received that money in the first place.
  After:  refund is allocated by `utils.refund_allocator.compute_refund_allocation`
          → reduces open AR first, then reverses digital channels, then debits
          cash. Day-closed corrections are now ALLOWED iff `cash_refund == 0`
          (the cashflow is untouched, so the closed Z-report stays consistent).
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List
from datetime import datetime, timezone

from utils import today_local
from utils.refund_allocator import compute_refund_allocation
from utils.helpers import (
    update_cashier_wallet, update_digital_wallet,
)

router = APIRouter()


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


async def log_inventory_movement(
    db, product_id: str, branch_id: str, movement_type: str,
    quantity: float, from_loc: str, to_loc: str, cost_price: float,
    user_id: str, user_name: str, notes: str,
):
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
        "created_at": now_iso(),
    })


@router.post("/invoices/{invoice_id}/correct-incomplete-stock")
async def correct_incomplete_stock(
    invoice_id: str,
    data: IncompleteStockCorrection,
):
    """
    Correct invoice when items weren't physically given.
    Routes refund payment-aware:
      • AR first  → reduces invoice.balance and customer.balance.
      • Digital   → reverses matching payment channel via digital wallet.
      • Cash      → debits cashier wallet (only if cash was paid).
    """
    from config import db
    from routes.verify import verify_pin_for_action

    user = {"id": "terminal", "full_name": "Terminal User", "email": "terminal@system"}

    # 1. Fetch invoice
    invoice = await db.invoices.find_one({"id": invoice_id}, {"_id": 0})
    if not invoice:
        raise HTTPException(404, "Invoice not found")

    # 2. Verify Manager PIN
    verifier = await verify_pin_for_action(
        data.manager_pin, "manager", branch_id=invoice["branch_id"]
    )
    if not verifier:
        await db.pin_attempts.insert_one({
            "id": new_id(),
            "action": "correct_incomplete_stock",
            "invoice_id": invoice_id,
            "success": False,
            "created_at": now_iso(),
        })
        raise HTTPException(403, "Invalid PIN. Authorization failed.")

    # 3. Build corrected items + compute refund_amount (pre-allocation)
    corrected_items = []
    items_to_return = []
    refund_amount = 0.0
    corrections_map = {item.product_id: item for item in data.items}

    for original_item in invoice.get("items", []):
        product_id = original_item.get("product_id", "")
        if product_id in corrections_map:
            correction = corrections_map[product_id]
            orig_qty = float(correction.original_qty)
            actual_qty = float(correction.actual_qty)
            diff_qty = orig_qty - actual_qty
            rate = float(correction.rate)
            if diff_qty > 0:
                refund_amount += diff_qty * rate
                items_to_return.append({
                    "product_id":   product_id,
                    "product_name": correction.product_name,
                    "quantity":     diff_qty,
                    "rate":         rate,
                    "unit":         correction.unit,
                })
            corrected_items.append({
                **original_item,
                "quantity": actual_qty,
                "total":    actual_qty * rate,
            })
        else:
            corrected_items.append(original_item)

    refund_amount = round(refund_amount, 2)
    new_subtotal = sum(item.get("total", 0) for item in corrected_items)
    discount = invoice.get("overall_discount", 0) or 0
    freight = invoice.get("freight", 0) or 0
    new_grand_total = round(new_subtotal - discount + freight, 2)

    # 4. Compute payment-aware allocation
    allocation = compute_refund_allocation(invoice, refund_amount)

    # 5. Day-closed gate — only block if a cash refund would touch the
    #    closed Z-report. AR-only & digital-only corrections can run.
    if allocation["cash_refund"] > 0:
        closed_doc = await db.daily_closings.find_one({
            "branch_id": invoice["branch_id"],
            "date":      invoice.get("order_date"),
            "status":    "closed",
        })
        if closed_doc:
            raise HTTPException(
                400,
                {
                    "type": "day_closed_cash_refund",
                    "message": (
                        f"Cannot refund cash on a closed day "
                        f"({invoice.get('order_date')}). Use Return & Refund, "
                        f"or correct without cash impact (AR/digital only)."
                    ),
                    "allocation_preview": {
                        "ar_reduction":     allocation["ar_reduction"],
                        "cash_refund":      allocation["cash_refund"],
                        "digital_refunds":  allocation["digital_refunds"],
                    },
                },
            )

    # 6. Compute new amount_paid + balance using AR-first accounting
    amount_paid = float(invoice.get("amount_paid", 0) or 0)
    # AR reduction shrinks invoice.balance; digital + cash reversals shrink
    # amount_paid (since money is being given back).
    refunded_from_payments = round(
        allocation["cash_refund"]
        + sum(d["amount"] for d in allocation["digital_refunds"]),
        2,
    )
    new_amount_paid = max(0.0, round(amount_paid - refunded_from_payments, 2))
    new_balance = max(0.0, round(new_grand_total - new_amount_paid, 2))

    # Derive new status
    if invoice.get("status") == "voided":
        new_status = "voided"
    elif new_balance <= 0.005:
        new_status = "paid"
    elif new_amount_paid > 0:
        new_status = "partial"
    else:
        new_status = invoice.get("status", "credit")

    # 7. Audit record (insert BEFORE mutating)
    correction_id = new_id()
    correction_record = {
        "id":                          correction_id,
        "invoice_id":                  invoice["id"],
        "invoice_number":              invoice.get("invoice_number", ""),
        "correction_type":             "incomplete_stock",
        "branch_id":                   invoice["branch_id"],
        "customer_id":                 invoice.get("customer_id"),
        "customer_name":               invoice.get("customer_name"),
        "order_date":                  invoice.get("order_date"),
        "original_items":              invoice.get("items", []),
        "corrected_items":             corrected_items,
        "items_returned_to_shelf":     items_to_return,
        "original_subtotal":           invoice.get("subtotal", 0),
        "original_grand_total":        invoice.get("grand_total", 0),
        "corrected_subtotal":          new_subtotal,
        "corrected_grand_total":       new_grand_total,
        "refund_amount":               refund_amount,
        # Payment-aware audit fields:
        "refund_allocation":           {
            "ar_reduction":           allocation["ar_reduction"],
            "cash_refund":            allocation["cash_refund"],
            "digital_refunds":        allocation["digital_refunds"],
            "check_refunds":          allocation["check_refunds"],
            "remaining_unallocated":  allocation["remaining_unallocated"],
        },
        "original_payment_summary":    {
            k: allocation["summary"][k] for k in (
                "grand_total", "open_balance",
                "cash_paid", "digital_paid", "check_paid",
            )
        },
        "corrected_by_id":             user.get("id"),
        "corrected_by_name":           user.get("full_name", ""),
        "authorized_by":               verifier.get("verifier_name", ""),
        "manager_pin_verified":        True,
        "notes":                       data.notes,
        "created_at":                  now_iso(),
    }
    await db.invoice_corrections.insert_one(correction_record)

    # 8. Return stock to shelves + inventory movements
    for item in items_to_return:
        await db.inventory.update_one(
            {"product_id": item["product_id"], "branch_id": invoice["branch_id"]},
            {"$inc": {"quantity": item["quantity"]},
             "$set": {"updated_at": now_iso()}},
            upsert=True,
        )
        await log_inventory_movement(
            db,
            item["product_id"], invoice["branch_id"],
            "incomplete_stock_return",
            item["quantity"],
            "", invoice.get("invoice_number", ""),
            0,
            user.get("id"), user.get("full_name", ""),
            f"Receipt correction - items not given - {invoice.get('invoice_number','')}",
        )

    # 9. Update invoice (totals, balance, payments)
    invoice_update = {
        "items":               corrected_items,
        "subtotal":            new_subtotal,
        "grand_total":         new_grand_total,
        "amount_paid":         new_amount_paid,
        "balance":             new_balance,
        "status":              new_status,
        "correction_applied":  True,
        "correction_id":       correction_id,
        "updated_at":          now_iso(),
        "updated_by":          user.get("id"),
    }
    await db.invoices.update_one({"id": invoice_id}, {"$set": invoice_update})

    # 10. Apply digital reversals (one per channel/ref)
    inv_number = invoice.get("invoice_number", "")
    for d in allocation["digital_refunds"]:
        await update_digital_wallet(
            invoice["branch_id"],
            -d["amount"],
            reference=(
                f"Incomplete-stock correction — {inv_number} — refund"
            ),
            platform=d.get("platform") or d.get("method", ""),
            ref_number=d.get("ref_number", ""),
        )
        # Mark the original payment as voided when a subsequent payment
        # was fully refunded (best-effort, idempotent)
        if d.get("payment_id"):
            await db.invoices.update_one(
                {"id": invoice_id, "payments.id": d["payment_id"]},
                {"$set": {"payments.$.voided": True,
                          "payments.$.voided_at": now_iso(),
                          "payments.$.voided_reason": "incomplete_stock_correction"}},
            )

    # 11. Apply cash refund (if any)
    if allocation["cash_refund"] > 0:
        ref_text = (
            f"Refund incomplete stock - {inv_number} - "
            f"{invoice.get('customer_name', 'Walk-in')} - "
            f"{len(items_to_return)} items not given"
        )
        await update_cashier_wallet(
            invoice["branch_id"],
            -allocation["cash_refund"],
            ref_text,
            allow_negative=False,
        )
        # Z-report visibility — log expense for cash refund only.
        await db.expenses.insert_one({
            "id":                  new_id(),
            "branch_id":           invoice["branch_id"],
            "category":            "Customer Return Refund",
            "description":         f"Incomplete stock refund - {inv_number}",
            "notes":               (
                data.notes
                or "Receipt corrected for items not physically given"
            ),
            "amount":              allocation["cash_refund"],
            "payment_method":      "Cash",
            "fund_source":         "cashier",
            "reference_number":    inv_number,
            "date":                await today_local(user.get("organization_id") or ""),
            "invoice_id":          invoice_id,
            "correction_id":       correction_id,
            "created_by":          user.get("id"),
            "created_by_name":     user.get("full_name", ""),
            "created_at":          now_iso(),
            "voided":              False,
        })

    # 12. Reduce customer AR (only for the ar_reduction portion).
    #     This prevents the previous double-debit (cashier AND customer
    #     balance both took the full hit on credit invoices).
    if invoice.get("customer_id") and allocation["ar_reduction"] > 0:
        await db.customers.update_one(
            {"id": invoice["customer_id"]},
            {"$inc": {"balance": -allocation["ar_reduction"]}},
        )

    # 13. SMS — notify customer of correction (any refund > 0 with linked customer)
    if invoice.get("customer_id") and refund_amount > 0:
        try:
            from routes.sms_hooks import on_payment_received
            customer = await db.customers.find_one(
                {"id": invoice["customer_id"]}, {"_id": 0}
            )
            if customer:
                await on_payment_received(
                    customer_id=invoice["customer_id"],
                    amount_paid=refund_amount,
                    remaining_balance=float(customer.get("balance", 0) or 0),
                    branch_id=invoice.get("branch_id", ""),
                    next_due_info=(
                        f"Receipt correction — {inv_number}. "
                    ),
                )
        except Exception as e:
            print(f"SMS notification failed: {e}")

    # 14. Updated invoice for response
    updated_invoice = await db.invoices.find_one({"id": invoice_id}, {"_id": 0})

    return {
        "success":                True,
        "message":                "Receipt corrected successfully",
        "invoice":                updated_invoice,
        "correction_id":          correction_id,
        "refund_amount":          refund_amount,
        "items_returned":         len(items_to_return),
        "items_returned_to_shelf": items_to_return,
        "refund_allocation":      correction_record["refund_allocation"],
        "reprint":                data.reprint_receipt,
    }

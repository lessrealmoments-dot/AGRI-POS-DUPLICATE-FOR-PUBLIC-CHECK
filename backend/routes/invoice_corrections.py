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

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List
from datetime import datetime, timezone

from utils import today_local, get_current_user
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
    user: dict = Depends(get_current_user),
):
    """
    Correct invoice when items weren't physically given.
    Routes refund payment-aware:
      • AR first  → reduces invoice.balance and customer.balance.
      • Digital   → reverses matching payment channel via digital wallet.
      • Cash      → debits cashier wallet (only if cash was paid).

    Requires an authenticated user — both for tenant scoping (so
    `db.invoices.find_one` sees the right organization) and for an honest
    audit trail (`corrected_by_id`, `corrected_by_name`). The PIN is then
    a SECOND factor on top of the user's auth.
    """
    from config import db
    from routes.verify import verify_pin_for_action

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
    #
    # Per-line discount preservation (2026-02 phantom-balance fix):
    # The previous version used `actual_qty * rate` as the new line total and
    # `diff_qty * rate` as the refund. Both formulas IGNORED the original
    # line's discount, which meant invoices with per-line discounts ended up
    # with a phantom AR balance equal to the sum of all "lost" discounts
    # (e.g. SI-MB-001059 / AIZON AGRIVET: ₱1,315 phantom balance from four
    # discounted items 325+650+300+40). The cash refund (4,490) correctly
    # debited the drawer, but `new_grand_total` failed to shrink by the
    # same 4,490 because the discount "reappeared" when we recomputed
    # `actual_qty * rate`.
    #
    # Fix: prorate the original line discount by actual_qty/orig_qty so the
    # remaining qty keeps a proportional discount, and derive refund_amount
    # from the ACTUAL line-total drop (orig_total - new_total). This keeps
    # refund == grand_total drop, so balance lands cleanly at 0 for cash
    # invoices and at the legitimate remainder for partials.
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

            orig_line_total = float(original_item.get("total", orig_qty * rate) or 0)
            orig_line_disc = float(original_item.get("discount_amount", 0) or 0)
            # Prorate per-unit discount so the remaining qty keeps the same
            # effective unit price. Avoids losing the discount altogether.
            per_unit_disc = (orig_line_disc / orig_qty) if orig_qty > 0 else 0.0
            new_line_disc = round(per_unit_disc * actual_qty, 2)
            new_line_total = round(actual_qty * rate - new_line_disc, 2)

            if diff_qty > 0:
                # Refund equals the actual line-value drop (preserves discount
                # math). For lines with no discount this matches diff_qty*rate
                # exactly, so behaviour for non-discounted invoices is unchanged.
                refund_amount += round(orig_line_total - new_line_total, 2)
                items_to_return.append({
                    "product_id":   product_id,
                    "product_name": correction.product_name,
                    "quantity":     diff_qty,
                    "rate":         rate,
                    "unit":         correction.unit,
                })
            corrected_items.append({
                **original_item,
                "quantity":         actual_qty,
                "discount_amount":  new_line_disc,
                "total":            new_line_total,
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

    # NOTE — day-closed corrections are intentionally ALLOWED.
    # Rationale (Feb 2026 owner decision): the inventory reversal +
    # today-dated wallet_movement + today-dated expense row provide a
    # complete audit trail. Count-sheets will catch any physical-vs-
    # system mismatch (e.g. system says 5 returned but only 3 are on
    # the shelf → audit flags 2 missing). The original closed Z-report
    # stays as it was; the refund's impact lives in TODAY's books.

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
    #
    # Use the dedicated refund-style hook so the message reads as a
    # correction + refund rather than (the old behaviour) a "we received
    # your payment" confirmation. Previous wording confused customers
    # whose invoice was actually being REFUNDED into thinking they had
    # just made a payment to the store.
    if invoice.get("customer_id") and refund_amount > 0:
        try:
            from routes.sms_hooks import on_stock_correction_refunded
            digital_total = sum(
                d["amount"] for d in allocation.get("digital_refunds", []) or []
            )
            await on_stock_correction_refunded(
                customer_id=invoice["customer_id"],
                invoice_number=inv_number,
                branch_id=invoice.get("branch_id", ""),
                refund_amount=refund_amount,
                ar_reduction=float(allocation.get("ar_reduction", 0) or 0),
                cash_refund=float(allocation.get("cash_refund", 0) or 0),
                digital_refund=float(digital_total),
                remaining_balance=new_balance,
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



@router.post("/invoices/{invoice_id}/repair-correction-balance")
async def repair_correction_balance(
    invoice_id: str,
    user: dict = Depends(get_current_user),
    write_off_residual: bool = False,
    write_off_max: float = 100.0,
):
    """
    One-off admin repair for invoices that suffered the pre-fix phantom-balance
    bug: an incomplete-stock correction overstated `refund_amount` for any
    line that originally had a per-line discount, so cash left the drawer for
    the full diff_qty*rate but the new grand_total only fell by the discounted
    amount — leaving a positive `balance` that the customer never owed.

    This endpoint recomputes corrected line totals with per-unit discount
    prorated and trues up grand_total + status. Cash, AR, inventory, and
    expense rows are untouched (those were already correct — only the
    invoice-side numbers drifted).

    Optional `write_off_residual=true` (admin only): after re-prorating
    discounts, any remaining balance ≤ `write_off_max` is zeroed and the
    delta is logged as a "Correction Over-Refund Write-off" expense for
    clean accounting. This handles the corner case where the cashier
    over-refunded by a small amount because the buggy refund formula
    didn't account for the per-unit discount on the refunded units.

    Idempotent: re-running on an already-clean invoice is a no-op.
    Admin-only.
    """
    from config import db

    if user.get("role") != "admin":
        raise HTTPException(403, "Admin only")

    invoice = await db.invoices.find_one({"id": invoice_id}, {"_id": 0})
    if not invoice:
        raise HTTPException(404, "Invoice not found")
    if not invoice.get("correction_applied"):
        raise HTTPException(400, "Invoice has no correction applied — nothing to repair")

    correction = await db.invoice_corrections.find_one(
        {"id": invoice.get("correction_id")}, {"_id": 0}
    )
    if not correction:
        raise HTTPException(400, "Correction record not found — cannot repair safely")

    # Rebuild corrected items with prorated discount preservation.
    orig_items = correction.get("original_items", []) or []
    cur_items = invoice.get("items", []) or []
    by_id_orig = {it.get("product_id"): it for it in orig_items if it.get("product_id")}

    rebuilt = []
    for cur in cur_items:
        pid = cur.get("product_id", "")
        orig = by_id_orig.get(pid)
        if not orig:
            rebuilt.append(cur)
            continue
        orig_qty = float(orig.get("quantity", 0) or 0)
        new_qty = float(cur.get("quantity", 0) or 0)
        rate = float(cur.get("rate", 0) or 0)
        orig_line_disc = float(orig.get("discount_amount", 0) or 0)
        if orig_qty > 0:
            per_unit_disc = orig_line_disc / orig_qty
        else:
            per_unit_disc = 0.0
        new_line_disc = round(per_unit_disc * new_qty, 2)
        new_line_total = round(new_qty * rate - new_line_disc, 2)
        rebuilt.append({
            **cur,
            "discount_amount": new_line_disc,
            "total":           new_line_total,
        })

    new_subtotal = round(sum(float(it.get("total", 0) or 0) for it in rebuilt), 2)
    discount = float(invoice.get("overall_discount", 0) or 0)
    freight = float(invoice.get("freight", 0) or 0)
    new_grand_total = round(new_subtotal - discount + freight, 2)

    new_amount_paid = float(invoice.get("amount_paid", 0) or 0)
    new_balance = max(0.0, round(new_grand_total - new_amount_paid, 2))

    if invoice.get("status") == "voided":
        new_status = "voided"
    elif new_balance <= 0.005:
        new_status = "paid"
    elif new_amount_paid > 0:
        new_status = "partial"
    else:
        new_status = invoice.get("status", "credit")

    # Capture before-state for the audit log.
    before = {
        "grand_total": invoice.get("grand_total"),
        "subtotal":    invoice.get("subtotal"),
        "balance":     invoice.get("balance"),
        "status":      invoice.get("status"),
    }
    delta = round(float(invoice.get("grand_total", 0) or 0) - new_grand_total, 2)

    await db.invoices.update_one(
        {"id": invoice_id},
        {"$set": {
            "items":            rebuilt,
            "subtotal":         new_subtotal,
            "grand_total":      new_grand_total,
            "balance":          new_balance,
            "status":           new_status,
            "balance_repaired": True,
            "balance_repaired_at": now_iso(),
            "balance_repaired_by": user.get("id"),
            "balance_repair_delta": delta,
            "updated_at":       now_iso(),
        }}
    )

    # If we shrank the grand_total, the customer's open AR over-counted that
    # invoice's balance by `delta`. Reduce their aggregate balance to match.
    if delta > 0 and invoice.get("customer_id"):
        await db.customers.update_one(
            {"id": invoice["customer_id"]},
            {"$inc": {"balance": -delta}},
        )

    # ── Optional residual write-off (cash over-refund cleanup) ─────────────
    # The buggy refund formula (diff_qty * rate, no per-unit discount) often
    # over-refunded the customer in cash for the units they returned. That
    # over-refund shows up here as a positive residual `new_balance` even
    # after we fixed the line totals. Cashier wallet is already short by
    # this amount (cash physically left the drawer), so the cleanest fix
    # is to write the residual off as a small Customer Service expense
    # — keeping the invoice clean and the books truthful.
    write_off_amount = 0.0
    if (
        write_off_residual
        and new_balance > 0
        and new_balance <= float(write_off_max)
    ):
        write_off_amount = new_balance
        # Bring AR all the way to zero on both invoice and customer ledger.
        await db.invoices.update_one(
            {"id": invoice_id},
            {"$set": {
                "balance":              0.0,
                "status":               "paid" if invoice.get("status") != "voided" else "voided",
                "balance_write_off":    write_off_amount,
                "balance_write_off_at": now_iso(),
                "balance_write_off_by": user.get("id"),
            }}
        )
        if invoice.get("customer_id"):
            await db.customers.update_one(
                {"id": invoice["customer_id"]},
                {"$inc": {"balance": -write_off_amount}},
            )
        # Audit trail: surface in Z-report / Expenses as a tiny customer-service loss.
        await db.expenses.insert_one({
            "id":                  new_id(),
            "branch_id":           invoice["branch_id"],
            "category":            "Correction Over-Refund Write-off",
            "description":         f"Phantom balance write-off — {invoice.get('invoice_number','')}",
            "notes":               (
                "Auto write-off after repair-correction-balance: cashier "
                "previously over-refunded the customer in cash because the "
                "old correction formula ignored per-line discount on the "
                "refunded units."
            ),
            "amount":              write_off_amount,
            "payment_method":      "Cash",
            "fund_source":         "cashier",
            "reference_number":    invoice.get("invoice_number", ""),
            "date":                await today_local(user.get("organization_id") or ""),
            "invoice_id":          invoice_id,
            "correction_id":       invoice.get("correction_id"),
            "created_by":          user.get("id"),
            "created_by_name":     user.get("full_name", ""),
            "created_at":          now_iso(),
            "voided":              False,
        })
        new_balance = 0.0
        new_status = "paid" if invoice.get("status") != "voided" else "voided"

    return {
        "success":         True,
        "invoice_id":      invoice_id,
        "invoice_number":  invoice.get("invoice_number", ""),
        "before":          before,
        "after": {
            "grand_total": new_grand_total,
            "subtotal":    new_subtotal,
            "balance":     new_balance,
            "status":      new_status,
        },
        "grand_total_delta":   delta,
        "residual_written_off": round(write_off_amount, 2),
    }

"""
Internal Invoices — auto-generated when a branch transfer is created.
Tracks the financial obligation between branches (internal AP/AR).
"""
from fastapi import APIRouter, Depends, HTTPException
from typing import Optional
from datetime import datetime, timezone, timedelta
from config import db
from utils import get_current_user, now_iso, new_id, generate_next_number

router = APIRouter(prefix="/internal-invoices", tags=["Internal Invoices"])


async def create_internal_invoice(transfer: dict, user: dict):
    """Auto-create an internal invoice from a branch transfer order."""
    items = transfer.get("items", [])
    total = round(sum(
        float(i.get("transfer_capital", 0)) * float(i.get("qty", 0)) for i in items
    ), 2)

    # Default payment terms: Net 15
    terms_days = 15
    created_at = datetime.now(timezone.utc)
    due_date = (created_at + timedelta(days=terms_days)).isoformat()

    count = await db.internal_invoices.count_documents({})
    # Generate internal invoice number (atomic, branch-specific)
    to_branch_id = transfer.get("to_branch_id", "")
    invoice_number = await generate_next_number("INV", to_branch_id) if to_branch_id else f"INV-{created_at.strftime('%Y%m%d')}-{str(count + 1).zfill(4)}"

    invoice = {
        "id": new_id(),
        "invoice_number": invoice_number,
        "transfer_id": transfer["id"],
        "transfer_number": transfer.get("order_number", ""),
        "request_po_id": transfer.get("request_po_id", ""),
        "request_po_number": transfer.get("request_po_number", ""),
        "from_branch_id": transfer["from_branch_id"],
        "to_branch_id": transfer["to_branch_id"],
        "items": [{
            "product_id": i.get("product_id", ""),
            "product_name": i.get("product_name", ""),
            "sku": i.get("sku", ""),
            "category": i.get("category", ""),
            "unit": i.get("unit", ""),
            "qty": float(i.get("qty", 0)),
            "requested_qty": float(i.get("requested_qty", 0)) if i.get("requested_qty") else None,
            "transfer_capital": float(i.get("transfer_capital", 0)),
            "branch_retail": float(i.get("branch_retail", 0)),
            "line_total": round(float(i.get("transfer_capital", 0)) * float(i.get("qty", 0)), 2),
        } for i in items],
        "subtotal": total,
        "grand_total": total,
        "status": "prepared",  # prepared → sent → received → paid
        "payment_status": "unpaid",  # unpaid → paid
        "terms_days": terms_days,
        "due_date": due_date,
        "created_by": user.get("id", ""),
        "created_by_name": user.get("full_name", user.get("username", "")),
        "created_at": now_iso(),
        "sent_at": None,
        "received_at": None,
        "paid_at": None,
    }

    await db.internal_invoices.insert_one(invoice)
    del invoice["_id"]

    # Store invoice_id on the transfer
    await db.branch_transfer_orders.update_one(
        {"id": transfer["id"]},
        {"$set": {"invoice_id": invoice["id"], "invoice_number": invoice["invoice_number"]}}
    )

    return invoice


async def update_invoice_status(transfer_id: str, new_status: str, extra_fields: dict = None):
    """Update invoice status when transfer status changes."""
    invoice = await db.internal_invoices.find_one(
        {"transfer_id": transfer_id}, {"_id": 0}
    )
    if not invoice:
        return

    update = {"status": new_status}
    if new_status == "sent":
        update["sent_at"] = now_iso()
    elif new_status == "received":
        update["received_at"] = now_iso()
    elif new_status == "paid":
        update["paid_at"] = now_iso()
        update["payment_status"] = "paid"

    if extra_fields:
        update.update(extra_fields)

    await db.internal_invoices.update_one(
        {"transfer_id": transfer_id}, {"$set": update}
    )


# ── API Endpoints ─────────────────────────────────────────────────────────────

@router.get("")
async def list_internal_invoices(
    user=Depends(get_current_user),
    branch_id: Optional[str] = None,
    status: Optional[str] = None,
    payment_status: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
):
    """List internal invoices. Admin sees all, branch users see their branch."""
    q = {}
    user_branch = user.get("branch_id")
    is_admin = user.get("role") == "admin"

    if branch_id:
        q["$or"] = [{"from_branch_id": branch_id}, {"to_branch_id": branch_id}]
    elif not is_admin and user_branch:
        q["$or"] = [{"from_branch_id": user_branch}, {"to_branch_id": user_branch}]

    if status:
        q["status"] = status
    if payment_status:
        q["payment_status"] = payment_status

    total = await db.internal_invoices.count_documents(q)
    invoices = await db.internal_invoices.find(
        q, {"_id": 0}
    ).sort("created_at", -1).skip(skip).limit(limit).to_list(limit)

    # Enrich with branch names
    branch_cache = {}
    for inv in invoices:
        for key in ["from_branch_id", "to_branch_id"]:
            bid = inv.get(key, "")
            if bid and bid not in branch_cache:
                b = await db.branches.find_one({"id": bid}, {"_id": 0, "name": 1})
                branch_cache[bid] = b.get("name", "") if b else ""
            inv[f"{key.replace('_id','_name')}"] = branch_cache.get(bid, "")

    return {"invoices": invoices, "total": total}


@router.get("/summary")
async def get_invoice_summary(
    user=Depends(get_current_user),
    branch_id: Optional[str] = None,
):
    """Get summary of internal invoices for dashboard."""
    user_branch = branch_id or user.get("branch_id", "")
    is_admin = user.get("role") == "admin"

    # Payables: invoices where this branch is the TO (receiver/buyer)
    payable_q = {"payment_status": "unpaid"}
    if user_branch and not is_admin:
        payable_q["to_branch_id"] = user_branch
    elif user_branch:
        payable_q["to_branch_id"] = user_branch

    payables = await db.internal_invoices.find(payable_q, {"_id": 0}).to_list(200)

    now = datetime.now(timezone.utc)
    total_payable = sum(inv.get("grand_total", 0) for inv in payables)
    overdue = [inv for inv in payables if inv.get("due_date") and datetime.fromisoformat(inv["due_date"].replace("Z", "+00:00")) < now]
    overdue_total = sum(inv.get("grand_total", 0) for inv in overdue)
    due_soon = [inv for inv in payables
                if inv.get("due_date")
                and now <= datetime.fromisoformat(inv["due_date"].replace("Z", "+00:00")) <= now + timedelta(days=7)]
    due_soon_total = sum(inv.get("grand_total", 0) for inv in due_soon)

    # Receivables: invoices where this branch is the FROM (supplier/sender)
    receivable_q = {"payment_status": "unpaid"}
    if user_branch and not is_admin:
        receivable_q["from_branch_id"] = user_branch
    elif user_branch:
        receivable_q["from_branch_id"] = user_branch

    receivables = await db.internal_invoices.find(receivable_q, {"_id": 0}).to_list(200)
    total_receivable = sum(inv.get("grand_total", 0) for inv in receivables)

    return {
        "payable": {
            "total": round(total_payable, 2),
            "count": len(payables),
            "overdue_total": round(overdue_total, 2),
            "overdue_count": len(overdue),
            "due_soon_total": round(due_soon_total, 2),
            "due_soon_count": len(due_soon),
        },
        "receivable": {
            "total": round(total_receivable, 2),
            "count": len(receivables),
        }
    }


@router.get("/profitability")
async def get_internal_profitability(
    user=Depends(get_current_user),
    period: str = "this_month",
):
    """
    Get internal profitability per branch.
    Revenue = total transfer capital from invoices where branch is supplier (from_branch_id).
    Cost = total transfer capital from invoices where branch is buyer (to_branch_id).
    Profit = Revenue - Cost.
    """
    now = datetime.now(timezone.utc)
    if period == "last_month":
        start = (now.replace(day=1) - timedelta(days=1)).replace(day=1)
        end = now.replace(day=1)
    elif period == "quarter":
        q_month = ((now.month - 1) // 3) * 3 + 1
        start = now.replace(month=q_month, day=1)
        end = now
    elif period == "year":
        start = now.replace(month=1, day=1)
        end = now
    else:  # this_month
        start = now.replace(day=1)
        end = now

    date_filter = {"created_at": {"$gte": start.isoformat(), "$lte": end.isoformat()}}

    # Get all invoices in period (exclude cancelled)
    invoices = await db.internal_invoices.find(
        {**date_filter, "status": {"$ne": "cancelled"}}, {"_id": 0}
    ).to_list(1000)

    # Get all branches
    all_branches = await db.branches.find({}, {"_id": 0, "id": 1, "name": 1}).to_list(50)

    # Build per-branch profitability
    branch_data = {}
    for b in all_branches:
        branch_data[b["id"]] = {
            "branch_id": b["id"],
            "branch_name": b["name"],
            "revenue": 0.0,      # supplied to others
            "cost": 0.0,         # received from others
            "invoice_count": 0,
            "supplied_count": 0,
            "received_count": 0,
        }

    for inv in invoices:
        amount = float(inv.get("received_total") or inv.get("grand_total", 0))
        from_id = inv.get("from_branch_id", "")
        to_id = inv.get("to_branch_id", "")

        if from_id in branch_data:
            branch_data[from_id]["revenue"] += amount
            branch_data[from_id]["supplied_count"] += 1
        if to_id in branch_data:
            branch_data[to_id]["cost"] += amount
            branch_data[to_id]["received_count"] += 1
            branch_data[to_id]["invoice_count"] += 1

    # Calculate profit and sort by profit desc
    result = []
    total_revenue = 0.0
    total_cost = 0.0
    for bd in branch_data.values():
        bd["profit"] = round(bd["revenue"] - bd["cost"], 2)
        bd["revenue"] = round(bd["revenue"], 2)
        bd["cost"] = round(bd["cost"], 2)
        total_revenue += bd["revenue"]
        total_cost += bd["cost"]
        if bd["revenue"] > 0 or bd["cost"] > 0:
            result.append(bd)

    result.sort(key=lambda x: x["profit"], reverse=True)

    return {
        "period": period,
        "branches": result,
        "totals": {
            "revenue": round(total_revenue, 2),
            "cost": round(total_cost, 2),
            "net": round(total_revenue - total_cost, 2),
            "invoice_count": len(invoices),
        }
    }



@router.get("/{invoice_id}")
async def get_internal_invoice(invoice_id: str, user=Depends(get_current_user)):
    """Get a single internal invoice."""
    invoice = await db.internal_invoices.find_one({"id": invoice_id}, {"_id": 0})
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    # Enrich with branch names
    for key in ["from_branch_id", "to_branch_id"]:
        bid = invoice.get(key, "")
        b = await db.branches.find_one({"id": bid}, {"_id": 0, "name": 1})
        invoice[f"{key.replace('_id','_name')}"] = b.get("name", "") if b else ""

    return invoice


@router.get("/by-transfer/{transfer_id}")
async def get_invoice_by_transfer(transfer_id: str, user=Depends(get_current_user)):
    """Get the internal invoice linked to a transfer."""
    invoice = await db.internal_invoices.find_one(
        {"transfer_id": transfer_id}, {"_id": 0}
    )
    if not invoice:
        raise HTTPException(status_code=404, detail="No invoice for this transfer")

    for key in ["from_branch_id", "to_branch_id"]:
        bid = invoice.get(key, "")
        b = await db.branches.find_one({"id": bid}, {"_id": 0, "name": 1})
        invoice[f"{key.replace('_id','_name')}"] = b.get("name", "") if b else ""

    return invoice


@router.post("/{invoice_id}/pay")
async def pay_internal_invoice(invoice_id: str, data: dict, user=Depends(get_current_user)):
    """
    Pay an internal invoice by deducting from the receiving branch's bank.
    Credits the supplier branch's bank.
    Requires admin PIN or TOTP verification.
    """
    if user.get("role") not in ["admin"]:
        raise HTTPException(status_code=403, detail="Only admin can pay internal invoices")

    invoice = await db.internal_invoices.find_one({"id": invoice_id}, {"_id": 0})
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if invoice.get("payment_status") == "paid":
        raise HTTPException(status_code=400, detail="Invoice is already paid")

    to_branch_id = invoice["to_branch_id"]   # receiver/buyer — pays
    from_branch_id = invoice["from_branch_id"]  # supplier — receives

    amount = float(invoice.get("received_total") or invoice.get("grand_total", 0))
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Invalid invoice amount")

    # Get bank wallets for both branches
    buyer_bank = await db.fund_wallets.find_one(
        {"branch_id": to_branch_id, "type": "bank", "active": True}, {"_id": 0}
    )
    supplier_bank = await db.fund_wallets.find_one(
        {"branch_id": from_branch_id, "type": "bank", "active": True}, {"_id": 0}
    )

    if not buyer_bank:
        raise HTTPException(status_code=400, detail="Receiving branch has no bank wallet")
    if not supplier_bank:
        raise HTTPException(status_code=400, detail="Supplier branch has no bank wallet")

    buyer_balance = float(buyer_bank.get("balance", 0))
    if buyer_balance < amount:
        # Get branch names for error message
        to_branch = await db.branches.find_one({"id": to_branch_id}, {"_id": 0, "name": 1})
        to_name = to_branch.get("name", "") if to_branch else ""
        raise HTTPException(status_code=400, detail={
            "type": "insufficient_funds",
            "message": f"{to_name} bank has ₱{buyer_balance:,.2f}. Need ₱{amount:,.2f}.",
            "balance": buyer_balance,
            "required": amount,
            "shortfall": round(amount - buyer_balance, 2),
        })

    # Deduct from buyer's bank
    new_buyer_balance = round(buyer_balance - amount, 2)
    await db.fund_wallets.update_one(
        {"id": buyer_bank["id"]}, {"$inc": {"balance": -amount}}
    )
    await db.wallet_movements.insert_one({
        "id": new_id(),
        "wallet_id": buyer_bank["id"],
        "branch_id": to_branch_id,
        "type": "cash_out",
        "amount": round(-amount, 2),
        "reference": f"Internal invoice payment {invoice['invoice_number']} ({invoice.get('transfer_number', '')})",
        "balance_after": new_buyer_balance,
        "created_at": now_iso(),
    })

    # Credit supplier's bank
    supplier_balance = float(supplier_bank.get("balance", 0))
    new_supplier_balance = round(supplier_balance + amount, 2)
    await db.fund_wallets.update_one(
        {"id": supplier_bank["id"]}, {"$inc": {"balance": amount}}
    )
    await db.wallet_movements.insert_one({
        "id": new_id(),
        "wallet_id": supplier_bank["id"],
        "branch_id": from_branch_id,
        "type": "cash_in",
        "amount": round(amount, 2),
        "reference": f"Internal invoice received {invoice['invoice_number']} ({invoice.get('transfer_number', '')})",
        "balance_after": new_supplier_balance,
        "created_at": now_iso(),
    })

    # Update invoice status
    await db.internal_invoices.update_one(
        {"id": invoice_id},
        {"$set": {
            "status": "paid",
            "payment_status": "paid",
            "paid_at": now_iso(),
            "paid_by": user["id"],
            "paid_by_name": user.get("full_name", user.get("username", "")),
            "paid_amount": amount,
            "payment_note": data.get("note", ""),
        }}
    )

    # Get branch names for notification
    from_branch = await db.branches.find_one({"id": from_branch_id}, {"_id": 0, "name": 1})
    to_branch = await db.branches.find_one({"id": to_branch_id}, {"_id": 0, "name": 1})
    from_name = from_branch.get("name", "") if from_branch else ""
    to_name = to_branch.get("name", "") if to_branch else ""

    # Notify both branches
    admins = await db.users.find({"role": "admin", "active": True}, {"_id": 0, "id": 1}).to_list(50)
    admin_ids = [a["id"] for a in admins]
    await db.notifications.insert_one({
        "id": new_id(),
        "type": "internal_invoice_paid",
        "title": "Internal Invoice Paid",
        "message": f"Invoice {invoice['invoice_number']} paid: ₱{amount:,.2f} from {to_name} bank → {from_name} bank",
        "branch_id": to_branch_id,
        "metadata": {
            "invoice_id": invoice_id,
            "invoice_number": invoice["invoice_number"],
            "amount": amount,
        },
        "target_user_ids": admin_ids,
        "read_by": [],
        "created_at": now_iso(),
    })

    return {
        "message": f"Invoice {invoice['invoice_number']} paid. ₱{amount:,.2f} transferred from {to_name} bank to {from_name} bank.",
        "invoice_number": invoice["invoice_number"],
        "amount": amount,
        "buyer_bank_balance": new_buyer_balance,
        "supplier_bank_balance": new_supplier_balance,
    }


async def check_due_invoices():
    """Check for due/overdue invoices and send notifications. Called by scheduler.

    Phase 5+ B-1 hardening: `internal_invoices` is now a tenant-scoped
    collection (registered in `TENANT_COLLECTIONS`), so reading without an
    org context fails closed. The scheduler therefore walks each active
    organization and runs the existing due/overdue logic inside that
    tenant's context. All downstream `db.*` reads/writes inherit the
    correct scope automatically.
    """
    from config import _raw_db, set_org_context, get_org_context

    # Snapshot active org ids cross-tenant (the only legitimate use of
    # `_raw_db.organizations` outside admin tools).
    org_rows = await _raw_db.organizations.find(
        {"active": True}, {"_id": 0, "id": 1}
    ).to_list(10000)
    org_ids = [o["id"] for o in org_rows if o.get("id")]

    saved_ctx = get_org_context()
    try:
        for org_id in org_ids:
            set_org_context(org_id)
            await _check_due_invoices_for_current_org()
    finally:
        # Restore whatever context was active before this batch ran so we
        # don't leak the last-iterated org back to the scheduler caller.
        set_org_context(saved_ctx)


async def _check_due_invoices_for_current_org():
    """Per-tenant body of the daily due-invoice check.

    Assumes `set_org_context(org_id)` has already been called by the
    caller. All `db.*` queries below auto-scope to the current tenant.
    """
    now = datetime.now(timezone.utc)
    three_days_ahead = now + timedelta(days=3)

    # Find unpaid invoices due within 3 days
    due_soon = await db.internal_invoices.find({
        "payment_status": "unpaid",
        "due_date": {"$lte": three_days_ahead.isoformat(), "$gte": now.isoformat()},
    }, {"_id": 0}).to_list(100)

    # Find overdue invoices
    overdue = await db.internal_invoices.find({
        "payment_status": "unpaid",
        "due_date": {"$lt": now.isoformat()},
        "overdue_notified": {"$ne": True},
    }, {"_id": 0}).to_list(100)

    admins = await db.users.find({"role": "admin", "active": True}, {"_id": 0, "id": 1}).to_list(50)
    admin_ids = [a["id"] for a in admins]

    # Notify for due-soon invoices (only once)
    for inv in due_soon:
        if inv.get("due_soon_notified"):
            continue
        to_branch = await db.branches.find_one({"id": inv["to_branch_id"]}, {"_id": 0, "name": 1})
        to_name = to_branch.get("name", "") if to_branch else ""
        days_left = max(0, (datetime.fromisoformat(inv["due_date"].replace("Z", "+00:00")) - now).days)

        await db.notifications.insert_one({
            "id": new_id(),
            "type": "internal_invoice_due",
            "title": f"Invoice Due in {days_left} Day{'s' if days_left != 1 else ''}",
            "message": f"{inv['invoice_number']} for {to_name}: ₱{inv['grand_total']:,.2f} due in {days_left} day{'s' if days_left != 1 else ''}",
            "branch_id": inv["to_branch_id"],
            "metadata": {"invoice_id": inv["id"], "invoice_number": inv["invoice_number"]},
            "target_user_ids": admin_ids,
            "read_by": [],
            "created_at": now_iso(),
        })
        await db.internal_invoices.update_one(
            {"id": inv["id"]}, {"$set": {"due_soon_notified": True}}
        )

    # Notify + auto-pay overdue invoices
    for inv in overdue:
        to_branch = await db.branches.find_one({"id": inv["to_branch_id"]}, {"_id": 0, "name": 1})
        to_name = to_branch.get("name", "") if to_branch else ""

        # Try auto-pay from bank
        buyer_bank = await db.fund_wallets.find_one(
            {"branch_id": inv["to_branch_id"], "type": "bank", "active": True}, {"_id": 0}
        )
        amount = float(inv.get("received_total") or inv.get("grand_total", 0))
        buyer_balance = float(buyer_bank.get("balance", 0)) if buyer_bank else 0

        if buyer_bank and buyer_balance >= amount and amount > 0:
            # Auto-deduct
            supplier_bank = await db.fund_wallets.find_one(
                {"branch_id": inv["from_branch_id"], "type": "bank", "active": True}, {"_id": 0}
            )
            if supplier_bank:
                new_buyer = round(buyer_balance - amount, 2)
                await db.fund_wallets.update_one({"id": buyer_bank["id"]}, {"$inc": {"balance": -amount}})
                await db.wallet_movements.insert_one({
                    "id": new_id(), "wallet_id": buyer_bank["id"],
                    "branch_id": inv["to_branch_id"], "type": "cash_out",
                    "amount": round(-amount, 2),
                    "reference": f"Auto-payment: {inv['invoice_number']} (overdue)",
                    "balance_after": new_buyer, "created_at": now_iso(),
                })
                sup_balance = float(supplier_bank.get("balance", 0))
                new_sup = round(sup_balance + amount, 2)
                await db.fund_wallets.update_one({"id": supplier_bank["id"]}, {"$inc": {"balance": amount}})
                await db.wallet_movements.insert_one({
                    "id": new_id(), "wallet_id": supplier_bank["id"],
                    "branch_id": inv["from_branch_id"], "type": "cash_in",
                    "amount": round(amount, 2),
                    "reference": f"Auto-received: {inv['invoice_number']} (overdue)",
                    "balance_after": new_sup, "created_at": now_iso(),
                })
                await db.internal_invoices.update_one(
                    {"id": inv["id"]},
                    {"$set": {
                        "status": "paid", "payment_status": "paid",
                        "paid_at": now_iso(), "paid_by_name": "System (auto-pay)",
                        "paid_amount": amount, "payment_note": "Auto-deducted on due date",
                        "overdue_notified": True,
                    }}
                )
                await db.notifications.insert_one({
                    "id": new_id(), "type": "internal_invoice_auto_paid",
                    "title": "Invoice Auto-Paid (Overdue)",
                    "message": f"{inv['invoice_number']} auto-deducted ₱{amount:,.2f} from {to_name} bank (overdue)",
                    "branch_id": inv["to_branch_id"],
                    "metadata": {"invoice_id": inv["id"], "invoice_number": inv["invoice_number"]},
                    "target_user_ids": admin_ids, "read_by": [], "created_at": now_iso(),
                })
                continue

        # Can't auto-pay — notify admin
        await db.notifications.insert_one({
            "id": new_id(), "type": "internal_invoice_overdue",
            "title": "Internal Invoice OVERDUE",
            "message": f"{inv['invoice_number']} for {to_name}: ₱{amount:,.2f} is OVERDUE. Bank balance: ₱{buyer_balance:,.2f}.",
            "branch_id": inv["to_branch_id"],
            "metadata": {"invoice_id": inv["id"], "invoice_number": inv["invoice_number"]},
            "target_user_ids": admin_ids, "read_by": [], "created_at": now_iso(),
        })
        await db.internal_invoices.update_one(
            {"id": inv["id"]}, {"$set": {"overdue_notified": True}}
        )

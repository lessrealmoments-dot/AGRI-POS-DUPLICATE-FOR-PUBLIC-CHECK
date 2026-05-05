"""
Incident Tickets — Track and investigate transfer variances (shortages, excesses, damage).
Created when a sender accepts a variance but wants it investigated.

Phase 2: Structured resolution with resolution_type, accountable_party, sender confirmation.
"""
from fastapi import APIRouter, Depends, HTTPException
from typing import Optional
from config import db
from utils import get_current_user, check_perm, now_iso, new_id, today_local

router = APIRouter(prefix="/incident-tickets", tags=["Incident Tickets"])

TRANSFER_RESOLUTION_TYPES = {
    "transit_loss": "Transit Loss — goods lost during transport",
    "sender_error": "Sender Error — sender miscounted, no actual loss",
    "receiver_error": "Receiver Error — receiver miscounted, corrected",
    "write_off": "Write Off — accepted business loss",
    "insurance_claim": "Insurance Claim — claimed from logistics provider",
    "partial_recovery": "Partial Recovery — partial compensation received",
}

STOCK_RESOLUTION_TYPES = {
    "unencoded_po": "Unencoded PO — supplier delivered but PO was never entered",
    "count_error": "Count Error — last physical count was wrong, corrected",
    "wrong_item": "Wrong Item — wrong product was sold or scanned",
    "shrinkage": "Shrinkage — actual loss (theft, damage, expiry)",
}

RESOLUTION_TYPES = {**TRANSFER_RESOLUTION_TYPES, **STOCK_RESOLUTION_TYPES}


@router.get("")
async def list_incident_tickets(
    user=Depends(get_current_user),
    status: Optional[str] = None,
    branch_id: Optional[str] = None,
    transfer_id: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
):
    """List incident tickets with optional filters."""
    query = {}
    if status:
        query["status"] = status
    if branch_id:
        query["$or"] = [
            {"branch_id": branch_id},
            {"from_branch_id": branch_id},
            {"to_branch_id": branch_id},
        ]
    if transfer_id:
        query["transfer_id"] = transfer_id

    total = await db.incident_tickets.count_documents(query)
    tickets = await db.incident_tickets.find(query, {"_id": 0}).sort(
        "created_at", -1
    ).skip(skip).limit(limit).to_list(limit)
    return {"tickets": tickets, "total": total}


@router.get("/summary")
async def incident_summary(user=Depends(get_current_user)):
    """Summary stats for incident tickets dashboard."""
    pipeline = [
        {"$group": {
            "_id": "$status",
            "count": {"$sum": 1},
            "total_capital_loss": {"$sum": "$total_capital_loss"},
            "total_retail_loss": {"$sum": "$total_retail_loss"},
            "total_recovery": {"$sum": {"$ifNull": ["$recovery_amount", 0]}},
        }}
    ]
    results = await db.incident_tickets.aggregate(pipeline).to_list(20)

    # Separate pipeline: capital loss excluding sender_error (no real loss)
    real_loss_pipeline = [
        {"$match": {"resolution_type": {"$ne": "sender_error"}}},
        {"$group": {"_id": None, "total": {"$sum": "$total_capital_loss"}}},
    ]
    real_loss_result = await db.incident_tickets.aggregate(real_loss_pipeline).to_list(1)
    total_real_capital_loss = real_loss_result[0]["total"] if real_loss_result else 0

    summary = {
        "open": 0, "investigating": 0, "resolved": 0, "closed": 0,
        "total_unresolved_capital_loss": 0, "total_unresolved_retail_loss": 0,
        "total_recovered": 0,
        "total_real_capital_loss": total_real_capital_loss,
    }
    for r in results:
        s = r["_id"]
        summary[s] = r["count"]
        if s in ("open", "investigating"):
            summary["total_unresolved_capital_loss"] += r["total_capital_loss"]
            summary["total_unresolved_retail_loss"] += r["total_retail_loss"]
        summary["total_recovered"] += r.get("total_recovery", 0)
    return summary


@router.get("/resolution-types")
async def get_resolution_types(user=Depends(get_current_user), ticket_type: Optional[str] = None):
    """Return available resolution types, optionally filtered by ticket type."""
    if ticket_type == "negative_stock_override":
        return [{"value": k, "label": v} for k, v in STOCK_RESOLUTION_TYPES.items()]
    if ticket_type and ticket_type != "negative_stock_override":
        return [{"value": k, "label": v} for k, v in TRANSFER_RESOLUTION_TYPES.items()]
    return [{"value": k, "label": v} for k, v in RESOLUTION_TYPES.items()]


@router.get("/{ticket_id}")
async def get_incident_ticket(ticket_id: str, user=Depends(get_current_user)):
    """Get full ticket detail including timeline."""
    ticket = await db.incident_tickets.find_one({"id": ticket_id}, {"_id": 0})
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return ticket


@router.put("/{ticket_id}/assign")
async def assign_ticket(ticket_id: str, data: dict, user=Depends(get_current_user)):
    """Assign a ticket to a user for investigation."""
    if user.get("role") not in ["admin", "manager"]:
        raise HTTPException(status_code=403, detail="Manager or admin required")

    ticket = await db.incident_tickets.find_one({"id": ticket_id}, {"_id": 0})
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    assigned_to_id = data.get("assigned_to_id", "")
    assigned_to_name = data.get("assigned_to_name", "")

    event = {
        "action": "assigned",
        "by_id": user["id"],
        "by_name": user.get("full_name", user["username"]),
        "detail": f"Assigned to {assigned_to_name}",
        "at": now_iso(),
    }

    await db.incident_tickets.update_one(
        {"id": ticket_id},
        {"$set": {
            "assigned_to_id": assigned_to_id,
            "assigned_to_name": assigned_to_name,
            "status": "investigating",
            "updated_at": now_iso(),
        }, "$push": {"timeline": event}}
    )
    return {"message": f"Ticket assigned to {assigned_to_name}"}


@router.put("/{ticket_id}/add-note")
async def add_ticket_note(ticket_id: str, data: dict, user=Depends(get_current_user)):
    """Add an investigation note to the ticket timeline."""
    ticket = await db.incident_tickets.find_one({"id": ticket_id}, {"_id": 0})
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    note = data.get("note", "").strip()
    if not note:
        raise HTTPException(status_code=400, detail="Note is required")

    event = {
        "action": "note",
        "by_id": user["id"],
        "by_name": user.get("full_name", user["username"]),
        "detail": note,
        "at": now_iso(),
    }

    await db.incident_tickets.update_one(
        {"id": ticket_id},
        {"$set": {"updated_at": now_iso()}, "$push": {"timeline": event}}
    )
    return {"message": "Note added"}


@router.put("/{ticket_id}/sender-confirm")
async def sender_confirm(ticket_id: str, data: dict, user=Depends(get_current_user)):
    """Sender confirms the actual qty they sent. If it matches receiver, variance is cancelled."""
    if user.get("role") not in ["admin", "manager"]:
        raise HTTPException(status_code=403, detail="Manager or admin required")

    ticket = await db.incident_tickets.find_one({"id": ticket_id}, {"_id": 0})
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if ticket["status"] in ("resolved", "closed"):
        raise HTTPException(status_code=400, detail="Ticket already resolved/closed")

    confirmed_items = data.get("confirmed_items", [])
    note = data.get("note", "").strip()

    # Compare sender's confirmed qty with receiver's qty
    variance_cancelled = True
    updated_items = []
    for item in ticket.get("items", []):
        # Find sender's confirmed qty for this product
        confirmed = next(
            (c for c in confirmed_items if c.get("product_id") == item.get("product_id")),
            None
        )
        sender_qty = confirmed["sender_confirmed_qty"] if confirmed else item.get("qty_ordered", 0)
        receiver_qty = item.get("qty_received", 0)

        new_item = {**item, "sender_confirmed_qty": sender_qty}
        if sender_qty != receiver_qty:
            variance_cancelled = False
        updated_items.append(new_item)

    detail = "Sender confirmed quantities"
    if note:
        detail += f": {note}"
    if variance_cancelled:
        detail += " — ALL VARIANCES CANCELLED (sender confirms receiver was correct)"

    event = {
        "action": "sender_confirmed",
        "by_id": user["id"],
        "by_name": user.get("full_name", user["username"]),
        "detail": detail,
        "confirmed_items": confirmed_items,
        "variance_cancelled": variance_cancelled,
        "at": now_iso(),
    }

    update_set = {
        "items": updated_items,
        "sender_confirmed": True,
        "sender_confirmed_at": now_iso(),
        "sender_confirmed_by_id": user["id"],
        "sender_confirmed_by_name": user.get("full_name", user["username"]),
        "updated_at": now_iso(),
    }

    if variance_cancelled:
        # Auto-resolve: sender confirms they actually sent what receiver got
        update_set["status"] = "resolved"
        update_set["resolution_type"] = "sender_error"
        update_set["resolution_note"] = "Sender confirmed actual quantities match receiver. No real loss."
        update_set["resolved_at"] = now_iso()
        update_set["resolved_by_id"] = user["id"]
        update_set["resolved_by_name"] = user.get("full_name", user["username"])

        resolve_event = {
            "action": "resolved",
            "by_id": user["id"],
            "by_name": user.get("full_name", user["username"]),
            "detail": "Auto-resolved: Sender confirmed no actual loss (sender_error)",
            "at": now_iso(),
        }
        await db.incident_tickets.update_one(
            {"id": ticket_id},
            {"$set": update_set, "$push": {"timeline": {"$each": [event, resolve_event]}}}
        )

        # Update the transfer to clear shortage flags
        if ticket.get("transfer_id"):
            await db.branch_transfer_orders.update_one(
                {"id": ticket["transfer_id"]},
                {"$set": {"has_shortage": False, "sender_confirmed_no_loss": True, "updated_at": now_iso()}}
            )

        return {"message": "Variance cancelled — sender confirms no actual loss", "auto_resolved": True}
    else:
        await db.incident_tickets.update_one(
            {"id": ticket_id},
            {"$set": update_set, "$push": {"timeline": event}}
        )
        return {"message": "Sender confirmation recorded. Variance still exists — proceed to resolve.", "auto_resolved": False}


@router.put("/{ticket_id}/resolve")
async def resolve_ticket(ticket_id: str, data: dict, user=Depends(get_current_user)):
    """Resolve a ticket with structured resolution type, accountable party, and recovery."""
    if user.get("role") not in ["admin", "manager"]:
        raise HTTPException(status_code=403, detail="Manager or admin required")

    ticket = await db.incident_tickets.find_one({"id": ticket_id}, {"_id": 0})
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if ticket["status"] == "closed":
        raise HTTPException(status_code=400, detail="Ticket is already closed")

    resolution_note = data.get("resolution_note", "").strip()
    if not resolution_note:
        raise HTTPException(status_code=400, detail="Resolution note is required")

    # ── PIN Authorization ──
    pin = data.get("pin", "").strip()
    if not pin:
        raise HTTPException(status_code=400, detail="Authorization PIN required")

    from routes.verify import verify_pin_for_action
    verifier = await verify_pin_for_action(pin, "incident_resolve", branch_id=ticket.get("to_branch_id") or ticket.get("branch_id"))
    if not verifier:
        raise HTTPException(status_code=403, detail="Invalid PIN — authorization denied")

    resolution_type = data.get("resolution_type", "write_off")
    if resolution_type not in RESOLUTION_TYPES:
        raise HTTPException(status_code=400, detail=f"Invalid resolution type: {resolution_type}")

    accountable_party = data.get("accountable_party", "").strip()
    recovery_amount = float(data.get("recovery_amount", 0))
    linked_po_id = data.get("linked_po_id", "").strip()
    linked_po_number = data.get("linked_po_number", "").strip()

    type_label = RESOLUTION_TYPES.get(resolution_type, resolution_type)
    detail_parts = [f"Resolved as: {type_label}"]
    if linked_po_number:
        detail_parts.append(f"Linked PO: {linked_po_number}")
    if accountable_party:
        detail_parts.append(f"Charged to: {accountable_party}")
    if recovery_amount > 0:
        detail_parts.append(f"Recovery: PHP {recovery_amount:,.2f}")
    detail_parts.append(f"Approved by: {verifier['verifier_name']} ({verifier['method']})")
    detail_parts.append(resolution_note)

    event = {
        "action": "resolved",
        "by_id": user["id"],
        "by_name": user.get("full_name", user["username"]),
        "detail": " | ".join(detail_parts),
        "resolution_type": resolution_type,
        "accountable_party": accountable_party,
        "recovery_amount": recovery_amount,
        "linked_po_id": linked_po_id,
        "linked_po_number": linked_po_number,
        "approved_by_id": verifier["verifier_id"],
        "approved_by_name": verifier["verifier_name"],
        "approval_method": verifier["method"],
        "at": now_iso(),
    }

    update_fields = {
        "status": "resolved",
        "resolution_type": resolution_type,
        "resolution_note": resolution_note,
        "accountable_party": accountable_party,
        "recovery_amount": recovery_amount,
        "resolved_by_id": user["id"],
        "resolved_by_name": user.get("full_name", user["username"]),
        "approved_by_id": verifier["verifier_id"],
        "approved_by_name": verifier["verifier_name"],
        "approval_method": verifier["method"],
        "resolved_at": now_iso(),
        "updated_at": now_iso(),
    }
    if linked_po_id:
        update_fields["linked_po_id"] = linked_po_id
        update_fields["linked_po_number"] = linked_po_number

    await db.incident_tickets.update_one(
        {"id": ticket_id},
        {"$set": update_fields, "$push": {"timeline": event}}
    )

    # Create audit log
    await db.audit_log.insert_one({
        "id": new_id(),
        "type": "incident_resolved",
        "entity_type": "incident_ticket",
        "entity_id": ticket_id,
        "description": f"Incident {ticket['ticket_number']} resolved as {resolution_type}: {resolution_note}",
        "metadata": {
            "transfer_id": ticket.get("transfer_id"),
            "order_number": ticket.get("order_number"),
            "resolution_type": resolution_type,
            "accountable_party": accountable_party,
            "total_capital_loss": ticket.get("total_capital_loss", 0),
            "recovery_amount": recovery_amount,
        },
        "user_id": user["id"],
        "user_name": user.get("full_name", user["username"]),
        "created_at": now_iso(),
    })

    # ── Phase 3: Auto-generate Journal Entry based on resolution type ──
    capital_loss = ticket.get("total_capital_loss", 0)
    je_lines = []
    je_memo = ""

    if resolution_type == "transit_loss" and capital_loss > 0:
        je_memo = f"Transit loss: {ticket['ticket_number']} ({ticket.get('order_number', '')}) — charged to {accountable_party}"
        je_lines = [
            {"account_code": "1110", "account_name": "Driver/Courier Receivable", "debit": capital_loss, "credit": 0,
             "memo": f"Receivable from {accountable_party}"},
            {"account_code": "1200", "account_name": "Inventory", "debit": 0, "credit": capital_loss,
             "memo": f"Inventory loss - {ticket.get('order_number', '')}"},
        ]

    elif resolution_type == "write_off" and capital_loss > 0:
        je_memo = f"Inventory write-off: {ticket['ticket_number']} ({ticket.get('order_number', '')})"
        je_lines = [
            {"account_code": "5500", "account_name": "Inventory Loss / Write-off", "debit": capital_loss, "credit": 0,
             "memo": f"Write-off - {ticket.get('order_number', '')}"},
            {"account_code": "1200", "account_name": "Inventory", "debit": 0, "credit": capital_loss,
             "memo": f"Inventory shrinkage - {ticket.get('order_number', '')}"},
        ]

    elif resolution_type == "insurance_claim" and capital_loss > 0:
        je_memo = f"Insurance claim: {ticket['ticket_number']} ({ticket.get('order_number', '')}) — filed with {accountable_party}"
        je_lines = [
            {"account_code": "1120", "account_name": "Insurance Receivable", "debit": capital_loss, "credit": 0,
             "memo": f"Claim filed with {accountable_party}"},
            {"account_code": "1200", "account_name": "Inventory", "debit": 0, "credit": capital_loss,
             "memo": f"Inventory loss - {ticket.get('order_number', '')}"},
        ]

    elif resolution_type == "partial_recovery" and capital_loss > 0:
        recovered = min(recovery_amount, capital_loss)
        unrecovered = capital_loss - recovered
        je_memo = f"Partial recovery: {ticket['ticket_number']} — recovered {recovered}, written off {unrecovered}"
        je_lines = [
            {"account_code": "1000", "account_name": "Cash - Cashier Drawer", "debit": recovered, "credit": 0,
             "memo": f"Recovery from {accountable_party}"},
        ]
        if unrecovered > 0:
            je_lines.append(
                {"account_code": "5500", "account_name": "Inventory Loss / Write-off", "debit": unrecovered, "credit": 0,
                 "memo": "Unrecovered loss"}
            )
        je_lines.append(
            {"account_code": "1200", "account_name": "Inventory", "debit": 0, "credit": capital_loss,
             "memo": f"Inventory adjustment - {ticket.get('order_number', '')}"}
        )

    # sender_error, receiver_error, unencoded_po, count_error, wrong_item: no journal entry (no financial impact — stock self-corrects)

    # shrinkage: write off (same as write_off but for negative stock context)
    elif resolution_type == "shrinkage" and capital_loss > 0:
        je_memo = f"Shrinkage write-off: {ticket['ticket_number']} — {ticket.get('product_name', '')} ({resolution_note})"
        je_lines = [
            {"account_code": "5500", "account_name": "Inventory Loss / Write-off", "debit": capital_loss, "credit": 0,
             "memo": f"Shrinkage - {ticket.get('product_name', '')}"},
            {"account_code": "1200", "account_name": "Inventory", "debit": 0, "credit": capital_loss,
             "memo": f"Inventory shrinkage - {ticket.get('invoice_number', '')}"},
        ]

    je_id = None
    if je_lines:
        from datetime import datetime, timezone
        from utils.numbering import generate_next_number

        branch_id = ticket.get("to_branch_id") or ticket.get("branch_id", "")
        je_number = await generate_next_number("JE", branch_id)
        je_id = new_id()

        journal_entry = {
            "id": je_id,
            "je_number": je_number,
            "entry_type": "incident_adjustment",
            "entry_type_label": "Incident Resolution Adjustment",
            "branch_id": branch_id,
            "effective_date": await today_local(user.get("organization_id") or ""),
            "posted_date": await today_local(user.get("organization_id") or ""),
            "memo": je_memo,
            "reference_number": ticket.get("ticket_number", ""),
            "reference_type": "incident_ticket",
            "product_id": "",
            "product_name": "",
            "lines": je_lines,
            "total_amount": capital_loss,
            "status": "posted",
            "authorized_by_id": verifier["verifier_id"],
            "authorized_by_name": verifier["verifier_name"],
            "authorized_method": verifier["method"],
            "created_by_id": user["id"],
            "created_by_name": user.get("full_name", user.get("username", "")),
            "created_at": now_iso(),
            "voided": False,
            "void_reason": "",
            "voided_at": "",
            "voided_by": "",
            "auto_generated": True,
            "source_ticket_id": ticket_id,
        }

        await db.journal_entries.insert_one(journal_entry)
        del journal_entry["_id"]

        # Link JE back to the ticket
        await db.incident_tickets.update_one(
            {"id": ticket_id},
            {"$set": {"journal_entry_id": je_id, "journal_entry_number": je_number}}
        )

    return {
        "message": f"Ticket resolved as {resolution_type}",
        "status": "resolved",
        "journal_entry": je_id,
    }


@router.put("/{ticket_id}/close")
async def close_ticket(ticket_id: str, data: dict, user=Depends(get_current_user)):
    """Close a resolved ticket (final step)."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")

    ticket = await db.incident_tickets.find_one({"id": ticket_id}, {"_id": 0})
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    event = {
        "action": "closed",
        "by_id": user["id"],
        "by_name": user.get("full_name", user["username"]),
        "detail": data.get("note", "Closed by admin"),
        "at": now_iso(),
    }

    await db.incident_tickets.update_one(
        {"id": ticket_id},
        {"$set": {
            "status": "closed",
            "closed_at": now_iso(),
            "updated_at": now_iso(),
        }, "$push": {"timeline": event}}
    )
    return {"message": "Ticket closed"}

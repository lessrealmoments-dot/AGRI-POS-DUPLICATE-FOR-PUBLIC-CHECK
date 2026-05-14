"""
Pickup-Ready SMS — manual customer notification for prepared orders.

Use case: a customer pays upfront for an order they'll collect later
("ipreparé na lang, kukunin namin mamaya"). The cashier ticks items as
prepared, then taps "Send Pickup SMS" from the terminal to tell the
customer their order is ready.

Rate-limit policy (anti-abuse — protects both store and customer):
  * Maximum **3 sends** per invoice (lifetime, no reset).
  * **5-minute cooldown** between consecutive sends on the same invoice.
  * **Manual trigger only** — no auto-send, no scheduled retry.

The frontend confirmation modal ("Are you sure the items are ready?")
prevents accidental taps; the server-side counters are the source of
truth and protect against any client-side bypass attempts.
"""
from fastapi import APIRouter, HTTPException, Depends
from datetime import datetime, timezone, timedelta

from config import db
from utils import get_current_user, now_iso
from utils.terminal_guard import require_terminal_session

router = APIRouter()


MAX_SENDS = 3
COOLDOWN_SECONDS = 5 * 60  # 5 minutes


def _parse_iso(s: str):
    """ISO → aware datetime; returns None if unparseable."""
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


@router.post("/invoices/{invoice_id}/send-pickup-sms")
async def send_pickup_sms(
    invoice_id: str,
    user: dict = Depends(get_current_user),
    _terminal: dict = Depends(require_terminal_session),
):
    """
    Send a pickup-ready SMS to the invoice's customer.

    Returns:
      {
        "success": True,
        "sent_count":     int,    # how many sends this invoice has now used
        "remaining":      int,    # MAX_SENDS - sent_count
        "next_allowed_at": ISO|None,  # earliest next send (UTC)
        "phones":         [str],  # phones the SMS was queued to
      }

    Errors:
      400 — no customer linked / no phone numbers on file.
      404 — invoice not found.
      429 — cooldown active OR send-count exhausted; response detail
            includes `retry_after_seconds` and `remaining` so the UI
            can render a friendly countdown.
    """
    invoice = await db.invoices.find_one({"id": invoice_id}, {"_id": 0})
    if not invoice:
        raise HTTPException(404, "Invoice not found")

    customer_id = invoice.get("customer_id") or ""
    if not customer_id:
        raise HTTPException(400, "No customer linked to this invoice — pickup SMS requires a customer account.")

    customer = await db.customers.find_one({"id": customer_id}, {"_id": 0})
    if not customer:
        raise HTTPException(400, "Customer not found")

    phones = customer.get("phones") or (
        [customer["phone"]] if customer.get("phone") else []
    )
    phones = [p for p in phones if p and p.strip()]
    if not phones:
        raise HTTPException(400, "Customer has no phone number on file.")

    sent_count = int(invoice.get("pickup_sms_count", 0) or 0)
    if sent_count >= MAX_SENDS:
        raise HTTPException(
            status_code=429,
            detail={
                "message": f"Pickup SMS limit reached ({MAX_SENDS}/{MAX_SENDS}). No more sends allowed for this invoice.",
                "sent_count": sent_count,
                "remaining": 0,
                "retry_after_seconds": None,  # never — limit is lifetime
            },
        )

    last_sent_at = _parse_iso(invoice.get("pickup_sms_last_sent_at", ""))
    if last_sent_at:
        now = datetime.now(timezone.utc)
        elapsed = (now - last_sent_at).total_seconds()
        if elapsed < COOLDOWN_SECONDS:
            retry_after = int(COOLDOWN_SECONDS - elapsed)
            mins = retry_after // 60
            secs = retry_after % 60
            raise HTTPException(
                status_code=429,
                detail={
                    "message": (
                        f"Please wait {mins}m {secs:02d}s before sending again. "
                        f"({MAX_SENDS - sent_count} send/s remaining after cooldown.)"
                    ),
                    "sent_count": sent_count,
                    "remaining": MAX_SENDS - sent_count,
                    "retry_after_seconds": retry_after,
                },
            )

    # ── Resolve org + branch context for SMS dispatch ───────────────────────
    branch_id = invoice.get("branch_id", "")
    from routes.sms_hooks import (
        _resolve_org_id, get_company_name, get_branch_name,
    )
    from routes.sms import queue_sms

    org_id = await _resolve_org_id(branch_id)
    company_name = await get_company_name(org_id)
    branch_name = await get_branch_name(branch_id)

    variables = {
        "customer_name":  customer.get("name", invoice.get("customer_name", "")),
        "invoice_number": invoice.get("invoice_number", ""),
        "company_name":   company_name,
        "branch_name":    branch_name,
    }

    queued_phones = []
    for phone in phones:
        await queue_sms(
            template_key="pickup_ready",
            customer_id=customer_id,
            customer_name=customer.get("name", ""),
            phone=phone,
            variables=variables,
            organization_id=org_id,
            branch_id=branch_id,
            branch_name=branch_name,
            trigger="manual",
            trigger_ref=invoice.get("id", ""),
            # Dedup by minute-bucket so the 5-min cooldown is enforced even
            # if two terminals race on the same invoice.
            dedup_key=(
                f"pickup_ready:{invoice.get('id','')}:{phone}:"
                f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M')[:11]}"
            ),
        )
        queued_phones.append(phone)

    # ── Record send for rate-limit tracking ─────────────────────────────────
    sent_at_iso = now_iso()
    new_count = sent_count + 1
    history_entry = {
        "sent_at":   sent_at_iso,
        "sent_by":   user.get("id", ""),
        "sent_by_name": user.get("full_name", "") or user.get("username", ""),
        "phones":    queued_phones,
        "send_index": new_count,
    }
    await db.invoices.update_one(
        {"id": invoice_id},
        {
            "$set": {
                "pickup_sms_count":         new_count,
                "pickup_sms_last_sent_at":  sent_at_iso,
            },
            "$push": {"pickup_sms_history": history_entry},
        },
    )

    next_allowed_at = (
        datetime.now(timezone.utc) + timedelta(seconds=COOLDOWN_SECONDS)
    ).isoformat() if new_count < MAX_SENDS else None

    return {
        "success":         True,
        "sent_count":      new_count,
        "remaining":       MAX_SENDS - new_count,
        "next_allowed_at": next_allowed_at,
        "phones":          queued_phones,
    }


@router.get("/invoices/{invoice_id}/pickup-sms-status")
async def get_pickup_sms_status(
    invoice_id: str,
    user: dict = Depends(get_current_user),
):
    """
    Lightweight read used by the terminal UI to hydrate the button state
    on page load (so users see the current remaining-send count and any
    active cooldown countdown without having to click first).
    """
    inv = await db.invoices.find_one(
        {"id": invoice_id},
        {"_id": 0, "pickup_sms_count": 1, "pickup_sms_last_sent_at": 1,
         "customer_id": 1, "pickup_sms_history": 1},
    )
    if not inv:
        raise HTTPException(404, "Invoice not found")

    sent_count = int(inv.get("pickup_sms_count", 0) or 0)
    last_sent_at = _parse_iso(inv.get("pickup_sms_last_sent_at", ""))
    retry_after = 0
    if last_sent_at:
        now = datetime.now(timezone.utc)
        elapsed = (now - last_sent_at).total_seconds()
        if elapsed < COOLDOWN_SECONDS:
            retry_after = int(COOLDOWN_SECONDS - elapsed)

    return {
        "sent_count":         sent_count,
        "remaining":          max(0, MAX_SENDS - sent_count),
        "max_sends":          MAX_SENDS,
        "retry_after_seconds": retry_after,
        "has_customer":       bool(inv.get("customer_id")),
        "history":            inv.get("pickup_sms_history", []) or [],
    }

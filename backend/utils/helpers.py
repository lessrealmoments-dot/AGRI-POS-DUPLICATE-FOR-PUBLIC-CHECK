"""
Common helper functions used across the application.
"""
import uuid
from datetime import datetime, timezone, timedelta
from fastapi import HTTPException
from config import db, _raw_db, get_org_context, set_org_context


# ── C-9 (Audit 2026-02): central voided-invoice payment guard ────────────────
# A payment must not be applied to an invoice in any of these statuses. Three
# routes used to allow this (record_invoice_payment, pay_receivable,
# receive_customer_payment), causing customer.balance to silently go negative
# and the wallet to receive cash for a sale that was already reversed.
#
# `for_preparation` / `processing` / `error_partial_write` are in-flight states
# (drafts, mid-finalize, partial-write tombstones) and also reject payments —
# a finalized status is required first.
NON_PAYABLE_INVOICE_STATUSES = {
    "voided",
    "cancelled",
    "deleted",
    "for_preparation",
    "processing",
    "error_partial_write",
}


def assert_invoice_payable(inv: dict) -> None:
    """Raise HTTPException 400 when the invoice cannot accept a payment.

    Used by every payment-write route so the rule is enforced regardless of
    which entry point a caller hits (terminal, web POS, accounting page, or a
    direct API request bypassing the frontend).
    """
    status = (inv.get("status") or "").lower()
    if status in NON_PAYABLE_INVOICE_STATUSES:
        inv_no = inv.get("invoice_number") or inv.get("id") or "?"
        raise HTTPException(
            status_code=400,
            detail=(
                f"Cannot record payment on invoice {inv_no}: status is '{status}'. "
                f"Only finalized open/partial/credit invoices can receive payments."
            ),
        )


# ── Phase 2C.2 (Audit 2026-02): guard payment edit/void on bad invoice ──────
def assert_invoice_payment_modifiable(inv: dict, action: str = "modify") -> None:
    """Raise HTTPException 400 when an existing payment record on this invoice
    cannot be modified or voided through the normal flow.

    Used by void_invoice_payment and modify-payment routes. A payment that
    belongs to a now-voided/cancelled/deleted invoice MUST NOT be silently
    mutated — restoring AR for a defunct invoice corrupts customer.balance.
    Operators must reconcile via the audit / reopen flow first.
    """
    status = (inv.get("status") or "").lower()
    if status in NON_PAYABLE_INVOICE_STATUSES:
        inv_no = inv.get("invoice_number") or inv.get("id") or "?"
        raise HTTPException(
            status_code=400,
            detail=(
                f"Cannot {action} payment on invoice {inv_no}: status is '{status}'. "
                f"Reopen / restore the invoice via the audit flow before editing payments."
            ),
        )


# ── Phase 2C.4 (Audit 2026-02): overpayment guard ───────────────────────────
OVERPAYMENT_TOLERANCE = 0.50  # ₱


def assert_payment_within_balance(inv: dict, amount: float) -> None:
    """Reject payments greater than the outstanding balance + tolerance.

    Prevents informal credits accumulating on customer.balance (negative AR).
    Tolerance covers rounding artefacts in cashier UIs.
    """
    bal = float(inv.get("balance") or 0)
    interest = float(inv.get("interest_accrued") or 0) + float(inv.get("penalties") or 0)
    payable = bal + interest
    if amount > payable + OVERPAYMENT_TOLERANCE:
        inv_no = inv.get("invoice_number") or inv.get("id") or "?"
        raise HTTPException(
            status_code=400,
            detail=(
                f"Overpayment rejected on invoice {inv_no}: amount ₱{amount:,.2f} "
                f"exceeds outstanding balance ₱{payable:,.2f} (tolerance ₱{OVERPAYMENT_TOLERANCE:.2f}). "
                f"Reduce the amount or use a separate credit-memo flow."
            ),
        )


# ── Phase 2C.1 (Audit 2026-02): payment idempotency helpers ─────────────────
async def payment_idempotency_lookup_or_reserve(
    key: str, organization_id: str, route_tag: str
):
    """Atomic lookup-or-reserve for a payment idempotency key.

    Returns:
      None                    — first caller, proceed with the payment.
      {"replay": True, ...}   — a previous identical request already completed;
                                caller MUST return that cached response.

    Raises HTTPException 409 if an in-flight request is still being processed.
    """
    if not key:
        return None
    from pymongo import ReturnDocument
    res = await _raw_db.payment_idempotency.find_one_and_update(
        {"key": key, "organization_id": organization_id, "route": route_tag},
        {"$setOnInsert": {
            "key": key,
            "organization_id": organization_id,
            "route": route_tag,
            "status": "in_flight",
            "response": None,
            "created_at": now_iso(),
        }},
        upsert=True,
        return_document=ReturnDocument.BEFORE,
    )
    if res is None:
        # We just inserted the marker → first caller.
        return None
    status = (res.get("status") or "").lower()
    if status == "completed":
        return {"replay": True, "response": res.get("response")}
    # in_flight from a concurrent caller — reject as 409.
    raise HTTPException(
        status_code=409,
        detail=(
            "A payment with this idempotency_key is already in flight. "
            "Wait for the original request to complete and retry."
        ),
    )


async def payment_idempotency_record(
    key: str, organization_id: str, route_tag: str, response: dict
) -> None:
    """Persist the completed response for a reserved idempotency key."""
    if not key:
        return
    await _raw_db.payment_idempotency.update_one(
        {"key": key, "organization_id": organization_id, "route": route_tag},
        {"$set": {
            "status": "completed",
            "response": response,
            "completed_at": now_iso(),
        }},
    )


async def payment_idempotency_release(
    key: str, organization_id: str, route_tag: str
) -> None:
    """Release a reservation if the payment failed before recording.

    Without this, an exception during the payment write would leave the
    key locked in-flight forever. Routes call this in their except block.
    """
    if not key:
        return
    await _raw_db.payment_idempotency.delete_one(
        {"key": key, "organization_id": organization_id,
         "route": route_tag, "status": "in_flight"},
    )


def now_iso():
    """Return current UTC timestamp in ISO format.

    Used for all `created_at`, `updated_at`, `voided_at`, `recorded_at`, etc.
    These are TRANSPORT timestamps — Mongo string-comparison ordering relies
    on ISO-8601 + UTC. NEVER change to local time. For DISPLAY, the frontend
    converts via formatDateTime (MM/DD/YYYY · hh:mm AM/PM). For FILTERING by
    a calendar day, use `today_local(org_id)` instead.
    """
    return datetime.now(timezone.utc).isoformat()


# ── Timezone-aware "today" helpers (Iter 238) ────────────────────────────────
# Background: every call to `datetime.now(timezone.utc).strftime("%Y-%m-%d")`
# was producing the UTC date, NOT the org's local date. From 12 AM to 8 AM
# Manila that meant `today` = yesterday-Manila, which corrupted "today's
# sales", silenced same-day reminder triggers, mis-stamped order_date
# defaults, and caused the user's 8 AM sales to display "00:00".
#
# These helpers resolve the org's timezone (defaults to Asia/Manila when no
# context) and return the LOCAL day or LOCAL time.
#
# IMPORTANT: do NOT use these for `created_at` / `updated_at` / `voided_at`
# style timestamps. Those MUST stay UTC ISO (transport layer). Only use
# these where you previously wrote `.strftime("%Y-%m-%d")` for "today" or
# `strftime("%H:%M:%S")` for a wall-clock time field.

def _local_now_for(org_id: str = "") -> datetime:
    """Return a timezone-aware datetime in the org's local zone.
    Synchronous so it's safe inside list-comprehensions and sync callsites.
    Falls back to Asia/Manila when no org context."""
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        return datetime.now(timezone.utc) + timedelta(hours=8)
    tz_name = "Asia/Manila"
    if org_id:
        try:
            # Cheap synchronous read via _raw_db's underlying pymongo handle
            # is not available here — async resolver lives in close_reminder.
            # For sync sites we accept the Asia/Manila default; async sites
            # use `today_local` / `now_local_iso` below which call the proper
            # async resolver.
            pass
        except Exception:
            pass
    try:
        return datetime.now(ZoneInfo(tz_name))
    except Exception:
        return datetime.now(timezone.utc) + timedelta(hours=8)


async def today_local(org_id: str = "") -> str:
    """Return TODAY as `YYYY-MM-DD` in the organization's local timezone.

    Replaces every `datetime.now(timezone.utc).strftime("%Y-%m-%d")` that
    was being used as a calendar-day boundary. The UTC version was
    yesterday-Manila for ~8 hours per day. Falls back to Asia/Manila when
    org_id is empty (super-admin / scheduler contexts)."""
    from routes.close_reminder import _resolve_org_timezone, _local_now_in
    tz = await _resolve_org_timezone(org_id) if org_id else "Asia/Manila"
    return _local_now_in(tz).strftime("%Y-%m-%d")


async def now_local_iso(org_id: str = "") -> str:
    """Return current time in org TZ as ISO-8601 with offset
    (e.g. `2026-05-05T08:30:00+08:00`).

    Used for fields meant to display wall-clock time directly without
    needing front-end TZ conversion."""
    from routes.close_reminder import _resolve_org_timezone, _local_now_in
    tz = await _resolve_org_timezone(org_id) if org_id else "Asia/Manila"
    return _local_now_in(tz).isoformat()


async def now_local_time_str(org_id: str = "") -> str:
    """Return current wall-clock time as `HH:MM:SS` in org TZ.
    Used for the `sales_log.time` display column."""
    from routes.close_reminder import _resolve_org_timezone, _local_now_in
    tz = await _resolve_org_timezone(org_id) if org_id else "Asia/Manila"
    return _local_now_in(tz).strftime("%H:%M:%S")


def utc_iso_to_local_time_str(iso_str: str, tz_name: str = "Asia/Manila") -> str:
    """Convert a UTC ISO timestamp to `HH:MM:SS` in the given local TZ.
    Used by the one-shot sales_log time-backfill migration."""
    if not iso_str:
        return ""
    try:
        from zoneinfo import ZoneInfo
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(ZoneInfo(tz_name)).strftime("%H:%M:%S")
    except Exception:
        # Last-resort offset fallback
        try:
            dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return (dt + timedelta(hours=8)).strftime("%H:%M:%S")
        except Exception:
            return ""


def new_id():
    """Generate a new UUID string."""
    return str(uuid.uuid4())


async def mark_price_reviewed(product_id: str, branch_id: str, source: str = ""):
    """
    Mark a product's pricing as 'reviewed' for a branch — clears the
    "Global Price" badge. Idempotent. Auto-called from PO/transfer/import/manual
    edit hooks. Upserts an inventory row if one doesn't exist yet.

    source: short tag like 'po', 'transfer', 'import', 'override', 'manual'
            (purely informational, stored on inventory.last_price_review_source).
    """
    if not product_id or not branch_id:
        return
    await db.inventory.update_one(
        {"product_id": product_id, "branch_id": branch_id},
        {
            "$set": {
                "price_reviewed_at": now_iso(),
                "last_price_review_source": source or "auto",
            },
            "$setOnInsert": {
                "id": str(uuid.uuid4()),
                "product_id": product_id,
                "branch_id": branch_id,
                "quantity": 0,
            },
        },
        upsert=True,
    )


async def ensure_org_context(branch_id: str = None, org_id: str = None):
    """Ensure org context is set. Super admins have None — resolve from branch or explicit org_id.
    Call this at the start of any write operation that could be triggered by a super admin."""
    if get_org_context():
        return  # Already set
    if org_id:
        set_org_context(org_id)
        return
    if branch_id:
        branch = await _raw_db.branches.find_one({"id": branch_id}, {"_id": 0, "organization_id": 1})
        if branch and branch.get("organization_id"):
            set_org_context(branch["organization_id"])


async def log_movement(product_id, branch_id, m_type, qty_change, ref_id, ref_number, price, user_id, user_name, notes="", reserved_qty_change=0):
    """
    Log a product movement (sale, purchase, adjustment, transfer, release, etc.).

    qty_change          — change to inventory.quantity  (negative = out, positive = in)
    reserved_qty_change — change to inventory.reserved_qty (negative = released to customer, positive = reserved at sale)
                          Defaults to 0 for all existing callers. Only sale_release uses a non-zero value.
    """
    # Ensure org context — super admin operations would otherwise create orphaned records
    await ensure_org_context(branch_id=branch_id)

    await db.movements.insert_one({
        "id": new_id(),
        "product_id": product_id,
        "branch_id": branch_id,
        "type": m_type,
        "quantity_change": qty_change,
        "reserved_qty_change": reserved_qty_change,
        "reference_id": ref_id,
        "reference_number": ref_number,
        "price_at_time": float(price) if price else 0,
        "notes": notes,
        "user_id": user_id,
        "user_name": user_name,
        "created_at": now_iso()
    })


async def log_sale_items(branch_id, date, items, invoice_number, customer_name, payment_method, cashier_name, split_meta=None, partial_meta=None):
    """Record each sold item to sequential sales log."""
    last = await db.sales_log.find_one(
        {"branch_id": branch_id, "date": date},
        {"_id": 0},
        sort=[("sequence", -1)]
    )
    seq = last["sequence"] if last else 0
    running = last["running_total"] if last else 0

    # Resolve org timezone ONCE per batch so the time string matches the
    # local wall-clock the cashier actually saw on the screen — not UTC.
    # (Iter 238 fix: previously stored `datetime.now(timezone.utc).strftime`
    # which produced "00:00:00" for an 8 AM Manila sale.)
    org_id = get_org_context() or ""
    local_time_str = await now_local_time_str(org_id)
    timestamp_local = await now_local_iso(org_id)

    for item in items:
        seq += 1
        lt = float(item.get("total", item.get("quantity", 0) * item.get("rate", item.get("price", 0))))
        running = round(running + lt, 2)
        entry = {
            "id": new_id(),
            "branch_id": branch_id,
            "date": date,
            "sequence": seq,
            "time": local_time_str,
            "timestamp": timestamp_local,
            "timestamp_utc": now_iso(),
            "product_name": item.get("product_name", ""),
            "product_id": item.get("product_id", ""),
            "quantity": float(item.get("quantity", 0)),
            "unit": item.get("unit", ""),
            "unit_price": float(item.get("rate", item.get("price", 0))),
            "discount": float(item.get("discount_amount", 0)),
            "line_total": lt,
            "running_total": running,
            "category": item.get("category", ""),
            "invoice_number": invoice_number,
            "customer_name": customer_name,
            "payment_method": (payment_method or "cash").lower(),
            "cashier_name": cashier_name,
        }
        # Store split ratio so daily log can decompose into cash + digital
        if split_meta:
            entry["split_cash_amount"] = split_meta.get("cash_amount", 0)
            entry["split_digital_amount"] = split_meta.get("digital_amount", 0)
            entry["split_digital_platform"] = split_meta.get("digital_platform", "")
            entry["split_grand_total"] = split_meta.get("grand_total", 0)
        # Store partial ratio so daily log can decompose into cash + credit
        if partial_meta:
            entry["partial_cash_amount"] = partial_meta.get("cash_amount", 0)
            entry["partial_credit_amount"] = partial_meta.get("credit_amount", 0)
            entry["partial_grand_total"] = partial_meta.get("grand_total", 0)
        await db.sales_log.insert_one(entry)


async def get_active_date(branch_id):
    """Return today's date unless closed, then return next day.
    Uses the org's local TZ so the day boundary lines up with the cashier's
    wall clock — not UTC. (Iter 238 fix.)"""
    # Resolve via the branch's organization_id (sync sites may not have
    # _current_org_id set, so we read it off the branch row).
    br = await _raw_db.branches.find_one(
        {"id": branch_id}, {"_id": 0, "organization_id": 1}
    )
    org_id = (br or {}).get("organization_id") or get_org_context() or ""
    today = await today_local(org_id)
    closed = await db.daily_closings.find_one(
        {"branch_id": branch_id, "date": today, "status": "closed"},
        {"_id": 0}
    )
    if closed:
        from routes.close_reminder import _resolve_org_timezone, _local_now_in
        tz = await _resolve_org_timezone(org_id) if org_id else "Asia/Manila"
        next_day = (_local_now_in(tz) + timedelta(days=1)).strftime("%Y-%m-%d")
        return next_day
    return today


async def update_cashier_wallet(branch_id, amount, reference="", allow_negative=False):
    """
    Update cashier drawer wallet balance. Positive = cash in, negative = cash out.
    Raises ValueError if deduction would cause a negative balance (unless allow_negative=True).
    """
    wallet = await db.fund_wallets.find_one(
        {"branch_id": branch_id, "type": "cashier", "active": True},
        {"_id": 0}
    )
    if not wallet:
        wallet = {
            "id": new_id(),
            "branch_id": branch_id,
            "type": "cashier",
            "name": "Cashier Drawer",
            "balance": 0,
            "active": True,
            "created_at": now_iso()
        }
        await db.fund_wallets.insert_one(wallet)
        del wallet["_id"]

    current_balance = float(wallet.get("balance", 0))
    new_balance = round(current_balance + amount, 2)

    # Guard against negative balance on cash-out operations
    if amount < 0 and new_balance < 0 and not allow_negative:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail={
            "type": "insufficient_funds",
            "message": f"Cashier has ₱{current_balance:,.2f} but ₱{abs(amount):,.2f} is needed. "
                       f"Use the Safe or add a deposit to the cashier first.",
            "cashier_balance": current_balance,
            "required": abs(amount),
            "shortfall": round(abs(new_balance), 2),
            "suggestion": "safe",
        })

    await db.fund_wallets.update_one(
        {"id": wallet["id"]},
        {"$inc": {"balance": round(amount, 2)}}
    )
    await db.wallet_movements.insert_one({
        "id": new_id(),
        "wallet_id": wallet["id"],
        "branch_id": branch_id,
        "type": "cash_in" if amount >= 0 else "cash_out",
        "amount": round(amount, 2),
        "reference": reference,
        "balance_after": new_balance,
        "created_at": now_iso()
    })


# ── Digital Wallet ────────────────────────────────────────────────────────────

# Payment methods that route to digital wallet (everything non-cash except check)
DIGITAL_PAYMENT_METHODS = {
    "gcash", "maya", "paymaya", "bank transfer", "instapay", "pesonet",
    "paypal", "shopee pay", "grabpay", "coins.ph", "seabank",
    "digital", "e-wallet", "online transfer", "mobile payment",
}

def is_digital_payment(method: str) -> bool:
    """Return True if the payment method should route to the digital wallet."""
    if not method:
        return False
    m = method.lower().strip()
    # These are payment types or non-digital methods — NOT digital wallet targets
    NON_DIGITAL = {"cash", "check", "cheque", "credit", "partial", "split", ""}
    if m in NON_DIGITAL:
        return False
    return True  # Everything else (GCash, Maya, Bank Transfer, etc.) is digital



async def record_safe_movement(branch_id: str, amount: float, reference: str = ""):
    """
    Record a wallet_movements entry for the safe wallet.
    amount: negative for outflow, positive for inflow.
    """
    safe_wallet = await db.fund_wallets.find_one(
        {"branch_id": branch_id, "type": "safe", "active": True}, {"_id": 0}
    )
    if not safe_wallet:
        return
    # Compute current safe balance from lots
    lots = await db.safe_lots.find(
        {"wallet_id": safe_wallet["id"], "remaining_amount": {"$gt": 0}}, {"_id": 0, "remaining_amount": 1}
    ).to_list(500)
    balance_after = round(sum(lot["remaining_amount"] for lot in lots), 2)
    await db.wallet_movements.insert_one({
        "id": new_id(),
        "wallet_id": safe_wallet["id"],
        "branch_id": branch_id,
        "type": "cash_in" if amount >= 0 else "cash_out",
        "amount": round(amount, 2),
        "reference": reference,
        "balance_after": balance_after,
        "created_at": now_iso(),
    })



async def update_digital_wallet(branch_id: str, amount: float, reference: str = "",
                                platform: str = "", sender: str = "", ref_number: str = ""):
    """
    Update the branch digital wallet balance (GCash, Maya, Bank Transfer, etc.).
    Positive = collection in, negative = reversal.
    Auto-creates the digital wallet if it doesn't exist.
    """
    wallet = await db.fund_wallets.find_one(
        {"branch_id": branch_id, "type": "digital", "active": True}, {"_id": 0}
    )
    if not wallet:
        wallet = {
            "id": new_id(),
            "branch_id": branch_id,
            "type": "digital",
            "name": "Digital / E-Wallet",
            "balance": 0.0,
            "active": True,
            "created_at": now_iso(),
        }
        await db.fund_wallets.insert_one(wallet)
        del wallet["_id"]

    new_balance = round(float(wallet.get("balance", 0)) + amount, 2)
    await db.fund_wallets.update_one(
        {"id": wallet["id"]},
        {"$inc": {"balance": round(amount, 2)}}
    )
    await db.wallet_movements.insert_one({
        "id": new_id(),
        "wallet_id": wallet["id"],
        "branch_id": branch_id,
        "type": "digital_in" if amount >= 0 else "digital_reversal",
        "amount": round(amount, 2),
        "reference": reference,
        "platform": platform,
        "sender": sender,
        "ref_number": ref_number,
        "balance_after": new_balance,
        "created_at": now_iso(),
    })


# ── Branch Wallet Provisioning ────────────────────────────────────────────────

WALLET_TEMPLATES = [
    {"type": "cashier", "name": "Cashier Drawer"},
    {"type": "safe",    "name": "Physical Safe"},
    {"type": "digital", "name": "Digital / E-Wallet"},
    {"type": "bank",    "name": "Bank Deposit Account"},
]


async def provision_branch_wallets(branch_id: str, branch_name: str = ""):
    """
    Ensure all 4 standard wallets exist for a branch.
    Safe to call multiple times — only creates missing wallets.
    """
    for tmpl in WALLET_TEMPLATES:
        exists = await db.fund_wallets.find_one(
            {"branch_id": branch_id, "type": tmpl["type"], "active": True}, {"_id": 0}
        )
        if not exists:
            await db.fund_wallets.insert_one({
                "id": new_id(),
                "branch_id": branch_id,
                "type": tmpl["type"],
                "name": tmpl["name"],
                "balance": 0.0,
                "active": True,
                "created_at": now_iso(),
            })


async def get_product_price(product: dict, branch_id: str, scheme: str) -> float:
    """
    Get the effective price for a product at a specific branch.
    Fallback chain: branch_prices override → product.prices (global default)
    """
    if branch_id:
        override = await db.branch_prices.find_one(
            {"product_id": product["id"], "branch_id": branch_id}, {"_id": 0}
        )
        if override and scheme in (override.get("prices") or {}):
            return float(override["prices"][scheme])
    return float((product.get("prices") or {}).get(scheme, product.get("cost_price", 0)))


async def get_branch_cost(product: dict, branch_id: str) -> float:
    """
    Get the effective cost/capital price for a product at a specific branch.

    Repacks: derive on-the-fly from parent's branch capital (parent_branch_cost
    ÷ units_per_parent + add_on_cost). This keeps repack capital always in sync
    with PO/transfer/manual updates to the parent — no migration or sync required.

    Fallback chain (non-repack):
        branch_prices.cost_price → product.cost_price (global)
    Fallback chain (repack):
        parent.branch_prices.cost_price → parent.cost_price → repack.cost_price (legacy)
    """
    if product.get("is_repack") and product.get("parent_id"):
        return await get_repack_capital(product, branch_id)

    if branch_id:
        override = await db.branch_prices.find_one(
            {"product_id": product["id"], "branch_id": branch_id}, {"_id": 0}
        )
        if override and override.get("cost_price") is not None:
            return float(override["cost_price"])
    return float(product.get("cost_price", 0))


async def get_repack_capital(repack: dict, branch_id: str) -> float:
    """
    Compute a repack's capital on-the-fly for a specific branch.

    Formula: parent_branch_cost ÷ units_per_parent + add_on_cost

    Fallback chain:
      1. parent.branch_prices.cost_price (Branch X)  → branch-specific live data
      2. parent.cost_price                            → global parent cost
      3. repack.cost_price                            → legacy stored repack cost
    """
    units_per_parent = float(repack.get("units_per_parent", 1) or 1)
    if units_per_parent <= 0:
        units_per_parent = 1
    add_on = float(repack.get("add_on_cost", 0) or 0)
    parent_id = repack.get("parent_id")

    parent_cost = None
    if parent_id and branch_id:
        bp = await db.branch_prices.find_one(
            {"product_id": parent_id, "branch_id": branch_id}, {"_id": 0}
        )
        if bp and bp.get("cost_price") is not None:
            parent_cost = float(bp["cost_price"])

    if parent_cost is None and parent_id:
        parent = await db.products.find_one({"id": parent_id}, {"_id": 0, "cost_price": 1})
        if parent and parent.get("cost_price") is not None:
            parent_cost = float(parent.get("cost_price", 0))

    if parent_cost is None:
        # Last-ditch: use legacy stored repack cost
        return float(repack.get("cost_price", 0) or 0)

    return round(parent_cost / units_per_parent + add_on, 4)

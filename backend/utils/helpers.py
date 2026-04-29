"""
Common helper functions used across the application.
"""
import uuid
from datetime import datetime, timezone, timedelta
from config import db, _raw_db, get_org_context, set_org_context


def now_iso():
    """Return current UTC timestamp in ISO format."""
    return datetime.now(timezone.utc).isoformat()


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
    
    for item in items:
        seq += 1
        lt = float(item.get("total", item.get("quantity", 0) * item.get("rate", item.get("price", 0))))
        running = round(running + lt, 2)
        entry = {
            "id": new_id(),
            "branch_id": branch_id,
            "date": date,
            "sequence": seq,
            "time": datetime.now(timezone.utc).strftime("%H:%M:%S"),
            "timestamp": now_iso(),
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
    """Return today's date unless closed, then return next day."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    closed = await db.daily_closings.find_one(
        {"branch_id": branch_id, "date": today, "status": "closed"},
        {"_id": 0}
    )
    if closed:
        next_day = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")
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
    Fallback chain: branch_prices.cost_price → product.cost_price (global)
    """
    if branch_id:
        override = await db.branch_prices.find_one(
            {"product_id": product["id"], "branch_id": branch_id}, {"_id": 0}
        )
        if override and override.get("cost_price") is not None:
            return float(override["cost_price"])
    return float(product.get("cost_price", 0))

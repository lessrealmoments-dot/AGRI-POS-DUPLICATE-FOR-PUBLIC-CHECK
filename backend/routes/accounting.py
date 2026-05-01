"""
Accounting routes: Fund wallets, expenses, receivables, payables.
"""
from fastapi import APIRouter, Depends, HTTPException
from typing import Optional
from datetime import datetime, timezone, timedelta
from config import db
from utils import (
    get_current_user, check_perm, now_iso, new_id, update_cashier_wallet,
    update_digital_wallet, is_digital_payment, record_safe_movement,
    get_branch_filter, apply_branch_filter, verify_password,
    generate_next_number, assert_branch_access,
)
from utils.security import log_failed_pin_attempt

router = APIRouter(tags=["Accounting"])


def derive_fund_source(payment_method: str, explicit_fund_source: str = None) -> str:
    """Derive the correct fund source from the payment method.
    Cash/Check → cashier, Safe → safe, GCash/Maya/etc → digital.
    If an explicit fund_source is provided, use it as override."""
    if explicit_fund_source and explicit_fund_source in ("cashier", "safe", "digital"):
        return explicit_fund_source
    if not payment_method:
        return "cashier"
    pm = payment_method.lower().strip()
    if pm in ("safe", "vault", "from safe", "cash (from safe)"):
        return "safe"
    if is_digital_payment(pm):
        return "digital"
    return "cashier"


async def deduct_from_fund_source(branch_id: str, fund_source: str, amount: float,
                                   ref_text: str, payment_method: str = ""):
    """Deduct expense amount from the correct wallet based on fund_source."""
    if fund_source == "safe":
        safe_wallet = await db.fund_wallets.find_one(
            {"branch_id": branch_id, "type": "safe", "active": True}, {"_id": 0}
        )
        if not safe_wallet:
            raise HTTPException(status_code=404, detail="Safe wallet not found for this branch")
        lots = await db.safe_lots.find(
            {"wallet_id": safe_wallet["id"], "remaining_amount": {"$gt": 0}}, {"_id": 0}
        ).sort("remaining_amount", -1).to_list(500)
        safe_balance = sum(lot["remaining_amount"] for lot in lots)
        if safe_balance < amount:
            raise HTTPException(status_code=400, detail=f"Safe has ₱{safe_balance:,.2f}, need ₱{amount:,.2f}")
        remaining = amount
        for lot in lots:
            if remaining <= 0:
                break
            take = min(lot["remaining_amount"], remaining)
            await db.safe_lots.update_one({"id": lot["id"]}, {"$inc": {"remaining_amount": -take}})
            remaining -= take
        await record_safe_movement(branch_id, -amount, ref_text)
    elif fund_source == "digital":
        await update_digital_wallet(branch_id, -amount, ref_text,
                                     platform=payment_method or "digital")
    else:
        await update_cashier_wallet(branch_id, -amount, ref_text)


# Preset expense categories
EXPENSE_CATEGORIES = [
    "Utilities", "Rent", "Supplies", "Transportation", "Fuel/Gas",
    "Employee Advance", "Farm Expense", "Repairs & Maintenance",
    "Marketing", "Salaries & Wages", "Communication", "Insurance",
    "Professional Fees", "Taxes & Licenses", "Office Supplies",
    "Equipment", "Miscellaneous"
]


# ==================== FUND WALLETS ====================
@router.get("/fund-wallets")
async def list_fund_wallets(user=Depends(get_current_user), branch_id: Optional[str] = None):
    """
    List all fund wallets (Cashier, Safe, Digital, Bank).
    Bank balance hidden from non-admin roles for confidentiality.
    """
    query = {"active": True}
    if branch_id:
        query["branch_id"] = branch_id
    elif user.get("branch_id") and user.get("role") not in ["admin"]:
        query["branch_id"] = user["branch_id"]
    wallets = await db.fund_wallets.find(query, {"_id": 0}).to_list(50)
    for w in wallets:
        if w["type"] == "safe":
            lots = await db.safe_lots.find(
                {"wallet_id": w["id"], "remaining_amount": {"$gt": 0}}, {"_id": 0}
            ).to_list(500)
            w["balance"] = round(sum(lot["remaining_amount"] for lot in lots), 2)
            w["lots"] = lots
        # Confidentiality: hide bank balance from non-admins
        if w["type"] == "bank" and user.get("role") not in ["admin"]:
            w["balance"] = None
            w["balance_hidden"] = True
    return wallets


@router.post("/fund-wallets")
async def create_fund_wallet(data: dict, user=Depends(get_current_user)):
    """Create a new fund wallet."""
    check_perm(user, "accounting", "manage_funds")
    wallet = {
        "id": new_id(),
        "branch_id": data["branch_id"],
        "type": data["type"],
        "name": data["name"],
        "balance": float(data.get("balance", 0)),
        "bank_name": data.get("bank_name", ""),
        "account_number": data.get("account_number", ""),
        "active": True,
        "created_at": now_iso(),
    }
    await db.fund_wallets.insert_one(wallet)
    del wallet["_id"]
    return wallet


@router.post("/fund-wallets/{wallet_id}/deposit")
async def deposit_to_wallet(wallet_id: str, data: dict, user=Depends(get_current_user)):
    """Deposit money to a wallet."""
    check_perm(user, "accounting", "manage_funds")
    wallet = await db.fund_wallets.find_one({"id": wallet_id}, {"_id": 0})
    if not wallet:
        raise HTTPException(status_code=404, detail="Wallet not found")
    
    amount = float(data["amount"])
    if wallet["type"] == "safe":
        await db.safe_lots.insert_one({
            "id": new_id(),
            "branch_id": wallet["branch_id"],
            "wallet_id": wallet_id,
            "date_received": data.get("date", now_iso()[:10]),
            "original_amount": amount,
            "remaining_amount": amount,
            "source_reference": data.get("reference", "Manual deposit"),
            "created_by": user["id"],
            "created_at": now_iso()
        })
    else:
        await db.fund_wallets.update_one({"id": wallet_id}, {"$inc": {"balance": amount}})
    
    await db.wallet_movements.insert_one({
        "id": new_id(),
        "wallet_id": wallet_id,
        "branch_id": wallet["branch_id"],
        "type": "deposit",
        "amount": amount,
        "reference": data.get("reference", ""),
        "user_id": user["id"],
        "user_name": user.get("full_name", user["username"]),
        "created_at": now_iso()
    })
    return {"message": "Deposited", "amount": amount}


@router.post("/fund-wallets/pay")
async def pay_from_wallet(data: dict, user=Depends(get_current_user)):
    """Pay from a wallet (deduct funds)."""
    check_perm(user, "accounting", "manage_funds")
    wallet_id = data["wallet_id"]
    amount = float(data["amount"])
    wallet = await db.fund_wallets.find_one({"id": wallet_id}, {"_id": 0})
    if not wallet:
        raise HTTPException(status_code=404, detail="Wallet not found")
    
    if wallet["type"] == "safe":
        lots = await db.safe_lots.find(
            {"wallet_id": wallet_id, "remaining_amount": {"$gt": 0}}, {"_id": 0}
        ).sort("remaining_amount", -1).to_list(500)
        total_available = sum(lot["remaining_amount"] for lot in lots)
        if total_available < amount:
            raise HTTPException(status_code=400, detail="Insufficient Safe balance")
        remaining = amount
        usages = []
        for lot in lots:
            if remaining <= 0:
                break
            take = min(lot["remaining_amount"], remaining)
            await db.safe_lots.update_one({"id": lot["id"]}, {"$inc": {"remaining_amount": -take}})
            usages.append({"lot_id": lot["id"], "lot_date": lot["date_received"], "amount_used": take})
            await db.safe_lot_usages.insert_one({
                "id": new_id(),
                "lot_id": lot["id"],
                "payment_id": data.get("reference_id", ""),
                "branch_id": wallet["branch_id"],
                "amount_used": take,
                "used_by_user_id": user["id"],
                "used_at": now_iso()
            })
            remaining -= take
        summary = ", ".join([f"{u['lot_date']} lot: ₱{u['amount_used']:.2f}" for u in usages])
    else:
        if wallet["balance"] < amount:
            raise HTTPException(status_code=400, detail=f"Insufficient {wallet['type']} balance")
        await db.fund_wallets.update_one({"id": wallet_id}, {"$inc": {"balance": -amount}})
        summary = f"Deducted ₱{amount:.2f} from {wallet['name']}"
    
    await db.wallet_movements.insert_one({
        "id": new_id(),
        "wallet_id": wallet_id,
        "branch_id": wallet["branch_id"],
        "type": "payment",
        "amount": -amount,
        "reference": data.get("reference", ""),
        "description": data.get("description", ""),
        "user_id": user["id"],
        "user_name": user.get("full_name", user["username"]),
        "created_at": now_iso()
    })
    return {"message": "Payment processed", "summary": summary}


@router.get("/safe-lots")
async def list_safe_lots(user=Depends(get_current_user), branch_id: Optional[str] = None):
    """List safe lots with remaining balance."""
    query = {"remaining_amount": {"$gt": 0}}
    if branch_id:
        query["branch_id"] = branch_id
    lots = await db.safe_lots.find(query, {"_id": 0}).sort("date_received", 1).to_list(500)
    return lots


@router.get("/fund-wallets/{wallet_id}/movements")
async def get_wallet_movements(wallet_id: str, user=Depends(get_current_user), limit: int = 50):
    """Get movement history for a wallet."""
    movements = await db.wallet_movements.find(
        {"wallet_id": wallet_id}, {"_id": 0}
    ).sort("created_at", -1).limit(limit).to_list(limit)
    return movements


# ══════════════════════════════════════════════════════════════════
# FUND TRANSFER SYSTEM — Controlled internal money movement
# Protocol:
#   Cashier ↔ Safe  → Manager PIN required
#   Safe → Bank     → Admin TOTP required
#   Capital add     → Admin only (no PIN, role check)
# ══════════════════════════════════════════════════════════════════

TRANSFER_DESCRIPTIONS = {
    "cashier_to_safe": "Cashier → Safe transfer",
    "safe_to_cashier": "Safe → Cashier transfer",
    "safe_to_bank": "Safe → Bank deposit",
    "capital_add": "Capital injection",
}


@router.post("/fund-transfers")
async def create_fund_transfer(data: dict, user=Depends(get_current_user)):
    """
    Internal fund transfer with full audit trail.
    - cashier ↔ safe: manager PIN
    - safe → bank: admin TOTP
    - capital add: admin only
    """
    import pyotp
    check_perm(user, "accounting", "manage_funds")

    branch_id = data.get("branch_id") or user.get("branch_id", "")
    if not branch_id:
        raise HTTPException(status_code=400, detail="Branch ID required")

    transfer_type = data.get("transfer_type", "")  # cashier_to_safe | safe_to_cashier | safe_to_bank | capital_add
    amount = float(data.get("amount", 0))
    note = data.get("note", "")
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")

    authorized_by = None

    # ── Authorization based on transfer type ─────────────────────────────────
    if transfer_type in ("cashier_to_safe", "safe_to_cashier"):
        # Manager PIN required
        manager_pin = data.get("manager_pin", "")
        if not manager_pin:
            raise HTTPException(status_code=400, detail="Manager PIN required for this transfer")
        from routes.verify import verify_pin_for_action
        verifier = await verify_pin_for_action(manager_pin, "fund_transfer_cashier_safe")
        if verifier:
            authorized_by = verifier["verifier_name"]
        if not authorized_by:
            await log_failed_pin_attempt(user, f"Fund transfer: cashier ↔ safe (₱{amount:,.2f})", "fund_transfer")
            raise HTTPException(status_code=400, detail="Invalid manager PIN")

    elif transfer_type == "safe_to_bank":
        # Policy-driven: defaults to admin_pin + totp
        totp_code = data.get("totp_code", "")
        if not totp_code:
            raise HTTPException(status_code=400, detail="Admin TOTP code or Owner PIN required for bank deposit")
        from routes.verify import verify_pin_for_action
        verifier = await verify_pin_for_action(totp_code, "fund_transfer_safe_bank")
        if verifier:
            authorized_by = verifier["verifier_name"]
        if not authorized_by:
            await log_failed_pin_attempt(user, f"Fund transfer: safe → bank (₱{amount:,.2f})", "fund_transfer")
            raise HTTPException(status_code=400, detail="Invalid code — check your authenticator app or Owner PIN")

    elif transfer_type == "capital_add":
        owner_pin = data.get("owner_pin", "") or data.get("totp_code", "")
        if not owner_pin:
            raise HTTPException(status_code=400, detail="Owner PIN or TOTP code required for capital injection")
        from routes.verify import verify_pin_for_action
        verifier = await verify_pin_for_action(owner_pin, "fund_transfer_capital_add")
        if verifier:
            authorized_by = verifier["verifier_name"]
        if not authorized_by:
            await log_failed_pin_attempt(user, f"Capital injection (₱{amount:,.2f})", "fund_transfer")
            raise HTTPException(status_code=400, detail="Invalid Owner PIN or TOTP code")

    else:
        raise HTTPException(status_code=400, detail=f"Invalid transfer_type: {transfer_type}")

    description = TRANSFER_DESCRIPTIONS.get(transfer_type, transfer_type)
    ref_text = f"{description} — ₱{amount:,.2f} — {note or 'No note'}"

    # ── Execute transfer ──────────────────────────────────────────────────────
    if transfer_type == "cashier_to_safe":
        # Validate cashier has enough
        cashier_w = await db.fund_wallets.find_one(
            {"branch_id": branch_id, "type": "cashier", "active": True}, {"_id": 0}
        )
        cashier_bal = float(cashier_w.get("balance", 0)) if cashier_w else 0.0
        if cashier_bal < amount:
            raise HTTPException(status_code=400, detail=f"Cashier has ₱{cashier_bal:,.2f}, need ₱{amount:,.2f}")
        # Deduct cashier
        await update_cashier_wallet(branch_id, -amount, ref_text, allow_negative=False)
        # Add to safe
        safe_w = await db.fund_wallets.find_one(
            {"branch_id": branch_id, "type": "safe", "active": True}, {"_id": 0}
        )
        if safe_w:
            await db.safe_lots.insert_one({
                "id": new_id(), "branch_id": branch_id, "wallet_id": safe_w["id"],
                "date_received": now_iso()[:10],
                "original_amount": amount, "remaining_amount": amount,
                "source_reference": ref_text,
                "created_by": user["id"], "created_at": now_iso(),
            })
            await record_safe_movement(branch_id, amount, ref_text)

    elif transfer_type == "safe_to_cashier":
        # Validate safe has enough
        safe_w = await db.fund_wallets.find_one(
            {"branch_id": branch_id, "type": "safe", "active": True}, {"_id": 0}
        )
        if not safe_w:
            raise HTTPException(status_code=404, detail="Safe wallet not found")
        lots = await db.safe_lots.find(
            {"wallet_id": safe_w["id"], "remaining_amount": {"$gt": 0}}, {"_id": 0}
        ).to_list(500)
        safe_bal = round(sum(lot["remaining_amount"] for lot in lots), 2)
        if safe_bal < amount:
            raise HTTPException(status_code=400, detail=f"Safe has ₱{safe_bal:,.2f}, need ₱{amount:,.2f}")
        # Deduct safe
        remaining = amount
        for lot in sorted(lots, key=lambda x: -x["remaining_amount"]):
            if remaining <= 0: break
            take = min(lot["remaining_amount"], remaining)
            await db.safe_lots.update_one({"id": lot["id"]}, {"$inc": {"remaining_amount": -take}})
            remaining -= take
        await record_safe_movement(branch_id, -amount, ref_text)
        # Add to cashier
        await update_cashier_wallet(branch_id, amount, ref_text)

    elif transfer_type == "safe_to_bank":
        # Deduct from safe
        safe_w = await db.fund_wallets.find_one(
            {"branch_id": branch_id, "type": "safe", "active": True}, {"_id": 0}
        )
        if not safe_w:
            raise HTTPException(status_code=404, detail="Safe wallet not found")
        lots = await db.safe_lots.find(
            {"wallet_id": safe_w["id"], "remaining_amount": {"$gt": 0}}, {"_id": 0}
        ).to_list(500)
        safe_bal = round(sum(lot["remaining_amount"] for lot in lots), 2)
        if safe_bal < amount:
            raise HTTPException(status_code=400, detail=f"Safe has ₱{safe_bal:,.2f}, need ₱{amount:,.2f}")
        remaining = amount
        for lot in sorted(lots, key=lambda x: -x["remaining_amount"]):
            if remaining <= 0: break
            take = min(lot["remaining_amount"], remaining)
            await db.safe_lots.update_one({"id": lot["id"]}, {"$inc": {"remaining_amount": -take}})
            remaining -= take
        await record_safe_movement(branch_id, -amount, ref_text)
        # Add to bank wallet
        bank_w = await db.fund_wallets.find_one(
            {"branch_id": branch_id, "type": "bank", "active": True}, {"_id": 0}
        )
        if bank_w:
            await db.fund_wallets.update_one({"id": bank_w["id"]}, {"$inc": {"balance": amount}})
            await db.wallet_movements.insert_one({
                "id": new_id(), "wallet_id": bank_w["id"],
                "branch_id": branch_id, "type": "bank_deposit",
                "amount": amount, "reference": ref_text,
                "authorized_by": authorized_by,
                "balance_after": round(float(bank_w.get("balance", 0)) + amount, 2),
                "created_at": now_iso(),
            })

    elif transfer_type == "capital_add":
        # Admin adds capital to cashier or safe — full audit trail
        target = data.get("target_wallet", "cashier")  # 'cashier' | 'safe'
        ref_text_cap = f"Capital injection ({target}) — {note or 'Admin deposit'}"
        if target == "safe":
            safe_w = await db.fund_wallets.find_one(
                {"branch_id": branch_id, "type": "safe", "active": True}, {"_id": 0}
            )
            if safe_w:
                await db.safe_lots.insert_one({
                    "id": new_id(), "branch_id": branch_id, "wallet_id": safe_w["id"],
                    "date_received": now_iso()[:10],
                    "original_amount": amount, "remaining_amount": amount,
                    "source_reference": ref_text_cap,
                    "created_by": user["id"], "created_at": now_iso(),
                })
                await record_safe_movement(branch_id, amount, ref_text_cap)
        else:
            await update_cashier_wallet(branch_id, amount, ref_text_cap)

    # ── Record transfer log ────────────────────────────────────────────────────
    transfer_log = {
        "id": new_id(),
        "branch_id": branch_id,
        "transfer_type": transfer_type,
        "amount": amount,
        "description": description,
        "note": note,
        "authorized_by": authorized_by,
        "performed_by_id": user["id"],
        "performed_by_name": user.get("full_name", user["username"]),
        "created_at": now_iso(),
        "date": data.get("date", datetime.now(timezone.utc).strftime("%Y-%m-%d")),
    }
    # Store target for capital_add so closing wizard knows if it went to cashier or safe
    if transfer_type == "capital_add":
        transfer_log["target_wallet"] = data.get("target_wallet", "cashier")
    await db.fund_transfers.insert_one(transfer_log)
    del transfer_log["_id"]

    return {
        "message": f"{description} of ₱{amount:,.2f} completed",
        "authorized_by": authorized_by,
        "transfer": transfer_log,
    }


@router.get("/fund-transfers")
async def list_fund_transfers(
    user=Depends(get_current_user),
    branch_id: Optional[str] = None,
    limit: int = 50,
):
    """Get fund transfer audit log for a branch."""
    check_perm(user, "accounting", "view")
    query = {}
    if branch_id:
        query["branch_id"] = branch_id
    elif user.get("branch_id") and user.get("role") not in ["admin"]:
        query["branch_id"] = user["branch_id"]
    transfers = await db.fund_transfers.find(query, {"_id": 0}).sort("created_at", -1).limit(limit).to_list(limit)
    return transfers



@router.get("/expenses")
async def list_expenses(
    user=Depends(get_current_user),
    branch_id: Optional[str] = None,
    category: Optional[str] = None,
    payment_method: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    search: Optional[str] = None,
    skip: int = 0,
    limit: int = 50
):
    """List expenses with optional filters."""
    check_perm(user, "accounting", "view")
    query = {"voided": {"$ne": True}}
    if branch_id:
        query["branch_id"] = branch_id
    if category:
        query["category"] = category
    if payment_method:
        query["payment_method"] = payment_method
    if date_from:
        query["date"] = {"$gte": date_from}
    if date_to:
        query.setdefault("date", {})["$lte"] = date_to
    if search:
        query["$or"] = [
            {"description": {"$regex": search, "$options": "i"}},
            {"reference_number": {"$regex": search, "$options": "i"}},
            {"notes": {"$regex": search, "$options": "i"}},
        ]
    total = await db.expenses.count_documents(query)
    expenses = await db.expenses.find(query, {"_id": 0}).sort("created_at", -1).skip(skip).limit(limit).to_list(limit)
    return {"expenses": expenses, "total": total}


@router.get("/expenses/categories")
async def get_expense_categories(user=Depends(get_current_user)):
    """Get preset expense categories."""
    return EXPENSE_CATEGORIES



@router.get("/expenses/{expense_id}")
async def get_expense(expense_id: str, user=Depends(get_current_user)):
    """Get a single expense by ID."""
    expense = await db.expenses.find_one({"id": expense_id}, {"_id": 0})
    if not expense:
        raise HTTPException(status_code=404, detail="Expense not found")
    return expense



@router.post("/expenses")
async def create_expense(data: dict, user=Depends(get_current_user)):
    """Create a new expense. If category is 'Employee Advance', updates employee's advance balance."""
    check_perm(user, "accounting", "create_expense")
    branch_id = data.get("branch_id") or ""  # Safe: never raise KeyError
    if not branch_id:
        raise HTTPException(status_code=400, detail="Branch is required. Please select a specific branch before recording expenses.")
    assert_branch_access(user, branch_id)

    # ── Employee Advance: enforce monthly CA limit on backend ──────────────
    if data.get("category") == "Employee Advance" and data.get("employee_id"):
        employee = await db.employees.find_one({"id": data["employee_id"]}, {"_id": 0})
        if not employee:
            raise HTTPException(status_code=404, detail="Employee not found")
        monthly_limit = float(employee.get("monthly_ca_limit", 0))
        if monthly_limit > 0:
            now_dt = datetime.now(timezone.utc)
            month_start = f"{now_dt.year}-{now_dt.month:02d}-01"
            agg = await db.expenses.aggregate([
                {"$match": {"employee_id": data["employee_id"], "category": "Employee Advance", "date": {"$gte": month_start}}},
                {"$group": {"_id": None, "total": {"$sum": "$amount"}}}
            ]).to_list(1)
            this_month_total = agg[0]["total"] if agg else 0
            new_total = this_month_total + float(data["amount"])
            if new_total > monthly_limit and not data.get("manager_approved_by"):
                raise HTTPException(
                    status_code=400,
                    detail=f"Monthly CA limit exceeded. Limit: ₱{monthly_limit:.2f}, This month: ₱{this_month_total:.2f}, This advance: ₱{float(data['amount']):.2f}. Manager approval required."
                )

    payment_method = data.get("payment_method", "Cash")
    fund_source = derive_fund_source(payment_method, data.get("fund_source"))
    expense = {
        "id": new_id(),
        "branch_id": branch_id,
        "category": data.get("category", "Miscellaneous"),
        "description": data.get("description", ""),
        "notes": data.get("notes", ""),
        "amount": float(data["amount"]),
        "payment_method": payment_method,
        "fund_source": fund_source,
        "reference_number": data.get("reference_number", ""),
        "date": data.get("date", now_iso()[:10]),
        # Employee CA fields
        "employee_id": data.get("employee_id", ""),
        "employee_name": data.get("employee_name", ""),
        "manager_approved_by": data.get("manager_approved_by", ""),
        "created_by": user["id"],
        "created_by_name": user.get("full_name", user["username"]),
        "created_at": now_iso(),
    }

    ref_text = f"Expense: {data.get('category', 'General')} - {data.get('description', '')}"
    if data.get("reference_number"):
        ref_text += f" (Ref: {data['reference_number']})"
    if data.get("employee_name"):
        ref_text += f" [{data['employee_name']}]"

    # Deduct from the correct fund source (cashier, safe, or digital)
    await deduct_from_fund_source(branch_id, fund_source, float(data["amount"]), ref_text, payment_method)

    await db.expenses.insert_one(expense)

    # If Employee Advance: update employee's advance balance
    if expense["category"] == "Employee Advance" and expense.get("employee_id"):
        await db.employees.update_one(
            {"id": expense["employee_id"]},
            {"$inc": {"advance_balance": expense["amount"]}, "$set": {"updated_at": now_iso()}}
        )

    del expense["_id"]

    # ── Link pending upload sessions (inline receipt uploads) ─────────────
    upload_session_ids = data.get("upload_session_ids", [])
    if upload_session_ids:
        from pathlib import Path
        upload_dir = Path("/app/uploads")
        for sid in upload_session_ids:
            session = await db.upload_sessions.find_one({"id": sid}, {"_id": 0})
            if not session:
                continue
            old_record_id = session.get("record_id", "")
            new_dir = upload_dir / "expense" / expense["id"]
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
                old_dir = upload_dir / "expense" / old_record_id
                if old_dir.is_dir() and not any(old_dir.iterdir()):
                    try:
                        old_dir.rmdir()
                    except Exception:
                        pass
            await db.upload_sessions.update_one(
                {"id": sid},
                {"$set": {
                    "record_type": "expense",
                    "record_id": expense["id"],
                    "is_pending": False,
                    "reassigned_at": now_iso(),
                    "files": updated_files,
                }}
            )

    return expense


@router.put("/expenses/{expense_id}")
async def update_expense(expense_id: str, data: dict, user=Depends(get_current_user)):
    """Update an existing expense."""
    check_perm(user, "accounting", "edit_expense")
    expense = await db.expenses.find_one({"id": expense_id}, {"_id": 0})
    if not expense:
        raise HTTPException(status_code=404, detail="Expense not found")
    
    old_amount = expense.get("amount", 0)
    new_amount = float(data.get("amount", old_amount))
    amount_diff = new_amount - old_amount
    
    allowed = ["category", "description", "notes", "amount", "payment_method", "reference_number", "date"]
    update = {k: v for k, v in data.items() if k in allowed}
    if "amount" in update:
        update["amount"] = float(update["amount"])
    update["updated_at"] = now_iso()
    update["updated_by"] = user["id"]
    update["updated_by_name"] = user.get("full_name", user["username"])
    
    await db.expenses.update_one({"id": expense_id}, {"$set": update})
    
    if amount_diff != 0:
        # FIX: use the expense's original fund_source, not always cashier
        fund_source = expense.get("fund_source", "cashier")
        ref = f"Expense adjusted: {expense.get('description', '')} ({'+' if amount_diff > 0 else ''}{amount_diff:.2f})"
        if fund_source == "safe" and amount_diff > 0:
            # Expense increased — deduct additional from safe
            safe_wallet = await db.fund_wallets.find_one(
                {"branch_id": expense["branch_id"], "type": "safe", "active": True}, {"_id": 0}
            )
            if safe_wallet:
                remaining = amount_diff
                for lot in await db.safe_lots.find(
                    {"wallet_id": safe_wallet["id"], "remaining_amount": {"$gt": 0}}, {"_id": 0}
                ).sort("remaining_amount", -1).to_list(500):
                    if remaining <= 0: break
                    take = min(lot["remaining_amount"], remaining)
                    await db.safe_lots.update_one({"id": lot["id"]}, {"$inc": {"remaining_amount": -take}})
                    remaining -= take
                await record_safe_movement(expense["branch_id"], -amount_diff, ref)
        elif fund_source == "safe" and amount_diff < 0:
            # Expense decreased — refund difference back to safe
            safe_wallet = await db.fund_wallets.find_one(
                {"branch_id": expense["branch_id"], "type": "safe", "active": True}, {"_id": 0}
            )
            if safe_wallet:
                refund = abs(amount_diff)
                await db.safe_lots.insert_one({
                    "id": new_id(), "branch_id": expense["branch_id"],
                    "wallet_id": safe_wallet["id"],
                    "date_received": now_iso()[:10],
                    "original_amount": refund, "remaining_amount": refund,
                    "source_reference": ref, "created_by": user["id"], "created_at": now_iso(),
                })
                await record_safe_movement(expense["branch_id"], refund, ref)
        else:
            await update_cashier_wallet(expense["branch_id"], -amount_diff, ref)

    # BUG-6 FIX: Adjust employee advance_balance when editing Employee Advance expenses
    if amount_diff != 0 and expense.get("category") == "Employee Advance" and expense.get("employee_id"):
        await db.employees.update_one(
            {"id": expense["employee_id"]},
            {"$inc": {"advance_balance": amount_diff}, "$set": {"updated_at": now_iso()}}
        )
    
    return await db.expenses.find_one({"id": expense_id}, {"_id": 0})


@router.delete("/expenses/{expense_id}")
async def delete_expense(expense_id: str, data: dict = None, user=Depends(get_current_user)):
    """
    Void an expense and return the funds to the ORIGINAL fund source.
    Requires PIN verification.
    """
    check_perm(user, "accounting", "edit_expense")

    # PIN verification
    pin = (data or {}).get("pin", "")
    if not pin:
        raise HTTPException(status_code=400, detail="PIN is required to void an expense")
    from routes.verify import verify_pin_for_action
    verifier = await verify_pin_for_action(pin, "void_expense")
    if not verifier:
        raise HTTPException(status_code=403, detail="Invalid PIN")

    expense = await db.expenses.find_one({"id": expense_id}, {"_id": 0})
    if not expense:
        raise HTTPException(status_code=404, detail="Expense not found")
    if expense.get("voided"):
        raise HTTPException(status_code=400, detail="Expense is already voided")

    branch_id = expense["branch_id"]
    amount = float(expense.get("amount", 0))
    fund_source = expense.get("fund_source", "cashier")
    ref_text = f"Expense voided: {expense.get('description', '')}"

    # Return funds to the ORIGINAL source (not always cashier)
    if amount > 0:
        if fund_source == "safe":
            safe_wallet = await db.fund_wallets.find_one(
                {"branch_id": branch_id, "type": "safe", "active": True}, {"_id": 0}
            )
            if safe_wallet:
                await db.safe_lots.insert_one({
                    "id": new_id(), "branch_id": branch_id,
                    "wallet_id": safe_wallet["id"],
                    "date_received": now_iso()[:10],
                    "original_amount": amount,
                    "remaining_amount": amount,
                    "source_reference": ref_text,
                    "created_by": user["id"],
                    "created_at": now_iso(),
                })
        else:
            await update_cashier_wallet(branch_id, amount, ref_text)

    # Soft-void: keep the record for audit trail
    await db.expenses.update_one(
        {"id": expense_id},
        {"$set": {
            "voided": True,
            "voided_at": now_iso(),
            "voided_by": user.get("full_name", user["username"]),
            "void_reason": "Manually deleted",
        }}
    )

    # Reverse employee advance balance if applicable
    if expense.get("category") == "Employee Advance" and expense.get("employee_id") and amount > 0:
        await db.employees.update_one(
            {"id": expense["employee_id"]},
            {"$inc": {"advance_balance": -amount}, "$set": {"updated_at": now_iso()}}
        )

    return {"message": f"Expense voided and ₱{amount:,.2f} returned to {fund_source}"}


@router.post("/expenses/farm")
async def create_farm_expense_with_invoice(data: dict, user=Depends(get_current_user)):
    """Create a farm expense and auto-generate an invoice for the customer."""
    check_perm(user, "accounting", "create_expense")
    
    customer_id = data.get("customer_id")
    if not customer_id:
        raise HTTPException(status_code=400, detail="Customer is required for farm expense")
    
    customer = await db.customers.find_one({"id": customer_id}, {"_id": 0})
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    
    amount = float(data["amount"])
    branch_id = data["branch_id"]
    payment_method = data.get("payment_method", "Cash")
    fund_source = derive_fund_source(payment_method, data.get("fund_source"))
    
    expense = {
        "id": new_id(),
        "branch_id": branch_id,
        "category": "Farm Expense",
        "description": data.get("description", "Farm Service"),
        "notes": data.get("notes", ""),
        "amount": amount,
        "payment_method": payment_method,
        "fund_source": fund_source,
        "reference_number": data.get("reference_number", ""),
        "date": data.get("date", now_iso()[:10]),
        "customer_id": customer_id,
        "customer_name": customer.get("name", ""),
        "linked_invoice_id": None,
        "created_by": user["id"],
        "created_by_name": user.get("full_name", user["username"]),
        "created_at": now_iso(),
    }
    
    ref_text = f"Farm Expense for {customer.get('name', '')}: {data.get('description', '')}"
    await deduct_from_fund_source(branch_id, fund_source, amount, ref_text, payment_method)
    
    # Auto-generate invoice
    settings = await db.settings.find_one({"key": "invoice_prefixes"}, {"_id": 0})
    prefix = settings.get("value", {}).get("service_invoice", "SVC") if settings else "SVC"
    inv_number = await generate_next_number(prefix, branch_id)
    
    invoice = {
        "id": new_id(),
        "invoice_number": inv_number,
        "prefix": prefix,
        "customer_id": customer_id,
        "customer_name": customer.get("name", ""),
        "branch_id": branch_id,
        "order_date": data.get("date", now_iso()[:10]),
        "invoice_date": data.get("date", now_iso()[:10]),
        "due_date": data.get("date", now_iso()[:10]),
        "items": [{
            "product_id": "",
            "product_name": f"Farm Service: {data.get('description', 'Farm Expense')}",
            "description": data.get("notes", ""),
            "quantity": 1,
            "rate": amount,
            "discount_type": "amount",
            "discount_value": 0,
            "discount_amount": 0,
            "total": amount,
        }],
        "subtotal": amount,
        "freight": 0,
        "overall_discount": 0,
        "grand_total": amount,
        "amount_paid": 0,
        "balance": amount,
        "interest_rate": customer.get("interest_rate", 0),
        "interest_accrued": 0,
        "penalties": 0,
        "last_interest_date": None,
        "sale_type": "farm_expense",
        "status": "open",
        "payments": [],
        "cashier_id": user["id"],
        "cashier_name": user.get("full_name", user["username"]),
        "created_at": now_iso(),
    }
    
    await db.invoices.insert_one(invoice)
    del invoice["_id"]
    
    expense["linked_invoice_id"] = invoice["id"]
    await db.expenses.insert_one(expense)
    del expense["_id"]
    
    # Update customer balance
    await db.customers.update_one({"id": customer_id}, {"$inc": {"balance": amount}})
    
    # ── Link pending upload sessions (inline receipt uploads) ─────────────
    upload_session_ids = data.get("upload_session_ids", [])
    if upload_session_ids:
        from pathlib import Path
        upload_dir = Path("/app/uploads")
        for sid in upload_session_ids:
            session = await db.upload_sessions.find_one({"id": sid}, {"_id": 0})
            if not session:
                continue
            old_record_id = session.get("record_id", "")
            new_dir = upload_dir / "expense" / expense["id"]
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
                old_dir = upload_dir / "expense" / old_record_id
                if old_dir.is_dir() and not any(old_dir.iterdir()):
                    try:
                        old_dir.rmdir()
                    except Exception:
                        pass
            await db.upload_sessions.update_one(
                {"id": sid},
                {"$set": {
                    "record_type": "expense",
                    "record_id": expense["id"],
                    "is_pending": False,
                    "reassigned_at": now_iso(),
                    "files": updated_files,
                }}
            )

    return {"expense": expense, "invoice": invoice, "message": f"Farm expense recorded — Invoice {inv_number} created for {customer.get('name', '')}"}


@router.post("/expenses/customer-cashout")
async def create_customer_cashout(data: dict, user=Depends(get_current_user)):
    """Create a cash-out expense for a customer (advance/loan)."""
    check_perm(user, "accounting", "create_expense")
    
    customer_id = data.get("customer_id")
    if not customer_id:
        raise HTTPException(status_code=400, detail="Customer is required")
    
    customer = await db.customers.find_one({"id": customer_id}, {"_id": 0})
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    
    amount = float(data["amount"])
    branch_id = data["branch_id"]
    payment_method = data.get("payment_method", "Cash")
    fund_source = derive_fund_source(payment_method, data.get("fund_source"))
    
    expense = {
        "id": new_id(),
        "branch_id": branch_id,
        "category": "Customer Cash-out",
        "description": data.get("description", f"Cash advance to {customer.get('name', '')}"),
        "notes": data.get("notes", ""),
        "amount": amount,
        "payment_method": payment_method,
        "fund_source": fund_source,
        "date": data.get("date", now_iso()[:10]),
        "customer_id": customer_id,
        "customer_name": customer.get("name", ""),
        "created_by": user["id"],
        "created_by_name": user.get("full_name", user["username"]),
        "created_at": now_iso(),
    }
    
    ref_text = f"Cash-out to {customer.get('name', '')}"
    await deduct_from_fund_source(branch_id, fund_source, amount, ref_text, payment_method)
    
    # Create invoice for tracking
    settings = await db.settings.find_one({"key": "invoice_prefixes"}, {"_id": 0})
    prefix = settings.get("value", {}).get("cash_advance", "CA") if settings else "CA"
    inv_number = await generate_next_number(prefix, branch_id)
    
    invoice = {
        "id": new_id(),
        "invoice_number": inv_number,
        "prefix": prefix,
        "customer_id": customer_id,
        "customer_name": customer.get("name", ""),
        "branch_id": branch_id,
        "order_date": data.get("date", now_iso()[:10]),
        "invoice_date": data.get("date", now_iso()[:10]),
        "due_date": data.get("date", now_iso()[:10]),
        "items": [{
            "product_id": "",
            "product_name": "Cash Advance",
            "description": data.get("notes", ""),
            "quantity": 1,
            "rate": amount,
            "total": amount,
        }],
        "subtotal": amount,
        "grand_total": amount,
        "amount_paid": 0,
        "balance": amount,
        "interest_rate": customer.get("interest_rate", 0),
        "interest_accrued": 0,
        "penalties": 0,
        "sale_type": "cash_advance",
        "status": "open",
        "payments": [],
        "cashier_id": user["id"],
        "cashier_name": user.get("full_name", user["username"]),
        "created_at": now_iso(),
    }
    
    await db.invoices.insert_one(invoice)
    del invoice["_id"]
    
    expense["linked_invoice_id"] = invoice["id"]
    await db.expenses.insert_one(expense)
    del expense["_id"]
    
    await db.customers.update_one({"id": customer_id}, {"$inc": {"balance": amount}})
    
    return {"expense": expense, "invoice": invoice, "message": f"Cash out recorded — Invoice {inv_number} created for {customer.get('name', '')}"}


@router.post("/expenses/employee-advance")
async def create_employee_advance(data: dict, user=Depends(get_current_user)):
    """Create an employee advance expense. Enforces monthly CA limit."""
    check_perm(user, "accounting", "create_expense")
    
    employee_id = data.get("employee_id")
    if not employee_id:
        raise HTTPException(status_code=400, detail="Employee is required")
    
    employee = await db.employees.find_one({"id": employee_id}, {"_id": 0})
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")
    
    amount = float(data["amount"])
    branch_id = data["branch_id"]

    # ── Enforce monthly CA limit ──────────────────────────────────────────
    monthly_limit = float(employee.get("monthly_ca_limit", 0))
    if monthly_limit > 0:
        now_dt = datetime.now(timezone.utc)
        month_start = f"{now_dt.year}-{now_dt.month:02d}-01"
        agg = await db.expenses.aggregate([
            {"$match": {"employee_id": employee_id, "category": "Employee Advance", "date": {"$gte": month_start}}},
            {"$group": {"_id": None, "total": {"$sum": "$amount"}}}
        ]).to_list(1)
        this_month_total = agg[0]["total"] if agg else 0
        new_total = this_month_total + amount
        if new_total > monthly_limit and not data.get("manager_approved_by"):
            raise HTTPException(
                status_code=400,
                detail=f"Monthly CA limit exceeded. Limit: ₱{monthly_limit:.2f}, This month: ₱{this_month_total:.2f}, This advance: ₱{amount:.2f}. Manager approval required."
            )
    
    payment_method = data.get("payment_method", "Cash")
    fund_source = derive_fund_source(payment_method, data.get("fund_source"))
    expense = {
        "id": new_id(),
        "branch_id": branch_id,
        "category": "Employee Advance",
        "description": data.get("description", f"Advance to {employee.get('name', '')}"),
        "notes": data.get("notes", ""),
        "amount": amount,
        "payment_method": payment_method,
        "fund_source": fund_source,
        "date": data.get("date", now_iso()[:10]),
        "employee_id": employee_id,
        "employee_name": employee.get("name", ""),
        "created_by": user["id"],
        "created_by_name": user.get("full_name", user["username"]),
        "created_at": now_iso(),
    }
    
    ref_text = f"Employee advance to {employee.get('name', '')}"
    await deduct_from_fund_source(branch_id, fund_source, amount, ref_text, payment_method)
    await db.employees.update_one({"id": employee_id}, {"$inc": {"advance_balance": amount}})
    await db.expenses.insert_one(expense)
    del expense["_id"]
    
    return {"expense": expense, "message": f"Cash advance of ₱{amount:,.2f} recorded for {employee.get('name', '')}"}


# ==================== RECEIVABLES ====================
@router.get("/receivables")
async def list_receivables(user=Depends(get_current_user), branch_id: Optional[str] = None, status: Optional[str] = None):
    """
    List all outstanding receivables — reads from invoices collection (the live AR system).
    Includes farm expenses, cash advances, credit sales, and partial payments.
    Legacy 'receivables' collection is no longer used.
    """
    query = {"customer_id": {"$ne": None}, "balance": {"$gt": 0}, "status": {"$nin": ["voided", "paid"]}}
    if branch_id:
        query["branch_id"] = branch_id
    if status:
        query["status"] = status

    invoices = await db.invoices.find(query, {"_id": 0}).sort("created_at", -1).to_list(500)

    TYPE_LABELS = {
        "farm_expense": "Farm Expense",
        "cash_advance": "Customer Cash Out",  # Never "Cash Advance" — reserved for employee advances
        "interest_charge": "Interest Charge",
        "penalty_charge": "Penalty Charge",
        "walk_in": "Credit Sale",
    }

    result = []
    for inv in invoices:
        sale_type = inv.get("sale_type", "walk_in")
        type_label = TYPE_LABELS.get(sale_type, "Invoice")
        items_preview = ", ".join(i.get("product_name", "Service") for i in inv.get("items", [])[:2])
        description = f"{type_label}: {items_preview}" if items_preview else type_label

        result.append({
            "id": inv["id"],
            "invoice_id": inv["id"],
            "invoice_number": inv.get("invoice_number", ""),
            "customer_id": inv.get("customer_id"),
            "customer_name": inv.get("customer_name", ""),
            "description": description,
            "amount": inv.get("grand_total", 0),
            "paid": inv.get("amount_paid", 0),
            "balance": inv.get("balance", 0),
            "status": inv.get("status", "open"),
            "due_date": inv.get("due_date", ""),
            "sale_type": sale_type,
            "created_at": inv.get("created_at", ""),
        })

    return result


@router.post("/receivables/{rec_id}/payment")
async def pay_receivable(rec_id: str, data: dict, user=Depends(get_current_user)):
    """
    Record payment on a receivable (invoice). Updates invoice, customer balance, and correct wallet.
    Routes to digital wallet when payment method is GCash/Maya/etc.
    """
    check_perm(user, "accounting", "receive_payment")

    inv = await db.invoices.find_one({"id": rec_id}, {"_id": 0})
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice/Receivable not found")
    if inv.get("balance", 0) <= 0:
        raise HTTPException(status_code=400, detail="Already fully paid")

    amount = float(data["amount"])
    if amount > inv["balance"]:
        raise HTTPException(status_code=400, detail=f"Amount ₱{amount:,.2f} exceeds balance ₱{inv['balance']:,.2f}")
    new_balance = max(0, round(inv["balance"] - amount, 2))
    new_paid = round(inv.get("amount_paid", 0) + amount, 2)
    new_status = "paid" if new_balance <= 0 else "partial"

    # Interest & penalties first, then principal (aligned with invoice payment logic)
    interest_owed = float(inv.get("interest_accrued", 0)) + float(inv.get("penalties", 0))
    applied_interest = min(amount, interest_owed)
    applied_principal = round(amount - applied_interest, 2)
    new_interest = max(0, round(float(inv.get("interest_accrued", 0)) - applied_interest, 2))

    method = data.get("method", "Cash")
    digital = is_digital_payment(method)
    fund_source = "digital" if digital else "cashier"

    payment_record = {
        "id": new_id(),
        "amount": amount,
        "date": data.get("date", now_iso()[:10]),
        "method": method,
        "reference": data.get("reference", ""),
        "fund_source": fund_source,
        "applied_to_interest": round(applied_interest, 2),
        "applied_to_principal": applied_principal,
        "recorded_by": user.get("full_name", user["username"]),
        "recorded_at": now_iso(),
    }

    await db.invoices.update_one({"id": rec_id}, {
        "$set": {"balance": new_balance, "amount_paid": new_paid, "status": new_status, "interest_accrued": new_interest},
        "$push": {"payments": payment_record}
    })

    # Update customer balance
    if inv.get("customer_id"):
        await db.customers.update_one({"id": inv["customer_id"]}, {"$inc": {"balance": -amount}})

    # Route to correct wallet based on payment method
    branch_id = inv.get("branch_id", "")
    if branch_id:
        ref_text = f"Receivable payment {inv.get('invoice_number', '')} — {inv.get('customer_name', '')}"
        if digital:
            await update_digital_wallet(
                branch_id, amount, reference=ref_text,
                platform=method, ref_number=data.get("reference", ""),
            )
        else:
            await update_cashier_wallet(branch_id, amount, ref_text)

    return {"message": "Payment recorded", "new_balance": new_balance, "status": new_status, "fund_source": fund_source}


# ==================== PAYABLES ====================
@router.get("/payables")
async def list_payables(user=Depends(get_current_user), branch_id: Optional[str] = None, status: Optional[str] = None):
    """List payables (amounts owed to suppliers)."""
    query = {}
    if branch_id:
        query["branch_id"] = branch_id
    if status:
        query["status"] = status
    payables = await db.payables.find(query, {"_id": 0}).sort("created_at", -1).to_list(500)
    return payables


@router.post("/payables")
async def create_payable(data: dict, user=Depends(get_current_user)):
    """Create a new payable."""
    check_perm(user, "accounting", "create_expense")
    payable = {
        "id": new_id(),
        "supplier": data["supplier"],
        "branch_id": data.get("branch_id", ""),
        "description": data.get("description", ""),
        "amount": float(data["amount"]),
        "paid": 0,
        "balance": float(data["amount"]),
        "due_date": data.get("due_date", ""),
        "status": "pending",
        "created_at": now_iso(),
    }
    await db.payables.insert_one(payable)
    del payable["_id"]
    return payable


@router.post("/payables/{pay_id}/payment")
async def pay_payable(pay_id: str, data: dict, user=Depends(get_current_user)):
    """
    Record payment on a payable (accounts payable / supplier terms PO).
    - ALWAYS deducts from the specified wallet (cashier or safe)
    - Creates an expense record so it appears in the Z-Report
    - Updates the linked PO's payment status and history
    - Creates wallet_movements audit entry
    """
    check_perm(user, "accounting", "create_expense")
    pay = await db.payables.find_one({"id": pay_id}, {"_id": 0})
    if not pay:
        raise HTTPException(status_code=404, detail="Payable not found")
    if pay.get("status") == "paid":
        raise HTTPException(status_code=400, detail="Payable is already fully paid")

    amount = float(data["amount"])
    branch_id = pay.get("branch_id", data.get("branch_id", ""))
    if not branch_id:
        raise HTTPException(status_code=400, detail="Branch ID required")
    fund_source = data.get("fund_source", "cashier")
    payment_method_detail = data.get("payment_method_detail", "Cash")
    check_number = data.get("check_number", "")
    note = data.get("note", "")

    # Cap at remaining balance
    remaining_balance = round(float(pay.get("amount", 0)) - float(pay.get("paid", 0)), 2)
    amount = min(amount, remaining_balance)
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Nothing left to pay")

    # Deduct from wallet
    ref_text = f"Supplier payment — {pay.get('supplier', '')} — Payable {pay_id[:8]}"
    if check_number:
        ref_text += f" | Check #{check_number}"

    if fund_source == "safe":
        safe_wallet = await db.fund_wallets.find_one(
            {"branch_id": branch_id, "type": "safe", "active": True}, {"_id": 0}
        )
        if not safe_wallet:
            raise HTTPException(status_code=404, detail="Safe wallet not found for this branch")
        lots = await db.safe_lots.find(
            {"wallet_id": safe_wallet["id"], "remaining_amount": {"$gt": 0}}, {"_id": 0}
        ).sort("remaining_amount", -1).to_list(500)
        safe_balance = sum(lot["remaining_amount"] for lot in lots)
        if safe_balance < amount:
            raise HTTPException(status_code=400, detail=f"Safe has ₱{safe_balance:,.2f}, need ₱{amount:,.2f}")
        remaining = amount
        for lot in lots:
            if remaining <= 0:
                break
            take = min(lot["remaining_amount"], remaining)
            await db.safe_lots.update_one({"id": lot["id"]}, {"$inc": {"remaining_amount": -take}})
            remaining -= take
    else:
        cashier_wallet = await db.fund_wallets.find_one(
            {"branch_id": branch_id, "type": "cashier", "active": True}, {"_id": 0}
        )
        cashier_balance = float(cashier_wallet.get("balance", 0)) if cashier_wallet else 0
        if cashier_balance < amount:
            raise HTTPException(status_code=400, detail=f"Cashier has ₱{cashier_balance:,.2f}, need ₱{amount:,.2f}")
        await update_cashier_wallet(branch_id, -amount, ref_text)

    # Update payable
    new_paid = round(float(pay.get("paid", 0)) + amount, 2)
    new_balance = max(0, round(float(pay["amount"]) - new_paid, 2))
    new_status = "paid" if new_balance <= 0 else "partial"
    await db.payables.update_one({"id": pay_id}, {
        "$set": {"paid": new_paid, "balance": new_balance, "status": new_status,
                 "last_payment_date": now_iso()[:10]}
    })

    # Update linked PO payment status
    po_id = pay.get("po_id")
    if po_id:
        po = await db.purchase_orders.find_one({"id": po_id}, {"_id": 0})
        if po:
            po_new_paid = round(float(po.get("amount_paid", 0)) + amount, 2)
            po_grand = float(po.get("grand_total", po.get("subtotal", 0)))
            po_new_balance = max(0, round(po_grand - po_new_paid, 2))
            po_pay_status = "paid" if po_new_balance <= 0 else "partial"
            payment_record = {
                "id": new_id(), "amount": amount, "date": now_iso()[:10],
                "method": payment_method_detail, "fund_source": fund_source,
                "check_number": check_number, "reference": note,
                "recorded_by": user.get("full_name", user["username"]),
                "recorded_at": now_iso(),
            }
            await db.purchase_orders.update_one(
                {"id": po_id},
                {"$set": {"amount_paid": po_new_paid, "balance": po_new_balance,
                          "payment_status": po_pay_status},
                 "$push": {"payment_history": payment_record}}
            )

    # Create expense record so it shows in Z-Report
    expense_id = new_id()
    await db.expenses.insert_one({
        "id": expense_id, "branch_id": branch_id,
        "category": "Supplier Payment",
        "description": f"AP payment — {pay.get('supplier', '')} — {pay.get('description', '')}",
        "notes": f"Payable {pay_id[:8]} | PO {pay.get('po_id', 'N/A')[:8]} | {note}".strip(" |"),
        "amount": amount,
        "payment_method": payment_method_detail,
        "fund_source": fund_source,
        "reference_number": check_number or pay.get("po_id", ""),
        "date": now_iso()[:10],
        "po_id": po_id,
        "payable_id": pay_id,
        "vendor": pay.get("supplier", ""),
        "created_by": user["id"],
        "created_by_name": user.get("full_name", user["username"]),
        "created_at": now_iso(),
    })

    # Wallet movements audit
    await db.wallet_movements.insert_one({
        "id": new_id(),
        "wallet_id": fund_source,
        "branch_id": branch_id,
        "type": "supplier_payment",
        "amount": -amount,
        "reference": ref_text,
        "user_id": user["id"],
        "user_name": user.get("full_name", user["username"]),
        "created_at": now_iso(),
    })

    return {
        "message": f"₱{amount:,.2f} paid to {pay.get('supplier', '')} from {fund_source}. Remaining: ₱{new_balance:,.2f}",
        "new_balance": new_balance,
        "status": new_status,
        "fund_source": fund_source,
    }


# ==================== CUSTOMER INVOICE / PAYMENT MANAGEMENT ====================


@router.get("/customers/{customer_id}/invoices")
async def get_customer_open_invoices(customer_id: str, user=Depends(get_current_user)):
    """Get all open invoices for a customer. Sorted: penalty → interest → regular (oldest first)."""
    invoices = await db.invoices.find(
        {"customer_id": customer_id, "status": {"$nin": ["voided", "paid"]}, "balance": {"$gt": 0}},
        {"_id": 0}
    ).to_list(500)

    def sort_key(inv):
        t = inv.get("sale_type", "regular")
        if t == "penalty_charge":
            return (0, inv.get("order_date", ""))
        if t == "interest_charge":
            return (1, inv.get("order_date", ""))
        return (2, inv.get("order_date", ""))

    invoices.sort(key=sort_key)
    return invoices


@router.post("/customers/{customer_id}/generate-interest")
async def generate_interest_invoice(customer_id: str, data: dict, user=Depends(get_current_user)):
    """Calculate accrued interest for all overdue invoices and create one consolidated interest invoice.
    Accepts optional rate_override to use instead of customer default, and save_rate to persist it.

    Minimum interval: interest can only be generated once every 30 days (auto-mode).
    Pass force=True to override the interval — manual button only, not auto-select.
    """
    check_perm(user, "accounting", "generate_interest")
    customer = await db.customers.find_one({"id": customer_id}, {"_id": 0})
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    as_of_str = data.get("as_of_date", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    comp_date = datetime.strptime(as_of_str, "%Y-%m-%d")
    grace_period = int(customer.get("grace_period", 7))
    rate_override = data.get("rate_override")
    save_rate = data.get("save_rate", False)
    force = data.get("force", False)          # True = manual override button
    MIN_INTERVAL_DAYS = 30

    # ── Minimum interval guard (prevents accumulation of many tiny INT invoices) ──
    if not force:
        last_int_inv = await db.invoices.find_one(
            {"customer_id": customer_id, "sale_type": "interest_charge",
             "status": {"$nin": ["voided"]}},
            {"_id": 0, "order_date": 1},
            sort=[("order_date", -1)]
        )
        if last_int_inv:
            last_date = datetime.strptime(last_int_inv["order_date"], "%Y-%m-%d")
            days_since = (comp_date - last_date).days
            if days_since < MIN_INTERVAL_DAYS:
                days_until = MIN_INTERVAL_DAYS - days_since
                return {
                    "total_interest": 0,
                    "grace_period": grace_period,
                    "skipped": True,
                    "days_since_last": days_since,
                    "days_until_next": days_until,
                    "message": f"Interest already generated {days_since} day(s) ago. Next generation available in {days_until} day(s). Use Force Generate to override.",
                }

    # Use override if provided, else customer default
    default_rate = float(rate_override) if rate_override is not None else float(customer.get("interest_rate", 0))

    if not default_rate or default_rate <= 0:
        return {"message": "No interest rate provided. Enter a rate to compute interest.", "total_interest": 0, "grace_period": grace_period}

    # Save rate to customer profile if requested
    if save_rate and rate_override is not None:
        await db.customers.update_one({"id": customer_id}, {"$set": {"interest_rate": float(rate_override)}})

    open_invoices = await db.invoices.find(
        {"customer_id": customer_id, "balance": {"$gt": 0},
         "status": {"$nin": ["voided", "paid"]},
         "sale_type": {"$nin": ["interest_charge", "penalty_charge"]}},
        {"_id": 0}
    ).to_list(500)

    interest_lines, total_interest, branch_id = [], 0, ""

    for inv in open_invoices:
        rate = float(inv.get("interest_rate") or default_rate)
        if rate <= 0:
            continue
        due_str = inv.get("due_date") or inv.get("order_date") or as_of_str
        due_date = datetime.strptime(due_str, "%Y-%m-%d")
        grace_end = due_date + timedelta(days=grace_period)
        last_int_str = inv.get("last_interest_date")
        start_date = datetime.strptime(last_int_str, "%Y-%m-%d") if last_int_str else grace_end
        days = max(0, (comp_date - start_date).days)
        if days <= 0:
            continue
        principal = max(0, inv.get("balance", 0) - inv.get("interest_accrued", 0) - inv.get("penalties", 0))
        if principal <= 0:
            continue
        interest = round(principal * (rate / 100 / 30) * days, 2)
        total_interest += interest
        branch_id = inv.get("branch_id", branch_id)
        interest_lines.append({
            "product_id": "", "product_name": f"Interest — {inv.get('invoice_number','')} ({days}d × {rate}%/mo)",
            "description": f"Principal ₱{principal:.2f} | {start_date.strftime('%b %d')} – {comp_date.strftime('%b %d, %Y')}",
            "quantity": 1, "rate": interest, "discount_type": "amount",
            "discount_value": 0, "discount_amount": 0, "total": interest,
            "source_invoice_id": inv["id"], "source_invoice_number": inv.get("invoice_number", ""),
        })
        await db.invoices.update_one({"id": inv["id"]}, {"$set": {"last_interest_date": as_of_str}})

    if total_interest <= 0:
        return {"message": "No interest accrued — invoices still within grace period", "total_interest": 0, "grace_period": grace_period}

    settings = await db.settings.find_one({"key": "invoice_prefixes"}, {"_id": 0})
    prefix = settings.get("value", {}).get("interest_charge", "INT") if settings else "INT"
    inv_number = await generate_next_number(prefix, branch_id)

    interest_invoice = {
        "id": new_id(), "invoice_number": inv_number, "prefix": prefix,
        "customer_id": customer_id, "customer_name": customer.get("name", ""),
        "branch_id": branch_id, "order_date": as_of_str, "invoice_date": as_of_str, "due_date": as_of_str,
        "items": interest_lines, "subtotal": round(total_interest, 2), "freight": 0, "overall_discount": 0,
        "grand_total": round(total_interest, 2), "amount_paid": 0, "balance": round(total_interest, 2),
        "interest_rate": 0, "interest_accrued": 0, "penalties": 0, "last_interest_date": None,
        "sale_type": "interest_charge", "status": "open", "payments": [],
        "cashier_id": user["id"], "cashier_name": user.get("full_name", user["username"]), "created_at": now_iso(),
    }
    await db.invoices.insert_one(interest_invoice)
    del interest_invoice["_id"]
    await db.customers.update_one({"id": customer_id}, {"$inc": {"balance": round(total_interest, 2)}})

    # SMS hook: notify customer of interest charge — use live invoice sum
    from routes.sms_hooks import on_charge_applied
    live_res = await db.invoices.aggregate([
        {"$match": {"customer_id": customer_id, "status": {"$nin": ["voided","paid"]}, "balance": {"$gt": 0}}},
        {"$group": {"_id": None, "total": {"$sum": "$balance"}}}
    ]).to_list(1)
    live_total = round(live_res[0]["total"], 2) if live_res else 0
    await on_charge_applied(customer_id, "Interest", round(total_interest, 2), live_total, branch_id)

    return {"message": "Interest invoice created", "invoice_number": inv_number,
            "total_interest": round(total_interest, 2), "invoice": interest_invoice}


@router.post("/customers/{customer_id}/generate-penalty")
async def generate_penalty_invoice(customer_id: str, data: dict, user=Depends(get_current_user)):
    """Generate a one-time penalty invoice for all overdue invoices not yet penalized."""
    check_perm(user, "accounting", "generate_penalty")
    customer = await db.customers.find_one({"id": customer_id}, {"_id": 0})
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    penalty_rate = float(data.get("penalty_rate", 5))
    as_of_str = data.get("as_of_date", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    comp_date = datetime.strptime(as_of_str, "%Y-%m-%d")
    grace_period = int(customer.get("grace_period", 7))

    open_invoices = await db.invoices.find(
        {"customer_id": customer_id, "balance": {"$gt": 0},
         "status": {"$nin": ["voided", "paid"]},
         "sale_type": {"$nin": ["interest_charge", "penalty_charge"]},
         "penalty_applied": {"$ne": True}},
        {"_id": 0}
    ).to_list(500)

    penalty_lines, total_penalty, branch_id = [], 0, ""

    for inv in open_invoices:
        due_str = inv.get("due_date") or inv.get("order_date") or as_of_str
        due_date = datetime.strptime(due_str, "%Y-%m-%d")
        if comp_date <= due_date + timedelta(days=grace_period):
            continue
        principal = max(0, inv.get("balance", 0) - inv.get("penalties", 0))
        if principal <= 0:
            continue
        penalty = round(principal * penalty_rate / 100, 2)
        total_penalty += penalty
        branch_id = inv.get("branch_id", branch_id)
        penalty_lines.append({
            "product_id": "", "product_name": f"Penalty — {inv.get('invoice_number','')} ({penalty_rate}%)",
            "description": f"Overdue balance ₱{principal:.2f} × {penalty_rate}%",
            "quantity": 1, "rate": penalty, "discount_type": "amount",
            "discount_value": 0, "discount_amount": 0, "total": penalty,
            "source_invoice_id": inv["id"], "source_invoice_number": inv.get("invoice_number", ""),
        })
        await db.invoices.update_one({"id": inv["id"]}, {"$set": {"penalty_applied": True, "last_penalty_date": as_of_str}})

    if total_penalty <= 0:
        return {"message": "No penalties applicable — invoices within grace period or already penalized", "total_penalty": 0, "grace_period": grace_period}

    settings = await db.settings.find_one({"key": "invoice_prefixes"}, {"_id": 0})
    prefix = settings.get("value", {}).get("penalty_charge", "PEN") if settings else "PEN"
    inv_number = await generate_next_number(prefix, branch_id)

    penalty_invoice = {
        "id": new_id(), "invoice_number": inv_number, "prefix": prefix,
        "customer_id": customer_id, "customer_name": customer.get("name", ""),
        "branch_id": branch_id, "order_date": as_of_str, "invoice_date": as_of_str, "due_date": as_of_str,
        "items": penalty_lines, "subtotal": round(total_penalty, 2), "freight": 0, "overall_discount": 0,
        "grand_total": round(total_penalty, 2), "amount_paid": 0, "balance": round(total_penalty, 2),
        "interest_rate": 0, "interest_accrued": 0, "penalties": 0, "last_interest_date": None,
        "sale_type": "penalty_charge", "status": "open", "payments": [], "penalty_rate_applied": penalty_rate,
        "cashier_id": user["id"], "cashier_name": user.get("full_name", user["username"]), "created_at": now_iso(),
    }
    await db.invoices.insert_one(penalty_invoice)
    del penalty_invoice["_id"]
    await db.customers.update_one({"id": customer_id}, {"$inc": {"balance": round(total_penalty, 2)}})

    # SMS hook: notify customer of penalty charge — use live invoice sum
    from routes.sms_hooks import on_charge_applied
    live_res = await db.invoices.aggregate([
        {"$match": {"customer_id": customer_id, "status": {"$nin": ["voided","paid"]}, "balance": {"$gt": 0}}},
        {"$group": {"_id": None, "total": {"$sum": "$balance"}}}
    ]).to_list(1)
    live_total = round(live_res[0]["total"], 2) if live_res else 0
    await on_charge_applied(customer_id, "Penalty", round(total_penalty, 2), live_total, branch_id)

    return {"message": "Penalty invoice created", "invoice_number": inv_number,
            "total_penalty": round(total_penalty, 2), "invoice": penalty_invoice}


@router.post("/customers/{customer_id}/receive-payment")
async def receive_customer_payment(customer_id: str, data: dict, user=Depends(get_current_user)):
    """QuickBooks-style: apply payment across multiple invoices with per-row allocation.
    Supports optional discount on interest/penalty invoices."""
    check_perm(user, "accounting", "receive_payment")
    customer = await db.customers.find_one({"id": customer_id}, {"_id": 0})
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    allocations = data.get("allocations", [])  # [{invoice_id, amount, discount?}]
    total_amount = round(sum(float(a.get("amount", 0)) for a in allocations if float(a.get("amount", 0)) > 0), 2)
    total_discount = round(sum(float(a.get("discount", 0)) for a in allocations if float(a.get("discount", 0)) > 0), 2)
    if total_amount <= 0 and total_discount <= 0:
        raise HTTPException(status_code=400, detail="Payment or discount amount must be > 0")

    method = data.get("method", "Cash")
    digital = is_digital_payment(method)
    fund_source = "digital" if digital else "cashier"
    reference = data.get("reference", "")
    pay_date = data.get("date", now_iso()[:10])
    branch_id = data.get("branch_id", "")
    total_applied, total_discounted, applied_invoices = 0, 0, []

    for alloc in allocations:
        apply = round(float(alloc.get("amount", 0)), 2)
        discount = round(float(alloc.get("discount", 0)), 2)
        if apply <= 0 and discount <= 0:
            continue
        inv = await db.invoices.find_one({"id": alloc["invoice_id"], "customer_id": customer_id}, {"_id": 0})
        if not inv or inv.get("balance", 0) <= 0:
            continue

        # Only allow discounts on interest/penalty invoices
        if discount > 0 and inv.get("sale_type") not in ("interest_charge", "penalty_charge"):
            discount = 0

        actual_discount = min(discount, inv["balance"])
        actual_apply = min(apply, inv["balance"] - actual_discount)
        total_reduction = round(actual_apply + actual_discount, 2)

        new_balance = max(0, round(inv["balance"] - total_reduction, 2))
        new_paid = round(inv.get("amount_paid", 0) + total_reduction, 2)
        new_status = "paid" if new_balance <= 0 else "partial"

        payments_to_push = []

        # Record discount first (no wallet impact)
        if actual_discount > 0:
            discount_record = {
                "id": new_id(), "amount": actual_discount, "date": pay_date, "method": "Discount",
                "reference": f"Discount on {inv.get('sale_type', 'charge')}",
                "fund_source": "discount",
                "applied_to_interest": 0, "applied_to_principal": actual_discount,
                "recorded_by": user.get("full_name", user["username"]), "recorded_at": now_iso(),
            }
            payments_to_push.append(discount_record)
            total_discounted = round(total_discounted + actual_discount, 2)

        # Record actual payment (goes to wallet)
        if actual_apply > 0:
            payment_record = {
                "id": new_id(), "amount": actual_apply, "date": pay_date, "method": method,
                "reference": reference, "fund_source": fund_source,
                "applied_to_interest": 0, "applied_to_principal": actual_apply,
                "recorded_by": user.get("full_name", user["username"]), "recorded_at": now_iso(),
            }
            payments_to_push.append(payment_record)
            total_applied = round(total_applied + actual_apply, 2)

        if not branch_id and inv.get("branch_id"):
            branch_id = inv["branch_id"]

        await db.invoices.update_one({"id": inv["id"]}, {
            "$set": {"balance": new_balance, "amount_paid": new_paid, "status": new_status},
            "$push": {"payments": {"$each": payments_to_push}}
        })
        applied_invoices.append({"invoice_id": inv["id"], "invoice_number": inv.get("invoice_number"),
                                  "applied": actual_apply, "discount": actual_discount, "new_balance": new_balance})

    if total_applied <= 0 and total_discounted <= 0:
        raise HTTPException(status_code=400, detail="No payment could be applied")

    # Update customer balance (both payment + discount reduce AR)
    total_balance_reduction = round(total_applied + total_discounted, 2)
    await db.customers.update_one({"id": customer_id}, {"$inc": {"balance": -total_balance_reduction}})
    if branch_id and total_applied > 0:
        ref_text = f"Payment — {customer.get('name','')} {reference or method}"
        if digital:
            await update_digital_wallet(
                branch_id, total_applied, reference=ref_text,
                platform=method, ref_number=reference,
            )
        else:
            await update_cashier_wallet(branch_id, total_applied, ref_text)

    deposited_to = "Digital / E-Wallet" if digital else "Cashier Drawer"

    # SMS hook: notify customer of payment received
    if total_applied > 0:
        from routes.sms_hooks import on_payment_received
        updated_cust = await db.customers.find_one({"id": customer_id}, {"_id": 0})
        remaining = updated_cust.get("balance", 0) if updated_cust else 0
        # Find next due invoice
        next_inv = await db.invoices.find_one(
            {"customer_id": customer_id, "status": {"$nin": ["voided", "paid"]}, "balance": {"$gt": 0}, "due_date": {"$ne": None}},
            {"_id": 0, "due_date": 1},
            sort=[("due_date", 1)],
        )
        next_due_info = f"Next due: {next_inv['due_date']}. " if next_inv else ""
        await on_payment_received(customer_id, total_applied, remaining, branch_id, next_due_info)

    return {"message": "Payment applied", "total_applied": total_applied, "total_discounted": total_discounted,
            "applied_invoices": applied_invoices, "deposited_to": deposited_to}


@router.get("/customers/{customer_id}/payment-history")
async def get_customer_payment_history(customer_id: str, user=Depends(get_current_user)):
    """Get all payment records for a customer across all invoices."""
    invoices = await db.invoices.find(
        {"customer_id": customer_id},
        {"_id": 0, "invoice_number": 1, "sale_type": 1, "payments": 1}
    ).to_list(500)
    history = []
    for inv in invoices:
        for p in inv.get("payments", []):
            history.append({
                "date": p.get("date", ""), "invoice_number": inv.get("invoice_number", ""),
                "sale_type": inv.get("sale_type", "regular"), "method": p.get("method", ""),
                "reference": p.get("reference", ""), "amount": p.get("amount", 0),
                "recorded_by": p.get("recorded_by", ""), "recorded_at": p.get("recorded_at", ""),
            })
    history.sort(key=lambda x: x.get("recorded_at", ""), reverse=True)
    return history


# ==================== CUSTOMER INTEREST/PENALTY GENERATION ====================
@router.get("/customers/{customer_id}/charges-preview")
async def preview_customer_charges(customer_id: str, as_of_date: Optional[str] = None, rate_override: Optional[float] = None, user=Depends(get_current_user)):
    """Preview interest for a customer WITHOUT creating invoices. Used for display only."""
    customer = await db.customers.find_one({"id": customer_id}, {"_id": 0})
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    comp_date_str = as_of_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    comp_date = datetime.strptime(comp_date_str, "%Y-%m-%d")
    grace_period = int(customer.get("grace_period", 7))
    default_rate = float(rate_override) if rate_override is not None else float(customer.get("interest_rate", 0))

    invoices = await db.invoices.find(
        {"customer_id": customer_id, "balance": {"$gt": 0}, "status": {"$nin": ["voided", "paid"]}},
        {"_id": 0}
    ).to_list(500)

    interest_preview, total_interest, total_principal = [], 0, 0

    for inv in invoices:
        if inv.get("sale_type") in ("interest_charge", "penalty_charge"):
            continue
        rate = float(inv.get("interest_rate") or default_rate)
        if rate <= 0:
            continue
        due_str = inv.get("due_date") or inv.get("order_date") or comp_date_str
        due_date = datetime.strptime(due_str, "%Y-%m-%d")
        grace_end = due_date + timedelta(days=grace_period)
        last_int_str = inv.get("last_interest_date")
        start_date = datetime.strptime(last_int_str, "%Y-%m-%d") if last_int_str else grace_end
        days = max(0, (comp_date - start_date).days)
        principal = max(0, inv.get("balance", 0) - inv.get("interest_accrued", 0) - inv.get("penalties", 0))
        total_principal += principal
        if days > 0 and principal > 0:
            interest = round(principal * (rate / 100 / 30) * days, 2)
            total_interest += interest
            interest_preview.append({
                "invoice_number": inv.get("invoice_number", ""),
                "invoice_id": inv["id"],
                "principal": principal,
                "rate": rate,
                "days_for_interest": days,
                "interest_amount": interest,
            })

    return {
        "customer_id": customer_id,
        "total_principal": total_principal,
        "total_interest": round(total_interest, 2),
        "interest_preview": interest_preview,
    }


# ==================== CUSTOMER STATEMENT OF ACCOUNT ====================
@router.get("/customers/{customer_id}/statement")
async def get_customer_statement(
    customer_id: str,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    user=Depends(get_current_user)
):
    """Full statement of account for a customer: all charges, payments, with running balance."""
    customer = await db.customers.find_one({"id": customer_id}, {"_id": 0})
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    query = {"customer_id": customer_id}
    if date_from:
        query["order_date"] = {"$gte": date_from}
    if date_to:
        query.setdefault("order_date", {})["$lte"] = date_to

    invoices = await db.invoices.find(query, {"_id": 0}).sort("order_date", 1).to_list(1000)

    TYPE_LABELS = {
        "interest_charge": "Interest Charge",
        "penalty_charge": "Penalty Charge",
        "farm_expense": "Farm Expense",
        "cash_advance": "Customer Cash Out",  # Never "Cash Advance" — that's for employees
    }

    transactions = []
    for inv in invoices:
        label = TYPE_LABELS.get(inv.get("sale_type", ""), "Invoice")
        items_preview = ", ".join(
            i.get("product_name", "Service") for i in inv.get("items", [])[:2]
        )
        transactions.append({
            "date": inv.get("order_date", ""),
            "reference": inv.get("invoice_number", ""),
            "description": f"{label}: {items_preview}" if items_preview else label,
            "debit": inv.get("grand_total", 0),
            "credit": 0,
            "type": "charge",
            "invoice_id": inv["id"],
        })
        for p in inv.get("payments", []):
            transactions.append({
                "date": p.get("date", ""),
                "reference": inv.get("invoice_number", ""),
                "description": f"Payment — {p.get('method', 'Cash')}{' Ref:' + p['reference'] if p.get('reference') else ''}",
                "debit": 0,
                "credit": p.get("amount", 0),
                "type": "payment",
            })

    transactions.sort(key=lambda x: (x.get("date", ""), 0 if x["type"] == "charge" else 1))

    running = 0.0
    for t in transactions:
        running = round(running + t["debit"] - t["credit"], 2)
        t["running_balance"] = running

    return {
        "customer": customer,
        "transactions": transactions,
        "closing_balance": running,
        "statement_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "date_from": date_from,
        "date_to": date_to,
    }



# ══════════════════════════════════════════════════════════════════
# REVERSE / VOID FLOWS — Fix #8–10
# ══════════════════════════════════════════════════════════════════

async def _verify_manager(manager_pin: str, action_key: str = "reverse_customer_cashout") -> dict:
    """Verify manager PIN and return the authorizing manager. Raises 401 on failure."""
    if not manager_pin:
        raise HTTPException(status_code=400, detail="Manager PIN required")
    from routes.verify import verify_pin_for_action
    verifier = await verify_pin_for_action(manager_pin, action_key)
    if verifier:
        return {
            "id": verifier["verifier_id"],
            "full_name": verifier["verifier_name"],
            "username": verifier["verifier_name"],
            "role": verifier.get("method", "admin"),
        }
    raise HTTPException(status_code=403, detail="Invalid manager PIN")


# ── Fix #9: Reverse Customer Cash Advance ───────────────────────

@router.post("/expenses/customer-cashout/{expense_id}/reverse")
async def reverse_customer_cashout(expense_id: str, data: dict, user=Depends(get_current_user)):
    """
    Reverse a customer cash-out/advance.
    Re-deducts the advance from the customer's AR balance and returns cash to fund.
    Requires manager PIN.
    """
    check_perm(user, "accounting", "edit_expense")
    mgr = await _verify_manager(data.get("manager_pin", ""), "reverse_customer_cashout")

    expense = await db.expenses.find_one({"id": expense_id}, {"_id": 0})
    if not expense:
        raise HTTPException(status_code=404, detail="Expense not found")
    if expense.get("voided"):
        raise HTTPException(status_code=400, detail="Already voided")
    if expense.get("category") not in ("Customer Cash-out", "Customer Cash Out"):
        raise HTTPException(status_code=400, detail="Not a customer cash-out expense")

    branch_id = expense["branch_id"]
    amount = float(expense.get("amount", 0))
    fund_source = expense.get("fund_source", "cashier")
    reason = data.get("reason", "Reversed by manager")

    # Return cash to fund (the original cash-out deducted from fund; reversal adds it back)
    ref_text = f"Reversal — Customer advance {expense.get('description', '')} — {reason}"
    if fund_source == "safe":
        safe_wallet = await db.fund_wallets.find_one(
            {"branch_id": branch_id, "type": "safe", "active": True}, {"_id": 0}
        )
        if safe_wallet:
            await db.safe_lots.insert_one({
                "id": new_id(), "branch_id": branch_id,
                "wallet_id": safe_wallet["id"],
                "date_received": now_iso()[:10],
                "original_amount": amount, "remaining_amount": amount,
                "source_reference": ref_text,
                "created_by": user["id"], "created_at": now_iso(),
            })
    else:
        await update_cashier_wallet(branch_id, amount, ref_text)

    # Reduce customer AR balance
    customer_id = expense.get("customer_id")
    if customer_id:
        await db.customers.update_one({"id": customer_id}, {"$inc": {"balance": -amount}})

    # Void linked invoice if any
    linked_inv_id = expense.get("linked_invoice_id")
    if linked_inv_id:
        await db.invoices.update_one(
            {"id": linked_inv_id},
            {"$set": {"status": "voided", "voided_at": now_iso(),
                      "void_reason": f"Advance reversed: {reason}"}}
        )

    # Soft-void the expense
    await db.expenses.update_one({"id": expense_id}, {"$set": {
        "voided": True, "voided_at": now_iso(),
        "void_reason": reason,
        "voided_by": user.get("full_name", user["username"]),
        "void_authorized_by": mgr.get("full_name", mgr["username"]),
    }})

    return {
        "message": f"Customer advance reversed. ₱{amount:,.2f} re-deducted from {fund_source}. Customer balance reduced.",
        "authorized_by": mgr.get("full_name", mgr["username"]),
    }


# ── Fix #9b: Reverse Employee Advance ───────────────────────────

@router.post("/expenses/employee-advance/{expense_id}/reverse")
async def reverse_employee_advance(expense_id: str, data: dict, user=Depends(get_current_user)):
    """
    Reverse an employee advance.
    Returns cash to fund and reduces the employee's advance_balance.
    Requires manager PIN.
    """
    check_perm(user, "accounting", "edit_expense")
    mgr = await _verify_manager(data.get("manager_pin", ""), "reverse_employee_advance")

    expense = await db.expenses.find_one({"id": expense_id}, {"_id": 0})
    if not expense:
        raise HTTPException(status_code=404, detail="Expense not found")
    if expense.get("voided"):
        raise HTTPException(status_code=400, detail="Already voided")
    if expense.get("category") != "Employee Advance":
        raise HTTPException(status_code=400, detail="Not an employee advance expense")

    branch_id = expense["branch_id"]
    amount = float(expense.get("amount", 0))
    fund_source = expense.get("fund_source", "cashier")
    reason = data.get("reason", "Reversed by manager")

    # Return cash to fund
    ref_text = f"Employee advance reversed — {expense.get('employee_name', '')} — {reason}"
    if fund_source == "safe":
        safe_wallet = await db.fund_wallets.find_one(
            {"branch_id": branch_id, "type": "safe", "active": True}, {"_id": 0}
        )
        if safe_wallet:
            await db.safe_lots.insert_one({
                "id": new_id(), "branch_id": branch_id,
                "wallet_id": safe_wallet["id"],
                "date_received": now_iso()[:10],
                "original_amount": amount, "remaining_amount": amount,
                "source_reference": ref_text,
                "created_by": user["id"], "created_at": now_iso(),
            })
    else:
        await update_cashier_wallet(branch_id, amount, ref_text)

    # Reduce employee advance balance
    employee_id = expense.get("employee_id")
    if employee_id:
        await db.employees.update_one(
            {"id": employee_id},
            {"$inc": {"advance_balance": -amount}, "$set": {"updated_at": now_iso()}}
        )

    await db.expenses.update_one({"id": expense_id}, {"$set": {
        "voided": True, "voided_at": now_iso(),
        "void_reason": reason,
        "voided_by": user.get("full_name", user["username"]),
        "void_authorized_by": mgr.get("full_name", mgr["username"]),
    }})

    return {
        "message": f"Employee advance reversed. ₱{amount:,.2f} returned to {fund_source}. Employee advance balance reduced.",
        "authorized_by": mgr.get("full_name", mgr["username"]),
    }



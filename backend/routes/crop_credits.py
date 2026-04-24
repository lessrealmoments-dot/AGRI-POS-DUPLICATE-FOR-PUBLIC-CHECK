"""
Crop Credit routes — Charged-to-Crop credit system for agricultural POS.

Business rules:
  - Crop cycle: 120 days + 7 grace = 127 days from planting date
  - Interest: simple, monthly on principal only (no compounding)
  - Payments: interest-first, then principal
  - One active crop season per customer at a time
  - New credits during season stack into the same running total
  - Active crop credit blocks ALL normal term credits
  - Expired season + unpaid balance = soft block on new credit
  - Extensions: +15 days per extension
    - Extension 1 & 2: Manager PIN + reason
    - Extension 3+: Owner TOTP only (flagged)
"""
from fastapi import APIRouter, Depends, HTTPException
from typing import Optional
from datetime import datetime, timezone, timedelta
from config import db, _raw_db
from utils import get_current_user, check_perm, now_iso, new_id

router = APIRouter(prefix="/crop-credits", tags=["Crop Credits"])

CROP_CYCLE_DAYS = 120
GRACE_PERIOD_DAYS = 7
EXTENSION_DAYS = 15
MAX_MANAGER_EXTENSIONS = 2  # Extensions 1 & 2 use manager PIN; 3+ require TOTP


def _compute_harvest_date(planting_date_str: str) -> str:
    """Compute expected harvest date: planting + 120 + 7 days."""
    planting = datetime.strptime(planting_date_str, "%Y-%m-%d")
    harvest = planting + timedelta(days=CROP_CYCLE_DAYS + GRACE_PERIOD_DAYS)
    return harvest.strftime("%Y-%m-%d")


def _compute_accrued_interest(principal: float, monthly_rate: float, months: float) -> float:
    """Simple interest on principal only. Rate is percentage (e.g. 2 for 2%)."""
    if principal <= 0 or monthly_rate <= 0:
        return 0.0
    return round(principal * (monthly_rate / 100) * months, 2)


def _months_between(date1_str: str, date2_str: str) -> float:
    """Approximate number of months between two ISO dates."""
    d1 = datetime.strptime(date1_str, "%Y-%m-%d")
    d2 = datetime.strptime(date2_str, "%Y-%m-%d")
    delta = d2 - d1
    return max(0, delta.days / 30.0)


async def _get_org_id_from_branch(branch_id: str) -> str:
    """Get organization_id from branch_id."""
    branch = await _raw_db.branches.find_one({"id": branch_id}, {"_id": 0, "organization_id": 1})
    return (branch or {}).get("organization_id", "")


async def _send_harvest_notification(crop_credit: dict, notification_type: str):
    """
    Queue SMS to customer + all configured staff recipients.
    notification_type: '15_day' | '7_day' | 'due_today' | 'extension'
    """
    try:
        org_id = crop_credit.get("organization_id", "")
        branch_id = crop_credit.get("branch_id", "")

        # Get org-level recipient settings
        recipients_doc = await _raw_db.system_settings.find_one(
            {"key": "collection_notification_recipients", "organization_id": org_id},
            {"_id": 0}
        )
        recipients = recipients_doc or {}

        # Get company name
        biz = await _raw_db.settings.find_one(
            {"key": "company_info", "organization_id": org_id}, {"_id": 0}
        )
        company_name = (biz or {}).get("value", {}).get("name", "AgriBooks")

        customer_name = crop_credit.get("customer_name", "Farmer")
        season_end = crop_credit.get("season_end_date", "")
        principal = crop_credit.get("principal_balance", 0)
        interest = crop_credit.get("accrued_interest", 0)
        total_due = round(principal + interest, 2)
        extension_count = crop_credit.get("extension_count", 0)

        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        days_left = (datetime.strptime(season_end, "%Y-%m-%d") - datetime.strptime(today_str, "%Y-%m-%d")).days if season_end else 0

        # Build messages per notification type
        if notification_type == "15_day":
            customer_msg = (
                f"Magandang araw, {customer_name}! "
                f"Paalala: ang inyong Charged-to-Crop account sa {company_name} ay magtatapos na sa "
                f"{season_end} ({days_left} araw na lang). "
                f"Kasalukuyang principal: P{principal:,.2f} | Naipon na interest: P{interest:,.2f}. "
                f"Pakihandaan na ang pagbabayad. Salamat po!"
            )
            staff_msg = (
                f"[Harvest Alert - 15 Days] {customer_name} crop season ends {season_end}. "
                f"Principal: P{principal:,.2f} | Interest: P{interest:,.2f} | Total Due: P{total_due:,.2f}. "
                f"Please prepare for collection."
            )
        elif notification_type == "7_day":
            monthly_rate = crop_credit.get("monthly_interest_rate", 0)
            customer_msg = (
                f"Urgent: {customer_name}, 7 araw na lang sa inyong harvest date ({season_end}) "
                f"sa {company_name}. "
                f"Principal: P{principal:,.2f} + Naipon na interest: P{interest:,.2f} = "
                f"Kabuuang bayad: P{total_due:,.2f}. "
                f"Makipag-ugnayan sa amin para sa settlement. Salamat!"
            )
            staff_msg = (
                f"[Harvest Alert - 7 Days] {customer_name} harvest due {season_end}. "
                f"Principal: P{principal:,.2f} | Interest: P{interest:,.2f} | "
                f"Total Due: P{total_due:,.2f} | Rate: {monthly_rate}%/mo."
            )
        elif notification_type == "due_today":
            customer_msg = (
                f"Pagpapaalala: {customer_name}, ngayon na ang inyong harvest/due date. "
                f"Kabuuang babayaran sa {company_name}: P{total_due:,.2f} "
                f"(Principal: P{principal:,.2f} + Interest: P{interest:,.2f}). "
                f"Makipag-ugnayan sa amin ngayon. Maraming salamat!"
            )
            staff_msg = (
                f"[DUE TODAY] {customer_name} crop credit harvest due today ({season_end}). "
                f"Total Due: P{total_due:,.2f} (Principal: P{principal:,.2f} + Interest: P{interest:,.2f}). "
                f"Initiate collection."
            )
        elif notification_type == "extension":
            ext_num = extension_count
            new_end = crop_credit.get("season_end_date", "")
            last_ext = crop_credit.get("extensions", [])
            reason = last_ext[-1].get("reason", "") if last_ext else ""
            approver = last_ext[-1].get("approved_by_name", "") if last_ext else ""
            customer_msg = (
                f"Abiso: {customer_name}, ang inyong Charged-to-Crop account sa {company_name} "
                f"ay na-extend ng {EXTENSION_DAYS} araw. "
                f"Bagong due date: {new_end}. "
                f"Kasalukuyang total: P{total_due:,.2f}. "
                f"Pakitiyak ang payment sa bagong due date. Salamat!"
            )
            staff_msg = (
                f"[Extension #{ext_num}] {customer_name} crop credit extended to {new_end}. "
                f"Reason: {reason}. Approved by: {approver}. "
                f"Running total: P{total_due:,.2f}. "
                + ("[FLAGGED - Owner approval required for further extensions]" if ext_num >= 3 else "")
            )
        else:
            return

        # Queue to customer
        customer_phone = crop_credit.get("customer_phone", "")
        customer_id = crop_credit.get("customer_id", "")
        if not customer_phone and customer_id:
            cust = await _raw_db.customers.find_one({"id": customer_id}, {"_id": 0, "phone": 1, "phones": 1})
            if cust:
                customer_phone = (cust.get("phones") or [cust.get("phone", "")])[0] if cust.get("phones") else cust.get("phone", "")

        if customer_phone:
            await _raw_db.sms_queue.insert_one({
                "id": new_id(),
                "organization_id": org_id,
                "template_key": f"crop_{notification_type}",
                "customer_id": customer_id,
                "customer_name": customer_name,
                "phone": customer_phone,
                "message": customer_msg,
                "status": "pending",
                "trigger": "scheduled" if notification_type != "extension" else "auto",
                "trigger_ref": f"crop_credit:{crop_credit.get('id', '')}",
                "dedup_key": f"crop:{notification_type}:{crop_credit.get('id', '')}:{today_str}" if notification_type != "extension" else "",
                "branch_id": branch_id,
                "branch_name": "",
                "created_at": now_iso(),
                "sent_at": None, "failed_at": None, "error": None, "retry_count": 0,
            })

        # Queue to staff recipients (owner, manager, admin, auditor)
        staff_phones = {
            "owner": recipients.get("owner_phone", ""),
            "manager": recipients.get("manager_phone", ""),
            "admin": recipients.get("admin_phone", ""),
            "auditor": recipients.get("auditor_phone", ""),
        }
        for role, phone in staff_phones.items():
            if phone and phone.strip():
                await _raw_db.sms_queue.insert_one({
                    "id": new_id(),
                    "organization_id": org_id,
                    "template_key": f"crop_{notification_type}_staff",
                    "customer_id": "",
                    "customer_name": f"[{role.title()}]",
                    "phone": phone.strip(),
                    "message": staff_msg,
                    "status": "pending",
                    "trigger": "auto",
                    "trigger_ref": f"crop_credit:{crop_credit.get('id', '')}",
                    "dedup_key": f"crop:{notification_type}:staff:{role}:{crop_credit.get('id', '')}:{today_str}" if notification_type != "extension" else "",
                    "branch_id": branch_id,
                    "branch_name": "",
                    "created_at": now_iso(),
                    "sent_at": None, "failed_at": None, "error": None, "retry_count": 0,
                })

        # Mark notification as sent on crop credit record
        await _raw_db.crop_credits.update_one(
            {"id": crop_credit.get("id", "")},
            {"$push": {"notifications_sent": {"type": notification_type, "sent_at": now_iso()}}}
        )
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Crop credit notification failed: {e}")


async def _compute_crop_principal(credit: dict, raw_db=None) -> float:
    """
    Compute principal balance from crop-credit-LINKED invoices only.
    Used for interest accrual (charges interest only on crop-season purchases, not all customer debt).
    """
    db_to_use = raw_db if raw_db else _raw_db
    credits = credit.get("credits", [])
    invoice_ids = [c["invoice_id"] for c in credits if c.get("invoice_id")]
    if not invoice_ids:
        return 0.0

    pipeline = [
        {"$match": {"id": {"$in": invoice_ids}, "status": {"$nin": ["voided"]}}},
        {"$group": {"_id": None, "total_balance": {"$sum": "$balance"}}}
    ]
    result = await db_to_use.invoices.aggregate(pipeline).to_list(1)
    return round(result[0]["total_balance"], 2) if result else 0.0


async def _compute_total_customer_balance(customer_id: str, db_to_use=None) -> float:
    """
    Compute total outstanding balance for a customer across ALL non-paid/non-voided invoices,
    including interest_charge and penalty_charge invoices generated via /payments.

    This matches exactly what /customers and /payments show so all three pages
    are always consistent — they all read from the same invoices collection.

    Note: crop credit accrued_interest is a projected/informational field only and
    is NOT added to this total. Interest is officially tracked via INT-XXXXX invoices.
    """
    if not db_to_use:
        db_to_use = _raw_db
    if not customer_id:
        return 0.0
    pipeline = [
        {"$match": {
            "customer_id": customer_id,
            "status": {"$nin": ["voided", "paid"]},
            "balance": {"$gt": 0},
        }},
        {"$group": {"_id": None, "total_balance": {"$sum": "$balance"}}}
    ]
    result = await db_to_use.invoices.aggregate(pipeline).to_list(1)
    return round(result[0]["total_balance"], 2) if result else 0.0


def _enrich_credit(credit: dict, total_customer_balance: float) -> dict:
    """
    Attach the total customer balance to a crop credit dict.

    principal_balance  = ALL outstanding invoices (regular + INT + penalty)
                         — identical to what /customers and /payments show.
    total_due          = same as principal_balance (interest already lives in INT invoices).
    accrued_interest   = kept on the document as a projected/informational figure only;
                         it is NOT added to total_due to avoid double-counting with INT invoices.
    """
    credit["principal_balance"] = total_customer_balance
    credit["total_due"] = total_customer_balance          # INT invoices already included above
    return credit


# ==================== SCHEDULER JOBS ====================

async def run_harvest_reminders():
    """
    Daily job: Check all active/extended crop credits and send harvest reminders.
    Runs at 7 AM. Sends at 15 days, 7 days, and 0 days before season_end_date.
    """
    import logging
    logger = logging.getLogger(__name__)
    try:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        today_dt = datetime.strptime(today, "%Y-%m-%d")

        # Find all active or extended crop credits
        credits = await _raw_db.crop_credits.find(
            {"status": {"$in": ["active", "extended", "overdue"]}},
            {"_id": 0}
        ).to_list(10000)

        sent_count = 0
        for cc in credits:
            season_end = cc.get("season_end_date", "")
            if not season_end:
                continue
            end_dt = datetime.strptime(season_end, "%Y-%m-%d")
            days_left = (end_dt - today_dt).days

            # Determine which notification to send (avoid duplicates via dedup_key)
            notif_type = None
            if days_left == 15:
                notif_type = "15_day"
            elif days_left == 7:
                notif_type = "7_day"
            elif days_left == 0:
                notif_type = "due_today"

            if notif_type:
                # Check if already sent today
                already_sent = any(
                    n.get("type") == notif_type and n.get("sent_at", "")[:10] == today
                    for n in cc.get("notifications_sent", [])
                )
                if not already_sent:
                    await _send_harvest_notification(cc, notif_type)
                    sent_count += 1

            # Update status to overdue if season has ended and balance > 0
            if days_left < 0 and cc.get("status") != "overdue":
                total_remaining = cc.get("principal_balance", 0) + cc.get("accrued_interest", 0)
                if total_remaining > 0:
                    await _raw_db.crop_credits.update_one(
                        {"id": cc["id"]},
                        {"$set": {"status": "overdue", "updated_at": now_iso()}}
                    )

        logger.info(f"Harvest reminders: {sent_count} notifications sent")
    except Exception as e:
        logging.getLogger(__name__).error(f"Harvest reminder job failed: {e}")


async def run_monthly_interest_accrual():
    """
    Monthly job (1st of month): Accrue interest on all active crop credits.
    Simple interest on principal balance only.
    """
    import logging
    logger = logging.getLogger(__name__)
    try:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        credits = await _raw_db.crop_credits.find(
            {"status": {"$in": ["active", "extended", "overdue"]}},
            {"_id": 0}
        ).to_list(10000)

        accrued_count = 0
        for cc in credits:
            # Compute principal from crop-linked invoices only (interest charged on crop principal only)
            computed_principal = await _compute_crop_principal(cc, raw_db=_raw_db)
            monthly_rate = cc.get("monthly_interest_rate", 0)
            if monthly_rate <= 0 or computed_principal <= 0:
                continue

            interest_amount = round(computed_principal * (monthly_rate / 100), 2)
            if interest_amount <= 0:
                continue

            await _raw_db.crop_credits.update_one(
                {"id": cc["id"]},
                {
                    "$inc": {"accrued_interest": interest_amount},
                    "$push": {
                        "interest_log": {
                            "date": today,
                            "amount": interest_amount,
                            "principal_basis": computed_principal,
                            "rate": monthly_rate,
                        }
                    },
                    "$set": {"updated_at": now_iso()}
                }
            )
            accrued_count += 1

        logger.info(f"Monthly interest accrual: {accrued_count} crop credits updated")
    except Exception as e:
        logging.getLogger(__name__).error(f"Monthly interest accrual failed: {e}")


# ==================== REST ENDPOINTS ====================

@router.post("")
async def create_crop_credit(data: dict, user=Depends(get_current_user)):
    """
    Create a new crop credit for a customer.
    If customer has an active season, reject and return the active credit.
    If customer has unpaid expired season, block creation.
    """
    check_perm(user, "pos", "sell")

    customer_id = data.get("customer_id", "")
    if not customer_id:
        raise HTTPException(status_code=400, detail="customer_id is required")

    planting_date = data.get("planting_date", "")
    if not planting_date:
        raise HTTPException(status_code=400, detail="planting_date is required")

    # Validate planting date format
    try:
        datetime.strptime(planting_date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid planting_date format (YYYY-MM-DD)")

    customer = await db.customers.find_one({"id": customer_id}, {"_id": 0})
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    # Check for existing active/extended crop credit
    existing = await db.crop_credits.find_one(
        {"customer_id": customer_id, "status": {"$in": ["active", "extended"]}},
        {"_id": 0}
    )
    if existing:
        raise HTTPException(
            status_code=409,
            detail="Customer already has an active crop season. Add to existing credit instead.",
            headers={"X-Crop-Credit-Id": existing.get("id", "")}
        )

    # Check for overdue (expired season + unpaid)
    overdue = await db.crop_credits.find_one(
        {"customer_id": customer_id, "status": "overdue"},
        {"_id": 0}
    )
    if overdue:
        total_due = overdue.get("principal_balance", 0) + overdue.get("accrued_interest", 0)
        raise HTTPException(
            status_code=403,
            detail=f"Customer has an expired crop season with outstanding balance of ₱{total_due:,.2f}. Must be settled before a new season can begin.",
            headers={"X-Crop-Credit-Id": overdue.get("id", "")}
        )

    harvest_date = _compute_harvest_date(planting_date)
    monthly_rate = float(data.get("monthly_interest_rate", customer.get("interest_rate", 0)))
    initial_amount = float(data.get("initial_amount", 0))
    branch_id = data.get("branch_id", user.get("branch_id", ""))
    description = data.get("description", "Initial crop credit")

    crop_credit = {
        "id": new_id(),
        "organization_id": user.get("organization_id", ""),
        "branch_id": branch_id,
        "customer_id": customer_id,
        "customer_name": customer.get("name", ""),
        "customer_phone": (customer.get("phones") or [customer.get("phone", "")])[0] if customer.get("phones") else customer.get("phone", ""),
        "planting_date": planting_date,
        "expected_harvest_date": harvest_date,
        "season_end_date": harvest_date,
        "status": "active",
        "principal_balance": 0,  # Computed dynamically from linked invoices
        "total_principal_credited": initial_amount,
        "monthly_interest_rate": monthly_rate,
        "accrued_interest": 0.0,
        "paid_interest": 0.0,
        "paid_principal": 0.0,
        "extension_count": 0,
        "extensions": [],
        "credits": [],
        "payments": [],
        "interest_log": [],
        "notifications_sent": [],
        "created_by_id": user["id"],
        "created_by_name": user.get("full_name", user.get("username", "")),
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }

    if initial_amount > 0:
        crop_credit["credits"].append({
            "id": new_id(),
            "amount": initial_amount,
            "date": now_iso()[:10],
            "description": description,
            "invoice_id": data.get("invoice_id", ""),
            "invoice_number": data.get("invoice_number", ""),
            "recorded_by": user.get("full_name", user.get("username", "")),
            "recorded_at": now_iso(),
        })
        # Tag the invoice with crop_credit_id
        if data.get("invoice_id"):
            await _raw_db.invoices.update_one(
                {"id": data["invoice_id"]},
                {"$set": {"crop_credit_id": crop_credit["id"], "updated_at": now_iso()}}
            )

    # Don't pre-store principal_balance — it's computed dynamically from linked invoices
    crop_credit["principal_balance"] = 0

    await db.crop_credits.insert_one(crop_credit)
    del crop_credit["_id"]
    return crop_credit


@router.get("")
async def list_crop_credits(
    user=Depends(get_current_user),
    customer_id: Optional[str] = None,
    status: Optional[str] = None,
    branch_id: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
):
    """List crop credits with optional filters."""
    query = {}
    if customer_id:
        query["customer_id"] = customer_id
    if status:
        query["status"] = status
    if branch_id:
        query["branch_id"] = branch_id

    total = await db.crop_credits.count_documents(query)
    items = await db.crop_credits.find(query, {"_id": 0}).sort("created_at", -1).skip(skip).limit(limit).to_list(limit)

    # Compute total customer balance for each item (matches /customers and /payments)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for item in items:
        total_balance = await _compute_total_customer_balance(item.get("customer_id", ""))
        _enrich_credit(item, total_balance)
        season_end = item.get("season_end_date", "")
        if season_end:
            try:
                item["days_to_harvest"] = (datetime.strptime(season_end, "%Y-%m-%d") - datetime.strptime(today, "%Y-%m-%d")).days
            except Exception:
                item["days_to_harvest"] = None

    return {"items": items, "total": total}


@router.get("/check-block/{customer_id}")
async def check_customer_block(customer_id: str, user=Depends(get_current_user)):
    """
    Check if a customer is blocked from new term credits due to crop credit status.
    Returns block status, reason, and active credit info.
    """
    # Check for active/extended crop credit (blocks term credits)
    active = await db.crop_credits.find_one(
        {"customer_id": customer_id, "status": {"$in": ["active", "extended"]}},
        {"_id": 0}
    )
    if active:
        total_balance = await _compute_total_customer_balance(customer_id)
        total_interest = active.get("accrued_interest", 0)
        return {
            "blocked": True,
            "reason": "active_crop_credit",
            "message": f"Customer has an active Charged-to-Crop season (ends {active.get('season_end_date', 'N/A')}). Term credits are blocked.",
            "can_add_to_crop": True,
            "active_credit_id": active.get("id", ""),
            "active_credit": {
                "id": active.get("id"),
                "status": active.get("status"),
                "season_end_date": active.get("season_end_date"),
                "principal_balance": total_balance,
                "accrued_interest": total_interest,
                "total_due": round(total_balance + total_interest, 2),
                "extension_count": active.get("extension_count", 0),
            }
        }

    # Check for overdue (expired + unpaid)
    overdue = await db.crop_credits.find_one(
        {"customer_id": customer_id, "status": "overdue"},
        {"_id": 0}
    )
    if overdue:
        total_balance = await _compute_total_customer_balance(customer_id)
        total_due = round(total_balance + overdue.get("accrued_interest", 0), 2)
        return {
            "blocked": True,
            "reason": "expired_season_unpaid",
            "message": f"Crop season ended on {overdue.get('season_end_date', 'N/A')} with ₱{total_due:,.2f} still outstanding. Customer must pay or get an extension.",
            "can_add_to_crop": False,
            "active_credit_id": overdue.get("id", ""),
            "active_credit": {
                "id": overdue.get("id"),
                "status": "overdue",
                "season_end_date": overdue.get("season_end_date"),
                "principal_balance": total_balance,
                "accrued_interest": overdue.get("accrued_interest", 0),
                "total_due": total_due,
                "extension_count": overdue.get("extension_count", 0),
            }
        }

    return {
        "blocked": False,
        "reason": "none",
        "message": "Customer is clear for new credit.",
        "can_add_to_crop": False,
        "active_credit_id": None,
        "active_credit": None,
    }


@router.get("/customer/{customer_id}")
async def get_customer_active_credit(customer_id: str, user=Depends(get_current_user)):
    """Get the active crop credit for a customer (any non-settled status)."""
    credit = await db.crop_credits.find_one(
        {"customer_id": customer_id, "status": {"$in": ["active", "extended", "overdue"]}},
        {"_id": 0}
    )
    if not credit:
        # Also return most recent settled for history
        credit = await db.crop_credits.find_one(
            {"customer_id": customer_id, "status": "settled"},
            {"_id": 0}
        )
        if credit:
            credit["_is_settled"] = True
        return credit

    # Compute summary fields
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    season_end = credit.get("season_end_date", "")
    if season_end:
        try:
            days_left = (datetime.strptime(season_end, "%Y-%m-%d") - datetime.strptime(today, "%Y-%m-%d")).days
            credit["days_to_harvest"] = days_left
        except Exception:
            credit["days_to_harvest"] = None
    else:
        credit["days_to_harvest"] = None

    # Compute total customer balance (matches /customers and /payments)
    computed_principal = await _compute_total_customer_balance(credit.get("customer_id", ""))
    _enrich_credit(credit, computed_principal)
    return credit


@router.get("/{credit_id}")
async def get_crop_credit(credit_id: str, user=Depends(get_current_user)):
    """Get a specific crop credit by ID."""
    credit = await db.crop_credits.find_one({"id": credit_id}, {"_id": 0})
    if not credit:
        raise HTTPException(status_code=404, detail="Crop credit not found")

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    season_end = credit.get("season_end_date", "")
    if season_end:
        try:
            days_left = (datetime.strptime(season_end, "%Y-%m-%d") - datetime.strptime(today, "%Y-%m-%d")).days
            credit["days_to_harvest"] = days_left
        except Exception:
            credit["days_to_harvest"] = None

    # Compute total customer balance (matches /customers and /payments)
    total_balance = await _compute_total_customer_balance(credit.get("customer_id", ""))
    _enrich_credit(credit, total_balance)
    return credit


@router.get("/customer/{customer_id}/history")
async def get_customer_credit_history(
    customer_id: str,
    user=Depends(get_current_user),
    skip: int = 0,
    limit: int = 20,
):
    """Get all crop credit records for a customer (history)."""
    total = await db.crop_credits.count_documents({"customer_id": customer_id})
    items = await db.crop_credits.find(
        {"customer_id": customer_id}, {"_id": 0}
    ).sort("created_at", -1).skip(skip).limit(limit).to_list(limit)
    return {"items": items, "total": total}


@router.post("/{credit_id}/add-credit")
async def add_credit_to_season(credit_id: str, data: dict, user=Depends(get_current_user)):
    """
    Add a new credit amount to an existing active crop season.
    This stacks into the running total without resetting the harvest date.
    """
    check_perm(user, "pos", "sell")

    credit = await db.crop_credits.find_one({"id": credit_id}, {"_id": 0})
    if not credit:
        raise HTTPException(status_code=404, detail="Crop credit not found")

    if credit.get("status") not in ("active", "extended"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot add to a crop credit with status '{credit.get('status')}'. Only active or extended credits can receive new amounts."
        )

    amount = float(data.get("amount", 0))
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be greater than zero")

    credit_entry = {
        "id": new_id(),
        "amount": amount,
        "date": data.get("date", now_iso()[:10]),
        "description": data.get("description", "Credit added to season"),
        "invoice_id": data.get("invoice_id", ""),
        "invoice_number": data.get("invoice_number", ""),
        "recorded_by": user.get("full_name", user.get("username", "")),
        "recorded_at": now_iso(),
    }

    # Tag the invoice with crop_credit_id so the accounting flow stays connected
    invoice_id = data.get("invoice_id", "")
    if invoice_id:
        await _raw_db.invoices.update_one(
            {"id": invoice_id},
            {"$set": {"crop_credit_id": credit_id, "updated_at": now_iso()}}
        )

    await db.crop_credits.update_one(
        {"id": credit_id},
        {
            "$inc": {"total_principal_credited": amount},
            "$push": {"credits": credit_entry},
            "$set": {"updated_at": now_iso()}
        }
    )

    updated = await db.crop_credits.find_one({"id": credit_id}, {"_id": 0})
    total_balance = await _compute_total_customer_balance(updated.get("customer_id", ""))
    _enrich_credit(updated, total_balance)
    return updated


@router.post("/{credit_id}/payment")
async def record_crop_payment(credit_id: str, data: dict, user=Depends(get_current_user)):
    """
    Record a payment on a crop credit.
    Payment allocation: interest first, then principal.
    Auto-settles when total balance (principal + interest) reaches zero.
    """
    check_perm(user, "accounting", "create")

    credit = await db.crop_credits.find_one({"id": credit_id}, {"_id": 0})
    if not credit:
        raise HTTPException(status_code=404, detail="Crop credit not found")

    if credit.get("status") == "settled":
        raise HTTPException(status_code=400, detail="Crop credit is already settled")

    amount = float(data.get("amount", 0))
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Payment amount must be greater than zero")

    accrued_interest = credit.get("accrued_interest", 0)
    # Use crop-linked principal for allocation logic (interest is charged on crop principal)
    principal_balance = await _compute_crop_principal(credit)

    # Interest first, then principal
    applied_interest = min(amount, accrued_interest)
    remaining_after_interest = amount - applied_interest
    applied_principal = min(remaining_after_interest, principal_balance)

    new_accrued_interest = round(accrued_interest - applied_interest, 2)
    new_paid_interest = round(credit.get("paid_interest", 0) + applied_interest, 2)
    new_paid_principal = round(credit.get("paid_principal", 0) + applied_principal, 2)

    payment_entry = {
        "id": new_id(),
        "amount": amount,
        "date": data.get("date", now_iso()[:10]),
        "method": data.get("method", "Cash"),
        "reference": data.get("reference", ""),
        "interest_portion": applied_interest,
        "principal_portion": applied_principal,
        "recorded_by": user.get("full_name", user.get("username", "")),
        "recorded_at": now_iso(),
    }

    # Determine new status based on computed principal
    remaining_principal_after = max(0, principal_balance - applied_principal)
    total_remaining = remaining_principal_after + max(0, new_accrued_interest)
    new_status = "settled" if total_remaining <= 0 else credit.get("status", "active")

    update = {
        "$set": {
            "accrued_interest": max(0, new_accrued_interest),
            "paid_interest": new_paid_interest,
            "paid_principal": new_paid_principal,
            "status": new_status,
            "updated_at": now_iso(),
        },
        "$push": {"payments": payment_entry}
    }

    if new_status == "settled":
        update["$set"]["settled_at"] = now_iso()

    await db.crop_credits.update_one({"id": credit_id}, update)
    updated = await db.crop_credits.find_one({"id": credit_id}, {"_id": 0})
    total_balance = await _compute_total_customer_balance(updated.get("customer_id", ""))
    _enrich_credit(updated, total_balance)
    return {
        "message": "Interest payment recorded" + (" — Interest cleared!" if new_accrued_interest <= 0 else "") + (" — Account settled!" if new_status == "settled" else ""),
        "applied_interest": applied_interest,
        "applied_principal": applied_principal,
        "new_accrued_interest": max(0, new_accrued_interest),
        "total_remaining": max(0, total_remaining),
        "status": new_status,
        "payment": payment_entry,
        "credit": updated,
    }


@router.post("/{credit_id}/extend")
async def extend_crop_season(credit_id: str, data: dict, user=Depends(get_current_user)):
    """
    Extend the crop season by 15 days.
    Extension 1 & 2: Manager PIN + reason required.
    Extension 3+: Owner TOTP (Google Authenticator) required. Flagged.
    """
    check_perm(user, "pos", "sell")

    credit = await db.crop_credits.find_one({"id": credit_id}, {"_id": 0})
    if not credit:
        raise HTTPException(status_code=404, detail="Crop credit not found")

    if credit.get("status") == "settled":
        raise HTTPException(status_code=400, detail="Cannot extend a settled crop credit")

    if credit.get("status") not in ("active", "extended", "overdue"):
        raise HTTPException(status_code=400, detail="Crop credit is not in an extendable state")

    reason = data.get("reason", "").strip()
    if not reason:
        raise HTTPException(status_code=400, detail="Extension reason is required")

    pin = data.get("pin", "")
    if not pin:
        raise HTTPException(status_code=400, detail="PIN or TOTP code is required")

    extension_count = credit.get("extension_count", 0)
    new_extension_count = extension_count + 1

    # Determine required auth method
    from routes.verify import verify_pin_for_action

    if new_extension_count <= MAX_MANAGER_EXTENSIONS:
        # Extensions 1 & 2: Manager PIN allowed
        verifier = await verify_pin_for_action(
            pin, "credit_sale_approval", branch_id=credit.get("branch_id", "")
        )
        if not verifier:
            raise HTTPException(
                status_code=403,
                detail="Invalid PIN. Manager PIN, Admin PIN, or TOTP required for extensions 1 & 2."
            )
        approved_method = verifier.get("method", "pin")
    else:
        # Extension 3+: OWNER TOTP ONLY
        if len(pin) != 6 or not pin.isdigit():
            raise HTTPException(
                status_code=403,
                detail=f"Extension #{new_extension_count} requires Owner TOTP (6-digit Google Authenticator code)."
            )
        verifier = await verify_pin_for_action(
            pin, "credit_sale_approval", branch_id=credit.get("branch_id", "")
        )
        if not verifier or verifier.get("method") != "totp":
            raise HTTPException(
                status_code=403,
                detail=f"Extension #{new_extension_count} requires Owner TOTP (Google Authenticator). Manager PIN is not accepted."
            )
        approved_method = "totp"

    # Compute new season end date
    current_end = credit.get("season_end_date", "")
    if current_end:
        current_end_dt = datetime.strptime(current_end, "%Y-%m-%d")
    else:
        current_end_dt = datetime.now(timezone.utc)
    new_end_dt = current_end_dt + timedelta(days=EXTENSION_DAYS)
    new_end_date = new_end_dt.strftime("%Y-%m-%d")

    extension_entry = {
        "extension_number": new_extension_count,
        "reason": reason,
        "approved_by_id": verifier.get("verifier_id", user["id"]),
        "approved_by_name": verifier.get("verifier_name", user.get("full_name", "")),
        "approved_method": approved_method,
        "approved_at": now_iso(),
        "previous_end_date": current_end,
        "new_end_date": new_end_date,
        "flagged": new_extension_count >= 3,
    }

    new_status = "extended"

    await db.crop_credits.update_one(
        {"id": credit_id},
        {
            "$set": {
                "season_end_date": new_end_date,
                "status": new_status,
                "extension_count": new_extension_count,
                "updated_at": now_iso(),
            },
            "$push": {"extensions": extension_entry}
        }
    )

    updated = await db.crop_credits.find_one({"id": credit_id}, {"_id": 0})
    updated["total_due"] = round(
        updated.get("principal_balance", 0) + updated.get("accrued_interest", 0), 2
    )

    # Send extension notification to all parties
    await _send_harvest_notification(updated, "extension")

    return {
        "message": f"Season extended to {new_end_date}" + (" [FLAGGED — Owner approval used]" if new_extension_count >= 3 else ""),
        "new_end_date": new_end_date,
        "extension_count": new_extension_count,
        "flagged": new_extension_count >= 3,
        "approved_by": verifier.get("verifier_name", ""),
        "credit": updated,
    }


@router.post("/{credit_id}/accrue-interest")
async def manually_accrue_interest(credit_id: str, user=Depends(get_current_user)):
    """
    Manually trigger monthly interest accrual for a specific crop credit.
    Admin only.
    """
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")

    credit = await db.crop_credits.find_one({"id": credit_id}, {"_id": 0})
    if not credit:
        raise HTTPException(status_code=404, detail="Crop credit not found")

    if credit.get("status") == "settled":
        raise HTTPException(status_code=400, detail="Cannot accrue interest on settled credit")

    monthly_rate = credit.get("monthly_interest_rate", 0)
    # Interest is charged on crop-linked principal only
    principal = await _compute_crop_principal(credit)

    if monthly_rate <= 0 or principal <= 0:
        return {"message": "No interest applicable", "interest_added": 0}

    interest_amount = round(principal * (monthly_rate / 100), 2)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    await db.crop_credits.update_one(
        {"id": credit_id},
        {
            "$inc": {"accrued_interest": interest_amount},
            "$push": {
                "interest_log": {
                    "date": today,
                    "amount": interest_amount,
                    "principal_basis": principal,
                    "rate": monthly_rate,
                    "manual": True,
                }
            },
            "$set": {"updated_at": now_iso()}
        }
    )

    updated = await db.crop_credits.find_one({"id": credit_id}, {"_id": 0})
    total_balance = await _compute_total_customer_balance(updated.get("customer_id", ""))
    return {
        "message": f"Interest of ₱{interest_amount:,.2f} accrued",
        "interest_added": interest_amount,
        "new_accrued_interest": updated.get("accrued_interest", 0),
        "total_due": round(total_balance + updated.get("accrued_interest", 0), 2),
    }

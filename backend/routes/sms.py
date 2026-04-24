"""
SMS Engine — Queue, Templates, Settings, and Auto-trigger helpers.
All SMS goes through the queue. An external gateway (phone app / API) polls
GET /pending and marks sent via PATCH /{id}/mark-sent.
"""
from fastapi import APIRouter, Depends, HTTPException
from typing import Optional
from datetime import datetime, timezone, timedelta, date
from config import db, _raw_db, logger as _config_logger
from utils import get_current_user, check_perm, now_iso, new_id

router = APIRouter(prefix="/sms", tags=["SMS"])

# ── Default Templates (seeded on first access) ─────────────────────────────

DEFAULT_TEMPLATES = [
    {
        "key": "credit_new",
        "name": "New Credit Notification",
        "body": (
            "Hi <customer_name>, ikaw ay may bagong credit na P<amount> "
            "sa <company_name> - <branch_name> ngayong <date>. "
            "Due date: <due_date>. "
            "Current total balance mo: P<total_balance>. "
            "Salamat po! - <company_name>"
        ),
        "placeholders": ["customer_name", "amount", "company_name", "branch_name",
                         "date", "due_date", "total_balance"],
        "trigger": "auto",
        "active": True,
    },
    {
        "key": "reminder_15day",
        "name": "15-Day Due Reminder",
        "body": (
            "Reminder: Hi <customer_name>, may balance ka pong "
            "P<total_balance> sa <company_name>. "
            "May P<amount_due_soon> na due sa <due_date> (15 days na lang po). "
            "Para maiwasan ang interest, bayaran po bago mag-due. Salamat!"
        ),
        "placeholders": ["customer_name", "total_balance", "company_name",
                         "amount_due_soon", "due_date"],
        "trigger": "scheduled",
        "active": True,
    },
    {
        "key": "reminder_7day",
        "name": "7-Day Due Reminder (with interest estimate)",
        "body": (
            "Urgent: Hi <customer_name>, P<amount_due_soon> mo po sa "
            "<company_name> ay due na sa <due_date> (7 days na lang). "
            "Kung hindi mababayaran on time, estimated interest po ay "
            "~P<est_interest>/month (<interest_rate>%/mo). "
            "Current total balance: P<total_balance>. "
            "Paki-settle na po. Salamat!"
        ),
        "placeholders": ["customer_name", "amount_due_soon", "company_name",
                         "due_date", "est_interest", "interest_rate", "total_balance"],
        "trigger": "scheduled",
        "active": True,
    },
    {
        "key": "overdue_notice",
        "name": "Overdue Notice",
        "body": (
            "Hi <customer_name>, ang P<amount_overdue> mo po sa "
            "<company_name> ay <days_overdue> days na overdue. "
            "Interest is accruing at <interest_rate>%/mo. "
            "Current total balance: P<total_balance>. "
            "Paki-settle na po agad. Salamat!"
        ),
        "placeholders": ["customer_name", "amount_overdue", "company_name",
                         "days_overdue", "interest_rate", "total_balance"],
        "trigger": "scheduled",
        "active": True,
    },
    {
        "key": "payment_received",
        "name": "Payment Received Confirmation",
        "body": (
            "Salamat <customer_name>! Natanggap na namin ang P<amount_paid> mo. "
            "Remaining balance: P<remaining_balance>. "
            "<next_due_info>"
            "Salamat po! - <company_name>"
        ),
        "placeholders": ["customer_name", "amount_paid", "remaining_balance",
                         "next_due_info", "company_name"],
        "trigger": "auto",
        "active": True,
    },
    {
        "key": "charge_applied",
        "name": "Interest/Penalty Applied",
        "body": (
            "Notice: <charge_type> of P<charge_amount> na-apply sa account mo, "
            "<customer_name>. New balance: P<total_balance>. "
            "Para maiwasan ang dagdag charges, bayaran po agad. "
            "- <company_name>"
        ),
        "placeholders": ["charge_type", "charge_amount", "customer_name",
                         "total_balance", "company_name"],
        "trigger": "auto",
        "active": True,
    },
    {
        "key": "delivery_ready",
        "name": "Delivery/Pickup Ready",
        "body": (
            "Hi <customer_name>, ang order mo po sa <company_name> - <branch_name> "
            "ay ready na for pickup. Ref: <reference_number>. "
            "Salamat po!"
        ),
        "placeholders": ["customer_name", "company_name", "branch_name",
                         "reference_number"],
        "trigger": "manual",
        "active": True,
    },
    {
        "key": "promo_blast",
        "name": "Promotional Message",
        "body": (
            "Hi <customer_name>! <promo_message> "
            "- <company_name> <branch_name>"
        ),
        "placeholders": ["customer_name", "promo_message", "company_name",
                         "branch_name"],
        "trigger": "manual",
        "active": True,
    },
    {
        "key": "monthly_summary",
        "name": "Monthly Statement Summary",
        "body": (
            "Monthly Summary: Hi <customer_name>, total balance mo po sa "
            "<company_name> ay P<total_balance>. "
            "Overdue: P<overdue_amount>. "
            "Paki-visit po kami para ma-settle. Salamat!"
        ),
        "placeholders": ["customer_name", "company_name", "total_balance",
                         "overdue_amount"],
        "trigger": "scheduled",
        "active": True,
    },
    {
        "key": "custom",
        "name": "Custom Message",
        "body": "<message>",
        "placeholders": ["message"],
        "trigger": "manual",
        "active": True,
    },
    # ── Crop Credit Notifications ────────────────────────────────────────────
    {
        "key": "crop_season_started",
        "name": "Crop Season Started",
        "body": (
            "Hi <customer_name>, nagsimula na ang inyong Charged-to-Crop account "
            "sa <company_name>. "
            "Planting date: <planting_date>. "
            "Expected harvest / due date: <harvest_date>. "
            "Kasalukuyang balance: P<total_balance>. "
            "Salamat po!"
        ),
        "placeholders": ["customer_name", "company_name", "planting_date",
                         "harvest_date", "total_balance"],
        "trigger": "auto",
        "active": True,
    },
    {
        "key": "crop_credit_added",
        "name": "Crop Credit — New Purchase Added",
        "body": (
            "Hi <customer_name>, may bagong na-charge na P<amount> "
            "sa inyong Charged-to-Crop account sa <company_name>. "
            "Invoice: <invoice_number>. "
            "Running total balance: P<total_balance>. "
            "Due: <harvest_date>. Salamat!"
        ),
        "placeholders": ["customer_name", "amount", "company_name",
                         "invoice_number", "total_balance", "harvest_date"],
        "trigger": "auto",
        "active": True,
    },
    {
        "key": "crop_harvest_15day",
        "name": "Crop Harvest Reminder — 15 Days",
        "body": (
            "Magandang araw, <customer_name>! "
            "Paalala: ang inyong Charged-to-Crop account sa <company_name> "
            "ay magtatapos na sa <harvest_date> (15 araw na lang). "
            "Kasalukuyang kabuuang balance: P<total_balance>. "
            "Pakihandaan na ang pagbabayad. Salamat po!"
        ),
        "placeholders": ["customer_name", "company_name", "harvest_date", "total_balance"],
        "trigger": "scheduled",
        "active": True,
    },
    {
        "key": "crop_harvest_7day",
        "name": "Crop Harvest Reminder — 7 Days",
        "body": (
            "Urgent: <customer_name>, 7 araw na lang sa inyong harvest due date "
            "(<harvest_date>) sa <company_name>. "
            "Kabuuang balance: P<total_balance>. "
            "Makipag-ugnayan sa amin para sa settlement. Salamat!"
        ),
        "placeholders": ["customer_name", "company_name", "harvest_date", "total_balance"],
        "trigger": "scheduled",
        "active": True,
    },
    {
        "key": "crop_harvest_due",
        "name": "Crop Harvest Due Today",
        "body": (
            "Pagpapaalala: <customer_name>, ngayon na ang inyong harvest due date. "
            "Kabuuang babayaran sa <company_name>: P<total_balance>. "
            "Makipag-ugnayan sa amin ngayon. Maraming salamat!"
        ),
        "placeholders": ["customer_name", "company_name", "total_balance"],
        "trigger": "scheduled",
        "active": True,
    },
    {
        "key": "crop_extension",
        "name": "Crop Season Extended",
        "body": (
            "Abiso: <customer_name>, ang inyong Charged-to-Crop account sa "
            "<company_name> ay na-extend ng 15 araw. "
            "Bagong due date: <new_harvest_date>. "
            "Kasalukuyang balance: P<total_balance>. "
            "Pakitiyak ang payment sa bagong due date. Salamat!"
        ),
        "placeholders": ["customer_name", "company_name", "new_harvest_date",
                         "total_balance"],
        "trigger": "auto",
        "active": True,
    },
    {
        "key": "crop_overdue_notice",
        "name": "Crop Season Overdue Reminder (Post-Harvest)",
        "body": (
            "Paalala: <customer_name>, ang inyong Charged-to-Crop account sa "
            "<company_name> ay <days_overdue> araw nang overdue mula sa "
            "harvest date na <harvest_date>. "
            "Kabuuang dapat bayaran: P<total_balance>. "
            "Makipag-ugnayan sa amin agad para maiwasan ang dagdag charges. "
            "Salamat po!"
        ),
        "placeholders": ["customer_name", "company_name", "days_overdue",
                         "harvest_date", "total_balance"],
        "trigger": "scheduled",
        "active": True,
    },
]


async def _ensure_templates():
    """
    Upsert default templates — inserts any keys that don't yet exist in the current org.
    Safe to call repeatedly: never overwrites customized templates, only fills gaps.
    This handles both fresh installs (count=0) and orgs that existed before new templates were added.
    """
    existing_keys = set()
    async for doc in db.sms_templates.find({}, {"_id": 0, "key": 1}):
        existing_keys.add(doc["key"])

    missing = [t for t in DEFAULT_TEMPLATES if t["key"] not in existing_keys]
    if missing:
        docs = [{**t, "id": new_id(), "created_at": now_iso(), "updated_at": now_iso()}
                for t in missing]
        await db.sms_templates.insert_many(docs)


# ── Template rendering helper ───────────────────────────────────────────────

def render_template(body: str, variables: dict) -> str:
    """Replace <placeholder> tokens with actual values."""
    result = body
    for key, val in variables.items():
        result = result.replace(f"<{key}>", str(val))
    return result


# ── Queue a single SMS (called by hooks / scheduler) ───────────────────────

async def queue_sms(
    template_key: str,
    customer_id: str,
    customer_name: str,
    phone: str,
    variables: dict,
    organization_id: str = "",
    branch_id: str = "",
    branch_name: str = "",
    trigger: str = "auto",
    trigger_ref: str = "",
    dedup_key: str = "",
):
    """Insert an SMS into the queue. Scoped to organization_id for multi-tenant isolation."""
    if not phone or not phone.strip():
        return None

    # Build org filter for _raw_db reads
    org_filter = {"organization_id": organization_id} if organization_id else {}

    # Check template — org-scoped first, fallback to global default
    template = None
    if organization_id:
        template = await _raw_db.sms_templates.find_one({"key": template_key, "organization_id": organization_id}, {"_id": 0})
    if not template:
        template = await _raw_db.sms_templates.find_one({"key": template_key, "organization_id": {"$exists": False}}, {"_id": 0})
    if not template:
        template = await _raw_db.sms_templates.find_one({"key": template_key}, {"_id": 0})
    if not template or not template.get("active", True):
        return None

    # Check per-trigger setting — org-scoped first, fallback to global
    setting = None
    base_setting_query = {"trigger_key": template_key, "$or": [{"branch_id": branch_id}, {"branch_id": None}, {"branch_id": ""}]}
    if organization_id:
        setting = await _raw_db.sms_settings.find_one({**base_setting_query, "organization_id": organization_id}, {"_id": 0})
    if not setting:
        setting = await _raw_db.sms_settings.find_one(base_setting_query, {"_id": 0})
    if setting and not setting.get("enabled", True):
        return None

    # De-duplication — always org-scoped to prevent cross-company dedup conflicts
    if dedup_key:
        existing = await _raw_db.sms_queue.find_one({"dedup_key": dedup_key, **org_filter}, {"_id": 0})
        if existing:
            return None

    message = render_template(template["body"], variables)
    doc = {
        "id": new_id(),
        "organization_id": organization_id,
        "template_key": template_key,
        "customer_id": customer_id,
        "customer_name": customer_name,
        "phone": phone.strip(),
        "message": message,
        "status": "pending",
        "trigger": trigger,
        "trigger_ref": trigger_ref,
        "dedup_key": dedup_key,
        "branch_id": branch_id,
        "branch_name": branch_name,
        "created_at": now_iso(),
        "sent_at": None,
        "failed_at": None,
        "error": None,
        "retry_count": 0,
    }
    await _raw_db.sms_queue.insert_one(doc)
    del doc["_id"]
    return doc


# ═══════════════════════════════════════════════════════════════════════════
# REST API
# ═══════════════════════════════════════════════════════════════════════════


# ── Templates ───────────────────────────────────────────────────────────────

@router.get("/templates")
async def list_templates(user=Depends(get_current_user)):
    """List all SMS templates."""
    await _ensure_templates()
    templates = await db.sms_templates.find({}, {"_id": 0}).to_list(50)
    return templates


@router.put("/templates/{template_id}")
async def update_template(template_id: str, data: dict, user=Depends(get_current_user)):
    """Update template body or active status."""
    check_perm(user, "settings", "edit")
    allowed = {"body", "name", "active"}
    update = {k: v for k, v in data.items() if k in allowed}
    if not update:
        raise HTTPException(status_code=400, detail="Nothing to update")
    update["updated_at"] = now_iso()
    result = await db.sms_templates.update_one({"id": template_id}, {"$set": update})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Template not found")
    return await db.sms_templates.find_one({"id": template_id}, {"_id": 0})


# ── Settings ────────────────────────────────────────────────────────────────

@router.get("/settings")
async def list_sms_settings(user=Depends(get_current_user)):
    """List SMS trigger settings."""
    await _ensure_templates()
    settings = await db.sms_settings.find({}, {"_id": 0}).to_list(100)
    # Return merged with template defaults
    templates = await db.sms_templates.find({}, {"_id": 0}).to_list(50)
    settings_map = {s["trigger_key"]: s for s in settings}
    result = []
    for t in templates:
        s = settings_map.get(t["key"], {})
        result.append({
            "trigger_key": t["key"],
            "template_name": t["name"],
            "enabled": s.get("enabled", True),
            "branch_id": s.get("branch_id"),
        })
    return result


@router.put("/settings/{trigger_key}")
async def update_sms_setting(trigger_key: str, data: dict, user=Depends(get_current_user)):
    """Enable/disable a specific SMS trigger."""
    check_perm(user, "settings", "edit")
    enabled = data.get("enabled", True)
    branch_id = data.get("branch_id")
    await db.sms_settings.update_one(
        {"trigger_key": trigger_key, "branch_id": branch_id},
        {"$set": {"enabled": enabled, "updated_at": now_iso()},
         "$setOnInsert": {"id": new_id(), "trigger_key": trigger_key, "branch_id": branch_id}},
        upsert=True,
    )
    return {"trigger_key": trigger_key, "enabled": enabled}


# ── Queue ───────────────────────────────────────────────────────────────────

@router.get("/queue")
async def list_sms_queue(
    status: Optional[str] = None,
    branch_id: Optional[str] = None,
    limit: int = 100,
    skip: int = 0,
    user=Depends(get_current_user),
):
    """List SMS queue entries. Filter by status and branch."""
    query = {}
    if status:
        query["status"] = status
    if branch_id:
        query["branch_id"] = branch_id
    total = await db.sms_queue.count_documents(query)
    items = await db.sms_queue.find(query, {"_id": 0}).sort("created_at", -1).skip(skip).limit(limit).to_list(limit)
    return {"items": items, "total": total}


@router.get("/queue/pending")
async def get_pending_sms(limit: int = 50, user=Depends(get_current_user)):
    """Get pending SMS for the gateway app to send."""
    items = await db.sms_queue.find(
        {"status": "pending"}, {"_id": 0}
    ).sort("created_at", 1).limit(limit).to_list(limit)
    return items


@router.patch("/queue/{sms_id}/mark-sent")
async def mark_sms_sent(sms_id: str, user=Depends(get_current_user)):
    """Gateway app reports SMS was sent successfully."""
    result = await db.sms_queue.update_one(
        {"id": sms_id, "status": "pending"},
        {"$set": {"status": "sent", "sent_at": now_iso()}}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="SMS not found or not pending")
    return {"status": "sent"}


@router.patch("/queue/{sms_id}/mark-failed")
async def mark_sms_failed(sms_id: str, data: dict = None, user=Depends(get_current_user)):
    """Gateway app reports SMS send failure."""
    error = (data or {}).get("error", "Unknown error")
    await db.sms_queue.update_one(
        {"id": sms_id},
        {"$set": {"status": "failed", "failed_at": now_iso(), "error": error},
         "$inc": {"retry_count": 1}}
    )
    return {"status": "failed"}


@router.post("/queue/{sms_id}/retry")
async def retry_sms(sms_id: str, user=Depends(get_current_user)):
    """Re-queue a failed SMS."""
    result = await db.sms_queue.update_one(
        {"id": sms_id, "status": "failed"},
        {"$set": {"status": "pending", "error": None}}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="SMS not found or not failed")
    return {"status": "pending"}


@router.post("/queue/{sms_id}/skip")
async def skip_sms(sms_id: str, user=Depends(get_current_user)):
    """Skip a pending SMS (admin decided not to send)."""
    result = await db.sms_queue.update_one(
        {"id": sms_id, "status": "pending"},
        {"$set": {"status": "skipped"}}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="SMS not found or not pending")
    return {"status": "skipped"}


# ── Manual Send / Compose ───────────────────────────────────────────────────

@router.post("/send")
async def send_manual_sms(data: dict, user=Depends(get_current_user)):
    """Manually compose and queue an SMS to a customer.
    If customer_id is provided, sends to ALL registered phones for that customer.
    """
    customer_id   = data.get("customer_id", "")
    customer_name = data.get("customer_name", "")
    message       = data.get("message", "")
    branch_id     = data.get("branch_id", "")
    branch_name   = data.get("branch_name", "")

    if not message:
        raise HTTPException(status_code=400, detail="Message is required")

    # Resolve phones — all registered numbers when customer_id given
    if customer_id:
        customer_doc = await db.customers.find_one(
            {"id": customer_id}, {"_id": 0, "phones": 1, "phone": 1, "name": 1, "branch_id": 1}
        )
        if customer_doc:
            phones_to_send = customer_doc.get("phones") or (
                [customer_doc["phone"]] if customer_doc.get("phone") else []
            )
            customer_name = customer_name or customer_doc.get("name", "")
            branch_id = branch_id or customer_doc.get("branch_id", "")
        else:
            phones_to_send = [data.get("phone", "")] if data.get("phone") else []
    else:
        phones_to_send = [data.get("phone", "")] if data.get("phone") else []

    phones_to_send = [p.strip() for p in phones_to_send if p and p.strip()]
    if not phones_to_send:
        raise HTTPException(status_code=400, detail="No phone numbers to send to")

    # Look up branch name if not provided
    if branch_id and not branch_name:
        br = await db.branches.find_one({"id": branch_id}, {"_id": 0, "name": 1})
        branch_name = (br or {}).get("name", "")

    # Auto-append signature server-side — cannot be removed or edited by the sender
    biz = await db.settings.find_one({"key": "company_info"}, {"_id": 0})
    company_name = (biz or {}).get("value", {}).get("name", "")
    sig_parts = [p for p in [company_name, branch_name] if p]
    message_with_sig = message + ("\n\n- " + " | ".join(sig_parts) if sig_parts else "")

    sent_by_name    = user.get("full_name") or user.get("email", "")
    organization_id = user.get("organization_id", "")

    queued = []
    for phone in phones_to_send:
        doc = {
            "id": new_id(),
            "organization_id": organization_id,
            "template_key": "custom",
            "customer_id": customer_id,
            "customer_name": customer_name,
            "phone": phone,
            "message": message_with_sig,
            "status": "pending",
            "trigger": "manual",
            "trigger_ref": "",
            "dedup_key": "",
            "branch_id": branch_id,
            "branch_name": branch_name,
            "sent_by_name": sent_by_name,
            "created_at": now_iso(),
            "sent_at": None,
            "failed_at": None,
            "error": None,
            "retry_count": 0,
        }
        await db.sms_queue.insert_one(doc)
        del doc["_id"]
        queued.append(doc)

    return queued[0] if len(queued) == 1 else {"queued": len(queued), "phones": phones_to_send}


@router.post("/blast")
async def send_promo_blast(data: dict, user=Depends(get_current_user)):
    """Send a promotional message to multiple customers.
    Body: { message, customer_ids?: [], filter?: { min_balance, branch_id }, branch_id, branch_name }
    """
    check_perm(user, "settings", "edit")
    message_text = data.get("message", "")
    if not message_text:
        raise HTTPException(status_code=400, detail="Message is required")

    branch_id = data.get("branch_id", "")
    branch_name = data.get("branch_name", "")
    customer_ids = data.get("customer_ids")
    filter_opts = data.get("filter", {})

    # Build customer query
    query = {"active": True}
    if customer_ids:
        query["id"] = {"$in": customer_ids}
    else:
        if filter_opts.get("branch_id"):
            query["branch_id"] = filter_opts["branch_id"]
        if filter_opts.get("min_balance"):
            query["balance"] = {"$gte": float(filter_opts["min_balance"])}

    customers = await db.customers.find(query, {"_id": 0, "id": 1, "name": 1, "phone": 1}).to_list(5000)

    # Get company name from settings
    biz = await db.settings.find_one({"key": "company_info"}, {"_id": 0})
    company_name = (biz or {}).get("value", {}).get("name", "")

    queued = 0
    for c in customers:
        phone = c.get("phone", "")
        if not phone:
            continue
        rendered = message_text.replace("<customer_name>", c.get("name", "Customer"))
        doc = {
            "id": new_id(),
            "template_key": "promo_blast",
            "customer_id": c["id"],
            "customer_name": c.get("name", ""),
            "phone": phone.strip(),
            "message": f"{rendered} - {company_name} {branch_name}".strip(),
            "status": "pending",
            "trigger": "manual",
            "trigger_ref": "blast",
            "dedup_key": "",
            "branch_id": branch_id,
            "branch_name": branch_name,
            "created_at": now_iso(),
            "sent_at": None,
            "failed_at": None,
            "error": None,
            "retry_count": 0,
        }
        await db.sms_queue.insert_one(doc)
        queued += 1

    return {"queued": queued, "total_customers": len(customers), "skipped_no_phone": len(customers) - queued}



# ── Credit Reminder Blast ─────────────────────────────────────────────────────

@router.post("/credit-blast")
async def credit_reminder_blast(data: dict, user=Depends(get_current_user)):
    """Smart credit reminder blast.
    Automatically selects message template per customer:
      Option A (short)    — has balance, no overdue, due > 15 days away
      Option B (detailed) — has overdue OR due within 15 days

    Pass dry_run=true (default) for a preview without queueing.
    Pass dry_run=false to actually queue.
    """
    check_perm(user, "settings", "edit")
    dry_run   = data.get("dry_run", True)
    min_bal   = float(data.get("min_balance", 0))
    branch_id = data.get("branch_id", "")

    today     = date.today()
    today_str = today.isoformat()

    # 1. Customers with outstanding balance
    cust_query: dict = {"active": True, "balance": {"$gt": min_bal}}
    if branch_id:
        cust_query["branch_id"] = branch_id
    customers = await db.customers.find(cust_query, {"_id": 0}).to_list(5000)
    if not customers:
        return {"dry_run": dry_run, "total_customers": 0, "total_sms": 0,
                "short_count": 0, "detailed_count": 0, "preview": [], "queued": 0}

    # 2. Open invoices for all these customers in one query
    cids      = [c["id"] for c in customers]
    inv_query: dict = {
        "customer_id": {"$in": cids},
        "status": {"$nin": ["paid", "voided"]},
        "balance": {"$gt": 0},
    }
    if branch_id:
        inv_query["branch_id"] = branch_id
    invoices = await db.invoices.find(
        inv_query, {"_id": 0, "customer_id": 1, "balance": 1, "due_date": 1}
    ).to_list(100000)
    inv_map: dict = {}
    for inv in invoices:
        inv_map.setdefault(inv["customer_id"], []).append(inv)

    # 3. Branch names in one query
    all_bids = list({c.get("branch_id", "") for c in customers if c.get("branch_id")})
    branch_docs = await db.branches.find(
        {"id": {"$in": all_bids}}, {"_id": 0, "id": 1, "name": 1}
    ).to_list(100)
    branch_map = {b["id"]: b["name"] for b in branch_docs}

    # 4. Company name
    biz          = await db.settings.find_one({"key": "company_info"}, {"_id": 0})
    company_name = (biz or {}).get("value", {}).get("name", "")

    sent_by_name    = user.get("full_name") or user.get("email", "")
    organization_id = user.get("organization_id", "")

    short_count    = 0
    detailed_count = 0
    preview        = []
    total_sms      = 0
    queued         = 0

    for customer in customers:
        cid           = customer["id"]
        cust_invs     = inv_map.get(cid, [])
        cust_branch   = branch_map.get(customer.get("branch_id", ""), "")
        total_balance = customer.get("balance", 0)
        interest_rate = customer.get("interest_rate", 0)

        # Overdue vs future invoices
        overdue_invs  = [i for i in cust_invs if i.get("due_date") and i["due_date"] < today_str]
        future_invs   = [i for i in cust_invs if i.get("due_date") and i["due_date"] >= today_str]

        overdue_amount = sum(i["balance"] for i in overdue_invs)
        days_overdue   = 0
        if overdue_invs:
            oldest       = min(i["due_date"] for i in overdue_invs)
            days_overdue = (today - date.fromisoformat(oldest)).days

        next_due_date   = None
        next_due_amount = 0
        days_until_due  = None
        if future_invs:
            next_due_date   = min(i["due_date"] for i in future_invs)
            next_due_amount = sum(i["balance"] for i in future_invs if i["due_date"] == next_due_date)
            days_until_due  = (date.fromisoformat(next_due_date) - today).days

        est_interest = total_balance * interest_rate / 100 if interest_rate else 0

        # Smart template selection
        use_b = overdue_amount > 0 or (days_until_due is not None and days_until_due <= 15)
        label = "detailed" if use_b else "short"

        company_branch = f"{company_name} - {cust_branch}".strip(" -")

        if use_b:
            # Option B — Detailed
            lines = [f"Hi {customer['name']}, balanse summary mo sa {company_branch}:"]
            lines.append(f"\nKabuuang balanse: P{total_balance:,.2f}")
            if overdue_amount > 0:
                lines.append(f"OVERDUE: P{overdue_amount:,.2f} ({days_overdue} araw na!)")
            if next_due_date:
                lines.append(f"Susunod na due: P{next_due_amount:,.2f} sa {next_due_date} ({days_until_due} araw na lang)")
            if interest_rate > 0:
                lines.append(f"Est. interest: ~P{est_interest:,.2f}/buwan ({interest_rate}%/mo)")
            lines.append("\nPaki-bisita o bayaran na po agad. Salamat!")
            message = "\n".join(lines)
        else:
            # Option A — Short
            due_line = ""
            if next_due_date and days_until_due is not None:
                due_line = f"\n\nPinakamalapit na due: P{next_due_amount:,.2f} sa {next_due_date} ({days_until_due} araw na lang po)"
            int_line  = (f"\nPara maiwasan ang {interest_rate}%/mo na interest, paki-settle na bago mag-due."
                         if interest_rate > 0 else "")
            message = (
                f"Hi {customer['name']}! Paalala po mula sa {company_branch}.\n\n"
                f"Kasalukuyang balanse: P{total_balance:,.2f}"
                f"{due_line}"
                f"{int_line}\n\nSalamat!"
            )
        phones = customer.get("phones") or ([customer["phone"]] if customer.get("phone") else [])
        phones = [p for p in phones if p]
        if not phones:
            continue

        # Count only customers that will actually receive SMS
        if use_b:
            detailed_count += 1
        else:
            short_count += 1

        total_sms += len(phones)

        # Collect up to 2 preview samples (1 short + 1 detailed if available)
        if len(preview) < 2 and not any(p["template"] == label for p in preview):
            preview.append({
                "customer_name": customer["name"],
                "phones": phones,
                "template": label,
                "message": message,
                "total_balance": total_balance,
                "overdue_amount": overdue_amount,
                "days_until_due": days_until_due,
            })

        if not dry_run:
            for phone in phones:
                doc = {
                    "id": new_id(),
                    "organization_id": organization_id,
                    "template_key": "credit_reminder_blast",
                    "customer_id": cid,
                    "customer_name": customer["name"],
                    "phone": phone,
                    "message": message,
                    "status": "pending",
                    "trigger": "manual",
                    "trigger_ref": "credit_blast",
                    "dedup_key": "",
                    "branch_id": customer.get("branch_id", ""),
                    "branch_name": cust_branch,
                    "sent_by_name": sent_by_name,
                    "created_at": now_iso(),
                    "sent_at": None,
                    "failed_at": None,
                    "error": None,
                    "retry_count": 0,
                }
                await db.sms_queue.insert_one(doc)
                del doc["_id"]
                queued += 1

    return {
        "dry_run": dry_run,
        "total_customers": short_count + detailed_count,
        "total_sms": total_sms if dry_run else queued,
        "short_count": short_count,
        "detailed_count": detailed_count,
        "preview": preview,
        "queued": queued,
    }



# ── Stats ───────────────────────────────────────────────────────────────────

@router.get("/stats")
async def sms_stats(branch_id: Optional[str] = None, user=Depends(get_current_user)):
    """Get SMS queue statistics plus branch-specific unread inbox count."""
    pipeline = [
        {"$group": {"_id": "$status", "count": {"$sum": 1}}}
    ]
    results = await db.sms_queue.aggregate(pipeline).to_list(10)
    stats = {r["_id"]: r["count"] for r in results}

    # Unread incoming messages — branch-scoped for the badge
    unread_query: dict = {"read": False, "customer_id": {"$ne": ""}}
    if branch_id:
        unread_query["branch_id"] = branch_id
    unread = await db.sms_inbox.count_documents(unread_query)

    return {
        "pending": stats.get("pending", 0),
        "sent": stats.get("sent", 0),
        "failed": stats.get("failed", 0),
        "skipped": stats.get("skipped", 0),
        "total": sum(stats.values()),
        "unread": unread,
    }


# ══════════════════════════════════════════════════════════════════════════════
# PHONE CHECK — Whitelist filter used by Android gateway before processing SMS
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/check-phone")
async def check_phone(phone: str, user=Depends(get_current_user)):
    """Check if a phone number belongs to a known customer.
    Android app calls this before processing any incoming or outgoing SMS.
    Unknown numbers are silently ignored by the app.
    """
    normalized = phone.lstrip("+")
    if normalized.startswith("63") and len(normalized) > 10:
        normalized = "0" + normalized[2:]
    phones = list({phone, normalized})

    customer = await _raw_db.customers.find_one(
        {"$or": [{"phone": {"$in": phones}}, {"phones": {"$in": phones}}],
         "organization_id": user.get("organization_id")},
        {"_id": 0, "id": 1, "name": 1, "branch_id": 1}
    )
    if not customer:
        customer = await _raw_db.customers.find_one(
            {"$or": [{"phone": {"$in": phones}}, {"phones": {"$in": phones}}]},
            {"_id": 0, "id": 1, "name": 1, "branch_id": 1}
        )

    if customer:
        return {"exists": True, "customer_name": customer.get("name", ""), "customer_id": customer.get("id", "")}
    return {"exists": False, "customer_name": "", "customer_id": ""}


# ══════════════════════════════════════════════════════════════════════════════
# SENT FROM DEVICE — Outgoing SMS typed directly on the gateway phone
# No signature = Admin sent it. Visible to all branches.
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/sent-from-device")
async def sent_from_device(data: dict, user=Depends(get_current_user)):
    """Gateway app posts an SMS it sent directly from the native SMS app.
    These are attributed to Admin (device holder) with no branch scope.
    They appear in ALL branch conversation views for that customer.
    """
    phone = (data.get("phone") or "").strip()
    message = (data.get("message") or "").strip()
    if not phone or not message:
        raise HTTPException(status_code=400, detail="phone and message required")

    # Normalize to local format to match customer records
    normalized = phone.lstrip("+")
    if normalized.startswith("63") and len(normalized) > 10:
        normalized = "0" + normalized[2:]
    stored_phone = normalized
    phones = list({phone, normalized})

    # Look up customer — checks both primary phone and phones[] array
    customer = await _raw_db.customers.find_one(
        {"$or": [{"phone": {"$in": phones}}, {"phones": {"$in": phones}}],
         "organization_id": user.get("organization_id")},
        {"_id": 0, "id": 1, "name": 1, "branch_id": 1}
    )
    if not customer:
        customer = await _raw_db.customers.find_one(
            {"$or": [{"phone": {"$in": phones}}, {"phones": {"$in": phones}}]},
            {"_id": 0, "id": 1, "name": 1, "branch_id": 1}
        )

    # Resolve organization_id — context var (company admin call) → branch lookup → user token
    from config import get_org_context
    org_id = get_org_context()
    if not org_id and customer and customer.get("branch_id"):
        br = await _raw_db.branches.find_one({"id": customer["branch_id"]}, {"_id": 0, "organization_id": 1})
        org_id = (br or {}).get("organization_id", "")
    if not org_id:
        org_id = user.get("organization_id") or ""

    # Build signature: full company name + "| Admin"
    # e.g. "Sibugay Agricultural Supply | Admin"
    biz = await _raw_db.settings.find_one({"key": "company_info", "organization_id": org_id}, {"_id": 0})
    if not biz and org_id:
        biz = await _raw_db.settings.find_one({"key": "company_info", "organization_id": {"$exists": False}}, {"_id": 0})
    # Only fall back to any company if org_id is genuinely unknown (avoid wrong-org bleed)
    if not biz and not org_id:
        biz = await _raw_db.settings.find_one({"key": "company_info"}, {"_id": 0})
    company_name = (biz or {}).get("value", {}).get("name", "")
    device_sig = f"\n\n- {company_name} | Admin" if company_name else "\n\n- Admin"

    # Don't double-sign: skip if message already contains our signature marker
    message_with_sig = message if "\n\n-" in message else message + device_sig

    # Store as already-sent with Admin attribution, no branch scope
    doc = {
        "id": new_id(),
        "organization_id": org_id,
        "template_key": "custom",
        "customer_id": customer["id"] if customer else "",
        "customer_name": customer["name"] if customer else stored_phone,
        "phone": stored_phone,
        "message": message_with_sig,
        "status": "sent",           # Already delivered — skip the queue
        "trigger": "device",
        "trigger_ref": "admin_device",
        "dedup_key": "",
        "branch_id": None,          # No branch — visible to all
        "branch_name": "",
        "sent_by_name": "Admin (via device)",
        "created_at": data.get("sent_at", now_iso()),
        "sent_at": data.get("sent_at", now_iso()),
        "failed_at": None,
        "error": None,
        "retry_count": 0,
    }
    await _raw_db.sms_queue.insert_one(doc)
    del doc["_id"]
    return doc


# ══════════════════════════════════════════════════════════════════════════════
# INBOX — Incoming SMS from gateway phone (replies from customers)
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/inbox")
async def receive_inbox_sms(data: dict, user=Depends(get_current_user)):
    """Gateway app posts ALL incoming SMS here — no filtering on the phone side.
    Backend classifies: registered customer → branch-scoped; unknown → admin-only inbox.
    """
    phone = (data.get("phone") or "").strip()
    message = (data.get("message") or "").strip()
    if not phone or not message:
        raise HTTPException(status_code=400, detail="phone and message required")

    # Always store in local format (09...) to unify +63 and 09 variants
    normalized = phone.lstrip("+")
    if normalized.startswith("63") and len(normalized) > 10:
        normalized = "0" + normalized[2:]
    stored_phone = normalized
    phones = list({phone, normalized})

    # Try to match customer — checks primary phone AND phones[] array
    customer = await _raw_db.customers.find_one(
        {"$or": [{"phone": {"$in": phones}}, {"phones": {"$in": phones}}],
         "organization_id": user.get("organization_id")},
        {"_id": 0, "id": 1, "name": 1, "branch_id": 1}
    )
    if not customer:
        customer = await _raw_db.customers.find_one(
            {"$or": [{"phone": {"$in": phones}}, {"phones": {"$in": phones}}]},
            {"_id": 0, "id": 1, "name": 1, "branch_id": 1}
        )

    registered = customer is not None
    doc = {
        "id": new_id(),
        "phone": stored_phone,
        "message": message,
        "direction": "in",
        "registered": registered,                           # True = known customer
        "customer_id": customer["id"] if customer else "",
        "customer_name": customer["name"] if customer else stored_phone,
        "branch_id": customer.get("branch_id", "") if customer else "",
        "received_at": data.get("received_at", now_iso()),
        "created_at": now_iso(),
        "read": False,
    }
    await db.sms_inbox.insert_one(doc)
    del doc["_id"]
    return doc


@router.get("/conversations")
async def list_conversations(
    branch_id: Optional[str] = None,
    section: str = "customers",   # "customers" | "unknown"
    user=Depends(get_current_user),
):
    """List conversations grouped by phone.
    section=customers  → registered customers, branch-filtered (default)
    section=unknown    → unregistered/unknown numbers, admin-only
    """

    # ── Unknown numbers section (admin only) ────────────────────────────────
    if section == "unknown":
        if user.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Admin access required for unknown numbers inbox")
        pipeline = [
            # Messages where no customer was matched — customer_id is empty
            {"$match": {"customer_id": ""}},
            {"$sort": {"created_at": -1}},
            {"$group": {
                "_id": "$phone",
                "last_message": {"$first": "$message"},
                "last_time": {"$first": "$created_at"},
                "unread": {"$sum": {"$cond": [{"$eq": ["$read", False]}, 1, 0]}},
            }},
        ]
        items = await db.sms_inbox.aggregate(pipeline).to_list(500)
        result = [
            {
                "phone": item["_id"],
                "customer_name": item["_id"],   # Phone number as display name
                "customer_id": "",
                "last_message": item.get("last_message", ""),
                "last_time": item.get("last_time", ""),
                "last_direction": "in",
                "unread": item.get("unread", 0),
                "branch_ids": [],
                "branch_names": [],
                "registered": False,
            }
            for item in items
        ]
        return sorted(result, key=lambda x: x.get("last_time", ""), reverse=True)

    # ── Customers section — grouped by customer_id (multi-phone safe) ────────
    # Branch filter: collect customer_ids that have activity in this branch
    cid_filter: dict = {"customer_id": {"$ne": ""}}
    if branch_id:
        queue_cids = await db.sms_queue.distinct(
            "customer_id",
            {"branch_id": branch_id, "status": {"$in": ["sent", "pending", "failed"]}, "customer_id": {"$ne": ""}},
        )
        inbox_cids = await db.sms_inbox.distinct(
            "customer_id", {"branch_id": branch_id, "customer_id": {"$ne": ""}},
        )
        cids = list(set(queue_cids) | set(inbox_cids))
        if not cids:
            return []
        cid_filter = {"customer_id": {"$in": cids}}

    # Latest outgoing per customer — from ALL branches (collaboration context)
    out_pipeline = [
        {"$match": {"status": {"$in": ["sent", "pending", "failed"]}, "customer_id": {"$ne": ""}, **cid_filter}},
        {"$sort": {"created_at": -1}},
        {"$group": {
            "_id": "$customer_id",
            "last_message": {"$first": "$message"},
            "last_time": {"$first": "$created_at"},
            "customer_name": {"$first": "$customer_name"},
            "customer_id": {"$first": "$customer_id"},
            "phones": {"$addToSet": "$phone"},
            "branch_ids": {"$addToSet": "$branch_id"},
            "branch_names": {"$addToSet": "$branch_name"},
        }},
    ]
    # Latest incoming per customer
    in_pipeline = [
        {"$match": {"customer_id": {"$ne": ""}, **cid_filter}},
        {"$sort": {"created_at": -1}},
        {"$group": {
            "_id": "$customer_id",
            "last_message": {"$first": "$message"},
            "last_time": {"$first": "$created_at"},
            "customer_name": {"$first": "$customer_name"},
            "customer_id": {"$first": "$customer_id"},
            "phones": {"$addToSet": "$phone"},
            "unread": {"$sum": {"$cond": [{"$eq": ["$read", False]}, 1, 0]}},
        }},
    ]

    out_items = await db.sms_queue.aggregate(out_pipeline).to_list(500)
    in_items  = await db.sms_inbox.aggregate(in_pipeline).to_list(500)

    # Merge by customer_id
    merged: dict = {}
    for item in out_items:
        cid = item["_id"]
        branch_ids   = [b for b in item.get("branch_ids",  []) if b]
        branch_names = [b for b in item.get("branch_names",[]) if b]
        phones       = [p for p in item.get("phones", [])       if p]
        merged[cid] = {
            "customer_id": cid,
            "customer_name": item.get("customer_name", cid),
            "phone": phones[0] if phones else "",
            "phones": phones,
            "last_message": item.get("last_message", ""),
            "last_time": item.get("last_time", ""),
            "last_direction": "out",
            "unread": 0,
            "branch_ids": branch_ids,
            "branch_names": branch_names,
        }
    for item in in_items:
        cid = item["_id"]
        unread = item.get("unread", 0)
        phones = [p for p in item.get("phones", []) if p]
        if cid in merged:
            if item.get("last_time", "") > merged[cid]["last_time"]:
                merged[cid]["last_message"]  = item.get("last_message", "")
                merged[cid]["last_time"]     = item.get("last_time", "")
                merged[cid]["last_direction"] = "in"
            merged[cid]["unread"] = unread
            for p in phones:
                if p not in merged[cid]["phones"]:
                    merged[cid]["phones"].append(p)
        else:
            merged[cid] = {
                "customer_id": cid,
                "customer_name": item.get("customer_name", cid),
                "phone": phones[0] if phones else "",
                "phones": phones,
                "last_message": item.get("last_message", ""),
                "last_time": item.get("last_time", ""),
                "last_direction": "in",
                "unread": unread,
                "branch_ids": [],
                "branch_names": [],
            }

    return sorted(merged.values(), key=lambda x: x.get("last_time", ""), reverse=True)


@router.get("/conversation/customer/{customer_id}")
async def get_conversation_by_customer(customer_id: str, user=Depends(get_current_user)):
    """Full message thread for a customer — all their phone numbers merged into one thread."""
    # All phones this customer has ever used (from messages + customer record)
    queue_phones = await db.sms_queue.distinct("phone", {"customer_id": customer_id})
    inbox_phones = await db.sms_inbox.distinct("phone", {"customer_id": customer_id})
    customer_doc = await db.customers.find_one({"id": customer_id}, {"_id": 0, "name": 1, "phones": 1, "phone": 1})
    cust_phones  = (customer_doc or {}).get("phones") or (
        [(customer_doc or {}).get("phone")] if (customer_doc or {}).get("phone") else []
    )
    all_phones = list(set(queue_phones) | set(inbox_phones) | set(cust_phones))

    out_msgs = await db.sms_queue.find(
        {"customer_id": customer_id, "status": {"$in": ["sent", "pending", "failed"]}},
        {"_id": 0, "id": 1, "message": 1, "created_at": 1, "status": 1,
         "customer_name": 1, "template_key": 1, "branch_id": 1, "branch_name": 1,
         "sent_by_name": 1, "phone": 1}
    ).sort("created_at", 1).to_list(500)
    for m in out_msgs:
        m["direction"] = "out"

    in_msgs = await db.sms_inbox.find(
        {"$or": [{"customer_id": customer_id}, {"phone": {"$in": all_phones}}]},
        {"_id": 0, "id": 1, "message": 1, "created_at": 1, "customer_name": 1, "phone": 1}
    ).sort("created_at", 1).to_list(500)
    for m in in_msgs:
        m["direction"] = "in"
        m["status"] = "received"

    # Mark all as read
    await db.sms_inbox.update_many(
        {"$or": [{"customer_id": customer_id}, {"phone": {"$in": all_phones}}]},
        {"$set": {"read": True}}
    )

    all_msgs = sorted(out_msgs + in_msgs, key=lambda x: x.get("created_at", ""))
    customer_name = (customer_doc or {}).get("name", customer_id)
    # Return ALL registered phones (including those not yet used in messages)
    all_registered_phones = sorted(p for p in all_phones if p)
    return {
        "customer_id": customer_id,
        "customer_name": customer_name,
        "phones": all_registered_phones,
        "messages": all_msgs,
        "registered": True,
    }



@router.get("/conversation/{phone}")
async def get_conversation(phone: str, user=Depends(get_current_user)):
    """Get full message thread for a phone number — sent + received merged."""
    # Build all phone variants: 09... and +63... so old and new records are both found
    normalized = phone.lstrip("+")
    if normalized.startswith("63") and len(normalized) > 10:
        normalized = "0" + normalized[2:]
    variants: set = {phone, normalized}
    # Also add the +63 international variant of any 09... number
    if normalized.startswith("09") and len(normalized) == 11:
        variants.add("+63" + normalized[1:])
    phones = list(variants)

    # Outgoing messages
    out_msgs = await db.sms_queue.find(
        {"phone": {"$in": phones}, "status": {"$in": ["sent", "pending", "failed"]}},
        {"_id": 0, "id": 1, "message": 1, "created_at": 1, "status": 1,
         "customer_name": 1, "template_key": 1, "branch_id": 1, "branch_name": 1, "sent_by_name": 1}
    ).sort("created_at", 1).to_list(500)
    for m in out_msgs:
        m["direction"] = "out"

    # Incoming messages
    in_msgs = await db.sms_inbox.find(
        {"phone": {"$in": phones}},
        {"_id": 0, "id": 1, "message": 1, "created_at": 1, "customer_name": 1, "registered": 1}
    ).sort("created_at", 1).to_list(500)
    for m in in_msgs:
        m["direction"] = "in"
        m["status"] = "received"

    # Mark inbox as read
    await db.sms_inbox.update_many({"phone": {"$in": phones}}, {"$set": {"read": True}})

    all_msgs = sorted(out_msgs + in_msgs, key=lambda x: x.get("created_at", ""))
    customer_name = all_msgs[0].get("customer_name", phone) if all_msgs else phone
    # Is this a registered customer conversation?
    registered = any(m.get("registered", True) for m in in_msgs) or bool(out_msgs)
    return {"phone": phone, "customer_name": customer_name, "messages": all_msgs, "registered": registered}


# ══════════════════════════════════════════════════════════════════════════════
# ASSIGN PHONE — Link an unknown number to an existing customer
# Migrates all past inbox messages to the customer's branch
# ══════════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════════
# GATEWAY ACTIVITY LOG — Android APK keylogger / debug feed synced to web
# POST /gateway/log       — single entry (used during foreground)
# POST /gateway/logs/batch — bulk entries (used for buffered offline logs)
# GET  /gateway/logs      — web UI fetches the feed
# DELETE /gateway/logs    — admin clears old logs
# ══════════════════════════════════════════════════════════════════════════════

VALID_LEVELS = {"INFO", "WARN", "ERROR", "DEBUG"}
VALID_EVENTS = {
    "boot", "poll", "send_queued", "sent", "failed",
    "received", "device_sent", "sync", "token_loaded",
    "observer_start", "observer_stop", "db_error", "error", "custom",
}


def _build_log_doc(entry: dict, org_id: str) -> dict:
    level = (entry.get("level") or "INFO").upper()
    if level not in VALID_LEVELS:
        level = "INFO"
    event_type = (entry.get("event_type") or "custom").lower()
    if event_type not in VALID_EVENTS:
        event_type = "custom"
    return {
        "id": new_id(),
        "organization_id": org_id,
        "level": level,
        "event_type": event_type,
        "message": (entry.get("message") or "").strip(),
        "phone": (entry.get("phone") or "").strip(),
        "queue_id": (entry.get("queue_id") or "").strip(),
        "device_id": (entry.get("device_id") or "").strip(),
        "metadata": entry.get("metadata") or {},
        "created_at": entry.get("created_at") or now_iso(),
        "logged_at": now_iso(),
    }


@router.post("/gateway/log")
async def post_gateway_log(data: dict, user=Depends(get_current_user)):
    """Android APK posts a single activity log entry for real-time debugging."""
    if not data.get("message", "").strip():
        raise HTTPException(status_code=400, detail="message required")
    org_id = user.get("organization_id", "")
    doc = _build_log_doc(data, org_id)
    await _raw_db.sms_gateway_logs.insert_one(doc)
    del doc["_id"]
    return {"ok": True}


@router.post("/gateway/logs/batch")
async def post_gateway_logs_batch(data: dict, user=Depends(get_current_user)):
    """Android APK posts buffered log entries in one call (offline-first support)."""
    entries = data.get("entries") or []
    if not entries:
        return {"inserted": 0}
    org_id = user.get("organization_id", "")
    docs = [_build_log_doc(e, org_id) for e in entries[:500] if (e.get("message") or "").strip()]
    if docs:
        await _raw_db.sms_gateway_logs.insert_many(docs)
        for d in docs:
            del d["_id"]
    return {"inserted": len(docs)}


@router.get("/gateway/logs")
async def get_gateway_logs(
    level: Optional[str] = None,
    event_type: Optional[str] = None,
    limit: int = 300,
    skip: int = 0,
    user=Depends(get_current_user),
):
    """Fetch gateway activity logs for the web debug panel."""
    query: dict = {"organization_id": user.get("organization_id", "")}
    if level and level.upper() not in ("ALL", ""):
        query["level"] = level.upper()
    if event_type and event_type not in ("all", ""):
        query["event_type"] = event_type.lower()
    total = await _raw_db.sms_gateway_logs.count_documents(query)
    items = (
        await _raw_db.sms_gateway_logs.find(query, {"_id": 0})
        .sort("created_at", -1)
        .skip(skip)
        .limit(min(limit, 500))
        .to_list(min(limit, 500))
    )
    return {"items": items, "total": total}


@router.delete("/gateway/logs")
async def clear_gateway_logs(user=Depends(get_current_user)):
    """Clear all gateway logs for this organization (admin only)."""
    check_perm(user, "settings", "edit")
    org_id = user.get("organization_id", "")
    result = await _raw_db.sms_gateway_logs.delete_many({"organization_id": org_id})
    return {"deleted": result.deleted_count}


@router.patch("/assign-phone")
async def assign_phone_to_customer(data: dict, user=Depends(get_current_user)):
    """Assign an unregistered phone number to an existing customer.
    All past sms_inbox records for that phone are migrated to the customer's branch.
    The customer's phone field is updated if they don't already have one.
    """
    check_perm(user, "settings", "edit")
    phone = (data.get("phone") or "").strip()
    customer_id = data.get("customer_id", "")
    if not phone or not customer_id:
        raise HTTPException(status_code=400, detail="phone and customer_id required")

    # Normalize phone
    normalized = phone.lstrip("+")
    if normalized.startswith("63") and len(normalized) > 10:
        normalized = "0" + normalized[2:]
    phones = list({phone, normalized})

    # Look up target customer
    customer = await db.customers.find_one(
        {"id": customer_id}, {"_id": 0, "id": 1, "name": 1, "branch_id": 1, "phone": 1}
    )
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    branch_id = customer.get("branch_id", "")

    # ADD the new phone to customer's phones array (not replace)
    await db.customers.update_one(
        {"id": customer_id},
        {"$addToSet": {"phones": normalized}}
    )
    # If customer has no primary phone yet, set it
    if not customer.get("phone"):
        await db.customers.update_one(
            {"id": customer_id}, {"$set": {"phone": normalized, "updated_at": now_iso()}}
        )

    # Migrate all inbox messages for this phone to the customer
    inbox_result = await _raw_db.sms_inbox.update_many(
        {"phone": {"$in": phones}},
        {"$set": {
            "customer_id": customer["id"],
            "customer_name": customer["name"],
            "branch_id": branch_id,
            "registered": True,
            "phone": normalized,
        }}
    )
    # Also update any unattributed outgoing queue messages for this phone
    await _raw_db.sms_queue.update_many(
        {"phone": {"$in": phones}, "customer_id": ""},
        {"$set": {
            "customer_id": customer["id"],
            "customer_name": customer["name"],
            "branch_id": branch_id,
        }}
    )

    return {
        "migrated_messages": inbox_result.modified_count,
        "customer_name": customer["name"],
        "customer_id": customer["id"],
        "branch_id": branch_id,
        "phone": normalized,
    }

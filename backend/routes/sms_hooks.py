"""
SMS trigger hooks — called from invoice creation, payment receipt, etc.
Each function is fire-and-forget (errors logged, never blocks the caller).
Uses _raw_db (unscoped) for lookups to bypass tenant ContextVar mutations.
organization_id is resolved explicitly and passed to queue_sms for proper isolation.
"""
from config import _raw_db as raw_db, logger
from utils import now_iso, new_id


async def _resolve_org_id(branch_id: str) -> str:
    """Resolve organization_id from a branch_id. Returns empty string if not found."""
    if not branch_id:
        return ""
    branch = await raw_db.branches.find_one({"id": branch_id}, {"_id": 0, "organization_id": 1})
    return (branch or {}).get("organization_id", "") if branch else ""


async def get_company_name(organization_id: str = "") -> str:
    """Get business name — org-scoped first, fallback to any company_info setting."""
    biz = None
    if organization_id:
        biz = await raw_db.settings.find_one(
            {"key": "company_info", "organization_id": organization_id}, {"_id": 0}
        )
    if not biz:
        biz = await raw_db.settings.find_one({"key": "company_info"}, {"_id": 0})
    if not biz:
        return "AgriBooks"
    return biz.get("value", {}).get("name", "AgriBooks")


async def get_branch_name(branch_id: str) -> str:
    if not branch_id:
        return ""
    branch = await raw_db.branches.find_one({"id": branch_id}, {"_id": 0, "name": 1})
    return branch.get("name", "") if branch else ""


async def on_credit_sale_created(invoice: dict):
    """Called after a credit sale invoice is created with balance > 0."""
    try:
        from routes.sms import queue_sms
        customer_id = invoice.get("customer_id", "")
        if not customer_id:
            return
        customer = await raw_db.customers.find_one({"id": customer_id}, {"_id": 0})
        if not customer:
            return
        phones = customer.get("phones") or ([customer["phone"]] if customer.get("phone") else [])
        if not phones:
            return

        branch_id = invoice.get("branch_id", "")
        org_id = await _resolve_org_id(branch_id)
        company_name = await get_company_name(org_id)
        branch_name = await get_branch_name(branch_id)
        variables = {
            "customer_name": customer.get("name", invoice.get("customer_name", "")),
            "amount": f"{invoice.get('balance', 0):,.2f}",
            "company_name": company_name,
            "branch_name": branch_name,
            "date": invoice.get("order_date", ""),
            "due_date": invoice.get("due_date", ""),
            "total_balance": f"{customer.get('balance', 0):,.2f}",
        }
        for phone in phones:
            if not phone:
                continue
            await queue_sms(
                template_key="credit_new",
                customer_id=customer_id,
                customer_name=customer.get("name", invoice.get("customer_name", "")),
                phone=phone,
                variables=variables,
                organization_id=org_id,
                branch_id=branch_id,
                branch_name=branch_name,
                trigger="auto",
                trigger_ref=invoice.get("id", ""),
                dedup_key=f"credit_new:{invoice.get('id', '')}:{phone}",
            )

        # CC: manager — needs awareness of new credit extended on their branch
        recipients_doc = await raw_db.system_settings.find_one(
            {"key": "collection_notification_recipients", "organization_id": org_id}, {"_id": 0}
        )
        manager_phone = (recipients_doc or {}).get("manager_phone", "")
        if manager_phone:
            mgr_msg = (
                f"[New Credit] {customer.get('name','')} — "
                f"P{invoice.get('balance', 0):,.2f} on {invoice.get('order_date','')}. "
                f"Invoice: {invoice.get('invoice_number','')}. "
                f"Due: {invoice.get('due_date','N/A')}. "
                f"Total balance: P{customer.get('balance', 0):,.2f}."
            )
            await raw_db.sms_queue.insert_one({
                "id": new_id(), "organization_id": org_id,
                "template_key": "credit_new_staff",
                "customer_id": customer_id,
                "customer_name": customer.get("name", invoice.get("customer_name", "")),
                "phone": manager_phone, "message": mgr_msg,
                "status": "pending", "trigger": "auto",
                "trigger_ref": invoice.get("id", ""),
                "dedup_key": f"credit_new_mgr:{invoice.get('id', '')}",
                "branch_id": branch_id, "branch_name": branch_name,
                "created_at": now_iso(),
                "sent_at": None, "failed_at": None, "error": None, "retry_count": 0,
            })
    except Exception as e:
        logger.error(f"SMS hook on_credit_sale_created failed: {e}")


async def on_payment_received(customer_id: str, amount_paid: float, remaining_balance: float,
                               branch_id: str = "", next_due_info: str = ""):
    """Called after a customer payment is applied."""
    try:
        from routes.sms import queue_sms
        customer = await raw_db.customers.find_one({"id": customer_id}, {"_id": 0})
        if not customer:
            return
        phones = customer.get("phones") or ([customer["phone"]] if customer.get("phone") else [])
        if not phones:
            return

        org_id = await _resolve_org_id(branch_id)
        company_name = await get_company_name(org_id)
        variables = {
            "customer_name": customer.get("name", ""),
            "amount_paid": f"{amount_paid:,.2f}",
            "remaining_balance": f"{remaining_balance:,.2f}",
            "next_due_info": next_due_info,
            "company_name": company_name,
        }
        for phone in phones:
            if not phone:
                continue
            await queue_sms(
                template_key="payment_received",
                customer_id=customer_id,
                customer_name=customer.get("name", ""),
                phone=phone,
                variables=variables,
                organization_id=org_id,
                branch_id=branch_id,
                branch_name=await get_branch_name(branch_id),
                trigger="auto",
                trigger_ref=f"payment:{customer_id}:{amount_paid}:{phone}",
            )
    except Exception as e:
        logger.error(f"SMS hook on_payment_received failed: {e}")


async def on_charge_applied(customer_id: str, charge_type: str, charge_amount: float,
                             total_balance: float, branch_id: str = ""):
    """Called after interest or penalty is generated."""
    try:
        from routes.sms import queue_sms
        customer = await raw_db.customers.find_one({"id": customer_id}, {"_id": 0})
        if not customer:
            return
        phones = customer.get("phones") or ([customer["phone"]] if customer.get("phone") else [])
        if not phones:
            return

        org_id = await _resolve_org_id(branch_id)
        company_name = await get_company_name(org_id)
        variables = {
            "charge_type": charge_type,
            "charge_amount": f"{charge_amount:,.2f}",
            "customer_name": customer.get("name", ""),
            "total_balance": f"{total_balance:,.2f}",
            "company_name": company_name,
        }
        for phone in phones:
            if not phone:
                continue
            await queue_sms(
                template_key="charge_applied",
                customer_id=customer_id,
                customer_name=customer.get("name", ""),
                phone=phone,
                variables=variables,
                organization_id=org_id,
                branch_id=branch_id,
                branch_name=await get_branch_name(branch_id),
                trigger="auto",
                trigger_ref=f"charge:{customer_id}:{charge_type}:{phone}",
            )

        # CC: manager — closes the loop; confirms charge was applied and customer notified
        recipients_doc = await raw_db.system_settings.find_one(
            {"key": "collection_notification_recipients", "organization_id": org_id}, {"_id": 0}
        )
        manager_phone = (recipients_doc or {}).get("manager_phone", "")
        if manager_phone:
            mgr_msg = (
                f"[{charge_type} Applied] {customer.get('name','')} — "
                f"P{charge_amount:,.2f} charged. "
                f"New total balance: P{total_balance:,.2f}. "
                f"Customer has been notified via SMS."
            )
            await raw_db.sms_queue.insert_one({
                "id": new_id(), "organization_id": org_id,
                "template_key": "charge_applied_staff",
                "customer_id": customer_id,
                "customer_name": customer.get("name", ""),
                "phone": manager_phone, "message": mgr_msg,
                "status": "pending", "trigger": "auto",
                "trigger_ref": f"charge:{customer_id}:{charge_type}",
                "dedup_key": f"charge_mgr:{customer_id}:{charge_type}:{now_iso()[:10]}",
                "branch_id": branch_id, "branch_name": await get_branch_name(branch_id),
                "created_at": now_iso(),
                "sent_at": None, "failed_at": None, "error": None, "retry_count": 0,
            })
    except Exception as e:
        logger.error(f"SMS hook on_charge_applied failed: {e}")



async def on_crop_season_started(crop_credit: dict, total_balance: float):
    """Called when a new Charged-to-Crop season is created."""
    try:
        from routes.sms import queue_sms
        customer_id = crop_credit.get("customer_id", "")
        customer = await raw_db.customers.find_one({"id": customer_id}, {"_id": 0})
        if not customer:
            return
        phones = customer.get("phones") or ([customer["phone"]] if customer.get("phone") else [])
        if not phones:
            return

        branch_id = crop_credit.get("branch_id", "")
        org_id = crop_credit.get("organization_id", "") or await _resolve_org_id(branch_id)
        company_name = await get_company_name(org_id)
        variables = {
            "customer_name": customer.get("name", crop_credit.get("customer_name", "")),
            "company_name": company_name,
            "planting_date": crop_credit.get("planting_date", ""),
            "harvest_date": crop_credit.get("season_end_date", ""),
            "total_balance": f"{total_balance:,.2f}",
        }
        for phone in phones:
            if not phone:
                continue
            await queue_sms(
                template_key="crop_season_started",
                customer_id=customer_id,
                customer_name=customer.get("name", ""),
                phone=phone,
                variables=variables,
                organization_id=org_id,
                branch_id=branch_id,
                branch_name=await get_branch_name(branch_id),
                trigger="auto",
                trigger_ref=crop_credit.get("id", ""),
                dedup_key=f"crop_season_started:{crop_credit.get('id', '')}:{phone}",
            )

        # CC: notify owner — new season is a significant financial commitment
        recipients_doc = await raw_db.system_settings.find_one(
            {"key": "collection_notification_recipients", "organization_id": org_id}, {"_id": 0}
        )
        owner_phone = (recipients_doc or {}).get("owner_phone", "")
        if owner_phone:
            owner_msg = (
                f"[New Crop Season] {customer.get('name','')} — "
                f"planting {crop_credit.get('planting_date','')} | "
                f"due {crop_credit.get('season_end_date','')}. "
                f"Balance: P{total_balance:,.2f}."
            )
            await raw_db.sms_queue.insert_one({
                "id": new_id(), "organization_id": org_id,
                "template_key": "crop_season_started_owner",
                "customer_id": customer_id, "customer_name": customer.get("name", ""),
                "phone": owner_phone, "message": owner_msg,
                "status": "pending", "trigger": "auto",
                "trigger_ref": crop_credit.get("id", ""),
                "dedup_key": f"crop_season_started_owner:{crop_credit.get('id', '')}",
                "branch_id": branch_id, "branch_name": await get_branch_name(branch_id),
                "created_at": now_iso(),
                "sent_at": None, "failed_at": None, "error": None, "retry_count": 0,
            })
    except Exception as e:
        logger.error(f"SMS hook on_crop_season_started failed: {e}")


async def on_crop_credit_added(crop_credit: dict, amount: float, invoice_number: str, total_balance: float):
    """Called when a new purchase is added to an active crop season."""
    try:
        from routes.sms import queue_sms
        customer_id = crop_credit.get("customer_id", "")
        customer = await raw_db.customers.find_one({"id": customer_id}, {"_id": 0})
        if not customer:
            return
        phones = customer.get("phones") or ([customer["phone"]] if customer.get("phone") else [])
        if not phones:
            return

        branch_id = crop_credit.get("branch_id", "")
        org_id = crop_credit.get("organization_id", "") or await _resolve_org_id(branch_id)
        company_name = await get_company_name(org_id)
        variables = {
            "customer_name": customer.get("name", crop_credit.get("customer_name", "")),
            "amount": f"{amount:,.2f}",
            "company_name": company_name,
            "invoice_number": invoice_number or "—",
            "total_balance": f"{total_balance:,.2f}",
            "harvest_date": crop_credit.get("season_end_date", ""),
        }
        for phone in phones:
            if not phone:
                continue
            await queue_sms(
                template_key="crop_credit_added",
                customer_id=customer_id,
                customer_name=customer.get("name", ""),
                phone=phone,
                variables=variables,
                organization_id=org_id,
                branch_id=branch_id,
                branch_name=await get_branch_name(branch_id),
                trigger="auto",
                trigger_ref=crop_credit.get("id", ""),
                dedup_key=f"crop_credit_added:{crop_credit.get('id', '')}:{invoice_number}:{phone}",
            )
    except Exception as e:
        logger.error(f"SMS hook on_crop_credit_added failed: {e}")

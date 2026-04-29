"""
SMS trigger hooks — called from invoice creation, payment receipt, etc.
Each function is fire-and-forget (errors logged, never blocks the caller).
Uses _raw_db (unscoped) for lookups to bypass tenant ContextVar mutations.
organization_id is resolved explicitly and passed to queue_sms for proper isolation.
"""
from config import _raw_db as raw_db, logger
from utils import now_iso, new_id


async def _get_cc_phones(org_id: str, branch_id: str, roles: set) -> dict:
    """
    Return phone numbers for each requested role, respecting branch scope.

    Scoping rules:
      owner / admin  → global (one phone configured per org, notified on ALL branches)
      manager        → branch-specific first, falls back to global manager_phone
      auditor        → branch-specific first, falls back to global auditor_phone
                       (an auditor may cover only certain branches, not all)

    Configure via Settings → Messages → Collection Recipients.

    Returns dict of {role: phone} — roles with no phone configured are omitted.
    """
    setting = await raw_db.system_settings.find_one(
        {"key": "collection_notification_recipients", "organization_id": org_id},
        {"_id": 0}
    )
    s = setting or {}
    # Per-branch overrides for manager/auditor
    branch_config = s.get("branch_phones", {}).get(branch_id, {})

    result = {}
    if "owner" in roles and s.get("owner_phone"):
        result["owner"] = s["owner_phone"].strip()
    if "admin" in roles and s.get("admin_phone"):
        result["admin"] = s["admin_phone"].strip()
    if "manager" in roles:
        phone = (branch_config.get("manager_phone") or s.get("manager_phone", "")).strip()
        if phone:
            result["manager"] = phone
    if "auditor" in roles:
        phone = (branch_config.get("auditor_phone") or s.get("auditor_phone", "")).strip()
        if phone:
            result["auditor"] = phone
    return result


async def _resolve_org_id(branch_id: str) -> str:
    """Resolve organization_id from a branch_id. Returns empty string if not found."""
    if not branch_id:
        return ""
    branch = await raw_db.branches.find_one({"id": branch_id}, {"_id": 0, "organization_id": 1})
    return (branch or {}).get("organization_id", "") if branch else ""


async def get_company_name(organization_id: str = "") -> str:
    """Get business name strictly scoped to this organization.

    NEVER falls back to a different tenant's company_info — that caused live cross-org
    bleed (a Sibugay reset → SMS signed as 'JND store'). If the org-scoped lookup
    fails, we additionally try the immutable organizations.name as a safe last resort
    BEFORE giving up. Returns '' on miss — caller must handle the empty case
    (existing code skips the signature when name is empty).
    """
    if not organization_id:
        return ""
    biz = await raw_db.settings.find_one(
        {"key": "company_info", "organization_id": organization_id}, {"_id": 0}
    )
    if biz:
        return biz.get("value", {}).get("name", "")
    # Settings doc missing (e.g. post-reset before re-seed) — read the immutable
    # organizations.name. Same tenant, so no bleed risk.
    org = await raw_db.organizations.find_one(
        {"id": organization_id}, {"_id": 0, "name": 1}
    )
    return (org or {}).get("name", "") if org else ""


async def get_branch_name(branch_id: str) -> str:
    if not branch_id:
        return ""
    branch = await raw_db.branches.find_one({"id": branch_id}, {"_id": 0, "name": 1})
    return branch.get("name", "") if branch else ""


async def on_credit_sale_created(invoice: dict):
    """Called after a credit sale invoice is created with balance > 0."""
    try:
        from routes.sms import queue_sms
        inv_id = invoice.get("id", "")
        customer_id = invoice.get("customer_id", "")
        if not customer_id:
            logger.warning(f"on_credit_sale_created skipped — no customer_id on invoice {inv_id}")
            return
        customer = await raw_db.customers.find_one({"id": customer_id}, {"_id": 0})
        if not customer:
            logger.warning(f"on_credit_sale_created skipped — customer {customer_id} not found (invoice {inv_id})")
            return
        phones = customer.get("phones") or ([customer["phone"]] if customer.get("phone") else [])
        if not phones:
            logger.warning(f"on_credit_sale_created skipped — customer {customer_id} ({customer.get('name','')}) has no phone numbers (invoice {inv_id})")
            return

        branch_id = invoice.get("branch_id", "")
        org_id = await _resolve_org_id(branch_id)
        company_name = await get_company_name(org_id)
        branch_name = await get_branch_name(branch_id)

        # Compute live total balance from invoices — avoids stale customer.balance
        live_res = await raw_db.invoices.aggregate([
            {"$match": {"customer_id": customer_id,
                        "status": {"$nin": ["voided", "paid"]}, "balance": {"$gt": 0}}},
            {"$group": {"_id": None, "total": {"$sum": "$balance"}}}
        ]).to_list(1)
        live_total = round(live_res[0]["total"], 2) if live_res else 0

        variables = {
            "customer_name": customer.get("name", invoice.get("customer_name", "")),
            "amount": f"{invoice.get('balance', 0):,.2f}",
            "company_name": company_name,
            "branch_name": branch_name,
            "date": invoice.get("order_date", ""),
            "due_date": invoice.get("due_date", ""),
            "total_balance": f"{live_total:,.2f}",
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

        # CC: manager — branch-specific; needs awareness of new credit extended on their branch
        cc = await _get_cc_phones(org_id, branch_id, {"manager"})
        manager_phone = cc.get("manager", "")
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

        # CC: manager — branch-specific; closes the loop; confirms charge was applied and customer notified
        cc = await _get_cc_phones(org_id, branch_id, {"manager"})
        manager_phone = cc.get("manager", "")
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

        # CC: notify owner — new season is a significant financial commitment (global — all branches)
        cc = await _get_cc_phones(org_id, branch_id, {"owner"})
        owner_phone = cc.get("owner", "")
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

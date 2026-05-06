"""
Branch Transfer Orders — inter-branch stock movement with automatic price propagation.
Manages the 3-price model: Branch Capital → Transfer Capital → Branch Retail
"""
from fastapi import APIRouter, Depends, HTTPException
from typing import Optional
from datetime import datetime, timezone
from config import db, _raw_db, get_org_context, set_org_context
from utils import (
    get_current_user, check_perm, now_iso, new_id,
    log_movement, get_branch_cost, ensure_org_context,
    mark_price_reviewed, assert_branch_access,
)

router = APIRouter(prefix="/branch-transfers", tags=["Branch Transfers"])


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _notify_admins_pending_approval(transfer: dict, requester: dict):
    """Send SMS to all admins in the org with a link to approve/reject the transfer."""
    org_id = transfer.get("organization_id") or requester.get("organization_id")
    if not org_id:
        return
    # Resolve branch names for the SMS message
    from_branch = await _raw_db.branches.find_one(
        {"id": transfer["from_branch_id"]}, {"_id": 0, "name": 1}
    )
    to_branch = await _raw_db.branches.find_one(
        {"id": transfer["to_branch_id"]}, {"_id": 0, "name": 1}
    )
    from_name = from_branch.get("name", "") if from_branch else ""
    to_name = to_branch.get("name", "") if to_branch else ""

    # Get the public app URL (frontend) to build the approval link.
    # Prefer org-configured value; fall back to env (REACT_APP_FRONTEND_URL or APP_PUBLIC_URL).
    import os
    org = await _raw_db.organizations.find_one({"id": org_id}, {"_id": 0})
    app_url = (
        (org or {}).get("app_url")
        or os.environ.get("REACT_APP_FRONTEND_URL", "")
        or os.environ.get("APP_PUBLIC_URL", "")
    )
    app_url = app_url.rstrip("/")
    approval_link = f"{app_url}/approve-transfer/{transfer['id']}" if app_url else f"/approve-transfer/{transfer['id']}"

    # Find all admin users in the org with phone numbers
    admins = await _raw_db.users.find(
        {"organization_id": org_id, "role": "admin", "active": True},
        {"_id": 0, "id": 1, "full_name": 1, "username": 1, "phone": 1, "phone_number": 1}
    ).to_list(50)

    # Compose the message variables (template body uses < > placeholders)
    items_count = len(transfer.get("items", []))
    cost_total = transfer.get("total_at_transfer_capital", 0)
    company = (org or {}).get("name", "AgriBooks") if org else "AgriBooks"

    from routes.sms import queue_sms
    for admin in admins:
        phone = (admin.get("phone") or admin.get("phone_number") or "").strip()
        if not phone:
            continue
        await queue_sms(
            template_key="transfer_pending_approval",
            customer_id=admin["id"],
            customer_name=admin.get("full_name") or admin.get("username", "Admin"),
            phone=phone,
            variables={
                "admin_name": admin.get("full_name") or admin.get("username", "Admin"),
                "requester_name": requester.get("full_name") or requester.get("username", "Manager"),
                "order_number": transfer.get("order_number", ""),
                "from_branch": from_name,
                "to_branch": to_name,
                "items_count": str(items_count),
                "cost_total": f"{float(cost_total):,.2f}",
                "approval_link": approval_link,
                "company_name": company,
            },
            organization_id=org_id,
            branch_id=transfer.get("from_branch_id", ""),
            branch_name=from_name,
            trigger="auto",
            trigger_ref=transfer["id"],
            dedup_key=f"transfer_pending_approval:{transfer['id']}:{admin['id']}",
        )


async def _get_po_refs(product_id: str, branch_id: str = None):
    """Get moving average and last purchase cost from acquisition history (POs + transfers), branch-specific.
    Excludes reversed movements (e.g. from PO Reopen) — Fix #4."""
    # Use movements collection — includes both 'purchase' and 'transfer_in' with price_at_time
    acq_query = {
        "product_id": product_id,
        "type": {"$in": ["purchase", "transfer_in"]},
        "quantity_change": {"$gt": 0},
        "reversed": {"$ne": True},
    }
    if branch_id:
        acq_query["branch_id"] = branch_id

    last_acq = await db.movements.find_one(acq_query, {"_id": 0}, sort=[("created_at", -1)])
    last_purchase = float(last_acq.get("price_at_time", 0)) if last_acq else 0.0

    all_acqs = await db.movements.find(acq_query, {"_id": 0}).to_list(10000)
    total_qty = sum(m["quantity_change"] for m in all_acqs)
    total_cost = sum(m["quantity_change"] * m.get("price_at_time", 0) for m in all_acqs)
    moving_average = round(total_cost / total_qty, 4) if total_qty > 0 else 0.0

    return round(last_purchase, 2), round(moving_average, 2)


async def _get_price_memory(product_id: str, to_branch_id: str):
    """Get last retail price and transfer capital for this product at the destination branch."""
    mem = await db.branch_transfer_price_memory.find_one(
        {"product_id": product_id, "branch_id": to_branch_id}, {"_id": 0}
    )
    return mem or {}


async def _apply_markup(cost: float, markup: dict) -> float:
    """Apply a single markup rule to a cost price."""
    if not markup:
        return cost
    mk_type = markup.get("type", "fixed")
    mk_val = float(markup.get("value", 0))
    if mk_type == "percent":
        return round(cost * (1 + mk_val / 100), 2)
    return round(cost + mk_val, 2)


# ── Markup template per destination branch ────────────────────────────────────

@router.get("/markup-template/{to_branch_id}")
async def get_markup_template(to_branch_id: str, user=Depends(get_current_user)):
    """Get saved category markup template for a destination branch."""
    template = await db.branch_transfer_templates.find_one(
        {"to_branch_id": to_branch_id}, {"_id": 0}
    )
    if not template:
        # Return defaults: empty markups, min_margin = 20
        return {
            "to_branch_id": to_branch_id,
            "min_margin": 20.0,
            "category_markups": [],
        }
    return template


@router.put("/markup-template/{to_branch_id}")
async def save_markup_template(to_branch_id: str, data: dict, user=Depends(get_current_user)):
    """Save category markup template for a destination branch."""
    if user.get("role") not in ["admin", "manager"]:
        raise HTTPException(status_code=403, detail="Manager or admin required")
    payload = {
        "to_branch_id": to_branch_id,
        "min_margin": float(data.get("min_margin", 20)),
        "category_markups": data.get("category_markups", []),
        "updated_by": user["id"],
        "updated_at": now_iso(),
    }
    await db.branch_transfer_templates.update_one(
        {"to_branch_id": to_branch_id}, {"$set": payload}, upsert=True
    )
    return payload


# ── Product lookup for the transfer form ──────────────────────────────────────

@router.get("/product-lookup")
async def lookup_product_for_transfer(
    q: str = "",
    from_branch_id: str = "",
    to_branch_id: str = "",
    user=Depends(get_current_user),
):
    """
    Search products and return all pricing data needed for a transfer row.
    Optimized: all per-product enrichment calls run in parallel via asyncio.gather().
    Previously ran 50+ sequential DB queries per search; now runs in ~3 parallel rounds.
    """
    import asyncio

    if not q or len(q) < 1:
        return []
    query = {
        "active": True,
        "is_repack": {"$ne": True},
        "$or": [
            {"name": {"$regex": q, "$options": "i"}},
            {"sku": {"$regex": q, "$options": "i"}},
        ]
    }
    products = await db.products.find(query, {"_id": 0}).limit(10).to_list(10)

    async def _val(v):
        """Return a constant value as a coroutine — used in asyncio.gather() for optional calls."""
        return v

    async def enrich_product(p):
        global_cost = float(p.get("cost_price", 0))

        # Run all 5 independent lookups in parallel
        branch_capital_raw, po_refs, memory, target_capital_raw, target_po_refs = await asyncio.gather(
            get_branch_cost(p, from_branch_id) if from_branch_id else _val(global_cost),
            _get_po_refs(p["id"], from_branch_id),
            _get_price_memory(p["id"], to_branch_id) if to_branch_id else _val({}),
            get_branch_cost(p, to_branch_id) if to_branch_id else _val(global_cost),
            _get_po_refs(p["id"], to_branch_id) if to_branch_id else _val((0.0, 0.0)),
        )
        branch_capital = float(branch_capital_raw)
        last_purchase, moving_avg = po_refs
        target_branch_capital = float(target_capital_raw)
        target_last_purchase, target_moving_avg = target_po_refs

        # Target current stock (for "new capital after transfer" weighted average)
        target_branch_stock = 0.0
        target_branch_retail = None
        if to_branch_id:
            target_inv = await db.inventory.find_one(
                {"product_id": p["id"], "branch_id": to_branch_id}, {"_id": 0, "quantity": 1}
            )
            if target_inv:
                target_branch_stock = float(target_inv.get("quantity", 0))
            target_price_doc = await db.branch_prices.find_one(
                {"product_id": p["id"], "branch_id": to_branch_id}, {"_id": 0, "prices": 1}
            )
            if target_price_doc:
                target_branch_retail = target_price_doc.get("prices", {}).get("retail")
        if target_branch_retail is None:
            target_branch_retail = p.get("prices", {}).get("retail", 0) or 0

        result = {
            "id": p["id"],
            "sku": p["sku"],
            "name": p["name"],
            "category": p.get("category", "General"),
            "unit": p.get("unit", ""),
            "branch_capital": branch_capital,
            "global_cost_price": global_cost,
            "is_branch_specific_cost": (branch_capital != global_cost),
            "last_purchase_ref": last_purchase,
            "moving_average_ref": moving_avg,
            "last_branch_retail": memory.get("last_retail_price"),
            "last_transfer_capital": memory.get("last_transfer_capital"),
            # ── Target-branch insights (helps the sender decide pricing) ─────
            "target_branch_capital": target_branch_capital,
            "target_branch_moving_average": target_moving_avg,
            "target_branch_retail": float(target_branch_retail) if target_branch_retail else 0,
            "target_branch_stock": target_branch_stock,
        }

        # Enrich with repack children — fetch all at once then parallel price lookups
        repacks = await db.products.find(
            {"parent_id": p["id"], "is_repack": True, "active": True}, {"_id": 0}
        ).to_list(10)

        async def enrich_repack(rp):
            units_per_parent = float(rp.get("units_per_parent", 1) or 1)
            # FROM-branch capital per repack (what we currently book it at)
            capital_per_repack = round(branch_capital / units_per_parent, 4) if units_per_parent > 0 else 0
            # TARGET-branch capital per repack (what they currently book it at)
            target_capital_per_repack = round(target_branch_capital / units_per_parent, 4) if units_per_parent > 0 else 0
            dest_price_doc = await db.branch_prices.find_one(
                {"product_id": rp["id"], "branch_id": to_branch_id}, {"_id": 0}
            ) if to_branch_id else None
            dest_retail = None
            if dest_price_doc:
                dest_retail = dest_price_doc.get("prices", {}).get("retail")
            if dest_retail is None:
                dest_retail = rp.get("prices", {}).get("retail", 0) or 0
            return {
                "id": rp["id"],
                "name": rp["name"],
                "sku": rp.get("sku", ""),
                "unit": rp.get("unit", ""),
                "units_per_parent": units_per_parent,
                "capital_per_repack": capital_per_repack,
                "current_dest_retail": float(dest_retail),
                # Target-branch insights for repacks
                "target_capital_per_repack": target_capital_per_repack,
            }

        result["repacks"] = await asyncio.gather(*[enrich_repack(rp) for rp in repacks])
        return result

    # Run ALL product enrichments in parallel — the key optimization
    results = await asyncio.gather(*[enrich_product(p) for p in products])
    return list(results)


# ── Transfer order CRUD ───────────────────────────────────────────────────────

@router.get("")
async def list_transfers(
    user=Depends(get_current_user),
    status: Optional[str] = None,
    from_branch_id: Optional[str] = None,
    to_branch_id: Optional[str] = None,
    branch_id: Optional[str] = None,   # convenience: filter for either side
    skip: int = 0,
    limit: int = 40,
):
    """List branch transfer orders.
    Non-admins automatically see only orders relevant to their branch (from OR to).
    Admins can pass branch_id to filter, or omit it to see all.
    """
    q = {}
    if status:
        q["status"] = status
    if from_branch_id:
        q["from_branch_id"] = from_branch_id
    if to_branch_id:
        q["to_branch_id"] = to_branch_id

    # Branch isolation: non-admins only see their own branch's orders
    user_branch = user.get("branch_id")
    is_admin = user.get("role") == "admin"

    if branch_id:
        # Explicit branch filter (admin scoping to a specific branch)
        q["$or"] = [{"from_branch_id": branch_id}, {"to_branch_id": branch_id}]
    elif not is_admin and user_branch:
        # Non-admin: restrict to orders involving their branch
        q["$or"] = [{"from_branch_id": user_branch}, {"to_branch_id": user_branch}]

    total = await db.branch_transfer_orders.count_documents(q)
    orders = await db.branch_transfer_orders.find(
        q, {"_id": 0}
    ).sort("created_at", -1).skip(skip).limit(limit).to_list(limit)
    return {"orders": orders, "total": total}


@router.put("/{transfer_id}")
async def update_transfer(transfer_id: str, data: dict, user=Depends(get_current_user)):
    """Edit a draft transfer order. Only source branch (or admin) can edit. Order must be draft."""
    if user.get("role") not in ["admin", "manager"]:
        raise HTTPException(status_code=403, detail="Manager or admin required")

    order = await db.branch_transfer_orders.find_one({"id": transfer_id}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Transfer not found")
    if order["status"] not in ("draft", "pending_approval", "returned"):
        raise HTTPException(status_code=400, detail="Only draft / pending / returned orders can be edited")

    # Non-admin: must be the source branch
    user_branch = user.get("branch_id")
    if user.get("role") != "admin" and user_branch and user_branch != order["from_branch_id"]:
        raise HTTPException(status_code=403, detail="Only the source branch can edit this transfer")

    items = data.get("items", order["items"])

    total_at_branch_capital = round(sum(
        float(i.get("branch_capital", 0)) * float(i.get("qty", 0)) for i in items), 2)
    total_at_transfer_capital = round(sum(
        float(i.get("transfer_capital", 0)) * float(i.get("qty", 0)) for i in items), 2)
    total_at_branch_retail = round(sum(
        float(i.get("branch_retail", 0)) * float(i.get("qty", 0)) for i in items), 2)

    update = {
        "items": items,
        "min_margin": float(data.get("min_margin", order.get("min_margin", 20))),
        "category_markups": data.get("category_markups", order.get("category_markups", [])),
        "notes": data.get("notes", order.get("notes", "")),
        "total_at_branch_capital": total_at_branch_capital,
        "total_at_transfer_capital": total_at_transfer_capital,
        "total_at_branch_retail": total_at_branch_retail,
        # Preserve / update repack price changes
        "repack_price_updates": data.get("repack_price_updates", order.get("repack_price_updates", [])),
        "updated_at": now_iso(),
        "updated_by": user.get("full_name", user["username"]),
    }
    await db.branch_transfer_orders.update_one({"id": transfer_id}, {"$set": update})
    updated = await db.branch_transfer_orders.find_one({"id": transfer_id}, {"_id": 0})
    return updated


@router.post("")
async def create_transfer(data: dict, user=Depends(get_current_user)):
    """Create a new branch transfer order (saved as draft)."""
    if user.get("role") not in ["admin", "manager"]:
        raise HTTPException(status_code=403, detail="Manager or admin required")

    from_branch_id = data["from_branch_id"]
    to_branch_id = data["to_branch_id"]
    # Both ends must be branches the manager is assigned to. Admins skip
    # this check (they can move stock anywhere within their org).
    assert_branch_access(user, from_branch_id)
    assert_branch_access(user, to_branch_id)
    items = data.get("items", [])
    if not items:
        raise HTTPException(status_code=400, detail="No items in transfer")

    # Ensure org context for super admin
    if not get_org_context():
        await ensure_org_context(branch_id=from_branch_id)

    count = await db.branch_transfer_orders.count_documents({})
    order_number = f"BTO-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{str(count + 1).zfill(4)}"

    # Compute totals
    total_at_branch_capital = round(sum(
        float(i.get("branch_capital", 0)) * float(i.get("qty", 0)) for i in items), 2)
    total_at_transfer_capital = round(sum(
        float(i.get("transfer_capital", 0)) * float(i.get("qty", 0)) for i in items), 2)
    total_at_branch_retail = round(sum(
        float(i.get("branch_retail", 0)) * float(i.get("qty", 0)) for i in items), 2)

    transfer = {
        "id": new_id(),
        "order_number": order_number,
        "from_branch_id": from_branch_id,
        "to_branch_id": to_branch_id,
        "status": "pending_approval" if data.get("requires_approval") else "draft",
        "min_margin": float(data.get("min_margin", 20)),
        "category_markups": data.get("category_markups", []),
        "items": items,
        "notes": data.get("notes", ""),
        "total_at_branch_capital": total_at_branch_capital,
        "total_at_transfer_capital": total_at_transfer_capital,
        "total_at_branch_retail": total_at_branch_retail,
        # Repack price updates: applied to destination branch on receive
        "repack_price_updates": data.get("repack_price_updates", []),
        # Link to originating stock request (if any)
        "request_po_id": data.get("request_po_id", ""),
        "request_po_number": data.get("request_po_number", ""),
        "created_by": user["id"],
        "created_by_name": user.get("full_name", user["username"]),
        "created_at": now_iso(),
        "sent_at": None,
        "received_at": None,
        "received_by": None,
    }

    await db.branch_transfer_orders.insert_one(transfer)
    del transfer["_id"]

    # Auto-create internal invoice for this transfer
    from routes.internal_invoices import create_internal_invoice
    try:
        invoice = await create_internal_invoice(transfer, user)
        transfer["invoice_id"] = invoice["id"]
        transfer["invoice_number"] = invoice["invoice_number"]
    except Exception:
        pass  # Invoice creation failure should not block transfer creation

    # If this draft was Submitted for Approval, notify all admins via SMS
    if transfer["status"] == "pending_approval":
        try:
            await _notify_admins_pending_approval(transfer, user)
        except Exception as notify_err:
            # Non-critical — admins can still see it in Pending Approval tab
            import logging
            logging.getLogger("branch_transfers").warning(
                f"pending-approval SMS failed for {transfer['order_number']}: {notify_err}"
            )

    return transfer


# ── Pending-Approval Workflow (Manager submits, Admin approves) ───────────────

@router.post("/{transfer_id}/approve")
async def approve_pending_transfer(transfer_id: str, data: dict, user=Depends(get_current_user)):
    """
    Approve a pending-approval transfer. Body:
      - items: [{product_id, branch_retail}]  — admin's retail per row.
                `branch_retail=0` or omitted → inherit CURRENT target-branch
                retail (falls back to source-branch retail, then product list
                price). This lets admins approve a transfer without setting a
                fresh retail for every line.
      - notes: optional approval note (audit trail)
      - pin: manager/admin PIN, verified via `transfer_approve` policy
             (defaults admin_pin + manager_pin + totp; owner can tighten in
             Settings → PIN Policy). Manager PIN only passes if that manager
             has `branch_transfers.approve=True` in their permissions.

    Permission: `branch_transfers.approve` (admin has it by default; admin
    can grant to a specific manager via User Permissions).

    On approval: retail merged + recomputed, status → 'sent' (auto-dispatch),
    destination branch notified, manager SMS'd.
    """
    from routes.verify import verify_pin_for_action

    # 1. Permission gate (replaces hardcoded admin check)
    from utils import check_perm as _check_perm
    _check_perm(user, "branch_transfers", "approve")

    # 2. PIN gate
    pin = str(data.get("pin") or "").strip()
    if not pin:
        raise HTTPException(status_code=400, detail="PIN required to approve transfer")
    verifier = await verify_pin_for_action(pin, "transfer_approve")
    if not verifier:
        raise HTTPException(status_code=403, detail="Invalid PIN or unauthorized for transfer approval")

    # Resolve the verifier to a real user (so we can check their permissions).
    # The `verifier_id="system_admin"` case means the system admin_pin (hashed
    # in system_settings) matched — implicitly grants approval authority.
    verifier_id = verifier.get("verifier_id")
    verifier_user = None
    if verifier_id and verifier_id != "system_admin":
        verifier_user = await _raw_db.users.find_one({"id": verifier_id}, {"_id": 0})

    # If the PIN belongs to a real user AND that user is not an admin, they
    # must ALSO have the transfers.approve permission themselves — prevents a
    # manager PIN from bypassing the grant.
    if verifier_user and verifier_user.get("role") != "admin":
        from utils import has_perm as _has_perm
        if not _has_perm(verifier_user, "branch_transfers", "approve"):
            raise HTTPException(
                status_code=403,
                detail="This manager is not an authorized approver. Ask the admin to enable 'Approve Pending Transfer' in User Permissions."
            )

    order = await db.branch_transfer_orders.find_one({"id": transfer_id}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Transfer not found")
    if order.get("status") != "pending_approval":
        raise HTTPException(status_code=400, detail=f"Transfer is not awaiting approval (status: {order.get('status')})")

    # Ensure org context
    if not get_org_context():
        await ensure_org_context(branch_id=order["from_branch_id"], org_id=order.get("organization_id"))

    # 3. Merge admin-set retail with smart fallback:
    #    blank/0 → target branch's current retail → source branch's retail →
    #    product list price → keep whatever the draft already had.
    admin_items = {it.get("product_id"): it for it in (data.get("items") or []) if it.get("product_id")}
    to_branch = order["to_branch_id"]
    from_branch = order["from_branch_id"]

    async def _resolve_retail(product_id: str, draft_retail: float) -> float:
        """Return a sensible retail for this product+target-branch when admin blanked it.
        Fallback chain: target-branch price → source-branch price → product list price.
        """
        if not product_id:
            return float(draft_retail or 0)
        # 1. Target branch price
        target_doc = await _raw_db.branch_prices.find_one(
            {"product_id": product_id, "branch_id": to_branch},
            {"_id": 0, "prices": 1},
        )
        target_retail = (target_doc or {}).get("prices", {}).get("retail")
        if target_retail and float(target_retail) > 0:
            return float(target_retail)
        # 2. Source branch price
        source_doc = await _raw_db.branch_prices.find_one(
            {"product_id": product_id, "branch_id": from_branch},
            {"_id": 0, "prices": 1},
        )
        source_retail = (source_doc or {}).get("prices", {}).get("retail")
        if source_retail and float(source_retail) > 0:
            return float(source_retail)
        # 3. Product list price (global default)
        prod = await _raw_db.products.find_one(
            {"id": product_id}, {"_id": 0, "prices": 1, "price": 1}
        )
        list_price = ((prod or {}).get("prices", {}).get("retail")
                      or (prod or {}).get("price")
                      or 0)
        return float(list_price or draft_retail or 0)

    new_items = []
    for it in order.get("items", []):
        merged = dict(it)
        admin_row = admin_items.get(it.get("product_id"))
        admin_retail = None
        if admin_row is not None and admin_row.get("branch_retail") not in (None, ""):
            admin_retail = float(admin_row["branch_retail"])
        if admin_retail is not None and admin_retail > 0:
            merged["branch_retail"] = admin_retail
        else:
            # Admin left it blank → smart fallback
            merged["branch_retail"] = await _resolve_retail(
                it.get("product_id"),
                float(it.get("branch_retail") or 0),
            )
            merged["retail_source"] = "inherited_target"
        new_items.append(merged)

    # Recompute retail total
    total_at_branch_retail = round(sum(
        float(i.get("branch_retail", 0)) * float(i.get("qty", 0)) for i in new_items), 2)

    approval_meta = {
        "approved_by": (verifier_user or {}).get("id") or user["id"],
        "approved_by_name": (verifier_user or {}).get("full_name") or (verifier_user or {}).get("username")
                             or verifier.get("verifier_name") or user.get("full_name", user["username"]),
        "approved_by_role": (verifier_user or {}).get("role", user.get("role", "")),
        "approved_pin_method": verifier.get("method", ""),
        "approved_at": now_iso(),
        "approval_note": data.get("notes", ""),
    }

    await db.branch_transfer_orders.update_one(
        {"id": transfer_id},
        {"$set": {
            **approval_meta,
            "items": new_items,
            "total_at_branch_retail": total_at_branch_retail,
            "status": "sent",
            "sent_at": now_iso(),
            "sent_by": (verifier_user or {}).get("id") or user["id"],
        }}
    )

    # Re-use the same destination notification + invoice update + doc-code path as /send
    from_branch_doc = await db.branches.find_one({"id": order["from_branch_id"]}, {"_id": 0, "name": 1})
    to_branch_doc = await db.branches.find_one({"id": order["to_branch_id"]}, {"_id": 0, "name": 1})
    from_name = from_branch_doc.get("name", order["from_branch_id"]) if from_branch_doc else order["from_branch_id"]
    to_name = to_branch_doc.get("name", order["to_branch_id"]) if to_branch_doc else order["to_branch_id"]

    dest_users = await db.users.find(
        {"branch_id": order["to_branch_id"], "active": True}, {"_id": 0, "id": 1}
    ).to_list(50)
    admins = await db.users.find(
        {"role": "admin", "active": True}, {"_id": 0, "id": 1}
    ).to_list(50)
    target_ids = list({u["id"] for u in dest_users + admins})

    await db.notifications.insert_one({
        "id": new_id(),
        "type": "transfer_incoming",
        "title": "Incoming Stock Transfer (Approved)",
        "message": f"Transfer {order['order_number']} from {from_name} approved by {approval_meta['approved_by_name']} — on the way",
        "branch_id": order["to_branch_id"],
        "branch_name": to_name,
        "metadata": {
            "transfer_id": transfer_id,
            "order_number": order["order_number"],
            "from_branch": from_name,
            "to_branch": to_name,
            "approved_by": approval_meta["approved_by_name"],
        },
        "target_user_ids": target_ids,
        "read_by": [],
        "created_at": now_iso(),
    })

    # SMS the manager that submitted it
    try:
        requester = await _raw_db.users.find_one(
            {"id": order.get("created_by")},
            {"_id": 0, "phone": 1, "phone_number": 1, "full_name": 1, "username": 1, "id": 1}
        )
        phone = (requester or {}).get("phone") or (requester or {}).get("phone_number") or ""
        if phone:
            from routes.sms import queue_sms
            await queue_sms(
                template_key="transfer_approved",
                customer_id=requester.get("id", ""),
                customer_name=requester.get("full_name") or requester.get("username", "Manager"),
                phone=phone.strip(),
                variables={
                    "manager_name": requester.get("full_name") or requester.get("username", "Manager"),
                    "approver_name": approval_meta["approved_by_name"],
                    "order_number": order.get("order_number", ""),
                    "from_branch": from_name,
                    "to_branch": to_name,
                },
                organization_id=order.get("organization_id", ""),
                branch_id=order.get("from_branch_id", ""),
                branch_name=from_name,
                trigger="auto",
                trigger_ref=transfer_id,
                dedup_key=f"transfer_approved:{transfer_id}",
            )
    except Exception:
        pass  # Non-critical

    # Update internal invoice status
    from routes.internal_invoices import update_invoice_status
    try:
        await update_invoice_status(transfer_id, "sent")
    except Exception:
        pass

    # Auto-generate doc code so QR is ready on the printed transfer slip
    from routes.doc_lookup import auto_generate_doc_code
    try:
        await auto_generate_doc_code(
            "branch_transfer", transfer_id,
            org_id=order.get("organization_id", ""),
            created_by=user.get("id", ""),
        )
    except Exception:
        pass

    return {"message": "Transfer approved and dispatched", "status": "sent"}


@router.post("/{transfer_id}/reject")
async def reject_pending_transfer(transfer_id: str, data: dict, user=Depends(get_current_user)):
    """Reject a pending-approval transfer with a reason. Requires same
    permission as approve (`branch_transfers.approve`) — rejection is an
    authoritative action. PIN is NOT required for reject: it's a return, not
    a dispatch. Manager can edit + resubmit after the bounce-back.
    """
    from utils import check_perm as _check_perm
    _check_perm(user, "branch_transfers", "approve")

    reason = (data.get("reason") or "").strip()
    if len(reason) < 4:
        raise HTTPException(status_code=400, detail="Rejection reason is required (≥ 4 chars)")

    order = await db.branch_transfer_orders.find_one({"id": transfer_id}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Transfer not found")
    if order.get("status") != "pending_approval":
        raise HTTPException(status_code=400, detail=f"Transfer is not awaiting approval (status: {order.get('status')})")

    if not get_org_context():
        await ensure_org_context(branch_id=order["from_branch_id"], org_id=order.get("organization_id"))

    await db.branch_transfer_orders.update_one(
        {"id": transfer_id},
        {"$set": {
            "status": "returned",
            "rejected_by": user["id"],
            "rejected_by_name": user.get("full_name", user["username"]),
            "rejected_at": now_iso(),
            "rejection_reason": reason,
        }}
    )

    # SMS the manager who created it
    try:
        from_branch = await _raw_db.branches.find_one({"id": order["from_branch_id"]}, {"_id": 0, "name": 1})
        from_name = (from_branch or {}).get("name", "")
        requester = await _raw_db.users.find_one(
            {"id": order.get("created_by")},
            {"_id": 0, "phone": 1, "phone_number": 1, "full_name": 1, "username": 1, "id": 1}
        )
        phone = (requester or {}).get("phone") or (requester or {}).get("phone_number") or ""
        if phone:
            from routes.sms import queue_sms
            await queue_sms(
                template_key="transfer_rejected",
                customer_id=requester.get("id", ""),
                customer_name=requester.get("full_name") or requester.get("username", "Manager"),
                phone=phone.strip(),
                variables={
                    "manager_name": requester.get("full_name") or requester.get("username", "Manager"),
                    "rejecter_name": user.get("full_name", user.get("username", "Admin")),
                    "order_number": order.get("order_number", ""),
                    "from_branch": from_name,
                    "reason": reason,
                },
                organization_id=order.get("organization_id", ""),
                branch_id=order.get("from_branch_id", ""),
                branch_name=from_name,
                trigger="auto",
                trigger_ref=transfer_id,
                dedup_key=f"transfer_rejected:{transfer_id}",
            )
    except Exception:
        pass

    return {"message": "Transfer rejected — manager has been notified", "status": "returned"}


@router.post("/{transfer_id}/resubmit")
async def resubmit_returned_transfer(transfer_id: str, user=Depends(get_current_user)):
    """Manager resubmits a 'returned' transfer for approval after fixing it."""
    if user.get("role") not in ("admin", "manager"):
        raise HTTPException(status_code=403, detail="Manager or admin required")
    order = await db.branch_transfer_orders.find_one({"id": transfer_id}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Transfer not found")
    if order.get("status") != "returned":
        raise HTTPException(status_code=400, detail=f"Only returned transfers can be resubmitted (status: {order.get('status')})")
    # Source-branch / admin gate
    user_branch = user.get("branch_id")
    if user.get("role") != "admin" and user_branch and user_branch != order["from_branch_id"]:
        raise HTTPException(status_code=403, detail="Only the source branch can resubmit this transfer")

    if not get_org_context():
        await ensure_org_context(branch_id=order["from_branch_id"], org_id=order.get("organization_id"))

    await db.branch_transfer_orders.update_one(
        {"id": transfer_id},
        {"$set": {
            "status": "pending_approval",
            "resubmitted_by": user["id"],
            "resubmitted_by_name": user.get("full_name", user["username"]),
            "resubmitted_at": now_iso(),
        },
         "$unset": {"rejected_by": "", "rejected_by_name": "", "rejected_at": "", "rejection_reason": ""}}
    )
    refreshed = await db.branch_transfer_orders.find_one({"id": transfer_id}, {"_id": 0})
    try:
        await _notify_admins_pending_approval(refreshed, user)
    except Exception:
        pass
    return {"message": "Transfer resubmitted for approval", "status": "pending_approval"}


@router.get("/{transfer_id}/approval-insights")
async def get_approval_insights(transfer_id: str, user=Depends(get_current_user)):
    """
    Returns per-item insights the approver needs on the approval page:
      - current_target_retail: the retail price currently set in the target
        branch (what the blank-retail fallback will inherit)
      - current_source_retail: the source branch's current retail (reference)
      - target_moving_capital: moving-avg capital in the target branch
      - target_last_purchase_cost: most recent purchase/transfer-in cost at
        the target branch

    Keeps UI reads fast: one call per approval page, instead of N product
    fetches from the frontend.
    """
    from utils import check_perm as _check_perm
    _check_perm(user, "branch_transfers", "approve")

    order = await db.branch_transfer_orders.find_one({"id": transfer_id}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Transfer not found")

    to_branch = order["to_branch_id"]
    from_branch = order["from_branch_id"]
    insights = {}
    for it in (order.get("items") or []):
        pid = it.get("product_id")
        if not pid:
            continue
        # Retail prices — target and source
        target_price = await _raw_db.branch_prices.find_one(
            {"product_id": pid, "branch_id": to_branch}, {"_id": 0, "prices": 1}
        )
        source_price = await _raw_db.branch_prices.find_one(
            {"product_id": pid, "branch_id": from_branch}, {"_id": 0, "prices": 1}
        )
        prod = await _raw_db.products.find_one(
            {"id": pid}, {"_id": 0, "prices": 1, "price": 1}
        )
        current_target_retail = (
            (target_price or {}).get("prices", {}).get("retail")
            or (prod or {}).get("prices", {}).get("retail")
            or (prod or {}).get("price") or 0
        )
        current_source_retail = (
            (source_price or {}).get("prices", {}).get("retail")
            or (prod or {}).get("prices", {}).get("retail")
            or (prod or {}).get("price") or 0
        )
        # Capital — moving avg + last purchase at target branch
        inv = await _raw_db.inventory.find_one(
            {"product_id": pid, "branch_id": to_branch},
            {"_id": 0, "moving_avg_cost": 1, "last_purchase_cost": 1, "quantity": 1},
        )
        insights[pid] = {
            "current_target_retail": float(current_target_retail or 0),
            "current_source_retail": float(current_source_retail or 0),
            "target_moving_capital": float((inv or {}).get("moving_avg_cost") or 0),
            "target_last_purchase_cost": float((inv or {}).get("last_purchase_cost") or 0),
            "target_stock": float((inv or {}).get("quantity") or 0),
        }
    return {"insights": insights}


@router.get("/{transfer_id}")
async def get_transfer(transfer_id: str, user=Depends(get_current_user)):
    """Get a single branch transfer order with resolved branch names."""
    order = await db.branch_transfer_orders.find_one({"id": transfer_id}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Transfer not found")
    # Resolve branch names if missing
    if not order.get("from_branch_name"):
        from_branch = await db.branches.find_one({"id": order.get("from_branch_id")}, {"_id": 0, "name": 1})
        if from_branch:
            order["from_branch_name"] = from_branch["name"]
    if not order.get("to_branch_name"):
        to_branch = await db.branches.find_one({"id": order.get("to_branch_id")}, {"_id": 0, "name": 1})
        if to_branch:
            order["to_branch_name"] = to_branch["name"]
    return order


@router.post("/{transfer_id}/send")
async def send_transfer(transfer_id: str, user=Depends(get_current_user)):
    """Mark transfer as sent (goods are on the way). Creates incoming notification for destination."""
    order = await db.branch_transfer_orders.find_one({"id": transfer_id}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Transfer not found")
    if order["status"] != "draft":
        raise HTTPException(status_code=400, detail="Only draft orders can be sent")

    # Ensure org context for super admin
    if not get_org_context():
        org_id = order.get("organization_id")
        await ensure_org_context(branch_id=order["from_branch_id"], org_id=org_id)

    await db.branch_transfer_orders.update_one(
        {"id": transfer_id},
        {"$set": {"status": "sent", "sent_at": now_iso(), "sent_by": user["id"]}}
    )

    # Notify destination branch users + admins
    from_branch = await db.branches.find_one({"id": order["from_branch_id"]}, {"_id": 0, "name": 1})
    to_branch = await db.branches.find_one({"id": order["to_branch_id"]}, {"_id": 0, "name": 1})
    from_name = from_branch.get("name", order["from_branch_id"]) if from_branch else order["from_branch_id"]
    to_name = to_branch.get("name", order["to_branch_id"]) if to_branch else order["to_branch_id"]

    dest_users = await db.users.find(
        {"branch_id": order["to_branch_id"], "active": True}, {"_id": 0, "id": 1}
    ).to_list(50)
    admins = await db.users.find(
        {"role": "admin", "active": True}, {"_id": 0, "id": 1}
    ).to_list(50)
    target_ids = list({u["id"] for u in dest_users + admins})

    await db.notifications.insert_one({
        "id": new_id(),
        "type": "transfer_incoming",
        "title": "Incoming Stock Transfer",
        "message": f"Transfer {order['order_number']} from {from_name} is on the way — {len(order.get('items', []))} product(s)",
        "branch_id": order["to_branch_id"],
        "branch_name": to_name,
        "metadata": {
            "transfer_id": transfer_id,
            "order_number": order["order_number"],
            "from_branch": from_name,
            "to_branch": to_name,
        },
        "target_user_ids": target_ids,
        "read_by": [],
        "created_at": now_iso(),
    })

    # Update internal invoice status
    from routes.internal_invoices import update_invoice_status
    try:
        await update_invoice_status(transfer_id, "sent")
    except Exception:
        pass

    # Auto-generate doc code so QR is ready on the printed transfer slip
    from routes.doc_lookup import auto_generate_doc_code
    try:
        await auto_generate_doc_code(
            "branch_transfer", transfer_id,
            org_id=order.get("org_id", ""),
            created_by=user.get("id", ""),
        )
    except Exception:
        pass  # Non-critical — print still works without QR

    return {"message": "Transfer sent", "status": "sent"}


# ── Terminal Integration ──────────────────────────────────────────────────────

@router.post("/{transfer_id}/send-to-terminal")
async def send_transfer_to_terminal(transfer_id: str, user=Depends(get_current_user)):
    """
    Mark a sent transfer for terminal checking. Locks it on PC.
    Terminal will verify quantities and submit receipt.
    """
    if user.get("role") not in ["admin", "manager"]:
        raise HTTPException(status_code=403, detail="Manager or admin required")

    order = await db.branch_transfer_orders.find_one({"id": transfer_id}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Transfer not found")
    if order["status"] != "sent":
        raise HTTPException(status_code=400, detail=f"Only 'sent' transfers can be sent to terminal (current: {order['status']})")

    await db.branch_transfer_orders.update_one(
        {"id": transfer_id},
        {"$set": {
            "status": "sent_to_terminal",
            "sent_to_terminal_at": now_iso(),
            "sent_to_terminal_by": user.get("full_name", user.get("username", "")),
        }}
    )

    # Notify terminals for the destination branch via WebSocket
    to_branch_id = order.get("to_branch_id")
    from_branch = await db.branches.find_one({"id": order["from_branch_id"]}, {"_id": 0, "name": 1})
    from_name = from_branch.get("name", "") if from_branch else ""

    if to_branch_id:
        try:
            from routes.terminal_ws import terminal_ws_manager
            from config import _raw_db
            terminals = await _raw_db.terminal_sessions.find(
                {"branch_id": to_branch_id, "status": "active"}, {"_id": 0, "terminal_id": 1}
            ).to_list(20)
            for t in terminals:
                await terminal_ws_manager.notify_terminal(t["terminal_id"], "transfer_assigned", {
                    "transfer_id": transfer_id,
                    "order_number": order.get("order_number", ""),
                    "from_branch": from_name,
                    "item_count": len(order.get("items", [])),
                })
        except Exception:
            pass

    return {"message": f"Transfer {order.get('order_number', '')} sent to terminal for checking"}


@router.post("/{transfer_id}/terminal-receive")
async def terminal_receive_transfer(transfer_id: str, data: dict, user=Depends(get_current_user)):
    """
    Terminal submits received quantities. Uses the same receive logic but:
    - Skips receipt upload requirement (terminal can't upload photos yet)
    - Records terminal_id for audit trail
    - If all quantities match: immediate inventory update (received)
    - If variance: status becomes received_pending, source branch notified
    """
    order = await db.branch_transfer_orders.find_one({"id": transfer_id}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Transfer not found")
    if order["status"] not in ["sent", "sent_to_terminal"]:
        raise HTTPException(status_code=400, detail="Transfer is not in a receivable state")

    terminal_id = data.get("terminal_id", "")
    items = data.get("items", [])
    notes = data.get("notes", "")

    # Add terminal metadata
    await db.branch_transfer_orders.update_one(
        {"id": transfer_id},
        {"$set": {
            "terminal_id": terminal_id,
            "terminal_receive_started_at": now_iso(),
        }}
    )

    # Delegate to the existing receive logic with skip_receipt_check
    receive_data = {
        "items": items,
        "notes": notes,
        "skip_receipt_check": True,
    }

    return await receive_transfer(transfer_id, receive_data, user)


@router.get("/{transfer_id}/capital-preview")
async def get_transfer_capital_preview(transfer_id: str, user=Depends(get_current_user)):
    """
    Preview the capital impact of receiving this branch transfer at the destination.
    Returns per-item: current_dest_capital, transfer_capital, moving_avg, needs_warning.
    needs_warning=True when transfer_capital < current destination branch capital.
    """
    order = await db.branch_transfer_orders.find_one({"id": transfer_id}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Transfer not found")

    to_branch_id = order.get("to_branch_id", "")
    items_preview = []

    for item in order.get("items", []):
        pid = item["product_id"]
        transfer_capital = float(item.get("transfer_capital") or item.get("branch_capital") or 0)

        # Current capital at destination (branch-specific or global fallback)
        bp = await db.branch_prices.find_one(
            {"product_id": pid, "branch_id": to_branch_id}, {"_id": 0}
        )
        if bp and bp.get("cost_price") is not None:
            current_dest_capital = float(bp["cost_price"])
        else:
            product = await db.products.find_one({"id": pid}, {"_id": 0})
            current_dest_capital = float(product.get("cost_price", 0)) if product else 0

        # Moving average from acquisition history (POs + transfers) at destination branch
        _, moving_avg = await _get_po_refs(pid, to_branch_id)
        if moving_avg == 0:
            moving_avg = current_dest_capital

        needs_warning = transfer_capital < current_dest_capital and transfer_capital > 0 and current_dest_capital > 0
        price_drop_pct = round((current_dest_capital - transfer_capital) / current_dest_capital * 100, 1) if needs_warning else 0

        items_preview.append({
            "product_id": pid,
            "product_name": item.get("product_name", ""),
            "sku": item.get("sku", ""),
            "qty": float(item.get("qty", 0)),
            "unit": item.get("unit", ""),
            "transfer_capital": transfer_capital,
            "current_dest_capital": current_dest_capital,
            "moving_avg": moving_avg,
            "needs_warning": needs_warning,
            "price_drop_pct": price_drop_pct,
        })

    has_warnings = any(i["needs_warning"] for i in items_preview)
    to_branch = await db.branches.find_one({"id": to_branch_id}, {"_id": 0, "name": 1})
    return {
        "order_number": order.get("order_number", ""),
        "to_branch_name": to_branch.get("name", "") if to_branch else "",
        "has_warnings": has_warnings,
        "items": items_preview,
    }


@router.post("/{transfer_id}/receive")
async def receive_transfer(transfer_id: str, data: dict, user=Depends(get_current_user)):
    """
    Submit received quantities for a branch transfer.
    - If ALL quantities match ordered: update inventory immediately → status 'received'
    - If ANY variance (shortage/excess): save pending receipt WITHOUT updating inventory
      → status 'received_pending', notify source to confirm or dispute
    """
    order = await db.branch_transfer_orders.find_one({"id": transfer_id}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Transfer not found")
    if order["status"] == "sent_to_terminal" and not data.get("skip_receipt_check"):
        raise HTTPException(
            status_code=423,
            detail="Transfer is locked — currently being checked on a terminal. Finalize it on the terminal first."
        )
    if order["status"] not in ["sent", "draft", "sent_to_terminal"]:
        raise HTTPException(status_code=400, detail="Transfer is not in a receivable state")

    # ── Branch guard: only the destination branch can receive ─────────────
    user_branch = user.get("branch_id", "")
    if user.get("role") != "admin" and user_branch and user_branch != order.get("to_branch_id", ""):
        raise HTTPException(status_code=403, detail="Only the destination branch can receive this transfer")

    # Ensure org context is set for super admins acting on tenant data
    if not get_org_context():
        org_id = order.get("organization_id")
        await ensure_org_context(branch_id=order.get("to_branch_id"), org_id=org_id)

    # ── Mandatory receipt check for final receiving ──────────────────────
    upload_session_ids = data.get("upload_session_ids", [])
    if not data.get("skip_receipt_check"):
        # Check existing uploads + new inline uploads
        existing_sessions = await db.upload_sessions.find(
            {"record_type": "branch_transfer", "record_id": transfer_id},
            {"_id": 0, "file_count": 1}
        ).to_list(20)
        existing_count = sum(s.get("file_count", 0) for s in existing_sessions)
        # Count files from inline upload sessions being submitted now
        inline_count = 0
        for sid in upload_session_ids:
            s = await db.upload_sessions.find_one({"id": sid}, {"_id": 0, "file_count": 1})
            if s:
                inline_count += s.get("file_count", 0)
        total_receipts = existing_count + inline_count
        if total_receipts == 0:
            raise HTTPException(
                status_code=400,
                detail="Receipt upload required. Please upload at least 1 receipt/DR photo before confirming receipt."
            )

    # ── Link pending upload sessions (supports R2 + legacy local) ────────
    if upload_session_ids:
        from pathlib import Path
        for sid in upload_session_ids:
            session = await db.upload_sessions.find_one({"id": sid}, {"_id": 0})
            if not session:
                continue
            old_record_id = session.get("record_id", "")
            org_id = session.get("org_id", "default")
            updated_files = []
            for f in session.get("files", []):
                r2_key = f.get("r2_key", "")
                if r2_key and old_record_id != transfer_id:
                    # R2 file — copy to new key, delete old
                    try:
                        from utils.r2_storage import _get_client, _bucket, build_key
                        client = _get_client()
                        ext = Path(r2_key).suffix
                        new_key = build_key(org_id, "branch_transfer", transfer_id, f"{f['id']}{ext}")
                        client.copy_object(Bucket=_bucket, CopySource={"Bucket": _bucket, "Key": r2_key}, Key=new_key)
                        client.delete_object(Bucket=_bucket, Key=r2_key)
                        f["r2_key"] = new_key
                    except Exception:
                        pass
                elif f.get("stored_path") and old_record_id != transfer_id:
                    # Legacy local file
                    upload_dir = Path("/app/uploads")
                    old_path = Path(f["stored_path"])
                    if old_path.exists():
                        new_dir = upload_dir / "branch_transfer" / transfer_id
                        new_dir.mkdir(parents=True, exist_ok=True)
                        new_path = new_dir / old_path.name
                        old_path.rename(new_path)
                        f["stored_path"] = str(new_path)
                updated_files.append(f)
            await db.upload_sessions.update_one(
                {"id": sid},
                {"$set": {
                    "record_type": "branch_transfer",
                    "record_id": transfer_id,
                    "is_pending": False,
                    "reassigned_at": now_iso(),
                    "files": updated_files,
                }}
            )

    from_branch_id = order["from_branch_id"]
    to_branch_id = order["to_branch_id"]

    from_branch = await db.branches.find_one({"id": from_branch_id}, {"_id": 0, "name": 1})
    to_branch = await db.branches.find_one({"id": to_branch_id}, {"_id": 0, "name": 1})
    from_name = from_branch.get("name", from_branch_id) if from_branch else from_branch_id
    to_name = to_branch.get("name", to_branch_id) if to_branch else to_branch_id

    qty_overrides = {}
    for _item in data.get("items", []):
        _qty_recv = _item.get("qty_received")
        qty_overrides[_item["product_id"]] = float(
            _qty_recv if _qty_recv is not None else _item.get("qty", 0)
        )

    pending_items = []
    shortages = []
    excesses = []

    for item in order["items"]:
        product_id = item["product_id"]
        qty_ordered = float(item["qty"])
        qty_received = qty_overrides.get(product_id, qty_ordered)
        transfer_capital = float(item.get("transfer_capital") or item.get("branch_capital") or 0)
        branch_retail = float(item.get("branch_retail") or 0)

        pending_items.append({
            **item,
            "qty_ordered": qty_ordered,
            "qty_received": qty_received,
        })

        variance = qty_ordered - qty_received   # positive = short, negative = excess
        if variance != 0:
            var_entry = {
                "product_id": product_id,
                "product_name": item["product_name"],
                "sku": item.get("sku", ""),
                "unit": item.get("unit", ""),
                "qty_ordered": qty_ordered,
                "qty_received": qty_received,
                "variance": variance,
                "transfer_capital": transfer_capital,
                "branch_retail": branch_retail,
                "capital_variance": round(abs(variance) * transfer_capital, 2),
                "retail_variance": round(abs(variance) * branch_retail, 2),
            }
            if variance > 0:
                shortages.append(var_entry)
            else:
                excesses.append(var_entry)

    has_variance = len(shortages) > 0 or len(excesses) > 0

    if has_variance:
        # ── PENDING PATH: store claim, do NOT touch inventory yet ────────────
        await db.branch_transfer_orders.update_one(
            {"id": transfer_id},
            {"$set": {
                "status": "received_pending",
                "pending_receipt_at": now_iso(),
                "pending_receipt_by": user["id"],
                "pending_receipt_by_name": user.get("full_name", user["username"]),
                "receive_notes": data.get("notes", ""),
                "pending_items": pending_items,
                "shortages": shortages,
                "excesses": excesses,
                "has_shortage": len(shortages) > 0,
                "has_excess": len(excesses) > 0,
            }}
        )

        # Notify source branch + admins to review
        src_users = await db.users.find(
            {"branch_id": from_branch_id, "active": True}, {"_id": 0, "id": 1}
        ).to_list(50)
        admins = await db.users.find(
            {"role": "admin", "active": True}, {"_id": 0, "id": 1}
        ).to_list(50)
        notify_ids = list({u["id"] for u in src_users + admins})

        variance_parts = []
        if shortages:
            variance_parts.append(f"{len(shortages)} short")
        if excesses:
            variance_parts.append(f"{len(excesses)} excess")

        await db.notifications.insert_one({
            "id": new_id(),
            "type": "transfer_variance_review",
            "title": "Transfer Receipt — Variance Needs Review",
            "message": (
                f"{to_name} received {order['order_number']} with discrepancy ({', '.join(variance_parts)}). "
                f"Please verify and Accept or Dispute."
            ),
            "branch_id": from_branch_id,
            "branch_name": from_name,
            "metadata": {
                "transfer_id": transfer_id,
                "order_number": order["order_number"],
                "shortages": shortages,
                "excesses": excesses,
            },
            "target_user_ids": notify_ids,
            "read_by": [],
            "created_at": now_iso(),
        })

        return {
            "message": "Receipt submitted — waiting for source branch to confirm the variance.",
            "status": "received_pending",
            "has_variance": True,
            "shortages": shortages,
            "excesses": excesses,
        }

    # ── EXACT MATCH PATH: update inventory immediately ───────────────────────
    return await _apply_receipt(
        order, pending_items, shortages, excesses, from_branch_id, to_branch_id,
        from_name, to_name, transfer_id, user, data.get("notes", ""),
        capital_choices=data.get("capital_choices", {})
    )


async def _apply_receipt(order, items, shortages, excesses, from_branch_id, to_branch_id,
                          from_name, to_name, transfer_id, user, notes="", capital_choices=None):
    """Apply the inventory movement for a confirmed receipt.
    capital_choices: dict of {product_id: "transfer_capital"|"moving_average"}
      - "transfer_capital": use the transfer's capital at destination
      - "moving_average": use the moving average from acquisition history
    Smart rule (when no explicit choice given):
      - transfer_capital >= current_dest_capital → use transfer_capital
      - transfer_capital < current_dest_capital  → use moving_average (cushion the drop)
    """
    # Ensure org context is set — super admins have None, which causes tenant writes
    # to omit organization_id. Resolve from the transfer order or branch.
    if not get_org_context():
        await ensure_org_context(branch_id=to_branch_id, org_id=order.get("organization_id"))

    if capital_choices is None:
        capital_choices = {}

    # ── PRE-FLIGHT: validate source stock for EVERY item BEFORE any inventory
    # mutation. Without this, a mid-loop HTTPException leaves items 1..N-1
    # already decremented from source + incremented at destination while the
    # transfer status never flips to "received" — user sees "Failed" but
    # stocks got deducted. Ref: live report BTO-20260503-0003.
    # Also snapshot each product's CURRENT destination capital here so the
    # audit log records the real old→new transition (not the post-upsert
    # value it was inadvertently reading).
    dest_capital_before_by_product: dict = {}
    for item in items:
        product_id = item["product_id"]
        qty_needed = float(item.get("qty_received", item["qty"]))
        if qty_needed <= 0:
            continue
        src_inv = await db.inventory.find_one(
            {"product_id": product_id, "branch_id": from_branch_id}, {"_id": 0, "quantity": 1}
        )
        src_stock = float(src_inv["quantity"]) if src_inv else 0
        if src_stock < qty_needed:
            raise HTTPException(
                status_code=400,
                detail=f"Insufficient stock for '{item['product_name']}' in source branch: "
                       f"have {src_stock:.0f}, need {qty_needed:.0f}. No inventory changed."
            )
        bp_before = await db.branch_prices.find_one(
            {"product_id": product_id, "branch_id": to_branch_id}, {"_id": 0, "cost_price": 1}
        )
        dest_capital_before_by_product[product_id] = (
            float(bp_before["cost_price"]) if bp_before and bp_before.get("cost_price") is not None else 0
        )

    for item in items:
        product_id = item["product_id"]
        qty_received = float(item.get("qty_received", item["qty"]))
        transfer_capital = float(item.get("transfer_capital") or item.get("branch_capital") or 0)
        branch_retail = float(item.get("branch_retail") or 0)

        # Current capital at destination (snapshot BEFORE we mutate) — used
        # both to drive the Smart Rule and to populate the audit log.
        current_dest_capital = dest_capital_before_by_product.get(product_id, 0)

        # Determine final capital at destination based on choice
        # Smart rule: same logic as PO receive
        explicit_choice = capital_choices.get(product_id)
        if explicit_choice:
            choice = explicit_choice
        elif transfer_capital < current_dest_capital and current_dest_capital > 0 and transfer_capital > 0:
            choice = "moving_average"
        else:
            choice = "transfer_capital"

        if choice == "moving_average":
            _, moving_avg = await _get_po_refs(product_id, to_branch_id)
            dest_capital = moving_avg if moving_avg > 0 else transfer_capital
        else:
            dest_capital = transfer_capital

        if qty_received <= 0:
            continue

        await db.inventory.update_one(
            {"product_id": product_id, "branch_id": from_branch_id},
            {"$inc": {"quantity": -qty_received}, "$set": {"updated_at": now_iso()}}
        )
        await db.inventory.update_one(
            {"product_id": product_id, "branch_id": to_branch_id},
            {"$inc": {"quantity": qty_received}, "$set": {"updated_at": now_iso()}},
            upsert=True
        )

        # Set branch_prices at destination using the resolved capital
        await db.branch_prices.update_one(
            {"product_id": product_id, "branch_id": to_branch_id},
            {"$set": {
                "product_id": product_id, "branch_id": to_branch_id,
                "cost_price": dest_capital,
                "prices": {"retail": branch_retail},
                "updated_at": now_iso(),
                "source": "branch_transfer",
                "transfer_order": order["order_number"],
            }},
            upsert=True
        )

        await db.branch_transfer_price_memory.update_one(
            {"product_id": product_id, "branch_id": to_branch_id},
            {"$set": {
                "product_id": product_id, "branch_id": to_branch_id,
                "last_retail_price": branch_retail, "last_transfer_capital": dest_capital,
                "last_order_number": order["order_number"], "updated_at": now_iso(),
            }},
            upsert=True
        )

        # Log capital change at destination — use the pre-flight snapshot
        # and the ACTUAL method selected (Smart Rule respected) so the audit
        # trail stays accurate.
        await db.capital_changes.insert_one({
            "id": new_id(),
            "product_id": product_id,
            "branch_id": to_branch_id,
            "old_capital": current_dest_capital,
            "new_capital": dest_capital,
            "method": choice,
            "source_type": "branch_transfer",
            "source_ref": order["order_number"],
            "from_branch": from_name,
            "to_branch": to_name,
            "changed_by_id": user["id"],
            "changed_by_name": user.get("full_name", user.get("username", "")),
            "changed_at": now_iso(),
            # was_user_choice: True if admin explicitly picked the new capital
            # via capital_choices (skip Smart Price alert in that case).
            "was_user_choice": product_id in capital_choices,
        })

        await log_movement(
            product_id, from_branch_id, "transfer_out", -qty_received,
            transfer_id, order["order_number"], dest_capital,
            user["id"], user.get("full_name", user["username"]),
            f"Branch transfer to {to_name}"
        )
        await log_movement(
            product_id, to_branch_id, "transfer_in", qty_received,
            transfer_id, order["order_number"], dest_capital,
            user["id"], user.get("full_name", user["username"]),
            f"Branch transfer from {from_name}"
        )

        # Clear "Global Price" badge at destination — transfer = implicit price review.
        # If admin used capital_choices to pick the value, that's still a manual decision,
        # so the badge clears regardless of method.
        await mark_price_reviewed(product_id, to_branch_id, source="transfer")

    # ── Apply repack price updates at destination ──────────────────────────────
    repack_updates = order.get("repack_price_updates", [])
    applied_repack_updates = []
    for rpu in repack_updates:
        repack_id = rpu.get("repack_id")
        new_price = rpu.get("new_retail_price")
        if not repack_id or new_price is None:
            continue
        new_price = float(new_price)
        if new_price <= 0:
            continue
        capital_per = float(rpu.get("capital_per_repack", 0))
        # Apply to destination branch_prices for the repack product
        await db.branch_prices.update_one(
            {"product_id": repack_id, "branch_id": to_branch_id},
            {"$set": {
                "product_id": repack_id, "branch_id": to_branch_id,
                "cost_price": capital_per,
                "prices": {"retail": new_price},
                "updated_at": now_iso(),
                "source": "branch_transfer_repack",
                "transfer_order": order["order_number"],
            }},
            upsert=True,
        )
        applied_repack_updates.append({
            "repack_id": repack_id,
            "repack_name": rpu.get("repack_name", ""),
            "new_retail_price": new_price,
        })

    # ── Date guard: if destination branch's "today" is closed, auto-roll
    # to the next open day for the receipt's effective_date. Inventory
    # plus/minus is not date-bound (final total matters), so we don't
    # block receiving — only stamp the right effective_date for any
    # downstream Z-Report/journal lookups.
    from utils.closed_day_guard import _is_closed, _next_open_day  # type: ignore
    receive_intended = now_iso()[:10]
    receive_effective = receive_intended
    receive_late_encoded = False
    receive_carryover_label = ""
    to_branch = order.get("to_branch_id", "")
    if to_branch and await _is_closed(to_branch, receive_intended):
        receive_effective = await _next_open_day(
            to_branch, receive_intended, order.get("organization_id") or ""
        )
        receive_late_encoded = True
        receive_carryover_label = (
            f"[LATE ENCODE] Branch Transfer Receipt — original date {receive_intended} (closed)"
        )

    await db.branch_transfer_orders.update_one(
        {"id": transfer_id},
        {"$set": {
            "status": "received",
            "received_at": now_iso(),
            "received_date": receive_effective,
            "received_intended_date": receive_intended,
            "receive_late_encoded": receive_late_encoded,
            "receive_late_encode_label": receive_carryover_label,
            "received_by": user["id"],
            "received_by_name": user.get("full_name", user["username"]),
            "receive_notes": notes,
            "items": items,
            "shortages": shortages,
            "excesses": excesses,
            "has_shortage": len(shortages) > 0,
            "has_excess": len(excesses) > 0,
        }}
    )

    # Update linked stock request PO status (fulfilled / partially_fulfilled)
    request_po_id = order.get("request_po_id", "")
    if request_po_id:
        total_requested = sum(float(i.get("requested_qty", i.get("qty", 0))) for i in items)
        total_sent = sum(float(i.get("qty_received", i.get("qty", 0))) for i in items)
        fulfillment_status = "fulfilled" if total_sent >= total_requested else "partially_fulfilled"
        await db.purchase_orders.update_one(
            {"id": request_po_id},
            {"$set": {
                "status": fulfillment_status,
                "fulfilled_at": now_iso(),
                "fulfilled_transfer_id": transfer_id,
                "fulfilled_transfer_number": order.get("order_number", ""),
            }}
        )

    # Update internal invoice status to "received"
    from routes.internal_invoices import update_invoice_status
    try:
        # Update invoice with actual received amounts
        actual_total = round(sum(
            float(i.get("transfer_capital", 0)) * float(i.get("qty_received", i.get("qty", 0)))
            for i in items
        ), 2)
        await update_invoice_status(transfer_id, "received", {
            "received_total": actual_total,
            "has_variance": len(shortages) > 0 or len(excesses) > 0,
        })
    except Exception:
        pass

    return {
        "message": f"Transfer received. {len(items)} product(s) updated.",
        "order_number": order["order_number"],
        "status": "received",
        "shortages": shortages,
        "excesses": excesses,
        "has_shortage": len(shortages) > 0,
        "has_excess": len(excesses) > 0,
        "repack_prices_applied": applied_repack_updates,
    }


@router.post("/{transfer_id}/accept-receipt")
async def accept_receipt(transfer_id: str, data: dict, user=Depends(get_current_user)):
    """
    Source branch accepts the destination's claimed quantities.
    Triggers the actual inventory movement and finalises the transfer.
    
    Options via `action`:
      - "accept" (default): Accept variance, log it, move inventory.
      - "accept_with_incident": Accept AND create an incident ticket for investigation.
    """
    if user.get("role") not in ["admin", "manager"]:
        raise HTTPException(status_code=403, detail="Manager or admin required")

    order = await db.branch_transfer_orders.find_one({"id": transfer_id}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Transfer not found")
    if order["status"] != "received_pending":
        raise HTTPException(status_code=400, detail="Transfer is not pending receipt confirmation")

    user_branch = user.get("branch_id")
    if user.get("role") != "admin" and user_branch and user_branch != order["from_branch_id"]:
        raise HTTPException(status_code=403, detail="Only the source branch can accept this receipt")

    # Ensure org context for super admin
    if not get_org_context():
        org_id = order.get("organization_id")
        await ensure_org_context(branch_id=order["from_branch_id"], org_id=org_id)

    from_branch_id = order["from_branch_id"]
    to_branch_id = order["to_branch_id"]
    from_branch = await db.branches.find_one({"id": from_branch_id}, {"_id": 0, "name": 1})
    to_branch = await db.branches.find_one({"id": to_branch_id}, {"_id": 0, "name": 1})
    from_name = from_branch.get("name", from_branch_id) if from_branch else from_branch_id
    to_name = to_branch.get("name", to_branch_id) if to_branch else to_branch_id

    items = order.get("pending_items", order["items"])
    shortages = order.get("shortages", [])
    excesses = order.get("excesses", [])
    accept_note = data.get("note", "").strip()
    action = data.get("action", "accept")

    # Apply inventory movement
    result = await _apply_receipt(
        order, items, shortages, excesses, from_branch_id, to_branch_id,
        from_name, to_name, transfer_id, user,
        notes=order.get("receive_notes", "")
    )

    # Record acceptance
    total_capital_loss = sum(s.get("capital_variance", 0) for s in shortages)
    total_retail_loss = sum(s.get("retail_variance", 0) for s in shortages)

    update_fields = {
        "accepted_by": user["id"],
        "accepted_by_name": user.get("full_name", user["username"]),
        "accepted_at": now_iso(),
        "accept_note": accept_note,
        "accept_action": action,
        "total_capital_loss": total_capital_loss,
        "total_retail_loss": total_retail_loss,
    }

    # Log variance as audit record
    if shortages or excesses:
        await db.audit_log.insert_one({
            "id": new_id(),
            "type": "transfer_variance_accepted",
            "entity_type": "branch_transfer",
            "entity_id": transfer_id,
            "description": (
                f"Transfer {order['order_number']} variance accepted by {user.get('full_name', user['username'])}. "
                f"Shortages: {len(shortages)}, Excesses: {len(excesses)}. "
                f"Capital loss: {total_capital_loss:.2f}. Note: {accept_note or 'N/A'}"
            ),
            "metadata": {
                "order_number": order["order_number"],
                "from_branch": from_name,
                "to_branch": to_name,
                "shortages": shortages,
                "excesses": excesses,
                "total_capital_loss": total_capital_loss,
                "total_retail_loss": total_retail_loss,
                "action": action,
            },
            "branch_id": from_branch_id,
            "user_id": user["id"],
            "user_name": user.get("full_name", user["username"]),
            "created_at": now_iso(),
        })

    # Create incident ticket if requested
    incident_ticket_id = None
    if action == "accept_with_incident" and (shortages or excesses):
        ticket_count = await db.incident_tickets.count_documents({})
        ticket_number = f"INC-{ticket_count + 1:05d}"
        incident_ticket_id = new_id()

        variance_items = []
        for s in shortages:
            variance_items.append({**s, "type": "shortage"})
        for e in excesses:
            variance_items.append({**e, "type": "excess"})

        ticket = {
            "id": incident_ticket_id,
            "ticket_number": ticket_number,
            "transfer_id": transfer_id,
            "order_number": order["order_number"],
            "from_branch_id": from_branch_id,
            "from_branch_name": from_name,
            "to_branch_id": to_branch_id,
            "to_branch_name": to_name,
            "items": variance_items,
            "total_capital_loss": total_capital_loss,
            "total_retail_loss": total_retail_loss,
            "status": "open",
            "priority": "high" if total_capital_loss > 1000 else "medium",
            "created_by_id": user["id"],
            "created_by_name": user.get("full_name", user["username"]),
            "assigned_to_id": "",
            "assigned_to_name": "",
            "resolution_note": "",
            "recovery_amount": 0,
            "timeline": [
                {
                    "action": "created",
                    "by_id": user["id"],
                    "by_name": user.get("full_name", user["username"]),
                    "detail": f"Incident created from transfer {order['order_number']} variance. {accept_note}" if accept_note else f"Incident created from transfer {order['order_number']} variance.",
                    "at": now_iso(),
                }
            ],
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }
        await db.incident_tickets.insert_one(ticket)
        del ticket["_id"]

        update_fields["incident_ticket_id"] = incident_ticket_id
        update_fields["incident_ticket_number"] = ticket_number

        # Notify admins about the new incident
        admins = await db.users.find({"role": "admin", "active": True}, {"_id": 0, "id": 1}).to_list(50)
        admin_ids = [a["id"] for a in admins]
        await db.notifications.insert_one({
            "id": new_id(),
            "type": "incident_created",
            "title": f"Incident Ticket {ticket_number} — Transfer Variance",
            "message": (
                f"Transfer {order['order_number']} ({from_name} → {to_name}) has an unresolved variance. "
                f"Capital loss: ₱{total_capital_loss:,.2f}. Investigation required."
            ),
            "branch_id": from_branch_id,
            "branch_name": from_name,
            "metadata": {
                "ticket_id": incident_ticket_id,
                "ticket_number": ticket_number,
                "transfer_id": transfer_id,
            },
            "target_user_ids": admin_ids,
            "read_by": [],
            "created_at": now_iso(),
        })

    await db.branch_transfer_orders.update_one(
        {"id": transfer_id}, {"$set": update_fields}
    )

    # Notify destination that the receipt was accepted
    dest_users = await db.users.find(
        {"branch_id": to_branch_id, "active": True}, {"_id": 0, "id": 1}
    ).to_list(50)
    admins = await db.users.find({"role": "admin", "active": True}, {"_id": 0, "id": 1}).to_list(50)
    notify_ids = list({u["id"] for u in dest_users + admins})
    msg = f"{from_name} accepted the receipt for {order['order_number']}. Inventory has been updated."
    if incident_ticket_id:
        msg += f" An incident ticket ({update_fields['incident_ticket_number']}) has been created for investigation."
    await db.notifications.insert_one({
        "id": new_id(),
        "type": "transfer_accepted",
        "title": "Transfer Receipt Accepted",
        "message": msg,
        "branch_id": to_branch_id,
        "branch_name": to_name,
        "metadata": {"transfer_id": transfer_id, "order_number": order["order_number"],
                      "incident_ticket_id": incident_ticket_id},
        "target_user_ids": notify_ids,
        "read_by": [],
        "created_at": now_iso(),
    })

    resp = {**result}
    if incident_ticket_id:
        resp["incident_ticket_id"] = incident_ticket_id
        resp["incident_ticket_number"] = update_fields["incident_ticket_number"]
    return resp


@router.post("/{transfer_id}/dispute-receipt")
async def dispute_receipt(transfer_id: str, data: dict, user=Depends(get_current_user)):
    """
    Source branch disputes the destination's claimed quantities.
    Inventory is NOT updated. Destination is notified to re-count.
    """
    if user.get("role") not in ["admin", "manager"]:
        raise HTTPException(status_code=403, detail="Manager or admin required")

    order = await db.branch_transfer_orders.find_one({"id": transfer_id}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Transfer not found")
    if order["status"] != "received_pending":
        raise HTTPException(status_code=400, detail="Transfer is not pending receipt confirmation")

    user_branch = user.get("branch_id")
    if user.get("role") != "admin" and user_branch and user_branch != order["from_branch_id"]:
        raise HTTPException(status_code=403, detail="Only the source branch can dispute this receipt")

    dispute_note = data.get("note", "").strip()
    if not dispute_note:
        raise HTTPException(status_code=400, detail="Dispute reason is required")

    from_branch_id = order["from_branch_id"]
    to_branch_id = order["to_branch_id"]
    from_branch = await db.branches.find_one({"id": from_branch_id}, {"_id": 0, "name": 1})
    to_branch = await db.branches.find_one({"id": to_branch_id}, {"_id": 0, "name": 1})
    from_name = from_branch.get("name", from_branch_id) if from_branch else from_branch_id
    to_name = to_branch.get("name", to_branch_id) if to_branch else to_branch_id

    await db.branch_transfer_orders.update_one(
        {"id": transfer_id},
        {"$set": {
            "status": "disputed",
            "disputed_at": now_iso(),
            "disputed_by": user["id"],
            "disputed_by_name": user.get("full_name", user["username"]),
            "dispute_note": dispute_note,
        }}
    )

    # Notify destination to re-count
    dest_users = await db.users.find(
        {"branch_id": to_branch_id, "active": True}, {"_id": 0, "id": 1}
    ).to_list(50)
    admins = await db.users.find({"role": "admin", "active": True}, {"_id": 0, "id": 1}).to_list(50)
    notify_ids = list({u["id"] for u in dest_users + admins})
    await db.notifications.insert_one({
        "id": new_id(),
        "type": "transfer_disputed",
        "title": "Transfer Receipt Disputed",
        "message": (
            f"{from_name} disputes the quantities for {order['order_number']}. "
            f"Reason: {dispute_note}. Please re-count and re-submit."
        ),
        "branch_id": to_branch_id,
        "branch_name": to_name,
        "metadata": {
            "transfer_id": transfer_id,
            "order_number": order["order_number"],
            "dispute_note": dispute_note,
        },
        "target_user_ids": notify_ids,
        "read_by": [],
        "created_at": now_iso(),
    })

    return {
        "message": f"Receipt disputed. {to_name} has been notified to re-count.",
        "status": "disputed",
    }


@router.delete("/{transfer_id}")
async def cancel_transfer(transfer_id: str, user=Depends(get_current_user)):
    """
    Cancel a draft or sent transfer.
    Blocks cancellation if inventory has already moved (received/received_pending/disputed).
    """
    order = await db.branch_transfer_orders.find_one({"id": transfer_id}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Transfer not found")
    if order["status"] in ["received", "received_pending", "disputed", "sent_to_terminal"]:
        detail = "Cannot cancel — inventory has already been partially or fully received. Use Accept/Dispute flow to resolve discrepancies."
        if order["status"] == "sent_to_terminal":
            detail = "Cannot cancel — transfer is being checked on a terminal."
        raise HTTPException(status_code=400, detail=detail)
    await db.branch_transfer_orders.update_one(
        {"id": transfer_id}, {"$set": {"status": "cancelled", "cancelled_at": now_iso()}}
    )
    return {"message": "Transfer cancelled"}



@router.post("/admin/fix-orphaned-movements")
async def fix_orphaned_movements(user=Depends(get_current_user)):
    """Fix movements that were created without organization_id (super admin bug).
    Looks up the correct org from the branch_id on each movement."""
    if user.get("role") != "admin" and not user.get("is_super_admin"):
        raise HTTPException(status_code=403, detail="Admin required")

    # Find movements without organization_id
    orphaned = await _raw_db.movements.find(
        {"organization_id": {"$exists": False}},
        {"_id": 1, "branch_id": 1, "reference_number": 1, "type": 1}
    ).to_list(10000)

    if not orphaned:
        return {"message": "No orphaned movements found", "fixed": 0}

    # Build branch → org mapping
    branch_ids = list({m.get("branch_id") for m in orphaned if m.get("branch_id")})
    branches = await _raw_db.branches.find(
        {"id": {"$in": branch_ids}},
        {"_id": 0, "id": 1, "organization_id": 1}
    ).to_list(100)
    branch_org_map = {b["id"]: b.get("organization_id") for b in branches}

    fixed = 0
    for m in orphaned:
        org_id = branch_org_map.get(m.get("branch_id"))
        if org_id:
            await _raw_db.movements.update_one(
                {"_id": m["_id"]},
                {"$set": {"organization_id": org_id}}
            )
            fixed += 1

    # Also fix other tenant collections with missing org_id
    other_collections = ["capital_changes", "branch_transfer_orders", "inventory",
                         "branch_prices", "branch_transfer_price_memory", "notifications",
                         "audit_log", "incident_tickets", "business_documents"]
    other_fixed = {}
    for col_name in other_collections:
        col = _raw_db[col_name]
        orphaned_docs = await col.find(
            {"organization_id": {"$exists": False}},
            {"_id": 1, "branch_id": 1}
        ).to_list(10000)
        count = 0
        for doc in orphaned_docs:
            org_id = branch_org_map.get(doc.get("branch_id"))
            if org_id:
                await col.update_one({"_id": doc["_id"]}, {"$set": {"organization_id": org_id}})
                count += 1
        if count > 0:
            other_fixed[col_name] = count

    return {
        "message": f"Fixed {fixed} orphaned movements",
        "movements_fixed": fixed,
        "other_collections_fixed": other_fixed,
        "total_orphaned_checked": len(orphaned),
    }

"""
QR Actions — context-aware operational actions triggered by scanning a document QR code.
No login required; all actions are PIN-gated via the existing pin_policy system.

Security hardening (implemented):
  - Brute-force lockout: 5 failures → admin alert; 10 failures → 15-min lock (429)
  - Idempotency keys on receive_payment and transfer_receive
  - Journal entry written for every QR payment
  - IP + User-Agent captured on every action log

Supported actions:
  release_stocks   — decrement reserved_qty, mark delivery batches (Phase 2)
  receive_payment  — record payment on invoice (Phase 3)
  transfer_receive — receive a branch transfer via QR (Phase 4)
"""
from fastapi import APIRouter, HTTPException, Request
from datetime import datetime, timezone, timedelta
from config import db, _raw_db, set_org_context, get_org_context
from utils import now_iso, new_id, is_digital_payment, update_cashier_wallet, update_digital_wallet, log_movement, today_local
from utils.security import (
    check_qr_lockout, log_failed_qr_pin_attempt, log_successful_qr_pin_attempt,
)

router = APIRouter(prefix="/qr-actions", tags=["QR Actions"])


_DOC_TYPE_TO_COLLECTION = {
    "invoice": "invoices",
    "purchase_order": "purchase_orders",
    "branch_transfer": "branch_transfer_orders",
}


async def _verify_terminal_session(terminal_id: str, device_id: str = ""):
    """Verify that a terminal_id corresponds to an active terminal session.
    Also verifies device binding if the session was paired with a device_id.
    QR actions (stock release, payment, transfer/PO receive) require a paired terminal."""
    if not terminal_id:
        raise HTTPException(
            status_code=403,
            detail="Actions require an AgriSmart Terminal. Please use a paired terminal device."
        )
    from config import _raw_db
    session = await _raw_db.terminal_sessions.find_one(
        {"terminal_id": terminal_id, "status": "active"}, {"_id": 0, "terminal_id": 1, "device_id": 1}
    )
    if not session:
        raise HTTPException(
            status_code=403,
            detail="Invalid or expired terminal session. Please re-pair the terminal."
        )

    # Device binding — only enforce when BOTH session and caller have a non-empty device_id
    stored_device_id = session.get("device_id", "")
    if stored_device_id and device_id and stored_device_id != device_id:
        raise HTTPException(
            status_code=403,
            detail={
                "message": "Device mismatch. This session was paired on a different device. Please re-pair.",
                "code": "DEVICE_MISMATCH",
            }
        )


async def _verify_staff_jwt(token: str, branch_id: str = "") -> dict:
    """Verify a staff user JWT for web payment. Returns user dict if valid admin/manager/owner.
    Branch restriction: managers can only process payments for their own branch."""
    import jwt as pyjwt
    from config import JWT_SECRET, _raw_db
    try:
        payload = pyjwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired staff session. Please log in again.")
    user = await _raw_db.users.find_one({"id": payload.get("user_id", ""), "active": True}, {"_id": 0})
    if not user:
        raise HTTPException(status_code=401, detail="Staff account not found or inactive")
    role = user.get("role", "")
    if role not in ("admin", "owner", "manager"):
        raise HTTPException(status_code=403, detail="You don't have the necessary authority for this action")
    if role == "manager" and branch_id:
        mgr_branch = user.get("branch_id", "")
        if mgr_branch and mgr_branch != branch_id:
            raise HTTPException(status_code=403, detail="Manager can only process payments for their assigned branch")
    return user


async def _resolve_doc(code: str):
    """Look up doc_code, set tenant context (so downstream `db.*` queries work
    on these public/no-JWT endpoints), and return (doc_ref, doc_type, doc_id)."""
    code = code.strip().upper()
    doc_ref = await db.doc_codes.find_one({"code": code}, {"_id": 0})
    if not doc_ref:
        raise HTTPException(status_code=404, detail=f"Document code '{code}' not found")

    # Public QR endpoints have no JWT → tenant proxy is fail-closed.
    # Hydrate org context from the doc_code entry, with a legacy fallback that
    # reads the referenced document via _raw_db when org_id wasn't stamped.
    org_id = (doc_ref.get("org_id") or "").strip()
    if not org_id:
        coll_name = _DOC_TYPE_TO_COLLECTION.get(doc_ref.get("doc_type", ""))
        if coll_name:
            doc = await _raw_db[coll_name].find_one(
                {"id": doc_ref.get("doc_id", "")},
                {"_id": 0, "organization_id": 1},
            )
            if doc and doc.get("organization_id"):
                org_id = doc["organization_id"]
                try:
                    await _raw_db.doc_codes.update_one(
                        {"code": code}, {"$set": {"org_id": org_id}}
                    )
                except Exception:
                    pass

    if org_id:
        set_org_context(org_id)

    return doc_ref, doc_ref["doc_type"], doc_ref["doc_id"]


async def _log_action(doc_ref, action, verifier, payload_summary, result="success", error=None, release_ref=None, client_ip="", user_agent=""):
    await db.qr_action_log.insert_one({
        "id": new_id(),
        "doc_code": doc_ref["code"],
        "doc_type": doc_ref["doc_type"],
        "doc_id": doc_ref["doc_id"],
        "action": action,
        "release_ref": release_ref,
        "performed_by_name": verifier.get("verifier_name", "") if verifier else "",
        "pin_method": verifier.get("method", "") if verifier else "",
        "payload_summary": payload_summary,
        "result": result,
        "error": error,
        "client_ip": client_ip,
        "user_agent": user_agent,
        "created_at": now_iso(),
    })


# ── Context endpoint (used by mobile page to know what actions are available) ─

@router.get("/{code}/context")
async def get_qr_context(code: str):
    """Returns document info + available actions for the mobile QR page. No auth required."""
    from routes.doc_lookup import view_document_open
    return await view_document_open(code)


@router.post("/{code}/verify_pin")
async def verify_release_pin(code: str, data: dict, request: Request):
    """
    Validates a PIN for the relevant policy based on the document's type and branch.
    Returns { valid: true, verifier_name } or 403.
    Used to unlock action panels before any action is taken.
    """
    pin = (data.get("pin") or "").strip()
    if not pin:
        raise HTTPException(status_code=400, detail="PIN is required")

    doc_ref, doc_type, doc_id = await _resolve_doc(code)
    client_ip  = request.client.host if request.client else ""
    user_agent = request.headers.get("user-agent", "")

    # ── Lockout check ────────────────────────────────────────────────────────
    lockout = await check_qr_lockout(doc_ref["code"])
    if lockout["locked"]:
        raise HTTPException(status_code=429, detail={
            "message": "Too many failed attempts. This document is temporarily locked.",
            "retry_after": lockout["retry_after"],
            "locked": True,
        })

    # Derive action key and branch_id from doc type
    if doc_type == "invoice":
        doc = await db.invoices.find_one({"id": doc_id}, {"_id": 0, "branch_id": 1})
        action_key = "qr_release_stocks"
    elif doc_type == "branch_transfer":
        doc = await db.branch_transfer_orders.find_one({"id": doc_id}, {"_id": 0, "to_branch_id": 1})
        action_key = "qr_transfer_receive"
        if doc:
            doc = {"branch_id": doc.get("to_branch_id", "")}
    else:
        raise HTTPException(status_code=400, detail=f"No PIN action defined for doc type: {doc_type}")

    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    branch_id = doc.get("branch_id", "")

    from routes.verify import verify_pin_for_action
    verifier = await verify_pin_for_action(pin, action_key, branch_id=branch_id)
    if not verifier:
        await log_failed_qr_pin_attempt(doc_ref["code"], doc_type, "verify_pin", client_ip, branch_id)
        raise HTTPException(status_code=403, detail={
            "message": "Invalid PIN",
            "warn": lockout["warn"],
            "attempts_remaining": max(0, lockout["attempts_remaining"] - 1),
        })

    await log_successful_qr_pin_attempt(doc_ref["code"], doc_type, "verify_pin", verifier["verifier_name"], client_ip)
    return {"valid": True, "verifier_name": verifier["verifier_name"], "method": verifier["method"]}


# ── Action: Release Stocks ─────────────────────────────────────────────────────

@router.post("/{code}/release_stocks")
async def release_stocks(code: str, data: dict, request: Request):
    """
    Release a batch of reserved stock for a partial-release invoice.
    PIN required (qr_release_stocks policy — branch-restricted for managers).
    Terminal required.

    Body: {
      pin: str,
      release_ref: str (client UUID for idempotency),
      items: [{ sold_product_id: str, qty_release: float }],
      terminal_id: str (required — active terminal session)
    }
    """
    await _verify_terminal_session(data.get("terminal_id", ""), data.get("device_id", ""))

    pin = (data.get("pin") or "").strip()
    release_ref = (data.get("release_ref") or "").strip()
    items_input = data.get("items", [])
    client_ip  = request.client.host if request.client else ""
    user_agent = request.headers.get("user-agent", "")

    if not pin:
        raise HTTPException(status_code=400, detail="PIN is required")
    if not items_input:
        raise HTTPException(status_code=400, detail="No items to release")

    # Resolve document
    doc_ref, doc_type, doc_id = await _resolve_doc(code)
    if doc_type != "invoice":
        raise HTTPException(status_code=400, detail="This QR code is not for an invoice")

    # ── Lockout check ────────────────────────────────────────────────────────
    lockout = await check_qr_lockout(doc_ref["code"])
    if lockout["locked"]:
        raise HTTPException(status_code=429, detail={
            "message": "Too many failed attempts. This document is temporarily locked.",
            "retry_after": lockout["retry_after"],
            "locked": True,
        })

    invoice = await db.invoices.find_one({"id": doc_id}, {"_id": 0})
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if invoice.get("release_mode") != "partial":
        raise HTTPException(status_code=400, detail="This invoice uses full release — no stock release needed")
    if invoice.get("stock_release_status") in ("fully_released", "expired"):
        raise HTTPException(status_code=400, detail="All stock for this invoice has already been released")
    if invoice.get("status") == "voided":
        raise HTTPException(status_code=400, detail="Cannot release stock on a voided invoice")

    # Idempotency check — reject duplicate release_ref
    if release_ref:
        already = await db.qr_action_log.find_one(
            {"doc_id": doc_id, "action": "release_stocks", "release_ref": release_ref, "result": "success"},
            {"_id": 0}
        )
        if already:
            raise HTTPException(status_code=409, detail="This release was already processed")

    branch_id = invoice.get("branch_id", "")

    # Verify PIN — branch-restricted for managers
    from routes.verify import verify_pin_for_action
    verifier = await verify_pin_for_action(pin, "qr_release_stocks", branch_id=branch_id)
    if not verifier:
        await log_failed_qr_pin_attempt(doc_ref["code"], doc_type, "release_stocks", client_ip, branch_id, terminal_id=data.get("terminal_id", ""))
        await _log_action(doc_ref, "release_stocks", None, "PIN failed", result="failed", error="invalid_pin", client_ip=client_ip, user_agent=user_agent)
        raise HTTPException(status_code=403, detail={
            "message": "Invalid PIN — use branch manager PIN, admin PIN, or admin TOTP",
            "warn": lockout["warn"],
            "attempts_remaining": max(0, lockout["attempts_remaining"] - 1),
        })
    await log_successful_qr_pin_attempt(doc_ref["code"], doc_type, "release_stocks", verifier["verifier_name"], client_ip)

    # Load current reservations keyed by sold_product_id
    reservations = await db.sale_reservations.find(
        {"invoice_id": doc_id}, {"_id": 0}
    ).to_list(100)
    res_map = {r["sold_product_id"]: r for r in reservations}

    # Validate all items first before touching anything
    for item in items_input:
        spid = item.get("sold_product_id")
        qty_release = float(item.get("qty_release", 0))
        if qty_release <= 0:
            continue
        res = res_map.get(spid)
        if not res:
            raise HTTPException(status_code=400, detail="Product not found in this invoice's reservations")
        if qty_release > res["sold_qty_remaining"] + 0.001:  # float tolerance
            raise HTTPException(
                status_code=400,
                detail=f"Cannot release {qty_release} of {res['sold_product_name']} — only {res['sold_qty_remaining']} remaining"
            )

    # Apply releases
    release_items_log = []
    total_released_this_batch = 0

    for item in items_input:
        spid = item.get("sold_product_id")
        qty_release = float(item.get("qty_release", 0))
        if qty_release <= 0:
            continue

        res = res_map[spid]
        inv_product_id = res["product_id"]     # inventory product (parent for repacks)
        units_per_parent = float(res.get("units_per_parent", 1))
        qty_release_inv = qty_release / units_per_parent  # in inventory units

        # Decrement reserved_qty on inventory — no quantity change (already deducted at sale)
        await db.inventory.update_one(
            {"product_id": inv_product_id, "branch_id": branch_id},
            {"$inc": {"reserved_qty": -qty_release_inv}, "$set": {"updated_at": now_iso()}}
        )

        # Log movement: quantity_change=0 (stock already left at sale time),
        # reserved_qty_change=-qty_release_inv (reserved stock handed to customer)
        await log_movement(
            inv_product_id, branch_id, "sale_release", 0,
            doc_id, invoice.get("invoice_number", ""),
            0,  # price already captured at original sale
            verifier["verifier_id"], verifier["verifier_name"],
            f"Released to customer: {res['sold_product_name']} x{qty_release} {res.get('sold_unit', '')}",
            reserved_qty_change=-qty_release_inv,
        )

        # Update sale_reservation record
        new_sold_released = res["sold_qty_released"] + qty_release
        new_sold_remaining = res["sold_qty_remaining"] - qty_release
        new_qty_released = res["qty_released"] + qty_release_inv
        new_qty_remaining = res["qty_remaining"] - qty_release_inv

        await db.sale_reservations.update_one(
            {"invoice_id": doc_id, "sold_product_id": spid},
            {"$set": {
                "sold_qty_released": round(new_sold_released, 4),
                "sold_qty_remaining": round(max(0, new_sold_remaining), 4),
                "qty_released": round(new_qty_released, 4),
                "qty_remaining": round(max(0, new_qty_remaining), 4),
            }}
        )

        release_items_log.append({
            "product_id": spid,
            "product_name": res["sold_product_name"],
            "qty_released": qty_release,
            "unit": res.get("sold_unit", ""),
        })
        total_released_this_batch += qty_release
        # Update local map for status check below
        res_map[spid] = {**res,
            "sold_qty_released": new_sold_released,
            "sold_qty_remaining": max(0, new_sold_remaining),
            "qty_remaining": max(0, new_qty_remaining),
        }

    # Compute new stock_release_status
    all_remaining = sum(r["sold_qty_remaining"] for r in res_map.values())
    all_released = sum(r["sold_qty_released"] for r in res_map.values())
    if all_remaining <= 0.001:
        new_status = "fully_released"
    elif all_released > 0:
        new_status = "partially_released"
    else:
        new_status = "not_released"

    # Release event record
    release_number = len(invoice.get("stock_releases", [])) + 1
    release_event = {
        "release_number": release_number,
        "released_at": now_iso(),
        "released_by_name": verifier["verifier_name"],
        "pin_method": verifier["method"],
        "release_ref": release_ref,
        "items": release_items_log,
        "total_qty_released": round(total_released_this_batch, 4),
        "remaining_after": round(all_remaining, 4),
        "fully_released": new_status == "fully_released",
    }

    await db.invoices.update_one(
        {"id": doc_id},
        {
            "$set": {"stock_release_status": new_status},
            "$push": {"stock_releases": release_event},
        }
    )

    # Log to qr_action_log
    summary = ", ".join(f"{i['product_name']} x{i['qty_released']}{i['unit']}" for i in release_items_log)
    await _log_action(doc_ref, "release_stocks", verifier, summary, release_ref=release_ref, client_ip=client_ip, user_agent=user_agent)

    return {
        "success": True,
        "invoice_number": invoice.get("invoice_number"),
        "release_number": release_number,
        "items_released": release_items_log,
        "stock_release_status": new_status,
        "remaining_qty": round(all_remaining, 4),
        "fully_released": new_status == "fully_released",
        "authorized_by": verifier["verifier_name"],
        "message": "All stock fully released!" if new_status == "fully_released" else f"Batch {release_number} recorded. {round(all_remaining, 2)} units still pending.",
    }



# ── Action: Confirm Stock Request (Phase 2 — QR/mobile) ──────────────────────

@router.post("/{code}/confirm_stock_request")
async def confirm_stock_request_qr(code: str, data: dict, request: Request):
    """Confirm/adjust `approved_qty` on a branch-request PO via QR scan.

    Mobile counterpart of `POST /api/purchase-orders/{po_id}/confirm-request`
    (Phase 1). Delegates the actual confirmation work to
    `routes.purchase_orders._apply_confirmation` so the rules stay in one
    place:
      * Original `items[].quantity` (requested qty) is NEVER overwritten.
      * `approved_qty` / `approved_note` saved per line; approval_note global.
      * `approved_qty > requested_qty` requires a note.
      * Soft-locks once a non-cancelled linked BTO exists.
      * Appends `request_approval_log` row with `source="qr_mobile"`.
      * No stock movement. No BTO created. No internal invoice.

    Auth: PIN/TOTP via `confirm_stock_request` policy, branch-scoped to
    `supply_branch_id`. Brute-force lockout via `check_qr_lockout`.
    Idempotency: optional `confirm_ref` → re-running with the same ref
    returns the cached result and skips mutation.

    Body: { pin, items: [...], approval_note?, confirm_ref? }
    """
    pin = (data.get("pin") or "").strip()
    confirm_ref = (data.get("confirm_ref") or "").strip()
    client_ip  = request.client.host if request.client else ""
    user_agent = request.headers.get("user-agent", "")

    if not pin:
        raise HTTPException(status_code=400, detail="PIN is required")

    doc_ref, doc_type, doc_id = await _resolve_doc(code)
    if doc_type != "purchase_order":
        raise HTTPException(status_code=400, detail="This QR code is not for a stock request")

    po = await db.purchase_orders.find_one({"id": doc_id}, {"_id": 0})
    if not po:
        raise HTTPException(status_code=404, detail="Request not found")
    if po.get("po_type") != "branch_request":
        raise HTTPException(status_code=400, detail="This QR code is not for a stock request")

    supply_branch_id = po.get("supply_branch_id", "")

    # ── Brute-force lockout ──────────────────────────────────────────────────
    lockout = await check_qr_lockout(doc_ref["code"])
    if lockout["locked"]:
        raise HTTPException(status_code=429, detail={
            "message": "Too many failed attempts. This document is temporarily locked.",
            "retry_after": lockout["retry_after"],
            "locked": True,
        })

    # ── Idempotency: replay cached response on duplicate confirm_ref. ────────
    if confirm_ref:
        prior = await db.qr_action_log.find_one(
            {"doc_id": doc_id, "action": "confirm_stock_request",
             "release_ref": confirm_ref, "result": "success"},
            {"_id": 0, "payload_summary": 1, "performed_by_name": 1},
        )
        if prior:
            # Idempotent replay — return current PO state without mutation.
            current = await db.purchase_orders.find_one(
                {"id": doc_id}, {"_id": 0})
            items_out = []
            for it in (current.get("items") or []):
                appr_raw = it.get("approved_qty")
                has_appr = appr_raw is not None and appr_raw != ""
                items_out.append({
                    "product_id": it.get("product_id", ""),
                    "product_name": it.get("product_name", ""),
                    "requested_qty": float(it.get("quantity", 0) or 0),
                    "approved_qty": float(appr_raw) if has_appr else None,
                    "approved_note": it.get("approved_note", "") or "",
                })
            return {
                "po_id": doc_id,
                "po_number": current.get("po_number", ""),
                "approval_status": current.get("approval_status") or "pending",
                "approved_at": current.get("approved_at", ""),
                "approved_by_name": current.get("approved_by_name", ""),
                "approval_method": current.get("approval_method", ""),
                "approval_note": current.get("approval_note", ""),
                "items": items_out,
                "idempotent": True,
            }

    # ── PIN verification — branch-scoped to supply branch. ────────────────────
    from routes.verify import verify_pin_for_action
    verifier = await verify_pin_for_action(
        pin, "confirm_stock_request", branch_id=supply_branch_id)
    if not verifier:
        await log_failed_qr_pin_attempt(
            doc_ref["code"], doc_type, "confirm_stock_request",
            client_ip, supply_branch_id)
        await _log_action(
            doc_ref, "confirm_stock_request", None, "PIN failed",
            result="failed", error="invalid_pin",
            release_ref=confirm_ref,
            client_ip=client_ip, user_agent=user_agent)
        raise HTTPException(status_code=403, detail={
            "message": "Invalid PIN — use supply-branch manager PIN, admin PIN, or TOTP",
            "warn": lockout["warn"],
            "attempts_remaining": max(0, lockout["attempts_remaining"] - 1),
        })
    await log_successful_qr_pin_attempt(
        doc_ref["code"], doc_type, "confirm_stock_request",
        verifier["verifier_name"], client_ip)

    # ── Delegate to the shared Phase 1 helper. ───────────────────────────────
    from routes.purchase_orders import _apply_confirmation
    try:
        result = await _apply_confirmation(
            po=po,
            approval_payload=data,
            verifier=verifier,
            caller_id=verifier.get("verifier_id", ""),
            caller_name=verifier.get("verifier_name", ""),
            source="qr_mobile",
            extra_log_fields={
                "client_ip": client_ip,
                "user_agent": user_agent,
                "confirm_ref": confirm_ref,
            },
        )
    except HTTPException as e:
        await _log_action(
            doc_ref, "confirm_stock_request", verifier, str(e.detail),
            result="failed", error="rejected",
            release_ref=confirm_ref,
            client_ip=client_ip, user_agent=user_agent)
        raise

    summary = (
        f"{len(result.get('items') or [])} line(s) "
        f"→ {result.get('approval_status')}"
    )
    await _log_action(
        doc_ref, "confirm_stock_request", verifier, summary,
        result="success", release_ref=confirm_ref,
        client_ip=client_ip, user_agent=user_agent)

    result["idempotent"] = False
    return result


# ── Generate upload token (public — for mobile payment proof upload) ──────────

@router.post("/{code}/generate-upload-token")
async def generate_upload_token(code: str, data: dict, request: Request):
    """
    Generate a short-lived upload token for attaching payment proof photos.
    No auth required — doc code + verified PIN is sufficient.
    Called from the mobile ReceivePaymentPanel when a digital payment method is selected.

    Body: { pin }
    Returns: { token, session_id, upload_url }
    """
    pin = (data.get("pin") or "").strip()
    if not pin:
        raise HTTPException(status_code=400, detail="PIN is required")

    client_ip  = request.client.host if request.client else ""
    user_agent = request.headers.get("user-agent", "")

    doc_ref, doc_type, doc_id = await _resolve_doc(code)
    if doc_type != "invoice":
        raise HTTPException(status_code=400, detail="Upload proof is only available for invoices")

    # ── Lockout check ────────────────────────────────────────────────────────
    lockout = await check_qr_lockout(doc_ref["code"])
    if lockout["locked"]:
        raise HTTPException(status_code=429, detail={
            "message": "Too many failed attempts. This document is temporarily locked.",
            "retry_after": lockout["retry_after"],
            "locked": True,
        })

    invoice = await db.invoices.find_one({"id": doc_id}, {"_id": 0, "branch_id": 1})
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    from routes.verify import verify_pin_for_action
    verifier = await verify_pin_for_action(pin, "qr_receive_payment", branch_id=invoice.get("branch_id", ""))
    if not verifier:
        await log_failed_qr_pin_attempt(doc_ref["code"], doc_type, "generate_upload_token", client_ip, invoice.get("branch_id", ""))
        raise HTTPException(status_code=403, detail={
            "message": "Invalid PIN",
            "warn": lockout["warn"],
            "attempts_remaining": max(0, lockout["attempts_remaining"] - 1),
        })

    # Create an upload session pre-linked to this invoice
    import secrets as _secrets
    token = _secrets.token_urlsafe(24)
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()

    session_doc = {
        "id": new_id(),
        "token": token,
        "token_expires_at": expires_at,
        "record_type": "invoice",
        "record_id": doc_id,
        "is_pending": False,
        "org_id": doc_ref.get("org_id", ""),
        "files": [],
        "file_count": 0,
        "created_by": verifier["verifier_id"],
        "created_by_name": verifier["verifier_name"],
        "created_at": now_iso(),
        "purpose": "payment_proof",
    }
    await db.upload_sessions.insert_one(session_doc)

    return {
        "token": token,
        "session_id": session_doc["id"],
        "expires_at": expires_at,
    }




@router.post("/{code}/receive_payment")
async def receive_payment(code: str, data: dict, request: Request):
    """
    Record a payment on a credit/partial invoice via QR scan.

    **Terminal-only** (locked Feb 2026). Only Path 1 is accepted. The previous
    web-staff-JWT (Path 2) and TOTP-only (Path 3) paths were dropped because
    they allowed any admin with a regular phone camera to record payments —
    a cash-siphoning risk. Payments must now be entered through a paired
    AgriSmart terminal so every receive_payment carries a verifiable
    device-binding + PIN trail.

    Body: { amount, method, reference, payment_ref, terminal_id, device_id, pin }
    """
    terminal_id       = (data.get("terminal_id")    or "").strip()
    pin               = (data.get("pin")            or "").strip()
    amount            = float(data.get("amount", 0))
    method            = (data.get("method") or "Cash").strip()
    reference         = (data.get("reference") or "").strip()
    upload_session_id = (data.get("upload_session_id") or "").strip()
    payment_ref       = (data.get("payment_ref") or "").strip()
    client_ip         = request.client.host if request.client else ""
    user_agent        = request.headers.get("user-agent", "")

    # Hard gate: terminal session is mandatory. No more web/TOTP fallback.
    if not terminal_id:
        raise HTTPException(
            status_code=403,
            detail="Payments must be recorded from a paired AgriBooks terminal app. "
                   "Web/camera-scan recording is no longer permitted."
        )
    await _verify_terminal_session(terminal_id, data.get("device_id", ""))

    if amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be greater than zero")

    doc_ref, doc_type, doc_id = await _resolve_doc(code)
    if doc_type != "invoice":
        raise HTTPException(status_code=400, detail="This QR code is not for an invoice")

    # ── Lockout check ────────────────────────────────────────────────────────
    lockout = await check_qr_lockout(doc_ref["code"])
    if lockout["locked"]:
        raise HTTPException(status_code=429, detail={
            "message": "Too many failed attempts. This document is temporarily locked.",
            "retry_after": lockout["retry_after"],
            "locked": True,
        })

    invoice = await db.invoices.find_one({"id": doc_id}, {"_id": 0})
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if invoice.get("status") == "voided":
        raise HTTPException(status_code=400, detail="Cannot receive payment on a voided invoice")

    balance = float(invoice.get("balance", 0))
    if balance <= 0:
        raise HTTPException(status_code=400, detail="Invoice is already fully paid")
    if amount > balance + 0.01:
        raise HTTPException(status_code=400, detail=f"Amount ₱{amount:,.2f} exceeds balance ₱{balance:,.2f}")

    # ── Idempotency check ────────────────────────────────────────────────────
    if payment_ref:
        already = await db.qr_action_log.find_one(
            {"doc_id": doc_id, "action": "receive_payment",
             "release_ref": payment_ref, "result": "success"},
            {"_id": 0},
        )
        if already:
            raise HTTPException(status_code=409, detail="This payment was already processed")

    branch_id = invoice.get("branch_id", "")

    # ── Verify PIN against the active terminal session ────────────────────
    # Web/TOTP paths were removed Feb 2026 — terminal+PIN is the only flow.
    if not pin:
        raise HTTPException(status_code=400, detail="PIN is required")
    from routes.verify import verify_pin_for_action
    verifier = await verify_pin_for_action(pin, "qr_receive_payment", branch_id=branch_id)
    if not verifier:
        await log_failed_qr_pin_attempt(doc_ref["code"], doc_type, "receive_payment", client_ip, branch_id, terminal_id=terminal_id)
        await _log_action(doc_ref, "receive_payment", None, f"₱{amount:,.2f} PIN failed",
                          result="failed", error="invalid_pin", client_ip=client_ip, user_agent=user_agent)
        raise HTTPException(status_code=403, detail={
            "message": "Invalid PIN",
            "warn": lockout["warn"],
            "attempts_remaining": max(0, lockout["attempts_remaining"] - 1),
        })
    await log_successful_qr_pin_attempt(doc_ref["code"], doc_type, "receive_payment", verifier["verifier_name"], client_ip)

    # Build payment record — same schema as invoices.payments[]
    interest_owed      = float(invoice.get("interest_accrued", 0)) + float(invoice.get("penalties", 0))
    applied_interest   = min(amount, interest_owed)
    applied_principal  = amount - applied_interest
    new_interest       = max(0, round(float(invoice.get("interest_accrued", 0)) - applied_interest, 2))
    new_balance        = round(balance - amount, 2)
    new_paid           = round(float(invoice.get("amount_paid", 0)) + amount, 2)
    new_status         = "paid" if new_balance <= 0 else "partial"

    payment = {
        "id":                    new_id(),
        "amount":                amount,
        "date":                  await today_local(invoice.get("organization_id") or ""),
        "method":                method,
        "fund_source":           "digital" if is_digital_payment(method) else "cashier",
        "reference":             reference,
        "applied_to_interest":   applied_interest,
        "applied_to_principal":  applied_principal,
        "recorded_by":           verifier["verifier_name"],
        "recorded_at":           now_iso(),
    }

    await db.invoices.update_one({"id": doc_id}, {
        "$set": {
            "balance":          max(0, new_balance),
            "amount_paid":      new_paid,
            "interest_accrued": new_interest,
            "status":           new_status,
        },
        "$push": {"payments": payment}
    })

    # Route to correct wallet (same logic as record_invoice_payment)
    ref_text = f"QR Payment — {invoice.get('invoice_number', '')} · {invoice.get('customer_name', 'Walk-in')}"
    if is_digital_payment(method):
        await update_digital_wallet(branch_id, amount, ref_text, platform=method, ref_number=reference)
    else:
        await update_cashier_wallet(branch_id, amount, ref_text)

    # Update customer AR balance
    if invoice.get("customer_id"):
        await db.customers.update_one({"id": invoice["customer_id"]}, {"$inc": {"balance": -amount}})

    # ── Gap 3 fix: Double-entry journal record ───────────────────────────────
    debit_account  = "Digital Receipts" if is_digital_payment(method) else "Cash on Hand"
    je = {
        "id":          new_id(),
        "date":        await today_local(invoice.get("organization_id") or ""),
        "description": f"QR Payment — {invoice.get('invoice_number', '')} | {invoice.get('customer_name', 'Walk-in')} | {method}",
        "lines": [
            {"account": debit_account,       "debit": amount, "credit": 0},
            {"account": "Accounts Receivable", "debit": 0,      "credit": amount},
        ],
        "auto":        True,
        "source":      "qr_receive_payment",
        "source_ref":  invoice.get("invoice_number", ""),
        "branch_id":   branch_id,
        "created_by":  verifier["verifier_name"],
        "created_at":  now_iso(),
    }
    await db.journal_entries.insert_one(je)
    del je["_id"]

    await _log_action(doc_ref, "receive_payment", verifier, f"₱{amount:,.2f} {method}",
                      release_ref=payment_ref, client_ip=client_ip, user_agent=user_agent)

    # Link upload session if a payment proof was attached
    if upload_session_id:
        await db.upload_sessions.update_one(
            {"id": upload_session_id, "record_type": "invoice", "record_id": doc_id},
            {"$set": {"is_pending": False, "linked_at": now_iso(), "linked_payment_ref": reference}}
        )

    # SMS notification to customer (same hook as standard accounting payment)
    if invoice.get("customer_id"):
        try:
            from routes.sms_hooks import on_payment_received
            # Iter 244 audit fix: compute next-due info so the customer SMS
            # includes a concrete reminder (matches accounting.py behaviour).
            next_inv = await db.invoices.find_one(
                {"customer_id": invoice["customer_id"],
                 "status": {"$nin": ["voided", "paid"]},
                 "balance": {"$gt": 0},
                 "due_date": {"$ne": None}},
                {"_id": 0, "due_date": 1, "invoice_number": 1},
                sort=[("due_date", 1)],
            )
            next_due_info = (
                f"Next due: {next_inv['due_date']} ({next_inv.get('invoice_number','')}). "
                if next_inv else ""
            )
            await on_payment_received(
                customer_id=invoice["customer_id"],
                amount_paid=amount,
                remaining_balance=max(0, new_balance),
                branch_id=branch_id,
                next_due_info=next_due_info,
                invoice_number=invoice.get("invoice_number", ""),
            )
        except Exception:
            pass  # Non-critical — payment is already recorded

    return {
        "success":        True,
        "invoice_number": invoice.get("invoice_number"),
        "amount_received": amount,
        "new_balance":    max(0, new_balance),
        "new_status":     new_status,
        "payment":        {k: v for k, v in payment.items()},
        "authorized_by":  verifier["verifier_name"],
        "message": "Invoice fully paid!" if new_status == "paid" else f"Payment recorded. Remaining balance: ₱{max(0, new_balance):,.2f}",
    }


# ── Action: Transfer Receive ──────────────────────────────────────────────────

@router.post("/{code}/transfer_receive")
async def qr_transfer_receive(code: str, data: dict, request: Request):
    """
    Receive a branch transfer via QR scan.
    PIN required (qr_transfer_receive policy — restricted to dest branch managers).
    Terminal required.

    Body: { pin, items: [{product_id, qty_received}], notes, transfer_ref (idempotency UUID), terminal_id }
    """
    await _verify_terminal_session(data.get("terminal_id", ""), data.get("device_id", ""))

    pin          = (data.get("pin") or "").strip()
    items_input  = data.get("items", [])
    notes        = (data.get("notes") or "").strip()
    transfer_ref = (data.get("transfer_ref") or "").strip()   # idempotency key
    client_ip    = request.client.host if request.client else ""
    user_agent   = request.headers.get("user-agent", "")

    if not pin:
        raise HTTPException(status_code=400, detail="PIN is required")
    if not items_input:
        raise HTTPException(status_code=400, detail="No items provided")

    doc_ref, doc_type, doc_id = await _resolve_doc(code)
    if doc_type != "branch_transfer":
        raise HTTPException(status_code=400, detail="This QR code is not for a branch transfer")

    # ── Lockout check ────────────────────────────────────────────────────────
    lockout = await check_qr_lockout(doc_ref["code"])
    if lockout["locked"]:
        raise HTTPException(status_code=429, detail={
            "message": "Too many failed attempts. This document is temporarily locked.",
            "retry_after": lockout["retry_after"],
            "locked": True,
        })

    transfer = await db.branch_transfer_orders.find_one({"id": doc_id}, {"_id": 0})
    if not transfer:
        raise HTTPException(status_code=404, detail="Transfer not found")
    if transfer.get("status") != "sent":
        status_label = transfer.get("status", "?").replace("_", " ").title()
        raise HTTPException(status_code=400, detail=f"Transfer cannot be received — current status: {status_label}")

    # ── Idempotency check ────────────────────────────────────────────────────
    if transfer_ref:
        already = await db.qr_action_log.find_one(
            {"doc_id": doc_id, "action": "transfer_receive",
             "release_ref": transfer_ref, "result": "success"},
            {"_id": 0},
        )
        if already:
            raise HTTPException(status_code=409, detail="This transfer receive was already processed")

    from routes.verify import verify_pin_for_action
    verifier = await verify_pin_for_action(pin, "qr_transfer_receive", branch_id=transfer.get("to_branch_id", ""))
    if not verifier:
        await log_failed_qr_pin_attempt(doc_ref["code"], doc_type, "transfer_receive", client_ip, transfer.get("to_branch_id", ""), terminal_id=data.get("terminal_id", ""))
        await _log_action(doc_ref, "transfer_receive", None, "PIN failed",
                          result="failed", error="invalid_pin", client_ip=client_ip, user_agent=user_agent)
        raise HTTPException(status_code=403, detail={
            "message": "Invalid PIN",
            "warn": lockout["warn"],
            "attempts_remaining": max(0, lockout["attempts_remaining"] - 1),
        })
    await log_successful_qr_pin_attempt(doc_ref["code"], doc_type, "transfer_receive", verifier["verifier_name"], client_ip)

    # Synthetic user built from the PIN verifier — needed by receive_transfer()
    synthetic_user = {
        "id":        verifier["verifier_id"],
        "full_name": verifier["verifier_name"],
        "username":  verifier["verifier_name"],
        "branch_id": transfer.get("to_branch_id", ""),
        "role":      "manager",
    }

    from routes.branch_transfers import receive_transfer
    receive_data = {
        "items":              items_input,
        "notes":              notes,
        "skip_receipt_check": True,
    }

    result = await receive_transfer(doc_id, receive_data, synthetic_user)

    item_summary = ", ".join(
        f"{i.get('product_id', '?')} ×{i.get('qty_received', 0)}" for i in items_input[:3]
    )
    await _log_action(doc_ref, "transfer_receive", verifier, item_summary,
                      release_ref=transfer_ref, client_ip=client_ip, user_agent=user_agent)

    return result




async def process_expired_reservations():
    """
    Daily job: return unreleased reserved stock to available inventory after 30 days.
    Logs an expiry_return movement and notifies branch managers.
    """
    from utils import log_movement  # already top-level, kept for clarity
    now_str = datetime.now(timezone.utc).isoformat()

    expired = await db.sale_reservations.find(
        {"qty_remaining": {"$gt": 0}, "expires_at": {"$lt": now_str}},
        {"_id": 0}
    ).to_list(1000)

    # Group by invoice
    from collections import defaultdict
    by_invoice = defaultdict(list)
    for r in expired:
        by_invoice[r["invoice_id"]].append(r)

    for invoice_id, res_list in by_invoice.items():
        invoice = await db.invoices.find_one({"id": invoice_id}, {"_id": 0})
        if not invoice:
            continue
        branch_id = invoice.get("branch_id", "")
        inv_number = invoice.get("invoice_number", "")

        for res in res_list:
            qty_to_return = float(res["qty_remaining"])
            if qty_to_return <= 0:
                continue
            product_id = res["product_id"]

            # Move reserved_qty back to available quantity
            await db.inventory.update_one(
                {"product_id": product_id, "branch_id": branch_id},
                {"$inc": {"quantity": qty_to_return, "reserved_qty": -qty_to_return},
                 "$set": {"updated_at": now_iso()}}
            )

            await log_movement(
                product_id, branch_id, "expiry_return", qty_to_return,
                invoice_id, inv_number, 0, "system", "System (Auto-Expiry)",
                f"30-day reservation expired — {qty_to_return} units returned to stock. Invoice: {inv_number}"
            )

            # Clear the reservation
            await db.sale_reservations.update_one(
                {"invoice_id": invoice_id, "sold_product_id": res["sold_product_id"]},
                {"$set": {"qty_remaining": 0, "sold_qty_remaining": 0, "expired": True, "expired_at": now_str}}
            )

        # Update invoice status
        await db.invoices.update_one(
            {"id": invoice_id},
            {"$set": {"stock_release_status": "expired"}}
        )

        # Notify branch manager
        managers = await db.users.find(
            {"branch_id": branch_id, "role": {"$in": ["admin", "manager"]}, "active": True},
            {"_id": 0, "id": 1}
        ).to_list(20)
        admins = await db.users.find(
            {"role": "admin", "active": True}, {"_id": 0, "id": 1}
        ).to_list(20)
        notify_ids = list({u["id"] for u in managers + admins})
        total_returned = sum(float(r["qty_remaining"]) for r in res_list)
        if notify_ids:
            await db.notifications.insert_one({
                "id": new_id(),
                "type": "reservation_expired",
                "title": f"Reserved Stock Expired — {inv_number}",
                "message": (
                    f"{total_returned:.2f} units from {inv_number} (customer: {invoice.get('customer_name', 'Walk-in')}) "
                    f"were not picked up within 30 days and have been returned to available stock."
                ),
                "branch_id": branch_id,
                "metadata": {"invoice_id": invoice_id, "invoice_number": inv_number, "qty_returned": total_returned},
                "target_user_ids": notify_ids,
                "read_by": [],
                "created_at": now_iso(),
            })


# ── Action: Update Draft Order Quantities via QR ──────────────────────────────

@router.post("/{code}/update_draft")
async def update_draft_quantities(code: str, data: dict, request: Request):
    """
    Adjust item quantities on a for_preparation draft order via QR scan.
    PIN required (manager PIN = branch-scoped; admin PIN + TOTP = any branch).

    Lockout policy (stricter than global QR lockout):
      - 3 wrong PINs → 3-minute lock (rolling window)
      - 6 total lifetime wrong PINs → qr_edit_disabled = True (web-only editing)

    Body:
      pin: str
      items: [{ product_id, actual_qty }]   — only items being changed
    """
    from utils.security import (
        check_draft_qr_lockout, DRAFT_QR_DISABLE_TOTAL,
    )
    from routes.notifications import create_notification

    code_norm = code.strip().upper()
    client_ip  = request.client.host if request.client else ""
    pin        = (data.get("pin") or "").strip()
    new_items  = data.get("items") or []

    if not pin:
        raise HTTPException(status_code=400, detail="PIN is required")

    # ── Resolve doc & set tenant context ──────────────────────────────────────
    doc_ref, doc_type, doc_id = await _resolve_doc(code_norm)

    if doc_type != "invoice":
        raise HTTPException(status_code=400, detail="QR update only available for draft orders")

    invoice = await db.invoices.find_one({"id": doc_id}, {"_id": 0})
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if invoice.get("status") != "for_preparation":
        raise HTTPException(status_code=400, detail="This order is no longer in preparation status")

    branch_id = invoice.get("branch_id", "")

    # ── QR editing permanently disabled? ─────────────────────────────────────
    if invoice.get("qr_edit_disabled"):
        raise HTTPException(
            status_code=403,
            detail={
                "message": "QR editing has been permanently disabled for this order due to multiple failed PIN attempts. Please edit via the web portal.",
                "permanently_disabled": True,
            },
        )

    # ── Draft-specific lockout check (3 attempts → 3 min) ─────────────────────
    lockout = await check_draft_qr_lockout(doc_ref["code"])
    if lockout.get("permanently_disabled"):
        # Lockout crossed the permanent threshold — disable now
        await db.invoices.update_one({"id": doc_id}, {"$set": {"qr_edit_disabled": True}})
        raise HTTPException(
            status_code=403,
            detail={
                "message": "QR editing has been permanently disabled due to too many failed attempts. Please use the web portal.",
                "permanently_disabled": True,
            },
        )
    if lockout["locked"]:
        raise HTTPException(
            status_code=429,
            detail={
                "message": f"Too many failed PIN attempts. Try again in {lockout['retry_after']} seconds.",
                "retry_after": lockout["retry_after"],
                "locked": True,
            },
        )

    # ── PIN verification (branch-scoped for manager PIN) ───────────────────────
    from routes.verify import verify_pin_for_action
    verifier = await verify_pin_for_action(pin, "qr_release_stocks", branch_id=branch_id)
    if not verifier:
        # Log failure with action="update_draft" for the draft-specific counter
        await db.pin_attempt_log.insert_one({
            "doc_code":     doc_ref["code"],
            "doc_type":     "invoice",
            "action":       "update_draft",
            "success":      False,
            "client_ip":    client_ip,
            "branch_id":    branch_id,
            "attempted_at": now_iso(),
        })

        # Re-check to give accurate remaining count and detect permanent disable
        fresh_lockout = await check_draft_qr_lockout(doc_ref["code"])
        if fresh_lockout.get("permanently_disabled"):
            await db.invoices.update_one({"id": doc_id}, {"$set": {"qr_edit_disabled": True}})
            raise HTTPException(
                status_code=403,
                detail={
                    "message": "QR editing permanently disabled after too many failed attempts. Edit via web portal.",
                    "permanently_disabled": True,
                },
            )

        msg = "Invalid PIN"
        if fresh_lockout["locked"]:
            msg = f"Invalid PIN — document locked for {fresh_lockout['retry_after']} seconds."
            raise HTTPException(
                status_code=429,
                detail={"message": msg, "retry_after": fresh_lockout["retry_after"], "locked": True},
            )
        raise HTTPException(
            status_code=403,
            detail={
                "message": msg,
                "attempts_remaining": fresh_lockout["attempts_remaining"],
                "warn": fresh_lockout["warn"],
            },
        )

    # ── PIN passed — log success ───────────────────────────────────────────────
    await db.pin_attempt_log.insert_one({
        "doc_code":       doc_ref["code"],
        "doc_type":       "invoice",
        "action":         "update_draft",
        "success":        True,
        "verifier_name":  verifier.get("verifier_name", ""),
        "client_ip":      client_ip,
        "branch_id":      branch_id,
        "attempted_at":   now_iso(),
    })

    # ── Apply quantity updates ─────────────────────────────────────────────────
    existing_items = invoice.get("items") or []
    qty_map = {row["product_id"]: float(row.get("actual_qty", 0)) for row in new_items if row.get("product_id")}

    updated_items = []
    for item in existing_items:
        pid = item.get("product_id", "")
        if pid in qty_map:
            new_qty = qty_map[pid]
            rate = float(item.get("rate") or item.get("unit_price") or item.get("price") or 0)
            disc = float(item.get("discount_amount") or 0)
            new_total = max(0, new_qty * rate - disc)
            updated_items.append({**item, "quantity": new_qty, "total": new_total})
        else:
            updated_items.append(item)

    # Recalculate totals
    new_subtotal    = sum(float(i.get("total", 0)) for i in updated_items)
    freight         = float(invoice.get("freight") or 0)
    overall_disc    = float(invoice.get("overall_discount") or 0)
    new_grand_total = max(0, new_subtotal + freight - overall_disc)
    updated_at      = now_iso()

    await db.invoices.update_one(
        {"id": doc_id},
        {"$set": {
            "items":            updated_items,
            "subtotal":         new_subtotal,
            "grand_total":      new_grand_total,
            "balance":          new_grand_total,
            "updated_at":       updated_at,
            "last_qr_edit_by":  verifier.get("verifier_name", ""),
            "last_qr_edit_at":  updated_at,
        }},
    )

    # ── In-app notification → cashier who created the draft ───────────────────
    cashier_id   = invoice.get("cashier_id", "")
    inv_number   = invoice.get("invoice_number", "")
    verifier_name = verifier.get("verifier_name", "")
    org_ctx = get_org_context()
    if cashier_id and org_ctx:
        try:
            await create_notification(
                org_id=org_ctx,
                type="draft_updated_via_qr",
                title="Draft Order Updated via QR",
                message=(
                    f"Draft {inv_number} quantities were adjusted by "
                    f"{verifier_name} via QR scan at {updated_at[:16].replace('T', ' ')} UTC."
                ),
                branch_id=branch_id,
                data={
                    "invoice_id":     doc_id,
                    "invoice_number": inv_number,
                    "updated_by":     verifier_name,
                    "updated_at":     updated_at,
                },
                target_user_ids=[cashier_id],
            )
        except Exception:
            pass  # Notification failure must not block the save

    return {
        "ok": True,
        "invoice_number": inv_number,
        "updated_by":     verifier_name,
        "updated_at":     updated_at,
        "new_grand_total": new_grand_total,
        "items":          updated_items,
    }

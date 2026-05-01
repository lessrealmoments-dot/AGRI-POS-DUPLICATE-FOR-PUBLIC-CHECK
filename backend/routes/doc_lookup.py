"""
Document Lookup routes.
Generates unique document codes for printed receipts and provides
PIN-protected lookup for scanning QR codes or entering codes manually.
"""
from fastapi import APIRouter, HTTPException, Depends
from config import db, _raw_db, set_org_context, get_org_context
from utils import get_current_user, now_iso, new_id
import string
import secrets


# ── Tenant-context helper for public QR scan endpoints ──────────────────────
_DOC_TYPE_TO_COLLECTION = {
    "invoice": "invoices",
    "purchase_order": "purchase_orders",
    "branch_transfer": "branch_transfer_orders",
}


async def _resolve_doc_code_with_context(code: str):
    """
    Resolve a doc_code → (doc_ref) AND set the org context for downstream
    tenant-scoped queries. QR scans hit public endpoints with no JWT, so the
    fail-closed tenant proxy would otherwise return zero results from
    `db.invoices`, `db.customers`, etc.

    1. Look up doc_code (collection is NOT tenant-scoped).
    2. If org_id present on doc_ref → set context directly.
    3. If org_id missing/empty (legacy data) → fall back to reading the
       referenced document via _raw_db, derive org_id from it, set context,
       and backfill the doc_codes entry.
    """
    code_norm = (code or "").strip().upper()
    doc_ref = await db.doc_codes.find_one({"code": code_norm}, {"_id": 0})
    if not doc_ref:
        raise HTTPException(status_code=404, detail="Document not found")

    org_id = (doc_ref.get("org_id") or "").strip()
    if not org_id:
        # Legacy doc_code without org_id — recover from the referenced document.
        coll_name = _DOC_TYPE_TO_COLLECTION.get(doc_ref.get("doc_type", ""))
        if coll_name:
            doc = await _raw_db[coll_name].find_one(
                {"id": doc_ref.get("doc_id", "")},
                {"_id": 0, "organization_id": 1},
            )
            if doc and doc.get("organization_id"):
                org_id = doc["organization_id"]
                # Backfill so future reads are fast.
                try:
                    await _raw_db.doc_codes.update_one(
                        {"code": code_norm}, {"$set": {"org_id": org_id}}
                    )
                except Exception:
                    pass

    if org_id:
        set_org_context(org_id)

    return doc_ref

router = APIRouter(prefix="/doc", tags=["Document Lookup"])


def _generate_doc_code(length=8):
    """Generate a unique alphanumeric code (uppercase + digits, no ambiguous chars)."""
    alphabet = string.ascii_uppercase.replace('O', '').replace('I', '') + string.digits.replace('0', '')
    return ''.join(secrets.choice(alphabet) for _ in range(length))


async def _ensure_unique_code():
    """Generate a code and ensure it doesn't already exist."""
    for _ in range(10):
        code = _generate_doc_code()
        existing = await db.doc_codes.find_one({"code": code}, {"_id": 0, "code": 1})
        if not existing:
            return code
    raise HTTPException(status_code=500, detail="Could not generate unique code")




async def auto_generate_doc_code(doc_type: str, doc_id: str, org_id: str = "", created_by: str = "") -> str:
    """
    Shared helper: generate and store a doc code at document creation time.
    Idempotent — returns existing code if one already exists.
    Updates the document's own doc_code field.
    """
    existing = await db.doc_codes.find_one({"doc_type": doc_type, "doc_id": doc_id}, {"_id": 0})
    if existing:
        return existing["code"]

    code = await _ensure_unique_code()
    await db.doc_codes.insert_one({
        "id": new_id(),
        "code": code,
        "doc_type": doc_type,
        "doc_id": doc_id,
        "org_id": org_id,
        "created_at": now_iso(),
        "created_by": created_by,
    })
    collection_map = {
        "invoice": "invoices",
        "purchase_order": "purchase_orders",
        "branch_transfer": "branch_transfer_orders",
    }
    coll_name = collection_map.get(doc_type)
    if coll_name:
        await db[coll_name].update_one({"id": doc_id}, {"$set": {"doc_code": code}})
    return code


@router.post("/generate-code")
async def generate_doc_code(data: dict, user=Depends(get_current_user)):
    """
    Generate a unique document code for a document (invoice, PO, branch transfer).
    If one already exists, return it.
    Body: { doc_type: "invoice"|"purchase_order"|"branch_transfer", doc_id: "..." }
    """
    doc_type = data.get("doc_type", "")
    doc_id = data.get("doc_id", "")
    if not doc_type or not doc_id:
        raise HTTPException(status_code=400, detail="doc_type and doc_id required")

    # Check if code already exists for this document
    existing = await db.doc_codes.find_one(
        {"doc_type": doc_type, "doc_id": doc_id}, {"_id": 0}
    )
    if existing:
        return {"code": existing["code"], "doc_type": doc_type, "doc_id": doc_id}

    code = await _ensure_unique_code()
    await db.doc_codes.insert_one({
        "id": new_id(),
        "code": code,
        "doc_type": doc_type,
        "doc_id": doc_id,
        "org_id": user.get("org_id", ""),
        "created_at": now_iso(),
        "created_by": user.get("id", ""),
    })

    # Also store the code on the document itself for quick access
    collection_map = {
        "invoice": "invoices",
        "purchase_order": "purchase_orders",
        "branch_transfer": "branch_transfer_orders",
    }
    coll_name = collection_map.get(doc_type)
    if coll_name:
        coll = db[coll_name]
        await coll.update_one({"id": doc_id}, {"$set": {"doc_code": code}})

    return {"code": code, "doc_type": doc_type, "doc_id": doc_id}


@router.post("/lookup")
async def lookup_document(data: dict):
    """
    PIN-protected document lookup by code — returns FULL details.
    Body: { code: "ABC12345", pin: "521325" }
    """
    code = (data.get("code") or "").strip().upper()
    pin = (data.get("pin") or "").strip()

    if not code:
        raise HTTPException(status_code=400, detail="Document code required")
    if not pin:
        raise HTTPException(status_code=400, detail="PIN required")

    # Brute-force lockout (same policy as QR actions)
    from utils.security import check_qr_lockout, log_failed_qr_pin_attempt, log_successful_qr_pin_attempt
    lockout = await check_qr_lockout(code)
    if lockout["locked"]:
        raise HTTPException(
            status_code=429,
            detail=f"Too many failed attempts. Try again in {lockout['retry_after']} seconds.",
        )

    # Verify PIN
    from routes.verify import _resolve_pin
    verifier = await _resolve_pin(pin)
    if not verifier:
        await log_failed_qr_pin_attempt(code, "doc_lookup", "lookup")
        lockout_after = await check_qr_lockout(code)
        detail = "Invalid PIN"
        if lockout_after.get("warn"):
            detail += f" — {lockout_after['attempts_remaining']} attempt(s) remaining before lockout"
        raise HTTPException(status_code=403, detail=detail)

    await log_successful_qr_pin_attempt(code, "doc_lookup", "lookup", verifier.get("verifier_name", ""))

    # Find document code AND set tenant context (public endpoint — no JWT).
    doc_ref = await _resolve_doc_code_with_context(code)

    doc_type = doc_ref["doc_type"]
    doc_id = doc_ref["doc_id"]

    # Fetch document based on type
    if doc_type == "invoice":
        doc = await db.invoices.find_one({"id": doc_id}, {"_id": 0})
        if not doc:
            raise HTTPException(status_code=404, detail="Invoice not found")
        payments = doc.get("payments", [])
        customer = None
        if doc.get("customer_id"):
            customer = await db.customers.find_one(
                {"id": doc["customer_id"]}, {"_id": 0, "name": 1, "phone": 1, "address": 1, "balance": 1}
            )
        # Fetch attached files (payment proofs, receipts)
        uploads = await db.upload_sessions.find(
            {"record_type": "invoice", "record_id": doc_id, "is_pending": {"$ne": True}},
            {"_id": 0}
        ).to_list(20)
        files = []
        for u in uploads:
            session_files = await db.uploaded_files.find(
                {"session_id": u["id"]}, {"_id": 0}
            ).to_list(50)
            if session_files:
                files.extend(session_files)
            else:
                # Files stored inline on the session document (R2 flow)
                for f in u.get("files", []):
                    from utils.r2_storage import get_presigned_url
                    try:
                        url = await get_presigned_url(f["r2_key"])
                    except Exception:
                        url = ""
                    files.append({"id": f["id"], "filename": f.get("filename", ""), "url": url, "content_type": f.get("content_type", "image/jpeg")})
        return {
            "doc_type": "invoice",
            "document": doc,
            "payments": payments,
            "customer": customer,
            "attached_files": files,
            "verifier": verifier.get("verifier_name", ""),
            "verifier_method": verifier.get("method", ""),
        }

    elif doc_type == "purchase_order":
        doc = await db.purchase_orders.find_one({"id": doc_id}, {"_id": 0})
        if not doc:
            raise HTTPException(status_code=404, detail="PO not found")
        uploads = await db.upload_sessions.find(
            {"record_type": "purchase_order", "record_id": doc_id},
            {"_id": 0}
        ).to_list(20)
        files = []
        for u in uploads:
            session_files = await db.uploaded_files.find(
                {"session_id": u["id"]}, {"_id": 0}
            ).to_list(50)
            files.extend(session_files)
        return {
            "doc_type": "purchase_order",
            "document": doc,
            "attached_files": files,
            "verifier": verifier.get("verifier_name", ""),
        }

    elif doc_type == "branch_transfer":
        doc = await db.branch_transfer_orders.find_one({"id": doc_id}, {"_id": 0})
        if not doc:
            raise HTTPException(status_code=404, detail="Transfer not found")
        from_branch = await db.branches.find_one({"id": doc.get("from_branch_id")}, {"_id": 0, "name": 1})
        to_branch = await db.branches.find_one({"id": doc.get("to_branch_id")}, {"_id": 0, "name": 1})
        uploads = await db.upload_sessions.find(
            {"record_type": "branch_transfer", "record_id": doc_id},
            {"_id": 0}
        ).to_list(20)
        files = []
        for u in uploads:
            session_files = await db.uploaded_files.find(
                {"session_id": u["id"]}, {"_id": 0}
            ).to_list(50)
            files.extend(session_files)
        return {
            "doc_type": "branch_transfer",
            "document": doc,
            "from_branch_name": from_branch.get("name", "") if from_branch else "",
            "to_branch_name": to_branch.get("name", "") if to_branch else "",
            "attached_files": files,
            "verifier": verifier.get("verifier_name", ""),
        }

    raise HTTPException(status_code=400, detail=f"Unknown document type: {doc_type}")


@router.get("/view/{code}")
async def view_document_open(code: str):
    """
    Open (no PIN) document view — returns basic receipt info only.
    Sensitive data (payment history, attached files, internal notes) excluded.
    """
    code = code.strip().upper()
    # Tenant-context is set here so downstream `db.invoices`, `db.branches`, etc.
    # work even though the request has no JWT.
    doc_ref = await _resolve_doc_code_with_context(code)

    doc_type = doc_ref["doc_type"]
    doc_id = doc_ref["doc_id"]

    if doc_type == "invoice":
        doc = await db.invoices.find_one({"id": doc_id}, {"_id": 0})
        if not doc:
            raise HTTPException(status_code=404, detail="Invoice not found")
        balance = (doc.get("grand_total") or 0) - (doc.get("amount_paid") or 0)
        is_paid = balance <= 0 or doc.get("payment_status") == "paid"

        # Resolve branch name
        branch = await db.branches.find_one({"id": doc.get("branch_id", "")}, {"_id": 0, "name": 1})
        branch_name = branch.get("name", "") if branch else ""

        # Available actions based on state
        available_actions = []
        status = doc.get("status", "")
        if status != "voided":
            if doc.get("release_mode") == "partial" and doc.get("stock_release_status") not in ("fully_released", "expired"):
                available_actions.append("release_stocks")
            if balance > 0:
                available_actions.append("receive_payment")

        # Fetch reservations for partial-release invoices
        reservations = []
        if doc.get("release_mode") == "partial":
            reservations = await db.sale_reservations.find(
                {"invoice_id": doc_id}, {"_id": 0,
                 "sold_product_id": 1, "sold_product_name": 1,
                 "sold_qty_ordered": 1, "sold_qty_released": 1, "sold_qty_remaining": 1,
                 "sold_unit": 1, "qty_remaining": 1, "expires_at": 1}
            ).to_list(100)

        return {
            "doc_type": "invoice",
            "doc_id": doc_id,
            "number": doc.get("invoice_number", ""),
            "date": doc.get("created_at") or doc.get("order_date", ""),
            "order_date": doc.get("order_date", ""),
            "customer_name": doc.get("customer_name", "Walk-in"),
            "branch_id": doc.get("branch_id", ""),
            "branch_name": branch_name,
            "items": [{"name": i.get("product_name", ""), "qty": i.get("quantity", 0), "price": i.get("rate") or i.get("unit_price") or i.get("price", 0), "total": i.get("total", 0)} for i in (doc.get("items") or [])],
            "subtotal": doc.get("subtotal", 0),
            "discount": doc.get("overall_discount", 0),
            "grand_total": doc.get("grand_total", 0),
            "amount_paid": doc.get("amount_paid", 0),
            "balance": max(0, balance),
            "status": "Fully Paid" if is_paid else f"Balance: ₱{balance:,.2f}",
            "payment_status": doc.get("status", ""),
            "payment_method": doc.get("payment_method", "Cash"),
            "payment_type": doc.get("payment_type", "cash"),
            "customer_id": doc.get("customer_id", ""),
            "release_mode": doc.get("release_mode", "full"),
            "stock_release_status": doc.get("stock_release_status", "na"),
            "stock_releases": doc.get("stock_releases", []),
            "reservations": reservations,
            "available_actions": available_actions,
        }

    elif doc_type == "purchase_order":
        doc = await db.purchase_orders.find_one({"id": doc_id}, {"_id": 0})
        if not doc:
            raise HTTPException(status_code=404, detail="PO not found")
        branch = await db.branches.find_one({"id": doc.get("branch_id", "")}, {"_id": 0, "name": 1})
        branch_name = branch.get("name", "") if branch else ""
        po_status = doc.get("status", "")
        available_actions = []
        if po_status in ("ordered", "draft", "in_progress"):
            available_actions.append("po_receive")
        # received = view only
        return {
            "doc_type": "purchase_order",
            "doc_id": doc_id,
            "number": doc.get("po_number", ""),
            "date": doc.get("purchase_date", ""),
            "supplier_name": doc.get("vendor", ""),
            "branch_id": doc.get("branch_id", ""),
            "branch_name": branch_name,
            "items": [{"name": i.get("product_name") or i.get("description", ""), "qty": i.get("quantity", 0), "price": i.get("rate") or i.get("unit_price") or i.get("price", 0), "total": i.get("total", 0)} for i in (doc.get("items") or [])],
            "grand_total": doc.get("grand_total", 0),
            "status": (doc.get("status") or "").replace("_", " ").title(),
            "raw_status": po_status,
            "payment_status": doc.get("payment_status", "unpaid"),
            "available_actions": available_actions,
        }

    elif doc_type == "branch_transfer":
        doc = await db.branch_transfer_orders.find_one({"id": doc_id}, {"_id": 0})
        if not doc:
            raise HTTPException(status_code=404, detail="Transfer not found")
        from_branch = await db.branches.find_one({"id": doc.get("from_branch_id")}, {"_id": 0, "name": 1})
        to_branch = await db.branches.find_one({"id": doc.get("to_branch_id")}, {"_id": 0, "name": 1})
        status_labels = {"draft": "Draft", "sent": "In Transit", "sent_to_terminal": "On Terminal", "received_pending": "Pending Review", "received": "Completed", "disputed": "Disputed", "cancelled": "Cancelled"}
        transfer_status = doc.get("status", "")
        available_actions = []
        if transfer_status == "sent":
            available_actions.append("transfer_receive")
        return {
            "doc_type": "branch_transfer",
            "doc_id": doc_id,
            "number": doc.get("invoice_number") or doc.get("order_number", ""),
            "date": doc.get("created_at", ""),
            "from_branch": from_branch.get("name", "") if from_branch else "",
            "to_branch": to_branch.get("name", "") if to_branch else "",
            "items": [{"product_id": i.get("product_id", ""), "name": i.get("product_name", ""), "qty": i.get("qty", 0), "price": i.get("transfer_capital", 0), "total": (i.get("transfer_capital", 0) * i.get("qty", 0))} for i in (doc.get("items") or [])],
            "total": sum(i.get("transfer_capital", 0) * i.get("qty", 0) for i in (doc.get("items") or [])),
            "status": status_labels.get(transfer_status, transfer_status),
            "raw_status": transfer_status,
            "to_branch_id": doc.get("to_branch_id", ""),
            "available_actions": available_actions,
        }

    raise HTTPException(status_code=400, detail=f"Unknown document type: {doc_type}")


@router.get("/by-ref/{doc_type}/{doc_id}")
async def get_doc_code_by_ref(doc_type: str, doc_id: str, user=Depends(get_current_user)):
    """Get existing doc code for a document (no PIN needed, just auth)."""
    doc_ref = await db.doc_codes.find_one(
        {"doc_type": doc_type, "doc_id": doc_id}, {"_id": 0}
    )
    if doc_ref:
        return {"code": doc_ref["code"]}
    return {"code": None}


@router.get("/search")
async def search_documents(q: str = "", branch_id: str = "", user=Depends(get_current_user)):
    """
    Search for documents by invoice number, PO number, or transfer order number.
    Branch-scoped: only returns results from the specified branch (or transfers involving it).
    Used by the terminal smart scanner to resolve human-readable numbers to doc codes.
    """
    if not q or not branch_id:
        raise HTTPException(status_code=400, detail="q and branch_id are required")

    q_stripped = q.strip()
    results = []

    # Search invoices by invoice_number
    invoices = await db.invoices.find(
        {"branch_id": branch_id, "invoice_number": {"$regex": q_stripped, "$options": "i"}},
        {"_id": 0, "id": 1, "invoice_number": 1, "doc_code": 1,
         "customer_name": 1, "grand_total": 1, "status": 1}
    ).sort("created_at", -1).limit(5).to_list(5)

    for inv in invoices:
        if inv.get("doc_code"):
            results.append({
                "doc_type": "invoice",
                "number": inv.get("invoice_number", ""),
                "doc_code": inv["doc_code"],
                "label": inv.get("customer_name", "Walk-in"),
                "status": inv.get("status", ""),
                "amount": inv.get("grand_total", 0),
            })

    # Search purchase orders by po_number
    pos = await db.purchase_orders.find(
        {"branch_id": branch_id, "po_number": {"$regex": q_stripped, "$options": "i"}},
        {"_id": 0, "id": 1, "po_number": 1, "doc_code": 1,
         "vendor": 1, "grand_total": 1, "status": 1}
    ).sort("created_at", -1).limit(5).to_list(5)

    for po in pos:
        if po.get("doc_code"):
            results.append({
                "doc_type": "purchase_order",
                "number": po.get("po_number", ""),
                "doc_code": po["doc_code"],
                "label": po.get("vendor", ""),
                "status": po.get("status", ""),
                "amount": po.get("grand_total", 0),
            })

    # Search branch transfers by order_number or invoice_number (from or to this branch)
    transfers = await db.branch_transfer_orders.find(
        {
            "$and": [
                {"$or": [{"from_branch_id": branch_id}, {"to_branch_id": branch_id}]},
                {"$or": [
                    {"order_number": {"$regex": q_stripped, "$options": "i"}},
                    {"invoice_number": {"$regex": q_stripped, "$options": "i"}},
                ]},
            ]
        },
        {"_id": 0, "id": 1, "order_number": 1, "invoice_number": 1,
         "doc_code": 1, "from_branch_id": 1, "to_branch_id": 1, "status": 1}
    ).sort("created_at", -1).limit(5).to_list(5)

    for t in transfers:
        if t.get("doc_code"):
            number = t.get("order_number") or t.get("invoice_number", "")
            results.append({
                "doc_type": "branch_transfer",
                "number": number,
                "doc_code": t["doc_code"],
                "label": f"Transfer {number}",
                "status": t.get("status", ""),
                "amount": 0,
            })

    return {"results": results, "total": len(results)}

"""
Receipt Upload routes: QR-based photo upload linked to any record.
Supports: purchase_order, expense, payment, return, branch_transfer, inventory_correction.
Files stored in Cloudflare R2 (multi-tenant, pre-signed URLs).
"""
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from typing import Optional, List
from datetime import datetime, timezone, timedelta
from pathlib import Path
import secrets
from config import db, _raw_db, get_org_context, set_org_context
from utils import get_current_user, now_iso, new_id
from utils.r2_storage import upload_file, get_presigned_url, delete_file, build_key

router = APIRouter(prefix="/uploads", tags=["Uploads"])

MAX_FILES = 10

RECORD_TYPE_LABELS = {
    "purchase_order": "Purchase Order",
    "expense": "Expense",
    "payment": "Supplier Payment",
    "return": "Customer Return",
    "branch_transfer": "Branch Transfer",
    "inventory_correction": "Inventory Correction",
    "invoice": "Sales Invoice",
}


async def _get_record_summary(record_type: str, record_id: str) -> dict:
    """Fetch a brief summary of the linked record for display on the upload page."""
    summary = {"type_label": RECORD_TYPE_LABELS.get(record_type, record_type), "record_id": record_id}
    try:
        if record_type == "purchase_order":
            po = await db.purchase_orders.find_one({"id": record_id}, {"_id": 0})
            if po:
                summary.update({
                    "title": po.get("po_number", ""),
                    "description": f"{po.get('vendor', '')} · DR: {po.get('dr_number', 'N/A')}",
                    "amount": po.get("grand_total") or po.get("subtotal", 0),
                    "date": po.get("purchase_date", ""),
                    "status": po.get("status", ""),
                })
        elif record_type == "expense":
            exp = await db.expenses.find_one({"id": record_id}, {"_id": 0})
            if exp:
                summary.update({
                    "title": exp.get("description", "Expense"),
                    "description": f"{exp.get('category', '')} · {exp.get('payment_method', '')}",
                    "amount": exp.get("amount", 0),
                    "date": exp.get("date", ""),
                    "reference": exp.get("reference_number", ""),
                })
        elif record_type == "payment":
            po = await db.purchase_orders.find_one({"id": record_id}, {"_id": 0})
            if po:
                summary.update({
                    "title": f"Payment — {po.get('vendor', '')}",
                    "description": f"{po.get('po_number', '')} · {po.get('payment_method_detail', 'Cash')}",
                    "amount": po.get("amount_paid", 0),
                    "date": po.get("purchase_date", ""),
                })
        elif record_type == "return":
            ret = await db.returns.find_one({"id": record_id}, {"_id": 0})
            if ret:
                summary.update({
                    "title": ret.get("rma_number", "Return"),
                    "description": f"{ret.get('customer_name', '')} · {ret.get('reason', '')}",
                    "amount": ret.get("refund_amount", 0),
                    "date": ret.get("return_date", ""),
                })
        elif record_type == "branch_transfer":
            bt = await db.branch_transfer_orders.find_one({"id": record_id}, {"_id": 0})
            if bt:
                from_b = await db.branches.find_one({"id": bt.get("from_branch_id")}, {"_id": 0, "name": 1})
                to_b = await db.branches.find_one({"id": bt.get("to_branch_id")}, {"_id": 0, "name": 1})
                summary.update({
                    "title": bt.get("order_number", "Transfer"),
                    "description": f"{from_b.get('name', '') if from_b else ''} → {to_b.get('name', '') if to_b else ''}",
                    "amount": bt.get("total_at_transfer_capital", 0),
                    "date": bt.get("created_at", "")[:10],
                })
    except Exception:
        pass
    return summary


def _get_org_id() -> str:
    """Get current org_id from context, fallback to 'default'."""
    return get_org_context() or "default"


# ─────────────────────────────────────────────────────────────────────────────
#  Generate upload link
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/generate-link")
async def generate_upload_link(data: dict, user=Depends(get_current_user)):
    """Generate a 1-hour upload link and QR for a record."""
    record_type = data.get("record_type", "")
    record_id = data.get("record_id", "")
    if not record_type or not record_id:
        raise HTTPException(status_code=400, detail="record_type and record_id required")

    token = secrets.token_urlsafe(24)
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    summary = await _get_record_summary(record_type, record_id)

    doc = {
        "id": new_id(),
        "token": token,
        "token_expires_at": expires_at,
        "record_type": record_type,
        "record_id": record_id,
        "record_summary": summary,
        "org_id": _get_org_id(),
        "files": [],
        "file_count": 0,
        "created_by": user["id"],
        "created_by_name": user.get("full_name", user["username"]),
        "created_at": now_iso(),
    }
    await db.upload_sessions.insert_one(doc)
    del doc["_id"]
    return {"token": token, "expires_at": expires_at, "record_summary": summary}


# ─────────────────────────────────────────────────────────────────────────────
#  Public: get record summary for the upload page (no auth, token only)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/preview/{token}")
async def get_upload_preview(token: str):
    """Public endpoint — shows record summary for phone upload page.
    
    Uses _raw_db for the token lookup because public endpoints have no auth and
    therefore no org_context — the scoped proxy would fail-closed and return
    nothing. Once we identify the org from the session doc, we set the context
    so any downstream scoped queries (e.g. record summary) work correctly.
    """
    session = await _raw_db.upload_sessions.find_one({"token": token}, {"_id": 0})
    if not session:
        raise HTTPException(status_code=404, detail="Upload link not found")

    # Set org context from the session so any scoped queries downstream work
    org_id = session.get("organization_id") or session.get("org_id")
    if org_id:
        set_org_context(org_id)

    expires_at = session.get("token_expires_at", "")
    if expires_at:
        try:
            exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if exp < datetime.now(timezone.utc):
                raise HTTPException(status_code=410, detail="Upload link has expired. Ask the branch to generate a new link.")
        except ValueError:
            pass

    return {
        "record_summary": session.get("record_summary", {}),
        "file_count": session.get("file_count", 0),
        "max_files": MAX_FILES,
        "expired": False,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Public: upload files via token (no auth, token only) → R2
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/upload/{token}")
async def upload_files_via_token(
    token: str,
    files: List[UploadFile] = File(...),
):
    """Public endpoint — accepts photo uploads from mobile. Stores in R2.
    
    Uses _raw_db for the initial token lookup (public, no auth). Sets org
    context from the session so subsequent scoped writes (e.g. invoice
    receipt_status update) target the correct tenant.
    """
    session = await _raw_db.upload_sessions.find_one({"token": token}, {"_id": 0})
    if not session:
        raise HTTPException(status_code=404, detail="Upload link not found")

    # Set org context from the session so all subsequent scoped queries work
    org_id = session.get("organization_id") or session.get("org_id")
    if org_id:
        set_org_context(org_id)

    expires_at = session.get("token_expires_at", "")
    if expires_at:
        try:
            exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if exp < datetime.now(timezone.utc):
                raise HTTPException(status_code=410, detail="Upload link has expired")
        except ValueError:
            pass

    current_count = session.get("file_count", 0)
    if current_count + len(files) > MAX_FILES:
        raise HTTPException(status_code=400, detail=f"Maximum {MAX_FILES} files allowed. Already has {current_count}.")

    org_id = session.get("org_id", "default")
    record_type = session["record_type"]
    record_id = session["record_id"]

    saved = []
    for file in files:
        file_id = new_id()
        ext = Path(file.filename).suffix if file.filename else ".jpg"
        safe_ext = ext.lower() if ext.lower() in (".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".pdf") else ".jpg"
        filename = f"{file_id}{safe_ext}"
        content_type = file.content_type or "image/jpeg"

        content = await file.read()
        r2_result = await upload_file(org_id, record_type, record_id, filename, content, content_type)

        saved.append({
            "id": file_id,
            "filename": file.filename or filename,
            "r2_key": r2_result["key"],
            "content_type": content_type,
            "size": len(content),
            "uploaded_at": now_iso(),
        })

    await db.upload_sessions.update_one(
        {"token": token},
        {"$push": {"files": {"$each": saved}}, "$inc": {"file_count": len(saved)}}
    )

    # Auto-update receipt_status on the linked invoice (for phone uploads via QR)
    new_total = current_count + len(saved)
    if record_type == "invoice" and record_id and new_total > 0:
        await db.invoices.update_one(
            {"id": record_id, "receipt_status": "pending"},
            {"$set": {"receipt_status": "uploaded"}}
        )

    return {"uploaded": len(saved), "total_files": new_total}


# ─────────────────────────────────────────────────────────────────────────────
#  Get uploads for a record (auth required)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/record/{record_type}/{record_id}")
async def get_record_uploads(
    record_type: str, record_id: str, user=Depends(get_current_user),
):
    """Get all upload sessions for a record with pre-signed URLs for each file."""
    sessions = await db.upload_sessions.find(
        {"record_type": record_type, "record_id": record_id},
        {"_id": 0, "token": 0}
    ).sort("created_at", -1).to_list(20)

    # Generate pre-signed URLs for all files
    for session in sessions:
        for f in session.get("files", []):
            r2_key = f.get("r2_key") or f.get("stored_path", "")
            if r2_key and not r2_key.startswith("/"):
                f["url"] = await get_presigned_url(r2_key, expires_in=3600)
            elif f.get("stored_path"):
                # Legacy local file — serve via old endpoint
                f["url"] = f"/api/uploads/file/{record_type}/{record_id}/{f['id']}"

    total_files = sum(s.get("file_count", 0) for s in sessions)
    return {"sessions": sessions, "total_files": total_files}


# ─────────────────────────────────────────────────────────────────────────────
#  Serve a file (supports both R2 and legacy local files)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/file/{record_type}/{record_id}/{file_id}")
async def serve_file(record_type: str, record_id: str, file_id: str):
    """Serve a file — redirects to pre-signed R2 URL, or serves local file for legacy.
    
    Public endpoint (linked from the upload preview page on mobile). Uses _raw_db
    because public endpoints have no org context.
    """
    session = await _raw_db.upload_sessions.find_one(
        {"record_type": record_type, "record_id": record_id, "files.id": file_id},
        {"_id": 0, "files": 1, "org_id": 1, "organization_id": 1}
    )
    if not session:
        raise HTTPException(status_code=404, detail="File not found")

    file_meta = next((f for f in session.get("files", []) if f["id"] == file_id), None)
    if not file_meta:
        raise HTTPException(status_code=404, detail="File not found")

    # R2 file — redirect to pre-signed URL
    r2_key = file_meta.get("r2_key")
    if r2_key:
        from fastapi.responses import RedirectResponse
        url = await get_presigned_url(r2_key, expires_in=3600)
        return RedirectResponse(url=url)

    # Legacy local file
    from fastapi.responses import FileResponse
    filepath = Path(file_meta.get("stored_path", ""))
    if not filepath.exists():
        raise HTTPException(status_code=404, detail="File data not found on server")
    media_type = file_meta.get("content_type", "image/jpeg")
    return FileResponse(path=str(filepath), media_type=media_type, filename=file_meta.get("filename", "file"))


# ─────────────────────────────────────────────────────────────────────────────
#  View tokens (QR "View on Phone")
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/generate-view-token")
async def generate_view_token(data: dict, user=Depends(get_current_user)):
    """Generate a 1-hour read-only view token for a record's uploaded photos."""
    record_type = data.get("record_type", "")
    record_id = data.get("record_id", "")
    if not record_type or not record_id:
        raise HTTPException(status_code=400, detail="record_type and record_id required")

    token = secrets.token_urlsafe(24)
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    summary = await _get_record_summary(record_type, record_id)

    doc = {
        "id": new_id(),
        "token": token,
        "token_type": "view",
        "token_expires_at": expires_at,
        "record_type": record_type,
        "record_id": record_id,
        "record_summary": summary,
        "created_by": user["id"],
        "created_by_name": user.get("full_name", user["username"]),
        "created_at": now_iso(),
    }
    await db.view_tokens.insert_one(doc)
    return {"token": token, "expires_at": expires_at, "record_summary": summary}


@router.get("/view-session/{token}")
async def get_view_session(token: str):
    """Public endpoint — returns record summary + all uploaded files for the view QR.
    
    Uses _raw_db for the initial token lookup (public, no auth) and sets org
    context from the view doc so subsequent scoped queries work.
    """
    view_doc = await _raw_db.view_tokens.find_one({"token": token, "token_type": "view"}, {"_id": 0})
    if not view_doc:
        raise HTTPException(status_code=404, detail="View link not found or expired")

    # Set org context from the view doc
    org_id = view_doc.get("organization_id") or view_doc.get("org_id")
    if org_id:
        set_org_context(org_id)

    expires_at = view_doc.get("token_expires_at", "")
    if expires_at:
        try:
            exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if exp < datetime.now(timezone.utc):
                raise HTTPException(status_code=410, detail="View link has expired.")
        except ValueError:
            pass

    record_type = view_doc["record_type"]
    record_id = view_doc["record_id"]

    sessions = await db.upload_sessions.find(
        {"record_type": record_type, "record_id": record_id},
        {"_id": 0, "token": 0}
    ).sort("created_at", -1).to_list(20)

    # Generate pre-signed URLs
    for session in sessions:
        for f in session.get("files", []):
            r2_key = f.get("r2_key") or f.get("stored_path", "")
            if r2_key and not r2_key.startswith("/"):
                f["url"] = await get_presigned_url(r2_key, expires_in=3600)
            elif f.get("stored_path"):
                f["url"] = f"/api/uploads/file/{record_type}/{record_id}/{f['id']}"

    total_files = sum(s.get("file_count", 0) for s in sessions)

    # Verification status
    collection_map = {
        "purchase_order": "purchase_orders",
        "expense": "expenses",
        "branch_transfer": "branch_transfer_orders",
    }
    verification = {}
    if record_type in collection_map:
        coll = getattr(db, collection_map[record_type])
        doc = await coll.find_one({"id": record_id}, {"_id": 0,
            "verified": 1, "verified_by_name": 1, "verified_at": 1,
            "verification_status": 1, "has_discrepancy": 1, "discrepancy": 1
        })
        if doc:
            verification = {
                "verified": doc.get("verified", False),
                "verified_by_name": doc.get("verified_by_name"),
                "verified_at": doc.get("verified_at"),
                "verification_status": doc.get("verification_status"),
                "has_discrepancy": doc.get("has_discrepancy", False),
                "discrepancy": doc.get("discrepancy"),
            }

    return {
        "record_summary": view_doc.get("record_summary", {}),
        "record_type": record_type,
        "record_id": record_id,
        "sessions": sessions,
        "total_files": total_files,
        "verification": verification,
        "expires_at": expires_at,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Direct upload (authenticated, inline) → R2
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/direct")
async def direct_upload(
    files: List[UploadFile] = File(...),
    record_type: str = Form("purchase_order"),
    session_id: Optional[str] = Form(None),
    user=Depends(get_current_user),
):
    """Authenticated direct upload — stores files in R2 and returns a session_id."""
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")

    org_id = _get_org_id()

    if session_id:
        session = await db.upload_sessions.find_one({"id": session_id}, {"_id": 0})
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        current_count = session.get("file_count", 0)
        if current_count + len(files) > MAX_FILES:
            raise HTTPException(status_code=400, detail=f"Max {MAX_FILES} files. Already has {current_count}.")
    else:
        session_id = new_id()
        pending_record_id = f"pending_{new_id()}"
        session = {
            "id": session_id,
            "token": "",
            "record_type": record_type,
            "record_id": pending_record_id,
            "record_summary": {},
            "org_id": org_id,
            "files": [],
            "file_count": 0,
            "is_pending": True,
            "created_by": user["id"],
            "created_by_name": user.get("full_name", user["username"]),
            "created_at": now_iso(),
        }
        await db.upload_sessions.insert_one(session)
        del session["_id"]
        current_count = 0

    record_id = session.get("record_id", session_id)

    saved = []
    file_ids = []
    for file in files:
        file_id = new_id()
        ext = Path(file.filename).suffix if file.filename else ".jpg"
        safe_ext = ext.lower() if ext.lower() in (".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".pdf") else ".jpg"
        filename = f"{file_id}{safe_ext}"
        content_type = file.content_type or "image/jpeg"

        content = await file.read()
        r2_result = await upload_file(org_id, record_type, record_id, filename, content, content_type)

        saved.append({
            "id": file_id,
            "filename": file.filename or filename,
            "r2_key": r2_result["key"],
            "content_type": content_type,
            "size": len(content),
            "uploaded_at": now_iso(),
        })
        file_ids.append(file_id)

    await db.upload_sessions.update_one(
        {"id": session_id},
        {"$push": {"files": {"$each": saved}}, "$inc": {"file_count": len(saved)}}
    )
    return {
        "session_id": session_id,
        "uploaded": len(saved),
        "total_files": current_count + len(saved),
        "file_ids": file_ids,
    }


@router.delete("/direct/{session_id}/{file_id}")
async def delete_direct_file(session_id: str, file_id: str, user=Depends(get_current_user)):
    """Remove a single file from a direct upload session."""
    session = await db.upload_sessions.find_one({"id": session_id}, {"_id": 0})
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    file_meta = next((f for f in session.get("files", []) if f["id"] == file_id), None)
    if not file_meta:
        raise HTTPException(status_code=404, detail="File not found in session")

    # Delete from R2
    r2_key = file_meta.get("r2_key")
    if r2_key:
        await delete_file(r2_key)
    else:
        # Legacy local file
        filepath = Path(file_meta.get("stored_path", ""))
        if filepath.exists():
            filepath.unlink()

    await db.upload_sessions.update_one(
        {"id": session_id},
        {"$pull": {"files": {"id": file_id}}, "$inc": {"file_count": -1}}
    )
    return {"message": "File removed"}


@router.post("/generate-pending-link")
async def generate_pending_link(data: dict, user=Depends(get_current_user)):
    """Generate a QR-scannable upload link for records that don't exist yet."""
    record_type = data.get("record_type", "purchase_order")
    session_id = data.get("session_id", "")
    custom_summary = data.get("record_summary", {})

    token = secrets.token_urlsafe(24)
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()

    if session_id:
        session = await db.upload_sessions.find_one({"id": session_id}, {"_id": 0})
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        await db.upload_sessions.update_one(
            {"id": session_id},
            {"$set": {
                "token": token,
                "token_expires_at": expires_at,
                "record_summary": custom_summary or session.get("record_summary", {}),
            }}
        )
        record_id = session.get("record_id", "")
    else:
        session_id = new_id()
        record_id = f"pending_{new_id()}"
        session = {
            "id": session_id,
            "token": token,
            "token_expires_at": expires_at,
            "record_type": record_type,
            "record_id": record_id,
            "record_summary": custom_summary or {
                "type_label": RECORD_TYPE_LABELS.get(record_type, record_type),
                "title": f"New {RECORD_TYPE_LABELS.get(record_type, record_type)}",
                "description": "Receipt will be linked when record is saved",
            },
            "org_id": _get_org_id(),
            "files": [],
            "file_count": 0,
            "is_pending": True,
            "created_by": user["id"],
            "created_by_name": user.get("full_name", user["username"]),
            "created_at": now_iso(),
        }
        await db.upload_sessions.insert_one(session)
        del session["_id"]

    return {
        "token": token,
        "session_id": session_id,
        "record_id": record_id,
        "expires_at": expires_at,
    }


@router.get("/session-status/{session_id}")
async def get_session_status(session_id: str, user=Depends(get_current_user)):
    """Poll endpoint — returns current file count and files for a session."""
    session = await db.upload_sessions.find_one({"id": session_id}, {"_id": 0, "token": 0})
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        "session_id": session_id,
        "file_count": session.get("file_count", 0),
        "files": [{"id": f["id"], "filename": f["filename"], "content_type": f.get("content_type", "")} for f in session.get("files", [])],
    }


@router.post("/reassign")
async def reassign_upload_session(data: dict, user=Depends(get_current_user)):
    """Link a pending upload session to an actual record. Moves R2 files to correct path."""
    session_id = data.get("session_id", "")
    new_record_type = data.get("record_type", "")
    new_record_id = data.get("record_id", "")
    if not session_id or not new_record_id:
        raise HTTPException(status_code=400, detail="session_id and record_id required")

    session = await db.upload_sessions.find_one({"id": session_id}, {"_id": 0})
    if not session:
        raise HTTPException(status_code=404, detail="Upload session not found")

    old_record_id = session.get("record_id", "")
    record_type = new_record_type or session.get("record_type", "purchase_order")
    org_id = session.get("org_id", _get_org_id())

    if old_record_id != new_record_id:
        # Move R2 files to new path
        from utils.r2_storage import _get_client, _bucket
        client = _get_client()
        updated_files = []
        for f in session.get("files", []):
            old_key = f.get("r2_key", "")
            if old_key:
                # Copy to new key, delete old
                ext = Path(old_key).suffix
                new_filename = f"{f['id']}{ext}"
                new_key = build_key(org_id, record_type, new_record_id, new_filename)
                try:
                    client.copy_object(
                        Bucket=_bucket,
                        CopySource={"Bucket": _bucket, "Key": old_key},
                        Key=new_key,
                    )
                    client.delete_object(Bucket=_bucket, Key=old_key)
                    f["r2_key"] = new_key
                except Exception:
                    pass  # Keep old key if move fails
            elif f.get("stored_path"):
                # Legacy local file — move on disk
                old_path = Path(f["stored_path"])
                if old_path.exists():
                    new_dir = Path("/app/uploads") / record_type / new_record_id
                    new_dir.mkdir(parents=True, exist_ok=True)
                    new_path = new_dir / old_path.name
                    old_path.rename(new_path)
                    f["stored_path"] = str(new_path)
            updated_files.append(f)

        summary = await _get_record_summary(record_type, new_record_id)
        await db.upload_sessions.update_one(
            {"id": session_id},
            {"$set": {
                "record_type": record_type,
                "record_id": new_record_id,
                "record_summary": summary,
                "files": updated_files,
                "is_pending": False,
                "reassigned_at": now_iso(),
            }}
        )
    else:
        await db.upload_sessions.update_one(
            {"id": session_id},
            {"$set": {"is_pending": False, "reassigned_at": now_iso()}}
        )

    # If this is an invoice upload, mark receipt as uploaded
    if record_type == "invoice" and new_record_id:
        await db.invoices.update_one(
            {"id": new_record_id},
            {"$set": {"receipt_status": "uploaded"}}
        )

    return {"message": "Upload session linked to record", "record_id": new_record_id}


# ─────────────────────────────────────────────────────────────────────────────
#  Mark-reviewed endpoint
# ─────────────────────────────────────────────────────────────────────────────
REVIEWABLE_COLLECTIONS = {
    "branch_transfer": "branch_transfer_orders",
    "expense": "expenses",
    "invoice": "invoices",
}

@router.post("/mark-reviewed/{record_type}/{record_id}")
async def mark_record_reviewed(record_type: str, record_id: str, data: dict, user=Depends(get_current_user)):
    """Mark a record's receipts as reviewed."""
    collection_name = REVIEWABLE_COLLECTIONS.get(record_type)
    if not collection_name:
        raise HTTPException(status_code=400, detail=f"Unsupported record type: {record_type}")

    collection = getattr(db, collection_name)
    record = await collection.find_one({"id": record_id}, {"_id": 0})
    if not record:
        raise HTTPException(status_code=404, detail="Record not found")

    pin = str(data.get("pin", ""))
    if not pin:
        raise HTTPException(status_code=400, detail="Admin PIN or TOTP required")

    from routes.verify import verify_pin_for_action
    verifier = await verify_pin_for_action(pin, "receipt_mark_reviewed")
    if not verifier:
        raise HTTPException(status_code=403, detail="Invalid PIN or TOTP")

    review_notes = data.get("notes", "")
    await collection.update_one({"id": record_id}, {"$set": {
        "receipt_review_status": "reviewed",
        "receipt_reviewed_by_id": verifier["verifier_id"],
        "receipt_reviewed_by_name": verifier["verifier_name"],
        "receipt_reviewed_at": now_iso(),
        "receipt_review_notes": review_notes,
    }})

    label = record.get("order_number", record.get("reference_number", record_id[:8]))
    return {
        "message": f"{RECORD_TYPE_LABELS.get(record_type, record_type)} {label} receipts marked as reviewed",
        "reviewed_by": verifier["verifier_name"],
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Share receipt — attach one uploaded receipt to multiple records
# ─────────────────────────────────────────────────────────────────────────────
@router.post("/share-receipt")
async def share_receipt_to_records(data: dict, user=Depends(get_current_user)):
    """
    Share an uploaded receipt from one PO to multiple other POs.
    Creates new upload_session records pointing to the same files (no R2/storage copy needed).
    Used for collection receipts that cover multiple supplier invoices on one physical document.

    Body: { source_record_id, target_record_ids: [...], record_type: "purchase_order" }
    """
    source_record_id = data.get("source_record_id", "")
    target_record_ids = data.get("target_record_ids", [])
    record_type = data.get("record_type", "purchase_order")

    if not source_record_id or not target_record_ids:
        raise HTTPException(status_code=400, detail="source_record_id and target_record_ids required")

    # Find the most recent completed upload session for the source record
    source_session = await db.upload_sessions.find_one(
        {"record_type": record_type, "record_id": source_record_id, "file_count": {"$gt": 0}},
        {"_id": 0},
        sort=[("created_at", -1)],
    )
    if not source_session:
        raise HTTPException(status_code=404, detail="No uploaded receipt found for source record. Please upload first.")

    source_files = source_session.get("files", [])
    if not source_files:
        raise HTTPException(status_code=400, detail="Source session has no files")

    created = 0
    for target_id in target_record_ids:
        if target_id == source_record_id:
            continue  # Source already has the receipt — skip

        record_summary = await _get_record_summary(record_type, target_id)

        # Mirror file entries — same stored_path/R2 key, just a new session record
        # shared_from field provides a clear audit trail back to the original upload
        shared_files = [
            {**f, "shared_from": source_record_id}
            for f in source_files
        ]

        await db.upload_sessions.insert_one({
            "id": new_id(),
            "token": secrets.token_urlsafe(24),   # expired token — no new uploads via this session
            "token_expires_at": now_iso(),          # immediately expired
            "record_type": record_type,
            "record_id": target_id,
            "record_summary": record_summary,
            "file_count": len(source_files),
            "is_shared": True,
            "shared_from_record_id": source_record_id,
            "files": shared_files,
            "created_by": user["id"],
            "created_by_name": user.get("full_name", user.get("username", "")),
            "created_at": now_iso(),
        })
        created += 1

    return {
        "shared_to": created,
        "file_count": len(source_files),
        "message": f"Receipt shared to {created} PO{'s' if created != 1 else ''}",
    }

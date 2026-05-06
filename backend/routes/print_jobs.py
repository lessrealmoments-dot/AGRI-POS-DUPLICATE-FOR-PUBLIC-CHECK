"""
Remote Branch Printing Terminal — Print Job Management
=======================================================
Handles: Job creation, status tracking, real-time WebSocket push,
         polling fallback, terminal mode (auto/manual), 15-day history,
         30-day auto-purge of inactive terminals, and external document
         upload-to-print (PDF / images via Cloudflare R2).

EXE Integration Contract:
  POST /api/terminal/credential-pair         → Login + branch selection (terminal.py)
  WS   /api/terminal/ws/terminal/{id}        → Real-time push (terminal_ws.py)
  GET  /api/print/jobs/pending?terminal_id=  → Polling fallback — ALWAYS pass terminal_id
  PUT  /api/print/jobs/{id}/status           → Mark printed/failed/cancelled
  GET  /api/print/jobs/{id}/file-url         → Fresh presigned URL for external docs
  POST /api/print/upload-job                 → Upload external doc + create print job
"""
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, UploadFile, File, Form
from typing import Optional
from datetime import datetime, timezone, timedelta
import os
from config import _raw_db
from utils import get_current_user, now_iso, new_id

router = APIRouter(prefix="/print", tags=["Print Jobs"])

HISTORY_DAYS = 15
PURGE_DAYS = 30

# Max upload size for external print documents: 20 MB
MAX_UPLOAD_BYTES = 20 * 1024 * 1024

ALLOWED_MIME_TYPES = {
    "application/pdf":   "pdf",
    "image/jpeg":        "image",
    "image/jpg":         "image",
    "image/png":         "image",
    "image/tiff":        "image",
    "image/webp":        "image",
    "image/gif":         "image",
    "image/bmp":         "image",
}

DOCUMENT_TYPE_LABELS = {
    "sales_receipt":       "Sales Receipt",
    "purchase_order":      "Purchase Order",
    "z_report":            "Z-Report",
    "advance_z_report":    "Advance Z-Report",
    "branch_transfer":     "Branch Transfer",
    "expense_receipt":     "Expense Receipt",
    "return_receipt":      "Return/Refund Receipt",
    "statement":           "Customer Statement",
    "barcode_sheet":       "Barcode Sheet",
    "external_document":   "External Document",
}


def build_document_name(
    document_type: str,
    reference_number: str = "",
    branch_name: str = "",
    date_str: str = "",
) -> str:
    label = DOCUMENT_TYPE_LABELS.get(document_type, document_type.replace("_", " ").title())
    if document_type in ("z_report", "advance_z_report") and branch_name:
        date_part = f" – {date_str}" if date_str else ""
        ref_part = f" #{reference_number}" if reference_number else ""
        return f"{label} – {branch_name}{date_part}{ref_part}"
    if reference_number:
        return f"{label} #{reference_number}"
    return label


# ── Create Print Job ─────────────────────────────────────────────────────────

@router.post("/jobs")
async def create_print_job(data: dict, user=Depends(get_current_user)):
    """
    Create a new print job and push to connected terminal immediately.
    If terminal is offline, job stays PENDING until it reconnects.
    Terminals not seen for 30+ days are treated as purged (job still queued
    for 15 days in case they come back).
    """
    from routes.terminal_ws import terminal_ws_manager

    org_id = user.get("organization_id")
    terminal_id     = data.get("terminal_id", "")
    branch_id       = data.get("branch_id", "")
    document_type   = data.get("document_type", "")
    document_id     = data.get("document_id", "")
    document_name   = data.get("document_name", "")
    reference_number = data.get("reference_number", "")
    html_content    = data.get("html_content", "")
    metadata        = data.get("metadata", {})
    priority        = data.get("priority", "normal")

    if not terminal_id:
        raise HTTPException(status_code=400, detail="terminal_id required")
    if not document_type:
        raise HTTPException(status_code=400, detail="document_type required")
    if not html_content:
        raise HTTPException(status_code=400, detail="html_content required")

    # Validate terminal belongs to org
    t_query = {"terminal_id": terminal_id}
    if org_id:
        t_query["organization_id"] = org_id
    terminal = await _raw_db.terminal_sessions.find_one(t_query, {"_id": 0})
    if not terminal:
        raise HTTPException(status_code=404, detail="Terminal not found or not assigned to your organisation")

    # Build document name if caller didn't provide one
    if not document_name:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        branch_name = terminal.get("branch_name", "")
        document_name = build_document_name(document_type, reference_number, branch_name, date_str)

    job_id = new_id()
    job = {
        "id":               job_id,
        "organization_id":  org_id,
        "branch_id":        branch_id or terminal.get("branch_id", ""),
        "branch_name":      terminal.get("branch_name", ""),
        "terminal_id":      terminal_id,
        "terminal_name":    terminal.get("user_name", terminal.get("code", "Terminal")),
        "document_type":    document_type,
        "document_name":    document_name,
        "document_id":      document_id,
        "reference_number": reference_number,
        "html_content":     html_content,
        "metadata":         metadata,
        "priority":         priority,
        "status":           "pending",
        "created_at":       now_iso(),
        "created_by":       user.get("id", ""),
        "created_by_name":  user.get("full_name", user.get("username", "")),
        "sent_at":          None,
        "printed_at":       None,
        "failed_at":        None,
        "cancelled_at":     None,
        "error_message":    None,
    }
    await _raw_db.print_jobs.insert_one(job)

    # Try immediate WebSocket push
    is_connected = terminal_id in terminal_ws_manager.get_connected_terminal_ids()
    if is_connected:
        await terminal_ws_manager.notify_terminal(terminal_id, "print_job", {
            "job_id":           job_id,
            "document_type":    document_type,
            "document_name":    document_name,
            "document_id":      document_id,
            "reference_number": reference_number,
            "html_content":     html_content,
            "metadata":         metadata,
            "priority":         priority,
            "created_at":       job["created_at"],
        })
        await _raw_db.print_jobs.update_one(
            {"id": job_id},
            {"$set": {"status": "sent", "sent_at": now_iso()}}
        )
        return {"job_id": job_id, "status": "sent",
                "message": "Print job sent to terminal"}

    return {"job_id": job_id, "status": "pending",
            "message": "Terminal is offline. Job queued — will deliver when terminal reconnects."}


# ── List Print Jobs (Admin) ──────────────────────────────────────────────────

@router.get("/jobs")
async def list_print_jobs(
    branch_id:   str = None,
    terminal_id: str = None,
    status:      str = None,
    limit:       int = 200,
    user=Depends(get_current_user),
):
    """
    List print jobs for admin — last 15 days of history.
    Filterable by branch, terminal, and status.
    """
    org_id = user.get("organization_id")
    cutoff = (datetime.now(timezone.utc) - timedelta(days=HISTORY_DAYS)).isoformat()
    query = {"organization_id": org_id, "created_at": {"$gte": cutoff}}

    if branch_id:   query["branch_id"]   = branch_id
    if terminal_id: query["terminal_id"] = terminal_id
    if status:      query["status"]      = status

    jobs = await _raw_db.print_jobs.find(
        query, {"_id": 0, "html_content": 0}
    ).sort("created_at", -1).to_list(min(limit, 500))
    return jobs


# ── Pending Jobs Polling (EXE / terminal reconnect) ─────────────────────────

@router.get("/jobs/pending")
async def get_pending_jobs(
    terminal_id: str = None,
    user=Depends(get_current_user),
):
    """
    Polling endpoint for terminal EXE.
    Returns all pending/sent jobs for this terminal and marks them as sent.
    Call this on reconnect and periodically as a fallback to WebSocket.

    IMPORTANT: always pass ?terminal_id= so the correct terminal session is
    used. Without it we fall back to the user's latest session, which can
    drift if the same user has multiple active terminals (e.g. admin at
    two branches).
    """
    org_id = user.get("organization_id")

    if terminal_id:
        # EXE passes its own terminal_id — precise, no ambiguity
        t_query = {"terminal_id": terminal_id, "status": "active"}
        if org_id:
            t_query["organization_id"] = org_id
        terminal = await _raw_db.terminal_sessions.find_one(t_query, {"_id": 0})
        if not terminal:
            raise HTTPException(status_code=404, detail="Terminal session not found")
    else:
        # Fallback: resolve by latest active session for this user.
        # Works fine for single-terminal users; may drift for admins with
        # multiple active sessions — pass terminal_id to avoid this.
        terminal = await _raw_db.terminal_sessions.find_one(
            {"user_id": user["id"], "status": "active"},
            {"_id": 0}, sort=[("paired_at", -1)]
        )
        if not terminal:
            raise HTTPException(status_code=404, detail="No active terminal session")

    resolved_terminal_id = terminal["terminal_id"]

    # Heartbeat
    await _raw_db.terminal_sessions.update_one(
        {"terminal_id": resolved_terminal_id},
        {"$set": {"last_seen": now_iso()}}
    )

    job_query: dict = {"terminal_id": resolved_terminal_id, "status": {"$in": ["pending", "sent"]}}
    if org_id:
        job_query["organization_id"] = org_id

    jobs = await _raw_db.print_jobs.find(
        job_query, {"_id": 0}
    ).sort("created_at", 1).to_list(50)

    # Mark newly found pending → sent
    if jobs:
        pending_ids = [j["id"] for j in jobs if j["status"] == "pending"]
        if pending_ids:
            await _raw_db.print_jobs.update_many(
                {"id": {"$in": pending_ids}},
                {"$set": {"status": "sent", "sent_at": now_iso()}}
            )

    return {
        "jobs":        jobs,
        "terminal_id": resolved_terminal_id,
        "print_mode":  terminal.get("print_mode", "manual"),
        "branch_id":   terminal.get("branch_id"),
        "branch_name": terminal.get("branch_name"),
    }


# ── Update Job Status (EXE confirms print/fail) ─────────────────────────────

@router.put("/jobs/{job_id}/status")
async def update_job_status(job_id: str, data: dict, user=Depends(get_current_user)):
    """
    Update print job status. Called by terminal EXE after printing.
    Allowed statuses: printed | failed | cancelled
    """
    new_status    = data.get("status", "")
    error_message = data.get("error_message", "")

    if new_status not in ("printed", "failed", "cancelled"):
        raise HTTPException(status_code=400, detail="Status must be: printed, failed, or cancelled")

    org_id = user.get("organization_id")
    job_query = {"id": job_id}
    if org_id:
        job_query["organization_id"] = org_id

    job = await _raw_db.print_jobs.find_one(job_query, {"_id": 0, "organization_id": 1})
    if not job:
        raise HTTPException(status_code=404, detail="Print job not found")

    update: dict = {"status": new_status}
    ts = now_iso()
    if new_status == "printed":
        update["printed_at"] = ts
    elif new_status == "failed":
        update["failed_at"]     = ts
        update["error_message"] = error_message
    elif new_status == "cancelled":
        update["cancelled_at"] = ts

    # Scope the write to the same filter used for the read (defense-in-depth)
    await _raw_db.print_jobs.update_one(job_query, {"$set": update})
    return {"job_id": job_id, "status": new_status}


# ── Get Single Job (with HTML, for re-printing) ─────────────────────────────

@router.get("/jobs/{job_id}")
async def get_print_job(job_id: str, user=Depends(get_current_user)):
    """Get a specific print job including HTML content (for re-printing)."""
    org_id = user.get("organization_id")
    job = await _raw_db.print_jobs.find_one(
        {"id": job_id, "organization_id": org_id}, {"_id": 0}
    )
    if not job:
        raise HTTPException(status_code=404, detail="Print job not found")
    return job


# ── Resend Failed / Cancelled Jobs ──────────────────────────────────────────

@router.post("/jobs/{job_id}/resend")
async def resend_print_job(job_id: str, user=Depends(get_current_user)):
    """Re-queue a failed or cancelled print job."""
    from routes.terminal_ws import terminal_ws_manager

    org_id = user.get("organization_id")
    job = await _raw_db.print_jobs.find_one(
        {"id": job_id, "organization_id": org_id}, {"_id": 0}
    )
    if not job:
        raise HTTPException(status_code=404, detail="Print job not found")
    if job["status"] not in ("failed", "cancelled", "pending"):
        raise HTTPException(status_code=400, detail=f"Cannot resend a job with status '{job['status']}'")

    terminal_id  = job["terminal_id"]
    is_connected = terminal_id in terminal_ws_manager.get_connected_terminal_ids()

    if is_connected:
        await terminal_ws_manager.notify_terminal(terminal_id, "print_job", {
            "job_id":           job_id,
            "document_type":    job["document_type"],
            "document_name":    job["document_name"],
            "document_id":      job.get("document_id"),
            "reference_number": job.get("reference_number"),
            "html_content":     job["html_content"],
            "metadata":         job.get("metadata", {}),
            "priority":         job.get("priority", "normal"),
            "created_at":       job["created_at"],
        })
        await _raw_db.print_jobs.update_one(
            {"id": job_id},
            {"$set": {"status": "sent", "sent_at": now_iso(), "error_message": None}}
        )
        return {"job_id": job_id, "status": "sent"}
    else:
        await _raw_db.print_jobs.update_one(
            {"id": job_id},
            {"$set": {"status": "pending", "sent_at": None, "error_message": None}}
        )
        return {"job_id": job_id, "status": "pending",
                "message": "Terminal offline. Job queued for reconnect."}


# ── List Print Terminals (Admin) ─────────────────────────────────────────────

@router.get("/terminals")
async def list_print_terminals(user=Depends(get_current_user)):
    """
    List all active terminal sessions for admin.
    Includes online/offline status and pending job count.
    """
    from routes.terminal_ws import terminal_ws_manager

    org_id = user.get("organization_id")
    sessions = await _raw_db.terminal_sessions.find(
        {"organization_id": org_id, "status": "active"},
        {"_id": 0, "token": 0}
    ).sort("paired_at", -1).to_list(100)

    connected_ids = set(terminal_ws_manager.get_connected_terminal_ids())

    for s in sessions:
        s["is_online"] = s["terminal_id"] in connected_ids
        s["pending_jobs"] = await _raw_db.print_jobs.count_documents({
            "terminal_id": s["terminal_id"],
            "status":      {"$in": ["pending", "sent"]},
        })

    return sessions


# ── Set Terminal Print Mode ──────────────────────────────────────────────────

@router.post("/terminal/set-mode")
async def set_terminal_print_mode(data: dict, user=Depends(get_current_user)):
    """
    Toggle auto/manual print mode for a terminal.
      auto   = print dialog fires immediately when job arrives (good for busy counters)
      manual = job sits in queue; staff clicks "Print Now" (good for careful review)
    """
    from routes.terminal_ws import terminal_ws_manager

    terminal_id = data.get("terminal_id", "")
    mode        = data.get("mode", "")

    if mode not in ("auto", "manual"):
        raise HTTPException(status_code=400, detail="mode must be: auto or manual")
    if not terminal_id:
        raise HTTPException(status_code=400, detail="terminal_id required")

    org_id   = user.get("organization_id")
    terminal = await _raw_db.terminal_sessions.find_one(
        {"terminal_id": terminal_id, "organization_id": org_id}, {"_id": 0}
    )
    if not terminal:
        raise HTTPException(status_code=404, detail="Terminal not found")

    await _raw_db.terminal_sessions.update_one(
        {"terminal_id": terminal_id},
        {"$set": {"print_mode": mode, "last_seen": now_iso()}}
    )
    # Notify connected terminal of mode change
    await terminal_ws_manager.notify_terminal(
        terminal_id, "print_mode_changed", {"mode": mode}
    )
    return {"terminal_id": terminal_id, "print_mode": mode}


# ── Get Current Terminal Print Session ──────────────────────────────────────

@router.get("/terminal/session")
async def get_terminal_print_session(user=Depends(get_current_user)):
    """Get print mode and session info for the currently authenticated terminal."""
    session = await _raw_db.terminal_sessions.find_one(
        {"user_id": user["id"], "status": "active"},
        {"_id": 0, "token": 0}, sort=[("paired_at", -1)]
    )
    if not session:
        raise HTTPException(status_code=404, detail="No active terminal session")
    return {
        "terminal_id": session["terminal_id"],
        "branch_id":   session.get("branch_id"),
        "branch_name": session.get("branch_name"),
        "print_mode":  session.get("print_mode", "manual"),
        "last_seen":   session.get("last_seen"),
        "paired_via":  session.get("paired_via"),
    }


# ── Available Terminals for a Branch (for Send to Print picker) ──────────────

@router.get("/terminals/for-branch/{branch_id}")
async def get_terminals_for_branch(branch_id: str, user=Depends(get_current_user)):
    """
    Return all active terminals for a given branch (online first).
    Used by the SendToPrintModal to populate the terminal picker.
    """
    from routes.terminal_ws import terminal_ws_manager

    org_id = user.get("organization_id")
    sessions = await _raw_db.terminal_sessions.find(
        {"organization_id": org_id, "branch_id": branch_id, "status": "active"},
        {"_id": 0, "token": 0}
    ).sort("last_seen", -1).to_list(20)

    connected_ids = set(terminal_ws_manager.get_connected_terminal_ids())
    for s in sessions:
        s["is_online"] = s["terminal_id"] in connected_ids

    # Sort: online first
    sessions.sort(key=lambda s: (0 if s["is_online"] else 1))
    return sessions


# ── Upload External Document → Print Job ─────────────────────────────────────

@router.post("/upload-job")
async def upload_external_print_job(
    file:        UploadFile = File(...),
    terminal_id: str        = Form(...),
    branch_id:   str        = Form(""),
    title:       str        = Form(...),
    description: str        = Form(""),
    print_mode:  str        = Form("manual"),
    user=Depends(get_current_user),
):
    """
    Upload an external document (PDF / image) and send it as a print job
    to a selected branch terminal.

    Flow:
      1. Validate file type + size (≤ 20 MB)
      2. Upload to Cloudflare R2 under print_jobs/{org_id}/{job_id}/{filename}
      3. Generate a 24-hour presigned download URL
      4. Create print job with source_type="external"
      5. Push to terminal via WebSocket if online; otherwise stays "pending"
    """
    from routes.terminal_ws import terminal_ws_manager

    org_id = user.get("organization_id")

    # ── Validate content type ──────────────────────────────────────────────
    content_type = file.content_type or ""
    file_format  = ALLOWED_MIME_TYPES.get(content_type)
    if not file_format:
        # Try extension fallback
        ext = (file.filename or "").rsplit(".", 1)[-1].lower()
        ext_map = {"pdf": "pdf", "jpg": "image", "jpeg": "image",
                   "png": "image", "tiff": "image", "tif": "image",
                   "webp": "image", "gif": "image", "bmp": "image"}
        file_format = ext_map.get(ext)
        if not file_format:
            raise HTTPException(
                status_code=415,
                detail="Unsupported file type. Allowed: PDF, JPG, PNG, TIFF, WEBP, GIF, BMP"
            )

    # ── Read file + size check ─────────────────────────────────────────────
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum size is {MAX_UPLOAD_BYTES // (1024*1024)} MB"
        )

    # ── Validate terminal ──────────────────────────────────────────────────
    terminal_query = {"terminal_id": terminal_id}
    if org_id:
        terminal_query["organization_id"] = org_id
    terminal = await _raw_db.terminal_sessions.find_one(terminal_query, {"_id": 0})
    if not terminal:
        raise HTTPException(status_code=404, detail="Terminal not found")

    # ── Upload to R2 ──────────────────────────────────────────────────────
    job_id    = new_id()
    safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in (file.filename or "document"))
    filename  = f"{job_id}_{safe_name}"

    file_key  = ""
    file_url  = ""

    try:
        from utils.r2_storage import upload_file as r2_upload, get_presigned_url
        result   = await r2_upload(
            org_id      = org_id,
            record_type = "print_jobs",
            record_id   = job_id,
            filename    = filename,
            content     = content,
            content_type= content_type or ("application/pdf" if file_format == "pdf" else "image/jpeg"),
        )
        file_key = result["key"]
        file_url = await get_presigned_url(file_key, expires_in=86400)  # 24 hr
    except Exception as r2_err:
        # R2 not configured or failed — reject with helpful message
        raise HTTPException(
            status_code=503,
            detail=f"File storage unavailable. Configure R2 credentials to enable external document printing. ({r2_err})"
        )

    # ── Build document name ────────────────────────────────────────────────
    document_name = title.strip() or safe_name

    # ── Create print job ──────────────────────────────────────────────────
    job = {
        "id":               job_id,
        "organization_id":  org_id,
        "branch_id":        branch_id or terminal.get("branch_id", ""),
        "branch_name":      terminal.get("branch_name", ""),
        "terminal_id":      terminal_id,
        "terminal_name":    terminal.get("user_name", terminal.get("code", "Terminal")),
        "document_type":    "external_document",
        "document_name":    document_name,
        "document_id":      "",
        "reference_number": "",
        "html_content":     "",
        "source_type":      "external",
        "file_key":         file_key,
        "file_url":         file_url,
        "file_type":        file_format,
        "file_name":        file.filename or safe_name,
        "description":      description.strip(),
        "metadata": {
            "original_filename": file.filename,
            "file_size_bytes":   len(content),
            "uploaded_by":       user.get("full_name", user.get("username", "")),
        },
        "priority":         "normal",
        "print_mode_hint":  print_mode,  # suggestion to terminal; terminal's own setting takes precedence
        "status":           "pending",
        "created_at":       now_iso(),
        "created_by":       user.get("id", ""),
        "created_by_name":  user.get("full_name", user.get("username", "")),
        "sent_at":          None,
        "printed_at":       None,
        "failed_at":        None,
        "cancelled_at":     None,
        "error_message":    None,
    }
    await _raw_db.print_jobs.insert_one(job)

    # ── Push via WebSocket if terminal is online ───────────────────────────
    is_connected = terminal_id in terminal_ws_manager.get_connected_terminal_ids()
    if is_connected:
        await terminal_ws_manager.notify_terminal(terminal_id, "print_job", {
            "job_id":        job_id,
            "document_type": "external_document",
            "document_name": document_name,
            "source_type":   "external",
            "file_url":      file_url,
            "file_type":     file_format,
            "file_name":     file.filename or safe_name,
            "description":   description,
            "priority":      "normal",
            "created_at":    job["created_at"],
        })
        await _raw_db.print_jobs.update_one(
            {"id": job_id},
            {"$set": {"status": "sent", "sent_at": now_iso()}}
        )
        return {"job_id": job_id, "status": "sent",
                "message": "Document sent to terminal",
                "document_name": document_name}

    return {"job_id": job_id, "status": "pending",
            "message": "Terminal is offline. Document will be delivered when it reconnects.",
            "document_name": document_name}


# ── Fresh Presigned URL for External Doc ─────────────────────────────────────

@router.get("/jobs/{job_id}/file-url")
async def get_job_file_url(job_id: str, user=Depends(get_current_user)):
    """
    Return a fresh 24-hour presigned download URL for an external print job file.
    Called by the EXE before printing if the stored URL may have expired.
    """
    org_id = user.get("organization_id")
    job_query = {"id": job_id}
    if org_id:
        job_query["organization_id"] = org_id
    job    = await _raw_db.print_jobs.find_one(
        job_query, {"_id": 0, "file_key": 1, "source_type": 1}
    )
    if not job:
        raise HTTPException(status_code=404, detail="Print job not found")
    if job.get("source_type") != "external":
        raise HTTPException(status_code=400, detail="This job does not have an attached file")
    if not job.get("file_key"):
        raise HTTPException(status_code=400, detail="No file key stored for this job")

    try:
        from utils.r2_storage import get_presigned_url
        url = await get_presigned_url(job["file_key"], expires_in=86400)
        # Update stored URL with fresh one
        await _raw_db.print_jobs.update_one(
            {"id": job_id}, {"$set": {"file_url": url}}
        )
        return {"job_id": job_id, "file_url": url, "expires_in": 86400}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Could not refresh file URL: {e}")


# ── Auto-Purge Background Task ────────────────────────────────────────────────

async def purge_inactive_terminals():
    """
    Scheduled task: purge terminal sessions not seen for PURGE_DAYS (30 days).
    Also cleans up print_jobs older than HISTORY_DAYS (15 days).
    Safe to call multiple times (idempotent).
    """
    now = datetime.now(timezone.utc)
    purge_cutoff   = (now - timedelta(days=PURGE_DAYS)).isoformat()
    history_cutoff = (now - timedelta(days=HISTORY_DAYS)).isoformat()

    # Mark stale terminal sessions as purged
    result = await _raw_db.terminal_sessions.update_many(
        {
            "status":    "active",
            "last_seen": {"$lt": purge_cutoff},
        },
        {"$set": {"status": "purged", "purged_at": now.isoformat()}}
    )
    purged_count = result.modified_count

    # Remove print jobs older than HISTORY_DAYS
    del_result = await _raw_db.print_jobs.delete_many(
        {"created_at": {"$lt": history_cutoff}}
    )

    return {
        "purged_terminals": purged_count,
        "deleted_jobs":     del_result.deleted_count,
    }

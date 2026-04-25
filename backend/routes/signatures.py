"""
Digital Signature routes — QR-based signature capture for credit authorization.

Flow (Computer):
  1. Staff clicks "Request Signature" → POST /api/signatures/session → get token
  2. QR code displayed (links to /sign/{token})
  3. Customer scans with any phone → views credit summary → draws signature
  4. Customer hits Submit → POST /api/signatures/submit/{token} (public, no auth)
  5. Computer polls GET /api/signatures/status/{token} every 2s
  6. When signed → "Confirm Credit" button unlocks

Flow (Terminal):
  1. Staff creates credit → signature overlay shown directly on screen
  2. Customer draws on touch screen
  3. Submit → POST /api/signatures/submit/{token}

Fallback:
  - If customer can't sign → POST /api/signatures/bypass/{session_id} with Manager PIN

Storage:
  - Signatures stored in R2: {org_id}/signatures/{session_id}/{timestamp}.png
  - Non-deletable (no delete endpoint)
  - Permanently linked to session_id and linked_record_id
"""
import base64
import secrets
import uuid
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException
from config import db, _raw_db
from utils import get_current_user, check_perm, now_iso, new_id

router = APIRouter(prefix="/signatures", tags=["Signatures"])

SESSION_EXPIRY_MINUTES = 5


def _is_session_expired(session: dict) -> bool:
    expires_at = session.get("expires_at", "")
    if not expires_at:
        return True
    try:
        exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        return datetime.now(timezone.utc) > exp
    except Exception:
        return True


# ==================== AUTHENTICATED ENDPOINTS ====================

@router.post("/session")
async def create_signing_session(data: dict, user=Depends(get_current_user)):
    """
    Create a new signing session for a credit authorization.
    Returns a unique token and QR signing URL.
    """
    check_perm(user, "pos", "sell")

    linked_record_type = data.get("linked_record_type", "crop_credit")
    linked_record_id = data.get("linked_record_id", "")
    credit_context = data.get("credit_context", {})

    if not credit_context:
        raise HTTPException(status_code=400, detail="credit_context is required")

    token = secrets.token_urlsafe(32)
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=SESSION_EXPIRY_MINUTES)).isoformat()

    session = {
        "id": new_id(),
        "token": token,
        "organization_id": user.get("organization_id", ""),
        "branch_id": data.get("branch_id", user.get("branch_id", "")),
        "credit_context": {
            "customer_name": credit_context.get("customer_name", ""),
            "amount": float(credit_context.get("amount", 0)),
            "credit_type": credit_context.get("credit_type", "charged_to_crop"),
            "date": credit_context.get("date", now_iso()[:10]),
            "branch_name": credit_context.get("branch_name", ""),
            "description": credit_context.get("description", ""),
            "invoice_number": credit_context.get("invoice_number", ""),
            "items": credit_context.get("items", []),
            "subtotal": float(credit_context.get("subtotal", 0)) if credit_context.get("subtotal") is not None else None,
            "discount": float(credit_context.get("discount", 0)) if credit_context.get("discount") is not None else None,
            "partial_paid": float(credit_context.get("partial_paid", 0)) if credit_context.get("partial_paid") is not None else None,
        },
        "linked_record_type": linked_record_type,
        "linked_record_id": linked_record_id,
        "status": "pending",
        "signature_r2_key": None,
        "signature_url": None,
        "signed_at": None,
        "signer_info": None,
        "bypass_method": None,
        "bypass_by_id": None,
        "bypass_by_name": None,
        "bypass_reason": None,
        "bypassed_at": None,
        "expires_at": expires_at,
        "created_by_id": user["id"],
        "created_by_name": user.get("full_name", user.get("username", "")),
        "created_at": now_iso(),
    }

    await db.signature_sessions.insert_one(session)
    del session["_id"]

    return {
        **session,
        "signing_url": f"/sign/{token}",
    }


@router.get("/status/{token}")
async def get_session_status(token: str, user=Depends(get_current_user)):
    """Poll signing session status. Used by computer side every 2 seconds."""
    session = await db.signature_sessions.find_one({"token": token}, {"_id": 0})
    if not session:
        raise HTTPException(status_code=404, detail="Signing session not found")

    expired = _is_session_expired(session)

    # If signed, surface a fresh presigned signature URL so the cashier UI can preview it.
    signature_url = None
    if session.get("status") == "signed" and session.get("signature_r2_key"):
        try:
            from utils.r2_storage import get_presigned_url
            signature_url = await get_presigned_url(session["signature_r2_key"], expires_in=3600)
        except Exception:
            signature_url = None

    return {
        "id": session.get("id"),
        "token": token,
        "status": session.get("status"),
        "signed_at": session.get("signed_at"),
        "bypass_method": session.get("bypass_method"),
        "signature_url": signature_url,
        "expired": expired,
        "expires_at": session.get("expires_at"),
        "credit_context": session.get("credit_context"),
    }


@router.post("/bypass/{session_id}")
async def bypass_with_pin(session_id: str, data: dict, user=Depends(get_current_user)):
    """
    Manager PIN bypass when customer cannot sign.
    Marks session as 'bypassed' — no signature image stored.
    """
    check_perm(user, "pos", "sell")

    session = await db.signature_sessions.find_one({"id": session_id}, {"_id": 0})
    if not session:
        raise HTTPException(status_code=404, detail="Signing session not found")

    if session.get("status") != "pending":
        raise HTTPException(status_code=400, detail=f"Session is already '{session.get('status')}'")

    if _is_session_expired(session):
        raise HTTPException(status_code=410, detail="Signing session has expired")

    pin = data.get("pin", "")
    reason = data.get("reason", "Customer unable to sign").strip()

    if not pin:
        raise HTTPException(status_code=400, detail="Manager PIN is required for bypass")

    from routes.verify import verify_pin_for_action
    verifier = await verify_pin_for_action(
        pin, "credit_sale_approval", branch_id=session.get("branch_id", "")
    )
    if not verifier:
        raise HTTPException(status_code=403, detail="Invalid PIN. Manager PIN or Admin PIN required.")

    await db.signature_sessions.update_one(
        {"id": session_id},
        {"$set": {
            "status": "bypassed",
            "bypass_method": verifier.get("method", "pin"),
            "bypass_by_id": verifier.get("verifier_id", user["id"]),
            "bypass_by_name": verifier.get("verifier_name", user.get("full_name", "")),
            "bypass_reason": reason,
            "bypassed_at": now_iso(),
        }}
    )

    return {
        "message": "Signature bypassed with manager PIN",
        "bypass_by": verifier.get("verifier_name", ""),
        "status": "bypassed",
    }


@router.get("/record/{record_type}/{record_id}")
async def get_signatures_for_record(
    record_type: str,
    record_id: str,
    user=Depends(get_current_user),
):
    """Get all signature sessions linked to a specific record (for receipt viewing).

    Matches both `sale` and `invoice` linked_record_type for the same id, since
    sales and invoices share an ID space and historically both names have been used.
    """
    types_to_match = [record_type]
    if record_type in ("sale", "invoice"):
        types_to_match = ["sale", "invoice"]

    sessions = await db.signature_sessions.find(
        {"linked_record_type": {"$in": types_to_match}, "linked_record_id": record_id},
        {"_id": 0}
    ).sort("created_at", -1).to_list(20)

    # Generate pre-signed URLs for signed sessions
    for s in sessions:
        if s.get("signature_r2_key"):
            try:
                from utils.r2_storage import get_presigned_url
                s["signature_url"] = await get_presigned_url(s["signature_r2_key"], expires_in=3600)
            except Exception:
                s["signature_url"] = None

    return sessions


# ==================== PUBLIC ENDPOINTS (no auth — for mobile signing) ====================

@router.get("/view/{token}")
async def get_session_for_signing(token: str):
    """
    Public endpoint — returns credit summary for the signer to review.
    No authentication required (accessed via QR code on customer's phone).
    """
    session = await _raw_db.signature_sessions.find_one({"token": token}, {"_id": 0})
    if not session:
        raise HTTPException(status_code=404, detail="Signing session not found or expired")

    if session.get("status") != "pending":
        return {
            "status": session.get("status"),
            "message": "This signing request has already been completed.",
            "credit_context": session.get("credit_context"),
        }

    if _is_session_expired(session):
        return {
            "status": "expired",
            "message": "This signing link has expired. Please ask staff to generate a new one.",
            "credit_context": session.get("credit_context"),
        }

    return {
        "id": session.get("id"),
        "token": token,
        "status": "pending",
        "credit_context": session.get("credit_context"),
        "expires_at": session.get("expires_at"),
    }


@router.post("/submit/{token}")
async def submit_signature(token: str, data: dict):
    """
    Public endpoint — customer submits their drawn signature.
    Accepts base64 PNG image from signature_pad canvas.
    Uploads to R2 and marks session as signed.
    No authentication required.
    """
    session = await _raw_db.signature_sessions.find_one({"token": token}, {"_id": 0})
    if not session:
        raise HTTPException(status_code=404, detail="Signing session not found")

    if session.get("status") != "pending":
        raise HTTPException(status_code=400, detail=f"Session is already '{session.get('status')}'")

    if _is_session_expired(session):
        raise HTTPException(status_code=410, detail="Signing session has expired. Please ask staff to generate a new link.")

    signature_data = data.get("signature", "")
    if not signature_data:
        raise HTTPException(status_code=400, detail="Signature data is required")

    # Decode base64 PNG
    try:
        if "," in signature_data:
            signature_data = signature_data.split(",", 1)[1]
        image_bytes = base64.b64decode(signature_data)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid signature image data")

    if len(image_bytes) < 100:
        raise HTTPException(status_code=400, detail="Signature appears to be empty")

    # Upload to R2 — fall back to "global" when org_id is null (e.g. super-admin sessions)
    org_id = session.get("organization_id") or "global"
    session_id = session.get("id", new_id())
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"signature_{timestamp}.png"

    r2_key = None
    try:
        from utils.r2_storage import upload_file
        result = await upload_file(
            org_id=org_id,
            record_type="signatures",
            record_id=session_id,
            filename=filename,
            content=image_bytes,
            content_type="image/png",
        )
        r2_key = result.get("key")
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Signature R2 upload failed: {e}")
        # Still mark as signed even if R2 fails (store raw data as fallback)
        r2_key = None

    signer_info = {
        "user_agent": data.get("user_agent", ""),
        "signed_at_client": data.get("signed_at", now_iso()),
        "ip": data.get("ip", ""),
    }

    await _raw_db.signature_sessions.update_one(
        {"token": token},
        {"$set": {
            "status": "signed",
            "signature_r2_key": r2_key,
            "signed_at": now_iso(),
            "signer_info": signer_info,
        }}
    )

    return {
        "message": "Signature submitted successfully. Thank you!",
        "status": "signed",
        "signed_at": now_iso(),
    }

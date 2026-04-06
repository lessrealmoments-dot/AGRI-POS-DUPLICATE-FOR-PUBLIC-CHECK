"""
AgriSmart Terminal — Device pairing, session management & WebSocket real-time.
YouTube TV-style login: terminal shows a 6-char code, PC enters it to pair.
WebSocket for instant pairing detection + PO/transfer push notifications.
"""
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from datetime import datetime, timezone, timedelta
import secrets
import string
import asyncio
from config import db, _raw_db
from utils import get_current_user, now_iso, new_id, create_token, verify_password
from routes.terminal_ws import terminal_ws_manager

router = APIRouter(prefix="/terminal", tags=["Terminal"])

CODE_CHARS = string.ascii_uppercase + string.digits
CODE_LENGTH = 6
CODE_EXPIRY_MINUTES = 5


def _generate_code() -> str:
    return ''.join(secrets.choice(CODE_CHARS) for _ in range(CODE_LENGTH))


# ── WebSocket Endpoints ─────────────────────────────────────────────────────

@router.websocket("/ws/pairing/{code}")
async def ws_pairing(websocket: WebSocket, code: str):
    """Terminal connects here while showing the pairing code. Gets instant notification when paired."""
    code = code.upper().strip()
    await terminal_ws_manager.connect_pairing(code, websocket)
    try:
        while True:
            # Keep connection alive — receive pings
            await websocket.receive_text()
    except WebSocketDisconnect:
        terminal_ws_manager.disconnect_pairing(code)


@router.websocket("/ws/terminal/{terminal_id}")
async def ws_terminal(websocket: WebSocket, terminal_id: str):
    """Connected terminal receives real-time events (PO assignments, transfers, etc.)."""
    await terminal_ws_manager.connect_terminal(terminal_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        terminal_ws_manager.disconnect_terminal(terminal_id)


# ── REST Endpoints ──────────────────────────────────────────────────────────

@router.post("/generate-code")
async def generate_pairing_code():
    """Terminal requests a pairing code. No auth required."""
    for _ in range(10):
        code = _generate_code()
        existing = await _raw_db.terminal_codes.find_one(
            {"code": code, "status": "pending", "expires_at": {"$gt": now_iso()}},
            {"_id": 0}
        )
        if not existing:
            break
    else:
        raise HTTPException(status_code=500, detail="Could not generate unique code")

    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=CODE_EXPIRY_MINUTES)).isoformat()
    doc = {
        "id": new_id(), "code": code, "status": "pending",
        "created_at": now_iso(), "expires_at": expires_at,
    }
    await _raw_db.terminal_codes.insert_one(doc)
    return {"code": code, "expires_in": CODE_EXPIRY_MINUTES * 60}


@router.get("/poll/{code}")
async def poll_pairing_status(code: str):
    """Fallback polling — terminal checks if code has been paired."""
    code = code.upper().strip()
    doc = await _raw_db.terminal_codes.find_one({"code": code}, {"_id": 0})
    if not doc:
        return {"status": "invalid"}

    if doc["status"] == "pending":
        expires = datetime.fromisoformat(doc["expires_at"].replace("Z", "+00:00"))
        if datetime.now(timezone.utc) > expires:
            await _raw_db.terminal_codes.update_one({"code": code}, {"$set": {"status": "expired"}})
            return {"status": "expired"}

    if doc["status"] == "paired":
        session = await _raw_db.terminal_sessions.find_one({"code": code}, {"_id": 0})
        if session:
            return {
                "status": "paired",
                "token": session["token"],
                "terminal_id": session["terminal_id"],
                "branch_id": session["branch_id"],
                "branch_name": session.get("branch_name", ""),
                "user_name": session.get("user_name", ""),
                "organization_id": session.get("organization_id", ""),
            }
    return {"status": doc["status"]}


@router.post("/pair")
async def pair_terminal(data: dict, user=Depends(get_current_user)):
    """PC user enters the 6-char code + selects a branch to pair."""
    code = (data.get("code") or "").upper().strip()
    branch_id = data.get("branch_id")

    if not code or len(code) != CODE_LENGTH:
        raise HTTPException(status_code=400, detail="Invalid pairing code")
    if not branch_id:
        raise HTTPException(status_code=400, detail="Branch ID required")

    # Non-admin users can only pair to their assigned branch
    if user.get("role") not in ("admin",) and user.get("branch_id") and user["branch_id"] != branch_id:
        raise HTTPException(status_code=403, detail="You can only pair a terminal to your assigned branch")

    doc = await _raw_db.terminal_codes.find_one({"code": code, "status": "pending"}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Code not found or already used")

    expires = datetime.fromisoformat(doc["expires_at"].replace("Z", "+00:00"))
    if datetime.now(timezone.utc) > expires:
        await _raw_db.terminal_codes.update_one({"code": code}, {"$set": {"status": "expired"}})
        raise HTTPException(status_code=410, detail="Code expired — generate a new one on the terminal")

    branch = await _raw_db.branches.find_one({"id": branch_id}, {"_id": 0})
    if not branch:
        raise HTTPException(status_code=404, detail="Branch not found")

    org_id = user.get("organization_id")
    terminal_id = new_id()
    token = create_token(user["id"], user["role"], org_id=org_id)

    session = {
        "id": new_id(), "terminal_id": terminal_id, "code": code,
        "branch_id": branch_id, "branch_name": branch.get("name", ""),
        "user_id": user["id"],
        "user_name": user.get("full_name", user.get("username", "")),
        "organization_id": org_id, "token": token,
        "status": "active", "paired_at": now_iso(), "last_seen": now_iso(),
    }
    await _raw_db.terminal_sessions.insert_one(session)
    await _raw_db.terminal_codes.update_one(
        {"code": code},
        {"$set": {"status": "paired", "paired_at": now_iso(), "terminal_id": terminal_id}}
    )

    # Real-time: instantly notify the pairing screen via WebSocket
    await terminal_ws_manager.notify_paired(code, {
        "token": token,
        "terminal_id": terminal_id,
        "branch_id": branch_id,
        "branch_name": branch.get("name", ""),
        "user_name": user.get("full_name", user.get("username", "")),
        "organization_id": org_id,
    })

    return {
        "message": f"Terminal paired to {branch.get('name', branch_id)}",
        "terminal_id": terminal_id,
        "branch_name": branch.get("name", ""),
    }


QR_TOKEN_EXPIRY_MINUTES = 10


@router.post("/initiate-qr-pairing")
async def initiate_qr_pairing(data: dict, user=Depends(get_current_user)):
    """PC generates a QR pair token tied to a branch. Mobile scans to auto-pair."""
    branch_id = data.get("branch_id")
    if not branch_id:
        raise HTTPException(status_code=400, detail="Branch ID required")

    # Non-admin users can only pair to their assigned branch
    if user.get("role") not in ("admin",) and user.get("branch_id") and user["branch_id"] != branch_id:
        raise HTTPException(status_code=403, detail="You can only pair a terminal to your assigned branch")

    branch = await _raw_db.branches.find_one({"id": branch_id}, {"_id": 0})
    if not branch:
        raise HTTPException(status_code=404, detail="Branch not found")

    token = secrets.token_urlsafe(32)
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=QR_TOKEN_EXPIRY_MINUTES)).isoformat()
    doc = {
        "id": new_id(), "token": token, "status": "pending",
        "branch_id": branch_id, "branch_name": branch.get("name", ""),
        "user_id": user["id"],
        "user_name": user.get("full_name", user.get("username", "")),
        "organization_id": user.get("organization_id"),
        "created_at": now_iso(), "expires_at": expires_at,
    }
    await _raw_db.qr_pair_tokens.insert_one(doc)
    return {"token": token, "branch_name": branch.get("name", ""), "expires_in": QR_TOKEN_EXPIRY_MINUTES * 60}


@router.post("/qr-pair")
async def qr_pair_terminal(data: dict):
    """Terminal submits a QR pair token to auto-pair. No auth required (token is proof)."""
    token = (data.get("token") or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="Token required")

    doc = await _raw_db.qr_pair_tokens.find_one({"token": token, "status": "pending"}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Invalid or expired QR code — generate a new one")

    expires = datetime.fromisoformat(doc["expires_at"].replace("Z", "+00:00"))
    if datetime.now(timezone.utc) > expires:
        await _raw_db.qr_pair_tokens.update_one({"token": token}, {"$set": {"status": "expired"}})
        raise HTTPException(status_code=410, detail="QR code expired — generate a new one on the PC")

    # Create terminal session — use the initiating user's actual role, never hardcode admin
    terminal_id = new_id()
    initiating_user = await _raw_db.users.find_one({"id": doc["user_id"]}, {"_id": 0, "role": 1})
    actual_role = initiating_user.get("role", "manager") if initiating_user else "manager"
    session_token = create_token(doc["user_id"], actual_role, org_id=doc.get("organization_id"))
    device_id = (data.get("device_id") or "").strip()
    session = {
        "id": new_id(), "terminal_id": terminal_id, "code": f"QR-{token[:8]}",
        "branch_id": doc["branch_id"], "branch_name": doc["branch_name"],
        "user_id": doc["user_id"], "user_name": doc.get("user_name", ""),
        "organization_id": doc.get("organization_id"), "token": session_token,
        "status": "active", "paired_at": now_iso(), "last_seen": now_iso(),
        "paired_via": "qr",
        "device_id": device_id,
    }
    await _raw_db.terminal_sessions.insert_one(session)
    await _raw_db.qr_pair_tokens.update_one(
        {"token": token},
        {"$set": {"status": "paired", "paired_at": now_iso(), "terminal_id": terminal_id}}
    )

    return {
        "status": "paired",
        "token": session_token,
        "terminal_id": terminal_id,
        "branch_id": doc["branch_id"],
        "branch_name": doc["branch_name"],
        "user_name": doc.get("user_name", ""),
        "organization_id": doc.get("organization_id", ""),
    }


@router.post("/credential-pair")
async def credential_pair_terminal(data: dict):
    """Login directly on the terminal with email + password.
    Manager → auto-links to their assigned branch.
    Admin → requires branch_id selection (returned branches list if not provided)."""
    email = (data.get("email") or "").strip().lower()
    password = data.get("password", "")
    branch_id = data.get("branch_id")

    if not email or not password:
        raise HTTPException(status_code=400, detail="Email and password are required")

    user = await _raw_db.users.find_one({"email": email, "active": True}, {"_id": 0})
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not verify_password(password, user.get("password_hash", "")):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    is_admin = user.get("role") == "admin" or user.get("is_super_admin")
    org_id = user.get("organization_id")

    # Manager / non-admin: must have an assigned branch
    if not is_admin:
        user_branch = user.get("branch_id")
        if not user_branch:
            raise HTTPException(status_code=400, detail="Your account has no branch assigned. Contact your admin.")
        branch_id = user_branch

    # Admin without branch_id → return available branches for selection
    if is_admin and not branch_id:
        query = {}
        if org_id:
            query["organization_id"] = org_id
        branches = await _raw_db.branches.find(query, {"_id": 0, "id": 1, "name": 1}).to_list(50)
        return {
            "status": "select_branch",
            "branches": branches,
            "user_name": user.get("full_name", user.get("username", "")),
            "is_admin": True,
        }

    # Validate branch exists
    branch = await _raw_db.branches.find_one({"id": branch_id}, {"_id": 0})
    if not branch:
        raise HTTPException(status_code=404, detail="Branch not found")

    # Create terminal session
    terminal_id = new_id()
    token = create_token(user["id"], user["role"], org_id=org_id)
    device_id = (data.get("device_id") or "").strip()

    session = {
        "id": new_id(), "terminal_id": terminal_id, "code": f"CRED-{email[:8]}",
        "branch_id": branch_id, "branch_name": branch.get("name", ""),
        "user_id": user["id"],
        "user_name": user.get("full_name", user.get("username", "")),
        "organization_id": org_id, "token": token,
        "status": "active", "paired_at": now_iso(), "last_seen": now_iso(),
        "paired_via": "credential",
        "is_admin": is_admin,
        "device_id": device_id,
    }
    await _raw_db.terminal_sessions.insert_one(session)

    return {
        "status": "paired",
        "token": token,
        "terminal_id": terminal_id,
        "branch_id": branch_id,
        "branch_name": branch.get("name", ""),
        "user_name": user.get("full_name", user.get("username", "")),
        "organization_id": org_id or "",
        "is_admin": is_admin,
    }


@router.get("/branches")
async def list_branches_for_terminal(user=Depends(get_current_user)):
    """List available branches for admin terminal selection."""
    org_id = user.get("organization_id")
    query = {}
    if org_id:
        query["organization_id"] = org_id
    branches = await _raw_db.branches.find(query, {"_id": 0, "id": 1, "name": 1}).to_list(50)
    return branches


@router.get("/session")
async def get_terminal_session(user=Depends(get_current_user)):
    """Validate current terminal session."""
    session = await _raw_db.terminal_sessions.find_one(
        {"user_id": user["id"], "status": "active"},
        {"_id": 0}, sort=[("paired_at", -1)]
    )
    if not session:
        raise HTTPException(status_code=404, detail="No active terminal session")
    await _raw_db.terminal_sessions.update_one(
        {"terminal_id": session["terminal_id"]}, {"$set": {"last_seen": now_iso()}}
    )
    branch = await _raw_db.branches.find_one({"id": session["branch_id"]}, {"_id": 0})
    return {
        "terminal_id": session["terminal_id"],
        "branch_id": session["branch_id"],
        "branch_name": session.get("branch_name", branch.get("name", "") if branch else ""),
        "user_name": session.get("user_name", ""),
        "paired_at": session["paired_at"],
    }


@router.post("/bind-device")
async def bind_device_to_session(data: dict):
    """
    Bind a physical device ID to an active terminal session.
    Called by TerminalShell on first load — handles code-based pairing where
    the terminal couldn't send device_id during the pairing flow (admin initiated it).
    Idempotent: safe to call multiple times with the same device_id.
    No auth required — terminal_id + token are used as proof of identity.
    """
    terminal_id = (data.get("terminal_id") or "").strip()
    device_id   = (data.get("device_id")   or "").strip()

    if not terminal_id or not device_id:
        raise HTTPException(status_code=400, detail="terminal_id and device_id required")

    session = await _raw_db.terminal_sessions.find_one(
        {"terminal_id": terminal_id, "status": "active"}, {"_id": 0, "device_id": 1}
    )
    if not session:
        raise HTTPException(status_code=404, detail="Active terminal session not found")

    stored_device_id = session.get("device_id", "")

    # If session already has a device_id that differs from what's being sent → reject
    # (prevents a different device from hijacking an already-bound session)
    if stored_device_id and stored_device_id != device_id:
        raise HTTPException(
            status_code=403,
            detail="This terminal session is already bound to a different device. Re-pair to link a new device."
        )

    # Bind device_id (or update if unbound)
    await _raw_db.terminal_sessions.update_one(
        {"terminal_id": terminal_id},
        {"$set": {"device_id": device_id, "last_seen": now_iso()}}
    )
    return {"bound": True, "terminal_id": terminal_id}


@router.get("/active")
async def list_active_terminals(user=Depends(get_current_user)):
    """List active terminal sessions for the org."""
    org_id = user.get("organization_id")
    query = {"status": "active"}
    if org_id:
        query["organization_id"] = org_id
    sessions = await _raw_db.terminal_sessions.find(
        query, {"_id": 0, "token": 0}
    ).sort("paired_at", -1).to_list(50)
    return sessions


@router.post("/disconnect/{terminal_id}")
async def disconnect_terminal(terminal_id: str, user=Depends(get_current_user)):
    """Disconnect a terminal."""
    result = await _raw_db.terminal_sessions.update_one(
        {"terminal_id": terminal_id},
        {"$set": {"status": "disconnected", "disconnected_at": now_iso()}}
    )
    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="Terminal not found")
    terminal_ws_manager.disconnect_terminal(terminal_id)
    return {"message": "Terminal disconnected"}



@router.post("/refresh-token")
async def refresh_terminal_token(user=Depends(get_current_user)):
    """Refresh the terminal JWT token. Called periodically by the terminal to stay connected."""
    session = await _raw_db.terminal_sessions.find_one(
        {"user_id": user["id"], "status": "active"},
        {"_id": 0}, sort=[("paired_at", -1)]
    )
    if not session:
        raise HTTPException(status_code=404, detail="No active terminal session")

    org_id = session.get("organization_id")
    new_token = create_token(user["id"], user["role"], org_id=org_id)

    await _raw_db.terminal_sessions.update_one(
        {"terminal_id": session["terminal_id"]},
        {"$set": {"token": new_token, "last_seen": now_iso()}}
    )

    return {"token": new_token, "expires_in": 86400}



# ── Send PO / Transfer to Terminal ──────────────────────────────────────────

@router.post("/notify")
async def send_notification_to_terminal(data: dict, user=Depends(get_current_user)):
    """
    Send a real-time notification to all active terminals for a branch.
    Used by PC when sending a PO or transfer for terminal checking.
    Body: { branch_id, event_type: "po_assigned"|"transfer_assigned", payload: {...} }
    """
    branch_id = data.get("branch_id")
    event_type = data.get("event_type")
    payload = data.get("payload", {})

    if not branch_id or not event_type:
        raise HTTPException(status_code=400, detail="branch_id and event_type required")

    # Find all active terminals for this branch
    terminals = await _raw_db.terminal_sessions.find(
        {"branch_id": branch_id, "status": "active"},
        {"_id": 0, "terminal_id": 1}
    ).to_list(20)

    notified = 0
    for t in terminals:
        tid = t["terminal_id"]
        await terminal_ws_manager.notify_terminal(tid, event_type, payload)
        notified += 1

    return {"notified": notified, "terminal_count": len(terminals)}



# ── Terminal Pull (Self-Serve) ──────────────────────────────────────────────

@router.get("/available-pos")
async def list_available_pos(branch_id: str = None, user=Depends(get_current_user)):
    """List POs that the terminal can pull (ordered/draft/in_progress, not yet on terminal)."""
    if not branch_id:
        raise HTTPException(status_code=400, detail="branch_id required")
    query = {
        "branch_id": branch_id,
        "status": {"$in": ["draft", "ordered", "in_progress"]},
    }
    pos = await db.purchase_orders.find(
        query, {"_id": 0, "id": 1, "po_number": 1, "vendor": 1, "status": 1,
                "items": 1, "purchase_date": 1, "created_at": 1}
    ).sort("created_at", -1).to_list(50)
    for po in pos:
        po["item_count"] = len(po.get("items", []))
        po.pop("items", None)  # Don't send full items in list
    return pos


@router.get("/available-transfers")
async def list_available_transfers(branch_id: str = None, user=Depends(get_current_user)):
    """List transfers that the terminal can pull (sent status, destination = this branch)."""
    if not branch_id:
        raise HTTPException(status_code=400, detail="branch_id required")
    query = {
        "to_branch_id": branch_id,
        "status": "sent",
    }
    transfers = await db.transfers.find(
        query, {"_id": 0, "id": 1, "order_number": 1, "from_branch_id": 1,
                "to_branch_id": 1, "status": 1, "items": 1, "created_at": 1,
                "total_at_transfer_capital": 1, "total_at_branch_retail": 1}
    ).sort("created_at", -1).to_list(50)
    # Resolve branch names
    branch_ids = set()
    for t in transfers:
        branch_ids.add(t.get("from_branch_id", ""))
    branches = {}
    async for b in _raw_db.branches.find({"id": {"$in": list(branch_ids)}}, {"_id": 0, "id": 1, "name": 1}):
        branches[b["id"]] = b["name"]
    for t in transfers:
        t["from_branch_name"] = branches.get(t.get("from_branch_id", ""), "?")
        t["item_count"] = len(t.get("items", []))
        t.pop("items", None)
    return transfers


@router.post("/pull-po")
async def pull_po(data: dict, user=Depends(get_current_user)):
    """Terminal pulls a PO for checking. Requires PIN — branch-restricted for managers."""
    po_id = data.get("po_id")
    pin = str(data.get("pin", ""))
    if not po_id:
        raise HTTPException(status_code=400, detail="po_id required")
    if not pin:
        raise HTTPException(status_code=400, detail="PIN required")

    # Fetch PO first so we can apply branch restriction to the PIN check
    po = await db.purchase_orders.find_one({"id": po_id}, {"_id": 0})
    if not po:
        raise HTTPException(status_code=404, detail="PO not found")
    if po["status"] not in ("draft", "ordered", "in_progress"):
        raise HTTPException(status_code=400, detail=f"Cannot pull PO with status '{po['status']}'")

    # Verify PIN — branch-restricted: manager PIN only works for this PO's branch
    from routes.verify import verify_pin_for_action
    verifier = await verify_pin_for_action(pin, "terminal_pull", branch_id=po.get("branch_id", ""))
    if not verifier:
        raise HTTPException(status_code=403, detail="Invalid PIN — use your branch manager PIN, admin PIN, or time-based PIN")

    await db.purchase_orders.update_one(
        {"id": po_id},
        {"$set": {
            "status": "sent_to_terminal",
            "sent_to_terminal_at": now_iso(),
            "sent_to_terminal_by": f"Terminal ({verifier.get('verifier_name', 'PIN')})",
            "pulled_by_terminal": True,
        }}
    )
    return {"message": f"PO {po.get('po_number', '')} pulled to terminal", "verified_by": verifier.get("verifier_name", "")}


@router.post("/pull-transfer")
async def pull_transfer(data: dict, user=Depends(get_current_user)):
    """Terminal pulls a transfer for checking. Requires PIN — branch-restricted for managers."""
    transfer_id = data.get("transfer_id")
    pin = str(data.get("pin", ""))
    if not transfer_id:
        raise HTTPException(status_code=400, detail="transfer_id required")
    if not pin:
        raise HTTPException(status_code=400, detail="PIN required")

    # Fetch transfer first for branch restriction
    transfer = await db.transfers.find_one({"id": transfer_id}, {"_id": 0})
    if not transfer:
        raise HTTPException(status_code=404, detail="Transfer not found")
    if transfer["status"] != "sent":
        raise HTTPException(status_code=400, detail=f"Cannot pull transfer with status '{transfer['status']}'")

    # Branch restriction: manager PIN must belong to the receiving branch (to_branch_id)
    from routes.verify import verify_pin_for_action
    verifier = await verify_pin_for_action(pin, "terminal_pull", branch_id=transfer.get("to_branch_id", ""))
    if not verifier:
        raise HTTPException(status_code=403, detail="Invalid PIN — use your branch manager PIN, admin PIN, or time-based PIN")

    await db.transfers.update_one(
        {"id": transfer_id},
        {"$set": {
            "status": "sent_to_terminal",
            "sent_to_terminal_at": now_iso(),
            "sent_to_terminal_by": f"Terminal ({verifier.get('verifier_name', 'PIN')})",
            "pulled_by_terminal": True,
        }}
    )
    return {"message": f"Transfer {transfer.get('order_number', '')} pulled to terminal", "verified_by": verifier.get("verifier_name", "")}

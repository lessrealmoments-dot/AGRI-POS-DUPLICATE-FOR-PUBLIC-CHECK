"""
Security tracking: failed PIN/TOTP attempt logging and brute-force alerting.

Two layers:
 1. Authenticated users (cashier, manager, etc.) — existing log_failed_pin_attempt
 2. Anonymous QR endpoints — log_failed_qr_pin_attempt + check_qr_lockout

When threshold is crossed: security notification to all admins + security_events entry.
"""
from datetime import datetime, timezone, timedelta
from config import db
from utils.helpers import new_id, now_iso

ATTEMPT_THRESHOLD = 5          # alert after this many failures
WINDOW_MINUTES = 30            # rolling time window

# QR-specific constants (unauthenticated endpoints)
QR_FAIL_WARN_THRESHOLD   = 5   # alert admins after this many failures per doc_code
QR_FAIL_LOCK_THRESHOLD   = 10  # lock the doc_code after this many failures
QR_LOCK_WINDOW_MINUTES   = 15  # rolling window for failure counting
QR_LOCK_DURATION_MINUTES = 15  # how long a lockout lasts after the last failure

# Draft-QR-specific lockout (stricter — 3 wrong PINs → 3 min; 6 total → permanent disable)
DRAFT_QR_FAIL_THRESHOLD   = 3   # wrong PINs before lockout
DRAFT_QR_LOCK_MINUTES     = 3   # lockout duration in minutes
DRAFT_QR_DISABLE_TOTAL    = 6   # total wrong PINs across all sessions → permanent disable


async def check_draft_qr_lockout(doc_code: str) -> dict:
    """
    Draft-specific QR lockout: 3 wrong PINs → 3-min lock.
    Counts all failures with action='update_draft' for this doc_code
    since the last successful attempt.

    Returns same shape as check_qr_lockout():
      { locked, failure_count, retry_after, warn, attempts_remaining,
        permanently_disabled }
    """
    from datetime import datetime, timezone, timedelta
    window_start = (
        datetime.now(timezone.utc) - timedelta(minutes=60)   # 1-hour rolling window
    ).isoformat()

    last_success = await db.pin_attempt_log.find_one(
        {"doc_code": doc_code, "action": "update_draft", "success": True},
        {"_id": 0, "attempted_at": 1},
        sort=[("attempted_at", -1)],
    )
    count_from = max(window_start, last_success["attempted_at"] if last_success else window_start)

    recent_failures = await db.pin_attempt_log.count_documents({
        "doc_code":     doc_code,
        "action":       "update_draft",
        "success":      False,
        "attempted_at": {"$gte": count_from},
    })

    # Lifetime failures check (for permanent disable)
    lifetime_failures = await db.pin_attempt_log.count_documents({
        "doc_code": doc_code,
        "action":   "update_draft",
        "success":  False,
    })

    attempts_remaining = max(0, DRAFT_QR_FAIL_THRESHOLD - recent_failures)
    warn = recent_failures >= (DRAFT_QR_FAIL_THRESHOLD - 1)   # warn on last attempt

    if lifetime_failures >= DRAFT_QR_DISABLE_TOTAL:
        return {
            "locked": True, "failure_count": recent_failures,
            "retry_after": 0, "warn": True, "attempts_remaining": 0,
            "permanently_disabled": True,
        }

    if recent_failures < DRAFT_QR_FAIL_THRESHOLD:
        return {
            "locked": False, "failure_count": recent_failures,
            "retry_after": 0, "warn": warn,
            "attempts_remaining": attempts_remaining,
            "permanently_disabled": False,
        }

    # Locked — find most recent failure to compute unlock time
    latest = await db.pin_attempt_log.find_one(
        {"doc_code": doc_code, "action": "update_draft", "success": False,
         "attempted_at": {"$gte": count_from}},
        {"_id": 0, "attempted_at": 1},
        sort=[("attempted_at", -1)],
    )
    if not latest:
        return {"locked": False, "failure_count": recent_failures,
                "retry_after": 0, "warn": True, "attempts_remaining": 0,
                "permanently_disabled": False}

    ts = latest["attempted_at"]
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    latest_time = datetime.fromisoformat(ts)
    if latest_time.tzinfo is None:
        latest_time = latest_time.replace(tzinfo=timezone.utc)

    unlock_time = latest_time + timedelta(minutes=DRAFT_QR_LOCK_MINUTES)
    retry_after = max(0, int((unlock_time - datetime.now(timezone.utc)).total_seconds()))

    if retry_after <= 0:
        return {"locked": False, "failure_count": recent_failures,
                "retry_after": 0, "warn": True, "attempts_remaining": 0,
                "permanently_disabled": False}

    return {"locked": True, "failure_count": recent_failures,
            "retry_after": retry_after, "warn": True, "attempts_remaining": 0,
            "permanently_disabled": False}


async def log_failed_pin_attempt(user: dict, context: str, attempt_type: str, doc_id: str = "", doc_type: str = ""):
    """
    Log a failed PIN/TOTP attempt for the authenticated user.
    Triggers a security alert to admins when threshold is reached.

    Args:
        user:         The currently logged-in user (who submitted the wrong PIN)
        context:      Human-readable description of what they were trying to do
        attempt_type: One of "transaction_verify" | "fund_transfer" | "admin_action"
        doc_id:       Optional UUID of the related document (PO, transfer, etc.)
        doc_type:     Optional document type (purchase_order, branch_transfer, etc.)
    """
    user_id   = user.get("id", "unknown")
    user_name = user.get("full_name") or user.get("username") or "Unknown User"
    branch_id = user.get("branch_id")

    # 1. Log the attempt
    await db.pin_attempt_log.insert_one({
        "id":           new_id(),
        "user_id":      user_id,
        "user_name":    user_name,
        "attempt_type": attempt_type,
        "context":      context,
        "branch_id":    branch_id,
        "success":      False,
        "attempted_at": now_iso(),
    })

    # 2. Count failures for this user in the rolling window
    window_start = (datetime.now(timezone.utc) - timedelta(minutes=WINDOW_MINUTES)).isoformat()
    recent_failures = await db.pin_attempt_log.count_documents({
        "user_id":      user_id,
        "success":      False,
        "attempted_at": {"$gte": window_start},
    })

    # 3. Alert on threshold and every subsequent attempt (5, 6, 7, ...)
    if recent_failures >= ATTEMPT_THRESHOLD:
        await _raise_security_alert(user_id, user_name, branch_id, recent_failures, context, attempt_type, user=user, doc_id=doc_id, doc_type=doc_type)


async def log_successful_pin_attempt(user: dict, context: str, attempt_type: str):
    """
    Log a successful PIN attempt (for audit completeness).
    Optionally useful for anomaly detection later.
    """
    await db.pin_attempt_log.insert_one({
        "id":           new_id(),
        "user_id":      user.get("id", "unknown"),
        "user_name":    user.get("full_name") or user.get("username") or "Unknown",
        "attempt_type": attempt_type,
        "context":      context,
        "branch_id":    user.get("branch_id"),
        "success":      True,
        "attempted_at": now_iso(),
    })


async def _raise_security_alert(user_id, user_name, branch_id, failure_count, context, attempt_type, user: dict = None, doc_id: str = "", doc_type: str = ""):
    """
    Create a security notification for admins and log to security_events.
    Only fires once per threshold crossing (every attempt at/above threshold).
    Deduplication: skip if an identical alert was sent in the last 5 minutes.
    """
    dedup_window = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    recent_alert = await db.security_events.find_one({
        "user_id":    user_id,
        "event_type": "failed_pin_brute_force",
        "created_at": {"$gte": dedup_window},
    })
    if recent_alert:
        return  # Already alerted recently — don't spam

    type_labels = {
        "transaction_verify": "Transaction Verification",
        "fund_transfer":      "Fund Transfer",
        "admin_action":       "Admin Action Authorization",
    }
    action_label = type_labels.get(attempt_type, attempt_type)

    # --- Resolve branch name from branch_id ---
    branch_name = ""
    if branch_id:
        branch_doc = await db.branches.find_one({"id": branch_id}, {"_id": 0, "name": 1})
        if branch_doc:
            branch_name = branch_doc.get("name", "")

    # --- Enrich user details from the user dict or a DB lookup ---
    user_role  = ""
    user_email = ""
    if user:
        user_role  = user.get("role", "")
        user_email = user.get("email", "")
    elif user_id and user_id != "unknown":
        user_doc = await db.users.find_one({"id": user_id}, {"_id": 0, "role": 1, "email": 1})
        if user_doc:
            user_role  = user_doc.get("role", "")
            user_email = user_doc.get("email", "")

    # --- Audit log entry ---
    event = {
        "id":            new_id(),
        "event_type":    "failed_pin_brute_force",
        "user_id":       user_id,
        "user_name":     user_name,
        "user_role":     user_role,
        "user_email":    user_email,
        "branch_id":     branch_id,
        "branch_name":   branch_name,
        "failure_count": failure_count,
        "attempt_type":  attempt_type,
        "context":       context,
        "severity":      "high" if failure_count >= 10 else "medium",
        "created_at":    now_iso(),
    }
    await db.security_events.insert_one(event)

    # --- Notification to all admins ---
    admins = await db.users.find(
        {"role": "admin", "active": True}, {"_id": 0, "id": 1}
    ).to_list(50)
    target_ids = [a["id"] for a in admins]

    severity_label = "URGENT" if failure_count >= 10 else "Warning"
    role_label     = f" ({user_role.title()})" if user_role else ""
    branch_label   = f" at {branch_name}" if branch_name else ""

    await db.notifications.insert_one({
        "id":        new_id(),
        "type":      "security_alert",
        "category":  "security",
        "severity":  "critical" if failure_count >= 10 else "warning",
        "title":     f"Security {severity_label}: Repeated Wrong PIN",
        "message":   (
            f"{user_name}{role_label} entered the wrong PIN {failure_count}x"
            f"{branch_label} — {action_label}: {context}"
        ),
        "branch_id":       branch_id,
        "branch_name":     branch_name,
        "metadata": {
            "alert_source":  "authenticated_pin",
            "user_id":       user_id,
            "user_name":     user_name,
            "user_role":     user_role,
            "user_email":    user_email,
            "branch_name":   branch_name,
            "failure_count": failure_count,
            "attempt_type":  attempt_type,
            "action_label":  action_label,
            "context":       context,
            "doc_id":        doc_id,
            "doc_type":      doc_type,
            "severity":      "high" if failure_count >= 10 else "medium",
        },
        "target_user_ids": target_ids,
        "read_by":         [],
        "created_at":      now_iso(),
    })


# ─────────────────────────────────────────────────────────────────────────────
#  QR-specific brute-force protection (anonymous callers — no user session)
# ─────────────────────────────────────────────────────────────────────────────

async def check_qr_lockout(doc_code: str) -> dict:
    """
    Check if a doc_code is currently locked due to too many failed PIN attempts.
    Counts failures since the last successful attempt (auto-reset on success).

    Returns:
      { locked, failure_count, retry_after (seconds), warn, attempts_remaining }
    """
    window_start = (
        datetime.now(timezone.utc) - timedelta(minutes=QR_LOCK_WINDOW_MINUTES)
    ).isoformat()

    # A successful attempt resets the failure counter for this doc_code
    last_success = await db.pin_attempt_log.find_one(
        {"doc_code": doc_code, "success": True},
        {"_id": 0, "attempted_at": 1},
        sort=[("attempted_at", -1)],
    )
    count_from = max(
        window_start,
        last_success["attempted_at"] if last_success else window_start,
    )

    recent_failures = await db.pin_attempt_log.count_documents({
        "doc_code":     doc_code,
        "success":      False,
        "attempted_at": {"$gte": count_from},
    })

    attempts_remaining = max(0, QR_FAIL_LOCK_THRESHOLD - recent_failures)
    warn = recent_failures >= QR_FAIL_WARN_THRESHOLD

    if recent_failures < QR_FAIL_LOCK_THRESHOLD:
        return {
            "locked": False, "failure_count": recent_failures,
            "retry_after": 0, "warn": warn,
            "attempts_remaining": attempts_remaining,
        }

    # Locked — compute retry_after from the most recent failure
    latest = await db.pin_attempt_log.find_one(
        {"doc_code": doc_code, "success": False, "attempted_at": {"$gte": count_from}},
        {"_id": 0, "attempted_at": 1},
        sort=[("attempted_at", -1)],
    )
    if not latest:
        return {"locked": False, "failure_count": recent_failures,
                "retry_after": 0, "warn": True, "attempts_remaining": 0}

    ts = latest["attempted_at"]
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    latest_time = datetime.fromisoformat(ts)
    if latest_time.tzinfo is None:
        latest_time = latest_time.replace(tzinfo=timezone.utc)

    unlock_time  = latest_time + timedelta(minutes=QR_LOCK_DURATION_MINUTES)
    retry_after  = max(0, int((unlock_time - datetime.now(timezone.utc)).total_seconds()))

    if retry_after <= 0:
        return {"locked": False, "failure_count": recent_failures,
                "retry_after": 0, "warn": True, "attempts_remaining": 0}

    return {"locked": True, "failure_count": recent_failures,
            "retry_after": retry_after, "warn": True, "attempts_remaining": 0}


async def log_failed_qr_pin_attempt(
    doc_code: str, doc_type: str, action: str,
    client_ip: str = "", branch_id: str = "", terminal_id: str = "",
):
    """Log a failed PIN attempt from an unauthenticated QR endpoint."""
    await db.pin_attempt_log.insert_one({
        "id":           new_id(),
        "user_id":      "anonymous",
        "user_name":    f"Anonymous (QR:{doc_code})",
        "attempt_type": "qr_action",
        "context":      f"QR {action} on {doc_type} {doc_code}",
        "doc_code":     doc_code,
        "doc_type":     doc_type,
        "action":       action,
        "client_ip":    client_ip,
        "terminal_id":  terminal_id,
        "branch_id":    branch_id,
        "success":      False,
        "attempted_at": now_iso(),
    })

    # Recount using the same window used by check_qr_lockout
    last_success = await db.pin_attempt_log.find_one(
        {"doc_code": doc_code, "success": True},
        {"_id": 0, "attempted_at": 1},
        sort=[("attempted_at", -1)],
    )
    window_start = (
        datetime.now(timezone.utc) - timedelta(minutes=QR_LOCK_WINDOW_MINUTES)
    ).isoformat()
    count_from = max(
        window_start,
        last_success["attempted_at"] if last_success else window_start,
    )
    recent_failures = await db.pin_attempt_log.count_documents({
        "doc_code": doc_code, "success": False,
        "attempted_at": {"$gte": count_from},
    })

    if recent_failures >= QR_FAIL_WARN_THRESHOLD:
        await _raise_qr_security_alert(
            doc_code, doc_type, action, client_ip, branch_id, recent_failures,
            terminal_id=terminal_id,
        )


async def log_successful_qr_pin_attempt(
    doc_code: str, doc_type: str, action: str,
    verifier_name: str = "", client_ip: str = "",
):
    """Log a successful QR PIN — resets the failure counter for this doc_code."""
    await db.pin_attempt_log.insert_one({
        "id":           new_id(),
        "user_id":      "anonymous",
        "user_name":    verifier_name or "Anonymous",
        "attempt_type": "qr_action",
        "context":      f"QR {action} on {doc_code} — SUCCESS",
        "doc_code":     doc_code,
        "doc_type":     doc_type,
        "action":       action,
        "client_ip":    client_ip,
        "success":      True,
        "attempted_at": now_iso(),
    })


async def _raise_qr_security_alert(
    doc_code: str, doc_type: str, action: str,
    client_ip: str, branch_id: str, failure_count: int,
    terminal_id: str = "",
):
    """Fire a security_events entry + admin notification for QR PIN brute-force."""
    dedup_window = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    already = await db.security_events.find_one({
        "doc_code":   doc_code,
        "event_type": "qr_pin_brute_force",
        "created_at": {"$gte": dedup_window},
    })
    if already:
        return

    locked   = failure_count >= QR_FAIL_LOCK_THRESHOLD
    severity = "high" if locked else "medium"

    # ── Resolve terminal identity (branch name, device label) ─────────────────
    branch_name   = ""
    terminal_label = f"IP: {client_ip or 'unknown'}"  # fallback for non-terminal callers

    if terminal_id:
        from config import _raw_db
        t_session = await _raw_db.terminal_sessions.find_one(
            {"terminal_id": terminal_id, "status": "active"},
            {"_id": 0, "branch_name": 1, "branch_id": 1}
        )
        if t_session:
            branch_name    = t_session.get("branch_name", "")
            terminal_label = f"AgriSmart Terminal at {branch_name}" if branch_name else "AgriSmart Terminal"
        else:
            terminal_label = "AgriSmart Terminal (session expired)"
    elif branch_id:
        branch_doc  = await db.branches.find_one({"id": branch_id}, {"_id": 0, "name": 1})
        branch_name = branch_doc.get("name", "") if branch_doc else ""

    # ── Resolve document details ───────────────────────────────────────────────
    doc_number   = doc_code
    doc_id_meta  = ""        # UUID for ReviewDetailDialog
    counterparty = ""
    doc_amount   = None

    try:
        dc = await db.doc_codes.find_one({"code": doc_code}, {"_id": 0, "doc_id": 1})
        if dc:
            doc_id_meta = dc["doc_id"]

        if doc_type == "invoice" and doc_id_meta:
            inv = await db.invoices.find_one({"id": doc_id_meta},
                {"_id": 0, "invoice_number": 1, "customer_name": 1, "grand_total": 1})
            if inv:
                doc_number   = inv.get("invoice_number", doc_code)
                counterparty = inv.get("customer_name", "")
                doc_amount   = inv.get("grand_total")

        elif doc_type == "purchase_order" and doc_id_meta:
            po = await db.purchase_orders.find_one({"id": doc_id_meta},
                {"_id": 0, "po_number": 1, "supplier_name": 1, "grand_total": 1})
            if po:
                doc_number   = po.get("po_number", doc_code)
                counterparty = po.get("supplier_name", "")
                doc_amount   = po.get("grand_total")

        elif doc_type == "branch_transfer" and doc_id_meta:
            bt = await db.branch_transfers.find_one({"id": doc_id_meta},
                {"_id": 0, "transfer_number": 1, "from_branch_name": 1, "to_branch_name": 1})
            if bt:
                doc_number   = bt.get("transfer_number", doc_code)
                counterparty = f"{bt.get('from_branch_name','')} → {bt.get('to_branch_name','')}"
    except Exception:
        pass  # Doc enrichment is best-effort; never block the alert

    # ── Action label ──────────────────────────────────────────────────────────
    action_labels = {
        "release_stocks":  "Release Stocks",
        "receive_payment": "Receive Payment",
        "transfer_receive": "Receive Transfer",
        "verify_pin":      "Verify PIN",
    }
    action_label = action_labels.get(action, action.replace("_", " ").title())

    # ── Build message ─────────────────────────────────────────────────────────
    amount_str = f" (₱{doc_amount:,.2f})" if doc_amount is not None else ""
    party_str  = f" · {counterparty}" if counterparty else ""
    lock_note  = " The document has been temporarily locked for 15 minutes." if locked else ""

    message = (
        f"{terminal_label} entered the wrong PIN {failure_count}x on "
        f"{doc_number}{party_str}{amount_str} — {action_label}.{lock_note}"
    )

    await db.security_events.insert_one({
        "id":            new_id(),
        "event_type":    "qr_pin_brute_force",
        "user_id":       "anonymous",
        "user_name":     terminal_label,
        "doc_code":      doc_code,
        "doc_type":      doc_type,
        "doc_number":    doc_number,
        "counterparty":  counterparty,
        "doc_amount":    doc_amount,
        "action":        action,
        "client_ip":     client_ip,
        "terminal_id":   terminal_id,
        "branch_id":     branch_id,
        "branch_name":   branch_name,
        "failure_count": failure_count,
        "attempt_type":  "qr_action",
        "context":       f"QR PIN brute-force on {doc_type} {doc_code}",
        "severity":      severity,
        "locked":        locked,
        "created_at":    now_iso(),
    })

    admins = await db.users.find(
        {"role": "admin", "active": True}, {"_id": 0, "id": 1}
    ).to_list(50)

    severity_label = "URGENT — Document Locked" if locked else "Warning"

    await db.notifications.insert_one({
        "id":        new_id(),
        "type":      "security_alert",
        "category":  "security",
        "severity":  "critical" if locked else "warning",
        "title":     f"QR Security {severity_label}: Wrong PIN on {doc_number}",
        "message":   message,
        "branch_id":   branch_id,
        "branch_name": branch_name,
        "metadata": {
            "alert_source":   "qr_terminal",
            "terminal_id":    terminal_id,
            "terminal_label": terminal_label,
            "doc_code":       doc_code,
            "doc_type":       doc_type,
            "doc_id":         doc_id_meta,
            "doc_number":     doc_number,
            "counterparty":   counterparty,
            "doc_amount":     doc_amount,
            "action":         action,
            "action_label":   action_label,
            "client_ip":      client_ip,
            "branch_name":    branch_name,
            "failure_count":  failure_count,
            "severity":       severity,
            "locked":         locked,
        },
        "target_user_ids": [a["id"] for a in admins],
        "read_by":         [],
        "created_at":      now_iso(),
    })

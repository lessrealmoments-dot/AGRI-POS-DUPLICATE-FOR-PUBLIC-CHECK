"""
Close-Day SMS scheduler & reminders.

Two kinds of SMS:
 1. Scheduled reminders — a background loop ticks every minute and fires the
    appropriate stage based on the branch's close_time and the branch's current
    close status. See STAGES below.
 2. Z-Report Finalized — fired synchronously from `daily_operations.submit_*_close`
    the moment a day closes successfully, so the owner sees the daily summary
    on their phone without opening the app.

Guardrails (baked in):
  - Quiet hours 22:00–07:00 local — no reminders fire in that window (escalation
    carries over to the 07:00 Day+1 alert).
  - One SMS per (stage, branch, date, recipient) per day — tracked via the
    `close_reminder_log` collection so we never double-fire.
  - Zero-sales suppress — a branch with no sales/expenses/credits today gets
    ONLY the 22:00 quiet-summary, never the escalation chain.
  - Branch "closed today" calendar — organizations can mark dates as non-
    operating via `branch_calendar` collection to fully silence reminders.
  - Role-aware dedup — if Manager == Cashier (same user), they get the SMS
    once, not twice.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone, date as _date
from typing import Optional

from config import db
from utils import now_iso, new_id

sms_log = logging.getLogger("close_reminder")

# Manila is UTC+8; no DST. Used for determining "local now" without pulling pytz.
LOCAL_OFFSET = timedelta(hours=8)


def _local_now() -> datetime:
    return datetime.now(timezone.utc) + LOCAL_OFFSET


# ── Stage definitions ────────────────────────────────────────────────────────
# `offset_h` is minutes relative to the branch's configured close_time (in hours
# — fractional allowed). Negative = before close, positive = after.
# `day_offset` tells us which day relative to the open-close date we're
# messaging about (0 = today, 1 = yesterday, 2+ = multi-day overdue).
# `recipients` is a list of role keys to dispatch to. The dispatcher will
# dedup by user id/phone.

STAGES = [
    {"key": "close_catchup_3pm",   "offset_h": -3.0, "day_offset": 0,
     "recipients": ["cashier"]},
    {"key": "close_precheck",      "offset_h":  0.0, "day_offset": 0,
     "recipients": ["cashier", "manager"]},
    {"key": "close_late_notice",   "offset_h":  1.5, "day_offset": 0,
     "recipients": ["cashier", "manager"]},
    {"key": "close_status_snapshot", "offset_h": 2.5, "day_offset": 0,
     "recipients": ["cashier", "manager", "owner"]},
    {"key": "close_escalation",    "offset_h":  3.5, "day_offset": 0,
     "recipients": ["cashier", "manager", "owner", "admin", "auditor"]},
    # Day +1
    {"key": "close_overdue_next_day", "fixed_hour": 7, "day_offset": 1,
     "recipients": ["manager", "owner", "auditor"]},
    {"key": "close_overdue_multi_day", "fixed_hour": 12, "day_offset": 1,
     "recipients": ["cashier", "manager", "owner", "admin", "auditor"]},
    # Day +2+
    {"key": "close_overdue_multi_day", "fixed_hour": 7, "day_offset": 2,
     "recipients": ["manager", "owner", "admin", "auditor"]},
    {"key": "close_overdue_multi_day", "fixed_hour": 12, "day_offset": 2,
     "recipients": ["cashier", "manager", "owner", "admin", "auditor"]},
]

QUIET_START = 22  # 10 PM
QUIET_END = 7     # 7 AM


def _in_quiet_hours(local_dt: datetime) -> bool:
    h = local_dt.hour
    if QUIET_START <= QUIET_END:
        return QUIET_START <= h < QUIET_END
    # wrap — typical case (22..7)
    return h >= QUIET_START or h < QUIET_END


async def _resolve_recipients(
    organization_id: str, branch_id: str, role_keys: list
) -> list:
    """Resolve role keys to unique users {id, phone, name}. Dedups by phone."""
    allowed_roles = set()
    if "cashier" in role_keys: allowed_roles |= {"cashier"}
    if "manager" in role_keys: allowed_roles |= {"manager"}
    if "owner" in role_keys or "admin" in role_keys: allowed_roles |= {"admin"}
    if "auditor" in role_keys: allowed_roles |= {"auditor"}

    q: dict = {
        "$or": [
            {"organization_id": organization_id},
            {"organization_id": {"$exists": False}},
        ],
        "active": {"$ne": False},
    }
    if allowed_roles:
        q["role"] = {"$in": list(allowed_roles)}
    # Branch scoping: admins see all branches; others only if match
    users = await db.users.find(q, {
        "_id": 0, "id": 1, "role": 1, "branch_id": 1,
        "full_name": 1, "username": 1, "phone": 1,
    }).to_list(500)

    seen = set()
    out = []
    for u in users:
        role = u.get("role")
        if role not in ("admin", "auditor") and u.get("branch_id") not in (branch_id, "", None):
            continue
        phone = (u.get("phone") or "").strip()
        if not phone or phone in seen:
            continue
        seen.add(phone)
        out.append({
            "id": u.get("id"),
            "phone": phone,
            "name": u.get("full_name") or u.get("username") or "team",
            "role": role,
        })
    return out


async def _build_branch_snapshot(branch_id: str, target_date: str) -> dict:
    """Compile the numbers SMS templates need."""
    inv_q = {"branch_id": branch_id, "order_date": target_date,
             "status": {"$ne": "voided"}}
    invoices = await db.invoices.find(
        inv_q, {"_id": 0, "grand_total": 1, "payment_type": 1, "fund_source": 1,
                "amount_paid": 1, "balance": 1, "late_encoded": 1}
    ).to_list(2000)

    s_count = len(invoices)
    s_total = sum(float(i.get("grand_total", 0) or 0) for i in invoices)
    cash_total = 0.0
    credit_total = 0.0
    digital_total = 0.0
    credit_count = 0
    for i in invoices:
        ptype = (i.get("payment_type") or "").lower()
        fsrc = (i.get("fund_source") or "").lower()
        amt = float(i.get("grand_total", 0) or 0)
        paid = float(i.get("amount_paid", 0) or 0)
        bal = float(i.get("balance", 0) or 0)
        if fsrc == "digital":
            digital_total += paid
        elif ptype in ("credit", "partial"):
            credit_total += bal
            credit_count += 1
            cash_total += paid
        else:
            cash_total += amt

    expenses = await db.expenses.find(
        {"branch_id": branch_id, "date": target_date, "status": {"$ne": "voided"}},
        {"_id": 0, "amount": 1}
    ).to_list(500)
    exp_count = len(expenses)
    exp_total = sum(float(e.get("amount", 0) or 0) for e in expenses)

    # Pending credit slips (if the Capture Inbox exists; no-op otherwise)
    try:
        pending_credits = await db.pending_credits.count_documents(
            {"branch_id": branch_id, "status": "open"}
        )
    except Exception:
        pending_credits = 0

    return {
        "sales_count": s_count,
        "sales_total": f"{s_total:,.2f}",
        "sales_total_raw": s_total,
        "cash_total": f"{cash_total:,.2f}",
        "credit_total": f"{credit_total:,.2f}",
        "digital_total": f"{digital_total:,.2f}",
        "credit_count": credit_count,
        "expense_count": exp_count,
        "expense_total": f"{exp_total:,.2f}",
        "cash_expected": f"{(cash_total - exp_total):,.2f}",
        "pending_credits": pending_credits,
    }


async def _already_fired(branch_id: str, date_str: str, stage_key: str, user_id: str) -> bool:
    doc = await db.close_reminder_log.find_one(
        {"branch_id": branch_id, "target_date": date_str,
         "stage_key": stage_key, "recipient_user_id": user_id},
        {"_id": 0, "id": 1},
    )
    return bool(doc)


async def _mark_fired(branch_id: str, date_str: str, stage_key: str,
                      user_id: str, phone: str):
    await db.close_reminder_log.insert_one({
        "id": new_id(),
        "branch_id": branch_id,
        "target_date": date_str,
        "stage_key": stage_key,
        "recipient_user_id": user_id,
        "recipient_phone": phone,
        "fired_at": now_iso(),
    })


async def _dispatch_stage(branch: dict, target_date_str: str, stage: dict):
    """Evaluate a single stage for a single branch-date and fire SMS if due."""
    from routes.sms import queue_sms

    # Look up snapshot numbers and build variables
    snapshot = await _build_branch_snapshot(branch["id"], target_date_str)

    # Zero-sales suppression on escalation stages (but keep the 22:00 summary)
    escalation_keys = {"close_late_notice", "close_status_snapshot",
                       "close_escalation", "close_overdue_next_day",
                       "close_overdue_multi_day"}
    if stage["key"] in escalation_keys and snapshot["sales_count"] == 0 \
            and snapshot["credit_count"] == 0 and snapshot["expense_count"] == 0:
        return

    # Resolve recipients
    recipients = await _resolve_recipients(
        branch.get("organization_id", ""), branch["id"], stage["recipients"]
    )
    if not recipients:
        return

    # Compute days overdue for multi-day template
    try:
        tgt = datetime.strptime(target_date_str, "%Y-%m-%d").date()
        days_overdue = (_local_now().date() - tgt).days
    except ValueError:
        days_overdue = 0

    hours_overdue = max(0, round(stage.get("offset_h", 0), 1))
    # For fixed-hour day+N stages, compute hours_overdue from local now
    if "fixed_hour" in stage:
        hours_overdue = max(0, days_overdue * 24)

    # Compute hours_to_close for catch-up
    close_time = float(branch.get("close_time_h", 18))  # hours as float
    now_h = _local_now().hour + _local_now().minute / 60
    hrs_to_close = round(max(0.0, close_time - now_h), 1)

    variables = {
        **snapshot,
        "branch_name": branch.get("name", ""),
        "date": target_date_str,
        "days_overdue": days_overdue,
        "hours_overdue": hours_overdue,
        "hours_to_close": hrs_to_close,
    }

    for r in recipients:
        if await _already_fired(branch["id"], target_date_str, stage["key"], r["id"]):
            continue
        try:
            await queue_sms(
                template_key=stage["key"],
                customer_id=r["id"],          # re-using customer_id slot for recipient user id
                customer_name=r["name"],
                phone=r["phone"],
                variables=variables,
                organization_id=branch.get("organization_id", ""),
                branch_id=branch["id"],
                branch_name=branch.get("name", ""),
                trigger="scheduled",
                trigger_ref=f"close_reminder:{branch['id']}:{target_date_str}:{stage['key']}",
                dedup_key=f"close_reminder:{branch['id']}:{target_date_str}:{stage['key']}:{r['id']}",
            )
            await _mark_fired(branch["id"], target_date_str, stage["key"], r["id"], r["phone"])
        except Exception as e:
            sms_log.error(f"close_reminder dispatch failed: {e}")


async def _is_branch_closed_on(branch_id: str, date_str: str) -> bool:
    doc = await db.daily_closings.find_one(
        {"branch_id": branch_id, "date": date_str, "status": "closed"},
        {"_id": 0, "id": 1},
    )
    return bool(doc)


async def _is_calendar_closed(branch_id: str, date_str: str) -> bool:
    try:
        doc = await db.branch_calendar.find_one(
            {"branch_id": branch_id, "date": date_str, "closed": True},
            {"_id": 0, "id": 1},
        )
        return bool(doc)
    except Exception:
        return False


async def tick_once() -> dict:
    """
    Evaluate every active branch against the stage schedule for the current
    local time. Called by the background loop once per minute.
    Returns a small stats dict for logging/debugging.
    """
    local = _local_now()
    if _in_quiet_hours(local):
        return {"skipped": "quiet_hours", "hour": local.hour}

    branches = await db.branches.find(
        {"active": {"$ne": False}}, {"_id": 0, "id": 1, "name": 1,
                                      "organization_id": 1, "close_time_h": 1}
    ).to_list(500)

    fired = 0
    for br in branches:
        close_h = float(br.get("close_time_h", 18))  # default 6 PM

        for stage in STAGES:
            # Determine the target_date + the local trigger time for this stage
            day_off = stage.get("day_offset", 0)
            target_date = (local.date() - timedelta(days=day_off))
            target_date_str = target_date.strftime("%Y-%m-%d")

            if "fixed_hour" in stage:
                trigger_h = float(stage["fixed_hour"])
            else:
                trigger_h = close_h + float(stage["offset_h"])

            # Trigger within a 2-minute window around trigger_h
            now_h = local.hour + local.minute / 60
            if abs(now_h - trigger_h) > (2.0 / 60):
                continue

            # Skip stages aimed at day-0 if target_date is in the future
            if target_date > local.date():
                continue

            # Skip if branch closed target_date already
            if await _is_branch_closed_on(br["id"], target_date_str):
                continue
            # Skip calendar-closed days
            if await _is_calendar_closed(br["id"], target_date_str):
                continue

            await _dispatch_stage(br, target_date_str, stage)
            fired += 1

    return {"fired_stages": fired, "branches": len(branches),
            "local_time": local.isoformat()}


# ── Z-Report Finalized (fired from close endpoint) ───────────────────────────

async def send_zreport_finalized(close_record: dict, user: Optional[dict] = None):
    """
    Sent the moment a daily close is persisted. Owner/Manager/Auditor get a
    human-readable daily summary on their phone without opening the app.
    """
    from routes.sms import queue_sms

    branch_id = close_record.get("branch_id", "")
    branch = await db.branches.find_one(
        {"id": branch_id}, {"_id": 0, "name": 1, "organization_id": 1}
    ) or {}

    snapshot = await _build_branch_snapshot(branch_id, close_record.get("date", ""))

    # Late-encode note (the owner actively wants to see this)
    late_invoices = await db.invoices.count_documents({
        "branch_id": branch_id, "late_encoded": True, "status": {"$ne": "voided"},
        "late_encoded_at": {"$gte": f"{close_record.get('date', '')}T00:00:00",
                            "$lt":  f"{close_record.get('date', '')}T23:59:59"},
    })
    late_note = ""
    if late_invoices:
        late_note = f"⚠ Includes {late_invoices} late-encoded credit(s).\n"

    actual = float(close_record.get("actual_cash", 0) or 0)
    expected = float(close_record.get("expected_cash", 0) or 0)
    over_short = actual - expected
    os_str = f"+₱{over_short:,.2f} over" if over_short > 0 else \
             (f"-₱{abs(over_short):,.2f} short" if over_short < 0 else "matches")

    closed_at = close_record.get("closed_at") or now_iso()
    try:
        closed_time = datetime.fromisoformat(closed_at.replace("Z", "+00:00")) + LOCAL_OFFSET
        closed_time_str = closed_time.strftime("%I:%M %p")
    except Exception:
        closed_time_str = "—"

    recipients = await _resolve_recipients(
        branch.get("organization_id", ""), branch_id,
        ["manager", "owner", "auditor"],
    )

    variables = {
        **snapshot,
        "branch_name": branch.get("name", ""),
        "date": close_record.get("date", ""),
        "closed_time": closed_time_str,
        "cash_actual": f"{actual:,.2f}",
        "cash_expected": f"{expected:,.2f}",
        "over_short": os_str,
        "late_encode_note": late_note,
        "closer_name": (user or {}).get("full_name") or (user or {}).get("username") or "",
    }

    for r in recipients:
        try:
            await queue_sms(
                template_key="zreport_finalized",
                customer_id=r["id"],
                customer_name=r["name"],
                phone=r["phone"],
                variables=variables,
                organization_id=branch.get("organization_id", ""),
                branch_id=branch_id,
                branch_name=branch.get("name", ""),
                trigger="auto",
                trigger_ref=f"zreport:{branch_id}:{close_record.get('date', '')}",
                dedup_key=f"zreport:{branch_id}:{close_record.get('date', '')}:{r['id']}",
            )
        except Exception as e:
            sms_log.error(f"zreport_finalized dispatch failed: {e}")


# ── Background loop & start hook ─────────────────────────────────────────────

_loop_task: Optional[asyncio.Task] = None


async def _loop():
    while True:
        try:
            await tick_once()
        except Exception as e:
            sms_log.error(f"close_reminder tick failed: {e}")
        await asyncio.sleep(60)  # 1 tick per minute


def start_scheduler_on_startup(app):
    """Register a FastAPI startup handler to kick off the minute loop."""
    @app.on_event("startup")
    async def _startup():
        global _loop_task
        if _loop_task is None or _loop_task.done():
            _loop_task = asyncio.create_task(_loop())
            sms_log.info("close_reminder loop started")

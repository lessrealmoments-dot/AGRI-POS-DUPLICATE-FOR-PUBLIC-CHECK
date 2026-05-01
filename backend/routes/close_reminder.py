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

try:
    # Python 3.9+ ships `zoneinfo` so we don't need pytz. tzdata is vendored
    # on most Linux distros; if the tenant picks an obscure TZ that isn't
    # installed locally we catch ZoneInfoNotFoundError below and fall back.
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore
    ZoneInfoNotFoundError = Exception  # type: ignore

from config import db, _raw_db, set_org_context
from utils import now_iso, new_id

sms_log = logging.getLogger("close_reminder")

# Fallback when a tenant hasn't picked a timezone yet, or when the picked one
# can't be loaded. Matches the app's historical Philippine default so the
# behaviour is identical for existing orgs.
DEFAULT_TIMEZONE = "Asia/Manila"


async def _resolve_org_timezone(org_id: str) -> str:
    """Return the IANA timezone name configured for an organization.

    Resolution order:
      1. `organizations.timezone` — canonical, set via the Timezone settings UI.
      2. `settings.company_info.value.timezone` (per-org) — legacy fallback.
      3. DEFAULT_TIMEZONE (Asia/Manila).
    """
    if not org_id:
        return DEFAULT_TIMEZONE
    try:
        org = await _raw_db.organizations.find_one(
            {"id": org_id}, {"_id": 0, "timezone": 1}
        )
        if org and org.get("timezone"):
            return org["timezone"]
        ci = await _raw_db.settings.find_one(
            {"key": "company_info", "organization_id": org_id},
            {"_id": 0, "value": 1},
        )
        tz = (((ci or {}).get("value") or {}).get("timezone") or "").strip()
        if tz:
            return tz
    except Exception as e:
        sms_log.warning(f"_resolve_org_timezone failed for {org_id}: {e}")
    return DEFAULT_TIMEZONE


def _local_now_in(tz_name: str) -> datetime:
    """Return the current wall-clock time in the given IANA zone."""
    if ZoneInfo is None:
        # Last-resort: assume UTC+8 to preserve legacy behaviour if zoneinfo
        # somehow isn't available (shouldn't happen on Python 3.9+).
        return datetime.now(timezone.utc) + timedelta(hours=8)
    try:
        return datetime.now(ZoneInfo(tz_name))
    except (ZoneInfoNotFoundError, Exception):
        return datetime.now(ZoneInfo(DEFAULT_TIMEZONE))


# Kept for backward compatibility with older call sites that still import this.
# Resolves to Asia/Manila wall-clock time, same as before.
LOCAL_OFFSET = timedelta(hours=8)


def _local_now() -> datetime:
    """DEPRECATED — use `_local_now_in(tz_name)` with the branch's org TZ
    instead. Retained for a couple of logging-only callers."""
    if ZoneInfo is not None:
        try:
            return datetime.now(ZoneInfo(DEFAULT_TIMEZONE))
        except Exception:
            pass
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
    if "cashier" in role_keys:
        allowed_roles |= {"cashier"}
    if "manager" in role_keys:
        allowed_roles |= {"manager"}
    if "owner" in role_keys or "admin" in role_keys:
        allowed_roles |= {"admin"}
    if "auditor" in role_keys:
        allowed_roles |= {"auditor"}

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

    # Resolve this org's local time for overdue/hours-to-close maths
    org_id = branch.get("organization_id", "")
    tz_name = await _resolve_org_timezone(org_id)
    local = _local_now_in(tz_name)

    # Compute days overdue for multi-day template
    try:
        tgt = datetime.strptime(target_date_str, "%Y-%m-%d").date()
        days_overdue = (local.date() - tgt).days
    except ValueError:
        days_overdue = 0

    hours_overdue = max(0, round(stage.get("offset_h", 0), 1))
    # For fixed-hour day+N stages, compute hours_overdue from local now
    if "fixed_hour" in stage:
        hours_overdue = max(0, days_overdue * 24)

    # Compute hours_to_close for catch-up
    close_time = float(branch.get("close_time_h", 18))  # hours as float
    now_h = local.hour + local.minute / 60
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
    Evaluate every active branch against the stage schedule for THAT branch's
    organization local time. Called by the background loop once per minute.

    Multi-tenant: each organization can configure its own timezone via
    `organizations.timezone` (or `settings.company_info.timezone` as a
    legacy fallback). Quiet-hour windows, trigger times and target dates are
    all evaluated in the tenant's local clock, so a Philippine tenant's 3 PM
    reminder fires at 3 PM Manila time while a US tenant's 3 PM fires at
    3 PM America/New_York on the same calendar day.
    """
    branches = await db.branches.find(
        {"active": {"$ne": False}}, {"_id": 0, "id": 1, "name": 1,
                                      "organization_id": 1, "close_time_h": 1}
    ).to_list(500)

    # Cache org→tz lookups so we don't hit Mongo once per branch.
    tz_cache: dict[str, str] = {}

    fired = 0
    first_local: Optional[datetime] = None
    skipped_quiet = 0
    for br in branches:
        org_id = br.get("organization_id", "") or ""
        if org_id not in tz_cache:
            tz_cache[org_id] = await _resolve_org_timezone(org_id)
        tz_name = tz_cache[org_id]
        local = _local_now_in(tz_name)
        if first_local is None:
            first_local = local
        if _in_quiet_hours(local):
            skipped_quiet += 1
            continue

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

    return {
        "fired_stages": fired,
        "branches": len(branches),
        "skipped_quiet": skipped_quiet,
        "local_time": first_local.isoformat() if first_local else None,
        "orgs_scanned": len(tz_cache),
    }


# ── Diagnostics ─────────────────────────────────────────────────────────────
# Exposed as `GET /api/sms/close-reminder/diagnose` so admins can quickly see
# exactly what the scheduler thinks about each of their branches right now.
# Answers questions like "why didn't my 3 PM ping fire?" without digging
# through logs.

async def diagnose_for_org(org_id: str) -> dict:
    tz_name = await _resolve_org_timezone(org_id)
    local = _local_now_in(tz_name)
    branches = await db.branches.find(
        {"organization_id": org_id, "active": {"$ne": False}},
        {"_id": 0, "id": 1, "name": 1, "close_time_h": 1},
    ).to_list(500)

    def _next_stage(close_h: float):
        now_h = local.hour + local.minute / 60
        candidates = []
        for s in STAGES:
            if "fixed_hour" in s:
                th = float(s["fixed_hour"])
            else:
                th = close_h + float(s["offset_h"])
            delta = th - now_h
            if delta < 0:
                delta += 24
            candidates.append((delta, s["key"], round(th, 2)))
        candidates.sort()
        return candidates[0] if candidates else (None, None, None)

    branch_info = []
    for br in branches:
        close_h = float(br.get("close_time_h", 18))
        delta, key, at_h = _next_stage(close_h)
        # Count resolved recipient phones for each role we care about
        recipients = {}
        for role in ("cashier", "manager", "owner", "admin", "auditor"):
            r = await _resolve_recipients(org_id, br["id"], [role])
            recipients[role] = [x["phone"] for x in r if x.get("phone")]
        branch_info.append({
            "id": br["id"], "name": br.get("name", ""),
            "close_time_h": close_h,
            "next_stage": key,
            "fires_at_local_hour": at_h,
            "in_hours": round(delta, 2),
            "recipient_phones": recipients,
        })

    return {
        "org_id": org_id,
        "timezone": tz_name,
        "local_now": local.isoformat(),
        "quiet_hours": _in_quiet_hours(local),
        "branches": branch_info,
    }


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
    # Render closed_time in the tenant's local zone so the SMS reads as they
    # experience it on the counter — not in UTC or some server-default TZ.
    tz_name = await _resolve_org_timezone(branch.get("organization_id", ""))
    try:
        ts = datetime.fromisoformat(closed_at.replace("Z", "+00:00"))
        if ZoneInfo is not None:
            try:
                ts = ts.astimezone(ZoneInfo(tz_name))
            except (ZoneInfoNotFoundError, Exception):
                ts = ts + LOCAL_OFFSET
        else:
            ts = ts + LOCAL_OFFSET
        closed_time_str = ts.strftime("%I:%M %p")
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

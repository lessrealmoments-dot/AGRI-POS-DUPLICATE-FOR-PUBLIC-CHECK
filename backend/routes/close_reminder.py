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


def _user_branch_ids(u: dict) -> list:
    """Return the list of branches a user is assigned to.

    Multi-branch model (new): `users.branch_ids` is a list of branch UUIDs.
    A user receives SMS (and can switch into) any branch in that list.

    Legacy model (backward-compatible): `users.branch_id` is a single UUID.
    If present and not already in `branch_ids`, it's folded in. Empty or
    missing both → treat as "all branches" (admin-style unscoped).
    """
    ids = u.get("branch_ids") or []
    if not isinstance(ids, list):
        ids = []
    ids = [b for b in ids if isinstance(b, str) and b.strip()]
    legacy = (u.get("branch_id") or "").strip()
    if legacy and legacy not in ids:
        ids.append(legacy)
    return ids


def _user_covers_branch(u: dict, branch_id: str) -> bool:
    """True if this user should receive SMS/data for the given branch.

    Rules:
      - admin users → cover every branch (global)
      - users with `is_auditor=True` and no branch list → global auditor
      - otherwise → must have branch_id in their assigned list, OR no list
        at all (legacy unscoped user = all branches)
    """
    role = (u.get("role") or "").lower()
    if role == "admin":
        return True
    ids = _user_branch_ids(u)
    if not ids:
        # No branch assignment at all → treat as "all branches" (legacy)
        return True
    return branch_id in ids

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


# ── Stage defaults (seed values) ─────────────────────────────────────────────
# The UI lets each tenant toggle stages on/off and customize per-stage
# recipients. We seed from this dict the first time a stage is fetched;
# afterwards the saved values win. Role labels match the five roles the
# dispatcher dedups against (cashier/manager/owner/admin/auditor).
STAGE_DEFAULTS = {s["key"]: {"enabled": True, "recipients": list(s["recipients"])}
                  for s in STAGES}


async def _load_stage_settings(organization_id: str) -> dict:
    """Return `{stage_key: {enabled: bool, recipients: [...]}, ...}` merged
    with the code defaults. Missing keys inherit defaults so a newly-added
    stage becomes active automatically for every org without a migration.

    Uses raw db — safe from scheduler background loop and request context."""
    merged = {k: dict(v, recipients=list(v["recipients"])) for k, v in STAGE_DEFAULTS.items()}
    try:
        async for doc in _raw_db.sms_close_stages.find(
            {"organization_id": organization_id}, {"_id": 0, "stage_key": 1,
                                                    "enabled": 1, "recipients": 1},
        ):
            k = doc.get("stage_key")
            if k in merged:
                if "enabled" in doc:
                    merged[k]["enabled"] = bool(doc["enabled"])
                if isinstance(doc.get("recipients"), list):
                    merged[k]["recipients"] = [r for r in doc["recipients"] if isinstance(r, str)]
    except Exception as e:
        sms_log.warning(f"_load_stage_settings read failed for {organization_id}: {e}")
    return merged


def _in_quiet_hours(local_dt: datetime) -> bool:
    h = local_dt.hour
    if QUIET_START <= QUIET_END:
        return QUIET_START <= h < QUIET_END
    # wrap — typical case (22..7)
    return h >= QUIET_START or h < QUIET_END


async def _resolve_recipients(
    organization_id: str, branch_id: str, role_keys: list,
    include_debug: bool = False,
) -> list | tuple:
    """Resolve role keys to unique users {id, phone, name, role}. Dedups by
    phone. Uses the RAW (unscoped) db because this is called from both
    request-scoped code AND the background scheduler tick (which has no
    org context set). We pass `organization_id` explicitly in the query.

    Role-key → user-role mapping:
      cashier  → users where role="cashier"
      manager  → users where role="manager"
      owner    → users where role="admin" (owner is modeled as admin here)
      admin    → users where role="admin" (deduped with owner)
      auditor  → users with is_auditor=True (flag, not role)

    Branch scoping (non-admin only):
      - honors `branch_ids` list if present (multi-branch assignment)
      - legacy `branch_id` single value is included automatically
      - no branch assignment at all → treated as "all branches" (legacy)

    Fallback:
      When a role resolves to zero users with phone numbers, we read the
      configured "Collection Notification Recipients" phone for that role
      (Settings → Messages) and add it as a synthetic recipient so alerts
      still go out. Per-branch overrides respected.

    Returns list of recipients. If `include_debug=True`, returns
    `(recipients, debug_by_role)` where debug gives a per-role breakdown
    suitable for test-button UX.
    """
    want_cashier = "cashier" in role_keys
    want_manager = "manager" in role_keys
    want_admin = "owner" in role_keys or "admin" in role_keys
    want_auditor = "auditor" in role_keys

    role_filters = []
    if want_cashier:
        role_filters.append("cashier")
    if want_manager:
        role_filters.append("manager")
    if want_admin:
        role_filters.append("admin")

    q: dict = {
        "organization_id": organization_id,
        "active": {"$ne": False},
    }
    or_clauses = []
    if role_filters:
        or_clauses.append({"role": {"$in": role_filters}})
    if want_auditor:
        # Auditor is a capability flag on any user — not a distinct role
        or_clauses.append({"is_auditor": True})
    if not or_clauses:
        return ([], {}) if include_debug else []
    q["$or"] = or_clauses

    users = await _raw_db.users.find(q, {
        "_id": 0, "id": 1, "role": 1, "branch_id": 1, "branch_ids": 1,
        "full_name": 1, "username": 1, "phone": 1, "is_auditor": 1,
    }).to_list(500)

    seen_phones = set()
    out = []
    # Tracks which logical role_keys found a real user — used to decide
    # whether to fall back to Collection Notification Recipients.
    matched_by_role: dict[str, list] = {k: [] for k in ("cashier", "manager", "owner", "admin", "auditor")}
    no_phone_by_role: dict[str, int] = {k: 0 for k in ("cashier", "manager", "owner", "admin", "auditor")}

    for u in users:
        role = (u.get("role") or "").lower()
        is_auditor = bool(u.get("is_auditor"))
        # Decide which logical keys this user satisfies
        user_logical = []
        if want_cashier and role == "cashier":
            user_logical.append("cashier")
        if want_manager and role == "manager":
            user_logical.append("manager")
        if want_admin and role == "admin":
            # Prefer "owner" label if caller asked for it, else "admin"
            user_logical.append("owner" if "owner" in role_keys else "admin")
        if want_auditor and is_auditor:
            user_logical.append("auditor")
        if not user_logical:
            continue

        # Branch scope — admin users cover every branch; others respect
        # their branch_ids list (+ legacy branch_id). Auditors with no
        # assignment default to "all branches" so they always get pinged.
        if not _user_covers_branch(u, branch_id):
            continue

        phone = (u.get("phone") or "").strip()
        if not phone:
            for k in user_logical:
                no_phone_by_role[k] = no_phone_by_role.get(k, 0) + 1
            continue
        if phone in seen_phones:
            # Same human, multiple logical roles — track but don't double-queue
            for k in user_logical:
                matched_by_role.setdefault(k, []).append(phone)
            continue
        seen_phones.add(phone)
        for k in user_logical:
            matched_by_role.setdefault(k, []).append(phone)
        out.append({
            "id": u.get("id"),
            "phone": phone,
            "name": u.get("full_name") or u.get("username") or "team",
            "role": user_logical[0],
        })

    # ── Fallback: Collection Notification Recipients ─────────────────────────
    # For every requested role that found NO user with a phone, fold in the
    # admin-configured phone from Settings → Messages → Collection Recipients.
    # Per-branch overrides for manager/auditor are respected.
    cc_setting = await _raw_db.system_settings.find_one(
        {"key": "collection_notification_recipients", "organization_id": organization_id},
        {"_id": 0},
    ) or {}
    branch_override = (cc_setting.get("branch_phones") or {}).get(branch_id, {})

    def _add_fallback(role_label: str, phone_raw: str):
        phone = (phone_raw or "").strip()
        if not phone or phone in seen_phones:
            return False
        seen_phones.add(phone)
        out.append({
            "id": "",
            "phone": phone,
            "name": f"{role_label.title()} (fallback)",
            "role": role_label,
            "fallback": True,
        })
        return True

    fallback_used: dict[str, bool] = {}
    if want_admin and not matched_by_role.get("admin") and not matched_by_role.get("owner"):
        key = "owner" if "owner" in role_keys else "admin"
        used = _add_fallback(key, cc_setting.get("owner_phone") or cc_setting.get("admin_phone"))
        fallback_used[key] = used
    if want_manager and not matched_by_role.get("manager"):
        phone_raw = branch_override.get("manager_phone") or cc_setting.get("manager_phone")
        fallback_used["manager"] = _add_fallback("manager", phone_raw)
    if want_auditor and not matched_by_role.get("auditor"):
        phone_raw = branch_override.get("auditor_phone") or cc_setting.get("auditor_phone")
        fallback_used["auditor"] = _add_fallback("auditor", phone_raw)
    # Cashier has no Collection-Recipient field by design (floor staff get
    # notified via Team phone only — a missing cashier phone is a data
    # quality problem to surface in the test-button feedback).

    if include_debug:
        debug = {}
        for k in ("cashier", "manager", "owner", "admin", "auditor"):
            if k not in role_keys:
                continue
            debug[k] = {
                "matched_users": len(matched_by_role.get(k, [])),
                "users_without_phone": no_phone_by_role.get(k, 0),
                "fallback_used": bool(fallback_used.get(k)),
            }
        return out, debug
    return out


async def _build_branch_snapshot(branch_id: str, target_date: str, organization_id: str = "") -> dict:
    """Compile the numbers SMS templates need. Uses the RAW db with an
    explicit organization_id so it works from the background scheduler
    (which has no org context set) AND from request-scoped callers."""
    inv_q = {"branch_id": branch_id, "order_date": target_date,
             "status": {"$ne": "voided"}}
    if organization_id:
        inv_q["organization_id"] = organization_id
    invoices = await _raw_db.invoices.find(
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

    exp_q = {"branch_id": branch_id, "date": target_date, "status": {"$ne": "voided"}}
    if organization_id:
        exp_q["organization_id"] = organization_id
    expenses = await _raw_db.expenses.find(
        exp_q, {"_id": 0, "amount": 1}
    ).to_list(500)
    exp_count = len(expenses)
    exp_total = sum(float(e.get("amount", 0) or 0) for e in expenses)

    # Pending credit slips (if the Capture Inbox exists; no-op otherwise)
    try:
        pc_q: dict = {"branch_id": branch_id, "status": "open"}
        if organization_id:
            pc_q["organization_id"] = organization_id
        pending_credits = await _raw_db.pending_credits.count_documents(pc_q)
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
    doc = await _raw_db.close_reminder_log.find_one(
        {"branch_id": branch_id, "target_date": date_str,
         "stage_key": stage_key, "recipient_user_id": user_id},
        {"_id": 0, "id": 1},
    )
    return bool(doc)


async def _mark_fired(branch_id: str, date_str: str, stage_key: str,
                      user_id: str, phone: str, organization_id: str = ""):
    await _raw_db.close_reminder_log.insert_one({
        "id": new_id(),
        "organization_id": organization_id,
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

    org_id = branch.get("organization_id", "") or ""
    # Look up snapshot numbers and build variables
    snapshot = await _build_branch_snapshot(branch["id"], target_date_str, org_id)

    # Zero-sales suppression on escalation stages (but keep the 22:00 summary)
    escalation_keys = {"close_late_notice", "close_status_snapshot",
                       "close_escalation", "close_overdue_next_day",
                       "close_overdue_multi_day"}
    if stage["key"] in escalation_keys and snapshot["sales_count"] == 0 \
            and snapshot["credit_count"] == 0 and snapshot["expense_count"] == 0:
        return

    # Resolve recipients (uses _raw_db under the hood — safe from scheduler)
    recipients = await _resolve_recipients(org_id, branch["id"], stage["recipients"])
    if not recipients:
        return

    # Resolve this org's local time for overdue/hours-to-close maths
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
        # Fallback recipients have no user_id — skip the per-user dedup and
        # use the phone-keyed dedup below so repeated ticks don't re-fire.
        recipient_key = r.get("id") or f"fallback:{r['phone']}"
        if await _already_fired(branch["id"], target_date_str, stage["key"], recipient_key):
            continue
        try:
            await queue_sms(
                template_key=stage["key"],
                customer_id=r.get("id") or "",
                customer_name=r["name"],
                phone=r["phone"],
                variables=variables,
                organization_id=org_id,
                branch_id=branch["id"],
                branch_name=branch.get("name", ""),
                trigger="scheduled",
                trigger_ref=f"close_reminder:{branch['id']}:{target_date_str}:{stage['key']}",
                dedup_key=f"close_reminder:{branch['id']}:{target_date_str}:{stage['key']}:{recipient_key}",
            )
            await _mark_fired(branch["id"], target_date_str, stage["key"], recipient_key, r["phone"], org_id)
        except Exception as e:
            sms_log.error(f"close_reminder dispatch failed: {e}")


async def _is_branch_closed_on(branch_id: str, date_str: str, organization_id: str = "") -> bool:
    q: dict = {"branch_id": branch_id, "date": date_str, "status": "closed"}
    if organization_id:
        q["organization_id"] = organization_id
    doc = await _raw_db.daily_closings.find_one(q, {"_id": 0, "id": 1})
    return bool(doc)


async def _is_calendar_closed(branch_id: str, date_str: str, organization_id: str = "") -> bool:
    try:
        q: dict = {"branch_id": branch_id, "date": date_str, "closed": True}
        if organization_id:
            q["organization_id"] = organization_id
        doc = await _raw_db.branch_calendar.find_one(q, {"_id": 0, "id": 1})
        return bool(doc)
    except Exception:
        return False


async def tick_once() -> dict:
    """
    Evaluate every active branch against the stage schedule for THAT branch's
    organization local time. Called by the background loop once per minute.

    ⚠ IMPORTANT — uses `_raw_db` with explicit `organization_id` filters,
    NOT the tenant-scoped `db` proxy. The background scheduler has no
    `_current_org_id` ContextVar set, so the scoped `db` would fail
    closed and match zero rows. That was the root cause of the entire
    "scheduled SMS never fires" bug prior to this fix.

    Multi-tenant: each organization can configure its own timezone via
    `organizations.timezone` (or `settings.company_info.timezone` as a
    legacy fallback). Quiet-hour windows, trigger times and target dates are
    all evaluated in the tenant's local clock.
    """
    branches = await _raw_db.branches.find(
        {"active": {"$ne": False}}, {"_id": 0, "id": 1, "name": 1,
                                      "organization_id": 1, "close_time_h": 1,
                                      "close_reminder_disabled": 1}
    ).to_list(500)

    # Cache org→tz lookups AND org→stage-settings lookups so we don't hit
    # Mongo once per branch-per-stage when the scheduler ticks.
    tz_cache: dict[str, str] = {}
    stage_cache: dict[str, dict] = {}

    fired = 0
    first_local: Optional[datetime] = None
    skipped_quiet = 0
    skipped_disabled = 0
    for br in branches:
        # Per-branch opt-out — set from the Team SMS card when an owner
        # marks a warehouse/transfer-only branch as "no close-day SMS".
        if br.get("close_reminder_disabled"):
            skipped_disabled += 1
            continue
        org_id = br.get("organization_id", "") or ""
        if org_id not in tz_cache:
            tz_cache[org_id] = await _resolve_org_timezone(org_id)
        if org_id not in stage_cache:
            stage_cache[org_id] = await _load_stage_settings(org_id)
        tz_name = tz_cache[org_id]
        stage_settings = stage_cache[org_id]
        local = _local_now_in(tz_name)
        if first_local is None:
            first_local = local
        if _in_quiet_hours(local):
            skipped_quiet += 1
            continue

        close_h = float(br.get("close_time_h", 18))  # default 6 PM

        for stage in STAGES:
            # Apply per-stage org settings — admin may have disabled this
            # stage or narrowed the recipient roles from the Team SMS UI.
            cfg = stage_settings.get(stage["key"], {})
            if cfg and not cfg.get("enabled", True):
                continue
            effective_recipients = cfg.get("recipients") or list(stage["recipients"])
            if not effective_recipients:
                continue
            stage_effective = dict(stage, recipients=effective_recipients)

            # Determine the target_date + the local trigger time for this stage
            day_off = stage["day_offset"]
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
            if await _is_branch_closed_on(br["id"], target_date_str, org_id):
                continue
            # Skip calendar-closed days
            if await _is_calendar_closed(br["id"], target_date_str, org_id):
                continue

            await _dispatch_stage(br, target_date_str, stage_effective)
            fired += 1

    if fired > 0:
        sms_log.info(
            f"close_reminder tick: fired {fired} stage(s) across "
            f"{len(branches)} branch(es) in {len(tz_cache)} org(s) "
            f"(skipped {skipped_quiet} in quiet hours, {skipped_disabled} opted-out)"
        )
    return {
        "fired_stages": fired,
        "branches": len(branches),
        "skipped_quiet": skipped_quiet,
        "skipped_disabled": skipped_disabled,
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
    branches = await _raw_db.branches.find(
        {"organization_id": org_id, "active": {"$ne": False}},
        {"_id": 0, "id": 1, "name": 1, "close_time_h": 1, "close_reminder_disabled": 1},
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
            "close_reminder_disabled": bool(br.get("close_reminder_disabled")),
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
    branch = await _raw_db.branches.find_one(
        {"id": branch_id}, {"_id": 0, "name": 1, "organization_id": 1}
    ) or {}

    org_id = branch.get("organization_id", "")
    snapshot = await _build_branch_snapshot(branch_id, close_record.get("date", ""), org_id)

    # Late-encode note (the owner actively wants to see this)
    late_invoices = await _raw_db.invoices.count_documents({
        "organization_id": org_id,
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

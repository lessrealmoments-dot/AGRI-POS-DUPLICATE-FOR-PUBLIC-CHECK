"""
SMS Engine — Queue, Templates, Settings, and Auto-trigger helpers.
All SMS goes through the queue. An external gateway (phone app / API) polls
GET /pending and marks sent via PATCH /{id}/mark-sent.
"""
from fastapi import APIRouter, Depends, HTTPException
from typing import Optional
from datetime import datetime, timezone, timedelta, date
from config import db, _raw_db, logger as _config_logger, get_org_context
from utils import get_current_user, check_perm, now_iso, new_id

router = APIRouter(prefix="/sms", tags=["SMS"])


# ── Gateway-dispatch tuning (ONE-SHOT policy, Iter 227) ────────────────────
#
# Policy change (user-driven, after carrier flagged the SIM for spam):
#   The server dispatches each queue row EXACTLY ONCE. Period.
#
#   Rationale: once the gateway polls /queue/pending, it physically sends the
#   SMS via GSM. If its mark-sent ACK fails (DNS outage, power blip, server
#   maintenance), re-dispatching the SAME row just sends the SAME SMS AGAIN
#   via GSM — that's what caused the 60+ duplicate "URGENT close" flood and
#   got the user's SIM flagged by the carrier.
#
#   Trade-off: if the gateway crashed BEFORE actually sending the SMS we
#   lose one legitimate message. The user has explicitly opted into this
#   because under-delivery is far cheaper than carrier blacklisting.
#
#   After dispatch, the row waits for either mark-sent (→ sent) or
#   mark-failed (→ failed, admin can /retry manually). If neither ACK
#   arrives within the lease, the row becomes `deferred` with a visible
#   reason so the admin can inspect it in the Queue UI and decide.
#
# What stays in place from Iter 216/226:
#   - LEASE — still locks the row so two concurrent polls don't double-ship.
#   - PER-RECIPIENT THROTTLE — `queue_sms` refuses to enqueue the same
#     (template_key, phone) combo within ENQUEUE_THROTTLE_SECONDS. Stops
#     the scheduler from piling on while the gateway is offline.
#   - MUTE PURGE + FILTER — muting a branch cancels queued rows AND the
#     dispatch handler skips rows for muted branches.
DISPATCH_LEASE_SECONDS = 300          # 5 min lease before self-defer
MAX_DISPATCHES_PER_DAY = 1            # ONE-SHOT: server dispatches each row once
ENQUEUE_THROTTLE_SECONDS = 600        # 10 min per-recipient per-template
MAX_GATEWAY_RETRIES = 3               # existing cap for mark-failed ACK path

# Close-reminder stage keys. When an admin mutes a branch (via Team SMS card),
# we purge any pending/deferred/failed rows with these template_keys for that
# branch — otherwise items queued BEFORE the mute keep flowing to the gateway
# and spam recipients even though the UI says "muted". Kept in sync with
# STAGES list in `routes/close_reminder.py`.
CLOSE_REMINDER_TEMPLATE_KEYS = [
    "close_catchup_3pm",
    "close_precheck",
    "close_late_notice",
    "close_status_snapshot",
    "close_escalation",
    "close_overdue_next_day",
    "close_overdue_multi_day",
]

# Templates that reference a specific branch's operational status and should
# ALL be purged when a branch is muted / scope-blocked. Superset of
# CLOSE_REMINDER_TEMPLATE_KEYS + the Z-Report finalized daily summary (which
# previously survived a mute and kept arriving on owners' phones overnight).
MUTED_BRANCH_TEMPLATE_KEYS = CLOSE_REMINDER_TEMPLATE_KEYS + [
    "zreport_finalized",
]

# Night-time dispatch gate (Iter 237) — the Android gateway polls 24/7, so
# rows queued at 6 PM (with the recipient asleep by 10 PM) used to be handed
# out at 1 AM or 2 AM when the phone reconnected. We now refuse to dispatch
# non-manual rows inside the org's local quiet window, mirroring the
# enqueuer's 22:00–07:00 guard (close_reminder.py: QUIET_START / QUIET_END).
DISPATCH_QUIET_START_H = 22   # 10 PM local
DISPATCH_QUIET_END_H   = 7    # 7 AM local

# Close-reminder rows become stale after this many hours (Iter 237).
# An SMS queued 3 days ago with `days_overdue=0` is wrong by the time it
# ships, and the recipient has already moved on. Before dispatch we expire
# any pending close-reminder row older than this threshold so it can never
# leak out after the situation has changed.
CLOSE_REMINDER_TTL_HOURS = 24

# Gateway heartbeat (Iter 237). Every authenticated `GET /sms/queue/pending`
# call updates `settings.sms_gateway_heartbeat`. If no poll lands within this
# window the UI flashes a red badge so a stuck/disconnected gateway is
# visible immediately — instead of leaking 5 days of replayed SMS at the
# owner. 15 min matches the gateway app's default poll cadence × 3.
GATEWAY_STALE_SECONDS = 15 * 60
# Close-reminder cap equals the global one-shot cap. Explicit alias kept
# so future per-template overrides (e.g. payment reminders allowed 2) can
# land without re-plumbing. ONE means: one dispatch, one chance to send.
MAX_DISPATCHES_CLOSE_REMINDER = 1

# ── Absolute per-phone safety fuse (Iter 227) ───────────────────────────────
# No matter what templates, triggers, schedulers, or retries are in play, a
# single recipient phone can NEVER receive more than this many SMS from our
# queue in a rolling 24h window. The carrier flagged the user's SIM after
# ~60 duplicates hit one owner in an hour; this is the last line of defense
# if every other guard fails. Count includes any row in status
# {pending, sent, deferred, failed} — i.e. the intent counts, not just the
# physical-send.
MAX_SMS_PER_PHONE_PER_DAY = 10


async def _tomorrow_midnight_iso(org_id: str) -> str:
    """Return tomorrow 00:00:00 in the org's timezone, as an ISO-8601 string.
    Used as `deferred_until` so deferred rows re-arm at the start of the next
    local business day. Imports locally to avoid a circular module import.
    """
    from routes.close_reminder import _resolve_org_timezone
    try:
        from zoneinfo import ZoneInfo
        tz_name = await _resolve_org_timezone(org_id)
        now_local = datetime.now(ZoneInfo(tz_name))
        tomorrow = (now_local + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        # Store as UTC-ISO so Mongo string comparisons are monotonic.
        return tomorrow.astimezone(timezone.utc).isoformat()
    except Exception:
        return (datetime.now(timezone.utc) + timedelta(days=1)).replace(
            hour=16, minute=0, second=0, microsecond=0
        ).isoformat()


async def _is_org_in_quiet_hours(org_id: str) -> bool:
    """True when the organization's local wall-clock is inside the
    night-time dispatch window (DISPATCH_QUIET_START_H .. DISPATCH_QUIET_END_H).

    Used by `GET /sms/queue/pending` to block the gateway from shipping
    automated/scheduled rows overnight. Manual sends always bypass this.
    """
    if not org_id:
        return False
    try:
        from routes.close_reminder import _resolve_org_timezone, _local_now_in
        tz_name = await _resolve_org_timezone(org_id)
        local = _local_now_in(tz_name)
        h = local.hour
        start, end = DISPATCH_QUIET_START_H, DISPATCH_QUIET_END_H
        if start <= end:
            return start <= h < end
        # wrap (typical 22..7 case)
        return h >= start or h < end
    except Exception:
        return False


async def _get_org_pause_state(org_id: str) -> dict:
    """Return `{paused: bool, until: iso|None, reason: str|None}` for the
    org-wide SMS circuit breaker. Stored in the settings collection under
    key="sms_global_pause" so admins can pause ALL automated SMS for a window
    (e.g. 24h) without unmuting branches individually.
    """
    if not org_id:
        return {"paused": False, "until": None, "reason": None}
    try:
        doc = await _raw_db.settings.find_one(
            {"key": "sms_global_pause", "organization_id": org_id},
            {"_id": 0, "value": 1},
        )
        value = (doc or {}).get("value") or {}
        until = value.get("until")
        if not until:
            return {"paused": False, "until": None, "reason": None}
        if until <= now_iso():
            return {"paused": False, "until": until, "reason": value.get("reason")}
        return {
            "paused": True,
            "until": until,
            "reason": value.get("reason") or "",
        }
    except Exception:
        return {"paused": False, "until": None, "reason": None}


async def _get_gateway_heartbeat(org_id: str) -> dict:
    """Return `{last_poll_at, age_seconds, last_returned_count, healthy}` for
    the org's SMS gateway. The gateway is considered healthy if it polled
    within the last `GATEWAY_STALE_SECONDS` window. Surfaces a stuck/
    disconnected gateway BEFORE it leaks 5 days of replayed SMS at the user.
    """
    if not org_id:
        return {"last_poll_at": None, "age_seconds": None,
                "last_returned_count": None, "healthy": False}
    try:
        doc = await _raw_db.settings.find_one(
            {"key": "sms_gateway_heartbeat", "organization_id": org_id},
            {"_id": 0, "value": 1},
        )
        value = (doc or {}).get("value") or {}
        last = value.get("last_poll_at")
        if not last:
            return {"last_poll_at": None, "age_seconds": None,
                    "last_returned_count": None, "healthy": False}
        try:
            last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
            age = int((datetime.now(timezone.utc) - last_dt).total_seconds())
        except Exception:
            age = None
        return {
            "last_poll_at": last,
            "age_seconds": age,
            "last_returned_count": value.get("last_returned_count"),
            "healthy": age is not None and age <= GATEWAY_STALE_SECONDS,
        }
    except Exception:
        return {"last_poll_at": None, "age_seconds": None,
                "last_returned_count": None, "healthy": False}
# Falls back to the immutable organizations.name when the settings doc is
# missing (e.g. after Reset Company before the user re-saves Settings).
# Mirrors the contract used in sms_hooks.get_company_name — never reads
# another tenant's company_info.
async def _resolve_company_name() -> str:
    biz = await db.settings.find_one({"key": "company_info"}, {"_id": 0})
    name = (biz or {}).get("value", {}).get("name", "")
    if name:
        return name
    # Settings doc missing → look up org by its known id (organizations is not
    # auto-scoped, so we MUST use the explicit id from context to avoid
    # returning some other tenant's first-inserted org row).
    org_id = get_org_context()
    if not org_id:
        return ""
    org = await _raw_db.organizations.find_one({"id": org_id}, {"_id": 0, "name": 1})
    return (org or {}).get("name", "") if org else ""


# ── Default Templates (seeded on first access) ─────────────────────────────

DEFAULT_TEMPLATES = [
    {
        "key": "opening_balance_notice",
        "name": "Opening Balance Carry-forward Notice",
        "body": (
            "Hi <customer_name>, ang account mo po sa <company_name> "
            "may opening balance na P<amount> bilang carry-forward "
            "mula sa aming previous records (<date>). "
            "Pakiusap, settle po kapag nakakaya. Salamat! - <company_name>"
        ),
        "placeholders": ["customer_name", "amount", "company_name",
                         "branch_name", "date"],
        "trigger": "auto",
        "active": True,
    },
    {
        "key": "credit_new",
        "name": "New Credit Notification",
        "body": (
            "Hi <customer_name>, ikaw ay may bagong credit na P<amount> "
            "sa <company_name> - <branch_name> ngayong <date>. "
            "Due date: <due_date>. "
            "Current total balance mo: P<total_balance>. "
            "Salamat po! - <company_name>"
        ),
        "placeholders": ["customer_name", "amount", "company_name", "branch_name",
                         "date", "due_date", "total_balance"],
        "trigger": "auto",
        "active": True,
    },
    {
        "key": "reminder_15day",
        "name": "15-Day Due Reminder",
        "body": (
            "Reminder: Hi <customer_name>, may balance ka pong "
            "P<total_balance> sa <company_name>. "
            "May P<amount_due_soon> na due sa <due_date> (15 days na lang po). "
            "Para maiwasan ang interest, bayaran po bago mag-due. Salamat!"
        ),
        "placeholders": ["customer_name", "total_balance", "company_name",
                         "amount_due_soon", "due_date"],
        "trigger": "scheduled",
        "active": True,
    },
    {
        "key": "reminder_7day",
        "name": "7-Day Due Reminder (with interest estimate)",
        "body": (
            "Urgent: Hi <customer_name>, P<amount_due_soon> mo po sa "
            "<company_name> ay due na sa <due_date> (7 days na lang). "
            "Kung hindi mababayaran on time, estimated interest po ay "
            "~P<est_interest>/month (<interest_rate>%/mo). "
            "Current total balance: P<total_balance>. "
            "Paki-settle na po. Salamat!"
        ),
        "placeholders": ["customer_name", "amount_due_soon", "company_name",
                         "due_date", "est_interest", "interest_rate", "total_balance"],
        "trigger": "scheduled",
        "active": True,
    },
    {
        "key": "overdue_notice",
        "name": "Overdue Notice",
        "body": (
            "Hi <customer_name>, ang P<amount_overdue> mo po sa "
            "<company_name> ay <days_overdue> days na overdue. "
            "Interest is accruing at <interest_rate>%/mo. "
            "Current total balance: P<total_balance>. "
            "Paki-settle na po agad. Salamat!"
        ),
        "placeholders": ["customer_name", "amount_overdue", "company_name",
                         "days_overdue", "interest_rate", "total_balance"],
        "trigger": "scheduled",
        "active": True,
    },
    {
        "key": "payment_received",
        "name": "Payment Received Confirmation",
        "body": (
            "Salamat <customer_name>! Natanggap na namin ang P<amount_paid> mo"
            "<applied_to>. "
            "Remaining balance: P<remaining_balance>. "
            "<next_due_info>"
            "Salamat po! - <company_name>"
        ),
        "placeholders": ["customer_name", "amount_paid", "applied_to",
                         "remaining_balance", "next_due_info", "company_name"],
        "trigger": "auto",
        "active": True,
    },
    {
        "key": "charge_applied",
        "name": "Interest/Penalty Applied",
        "body": (
            "Notice: <charge_type> of P<charge_amount><source_invoice>"
            "<period> ay na-apply sa account mo, <customer_name>. "
            "New balance: P<total_balance>. "
            "Para maiwasan ang dagdag charges, bayaran po agad. "
            "- <company_name>"
        ),
        "placeholders": ["charge_type", "charge_amount", "source_invoice",
                         "period", "customer_name", "total_balance", "company_name"],
        "trigger": "auto",
        "active": True,
    },
    {
        "key": "delivery_ready",
        "name": "Delivery/Pickup Ready",
        "body": (
            "Hi <customer_name>, ang order mo po sa <company_name> - <branch_name> "
            "ay ready na for pickup. Ref: <reference_number>. "
            "Salamat po!"
        ),
        "placeholders": ["customer_name", "company_name", "branch_name",
                         "reference_number"],
        "trigger": "manual",
        "active": True,
    },
    {
        "key": "promo_blast",
        "name": "Promotional Message",
        "body": (
            "Hi <customer_name>! <promo_message> "
            "- <company_name> <branch_name>"
        ),
        "placeholders": ["customer_name", "promo_message", "company_name",
                         "branch_name"],
        "trigger": "manual",
        "active": True,
    },
    {
        "key": "monthly_summary",
        "name": "Monthly Statement Summary",
        "body": (
            "Monthly Summary: Hi <customer_name>, total balance mo po sa "
            "<company_name> ay P<total_balance>. "
            "Overdue: P<overdue_amount>. "
            "Paki-visit po kami para ma-settle. Salamat!"
        ),
        "placeholders": ["customer_name", "company_name", "total_balance",
                         "overdue_amount"],
        "trigger": "scheduled",
        "active": True,
    },
    {
        "key": "custom",
        "name": "Custom Message",
        "body": "<message>",
        "placeholders": ["message"],
        "trigger": "manual",
        "active": True,
    },
    # ── Staff CC notifications (manager / owner) ─────────────────────────────
    {
        "key": "credit_new_staff",
        "name": "New Credit — Staff CC (Manager)",
        "body": (
            "[New Credit] <customer_name> - P<amount> on <date>. "
            "Invoice: <invoice_number>. Due: <due_date>. "
            "Total balance: P<total_balance>."
        ),
        "placeholders": ["customer_name", "amount", "date", "invoice_number",
                         "due_date", "total_balance"],
        "trigger": "auto",
        "active": True,
    },
    {
        "key": "charge_applied_staff",
        "name": "Interest/Penalty Applied — Staff CC (Manager)",
        "body": (
            "[<charge_type> Applied] <customer_name> - P<charge_amount> charged"
            "<source_invoice>. New total balance: P<total_balance>. "
            "Customer notified via SMS."
        ),
        "placeholders": ["charge_type", "customer_name", "charge_amount",
                         "source_invoice", "total_balance"],
        "trigger": "auto",
        "active": True,
    },
    {
        "key": "crop_season_started_owner",
        "name": "Crop Season Started — Owner CC",
        "body": (
            "[New Crop Season] <customer_name> - planting <planting_date> | "
            "due <harvest_date>. Balance: P<total_balance>."
        ),
        "placeholders": ["customer_name", "planting_date", "harvest_date",
                         "total_balance"],
        "trigger": "auto",
        "active": True,
    },
    # ── Reversal notifications (close the loop with customer) ────────────────
    {
        "key": "sale_voided",
        "name": "Sale Voided — Customer Notice",
        "body": (
            "Hi <customer_name>, ang inyong invoice <invoice_number> "
            "(P<grand_total>) sa <company_name> ay na-void na ngayong <date>. "
            "<balance_note>"
            "Reason: <reason>. "
            "Salamat po!"
        ),
        "placeholders": ["customer_name", "invoice_number", "grand_total",
                         "company_name", "date", "balance_note", "reason"],
        "trigger": "auto",
        "active": True,
    },
    {
        "key": "refund_processed",
        "name": "Refund Processed — Customer Notice",
        "body": (
            "Hi <customer_name>, na-process na ang inyong return "
            "<rma_number> sa <company_name>. "
            "<refund_line>"
            "<credit_line>"
            "Reason: <reason>. Salamat po!"
        ),
        "placeholders": ["customer_name", "rma_number", "company_name",
                         "refund_line", "credit_line", "reason"],
        "trigger": "auto",
        "active": True,
    },
    # ── Branch Transfer Approval Workflow ────────────────────────────────────
    {
        "key": "transfer_pending_approval",
        "name": "Transfer Pending Approval (to Admin)",
        "body": (
            "Hi <admin_name>, <requester_name> submitted Branch Transfer "
            "<order_number> from <from_branch> to <to_branch> "
            "(<items_count> item/s, P<cost_total>). "
            "Review & approve: <approval_link>"
        ),
        "placeholders": ["admin_name", "requester_name", "order_number",
                         "from_branch", "to_branch", "items_count",
                         "cost_total", "approval_link", "company_name"],
        "trigger": "auto",
        "active": True,
    },
    {
        "key": "transfer_approved",
        "name": "Transfer Approved (to Manager)",
        "body": (
            "Hi <manager_name>, your Branch Transfer <order_number> "
            "(<from_branch> -> <to_branch>) has been APPROVED by "
            "<approver_name> and dispatched. You may now print and send."
        ),
        "placeholders": ["manager_name", "approver_name", "order_number",
                         "from_branch", "to_branch"],
        "trigger": "auto",
        "active": True,
    },
    {
        "key": "transfer_rejected",
        "name": "Transfer Rejected (to Manager)",
        "body": (
            "Hi <manager_name>, your Branch Transfer <order_number> was "
            "RETURNED by <rejecter_name>. Reason: <reason>. Please review, "
            "fix, and resubmit."
        ),
        "placeholders": ["manager_name", "rejecter_name", "order_number",
                         "from_branch", "reason"],
        "trigger": "auto",
        "active": True,
    },
    {
        "key": "branch_stock_request",
        "name": "Branch Stock Request (to Recipients)",
        "body": (
            "Hi <recipient_name>, <requesting_branch> requested stocks from "
            "<supply_branch> (PO <po_number>, <items_count> item/s). "
            "Top items: <items_summary>. "
            "View: <view_link>"
        ),
        "placeholders": ["recipient_name", "requesting_branch", "supply_branch",
                         "po_number", "items_count", "items_summary",
                         "view_link", "company_name"],
        "trigger": "auto",
        "active": True,
    },
    # ── Crop Credit Notifications ────────────────────────────────────────────
    {
        "key": "crop_season_started",
        "name": "Crop Season Started",
        "body": (
            "Hi <customer_name>, nagsimula na ang inyong Charged-to-Crop account "
            "sa <company_name>. "
            "Planting date: <planting_date>. "
            "Expected harvest / due date: <harvest_date>. "
            "Kasalukuyang balance: P<total_balance>. "
            "Salamat po!"
        ),
        "placeholders": ["customer_name", "company_name", "planting_date",
                         "harvest_date", "total_balance"],
        "trigger": "auto",
        "active": True,
    },
    {
        "key": "crop_credit_added",
        "name": "Crop Credit — New Purchase Added",
        "body": (
            "Hi <customer_name>, may bagong na-charge na P<amount> "
            "sa inyong Charged-to-Crop account sa <company_name>. "
            "Invoice: <invoice_number>. "
            "Running total balance: P<total_balance>. "
            "Due: <harvest_date>. Salamat!"
        ),
        "placeholders": ["customer_name", "amount", "company_name",
                         "invoice_number", "total_balance", "harvest_date"],
        "trigger": "auto",
        "active": True,
    },
    {
        "key": "crop_harvest_15day",
        "name": "Crop Harvest Reminder — 15 Days",
        "body": (
            "Magandang araw, <customer_name>! "
            "Paalala: ang inyong Charged-to-Crop account sa <company_name> "
            "ay magtatapos na sa <harvest_date> (15 araw na lang). "
            "Kasalukuyang kabuuang balance: P<total_balance>. "
            "Pakihandaan na ang pagbabayad. Salamat po!"
        ),
        "placeholders": ["customer_name", "company_name", "harvest_date", "total_balance"],
        "trigger": "scheduled",
        "active": True,
    },
    {
        "key": "crop_harvest_7day",
        "name": "Crop Harvest Reminder — 7 Days",
        "body": (
            "Urgent: <customer_name>, 7 araw na lang sa inyong harvest due date "
            "(<harvest_date>) sa <company_name>. "
            "Kabuuang balance: P<total_balance>. "
            "Makipag-ugnayan sa amin para sa settlement. Salamat!"
        ),
        "placeholders": ["customer_name", "company_name", "harvest_date", "total_balance"],
        "trigger": "scheduled",
        "active": True,
    },
    {
        "key": "crop_harvest_due",
        "name": "Crop Harvest Due Today",
        "body": (
            "Pagpapaalala: <customer_name>, ngayon na ang inyong harvest due date. "
            "Kabuuang babayaran sa <company_name>: P<total_balance>. "
            "Makipag-ugnayan sa amin ngayon. Maraming salamat!"
        ),
        "placeholders": ["customer_name", "company_name", "total_balance"],
        "trigger": "scheduled",
        "active": True,
    },
    {
        "key": "crop_extension",
        "name": "Crop Season Extended",
        "body": (
            "Abiso: <customer_name>, ang inyong Charged-to-Crop account sa "
            "<company_name> ay na-extend ng 15 araw. "
            "Bagong due date: <new_harvest_date>. "
            "Kasalukuyang balance: P<total_balance>. "
            "Pakitiyak ang payment sa bagong due date. Salamat!"
        ),
        "placeholders": ["customer_name", "company_name", "new_harvest_date",
                         "total_balance"],
        "trigger": "auto",
        "active": True,
    },
    {
        "key": "crop_overdue_notice",
        "name": "Crop Season Overdue Reminder (Post-Harvest)",
        "body": (
            "Paalala: <customer_name>, ang inyong Charged-to-Crop account sa "
            "<company_name> ay <days_overdue> araw nang overdue mula sa "
            "harvest date na <harvest_date>. "
            "Kabuuang dapat bayaran: P<total_balance>. "
            "Makipag-ugnayan sa amin agad para maiwasan ang dagdag charges. "
            "Salamat po!"
        ),
        "placeholders": ["customer_name", "company_name", "days_overdue",
                         "harvest_date", "total_balance"],
        "trigger": "scheduled",
        "active": True,
    },
    # ── Close-Day Reminders & Z-Report Finalized ─────────────────────────────
    {
        "key": "close_catchup_3pm",
        "name": "Close Day — Mid-Afternoon Catch-Up (3 PM)",
        "body": (
            "AgriBooks Mid-Day Snapshot\n<branch_name> - <date>\n\n"
            "Encoded so far: <sales_count> sales (P<sales_total>), "
            "<credit_count> credits (P<credit_total>), <expense_count> expenses.\n"
            "Cash expected: P<cash_expected>.\n\n"
            "Any credit slips or receipts still pending? Encode before close. "
            "You have <hours_to_close>hrs left."
        ),
        "placeholders": ["branch_name", "date", "sales_count", "sales_total",
                         "credit_count", "credit_total", "expense_count",
                         "cash_expected", "hours_to_close"],
        "trigger": "scheduled", "active": True,
    },
    {
        "key": "close_precheck",
        "name": "Close Day — Pre-Close Ping",
        "body": (
            "AgriBooks: Time to close <branch_name>.\n"
            "Today (<date>): <sales_count> sales, P<cash_expected> cash expected, "
            "<pending_credits> pending credits. "
            "Open Close Wizard when ready."
        ),
        "placeholders": ["branch_name", "date", "sales_count", "cash_expected",
                         "pending_credits"],
        "trigger": "scheduled", "active": True,
    },
    {
        "key": "close_late_notice",
        "name": "Close Day — Late Notice",
        "body": (
            "AgriBooks: <branch_name> close is overdue by ~<hours_overdue>h. "
            "Please finalize <date>. "
            "If you cannot close tonight, inform the owner immediately."
        ),
        "placeholders": ["branch_name", "date", "hours_overdue"],
        "trigger": "scheduled", "active": True,
    },
    {
        "key": "close_status_snapshot",
        "name": "Close Day — Status Snapshot",
        "body": (
            "Status: <branch_name> still OPEN for <date>.\n"
            "Sales: <sales_count> (P<sales_total>)\n"
            "Credits: <credit_count> (P<credit_total>)\n"
            "Expenses: <expense_count> (P<expense_total>)\n"
            "Cash expected: P<cash_expected>\n"
            "Pending credits: <pending_credits>"
        ),
        "placeholders": ["branch_name", "date", "sales_count", "sales_total",
                         "credit_count", "credit_total", "expense_count",
                         "expense_total", "cash_expected", "pending_credits"],
        "trigger": "scheduled", "active": True,
    },
    {
        "key": "close_escalation",
        "name": "Close Day — Hard Escalation",
        "body": (
            "URGENT: <branch_name> has NOT closed <date>. "
            "Owner / auditor attention needed. "
            "If the assigned cashier is unable to close, please reassign or close remotely."
        ),
        "placeholders": ["branch_name", "date"],
        "trigger": "scheduled", "active": True,
    },
    {
        "key": "close_overdue_next_day",
        "name": "Close Day — Overdue Next Morning",
        "body": (
            "<branch_name> did NOT close <date>. "
            "Owner attention: assigned cashier may have skipped closing duty. "
            "Please finalize <date> or reassign."
        ),
        "placeholders": ["branch_name", "date"],
        "trigger": "scheduled", "active": True,
    },
    {
        "key": "close_overdue_multi_day",
        "name": "Close Day — Multi-Day Overdue",
        "body": (
            "URGENT: <branch_name> is <days_overdue> days overdue on closing <date>. "
            "Serious accountability issue — owner action required. "
            "Consider reassigning the closing duty."
        ),
        "placeholders": ["branch_name", "date", "days_overdue"],
        "trigger": "scheduled", "active": True,
    },
    {
        "key": "zreport_finalized",
        "name": "Z-Report Finalized",
        "body": (
            "<branch_name> closed <date> at <closed_time>.\n"
            "Sales: P<sales_total> (<sales_count> txns)\n"
            "Cash: P<cash_total> / Credit: P<credit_total> / Digital: P<digital_total>\n"
            "AR encoded: <credit_count> (P<credit_total>)\n"
            "Expenses: <expense_count> (P<expense_total>)\n"
            "Cash drawer: P<cash_actual> vs P<cash_expected> (<over_short>)\n"
            "<late_encode_note>"
            "Closed by: <closer_name>\n"
            "View Report: <zreport_link>"
        ),
        "placeholders": ["branch_name", "date", "closed_time", "sales_total",
                         "sales_count", "cash_total", "credit_total",
                         "digital_total", "credit_count", "expense_count",
                         "expense_total", "cash_actual", "cash_expected",
                         "over_short", "late_encode_note", "closer_name",
                         "zreport_link"],
        "trigger": "auto", "active": True,
    },
    {
        # Iter 253 — owner alert when a Z-Report share link was auto-revoked
        # because too many unique IPs accessed it (forwarding suspected).
        "key": "zreport_share_auto_revoked",
        "name": "Z-Report Share Link Auto-Revoked",
        "body": (
            "ALERT: Z-Report share link for <branch_id> on <date> was "
            "auto-revoked after <ips> unique IPs accessed it. "
            "Original recipient: <recipient>. Review in Audit Center."
        ),
        "placeholders": ["branch_id", "date", "ips", "recipient"],
        "trigger": "auto", "active": True,
    },
]


async def _ensure_templates():
    """
    Upsert default templates with version-aware self-healing.

    On every call we do three things, in order:
      1. Insert any DEFAULT_TEMPLATES that don't yet exist for the current org.
      2. Backfill the `default_body` snapshot on legacy template docs that
         were seeded before this field existed — conservatively, only when
         the current body still matches a known stale default (see
         LEGACY_DEFAULT_BODIES below). Once `default_body` is set, future
         upgrades are clean.
      3. Auto-upgrade any template whose `body == default_body` (i.e. user
         has not customized the wording) to the latest default_body shipped
         in code. Customized templates are left untouched — operators keep
         their edits, and only the unedited "factory" wording gets refreshed.

    Idempotent. Safe to call on startup, on GET /sms/templates, and inside
    queue_sms self-seed.
    """
    # Pull every existing template doc (org-scoped via db wrapper)
    existing_docs = []
    async for doc in db.sms_templates.find({}, {"_id": 0}):
        existing_docs.append(doc)
    existing_by_key = {d["key"]: d for d in existing_docs}

    # 1️⃣ Insert missing keys with default_body snapshot
    now = now_iso()
    missing = [t for t in DEFAULT_TEMPLATES if t["key"] not in existing_by_key]
    if missing:
        docs = [
            {**t, "id": new_id(), "default_body": t["body"],
             "created_at": now, "updated_at": now}
            for t in missing
        ]
        await db.sms_templates.insert_many(docs)

    # 2️⃣ + 3️⃣ Backfill default_body and upgrade unedited templates
    for default in DEFAULT_TEMPLATES:
        key = default["key"]
        existing = existing_by_key.get(key)
        if not existing:
            continue
        new_body = default["body"]
        stored_default = existing.get("default_body")
        current_body = existing.get("body", "")

        if stored_default is None:
            # Legacy doc — backfill conservatively. If the current body
            # matches one of the known stale defaults for this key, we
            # treat it as "unedited" and upgrade. Otherwise we anchor
            # default_body to whatever they currently have so future
            # ships can detect customization correctly.
            stale_set = LEGACY_DEFAULT_BODIES.get(key, set())
            if current_body in stale_set or current_body == new_body:
                await db.sms_templates.update_one(
                    {"id": existing["id"]},
                    {"$set": {
                        "body": new_body,
                        "default_body": new_body,
                        "name": default.get("name", existing.get("name", "")),
                        "placeholders": default.get("placeholders", existing.get("placeholders", [])),
                        "updated_at": now,
                    }},
                )
            else:
                # User customized — anchor default_body to their current
                # body so we never accidentally clobber it later.
                await db.sms_templates.update_one(
                    {"id": existing["id"]},
                    {"$set": {"default_body": current_body, "updated_at": now}},
                )
        elif stored_default != new_body and current_body == stored_default:
            # Unedited template, factory wording changed → upgrade
            await db.sms_templates.update_one(
                {"id": existing["id"]},
                {"$set": {
                    "body": new_body,
                    "default_body": new_body,
                    "name": default.get("name", existing.get("name", "")),
                    "placeholders": default.get("placeholders", existing.get("placeholders", [])),
                    "updated_at": now,
                }},
            )


# Known stale default bodies from previous releases. Used to identify legacy
# template docs that still carry the OLD wording so we can safely refresh
# them in step 2 of _ensure_templates. ONLY add bodies that were the literal
# DEFAULT in code at some point — never user-edited content.
LEGACY_DEFAULT_BODIES = {
    "close_late_notice": {
        # Pre-Apr-2026 wording that falsely threatened "Sales BLOCKED"
        ("AgriBooks: <branch_name> close is overdue. Sales are BLOCKED until "
         "you finalize <date>. Open Close Wizard now."),
        ("AgriBooks: <branch_name> close is overdue by <hours_overdue>h. "
         "Sales are BLOCKED until you finalize <date>. Open Close Wizard now."),
    },
    "close_escalation": {
        ("URGENT: <branch_name> has NOT closed <date>. Sales are BLOCKED. "
         "Owner / auditor attention needed."),
        ("URGENT: <branch_name> has NOT closed <date>. Sales BLOCKED. "
         "Owner / auditor attention needed."),
    },
    "close_overdue_next_day": {
        ("<branch_name> did NOT close <date>. Sales remain BLOCKED. "
         "Owner attention required."),
    },
    "close_overdue_multi_day": {
        ("URGENT: <branch_name> is <days_overdue> days overdue on closing <date>. "
         "Sales BLOCKED. Owner action required."),
    },
    "zreport_finalized": {
        # Pre-Iter-259: old default WITHOUT the share link
        ("<branch_name> closed <date> at <closed_time>.\n"
         "Sales: P<sales_total> (<sales_count> txns)\n"
         "Cash: P<cash_total> / Credit: P<credit_total> / Digital: P<digital_total>\n"
         "AR encoded: <credit_count> (P<credit_total>)\n"
         "Expenses: <expense_count> (P<expense_total>)\n"
         "Cash drawer: P<cash_actual> vs P<cash_expected> (<over_short>)\n"
         "<late_encode_note>"
         "Closed by: <closer_name>"),
    },
}


# ── Template rendering helper ───────────────────────────────────────────────

import re as _re_template

_PLACEHOLDER_RE = _re_template.compile(r"<([a-z_][a-z0-9_]*)>")


def render_template(body: str, variables: dict) -> str:
    """Replace <placeholder> tokens with actual values.

    Iter 244 audit fix: previously, any placeholder missing from `variables`
    was left as literal `<name>` text in the outgoing SMS — so recipients
    occasionally saw `<hours_to_close>hrs left` etc. We now substitute via
    regex so unknown placeholders silently collapse to an empty string,
    and we log a single WARN line listing which keys were missing so
    operators can diagnose coverage gaps from the backend log.
    """
    import logging
    missing: list[str] = []

    def _sub(match):
        key = match.group(1)
        if key in variables and variables[key] is not None:
            return str(variables[key])
        missing.append(key)
        return ""

    result = _PLACEHOLDER_RE.sub(_sub, body or "")
    if missing:
        logging.getLogger("sms").warning(
            f"render_template — missing keys substituted as empty string: {sorted(set(missing))}"
        )
    return result


# ── Queue a single SMS (called by hooks / scheduler) ───────────────────────

async def queue_sms(
    template_key: str,
    customer_id: str,
    customer_name: str,
    phone: str,
    variables: dict,
    organization_id: str = "",
    branch_id: str = "",
    branch_name: str = "",
    trigger: str = "auto",
    trigger_ref: str = "",
    dedup_key: str = "",
):
    """Insert an SMS into the queue. Scoped to organization_id for multi-tenant isolation.

    Logs a structured WARN line for each bail-out reason so operators can
    diagnose "my SMS didn't fire" via grep on the backend log.
    """
    import logging
    sms_log = logging.getLogger("sms")
    if not phone or not phone.strip():
        sms_log.warning(f"queue_sms skipped — no phone | template={template_key} customer={customer_id} org={organization_id} ref={trigger_ref}")
        return None

    # Build org filter for _raw_db reads
    org_filter = {"organization_id": organization_id} if organization_id else {}

    # Check template — org-scoped first, fallback to global default.
    # SELF-HEALING: if the requested template key is missing for this org, seed
    # JUST THAT KEY from DEFAULT_TEMPLATES. This handles two cases:
    #   (a) brand new tenant — never opened Settings → Messages
    #   (b) new template key shipped in a later release — existing tenants
    #       already have other templates seeded, so a "count == 0" gate fails
    #       to backfill the new key (resulting in silent template_missing).
    template = None
    if organization_id:
        template = await _raw_db.sms_templates.find_one({"key": template_key, "organization_id": organization_id}, {"_id": 0})
        if not template:
            # Try to seed just the requested key from DEFAULT_TEMPLATES (per-key upsert)
            default = next((t for t in DEFAULT_TEMPLATES if t["key"] == template_key), None)
            if default:
                try:
                    seed_doc = {
                        **default, "id": new_id(), "organization_id": organization_id,
                        "default_body": default["body"],
                        "created_at": now_iso(), "updated_at": now_iso(),
                    }
                    await _raw_db.sms_templates.update_one(
                        {"key": template_key, "organization_id": organization_id},
                        {"$setOnInsert": seed_doc},
                        upsert=True,
                    )
                    sms_log.info(f"queue_sms auto-seeded missing template key={template_key} for org={organization_id}")
                    template = await _raw_db.sms_templates.find_one(
                        {"key": template_key, "organization_id": organization_id}, {"_id": 0}
                    )
                except Exception as seed_err:
                    sms_log.error(f"queue_sms auto-seed failed for key={template_key} org={organization_id}: {seed_err}")
    if not template:
        template = await _raw_db.sms_templates.find_one({"key": template_key, "organization_id": {"$exists": False}}, {"_id": 0})
    if not template:
        template = await _raw_db.sms_templates.find_one({"key": template_key}, {"_id": 0})
    if not template or not template.get("active", True):
        reason = "template_missing" if not template else "template_inactive"
        sms_log.warning(f"queue_sms skipped — {reason} | template={template_key} org={organization_id} customer={customer_id} ref={trigger_ref}")
        return None

    # ── Legacy-body self-heal (Iter 260) ────────────────────────────────────
    # If the org's stored template body matches a known legacy default
    # (e.g. the pre-share-link `zreport_finalized` body), upgrade to the
    # current factory body before render. Without this, every send
    # silently drops new placeholders like `<zreport_link>` because the
    # OLD body never references them. _ensure_templates() does this
    # globally but is only triggered by a few admin paths (Settings →
    # Messages list, backfill); the close-day SMS used to be queued
    # before the operator ever opened those screens, so the upgrade
    # never landed in time. Idempotent and per-key so we don't pay an
    # ALL-templates pass on every queue.
    try:
        current_body = template.get("body", "") or ""
        new_default = next(
            (t for t in DEFAULT_TEMPLATES if t["key"] == template_key), None
        )
        if new_default:
            new_body = new_default["body"]
            stale_set = LEGACY_DEFAULT_BODIES.get(template_key, set())
            stored_default = template.get("default_body")
            should_upgrade = (
                # Body matches a known legacy default → unedited, safe to upgrade
                (current_body in stale_set and current_body != new_body)
                # Or default_body anchor says they're on a stale factory build
                or (stored_default in stale_set and current_body == stored_default
                    and stored_default != new_body)
            )
            if should_upgrade:
                await _raw_db.sms_templates.update_one(
                    {"id": template["id"]},
                    {"$set": {
                        "body": new_body,
                        "default_body": new_body,
                        "name": new_default.get("name", template.get("name", "")),
                        "placeholders": new_default.get(
                            "placeholders", template.get("placeholders", [])
                        ),
                        "updated_at": now_iso(),
                    }},
                )
                sms_log.info(
                    f"queue_sms auto-upgraded legacy template body | "
                    f"key={template_key} org={organization_id}"
                )
                template["body"] = new_body
                template["default_body"] = new_body
    except Exception as upg_err:
        sms_log.error(
            f"queue_sms legacy self-heal failed | key={template_key} "
            f"org={organization_id}: {upg_err}"
        )

    # Check per-trigger setting — STRICTLY org-scoped to prevent cross-org bleed.
    # Previously this fell back to ANY org's sms_settings if the scoped lookup
    # failed; that caused another tenant's enable/disable flags to be honored
    # for this org. Now: if no org-scoped setting exists, default to enabled.
    setting = None
    base_setting_query = {"trigger_key": template_key, "$or": [{"branch_id": branch_id}, {"branch_id": None}, {"branch_id": ""}]}
    if organization_id:
        setting = await _raw_db.sms_settings.find_one({**base_setting_query, "organization_id": organization_id}, {"_id": 0})
    if setting and not setting.get("enabled", True):
        sms_log.warning(f"queue_sms skipped — trigger_disabled in Settings | template={template_key} org={organization_id} branch={branch_id} customer={customer_id}")
        return None

    # ── GLOBAL PAUSE GATE (Iter 237) ────────────────────────────────────────
    # While an admin-triggered org-wide pause is active, don't enqueue any
    # automated/scheduled SMS — they'd just pile up unbounded (and stale) in
    # the queue until resume. Manual composes always bypass so admins can
    # reach customers urgently during the pause.
    if organization_id and trigger in ("auto", "scheduled"):
        pause = await _get_org_pause_state(organization_id)
        if pause.get("paused"):
            sms_log.info(
                f"queue_sms skipped — org sms paused until {pause.get('until')} "
                f"| template={template_key} org={organization_id}"
            )
            return None

    # De-duplication — always org-scoped to prevent cross-company dedup conflicts
    if dedup_key:
        existing = await _raw_db.sms_queue.find_one({"dedup_key": dedup_key, **org_filter}, {"_id": 0})
        if existing:
            sms_log.info(f"queue_sms skipped — duplicate dedup_key={dedup_key} (already queued)")
            return None

    # Per-recipient throttle (Iter 216) — stops scheduler-storms during gateway
    # outages from piling multiple copies of the same reminder onto the same
    # phone. Only applies to `auto`-trigger templates; manual sends are always
    # honoured so the user can urgently SMS a customer again.
    if trigger == "auto":
        throttle_cutoff = (
            datetime.now(timezone.utc) - timedelta(seconds=ENQUEUE_THROTTLE_SECONDS)
        ).isoformat()
        recent = await _raw_db.sms_queue.find_one(
            {
                "template_key": template_key,
                "phone": phone.strip(),
                "status": {"$in": ["pending", "sent", "deferred"]},
                "created_at": {"$gte": throttle_cutoff},
                **org_filter,
            },
            {"_id": 0, "id": 1, "created_at": 1},
        )
        if recent:
            sms_log.info(
                f"queue_sms skipped — throttled (same template+phone within "
                f"{ENQUEUE_THROTTLE_SECONDS//60}min) | template={template_key} "
                f"phone={phone} recent_id={recent.get('id')}"
            )
            return None

    # Absolute per-phone-per-day fuse (Iter 227) — NO recipient may receive
    # more than MAX_SMS_PER_PHONE_PER_DAY distinct queue rows in the rolling
    # 24h window, regardless of template, trigger, or scheduler. This is the
    # final safeguard that would have prevented the carrier from flagging
    # the user's SIM. Auto-triggers are enforced hard; manual composes bypass
    # this (admin has consciously composed each message) because otherwise
    # the user can't urgently reach a customer late in the day.
    if trigger == "auto":
        day_cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=24)
        ).isoformat()
        recent_count = await _raw_db.sms_queue.count_documents({
            "phone": phone.strip(),
            "status": {"$in": ["pending", "sent", "deferred", "failed"]},
            "created_at": {"$gte": day_cutoff},
            **org_filter,
        })
        if recent_count >= MAX_SMS_PER_PHONE_PER_DAY:
            sms_log.warning(
                f"queue_sms BLOCKED — per-phone daily cap hit ({recent_count} "
                f">= {MAX_SMS_PER_PHONE_PER_DAY}) | template={template_key} "
                f"phone={phone} org={organization_id}"
            )
            return None

    message = render_template(template["body"], variables)
    doc = {
        "id": new_id(),
        "organization_id": organization_id,
        "template_key": template_key,
        "customer_id": customer_id,
        "customer_name": customer_name,
        "phone": phone.strip(),
        "message": message,
        "status": "pending",
        "trigger": trigger,
        "trigger_ref": trigger_ref,
        "dedup_key": dedup_key,
        "branch_id": branch_id,
        "branch_name": branch_name,
        "created_at": now_iso(),
        "sent_at": None,
        "failed_at": None,
        "error": None,
        "retry_count": 0,
        # Spam-storm protection (Iter 216)
        "dispatch_count": 0,
        "leased_until": None,
        "deferred_until": None,
    }
    await _raw_db.sms_queue.insert_one(doc)
    del doc["_id"]
    return doc


# ═══════════════════════════════════════════════════════════════════════════
# REST API
# ═══════════════════════════════════════════════════════════════════════════


# ── Templates ───────────────────────────────────────────────────────────────

@router.get("/templates")
async def list_templates(user=Depends(get_current_user)):
    """List all SMS templates."""
    await _ensure_templates()
    templates = await db.sms_templates.find({}, {"_id": 0}).to_list(50)
    return templates


@router.put("/templates/{template_id}")
async def update_template(template_id: str, data: dict, user=Depends(get_current_user)):
    """Update template body or active status."""
    check_perm(user, "settings", "edit")
    allowed = {"body", "name", "active"}
    update = {k: v for k, v in data.items() if k in allowed}
    if not update:
        raise HTTPException(status_code=400, detail="Nothing to update")
    update["updated_at"] = now_iso()
    result = await db.sms_templates.update_one({"id": template_id}, {"$set": update})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Template not found")
    return await db.sms_templates.find_one({"id": template_id}, {"_id": 0})


# ── Settings ────────────────────────────────────────────────────────────────

@router.get("/settings")
async def list_sms_settings(user=Depends(get_current_user)):
    """List SMS trigger settings."""
    await _ensure_templates()
    settings = await db.sms_settings.find({}, {"_id": 0}).to_list(100)
    # Return merged with template defaults
    templates = await db.sms_templates.find({}, {"_id": 0}).to_list(50)
    settings_map = {s["trigger_key"]: s for s in settings}
    result = []
    for t in templates:
        s = settings_map.get(t["key"], {})
        result.append({
            "trigger_key": t["key"],
            "template_name": t["name"],
            "enabled": s.get("enabled", True),
            "branch_id": s.get("branch_id"),
        })
    return result


@router.put("/settings/{trigger_key}")
async def update_sms_setting(trigger_key: str, data: dict, user=Depends(get_current_user)):
    """Enable/disable a specific SMS trigger."""
    check_perm(user, "settings", "edit")
    enabled = data.get("enabled", True)
    branch_id = data.get("branch_id")
    await db.sms_settings.update_one(
        {"trigger_key": trigger_key, "branch_id": branch_id},
        {"$set": {"enabled": enabled, "updated_at": now_iso()},
         "$setOnInsert": {"id": new_id(), "trigger_key": trigger_key, "branch_id": branch_id}},
        upsert=True,
    )
    return {"trigger_key": trigger_key, "enabled": enabled}


# ── Per-trigger recipient role configuration ────────────────────────────────
# Determines WHICH user roles receive the SMS for a given trigger.
# Currently used by `branch_stock_request`. Stored in `db.sms_recipient_config`.
DEFAULT_RECIPIENT_CONFIG = {
    "branch_stock_request": {
        "include_admins": True,           # All org admins
        "include_supply_manager": True,   # Manager(s) of supply branch
        "include_supply_auditor": False,  # Auditor(s) of supply branch
        "include_all_supply_users": False,# Every active user of supply branch
    }
}


@router.get("/recipients/{trigger_key}")
async def get_recipient_config(trigger_key: str, user=Depends(get_current_user)):
    """Fetch role-based recipient settings for a trigger (e.g. branch_stock_request)."""
    doc = await db.sms_recipient_config.find_one({"trigger_key": trigger_key}, {"_id": 0})
    defaults = DEFAULT_RECIPIENT_CONFIG.get(trigger_key, {})
    if not doc:
        return {"trigger_key": trigger_key, **defaults}
    # Merge defaults with stored values so newly-added flags don't break old configs
    return {"trigger_key": trigger_key, **defaults, **{k: v for k, v in doc.items() if k != "trigger_key"}}


@router.put("/recipients/{trigger_key}")
async def update_recipient_config(trigger_key: str, data: dict, user=Depends(get_current_user)):
    """Update role-based recipient settings for a trigger."""
    check_perm(user, "settings", "edit")
    if trigger_key not in DEFAULT_RECIPIENT_CONFIG:
        raise HTTPException(status_code=400, detail=f"Trigger '{trigger_key}' has no recipient config")
    allowed = set(DEFAULT_RECIPIENT_CONFIG[trigger_key].keys())
    update = {k: bool(v) for k, v in (data or {}).items() if k in allowed}
    update["updated_at"] = now_iso()
    await db.sms_recipient_config.update_one(
        {"trigger_key": trigger_key},
        {"$set": update, "$setOnInsert": {"id": new_id(), "trigger_key": trigger_key}},
        upsert=True,
    )
    doc = await db.sms_recipient_config.find_one({"trigger_key": trigger_key}, {"_id": 0})
    defaults = DEFAULT_RECIPIENT_CONFIG[trigger_key]
    return {"trigger_key": trigger_key, **defaults, **{k: v for k, v in doc.items() if k != "trigger_key"}}


# ── Queue ───────────────────────────────────────────────────────────────────

@router.get("/queue")
async def list_sms_queue(
    status: Optional[str] = None,
    branch_id: Optional[str] = None,
    trigger_ref: Optional[str] = None,
    limit: int = 100,
    skip: int = 0,
    user=Depends(get_current_user),
):
    """List SMS queue entries. Filter by status, branch, and/or trigger_ref."""
    query = {}
    if status:
        query["status"] = status
    if branch_id:
        query["branch_id"] = branch_id
    if trigger_ref:
        query["trigger_ref"] = trigger_ref
    total = await db.sms_queue.count_documents(query)
    items = await db.sms_queue.find(query, {"_id": 0}).sort("created_at", -1).skip(skip).limit(limit).to_list(limit)
    return {"items": items, "total": total}


@router.post("/templates/backfill")
async def backfill_sms_templates(user=Depends(get_current_user)):
    """Backfill / refresh default SMS templates for the current org.

    Admin-only — template wording affects every future automated SMS sent to
    customers, so changes flow through the same `settings.edit` gate as the
    other SMS configuration endpoints.

    Idempotent self-heal endpoint. Combines three actions:
      • Inserts any missing default templates (e.g. the close-day reminder set
        for orgs that existed before those templates were added).
      • Auto-upgrades unedited templates whose factory wording was changed in
        a later release (tracked via `default_body` snapshot).
      • Leaves user-customized templates fully intact.

    Returns counts of inserted vs upgraded.
    """
    check_perm(user, "settings", "edit")

    # Snapshot before to compute deltas
    before_keys = set()
    before_bodies = {}
    async for doc in db.sms_templates.find({}, {"_id": 0, "key": 1, "body": 1}):
        before_keys.add(doc["key"])
        before_bodies[doc["key"]] = doc.get("body", "")

    await _ensure_templates()

    after_keys = set()
    after_bodies = {}
    async for doc in db.sms_templates.find({}, {"_id": 0, "key": 1, "body": 1}):
        after_keys.add(doc["key"])
        after_bodies[doc["key"]] = doc.get("body", "")

    seeded_keys = sorted(after_keys - before_keys)
    upgraded_keys = sorted(
        k for k in (before_keys & after_keys)
        if before_bodies.get(k) != after_bodies.get(k)
    )

    if not seeded_keys and not upgraded_keys:
        return {
            "seeded": 0, "upgraded": 0,
            "existing": len(after_keys),
            "message": "All default templates are present and up to date."
        }
    parts = []
    if seeded_keys:
        parts.append(f"Seeded {len(seeded_keys)} new template(s)")
    if upgraded_keys:
        parts.append(f"refreshed {len(upgraded_keys)} unedited template(s)")
    return {
        "seeded": len(seeded_keys),
        "upgraded": len(upgraded_keys),
        "existing": len(after_keys),
        "seeded_keys": seeded_keys,
        "upgraded_keys": upgraded_keys,
        "message": ". ".join(parts) + ".",
    }



@router.get("/diagnose-trigger/{template_key}")
async def diagnose_sms_trigger(
    template_key: str,
    branch_id: Optional[str] = None,
    user=Depends(get_current_user),
):
    """Diagnose why an SMS trigger may have been skipped.

    Returns the live state of: template existence + active flag, sms_settings
    enabled flag for the trigger, and per-trigger setting hits. Helpful for
    customers who say "my credit_new SMS didn't fire" — surfaces the bail-out
    reason in plain text.
    """
    org_id = user.get("organization_id") or ""
    diag = {
        "template_key": template_key,
        "organization_id": org_id,
        "branch_id": branch_id or None,
        "checks": [],
    }

    # Template
    tpl = None
    if org_id:
        tpl = await _raw_db.sms_templates.find_one(
            {"key": template_key, "organization_id": org_id}, {"_id": 0}
        )
    if not tpl:
        tpl = await _raw_db.sms_templates.find_one(
            {"key": template_key, "organization_id": {"$exists": False}}, {"_id": 0}
        )
    if not tpl:
        diag["checks"].append({"step": "template", "ok": False, "detail": "No template found for this key (org-scoped or global)"})
        diag["would_send"] = False
        return diag
    if not tpl.get("active", True):
        diag["checks"].append({"step": "template", "ok": False, "detail": f"Template '{template_key}' exists but is INACTIVE"})
        diag["would_send"] = False
        return diag
    diag["checks"].append({"step": "template", "ok": True, "detail": "Template found and active"})

    # Per-trigger setting
    base_q = {"trigger_key": template_key, "$or": [{"branch_id": branch_id}, {"branch_id": None}, {"branch_id": ""}]}
    setting = None
    if org_id:
        setting = await _raw_db.sms_settings.find_one({**base_q, "organization_id": org_id}, {"_id": 0})
    if setting and not setting.get("enabled", True):
        diag["checks"].append({"step": "trigger_setting", "ok": False, "detail": f"Trigger '{template_key}' is DISABLED in Settings → Messages"})
        diag["would_send"] = False
        return diag
    diag["checks"].append({"step": "trigger_setting", "ok": True, "detail": "Trigger is enabled (or not configured → defaults to enabled)"})
    diag["would_send"] = True
    return diag


# Hard cap on per-message retries — prevents infinite carrier spiral when
# the SIM/network is rejecting outbound SMS (Android error code 124 etc).
# After this many failures, the message is moved to status="failed_permanent"
# and the gateway will stop trying. Admin can manually re-queue if needed.
# (MAX_GATEWAY_RETRIES is declared once at the top of this module; the
# duplicate constant here is intentionally removed to prevent drift.)


@router.get("/queue/pending")
async def get_pending_sms(limit: int = 50, user=Depends(get_current_user)):
    """Get pending SMS for the gateway app to send.

    ONE-SHOT policy (Iter 227):
      • LEASE — every row handed out is locked for DISPATCH_LEASE_SECONDS so
        two concurrent polls never ship the same row.
      • SINGLE DISPATCH — each row is handed out EXACTLY ONCE. After that it
        either becomes `sent` (ACK received), `failed` (gateway reported
        failure), or `deferred` (no ACK within lease → waits for admin /retry).
      • NO AUTO-REVIVE — deferred rows do NOT auto-resurrect the next day.
        They sit visibly in the queue so the admin can decide whether the
        SMS actually went out before flipping it back to pending. This is
        the explicit guard against re-sending a message whose ACK failed
        but whose GSM send succeeded — which was the root cause of the
        carrier-flagging spam incident.
      • MUTED-BRANCH FILTER — close-reminder + zreport-finalized rows for
        branches flipped to muted between queuing and dispatch are
        auto-cancelled.
      • QUIET-HOURS GATE (Iter 237) — no auto/scheduled rows are handed out
        inside the org's local 22:00–07:00 window. Manual sends bypass.
      • GLOBAL PAUSE (Iter 237) — if an admin clicked "Pause ALL SMS",
        every non-manual row is held until the pause expires.
      • STALE-ROW EXPIRY (Iter 237) — close-reminder rows older than
        CLOSE_REMINDER_TTL_HOURS are expired before dispatch so a 3-day-old
        "branch didn't close" reminder can never leak out after the day
        has moved on.
    """
    now = now_iso()
    org_id = user.get("organization_id") or ""

    # ── GATEWAY HEARTBEAT (Iter 237) ────────────────────────────────────────
    # Record the timestamp of every successful poll so the UI can surface
    # "Gateway last polled X min ago" — a stuck/disconnected gateway used
    # to be invisible until 5 days of replayed SMS hit the owner.
    try:
        await _raw_db.settings.update_one(
            {"key": "sms_gateway_heartbeat", "organization_id": org_id},
            {"$set": {
                "key": "sms_gateway_heartbeat",
                "organization_id": org_id,
                "value": {
                    "last_poll_at": now,
                    "last_poll_user_id": user.get("id") or user.get("username") or "",
                    "last_returned_count": 0,  # set after items computed
                },
                "updated_at": now,
            }},
            upsert=True,
        )
    except Exception:
        pass

    # ── STALE-ROW EXPIRY (Iter 237) ─────────────────────────────────────────
    # Expire any close-reminder row whose created_at is older than the TTL.
    # This prevents the 1 AM / 2 AM "leftover from 3 days ago" spam that
    # happens when the gateway reconnects after a long outage.
    ttl_cutoff = (
        datetime.now(timezone.utc) - timedelta(hours=CLOSE_REMINDER_TTL_HOURS)
    ).isoformat()
    try:
        await _raw_db.sms_queue.update_many(
            {
                "organization_id": org_id,
                "template_key": {"$in": CLOSE_REMINDER_TEMPLATE_KEYS},
                "status": {"$in": ["pending", "deferred", "failed"]},
                "created_at": {"$lt": ttl_cutoff},
            },
            {"$set": {
                "status": "expired",
                "expired_at": now,
                "expired_reason": "close_reminder_ttl_exceeded",
                "leased_until": None,
            }},
        )
    except Exception:
        pass

    # ── GLOBAL PAUSE GATE (Iter 237) ────────────────────────────────────────
    pause = await _get_org_pause_state(org_id)
    if pause["paused"]:
        # Only return manual sends while paused so urgent admin-composed
        # SMS still flow. Everything else waits for resume.
        return []

    # ── QUIET-HOURS GATE (Iter 237) ─────────────────────────────────────────
    # Inside the org's local 22:00–07:00 window we refuse to hand out any
    # non-manual rows. The gateway will poll again in a few minutes and
    # receive everything once the quiet window ends.
    quiet = await _is_org_in_quiet_hours(org_id)

    # NOTE: Auto-revive of expired deferrals was intentionally REMOVED in
    # Iter 227. Rows become deferred for a reason (no ACK after one dispatch),
    # and resurrecting them risks re-sending a message that actually got
    # delivered via GSM. Admin must explicitly POST /sms/queue/{id}/retry
    # after confirming non-delivery.

    # 2. Pull candidates whose lease (if any) has expired.
    cand_query = {
        "status": "pending",
        "retry_count": {"$lt": MAX_GATEWAY_RETRIES},
        "$or": [
            {"leased_until": {"$exists": False}},
            {"leased_until": None},
            {"leased_until": {"$lte": now}},
        ],
    }
    if quiet:
        # Only manual sends escape the quiet-hour gate.
        cand_query["trigger"] = "manual"

    candidates = await db.sms_queue.find(
        cand_query,
        {"_id": 0},
    ).sort("created_at", 1).limit(limit).to_list(limit)

    # 2a. BELT-AND-SUSPENDERS (Iter 226/237) — any candidate whose branch was
    # muted AFTER the row was queued must NOT be handed out. Applies to BOTH
    # close-reminder stages AND zreport_finalized (Iter 237) — previously
    # zreport_finalized survived a mute and kept arriving overnight.
    muted_branch_ids: set[str] = set()
    candidate_branch_ids = [c.get("branch_id") for c in candidates if c.get("branch_id")]
    if candidate_branch_ids:
        muted_cursor = db.branches.find(
            {"id": {"$in": candidate_branch_ids}, "close_reminder_disabled": True},
            {"_id": 0, "id": 1},
        )
        async for br in muted_cursor:
            muted_branch_ids.add(br["id"])
    if muted_branch_ids:
        filtered: list[dict] = []
        to_cancel: list[str] = []
        for c in candidates:
            if (c.get("branch_id") in muted_branch_ids
                    and c.get("template_key") in MUTED_BRANCH_TEMPLATE_KEYS):
                to_cancel.append(c["id"])
                continue
            filtered.append(c)
        if to_cancel:
            await db.sms_queue.update_many(
                {"id": {"$in": to_cancel}},
                {"$set": {
                    "status": "cancelled",
                    "cancelled_at": now,
                    "cancelled_reason": "branch_muted_at_dispatch",
                    "leased_until": None,
                }},
            )
        candidates = filtered

    # 3. Atomically claim each (prevents two pollers racing on the same row).
    lease_until = (datetime.now(timezone.utc) + timedelta(seconds=DISPATCH_LEASE_SECONDS)).isoformat()
    items = []
    for c in candidates:
        new_dispatch_count = (c.get("dispatch_count") or 0) + 1

        # ONE-SHOT cap — close-reminder and everything else both top out at
        # MAX_DISPATCHES_PER_DAY=1. The per-template variable stays in place
        # so a future trigger type (e.g. payment reminders) can have a
        # different cap without re-plumbing this whole block.
        per_day_cap = (
            MAX_DISPATCHES_CLOSE_REMINDER
            if c.get("template_key") in CLOSE_REMINDER_TEMPLATE_KEYS
            else MAX_DISPATCHES_PER_DAY
        )

        # Over the one-shot cap → defer (stays deferred until admin /retry).
        if new_dispatch_count > per_day_cap:
            deferred_until = await _tomorrow_midnight_iso(c.get("organization_id", ""))
            await db.sms_queue.update_one(
                {"id": c["id"], "status": "pending"},
                {"$set": {
                    "status": "deferred",
                    "deferred_until": deferred_until,
                    "leased_until": None,
                    "deferred_reason": "daily_dispatch_cap",
                }},
            )
            continue

        # Atomic claim — only succeeds if the row is still pending + unleased.
        claimed = await db.sms_queue.find_one_and_update(
            {
                "id": c["id"],
                "status": "pending",
                "$or": [
                    {"leased_until": {"$exists": False}},
                    {"leased_until": None},
                    {"leased_until": {"$lte": now}},
                ],
            },
            {"$set": {
                "leased_until": lease_until,
                "last_dispatched_at": now,
                "dispatch_count": new_dispatch_count,
            }},
            projection={"_id": 0},
            return_document=True,
        )
        if claimed:
            items.append(claimed)

    # Update the heartbeat row with the actual returned count so admins
    # can see at a glance whether polls are returning real work.
    try:
        await _raw_db.settings.update_one(
            {"key": "sms_gateway_heartbeat", "organization_id": org_id},
            {"$set": {"value.last_returned_count": len(items),
                      "value.last_poll_at": now}},
        )
    except Exception:
        pass

    return items


@router.patch("/queue/{sms_id}/mark-sent")
async def mark_sms_sent(sms_id: str, user=Depends(get_current_user)):
    """Gateway app reports SMS was sent successfully.

    Idempotent: if the doc is already 'sent', return ok without bumping anything.
    Critical when the gateway times out on this PATCH and retries — we must NOT
    treat the same successful send as a new event.
    """
    # Already-sent → no-op
    existing = await db.sms_queue.find_one({"id": sms_id}, {"_id": 0, "status": 1})
    if existing and existing.get("status") == "sent":
        return {"status": "sent", "idempotent": True}
    result = await db.sms_queue.update_one(
        {"id": sms_id, "status": {"$in": ["pending", "failed"]}},
        {"$set": {
            "status": "sent", "sent_at": now_iso(), "error": None,
            # Release any active lease so future re-queues (via /retry) aren't
            # blocked by a stale claim.
            "leased_until": None,
        }},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="SMS not found or already in terminal state")
    return {"status": "sent"}


@router.patch("/queue/{sms_id}/mark-failed")
async def mark_sms_failed(sms_id: str, data: dict = None, user=Depends(get_current_user)):
    """Gateway app reports SMS send failure.

    After MAX_GATEWAY_RETRIES attempts the message becomes 'failed_permanent'
    and is no longer fed back to the gateway — this stops the infinite-loop
    spiral that was hammering the SIM card with duplicate retries when the
    carrier was rejecting messages (Android error code 124 etc).
    """
    error = (data or {}).get("error", "Unknown error")
    existing = await db.sms_queue.find_one(
        {"id": sms_id}, {"_id": 0, "status": 1, "retry_count": 1}
    )
    if not existing:
        raise HTTPException(status_code=404, detail="SMS not found")
    # Already permanently failed — idempotent no-op (don't bump retry_count again)
    if existing.get("status") == "failed_permanent":
        return {"status": "failed_permanent", "idempotent": True}
    new_retry = (existing.get("retry_count") or 0) + 1
    final_status = "failed_permanent" if new_retry >= MAX_GATEWAY_RETRIES else "failed"
    await db.sms_queue.update_one(
        {"id": sms_id},
        {"$set": {
            "status": final_status,
            "failed_at": now_iso(),
            "error": error,
            "retry_count": new_retry,
            # Release lease: a 'failed' row is eligible to be re-dispatched
            # after the lease expires / after admin hits /retry.
            "leased_until": None,
        }},
    )
    return {
        "status": final_status,
        "retry_count": new_retry,
        "max_retries": MAX_GATEWAY_RETRIES,
    }


@router.post("/queue/{sms_id}/retry")
async def retry_sms(sms_id: str, user=Depends(get_current_user)):
    """Re-queue a failed SMS. Resets retry_count so it gets a fresh start.
    Admin-only — retrying touches the carrier-billed outbound queue.
    """
    check_perm(user, "settings", "edit")
    result = await db.sms_queue.update_one(
        {"id": sms_id, "status": {"$in": ["failed", "failed_permanent"]}},
        {"$set": {
            "status": "pending",
            "error": None,
            "retry_count": 0,
            "failed_at": None,
        }},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="SMS not found or not in failed state")
    return {"status": "pending"}


@router.post("/queue/cancel-pending-close-reminders")
async def cancel_pending_close_reminders(user=Depends(get_current_user)):
    """Admin: emergency "Stop All Pending Close-Day SMS" button.

    Cancels EVERY pending / deferred / failed close-reminder row across ALL
    branches for this org — without touching customer-facing SMS, expense
    reminders, or any other template. Used when the owner is being spammed
    by a gateway-replay storm and wants to pull the plug immediately.

    Scoped to the caller's organization only (multi-tenant safe).
    """
    check_perm(user, "settings", "edit")
    org_id = user.get("organization_id") or ""
    result = await _raw_db.sms_queue.update_many(
        {
            "organization_id": org_id,
            "template_key": {"$in": CLOSE_REMINDER_TEMPLATE_KEYS},
            "status": {"$in": ["pending", "deferred", "failed"]},
        },
        {"$set": {
            "status": "cancelled",
            "cancelled_at": now_iso(),
            "cancelled_reason": "admin_emergency_stop",
            "leased_until": None,
        }},
    )
    return {"cancelled": result.modified_count or 0}


@router.post("/queue/clear-stuck")
async def clear_stuck_queue(data: dict = None, user=Depends(get_current_user)):
    """Admin: mark queued SMS rows as 'skipped' in one batch.

    Default: skip `failed` + `failed_permanent` (existing behaviour).
    Pass `{"include_pending": true}` to ALSO skip every `pending` + `deferred`
    row — this is the emergency "Stop All Pending" button used during a
    gateway DNS / power outage where the phone is replaying the same SMS.

    The skipped items remain in queue history (auditable) but are no longer
    pollable by the gateway.

    (Iter 237) Scoped to the caller's organization via explicit filter +
    _raw_db — the prior version used the scoped `db` proxy which could
    silently match zero rows if the request context lost its org scope.
    """
    check_perm(user, "settings", "edit")
    data = data or {}
    org_id = user.get("organization_id") or ""
    statuses = ["failed", "failed_permanent"]
    if data.get("include_pending"):
        statuses.extend(["pending", "deferred"])
    result = await _raw_db.sms_queue.update_many(
        {"organization_id": org_id, "status": {"$in": statuses}},
        {"$set": {
            "status": "skipped",
            "skipped_at": now_iso(),
            "skip_reason": "admin_stop_all" if data.get("include_pending") else "admin_clear_stuck",
            "leased_until": None,
        }},
    )
    return {"cleared": result.modified_count, "statuses_cleared": statuses}


@router.post("/queue/stop-all-auto")
async def stop_all_auto_sms(user=Depends(get_current_user)):
    """Admin: TRUE kill-switch — cancels EVERY automated / scheduled SMS in
    flight for this org (close-reminder, zreport-finalized, credit hooks,
    payment confirmations, interest/penalty notices, due-date reminders,
    crop-credit alerts, etc.).

    Manual admin composes (trigger="manual") are untouched so urgent
    human-composed messages still flow.

    (Iter 237) Complements the narrower `/queue/cancel-pending-close-reminders`
    which only targets close-day templates. Use this during a gateway storm
    when the owner wants everything automated to stop immediately.
    """
    check_perm(user, "settings", "edit")
    org_id = user.get("organization_id") or ""
    result = await _raw_db.sms_queue.update_many(
        {
            "organization_id": org_id,
            "trigger": {"$in": ["auto", "scheduled"]},
            "status": {"$in": ["pending", "deferred", "failed"]},
        },
        {"$set": {
            "status": "cancelled",
            "cancelled_at": now_iso(),
            "cancelled_reason": "admin_stop_all_auto",
            "leased_until": None,
        }},
    )
    return {"cancelled": result.modified_count or 0}


@router.post("/queue/pause-all")
async def pause_all_sms(data: dict = None, user=Depends(get_current_user)):
    """Admin: pause ALL automated SMS dispatch for the next N hours (default 24).

    Sets a pause flag in settings that `GET /sms/queue/pending` honours —
    non-manual rows are held back until `until` is reached. Manual admin
    composes still flow. Queue rows are NOT cancelled, just not handed to
    the gateway until resume. Calling again while paused extends the pause.

    Body: { "hours": 24, "reason": "gateway storm" }
    """
    check_perm(user, "settings", "edit")
    data = data or {}
    org_id = user.get("organization_id") or ""
    try:
        hours = float(data.get("hours", 24))
    except (TypeError, ValueError):
        hours = 24.0
    hours = max(0.25, min(hours, 168.0))  # clamp 15min..7d
    until = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()
    reason = str(data.get("reason") or "").strip()[:200]
    await _raw_db.settings.update_one(
        {"key": "sms_global_pause", "organization_id": org_id},
        {"$set": {
            "key": "sms_global_pause",
            "organization_id": org_id,
            "value": {
                "until": until,
                "reason": reason,
                "paused_by": user.get("id") or user.get("username") or "",
                "paused_at": now_iso(),
            },
            "updated_at": now_iso(),
        }},
        upsert=True,
    )
    return {"paused": True, "until": until, "hours": hours, "reason": reason}


@router.post("/queue/resume-all")
async def resume_all_sms(user=Depends(get_current_user)):
    """Admin: cancel the active org-wide SMS pause immediately."""
    check_perm(user, "settings", "edit")
    org_id = user.get("organization_id") or ""
    await _raw_db.settings.update_one(
        {"key": "sms_global_pause", "organization_id": org_id},
        {"$set": {"value": {"until": None, "reason": None,
                            "resumed_by": user.get("id") or "",
                            "resumed_at": now_iso()},
                  "updated_at": now_iso()}},
        upsert=True,
    )
    return {"paused": False}


@router.get("/queue/pause-status")
async def get_pause_status(user=Depends(get_current_user)):
    """Return the current org-wide pause state for the UI badge."""
    org_id = user.get("organization_id") or ""
    state = await _get_org_pause_state(org_id)
    return state


@router.get("/queue/audit-report")
async def queue_audit_report(user=Depends(get_current_user)):
    """Forensics for the SMS queue — helps operators diagnose "why did I
    get an old/unexpected SMS?" without grepping logs.

    Reports:
      • oldest pending row (age in hours) — how stale is the backlog
      • pending / deferred / cancelled counts by template_key bucket
      • recent mute-leakage events (rows cancelled by branch_muted_at_dispatch)
      • close-reminder rows expired by TTL in the last 24h
      • currently-muted branch count + total auto-pending size
      • global pause state
    """
    check_perm(user, "settings", "edit")
    org_id = user.get("organization_id") or ""
    day_cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()

    # Oldest pending row
    oldest = await _raw_db.sms_queue.find_one(
        {"organization_id": org_id, "status": "pending"},
        {"_id": 0, "id": 1, "template_key": 1, "branch_name": 1,
         "created_at": 1, "phone": 1},
        sort=[("created_at", 1)],
    )
    oldest_age_h = None
    if oldest and oldest.get("created_at"):
        try:
            oldest_dt = datetime.fromisoformat(oldest["created_at"].replace("Z", "+00:00"))
            oldest_age_h = round(
                (datetime.now(timezone.utc) - oldest_dt).total_seconds() / 3600, 1
            )
        except Exception:
            oldest_age_h = None

    # Counts by status
    counts: dict[str, int] = {}
    async for row in _raw_db.sms_queue.aggregate([
        {"$match": {"organization_id": org_id}},
        {"$group": {"_id": "$status", "n": {"$sum": 1}}},
    ]):
        counts[row["_id"] or "unknown"] = row["n"]

    # Close-reminder expired in the last 24h
    expired_24h = await _raw_db.sms_queue.count_documents({
        "organization_id": org_id,
        "status": "expired",
        "expired_reason": "close_reminder_ttl_exceeded",
        "expired_at": {"$gte": day_cutoff},
    })

    # Mute-leakage cancellations in the last 24h
    mute_leak_24h = await _raw_db.sms_queue.count_documents({
        "organization_id": org_id,
        "status": "cancelled",
        "cancelled_reason": "branch_muted_at_dispatch",
        "cancelled_at": {"$gte": day_cutoff},
    })

    # Muted branch count
    muted_branches = await _raw_db.branches.count_documents({
        "organization_id": org_id,
        "close_reminder_disabled": True,
        "active": {"$ne": False},
    })

    # Global pause state + quiet-hours state right now
    pause = await _get_org_pause_state(org_id)
    quiet = await _is_org_in_quiet_hours(org_id)
    heartbeat = await _get_gateway_heartbeat(org_id)

    return {
        "oldest_pending": {
            "age_hours": oldest_age_h,
            "created_at": (oldest or {}).get("created_at"),
            "template_key": (oldest or {}).get("template_key"),
            "branch_name": (oldest or {}).get("branch_name"),
            "phone": (oldest or {}).get("phone"),
        } if oldest else None,
        "status_counts": counts,
        "close_reminder_ttl_expired_24h": expired_24h,
        "muted_branch_leak_cancelled_24h": mute_leak_24h,
        "muted_branches": muted_branches,
        "global_pause": pause,
        "dispatch_quiet_hours_now": quiet,
        "gateway_heartbeat": heartbeat,
        "config": {
            "close_reminder_ttl_hours": CLOSE_REMINDER_TTL_HOURS,
            "dispatch_quiet_window": f"{DISPATCH_QUIET_START_H:02d}:00–{DISPATCH_QUIET_END_H:02d}:00 local",
            "max_sms_per_phone_per_day": MAX_SMS_PER_PHONE_PER_DAY,
            "gateway_stale_seconds": GATEWAY_STALE_SECONDS,
        },
    }


@router.get("/queue/gateway-heartbeat")
async def gateway_heartbeat_status(user=Depends(get_current_user)):
    """Lightweight endpoint for the UI badge. Returns last-poll-at, age in
    seconds, and a `healthy` flag (true if poll was within GATEWAY_STALE_SECONDS).
    Used by the SMS Team card to flag a disconnected gateway in real time.
    """
    org_id = user.get("organization_id") or ""
    return await _get_gateway_heartbeat(org_id)


@router.post("/queue/{sms_id}/skip")
async def skip_sms(sms_id: str, user=Depends(get_current_user)):
    """Skip a pending SMS (admin decided not to send)."""
    result = await db.sms_queue.update_one(
        {"id": sms_id, "status": "pending"},
        {"$set": {"status": "skipped"}}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="SMS not found or not pending")
    return {"status": "skipped"}


# ── Manual Send / Compose ───────────────────────────────────────────────────

@router.post("/send")
async def send_manual_sms(data: dict, user=Depends(get_current_user)):
    """Manually compose and queue an SMS to a customer.
    If customer_id is provided, sends to ALL registered phones for that customer.

    Admin/manager-only: composing carrier-billed SMS to customers is sensitive
    enough to keep out of cashier hands. We check `customers.edit` because that
    perm is granted to admin and manager but NOT cashier/staff/inventory by
    default — exactly the boundary we want for outbound customer messaging.
    """
    check_perm(user, "customers", "edit")
    customer_id   = data.get("customer_id", "")
    customer_name = data.get("customer_name", "")
    message       = data.get("message", "")
    branch_id     = data.get("branch_id", "")
    branch_name   = data.get("branch_name", "")

    if not message:
        raise HTTPException(status_code=400, detail="Message is required")
    # Minimum length guard — prevents truly empty / single-keystroke sends
    # from the cashier UI that burn carrier rate limits. Two characters is
    # the floor: short conversational replies like "ok", "hi", "no", "sige"
    # are LEGITIMATE in chat threads (and the server appends a non-trivial
    # auto-signature anyway, so the gateway never ships a 2-byte SMS).
    if len(message.strip()) < 2:
        raise HTTPException(
            status_code=400,
            detail="Message is too short. Please type at least 2 characters.",
        )

    # Resolve phones — all registered numbers when customer_id given.
    # Important: `customer_id` here may also be a USER id (e.g. when the
    # cashier replies in a conversation thread that was created by an
    # outbound SMS like `zreport_finalized` to an admin/owner — the
    # conversation aggregator stamps the recipient user-id into
    # `customer_id` for grouping). Without the users-fallback the reply
    # would 400 with "No phone numbers to send to" even though the
    # conversation thread clearly shows their phone.
    if customer_id:
        customer_doc = await db.customers.find_one(
            {"id": customer_id}, {"_id": 0, "phones": 1, "phone": 1, "name": 1, "branch_id": 1}
        )
        if customer_doc:
            phones_to_send = customer_doc.get("phones") or (
                [customer_doc["phone"]] if customer_doc.get("phone") else []
            )
            customer_name = customer_name or customer_doc.get("name", "")
            branch_id = branch_id or customer_doc.get("branch_id", "")
        else:
            # Fall back to internal users table (admin/owner/manager/auditor/cashier)
            user_doc = await db.users.find_one(
                {"id": customer_id},
                {"_id": 0, "phone": 1, "phones": 1, "full_name": 1, "username": 1, "branch_id": 1}
            )
            if user_doc:
                phones_to_send = user_doc.get("phones") or (
                    [user_doc["phone"]] if user_doc.get("phone") else []
                )
                customer_name = customer_name or user_doc.get("full_name") \
                    or user_doc.get("username", "")
                branch_id = branch_id or user_doc.get("branch_id", "")
            else:
                # Final fallback — caller-supplied phone (e.g. unknown-number reply)
                phones_to_send = [data.get("phone", "")] if data.get("phone") else []
    else:
        phones_to_send = [data.get("phone", "")] if data.get("phone") else []

    phones_to_send = [p.strip() for p in phones_to_send if p and p.strip()]
    if not phones_to_send:
        raise HTTPException(status_code=400, detail="No phone numbers to send to")

    # Look up branch name if not provided
    if branch_id and not branch_name:
        br = await db.branches.find_one({"id": branch_id}, {"_id": 0, "name": 1})
        branch_name = (br or {}).get("name", "")

    # Auto-append signature server-side — cannot be removed or edited by the sender
    company_name = await _resolve_company_name()
    sig_parts = [p for p in [company_name, branch_name] if p]
    message_with_sig = message + ("\n\n- " + " | ".join(sig_parts) if sig_parts else "")

    sent_by_name    = user.get("full_name") or user.get("email", "")
    organization_id = user.get("organization_id", "")

    queued = []
    for phone in phones_to_send:
        doc = {
            "id": new_id(),
            "organization_id": organization_id,
            "template_key": "custom",
            "customer_id": customer_id,
            "customer_name": customer_name,
            "phone": phone,
            "message": message_with_sig,
            "status": "pending",
            "trigger": "manual",
            "trigger_ref": "",
            "dedup_key": "",
            "branch_id": branch_id,
            "branch_name": branch_name,
            "sent_by_name": sent_by_name,
            "created_at": now_iso(),
            "sent_at": None,
            "failed_at": None,
            "error": None,
            "retry_count": 0,
        }
        await db.sms_queue.insert_one(doc)
        del doc["_id"]
        queued.append(doc)

    return queued[0] if len(queued) == 1 else {"queued": len(queued), "phones": phones_to_send}


# ── Sample SMS for Collection Notification Recipients ──────────────────────
@router.post("/send-sample-recipients")
async def send_sample_collection_recipients(data: dict, user=Depends(get_current_user)):
    """Queue a short, clearly-tagged SAMPLE SMS to every configured Collection
    Notification Recipient (Owner, Admin, Manager/Auditor fallback and each
    branch-specific Manager/Auditor). Body is supplied by the client to allow
    testing unsaved edits — the backend resolves branch names and de-dupes
    phones to avoid double-sends.
    """
    check_perm(user, "settings", "edit")
    recipients = data or {}

    company_name = await _resolve_company_name()
    sender = user.get("full_name") or user.get("email", "")
    organization_id = user.get("organization_id", "")

    # Build (phone, role_label, branch_id, branch_name) entries
    entries = []
    entries.append((recipients.get("owner_phone"),   "Owner",   "", ""))
    entries.append((recipients.get("admin_phone"),   "Admin",   "", ""))
    entries.append((recipients.get("manager_phone"), "Manager (Global Fallback)", "", ""))
    entries.append((recipients.get("auditor_phone"), "Auditor (Global Fallback)", "", ""))

    branch_phones = recipients.get("branch_phones") or {}
    for br_id, phones in branch_phones.items():
        br_doc = await db.branches.find_one({"id": br_id}, {"_id": 0, "name": 1})
        br_name = (br_doc or {}).get("name", "") if br_doc else ""
        entries.append(((phones or {}).get("manager_phone"), "Manager", br_id, br_name))
        entries.append(((phones or {}).get("auditor_phone"), "Auditor", br_id, br_name))

    # Normalize, de-dupe by phone (first role wins)
    seen = set()
    cleaned = []
    for phone, role, br_id, br_name in entries:
        p = (phone or "").strip()
        if not p or p in seen:
            continue
        seen.add(p)
        cleaned.append((p, role, br_id, br_name))

    if not cleaned:
        raise HTTPException(
            status_code=400,
            detail="No recipient phone numbers configured. Add at least one phone before sending a sample."
        )

    queued = []
    for phone, role, br_id, br_name in cleaned:
        scope = f" — {br_name}" if br_name else " — All Branches"
        body = (
            f"[SAMPLE] Hi {role}, ito ay test SMS mula sa {company_name or 'AgriBooks'}{scope}. "
            f"Kumpirmado na tama ang naka-configure na numero upang makatanggap ng "
            f"Collection notifications. Walang aksyon na kailangan. "
            f"Sent by: {sender}."
        )
        doc = {
            "id": new_id(),
            "organization_id": organization_id,
            "template_key": "sample_recipient_test",
            "customer_id": "",
            "customer_name": f"{role}{(' - ' + br_name) if br_name else ''}",
            "phone": phone,
            "message": body,
            "status": "pending",
            "trigger": "manual",
            "trigger_ref": "sample_recipient_test",
            "dedup_key": "",
            "branch_id": br_id,
            "branch_name": br_name,
            "sent_by_name": sender,
            "created_at": now_iso(),
            "sent_at": None,
            "failed_at": None,
            "error": None,
            "retry_count": 0,
        }
        await db.sms_queue.insert_one(doc)
        queued.append({"phone": phone, "role": role, "branch_name": br_name})

    return {"queued": len(queued), "recipients": queued}


@router.post("/send-sample-single")
async def send_sample_single_recipient(data: dict, user=Depends(get_current_user)):
    """Queue a [SAMPLE] SMS to a single recipient — used by the per-row Test
    buttons next to each phone field on the Collection Recipients settings.

    Body:
      { phone: "09xx...", role: "Owner|Admin|Manager|Auditor",
        branch_id?: "...", branch_name?: "..." }

    Admin-only via `settings.edit` (same gate as the other recipient ops).
    """
    check_perm(user, "settings", "edit")
    phone = (data.get("phone") or "").strip()
    role = (data.get("role") or "Recipient").strip() or "Recipient"
    branch_id = data.get("branch_id", "") or ""
    branch_name = (data.get("branch_name") or "").strip()

    if not phone:
        raise HTTPException(status_code=400, detail="Phone number is required")

    company_name = await _resolve_company_name()
    sender = user.get("full_name") or user.get("email", "")
    organization_id = user.get("organization_id", "")

    scope = f" — {branch_name}" if branch_name else " — All Branches"
    body = (
        f"[SAMPLE] Hi {role}, ito ay test SMS mula sa {company_name or 'AgriBooks'}{scope}. "
        f"Kumpirmado na tama ang naka-configure na numero upang makatanggap ng "
        f"Collection notifications. Walang aksyon na kailangan. "
        f"Sent by: {sender}."
    )

    doc = {
        "id": new_id(),
        "organization_id": organization_id,
        "template_key": "sample_recipient_test",
        "customer_id": "",
        "customer_name": f"{role}{(' - ' + branch_name) if branch_name else ''}",
        "phone": phone,
        "message": body,
        "status": "pending",
        "trigger": "manual",
        "trigger_ref": "sample_recipient_test_single",
        "dedup_key": "",
        "branch_id": branch_id,
        "branch_name": branch_name,
        "sent_by_name": sender,
        "created_at": now_iso(),
        "sent_at": None,
        "failed_at": None,
        "error": None,
        "retry_count": 0,
    }
    await db.sms_queue.insert_one(doc)
    return {"queued": 1, "phone": phone, "role": role, "branch_name": branch_name}


@router.post("/blast")
async def send_promo_blast(data: dict, user=Depends(get_current_user)):
    """Send a promotional message to multiple customers.
    Body: { message, customer_ids?: [], filter?: { min_balance, branch_id }, branch_id, branch_name }
    """
    check_perm(user, "settings", "edit")
    message_text = data.get("message", "")
    if not message_text:
        raise HTTPException(status_code=400, detail="Message is required")

    branch_id = data.get("branch_id", "")
    branch_name = data.get("branch_name", "")
    customer_ids = data.get("customer_ids")
    filter_opts = data.get("filter", {})

    # Build customer query
    query = {"active": True}
    if customer_ids:
        query["id"] = {"$in": customer_ids}
    else:
        if filter_opts.get("branch_id"):
            query["branch_id"] = filter_opts["branch_id"]
        if filter_opts.get("min_balance"):
            query["balance"] = {"$gte": float(filter_opts["min_balance"])}

    customers = await db.customers.find(query, {"_id": 0, "id": 1, "name": 1, "phone": 1}).to_list(5000)

    # Get company name (settings → organizations.name fallback, no cross-org bleed)
    company_name = await _resolve_company_name()

    queued = 0
    for c in customers:
        phone = c.get("phone", "")
        if not phone:
            continue
        rendered = message_text.replace("<customer_name>", c.get("name", "Customer"))
        doc = {
            "id": new_id(),
            "template_key": "promo_blast",
            "customer_id": c["id"],
            "customer_name": c.get("name", ""),
            "phone": phone.strip(),
            "message": f"{rendered} - {company_name} {branch_name}".strip(),
            "status": "pending",
            "trigger": "manual",
            "trigger_ref": "blast",
            "dedup_key": "",
            "branch_id": branch_id,
            "branch_name": branch_name,
            "created_at": now_iso(),
            "sent_at": None,
            "failed_at": None,
            "error": None,
            "retry_count": 0,
        }
        await db.sms_queue.insert_one(doc)
        queued += 1

    return {"queued": queued, "total_customers": len(customers), "skipped_no_phone": len(customers) - queued}



# ── Credit Reminder Blast ─────────────────────────────────────────────────────

@router.post("/credit-blast")
async def credit_reminder_blast(data: dict, user=Depends(get_current_user)):
    """Smart credit reminder blast.
    Automatically selects message template per customer:
      Option A (short)    — has balance, no overdue, due > 15 days away
      Option B (detailed) — has overdue OR due within 15 days

    Pass dry_run=true (default) for a preview without queueing.
    Pass dry_run=false to actually queue.
    """
    check_perm(user, "settings", "edit")
    dry_run   = data.get("dry_run", True)
    min_bal   = float(data.get("min_balance", 0))
    branch_id = data.get("branch_id", "")

    today     = date.today()
    today_str = today.isoformat()

    # 1. Customers with outstanding balance
    cust_query: dict = {"active": True, "balance": {"$gt": min_bal}}
    if branch_id:
        cust_query["branch_id"] = branch_id
    customers = await db.customers.find(cust_query, {"_id": 0}).to_list(5000)
    if not customers:
        return {"dry_run": dry_run, "total_customers": 0, "total_sms": 0,
                "short_count": 0, "detailed_count": 0, "preview": [], "queued": 0}

    # 2. Open invoices for all these customers in one query
    cids      = [c["id"] for c in customers]
    inv_query: dict = {
        "customer_id": {"$in": cids},
        "status": {"$nin": ["paid", "voided"]},
        "balance": {"$gt": 0},
    }
    if branch_id:
        inv_query["branch_id"] = branch_id
    invoices = await db.invoices.find(
        inv_query, {"_id": 0, "customer_id": 1, "balance": 1, "due_date": 1}
    ).to_list(100000)
    inv_map: dict = {}
    for inv in invoices:
        inv_map.setdefault(inv["customer_id"], []).append(inv)

    # 3. Branch names in one query
    all_bids = list({c.get("branch_id", "") for c in customers if c.get("branch_id")})
    branch_docs = await db.branches.find(
        {"id": {"$in": all_bids}}, {"_id": 0, "id": 1, "name": 1}
    ).to_list(100)
    branch_map = {b["id"]: b["name"] for b in branch_docs}

    # 4. Company name (settings → organizations.name fallback, no cross-org bleed)
    company_name = await _resolve_company_name()

    sent_by_name    = user.get("full_name") or user.get("email", "")
    organization_id = user.get("organization_id", "")

    short_count    = 0
    detailed_count = 0
    preview        = []
    total_sms      = 0
    queued         = 0

    for customer in customers:
        cid           = customer["id"]
        cust_invs     = inv_map.get(cid, [])
        cust_branch   = branch_map.get(customer.get("branch_id", ""), "")
        total_balance = customer.get("balance", 0)
        interest_rate = customer.get("interest_rate", 0)

        # Overdue vs future invoices
        overdue_invs  = [i for i in cust_invs if i.get("due_date") and i["due_date"] < today_str]
        future_invs   = [i for i in cust_invs if i.get("due_date") and i["due_date"] >= today_str]

        overdue_amount = sum(i["balance"] for i in overdue_invs)
        days_overdue   = 0
        if overdue_invs:
            oldest       = min(i["due_date"] for i in overdue_invs)
            days_overdue = (today - date.fromisoformat(oldest)).days

        next_due_date   = None
        next_due_amount = 0
        days_until_due  = None
        if future_invs:
            next_due_date   = min(i["due_date"] for i in future_invs)
            next_due_amount = sum(i["balance"] for i in future_invs if i["due_date"] == next_due_date)
            days_until_due  = (date.fromisoformat(next_due_date) - today).days

        est_interest = total_balance * interest_rate / 100 if interest_rate else 0

        # Smart template selection
        use_b = overdue_amount > 0 or (days_until_due is not None and days_until_due <= 15)
        label = "detailed" if use_b else "short"

        company_branch = f"{company_name} - {cust_branch}".strip(" -")

        if use_b:
            # Option B — Detailed
            lines = [f"Hi {customer['name']}, balanse summary mo sa {company_branch}:"]
            lines.append(f"\nKabuuang balanse: P{total_balance:,.2f}")
            if overdue_amount > 0:
                lines.append(f"OVERDUE: P{overdue_amount:,.2f} ({days_overdue} araw na!)")
            if next_due_date:
                lines.append(f"Susunod na due: P{next_due_amount:,.2f} sa {next_due_date} ({days_until_due} araw na lang)")
            if interest_rate > 0:
                lines.append(f"Est. interest: ~P{est_interest:,.2f}/buwan ({interest_rate}%/mo)")
            lines.append("\nPaki-bisita o bayaran na po agad. Salamat!")
            message = "\n".join(lines)
        else:
            # Option A — Short
            due_line = ""
            if next_due_date and days_until_due is not None:
                due_line = f"\n\nPinakamalapit na due: P{next_due_amount:,.2f} sa {next_due_date} ({days_until_due} araw na lang po)"
            int_line  = (f"\nPara maiwasan ang {interest_rate}%/mo na interest, paki-settle na bago mag-due."
                         if interest_rate > 0 else "")
            message = (
                f"Hi {customer['name']}! Paalala po mula sa {company_branch}.\n\n"
                f"Kasalukuyang balanse: P{total_balance:,.2f}"
                f"{due_line}"
                f"{int_line}\n\nSalamat!"
            )
        phones = customer.get("phones") or ([customer["phone"]] if customer.get("phone") else [])
        phones = [p for p in phones if p]
        if not phones:
            continue

        # Count only customers that will actually receive SMS
        if use_b:
            detailed_count += 1
        else:
            short_count += 1

        total_sms += len(phones)

        # Collect up to 2 preview samples (1 short + 1 detailed if available)
        if len(preview) < 2 and not any(p["template"] == label for p in preview):
            preview.append({
                "customer_name": customer["name"],
                "phones": phones,
                "template": label,
                "message": message,
                "total_balance": total_balance,
                "overdue_amount": overdue_amount,
                "days_until_due": days_until_due,
            })

        if not dry_run:
            for phone in phones:
                doc = {
                    "id": new_id(),
                    "organization_id": organization_id,
                    "template_key": "credit_reminder_blast",
                    "customer_id": cid,
                    "customer_name": customer["name"],
                    "phone": phone,
                    "message": message,
                    "status": "pending",
                    "trigger": "manual",
                    "trigger_ref": "credit_blast",
                    "dedup_key": "",
                    "branch_id": customer.get("branch_id", ""),
                    "branch_name": cust_branch,
                    "sent_by_name": sent_by_name,
                    "created_at": now_iso(),
                    "sent_at": None,
                    "failed_at": None,
                    "error": None,
                    "retry_count": 0,
                }
                await db.sms_queue.insert_one(doc)
                del doc["_id"]
                queued += 1

    return {
        "dry_run": dry_run,
        "total_customers": short_count + detailed_count,
        "total_sms": total_sms if dry_run else queued,
        "short_count": short_count,
        "detailed_count": detailed_count,
        "preview": preview,
        "queued": queued,
    }



# ── Close-Reminder Diagnostics ──────────────────────────────────────────────

@router.get("/close-reminder/diagnose")
async def close_reminder_diagnose(user=Depends(get_current_user)):
    """Admin-only snapshot of what the close-reminder scheduler sees right
    now for the current org: timezone, local time, quiet-hours flag, per-
    branch next-stage fire time, and resolved recipient phones by role.

    Answers "why didn't my 3 PM ping fire?" without grep-ing server logs.
    """
    check_perm(user, "settings", "edit")
    from routes.close_reminder import diagnose_for_org
    org_id = user.get("organization_id") or ""
    return await diagnose_for_org(org_id)


# ── Close-Reminder Stage Settings (Team SMS UI) ─────────────────────────────
# Each org can customise each scheduled-reminder stage independently:
#   • enabled/disabled toggle
#   • which roles receive it (subset of cashier/manager/owner/admin/auditor)
# We expose the curated STAGE_META below so the UI can render a human-
# readable label + timing description next to each toggle without having
# to duplicate that metadata on the client.

_STAGE_META = {
    "close_catchup_3pm":      {"label": "Catch-up ping",         "timing": "3 hours before close (e.g. 3 PM if close is 6 PM)"},
    "close_precheck":         {"label": "Pre-close check-in",    "timing": "At closing time"},
    "close_late_notice":      {"label": "Late notice",            "timing": "1.5 h after close"},
    "close_status_snapshot":  {"label": "Status snapshot",        "timing": "2.5 h after close"},
    "close_escalation":       {"label": "Escalation alert",       "timing": "3.5 h after close"},
    "close_overdue_next_day": {"label": "Day +1 overdue (AM)",    "timing": "7 AM the next day"},
    "close_overdue_multi_day": {"label": "Multi-day overdue",     "timing": "Noon of Day +1 onward"},
}

_VALID_ROLES = {"cashier", "manager", "owner", "admin", "auditor"}


@router.get("/close-reminder/stages")
async def list_close_reminder_stages(user=Depends(get_current_user)):
    """Return the current per-stage settings merged with defaults + the
    metadata the UI needs to render each row (label, timing, order)."""
    check_perm(user, "settings", "edit")
    from routes.close_reminder import _load_stage_settings, STAGES
    org_id = user.get("organization_id") or ""
    merged = await _load_stage_settings(org_id)

    # Preserve the in-code order (top-down: catchup → precheck → late notice…)
    # and fold in display metadata so the UI has everything in one payload.
    seen = set()
    rows = []
    for s in STAGES:
        k = s["key"]
        if k in seen:
            continue
        seen.add(k)
        meta = _STAGE_META.get(k, {})
        cfg = merged.get(k, {"enabled": True, "recipients": list(s["recipients"])})
        rows.append({
            "stage_key": k,
            "label": meta.get("label", k),
            "timing": meta.get("timing", ""),
            "enabled": bool(cfg.get("enabled", True)),
            "recipients": list(cfg.get("recipients") or []),
            "default_recipients": list(s["recipients"]),
        })
    return {"stages": rows, "valid_roles": sorted(_VALID_ROLES)}


@router.put("/close-reminder/stages/{stage_key}")
async def update_close_reminder_stage(stage_key: str, data: dict, user=Depends(get_current_user)):
    """Upsert per-stage settings (enabled + recipients list). Unknown stage
    keys are rejected; unknown role names inside `recipients` are silently
    dropped so a forward-compat UI can't pollute the store."""
    check_perm(user, "settings", "edit")
    from routes.close_reminder import STAGE_DEFAULTS
    if stage_key not in STAGE_DEFAULTS:
        raise HTTPException(status_code=404, detail=f"Unknown stage: {stage_key}")
    org_id = user.get("organization_id") or ""
    enabled = bool(data.get("enabled", True))
    recipients_in = data.get("recipients") or []
    if not isinstance(recipients_in, list):
        raise HTTPException(status_code=400, detail="recipients must be a list")
    recipients = [r for r in recipients_in if r in _VALID_ROLES]
    await db.sms_close_stages.update_one(
        {"organization_id": org_id, "stage_key": stage_key},
        {"$set": {
            "organization_id": org_id,
            "stage_key": stage_key,
            "enabled": enabled,
            "recipients": recipients,
            "updated_at": now_iso(),
        }},
        upsert=True,
    )
    return {"stage_key": stage_key, "enabled": enabled, "recipients": recipients}


# ── Per-branch Close Time ───────────────────────────────────────────────────

@router.put("/close-reminder/branch-close-time/{branch_id}")
async def update_branch_close_time(branch_id: str, data: dict, user=Depends(get_current_user)):
    """Set a branch's closing time (0–24, fractional allowed). This flows
    through to the scheduler's per-branch trigger calculations on the next
    tick — no restart required.
    """
    check_perm(user, "settings", "edit")
    close_time_h = data.get("close_time_h")
    try:
        close_time_h = float(close_time_h)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="close_time_h must be a number")
    if not (0 <= close_time_h <= 24):
        raise HTTPException(status_code=400, detail="close_time_h must be between 0 and 24")
    org_id = user.get("organization_id") or ""
    br = await db.branches.find_one(
        {"id": branch_id, "organization_id": org_id}, {"_id": 0, "id": 1}
    )
    if not br:
        raise HTTPException(status_code=404, detail="Branch not found")
    await db.branches.update_one(
        {"id": branch_id},
        {"$set": {"close_time_h": close_time_h, "updated_at": now_iso()}},
    )
    return {"branch_id": branch_id, "close_time_h": close_time_h}


@router.put("/close-reminder/branch-toggle/{branch_id}")
async def update_branch_close_reminder_toggle(branch_id: str, data: dict, user=Depends(get_current_user)):
    """Enable or disable automated close-day SMS reminders for ONE branch.

    When `disabled=true`, the scheduler skips this branch entirely —
    no "Approaching close", "At close", "Overdue", "Day-after recap"
    stages fire. Useful for warehouse/transfer-only branches where the
    owner handles everything manually and doesn't want SMS noise.

    Body: { disabled: bool }
    """
    check_perm(user, "settings", "edit")
    disabled = bool(data.get("disabled"))
    org_id = user.get("organization_id") or ""
    br = await db.branches.find_one(
        {"id": branch_id, "organization_id": org_id}, {"_id": 0, "id": 1}
    )
    if not br:
        raise HTTPException(status_code=404, detail="Branch not found")
    await db.branches.update_one(
        {"id": branch_id},
        {"$set": {"close_reminder_disabled": disabled, "updated_at": now_iso()}},
    )
    # Purge any close-reminder rows already in-flight for this branch so the
    # mute takes effect immediately. Without this, items queued BEFORE the
    # mute keep being handed out to the gateway and the owner still gets
    # spam — which is exactly the user-visible bug the mute is supposed to
    # solve. Scoped to this org + branch + close-reminder template_keys.
    # (Iter 237) Now also purges zreport_finalized via MUTED_BRANCH_TEMPLATE_KEYS
    # so the "daily summary" SMS can't slip through after a branch is muted.
    purged_count = 0
    if disabled:
        purge_result = await _raw_db.sms_queue.update_many(
            {
                "organization_id": org_id,
                "branch_id": branch_id,
                "template_key": {"$in": MUTED_BRANCH_TEMPLATE_KEYS},
                "status": {"$in": ["pending", "deferred", "failed"]},
            },
            {"$set": {
                "status": "cancelled",
                "cancelled_at": now_iso(),
                "cancelled_reason": "branch_muted",
                "leased_until": None,
            }},
        )
        purged_count = purge_result.modified_count or 0
    return {
        "branch_id": branch_id,
        "close_reminder_disabled": disabled,
        "purged": purged_count,
    }


@router.post("/close-reminder/test-stage/{stage_key}")
async def test_close_reminder_stage(stage_key: str, data: dict = None, user=Depends(get_current_user)):
    """Fire a [SAMPLE] SMS for ONE stage immediately to the stage's currently-
    configured roles, scoped to a specific branch. Lets an admin verify that
    routing works end-to-end without waiting for the scheduled trigger.

    Body:
      { branch_id: "..." }   # required — roles resolve against this branch

    Uses the same role → user phone resolution the live scheduler uses, but
    builds a short [SAMPLE] body so customers/staff can't confuse it with a
    real alert. Bypasses the dedup log so the admin can retest freely.
    """
    check_perm(user, "settings", "edit")
    from routes.close_reminder import STAGE_DEFAULTS, _load_stage_settings, _resolve_recipients
    if stage_key not in STAGE_DEFAULTS:
        raise HTTPException(status_code=404, detail=f"Unknown stage: {stage_key}")

    data = data or {}
    branch_id = (data.get("branch_id") or "").strip()
    if not branch_id:
        raise HTTPException(status_code=400, detail="branch_id is required")

    org_id = user.get("organization_id") or ""
    branch = await db.branches.find_one(
        {"id": branch_id, "organization_id": org_id},
        {"_id": 0, "id": 1, "name": 1},
    )
    if not branch:
        raise HTTPException(status_code=404, detail="Branch not found")

    # Resolve the stage's CURRENT effective settings (org override → defaults).
    merged = await _load_stage_settings(org_id)
    cfg = merged.get(stage_key) or {}
    if not cfg.get("enabled", True):
        raise HTTPException(
            status_code=400,
            detail="This stage is currently disabled. Enable it first, then test.",
        )
    role_keys = list(cfg.get("recipients") or [])
    if not role_keys:
        raise HTTPException(
            status_code=400,
            detail="No recipient roles configured for this stage. Pick at least one role, then test.",
        )

    recipients, debug_by_role = await _resolve_recipients(
        org_id, branch_id, role_keys, include_debug=True
    )
    if not recipients:
        # Build a per-role explanation for the admin — better UX than a
        # generic "no recipients" toast.
        why = []
        for role_key, info in (debug_by_role or {}).items():
            if info.get("matched_users"):
                continue
            if info.get("users_without_phone"):
                why.append(f"{role_key}: has users but none have a phone")
            else:
                why.append(f"{role_key}: no users assigned" if role_key != "auditor"
                           else f"{role_key}: no user has is_auditor=true")
        detail = (
            "No users with phone numbers match the selected roles for this branch, "
            "and no Collection-Recipient fallback phone is configured. "
            + ("Issues: " + "; ".join(why) + ". " if why else "")
            + "Add a phone number to a team member in the Team section, or set a "
              "Collection Recipient phone under Settings → Messages, then retry."
        )
        raise HTTPException(status_code=400, detail=detail)

    company_name = await _resolve_company_name()
    sender = user.get("full_name") or user.get("email", "")
    label = _STAGE_META.get(stage_key, {}).get("label", stage_key)

    queued = []
    for r in recipients:
        body = (
            f"[SAMPLE] {label} test — {company_name or 'AgriBooks'} / {branch.get('name', '')}. "
            f"Hi {r.get('name') or r.get('role') or 'team'}, kung nakatanggap ka ng SMS na ito, "
            f"gumagana nang tama ang reminder routing para sa '{label}' stage. "
            f"Walang aksyon na kailangan. Sent by: {sender}."
        )
        doc = {
            "id": new_id(),
            "organization_id": org_id,
            "template_key": f"sample_stage_test:{stage_key}",
            "customer_id": r.get("id") or "",
            "customer_name": f"{(r.get('role') or 'team').title()} — {branch.get('name', '')}",
            "phone": r["phone"],
            "message": body,
            "status": "pending",
            "trigger": "manual",
            "trigger_ref": f"test_stage:{stage_key}:{branch_id}",
            "dedup_key": "",
            "branch_id": branch_id,
            "branch_name": branch.get("name", ""),
            "sent_by_name": sender,
            "created_at": now_iso(),
            "sent_at": None,
            "failed_at": None,
            "error": None,
            "retry_count": 0,
        }
        await db.sms_queue.insert_one(doc)
        queued.append({
            "phone": r["phone"],
            "role": r.get("role"),
            "name": r.get("name"),
            "fallback": bool(r.get("fallback")),
        })

    return {
        "stage_key": stage_key,
        "stage_label": label,
        "branch_id": branch_id,
        "branch_name": branch.get("name", ""),
        "queued": len(queued),
        "recipients": queued,
        "resolution": debug_by_role,
    }


# ── Stats ───────────────────────────────────────────────────────────────────

@router.get("/stats")
async def sms_stats(branch_id: Optional[str] = None, user=Depends(get_current_user)):
    """Get SMS queue statistics plus branch-specific unread inbox count."""
    pipeline = [
        {"$group": {"_id": "$status", "count": {"$sum": 1}}}
    ]
    results = await db.sms_queue.aggregate(pipeline).to_list(10)
    stats = {r["_id"]: r["count"] for r in results}

    # Unread incoming messages — branch-scoped for the badge
    unread_query: dict = {"read": False, "customer_id": {"$ne": ""}}
    if branch_id:
        unread_query["branch_id"] = branch_id
    unread = await db.sms_inbox.count_documents(unread_query)

    return {
        "pending": stats.get("pending", 0),
        "sent": stats.get("sent", 0),
        "failed": stats.get("failed", 0),
        "skipped": stats.get("skipped", 0),
        "total": sum(stats.values()),
        "unread": unread,
    }


# ══════════════════════════════════════════════════════════════════════════════
# PHONE CHECK — Whitelist filter used by Android gateway before processing SMS
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/check-phone")
async def check_phone(phone: str, user=Depends(get_current_user)):
    """Check if a phone number belongs to a known customer.
    Android app calls this before processing any incoming or outgoing SMS.
    Unknown numbers are silently ignored by the app.
    """
    normalized = phone.lstrip("+")
    if normalized.startswith("63") and len(normalized) > 10:
        normalized = "0" + normalized[2:]
    phones = list({phone, normalized})

    customer = await _raw_db.customers.find_one(
        {"$or": [{"phone": {"$in": phones}}, {"phones": {"$in": phones}}],
         "organization_id": user.get("organization_id")},
        {"_id": 0, "id": 1, "name": 1, "branch_id": 1}
    )
    if not customer:
        customer = await _raw_db.customers.find_one(
            {"$or": [{"phone": {"$in": phones}}, {"phones": {"$in": phones}}]},
            {"_id": 0, "id": 1, "name": 1, "branch_id": 1}
        )

    if customer:
        return {"exists": True, "customer_name": customer.get("name", ""), "customer_id": customer.get("id", "")}
    return {"exists": False, "customer_name": "", "customer_id": ""}


# ══════════════════════════════════════════════════════════════════════════════
# SENT FROM DEVICE — Outgoing SMS typed directly on the gateway phone
# No signature = Admin sent it. Visible to all branches.
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/sent-from-device")
async def sent_from_device(data: dict, user=Depends(get_current_user)):
    """Gateway app posts an SMS it sent directly from the native SMS app.
    These are attributed to Admin (device holder) with no branch scope.
    They appear in ALL branch conversation views for that customer.
    """
    phone = (data.get("phone") or "").strip()
    message = (data.get("message") or "").strip()
    if not phone or not message:
        raise HTTPException(status_code=400, detail="phone and message required")

    # Normalize to local format to match customer records
    normalized = phone.lstrip("+")
    if normalized.startswith("63") and len(normalized) > 10:
        normalized = "0" + normalized[2:]
    stored_phone = normalized
    phones = list({phone, normalized})

    # Look up customer — checks both primary phone and phones[] array
    customer = await _raw_db.customers.find_one(
        {"$or": [{"phone": {"$in": phones}}, {"phones": {"$in": phones}}],
         "organization_id": user.get("organization_id")},
        {"_id": 0, "id": 1, "name": 1, "branch_id": 1}
    )
    if not customer:
        customer = await _raw_db.customers.find_one(
            {"$or": [{"phone": {"$in": phones}}, {"phones": {"$in": phones}}]},
            {"_id": 0, "id": 1, "name": 1, "branch_id": 1}
        )

    # Resolve organization_id — context var (company admin call) → branch lookup → user token
    from config import get_org_context
    org_id = get_org_context()
    if not org_id and customer and customer.get("branch_id"):
        br = await _raw_db.branches.find_one({"id": customer["branch_id"]}, {"_id": 0, "organization_id": 1})
        org_id = (br or {}).get("organization_id", "")
    if not org_id:
        org_id = user.get("organization_id") or ""

    # Build signature: full company name + "| Admin"
    # e.g. "Sibugay Agricultural Supply | Admin"
    biz = await _raw_db.settings.find_one({"key": "company_info", "organization_id": org_id}, {"_id": 0})
    company_name = (biz or {}).get("value", {}).get("name", "")
    if not company_name and org_id:
        # Settings doc missing for this org — fall back to the immutable
        # organizations.name (own tenant only, no bleed). Mirrors helper logic.
        org_doc = await _raw_db.organizations.find_one({"id": org_id}, {"_id": 0, "name": 1})
        company_name = (org_doc or {}).get("name", "")
    device_sig = f"\n\n- {company_name} | Admin" if company_name else "\n\n- Admin"

    # Don't double-sign: skip if message already contains our signature marker
    message_with_sig = message if "\n\n-" in message else message + device_sig

    # Store as already-sent with Admin attribution, no branch scope
    doc = {
        "id": new_id(),
        "organization_id": org_id,
        "template_key": "custom",
        "customer_id": customer["id"] if customer else "",
        "customer_name": customer["name"] if customer else stored_phone,
        "phone": stored_phone,
        "message": message_with_sig,
        "status": "sent",           # Already delivered — skip the queue
        "trigger": "device",
        "trigger_ref": "admin_device",
        "dedup_key": "",
        "branch_id": None,          # No branch — visible to all
        "branch_name": "",
        "sent_by_name": "Admin (via device)",
        "created_at": data.get("sent_at", now_iso()),
        "sent_at": data.get("sent_at", now_iso()),
        "failed_at": None,
        "error": None,
        "retry_count": 0,
    }
    await _raw_db.sms_queue.insert_one(doc)
    del doc["_id"]
    return doc


# ══════════════════════════════════════════════════════════════════════════════
# INBOX — Incoming SMS from gateway phone (replies from customers)
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/inbox")
async def receive_inbox_sms(data: dict, user=Depends(get_current_user)):
    """Gateway app posts ALL incoming SMS here — no filtering on the phone side.
    Backend classifies: registered customer → branch-scoped; unknown → admin-only inbox.
    """
    phone = (data.get("phone") or "").strip()
    message = (data.get("message") or "").strip()
    if not phone or not message:
        raise HTTPException(status_code=400, detail="phone and message required")

    # Always store in local format (09...) to unify +63 and 09 variants
    normalized = phone.lstrip("+")
    if normalized.startswith("63") and len(normalized) > 10:
        normalized = "0" + normalized[2:]
    stored_phone = normalized
    phones = list({phone, normalized})

    # Try to match customer — checks primary phone AND phones[] array
    customer = await _raw_db.customers.find_one(
        {"$or": [{"phone": {"$in": phones}}, {"phones": {"$in": phones}}],
         "organization_id": user.get("organization_id")},
        {"_id": 0, "id": 1, "name": 1, "branch_id": 1}
    )
    if not customer:
        customer = await _raw_db.customers.find_one(
            {"$or": [{"phone": {"$in": phones}}, {"phones": {"$in": phones}}]},
            {"_id": 0, "id": 1, "name": 1, "branch_id": 1}
        )

    registered = customer is not None
    doc = {
        "id": new_id(),
        "phone": stored_phone,
        "message": message,
        "direction": "in",
        "registered": registered,                           # True = known customer
        "customer_id": customer["id"] if customer else "",
        "customer_name": customer["name"] if customer else stored_phone,
        "branch_id": customer.get("branch_id", "") if customer else "",
        "received_at": data.get("received_at", now_iso()),
        "created_at": now_iso(),
        "read": False,
    }
    await db.sms_inbox.insert_one(doc)
    del doc["_id"]
    return doc


@router.get("/conversations")
async def list_conversations(
    branch_id: Optional[str] = None,
    section: str = "customers",   # "customers" | "unknown"
    user=Depends(get_current_user),
):
    """List conversations grouped by phone.
    section=customers  → registered customers, branch-filtered (default)
    section=unknown    → unregistered/unknown numbers, admin-only
    """

    # ── Unknown numbers section (admin only) ────────────────────────────────
    if section == "unknown":
        if user.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Admin access required for unknown numbers inbox")
        pipeline = [
            # Messages where no customer was matched — customer_id is empty
            {"$match": {"customer_id": ""}},
            {"$sort": {"created_at": -1}},
            {"$group": {
                "_id": "$phone",
                "last_message": {"$first": "$message"},
                "last_time": {"$first": "$created_at"},
                "unread": {"$sum": {"$cond": [{"$eq": ["$read", False]}, 1, 0]}},
            }},
        ]
        items = await db.sms_inbox.aggregate(pipeline).to_list(500)
        result = [
            {
                "phone": item["_id"],
                "customer_name": item["_id"],   # Phone number as display name
                "customer_id": "",
                "last_message": item.get("last_message", ""),
                "last_time": item.get("last_time", ""),
                "last_direction": "in",
                "unread": item.get("unread", 0),
                "branch_ids": [],
                "branch_names": [],
                "registered": False,
            }
            for item in items
        ]
        return sorted(result, key=lambda x: x.get("last_time", ""), reverse=True)

    # ── Customers section — grouped by customer_id (multi-phone safe) ────────
    # SECURITY (Iter 216): non-admin users see ONLY their assigned branch's
    # customers. Managers/cashiers can thus follow up with their branch's
    # customers via SMS without being able to read other branches' convos.
    if user.get("role") != "admin":
        user_branch = user.get("branch_id") or ""
        # Force the branch filter to the user's own branch, overriding any
        # client-supplied branch_id (defense-in-depth against URL tampering).
        branch_id = user_branch

    # Branch filter: collect customer_ids that have activity in this branch
    cid_filter: dict = {"customer_id": {"$ne": ""}}
    if branch_id:
        queue_cids = await db.sms_queue.distinct(
            "customer_id",
            {"branch_id": branch_id, "status": {"$in": ["sent", "pending", "failed"]}, "customer_id": {"$ne": ""}},
        )
        inbox_cids = await db.sms_inbox.distinct(
            "customer_id", {"branch_id": branch_id, "customer_id": {"$ne": ""}},
        )
        cids = list(set(queue_cids) | set(inbox_cids))
        if not cids:
            return []
        cid_filter = {"customer_id": {"$in": cids}}

    # Latest outgoing per customer — from ALL branches (collaboration context)
    out_pipeline = [
        {"$match": {"status": {"$in": ["sent", "pending", "failed"]}, "customer_id": {"$ne": ""}, **cid_filter}},
        {"$sort": {"created_at": -1}},
        {"$group": {
            "_id": "$customer_id",
            "last_message": {"$first": "$message"},
            "last_time": {"$first": "$created_at"},
            "customer_name": {"$first": "$customer_name"},
            "customer_id": {"$first": "$customer_id"},
            "phones": {"$addToSet": "$phone"},
            "branch_ids": {"$addToSet": "$branch_id"},
            "branch_names": {"$addToSet": "$branch_name"},
        }},
    ]
    # Latest incoming per customer
    in_pipeline = [
        {"$match": {"customer_id": {"$ne": ""}, **cid_filter}},
        {"$sort": {"created_at": -1}},
        {"$group": {
            "_id": "$customer_id",
            "last_message": {"$first": "$message"},
            "last_time": {"$first": "$created_at"},
            "customer_name": {"$first": "$customer_name"},
            "customer_id": {"$first": "$customer_id"},
            "phones": {"$addToSet": "$phone"},
            "unread": {"$sum": {"$cond": [{"$eq": ["$read", False]}, 1, 0]}},
        }},
    ]

    out_items = await db.sms_queue.aggregate(out_pipeline).to_list(500)
    in_items  = await db.sms_inbox.aggregate(in_pipeline).to_list(500)

    # Merge by customer_id
    merged: dict = {}
    for item in out_items:
        cid = item["_id"]
        branch_ids   = [b for b in item.get("branch_ids",  []) if b]
        branch_names = [b for b in item.get("branch_names",[]) if b]
        phones       = [p for p in item.get("phones", [])       if p]
        merged[cid] = {
            "customer_id": cid,
            "customer_name": item.get("customer_name", cid),
            "phone": phones[0] if phones else "",
            "phones": phones,
            "last_message": item.get("last_message", ""),
            "last_time": item.get("last_time", ""),
            "last_direction": "out",
            "unread": 0,
            "branch_ids": branch_ids,
            "branch_names": branch_names,
        }
    for item in in_items:
        cid = item["_id"]
        unread = item.get("unread", 0)
        phones = [p for p in item.get("phones", []) if p]
        if cid in merged:
            if item.get("last_time", "") > merged[cid]["last_time"]:
                merged[cid]["last_message"]  = item.get("last_message", "")
                merged[cid]["last_time"]     = item.get("last_time", "")
                merged[cid]["last_direction"] = "in"
            merged[cid]["unread"] = unread
            for p in phones:
                if p not in merged[cid]["phones"]:
                    merged[cid]["phones"].append(p)
        else:
            merged[cid] = {
                "customer_id": cid,
                "customer_name": item.get("customer_name", cid),
                "phone": phones[0] if phones else "",
                "phones": phones,
                "last_message": item.get("last_message", ""),
                "last_time": item.get("last_time", ""),
                "last_direction": "in",
                "unread": unread,
                "branch_ids": [],
                "branch_names": [],
            }

    return sorted(merged.values(), key=lambda x: x.get("last_time", ""), reverse=True)


@router.get("/conversation/customer/{customer_id}")
async def get_conversation_by_customer(customer_id: str, user=Depends(get_current_user)):
    """Full message thread for a customer — all their phone numbers merged into one thread."""
    # SECURITY (Iter 216): non-admin users can only open a customer thread when
    # that customer has activity in their assigned branch. Prevents a manager
    # from seeing another branch's customer history by guessing IDs.
    if user.get("role") != "admin":
        user_branch = user.get("branch_id") or ""
        if not user_branch:
            raise HTTPException(status_code=403, detail="Your user has no branch assigned — contact admin")
        in_branch_queue = await db.sms_queue.find_one(
            {"customer_id": customer_id, "branch_id": user_branch}, {"_id": 0, "id": 1}
        )
        in_branch_inbox = await db.sms_inbox.find_one(
            {"customer_id": customer_id, "branch_id": user_branch}, {"_id": 0, "id": 1}
        )
        if not (in_branch_queue or in_branch_inbox):
            raise HTTPException(status_code=403, detail="Customer not in your branch")

    # All phones this customer has ever used (from messages + customer record)
    queue_phones = await db.sms_queue.distinct("phone", {"customer_id": customer_id})
    inbox_phones = await db.sms_inbox.distinct("phone", {"customer_id": customer_id})
    customer_doc = await db.customers.find_one({"id": customer_id}, {"_id": 0, "name": 1, "phones": 1, "phone": 1})
    cust_phones  = (customer_doc or {}).get("phones") or (
        [(customer_doc or {}).get("phone")] if (customer_doc or {}).get("phone") else []
    )
    all_phones = list(set(queue_phones) | set(inbox_phones) | set(cust_phones))

    out_msgs = await db.sms_queue.find(
        {"customer_id": customer_id, "status": {"$in": ["sent", "pending", "failed"]}},
        {"_id": 0, "id": 1, "message": 1, "created_at": 1, "status": 1,
         "customer_name": 1, "template_key": 1, "branch_id": 1, "branch_name": 1,
         "sent_by_name": 1, "phone": 1}
    ).sort("created_at", 1).to_list(500)
    for m in out_msgs:
        m["direction"] = "out"

    in_msgs = await db.sms_inbox.find(
        {"$or": [{"customer_id": customer_id}, {"phone": {"$in": all_phones}}]},
        {"_id": 0, "id": 1, "message": 1, "created_at": 1, "customer_name": 1, "phone": 1}
    ).sort("created_at", 1).to_list(500)
    for m in in_msgs:
        m["direction"] = "in"
        m["status"] = "received"

    # Mark all as read
    await db.sms_inbox.update_many(
        {"$or": [{"customer_id": customer_id}, {"phone": {"$in": all_phones}}]},
        {"$set": {"read": True}}
    )

    all_msgs = sorted(out_msgs + in_msgs, key=lambda x: x.get("created_at", ""))
    customer_name = (customer_doc or {}).get("name", customer_id)
    # Return ALL registered phones (including those not yet used in messages)
    all_registered_phones = sorted(p for p in all_phones if p)
    return {
        "customer_id": customer_id,
        "customer_name": customer_name,
        "phones": all_registered_phones,
        "messages": all_msgs,
        "registered": True,
    }



@router.get("/conversation/{phone}")
async def get_conversation(phone: str, user=Depends(get_current_user)):
    """Get full message thread for a phone number — sent + received merged."""
    # Build all phone variants: 09... and +63... so old and new records are both found
    normalized = phone.lstrip("+")
    if normalized.startswith("63") and len(normalized) > 10:
        normalized = "0" + normalized[2:]
    variants: set = {phone, normalized}
    # Also add the +63 international variant of any 09... number
    if normalized.startswith("09") and len(normalized) == 11:
        variants.add("+63" + normalized[1:])
    phones = list(variants)

    # Outgoing messages
    out_msgs = await db.sms_queue.find(
        {"phone": {"$in": phones}, "status": {"$in": ["sent", "pending", "failed"]}},
        {"_id": 0, "id": 1, "message": 1, "created_at": 1, "status": 1,
         "customer_name": 1, "template_key": 1, "branch_id": 1, "branch_name": 1, "sent_by_name": 1}
    ).sort("created_at", 1).to_list(500)
    for m in out_msgs:
        m["direction"] = "out"

    # Incoming messages
    in_msgs = await db.sms_inbox.find(
        {"phone": {"$in": phones}},
        {"_id": 0, "id": 1, "message": 1, "created_at": 1, "customer_name": 1, "registered": 1}
    ).sort("created_at", 1).to_list(500)
    for m in in_msgs:
        m["direction"] = "in"
        m["status"] = "received"

    # Mark inbox as read
    await db.sms_inbox.update_many({"phone": {"$in": phones}}, {"$set": {"read": True}})

    all_msgs = sorted(out_msgs + in_msgs, key=lambda x: x.get("created_at", ""))
    customer_name = all_msgs[0].get("customer_name", phone) if all_msgs else phone
    # Is this a registered customer conversation?
    registered = any(m.get("registered", True) for m in in_msgs) or bool(out_msgs)
    return {"phone": phone, "customer_name": customer_name, "messages": all_msgs, "registered": registered}


# ══════════════════════════════════════════════════════════════════════════════
# ASSIGN PHONE — Link an unknown number to an existing customer
# Migrates all past inbox messages to the customer's branch
# ══════════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════════
# GATEWAY ACTIVITY LOG — Android APK keylogger / debug feed synced to web
# POST /gateway/log       — single entry (used during foreground)
# POST /gateway/logs/batch — bulk entries (used for buffered offline logs)
# GET  /gateway/logs      — web UI fetches the feed
# DELETE /gateway/logs    — admin clears old logs
# ══════════════════════════════════════════════════════════════════════════════

VALID_LEVELS = {"INFO", "WARN", "ERROR", "DEBUG"}
VALID_EVENTS = {
    "boot", "poll", "send_queued", "sent", "failed",
    "received", "device_sent", "sync", "token_loaded",
    "observer_start", "observer_stop", "db_error", "error", "custom",
}


def _build_log_doc(entry: dict, org_id: str) -> dict:
    level = (entry.get("level") or "INFO").upper()
    if level not in VALID_LEVELS:
        level = "INFO"
    event_type = (entry.get("event_type") or "custom").lower()
    if event_type not in VALID_EVENTS:
        event_type = "custom"
    return {
        "id": new_id(),
        "organization_id": org_id,
        "level": level,
        "event_type": event_type,
        "message": (entry.get("message") or "").strip(),
        "phone": (entry.get("phone") or "").strip(),
        "queue_id": (entry.get("queue_id") or "").strip(),
        "device_id": (entry.get("device_id") or "").strip(),
        "metadata": entry.get("metadata") or {},
        "created_at": entry.get("created_at") or now_iso(),
        "logged_at": now_iso(),
    }


@router.post("/gateway/log")
async def post_gateway_log(data: dict, user=Depends(get_current_user)):
    """Android APK posts a single activity log entry for real-time debugging."""
    if not data.get("message", "").strip():
        raise HTTPException(status_code=400, detail="message required")
    org_id = user.get("organization_id", "")
    doc = _build_log_doc(data, org_id)
    await _raw_db.sms_gateway_logs.insert_one(doc)
    del doc["_id"]
    return {"ok": True}


@router.post("/gateway/logs/batch")
async def post_gateway_logs_batch(data: dict, user=Depends(get_current_user)):
    """Android APK posts buffered log entries in one call (offline-first support)."""
    entries = data.get("entries") or []
    if not entries:
        return {"inserted": 0}
    org_id = user.get("organization_id", "")
    docs = [_build_log_doc(e, org_id) for e in entries[:500] if (e.get("message") or "").strip()]
    if docs:
        await _raw_db.sms_gateway_logs.insert_many(docs)
        for d in docs:
            del d["_id"]
    return {"inserted": len(docs)}


@router.get("/gateway/logs")
async def get_gateway_logs(
    level: Optional[str] = None,
    event_type: Optional[str] = None,
    limit: int = 300,
    skip: int = 0,
    user=Depends(get_current_user),
):
    """Fetch gateway activity logs for the web debug panel."""
    query: dict = {"organization_id": user.get("organization_id", "")}
    if level and level.upper() not in ("ALL", ""):
        query["level"] = level.upper()
    if event_type and event_type not in ("all", ""):
        query["event_type"] = event_type.lower()
    total = await _raw_db.sms_gateway_logs.count_documents(query)
    items = (
        await _raw_db.sms_gateway_logs.find(query, {"_id": 0})
        .sort("created_at", -1)
        .skip(skip)
        .limit(min(limit, 500))
        .to_list(min(limit, 500))
    )
    return {"items": items, "total": total}


@router.delete("/gateway/logs")
async def clear_gateway_logs(user=Depends(get_current_user)):
    """Clear all gateway logs for this organization (admin only)."""
    check_perm(user, "settings", "edit")
    org_id = user.get("organization_id", "")
    result = await _raw_db.sms_gateway_logs.delete_many({"organization_id": org_id})
    return {"deleted": result.deleted_count}


@router.patch("/assign-phone")
async def assign_phone_to_customer(data: dict, user=Depends(get_current_user)):
    """Assign an unregistered phone number to an existing customer.
    All past sms_inbox records for that phone are migrated to the customer's branch.
    The customer's phone field is updated if they don't already have one.
    """
    check_perm(user, "settings", "edit")
    phone = (data.get("phone") or "").strip()
    customer_id = data.get("customer_id", "")
    if not phone or not customer_id:
        raise HTTPException(status_code=400, detail="phone and customer_id required")

    # Normalize phone
    normalized = phone.lstrip("+")
    if normalized.startswith("63") and len(normalized) > 10:
        normalized = "0" + normalized[2:]
    phones = list({phone, normalized})

    # Look up target customer
    customer = await db.customers.find_one(
        {"id": customer_id}, {"_id": 0, "id": 1, "name": 1, "branch_id": 1, "phone": 1}
    )
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    branch_id = customer.get("branch_id", "")

    # ADD the new phone to customer's phones array (not replace)
    await db.customers.update_one(
        {"id": customer_id},
        {"$addToSet": {"phones": normalized}}
    )
    # If customer has no primary phone yet, set it
    if not customer.get("phone"):
        await db.customers.update_one(
            {"id": customer_id}, {"$set": {"phone": normalized, "updated_at": now_iso()}}
        )

    # Migrate all inbox messages for this phone to the customer
    inbox_result = await _raw_db.sms_inbox.update_many(
        {"phone": {"$in": phones}},
        {"$set": {
            "customer_id": customer["id"],
            "customer_name": customer["name"],
            "branch_id": branch_id,
            "registered": True,
            "phone": normalized,
        }}
    )
    # Also update any unattributed outgoing queue messages for this phone
    await _raw_db.sms_queue.update_many(
        {"phone": {"$in": phones}, "customer_id": ""},
        {"$set": {
            "customer_id": customer["id"],
            "customer_name": customer["name"],
            "branch_id": branch_id,
        }}
    )

    return {
        "migrated_messages": inbox_result.modified_count,
        "customer_name": customer["name"],
        "customer_id": customer["id"],
        "branch_id": branch_id,
        "phone": normalized,
    }

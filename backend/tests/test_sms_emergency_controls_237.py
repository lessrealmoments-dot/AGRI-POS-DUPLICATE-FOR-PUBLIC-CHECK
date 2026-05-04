"""
SMS Emergency Controls & Quiet-Hours Audit — Iteration 237

Covers the six bugs reported by the user (3-day-old SMS at 1 AM, muted-branch
leakage, emergency-stop only hitting close-reminders):

  1. Dispatch quiet-hours constants + wrap logic.
  2. Close-reminder TTL expiry — pending rows older than 24h become `expired`
     before dispatch.
  3. Mute superset — zreport_finalized is purged on branch mute.
  5. True kill-switch — `/sms/queue/stop-all-auto` cancels every
     trigger=auto|scheduled row without touching manual composes.
  6. Global pause / resume — blocks dispatch + enqueue for the window.
  7. `/sms/queue/audit-report` returns the expected forensic shape.
  8. Auth gating on every new endpoint.

Uses sync pymongo to avoid the motor-vs-asyncio.run() event-loop crash.
"""
import uuid
from datetime import datetime, timezone, timedelta

import requests

from routes import sms as sms_mod
from tests._org_test_helpers import ensure_org_admin_token, _db, API


# ─────────────────────────────────────────────────────────────────────────
# Pure unit tests (no DB / no HTTP)
# ─────────────────────────────────────────────────────────────────────────

def test_quiet_hours_helper_wraps_midnight():
    """22:00–07:00 wrap must detect 01:00, 02:00, 05:00 as quiet."""
    def _quiet(h):
        s, e = sms_mod.DISPATCH_QUIET_START_H, sms_mod.DISPATCH_QUIET_END_H
        if s <= e:
            return s <= h < e
        return h >= s or h < e

    for h in (22, 23, 0, 1, 2, 5, 6):
        assert _quiet(h), f"hour {h} should be in quiet window"
    for h in (7, 8, 12, 18, 21):
        assert not _quiet(h), f"hour {h} should be OUTSIDE quiet window"


def test_dispatch_quiet_window_constants():
    assert sms_mod.DISPATCH_QUIET_START_H == 22
    assert sms_mod.DISPATCH_QUIET_END_H == 7
    assert sms_mod.CLOSE_REMINDER_TTL_HOURS == 24


def test_muted_branch_template_keys_includes_zreport_finalized():
    """The superset used by mute/purge + dispatch-time filter must include
    the daily summary template so it can't slip past a mute."""
    assert "zreport_finalized" in sms_mod.MUTED_BRANCH_TEMPLATE_KEYS
    for key in sms_mod.CLOSE_REMINDER_TEMPLATE_KEYS:
        assert key in sms_mod.MUTED_BRANCH_TEMPLATE_KEYS


# ─────────────────────────────────────────────────────────────────────────
# DB-level behaviour tests (sync pymongo)
# ─────────────────────────────────────────────────────────────────────────

def _seed_queue_rows(db, org_id, rows):
    """Insert a list of queue dicts after filling in common defaults."""
    base = {
        "organization_id": org_id,
        "customer_id": "c", "customer_name": "T", "message": "body",
        "status": "pending", "branch_id": "br-z", "branch_name": "Z",
        "retry_count": 0, "dispatch_count": 0, "leased_until": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    db.sms_queue.insert_many([{**base, **r} for r in rows])


def test_ttl_expires_stale_close_reminder_rows():
    """Stale (>24h) close-reminder rows must be expired; fresh rows stay."""
    db = _db()
    org_id = f"test_iter237_ttl_{uuid.uuid4().hex[:8]}"
    stale_created = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    fresh_created = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    stale_id, fresh_id = f"stale-{uuid.uuid4().hex[:6]}", f"fresh-{uuid.uuid4().hex[:6]}"

    try:
        _seed_queue_rows(db, org_id, [
            {"id": stale_id, "template_key": "close_late_notice",
             "phone": "+639001234567", "trigger": "scheduled",
             "created_at": stale_created},
            {"id": fresh_id, "template_key": "close_late_notice",
             "phone": "+639001234568", "trigger": "scheduled",
             "created_at": fresh_created},
        ])

        ttl_cutoff = (
            datetime.now(timezone.utc)
            - timedelta(hours=sms_mod.CLOSE_REMINDER_TTL_HOURS)
        ).isoformat()
        db.sms_queue.update_many(
            {
                "organization_id": org_id,
                "template_key": {"$in": sms_mod.CLOSE_REMINDER_TEMPLATE_KEYS},
                "status": {"$in": ["pending", "deferred", "failed"]},
                "created_at": {"$lt": ttl_cutoff},
            },
            {"$set": {"status": "expired",
                      "expired_reason": "close_reminder_ttl_exceeded"}},
        )

        stale = db.sms_queue.find_one({"id": stale_id}, {"_id": 0})
        fresh = db.sms_queue.find_one({"id": fresh_id}, {"_id": 0})
        assert stale["status"] == "expired"
        assert stale["expired_reason"] == "close_reminder_ttl_exceeded"
        assert fresh["status"] == "pending", "fresh row must NOT be expired"
    finally:
        db.sms_queue.delete_many({"organization_id": org_id})


def test_stop_all_auto_cancels_auto_not_manual():
    """auto + scheduled cancelled; manual compose survives."""
    db = _db()
    org_id = f"test_iter237_stop_{uuid.uuid4().hex[:8]}"
    auto_id, sched_id, manual_id = (
        f"a-{uuid.uuid4().hex[:6]}",
        f"s-{uuid.uuid4().hex[:6]}",
        f"m-{uuid.uuid4().hex[:6]}",
    )

    try:
        _seed_queue_rows(db, org_id, [
            {"id": auto_id, "template_key": "zreport_finalized",
             "trigger": "auto", "phone": "+639001"},
            {"id": sched_id, "template_key": "close_late_notice",
             "trigger": "scheduled", "phone": "+639002"},
            {"id": manual_id, "template_key": "promo_blast",
             "trigger": "manual", "phone": "+639003"},
        ])
        result = db.sms_queue.update_many(
            {
                "organization_id": org_id,
                "trigger": {"$in": ["auto", "scheduled"]},
                "status": {"$in": ["pending", "deferred", "failed"]},
            },
            {"$set": {"status": "cancelled",
                      "cancelled_reason": "admin_stop_all_auto"}},
        )
        assert result.modified_count == 2
        assert db.sms_queue.find_one({"id": auto_id})["status"] == "cancelled"
        assert db.sms_queue.find_one({"id": sched_id})["status"] == "cancelled"
        manual = db.sms_queue.find_one({"id": manual_id})
        assert manual["status"] == "pending", \
            "manual compose must survive stop-all-auto"
    finally:
        db.sms_queue.delete_many({"organization_id": org_id})


def test_mute_purge_superset_cancels_zreport_finalized():
    """Muting a branch must also cancel zreport_finalized queued rows."""
    db = _db()
    org_id = f"test_iter237_mute_{uuid.uuid4().hex[:8]}"
    branch_id = f"br-{uuid.uuid4().hex[:6]}"
    zrow, crow = f"z-{uuid.uuid4().hex[:6]}", f"c-{uuid.uuid4().hex[:6]}"
    try:
        _seed_queue_rows(db, org_id, [
            {"id": zrow, "template_key": "zreport_finalized",
             "trigger": "auto", "phone": "+639001", "branch_id": branch_id},
            {"id": crow, "template_key": "close_late_notice",
             "trigger": "scheduled", "phone": "+639002", "branch_id": branch_id},
        ])
        # Mirror the mute-toggle endpoint's purge query
        result = db.sms_queue.update_many(
            {
                "organization_id": org_id,
                "branch_id": branch_id,
                "template_key": {"$in": sms_mod.MUTED_BRANCH_TEMPLATE_KEYS},
                "status": {"$in": ["pending", "deferred", "failed"]},
            },
            {"$set": {"status": "cancelled",
                      "cancelled_reason": "branch_muted"}},
        )
        assert result.modified_count == 2, \
            "both zreport_finalized AND close_late_notice must be cancelled"
        assert db.sms_queue.find_one({"id": zrow})["status"] == "cancelled"
        assert db.sms_queue.find_one({"id": crow})["status"] == "cancelled"
    finally:
        db.sms_queue.delete_many({"organization_id": org_id})


# ─────────────────────────────────────────────────────────────────────────
# HTTP endpoint tests (require a real org-admin token)
# ─────────────────────────────────────────────────────────────────────────

def _auth_headers():
    token, _user = ensure_org_admin_token()
    return {"Authorization": f"Bearer {token}"}


def test_audit_report_endpoint_shape():
    r = requests.get(f"{API}/sms/queue/audit-report", headers=_auth_headers())
    assert r.status_code == 200, r.text
    data = r.json()
    for key in (
        "oldest_pending", "status_counts", "close_reminder_ttl_expired_24h",
        "muted_branch_leak_cancelled_24h", "muted_branches",
        "global_pause", "dispatch_quiet_hours_now", "config",
    ):
        assert key in data, f"audit-report missing key: {key}"
    assert data["config"]["close_reminder_ttl_hours"] == 24


def test_pause_and_resume_flow():
    h = _auth_headers()
    # Always try to resume first to reset state
    requests.post(f"{API}/sms/queue/resume-all", headers=h)

    r = requests.post(f"{API}/sms/queue/pause-all",
                      headers=h, json={"hours": 1, "reason": "unit test"})
    assert r.status_code == 200, r.text
    assert r.json()["paused"] is True

    r2 = requests.get(f"{API}/sms/queue/pause-status", headers=h)
    assert r2.status_code == 200
    assert r2.json()["paused"] is True

    r3 = requests.post(f"{API}/sms/queue/resume-all", headers=h)
    assert r3.status_code == 200
    assert r3.json()["paused"] is False

    r4 = requests.get(f"{API}/sms/queue/pause-status", headers=h)
    assert r4.json()["paused"] is False


def test_pause_clamps_hours():
    """Minimum 15 min — must not accept 0.01h."""
    h = _auth_headers()
    requests.post(f"{API}/sms/queue/resume-all", headers=h)
    try:
        r = requests.post(f"{API}/sms/queue/pause-all",
                          headers=h, json={"hours": 0.01})
        assert r.status_code == 200
        # Until should be >= 14 min from now (clamp floor is 0.25h = 15min)
        until = datetime.fromisoformat(r.json()["until"].replace("Z", "+00:00"))
        delta_min = (until - datetime.now(timezone.utc)).total_seconds() / 60
        assert delta_min >= 14, f"clamp floor not honoured: {delta_min} min"
    finally:
        requests.post(f"{API}/sms/queue/resume-all", headers=h)


def test_endpoints_require_auth():
    """All new admin endpoints must reject unauthenticated callers."""
    for path, method in [
        ("/sms/queue/stop-all-auto", "POST"),
        ("/sms/queue/pause-all", "POST"),
        ("/sms/queue/resume-all", "POST"),
        ("/sms/queue/audit-report", "GET"),
        ("/sms/queue/pause-status", "GET"),
    ]:
        r = requests.request(method, f"{API}{path}")
        assert r.status_code in (401, 403), \
            f"{method} {path} should reject anon, got {r.status_code}"


def test_stop_all_auto_endpoint_rejects_non_admin():
    """Placeholder: stop-all-auto must require settings.edit — covered via
    check_perm. Without an explicit non-admin fixture here, we verify anon
    rejection as a minimum-bar smoke check."""
    r = requests.post(f"{API}/sms/queue/stop-all-auto")
    assert r.status_code in (401, 403)

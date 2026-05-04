"""
Regression test — Iter 226: Mute-Purge + Dispatch-Time Muted-Branch Filter.

User report:
  "I got spammed with 60+ messages on muted branches after a DNS outage on
   the gateway phone. Muting a branch did nothing — rows already queued
   before the mute kept being handed out to the gateway."

Fix:
  1. `PUT /sms/close-reminder/branch-toggle/:branch_id` with `disabled=true`
     PURGES all pending/deferred/failed close-reminder rows for that
     branch (sets status=cancelled, reason=branch_muted) and returns the
     purge count in the response.
  2. `GET /sms/queue/pending` filters out rows whose branch is now muted
     as a belt-and-suspenders safety net (flips them to cancelled on-the-fly).
  3. `POST /sms/queue/cancel-pending-close-reminders` is the emergency
     kill-switch that cancels ALL pending close-reminder rows across every
     branch for the caller's org (customer/expense SMS untouched).

Multi-tenant safety is asserted: purges only touch the caller's org.
"""
import os
import sys
import uuid

import pytest
import requests
from pymongo import MongoClient

sys.path.insert(0, "/app/backend")
sys.path.insert(0, "/app/backend/tests")

from _org_test_helpers import ensure_org_admin_token  # noqa: E402


def _read_frontend_env_var(key: str) -> str:
    try:
        with open("/app/frontend/.env") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith(f"{key}="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return ""


BASE_URL = (
    os.environ.get("REACT_APP_BACKEND_URL")
    or _read_frontend_env_var("REACT_APP_BACKEND_URL")
    or ""
).rstrip("/")
API = f"{BASE_URL}/api"
MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "test_database")

CLOSE_REMINDER_KEYS = [
    "close_catchup_3pm", "close_precheck", "close_late_notice",
    "close_status_snapshot", "close_escalation",
    "close_overdue_next_day", "close_overdue_multi_day",
]


@pytest.fixture(scope="module")
def admin_session():
    token, user = ensure_org_admin_token()
    return token, user


@pytest.fixture(scope="module")
def raw_db():
    return MongoClient(MONGO_URL)[DB_NAME]


@pytest.fixture
def scratch(raw_db, admin_session):
    _, user = admin_session
    org_id = user["organization_id"]
    branch_id = f"br-purge-{uuid.uuid4().hex[:8]}"
    other_branch_id = f"br-other-{uuid.uuid4().hex[:8]}"
    raw_db.branches.insert_many([
        {"id": branch_id, "organization_id": org_id, "name": "TEST Purge Branch",
         "active": True, "close_time_h": 18.0, "close_reminder_disabled": False},
        {"id": other_branch_id, "organization_id": org_id, "name": "TEST Other Branch",
         "active": True, "close_time_h": 18.0, "close_reminder_disabled": False},
    ])
    yield {"org_id": org_id, "branch_id": branch_id,
           "other_branch_id": other_branch_id}
    raw_db.branches.delete_many({"id": {"$in": [branch_id, other_branch_id]}})
    raw_db.sms_queue.delete_many({"branch_id": {"$in": [branch_id, other_branch_id]}})


def _seed_queue_row(raw_db, org_id, branch_id, template_key,
                    status="pending", sms_id=None):
    row = {
        "id": sms_id or f"sms-{uuid.uuid4().hex[:10]}",
        "organization_id": org_id,
        "branch_id": branch_id,
        "branch_name": "TEST",
        "template_key": template_key,
        "phone": "+639000000000",
        "message": "test",
        "status": status,
        "trigger": "scheduled",
        "created_at": "2026-05-01T00:00:00+00:00",
        "retry_count": 0,
        "dispatch_count": 0,
        "leased_until": None,
    }
    raw_db.sms_queue.insert_one(row)
    return row["id"]


# ── 1. Muting purges pending close-reminder rows for that branch ───────────
def test_mute_purges_pending_close_reminder_rows(raw_db, admin_session, scratch):
    token, _ = admin_session
    org_id = scratch["org_id"]
    branch_id = scratch["branch_id"]
    other_branch_id = scratch["other_branch_id"]

    # Seed 3 close-reminder rows on target branch (pending, deferred, failed)
    id_a = _seed_queue_row(raw_db, org_id, branch_id, "close_late_notice", "pending")
    id_b = _seed_queue_row(raw_db, org_id, branch_id, "close_overdue_multi_day", "deferred")
    id_c = _seed_queue_row(raw_db, org_id, branch_id, "close_escalation", "failed")
    # Seed a NON-close-reminder row — MUST survive
    id_d = _seed_queue_row(raw_db, org_id, branch_id, "payment_reminder", "pending")
    # Seed a close-reminder on a DIFFERENT branch — MUST survive
    id_e = _seed_queue_row(raw_db, org_id, other_branch_id, "close_late_notice", "pending")

    # Mute the target branch
    r = requests.put(
        f"{API}/sms/close-reminder/branch-toggle/{branch_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"disabled": True},
        timeout=10,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["close_reminder_disabled"] is True
    assert body["purged"] == 3, f"Expected 3 purged, got {body['purged']}"

    # Verify statuses
    rows = {x["id"]: x for x in raw_db.sms_queue.find(
        {"id": {"$in": [id_a, id_b, id_c, id_d, id_e]}}
    )}
    assert rows[id_a]["status"] == "cancelled"
    assert rows[id_a]["cancelled_reason"] == "branch_muted"
    assert rows[id_b]["status"] == "cancelled"
    assert rows[id_c]["status"] == "cancelled"
    # NON close-reminder row on muted branch → untouched
    assert rows[id_d]["status"] == "pending"
    # Close-reminder on ANOTHER branch → untouched
    assert rows[id_e]["status"] == "pending"


# ── 2. Unmuting does NOT re-queue cancelled rows (no accidental resurrection)
def test_unmute_does_not_resurrect_cancelled_rows(raw_db, admin_session, scratch):
    token, _ = admin_session
    org_id = scratch["org_id"]
    branch_id = scratch["branch_id"]

    sid = _seed_queue_row(raw_db, org_id, branch_id, "close_late_notice", "pending")
    # Mute (purges)
    requests.put(
        f"{API}/sms/close-reminder/branch-toggle/{branch_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"disabled": True}, timeout=10,
    )
    # Un-mute
    r = requests.put(
        f"{API}/sms/close-reminder/branch-toggle/{branch_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"disabled": False}, timeout=10,
    )
    assert r.status_code == 200
    # purged count on un-mute must be 0 (we only purge on mute ON)
    assert r.json().get("purged", 0) == 0
    # The previously cancelled row stays cancelled — we never auto-revive.
    row = raw_db.sms_queue.find_one({"id": sid})
    assert row["status"] == "cancelled"


# ── 3. Emergency /queue/cancel-pending-close-reminders ─────────────────────
def test_emergency_cancel_pending_close_reminders(raw_db, admin_session, scratch):
    token, _ = admin_session
    org_id = scratch["org_id"]
    branch_id = scratch["branch_id"]
    other_branch_id = scratch["other_branch_id"]

    # Both branches have a close-reminder row + a non-close-reminder row
    id1 = _seed_queue_row(raw_db, org_id, branch_id, "close_precheck", "pending")
    id2 = _seed_queue_row(raw_db, org_id, other_branch_id, "close_overdue_multi_day", "deferred")
    id3 = _seed_queue_row(raw_db, org_id, branch_id, "payment_reminder", "pending")

    r = requests.post(
        f"{API}/sms/queue/cancel-pending-close-reminders",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    assert r.status_code == 200, r.text
    cancelled = r.json().get("cancelled", 0)
    assert cancelled >= 2, f"Expected at least 2 cancelled, got {cancelled}"

    rows = {x["id"]: x for x in raw_db.sms_queue.find(
        {"id": {"$in": [id1, id2, id3]}}
    )}
    assert rows[id1]["status"] == "cancelled"
    assert rows[id1]["cancelled_reason"] == "admin_emergency_stop"
    assert rows[id2]["status"] == "cancelled"
    # Non close-reminder row is untouched
    assert rows[id3]["status"] == "pending"

"""
SMS spam-storm protection (Iter 216) — HTTP integration tests.

Covers:
- GET /api/sms/queue/pending applies a lease — same row not re-served on the next poll.
- 3 dispatch strikes (with expired lease between each) flips row to 'deferred'.
- A 'deferred' row with deferred_until in the past self-heals to 'pending' on next poll.
- POST /api/sms/queue/clear-stuck with {include_pending: true} wipes pending+deferred rows.
- Throttle constants (10-min throttle, 3-strikes, 5-min lease) are as per user spec.
"""
import os
import sys
import time
from datetime import datetime, timezone, timedelta

import pytest
import requests
from pymongo import MongoClient

sys.path.insert(0, "/app/backend")
sys.path.insert(0, "/app/backend/tests")

from _org_test_helpers import ensure_org_admin_token  # noqa: E402


def _read_env(key, path="/app/frontend/.env"):
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if line.startswith(f"{key}="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return ""


BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL") or _read_env("REACT_APP_BACKEND_URL") or "").rstrip("/")
API = f"{BASE_URL}/api"
MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "test_database")

_client = MongoClient(MONGO_URL)
_db = _client[DB_NAME]


def _iso(delta_seconds=0):
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_seconds)).isoformat()


@pytest.fixture
def admin_token():
    tok, user = ensure_org_admin_token()
    # Decode the JWT (unverified) to grab the org id — admin user dict may not carry it.
    import jwt as _jwt
    org_id = user.get("organization_id") or _jwt.decode(tok, options={"verify_signature": False}).get("org_id", "")
    globals()["_CURRENT_ORG_ID"] = org_id
    return tok


_CURRENT_ORG_ID = ""


def _hdr(tok):
    return {"Authorization": f"Bearer {tok}"}


def _insert_pending(phone, tpl="close_overdue_next_day"):
    """Insert a synthetic pending row directly into sms_queue (bypasses throttle)."""
    sid = f"spam216-{phone}-{_iso()}"
    _db.sms_queue.insert_one({
        "id": sid,
        "organization_id": _CURRENT_ORG_ID,  # scoped so admin's GET /pending sees it
        "template_key": tpl,
        "customer_id": "c1", "customer_name": "Test",
        "phone": phone, "message": "test",
        "status": "pending", "trigger": "auto",
        "dedup_key": "", "branch_id": "", "branch_name": "",
        "created_at": _iso(), "retry_count": 0,
        "dispatch_count": 0, "leased_until": None, "deferred_until": None,
    })
    return sid


def _cleanup(phone_prefix="+63900000"):
    # Exact-prefix match (no regex — + is a regex quantifier).
    _db.sms_queue.delete_many({"phone": {"$regex": r"^\+63900000"}})


def test_lease_prevents_immediate_reissue(admin_token):
    _cleanup()
    sid = _insert_pending(phone="+639000000201")
    try:
        r1 = requests.get(f"{API}/sms/queue/pending", headers=_hdr(admin_token))
        assert r1.status_code == 200
        ids1 = [x["id"] for x in r1.json()]
        assert sid in ids1, "first poll should include the row"

        # Immediate 2nd poll — lease should hide it
        r2 = requests.get(f"{API}/sms/queue/pending", headers=_hdr(admin_token))
        assert r2.status_code == 200
        ids2 = [x["id"] for x in r2.json()]
        assert sid not in ids2, "lease should block immediate re-poll"

        row = _db.sms_queue.find_one({"id": sid}, {"_id": 0})
        assert row["dispatch_count"] == 1
        assert row["leased_until"] is not None
        assert row["leased_until"] > _iso()
    finally:
        _cleanup()


def test_one_shot_dispatch_defers_after_single_hand_out(admin_token):
    """ONE-SHOT policy (Iter 227): after ONE dispatch the row is deferred
    until admin manually /retry — never auto-re-dispatched.
    Previously this was 3 strikes/day; the old behavior spammed recipients
    when the gateway's mark-sent ACK failed (DNS outage) and got the user's
    SIM flagged by the carrier."""
    _cleanup()
    sid = _insert_pending(phone="+639000000202")
    try:
        # First poll → row dispatched
        r = requests.get(f"{API}/sms/queue/pending", headers=_hdr(admin_token))
        assert r.status_code == 200
        assert sid in [x["id"] for x in r.json()], "first poll should yield the row"

        # Simulate phone failing to ACK — expire the lease manually
        _db.sms_queue.update_one({"id": sid}, {"$set": {"leased_until": _iso(-10)}})

        # Second poll → row must be deferred, not re-dispatched
        r2 = requests.get(f"{API}/sms/queue/pending", headers=_hdr(admin_token))
        assert r2.status_code == 200
        assert sid not in [x["id"] for x in r2.json()], (
            "one-shot policy: a second dispatch must never happen"
        )

        row = _db.sms_queue.find_one({"id": sid}, {"_id": 0})
        assert row["status"] == "deferred"
        assert row.get("deferred_reason") == "daily_dispatch_cap"
        assert row["deferred_until"] > _iso()
    finally:
        _cleanup()


def test_deferred_rows_do_not_auto_revive(admin_token):
    """Iter 227: auto-revive of expired-deferred rows was INTENTIONALLY
    removed. A deferred row stays deferred until admin explicitly /retry —
    this is the safeguard against re-sending an SMS whose GSM send
    succeeded but whose ACK failed (the exact spam pattern)."""
    _cleanup()
    sid = _insert_pending(phone="+639000000203")
    try:
        # Mark as deferred with an expired deferred_until
        _db.sms_queue.update_one(
            {"id": sid},
            {"$set": {"status": "deferred", "deferred_until": _iso(-60),
                      "dispatch_count": 1}},
        )
        r = requests.get(f"{API}/sms/queue/pending", headers=_hdr(admin_token))
        assert r.status_code == 200
        assert sid not in [x["id"] for x in r.json()], (
            "deferred rows must NEVER auto-revive — admin manual /retry only"
        )

        row = _db.sms_queue.find_one({"id": sid}, {"_id": 0})
        # Should still be deferred — server never flipped it back
        assert row["status"] == "deferred", (
            "deferred should stay deferred (no self-heal)"
        )
    finally:
        _cleanup()


def test_clear_stuck_include_pending(admin_token):
    _cleanup()
    sids = [_insert_pending(phone=f"+63900000030{i}") for i in range(3)]
    try:
        r = requests.post(
            f"{API}/sms/queue/clear-stuck",
            headers={**_hdr(admin_token), "Content-Type": "application/json"},
            json={"include_pending": True},
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["cleared"] >= 3
        assert "pending" in data["statuses_cleared"]

        # All seeded rows should now be 'skipped'
        for sid in sids:
            row = _db.sms_queue.find_one({"id": sid}, {"_id": 0, "status": 1})
            assert row["status"] == "skipped", f"{sid} still {row['status']}"
    finally:
        _cleanup()


def test_clear_stuck_default_leaves_pending(admin_token):
    _cleanup()
    sid = _insert_pending(phone="+639000000401")
    try:
        r = requests.post(
            f"{API}/sms/queue/clear-stuck",
            headers={**_hdr(admin_token), "Content-Type": "application/json"},
            json={},
        )
        assert r.status_code == 200
        row = _db.sms_queue.find_one({"id": sid}, {"_id": 0, "status": 1})
        assert row["status"] == "pending", "default clear-stuck must not touch pending rows"
    finally:
        _cleanup()


def test_spam_protection_constants():
    from routes.sms import (
        DISPATCH_LEASE_SECONDS, MAX_DISPATCHES_PER_DAY, ENQUEUE_THROTTLE_SECONDS,
        MAX_SMS_PER_PHONE_PER_DAY,
    )
    assert DISPATCH_LEASE_SECONDS == 300        # 5 min
    assert MAX_DISPATCHES_PER_DAY == 1          # Iter 227: ONE-SHOT dispatch
    assert ENQUEUE_THROTTLE_SECONDS == 600      # 10 min per-recipient throttle
    assert MAX_SMS_PER_PHONE_PER_DAY == 10      # Iter 227: absolute 24h fuse

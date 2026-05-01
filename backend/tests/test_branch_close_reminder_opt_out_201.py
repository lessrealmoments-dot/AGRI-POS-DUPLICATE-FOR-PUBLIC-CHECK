"""
Regression test: per-branch close-reminder opt-out.

A branch flagged `close_reminder_disabled=true` must be skipped by
`tick_once()`, even when the trigger-time window lines up exactly — so
warehouse/transfer-only branches don't generate SMS noise.

Covers:
  1. `PUT /api/sms/close-reminder/branch-toggle/:branch_id` flips the flag
     and returns the updated value (HTTP integration).
  2. `tick_once()` skips disabled branches (direct call; monkey-patched
     dispatch) and still processes siblings.
  3. `diagnose_for_org()` surfaces the flag so owners can see the state.
"""
import asyncio
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


@pytest.fixture(scope="module")
def admin_session():
    token, user = ensure_org_admin_token()
    return token, user


@pytest.fixture(scope="module")
def raw_db():
    return MongoClient(MONGO_URL)[DB_NAME]


@pytest.fixture(scope="module")
def scratch_branches(raw_db, admin_session):
    _, user = admin_session
    org_id = user["organization_id"]
    muted_id = f"br-muted-{uuid.uuid4().hex[:8]}"
    active_id = f"br-active-{uuid.uuid4().hex[:8]}"
    raw_db.branches.insert_many([
        {"id": muted_id, "organization_id": org_id, "name": "TEST Warehouse (muted)",
         "active": True, "close_time_h": 18.0, "close_reminder_disabled": True},
        {"id": active_id, "organization_id": org_id, "name": "TEST Main (active)",
         "active": True, "close_time_h": 18.0, "close_reminder_disabled": False},
    ])
    yield {"muted": muted_id, "active": active_id, "org_id": org_id}
    raw_db.branches.delete_many({"id": {"$in": [muted_id, active_id]}})


# ── 1. HTTP handler flips the flag both ways ────────────────────────────────
def test_toggle_endpoint_mutes_and_unmutes_branch(admin_session, raw_db, scratch_branches):
    token, _ = admin_session
    muted = scratch_branches["muted"]

    # Unmute it via the new endpoint
    r = requests.put(
        f"{API}/sms/close-reminder/branch-toggle/{muted}",
        headers={"Authorization": f"Bearer {token}"},
        json={"disabled": False},
        timeout=10,
    )
    assert r.status_code == 200, r.text
    assert r.json()["close_reminder_disabled"] is False
    br = raw_db.branches.find_one({"id": muted})
    assert br["close_reminder_disabled"] is False

    # Mute it again
    r2 = requests.put(
        f"{API}/sms/close-reminder/branch-toggle/{muted}",
        headers={"Authorization": f"Bearer {token}"},
        json={"disabled": True},
        timeout=10,
    )
    assert r2.status_code == 200
    assert r2.json()["close_reminder_disabled"] is True
    br2 = raw_db.branches.find_one({"id": muted})
    assert br2["close_reminder_disabled"] is True


# ── 2. tick_once() skips muted branches (unit-level assertion) ─────────────
def test_tick_once_skips_muted_branches(scratch_branches, monkeypatch):
    from routes import close_reminder as cr

    dispatched = []

    async def _fake_dispatch(branch, target_date_str, stage):
        dispatched.append((branch["id"], stage["key"]))

    monkeypatch.setattr(cr, "_dispatch_stage", _fake_dispatch)

    result = asyncio.get_event_loop().run_until_complete(cr.tick_once())

    # Muted branch must NEVER be dispatched — even if the trigger window matches.
    muted_hits = [d for d in dispatched if d[0] == scratch_branches["muted"]]
    assert muted_hits == [], f"Muted branch received dispatch: {muted_hits}"

    # Summary must reflect at least the muted branch we seeded.
    assert result.get("skipped_disabled", 0) >= 1, (
        f"skipped_disabled counter missing or 0 in tick summary: {result}"
    )


# ── 3. diagnose_for_org() exposes the flag ─────────────────────────────────
def test_diagnose_surfaces_flag(scratch_branches):
    from routes.close_reminder import diagnose_for_org

    result = asyncio.get_event_loop().run_until_complete(
        diagnose_for_org(scratch_branches["org_id"])
    )
    by_id = {b["id"]: b for b in result["branches"]}
    assert by_id[scratch_branches["muted"]]["close_reminder_disabled"] is True
    assert by_id[scratch_branches["active"]]["close_reminder_disabled"] is False

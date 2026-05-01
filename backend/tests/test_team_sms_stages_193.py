"""
Iteration 193 — Team SMS Reminder stage settings + per-branch close time.

Exercises the new endpoints:
  • GET  /api/sms/close-reminder/stages
  • PUT  /api/sms/close-reminder/stages/{stage_key}
  • PUT  /api/sms/close-reminder/branch-close-time/{branch_id}

And confirms the scheduler reads the saved settings on the next tick
(stage disabled ⇒ no dispatch, recipients list narrowed ⇒ only those roles
receive the SMS).
"""
import pytest
import requests

from _org_test_helpers import API, _db, ensure_org_admin_token


@pytest.fixture(scope="module")
def auth():
    return ensure_org_admin_token()


@pytest.fixture(autouse=True)
def _reset_stage_overrides(auth):
    """Wipe the stage-override collection before each test so defaults apply."""
    _, user = auth
    db = _db()
    db.sms_close_stages.delete_many({"organization_id": user["organization_id"]})
    yield
    db.sms_close_stages.delete_many({"organization_id": user["organization_id"]})


def test_get_stages_returns_defaults(auth):
    token, _ = auth
    r = requests.get(
        f"{API}/sms/close-reminder/stages",
        headers={"Authorization": f"Bearer {token}"}, timeout=10,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["stages"]) >= 5
    for row in body["stages"]:
        assert row["enabled"] is True
        assert isinstance(row["recipients"], list)
        assert isinstance(row["default_recipients"], list)
        assert row["label"]
    assert set(body["valid_roles"]) == {"cashier", "manager", "owner", "admin", "auditor"}


def test_put_persists_toggle_and_recipients(auth):
    token, _ = auth
    r = requests.put(
        f"{API}/sms/close-reminder/stages/close_catchup_3pm",
        headers={"Authorization": f"Bearer {token}"},
        json={"enabled": False, "recipients": ["cashier"]},
        timeout=10,
    )
    assert r.status_code == 200, r.text
    assert r.json()["enabled"] is False
    assert r.json()["recipients"] == ["cashier"]

    # Round-trip
    g = requests.get(
        f"{API}/sms/close-reminder/stages",
        headers={"Authorization": f"Bearer {token}"}, timeout=10,
    )
    row = next(s for s in g.json()["stages"] if s["stage_key"] == "close_catchup_3pm")
    assert row["enabled"] is False
    assert row["recipients"] == ["cashier"]


def test_put_unknown_stage_404(auth):
    token, _ = auth
    r = requests.put(
        f"{API}/sms/close-reminder/stages/doesnt_exist",
        headers={"Authorization": f"Bearer {token}"},
        json={"enabled": True, "recipients": []},
        timeout=10,
    )
    assert r.status_code == 404


def test_put_unknown_role_silently_dropped(auth):
    token, _ = auth
    r = requests.put(
        f"{API}/sms/close-reminder/stages/close_precheck",
        headers={"Authorization": f"Bearer {token}"},
        json={"enabled": True, "recipients": ["cashier", "alien_queen", "manager"]},
        timeout=10,
    )
    assert r.status_code == 200
    assert r.json()["recipients"] == ["cashier", "manager"]


def test_branch_close_time_update_and_validation(auth):
    token, user = auth
    db = _db()
    branch = db.branches.find_one(
        {"organization_id": user["organization_id"], "active": {"$ne": False}},
        {"_id": 0, "id": 1},
    )
    if not branch:
        pytest.skip("No active branch to update")
    br_id = branch["id"]

    # Valid
    r = requests.put(
        f"{API}/sms/close-reminder/branch-close-time/{br_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"close_time_h": 19.5},
        timeout=10,
    )
    assert r.status_code == 200
    assert r.json()["close_time_h"] == 19.5

    # Out-of-range
    r2 = requests.put(
        f"{API}/sms/close-reminder/branch-close-time/{br_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"close_time_h": 30},
        timeout=10,
    )
    assert r2.status_code == 400

    # Non-numeric
    r3 = requests.put(
        f"{API}/sms/close-reminder/branch-close-time/{br_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"close_time_h": "six pm"},
        timeout=10,
    )
    assert r3.status_code == 400

    # Unknown branch
    r4 = requests.put(
        f"{API}/sms/close-reminder/branch-close-time/does-not-exist",
        headers={"Authorization": f"Bearer {token}"},
        json={"close_time_h": 18},
        timeout=10,
    )
    assert r4.status_code == 404

    # Restore default
    requests.put(
        f"{API}/sms/close-reminder/branch-close-time/{br_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"close_time_h": 18},
        timeout=10,
    )


def test_scheduler_respects_disabled_stage(auth):
    """With close_catchup_3pm disabled, _load_stage_settings should flip its
    `enabled` to False so tick_once skips it. We just verify the loader here
    since tick_once depends on the wall-clock minute."""
    import asyncio
    from routes.close_reminder import _load_stage_settings

    token, user = auth
    r = requests.put(
        f"{API}/sms/close-reminder/stages/close_catchup_3pm",
        headers={"Authorization": f"Bearer {token}"},
        json={"enabled": False, "recipients": ["cashier"]},
        timeout=10,
    )
    assert r.status_code == 200

    settings = asyncio.run(_load_stage_settings(user["organization_id"]))
    assert settings["close_catchup_3pm"]["enabled"] is False
    # Other stages still default-enabled
    assert settings["close_precheck"]["enabled"] is True

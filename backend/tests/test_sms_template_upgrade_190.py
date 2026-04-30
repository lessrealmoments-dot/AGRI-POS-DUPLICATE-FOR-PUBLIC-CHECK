"""
Iteration 190 — SMS template auto-upgrade for unedited templates.

Verifies the new versioning behaviour added to `_ensure_templates`:
  - A legacy template doc whose body matches a known stale `LEGACY_DEFAULT_BODIES`
    entry gets refreshed to the latest factory wording.
  - A user-customized template (body != stored default_body) is NEVER clobbered.
  - Templates with `default_body == body` get upgraded when the factory wording
    changes; their `default_body` is re-anchored to the new value.
"""
import pytest
import requests

from _org_test_helpers import API, _db, ensure_org_admin_token


@pytest.fixture(scope="module")
def auth():
    token, user = ensure_org_admin_token()
    return token, user


def _backfill(token):
    r = requests.post(
        f"{API}/sms/templates/backfill",
        headers={"Authorization": f"Bearer {token}"}, timeout=15,
    )
    assert r.status_code == 200, r.text
    return r.json()


def _get_template(db, org_id, key):
    return db.sms_templates.find_one(
        {"key": key, "organization_id": org_id}, {"_id": 0}
    )


def test_legacy_stale_template_auto_upgrades(auth):
    """A close_late_notice carrying the old 'BLOCKED' wording with no
    default_body field should be detected as legacy + stale, then refreshed
    to the current factory wording."""
    token, user = auth
    db = _db()
    org_id = user["organization_id"]

    # First make sure the template exists for this org
    _backfill(token)

    stale_body = (
        "AgriBooks: <branch_name> close is overdue. Sales are BLOCKED until "
        "you finalize <date>. Open Close Wizard now."
    )
    db.sms_templates.update_one(
        {"key": "close_late_notice", "organization_id": org_id},
        {"$set": {"body": stale_body}, "$unset": {"default_body": ""}},
    )

    body = _backfill(token)
    assert "close_late_notice" in body.get("upgraded_keys", []), body

    after = _get_template(db, org_id, "close_late_notice")
    assert "BLOCKED" not in after["body"]
    assert after["body"] == after["default_body"]


def test_customized_template_is_not_clobbered(auth):
    """If the user has edited a template body, the upgrader must leave both
    `body` alone. The customization signal is `body != default_body` — once
    that holds, we never touch the body again."""
    token, user = auth
    db = _db()
    org_id = user["organization_id"]

    # Anchor default_body to current factory, then simulate a user edit:
    # body diverges from default_body → "customized" signal active.
    from routes.sms import DEFAULT_TEMPLATES
    factory = next(t["body"] for t in DEFAULT_TEMPLATES if t["key"] == "close_late_notice")
    custom = "CUSTOM TEXT: <branch_name> please close <date> kaagad."
    db.sms_templates.update_one(
        {"key": "close_late_notice", "organization_id": org_id},
        {"$set": {"body": custom, "default_body": factory}},
    )

    body = _backfill(token)
    assert "close_late_notice" not in body.get("upgraded_keys", []), body

    after = _get_template(db, org_id, "close_late_notice")
    assert after["body"] == custom
    assert after["default_body"] == factory  # default_body anchor preserved

    # Reset to factory so subsequent test runs are clean
    db.sms_templates.update_one(
        {"key": "close_late_notice", "organization_id": org_id},
        {"$set": {"body": factory, "default_body": factory}},
    )


def test_idempotent_backfill_no_changes(auth):
    """Running backfill twice in a row should report 0 seeded + 0 upgraded
    on the second call."""
    token, _ = auth
    _backfill(token)  # first call may upgrade
    second = _backfill(token)
    assert second["seeded"] == 0
    assert second["upgraded"] == 0

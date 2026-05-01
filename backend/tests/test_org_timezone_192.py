"""
Iteration 192 — Organization timezone setting + scheduler integration.

Ensures:
  • GET /api/settings/timezone returns the current org TZ + curated choices.
  • PUT /api/settings/timezone persists a valid IANA zone on the org row AND
    mirrors it to settings.company_info.value.timezone.
  • Invalid IANA names are rejected with a 400.
  • close_reminder._resolve_org_timezone picks up the change without restart.
  • _local_now_in resolves different zones correctly (basic sanity).
"""
import pytest
import requests

from _org_test_helpers import API, _db, ensure_org_admin_token


@pytest.fixture(scope="module")
def auth():
    return ensure_org_admin_token()


@pytest.fixture(autouse=True)
def _restore_manila(auth):
    """Make sure every test starts/ends with Asia/Manila so we don't pollute
    the rest of the suite."""
    token, _ = auth
    yield
    requests.put(
        f"{API}/settings/timezone",
        headers={"Authorization": f"Bearer {token}"},
        json={"timezone": "Asia/Manila"},
        timeout=10,
    )


def test_get_returns_default_and_choices(auth):
    token, _ = auth
    r = requests.get(f"{API}/settings/timezone",
                     headers={"Authorization": f"Bearer {token}"}, timeout=10)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "timezone" in body
    assert body["timezone"]  # non-empty default (Asia/Manila or whatever persisted)
    assert isinstance(body["choices"], list)
    assert "Asia/Manila" in body["choices"]
    assert "America/New_York" in body["choices"]
    assert "Africa/Nairobi" in body["choices"]


def test_put_persists_valid_tz(auth):
    token, user = auth
    db = _db()
    org_id = user["organization_id"]
    r = requests.put(
        f"{API}/settings/timezone",
        headers={"Authorization": f"Bearer {token}"},
        json={"timezone": "America/New_York"},
        timeout=10,
    )
    assert r.status_code == 200, r.text
    assert r.json()["timezone"] == "America/New_York"

    # Stored on the canonical organizations row
    org = db.organizations.find_one({"id": org_id}, {"_id": 0, "timezone": 1})
    assert org["timezone"] == "America/New_York"

    # Mirrored on settings.company_info for legacy readers
    ci = db.settings.find_one({"key": "company_info", "organization_id": org_id},
                              {"_id": 0, "value": 1})
    assert (ci or {}).get("value", {}).get("timezone") == "America/New_York"

    # And GET echoes it back
    g = requests.get(f"{API}/settings/timezone",
                     headers={"Authorization": f"Bearer {token}"}, timeout=10)
    assert g.json()["timezone"] == "America/New_York"


def test_put_rejects_invalid_tz(auth):
    token, _ = auth
    r = requests.put(
        f"{API}/settings/timezone",
        headers={"Authorization": f"Bearer {token}"},
        json={"timezone": "Mars/Olympus_Mons"},
        timeout=10,
    )
    assert r.status_code == 400, r.text
    assert "Unknown timezone" in r.text


def test_put_rejects_empty(auth):
    token, _ = auth
    r = requests.put(
        f"{API}/settings/timezone",
        headers={"Authorization": f"Bearer {token}"},
        json={"timezone": "   "},
        timeout=10,
    )
    assert r.status_code == 400, r.text


def test_scheduler_resolver_sees_change(auth):
    """Changing the TZ via the API must immediately be visible to
    _resolve_org_timezone (scheduler runs outside HTTP context so it reads
    directly from Mongo)."""
    import asyncio
    from routes.close_reminder import _resolve_org_timezone, _local_now_in

    token, user = auth
    org_id = user["organization_id"]

    # Flip to Africa/Nairobi via the API
    r = requests.put(
        f"{API}/settings/timezone",
        headers={"Authorization": f"Bearer {token}"},
        json={"timezone": "Africa/Nairobi"},
        timeout=10,
    )
    assert r.status_code == 200

    tz = asyncio.run(_resolve_org_timezone(org_id))
    assert tz == "Africa/Nairobi"

    # And _local_now_in can evaluate that zone
    local = _local_now_in(tz)
    assert local.tzinfo is not None
    # Africa/Nairobi is UTC+3, always — no DST
    assert str(local.utcoffset()) == "3:00:00"

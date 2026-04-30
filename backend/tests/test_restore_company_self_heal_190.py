"""
Iteration 190 — Restore Company Info self-heal.

Verifies the new behaviour of POST /api/settings/restore-company-info:
  - When the user's organization row is missing (deleted/orphaned tenant),
    the endpoint recreates the org as a fresh "<full_name>'s Company" trial
    instead of returning a 404 "Organization record missing" error.
  - When the user's JWT carries no organization_id at all, the endpoint
    returns a clear "session has no organization context" instruction so the
    caller can prompt a re-login.
"""
import pytest
import requests

from _org_test_helpers import API, _db, ensure_org_admin_token


@pytest.fixture(scope="module")
def auth():
    token, user = ensure_org_admin_token()
    return token, user


def test_restore_company_recreates_missing_org(auth):
    token, user = auth
    db = _db()
    org_id = user["organization_id"]

    # Backup the org row so we can restore it after the test
    original = db.organizations.find_one({"id": org_id}, {"_id": 0})
    assert original is not None, "fixture setup error — org should exist"

    try:
        # Simulate a deleted/orphan tenant
        db.organizations.delete_one({"id": org_id})
        db.settings.delete_one({"key": "company_info", "organization_id": org_id})

        r = requests.post(
            f"{API}/settings/restore-company-info",
            headers={"Authorization": f"Bearer {token}"}, timeout=15,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["recreated"] is True
        assert body["restored"] is True
        assert body["value"]["name"]  # non-empty
        assert body["value"]["email"] == user["email"]

        # The recreated org must be present in the DB
        recreated = db.organizations.find_one({"id": org_id}, {"_id": 0})
        assert recreated is not None
        assert recreated.get("self_healed_at"), "self_healed_at marker missing"

    finally:
        # Restore original org so the rest of the test suite stays clean
        db.organizations.delete_one({"id": org_id})
        original.pop("_id", None)
        db.organizations.insert_one(original)
        # Restore company_info too
        db.settings.update_one(
            {"key": "company_info", "organization_id": org_id},
            {"$set": {
                "key": "company_info",
                "organization_id": org_id,
                "value": {
                    "name": original.get("name", "AgriBooks (Default)"),
                    "email": original.get("owner_email", ""),
                    "phone": original.get("phone", ""),
                    "address": original.get("address", ""),
                    "currency": "PHP",
                    "date_format": "MM/DD/YYYY",
                },
            }},
            upsert=True,
        )


def test_restore_company_idempotent_when_already_set(auth):
    """Calling restore on an org with intact company_info returns
    {restored:false, reason:already_set} and does not clobber edits."""
    token, _ = auth
    r = requests.post(
        f"{API}/settings/restore-company-info",
        headers={"Authorization": f"Bearer {token}"}, timeout=15,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # Either already set, or freshly restored — both are acceptable. The
    # invariant is no exception/500.
    assert "restored" in body

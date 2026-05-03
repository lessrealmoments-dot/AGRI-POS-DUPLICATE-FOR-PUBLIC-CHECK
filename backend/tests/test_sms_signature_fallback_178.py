"""
Iteration 178 — SMS signature company-name resolver fallback.

Live RCA (agri-books.com):
  User receives messages with signature "- MAIN BRANCH" only — company name
  missing. Cause: their `settings.company_info` doc is gone (post-Reset, before
  any Settings page save). All four send paths in routes/sms.py read the
  settings doc directly and silently fell to empty when missing.

Fix: introduced `_resolve_company_name()` helper that reads
  `settings.company_info.value.name` first, then falls back to the immutable
  `organizations.name` row (own-tenant only — no cross-org bleed). All four
  send paths in sms.py now use it.

This test exercises the helper-equivalent logic via real HTTP — push a
manual SMS through `/sms/send` to a pending queue, then verify the queued
message body ends with the org's company name in the signature.
"""
import os
import sys
import uuid
import requests
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "test_database")
API = os.environ.get(
    "API_URL", "https://multi-branch-pos-14.preview.emergentagent.com"
).rstrip("/") + "/api"
EMAIL = os.environ.get("TEST_EMAIL", "janmarkeahig@gmail.com")
PASSWORD = os.environ.get("TEST_PASSWORD", "Aa@58798546521325")


def _db():
    return MongoClient(MONGO_URL)[DB_NAME]


def _login():
    """Use a real org admin (super admin can no longer touch tenant data after
    the privacy fix)."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _org_test_helpers import ensure_org_admin_token
    token, user = ensure_org_admin_token()
    return token, user


def test_signature_falls_back_to_organizations_name_when_settings_missing():
    """
    Force the user's organization_id to have NO company_info in settings.
    Send via /sms/send. Verify the queued message ends with the org's name
    pulled from the organizations row — never empty, never another tenant's.
    """
    token, user = _login()
    org_id = user.get("organization_id")
    if not org_id:
        # Super admin with no org context — can't easily simulate. Still run
        # a smoke test that send works and signature contains a "- " line.
        r = requests.post(
            f"{API}/sms/send",
            headers={"Authorization": f"Bearer {token}"},
            json={"phone": "09000000000",
                  "message": f"Signature smoke {uuid.uuid4()}"},
            timeout=15,
        )
        assert r.status_code == 200, r.text
        sid = r.json()["id"]
        db = _db()
        doc = db.sms_queue.find_one({"id": sid}, {"_id": 0})
        body = doc["message"]
        assert "\n\n-" in body, (
            f"signature marker missing — got: {body!r}"
        )
        print(f"PASS · super-admin smoke: signature marker present in queued msg")
        return

    db = _db()
    org_doc = db.organizations.find_one({"id": org_id}, {"_id": 0, "name": 1})
    org_name = org_doc.get("name", "") if org_doc else ""
    assert org_name, f"organizations row for {org_id} missing 'name'"

    # Snapshot + clear the company_info setting for this org
    saved = db.settings.find_one(
        {"key": "company_info", "organization_id": org_id}, {"_id": 0}
    )
    db.settings.delete_one(
        {"key": "company_info", "organization_id": org_id}
    )

    try:
        unique = f"sigtest-{uuid.uuid4()}"
        r = requests.post(
            f"{API}/sms/send",
            headers={"Authorization": f"Bearer {token}"},
            json={"phone": "09000000000",
                  "message": f"{unique} body content here"},
            timeout=15,
        )
        assert r.status_code == 200, r.text
        sid = r.json()["id"]

        doc = db.sms_queue.find_one({"id": sid}, {"_id": 0})
        body = doc.get("message", "")
        # The signature MUST contain the org's name (fallback worked)
        assert org_name in body, (
            f"company name '{org_name}' missing from signature — body was: {body!r}"
        )
        # And must NOT contain a different tenant's name (no cross-org bleed)
        for other in ("Jnd store", "JND store", "AgriBooks (Default)", "LimitTest Corp"):
            if other != org_name:
                assert other not in body, (
                    f"CROSS-ORG BLEED: '{other}' appeared in signature for org "
                    f"'{org_name}'. Full body: {body!r}"
                )
        print(
            f"PASS · signature fell back to organizations.name "
            f"({org_name!r}) when settings.company_info missing"
        )

        # Cleanup — remove the test SMS we just queued
        db.sms_queue.delete_one({"id": sid})

    finally:
        # Restore the settings doc so we don't leave the user broken
        if saved:
            saved.pop("_id", None)
            db.settings.insert_one(saved)


if __name__ == "__main__":
    test_signature_falls_back_to_organizations_name_when_settings_missing()
    print("\nIteration 178 SMS signature fallback test passed.")

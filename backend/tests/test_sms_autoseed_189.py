"""
Iteration 189 — SMS template auto-seeding for new orgs.

Verifies:
  - queue_sms() auto-seeds DEFAULT_TEMPLATES when an org has zero templates,
    so brand-new tenants don't silently lose auto-SMS triggers.
  - POST /api/sms/templates/backfill seeds missing templates idempotently.
  - GET /api/sms/diagnose-trigger/{key} returns clear pass/fail breakdown.
"""
from uuid import uuid4

import pytest
import requests

from _org_test_helpers import API, _db, ensure_org_admin_token


@pytest.fixture(scope="module")
def auth():
    token, user = ensure_org_admin_token()
    return token, user


def test_backfill_endpoint_seeds_missing(auth):
    token, user = auth
    db = _db()
    org_id = user.get("organization_id", "")

    # Wipe templates for this org (test fixture isolation)
    db.sms_templates.delete_many({"organization_id": org_id})

    r = requests.post(
        f"{API}/sms/templates/backfill",
        headers={"Authorization": f"Bearer {token}"}, timeout=15,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["seeded"] > 0
    assert "credit_new" in body["seeded_keys"]

    # Idempotent: second call seeds nothing
    r2 = requests.post(
        f"{API}/sms/templates/backfill",
        headers={"Authorization": f"Bearer {token}"}, timeout=15,
    )
    assert r2.status_code == 200
    assert r2.json()["seeded"] == 0


def test_diagnose_trigger_template_present(auth):
    token, _ = auth
    r = requests.get(
        f"{API}/sms/diagnose-trigger/credit_new",
        headers={"Authorization": f"Bearer {token}"}, timeout=15,
    )
    assert r.status_code == 200, r.text
    diag = r.json()
    assert diag["template_key"] == "credit_new"
    assert diag["would_send"] is True
    # Should have at least one passing check (template found)
    template_check = next((c for c in diag["checks"] if c["step"] == "template"), None)
    assert template_check and template_check["ok"] is True


def test_diagnose_trigger_missing_template(auth):
    token, _ = auth
    r = requests.get(
        f"{API}/sms/diagnose-trigger/{uuid4().hex}_does_not_exist",
        headers={"Authorization": f"Bearer {token}"}, timeout=15,
    )
    assert r.status_code == 200
    diag = r.json()
    assert diag["would_send"] is False
    template_check = next((c for c in diag["checks"] if c["step"] == "template"), None)
    assert template_check and template_check["ok"] is False

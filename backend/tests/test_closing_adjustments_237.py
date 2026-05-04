"""
Regression tests for admin-only reconciliation adjustments on closed days
(Iter 237).

Scenario that prompted the feature: a branch was migrated from a legacy POS
and the first day's sync double-counted AR carry-over, giving an on-paper
Over/Short of -P145,608 when the physical drawer was actually +P1,746 over.
Rather than mutate the historical closing (Expected/Actual must remain the
raw-as-recorded figures for auditability), admins apply a signed
`daily_closing_adjustments` entry that shifts the effective Over/Short. The
original closing values are never touched.
"""
import os
import requests
import pytest

from tests._org_test_helpers import ensure_org_admin_token, _db, API


@pytest.fixture
def closing_fixture():
    """Seed a closed day matching the real migration-pollution case."""
    token, user = ensure_org_admin_token()
    org_id = user["organization_id"]
    db = _db()
    branch = db.branches.find_one({"organization_id": org_id}, {"_id": 0, "id": 1})
    assert branch, "no branch in test org"
    closing_id = "pytest-adj-closing-237"
    doc = {
        "id": closing_id,
        "organization_id": org_id,
        "branch_id": branch["id"],
        "date": "2025-10-15",
        "status": "closed",
        "expected_counter": 62965.0,
        "actual_cash": 156431.40,
        "over_short": -145608.40,
        "closed_by_name": "Test Closer",
        "cash_to_drawer": 5000,
        "cash_to_safe": 151431.40,
    }
    db.daily_closings.replace_one({"id": closing_id}, doc, upsert=True)
    db.daily_closing_adjustments.delete_many({"closing_id": closing_id})
    db.audit_log.delete_many({"entity_id": closing_id})
    yield {"token": token, "user": user, "org_id": org_id,
           "branch_id": branch["id"], "closing_id": closing_id,
           "date": "2025-10-15"}
    db.daily_closings.delete_one({"id": closing_id})
    db.daily_closing_adjustments.delete_many({"closing_id": closing_id})
    db.audit_log.delete_many({"entity_id": closing_id})


def _h(tok):
    return {"Authorization": f"Bearer {tok}"}


def test_admin_create_adjustment_applies_override_without_mutating_original(closing_fixture):
    ctx = closing_fixture
    tok = ctx["token"]

    # Before: adjusted == original
    r = requests.get(f"{API}/daily-close/{ctx['date']}",
                     params={"branch_id": ctx["branch_id"]}, headers=_h(tok), timeout=15)
    assert r.status_code == 200
    body = r.json()
    assert body["over_short"] == -145608.40
    assert body["adjustment_total"] == 0
    assert body["adjusted_over_short"] == -145608.40

    # Create: shift by +147,354.40 → corrected Over/Short should be +1,746.00
    r = requests.post(
        f"{API}/daily-closings/{ctx['closing_id']}/adjustments",
        headers=_h(tok),
        json={"amount": 147354.40,
              "reason": "Pre-migration AR double-counted in initial sync"},
        timeout=15,
    )
    assert r.status_code == 200, r.text
    adj = r.json()
    assert adj["amount"] == 147354.40
    assert adj["voided"] is False
    assert adj["original_over_short"] == -145608.40
    assert adj["created_by_name"]

    # After: adjusted_over_short reflects the correction; original untouched
    r = requests.get(f"{API}/daily-close/{ctx['date']}",
                     params={"branch_id": ctx["branch_id"]}, headers=_h(tok), timeout=15)
    body = r.json()
    assert body["over_short"] == -145608.40
    assert body["adjustment_total"] == 147354.40
    assert body["adjusted_over_short"] == 1746.0
    assert len(body["adjustments"]) == 1

    log = _db().audit_log.find_one(
        {"type": "closing_adjustment_created", "entity_id": ctx["closing_id"]},
        {"_id": 0},
    )
    assert log is not None
    assert log["metadata"]["new_adjusted_over_short"] == 1746.0


def test_variance_history_attaches_adjusted_values(closing_fixture):
    ctx = closing_fixture
    tok = ctx["token"]

    # Two partial adjustments — aggregates should sum
    requests.post(f"{API}/daily-closings/{ctx['closing_id']}/adjustments",
                  headers=_h(tok),
                  json={"amount": 100000, "reason": "first correction pass"},
                  timeout=15)
    requests.post(f"{API}/daily-closings/{ctx['closing_id']}/adjustments",
                  headers=_h(tok),
                  json={"amount": 47354.40, "reason": "second correction pass"},
                  timeout=15)

    r = requests.get(f"{API}/daily-variance-history",
                     params={"branch_id": ctx["branch_id"], "limit": 120},
                     headers=_h(tok), timeout=15)
    assert r.status_code == 200
    records = r.json()["records"]
    ours = next((rec for rec in records if rec["id"] == ctx["closing_id"]), None)
    assert ours, "seeded closing not found in variance history"
    assert ours["over_short"] == -145608.40         # raw untouched
    assert ours["adjustment_count"] == 2
    assert ours["adjustment_total"] == 147354.40
    assert ours["adjusted_over_short"] == 1746.0


def test_non_admin_cannot_create_adjustment(closing_fixture):
    ctx = closing_fixture
    # Manager login
    mgr = requests.post(f"{API}/auth/login", json={
        "email": "test_org_manager@regression.local",
        "password": "RegressionMgrPass!2026",
    }, timeout=15)
    assert mgr.status_code == 200, mgr.text
    mgr_tok = mgr.json()["token"]

    r = requests.post(
        f"{API}/daily-closings/{ctx['closing_id']}/adjustments",
        headers=_h(mgr_tok),
        json={"amount": 100, "reason": "not allowed"},
        timeout=15,
    )
    assert r.status_code == 403
    assert "admin" in r.text.lower()


def test_reason_required_and_amount_non_zero(closing_fixture):
    ctx = closing_fixture
    tok = ctx["token"]
    r = requests.post(
        f"{API}/daily-closings/{ctx['closing_id']}/adjustments",
        headers=_h(tok),
        json={"amount": 100, "reason": "x"},
        timeout=15,
    )
    assert r.status_code == 400
    assert "reason" in r.text.lower()

    r = requests.post(
        f"{API}/daily-closings/{ctx['closing_id']}/adjustments",
        headers=_h(tok),
        json={"amount": 0, "reason": "valid reason string"},
        timeout=15,
    )
    assert r.status_code == 400
    assert "zero" in r.text.lower() or "non-zero" in r.text.lower()


def test_void_excludes_from_totals_keeps_history_and_audits(closing_fixture):
    ctx = closing_fixture
    tok = ctx["token"]
    r = requests.post(
        f"{API}/daily-closings/{ctx['closing_id']}/adjustments",
        headers=_h(tok),
        json={"amount": 147354.40, "reason": "will be voided"},
        timeout=15,
    )
    adj_id = r.json()["id"]

    r = requests.post(
        f"{API}/daily-closings/{ctx['closing_id']}/adjustments/{adj_id}/void",
        headers=_h(tok),
        json={"reason": "entered wrong amount - superseded"},
        timeout=15,
    )
    assert r.status_code == 200
    assert r.json() == {"voided": True}

    # get_daily_close returns only non-voided adjustments
    r = requests.get(f"{API}/daily-close/{ctx['date']}",
                     params={"branch_id": ctx["branch_id"]}, headers=_h(tok), timeout=15)
    body = r.json()
    assert body["adjustment_total"] == 0
    assert body["adjusted_over_short"] == -145608.40
    assert len(body["adjustments"]) == 0

    # /adjustments list returns full history including voided
    r = requests.get(f"{API}/daily-closings/{ctx['closing_id']}/adjustments",
                     headers=_h(tok), timeout=15)
    assert r.status_code == 200
    all_adj = r.json()["adjustments"]
    assert len(all_adj) == 1
    assert all_adj[0]["voided"] is True
    assert "entered wrong" in all_adj[0]["voided_reason"]

    log = _db().audit_log.find_one(
        {"type": "closing_adjustment_voided", "entity_id": ctx["closing_id"]},
        {"_id": 0},
    )
    assert log is not None

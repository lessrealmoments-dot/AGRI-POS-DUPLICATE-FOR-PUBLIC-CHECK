"""
Test the Manager-Initiated Branch Transfer with Admin Approval workflow (Iter 215).

Covers:
- Manager creates a transfer with requires_approval=true → status = 'pending_approval'
- Admin approves, sets retail prices → status flips to 'sent', items carry admin's retail
- Admin rejects with reason ≥ 4 chars → status = 'returned', rejection_reason persisted
- Reject with short reason → 400
- Approve a non-pending transfer → 400
- Manager resubmit on a 'returned' transfer → status back to 'pending_approval'
"""
import os
import requests

API_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001") + "/api"
ADMIN_EMAIL = "test_org_admin@regression.local"
ADMIN_PASS = "RegressionPass!2026"


def _login(email, password):
    r = requests.post(f"{API_URL}/auth/login", json={"email": email, "password": password})
    r.raise_for_status()
    return r.json()["token"]


def _hdr(tok):
    return {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}


def _two_branches(tok):
    r = requests.get(f"{API_URL}/branches", headers=_hdr(tok))
    r.raise_for_status()
    bs = r.json()
    assert len(bs) >= 2, "need at least 2 branches in regression org"
    return bs[0]["id"], bs[1]["id"]


def _create(tok, b1, b2, requires_approval=False):
    payload = {
        "from_branch_id": b1,
        "to_branch_id": b2,
        "min_margin": 20,
        "requires_approval": requires_approval,
        "items": [{
            "product_id": "test-prod",
            "product_name": "Test",
            "sku": "TST",
            "qty": 5,
            "branch_capital": 100,
            "transfer_capital": 100,
            "branch_retail": 0 if requires_approval else 150,
        }],
    }
    r = requests.post(f"{API_URL}/branch-transfers", json=payload, headers=_hdr(tok))
    r.raise_for_status()
    return r.json()


def test_pending_approval_status_set_when_requires_approval_true():
    tok = _login(ADMIN_EMAIL, ADMIN_PASS)
    b1, b2 = _two_branches(tok)
    t = _create(tok, b1, b2, requires_approval=True)
    assert t["status"] == "pending_approval"


def test_create_without_requires_approval_is_draft():
    tok = _login(ADMIN_EMAIL, ADMIN_PASS)
    b1, b2 = _two_branches(tok)
    t = _create(tok, b1, b2, requires_approval=False)
    assert t["status"] == "draft"


def test_admin_approve_dispatches_with_retail():
    tok = _login(ADMIN_EMAIL, ADMIN_PASS)
    b1, b2 = _two_branches(tok)
    t = _create(tok, b1, b2, requires_approval=True)
    # Approve with retail
    r = requests.post(
        f"{API_URL}/branch-transfers/{t['id']}/approve",
        headers=_hdr(tok),
        json={"items": [{"product_id": "test-prod", "branch_retail": 175}], "notes": "ok"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "sent"
    # Verify persisted
    g = requests.get(f"{API_URL}/branch-transfers/{t['id']}", headers=_hdr(tok)).json()
    assert g["status"] == "sent"
    assert any(it.get("branch_retail") == 175 for it in g["items"])
    assert g.get("approved_by_name")
    assert g.get("approval_note") == "ok"


def test_admin_approve_on_non_pending_returns_400():
    tok = _login(ADMIN_EMAIL, ADMIN_PASS)
    b1, b2 = _two_branches(tok)
    t = _create(tok, b1, b2, requires_approval=False)  # draft
    r = requests.post(
        f"{API_URL}/branch-transfers/{t['id']}/approve",
        headers=_hdr(tok), json={"items": []},
    )
    assert r.status_code == 400


def test_admin_reject_requires_reason_min_4_chars():
    tok = _login(ADMIN_EMAIL, ADMIN_PASS)
    b1, b2 = _two_branches(tok)
    t = _create(tok, b1, b2, requires_approval=True)
    bad = requests.post(
        f"{API_URL}/branch-transfers/{t['id']}/reject",
        headers=_hdr(tok), json={"reason": "no"},
    )
    assert bad.status_code == 400


def test_admin_reject_marks_returned_and_persists_reason():
    tok = _login(ADMIN_EMAIL, ADMIN_PASS)
    b1, b2 = _two_branches(tok)
    t = _create(tok, b1, b2, requires_approval=True)
    reason = "transfer capital is too low for current market"
    r = requests.post(
        f"{API_URL}/branch-transfers/{t['id']}/reject",
        headers=_hdr(tok), json={"reason": reason},
    )
    assert r.status_code == 200
    g = requests.get(f"{API_URL}/branch-transfers/{t['id']}", headers=_hdr(tok)).json()
    assert g["status"] == "returned"
    assert g["rejection_reason"] == reason
    assert g.get("rejected_by_name")


def test_manager_can_resubmit_returned():
    tok = _login(ADMIN_EMAIL, ADMIN_PASS)
    b1, b2 = _two_branches(tok)
    t = _create(tok, b1, b2, requires_approval=True)
    # Reject
    requests.post(f"{API_URL}/branch-transfers/{t['id']}/reject",
                  headers=_hdr(tok), json={"reason": "fix retail"})
    # Resubmit
    r = requests.post(f"{API_URL}/branch-transfers/{t['id']}/resubmit", headers=_hdr(tok))
    assert r.status_code == 200
    assert r.json()["status"] == "pending_approval"
    g = requests.get(f"{API_URL}/branch-transfers/{t['id']}", headers=_hdr(tok)).json()
    assert g["status"] == "pending_approval"
    # rejection metadata cleared
    assert not g.get("rejection_reason")


def test_resubmit_only_allowed_on_returned():
    tok = _login(ADMIN_EMAIL, ADMIN_PASS)
    b1, b2 = _two_branches(tok)
    t = _create(tok, b1, b2, requires_approval=True)  # pending
    r = requests.post(f"{API_URL}/branch-transfers/{t['id']}/resubmit", headers=_hdr(tok))
    assert r.status_code == 400


def test_list_filter_by_pending_approval():
    tok = _login(ADMIN_EMAIL, ADMIN_PASS)
    b1, b2 = _two_branches(tok)
    _create(tok, b1, b2, requires_approval=True)
    r = requests.get(f"{API_URL}/branch-transfers?status=pending_approval", headers=_hdr(tok))
    assert r.status_code == 200
    data = r.json()
    assert all(o["status"] == "pending_approval" for o in data["orders"])
    assert data["total"] >= 1

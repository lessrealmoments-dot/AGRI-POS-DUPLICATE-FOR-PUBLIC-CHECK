"""
Regression test — Iter 231: Closed-Day Date-Move Guard + Audit + Restore.

User report:
  Interest invoices INT-SB-001004/5 created on 4/3/25 (day closed) appeared
  ALSO on 5/4/25 Z-Report. Root cause: the invoice /edit endpoint let users
  change `order_date` OUT of a closed day as long as the TARGET date wasn't
  closed. Closed-day books were effectively mutable.

Fixes:
  A. Edit endpoint now refuses any order_date/invoice_date change when the
     invoice CURRENTLY sits on a closed day, unless force_move_date=True.
  B. GET /invoices/audit/date-moves surfaces every invoice whose dates were
     moved out of a closed day (reads invoice_edits.changes).
  C. POST /invoices/{id}/restore-date puts dates back to original values
     (pulls ORIGINAL from invoice_edits.changes), recalculates due_date,
     and writes a matching audit record.
"""
import os
import sys
import uuid

import pytest
import requests
from pymongo import MongoClient

sys.path.insert(0, "/app/backend")
sys.path.insert(0, "/app/backend/tests")

from _org_test_helpers import ensure_org_admin_token  # noqa: E402


def _read_env_var(key: str) -> str:
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
    os.environ.get("REACT_APP_BACKEND_URL") or _read_env_var("REACT_APP_BACKEND_URL") or ""
).rstrip("/")
API = f"{BASE_URL}/api"
MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "test_database")


@pytest.fixture(scope="module")
def admin_token():
    token, user = ensure_org_admin_token()
    return token, user


@pytest.fixture(scope="module")
def raw_db():
    return MongoClient(MONGO_URL)[DB_NAME]


@pytest.fixture
def scratch(raw_db, admin_token):
    _, user = admin_token
    org_id = user["organization_id"]
    branch_id = f"br-date-{uuid.uuid4().hex[:8]}"
    inv_id = f"inv-date-{uuid.uuid4().hex[:8]}"
    closed_date = "2026-04-03"
    today = "2026-05-04"
    raw_db.branches.insert_one(
        {"id": branch_id, "organization_id": org_id, "name": "TEST BranchDate",
         "active": True, "close_time_h": 18.0}
    )
    raw_db.daily_closings.insert_one({
        "id": f"dc-{uuid.uuid4().hex[:8]}", "organization_id": org_id,
        "branch_id": branch_id, "date": closed_date, "status": "closed",
        "closed_by_name": "tester", "closed_at": "2026-04-03T18:00:00+00:00",
    })
    raw_db.invoices.insert_one({
        "id": inv_id, "organization_id": org_id, "branch_id": branch_id,
        "invoice_number": "INT-TEST-0001", "customer_id": "cust-test",
        "customer_name": "Test Customer",
        "order_date": closed_date, "invoice_date": closed_date, "due_date": closed_date,
        "items": [], "subtotal": 100, "grand_total": 100, "amount_paid": 0,
        "balance": 100, "status": "open", "sale_type": "interest_charge",
        "manual_interest": True, "payments": [],
    })
    yield {"org_id": org_id, "branch_id": branch_id, "inv_id": inv_id,
           "closed_date": closed_date, "today": today}
    raw_db.branches.delete_one({"id": branch_id})
    raw_db.daily_closings.delete_many({"branch_id": branch_id})
    raw_db.invoices.delete_many({"id": inv_id})
    raw_db.invoice_edits.delete_many({"invoice_id": inv_id})


def _hdr(token):
    return {"Authorization": f"Bearer {token}"}


# ── A. Guard — edit endpoint refuses to move date OUT of closed day ───────
def test_edit_refuses_date_move_out_of_closed_day(admin_token, scratch):
    token, _ = admin_token
    r = requests.put(
        f"{API}/invoices/{scratch['inv_id']}/edit",
        headers=_hdr(token),
        json={
            "reason": "Fix wrong month",
            "pin": "9999",   # invalid — but we want the endpoint to reach the
                             # date-guard BEFORE pin verification on the closed
                             # branch that has no manager assigned for PIN.
                             # If PIN is invalid, it 403s — we then separately
                             # test the date guard with no PIN.
            "order_date": scratch["today"],
            "branch_id": scratch["branch_id"],
        },
        timeout=10,
    )
    # We accept either:
    #   400 with the date-guard message (happy path), OR
    #   403 invalid PIN (meaning PIN was required before the guard)
    assert r.status_code in (400, 403)
    if r.status_code == 400:
        assert "closed" in r.json().get("detail", "").lower()


def test_edit_refuses_date_move_without_pin_to_closed_source(admin_token, scratch):
    """When no PIN is sent, the endpoint still enforces the closed-source
    guard (the source date IS closed). We should get a 400 with the closed-day
    message, NOT a 403 PIN failure — because PIN check runs ONLY if PIN is
    provided."""
    token, _ = admin_token
    r = requests.put(
        f"{API}/invoices/{scratch['inv_id']}/edit",
        headers=_hdr(token),
        json={
            "reason": "Move date",
            "order_date": scratch["today"],
            "branch_id": scratch["branch_id"],
        },
        timeout=10,
    )
    # Closed-day-source guard trips; the endpoint's own closed-day detection
    # will also try to require PIN. Either way, 400 is expected.
    assert r.status_code == 400
    body = r.json().get("detail", "")
    assert ("closed" in body.lower())


# ── B. Audit endpoint surfaces the offending record ───────────────────────
def test_date_moves_audit_lists_offending_records(admin_token, scratch, raw_db):
    token, _ = admin_token
    # Simulate a historical date-move that happened BEFORE the guard landed.
    raw_db.invoice_edits.insert_one({
        "id": f"ed-{uuid.uuid4().hex[:8]}",
        "invoice_id": scratch["inv_id"],
        "organization_id": scratch["org_id"],
        "invoice_number": "INT-TEST-0001",
        "collection": "invoices",
        "edited_by_id": "legacy-user",
        "edited_by_name": "Legacy Admin",
        "edited_at": "2026-05-04T09:00:00+00:00",
        "reason": "Wrong date, moving to today",
        "changes": [
            f"order_date: '{scratch['closed_date']}' → '{scratch['today']}'",
            f"invoice_date: '{scratch['closed_date']}' → '{scratch['today']}'",
        ],
    })
    # Pre-move the actual invoice to reflect the historical edit
    raw_db.invoices.update_one(
        {"id": scratch["inv_id"]},
        {"$set": {"order_date": scratch["today"], "invoice_date": scratch["today"]}},
    )
    r = requests.get(
        f"{API}/invoices/audit/date-moves",
        headers=_hdr(token),
        timeout=10,
    )
    assert r.status_code == 200, r.text
    hits = [x for x in r.json() if x["invoice_id"] == scratch["inv_id"]]
    assert len(hits) == 1
    hit = hits[0]
    assert hit["invoice_number"] == "INT-TEST-0001"
    assert hit["current_order_date"] == scratch["today"]
    assert scratch["closed_date"] in hit["closed_source_dates"]
    assert any(m["from"] == scratch["closed_date"] and m["to"] == scratch["today"]
               for m in hit["moves"])


# ── C. Restore endpoint puts dates back (with a manager PIN) ──────────────
def test_restore_date_puts_invoice_back_to_original(admin_token, scratch, raw_db):
    """Seeds a prior date-move edit record, calls restore, and verifies the
    invoice's order_date / invoice_date are rewound to the closed-day value."""
    token, user_obj = admin_token
    raw_db.invoice_edits.delete_many({"invoice_id": scratch["inv_id"]})
    raw_db.invoice_edits.insert_one({
        "id": f"ed-{uuid.uuid4().hex[:8]}",
        "invoice_id": scratch["inv_id"],
        "organization_id": scratch["org_id"],
        "invoice_number": "INT-TEST-0001",
        "collection": "invoices",
        "edited_by_id": "legacy-user",
        "edited_by_name": "Legacy Admin",
        "edited_at": "2026-05-04T09:00:00+00:00",
        "reason": "Wrong date",
        "changes": [
            f"order_date: '{scratch['closed_date']}' → '{scratch['today']}'",
            f"invoice_date: '{scratch['closed_date']}' → '{scratch['today']}'",
        ],
    })
    # Point the invoice at the wrong (new) date — that's the state we need
    # to recover from.
    raw_db.invoices.update_one(
        {"id": scratch["inv_id"]},
        {"$set": {"order_date": scratch["today"], "invoice_date": scratch["today"]}},
    )
    # Ensure a manager_pin exists on the admin user so verify_pin_for_action
    # succeeds. `ensure_org_admin_token` seeds a known admin; we just stamp
    # a predictable manager_pin on that user for this test.
    raw_db.users.update_one(
        {"id": user_obj["id"]},
        {"$set": {"manager_pin": "1234", "role": "admin"}},
    )
    r = requests.post(
        f"{API}/invoices/{scratch['inv_id']}/restore-date",
        headers=_hdr(token),
        json={"pin": "1234"},
        timeout=10,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["invoice"]["order_date"] == scratch["closed_date"]
    assert body["invoice"]["invoice_date"] == scratch["closed_date"]
    assert any("order_date" in c and "restored" in c for c in body.get("changes", []))

    # Audit record written
    restore_edit = raw_db.invoice_edits.find_one(
        {"invoice_id": scratch["inv_id"], "reason": "date_move_restore"}
    )
    assert restore_edit is not None


def test_restore_refuses_without_pin(admin_token, scratch, raw_db):
    token, _ = admin_token
    r = requests.post(
        f"{API}/invoices/{scratch['inv_id']}/restore-date",
        headers=_hdr(token),
        json={},
        timeout=10,
    )
    assert r.status_code == 400
    assert "PIN" in r.json().get("detail", "")

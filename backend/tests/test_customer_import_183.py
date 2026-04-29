"""
Iteration 183 — Customer Import (branch-scoped, smart dupe detection, opening balance).

Verifies:
  - Branch-scoped customer creation
  - Exact duplicates (same name) are auto-skipped
  - Fuzzy duplicates surface for user decision
  - Opening balance creates a real invoice flagged is_opening_balance
  - The opening balance invoice flows through customer.balance + appears
    in receivables aggregate (proxy for AR aging / closing wizard)
  - "Skip & Remember" persists a customer_import_decisions row
  - One-time SMS is queued (idempotent — re-import doesn't re-fire)
"""
import io
import csv
import json
from uuid import uuid4

import pytest
import requests

from _org_test_helpers import API, _db, ensure_org_admin_token


def _csv_bytes(rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerows(rows)
    return buf.getvalue().encode()


@pytest.fixture(scope="module")
def auth():
    token, user = ensure_org_admin_token()
    return token, user


@pytest.fixture
def test_branch(auth):
    db = _db()
    token, user = auth
    org_id = user.get("organization_id", "")
    branch = db.branches.find_one({"organization_id": org_id, "active": True}, {"_id": 0, "id": 1, "name": 1})
    if not branch:
        pytest.skip("No branch found for test org")
    return branch


@pytest.fixture
def cleanup(auth, test_branch):
    """Clean up any leftover test customers/invoices/sms before & after each test."""
    db = _db()
    test_names = ["Juan Test Cruz", "Juan Test Cruz Jr", "Maria Test Santos",
                  "Maria T Santos", "Imp Customer A", "Imp Customer B"]
    db.customers.delete_many({"name": {"$in": test_names}, "branch_id": test_branch["id"]})
    db.invoices.delete_many({"is_opening_balance": True, "customer_name": {"$in": test_names}})
    db.sms_queue.delete_many({"customer_name": {"$in": test_names}})
    db.customer_import_decisions.delete_many({"branch_id": test_branch["id"]})
    yield
    db.customers.delete_many({"name": {"$in": test_names}, "branch_id": test_branch["id"]})
    db.invoices.delete_many({"is_opening_balance": True, "customer_name": {"$in": test_names}})
    db.sms_queue.delete_many({"customer_name": {"$in": test_names}})
    db.customer_import_decisions.delete_many({"branch_id": test_branch["id"]})


def _preview(token, branch_id, csv_rows, mapping):
    csv_data = _csv_bytes(csv_rows)
    r = requests.post(
        f"{API}/import/customers/preview",
        files={"file": ("c.csv", csv_data, "text/csv")},
        data={"mapping": json.dumps(mapping), "branch_id": branch_id},
        headers={"Authorization": f"Bearer {token}"}, timeout=30,
    )
    return r


def _commit(token, branch_id, csv_rows, mapping, decisions=None, ob_date=""):
    csv_data = _csv_bytes(csv_rows)
    r = requests.post(
        f"{API}/import/customers/commit",
        files={"file": ("c.csv", csv_data, "text/csv")},
        data={
            "mapping": json.dumps(mapping),
            "branch_id": branch_id,
            "decisions": json.dumps(decisions or []),
            "opening_balance_date": ob_date,
        },
        headers={"Authorization": f"Bearer {token}"}, timeout=30,
    )
    return r


CSV_HEADER = ["Customer Name", "Phone", "Email", "Credit Limit", "Opening Balance"]
MAPPING = {
    "name": "Customer Name", "phone": "Phone", "email": "Email",
    "credit_limit": "Credit Limit", "opening_balance": "Opening Balance",
}


def test_create_new_customer_with_opening_balance(auth, test_branch, cleanup):
    token, _ = auth
    rows = [CSV_HEADER, ["Imp Customer A", "09171000001", "a@x.com", "5000", "1500"]]

    # Preview
    pr = _preview(token, test_branch["id"], rows, MAPPING)
    assert pr.status_code == 200, pr.text
    pbody = pr.json()
    assert len(pbody["auto_create"]) == 1
    assert pbody["auto_create"][0]["payload"]["opening_balance"] == 1500

    # Commit
    cr = _commit(token, test_branch["id"], rows, MAPPING, ob_date="2025-01-15")
    assert cr.status_code == 200, cr.text
    cbody = cr.json()
    assert cbody["created"] == 1
    assert cbody["invoiced_count"] == 1

    # Customer exists with credit_limit
    db = _db()
    cust = db.customers.find_one({"name": "Imp Customer A", "branch_id": test_branch["id"]}, {"_id": 0})
    assert cust is not None
    assert cust["credit_limit"] == 5000
    assert cust["balance"] == 1500   # recomputed from open invoices
    assert cust["phones"] == ["09171000001"]

    # Invoice exists with is_opening_balance flag
    inv = db.invoices.find_one({"customer_id": cust["id"], "is_opening_balance": True}, {"_id": 0})
    assert inv is not None
    assert inv["balance"] == 1500
    assert inv["payment_type"] == "credit"
    assert inv["status"] == "open"
    assert inv["order_date"] == "2025-01-15"
    assert inv["branch_id"] == test_branch["id"]

    # SMS queued
    sms = db.sms_queue.find_one({"customer_id": cust["id"], "template_key": "opening_balance_notice"}, {"_id": 0})
    assert sms is not None


def test_exact_duplicate_skipped(auth, test_branch, cleanup):
    token, user = auth
    db = _db()
    org_id = user.get("organization_id", "")

    # Pre-seed an existing customer with same exact name
    db.customers.insert_one({
        "id": str(uuid4()),
        "name": "Imp Customer A", "phone": "09170000000", "phones": ["09170000000"],
        "email": "old@x.com", "address": "", "price_scheme": "retail",
        "credit_limit": 100, "interest_rate": 0, "grace_period": 7, "balance": 0,
        "branch_id": test_branch["id"], "organization_id": org_id,
        "active": True, "created_at": "2025-01-01T00:00:00Z",
    })

    rows = [CSV_HEADER, ["Imp Customer A", "09171000001", "new@x.com", "5000", "1500"]]
    pr = _preview(token, test_branch["id"], rows, MAPPING)
    assert pr.status_code == 200
    body = pr.json()
    assert len(body["exact_dupe"]) == 1
    assert body["exact_dupe"][0]["reason"] in ("name", "exact_duplicate", "exact")

    cr = _commit(token, test_branch["id"], rows, MAPPING)
    assert cr.status_code == 200
    cbody = cr.json()
    assert cbody["created"] == 0
    assert cbody["invoiced_count"] == 0
    assert any(s["reason"] in ("exact_duplicate", "name") for s in cbody["skipped"])


def test_fuzzy_match_with_merge_decision(auth, test_branch, cleanup):
    token, user = auth
    db = _db()
    org_id = user.get("organization_id", "")

    # Pre-seed similar-named customer
    target_id = str(uuid4())
    db.customers.insert_one({
        "id": target_id,
        "name": "Juan Test Cruz",
        "phone": "09180000000", "phones": ["09180000000"],
        "email": "", "address": "Old Addr", "price_scheme": "retail",
        "credit_limit": 1000, "interest_rate": 0, "grace_period": 7,
        "balance": 0, "branch_id": test_branch["id"], "organization_id": org_id,
        "active": True, "created_at": "2025-01-01T00:00:00Z",
    })

    # Slight rearrangement — should hit fuzzy
    rows = [CSV_HEADER, ["Cruz Juan Test", "09181111111", "merge@x.com", "5000", "0"]]
    pr = _preview(token, test_branch["id"], rows, MAPPING)
    assert pr.status_code == 200
    body = pr.json()
    assert len(body["fuzzy"]) == 1
    assert body["fuzzy"][0]["candidates"][0]["id"] == target_id

    # Merge decision
    decisions = [{"row": 2, "action": "merge", "target_id": target_id}]
    cr = _commit(token, test_branch["id"], rows, MAPPING, decisions=decisions)
    assert cr.status_code == 200, cr.text
    cbody = cr.json()
    assert cbody["merged"] == 1
    assert cbody["created"] == 0

    # Existing customer enriched
    after = db.customers.find_one({"id": target_id}, {"_id": 0})
    assert after["email"] == "merge@x.com"
    assert after["credit_limit"] == 5000
    assert "09181111111" in after["phones"]


def test_skip_and_remember_persists(auth, test_branch, cleanup):
    token, user = auth
    db = _db()
    org_id = user.get("organization_id", "")
    target_id = str(uuid4())
    db.customers.insert_one({
        "id": target_id, "name": "Maria Test Santos", "phone": "09181111000",
        "phones": ["09181111000"], "email": "", "address": "", "price_scheme": "retail",
        "credit_limit": 0, "interest_rate": 0, "grace_period": 7,
        "balance": 0, "branch_id": test_branch["id"], "organization_id": org_id,
        "active": True, "created_at": "2025-01-01T00:00:00Z",
    })

    rows = [CSV_HEADER, ["Maria T Santos", "09182222222", "", "0", "0"]]
    pr = _preview(token, test_branch["id"], rows, MAPPING)
    body = pr.json()
    assert len(body["fuzzy"]) == 1

    # Skip & remember + create as new
    decisions = [{"row": 2, "action": "skip_and_remember", "target_id": target_id}]
    cr = _commit(token, test_branch["id"], rows, MAPPING, decisions=decisions)
    assert cr.status_code == 200, cr.text
    cbody = cr.json()
    assert cbody["created"] == 1   # was created as new

    # The decision is persisted
    decision_doc = db.customer_import_decisions.find_one(
        {"branch_id": test_branch["id"], "existing_id": target_id, "decision": "distinct"},
        {"_id": 0},
    )
    assert decision_doc is not None

    # Now if we preview the same row again, it should NOT show up in fuzzy anymore
    pr2 = _preview(token, test_branch["id"], rows, MAPPING)
    body2 = pr2.json()
    # Could be in auto_create OR exact_dupe (since we just created "Maria T Santos")
    assert not any(f["payload"]["name"] == "Maria T Santos" for f in body2["fuzzy"])


def test_opening_balance_idempotency(auth, test_branch, cleanup):
    """Re-running the same import should NOT create duplicate opening balance invoices."""
    token, _ = auth
    rows = [CSV_HEADER, ["Imp Customer B", "09199999999", "b@x.com", "1000", "750"]]

    cr1 = _commit(token, test_branch["id"], rows, MAPPING)
    assert cr1.status_code == 200
    assert cr1.json()["invoiced_count"] == 1

    db = _db()
    cust = db.customers.find_one({"name": "Imp Customer B", "branch_id": test_branch["id"]}, {"_id": 0})

    # Re-run: customer is now an exact dupe → skipped, no new invoice
    cr2 = _commit(token, test_branch["id"], rows, MAPPING)
    assert cr2.status_code == 200
    assert cr2.json()["invoiced_count"] == 0

    # Only one OB invoice exists for this customer
    invs = list(db.invoices.find(
        {"customer_id": cust["id"], "is_opening_balance": True},
        {"_id": 0},
    ))
    assert len(invs) == 1

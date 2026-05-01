"""
Iteration 197 — Customer Dedupe Manager + Bulk Delete + No-Blocker Import

Contract this suite enforces:

  1. /api/import/customers/preview — every valid row now lands in auto_create;
     fuzzy[]/exact_dupe[] are always empty. Total opening-balance is reported.

  2. /api/import/customers/commit — every row becomes a NEW customer with an
     opening-balance invoice if OB > 0, even when a customer with the SAME
     name already exists. (The dedupe tool cleans up afterwards — this is
     exactly what protects the user from losing OB on re-imports.)

  3. GET /api/customers/-/duplicates — union-find clusters customers with
     similar names or shared phone tails. Pairs marked distinct are excluded.

  4. POST /api/customers/mark-distinct — all pairwise combos are persisted;
     subsequent scans don't re-flag them.

  5. POST /api/customers/merge — moves invoices, merges fields per
     "master-wins-if-present" rule, soft-deletes duplicates, writes audit.

  6. POST /api/customers/bulk-delete — PIN-gated; refuses customers with
     balance or open invoices unless force=true (admin/owner only).
"""
import os
import uuid
import requests
import pytest

from _org_test_helpers import (
    API, _db, ensure_org_admin_token, TEST_ORG_ADMIN_PIN,
)


@pytest.fixture(scope="module")
def auth():
    return ensure_org_admin_token()


@pytest.fixture
def branch(auth):
    """Ensure at least one branch exists in the test org, return its id."""
    _, user = auth
    db = _db()
    org_id = user["organization_id"]
    br = db.branches.find_one({"organization_id": org_id}, {"_id": 0, "id": 1})
    if not br:
        bid = str(uuid.uuid4())
        db.branches.insert_one({
            "id": bid,
            "name": "Dedupe Test Branch",
            "organization_id": org_id,
            "active": True,
        })
        return bid
    return br["id"]


def _auth_headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _make_customer(db, *, name, branch_id, org_id, phones=None, balance=0.0, email="", address=""):
    cid = str(uuid.uuid4())
    db.customers.insert_one({
        "id": cid,
        "name": name,
        "phone": (phones or [""])[0],
        "phones": phones or [],
        "email": email,
        "address": address,
        "price_scheme": "retail",
        "credit_limit": 0.0,
        "interest_rate": 0.0,
        "grace_period": 7,
        "balance": balance,
        "branch_id": branch_id,
        "organization_id": org_id,
        "active": True,
        "created_at": "2026-01-01T00:00:00+00:00",
    })
    return cid


def _make_invoice(db, *, customer_id, customer_name, branch_id, org_id,
                  total=1000.0, balance=1000.0, status="unpaid",
                  is_opening_balance=False):
    iid = str(uuid.uuid4())
    db.invoices.insert_one({
        "id": iid,
        "invoice_number": f"INV-{iid[:6].upper()}",
        "customer_id": customer_id,
        "customer_name": customer_name,
        "branch_id": branch_id,
        "organization_id": org_id,
        "total": total, "grand_total": total, "subtotal": total,
        "amount_paid": 0.0, "paid": 0.0, "balance": balance,
        "status": status,
        "items": [], "discount": 0.0, "tax": 0.0,
        "invoice_date": "2026-01-15", "due_date": "2026-01-22",
        "cashier_id": "test", "cashier_name": "test",
        "is_opening_balance": is_opening_balance,
        "created_at": "2026-01-15T00:00:00+00:00",
    })
    return iid


# ─────────────────────────────────────────────────────────────────────────────
#  1. Importer — no fuzzy/exact blocker; every row creates a new customer + OB
# ─────────────────────────────────────────────────────────────────────────────
def test_importer_always_creates_customer_and_ob_invoice(auth, branch):
    """
    Even when a customer with the same name already exists in the branch,
    re-importing creates a NEW customer record + an OB invoice. This is the
    core contract that fixes the previous bug (re-imports dropped OB silently).
    """
    token, user = auth
    db = _db()
    org_id = user["organization_id"]

    suffix = uuid.uuid4().hex[:6]
    existing_name = f"Juan Dela Cruz {suffix}"
    # Seed an existing customer with the same name
    _make_customer(db, name=existing_name, branch_id=branch, org_id=org_id, phones=["09181234567"])

    # CSV body with TWO rows both naming the SAME existing customer, each with OB
    csv_body = (
        "Customer Name,Phone,Opening Balance\n"
        f"{existing_name},09181234567,2000\n"
        f"{existing_name},09181234568,1000\n"
    )
    files = {"file": ("customers.csv", csv_body, "text/csv")}
    data = {
        "mapping": '{"name":"Customer Name","phone":"Phone","opening_balance":"Opening Balance"}',
        "branch_id": branch,
    }
    headers = {"Authorization": f"Bearer {token}"}

    # Preview — both rows should land in auto_create; fuzzy & exact_dupe empty
    r = requests.post(f"{API}/import/customers/preview", data=data, files=files, headers=headers, timeout=20)
    assert r.status_code == 200, r.text
    pv = r.json()
    assert pv["fuzzy"] == []
    assert pv["exact_dupe"] == []
    assert len(pv["auto_create"]) == 2
    assert abs(pv["total_opening_balance"] - 3000.0) < 0.01
    assert pv["existing_similar_count"] >= 1  # informational, non-blocking

    # Commit — both rows should create new customers + OB invoices
    files = {"file": ("customers.csv", csv_body, "text/csv")}
    r = requests.post(f"{API}/import/customers/commit", data=data, files=files, headers=headers, timeout=20)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] == 2
    assert body["invoiced_count"] == 2
    assert abs(body["ob_amount_total"] - 3000.0) < 0.01

    # Verify the new customers actually exist and have OB invoices
    new_cust_ids = [row["customer_id"] for row in body["invoiced"]]
    for cid in new_cust_ids:
        c = db.customers.find_one({"id": cid}, {"_id": 0})
        assert c and c["active"] is True
        assert c["name"] == existing_name
        # balance should match OB (only one invoice exists)
        assert c["balance"] > 0
        ob = db.invoices.find_one(
            {"customer_id": cid, "is_opening_balance": True},
            {"_id": 0},
        )
        assert ob is not None
        assert ob["balance"] > 0


# ─────────────────────────────────────────────────────────────────────────────
#  2. /customers/-/duplicates — union-find clusters similar names & phones
# ─────────────────────────────────────────────────────────────────────────────
def test_duplicates_endpoint_finds_clusters(auth, branch):
    """James Ahig / Ahig James / +09181234567 vs +63 9181234567 get clustered."""
    token, user = auth
    db = _db()
    org_id = user["organization_id"]

    suffix = uuid.uuid4().hex[:6]
    a = _make_customer(db, name=f"James Ahig {suffix}", branch_id=branch, org_id=org_id,
                       phones=["09181234567"], balance=0)
    b = _make_customer(db, name=f"Ahig James {suffix}", branch_id=branch, org_id=org_id,
                       phones=[], balance=0)
    # Unrelated customer — must NOT be in the cluster
    _make_customer(db, name=f"Maria Santos {suffix}", branch_id=branch, org_id=org_id,
                   phones=["09999999999"], balance=0)

    r = requests.get(
        f"{API}/customers/-/duplicates?branch_id={branch}",
        headers=_auth_headers(token), timeout=15,
    )
    assert r.status_code == 200, r.text
    payload = r.json()

    # The cluster containing our two seeded customers must be present.
    found = False
    for cluster in payload["clusters"]:
        ids = {m["id"] for m in cluster["members"]}
        if a in ids and b in ids:
            found = True
            assert cluster["member_count"] >= 2
            # Every member exposes the required fields
            for m in cluster["members"]:
                assert "name" in m and "balance" in m and "invoice_count" in m
            break
    assert found, f"Expected cluster containing {a} & {b}; got: {payload}"


# ─────────────────────────────────────────────────────────────────────────────
#  3. mark-distinct → cluster disappears from future scans
# ─────────────────────────────────────────────────────────────────────────────
def test_mark_distinct_removes_cluster_from_scan(auth, branch):
    token, user = auth
    db = _db()
    org_id = user["organization_id"]

    # Use a name unlikely to collide with other customers seeded by other tests.
    # We also clean up any prior "Zanthuria" seeds first so the union-find
    # doesn't transitively re-link a<->b via a 3rd match.
    suffix = uuid.uuid4().hex[:8]
    db.customers.update_many(
        {"branch_id": branch, "name": {"$regex": "Zanthuria"}},
        {"$set": {"active": False}},
    )
    db.customer_dedupe_decisions.delete_many({"branch_id": branch})

    a = _make_customer(db, name=f"Zanthuria Plutarch {suffix}", branch_id=branch, org_id=org_id)
    b = _make_customer(db, name=f"Plutarch Zanthuria {suffix}", branch_id=branch, org_id=org_id)

    # Confirm the cluster exists first
    r = requests.get(f"{API}/customers/-/duplicates?branch_id={branch}",
                     headers=_auth_headers(token), timeout=15)
    assert r.status_code == 200
    clusters = r.json()["clusters"]
    assert any({a, b}.issubset({m["id"] for m in c["members"]}) for c in clusters)

    # Mark distinct
    r = requests.post(
        f"{API}/customers/mark-distinct",
        json={"customer_ids": [a, b]},
        headers=_auth_headers(token), timeout=15,
    )
    assert r.status_code == 200, r.text
    assert r.json()["recorded"] >= 1

    # Rescan — the exact pair should no longer cluster together
    r = requests.get(f"{API}/customers/-/duplicates?branch_id={branch}",
                     headers=_auth_headers(token), timeout=15)
    assert r.status_code == 200
    clusters = r.json()["clusters"]
    for c in clusters:
        ids = {m["id"] for m in c["members"]}
        assert not (a in ids and b in ids), \
            f"Cluster still contains marked-distinct pair: {c}"


# ─────────────────────────────────────────────────────────────────────────────
#  4. /customers/merge — moves invoices, soft-deletes dup, audit trail exists
# ─────────────────────────────────────────────────────────────────────────────
def test_merge_moves_invoices_and_soft_deletes_duplicates(auth, branch):
    token, user = auth
    db = _db()
    org_id = user["organization_id"]

    suffix = uuid.uuid4().hex[:6]
    # Master — has phone but empty email/address
    master_id = _make_customer(db, name=f"Canonical Name {suffix}", branch_id=branch,
                                org_id=org_id, phones=["09180000001"], email="", address="")
    # Dup — has email + address that should flow onto master
    dup_id = _make_customer(db, name=f"Canon Name {suffix}", branch_id=branch,
                              org_id=org_id, phones=["09180000002"],
                              email="dup@example.com", address="Dup Street 123")

    inv_master = _make_invoice(db, customer_id=master_id, customer_name="Canonical Name",
                                branch_id=branch, org_id=org_id, total=2000, balance=2000)
    inv_dup = _make_invoice(db, customer_id=dup_id, customer_name="Canon Name",
                              branch_id=branch, org_id=org_id, total=1500, balance=1500)

    r = requests.post(
        f"{API}/customers/merge",
        json={
            "master_id": master_id,
            "duplicate_ids": [dup_id],
            "canonical_name": f"Canonical Name {suffix} (canon)",
        },
        headers=_auth_headers(token), timeout=20,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["duplicates_merged"] == 1
    assert body["invoices_moved"] >= 1

    # Duplicate should now be soft-deleted with merged_into set
    dup = db.customers.find_one({"id": dup_id}, {"_id": 0})
    assert dup["active"] is False
    assert dup.get("merged_into") == master_id

    # Master should have new email/address (previously empty) and renamed
    master = db.customers.find_one({"id": master_id}, {"_id": 0})
    assert master["email"] == "dup@example.com"
    assert master["address"] == "Dup Street 123"
    assert master["name"] == f"Canonical Name {suffix} (canon)"

    # Phones merged
    assert "09180000001" in master["phones"]
    assert "09180000002" in master["phones"]

    # Invoices re-pointed
    inv_check = db.invoices.find_one({"id": inv_dup}, {"_id": 0})
    assert inv_check["customer_id"] == master_id

    # Balance recomputed (2000 + 1500 = 3500)
    assert abs(master["balance"] - 3500.0) < 0.01

    # Audit trail
    audit = db.customer_merges.find_one(
        {"master_id": master_id, "merged_ids": {"$in": [dup_id]}},
        {"_id": 0},
    )
    assert audit is not None
    assert audit["invoices_moved"] >= 1
    # _master_before_ has the ORIGINAL pre-merge name
    assert "Canonical Name" in audit["master_name_before"]


# ─────────────────────────────────────────────────────────────────────────────
#  5. Merge rule — master's non-empty field WINS (never overwritten by dup)
# ─────────────────────────────────────────────────────────────────────────────
def test_merge_preserves_master_fields_when_present(auth, branch):
    token, user = auth
    db = _db()
    org_id = user["organization_id"]

    suffix = uuid.uuid4().hex[:6]
    master_id = _make_customer(db, name=f"Keep Master {suffix}", branch_id=branch,
                                org_id=org_id, phones=["09111111111"],
                                email="master@example.com", address="Master Street")
    dup_id = _make_customer(db, name=f"Keep Master Dup {suffix}", branch_id=branch,
                              org_id=org_id, phones=["09222222222"],
                              email="dup@example.com", address="Dup Street")

    r = requests.post(
        f"{API}/customers/merge",
        json={"master_id": master_id, "duplicate_ids": [dup_id]},
        headers=_auth_headers(token), timeout=20,
    )
    assert r.status_code == 200, r.text

    master = db.customers.find_one({"id": master_id}, {"_id": 0})
    # Master values must survive; dup values are discarded for non-empty fields
    assert master["email"] == "master@example.com"
    assert master["address"] == "Master Street"


# ─────────────────────────────────────────────────────────────────────────────
#  6. Bulk delete — PIN required; guard blocks balance>0 without force
# ─────────────────────────────────────────────────────────────────────────────
def test_bulk_delete_requires_pin_and_respects_guards(auth, branch):
    token, user = auth
    db = _db()
    org_id = user["organization_id"]

    suffix = uuid.uuid4().hex[:6]
    safe_id = _make_customer(db, name=f"SafeToPurge {suffix}", branch_id=branch,
                              org_id=org_id, balance=0)
    busy_id = _make_customer(db, name=f"HasBalance {suffix}", branch_id=branch,
                              org_id=org_id, balance=750.0)
    _make_invoice(db, customer_id=busy_id, customer_name="HasBalance",
                   branch_id=branch, org_id=org_id, total=750, balance=750)

    # Missing PIN → 400
    r = requests.post(
        f"{API}/customers/bulk-delete",
        json={"customer_ids": [safe_id, busy_id]},
        headers=_auth_headers(token), timeout=15,
    )
    assert r.status_code == 400

    # Wrong PIN → 403
    r = requests.post(
        f"{API}/customers/bulk-delete",
        json={"customer_ids": [safe_id], "pin": "000000"},
        headers=_auth_headers(token), timeout=15,
    )
    assert r.status_code == 403

    # Correct PIN, no force → safe deleted, busy blocked
    r = requests.post(
        f"{API}/customers/bulk-delete",
        json={"customer_ids": [safe_id, busy_id], "pin": TEST_ORG_ADMIN_PIN},
        headers=_auth_headers(token), timeout=15,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["deleted_count"] == 1
    assert body["blocked_count"] == 1
    deleted_ids = [d["id"] for d in body["deleted"]]
    blocked_ids = [b["id"] for b in body["blocked"]]
    assert safe_id in deleted_ids
    assert busy_id in blocked_ids
    # Safe customer actually soft-deleted
    safe_check = db.customers.find_one({"id": safe_id}, {"_id": 0})
    assert safe_check["active"] is False

    # With force=true, admin can also delete the busy customer
    r = requests.post(
        f"{API}/customers/bulk-delete",
        json={"customer_ids": [busy_id], "pin": TEST_ORG_ADMIN_PIN, "force": True},
        headers=_auth_headers(token), timeout=15,
    )
    assert r.status_code == 200, r.text
    assert r.json()["deleted_count"] == 1
    busy_check = db.customers.find_one({"id": busy_id}, {"_id": 0})
    assert busy_check["active"] is False

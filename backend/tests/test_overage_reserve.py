"""
Backend tests for the Overage Reserve Ledger system (iteration 176).

Covers:
- GET /api/reserve/summary (multi-tenant scoping, branch filter, totals shape)
- GET /api/reserve/ledger (pagination, pool/type filters, schema)
- POST /api/reserve/backfill (admin-only, idempotent)
- POST /api/reserve/apply (PIN, validation, audit linking)
- POST /api/reserve/net-shortage (paired entries, cap)
- POST /api/reserve/claw-back (reversal, double-reverse rejection)
- balance_after running consistency
- Daily-close auto-hook via backfill seeded data
- /api/audit/compute response includes 'reserve' field for branched calls
"""
import os
import sys
import pytest
import requests
from uuid import uuid4
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _org_test_helpers import ensure_org_admin_token, _db, TEST_ORG_MANAGER_PIN, TEST_ORG_ADMIN_PIN  # noqa

API = os.environ.get("API_URL", "https://pos-refund-engine.preview.emergentagent.com").rstrip("/") + "/api"


# ─── Module-scoped fixtures ───────────────────────────────────────────────
@pytest.fixture(scope="module")
def admin_token():
    token, user = ensure_org_admin_token()
    return token, user


@pytest.fixture(scope="module")
def headers(admin_token):
    return {"Authorization": f"Bearer {admin_token[0]}", "Content-Type": "application/json"}


@pytest.fixture(scope="module")
def org_id(admin_token):
    return admin_token[1].get("organization_id")


@pytest.fixture(scope="module")
def test_branch_id(headers, org_id):
    """Pick the first active branch belonging to the test org admin's org."""
    db = _db()
    b = db.branches.find_one({"active": True, "organization_id": org_id}, {"_id": 0, "id": 1})
    if not b:
        pytest.skip("No active branches in test org")
    return b["id"]


@pytest.fixture(scope="module")
def seeded_closings(test_branch_id, headers, org_id):
    """
    Seed 2 daily_closings rows with non-zero over_short, then call /backfill
    to populate the ledger. Yields a marker tag so we can clean up afterwards.
    """
    db = _db()
    tag = f"TEST_RES_{uuid4().hex[:8]}"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    closings = [
        {
            "id": str(uuid4()),
            "branch_id": test_branch_id,
            "date": today,
            "status": "closed",
            "over_short": 50.0,
            "expected_cash": 1000.0,
            "actual_cash": 1050.0,
            "_test_tag": tag,
            "organization_id": org_id,
        },
        {
            "id": str(uuid4()),
            "branch_id": test_branch_id,
            "date": today,
            "status": "closed",
            "over_short": -30.0,
            "expected_cash": 1000.0,
            "actual_cash": 970.0,
            "_test_tag": tag,
            "organization_id": org_id,
        },
        # Zero over_short — should NOT create a ledger entry
        {
            "id": str(uuid4()),
            "branch_id": test_branch_id,
            "date": today,
            "status": "closed",
            "over_short": 0.0,
            "expected_cash": 1000.0,
            "actual_cash": 1000.0,
            "_test_tag": tag,
            "organization_id": org_id,
        },
    ]
    # Pre-cleanup: drop any orphan TEST_ ledger rows from prior runs on this
    # branch so balance_after consistency math isn't poisoned.
    db.overage_reserve_ledger.delete_many({
        "branch_id": test_branch_id,
        "$or": [
            {"note": {"$regex": "^TEST_"}},
            {"source_id": "net_shortage"},
            {"source_id": "manual"},
        ],
    })
    db.daily_closings.insert_many(closings)
    yield {"tag": tag, "closings": closings}
    # Cleanup: closings + every ledger entry created against those closings
    # (auto_credit + any claw_back referencing them) + manual TEST_ entries.
    closing_ids = [c["id"] for c in closings]
    db.daily_closings.delete_many({"_test_tag": tag})
    db.overage_reserve_ledger.delete_many({
        "branch_id": test_branch_id,
        "$or": [
            {"source_id": {"$in": closing_ids}},
            {"note": {"$regex": "^TEST_"}},
            {"source_id": "net_shortage"},
            {"source_id": "manual"},
        ],
    })


# ─── Tests ───────────────────────────────────────────────────────────────
class TestReserveSummary:
    """GET /api/reserve/summary"""

    def test_summary_requires_auth(self):
        r = requests.get(f"{API}/reserve/summary", timeout=10)
        assert r.status_code in (401, 403)

    def test_summary_shape_and_multitenant_scoping(self, headers, org_id):
        r = requests.get(f"{API}/reserve/summary", headers=headers, timeout=15)
        assert r.status_code == 200, r.text
        body = r.json()
        # shape
        assert "branches" in body and "totals" in body
        assert isinstance(body["branches"], list)
        for required in ("reserve_total", "deficit_total", "net_pool"):
            assert required in body["totals"]
        # multi-tenant: every returned branch must belong to the admin's org
        db = _db()
        branch_ids = [b["branch_id"] for b in body["branches"]]
        if branch_ids:
            mismatched = list(db.branches.find(
                {"id": {"$in": branch_ids}, "organization_id": {"$ne": org_id}},
                {"_id": 0, "id": 1, "organization_id": 1}
            ))
            assert mismatched == [], f"Multi-tenant leak: {mismatched}"
        # branches list should NOT include 30+ orgs' branches
        all_branches_count = db.branches.count_documents({"active": True})
        org_branches_count = db.branches.count_documents({"active": True, "organization_id": org_id})
        assert len(body["branches"]) == org_branches_count
        if all_branches_count > org_branches_count:
            assert len(body["branches"]) < all_branches_count, "Leak: returns all-org branches"

    def test_summary_branch_id_filter(self, headers, test_branch_id):
        r = requests.get(f"{API}/reserve/summary?branch_id={test_branch_id}", headers=headers, timeout=15)
        assert r.status_code == 200
        body = r.json()
        assert len(body["branches"]) == 1
        assert body["branches"][0]["branch_id"] == test_branch_id
        for k in ("reserve_balance", "deficit_balance", "net_pool",
                  "last_reserve_at", "last_deficit_at", "branch_name"):
            assert k in body["branches"][0]


class TestBackfillAndAutoHook:
    """POST /api/reserve/backfill (admin) — covers the daily-close hook idempotency."""

    def test_backfill_creates_entries(self, headers, seeded_closings, test_branch_id):
        r = requests.post(
            f"{API}/reserve/backfill",
            headers=headers,
            json={"branch_id": test_branch_id},
            timeout=30,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        # Two non-zero closings should produce 2 entries; zero one should skip
        assert body["created"] >= 2, f"expected >=2 created, got {body}"
        assert body["scanned"] >= 2

    def test_backfill_idempotent(self, headers, seeded_closings, test_branch_id):
        r = requests.post(
            f"{API}/reserve/backfill",
            headers=headers,
            json={"branch_id": test_branch_id},
            timeout=30,
        )
        assert r.status_code == 200
        # Second call should create 0 new
        assert r.json()["created"] == 0

    def test_summary_reflects_seeded_balances(self, headers, seeded_closings, test_branch_id):
        r = requests.get(f"{API}/reserve/summary?branch_id={test_branch_id}", headers=headers, timeout=15)
        assert r.status_code == 200
        row = r.json()["branches"][0]
        # reserve was 50, deficit was 30 from seeded closings.
        # Other historical entries may exist on this branch — assert at-least.
        assert row["reserve_balance"] >= 50.0
        assert row["deficit_balance"] >= 30.0


class TestLedger:
    """GET /api/reserve/ledger"""

    def test_ledger_paginated_and_schema(self, headers, test_branch_id, seeded_closings):
        r = requests.get(
            f"{API}/reserve/ledger?branch_id={test_branch_id}&limit=10",
            headers=headers, timeout=15,
        )
        assert r.status_code == 200
        body = r.json()
        assert "entries" in body and "total" in body
        assert len(body["entries"]) <= 10
        if body["entries"]:
            e = body["entries"][0]
            for k in (
                "id", "pool", "type", "amount", "balance_after", "source_type",
                "source_id", "source_ref", "note", "applied_to", "paired_entry_id",
                "date", "created_at", "branch_id", "branch_name",
                "verifier_id", "verifier_name", "created_by", "created_by_name",
            ):
                assert k in e, f"missing field {k}"

    def test_ledger_pool_filter(self, headers, test_branch_id, seeded_closings):
        r = requests.get(
            f"{API}/reserve/ledger?branch_id={test_branch_id}&pool=reserve&limit=20",
            headers=headers, timeout=15,
        )
        assert r.status_code == 200
        for e in r.json()["entries"]:
            assert e["pool"] == "reserve"

    def test_ledger_type_filter(self, headers, test_branch_id, seeded_closings):
        r = requests.get(
            f"{API}/reserve/ledger?branch_id={test_branch_id}&entry_type=auto_credit&limit=20",
            headers=headers, timeout=15,
        )
        assert r.status_code == 200
        for e in r.json()["entries"]:
            assert e["type"] == "auto_credit"

    def test_balance_after_running_consistency(self, headers, test_branch_id, seeded_closings):
        """For each pool, sort by created_at ASC and verify balance_after == prev + amount."""
        for pool in ("reserve", "deficit"):
            r = requests.get(
                f"{API}/reserve/ledger?branch_id={test_branch_id}&pool={pool}&limit=200",
                headers=headers, timeout=15,
            )
            assert r.status_code == 200
            entries = sorted(r.json()["entries"], key=lambda x: x["created_at"])
            running = 0.0
            for e in entries:
                running = round(running + e["amount"], 2)
                assert abs(e["balance_after"] - running) < 0.02, (
                    f"Inconsistent balance_after on pool={pool} entry {e['id']}: "
                    f"expected {running}, got {e['balance_after']}"
                )


class TestApplyReserve:
    """POST /api/reserve/apply"""

    def test_apply_rejects_invalid_pin(self, headers, test_branch_id, seeded_closings):
        r = requests.post(
            f"{API}/reserve/apply",
            headers=headers,
            json={
                "branch_id": test_branch_id, "amount": 5.0,
                "applied_to": "inventory_variance", "reason": "test",
                "pin": "000000",
            },
            timeout=15,
        )
        assert r.status_code == 403

    def test_apply_rejects_missing_branch(self, headers):
        r = requests.post(
            f"{API}/reserve/apply",
            headers=headers,
            json={"amount": 5.0, "applied_to": "inventory_variance",
                  "reason": "x", "pin": TEST_ORG_MANAGER_PIN},
            timeout=15,
        )
        assert r.status_code == 400

    def test_apply_rejects_missing_reason(self, headers, test_branch_id):
        r = requests.post(
            f"{API}/reserve/apply",
            headers=headers,
            json={"branch_id": test_branch_id, "amount": 5.0,
                  "applied_to": "inventory_variance", "reason": "",
                  "pin": TEST_ORG_MANAGER_PIN},
            timeout=15,
        )
        assert r.status_code == 400

    def test_apply_rejects_invalid_applied_to(self, headers, test_branch_id):
        r = requests.post(
            f"{API}/reserve/apply",
            headers=headers,
            json={"branch_id": test_branch_id, "amount": 5.0,
                  "applied_to": "bogus_category", "reason": "x",
                  "pin": TEST_ORG_MANAGER_PIN},
            timeout=15,
        )
        assert r.status_code == 400

    def test_apply_rejects_overdraw(self, headers, test_branch_id, seeded_closings):
        r = requests.post(
            f"{API}/reserve/apply",
            headers=headers,
            json={"branch_id": test_branch_id, "amount": 99999999.0,
                  "applied_to": "inventory_variance", "reason": "way too much",
                  "pin": TEST_ORG_MANAGER_PIN},
            timeout=15,
        )
        assert r.status_code == 400
        assert "Insufficient" in r.text or "balance" in r.text.lower()

    def test_apply_success_debits_reserve(self, headers, test_branch_id, seeded_closings):
        # Get current reserve balance
        r0 = requests.get(f"{API}/reserve/summary?branch_id={test_branch_id}",
                          headers=headers, timeout=15)
        before = r0.json()["branches"][0]["reserve_balance"]
        amount = min(10.0, before)
        if amount <= 0:
            pytest.skip("No reserve balance to test apply against")

        r = requests.post(
            f"{API}/reserve/apply",
            headers=headers,
            json={
                "branch_id": test_branch_id, "amount": amount,
                "applied_to": "inventory_variance",
                "reason": "TEST_apply audit offset",
                "pin": TEST_ORG_MANAGER_PIN,
            },
            timeout=15,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert "entry" in body and "new_reserve_balance" in body
        assert body["entry"]["type"] == "apply_audit"
        assert body["entry"]["amount"] == -amount
        assert body["entry"]["applied_to"] == "inventory_variance"
        assert abs(body["new_reserve_balance"] - (before - amount)) < 0.02


class TestNetShortage:
    """POST /api/reserve/net-shortage"""

    def test_net_shortage_requires_pin(self, headers, test_branch_id):
        r = requests.post(
            f"{API}/reserve/net-shortage",
            headers=headers,
            json={"branch_id": test_branch_id, "amount": 5.0, "pin": "000000"},
            timeout=15,
        )
        assert r.status_code == 403

    def test_net_shortage_rejects_above_cap(self, headers, test_branch_id, seeded_closings):
        r = requests.post(
            f"{API}/reserve/net-shortage",
            headers=headers,
            json={"branch_id": test_branch_id, "amount": 99999999.0,
                  "pin": TEST_ORG_MANAGER_PIN},
            timeout=15,
        )
        assert r.status_code == 400

    def test_net_shortage_success_paired(self, headers, test_branch_id, seeded_closings):
        r0 = requests.get(f"{API}/reserve/summary?branch_id={test_branch_id}",
                          headers=headers, timeout=15)
        row = r0.json()["branches"][0]
        cap = min(row["reserve_balance"], row["deficit_balance"])
        if cap <= 1.0:
            pytest.skip("Insufficient cap for netting test")
        amount = min(5.0, cap)

        r = requests.post(
            f"{API}/reserve/net-shortage",
            headers=headers,
            json={"branch_id": test_branch_id, "amount": amount,
                  "reason": "TEST_net", "pin": TEST_ORG_MANAGER_PIN},
            timeout=15,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["reserve_entry"]["type"] == "net_shortage"
        assert body["deficit_entry"]["type"] == "net_shortage"
        assert body["reserve_entry"]["amount"] == -amount
        assert body["deficit_entry"]["amount"] == -amount
        # paired_entry_id linkage
        assert body["deficit_entry"]["paired_entry_id"] == body["reserve_entry"]["id"]


class TestClawBack:
    """POST /api/reserve/claw-back"""

    def test_claw_back_full_flow(self, headers, test_branch_id, seeded_closings):
        # Find an auto_credit entry to reverse
        r = requests.get(
            f"{API}/reserve/ledger?branch_id={test_branch_id}&entry_type=auto_credit&limit=5",
            headers=headers, timeout=15,
        )
        # Find one that is NOT yet reversed
        entries = [e for e in r.json()["entries"] if not e.get("reversed")]
        if not entries:
            pytest.skip("No un-reversed auto_credit entries to claw back")
        entry = entries[0]

        r1 = requests.post(
            f"{API}/reserve/claw-back",
            headers=headers,
            json={"entry_id": entry["id"], "reason": "TEST_clawback",
                  "pin": TEST_ORG_MANAGER_PIN},
            timeout=15,
        )
        assert r1.status_code == 200, r1.text
        body = r1.json()
        assert body["reversal_entry"]["type"] == "claw_back"
        # Opposite sign
        assert body["reversal_entry"]["amount"] == -entry["amount"]
        assert body["reversal_entry"]["paired_entry_id"] == entry["id"]

        # Second claw-back of the same entry should fail
        r2 = requests.post(
            f"{API}/reserve/claw-back",
            headers=headers,
            json={"entry_id": entry["id"], "reason": "TEST_clawback2",
                  "pin": TEST_ORG_MANAGER_PIN},
            timeout=15,
        )
        assert r2.status_code == 400

    def test_claw_back_invalid_pin(self, headers, test_branch_id, seeded_closings):
        r = requests.get(
            f"{API}/reserve/ledger?branch_id={test_branch_id}&limit=1",
            headers=headers, timeout=15,
        )
        entries = r.json()["entries"]
        if not entries:
            pytest.skip("No ledger entries")
        r2 = requests.post(
            f"{API}/reserve/claw-back",
            headers=headers,
            json={"entry_id": entries[0]["id"], "reason": "x", "pin": "000000"},
            timeout=15,
        )
        # Either 403 (bad pin) or 400 (already reversed)
        assert r2.status_code in (400, 403)


class TestAuditComputeReserveField:
    """/api/audit/compute should expose 'reserve' field for branched calls."""

    def test_compute_includes_reserve_for_branch(self, headers, test_branch_id, seeded_closings):
        r = requests.get(
            f"{API}/audit/compute?branch_id={test_branch_id}",
            headers=headers, timeout=30,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert "reserve" in body, "compute response missing 'reserve' field"
        assert body["reserve"] is not None
        assert body["reserve"]["branch_id"] == test_branch_id
        assert "reserve_balance" in body["reserve"]
        assert "deficit_balance" in body["reserve"]

    def test_compute_orgwide_reserve_null(self, headers):
        """Branchless org-wide compute should not break — reserve should be null/missing."""
        r = requests.get(f"{API}/audit/compute", headers=headers, timeout=30)
        assert r.status_code == 200, r.text
        body = r.json()
        # spec: "Doesn't break for branchless org-wide compute (returns null)"
        # Accept either explicit None or missing key (current code path doesn't set it)
        assert body.get("reserve") is None

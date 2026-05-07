"""
Regression tests for Linked Offline Draft Finalization (Iter 252, Feb 2026).

Coverage:
  1. Happy path: envelope with `draft_invoice_id` flips an existing
     for_preparation draft to paid; canonical invoice_number preserved;
     `linked_offline_receipt_number` stamped; inventory deducted ONCE.
  2. Idempotency: re-running the same envelope returns "duplicate", no
     double inventory deduction.
  3. Conflict — draft already paid: reconciliation queue entry created.
  4. Conflict — draft not found: reconciliation queue entry created.
  5. Doc lookup: both `invoice_number` and `linked_offline_receipt_number`
     resolve to the same canonical record.

All tests share a single event loop (set BEFORE importing routes.sync) so the
module-level `config.db` motor client uses the same loop the tests run in.
"""
import asyncio
import os
import sys

import pytest
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
sys.path.insert(0, "/app/backend")

# ─── Single shared event loop ────────────────────────────────────────────
# `routes.sync` imports `from config import db` which creates an
# AsyncIOMotorClient bound to whatever event loop is current at import time.
# pytest's default `asyncio.run()` per test makes a fresh loop and closes it,
# leaving subsequent tests with a dead motor client. We pin one loop for the
# whole test module before importing routes.sync.
_LOOP = asyncio.new_event_loop()
asyncio.set_event_loop(_LOOP)

from routes.sync import _finalize_draft_offline  # noqa: E402
from config import _raw_db as _module_db, set_org_context  # noqa: E402

# Set a stable test org context so the tenant proxy allows inserts/finds.
set_org_context("test-org-iter252")

TEST_BRANCH = "test-iter252-branch"
TEST_ORG = "test-org-iter252"


def _run(coro):
    return _LOOP.run_until_complete(coro)


def _make_user():
    return {
        "id": "test-user-iter252",
        "username": "testcashier",
        "full_name": "Test Cashier",
        "role": "cashier",
        "organization_id": TEST_ORG,
        "branch_id": TEST_BRANCH,
    }


async def _seed_draft(draft_id="draft-1", number="SI-T-001", total=1000.0):
    db = _module_db
    await db.invoices.delete_many({"branch_id": TEST_BRANCH})
    await db.inventory.delete_many({"branch_id": TEST_BRANCH})
    await db.products.delete_many({"id": "prod-iter252"})
    await db.offline_reconciliation_queue.delete_many({"branch_id": TEST_BRANCH})
    await db.products.insert_one({
        "id": "prod-iter252", "name": "Test Fertilizer",
        "is_repack": False, "active": True,
        "organization_id": TEST_ORG,
    })
    await db.inventory.insert_one({
        "product_id": "prod-iter252", "branch_id": TEST_BRANCH,
        "quantity": 50.0, "organization_id": TEST_ORG,
    })
    await db.invoices.insert_one({
        "id": draft_id, "branch_id": TEST_BRANCH,
        "invoice_number": number,
        "customer_name": "Walk-in", "customer_id": None,
        "order_date": "2026-02-15",
        "payment_type": "cash", "status": "for_preparation",
        "grand_total": total, "amount_paid": 0.0, "balance": total,
        "items": [{"product_id": "prod-iter252", "product_name": "Test Fertilizer",
                   "quantity": 5.0, "rate": 200.0, "total": 1000.0}],
        "created_at": "2026-02-15T09:00:00+00:00",
        "payments": [],
        "organization_id": TEST_ORG,
    })


async def _cleanup():
    db = _module_db
    await db.invoices.delete_many({"branch_id": TEST_BRANCH})
    await db.inventory.delete_many({"branch_id": TEST_BRANCH})
    await db.products.delete_many({"id": "prod-iter252"})
    await db.offline_reconciliation_queue.delete_many({"branch_id": TEST_BRANCH})


def _envelope(envelope_id, off_num, draft_id="draft-1", draft_num="SI-T-001"):
    return {
        "id": f"off-id-{envelope_id}", "envelope_id": envelope_id,
        "branch_id": TEST_BRANCH,
        "draft_invoice_id": draft_id,
        "draft_invoice_number": draft_num,
        "kind": "draft_finalization_offline",
        "invoice_number": off_num,
        "offline_receipt_number": off_num,
        "items": [{"product_id": "prod-iter252", "product_name": "Test Fertilizer",
                   "quantity": 5.0, "rate": 200.0, "total": 1000.0}],
        "grand_total": 1000.0, "amount_paid": 1000.0, "balance": 0.0,
        "payment_type": "cash", "subtotal": 1000.0,
        "date": "2026-02-15",
        "payments": [{"amount": 1000.0, "date": "2026-02-15",
                      "method": "Cash", "fund_source": "cashier"}],
    }


def test_offline_draft_finalization_happy_path():
    async def go():
        await _seed_draft()
        env = _envelope("env-1", "SI-T-OFF-000001")
        res = await _finalize_draft_offline(env, _make_user(), "env-1")
        assert res["status"] == "synced"
        assert res["official_invoice"] == "SI-T-001"
        assert res["off_receipt"] == "SI-T-OFF-000001"

        doc = await _module_db.invoices.find_one({"id": "draft-1"}, {"_id": 0})
        assert doc["invoice_number"] == "SI-T-001"
        assert doc["status"] == "paid"
        assert doc["linked_offline_receipt_number"] == "SI-T-OFF-000001"
        assert doc["finalized_from_draft_offline"] is True
        assert doc["original_draft_invoice_number"] == "SI-T-001"

        inv = await _module_db.inventory.find_one(
            {"product_id": "prod-iter252", "branch_id": TEST_BRANCH},
            {"_id": 0, "quantity": 1},
        )
        assert inv["quantity"] == 45.0
        await _cleanup()
    _run(go())


def test_offline_draft_finalization_idempotent():
    async def go():
        await _seed_draft()
        env = _envelope("env-2", "SI-T-OFF-000002")
        r1 = await _finalize_draft_offline(env, _make_user(), "env-2")
        assert r1["status"] == "synced"

        r2 = await _finalize_draft_offline(env, _make_user(), "env-2")
        assert r2["status"] == "duplicate"

        inv = await _module_db.inventory.find_one(
            {"product_id": "prod-iter252", "branch_id": TEST_BRANCH},
            {"_id": 0, "quantity": 1},
        )
        assert inv["quantity"] == 45.0  # NOT 40 — proves no double-deduct
        await _cleanup()
    _run(go())


def test_offline_draft_conflict_already_paid():
    async def go():
        await _seed_draft()
        await _module_db.invoices.update_one(
            {"id": "draft-1"},
            {"$set": {"status": "paid", "amount_paid": 1000.0, "balance": 0.0}},
        )
        env = _envelope("env-3", "SI-T-OFF-000003")
        res = await _finalize_draft_offline(env, _make_user(), "env-3")
        assert res["status"] == "conflict"
        assert res["reason"] == "draft_already_paid"

        row = await _module_db.offline_reconciliation_queue.find_one(
            {"envelope_id": "env-3"}, {"_id": 0}
        )
        assert row is not None
        assert row["reason"] == "draft_already_paid"
        assert row["status"] == "open"
        await _cleanup()
    _run(go())


def test_offline_draft_conflict_not_found():
    async def go():
        await _cleanup()
        env = _envelope("env-4", "SI-T-OFF-000004",
                        draft_id="nonexistent-draft-xxx",
                        draft_num="SI-T-NONEXISTENT")
        res = await _finalize_draft_offline(env, _make_user(), "env-4")
        assert res["status"] == "conflict"
        assert res["reason"] == "draft_not_found"

        row = await _module_db.offline_reconciliation_queue.find_one(
            {"envelope_id": "env-4"}, {"_id": 0}
        )
        assert row is not None
        await _cleanup()
    _run(go())


def test_doc_lookup_resolves_both_numbers():
    async def go():
        await _seed_draft()
        await _module_db.invoices.update_one(
            {"id": "draft-1"},
            {"$set": {
                "status": "paid", "amount_paid": 1000.0, "balance": 0.0,
                "linked_offline_receipt_number": "SI-T-OFF-LOOKUP",
                "finalized_from_draft_offline": True,
                "original_draft_invoice_number": "SI-T-001",
            }},
        )
        # Mirror the route's $or query
        r1 = await _module_db.invoices.find_one(
            {"$or": [
                {"invoice_number": "SI-T-001"},
                {"linked_offline_receipt_number": "SI-T-001"},
            ]}, {"_id": 0, "id": 1}
        )
        r2 = await _module_db.invoices.find_one(
            {"$or": [
                {"invoice_number": "SI-T-OFF-LOOKUP"},
                {"linked_offline_receipt_number": "SI-T-OFF-LOOKUP"},
            ]}, {"_id": 0, "id": 1}
        )
        assert r1 and r2
        assert r1["id"] == r2["id"] == "draft-1"
        await _cleanup()
    _run(go())

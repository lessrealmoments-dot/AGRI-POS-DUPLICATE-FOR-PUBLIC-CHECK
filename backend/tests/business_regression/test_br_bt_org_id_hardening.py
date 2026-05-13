"""br_bt_org_id — defensive lock that BTO writers preserve organization_id.

Phase 3.1 — preventive only. Today every BTO writer uses a hardcoded
`$set` whitelist so a rogue `organization_id` field in the payload can't
reach Mongo. These tests pin that behaviour so a future refactor (e.g.
switching to `{"$set": data}`) can't silently break tenant scoping.

Scope (deliberately small):
  1. PUT /branch-transfers/{id} ignores `organization_id` in the body.
  2. PUT also ignores a blank `organization_id` (the prior prepared-order
     class of bug).
  3. /approve preserves org_id end-to-end.
  4. /reject preserves org_id end-to-end.
  5. /resubmit preserves org_id end-to-end.
  6. After a hostile update, the BTO still lists tenant-scoped (i.e.
     a subsequent GET via the tenant proxy finds it).

The defensive tripwire in `branch_transfers._assert_org_preserved` is
called from each writer — if Mongo ever did blank the field, the writer
would 500 BEFORE returning. The tests below pass today because the
whitelist already protects us; they would catch a future regression.
"""
import os
import sys

import pytest
from fastapi import HTTPException

BACKEND = os.path.join(os.path.dirname(__file__), "..", "..")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from config import _raw_db                                       # noqa: E402
from tests.business_regression._fixtures import seed_product    # noqa: E402
from tests.phase2b._fixtures import fake_user, _uid             # noqa: E402
from routes.branch_transfers import (                            # noqa: E402
    create_transfer,
    update_transfer,
    approve_pending_transfer,
    reject_pending_transfer,
    resubmit_returned_transfer,
    list_transfers,
)


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────
async def _bto_row(transfer_id):
    return await _raw_db.branch_transfer_orders.find_one(
        {"id": transfer_id}, {"_id": 0}
    )


async def _seed_pending_bto(tenant):
    """Seed a `pending_approval` BTO via the real handler so the row
    carries every default field (`organization_id` included)."""
    org_id = tenant["org_id"]
    main = tenant["branches"]["main"]
    b2 = tenant["branches"]["b2"]
    owner = tenant["users"]["owner"]
    pid = await seed_product(
        org_id, main, name=f"br_bt_org-{_uid('p')[-4:]}",
        price=100, stock=50, cost=60,
    )
    res = await create_transfer({
        "from_branch_id": main, "to_branch_id": b2,
        "items": [{
            "product_id": pid, "product_name": "br_bt_org Product",
            "sku": f"SKU-{pid[-6:]}", "unit": "pc",
            "qty": 5, "branch_capital": 60.0,
            "transfer_capital": 60.0, "branch_retail": 100.0,
        }],
        "min_margin": 20, "notes": "br_bt_org seed",
        # Force the pending-approval path via the manager's path: when
        # `requires_approval=True` the handler sets status=pending_approval.
        "requires_approval": True,
    }, user=owner)
    return res["id"], org_id


async def _seed_admin_pin(org_id: str, admin_pin: str = "445566"):
    from utils.auth import hash_password
    await _raw_db.system_settings.update_one(
        {"organization_id": org_id, "key": "admin_pin"},
        {"$set": {"organization_id": org_id, "key": "admin_pin",
                  "pin_hash": hash_password(admin_pin),
                  "updated_at": "2026-01-01T00:00:00Z"}},
        upsert=True,
    )
    return admin_pin


# ═════════════════════════════════════════════════════════════════════
# Test 1 — PUT /update_transfer ignores organization_id in payload
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_br_bt_org_1_update_ignores_explicit_org_in_payload(
    tenant, record_result
):
    transfer_id, prev_org = await _seed_pending_bto(tenant)
    # Move it to draft so update_transfer accepts it.
    await _raw_db.branch_transfer_orders.update_one(
        {"id": transfer_id}, {"$set": {"status": "draft"}}
    )
    owner = tenant["users"]["owner"]
    HOSTILE = "evil-other-tenant"
    await update_transfer(transfer_id, {
        "organization_id": HOSTILE,  # rogue
        "notes": "still mine",
    }, user=owner)
    after = await _bto_row(transfer_id)
    record_result(
        scenario="br_bt_org.1_update_ignores_hostile_org",
        step="org_id_unchanged_after_PUT",
        expected={"org_id": prev_org, "hostile_won": False},
        actual={"org_id": after.get("organization_id"),
                "hostile_won": after.get("organization_id") == HOSTILE},
        evidence={"transfer_id": transfer_id, "hostile": HOSTILE},
    )
    assert after["organization_id"] == prev_org


# ═════════════════════════════════════════════════════════════════════
# Test 2 — PUT /update_transfer ignores BLANK organization_id payload
#         (the prior prepared-order class of bug)
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_br_bt_org_2_update_ignores_blank_org_in_payload(
    tenant, record_result
):
    transfer_id, prev_org = await _seed_pending_bto(tenant)
    await _raw_db.branch_transfer_orders.update_one(
        {"id": transfer_id}, {"$set": {"status": "draft"}}
    )
    owner = tenant["users"]["owner"]
    await update_transfer(transfer_id, {
        "organization_id": "",     # blank — what we explicitly want to block
        "notes": "still mine — blank attack",
    }, user=owner)
    after = await _bto_row(transfer_id)
    record_result(
        scenario="br_bt_org.2_update_ignores_blank_org",
        step="org_id_unchanged_after_blank_payload",
        expected={"org_id": prev_org, "blanked": False},
        actual={"org_id": after.get("organization_id"),
                "blanked": not after.get("organization_id")},
        evidence={"transfer_id": transfer_id},
    )
    assert after["organization_id"] == prev_org


# ═════════════════════════════════════════════════════════════════════
# Test 3 — /approve preserves organization_id
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_br_bt_org_3_approve_preserves_org(
    tenant, record_result
):
    transfer_id, prev_org = await _seed_pending_bto(tenant)
    admin_pin = await _seed_admin_pin(prev_org)
    owner = tenant["users"]["owner"]
    await approve_pending_transfer(transfer_id, {
        "pin": admin_pin,
        "organization_id": "evil-during-approve",  # rogue field — must be ignored
        "items": [],
        "notes": "approved by test",
    }, user=owner)
    after = await _bto_row(transfer_id)
    record_result(
        scenario="br_bt_org.3_approve_preserves_org",
        step="org_id_unchanged_after_approve",
        expected={"org_id": prev_org, "status": "sent"},
        actual={"org_id": after.get("organization_id"),
                "status": after.get("status")},
        evidence={"transfer_id": transfer_id},
    )
    assert after["organization_id"] == prev_org
    assert after["status"] == "sent"


# ═════════════════════════════════════════════════════════════════════
# Test 4 — /reject preserves organization_id
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_br_bt_org_4_reject_preserves_org(
    tenant, record_result
):
    transfer_id, prev_org = await _seed_pending_bto(tenant)
    owner = tenant["users"]["owner"]
    await reject_pending_transfer(transfer_id, {
        "reason": "test reject - org id lock",
        "organization_id": "evil-during-reject",  # rogue
    }, user=owner)
    after = await _bto_row(transfer_id)
    record_result(
        scenario="br_bt_org.4_reject_preserves_org",
        step="org_id_unchanged_after_reject",
        expected={"org_id": prev_org, "status": "returned"},
        actual={"org_id": after.get("organization_id"),
                "status": after.get("status")},
        evidence={"transfer_id": transfer_id},
    )
    assert after["organization_id"] == prev_org
    assert after["status"] == "returned"


# ═════════════════════════════════════════════════════════════════════
# Test 5 — /resubmit preserves organization_id
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_br_bt_org_5_resubmit_preserves_org(
    tenant, record_result
):
    transfer_id, prev_org = await _seed_pending_bto(tenant)
    owner = tenant["users"]["owner"]
    # First reject it so we can resubmit.
    await reject_pending_transfer(transfer_id, {
        "reason": "test reject before resubmit lock",
    }, user=owner)
    await resubmit_returned_transfer(transfer_id, user=owner)
    after = await _bto_row(transfer_id)
    record_result(
        scenario="br_bt_org.5_resubmit_preserves_org",
        step="org_id_unchanged_after_resubmit",
        expected={"org_id": prev_org, "status": "pending_approval"},
        actual={"org_id": after.get("organization_id"),
                "status": after.get("status")},
        evidence={"transfer_id": transfer_id},
    )
    assert after["organization_id"] == prev_org
    assert after["status"] == "pending_approval"


# ═════════════════════════════════════════════════════════════════════
# Test 6 — After a hostile update, BTO remains tenant-visible
#         (the actual business impact of an org_id wipe).
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_br_bt_org_6_remains_listed_after_hostile_update(
    tenant, record_result
):
    transfer_id, prev_org = await _seed_pending_bto(tenant)
    await _raw_db.branch_transfer_orders.update_one(
        {"id": transfer_id}, {"$set": {"status": "draft"}}
    )
    owner = tenant["users"]["owner"]
    await update_transfer(transfer_id, {
        "organization_id": "",
        "notes": "listing-visibility lock",
    }, user=owner)
    # list_transfers is tenant-scoped via the proxy. If org_id had been
    # blanked, this row would silently disappear from the org's view.
    res = await list_transfers(user=owner)
    rows = res.get("orders", []) if isinstance(res, dict) else res
    found = any(r.get("id") == transfer_id for r in rows)
    record_result(
        scenario="br_bt_org.6_still_listed_after_hostile_update",
        step="bto_remains_in_tenant_listing",
        expected={"visible": True},
        actual={"visible": found},
        evidence={"transfer_id": transfer_id, "n_rows": len(rows)},
    )
    assert found, "BTO must remain tenant-visible after a hostile PUT"

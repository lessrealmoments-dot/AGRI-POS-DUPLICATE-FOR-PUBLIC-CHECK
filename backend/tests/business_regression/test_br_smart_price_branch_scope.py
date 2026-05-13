"""br_smart_price_branch_scope — Smart Price Update writes to branch_prices
when branch_id is supplied; falls back to global product.prices when it
is not. No cross-branch contamination.

User-stated semantic: "Smart Scan and Price changes is only branch specific.
I don't want my PO affecting my other branches."

Locks:
  1. Update with branch_id writes to branch_prices for THAT branch and
     does NOT mutate product.prices (global).
  2. Update without branch_id writes to global product.prices.
  3. Multiple schemes merge — pre-existing branch override on wholesale
     is preserved when only retail is updated.
  4. Branch B's branch_prices is untouched when Branch A updates.
  5. Manager PIN rejected (admin/TOTP only) — same policy regardless of
     scope.
"""
import os
import sys

import pytest
import pytest_asyncio
from fastapi import HTTPException

BACKEND = os.path.join(os.path.dirname(__file__), "..", "..")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from config import _raw_db                                       # noqa: E402
from tests.phase2b._fixtures import _uid, fake_user              # noqa: E402
from routes.products import smart_price_update                   # noqa: E402


@pytest_asyncio.fixture(scope="module")
async def sps_admin(tenant):
    """Seed an admin + admin_pin + manager + manager_pin in the BR
    tenant. Smart Price Update accepts admin/TOTP only — manager PIN
    must be rejected at the verifier."""
    org_id = tenant["org_id"]
    main = tenant["branches"]["main"]
    admin_id = _uid("br_sps-admin")
    mgr_id = _uid("br_sps-mgr")
    admin_pin = "224488"
    mgr_pin = "990011"
    await _raw_db.users.insert_many([
        {"id": admin_id, "username": f"admin-{admin_id[-4:]}",
         "full_name": "BR-SPS Admin", "organization_id": org_id,
         "role": "admin", "active": True,
         "branch_ids": [main], "branch_id": main,
         "manager_pin": admin_pin},
        {"id": mgr_id, "username": f"mgr-{mgr_id[-4:]}",
         "full_name": "BR-SPS Manager", "organization_id": org_id,
         "role": "manager", "active": True,
         "branch_ids": [main], "branch_id": main,
         "manager_pin": mgr_pin},
    ])
    from utils.auth import hash_password
    await _raw_db.system_settings.update_one(
        {"organization_id": org_id, "key": "admin_pin"},
        {"$set": {"organization_id": org_id, "key": "admin_pin",
                  "pin_hash": hash_password(admin_pin),
                  "updated_at": "2026-01-01T00:00:00Z"}},
        upsert=True,
    )
    yield {
        "admin_user": fake_user(org_id, admin_id, branch_id=main, role="admin"),
        "mgr_user": fake_user(org_id, mgr_id, branch_id=main, role="manager"),
        "admin_pin": admin_pin,
        "mgr_pin": mgr_pin,
    }


async def _seed_product(org_id, *, name, prices, cost=50.0):
    pid = _uid("br_sps-p")
    await _raw_db.products.insert_one({
        "id": pid, "organization_id": org_id,
        "name": name, "sku": pid[-6:].upper(), "category": "Test",
        "unit": "pc", "active": True, "is_repack": False,
        "cost_price": cost, "prices": prices,
    })
    return pid


# ═════════════════════════════════════════════════════════════════════
# Test 1 — branch_id present → writes to branch_prices ONLY (no global)
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_br_sps_1_branch_id_writes_branch_prices_only(
    tenant, sps_admin, record_result
):
    org_id = tenant["org_id"]
    main = tenant["branches"]["main"]
    pid = await _seed_product(
        org_id, name="BR-SPS Branch Scope Item",
        prices={"retail": 100.0, "wholesale": 90.0},
    )
    res = await smart_price_update({
        "product_id": pid,
        "branch_id": main,
        "prices": {"retail": 125.0},
        "pin": sps_admin["admin_pin"],
    }, user=sps_admin["admin_user"])

    # Global must be UNTOUCHED.
    global_doc = await _raw_db.products.find_one(
        {"id": pid}, {"_id": 0, "prices": 1}
    )
    bp = await _raw_db.branch_prices.find_one(
        {"product_id": pid, "branch_id": main}, {"_id": 0, "prices": 1}
    )
    record_result(
        scenario="br_sps.1_branch_id_writes_branch_only",
        step="global_unchanged_branch_set",
        expected={
            "scope": "branch",
            "global_retail": 100.0,
            "branch_retail": 125.0,
        },
        actual={
            "scope": res.get("scope"),
            "global_retail": (global_doc.get("prices") or {}).get("retail"),
            "branch_retail": (bp or {}).get("prices", {}).get("retail"),
        },
        evidence={"product_id": pid},
    )
    assert res["scope"] == "branch"
    assert global_doc["prices"]["retail"] == 100.0  # global untouched
    assert bp is not None
    assert bp["prices"]["retail"] == 125.0


# ═════════════════════════════════════════════════════════════════════
# Test 2 — branch_id absent → writes to global product.prices
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_br_sps_2_no_branch_id_writes_global(
    tenant, sps_admin, record_result
):
    org_id = tenant["org_id"]
    pid = await _seed_product(
        org_id, name="BR-SPS Global Scope Item",
        prices={"retail": 200.0, "wholesale": 180.0},
    )
    res = await smart_price_update({
        "product_id": pid,
        # branch_id intentionally OMITTED — "All Branches" semantics.
        "prices": {"retail": 225.0},
        "pin": sps_admin["admin_pin"],
    }, user=sps_admin["admin_user"])
    global_doc = await _raw_db.products.find_one(
        {"id": pid}, {"_id": 0, "prices": 1}
    )
    record_result(
        scenario="br_sps.2_no_branch_id_writes_global",
        step="global_retail_updated",
        expected={"scope": "global", "global_retail": 225.0},
        actual={"scope": res.get("scope"),
                "global_retail": (global_doc.get("prices") or {}).get("retail")},
        evidence={"product_id": pid},
    )
    assert res["scope"] == "global"
    assert global_doc["prices"]["retail"] == 225.0
    # Wholesale must NOT be wiped (merge semantics).
    assert global_doc["prices"]["wholesale"] == 180.0


# ═════════════════════════════════════════════════════════════════════
# Test 3 — Branch-scope merges (existing wholesale override preserved
# when only retail is updated)
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_br_sps_3_branch_scope_merges_schemes(
    tenant, sps_admin, record_result
):
    org_id = tenant["org_id"]
    main = tenant["branches"]["main"]
    pid = await _seed_product(
        org_id, name="BR-SPS Merge Item",
        prices={"retail": 100.0, "wholesale": 90.0},
    )
    # Pre-existing branch override on wholesale only.
    await _raw_db.branch_prices.insert_one({
        "id": _uid("bp"), "organization_id": org_id,
        "product_id": pid, "branch_id": main,
        "prices": {"wholesale": 88.0},
        "updated_at": "2026-01-01T00:00:00Z",
    })
    await smart_price_update({
        "product_id": pid,
        "branch_id": main,
        "prices": {"retail": 110.0},
        "pin": sps_admin["admin_pin"],
    }, user=sps_admin["admin_user"])

    bp = await _raw_db.branch_prices.find_one(
        {"product_id": pid, "branch_id": main}, {"_id": 0, "prices": 1}
    )
    record_result(
        scenario="br_sps.3_branch_scope_merges",
        step="existing_wholesale_override_preserved",
        expected={"retail": 110.0, "wholesale": 88.0},
        actual={"retail": bp["prices"].get("retail"),
                "wholesale": bp["prices"].get("wholesale")},
        evidence={"product_id": pid},
    )
    assert bp["prices"]["retail"] == 110.0
    assert bp["prices"]["wholesale"] == 88.0


# ═════════════════════════════════════════════════════════════════════
# Test 4 — Branch B's branch_prices is untouched when Branch A updates
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_br_sps_4_no_cross_branch_contamination(
    tenant, sps_admin, record_result
):
    org_id = tenant["org_id"]
    main = tenant["branches"]["main"]
    b2 = tenant["branches"]["b2"]
    pid = await _seed_product(
        org_id, name="BR-SPS Cross-Branch Item",
        prices={"retail": 100.0, "wholesale": 90.0},
    )
    # Pre-seed branch B with its OWN distinct retail override.
    BRANCH_B_RETAIL = 175.0
    await _raw_db.branch_prices.insert_one({
        "id": _uid("bp"), "organization_id": org_id,
        "product_id": pid, "branch_id": b2,
        "prices": {"retail": BRANCH_B_RETAIL},
        "updated_at": "2026-01-01T00:00:00Z",
    })
    # Update via Smart Price Scan at Branch A (main).
    await smart_price_update({
        "product_id": pid,
        "branch_id": main,
        "prices": {"retail": 105.0},
        "pin": sps_admin["admin_pin"],
    }, user=sps_admin["admin_user"])

    bp_b = await _raw_db.branch_prices.find_one(
        {"product_id": pid, "branch_id": b2}, {"_id": 0, "prices": 1}
    )
    bp_main = await _raw_db.branch_prices.find_one(
        {"product_id": pid, "branch_id": main}, {"_id": 0, "prices": 1}
    )
    record_result(
        scenario="br_sps.4_no_cross_branch_contamination",
        step="branch_b_retail_unchanged_after_branch_a_update",
        expected={"branch_b_retail": BRANCH_B_RETAIL,
                  "branch_a_retail": 105.0},
        actual={"branch_b_retail": bp_b["prices"].get("retail"),
                "branch_a_retail": bp_main["prices"].get("retail")},
        evidence={"product_id": pid, "branch_a": main, "branch_b": b2},
    )
    assert bp_b["prices"]["retail"] == BRANCH_B_RETAIL
    assert bp_main["prices"]["retail"] == 105.0


# ═════════════════════════════════════════════════════════════════════
# Test 5 — Manager PIN rejected on branch-scoped update (admin/TOTP only)
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_br_sps_5_manager_pin_rejected_branch_scope(
    tenant, sps_admin, record_result
):
    org_id = tenant["org_id"]
    main = tenant["branches"]["main"]
    pid = await _seed_product(
        org_id, name="BR-SPS Manager Reject", prices={"retail": 100.0},
    )
    state = "ok"
    status = None
    try:
        await smart_price_update({
            "product_id": pid,
            "branch_id": main,
            "prices": {"retail": 999.0},
            "pin": sps_admin["mgr_pin"],
        }, user=sps_admin["mgr_user"])
    except HTTPException as e:
        state = "err"
        status = e.status_code
    record_result(
        scenario="br_sps.5_manager_pin_rejected",
        step="manager_pin_403_even_for_branch_scope",
        expected={"state": "err", "status": 403},
        actual={"state": state, "status": status},
        evidence={"product_id": pid},
    )
    assert state == "err"
    assert status == 403

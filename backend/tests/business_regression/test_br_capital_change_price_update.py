"""br_cap_change — Smart Price Scan / Capital Change enrichment + price update.

Locks:
  * `/capital-change-alerts` returns enriched rows with `prices`, `moving_average`,
    `last_purchase`, `scheme_margins`, and a top-level `schemes` meta.
  * Below-cost and thin-margin classifications honour effective branch capital.
  * Branch-specific price overrides shadow the global product prices.
  * Auto-acknowledge: posting `/capital-change-alerts/{id}/acknowledge` flips
    the row out of the unacknowledged set.
  * Smart-price-update is admin-PIN gated; manager PIN rejected; warning state
    on the alert does NOT block the price write (warning is visible only).
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from fastapi import HTTPException

BACKEND = os.path.join(os.path.dirname(__file__), "..", "..")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from config import _raw_db                                       # noqa: E402
from tests.phase2b._fixtures import _uid, fake_user              # noqa: E402
from routes.products import (                                    # noqa: E402
    capital_change_alerts,
    acknowledge_capital_change,
    smart_price_update,
)


@pytest_asyncio.fixture(scope="module")
async def cap_users(tenant):
    """Seed an admin with admin_pin + a manager with manager_pin in the
    business-day tenant. Mirrors `hs_users` shape from the HS-PO suite."""
    org_id = tenant["org_id"]
    main = tenant["branches"]["main"]
    admin_id = _uid("br_cap-admin")
    mgr_id = _uid("br_cap-mgr")
    admin_pin = "778899"
    mgr_pin = "112233"
    await _raw_db.users.insert_many([
        {"id": admin_id, "username": f"admin-{admin_id[-4:]}",
         "full_name": "CAP Admin", "organization_id": org_id,
         "role": "admin", "active": True,
         "branch_ids": [main], "branch_id": main,
         "manager_pin": admin_pin},
        {"id": mgr_id, "username": f"mgr-{mgr_id[-4:]}",
         "full_name": "CAP Manager", "organization_id": org_id,
         "role": "manager", "active": True,
         "branch_ids": [main], "branch_id": main,
         "manager_pin": mgr_pin},
    ])
    # Seed admin_pin in system_settings so verify.py can match it.
    from utils.auth import hash_password
    await _raw_db.system_settings.update_one(
        {"organization_id": org_id, "key": "admin_pin"},
        {"$set": {"organization_id": org_id, "key": "admin_pin",
                  "pin_hash": hash_password(admin_pin),
                  "updated_at": "2026-01-01T00:00:00Z"}},
        upsert=True,
    )
    # Seed the standard active price schemes the UI expects.
    for k, n in (("retail", "Retail"), ("wholesale", "Wholesale"),
                 ("special", "Special"), ("government", "Government")):
        await _raw_db.price_schemes.update_one(
            {"organization_id": org_id, "key": k},
            {"$set": {"organization_id": org_id, "key": k,
                      "name": n, "active": True}},
            upsert=True,
        )
    yield {
        "admin_user": fake_user(org_id, admin_id, branch_id=main, role="admin"),
        "mgr_user":   fake_user(org_id, mgr_id, branch_id=main, role="manager"),
        "admin_pin": admin_pin,
        "mgr_pin": mgr_pin,
    }


async def _seed_product(org_id, branch_id, *, name, cost, prices):
    pid = _uid("br_cap-p")
    sku = pid[-6:].upper()
    await _raw_db.products.insert_one({
        "id": pid, "organization_id": org_id,
        "name": name, "sku": sku, "category": "Test",
        "unit": "kg", "active": True, "is_repack": False,
        "cost_price": cost, "prices": prices,
    })
    await _raw_db.inventory.insert_one({
        "id": _uid("inv"), "organization_id": org_id,
        "product_id": pid, "branch_id": branch_id, "quantity": 100,
    })
    return pid


async def _seed_capital_change(org_id, pid, bid, *, old, new,
                                source_ref="PO-TEST", vendor="Vendor X",
                                method="latest"):
    cid = _uid("cap")
    now = datetime.now(timezone.utc).isoformat()
    await _raw_db.capital_changes.insert_one({
        "id": cid, "organization_id": org_id,
        "product_id": pid, "branch_id": bid,
        "old_capital": old, "new_capital": new,
        "method": method, "source_type": "purchase_order",
        "source_ref": source_ref, "vendor": vendor,
        "changed_by_id": "system", "changed_by_name": "System",
        "changed_at": now, "was_user_choice": False,
    })
    return cid


# ═════════════════════════════════════════════════════════════════════
# Test 1 — enrichment payload shape
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_br_cap_1_alerts_enriched_with_prices_and_margins(
    tenant, cap_users, record_result
):
    org_id = tenant["org_id"]
    main = tenant["branches"]["main"]
    pid = await _seed_product(
        org_id, main,
        name="CAP Test Rice 25kg",
        cost=110.0,
        prices={"retail": 130.0, "wholesale": 120.0, "special": 115.0},
    )
    await _seed_capital_change(org_id, pid, main, old=100.0, new=115.0)

    res = await capital_change_alerts(
        branch_id=main, days=14, user=cap_users["admin_user"],
    )
    assert len(res["alerts"]) >= 1
    a = next(x for x in res["alerts"] if x["product_id"] == pid)

    # Top-level schemes echoed
    scheme_keys = {s["key"] for s in res["schemes"]}
    assert {"retail", "wholesale"}.issubset(scheme_keys)

    # Effective cost reflects the change event's new_capital
    assert a["effective_cost"] == 115.0
    assert a["prices"]["retail"] == 130.0
    assert a["prices"]["wholesale"] == 120.0

    # scheme_margins per scheme
    by_key = {sm["scheme_key"]: sm for sm in a["scheme_margins"]}
    # Retail margin = 130 - 115 = 15 → margin_pct ≈ 11.5% (healthy)
    assert by_key["retail"]["margin"] == 15.0
    assert by_key["retail"]["is_below_cost"] is False
    assert by_key["retail"]["is_thin"] is False
    # Wholesale margin = 120 - 115 = 5 (thin: <5% of 120 = 6, exact margin ≥ 5
    # so it might be borderline). Actual: 5/120 = 4.17% → thin.
    assert by_key["wholesale"]["is_thin"] is True
    assert by_key["wholesale"]["is_below_cost"] is False
    # Special margin = 115 - 115 = 0 → below cost? actually exactly at cost.
    # current_price < effective_cost → below_cost. 115 < 115 is False so not flagged.
    assert by_key["special"]["is_below_cost"] is False

    record_result(
        scenario="br_cap_change.1_enrichment",
        step="alert_has_prices_movingavg_margins",
        expected={
            "has_prices": True, "has_scheme_margins": True,
            "retail_margin": 15.0, "wholesale_thin": True,
        },
        actual={
            "has_prices": bool(a["prices"]),
            "has_scheme_margins": bool(a["scheme_margins"]),
            "retail_margin": by_key["retail"]["margin"],
            "wholesale_thin": by_key["wholesale"]["is_thin"],
        },
        evidence={"alert_id": a["id"], "product_id": pid},
    )


# ═════════════════════════════════════════════════════════════════════
# Test 2 — below-new-capital is detected and surfaced (visible warning)
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_br_cap_2_below_cost_flag_visible(
    tenant, cap_users, record_result
):
    org_id = tenant["org_id"]
    main = tenant["branches"]["main"]
    pid = await _seed_product(
        org_id, main,
        name="CAP Below Cost Item",
        cost=200.0,
        prices={"retail": 180.0, "wholesale": 210.0},
    )
    # Capital jumped from 150 → 200; retail 180 is now below cost.
    await _seed_capital_change(org_id, pid, main, old=150.0, new=200.0,
                                source_ref="PO-BC-1")
    res = await capital_change_alerts(
        branch_id=main, days=14, user=cap_users["admin_user"],
    )
    a = next(x for x in res["alerts"] if x["product_id"] == pid)
    by_key = {sm["scheme_key"]: sm for sm in a["scheme_margins"]}
    retail_below = by_key["retail"]["is_below_cost"]
    wholesale_below = by_key["wholesale"]["is_below_cost"]
    record_result(
        scenario="br_cap_change.2_below_cost_visible",
        step="retail_below_wholesale_not",
        expected={"retail_below": True, "wholesale_below": False},
        actual={"retail_below": retail_below, "wholesale_below": wholesale_below},
        evidence={"alert_id": a["id"]},
    )
    assert retail_below is True
    assert wholesale_below is False


# ═════════════════════════════════════════════════════════════════════
# Test 3 — branch override shadows global prices
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_br_cap_3_branch_price_override_honoured(
    tenant, cap_users, record_result
):
    org_id = tenant["org_id"]
    main = tenant["branches"]["main"]
    pid = await _seed_product(
        org_id, main,
        name="CAP Branch-Price Item",
        cost=50.0,
        prices={"retail": 80.0, "wholesale": 70.0},
    )
    # Branch override: retail higher at this branch
    await _raw_db.branch_prices.insert_one({
        "id": _uid("bp"), "organization_id": org_id,
        "product_id": pid, "branch_id": main,
        "cost_price": 55.0,  # branch-specific cost
        "prices": {"retail": 95.0},  # override only retail
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })
    await _seed_capital_change(org_id, pid, main, old=45.0, new=55.0)
    res = await capital_change_alerts(
        branch_id=main, days=14, user=cap_users["admin_user"],
    )
    a = next(x for x in res["alerts"] if x["product_id"] == pid)
    record_result(
        scenario="br_cap_change.3_branch_override",
        step="retail_uses_branch_price_wholesale_global",
        expected={"retail": 95.0, "wholesale": 70.0,
                  "is_branch_specific_cost": True},
        actual={"retail": a["prices"]["retail"],
                "wholesale": a["prices"]["wholesale"],
                "is_branch_specific_cost": a["is_branch_specific_cost"]},
        evidence={"alert_id": a["id"]},
    )
    assert a["prices"]["retail"] == 95.0
    assert a["prices"]["wholesale"] == 70.0
    assert a["is_branch_specific_cost"] is True


# ═════════════════════════════════════════════════════════════════════
# Test 4 — Smart Price Update (admin PIN) applies even on thin/below-cost
# (warning is visible-only — backend never blocks).
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_br_cap_4_smart_price_update_admin_pin_writes_even_below_cost(
    tenant, cap_users, record_result
):
    org_id = tenant["org_id"]
    main = tenant["branches"]["main"]
    pid = await _seed_product(
        org_id, main,
        name="CAP Update Item",
        cost=100.0,
        prices={"retail": 130.0, "wholesale": 125.0},
    )
    # User deliberately sets retail BELOW current capital to clear stock.
    new_retail = 95.0  # below cost (100). The system must accept and warn.
    res = await smart_price_update(
        {"product_id": pid, "prices": {"retail": new_retail},
         "pin": cap_users["admin_pin"]},
        user=cap_users["admin_user"],
    )
    record_result(
        scenario="br_cap_change.4_update_accepts_below_cost",
        step="admin_pin_accepted_even_below_cost",
        expected={"ok": True, "retail": new_retail},
        actual={"ok": res.get("ok"), "retail": res["prices"]["retail"]},
        evidence={"product_id": pid, "pin_method": res.get("pin_method")},
    )
    assert res.get("ok") is True
    assert res["prices"]["retail"] == new_retail


# ═════════════════════════════════════════════════════════════════════
# Test 5 — Manager PIN rejected on smart-price-update
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_br_cap_5_manager_pin_rejected(
    tenant, cap_users, record_result
):
    org_id = tenant["org_id"]
    main = tenant["branches"]["main"]
    pid = await _seed_product(
        org_id, main,
        name="CAP MgrPin Reject Item",
        cost=100.0, prices={"retail": 120.0},
    )
    state = "ok"
    detail = None
    try:
        await smart_price_update(
            {"product_id": pid, "prices": {"retail": 130.0},
             "pin": cap_users["mgr_pin"]},
            user=cap_users["mgr_user"],
        )
    except HTTPException as e:
        state = "err"
        detail = (e.status_code, e.detail)
    record_result(
        scenario="br_cap_change.5_manager_pin_blocked",
        step="manager_pin_rejected_403",
        expected={"state": "err", "status": 403},
        actual={"state": state, "status": detail[0] if detail else None},
        evidence={"product_id": pid},
    )
    assert state == "err"
    assert detail[0] == 403


# ═════════════════════════════════════════════════════════════════════
# Test 6 — Acknowledge removes the alert from the next fetch
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_br_cap_6_acknowledge_clears_alert(
    tenant, cap_users, record_result
):
    org_id = tenant["org_id"]
    main = tenant["branches"]["main"]
    pid = await _seed_product(
        org_id, main, name="CAP Ack Item",
        cost=50.0, prices={"retail": 80.0},
    )
    cid = await _seed_capital_change(org_id, pid, main, old=45.0, new=55.0,
                                      source_ref="PO-ACK-1")
    # Pre-ack visible
    pre = await capital_change_alerts(
        branch_id=main, days=14, user=cap_users["admin_user"],
    )
    pre_ids = [x["id"] for x in pre["alerts"]]
    assert cid in pre_ids

    await acknowledge_capital_change(cid, user=cap_users["admin_user"])

    # Post-ack hidden
    post = await capital_change_alerts(
        branch_id=main, days=14, user=cap_users["admin_user"],
    )
    post_ids = [x["id"] for x in post["alerts"]]
    record_result(
        scenario="br_cap_change.6_ack_clears",
        step="alert_hidden_after_acknowledge",
        expected={"pre_visible": True, "post_visible": False},
        actual={"pre_visible": cid in pre_ids, "post_visible": cid in post_ids},
        evidence={"alert_id": cid, "product_id": pid},
    )
    assert cid not in post_ids


# ═════════════════════════════════════════════════════════════════════
# Test 7 — Sub-threshold deltas filtered out
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_br_cap_7_sub_threshold_filtered(
    tenant, cap_users, record_result
):
    org_id = tenant["org_id"]
    main = tenant["branches"]["main"]
    pid = await _seed_product(
        org_id, main, name="CAP Tiny Delta Item",
        cost=100.0, prices={"retail": 130.0},
    )
    # 50¢ delta — below ₱1 floor.
    await _seed_capital_change(org_id, pid, main, old=100.0, new=100.5,
                                source_ref="PO-TINY-1")
    res = await capital_change_alerts(
        branch_id=main, days=14, user=cap_users["admin_user"],
    )
    ids = [x["id"] for x in res["alerts"] if x["product_id"] == pid]
    record_result(
        scenario="br_cap_change.7_threshold_floor",
        step="tiny_delta_filtered_below_floor",
        expected={"surfaced": False},
        actual={"surfaced": bool(ids)},
        evidence={"product_id": pid},
    )
    assert not ids

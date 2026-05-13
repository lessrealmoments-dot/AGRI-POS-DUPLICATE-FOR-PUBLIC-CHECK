"""br_cap_report — Capital Change Report (printable per-product cost history).

Locks the contract for `/api/products/capital-change-report`:

  * Returns products grouped by category, only products with ≥1 change.
  * Each product carries chronological `changes[]` (oldest → newest) with
    `direction` (up/down/flat) computed from old_capital vs new_capital
    per record.
  * `by_month` buckets group changes by YYYY-MM key.
  * `net_delta` / `net_delta_pct` are computed against the first change's
    old_capital (the "start" capital at the window edge).
  * Default `source_type` is `purchase_order`: manual edits are excluded
    unless the caller explicitly sets `source_type=manual_edit` or `all`.
  * Range cut-off honours window: rows older than the range are excluded.
  * Branch filter scopes to that branch only.
  * Search filter matches name OR sku, case-insensitive.
  * Summary aggregates totals correctly.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

BACKEND = os.path.join(os.path.dirname(__file__), "..", "..")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from config import _raw_db                                       # noqa: E402
from tests.phase2b._fixtures import _uid, fake_user              # noqa: E402
from routes.products import capital_change_report               # noqa: E402


@pytest_asyncio.fixture(scope="module")
async def report_user(tenant):
    org_id = tenant["org_id"]
    main = tenant["branches"]["main"]
    uid = _uid("br_cap_rep-admin")
    await _raw_db.users.insert_one({
        "id": uid, "username": f"adm-{uid[-4:]}",
        "full_name": "CapReport Admin", "organization_id": org_id,
        "role": "admin", "active": True,
        "branch_ids": [main], "branch_id": main,
    })
    return fake_user(org_id, uid, branch_id=main, role="admin")


async def _seed_product(org_id, *, name, cost, category="Grains", sku=None):
    pid = _uid("br_cap_rep-p")
    await _raw_db.products.insert_one({
        "id": pid, "organization_id": org_id,
        "name": name, "sku": sku or pid[-6:].upper(),
        "category": category, "unit": "kg", "active": True,
        "is_repack": False, "cost_price": cost, "prices": {"retail": cost + 20},
    })
    return pid


async def _seed_change(org_id, pid, branch_id, *, old, new,
                       days_ago=1, source_type="purchase_order",
                       source_ref="PO-CR-1", vendor="Vendor A"):
    cid = _uid("cap_chg")
    when = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    await _raw_db.capital_changes.insert_one({
        "id": cid, "organization_id": org_id,
        "product_id": pid, "branch_id": branch_id,
        "old_capital": old, "new_capital": new,
        "method": "latest", "source_type": source_type,
        "source_ref": source_ref, "vendor": vendor,
        "changed_by_id": "system", "changed_by_name": "System",
        "changed_at": when,
    })
    return cid


# ═════════════════════════════════════════════════════════════════════
# Test 1 — Basic shape + direction + net delta
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_br_cap_rep_1_basic_shape_and_directions(
    tenant, report_user, record_result
):
    org_id = tenant["org_id"]
    main = tenant["branches"]["main"]
    pid = await _seed_product(org_id, name="REP Rice 25kg",
                              cost=200.0, category="Grains")
    # 4 changes over the last 12 days: 200 → 240 (up) → 230 (down)
    #                                  → 250 (up)  → 245 (down)
    await _seed_change(org_id, pid, main, old=200, new=240, days_ago=12)
    await _seed_change(org_id, pid, main, old=240, new=230, days_ago=9)
    await _seed_change(org_id, pid, main, old=230, new=250, days_ago=5)
    await _seed_change(org_id, pid, main, old=250, new=245, days_ago=2)

    res = await capital_change_report(range="30d", user=report_user)

    cats = {c["category"]: c for c in res["categories"]}
    assert "Grains" in cats
    prod_entries = {p["product_id"]: p for p in cats["Grains"]["products"]}
    assert pid in prod_entries
    p = prod_entries[pid]
    dirs = [c["direction"] for c in p["changes"]]
    record_result(
        scenario="br_cap_report.1_basic_shape",
        step="changes_sorted_with_correct_directions",
        expected={
            "change_count": 4,
            "directions": ["up", "down", "up", "down"],
            "first_capital": 200.0,
            "current_capital": 245.0,
            "net_delta": 45.0,
            "increases": 2,
            "decreases": 2,
        },
        actual={
            "change_count": p["change_count"],
            "directions": dirs,
            "first_capital": p["first_capital"],
            "current_capital": p["current_capital"],
            "net_delta": p["net_delta"],
            "increases": p["increases"],
            "decreases": p["decreases"],
        },
        evidence={"product_id": pid},
    )
    assert p["change_count"] == 4
    assert dirs == ["up", "down", "up", "down"]
    assert p["first_capital"] == 200.0
    assert p["current_capital"] == 245.0
    assert p["net_delta"] == 45.0
    assert p["increases"] == 2
    assert p["decreases"] == 2


# ═════════════════════════════════════════════════════════════════════
# Test 2 — Range filter excludes older changes
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_br_cap_rep_2_range_filter_excludes_old(
    tenant, report_user, record_result
):
    org_id = tenant["org_id"]
    main = tenant["branches"]["main"]
    pid = await _seed_product(org_id, name="REP RangeTest", cost=100.0,
                              category="RangeCat")
    await _seed_change(org_id, pid, main, old=100, new=110, days_ago=45,
                       source_ref="PO-OLD")
    await _seed_change(org_id, pid, main, old=110, new=115, days_ago=10,
                       source_ref="PO-RECENT")

    # 30d range: only the recent change should show up
    res30 = await capital_change_report(range="30d", user=report_user)
    p30 = None
    for c in res30["categories"]:
        for pr in c["products"]:
            if pr["product_id"] == pid:
                p30 = pr
    assert p30 is not None
    count_30 = p30["change_count"]
    first_30 = p30["first_capital"]

    # 60d range: both changes should show up
    res60 = await capital_change_report(range="60d", user=report_user)
    p60 = None
    for c in res60["categories"]:
        for pr in c["products"]:
            if pr["product_id"] == pid:
                p60 = pr
    assert p60 is not None

    record_result(
        scenario="br_cap_report.2_range_filter",
        step="30d_excludes_old_60d_includes_both",
        expected={"count_30": 1, "first_30": 110.0, "count_60": 2,
                  "first_60": 100.0},
        actual={"count_30": count_30, "first_30": first_30,
                "count_60": p60["change_count"],
                "first_60": p60["first_capital"]},
        evidence={"product_id": pid},
    )
    assert count_30 == 1
    assert first_30 == 110.0
    assert p60["change_count"] == 2
    assert p60["first_capital"] == 100.0


# ═════════════════════════════════════════════════════════════════════
# Test 3 — Source-type filter (default = purchase_order)
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_br_cap_rep_3_source_type_filter(
    tenant, report_user, record_result
):
    org_id = tenant["org_id"]
    main = tenant["branches"]["main"]
    pid = await _seed_product(org_id, name="REP SourceTest", cost=50.0,
                              category="SourceCat")
    await _seed_change(org_id, pid, main, old=50, new=55, days_ago=5,
                       source_type="purchase_order", source_ref="PO-S")
    await _seed_change(org_id, pid, main, old=55, new=60, days_ago=3,
                       source_type="manual_edit", source_ref="")

    # Default = purchase_order → only the PO change
    res_po = await capital_change_report(range="30d", user=report_user)
    po_prod = None
    for c in res_po["categories"]:
        for pr in c["products"]:
            if pr["product_id"] == pid:
                po_prod = pr
    assert po_prod is not None
    po_count = po_prod["change_count"]

    # source_type=all → both
    res_all = await capital_change_report(range="30d", source_type="all",
                                          user=report_user)
    all_prod = None
    for c in res_all["categories"]:
        for pr in c["products"]:
            if pr["product_id"] == pid:
                all_prod = pr
    assert all_prod is not None

    # source_type=manual_edit → only manual
    res_man = await capital_change_report(range="30d", source_type="manual_edit",
                                          user=report_user)
    man_prod = None
    for c in res_man["categories"]:
        for pr in c["products"]:
            if pr["product_id"] == pid:
                man_prod = pr
    assert man_prod is not None

    record_result(
        scenario="br_cap_report.3_source_type_filter",
        step="default_po_only_all_includes_both_manual_only_excludes_po",
        expected={"po_count": 1, "all_count": 2, "manual_count": 1},
        actual={"po_count": po_count,
                "all_count": all_prod["change_count"],
                "manual_count": man_prod["change_count"]},
        evidence={"product_id": pid},
    )
    assert po_count == 1
    assert all_prod["change_count"] == 2
    assert man_prod["change_count"] == 1


# ═════════════════════════════════════════════════════════════════════
# Test 4 — Category filter
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_br_cap_rep_4_category_filter(
    tenant, report_user, record_result
):
    org_id = tenant["org_id"]
    main = tenant["branches"]["main"]
    pid_a = await _seed_product(org_id, name="REP CatA Item", cost=10.0,
                                category="CatA")
    pid_b = await _seed_product(org_id, name="REP CatB Item", cost=10.0,
                                category="CatB")
    await _seed_change(org_id, pid_a, main, old=10, new=12, days_ago=2,
                       source_ref="PO-CAT-A")
    await _seed_change(org_id, pid_b, main, old=10, new=12, days_ago=2,
                       source_ref="PO-CAT-B")
    res = await capital_change_report(range="30d", category="CatA",
                                      user=report_user)
    seen_cats = {c["category"] for c in res["categories"]}
    record_result(
        scenario="br_cap_report.4_category_filter",
        step="only_filtered_category_returned",
        expected={"contains_CatA": True, "contains_CatB": False},
        actual={"contains_CatA": "CatA" in seen_cats,
                "contains_CatB": "CatB" in seen_cats},
        evidence={"seen_cats": list(seen_cats)},
    )
    assert "CatA" in seen_cats
    assert "CatB" not in seen_cats


# ═════════════════════════════════════════════════════════════════════
# Test 5 — Search filter (name OR sku, case-insensitive)
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_br_cap_rep_5_search_filter(
    tenant, report_user, record_result
):
    org_id = tenant["org_id"]
    main = tenant["branches"]["main"]
    pid_x = await _seed_product(org_id, name="REP Magic Token Item",
                                cost=10.0, category="SearchCat",
                                sku="MAGIC1")
    pid_y = await _seed_product(org_id, name="REP Normal Item", cost=10.0,
                                category="SearchCat", sku="NORM1")
    await _seed_change(org_id, pid_x, main, old=10, new=11, days_ago=2)
    await _seed_change(org_id, pid_y, main, old=10, new=11, days_ago=2)

    res = await capital_change_report(range="30d", search="magic",
                                      user=report_user)
    found_pids = {p["product_id"]
                  for c in res["categories"] for p in c["products"]}
    record_result(
        scenario="br_cap_report.5_search_filter",
        step="search_matches_name_excludes_other",
        expected={"x_found": True, "y_found": False},
        actual={"x_found": pid_x in found_pids,
                "y_found": pid_y in found_pids},
        evidence={"hits": list(found_pids)},
    )
    assert pid_x in found_pids
    assert pid_y not in found_pids


# ═════════════════════════════════════════════════════════════════════
# Test 6 — by_month bucket key shape
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_br_cap_rep_6_by_month_bucket(
    tenant, report_user, record_result
):
    org_id = tenant["org_id"]
    main = tenant["branches"]["main"]
    pid = await _seed_product(org_id, name="REP MonthBucket Item",
                              cost=100.0, category="BucketCat")
    # Two changes in same month
    await _seed_change(org_id, pid, main, old=100, new=105, days_ago=4)
    await _seed_change(org_id, pid, main, old=105, new=110, days_ago=2)

    res = await capital_change_report(range="30d", user=report_user)
    target = None
    for c in res["categories"]:
        for pr in c["products"]:
            if pr["product_id"] == pid:
                target = pr
    assert target is not None
    months = list(target["by_month"].keys())
    # Each month key is YYYY-MM (7 chars)
    valid = all(len(m) == 7 and m[4] == "-" for m in months)
    total_in_buckets = sum(len(v) for v in target["by_month"].values())
    record_result(
        scenario="br_cap_report.6_by_month_shape",
        step="month_keys_valid_and_count_matches",
        expected={"valid_keys": True,
                  "total_in_buckets": target["change_count"]},
        actual={"valid_keys": valid,
                "total_in_buckets": total_in_buckets},
        evidence={"months": months},
    )
    assert valid
    assert total_in_buckets == target["change_count"]

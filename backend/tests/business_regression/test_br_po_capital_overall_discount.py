"""
br_po_capital_overall_discount — pins the Feb 2026 fix where the
*overall* PO discount only hit AP, leaving each line's `cost_price`
stamped at the pre-overall-discount price.

User pattern: "On our PO we have an overall discount separate from the
per-line discount. Also our supplier does 10+1 free (encoded as a
second line with 100% off)."

The fix prorates `overall_discount_amount` proportionally by each
line's contribution to `line_subtotal` (GAAP landed-cost allocation)
and folds the share into `cost_price` alongside the per-line discount.

Scenarios pinned:
  1. Pure overall discount, mixed-value basket → each line absorbs a
     proportional share.
  2. Combined per-line + overall discount.
  3. "10+1 free" encoded as 2 lines → moving-average lands at ~₱90.91.
  4. "10+1 free" + per-line disc → moving-average lands at ~₱81.82.
  5. capital-preview matches what apply will commit (preview/apply
     agreement, same as line-discount fix).
  6. Backfill endpoint is idempotent on already-correct rows.
"""
import uuid

import pytest

from config import _raw_db
from routes.purchase_orders import (
    _apply_po_inventory,
    _effective_unit_cost,
    backfill_overall_discount_capital,
    get_capital_preview,
)


# ─────────────────────────────────────────────────────────────────────
# Seed helpers
# ─────────────────────────────────────────────────────────────────────
async def _seed_product(*, org_id, pid, name="Test Product", initial_cost=0.0):
    await _raw_db.products.update_one(
        {"id": pid},
        {"$set": {
            "id":              pid,
            "organization_id": org_id,
            "name":            name,
            "sku":             f"SKU-{pid[-4:].upper()}",
            "cost_price":      initial_cost,
            "active":          True,
        }},
        upsert=True,
    )


async def _seed_po(*, org_id, branch_id, lines, overall_disc=0.0,
                   status="received"):
    """`lines` = list of {pid, qty, unit_price, line_disc?}.

    Returns the inserted PO doc (with `line_subtotal`, `grand_total`,
    `overall_discount_amount` pre-computed the same way the create
    endpoint does).
    """
    items = []
    line_subtotal = 0.0
    for line in lines:
        qty = float(line["qty"])
        up = float(line["unit_price"])
        ld = float(line.get("line_disc", 0))
        total = round(qty * up - ld, 2)
        items.append({
            "product_id":      line["pid"],
            "product_name":    line.get("name", "Test Product"),
            "quantity":        qty,
            "unit_price":      up,
            "discount_amount": ld,
            "total":           total,
            "unit":            "pc",
        })
        line_subtotal += total

    overall_disc = round(float(overall_disc), 2)
    grand_total = round(line_subtotal - overall_disc, 2)
    po_id = f"po-ocap-{uuid.uuid4().hex[:8]}"
    po = {
        "id":                       po_id,
        "organization_id":          org_id,
        "branch_id":                branch_id,
        "po_number":                f"PO-{po_id[-6:].upper()}",
        "vendor":                   "Acme Suppliers",
        "status":                   status,
        "items":                    items,
        "line_subtotal":            round(line_subtotal, 2),
        "subtotal":                 round(line_subtotal, 2),
        "overall_discount_type":    "amount",
        "overall_discount_value":   overall_disc,
        "overall_discount_amount":  overall_disc,
        "grand_total":              grand_total,
    }
    await _raw_db.purchase_orders.insert_one(po)
    return po


async def _branch_cost(*, pid, branch_id):
    bp = await _raw_db.branch_prices.find_one(
        {"product_id": pid, "branch_id": branch_id}, {"_id": 0}
    )
    return float(bp["cost_price"]) if bp and bp.get("cost_price") is not None else None


# ─────────────────────────────────────────────────────────────────────
# Test 1 — Pure overall discount, mixed-value basket
#   Premium: 1 × ₱10,000 = ₱10,000 (line value)
#   Cheap:   10 × ₱100   = ₱1,000  (line value)
#   Overall disc:                     ₱1,100  (10% of subtotal)
#   Premium share = 10000/11000 × 1100 = ₱1,000 → eff = 9000 / 1  = 9000
#   Cheap   share =  1000/11000 × 1100 = ₱100   → eff =  900 / 10 = 90
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_ocap_1_proportional_share(tenant, record_result):
    org_id = tenant["org_id"]
    branch_id = tenant["branches"]["main"]
    pid_premium = "prod-ocap1-premium"
    pid_cheap = "prod-ocap1-cheap"
    await _seed_product(org_id=org_id, pid=pid_premium, name="Premium")
    await _seed_product(org_id=org_id, pid=pid_cheap, name="Cheap")

    po = await _seed_po(
        org_id=org_id, branch_id=branch_id,
        lines=[
            {"pid": pid_premium, "qty": 1,  "unit_price": 10000.0},
            {"pid": pid_cheap,   "qty": 10, "unit_price": 100.0},
        ],
        overall_disc=1100.0,
    )

    await _apply_po_inventory(po, user=tenant["users"]["owner"])

    premium_cost = await _branch_cost(pid=pid_premium, branch_id=branch_id)
    cheap_cost = await _branch_cost(pid=pid_cheap, branch_id=branch_id)

    record_result(
        scenario="br_po_ocap.1_proportional",
        step="overall_disc_distributed_by_value",
        expected={"premium": 9000.0, "cheap": 90.0},
        actual={"premium": premium_cost, "cheap": cheap_cost},
    )
    assert premium_cost == 9000.0, f"Premium expected 9000, got {premium_cost!r}"
    assert cheap_cost == 90.0,    f"Cheap expected 90, got {cheap_cost!r}"


# ─────────────────────────────────────────────────────────────────────
# Test 2 — Combined per-line + overall discount.
#   Line: 10 × ₱100 less ₱10/pc per-line disc = total ₱900
#   Overall disc: ₱100 on subtotal ₱900 → line share = ₱100
#   Effective total = 900 - 100 = 800 → per-unit = 80.
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_ocap_2_line_plus_overall(tenant, record_result):
    org_id = tenant["org_id"]
    branch_id = tenant["branches"]["main"]
    pid = "prod-ocap2"
    await _seed_product(org_id=org_id, pid=pid)

    po = await _seed_po(
        org_id=org_id, branch_id=branch_id,
        lines=[{"pid": pid, "qty": 10, "unit_price": 100.0, "line_disc": 100.0}],
        overall_disc=100.0,
    )

    await _apply_po_inventory(po, user=tenant["users"]["owner"])

    cost = await _branch_cost(pid=pid, branch_id=branch_id)
    record_result(
        scenario="br_po_ocap.2_line_plus_overall",
        step="both_discounts_compose",
        expected={"cost_price": 80.0},
        actual={"cost_price": cost},
    )
    assert cost == 80.0, f"Expected 80 (line+overall disc), got {cost!r}"


# ─────────────────────────────────────────────────────────────────────
# Test 3 — "10 + 1 free" encoded as 2 lines, NO overall discount.
#   Line 1: 10 × 100 = 1000 (cost_price stamped at 100)
#   Line 2:  1 × 100 less ₱100 disc = 0 (cost_price stamped at 0)
#   Moving average across both lines: (10×100 + 1×0) / 11 = 90.909…
#   Final branch cost (last apply wins → choice=last_purchase): 0, BUT
#   the moving-average snapshot stored on the product MUST be ≈90.91.
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_ocap_3_ten_plus_one_free(tenant, record_result):
    org_id = tenant["org_id"]
    branch_id = tenant["branches"]["main"]
    pid = "prod-ocap3-tplus1"
    await _seed_product(org_id=org_id, pid=pid)

    po = await _seed_po(
        org_id=org_id, branch_id=branch_id,
        lines=[
            {"pid": pid, "qty": 10, "unit_price": 100.0},
            {"pid": pid, "qty": 1,  "unit_price": 100.0, "line_disc": 100.0},
        ],
    )

    await _apply_po_inventory(po, user=tenant["users"]["owner"])

    product = await _raw_db.products.find_one({"id": pid}, {"_id": 0})
    moving_avg = round(float(product.get("moving_average_cost") or 0), 2)

    record_result(
        scenario="br_po_ocap.3_ten_plus_one_free",
        step="moving_avg_pulls_to_90_91",
        expected={"moving_avg": 90.91},
        actual={"moving_avg": moving_avg},
    )
    assert moving_avg == 90.91, f"Expected moving_avg=90.91, got {moving_avg!r}"


# ─────────────────────────────────────────────────────────────────────
# Test 4 — "10 + 1 free" + per-line disc on line 1.
#   Line 1: 10 × (100 less ₱10/pc) = ₱900 → cost_price 90
#   Line 2:  1 × 100 less ₱100      = ₱0   → cost_price 0
#   Moving avg = (10×90 + 1×0) / 11 = 81.818…
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_ocap_4_ten_plus_one_with_line_disc(tenant, record_result):
    org_id = tenant["org_id"]
    branch_id = tenant["branches"]["main"]
    pid = "prod-ocap4-tplus1d"
    await _seed_product(org_id=org_id, pid=pid)

    po = await _seed_po(
        org_id=org_id, branch_id=branch_id,
        lines=[
            {"pid": pid, "qty": 10, "unit_price": 100.0, "line_disc": 100.0},
            {"pid": pid, "qty": 1,  "unit_price": 100.0, "line_disc": 100.0},
        ],
    )

    await _apply_po_inventory(po, user=tenant["users"]["owner"])

    product = await _raw_db.products.find_one({"id": pid}, {"_id": 0})
    moving_avg = round(float(product.get("moving_average_cost") or 0), 2)

    record_result(
        scenario="br_po_ocap.4_ten_plus_one_with_line_disc",
        step="moving_avg_pulls_to_81_82",
        expected={"moving_avg": 81.82},
        actual={"moving_avg": moving_avg},
    )
    assert moving_avg == 81.82, f"Expected moving_avg=81.82, got {moving_avg!r}"


# ─────────────────────────────────────────────────────────────────────
# Test 5 — capital-preview shows the SAME effective_unit `_apply_po_inventory`
# will commit, including the overall-discount share. Pre-fix the preview
# only handled per-line disc and would mislead the user when both
# discounts were present.
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_ocap_5_preview_matches_apply(tenant, record_result):
    org_id = tenant["org_id"]
    branch_id = tenant["branches"]["main"]
    pid = "prod-ocap5"
    await _seed_product(org_id=org_id, pid=pid, initial_cost=500.0)

    po = await _seed_po(
        org_id=org_id, branch_id=branch_id,
        lines=[
            {"pid": pid, "qty": 10, "unit_price": 100.0, "line_disc": 100.0},
        ],
        overall_disc=100.0,
        status="ordered",  # preview is typically called pre-receive
    )

    preview = await get_capital_preview(po["id"], user=tenant["users"]["owner"])
    new_prices = {it["product_id"]: it["new_price"] for it in preview["items"]}
    shares = {it["product_id"]: it["overall_disc_share"] for it in preview["items"]}

    record_result(
        scenario="br_po_ocap.5_preview_apply_agreement",
        step="preview_includes_overall_share",
        expected={"new_price": 80.0, "overall_disc_share": 100.0},
        actual={"new_price": new_prices.get(pid),
                "overall_disc_share": shares.get(pid)},
    )
    assert new_prices.get(pid) == 80.0
    assert shares.get(pid) == 100.0
    assert preview["overall_discount_amount"] == 100.0


# ─────────────────────────────────────────────────────────────────────
# Test 6 — Backfill endpoint walks past POs and corrects cost_price.
#   We deliberately seed `branch_prices.cost_price = 100` (the pre-fix
#   wrong value) for a PO that should have stamped 80. Run backfill →
#   row must be rewritten to 80.0. Re-run → idempotent (0 writes).
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_ocap_6_backfill_endpoint(tenant, record_result):
    org_id = tenant["org_id"]
    branch_id = tenant["branches"]["main"]
    pid = "prod-ocap6"
    await _seed_product(org_id=org_id, pid=pid)

    # Seed PO with overall discount; intentionally do NOT call
    # `_apply_po_inventory` here — we mimic the pre-fix state by stamping
    # cost_price ourselves at the wrong (pre-overall-discount) value.
    await _seed_po(
        org_id=org_id, branch_id=branch_id,
        lines=[{"pid": pid, "qty": 10, "unit_price": 100.0, "line_disc": 100.0}],
        overall_disc=100.0,
    )
    await _raw_db.branch_prices.update_one(
        {"product_id": pid, "branch_id": branch_id},
        {"$set": {
            "product_id":      pid,
            "branch_id":       branch_id,
            "organization_id": org_id,
            "cost_price":      90.0,  # pre-fix stamped (line disc only, no overall)
            "source":          "purchase_order",
        }},
        upsert=True,
    )

    # First run — should write the correction.
    user = {
        **tenant["users"]["owner"],
        "organization_id": org_id,
    }
    res = await backfill_overall_discount_capital(
        data={"dry_run": False}, user=user
    )

    record_result(
        scenario="br_po_ocap.6_backfill",
        step="first_run_writes_correction",
        expected={"written_at_least": 1},
        actual={"written": res["written"], "planned": res["planned_changes"]},
    )
    assert res["written"] >= 1
    cost_after = await _branch_cost(pid=pid, branch_id=branch_id)
    assert cost_after == 80.0, f"Backfill should set cost to 80, got {cost_after!r}"

    # Second run — idempotent.
    res2 = await backfill_overall_discount_capital(
        data={"dry_run": False}, user=user
    )
    # The product we care about should no longer appear in planned changes.
    pids_in_plan = {p["product_id"] for p in res2["preview"]}
    record_result(
        scenario="br_po_ocap.6_backfill",
        step="second_run_idempotent_for_pid",
        expected={"pid_in_plan": False},
        actual={"pid_in_plan": pid in pids_in_plan},
    )
    assert pid not in pids_in_plan, (
        f"Backfill should be idempotent — {pid} re-appeared in second run"
    )


# ─────────────────────────────────────────────────────────────────────
# Test 7 — `_effective_unit_cost` helper is a pure function; lock the
# math directly so any future refactor that changes the formula has to
# update this contract test.
# ─────────────────────────────────────────────────────────────────────
def test_ocap_7_effective_unit_pure_math():
    # Pure overall discount split
    item_a = {"product_id": "a", "quantity": 1,  "unit_price": 10000.0,
              "discount_amount": 0.0, "total": 10000.0}
    item_b = {"product_id": "b", "quantity": 10, "unit_price": 100.0,
              "discount_amount": 0.0, "total": 1000.0}
    subtotal = 11000.0
    overall = 1100.0
    eff_a = _effective_unit_cost(item_a, line_subtotal=subtotal, overall_disc_amt=overall)
    eff_b = _effective_unit_cost(item_b, line_subtotal=subtotal, overall_disc_amt=overall)
    assert eff_a["effective_unit"] == 9000.0
    assert eff_b["effective_unit"] == 90.0
    assert round(eff_a["overall_disc_share"], 2) == 1000.0
    assert round(eff_b["overall_disc_share"], 2) == 100.0

    # Zero overall → unchanged
    eff_z = _effective_unit_cost(item_a, line_subtotal=subtotal, overall_disc_amt=0)
    assert eff_z["overall_disc_share"] == 0.0
    assert eff_z["effective_unit"] == 10000.0

    # Zero subtotal → safe fallback
    eff_s = _effective_unit_cost(
        {"quantity": 0, "unit_price": 50.0, "discount_amount": 0, "total": 0},
        line_subtotal=0, overall_disc_amt=10,
    )
    assert eff_s["effective_unit"] == 50.0  # fallback to unit_price when qty=0

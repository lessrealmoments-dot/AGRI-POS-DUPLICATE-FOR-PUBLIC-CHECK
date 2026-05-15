"""
br_po_capital_line_discount — pin the Feb 2026 fix where per-line
supplier discount on a PO was correctly debited from payables but the
discount was DROPPED when updating the product's cost_price.

User repro: "Product A — 1500 less 100 = 1400. After receiving the
PO, the capital still shows 1500."

Root cause: `_apply_po_inventory` and `/capital-preview` both read
`item.unit_price` (raw, pre-discount). Fixed to derive effective
per-unit cost from the line `total` (or `unit_price - disc/qty`).
"""
import pytest

from config import _raw_db
from routes.purchase_orders import _apply_po_inventory, get_capital_preview


async def _seed_product(*, org_id, pid="prod-pcap", initial_cost=900.0):
    await _raw_db.products.update_one(
        {"id": pid},
        {"$set": {
            "id":               pid,
            "organization_id":  org_id,
            "name":             "Test Product",
            "sku":              "T-1",
            "cost_price":       initial_cost,
            "active":           True,
        }},
        upsert=True,
    )


async def _seed_po(*, org_id, branch_id, pid, qty, unit_price, line_disc=0.0):
    import uuid
    pid_po = f"po-pcap-{uuid.uuid4().hex[:8]}"
    line_total = round(qty * unit_price - line_disc, 2)
    po = {
        "id":               pid_po,
        "organization_id":  org_id,
        "branch_id":        branch_id,
        "po_number":        f"PO-{pid_po[-6:].upper()}",
        "vendor":           "Acme Suppliers",
        "status":           "received",
        "items": [{
            "product_id":      pid,
            "product_name":    "Test Product",
            "quantity":        qty,
            "unit_price":      unit_price,
            "discount_amount": line_disc,
            "total":           line_total,
            "unit":            "pc",
        }],
        "grand_total":     line_total,
    }
    await _raw_db.purchase_orders.insert_one(po)
    return po


# ═════════════════════════════════════════════════════════════════════
# Test 1 — Repro the user's exact scenario.
# 1500 less 100 → effective ₱1,400. cost_price must land at 1400, not 1500.
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_pcap_1_line_discount_lowers_capital(tenant, record_result):
    org_id = tenant["org_id"]
    branch_id = tenant["branches"]["main"]
    pid = "prod-pcap-1"
    await _seed_product(org_id=org_id, pid=pid, initial_cost=1300.0)

    po = await _seed_po(
        org_id=org_id, branch_id=branch_id, pid=pid,
        qty=1, unit_price=1500.0, line_disc=100.0,
    )

    await _apply_po_inventory(po, user=tenant["users"]["owner"])

    bp = await _raw_db.branch_prices.find_one(
        {"product_id": pid, "branch_id": branch_id}, {"_id": 0}
    )

    record_result(
        scenario="br_po_cap.1_line_discount",
        step="cost_price_reflects_post_discount",
        expected={"cost_price": 1400.0},
        actual={"cost_price": bp.get("cost_price") if bp else None},
    )
    assert bp is not None
    assert bp["cost_price"] == 1400.0, (
        f"Expected cost_price=1400 (= 1500 less 100 discount), "
        f"got {bp['cost_price']!r}"
    )


# ═════════════════════════════════════════════════════════════════════
# Test 2 — No discount → behaviour unchanged.
# unit_price=500 / disc=0 → cost_price = 500. Regression guard so the
# fix doesn't accidentally alter the happy-path.
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_pcap_2_no_discount_unchanged(tenant, record_result):
    org_id = tenant["org_id"]
    branch_id = tenant["branches"]["main"]
    pid = "prod-pcap-2"
    await _seed_product(org_id=org_id, pid=pid, initial_cost=300.0)

    po = await _seed_po(
        org_id=org_id, branch_id=branch_id, pid=pid,
        qty=3, unit_price=500.0, line_disc=0.0,
    )

    await _apply_po_inventory(po, user=tenant["users"]["owner"])

    bp = await _raw_db.branch_prices.find_one(
        {"product_id": pid, "branch_id": branch_id}, {"_id": 0}
    )

    record_result(
        scenario="br_po_cap.2_no_discount",
        step="happy_path_unchanged",
        expected={"cost_price": 500.0},
        actual={"cost_price": bp.get("cost_price") if bp else None},
    )
    assert bp["cost_price"] == 500.0


# ═════════════════════════════════════════════════════════════════════
# Test 3 — Multi-unit per-line discount.
# qty=10, unit_price=1000, line_disc=200  → effective unit cost = 980.
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_pcap_3_multi_unit_line_discount(tenant, record_result):
    org_id = tenant["org_id"]
    branch_id = tenant["branches"]["main"]
    pid = "prod-pcap-3"
    await _seed_product(org_id=org_id, pid=pid, initial_cost=950.0)

    po = await _seed_po(
        org_id=org_id, branch_id=branch_id, pid=pid,
        qty=10, unit_price=1000.0, line_disc=200.0,
    )

    await _apply_po_inventory(po, user=tenant["users"]["owner"])

    bp = await _raw_db.branch_prices.find_one(
        {"product_id": pid, "branch_id": branch_id}, {"_id": 0}
    )

    record_result(
        scenario="br_po_cap.3_multi_unit_disc",
        step="per_unit_disc_prorated",
        expected={"cost_price": 980.0},
        actual={"cost_price": bp.get("cost_price") if bp else None},
    )
    assert bp["cost_price"] == 980.0


# ═════════════════════════════════════════════════════════════════════
# Test 4 — `capital-preview` endpoint must show the SAME post-discount
# new_price the apply step will commit. Pre-fix the preview showed
# `unit_price` (1500), causing the cashier to expect the wrong number,
# and the apply diverged silently. Now they agree.
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_pcap_4_preview_matches_apply(tenant, record_result):
    org_id = tenant["org_id"]
    branch_id = tenant["branches"]["main"]
    pid = "prod-pcap-4"
    await _seed_product(org_id=org_id, pid=pid, initial_cost=1500.0)

    po = await _seed_po(
        org_id=org_id, branch_id=branch_id, pid=pid,
        qty=1, unit_price=1500.0, line_disc=100.0,
    )

    preview = await get_capital_preview(po["id"], user=tenant["users"]["owner"])
    new_prices = {it["product_id"]: it["new_price"] for it in preview["items"]}

    record_result(
        scenario="br_po_cap.4_preview_apply_agreement",
        step="preview_shows_post_discount_price",
        expected={"new_price": 1400.0},
        actual={"new_price": new_prices.get(pid)},
    )
    assert new_prices.get(pid) == 1400.0

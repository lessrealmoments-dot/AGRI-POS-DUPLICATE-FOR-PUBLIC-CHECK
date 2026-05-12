"""
br5 — Repack flow regression.

Owner-confirmed business invariants:
  * Parent/bulk product holds the only `inventory` row. Child SKUs have NO
    independent inventory row.
  * Child availability is DERIVED at read-time:
        child_available = parent_stock × units_per_parent
  * On sale of a child SKU, the route deducts the parent's inventory by
        parent_deduction = sold_child_qty ÷ units_per_parent
  * Repack capital is DERIVED on the fly:
        repack_capital = parent_branch_cost ÷ units_per_parent + add_on_cost
    (utils/helpers.get_repack_capital + get_branch_cost dispatcher)
  * Repack retail is branch-specific (lives in `branch_prices`); the
    catalog row's `product.prices` is `{}`.
  * Branch isolation: parent stock at Main cannot be borrowed by a sale
    at Branch B. Branch B's sale checks Branch B's parent inventory.

Scope NOT covered here (TODO at bottom of file):
  * br5.e — JIT retail save (`repack_retail_save` Owner PIN policy).
  * br5.f — repack propagation through branch transfer.
  * br5.g — capital_change row when parent cost shifts (sync.py:647-658).
  * br5.h — override-PIN happy path (`stock_negative_override`).
"""
import os
import sys

import pytest
from fastapi import HTTPException

BACKEND = os.path.join(os.path.dirname(__file__), "..", "..")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from config import _raw_db, set_org_context  # noqa: E402
from tests.business_regression._fixtures import seed_product  # noqa: E402
from tests.phase2b._fixtures import base_sale_payload, _uid  # noqa: E402
from routes.products import generate_repack  # noqa: E402
from routes.sales import create_unified_sale  # noqa: E402


# ─────────────────────────────────────────────────────────────────────
# Local helpers — file-local; promote to _fixtures.py only if br5+/br6
# needs them.
# ─────────────────────────────────────────────────────────────────────
async def _seed_parent_with_branch_cost(org_id, branch_id, *, name,
                                        sku_hint, parent_cost, parent_retail,
                                        stock):
    """Seed a parent product whose `branch_prices.cost_price` IS set so
    `get_repack_capital`'s preferred fallback (branch_prices) resolves
    deterministically. Also writes `cost_price` on the product row so
    invoice-line capital calculations have a clean number."""
    product_id = _uid("prd")
    sku = f"{sku_hint}-{product_id[-4:]}"
    await _raw_db.products.insert_one({
        "id": product_id, "organization_id": org_id,
        "name": name, "sku": sku,
        "category": "General", "unit": "Box",
        "price": parent_retail, "cost": parent_cost,
        "cost_price": parent_cost,
        "prices": {"retail": parent_retail},
        "active": True, "is_repack": False,
    })
    await _raw_db.inventory.insert_one({
        "id": _uid("inv"), "organization_id": org_id,
        "product_id": product_id, "branch_id": branch_id,
        "quantity": stock, "reserved_qty": 0,
    })
    await _raw_db.branch_prices.insert_one({
        "id": _uid("bp"), "organization_id": org_id,
        "product_id": product_id, "branch_id": branch_id,
        "cost_price": parent_cost,
        "prices": {"retail": parent_retail},
        "source": "br5_test_seed",
    })
    return product_id, sku


async def _inv_qty(product_id, branch_id):
    row = await _raw_db.inventory.find_one(
        {"product_id": product_id, "branch_id": branch_id},
        {"_id": 0, "quantity": 1},
    )
    return float(row["quantity"]) if row else 0.0


async def _movements_for_invoice(invoice_id, product_id, branch_id):
    return await _raw_db.movements.find(
        {"reference_id": invoice_id, "product_id": product_id,
         "branch_id": branch_id},
        {"_id": 0},
    ).to_list(None)


async def _wallet_balance(branch_id, wtype="cashier"):
    w = await _raw_db.fund_wallets.find_one(
        {"branch_id": branch_id, "type": wtype, "active": True},
        {"_id": 0, "balance": 1},
    )
    return float(w["balance"]) if w else 0.0


def _repack_line_payload(branch_id, child_id, qty, rate):
    """Same shape as base_sale_payload but the line points at the child SKU.
    The unified-sale route looks up the product, recognises it as a repack,
    and dispatches the parent-deduction path."""
    return {
        "branch_id": branch_id,
        "items": [{
            "product_id": child_id, "quantity": qty, "rate": rate,
            "discount_type": "amount", "discount_value": 0,
        }],
        "subtotal": qty * rate,
        "freight": 0,
        "overall_discount": 0,
        "grand_total": qty * rate,
        "payment_method": "Cash",
    }


# ─────────────────────────────────────────────────────────────────────
# br5.a — Catalog shape: parent → child link, no own inventory, no global retail
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_br5a_catalog_shape_parent_child_relation(tenant, record_result):
    org_id     = tenant["org_id"]
    main       = tenant["branches"]["main"]
    owner_user = tenant["users"]["owner"]
    set_org_context(org_id)

    UNITS  = 12       # 1 box = 12 pieces
    PARENT_COST   = 60.0
    PARENT_RETAIL = 100.0
    CHILD_RETAIL  = 12.0
    ADD_ON_COST   = 1.0

    parent_id, parent_sku = await _seed_parent_with_branch_cost(
        org_id, main, name="BR5 Parent Box",
        sku_hint="BR5-PAR", parent_cost=PARENT_COST,
        parent_retail=PARENT_RETAIL, stock=10,
    )
    child = await generate_repack(parent_id, {
        "branch_id": main,
        "units_per_parent": UNITS,
        "add_on_cost": ADD_ON_COST,
        "unit": "Piece",
        "prices": {"retail": CHILD_RETAIL},
    }, user=owner_user)
    child_id  = child["id"]
    child_sku = child["sku"]

    # Read what was actually persisted.
    persisted = await _raw_db.products.find_one(
        {"id": child_id}, {"_id": 0}
    ) or {}
    child_inv_row = await _raw_db.inventory.find_one(
        {"product_id": child_id}, {"_id": 0}
    )
    child_branch_price = await _raw_db.branch_prices.find_one(
        {"product_id": child_id, "branch_id": main}, {"_id": 0}
    ) or {}

    base_ev = {
        "org_id": org_id, "branch_id": main,
        "parent_product_id": parent_id, "parent_sku": parent_sku,
        "child_product_id": child_id, "child_sku": child_sku,
        "units_per_parent": UNITS,
        "add_on_cost": ADD_ON_COST,
        "parent_cost_seeded": PARENT_COST,
        "child_retail_seeded": CHILD_RETAIL,
    }

    record_result(
        scenario="br5.a_catalog_shape",
        step="child_sku_follows_R_prefix_convention",
        expected={"prefix": "R-", "starts_with_parent_sku": True},
        actual={"prefix": child_sku[:2],
                "starts_with_parent_sku": child_sku.startswith(f"R-{parent_sku}")},
        evidence=base_ev,
    )
    record_result(
        scenario="br5.a_catalog_shape",
        step="child_is_repack_with_parent_link",
        expected={"is_repack": True, "parent_id": parent_id,
                  "units_per_parent": UNITS,
                  "add_on_cost": ADD_ON_COST},
        actual={"is_repack": persisted.get("is_repack"),
                "parent_id": persisted.get("parent_id"),
                "units_per_parent": persisted.get("units_per_parent"),
                "add_on_cost": persisted.get("add_on_cost")},
        evidence=base_ev,
    )
    record_result(
        scenario="br5.a_catalog_shape",
        step="child_cost_is_sentinel_zero_global_prices_empty",
        expected={"cost_price": 0, "global_prices": {}},
        actual={"cost_price": persisted.get("cost_price"),
                "global_prices": persisted.get("prices")},
        evidence={**base_ev,
                  "rationale": "cost derived live via get_repack_capital; "
                               "retail lives in branch_prices only"},
    )
    record_result(
        scenario="br5.a_catalog_shape",
        step="child_has_NO_independent_inventory_row",
        expected={"inventory_row_exists": False},
        actual={"inventory_row_exists": child_inv_row is not None},
        evidence=base_ev,
    )
    record_result(
        scenario="br5.a_catalog_shape",
        step="child_branch_retail_persisted_in_branch_prices",
        expected={"retail": CHILD_RETAIL, "source": "repack_create"},
        actual={"retail": float((child_branch_price.get("prices") or {})
                                 .get("retail") or 0),
                "source": child_branch_price.get("source")},
        evidence={**base_ev,
                  "branch_price_id": child_branch_price.get("id")},
    )
    record_result(
        scenario="br5.a_catalog_shape",
        step="organization_id_tenant_scoped",
        expected={"organization_id": org_id},
        actual={"organization_id": persisted.get("organization_id")},
        evidence=base_ev,
    )

    assert persisted.get("is_repack") is True
    assert persisted.get("parent_id") == parent_id
    assert persisted.get("units_per_parent") == UNITS
    assert float(persisted.get("add_on_cost") or 0) == ADD_ON_COST
    assert persisted.get("cost_price") == 0
    assert persisted.get("prices") == {}
    assert child_inv_row is None, (
        "br5a INVARIANT BUG — child SKU should NOT have its own "
        f"inventory row, but found {child_inv_row}"
    )
    assert float((child_branch_price.get("prices") or {}).get("retail") or 0) == CHILD_RETAIL
    assert child_branch_price.get("source") == "repack_create"
    assert persisted.get("organization_id") == org_id


# ─────────────────────────────────────────────────────────────────────
# br5.b — Sale math: parent deducted by qty/units, invoice + movements
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_br5b_repack_sale_deducts_parent_by_ratio(tenant, record_result):
    org_id     = tenant["org_id"]
    main       = tenant["branches"]["main"]
    owner_user = tenant["users"]["owner"]
    set_org_context(org_id)

    UNITS         = 12
    PARENT_COST   = 60.0    # ₱60 per box
    PARENT_RETAIL = 100.0
    CHILD_RETAIL  = 12.0    # ₱12 per piece
    ADD_ON_COST   = 1.0
    PARENT_START  = 10      # 10 boxes
    QTY_CHILD     = 24      # sell 24 pieces

    parent_id, _ = await _seed_parent_with_branch_cost(
        org_id, main, name="BR5b Parent",
        sku_hint="BR5B-PAR", parent_cost=PARENT_COST,
        parent_retail=PARENT_RETAIL, stock=PARENT_START,
    )
    child = await generate_repack(parent_id, {
        "branch_id": main, "units_per_parent": UNITS,
        "add_on_cost": ADD_ON_COST, "unit": "Piece",
        "prices": {"retail": CHILD_RETAIL},
    }, user=owner_user)
    child_id = child["id"]

    # Snapshot pre-sale state.
    parent_stock_before    = await _inv_qty(parent_id, main)
    child_available_before = parent_stock_before * UNITS
    cashier_before         = await _wallet_balance(main, "cashier")

    # ── Act
    res = await create_unified_sale({
        **_repack_line_payload(main, child_id, QTY_CHILD, CHILD_RETAIL),
        "payment_type": "cash",
        "amount_paid": float(QTY_CHILD * CHILD_RETAIL),
    }, user=owner_user)
    invoice_id = res["id"]

    # Read post-sale state.
    invoice = await _raw_db.invoices.find_one(
        {"id": invoice_id}, {"_id": 0}
    ) or {}
    parent_stock_after    = await _inv_qty(parent_id, main)
    child_available_after = parent_stock_after * UNITS
    cashier_after         = await _wallet_balance(main, "cashier")
    movements             = await _movements_for_invoice(invoice_id, parent_id, main)
    line                  = next(
        (it for it in invoice.get("items", []) if it.get("product_id") == child_id),
        None,
    )

    expected_parent_deduction = QTY_CHILD / UNITS                   # 2.0
    expected_parent_after     = PARENT_START - expected_parent_deduction  # 8.0
    expected_child_after      = expected_parent_after * UNITS       # 96
    expected_sale_amount      = QTY_CHILD * CHILD_RETAIL            # 288.0
    expected_repack_capital   = PARENT_COST / UNITS + ADD_ON_COST   # 5.0 + 1.0 = 6.0
    expected_cogs             = expected_repack_capital * QTY_CHILD # 144.0
    expected_margin           = expected_sale_amount - expected_cogs # 144.0

    base_ev = {
        "org_id": org_id, "branch_id": main,
        "parent_product_id": parent_id,
        "child_product_id": child_id,
        "units_per_parent": UNITS,
        "parent_cost": PARENT_COST,
        "add_on_cost": ADD_ON_COST,
        "child_retail": CHILD_RETAIL,
        "qty_sold_child": QTY_CHILD,
        "parent_stock_before": parent_stock_before,
        "child_available_before": child_available_before,
        "invoice_id": invoice_id,
        "invoice_number": invoice.get("invoice_number"),
    }

    record_result(
        scenario="br5.b_repack_sale_math",
        step="parent_stock_deducted_by_ratio",
        expected={"parent_stock_after": expected_parent_after,
                  "deduction": expected_parent_deduction},
        actual={"parent_stock_after": parent_stock_after,
                "deduction": parent_stock_before - parent_stock_after},
        evidence=base_ev,
    )
    record_result(
        scenario="br5.b_repack_sale_math",
        step="child_derived_availability_drops",
        expected={"child_available_after": expected_child_after},
        actual={"child_available_after": child_available_after},
        evidence=base_ev,
    )
    record_result(
        scenario="br5.b_repack_sale_math",
        step="invoice_line_records_child_with_repack_flag_and_derived_cost",
        expected={"product_id": child_id, "is_repack": True,
                  "quantity": float(QTY_CHILD),
                  "cost_price_per_unit": expected_repack_capital},
        actual={
            "product_id": (line or {}).get("product_id"),
            "is_repack": (line or {}).get("is_repack"),
            "quantity": float((line or {}).get("quantity") or 0),
            "cost_price_per_unit": float((line or {}).get("cost_price") or 0),
        },
        evidence={**base_ev,
                  "invoice_line_keys": list((line or {}).keys()),
                  "note": "Parent linkage (parent_product_id, "
                          "units_per_parent) lives on the movements row, "
                          "not on the invoice line."},
    )
    record_result(
        scenario="br5.b_repack_sale_math",
        step="movement_row_on_parent_with_repack_marker_in_notes",
        expected={"count": 1, "type": "sale",
                  "qty_change": -expected_parent_deduction,
                  "product_id": parent_id,
                  "notes_starts_with": "Sold as repack: "},
        actual={
            "count": len(movements),
            "type": movements[0]["type"] if movements else None,
            "qty_change": (float(movements[0]["quantity_change"])
                           if movements else None),
            "product_id": movements[0].get("product_id") if movements else None,
            "notes_starts_with": (movements[0].get("notes", "")[:16]
                                  if movements else None),
        },
        evidence={**base_ev,
                  "movement_ids": [m.get("id") for m in movements],
                  "full_notes": (movements[0].get("notes")
                                 if movements else None),
                  "rationale": "movements schema is fixed by log_movement(); "
                               "parent-child linkage encoded textually in `notes`. "
                               "Strong: child name + sold-qty + literal "
                               "'Sold as repack' marker."},
    )
    record_result(
        scenario="br5.b_repack_sale_math",
        step="cashier_wallet_credited_repack_sale_amount",
        expected={"cashier_delta": +expected_sale_amount,
                  "cashier_after": cashier_before + expected_sale_amount},
        actual={"cashier_delta": cashier_after - cashier_before,
                "cashier_after": cashier_after},
        evidence=base_ev,
    )
    record_result(
        scenario="br5.b_repack_sale_math",
        step="cogs_and_margin_derived_from_parent_cost",
        expected={"derived_repack_capital_per_unit": expected_repack_capital,
                  "line_capital_per_unit_recorded": expected_repack_capital,
                  "expected_cogs": expected_cogs,
                  "expected_margin": expected_margin},
        actual={
            "derived_repack_capital_per_unit": expected_repack_capital,
            "line_capital_per_unit_recorded":
                float((line or {}).get("cost_price") or 0),
            "expected_cogs":
                float((line or {}).get("cost_price") or 0)
                * float((line or {}).get("quantity") or 0),
            "expected_margin":
                float(QTY_CHILD * CHILD_RETAIL)
                - float((line or {}).get("cost_price") or 0)
                * float((line or {}).get("quantity") or 0),
        },
        evidence={**base_ev,
                  "rationale": "parent_branch_cost / units_per_parent + add_on_cost "
                               "= 60/12 + 1 = 6.0"},
    )

    # Hard asserts.
    assert parent_stock_after == expected_parent_after, (
        f"br5b parent stock — expected {expected_parent_after}, "
        f"got {parent_stock_after}"
    )
    assert child_available_after == expected_child_after
    assert line is not None, "br5b BUG: invoice line for child not found"
    assert line["product_id"] == child_id
    assert line.get("is_repack") is True
    assert float(line["quantity"]) == float(QTY_CHILD)
    # Derived repack capital persisted on the invoice line.
    assert float(line.get("cost_price") or 0) == expected_repack_capital, (
        f"br5b cost — expected derived repack capital {expected_repack_capital}, "
        f"got line.cost_price={line.get('cost_price')!r} "
        f"(parent_cost/units + add_on = {PARENT_COST}/{UNITS} + {ADD_ON_COST})"
    )
    assert len(movements) == 1, (
        f"br5b: expected 1 movement row on parent, got {len(movements)}"
    )
    assert movements[0]["type"] == "sale"
    assert float(movements[0]["quantity_change"]) == -expected_parent_deduction
    assert movements[0]["product_id"] == parent_id
    # Parent-child linkage encoded in `notes` (log_movement's fixed schema).
    assert movements[0].get("notes", "").startswith("Sold as repack:"), (
        f"br5b: movement notes should mark repack provenance, got "
        f"{movements[0].get('notes')!r}"
    )
    assert cashier_after - cashier_before == expected_sale_amount


# ─────────────────────────────────────────────────────────────────────
# br5.c — Insufficient parent stock guard (no override PIN)
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_br5c_insufficient_parent_stock_blocks_sale(tenant, record_result):
    org_id     = tenant["org_id"]
    main       = tenant["branches"]["main"]
    owner_user = tenant["users"]["owner"]
    set_org_context(org_id)

    UNITS = 12
    PARENT_START = 1   # 1 box → 12 pieces available

    parent_id, _ = await _seed_parent_with_branch_cost(
        org_id, main, name="BR5c Parent",
        sku_hint="BR5C-PAR", parent_cost=60.0, parent_retail=100.0,
        stock=PARENT_START,
    )
    child = await generate_repack(parent_id, {
        "branch_id": main, "units_per_parent": UNITS,
        "add_on_cost": 0, "unit": "Piece",
        "prices": {"retail": 12.0},
    }, user=owner_user)
    child_id = child["id"]

    invoices_before = await _raw_db.invoices.count_documents(
        {"organization_id": org_id}
    )
    movements_before = await _raw_db.movements.count_documents(
        {"product_id": parent_id, "branch_id": main}
    )
    parent_stock_before = await _inv_qty(parent_id, main)

    status_code = None
    detail = None
    try:
        await create_unified_sale({
            **_repack_line_payload(main, child_id, 15, 12.0),
            "payment_type": "cash", "amount_paid": 180.0,
        }, user=owner_user)
    except HTTPException as e:
        status_code = e.status_code
        detail = e.detail

    invoices_after = await _raw_db.invoices.count_documents(
        {"organization_id": org_id}
    )
    movements_after = await _raw_db.movements.count_documents(
        {"product_id": parent_id, "branch_id": main}
    )
    parent_stock_after = await _inv_qty(parent_id, main)

    base_ev = {
        "org_id": org_id, "branch_id": main,
        "parent_product_id": parent_id, "child_product_id": child_id,
        "units_per_parent": UNITS,
        "parent_stock_seeded": PARENT_START,
        "child_available_seeded": PARENT_START * UNITS,
        "qty_requested_child": 15,
        "status_code": status_code,
        "detail_type": (detail.get("type")
                        if isinstance(detail, dict) else None),
    }
    record_result(
        scenario="br5.c_insufficient_parent_stock",
        step="sale_rejected_422_insufficient_stock",
        expected={"status_code": 422, "detail_type": "insufficient_stock"},
        actual={"status_code": status_code,
                "detail_type": (detail.get("type")
                                if isinstance(detail, dict) else None)},
        evidence={**base_ev, "detail_message":
                  (detail.get("message")
                   if isinstance(detail, dict) else None)},
    )
    record_result(
        scenario="br5.c_insufficient_parent_stock",
        step="parent_stock_unchanged",
        expected={"parent_stock": float(PARENT_START)},
        actual={"parent_stock": parent_stock_after},
        evidence=base_ev,
    )
    record_result(
        scenario="br5.c_insufficient_parent_stock",
        step="no_invoice_created",
        expected={"invoice_delta": 0},
        actual={"invoice_delta": invoices_after - invoices_before},
        evidence=base_ev,
    )
    record_result(
        scenario="br5.c_insufficient_parent_stock",
        step="no_movement_row_created",
        expected={"movement_delta": 0},
        actual={"movement_delta": movements_after - movements_before},
        evidence=base_ev,
    )

    assert status_code == 422, (
        f"br5c expected HTTP 422 (insufficient_stock), got {status_code} "
        f"(detail={detail!r})"
    )
    assert isinstance(detail, dict) and detail.get("type") == "insufficient_stock"
    assert parent_stock_after == float(PARENT_START)
    assert invoices_after == invoices_before
    assert movements_after == movements_before


# ─────────────────────────────────────────────────────────────────────
# br5.d — Branch isolation: parent stock at Main does NOT cover a sale at b2
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_br5d_branch_isolation_repack_sale(tenant, record_result):
    org_id     = tenant["org_id"]
    main       = tenant["branches"]["main"]
    b2         = tenant["branches"]["b2"]
    owner_user = tenant["users"]["owner"]
    set_org_context(org_id)

    UNITS = 12
    PARENT_AT_MAIN = 10  # only at Main; b2 has NO parent inventory row.

    parent_id, _ = await _seed_parent_with_branch_cost(
        org_id, main, name="BR5d Parent",
        sku_hint="BR5D-PAR", parent_cost=60.0, parent_retail=100.0,
        stock=PARENT_AT_MAIN,
    )
    child = await generate_repack(parent_id, {
        "branch_id": main, "units_per_parent": UNITS,
        "add_on_cost": 0, "unit": "Piece",
        "prices": {"retail": 12.0},
    }, user=owner_user)
    # Also seed branch_prices.retail at b2 so the sale doesn't trip on a
    # missing price before it gets to the stock check.
    await _raw_db.branch_prices.insert_one({
        "id": _uid("bp"), "organization_id": org_id,
        "product_id": child["id"], "branch_id": b2,
        "prices": {"retail": 12.0},
        "source": "br5d_test_seed",
    })
    child_id = child["id"]

    # Confirm b2 starts with NO parent inventory row.
    b2_parent_inv = await _raw_db.inventory.find_one(
        {"product_id": parent_id, "branch_id": b2}, {"_id": 0}
    )
    assert b2_parent_inv is None, (
        "br5d setup: b2 unexpectedly has parent inventory row"
    )

    main_stock_before = await _inv_qty(parent_id, main)
    invoices_before = await _raw_db.invoices.count_documents(
        {"organization_id": org_id}
    )

    status_code = None
    detail = None
    try:
        await create_unified_sale({
            **_repack_line_payload(b2, child_id, 1, 12.0),
            "payment_type": "cash", "amount_paid": 12.0,
        }, user=owner_user)   # owner has admin role → branch access bypass
    except HTTPException as e:
        status_code = e.status_code
        detail = e.detail

    main_stock_after = await _inv_qty(parent_id, main)
    invoices_after = await _raw_db.invoices.count_documents(
        {"organization_id": org_id}
    )
    b2_parent_inv_after = await _raw_db.inventory.find_one(
        {"product_id": parent_id, "branch_id": b2}, {"_id": 0}
    )

    base_ev = {
        "org_id": org_id,
        "from_branch_id_seeded_stock": main, "attempted_branch_id": b2,
        "parent_product_id": parent_id, "child_product_id": child_id,
        "main_parent_stock_before": main_stock_before,
        "qty_requested_child": 1,
        "status_code": status_code,
        "detail_type": (detail.get("type")
                        if isinstance(detail, dict) else None),
    }
    record_result(
        scenario="br5.d_branch_isolation",
        step="b2_sale_rejected_insufficient_stock",
        expected={"status_code": 422, "detail_type": "insufficient_stock"},
        actual={"status_code": status_code,
                "detail_type": (detail.get("type")
                                if isinstance(detail, dict) else None)},
        evidence={**base_ev, "detail_message":
                  (detail.get("message")
                   if isinstance(detail, dict) else None)},
    )
    record_result(
        scenario="br5.d_branch_isolation",
        step="main_branch_parent_stock_unchanged",
        expected={"main_stock": float(PARENT_AT_MAIN)},
        actual={"main_stock": main_stock_after},
        evidence=base_ev,
    )
    record_result(
        scenario="br5.d_branch_isolation",
        step="b2_did_not_borrow_or_create_parent_inventory",
        expected={"b2_parent_inv_exists": False},
        actual={"b2_parent_inv_exists": b2_parent_inv_after is not None},
        evidence=base_ev,
    )
    record_result(
        scenario="br5.d_branch_isolation",
        step="no_invoice_created",
        expected={"invoice_delta": 0},
        actual={"invoice_delta": invoices_after - invoices_before},
        evidence=base_ev,
    )

    assert status_code == 422, (
        f"br5d expected 422 insufficient_stock at b2, got {status_code} "
        f"(detail={detail!r})"
    )
    assert isinstance(detail, dict) and detail.get("type") == "insufficient_stock"
    assert main_stock_after == float(PARENT_AT_MAIN), (
        f"br5d ISOLATION BUG — Main parent stock changed from "
        f"{PARENT_AT_MAIN} to {main_stock_after} during a b2 sale attempt"
    )
    assert b2_parent_inv_after is None, (
        "br5d ISOLATION BUG — b2 silently materialised a parent inventory "
        "row during the rejected sale"
    )
    assert invoices_after == invoices_before


# TODO (future business_regression prompts):
#   * br5.e — JIT retail save (Owner PIN policy `repack_retail_save`).
#   * br5.f — repack propagation through branch transfer (sync.py:765-789).
#   * br5.g — capital_change row when parent cost shifts (sync.py:647-658).
#   * br5.h — override-PIN happy path (`stock_negative_override`).

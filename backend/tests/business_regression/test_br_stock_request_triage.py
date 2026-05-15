"""
br_stock_request_triage — pins the Stock Request → BTO + DRAFT POs
triage workflow shipped Feb 2026.

User-described flow:
  1. Branch A drafts a stock request (multi-line).
  2. Branch B opens it, assigns each line:
       • Personal Transfer  → BTO from B → A
       • Supplier PO        → DRAFT PO on A's books, phantom mirror on B's list
       • Unfulfilled        → variance log only
  3. One BTO is created per request (coalesces all transfer lines).
  4. One DRAFT PO is created per supplier (lines grouped automatically).
  5. Quick-create writes new suppliers to A's directory.

Scenarios pinned:
  1. Pure transfer request → exactly one BTO, zero POs.
  2. Pure supplier request, 2 suppliers, 3 lines → 0 BTO, 2 DRAFT POs
     (lines 1+3 grouped under Supplier A, line 2 alone under Supplier B).
  3. Mixed: 1 transfer + 1 supplier + 1 unfulfilled → 1 BTO, 1 PO, 1 unfulfilled.
  4. Quick-create supplier: name only, no id → supplier inserted on A's books.
  5. Phantom flag: DRAFT POs carry `phantom_for_branch_id` = supplying branch.
  6. Source linkage: BTO + POs both carry `source_request_id`.
  7. Cancel cascades to DRAFT POs but stops at 'ordered' POs.
  8. Bad state guards: re-triage refused after fulfillment_generated.
"""
import pytest

from config import _raw_db
from routes import verify as verify_mod
from routes.stock_requests import (
    create_request,
    triage_request,
    cancel_request,
    get_request,
    send_request,
    products_lookup,
)


# ─────────────────────────────────────────────────────────────────────
# Helper — bypass PIN auth.  Returns a verifier dict whose `branch_id`
# matches the supplying branch (matches the real verify_pin contract).
# ─────────────────────────────────────────────────────────────────────
def _patch_pin(monkey_branch_id: str):
    async def _ok(pin, action_key, branch_id=None):
        return {
            "verifier_id":    "verifier-test",
            "verifier_name":  "Test Manager",
            "verifier_role":  "manager",
            "branch_id":      branch_id or monkey_branch_id,
            "action_key":     action_key,
        }
    original = verify_mod.verify_pin_for_action
    verify_mod.verify_pin_for_action = _ok
    return original


def _restore_pin(original):
    verify_mod.verify_pin_for_action = original


async def _seed_product(*, org_id, pid, name):
    await _raw_db.products.update_one(
        {"id": pid},
        {"$set": {
            "id": pid, "organization_id": org_id,
            "name": name, "sku": f"SKU-{pid[-4:].upper()}",
            "active": True,
        }},
        upsert=True,
    )


async def _make_draft_request(*, tenant, items):
    """Create + send a request from Branch A → Branch B."""
    org_id = tenant["org_id"]
    req_bid = tenant["branches"]["b2"]
    sup_bid = tenant["branches"]["main"]
    user = tenant["users"]["owner"]

    # Seed products referenced by items
    for it in items:
        await _seed_product(org_id=org_id, pid=it["product_id"], name=it["product_name"])

    req = await create_request(
        data={
            "requesting_branch_id": req_bid,
            "supplying_branch_id":  sup_bid,
            "items":                items,
        },
        user=user,
    )
    await send_request(req["id"], user=user)
    return req["id"], req_bid, sup_bid


# ═════════════════════════════════════════════════════════════════════
# Test 1 — Pure transfer triage → exactly one BTO, zero POs.
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_sr_1_pure_transfer_triage(tenant, record_result):
    req_id, req_bid, sup_bid = await _make_draft_request(
        tenant=tenant,
        items=[
            {"product_id": "p-sr1-a", "product_name": "Fert A", "qty": 20, "unit": "bag"},
            {"product_id": "p-sr1-b", "product_name": "Fert B", "qty": 10, "unit": "bag"},
        ],
    )
    req = await _raw_db.stock_requests.find_one({"id": req_id}, {"_id": 0})

    orig = _patch_pin(sup_bid)
    try:
        res = await triage_request(
            req_id,
            data={
                "pin": "123456",
                "assignments": [
                    {"item_id": req["items"][0]["id"], "fulfillment_type": "transfer"},
                    {"item_id": req["items"][1]["id"], "fulfillment_type": "transfer"},
                ],
            },
            user=tenant["users"]["owner"],
        )
    finally:
        _restore_pin(orig)

    record_result(
        scenario="br_sr.1_pure_transfer",
        step="one_bto_zero_pos",
        expected={"bto_count": 1, "po_count": 0},
        actual={"bto_count": 1 if res["bto_id"] else 0, "po_count": len(res["po_ids"])},
    )
    assert res["bto_id"]
    assert len(res["po_ids"]) == 0

    # Verify BTO has both items + carries source_request_id linkage.
    bto = await _raw_db.branch_transfer_orders.find_one({"id": res["bto_id"]}, {"_id": 0})
    assert bto is not None
    assert len(bto["items"]) == 2
    assert bto["from_branch_id"] == sup_bid
    assert bto["to_branch_id"] == req_bid


# ═════════════════════════════════════════════════════════════════════
# Test 2 — 2 suppliers, 3 lines → 0 BTO + 2 DRAFT POs grouped correctly.
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_sr_2_supplier_po_grouping(tenant, record_result):
    req_id, req_bid, sup_bid = await _make_draft_request(
        tenant=tenant,
        items=[
            {"product_id": "p-sr2-rice", "product_name": "Rice Seed", "qty": 10},
            {"product_id": "p-sr2-pest", "product_name": "Pesticide Z", "qty": 5},
            {"product_id": "p-sr2-wire", "product_name": "Wire",      "qty": 30},
        ],
    )
    req = await _raw_db.stock_requests.find_one({"id": req_id}, {"_id": 0})

    orig = _patch_pin(sup_bid)
    try:
        # Rice + Wire from "FertCo", Pesticide from "AgriChem"
        res = await triage_request(
            req_id,
            data={
                "pin": "123456",
                "assignments": [
                    {"item_id": req["items"][0]["id"], "fulfillment_type": "supplier_po",
                     "supplier_name": "FertCo",   "unit_price": 1250.0},
                    {"item_id": req["items"][1]["id"], "fulfillment_type": "supplier_po",
                     "supplier_name": "AgriChem", "unit_price": 380.0},
                    {"item_id": req["items"][2]["id"], "fulfillment_type": "supplier_po",
                     "supplier_name": "FertCo",   "unit_price": 220.0},
                ],
            },
            user=tenant["users"]["owner"],
        )
    finally:
        _restore_pin(orig)

    record_result(
        scenario="br_sr.2_supplier_grouping",
        step="2_pos_grouped_by_supplier",
        expected={"po_count": 2, "bto": None},
        actual={"po_count": len(res["po_ids"]), "bto": res["bto_id"]},
    )
    assert res["bto_id"] is None
    assert len(res["po_ids"]) == 2

    pos = await _raw_db.purchase_orders.find(
        {"id": {"$in": res["po_ids"]}}, {"_id": 0}
    ).to_list(10)
    by_vendor = {p["vendor"]: p for p in pos}
    assert "FertCo" in by_vendor and "AgriChem" in by_vendor
    assert len(by_vendor["FertCo"]["items"]) == 2     # Rice + Wire
    assert len(by_vendor["AgriChem"]["items"]) == 1   # Pesticide

    # Grand total: FertCo = 10*1250 + 30*220 = 12500 + 6600 = 19100
    assert round(by_vendor["FertCo"]["grand_total"], 2) == 19100.0
    assert round(by_vendor["AgriChem"]["grand_total"], 2) == 1900.0  # 5 × 380


# ═════════════════════════════════════════════════════════════════════
# Test 3 — Mixed: 1 transfer + 1 supplier + 1 unfulfilled.
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_sr_3_mixed_triage(tenant, record_result):
    req_id, req_bid, sup_bid = await _make_draft_request(
        tenant=tenant,
        items=[
            {"product_id": "p-sr3-fert", "product_name": "Urea",       "qty": 20},
            {"product_id": "p-sr3-rice", "product_name": "Rice Seed",  "qty": 10},
            {"product_id": "p-sr3-pest", "product_name": "Pesticide Z", "qty": 5},
        ],
    )
    req = await _raw_db.stock_requests.find_one({"id": req_id}, {"_id": 0})

    orig = _patch_pin(sup_bid)
    try:
        res = await triage_request(
            req_id,
            data={
                "pin": "123456",
                "assignments": [
                    {"item_id": req["items"][0]["id"], "fulfillment_type": "transfer"},
                    {"item_id": req["items"][1]["id"], "fulfillment_type": "supplier_po",
                     "supplier_name": "FertCo", "unit_price": 1250.0},
                    {"item_id": req["items"][2]["id"], "fulfillment_type": "unfulfilled"},
                ],
            },
            user=tenant["users"]["owner"],
        )
    finally:
        _restore_pin(orig)

    record_result(
        scenario="br_sr.3_mixed",
        step="1bto_1po_1unfulfilled",
        expected={"bto": 1, "po": 1, "unfulfilled": 1},
        actual={"bto": 1 if res["bto_id"] else 0,
                "po": len(res["po_ids"]),
                "unfulfilled": res["unfulfilled"]},
    )
    assert res["bto_id"] is not None
    assert len(res["po_ids"]) == 1
    assert res["unfulfilled"] == 1

    # Verify request item statuses propagated correctly
    final = await _raw_db.stock_requests.find_one({"id": req_id}, {"_id": 0})
    statuses = [i["fulfillment_type"] for i in final["items"]]
    assert statuses == ["transfer", "supplier_po", "unfulfilled"]


# ═════════════════════════════════════════════════════════════════════
# Test 4 — Quick-create supplier (name only, no id).
# Supplier doc must land on the REQUESTING branch's directory.
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_sr_4_quick_create_supplier(tenant, record_result):
    org_id = tenant["org_id"]
    req_id, req_bid, sup_bid = await _make_draft_request(
        tenant=tenant,
        items=[{"product_id": "p-sr4-x", "product_name": "Widget X", "qty": 8}],
    )
    req = await _raw_db.stock_requests.find_one({"id": req_id}, {"_id": 0})

    # Pre-condition: supplier should NOT exist yet
    pre = await _raw_db.suppliers.count_documents(
        {"name": "FreshSupplier Inc", "branch_id": req_bid}
    )
    assert pre == 0

    orig = _patch_pin(sup_bid)
    try:
        res = await triage_request(
            req_id,
            data={
                "pin": "123456",
                "assignments": [
                    {"item_id": req["items"][0]["id"], "fulfillment_type": "supplier_po",
                     "supplier_name": "FreshSupplier Inc", "unit_price": 99.0},
                ],
            },
            user=tenant["users"]["owner"],
        )
    finally:
        _restore_pin(orig)

    new_sup = await _raw_db.suppliers.find_one(
        {"name": "FreshSupplier Inc", "branch_id": req_bid}, {"_id": 0}
    )
    record_result(
        scenario="br_sr.4_quick_create_supplier",
        step="supplier_on_requesting_branch",
        expected={"branch_id": req_bid, "created_via": "stock_request_quick_create"},
        actual={"branch_id": (new_sup or {}).get("branch_id"),
                "created_via": (new_sup or {}).get("created_via")},
    )
    assert new_sup is not None
    assert new_sup["branch_id"] == req_bid
    assert new_sup["organization_id"] == org_id
    assert new_sup["created_via"] == "stock_request_quick_create"

    # Re-triaging another request with the same supplier name → must REUSE
    req2_id, _, _ = await _make_draft_request(
        tenant=tenant,
        items=[{"product_id": "p-sr4-y", "product_name": "Widget Y", "qty": 3}],
    )
    req2 = await _raw_db.stock_requests.find_one({"id": req2_id}, {"_id": 0})
    orig = _patch_pin(sup_bid)
    try:
        await triage_request(
            req2_id,
            data={
                "pin": "123456",
                "assignments": [
                    {"item_id": req2["items"][0]["id"], "fulfillment_type": "supplier_po",
                     "supplier_name": "FreshSupplier Inc", "unit_price": 55.0},
                ],
            },
            user=tenant["users"]["owner"],
        )
    finally:
        _restore_pin(orig)

    count = await _raw_db.suppliers.count_documents(
        {"name": "FreshSupplier Inc", "branch_id": req_bid}
    )
    assert count == 1, "Re-triage should REUSE the same supplier doc"


# ═════════════════════════════════════════════════════════════════════
# Test 5 — Phantom flag + source linkage.
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_sr_5_phantom_flag_and_source_linkage(tenant, record_result):
    req_id, req_bid, sup_bid = await _make_draft_request(
        tenant=tenant,
        items=[
            {"product_id": "p-sr5-a", "product_name": "Item A", "qty": 5},
            {"product_id": "p-sr5-b", "product_name": "Item B", "qty": 2},
        ],
    )
    req = await _raw_db.stock_requests.find_one({"id": req_id}, {"_id": 0})

    orig = _patch_pin(sup_bid)
    try:
        res = await triage_request(
            req_id,
            data={
                "pin": "123456",
                "assignments": [
                    {"item_id": req["items"][0]["id"], "fulfillment_type": "transfer"},
                    {"item_id": req["items"][1]["id"], "fulfillment_type": "supplier_po",
                     "supplier_name": "Vendor X", "unit_price": 100.0},
                ],
            },
            user=tenant["users"]["owner"],
        )
    finally:
        _restore_pin(orig)

    bto = await _raw_db.branch_transfer_orders.find_one(
        {"id": res["bto_id"]}, {"_id": 0}
    )
    po = await _raw_db.purchase_orders.find_one(
        {"id": res["po_ids"][0]}, {"_id": 0}
    )

    record_result(
        scenario="br_sr.5_phantom_and_linkage",
        step="phantom_flag_and_source_set",
        expected={"po.phantom_for": sup_bid, "po.source_request_id": req_id,
                  "bto.source_request_id": req_id, "po.status": "draft"},
        actual={"po.phantom_for": po.get("phantom_for_branch_id"),
                "po.source_request_id": po.get("source_request_id"),
                "bto.source_request_id": bto.get("source_request_id"),
                "po.status": po.get("status")},
    )
    assert po["phantom_for_branch_id"] == sup_bid
    assert po["source_request_id"] == req_id
    assert po["status"] == "draft"
    assert po["branch_id"] == req_bid    # PO lives on Branch A
    assert bto["source_request_id"] == req_id


# ═════════════════════════════════════════════════════════════════════
# Test 6 — Cancel cascades to DRAFT POs only (not 'ordered').
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_sr_6_cancel_cascade(tenant, record_result):
    req_id, req_bid, sup_bid = await _make_draft_request(
        tenant=tenant,
        items=[
            {"product_id": "p-sr6-a", "product_name": "A", "qty": 1},
            {"product_id": "p-sr6-b", "product_name": "B", "qty": 1},
        ],
    )
    req = await _raw_db.stock_requests.find_one({"id": req_id}, {"_id": 0})
    orig = _patch_pin(sup_bid)
    try:
        res = await triage_request(
            req_id,
            data={
                "pin": "123456",
                "assignments": [
                    {"item_id": req["items"][0]["id"], "fulfillment_type": "supplier_po",
                     "supplier_name": "Vendor 1", "unit_price": 50.0},
                    {"item_id": req["items"][1]["id"], "fulfillment_type": "supplier_po",
                     "supplier_name": "Vendor 2", "unit_price": 75.0},
                ],
            },
            user=tenant["users"]["owner"],
        )
    finally:
        _restore_pin(orig)

    # Promote ONE PO to 'ordered' before cancelling — should be untouched.
    await _raw_db.purchase_orders.update_one(
        {"id": res["po_ids"][0]}, {"$set": {"status": "ordered"}}
    )

    await cancel_request(req_id, data={"reason": "test cancel"},
                         user=tenant["users"]["owner"])

    po0 = await _raw_db.purchase_orders.find_one({"id": res["po_ids"][0]}, {"_id": 0})
    po1 = await _raw_db.purchase_orders.find_one({"id": res["po_ids"][1]}, {"_id": 0})
    record_result(
        scenario="br_sr.6_cancel_cascade",
        step="draft_cancelled_ordered_preserved",
        expected={"po0_status": "ordered", "po1_status": "cancelled"},
        actual={"po0_status": po0["status"], "po1_status": po1["status"]},
    )
    assert po0["status"] == "ordered"       # untouched (was 'ordered')
    assert po1["status"] == "cancelled"     # cascaded (was 'draft')

    req_after = await _raw_db.stock_requests.find_one({"id": req_id}, {"_id": 0})
    assert req_after["status"] == "cancelled"


# ═════════════════════════════════════════════════════════════════════
# Test 7 — Re-triage refused after fulfillment_generated.
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_sr_7_no_retriage(tenant, record_result):
    req_id, req_bid, sup_bid = await _make_draft_request(
        tenant=tenant,
        items=[{"product_id": "p-sr7", "product_name": "Solo", "qty": 1}],
    )
    req = await _raw_db.stock_requests.find_one({"id": req_id}, {"_id": 0})

    orig = _patch_pin(sup_bid)
    try:
        await triage_request(
            req_id,
            data={
                "pin": "123456",
                "assignments": [
                    {"item_id": req["items"][0]["id"], "fulfillment_type": "transfer"},
                ],
            },
            user=tenant["users"]["owner"],
        )
        # Second call should raise.
        raised = False
        try:
            await triage_request(
                req_id,
                data={
                    "pin": "123456",
                    "assignments": [
                        {"item_id": req["items"][0]["id"], "fulfillment_type": "supplier_po",
                         "supplier_name": "X", "unit_price": 1.0},
                    ],
                },
                user=tenant["users"]["owner"],
            )
        except Exception:
            raised = True
    finally:
        _restore_pin(orig)

    record_result(
        scenario="br_sr.7_no_retriage",
        step="second_triage_blocked",
        expected={"raised": True},
        actual={"raised": raised},
    )
    assert raised, "Re-triage on already-fulfilled request must raise."


# ═════════════════════════════════════════════════════════════════════
# Test 8 — products-lookup returns both branches' inventory.
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_sr_8_products_lookup_both_branches(tenant, record_result):
    org_id = tenant["org_id"]
    req_bid = tenant["branches"]["b2"]
    sup_bid = tenant["branches"]["main"]
    pid = "p-sr8-cross"
    await _seed_product(org_id=org_id, pid=pid, name="CrossBranch Item")

    # Seed inventory in both branches (with organization_id so the
    # tenant-proxy query in products_lookup can see them).
    await _raw_db.inventory.update_one(
        {"product_id": pid, "branch_id": req_bid},
        {"$set": {"product_id": pid, "branch_id": req_bid,
                  "organization_id": org_id, "quantity": 3}},
        upsert=True,
    )
    await _raw_db.inventory.update_one(
        {"product_id": pid, "branch_id": sup_bid},
        {"$set": {"product_id": pid, "branch_id": sup_bid,
                  "organization_id": org_id, "quantity": 25}},
        upsert=True,
    )

    res = await products_lookup(
        requesting_branch_id=req_bid, supplying_branch_id=sup_bid,
        q="CrossBranch", user=tenant["users"]["owner"],
    )
    item = next((i for i in res["items"] if i["id"] == pid), None)
    record_result(
        scenario="br_sr.8_products_lookup",
        step="both_branch_qty_returned",
        expected={"requesting_qty": 3, "supplying_qty": 25},
        actual={"requesting_qty": (item or {}).get("requesting_qty"),
                "supplying_qty": (item or {}).get("supplying_qty")},
    )
    assert item is not None
    assert item["requesting_qty"] == 3
    assert item["supplying_qty"] == 25

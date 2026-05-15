"""
br_stock_request_variance — pins the Phase 3 variance detection +
back-propagation shipped Feb 2026.

Lifecycle covered:
  Stock Request → triage → DRAFT PO → mark-ordered (snapshot stored) →
  Branch A receives PO → variance computed vs ordered snapshot → status
  back-propagated to the stock_request item + parent PO.

Variance kinds (precedence: missing > extra > under > over > completed):
  completed         all lines received in exact qty
  under_delivered   at least one line received with qty < ordered
  over_delivered    at least one line received with qty > ordered
  extra_items       a line in received that wasn't in ordered
  missing_items     an ordered line entirely absent from received

Scenarios pinned:
  1. Pure helper math — `compute_phantom_po_variance` direct unit test.
  2. completed       — exact-qty receive
  3. under_delivered — supplier shipped less
  4. over_delivered  — supplier shipped more
  5. extra_items     — supplier added a product
  6. missing_items   — supplier omitted a product
  7. Stock-request item status back-propagates correctly.
  8. Request auto-completes when ALL child docs reach terminal status.
"""
import pytest

from config import _raw_db
from routes import verify as verify_mod
from routes.stock_requests import (
    compute_phantom_po_variance,
    create_request,
    mark_phantom_po_ordered,
    send_request,
    triage_request,
    update_phantom_po_received,
)


def _patch_pin(monkey_branch_id: str):
    async def _ok(pin, action_key, branch_id=None):
        return {"verifier_id": "v", "verifier_name": "M", "verifier_role": "manager",
                "branch_id": branch_id or monkey_branch_id, "action_key": action_key}
    original = verify_mod.verify_pin_for_action
    verify_mod.verify_pin_for_action = _ok
    return original


def _restore_pin(original):
    verify_mod.verify_pin_for_action = original


async def _seed_product(*, org_id, pid, name):
    await _raw_db.products.update_one(
        {"id": pid},
        {"$set": {"id": pid, "organization_id": org_id, "name": name,
                  "sku": f"SKU-{pid[-4:].upper()}", "active": True}},
        upsert=True,
    )


async def _setup_ordered_po(*, tenant, lines):
    """Create a request → triage → mark-ordered. Returns (request_id, po_id).
    `lines`: list of {pid, name, qty, unit_price}."""
    org_id = tenant["org_id"]
    req_bid = tenant["branches"]["b2"]
    sup_bid = tenant["branches"]["main"]
    user = tenant["users"]["owner"]

    items_payload = []
    for ln in lines:
        await _seed_product(org_id=org_id, pid=ln["pid"], name=ln["name"])
        items_payload.append({
            "product_id": ln["pid"], "product_name": ln["name"],
            "qty": ln["qty"], "unit": "pc",
        })

    req = await create_request(
        data={"requesting_branch_id": req_bid, "supplying_branch_id": sup_bid,
              "items": items_payload},
        user=user,
    )
    await send_request(req["id"], user=user)
    req_doc = await _raw_db.stock_requests.find_one({"id": req["id"]}, {"_id": 0})

    orig = _patch_pin(sup_bid)
    try:
        res = await triage_request(
            req["id"],
            data={"pin": "1",
                  "assignments": [
                      {"item_id": req_doc["items"][i]["id"],
                       "fulfillment_type": "supplier_po",
                       "supplier_name": "Acme",
                       "unit_price": lines[i]["unit_price"]}
                      for i in range(len(lines))
                  ]},
            user=user,
        )
        await mark_phantom_po_ordered(
            req["id"], res["po_ids"][0],
            data={"pin": "1", "supplier_ref": "TEST"},
            user=user,
        )
    finally:
        _restore_pin(orig)
    return req["id"], res["po_ids"][0]


async def _set_received_qtys(po_id: str, qty_by_pid: dict, extra_lines=None,
                              drop_pids=None):
    """Simulate what Branch A would do on receive: mutate the PO's `items`
    list to reflect received qtys.  `extra_lines` adds wholly new lines.
    `drop_pids` removes ordered lines entirely (simulates 'missing')."""
    po = await _raw_db.purchase_orders.find_one({"id": po_id}, {"_id": 0})
    new_items = []
    for it in po["items"]:
        pid = it["product_id"]
        if drop_pids and pid in drop_pids:
            continue
        copy = dict(it)
        if pid in qty_by_pid:
            copy["quantity"] = float(qty_by_pid[pid])
        new_items.append(copy)
    if extra_lines:
        for el in extra_lines:
            new_items.append({
                "product_id":  el["pid"],
                "product_name": el.get("name", el["pid"]),
                "quantity":     float(el["qty"]),
                "unit_price":   float(el.get("unit_price", 0)),
                "total":        float(el["qty"]) * float(el.get("unit_price", 0)),
            })
    await _raw_db.purchase_orders.update_one(
        {"id": po_id}, {"$set": {"items": new_items}}
    )


# ═════════════════════════════════════════════════════════════════════
# Test 1 — compute_phantom_po_variance is a pure function.
# ═════════════════════════════════════════════════════════════════════
def test_var_1_helper_pure_math():
    po = {
        "ordered_snapshot": {"items": [
            {"product_id": "A", "quantity": 10, "product_name": "A"},
            {"product_id": "B", "quantity":  5, "product_name": "B"},
            {"product_id": "C", "quantity":  2, "product_name": "C"},  # missing
        ]},
        "items": [
            {"product_id": "A", "quantity": 10, "product_name": "A"},  # match
            {"product_id": "B", "quantity":  4, "product_name": "B"},  # under
            {"product_id": "D", "quantity":  1, "product_name": "D"},  # extra
        ],
    }
    v = compute_phantom_po_variance(po)
    # Precedence: missing > extra > under
    assert v["kind"] == "missing_items"
    assert v["has_missing"] and v["has_extra"] and v["has_under"]
    kinds_by_pid = {it["product_id"]: it["kind"] for it in v["items_variance"]}
    assert kinds_by_pid["A"] == "match"
    assert kinds_by_pid["B"] == "under"
    assert kinds_by_pid["C"] == "missing"
    assert kinds_by_pid["D"] == "extra"


# ═════════════════════════════════════════════════════════════════════
# Test 2 — completed: exact-qty receive.
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_var_2_completed(tenant, record_result):
    req_id, po_id = await _setup_ordered_po(
        tenant=tenant,
        lines=[{"pid": "p-v2-a", "name": "A", "qty": 10, "unit_price": 100.0}],
    )
    # Branch A receives exactly what was ordered — no qty mutation needed.
    po = await _raw_db.purchase_orders.find_one({"id": po_id}, {"_id": 0})
    await update_phantom_po_received(po)

    po_after = await _raw_db.purchase_orders.find_one({"id": po_id}, {"_id": 0})
    req_after = await _raw_db.stock_requests.find_one({"id": req_id}, {"_id": 0})
    record_result(
        scenario="br_var.2_completed",
        step="kind_completed_and_request_updated",
        expected={"po_kind": "completed", "item_status": "completed"},
        actual={"po_kind":      po_after.get("received_variance_kind"),
                "item_status":  req_after["items"][0]["status"]},
    )
    assert po_after["received_variance_kind"] == "completed"
    assert req_after["items"][0]["status"] == "completed"


# ═════════════════════════════════════════════════════════════════════
# Test 3 — under_delivered.
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_var_3_under_delivered(tenant, record_result):
    req_id, po_id = await _setup_ordered_po(
        tenant=tenant,
        lines=[{"pid": "p-v3-a", "name": "A", "qty": 10, "unit_price": 100.0}],
    )
    await _set_received_qtys(po_id, {"p-v3-a": 8})  # supplier shipped 8 / 10
    po = await _raw_db.purchase_orders.find_one({"id": po_id}, {"_id": 0})
    await update_phantom_po_received(po)

    po_after = await _raw_db.purchase_orders.find_one({"id": po_id}, {"_id": 0})
    record_result(
        scenario="br_var.3_under",
        step="kind_under_delivered",
        expected={"kind": "under_delivered"},
        actual={"kind": po_after["received_variance_kind"]},
    )
    assert po_after["received_variance_kind"] == "under_delivered"


# ═════════════════════════════════════════════════════════════════════
# Test 4 — over_delivered.
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_var_4_over_delivered(tenant, record_result):
    req_id, po_id = await _setup_ordered_po(
        tenant=tenant,
        lines=[{"pid": "p-v4-a", "name": "A", "qty": 5, "unit_price": 100.0}],
    )
    await _set_received_qtys(po_id, {"p-v4-a": 7})
    po = await _raw_db.purchase_orders.find_one({"id": po_id}, {"_id": 0})
    await update_phantom_po_received(po)

    po_after = await _raw_db.purchase_orders.find_one({"id": po_id}, {"_id": 0})
    record_result(
        scenario="br_var.4_over",
        step="kind_over_delivered",
        expected={"kind": "over_delivered"},
        actual={"kind": po_after["received_variance_kind"]},
    )
    assert po_after["received_variance_kind"] == "over_delivered"


# ═════════════════════════════════════════════════════════════════════
# Test 5 — extra_items: supplier added a product Branch B didn't order.
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_var_5_extra_items(tenant, record_result):
    req_id, po_id = await _setup_ordered_po(
        tenant=tenant,
        lines=[{"pid": "p-v5-a", "name": "A", "qty": 5, "unit_price": 100.0}],
    )
    await _set_received_qtys(
        po_id, {},
        extra_lines=[{"pid": "p-v5-bonus", "name": "Bonus", "qty": 1, "unit_price": 0}],
    )
    po = await _raw_db.purchase_orders.find_one({"id": po_id}, {"_id": 0})
    await update_phantom_po_received(po)

    po_after = await _raw_db.purchase_orders.find_one({"id": po_id}, {"_id": 0})
    record_result(
        scenario="br_var.5_extra",
        step="kind_extra_items",
        expected={"kind": "extra_items"},
        actual={"kind": po_after["received_variance_kind"]},
    )
    assert po_after["received_variance_kind"] == "extra_items"
    # extra item must appear in items_variance with kind='extra'
    extra = next((iv for iv in po_after["received_variance"]["items_variance"]
                  if iv["kind"] == "extra"), None)
    assert extra is not None
    assert extra["product_id"] == "p-v5-bonus"


# ═════════════════════════════════════════════════════════════════════
# Test 6 — missing_items: supplier omitted a product entirely.
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_var_6_missing_items(tenant, record_result):
    req_id, po_id = await _setup_ordered_po(
        tenant=tenant,
        lines=[
            {"pid": "p-v6-a", "name": "A", "qty": 3, "unit_price": 100.0},
            {"pid": "p-v6-b", "name": "B", "qty": 4, "unit_price": 100.0},
        ],
    )
    await _set_received_qtys(po_id, {}, drop_pids={"p-v6-b"})  # B never arrived
    po = await _raw_db.purchase_orders.find_one({"id": po_id}, {"_id": 0})
    await update_phantom_po_received(po)

    po_after = await _raw_db.purchase_orders.find_one({"id": po_id}, {"_id": 0})
    record_result(
        scenario="br_var.6_missing",
        step="kind_missing_items",
        expected={"kind": "missing_items"},
        actual={"kind": po_after["received_variance_kind"]},
    )
    assert po_after["received_variance_kind"] == "missing_items"


# ═════════════════════════════════════════════════════════════════════
# Test 7 — stock-request item status reflects per-PO variance.
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_var_7_request_item_status_propagation(tenant, record_result):
    req_id, po_id = await _setup_ordered_po(
        tenant=tenant,
        lines=[{"pid": "p-v7-a", "name": "A", "qty": 10, "unit_price": 100.0}],
    )
    await _set_received_qtys(po_id, {"p-v7-a": 7})  # under-delivered
    po = await _raw_db.purchase_orders.find_one({"id": po_id}, {"_id": 0})
    await update_phantom_po_received(po)

    req_after = await _raw_db.stock_requests.find_one({"id": req_id}, {"_id": 0})
    item = req_after["items"][0]
    record_result(
        scenario="br_var.7_propagation",
        step="status_and_variance_propagated",
        expected={"status": "under_delivered", "variance": "under_delivered"},
        actual={"status": item["status"], "variance": item.get("variance")},
    )
    assert item["status"] == "under_delivered"
    assert item.get("variance") == "under_delivered"


# ═════════════════════════════════════════════════════════════════════
# Test 8 — request auto-completes when every child reaches terminal state.
#   Single-PO request, complete receive → request status → completed.
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_var_8_request_auto_complete(tenant, record_result):
    req_id, po_id = await _setup_ordered_po(
        tenant=tenant,
        lines=[{"pid": "p-v8-a", "name": "A", "qty": 5, "unit_price": 50.0}],
    )
    # Mark PO received (mimics the receive_purchase_order endpoint side-effect)
    await _raw_db.purchase_orders.update_one(
        {"id": po_id}, {"$set": {"status": "received"}}
    )
    po = await _raw_db.purchase_orders.find_one({"id": po_id}, {"_id": 0})
    await update_phantom_po_received(po)

    req_after = await _raw_db.stock_requests.find_one({"id": req_id}, {"_id": 0})
    record_result(
        scenario="br_var.8_auto_complete",
        step="request_status_completed",
        expected={"req_status": "completed"},
        actual={"req_status": req_after["status"]},
    )
    assert req_after["status"] == "completed"

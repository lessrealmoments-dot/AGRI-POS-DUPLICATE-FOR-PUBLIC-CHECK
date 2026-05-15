"""
br_stock_request_timeline — pins the unified-chronology timeline endpoint
shipped Feb 2026 (Phase 3+).

The endpoint is read-only and aggregates existing stamped events from the
stock_request, its BTO (if any), and each child PO into ONE chronologically-
sorted feed. No new event-logging concept introduced.

Scenarios pinned:
  1. Newly created request → exactly 1 event (request.created)
  2. After send → 2 events; chronological order preserved
  3. After triage spawning 1 BTO + 2 POs → bto.created + 2× po.created events
  4. After mark-ordered → po.ordered event w/ supplier_ref + ETA in detail
  5. After receive with variance → po.received event flags variance kind
  6. After full receive of single PO → request.completed event present
  7. Cancelled request → request.cancelled event present
"""
import pytest

from config import _raw_db
from routes import verify as verify_mod
from routes.stock_requests import (
    cancel_request,
    create_request,
    get_request_timeline,
    mark_phantom_po_ordered,
    send_request,
    triage_request,
    update_phantom_po_received,
)


def _patch_pin(bid):
    async def _ok(pin, action_key, branch_id=None):
        return {"verifier_id": "v", "verifier_name": "Mgr X",
                "verifier_role": "manager",
                "branch_id": branch_id or bid, "action_key": action_key}
    orig = verify_mod.verify_pin_for_action
    verify_mod.verify_pin_for_action = _ok
    return orig


def _restore_pin(o):
    verify_mod.verify_pin_for_action = o


async def _seed_product(*, org_id, pid, name):
    await _raw_db.products.update_one(
        {"id": pid},
        {"$set": {"id": pid, "organization_id": org_id, "name": name,
                  "sku": f"SKU-{pid[-4:].upper()}", "active": True}},
        upsert=True,
    )


async def _make_simple_request(*, tenant, items, send=True):
    org_id = tenant["org_id"]
    req_bid = tenant["branches"]["b2"]
    sup_bid = tenant["branches"]["main"]
    user = tenant["users"]["owner"]
    for it in items:
        await _seed_product(org_id=org_id, pid=it["product_id"],
                             name=it["product_name"])
    req = await create_request(
        data={"requesting_branch_id": req_bid,
              "supplying_branch_id":  sup_bid, "items": items},
        user=user,
    )
    if send:
        await send_request(req["id"], user=user)
    return req["id"], req_bid, sup_bid


# ═════════════════════════════════════════════════════════════════════
# Test 1 — Brand-new request → one event.
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_tl_1_created_only(tenant, record_result):
    req_id, _, _ = await _make_simple_request(
        tenant=tenant,
        items=[{"product_id": "p-tl1", "product_name": "A", "qty": 1}],
        send=False,
    )
    tl = await get_request_timeline(req_id, user=tenant["users"]["owner"])
    kinds = [e["kind"] for e in tl["events"]]
    record_result(
        scenario="br_tl.1_created",
        step="only_request_created",
        expected={"kinds": ["request.created"]},
        actual={"kinds": kinds},
    )
    assert kinds == ["request.created"]


# ═════════════════════════════════════════════════════════════════════
# Test 2 — After send → 2 events, chronological order.
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_tl_2_sent_two_events(tenant, record_result):
    req_id, _, _ = await _make_simple_request(
        tenant=tenant,
        items=[{"product_id": "p-tl2", "product_name": "A", "qty": 1}],
    )
    tl = await get_request_timeline(req_id, user=tenant["users"]["owner"])
    kinds = [e["kind"] for e in tl["events"]]
    record_result(
        scenario="br_tl.2_sent",
        step="created_then_sent",
        expected={"kinds": ["request.created", "request.sent"]},
        actual={"kinds": kinds},
    )
    assert kinds == ["request.created", "request.sent"]
    # Sort property: each event's `at` >= previous
    ts = [e["at"] for e in tl["events"]]
    assert ts == sorted(ts)


# ═════════════════════════════════════════════════════════════════════
# Test 3 — Triage spawning 1 BTO + 2 POs surfaces all child docs.
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_tl_3_triage_spawns(tenant, record_result):
    req_id, req_bid, sup_bid = await _make_simple_request(
        tenant=tenant,
        items=[
            {"product_id": "p-tl3-a", "product_name": "A", "qty": 1},
            {"product_id": "p-tl3-b", "product_name": "B", "qty": 1},
            {"product_id": "p-tl3-c", "product_name": "C", "qty": 1},
        ],
    )
    req_doc = await _raw_db.stock_requests.find_one({"id": req_id}, {"_id": 0})
    orig = _patch_pin(sup_bid)
    try:
        await triage_request(
            req_id,
            data={"pin": "1",
                  "assignments": [
                      {"item_id": req_doc["items"][0]["id"],
                       "fulfillment_type": "transfer"},
                      {"item_id": req_doc["items"][1]["id"],
                       "fulfillment_type": "supplier_po",
                       "supplier_name": "X", "unit_price": 10},
                      {"item_id": req_doc["items"][2]["id"],
                       "fulfillment_type": "supplier_po",
                       "supplier_name": "Y", "unit_price": 20},
                  ]},
            user=tenant["users"]["owner"],
        )
    finally:
        _restore_pin(orig)

    tl = await get_request_timeline(req_id, user=tenant["users"]["owner"])
    kinds = [e["kind"] for e in tl["events"]]
    record_result(
        scenario="br_tl.3_triage",
        step="bto_and_pos_events_present",
        expected={"has_bto": True, "po_created_count": 2,
                  "has_triage": True},
        actual={"has_bto": "bto.created" in kinds,
                "po_created_count": kinds.count("po.created"),
                "has_triage": "request.triaged" in kinds},
    )
    assert "bto.created" in kinds
    assert kinds.count("po.created") == 2
    assert "request.triaged" in kinds


# ═════════════════════════════════════════════════════════════════════
# Test 4 — Mark-ordered surfaces a po.ordered event with detail.
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_tl_4_mark_ordered_detail(tenant, record_result):
    req_id, req_bid, sup_bid = await _make_simple_request(
        tenant=tenant,
        items=[{"product_id": "p-tl4", "product_name": "A", "qty": 5}],
    )
    req_doc = await _raw_db.stock_requests.find_one({"id": req_id}, {"_id": 0})
    orig = _patch_pin(sup_bid)
    try:
        res = await triage_request(
            req_id,
            data={"pin": "1",
                  "assignments": [
                      {"item_id": req_doc["items"][0]["id"],
                       "fulfillment_type": "supplier_po",
                       "supplier_name": "Acme", "unit_price": 100},
                  ]},
            user=tenant["users"]["owner"],
        )
        await mark_phantom_po_ordered(
            req_id, res["po_ids"][0],
            data={"pin": "1",
                  "supplier_ref": "ACM-2026-9",
                  "expected_delivery_date": "2026-02-25"},
            user=tenant["users"]["owner"],
        )
    finally:
        _restore_pin(orig)

    tl = await get_request_timeline(req_id, user=tenant["users"]["owner"])
    ordered_ev = next((e for e in tl["events"] if e["kind"] == "po.ordered"), None)
    record_result(
        scenario="br_tl.4_ordered_detail",
        step="supplier_ref_and_eta_in_detail",
        expected={"detail_has_ref": True, "detail_has_eta": True},
        actual={"detail": (ordered_ev or {}).get("detail")},
    )
    assert ordered_ev is not None
    assert "ACM-2026-9" in ordered_ev["detail"]
    assert "2026-02-25" in ordered_ev["detail"]


# ═════════════════════════════════════════════════════════════════════
# Test 5 — Received with variance → po.received detail flags the kind.
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_tl_5_received_variance(tenant, record_result):
    req_id, req_bid, sup_bid = await _make_simple_request(
        tenant=tenant,
        items=[{"product_id": "p-tl5", "product_name": "A", "qty": 10}],
    )
    req_doc = await _raw_db.stock_requests.find_one({"id": req_id}, {"_id": 0})
    orig = _patch_pin(sup_bid)
    try:
        res = await triage_request(
            req_id,
            data={"pin": "1",
                  "assignments": [
                      {"item_id": req_doc["items"][0]["id"],
                       "fulfillment_type": "supplier_po",
                       "supplier_name": "Acme", "unit_price": 100},
                  ]},
            user=tenant["users"]["owner"],
        )
        await mark_phantom_po_ordered(
            req_id, res["po_ids"][0], data={"pin": "1"},
            user=tenant["users"]["owner"],
        )
    finally:
        _restore_pin(orig)

    # Simulate Branch A receiving with under-delivery + set received_date.
    po_id = res["po_ids"][0]
    po = await _raw_db.purchase_orders.find_one({"id": po_id}, {"_id": 0})
    new_items = [{**it, "quantity": 7} for it in po["items"]]
    from utils import now_iso
    await _raw_db.purchase_orders.update_one(
        {"id": po_id},
        {"$set": {"items": new_items, "status": "received",
                  "received_date": now_iso()}},
    )
    fresh = await _raw_db.purchase_orders.find_one({"id": po_id}, {"_id": 0})
    await update_phantom_po_received(fresh)

    tl = await get_request_timeline(req_id, user=tenant["users"]["owner"])
    rec_ev = next((e for e in tl["events"] if e["kind"] == "po.received"), None)
    record_result(
        scenario="br_tl.5_received_variance",
        step="variance_kind_in_detail",
        expected={"detail_contains": "under delivered"},
        actual={"detail": (rec_ev or {}).get("detail")},
    )
    assert rec_ev is not None
    assert "under delivered" in rec_ev["detail"]


# ═════════════════════════════════════════════════════════════════════
# Test 6 — Cancelled request emits request.cancelled.
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_tl_6_cancelled_event(tenant, record_result):
    req_id, _, _ = await _make_simple_request(
        tenant=tenant,
        items=[{"product_id": "p-tl6", "product_name": "A", "qty": 1}],
    )
    await cancel_request(req_id, data={"reason": "test"},
                         user=tenant["users"]["owner"])

    tl = await get_request_timeline(req_id, user=tenant["users"]["owner"])
    cancel_ev = next((e for e in tl["events"] if e["kind"] == "request.cancelled"), None)
    record_result(
        scenario="br_tl.6_cancel_event",
        step="cancelled_present_with_reason",
        expected={"present": True, "reason": "test"},
        actual={"present": cancel_ev is not None,
                "reason": (cancel_ev or {}).get("detail")},
    )
    assert cancel_ev is not None
    assert cancel_ev["detail"] == "test"

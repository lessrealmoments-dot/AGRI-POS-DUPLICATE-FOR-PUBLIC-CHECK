"""
br_stock_request_parked_receipt — pins the Feb 2026 Phase-3+ fixes that
make Stock-Request DRAFT POs behave like parked receipts AND ensure the
BTO from triage lands as an editable `draft` so the fulfilling branch
can review, add/remove items, and set prices before sending.

Behavior pinned:
  1. Snapshot is captured at TRIAGE time, not at Mark-Ordered.
  2. Mark-Ordered does NOT rewrite the snapshot — purely informational.
  3. Branch A can receive a DRAFT PO directly (without mark-ordered),
     and variance is still computed correctly against the triage
     snapshot.
  4. The BTO spawned from triage lands in `draft` (editable in BT
     composer), so the fulfilling branch can add/remove products and
     set transfer prices before sending.
"""
import pytest

from config import _raw_db
from routes import verify as verify_mod
from routes.stock_requests import (
    create_request,
    mark_phantom_po_ordered,
    send_request,
    triage_request,
    update_phantom_po_received,
)


def _patch_pin(bid):
    async def _ok(pin, action_key, branch_id=None):
        return {"verifier_id": "v", "verifier_name": "Mgr",
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


async def _make_triaged(*, tenant, lines, with_bto=False):
    """Triage with all lines as supplier_po by default; if with_bto=True,
    the first line becomes a transfer line."""
    org_id = tenant["org_id"]
    req_bid = tenant["branches"]["b2"]
    sup_bid = tenant["branches"]["main"]
    user = tenant["users"]["owner"]
    for ln in lines:
        await _seed_product(org_id=org_id, pid=ln["pid"], name=ln["name"])
    items = [{"product_id": ln["pid"], "product_name": ln["name"],
              "qty": ln["qty"], "unit": "pc"} for ln in lines]
    req = await create_request(
        data={"requesting_branch_id": req_bid, "supplying_branch_id": sup_bid,
              "items": items}, user=user,
    )
    await send_request(req["id"], user=user)
    rd = await _raw_db.stock_requests.find_one({"id": req["id"]}, {"_id": 0})

    assigns = []
    for i, ln in enumerate(lines):
        if with_bto and i == 0:
            assigns.append({"item_id": rd["items"][i]["id"],
                            "fulfillment_type": "transfer"})
        else:
            assigns.append({"item_id": rd["items"][i]["id"],
                            "fulfillment_type": "supplier_po",
                            "supplier_name": "Acme",
                            "unit_price": ln.get("unit_price", 100.0)})

    orig = _patch_pin(sup_bid)
    try:
        res = await triage_request(
            req["id"], data={"pin": "1", "assignments": assigns}, user=user,
        )
    finally:
        _restore_pin(orig)
    return req["id"], res


# ═════════════════════════════════════════════════════════════════════
# Test 1 — Snapshot stamped at TRIAGE.
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_pr_1_snapshot_at_triage(tenant, record_result):
    _, res = await _make_triaged(
        tenant=tenant,
        lines=[{"pid": "p-pr1", "name": "A", "qty": 10, "unit_price": 100.0}],
    )
    po = await _raw_db.purchase_orders.find_one({"id": res["po_ids"][0]}, {"_id": 0})
    snap = po.get("ordered_snapshot") or {}
    record_result(
        scenario="br_pr.1_snapshot_at_triage",
        step="snapshot_present_before_mark_ordered",
        expected={"has_snapshot": True, "source": "triage",
                  "items_count": 1},
        actual={"has_snapshot": bool(snap),
                "source": snap.get("source"),
                "items_count": len(snap.get("items", []))},
    )
    assert snap, "Snapshot must be stamped at triage, not at mark-ordered"
    assert snap["source"] == "triage"
    assert len(snap["items"]) == 1
    assert snap["items"][0]["quantity"] == 10
    # PO is still in DRAFT status — snapshot doesn't depend on Mark-Ordered.
    assert po["status"] == "draft"


# ═════════════════════════════════════════════════════════════════════
# Test 2 — Mark-Ordered does NOT rewrite snapshot.
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_pr_2_mark_ordered_preserves_snapshot(tenant, record_result):
    sup_bid = tenant["branches"]["main"]
    req_id, res = await _make_triaged(
        tenant=tenant,
        lines=[{"pid": "p-pr2", "name": "A", "qty": 10, "unit_price": 100.0}],
    )
    po_id = res["po_ids"][0]
    before = await _raw_db.purchase_orders.find_one({"id": po_id}, {"_id": 0})
    snap_before = before["ordered_snapshot"]

    orig = _patch_pin(sup_bid)
    try:
        # Mark-Ordered with a different price — snapshot should NOT shift.
        await mark_phantom_po_ordered(
            req_id, po_id,
            data={"pin": "1",
                  "item_overrides": [
                      {"item_id": before["items"][0]["source_request_item_id"],
                       "unit_price": 80.0}
                  ]},
            user=tenant["users"]["owner"],
        )
    finally:
        _restore_pin(orig)

    after = await _raw_db.purchase_orders.find_one({"id": po_id}, {"_id": 0})
    snap_after = after["ordered_snapshot"]

    record_result(
        scenario="br_pr.2_mark_ordered_snapshot_immutable",
        step="snapshot_unchanged_after_mark_ordered",
        expected={"snapshot_qty":         10.0,
                  "snapshot_unit_price":  100.0,
                  "live_unit_price":      80.0},
        actual={"snapshot_qty":        snap_after["items"][0]["quantity"],
                "snapshot_unit_price": snap_after["items"][0]["unit_price"],
                "live_unit_price":     after["items"][0]["unit_price"]},
    )
    assert snap_after["source"] == "triage"
    assert snap_after["items"][0]["unit_price"] == 100.0   # triage baseline preserved
    assert after["items"][0]["unit_price"] == 80.0          # live state updated
    # captured_at field should also be unchanged
    assert snap_after.get("captured_at") == snap_before.get("captured_at")


# ═════════════════════════════════════════════════════════════════════
# Test 3 — Receive a DRAFT PO directly (skip Mark-Ordered) → variance
# is correctly computed against the triage snapshot.
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_pr_3_receive_draft_direct(tenant, record_result):
    req_id, res = await _make_triaged(
        tenant=tenant,
        lines=[{"pid": "p-pr3", "name": "A", "qty": 10, "unit_price": 100.0}],
    )
    po_id = res["po_ids"][0]

    # Simulate Branch A opening the DRAFT, adjusting qty downward to
    # reflect actuals, and clicking Receive — WITHOUT Mark-Ordered first.
    po = await _raw_db.purchase_orders.find_one({"id": po_id}, {"_id": 0})
    edited_items = [{**it, "quantity": 7} for it in po["items"]]
    from utils import now_iso
    await _raw_db.purchase_orders.update_one(
        {"id": po_id},
        {"$set": {"items": edited_items, "status": "received",
                  "received_date": now_iso()}},
    )

    fresh = await _raw_db.purchase_orders.find_one({"id": po_id}, {"_id": 0})
    await update_phantom_po_received(fresh)

    after = await _raw_db.purchase_orders.find_one({"id": po_id}, {"_id": 0})
    record_result(
        scenario="br_pr.3_receive_draft_direct",
        step="variance_computed_without_mark_ordered",
        expected={"kind": "under_delivered"},
        actual={"kind": after.get("received_variance_kind")},
    )
    assert after["received_variance_kind"] == "under_delivered"
    # Mark-Ordered was never called — confirm we still got here.
    assert not after.get("ordered_at"), "Mark-Ordered was NOT performed in this test"


# ═════════════════════════════════════════════════════════════════════
# Test 4 — BTO from triage lands in `draft` so the fulfilling branch
# can review, edit items, and set transfer prices before sending.
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_pr_4_bto_lands_in_pending_approval(tenant, record_result):
    _, res = await _make_triaged(
        tenant=tenant,
        lines=[
            {"pid": "p-pr4-a", "name": "A", "qty": 5},
            {"pid": "p-pr4-b", "name": "B", "qty": 3, "unit_price": 100.0},
        ],
        with_bto=True,
    )
    bto = await _raw_db.branch_transfer_orders.find_one(
        {"id": res["bto_id"]}, {"_id": 0}
    )
    record_result(
        scenario="br_pr.4_bto_lands_in_draft",
        step="editable_draft",
        expected={"status": "draft"},
        actual={"status": bto.get("status")},
    )
    assert bto is not None
    assert bto["status"] == "draft", (
        f"BTO should land in draft (editable in BT composer), "
        f"got {bto.get('status')!r}"
    )


# ═════════════════════════════════════════════════════════════════════
# Test 5 — Adding an EXTRA line at receive (parked-receipt scenario)
# triggers extra_items variance + SMS to supplying branch.
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_pr_5_add_extra_at_receive(tenant, record_result):
    org_id = tenant["org_id"]
    sup_bid = tenant["branches"]["main"]
    # Seed a supplying-branch manager for SMS recipient.
    await _raw_db.users.update_one(
        {"id": f"pr5-mgr-{org_id[-4:]}"},
        {"$set": {"id": f"pr5-mgr-{org_id[-4:]}", "username": "pr5-mgr",
                  "full_name": "Sup Mgr", "organization_id": org_id,
                  "role": "manager", "active": True,
                  "branch_id": sup_bid, "branch_ids": [sup_bid],
                  "phone": "+639170005555"}},
        upsert=True,
    )

    req_id, res = await _make_triaged(
        tenant=tenant,
        lines=[{"pid": "p-pr5", "name": "Ordered Item", "qty": 5,
                "unit_price": 100.0}],
    )
    po_id = res["po_ids"][0]

    # Branch A adds an EXTRA line that wasn't on the original PO (parked
    # receipt scenario — supplier shipped a bonus item).
    po = await _raw_db.purchase_orders.find_one({"id": po_id}, {"_id": 0})
    new_items = list(po["items"]) + [{
        "product_id":   "bonus-item",
        "product_name": "Bonus Item",
        "quantity":     2, "unit_price": 0, "total": 0,
    }]
    from utils import now_iso
    await _raw_db.purchase_orders.update_one(
        {"id": po_id},
        {"$set": {"items": new_items, "status": "received",
                  "received_date": now_iso()}},
    )

    # Spy on SMS so we confirm the supplying-branch manager is notified.
    from routes import sms as sms_mod
    captured = []
    async def _spy(**kw):
        captured.append(kw); return {"ok": True}
    original = sms_mod.queue_sms
    sms_mod.queue_sms = _spy
    try:
        fresh = await _raw_db.purchase_orders.find_one({"id": po_id}, {"_id": 0})
        await update_phantom_po_received(fresh)
    finally:
        sms_mod.queue_sms = original

    after = await _raw_db.purchase_orders.find_one({"id": po_id}, {"_id": 0})
    sms_calls = [c for c in captured if c.get("template_key") == "phantom_po_variance"]
    record_result(
        scenario="br_pr.5_extra_at_receive",
        step="extra_items_kind_and_supplying_mgr_alerted",
        expected={"kind": "extra_items", "sms_fired": True},
        actual={"kind": after.get("received_variance_kind"),
                "sms_fired": len(sms_calls) > 0,
                "manager_alerted": any(c.get("phone") == "+639170005555" for c in sms_calls)},
    )
    assert after["received_variance_kind"] == "extra_items"
    assert sms_calls, "Adding an item at receive must alert the supplying branch"
    assert any(c.get("phone") == "+639170005555" for c in sms_calls)

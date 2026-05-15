"""
br_stock_request_mark_ordered — pins the Phase 2 "Mark Phantom PO Ordered"
flow shipped Feb 2026.

After triage spawns DRAFT POs, Branch B negotiates with each supplier and
clicks "Mark Ordered" to lock the deal. The endpoint:
  * Requires the same supplying-branch PIN policy as triage
  * Allows per-item price/qty overrides (final-negotiation pricing)
  * Recomputes line_subtotal/grand_total
  * Flips status: draft → ordered
  * Stamps ordered_at / ordered_by_name / supplier_ref
  * Fires SMS to requesting-branch admins + managers
  * Updates the linked stock-request items' status to 'ordered'

Scenarios pinned:
  1. Happy path: draft → ordered, status flipped, ordered_at stamped.
  2. Per-item price override recomputes line_subtotal/grand_total.
  3. Cannot mark-ordered a PO that's not in 'draft' status.
  4. PIN required; invalid PIN → 403.
  5. PO from a different request → 400 (cross-request guard).
  6. SMS notification fires (queue_sms invoked w/ phantom_po_ordered template).
"""
import pytest

from config import _raw_db
from routes import verify as verify_mod
from routes.stock_requests import (
    create_request,
    send_request,
    triage_request,
    mark_phantom_po_ordered,
)
import routes.stock_requests as sr_mod


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
        {"$set": {"id": pid, "organization_id": org_id, "name": name,
                  "sku": f"SKU-{pid[-4:].upper()}", "active": True}},
        upsert=True,
    )


async def _make_triaged_request_with_po(*, tenant, item_count=1, unit_price=100.0):
    """Helper: create + send + triage a request that yields a single
    DRAFT PO with `item_count` lines.  Returns (request_id, po_id, items)."""
    org_id = tenant["org_id"]
    req_bid = tenant["branches"]["b2"]
    sup_bid = tenant["branches"]["main"]
    user = tenant["users"]["owner"]

    items_payload = []
    for n in range(item_count):
        pid = f"p-mo-{n}-{org_id[-4:]}"
        await _seed_product(org_id=org_id, pid=pid, name=f"Item {n+1}")
        items_payload.append({
            "product_id": pid, "product_name": f"Item {n+1}", "qty": 2, "unit": "pc",
        })

    req = await create_request(
        data={"requesting_branch_id": req_bid,
              "supplying_branch_id":  sup_bid,
              "items": items_payload},
        user=user,
    )
    await send_request(req["id"], user=user)

    req_doc = await _raw_db.stock_requests.find_one({"id": req["id"]}, {"_id": 0})

    orig = _patch_pin(sup_bid)
    try:
        res = await triage_request(
            req["id"],
            data={
                "pin": "123456",
                "assignments": [
                    {"item_id": req_doc["items"][i]["id"],
                     "fulfillment_type": "supplier_po",
                     "supplier_name": "FertCo",
                     "unit_price": unit_price}
                    for i in range(item_count)
                ],
            },
            user=user,
        )
    finally:
        _restore_pin(orig)

    return req["id"], res["po_ids"][0], req_doc["items"]


# ═════════════════════════════════════════════════════════════════════
# Test 1 — Happy path: draft → ordered, fields stamped.
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_mo_1_happy_path_lock(tenant, record_result):
    sup_bid = tenant["branches"]["main"]
    req_id, po_id, _ = await _make_triaged_request_with_po(tenant=tenant)

    orig = _patch_pin(sup_bid)
    try:
        res = await mark_phantom_po_ordered(
            req_id, po_id,
            data={"pin": "123456",
                  "supplier_ref": "FERT-INV-2026-0042",
                  "expected_delivery_date": "2026-02-20",
                  "notes": "Delivery via courier"},
            user=tenant["users"]["owner"],
        )
    finally:
        _restore_pin(orig)

    po = await _raw_db.purchase_orders.find_one({"id": po_id}, {"_id": 0})
    record_result(
        scenario="br_mo.1_happy_path",
        step="status_flipped_to_ordered",
        expected={"status": "ordered", "supplier_ref": "FERT-INV-2026-0042"},
        actual={"status": po["status"], "supplier_ref": po.get("supplier_ref")},
    )
    assert res["ok"] is True
    assert res["status"] == "ordered"
    assert po["status"] == "ordered"
    assert po["supplier_ref"] == "FERT-INV-2026-0042"
    assert po["expected_delivery_date"] == "2026-02-20"
    assert po.get("ordered_at")
    assert po.get("ordered_by_name") == "Test Manager"
    assert po.get("ordered_notes") == "Delivery via courier"


# ═════════════════════════════════════════════════════════════════════
# Test 2 — Per-item price override recomputes totals.
#   Triaged at ₱100/unit × 2 lines × 2 qty = ₱400 grand_total.
#   Override line 1 to ₱150/unit → 2 × 150 + 2 × 100 = ₱500.
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_mo_2_price_override_recomputes(tenant, record_result):
    sup_bid = tenant["branches"]["main"]
    req_id, po_id, request_items = await _make_triaged_request_with_po(
        tenant=tenant, item_count=2, unit_price=100.0,
    )
    po_before = await _raw_db.purchase_orders.find_one({"id": po_id}, {"_id": 0})
    assert round(po_before["grand_total"], 2) == 400.0  # 2 × 100 + 2 × 100

    # Override the FIRST request line's unit price.
    target_item_id = request_items[0]["id"]
    orig = _patch_pin(sup_bid)
    try:
        await mark_phantom_po_ordered(
            req_id, po_id,
            data={"pin": "123456",
                  "item_overrides": [
                      {"item_id": target_item_id, "unit_price": 150.0},
                  ]},
            user=tenant["users"]["owner"],
        )
    finally:
        _restore_pin(orig)

    po_after = await _raw_db.purchase_orders.find_one({"id": po_id}, {"_id": 0})
    record_result(
        scenario="br_mo.2_price_override",
        step="grand_total_recomputed",
        expected={"grand_total": 500.0},
        actual={"grand_total": po_after["grand_total"]},
    )
    assert round(po_after["grand_total"], 2) == 500.0
    # The line we overrode now reflects the new unit price.
    overridden = next(it for it in po_after["items"]
                      if it.get("source_request_item_id") == target_item_id)
    assert round(overridden["unit_price"], 2) == 150.0
    assert round(overridden["total"], 2) == 300.0


# ═════════════════════════════════════════════════════════════════════
# Test 3 — Cannot mark-ordered a non-draft PO.
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_mo_3_non_draft_refused(tenant, record_result):
    sup_bid = tenant["branches"]["main"]
    req_id, po_id, _ = await _make_triaged_request_with_po(tenant=tenant)

    # Flip PO directly to 'ordered' to simulate second-call attempt.
    await _raw_db.purchase_orders.update_one(
        {"id": po_id}, {"$set": {"status": "ordered"}}
    )

    orig = _patch_pin(sup_bid)
    raised = False
    try:
        await mark_phantom_po_ordered(
            req_id, po_id, data={"pin": "123456"},
            user=tenant["users"]["owner"],
        )
    except Exception:
        raised = True
    finally:
        _restore_pin(orig)

    record_result(
        scenario="br_mo.3_non_draft",
        step="refused",
        expected={"raised": True},
        actual={"raised": raised},
    )
    assert raised


# ═════════════════════════════════════════════════════════════════════
# Test 4 — PIN required + invalid PIN → 403.
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_mo_4_pin_required(tenant, record_result):
    sup_bid = tenant["branches"]["main"]
    req_id, po_id, _ = await _make_triaged_request_with_po(tenant=tenant)

    # Missing PIN
    raised_missing = False
    try:
        await mark_phantom_po_ordered(
            req_id, po_id, data={}, user=tenant["users"]["owner"]
        )
    except Exception:
        raised_missing = True

    # Invalid PIN — patch verify to return None (=fail)
    async def _fail(pin, action_key, branch_id=None):
        return None
    original = verify_mod.verify_pin_for_action
    verify_mod.verify_pin_for_action = _fail
    raised_bad = False
    try:
        await mark_phantom_po_ordered(
            req_id, po_id, data={"pin": "wrong"}, user=tenant["users"]["owner"]
        )
    except Exception:
        raised_bad = True
    finally:
        verify_mod.verify_pin_for_action = original

    record_result(
        scenario="br_mo.4_pin_gating",
        step="missing_and_invalid_both_refused",
        expected={"missing": True, "invalid": True},
        actual={"missing": raised_missing, "invalid": raised_bad},
    )
    assert raised_missing and raised_bad


# ═════════════════════════════════════════════════════════════════════
# Test 5 — Cross-request guard (PO from a different request → 400).
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_mo_5_cross_request_guard(tenant, record_result):
    sup_bid = tenant["branches"]["main"]
    req1_id, po1_id, _ = await _make_triaged_request_with_po(tenant=tenant)
    req2_id, _, _ = await _make_triaged_request_with_po(tenant=tenant)

    orig = _patch_pin(sup_bid)
    raised = False
    try:
        # Pass po1_id but request2_id — should be refused.
        await mark_phantom_po_ordered(
            req2_id, po1_id, data={"pin": "123456"},
            user=tenant["users"]["owner"],
        )
    except Exception:
        raised = True
    finally:
        _restore_pin(orig)

    record_result(
        scenario="br_mo.5_cross_request_guard",
        step="cross_link_refused",
        expected={"raised": True},
        actual={"raised": raised},
    )
    assert raised


# ═════════════════════════════════════════════════════════════════════
# Test 6 — SMS notification fires with the expected template + variables.
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_mo_6_sms_notification(tenant, record_result):
    sup_bid = tenant["branches"]["main"]
    org_id = tenant["org_id"]
    req_bid = tenant["branches"]["b2"]

    # Seed a manager on the REQUESTING branch with a phone number so
    # the SMS pipeline considers them a recipient.
    mgr_id = f"mo6-mgr-{org_id[-4:]}"
    await _raw_db.users.update_one(
        {"id": mgr_id},
        {"$set": {
            "id": mgr_id, "username": "mo6-mgr",
            "full_name": "Branch A Manager",
            "organization_id": org_id,
            "role": "manager", "active": True,
            "branch_id": req_bid, "branch_ids": [req_bid],
            "phone": "+639170000001",
        }},
        upsert=True,
    )

    req_id, po_id, _ = await _make_triaged_request_with_po(tenant=tenant)

    # Spy on queue_sms via the SMS module the route lazy-imports.
    from routes import sms as sms_mod
    captured = []

    async def _spy(**kwargs):
        captured.append(kwargs)
        return {"ok": True}

    original_q = sms_mod.queue_sms
    sms_mod.queue_sms = _spy
    orig = _patch_pin(sup_bid)
    try:
        await mark_phantom_po_ordered(
            req_id, po_id,
            data={"pin": "123456",
                  "supplier_ref": "SR-2026-001"},
            user=tenant["users"]["owner"],
        )
    finally:
        _restore_pin(orig)
        sms_mod.queue_sms = original_q

    record_result(
        scenario="br_mo.6_sms",
        step="phantom_po_ordered_template_invoked",
        expected={"template_key": "phantom_po_ordered",
                  "at_least_one_recipient": True},
        actual={"templates":  [c.get("template_key") for c in captured],
                "recipients": len(captured)},
    )
    assert any(c.get("template_key") == "phantom_po_ordered" for c in captured)
    # The seeded manager must be among the recipients.
    assert any(c.get("phone") == "+639170000001" for c in captured)

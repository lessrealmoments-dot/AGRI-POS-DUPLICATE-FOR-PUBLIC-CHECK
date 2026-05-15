"""
br_stock_request_variance_sms — pins that the supplying branch is
notified via SMS when a phantom PO comes back from the supplier with a
non-trivial variance.

  • completed       → no SMS (silence is golden)
  • under/over/extra/missing → SMS to supplying-branch admins + managers
                                using template 'phantom_po_variance'

The summary string built into the SMS variables is also asserted so
template authors / future re-wording can rely on a stable shape.
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
        return {"verifier_id": "v", "verifier_name": "M", "verifier_role": "manager",
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


async def _seed_supplying_branch_manager(*, org_id, sup_bid):
    mgr_id = f"vsms-mgr-{org_id[-4:]}"
    await _raw_db.users.update_one(
        {"id": mgr_id},
        {"$set": {"id": mgr_id, "username": "vsms-mgr",
                  "full_name": "Supplying Manager",
                  "organization_id": org_id, "role": "manager",
                  "active": True,
                  "branch_id": sup_bid, "branch_ids": [sup_bid],
                  "phone": "+639170009999"}},
        upsert=True,
    )
    return mgr_id


async def _setup_received_po(*, tenant, ordered_qty, received_qty,
                              add_extra=False, drop_line=False):
    """Build a phantom PO scenario whose 'received' state is whatever the
    caller specifies, then return (request_id, po_id) ready for
    `update_phantom_po_received`."""
    org_id = tenant["org_id"]
    req_bid = tenant["branches"]["b2"]
    sup_bid = tenant["branches"]["main"]
    user = tenant["users"]["owner"]

    pid_a = f"pa-{org_id[-4:]}"
    pid_b = f"pb-{org_id[-4:]}"
    await _seed_product(org_id=org_id, pid=pid_a, name="Item A")
    await _seed_product(org_id=org_id, pid=pid_b, name="Item B")

    items = [
        {"product_id": pid_a, "product_name": "Item A", "qty": ordered_qty, "unit": "pc"},
    ]
    if drop_line:
        items.append({"product_id": pid_b, "product_name": "Item B", "qty": 4, "unit": "pc"})

    req = await create_request(
        data={"requesting_branch_id": req_bid,
              "supplying_branch_id":  sup_bid, "items": items},
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
                      {"item_id": it["id"], "fulfillment_type": "supplier_po",
                       "supplier_name": "Acme", "unit_price": 100.0}
                      for it in req_doc["items"]
                  ]},
            user=user,
        )
        await mark_phantom_po_ordered(
            req["id"], res["po_ids"][0],
            data={"pin": "1"}, user=user,
        )
    finally:
        _restore_pin(orig)

    po_id = res["po_ids"][0]

    # Mutate the PO `items` to simulate Branch A's receive state.
    po = await _raw_db.purchase_orders.find_one({"id": po_id}, {"_id": 0})
    new_items = []
    for it in po["items"]:
        if drop_line and it["product_id"] == pid_b:
            # Simulate 'missing' — supplier failed to deliver line B entirely.
            continue
        copy = dict(it)
        if it["product_id"] == pid_a:
            copy["quantity"] = float(received_qty)
        new_items.append(copy)
    if add_extra:
        new_items.append({"product_id": "bonus-x",
                          "product_name": "Bonus Item",
                          "quantity": 1, "unit_price": 0, "total": 0})
    await _raw_db.purchase_orders.update_one(
        {"id": po_id}, {"$set": {"items": new_items}}
    )
    return req["id"], po_id


# ═════════════════════════════════════════════════════════════════════
# Test 1 — Variance SMS fires on under_delivered.
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_vsms_1_under_delivered_alerts_supplying(tenant, record_result):
    org_id = tenant["org_id"]
    sup_bid = tenant["branches"]["main"]
    await _seed_supplying_branch_manager(org_id=org_id, sup_bid=sup_bid)

    req_id, po_id = await _setup_received_po(
        tenant=tenant, ordered_qty=10, received_qty=8,
    )

    from routes import sms as sms_mod
    captured = []

    async def _spy(**kwargs):
        captured.append(kwargs)
        return {"ok": True}
    original_q = sms_mod.queue_sms
    sms_mod.queue_sms = _spy
    try:
        po = await _raw_db.purchase_orders.find_one({"id": po_id}, {"_id": 0})
        await update_phantom_po_received(po)
    finally:
        sms_mod.queue_sms = original_q

    variance_calls = [c for c in captured if c.get("template_key") == "phantom_po_variance"]
    record_result(
        scenario="br_vsms.1_under",
        step="supplying_manager_sms_fired",
        expected={"template": "phantom_po_variance",
                  "supplying_mgr_alerted": True},
        actual={"template": variance_calls[0].get("template_key") if variance_calls else None,
                "phones": [c.get("phone") for c in variance_calls]},
    )
    assert len(variance_calls) >= 1, "Variance SMS should fire on under_delivered"
    assert any(c.get("phone") == "+639170009999" for c in variance_calls)
    # Variables on the SMS should carry a useful summary.
    var = variance_calls[0]["variables"]
    assert var["variance_kind"] == "under_delivered"
    assert "8/10" in var["variance_summary"] or "Item A" in var["variance_summary"]


# ═════════════════════════════════════════════════════════════════════
# Test 2 — No SMS fires on `completed` variance (silence is correct).
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_vsms_2_completed_silent(tenant, record_result):
    org_id = tenant["org_id"]
    sup_bid = tenant["branches"]["main"]
    await _seed_supplying_branch_manager(org_id=org_id, sup_bid=sup_bid)

    req_id, po_id = await _setup_received_po(
        tenant=tenant, ordered_qty=10, received_qty=10,
    )

    from routes import sms as sms_mod
    captured = []
    async def _spy(**kwargs):
        captured.append(kwargs); return {"ok": True}
    original_q = sms_mod.queue_sms
    sms_mod.queue_sms = _spy
    try:
        po = await _raw_db.purchase_orders.find_one({"id": po_id}, {"_id": 0})
        await update_phantom_po_received(po)
    finally:
        sms_mod.queue_sms = original_q

    variance_calls = [c for c in captured if c.get("template_key") == "phantom_po_variance"]
    record_result(
        scenario="br_vsms.2_completed",
        step="no_variance_sms_on_completed",
        expected={"variance_sms_count": 0},
        actual={"variance_sms_count": len(variance_calls)},
    )
    assert len(variance_calls) == 0, "No variance SMS should fire when receive matches order"


# ═════════════════════════════════════════════════════════════════════
# Test 3 — missing_items + extra_items combined produces ONE summary
# SMS whose variance_kind_label is the most-severe ('missing items').
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_vsms_3_missing_precedence(tenant, record_result):
    org_id = tenant["org_id"]
    sup_bid = tenant["branches"]["main"]
    await _seed_supplying_branch_manager(org_id=org_id, sup_bid=sup_bid)

    # 2 lines ordered, line B dropped on receive + a bonus line added.
    req_id, po_id = await _setup_received_po(
        tenant=tenant, ordered_qty=5, received_qty=5,
        add_extra=True, drop_line=True,
    )

    from routes import sms as sms_mod
    captured = []
    async def _spy(**kwargs):
        captured.append(kwargs); return {"ok": True}
    original_q = sms_mod.queue_sms
    sms_mod.queue_sms = _spy
    try:
        po = await _raw_db.purchase_orders.find_one({"id": po_id}, {"_id": 0})
        await update_phantom_po_received(po)
    finally:
        sms_mod.queue_sms = original_q

    variance_calls = [c for c in captured if c.get("template_key") == "phantom_po_variance"]
    record_result(
        scenario="br_vsms.3_precedence",
        step="kind_label_missing_items",
        expected={"kind": "missing_items"},
        actual={"kind": variance_calls[0]["variables"]["variance_kind"]
                if variance_calls else None},
    )
    assert variance_calls
    assert variance_calls[0]["variables"]["variance_kind"] == "missing_items"
    assert variance_calls[0]["variables"]["variance_kind_label"] == "missing items"

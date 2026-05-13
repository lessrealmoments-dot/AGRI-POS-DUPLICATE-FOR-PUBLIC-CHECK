"""
br_bt_var — Phase 3: Branch Transfer Variance + Internal Invoice Integrity.

Locks:
  * Accepting variance with a capital_loss > BT_VARIANCE_PIN_THRESHOLD
    requires PIN/TOTP via the new `transfer_variance_accept` policy,
    branch-scoped to `from_branch_id`. Low-value variance keeps the
    fast JWT path.
  * After `/accept-receipt`, the linked internal invoice line items are
    rewritten to reflect `qty_received` (not the original sent qty).
    Subtotal/grand_total recompute to the received total. `original_sent_qty`
    is preserved per line; PO-level `variance_history[]` row appended.
  * Exact-receive path leaves invoice line items unchanged.
  * Silent invoice-creation failures (BTO create-time) now surface as
    `audit_log[type=internal_invoice_creation_failed]` + BTO flag
    `invoice_creation_failed=True`.
  * Incident tickets carry denormalized requested/approved/sent/received
    qty per line + `request_po_id` / `invoice_id` links + PIN verifier
    metadata.

Phase 0 stock invariants stay green — stock still moves once, only in
`_apply_receipt`, only at received qty.
"""
import os
import sys

import pytest
import pytest_asyncio
from fastapi import HTTPException

BACKEND = os.path.join(os.path.dirname(__file__), "..", "..")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from config import _raw_db                                       # noqa: E402
from tests.business_regression._fixtures import seed_product     # noqa: E402
from tests.phase2b._fixtures import _uid, fake_user              # noqa: E402
from routes.branch_transfers import (                            # noqa: E402
    create_transfer,
    send_transfer,
    receive_transfer,
    accept_receipt,
)
from routes.purchase_orders import create_purchase_order         # noqa: E402


# Phase 3 — capital loss in pesos above which PIN is required.
PIN_THRESHOLD = 5000.0

APPROVE_PERMS = {
    "branch_transfers": {"approve": True, "create": True, "update": True,
                          "delete": True, "read": True},
    "purchase_orders": {"create": True, "read": True, "update": True, "delete": True},
}


@pytest_asyncio.fixture(scope="module")
async def extra_users(tenant):
    """Seed source-branch manager + destination-branch manager + admin
    user, each with deterministic manager_pin."""
    org_id = tenant["org_id"]
    main = tenant["branches"]["main"]
    b2 = tenant["branches"]["b2"]

    src_mgr_id = _uid("br_bt_var-srcMgr")
    dst_mgr_id = _uid("br_bt_var-dstMgr")
    admin_id = _uid("br_bt_var-admin")
    src_pin, dst_pin, admin_pin = "511511", "622622", "733733"
    await _raw_db.users.insert_many([
        {"id": src_mgr_id, "username": f"src-mgr-{src_mgr_id[-4:]}",
         "full_name": "Source Mgr (br_bt_var)", "organization_id": org_id,
         "role": "manager", "active": True,
         "branch_ids": [main], "branch_id": main,
         "manager_pin": src_pin, "permissions": APPROVE_PERMS},
        {"id": dst_mgr_id, "username": f"dst-mgr-{dst_mgr_id[-4:]}",
         "full_name": "Dest Mgr (br_bt_var)", "organization_id": org_id,
         "role": "manager", "active": True,
         "branch_ids": [b2], "branch_id": b2,
         "manager_pin": dst_pin, "permissions": APPROVE_PERMS},
        {"id": admin_id, "username": f"admin-{admin_id[-4:]}",
         "full_name": "Org Admin (br_bt_var)", "organization_id": org_id,
         "role": "admin", "active": True,
         "branch_ids": [], "branch_id": "",
         "manager_pin": admin_pin},
    ])
    yield {
        "src_mgr": fake_user(org_id, src_mgr_id, branch_id=main,
                              role="manager", perms=APPROVE_PERMS),
        "dst_mgr": fake_user(org_id, dst_mgr_id, branch_id=b2,
                              role="manager", perms=APPROVE_PERMS),
        "src_pin": src_pin,
        "dst_pin": dst_pin,
        "admin_pin": admin_pin,
    }


def _bto_payload(from_b, to_b, pid, name, *, qty,
                 capital=60.0, retail=100.0,
                 request_po_id="", request_po_number=""):
    return {
        "from_branch_id": from_b, "to_branch_id": to_b,
        "items": [{
            "product_id": pid, "product_name": name,
            "sku": f"SKU-{pid[-6:]}", "unit": "pc",
            "qty": qty, "branch_capital": capital,
            "transfer_capital": capital, "branch_retail": retail,
        }],
        "min_margin": 20,
        "request_po_id": request_po_id,
        "request_po_number": request_po_number,
        "notes": "br_bt_var regression",
    }


async def _bto(transfer_id):
    return await _raw_db.branch_transfer_orders.find_one(
        {"id": transfer_id}, {"_id": 0}) or {}


async def _invoice_for_transfer(transfer_id):
    return await _raw_db.internal_invoices.find_one(
        {"transfer_id": transfer_id}, {"_id": 0}) or {}


async def _audit_rows(predicate):
    return await _raw_db.audit_log.find(predicate, {"_id": 0}).to_list(50)


async def _call(coro):
    try:
        return ("ok", await coro)
    except HTTPException as e:
        return ("err", (e.status_code, e.detail))


async def _drive_variance(tenant, *, sent_qty, received_qty,
                          unit_cost=60.0, retail=100.0,
                          name_tag="br_bt_var"):
    """Helper: seed product → create BTO → send → receive at received_qty.
    Returns (product_id, bto_id, src_before, dst_before)."""
    org_id = tenant["org_id"]
    main = tenant["branches"]["main"]
    b2 = tenant["branches"]["b2"]
    owner = tenant["users"]["owner"]
    pid = await seed_product(org_id, main, name=f"{name_tag} P",
                              stock=sent_qty + 50, cost=unit_cost, price=retail)
    draft = await create_transfer(
        _bto_payload(main, b2, pid, f"{name_tag} P",
                      qty=sent_qty, capital=unit_cost, retail=retail),
        user=owner)
    await send_transfer(draft["id"], user=owner)
    await receive_transfer(
        draft["id"],
        data={"skip_receipt_check": True,
              "items": [{"product_id": pid, "qty_received": received_qty}]},
        user=owner)
    return pid, draft["id"]


# ═════════════════════════════════════════════════════════════════════
# Test 1 — invoice qty matches received_qty after accept shortage
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_br_bt_var_1_invoice_qty_matches_received_after_shortage(
    tenant, record_result
):
    owner = tenant["users"]["owner"]
    pid, bto_id = await _drive_variance(
        tenant, sent_qty=10, received_qty=7, name_tag="br_bt_var1")
    inv_before = await _invoice_for_transfer(bto_id)
    qty_before = float(inv_before["items"][0]["qty"])

    await accept_receipt(bto_id, data={"action": "accept", "note": "ok"},
                          user=owner)
    inv_after = await _invoice_for_transfer(bto_id)
    qty_after = float(inv_after["items"][0]["qty"])
    line_total_after = float(inv_after["items"][0]["line_total"])
    original_sent = float(inv_after["items"][0].get("original_sent_qty") or 0)

    ev = {"bto_id": bto_id,
          "qty_before": qty_before, "qty_after": qty_after,
          "line_total_after": line_total_after,
          "original_sent_qty": original_sent}
    record_result(
        scenario="br_bt_var.1_invoice_qty_matches_received_after_shortage",
        step="line_qty_rewritten_to_7",
        expected={"qty": 7.0}, actual={"qty": qty_after}, evidence=ev,
    )
    record_result(
        scenario="br_bt_var.1_invoice_qty_matches_received_after_shortage",
        step="line_total_recomputed",
        expected={"line_total": 7.0 * 60.0},
        actual={"line_total": line_total_after}, evidence=ev,
    )
    record_result(
        scenario="br_bt_var.1_invoice_qty_matches_received_after_shortage",
        step="original_sent_qty_preserved",
        expected={"sent": 10.0}, actual={"sent": original_sent}, evidence=ev,
    )
    assert qty_after == 7.0
    assert line_total_after == 7.0 * 60.0
    assert original_sent == 10.0


# ═════════════════════════════════════════════════════════════════════
# Test 2 — invoice qty matches received_qty after accept excess
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_br_bt_var_2_invoice_qty_matches_received_after_excess(
    tenant, record_result
):
    owner = tenant["users"]["owner"]
    # Excess: source sends 10, destination claims to have received 12
    pid, bto_id = await _drive_variance(
        tenant, sent_qty=10, received_qty=12, name_tag="br_bt_var2")

    await accept_receipt(bto_id, data={"action": "accept", "note": "ok"},
                          user=owner)
    inv = await _invoice_for_transfer(bto_id)
    qty_after = float(inv["items"][0]["qty"])
    line_total = float(inv["items"][0]["line_total"])

    ev = {"bto_id": bto_id, "qty_after": qty_after, "line_total": line_total}
    record_result(
        scenario="br_bt_var.2_invoice_qty_matches_received_after_excess",
        step="line_qty_rewritten_to_12",
        expected={"qty": 12.0}, actual={"qty": qty_after}, evidence=ev,
    )
    assert qty_after == 12.0
    assert line_total == 12.0 * 60.0


# ═════════════════════════════════════════════════════════════════════
# Test 3 — grand_total matches received_total after accept
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_br_bt_var_3_invoice_grand_total_matches_received_total(
    tenant, record_result
):
    owner = tenant["users"]["owner"]
    pid, bto_id = await _drive_variance(
        tenant, sent_qty=10, received_qty=8, name_tag="br_bt_var3")

    await accept_receipt(bto_id, data={"action": "accept", "note": "ok"},
                          user=owner)
    inv = await _invoice_for_transfer(bto_id)
    sub = float(inv["subtotal"])
    grand = float(inv["grand_total"])
    received_total = float(inv["received_total"])
    expected = 8.0 * 60.0

    ev = {"bto_id": bto_id, "subtotal": sub, "grand_total": grand,
          "received_total": received_total, "expected": expected}
    record_result(
        scenario="br_bt_var.3_invoice_grand_total_matches_received_total",
        step="grand_total_equals_received_total",
        expected={"grand": expected}, actual={"grand": grand}, evidence=ev,
    )
    record_result(
        scenario="br_bt_var.3_invoice_grand_total_matches_received_total",
        step="subtotal_equals_received_total",
        expected={"sub": expected}, actual={"sub": sub}, evidence=ev,
    )
    assert sub == expected
    assert grand == expected
    assert received_total == expected


# ═════════════════════════════════════════════════════════════════════
# Test 4 — silent invoice creation failure logged + flagged
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_br_bt_var_4_silent_invoice_creation_failure_logged(
    tenant, record_result, monkeypatch
):
    org_id = tenant["org_id"]; main = tenant["branches"]["main"]
    b2 = tenant["branches"]["b2"]; owner = tenant["users"]["owner"]
    pid = await seed_product(org_id, main, name="br_bt_var4 P",
                              stock=20, cost=60, price=100)

    # Monkey-patch create_internal_invoice to raise — BTO creation must
    # still succeed but stamp the failure flags AND write audit_log row.
    from routes import branch_transfers as bt_mod
    from routes import internal_invoices as ii_mod
    async def boom(*a, **kw):
        raise RuntimeError("simulated invoice DB outage")
    monkeypatch.setattr(ii_mod, "create_internal_invoice", boom)

    draft = await create_transfer(
        _bto_payload(main, b2, pid, "br_bt_var4 P", qty=5),
        user=owner)
    after = await _bto(draft["id"])
    audit = await _audit_rows({
        "type": "internal_invoice_creation_failed",
        "entity_id": draft["id"],
    })

    ev = {"bto_id": draft["id"],
          "invoice_creation_failed": after.get("invoice_creation_failed"),
          "invoice_creation_error": after.get("invoice_creation_error"),
          "audit_count": len(audit)}
    record_result(
        scenario="br_bt_var.4_silent_invoice_creation_failure_logged",
        step="bto_flag_set",
        expected={"failed": True},
        actual={"failed": after.get("invoice_creation_failed")},
        evidence=ev,
    )
    record_result(
        scenario="br_bt_var.4_silent_invoice_creation_failure_logged",
        step="audit_log_row_written",
        expected={"count": 1}, actual={"count": len(audit)}, evidence=ev,
    )
    record_result(
        scenario="br_bt_var.4_silent_invoice_creation_failure_logged",
        step="bto_still_created",
        expected={"id_set": True}, actual={"id_set": bool(draft.get("id"))},
        evidence=ev,
    )
    assert after.get("invoice_creation_failed") is True
    assert "simulated invoice DB outage" in (after.get("invoice_creation_error") or "")
    assert len(audit) == 1


# ═════════════════════════════════════════════════════════════════════
# Test 5 — high-value variance accept REQUIRES PIN
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_br_bt_var_5_high_value_variance_requires_pin(
    tenant, extra_users, record_result
):
    owner = tenant["users"]["owner"]
    # Capital loss = (10 - 0) * 600 = 6000 → exceeds 5000 threshold.
    pid, bto_id = await _drive_variance(
        tenant, sent_qty=10, received_qty=0, unit_cost=600.0,
        name_tag="br_bt_var5")

    state, result = await _call(accept_receipt(
        bto_id, data={"action": "accept", "note": "no pin"},
        user=owner))
    after = await _bto(bto_id)

    ev = {"bto_id": bto_id, "state": state, "result": result,
          "status_after": after.get("status")}
    record_result(
        scenario="br_bt_var.5_high_value_variance_requires_pin",
        step="rejected_without_pin",
        expected={"state": "err"}, actual={"state": state}, evidence=ev,
    )
    record_result(
        scenario="br_bt_var.5_high_value_variance_requires_pin",
        step="status_unchanged",
        expected={"status": "received_pending"},
        actual={"status": after.get("status")}, evidence=ev,
    )
    assert state == "err"
    assert after.get("status") == "received_pending"


# ═════════════════════════════════════════════════════════════════════
# Test 6 — low-value variance accept SKIPS PIN
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_br_bt_var_6_low_value_variance_skips_pin(
    tenant, record_result
):
    owner = tenant["users"]["owner"]
    # Capital loss = (10 - 7) * 60 = 180 → well below threshold.
    pid, bto_id = await _drive_variance(
        tenant, sent_qty=10, received_qty=7, unit_cost=60.0,
        name_tag="br_bt_var6")

    state, _ = await _call(accept_receipt(
        bto_id, data={"action": "accept", "note": "low value"},
        user=owner))
    after = await _bto(bto_id)
    ev = {"bto_id": bto_id, "state": state, "status_after": after.get("status")}
    record_result(
        scenario="br_bt_var.6_low_value_variance_skips_pin",
        step="accepted_without_pin",
        expected={"state": "ok"}, actual={"state": state}, evidence=ev,
    )
    record_result(
        scenario="br_bt_var.6_low_value_variance_skips_pin",
        step="status_accepted",
        expected={"status": "received"},
        actual={"status": after.get("status")}, evidence=ev,
    )
    assert state == "ok"
    assert after.get("status") == "received"


# ═════════════════════════════════════════════════════════════════════
# Test 7 — wrong-branch PIN rejected for high-value variance
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_br_bt_var_7_wrong_branch_pin_rejected(
    tenant, extra_users, record_result
):
    owner = tenant["users"]["owner"]
    pid, bto_id = await _drive_variance(
        tenant, sent_qty=10, received_qty=0, unit_cost=600.0,
        name_tag="br_bt_var7")

    # Destination-branch manager's PIN attempts to accept — branch
    # mismatch (PIN belongs to b2, supply branch is main).
    state, result = await _call(accept_receipt(
        bto_id,
        data={"action": "accept", "note": "wrong pin",
              "pin": extra_users["dst_pin"]},
        user=owner))
    after = await _bto(bto_id)

    ev = {"bto_id": bto_id, "state": state, "result": result,
          "status_after": after.get("status")}
    record_result(
        scenario="br_bt_var.7_wrong_branch_pin_rejected",
        step="403_rejected",
        expected={"state": "err"}, actual={"state": state}, evidence=ev,
    )
    record_result(
        scenario="br_bt_var.7_wrong_branch_pin_rejected",
        step="status_unchanged",
        expected={"status": "received_pending"},
        actual={"status": after.get("status")}, evidence=ev,
    )
    assert state == "err"
    assert after.get("status") == "received_pending"


# ═════════════════════════════════════════════════════════════════════
# Test 8 — admin PIN accepts high-value variance
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_br_bt_var_8_admin_pin_accepts_high_variance(
    tenant, extra_users, record_result
):
    owner = tenant["users"]["owner"]
    pid, bto_id = await _drive_variance(
        tenant, sent_qty=10, received_qty=0, unit_cost=600.0,
        name_tag="br_bt_var8")

    state, _ = await _call(accept_receipt(
        bto_id,
        data={"action": "accept", "note": "admin override",
              "pin": extra_users["admin_pin"]},
        user=owner))
    after = await _bto(bto_id)
    ev = {"bto_id": bto_id, "state": state,
          "status_after": after.get("status"),
          "pin_verified_by_id": after.get("variance_pin_verified_by_id"),
          "pin_verified_method": after.get("variance_pin_verified_method")}
    record_result(
        scenario="br_bt_var.8_admin_pin_accepts_high_variance",
        step="200_succeeded",
        expected={"state": "ok"}, actual={"state": state}, evidence=ev,
    )
    record_result(
        scenario="br_bt_var.8_admin_pin_accepts_high_variance",
        step="verifier_method_recorded",
        expected={"method_in_set": True},
        actual={"method_in_set":
                after.get("variance_pin_verified_method") in ("admin_pin", "manager_pin", "totp")},
        evidence=ev,
    )
    assert state == "ok"
    assert after.get("status") == "received"


# ═════════════════════════════════════════════════════════════════════
# Test 9 — incident ticket carries requested/approved/sent/received
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_br_bt_var_9_incident_ticket_full_qty_chain(
    tenant, extra_users, record_result
):
    org_id = tenant["org_id"]; main = tenant["branches"]["main"]
    b2 = tenant["branches"]["b2"]; owner = tenant["users"]["owner"]

    # Build a PO request first so we have requested + approved qty.
    pid = await seed_product(org_id, main, name="br_bt_var9 P",
                              stock=50, cost=60, price=100)
    po = await create_purchase_order({
        "po_type": "branch_request",
        "branch_id": b2, "supply_branch_id": main,
        "vendor": "Internal — Branch Stock Request",
        "items": [{"product_id": pid, "product_name": "br_bt_var9 P",
                    "unit": "pc", "quantity": 12, "unit_price": 60.0}],
        "show_retail": True, "notes": "br_bt_var9",
    }, user=owner)
    # No confirm-request; approved_qty stays None.
    # Now create BTO with sent_qty=10, link to the PO.
    draft = await create_transfer(_bto_payload(
        main, b2, pid, "br_bt_var9 P", qty=10,
        request_po_id=po["id"],
        request_po_number=po.get("po_number", "")), user=owner)
    await send_transfer(draft["id"], user=owner)
    await receive_transfer(
        draft["id"],
        data={"skip_receipt_check": True,
              "items": [{"product_id": pid, "qty_received": 6}]},
        user=owner)
    await accept_receipt(draft["id"],
        data={"action": "accept_with_incident",
              "note": "br_bt_var9 incident"},
        user=owner)

    ticket = await _raw_db.incident_tickets.find_one(
        {"transfer_id": draft["id"]}, {"_id": 0})
    line = (ticket or {}).get("items", [{}])[0]

    ev = {"bto_id": draft["id"], "ticket": ticket}
    record_result(
        scenario="br_bt_var.9_incident_ticket_full_qty_chain",
        step="requested_qty_present",
        expected={"requested": 12.0},
        actual={"requested": float(line.get("requested_qty") or 0)},
        evidence=ev,
    )
    record_result(
        scenario="br_bt_var.9_incident_ticket_full_qty_chain",
        step="approved_qty_present_or_null",
        expected={"approved_in_line": True},
        actual={"approved_in_line": "approved_qty" in line},
        evidence=ev,
    )
    record_result(
        scenario="br_bt_var.9_incident_ticket_full_qty_chain",
        step="sent_and_received_present",
        expected={"sent": 10.0, "received": 6.0},
        actual={"sent": float(line.get("sent_qty") or 0),
                "received": float(line.get("received_qty") or 0)},
        evidence=ev,
    )
    assert ticket is not None
    assert float(line["requested_qty"]) == 12.0
    assert "approved_qty" in line   # may be None when not confirmed
    assert float(line["sent_qty"]) == 10.0
    assert float(line["received_qty"]) == 6.0


# ═════════════════════════════════════════════════════════════════════
# Test 10 — incident ticket links to PO + invoice
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_br_bt_var_10_incident_ticket_links_po_and_invoice(
    tenant, record_result
):
    org_id = tenant["org_id"]; main = tenant["branches"]["main"]
    b2 = tenant["branches"]["b2"]; owner = tenant["users"]["owner"]
    pid = await seed_product(org_id, main, name="br_bt_var10 P",
                              stock=40, cost=60, price=100)
    po = await create_purchase_order({
        "po_type": "branch_request",
        "branch_id": b2, "supply_branch_id": main,
        "vendor": "Internal — Branch Stock Request",
        "items": [{"product_id": pid, "product_name": "br_bt_var10 P",
                    "unit": "pc", "quantity": 10, "unit_price": 60.0}],
        "show_retail": True, "notes": "br_bt_var10",
    }, user=owner)
    draft = await create_transfer(_bto_payload(
        main, b2, pid, "br_bt_var10 P", qty=10,
        request_po_id=po["id"],
        request_po_number=po.get("po_number", "")), user=owner)
    invoice_number = draft["invoice_number"]
    await send_transfer(draft["id"], user=owner)
    await receive_transfer(
        draft["id"],
        data={"skip_receipt_check": True,
              "items": [{"product_id": pid, "qty_received": 7}]},
        user=owner)
    await accept_receipt(draft["id"],
        data={"action": "accept_with_incident", "note": "br_bt_var10"},
        user=owner)

    ticket = await _raw_db.incident_tickets.find_one(
        {"transfer_id": draft["id"]}, {"_id": 0})
    ev = {"bto_id": draft["id"], "ticket": ticket}
    record_result(
        scenario="br_bt_var.10_incident_ticket_links_po_and_invoice",
        step="request_po_id_set",
        expected={"po_id": po["id"]},
        actual={"po_id": (ticket or {}).get("request_po_id")}, evidence=ev,
    )
    record_result(
        scenario="br_bt_var.10_incident_ticket_links_po_and_invoice",
        step="invoice_number_set",
        expected={"inv": invoice_number},
        actual={"inv": (ticket or {}).get("invoice_number")}, evidence=ev,
    )
    assert ticket.get("request_po_id") == po["id"]
    assert ticket.get("invoice_number") == invoice_number


# ═════════════════════════════════════════════════════════════════════
# Test 11 — exact receive leaves invoice line items unchanged
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_br_bt_var_11_exact_receive_invoice_unchanged(
    tenant, record_result
):
    org_id = tenant["org_id"]; main = tenant["branches"]["main"]
    b2 = tenant["branches"]["b2"]; owner = tenant["users"]["owner"]
    pid = await seed_product(org_id, main, name="br_bt_var11 P",
                              stock=30, cost=60, price=100)
    draft = await create_transfer(
        _bto_payload(main, b2, pid, "br_bt_var11 P", qty=10),
        user=owner)
    inv_before = await _invoice_for_transfer(draft["id"])
    qty_before = float(inv_before["items"][0]["qty"])
    total_before = float(inv_before["grand_total"])
    await send_transfer(draft["id"], user=owner)
    # Exact receive — no variance.
    await receive_transfer(
        draft["id"],
        data={"skip_receipt_check": True,
              "items": [{"product_id": pid, "qty_received": 10}]},
        user=owner)

    inv_after = await _invoice_for_transfer(draft["id"])
    qty_after = float(inv_after["items"][0]["qty"])
    total_after = float(inv_after["grand_total"])

    ev = {"bto_id": draft["id"], "qty_before": qty_before,
          "qty_after": qty_after, "total_before": total_before,
          "total_after": total_after,
          "items_have_original_sent_qty": "original_sent_qty" in inv_after["items"][0]}
    record_result(
        scenario="br_bt_var.11_exact_receive_invoice_unchanged",
        step="line_qty_unchanged",
        expected={"qty": qty_before}, actual={"qty": qty_after}, evidence=ev,
    )
    record_result(
        scenario="br_bt_var.11_exact_receive_invoice_unchanged",
        step="grand_total_unchanged",
        expected={"total": total_before},
        actual={"total": total_after}, evidence=ev,
    )
    assert qty_after == qty_before
    assert total_after == total_before


# ═════════════════════════════════════════════════════════════════════
# Test 12 — PIN verifier stamped on audit_log + BTO
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_br_bt_var_12_pin_verifier_metadata_on_audit_log(
    tenant, extra_users, record_result
):
    owner = tenant["users"]["owner"]
    pid, bto_id = await _drive_variance(
        tenant, sent_qty=10, received_qty=0, unit_cost=600.0,
        name_tag="br_bt_var12")

    await accept_receipt(bto_id, data={
        "action": "accept", "note": "audit",
        "pin": extra_users["admin_pin"],
    }, user=owner)
    after = await _bto(bto_id)
    audit = await _audit_rows({"type": "transfer_variance_accepted",
                                "entity_id": bto_id})

    metadata = (audit[0] if audit else {}).get("metadata", {})
    ev = {"bto_id": bto_id,
          "audit_count": len(audit),
          "pin_verified_by_name": metadata.get("pin_verified_by_name"),
          "pin_verified_method": metadata.get("pin_verified_method"),
          "bto_pin_verifier_id": after.get("variance_pin_verified_by_id"),
          "bto_pin_method": after.get("variance_pin_verified_method")}
    record_result(
        scenario="br_bt_var.12_pin_verifier_metadata_on_audit_log",
        step="audit_metadata_contains_method",
        expected={"method_set": True},
        actual={"method_set": bool(metadata.get("pin_verified_method"))},
        evidence=ev,
    )
    record_result(
        scenario="br_bt_var.12_pin_verifier_metadata_on_audit_log",
        step="bto_carries_verifier_method",
        expected={"method_set": True},
        actual={"method_set": bool(after.get("variance_pin_verified_method"))},
        evidence=ev,
    )
    assert metadata.get("pin_verified_method") in ("admin_pin", "manager_pin", "totp")
    assert after.get("variance_pin_verified_method") in ("admin_pin", "manager_pin", "totp")

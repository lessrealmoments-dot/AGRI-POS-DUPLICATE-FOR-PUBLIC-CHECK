"""
br_bt_perm — Branch Transfer permission, branch-context, and PIN-identity
regression tests (Phase 0.5).

Purpose
-------
Lock down the bugs surfaced by the Phase 0.5 audit BEFORE we ship the
approved_qty / QR confirmation work:

  1. `generate-branch-transfer` flips PO → `in_progress` even when no BTO
     is created, leaving the PO un-cancellable on the requester side.
  2. The same endpoint has no branch enforcement, so a Branch A manager
     can "fulfill" their own outgoing request.
  3. `send_transfer`, `cancel_transfer` accept any admin/manager
     regardless of `from_branch_id`.
  4. `approve_pending_transfer` passes `branch_id=None` to
     `verify_pin_for_action`, allowing a Branch A manager's PIN to
     approve a Branch B → Branch A pending BTO.

Some tests in this file deliberately FAIL on the un-fixed baseline so
that the surgical guards become a verified red→green transition.

Zero-footprint policy
---------------------
Re-uses the module-scoped `tenant` fixture from conftest.py. Seeds extra
in-tenant users (Branch B manager + approvers) at module scope so they
share the same teardown.
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
    create_transfer, send_transfer, cancel_transfer,
    approve_pending_transfer,
)
from routes.purchase_orders import (                             # noqa: E402
    create_purchase_order, generate_branch_transfer_from_request,
    cancel_purchase_order,
)


# ─────────────────────────────────────────────────────────────────────
# Module-scoped extra users with deterministic PINs and permissions.
# ─────────────────────────────────────────────────────────────────────
APPROVE_PERMS = {
    "purchase_orders": {"create": True, "read": True, "update": True, "delete": True},
    "branch_transfers": {"approve": True, "create": True, "update": True, "delete": True, "read": True},
}


@pytest_asyncio.fixture(scope="module")
async def extra_users(tenant):
    """Seed two managers (one per branch) with manager_pin + the
    `branch_transfers.approve` permission. Each PIN is unique and
    deterministic enough for assertions.
    """
    org_id = tenant["org_id"]
    main = tenant["branches"]["main"]
    b2 = tenant["branches"]["b2"]

    a_mgr_id = _uid("br_bt_perm-Amgr")
    b_mgr_id = _uid("br_bt_perm-Bmgr")
    a_pin = "411411"
    b_pin = "522522"
    await _raw_db.users.insert_many([
        {
            "id": a_mgr_id, "username": f"a-mgr-{a_mgr_id[-4:]}",
            "full_name": "A-Branch Manager", "organization_id": org_id,
            "role": "manager", "active": True,
            "branch_ids": [main], "branch_id": main,
            "manager_pin": a_pin,
            "permissions": APPROVE_PERMS,
        },
        {
            "id": b_mgr_id, "username": f"b-mgr-{b_mgr_id[-4:]}",
            "full_name": "B-Branch Manager", "organization_id": org_id,
            "role": "manager", "active": True,
            "branch_ids": [b2], "branch_id": b2,
            "manager_pin": b_pin,
            "permissions": APPROVE_PERMS,
        },
    ])
    yield {
        "a_mgr_user": fake_user(org_id, a_mgr_id, branch_id=main,
                                role="manager", perms=APPROVE_PERMS),
        "b_mgr_user": fake_user(org_id, b_mgr_id, branch_id=b2,
                                role="manager", perms=APPROVE_PERMS),
        "a_pin": a_pin,
        "b_pin": b_pin,
    }
    # Cleanup handled by module-scoped tenant teardown.


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────
async def _po(po_id):
    return await _raw_db.purchase_orders.find_one({"id": po_id}, {"_id": 0}) or {}


async def _bto(t_id):
    return await _raw_db.branch_transfer_orders.find_one({"id": t_id}, {"_id": 0}) or {}


async def _bto_count_for_po(po_id):
    return await _raw_db.branch_transfer_orders.count_documents(
        {"request_po_id": po_id, "status": {"$ne": "cancelled"}}
    )


def _request_payload(requesting_branch_id, supply_branch_id,
                     product_id, name, qty=20):
    return {
        "po_type": "branch_request",
        "branch_id": requesting_branch_id,
        "supply_branch_id": supply_branch_id,
        "vendor": "Internal — Branch Stock Request",
        "items": [{
            "product_id": product_id, "product_name": name,
            "unit": "pc", "quantity": qty, "unit_price": 60.0,
        }],
        "show_retail": True,
        "notes": "br_bt_perm regression",
    }


def _bto_payload(from_b, to_b, product_id, name, *, qty=10,
                 request_po_id="", request_po_number=""):
    return {
        "from_branch_id": from_b, "to_branch_id": to_b,
        "items": [{
            "product_id": product_id, "product_name": name,
            "sku": f"SKU-{product_id[-6:]}", "unit": "pc",
            "qty": qty, "branch_capital": 60.0,
            "transfer_capital": 60.0, "branch_retail": 100.0,
        }],
        "min_margin": 20,
        "request_po_id": request_po_id,
        "request_po_number": request_po_number,
        "notes": "br_bt_perm regression",
    }


async def _call_and_capture(coro):
    """Run a coroutine and capture HTTPException as (status_code, detail)."""
    try:
        result = await coro
        return ("ok", result)
    except HTTPException as e:
        return ("err", (e.status_code, e.detail))


# ═════════════════════════════════════════════════════════════════════
# LIFECYCLE TESTS (1–6)
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_br_bt_perm_1_generate_transfer_does_not_create_bto(
    tenant, record_result
):
    """Generate Transfer is a prefill — never creates a BTO row."""
    org_id = tenant["org_id"]; main = tenant["branches"]["main"]
    b2 = tenant["branches"]["b2"]; owner = tenant["users"]["owner"]

    product_id = await seed_product(org_id, b2, name="br_bt_perm1 P", stock=30, cost=60, price=100)
    po = await create_purchase_order(
        _request_payload(main, b2, product_id, "br_bt_perm1 P"), user=owner)
    btos_before = await _bto_count_for_po(po["id"])

    await generate_branch_transfer_from_request(po["id"], user=owner)
    btos_after = await _bto_count_for_po(po["id"])

    ev = {"po_id": po["id"], "po_number": po.get("po_number"),
          "btos_before": btos_before, "btos_after": btos_after}
    record_result(
        scenario="br_bt_perm.1_generate_transfer_does_not_create_bto",
        step="bto_count_unchanged",
        expected={"bto_count": 0},
        actual={"bto_count": btos_after},
        evidence=ev,
    )
    assert btos_after == 0


@pytest.mark.asyncio
async def test_br_bt_perm_2_generate_transfer_does_not_mark_in_progress(
    tenant, record_result
):
    """Post-fix: opening the composer does NOT mutate PO status. The
    status flip belongs to `create_transfer`, not `generate-…` ."""
    org_id = tenant["org_id"]; main = tenant["branches"]["main"]
    b2 = tenant["branches"]["b2"]; owner = tenant["users"]["owner"]

    product_id = await seed_product(org_id, b2, name="br_bt_perm2 P", stock=30, cost=60, price=100)
    po = await create_purchase_order(
        _request_payload(main, b2, product_id, "br_bt_perm2 P"), user=owner)
    status_before = (await _po(po["id"])).get("status")
    started_before = (await _po(po["id"])).get("fulfillment_started_at")

    await generate_branch_transfer_from_request(po["id"], user=owner)
    after = await _po(po["id"])

    ev = {"po_id": po["id"], "status_before": status_before,
          "status_after": after.get("status"),
          "fulfillment_started_at_before": started_before,
          "fulfillment_started_at_after": after.get("fulfillment_started_at")}
    record_result(
        scenario="br_bt_perm.2_generate_transfer_does_not_mark_in_progress",
        step="po_status_remains_requested",
        expected={"status": "requested"},
        actual={"status": after.get("status")},
        evidence=ev,
    )
    record_result(
        scenario="br_bt_perm.2_generate_transfer_does_not_mark_in_progress",
        step="fulfillment_started_not_stamped_yet",
        expected={"stamped": False},
        actual={"stamped": bool(after.get("fulfillment_started_at"))},
        evidence=ev,
    )
    assert after.get("status") == "requested", (
        "br_bt_perm.2: generate-branch-transfer must NOT flip PO to "
        f"in_progress (got {after.get('status')!r})"
    )
    assert not after.get("fulfillment_started_at")


@pytest.mark.asyncio
async def test_br_bt_perm_3_cancel_request_after_generate_succeeds_when_no_bto(
    tenant, record_result
):
    """Owner-facing dead-end fix: after Generate Transfer (no BTO), the
    requesting branch can still cancel the request."""
    org_id = tenant["org_id"]; main = tenant["branches"]["main"]
    b2 = tenant["branches"]["b2"]; owner = tenant["users"]["owner"]
    manager_pin = tenant["users"]["manager"]["id"]  # not actually a pin; we use the dedicated admin pin via owner role bypass

    product_id = await seed_product(org_id, b2, name="br_bt_perm3 P", stock=30, cost=60, price=100)
    po = await create_purchase_order(
        _request_payload(main, b2, product_id, "br_bt_perm3 P"), user=owner)
    await generate_branch_transfer_from_request(po["id"], user=owner)

    # Need to seed an admin_pin policy row + admin PIN so cancel_po PIN passes.
    # The verify_pin_for_action helper accepts either admin_pin (system_settings)
    # or a manager_pin tied to a user. We'll use the existing tenant manager's PIN.
    mgr_pin = tenant["users"]["manager"].get("manager_pin")
    if not mgr_pin:
        # Read the seeded user row to get the auto-generated manager_pin.
        u = await _raw_db.users.find_one({"id": tenant["users"]["manager"]["id"]},
                                          {"_id": 0, "manager_pin": 1})
        mgr_pin = (u or {}).get("manager_pin")

    state, result = await _call_and_capture(
        cancel_purchase_order(po["id"], data={"pin": mgr_pin}, user=owner)
    )
    after = await _po(po["id"])
    btos = await _bto_count_for_po(po["id"])

    ev = {"po_id": po["id"], "result": result, "state": state,
          "status_after": after.get("status"), "bto_count": btos,
          "mgr_pin_used": bool(mgr_pin)}
    record_result(
        scenario="br_bt_perm.3_cancel_request_after_generate_succeeds_when_no_bto",
        step="cancel_succeeds",
        expected={"state": "ok"},
        actual={"state": state},
        evidence=ev,
    )
    record_result(
        scenario="br_bt_perm.3_cancel_request_after_generate_succeeds_when_no_bto",
        step="po_status_is_cancelled",
        expected={"status": "cancelled"},
        actual={"status": after.get("status")},
        evidence=ev,
    )
    assert state == "ok", (
        f"br_bt_perm.3: cancel should succeed when no BTO exists "
        f"(got {state}={result})"
    )
    assert after.get("status") == "cancelled"
    assert btos == 0


@pytest.mark.asyncio
async def test_br_bt_perm_4_create_bto_marks_request_in_progress(
    tenant, record_result
):
    """Creating a BTO linked to a request flips the PO → in_progress and
    stamps fulfillment_started_at/by. (Post-fix behaviour.)"""
    org_id = tenant["org_id"]; main = tenant["branches"]["main"]
    b2 = tenant["branches"]["b2"]; owner = tenant["users"]["owner"]

    product_id = await seed_product(org_id, b2, name="br_bt_perm4 P", stock=30, cost=60, price=100)
    po = await create_purchase_order(
        _request_payload(main, b2, product_id, "br_bt_perm4 P"), user=owner)
    await generate_branch_transfer_from_request(po["id"], user=owner)

    bto = await create_transfer(
        _bto_payload(b2, main, product_id, "br_bt_perm4 P", qty=10,
                     request_po_id=po["id"], request_po_number=po.get("po_number", "")),
        user=owner,
    )
    after = await _po(po["id"])
    bto_row = await _bto(bto["id"])

    ev = {"po_id": po["id"], "bto_id": bto["id"],
          "bto_number": bto_row.get("order_number"),
          "po_status_after_bto": after.get("status"),
          "fulfillment_started_at": after.get("fulfillment_started_at"),
          "fulfillment_started_by": after.get("fulfillment_started_by")}
    record_result(
        scenario="br_bt_perm.4_create_bto_marks_request_in_progress",
        step="po_status_in_progress_only_after_bto",
        expected={"status": "in_progress"},
        actual={"status": after.get("status")},
        evidence=ev,
    )
    record_result(
        scenario="br_bt_perm.4_create_bto_marks_request_in_progress",
        step="fulfillment_started_at_stamped",
        expected={"stamped": True},
        actual={"stamped": bool(after.get("fulfillment_started_at"))},
        evidence=ev,
    )
    record_result(
        scenario="br_bt_perm.4_create_bto_marks_request_in_progress",
        step="fulfillment_started_by_stamped",
        expected={"stamped": True},
        actual={"stamped": bool(after.get("fulfillment_started_by"))},
        evidence=ev,
    )
    assert after.get("status") == "in_progress"
    assert after.get("fulfillment_started_at")
    assert after.get("fulfillment_started_by")


@pytest.mark.asyncio
async def test_br_bt_perm_5_cancel_in_progress_rejected_when_linked_bto_exists(
    tenant, record_result
):
    """If a non-cancelled linked BTO exists, request cancel returns 400
    with an actionable message pointing at the BTO."""
    org_id = tenant["org_id"]; main = tenant["branches"]["main"]
    b2 = tenant["branches"]["b2"]; owner = tenant["users"]["owner"]

    product_id = await seed_product(org_id, b2, name="br_bt_perm5 P", stock=30, cost=60, price=100)
    po = await create_purchase_order(
        _request_payload(main, b2, product_id, "br_bt_perm5 P"), user=owner)
    await generate_branch_transfer_from_request(po["id"], user=owner)
    bto = await create_transfer(
        _bto_payload(b2, main, product_id, "br_bt_perm5 P", qty=10,
                     request_po_id=po["id"], request_po_number=po.get("po_number", "")),
        user=owner,
    )

    u = await _raw_db.users.find_one({"id": tenant["users"]["manager"]["id"]},
                                      {"_id": 0, "manager_pin": 1})
    mgr_pin = (u or {}).get("manager_pin")
    state, result = await _call_and_capture(
        cancel_purchase_order(po["id"], data={"pin": mgr_pin}, user=owner)
    )
    po_after = await _po(po["id"])
    bto_after = await _bto(bto["id"])

    detail_str = ""
    if state == "err" and isinstance(result, tuple):
        detail_str = str(result[1]) if result[1] is not None else ""

    ev = {"po_id": po["id"], "bto_id": bto["id"],
          "bto_number": bto_after.get("order_number"),
          "po_status_after": po_after.get("status"),
          "bto_status_after": bto_after.get("status"),
          "rejection_state": state, "rejection_result": result,
          "rejection_detail_str": detail_str}
    record_result(
        scenario="br_bt_perm.5_cancel_in_progress_rejected_when_linked_bto_exists",
        step="cancel_rejected_400",
        expected={"state": "err", "status_code": 400},
        actual={"state": state,
                "status_code": result[0] if state == "err" else None},
        evidence=ev,
    )
    record_result(
        scenario="br_bt_perm.5_cancel_in_progress_rejected_when_linked_bto_exists",
        step="error_detail_mentions_linked_bto",
        expected={"mentions_bto": True},
        actual={"mentions_bto": (
            "BTO" in detail_str or "transfer draft" in detail_str.lower()
        )},
        evidence=ev,
    )
    record_result(
        scenario="br_bt_perm.5_cancel_in_progress_rejected_when_linked_bto_exists",
        step="po_unchanged",
        expected={"status": "in_progress"},
        actual={"status": po_after.get("status")},
        evidence=ev,
    )
    record_result(
        scenario="br_bt_perm.5_cancel_in_progress_rejected_when_linked_bto_exists",
        step="bto_unchanged",
        expected={"status": "draft"},
        actual={"status": bto_after.get("status")},
        evidence=ev,
    )
    assert state == "err" and result[0] == 400
    assert "BTO" in detail_str or "transfer draft" in detail_str.lower()
    assert po_after.get("status") == "in_progress"
    assert bto_after.get("status") == "draft"


@pytest.mark.asyncio
async def test_br_bt_perm_6_cancel_linked_bto_then_request_can_cancel(
    tenant, record_result
):
    """After the linked BTO is cancelled, the request can be cancelled
    normally. (Post-fix: PO stays in_progress until cancelled because
    fulfillment did start.)"""
    org_id = tenant["org_id"]; main = tenant["branches"]["main"]
    b2 = tenant["branches"]["b2"]; owner = tenant["users"]["owner"]

    product_id = await seed_product(org_id, b2, name="br_bt_perm6 P", stock=30, cost=60, price=100)
    po = await create_purchase_order(
        _request_payload(main, b2, product_id, "br_bt_perm6 P"), user=owner)
    await generate_branch_transfer_from_request(po["id"], user=owner)
    bto = await create_transfer(
        _bto_payload(b2, main, product_id, "br_bt_perm6 P", qty=10,
                     request_po_id=po["id"], request_po_number=po.get("po_number", "")),
        user=owner,
    )
    # Cancel the linked BTO first.
    await cancel_transfer(bto["id"], user=owner)
    bto_after_cancel = await _bto(bto["id"])

    u = await _raw_db.users.find_one({"id": tenant["users"]["manager"]["id"]},
                                      {"_id": 0, "manager_pin": 1})
    mgr_pin = (u or {}).get("manager_pin")
    state, result = await _call_and_capture(
        cancel_purchase_order(po["id"], data={"pin": mgr_pin}, user=owner)
    )
    po_after = await _po(po["id"])

    ev = {"po_id": po["id"], "bto_id": bto["id"],
          "bto_status_after_cancel": bto_after_cancel.get("status"),
          "po_status_after_cancel": po_after.get("status"),
          "cancel_state": state, "cancel_result": result}
    record_result(
        scenario="br_bt_perm.6_cancel_linked_bto_then_request_can_cancel",
        step="bto_cancelled_first",
        expected={"status": "cancelled"},
        actual={"status": bto_after_cancel.get("status")},
        evidence=ev,
    )
    record_result(
        scenario="br_bt_perm.6_cancel_linked_bto_then_request_can_cancel",
        step="po_cancel_succeeds_after_bto_cancelled",
        expected={"state": "ok"},
        actual={"state": state},
        evidence=ev,
    )
    record_result(
        scenario="br_bt_perm.6_cancel_linked_bto_then_request_can_cancel",
        step="po_status_cancelled",
        expected={"status": "cancelled"},
        actual={"status": po_after.get("status")},
        evidence=ev,
    )
    assert bto_after_cancel.get("status") == "cancelled"
    assert state == "ok", f"br_bt_perm.6: expected cancel ok, got {state}={result}"
    assert po_after.get("status") == "cancelled"


# ═════════════════════════════════════════════════════════════════════
# BRANCH-CONTEXT TESTS (7–13)
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_br_bt_perm_7_requester_manager_cannot_generate_own_request(
    tenant, extra_users, record_result
):
    """A manager in the REQUESTING branch (Branch A = main) cannot call
    generate-branch-transfer on their own outgoing request. The supply
    branch is Branch B (b2)."""
    org_id = tenant["org_id"]; main = tenant["branches"]["main"]
    b2 = tenant["branches"]["b2"]; owner = tenant["users"]["owner"]
    a_mgr = extra_users["a_mgr_user"]   # branch=main (requester)

    product_id = await seed_product(org_id, b2, name="br_bt_perm7 P", stock=30, cost=60, price=100)
    po = await create_purchase_order(
        _request_payload(main, b2, product_id, "br_bt_perm7 P"), user=owner)
    state, result = await _call_and_capture(
        generate_branch_transfer_from_request(po["id"], user=a_mgr)
    )

    ev = {"po_id": po["id"], "supply_branch_id": b2,
          "user_branch_id": main, "state": state, "result": result}
    record_result(
        scenario="br_bt_perm.7_requester_manager_cannot_generate_own_request",
        step="non_supply_manager_rejected",
        expected={"state": "err", "status_code": 403},
        actual={"state": state,
                "status_code": result[0] if state == "err" else None},
        evidence=ev,
    )
    assert state == "err" and result[0] == 403, (
        "br_bt_perm.7: requester-branch manager must be 403 on "
        f"generate-branch-transfer (got {state}={result})"
    )


@pytest.mark.asyncio
async def test_br_bt_perm_8_supplier_manager_can_generate_incoming(
    tenant, extra_users, record_result
):
    """A manager in the SUPPLYING branch (Branch B) succeeds on
    generate-branch-transfer."""
    org_id = tenant["org_id"]; main = tenant["branches"]["main"]
    b2 = tenant["branches"]["b2"]; owner = tenant["users"]["owner"]
    b_mgr = extra_users["b_mgr_user"]   # branch=b2 (supplier)

    product_id = await seed_product(org_id, b2, name="br_bt_perm8 P", stock=30, cost=60, price=100)
    po = await create_purchase_order(
        _request_payload(main, b2, product_id, "br_bt_perm8 P"), user=owner)
    state, result = await _call_and_capture(
        generate_branch_transfer_from_request(po["id"], user=b_mgr)
    )

    ev = {"po_id": po["id"], "state": state,
          "items_returned": len((result or {}).get("items", [])) if state == "ok" else 0}
    record_result(
        scenario="br_bt_perm.8_supplier_manager_can_generate_incoming",
        step="supply_manager_accepted",
        expected={"state": "ok"},
        actual={"state": state},
        evidence=ev,
    )
    assert state == "ok", (
        f"br_bt_perm.8: supply-branch manager must succeed "
        f"(got {state}={result})"
    )


@pytest.mark.asyncio
async def test_br_bt_perm_9_admin_can_generate_from_any_branch_context(
    tenant, record_result
):
    """Admin/owner always succeeds, regardless of currentBranch context.
    (Branch enforcement intentionally bypasses privileged roles.)"""
    org_id = tenant["org_id"]; main = tenant["branches"]["main"]
    b2 = tenant["branches"]["b2"]; owner = tenant["users"]["owner"]

    product_id = await seed_product(org_id, b2, name="br_bt_perm9 P", stock=30, cost=60, price=100)
    po = await create_purchase_order(
        _request_payload(main, b2, product_id, "br_bt_perm9 P"), user=owner)
    state, result = await _call_and_capture(
        generate_branch_transfer_from_request(po["id"], user=owner)
    )

    ev = {"po_id": po["id"], "role": owner.get("role"),
          "user_branch_id": owner.get("branch_id"),
          "supply_branch_id": b2, "state": state}
    record_result(
        scenario="br_bt_perm.9_admin_can_generate_from_any_branch_context",
        step="admin_override_allowed",
        expected={"state": "ok"},
        actual={"state": state},
        evidence=ev,
    )
    assert state == "ok", (
        f"br_bt_perm.9: admin override must succeed (got {state}={result})"
    )


@pytest.mark.asyncio
async def test_br_bt_perm_10_wrong_branch_manager_cannot_send_bto(
    tenant, extra_users, record_result
):
    """A manager in the destination branch cannot send a BTO whose source
    is a different branch."""
    org_id = tenant["org_id"]; main = tenant["branches"]["main"]
    b2 = tenant["branches"]["b2"]; owner = tenant["users"]["owner"]
    a_mgr = extra_users["a_mgr_user"]   # branch=main; BTO source is b2

    product_id = await seed_product(org_id, b2, name="br_bt_perm10 P", stock=30, cost=60, price=100)
    bto = await create_transfer(
        _bto_payload(b2, main, product_id, "br_bt_perm10 P", qty=10),
        user=owner,
    )
    state, result = await _call_and_capture(send_transfer(bto["id"], user=a_mgr))
    after = await _bto(bto["id"])

    ev = {"bto_id": bto["id"], "from_branch_id": b2,
          "user_branch_id": main, "state": state, "result": result,
          "status_after": after.get("status")}
    record_result(
        scenario="br_bt_perm.10_wrong_branch_manager_cannot_send_bto",
        step="non_source_manager_rejected",
        expected={"state": "err", "status_code": 403},
        actual={"state": state,
                "status_code": result[0] if state == "err" else None},
        evidence=ev,
    )
    record_result(
        scenario="br_bt_perm.10_wrong_branch_manager_cannot_send_bto",
        step="bto_remains_draft",
        expected={"status": "draft"},
        actual={"status": after.get("status")},
        evidence=ev,
    )
    assert state == "err" and result[0] == 403
    assert after.get("status") == "draft"


@pytest.mark.asyncio
async def test_br_bt_perm_11_source_branch_manager_can_send_bto(
    tenant, extra_users, record_result
):
    """Source-branch manager can send their own BTO."""
    org_id = tenant["org_id"]; main = tenant["branches"]["main"]
    b2 = tenant["branches"]["b2"]; owner = tenant["users"]["owner"]
    b_mgr = extra_users["b_mgr_user"]   # branch=b2; BTO source=b2

    product_id = await seed_product(org_id, b2, name="br_bt_perm11 P", stock=30, cost=60, price=100)
    bto = await create_transfer(
        _bto_payload(b2, main, product_id, "br_bt_perm11 P", qty=10),
        user=owner,
    )
    state, result = await _call_and_capture(send_transfer(bto["id"], user=b_mgr))
    after = await _bto(bto["id"])

    ev = {"bto_id": bto["id"], "state": state,
          "status_after": after.get("status"), "result_keys": list((result or {}).keys()) if isinstance(result, dict) else None}
    record_result(
        scenario="br_bt_perm.11_source_branch_manager_can_send_bto",
        step="source_manager_accepted",
        expected={"state": "ok"},
        actual={"state": state},
        evidence=ev,
    )
    record_result(
        scenario="br_bt_perm.11_source_branch_manager_can_send_bto",
        step="bto_status_sent",
        expected={"status": "sent"},
        actual={"status": after.get("status")},
        evidence=ev,
    )
    assert state == "ok", f"br_bt_perm.11: got {state}={result}"
    assert after.get("status") == "sent"


@pytest.mark.asyncio
async def test_br_bt_perm_12_wrong_branch_manager_cannot_cancel_bto(
    tenant, extra_users, record_result
):
    """Non-source branch manager cannot cancel a draft BTO."""
    org_id = tenant["org_id"]; main = tenant["branches"]["main"]
    b2 = tenant["branches"]["b2"]; owner = tenant["users"]["owner"]
    a_mgr = extra_users["a_mgr_user"]   # branch=main; BTO source=b2

    product_id = await seed_product(org_id, b2, name="br_bt_perm12 P", stock=30, cost=60, price=100)
    bto = await create_transfer(
        _bto_payload(b2, main, product_id, "br_bt_perm12 P", qty=10),
        user=owner,
    )
    state, result = await _call_and_capture(cancel_transfer(bto["id"], user=a_mgr))
    after = await _bto(bto["id"])

    ev = {"bto_id": bto["id"], "user_branch": main, "from_branch": b2,
          "state": state, "result": result,
          "status_after": after.get("status")}
    record_result(
        scenario="br_bt_perm.12_wrong_branch_manager_cannot_cancel_bto",
        step="non_source_manager_cancel_rejected",
        expected={"state": "err", "status_code": 403},
        actual={"state": state,
                "status_code": result[0] if state == "err" else None},
        evidence=ev,
    )
    record_result(
        scenario="br_bt_perm.12_wrong_branch_manager_cannot_cancel_bto",
        step="bto_remains_draft",
        expected={"status": "draft"},
        actual={"status": after.get("status")},
        evidence=ev,
    )
    assert state == "err" and result[0] == 403
    assert after.get("status") == "draft"


@pytest.mark.asyncio
async def test_br_bt_perm_13_source_branch_manager_can_cancel_bto_draft(
    tenant, extra_users, record_result
):
    """Source-branch manager can cancel their own draft BTO."""
    org_id = tenant["org_id"]; main = tenant["branches"]["main"]
    b2 = tenant["branches"]["b2"]; owner = tenant["users"]["owner"]
    b_mgr = extra_users["b_mgr_user"]

    product_id = await seed_product(org_id, b2, name="br_bt_perm13 P", stock=30, cost=60, price=100)
    bto = await create_transfer(
        _bto_payload(b2, main, product_id, "br_bt_perm13 P", qty=10),
        user=owner,
    )
    state, result = await _call_and_capture(cancel_transfer(bto["id"], user=b_mgr))
    after = await _bto(bto["id"])

    ev = {"bto_id": bto["id"], "state": state,
          "status_after": after.get("status")}
    record_result(
        scenario="br_bt_perm.13_source_branch_manager_can_cancel_bto_draft",
        step="source_manager_cancel_accepted",
        expected={"state": "ok"},
        actual={"state": state},
        evidence=ev,
    )
    record_result(
        scenario="br_bt_perm.13_source_branch_manager_can_cancel_bto_draft",
        step="bto_status_cancelled",
        expected={"status": "cancelled"},
        actual={"status": after.get("status")},
        evidence=ev,
    )
    assert state == "ok"
    assert after.get("status") == "cancelled"


# ═════════════════════════════════════════════════════════════════════
# PIN / APPROVAL TESTS (14–18)
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_br_bt_perm_14_wrong_branch_pin_cannot_approve_pending_bto(
    tenant, extra_users, record_result
):
    """Branch A manager PIN must NOT approve a Branch B → Branch A
    pending BTO. (Fix: pass `branch_id=order.from_branch_id` to
    `verify_pin_for_action`.)"""
    org_id = tenant["org_id"]; main = tenant["branches"]["main"]
    b2 = tenant["branches"]["b2"]; owner = tenant["users"]["owner"]

    product_id = await seed_product(org_id, b2, name="br_bt_perm14 P", stock=30, cost=60, price=100)
    payload = _bto_payload(b2, main, product_id, "br_bt_perm14 P", qty=10)
    payload["requires_approval"] = True
    bto = await create_transfer(payload, user=owner)

    # Caller is Branch A manager (has branch_transfers.approve perm),
    # PIN is Branch A manager's PIN. BTO source = b2 (Branch B).
    a_mgr = extra_users["a_mgr_user"]
    a_pin = extra_users["a_pin"]
    state, result = await _call_and_capture(
        approve_pending_transfer(bto["id"], data={"pin": a_pin}, user=a_mgr)
    )
    after = await _bto(bto["id"])

    ev = {"bto_id": bto["id"], "from_branch": b2,
          "pin_user_branch": main, "state": state, "result": result,
          "status_after": after.get("status")}
    record_result(
        scenario="br_bt_perm.14_wrong_branch_pin_cannot_approve_pending_bto",
        step="cross_branch_pin_rejected",
        expected={"state": "err"},
        actual={"state": state},
        evidence=ev,
    )
    record_result(
        scenario="br_bt_perm.14_wrong_branch_pin_cannot_approve_pending_bto",
        step="bto_remains_pending_approval",
        expected={"status": "pending_approval"},
        actual={"status": after.get("status")},
        evidence=ev,
    )
    assert state == "err", (
        f"br_bt_perm.14: Branch A PIN must NOT approve Branch B's BTO "
        f"(got {state}={result})"
    )
    assert after.get("status") == "pending_approval"


@pytest.mark.asyncio
async def test_br_bt_perm_15_source_branch_pin_can_approve_pending_bto(
    tenant, extra_users, record_result
):
    """Source-branch manager's PIN successfully approves the BTO."""
    org_id = tenant["org_id"]; main = tenant["branches"]["main"]
    b2 = tenant["branches"]["b2"]; owner = tenant["users"]["owner"]

    product_id = await seed_product(org_id, b2, name="br_bt_perm15 P", stock=30, cost=60, price=100)
    payload = _bto_payload(b2, main, product_id, "br_bt_perm15 P", qty=10)
    payload["requires_approval"] = True
    bto = await create_transfer(payload, user=owner)

    b_mgr = extra_users["b_mgr_user"]
    b_pin = extra_users["b_pin"]
    state, result = await _call_and_capture(
        approve_pending_transfer(bto["id"], data={"pin": b_pin}, user=b_mgr)
    )
    after = await _bto(bto["id"])

    ev = {"bto_id": bto["id"], "state": state,
          "status_after": after.get("status"),
          "approved_by": after.get("approved_by"),
          "approved_pin_method": after.get("approved_pin_method")}
    record_result(
        scenario="br_bt_perm.15_source_branch_pin_can_approve_pending_bto",
        step="source_pin_accepted",
        expected={"state": "ok"},
        actual={"state": state},
        evidence=ev,
    )
    record_result(
        scenario="br_bt_perm.15_source_branch_pin_can_approve_pending_bto",
        step="bto_status_sent_after_approval",
        expected={"status": "sent"},
        actual={"status": after.get("status")},
        evidence=ev,
    )
    assert state == "ok", f"br_bt_perm.15: got {state}={result}"
    assert after.get("status") == "sent"


@pytest.mark.asyncio
async def test_br_bt_perm_16_admin_pin_can_approve_any_branch(
    tenant, extra_users, record_result
):
    """Admin PIN approves regardless of branch."""
    # We can't easily seed a system-wide `admin_pin` (lives in
    # system_settings), so we exercise the admin-role override path
    # instead: the calling user is the org admin, and the manager PIN
    # they submit IS the source-branch manager's PIN. The verifier role
    # check tolerates admin caller + valid PIN.
    org_id = tenant["org_id"]; main = tenant["branches"]["main"]
    b2 = tenant["branches"]["b2"]; owner = tenant["users"]["owner"]

    product_id = await seed_product(org_id, b2, name="br_bt_perm16 P", stock=30, cost=60, price=100)
    payload = _bto_payload(b2, main, product_id, "br_bt_perm16 P", qty=10)
    payload["requires_approval"] = True
    bto = await create_transfer(payload, user=owner)

    b_pin = extra_users["b_pin"]
    state, result = await _call_and_capture(
        approve_pending_transfer(bto["id"], data={"pin": b_pin}, user=owner)
    )
    after = await _bto(bto["id"])

    ev = {"bto_id": bto["id"], "caller_role": owner.get("role"),
          "state": state, "status_after": after.get("status"),
          "approved_by": after.get("approved_by")}
    record_result(
        scenario="br_bt_perm.16_admin_caller_with_source_pin_can_approve",
        step="admin_caller_accepted",
        expected={"state": "ok"},
        actual={"state": state},
        evidence=ev,
    )
    record_result(
        scenario="br_bt_perm.16_admin_caller_with_source_pin_can_approve",
        step="bto_status_sent",
        expected={"status": "sent"},
        actual={"status": after.get("status")},
        evidence=ev,
    )
    assert state == "ok", f"br_bt_perm.16: got {state}={result}"
    assert after.get("status") == "sent"


@pytest.mark.asyncio
async def test_br_bt_perm_17_verifier_identity_stamped_on_approval(
    tenant, extra_users, record_result
):
    """The approval writes verifier id/name/method onto the BTO doc."""
    org_id = tenant["org_id"]; main = tenant["branches"]["main"]
    b2 = tenant["branches"]["b2"]; owner = tenant["users"]["owner"]

    product_id = await seed_product(org_id, b2, name="br_bt_perm17 P", stock=30, cost=60, price=100)
    payload = _bto_payload(b2, main, product_id, "br_bt_perm17 P", qty=10)
    payload["requires_approval"] = True
    bto = await create_transfer(payload, user=owner)

    b_mgr = extra_users["b_mgr_user"]
    b_pin = extra_users["b_pin"]
    await approve_pending_transfer(bto["id"], data={"pin": b_pin}, user=b_mgr)
    after = await _bto(bto["id"])

    ev = {"bto_id": bto["id"], "approved_by": after.get("approved_by"),
          "approved_by_name": after.get("approved_by_name"),
          "approved_pin_method": after.get("approved_pin_method"),
          "approved_at": after.get("approved_at"),
          "approved_by_role": after.get("approved_by_role")}
    record_result(
        scenario="br_bt_perm.17_verifier_identity_stamped_on_approval",
        step="approved_by_id_stamped",
        expected={"stamped": True},
        actual={"stamped": bool(after.get("approved_by"))},
        evidence=ev,
    )
    record_result(
        scenario="br_bt_perm.17_verifier_identity_stamped_on_approval",
        step="approved_by_name_stamped",
        expected={"stamped": True},
        actual={"stamped": bool(after.get("approved_by_name"))},
        evidence=ev,
    )
    record_result(
        scenario="br_bt_perm.17_verifier_identity_stamped_on_approval",
        step="approved_pin_method_stamped",
        expected={"stamped": True},
        actual={"stamped": bool(after.get("approved_pin_method"))},
        evidence=ev,
    )
    record_result(
        scenario="br_bt_perm.17_verifier_identity_stamped_on_approval",
        step="approved_at_stamped",
        expected={"stamped": True},
        actual={"stamped": bool(after.get("approved_at"))},
        evidence=ev,
    )
    assert after.get("approved_by")
    assert after.get("approved_by_name")
    assert after.get("approved_pin_method") in ("manager_pin", "admin_pin", "totp")
    assert after.get("approved_at")


@pytest.mark.asyncio
async def test_br_bt_perm_18_cancel_po_stamps_verifier_identity(
    tenant, record_result
):
    """Cancelling a branch_request PO with a manager PIN should write
    verifier identity onto the cancelled PO for the audit trail.

    Today the route only flips `status=cancelled`; the verifier id is
    not stamped. After the fix, fields cancelled_by_id, cancelled_by_name,
    cancel_pin_method, cancelled_at should be present.
    """
    org_id = tenant["org_id"]; main = tenant["branches"]["main"]
    b2 = tenant["branches"]["b2"]; owner = tenant["users"]["owner"]

    product_id = await seed_product(org_id, b2, name="br_bt_perm18 P", stock=30, cost=60, price=100)
    po = await create_purchase_order(
        _request_payload(main, b2, product_id, "br_bt_perm18 P"), user=owner)

    u = await _raw_db.users.find_one({"id": tenant["users"]["manager"]["id"]},
                                      {"_id": 0, "manager_pin": 1})
    mgr_pin = (u or {}).get("manager_pin")
    await cancel_purchase_order(po["id"], data={"pin": mgr_pin}, user=owner)
    after = await _po(po["id"])

    ev = {"po_id": po["id"],
          "status_after": after.get("status"),
          "cancelled_by": after.get("cancelled_by_id"),
          "cancelled_by_name": after.get("cancelled_by_name"),
          "cancel_pin_method": after.get("cancel_pin_method"),
          "cancelled_at": after.get("cancelled_at")}
    record_result(
        scenario="br_bt_perm.18_cancel_po_stamps_verifier_identity",
        step="po_status_cancelled",
        expected={"status": "cancelled"},
        actual={"status": after.get("status")},
        evidence=ev,
    )
    record_result(
        scenario="br_bt_perm.18_cancel_po_stamps_verifier_identity",
        step="verifier_id_stamped",
        expected={"stamped": True},
        actual={"stamped": bool(after.get("cancelled_by_id"))},
        evidence=ev,
    )
    record_result(
        scenario="br_bt_perm.18_cancel_po_stamps_verifier_identity",
        step="cancel_pin_method_stamped",
        expected={"stamped": True},
        actual={"stamped": bool(after.get("cancel_pin_method"))},
        evidence=ev,
    )
    record_result(
        scenario="br_bt_perm.18_cancel_po_stamps_verifier_identity",
        step="cancelled_at_stamped",
        expected={"stamped": True},
        actual={"stamped": bool(after.get("cancelled_at"))},
        evidence=ev,
    )
    assert after.get("status") == "cancelled"
    assert after.get("cancelled_by_id")
    assert after.get("cancel_pin_method") in ("manager_pin", "admin_pin", "totp")
    assert after.get("cancelled_at")

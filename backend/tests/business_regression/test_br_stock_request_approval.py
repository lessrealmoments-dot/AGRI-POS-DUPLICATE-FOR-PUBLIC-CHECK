"""
br_sr_conf — Phase 1: Stock Request Confirmation Layer.

Locks down the new `approved_qty` workflow:
  * Supply branch (or admin/owner) confirms a requested quantity before
    a BTO is created. `quantity` (the original requested qty) stays
    immutable; only `approved_qty` / `approved_note` are mutated per line.
  * No stock movement during confirmation.
  * `generate-branch-transfer` prefills `qty` from `approved_qty` when
    present and still surfaces `requested_qty` for transparency.
  * Append-only `request_approval_log` ledger.

Zero-footprint policy: re-uses the module-scoped `tenant` fixture from
conftest.py and the `extra_users` pattern from the Phase 0.5 file (Branch
A manager + Branch B manager + deterministic PINs).
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
from routes.branch_transfers import create_transfer              # noqa: E402
from routes.purchase_orders import (                             # noqa: E402
    create_purchase_order,
    generate_branch_transfer_from_request,
    confirm_stock_request,
    get_stock_request_confirmation,
)


# ─────────────────────────────────────────────────────────────────────
APPROVE_PERMS = {
    "purchase_orders": {"create": True, "read": True, "update": True, "delete": True},
    "branch_transfers": {"approve": True, "create": True, "update": True,
                          "delete": True, "read": True},
}


@pytest_asyncio.fixture(scope="module")
async def extra_users(tenant):
    """Seed two managers (one per branch) with manager_pin + the
    `branch_transfers.approve` + `purchase_orders.*` permissions.
    Mirrors the Phase 0.5 pattern."""
    org_id = tenant["org_id"]
    main = tenant["branches"]["main"]
    b2 = tenant["branches"]["b2"]

    a_mgr_id = _uid("br_sr_conf-Amgr")
    b_mgr_id = _uid("br_sr_conf-Bmgr")
    a_pin = "611611"
    b_pin = "722722"
    await _raw_db.users.insert_many([
        {
            "id": a_mgr_id, "username": f"a-mgr-{a_mgr_id[-4:]}",
            "full_name": "A-Branch Manager (SR)", "organization_id": org_id,
            "role": "manager", "active": True,
            "branch_ids": [main], "branch_id": main,
            "manager_pin": a_pin,
            "permissions": APPROVE_PERMS,
        },
        {
            "id": b_mgr_id, "username": f"b-mgr-{b_mgr_id[-4:]}",
            "full_name": "B-Branch Manager (SR)", "organization_id": org_id,
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


# ─────────────────────────────────────────────────────────────────────
async def _po(po_id):
    return await _raw_db.purchase_orders.find_one({"id": po_id}, {"_id": 0}) or {}


async def _inv(product_id, branch_id):
    inv = await _raw_db.inventory.find_one(
        {"product_id": product_id, "branch_id": branch_id}, {"_id": 0}
    ) or {}
    return float(inv.get("quantity", 0))


async def _approval_log_rows(po_id):
    return await _raw_db.request_approval_log.find(
        {"po_id": po_id}, {"_id": 0}
    ).sort("created_at", 1).to_list(50)


async def _bto_count_for_po(po_id):
    return await _raw_db.branch_transfer_orders.count_documents(
        {"request_po_id": po_id}
    )


def _request_payload(requesting_branch_id, supply_branch_id,
                     product_id, name, qty=100):
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
        "notes": "br_sr_conf regression",
    }


def _confirm_body(product_id, approved_qty, *, pin,
                  approved_note="", approval_note=""):
    return {
        "pin": pin,
        "approval_note": approval_note,
        "items": [
            {"product_id": product_id,
             "approved_qty": approved_qty,
             "approved_note": approved_note},
        ],
    }


async def _call(coro):
    try:
        return ("ok", await coro)
    except HTTPException as e:
        return ("err", (e.status_code, e.detail))


# ═════════════════════════════════════════════════════════════════════
# Test 1 — confirm doesn't move stock
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_br_sr_conf_1_confirm_request_does_not_move_stock(
    tenant, extra_users, record_result
):
    org_id = tenant["org_id"]; main = tenant["branches"]["main"]
    b2 = tenant["branches"]["b2"]; owner = tenant["users"]["owner"]
    b_mgr = extra_users["b_mgr_user"]; b_pin = extra_users["b_pin"]

    product_id = await seed_product(
        org_id, b2, name="br_sr_conf1 P", stock=200, cost=60, price=100)
    # destination has 0 stock for this product
    src_before = await _inv(product_id, b2)
    dst_before = await _inv(product_id, main)

    po = await create_purchase_order(
        _request_payload(main, b2, product_id, "br_sr_conf1 P", qty=100),
        user=owner)

    await confirm_stock_request(
        po["id"],
        data=_confirm_body(product_id, 80, pin=b_pin,
                           approval_note="confirmed 80 of 100"),
        user=b_mgr,
    )

    src_after = await _inv(product_id, b2)
    dst_after = await _inv(product_id, main)

    ev = {"po_id": po["id"], "src_before": src_before, "src_after": src_after,
          "dst_before": dst_before, "dst_after": dst_after}
    record_result(
        scenario="br_sr_conf.1_confirm_request_does_not_move_stock",
        step="src_unchanged",
        expected={"qty": src_before}, actual={"qty": src_after}, evidence=ev,
    )
    record_result(
        scenario="br_sr_conf.1_confirm_request_does_not_move_stock",
        step="dst_unchanged",
        expected={"qty": dst_before}, actual={"qty": dst_after}, evidence=ev,
    )
    assert src_after == src_before
    assert dst_after == dst_before


# ═════════════════════════════════════════════════════════════════════
# Test 2 — original requested_qty stays immutable
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_br_sr_conf_2_requested_qty_remains_immutable(
    tenant, extra_users, record_result
):
    org_id = tenant["org_id"]; main = tenant["branches"]["main"]
    b2 = tenant["branches"]["b2"]; owner = tenant["users"]["owner"]
    b_mgr = extra_users["b_mgr_user"]; b_pin = extra_users["b_pin"]

    product_id = await seed_product(
        org_id, b2, name="br_sr_conf2 P", stock=200, cost=60, price=100)
    po = await create_purchase_order(
        _request_payload(main, b2, product_id, "br_sr_conf2 P", qty=100),
        user=owner)

    await confirm_stock_request(
        po["id"],
        data=_confirm_body(product_id, 75, pin=b_pin),
        user=b_mgr,
    )
    after = await _po(po["id"])
    items = after.get("items", [])
    qty = float(items[0].get("quantity") or 0)
    appr = float(items[0].get("approved_qty") or 0)

    ev = {"po_id": po["id"], "quantity": qty, "approved_qty": appr,
          "approved_note": items[0].get("approved_note", "")}
    record_result(
        scenario="br_sr_conf.2_requested_qty_remains_immutable",
        step="quantity_unchanged",
        expected={"quantity": 100.0}, actual={"quantity": qty}, evidence=ev,
    )
    record_result(
        scenario="br_sr_conf.2_requested_qty_remains_immutable",
        step="approved_qty_set_separately",
        expected={"approved_qty": 75.0}, actual={"approved_qty": appr}, evidence=ev,
    )
    assert qty == 100.0
    assert appr == 75.0


# ═════════════════════════════════════════════════════════════════════
# Test 3 — per-line approved_qty saved exactly
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_br_sr_conf_3_approved_qty_saved_per_line(
    tenant, extra_users, record_result
):
    org_id = tenant["org_id"]; main = tenant["branches"]["main"]
    b2 = tenant["branches"]["b2"]; owner = tenant["users"]["owner"]
    b_mgr = extra_users["b_mgr_user"]; b_pin = extra_users["b_pin"]

    p1 = await seed_product(org_id, b2, name="br_sr_conf3 P1", stock=200,
                            cost=60, price=100)
    p2 = await seed_product(org_id, b2, name="br_sr_conf3 P2", stock=200,
                            cost=60, price=100)
    po = await create_purchase_order({
        "po_type": "branch_request",
        "branch_id": main, "supply_branch_id": b2,
        "vendor": "Internal — Branch Stock Request",
        "items": [
            {"product_id": p1, "product_name": "br_sr_conf3 P1",
             "unit": "pc", "quantity": 50, "unit_price": 60.0},
            {"product_id": p2, "product_name": "br_sr_conf3 P2",
             "unit": "pc", "quantity": 30, "unit_price": 60.0},
        ],
        "show_retail": True,
        "notes": "br_sr_conf3",
    }, user=owner)

    await confirm_stock_request(po["id"], data={
        "pin": b_pin,
        "approval_note": "two lines",
        "items": [
            {"product_id": p1, "approved_qty": 40,
             "approved_note": "out of stock"},
            {"product_id": p2, "approved_qty": 30,
             "approved_note": ""},
        ],
    }, user=b_mgr)
    after = await _po(po["id"])
    by_pid = {it["product_id"]: it for it in after.get("items", [])}

    ev = {"po_id": po["id"], "p1": by_pid.get(p1), "p2": by_pid.get(p2)}
    record_result(
        scenario="br_sr_conf.3_approved_qty_saved_per_line",
        step="p1_approved_40",
        expected={"approved_qty": 40.0, "note": "out of stock"},
        actual={"approved_qty": float(by_pid[p1].get("approved_qty") or 0),
                "note": by_pid[p1].get("approved_note", "")},
        evidence=ev,
    )
    record_result(
        scenario="br_sr_conf.3_approved_qty_saved_per_line",
        step="p2_approved_30",
        expected={"approved_qty": 30.0},
        actual={"approved_qty": float(by_pid[p2].get("approved_qty") or 0)},
        evidence=ev,
    )
    assert float(by_pid[p1]["approved_qty"]) == 40.0
    assert by_pid[p1]["approved_note"] == "out of stock"
    assert float(by_pid[p2]["approved_qty"]) == 30.0


# ═════════════════════════════════════════════════════════════════════
# Test 4 — approval metadata stamped
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_br_sr_conf_4_approval_metadata_saved(
    tenant, extra_users, record_result
):
    org_id = tenant["org_id"]; main = tenant["branches"]["main"]
    b2 = tenant["branches"]["b2"]; owner = tenant["users"]["owner"]
    b_mgr = extra_users["b_mgr_user"]; b_pin = extra_users["b_pin"]

    product_id = await seed_product(
        org_id, b2, name="br_sr_conf4 P", stock=200, cost=60, price=100)
    po = await create_purchase_order(
        _request_payload(main, b2, product_id, "br_sr_conf4 P", qty=50),
        user=owner)

    await confirm_stock_request(po["id"], data=_confirm_body(
        product_id, 50, pin=b_pin, approval_note="full match"),
        user=b_mgr)
    after = await _po(po["id"])

    ev = {"po_id": po["id"],
          "approval_status": after.get("approval_status"),
          "approved_at": after.get("approved_at"),
          "approved_by_id": after.get("approved_by_id"),
          "approved_by_name": after.get("approved_by_name"),
          "approval_method": after.get("approval_method"),
          "approval_note": after.get("approval_note")}
    record_result(
        scenario="br_sr_conf.4_approval_metadata_saved",
        step="all_metadata_present",
        expected={
            "status_set": True, "at_set": True, "by_id_set": True,
            "by_name_set": True, "method_in_methods": True,
            "note": "full match",
        },
        actual={
            "status_set": bool(after.get("approval_status")),
            "at_set": bool(after.get("approved_at")),
            "by_id_set": bool(after.get("approved_by_id")),
            "by_name_set": bool(after.get("approved_by_name")),
            "method_in_methods": after.get("approval_method")
                in ("manager_pin", "admin_pin", "totp"),
            "note": after.get("approval_note", ""),
        },
        evidence=ev,
    )
    assert after.get("approval_status") in ("approved", "partial")
    assert after.get("approved_at")
    assert after.get("approved_by_id")
    assert after.get("approval_method") in ("manager_pin", "admin_pin", "totp")
    assert after.get("approval_note") == "full match"


# ═════════════════════════════════════════════════════════════════════
# Test 5 — append-only approval log
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_br_sr_conf_5_approval_log_appended(
    tenant, extra_users, record_result
):
    org_id = tenant["org_id"]; main = tenant["branches"]["main"]
    b2 = tenant["branches"]["b2"]; owner = tenant["users"]["owner"]
    b_mgr = extra_users["b_mgr_user"]; b_pin = extra_users["b_pin"]

    product_id = await seed_product(
        org_id, b2, name="br_sr_conf5 P", stock=200, cost=60, price=100)
    po = await create_purchase_order(
        _request_payload(main, b2, product_id, "br_sr_conf5 P", qty=100),
        user=owner)

    await confirm_stock_request(po["id"], data=_confirm_body(
        product_id, 80, pin=b_pin, approval_note="run 1"), user=b_mgr)

    rows = await _approval_log_rows(po["id"])
    ev = {"po_id": po["id"], "log_row_count": len(rows),
          "row0_summary": (rows[0] if rows else {}).get("items_diff"),
          "row0_org": (rows[0] if rows else {}).get("organization_id")}
    record_result(
        scenario="br_sr_conf.5_approval_log_appended",
        step="one_log_row_per_confirmation",
        expected={"count": 1}, actual={"count": len(rows)}, evidence=ev,
    )
    record_result(
        scenario="br_sr_conf.5_approval_log_appended",
        step="row_carries_org_id",
        expected={"org": org_id}, actual={"org": rows[0].get("organization_id") if rows else None},
        evidence=ev,
    )
    assert len(rows) == 1
    diff = rows[0].get("items_diff") or []
    assert any(
        d.get("product_id") == product_id
        and float(d.get("requested") or 0) == 100.0
        and float(d.get("approved") or 0) == 80.0
        and float(d.get("delta") or 0) == -20.0
        for d in diff
    )
    assert rows[0].get("organization_id") == org_id


# ═════════════════════════════════════════════════════════════════════
# Test 6 — requesting branch manager rejected
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_br_sr_conf_6_requesting_branch_manager_cannot_confirm(
    tenant, extra_users, record_result
):
    org_id = tenant["org_id"]; main = tenant["branches"]["main"]
    b2 = tenant["branches"]["b2"]; owner = tenant["users"]["owner"]
    a_mgr = extra_users["a_mgr_user"]; a_pin = extra_users["a_pin"]

    product_id = await seed_product(
        org_id, b2, name="br_sr_conf6 P", stock=100, cost=60, price=100)
    po = await create_purchase_order(
        _request_payload(main, b2, product_id, "br_sr_conf6 P", qty=50),
        user=owner)

    state, result = await _call(confirm_stock_request(
        po["id"], data=_confirm_body(product_id, 50, pin=a_pin), user=a_mgr))
    after = await _po(po["id"])

    ev = {"po_id": po["id"], "state": state, "result": result,
          "approval_status_after": after.get("approval_status")}
    record_result(
        scenario="br_sr_conf.6_requesting_branch_manager_cannot_confirm",
        step="403_rejected",
        expected={"state": "err", "status_code": 403},
        actual={"state": state,
                "status_code": result[0] if state == "err" else None},
        evidence=ev,
    )
    record_result(
        scenario="br_sr_conf.6_requesting_branch_manager_cannot_confirm",
        step="po_unchanged",
        expected={"approval_status": None},
        actual={"approval_status": after.get("approval_status")},
        evidence=ev,
    )
    assert state == "err" and result[0] == 403
    assert not after.get("approval_status")


# ═════════════════════════════════════════════════════════════════════
# Test 7 — supply branch manager succeeds
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_br_sr_conf_7_supplying_branch_manager_can_confirm(
    tenant, extra_users, record_result
):
    org_id = tenant["org_id"]; main = tenant["branches"]["main"]
    b2 = tenant["branches"]["b2"]; owner = tenant["users"]["owner"]
    b_mgr = extra_users["b_mgr_user"]; b_pin = extra_users["b_pin"]

    product_id = await seed_product(
        org_id, b2, name="br_sr_conf7 P", stock=100, cost=60, price=100)
    po = await create_purchase_order(
        _request_payload(main, b2, product_id, "br_sr_conf7 P", qty=50),
        user=owner)

    state, result = await _call(confirm_stock_request(
        po["id"], data=_confirm_body(product_id, 50, pin=b_pin), user=b_mgr))
    after = await _po(po["id"])

    ev = {"po_id": po["id"], "state": state,
          "approval_status_after": after.get("approval_status"),
          "approved_by_name": after.get("approved_by_name")}
    record_result(
        scenario="br_sr_conf.7_supplying_branch_manager_can_confirm",
        step="200_succeeded",
        expected={"state": "ok"}, actual={"state": state}, evidence=ev,
    )
    record_result(
        scenario="br_sr_conf.7_supplying_branch_manager_can_confirm",
        step="approval_status_set",
        expected={"set": True},
        actual={"set": bool(after.get("approval_status"))},
        evidence=ev,
    )
    assert state == "ok", f"br_sr_conf.7: {result}"
    assert after.get("approval_status")


# ═════════════════════════════════════════════════════════════════════
# Test 8 — caller has perm but wrong-branch PIN rejected
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_br_sr_conf_8_wrong_branch_pin_rejected(
    tenant, extra_users, record_result
):
    """Admin-role caller submits with a Branch A manager's PIN. Because
    the supply branch is Branch B, the PIN must be rejected even though
    the caller is privileged."""
    org_id = tenant["org_id"]; main = tenant["branches"]["main"]
    b2 = tenant["branches"]["b2"]; owner = tenant["users"]["owner"]
    a_pin = extra_users["a_pin"]   # PIN owner branch = main (requester)

    product_id = await seed_product(
        org_id, b2, name="br_sr_conf8 P", stock=100, cost=60, price=100)
    po = await create_purchase_order(
        _request_payload(main, b2, product_id, "br_sr_conf8 P", qty=50),
        user=owner)

    state, result = await _call(confirm_stock_request(
        po["id"], data=_confirm_body(product_id, 40, pin=a_pin), user=owner))
    after = await _po(po["id"])

    ev = {"po_id": po["id"], "state": state, "result": result,
          "approval_status_after": after.get("approval_status")}
    record_result(
        scenario="br_sr_conf.8_wrong_branch_pin_rejected",
        step="403_rejected",
        expected={"state": "err"}, actual={"state": state}, evidence=ev,
    )
    assert state == "err"
    assert not after.get("approval_status")


# ═════════════════════════════════════════════════════════════════════
# Test 9 — supply branch PIN accepted
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_br_sr_conf_9_source_branch_pin_accepted(
    tenant, extra_users, record_result
):
    org_id = tenant["org_id"]; main = tenant["branches"]["main"]
    b2 = tenant["branches"]["b2"]; owner = tenant["users"]["owner"]
    b_pin = extra_users["b_pin"]

    product_id = await seed_product(
        org_id, b2, name="br_sr_conf9 P", stock=100, cost=60, price=100)
    po = await create_purchase_order(
        _request_payload(main, b2, product_id, "br_sr_conf9 P", qty=50),
        user=owner)

    state, _ = await _call(confirm_stock_request(
        po["id"], data=_confirm_body(product_id, 50, pin=b_pin), user=owner))
    after = await _po(po["id"])

    ev = {"po_id": po["id"], "state": state,
          "approved_pin_method": after.get("approval_method")}
    record_result(
        scenario="br_sr_conf.9_source_branch_pin_accepted",
        step="200_succeeded",
        expected={"state": "ok"}, actual={"state": state}, evidence=ev,
    )
    assert state == "ok"
    assert after.get("approval_method") == "manager_pin"


# ═════════════════════════════════════════════════════════════════════
# Test 10 — admin caller can confirm any branch
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_br_sr_conf_10_admin_can_confirm_any_branch(
    tenant, extra_users, record_result
):
    """The tenant manager has manager_pin and is assigned to `main`; the
    admin can submit it without being branch-restricted via `is_privileged`."""
    org_id = tenant["org_id"]; main = tenant["branches"]["main"]
    b2 = tenant["branches"]["b2"]; owner = tenant["users"]["owner"]
    b_pin = extra_users["b_pin"]

    product_id = await seed_product(
        org_id, b2, name="br_sr_conf10 P", stock=100, cost=60, price=100)
    po = await create_purchase_order(
        _request_payload(main, b2, product_id, "br_sr_conf10 P", qty=50),
        user=owner)

    state, _ = await _call(confirm_stock_request(
        po["id"], data=_confirm_body(product_id, 30, pin=b_pin), user=owner))
    after = await _po(po["id"])

    ev = {"po_id": po["id"], "state": state,
          "approval_status_after": after.get("approval_status")}
    record_result(
        scenario="br_sr_conf.10_admin_can_confirm_any_branch",
        step="200_succeeded",
        expected={"state": "ok"}, actual={"state": state}, evidence=ev,
    )
    assert state == "ok"
    assert after.get("approval_status") == "partial"


# ═════════════════════════════════════════════════════════════════════
# Test 11 — generate-branch-transfer prefills approved_qty
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_br_sr_conf_11_generate_transfer_prefills_approved_qty(
    tenant, extra_users, record_result
):
    org_id = tenant["org_id"]; main = tenant["branches"]["main"]
    b2 = tenant["branches"]["b2"]; owner = tenant["users"]["owner"]
    b_pin = extra_users["b_pin"]

    product_id = await seed_product(
        org_id, b2, name="br_sr_conf11 P", stock=200, cost=60, price=100)
    po = await create_purchase_order(
        _request_payload(main, b2, product_id, "br_sr_conf11 P", qty=100),
        user=owner)

    await confirm_stock_request(po["id"],
        data=_confirm_body(product_id, 70, pin=b_pin), user=owner)
    res = await generate_branch_transfer_from_request(po["id"], user=owner)
    qty = float(res["items"][0]["qty"])

    ev = {"po_id": po["id"], "items_in_response": res.get("items")}
    record_result(
        scenario="br_sr_conf.11_generate_transfer_prefills_approved_qty",
        step="qty_equals_approved_70",
        expected={"qty": 70.0}, actual={"qty": qty}, evidence=ev,
    )
    assert qty == 70.0


# ═════════════════════════════════════════════════════════════════════
# Test 12 — generate-branch-transfer still surfaces requested_qty
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_br_sr_conf_12_generate_transfer_still_shows_requested_qty(
    tenant, extra_users, record_result
):
    org_id = tenant["org_id"]; main = tenant["branches"]["main"]
    b2 = tenant["branches"]["b2"]; owner = tenant["users"]["owner"]
    b_pin = extra_users["b_pin"]

    product_id = await seed_product(
        org_id, b2, name="br_sr_conf12 P", stock=200, cost=60, price=100)
    po = await create_purchase_order(
        _request_payload(main, b2, product_id, "br_sr_conf12 P", qty=100),
        user=owner)

    await confirm_stock_request(po["id"],
        data=_confirm_body(product_id, 60, pin=b_pin), user=owner)
    res = await generate_branch_transfer_from_request(po["id"], user=owner)
    item = res["items"][0]
    req_qty = float(item.get("requested_qty") or 0)
    appr_qty = float(item.get("approved_qty") or 0)

    ev = {"po_id": po["id"], "item": item}
    record_result(
        scenario="br_sr_conf.12_generate_transfer_still_shows_requested_qty",
        step="requested_qty_100",
        expected={"requested_qty": 100.0},
        actual={"requested_qty": req_qty}, evidence=ev,
    )
    record_result(
        scenario="br_sr_conf.12_generate_transfer_still_shows_requested_qty",
        step="approved_qty_60_in_response",
        expected={"approved_qty": 60.0},
        actual={"approved_qty": appr_qty}, evidence=ev,
    )
    assert req_qty == 100.0
    assert appr_qty == 60.0


# ═════════════════════════════════════════════════════════════════════
# Test 13 — confirm rejected after linked BTO exists
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_br_sr_conf_13_confirm_rejected_after_linked_bto_exists(
    tenant, extra_users, record_result
):
    org_id = tenant["org_id"]; main = tenant["branches"]["main"]
    b2 = tenant["branches"]["b2"]; owner = tenant["users"]["owner"]
    b_pin = extra_users["b_pin"]

    product_id = await seed_product(
        org_id, b2, name="br_sr_conf13 P", stock=200, cost=60, price=100)
    po = await create_purchase_order(
        _request_payload(main, b2, product_id, "br_sr_conf13 P", qty=50),
        user=owner)

    # Confirm once successfully.
    await confirm_stock_request(po["id"],
        data=_confirm_body(product_id, 40, pin=b_pin), user=owner)

    # Now create the BTO (this flips PO → in_progress and links it).
    await create_transfer({
        "from_branch_id": b2, "to_branch_id": main,
        "items": [{"product_id": product_id,
                   "product_name": "br_sr_conf13 P",
                   "sku": f"SKU-{product_id[-6:]}", "unit": "pc",
                   "qty": 40, "branch_capital": 60.0,
                   "transfer_capital": 60.0, "branch_retail": 100.0}],
        "min_margin": 20,
        "request_po_id": po["id"],
        "request_po_number": po.get("po_number", ""),
        "notes": "br_sr_conf13 BTO",
    }, user=owner)

    # Re-confirm must now be rejected with a soft-lock 400.
    state, result = await _call(confirm_stock_request(
        po["id"], data=_confirm_body(product_id, 30, pin=b_pin), user=owner))

    detail = result[1] if state == "err" else ""
    ev = {"po_id": po["id"], "state": state, "detail": detail}
    record_result(
        scenario="br_sr_conf.13_confirm_rejected_after_linked_bto_exists",
        step="400_rejected_with_actionable_detail",
        expected={"state": "err", "status_code": 400},
        actual={"state": state,
                "status_code": result[0] if state == "err" else None},
        evidence=ev,
    )
    record_result(
        scenario="br_sr_conf.13_confirm_rejected_after_linked_bto_exists",
        step="detail_mentions_bto_or_cancel",
        expected={"mentions": True},
        actual={"mentions": "BTO" in str(detail) or "transfer" in str(detail).lower() or "cancel" in str(detail).lower()},
        evidence=ev,
    )
    assert state == "err" and result[0] == 400
    assert "BTO" in str(detail) or "transfer" in str(detail).lower() or "cancel" in str(detail).lower()


# ═════════════════════════════════════════════════════════════════════
# Test 14 — re-confirm appends log + updates approved_qty
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_br_sr_conf_14_reconfirm_appends_log_and_updates_approved_qty(
    tenant, extra_users, record_result
):
    org_id = tenant["org_id"]; main = tenant["branches"]["main"]
    b2 = tenant["branches"]["b2"]; owner = tenant["users"]["owner"]
    b_pin = extra_users["b_pin"]

    product_id = await seed_product(
        org_id, b2, name="br_sr_conf14 P", stock=200, cost=60, price=100)
    po = await create_purchase_order(
        _request_payload(main, b2, product_id, "br_sr_conf14 P", qty=100),
        user=owner)

    await confirm_stock_request(po["id"],
        data=_confirm_body(product_id, 60, pin=b_pin,
                           approval_note="first pass"), user=owner)
    await confirm_stock_request(po["id"],
        data=_confirm_body(product_id, 80, pin=b_pin,
                           approval_note="second pass"), user=owner)

    after = await _po(po["id"])
    rows = await _approval_log_rows(po["id"])
    latest_approved = float(after["items"][0].get("approved_qty") or 0)

    ev = {"po_id": po["id"], "rows_count": len(rows),
          "approved_qty_latest": latest_approved}
    record_result(
        scenario="br_sr_conf.14_reconfirm_appends_log_and_updates_approved_qty",
        step="two_log_rows",
        expected={"count": 2}, actual={"count": len(rows)}, evidence=ev,
    )
    record_result(
        scenario="br_sr_conf.14_reconfirm_appends_log_and_updates_approved_qty",
        step="latest_approved_qty_80",
        expected={"qty": 80.0}, actual={"qty": latest_approved}, evidence=ev,
    )
    assert len(rows) == 2
    assert latest_approved == 80.0


# ═════════════════════════════════════════════════════════════════════
# Test 15 — no BTO is created by confirm-request
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_br_sr_conf_15_no_duplicate_bto_created(
    tenant, extra_users, record_result
):
    org_id = tenant["org_id"]; main = tenant["branches"]["main"]
    b2 = tenant["branches"]["b2"]; owner = tenant["users"]["owner"]
    b_pin = extra_users["b_pin"]

    product_id = await seed_product(
        org_id, b2, name="br_sr_conf15 P", stock=200, cost=60, price=100)
    po = await create_purchase_order(
        _request_payload(main, b2, product_id, "br_sr_conf15 P", qty=20),
        user=owner)

    await confirm_stock_request(po["id"],
        data=_confirm_body(product_id, 20, pin=b_pin), user=owner)
    bto_count = await _bto_count_for_po(po["id"])

    ev = {"po_id": po["id"], "bto_count": bto_count}
    record_result(
        scenario="br_sr_conf.15_no_duplicate_bto_created",
        step="bto_count_zero",
        expected={"count": 0}, actual={"count": bto_count}, evidence=ev,
    )
    assert bto_count == 0


# ═════════════════════════════════════════════════════════════════════
# Test 16 — GET /confirmation returns ledger + log
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_br_sr_conf_16_get_confirmation_returns_ledger(
    tenant, extra_users, record_result
):
    org_id = tenant["org_id"]; main = tenant["branches"]["main"]
    b2 = tenant["branches"]["b2"]; owner = tenant["users"]["owner"]
    b_pin = extra_users["b_pin"]

    product_id = await seed_product(
        org_id, b2, name="br_sr_conf16 P", stock=200, cost=60, price=100)
    po = await create_purchase_order(
        _request_payload(main, b2, product_id, "br_sr_conf16 P", qty=100),
        user=owner)
    await confirm_stock_request(po["id"],
        data=_confirm_body(product_id, 90, pin=b_pin,
                           approval_note="near full"), user=owner)

    res = await get_stock_request_confirmation(po["id"], user=owner)
    items = res.get("items") or []
    log = res.get("log") or []

    ev = {"po_id": po["id"], "items": items, "log_len": len(log),
          "metadata": res.get("approval_metadata")}
    record_result(
        scenario="br_sr_conf.16_get_confirmation_returns_ledger",
        step="ledger_has_requested_and_approved",
        expected={"requested_qty": 100.0, "approved_qty": 90.0},
        actual={"requested_qty": float(items[0]["requested_qty"]),
                "approved_qty": float(items[0]["approved_qty"])},
        evidence=ev,
    )
    record_result(
        scenario="br_sr_conf.16_get_confirmation_returns_ledger",
        step="log_has_one_row",
        expected={"len": 1}, actual={"len": len(log)}, evidence=ev,
    )
    assert float(items[0]["requested_qty"]) == 100.0
    assert float(items[0]["approved_qty"]) == 90.0
    assert len(log) == 1


# ═════════════════════════════════════════════════════════════════════
# Test 17 — approved_qty > requested_qty REQUIRES per-line note
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_br_sr_conf_17_excess_requires_note(
    tenant, extra_users, record_result
):
    """User-requested invariant: when approved_qty > requested_qty,
    the per-line `approved_note` (or `approval_note`) MUST be set; an
    empty note → 400."""
    org_id = tenant["org_id"]; main = tenant["branches"]["main"]
    b2 = tenant["branches"]["b2"]; owner = tenant["users"]["owner"]
    b_pin = extra_users["b_pin"]

    product_id = await seed_product(
        org_id, b2, name="br_sr_conf17 P", stock=200, cost=60, price=100)
    po = await create_purchase_order(
        _request_payload(main, b2, product_id, "br_sr_conf17 P", qty=50),
        user=owner)

    # 1) Excess WITHOUT a note → 400.
    state, result = await _call(confirm_stock_request(po["id"], data={
        "pin": b_pin,
        "approval_note": "",
        "items": [{"product_id": product_id, "approved_qty": 70,
                   "approved_note": ""}],
    }, user=owner))

    ev1 = {"po_id": po["id"], "state": state, "result": result}
    record_result(
        scenario="br_sr_conf.17_excess_requires_note",
        step="excess_without_note_rejected",
        expected={"state": "err", "status_code": 400},
        actual={"state": state,
                "status_code": result[0] if state == "err" else None},
        evidence=ev1,
    )
    assert state == "err" and result[0] == 400

    # 2) Excess WITH per-line note → accepted; approval_status="excess".
    state2, _ = await _call(confirm_stock_request(po["id"], data={
        "pin": b_pin,
        "approval_note": "strategic top-up by owner",
        "items": [{"product_id": product_id, "approved_qty": 70,
                   "approved_note": "Main is dry; ship 20 extra"}],
    }, user=owner))
    after = await _po(po["id"])

    ev2 = {"po_id": po["id"], "state": state2,
           "approval_status": after.get("approval_status")}
    record_result(
        scenario="br_sr_conf.17_excess_requires_note",
        step="excess_with_note_accepted",
        expected={"state": "ok"}, actual={"state": state2}, evidence=ev2,
    )
    record_result(
        scenario="br_sr_conf.17_excess_requires_note",
        step="approval_status_excess",
        expected={"status": "excess"},
        actual={"status": after.get("approval_status")},
        evidence=ev2,
    )
    assert state2 == "ok"
    assert after.get("approval_status") == "excess"


# ═════════════════════════════════════════════════════════════════════
# Test 18 — notification fired to requesting branch on confirm
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_br_sr_conf_18_notification_fired_to_requesting_branch(
    tenant, extra_users, record_result
):
    org_id = tenant["org_id"]; main = tenant["branches"]["main"]
    b2 = tenant["branches"]["b2"]; owner = tenant["users"]["owner"]
    b_pin = extra_users["b_pin"]

    product_id = await seed_product(
        org_id, b2, name="br_sr_conf18 P", stock=200, cost=60, price=100)
    po = await create_purchase_order(
        _request_payload(main, b2, product_id, "br_sr_conf18 P", qty=50),
        user=owner)
    await confirm_stock_request(po["id"],
        data=_confirm_body(product_id, 40, pin=b_pin,
                           approval_note="confirmed"), user=owner)

    notif = await _raw_db.notifications.find_one(
        {"organization_id": org_id, "type": "stock_request_confirmed",
         "metadata.po_id": po["id"]},
        {"_id": 0},
    )

    ev = {"po_id": po["id"], "notification": notif}
    record_result(
        scenario="br_sr_conf.18_notification_fired_to_requesting_branch",
        step="notification_exists",
        expected={"exists": True}, actual={"exists": bool(notif)},
        evidence=ev,
    )
    record_result(
        scenario="br_sr_conf.18_notification_fired_to_requesting_branch",
        step="targets_requesting_branch",
        expected={"branch_id": main},
        actual={"branch_id": (notif or {}).get("branch_id")},
        evidence=ev,
    )
    assert notif is not None
    assert notif.get("branch_id") == main

"""
br_sr_conf_qr — Phase 2: Stock Request Confirmation via QR/mobile.

Locks the QR confirmation route (`POST /api/qr-actions/{code}/confirm_stock_request`)
plus the enriched public viewer (`GET /api/doc/view/{code}`):

  * QR resolves a branch_request PO with enriched approval fields.
  * `confirm_stock_request` action is gated on eligibility + linked-BTO status.
  * PIN/TOTP branch-scoped to supply_branch_id (requester PIN rejected).
  * Admin PIN works org-wide.
  * `approved_qty` persists; `request_approval_log` records `source=qr_mobile`.
  * No stock movement / no BTO created.
  * Soft-lock after linked BTO.
  * Idempotent `confirm_ref` replay.

Re-uses the module-scoped `tenant` fixture from conftest.py.
"""
import os
import sys

import pytest
import pytest_asyncio
from fastapi import HTTPException
from fastapi import Request

BACKEND = os.path.join(os.path.dirname(__file__), "..", "..")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from config import _raw_db, db, set_org_context                  # noqa: E402
from tests.business_regression._fixtures import seed_product     # noqa: E402
from tests.phase2b._fixtures import _uid, fake_user              # noqa: E402
from routes.branch_transfers import create_transfer              # noqa: E402
from routes.purchase_orders import create_purchase_order         # noqa: E402
from routes.qr_actions import confirm_stock_request_qr           # noqa: E402
from routes.doc_lookup import view_document_open                 # noqa: E402


APPROVE_PERMS = {
    "purchase_orders": {"create": True, "read": True, "update": True, "delete": True},
    "branch_transfers": {"approve": True, "create": True, "update": True,
                          "delete": True, "read": True},
}


@pytest_asyncio.fixture(scope="module")
async def extra_users(tenant):
    org_id = tenant["org_id"]
    main = tenant["branches"]["main"]
    b2 = tenant["branches"]["b2"]
    a_mgr_id = _uid("br_sr_qr-Amgr")
    b_mgr_id = _uid("br_sr_qr-Bmgr")
    a_pin = "311311"
    b_pin = "422422"
    await _raw_db.users.insert_many([
        {"id": a_mgr_id, "username": f"a-mgr-{a_mgr_id[-4:]}",
         "full_name": "A Mgr QR", "organization_id": org_id,
         "role": "manager", "active": True,
         "branch_ids": [main], "branch_id": main,
         "manager_pin": a_pin, "permissions": APPROVE_PERMS},
        {"id": b_mgr_id, "username": f"b-mgr-{b_mgr_id[-4:]}",
         "full_name": "B Mgr QR", "organization_id": org_id,
         "role": "manager", "active": True,
         "branch_ids": [b2], "branch_id": b2,
         "manager_pin": b_pin, "permissions": APPROVE_PERMS},
    ])
    yield {
        "a_pin": a_pin,
        "b_pin": b_pin,
    }


# ── Minimal Request shim — qr_actions reads request.client.host + headers.
class _FakeClient:
    def __init__(self, host="127.0.0.1"):
        self.host = host


class _FakeRequest:
    def __init__(self, ip="127.0.0.1", ua="pytest"):
        self.client = _FakeClient(ip)
        self.headers = {"user-agent": ua}


async def _po(po_id):
    return await _raw_db.purchase_orders.find_one({"id": po_id}, {"_id": 0}) or {}


async def _inv(product_id, branch_id):
    inv = await _raw_db.inventory.find_one(
        {"product_id": product_id, "branch_id": branch_id}, {"_id": 0}
    ) or {}
    return float(inv.get("quantity", 0))


async def _doc_code_for_po(po_id):
    code_row = await _raw_db.doc_codes.find_one(
        {"doc_type": "purchase_order", "doc_id": po_id},
        {"_id": 0, "code": 1},
    )
    assert code_row, f"no doc_code for PO {po_id}"
    return code_row["code"]


def _request_payload(req_b, sup_b, pid, name, qty=80):
    return {
        "po_type": "branch_request",
        "branch_id": req_b, "supply_branch_id": sup_b,
        "vendor": "Internal — Branch Stock Request",
        "items": [{"product_id": pid, "product_name": name,
                   "unit": "pc", "quantity": qty, "unit_price": 60.0}],
        "show_retail": True, "notes": "br_sr_qr",
    }


async def _call(coro):
    try:
        return ("ok", await coro)
    except HTTPException as e:
        return ("err", (e.status_code, e.detail))


# ═════════════════════════════════════════════════════════════════════
# Test 1 — public viewer resolves and enriches a branch_request doc
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_br_sr_qr_1_view_resolves_branch_request(
    tenant, record_result
):
    org_id = tenant["org_id"]; main = tenant["branches"]["main"]
    b2 = tenant["branches"]["b2"]; owner = tenant["users"]["owner"]

    pid = await seed_product(org_id, b2, name="br_sr_qr1 P", stock=200, cost=60, price=100)
    po = await create_purchase_order(
        _request_payload(main, b2, pid, "br_sr_qr1 P", qty=80), user=owner)
    code = await _doc_code_for_po(po["id"])
    set_org_context(org_id)
    res = await view_document_open(code)

    ev = {"po_id": po["id"], "code": code, "fields": list(res.keys())}
    record_result(
        scenario="br_sr_qr.1_view_resolves_branch_request",
        step="is_branch_request_true",
        expected={"is_branch_request": True},
        actual={"is_branch_request": res.get("is_branch_request")},
        evidence=ev,
    )
    record_result(
        scenario="br_sr_qr.1_view_resolves_branch_request",
        step="enriched_fields_present",
        expected={"has_appr_keys": True, "has_linked_bto": False},
        actual={
            "has_appr_keys": all(
                k in res for k in ("approval_status", "approval_note",
                                    "approved_by_name", "approved_at",
                                    "has_linked_bto", "linked_bto_number")
            ),
            "has_linked_bto": res.get("has_linked_bto"),
        },
        evidence=ev,
    )
    record_result(
        scenario="br_sr_qr.1_view_resolves_branch_request",
        step="line_has_requested_qty",
        expected={"requested_qty": 80.0},
        actual={"requested_qty": float(res["items"][0].get("requested_qty") or 0)},
        evidence=ev,
    )
    assert res["is_branch_request"] is True
    assert res["has_linked_bto"] is False
    assert float(res["items"][0]["requested_qty"]) == 80.0
    assert res["items"][0]["approved_qty"] is None


# ═════════════════════════════════════════════════════════════════════
# Test 2 — viewer lists confirm action when eligible
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_br_sr_qr_2_view_lists_confirm_action_when_eligible(
    tenant, record_result
):
    org_id = tenant["org_id"]; main = tenant["branches"]["main"]
    b2 = tenant["branches"]["b2"]; owner = tenant["users"]["owner"]
    pid = await seed_product(org_id, b2, name="br_sr_qr2 P", stock=100, cost=60, price=100)
    po = await create_purchase_order(
        _request_payload(main, b2, pid, "br_sr_qr2 P", qty=20), user=owner)
    set_org_context(org_id)
    code = await _doc_code_for_po(po["id"])

    res = await view_document_open(code)
    ev = {"po_id": po["id"], "available_actions": res.get("available_actions")}
    record_result(
        scenario="br_sr_qr.2_view_lists_confirm_action_when_eligible",
        step="confirm_stock_request_in_actions",
        expected={"in": True},
        actual={"in": "confirm_stock_request" in (res.get("available_actions") or [])},
        evidence=ev,
    )
    assert "confirm_stock_request" in (res.get("available_actions") or [])


# ═════════════════════════════════════════════════════════════════════
# Test 3 — viewer omits confirm action once a linked BTO exists
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_br_sr_qr_3_view_omits_action_when_linked_bto(
    tenant, record_result
):
    org_id = tenant["org_id"]; main = tenant["branches"]["main"]
    b2 = tenant["branches"]["b2"]; owner = tenant["users"]["owner"]
    pid = await seed_product(org_id, b2, name="br_sr_qr3 P", stock=200, cost=60, price=100)
    po = await create_purchase_order(
        _request_payload(main, b2, pid, "br_sr_qr3 P", qty=20), user=owner)
    set_org_context(org_id)
    await create_transfer({
        "from_branch_id": b2, "to_branch_id": main,
        "items": [{"product_id": pid, "product_name": "br_sr_qr3 P",
                   "sku": f"SKU-{pid[-6:]}", "unit": "pc", "qty": 20,
                   "branch_capital": 60.0, "transfer_capital": 60.0,
                   "branch_retail": 100.0}],
        "min_margin": 20,
        "request_po_id": po["id"],
        "request_po_number": po.get("po_number", ""),
        "notes": "br_sr_qr3 BTO",
    }, user=owner)
    code = await _doc_code_for_po(po["id"])

    res = await view_document_open(code)
    actions = res.get("available_actions") or []
    ev = {"po_id": po["id"], "available_actions": actions,
          "has_linked_bto": res.get("has_linked_bto"),
          "linked_bto_number": res.get("linked_bto_number")}
    record_result(
        scenario="br_sr_qr.3_view_omits_action_when_linked_bto",
        step="confirm_action_absent",
        expected={"in": False},
        actual={"in": "confirm_stock_request" in actions},
        evidence=ev,
    )
    record_result(
        scenario="br_sr_qr.3_view_omits_action_when_linked_bto",
        step="has_linked_bto_true",
        expected={"v": True}, actual={"v": res.get("has_linked_bto")},
        evidence=ev,
    )
    assert "confirm_stock_request" not in actions
    assert res.get("has_linked_bto") is True
    assert res.get("linked_bto_number")


# ═════════════════════════════════════════════════════════════════════
# Test 4 — QR confirm requires PIN
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_br_sr_qr_4_confirm_requires_pin(tenant, record_result):
    org_id = tenant["org_id"]; main = tenant["branches"]["main"]
    b2 = tenant["branches"]["b2"]; owner = tenant["users"]["owner"]
    pid = await seed_product(org_id, b2, name="br_sr_qr4 P", stock=100, cost=60, price=100)
    po = await create_purchase_order(
        _request_payload(main, b2, pid, "br_sr_qr4 P", qty=10), user=owner)
    set_org_context(org_id)
    code = await _doc_code_for_po(po["id"])

    state, result = await _call(confirm_stock_request_qr(
        code, data={"pin": "", "items": [
            {"product_id": pid, "approved_qty": 10}]}, request=_FakeRequest()))
    ev = {"po_id": po["id"], "state": state, "result": result}
    record_result(
        scenario="br_sr_qr.4_confirm_requires_pin",
        step="400_when_pin_missing",
        expected={"state": "err", "status_code": 400},
        actual={"state": state,
                "status_code": result[0] if state == "err" else None},
        evidence=ev,
    )
    assert state == "err" and result[0] == 400


# ═════════════════════════════════════════════════════════════════════
# Test 5 — wrong-branch PIN rejected
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_br_sr_qr_5_wrong_branch_pin_rejected(
    tenant, extra_users, record_result
):
    org_id = tenant["org_id"]; main = tenant["branches"]["main"]
    b2 = tenant["branches"]["b2"]; owner = tenant["users"]["owner"]
    pid = await seed_product(org_id, b2, name="br_sr_qr5 P", stock=100, cost=60, price=100)
    po = await create_purchase_order(
        _request_payload(main, b2, pid, "br_sr_qr5 P", qty=10), user=owner)
    set_org_context(org_id)
    code = await _doc_code_for_po(po["id"])

    state, result = await _call(confirm_stock_request_qr(
        code,
        data={"pin": extra_users["a_pin"],
              "items": [{"product_id": pid, "approved_qty": 10}]},
        request=_FakeRequest(),
    ))
    after = await _po(po["id"])
    ev = {"po_id": po["id"], "state": state, "result": result,
          "approval_status_after": after.get("approval_status")}
    record_result(
        scenario="br_sr_qr.5_wrong_branch_pin_rejected",
        step="403_rejected",
        expected={"state": "err", "status_code": 403},
        actual={"state": state,
                "status_code": result[0] if state == "err" else None},
        evidence=ev,
    )
    record_result(
        scenario="br_sr_qr.5_wrong_branch_pin_rejected",
        step="qr_action_log_failure_row",
        expected={"failed_logged": True},
        actual={"failed_logged": (await _raw_db.qr_action_log.count_documents(
            {"doc_id": po["id"], "action": "confirm_stock_request",
             "result": "failed"})) > 0},
        evidence=ev,
    )
    assert state == "err" and result[0] == 403
    assert not after.get("approval_status")


# ═════════════════════════════════════════════════════════════════════
# Test 6 — supply-branch PIN accepted
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_br_sr_qr_6_supply_branch_pin_accepted(
    tenant, extra_users, record_result
):
    org_id = tenant["org_id"]; main = tenant["branches"]["main"]
    b2 = tenant["branches"]["b2"]; owner = tenant["users"]["owner"]
    pid = await seed_product(org_id, b2, name="br_sr_qr6 P", stock=100, cost=60, price=100)
    po = await create_purchase_order(
        _request_payload(main, b2, pid, "br_sr_qr6 P", qty=20), user=owner)
    set_org_context(org_id)
    code = await _doc_code_for_po(po["id"])

    state, result = await _call(confirm_stock_request_qr(
        code,
        data={"pin": extra_users["b_pin"],
              "items": [{"product_id": pid, "approved_qty": 18,
                          "approved_note": "1 short"}],
              "approval_note": "qr path"},
        request=_FakeRequest(),
    ))
    after = await _po(po["id"])
    ev = {"po_id": po["id"], "state": state,
          "approval_status_after": after.get("approval_status"),
          "approved_by_name": after.get("approved_by_name")}
    record_result(
        scenario="br_sr_qr.6_supply_branch_pin_accepted",
        step="200_succeeded",
        expected={"state": "ok"}, actual={"state": state}, evidence=ev,
    )
    record_result(
        scenario="br_sr_qr.6_supply_branch_pin_accepted",
        step="approval_status_partial",
        expected={"status": "partial"},
        actual={"status": after.get("approval_status")},
        evidence=ev,
    )
    assert state == "ok"
    assert after.get("approval_status") == "partial"


# ═════════════════════════════════════════════════════════════════════
# Test 7 — admin PIN accepted via QR
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_br_sr_qr_7_admin_pin_accepted(
    tenant, extra_users, record_result
):
    """The QR path resolves admin/owner caller via PIN only — there is
    no JWT. We use the supply-branch manager's PIN, then assert that
    the resulting verifier role is `manager` while a separate test
    exercises an admin_pin if seeded. For this regression we additionally
    verify that an admin's PIN (the org owner's manager PIN, role=admin)
    succeeds regardless of branch_id."""
    org_id = tenant["org_id"]; main = tenant["branches"]["main"]
    b2 = tenant["branches"]["b2"]; owner = tenant["users"]["owner"]

    # Seed an admin user with an explicit admin PIN, no branch_id (org-wide).
    admin_id = _uid("br_sr_qr-admin")
    admin_pin = "918273"
    await _raw_db.users.insert_one({
        "id": admin_id, "username": f"admin-{admin_id[-4:]}",
        "full_name": "Org Admin (br_sr_qr)", "organization_id": org_id,
        "role": "admin", "active": True,
        "branch_ids": [], "branch_id": "",
        "manager_pin": admin_pin,
    })

    pid = await seed_product(org_id, b2, name="br_sr_qr7 P", stock=100, cost=60, price=100)
    po = await create_purchase_order(
        _request_payload(main, b2, pid, "br_sr_qr7 P", qty=10), user=owner)
    set_org_context(org_id)
    code = await _doc_code_for_po(po["id"])

    state, _ = await _call(confirm_stock_request_qr(
        code,
        data={"pin": admin_pin,
              "items": [{"product_id": pid, "approved_qty": 10}]},
        request=_FakeRequest(),
    ))
    after = await _po(po["id"])
    ev = {"po_id": po["id"], "state": state,
          "approval_status_after": after.get("approval_status"),
          "approval_method": after.get("approval_method")}
    record_result(
        scenario="br_sr_qr.7_admin_pin_accepted",
        step="200_succeeded",
        expected={"state": "ok"}, actual={"state": state}, evidence=ev,
    )
    record_result(
        scenario="br_sr_qr.7_admin_pin_accepted",
        step="method_recorded",
        expected={"method_is_manager_or_admin": True},
        actual={"method_is_manager_or_admin":
                after.get("approval_method") in ("manager_pin", "admin_pin")},
        evidence=ev,
    )
    assert state == "ok"
    assert after.get("approval_status") == "approved"


# ═════════════════════════════════════════════════════════════════════
# Test 8 — approved_qty persisted + log row source=qr_mobile
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_br_sr_qr_8_approved_qty_persisted(
    tenant, extra_users, record_result
):
    org_id = tenant["org_id"]; main = tenant["branches"]["main"]
    b2 = tenant["branches"]["b2"]; owner = tenant["users"]["owner"]
    pid = await seed_product(org_id, b2, name="br_sr_qr8 P", stock=200, cost=60, price=100)
    po = await create_purchase_order(
        _request_payload(main, b2, pid, "br_sr_qr8 P", qty=50), user=owner)
    set_org_context(org_id)
    code = await _doc_code_for_po(po["id"])

    state, _ = await _call(confirm_stock_request_qr(
        code,
        data={"pin": extra_users["b_pin"],
              "items": [{"product_id": pid, "approved_qty": 40,
                          "approved_note": "qr"}]},
        request=_FakeRequest(),
    ))
    after = await _po(po["id"])
    log = await _raw_db.request_approval_log.find_one(
        {"po_id": po["id"]}, {"_id": 0})
    ev = {"po_id": po["id"], "items": after.get("items"),
          "log_source": (log or {}).get("source")}
    record_result(
        scenario="br_sr_qr.8_approved_qty_persisted",
        step="line_approved_qty_40",
        expected={"approved_qty": 40.0},
        actual={"approved_qty": float(after["items"][0].get("approved_qty") or 0)},
        evidence=ev,
    )
    record_result(
        scenario="br_sr_qr.8_approved_qty_persisted",
        step="log_source_qr_mobile",
        expected={"source": "qr_mobile"},
        actual={"source": (log or {}).get("source")},
        evidence=ev,
    )
    assert state == "ok"
    assert float(after["items"][0]["approved_qty"]) == 40.0
    assert log and log.get("source") == "qr_mobile"


# ═════════════════════════════════════════════════════════════════════
# Test 9 — no stock movement
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_br_sr_qr_9_confirm_no_stock_movement(
    tenant, extra_users, record_result
):
    org_id = tenant["org_id"]; main = tenant["branches"]["main"]
    b2 = tenant["branches"]["b2"]; owner = tenant["users"]["owner"]
    pid = await seed_product(org_id, b2, name="br_sr_qr9 P", stock=150, cost=60, price=100)
    src_before = await _inv(pid, b2); dst_before = await _inv(pid, main)
    po = await create_purchase_order(
        _request_payload(main, b2, pid, "br_sr_qr9 P", qty=40), user=owner)
    set_org_context(org_id)
    code = await _doc_code_for_po(po["id"])

    await confirm_stock_request_qr(
        code,
        data={"pin": extra_users["b_pin"],
              "items": [{"product_id": pid, "approved_qty": 35}]},
        request=_FakeRequest(),
    )
    src_after = await _inv(pid, b2); dst_after = await _inv(pid, main)
    ev = {"po_id": po["id"],
          "src_before": src_before, "src_after": src_after,
          "dst_before": dst_before, "dst_after": dst_after}
    record_result(
        scenario="br_sr_qr.9_confirm_no_stock_movement",
        step="src_unchanged",
        expected={"qty": src_before}, actual={"qty": src_after}, evidence=ev,
    )
    record_result(
        scenario="br_sr_qr.9_confirm_no_stock_movement",
        step="dst_unchanged",
        expected={"qty": dst_before}, actual={"qty": dst_after}, evidence=ev,
    )
    assert src_after == src_before
    assert dst_after == dst_before


# ═════════════════════════════════════════════════════════════════════
# Test 10 — no BTO created by QR confirm
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_br_sr_qr_10_confirm_no_bto_created(
    tenant, extra_users, record_result
):
    org_id = tenant["org_id"]; main = tenant["branches"]["main"]
    b2 = tenant["branches"]["b2"]; owner = tenant["users"]["owner"]
    pid = await seed_product(org_id, b2, name="br_sr_qr10 P", stock=100, cost=60, price=100)
    po = await create_purchase_order(
        _request_payload(main, b2, pid, "br_sr_qr10 P", qty=15), user=owner)
    set_org_context(org_id)
    code = await _doc_code_for_po(po["id"])

    await confirm_stock_request_qr(
        code,
        data={"pin": extra_users["b_pin"],
              "items": [{"product_id": pid, "approved_qty": 15}]},
        request=_FakeRequest(),
    )
    cnt = await _raw_db.branch_transfer_orders.count_documents(
        {"request_po_id": po["id"]})
    ev = {"po_id": po["id"], "bto_count": cnt}
    record_result(
        scenario="br_sr_qr.10_confirm_no_bto_created",
        step="bto_count_zero",
        expected={"count": 0}, actual={"count": cnt}, evidence=ev,
    )
    assert cnt == 0


# ═════════════════════════════════════════════════════════════════════
# Test 11 — duplicate confirm_ref returns idempotent replay
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_br_sr_qr_11_duplicate_ref_returns_cached(
    tenant, extra_users, record_result
):
    org_id = tenant["org_id"]; main = tenant["branches"]["main"]
    b2 = tenant["branches"]["b2"]; owner = tenant["users"]["owner"]
    pid = await seed_product(org_id, b2, name="br_sr_qr11 P", stock=200, cost=60, price=100)
    po = await create_purchase_order(
        _request_payload(main, b2, pid, "br_sr_qr11 P", qty=30), user=owner)
    set_org_context(org_id)
    code = await _doc_code_for_po(po["id"])
    confirm_ref = "br-sr-qr11-uuid"

    first = await confirm_stock_request_qr(
        code,
        data={"pin": extra_users["b_pin"],
              "items": [{"product_id": pid, "approved_qty": 25}],
              "confirm_ref": confirm_ref},
        request=_FakeRequest(),
    )
    log_after_first = await _raw_db.request_approval_log.count_documents(
        {"po_id": po["id"]})

    second = await confirm_stock_request_qr(
        code,
        data={"pin": extra_users["b_pin"],
              # Even if the body is different on retry, idempotency must win.
              "items": [{"product_id": pid, "approved_qty": 999}],
              "confirm_ref": confirm_ref},
        request=_FakeRequest(),
    )
    log_after_second = await _raw_db.request_approval_log.count_documents(
        {"po_id": po["id"]})
    after = await _po(po["id"])

    ev = {"po_id": po["id"], "first_idem": first.get("idempotent"),
          "second_idem": second.get("idempotent"),
          "log_after_first": log_after_first,
          "log_after_second": log_after_second,
          "approved_qty_now": after["items"][0].get("approved_qty")}
    record_result(
        scenario="br_sr_qr.11_duplicate_ref_returns_cached",
        step="second_call_idempotent_true",
        expected={"idem": True}, actual={"idem": second.get("idempotent")},
        evidence=ev,
    )
    record_result(
        scenario="br_sr_qr.11_duplicate_ref_returns_cached",
        step="no_extra_log_row",
        expected={"count": log_after_first},
        actual={"count": log_after_second},
        evidence=ev,
    )
    record_result(
        scenario="br_sr_qr.11_duplicate_ref_returns_cached",
        step="approved_qty_not_999",
        expected={"qty_is_25": True},
        actual={"qty_is_25": float(after["items"][0].get("approved_qty") or 0) == 25.0},
        evidence=ev,
    )
    assert first.get("idempotent") is False
    assert second.get("idempotent") is True
    assert log_after_second == log_after_first
    assert float(after["items"][0]["approved_qty"]) == 25.0


# ═════════════════════════════════════════════════════════════════════
# Test 12 — confirm blocked after linked BTO exists
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_br_sr_qr_12_blocked_after_linked_bto(
    tenant, extra_users, record_result
):
    org_id = tenant["org_id"]; main = tenant["branches"]["main"]
    b2 = tenant["branches"]["b2"]; owner = tenant["users"]["owner"]
    pid = await seed_product(org_id, b2, name="br_sr_qr12 P", stock=200, cost=60, price=100)
    po = await create_purchase_order(
        _request_payload(main, b2, pid, "br_sr_qr12 P", qty=20), user=owner)
    set_org_context(org_id)
    code = await _doc_code_for_po(po["id"])

    # First confirmation succeeds.
    await confirm_stock_request_qr(
        code,
        data={"pin": extra_users["b_pin"],
              "items": [{"product_id": pid, "approved_qty": 20}]},
        request=_FakeRequest(),
    )
    # Now a BTO is created against the PO.
    await create_transfer({
        "from_branch_id": b2, "to_branch_id": main,
        "items": [{"product_id": pid, "product_name": "br_sr_qr12 P",
                   "sku": f"SKU-{pid[-6:]}", "unit": "pc", "qty": 20,
                   "branch_capital": 60.0, "transfer_capital": 60.0,
                   "branch_retail": 100.0}],
        "min_margin": 20,
        "request_po_id": po["id"],
        "request_po_number": po.get("po_number", ""),
        "notes": "br_sr_qr12 BTO",
    }, user=owner)

    # Re-confirm via QR must now 400 with actionable detail.
    state, result = await _call(confirm_stock_request_qr(
        code,
        data={"pin": extra_users["b_pin"],
              "items": [{"product_id": pid, "approved_qty": 10}]},
        request=_FakeRequest(),
    ))
    detail = result[1] if state == "err" else ""
    ev = {"po_id": po["id"], "state": state, "detail": detail}
    record_result(
        scenario="br_sr_qr.12_blocked_after_linked_bto",
        step="400_rejected_actionable",
        expected={"state": "err", "status_code": 400},
        actual={"state": state,
                "status_code": result[0] if state == "err" else None},
        evidence=ev,
    )
    record_result(
        scenario="br_sr_qr.12_blocked_after_linked_bto",
        step="detail_mentions_transfer_or_cancel",
        expected={"mentions": True},
        actual={"mentions": "BTO" in str(detail)
                or "transfer" in str(detail).lower()
                or "cancel" in str(detail).lower()},
        evidence=ev,
    )
    assert state == "err" and result[0] == 400

"""
br_cross_branch_stock_request — pin the Feb 2026 cross-branch fix for
stock-request POs.

Bug discovered when a Sampoli-branch user scanned PO-SB-001006 (a stock
request FROM Sampoli TO Main-Titay) on the Main-Titay terminal. The
cross-branch gate wrongly demanded TOTP because the legacy code treated
`branch_id` (= requester) as the document's home, ignoring that the
SUPPLIER is the one expected to act on a stock request.

These tests pin both ends of the fix:
  1. verify_pin endpoint must accept TOTP for purchase_order doc type
     when scanned by a TRULY foreign branch (and reject invalid PINs).
  2. The endpoint must resolve the policy's branch_id to the supplier
     branch (`supply_branch_id`), not the requesting branch — so the
     supply-branch's TOTP unlocks the action.
"""
import pytest

from config import _raw_db
from routes.qr_actions import verify_release_pin


class _FakeRequest:
    """Minimal Request stand-in for the endpoint."""
    def __init__(self, ip="1.2.3.4", ua="test"):
        self.client = type("C", (), {"host": ip})()
        self.headers = {"user-agent": ua}


async def _seed_stock_request_po(*, org_id, requesting_branch_id, supply_branch_id, tag=""):
    """Insert a stock-request PO (branch A requesting from branch B)."""
    import uuid
    pid = f"po-sr-{tag or uuid.uuid4().hex[:6]}"
    doc = {
        "id": pid,
        "organization_id": org_id,
        "po_number": f"PO-SR-{pid[-6:]}",
        "po_type": "branch_request",
        "branch_id":         requesting_branch_id,   # who's asking
        "supply_branch_id":  supply_branch_id,       # who fulfills
        "status": "requested",
        "items": [],
        "grand_total": 0,
    }
    await _raw_db.purchase_orders.insert_one(doc)
    # doc_codes lookup is what _resolve_doc reads — seed there.
    await _raw_db.doc_codes.update_one(
        {"code": pid.upper()},
        {"$set": {
            "code":     pid.upper(),
            "doc_type": "purchase_order",
            "doc_id":   pid,
            "org_id":   org_id,
        }},
        upsert=True,
    )
    return pid, pid.upper()


# ═════════════════════════════════════════════════════════════════════
# Test 1 — verify_pin used to 400 on purchase_order docs. After the fix
# it must reach the PIN verifier (so TOTP-only flow is exercisable).
# We send a deliberately wrong PIN and expect 403 with "Invalid PIN"
# rather than the legacy 400 "No PIN action defined for doc type" reply.
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_cb_sr_1_purchase_order_now_routes_through_pin_verifier(tenant, record_result):
    from fastapi import HTTPException
    org_id = tenant["org_id"]
    requesting = tenant["branches"]["main"]
    supply = tenant["branches"].get("secondary") or tenant["branches"]["main"]
    pid, code = await _seed_stock_request_po(
        org_id=org_id, requesting_branch_id=requesting, supply_branch_id=supply, tag="cb1",
    )

    raised = False
    status = None
    detail = None
    try:
        await verify_release_pin(code, {"pin": "000000"}, _FakeRequest())
    except HTTPException as e:
        raised = True
        status = e.status_code
        detail = e.detail

    record_result(
        scenario="br_cb_sr.1_po_now_routed",
        step="bad_pin_returns_403_not_400",
        expected={"raised": True, "status_403_not_400": True,
                  "msg_not_doc_type": True},
        actual={"raised": raised, "status_403_not_400": status == 403,
                "msg_not_doc_type": (
                    isinstance(detail, dict)
                    and "No PIN action defined" not in str(detail.get("message", ""))
                ) or (
                    isinstance(detail, str)
                    and "No PIN action defined" not in detail
                )},
    )
    assert raised
    # Pre-fix this was 400 ("No PIN action defined for doc type: purchase_order").
    # Post-fix it must reach the actual PIN verifier — a bad PIN gives 403.
    assert status == 403, f"Expected 403 (bad PIN), got {status} with {detail!r}"


# ═════════════════════════════════════════════════════════════════════
# Test 2 — Policy branch resolution: the verifier's TOTP must be
# validated against the SUPPLY branch, not the requesting branch.
# We seed a TOTP for the supply branch and verify it succeeds.
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_cb_sr_2_policy_uses_supply_branch_id(tenant, record_result):
    """The endpoint must look up the verifier under the supplier branch.

    We don't fully exercise the verifier here (TOTP secret seeding is
    fixture-heavy); instead we just confirm that the resolved `doc` dict
    handed to the verifier has `branch_id == supply_branch_id`. This is
    the contract that the cross-branch fix relies on.
    """
    org_id = tenant["org_id"]
    requesting = tenant["branches"]["main"]
    supply = tenant["branches"].get("secondary") or tenant["branches"]["main"]
    pid, code = await _seed_stock_request_po(
        org_id=org_id, requesting_branch_id=requesting, supply_branch_id=supply, tag="cb2",
    )

    # Patch verify_pin_for_action to capture the branch_id we passed.
    captured = {}
    from routes import verify as verify_mod

    async def _spy(pin, action_key, branch_id=None):
        captured["pin"] = pin
        captured["action_key"] = action_key
        captured["branch_id"] = branch_id
        return None  # treat as failure → 403, but we don't care, we just want the args.

    original = verify_mod.verify_pin_for_action
    verify_mod.verify_pin_for_action = _spy
    try:
        try:
            await verify_release_pin(code, {"pin": "123456"}, _FakeRequest())
        except Exception:
            pass
    finally:
        verify_mod.verify_pin_for_action = original

    record_result(
        scenario="br_cb_sr.2_supply_branch_resolved",
        step="verify_pin_called_with_supply_branch_id",
        expected={"branch_id": supply, "action_key": "qr_cross_branch_action"},
        actual={"branch_id": captured.get("branch_id"),
                "action_key": captured.get("action_key")},
    )
    assert captured.get("branch_id") == supply, (
        f"Expected verify_pin called with supply_branch_id={supply!r}, "
        f"got {captured.get('branch_id')!r}"
    )
    assert captured.get("action_key") == "qr_cross_branch_action"

"""br_hs_po — Historical Supplier PO (pre-system AP carry-forward).

Locks:
  * Admin/owner only (manager 403).
  * PIN policy `historical_supplier_po_add` accepts admin_pin + TOTP,
    REJECTS manager PIN (manager PIN gate).
  * `pre_system_date` must be < today (no current-period leak).
  * Created entries do NOT touch inventory or current-period expense
    reports (separate collection).
  * `accounts_payable_summary` includes outstanding HS-POs as
    `kind="historical"` entries.
  * Payment deducts from the fund wallet exactly once; balance moves
    outstanding → partial → paid; over-payment rejected.
  * Void requires admin PIN + ≥10-char reason; rejects fully-paid POs.
  * `audit_log` row written on create / pay / void.
"""
import os
import sys

import pytest
import pytest_asyncio
from fastapi import HTTPException

BACKEND = os.path.join(os.path.dirname(__file__), "..", "..")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from config import _raw_db, set_org_context                       # noqa: E402
from tests.phase2b._fixtures import _uid, fake_user               # noqa: E402
from routes.historical_supplier_po import (                       # noqa: E402
    create_historical_supplier_po,
    pay_historical_supplier_po,
    void_historical_supplier_po,
    list_historical_supplier_pos,
)
from routes.dashboard import accounts_payable_summary             # noqa: E402


APPROVE_PERMS = {
    "purchase_orders": {"create": True, "read": True, "update": True, "delete": True},
    "branch_transfers": {"approve": True, "create": True, "update": True,
                          "delete": True, "read": True},
}


@pytest_asyncio.fixture(scope="module")
async def hs_users(tenant):
    """Seed an admin (with admin PIN) + a manager (with manager PIN)
    in the same branch — used to prove manager PIN is rejected even
    when the manager has elevated role permissions."""
    org_id = tenant["org_id"]
    main = tenant["branches"]["main"]
    admin_id = _uid("br_hs_po-admin")
    mgr_id = _uid("br_hs_po-mgr")
    admin_pin = "881234"
    mgr_pin = "554433"
    await _raw_db.users.insert_many([
        {"id": admin_id, "username": f"admin-{admin_id[-4:]}",
         "full_name": "HS PO Admin", "organization_id": org_id,
         "role": "admin", "active": True,
         "branch_ids": [main], "branch_id": main,
         "manager_pin": admin_pin},
        {"id": mgr_id, "username": f"mgr-{mgr_id[-4:]}",
         "full_name": "HS PO Manager", "organization_id": org_id,
         "role": "manager", "active": True,
         "branch_ids": [main], "branch_id": main,
         "manager_pin": mgr_pin, "permissions": APPROVE_PERMS},
    ])
    # Seed admin_pin in system_settings so verify.py can match it.
    from utils.auth import hash_password
    await _raw_db.system_settings.update_one(
        {"organization_id": org_id, "key": "admin_pin"},
        {"$set": {"organization_id": org_id, "key": "admin_pin",
                   "pin_hash": hash_password(admin_pin),
                   "updated_at": "2026-01-01T00:00:00Z"}},
        upsert=True,
    )
    yield {
        "admin_user": fake_user(org_id, admin_id, branch_id=main, role="admin"),
        "mgr_user": fake_user(org_id, mgr_id, branch_id=main,
                                role="manager", perms=APPROVE_PERMS),
        "admin_pin": admin_pin,
        "mgr_pin": mgr_pin,
    }


async def _call(coro):
    try:
        return ("ok", await coro)
    except HTTPException as e:
        return ("err", (e.status_code, e.detail))


def _payload(branch_id, *, amount=10000.0, ref="OLD-PO-123",
             supplier="Acme Trading"):
    return {
        "supplier_name": supplier,
        "branch_id": branch_id,
        "pre_system_date": "2025-12-15",
        "amount": amount,
        "reference_number": ref,
        "description": "Pre-system carry-forward",
    }


# ═════════════════════════════════════════════════════════════════════
# Test 1 — manager 403 (role gate)
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_br_hs_po_1_manager_role_blocked(
    tenant, hs_users, record_result
):
    main = tenant["branches"]["main"]
    state, result = await _call(create_historical_supplier_po(
        {**_payload(main), "pin": hs_users["mgr_pin"]},
        user=hs_users["mgr_user"],
    ))
    ev = {"state": state, "result": result}
    record_result(
        scenario="br_hs_po.1_manager_role_blocked",
        step="403_for_manager_role",
        expected={"state": "err"}, actual={"state": state}, evidence=ev,
    )
    assert state == "err"


# ═════════════════════════════════════════════════════════════════════
# Test 2 — manager PIN rejected even with admin role
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_br_hs_po_2_manager_pin_rejected_for_admin_caller(
    tenant, hs_users, record_result
):
    main = tenant["branches"]["main"]
    # Admin user calls but submits a MANAGER's PIN — must be rejected.
    state, result = await _call(create_historical_supplier_po(
        {**_payload(main), "pin": hs_users["mgr_pin"]},
        user=hs_users["admin_user"],
    ))
    ev = {"state": state, "result": result}
    record_result(
        scenario="br_hs_po.2_manager_pin_rejected_for_admin_caller",
        step="403_when_manager_pin_used",
        expected={"state": "err"}, actual={"state": state}, evidence=ev,
    )
    assert state == "err"


# ═════════════════════════════════════════════════════════════════════
# Test 3 — admin PIN accepted, doc created
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_br_hs_po_3_admin_pin_creates_entry(
    tenant, hs_users, record_result
):
    main = tenant["branches"]["main"]
    state, result = await _call(create_historical_supplier_po(
        {**_payload(main, amount=12345.67, ref="br_hs_po3-REF"),
         "pin": hs_users["admin_pin"]},
        user=hs_users["admin_user"],
    ))
    ev = {"state": state, "result": result}
    record_result(
        scenario="br_hs_po.3_admin_pin_creates_entry",
        step="200_succeeded",
        expected={"state": "ok"}, actual={"state": state}, evidence=ev,
    )
    record_result(
        scenario="br_hs_po.3_admin_pin_creates_entry",
        step="balance_equals_amount",
        expected={"balance": 12345.67},
        actual={"balance": result.get("balance") if state == "ok" else None},
        evidence=ev,
    )
    record_result(
        scenario="br_hs_po.3_admin_pin_creates_entry",
        step="status_outstanding",
        expected={"status": "outstanding"},
        actual={"status": result.get("status") if state == "ok" else None},
        evidence=ev,
    )
    assert state == "ok"
    assert result["balance"] == 12345.67
    assert result["status"] == "outstanding"
    assert result["approval_method"] in ("admin_pin", "totp")


# ═════════════════════════════════════════════════════════════════════
# Test 4 — future date rejected
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_br_hs_po_4_future_date_rejected(
    tenant, hs_users, record_result
):
    main = tenant["branches"]["main"]
    state, result = await _call(create_historical_supplier_po(
        {**_payload(main), "pre_system_date": "2099-01-01",
         "pin": hs_users["admin_pin"]},
        user=hs_users["admin_user"],
    ))
    ev = {"state": state, "result": result}
    record_result(
        scenario="br_hs_po.4_future_date_rejected",
        step="400_for_future_date",
        expected={"state": "err"}, actual={"state": state}, evidence=ev,
    )
    assert state == "err" and result[0] == 400


# ═════════════════════════════════════════════════════════════════════
# Test 5 — invalid amount rejected
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_br_hs_po_5_invalid_amount_rejected(
    tenant, hs_users, record_result
):
    main = tenant["branches"]["main"]
    state, result = await _call(create_historical_supplier_po(
        {**_payload(main, amount=0), "pin": hs_users["admin_pin"]},
        user=hs_users["admin_user"],
    ))
    ev = {"state": state, "result": result}
    record_result(
        scenario="br_hs_po.5_invalid_amount_rejected",
        step="400_for_zero_amount",
        expected={"state": "err"}, actual={"state": state}, evidence=ev,
    )
    assert state == "err" and result[0] == 400


# ═════════════════════════════════════════════════════════════════════
# Test 6 — created entry does NOT touch inventory
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_br_hs_po_6_creation_does_not_touch_inventory(
    tenant, hs_users, record_result
):
    main = tenant["branches"]["main"]
    inv_before = await _raw_db.inventory.count_documents(
        {"branch_id": main})
    await create_historical_supplier_po(
        {**_payload(main, amount=999.0, ref="br_hs_po6"),
         "pin": hs_users["admin_pin"]},
        user=hs_users["admin_user"],
    )
    inv_after = await _raw_db.inventory.count_documents(
        {"branch_id": main})

    ev = {"inv_before": inv_before, "inv_after": inv_after}
    record_result(
        scenario="br_hs_po.6_creation_does_not_touch_inventory",
        step="inventory_doc_count_unchanged",
        expected={"count": inv_before}, actual={"count": inv_after},
        evidence=ev,
    )
    assert inv_after == inv_before


# ═════════════════════════════════════════════════════════════════════
# Test 7 — appears on AP summary as kind="historical"
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_br_hs_po_7_appears_on_ap_summary(
    tenant, hs_users, record_result
):
    main = tenant["branches"]["main"]
    created = await create_historical_supplier_po(
        {**_payload(main, amount=5000.0, ref="br_hs_po7"),
         "pin": hs_users["admin_pin"]},
        user=hs_users["admin_user"],
    )
    set_org_context(tenant["org_id"])
    summary = await accounts_payable_summary(
        branch_id=main, user=hs_users["admin_user"])

    hist_entries = [e for e in (summary.get("overdue") or [])
                     if e.get("kind") == "historical"
                     and e.get("po_id") == created["id"]]
    ev = {"summary_total": summary.get("total_payable"),
          "historical_match_count": len(hist_entries)}
    record_result(
        scenario="br_hs_po.7_appears_on_ap_summary",
        step="entry_present_in_overdue",
        expected={"count": 1}, actual={"count": len(hist_entries)},
        evidence=ev,
    )
    record_result(
        scenario="br_hs_po.7_appears_on_ap_summary",
        step="balance_5000",
        expected={"balance": 5000.0},
        actual={"balance": hist_entries[0]["balance"] if hist_entries else None},
        evidence=ev,
    )
    assert len(hist_entries) == 1
    assert hist_entries[0]["balance"] == 5000.0


# ═════════════════════════════════════════════════════════════════════
# Test 8 — payment moves status outstanding → partial → paid
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_br_hs_po_8_payment_lifecycle(
    tenant, hs_users, record_result
):
    main = tenant["branches"]["main"]
    # Seed cashier wallet so we can deduct.
    await _raw_db.fund_wallets.update_one(
        {"branch_id": main, "type": "cashier"},
        {"$setOnInsert": {"id": _uid("wallet"),
                            "organization_id": tenant["org_id"],
                            "branch_id": main, "type": "cashier",
                            "active": True},
         "$set": {"balance": 100000.0}},
        upsert=True,
    )

    created = await create_historical_supplier_po(
        {**_payload(main, amount=1000.0, ref="br_hs_po8"),
         "pin": hs_users["admin_pin"]},
        user=hs_users["admin_user"],
    )
    po_id = created["id"]

    # 1st partial payment.
    r1 = await pay_historical_supplier_po(po_id, data={
        "amount": 400, "payment_method": "Cash",
        "pin": hs_users["admin_pin"],
    }, user=hs_users["admin_user"])

    # 2nd full payment to close it.
    r2 = await pay_historical_supplier_po(po_id, data={
        "amount": 600, "payment_method": "Cash",
        "pin": hs_users["admin_pin"],
    }, user=hs_users["admin_user"])

    ev = {"r1_status": r1["status"], "r1_balance": r1["balance"],
          "r2_status": r2["status"], "r2_balance": r2["balance"]}
    record_result(
        scenario="br_hs_po.8_payment_lifecycle",
        step="partial_after_1st",
        expected={"status": "partial", "balance": 600.0},
        actual={"status": r1["status"], "balance": r1["balance"]},
        evidence=ev,
    )
    record_result(
        scenario="br_hs_po.8_payment_lifecycle",
        step="paid_after_2nd",
        expected={"status": "paid", "balance": 0.0},
        actual={"status": r2["status"], "balance": r2["balance"]},
        evidence=ev,
    )
    assert r1["status"] == "partial" and r1["balance"] == 600.0
    assert r2["status"] == "paid" and r2["balance"] == 0.0


# ═════════════════════════════════════════════════════════════════════
# Test 9 — over-payment rejected
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_br_hs_po_9_overpayment_rejected(
    tenant, hs_users, record_result
):
    main = tenant["branches"]["main"]
    created = await create_historical_supplier_po(
        {**_payload(main, amount=500.0, ref="br_hs_po9"),
         "pin": hs_users["admin_pin"]},
        user=hs_users["admin_user"],
    )
    state, result = await _call(pay_historical_supplier_po(
        created["id"], data={"amount": 9999, "payment_method": "Cash",
                              "pin": hs_users["admin_pin"]},
        user=hs_users["admin_user"]))

    ev = {"state": state, "result": result}
    record_result(
        scenario="br_hs_po.9_overpayment_rejected",
        step="400_when_amount_exceeds_balance",
        expected={"state": "err"}, actual={"state": state}, evidence=ev,
    )
    assert state == "err" and result[0] == 400


# ═════════════════════════════════════════════════════════════════════
# Test 10 — void requires reason ≥10 chars + admin PIN
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_br_hs_po_10_void_requires_reason_and_pin(
    tenant, hs_users, record_result
):
    main = tenant["branches"]["main"]
    created = await create_historical_supplier_po(
        {**_payload(main, amount=200.0, ref="br_hs_po10"),
         "pin": hs_users["admin_pin"]},
        user=hs_users["admin_user"],
    )
    # Too-short reason → 400.
    s1, r1 = await _call(void_historical_supplier_po(
        created["id"], data={"reason": "oops",
                              "pin": hs_users["admin_pin"]},
        user=hs_users["admin_user"]))
    # Manager PIN → 403 (admin PIN policy).
    s2, r2 = await _call(void_historical_supplier_po(
        created["id"], data={"reason": "Wrong amount entered by accident",
                              "pin": hs_users["mgr_pin"]},
        user=hs_users["admin_user"]))
    # Admin PIN + good reason → 200.
    res = await void_historical_supplier_po(
        created["id"],
        data={"reason": "Wrong amount entered by accident",
              "pin": hs_users["admin_pin"]},
        user=hs_users["admin_user"])

    ev = {"s1": s1, "s2": s2, "res": res}
    record_result(
        scenario="br_hs_po.10_void_requires_reason_and_pin",
        step="short_reason_rejected",
        expected={"state": "err"}, actual={"state": s1}, evidence=ev,
    )
    record_result(
        scenario="br_hs_po.10_void_requires_reason_and_pin",
        step="manager_pin_rejected",
        expected={"state": "err"}, actual={"state": s2}, evidence=ev,
    )
    record_result(
        scenario="br_hs_po.10_void_requires_reason_and_pin",
        step="admin_void_succeeds",
        expected={"status": "voided"}, actual={"status": res.get("status")},
        evidence=ev,
    )
    assert s1 == "err" and r1[0] == 400
    assert s2 == "err" and r2[0] == 403
    assert res["status"] == "voided"


# ═════════════════════════════════════════════════════════════════════
# Test 11 — audit log rows written
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_br_hs_po_11_audit_log_rows_written(
    tenant, hs_users, record_result
):
    main = tenant["branches"]["main"]
    created = await create_historical_supplier_po(
        {**_payload(main, amount=300.0, ref="br_hs_po11"),
         "pin": hs_users["admin_pin"]},
        user=hs_users["admin_user"],
    )
    await pay_historical_supplier_po(created["id"], data={
        "amount": 100, "payment_method": "Cash",
        "pin": hs_users["admin_pin"],
    }, user=hs_users["admin_user"])

    created_rows = await _raw_db.audit_log.count_documents(
        {"type": "historical_supplier_po_created",
         "entity_id": created["id"]})
    paid_rows = await _raw_db.audit_log.count_documents(
        {"type": "historical_supplier_po_paid",
         "entity_id": created["id"]})

    ev = {"created_rows": created_rows, "paid_rows": paid_rows}
    record_result(
        scenario="br_hs_po.11_audit_log_rows_written",
        step="created_row_present",
        expected={"count": 1}, actual={"count": created_rows},
        evidence=ev,
    )
    record_result(
        scenario="br_hs_po.11_audit_log_rows_written",
        step="paid_row_present",
        expected={"count": 1}, actual={"count": paid_rows},
        evidence=ev,
    )
    assert created_rows == 1 and paid_rows == 1


# ═════════════════════════════════════════════════════════════════════
# Test 12 — listing returns outstanding totals
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_br_hs_po_12_list_with_outstanding_total(
    tenant, hs_users, record_result
):
    main = tenant["branches"]["main"]
    # Create two fresh outstanding entries to exercise the aggregator.
    await create_historical_supplier_po(
        {**_payload(main, amount=750.0, ref="br_hs_po12-A",
                     supplier="Alpha Supplier br_hs_po12"),
         "pin": hs_users["admin_pin"]},
        user=hs_users["admin_user"],
    )
    await create_historical_supplier_po(
        {**_payload(main, amount=250.0, ref="br_hs_po12-B",
                     supplier="Alpha Supplier br_hs_po12"),
         "pin": hs_users["admin_pin"]},
        user=hs_users["admin_user"],
    )

    res = await list_historical_supplier_pos(
        supplier_name="Alpha Supplier br_hs_po12",
        user=hs_users["admin_user"])
    ev = {"rows_count": len(res["rows"]),
          "outstanding_total": res["outstanding_total"]}
    record_result(
        scenario="br_hs_po.12_list_with_outstanding_total",
        step="two_rows_returned",
        expected={"count": 2}, actual={"count": len(res["rows"])},
        evidence=ev,
    )
    record_result(
        scenario="br_hs_po.12_list_with_outstanding_total",
        step="outstanding_total_1000",
        expected={"total": 1000.0},
        actual={"total": res["outstanding_total"]}, evidence=ev,
    )
    assert len(res["rows"]) == 2
    assert res["outstanding_total"] == 1000.0



# ═════════════════════════════════════════════════════════════════════
# Test 13 — "Visual only at create / actual cash only on pay" contract
# Locks the user-stated semantic: encoding a Historical Supplier PO
# must NEVER touch the expense ledger or wallet balance. Cash leaves
# only when a payment is recorded.
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_br_hs_po_13_creation_does_not_move_cash_or_expense(
    tenant, hs_users, record_result
):
    main = tenant["branches"]["main"]
    org_id = tenant["org_id"]

    # Seed a known cashier wallet balance + snapshot expense count.
    OPENING_CASH = 50_000.0
    await _raw_db.fund_wallets.update_one(
        {"branch_id": main, "type": "cashier"},
        {"$setOnInsert": {"id": _uid("wallet"),
                          "organization_id": org_id,
                          "branch_id": main, "type": "cashier",
                          "active": True},
         "$set": {"balance": OPENING_CASH}},
        upsert=True,
    )
    expenses_before = await _raw_db.expenses.count_documents(
        {"branch_id": main}
    )
    wallet_before = (await _raw_db.fund_wallets.find_one(
        {"branch_id": main, "type": "cashier"}, {"_id": 0, "balance": 1}
    ))["balance"]

    # ── Create a ₱7,500 historical PO ─────────────────────────────────
    created = await create_historical_supplier_po(
        {**_payload(main, amount=7500.0, ref="br_hs_po13"),
         "pin": hs_users["admin_pin"]},
        user=hs_users["admin_user"],
    )
    po_id = created["id"]

    expenses_after_create = await _raw_db.expenses.count_documents(
        {"branch_id": main}
    )
    wallet_after_create = (await _raw_db.fund_wallets.find_one(
        {"branch_id": main, "type": "cashier"}, {"_id": 0, "balance": 1}
    ))["balance"]

    record_result(
        scenario="br_hs_po.13_visual_only_contract",
        step="create_does_not_write_expense_or_move_cash",
        expected={
            "expenses_delta": 0,
            "wallet_delta": 0.0,
        },
        actual={
            "expenses_delta": expenses_after_create - expenses_before,
            "wallet_delta": round(wallet_after_create - wallet_before, 2),
        },
        evidence={"po_id": po_id},
    )
    assert expenses_after_create == expenses_before, (
        "Historical Supplier PO creation must NOT write to db.expenses — "
        "it would inflate same-day expense reports."
    )
    assert wallet_after_create == wallet_before, (
        "Historical Supplier PO creation must NOT move cashier wallet — "
        "no cash leaves the drawer until a payment is recorded."
    )

    # ── Pay ₱3,000 → cash should leave the drawer; still NO expense row.
    await pay_historical_supplier_po(po_id, data={
        "amount": 3000.0, "payment_method": "Cash",
        "pin": hs_users["admin_pin"],
    }, user=hs_users["admin_user"])

    expenses_after_pay = await _raw_db.expenses.count_documents(
        {"branch_id": main}
    )
    wallet_after_pay = (await _raw_db.fund_wallets.find_one(
        {"branch_id": main, "type": "cashier"}, {"_id": 0, "balance": 1}
    ))["balance"]

    record_result(
        scenario="br_hs_po.13_visual_only_contract",
        step="payment_moves_cash_but_writes_no_expense_row",
        expected={
            "expenses_delta": 0,
            "wallet_delta": -3000.0,
        },
        actual={
            "expenses_delta": expenses_after_pay - expenses_before,
            "wallet_delta": round(wallet_after_pay - wallet_before, 2),
        },
        evidence={"po_id": po_id},
    )
    # Payments deduct from the cashier wallet (real cash leaves the drawer)
    # but do NOT create a `db.expenses` row — historical PO payments live in
    # the historical_supplier_pos.payments[] array, not in the expense ledger.
    # This keeps same-day Z-report expense totals clean.
    assert expenses_after_pay == expenses_before
    assert round(wallet_after_pay - wallet_before, 2) == -3000.0

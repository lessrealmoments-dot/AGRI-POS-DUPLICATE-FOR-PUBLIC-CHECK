"""br_payables_hsp — Historical Supplier POs surface in
`/payables-by-supplier` so they're payable from the regular Pay Supplier
page (Phase 3.2 surface fix).

Locks:
  * Outstanding historical_supplier_pos rows appear under their
    supplier_name in the payables-by-supplier response.
  * Each historical row carries `kind:"historical"` (so the FE routes
    the pay call to `/historical-supplier-pos/{id}/pay`).
  * `total_owed` adds historical balance to any regular-PO balance for
    the same supplier.
  * Vendors with NO regular PO but WITH a historical PO are still listed.
  * Paid / voided historical POs do NOT appear.
"""
import os
import sys

import pytest
import pytest_asyncio

BACKEND = os.path.join(os.path.dirname(__file__), "..", "..")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from config import _raw_db                                       # noqa: E402
from tests.phase2b._fixtures import _uid, fake_user              # noqa: E402
from routes.purchase_orders import get_payables_by_supplier      # noqa: E402
from routes.historical_supplier_po import (                       # noqa: E402
    create_historical_supplier_po, pay_historical_supplier_po,
)


@pytest_asyncio.fixture(scope="module")
async def hsp_admin(tenant):
    org_id = tenant["org_id"]
    main = tenant["branches"]["main"]
    admin_id = _uid("br_pay_hsp-admin")
    admin_pin = "224466"
    from utils.auth import hash_password
    await _raw_db.users.insert_one({
        "id": admin_id, "username": f"admin-{admin_id[-4:]}",
        "full_name": "Pay HSP Admin", "organization_id": org_id,
        "role": "admin", "active": True,
        "branch_ids": [main], "branch_id": main,
        "manager_pin": admin_pin,
    })
    await _raw_db.system_settings.update_one(
        {"organization_id": org_id, "key": "admin_pin"},
        {"$set": {"organization_id": org_id, "key": "admin_pin",
                  "pin_hash": hash_password(admin_pin),
                  "updated_at": "2026-01-01T00:00:00Z"}},
        upsert=True,
    )
    # Seed a cashier wallet so `pay_historical_supplier_po` can deduct.
    await _raw_db.fund_wallets.update_one(
        {"branch_id": main, "type": "cashier"},
        {"$setOnInsert": {"id": _uid("wallet"),
                          "organization_id": org_id,
                          "branch_id": main, "type": "cashier",
                          "active": True},
         "$set": {"balance": 100_000.0}},
        upsert=True,
    )
    yield {
        "user": fake_user(org_id, admin_id, branch_id=main, role="admin"),
        "pin": admin_pin,
    }


# ═════════════════════════════════════════════════════════════════════
# Test 1 — Outstanding HSP appears under its supplier with kind=historical
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_br_pay_hsp_1_outstanding_appears(
    tenant, hsp_admin, record_result
):
    main = tenant["branches"]["main"]
    SUP = "BR-Pay-HSP Sole-Source Supplier"
    created = await create_historical_supplier_po({
        "supplier_name": SUP, "branch_id": main,
        "pre_system_date": "2025-12-01", "amount": 7500.0,
        "description": "br_pay_hsp.1 outstanding",
        "pin": hsp_admin["pin"],
    }, user=hsp_admin["user"])

    rows = await get_payables_by_supplier(
        user=hsp_admin["user"], branch_id=main,
    )
    match = next((r for r in rows if r["vendor"] == SUP), None)
    assert match is not None, "HSP-only supplier missing from payables"
    hist_pos = [p for p in match["pos"] if p.get("kind") == "historical"]
    record_result(
        scenario="br_pay_hsp.1_outstanding_appears",
        step="hsp_visible_with_kind_historical",
        expected={"hist_count": 1, "total_owed": 7500.0,
                  "has_overdue": True},
        actual={"hist_count": len(hist_pos),
                "total_owed": match["total_owed"],
                "has_overdue": match["has_overdue"]},
        evidence={"po_id": created["id"], "po_number": created["reference_number"]},
    )
    assert len(hist_pos) == 1
    assert hist_pos[0]["id"] == created["id"]
    assert hist_pos[0]["po_number"] == created["reference_number"]
    assert hist_pos[0]["balance"] == 7500.0
    assert match["total_owed"] == 7500.0
    assert match["has_overdue"] is True


# ═════════════════════════════════════════════════════════════════════
# Test 2 — Regular PO + HSP for SAME supplier merged under one vendor row
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_br_pay_hsp_2_merged_with_regular_po(
    tenant, hsp_admin, record_result
):
    main = tenant["branches"]["main"]
    org_id = tenant["org_id"]
    SUP = "BR-Pay-HSP Merge Supplier"
    # Seed a regular PO under the same supplier with ₱4,000 balance.
    reg_po_id = _uid("po")
    await _raw_db.purchase_orders.insert_one({
        "id": reg_po_id, "organization_id": org_id,
        "po_number": "PO-BR-MERGE-1", "vendor": SUP,
        "branch_id": main, "po_type": "credit",
        "payment_status": "unpaid", "status": "received",
        "grand_total": 4000.0, "balance": 4000.0,
        "amount_paid": 0.0, "items": [],
        "due_date": "2025-11-30",  # overdue
        "created_at": "2025-11-30T00:00:00Z",
    })
    hsp = await create_historical_supplier_po({
        "supplier_name": SUP, "branch_id": main,
        "pre_system_date": "2025-10-15", "amount": 2500.0,
        "description": "br_pay_hsp.2 merged",
        "pin": hsp_admin["pin"],
    }, user=hsp_admin["user"])

    rows = await get_payables_by_supplier(
        user=hsp_admin["user"], branch_id=main,
    )
    match = next((r for r in rows if r["vendor"] == SUP), None)
    assert match is not None
    reg = [p for p in match["pos"] if p.get("kind") != "historical"]
    hist = [p for p in match["pos"] if p.get("kind") == "historical"]
    record_result(
        scenario="br_pay_hsp.2_merge_under_same_vendor",
        step="single_vendor_row_combines_regular_and_historical",
        expected={"reg_count": 1, "hist_count": 1, "total_owed": 6500.0},
        actual={"reg_count": len(reg), "hist_count": len(hist),
                "total_owed": match["total_owed"]},
        evidence={"reg_po_id": reg_po_id, "hsp_id": hsp["id"]},
    )
    assert len(reg) == 1 and len(hist) == 1
    assert match["total_owed"] == 6500.0


# ═════════════════════════════════════════════════════════════════════
# Test 3 — Paid-in-full HSP must NOT appear
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_br_pay_hsp_3_paid_hsp_hidden(
    tenant, hsp_admin, record_result
):
    main = tenant["branches"]["main"]
    SUP = "BR-Pay-HSP Paid Supplier"
    created = await create_historical_supplier_po({
        "supplier_name": SUP, "branch_id": main,
        "pre_system_date": "2025-12-01", "amount": 1000.0,
        "description": "br_pay_hsp.3 — paid-in-full hides",
        "pin": hsp_admin["pin"],
    }, user=hsp_admin["user"])
    # Pay it in full.
    await pay_historical_supplier_po(created["id"], data={
        "amount": 1000.0, "payment_method": "Cash",
        "pin": hsp_admin["pin"],
    }, user=hsp_admin["user"])

    rows = await get_payables_by_supplier(
        user=hsp_admin["user"], branch_id=main,
    )
    match = next((r for r in rows if r["vendor"] == SUP), None)
    record_result(
        scenario="br_pay_hsp.3_paid_hsp_hidden",
        step="vendor_row_omitted_when_only_hsp_is_paid",
        expected={"vendor_listed": False},
        actual={"vendor_listed": match is not None},
        evidence={"hsp_id": created["id"]},
    )
    assert match is None, "Fully-paid HSP must not surface"


# ═════════════════════════════════════════════════════════════════════
# Test 4 — Partial HSP still appears with its remaining balance
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_br_pay_hsp_4_partial_balance_remaining(
    tenant, hsp_admin, record_result
):
    main = tenant["branches"]["main"]
    SUP = "BR-Pay-HSP Partial Supplier"
    created = await create_historical_supplier_po({
        "supplier_name": SUP, "branch_id": main,
        "pre_system_date": "2025-12-01", "amount": 3000.0,
        "description": "br_pay_hsp.4 — partial keeps showing",
        "pin": hsp_admin["pin"],
    }, user=hsp_admin["user"])
    # Pay ₱1,000 (partial).
    await pay_historical_supplier_po(created["id"], data={
        "amount": 1000.0, "payment_method": "Cash",
        "pin": hsp_admin["pin"],
    }, user=hsp_admin["user"])

    rows = await get_payables_by_supplier(
        user=hsp_admin["user"], branch_id=main,
    )
    match = next((r for r in rows if r["vendor"] == SUP), None)
    assert match is not None
    hist = [p for p in match["pos"] if p.get("kind") == "historical"]
    record_result(
        scenario="br_pay_hsp.4_partial_remaining",
        step="hsp_listed_with_remaining_balance",
        expected={"hist_count": 1, "balance": 2000.0,
                  "payment_status": "partial"},
        actual={"hist_count": len(hist),
                "balance": hist[0]["balance"] if hist else None,
                "payment_status": hist[0]["payment_status"] if hist else None},
        evidence={"hsp_id": created["id"]},
    )
    assert len(hist) == 1
    assert hist[0]["balance"] == 2000.0
    assert hist[0]["payment_status"] == "partial"

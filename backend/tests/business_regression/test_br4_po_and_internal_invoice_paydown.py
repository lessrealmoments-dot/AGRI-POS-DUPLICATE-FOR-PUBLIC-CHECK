"""
br4 — Money-loop paydown: PO supplier payable + internal_invoice drain.

Closes the financial half of br2 (payables) + br3 (internal_invoices).

Discovered behaviour (read from `routes/purchase_orders.py` and
`routes/internal_invoices.py` BEFORE writing the test, NOT assumed):

  PO paydown — `POST /purchase-orders/{po_id}/pay`
    * Permission: `check_perm(user, "accounting", "create")` — admin bypasses.
    * PIN REQUIRED. Policy key `pay_po_standard` for cashier/safe (resolves
      via admin_pin in system_settings, manager_pin on users, TOTP, or
      auditor_pin). Bank/digital paths use `pay_po_bank` (admin/TOTP only)
      — NOT exercised here.
    * Amount validation: rejects 0/negative AND amount > stored_balance +
      ₱0.01 tolerance → HTTP 400 "Payment ₱X exceeds outstanding balance".
    * Fund source dispatch: cashier → `update_cashier_wallet(-amount)`,
      safe → safe-lot drawdown, bank/digital → wallet decrement + audit
      wallet_movement row.
    * Status transitions: PO `payment_status` flips `unpaid` → `partial`
      (with payment_history entry) → `paid` once `balance == 0`.
    * Linked `payables` row is updated in lockstep (`paid`, `balance`,
      `status` mirror the PO).
    * Already-paid guard: second `/pay` on a fully-paid PO → 400
      "PO already paid in full".
    * `expenses` row is inserted unconditionally (Z-Report carryover).
    * Journal entries are created ONLY for bank/digital — explicitly
      out of scope for the cashier flow exercised here.

  Internal-invoice paydown — `POST /internal-invoices/{invoice_id}/pay`
    * Permission: role MUST be exactly `admin` → 403 otherwise.
    * NO PIN required.
    * Already-paid guard: second `/pay` → 400 "Invoice is already paid".
    * Bank wallets REQUIRED at BOTH branches (to_branch = buyer/payer,
      from_branch = supplier/payee). 400 "no bank wallet" otherwise.
    * Buyer-bank balance check: insufficient → 400 with structured
      `insufficient_funds` detail (balance, required, shortfall).
    * Side effects: 2× wallet_movements (cash_out @ buyer + cash_in @
      supplier), invoice flipped to `paid` / `payment_status=paid`,
      `paid_amount` + `paid_at` + `paid_by` recorded, single
      `internal_invoice_paid` notification.

Scope deliberately deferred to later prompts:
  * Bank/digital PO payment path (TOTP-only PIN, journal entries).
  * Auto-pay scheduler tick (covered by the per-tenant refactor; needs
    its own time-warp test setup).
  * `internal_invoices` insufficient-funds happy-recovery path (just one
    400-shape assertion here, no wallet-mutation rollback test yet).
  * PO `/adjust-payment` — that's an *edit-delta* route, NOT a paydown;
    asserting it belongs to a future "PO edit lifecycle" test.
"""
import os
import sys

import pytest
from fastapi import HTTPException

BACKEND = os.path.join(os.path.dirname(__file__), "..", "..")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from config import _raw_db, set_org_context  # noqa: E402
from tests.business_regression._fixtures import (  # noqa: E402
    seed_product, seed_supplier,
)
from tests.phase2b._fixtures import seed_admin_pin, _uid  # noqa: E402
from routes.purchase_orders import (  # noqa: E402
    create_purchase_order, pay_purchase_order,
)
from routes.branch_transfers import create_transfer  # noqa: E402
from routes.internal_invoices import pay_internal_invoice  # noqa: E402


# ─────────────────────────────────────────────────────────────────────
# Local helpers — kept file-local; promote to _fixtures.py later when a
# second BR file needs them.
# ─────────────────────────────────────────────────────────────────────
def _terms_po_payload(branch_id, vendor_name, product_id, *, qty, unit_price):
    return {
        "branch_id": branch_id,
        "vendor": vendor_name,
        "po_type": "terms",
        "purchase_date": "",
        "items": [{
            "product_id": product_id, "product_name": "BR4 Product",
            "unit": "pc",
            "quantity": qty, "unit_price": unit_price,
            "discount_type": "amount", "discount_value": 0,
        }],
    }


def _xfer_payload(from_branch_id, to_branch_id, product_id, *, qty, capital, retail):
    return {
        "from_branch_id": from_branch_id, "to_branch_id": to_branch_id,
        "items": [{
            "product_id": product_id, "product_name": "BR4 Xfer",
            "sku": f"SKU-{product_id[-6:]}", "unit": "pc",
            "qty": qty, "branch_capital": capital,
            "transfer_capital": capital, "branch_retail": retail,
        }],
        "min_margin": 20, "notes": "br4 paydown",
    }


async def _wallet(branch_id, wtype):
    return await _raw_db.fund_wallets.find_one(
        {"branch_id": branch_id, "type": wtype, "active": True},
        {"_id": 0},
    ) or {}


async def _wallet_balance(branch_id, wtype):
    w = await _wallet(branch_id, wtype)
    return float(w.get("balance", 0.0))


async def _seed_bank_wallet(org_id, branch_id, *, balance=0.0):
    """Branch transfer fixtures don't seed bank wallets; internal-invoice
    /pay needs them on BOTH sides. Upserts: if a bank wallet already
    exists for this branch (e.g. seeded by an earlier test in the same
    module-scoped tenant), its balance is overwritten so each test
    starts from a deterministic balance instead of stacking duplicate
    rows that `find_one` would resolve unpredictably."""
    existing = await _raw_db.fund_wallets.find_one(
        {"branch_id": branch_id, "type": "bank"}, {"_id": 0, "id": 1}
    )
    if existing:
        await _raw_db.fund_wallets.update_one(
            {"id": existing["id"]},
            {"$set": {"balance": float(balance), "active": True}},
        )
        return existing["id"]
    wid = _uid("w-bank")
    await _raw_db.fund_wallets.insert_one({
        "id": wid, "organization_id": org_id, "branch_id": branch_id,
        "type": "bank", "balance": balance, "active": True,
    })
    return wid


async def _set_cashier_balance(branch_id, balance):
    """Bump the cashier wallet to a known starting balance so the PO /pay
    deduction has funds to draw against."""
    await _raw_db.fund_wallets.update_one(
        {"branch_id": branch_id, "type": "cashier"},
        {"$set": {"balance": float(balance)}},
    )


async def _po_row(po_id):
    return await _raw_db.purchase_orders.find_one(
        {"id": po_id}, {"_id": 0}
    ) or {}


async def _payable_for_po(po_id):
    return await _raw_db.payables.find_one(
        {"po_id": po_id}, {"_id": 0}
    ) or {}


async def _internal_invoice(inv_id):
    return await _raw_db.internal_invoices.find_one(
        {"id": inv_id}, {"_id": 0}
    ) or {}


# ─────────────────────────────────────────────────────────────────────
# SCENARIO A — PO terms paydown: partial → full → already-paid guard
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_br4a_po_terms_paydown_partial_then_full(tenant, record_result):
    org_id     = tenant["org_id"]
    main       = tenant["branches"]["main"]
    owner_user = tenant["users"]["owner"]

    # Seed system admin_pin so PO /pay's policy_key="pay_po_standard" passes.
    set_org_context(org_id)
    admin_pin = await seed_admin_pin("918273")

    # Fund the cashier wallet with enough to cover the full payment.
    GRAND_TOTAL = 20 * 60   # 1200
    CASHIER_START = 2000.0
    await _set_cashier_balance(main, CASHIER_START)

    supplier_id = await seed_supplier(org_id, name="BR4 Vendor")
    vendor_name = (await _raw_db.suppliers.find_one(
        {"id": supplier_id}, {"_id": 0, "name": 1}
    ))["name"]
    product_id = await seed_product(
        org_id, main, name="BR4 Product", price=120, stock=0, cost=60,
    )

    res = await create_purchase_order(
        _terms_po_payload(main, vendor_name, product_id, qty=20, unit_price=60),
        user=owner_user,
    )
    po_id = res["id"]
    payable = await _payable_for_po(po_id)
    assert payable["balance"] == GRAND_TOTAL, "br4a setup: payable not at full"

    # ── PAYMENT 1: partial — ₱500 of ₱1200
    PART_1 = 500.0
    pay1 = await pay_purchase_order(
        po_id,
        {"fund_source": "cashier", "amount": PART_1, "pin": admin_pin,
         "method": "Cash"},
        user=owner_user,
    )
    po_after1 = await _po_row(po_id)
    pay_after1 = await _payable_for_po(po_id)
    cash_after1 = await _wallet_balance(main, "cashier")

    base_ev_A = {
        "org_id": org_id, "branch_id": main,
        "supplier_id": supplier_id, "vendor": vendor_name,
        "po_id": po_id, "po_number": po_after1.get("po_number"),
        "payable_id": pay_after1.get("id"),
        "grand_total": GRAND_TOTAL, "cashier_start": CASHIER_START,
    }

    record_result(
        scenario="br4.a_po_terms_paydown",
        step="partial_payment_payable_status_partial",
        expected={"paid": PART_1, "balance": GRAND_TOTAL - PART_1,
                  "status": "partial"},
        actual={"paid": float(pay_after1["paid"]),
                "balance": float(pay_after1["balance"]),
                "status": pay_after1["status"]},
        evidence={**base_ev_A, "payment_amount": PART_1},
    )
    record_result(
        scenario="br4.a_po_terms_paydown",
        step="partial_payment_po_mirrors_payable",
        expected={"amount_paid": PART_1, "balance": GRAND_TOTAL - PART_1,
                  "payment_status": "partial"},
        actual={"amount_paid": float(po_after1["amount_paid"]),
                "balance": float(po_after1["balance"]),
                "payment_status": po_after1["payment_status"]},
        evidence=base_ev_A,
    )
    record_result(
        scenario="br4.a_po_terms_paydown",
        step="partial_payment_cashier_decremented",
        expected={"cashier_balance": CASHIER_START - PART_1,
                  "delta": -PART_1},
        actual={"cashier_balance": cash_after1,
                "delta": cash_after1 - CASHIER_START},
        evidence={**base_ev_A,
                  "route_response": {
                      "new_balance": pay1.get("new_balance"),
                      "payment_status": pay1.get("payment_status"),
                  }},
    )

    assert float(pay_after1["paid"]) == PART_1
    assert float(pay_after1["balance"]) == GRAND_TOTAL - PART_1
    assert pay_after1["status"] == "partial"
    assert float(po_after1["amount_paid"]) == PART_1
    assert float(po_after1["balance"]) == GRAND_TOTAL - PART_1
    assert po_after1["payment_status"] == "partial"
    assert cash_after1 == CASHIER_START - PART_1
    assert len(po_after1.get("payment_history", [])) == 1

    # ── PAYMENT 2: remainder — ₱700 settles the rest
    PART_2 = GRAND_TOTAL - PART_1   # 700
    await pay_purchase_order(
        po_id,
        {"fund_source": "cashier", "amount": PART_2, "pin": admin_pin,
         "method": "Cash"},
        user=owner_user,
    )
    po_after2 = await _po_row(po_id)
    pay_after2 = await _payable_for_po(po_id)
    cash_after2 = await _wallet_balance(main, "cashier")

    record_result(
        scenario="br4.a_po_terms_paydown",
        step="remainder_payment_payable_status_paid",
        expected={"paid": GRAND_TOTAL, "balance": 0.0, "status": "paid"},
        actual={"paid": float(pay_after2["paid"]),
                "balance": float(pay_after2["balance"]),
                "status": pay_after2["status"]},
        evidence={**base_ev_A, "payment_amount": PART_2},
    )
    record_result(
        scenario="br4.a_po_terms_paydown",
        step="remainder_payment_po_status_paid",
        expected={"amount_paid": GRAND_TOTAL, "balance": 0.0,
                  "payment_status": "paid"},
        actual={"amount_paid": float(po_after2["amount_paid"]),
                "balance": float(po_after2["balance"]),
                "payment_status": po_after2["payment_status"]},
        evidence=base_ev_A,
    )
    record_result(
        scenario="br4.a_po_terms_paydown",
        step="remainder_payment_cashier_balance_at_total_minus_grand",
        expected={"cashier_balance": CASHIER_START - GRAND_TOTAL},
        actual={"cashier_balance": cash_after2},
        evidence=base_ev_A,
    )

    assert float(pay_after2["paid"]) == GRAND_TOTAL
    assert float(pay_after2["balance"]) == 0.0
    assert pay_after2["status"] == "paid"
    assert float(po_after2["amount_paid"]) == GRAND_TOTAL
    assert float(po_after2["balance"]) == 0.0
    assert po_after2["payment_status"] == "paid"
    assert cash_after2 == CASHIER_START - GRAND_TOTAL
    assert len(po_after2.get("payment_history", [])) == 2

    # ── GUARD: third payment must be rejected
    third_status = None
    third_detail = None
    try:
        await pay_purchase_order(
            po_id,
            {"fund_source": "cashier", "amount": 1.0, "pin": admin_pin,
             "method": "Cash"},
            user=owner_user,
        )
    except HTTPException as e:
        third_status = e.status_code
        third_detail = e.detail

    record_result(
        scenario="br4.a_po_terms_paydown",
        step="already_paid_guard_rejects_extra_payment",
        expected={"status_code": 400, "rejected": True},
        actual={"status_code": third_status,
                "rejected": third_status == 400},
        evidence={**base_ev_A, "third_call_detail": third_detail},
    )
    assert third_status == 400, (
        f"br4a: post-fully-paid PO must reject further /pay calls "
        f"(got {third_status}, detail={third_detail!r})"
    )

    # Cashier balance unchanged after the rejected call.
    assert await _wallet_balance(main, "cashier") == CASHIER_START - GRAND_TOTAL


# ─────────────────────────────────────────────────────────────────────
# SCENARIO B — Internal invoice paydown: full pay + status + wallet flow
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_br4b_internal_invoice_paydown_full(tenant, record_result):
    org_id     = tenant["org_id"]
    main       = tenant["branches"]["main"]
    b2         = tenant["branches"]["b2"]
    owner_user = tenant["users"]["owner"]
    set_org_context(org_id)

    # Seed bank wallets on BOTH branches (the BR base fixture omits them).
    BUYER_BANK_START    = 200.0   # to_branch (b2) pays
    SUPPLIER_BANK_START = 0.0     # from_branch (main) receives
    await _seed_bank_wallet(org_id, b2,   balance=BUYER_BANK_START)
    await _seed_bank_wallet(org_id, main, balance=SUPPLIER_BANK_START)

    # Seed a product at main and post a transfer → auto-creates internal invoice.
    product_id = await seed_product(
        org_id, main, name="BR4 Xfer Product",
        price=80, stock=5, cost=50,
    )
    XFER_QTY  = 1
    XFER_CAP  = 50.0
    XFER_TOTAL = XFER_QTY * XFER_CAP   # 50

    xfer = await create_transfer(
        _xfer_payload(main, b2, product_id, qty=XFER_QTY,
                      capital=XFER_CAP, retail=80.0),
        user=owner_user,
    )
    inv = await _raw_db.internal_invoices.find_one(
        {"transfer_id": xfer["id"]}, {"_id": 0}
    )
    assert inv is not None, "br4b setup: internal_invoice not auto-created"
    assert inv.get("organization_id") == org_id, (
        "br4b setup: B-1 regression — invoice missing organization_id"
    )

    # ── PAY in full
    pay_res = await pay_internal_invoice(
        inv["id"], {"note": "br4 paydown"}, user=owner_user,
    )
    inv_after = await _internal_invoice(inv["id"])
    buyer_bank_after    = await _wallet_balance(b2,   "bank")
    supplier_bank_after = await _wallet_balance(main, "bank")
    wallet_moves = await _raw_db.wallet_movements.find(
        {"reference": {"$regex": f".*{inv['invoice_number']}.*"}},
        {"_id": 0},
    ).to_list(None)
    cash_out = [m for m in wallet_moves if m["type"] == "cash_out"]
    cash_in  = [m for m in wallet_moves if m["type"] == "cash_in"]

    base_ev_B = {
        "org_id": org_id, "from_branch_id": main, "to_branch_id": b2,
        "transfer_id": xfer["id"],
        "internal_invoice_id": inv["id"],
        "invoice_number": inv.get("invoice_number"),
        "grand_total": XFER_TOTAL,
        "buyer_bank_start": BUYER_BANK_START,
        "supplier_bank_start": SUPPLIER_BANK_START,
    }

    record_result(
        scenario="br4.b_internal_invoice_paydown",
        step="invoice_status_paid",
        expected={"status": "paid", "payment_status": "paid",
                  "paid_amount": XFER_TOTAL},
        actual={"status": inv_after.get("status"),
                "payment_status": inv_after.get("payment_status"),
                "paid_amount": float(inv_after.get("paid_amount") or 0)},
        evidence={**base_ev_B,
                  "route_response": {
                      "amount": pay_res.get("amount"),
                      "buyer_bank_balance": pay_res.get("buyer_bank_balance"),
                      "supplier_bank_balance": pay_res.get("supplier_bank_balance"),
                  }},
    )
    record_result(
        scenario="br4.b_internal_invoice_paydown",
        step="buyer_bank_decremented",
        expected={"buyer_bank": BUYER_BANK_START - XFER_TOTAL,
                  "delta": -XFER_TOTAL},
        actual={"buyer_bank": buyer_bank_after,
                "delta": buyer_bank_after - BUYER_BANK_START},
        evidence=base_ev_B,
    )
    record_result(
        scenario="br4.b_internal_invoice_paydown",
        step="supplier_bank_credited",
        expected={"supplier_bank": SUPPLIER_BANK_START + XFER_TOTAL,
                  "delta": +XFER_TOTAL},
        actual={"supplier_bank": supplier_bank_after,
                "delta": supplier_bank_after - SUPPLIER_BANK_START},
        evidence=base_ev_B,
    )
    record_result(
        scenario="br4.b_internal_invoice_paydown",
        step="wallet_movements_two_rows_cash_out_in",
        expected={"cash_out_count": 1, "cash_out_amount": -XFER_TOTAL,
                  "cash_in_count":  1, "cash_in_amount":  +XFER_TOTAL},
        actual={
            "cash_out_count": len(cash_out),
            "cash_out_amount": float(cash_out[0]["amount"]) if cash_out else None,
            "cash_in_count":  len(cash_in),
            "cash_in_amount":  float(cash_in[0]["amount"]) if cash_in else None,
        },
        evidence={**base_ev_B,
                  "wallet_movement_ids": [m["id"] for m in wallet_moves]},
    )

    assert inv_after["status"] == "paid"
    assert inv_after["payment_status"] == "paid"
    assert float(inv_after["paid_amount"]) == XFER_TOTAL
    assert inv_after.get("paid_at"), "br4b: paid_at not stamped"
    assert buyer_bank_after    == BUYER_BANK_START - XFER_TOTAL
    assert supplier_bank_after == SUPPLIER_BANK_START + XFER_TOTAL
    assert len(cash_out) == 1
    assert float(cash_out[0]["amount"]) == -XFER_TOTAL
    assert cash_out[0]["branch_id"] == b2
    assert len(cash_in) == 1
    assert float(cash_in[0]["amount"]) == XFER_TOTAL
    assert cash_in[0]["branch_id"] == main

    # ── GUARD: second /pay must be rejected.
    second_status = None
    try:
        await pay_internal_invoice(inv["id"], {}, user=owner_user)
    except HTTPException as e:
        second_status = e.status_code
    record_result(
        scenario="br4.b_internal_invoice_paydown",
        step="already_paid_guard_rejects_second_pay",
        expected={"status_code": 400, "rejected": True},
        actual={"status_code": second_status,
                "rejected": second_status == 400},
        evidence=base_ev_B,
    )
    assert second_status == 400
    # Bank balances unchanged after the rejected call.
    assert await _wallet_balance(b2,   "bank") == BUYER_BANK_START - XFER_TOTAL
    assert await _wallet_balance(main, "bank") == SUPPLIER_BANK_START + XFER_TOTAL


# ─────────────────────────────────────────────────────────────────────
# SCENARIO C — Invalid-payment guards (overpay, unknown id, insufficient bank)
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_br4c_payment_guards(tenant, record_result):
    org_id     = tenant["org_id"]
    main       = tenant["branches"]["main"]
    b2         = tenant["branches"]["b2"]
    owner_user = tenant["users"]["owner"]
    set_org_context(org_id)

    admin_pin = await seed_admin_pin("918273")
    await _set_cashier_balance(main, 1000.0)

    # ── C-1: PO overpayment — ₱2000 attempted against a ₱600 payable
    sid = await seed_supplier(org_id, name="BR4 Guard Vendor")
    vendor = (await _raw_db.suppliers.find_one(
        {"id": sid}, {"_id": 0, "name": 1}
    ))["name"]
    pid = await seed_product(
        org_id, main, name="BR4 Guard Product",
        price=100, stock=0, cost=50,
    )
    po = await create_purchase_order(
        _terms_po_payload(main, vendor, pid, qty=10, unit_price=60),
        user=owner_user,
    )
    over_status = None
    over_detail = None
    try:
        await pay_purchase_order(
            po["id"],
            {"fund_source": "cashier", "amount": 2000.0,
             "pin": admin_pin, "method": "Cash"},
            user=owner_user,
        )
    except HTTPException as e:
        over_status = e.status_code
        over_detail = e.detail

    po_unchanged = await _po_row(po["id"])
    payable_unchanged = await _payable_for_po(po["id"])
    record_result(
        scenario="br4.c_payment_guards",
        step="po_overpayment_rejected_400",
        expected={"status_code": 400, "rejected": True,
                  "po_balance_unchanged": 600.0,
                  "payable_balance_unchanged": 600.0},
        actual={"status_code": over_status,
                "rejected": over_status == 400,
                "po_balance_unchanged": float(po_unchanged["balance"]),
                "payable_balance_unchanged": float(payable_unchanged["balance"])},
        evidence={"po_id": po["id"], "detail": over_detail},
    )
    assert over_status == 400
    assert float(po_unchanged["balance"]) == 600.0
    assert float(payable_unchanged["balance"]) == 600.0
    assert po_unchanged["payment_status"] == "unpaid"

    # ── C-2: PO /pay against unknown id → 404
    bogus_status = None
    try:
        await pay_purchase_order(
            "br4-nonexistent-po-zzz",
            {"fund_source": "cashier", "amount": 10, "pin": admin_pin},
            user=owner_user,
        )
    except HTTPException as e:
        bogus_status = e.status_code
    record_result(
        scenario="br4.c_payment_guards",
        step="po_pay_unknown_id_404",
        expected={"status_code": 404},
        actual={"status_code": bogus_status},
        evidence={"attempted_po_id": "br4-nonexistent-po-zzz"},
    )
    assert bogus_status == 404

    # ── C-3: internal_invoice /pay against unknown id → 404
    inv_bogus_status = None
    try:
        await pay_internal_invoice(
            "br4-nonexistent-inv-zzz", {}, user=owner_user,
        )
    except HTTPException as e:
        inv_bogus_status = e.status_code
    record_result(
        scenario="br4.c_payment_guards",
        step="internal_invoice_pay_unknown_id_404",
        expected={"status_code": 404},
        actual={"status_code": inv_bogus_status},
        evidence={"attempted_invoice_id": "br4-nonexistent-inv-zzz"},
    )
    assert inv_bogus_status == 404

    # ── C-4: internal_invoice /pay with insufficient buyer-bank balance
    #         → 400 with `insufficient_funds` structured detail.
    # Buyer-bank deliberately under-funded; supplier-bank just needs to exist.
    BUYER_BANK_LOW    = 5.0
    SUPPLIER_BANK_LOW = 0.0
    await _seed_bank_wallet(org_id, b2,   balance=BUYER_BANK_LOW)
    await _seed_bank_wallet(org_id, main, balance=SUPPLIER_BANK_LOW)

    xfer_pid = await seed_product(
        org_id, main, name="BR4 Guard Xfer",
        price=80, stock=2, cost=50,
    )
    xfer = await create_transfer(
        _xfer_payload(main, b2, xfer_pid, qty=1, capital=50.0, retail=80.0),
        user=owner_user,
    )
    inv = await _raw_db.internal_invoices.find_one(
        {"transfer_id": xfer["id"]}, {"_id": 0}
    )
    short_status = None
    short_detail = None
    try:
        await pay_internal_invoice(inv["id"], {}, user=owner_user)
    except HTTPException as e:
        short_status = e.status_code
        short_detail = e.detail

    buyer_after  = await _wallet_balance(b2,   "bank")
    seller_after = await _wallet_balance(main, "bank")
    inv_after    = await _internal_invoice(inv["id"])
    record_result(
        scenario="br4.c_payment_guards",
        step="internal_invoice_insufficient_bank_400",
        expected={"status_code": 400,
                  "detail_type": "insufficient_funds",
                  "buyer_bank_unchanged": BUYER_BANK_LOW,
                  "supplier_bank_unchanged": SUPPLIER_BANK_LOW,
                  "invoice_status_unchanged": "prepared"},
        actual={
            "status_code": short_status,
            "detail_type": (short_detail.get("type")
                            if isinstance(short_detail, dict) else None),
            "buyer_bank_unchanged": buyer_after,
            "supplier_bank_unchanged": seller_after,
            "invoice_status_unchanged": inv_after.get("status"),
        },
        evidence={"internal_invoice_id": inv["id"],
                  "invoice_number": inv["invoice_number"],
                  "short_detail": short_detail},
    )
    assert short_status == 400
    assert isinstance(short_detail, dict) and short_detail.get("type") == "insufficient_funds"
    assert buyer_after  == BUYER_BANK_LOW
    assert seller_after == SUPPLIER_BANK_LOW
    assert inv_after["status"] == "prepared"


# TODO (later business_regression prompts):
#   * br4.d — PO bank/digital payment path (TOTP policy, journal_entries).
#   * br4.e — internal_invoice auto-pay scheduler tick (per-tenant context).
#   * br4.f — PO /adjust-payment (PO edit changed grand_total — different
#       semantics from /pay; belongs to a future PO-edit lifecycle test).

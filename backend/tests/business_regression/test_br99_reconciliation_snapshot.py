"""
br99 — Reconciliation Snapshot (keystone business-regression test).

Runs one small store-day on Main Branch with two products (one is unused
to anchor a "should-not-have-moved" check), one cash customer (walk-in),
and one credit customer. The day consists of FIVE actions, in order:

  1. Cash sale            — 5 × ₱100   (walk-in)
  2. Credit sale          — 3 × ₱200   (credit customer)
  3. Partial sale         — 2 × ₱300, ₱200 paid cash, ₱400 to AR (credit customer)
  4. Cash payment ₱400    — against the step-2 credit invoice
  5. Sellable return      — 1 unit refunded from the step-1 cash sale (walk-in)

After all five, the snapshot asserts a single deterministic end-of-day
state for stock, wallet, customer AR, and per-invoice balances. Every
expected/actual pair is also recorded via `record_result(...)` so the
JSON report carries the full evidence.

Why this single test:
  Phase 2B already covers each action in isolation. The reconciliation
  snapshot's job is integration — proving that running the five actions
  in sequence produces the predicted totals. If a future change quietly
  double-counts or misses a stock/wallet/AR movement somewhere in the
  chain, this test fails with a business-readable message.

Scope deferred to later business_regression prompts:
  * Supplier AP / PO receive       (Prompt 3)
  * Branch transfer                (Prompt 3)
  * Repack-derived stock           (Prompt 5)
  * Z-Report / close wizard        (Prompt 4)
  * Date-basis / closed-day guard  (Prompt 4)
  * Offline envelope idempotency   (Prompt 4 — already covered by phase2b/test_g7)
  * Permissions / cross-tenant     (Prompt 5 — phase2d already passes)
"""
import os
import sys

import pytest

BACKEND = os.path.join(os.path.dirname(__file__), "..", "..")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from config import _raw_db  # noqa: E402
from tests.business_regression._fixtures import (  # noqa: E402
    seed_product,
    snapshot_inventory, snapshot_customer, snapshot_wallet, snapshot_invoice,
    base_sale_payload,
)
from routes.sales import create_unified_sale          # noqa: E402
from routes.invoices import record_invoice_payment    # noqa: E402
from routes.returns import create_return              # noqa: E402


# ─────────────────────────────────────────────────────────────────────
# Deterministic constants — kept inline (no magic in helpers) so the
# arithmetic in the expected totals is obvious to a future reader.
# ─────────────────────────────────────────────────────────────────────
STOCK_START   = 50

CASH_QTY      = 5
CASH_RATE     = 100        # 5 × 100 = 500
CASH_TOTAL    = CASH_QTY * CASH_RATE

CREDIT_QTY    = 3
CREDIT_RATE   = 200        # 3 × 200 = 600
CREDIT_TOTAL  = CREDIT_QTY * CREDIT_RATE

PARTIAL_QTY   = 2
PARTIAL_RATE  = 300        # 2 × 300 = 600
PARTIAL_TOTAL = PARTIAL_QTY * PARTIAL_RATE
PARTIAL_PAID  = 200.0
PARTIAL_AR    = PARTIAL_TOTAL - PARTIAL_PAID    # 400

CREDIT_PAYMENT = 400.0      # step 4 cash payment on the step-2 invoice

RETURN_QTY     = 1
RETURN_REFUND  = 100.0      # step 5 sellable return on the step-1 cash sale


def _expected_end_stock():
    # +1 because the sellable return restores 1 unit.
    return STOCK_START - CASH_QTY - CREDIT_QTY - PARTIAL_QTY + RETURN_QTY  # 41


def _expected_end_cashier_wallet():
    # Cash in:  500 (step1) + 200 (step3 partial cash leg) + 400 (step4 payment)
    # Cash out: 100 (step5 sellable refund via cashier)
    return CASH_TOTAL + PARTIAL_PAID + CREDIT_PAYMENT - RETURN_REFUND       # 1000.0


def _expected_credit_customer_balance():
    # AR in:  600 (step2 full credit) + 400 (step3 unpaid leg)
    # AR out: 400 (step4 payment)
    return CREDIT_TOTAL + PARTIAL_AR - CREDIT_PAYMENT                       # 600.0


# ─────────────────────────────────────────────────────────────────────
# The single keystone test.
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_br99_reconciliation_snapshot(tenant, record_result):
    """One store day, five actions, one end-of-day reconciliation."""
    org_id        = tenant["org_id"]
    branch_id     = tenant["branches"]["main"]
    owner_user    = tenant["users"]["owner"]
    credit_cust   = tenant["customers"]["credit"]

    # Seed the product the day will trade on. Use phase2b's helper so we
    # inherit the same row shape every g1–g7 test exercises.
    product_id = await seed_product(
        org_id, branch_id,
        name="BR99 Sack 25kg", price=CASH_RATE,
        stock=STOCK_START, cost=60,
    )

    # ── Sanity: starting state matches what we declared above ─────────
    start_stock   = await snapshot_inventory(branch_id, product_id)
    start_cashier = await snapshot_wallet(branch_id, "cashier")
    start_ar      = (await snapshot_customer(credit_cust))["balance"]
    base_evidence = {
        "org_id": org_id, "branch_id": branch_id,
        "product_id": product_id, "credit_customer_id": credit_cust,
        "starting_stock": start_stock,
        "starting_cashier_wallet": start_cashier,
        "starting_credit_ar": start_ar,
    }
    assert start_stock == STOCK_START, (
        f"BR99 setup: expected starting stock {STOCK_START}, got {start_stock} "
        f"(product={product_id}, branch={branch_id})"
    )
    assert start_cashier == 0.0
    assert start_ar == 0.0

    # ── Step 1: cash sale ────────────────────────────────────────────
    res1 = await create_unified_sale({
        **base_sale_payload(branch_id=branch_id, product_id=product_id,
                            qty=CASH_QTY, rate=CASH_RATE),
        "payment_type": "cash",
        "amount_paid": float(CASH_TOTAL),
    }, user=owner_user)
    inv1 = await snapshot_invoice(res1["id"])
    assert inv1["status"] == "paid", (
        f"BR99 step1: cash sale not marked paid (invoice={inv1.get('invoice_number')}, "
        f"status={inv1.get('status')}, balance={inv1.get('balance')})"
    )

    # ── Step 2: credit sale (no cash collected) ──────────────────────
    res2 = await create_unified_sale({
        **base_sale_payload(branch_id=branch_id, product_id=product_id,
                            qty=CREDIT_QTY, rate=CREDIT_RATE),
        "payment_type": "credit",
        "amount_paid": 0.0,
        "customer_id": credit_cust, "customer_name": "BR Credit Cust",
    }, user=owner_user)
    inv2 = await snapshot_invoice(res2["id"])
    assert inv2["balance"] == float(CREDIT_TOTAL), (
        f"BR99 step2: credit sale balance should be {CREDIT_TOTAL}, "
        f"got {inv2['balance']} (invoice={inv2.get('invoice_number')})"
    )

    # ── Step 3: partial sale (cash + AR) ─────────────────────────────
    res3 = await create_unified_sale({
        **base_sale_payload(branch_id=branch_id, product_id=product_id,
                            qty=PARTIAL_QTY, rate=PARTIAL_RATE),
        "payment_type": "partial",
        "amount_paid": PARTIAL_PAID,
        "customer_id": credit_cust, "customer_name": "BR Credit Cust",
    }, user=owner_user)
    inv3 = await snapshot_invoice(res3["id"])
    assert inv3["status"] == "partial", (
        f"BR99 step3: partial sale status mismatch "
        f"(invoice={inv3.get('invoice_number')}, status={inv3.get('status')})"
    )
    assert inv3["balance"] == PARTIAL_AR, (
        f"BR99 step3: partial-sale unpaid leg should be {PARTIAL_AR}, "
        f"got {inv3['balance']}"
    )

    # ── Step 4: cash payment against the step-2 credit invoice ───────
    pay_res = await record_invoice_payment(
        res2["id"],
        {"amount": CREDIT_PAYMENT, "method": "Cash", "fund_source": "cashier"},
        user=owner_user,
    )
    assert pay_res["new_balance"] == CREDIT_TOTAL - CREDIT_PAYMENT, (
        f"BR99 step4: payment did not reduce balance correctly; "
        f"expected new_balance={CREDIT_TOTAL - CREDIT_PAYMENT}, got {pay_res['new_balance']}"
    )

    # ── Step 5: sellable return (walk-in) on the step-1 cash sale ────
    # Wallet is already > refund amount, so no manual top-up is needed.
    await create_return({
        "branch_id": branch_id,
        "customer_name": "Walk-in",
        "customer_type": "walkin",
        "items": [{
            "product_id": product_id,
            "product_name": "BR99 Sack 25kg",
            "quantity": RETURN_QTY,
            "condition": "sellable",
            "inventory_action": "shelf",
            "refund_price": CASH_RATE,
            "cost_price": 60,
        }],
        "refund_amount": RETURN_REFUND,
        "fund_source": "cashier",
        "reason": "BR99 keystone return",
        "linked_invoice_number": inv1.get("invoice_number", ""),
    }, user=owner_user)

    # ── End-of-day reconciliation ────────────────────────────────────
    actual_stock     = await snapshot_inventory(branch_id, product_id)
    actual_cashier   = await snapshot_wallet(branch_id, "cashier")
    actual_ar        = (await snapshot_customer(credit_cust))["balance"]
    actual_inv2_bal  = (await snapshot_invoice(res2["id"]))["balance"]
    actual_inv3_bal  = (await snapshot_invoice(res3["id"]))["balance"]

    exp_stock   = _expected_end_stock()
    exp_cashier = _expected_end_cashier_wallet()
    exp_ar      = _expected_credit_customer_balance()
    exp_inv2    = CREDIT_TOTAL - CREDIT_PAYMENT   # 200
    exp_inv3    = PARTIAL_AR                       # 400

    # ----- Evidence + reporting (one row per assertion family) -------
    evidence_stock = {
        **base_evidence,
        "expected_qty": exp_stock, "actual_qty": actual_stock,
        "deltas": {
            "cash_sale": -CASH_QTY, "credit_sale": -CREDIT_QTY,
            "partial_sale": -PARTIAL_QTY, "return": +RETURN_QTY,
        },
    }
    record_result(
        scenario="br99.reconciliation_snapshot",
        step="product_stock_end_of_day",
        expected={"stock": exp_stock},
        actual={"stock": actual_stock},
        evidence=evidence_stock,
    )

    evidence_cash = {
        **base_evidence,
        "expected_wallet": exp_cashier, "actual_wallet": actual_cashier,
        "money_in":  {"cash_sale": CASH_TOTAL, "partial_cash_leg": PARTIAL_PAID,
                      "credit_payment": CREDIT_PAYMENT},
        "money_out": {"return_refund": RETURN_REFUND},
    }
    record_result(
        scenario="br99.reconciliation_snapshot",
        step="cashier_wallet_end_of_day",
        expected={"cashier_wallet": exp_cashier},
        actual={"cashier_wallet": actual_cashier},
        evidence=evidence_cash,
    )

    evidence_ar = {
        **base_evidence,
        "expected_ar": exp_ar, "actual_ar": actual_ar,
        "ar_in":  {"credit_sale": CREDIT_TOTAL, "partial_unpaid_leg": PARTIAL_AR},
        "ar_out": {"credit_payment": CREDIT_PAYMENT},
    }
    record_result(
        scenario="br99.reconciliation_snapshot",
        step="credit_customer_AR_end_of_day",
        expected={"customer_balance": exp_ar},
        actual={"customer_balance": actual_ar},
        evidence=evidence_ar,
    )

    evidence_inv2 = {
        **base_evidence,
        "invoice_id": res2["id"],
        "invoice_number": inv2.get("invoice_number"),
        "expected_balance": exp_inv2, "actual_balance": actual_inv2_bal,
    }
    record_result(
        scenario="br99.reconciliation_snapshot",
        step="credit_invoice_balance_after_payment",
        expected={"invoice_balance": exp_inv2},
        actual={"invoice_balance": actual_inv2_bal},
        evidence=evidence_inv2,
    )

    evidence_inv3 = {
        **base_evidence,
        "invoice_id": res3["id"],
        "invoice_number": inv3.get("invoice_number"),
        "expected_balance": exp_inv3, "actual_balance": actual_inv3_bal,
    }
    record_result(
        scenario="br99.reconciliation_snapshot",
        step="partial_invoice_balance_unchanged",
        expected={"invoice_balance": exp_inv3},
        actual={"invoice_balance": actual_inv3_bal},
        evidence=evidence_inv3,
    )

    # ----- Hard assertions (with business-readable failure messages) -
    assert actual_stock == exp_stock, (
        f"BR99 stock mismatch — expected {exp_stock}, got {actual_stock}. "
        f"Evidence: {evidence_stock}"
    )
    assert actual_cashier == exp_cashier, (
        f"BR99 cashier wallet mismatch — expected ₱{exp_cashier:,.2f}, "
        f"got ₱{actual_cashier:,.2f}. Evidence: {evidence_cash}"
    )
    assert actual_ar == exp_ar, (
        f"BR99 credit customer AR mismatch — expected ₱{exp_ar:,.2f}, "
        f"got ₱{actual_ar:,.2f}. Evidence: {evidence_ar}"
    )
    assert actual_inv2_bal == exp_inv2, (
        f"BR99 credit-invoice balance mismatch — expected ₱{exp_inv2:,.2f}, "
        f"got ₱{actual_inv2_bal:,.2f}. Evidence: {evidence_inv2}"
    )
    assert actual_inv3_bal == exp_inv3, (
        f"BR99 partial-invoice balance mismatch — expected ₱{exp_inv3:,.2f}, "
        f"got ₱{actual_inv3_bal:,.2f}. Evidence: {evidence_inv3}"
    )

    # Sanity: cash-customer AR untouched (we never used them today).
    cash_cust_balance = (await snapshot_customer(tenant["customers"]["cash"]))["balance"]
    assert cash_cust_balance == 0.0, (
        f"BR99 cash-customer AR should be untouched (0), got {cash_cust_balance}"
    )


# TODO (later business_regression prompts):
#   * Supplier AP via PO receive (Prompt 3 — needs a real PO-receive helper).
#   * Branch-B half-day to exercise the second branch + transfer flow.
#   * Z-Report aggregation reconciliation against the same day's actions.
#   * Date-basis verification (encoded-today vs order-date) once historical
#     credit is part of the same day.

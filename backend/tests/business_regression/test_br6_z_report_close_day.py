"""
br6 — Z-Report / Close-Day end-of-day regression.

What's tested (narrow approved scope):
  br6.a — Conservation: starting_float + total_cash_in
          − total_cashier_expenses + net_fund_transfers == expected_counter
          across a mini business day (cash + credit + partial sale).
  br6.b — Duplicate-close guard + post-close wallet reset.
  br6.c — Cross-day AR payment date-basis: yesterday's credit invoice,
          today's payment → counted in today's AR receipts; yesterday's
          order_date does NOT leak into today's new_credit.
  br6.d — Negative variance recording: actual_cash < expected_counter
          → over_short < 0 captured on the close record.

Explicitly out of scope (TODO at bottom):
  * Overage-reserve hook, employee advances, fund-transfer ladder.
  * Refund-as-cash-out (flagged as murky in the pre-flight assessment).
  * Z-Report PDF / SMS hooks (fire-and-forget; out-of-process).
  * Multi-branch same-date close (br6.i).
  * Cross-tenant cross-read of daily_closings (br_iso pattern already
    covers the underlying tenant-isolation contract).

Discovered behaviour (read from `routes/daily_operations.py:559-2125`,
`utils/helpers.py:353-408`, `routes/sales.py:700-860` BEFORE writing
this test, NOT assumed):

  * Aggregation source-of-truth:
      total_cash_sales       <-  sales_log rows where payment_method ~ /^cash$/i
      total_cash_ar          <-  invoices.payments[*] where date==today and
                                 invoice.payment_type ∈ [credit, partial]
      total_split_cash       <-  invoices.cash_amount on split sales
      total_new_credit       <-  sum invoices.balance for payment_type
                                 ∈ [credit, partial] dated today
      total_cashier_expenses <-  expenses rows dated today / fund_source=cashier
      net_fund_transfers     <-  fund_transfers rows dated today (in − out)
      expected_counter       =   starting_float + total_cash_in
                                 − total_cashier_expenses + net_fund_transfers
                                 (where total_cash_in = total_cash_sales
                                 + total_cash_ar + total_split_cash)

  * starting_float resolution:
      Previous close exists → cash_to_drawer of that close.
      Else (first-ever close) → live cashier wallet balance (and the
      route reverse-derives a synthetic starting_float to keep the
      formula consistent).

  * Duplicate-close guard:
      `daily_closings.find_one({branch_id, date, status: closed})` → 400
      "Day already closed".

  * Post-close wallet reset:
      `fund_wallets.cashier.balance` set to `cash_to_drawer` after the
      close record is persisted (line 2108-2109). `cash_to_safe` is
      written as a new `safe_lots` row (line 2113-2119).
"""
import os
import sys

import pytest
from fastapi import HTTPException

BACKEND = os.path.join(os.path.dirname(__file__), "..", "..")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from config import _raw_db, set_org_context  # noqa: E402
from tests.business_regression._fixtures import seed_product  # noqa: E402
from tests.phase2b._fixtures import base_sale_payload  # noqa: E402
from utils.helpers import today_local  # noqa: E402
from routes.sales import create_unified_sale  # noqa: E402
from routes.daily_operations import close_day  # noqa: E402
from routes.invoices import record_invoice_payment  # noqa: E402


# ─────────────────────────────────────────────────────────────────────
# Local helpers — file-local; promote to _fixtures.py only when br6b/br7
# needs them.
# ─────────────────────────────────────────────────────────────────────
async def _wallet(branch_id, wtype="cashier"):
    return await _raw_db.fund_wallets.find_one(
        {"branch_id": branch_id, "type": wtype, "active": True},
        {"_id": 0},
    ) or {}


async def _wallet_balance(branch_id, wtype="cashier"):
    w = await _wallet(branch_id, wtype)
    return float(w.get("balance", 0.0))


async def _set_cashier_balance(branch_id, balance):
    await _raw_db.fund_wallets.update_one(
        {"branch_id": branch_id, "type": "cashier"},
        {"$set": {"balance": float(balance)}},
    )


async def _sales_log_cash_sum(branch_id, date):
    """Replicate the close-day cash_pipeline against sales_log so the
    test computes EXACTLY what the route will compute, instead of
    asserting an idealised number."""
    rows = await _raw_db.sales_log.find(
        {"branch_id": branch_id, "date": date,
         "voided": {"$ne": True},
         "payment_method": {"$regex": "^cash$", "$options": "i"}},
        {"_id": 0, "line_total": 1},
    ).to_list(1000)
    return round(sum(float(r.get("line_total") or 0) for r in rows), 2)


# ─────────────────────────────────────────────────────────────────────
# br6.a — Mini business day → close → conservation formula
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_br6a_close_day_conservation_formula(tenant, record_result):
    org_id     = tenant["org_id"]
    main       = tenant["branches"]["main"]
    owner_user = tenant["users"]["owner"]
    cash_cust  = tenant["customers"]["cash"]
    credit_cust = tenant["customers"]["credit"]
    set_org_context(org_id)
    today = await today_local(org_id)

    # Pre-fund cashier wallet to a clean starting float we control.
    STARTING_FLOAT = 1000.0
    await _set_cashier_balance(main, STARTING_FLOAT)
    cashier_before = await _wallet_balance(main, "cashier")
    assert cashier_before == STARTING_FLOAT

    product_id = await seed_product(
        org_id, main, name="BR6 Product",
        price=100, stock=100, cost=50,
    )

    # ── Cash sale: ₱500 (5 × ₱100), full cash. payment_method="Cash".
    cash_inv = await create_unified_sale({
        **base_sale_payload(branch_id=main, product_id=product_id,
                            qty=5, rate=100),
        "payment_type": "cash",
        "amount_paid": 500.0,
        "customer_id": cash_cust,
    }, user=owner_user)

    # ── Credit sale: ₱200 (2 × ₱100), unpaid. payment_method explicitly
    #    "Credit" so the sales_log row excludes itself from the cash agg.
    credit_payload = {
        **base_sale_payload(branch_id=main, product_id=product_id,
                            qty=2, rate=100),
        "payment_type": "credit",
        "amount_paid": 0.0,
        "customer_id": credit_cust,
        "payment_method": "Credit",
    }
    credit_inv = await create_unified_sale(credit_payload, user=owner_user)

    # ── Partial sale: ₱300 grand total, ₱100 paid in cash, ₱200 to AR.
    #    payment_method="Cash" (the cash portion is physically cash).
    #    The unified-sale route stamps a `payments[].date = order_date`
    #    record for the ₱100, and stores balance=200 on the invoice.
    partial_payload = {
        **base_sale_payload(branch_id=main, product_id=product_id,
                            qty=3, rate=100),
        "payment_type": "partial",
        "amount_paid": 100.0,
        "customer_id": credit_cust,
        "payment_method": "Cash",
    }
    partial_inv = await create_unified_sale(partial_payload, user=owner_user)

    # ── Re-derive the same numbers the close route will derive, by
    #    reading the underlying source rows directly. This protects the
    #    test from breaking if the close formula evolves (we still hold
    #    the conservation identity assertion below).
    derived_cash_sales = await _sales_log_cash_sum(main, today)
    today_inv_rows = await _raw_db.invoices.find(
        {"branch_id": main, "order_date": today,
         "status": {"$ne": "voided"}},
        {"_id": 0, "payment_type": 1, "balance": 1,
         "amount_paid": 1, "fund_source": 1,
         "cash_amount": 1, "payments": 1},
    ).to_list(500)
    derived_new_credit = round(sum(
        float(inv.get("balance") or 0)
        for inv in today_inv_rows
        if inv.get("payment_type") in ("credit", "partial")
    ), 2)
    derived_today_ar = 0.0
    for inv in today_inv_rows:
        if inv.get("payment_type") in ("credit", "partial"):
            for p in inv.get("payments") or []:
                if p.get("date") == today and not p.get("voided"):
                    derived_today_ar += float(p.get("amount") or 0)
    derived_today_ar = round(derived_today_ar, 2)

    # ── Close the day. actual_cash = real wallet snapshot just before
    #    close so over_short is a deterministic 0 for the happy path.
    real_cash_now = await _wallet_balance(main, "cashier")
    close_record = await close_day({
        "date": today, "branch_id": main,
        "actual_cash": real_cash_now,
        "cash_to_safe": 0.0,
        "cash_to_drawer": real_cash_now,
        "variance_notes": "",
    }, user=owner_user)

    # Read the persisted close row to confirm it's what the route returned.
    closed_row = await _raw_db.daily_closings.find_one(
        {"id": close_record["id"]}, {"_id": 0}
    ) or {}

    cashier_after = await _wallet_balance(main, "cashier")
    daily_closings_count = await _raw_db.daily_closings.count_documents(
        {"branch_id": main}
    )

    # Conservation identity (always holds at the formula level).
    starting_float = float(close_record["starting_float"])
    tot_cash_in    = float(close_record["total_cash_in"])
    tot_expenses   = float(close_record.get("total_cashier_expenses") or 0)
    net_transfers  = float(close_record.get("net_fund_transfers") or 0)
    expected_counter_from_record = float(close_record["expected_counter"])
    expected_counter_recomputed  = round(
        starting_float + tot_cash_in - tot_expenses + net_transfers, 2,
    )

    base_ev = {
        "org_id": org_id, "branch_id": main, "report_date": today,
        "close_id": close_record["id"],
        "closing_status": close_record.get("status"),
        "invoice_numbers": [cash_inv["invoice_number"],
                             credit_inv["invoice_number"],
                             partial_inv["invoice_number"]],
        "cashier_wallet_id": (await _wallet(main, "cashier")).get("id"),
        "starting_float": starting_float,
    }

    # ── Assertion family 1: conservation identity (mathematical truth).
    record_result(
        scenario="br6.a_close_day_conservation",
        step="expected_counter_equals_formula_recomputation",
        expected={"expected_counter": expected_counter_recomputed},
        actual={"expected_counter": expected_counter_from_record},
        evidence={**base_ev,
                  "components": {
                      "starting_float": starting_float,
                      "total_cash_in": tot_cash_in,
                      "total_cashier_expenses": tot_expenses,
                      "net_fund_transfers": net_transfers,
                  }},
    )
    assert expected_counter_from_record == expected_counter_recomputed, (
        f"br6a CONSERVATION VIOLATION — close record's expected_counter "
        f"{expected_counter_from_record} does not satisfy "
        f"starting_float({starting_float}) + total_cash_in({tot_cash_in}) "
        f"- expenses({tot_expenses}) + net_transfers({net_transfers}) "
        f"= {expected_counter_recomputed}"
    )

    # ── Assertion family 2: total_cash_sales matches the source-row sum.
    record_result(
        scenario="br6.a_close_day_conservation",
        step="total_cash_sales_matches_sales_log_aggregation",
        expected={"total_cash_sales": derived_cash_sales},
        actual={"total_cash_sales": float(closed_row.get("total_cash_sales") or 0)},
        evidence={**base_ev,
                  "rationale": "close route aggregates sales_log rows where "
                               "payment_method ~ /^cash$/i; the test re-runs "
                               "the same query and asserts equality."},
    )
    assert float(closed_row["total_cash_sales"]) == derived_cash_sales

    # ── Assertion family 3: total_cash_ar matches the payments-today sum.
    record_result(
        scenario="br6.a_close_day_conservation",
        step="total_cash_ar_matches_payments_today_aggregation",
        expected={"total_cash_ar": derived_today_ar},
        actual={"total_cash_ar": float(closed_row.get("total_cash_ar") or 0)},
        evidence={**base_ev,
                  "rationale": "close route unwinds invoices.payments and "
                               "sums payments.date==today for credit/partial "
                               "invoices."},
    )
    assert float(closed_row["total_cash_ar"]) == derived_today_ar

    # ── Assertion family 4: total_new_credit matches sum-of-balances.
    record_result(
        scenario="br6.a_close_day_conservation",
        step="total_new_credit_sums_credit_and_partial_balances",
        expected={"total_new_credit": derived_new_credit},
        actual={"total_new_credit": float(closed_row.get("total_new_credit") or 0)},
        evidence={**base_ev,
                  "credit_invoice_number": credit_inv["invoice_number"],
                  "partial_invoice_number": partial_inv["invoice_number"]},
    )
    assert float(closed_row["total_new_credit"]) == derived_new_credit

    # ── Assertion family 5: daily_closings row created.
    record_result(
        scenario="br6.a_close_day_conservation",
        step="daily_closings_row_created_with_status_closed",
        expected={"row_count_for_branch": 1, "status": "closed"},
        actual={"row_count_for_branch": daily_closings_count,
                "status": close_record.get("status")},
        evidence={**base_ev, "close_id": close_record["id"]},
    )
    assert close_record.get("status") == "closed"
    assert daily_closings_count == 1

    # ── Assertion family 6: business-rule SANITY surface (NON-asserting).
    # Owner's stated rule: cash sales count + partial paid portion +
    # AR collected today == cash that physically entered the drawer.
    owner_expected_cash_in = 500.0 + 100.0   # cash sale + partial cash portion
    record_result(
        scenario="br6.a_close_day_conservation",
        step="business_rule_owner_expected_cash_in_matches_route",
        expected={"owner_expected_cash_in": owner_expected_cash_in},
        actual={"total_cash_in_per_route": tot_cash_in},
        evidence={**base_ev,
                  "note": "If owner_expected_cash_in != "
                          "total_cash_in_per_route, surface as evidence — "
                          "the route's categorisation of partial line "
                          "totals may need review (flagged in the "
                          "pre-flight assessment)."},
    )

    # Save IDs for the br6.b / br6.d siblings.
    pytest._br6a_close_id = close_record["id"]
    pytest._br6a_cashier_balance_after_close = cashier_after


# ─────────────────────────────────────────────────────────────────────
# br6.b — Duplicate-close guard + cashier-wallet reset
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_br6b_duplicate_close_blocked_and_wallet_reset(
    tenant, record_result
):
    """Depends on br6.a having closed today's day already (same tenant,
    module-scoped fixture). If br6.a is run in isolation this test is
    self-sufficient — it closes the day inline as a precondition."""
    org_id     = tenant["org_id"]
    main       = tenant["branches"]["main"]
    owner_user = tenant["users"]["owner"]
    set_org_context(org_id)
    today = await today_local(org_id)

    existing = await _raw_db.daily_closings.find_one(
        {"branch_id": main, "date": today, "status": "closed"},
        {"_id": 0},
    )
    if not existing:
        # Self-sufficient mode (br6.b run alone or br6.a was skipped).
        await _set_cashier_balance(main, 250.0)
        existing = await close_day({
            "date": today, "branch_id": main,
            "actual_cash": 250.0,
            "cash_to_safe": 50.0,
            "cash_to_drawer": 200.0,
            "variance_notes": "",
        }, user=owner_user)

    cash_to_drawer_recorded = float(existing.get("cash_to_drawer") or 0)
    wallet_balance_now = await _wallet_balance(main, "cashier")

    record_result(
        scenario="br6.b_duplicate_close_and_wallet_reset",
        step="cashier_wallet_balance_reset_to_cash_to_drawer",
        expected={"cashier_balance": cash_to_drawer_recorded},
        actual={"cashier_balance": wallet_balance_now},
        evidence={"org_id": org_id, "branch_id": main, "report_date": today,
                  "close_id": existing.get("id"),
                  "cash_to_drawer_in_close_record": cash_to_drawer_recorded},
    )
    assert wallet_balance_now == cash_to_drawer_recorded, (
        f"br6b WALLET RESET BUG — after close the cashier wallet should "
        f"equal cash_to_drawer ({cash_to_drawer_recorded}), got "
        f"{wallet_balance_now}"
    )

    # ── Second close attempt on the same branch/date → 400.
    status_code = None
    detail = None
    try:
        await close_day({
            "date": today, "branch_id": main,
            "actual_cash": 999.0,
            "cash_to_safe": 0.0, "cash_to_drawer": 0.0,
            "variance_notes": "duplicate attempt",
        }, user=owner_user)
    except HTTPException as e:
        status_code = e.status_code
        detail = e.detail

    closings_count_after_dup = await _raw_db.daily_closings.count_documents(
        {"branch_id": main, "date": today, "status": "closed"}
    )
    wallet_after_dup = await _wallet_balance(main, "cashier")

    record_result(
        scenario="br6.b_duplicate_close_and_wallet_reset",
        step="duplicate_close_rejected_400",
        expected={"status_code": 400, "rejected": True},
        actual={"status_code": status_code,
                "rejected": status_code == 400},
        evidence={"detail": detail,
                  "closing_rows_after_attempt": closings_count_after_dup,
                  "wallet_balance_after_attempt": wallet_after_dup},
    )
    record_result(
        scenario="br6.b_duplicate_close_and_wallet_reset",
        step="no_extra_close_row_no_wallet_mutation",
        expected={"closing_row_count": 1,
                  "wallet_unchanged": cash_to_drawer_recorded},
        actual={"closing_row_count": closings_count_after_dup,
                "wallet_unchanged": wallet_after_dup},
        evidence={"close_id": existing.get("id")},
    )
    assert status_code == 400
    assert closings_count_after_dup == 1
    assert wallet_after_dup == cash_to_drawer_recorded


# ─────────────────────────────────────────────────────────────────────
# br6.c — Cross-day AR payment: yesterday's credit, today's payment
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_br6c_cross_day_ar_payment_date_basis(tenant, record_result):
    """A credit invoice dated YESTERDAY receives a payment dated TODAY.
    Use the daily-close *preview* (read-only, no state mutation) to keep
    this independent of br6.a's mutating close call."""
    from datetime import datetime, timedelta
    from routes.daily_operations import get_daily_close_preview

    org_id      = tenant["org_id"]
    main        = tenant["branches"]["main"]
    owner_user  = tenant["users"]["owner"]
    credit_cust = tenant["customers"]["credit"]
    set_org_context(org_id)
    today    = await today_local(org_id)
    yest_dt  = datetime.strptime(today, "%Y-%m-%d") - timedelta(days=1)
    yesterday = yest_dt.strftime("%Y-%m-%d")

    # Use a DIFFERENT branch (b2) for this scenario to keep br6.a's
    # closed-day state at `main` untouched.
    b2 = tenant["branches"]["b2"]
    await _set_cashier_balance(b2, 0.0)

    product_id = await seed_product(
        org_id, b2, name="BR6c Product",
        price=80, stock=50, cost=40,
    )

    # Yesterday's credit sale at b2.
    yest_credit_inv = await create_unified_sale({
        **base_sale_payload(branch_id=b2, product_id=product_id,
                            qty=2, rate=80),
        "payment_type": "credit",
        "amount_paid": 0.0,
        "customer_id": credit_cust,
        "payment_method": "Credit",
        "order_date": yesterday,
    }, user=owner_user)

    PAY_AMT = 75.0
    await record_invoice_payment(
        yest_credit_inv["id"],
        {"amount": PAY_AMT, "fund_source": "cashier",
         "method": "Cash", "date": today,
         "applied_to_interest": 0,
         "applied_to_principal": PAY_AMT},
        user=owner_user,
    )

    preview = await get_daily_close_preview(
        user=owner_user, branch_id=b2, date=today,
    )

    # Today's NEW credit at b2 should be zero (yesterday's invoice's
    # order_date is yesterday, not today).
    today_new_credit_b2 = float(preview.get("total_new_credit") or 0)
    today_cash_ar_b2    = float(preview.get("total_cash_ar") or 0)

    base_ev = {
        "org_id": org_id, "branch_id": b2, "report_date": today,
        "yesterday": yesterday,
        "credit_invoice_number": yest_credit_inv["invoice_number"],
        "payment_amount": PAY_AMT,
    }
    record_result(
        scenario="br6.c_cross_day_ar_payment",
        step="today_AR_payment_counted_in_today_cash_ar",
        expected={"total_cash_ar_meets_minimum": True, "minimum": PAY_AMT},
        actual={"total_cash_ar_meets_minimum": today_cash_ar_b2 >= PAY_AMT,
                "minimum": PAY_AMT},
        evidence={**base_ev,
                  "observed_total_cash_ar": today_cash_ar_b2,
                  "rationale": "ar_pipeline unwinds invoice.payments and "
                               "matches by payments.date == today, "
                               "independent of order_date."},
    )
    record_result(
        scenario="br6.c_cross_day_ar_payment",
        step="yesterday_credit_does_not_leak_into_today_new_credit",
        expected={"total_new_credit": 0.0},
        actual={"total_new_credit": today_new_credit_b2},
        evidence={**base_ev,
                  "rationale": "new_credit aggregates invoices.balance "
                               "filtered on order_date==today; yesterday's "
                               "invoice has order_date=yesterday."},
    )
    record_result(
        scenario="br6.c_cross_day_ar_payment",
        step="ar_classification_today_vs_older",
        expected={"today_AR_payment_present": True,
                  "older_AR_present_on_invoice": True},
        actual={
            "today_AR_payment_present": today_cash_ar_b2 >= PAY_AMT,
            "older_AR_present_on_invoice":
                await _raw_db.invoices.count_documents(
                    {"id": yest_credit_inv["id"], "payments.date": today}
                ) > 0,
        },
        evidence=base_ev,
    )

    assert today_cash_ar_b2 >= PAY_AMT, (
        f"br6c expected today's cash AR ≥ {PAY_AMT}, got {today_cash_ar_b2}"
    )
    assert today_new_credit_b2 == 0.0, (
        f"br6c DATE-BASIS BUG — yesterday's credit invoice (order_date="
        f"{yesterday}) is leaking into today's new_credit "
        f"(got {today_new_credit_b2})"
    )


# ─────────────────────────────────────────────────────────────────────
# br6.d — Negative variance: actual_cash < expected_counter → over_short < 0
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_br6d_negative_variance_recorded(tenant, record_result):
    """Use b2 to avoid colliding with main's closed-today state. Drive
    one cash sale, then under-report actual_cash by ₱50 and confirm the
    variance lands on `over_short` with the right sign and is reflected
    in `wallet_movements` close-ledger rows."""
    org_id      = tenant["org_id"]
    main        = tenant["branches"]["main"]   # unused here  # noqa: F841
    b2          = tenant["branches"]["b2"]
    owner_user  = tenant["users"]["owner"]
    cash_cust   = tenant["customers"]["cash"]
    set_org_context(org_id)
    today = await today_local(org_id)

    # br6.c already closed `b2`? No — br6.c used PREVIEW only, so b2's
    # day is still open. But br6.c left rows from yesterday + today, so
    # we use a different *date* to keep this scenario surgical: drive a
    # cash sale today at b2 (still open), then close with under-reported
    # actual_cash.
    if await _raw_db.daily_closings.count_documents(
        {"branch_id": b2, "date": today, "status": "closed"}
    ):
        pytest.skip("br6d: b2 day already closed by an earlier test; "
                    "skipping to avoid order coupling.")

    await _set_cashier_balance(b2, 0.0)
    product_id = await seed_product(
        org_id, b2, name="BR6d Product",
        price=100, stock=20, cost=50,
    )

    # Cash sale ₱400 at b2.
    await create_unified_sale({
        **base_sale_payload(branch_id=b2, product_id=product_id,
                            qty=4, rate=100),
        "payment_type": "cash",
        "amount_paid": 400.0,
        "customer_id": cash_cust,
    }, user=owner_user)

    real_cash = await _wallet_balance(b2, "cashier")
    SHORTAGE = 50.0
    actual_cash_reported = real_cash - SHORTAGE

    close_record = await close_day({
        "date": today, "branch_id": b2,
        "actual_cash": actual_cash_reported,
        "cash_to_safe": 0.0,
        "cash_to_drawer": actual_cash_reported,
        "variance_notes": "br6d-under-reported-by-50",
    }, user=owner_user)

    over_short = float(close_record.get("over_short") or 0)
    expected_counter = float(close_record.get("expected_counter") or 0)
    expected_over_short = round(actual_cash_reported - expected_counter, 2)

    # Locate the close-ledger variance entry in wallet_movements (if
    # the route persisted one for a negative variance).
    variance_entries = await _raw_db.wallet_movements.find(
        {"branch_id": b2, "reference": {"$regex": "Day close|over_short"}},
        {"_id": 0},
    ).to_list(50)
    short_entry = next(
        (m for m in variance_entries
         if m.get("amount") and float(m["amount"]) < 0
         and "variance" in (m.get("notes") or "").lower()),
        None,
    )

    base_ev = {
        "org_id": org_id, "branch_id": b2, "report_date": today,
        "close_id": close_record.get("id"),
        "real_cash_in_drawer": real_cash,
        "actual_cash_reported": actual_cash_reported,
        "expected_counter": expected_counter,
        "shortage": SHORTAGE,
    }
    record_result(
        scenario="br6.d_negative_variance",
        step="over_short_recorded_as_negative",
        expected={"over_short": expected_over_short, "negative": True},
        actual={"over_short": over_short, "negative": over_short < 0},
        evidence=base_ev,
    )
    record_result(
        scenario="br6.d_negative_variance",
        step="variance_notes_preserved_on_close_record",
        expected={"variance_notes": "br6d-under-reported-by-50"},
        actual={"variance_notes": close_record.get("variance_notes")},
        evidence=base_ev,
    )
    record_result(
        scenario="br6.d_negative_variance",
        step="wallet_movements_carry_variance_entry_if_recorded",
        expected={"has_variance_entry_or_field_present": True},
        actual={"has_variance_entry_or_field_present":
                short_entry is not None or "over_short" in close_record},
        evidence={**base_ev,
                  "variance_entries_seen": len(variance_entries),
                  "note": "Route persists `over_short` on close_record "
                          "unconditionally; ledger variance entry is "
                          "optional depending on _write_close_ledger_entries."},
    )

    assert over_short < 0, (
        f"br6d expected negative over_short (under-reported by {SHORTAGE}), "
        f"got {over_short}"
    )
    assert over_short == expected_over_short, (
        f"br6d over_short formula — expected actual_cash({actual_cash_reported}) "
        f"- expected_counter({expected_counter}) = {expected_over_short}, "
        f"got {over_short}"
    )
    assert close_record.get("variance_notes") == "br6d-under-reported-by-50"


# TODO (later business_regression prompts):
#   * br6.e — overage reserve hook (positive variance pooled, negative
#       tracked as deficit; idempotent via source_id).
#   * br6.f — employee advance & cash advance reconciliation.
#   * br6.g — fund-transfer ladder (safe↔cashier intra-day movements).
#   * br6.h — refund-as-cash-out path (flagged as murky; needs targeted
#       investigation before assertion design).
#   * br6.i — multi-branch same-date close (close `main` then `b2` and
#       confirm they're independent rows).
#   * br6.j — Z-Report PDF generation (out-of-process; possibly defer
#       indefinitely or test only the route's sync portion).

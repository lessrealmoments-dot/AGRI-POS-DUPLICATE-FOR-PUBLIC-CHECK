"""
br_close_wizard_batch — pin the Feb 2026 group/batch closing-wizard fix.

Bug discovered when user batch-closed ~10 days of ₱1.5M cash sales and
the wizard reported `expected_counter = 0`. Three independent defects
fed the same wrong answer:

  1. `prev_close` lookup required EXACT `first_date - 1` match.
     Group-close exists precisely BECAUSE the user skipped close-days,
     so the lookup almost always missed → starting_float fell through
     to wallet.balance (which had drifted to 0 on the user's setup).
  2. `expected_counter` was computed but never returned from
     `/daily-close-preview/batch`.
  3. Batch POST's `cash_sales_agg` was missing the
     `partial_grand_total: {$exists: False}` filter that the preview
     and single-day paths used → partial-sale line totals
     double-counted (once via cash_sales, once via cash_ar).

The single-day wizard worked because its `prev_close` lookup used
`date < target_date sort desc` — accepting ANY most-recent closed day.
These tests pin both versions to identical anchor semantics.
"""
import pytest
from datetime import datetime, timedelta

from config import _raw_db
from routes.daily_operations import (
    batch_close_preview,
    get_daily_close_preview,
)


async def _seed_close(*, org_id, branch_id, date, cash_to_drawer):
    await _raw_db.daily_closings.update_one(
        {"branch_id": branch_id, "date": date},
        {"$set": {
            "organization_id": org_id,
            "branch_id":      branch_id,
            "date":           date,
            "status":         "closed",
            "cash_to_drawer": float(cash_to_drawer),
            "starting_float": 0.0,
            "expected_counter": 0.0,
            "actual_cash":    0.0,
            "over_short":     0.0,
        }},
        upsert=True,
    )


async def _seed_cash_sale(*, org_id, branch_id, date, amount, with_partial=False, invoice_id=None):
    """Insert a sales_log row + a paid-cash invoice for the same date."""
    import uuid
    iid = invoice_id or f"inv-bc-{uuid.uuid4().hex[:8]}"
    sl = {
        "organization_id": org_id,
        "branch_id":      branch_id,
        "date":           date,
        "invoice_id":     iid,
        "invoice_number": iid.upper(),
        "category":       "General",
        "product_name":   "X",
        "quantity":       1, "rate": amount,
        "line_total":     float(amount),
        "payment_method": "Cash",
        "voided":         False,
    }
    if with_partial:
        sl["partial_grand_total"] = float(amount)
    await _raw_db.sales_log.insert_one(sl)
    # Matching invoice (paid cash, on-the-day).
    await _raw_db.invoices.insert_one({
        "id":             iid,
        "organization_id": org_id,
        "branch_id":      branch_id,
        "order_date":     date,
        "invoice_date":   date,
        "payment_method": "Cash",
        "payment_type":   "cash",
        "fund_source":    "cash",
        "grand_total":    float(amount),
        "amount_paid":    float(amount),
        "balance":        0.0,
        "status":         "paid",
        "items":          [],
    })
    return iid


async def _seed_wallet(*, org_id, branch_id, balance):
    await _raw_db.fund_wallets.update_one(
        {"branch_id": branch_id, "type": "cashier"},
        {"$set": {"organization_id": org_id, "branch_id": branch_id, "type": "cashier",
                  "active": True, "balance": float(balance),
                  "name": "Cashier"}},
        upsert=True,
    )


# ═════════════════════════════════════════════════════════════════════
# Test 1 — Reproduces the user's "₱1.5M sales but expected = 0" report.
# Pre-fix: prev_close lookup required exact first_date - 1 → None,
# fallback to wallet.balance (= 0 in repro), short-circuit skipped the
# formula → expected_counter = 0.
# Post-fix: prev_close anchored to most-recent closed day (5 days back),
# formula applied → expected_counter ≈ 1,505,000.
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_bc_1_skipped_days_anchor_to_most_recent_close(tenant, record_result):
    branch_id = tenant["branches"]["main"]
    org_id = tenant["org_id"]

    # Anchor close 5 days before the batch window with ₱5,000 left in drawer.
    base = datetime(2026, 3, 15)
    anchor_date = (base - timedelta(days=6)).strftime("%Y-%m-%d")
    batch_dates = [(base - timedelta(days=4 - i)).strftime("%Y-%m-%d") for i in range(5)]
    await _seed_close(org_id=org_id, branch_id=branch_id, date=anchor_date, cash_to_drawer=5_000)

    # ₱1.5M of cash sales spread across batch dates.
    for i, d in enumerate(batch_dates):
        await _seed_cash_sale(org_id=org_id, branch_id=branch_id, date=d, amount=300_000)

    # Wallet has drifted to zero (manual reset / fresh provisioning).
    await _seed_wallet(org_id=org_id, branch_id=branch_id, balance=0)

    preview = await batch_close_preview(
        branch_id=branch_id, dates=",".join(batch_dates),
        user=tenant["users"]["owner"],
    )

    record_result(
        scenario="br_close_wizard.batch.1_skipped_days",
        step="anchor_to_most_recent_close_not_exact_day_before",
        expected={"starting_float": 5_000.0, "total_cash_sales": 1_500_000.0,
                  "expected_counter_min": 1_500_000.0, "has_prev_close": True},
        actual={"starting_float": preview["starting_float"],
                "total_cash_sales": preview["total_cash_sales"],
                "expected_counter": preview["expected_counter"],
                "has_prev_close":   preview["has_prev_close"]},
    )
    assert preview["has_prev_close"] is True
    assert preview["starting_float"] == 5_000.0
    assert preview["total_cash_sales"] == 1_500_000.0
    # 5,000 + 1,500,000 + 0 - 0 = 1,505,000.
    assert preview["expected_counter"] == 1_505_000.0


# ═════════════════════════════════════════════════════════════════════
# Test 2 — Batch preview parity with single-day preview when the batch
# is exactly one day. Both must return the same anchor / starting_float
# / expected_counter (regression guard against future formula drift).
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_bc_2_batch_one_day_matches_single_day_preview(tenant, record_result):
    branch_id = tenant["branches"]["main"]
    org_id = tenant["org_id"]

    anchor_date = "2026-04-09"
    target_date = "2026-04-10"
    await _seed_close(org_id=org_id, branch_id=branch_id, date=anchor_date, cash_to_drawer=2_000)
    await _seed_cash_sale(org_id=org_id, branch_id=branch_id, date=target_date, amount=12_345.67)
    await _seed_wallet(org_id=org_id, branch_id=branch_id, balance=99_999)  # noise, should be ignored

    single = await get_daily_close_preview(
        branch_id=branch_id, date=target_date, user=tenant["users"]["owner"],
    )
    batch = await batch_close_preview(
        branch_id=branch_id, dates=target_date, user=tenant["users"]["owner"],
    )

    record_result(
        scenario="br_close_wizard.batch.2_parity_single_day",
        step="batch_with_one_day_matches_single_day",
        expected={"starting_float_match": True,
                  "expected_counter_match": True,
                  "total_cash_sales_match": True},
        actual={"single_sf": single["starting_float"], "batch_sf": batch["starting_float"],
                "single_ec": single["expected_counter"], "batch_ec": batch["expected_counter"],
                "single_cs": single["total_cash_sales"], "batch_cs": batch["total_cash_sales"]},
    )
    assert batch["starting_float"]   == single["starting_float"]
    assert batch["expected_counter"] == single["expected_counter"]
    assert batch["total_cash_sales"] == single["total_cash_sales"]


# ═════════════════════════════════════════════════════════════════════
# Test 3 — Genuine first-close-ever: NO prior closing exists at all,
# `has_prev_close` is False, expected_counter falls back to wallet.balance
# (canonical "current cash on hand" with no anchor). Catches anyone
# accidentally removing the wallet-fallback branch in the future.
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_bc_3_first_close_ever_falls_back_to_wallet(tenant, record_result):
    branch_id = tenant["branches"]["main"]
    org_id = tenant["org_id"]

    # Hard clean: no closings exist for this branch.
    await _raw_db.daily_closings.delete_many({"branch_id": branch_id})
    target_dates = ["2026-05-20", "2026-05-21"]
    for d in target_dates:
        await _seed_cash_sale(org_id=org_id, branch_id=branch_id, date=d, amount=10_000)
    await _seed_wallet(org_id=org_id, branch_id=branch_id, balance=55_555)

    preview = await batch_close_preview(
        branch_id=branch_id, dates=",".join(target_dates),
        user=tenant["users"]["owner"],
    )

    record_result(
        scenario="br_close_wizard.batch.3_first_close_ever",
        step="wallet_balance_used_when_no_prior_close",
        expected={"has_prev_close": False, "expected_counter": 55_555.0,
                  "starting_float": 55_555.0},
        actual={"has_prev_close": preview["has_prev_close"],
                "expected_counter": preview["expected_counter"],
                "starting_float":   preview["starting_float"]},
    )
    assert preview["has_prev_close"] is False
    assert preview["expected_counter"] == 55_555.0
    assert preview["starting_float"]   == 55_555.0


# ═════════════════════════════════════════════════════════════════════
# Test 4 — Partial-sale double-count regression. A partial-paid invoice
# generates a sales_log row with `partial_grand_total` set; its cash
# portion is tracked separately via `total_cash_ar`. The cash_sales
# query must exclude these rows, else the same money gets counted twice.
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_bc_4_partial_sale_not_double_counted(tenant, record_result):
    branch_id = tenant["branches"]["main"]
    org_id = tenant["org_id"]
    await _raw_db.daily_closings.delete_many({"branch_id": branch_id})

    d = "2026-06-10"
    await _seed_close(org_id=org_id, branch_id=branch_id,
                      date="2026-06-09", cash_to_drawer=1_000)
    # Clean sale: 100 (cash only).
    await _seed_cash_sale(org_id=org_id, branch_id=branch_id, date=d, amount=100)
    # Partial-paid: 200 sticker price, marked as partial_grand_total.
    # Should NOT inflate total_cash_sales — its cash leg arrives via AR.
    await _seed_cash_sale(org_id=org_id, branch_id=branch_id, date=d, amount=200, with_partial=True)
    await _seed_wallet(org_id=org_id, branch_id=branch_id, balance=0)

    preview = await batch_close_preview(
        branch_id=branch_id, dates=d, user=tenant["users"]["owner"],
    )

    record_result(
        scenario="br_close_wizard.batch.4_partial_no_double_count",
        step="cash_sales_only_counts_full_paid",
        expected={"total_cash_sales": 100.0},
        actual={"total_cash_sales": preview["total_cash_sales"]},
    )
    assert preview["total_cash_sales"] == 100.0, (
        f"Partial-sale line wrongly counted as cash_sales: got "
        f"{preview['total_cash_sales']}, expected 100.0"
    )

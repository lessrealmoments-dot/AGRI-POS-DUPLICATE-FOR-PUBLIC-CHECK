"""
br_pos_dual_date — POS Terminal BIR dual-date semantics (2026-02).

Locks the invariant that `order_date` (record date — what the close-day
guard validates and what sales_log groups by) can differ from
`invoice_date` (sale date — the actual day goods left the shelf, what
prints on the customer's receipt).

Without this separation, an after-hours / post-close sale would either
be blocked entirely or would carry tomorrow's date on the receipt —
violating BIR's requirement that the receipt show the actual sale day.

Three scenarios:
  1. Today is OPEN  → invoice_date == order_date == today. Plain happy
     path; no bumping needed.
  2. Today is CLOSED → cashier rings up "carry-over" sale. invoice_date
     stays today (BIR), order_date = today + 1 day. Close-day guard
     accepts the sale (because it checks order_date which is tomorrow).
  3. Backend reports group by order_date, NOT invoice_date. The closed
     day's totals stay locked; the carry-over appears under tomorrow.

These tests POST via `create_unified_sale` directly (same path as the
terminal sync code) so they exercise the real route, not a mock.
"""
import os
import sys

import pytest
from fastapi import HTTPException

BACKEND = os.path.join(os.path.dirname(__file__), "..", "..")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from config import _raw_db                                        # noqa: E402
from tests.business_regression._fixtures import seed_product      # noqa: E402
from routes.sales import create_unified_sale                      # noqa: E402


def _sale_payload(branch_id, product_id, name, *,
                  order_date, invoice_date, qty=1, unit_price=100.0):
    """Minimal cash-sale payload mirroring what TerminalSales posts."""
    return {
        "branch_id": branch_id,
        "items": [{
            "product_id": product_id, "product_name": name,
            "qty": qty, "unit_price": unit_price, "discount_value": 0,
        }],
        "payment_type": "cash",
        "payment_method": "Cash",
        "amount_paid": qty * unit_price,
        "fund_source": "cash",
        "order_date": order_date,
        "invoice_date": invoice_date,
        "subtotal": qty * unit_price,
        "grand_total": qty * unit_price,
        "overall_discount": 0,
        "freight": 0,
        "terms": "COD",
        "customer_name": "Walk-in",
    }


def _next_day(ymd):
    """Pure-string YYYY-MM-DD + 1 day (mirrors lib/dateFormat.nextCalendarDay)."""
    from datetime import datetime, timedelta
    return (datetime.strptime(ymd, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")


async def _close_day(branch_id, ymd, org_id):
    """Insert a `daily_closings` row marking the day as closed."""
    await _raw_db.daily_closings.insert_one({
        "id": f"br-pos-close-{branch_id}-{ymd}",
        "organization_id": org_id,
        "branch_id": branch_id,
        "date": ymd,
        "status": "closed",
        "closed_at": "2026-02-13T12:00:00+00:00",
    })


# ─────────────────────────────────────────────────────────────────────
# 1. Open-day sale: invoice_date == order_date == today.
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_br_pos_dual_date_1_open_day_sale_uses_today_for_both(
    tenant, record_result
):
    """On an open day the cashier sends both dates as today. Invoice
    persists both fields equal to today; close-day guard not invoked."""
    org_id = tenant["org_id"]; main = tenant["branches"]["main"]
    owner = tenant["users"]["owner"]
    from utils import today_local
    today = await today_local(org_id)

    pid = await seed_product(org_id, main, name="br_pos_dual1 P",
                              stock=20, cost=60, price=100)
    payload = _sale_payload(main, pid, "br_pos_dual1 P",
                            order_date=today, invoice_date=today)
    result = await create_unified_sale(payload, user=owner)

    inv = await _raw_db.invoices.find_one({"id": result["id"]}, {"_id": 0})

    ev = {"org_id": org_id, "branch_id": main, "today": today,
          "invoice_id": result["id"],
          "invoice_number": inv.get("invoice_number"),
          "order_date": inv.get("order_date"),
          "invoice_date": inv.get("invoice_date")}
    record_result(
        scenario="br_pos_dual_date.1_open_day_sale_uses_today_for_both",
        step="order_date_today",
        expected={"date": today}, actual={"date": inv.get("order_date")},
        evidence=ev,
    )
    record_result(
        scenario="br_pos_dual_date.1_open_day_sale_uses_today_for_both",
        step="invoice_date_today",
        expected={"date": today}, actual={"date": inv.get("invoice_date")},
        evidence=ev,
    )
    assert inv["order_date"] == today
    assert inv["invoice_date"] == today


# ─────────────────────────────────────────────────────────────────────
# 2. Closed-day late ring-up: invoice_date == today, order_date == today+1.
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_br_pos_dual_date_2_closed_day_carryover_keeps_invoice_today(
    tenant, record_result
):
    """Cashier reopens AFTER closing to ring up a late walk-in. Backend
    accepts because order_date = today+1 stays within the forward-date
    cap (which expands to today+1 when today is closed). invoice_date
    sticks at today so the receipt prints the BIR-correct sale day."""
    org_id = tenant["org_id"]; main = tenant["branches"]["main"]
    owner = tenant["users"]["owner"]
    from utils import today_local
    today = await today_local(org_id)
    tomorrow = _next_day(today)

    await _close_day(main, today, org_id)

    pid = await seed_product(org_id, main, name="br_pos_dual2 P",
                              stock=20, cost=60, price=100)
    payload = _sale_payload(main, pid, "br_pos_dual2 P",
                            order_date=tomorrow, invoice_date=today)
    result = await create_unified_sale(payload, user=owner)

    inv = await _raw_db.invoices.find_one({"id": result["id"]}, {"_id": 0})

    ev = {"org_id": org_id, "branch_id": main, "today": today,
          "tomorrow": tomorrow, "invoice_id": result["id"],
          "invoice_number": inv.get("invoice_number"),
          "order_date": inv.get("order_date"),
          "invoice_date": inv.get("invoice_date")}
    record_result(
        scenario="br_pos_dual_date.2_closed_day_carryover_keeps_invoice_today",
        step="invoice_date_remains_today",
        expected={"date": today}, actual={"date": inv.get("invoice_date")},
        evidence=ev,
    )
    record_result(
        scenario="br_pos_dual_date.2_closed_day_carryover_keeps_invoice_today",
        step="order_date_bumped_to_tomorrow",
        expected={"date": tomorrow}, actual={"date": inv.get("order_date")},
        evidence=ev,
    )
    record_result(
        scenario="br_pos_dual_date.2_closed_day_carryover_keeps_invoice_today",
        step="order_date_differs_from_invoice_date",
        expected={"differ": True},
        actual={"differ": inv.get("order_date") != inv.get("invoice_date")},
        evidence=ev,
    )
    assert inv["invoice_date"] == today, (
        f"br_pos_dual_date.2: invoice_date must stay {today} (BIR sale "
        f"date), got {inv.get('invoice_date')!r}"
    )
    assert inv["order_date"] == tomorrow, (
        f"br_pos_dual_date.2: order_date must be {tomorrow} (next open "
        f"book day), got {inv.get('order_date')!r}"
    )


# ─────────────────────────────────────────────────────────────────────
# 3. Sale grouped/reported by order_date, NOT invoice_date.
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_br_pos_dual_date_3_reports_group_by_order_date(
    tenant, record_result
):
    """Verifies the carryover sale appears under tomorrow's count when
    querying sales_log by date — not today's. The closed Z-report stays
    locked; only the next open day's report sees this row."""
    org_id = tenant["org_id"]; main = tenant["branches"]["main"]
    owner = tenant["users"]["owner"]
    from utils import today_local
    today = await today_local(org_id)
    tomorrow = _next_day(today)

    # Close today (fresh branch isolation per test — but to be safe,
    # delete any pre-existing close row from prior test in the same run).
    await _raw_db.daily_closings.delete_many(
        {"branch_id": main, "date": today})
    await _close_day(main, today, org_id)

    pid = await seed_product(org_id, main, name="br_pos_dual3 P",
                              stock=20, cost=60, price=100)
    payload = _sale_payload(main, pid, "br_pos_dual3 P",
                            order_date=tomorrow, invoice_date=today)
    result = await create_unified_sale(payload, user=owner)

    # Confirm invoice is queryable by order_date=tomorrow but NOT today.
    # This is how every report (Z, X, daily totals) queries the invoices
    # collection — `find({"order_date": <ymd>})`.
    inv_today = await _raw_db.invoices.find_one(
        {"branch_id": main, "order_date": today, "id": result["id"]},
        {"_id": 0},
    )
    inv_tomorrow = await _raw_db.invoices.find_one(
        {"branch_id": main, "order_date": tomorrow, "id": result["id"]},
        {"_id": 0},
    )

    ev = {"org_id": org_id, "branch_id": main, "today": today,
          "tomorrow": tomorrow, "invoice_id": result["id"],
          "found_by_today_order_date": bool(inv_today),
          "found_by_tomorrow_order_date": bool(inv_tomorrow)}
    record_result(
        scenario="br_pos_dual_date.3_reports_group_by_order_date",
        step="not_visible_under_today",
        expected={"visible": False}, actual={"visible": bool(inv_today)},
        evidence=ev,
    )
    record_result(
        scenario="br_pos_dual_date.3_reports_group_by_order_date",
        step="visible_under_tomorrow",
        expected={"visible": True}, actual={"visible": bool(inv_tomorrow)},
        evidence=ev,
    )
    assert inv_today is None, (
        f"br_pos_dual_date.3: closed-day invoice leaked into today's "
        f"report query — would corrupt the closed Z-report"
    )
    assert inv_tomorrow is not None, (
        f"br_pos_dual_date.3: carryover sale missing from tomorrow's "
        f"order_date query — won't appear on next Z-report"
    )


# ─────────────────────────────────────────────────────────────────────
# 4. Forward-date guard rejects order_date > today+1 even when closed.
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_br_pos_dual_date_4_forward_date_cap_still_enforced(
    tenant, record_result
):
    """The forward-date cap is `today+1` (when today is closed) — NOT
    arbitrarily far. A terminal posting `order_date = today+2` must be
    rejected with 403 unless it carries the manager-PIN override flag.
    Locks against bypass via the dual-date payload."""
    org_id = tenant["org_id"]; main = tenant["branches"]["main"]
    owner = tenant["users"]["owner"]
    from utils import today_local
    today = await today_local(org_id)
    day_after_tomorrow = _next_day(_next_day(today))

    await _raw_db.daily_closings.delete_many(
        {"branch_id": main, "date": today})
    await _close_day(main, today, org_id)

    pid = await seed_product(org_id, main, name="br_pos_dual4 P",
                              stock=20, cost=60, price=100)
    payload = _sale_payload(main, pid, "br_pos_dual4 P",
                            order_date=day_after_tomorrow,
                            invoice_date=today)

    status_code = None
    detail = None
    try:
        await create_unified_sale(payload, user=owner)
    except HTTPException as e:
        status_code = e.status_code
        detail = e.detail

    ev = {"org_id": org_id, "branch_id": main, "today": today,
          "attempted_order_date": day_after_tomorrow,
          "error_status": status_code, "error_detail": detail}
    record_result(
        scenario="br_pos_dual_date.4_forward_date_cap_still_enforced",
        step="rejected_403",
        expected={"status_code": 403}, actual={"status_code": status_code},
        evidence=ev,
    )
    assert status_code == 403, (
        f"br_pos_dual_date.4: forward-date cap must still reject "
        f"order_date={day_after_tomorrow} (got {status_code}={detail!r})"
    )

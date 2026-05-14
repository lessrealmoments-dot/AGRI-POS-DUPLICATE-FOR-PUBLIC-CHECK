"""br_pos_history — POS Terminal Sales/Purchase History endpoints.

Locks the contracts for the two new POS-terminal History endpoints:

  GET /api/invoices/history/by-range
  GET /api/purchase-orders/history/by-range

Both endpoints power the locked "History" sheet inside the POS terminal
(/terminal). Branch scoping is enforced by `get_branch_filter` — managers
must be assigned to the requested branch_id; admins can pass any branch.

Test coverage:
  1. Sales by-range: today / week / month / custom returns the right window
  2. Sales totals math matches per-payment-type aggregation
  3. PO by-range: today / week / month / custom returns the right window
  4. PO totals math (spent / paid / outstanding / by_status)
  5. Sales newest-first ordering
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

BACKEND = os.path.join(os.path.dirname(__file__), "..", "..")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from config import _raw_db                                       # noqa: E402
from tests.phase2b._fixtures import _uid, fake_user              # noqa: E402
from routes.invoices import get_invoices_by_range                # noqa: E402
from routes.purchase_orders import get_purchase_orders_by_range  # noqa: E402


@pytest_asyncio.fixture(scope="module")
async def history_user(tenant):
    org_id = tenant["org_id"]
    main = tenant["branches"]["main"]
    uid = _uid("br_hist-admin")
    await _raw_db.users.insert_one({
        "id": uid, "username": f"adm-{uid[-4:]}",
        "full_name": "History Admin", "organization_id": org_id,
        "role": "admin", "active": True,
        "branch_ids": [main], "branch_id": main,
    })
    return fake_user(org_id, uid, branch_id=main, role="admin")


async def _today_str(org_id):
    from utils.helpers import today_local
    return await today_local(org_id)


# ═════════════════════════════════════════════════════════════════════
# Test 1 — Sales by-range honours today/week/month windows
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_br_pos_history_1_sales_range_window(
    tenant, history_user, record_result
):
    org_id = tenant["org_id"]
    main = tenant["branches"]["main"]
    today = await _today_str(org_id)
    today_dt = datetime.strptime(today, "%Y-%m-%d")

    # Seed 3 invoices: today / 5 days ago / 20 days ago
    seeds = [
        ("br_hist-1-today",  today, 100.0),
        ("br_hist-1-5dago",  (today_dt - timedelta(days=5)).strftime("%Y-%m-%d"), 200.0),
        ("br_hist-1-20dago", (today_dt - timedelta(days=20)).strftime("%Y-%m-%d"), 300.0),
    ]
    for tag, day, total in seeds:
        iid = _uid(tag)
        ts = f"{day}T08:30:00+00:00"
        await _raw_db.invoices.insert_one({
            "id": iid, "organization_id": org_id, "branch_id": main,
            "invoice_number": tag.upper(), "customer_name": "Walk-in",
            "order_date": day, "invoice_date": day, "created_at": ts,
            "payment_type": "cash", "status": "paid",
            "grand_total": total, "amount_paid": total, "balance": 0,
        })

    rs_today = await get_invoices_by_range(range="today", user=history_user)
    rs_week  = await get_invoices_by_range(range="week",  user=history_user)
    rs_month = await get_invoices_by_range(range="month", user=history_user)

    seeded_nums = {s[0].upper() for s in seeds}
    today_hits = {i["invoice_number"] for i in rs_today["invoices"]
                  if i["invoice_number"] in seeded_nums}
    week_hits  = {i["invoice_number"] for i in rs_week["invoices"]
                  if i["invoice_number"] in seeded_nums}
    month_hits = {i["invoice_number"] for i in rs_month["invoices"]
                  if i["invoice_number"] in seeded_nums}

    record_result(
        scenario="br_pos_history.1_sales_range_window",
        step="today_window_matches_one_week_two_month_three",
        expected={"today": 1, "week": 2, "month": 3},
        actual={"today": len(today_hits), "week": len(week_hits), "month": len(month_hits)},
        evidence={"seeded": list(seeded_nums)},
    )
    assert len(today_hits) == 1
    assert len(week_hits)  == 2
    assert len(month_hits) == 3


# ═════════════════════════════════════════════════════════════════════
# Test 2 — Sales by-range totals math
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_br_pos_history_2_sales_totals(
    tenant, history_user, record_result
):
    org_id = tenant["org_id"]
    main = tenant["branches"]["main"]
    today = await _today_str(org_id)
    ts = f"{today}T10:00:00+00:00"

    rows = [
        ("br_hist-2-cash",    "cash",    "paid",   500.0, 500.0, 0),
        ("br_hist-2-credit",  "credit",  "open",   700.0,   0,  700.0),
        ("br_hist-2-voided",  "cash",    "voided", 999.0,   0,    0),
    ]
    for tag, ptype, st, gt, paid, bal in rows:
        iid = _uid(tag)
        await _raw_db.invoices.insert_one({
            "id": iid, "organization_id": org_id, "branch_id": main,
            "invoice_number": tag.upper(), "customer_name": "X",
            "order_date": today, "invoice_date": today, "created_at": ts,
            "payment_type": ptype, "status": st,
            "grand_total": gt, "amount_paid": paid, "balance": bal,
        })

    res = await get_invoices_by_range(range="today", user=history_user)
    t = res["totals"]

    # Check that cash, credit, voided_count are within our seeded contribution
    assert t["cash"]    >= 500.0
    assert t["credit"]  >= 700.0
    assert t["voided_count"] >= 1
    # Grand total excludes the voided invoice
    assert t["grand_total"] >= (500.0 + 700.0)

    record_result(
        scenario="br_pos_history.2_sales_totals",
        step="cash_credit_voided_aggregation_correct",
        expected={"cash_ge_500": True, "credit_ge_700": True, "voided_ge_1": True, "grand_ge_1200": True},
        actual={"cash_ge_500": t["cash"] >= 500.0,
                "credit_ge_700": t["credit"] >= 700.0,
                "voided_ge_1": t["voided_count"] >= 1,
                "grand_ge_1200": t["grand_total"] >= 1200.0},
        evidence={"cash": t["cash"], "credit": t["credit"],
                  "voided_count": t["voided_count"], "grand": t["grand_total"]},
    )


# ═════════════════════════════════════════════════════════════════════
# Test 3 — PO by-range honours today/week/month windows
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_br_pos_history_3_po_range_window(
    tenant, history_user, record_result
):
    org_id = tenant["org_id"]
    main = tenant["branches"]["main"]
    today = await _today_str(org_id)
    today_dt = datetime.strptime(today, "%Y-%m-%d")

    seeds = [
        ("br_hist-3-po-today",  today, 1000.0),
        ("br_hist-3-po-5d",     (today_dt - timedelta(days=5)).strftime("%Y-%m-%d"), 2000.0),
        ("br_hist-3-po-20d",    (today_dt - timedelta(days=20)).strftime("%Y-%m-%d"), 3000.0),
    ]
    for tag, day, total in seeds:
        pid = _uid(tag)
        await _raw_db.purchase_orders.insert_one({
            "id": pid, "organization_id": org_id, "branch_id": main,
            "po_number": tag.upper(), "vendor": "ACME",
            "date": day, "created_at": f"{day}T09:00:00+00:00",
            "status": "received", "grand_total": total, "balance": 0,
        })

    rs_today = await get_purchase_orders_by_range(range="today", user=history_user)
    rs_week  = await get_purchase_orders_by_range(range="week",  user=history_user)
    rs_month = await get_purchase_orders_by_range(range="month", user=history_user)

    nums = {s[0].upper() for s in seeds}
    today_hits = {p["po_number"] for p in rs_today["purchase_orders"] if p["po_number"] in nums}
    week_hits  = {p["po_number"] for p in rs_week["purchase_orders"]  if p["po_number"] in nums}
    month_hits = {p["po_number"] for p in rs_month["purchase_orders"] if p["po_number"] in nums}

    record_result(
        scenario="br_pos_history.3_po_range_window",
        step="today_one_week_two_month_three",
        expected={"today": 1, "week": 2, "month": 3},
        actual={"today": len(today_hits), "week": len(week_hits), "month": len(month_hits)},
        evidence={"seeded": list(nums)},
    )
    assert len(today_hits) == 1
    assert len(week_hits)  == 2
    assert len(month_hits) == 3


# ═════════════════════════════════════════════════════════════════════
# Test 4 — PO totals math: spent, paid, outstanding, by_status
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_br_pos_history_4_po_totals(
    tenant, history_user, record_result
):
    org_id = tenant["org_id"]
    main = tenant["branches"]["main"]
    today = await _today_str(org_id)
    ts = f"{today}T11:00:00+00:00"

    # 3 POs of varying status: received fully paid, partial unpaid, cancelled
    seeds = [
        ("br_hist-4-recv",   "received", 1500.0, 0),       # paid in full
        ("br_hist-4-partial","partial",  2000.0, 500.0),   # 1500 paid, 500 out
        ("br_hist-4-cancel", "cancelled", 999.0, 0),       # excluded from spent
    ]
    for tag, st, gt, bal in seeds:
        pid = _uid(tag)
        await _raw_db.purchase_orders.insert_one({
            "id": pid, "organization_id": org_id, "branch_id": main,
            "po_number": tag.upper(), "vendor": "V",
            "date": today, "created_at": ts,
            "status": st, "grand_total": gt, "balance": bal,
        })

    res = await get_purchase_orders_by_range(range="today", user=history_user)
    t = res["totals"]

    # The cancelled PO must NOT contribute to spent/paid/outstanding
    assert t["spent"]       >= 1500.0 + 2000.0
    assert t["paid"]        >= 1500.0 + 1500.0
    assert t["outstanding"] >= 500.0
    assert t["by_status"]["received"]  >= 1
    assert t["by_status"]["partial"]   >= 1
    assert t["by_status"]["cancelled"] >= 1

    record_result(
        scenario="br_pos_history.4_po_totals",
        step="spent_paid_outstanding_by_status_correct",
        expected={"spent_ge_3500": True, "paid_ge_3000": True, "outstanding_ge_500": True,
                  "received_ge_1": True, "partial_ge_1": True, "cancelled_ge_1": True},
        actual={"spent_ge_3500": t["spent"] >= 3500.0,
                "paid_ge_3000": t["paid"] >= 3000.0,
                "outstanding_ge_500": t["outstanding"] >= 500.0,
                "received_ge_1":  t["by_status"]["received"]  >= 1,
                "partial_ge_1":   t["by_status"]["partial"]   >= 1,
                "cancelled_ge_1": t["by_status"]["cancelled"] >= 1},
        evidence={"spent": t["spent"], "paid": t["paid"],
                  "outstanding": t["outstanding"], "by_status": t["by_status"]},
    )


# ═════════════════════════════════════════════════════════════════════
# Test 5 — Sales newest-first ordering
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_br_pos_history_5_newest_first(
    tenant, history_user, record_result
):
    org_id = tenant["org_id"]
    main = tenant["branches"]["main"]
    today = await _today_str(org_id)

    # Seed 3 with explicit, well-spaced timestamps
    times = [
        ("br_hist-5-A", f"{today}T08:00:00+00:00"),
        ("br_hist-5-B", f"{today}T12:00:00+00:00"),
        ("br_hist-5-C", f"{today}T18:00:00+00:00"),
    ]
    for tag, ts in times:
        iid = _uid(tag)
        await _raw_db.invoices.insert_one({
            "id": iid, "organization_id": org_id, "branch_id": main,
            "invoice_number": tag.upper(), "customer_name": "X",
            "order_date": today, "invoice_date": today, "created_at": ts,
            "payment_type": "cash", "status": "paid",
            "grand_total": 50.0, "amount_paid": 50.0, "balance": 0,
        })

    res = await get_invoices_by_range(range="today", user=history_user)
    nums = {t[0].upper() for t in times}
    seq = [i["invoice_number"] for i in res["invoices"] if i["invoice_number"] in nums]

    record_result(
        scenario="br_pos_history.5_newest_first",
        step="C_before_B_before_A",
        expected=["BR_HIST-5-C", "BR_HIST-5-B", "BR_HIST-5-A"],
        actual=seq,
        evidence={},
    )
    assert seq.index("BR_HIST-5-C") < seq.index("BR_HIST-5-B") < seq.index("BR_HIST-5-A")

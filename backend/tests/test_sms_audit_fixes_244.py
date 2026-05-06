"""
Iter 244 — SMS audit fixes

Regression coverage for:
  1. `send_zreport_finalized` reads `expected_counter` + `over_short` from
     the canonical daily_closings record (not the non-existent
     `expected_cash` key, and no silent recomputation).
  2. `render_template` collapses missing placeholders to empty string
     (no more literal `<placeholder>` text leaked into outgoing SMS).
  3. `_build_branch_snapshot` mirrors the Close Wizard's cash_expected
     formula: starting_float + cash_in + net_transfers - cashier_expenses.

We stub Mongo with simple in-memory objects and use `asyncio.run` (the
project's existing async-test convention).
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock


# ─────────────────────────────────────────────────────────────────────────────
# 1. render_template — missing keys collapse to empty string
# ─────────────────────────────────────────────────────────────────────────────
def test_render_template_missing_keys_become_empty():
    from routes.sms import render_template

    body = "Hi <customer_name>, <hours_to_close>hrs left at <branch_name>."
    out = render_template(body, {"customer_name": "Juan", "branch_name": "Sampoli"})

    assert "<hours_to_close>" not in out
    assert out == "Hi Juan, hrs left at Sampoli."


def test_render_template_none_value_also_empty():
    from routes.sms import render_template
    assert render_template("Hello <name>!", {"name": None}) == "Hello !"


def test_render_template_known_keys_unchanged():
    from routes.sms import render_template
    assert render_template("Total <a> + <b>", {"a": "10", "b": "20"}) == "Total 10 + 20"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers for async stubs
# ─────────────────────────────────────────────────────────────────────────────
def _async_list(items):
    async def _coro(*a, **kw):
        return items
    m = MagicMock()
    m.to_list = _coro
    return m


# ─────────────────────────────────────────────────────────────────────────────
# 2. send_zreport_finalized — uses close_record.expected_counter / over_short
# ─────────────────────────────────────────────────────────────────────────────
def test_zreport_sms_reads_canonical_keys(monkeypatch):
    """
    Feed a close_record with expected_counter=5_000 and actual_cash=5_000
    (perfect match). Before the fix the SMS would read `expected_cash` (0),
    compute over=+5000, and send "+₱5,000.00 over". After the fix we read
    `expected_counter` and the stored `over_short`, so the SMS says
    "matches".
    """
    from routes import close_reminder as cr

    captured: dict = {}

    async def fake_snapshot(*a, **kw):
        return {
            "sales_count": 3, "sales_total": "7,500.00", "sales_total_raw": 7500,
            "cash_total": "5,000.00", "credit_total": "2,500.00",
            "digital_total": "0.00", "credit_count": 1,
            "expense_count": 1, "expense_total": "200.00",
            "cash_expected": "5,000.00", "cash_expected_raw": 5000,
            "starting_float": "1,000.00", "total_cash_in": "4,200.00",
            "total_cash_ar": "0.00", "total_digital_ar": "0.00",
            "total_ar_received": "0.00", "net_fund_transfers": "0.00",
            "total_cashier_expenses": "200.00", "pending_credits": 0,
        }

    async def fake_recipients(*a, **kw):
        return [{"id": "u1", "name": "Owner", "phone": "+63900"}]

    async def fake_queue_sms(**kwargs):
        captured.update(kwargs)

    class _Coll:
        async def find_one(self, *a, **kw):
            return {"name": "Sampoli", "organization_id": "org1"}
        async def count_documents(self, *a, **kw):
            return 0

    class _Raw:
        branches = _Coll()
        invoices = _Coll()

    monkeypatch.setattr(cr, "_build_branch_snapshot", fake_snapshot)
    monkeypatch.setattr(cr, "_resolve_recipients", fake_recipients)
    monkeypatch.setattr(cr, "_raw_db", _Raw())

    import routes.sms as sms_mod
    monkeypatch.setattr(sms_mod, "queue_sms", fake_queue_sms)

    close_record = {
        "branch_id": "b1",
        "date": "2026-02-05",
        "expected_counter": 5000.0,
        "actual_cash": 5000.0,
        "over_short": 0.0,
        "closed_at": "2026-02-05T18:30:00+08:00",
    }

    asyncio.run(cr.send_zreport_finalized(close_record, user={"full_name": "Test"}))

    assert captured, "queue_sms was not invoked"
    variables = captured["variables"]
    assert variables["cash_expected"] == "5,000.00"
    assert variables["over_short"] == "matches", (
        f"Regression: over_short computed from wrong key. Got {variables['over_short']!r}"
    )


def test_zreport_sms_negative_variance_shows_short(monkeypatch):
    """Drawer short 250 → SMS says 'short'."""
    from routes import close_reminder as cr

    captured: dict = {}
    async def fake_snapshot(*a, **kw): return {"sales_count": 0, "credit_count": 0, "expense_count": 0}
    async def fake_recipients(*a, **kw): return [{"id": "u1", "name": "Owner", "phone": "+63900"}]
    async def fake_queue_sms(**kw): captured.update(kw)
    class _Coll:
        async def find_one(self, *a, **kw): return {"name": "B", "organization_id": "o"}
        async def count_documents(self, *a, **kw): return 0
    class _Raw:
        branches = _Coll(); invoices = _Coll()
    monkeypatch.setattr(cr, "_build_branch_snapshot", fake_snapshot)
    monkeypatch.setattr(cr, "_resolve_recipients", fake_recipients)
    monkeypatch.setattr(cr, "_raw_db", _Raw())
    import routes.sms as sms_mod
    monkeypatch.setattr(sms_mod, "queue_sms", fake_queue_sms)

    asyncio.run(cr.send_zreport_finalized(
        {"branch_id": "b", "date": "2026-02-05",
         "expected_counter": 5000, "actual_cash": 4750, "over_short": -250,
         "closed_at": "2026-02-05T18:30:00+08:00"},
        user={"full_name": "T"}
    ))

    assert "short" in captured["variables"]["over_short"].lower()
    assert "250" in captured["variables"]["over_short"]


# ─────────────────────────────────────────────────────────────────────────────
# 3. _build_branch_snapshot — mirrors Close Wizard formula
# ─────────────────────────────────────────────────────────────────────────────
def test_snapshot_cash_expected_includes_starting_float_and_ar(monkeypatch):
    """
    Scenario — single cash sale 1,000, AR payment 500 on prior invoice,
    safe→drawer transfer 200, one cashier expense 100. Yesterday's drawer
    carry-forward 2,000.

    Close-wizard formula:
        expected = 2000 + (1000 + 0 + 500 + 0) + 200 - 100 = 3,600
    Old shallow formula: 1000 - 100 = 900  ← wrong by ₱2,700
    """
    from routes import close_reminder as cr

    invoices_today = [{
        "grand_total": 1000.0, "payment_type": "cash", "fund_source": "cashier",
        "amount_paid": 1000.0, "balance": 0.0, "cash_amount": 0,
        "digital_amount": 0, "late_encoded": False,
    }]
    ar_rows = [{"amount": 500.0, "fund_source": "cashier"}]
    expenses_today = [{"amount": 100.0, "fund_source": "cashier"}]
    transfers_today = [{"transfer_type": "safe_to_cashier", "amount": 200.0}]
    prev_close = {"cash_to_drawer": 2000.0}

    class _InvColl:
        def find(self, *a, **kw): return _async_list(invoices_today)
        def aggregate(self, *a, **kw): return _async_list(ar_rows)
    class _Expenses:
        def find(self, *a, **kw): return _async_list(expenses_today)
    class _FundTransfers:
        def find(self, *a, **kw): return _async_list(transfers_today)
    class _Closings:
        async def find_one(self, *a, **kw): return prev_close
    class _PendingCredits:
        async def count_documents(self, *a, **kw): return 0
    class _Raw:
        invoices = _InvColl()
        expenses = _Expenses()
        fund_transfers = _FundTransfers()
        daily_closings = _Closings()
        fund_wallets = _Closings()
        pending_credits = _PendingCredits()

    monkeypatch.setattr(cr, "_raw_db", _Raw())

    snap = asyncio.run(cr._build_branch_snapshot("b1", "2026-02-05", organization_id="org1"))

    assert snap["cash_expected_raw"] == 3600.0, (
        f"Expected 3600, got {snap['cash_expected_raw']}"
    )
    assert snap["cash_expected"] == "3,600.00"
    assert snap["starting_float"] == "2,000.00"
    assert snap["total_cash_ar"] == "500.00"
    assert snap["net_fund_transfers"] == "200.00"
    assert snap["total_cashier_expenses"] == "100.00"


def test_snapshot_split_payment_splits_cash_and_digital(monkeypatch):
    """
    Split invoice: cash_amount=400 + digital_amount=600 (grand 1000).
    Old code: full 1000 went into cash. New: only 400 hits drawer.
    """
    from routes import close_reminder as cr

    invoices_today = [{
        "grand_total": 1000.0, "payment_type": "cash", "fund_source": "split",
        "amount_paid": 1000.0, "balance": 0.0,
        "cash_amount": 400.0, "digital_amount": 600.0,
    }]

    class _InvColl:
        def find(self, *a, **kw): return _async_list(invoices_today)
        def aggregate(self, *a, **kw): return _async_list([])
    class _Empty:
        def find(self, *a, **kw): return _async_list([])
        async def find_one(self, *a, **kw): return None
        async def count_documents(self, *a, **kw): return 0
    class _Raw:
        invoices = _InvColl()
        expenses = _Empty()
        fund_transfers = _Empty()
        daily_closings = _Empty()
        fund_wallets = _Empty()
        pending_credits = _Empty()

    monkeypatch.setattr(cr, "_raw_db", _Raw())

    snap = asyncio.run(cr._build_branch_snapshot("b1", "2026-02-05", organization_id="org1"))

    assert snap["cash_expected_raw"] == 400.0, (
        f"Split payment leaked digital into cash: {snap['cash_expected_raw']}"
    )
    assert snap["digital_total"] == "600.00"


def test_snapshot_excludes_safe_paid_expenses_from_cash_expected(monkeypatch):
    """
    Sale 1,000 cash. Two expenses: 100 cashier-paid + 50 safe-paid.
    Old shallow formula: cash_total - exp_total = 1000 - 150 = 850 (wrong).
    New formula: only cashier expense hits drawer → 1000 - 100 = 900.
    """
    from routes import close_reminder as cr

    invoices_today = [{
        "grand_total": 1000.0, "payment_type": "cash", "fund_source": "cashier",
        "amount_paid": 1000.0, "balance": 0.0, "cash_amount": 0, "digital_amount": 0,
    }]
    expenses_today = [
        {"amount": 100.0, "fund_source": "cashier"},
        {"amount": 50.0, "fund_source": "safe"},
    ]

    class _InvColl:
        def find(self, *a, **kw): return _async_list(invoices_today)
        def aggregate(self, *a, **kw): return _async_list([])
    class _Expenses:
        def find(self, *a, **kw): return _async_list(expenses_today)
    class _Empty:
        def find(self, *a, **kw): return _async_list([])
        async def find_one(self, *a, **kw): return None
        async def count_documents(self, *a, **kw): return 0
    class _Raw:
        invoices = _InvColl()
        expenses = _Expenses()
        fund_transfers = _Empty()
        daily_closings = _Empty()
        fund_wallets = _Empty()
        pending_credits = _Empty()

    monkeypatch.setattr(cr, "_raw_db", _Raw())

    snap = asyncio.run(cr._build_branch_snapshot("b1", "2026-02-05", organization_id="org1"))
    # 0 starting_float (no prev_close, no wallet) + 1000 cash_in + 0 transfers - 100 cashier exp = 900
    assert snap["cash_expected_raw"] == 900.0
    assert snap["expense_total"] == "150.00"  # all expenses still listed
    assert snap["total_cashier_expenses"] == "100.00"  # but only cashier hits drawer

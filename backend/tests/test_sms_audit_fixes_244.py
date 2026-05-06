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



# ─────────────────────────────────────────────────────────────────────────────
# 4. Iter 244 — content enrichment + void/refund hooks
# ─────────────────────────────────────────────────────────────────────────────
def _stub_customer_lookup(monkeypatch, sms_hooks_mod, customer):
    """Wire raw_db.customers.find_one to return a fixed customer doc."""
    class _Coll:
        async def find_one(self, *a, **kw):
            return customer
    class _Raw:
        customers = _Coll()
    monkeypatch.setattr(sms_hooks_mod, "raw_db", _Raw())


def _coro_value(v):
    async def _c(*a, **k): return v
    return _c


def test_payment_received_includes_invoice_number(monkeypatch):
    """`applied_to` placeholder exposes the source invoice number."""
    from routes import sms_hooks
    captured: dict = {}
    async def _fake_queue(**kw): captured.update(kw)

    _stub_customer_lookup(monkeypatch, sms_hooks, {
        "id": "c1", "name": "Juan", "phone": "+63900",
    })
    monkeypatch.setattr(sms_hooks, "_resolve_org_id", _coro_value("org1"))
    monkeypatch.setattr(sms_hooks, "get_company_name", _coro_value("Test Co"))
    monkeypatch.setattr(sms_hooks, "get_branch_name", _coro_value("Sampoli"))
    import routes.sms as sms_mod
    monkeypatch.setattr(sms_mod, "queue_sms", _fake_queue)

    asyncio.run(sms_hooks.on_payment_received(
        customer_id="c1", amount_paid=500, remaining_balance=1500,
        branch_id="b1", next_due_info="Next due: 2026-03-01. ",
        invoice_number="INV-2025-0041",
    ))

    assert captured["template_key"] == "payment_received"
    v = captured["variables"]
    assert v["applied_to"] == " (applied to INV-2025-0041)"
    assert v["amount_paid"] == "500.00"
    assert v["next_due_info"] == "Next due: 2026-03-01. "


def test_payment_received_omits_applied_to_when_no_invoice(monkeypatch):
    """When `invoice_number` is empty, no leading-space artifact appears."""
    from routes import sms_hooks
    captured: dict = {}
    async def _fake_queue(**kw): captured.update(kw)
    _stub_customer_lookup(monkeypatch, sms_hooks, {"id": "c1", "name": "J", "phone": "+63900"})
    monkeypatch.setattr(sms_hooks, "_resolve_org_id", _coro_value("o"))
    monkeypatch.setattr(sms_hooks, "get_company_name", _coro_value("Co"))
    monkeypatch.setattr(sms_hooks, "get_branch_name", _coro_value("B"))
    import routes.sms as sms_mod
    monkeypatch.setattr(sms_mod, "queue_sms", _fake_queue)

    asyncio.run(sms_hooks.on_payment_received("c1", 100, 0, "b1"))
    assert captured["variables"]["applied_to"] == ""


def test_charge_applied_includes_source_invoice(monkeypatch):
    """`source_invoice` is wrapped as ' for INV-...' so it reads naturally."""
    from routes import sms_hooks
    captured = []
    async def _fake_queue(**kw): captured.append(kw)
    _stub_customer_lookup(monkeypatch, sms_hooks, {"id": "c1", "name": "Juan", "phone": "+63900"})
    monkeypatch.setattr(sms_hooks, "_resolve_org_id", _coro_value("org1"))
    monkeypatch.setattr(sms_hooks, "get_company_name", _coro_value("Test Co"))
    monkeypatch.setattr(sms_hooks, "get_branch_name", _coro_value("Sampoli"))
    monkeypatch.setattr(sms_hooks, "_get_cc_phones", _coro_value({}))  # no manager CC
    import routes.sms as sms_mod
    monkeypatch.setattr(sms_mod, "queue_sms", _fake_queue)

    asyncio.run(sms_hooks.on_charge_applied(
        "c1", "Interest", 250.0, 5250.0, "b1",
        source_invoice="INV-INT-2025-0007",
    ))

    assert captured, "queue_sms not called"
    v = captured[0]["variables"]
    assert v["source_invoice"] == " for INV-INT-2025-0007"
    assert v["charge_amount"] == "250.00"


def test_on_invoice_voided_skips_walkin(monkeypatch):
    """Walk-in sale (no customer_id) should NOT enqueue any SMS."""
    from routes import sms_hooks
    calls = []
    async def _fake_queue(**kw): calls.append(kw)
    import routes.sms as sms_mod
    monkeypatch.setattr(sms_mod, "queue_sms", _fake_queue)

    asyncio.run(sms_hooks.on_invoice_voided(
        {"id": "i1", "customer_id": "", "invoice_number": "INV-0001",
         "branch_id": "b1", "grand_total": 500, "balance": 0}
    ))
    assert calls == [], "Walk-in voids must not trigger customer SMS"


def test_on_invoice_voided_credit_sends_sms(monkeypatch):
    """Credit-sale void → SMS with reason + balance restoration note."""
    from routes import sms_hooks
    captured: dict = {}
    async def _fake_queue(**kw): captured.update(kw)
    _stub_customer_lookup(monkeypatch, sms_hooks, {"id": "c1", "name": "Juan", "phone": "+63900"})
    monkeypatch.setattr(sms_hooks, "_resolve_org_id", _coro_value("org1"))
    monkeypatch.setattr(sms_hooks, "get_company_name", _coro_value("Co"))
    monkeypatch.setattr(sms_hooks, "get_branch_name", _coro_value("Br"))
    monkeypatch.setattr(sms_hooks, "today_local", _coro_value("2026-02-05"))
    import routes.sms as sms_mod
    monkeypatch.setattr(sms_mod, "queue_sms", _fake_queue)

    asyncio.run(sms_hooks.on_invoice_voided(
        {"id": "i1", "customer_id": "c1", "invoice_number": "INV-0007",
         "branch_id": "b1", "grand_total": 1000.0, "balance": 600.0,
         "order_date": "2026-02-04"},
        reason="Wrong item",
    ))

    assert captured["template_key"] == "sale_voided"
    v = captured["variables"]
    assert v["invoice_number"] == "INV-0007"
    assert v["grand_total"] == "1,000.00"
    assert "600.00" in v["balance_note"], "balance_note should mention restored balance"
    assert v["reason"] == "Wrong item"


def test_on_refund_processed_walkin_skipped(monkeypatch):
    """Walk-in returns (no customer_id) → no SMS."""
    from routes import sms_hooks
    calls = []
    async def _fake_queue(**kw): calls.append(kw)
    import routes.sms as sms_mod
    monkeypatch.setattr(sms_mod, "queue_sms", _fake_queue)

    asyncio.run(sms_hooks.on_refund_processed(
        {"id": "r1", "customer_id": "", "rma_number": "RTN-001"}
    ))
    assert calls == []


def test_on_refund_processed_full_cash_refund(monkeypatch):
    """Full cash refund → refund_line populated, credit_line empty."""
    from routes import sms_hooks
    captured: dict = {}
    async def _fake_queue(**kw): captured.update(kw)
    _stub_customer_lookup(monkeypatch, sms_hooks, {"id": "c1", "name": "Juan", "phone": "+63900"})
    monkeypatch.setattr(sms_hooks, "_resolve_org_id", _coro_value("org1"))
    monkeypatch.setattr(sms_hooks, "get_company_name", _coro_value("Co"))
    monkeypatch.setattr(sms_hooks, "get_branch_name", _coro_value("Br"))
    import routes.sms as sms_mod
    monkeypatch.setattr(sms_mod, "queue_sms", _fake_queue)

    asyncio.run(sms_hooks.on_refund_processed({
        "id": "r1", "customer_id": "c1", "rma_number": "RTN-001",
        "branch_id": "b1", "refund_amount": 500.0, "credit_applied": 0.0,
        "reason": "Defective",
    }))

    v = captured["variables"]
    assert "500.00" in v["refund_line"]
    assert v["credit_line"] == ""
    assert v["rma_number"] == "RTN-001"


def test_on_refund_processed_store_credit_only(monkeypatch):
    """Store-credit only → credit_line populated, refund_line empty."""
    from routes import sms_hooks
    captured: dict = {}
    async def _fake_queue(**kw): captured.update(kw)
    _stub_customer_lookup(monkeypatch, sms_hooks, {"id": "c1", "name": "Juan", "phone": "+63900"})
    monkeypatch.setattr(sms_hooks, "_resolve_org_id", _coro_value("org1"))
    monkeypatch.setattr(sms_hooks, "get_company_name", _coro_value("Co"))
    monkeypatch.setattr(sms_hooks, "get_branch_name", _coro_value("Br"))
    import routes.sms as sms_mod
    monkeypatch.setattr(sms_mod, "queue_sms", _fake_queue)

    asyncio.run(sms_hooks.on_refund_processed({
        "id": "r2", "customer_id": "c1", "rma_number": "RTN-002",
        "branch_id": "b1", "refund_amount": 0.0, "credit_applied": 750.0,
        "reason": "Customer changed mind",
    }))

    v = captured["variables"]
    assert v["refund_line"] == ""
    assert "750.00" in v["credit_line"]

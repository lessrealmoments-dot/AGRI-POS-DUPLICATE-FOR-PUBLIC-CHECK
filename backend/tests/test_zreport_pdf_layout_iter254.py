"""
Iter 254 — Regression tests for Z-Report PDF layout & math display fixes.

Bug history (user uploaded a closing PDF in Feb 2026 showing):
  1. "Total Cash In" subtotal value did NOT match the running sum of the
     rows above it (Opening Float and Fund Transfers were rendered above
     the subtotal but excluded from its math).
  2. "Expected in Drawer" was bolded in a way that suggested it was the
     post-vault drawer balance, while Over/Short was correctly using the
     pre-vault expected_counter — visually inconsistent.
  3. Fund Transfer rows had text overlap between Authorized By and Amount
     columns when names were long ("Sibugay Agrivet Supply").
  4. Expenses section header total didn't visibly match the sum of the
     itemized rows (likely a stale closing snapshot after a late-encoded
     expense was added — verification footer now flags the mismatch).

Fix:
  - render_detailed() Cash Drawer Reconciliation rebuilt as a single
    top-to-bottom math ledger; "Total Cash In" subtotal removed; only
    one bolded subtotal "= Expected in Drawer (Pre-Vault)" remains.
  - render_normal() Cash Reconciliation labels Pre-Vault explicitly and
    breaks End-of-Day Vault Allocation into its own sub-block.
  - Fund Transfer table widths rebalanced (38 / 52 / 30 / 50) and
    Authorized By / Note columns trimmed to safer character limits.
  - Expense section in render_normal now prints a verification footer
    showing rendered subtotal vs section total; flags any mismatch.
"""
from io import BytesIO

from pypdf import PdfReader
from routes.zreport_pdf import ZReportPDF, render_detailed, render_normal


def _render_pdf(mode: str, *, preview: dict, closing: dict) -> str:
    pdf = ZReportPDF("Test Branch", "2026-02-05", "Test Cashier", mode_label=mode.upper())
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()
    if mode == "detailed":
        render_detailed(pdf, preview, closing)
    else:
        render_normal(pdf, closing)
    buf = BytesIO()
    pdf.output(buf)
    buf.seek(0)
    reader = PdfReader(buf)
    return "\n".join(p.extract_text() or "" for p in reader.pages)


def _preview_with_fund_transfers():
    """Preview shape mirroring get_daily_close_preview output."""
    return {
        "date": "2026-02-05",
        "starting_float": 3000.0,
        "safe_balance": 50000.0,
        "total_cash_sales": 25000.0,
        "total_split_cash": 1500.0,
        "total_cash_ar": 4000.0,
        "total_digital_ar": 0.0,
        "total_ar_received": 4000.0,
        "capital_to_cashier": 0.0,
        "safe_to_cashier": 5000.0,
        "cashier_to_safe": 2000.0,
        "net_fund_transfers": 3000.0,
        "total_cashier_expenses": 1200.0,
        "total_safe_expenses": 0.0,
        "total_expenses": 1200.0,
        # expected_counter = 3000 + (25000 + 4000 + 1500) + 3000 - 1200 = 35300
        "expected_counter": 35300.0,
        "fund_transfers_today": [
            {
                "type": "safe_to_cashier",
                "amount": 5000.0,
                "note": "Restock float for the afternoon shift counter",
                "authorized_by": "Sibugay Agrivet Supply / J. Domunales",
            },
            {
                "type": "cashier_to_safe",
                "amount": 2000.0,
                "note": "Mid-day vault deposit",
                "authorized_by": "JOHNNY SUMMERSET DOMUNALES",
            },
        ],
        "expenses": [],
        "ar_payments": [],
        "credit_sales_today": [],
        "digital_sales_today": [],
        "cash_sales_by_category": [],
    }


def _closing_for_preview(preview: dict, *, actual_cash: float):
    """Closing record matching the preview snapshot."""
    expected = preview["expected_counter"]
    return {
        "id": "test-close-iter254",
        "date": preview["date"],
        "branch_id": "test-branch",
        "expected_counter": expected,
        "actual_cash": actual_cash,
        "over_short": round(actual_cash - expected, 2),
        "cash_to_safe": 30000.0,
        "cash_to_drawer": 5000.0,
    }


# ── Bug 1 ────────────────────────────────────────────────────────────────────
def test_detailed_no_misleading_total_cash_in_subtotal():
    """The old "= Total Cash In" subtotal label is gone — it summed only
    cash_sales+cash_ar+split_cash and confused readers because Opening
    Float and Fund Transfers were rendered above it but excluded from
    its math."""
    preview = _preview_with_fund_transfers()
    text = _render_pdf("detailed", preview=preview, closing=_closing_for_preview(preview, actual_cash=35300.0))
    assert "Total Cash In" not in text, (
        "render_detailed must not display the misleading 'Total Cash In' subtotal anymore"
    )
    # The only headline subtotal should be the Pre-Vault expected drawer.
    assert "Expected in Drawer (Pre-Vault)" in text


# ── Bug 2 ────────────────────────────────────────────────────────────────────
def test_detailed_expected_minus_actual_equals_over_short():
    """Actual - Expected (as displayed) must equal the Over/Short value
    rendered in the PDF."""
    preview = _preview_with_fund_transfers()
    closing = _closing_for_preview(preview, actual_cash=35290.0)  # P10 short
    text = _render_pdf("detailed", preview=preview, closing=closing)

    # P35,300.00 expected, P35,290.00 actual, Over/Short = -P10.00
    assert "P35,300.00" in text  # Expected
    assert "P35,290.00" in text  # Actual
    # Over/Short formatted with explicit sign
    assert "-P10.00" in text
    # End-of-Day Vault Allocation block must appear AFTER the over/short calc
    over_short_idx = text.find("Cash Short")
    vault_idx = text.find("End-of-Day Vault Allocation")
    assert over_short_idx > 0 and vault_idx > 0
    assert vault_idx > over_short_idx, (
        "End-of-Day Vault Allocation must render AFTER Over/Short, not before"
    )


def test_normal_cash_reconciliation_labels_pre_vault():
    """Normal compact layout must label Expected as Pre-Vault and
    separate End-of-Day allocation."""
    closing = {
        "id": "c1", "date": "2026-02-05", "branch_id": "b1",
        "starting_float": 3000.0, "safe_balance": 0.0,
        "expected_counter": 35300.0, "actual_cash": 35290.0,
        "over_short": -10.0, "cash_to_safe": 30000.0, "cash_to_drawer": 5000.0,
        "total_cash_sales": 25000.0, "sales_by_category": {"Feeds": 25000.0},
        "expenses": [], "total_expenses": 0.0,
    }
    text = _render_pdf("compact", preview=closing, closing=closing)
    assert "Expected in Counter (Pre-Vault)" in text
    assert "End-of-Day Vault Allocation" in text
    assert "Float for Tomorrow" in text


# ── Bug 3 ────────────────────────────────────────────────────────────────────
def test_fund_transfers_no_column_overlap():
    """Fund transfer rows must render without crashing even when
    authorized_by / note are very long (test ensures the new columns
    don't overflow the page width)."""
    preview = _preview_with_fund_transfers()
    text = _render_pdf("detailed", preview=preview, closing=_closing_for_preview(preview, actual_cash=35300.0))
    assert "FUND TRANSFERS" in text
    # Both authorized_by names should appear (truncated to ~32 chars)
    assert "Sibugay Agrivet Supply" in text
    assert "JOHNNY SUMMERSET DOMUNALES" in text
    # Note column shouldn't be cut to 6 chars — should fit ~32
    assert "Restock float" in text


# ── Bug 4 ────────────────────────────────────────────────────────────────────
def test_normal_expense_verification_footer_matches_section_total():
    """When section total matches itemized sum, the verification footer
    prints 'Itemized total' and NO mismatch warning."""
    closing = {
        "id": "c1", "date": "2026-02-05", "branch_id": "b1",
        "starting_float": 0.0, "safe_balance": 0.0,
        "expected_counter": 0.0, "actual_cash": 0.0, "over_short": 0.0,
        "cash_to_safe": 0.0, "cash_to_drawer": 0.0,
        "total_cash_sales": 0.0, "sales_by_category": {},
        "expenses": [
            {"category": "Utilities", "description": "Electric bill", "amount": 1500.0},
            {"category": "Supplies", "description": "Receipt paper", "amount": 500.0},
            {"category": "Repairs", "description": "Tire patch", "amount": 250.0},
        ],
        "total_expenses": 2250.0,
    }
    text = _render_pdf("compact", preview=closing, closing=closing)
    assert "Itemized total (3 rows)" in text
    assert "P2,250.00" in text
    assert "Mismatch" not in text


def test_normal_expense_mismatch_warning_when_stale_snapshot():
    """If a late-encoded expense bumps total_expenses but the stored
    `expenses` snapshot was not refreshed, the verification footer flags
    the mismatch instead of leaving a silent visual discrepancy."""
    closing = {
        "id": "c1", "date": "2026-02-05", "branch_id": "b1",
        "starting_float": 0.0, "safe_balance": 0.0,
        "expected_counter": 0.0, "actual_cash": 0.0, "over_short": 0.0,
        "cash_to_safe": 0.0, "cash_to_drawer": 0.0,
        "total_cash_sales": 0.0, "sales_by_category": {},
        "expenses": [
            # Only 8 visible rows, but total_expenses claims 9 rows worth.
            {"category": "Utilities", "description": f"row-{i}", "amount": 11151.5}
            for i in range(8)
        ],
        # Sum = 89212; pretend a 9th row of P1,500 was late-encoded later.
        "total_expenses": 90712.0,
    }
    text = _render_pdf("compact", preview=closing, closing=closing)
    assert "Itemized total (8 rows)" in text
    assert "P89,212.00" in text
    assert "Mismatch" in text
    # Diff sign — total > rendered means rows are MISSING from itemized list
    assert "missing from itemized list" in text

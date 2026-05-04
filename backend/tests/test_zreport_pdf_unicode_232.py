"""
Regression tests for the Z-Report PDF generator (zreport_pdf.py).

Bug history:
  - Detailed-mode downloads crashed with FPDFUnicodeEncodingException
    whenever any customer name, expense description, transfer note,
    or closed_by_name contained characters outside Latin-1 (em-dash `—`,
    peso `P`->`₱`, curly quotes, bullets, arrows). The default FPDF
    Helvetica font cannot encode those.
  - Normal mode also only printed a Cash-Drawer-Reconciliation stub,
    diverging from the on-screen Normal <ZReport> (UI preview).

Fix:
  - `_s()` sanitizer folds common unicode punctuation/currency to ASCII
    equivalents and drops any remaining non-Latin-1 chars.
  - `render_normal()` mirrors the on-screen Normal layout (Opening,
    Cash Reconciliation, Walk-in Sales by Category, Credit Extended,
    AR Payments, AR Balance, Expenses).
"""
from io import BytesIO

import pytest

from routes.zreport_pdf import (
    _s,
    ZReportPDF,
    render_normal,
    render_detailed,
)


# ── _s() sanitizer ───────────────────────────────────────────────────────────
@pytest.mark.parametrize("raw, expected_substr", [
    ("Doña Peñaflorida", "Doña Peñaflorida"),  # Latin-1 accents preserved
    ("₱99,274.70", "P99,274.70"),                # peso → P
    ("Niño — Ramos Farm", "Niño - Ramos Farm"),  # em-dash → hyphen
    ("Capital → Cashier", "Capital -> Cashier"), # arrow
    ("✓ Approved", "OK Approved"),               # check mark
    ("Bullet • point", "Bullet - point"),        # bullet
    ("\u2018hello\u2019", "'hello'"),            # curly quotes
    ("Ellipsis…", "Ellipsis..."),                # ellipsis
    ("Emoji 🎉 dropped", "Emoji  dropped"),      # emoji stripped, space kept
    (None, ""),
    (123.45, "123.45"),
])
def test_sanitizer_folds_to_latin1(raw, expected_substr):
    out = _s(raw)
    # Must be Latin-1 encodable.
    out.encode("latin-1")
    assert expected_substr in out


# ── End-to-end PDF generation ────────────────────────────────────────────────
def _sample_closing():
    return {
        "id": "unit-test-closing",
        "date": "2025-12-31",
        "branch_id": "test-branch",
        "status": "closed",
        "closed_by_name": "Doña — Ñaño",
        "starting_float": 5000.0,
        "safe_balance": 10000.0,
        "expected_counter": 12345.67,
        "actual_cash": 12000.0,
        "over_short": -345.67,
        "cash_to_safe": 10000.0,
        "cash_to_drawer": 2000.0,
        "total_cash_sales": 8500.0,
        "sales_by_category": {"Feeds": 3500.0, "Médicíñés — Vet": 5000.0},
        "credit_sales_today": [
            {"customer_name": "Tito Ñoño – Farm", "invoice_number": "SI-001",
             "grand_total": 1500.0, "balance": 1500.0, "payment_type": "credit"},
        ],
        "ar_credits_today": [
            {"customer_name": "Mang Pépé • Uy", "invoice_number": "CAD-007",
             "grand_total": 800.0, "type": "cash_advance"},
        ],
        "total_new_credit": 2300.0,
        "credit_collections": [
            {"customer": "María José—López", "invoice": "SI-999",
             "balance_before": 5000.0, "interest_paid": 250.0,
             "penalty_paid": 0, "total_paid": 2000.0, "balance": 3000.0},
        ],
        "total_ar_received": 2000.0,
        "total_ar_at_close": 45678.90,
        "expenses": [
            {"category": "Employee Advance",
             "description": "Cash advance for travel → Cebu",
             "employee_name": "Juan dela Cruz",
             "amount": 500.0, "monthly_ca_total": 1500.0,
             "monthly_ca_limit": 2000.0, "is_over_ca": False,
             "manager_approved_by": "Mgr Ñaño"},
            {"category": "Supplies",
             "description": "Receipts — ₱-denominated", "amount": 120.50},
        ],
        "total_expenses": 620.50,
    }


def _render(mode: str, closing: dict) -> bytes:
    pdf = ZReportPDF("Test Branch", closing["date"], closing["closed_by_name"],
                     mode_label=mode.upper())
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()
    if mode == "detailed":
        render_detailed(pdf, closing, closing)
    else:
        render_normal(pdf, closing)
    buf = BytesIO()
    pdf.output(buf)
    return buf.getvalue()


def test_normal_pdf_renders_unicode_without_crash():
    data = _render("compact", _sample_closing())
    assert data.startswith(b"%PDF"), "output is not a valid PDF"
    assert len(data) > 1000, "PDF suspiciously small — rendering likely bailed early"


def test_detailed_pdf_renders_unicode_without_crash():
    data = _render("detailed", _sample_closing())
    assert data.startswith(b"%PDF")
    assert len(data) > 500


def test_normal_pdf_contains_ui_sections():
    """The on-screen Normal preview shows Opening, Cash Reconciliation,
    Walk-in Sales, Credit Extended, AR Payments, AR Balance, Expenses —
    the PDF must mirror those same sections."""
    from pypdf import PdfReader
    data = _render("compact", _sample_closing())
    reader = PdfReader(BytesIO(data))
    text = "\n".join(p.extract_text() or "" for p in reader.pages)

    for section in [
        "OPENING", "CASH RECONCILIATION", "WALK-IN SALES",
        "NEW CREDIT EXTENDED TODAY", "AR PAYMENTS RECEIVED",
        "AR BALANCE AT CLOSE", "EXPENSES",
    ]:
        assert section in text, f"Normal PDF missing section {section!r}"
    # UI-specific rows
    assert "Feeds" in text
    assert "Tito Ñoño" in text  # customer w/ accent survives
    assert "Juan dela Cruz" in text  # employee CA detail line survives
    # Peso sign folded to P
    assert "P12,345.67" in text
    # No leftover unicode that Helvetica can't handle
    text.encode("latin-1")

"""
Z-Report PDF Generator.

Two layouts, selected by the `detailed` query flag:

* Normal  (detailed=False)  → mirrors the on-screen Normal Z-Report view in
  DailyLogPage.js: Opening + Cash Reconciliation side-by-side, Walk-in Sales
  by Category, Credit Extended Today, AR Payments Received, AR Balance at
  Close, and Expenses with employee cash-advance details.

* Detailed (detailed=True) → mirrors Close Wizard Step 7: full breakdown of
  AR-by-method, Fund Transfers, Cashier & Safe Expenses, Credit Extended,
  E-Wallet Payments, Cash Sales by Category, Discounts, Price Changes,
  Interest / Penalty Invoices.

Endpoint: GET /api/reports/z-report-pdf?date=YYYY-MM-DD&branch_id=xxx&detailed=<bool>
"""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from typing import Optional
from datetime import datetime, timezone, timedelta
from io import BytesIO
from fpdf import FPDF
from config import db
from utils import get_current_user, check_perm

router = APIRouter(prefix="/reports", tags=["Reports"])


# ── Unicode sanitizer ────────────────────────────────────────────────────────
# FPDF's built-in Helvetica font is Latin-1 only — any character outside that
# range (em-dash, peso sign, curly quotes, bullets, arrows, emoji) raises
# FPDFUnicodeEncodingException and aborts the whole PDF. Customer names,
# expense descriptions, and transfer notes routinely contain these, which
# was crashing the Detailed mode PDF. We fold common typographic characters
# to safe ASCII equivalents and drop anything still outside Latin-1 rather
# than shipping a broken download.
_UNI_MAP = {
    "—": "-", "–": "-", "‒": "-", "―": "-",
    "’": "'", "‘": "'", "“": '"', "”": '"', "‚": ",", "„": '"',
    "…": "...", "•": "-", "·": ".", "●": "-", "◦": "-", "▪": "-",
    "→": "->", "←": "<-", "↑": "^", "↓": "v", "⇒": "=>", "⇐": "<=",
    "✓": "OK", "✔": "OK", "✗": "X", "✘": "X", "×": "x",
    "₱": "P", "€": "EUR", "£": "GBP", "¥": "YEN",
    "©": "(c)", "®": "(R)", "™": "(TM)",
    "«": '"', "»": '"', " ": " ", "\u200b": "", "\ufeff": "",
}


def _s(val) -> str:
    """Return a Latin-1-safe string for FPDF.

    Normalizes common unicode punctuation/currency to ASCII equivalents and
    drops any remaining characters that Helvetica cannot encode.
    """
    if val is None:
        return ""
    s = str(val)
    for k, v in _UNI_MAP.items():
        if k in s:
            s = s.replace(k, v)
    # Strip anything that still isn't Latin-1 encodable.
    return s.encode("latin-1", "ignore").decode("latin-1")


def php(n) -> str:
    return f"P{abs(float(n or 0)):,.2f}"


def php_signed(n) -> str:
    n = float(n or 0)
    sign = "+" if n > 0 else ("-" if n < 0 else "")
    return f"{sign}P{abs(n):,.2f}"


class ZReportPDF(FPDF):
    def __init__(self, branch_name: str, date: str, cashier: str, mode_label: str = "COMPACT", company_name: str = ""):
        super().__init__()
        self.branch_name = _s(branch_name)
        self.report_date = _s(date)
        self.cashier_name = _s(cashier)
        self.mode_label = _s(mode_label)
        self.company_name = _s(company_name or "")

    def header(self):
        # Line 1 — Company name (primary brand) + Z-Report mode tag
        self.set_font("Helvetica", "B", 16)
        self.set_text_color(26, 77, 46)
        title = self.company_name if self.company_name else "AgriBooks"
        self.cell(0, 8, _s(title), align="C", new_x="LMARGIN", new_y="NEXT")
        # Line 2 — Z-Report subtitle (so the doc type is unambiguous)
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(80, 80, 80)
        self.cell(
            0, 5,
            _s(f"Z-Report ({self.mode_label})  |  {self.branch_name}  |  {self.report_date}"),
            align="C", new_x="LMARGIN", new_y="NEXT",
        )
        # Line 3 — Cashier metadata
        self.set_font("Helvetica", "", 8)
        self.set_text_color(120, 120, 120)
        self.cell(
            0, 4,
            _s(f"Prepared by: {self.cashier_name}"),
            align="C", new_x="LMARGIN", new_y="NEXT",
        )
        self.line(10, self.get_y() + 2, 200, self.get_y() + 2)
        self.ln(5)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 7)
        self.set_text_color(150, 150, 150)
        # Generated timestamp displayed in MM/DD/YYYY hh:mm AM/PM in the
        # org's local timezone (defaults to Asia/Manila). Iter 238 fix —
        # previously showed `UTC` which read as 8 hours off for Manila users.
        try:
            from zoneinfo import ZoneInfo
            local_dt = datetime.now(timezone.utc).astimezone(ZoneInfo(getattr(self, "_tz_name", "Asia/Manila")))
        except Exception:
            local_dt = datetime.now(timezone.utc) + timedelta(hours=8)
        self.cell(
            0, 10,
            _s(f"Generated: {local_dt.strftime('%m/%d/%Y %I:%M %p')}  |  Page {self.page_no()}/{{nb}}"),
            align="C",
        )

    # ── building blocks ──────────────────────────────────────────────────────
    def section_header(self, title: str, total: str = "", accent: tuple = (240, 245, 240)):
        self.set_font("Helvetica", "B", 10)
        self.set_fill_color(*accent)
        self.set_text_color(26, 77, 46)
        self.cell(150, 7, _s(f"  {title}"), fill=True)
        if total:
            self.set_font("Helvetica", "B", 10)
            self.cell(40, 7, _s(total), align="R", fill=True)
        self.ln(8)
        self.set_text_color(30, 30, 30)

    def row(self, label: str, value: str, bold: bool = False, indent: int = 0, color: tuple = None):
        self.set_font("Helvetica", "B" if bold else "", 9)
        if color:
            self.set_text_color(*color)
        x = 12 + indent
        self.set_x(x)
        self.cell(150 - indent, 5, _s(label))
        self.cell(40, 5, _s(value), align="R")
        self.ln(5)
        self.set_text_color(30, 30, 30)

    def detail_row(self, col1: str, col2: str, col3: str, col4: str = "", header: bool = False):
        f = "B" if header else ""
        self.set_font("Helvetica", f, 8)
        if header:
            self.set_fill_color(248, 248, 248)
        self.set_x(14)
        self.cell(65, 5, _s(col1), fill=header)
        self.cell(40, 5, _s(col2), fill=header)
        self.cell(35, 5, _s(col3), align="R", fill=header)
        if col4:
            self.cell(30, 5, _s(col4), align="R", fill=header)
        self.ln(5)

    def separator(self):
        y = self.get_y()
        self.set_draw_color(200, 200, 200)
        self.line(12, y, 198, y)
        self.ln(2)

    def column_header(self, cols):
        """cols = [(label, width, align)]"""
        self.set_font("Helvetica", "B", 8)
        self.set_fill_color(248, 248, 248)
        self.set_x(14)
        for label, w, align in cols:
            self.cell(w, 5, _s(label), align=align, fill=True)
        self.ln(5)

    def data_row(self, cells, colors=None):
        """cells = [(text, width, align)]; colors optional list of RGB tuples."""
        self.set_font("Helvetica", "", 8)
        self.set_x(14)
        for i, (text, w, align) in enumerate(cells):
            if colors and colors[i]:
                self.set_text_color(*colors[i])
            self.cell(w, 5, _s(text), align=align)
            self.set_text_color(30, 30, 30)
        self.ln(5)


# ── Layout: NORMAL (compact) — mirrors on-screen Normal Z-Report ────────────
def render_normal(pdf: ZReportPDF, closing: dict):
    """
    Mirrors the Normal <ZReport> UI in DailyLogPage.js:
      - Opening (Safe Balance, Opening Float)
      - Cash Reconciliation (Expected, Actual, Over/Short, to Vault, Float Tomorrow)
      - Walk-in Sales by Category
      - Credit Extended Today (credit_sales_today + ar_credits_today)
      - AR Payments Received (credit_collections)
      - AR Balance at Close
      - Expenses
    """
    starting_float = float(closing.get("starting_float", 0))
    safe_balance = float(closing.get("safe_balance", 0))
    expected = float(closing.get("expected_counter", 0))
    actual = float(closing.get("actual_cash", 0))
    over_short = float(closing.get("over_short", 0))
    cash_to_safe = float(closing.get("cash_to_safe", 0))
    cash_to_drawer = float(closing.get("cash_to_drawer", 0))

    # 1. Opening
    pdf.section_header("OPENING")
    pdf.row("Safe Balance", php(safe_balance), bold=True)
    pdf.row("Opening Float", php(starting_float), bold=True, color=(21, 128, 61))
    pdf.ln(2)

    # 2. Cash Reconciliation
    pdf.section_header("CASH RECONCILIATION")
    pdf.row("Expected in Counter", php(expected))
    pdf.row("Actual Cash Counted", php(actual), bold=True)
    pdf.separator()
    os_label = "Cash Over" if over_short >= 0 else "Cash Short"
    os_color = (21, 128, 61) if over_short >= 0 else (220, 38, 38)
    pdf.row(os_label, php_signed(over_short), bold=True, color=os_color)
    pdf.row("Transferred to Vault", php(cash_to_safe))
    pdf.row("Opening Float (Next Day)", php(cash_to_drawer), bold=True, color=(21, 128, 61))
    pdf.ln(2)

    # 3. Walk-in Sales by Category
    sales_by_cat = closing.get("sales_by_category") or {}
    total_cash_sales = float(closing.get("total_cash_sales", 0))
    pdf.section_header("WALK-IN SALES", php(total_cash_sales), accent=(236, 253, 245))
    if sales_by_cat:
        for cat, total in sales_by_cat.items():
            pdf.row(str(cat or "General"), php(total))
    else:
        pdf.row("(no walk-in sales recorded)", "", color=(150, 150, 150))
    pdf.ln(2)

    # 4. New Credit Extended Today
    credit_sales = closing.get("credit_sales_today") or []
    ar_credits = closing.get("ar_credits_today") or []
    total_new_credit = float(closing.get("total_new_credit", 0))
    if credit_sales or ar_credits:
        pdf.section_header("NEW CREDIT EXTENDED TODAY", php(total_new_credit), accent=(254, 249, 231))
        pdf.column_header([
            ("Customer", 55, "L"), ("Invoice", 40, "L"),
            ("Amount", 35, "R"), ("Balance", 30, "R"), ("Type", 26, "C"),
        ])
        for c in credit_sales:
            pdf.data_row([
                (str(c.get("customer_name", ""))[:32], 55, "L"),
                (str(c.get("invoice_number", ""))[:22], 40, "L"),
                (php(c.get("grand_total", 0)), 35, "R"),
                (php(c.get("balance", 0)), 30, "R"),
                ("Credit", 26, "C"),
            ])
        for c in ar_credits:
            typ = "Cash-out" if c.get("type") == "cash_advance" else "Farm"
            pdf.data_row([
                (str(c.get("customer_name", ""))[:32], 55, "L"),
                (str(c.get("invoice_number", ""))[:22], 40, "L"),
                (php(c.get("grand_total", 0)), 35, "R"),
                ("-", 30, "R"),
                (typ, 26, "C"),
            ])
        pdf.ln(2)

    # 5. AR Payments Received
    credit_collections = closing.get("credit_collections") or []
    total_ar_received = float(closing.get("total_ar_received", 0))
    if credit_collections:
        pdf.section_header("AR PAYMENTS RECEIVED", php(total_ar_received), accent=(239, 246, 255))
        pdf.column_header([
            ("Customer / Inv.", 60, "L"),
            ("Bal Before", 30, "R"), ("Interest", 25, "R"),
            ("Penalty", 25, "R"), ("Paid", 25, "R"), ("Remaining", 21, "R"),
        ])
        for p in credit_collections:
            cust = f"{p.get('customer', '') or ''} / {p.get('invoice', '') or ''}"
            pdf.data_row([
                (cust[:38], 60, "L"),
                (php(p.get("balance_before", 0)), 30, "R"),
                (php(p.get("interest_paid", 0)) if float(p.get("interest_paid", 0)) > 0 else "-", 25, "R"),
                (php(p.get("penalty_paid", 0)) if float(p.get("penalty_paid", 0)) > 0 else "-", 25, "R"),
                (php(p.get("total_paid", 0)), 25, "R"),
                (php(p.get("balance", 0)), 21, "R"),
            ])
        pdf.ln(2)

    # 6. AR Balance at Close
    if "total_ar_at_close" in closing:
        pdf.section_header("AR BALANCE AT CLOSE", php(closing.get("total_ar_at_close", 0)), accent=(238, 242, 255))
        pdf.set_font("Helvetica", "", 8)
        pdf.set_x(14)
        pdf.cell(0, 5, _s(f"Total outstanding AR at close of {closing.get('date', '')}"))
        pdf.ln(6)

    # 7. Expenses
    expenses = closing.get("expenses") or []
    total_expenses = float(closing.get("total_expenses", 0))
    if expenses or total_expenses:
        pdf.section_header("EXPENSES", php(total_expenses), accent=(254, 242, 242))
        for e in expenses:
            cat = str(e.get("category", ""))[:20]
            desc = str(e.get("description") or e.get("employee_name") or "")[:50]
            label = f"[{cat}] {desc}" if cat else desc
            pdf.row(label, php(e.get("amount", 0)), color=(220, 38, 38))
            # Late-encoded carryover note (printed in italics if backdated)
            if e.get("late_encoded") and e.get("late_encode_label"):
                pdf.set_font("Helvetica", "I", 7)
                pdf.set_text_color(217, 119, 6)
                pdf.cell(0, 3.5, f"   {e['late_encode_label']}", new_x="LMARGIN", new_y="NEXT")
                pdf.set_text_color(0, 0, 0)
            if e.get("category") == "Employee Advance" and "monthly_ca_total" in e:
                emp = e.get("employee_name", "Employee")
                mt = php(e.get("monthly_ca_total", 0))
                limit = e.get("monthly_ca_limit", 0)
                over = e.get("is_over_ca")
                note = f"   {emp} - monthly CA: {mt}"
                if limit and float(limit) > 0:
                    note += f" / limit {php(limit)}"
                if over:
                    note += "  [OVER CA]"
                if e.get("manager_approved_by"):
                    note += f"  (Approved: {e['manager_approved_by']})"
                pdf.set_font("Helvetica", "I", 7)
                pdf.set_text_color(150, 150, 150)
                pdf.set_x(14)
                pdf.cell(0, 4, _s(note))
                pdf.ln(5)
                pdf.set_text_color(30, 30, 30)


# ── Layout: DETAILED — mirrors Close Wizard Step 7 ──────────────────────────
def render_detailed(pdf: ZReportPDF, preview: dict, closing: Optional[dict]):
    """Rich breakdown read from /daily-close-preview."""
    expected = float(preview.get("expected_counter", 0))

    # 1. Cash Drawer Reconciliation
    pdf.section_header("CASH DRAWER RECONCILIATION")
    pdf.row("Opening Float", php(preview.get("starting_float", 0)))
    pdf.separator()
    pdf.row("+ Cash Sales", php(preview.get("total_cash_sales", 0)), color=(21, 128, 61))
    if float(preview.get("total_split_cash", 0)) > 0:
        pdf.row("+ Split Payment Cash", php(preview.get("total_split_cash", 0)), color=(13, 148, 136))
    if float(preview.get("total_partial_cash", 0)) > 0:
        pdf.row("+ Partial Cash Received", php(preview.get("total_partial_cash", 0)), color=(37, 99, 235))
    pdf.row(
        "+ AR Cash Payments",
        php(preview.get("total_cash_ar", preview.get("total_ar_received", 0))),
        color=(79, 70, 229),
    )
    if float(preview.get("total_digital_ar", 0)) > 0:
        pdf.row("  (AR Digital - not in drawer)", php(preview.get("total_digital_ar", 0)), indent=6, color=(150, 150, 150))

    net_ft = float(preview.get("net_fund_transfers", 0))
    if net_ft != 0:
        pdf.separator()
        if float(preview.get("capital_to_cashier", 0)) > 0:
            pdf.row("+ Capital Injection", php(preview.get("capital_to_cashier", 0)), color=(8, 145, 178))
        if float(preview.get("safe_to_cashier", 0)) > 0:
            pdf.row("+ Safe -> Cashier", php(preview.get("safe_to_cashier", 0)), color=(8, 145, 178))
        if float(preview.get("cashier_to_safe", 0)) > 0:
            pdf.row("- Cashier -> Safe", php(preview.get("cashier_to_safe", 0)), color=(234, 88, 12))

    pdf.separator()
    pdf.row("= Total Cash In", php(preview.get("total_cash_in", 0)), bold=True, color=(21, 128, 61))
    pdf.row(
        "- Cashier Expenses",
        php(preview.get("total_cashier_expenses", preview.get("total_expenses", 0))),
        color=(220, 38, 38),
    )
    if float(preview.get("total_safe_expenses", 0)) > 0:
        pdf.row("  (Safe expenses - not from drawer)", php(preview.get("total_safe_expenses", 0)), indent=6, color=(150, 150, 150))

    pdf.separator()
    pdf.row("Expected in Drawer", php(expected), bold=True)
    if closing:
        actual_cash = float(closing.get("actual_cash", 0))
        over_short = float(closing.get("over_short", 0))
        cash_to_safe = float(closing.get("cash_to_safe", 0))
        cash_to_drawer = float(closing.get("cash_to_drawer", 0))
        pdf.row("Actual Count", php(actual_cash), bold=True)
        color = (21, 128, 61) if over_short >= 0 else (220, 38, 38)
        pdf.row("Over / Short", php_signed(over_short), bold=True, color=color)
        pdf.ln(2)
        pdf.row("To Vault/Safe", php(cash_to_safe))
        pdf.row("Float Tomorrow", php(cash_to_drawer))
    pdf.ln(3)

    # 2. AR Payments Detail
    ar_payments = preview.get("ar_payments", [])
    if ar_payments:
        pdf.section_header("AR PAYMENTS RECEIVED", php(preview.get("total_ar_received", 0)))
        pdf.detail_row("Customer", "Invoice #", "Amount", "Method", header=True)
        for p in ar_payments:
            method = p.get("method") or ("Cash" if p.get("fund_source") == "cashier" else "Digital")
            pdf.detail_row(
                str(p.get("customer_name", ""))[:30],
                str(p.get("invoice_number", "")),
                php(p.get("amount_paid", 0)),
                str(method),
            )
        pdf.ln(3)

    # 3. Interest & Penalty Invoices  (placed directly under AR Payments so the
    #    owner can immediately see what NEW receivables were created today
    #    while reviewing the AR collections that came in.)
    int_invs = preview.get("interest_invoices_today") or []
    if int_invs:
        int_total = sum(float(i.get("grand_total", 0)) for i in int_invs)
        pdf.section_header("INTEREST & PENALTY INVOICES ISSUED TODAY", php(int_total))
        pdf.detail_row("Customer", "Invoice #", "Amount", "Type", header=True)
        for inv in int_invs:
            inv_type = (
                "Penalty" if inv.get("sale_type") == "penalty_charge"
                else "Manual INT" if inv.get("manual_interest")
                else "Interest"
            )
            pdf.detail_row(
                str(inv.get("customer_name", ""))[:30],
                str(inv.get("invoice_number", "")),
                php(inv.get("grand_total", 0)),
                inv_type,
            )
        pdf.ln(3)

    # 4. Fund Transfers
    fund_transfers = preview.get("fund_transfers_today", [])
    if fund_transfers:
        pdf.section_header("FUND TRANSFERS", php(net_ft))
        pdf.detail_row("Type", "Authorized By", "Amount", "Note", header=True)
        for ft in fund_transfers:
            ft_type = ft.get("type", "")
            label = (
                "Capital Injection" if ft_type == "capital_add"
                else "Safe -> Cashier" if ft_type == "safe_to_cashier"
                else "Cashier -> Safe"
            )
            pdf.detail_row(
                label,
                str(ft.get("authorized_by", ""))[:25],
                php(ft.get("amount", 0)),
                str(ft.get("note", ""))[:20],
            )
        pdf.ln(3)

    # 5. Expenses Detail
    expenses = preview.get("expenses", [])
    cashier_exps = [e for e in expenses if e.get("fund_source") != "safe"]
    safe_exps = [e for e in expenses if e.get("fund_source") == "safe"]

    if cashier_exps:
        pdf.section_header("CASHIER EXPENSES (from drawer)", php(preview.get("total_cashier_expenses", 0)))
        pdf.detail_row("Description", "Category", "Amount", "", header=True)
        for e in cashier_exps:
            desc = str(e.get("description", e.get("category", "")))[:35]
            if e.get("employee_name"):
                desc += f" ({e['employee_name']})"
            pdf.detail_row(desc[:40], str(e.get("category", ""))[:20], php(e.get("amount", 0)))
        pdf.ln(3)

    if safe_exps:
        pdf.section_header("SAFE EXPENSES (not from drawer)", php(preview.get("total_safe_expenses", 0)))
        pdf.detail_row("Description", "Category", "Amount", "", header=True)
        for e in safe_exps:
            pdf.detail_row(str(e.get("description", ""))[:40], str(e.get("category", ""))[:20], php(e.get("amount", 0)))
        pdf.ln(3)

    # 6. Credit Extended Today
    credit_sales = preview.get("credit_sales_today", [])
    if credit_sales:
        pdf.section_header("CREDIT EXTENDED TODAY", php(preview.get("total_credit_today", 0)))
        pdf.detail_row("Customer", "Invoice #", "Balance", "Type", header=True)
        for inv in credit_sales:
            pdf.detail_row(
                str(inv.get("customer_name", ""))[:30],
                str(inv.get("invoice_number", "")),
                php(inv.get("balance", inv.get("grand_total", 0))),
                str(inv.get("payment_type", "credit")).title(),
            )
        pdf.ln(3)

    # 7. Digital/E-Wallet Payments
    digital_sales = preview.get("digital_sales_today", [])
    if digital_sales:
        pdf.section_header("E-WALLET / DIGITAL PAYMENTS", php(preview.get("total_digital_today", 0)))
        pdf.detail_row("Customer", "Invoice #", "Amount", "Platform", header=True)
        for d in digital_sales:
            pdf.detail_row(
                str(d.get("customer_name") or "Walk-in")[:30],
                str(d.get("invoice_number", "")),
                php(d.get("amount", 0)),
                str(d.get("platform") or "Digital")[:15],
            )
        pdf.separator()
        for platform, amt in (preview.get("digital_by_platform") or {}).items():
            pdf.row(f"  {platform} Total", php(amt), indent=6)
        pdf.ln(3)

    # 8. Cash Sales by Category
    categories = preview.get("cash_sales_by_category", [])
    if categories:
        pdf.section_header("CASH SALES BY CATEGORY", php(preview.get("total_cash_sales", 0)))
        pdf.detail_row("Category", "Items Sold", "Total", "", header=True)
        for cat in categories:
            pdf.detail_row(str(cat.get("category", "General"))[:30], str(cat.get("qty", 0)), php(cat.get("total", 0)))
        pdf.ln(3)

    # 9. Discount Breakdown
    disc = preview.get("discount_breakdown") or {}
    disc_products = disc.get("products") or []
    if disc_products or float(disc.get("total_discount", 0)) > 0:
        pdf.section_header("DISCOUNTS GIVEN TODAY", php(disc.get("total_discount", 0)))
        if disc_products:
            pdf.detail_row("Product", "Units", "Total Disc.", "Avg/Unit", header=True)
            for p in disc_products:
                pdf.detail_row(
                    str(p.get("product_name", ""))[:35],
                    str(p.get("units_sold", 0)),
                    php(p.get("total_discount", 0)),
                    php(p.get("avg_discount_per_unit", 0)),
                )
        if float(disc.get("total_overall_discount", 0)) > 0:
            pdf.row("Overall (invoice-level) discounts", php(disc.get("total_overall_discount", 0)), indent=6)
        pdf.ln(3)

    # 10. Permanent Price Changes — grouped by category, alphabetical, with
    #     capital references (moving avg + last purchase). Per-row coloring:
    #     GREEN when the new price is higher than the old (margin up), RED
    #     when lower (margin compressed). Helps the owner spot suspicious
    #     downward overrides at a glance.
    price_changes = (preview.get("price_changes_today") or {}).get("rows") or []
    if price_changes:
        pdf.section_header(
            "PERMANENT PRICE CHANGES",
            f"{(preview.get('price_changes_today') or {}).get('count', len(price_changes))} change(s)",
        )

        # Column layout: Product (52) | Cap MA (22) | Last Pur (22) | From (22) | -> (6) | To (22) | OK by (42) = 188mm
        # Capital reference columns sit right next to the product so the eye
        # reads "what it costs us" before "what we sold it for".
        def _pc_header():
            pdf.set_font("Helvetica", "B", 8)
            pdf.set_fill_color(248, 248, 248)
            pdf.set_x(14)
            pdf.cell(52, 5, _s("Product"), fill=True)
            pdf.cell(22, 5, _s("Cap MA"), align="R", fill=True)
            pdf.cell(22, 5, _s("Last Pur"), align="R", fill=True)
            pdf.cell(22, 5, _s("From"), align="R", fill=True)
            pdf.cell(6, 5, _s(""), fill=True)
            pdf.cell(22, 5, _s("To"), align="R", fill=True)
            pdf.cell(42, 5, _s("OK by"), fill=True)
            pdf.ln(5)

        # Group rows by category (already sorted by backend (category, name))
        from itertools import groupby
        for cat, group in groupby(
            price_changes,
            key=lambda r: r.get("category") or "Uncategorized",
        ):
            group = list(group)
            # Category sub-header
            pdf.set_font("Helvetica", "B", 8)
            pdf.set_text_color(26, 77, 46)
            pdf.set_fill_color(236, 245, 240)
            pdf.set_x(14)
            pdf.cell(0, 5, _s(f"  {cat}  ({len(group)})"), fill=True)
            pdf.ln(5)
            pdf.set_text_color(30, 30, 30)
            _pc_header()

            for r in group:
                old_p = float(r.get("old_price") or 0)
                new_p = float(r.get("new_price") or 0)
                price_color = (
                    (21, 128, 61) if new_p > old_p
                    else (220, 38, 38) if new_p < old_p
                    else (30, 30, 30)
                )
                ma = float(r.get("moving_average_cost") or 0)
                lp = float(r.get("last_purchase_cost") or 0)
                lp_source = r.get("last_purchase_source", "branch")

                pdf.set_font("Helvetica", "", 8)
                pdf.set_x(14)
                # Product
                pdf.set_text_color(30, 30, 30)
                pdf.cell(52, 5, _s(str(r.get("product_name", ""))[:32]))
                # Cap MA (branch-specific; dash if no branch history)
                pdf.set_text_color(80, 80, 80)
                pdf.cell(22, 5, _s(php(ma) if ma > 0 else "-"), align="R")
                # Last Pur (branch first, global fallback). Italic when the
                # displayed value is the global cost_price fallback so the
                # owner can tell it isn't a real branch receipt.
                if lp > 0 and lp_source == "global":
                    pdf.set_font("Helvetica", "I", 8)
                    pdf.cell(22, 5, _s(f"{php(lp)}*"), align="R")
                    pdf.set_font("Helvetica", "", 8)
                else:
                    pdf.cell(22, 5, _s(php(lp) if lp > 0 else "-"), align="R")
                # From (struck-through visually impossible in fpdf — italic instead)
                pdf.set_text_color(120, 120, 120)
                pdf.set_font("Helvetica", "I", 8)
                pdf.cell(22, 5, _s(php(old_p)), align="R")
                # arrow
                pdf.set_font("Helvetica", "", 8)
                pdf.set_text_color(120, 120, 120)
                pdf.cell(6, 5, _s("->"), align="C")
                # To — colored
                pdf.set_text_color(*price_color)
                pdf.set_font("Helvetica", "B", 8)
                pdf.cell(22, 5, _s(php(new_p)), align="R")
                # OK by
                pdf.set_font("Helvetica", "", 8)
                pdf.set_text_color(30, 30, 30)
                pdf.cell(42, 5, _s(str(r.get("approver_name", ""))[:24]))
                pdf.ln(5)
                pdf.set_text_color(30, 30, 30)
            pdf.ln(1)
        # Footnote for the global-cost fallback marker used above.
        if any((r.get("last_purchase_source") == "global") for r in price_changes):
            pdf.set_font("Helvetica", "I", 7)
            pdf.set_text_color(150, 150, 150)
            pdf.set_x(14)
            pdf.cell(0, 4, _s("  * Last Purchase shown is the product's global cost (no branch purchase history yet)."))
            pdf.ln(5)
            pdf.set_text_color(30, 30, 30)
        pdf.ln(2)


# ── Endpoint ─────────────────────────────────────────────────────────────────
@router.get("/z-report-pdf")
async def generate_z_report_pdf(
    date: Optional[str] = None,
    branch_id: Optional[str] = None,
    closing_id: Optional[str] = None,
    detailed: bool = False,
    user=Depends(get_current_user),
):
    """Generate a Z-Report PDF.

    Normal (`detailed=False`, default) mirrors the on-screen Normal Z-Report:
    Opening, Cash Reconciliation, Walk-in Sales by Category, Credit Extended,
    AR Payments, AR Balance, Expenses.

    Detailed (`detailed=True`) mirrors Close Wizard Step 7 with the full
    breakdown (AR by method, fund transfers, per-category expenses, discounts,
    price changes, interest/penalty invoices).
    """
    check_perm(user, "reports", "view")

    # Resolve closing record. Support lookup by closing_id OR by date+branch_id
    # (the frontend calls the endpoint with the latter — prior code only
    # looked up by closing_id, leaving the PDF without actual-count / over-short
    # / cash-to-safe values for closed days).
    closing = None
    if closing_id:
        closing = await db.daily_closings.find_one({"id": closing_id}, {"_id": 0})
        if not closing:
            raise HTTPException(404, "Closing record not found")
        date = closing["date"]
        branch_id = closing["branch_id"]

    if not date:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if not branch_id:
        branch_id = user.get("branch_id", "")

    if not closing:
        closing = await db.daily_closings.find_one(
            {"branch_id": branch_id, "date": date, "status": "closed"},
            {"_id": 0},
        )

    branch = await db.branches.find_one({"id": branch_id}, {"_id": 0, "name": 1})
    branch_name = branch.get("name", branch_id) if branch else branch_id

    # Resolve company name (settings.company_info → fallback to organization.name)
    company_name = ""
    try:
        biz_doc = await db.settings.find_one({"key": "company_info"}, {"_id": 0})
        company_name = (biz_doc or {}).get("value", {}).get("name", "") or ""
        if not company_name:
            from config import db as _raw_db, get_org_context
            org_id = get_org_context() or user.get("organization_id")
            if org_id:
                org = await _raw_db.organizations.find_one({"id": org_id}, {"_id": 0, "name": 1})
                company_name = (org or {}).get("name", "") if org else ""
    except Exception:
        company_name = ""

    cashier = (
        (closing.get("closed_by_name") or closing.get("closed_by", "") if closing else "")
        or user.get("full_name")
        or user.get("username")
        or ""
    )

    mode_label = "DETAILED" if detailed else "COMPACT"
    pdf = ZReportPDF(branch_name, date, cashier, mode_label=mode_label, company_name=company_name)
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()

    try:
        if detailed:
            from routes.daily_operations import get_daily_close_preview
            try:
                preview = await get_daily_close_preview(user=user, branch_id=branch_id, date=date)
            except Exception as e:
                if closing:
                    preview = closing
                else:
                    raise HTTPException(400, f"Failed to generate detailed preview: {e}")
            render_detailed(pdf, preview, closing)
        else:
            # Normal layout — if a closed day exists, use it (matches UI 1:1).
            # For open days (no closing yet), use the preview shaped into the
            # same field names so the layout still renders cleanly.
            if closing:
                render_normal(pdf, closing)
            else:
                from routes.daily_operations import get_daily_close_preview
                try:
                    preview = await get_daily_close_preview(user=user, branch_id=branch_id, date=date)
                except Exception as e:
                    raise HTTPException(400, f"Failed to generate preview data: {e}")
                # Map preview fields into the closing shape used by render_normal.
                cats = preview.get("cash_sales_by_category") or []
                mapped = {
                    "date": date,
                    "starting_float": preview.get("starting_float", 0),
                    "safe_balance": preview.get("safe_balance", 0),
                    "expected_counter": preview.get("expected_counter", 0),
                    "actual_cash": 0,
                    "over_short": 0,
                    "cash_to_safe": 0,
                    "cash_to_drawer": 0,
                    "total_cash_sales": preview.get("total_cash_sales", 0),
                    "sales_by_category": {c.get("category", "General"): c.get("total", 0) for c in cats},
                    "credit_sales_today": preview.get("credit_sales_today") or [],
                    "ar_credits_today": preview.get("ar_credits_today") or [],
                    "total_new_credit": preview.get("total_credit_today", 0),
                    "credit_collections": [
                        {
                            "customer": p.get("customer_name", ""),
                            "invoice": p.get("invoice_number", ""),
                            "balance_before": p.get("balance_before", 0),
                            "interest_paid": p.get("interest_paid", 0),
                            "penalty_paid": p.get("penalty_paid", 0),
                            "total_paid": p.get("amount_paid", 0),
                            "balance": p.get("remaining_balance", 0),
                        }
                        for p in (preview.get("ar_payments") or [])
                    ],
                    "total_ar_received": preview.get("total_ar_received", 0),
                    "expenses": preview.get("expenses") or [],
                    "total_expenses": preview.get("total_expenses", 0),
                }
                render_normal(pdf, mapped)
    except HTTPException:
        raise
    except Exception as e:
        # Include the mode in the error so we can tell Compact vs Detailed apart.
        raise HTTPException(500, f"Failed to render {mode_label.lower()} PDF: {type(e).__name__}: {e}")

    # Footer summary bar
    pdf.separator()
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(26, 77, 46)
    pdf.set_text_color(255, 255, 255)
    gross = (
        float(closing.get("total_cash_sales", 0) if closing else 0)
        + float(closing.get("total_digital_today", 0) if closing else 0)
        + float(closing.get("total_new_credit", 0) if closing else 0)
    )
    expected_drawer = float(closing.get("expected_counter", 0) if closing else 0)
    safe_bal = float(closing.get("safe_balance", 0) if closing else 0)
    # Iter 243.4 — show BEFORE and AFTER allocation so the printed footer
    # reflects what's actually in the drawer & safe at the moment of closing,
    # not the pre-allocation snapshot. Owners were getting confused because
    # `expected_drawer` (pre-Step-6) was being read as "current drawer".
    cash_to_safe = float(closing.get("cash_to_safe", 0) if closing else 0)
    cash_to_drawer = float(closing.get("cash_to_drawer", 0) if closing else 0)
    final_drawer = cash_to_drawer if cash_to_drawer > 0 else expected_drawer
    final_safe = safe_bal + cash_to_safe
    pdf.cell(
        0, 7,
        _s(f"  Gross Sales: {php(gross)}   |   Drawer at Close: {php(expected_drawer)} -> {php(final_drawer)}   |   Safe: {php(safe_bal)} -> {php(final_safe)}"),
        fill=True,
    )
    pdf.ln(7)
    # Net sales (gross profit) line — separate row so it doesn't crowd the cash bar
    net_sales_data = closing.get("net_sales_today") or {} if closing else {}
    if net_sales_data:
        pdf.set_fill_color(34, 100, 60)
        pdf.cell(
            0, 6,
            _s(f"  Net Sales: {php(net_sales_data.get('net_sales', 0))}   (COGS {php(net_sales_data.get('cogs', 0))})"),
            fill=True,
        )
        pdf.ln(8)
    else:
        pdf.ln(3)

    if closing:
        pdf.set_text_color(100, 100, 100)
        pdf.set_font("Helvetica", "", 8)
        pdf.cell(
            0, 5,
            _s(f"Day closed by: {cashier}  |  Closing ID: {closing.get('id', 'N/A')}"),
            align="C",
        )

    buf = BytesIO()
    pdf.output(buf)
    buf.seek(0)

    # Filename pattern: <Company>_<Branch>_<YYYY-MM-DD>_ZReport[_DETAILED].pdf
    # Slugify each part — keep alphanumerics, dashes, underscores; collapse spaces.
    import re
    def _slug(s: str) -> str:
        s = _s(s).strip()
        s = re.sub(r"[^A-Za-z0-9_\-]+", "_", s)
        s = re.sub(r"_+", "_", s).strip("_")
        return s or "Unknown"

    parts = []
    if company_name:
        parts.append(_slug(company_name))
    parts.append(_slug(branch_name))
    parts.append(date)
    parts.append("ZReport_DETAILED" if detailed else "ZReport")
    filename = "_".join(parts) + ".pdf"
    return StreamingResponse(
        buf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

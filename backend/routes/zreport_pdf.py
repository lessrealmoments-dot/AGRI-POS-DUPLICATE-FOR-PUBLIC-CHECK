"""
Z-Report PDF Generator — Matches Closing Wizard UI detail level.
Endpoint: GET /api/reports/z-report-pdf?date=YYYY-MM-DD&branch_id=xxx
"""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from typing import Optional
from datetime import datetime, timezone
from io import BytesIO
from fpdf import FPDF
from config import db
from utils import get_current_user, check_perm

router = APIRouter(prefix="/reports", tags=["Reports"])


class ZReportPDF(FPDF):
    def __init__(self, branch_name: str, date: str, cashier: str):
        super().__init__()
        self.branch_name = branch_name
        self.report_date = date
        self.cashier_name = cashier

    def header(self):
        self.set_font("Helvetica", "B", 16)
        self.set_text_color(26, 77, 46)
        self.cell(0, 8, "AgriBooks Z-Report", align="C", new_x="LMARGIN", new_y="NEXT")
        self.set_font("Helvetica", "", 9)
        self.set_text_color(100, 100, 100)
        self.cell(0, 5, f"{self.branch_name}  |  {self.report_date}  |  Prepared by: {self.cashier_name}", align="C", new_x="LMARGIN", new_y="NEXT")
        self.line(10, self.get_y() + 2, 200, self.get_y() + 2)
        self.ln(5)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 7)
        self.set_text_color(150, 150, 150)
        self.cell(0, 10, f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}  |  Page {self.page_no()}/{{nb}}", align="C")

    def section_header(self, title: str, total: str = ""):
        self.set_font("Helvetica", "B", 10)
        self.set_fill_color(240, 245, 240)
        self.set_text_color(26, 77, 46)
        self.cell(150, 7, f"  {title}", fill=True)
        if total:
            self.set_font("Helvetica", "B", 10)
            self.cell(40, 7, total, align="R", fill=True)
        self.ln(8)
        self.set_text_color(30, 30, 30)

    def row(self, label: str, value: str, bold: bool = False, indent: int = 0, color: tuple = None):
        self.set_font("Helvetica", "B" if bold else "", 9)
        if color:
            self.set_text_color(*color)
        x = 12 + indent
        self.set_x(x)
        self.cell(150 - indent, 5, label)
        self.cell(40, 5, value, align="R")
        self.ln(5)
        self.set_text_color(30, 30, 30)

    def detail_row(self, col1: str, col2: str, col3: str, col4: str = "", header: bool = False):
        f = "B" if header else ""
        self.set_font("Helvetica", f, 8)
        if header:
            self.set_fill_color(248, 248, 248)
        self.set_x(14)
        self.cell(65, 5, col1, fill=header)
        self.cell(40, 5, col2, fill=header)
        self.cell(35, 5, col3, align="R", fill=header)
        if col4:
            self.cell(30, 5, col4, align="R", fill=header)
        self.ln(5)

    def separator(self):
        y = self.get_y()
        self.set_draw_color(200, 200, 200)
        self.line(12, y, 198, y)
        self.ln(2)


def php(n):
    return f"P{abs(float(n or 0)):,.2f}"


@router.get("/z-report-pdf")
async def generate_z_report_pdf(
    date: Optional[str] = None,
    branch_id: Optional[str] = None,
    closing_id: Optional[str] = None,
    detailed: bool = False,
    user=Depends(get_current_user),
):
    """Generate a Z-Report PDF.

    `detailed=False` (default) → compact Z-Report — only the Cash Drawer
    Reconciliation block + footer. Mirrors the on-screen "Normal" view.

    `detailed=True` → full breakdown (AR-by-method, Fund Transfers, Cashier
    & Safe Expenses, Credit Extended, Digital/E-Wallet, Cash Sales by
    Category, Discounts, Price Changes, Interest/Penalty Invoices). Mirrors
    the on-screen "Detailed" view & Close Wizard Step 7.
    """
    check_perm(user, "reports", "view")

    # If closing_id provided, pull data from the closing record
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

    # Get branch name
    branch = await db.branches.find_one({"id": branch_id}, {"_id": 0, "name": 1})
    branch_name = branch.get("name", branch_id) if branch else branch_id

    # Get preview data (reuse existing logic)
    from routes.daily_operations import get_daily_close_preview
    try:
        preview = await get_daily_close_preview(user=user, branch_id=branch_id, date=date)
    except Exception as e:
        # If preview fails, build a minimal preview from closing record
        if closing:
            preview = closing
        else:
            raise HTTPException(400, f"Failed to generate preview data: {e}")

    # If we have a closing record, use its actual counts
    actual_cash = 0
    cash_to_safe = 0
    cash_to_drawer = 0
    over_short = 0
    closed_by = ""
    if closing:
        actual_cash = float(closing.get("actual_cash", 0))
        cash_to_safe = float(closing.get("cash_to_safe", 0))
        cash_to_drawer = float(closing.get("cash_to_drawer", 0))
        over_short = float(closing.get("over_short", 0))
        closed_by = closing.get("closed_by_name", closing.get("closed_by", ""))

    cashier = closed_by or user.get("full_name", user.get("username", ""))

    # ── Build PDF ─────────────────────────────────────────────────────────
    pdf = ZReportPDF(branch_name, date, cashier)
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()

    # ── 1. Cash Drawer Reconciliation ─────────────────────────────────────
    expected = float(preview.get("expected_counter", 0))
    pdf.section_header("CASH DRAWER RECONCILIATION")

    pdf.row("Opening Float", php(preview.get("starting_float", 0)))
    pdf.separator()

    pdf.row("+ Cash Sales", php(preview.get("total_cash_sales", 0)), color=(21, 128, 61))
    if float(preview.get("total_split_cash", 0)) > 0:
        pdf.row("+ Split Payment Cash", php(preview.get("total_split_cash", 0)), color=(13, 148, 136))
    if float(preview.get("total_partial_cash", 0)) > 0:
        pdf.row("+ Partial Cash Received", php(preview.get("total_partial_cash", 0)), color=(37, 99, 235))
    pdf.row("+ AR Cash Payments", php(preview.get("total_cash_ar", preview.get("total_ar_received", 0))), color=(79, 70, 229))
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
    pdf.row("- Cashier Expenses", php(preview.get("total_cashier_expenses", preview.get("total_expenses", 0))), color=(220, 38, 38))
    if float(preview.get("total_safe_expenses", 0)) > 0:
        pdf.row("  (Safe expenses - not from drawer)", php(preview.get("total_safe_expenses", 0)), indent=6, color=(150, 150, 150))

    pdf.separator()
    pdf.row("Expected in Drawer", php(expected), bold=True)
    if closing:
        pdf.row("Actual Count", php(actual_cash), bold=True)
        color = (21, 128, 61) if over_short >= 0 else (220, 38, 38)
        sign = "+" if over_short > 0 else ""
        pdf.row("Over / Short", f"{sign}{php(over_short)}", bold=True, color=color)
        pdf.ln(2)
        pdf.row("To Vault/Safe", php(cash_to_safe))
        pdf.row("Float Tomorrow", php(cash_to_drawer))
    pdf.ln(3)

    # ── 2. AR Cash Payments Detail (DETAILED MODE ONLY) ──────────────────
    if detailed:
        ar_payments = preview.get("ar_payments", [])
        if ar_payments:
            pdf.section_header("AR PAYMENTS RECEIVED", php(preview.get("total_ar_received", 0)))
            pdf.detail_row("Customer", "Invoice #", "Amount", "Method", header=True)
            for p in ar_payments:
                method = p.get("method", "Cash" if p.get("fund_source") == "cashier" else "Digital")
                pdf.detail_row(
                    str(p.get("customer_name", ""))[:30],
                    str(p.get("invoice_number", "")),
                    php(p.get("amount_paid", 0)),
                    method
                )
            pdf.ln(3)

        # ── 3. Fund Transfers Detail ──────────────────────────────────────────
        fund_transfers = preview.get("fund_transfers_today", [])
        if fund_transfers:
            pdf.section_header("FUND TRANSFERS", php(net_ft))
            pdf.detail_row("Type", "Authorized By", "Amount", "Note", header=True)
            for ft in fund_transfers:
                ft_type = ft.get("type", "")
                label = "Capital Injection" if ft_type == "capital_add" else "Safe -> Cashier" if ft_type == "safe_to_cashier" else "Cashier -> Safe"
                pdf.detail_row(
                    label,
                    str(ft.get("authorized_by", ""))[:25],
                    php(ft.get("amount", 0)),
                    str(ft.get("note", ""))[:20]
                )
            pdf.ln(3)

        # ── 4. Expenses Detail ────────────────────────────────────────────────
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

        # ── 5. Credit Extended Today ──────────────────────────────────────────
        credit_sales = preview.get("credit_sales_today", [])
        if credit_sales:
            pdf.section_header("CREDIT EXTENDED TODAY", php(preview.get("total_credit_today", 0)))
            pdf.detail_row("Customer", "Invoice #", "Balance", "Type", header=True)
            for inv in credit_sales:
                pdf.detail_row(
                    str(inv.get("customer_name", ""))[:30],
                    str(inv.get("invoice_number", "")),
                    php(inv.get("balance", inv.get("grand_total", 0))),
                    str(inv.get("payment_type", "credit")).title()
                )
            pdf.ln(3)

        # ── 6. Digital/E-Wallet Payments ──────────────────────────────────────
        digital_sales = preview.get("digital_sales_today", [])
        if digital_sales:
            pdf.section_header("E-WALLET / DIGITAL PAYMENTS", php(preview.get("total_digital_today", 0)))
            pdf.detail_row("Customer", "Invoice #", "Amount", "Platform", header=True)
            for d in digital_sales:
                pdf.detail_row(
                    str(d.get("customer_name", "Walk-in"))[:30],
                    str(d.get("invoice_number", "")),
                    php(d.get("amount", 0)),
                    str(d.get("platform", "Digital"))[:15]
                )
            # Platform subtotals
            pdf.separator()
            for platform, amt in (preview.get("digital_by_platform") or {}).items():
                pdf.row(f"  {platform} Total", php(amt), indent=6)
            pdf.ln(3)

        # ── 7. Cash Sales by Category ─────────────────────────────────────────
        categories = preview.get("cash_sales_by_category", [])
        if categories:
            pdf.section_header("CASH SALES BY CATEGORY", php(preview.get("total_cash_sales", 0)))
            pdf.detail_row("Category", "Items Sold", "Total", "", header=True)
            for cat in categories:
                pdf.detail_row(str(cat.get("category", "General"))[:30], str(cat.get("qty", 0)), php(cat.get("total", 0)))
            pdf.ln(3)

        # ── 8. Discount Breakdown by Product ──────────────────────────────────
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
                pdf.row("Overall (invoice-level) discounts",
                        php(disc.get("total_overall_discount", 0)), indent=6)
            pdf.ln(3)

        # ── 9. Permanent Price Changes Today ──────────────────────────────────
        price_changes = (preview.get("price_changes_today") or {}).get("rows") or []
        if price_changes:
            pdf.section_header(
                "PERMANENT PRICE CHANGES",
                f"{(preview.get('price_changes_today') or {}).get('count', len(price_changes))} change(s)",
            )
            pdf.detail_row("Product / Invoice", "From", "To", "OK by", header=True)
            for r in price_changes:
                pdf.detail_row(
                    f"{str(r.get('product_name', ''))[:25]} / {str(r.get('invoice_number', ''))[:15]}",
                    php(r.get("old_price", 0)),
                    php(r.get("new_price", 0)),
                    str(r.get("approver_name", ""))[:15],
                )
            pdf.ln(3)

        # ── 10. Interest & Penalty Invoices Issued Today ──────────────────────
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

    # ── Footer summary ────────────────────────────────────────────────────
    pdf.separator()
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(26, 77, 46)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(0, 7, f"  Total Gross Sales: {php(preview.get('total_cash_sales', 0) + preview.get('total_digital_today', 0) + preview.get('total_credit_today', 0) + preview.get('total_partial_cash', 0))}   |   Cash In Drawer: {php(expected)}   |   Safe: {php(preview.get('safe_balance', 0))}", fill=True)
    pdf.ln(10)

    if closing:
        pdf.set_text_color(100, 100, 100)
        pdf.set_font("Helvetica", "", 8)
        pdf.cell(0, 5, f"Day closed by: {closed_by}  |  Closing ID: {closing.get('id', 'N/A')}", align="C")

    # ── Output ────────────────────────────────────────────────────────────
    buf = BytesIO()
    pdf.output(buf)
    buf.seek(0)

    filename = (
        f"ZReport{'_DETAILED' if detailed else ''}_"
        f"{branch_name.replace(' ', '_')}_{date}.pdf"
    )
    return StreamingResponse(
        buf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )

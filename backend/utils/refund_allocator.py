"""
refund_allocator — Payment-aware refund routing for invoice corrections and returns.

Given an invoice and a refund amount, compute how the refund should be allocated
across three buckets in priority order:

    1. AR REDUCTION    — applied to the still-open balance (invoice.balance).
                         No money leaves any wallet; just reduces what the
                         customer owes.
    2. DIGITAL REVERSAL — reverses paid digital payments (GCash, Maya, Bank
                         Transfer, etc.), debiting the digital wallet by the
                         matching channel + ref number.
    3. CASH REFUND     — debits the cashier wallet for the residual amount.

This intentionally mirrors how `void_invoice` reverses cashflow in
`routes/invoices.py` (lines ~1240-1280) so corrections behave consistently
with voids.

Why this ordering?
- AR-first is safe: it only shrinks an obligation, never moves real money.
- Digital-then-cash matches the physical reality: digital payments leave a
  refundable trail (txn ID, sender), so reversing the same channel is most
  auditable. Cash is the catch-all when nothing else can absorb the refund.
"""
from typing import List, Dict, Any
from .helpers import is_digital_payment


def _safe_float(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def summarize_invoice_payments(invoice: Dict[str, Any]) -> Dict[str, Any]:
    """
    Walk an invoice and produce a per-channel payment summary.

    Returns:
      {
        "grand_total":   float,
        "open_balance":  float,   # still owed (== invoice.balance)
        "cash_paid":     float,
        "digital_paid":  float,
        "check_paid":    float,
        # Detailed payment rows, newest-first ordering preserved for reversal:
        "payment_rows": [
            {"id", "amount", "method", "fund_source", "kind": "cash"|"digital"|"check",
             "platform", "ref_number", "source": "initial"|"subsequent"}
        ]
      }

    `payment_rows` is the canonical source of truth for the allocator. It
    composes two inputs:
      a) The INITIAL payment captured at invoice creation. `amount_paid` is
         carried at top-level along with `payment_method`, optional
         `cash_amount`/`digital_amount`/`digital_platform` for splits.
      b) The SUBSEQUENT `payments[]` array — AR payments recorded later via
         `record_invoice_payment`.

    We build the rows in chronological order (initial first, then payments[]
    in their natural order). The allocator will reverse this for "newest-first
    reversal".
    """
    grand_total = _safe_float(invoice.get("grand_total"))
    balance = _safe_float(invoice.get("balance"))
    payment_type = (invoice.get("payment_type") or "").lower()
    payment_method = invoice.get("payment_method") or "Cash"

    rows: List[Dict[str, Any]] = []

    # ── Initial payment row(s) reconstructed from top-level fields ─────────
    initial_amount = _safe_float(invoice.get("amount_paid")) - _sum_subsequent(invoice)
    initial_amount = max(0.0, round(initial_amount, 2))

    if initial_amount > 0:
        if payment_type == "split":
            cash_part = _safe_float(invoice.get("cash_amount"))
            digital_part = _safe_float(invoice.get("digital_amount"))
            # Fall back proportionally if split fields are missing
            if cash_part <= 0 and digital_part <= 0:
                cash_part = initial_amount / 2
                digital_part = initial_amount / 2
            if cash_part > 0:
                rows.append(_mk_row(initial_amount=cash_part, method="Cash",
                                    fund_source="cashier", source="initial-split-cash"))
            if digital_part > 0:
                rows.append(_mk_row(
                    initial_amount=digital_part,
                    method=invoice.get("digital_platform") or "Digital",
                    fund_source="digital",
                    platform=invoice.get("digital_platform") or "Digital",
                    ref_number=invoice.get("digital_ref_number", ""),
                    source="initial-split-digital",
                ))
        else:
            fund_source = "digital" if is_digital_payment(payment_method) else "cashier"
            rows.append(_mk_row(
                initial_amount=initial_amount,
                method=payment_method,
                fund_source=fund_source,
                platform=invoice.get("digital_platform", payment_method) if fund_source == "digital" else "",
                ref_number=invoice.get("digital_ref_number", "") if fund_source == "digital" else "",
                source="initial",
            ))

    # ── Subsequent payments (AR payments) ──────────────────────────────────
    for pmt in invoice.get("payments", []) or []:
        if pmt.get("voided"):
            continue
        amt = _safe_float(pmt.get("amount"))
        if amt <= 0:
            continue
        method = pmt.get("method", "Cash")
        fund_source = pmt.get("fund_source") or (
            "digital" if is_digital_payment(method) else "cashier"
        )
        rows.append(_mk_row(
            initial_amount=amt,
            method=method,
            fund_source=fund_source,
            platform=pmt.get("digital_platform", method) if fund_source == "digital" else "",
            ref_number=pmt.get("digital_ref_number", pmt.get("reference", "")) if fund_source == "digital" else "",
            payment_id=pmt.get("id", ""),
            source="subsequent",
        ))

    cash_paid = round(sum(r["amount"] for r in rows if r["kind"] == "cash"), 2)
    digital_paid = round(sum(r["amount"] for r in rows if r["kind"] == "digital"), 2)
    check_paid = round(sum(r["amount"] for r in rows if r["kind"] == "check"), 2)

    return {
        "grand_total":  round(grand_total, 2),
        "open_balance": round(balance, 2),
        "cash_paid":    cash_paid,
        "digital_paid": digital_paid,
        "check_paid":   check_paid,
        "payment_rows": rows,
    }


def _sum_subsequent(invoice: Dict[str, Any]) -> float:
    total = 0.0
    for pmt in invoice.get("payments", []) or []:
        if pmt.get("voided"):
            continue
        total += _safe_float(pmt.get("amount"))
    return round(total, 2)


def _classify(method: str, fund_source: str) -> str:
    m = (method or "").lower().strip()
    fs = (fund_source or "").lower().strip()
    if m in ("check", "cheque"):
        return "check"
    if fs == "digital" or is_digital_payment(method):
        return "digital"
    return "cash"


def _mk_row(initial_amount: float, method: str, fund_source: str,
            platform: str = "", ref_number: str = "",
            payment_id: str = "", source: str = "") -> Dict[str, Any]:
    return {
        "id":           payment_id,
        "amount":       round(_safe_float(initial_amount), 2),
        "method":       method,
        "fund_source":  fund_source,
        "platform":     platform or "",
        "ref_number":   ref_number or "",
        "source":       source,
        "kind":         _classify(method, fund_source),
    }


def compute_refund_allocation(invoice: Dict[str, Any],
                              refund_amount: float) -> Dict[str, Any]:
    """
    Allocate `refund_amount` across AR/digital/cash buckets for `invoice`.

    Returns:
      {
        "total":                  float,
        "ar_reduction":           float,
        "digital_refunds":        [ {amount, method, platform, ref_number, payment_id} ],
        "check_refunds":          [ {amount, ref_number, payment_id} ],
        "cash_refund":            float,
        "remaining_unallocated":  float,   # 0 unless refund > grand_total
        "summary":                summarize_invoice_payments(invoice),
      }

    Rules:
    - AR-first: never exceed `invoice.balance`.
    - Then walk digital payment rows newest-first, taking up to each row's
      remaining amount.
    - Then cash from the cashier wallet for the residual.
    - Check payments are NOT auto-refunded (we surface them in `check_refunds`
      so the caller can record manual handling).
    """
    refund_amount = max(0.0, round(_safe_float(refund_amount), 2))
    summary = summarize_invoice_payments(invoice)

    remaining = refund_amount

    # 1. AR
    ar_take = min(remaining, summary["open_balance"])
    remaining = round(remaining - ar_take, 2)

    # 2. Digital — newest first
    digital_refunds: List[Dict[str, Any]] = []
    if remaining > 0 and summary["digital_paid"] > 0:
        digital_rows = [r for r in summary["payment_rows"] if r["kind"] == "digital"]
        for r in reversed(digital_rows):
            if remaining <= 0:
                break
            take = min(remaining, r["amount"])
            if take <= 0:
                continue
            digital_refunds.append({
                "amount":      round(take, 2),
                "method":      r["method"],
                "platform":    r["platform"],
                "ref_number":  r["ref_number"],
                "payment_id":  r["id"],
            })
            remaining = round(remaining - take, 2)

    # 3. Check — surface but DO NOT auto-refund. Cashier must reissue/cancel
    #    the check manually. Caller decides whether to push residual into
    #    cash or leave it unallocated.
    check_refunds: List[Dict[str, Any]] = []
    if remaining > 0 and summary["check_paid"] > 0:
        check_rows = [r for r in summary["payment_rows"] if r["kind"] == "check"]
        for r in reversed(check_rows):
            if remaining <= 0:
                break
            take = min(remaining, r["amount"])
            if take <= 0:
                continue
            check_refunds.append({
                "amount":     round(take, 2),
                "ref_number": r["ref_number"],
                "payment_id": r["id"],
            })
            # Check refunds DON'T reduce `remaining` automatically: the
            # caller must decide whether to fall through to cash or leave
            # it as a manual task. Default behaviour: surface and fall
            # through.

    # 4. Cash residual — bounded by what was actually paid in cash (so we
    #    never debit more than the cashier originally received). Anything
    #    above that bound surfaces as `remaining_unallocated` for the caller
    #    to handle (typically: reject, or escalate to admin).
    cash_residual_requested = max(0.0, remaining)
    cash_refund = round(min(cash_residual_requested, summary["cash_paid"]), 2)

    # Compute final unallocated (== 0 in healthy flows). Anything that
    # remains here means refund > grand_total or some data corruption.
    allocated = (
        ar_take
        + sum(d["amount"] for d in digital_refunds)
        + cash_refund
    )
    unallocated = round(max(0.0, refund_amount - allocated), 2)

    return {
        "total":                   round(refund_amount, 2),
        "ar_reduction":            round(ar_take, 2),
        "digital_refunds":         digital_refunds,
        "check_refunds":           check_refunds,
        "cash_refund":             cash_refund,
        "remaining_unallocated":   unallocated,
        "summary":                 summary,
    }

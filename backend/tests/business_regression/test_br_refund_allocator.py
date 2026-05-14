"""
br_refund_allocator — unit tests for utils.refund_allocator.compute_refund_allocation.

These tests pin the math of the new payment-aware refund routing for both
the incomplete-stock correction flow and the customer-return flow:

  Rule 1 — AR FIRST.  Any open AR balance is reduced before money moves.
  Rule 2 — DIGITAL NEXT.  Digital payments are reversed channel-by-channel.
  Rule 3 — CASH LAST.    Only the residual touches the cashier wallet.

If these rules break, voids/corrections silently misroute money. The unit
tests are deliberately invoice-shape-only — no DB writes — so we can iterate
quickly without the per-tenant teardown overhead.
"""
import pytest

from utils.refund_allocator import (
    compute_refund_allocation,
    summarize_invoice_payments,
)


# ── Helpers ──────────────────────────────────────────────────────────────────
def _inv(
    *,
    grand_total: float,
    amount_paid: float,
    balance: float,
    payment_type: str = "cash",
    payment_method: str = "Cash",
    digital_platform: str = "",
    digital_ref_number: str = "",
    cash_amount: float = 0.0,
    digital_amount: float = 0.0,
    payments: list | None = None,
):
    """Compact constructor for invoice dicts in this test module."""
    return {
        "id": "inv-test",
        "grand_total": grand_total,
        "amount_paid": amount_paid,
        "balance": balance,
        "payment_type": payment_type,
        "payment_method": payment_method,
        "digital_platform": digital_platform,
        "digital_ref_number": digital_ref_number,
        "cash_amount": cash_amount,
        "digital_amount": digital_amount,
        "payments": payments or [],
    }


# ═══════════════════════════════════════════════════════════════════════════
# Test 1 — Pure cash sale → cash refund (no AR, no digital).
# ═══════════════════════════════════════════════════════════════════════════
def test_alloc_1_pure_cash_invoice(record_result):
    inv = _inv(grand_total=1000.0, amount_paid=1000.0, balance=0.0,
               payment_type="cash", payment_method="Cash")
    a = compute_refund_allocation(inv, 250.0)
    expected = {"ar": 0.0, "cash": 250.0, "digital_count": 0, "unalloc": 0.0}
    actual = {"ar": a["ar_reduction"], "cash": a["cash_refund"],
              "digital_count": len(a["digital_refunds"]),
              "unalloc": a["remaining_unallocated"]}
    record_result(
        scenario="br_refund_allocator.1_pure_cash",
        step="cash_sale_refund_goes_to_cashier",
        expected=expected, actual=actual, evidence={"inv_kind": "cash"},
    )
    assert actual == expected


# ═══════════════════════════════════════════════════════════════════════════
# Test 2 — Pure credit (unpaid) → AR-only reduction, no money moves.
# ═══════════════════════════════════════════════════════════════════════════
def test_alloc_2_pure_credit_unpaid(record_result):
    inv = _inv(grand_total=5000.0, amount_paid=0.0, balance=5000.0,
               payment_type="credit", payment_method="Cash")
    a = compute_refund_allocation(inv, 1000.0)
    record_result(
        scenario="br_refund_allocator.2_pure_credit_unpaid",
        step="credit_unpaid_refund_reduces_ar_only_no_wallets",
        expected={"ar": 1000.0, "cash": 0.0, "digital_count": 0},
        actual={"ar": a["ar_reduction"], "cash": a["cash_refund"],
                "digital_count": len(a["digital_refunds"])},
    )
    assert a["ar_reduction"] == 1000.0
    assert a["cash_refund"] == 0.0
    assert a["digital_refunds"] == []


# ═══════════════════════════════════════════════════════════════════════════
# Test 3 — Partial credit (some cash paid) → AR consumed first, then cash.
# ═══════════════════════════════════════════════════════════════════════════
def test_alloc_3_partial_credit_ar_then_cash(record_result):
    # ₱10k invoice: ₱3k cash paid, ₱7k still on AR. Refund ₱8k:
    # → AR shrinks 7k → 0, residual 1k comes from cashier.
    inv = _inv(grand_total=10000.0, amount_paid=3000.0, balance=7000.0,
               payment_type="partial", payment_method="Cash")
    a = compute_refund_allocation(inv, 8000.0)
    record_result(
        scenario="br_refund_allocator.3_partial_credit",
        step="ar_first_then_cash_for_overshoot",
        expected={"ar": 7000.0, "cash": 1000.0},
        actual={"ar": a["ar_reduction"], "cash": a["cash_refund"]},
    )
    assert a["ar_reduction"] == 7000.0
    assert a["cash_refund"] == 1000.0
    assert a["digital_refunds"] == []


# ═══════════════════════════════════════════════════════════════════════════
# Test 4 — Pure digital sale (GCash, fully paid) → digital wallet reversal.
# ═══════════════════════════════════════════════════════════════════════════
def test_alloc_4_pure_digital_invoice(record_result):
    inv = _inv(grand_total=2500.0, amount_paid=2500.0, balance=0.0,
               payment_type="digital", payment_method="GCash",
               digital_platform="GCash", digital_ref_number="GC-001")
    a = compute_refund_allocation(inv, 500.0)
    record_result(
        scenario="br_refund_allocator.4_pure_digital",
        step="digital_refund_routes_to_digital_wallet_not_cash",
        expected={"ar": 0.0, "cash": 0.0, "digital_amount": 500.0,
                  "digital_platform": "GCash"},
        actual={"ar": a["ar_reduction"], "cash": a["cash_refund"],
                "digital_amount": a["digital_refunds"][0]["amount"],
                "digital_platform": a["digital_refunds"][0]["platform"]},
    )
    assert a["cash_refund"] == 0.0
    assert len(a["digital_refunds"]) == 1
    assert a["digital_refunds"][0]["amount"] == 500.0
    assert a["digital_refunds"][0]["platform"] == "GCash"


# ═══════════════════════════════════════════════════════════════════════════
# Test 5 — Split payment (cash + digital, fully paid) → AR-first (=0),
# digital reversed before cash, refund > digital available falls into cash.
# ═══════════════════════════════════════════════════════════════════════════
def test_alloc_5_split_cash_and_digital_partial_refund(record_result):
    # ₱4000 invoice: ₱1500 cash + ₱2500 GCash. Refund ₱2000:
    # → All ₱2000 reverses digital (since digital_paid=2500 >= 2000).
    inv = _inv(grand_total=4000.0, amount_paid=4000.0, balance=0.0,
               payment_type="split", payment_method="GCash",
               digital_platform="GCash", digital_ref_number="GC-9",
               cash_amount=1500.0, digital_amount=2500.0)
    a = compute_refund_allocation(inv, 2000.0)
    record_result(
        scenario="br_refund_allocator.5_split_digital_first",
        step="split_refund_eats_digital_before_cash",
        expected={"ar": 0.0, "cash": 0.0, "digital_total": 2000.0},
        actual={"ar": a["ar_reduction"], "cash": a["cash_refund"],
                "digital_total": sum(d["amount"] for d in a["digital_refunds"])},
    )
    assert a["digital_refunds"]
    assert a["cash_refund"] == 0.0


def test_alloc_6_split_refund_exceeding_digital_falls_to_cash(record_result):
    # Same split shape — refund ₱3000:
    # → ₱2500 from digital (all of it), residual ₱500 from cashier.
    inv = _inv(grand_total=4000.0, amount_paid=4000.0, balance=0.0,
               payment_type="split", payment_method="GCash",
               digital_platform="GCash", cash_amount=1500.0, digital_amount=2500.0)
    a = compute_refund_allocation(inv, 3000.0)
    record_result(
        scenario="br_refund_allocator.6_split_overshoot_to_cash",
        step="overshoot_above_digital_paid_lands_in_cashier",
        expected={"ar": 0.0, "digital_total": 2500.0, "cash": 500.0},
        actual={"ar": a["ar_reduction"],
                "digital_total": sum(d["amount"] for d in a["digital_refunds"]),
                "cash": a["cash_refund"]},
    )
    assert sum(d["amount"] for d in a["digital_refunds"]) == 2500.0
    assert a["cash_refund"] == 500.0


# ═══════════════════════════════════════════════════════════════════════════
# Test 7 — Subsequent AR payment was digital (GCash later) → reversal targets
# that subsequent payment first (newest-first ordering).
# ═══════════════════════════════════════════════════════════════════════════
def test_alloc_7_subsequent_digital_ar_payment(record_result):
    # ₱5000 credit invoice. Later customer paid ₱2000 GCash. Balance now ₱3000.
    # Refund ₱2500:
    # → AR consumes 2500 (since balance=3000 still has room). digital untouched.
    inv = _inv(grand_total=5000.0, amount_paid=2000.0, balance=3000.0,
               payment_type="credit", payment_method="Cash",
               payments=[{
                   "id": "pmt-1", "amount": 2000.0,
                   "method": "GCash", "fund_source": "digital",
                   "digital_platform": "GCash", "digital_ref_number": "GR-77",
               }])
    a = compute_refund_allocation(inv, 2500.0)
    record_result(
        scenario="br_refund_allocator.7_subsequent_digital_payment",
        step="ar_consumed_first_even_when_digital_payment_exists",
        expected={"ar": 2500.0, "digital_count": 0, "cash": 0.0},
        actual={"ar": a["ar_reduction"],
                "digital_count": len(a["digital_refunds"]),
                "cash": a["cash_refund"]},
    )
    assert a["ar_reduction"] == 2500.0
    assert a["digital_refunds"] == []


def test_alloc_8_subsequent_digital_drained_then_cash(record_result):
    # ₱5000 credit invoice, paid ₱5000 GCash subsequent → fully paid.
    # Refund ₱3500:
    # → AR=0, digital reversal=3500.
    inv = _inv(grand_total=5000.0, amount_paid=5000.0, balance=0.0,
               payment_type="credit", payment_method="Cash",
               payments=[{
                   "id": "pmt-1", "amount": 5000.0,
                   "method": "GCash", "fund_source": "digital",
                   "digital_platform": "GCash", "digital_ref_number": "GR-77",
               }])
    a = compute_refund_allocation(inv, 3500.0)
    record_result(
        scenario="br_refund_allocator.8_drained_digital_then_cash",
        step="fully_digital_paid_credit_routes_to_digital_first",
        expected={"ar": 0.0, "digital_total": 3500.0, "cash": 0.0},
        actual={"ar": a["ar_reduction"],
                "digital_total": sum(d["amount"] for d in a["digital_refunds"]),
                "cash": a["cash_refund"]},
    )
    assert sum(d["amount"] for d in a["digital_refunds"]) == 3500.0
    assert a["cash_refund"] == 0.0


# ═══════════════════════════════════════════════════════════════════════════
# Test 9 — Voided payments are excluded from the allocator's view.
# ═══════════════════════════════════════════════════════════════════════════
def test_alloc_9_voided_payments_ignored(record_result):
    inv = _inv(grand_total=2000.0, amount_paid=2000.0, balance=0.0,
               payment_type="credit", payment_method="Cash",
               payments=[
                   {"id": "p1", "amount": 1000.0, "method": "GCash",
                    "fund_source": "digital", "voided": True},
                   {"id": "p2", "amount": 2000.0, "method": "GCash",
                    "fund_source": "digital"},
               ])
    summary = summarize_invoice_payments(inv)
    # Only the second 2000 should be counted as digital paid.
    record_result(
        scenario="br_refund_allocator.9_voided_payments_ignored",
        step="voided_payment_rows_dropped_from_summary",
        expected={"digital_paid": 2000.0},
        actual={"digital_paid": summary["digital_paid"]},
    )
    assert summary["digital_paid"] == 2000.0


# ═══════════════════════════════════════════════════════════════════════════
# Test 10 — Refund > grand_total → unallocated overflow surfaces (not silent).
# ═══════════════════════════════════════════════════════════════════════════
def test_alloc_10_overshoot_grand_total(record_result):
    inv = _inv(grand_total=1000.0, amount_paid=1000.0, balance=0.0,
               payment_type="cash", payment_method="Cash")
    # Asking to refund ₱1500 when only ₱1000 was paid: ₱1000 in cash, ₱500 unallocated.
    a = compute_refund_allocation(inv, 1500.0)
    record_result(
        scenario="br_refund_allocator.10_overshoot",
        step="refund_above_grand_total_surfaces_unallocated_residual",
        expected={"cash": 1000.0, "unalloc": 500.0},
        actual={"cash": a["cash_refund"], "unalloc": a["remaining_unallocated"]},
    )
    # The cashier should still take ₱1000 (the residual after AR & digital both 0).
    # And unallocated reports the ₱500 we couldn't place.
    assert a["cash_refund"] == 1000.0
    assert a["remaining_unallocated"] == 500.0


# ═══════════════════════════════════════════════════════════════════════════
# Test 11 — Summary numbers match channel breakdown for a split invoice.
# ═══════════════════════════════════════════════════════════════════════════
def test_alloc_11_split_summary_breakdown(record_result):
    inv = _inv(grand_total=4000.0, amount_paid=4000.0, balance=0.0,
               payment_type="split", payment_method="GCash",
               digital_platform="GCash", cash_amount=1500.0, digital_amount=2500.0)
    s = summarize_invoice_payments(inv)
    record_result(
        scenario="br_refund_allocator.11_split_summary",
        step="cash_and_digital_buckets_track_split_breakdown",
        expected={"cash_paid": 1500.0, "digital_paid": 2500.0, "open": 0.0},
        actual={"cash_paid": s["cash_paid"], "digital_paid": s["digital_paid"],
                "open": s["open_balance"]},
    )
    assert s["cash_paid"] == 1500.0
    assert s["digital_paid"] == 2500.0
    assert s["open_balance"] == 0.0


# ═══════════════════════════════════════════════════════════════════════════
# Test 12 — Allocation is conservative when refund is 0.
# ═══════════════════════════════════════════════════════════════════════════
def test_alloc_12_zero_refund_is_inert(record_result):
    inv = _inv(grand_total=1000.0, amount_paid=500.0, balance=500.0,
               payment_type="partial", payment_method="Cash")
    a = compute_refund_allocation(inv, 0.0)
    record_result(
        scenario="br_refund_allocator.12_zero_refund_inert",
        step="zero_refund_does_nothing",
        expected={"ar": 0.0, "cash": 0.0, "digital_count": 0, "unalloc": 0.0},
        actual={"ar": a["ar_reduction"], "cash": a["cash_refund"],
                "digital_count": len(a["digital_refunds"]),
                "unalloc": a["remaining_unallocated"]},
    )
    assert a == {
        "total": 0.0,
        "ar_reduction": 0.0,
        "digital_refunds": [],
        "check_refunds": [],
        "cash_refund": 0.0,
        "remaining_unallocated": 0.0,
        "summary": a["summary"],
    }

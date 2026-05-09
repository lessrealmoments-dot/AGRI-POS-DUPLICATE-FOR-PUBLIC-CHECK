"""Phase 2E — shared date-basis helpers.

Pure functions that classify a document by the **date-basis matrix** so all
reports speak the same language. No DB writes here.

`is_encoded_today` is the canonical predicate; everything else is a
convenience wrapper. See `/app/memory/PHASE_2E_DATE_BASIS_MATRIX.md`.
"""
from __future__ import annotations

from typing import Optional


def _date_part(iso_or_date: str) -> str:
    """Extract YYYY-MM-DD from either an ISO timestamp or a date string."""
    if not iso_or_date or not isinstance(iso_or_date, str):
        return ""
    return iso_or_date[:10]


def is_encoded_today(
    *,
    today: str,
    business_date: Optional[str],
    created_at: Optional[str],
) -> bool:
    """Was this document encoded today but dated to a different day?

    A document is **encoded today** when:
      - `created_at::date == today` AND
      - `business_date != today`

    Where `business_date` is `order_date` for invoices/sales, or the
    `payments[i].date` for individual payment events.

    Returns False if either timestamp is missing — the caller should treat
    that as "unknown / not encoded today" rather than as an alarm.
    """
    today_d = _date_part(today)
    bd = _date_part(business_date or "")
    ca = _date_part(created_at or "")
    if not today_d or not ca:
        return False
    if ca != today_d:
        return False
    if not bd:
        return False
    return bd != today_d


def enrich_invoice_with_date_basis(inv: dict, *, today: str) -> dict:
    """Return a shallow copy of `inv` with date-basis transparency fields.

    Adds (does NOT remove or rename existing fields):
      transaction_date    — `order_date` (canonical business date)
      late_encoded        — bool (already on the invoice; mirrored here)
      late_encoded_at     — pass-through if present
      late_encode_reason  — pass-through if present
      late_encoded_by_name— pass-through if present
      created_at          — pass-through (already on the invoice)
      encoded_today       — derived via `is_encoded_today`

    Safe for use on already-enriched invoices (idempotent).
    """
    if not isinstance(inv, dict):
        return inv
    out = dict(inv)
    out["transaction_date"] = inv.get("order_date") or inv.get("invoice_date") or ""
    out["late_encoded"] = bool(inv.get("late_encoded", False))
    out["late_encoded_at"] = inv.get("late_encoded_at") or None
    out["late_encode_reason"] = inv.get("late_encode_reason") or None
    out["late_encoded_by_name"] = inv.get("late_encoded_by_name") or None
    out["encoded_today"] = is_encoded_today(
        today=today,
        business_date=out["transaction_date"],
        created_at=inv.get("created_at"),
    )
    return out


def enrich_movement_with_source_date(mv: dict, *, source_order_date: Optional[str] = None,
                                      source_kind: Optional[str] = None) -> dict:
    """Return a shallow copy of `mv` with transparency fields.

    Adds:
      movement_date     — `created_at` (when the row was inserted; this is
                          the inventory event's wall-clock time)
      source_order_date — the referenced doc's business date (e.g. for a
                          sale, this is the invoice.order_date the cashier
                          chose). May be None when the source isn't dated.
      source_kind       — coarse classification — 'sale', 'purchase',
                          'transfer', 'adjustment', etc. (best-effort).
    """
    if not isinstance(mv, dict):
        return mv
    out = dict(mv)
    out["movement_date"] = mv.get("created_at") or ""
    out["source_order_date"] = source_order_date
    out["source_kind"] = source_kind or mv.get("type") or None
    return out

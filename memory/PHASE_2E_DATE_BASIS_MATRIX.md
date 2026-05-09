# Phase 2E — Report Date-Basis Matrix

**Status**: ✅ Implemented (Feb 2026). Read-only diagnostic + targeted additive enrichments. **Zero changes to totals or existing fields.**

This document is the canonical reference for which date field every report uses, why, and where transparency fields are surfaced. Treat it as the source of truth before Phase 3 (Historical Credit Encoding).

---

## 1. Glossary of date fields

| Field | Where stored | Meaning |
|---|---|---|
| `order_date` | invoices, sales | Transaction date — the date the sale logically occurred (the cashier may have backdated it) |
| `invoice_date` | invoices | Document date (usually equals `order_date`; carried for legacy POS imports) |
| `created_at` | every collection | Wall-clock UTC ISO timestamp when the row was inserted into Mongo |
| `payments[*].date` | invoices.payments | Local date (YYYY-MM-DD) on which a payment was actually collected |
| `late_encoded` | invoices | Boolean — `true` ⇔ the invoice was encoded after the day was closed |
| `late_encoded_at` | invoices, late_encode_log | UTC ISO when the late-encode happened |
| `late_encode_reason` | invoices | Cashier-supplied reason for backdating |
| `late_encoded_by_name` | invoices | Encoder identity |
| `movements.created_at` | movements (stock_movements) | UTC ISO when the inventory movement row was inserted |

---

## 2. Date-basis matrix per report

| # | Report | Endpoint | Current date field | Recommended | Status | Risk if changed | Phase 2E action |
|---|---|---|---|---|---|---|---|
| 1 | Sales report (transactions) | `GET /api/reports/sales` | `order_date` for filter+sort+row | `order_date` (kept) + expose `late_encoded`, `late_encoded_at`, `created_at`, `encoded_today` | ✅ correct base; transparency missing | LOW (additive only) | **Enriched** transaction rows with the 4 transparency fields. |
| 2 | Sales category breakdown | same endpoint | `sales_log.date` (= `order_date` at insert time) | (kept) | ✅ correct | NIL | None. |
| 3 | Cash-collected today | `GET /api/daily-report` (and Z-report) | `payments[*].date == today` AND `payments.voided != True` | (kept) | ✅ correct | NIL | None. Already keyed off payment date independent of invoice date. |
| 4 | Z-report regular sales | `GET /api/daily-operations/z-report` | `order_date == date` AND status ≠ voided | (kept) | ✅ correct | NIL | None. |
| 5 | Z-report late-encoded section | `GET /api/sales/late-encoded-since-last-close` | `late_encoded_at > last_close_at` | (kept) | ✅ correct (this is the audit-defined rule) | NIL | None. |
| 6 | Customer ledger / transactions | `GET /api/customers/{id}/transactions` | sorted by `created_at` | sort kept; expose `transaction_date` (= `order_date`), `late_encoded`, `late_encoded_at`, `encoded_today` per row | ✅ base + transparency | LOW (additive only) | **Enriched** each invoice row. |
| 7 | Inventory movement report | `GET /api/products/{id}/movements` | `created_at` (when row was inserted) | (kept) + expose `source_order_date` from referenced invoice | ✅ base + transparency | LOW (additive only) | **Enriched** each movement with `source_order_date` (and `source_kind`). |
| 8 | Encoded-today / backdated consolidated view | **NEW** `GET /api/reports/encoded-today` | n/a | every doc `created_at::date == today AND` (`order_date != today` OR `payment.date != today`) | Phase 2E adds it | LOW (read-only) | **New route** added. |
| 9 | Backdated charges audit | `GET /api/accounting/invoices/audit/backdated-charges` | `gap_days = created_at − order_date > 1` | (kept) | ✅ correct | NIL | None. |
| 10 | AR aging | `GET /api/reports/ar-aging` | `invoice_date OR order_date OR created_at` | `order_date` first (kept) | ✅ correct | NIL | None. |
| 11 | Audit log | `audit_log`, `late_encode_log`, `security_events` | `created_at` (= encoded_at) | (kept) | ✅ correct | NIL | None. |
| 12 | Customer payment history | `GET /api/accounting/customers/{id}/payment-history` | `payment.date` | (kept) | ✅ correct | NIL | None. |
| 13 | Daily profit report | `GET /api/daily-report` | `sales_log.date == date` for revenue, `payments.date == date` for collections | (kept) | ✅ correct | NIL | None. Documented. |
| 14 | Expense report | `GET /api/reports/expenses` | `expense.date` (operational date) | (kept) | ✅ correct | NIL | None. |

---

## 3. The encoded-today rule (canonical wording)

> A document is **"encoded today"** when its `created_at` date is today AND its
> business date (`order_date` for sales/invoices, `payments[i].date` for
> payments) is **not** today.
>
> This means: payments collected today against an old credit invoice ARE
> encoded-today. A backdated cash sale dated three days ago but typed in
> today IS encoded-today. A cash sale created today AND dated today is NOT
> encoded-today.

The new `/api/reports/encoded-today` endpoint surfaces these in one place
(invoices + payment events) so the cashier / owner can see which entries
shifted reality after the fact.

---

## 4. Reports that intentionally remain UNCHANGED

The following reports were considered and **left alone** because their
date-basis is already correct per the audit and changing them would alter
user-visible totals:

- **Cash collected today** — already keys off `payments.date`. Old credit
  paid today already shows in today's cash collected. ✅
- **Z-report regular sales section** — already filters by `order_date`.
  Late-encoded entries do NOT silently re-enter old closed Z-reports;
  they appear on the *current* open Z-report's "late-encoded since last
  close" section. ✅
- **Backdated interest/penalty charges audit** — already exposes the
  `gap_days = created_at − order_date` analysis. ✅

Phase 2E does not touch any totals math.

---

## 5. Files changed in Phase 2E

| File | Change kind | Summary |
|---|---|---|
| `backend/utils/date_basis.py` | NEW | Shared `is_encoded_today()`, `enrich_invoice_with_date_basis()`, `enrich_movement_with_source_date()`. |
| `backend/routes/reports.py` | UPDATED (additive) | Sales report `transactions[]` enriched. New `GET /reports/encoded-today` route. |
| `backend/routes/customers.py` | UPDATED (additive) | `/customers/{id}/transactions` invoices enriched. |
| `backend/routes/products.py` | UPDATED (additive) | `/products/{id}/movements` enriched with `source_order_date`. |
| `backend/utils/__init__.py` | UPDATED | Export the 3 helpers. |
| `backend/tests/test_phase2e_date_basis.py` | NEW | 11 tests covering the user-requested cases. |
| `memory/PHASE_2E_DATE_BASIS_MATRIX.md` | NEW (this doc) | Source-of-truth matrix. |

---

## 6. POS surfaces unaffected

- Quick POS, Advanced POS, POS Terminal (online), POS Terminal offline → sync — all still post sales via `POST /api/unified-sale` and `POST /api/sales/sync`. **Zero changes** in those code paths in Phase 2E.
- Returns, payments, purchases — all still use the same date contracts they did before. **Zero changes**.

---

## 7. Recommendation for Phase 3

Phase 3 (Historical Credit Encoding / Notebook AR) is now **safe to begin**
because:

1. The encoded-today / backdated transparency fields are surfaced on
   sales, customer ledgers, and inventory movements.
2. A consolidated audit view (`/reports/encoded-today`) exists for the
   owner to inspect any historical entry that shifts reality after the
   fact.
3. The Z-report rule for late-encoded entries is documented and verified
   in tests.
4. No totals were changed — Phase 3's historical encoding can rely on
   stable cash / Z / sales report semantics.

**Caveat for Phase 3 owner sign-off**: any historical encoding that
materially shifts customer balances must respect the date-basis rules
in §3. The new `/reports/encoded-today` will surface every historical
encoding action on the day it happens.

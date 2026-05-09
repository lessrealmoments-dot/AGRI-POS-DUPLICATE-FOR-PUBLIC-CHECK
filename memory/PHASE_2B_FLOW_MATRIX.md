# Phase 2B — Core POS Business Flow Verification Matrix

**Audit context:** AGRI-POS — Audit 2026-02
**Phase:** 2B (verification-first; **no production data, no fixes**)
**Approach:** controlled fixture-only pytest harness invoking actual route
handlers; captured invariants per flow; gaps recorded as observations.

**Headline result: 31 / 33 PASS · 2 SKIPPED · 0 FAILED · 0 production mutations**
- Pre-existing Phase 1 27/27 + Phase 2A 18/18 still green (45 total).
- 2 P1 risks confirmed (idempotent payment retries · returns credit fallback).
- 1 design-choice flagged (overpayment → customer credit balance).
- POS-route convergence verified — Quick / Advanced / Terminal-online / Terminal-offline-sync all funnel through `POST /unified-sale` with identical contract.

Run:
```
cd /app/backend && python3 -m pytest tests/phase2b/ -v
```

---

## Group 1 — SALES LOGIC

| # | Flow | Inventory Effect | Cash Effect | AR Effect | Branch Scope | Idempotency | Pass/Fail | Notes |
|---|---|---|---|---|---|---|---|---|
| 1.1 | Cash sale | Deducted ×1 | cashier +N | unchanged | branch only | n/a | **PASS** | — |
| 1.2 | Credit sale | Deducted ×1 | unchanged | +grand_total | branch only | n/a | **PASS** | — |
| 1.3 | Partial payment | Deducted ×1 | cashier +amount_paid | +balance only | branch only | n/a | **PASS** | only unpaid portion hits AR |
| 1.4 | Split (cash + digital) | Deducted ×1 | cashier+digital wallets | unchanged | branch only | n/a | **PASS** | sum of payments == amount_paid |
| 1.5 | Idempotent retry | Deducted ×1 | once | once | branch only | enforced | **PASS** | same `idempotency_key` returns same invoice id |
| 1.6 | Insufficient stock | unchanged | unchanged | unchanged | branch only | n/a | **PASS** | rejects with HTTP 422 (insufficient_stock manager-PIN flow) |
| 1.7 | Cross-branch sell attempt | unchanged | unchanged | unchanged | **403** | n/a | **PASS** | `assert_branch_access` enforced |
| 1.8 | Line discount | Deducted ×1 | discounted total to wallet | unchanged | branch only | n/a | **PASS** | `discount_value` is per-unit (documented) |

**Findings:** none. Group 1 contracts are all upheld. Phase 1 fixes (C-4, C-5) confirmed in production code path.

---

## Group 2 — CASH FLOW / PAYMENT LOGIC

| # | Payment Flow | Expected Cash Effect | Expected AR Effect | Actual Behavior | Risk | Recommended Fix |
|---|---|---|---|---|---|---|
| 2.1 | Cash payment on open | cashier +amount | customer −amount | matches | — | — |
| 2.2 | Full payment closes | cashier +balance | customer −balance | matches; status=paid | — | — |
| 2.3 | Voided invoice rejects | unchanged | unchanged | 400 (Phase 1 C-9) | — | — |
| 2.4 | Payment doesn't touch inventory | n/a | n/a | matches | — | — |
| 2.5 | Overpayment | cashier +amount | customer goes **negative** | invoice.balance capped at 0; customer.balance decremented by FULL amount → customer ends with NEGATIVE balance (a credit) | **P2** design-choice | Document explicitly OR introduce a credit-memo path (Phase 2C) |
| 2.6 | Identical retry | recorded once | decremented once | **recorded twice; AR decremented twice** — no idempotency | **P1 BUG** | Add `idempotency_key` support to `record_invoice_payment` and `pay_receivable`; return the existing payment row on dup |

**Findings:**
- **P1 — Payment idempotency missing.** `record_invoice_payment` accepts repeated identical bodies and creates duplicate payment rows. Network retry could double-record. Recommend Phase 2C scope.
- **P2 — Overpayment becomes negative AR (i.e. an unstructured customer credit).** No "credit memo" record exists; future invoice may silently consume it. Recommend introducing a structured `customer_credits` collection in Phase 2C OR rejecting overpayments at the route boundary.

---

## Group 3 — PRODUCT IN/OUT (STOCK MOVEMENT)

| # | Module | Action | Expected Stock Effect | Movement Log | Reversible? | Actual | Issue |
|---|---|---|---|---|---|---|---|
| 3.1 | Sales | Cash sale | −qty exactly once | sales_log row | by void | matches | — |
| 3.2 | Sales | Failed sale | unchanged | none | n/a | matches | Phase 1 C-4 reaffirmed |
| 3.3 | Sales | Movement log | sales_log entry | exists | n/a | matches | — |
| 3.4 | Sales | Branch isolation | only seller branch | per-branch | n/a | matches | inventory rows isolated |
| 3.5 | Sales | Idempotent retry | −qty once | once | by void | matches | Phase 1 C-5 reaffirmed |
| 3.6 | Sales | No-key repeat | −qty per call | per call | each | matches | (documented client contract) |

**Findings:** none. Stock-out invariants intact. Stock-in flows (PO receive, transfers, repack) need a separate fixture pass — see Group 4 skip note.

---

## Group 4 — PURCHASE / SUPPLIER LOGIC

| # | Purchase Flow | Inventory Effect | Supplier/Payable Effect | Cost Effect | Report Effect | Status |
|---|---|---|---|---|---|---|
| 4.1 | Draft PO | none | none | none | shown as draft | **SKIPPED** — needs fund balances + closed-day fixture; verify by inspection |
| 4.2 | PO idempotency_key | dedup'd | dedup'd | dedup'd | dedup'd | **SKIPPED** — same fixture cost |
| 4.3 | Cash PO closed-day guard | n/a | n/a | n/a | n/a | **VERIFIED BY INSPECTION** — `resolve_business_date` in `routes/purchase_orders.py:454` |
| 4.4 | Terms PO supplier payable | n/a | +amount | last_purchase_cost updated | shown in payables | **VERIFIED BY INSPECTION** |

**Findings:** PO route is too heavily fixtured for direct-call testing in this fork. Two tests are coded but **`pytest.skip`** until a follow-up phase wires the closed-day + fund-balance fixtures, OR until a live-API smoke pass is approved. Recommend keeping PO out of unit-test scope and using the live testing agent for integration coverage.

---

## Group 5 — RETURNS / VOIDS / REFUNDS

| # | Original Transaction | Reversal | Inventory Effect | Cash/AR Effect | Report Effect | Risk |
|---|---|---|---|---|---|---|
| 5.1 | Cash sale | Sellable return | +qty back | cashier −refund | +1 return row | **PASS** |
| 5.2 | Cash sale | Damaged pull-out | unchanged | cashier −refund | +1 return + inventory_corrections (loss) | **PASS** |
| 5.3 | Cash sale | Refund > cashier | unchanged | unchanged | rejected 400 | **PASS** |
| 5.4 | Credit sale (linked invoice) | Credit return | +qty back | customer −retail; invoice.balance −retail | invoice marked partial/paid | **PASS** |
| 5.5 | Credit sale (no invoice link) | Credit return | +qty back | applied to **OLDEST open invoice** for customer | could land on wrong invoice | **P1 H-4** |
| 5.6 | Sale void | (covered by C-9 in Phase 1) | n/a | payments rejected | tombstoned | **PASS (Phase 1)** |
| 5.7 | Modify payment / void payment | n/a | n/a | guards NOT YET wired | invoice may receive payment | **P1** — in Phase 2C scope per audit |

**Findings:**
- **P1 (H-4)** — Credit-customer return falls back to the oldest open invoice when `invoice_number` is omitted or doesn't match. Confirms audit finding H-4. Phase 2C scope.
- **P1** — `modify-payment` and `void-payment` are **not** guarded by `assert_invoice_payable` (Phase 1C scope explicitly excluded these). Phase 2C scope.

---

## Group 6 — REPORT CONSISTENCY

| # | Report | Source Data | Expected | Actual | Difference | Cause |
|---|---|---|---|---|---|---|
| 6.1 | Sales aggregate | invoices.grand_total (non-void) | 300 | 300 | 0 | — |
| 6.2 | Customer ledger | Σ inv.balance == customer.balance | equal | equal | 0 | invariant holds for clean cycle |
| 6.3 | Phase 2A reconciliation | drift after clean cycle | 0 | 0 | 0 | reconciliation report is the source of truth |
| 6.4 | Z-report cash collected | wallet_movements | n/a | n/a | n/a | **VERIFIED BY INSPECTION** — `update_cashier_wallet` writes one row per movement |
| 6.5 | Date-basis consistency across reports | order_date / invoice_date / payment.date | mixed | mixed | n/a | **deferred to Phase 2B → 2C** ([H-13 in audit](../memory/AUDIT_REPORT_2026-02.md)) |

**Findings:** Customer-ledger invariant holds for the clean cycle and the Phase 2A drift detector returns zero rows for the same cycle (cross-validates 2A). Date-basis report inconsistency (mixed `order_date`/`invoice_date`/`payment.date`) is the audit's H-13; flag for Phase 2C.

---

## Group 7 — POS SURFACE COMPARISON

| # | Scenario | Quick POS | Advanced POS | Terminal Online | Terminal Offline → Sync | Difference | Risk |
|---|---|---|---|---|---|---|---|
| 7.1 | Same backend route | `POST /unified-sale` | `POST /unified-sale` | `POST /unified-sale` | `POST /sales/sync` → `_create_unified_sale` (same logic, idempotent envelope) | only `mode` tag | none |
| 7.2 | Same invoice contract for identical body | grand_total/amount_paid/balance/status equal | equal | equal | equal | only `id`, `inv_number`, `mode` differ | none |
| 7.3 | Split payment decomposition | cash → cashier wallet, digital → digital wallet | same | same | same | none | none |
| 7.4 | Offline envelope idempotency | n/a | n/a | n/a | same `idempotency_key` returns same invoice id, no double-deduction | none | Phase 1 C-5 reaffirmed |
| 7.5 | Credit-sale signature/bypass | captured pre-invoice, linked post-insert | same | same | offline: PIN deferred (Phase 1 C-1) | none | none |

**Findings:** POS surfaces converge on a single backend route, which is the single biggest correctness lever in this codebase. No divergence detected. The four UI surfaces produce structurally-identical invoices for identical inputs.

---

## Aggregated Findings (sorted by severity)

| Severity | Finding | Location | Phase to Fix |
|---|---|---|---|
| **P1** | Payment idempotency missing — `record_invoice_payment` and `pay_receivable` accept duplicate identical bodies | `routes/invoices.py:181`, `routes/accounting.py:35` | **Phase 2C** |
| **P1** | Returns credit applied to OLDEST open invoice when invoice_number omitted (H-4) | `routes/returns.py:218` | **Phase 2C** |
| **P1** | `modify-payment` / `void-payment` routes do NOT enforce `assert_invoice_payable` | `routes/invoices.py` (modify, void paths) | **Phase 2C** |
| **P1** | Date-basis inconsistency across reports (`order_date` vs `invoice_date` vs `payment.date`) | report routes (H-13) | **Phase 2B → 2C** |
| **P2** | Overpayment quietly creates negative `customer.balance` (informal credit) | `routes/invoices.py:261` | **Phase 2C** — introduce `customer_credits` collection or reject overpayment |
| **P3** | PO endpoints (`create_purchase_order`, `receive_purchase_order`) too heavily fixtured for direct-call unit tests | `routes/purchase_orders.py` | refactor in Phase 4 cleanup |

---

## Code paths NOT touched in this phase

To honour the verification-first instruction, **no production code was modified** in Phase 2B. The harness:
- creates throw-away `p2b-org-*` / `p2b-br-*` / `p2b-cust-*` / `p2b-prd-*` documents
- never queries production tenants
- never mutates customer.balance, invoices, payments, returns, or fund_wallets in real tenants
- adds zero new endpoints

Quick POS, Advanced POS, POS Terminal, and offline sync remain unchanged.

---

## Tests added

| File | Tests | Status |
|---|---|---|
| `backend/tests/phase2b/_fixtures.py` | shared helper (no tests) | n/a |
| `backend/tests/phase2b/test_g1_sales.py` | 8 | 8/8 PASS |
| `backend/tests/phase2b/test_g2_payments.py` | 6 | 6/6 PASS |
| `backend/tests/phase2b/test_g3_inventory.py` | 6 | 6/6 PASS |
| `backend/tests/phase2b/test_g4_purchase.py` | 2 | 0/0 PASS, 2 SKIP (fixture cost) |
| `backend/tests/phase2b/test_g5_returns_voids.py` | 5 | 5/5 PASS |
| `backend/tests/phase2b/test_g6_reports.py` | 3 | 3/3 PASS |
| `backend/tests/phase2b/test_g7_pos_comparison.py` | 3 | 3/3 PASS |
| **TOTAL** | **33** | **31 PASS · 2 SKIP · 0 FAIL** |

Phase 1 (27/27) + Phase 2A (18/18) regression re-run: **45/45 PASS**.

---

## Recommended next phase

**Phase 2C — POS write-side hardening.** Highest-leverage, smallest scope.
Target order:

1. **Payment idempotency (P1)** — add `idempotency_key` support to the three payment-write routes; return existing payment row on dup. Add 4 regression tests.
2. **Returns credit fallback (H-4, P1)** — REQUIRE `invoice_number` for credit returns; reject if missing. Add 3 regression tests.
3. **modify-payment / void-payment guards (P1)** — wire `assert_invoice_payable` (and an analogous `assert_invoice_modifiable`) into both paths. Add 4 regression tests.
4. **Overpayment policy (P2)** — decide: reject at the boundary OR introduce structured `customer_credits` collection. Pre-2C decision required.
5. (Defer Phase 2B → 2D / 2E unless owner decides otherwise; date-basis standardisation is read-side and can ride alongside or follow.)

**Do not start Phase 3 (Historical Credit Encoding) until Phase 2C closes.**

— End of Phase 2B verification matrix —

# Phase 3 — Historical Credit Encoding / Notebook AR

**Status**: ✅ Implemented (Feb 2026). Backend-only; frontend wiring is a Phase 4 follow-up.

This document is the canonical reference for the Phase 3 workflow, data model, count-sheet stopper logic, and report behaviour. Treat it as the source of truth before Phase 4.

---

## 1. Purpose

Allow an admin / owner to safely reconstruct customer credit sales that were originally written in a paper notebook but never encoded into the POS. **Not** a backdated cash-sale path. **Not** a general-purpose late-encode tool. Reuses the existing invoice / customer-ledger / receivables infrastructure with a strong audit envelope.

---

## 2. Endpoints (admin / owner only)

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/historical-credit/preview` | Validate + run the count-sheet stopper + return the proposed effects without committing. |
| `POST` | `/api/historical-credit` | Commit a historical credit entry. |
| `GET`  | `/api/historical-credit?branch_id=&customer_id=&limit=` | List entries (filterable). |

All three reject non-admin/non-owner users via `assert_admin_or_owner` (Phase 2D).

---

## 3. Workflow (matches user spec §1-9)

1. Admin opens Historical Credit Encoding (frontend page — Phase 4 work).
2. Selects customer + branch.
3. Enters old `transaction_date` (must be < today; today's sales must use the regular POS).
4. Adds line items / amount / reference.
5. Provides `reason` (≥ 20 chars) and optional `proof_url` / `notebook_reference`.
6. Frontend calls `POST /api/historical-credit/preview` → backend runs the count-sheet stopper and returns a preview.
7. Preview surfaces: customer balance effect, inventory action, report effect, audit-log effect.
8. Admin confirms.
9. Frontend calls `POST /api/historical-credit` → backend commits and returns the created invoice.

Both preview and commit hit the **same validation + stopper logic**. Preview is verifiably read-only (a regression test asserts neither customer balance nor inventory nor invoice count moves).

---

## 4. Data model

Every committed entry produces ONE invoice document with the following shape (the relevant new fields only):

| Field | Value |
|---|---|
| `source` | `"historical_credit_encoding"` |
| `late_encoded` | `true` |
| `late_encoded_at` | UTC ISO at commit time |
| `late_encoded_by`, `late_encoded_by_name` | The admin who encoded |
| `late_encode_reason` | The 20+-char reason |
| `approved_by`, `approved_by_name`, `approved_at` | Same admin (single-step approval — sufficient because only admin / owner can call the endpoint at all) |
| `historical_credit_proof_url` | Optional URL string |
| `historical_credit_notebook_ref` | Optional notebook reference text |
| `historical_credit_inventory_action` | One of: `deducted`, `skipped_count_sheet_lock`, `deducted_with_admin_acknowledgement` |
| `historical_credit_count_sheet_anchor` | The `completed_at` of the latest approved count sheet at commit time, for forensic replay |
| `payment_type` | `"credit"` (always) |
| `status` | `"credit"` |
| `sale_type` | `"historical_credit"` |
| `order_date` (= transaction_date) | The old date the cashier entered |
| `created_at` | UTC ISO at commit time |
| `payments` | `[]` (no payments at encode time — payment is a separate step using existing payment endpoints) |

In addition, **two audit rows** are written:

- `late_encode_log` — same shape as the existing late-encode trail (so existing Audit Center tabs pick it up unchanged).
- `security_events` — `type = "historical_credit_encoded"` row carrying every audit field for forensic search.

---

## 5. Count-sheet stopper

The "approved" anchor is the latest `count_sheets` document with `status == "completed"` for the branch.

| Situation | `inventory_action` | Inventory? | Customer balance? |
|---|---|---|---|
| No count sheet exists | `deducted` | YES | UP |
| `transaction_date > completed_at` (after the count) | `deducted` | YES | UP |
| `transaction_date <= completed_at` AND `allow_inventory_deduction=false` | `skipped_count_sheet_lock` | NO | UP |
| `transaction_date <= completed_at` AND `allow_inventory_deduction=true` | `deducted_with_admin_acknowledgement` | YES (with audit) | UP |

The third row is the **default safe path** for notebook AR: customer balance reconstructed, inventory left alone. The fourth row exists for the rare case where the admin verified physical stock manually and chooses to override.

**Customer balance always goes up** — that's the whole point of historical credit reconstruction. The stopper only governs inventory.

---

## 6. Report behaviour

| Report | Behaviour |
|---|---|
| `/api/reports/sales` | Entry appears (filtered by `order_date == transaction_date`) WITH `late_encoded=true`, `encoded_today=true`, `transaction_date`, `created_at`, `late_encode_reason` per Phase 2E enrichment. |
| `/api/reports/encoded-today` | Entry appears in the `invoices[]` section. |
| `/api/customers/{id}/transactions` | Entry appears with `transaction_date`, `late_encoded`, `late_encoded_at`, `encoded_today`, plus the historical-credit-specific `source` field. |
| `/api/products/{id}/movements` | If inventory was deducted, the movement appears with `type="historical_credit_sale"`, `source_kind="historical_credit_sale"`, `source_order_date=transaction_date`, `movement_date=created_at`. |
| **Old day's Z-report regular credit-sales section** | Entry **does NOT appear**. Phase 3 added an explicit `source != "historical_credit_encoding"` filter to `daily_operations` Z-report queries (lines 619-623 partial cash + 629-634 credit_invoices). |
| **Today's open Z-report** | Entry appears in the existing "late-encoded since last close" section (`/api/sales/late-encoded-since-last-close`) — no new code needed; the existing query keys off `late_encoded == true`. |
| **Today's cash-collected** | **No change** unless an actual payment is recorded today. Encoding pure AR creates a credit invoice with `amount_paid = 0` and no `payments[]` entries. |
| Customer balance reconciliation (`/api/admin/customer-balance-reconciliation`) | Entry counted as part of customer's `balance` (raises AR). The diagnostic naturally shows the reconciled balance. |

---

## 7. Inventory behaviour

When `inventory_action == "deducted"` or `"deducted_with_admin_acknowledgement"`:

- `db.inventory.updateOne($inc {quantity: -qty})` upsert on `(product_id, branch_id)`.
- `db.movements.insertOne({type: "historical_credit_sale", quantity_change: -qty, reference_id: invoice_id, reference_number: invoice_number, ...})`.

When `inventory_action == "skipped_count_sheet_lock"`:

- **No inventory writes.** No movement row. The audit log + security_events row record the skip with `inventory_action` field for traceability.

---

## 8. Validation rules (matches user §B-1 to §B-9)

| Rule | Error trigger |
|---|---|
| `customer_id` required | Empty / missing → 400 |
| `branch_id` required | Empty / missing → 400 |
| `transaction_date` required | Empty / missing → 400 |
| `transaction_date` format `YYYY-MM-DD` | Bad format → 400 |
| `transaction_date` must be in the past | Future date → 400 |
| `transaction_date == today` is rejected | Use the regular POS for today's credit sale → 400 |
| `reason` ≥ 20 chars | Too short → 400 |
| `grand_total` > 0 | ≤ 0 → 400 |
| `items[]` non-empty | Empty → 400 |
| Customer must exist | Not found → 404 |
| Customer must be in same branch | Mismatch → 400 |
| Caller must be admin / owner / super-admin | Cashier / manager → 403 |
| `branch_id` must be in caller's whitelist (Phase 2D) | Cross-branch attempt → 403 |

---

## 9. Tests

`backend/tests/test_phase3_historical_credit.py` — **13 cases, all passing**:

1. Cashier cannot create historical credit (403).
2. Missing reason rejected (400).
3. Missing customer rejected (400).
4. `transaction_date == today` rejected (400).
5. Historical credit AFTER latest count → inventory deducts.
6. Historical credit BEFORE latest count → inventory NOT deducted (and: with admin override, deducts with audit tag).
7. `transaction_date` and `encoded_at` stored separately.
8. Entry surfaces in `/reports/encoded-today` AND customer ledger (with `transaction_date`, `late_encoded`, `encoded_today`, `source`).
9. Old-day Z-report regular credit-sales section unaffected.
10. `customer.balance` increases by `grand_total`.
11. Cross-branch / non-privileged user blocked.
12. Preview is read-only (no balance / inventory / invoice changes).
13. List endpoint returns only historical-credit entries.

The existing 113-test regression suite remains the regression baseline (Phase 1A+1B+1C, 2A, 2B, 2C, 2D, 2E). Phase 3 adds 13 new tests for a total of **126 + 2 skipped**.

---

## 10. Approved architectural decisions

These are settled. Phase 4 must NOT relitigate them:

1. **Reuse the `invoices` collection.** No parallel `historical_credits` collection. The audit envelope is on the invoice + the existing `late_encode_log` + a dedicated `security_events` row.
2. **Single-step admin approval.** The endpoint itself is admin-or-owner-gated, so a separate "approved_by" PIN flow was not added. Frontend may add a confirmation modal — that's UX, not auth.
3. **`transaction_date == today` is rejected.** Today's credit sales use the regular POS. This endpoint is exclusively for past notebook entries.
4. **Old-day Z-reports stay byte-stable.** The Z-report query in `daily_operations.py` filters `source != "historical_credit_encoding"`, so re-querying an old day after a historical credit was encoded today does NOT change the regular-sales rows for that old day.
5. **Customer balance always goes up.** No knob to opt out of the AR effect — that would defeat the purpose.
6. **Inventory is opt-IN when transaction predates the latest count.** Default is to skip; admin must pass `allow_inventory_deduction=true` to override.

---

## 11. Out-of-scope for Phase 3

- Frontend page (would be `frontend/src/pages/admin/HistoricalCreditEncodingPage.js`) — **not implemented**. The endpoints are ready; UI is a Phase 4 task.
- Bulk import of historical credits from CSV — possible Phase 4 enhancement.
- Reverse / cancel a historical credit entry — currently relies on the existing void-invoice flow (which already handles AR reversal).
- Photo upload for `proof_url` — currently accepts a URL string only; integration with the existing object-storage upload flow is a Phase 4 task.

---

## 12. POS surfaces unaffected

Quick POS, Advanced POS, POS Terminal (online), POS Terminal offline → sync — no code changes in any of those write paths. `/api/unified-sale` and `/api/sales/sync` are byte-for-byte unchanged. Phase 3 adds new routes; it does not modify the existing sales flow.

---

## 13. Recommendation for Phase 4

Phase 4 (Cleanup / Dedup) is now a clean candidate:

- Build the admin-only frontend for Historical Credit Encoding (uses these endpoints).
- Consolidate `assert_branch_access` (utils/auth.py) and `get_branch_filter` (utils/branch.py) — same role-privilege rules in two helpers.
- Dedupe `/sales` vs `/sales-new` pages.
- Address the 11 pre-existing hardcoded-date legacy unit-test failures.
- **Test infrastructure cleanup** (see §14).

---

## 14. Known test infrastructure flakiness (pre-existing)

The full pytest run (`tests/test_phase1a_security.py + ... + tests/test_phase3_historical_credit.py + tests/phase2b/`) shows **occasional** intermittent failures of one test on roughly 1 of every 3 full-suite runs. Same flake reproduces **without** Phase 3 in the suite — confirmed by re-running the pre-Phase-3 baseline 3× and seeing the same intermittent failure pattern.

**Root cause** (pre-existing): pytest-asyncio's `session`-scoped event loop combined with shared MongoDB state lets one test's `set_org_context` call briefly leak into the next test's setup window. Each failing test passes 100% in isolation.

**Recommended Phase 4 fix**: switch to `function`-scoped asyncio loops, or add a per-test `set_org_context(None)` cleanup fixture, or move tests to a fresh ephemeral Mongo per session. Out of scope for Phase 3.

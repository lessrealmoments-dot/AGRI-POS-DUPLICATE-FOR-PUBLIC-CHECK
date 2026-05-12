# Phase 5+ Business Regression — Handoff & Runbook

_Last updated: 2026-05-12_
_Owner of next fork: see §10 "Recommended next fork"._

This document is the canonical handoff for the **Business Regression (BR)**
test phase. It captures what exists, how to run it, how to read it, what
it has already caught, and how to extend it.

---

## 1. Current BR status

| Metric | Value |
|---|---|
| BR test files | **8** (br1, br2, br3, br4, br5, br6, br99, br_iso) |
| Total expected-vs-actual rows (latest run) | **102** |
| Pass | **102** |
| Fail | **0** |
| Pass rate | **100.0%** |
| Zero-footprint cleanup | **Enforced** — module-scoped tenant, deleted by `organization_id` at file teardown via `cleanup_business_tenant()` |
| Pytest exit status | **0** |
| Latest report (stable pointer) | `/app/test_reports/business_regression_latest.json` |
| Latest report (timestamped) | `/app/test_reports/business_regression_<UTC-timestamp>.json` |

The suite is currently **green** and **safe to leave running on every
deploy candidate** as a money-conservation / inventory-conservation
guard.

---

## 2. Files in the BR suite

### Infrastructure

| File | Role |
|---|---|
| `backend/tests/business_regression/_fixtures.py` | Builds a fresh throw-away tenant (`make_business_day_tenant()`) and removes it again (`cleanup_business_tenant(org_id)`). All IDs are UUID-suffixed so re-runs never collide. |
| `backend/tests/business_regression/conftest.py` | Provides the `tenant` fixture (module scope) and the `record_result(scenario, step, expected, actual, evidence)` collector. On session end, dumps every collected row into `business_regression_<ts>.json` **and** `business_regression_latest.json`. |
| `tools/br_report.py` | Read-only stdlib summarizer over the JSON report. Supports `--failures-only` and `--scenario <prefix>`. Exit codes: 0 pass / 1 fail / 2 missing-or-invalid report. |

### Scenario files

| File | Coverage |
|---|---|
| `test_br1_baseline.py` | Baseline tenant shape: 1 org, 2 branches, 3 users with correct roles, 2 zero-balance customers, 0 pre-seeded products/suppliers, 6 zero-balance fund wallets, full `organization_id` scoping, UUID-suffixed IDs. |
| `test_br2_purchase_to_stock.py` | PO → stock flows: (a) terms PO full-receive (stock + AP + movement), (b) draft → receive (no AP), (c) duplicate-receive guard (400, no double stock, no second movement), (d) unknown PO → 404. |
| `test_br3_branch_transfer.py` | Branch transfer flows: (a) main→B2 full receive (draft moves nothing, send moves nothing, receive decrements src + upserts dst, total stock conserved, status `received`, OUT+IN movement rows, destination price set, capital-change audit row, auto internal-invoice created, **no wallet movement**), (b) insufficient stock pre-flight 400 (nothing changes), (c) branch-isolation 403 for manager not on src branch. |
| `test_br4_po_and_internal_invoice_paydown.py` | Money side of PO/AP and internal invoices: (a) PO terms paydown — partial then remainder, cashier balance math, `already paid` guard; (b) internal-invoice paydown — buyer bank decrement, supplier bank credit, two wallet movement rows, `already paid` guard; (c) payment guards — overpayment 400, unknown PO id 404, unknown invoice id 404, insufficient bank funds 400. |
| `test_br5_repack_flow.py` | Repack catalog + sale math: (a) catalog shape — `R-` child SKU, repack flag, sentinel zero cost, no inventory row for child, branch retail in `branch_prices`, org-scoped; (b) sale math — parent stock deducted by ratio, child derived availability drops, invoice line carries `is_repack` + derived `cost_price`, parent movement row with `Sold as repack:` marker, cashier wallet credited, COGS & margin derived from parent cost; (c) insufficient parent stock → 422 (no invoice, no movement); (d) branch isolation: B2 cannot sell child seeded into main. |
| `test_br6_z_report_close_day.py` | End-of-day money conservation: (a) close-day conservation (sales + payments → cashier balance, daily_closings row, over_short=0); (b) duplicate close & wallet-reset guard; (c) cross-day AR payment flows to next day's closing; (d) negative variance recorded faithfully. This file is the one that originally surfaced **B-3**. |
| `test_br99_reconciliation_snapshot.py` | Cross-scenario reconciliation snapshot to ensure org-level invariants are intact after the per-scenario tests in the suite have run. |
| `test_br_iso_internal_invoices.py` | Cross-tenant isolation regression specifically for `internal_invoices`: (1) cross-tenant read blocked, (2) internal invoices carry `org_id`, (3) same-tenant read still works. This is the proof-test for **B-1**. |

---

## 3. Bugs discovered and fixed by BR

### B-1 — `internal_invoices` cross-tenant isolation weakness

* **Summary:** `internal_invoices` was not in `TENANT_COLLECTIONS`, so inserts did not auto-stamp `organization_id` and the proxy did not auto-scope reads. `check_due_invoices` also scanned globally. Historical rows could be missing `organization_id`.
* **Fix (already shipped):**
  * Added `internal_invoices` to `TENANT_COLLECTIONS` in `backend/config.py`.
  * Refactored `check_due_invoices` in `backend/routes/internal_invoices.py` to loop tenant-by-tenant.
  * Added admin backfill `POST /api/admin/backfill/internal-invoices-org-id` (`backend/routes/admin_backfill_internal_invoices.py`) to stamp `organization_id` on any pre-existing rows.
* **Backfill requirement on deploy:**
  * Run the admin backfill **once** after this code lands in production. Run in dry-run mode first; preserve the dry-run output. Then run with `apply=true`. Details in `PHASE_5_DEPLOYMENT_HANDOFF.md`.
* **Proof test:** `test_br_iso_internal_invoices.py` — 3 scenarios, all pass.

### B-3 — Partial-sale cash double-count in Z-reports

* **Summary:** The Z-report's `cash_sales_pipeline` in `backend/routes/daily_operations.py` was counting the unpaid portion of partial sales as cash, inflating `total_cash_sales` and the cashier reconciliation.
* **Fix (already shipped):** `cash_sales_pipeline` now excludes the unpaid portion of partial sales. Money conservation re-verified end-to-end.
* **Proof test:** `test_br6_z_report_close_day.py :: br6.b_close_day_with_partial_sale` and the associated br6 close-day conservation rows.

### Status of BR-discovered product bugs

* **B-1: fixed and proven.**
* **B-3: fixed and proven.**
* **No active BR-discovered product bugs remain.**

> Note: **B-2** (branch-transfer doc codes generated with `org_id=""`) is a
> cosmetic-only finding from an earlier session; lookups self-heal via
> legacy fallbacks and there is no money or isolation leak. Deferred to
> backlog by user.

---

## 4. How to run the suite

```bash
# Run the full BR suite
cd /app/backend && python3 -m pytest tests/business_regression/ -v

# Run a single BR file
cd /app/backend && python3 -m pytest tests/business_regression/test_br6_z_report_close_day.py -v

# Render the JSON report as a one-screen summary
cd /app && python3 tools/br_report.py

# Show failed rows only (any path it points at)
cd /app && python3 tools/br_report.py --failures-only

# Filter by scenario prefix (e.g. all br6 scenarios)
cd /app && python3 tools/br_report.py --scenario br6
```

**Do NOT** run the full legacy `pytest tests/` as part of BR — that suite
has known DB pollution issues. BR is intentionally scoped to
`tests/business_regression/` only.

---

## 5. How to interpret results

* **pytest result** — the standard pass/fail tree per scenario. This is the source of truth for "did anything assert fail?".
* **Expected-vs-actual rows** — every meaningful assertion calls `record_result(scenario, step, expected, actual, evidence)`. Each call writes one row into `business_regression_latest.json`. A row is `PASS` iff `expected == actual`.
* **`business_regression_latest.json`** — stable pointer that always reflects the most recent run. Has top-level `generated_at`, `pytest_exitstatus`, `row_count`, `pass_count`, `fail_count`, and a `rows[]` array (each row: `scenario`, `step`, `expected`, `actual`, `status`, `evidence`, `recorded_at`). A timestamped copy is also written next to it.
* **`tools/br_report.py`** — renders the JSON as a terminal summary: header, scenario table, and (when failures exist) a per-row failure detail block. Read-only, stdlib only.
* **Exit codes (br_report.py):**
  * `0` — every row in scope passed
  * `1` — at least one row failed
  * `2` — report file missing or not valid JSON

> A green pytest run with `br_report.py` exit `0` means: every BR scenario asserted what it expected, and the structured evidence is on disk for review.

---

## 6. Deploy-gate recommendation

**Treat BR as a deploy-gate check, not a nightly.**

* It runs in ~seconds locally, has zero DB footprint, and exercises the money + inventory invariants that matter most in production (PO → stock, branch transfers, paydowns, repack math, Z-report close-day, tenant isolation).
* These are exactly the invariants where a regression would corrupt customer books — making them a nightly is too lax.

**Practical command to run before any production deploy:**

```bash
cd /app/backend && python3 -m pytest tests/business_regression/ -v \
  && cd /app && python3 tools/br_report.py
```

Block the deploy if either step is non-zero. Later, when total BR runtime exceeds ~30s, split into `br-fast` (br1–br3, isolation) gating and `br-slow` (br4–br6, br99) running in parallel with the deploy step.

---

## 7. Cleanup / DB pollution policy

The BR suite is built around a single non-negotiable rule:
**no row written by a BR test may survive that test.**

* **Fresh throw-away tenant per file.** `make_business_day_tenant()` creates a brand-new organization, 2 branches, 3 users, 2 customers, 6 fund wallets, with UUID-suffixed IDs. Module-scoped — every BR file gets its own tenant.
* **Randomized IDs.** All IDs are UUID-suffixed (`p2b-org-<uuid>`, `p2b-br-<uuid>`, etc.) so re-runs never collide and no fixture ever shadows real data.
* **Cleanup by `organization_id`.** `cleanup_business_tenant(org_id)` walks the documented `TENANT_COLLECTIONS` list and deletes every row whose `organization_id` matches the throw-away tenant. This runs in `finally:` so it executes even on assertion failure.
* **Monitored collections.** Anything that takes tenant-scoped writes must be in `TENANT_COLLECTIONS` (this is how the proxy auto-injects `organization_id` and how cleanup knows what to delete). Adding a new tenant-scoped collection without registering it here is the same class of bug as B-1.
* **Zero-footprint expectation.** A BR test that writes to a collection not covered by cleanup is a defect in the test, not a tolerated cost. Fix the test or expand `TENANT_COLLECTIONS`.
* **Do not run full brittle legacy `pytest tests/` as part of BR.** The legacy suite has documented DB-state pollution and hardcoded dates. Run BR on its own.

---

## 8. Known limitations / deferred BR coverage

Backlog, in roughly recommended order:

1. **`test_br7_repack_branch_transfer.py`** — repack → branch transfer → reconcile, asserting cost basis carry-through across the seam.
2. **`test_br8_returns_refunds.py`** — sale-return / refund money + stock conservation, including refund-as-cash-out behavior in close-day.
3. **Offline sale replay** — server-side reconciliation of offline-queued sales.
4. **Bank/digital PO payment** — currently we cover cashier-funded PO paydown; bank/digital wallet paydown needs explicit coverage.
5. **Per-tenant scheduler tick** — `check_due_invoices` and any other scheduled job, asserted tenant-by-tenant.
6. **JIT repack retail save** — first-sale-time retail price persistence path.
7. **Override PIN happy path** — manager-PIN insufficient-stock override flow.
8. **Refund-as-cash-out close-day behavior** — explicit row coverage in `daily_closings`.
9. **Z-report PDF / SMS** — output side, not just numeric side.
10. **`tools/br_history.py`** — diff the latest run against the previous timestamped report (regression-direction signal).
11. **`br_report --json`** — slim machine-readable summary for CI gates.

These are all additive. None are blocking deploy today.

---

## 9. Production deployment notes tied to BR

The full deploy runbook lives in `PHASE_5_DEPLOYMENT_HANDOFF.md`. The
BR-driven items that **must** be executed on the deploy that carries
B-1 and B-3 fixes are:

* **B-1 — `internal_invoices` backfill (one-time):**
  1. Run `POST /api/admin/backfill/internal-invoices-org-id` in dry-run mode (no writes).
  2. **Preserve the dry-run output** (counts per tenant, candidate row ids) as a deploy artifact.
  3. Run again with `apply=true` only after dry-run looks sane.
  4. After it succeeds, re-run BR isolation: `python3 -m pytest tests/business_regression/test_br_iso_internal_invoices.py -v`.
* **B-3 — close-day partial-sale behavior change:**
  * `total_cash_sales` will now be lower for any day that contains partial sales, because the unpaid portion is no longer counted as cash. This is correct, but operators who have been mentally calibrated to the old (inflated) number should be told to expect a step-change downward.
  * **After deploy, smoke a real daily close** end-to-end in production on the first business day post-deploy. Compare to the operator's manual count. Variance should be ≤ what the cashier actually misses by, not a systematic bias.
* **General:** keep the BR command in the deploy script (§6). If BR ever goes red on a deploy candidate, the deploy must be blocked, not waived.

---

## 10. Recommended next fork

* **Option A — Stop and deploy/smoke.** BR is green, B-1 and B-3 are fixed and documented, deploy artifacts (backfill, close-day smoke) are listed. The most user-visible value right now is shipping these fixes to production and verifying them.
* **Option B — Continue BR expansion.** Start with an **assessment-first** pass for `test_br7_repack_branch_transfer.py` (same shape as the assessments we did for br5–br6: surface schema, map ids, list invariants, propose `record_result` rows, then ask for approval before writing the file). br7 is the highest-leverage next test because it covers the repack↔branch-transfer seam where cost propagation is the most fragile and currently untested.
* **Option C — Return to product work.** Resume **Phase 4A.2 Concurrent Held-Sales Queue (P1)**. BR will catch any money/inventory regression the queue work introduces.

**Recommendation:** **Option A** if a deploy slot is available in the next 1–2 days. **Option B** if there's a gap and we want to widen the safety net before HeldSalesQueue lands. **Option C** only after A is done.

---

_End of handoff. Treat this file as the source of truth for "what is BR, how do I run it, and what does it guarantee?" Any future BR change should update this document in the same commit._

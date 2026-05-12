# Phase 5 — Post-Deploy Next-Fork Handoff

_Created: 2026-05-12 · After successful production deploy to https://agri-books.com_

This file is the **canonical starting point for the next fork.** Read it
end-to-end before any planning. Do **not** code anything until the
opening prompt in §8 has been run and an alignment assessment has been
returned for whichever workstream the owner picks.

---

## 1. Stable deployed status

| Item | Status |
|---|---|
| Production deploy of fix bundle | ✅ Live |
| Server | `srv1427434` (`76.13.215.32`) at `/var/www/agribooks` |
| Production HEAD commit | `8083f89a` on `main` (FF from `c552decf`, 67 commits) |
| Backend supervisor process | `agribooks-backend` RUNNING (pid restarted post-pull) |
| Frontend bundle served by nginx | `build/static/js/main.802debfd.js` (rebuilt + nginx reloaded) |
| H-1 orphan-invoice backfill | ✅ Dry-run + apply complete (2 invoices, ₱170,331.00) |
| Smoke verified by owner | ✅ Confirmed |
| Active launch blockers | **None** |
| Deploy artifacts | `/var/log/agribooks/deploys/<STAMP>/h1_dryrun.json`, `h1_apply.json`, `h1_dryrun_after.json` |

The system is in a **safe-to-leave** state. The next fork can pick up
any workstream without having to first untangle deploy/data debt.

---

## 2. Important fixes now in production

### B-1 — `internal_invoices` tenant isolation
* **What:** `internal_invoices` was not in `TENANT_COLLECTIONS`, so cross-tenant leakage was possible and pre-existing rows could be missing `organization_id`. `check_due_invoices` scanned globally.
* **Fix:** Added `internal_invoices` to `TENANT_COLLECTIONS` in `backend/config.py`. Refactored `check_due_invoices` in `routes/internal_invoices.py` to loop tenant-by-tenant. One-time admin backfill `POST /api/admin/backfill/internal-invoices-org-id` available.
* **Proof:** `backend/tests/business_regression/test_br_iso_internal_invoices.py` — green.
* **Status:** ✅ Closed.

### B-3 — Partial-sale cash double-count in Z-reports
* **What:** `cash_sales_pipeline` in `routes/daily_operations.py` was counting the unpaid portion of partial sales as cash, inflating `total_cash_sales` and the cashier reconciliation.
* **Fix:** Excluded the unpaid portion of partial sales from the pipeline.
* **Proof:** `backend/tests/business_regression/test_br6_z_report_close_day.py :: br6.b_close_day_with_partial_sale` — green.
* **Status:** ✅ Closed.

### H-1 — Prepared-order invoice visibility + race
* **What:** Old finalize path in `routes/sales.py` used `db.invoices.replace_one(filter, invoice)`. The `TenantCollection` proxy does NOT wrap `replace_one`, so the call bypassed the proxy AND wrote a replacement document with NO `organization_id`. Result: customer balance increased correctly (via the proxied `customers.update_one` `$inc`), but the invoice was invisible to every tenant-scoped read (Sales History, Payments, statements, receipt lookup).
* **Fix (code, commit `200b6c1e`):** Replaced `replace_one` with guarded `find_one_and_update` (atomic status flip from `for_preparation` → `processing`) followed by `update_one({"id": ...}, {"$set": invoice})`. Both go through the proxy. `$set` payload does NOT include `organization_id`, so the org_id stamped at draft-create time survives.
* **Fix (data):** New `POST /api/admin/backfill/invoices-org-id` (admin role, `?apply=1` confirm flag, 3-path resolver: branch → customer → cashier; conflicts/unresolved skipped never guessed; audit log row per stamp).
* **Live impact restored:** GREEN HANDS AGRIVET SUPPLY (₱95,528 / `SI-MB-001027`) and ANGEL AGRIVET SUPPLY (₱74,803 / `SI-MB-001024`) — totalling **₱170,331.00** — were stamped and reappeared in Sales History + Payments + statements.
* **Proof:** `backend/tests/business_regression/test_br_prepare_order_completion_visibility.py` — 6 scenarios (cash, digital, split, partial, credit, orphan-backfill), 83 expected-vs-actual rows, all green. Idempotent across re-runs.
* **Status:** ✅ Closed (code + data).

---

## 3. Current BR status

Snapshot from `/app/test_reports/business_regression_latest.json`
(generated `2026-05-12T07:06:43Z`):

| Metric | Value |
|---|---|
| BR test files | **8** (br1, br2, br3, br4, br5, br6, br99, br_iso, br_prep) |
| Total expected-vs-actual rows | **185** |
| Pass | **185** |
| Fail | **0** |
| Pass rate | **100.0 %** |
| Pytest exit | 0 |
| Latest report | `/app/test_reports/business_regression_latest.json` |
| Timestamped artifact | `/app/test_reports/business_regression_<ts>.json` |

Run / read:
```bash
cd /app/backend && python3 -m pytest tests/business_regression/ -v
cd /app && python3 tools/br_report.py                 # full summary
cd /app && python3 tools/br_report.py --failures-only # just failures
cd /app && python3 tools/br_report.py --scenario br_prep   # filter
```

**Zero-footprint policy** is enforced — see §7 of
`PHASE_5_BUSINESS_REGRESSION_HANDOFF.md`. Every BR file uses the
module-scoped throw-away tenant + `cleanup_business_tenant()` strict
teardown. Adding a tenant-scoped collection without registering it in
`TENANT_COLLECTIONS` is the same class of bug as B-1.

**Do NOT** run the full legacy `pytest tests/` suite as part of BR work
— that suite has documented DB-state pollution and hardcoded dates and
will give false negatives. BR is scoped to
`tests/business_regression/` only.

---

## 4. Existing handoff documents

| Doc | Purpose |
|---|---|
| `/app/memory/PHASE_5_DEPLOYMENT_HANDOFF.md` | Source of truth for the deploy itself + H-1 post-deploy runbook (§4.A backfill, §4.B locked-in regression). Updated this session. |
| `/app/memory/PHASE_5_BUSINESS_REGRESSION_HANDOFF.md` | BR suite contract: what BR is, how to run it, how to read it, zero-footprint policy, deferred coverage backlog. |
| `/app/memory/PHASE_5_SURGICAL_FIX_HANDOFF.md` | Earlier-session record of surgical fixes (pre-this-deploy). |
| `/app/memory/PRD.md` | Long-running product requirements doc. |
| `/app/memory/CHANGELOG.md` | Append-only build log. |
| `/app/memory/ROADMAP.md` | Prioritised backlog. |

Read these in this order when picking up the next fork:
1. **This file** (orient on stable state + workstream choice)
2. `PHASE_5_DEPLOYMENT_HANDOFF.md` (if any deploy follow-up needed)
3. `PHASE_5_BUSINESS_REGRESSION_HANDOFF.md` (if BR work picked)
4. `PRD.md` (any new feature work)

---

## 5. Recommended next BR candidate — `test_br7_repack_branch_transfer.py`

**Purpose:** Lock down the seam between two scenarios that are
individually green but whose interaction is currently untested:
* `br3` — branch transfer of parent stock between branches
* `br5` — repack/child sale at a single branch

**Owner's repack business rule (must be honoured by the test):**
* Parent / bulk stock is the **true inventory line** and the source
  of capital. The parent row in `inventory` is what holds quantity
  and cost basis.
* The repack / child SKU is the **sellable unit**. It is NOT a separate
  inventory row; its availability is **derived** from
  `parent.quantity × units_per_parent` (the "conversion ratio").
* A repack sale deducts **parent stock by the conversion ratio**
  — e.g. selling 1 child unit when the ratio is 20-per-parent
  decrements parent quantity by `1 / 20`.
* Repack COGS is **derived from parent cost / units_per_parent**
  (plus any add-on/labor cost if the schema supports it).
* Repack retail price is **independent and branch-specific**
  (held in `branch_prices` / `branch_transfer_price_memory` for the
  child SKU, NOT the parent).
* Net margin on a repack sale = `(repack retail × qty) - derived COGS`.

**Candidate flow for the test:**
1. Seed parent/bulk stock in Main branch only.
2. Branch-transfer parent stock from Main → Branch B (via the same
   `routes/branch_transfers.py` path br3 already exercises).
3. Verify Branch B parent inventory increases by the transferred qty;
   Main parent inventory decreases by the same.
4. Verify the child/repack SKU's derived availability in Branch B
   updates accordingly (parent × ratio).
5. Sell N child units at Branch B (via `/api/unified-sale`, same path
   br5 exercises).
6. Verify Branch B parent stock decremented by `N / units_per_parent`.
7. Verify **Main branch parent stock is unchanged** by the Branch B
   sale (critical isolation invariant).
8. Verify derived cost/capital on the resulting invoice line equals
   `parent_cost_at_transfer / units_per_parent` (NOT the source-branch
   parent cost — the transfer carries a `transfer_capital` value).
9. Verify branch-specific retail/margin on the child SKU at Branch B
   is honored independently of Main's child retail.
10. Verify zero residual inventory drift across both branches when the
    transaction completes (sum of all parent stock should equal initial
    seed quantity minus the consumed child units / ratio).

**Do NOT implement yet.** The next fork must do an assessment-first
pass (same shape as br5/br6 assessments) before writing the file:
* Map the actual `branch_transfer_price_memory` schema.
* Confirm how cost is carried across the transfer boundary.
* List the exact `record_result` rows that prove each invariant.
* Get owner approval.
* Then implement against module-scoped throw-away tenant with strict
  cleanup.

---

## 6. New feature candidate — Prepared Order Completion SMS

### Business purpose
When a prepared order / for-preparation order is finalised, send the
customer a short SMS so they know their order is ready and what they
owe (if anything). Operators commonly:
1. Prepare the goods and set the customer.
2. Temporarily complete as credit because the customer is elsewhere.
3. Later, the customer returns; payment is taken; status flips to
   paid.

Step 1→2 today is silent — the customer has no signal that their goods
are ready or that there's an outstanding balance to settle. The
proposed SMS closes that loop.

### Trigger conditions (ALL must hold)
* `payment_type` in {`cash`, `digital`, `split`, `partial`, `credit`}
* `draft_invoice_id` was present on the finalize request (i.e. this
  came from a prepared-order / for-preparation flow, NOT a normal
  direct sale)
* `customer_id` is set AND `customer_name != "Walk-in"`
* Customer has a phone number that passes basic e.164 / 10-11 digit
  validation
* The unified-sale response succeeded (invoice persisted with valid
  `organization_id` — re-using the H-1 regression assertion as a
  gate is reasonable)
* SMS is not already sent for this `invoice.id` (idempotency key —
  see below)

### Exclusions (any one → no SMS)
* Walk-in customer
* No phone number on customer
* Direct sale (no `draft_invoice_id`) — normal POS sales don't get SMS
* Finalize failed / 4xx
* Tenant has disabled prepared-order SMS in settings (see §6.5)

### Proposed message content

| Scenario | Message |
|---|---|
| Cash / Digital / Split (paid in full) | `Hi {customer}, your order {invoice_no} is ready. Total ₱{grand_total}. Thank you!` |
| Partial | `Hi {customer}, your order {invoice_no} is ready. Total ₱{grand_total} · Paid ₱{amount_paid} · Balance ₱{balance}. Please settle when convenient. Thank you!` |
| Credit | `Hi {customer}, your order {invoice_no} is ready for pickup. Outstanding balance ₱{balance}. Thank you!` |

These are starting points — wording should be reviewed by the owner
during the assessment.

### Implementation concerns the next fork must surface BEFORE coding

1. **Duplicate-prevention / idempotency.** Finalize can be retried on
   offline replay, or in the rare case of an HTTP-level retry. Two
   options:
   * `sms_queue.dedupe_key = f"prep_done:{invoice.id}"` and unique-index it.
   * Mark `invoice.prep_sms_sent_at` after a successful send.
   The next fork must pick ONE and confirm it matches existing SMS
   queue infrastructure.

2. **SMS failure handling.** SMS gateway is async and can fail
   (network, no credits, invalid number). Sale finalization must NOT
   block on SMS. Options:
   * Fire-and-forget enqueue, log failure in `sms_queue.status`
   * Best-effort send with retries from the existing scheduler
   The existing `sms_*` tests (`test_sms_branch_filtering_153.py`,
   `test_sms_gateway_retry_cap_177.py`, `test_sms_signature_fallback_178.py`)
   tell us a retry-capped queue already exists — the next fork should
   re-use it, not reinvent.

3. **Tenant configurability.** Some tenants will want this off (cost,
   privacy, customer preference). Owner needs a settings toggle:
   `business.prepared_order_sms_enabled: bool = false` (default off
   until each tenant opts in). Per-customer opt-out
   (`customer.sms_opt_out`) is also probably necessary — check current
   schema.

4. **Existing SMS infrastructure discovery.** Before writing any new
   code, the next fork MUST:
   * Read `backend/routes/sms.py` end-to-end.
   * Read every `tests/test_sms_*.py` and identify what's already
     covered (branch filtering, retry-cap, signature fallback, terminal
     credential).
   * Identify the existing `sms_queue` collection schema and the
     enqueue helper.
   * Decide if this hook is best placed inside `_finalize_draft_online`
     in `routes/sales.py`, in the existing SMS scheduler, or in a new
     small helper.

5. **Audit / logging.** Each send (or skip) should be auditable:
   `sms_queue` insert with `kind: "prepared_order_ready"`,
   `invoice_id`, `customer_id`, `phone`, `payload`, `enqueued_at`,
   `sent_at`, `delivery_status`. Re-use the existing fields, do not
   invent a parallel collection.

6. **Provider discovery.** Sample-site agri-books.com is wired to a
   provider (likely the Kotlin Android SMS gateway based on
   `memory/KOTLIN_SMS_APP_GUIDE.md`). The next fork must confirm which
   tenant uses which provider before writing.

### Recommendation
**Do NOT code this yet.** The next fork should treat this as an
assessment-first feature exactly like br7 — produce an alignment
assessment that surfaces (a)-(f) above, then wait for owner approval
before any implementation.

---

## 7. Strict workflow rule (applies to ALL next-fork work)

For every workstream from this point forward:

1. **Produce a general prompt** stating the concern, the proposed
   change, and any obvious risks.
2. **Ask Emergent for a codebase-aware alignment assessment** (the
   "no-code first" pass — the same pattern we used for tools/br_report
   and for the H-1 backfill).
3. **Decide AFTER the assessment** — the assessment must surface
   existing helpers that can be reused, the smallest surgical fix,
   regression risk, and whether a BR test should be written first.
4. **Only then implement.** Implementation must:
   * Re-use existing helpers / modules (no duplicates).
   * Stay surgical to the agreed scope.
   * Add a regression test in `tests/business_regression/` if the
     touched code crosses the money or inventory invariants.
   * Update the relevant handoff doc in the same change.
5. **Never** run destructive commands on production without explicit
   owner approval and a saved deploy-artifact.

---

## 8. Suggested next-fork opening prompt

Copy-paste the block below as the FIRST message in the next fork:

```
Read /app/memory/PHASE_5_POST_DEPLOY_NEXT_FORK_HANDOFF.md end-to-end.
Then:

1. Confirm current status: deployed, BR green, H-1 closed (code + data),
   no active blocker.
2. Run `python3 tools/br_report.py` and report:
   * total tests, total rows, fail count
   * any drift from the 185-row baseline captured in §3
3. Confirm `git status` is clean on /app and `git log -1 --oneline`
   matches origin/main.

Do NOT code yet.

Then, recommend which of the two candidates to tackle first and why,
weighing:

  (A) br7 repack branch transfer regression
      — locks down a real-world seam that produces money & inventory
        invariants; high leverage; pure test work; zero deploy risk.
      — owner business rule in §5 of the handoff.

  (B) Prepared-order completion SMS notification feature
      — adds customer-facing value; uses existing SMS infrastructure;
        needs an assessment-first pass to confirm hook point,
        idempotency, and tenant toggle.
      — concerns documented in §6 of the handoff.

For your recommendation, also explain how the chosen workstream aligns
with the broader system purpose:
  * BR coverage protects the money / inventory invariants that matter
    in production. Every new BR file pays for itself the first time it
    catches a regression (B-1, B-3, H-1 are all proof).
  * SMS notification turns prepared-order workflow into a closed loop
    — currently silent for the customer; owner has explicitly flagged
    this as a friction point.

Wait for the owner's choice. Then run the assessment-first pass on the
chosen workstream (no implementation yet), surface a written alignment
opinion, and ask for approval before writing any code.

If anything in §1–§3 of the handoff does not match the live state,
STOP and ask. Do not silently proceed.
```

---

## 9. Safe-to-fork checklist

* [x] Production deployed and smoke-verified.
* [x] No active P0 / P1 blocker.
* [x] All session work committed (`8083f89a` on `main`, server pulled).
* [x] BR suite green (185 / 185).
* [x] Backfill artifacts saved.
* [x] Handoff written (this file).

**Safe to fork.**

— End of handoff —

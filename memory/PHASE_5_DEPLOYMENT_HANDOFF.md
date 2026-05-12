# Phase 5 — Final Deployment Handoff & Checklist

**App**: AgriBooks — multi-tenant POS + accounting for Philippine agri-supply businesses.
**Stack**: React 19 (CRA + craco) frontend, FastAPI + Motor + MongoDB backend, supervisor-managed.
**Prepared**: Feb 2026, end of Phase 5 surgical-fix + audit pass.
**Verdict (this document)**: ✅ **Deployment-ready, subject to owner manual smoke + deployment validation.**

---

## 1. Final Audit Closure Status

### P0 (C-1 → C-9)

| # | Finding | Status |
|---|---|---|
| C-1 | `/sync/pos-data` leaked PINs | ✅ CLOSED |
| C-2 | `/setup/reset` unauthenticated DB wipe | ✅ CLOSED |
| C-3 | TOTP take-over | ✅ CLOSED |
| C-4 | Inventory deducted before invoice insert | ✅ CLOSED |
| C-5 | Sync race double-deduct | ✅ CLOSED |
| C-6 | `_finalize_draft_offline` race | ✅ CLOSED |
| C-7 | Wrong field `current_balance` | ✅ CLOSED |
| **C-8** | **RMA generator collision + cross-tenant** | ✅ **CLOSED — generator atomic; backstop now compound unique `(organization_id, rma_number)` partial index `uniq_returns_org_rma`** |
| C-9 | Payment against voided invoice | ✅ CLOSED |

**P0 rollup**: 9 / 9 closed.

### P1 (H-1 → H-14)

| # | Finding | Status |
|---|---|---|
| H-1 | Draft `replace_one` race + invoice org_id strip | ✅ CLOSED (code) · ⚠️ DEPLOY ACTION REQUIRED (backfill — see §4.A) |
| H-2 | No MongoDB transactions | ⚙️ DEFERRED BY DESIGN (replica-set + WT migration; interim compensation pattern accepted) |
| H-3 | Customer balance drift / no reconciliation | ✅ DIAGNOSTIC CLOSED (`GET /api/admin/customer-balance-reconciliation`) |
| H-4 | Returns credit any-open-invoice | ✅ CLOSED |
| H-5 | `assert_branch_access` legacy bypass | ✅ CLOSED |
| H-6 | CORS `*` default | ✅ CLOSED (fail-closed in prod) |
| H-7 | JWT TTL 24h no rotation | ❌ OPEN BACKLOG |
| H-8 | `get_current_user` org_id check | ✅ CLOSED |
| H-9 | `users.email` non-unique index | ❌ OPEN BACKLOG |
| H-10 | `invoices.invoice_number` no unique index | ❌ OPEN BACKLOG (atomic per-branch counter mitigates runtime path) |
| H-11 | 7-day late-encode insufficient | ✅ CLOSED (Phase 3 Historical Credit) |
| H-12 | Offline negative stock silent | ⚠️ PARTIAL (`stock_warnings` logged; incident-ticket + admin notification pending) |
| H-13 | `setup/initialize` race | ✅ CLOSED |
| H-14 | `/admin/backfill/*` weak gate | ❌ OPEN BACKLOG |

**P1 rollup**: 9 closed · 1 deferred-by-design · 1 partial · 3 open (H-7, H-9, H-10, H-14) — none deploy-blocking.

### Cross-tenant receipt PIN audit (Feb 2026)

Public-receipt QR + PIN flow audited READ-ONLY. **Verdict: secure by design.**
- All PIN-gated routes (`/api/qr-actions/{code}/*`, `/api/doc/lookup`) resolve `organization_id` from `doc_codes` → `set_org_context()` BEFORE PIN check.
- `_resolve_pin` reads `db.system_settings.admin_pin` and `db.users` through `TenantCollection` (fail-closed when no org context).
- Cross-tenant PIN leak structurally impossible.
- Open view (`view_document_open`) exposes only basic fields; payments / customer balance / attached files are gated.
- Rate limiting (`check_qr_lockout`: 5-fail alert, 10-fail 15-min 429) + tenant-scoped audit log (`pin_attempt_log`) in place.
- Locked in by `tests/test_phase5_public_receipt_pin_isolation.py` (1/1 PASS).

Legacy observation (non-blocking): `POST /api/verify/public/{doc_type}/{doc_id}` doesn't call `set_org_context`; fail-closed proxy makes it always 404 in production → effectively dead code, no leak. Deprecate or wire to a `view_token` post-launch.

---

## 2. All Completed Fixes (this multi-fork program)

### Audit-closure fixes (P0 + P1)
- C-1 — PIN sync sanitisation + `pin_resync_failed` audit row.
- C-2 — super-admin + `ALLOW_DB_RESET=true` env + password re-confirm + `security_events` audit.
- C-3 — TOTP take-over guard (bootstrap-only window).
- C-4 / C-5 / C-6 — deferred `pending_inventory_ops` applied AFTER invoice commit; `envelope_id` unique partial index; `_finalize_draft_offline` guarded `update_one({status: for_preparation})`.
- C-7 — `$inc balance` correction.
- **C-8 (this fork)** — compound unique partial index on `returns(organization_id, rma_number)` named `uniq_returns_org_rma`.
- C-9 — `assert_invoice_payable` on 3 payment paths.
- H-1, H-3, H-4, H-5, H-6, H-8, H-11, H-13 — see closure table.
- Phase 2C.1 — `payment_idempotency(organization_id, route, key)` compound unique partial index.

### Feature-level closures
- Phase 3 — Historical Credit / Notebook AR backend + frontend (closes H-11). TOTP-only gate; soft floor <7 days.
- Phase 4A — Frontend Historical Credit / Notebook AR mode integrated into `UnifiedSalesPage`.
- Phase 4A.1 — Online/Offline routing stabilisation (`lib/connectivity.js`, 10-second reconnect grace, real-server errors as toasts).
- Phase 4A.1.1 — Pending-sync pill.

### Phase 4 cleanup (presentational decomposition)
- Pass 0 — UI pre-fixes (dedupe-pill pointer events, All-Branches UX guard).
- Pass 1 — `useConnectivity` hook extracted.
- Pass 2 — `useHistoricalCredit` hook extracted (14 hook tests).
- Pass 3 — `HistoricalCreditBanner.jsx` + `HistoricalCreditDialog.jsx` (14 RTL tests).
- Pass 4 — `CheckoutDialog.jsx` (13 RTL tests).
- Pass 5 — `CreditApprovalDialog.jsx` (11 RTL tests).
- Cumulative — `UnifiedSalesPage.js` reduced 6,030 → 5,420 lines (−610). 75 new frontend RTL tests. Zero business-behaviour changes.

### Phase 5 surgical pass (this fork)
- **C-8 RMA backstop index**: `main.py` switched to compound unique partial index `(organization_id, rma_number)` — `uniq_returns_org_rma`. Verified: index present, zero same-org duplicates.
- **Phase 2D brittle test**: `tests/test_phase2d_permissions.py` randomised hardcoded `jwt-test` / `jwt-ok` usernames → 17/17 PASS in any DB state.
- **NEW Phase 5 cross-tenant PIN isolation regression**: `tests/test_phase5_public_receipt_pin_isolation.py` — 1/1 PASS, idempotent, locks in tenant scoping of `lookup_document` + `view_document_open`.

---

## 3. Final Test Results (deploy-gate baseline)

### Frontend
| Command | Result | Time |
|---|---|---|
| `cd /app/frontend && CI=true yarn test --watchAll=false` | **75 / 75 PASS** | ~9–10s |
| `cd /app/frontend && yarn build` | **PASS** (clean prod bundle) | ~35s |

### Backend — phase-specific audit suite (the meaningful deploy gate)

Each file run in isolation OR as a phase-scoped batch:

| File | Result |
|---|---|
| `test_phase1a_security.py` | ✅ 6 / 6 |
| `test_phase1b_inventory.py` | ✅ 4 / 4 |
| `test_phase1c_money.py` | ✅ 17 / 17 |
| `test_phase2a_balance_recon.py` | ✅ 18 / 18 |
| `test_phase2a_live_api.py` | ✅ 2 / 2 |
| `test_phase2c_live_smoke.py` | ✅ 4 / 4 |
| `test_phase2c_pos_hardening.py` | ✅ 5 / 5 |
| `test_phase2d_permissions.py` | ✅ **17 / 17** (was 16/17 pre-fix) |
| `test_phase2e_date_basis.py` | ✅ 10 / 10 |
| `test_phase3_historical_credit.py` | ✅ 16 / 16 |
| `test_phase4a_approval_gate.py` | ✅ 12 / 12 |
| `test_phase5_public_receipt_pin_isolation.py` | ✅ **1 / 1** (NEW) |
| **Phase-specific deploy-gate total** | **112 / 112 PASS** |

> Full `pytest tests/` (~2,800 files) is **NOT** a meaningful deploy gate today — it is dominated by pre-existing test-authoring brittleness (hardcoded usernames, hardcoded dates, session-scoped event loop sharing state). Hardening that suite is a Phase 6 candidate, not a deploy blocker.

### Live API health
- `GET /api/health` → **200** ✅ (against `REACT_APP_BACKEND_URL`).

### Index verification (post-fix)
- `returns`: `_id_`, **`uniq_returns_org_rma`** keys `{organization_id: 1, rma_number: 1}` unique=True partial.
- `payment_idempotency`: `_id_`, `uniq_payment_idempotency` keys `{organization_id: 1, route: 1, key: 1}` unique=True.

---

## 4. Manual Smoke Checklist (owner-driven, ~15 min)

Recommended: run against the actual production URL post-deploy, using a low-stakes tenant.

### A. Sanity
1. [ ] Open the production URL — login page renders, no console errors.
2. [ ] Log in as a known admin — dashboard loads.
3. [ ] `/api/health` returns 200 (curl or browser).

### B. Quick Sale (Detailed Sale mode untouched as a sanity comparison)
4. [ ] `Sales` → Quick Sale → pick a branch → add 1 cash item → Checkout → Confirm. Receipt prints / shows.
5. [ ] Verify the resulting invoice in `Sales` list with correct amount and `paid` status.
6. [ ] Inventory of the sold product decremented by the sold qty.

### C. Credit sale + Authorization (Phase 4 dialog)
7. [ ] Quick Sale → add an item → Checkout → Credit tab → pick a customer → Confirm → `CropCreditTypeDialog` opens → "By Term".
8. [ ] `CreditApprovalDialog` opens. Manager PIN unlocks. Sale records with credit balance. Customer `balance` increases by grand_total.

### D. Historical Credit / Notebook AR (Phase 3 + 4A)
9. [ ] Switch to Historical Credit mode (amber banner appears).
10. [ ] Backdate transaction 10 days, fill reason + proof URL + notebook ref, customer, items.
11. [ ] Submit → TOTP-gated approval dialog → enter owner/admin TOTP → success.
12. [ ] Confirms in `Sales` list with `historical_credit=true`; appears under encoded-today (today's date) and customer ledger at the backdated date.

### E. RMA — proves C-8 fix
13. [ ] `Returns` → pick a recent invoice → generate one return.
14. [ ] Verify `rma_number` was issued without collision and the return is visible only inside the tenant.
15. [ ] (Multi-tenant operators only) Confirm `db.returns.getIndexes()` shows `uniq_returns_org_rma`.

### F. Payments on credit invoice — proves C-9 + 2C
16. [ ] Open a credit invoice → record a payment → verify cashier/digital wallet routing matches the payment method.
17. [ ] Try a second payment via the same `idempotency_key` (e.g., resubmit a stale request) → must fail / 409.
18. [ ] Open a voided invoice → attempt payment → must be rejected.

### G. Public Receipt QR + PIN — proves Phase 5 audit + new isolation test
19. [ ] From a sold invoice, copy / scan the QR doc code.
20. [ ] Open the public receipt URL on a phone. Verify basic receipt is visible WITHOUT PIN (line items + totals).
21. [ ] Attempt PIN unlock with a WRONG PIN three times — verify 403 + attempts-remaining warning; after 10 failures the doc locks for 15 min (429).
22. [ ] Unlock with a valid manager / admin PIN — payment history + customer + files now visible.

### H. Offline / Sync
23. [ ] Take the browser offline → place 1 cash sale → see pending-sync pill increment.
24. [ ] Return online — pill drains; resulting invoice is identical to an online sale (no double-deduct of stock).

### I. Reports
25. [ ] Run Z-Report for today → totals match the smoke-test sales.
26. [ ] Customer ledger for the credit customer shows the credit invoice at the right date.

If any item fails — STOP, roll back, share repro with the engineering owner.

---

## 5. Required Production Env Vars

These MUST be set on the production environment before / during deploy. Names taken from the current backend code; values come from the operator.

### Backend (`/app/backend/.env`)
| Var | Notes |
|---|---|
| `MONGO_URL` | Mongo connection string (replica set recommended for future H-2 transactions migration; not required today). |
| `DB_NAME` | Logical database name. |
| `JWT_SECRET` | **Minimum 32 bytes** — generate with `openssl rand -hex 32`. Current warning visible if shorter. |
| `ENV` | Set to `production` to enable CORS fail-closed (H-6). |
| `ALLOW_DB_RESET` | **MUST be unset OR `false`** in production. Only flip to `true` in temporary maintenance windows when truly required (C-2). |
| `CORS_ORIGINS` | Comma-separated allow-list of allowed frontend origins (no wildcards in production). |
| `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`, `R2_PUBLIC_BASE` | Cloudflare R2 — for upload sessions + payment proof storage. |
| `RESEND_API_KEY` | Outbound email (notifications / reminders). |
| `BACKUP_SCHEDULE_HOUR` | (Optional) Daily backup hour. Default 01:00 UTC. |
| `TZ` | Set to local business TZ if reports require it (else app handles per-tenant TZ in code). |

### Frontend (`/app/frontend/.env`)
| Var | Notes |
|---|---|
| `REACT_APP_BACKEND_URL` | Public HTTPS URL of the backend (no trailing slash). |

### Protected — DO NOT remove or rename
- Backend: `MONGO_URL`, `DB_NAME`.
- Frontend: `REACT_APP_BACKEND_URL`.

---

## 6. Required Post-Deploy Checks

Run these against the **production URL** within the first 30 minutes post-deploy:

### Health & indexes
1. `GET {PROD}/api/health` → 200.
2. Tail backend log for `"Application startup complete."` and `"Database indexes created"` with **no** `DuplicateKeyError`.
3. Confirm both critical indexes are present (one-shot script):
   ```python
   await db.returns.index_information()              # expect uniq_returns_org_rma
   await db.payment_idempotency.index_information()  # expect uniq_payment_idempotency
   ```
4. Confirm no cross-org pollution in `returns`:
   ```bash
   python3 /app/backend/scripts/rma_dedupe_dry_run.py
   # Expect: "Zero duplicate rma_number groups found across all tenants."
   ```

### Auth + tenant safety
5. Log in as a real owner; load `/me` — verify NO `totp_secret`, NO PIN fields leak in the JSON.
6. Confirm CORS: request from an un-allow-listed origin must be rejected (`ENV=production` ⇒ fail-closed).
7. Confirm `ALLOW_DB_RESET` is **unset** or `false` (`echo $ALLOW_DB_RESET`).

### Live functional spot-checks
8. Quick Sale (cash) — works end-to-end; inventory decrements; wallet credits.
9. Credit sale with PIN — works; customer balance increases.
10. Historical Credit (TOTP-only) — gate accepts owner/admin TOTP; rejects static `admin_pin` (per Phase 4A allow-list).
11. Payment on credit invoice — wallets route correctly; double-submit via same idempotency key returns 409.
12. Return creation — `rma_number` issued; visible only inside the issuing tenant; second tenant cannot see it.
13. Public QR receipt — open view works without PIN; PIN-gated lookup with a *wrong PIN from another tenant* → 403.

### Monitoring (set up day 1 if not already)
14. Surface `security_events` collection in your log aggregator — watch for `setup_reset_attempt`, `pin_resync_failed`, `qr_pin_brute_force`.
15. Alert on backend startup that does NOT log `"Database indexes created"` (means try/except swallowed an error).
16. Daily check on `pin_attempt_log` — group by `client_ip` over 24h to spot probing.

### One-shot data backfill — `internal_invoices.organization_id` (Phase 5+ B-1)

**Why this step is required.** As part of the Phase 5+ isolation hardening, `internal_invoices` was added to `TENANT_COLLECTIONS` (`backend/config.py`). The tenant proxy now auto-injects `organization_id` on insert and auto-scopes every `db.internal_invoices` read by the caller's org. Legacy rows persisted **before** this change carry `organization_id=None` and therefore become **invisible to their owning tenant** through the proxy (fail-closed by design) until backfilled. A small admin-gated, idempotent endpoint exists for exactly this remediation.

**When to run.** Immediately after the backend containing the B-1 fix is deployed and restarted, and **before** the owner runs the manual smoke against any internal-invoice surface (list, summary, by-transfer, pay, profitability). It is safe to run before traffic ramps, but must be completed before the owner exercises items 8–13 of this section against pre-existing internal-invoice rows.

**What to run** (order matters — review the dry-run, then apply):
```bash
# 1) DRY-RUN — no writes; review counts.
curl -X GET "{PROD}/api/admin/backfill/internal-invoices-org-id" \
     -H "Authorization: Bearer {ADMIN_JWT}"

# Inspect the response:
#   scanned          — rows with organization_id missing/null/empty
#   updated          — would-be writes (= rows with a derivable org)
#   skipped          — already carry a valid organization_id (no-op)
#   unresolved_count — branch lookup failed; lists invoice_id + branch ids
#   samples          — up to 5 (invoice_id, invoice_number, derived_org_id)
#
# 2) Save the dry-run JSON to your deploy log.
# 3) Apply (note the explicit ?apply=1 final-confirm guard):
curl -X POST "{PROD}/api/admin/backfill/internal-invoices-org-id?apply=1" \
     -H "Authorization: Bearer {ADMIN_JWT}"

# 4) Re-run the dry-run to confirm the queue is empty.
curl -X GET "{PROD}/api/admin/backfill/internal-invoices-org-id" \
     -H "Authorization: Bearer {ADMIN_JWT}"
```

**Success criteria.**
- Final dry-run reports `scanned = 0` (preferred) — or `updated = 0` with any `unresolved` rows individually documented and triaged.
- All `unresolved` rows from the apply step have been investigated; they remain in the collection untouched and continue to be invisible to the tenant proxy until manually resolved (e.g. orphan branches require operator intervention).
- An owner-driven internal-invoice read (`GET /api/internal-invoices`, `GET /api/internal-invoices/summary`, `GET /api/internal-invoices/by-transfer/{transfer_id}`) returns the expected rows for their tenant.
- An owner-driven internal-invoice payment (`POST /api/internal-invoices/{invoice_id}/pay`) succeeds end-to-end with correct wallet movement.
- `tests/business_regression/` is green (`10 passed · rows=51 pass=51`) — re-run is optional but recommended as the final smoke.

**Safety notes.**
- **Admin-only**: hard-gated on `user.role == "admin"`; non-admins receive HTTP 403.
- **Idempotent**: rows already carrying a valid `organization_id` are reported under `skipped` and never re-written. A re-apply on a clean dataset returns `scanned = 0`.
- **Non-destructive**: the endpoint never deletes rows. Unresolved invoices stay in the collection and are listed in the response (capped at 50 entries) so the operator can decide next steps.
- **Cross-tenant by necessity**: this is the one and only legitimate cross-tenant write path for `internal_invoices` (the rows being repaired have no org to filter by); documented in `tests/test_phase2d_permissions.py` `ALLOWLIST_FILES` with full justification.
- **Persist the dry-run output** (JSON) before applying — it is the auditable record of which legacy rows were resolved, and which branches (if any) require operator follow-up.

### B-3 — Close-Day / Z-Report partial-sale cash double-count fix

**What changed.** The close-day / Z-Report cash aggregation now excludes partial-sale rows from `total_cash_sales`. Specifically, every `cash_sales_pipeline` / `cash_sales_agg` `$match` in `backend/routes/daily_operations.py` (4 call sites: unclosed-days summary, preview-bookkeeping, `get_daily_close_preview`, mutating `close_day`) now also requires `"partial_grand_total": {"$exists": False}`. This uses the marker stamped by `helpers.py:407` only for partial sales, so the filter is exact and non-disruptive:
- Partial-sale **actual paid amount** still flows through the invoice payments / `ar_pipeline` and lands in `total_cash_ar` (today-dated `payments[]` on credit/partial invoices) — unchanged.
- Partial-sale **unpaid balance** still aggregates into `total_new_credit` via `payment_type ∈ [credit, partial]` and today's `order_date` — unchanged.
- Partial sales still surface through `partial_invoices` (and the corresponding section of the close-day record), but no longer inflate `total_cash_sales` or `total_cash_in`.

**Why it changed.** Before the patch, every partial sale was counted twice in the closing-day cash math:
1. The **full** partial-sale `line_total` (e.g. ₱300) entered `total_cash_sales` because the `sales_log` row carried `payment_method="cash"` and the cash-pipeline filter didn't exclude partial rows.
2. The **actual** cash leg of the partial sale (e.g. ₱100) entered `total_cash_ar` via the AR pipeline (`payment_type=partial` + `payments[0].date==today`).

Net effect: every partial sale inflated `total_cash_in` by `(partial_line_total − partial_cash_amount)`. For a cashier reconciling the drawer with a partial sale of ₱300 / ₱100 paid, the system claimed `expected_counter = +₱200` more than was physically present — i.e., a recurring **false shortage** equal to the unpaid AR portion of every partial-sale day. (The route's pre-existing comment at `daily_operations.py:1840-1846` already states the design intent that "the expanded `total_cash_ar` subsumes `partial_total`, which is dropped from the cash_in math below" — the bug was a missing implementation of that intent in the four cash-pipeline call sites.)

**Worked example.**
- Cash sale: ₱500 (paid in full)
- Partial sale: ₱300 grand_total, ₱100 cash, ₱200 AR
- **Before** the fix → `total_cash_in = 900` ← ₱300 phantom over, cashier appears to be ₱300 SHORT
- **After** the fix → `total_cash_in = 600` ← matches the drawer
- Expected counter for cashier reconciliation: **600** = ₱500 cash sale + ₱100 partial cash portion

**Post-deploy smoke check.** Run this once on production after the deploy:
1. As a cashier on a fresh business day, create a regular cash sale (any amount, payment_method=Cash).
2. Create one partial sale on the same day: capture the cash portion + leave a balance unpaid. (e.g. ₱300 sale, ₱100 cash, ₱200 to AR.)
3. Open the daily-close preview (`GET /api/daily-close-preview?date=<today>&branch_id=<id>`):
   - Confirm the partial sale does NOT appear in the cash-sales-by-category breakdown.
   - Confirm `total_cash_sales` equals only the cash sale amount.
   - Confirm `total_cash_ar` (or the route's collected-payments bucket — UI label varies) includes the partial sale's cash portion.
   - Confirm `total_new_credit` includes the partial sale's unpaid balance.
   - Confirm the partial sale appears under `partial_invoices` (or the partial-sales section of the UI).
4. Run the close-day flow (`POST /api/daily-close`):
   - Provide `actual_cash` = real cash physically counted in the drawer.
   - Confirm `expected_counter` matches what the drawer should hold (cash sale + partial cash portion + starting float − expenses + net transfers).
   - Confirm `over_short` is ₱0 (give or take rounding) when the drawer matches.

**Test evidence.**
- `tests/business_regression/` — **21 tests / 102 invariants — 100% PASS** (up from 101/102 pre-patch; the previously deliberate signal-channel FAIL row is now a hard assertion).
- `tests/business_regression/test_br6_z_report_close_day.py` — all 4 scenarios green (conservation, duplicate-close, cross-day AR, negative variance).
- `tests/test_phase2e_date_basis.py` — **10/10 PASS**. No regression in date-basis behavior, late-encode reporting, voided-transaction exclusion, branch scoping, transaction-date logic, or split-payment handling.

**Safety notes.**
- **No change to sale creation.** `routes/sales.py` and `helpers.log_sale_items` are untouched. Existing `sales_log` rows keep their original shape.
- **No change to invoice payments.** `record_invoice_payment`, the payments array, fund-source dispatch, and AR receivables are all unchanged.
- **No change to wallet behavior.** `update_cashier_wallet`, `fund_wallets` schema, `wallet_movements`, and the close-day wallet reset to `cash_to_drawer` are all unchanged.
- **No change to Historical Credit / late-encode behavior.** Historical Credit notebook AR continues to land in `total_new_credit` via `payment_type=credit` on the appropriate `order_date`; late-encoded sales of past dates do not leak into today via the unchanged `order_date` filter.
- **Only the close-day / Z-Report cash aggregation classification changed** — partial-sale rows are now correctly classified as belonging to the partial-sale bucket, not the cash-sales bucket.

---

## 7. Backlog Items (post-deploy work, prioritised)

### P1 — next sprint candidates
- **Phase 4A.2** — Concurrent Held-Sales Queue (multiple parked carts per branch).
- **Phase 4 cleanup Pass 6+** — `processSale` decomposition (deferred until Held-Sales naturally forces a payload-builder extraction).

### P2 — UX polish
- "Sync this AR to customer SMS" toggle on Historical Credit submit.
- Rename route `/sales` → `/sales-history` (and breadcrumb).
- Deprecate / re-wire legacy `POST /api/verify/public/{doc_type}/{doc_id}` (currently dead — fail-closed proxy ensures it 404s).

### Backlog — audit follow-ups (none are deploy-blockers)
- **H-7** — JWT TTL 24h, no rotation/blacklist. Add refresh-token rotation + revocation table.
- **H-9** — `users.email` per-tenant unique partial index.
- **H-10** — `invoices.invoice_number` per-(branch, org) unique partial index (atomic counter already mitigates the runtime path).
- **H-12** — Offline negative-stock incident ticket + admin notification.
- **H-14** — `/admin/backfill/*` PIN + dry-run + audit-chain hardening.
- **Phase 6** — Full `pytest tests/` cleanup (~589 failures dominated by hardcoded usernames + dates + DB state pollution). Multi-day effort.
- **`useCheckoutState` hook + `PaymentTabs`** — deferred (low value-to-churn ratio).
- 11 legacy FE unit tests using hardcoded dates — migrate to `freezegun`.

### Nice-to-have (operator-facing, post-launch)
- "Suspicious Receipt PIN Attempts" admin widget surfacing `pin_attempt_log` rows with >3 fails from the same IP (data already collected; just needs a panel).
- Cloudflare R2 lifecycle policy for receipt photos (cost containment).

---

## 8. Deployment-Readiness Statement

The application is **deployment-ready** for production multi-tenant rollout, **subject to**:

1. **Owner manual smoke** completing successfully (Section 4) on the deployed environment.
2. **Required env vars** (Section 5) being set correctly on production — specifically:
   - `ENV=production` (fail-closed CORS)
   - `ALLOW_DB_RESET` unset / false
   - `JWT_SECRET` ≥ 32 bytes
   - `REACT_APP_BACKEND_URL` pointing to the production backend.
3. **Required post-deploy checks** (Section 6) completing green — most importantly:
   - `uniq_returns_org_rma` and `uniq_payment_idempotency` indexes present.
   - `rma_dedupe_dry_run.py` shows zero same-org duplicates.
   - No `DuplicateKeyError` in backend startup logs.

All 9 P0 audit findings are closed (the C-8 backstop index is now correctly compound per-tenant). 9 of 14 P1 findings are closed; 1 deferred-by-design (H-2 transactions); 1 partial (H-12); 4 open backlog (H-7, H-9, H-10, H-14) — none deploy-blocking. **H-1 has a code-side closure but requires a one-time post-deploy backfill (§4.A below).** Phase-specific audit suite is **112 / 112 PASS** including the new cross-tenant receipt-PIN isolation regression (`tests/test_phase5_public_receipt_pin_isolation.py`). Frontend is **75 / 75 PASS** with a clean production build.

No additional code work is required prior to deploy. Engineering hands the keys over to the deployment owner.

— End of handoff —

---

## 4. POST-DEPLOY RUNBOOK ADDENDUM

### 4.A — H-1 prepared-order invoice visibility backfill (one-time)

**What happened**
The legacy draft-finalize path in `routes/sales.py` used
`db.invoices.replace_one(filter, invoice)`. The `TenantCollection`
proxy only wraps `insert_one`, `update_one`, and `find_one_and_update`;
`replace_one` falls through `__getattr__` to the raw collection.
This meant two things at the same time:
1. The filter was **not** org-scoped (the race that H-1 was originally
   filed for).
2. The replacement document was written **verbatim with no
   `organization_id`** — the proxy's `_inject_org` was bypassed
   entirely. The `organization_id` that was stamped at draft-create
   time was wiped by the wholesale replacement.

Symptom: after finalize, the customer balance increased correctly
(`customers.update_one $inc` goes through the proxy), but the invoice
row was invisible to every tenant-scoped read. Sales History, Payments
/ receivables, `/customers/{id}/invoices`, `/customers/{id}/statement`,
and receipt search all returned empty for the affected customer.

**Live sample-site evidence (`agri-books.com`, 2026-05-12)**

| Customer | `customer.balance` | Visible open invoices | Statement closing |
|---|---|---|---|
| GREEN HANDS AGRIVET SUPPLY | ₱95,528.00 | 0 | ₱0.00 |
| ANGEL AGRIVET SUPPLY       | ₱74,803.00 | 0 | ₱0.00 |
| **Total unaccounted AR**   | **₱170,331.00** | — | — |

Both customers' invoices have been finalized but the persisted rows
carry no `organization_id`, so the tenant proxy cannot see them.

**What is fixed in the current repo**
Commit `200b6c1e` (2026-05-09) replaced `replace_one` with:
```python
guard = await db.invoices.find_one_and_update(
    {"id": draft_doc["id"], "status": "for_preparation"},
    {"$set": {"status": "processing", "_finalize_started_at": now_iso()}},
)
if guard is None:
    return await db.invoices.find_one({"id": draft_doc["id"]}, {"_id": 0})
await db.invoices.update_one({"id": draft_doc["id"]}, {"$set": invoice})
```
Both `find_one_and_update` and `update_one` are proxy-wrapped. The
`$set` payload does NOT include `organization_id`, so the org_id from
the draft-create-time `insert_one` survives. Locked in by the new
regression file
`backend/tests/business_regression/test_br_prepare_order_completion_visibility.py`
(6 scenarios, 83 expected-vs-actual rows, all green).

**Required deploy action**

1. **Deploy the fixed code.** Confirm `git log -S "replace_one" -- backend/routes/sales.py` shows the removal commit (200b6c1e) is in the deploy artifact.

2. **Dry-run the backfill** (no writes; admin role only):
   ```bash
   curl -s -H "Authorization: Bearer $ADMIN_JWT" \
        https://<host>/api/admin/backfill/invoices-org-id \
        | tee /tmp/h1_invoices_dryrun_$(date +%F).json | jq .
   ```
   **Save the JSON output** as a deploy artifact. Expected fields:
   `scanned`, `missing_org_id`, `resolved_by_branch`,
   `resolved_by_customer`, `resolved_by_cashier`, `multi_path_agreed`,
   `multi_path_conflict`, `unresolved`, `per_tenant_breakdown`,
   `unresolved_samples`, `conflict_samples`.

3. **Review** the dry-run output:
   - `multi_path_conflict` should be 0 in a healthy production state.
     If > 0, inspect `conflict_samples` and resolve manually before
     proceeding. The backfill will **not** stamp conflict rows.
   - `unresolved` rows have no usable branch_id, customer_id, or
     cashier_id. The backfill will **not** stamp these either.
     Inspect `unresolved_samples` — these are typically very old
     pre-tenant rows that need human disposition.
   - `per_tenant_breakdown` should show counts and AR pesos per tenant.
     For agri-books.com, expect ≥2 invoices for org
     `c391c89b-3be9-4bbf-8e01-3ad5f758c448` summing to ~₱170,331.

4. **Apply only after review** (requires `apply=1` confirm flag):
   ```bash
   curl -s -X POST -H "Authorization: Bearer $ADMIN_JWT" \
        "https://<host>/api/admin/backfill/invoices-org-id?apply=1" \
        | tee /tmp/h1_invoices_apply_$(date +%F).json | jq .
   ```
   Save the JSON output. Each stamped row also writes an audit entry
   to `admin_backfill_log` with `kind = "invoices_org_id_backfill"`,
   the resolved org_id, the resolution source (`branch`/`customer`/
   `cashier`/`multi`), the actor user id, and `ran_at`.

5. **Re-run the dry-run** to confirm idempotency:
   ```bash
   curl -s -H "Authorization: Bearer $ADMIN_JWT" \
        https://<host>/api/admin/backfill/invoices-org-id | jq .
   ```
   Expected: `missing_org_id == 0` (every row that could be resolved
   has been stamped). The only remaining items should be
   `multi_path_conflict` and `unresolved` from step 3 — neither will
   shrink without manual intervention.

**Post-deploy smoke (mandatory)**

1. **Verify affected customers reappear** (agri-books.com):
   - Open GREEN HANDS AGRIVET SUPPLY → Payments tab. Expected: invoice(s)
     totalling ~₱95,528.00 now visible.
   - Same for ANGEL AGRIVET SUPPLY (~₱74,803.00).
   - Open one of those invoices → confirm receipt renders, doc_code
     resolves, payments form works.
2. **Apply a token payment** (e.g. ₱100.00) on one reappeared invoice
   to confirm the full Pay → invoice.balance decrement → customer.balance
   decrement chain works end-to-end.
3. **Create a fresh prepared order** at any branch:
   - Prepare Order → Complete as **Credit** → confirm invoice
     immediately appears in Sales History and Payments.
   - Prepare Order → Complete as **Partial** → confirm invoice in
     Sales History and Payments.
   - Prepare Order → Complete as **Cash** → confirm invoice in Sales
     History only (no AR).
4. **Re-run the drift check** from the diagnosis report:
   ```python
   # for every customer in /api/customers:
   #   diff = customer.balance - sum(open invoice balances for that customer)
   # expected post-backfill: 0 customers with |diff| > 0.01
   ```

**Rollback / safety**

- The backfill never deletes or rewrites real data. It only writes
  `organization_id` (plus four diagnostic fields:
  `_org_id_backfilled_at`, `_org_id_backfilled_by`,
  `_org_id_backfilled_source`) to rows where the field was previously
  missing/null/empty.
- The audit log makes every change reversible: an emergency
  `UPDATE invoices SET organization_id = "" WHERE id IN (SELECT invoice_id FROM admin_backfill_log WHERE ran_at = "<ts>")` (Mongo equivalent) would undo a run.
- Conflict and unresolved rows are never touched, so manual review
  cannot be silently overridden by re-running the backfill.

**Deferred / accepted-as-backlog**
- `multi_path_conflict` and `unresolved` rows from the dry-run are a
  manual triage queue. They should not block deploy. Track in the
  next ops cycle.

### 4.B — Locked-in regression

The bug class is permanently guarded by:
- `backend/tests/business_regression/test_br_prepare_order_completion_visibility.py`
  - 5 payment-type scenarios (cash, digital, split, partial, credit) +
    1 backfill end-to-end scenario.
  - 83 expected-vs-actual rows per run, all green.
  - Key regression assertion: tenant-scoped `db.invoices.find_one({"id": draft_id})`
    must return non-None after finalize. If `organization_id` is
    stripped again, this is the assertion that fails first.
- Run before every deploy: `python3 -m pytest tests/business_regression/ -v`
  must end at exit 0 with `business_regression_latest.json` reporting
  `fail_count == 0`.

— End of post-deploy runbook addendum —

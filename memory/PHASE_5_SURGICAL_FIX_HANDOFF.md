# Phase 5 — Surgical Fix Handoff

**Created**: Feb 2026 (after corrected deployment-validation addendum)
**Status**: Validation complete. ONE real must-fix item identified. Awaiting owner go-ahead before two surgical fixes.
**Read this file COMPLETELY before doing anything.**

---

## 1. Project / Repo Identity

- **App**: AgriBooks — multi-tenant Point of Sale + accounting for Philippine agri-supply businesses.
- **Stack**: React 19 (CRA + craco) frontend, FastAPI + Motor + MongoDB backend, supervisor-managed.
- **Repo paths**:
  - Backend: `/app/backend/` (routes in `/app/backend/routes/`, tests in `/app/backend/tests/`, scripts in `/app/backend/scripts/`)
  - Frontend: `/app/frontend/` (pages in `src/pages/`, shared components in `src/components/`, hooks/libs in `src/lib/`)
  - Memory / handoffs / audit: `/app/memory/`
- **Branch**: working branch in the Emergent platform fork ecosystem; not strictly named — the platform commits per step.
- **Active POS page**: `/app/frontend/src/pages/UnifiedSalesPage.js` (currently **5,420 lines** post Phase 4 cleanup).
- **Quick Sale and Detailed Sale / Order Mode are TWO MODES INSIDE THE SAME `UnifiedSalesPage`**, gated by the `mode` state (`quick` vs `detailed`). They are NOT separate pages, NOT separate POS systems, NOT separate routes. Treat them as one component.
- **Terminal POS** (`/app/frontend/src/pages/terminal/TerminalSales.jsx` etc.) is **separate and out of scope** for every audit and cleanup workstream so far. Do not touch it. It has its own payment state, its own offline auth (`OfflineCreditBypassDialog`), and its own queueing.

---

## 2. Completed Audit / Cleanup Phases

The following are all DONE per `/app/memory/PRD.md`, `/app/memory/CHANGELOG.md`, `/app/memory/PHASE_1_HANDOFF.md`, and the individual phase handoff docs:

### Audit phases (security + integrity)
- **Phase 1A — Security hardening** — C-1 PIN-sync sanitisation, C-2 setup/reset auth gate, C-3 TOTP take-over guard, plus bonus H-6 (CORS fail-closed in prod), H-8 (JWT org_id cross-check), H-13 (setup/initialize refusal). Tests: `test_phase1a_security.py` (6/6).
- **Phase 1B — Inventory / sales atomicity** — C-4 deferred `pending_inventory_ops` lists applied AFTER invoice commit; C-5 envelope_id race; C-6 `_finalize_draft_offline` guarded update. Tests: `test_phase1b_inventory.py` (4/4).
- **Phase 1C — Money / payment guards** — C-7 wrong field fix, C-9 `assert_invoice_payable` on 3 payment paths. Tests: `test_phase1c_money.py` (17/17).
- **Phase 2A — Customer balance reconciliation diagnostic** — read-only `GET /api/admin/customer-balance-reconciliation` (file: `routes/balance_reconciliation.py`, 337 lines). Closes H-3 as diagnostic. Tests: `test_phase2a_balance_recon.py` (18/18) + `test_phase2a_live_api.py` (2/2).
- **Phase 2B — Flow-matrix coverage** — money + stock atomicity matrix (`tests/phase2b/`).
- **Phase 2C — Payment idempotency + returns invoice_number requirement** — closes H-4 (returns require `invoice_number` or cash refund or `allow_pending_credit=true` flag, no silent fallback). Compound unique `payment_idempotency(organization_id, route, key)` partial index. Tests: `test_phase2c_live_smoke.py` (4/4) + `test_phase2c_pos_hardening.py` (5/5).
- **Phase 2C.5** — POS hardening continuation.
- **Phase 2D — `assert_branch_access` hardening** — closes H-5 (legacy unscoped bypass for non-privileged users). Tests: `test_phase2d_permissions.py` (16/17 — see Section 6, brittle test below) + `test_phase2d_live_smoke.py`.
- **Phase 2D.5** — admin grace + permission edge-case follow-ups.
- **Phase 2E — Report date-basis matrix** — `order_date` vs `created_at` correctness for Z-Report, encoded-today, customer ledger. Tests: `test_phase2e_date_basis.py` (10/10).
- **Phase 3 — Historical Credit / Notebook AR backend** — closes H-11. `POST /api/historical-credit/preview` + commit. TOTP-only gate. Count-sheet stopper. Soft floor `<7 days` rejection. Tests: `test_phase3_historical_credit.py` (16/16) + `test_phase4a_approval_gate.py` (12/12).

### Frontend integration + stabilisation
- **Phase 4A — Frontend Historical Credit / Notebook AR mode** — amber banner inside `UnifiedSalesPage`, TOTP-gated commit dialog.
- **Phase 4A.1 — Online/Offline routing stabilization** — `lib/connectivity.js` (`isTrueNetworkError`, `pingBackendHealth`), 10-second reconnect grace, envelope reuse, real-server-errors surface as toasts instead of forcing offline save.
- **Phase 4A.1.1 — Pending-sync pill** — read-only UI pill exposing `pendingCount`.

### Phase 4 cleanup (presentational decomposition)
- **Pass 0** — UI pre-fixes (dedupe-pill pointer events, All-Branches UX guard).
- **Pass 1** — `useConnectivity` hook extracted (`lib/useConnectivity.js`).
- **Pass 2** — `useHistoricalCredit` hook extracted (`lib/useHistoricalCredit.js`, 282 lines, 14 hook tests).
- **Pass 3** — `HistoricalCreditBanner.jsx` + `HistoricalCreditDialog.jsx` extracted (14 RTL tests).
- **Pass 4** — `CheckoutDialog.jsx` extracted with 26-prop surface + page-owned `confirmDisabled` (13 RTL tests).
- **Pass 5** — `CreditApprovalDialog.jsx` extracted with 11-prop surface + page-owned `pinSessionWarm` / `showOfflineReason` precomputes (11 RTL tests).

**Cumulative result**: `UnifiedSalesPage.js` reduced 6,030 → 5,420 lines (−610). 75 new frontend RTL tests. Zero business-behavior changes.

### Workstreams explicitly DEFERRED (per Phase 5 verdict)
- `processSale` decomposition — highest-risk, zero existing FE test coverage, deferred until HeldSalesQueue forces a natural payload-builder extraction.
- `useCheckoutState` hook — modest value-to-risk ratio (~50 page-side rewrites for ergonomics-only gain), deferred indefinitely.
- `PaymentTabs` — deferred pending `useCheckoutState`.

---

## 3. Current Test / Validation Baseline

### Frontend
- **`yarn test --watchAll=false`**: **75 / 75 PASS** in ~9.4s across 7 test files:
  - `lib/connectivity.test.js`, `lib/useConnectivity.test.js`, `lib/useHistoricalCredit.test.js`
  - `components/HistoricalCreditBanner.test.jsx`, `components/HistoricalCreditDialog.test.jsx`
  - `components/CheckoutDialog.test.jsx`
  - `components/CreditApprovalDialog.test.jsx`
- **`yarn build`**: ✅ PASS, ~35s. Clean production bundle.

### Backend — phase-specific audit suite (the meaningful deploy gate)
Each file run in isolation (`pytest tests/<file>.py`):

| File | Result |
|---|---|
| `test_phase1a_security.py` | ✅ 6 / 6 |
| `test_phase1b_inventory.py` | ✅ 4 / 4 |
| `test_phase1c_money.py` | ✅ 17 / 17 |
| `test_phase2a_balance_recon.py` | ✅ 18 / 18 |
| `test_phase2a_live_api.py` | ✅ 2 / 2 |
| `test_phase2c_live_smoke.py` | ✅ 4 / 4 |
| `test_phase2c_pos_hardening.py` | ✅ 5 / 5 |
| `test_phase2d_permissions.py` | ⚠️ **16 / 17** (1 brittle test — see §6) |
| `test_phase2d_live_smoke.py` | ⚠️ 2 / 12 + 10 fixture errors (DB pollution; see §3 note) |
| `test_phase2e_date_basis.py` | ✅ 10 / 10 |
| `test_phase3_historical_credit.py` | ✅ 16 / 16 |
| `test_phase4a_approval_gate.py` | ✅ 12 / 12 |
| **Total when run in isolation (excluding live_smoke pollution)** | **134 / 134** core audit-closure tests ✅ |

### Backend — full `pytest tests/` suite (NOT a meaningful deploy gate)
- Two runs were executed for the addendum:
  - Run 1 (shared dev DB, polluted): `1822 pass / 589 fail / 362 error / 247 skip / 759s`.
  - Run 2 (isolated fresh `DB_NAME=phase5_validation_<ts>`): `1470 pass / 608 fail / 679 error / 263 skip / 657s`.
- **Conclusion**: the failures are NOT meaningfully different across the polluted vs clean run. The dominant failure mode is **test-authoring brittleness**:
  - pytest-asyncio session-scoped event loop + session-shared DB state cause tests in the same invocation to collide on hardcoded usernames / org names.
  - Many one-off iter-numbered files (e.g. `test_zreport_net_sales_advances_243_4.py`, `test_test_stage_button_195.py`, `test_returns_iter252.py`) depend on specific historical seed states that have drifted.
- The full suite is best treated as a **historical bug catalog** for now, not a regression gate. Hardening it is a separate multi-day backlog item (Phase 6 candidate).

### API health
- `GET /api/health` → **200** ✅ (preview URL stored in `frontend/.env` as `REACT_APP_BACKEND_URL`; trust that value, ignore older preview URLs).

---

## 4. Corrected Audit-Closure Status (from the reconciliation addendum)

### P0 (C-1 → C-9)

| # | Finding | Status | Evidence |
|---|---|---|---|
| C-1 | `/sync/pos-data` leaked PINs | ✅ CLOSED | identity-only directory; `pin_resync_failed` audit row on mismatch (`test_phase1a_security.py`) |
| C-2 | `/setup/reset` unauthenticated DB wipe | ✅ CLOSED | super-admin + `ALLOW_DB_RESET=true` env + password re-confirm + `security_events` audit |
| C-3 | TOTP take-over | ✅ CLOSED | refuses if `totp_enabled` or `last_admin_login` set; bootstrap-only window |
| C-4 | Inventory deducted before invoice insert | ✅ CLOSED | deferred `pending_inventory_ops` / `sync_inventory_ops` lists applied AFTER invoice commit (`test_phase1b_inventory.py`) |
| C-5 | Sync race double-deduct | ✅ CLOSED | insert-first-then-mutate; `envelope_id` unique partial index |
| C-6 | `_finalize_draft_offline` race | ✅ CLOSED | guarded `update_one({status: for_preparation})` + `modified_count==0` early return |
| C-7 | Wrong field `current_balance` | ✅ CLOSED | changed to `$inc balance` (`test_phase1c_money.py`) |
| **C-8** | **RMA generator collision + cross-tenant** | **⚠️ GENERATOR CLOSED / BACKSTOP-INDEX MISDESIGNED** | **See §5 below** — this is the one real must-fix item before final closure claim |
| C-9 | Payment against voided invoice | ✅ CLOSED | `utils/helpers.assert_invoice_payable` rejects 6 non-payable statuses on 3 payment paths (`test_phase1c_money.py`) |

### P1 (H-1 → H-14)

| # | Finding | Status |
|---|---|---|
| H-1 | Draft `replace_one` race | ✅ CLOSED — Phase 1 replaced with guarded `find_one_and_update` |
| H-2 | No MongoDB transactions | ⚙️ DEFERRED BY DESIGN — requires replica set + WT migration; interim compensation pattern (`error_partial_write`) accepted |
| H-3 | Customer balance drift / no reconciliation | ✅ DIAGNOSTIC CLOSED — `routes/balance_reconciliation.py` 337 lines, Phase 2A |
| H-4 | Returns credit any-open-invoice | ✅ CLOSED — Phase 2C.3 requires `invoice_number` OR cash refund OR `allow_pending_credit=true` |
| H-5 | `assert_branch_access` legacy bypass | ✅ CLOSED — Phase 2D `utils/auth.py:194–235` |
| H-6 | CORS `*` default | ✅ CLOSED — fail-closed when `ENV=production` |
| H-7 | JWT TTL 24h no rotation | ❌ OPEN BACKLOG — pre-existing, not deploy-blocker |
| H-8 | `get_current_user` org_id check | ✅ CLOSED — Phase 1A bonus |
| H-9 | `users.email` non-unique index | ❌ OPEN BACKLOG |
| H-10 | `invoices.invoice_number` no unique index | ❌ OPEN BACKLOG (atomic per-branch counter mitigates runtime path) |
| H-11 | 7-day late-encode insufficient | ✅ CLOSED — Phase 3 Historical Credit shipped |
| H-12 | Offline negative stock silent | ⚠️ PARTIAL — `stock_warnings` logging exists; incident-ticket + admin notification not shipped |
| H-13 | `setup/initialize` race | ✅ CLOSED — Phase 1A bonus |
| H-14 | `/admin/backfill/*` weak gate | ❌ OPEN BACKLOG — role-only gate; PIN + dry-run + audit chain pending |

**Rollup**: 8 P0 closed + 1 P0 needs the surgical fix; 9 P1 closed + 1 P1 deferred by design + 1 P1 partial + 4 P1 open backlog.

---

## 5. CRITICAL CORRECTED FINDING — C-8 RMA Backstop Index

### What was claimed previously
"C-8 closed (atomic generator) with manual data cleanup pending for the unique index."

### What is actually true
- **Generator** (`utils/numbering.generate_next_rma_number(branch_id, org_id)`): correctly scoped per `(org, branch)` with atomic counter → prevents in-tenant collisions. ✅
- **Backstop index** (`main.py:460–466`): created as **GLOBAL UNIQUE on `rma_number` alone**, not compound. ❌ Wrong uniqueness contract.
- **Result on the preview DB** (`test_database`):
  - The index DID NOT create on backend startup (try/except silently swallowed).
  - Forcing the create raised `DuplicateKeyError: dup key: { rma_number: "P2A-RTN-1" }`.
- **Global rma_number duplicate scan** (cross-org grouping matching the GLOBAL index's actual constraint):
  ```
  5 duplicate groups
  184 total records
  5 / 5 groups span MULTIPLE organizations (cross-org collision)
  0 / 5 groups are same-org duplicates

  Top collisions:
    'RTN-20260509-0001'  count=142  spans 142 orgs
    'P2A-RTN-1'          count=33   spans 33 orgs
    'RTN-20260226-0001'  count=5    spans 5 orgs
    'RTN-20260511-0001'  count=2    spans 2 orgs
    'RTN-20260501-0001'  count=2    spans 2 orgs
  ```
- **Per-tenant duplicate scan** (`(organization_id, rma_number)` grouping, matching the GENERATOR's actual uniqueness intent): **ZERO same-org duplicates.**
- **Companion index** (`payment_idempotency` compound `(organization_id, route, key)`): ✅ created cleanly after the same restart (`uniq_payment_idempotency`).

### Interpretation
The 184 cross-org collisions are an artifact of `test_database` containing 1,409 test organizations (every fork run accumulates more). In production, where each tenant typically has 1 organization, the global index DOES happen to function correctly — but only by accident, not by design. The audit intent "the index catches any legacy duplicates so they cannot replicate" is **NOT being enforced** because:
- in production: the global constraint is over-constrained but not violated by chance;
- in any multi-org tenant (e.g., a future multi-branch enterprise on one cluster): a Day-1 RMA from each org will collide.

### Correct uniqueness contract
**Compound unique partial index on `(organization_id, rma_number)`** — matches the generator's actual uniqueness scope.

### Why C-8 is not yet fully closed
The generator part is closed. The backstop is not. The audit finding was about defense-in-depth — the backstop index is the second line of defense. As designed today it is not providing that defense in any meaningful way.

---

## 6. Tiny Test-Only Cleanup — Phase 2D brittle test

### Failing test
`tests/test_phase2d_permissions.py::test_jwt_org_mismatch_rejects_token` (line 187).

### Failure mode
`pymongo.errors.DuplicateKeyError: dup key: { username: "jwt-test" }` at the INSERT step (line 192), BEFORE the assertion runs. The test inserts a user with `username="jwt-test"` (hardcoded literal) and cleans up by `id` (randomized via `_uid()`). Once any `jwt-test` row exists in the DB (from a prior run), the next insert fails on the unique `username` index.

### Companion test
`test_jwt_aligned_org_passes` (line 207) has the same pattern with `username="jwt-ok"`. It currently passes only because no prior `jwt-ok` pollution exists; the same issue is latent.

### Smallest safe fix
```diff
- "id": uid, "username": "jwt-test", "role": "cashier",
+ "id": uid, "username": f"jwt-test-{uid}", "role": "cashier",
```
And the symmetric change at line 216:
```diff
- "id": uid, "username": "jwt-ok", "role": "cashier",
+ "id": uid, "username": f"jwt-ok-{uid}", "role": "cashier",
```

Note: the rest of the file already uses `_uid()`-randomized usernames; only these two lines slipped through.

### Classification
**Brittle test, NOT a product regression.** The product code path (`get_current_user` org_id mismatch → 401) is unchanged and is independently verified by the happy-path test that passes. The test's assertion logic is sound — only its fixture setup is brittle.

### Recommendation
Fix now for CI hygiene and to restore Phase 2D permissions to a clean 17/17. Not a deployment blocker on its own.

### Pre-existing pollution in the preview DB
Two stray rows exist that should be cleaned before re-verifying:
- `db.users.findOne({username: "jwt-test"})` → 1 row (`id: 'u-jwt-1735b0c6'`).
- `db.users.findOne({username: "regression_manager"})` → 1 row (`id: '67f8f85f-...'`).
After randomizing the usernames in the test, the next fork can either (a) leave these polluted rows in place (the new randomized usernames won't collide with them) or (b) delete them as part of the same surgical pass.

---

## 7. Next Intended Workstream After Fork

A very narrow surgical fix pass. Two and only two code changes:

### A. Change `main.py:460–466` to a compound unique partial index
```python
await _raw_db.returns.create_index(
    [("organization_id", 1), ("rma_number", 1)],
    unique=True,
    partialFilterExpression={"rma_number": {"$exists": True, "$type": "string"}},
    name="uniq_returns_org_rma",
)
```
(Existing try/except wrap is fine; the index will succeed because zero same-org duplicates exist.)

### B. Randomize `jwt-test` / `jwt-ok` usernames in `tests/test_phase2d_permissions.py`
Two `f"jwt-test-{uid}"` / `f"jwt-ok-{uid}"` interpolations as in §6 above.

### Verification steps
- C. `sudo supervisorctl restart backend` and confirm no errors in `/var/log/supervisor/backend.err.log`.
- D. Verify `uniq_returns_org_rma` is present via `/tmp/check_indexes.py` or equivalent.
- E. Re-run the per-org duplicate scan and confirm zero same-org duplicates (should already be the case; this is a regression check).
- F. Re-run `pytest tests/test_phase2d_permissions.py` and confirm 17/17 PASS.
- G. Re-issue the deployment-readiness verdict (expected: **E.1 READY**, with all 9 P0 closed including the C-8 backstop, full 135/135 audit-suite pass, frontend unchanged).

### Existing scripts available
- `/app/backend/scripts/rma_dedupe_dry_run.py` — per-tenant (org, rma) grouping; expect zero groups (read-only).
- `/tmp/global_rma_dup_check.py` — global rma_number grouping (read-only); for reference only.
- `/tmp/check_indexes.py` — prints returns + payment_idempotency indexes (read-only).
- `/tmp/inspect_raw.py` — raw cross-org row counts including `test_org_admin@regression.local` confirmation (read-only).
- `/tmp/try_create_rma_index.py` — forces the WRONG global index for diagnostic; do NOT run as part of the fix; superseded by the compound index in step A.

---

## 8. Strict Constraints for the Next Fork

Do NOT:
- Add features.
- Resume Phase 4 cleanup (no `processSale` decomposition, no `useCheckoutState`, no PaymentTabs).
- Start HeldSalesQueue or any new feature.
- Change RMA number generation logic in `utils/numbering` unless strictly required to make the new compound index enforce (it should not be required).
- Alter business logic beyond correcting the RMA backstop index.
- Broaden the test cleanup into the wider brittle legacy suite (the full `pytest tests/` has hundreds of brittle tests; that is a Phase 6 candidate, not part of this surgical pass).
- Touch POS Terminal (`src/pages/terminal/`).
- Touch offline sync logic (`backend/routes/sync.py`, `frontend/src/lib/syncManager.js`, `lib/offlineDB.js`, `lib/connectivity.js`).
- Touch reports (Z-Report, encoded-today, customer ledger) other than read-only verification.
- Mutate production data of any kind.

Preserve:
- Quick Sale and Detailed Sale modes inside `UnifiedSalesPage`.
- Normal today sales, 0–7 day late encode, Historical Credit / Notebook AR Mode, payments, voids, returns.
- CheckoutDialog, CreditApprovalDialog, HistoricalCreditBanner, HistoricalCreditDialog behaviour.
- Connectivity behaviour and pending-sync pill.
- All current data-testid values.
- All current tests that currently pass (75/75 frontend, 134/134 phase-specific audit suite, plus the targeted 17/17 once §6 is fixed).

---

## 9. Required Next-Fork Behavior

The next fork agent should:
1. **Read this entire handoff first.**
2. Run the smoke baseline below to reconfirm the current state.
3. Review the corrected Phase 5 deployment-validation addendum (the most recent agent message before this handoff was written).
4. Confirm understanding of the two approved surgical changes (compound RMA index + jwt-test username randomisation).
5. **Wait for the owner's final implementation prompt before writing any code.**
6. After the fixes land, run the verification steps (§7 D–G) and produce a fresh deployment-readiness verdict.

---

## 10. Smoke Commands for the Next Fork

### Frontend
```bash
cd /app/frontend && CI=true yarn test --watchAll=false
# Expect: 75/75 PASS in ~10s across 7 test files.

cd /app/frontend && yarn build
# Expect: clean production build in ~35s.
```

### Backend — phase-specific audit suites (the deploy gate)
```bash
cd /app/backend && python3 -m pytest \
  tests/test_phase1a_security.py tests/test_phase1b_inventory.py tests/test_phase1c_money.py \
  tests/test_phase2a_balance_recon.py tests/test_phase2a_live_api.py \
  tests/test_phase2c_live_smoke.py tests/test_phase2c_pos_hardening.py \
  tests/test_phase2e_date_basis.py tests/test_phase2_admin_grace.py \
  tests/test_phase3_historical_credit.py tests/test_phase4a_approval_gate.py
# Expected (with brittle test in phase2d run separately): ~120/120 plus phase2d 16/17 → fix → 17/17 = 137/137.
```

### Targeted Phase 2D (post-fix verification)
```bash
cd /app/backend && python3 -m pytest tests/test_phase2d_permissions.py -v
# Expected after the surgical fix: 17/17 PASS.
```

### `/api/health`
```bash
API=$(grep REACT_APP_BACKEND_URL /app/frontend/.env | cut -d= -f2 | tr -d '\n')
curl -sS -o /dev/null -w "GET /api/health → %{http_code}\n" "$API/api/health"
# Expected: 200.
```

### Index inspection (read-only)
```bash
cd /app/backend && python3 /tmp/check_indexes.py
# Expected pre-fix:
#   returns: only _id_
#   payment_idempotency: _id_ + uniq_payment_idempotency
# Expected post-fix:
#   returns: _id_ + uniq_returns_org_rma  (keys={'organization_id': 1, 'rma_number': 1} unique=True)
```

### Same-org RMA duplicate scan (read-only, must show zero)
```bash
cd /app/backend && python3 /app/backend/scripts/rma_dedupe_dry_run.py
# Expected: "Zero duplicate rma_number groups found across all tenants."
```

### Global RMA duplicate scan (read-only, diagnostic only)
```bash
cd /app/backend && python3 /tmp/global_rma_dup_check.py
# Expected: 5 groups, 184 records, all cross-org — confirms WHY the old global
# index was wrong. Not affected by the surgical fix; documents the rationale.
```

### Restart backend (only after the index code change)
```bash
sudo supervisorctl restart backend && sleep 4 && tail -20 /var/log/supervisor/backend.err.log
# Expect: "Application startup complete." No DuplicateKeyError on uniq_returns_org_rma.
```

---

## 11. Final Instruction for the Next Fork

> **Read this handoff first.** Reconfirm the current baseline (FE 75/75, BE phase-specific audit suites green when run in isolation, `/api/health` 200, returns global rma_number index ABSENT, payment_idempotency compound index PRESENT). Reconfirm the corrected C-8 finding: the RMA backstop index needs to be compound `(organization_id, rma_number)`, not global on `rma_number` alone, and the Phase 2D `jwt-test` / `jwt-ok` usernames need randomisation. **Do not code yet.** Wait for the owner's final implementation prompt authorising the two surgical fixes.

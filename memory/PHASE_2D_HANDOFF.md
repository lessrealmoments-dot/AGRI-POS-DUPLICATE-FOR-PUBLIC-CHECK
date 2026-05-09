# Phase 2D Handoff — Branch / Tenant Permission Hardening

**Single source of truth for the next fork agent.**
Read this first. Run the smoke commands. Confirm green baseline. Then start Phase 2D only.

---

## 1. Project identity

| Field | Value |
|---|---|
| Repo / app | **AgriBooks** — multi-tenant POS + AR/AP for agri-supply retail |
| Stack | React 18 (frontend) · FastAPI + Motor (backend) · MongoDB (multi-tenant via `TenantCollection`) |
| Backend root | `/app/backend` |
| Frontend root | `/app/frontend` |
| API prefix | `/api` (kubernetes ingress routes `/api/*` → port 8001, everything else → 3000) |
| Live preview URL | `process.env.REACT_APP_BACKEND_URL` from `/app/frontend/.env` |
| Test admin | email `janmarkeahig@gmail.com` / pwd `Aa@58798546521325` (in `/app/memory/test_credentials.md`) |
| POS surfaces | Quick POS, Advanced POS, POS Terminal (online), POS Terminal offline → sync |
| Common backend route | `POST /api/unified-sale` is the single entry point for all 4 POS surfaces |

---

## 2. Audit cycle context

The current programme of work descends from the **read-only system audit** generated in Feb 2026:

- `/app/memory/AUDIT_REPORT_2026-02.md` — full audit (9 Critical, 14 High findings)
- `/app/memory/PHASE_1_HANDOFF.md` — handoff after Phase 1
- `/app/memory/PHASE_2B_FLOW_MATRIX.md` — Phase 2B verification matrix
- `/app/memory/PHASE_2D_HANDOFF.md` — **this file**

We have closed the **Critical** track (Phase 1) and the **POS Write-Side** P1/P2 track (Phase 2A → 2C.5). What remains for the next agent is **Phase 2D** — closing the High finding **H-5: Branch/Tenant access bypass risk**.

---

## 3. Completed phases (✅ done — DO NOT re-do)

### Phase 1A — Security Lockdown ✅
- Stripped plain-text admin PINs from offline sync payload; verification deferred to server-side sync.
- Auth-gated `/setup/reset`.
- Hardened `/admin-auth/setup-totp`.
- Production CORS strictness; JWT org-cross-check in `utils/auth.py`.

### Phase 1B — Inventory & Offline Sync Safety ✅
- Deferred inventory mutation until AFTER invoice insert (no orphan deductions on partial failure).
- Atomic `find_one_and_update` for draft-order finalization.
- Atomic `generate_next_rma_number()`.

### Phase 1C — Money / AR / Payment Safety ✅
- Central `assert_invoice_payable()` blocks payments on voided/cancelled/draft invoices.
- Fixed `current_balance` typo to `balance` in `routes/sync.py`.

### Phase 2A — Customer Balance Reconciliation Report ✅
- `GET /api/admin/customer-balance-reconciliation?branch_id=&min_drift=&limit=` — read-only.
- Risk bands: Minor ≤ ₱100 · Needs Review ≤ ₱5,000 · Critical > ₱5,000.
- Read-only Admin UI at `/audit/balance-reconciliation` (linked from Audit Center).
- Static-source guard test ensures the route module contains zero Mongo write calls.

### Phase 2B — Core POS Business Flow Verification Matrix ✅
- 33 fixture-only pytest cases across 7 flow groups (sales, payments, inventory, purchase, returns/voids, reports, POS-surface comparison).
- Consolidated matrix: `/app/memory/PHASE_2B_FLOW_MATRIX.md`.
- Surfaced 4 P1/P2 findings later fixed in Phase 2C.

### Phase 2C — POS Write-Side Hardening ✅
- **2C.1** Payment idempotency on `record_invoice_payment`, `pay_receivable`, `receive_customer_payment`. New collection `payment_idempotency` with unique index `(org_id, route, key)`.
- **2C.2** Status guard on modify/void payment routes (4 paths). For void: status check moved BEFORE PIN; audit-log row `blocked_void_payment_on_bad_invoice` written on block.
- **2C.3** Returns credit fallback removed: credit returns now require `invoice_number` OR `allow_pending_credit=true`.
- **2C.4** Overpayment beyond ₱0.50 tolerance rejected at backend.

### Phase 2C.5 — Frontend UX Compatibility Guards ✅
- Returns Wizard: invoice-number now required for credit customers (red border, helper text, blocks `canProceed()`, defensive `handleSubmit()` guard).
- Accounting → Pay Receivable dialog: red border + warning + disabled button + toast when amount > balance.
- Close Wizard → Pay Invoice dialog: same pattern.
- `TerminalReturnRefundModal` already passed `invoice_number` correctly — no change needed.

---

## 4. Test status (run from `/app/backend`)

### Latest pass count

| Suite | Count |
|---|---|
| `tests/test_phase1a_security.py` | 6 PASS |
| `tests/test_phase1b_inventory.py` | 4 PASS |
| `tests/test_phase1c_money.py` | 17 PASS |
| `tests/test_phase2a_balance_recon.py` | 18 PASS |
| `tests/test_phase2c_pos_hardening.py` | 5 PASS |
| `tests/phase2b/test_g1_sales.py` | 8 PASS |
| `tests/phase2b/test_g2_payments.py` | 9 PASS *(updated in 2C: now verify FIXED behaviour)* |
| `tests/phase2b/test_g3_inventory.py` | 6 PASS |
| `tests/phase2b/test_g4_purchase.py` | 0 PASS / **2 SKIP** (intentional — PO fixture cost) |
| `tests/phase2b/test_g5_returns_voids.py` | 7 PASS *(updated in 2C: now verify FIXED behaviour)* |
| `tests/phase2b/test_g6_reports.py` | 3 PASS |
| `tests/phase2b/test_g7_pos_comparison.py` | 3 PASS |
| `tests/test_phase2c_live_smoke.py` *(added by testing agent)* | 4 PASS *(live API)* |
| **TOTAL** | **86 PASS · 2 SKIP · 0 FAIL** |

### Intentional skips
- `tests/phase2b/test_g4_purchase.py::test_g4_draft_po_does_not_increase_stock`
- `tests/phase2b/test_g4_purchase.py::test_g4_po_idempotency_key_blocks_duplicate`

Both skip cleanly because `create_purchase_order` requires heavier fixtures (closed-day guard, fund balances). Verified by inspection in the matrix; not a regression.

### Pre-existing unrelated failures (NOT in scope for 2D)
- **11 hardcoded-date unit-test failures** in legacy test files. Documented in PHASE_1_HANDOFF.md. Per-owner rule: ignore until a dedicated cleanup phase. They live OUTSIDE the 86-test suite above.

### How to verify baseline before starting

```bash
cd /app/backend && python3 -m pytest \
  tests/test_phase1a_security.py \
  tests/test_phase1b_inventory.py \
  tests/test_phase1c_money.py \
  tests/test_phase2a_balance_recon.py \
  tests/test_phase2c_pos_hardening.py \
  tests/phase2b/ \
  -v 2>&1 | tail -10
# Expected: 82 passed, 2 skipped (the 4 live-smoke tests added by the testing
# agent live in test_phase2c_live_smoke.py and depend on the live preview URL).
```

To include live-smoke (requires preview URL up):
```bash
cd /app/backend && python3 -m pytest tests/test_phase2c_live_smoke.py -v
# Expected: 4 passed.
```

---

## 5. Files changed in recent phases

### Phase 2C (backend write-side hardening)
- **UPDATED** `/app/backend/utils/helpers.py` — added `assert_invoice_payment_modifiable`, `assert_payment_within_balance`, `OVERPAYMENT_TOLERANCE`, and the 3 idempotency helpers (`payment_idempotency_lookup_or_reserve` / `_record` / `_release`).
- **UPDATED** `/app/backend/routes/invoices.py` — `record_invoice_payment` (idempotency + overpayment), `void_invoice_payment` (status guard before PIN + audit log).
- **UPDATED** `/app/backend/routes/accounting.py` — `pay_receivable` (idempotency + overpayment), `receive_customer_payment` (idempotency wrap), `modify_customer_payment`, `update_invoice_payment`, `void_customer_payment` (status guards).
- **UPDATED** `/app/backend/routes/returns.py` — credit-return path now requires `invoice_number` or `allow_pending_credit=true`.
- **UPDATED** `/app/backend/main.py` — startup unique index on `payment_idempotency (org_id, route, key)`.
- **NEW** `/app/backend/tests/test_phase2c_pos_hardening.py` (5 tests).
- **NEW** `/app/backend/tests/test_phase2c_live_smoke.py` (4 tests, added by testing agent).
- **UPDATED** `/app/backend/tests/phase2b/test_g2_payments.py` — overpayment + idempotency tests now verify FIXED behaviour.
- **UPDATED** `/app/backend/tests/phase2b/test_g5_returns_voids.py` — returns credit tests now verify FIXED behaviour.

### Phase 2C.5 (frontend UX guards)
- **UPDATED** `/app/frontend/src/pages/ReturnRefundWizard.js` — `canProceed()`, `handleSubmit()`, label, helper text, red border on credit-customer with empty invoice_number.
- **UPDATED** `/app/frontend/src/pages/AccountingPage.js` — `handlePayment()` guard, input red border, inline warning, disabled button on overpayment.
- **UPDATED** `/app/frontend/src/pages/CloseWizardPage.js` — `quickReceivePayment()` guard, input red border, inline warning, disabled button on overpayment.

---

## 6. Approved architectural decisions — DO NOT re-debate

These are settled. The next fork must NOT relitigate them:

1. **Backend is the source of truth.** Frontend guards are *UX assistance*, never the only enforcement.
2. **Repair existing routes / helpers.** No new parallel modules, no rewrites.
3. **No duplicate components / pages / endpoints.** Reuse the established surface.
4. **No Historical Credit Encoding** until after Phase 2D and 2E close and the owner approves.
5. **No broad UI redesign.** Targeted, minimal changes only.
6. **No production data mutation** unless explicitly approved by the owner. Diagnostic features remain read-only.
7. **Read-only diagnostics stay read-only.** The 2A reconciliation page must never gain a "Fix Balance" or "Auto-Correct" button.
8. **POS surfaces are protected.** Any change must preserve Quick POS, Advanced POS, POS Terminal (online), and POS Terminal offline → sync.
9. **`POST /api/unified-sale` is the single sales path** — do not introduce alternates.
10. **Idempotency key contract.** All 3 payment-write routes accept `idempotency_key`. Back-compat preserved (no key = pass through).
11. **Invoice payable contract** is enforced via the central helpers in `utils/helpers.py` — every new payment-write route must reuse them.
12. **Tenant isolation** is enforced by `TenantCollection` (org-scoped Mongo wrapper). Any route that touches `_raw_db` directly is a higher-risk path and must add explicit `organization_id` and `branch_id` filters.

---

## 7. Remaining risks and backlog

### Pending phases (in the recommended order)
- **Phase 2D — Branch/Tenant Permission Hardening** (audit H-5) — *next fork*
- **Phase 2E — Date-basis Standardisation across reports** (audit H-13)
- **Phase 3 — Historical Credit Encoding / notebook AR** (P1, owner sign-off required)
- **Phase 4 — Cleanup / dedup** (sales vs sales-new, UI alignment)
- **Phase 5 — Full regression + deployment checklist**

### Backlog (non-blocking, can be addressed any time)
- **Manual duplicate-RMA cleanup** before the existing unique index can enforce on legacy rows.
- **11 pre-existing hardcoded-date unit-test failures** outside the 86-test passing suite — *not* in the 2D scope.
- The 2 PO `pytest.skip` cases in 2B Group 4 — would need a heavier fixture (closed-day + fund balances). Defer to Phase 4 cleanup.
- **PaymentsPage** input has no `max` attribute on row-amount inputs, but the backend route silently caps. Considered acceptable. Revisit if customer reports confusion.
- **Optional UX (not started)**: "Tap to fill exact balance" on Pay Receivable dialog. Owner-discretion.

---

## 8. Phase 2D scope (the next fork's only deliverable)

**Goal:** close audit finding **H-5 — Branch/Tenant access bypass risk** and any related permission gaps that surfaced during 2A/2B/2C.

### Targets

1. **Inspect `assert_branch_access` and related helpers** in `/app/backend/utils/`. Confirm:
   - Cashiers with `branch_ids=[A]` are blocked from data in branch B.
   - Users with **missing** `branch_ids` (legacy accounts) do not silently get org-wide access unless their role explicitly allows it (admin / owner / super-admin).
   - The helper raises `HTTPException 403` consistently — not 401, not silent-pass.

2. **JWT cross-check** in `utils/auth.py`. Confirm:
   - `org_id` claim in the JWT is matched against `user.organization_id`.
   - Mismatch returns 401 with a clear error.
   - Tokens cannot survive an org change for the same user.

3. **High-risk raw DB access paths** — audit all callsites that hit `_raw_db.<collection>` directly (bypassing `TenantCollection`). Each must include explicit `organization_id` AND, where applicable, `branch_id` filters. Document a list of all such paths in the matrix; harden the ones missing scope.

4. **Legacy super-admin shortcuts** — the audit flagged places where `is_super_admin: true` (or its absence) was being used as a coarse bypass. Identify each and replace with the standard `assert_branch_access` / `assert_admin_or_owner` pattern, preserving legitimate platform-admin paths.

5. **POS Terminal sync** — `POST /api/sync/pos-data` must return ONLY the data the user is authorized to see (their `branch_ids`). Verify the response is filtered, not just the request.

### Hard constraints
- **Preserve legitimate owner / admin / super-admin workflows.** A super-admin must still be able to access multi-tenant tooling (Audit Center, Universal Key, etc.) through the existing intended paths. Don't break support flows.
- **Preserve all 4 POS surfaces.** Quick POS, Advanced POS, POS Terminal (online), Terminal offline → sync must still work for authorised users.
- **No broad UI redesign.** If a UI element needs a permission gate (e.g. hiding a button), do it minimally — don't restructure pages.
- **No production data mutation.** All 2D changes are guard-tightening + tests; no migrations.

### Out of scope for 2D
- Date-basis standardisation (Phase 2E)
- Historical credit encoding (Phase 3)
- Sales-page dedup (Phase 4)
- Hardcoded-date test cleanup
- New features of any kind

---

## 9. Required Phase 2D tests

Add as `backend/tests/test_phase2d_permissions.py`. Use the fixture pattern from `backend/tests/phase2b/_fixtures.py` (throw-away org/branch/customer/products) — never touch production tenants.

**Required cases:**
1. Cashier with `branch_ids=[A]` can read sales/inventory/customers in branch A.
2. Cashier with `branch_ids=[A]` is blocked (HTTP 403) from sales/inventory/customers in branch B.
3. User with **missing** `branch_ids` (legacy account, role=cashier) does NOT get org-wide access; must be 403.
4. User with **missing** `branch_ids` (role=admin) DOES get org-wide access (legitimate path).
5. Owner role: full org access; cross-org still blocked.
6. Super-admin: access works only through intended platform-admin paths; unit-style request for tenant B's data still verifies isolation.
7. Tenant A user cannot access tenant B data via direct API call (TenantCollection guard).
8. JWT `org_id` mismatch (token issued for org A, used in org B context) → 401.
9. Sensitive routes (`/sales`, `/invoices`, `/accounting/receivables`, `/customers`, `/returns`, `/sync/pos-data`) all enforce `organization_id` AND `branch_id` scoping.
10. POS Terminal sync (`POST /api/sync/pos-data`) returns only authorized branches' data.
11. Quick POS / Advanced POS / Terminal / offline sync remain functional for an authorized cashier (regression).
12. Static guard: list every `_raw_db.` callsite in `routes/` and assert each includes an `organization_id` filter (regex test).

Also rerun and **must remain green**:
- All Phase 1 tests (27)
- Phase 2A tests (18)
- Phase 2B tests (31 + 2 skip)
- Phase 2C tests (5)

Total expected at end of 2D: **86 + ~12 new = ~98 PASS / 2 SKIP / 0 FAIL**.

---

## 10. Smoke commands the next fork MUST run before starting

Run all four. If any fails, **stop and report**; do not begin Phase 2D.

```bash
# 1. Service health
sudo supervisorctl status backend frontend mongodb

# 2. Backend regression baseline
cd /app/backend && python3 -m pytest \
  tests/test_phase1a_security.py \
  tests/test_phase1b_inventory.py \
  tests/test_phase1c_money.py \
  tests/test_phase2a_balance_recon.py \
  tests/test_phase2c_pos_hardening.py \
  tests/phase2b/ \
  2>&1 | tail -5
# Expected: 82 passed, 2 skipped, 0 failed.

# 3. Frontend lint smoke (only the 3 files touched in 2C.5)
# Run via the lint tool, not eslint directly — environment-managed config.
# Expected: 0 issues on each.

# 4. Live API health
API_URL=$(grep REACT_APP_BACKEND_URL /app/frontend/.env | cut -d '=' -f2 | tr -d '\n')
TOKEN=$(curl -s -X POST "$API_URL/api/auth/login" -H "Content-Type: application/json" \
  -d '{"email":"janmarkeahig@gmail.com","password":"Aa@58798546521325"}' \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['token'])")
curl -s -o /dev/null -w "products: %{http_code}\n"      -H "Authorization: Bearer $TOKEN" "$API_URL/api/products?limit=1"
curl -s -o /dev/null -w "customers: %{http_code}\n"     -H "Authorization: Bearer $TOKEN" "$API_URL/api/customers?limit=1"
curl -s -o /dev/null -w "balance-recon: %{http_code}\n" -H "Authorization: Bearer $TOKEN" "$API_URL/api/admin/customer-balance-reconciliation?limit=5"
# Expected: products: 200 / customers: 200 / balance-recon: 200
```

---

## 11. Instructions to the next fork agent

> **New fork agent**: read this handoff first, run the four smoke commands in §10, confirm baseline is **82 passed / 2 skipped / 0 failed** and all live API checks return 200, then begin **Phase 2D — Branch/Tenant Permission Hardening** as scoped in §8 and §9.
>
> **Do NOT** start Phase 2E, Phase 3 (Historical Credit Encoding), Phase 4 cleanup, the 11 hardcoded-date test fixes, or any UI redesign.
>
> **Do NOT** mutate production data. Use throw-away fixtures only (pattern in `tests/phase2b/_fixtures.py`).
>
> **Do NOT** change any code path that touches Quick POS, Advanced POS, POS Terminal, or `POST /api/sync/pos-data` until you have a passing regression for that surface in your new test file.
>
> Stop after Phase 2D and report files changed, tests added, findings closed, remaining risks, and your recommendation for Phase 2E vs Phase 3.

— End of handoff —

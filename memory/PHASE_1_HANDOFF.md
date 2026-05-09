# AGRI-POS — Phase 1 Complete · Phase 2A Handoff (Audit 2026-02)

> **READ THIS FIRST.** This file is the single source of truth for the next fork agent. It encodes every approved decision, every closed finding, the manual cleanup tasks, and the exact next task with full scope. The audit report at `/app/memory/AUDIT_REPORT_2026-02.md` and the regression test files are the supporting artefacts.

---

## 1. Repository identity

- **Public repo:** `lessrealmoments-dot/AGRI-POS-DUPLICATE-FOR-PUBLIC-CHECK`
- **Working copy:** `/app` in this pod
- **Stack:** FastAPI backend (`/app/backend`) · React frontend (`/app/frontend`) · MongoDB
- **Multi-tenant:** `TenantCollection` wrapper (fail-closed) + ContextVar `_current_org_id`
- **Test admin credentials:** `janmarkeahig@gmail.com` / `Aa@58798546521325` (also in `/app/memory/test_credentials.md`)

## 2. What this audit cycle is

A read-only deep audit was performed in Feb 2026 → produced `AUDIT_REPORT_2026-02.md`. The report identified 9 P0 (Critical) findings, 14 P1 (High), 12 P2 (Medium), 8 P3 (Low). The owner approved a phased repair plan with strict scope discipline ("repair, not rebuild"). **Phase 1 (P0) is complete.** Phase 2 (P1) is starting next.

## 3. Phase 1 — COMPLETED & APPROVED (2026-02)

### Findings closed

| ID | What was wrong | What was fixed | Phase |
|---|---|---|---|
| **C-1** | `/api/sync/pos-data` shipped plain manager PINs + admin bcrypt hash to clients | Identity-only directory; PIN re-verified server-side at sync via existing `verify_pin_for_action` (mirrors `offline_price_changes` pattern). `pin_resync_failed` audit row written to `security_events` if wrong | 1A |
| **C-2** | `/api/setup/reset` wiped DB with no auth | Super-admin + `ALLOW_DB_RESET=true` env + password re-confirm + `security_events` audit row | 1A |
| **C-3** | `/api/admin-auth/setup-totp` allowed TOTP takeover by password thief | Refused if `totp_enabled` or `last_admin_login` is set; bootstrap-only window preserved | 1A |
| **C-4** | Sales/sync deducted inventory before invoice was committed | Inventory mutations deferred into `pending_inventory_ops` / `sync_inventory_ops` lists; applied only AFTER invoice commit | 1B |
| **C-5** | `/api/sales/sync` race could double-deduct under concurrent retries | Insert-first-then-mutate; `envelope_id` unique partial index rejects duplicates BEFORE inventory is touched | 1B |
| **C-6** | `_finalize_draft_offline` deducted inventory before guarded draft update | Guarded `update_one({status: for_preparation})` runs first; `modified_count==0` → return without mutating | 1B |
| **C-7** | `routes/sync.py:462` wrote `$inc current_balance` (non-existent field) | Changed to `$inc balance` (the canonical AR field) | 1C |
| **C-8** | RMA `count_documents+1` was racy + cross-tenant | New `utils/numbering.generate_next_rma_number(branch_id, org_id)` atomic counter scoped `(org, branch, date)`; unique partial index added | 1B |
| **C-9** | `record_invoice_payment` accepted payments on voided invoices | Central helper `utils/helpers.assert_invoice_payable(inv)`; rejects 6 statuses: `voided`, `cancelled`, `deleted`, `for_preparation`, `processing`, `error_partial_write` | 1C |

### Bonus hardening shipped within Phase 1A

- **CORS fail-closed in production** — if `ENV=production` and `CORS_ORIGINS` is unset or `*`, backend refuses to start
- **JWT org cross-check** — `payload.org_id` validated against `user.organization_id` for non-super-admins (rejects stale tokens after org migration)
- **Admin auth audit logs** — `admin_login_failed`, `admin_login_totp_ok`, `admin_recovery_used`, `admin_setup_totp_blocked_*`, `admin_verify_setup_blocked` all written to `security_events`
- **`/setup/initialize`** refuses if any orgs/users exist (prevents partial-restore re-init)

### Files changed across Phase 1

**Backend (10):**
- `backend/routes/sync.py` — C-1, C-4, C-5, C-6, C-7
- `backend/routes/sales.py` — C-4 (deferred inventory + guarded draft finalize, replaced `replace_one`)
- `backend/routes/setup.py` — C-2
- `backend/routes/admin_auth.py` — C-3 + audit logs
- `backend/routes/invoices.py` — C-9
- `backend/routes/accounting.py` — C-9 (single + batch payment paths)
- `backend/routes/returns.py` — C-8 (uses new RMA generator)
- `backend/utils/auth.py` — JWT org cross-check
- `backend/utils/numbering.py` — `generate_next_rma_number()`
- `backend/utils/helpers.py` — `NON_PAYABLE_INVOICE_STATUSES` + `assert_invoice_payable()`
- `backend/main.py` — CORS fail-closed; unique partial index `returns.rma_number`

**Frontend (5):**
- `frontend/src/lib/offlineAuth.js` — deferred-verification model
- `frontend/src/lib/syncManager.js` — null stale `admin_pin_hash`; strip `pin` defensively
- `frontend/src/pages/UnifiedSalesPage.js` — identity-only grants on sync
- `frontend/src/pages/terminal/TerminalShell.jsx` — same
- `frontend/src/components/OfflineCreditBypassDialog.js` — carry typed PIN in offline envelope only

**Tests + tooling (4):**
- `backend/tests/test_phase1a_security.py` — 6 tests
- `backend/tests/test_phase1b_inventory.py` — 4 tests (concurrency, race, atomicity)
- `backend/tests/test_phase1c_money.py` — 17 tests (guard helper + 3 routes + C-7 static guard)
- `backend/pytest.ini` — `asyncio_mode=auto`, **session-scoped event loop** (Motor needs this)

### Test command (run BEFORE any Phase 2A work to confirm green baseline)

```
cd /app/backend && python3 -m pytest tests/test_phase1a_security.py tests/test_phase1b_inventory.py tests/test_phase1c_money.py -v
```

**Expected:** `27 passed in ~2.5s`

### Audit report downloadables

- Canonical: `/app/memory/AUDIT_REPORT_2026-02.md`
- Static copies: `/app/uploads/audit/AGRI-POS_Audit_Report_2026-02.{pdf,html,md}`
- Auth-gated download API: `GET /api/reports/audit-2026-02/{pdf|html|md}` (admin/owner/super-admin only)

## 4. Approved architectural / scope decisions (DO NOT REVISIT)

These were debated and decided during Phase 1. Do not re-open them in Phase 2A.

| Decision | Rationale |
|---|---|
| **No MongoDB transactions yet** | Requires replica set + WT engine. Architectural decision deferred until Phase 2 finishes. Compensation pattern (`error_partial_write` tombstone + `security_events` row) is the interim. |
| **Offline manager PIN: deferred-verification chosen** over offline approval tokens | Minimum-disruption fix preserving the existing "manager types PIN on cashier device" workflow. The cleaner long-term option (pre-issued offline approval tokens) was documented as a future enhancement, NOT implemented. |
| **Draft `replace_one` is GONE** | Replaced with guarded `find_one_and_update({status: for_preparation})` + `update_one({"$set": invoice})`. Preserves doc_code, signature_session_id, late_encode flags etc. that `replace_one` would have wiped. |
| **`customer.balance` remains THE canonical AR field** | No new field introduced. C-7 was a typo, not an architectural ask. All future ledger work uses `balance`. |
| **Returns credit application is OUT OF SCOPE for Phase 1 and 2A** | Currently can apply to any open invoice; deferred to Phase 2C. |
| **No frontend cleanup, no duplicate-page consolidation, no UI redesign** | Owner explicitly forbids until P0/P1 are closed. |
| **Historical Credit Encoding (notebook AR reconstruction)** is **Phase 3** — not before | Owner explicitly held this back so safety fixes ship first. |
| **`error_partial_write` is a tombstone, not auto-rollback** | Inventory mid-loop failure leaves the invoice as a tombstone + `security_events` row for manager review. Auto-reverse needs transactions. |
| **Three payment-write surfaces guarded by `assert_invoice_payable`** | `record_invoice_payment` + `pay_receivable` (single) + `receive_customer_payment` (batch — skips with `skipped_reason` instead of 400'ing). `modify-payment` and `void-payment` are out of scope (Phase 2C). |

## 5. Phase 1 manual cleanup tasks (NOT YET DONE)

### 🚨 Duplicate RMA cleanup before unique index can enforce

Until done, the index creation in `main.py` is silently skipped (try/except wrapped — backend will not fail). Run when convenient:

```js
// 1. Find duplicates
db.returns.aggregate([
  { $group: { _id: "$rma_number", count: { $sum: 1 }, ids: { $push: "$id" } } },
  { $match: { count: { $gt: 1 } } }
])

// 2. Rename all but the most-recent in each duplicate set with `_legacy` suffix
//    (or `_legacy_1`, `_legacy_2` for >2 dupes)

// 3. Force-create the index OR restart the backend
db.returns.createIndex(
  { rma_number: 1 },
  { unique: true, partialFilterExpression: { rma_number: { $exists: true, $type: "string" } } }
)
```

If a tenant has no duplicate RMAs today, the index is already created.

### Optional production env-var changes

| Variable | When required | Value |
|---|---|---|
| `CORS_ORIGINS` | Only when `ENV=production` is being newly introduced | comma-separated allowlist |
| `ALLOW_DB_RESET` | Only to enable `/api/setup/reset` in dev/staging | `true` |

Existing prod env unchanged. Both can be left untouched if the prod env never had `ENV=production` set.

## 6. Remaining P1 risks (Phase 2 scope)

| Severity | Risk | Phase |
|---|---|---|
| **High** | `customer.balance` drift accumulated from past `current_balance` no-op writes — every orphan-offline reconcile that hit the C-7 bug left AR inflated by an unknown amount | **2A — first task** |
| **High** | `customer.balance` is incremented in 30+ call sites with no reconciliation pass — drift can recur from any future bug | **2A** |
| **High** | Returns can apply credit to *any* open invoice for the customer, not just the linked invoice | **2C** |
| **High** | `assert_branch_access` grants org-wide access to legacy users with neither `branch_ids` nor `branch_id` (legacy bypass path) | **2D** |
| **Medium** | Mid-loop inventory failure tombstones the invoice but does not auto-reverse already-applied items | Architectural (transactions) |
| **Medium** | Offline negative stock is logged-only (`stock_warnings` array); no incident_ticket / admin notification | **H-12 in audit** |
| **Medium** | Payment endpoints not idempotent — network retry could double-record | Future Phase 2 |
| **Medium** | Two parallel payment requests against same open invoice → both succeed, double-pay | Future Phase 2 |
| **Medium** | Date-field convention varies across reports (some `order_date`, some `created_at`, some `payment.date`) | **2B** |
| **Low** | `modify-payment` and `void-payment` routes don't yet enforce `assert_invoice_payable` | **2C** |
| **Low** | 11 pre-existing unit-test failures (hardcoded dates) in older test files unrelated to audit | Backlog |

## 7. Owner's explicit scope rules (NEVER VIOLATE)

These came from the prompt structure during Phase 1 and apply to Phase 2A and beyond:

1. **Repair, do not rebuild.** Modify existing routes/helpers; do not create v2/new/final variants.
2. **No duplicate modules.** If a route or helper exists, harden it.
3. **Backend is the source of truth.** Frontend cannot bypass backend validation.
4. **Preserve POS workflows** — Quick POS, Advanced POS, POS Terminal, offline sync must keep working after every phase. If a fix changes a workflow, document the change explicitly.
5. **Stop at the end of each sub-phase and report.** Do not proceed without owner approval.
6. **Add regression tests for every fixed finding.** No "trust me, it works."
7. **Do not implement features outside the current sub-phase.**

## 8. NEXT TASK — Phase 2A (already approved · ready to execute)

**Owner approved Phase 2A immediately after Phase 1 sign-off.** The next fork agent should begin this task right after reading this file.

### Scope: Customer Balance Reconciliation Report — READ-ONLY DIAGNOSTIC

Owner's exact ask:

> Create a READ-ONLY diagnostic report to detect customer balance drift.
> Do not automatically change customer balances. Do not rewrite old invoices.
> Do not modify payments / returns / customer.balance.
> This phase is diagnostic only.
> It must not affect Quick POS, Advanced POS, POS Terminal, offline sync, sales creation, payment creation, returns, voids, or reports used by cashiers.

### Endpoint

`GET /api/admin/customer-balance-reconciliation?branch_id=...`

- **Access:** admin / owner / super-admin only
- **Tenant + branch scoped**
- **No mutation paths**

### Compare

1. Stored `customer.balance`
2. Ledger-computed balance from:
   - open invoices · credit sales · partial payments · payments · voided invoices · returns · credit memos · adjustments

### Report columns

- Customer ID · Customer name · Branch
- Stored balance · Ledger-computed balance · Difference
- # affected invoices · # payments · # returns/credits
- Last transaction date
- Risk level: `OK` / `Minor Difference` / `Needs Review` / `Critical`
- Recommended action

### Rules

- **Read-only only.**
- Backend is source of truth — do not rely on frontend calculations.
- Exclude or reverse voided/cancelled/deleted invoices properly.
- Include partial payments correctly.
- Include returns and credit memos correctly.
- If logic is uncertain for a specific transaction type → **flag it instead of guessing.**
- Surface drift > ₱0.50 only.
- Sort by absolute drift descending.
- Admin page reachable from Audit Center is OK if low-risk; otherwise backend endpoint first is enough.

### Required tests (9)

1. Customer with no transactions → zero balance
2. Customer with one credit invoice → correct balance
3. Customer with partial payment → correct remaining balance
4. Customer with fully paid invoice → zero balance
5. Customer with voided invoice → no payable balance
6. Customer with return/credit → reduced correctly
7. Customer with intentional stored-vs-ledger mismatch → appears in report
8. Branch/organization scoping works
9. Direct API access cannot view another branch/tenant's data

### Deliverable (stop after Phase 2A and report)

1. Files changed
2. Endpoint/report created
3. Balance computation formula used
4. Transaction statuses included/excluded
5. Tests added and results
6. Whether Quick POS, Advanced POS, POS Terminal, offline sync are unaffected
7. Remaining risks
8. Whether any actual drift was found in sample/test data, if available

### Suggested implementation outline (for the fork agent)

- New file: `backend/routes/balance_reconciliation.py` (single endpoint, ~150 lines)
- Wire into `main.py` `api_router.include_router(balance_reconciliation_router, prefix="/admin")`
- Reuse `db` (TenantCollection) so org scoping is automatic
- For each customer in the tenant: aggregate `invoices`, `customer_payments`, `returns` and compute net AR
- Drift formula proposal (verify against actual schemas before committing):
  ```
  ledger_balance = Σ open_or_partial.balance               # outstanding AR
                 - Σ unapplied_credit_memos.amount          # credits not yet applied
                 - Σ returns.credit_to_invoice              # already-applied returns
  drift = stored_customer.balance - ledger_balance
  ```
- Risk band:
  - `|drift| ≤ 0.50` → OK (don't include in response)
  - `0.50 < |drift| ≤ 100` → Minor Difference
  - `100 < |drift| ≤ 5000` → Needs Review
  - `|drift| > 5000` → Critical
- Cap response size (paginate or cap at 500 rows by absolute drift descending)
- New file: `backend/tests/test_phase2a_balance_recon.py` — 9 tests as listed above
- **Optional** new admin page `frontend/src/pages/BalanceReconciliationPage.js` (only if low-risk; backend first is sufficient per owner's ask)

## 9. Codebase quick reference

### Where things live

```
backend/
  routes/
    sales.py              ← /unified-sale (UnifiedSalesPage)
    sync.py               ← /api/sales/sync, /sync/pos-data, _finalize_draft_offline
    invoices.py           ← invoice CRUD + record_invoice_payment
    accounting.py         ← receivables + customer payment batch
    returns.py            ← RMA flow
    setup.py              ← /setup/reset, /setup/initialize
    admin_auth.py         ← /admin-auth/* (super-admin TOTP)
    audit.py              ← Audit Center backend
    overage_reserve.py    ← fund-pool _current_balance() (NOT customer balance — different concept)
  utils/
    auth.py               ← get_current_user, has_perm, check_perm, assert_branch_access
    helpers.py            ← now_iso, NON_PAYABLE_INVOICE_STATUSES, assert_invoice_payable
    numbering.py          ← generate_next_number, generate_next_rma_number, check_idempotency
    closed_day_guard.py   ← late-encode + closed-day rules
  config.py               ← TenantCollection, _raw_db, set_org_context, get_org_context
  main.py                 ← FastAPI app + index creation + CORS
  tests/
    test_phase1a_security.py    ← 6 tests
    test_phase1b_inventory.py   ← 4 tests
    test_phase1c_money.py       ← 17 tests
  pytest.ini              ← asyncio_mode=auto, session-scoped event loop

frontend/src/
  pages/
    UnifiedSalesPage.js          ← /sales-new (current POS)
    SalesPage.js                 ← /sales (legacy, awaiting Phase 4 cleanup)
    terminal/TerminalShell.jsx   ← POS Terminal
    AuditCenterPage.js           ← admin audit dashboard
  lib/
    offlineAuth.js               ← verifyOfflinePin (deferred verification)
    syncManager.js               ← /sync/pos-data writer
    offlineDB.js                 ← IndexedDB shape
  components/
    OfflineCreditBypassDialog.js ← manager PIN dialog (offline)

memory/
  AUDIT_REPORT_2026-02.md       ← canonical audit
  PRD.md                        ← original requirements
  CHANGELOG.md                  ← bug-fix history
  test_credentials.md           ← admin login
  PHASE_1_HANDOFF.md            ← THIS FILE
```

### Key conventions to mimic

- Every route uses `Depends(get_current_user)`; permissions checked via `check_perm(user, "module", "action")`
- Multi-tenant queries use `db.<collection>` (auto-scoped). Cross-tenant or transient (terminal sessions, counters) use `_raw_db.<collection>`.
- Audit rows: `db.audit_log` for business actions, `_raw_db.security_events` for security/admin actions
- Atomic counters: `_raw_db.counters` with `find_one_and_update($inc seq, upsert=True, return_document=AFTER)` — see `utils/numbering.py`
- Test isolation: every Phase 1 test seeds throw-away `org_id`/`branch_id`/`product_id`/`customer_id` with unique uuid suffix; never touches real tenant data. Phase 2A tests must follow this pattern.
- Test event loop: pytest.ini sets session-scoped because Motor binds to first loop. **Don't change this.**

### Test admin credentials

```
Email:    janmarkeahig@gmail.com
Password: Aa@58798546521325
```

Full file: `/app/memory/test_credentials.md`

## 10. Smoke commands the fork agent should run before doing anything

```bash
# 1. Confirm services
sudo supervisorctl status

# 2. Confirm Phase 1 is still green
cd /app/backend && python3 -m pytest tests/test_phase1a_security.py tests/test_phase1b_inventory.py tests/test_phase1c_money.py -v

# 3. Confirm live API
API_URL=$(grep REACT_APP_BACKEND_URL /app/frontend/.env | cut -d '=' -f2 | tr -d '\n')
TOKEN=$(curl -s -X POST "$API_URL/api/auth/login" -H "Content-Type: application/json" \
  -d '{"email":"janmarkeahig@gmail.com","password":"Aa@58798546521325"}' \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['token'])")
curl -s -o /dev/null -w "products: %{http_code}\n" -H "Authorization: Bearer $TOKEN" "$API_URL/api/products?limit=1"
curl -s -o /dev/null -w "customers: %{http_code}\n" -H "Authorization: Bearer $TOKEN" "$API_URL/api/customers?limit=1"
curl -s -o /dev/null -w "branches: %{http_code}\n" -H "Authorization: Bearer $TOKEN" "$API_URL/api/branches"
```

If all of step 2 returns 27/27 and step 3 returns 200/200/200, the system is in known-good state and Phase 2A work can begin.

---

## TL;DR for the fork agent

1. **Read this file first.** Then read `/app/memory/AUDIT_REPORT_2026-02.md` for full context.
2. **Run the smoke commands in §10** to confirm green baseline.
3. **Begin Phase 2A** per §8 — Customer Balance Reconciliation Report (READ-ONLY).
4. **Stop after Phase 2A and report** per the deliverable checklist in §8.
5. **Do not start Phase 2B/2C/2D, Historical Credit Encoding, UI cleanup, or any mutation feature** without explicit owner approval.
6. **Owner is technical, prefers concise reports, and dislikes over-stipulation.** Match the response style of the previous Phase 1 deliverables: bulleted, dense, finding IDs prominent, code blocks where appropriate.

— End of handoff —

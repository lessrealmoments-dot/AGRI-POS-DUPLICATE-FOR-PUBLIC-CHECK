# AGRI-POS — Full Logic, Connection, Security & Data-Flow Audit

**Repository:** `lessrealmoments-dot/AGRI-POS-DUPLICATE-FOR-PUBLIC-CHECK` (audited via `/app` working copy)
**Date:** 2026-02
**Mode:** READ-ONLY DEEP AUDIT (no code changes applied)
**Scope:** FastAPI backend (`/app/backend`) + React frontend (`/app/frontend`) + MongoDB schema

> Backend is treated as the source of truth where backend and frontend disagree. No fixes applied.

---

## 1. Executive Summary

The system is **functionally rich and broadly self-consistent**, with mature ideas around tenant isolation (`TenantCollection`), idempotency on offline sync (envelope_id unique index), late-encode controls, audit logging, and signature/PIN policy. However, the audit surfaced a **non-trivial number of correctness, atomicity, and security defects** that are concentrated in five areas:

1. **Atomicity / multi-collection writes** — sales, returns, voids, and offline sync mutate inventory + invoices + wallets + customer balance in sequence, never inside a transaction or compensating action. A mid-flow failure leaves stock, AR, or cash in a permanently inconsistent state.
2. **Customer balance drift** — `customers.balance` is incremented/decremented in 30+ call sites with no reconciliation pass, plus one call site (`sync.py:437`) writes to a non-existent field (`current_balance`), silently no-op'ing AR reversal during orphan-offline reconciliation.
3. **Offline sync double-deduction window** — `sales/sync` checks idempotency, then mutates inventory, then inserts the invoice. Two concurrent retries can both pass the check and both deduct stock before the unique index trips.
4. **Plain-text PIN exposure to the client** — `/api/sync/pos-data` ships every admin/owner/manager PIN in cleartext (and the bcrypt hash of the admin PIN) to *any* authenticated user. This is a P0 secret-leak.
5. **Public/loose admin surfaces** — `/api/setup/reset` (full data wipe), `/api/setup/initialize`, `/api/admin-auth/setup-totp` (TOTP take-over of an un-MFA'd super admin), `/api/admin/backfill/iter240`, `/api/admin/reconcile-orphan-offline-draft`, and CORS defaulting to `*` form a wider attack surface than necessary.

Backdated credit sales are partially supported via the `late_encode` flow (capped at 7 days, no link to last count sheet), which is **insufficient for the user's "old notebook AR encoding" use case**. A proper Historical Credit / AR Reconstruction workflow is needed (see §6).

**Overall risk posture:**
- **Critical (P0):** 9 issues — fix before next production deploy
- **High (P1):** 14 issues — fix in the next 1–2 cycles
- **Medium (P2):** 12 issues — schedule for the next quarter
- **Low (P3):** 8 issues — backlog

---

## 2. Critical Findings (P0)

| # | Finding | File / Route | Risk |
|---|---------|--------------|------|
| C-1 | `/api/sync/pos-data` ships **plain-text PINs** of all admin/owner/manager users + bcrypt admin PIN hash to any authenticated caller | `routes/sync.py:734-798` | Any cashier can extract every manager/admin PIN. Privilege escalation, void/refund/discount bypass, audit forgery. |
| C-2 | `/api/setup/reset` deletes **every collection** with only `confirm_text="DELETE ALL DATA"` and **no auth** | `routes/setup.py:213-238` | Anyone on the internet can wipe the entire database. Data loss. |
| C-3 | `/api/admin-auth/setup-totp` lets a password holder set TOTP secret on a super-admin who hasn't enabled TOTP yet — full account takeover with just the password | `routes/admin_auth.py:150-223` | Super-admin compromise → all tenants exposed. |
| C-4 | Sales / offline-sync deduct inventory **before** invoice insert; on insert-failure (DuplicateKey, network, validation) stock is gone with no compensating reverse | `routes/sales.py:583-685, 902-910`, `routes/sync.py:864-1015` | Inventory permanently wrong, no audit trail. |
| C-5 | Offline `/api/sales/sync` idempotency check is non-atomic — two concurrent retries can both pass `find_one(...)` then both `$inc` inventory before the `envelope_id` unique index fires on the second insert | `routes/sync.py:870-983` | Double stock deduction under network retry / dual-tab POS. |
| C-6 | `_finalize_draft_offline` deducts inventory **before** the guarded draft `update_one`, with no rollback if the guard rejects a concurrent finalize | `routes/sync.py:99-179` | Same draft can deduct stock twice. |
| C-7 | `routes/sync.py:437` — `reconcile_orphan_offline_draft` reverses customer credit by `$inc current_balance` (wrong field; everywhere else it is `balance`) | `routes/sync.py:432-439` | Customer AR remains inflated after recovery; manager believes it was reversed. |
| C-8 | RMA number generator uses `count_documents({})+1` with no `branch_id`/`organization_id` scoping and no atomic counter — collisions guaranteed under concurrent returns; cross-tenant numbering pool | `routes/returns.py:105-106` | Duplicate RMA numbers, cross-tenant number leakage. |
| C-9 | `record_invoice_payment` does **not** check `status=="voided"` before applying — payments can be received against a voided invoice | `routes/invoices.py:192-264` | Wallet credited, customer balance silently goes negative, audit trail says "paid voided invoice". |

---

## 3. High-Risk Business Logic Findings (P1)

| # | Finding | File / Route | Risk |
|---|---------|--------------|------|
| H-1 | Draft finalization uses `db.invoices.replace_one({"id": draft_id}, invoice)` with **no version/status guard** | `routes/sales.py:900` | Concurrent finalize wipes the in-flight invoice; loses doc_code, signature_session, late_encode flags, recovery_note, etc. set between fetch and replace. |
| H-2 | No MongoDB transactions / Motor `start_session().with_transaction()` anywhere — every multi-collection mutation is best-effort sequential | system-wide | Sales, voids, returns, branch transfers, PO receive, repacks, daily close all have torn-write windows. |
| H-3 | Customer `balance` is stored & manually incremented in **30+ call sites**; no reconciliation report against ledger | `routes/sales.py:960`, `invoices.py:257,595,1157,1379`, `accounting.py` (×16), `returns.py:259,510`, `sync.py:1099`, etc. | Drift over time; no way to detect or repair without manual SQL-style audit. |
| H-4 | Returns can apply credit to **any** open invoice for the customer (`fallback`), not just the linked invoice — even unrelated, older debt | `routes/returns.py:213-220` | Customer A returns ₱500 of feed but gets ₱500 applied to a months-old chemical invoice they hadn't paid. Books move silently. |
| H-5 | `assert_branch_access` returns OK when user has **neither `branch_ids` nor `branch_id`** ("legacy unscoped") — a cashier with a malformed user record gains org-wide branch access | `utils/auth.py:172-177` | Cross-branch data exposure. |
| H-6 | CORS `allow_origins` defaults to `*` if env not set | `main.py:1184` | Cross-origin token theft via XSS/CSRF when env misconfigured. |
| H-7 | JWT TTL 24h with no refresh / rotation / blacklist | `utils/auth.py:26` | Stolen token is good for a full day; no logout-all. |
| H-8 | `get_current_user` does **not** validate that `user.organization_id == payload.org_id` — a moved/re-attached user keeps cross-org access until token expiry | `utils/auth.py:73-86` | Org migration bug → cross-tenant access. |
| H-9 | `users.email` index is **non-unique** (only sparse). Email duplicates allowed across tenants and within tenant | `main.py:389` | Login lookup ambiguity, password-reset confusion. |
| H-10 | `invoices.invoice_number` has **no uniqueness index** (only `idempotency_key` and `envelope_id` are unique); `generate_next_number` is atomic per branch but a manual import / migration / retry could create duplicates | `main.py:407-435`, `utils/numbering.py` | Duplicate invoice numbers possible via import or out-of-band insert. |
| H-11 | Backdated late-encode is capped at **7 days** with no awareness of last approved physical inventory count — too restrictive for "old notebook credit" reconstruction, while still allowing inventory to retro-deduct on the historical date | `routes/sales.py:78-118`, `utils/closed_day_guard.py` | User's primary outstanding business case is unsupported. See §6 for proposed Historical Credit Encoding flow. |
| H-12 | Offline negative stock is silently allowed — only writes a `stock_warnings` array on the invoice; no incident_ticket, no admin notification, no reconciliation queue entry | `routes/sync.py:113-148, 942-982` | Phantom inventory, no operator alert, eventual zero-out at next count sheet. |
| H-13 | `setup.py:initialize` creates the first admin even after multi-tenant migration if no users exist (race / partial-restore case) | `routes/setup.py:42-51` | Possibility of attaching a forged admin to an existing org if collections were partially deleted. |
| H-14 | `/api/admin/backfill/iter240` and `/api/admin/backfill/iter241-close-ledger` mutate every synced-from-offline invoice and every closed-day with `apply=true`; gated only by `role=="admin"` (no PIN, no audit-log, no per-call dry-run-then-confirm chain) | `routes/admin_backfill_240.py` | An admin with a stolen token can re-write historical financial data; no signed audit chain. |

---

## 4. Inventory Flow Audit Table

| Module | Action | Expected Stock Effect | Actual Code | Movement Logged? | Reversible? | Risk |
|---|---|---|---|---|---|---|
| Sales (`/unified-sale`) | Cash sale (full release) | -qty on commit | -qty before invoice insert | ✅ via `log_movement` | ✅ via void (reverses qty, not net of subsequent adjustments) | C-4 — stock gone if invoice insert fails |
| Sales | Partial release (reservation) | -qty avail, +qty reserved | Correct | ✅ | ✅ via void+reservation cleanup | OK; reservation doesn't expire-revert |
| Sales | Repack child sold | -qty parent / units_per_parent | Correct | ✅ on parent | ✅ | OK; parent capital change not propagated to existing reservations |
| Draft order | Reserve invoice number | none | none ✅ | n/a | n/a (cancel sets `cancelled_draft`) | OK |
| Draft → Finalize online | -qty once, replace draft inv | replace_one wipes guard fields | ✅ | ✅ | H-1 — replace_one race |
| Draft → Finalize offline | -qty once on canonical draft | -qty BEFORE guarded update_one | ✅ | ✅ on sync | C-6 — concurrent retry can deduct twice |
| Offline sale sync | -qty | -qty before insert | ✅ | ✅ | C-5 — race; H-12 — negative stock not alerted |
| Invoice edit | adjust delta only | $inc delta correctly handled (lines 380-447) | ✅ | ✅ | OK except no PIN required for closed-day edits when `pin == ""` and `is_on_closed_day == False` |
| Invoice void | +qty (or +reserved on partial-release) | Correct | ✅ | ✅ | OK |
| Returns — shelf | +qty | Correct | ✅ | ✅ | C-8 RMA collision |
| Returns — pullout | none | none + log loss | ✅ | ❌ (intentional — "physically gone") | OK |
| Return void | -qty (only shelf items) | Correct | ✅ | ✅ | OK |
| Branch transfer send | -qty source | Correct | ✅ | partial: receive can short, no auto-revert | OK |
| Branch transfer receive | +qty destination | Correct | ✅ | ✅ | OK |
| PO receive | +qty | Correct | ✅ | ✅ | partial-receive duplicate guard not formally checked here — review separately |
| PO cancel after receive | none (must do supplier return) | confirmed | n/a | n/a | OK |
| Repacking | -parent / +child slot via runtime derivation | child stock is derived, no separate row | n/a | n/a | OK by design; no double-count risk |
| Count sheet adjustment | set qty to actual | overwrites | ✅ | partial reversibility | check `count_sheets.py` for variance tagging on movements collection |
| Crop credit release | -qty (charged-to-crop) | needs re-verification | review pending | review pending | review pending |

---

## 5. Money Movement / Z-Report Audit

### Date-field usage inventory (highest impact discovery)

| Report / Wallet flow | Date field actually used | Should use | Issue |
|---|---|---|---|
| Sales report (`reports.py`) | Mix of `order_date`, `created_at` | `order_date` (transaction date) | Inconsistent — backdated/late-encoded sales sometimes appear on encoded-day list |
| Z-Report carryover for late-encode | `late_encoded_at` (effective day) | Effective day ✅ | Correct, but the carryover label uses intended_date which is fine |
| Daily sales log → Close Wizard | `date` field on sales_log | matches order_date | OK |
| Cash collected today | sums `payments[].date` and `created_at` | `payment.date` only | Backdated payments can leak into today |
| Customer ledger | `order_date` | `order_date` ✅ | OK |
| Inventory movement | `created_at` | `created_at` ✅ | OK |
| Backdated entry report | none — does not exist | needs separate tab | gap |

| Money Flow | Expected | Actual | Risk | Fix |
|---|---|---|---|---|
| Cash sale → cashier wallet | +amount | Correct (`update_cashier_wallet`) | low | OK |
| Credit sale → AR | +balance on customer | `$inc balance` | drift | reconciliation report |
| Payment received | -AR, +wallet | Correct | drift | reconciliation report |
| Partial payment | -balance, +wallet, status=partial | Correct | OK | OK |
| Void invoice | reverse all `payments[]` from each fund + reset AR | Correct (handles split) | low | OK; but wallet can go negative — `allow_negative=True` is OK in this path but no alert |
| Return + cash refund | -refund from selected fund | Correct (safe lots FIFO) | OK | OK |
| Return + AR credit | -applied from any open invoice | H-4 risk | misapplication | restrict to linked invoice or unapplied credit memo |
| Closed-day edit | journal_entry auto-generated | Correct | low | requires manager PIN + audit row — both present |
| Daily close | ledger entries via `_write_close_ledger_entries` | Correct | low | OK |
| Z-Report PDF share | synthetic user; org context resolved | recently fixed | OK | keep monitoring |
| Customer interest accrual | -AR scope, daily; can compound | reads stored balance | drift if balance is wrong | derive principal from invoices, not customer.balance |

---

## 6. Backdated Credit Sale Audit (USER'S PRIORITY)

### Current state

The current `late_encode` flow in `routes/sales.py:41-126` and `utils/closed_day_guard.py:179-327`:

- **Allows** late-encoded credit/partial sales up to 7 days back, on the same VAT month, with manager PIN + reason ≥10 chars + 5/branch/day cap.
- **Stores** `late_encoded`, `late_encoded_at`, `late_encode_reason`, `late_encoded_by`, `late_encode_verifier_*`, `late_encode_days_back` on the invoice.
- **Inserts** an `audit_log` row + a `late_encode_log` row.
- **Inventory** moves on the historical `order_date` — i.e. `inventory.quantity` decrements **today** but the invoice line is stamped with the historical date. There is no historical-vs-encoded divergence in the inventory ledger.
- **Z-Report** shows the entry on the next-open day with a `[LATE ENCODE] original date {intended} (closed)` label.

### Why this is insufficient for the user's case

The user's scenario: a customer's notebook credit was never encoded; **months later**, when they pay, the system has no record of the prior debt. Today the system blocks any encoding > 7 days back. Even if the cap were lifted, the existing flow:

- Re-deducts inventory **today** at historical `order_date`'s product cost → wrong COGS / margin reporting.
- Has no link to "last approved physical inventory count date for that branch/product".
- Mixes historical AR reconstruction with normal late-encode, distorting the late-encode metrics tab.

### Recommended workflow — separate **Historical Credit Encoding** module

| Rule | Recommendation | Risk Prevented | Code Changes |
|---|---|---|---|
| Store both `transaction_date` and `encoded_at` separately | Add `encoded_at` field on every invoice (already exists for late-encode); make `order_date` = historical and `created_at` = encoded for this flow | Reports can choose either basis intentionally | small |
| Reports use `transaction_date` for *Sales by date* and `customer ledger` | Switch to `order_date` everywhere; never use `created_at` for these reports | Backdated AR appears in correct historical position on the customer ledger | medium |
| Z-Report uses `created_at` (encoded_at) | Keep current carryover behaviour | Old Z-Reports remain immutable | none |
| Inventory ON historical date OR on encoded date — NOT both | New rule: if `order_date < last_count_sheet.date` → **do not** decrement inventory; create a `historical_credit_only=True` invoice with no movement and a sticky `inventory_already_settled_at_count` flag | Prevents double-deducting stock that was already reconciled in a count sheet | medium |
| If `order_date >= last_count_sheet.date` → require admin approval + reason + proof, then deduct inventory normally | Same workflow but allowed | Recent unrecorded sales still adjust stock | medium |
| Add `last_approved_count_sheet_date` per (branch, product) | Pull from the latest `count_sheets` doc with status=`approved` | Stable stopper for the rule above | small (already in count_sheets.py) |
| Required role: `admin` (not manager); reason ≥ 20 chars; proof_url required | Stricter than late_encode | Forensic audit trail | small |
| New audit collection: `historical_credit_encoding_log` | Distinct from `late_encode_log` | Clean separation in Audit Center | small |
| Separate "Backdated Credit Encoding Report" UI | New tab under Audit Center → "Historical AR Encoding" | Reviewers can spot patterns | medium |
| `backdated_credit=True` invoices are excluded from the 5/branch/day late-encode cap | Lift the 7-day rule for this specific flow only | User's "months later" use case | small |
| Cash backdating remains forbidden | unchanged | Z-Report tampering prevented | none |

**Frontend change:** New page `pages/HistoricalCreditEncodingPage.js` (or reuse `UnifiedSalesPage` with a `mode=historical_credit` switch behind admin permission). Backend: new endpoint `POST /api/sales/historical-credit` that bypasses the 7-day cap but enforces the `last_count_sheet_date` rule and writes to `historical_credit_encoding_log`. Re-use `_finalize_draft_offline`-style guard pattern for atomicity.

---

## 7. Void / Return / Refund Audit

| Original Transaction | Reversal Action | Expected Inv Effect | Expected Money Effect | Actual | Risk |
|---|---|---|---|---|---|
| Cash sale | Void invoice | +qty | -cashier wallet | Correct | OK |
| Credit sale | Void invoice | +qty | -customer.balance, no wallet change | Correct | drift on customer.balance |
| Partial sale | Void invoice | +qty | reverse each `payment[]` from its source | Correct (per-payment fund reversal in lines 1097-1133) | wallet can go negative — flagged by `allow_negative=True` but no alert |
| Sale with split payment | Void invoice | +qty | reverse cash + digital | Correct | OK |
| Partial-release sale | Void invoice | only +reserved_qty (released qty stays out) | reverse cash | Correct | OK |
| Return — shelf | Return | +qty | -wallet | Correct | C-8 RMA collisions |
| Return — pullout | Return | none + loss log | -wallet | Correct | OK |
| Return void | Reverse return | -qty (shelf only) | +wallet | Correct | also reverses applied AR credit (good) |
| Voided invoice — receive payment | should refuse | NO check on voided status | C-9 | wallet credited against voided invoice | severe drift |
| Payment void | Reverse a single payment | -wallet, +AR | Correct | OK |
| PO cancel after partial receive | should not auto-remove stock | not auto-removed (manual supplier return required) | OK | OK |
| Branch transfer cancel after send | manual supplier return path | OK | OK | OK |

**Double-void protection:** `void_invoice` checks `status == "voided"` ✅. `void_return` checks `voided` ✅. `void_payment` checks `payment.voided` ✅. ✓ good.

---

## 8. Purchase Order / Supplier Audit

(Cursory pass — full audit requires deeper read of `routes/purchase_orders.py` (2,288 lines).)

| Flow | Expected | Actual | Risk | Fix |
|---|---|---|---|---|
| PO creation | no inventory movement | confirmed by route names | OK | none |
| Receiving | +qty once | atomic per line | needs unique `(po_id, line_id, received_at)` constraint | partial dupe protection via `received_qty` increments |
| Partial receive | only delta increases | depends on UI passing only the delta — backend should validate `received_qty <= ordered_qty` | needs verification (P1) | add server-side cap |
| PO cancel after receive | no auto-remove | confirmed by user reports | OK | none |
| Supplier return | -qty + -payable | confirmed | OK | none |
| Cost update | logs to `capital_changes` and `price_change_log` | confirmed | OK | OK |
| Last purchase cost | derived in sync.py and dashboard | confirmed | OK | OK |

---

## 9. Offline Sync Audit

| Sync Scenario | Expected | Actual | Duplicate Risk | Fix |
|---|---|---|---|---|
| Same OFF receipt synced twice (same envelope_id) | second is no-op | unique index on `envelope_id` rejects second insert; existence check first | LOW once index in place; **inventory still moved** between check and unique index hit | apply inventory **after** insert succeeds (re-order) |
| Offline draft finalization synced twice | second is no-op | `linked_offline_receipt_number` canary + `status="for_preparation"` guard on update | inventory still moved twice if guard fails on second | apply inventory only after guarded update returns `modified_count > 0` |
| Network race during single OFF sync | only one inventory move | no `update_one(...).modified_count` checked → both pass | medium | guard pattern + check |
| Offline negative stock | warn + stock_warnings array | done | does NOT create incident_ticket / admin notification | add notification + reconciliation_queue entry |
| Offline price-match replay | branch_prices upserts only after PIN re-verifies | done; logs `pin_resync_failed` flag | OK | OK |
| Offline manager-bypass signature | retroactive signature_session w/ `status=bypassed` | done | OK | OK |
| Customer balance during offline sync | `$inc balance` after invoice insert | done | drift accumulates | reconciliation report |

**Design observation:** the entire sync layer should switch to a *write-then-mutate* order:
1. `db.invoices.insert_one(...)` (unique index will reject duplicate envelope_id)
2. only on success → mutate inventory + wallets + customer balance
3. on failure → rollback: invalid invoice (status `sync_failed`) + skip mutations

This is a non-trivial refactor but it eliminates the full class of double-deduction bugs at once.

---

## 10. API Connection Audit

### Confirmed dead/duplicate routes

| Endpoint | Backend | Frontend Caller | Status | Action |
|---|---|---|---|---|
| `/api/reports/test-report-v2` | `main.py:1205` | none | DEAD + UNAUTHENTICATED | **Remove** or auth-gate |
| `/api/setup/reset` | `routes/setup.py:213` | likely DEV-only | UNAUTHENTICATED | **Auth-gate to super_admin only OR remove** |
| `/api/setup/initialize` | `routes/setup.py:42` | first-run flow | UNAUTHENTICATED | guard against re-run on existing tenants (current check is too weak) |
| `/api/sales/*` (legacy `SalesPage.js`) | `routes/sales.py` (some legacy) | `pages/SalesPage.js` mounted at `/sales` | DUPLICATE WITH `/sales-new` | **Consolidate** — already in user backlog |
| `/api/admin/backfill/iter240` | one-shot | `AuditCenterPage.js` | KEEP but harden | require PIN + audit row + idempotency stamp (idempotency exists) |
| `/api/admin/backfill/iter241-close-ledger` | one-shot | `AuditCenterPage.js` | KEEP but harden | same |
| `/api/sales/sync` (legacy POST that handles brand-new offline sales) vs `_finalize_draft_offline` | both inside `routes/sync.py` | terminal | overlapping responsibilities | KEEP both, but unify inventory-deduction helper |
| `/api/admin-auth/setup-totp` | `routes/admin_auth.py:150` | one-time TOTP onboarding | DANGEROUS as-is | **Add side-channel verification** (email-link or pre-shared secret) before allowing first-time TOTP setup |

### Frontend → Backend connectivity audit

A full per-page sweep was not done, but spot checks confirm:
- `/sales-new` (UnifiedSalesPage) → `/api/unified-sale` ✅
- `/sales` (SalesPage) — still routed in `App.js:243` ❌ DUPLICATE PAGE; consolidate per user backlog
- `/pos`, `/sales-order` → both → UnifiedSalesPage ✅
- `ReturnRefundWizard` → `/api/returns` ✅
- `AuditCenterPage` → `/api/admin/backfill/*` ✅
- `MessagesPage` → `/api/sms/*` ✅
- `PrintQueuePage`, `TerminalShell` → `/api/print-jobs`, `/api/sync/*` ✅

### Dead frontend pages (suspected — needs design audit)
- `pages/UploadPage.js` vs `pages/DocUploadPage.js` — possibly overlapping
- `pages/PaymentsPage.js` vs `pages/AccountsPage.js` — possibly overlapping

---

## 11. Security and Permission Audit

| Risk | File / Route | Who Can Abuse | Severity | Fix |
|---|---|---|---|---|
| Plain-text PIN distribution to client | `sync.py:734-798` | any authenticated user | **Critical** | replace with bcrypt-only verification round-trip via `/api/verify/pin` (already exists); keep ONLY admin_pin_hash if you must, behind a manager-token gate |
| Public DB wipe | `setup.py:213` | anyone | **Critical** | super-admin auth + 2FA + confirmation token sent via email |
| TOTP take-over | `admin_auth.py:setup-totp` | password thief | **Critical** | force out-of-band confirmation (email link, recovery code) before allowing first TOTP setup |
| CORS `*` default | `main.py:1184` | external sites if env unset | High | force explicit allowlist; refuse to start if not set in production |
| JWT 24h, no refresh, no revocation list | `utils/auth.py` | stolen-token holder | High | shorter access tokens (15-30m) + refresh token + token blacklist on logout |
| Hardcoded test credentials in repo | `backend/tests/*.py` | anyone reading repo | High (since repo is public) | move to env-only; the credentials match a real production-ish account per `memory/test_credentials.md` |
| Backfill endpoints mutate prod data | `admin_backfill_240.py` | any admin token | High | PIN + 2-step confirm + audit ledger row per call |
| `/api/admin/reconcile-orphan-offline-draft` | `sync.py:304` | admin (PIN required) | Medium | already reasonably guarded; keep PIN; add per-orphan idempotency lock |
| `assert_branch_access` legacy bypass | `auth.py:172-177` | user with empty `branch_ids` | High | refuse access instead of granting org-wide (data has migrated; legacy mode can be retired) |
| `users.email` non-unique index | `main.py:389` | duplicate-email collisions | High | unique compound index `(organization_id, email)` |
| `check_perm`/`has_perm` short-circuit on `role=="admin"` | `utils/auth.py:100,120` | any user-doc with `role=admin` (not super-admin) | Medium | OK conceptually but means a malicious admin can void/edit anything — there's no per-action 4-eye principle except via PIN policy |
| No per-IP rate limiting on `/auth/*` and `/admin-auth/*` | `auth.py`, `admin_auth.py` | brute force | Medium | add slowapi or Cloudflare-side limiter |
| Global exception handler returns generic message but logs full stack to stdout | `main.py:87-104` | log scrapers | Low | OK as-is; just confirm logs are not shipped to a public bucket |

---

## 12. Multi-Tenant / Branch Isolation Audit

The `TenantCollection` design is **excellent in principle** — fail-closed when no org context, automatic `organization_id` injection. However:

| Surface | Expected Scope | Actual Scope | Isolation Risk | Fix |
|---|---|---|---|---|
| 339 uses of `_raw_db.*` across routes | explicit `organization_id` filter every time | mostly enforced; `superadmin.py` and `setup.py` legitimately cross-tenant; `terminal.py` (terminal_codes, qr_pair_tokens, terminal_sessions) is unscoped because they're transient — OK | LOW for terminal; MEDIUM for sms_hooks/settings.py | spot-check `sms_hooks.py:53,75,84,97,...` — these use `_raw_db` then resolve org via the branch — confirmed safe but fragile |
| `branch_transfer_orders` cross-branch within org | only via approved transfer | confirmed | OK | OK |
| `get_branch_filter` / `apply_branch_filter` honoring `branch_ids` | yes | confirmed | OK | OK except H-5 |
| `superadmin` impersonation | sets `_current_org_id` to target tenant | confirmed (`auth.py:77-80`) | OK | impersonation_session 4h TTL is reasonable |
| `users.find({"role": "admin"})` in `branch_transfers.py:614-619` | org-scoped via TenantCollection | confirmed | OK | OK |
| `notifications.target_user_ids` cross-branch admins | TenantCollection scopes by org | confirmed | OK | OK |
| `signature_sessions` | TenantCollection scopes | confirmed in TENANT_COLLECTIONS list | OK | OK |
| Counter collection (`counters`) is shared globally | by design — counter key is `{branch_id}:{prefix}` so branch UUID provides the namespace | confirmed | LOW | OK; but a cross-org branch_id collision is impossible because branch_ids are uuid4 |

---

## 13. Database Integrity Audit

| Collection | Issue | Risk | Fix | Migration? |
|---|---|---|---|---|
| `invoices` | no unique on `invoice_number` | dup numbers possible via import/migration | unique compound `(organization_id, invoice_number)` | yes — backfill scan first |
| `users` | `email` non-unique | dup logins | unique compound `(organization_id, email)` | yes — dedupe scan first |
| `users` | `username` unique global (not org-scoped) | cross-tenant username collision blocks user creation | change to `(organization_id, username)` | yes |
| `customers` | no unique on phone | dup customers, opening_balance double-create | unique compound `(organization_id, branch_id, phone)` partial filter | yes — already handled by `merge_duplicates` route |
| `movements` | no unique on `(reference_id, type)` | re-running PO receive could double-log | unique partial `(reference_id, branch_id, product_id, type)` | yes |
| `daily_closings` | no unique on `(branch_id, date)` | dup close possible | unique compound `(organization_id, branch_id, date)` | yes |
| `count_sheets.count_sheet_number` | unique not enforced; generator is racy | dup count sheet numbers | atomic counter via `_raw_db.counters` (like `generate_next_number`) | small |
| `returns.rma_number` | C-8 — racy `count_documents+1` global | duplicate RMAs across orgs | atomic counter scoped per `(organization_id, branch_id)` | yes |
| `safe_lots.remaining_amount` | no constraint preventing < 0 | overdraw silently | application check + monitoring | none |
| Soft delete vs hard delete | mostly soft (active=False) | OK | OK | none |
| Orphan records | `customers.reattach-orphans` exists | partial fix | OK | none |

---

## 14. Math and Calculation Audit

| Calculation | Frontend | Backend | Match? | Risk | Fix |
|---|---|---|---|---|---|
| Subtotal | per-line `qty*rate-disc` | same | ✅ | OK |  |
| Per-unit discount | `qty * disc_val` (amount) or `qty * rate * disc_val/100` (percent) | same | ✅ | OK |  |
| Overall discount | flat amount | flat amount | ✅ | OK |  |
| Grand total | `subtotal + freight - overall` | same | ✅ | OK |  |
| Balance | `grand_total - amount_paid` | same | ✅ | OK |  |
| Change | client-side only | n/a | n/a | OK | confirm receipt prints same value backend stores |
| COGS / margin | `cost_price * qty` snapshotted in sale_items | same | ✅ | OK |  |
| Repack capital | derived: `parent_cost / units + add_on_cost` | matches `sync.py:707-709` | ✅ | OK |  |
| Capital change alerts | dashboard reads `capital_changes` | written from PO receive | ✅ | OK |  |
| Interest (per invoice) | `principal * monthly/30 * days_overdue` | `invoices.py:289-294` | ✅ | OK; but uses stored `inv.balance` — drift exposed |
| Penalty | similar to interest | `accounting.py` | ✅ | same drift exposure |
| Rounding | `round(..., 2)` everywhere | matches | ✅ | OK; not using Decimal — accept Python float for now |
| Negative totals | guarded `max(0, ...)` | matches | ✅ | OK |
| Overpayment | not blocked; goes to `payments[].amount` > balance | not blocked at backend | ⚠ | refuse overpayment OR auto-credit memo |

---

## 15. Repacking Audit

| Repack Step | Expected | Actual | Risk |
|---|---|---|---|
| Parent-derived inventory | child availability = `parent_qty * units_per_parent` | confirmed in `sync.py:691-696`, `sales.py:589-595` | OK — no separate child stock row |
| Parent stock decrement on repack-child sale | -qty / units_per_parent | confirmed (`sales.py:602-608`) | OK |
| Cost allocation | `parent_cost / units + add_on_cost` | confirmed | OK |
| Repack reversal | comes from invoice void | confirmed | OK |
| JIT retail price for repacks | persisted to `branch_prices` after Owner PIN | confirmed (`sales.py:321-377`) | OK |
| Negative parent stock | allowed only with manager override pin | confirmed (`sales.py:489-522`) | OK |
| Repack reports | derived live | n/a | OK |

**Conclusion:** repacking design is **correct and not at risk** of creating/destroying value.

---

## 16. Report Reliability Audit

| Report | Source | Date Field | Voided handling | Branch/Tenant Filter | Reliability |
|---|---|---|---|---|---|
| Sales Report | `invoices` | `order_date` | excludes by default | TenantCollection + branch | OK |
| Inventory Report | `inventory` | `updated_at` | n/a | TenantCollection | OK |
| Movement Report | `movements` | `created_at` | filter `reversed != true` | TenantCollection | OK; some old reversed entries may not have the flag |
| Customer Balance Report | `customers.balance` | n/a | n/a | TenantCollection | DRIFT (H-3) |
| Customer Receivables Summary | `invoices` aggregated | `order_date`/`due_date` | excludes voided | TenantCollection | better than `customers.balance`, recommend authoritative |
| Cashier Report | `daily_sales_log` | `date` | excludes voided | branch | OK |
| Z-Report | `daily_closings` + `wallet_movements` | `date` (branch) | tagged late_encode + voided handled | branch | OK |
| Daily Close Report | `daily_closings` | `date` | OK | branch | OK |
| Void/Refund Report | needs verification | `voided_at`? | n/a | TBD | low — minor |
| Purchase Report | `purchase_orders` | `order_date` | OK | branch | OK |
| Backdated Entry Report | DOES NOT EXIST | — | — | — | **Gap — build for backdated credit reconstruction** |
| Audit Log Report | `audit_log` | `created_at` | n/a | TenantCollection | OK |
| Discount Audit Report | `discount_audit_log` | `created_at` | excludes voided invoices | OK | OK |

---

## 17. Duplicate / Dead Code Audit

| Item | Action |
|---|---|
| `pages/SalesPage.js` (legacy `/sales`) vs `UnifiedSalesPage` (`/sales-new`, `/pos`, `/sales-order`) | **Remove** SalesPage.js, redirect `/sales` → `/sales-new` (already in user backlog) |
| `pages/PaymentsPage.js` vs `pages/AccountsPage.js` | review for overlap |
| `pages/UploadPage.js` vs `pages/DocUploadPage.js` | review for overlap |
| `routes/admin_backfill_240.py` | one-shot migrations — keep but rename / move to `routes/admin_backfills.py` and add a manifest |
| `backend_test.py` (root level) | older test; check if subsumed by `backend/tests/*` |
| `generate_test_report.py` (73KB) | one-shot script; move to `scripts/` |
| `generate_audit_report.py` (21KB at backend root) | one-shot; move to `backend/scripts/` |
| 11 hardcoded-date unit test failures | Pre-existing per handoff |

---

## 18. Existing Audit / Test File Review

| File | Status |
|---|---|
| `AgriBooks_Feature_Test_Report.pdf` | feature checklist — broadly positive; auth + sales + close path covered; no atomicity tests |
| `AgriBooks_Full_System_Audit_Report.pdf` | older audit — issues largely about UI; back-end risk surface NOT covered |
| `AgriBooks_SaaS_Test_Report_v2.pdf` | tenant-isolation + plan limits — well covered |
| `test_result.md` | sparse, mostly green |
| `backend/tests/test_full_system_audit.py` | tests cross-tenant isolation but does NOT test idempotency races |
| `backend/tests/test_unclosed_days_92.py` | hardcoded dates — part of P0 11-fail set |
| `backend/tests/test_cross_tenant_privacy_180.py` | useful baseline; should be expanded with the new H-5 / C-1 cases |

---

## 19. Recommended Fix Plan

### Phase 1 — Critical Safety Fixes (ship in next deploy)

1. **C-1**: Stop shipping plain-text PINs in `/api/sync/pos-data`. Replace `offline_pin_grants` with a per-PIN `bcrypt_hash` + verifier metadata; client can `bcrypt.compare()` locally without ever seeing the plain PIN. Remove `admin_pin_hash` exposure or move it behind a higher-tier role.
2. **C-2**: Auth-gate `/api/setup/reset` to super-admin token + email-confirmation token; or remove from production builds.
3. **C-3**: Block `/api/admin-auth/setup-totp` for any super-admin who has `last_admin_login` set; require an out-of-band recovery code or signed email link.
4. **C-4 / C-5 / C-6**: Re-order sync paths to *insert invoice first, mutate inventory + AR + wallet only after success*; check `update_one(...).modified_count` everywhere; add unique index on `(branch_id, invoice_number)` once duplicates are scanned.
5. **C-7**: Fix `current_balance` → `balance` in `routes/sync.py:437`.
6. **C-8**: Replace RMA `count_documents+1` with `_raw_db.counters` atomic counter, key=`{org_id}:{branch_id}:RMA`; backfill any pre-existing duplicates.
7. **C-9**: `record_invoice_payment` and `accounting.receive_customer_payment` must reject `inv.status == "voided"`.
8. CORS allowlist enforced in production; refuse to start if `CORS_ORIGINS` is empty/`*` outside dev.

### Phase 2 — Business Logic Consistency (next 1–2 cycles)

1. **H-1**: Replace `replace_one` in draft finalization with explicit `update_one` of allowed fields + `find_one_and_update` guard on `status="for_preparation"`.
2. **H-3**: Build a `customers.balance` reconciliation report — for every customer, compare stored balance against ledger-derived balance from `invoices` + `payments[]` + `returns.credit_applied_to_invoices`. Flag drift > ₱0.50 for manager review.
3. **H-4**: Restrict return credit application to the linked invoice only by default; add a separate "Apply to other open invoices" UI that requires manager approval per row, written to a distinct audit row.
4. **H-11**: Build the **Historical Credit Encoding** workflow per §6 above. **This is the user's #1 outstanding business case.**
5. **H-12**: Offline negative stock now creates an `incident_ticket` + admin notification + `offline_reconciliation_queue` entry.
6. **H-5 / H-6 / H-7 / H-8**: Tighten `assert_branch_access`, force CORS allowlist, shorter JWT + refresh, validate `payload.org_id == user.organization_id`.
7. **H-9 / H-10**: Add unique compound indexes; backfill scan first.
8. **H-13**: Setup re-init guard should refuse if any `organizations` doc exists.
9. **H-14**: All admin backfill endpoints require manager+admin PIN combo + per-call audit row signed by user_id; idempotency stamps already exist (`iter240_backfill_at`).

### Phase 3 — Cleanup & Deduplication

1. Remove `pages/SalesPage.js`; redirect `/sales` → `/sales-new` (user backlog).
2. Move root-level `backend_test.py`, `generate_test_report.py`, `generate_audit_report.py` to `backend/scripts/` or `scripts/`.
3. Consolidate uploads pages.
4. Move all transient one-shot backfill endpoints behind a dedicated `/api/admin/backfills/*` namespace + PIN.
5. Standardize date-field convention: every report-style endpoint declares which of `order_date | created_at | payment.date | closed_at | encoded_at` it uses, in code comments and docs.

### Phase 4 — Tests and Guardrails

Add focused tests for:
1. Cash sale deducts stock once.
2. Credit sale deducts stock once and increases AR; void reverses both.
3. Payment reduces AR but never touches stock; voided invoice rejects payment.
4. Sale void restores stock and reverses each `payment[]` from its source wallet.
5. Partial return restores only returned qty.
6. Backdated credit AFTER last_count_sheet allowed; BEFORE blocked unless admin approves.
7. Offline sync replay (same envelope_id × N) → exactly one inventory move.
8. Concurrent offline sync (parallel POSTs, same envelope_id) → exactly one inventory move (proves race).
9. Branch A user cannot read Branch B inventory.
10. Tenant A cannot read Tenant B data via any route.
11. Cashier cannot void/refund/backdate via direct API call without permission/PIN.
12. Invoice correction does not double-deduct.
13. Drafts and parked sales do not deduct inventory.
14. Returns do not duplicate refunds.
15. Repack: parent-stock movement balances child-stock derivation across N concurrent sales.
16. RMA generator under 50 concurrent return inserts produces 50 unique numbers.
17. `/api/sync/pos-data` does not return any plain-text PIN field (regression test for C-1).
18. `/api/setup/reset` returns 401/403 without super-admin auth (regression test for C-2).
19. `/api/admin-auth/setup-totp` rejects when super-admin has prior `last_admin_login` (regression test for C-3).
20. `customers.balance` ledger-reconciliation invariant after a randomized fuzzed sequence of (sale, payment, return, void, partial-payment, interest accrual).

---

## 20. Test Plan (Required After Fix)

Every Phase-1 fix must ship with:
- A regression test reproducing the original defect.
- A fuzz test that runs 100+ randomized sequences (sale → payment → return → void → ...) and asserts the ledger invariants.
- A sync-replay test with simulated network jitter (asyncio.gather of 10 retries with same envelope_id) asserting exactly-once inventory move.

---

**End of audit.** No code changes were applied. Awaiting prioritization decision before any implementation.

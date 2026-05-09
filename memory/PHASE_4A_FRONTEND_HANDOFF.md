# PHASE 4A — FRONTEND INTEGRATION HANDOFF

**Single source of truth** for the next fork that will continue Historical Credit Encoding by integrating it into `UnifiedSalesPage.js`. Read this top-to-bottom before touching any code.

---

## 1. Project / repo identity

| Item | Value |
|---|---|
| **App** | AgriBooks — multi-tenant POS / inventory / receivables for Philippine agri-supply stores |
| **Stack** | React (CRA + Tailwind + shadcn/ui) frontend, FastAPI (Python 3.11) backend, MongoDB (Motor async client) |
| **Frontend root** | `/app/frontend/` — entry: `src/index.js` → `src/App.js` |
| **Backend root** | `/app/backend/` — entry: `main.py` (FastAPI on `:8001`, all routes prefixed `/api`) |
| **Active sales page** | `/app/frontend/src/pages/UnifiedSalesPage.js` (5,488 lines — current production sales surface) |
| **Legacy sales page** | `/app/frontend/src/pages/SalesPage.js` (394 lines — kept as reference; do NOT extend) |
| **Phase 3 backend route** | `/app/backend/routes/historical_credit.py` |
| **Verify-policy module** | `/app/backend/routes/verify.py` |
| **Z-report queries** | `/app/backend/routes/daily_operations.py` (Z-report handler around lines 600-720) |
| **Test fixtures** | `/app/backend/tests/phase2b/_fixtures.py` |
| **Branch / git** | Local Emergent workspace commits only — never auto-pushed to GitHub. The user clicks "Save to GitHub" in the chat input when ready. |
| **Preview URL source** | `/app/frontend/.env` → `REACT_APP_BACKEND_URL` (do **not** trust any hardcoded URL from old handoffs) |
| **Test admin** | `test_org_admin@regression.local` / `RegressionPass!2026` — see `/app/memory/test_credentials.md` |

---

## 2. Completed phases (in order)

| Phase | Status | One-line summary |
|---|---|---|
| **1A** | ✅ Done | Cross-tenant security hardening (TenantCollection wrapper, fail-closed scoping). |
| **1B** | ✅ Done | Inventory-write hardening (atomic decrements, stock-movement audit). |
| **1C** | ✅ Done | Money / rounding fixes (consistent 2-decimal rounding across totals). |
| **2A** | ✅ Done | Customer Balance Reconciliation diagnostic — `GET /api/admin/customer-balance-reconciliation` + `BalanceReconciliationPage.js`. |
| **2B** | ✅ Done | POS Business Flow Verification Matrix — 33-test harness in `tests/phase2b/`. Generated `/app/memory/PHASE_2B_FLOW_MATRIX.md`. |
| **2C** | ✅ Done | POS Write-Side Hardening — payment idempotency, modify/void guards, credit-return invoice required, overpayment block (>₱0.50). |
| **2C.5** | ✅ Done | Frontend UX Compatibility Guards (ReturnRefundWizard, AccountingPage, CloseWizardPage). |
| **2D** | ✅ Done | Branch / Tenant Permission Hardening — H-5 fix in `assert_branch_access`, sync routes scoped to user branches, JWT org-mismatch reject. |
| **2D.5** | ✅ Done in workspace | Terminal POS thermal-receipt CSS fix (qty/price 11→12px, dashed item-separator, looser padding) — pending physical H10P device validation. |
| **2E** | ✅ Done | Report Date-Basis Standardisation — `utils/date_basis.py`, `transaction_date` / `encoded_today` enrichments, new `GET /api/reports/encoded-today`. Matrix at `/app/memory/PHASE_2E_DATE_BASIS_MATRIX.md`. |
| **3** | ✅ Done | Historical Credit Encoding / Notebook AR backend — `routes/historical_credit.py` with `/preview`, commit, list endpoints. Spec at `/app/memory/PHASE_3_HISTORICAL_CREDIT.md`. |
| **4A backend** | ✅ Done | Approval-gate backend wiring — see §4 below. |
| **4A frontend** | ⏳ **NOT STARTED** | Scope of this handoff — see §7 below. |

---

## 3. Current test status

| Suite | Count | Status |
|---|---|---|
| Phase 1A `test_phase1a_security.py` | — | All pass |
| Phase 1B `test_phase1b_inventory.py` | — | All pass |
| Phase 1C `test_phase1c_money.py` | — | All pass |
| Phase 2A `test_phase2a_balance_recon.py` | — | All pass |
| Phase 2B `tests/phase2b/` | 33 (2 intentional skips) | 31 pass + 2 skip |
| Phase 2C `test_phase2c_pos_hardening.py` + `test_phase2c_live_smoke.py` | — | All pass |
| Phase 2D `test_phase2d_permissions.py` | 17 | 17 pass |
| Phase 2E `test_phase2e_date_basis.py` | 10 | 10 pass |
| **Phase 3 `test_phase3_historical_credit.py`** | **13** | **13 pass** (every commit-path test seeds a TOTP admin and submits a fresh `pyotp.TOTP(secret).now()` code) |
| **Phase 4A `test_phase4a_approval_gate.py`** | **12** | **12 pass** |
| **Combined total in isolation** | **138 pass + 2 skipped + 0 fail** | ✅ |

### Intentional skips (Phase 2B)
2 tests in `tests/phase2b/` are marked skip — documented in their docstrings as deferred sub-features. They have always been skipped, never failed.

### Pre-existing intermittent flakiness (out of scope for next fork)
When the **full** suite is run end-to-end, ~1 in 3 runs has 1 (rarely 2) phase2b/2D/2E tests fail. The **specific test** that fails varies between runs — consistent with shared-state leak, not a real bug. **Confirmed reproducible without Phase 3 or Phase 4A in the suite** (i.e. it predates this work). Each failing test passes 100% of the time in isolation. Root cause: pytest-asyncio `session`-scoped event loop + shared MongoDB state lets one test's `set_org_context` briefly leak into the next test's setup window. Documented at `/app/memory/PHASE_3_HISTORICAL_CREDIT.md` §14. **The next fork must not chase this flakiness — it is a Phase 4 cleanup item.**

### 11 hardcoded-date legacy unit-test failures (out of scope)
Pre-existing failures in legacy test files that hardcode month-boundary dates. They have been documented as out-of-scope across the last several phases. The next fork must not attempt to fix them.

---

## 4. Phase 4A backend approval gate — what it now does

### Files actually changed in Phase 4A backend
- `/app/backend/routes/verify.py` (extended event-config shape, new `_verifier_role` helper, role/user allow-list filter inside `verify_pin_for_action`).
- `/app/backend/routes/historical_credit.py` (commit endpoint now requires `approval_code` and routes through the new gate before any DB mutation).
- `/app/backend/tests/phase2b/_fixtures.py` (new helpers `seed_totp_admin`, `seed_manager_totp`, `seed_admin_pin`).
- `/app/backend/tests/test_phase3_historical_credit.py` (every commit-path test now provides a fresh TOTP code).
- `/app/backend/tests/test_phase4a_approval_gate.py` (NEW — 12 tests).

### `verify.py` — new event registration
```python
{"key": "historical_credit_encoding",
 "label": "Historical Credit Encoding (Notebook AR)",
 "module": "Sales",
 "defaults": ["totp"],
 "allowed_approver_roles": ["owner", "admin", "super_admin"],
 "allowed_approver_user_ids": []}
```

### `verify.py` — backwards-compatible shape change
- `_ACTION_CONFIG` lookup added (full per-event row).
- `_verifier_role(verifier_id)` helper — canonical role mapping; `system_admin` (the static admin-PIN verifier) maps to `"admin"`.
- `verify_pin_for_action` now applies the optional `allowed_approver_roles` and `allowed_approver_user_ids` filters AFTER `_resolve_pin` returns. Events without those keys behave exactly as before — fully backwards-compatible for all 50+ existing events.

### Approval defaults (Historical Credit Encoding)
- `defaults=["totp"]` — only the per-user Authenticator App from Settings → Security is accepted.
- `allowed_approver_roles=["owner","admin","super_admin"]` — manager TOTP **rejected**.
- `manager_pin` **rejected** (not in defaults).
- Static `admin_pin` **rejected** (not in defaults).
- `allowed_approver_user_ids=[]` — future per-user grant slot, populated via `system_settings.pin_policies` override; **no code change needed** to grant a specific trusted manager later.

### `routes/historical_credit.py` commit flow
1. `assert_admin_or_owner(user)` (JWT auth gate, unchanged from Phase 3)
2. `_validate_payload(data, today)` (reason ≥ 20 chars, transaction_date in past, etc.)
3. `assert_branch_access(user, branch_id)`
4. Customer / branch-binding checks
5. **NEW:** require `approval_code` in body → 400 `approval_code_required` if missing.
6. **NEW:** `verify_pin_for_action(approval_code, "historical_credit_encoding", branch_id=...)` → 403 `approval_invalid` if returns None.
7. Count-sheet stopper logic (unchanged)
8. Insert invoice with full audit envelope:
   - `source="historical_credit_encoding"`, `late_encoded=true`
   - `late_encoded_by`, `late_encoded_by_name` (the encoder)
   - `approved_by`, `approved_by_name`, `approved_at` (the verifier — may differ from encoder)
   - `approval_method` (`"totp"` / `"admin_pin"` / `"manager_pin"` / etc. — what method actually succeeded)
   - `approver_role` (`"owner"` / `"admin"` / `"super_admin"` / etc.)
9. Increment `customer.balance` (unchanged)
10. Apply (or skip) inventory deduction per stopper (unchanged)
11. Write `late_encode_log` row + `security_events` row — both now carry `approval_method` and `approver_role`.
12. Response includes an `approval` block:
    ```json
    {"approval": {"method": "totp", "approver_id": "...", "approver_name": "...", "approver_role": "owner"}}
    ```

### Where the approval gate is verified server-side
- `pyotp.TOTP(secret).verify(code, valid_window=1)` inside `verify.py`'s existing `_resolve_pin` TOTP branch.
- 30-second window with ±1 step tolerance.
- Frontend NEVER sees the secret or hash.

---

## 5. Seven invariants confirmed (verbatim)

1. **Normal 7-day late-encode credit flow remains unchanged and can still use existing manager/admin approval.**
2. **Only backdated credit outside the normal late-encode window should enter Historical Credit Mode.**
3. **Historical Credit Mode requires Owner/Admin TOTP, not generic manager PIN.**
4. **Historical Credit entries appear in encoded-today / AR encoded today reports with transaction_date and encoded_at shown separately.**
5. **Historical Credit entries must not alter old closed Z-reports.**
6. **Today's Z-report may show them only in late-encoded / encoded-today credit section, not as cash collected.**
7. **Cash collected today remains unchanged unless a separate payment is recorded.**

Each invariant is grounded in code:

| # | Verified at | Test that proves it |
|---|---|---|
| 1 | `routes/sales.py:44-121` (untouched by Phase 4A); `verify.py:215` `transaction_verify` event keeps `defaults=["admin_pin","manager_pin","totp","auditor_pin"]`. | Existing Phase 2C `test_phase2c_pos_hardening.py` regression. |
| 2 | Two physically separate routes: `/api/unified-sale` enforces 7-day cap (sales.py:79-83); `/api/historical-credit` is a separate admin/owner-only route. | The frontend will route by `daysBack > 7`. |
| 3 | `verify.py` `historical_credit_encoding` event with `defaults=["totp"]` + role allow-list `["owner","admin","super_admin"]`. | `test_phase4a_approval_gate::test_manager_totp_rejected_by_role_allow_list`, `::test_static_manager_pin_rejected`. |
| 4 | Phase 2E `utils/date_basis.py` enrichment + Phase 3 invoice tags `source`, `late_encoded`, separate `order_date` and `late_encoded_at`. | `test_phase3_historical_credit::test_appears_in_encoded_today_and_ledger`, `::test_transaction_date_and_encoded_at_separate`. |
| 5 | `routes/daily_operations.py` Z-report queries (around lines 619-635) carry `"source": {"$ne": "historical_credit_encoding"}`. | `test_phase3_historical_credit::test_old_zreport_regular_section_unaffected`. |
| 6 | Historical credit invoices are inserted with `payment_type="credit"`, `amount_paid=0`, `payments=[]`, `late_encoded=true`. Today's Z-report cash sums `payments[i].date == today AND voided != true` — historical credit contributes zero. | Same Phase 3 test ensures the invoice does not appear in regular cash/credit section of the Z-report. |
| 7 | No code path in `routes/historical_credit.py` writes to `payments[]`, calls `update_cashier_wallet`, or inserts to `cash_movements`. To collect cash, the user must call existing `POST /api/invoices/{id}/payment` separately. | `test_phase3_historical_credit::test_customer_balance_goes_up_by_grand_total` plus the data-shape contract. |

---

## 6. Important pending design decision — backend soft floor

### Current state (today)
The backend `/api/historical-credit` endpoint accepts **any past date** as long as `transaction_date != today` and the admin has a valid TOTP code. There is no backend enforcement of the "outside the 7-day window" boundary — that boundary is currently a frontend convention only.

### Recommendation BEFORE frontend integration
Add a backend soft floor to `routes/historical_credit.py` so the rule is data-enforced, not UI-enforced:

```python
# In _validate_payload, after parsing transaction_date:
days_back = (date.today() - parsed_transaction_date).days
if days_back <= 7:
    raise HTTPException(
        status_code=400,
        detail={
            "error": "use_regular_late_encode",
            "message": (
                "Use the regular Sales late-encode path for dates within "
                "the normal late-encode window (0-7 days back). "
                "Historical Credit / Notebook AR is for older entries only."
            ),
            "days_back": days_back,
        },
    )
```

### What this preserves
- **0–7 days backdated credit** = normal `/api/unified-sale` late-encode flow (manager PIN OK).
- **>7 days backdated credit** = Historical Credit / Notebook AR via `/api/historical-credit` (Owner/Admin TOTP only).
- A careless admin can no longer accidentally route a 2-day-old credit through the heavier-weight Historical Credit channel.

### Action for the next fork
Implement this BEFORE frontend integration **unless the owner explicitly says otherwise**. One tiny backend test case:
- `transaction_date = days_ago(3)` → `/api/historical-credit` returns 400 `use_regular_late_encode`.
- `transaction_date = days_ago(10)` → still works (existing path).

---

## 7. Phase 4A frontend integration — scope

### What to extend
- **`/app/frontend/src/pages/UnifiedSalesPage.js`** — extend in place. The active production Sales surface.
- **Do NOT** build a separate Historical Credit page unless extending UnifiedSalesPage proves technically unsafe; in that case, stop and explain why before coding.
- **Do NOT** touch `SalesPage.js` (legacy stub).
- **Do NOT** touch POS Terminal, offline sync, returns, payments, or reports.
- **Do NOT** start the broader Phase 4 cleanup or `/sales` vs `/sales-new` dedupe.

### Backend endpoints the frontend must use
| Step | Endpoint | Purpose |
|---|---|---|
| Preview | `POST /api/historical-credit/preview` | Dry-run with count-sheet stopper + effects. Read-only. |
| Commit | `POST /api/historical-credit` | Final commit; requires `approval_code`. |
| List (optional) | `GET /api/historical-credit?branch_id=&customer_id=&limit=` | Reference / audit list. |
| **Do NOT** use `POST /api/unified-sale` for historical credit commits. | | |

### Mode trigger
Switch into BACKDATED CREDIT / NOTEBOOK AR mode only when **all three** are true:

```
transaction_date < today
  AND payment_type == "credit"
  AND daysBack > 7
```

Outside that combination, the page must behave exactly as it does today.

### What must be blocked
- Backdated **cash** sale → block with: *"Backdated transactions are allowed for CREDIT only. Cash, digital, split, or paid transactions must be recorded on the actual payment date."*
- Backdated **digital** sale → same block.
- Backdated **split** sale → same block.
- Backdated **fully-paid** sale → same block.
- 0–7 day backdated credit → keep using the existing 7-day late-encode flow (already implemented in UnifiedSalesPage, do NOT replace it).

### Final commit gate
- Commit button stays disabled until: preview exists + reason ≥ 20 chars + payment_type == credit + daysBack > 7 + `approval_code` field is filled.
- POST to `/api/historical-credit` with `{...payload, approval_code}`.
- Show clear errors on 400 `approval_code_required` and 403 `approval_invalid`.
- **Do NOT** verify the code locally. **Do NOT** call any TOTP library on the frontend.
- **Do NOT** expose, log, or persist the approval code, TOTP secret, or hash on the frontend.

---

## 8. Required UI elements

| Element | Spec |
|---|---|
| **Banner** | "BACKDATED CREDIT / NOTEBOOK AR MODE" — visible only in mode. Body text: *"You are encoding an old credit transaction. This will be recorded as historical credit / AR reconstruction. It will not be treated as cash collected today and will not modify old closed Z-reports. Company Owner/Admin Authenticator App (TOTP) approval is required."* |
| **Reason input** | Required, minimum 20 characters. Placeholder example: *"Notebook AR carry-forward verified against ledger page 12, customer countersigned 2026-02-04."* |
| **Proof / reference field** | Optional `proof_url` (URL string) AND optional `notebook_reference` (free text). |
| **Preview panel** | After clicking Preview, render result of `POST /api/historical-credit/preview`. Show: customer, branch, transaction_date, encoded-today date, items / amount, count-sheet stopper result, inventory action, report effect block, audit log block. |
| **Customer Owes Total Snapshot** | `current balance + historical credit amount = projected balance`. Visually highlight when `historical credit amount > 50% of current balance` or `> ₱5,000` (whichever first). |
| **Count-sheet stopper warning** | If preview returns `inventory_action == "skipped_count_sheet_lock"`: *"This date is on or before the latest approved count sheet. Inventory will not be deducted unless Admin/Owner explicitly allows it. This will be recorded as AR-only reconstruction."* with an opt-in checkbox to set `allow_inventory_deduction=true`. |
| **Closed-day warning** | If the preview indicates the date is a closed business day: *"This is a closed business day. This entry will not modify the old closed Z-report. It will be recorded as encoded today with the original transaction date shown separately."* |
| **Final confirmation modal** | Re-show all preview info + add `approval_code` input (6-digit, numeric, autoFocus, autocomplete=off). Final warning text: *"You are about to create a backdated CREDIT transaction for AR reconstruction. This will not be treated as cash collected today and will not alter old closed Z-reports."* |
| **Success result** | After commit success, clearly mark the resulting invoice as "Historical Credit / AR Reconstruction" — include `transaction_date`, `encoded_at`, the approver's name, and the audit log reference. |
| **`data-testid`** | Every interactive / status element MUST have a kebab-case `data-testid`. Examples: `historical-credit-banner`, `historical-credit-reason-input`, `historical-credit-proof-url-input`, `historical-credit-preview-btn`, `historical-credit-customer-owes-snapshot`, `historical-credit-approval-code-input`, `historical-credit-commit-btn`, `historical-credit-success-result`. |

---

## 9. Required tests / checks for the next fork

The next fork must verify each of the following before reporting done. Use `testing_agent_v3_fork` for the frontend flows; backend cases can be unit-tested or curl-tested.

### Functional behaviour
1. Today's normal cash sale still works (no regression in regular flow).
2. Today's normal credit sale still works.
3. Backdated credit within 7 days still uses the normal `/api/unified-sale` late-encode flow (NOT historical credit).
4. Backdated credit > 7 days correctly enters Historical Credit Mode and posts to `/api/historical-credit`.
5. Backdated **cash** sale is blocked with the canonical warning.
6. Backdated **digital** sale is blocked.
7. Backdated **split** sale is blocked.
8. Backdated **fully-paid** sale is blocked.
9. Missing reason blocks preview AND commit.
10. Missing `approval_code` blocks commit (400 surfaced as a clear UI error).
11. Wrong / expired `approval_code` blocks commit (403 surfaced as a clear UI error).
12. Valid Owner/Admin TOTP commits successfully and shows the success result.
13. Manager TOTP does NOT commit unless their user-id is explicitly in `allowed_approver_user_ids` (verified server-side; UI just surfaces the 403).
14. Historical credit commit posts to `/api/historical-credit` and never to `/api/unified-sale`.
15. Normal sales continue to post to `/api/unified-sale`.

### Surface protection
16. POS Terminal flows (online + offline → sync) remain unaffected — verify by running an existing terminal smoke (or skip terminal testing if not feasible in the preview environment).
17. Quick POS / Advanced POS regression — confirm by running a today cash sale + a today credit sale.

### Security
18. TOTP secret / PIN / hash never appear in any network response or any frontend log. Inspect `/api/auth/me` payload + the historical-credit success response.

---

## 10. Out of scope for the next fork (do NOT touch)

- Phase 4 cleanup (consolidating `assert_branch_access` + `get_branch_filter`, etc.).
- `/sales` vs `/sales-new` dedupe.
- Report date-basis changes (Phase 2E is locked).
- A new manager request queue.
- A separate Historical Credit page (only if UnifiedSalesPage extension is unsafe — explain why first).
- A trusted-manager approval admin UI (the policy is data-driven via `system_settings.pin_policies`; UI is a future task).
- Physical H10P print test (pending hardware).
- The 11 legacy hardcoded-date test failures.
- Production data mutation of any kind.
- Pushing to GitHub (the user clicks "Save to GitHub" themselves).

---

## 11. Smoke commands for the next fork

### Backend regression (Phase 3 + Phase 4A targeted)
```bash
cd /app/backend && python3 -m pytest \
  tests/test_phase3_historical_credit.py \
  tests/test_phase4a_approval_gate.py -v
# Expected: 25 passed
```

### Backend regression (combined — expect occasional pre-existing flakiness)
```bash
cd /app/backend && python3 -m pytest \
  tests/test_phase1a_security.py \
  tests/test_phase1b_inventory.py \
  tests/test_phase1c_money.py \
  tests/test_phase2a_balance_recon.py \
  tests/test_phase2c_pos_hardening.py \
  tests/test_phase2d_permissions.py \
  tests/test_phase2e_date_basis.py \
  tests/test_phase3_historical_credit.py \
  tests/test_phase4a_approval_gate.py \
  tests/phase2b/
# Expected: 138 passed + 2 skipped in isolation.
# On ~1 of 3 runs, 1 pre-existing flake may fail (different test each time).
# This is documented as out of scope.
```

### Backend lint
```bash
ruff check /app/backend/routes/ /app/backend/utils/ /app/backend/tests/
```

### Frontend lint
```bash
cd /app/frontend && yarn lint
# OR via the agent:
# mcp_lint_javascript on /app/frontend/src/pages/UnifiedSalesPage.js
```

### Live API smoke
```bash
API_URL=$(grep REACT_APP_BACKEND_URL /app/frontend/.env | cut -d= -f2 | tr -d '\n')
TOKEN=$(curl -s -X POST "$API_URL/api/auth/login" -H "Content-Type: application/json" \
  -d '{"email":"test_org_admin@regression.local","password":"RegressionPass!2026"}' \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['token'])")

# Phase 4A endpoints reachable
curl -s -o /dev/null -w "preview empty body: %{http_code}\n" \
  -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{}' "$API_URL/api/historical-credit/preview"
# Expected: 400

curl -s -o /dev/null -w "commit empty body:  %{http_code}\n" \
  -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{}' "$API_URL/api/historical-credit"
# Expected: 400

curl -s -o /dev/null -w "list:               %{http_code}\n" \
  -H "Authorization: Bearer $TOKEN" "$API_URL/api/historical-credit"
# Expected: 200

# Surface protection
curl -s -o /dev/null -w "/reports/sales:     %{http_code}\n" \
  -H "Authorization: Bearer $TOKEN" "$API_URL/api/reports/sales"
# Expected: 200

curl -s -o /dev/null -w "/sync/pos-data:     %{http_code}\n" \
  -H "Authorization: Bearer $TOKEN" "$API_URL/api/sync/pos-data"
# Expected: 200
```

### UnifiedSalesPage smoke (manual, post-implementation)
1. Log in as `test_org_admin@regression.local`.
2. Open Sales page (UnifiedSalesPage).
3. Pick a customer, add 1 item, payment type = Credit, transaction_date = today → confirm posts to `/api/unified-sale`. ✅
4. Same but transaction_date = 3 days ago → confirm uses the existing 7-day late-encode flow (manager PIN modal). ✅
5. Same but transaction_date = 14 days ago → confirm enters Historical Credit Mode banner; reason input appears; preview button calls `/api/historical-credit/preview`; final modal asks for `approval_code`; commit posts to `/api/historical-credit`. ✅
6. Try transaction_date = 14 days ago + payment type = Cash → confirm blocked with the canonical warning. ✅

---

## 12. Final instruction for the next fork

> **Read this handoff first.** Run smoke baseline (§11). If baseline is **not** green, stop and report. If green, **first implement the backend soft floor** for `/historical-credit` within 7 days (§6) unless the owner explicitly says not to. Then proceed with **UnifiedSalesPage frontend integration only** (§7, §8). **Stop after Phase 4A frontend integration and report.** Do not proceed to Phase 4 cleanup, do not push to GitHub, do not touch out-of-scope items in §10.

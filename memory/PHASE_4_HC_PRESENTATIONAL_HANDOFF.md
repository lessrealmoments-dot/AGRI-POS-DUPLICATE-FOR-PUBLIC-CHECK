# Phase 4 Cleanup — Historical Credit Presentational Extraction Handoff

> **This document is the single source of truth for the next fork agent.**
> Read top-to-bottom before running anything.

---

## 1. Project / repo identity

| | |
|---|---|
| **Product** | AgriBooks — multi-tenant Filipino agri-supply POS + back-office (React + FastAPI + MongoDB). |
| **Repo root** | `/app` |
| **Frontend** | React (CRA / craco), Tailwind, shadcn/ui, sonner toasts, `@testing-library/react@^16` for hook tests. Build/test via `yarn`. |
| **Backend** | FastAPI + Motor (MongoDB). Pytest with pytest-asyncio. `pyotp` for TOTP. |
| **Active POS page** | `frontend/src/pages/UnifiedSalesPage.js` (~5,960 lines). Served at `/sales-new`, `/pos`, `/sales-order`. |
| **Quick Sale vs Detailed Sale** | **Two MODES inside the same `UnifiedSalesPage`** — switched via the `mode` state (`'quick'` vs `'order'`). They share cart/customer/branch/payment infrastructure. They are NOT separate POS systems and MUST stay inside the same page. |
| **POS Terminal** | `frontend/src/pages/terminal/*.jsx` (~5,000 lines, hardware-terminal stack). **Out of scope.** Verify it remains unaffected; do not touch. |
| **Sales History page** | `frontend/src/pages/SalesPage.js` at route `/sales` — list/filter of invoices, NOT a POS. Out of scope. |
| **Critical reference files** | `frontend/src/lib/useHistoricalCredit.js`, `frontend/src/lib/useConnectivity.js`, `frontend/src/lib/connectivity.js`, `frontend/src/lib/offlineDB.js`, `frontend/src/lib/syncManager.js`, `frontend/src/lib/dateFormat.js`, `frontend/src/components/LateEncodeDialog.js`, `backend/routes/historical_credit.py`, `backend/routes/verify.py`. |

---

## 2. Completed phases / status

| Phase | What it covered | Status |
|---|---|---|
| **1A / 1B / 1C** | Logic/connection/security audit + initial repairs. | ✅ Done. |
| **2A / 2B / 2C / 2C.5** | Tenant isolation, payment idempotency (`envelope_id`), inventory hardening. | ✅ Done. |
| **2D** | Branch/tenant permission hardening (`assert_branch_access`). | ✅ Done. |
| **2D.5** | Terminal POS built-in printer receipt layout hotfix. | ✅ Done. |
| **2E** | Report date-basis standardisation (`transaction_date` vs `late_encoded_at` vs `encoded_today`). | ✅ Done. |
| **3** | Historical Credit / Notebook AR **backend** (`POST /api/historical-credit{,/preview}`). | ✅ Done. |
| **4A backend** | Approval gate via `routes/verify.py` policy engine — `allowed_approver_roles`, TOTP-only for `historical_credit_encoding`. | ✅ Done. |
| **4A frontend** | Historical Credit Mode integrated into `UnifiedSalesPage` (banner + commit dialog with TOTP input, soft floor enforced). | ✅ Done. |
| **4A.1** | Online/offline routing stabilisation — error classifier, 10s reconnect grace, 3-state indicator, `/api/health` probe. | ✅ Done. |
| **4A.1.1** | Read-only "N sales waiting to sync" pill. | ✅ Done. |
| **4 cleanup Pass 0** | Dedupe-pill repositioned out of cart-Checkout overlap. All-Branches checkout guard made clearer (rule preserved). | ✅ Done. |
| **4 cleanup Pass 1** | `useConnectivity()` hook extracted into `lib/useConnectivity.js`. | ✅ Done. |
| **4 cleanup useHistoricalCredit** | Hook extracted into `lib/useHistoricalCredit.js` with `onCommitted` callback boundary. Page-local `localToday()` → shared `lib/dateFormat.js#localTodayStr`. | ✅ Done — latest. |

---

## 3. Current test baseline

| Suite | Count | Status |
|---|---|---|
| `frontend/src/lib/connectivity.test.js` | 17 | ✅ |
| `frontend/src/lib/useConnectivity.test.js` | 6 | ✅ |
| `frontend/src/lib/useHistoricalCredit.test.js` | 14 | ✅ |
| **Frontend unit total** | **37 / 37** | **PASS** |
| `backend/tests/test_phase3_historical_credit.py` | 16 | ✅ |
| `backend/tests/test_phase4a_approval_gate.py` | 12 | ✅ |
| **Backend regression total** | **28 / 28** | **PASS** |

**Live smoke (most recent)**: `https://bto-phase-rollout.preview.emergentagent.com/sales-new` mounts cleanly. `[data-testid='unified-sales-page']` present, `[data-testid='connectivity-status'][data-status='online']` visible, dedupe pill in bottom-left, no `pageerror`s, no console errors. Both Quick Sale and Detailed Sale tabs render.

**Known out-of-scope flakiness (DO NOT FIX in this fork)**:
- 11 pre-existing hardcoded-date unit-test failures in older test files (requires `freezegun` rework).
- Pytest-asyncio shared-state flakiness — ~1 in 3 full-suite runs has 1 phase2b/2D/2E test fail due to shared DB state between sessions. Tests pass cleanly in isolation. Listed for a future cleanup, not for this fork.

---

## 4. Latest completed pass — `useHistoricalCredit` extraction

### 4.1 Files changed
- **NEW** `frontend/src/lib/useHistoricalCredit.js` (~240 lines).
- **NEW** `frontend/src/lib/useHistoricalCredit.test.js` (14 tests, all green).
- **MOD** `frontend/src/pages/UnifiedSalesPage.js` — imports the hook, removed ~210 lines (12 HC state fields + 2 trigger memos + 4 helpers + page-local `localToday()`), wired `hc = useHistoricalCredit({...})`, migrated all consumer sites to `hc.*`.

### 4.2 State moved into `useHistoricalCredit`
`reason`, `proofUrl`, `notebookRef`, `allowInv`, `approvalCode`, `dialogOpen`, `preview`, `previewLoading`, `previewError`, `committing`, `commitError`, plus the constant `HISTORICAL_CREDIT_FLOOR_DAYS = 7`, plus the memoised flags `isMode` (= `isHistoricalCreditMode`) and `isBackdatedNonCreditBlocked`.

### 4.3 Functions moved into the hook
- `buildItems` — mode-aware cart→items mapper (closed over `mode`, `cart`, `lines`).
- `buildPayload` — preview/commit-shared, approval-code-aware (omits the code on preview).
- `runPreview` (was `previewHistoricalCredit`).
- `commit` (was `commitHistoricalCredit`).
- **New conveniences**: `openDialog()`, `closeDialog()`, `reset()`. The first two preserve the user-typed reason/proof/notebook on open/close.

### 4.4 What remains inside `UnifiedSalesPage.js`
- `isPrivilegedRole` and `daysBack` (used by non-HC code paths too — passed INTO the hook as options).
- All cart / lines / customer / branch / header / totals state.
- All HC JSX (`historical-credit-banner`, `backdated-non-credit-block`, `historical-credit-dialog`) — read from `hc.*` for now.
- Page-owned setters `clearCart`, `setHeader`, `setPendingCreditSale`, `setCheckoutDialog` — only invoked via `onCommitted`.

### 4.5 `onCommitted` wiring
```js
onCommitted: useCallback(() => {
  clearCart();
  setHeader(h => ({ ...h, order_date: localTodayStr() }));
  setPendingCreditSale(null);
  setCheckoutDialog(false);
}, []),
```
Hook never touches these page setters directly. Errors thrown from `onCommitted` are swallowed so the cashier still sees the green toast.

### 4.6 `localTodayStr` cleanup
Page-local `localToday()` helper (~20 lines) replaced with `localTodayStr` imported from `lib/dateFormat.js`. 9 call-sites migrated. Same `agribooks.org_tz` localStorage read, same Manila fallback, same YYYY-MM-DD output. `lib/nextOpenDate.js` still owns a parallel copy — flagged for a future tiny cleanup; **DO NOT touch in this fork**.

### 4.7 Behavior unchanged — confirmed
Same HC trigger conditions (`daysBack > 7` AND `paymentType === 'credit'` AND privileged role), same backdated-non-credit block, same approval-code rules, same Owner/Admin TOTP gate (server-side), same `use_regular_late_encode` routing for 1–7 day credit, same 0–7 day late-encode flow via `LateEncodeDialog`, same post-commit cleanup. Quick Sale and Detailed Sale modes both preserved inside `UnifiedSalesPage`. POS Terminal / offline sync / reports / pending-sync pill / Phase 4A.1 connectivity behavior all untouched.

---

## 5. Current structure after the extraction

| Responsibility | Lives in |
|---|---|
| Cart / lines / mode / customer / branch / header / totals | `UnifiedSalesPage.js` (page-owned) |
| Connectivity 3-state indicator + 10s reconnect grace + 30s `/api/health` probe + auto-sync wiring + offline-queue size | `lib/useConnectivity.js` |
| Pending-sync count + pill | `lib/useConnectivity.js` (state) + `UnifiedSalesPage.js` (JSX render of pill) |
| Historical Credit state / trigger flags / preview / commit | `lib/useHistoricalCredit.js` |
| `LateEncodeDialog` | `components/LateEncodeDialog.js` (already extracted, generic, reused) |
| **Historical Credit banner JSX** (red `backdated-non-credit-block` + amber `historical-credit-banner` with reason/proof/notebook inputs) | **Still inline in `UnifiedSalesPage.js`** — next-pass candidate. |
| **Historical Credit dialog JSX** (preview, owner-owes snapshot, count-sheet stopper, closed-day note, TOTP input, commit button) | **Still inline in `UnifiedSalesPage.js`** — next-pass candidate. |
| Checkout dialog + payment tabs | Inline (not yet extracted — final pass) |
| `processSale` | Inline (not yet extracted) |
| HeldSalesQueue (Phase 4A.2) | **Not yet built.** |

---

## 6. Next proposed pass — Historical Credit JSX presentational extractions

**Goal**: extract the two large inline JSX blocks into their own presentational components that consume the already-created `hc` object from `useHistoricalCredit`.

### Components to create

1. **`frontend/src/components/HistoricalCreditBanner.jsx`** (~110 lines moved out).
   - Renders the red `backdated-non-credit-block` banner (when `isBackdatedNonCreditBlocked`).
   - Renders the amber `historical-credit-banner` with reason textarea / proof URL input / notebook reference input (when `isHistoricalCreditMode`).
   - Already self-contained at lines ~3567 – ~3680 of `UnifiedSalesPage.js`.

2. **`frontend/src/components/HistoricalCreditDialog.jsx`** (~210 lines moved out).
   - Renders the `[data-testid='historical-credit-dialog']` `<Dialog>` block.
   - Customer-owes snapshot, count-sheet stopper warning + opt-in checkbox, closed-day note, TOTP input, commit button.
   - Already self-contained at lines ~5790 – ~5995 of `UnifiedSalesPage.js`.

### Constraints
- **Presentational only.** Both components must be pure renders of props.
- **Consume the existing `hc` object** from `useHistoricalCredit` — do NOT recreate any state, helper, or API call.
- **No business logic changes.** Banner triggers, approval rules, endpoint paths, TOTP gate — all stay identical.
- **No new endpoints.** The hook still owns `runPreview` / `commit`.
- **Preserve every existing `data-testid`** so the existing testing-agent assertions and the 14 hook tests stay green.

### Suggested prop signatures (next fork should validate against the actual JSX)
```jsx
<HistoricalCreditBanner
  enabled={isHistoricalCreditMode}
  blocked={isBackdatedNonCreditBlocked}
  daysBack={daysBack}
  hc={hc}                       // reason/proofUrl/notebookRef + setters
/>

<HistoricalCreditDialog
  hc={hc}                       // dialogOpen, preview, approvalCode, commit, etc.
  customer={selectedCustomer}
  branch={currentBranch}
  totals={{ subtotal, freight, overallDiscount, grandTotal }}
  daysBack={daysBack}
  itemsCount={items.length}
  orderDate={header.order_date}
/>
```

### Tests required
- Add (or extend) tests:
  - `components/HistoricalCreditBanner.test.jsx` — RTL snapshot: with `enabled=true`, banner appears and 3 inputs render; with `blocked=true`, the red banner appears instead; with both false, nothing renders.
  - `components/HistoricalCreditDialog.test.jsx` — RTL: dialog renders when `hc.dialogOpen=true`, snapshot panel reads from `hc.preview`, commit button is disabled until `approvalCode` is set + preview exists.
- Existing 37/37 unit tests must remain green.
- Backend 28/28 regression must remain green.
- Live smoke: page mounts, both modes work, dedupe pill still in bottom-left, no `pageerror`s.

---

## 7. Existing reusable pieces / no-duplicate rule

**Must reuse, MUST NOT recreate**:
- `useHistoricalCredit` (already in `lib/useHistoricalCredit.js`) — banner and dialog must read from this hook.
- `useConnectivity` (already in `lib/useConnectivity.js`) — do not duplicate connectivity state.
- `LateEncodeDialog` (already in `components/LateEncodeDialog.js`) — generic, NOT a duplicate of the HC dialog. Do not recreate.
- `connectivity.js`, `syncManager.js`, `offlineDB.js`, `dateFormat.js` — all already shared modules.
- `CropCreditTypeDialog`, `RequestSignatureDialog`, `GlobalPriceBadge`, `CustomerBalanceBadge`, `CalcInput`, `SmartProductSearch`, `UnclosedDaysBanner`, `PriceMatchModal`, `ReferenceNumberPrompt` — all already shared components.

**Must NOT create**:
- A separate Historical Credit page (it stays inside `UnifiedSalesPage`).
- A duplicate late-encode dialog.
- A duplicate connectivity hook.
- A parallel HC helper / payload builder / preview / commit function.
- A new sales sub-page or duplicate route.

---

## 8. Required assessment for the next fork

Before writing any code, the next fork **must** produce a written assessment that confirms:

1. Whether `HistoricalCreditBanner.jsx` and `HistoricalCreditDialog.jsx` are still the safest next pass given the current state of the file.
2. Whether any reusable component already exists for either (grep `components/` for "historical", "credit", "backdated", "notebook" before creating).
3. Whether the current inline JSX can be moved as-is without changing behavior (verify every `data-testid`, every conditional, every prop spread).
4. Whether both should be extracted together or one at a time. Recommendation criteria: shared dependencies, regression surface, test-iteration cost.
5. Exact prop signatures (read the actual JSX, list each `hc.*` reference and each page-state reference).
6. Risks (closures, stale `hc.*` references, prop drilling, missing testid coverage) and mitigations.
7. Test plan (RTL components, regression suites to run, live smoke).
8. Explicit confirmation that no duplicates will be created.

The assessment must use the same four-heading format used previously:
```
A. Current Implementation Map
B. Existing Reusable Pieces
C. Duplicate / Overlap Findings
D. Recommended Slice
E. Final Verdict (1/2/3/4)
```

---

## 9. Constraints (DO NOT do in this fork)

Do NOT:
- change Historical Credit business behavior
- change Owner/Admin TOTP approval rules
- change the 0–7 day late-encode behavior
- change normal today-sale behavior
- change Quick Sale / Detailed Sale mode behavior or move either out of `UnifiedSalesPage`
- change connectivity behavior (3-state indicator, 10s grace, 30s probe)
- change the pending-sync pill
- touch POS Terminal (`pages/terminal/*`)
- touch offline sync (`lib/syncManager.js`)
- touch reports
- extract `CheckoutDialog` yet
- extract `PaymentTabs` yet
- extract `processSale` yet
- build `HeldSalesQueue` yet
- add an SMS-receipt toggle yet
- rename `/sales` route yet
- dedupe `lib/nextOpenDate.js#localTodayStr` (already noted; out of scope for this fork)

---

## 10. Smoke commands for the next fork

Run these in this exact order at the start of the fork.

### 10.1 Frontend lint
```bash
cd /app/frontend && yarn eslint src/pages/UnifiedSalesPage.js src/lib/useHistoricalCredit.js src/lib/useConnectivity.js
```

### 10.2 Frontend unit tests (must be 37/37 green)
```bash
cd /app/frontend && CI=true yarn test --watchAll=false --testPathPattern="connectivity|useConnectivity|useHistoricalCredit"
```

### 10.3 Backend Phase 3 + 4A regression (must be 28/28 green)
```bash
cd /app/backend && python3 -m pytest tests/test_phase3_historical_credit.py tests/test_phase4a_approval_gate.py
```

### 10.4 Live page mount smoke (replace URL with the fork's REACT_APP_BACKEND_URL)
```bash
API_URL=$(grep REACT_APP_BACKEND_URL /app/frontend/.env | cut -d= -f2 | tr -d '\n')
curl -s -o /dev/null -w "/api/health: %{http_code}\n" "$API_URL/api/health"
```
Then use the screenshot tool to load `$API_URL/sales-new` after login (creds in §11 below) and assert:
- `[data-testid='unified-sales-page']` exists
- `[data-testid='connectivity-status'][data-status='online']` visible
- No `pageerror` events
- Both Quick Sale and Detailed Sale tabs render

### 10.5 Test credentials
Org Admin: `test_org_admin@regression.local` / `RegressionPass!2026`
(See `/app/memory/test_credentials.md` for the full list.)

---

## 11. Final instruction for the next fork

> **Read this handoff first.**
> Run the smoke baseline in §10 (lint + 37/37 frontend + 28/28 backend + `/api/health` 200 + live page mount).
> Then produce a written assessment in the A/B/C/D/E format described in §8 of this handoff.
> If the assessment confirms the plan, **wait for the owner's approval before extracting `HistoricalCreditBanner.jsx` and `HistoricalCreditDialog.jsx`.**
> Do not touch anything in §9. Do not duplicate anything listed in §7.

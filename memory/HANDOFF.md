# AgriBooks — Fork Handoff Document
**Created:** March 2026 (pre-fork)
**Purpose:** Complete state capture for the next agent in the fork.

---

## What Was Completed This Session (In Order)

### 1. Notification System Phase 5 — Scheduled Compliance Deadlines
- APScheduler daily job `_daily_compliance_check` in `main.py` at 8:30 AM
- Fires `compliance_deadline` notifications: expired docs (critical), expiring within 30d (warning), missing monthly filings after 15th (SSS, PhilHealth, Pag-IBIG, BIR 1601-C, 0619-E, 2550M — warning)
- Dedup via `metadata.dedup_key` to prevent re-firing same alert
- `create_notification()` in `notifications.py` extended with `severity_override` param
- Frontend `NotificationsPage.js`: `compliance_deadline` TYPE_CONFIG with orange FileWarning icon + ComplianceDetail expandable row
- Tested: iteration_144.json — 22/23 backend, 100% frontend

### 2. Security Alert Enrichment — Phases 1-3
- **Phase 1** `_raise_security_alert()`: now resolves branch_name, adds user_role, user_email. Message: "Test Manager (Manager) entered wrong PIN 6x at Branch 1 — Transaction Verification: Verify PO-XXX"
- **Phase 2** `_raise_qr_security_alert()`: accepts terminal_id, resolves "AgriSmart Terminal at Branch X" from terminal_sessions. Enriches with doc_id, doc_number, counterparty, doc_amount. qr_actions.py passes terminal_id at all 3 terminal-only call sites (release_stocks, receive_payment, transfer_receive)
- **Phase 3** Frontend `SecurityAlertDetail` in NotificationsPage.js: WHO+WHAT cards (auth) / TERMINAL+DOCUMENT cards (QR). Clickable doc number opens ReviewDetailDialog. Lock banner for locked docs
- Also added "View Receipt" button to authenticated PIN alerts when doc_id is available
- Tested: iteration_145.json — 28/28 backend, 11/11 frontend

### 3. Modal Registry PDF
- Full audit of all 23 modal/dialog components in the app
- PDF saved to R2: `agribooks-docs/reports/modal-registry-2026-03/agribooks_modal_registry.pdf`
- Groups A-G, Redundancy Map, Quick Reference Cheat Sheet
- Key redundancies identified (see below)

### 4. Modal Consolidation Phase 1 — A1 Absorbs A3
- `ReviewDetailDialog.js` (A1) extended with backward-compat props: `poId`, `poNumber`, `onUpdated`, `onOpenChange`
- When `poNumber` passed: resolves UUID via `/api/invoices/by-number/{poNumber}` → `/dashboard/review-detail`
- **7 pages migrated** from PODetailModal (A3) → ReviewDetailDialog (A1):
  - CloseWizardPage.js (showReviewAction=false, view-only)
  - PaySupplierPage.js (onUpdated={loadSuppliers})
  - QuickSearch.js (both poId + poNumber paths)
  - AuditCenterPage.js (showReviewAction=true)
  - SuppliersPage.js (onUpdated={fetchData})
  - TransactionSearchPage.jsx (poId path)
  - DashboardPage.js (view-only)
- `PODetailModal.js` now has **zero imports** — orphaned, safe to delete later
- Z-reports: zero impact (UI-only migration)
- Lint: all clean. API chain verified.

---

## The 4-Phase Modal Consolidation Plan (IN PROGRESS)

### Phase 1 — A1 absorbs A3 (PO modals) ✅ DONE
See above.

### Phase 2 — A2 absorbs A4 (Invoice/Sale modals) — NEXT TO DO
**Goal:** Add `compact={true}` prop to `InvoiceDetailModal` (A2) that renders a single-view identical to `SaleDetailModal` (A4). Then migrate 9 pages from A4 → A2 with `compact` prop.

**Files to change:**
- `components/InvoiceDetailModal.js` — add `compact` prop. When `compact=true`: hide tabs (Releases/Payment History tabs), show simplified single-view layout matching A4's current layout
- Migrate these 9 pages (all view-only except where noted):
  - `pages/SalesPage.js` — click sale row → opens SaleDetailModal
  - `pages/AccountingPage.js` — has saleId
  - `pages/ExpensesPage.js` — has invoiceNumber  
  - `pages/CustomersPage.js` — has invoiceNumber
  - `pages/CloseWizardPage.js` — already has SaleDetailModal import (detailType==='sale')
  - `pages/ReportsPage.js` — has saleId
  - `pages/DailyLogPage.js` — has saleId
  - `pages/PaymentsPage.js` — has invoiceNumber
  - `pages/PendingReleasesPage.jsx` — has saleId
  - `pages/InternalInvoicesPage.js` — has saleId
- Add backward-compat `saleId` and `invoiceNumber` props to A2 (InvoiceDetailModal already accepts invoiceNumber but check saleId too)
- SaleDetailModal.js → zero imports → orphaned (like PODetailModal after Phase 1)

**Prop mapping A4 → A2:**
- A4: `{ open, onOpenChange, saleId, invoiceNumber, onUpdated }`
- A2: `{ open, onOpenChange, invoiceId, invoiceNumber, expenseId, onUpdated, compact }`
- A4 uses `saleId` — A2 uses `invoiceId` (same thing, invoices collection)
- Need to add `saleId` as backward-compat alias for `invoiceId` in A2

**Z-report safeguard:** A4 in CloseWizardPage is view-only (no onUpdated). A4's void/edit actions call the same `/api/invoices/{id}/void` and `/api/invoices/{id}/edit` endpoints — these remain identical in A2.

### Phase 3 — C1 + C2 merge into AuthDialog — AFTER PHASE 2
**Goal:** Single `AuthDialog` component with `mode="pin"|"totp"|"either"` prop.

**Files to change:**
- New `components/AuthDialog.js` — unified PIN/TOTP entry dialog
  - Props: `open, onClose, mode, action, docType, docId, docLabel, title, description, onVerified`
  - When `mode="pin"`: same as current C1 (VerifyPinDialog)
  - When `mode="totp"`: same as current C2 (TotpVerifyDialog)
  - When `mode="either"`: accepts both
- Make `VerifyPinDialog.js` → thin wrapper around `<AuthDialog mode="pin" />`
- Make `TotpVerifyDialog.js` → thin wrapper around `<AuthDialog mode="totp" />`
- No page changes needed immediately (wrappers preserve backward compat)

**Z-report safeguard:** Auth dialogs call `/api/verify/{docType}/{docId}` and `/api/auth/verify-totp` — endpoints untouched.

### Phase 4 — Extract F7 Fund Transfer Dialog — AFTER PHASE 3
**Goal:** Extract duplicated fund transfer inline dialogs from FundManagementPage and AccountingPage into `components/FundTransferDialog.js`.

**Files to change:**
- New `components/FundTransferDialog.js` — handles Cashier↔Safe, Safe→Bank, Capital Injection with PIN
  - Props: `open, onClose, type, amount, onSuccess`
  - Calls: `/api/accounting/transfer-funds`
- Update `pages/FundManagementPage.js` — replace inline dialog with `<FundTransferDialog />`
- Update `pages/AccountingPage.js` — replace inline dialog with `<FundTransferDialog />`

**Z-report safeguard:** Fund transfers update wallet balances which appear in Z-reports, but the API call is unchanged — only the UI is extracted.

---

## Other Pending Work (Non-Modal)

### P0 — Compliance Calendar Widget on Dashboard
- **What:** New dashboard widget showing expired/expiring docs + monthly filing status
- **API:** `GET /api/documents/compliance/summary` — ALREADY EXISTS, returns: `{expired: [], expiring_soon: [], monthly_coverage: {}}`
- **Where:** `pages/DashboardPage.js` — add new widget to the dashboard grid
- **Widget content:**
  - Red card: "X expired" (click → /documents)
  - Amber card: "X expiring within 30d" (click → /documents)
  - Monthly filing tracker: 6 dots (SSS/PhilHealth/Pag-IBIG/BIR 1601-C/0619-E/2550M) — green=filed, red=missing
- **Note:** Scheduler (Phase 5 notifications) already fires alerts for these — widget is the visual dashboard companion

### P1 — Terminal Quick Stock Check
- New mode in terminal floating mode selector
- Scan barcode → instant stock level card (no PIN, read-only)
- Backend: reuse `GET /api/products?search=` + inventory lookup
- Frontend: new `TerminalStockCheck.jsx` component, add to TerminalShell mode selector

### P1 — Terminal Price Check
- Scan barcode → price card (respects `products.view_cost` permission)
- New `TerminalPriceCheck.jsx`

### P1 — Discount Cashier Drill-Down Report
- Aggregate `discount_audit_log` by cashier in `/reports` Discounts tab
- Show: cashier name, total discounts this month, discount count, avg %, sorted by amount

### P1 — AP Payment History per Supplier
- Show recent payment history in `PaySupplierPage.js` below PO list
- Query: `expenses` where fund_source linked to supplier's POs

### P2 — Backlog
- Shared receipt clickable link in ReviewDetailDialog (PO-XXXX → opens that PO's dialog)
- Admin tool for corrupted POs
- Visual trail for partial invoices
- Smart journal entries for back-dated sales

### P3 — Future
- Refactor SuperAdminPage.jsx (1000+ lines)
- Fix react-hooks/exhaustive-deps ESLint warnings
- Native Android APK (Capacitor + AAR)

---

## Current State of Key Files

### Modal Hierarchy (CANONICAL — use these going forward)
| Use Case | Component | File |
|---|---|---|
| View/Verify/Pay a PO | `ReviewDetailDialog` (A1) | `components/ReviewDetailDialog.js` |
| View/Verify a Sale/Invoice | `InvoiceDetailModal` (A2) | `components/InvoiceDetailModal.js` |
| View a Sale (compact) | `SaleDetailModal` (A4) — **MIGRATE TO A2 in Phase 2** | `components/SaleDetailModal.js` |
| View a Transfer | `TransferDetailModal` (A5) | `components/TransferDetailModal.js` |
| View an Expense | `ExpenseDetailModal` (A6) | `components/ExpenseDetailModal.js` |
| Customer AR Statement | `CustomerStatementModal` (B1) | `components/CustomerStatementModal.js` |
| PIN entry | `VerifyPinDialog` (C1) | `components/VerifyPinDialog.js` |
| TOTP entry | `TotpVerifyDialog` (C2) | `components/TotpVerifyDialog.js` |
| Receipt upload | `UploadQRDialog` (D1) | `components/UploadQRDialog.js` |
| Receipt view | `ViewQRDialog` (D2) | `components/ViewQRDialog.js` |

### Dead Files (zero imports — safe to delete when confident)
- `components/PODetailModal.js` — replaced by ReviewDetailDialog (A1)

### ReviewDetailDialog backward-compat props (added this session):
```jsx
// All these work now:
<ReviewDetailDialog recordType="purchase_order" recordId={uuid} ... />  // original
<ReviewDetailDialog poId={uuid} ... />                                   // alias
<ReviewDetailDialog poNumber="PO-B1-001014" ... />                       // resolves UUID
<ReviewDetailDialog poNumber="PO-X" onOpenChange={fn} onUpdated={fn} />  // full backward compat
```

---

## Credentials
- Super Admin: `janmarkeahig@gmail.com` / `Aa@58798546521325`
- Company Admin: `jovelyneahig@gmail.com` / `Aa@050772`
- Manager PIN: `521325`
- App URL: `https://audit-pulse-10.preview.emergentagent.com`

## Test Reports From This Session
- `iteration_144.json` — Compliance Deadline Notifications
- `iteration_145.json` — Security Alert Enrichment Phases 1-3

## Key Architecture Reminders
- Z-reports read from MongoDB directly (invoices, expenses, payments collections). Modal UI changes NEVER affect Z-report data.
- All QR actions (release_stocks, receive_payment, transfer_receive) require terminal_id — verified before PIN check
- `create_notification()` in `notifications.py` is the SINGLE helper for all new notifications — always use it
- ReviewDetailDialog uses `/dashboard/review-detail/{type}/{id}` endpoint (richer than direct PO endpoint)

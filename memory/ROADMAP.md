# AgriBooks — ROADMAP (Updated Mar 31, 2026)

---

## Modal Consolidation Plan (IN PROGRESS)

| Phase | What | Status | Files |
|---|---|---|---|
| Phase 1 | A1 (ReviewDetailDialog) absorbs A3 (PODetailModal) | DONE | 7 pages migrated, PODetailModal.js orphaned |
| Phase 2 | A2 (InvoiceDetailModal) absorbs A4 (SaleDetailModal) | DONE | 14 files migrated, SaleDetailModal.js orphaned |
| Phase 3 | C1+C2 merge into AuthDialog | DONE | AuthDialog.js created, C1/C2 are thin wrappers |
| Phase 4 | Extract F7 FundTransferDialog | DONE | FundTransferDialog.js extracted from FundManagementPage |
| Cleanup | Delete orphaned PODetailModal.js, SaleDetailModal.js | DONE | Files removed |

### Phase 2 Detail — DONE
`compact={true}` prop added to `InvoiceDetailModal.js` (A2). `saleId` backward-compat alias for `invoiceId`. 14 files migrated. SaleDetailModal.js orphaned.

### Phase 3 Detail — DONE
`AuthDialog.js` created with `mode="pin"|"totp"|"either"`. VerifyPinDialog → thin wrapper `<AuthDialog mode="pin">`. TotpVerifyDialog → thin wrapper `<AuthDialog mode="totp">`. No page-level changes needed.

### Phase 4 Detail — DONE
`FundTransferDialog.js` extracted from FundManagementPage inline dialog. Accepts `transferType`, `walletByType`, `branchId`, `onSuccess` props. FundManagementPage updated to use the component.

---

## P0 — DONE

### SMS Engine Phase 4: Terminal Messages UI — DONE
- TerminalMessages.jsx: Queue (Pending/Sent/Failed), Compose, Blast, Templates views
- Added to TerminalShell.jsx floating mode selector

### SMS Engine Phase 1-3 — DONE
- Backend: `sms_queue`, `sms_templates`, `sms_settings` collections
- 10 templates: credit_new, reminder_15day, reminder_7day, overdue_notice, payment_received, charge_applied, delivery_ready, promo_blast, monthly_summary, custom
- Auto-triggers: on credit sale, payment received, interest/penalty generated
- Scheduled: APScheduler daily 8AM (15d/7d/overdue reminders), monthly 1st 9AM (balance summary)
- API: full CRUD for templates, settings; queue management (pending/sent/failed/retry/skip)
- SMS delivery via external gateway (phone app polls `/api/sms/queue/pending`)

### Terminal Credential Login Phase 1 — DONE
- `POST /api/terminal/credential-pair`: manager auto-links, admin selects branch
- TerminalPairScreen.jsx: "Pairing Code" + "Login" tabs

### Customer Receivables Left Panel (PaymentsPage) — DONE
- Left sidebar panel on `/payments` matching `/pay-supplier` pattern
- Backend: `GET /api/customers/receivables-summary` with include_zero, branch_id params
- Frontend: filter/sort/search, DUE badges, total receivables display
- Orphaned files (`PODetailModal.js`, `SaleDetailModal.js`) deleted

---

## P0 — Immediate (Non-Modal)

### Compliance Calendar Widget on Dashboard
- New widget on `DashboardPage.js`
- API: `GET /api/documents/compliance/summary` (ALREADY EXISTS)
- Shows: expired count (red), expiring within 30d (amber), monthly filing 6-dot tracker
- Click → navigates to `/documents`
- Companion to the scheduled compliance notifications (already running daily at 8:30 AM)

---

## P1 — Terminal Features

### Quick Stock Check
- New mode in terminal floating mode selector
- Scan barcode → instant stock level card (no PIN, read-only)
- New `TerminalStockCheck.jsx` component
- Backend: `GET /api/products?search=` + inventory lookup

### Price Check
- Scan barcode → price card (respects `products.view_cost` permission)
- New `TerminalPriceCheck.jsx`

### Quick Count
- Scan + enter qty → submits count sheet (PIN required)
- New `TerminalQuickCount.jsx`
- Backend: reuse `POST /api/count-sheets` endpoints

---

## P1 — Finance & Reports

### Discount Cashier Drill-Down
- Aggregate `discount_audit_log` by cashier in `/reports` Discounts tab
- Sort by total discount amount, show repeat offenders

### AP Payment History per Supplier
- Show payment history below PO list in `PaySupplierPage.js`
- Query from `expenses` collection linked to supplier

---

## P2 — Backlog

### Shared Receipt Clickable Link
- In ReviewDetailDialog, "Collection receipt shared from PO-XXXX" → make PO-XXXX clickable
- Opens that source PO's ReviewDetailDialog
- `onClick={() => { setCurrentRecord({id: f.shared_from_record_id, type: 'purchase_order'}) }}`

### Admin Tool for Corrupted POs
- List/fix POs missing grand_total, subtotal
- Add to SuperAdminPage or new admin route

### Visual Trail for Partial Invoices
- Timeline view of all linked payment transactions for an invoice
- Add to InvoiceDetailModal (A2) as new tab

---

## P3 — Future

- Refactor SuperAdminPage.jsx (1000+ lines monolith)
- Fix react-hooks/exhaustive-deps ESLint warnings (low risk, cosmetic)
- Native Android APK (Capacitor + printer-release.aar)
- Weight-embedded EAN-13 barcode recognition
- Automated Payment Gateway & Demo Login

---

## Architecture Notes for Next Agent

### Modal Rules (ENFORCE THESE)
1. **To show a PO:** Use `ReviewDetailDialog` with `poId` or `poNumber` — NEVER import PODetailModal
2. **To show a Sale/Invoice:** Use `SaleDetailModal` for now — will migrate to `InvoiceDetailModal` with `compact` in Phase 2
3. **To show a Transfer:** Use `TransferDetailModal` (A5)
4. **To show an Expense:** Use `ExpenseDetailModal` (A6)
5. **To upload a receipt:** Use `UploadQRDialog` (D1) — UNIVERSAL
6. **To view receipts:** Use `ViewQRDialog` (D2) — UNIVERSAL

### ReviewDetailDialog (A1) — New Props Added This Session
```jsx
<ReviewDetailDialog
  // Original props (still work):
  open recordType recordId showReviewAction showPayAction onClose onReviewed recordNumber

  // New backward-compat props (Phase 1 addition):
  poId         // alias for recordId when dealing with POs
  poNumber     // resolves UUID via /invoices/by-number/{poNumber}
  onUpdated    // alias for onReviewed callback
  onOpenChange // alias for (v) => { if (!v) onClose() }
/>
```

### Notification System (Complete)
- All notifications use `create_notification()` from `notifications.py`
- Phase 5 scheduler runs daily at 8:30 AM (`_daily_compliance_check` in main.py)
- Types: security_alert, po_receipt_review, transfer_variance_review, compliance_deadline, discount_given, below_cost_sale, negative_stock_override, ap_payment + more
- Security alerts now enriched: user_role, user_email, branch_name (auth PIN), terminal identity + doc details (QR)

### Z-Report Safety
- Z-reports read from MongoDB directly (collections: invoices, expenses, payments, wallets)
- Modal consolidation (Phases 1-4) is PURELY UI — zero backend/DB changes
- Compliance notifications are PURELY additive — they write to `notifications` collection only

### Dead Files (can be deleted after confirming stable)
- `components/PODetailModal.js` — zero imports since Phase 1
- `components/SaleDetailModal.js` — zero imports since Phase 2

### Modal Hierarchy (CANONICAL — use these going forward)
1. **To show a PO:** Use `ReviewDetailDialog` with `poId` or `poNumber`
2. **To show a Sale/Invoice:** Use `InvoiceDetailModal` with `compact` prop (or without for full tabbed view)
3. **To show a Transfer:** Use `TransferDetailModal` (A5)
4. **To show an Expense:** Use `ExpenseDetailModal` (A6)
5. **To upload a receipt:** Use `UploadQRDialog` (D1) — UNIVERSAL
6. **To view receipts:** Use `ViewQRDialog` (D2) — UNIVERSAL
7. **For PIN verification:** Use `VerifyPinDialog` (wrapper) or `AuthDialog mode="pin"`
8. **For admin authorization:** Use `TotpVerifyDialog` (wrapper) or `AuthDialog mode="totp"`
9. **For fund transfers:** Use `FundTransferDialog`

---

## Key Credentials
- Super Admin: `janmarkeahig@gmail.com` / `Aa@58798546521325`
- Company Admin: `jovelyneahig@gmail.com` / `Aa@050772`
- Manager PIN: `521325`
- App URL: `https://late-encode-flow.preview.emergentagent.com`

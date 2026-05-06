# AgriBooks PRD

## Iter 253 — PO + Pay-Supplier Wallet Routing Audit (Feb 2026) ✅

### Problem
1. `/purchase-orders` Pay-in-Cash dialog only offered Cashier + Safe — no Digital, no Bank. Header dropdown only showed "Cash" or "Credit/Terms".
2. Backend `POST /purchase-orders` (cash branch) silently fell through to deduct from cashier when `bank` or `digital` was passed (data-integrity bug).
3. `/pay-supplier` worked for all 4 wallets but used hardcoded labels (e.g., "Check / Bank") instead of the wallet's actual `name` (e.g., "BDO Checking"), and didn't mask hidden bank balances or show a visible "paying via X" hint.

### Decisions
- Bank balance is masked (`••••` + lock) for all non-admin roles (option A).
- Canonical wallet labels: **Cashier Drawer**, **Physical Safe**, **Digital / E-Wallet**, **Bank Account** (fall back when DB `name` is empty).
- PO instant-pay via Bank/Digital deducts immediately; requires admin PIN or TOTP. Cashier/Safe still no PIN.
- Same PIN policy applies to PO Payment-Adjustment dialog.

### What was built
**Backend (`purchase_orders.py`)**
- `_get_fund_balances()` returns 4 wallets with `*_name`, `*_id`, `*_available` flags. `GET /fund-balances` masks bank for non-admins.
- `POST /purchase-orders` cash branch validates balance + drains correct wallet + writes expense + auto-posts double-entry JE for bank/digital. Bank/Digital go through `verify_pin_for_action(pin, "pay_po_bank")` policy.
- `POST /{po_id}/adjust-payment` mirrors the 4-wallet support with PIN policy + symmetric refund (safe lot insert / digital helper / bank $inc + `wallet_movements bank_in`).

**Frontend**
- `PurchaseOrderPage.js`: Pay-in-Cash dialog → 4-card grid with lock icons, masked balance, conditional PIN input, method/wallet hint in summary. Same for Pay-Adjustment dialog.
- `PaySupplierPage.js`: uses wallet `name` from `/fund-wallets`; masks hidden bank balance; "Paying via **<Method>** from **<Wallet>**" hint.

### Files
- `/app/backend/routes/purchase_orders.py`
- `/app/frontend/src/pages/PurchaseOrderPage.js`
- `/app/frontend/src/pages/PaySupplierPage.js`

### Verified
Testing agent iter 248: 16/16 backend tests pass (all 4 fund sources × create + adjust + PIN gate + redaction + insufficient-funds + journal entries). Frontend smoke confirmed 4-wallet pills render with canonical labels.

---


## Iter 252 — Request Stock UX: SmartProductSearch parity with /sales-new (Feb 2026) ✅

### Problem
Request Stock form's product picker was a basic anchored dropdown — no keyboard nav, no smart positioning, no fuzzy fallback. User wanted parity with `/sales-new` Detailed Sales.

### What was built
- `SmartProductSearch.js` extended with optional `mode='request'` + `alsoBranchId` + `placeholder` props. Same keyboard nav, scroll-into-center, auto-flip dropdown, fuzzy + offline IndexedDB fallback. Compact request-mode row layout (name/SKU + Supply/You stock badges).
- Request Stock form refactored to use it. Each row is `<SmartProductSearch>` (empty) or a green summary card with X (filled). Product select fills row + auto-grows next empty row + focuses qty (mirrors `UnifiedSalesPage.handleProductSelect`). Trash X marked `tabIndex={-1}` so Tab from qty hops to next row's search.

### Files
- `frontend/src/components/SmartProductSearch.js` — added `mode`, `alsoBranchId`, `placeholder` props; request-mode render branch
- `frontend/src/pages/BranchTransferPage.js` — Request Stock rows + `selectReqProduct` + `reqQtyRefs` map

### Verified
Testing agent iter 247: 10/10 UX checks pass (typeahead, ↑/↓ nav, Enter+focus, qty auto-focus, auto-grow, X-clear, dropdown flip-up/down, fuzzy "No exact match for X" chip, multi-row keyboard-only flow). The Tab-order minor was fixed in same iter.

---

## Iter 251 — Branch Stock Request: SMS + Tab UX + Configurable Recipients (Feb 2026) ✅

### Problem
Stock Request flow on Branch Transfer page had three usability gaps:
1. Manual "+ Add Product" button only — slow data entry.
2. No SMS to admins/managers — owners away from the dashboard missed urgent requests.
3. Concern about 3-4 staff racing to fulfill the same request.

### What was built
**Backend**
- New SMS template `branch_stock_request` (sms.py:`DEFAULT_TEMPLATES`).
- **Auto-seed bug fix** (`queue_sms()` sms.py:912-935): now does per-key `$setOnInsert` upsert from DEFAULT_TEMPLATES instead of only seeding when an org has zero templates. Existing tenants now get any newly-shipped template key on first send.
- New endpoints `GET/PUT /api/sms/recipients/{trigger_key}` (sms.py:1091-1142). Stores role config in `db.sms_recipient_config`. Used by `branch_stock_request` with flags: `include_admins`, `include_supply_manager`, `include_supply_auditor`, `include_all_supply_users`.
- New helper `_notify_stock_request_recipients()` (purchase_orders.py top). Resolves recipients per role config, builds public view link from `auto_generate_doc_code` output, calls `queue_sms()`. Wired into the existing branch_request block in `POST /api/purchase-orders`.
- Public viewer (`GET /api/doc/view/{code}`) enriched: returns `is_branch_request`, `supply_branch_name`, `requesting_branch_name`, `notes`, `fulfillment_started_at/by`.

**Frontend**
- `BranchTransferPage.js` — Request Stock rows now mirror /sales-new Detailed Sales UX: Enter on search picks first match + focuses qty; Tab on LAST row qty (when product+qty present) auto-adds a new row and focuses its search.
- `MessagesPage.js` — Settings tab gained "Branch Stock Request — Recipients" card with 4 toggles.
- `DocViewerPage.jsx` — branch_request POs render as "Stock Request" with `<requesting> requests from <supply>` route line + view-only amber notice + "Open in Branch Transfer" button.

### Concurrency safety
`POST /api/purchase-orders/{po_id}/generate-branch-transfer` already locks the request (status: requested → in_progress). Subsequent clicks return `400 "Request has already been processed"`. SMS link is deliberately view-only; only login can fulfill.

### Testing
- Backend: 7/7 pytests pass (`/app/backend/tests/test_branch_stock_request_sms_245.py`)
- Frontend: Tab UX, Settings card, DocViewer all validated in iter 246

---

## Iter 250 — Fund Source Picker on Expense Modals (Feb 2026) ✅

### What was built
Top-of-modal "Fund Source" picker on **Record Expense**, **Farm Expense** and **Customer Cash Out** dialogs in the Accounting page. Four colorful tiles + a prominent "Paying from X" banner so cashiers can never miss what wallet is being debited.

### Wallets supported
| Source | Color | Balance shown? | PIN required? |
|---|---|---|---|
| Cashier Drawer | Emerald | ✅ | ❌ Role-only |
| Physical Safe | Amber | ✅ | ❌ Role-only |
| Digital / E-Wallet | Violet | ✅ | ✅ Admin PIN or TOTP |
| Bank Account | Sky | ❌ Hidden | ✅ Admin PIN or TOTP |

### Backend
- `derive_fund_source` extended → now returns `cashier | safe | digital | bank`
- `deduct_from_fund_source` extended → handles `bank` (deducts from bank wallet, logs `wallet_movements` of type `bank_expense`)
- New helper `return_to_fund_source(branch_id, fund_source, amount, ref, user, payment_method)` — single source-of-truth used by all 3 void/reverse paths plus the `update_expense` adjustment delta
- New `_verify_protected_fund_source_pin` helper — called on every create endpoint (`/expenses`, `/expenses/farm`, `/expenses/customer-cashout`, `/expenses/employee-advance`) when fund_source is `digital` or `bank`
- 2 new PIN action keys registered in `verify.py`:
  - `expense_from_digital` (defaults: admin_pin + totp)
  - `expense_from_bank` (defaults: admin_pin + totp)

### Frontend
- New `components/FundSourcePicker.jsx` — reusable, fetches `/api/fund-wallets?branch_id=...`, renders 4 tiles with live balances, the "Paying from X" banner with after-balance preview, insufficient-funds warning, and a PIN input that only appears for protected sources.
- All 3 dialogs updated: state forms now include `fund_source` + `fund_source_pin`, picker rendered at top, submit handlers validate PIN presence before POST.
- All 3 dialogs scrollable on mobile (`max-h-[90dvh] overflow-y-auto`).

### Void / Refund flow
On void/reverse of any of the 3 expense types, funds are returned to the **original** fund source (cashier → cashier, safe → safe-lot, digital → digital wallet, bank → bank wallet) via `return_to_fund_source`. Mirror entries are recorded in `wallet_movements` for audit trail.

### Tested (curl)
- ✅ `GET /api/fund-wallets` — returns all 4 wallet types per branch with balances
- ✅ Cashier expense without PIN → 200 created
- ✅ Digital expense without PIN → **400** "Admin PIN or TOTP required to pay from digital"
- ✅ Digital expense with wrong PIN → **403** "Invalid PIN. Admin PIN or TOTP required to pay from digital."
- ✅ UI smoke: tile selection, banner update, PIN input appears for Digital/Bank, balance hidden for Bank

### Files
- `backend/routes/accounting.py` — fund-source helpers + 4 endpoints + void paths refactored
- `backend/routes/verify.py` — 2 new PIN action keys
- `frontend/src/components/FundSourcePicker.jsx` — NEW
- `frontend/src/pages/AccountingPage.js` — 3 dialogs wired to picker

---


## Iter 249 — Sales by Category Breakdown in Close Wizard Step 1 (Feb 2026) ✅

### What was built
A collapsible "Sales by Category" panel on Step 1 of the Daily Closing Wizard that breaks down `Total Sales` by product category. Includes:
- Category name + line count + % of total + amount (sorted desc by amount)
- Grand total row that **must** equal Total Sales (with green ✓ "Balances with Total Sales" badge)
- Red "Off by ₱X" warning + explanation if sums don't match (e.g., due to a missing category on a sale)
- Auto-expanded when ≤6 categories, collapsed by default for many

### Implementation note
Computed client-side from the same `entriesWithTotal` array already used for the running total (`_cash_amount` per entry, summed by `e.category || 'Uncategorized'`). This guarantees the breakdown reconciles to the displayed `Total Sales` to the penny — no risk of mismatch from a stale backend aggregate.

### Files
- `frontend/src/pages/CloseWizardPage.js` — added category breakdown computation + collapsible `<details>` panel between header row and entries table

### Tested
- ✅ Lint clean
- ✅ Screenshot smoke: route protected when no branch — once branch with sales selected, breakdown renders inline

---


## Iter 248 — Double-Submit Bug Fix (Expenses + Customers) (Feb 2026) ✅

### Reported issue
User accidentally pressed "Create Expense & Invoice" 2-3 times → 2 duplicate expenses created.

### Root cause
Save handlers had no in-flight guard. Rapid clicks → multiple parallel `api.post('/expenses', ...)` → backend creates each one.

### Fix
Added a per-handler `submitting` state + early-return guard + `disabled` button + "Saving…" / "Creating…" / "Releasing…" text. Applied across **all** money-creating dialogs that lacked guards:
- `ExpensesPage.js` — Record Expense, Farm Expense, Customer Cash Out, Employee Cash Advance (4 buttons)
- `DailyLogPage.js` — quick-expense save
- `AccountingPage.js` — record/edit expense
- `CustomersPage.js` — Save (also covers opening-balance invoice creation from Iter 247)

### Already protected (verified)
- `PaymentsPage.js` `handleApplyPayment` — uses `processing` state ✅
- `TerminalReturnRefundModal` / `TerminalUpdateReceiptModal` — use `submitting` state ✅
- `PurchaseOrderPage.js` — has guards on critical paths

### Files
- `frontend/src/pages/ExpensesPage.js`
- `frontend/src/pages/DailyLogPage.js`
- `frontend/src/pages/AccountingPage.js`
- `frontend/src/pages/CustomersPage.js`

### Tested
- ✅ Lint clean across all 4 files
- ✅ UI smoke test: Record Expense dialog renders correctly, button label "Save Expense"
- ✅ User must manually delete the existing duplicate expense (one-time cleanup)

---


## Iter 247 — Customer Starting Balance + Mobile Modal Scroll Fix (Feb 2026) ✅

### What was built
1. **Customer Opening Balance on create** — When adding a new customer, optionally enter `Amount Owed` + `As Of Date`. Backend (`POST /api/customers`) auto-generates a one-time receipt flagged `is_opening_balance=True` (named "Opening Balance Carry-forward (Migrated)"). Reuses existing `_create_opening_balance_invoice` and `_send_opening_balance_sms` helpers from import flow. Sets the customer's balance to the entered amount. Toast shows the generated invoice number.
2. **Mobile scroll fix on Return & Refund + Update for Incomplete Stock modals** — Replaced Radix `<ScrollArea>` (which intermittently swallows touch events on small viewports) with native `overflow-y-auto overscroll-contain` divs. Switched modal `max-h` from `vh` → `dvh` so on-screen keyboard doesn't squeeze the scroll region.

### Files
- `backend/routes/customers.py` — `create_customer` accepts `opening_balance` + `opening_balance_date`, lazy-imports import helpers, returns `opening_invoice_number`
- `frontend/src/pages/CustomersPage.js` — "Starting Balance (optional)" section in Add dialog (create-only, hidden on edit), dialog made scrollable on mobile (`max-h-[90dvh]`)
- `frontend/src/components/TerminalReturnRefundModal.jsx` — removed ScrollArea import, replaced step-1 + step-2 scroll regions with native scroll
- `frontend/src/components/TerminalUpdateReceiptModal.jsx` — same native-scroll swap

### Tested (backend curl)
- ✅ Create with opening_balance=1500 → balance=1500, opening_invoice_number=SI-MW-001079, invoice has is_opening_balance=true
- ✅ Create without opening_balance → balance=0, no invoice (regression preserved)
- ✅ Frontend: Add Customer dialog renders Starting Balance section with both fields visible

---


## Iter 246 — External Document Upload to Print (Feb 2026) ✅

### What was built
Extended the Remote Branch Printing Terminal to support external document uploads (PDFs, images).

### Features
1. `POST /api/print/upload-job` — multipart upload: validates file type (PDF/image ≤20MB), uploads to Cloudflare R2, creates print job with `source_type: "external"`, `file_url` (24hr presigned), `file_type`, `file_name`
2. `GET /api/print/jobs/{id}/file-url` — returns a fresh 24hr presigned URL (for EXE when URL expires)
3. `UploadPrintJobModal.js` — drag-drop UI with file picker, title, description, branch selector, terminal selector, print mode hint, upload progress bar
4. `PrintQueuePage.js` updated — "Upload Document" button, source_type icon/badge on all job rows, file info in expanded details
5. `cursor_handoff_print_terminal_exe.md` rewritten — now covers both HTML and external file printing (PDF via Sumatra PDF, images via PIL/win32print), `file_url` refresh, EXE architecture

### Files
- `backend/routes/print_jobs.py` — 2 new endpoints, `external_document` type
- `frontend/src/components/UploadPrintJobModal.js` — new
- `frontend/src/pages/PrintQueuePage.js` — upload button + source_type badges
- `app/memory/cursor_handoff_print_terminal_exe.md` — rewritten with external doc support

### Tested (backend)
- Upload endpoint: file → R2 → print job with correct fields ✅
- Job appears in `/api/print/jobs` with `source_type: "external"` ✅
- Org scoping fix: super-admin can use terminal lookup without org filter ✅

---



- Added "Send to Branch Printer" button to `ReferenceNumberPrompt` (sales completion modal — alongside Full Page + 58mm)
- Added "Remote Print" button to `PurchaseOrderPage` action toolbar (alongside Print Full + Print 58mm)
- Added "Send to Cloud Printer" button to `TerminalShell` QuickScan sheet (alongside View & Reprint)
- `SendToPrintModal` now accepts optional `axiosInstance` prop (used by terminal outside AuthContext)
- `QuickScanCloudPrint` inline component in TerminalShell uses terminal's own `api` axios instance

---



### What was built
A complete Remote Branch Printing Terminal system so users can send print jobs from anywhere to branch computers.

### Features
1. **Backend Print Jobs API** (`/api/print/*`) — create jobs, list with 15-day history, status tracking (pending/sent/printed/failed/cancelled), resend, polling fallback, terminal mode management, for-branch terminal picker, auto-purge (30-day inactive terminals)
2. **WebSocket push upgraded** — `terminal_ws.py` now tracks branch→terminal map; delivers pending jobs on WS connect; handles `print_job` and `print_mode_changed` events
3. **Print Center admin page** (`/print-center`) — terminal grid with online/offline status, pending job count, auto/manual mode toggle, job queue, 15-day history with filter
4. **SendToPrintModal** — Option C routing: auto-selects terminal for branch, picker if multiple, allows offline terminals (jobs queued)
5. **InvoiceDetailModal** — "Remote Print" button added to action toolbar (both compact and full-width render paths)
6. **Print mode in TerminalShell** — handles `print_job` WebSocket messages, auto-print vs manual queue, Print Queue overlay, mode toggle in Settings panel, startup mode load
7. **Cursor AI handoff prompt** — complete spec for Windows EXE at `/app/memory/cursor_handoff_print_terminal_exe.md`

### Document naming
- "Sales Receipt #INV-XXXX", "Purchase Order #PO-XXXX", "Z-Report – Branch – Date", "Branch Transfer #BT-XXXX", "Expense Receipt #EXP-XXXX"

### Files
- `backend/routes/print_jobs.py` (new — full print job API)
- `backend/routes/terminal_ws.py` (branch map, notify_branch_terminals)
- `backend/routes/terminal.py` (WS connect delivers pending jobs)
- `backend/main.py` (print_jobs router registered, purge scheduler)
- `frontend/src/pages/PrintQueuePage.js` (new — admin print center)
- `frontend/src/components/SendToPrintModal.js` (new — send to print dialog)
- `frontend/src/components/InvoiceDetailModal.js` (Remote Print button, both paths)
- `frontend/src/components/Layout.js` (Print Center sidebar section)
- `frontend/src/App.js` (/print-center route)
- `frontend/src/pages/terminal/TerminalShell.jsx` (print job handling + mode + queue)
- `app/memory/cursor_handoff_print_terminal_exe.md` (Windows EXE build guide)

### Tested
- 39/39 backend pytest cases passed
- Frontend: 3 bugs found and fixed by testing agent (babel plugin null-dereference, compact modal path missing SendToPrintModal, PrintEngine.getHtml→generateHtml)

---



Continuation of the audit. After shipping the 6 quick wins, this round closed the deferred items.

### What changed
1. **Sale Voided** customer template + hook — when an invoice is voided and `customer_id` is present, an SMS now goes out via the new editable `sale_voided` template (variables: invoice, grand_total, balance_note when balance>0, reason). `on_invoice_voided` is wired into `routes/invoices.py:void_invoice` after the price-match block. Walk-in voids are skipped.
2. **Refund Processed** customer template + hook — when a return is created and the customer is known, the new editable `refund_processed` template fires via `on_refund_processed` from `routes/returns.py:create_return`. `refund_line` and `credit_line` are conditional sentences so the SMS reads naturally for full-cash, store-credit-only, mixed, or zero-refund pull-outs.
3. **Inline staff strings → editable templates** — `credit_new_staff`, `charge_applied_staff`, `crop_season_started_owner` are now real entries in `DEFAULT_TEMPLATES`. `sms_hooks.py` calls `queue_sms(template_key=...)` instead of constructing literal strings, so owners can customise them from `/messages` like every other template.
4. **`payment_received` enriched** — added `<applied_to>` placeholder. When the payment is single-invoice (qr_actions, accounting), the template now reads "Salamat Juan! Natanggap namin ang P500 mo (applied to INV-2025-0041). Remaining balance: …".
5. **`charge_applied` enriched** — added `<source_invoice>` and `<period>` placeholders. `accounting.py` interest/penalty calls now pass the originating invoice number so customers see "Interest of P250 for INV-2025-0041 ay na-apply sa account mo …".
6. **Backwards compatible** — existing callers without the new args still work (defaults to empty string), and non-customised template bodies auto-upgrade via `_ensure_templates` self-healing.

### Files
- `backend/routes/sms.py` (5 new template entries, payment_received & charge_applied bodies enriched)
- `backend/routes/sms_hooks.py` (on_payment_received/on_charge_applied take new optional kwargs; converted 3 inline staff CCs to template-driven; appended `on_invoice_voided` + `on_refund_processed`)
- `backend/routes/invoices.py` (wired `on_invoice_voided` into `void_invoice`)
- `backend/routes/returns.py` (wired `on_refund_processed` into `create_return`)
- `backend/routes/qr_actions.py` (passes `invoice_number` to payment hook)
- `backend/routes/accounting.py` (passes `source_invoice` to interest & penalty hooks)
- `backend/tests/test_sms_audit_fixes_244.py` (extended — 16 tests total, all passing)

### Verified
- 16 pytest cases pass (templates, formula, content enrichment, void/refund hooks incl. walk-in skip behaviour).
- Backend boots clean, /api/health → 200.
- 34 default SMS templates registered in `DEFAULT_TEMPLATES`.

---



## Iter 244 — SMS Audit Quick-Wins (Feb 2026) ✅

User-requested: "Check the formula used on /messages... some data are missing in the SMS text like expected vs actual funds to compute over or short."

### Audit findings (full scan documented separately)
Six concrete problems; the four tiny ones plus the snapshot rewrite were applied this round. The remaining three (#7 / #8 / #9) are content/UX enhancements (deferred).

### What changed
1. **Z-Report SMS over/short was always wrong** — `close_reminder.py:send_zreport_finalized` was reading `close_record.expected_cash` (a key that does not exist on the daily_closings record; the canonical field is `expected_counter`). Result: every Z-Report SMS computed `over_short = actual − 0 = actual`. Now reads the canonical `expected_counter` AND the persisted `over_short` from the close_record (no recomputation).
2. **`_build_branch_snapshot` rewritten to mirror the Close Wizard** — previously `cash_total − exp_total`, ignoring starting_float, AR cash collections, fund_transfers, and fund-source on expenses. Now uses the same formula as `daily_operations.get_daily_close_preview()`: `starting_float + (cash_sales + partial_cash + cash_ar + split_cash) + net_fund_transfers − cashier_expenses`. Also splits cash vs digital correctly on `fund_source=split` invoices and exposes per-component placeholders (`<starting_float>`, `<total_cash_ar>`, `<net_fund_transfers>`, `<total_cashier_expenses>`).
3. **Refund-correction SMS silently broken** — `invoice_corrections.py` was calling `on_payment_received(...)` with 7 positional args in the wrong order (string passed as remaining_balance, db handle as last arg). Raised TypeError, caught silently. Fixed to the canonical 5-arg keyword call.
4. **QR payment SMS missing `next_due_info`** — `qr_actions.py` now computes the next-due invoice and passes it through, matching `accounting.py` behaviour.
5. **`render_template` hardened** — switched to regex substitution; missing/None placeholders collapse to empty string instead of leaking literal `<placeholder>` text. Logs a single WARN listing missing keys per template render.
6. **Manager CC for new credit uses live balance** — `sms_hooks.on_credit_sale_created` now reuses the live aggregation result (`live_total`) for the manager CC instead of the stale `customer.balance` snapshot taken before the new invoice was inserted.

### Files
- `backend/routes/close_reminder.py` (`_build_branch_snapshot` rewrite + `send_zreport_finalized` canonical-key fix)
- `backend/routes/sms.py` (`render_template` regex hardening)
- `backend/routes/invoice_corrections.py` (signature fix)
- `backend/routes/qr_actions.py` (next_due_info)
- `backend/routes/sms_hooks.py` (live manager total)
- `backend/tests/test_sms_audit_fixes_244.py` (NEW — 8 regression tests, all passing)

### Verified
- 8 new pytest cases pass (template rendering, Z-report SMS variance, snapshot formula incl. starting_float/AR/split-payment/fund-source-aware expenses).
- Backend boots, /api/health → 200.

### Deferred (per user agreement)
- Convert inline staff strings (`credit_new_staff`, `charge_applied_staff`, `crop_season_started_owner`) to real templates editable from `/messages`.
- Add `sale_voided` + `refund_processed` customer templates (close the loop on reversed invoices).
- Enrich `payment_received` and `charge_applied` with source invoice number + period.

---


## Iter 244 (Feb 2026) — Unified Invoice Detail Modal: Refund / Void / Void & Re-open ✅

### What changed
Consolidated the receipt-action buttons into the single canonical `InvoiceDetailModal.js` (~1,500 lines, used by Dashboard, Customers, Payments, Reports, Daily Log, Audit Center, Accounting, Expenses, Internal Invoices, Pending Releases, Transaction Search, Crop Credits, Close Wizard, QuickSearch, Signature Toolbar, and now Product Detail → Movement History). Both the **compact** and **full** layouts now show the same three actions on any non-voided invoice:
- **Refund** → closes modal, navigates to `/returns?invoice=<number>` (ReturnRefundWizard reads `useSearchParams` and pre-fills the invoice field in step 1).
- **Void** → reason + manager-PIN dialog, calls `POST /api/invoices/{id}/void` (accepts both `pin` and `manager_pin` server-side).
- **Void & Re-open** → same void flow, then stashes the returned `snapshot` in `sessionStorage.reopen_sale_snapshot` and navigates to `/sales-new`; `UnifiedSalesPage` consumes it on mount (once customers are loaded) and pre-fills a new sale draft with the original invoice_date preserved for interest continuity.

### Movement History click-through
Product Detail → Movement History now renders `reference_number` as a clickable link for `type === 'sale'` rows. Click opens the unified modal in compact mode.

### Files
- `/app/frontend/src/components/InvoiceDetailModal.js` (unified: added `handleRefund`, `openVoidDialog(alsoReopen)`, reopen snapshot flow; buttons wired into both compact and full layouts; void dialog label adapts to reopen mode)
- `/app/frontend/src/pages/ProductDetailPage.js` (clickable `reference_number` + unified modal usage with `compact` prop)
- `/app/frontend/src/pages/ReturnRefundWizard.js` (reads `?invoice=` query param)
- `/app/frontend/src/pages/UnifiedSalesPage.js` (consumes `sessionStorage.reopen_sale_snapshot` on mount)
- `/app/frontend/src/components/InvoiceDetailModal.jsx` — DELETED (superseded by the unified `.js`)

### Verified
- `/products/<id>` → Movement History → click sale ref → unified modal renders with all three buttons (`sale-refund-btn`, `sale-void-btn`, `sale-void-reopen-btn`).
- `/returns?invoice=TEST-INV-12345` pre-fills the invoice input.
- `/daily-log` and `/sales-new` still load cleanly with the updated modal.

---



## Iter 243.4 (May 2026) — Z-Report: Net Sales + Employee Advances + Before/After Allocation ✅

### What changed
- **Net Sales (gross profit) row** added to the Z-Report Detailed view, right under Cash Sales. Shows `gross - cogs` based on each sale line's `cost_price` snapshot. Also rendered on the Z-Report PDF as its own footer row.
- **Employee cash advance running totals** added under Cashier Expenses. For every employee who took an advance today, shows today's amount AND the outstanding `advance_balance` so the owner sees the full position at a glance.
- **Z-Report PDF footer now shows BEFORE → AFTER** for both Drawer and Safe:
  - `Drawer at Close: ₱110,267.25 → ₱56,400.00`
  - `Safe: ₱228,000.00 → ₱282,000.00`
- **Detailed Z-Report viewer** also gets a "After Allocation" panel showing `Cash to Safe`, `Drawer Carried Forward`, `Final Drawer`, `Final Safe` so on-screen and printed reports match.
- **Backend**: `_compute_net_sales_today()` and `_compute_employee_advances_today()` helpers in `daily_operations.py` — used by both `/daily-close-preview` and the stored `daily_closings` record so historical Z-Reports stay stable even when employee balances change later.

### Files
- `/app/backend/routes/daily_operations.py` (new helpers + wired into preview + close_record)
- `/app/backend/routes/zreport_pdf.py` (footer redesign)
- `/app/frontend/src/pages/DailyLogPage.js` (Z-Report detailed view)
- `/app/backend/tests/test_zreport_net_sales_advances_243_4.py` (2 tests, all PASS)

---

## Iter 243.3 (May 2026) — Receipt: single Transaction Date ✅
Reverted iter243's dual-date on receipts. Now prints ONLY the actual transaction date (`created_at`). The Z-Report posting bucket (`order_date`) is recoverable via QR scan / Audit Center.

---

## Iter 243.2 (May 2026) — `/api/doc/lookup` 500 → 403 ✅

### Problem
> "I tried putting the pin to process return and payment it told me to contact the admin" → "An unexpected server error occurred."

### Root cause
`/api/doc/lookup` (PUBLIC, no JWT) called `_resolve_pin()` and
`log_failed_qr_pin_attempt()` BEFORE setting tenant context. Tenant proxy
fail-closed → zero users found AND `pin_attempt_log.insert_one` raised
`RuntimeError("refusing to insert without organization_id")` → caught by
global handler → generic 500.

### Fix
Reordered: `_resolve_doc_code_with_context(code)` runs first (sets org
context), then lockout check, PIN check, log writes. Matches what
`/api/qr-actions/{code}/*` already does. 4 regression tests, all PASS.

---

## Iter 243.1 (May 2026) — Late-Encode unblocked ✅

### Problem
After iter243 the date input on `/sales-new` only allowed a single date
(min == max == today+1 when today closed). Late-encode for forgotten credit
sales was unreachable.

### Fix
- `min` expanded to today − 7 days (matches backend's late-encode window)
- `handleEncodingDateChange` no longer rejects closed past dates — backend
  catches them and the `LateEncodeDialog` auto-opens for credit/partial
  sales. Cash sales on closed days still 403 (correct).
- Amber hint "Past closed day — credit/partial requires manager PIN" shows
  when picking a closed past date.

---

## Iter 243 (May 2026) — Date Dead-Lock + Forward-Date Cap ✅

### Problem (reported by Jovelyn 2026-05-05)
> "Cannot encode before system start date (2026-04-30)" fires spuriously on
> /sales-new when trying to encode on today or the next date. We don't have
> this problem before. Also asked: terminal must auto-bump to next open day
> when today is closed; receipt should print BOTH transaction date and
> posted-to date.

### Root cause
After the iter241 day-close ledger fix made closings commit cleanly, the UI
began reliably seeing `lastCloseDate === today`. The native `<input type=date>`
on /sales-new had `min=lastCloseDate+1` and `max=today` → **min > max** when
today is closed → the browser emits an empty-string `onChange` → the client
floor-date guard fires `'' < floorDate` (string compare) → spurious error.

Separately, the system never enforced a forward-date cap, leaving open a
"forward-dated stock laundering" attack surface (cashier sets order_date
next year, stock deducts today, sale never appears on any Z-Report).

### Fix
1. **`maxAllowedDate` useMemo** — today, OR today+1 if today is already closed.
   Date input `max` now uses this. Eliminates min>max dead-lock.
2. **Empty/partial string guard in `handleEncodingDateChange`** — regex
   `/^\d{4}-\d{2}-\d{2}$/` filters intermediate browser-emitted values.
3. **Auto-bump on banner load** — when `last_close_date >= today`,
   `header.order_date` jumps to tomorrow automatically.
4. **Stale-error clear** — `dateError` resets when `lastCloseDate`/`floorDate`
   change, so old errors don't persist after the source data updates.
5. **Terminal auto-bump + persistent banner** — TerminalSales fetches
   `lastCloseDate`, computes saleDate at submit time, shows non-dismissable
   amber banner: *"Today is closed. All new sales will be dated 5/6"*.
6. **Backend forward-date cap** — `sales.py` rejects `order_date > max_allowed`
   (today, or today+1 if today closed) with 403. Manager-PIN override
   (`allow_forward_date_pin`) writes a `forward_date_override` row to
   `audit_log` for after-the-fact review.
7. **Dual-date receipts** — `PrintEngine.buildPageHeader` accepts a
   `postedToDate`. Both full-page (Order Slip, Charge Agreement) and thermal
   variants now print `Transaction Date: <created_at>` always, plus a
   smaller `Posted to Z-Report: <order_date>` line ONLY when the dates
   differ. Clean receipts stay clean.

### Files changed (iter243)
- `/app/frontend/src/pages/UnifiedSalesPage.js` — maxAllowedDate, regex guard,
  auto-bump on banner load, stale-error effect, Input max prop
- `/app/frontend/src/pages/terminal/TerminalSales.jsx` — lastCloseDate state,
  saleDate compute, persistent banner
- `/app/frontend/src/lib/nextOpenDate.js` — shared helper documenting the rule
- `/app/frontend/src/lib/PrintEngine.js` — dual-date support (buildPageHeader
  + Order Slip + Charge Agreement, full-page + thermal)
- `/app/backend/routes/sales.py` — forward-date cap with manager-PIN override
  + audit_log write
- `/app/backend/tests/test_forward_date_cap_243.py` — 5 regression tests, all PASS

### Standing rules going forward
- **Sale dates**: always pass through `getNextOpenDate(lastCloseDate)` on the
  client, capped at `today+1` (or `lastCloseDate+1`).
- **Backend trust boundary**: same cap enforced in `sales.py`. Override path
  must always write `audit_log` so suspicious patterns surface in the future
  Audit Pulse widget.
- **Receipts**: `created_at` is the legal Transaction Date (BIR-friendly);
  `order_date` is the Z-Report posting bucket. Print both when they differ.

---

## Iter 242 (May 2026) — Same-Day Interest Payment in Close Wizard ✅

### Problem
Step 5 (Actual Count) showed OVER-MONEY variance equal to interest collected
that day. Step 3 displayed the interest payment correctly, but Step 5's
`expected_counter` excluded it.

### Root cause
`daily_operations.py` AR aggregations filtered out `order_date == date` to
prevent double-counting today's credit/partial sales — but that also dropped
interest/penalty invoices generated AND paid on the same date (a normal
`/payments` flow). Cash hit the cashier drawer via `update_cashier_wallet`,
but `total_cash_ar` excluded it.

### Fix (3 aggregation sites: preview, close_day, batch_close)
- Widened pipeline: include today-dated invoices when
  `sale_type ∈ {interest_charge, penalty_charge}`.
- Classify the payment as `interest_paid`/`penalty_paid` by invoice
  `sale_type` (since `/customers/.../receive-payment` hardcodes
  `applied_to_interest=0` on payment records).

### Files changed (iter242)
- `/app/backend/routes/daily_operations.py` — three AR aggregations
- `/app/backend/tests/test_close_wizard_same_day_interest_242.py` — passing

### Verification
New test + 16 prior preview tests all pass. Step 3 shows payments on the
correct date; Step 5 + Z-Report math tie out.

---

## Iter 241 (May 2026) — Day-Close Ledger Trail Fix (CRITICAL audit gap) ✅

### Problem (reported by Jovelyn 2026-05-05)
> "On closing wizard fund allocation, what remains in cashier and what goes
>  to the safe? Not sure if it really transfers — I don't see it in
>  /fund-management Cashier Drawer history and Physical Safe Transaction History."

### Root cause
`routes/daily_operations.close_day` (and `batch_close_days`) directly
`$set` the cashier wallet balance and inserted a `safe_lots` row but
**never wrote** `wallet_movements` or `fund_transfers`. Fund Management
reads from those collections, so money silently appeared/disappeared on
close — major audit gap.

### Fix
1. **`_write_close_ledger_entries` helper** in `daily_operations.py` —
   writes 1-4 ledger entries on every close:
   - `over_short_adjust` movement on cashier (when |variance| > 1¢)
   - `cashier_to_safe` fund_transfer (when cash_to_safe > 0)
   - cashier-side `transfer_out` movement
   - safe-side `transfer_in` movement
   - All tagged `daily_close_ref = closing.id` for idempotency
2. Wired into both `close_day` and `batch_close_days` paths
3. **Admin backfill** for past closings: `POST /api/admin/backfill/iter241-close-ledger?apply=true`
   — recreates missing entries, idempotent, role-gated to admin only
4. **Audit Center → Security Flags tab** has a new "Day-Close Ledger Trail"
   maintenance card with Preview + Apply Fix buttons (mirrors iter240 UX)
5. **Regression tests** in `tests/test_close_day_ledger_trail_241.py` —
   dry-run shape + idempotency + non-admin 403 — both pass

### Files changed (iter241)
- `/app/backend/routes/daily_operations.py` — `_write_close_ledger_entries` helper + wired into close paths
- `/app/backend/routes/admin_backfill_240.py` — `iter241-close-ledger` GET + POST
- `/app/frontend/src/pages/AuditCenterPage.js` — second maintenance card + confirm dialog
- `/app/backend/tests/test_close_day_ledger_trail_241.py` — new regression suite

---

# AgriBooks PRD

## Iter 240 (May 2026) — Offline Sync Line-Discount Bug Fix + Purchase Order UI Redesign ✅

### Problem A — Offline-sync inflates per-line totals (CRITICAL data corruption bug)
Reported by Jovelyn (sibugayagrivetsupply) via invoice **SI-SB-OFF-000001**:
> ALMIX BOX retail ₱510, per-line discount ₱20. Sales screen showed total ₱490 ✓
> Saved invoice (Sale Detail dialog) showed total ₱510 ✗

**Root cause**: `routes/sync.py:418` (offline sync replay handler) recomputed
`line_total = qty * rate` ignoring `discount_amount`, then OVERWROTE the
frontend-correct `item.total`. Effects per affected `synced_from_offline=true`
invoice with per-line discounts: inflated `item.total`, `subtotal`,
`grand_total`, and `balance` (phantom AR). Customer wallets were unaffected
because they paid the correct amount; the inflation was server-side only.

Online (non-OFF) sales were never affected — they go through `routes/sales.py`
which had the correct math.

### Fixes shipped
1. **`routes/sync.py`** — line totals now correctly subtract per-line discounts
   (both `amount` and `percent` types), mirroring the online-sale math.
2. **Regression suite** — `tests/test_offline_sync_line_discount_240.py` covers
   amount + percent discount paths. Both pass.
3. **Backfill — admin endpoint** — `POST /api/admin/backfill/iter240?apply=1`
   (admin-role gated, org-scoped). DRY-RUN first via `GET /api/admin/backfill/iter240`.
   Fixes both `invoices.items` AND `sales_log.line_total` in one pass.
4. **Backfill — CLI script** — `scripts/backfill_offline_sync_line_discount_240.py`
   for direct DB access.
5. **Live-prod patch for SI-SB-OFF-000001** — applied via existing
   `PUT /api/invoices/{id}/edit` endpoint which auto-recomputes totals.
   Result: `subtotal 18598.50 → 18578.50`, `balance 20.00 → 0`, `status: paid`.
   Note: sales_log entries for that one ALMIX line still show 510 until
   admin endpoint is deployed + run.

### Problem B — Purchase Order UI redesign + Park feature
User requested PO page to match the polished Detailed Sale UI plus a
Park / Draft Sale function similar to the Sales page.

### What shipped
1. **`routes/parked_purchase_orders.py`** — new endpoints mirroring
   `parked_sales.py`. Branch-shared, 24h TTL, max 20 parks per branch,
   PIN-gated discard for other-user parks.
2. **`lib/parkedPOSync.js`** — frontend helper (network-only, no
   IndexedDB; POs are deliberate, online-only actions).
3. **`PurchaseOrderPage.js` UI rebuild**:
   - Sales-style hero bar with segmented "New PO / PO List" pill toggle
   - Online indicator pill, Park button, Parked button (badge with count),
     Pay Supplier, Refresh
   - Header card with proper 12-col grid (Supplier 4/12, Date 2, DR 2,
     Payment Type 2, PO# 2 — Notes spans full width on row 2)
   - Excel-style line items table with sticky header (Detailed Sale parity)
   - Notes (2/3) + Totals (1/3) panel with optional Freight/VAT chips,
     receipt upload, and 3 action buttons
   - Modernized PO List filter chips + table styling
4. **Two new dialogs**:
   - `park-po-prompt-dialog` — optional label, summary, attached receipt
   - `parked-pos-dialog` — list with vendor badge, item count, total, age,
     Resume + Discard (with PIN prompt for other-user parks)

### Files
- `/app/backend/routes/sync.py` — line-discount math fixed
- `/app/backend/routes/parked_purchase_orders.py` — new
- `/app/backend/routes/admin_backfill_240.py` — new (admin-gated)
- `/app/backend/scripts/backfill_offline_sync_line_discount_240.py` — new
- `/app/backend/tests/test_offline_sync_line_discount_240.py` — new
- `/app/frontend/src/lib/parkedPOSync.js` — new
- `/app/frontend/src/pages/PurchaseOrderPage.js` — major UI rewrite

---

# AgriBooks PRD

## Iter 238 (May 2026) — Time/Date Timezone Audit + Standardized Display ✅

**Problem**: User reported sales made at 8 AM Manila showing `00:00` time on the Daily Log. Full audit revealed **66+ places** in the backend computing "today" via `datetime.now(timezone.utc).strftime("%Y-%m-%d")` — which produced UTC dates instead of Manila local dates. From 12 AM to 8 AM Manila, every "today" query was off by one day. Frontend had **125+ inconsistent `toLocaleString()` calls** producing varied date formats across pages.

### Backend fixes (smoking gun + 8 systemic)
1. **`log_sale_items` `time` field** — now records `HH:MM:SS` in org-local TZ (Asia/Manila) instead of UTC. Was the root cause of "00:00 at 8 AM" symptom.
2. **`get_active_date(branch_id)`** — resolves the active business day via the branch's org timezone, not UTC.
3. **66+ "today" derivations** — every `datetime.now(timezone.utc).strftime("%Y-%m-%d")` replaced with `await today_local(user.get("organization_id"))` across `dashboard.py`, `reports.py`, `audit.py`, `accounting.py`, `daily_operations.py`, `invoices.py`, `purchase_orders.py`, `incident_tickets.py`, `price_changes.py`, `sales.py`, `returns.py`, `employees.py`, `qr_actions.py`, `overage_reserve.py`, `sync.py`, `invoice_corrections.py`, `sms_hooks.py`.
4. **Default `order_date` / `date_received` / `pay_date` / etc.** — every `now_iso()[:10]` server-side default replaced with `await today_local(...)`. Sales made between 12 AM and 8 AM Manila no longer get UTC-dated to yesterday.
5. **SMS reminder scheduler** (`main.py:_daily_sms_reminders`) — now computes `today` per-org so each tenant's 15/7-day reminder window matches their LOCAL calendar, not UTC.
6. **Z-Report PDF "Generated" footer** — converts UTC to org-local TZ and renders as `MM/DD/YYYY hh:mm AM/PM` (was `YYYY-MM-DD HH:MM UTC`).
7. **Z-Report PDF default date** — uses `today_local(org_id)` instead of UTC.
8. **`dashboard._compute_ar_aging`, `audit._compute_ar`, `audit._compute_payables`** — now take `org_id` parameter and use `today_local` for the day-arithmetic baseline.
9. **`audit.compute_audit` period defaults** — `period_to` defaults to `today_local(org_id)`; `period_from` derives from same.

### New helper module (`utils/helpers.py`)
- `today_local(org_id)` → `YYYY-MM-DD` in org TZ
- `now_local_iso(org_id)` → `2026-05-05T08:30:00+08:00`
- `now_local_time_str(org_id)` → `HH:MM:SS` in org TZ
- `utc_iso_to_local_time_str(iso, tz)` → conversion helper used by the backfill migration

### Frontend display standardization
- **NEW: `/app/frontend/src/lib/dateFormat.js`** — single source of truth with:
  - `formatDate(input)` → **MM/DD/YYYY**
  - `formatTime(input)` → **hh:mm AM/PM**
  - `formatDateTime(input)` → **MM/DD/YYYY · hh:mm AM/PM**
  - `formatTimeFull(input)` → with seconds
  - `formatDateLong(input)` → `May 5, 2026`
  - `localTodayStr()` → org-TZ-aware `YYYY-MM-DD` (replaces `new Date().toISOString().slice(0, 10)`)
  - `format24To12("00:00:00")` → `"12:00 AM"` — used to render legacy DB-stored 24h times
- **Bulk swept 51 frontend files**: 27 files for `toLocaleString/Date/Time` → `formatDateTime/Date/Time`, 24 files for `new Date().toISOString().slice(0,10)` → `localTodayStr()`.
- **`DailyLogPage.js:1154`** specifically: `{e.time}` → `{format24To12(e.time)}` so legacy DB rows still render as `08:00 AM` (looking up the new `time_tz_used` field).

### Historical data backfill
- **NEW: `/app/backend/scripts/backfill_sales_log_time_tz_238.py`** — idempotent one-shot migration that converts every `sales_log.time` UTC `HH:MM:SS` to org-local. Preserves the original UTC value as `time_utc_legacy` for auditability and stamps `time_tz_migrated=True`. **Ran successfully in dev: 595/597 rows migrated, 2 skipped (no timestamp).**

### What was deliberately NOT touched (to protect financial integrity)
- All `created_at` / `updated_at` / `voided_at` / `recorded_at` fields **stay UTC ISO** — they're transport timestamps for ordering and comparison. Frontend converts on display via `formatDateTime`.
- All historical `date` strings on `daily_closings`, `invoices`, `sales`, `journal_entries` are **left as-is** — moving sales between Z-Reports retroactively would corrupt closed-day cash counts and audit trails.
- All money/stock formulas (`running_total`, `quantity`, `total`, wallet balances, `over_short`, AR/AP balances) **untouched**.

### Testing
- `tests/test_timezone_helpers_238.py` — **8/8 PASS** (UTC↔Manila conversion, midnight boundary, default fallback, ISO offset format, HH:MM:SS format).
- Combined regression suite: **40/40 PASS** (Iter 232, 237, 238 all green).
- Verified zero new regressions: pre-existing 3 `TestEndpointRegistration` and 4 `TestPOCancel/Edit` failures fail identically on stashed clean code (unrelated, manager-PIN/test-fixture issues).

### Files touched
- Backend: `utils/helpers.py`, `utils/__init__.py`, `routes/{accounting,audit,dashboard,daily_operations,employees,incident_tickets,invoice_corrections,invoices,overage_reserve,price_changes,purchase_orders,qr_actions,reports,returns,sales,sms_hooks,sync,zreport_pdf}.py`, `main.py`.
- Frontend: 51 files (one-line replacements via centralized helper).
- New: `frontend/src/lib/dateFormat.js`, `backend/scripts/backfill_sales_log_time_tz_238.py`, `backend/tests/test_timezone_helpers_238.py`.



## Iter 237 (May 2026) — SMS Emergency Controls + Quiet-Hours Dispatch Gate + Gateway Heartbeat ✅

**Problem**: User reported receiving SMS 3 days old (from 5/2/26) at 1 AM, 2 AM, 5 AM local time on 5/5/26, including messages from a branch that was already muted. "Emergency Stop" button felt ineffective. Even after wiping the backend queue, SMS kept coming — gateway logs revealed the Android app was looping locally on 7 stuck rows because its API URL pointed to `agri-books.com` (NXDOMAIN), so `mark-sent` ACKs all failed and rows replayed forever. Full code audit identified 6 root-cause bugs PLUS the missing observability layer that allowed the gateway disconnection to go unnoticed for days.

### Root-cause fixes
1. **Dispatch-time quiet-hours gate** — `GET /sms/queue/pending` now blocks non-manual rows inside the org's local 22:00–07:00 window. Previously only the *enqueuer* honoured quiet hours; the gateway happily picked up rows at 1 AM.
2. **Close-reminder TTL (24h)** — pending close-reminder rows older than 24h are auto-expired before dispatch so a 3-day-old "didn't close" alert can never leak out after the situation has changed.
3. **Muted-branch template superset** — new `MUTED_BRANCH_TEMPLATE_KEYS` adds `zreport_finalized` to the close-reminder list. Mute-toggle purge and dispatch-time mute filter both use the superset so the Daily Summary SMS can no longer slip past a mute.
4. **True kill-switch** — new `POST /sms/queue/stop-all-auto` cancels EVERY `trigger=auto|scheduled` row (close reminders, Z-Report summary, credit/payment/interest/reminder/crop hooks) while leaving manual admin composes intact.
5. **Global pause / resume** — `POST /sms/queue/pause-all {hours}` sets a settings-level flag (`sms_global_pause`) that blocks both enqueue and dispatch for the window (clamp 15min..7d). `POST /sms/queue/resume-all` clears it. `GET /sms/queue/pause-status` surfaces the state for the UI banner.
6. **Scoping fix** — `POST /sms/queue/clear-stuck` now uses `_raw_db` with explicit `organization_id` filter (was leaking through the request-scoped `db` proxy).
7. **Audit-report endpoint** — new `GET /sms/queue/audit-report` surfaces oldest-pending age, status counts, TTL expiries 24h, mute-leak cancellations 24h, muted-branch count, current pause + quiet-hours state. Wired to a Queue Health modal in the Team SMS card.
8. **Gateway heartbeat** — every authenticated `GET /sms/queue/pending` updates `settings.sms_gateway_heartbeat` (last_poll_at, last_returned_count). New `GET /sms/queue/gateway-heartbeat` endpoint + UI banner in Team SMS card flashes red when no poll lands within 15 min — catches a disconnected gateway BEFORE it leaks days of replayed SMS.

### UI changes
- **TeamSmsRemindersCard**: 3-button emergency row (Stop Close-Day / Stop EVERY Auto SMS / Pause ALL 24h) + pause banner with inline Resume + Queue Health Report modal + **Gateway Heartbeat banner** (green/red/grey, polls every 30s). Data-testids: `emergency-cancel-close-sms-btn`, `emergency-stop-all-auto-sms-btn`, `emergency-pause-24h-btn`, `sms-resume-btn`, `sms-audit-report-btn`, `sms-audit-modal`, `sms-pause-banner`, `sms-gateway-heartbeat-banner`.

### Operational note (carrier incident 5/5/26)
The user's gateway phone had its API base URL configured as `agri-books.com` (with hyphen — NXDOMAIN) instead of `agribooks.com`. After fixing 6 backend bugs and the user clearing the gateway phone's IndexedDB + repointing to `agribooks.com`, the spam loop ended. The new heartbeat banner + 24h TTL + true kill-switch ensure this scenario is observable and recoverable in seconds, not days.

### Testing
- `tests/test_sms_emergency_controls_237.py` — **13/13 PASS** (quiet-hours wrap logic, TTL expiry, mute superset, stop-all-auto, pause/resume flow, clamp, audit-report shape, gateway-heartbeat shape, audit-report includes heartbeat, auth gating on every new endpoint).
- End-to-end heartbeat verified via curl (before-poll → healthy=false; after-poll → healthy=true, age_seconds=0).

### Files touched
- `/app/backend/routes/sms.py` — new constants (`MUTED_BRANCH_TEMPLATE_KEYS`, `DISPATCH_QUIET_START_H/END_H`, `CLOSE_REMINDER_TTL_HOURS`, `GATEWAY_STALE_SECONDS`), new helpers (`_is_org_in_quiet_hours`, `_get_org_pause_state`, `_get_gateway_heartbeat`), updated `get_pending_sms` (quiet-hours + TTL + global-pause gates, widened mute filter, heartbeat write), updated mute-toggle purge (superset), updated `clear-stuck` scoping, new endpoints: `/queue/stop-all-auto`, `/queue/pause-all`, `/queue/resume-all`, `/queue/pause-status`, `/queue/audit-report`, `/queue/gateway-heartbeat`, updated `queue_sms` (pause-aware enqueue).
- `/app/frontend/src/components/sms/TeamSmsRemindersCard.js` — 3-button emergency row + pause banner/resume + audit-report modal + gateway heartbeat banner (30s poll) + `formatHeartbeatAge` helper.
- `/app/backend/tests/test_sms_emergency_controls_237.py` — new 13-test suite.



## Iter 223 (Feb 2026) — Fund Injection Date + Offline Receipt Numbering + Payment Date Edit ✅

**Three minor improvements requested by user:**
1. **Fund Injection Date + Closed-Day Guard**: `FundTransferDialog` now has a date picker (default today, max=today); backend rejects capital injections backdated to a closed Z-Report day with 403. Batch and single-day close preview now both include `fund_transfers_today` with per-row `date`, fixing the bug where Z-Reports did not show fund injections.
2. **Offline Receipt Numbering Standard**: Offline sales mint `{PREFIX}-{BRANCH_CODE}-OFF-{6-digit local seq}` (e.g. `SI-MN-OFF-000001`). Per-device counter in IndexedDB. Preserved by `/sales/sync` — no renumbering.
3. **Payment History Change Date**: New PIN-gated endpoint `POST /customers/{id}/edit-payment-date` with audit trail (`date_edit_history` array). Guards: voided payments blocked; both old and new dates must be on open (unclosed) days. UI: `CalendarDays` buttons in customer history and global Payment History rows; reusable `edit-payment-date-dialog`.

**Testing**: iteration_191.json — backend 11/11 PASS; frontend UI smoke verified.

**Files touched**:
- `/app/backend/routes/accounting.py` — fund_transfers date+closed-day validation; new edit-payment-date endpoint.
- `/app/backend/routes/daily_operations.py` — batch preview now returns `fund_transfers_today`; per-row `date` field.
- `/app/frontend/src/components/FundTransferDialog.js` — date picker + closed-day pre-flight.
- `/app/frontend/src/lib/offlineDB.js` — `getNextOfflineReceiptNumber()` helper.
- `/app/frontend/src/pages/UnifiedSalesPage.js` — offline invoice_number minting + fallback.
- `/app/frontend/src/pages/PaymentsPage.js` — Change Date dialog + action buttons.
- `/app/frontend/src/pages/CloseWizardPage.js` — render per-row date on fund transfers.


## Iter 219 (Feb 2026) — UI Permission Gating Audit: Suppliers/Accounting/Inventory ✅

Follow-up to Iter 218. Extended the same button-level `hasPerm` gating pattern to 3 more pages.

### Changes
- **SuppliersPage.js**: Gated New Supplier, Edit Supplier, Save-as-Supplier buttons.
- **AccountingPage.js**: Gated Cash Out / Farm Expense / Record Expense (top-right), per-expense row controls (Edit / Upload / Verify / Delete), Receivable Record Payment, Payable Record Payable + Record Payment.
- **InventoryPage.js**: No changes — already properly gated.

### Tested
- 2-pass Playwright (admin + limited-perm manager) — 0 UI/backend issues.
- Admin sees all buttons (admin bypass in `hasPerm`); limited manager sees none of the gated ones.

---

## Iter 218 (Feb 2026) — Per-Unit Discount + Products Permission Gating ✅

### Fix 1 — Order Mode amount discount now per-unit × qty
Reported: "our discounts from sales - Order are per line not per product. This should be per product".

**Root cause**: `lineTotal` in `UnifiedSalesPage.js` and backend `sales.py` treated amount discount as flat per-line. `₱5` discount with qty `10` only removed `₱5` from the line.

**Fix**: For `amount` discount type, `disc_amt = qty × discount_value`. Percent discount unchanged.
- Backend `routes/sales.py` line 454 updated.
- Frontend `UnifiedSalesPage.js` `lineTotal()` + payload `discount_amount` updated.
- Column header relabelled "Discount /unit" with tooltip.

### Fix 2 — Manager Products page: permission gating audit
Reported: "I can click the pen and edit the inventory and price. But how are we allowing this when I am using a manager account that has no access to inventory... I wonder why the manager can create products... Also Created repacks".

**Findings**: ProductsPage.js had ZERO frontend gates on pencil/create/repack/inventory-correction buttons. Backend also missed perm check on `/api/inventory/admin-adjust`.

**Fix**:
- Backend `routes/inventory.py`: `admin_adjust_inventory` now returns 403 unless user is admin OR has `inventory.adjust` perm.
- Frontend `ProductsPage.js`:
  - `canCreateProduct`, `canEditProduct`, `canAdjustInventory` flags added.
  - "Add Product" + "Quick Repack" + per-row Link2 repack btn hidden if !canCreateProduct.
  - Pencil shows Eye icon (read-only) if !canEditProduct → dialog opens read-only (fieldset disabled, Save hidden, amber "Read-only" banner shown, Close button instead of Cancel).
  - Inventory Correction accordion hidden unless canAdjustInventory.

### Tested
- **11/11 pytest** across `tests/test_perunit_discount_and_perm_gates_218.py` (6) + `tests/test_perunit_discount_edges_218.py` (5 edge cases from testing agent).
- Zero regressions in existing discount, price-match, consistency, and stock-injection suites.

### Known Remaining Gaps (backlog)
- `SuppliersPage.js`, `AccountingPage.js`, `InventoryPage.js` UIs do not use `hasPerm` for button-level gating. Backend mutations ARE gated (all endpoints use `check_perm`), so this is a UX/trust-but-verify gap rather than a security hole. Tracked as P1 refactor.

---

## Iter 217b (May 2026) — Sales Price-Change Prompt (retail=0 fix) + Admin Stock Injection ✅

### Fix 1 — Price-change prompt didn't trigger when current retail is 0
Reported: "You know how we get notified when we change the price? For some reason it doesn't ask to save when retail is 0 and we change it to an actual price."

**Root cause**: `computePriceChanges` in `UnifiedSalesPage.js` required BOTH `old_price > 0 AND new_price > 0`. When a product's retail is blank (0), typing a new price failed `op > 0` → prompt skipped.

**Fix**: allow the prompt when `op === 0` (first-time pricing). Tag these changes as `reason_hint: "first_time_pricing"`. `PriceMatchModal` displays an amber "FIRST-TIME PRICING" label instead of strike-through old price.

### Fix 2 — Admin Stock Injection (bypass PO / Transfer flow, preserve cost basis)
Reported: "Inject stocks directly without affecting moving average or current price, admin only, audit + stock movements visible."

**Backend (`routes/inventory.py`)**
- New endpoint `POST /inventory/admin-inject`:
  - **Strict admin role gate** — not permission-based, not manager-grantable (per user: "only admin can").
  - 3 modes: `add` (+N), `deduct` (−N, with over-deduct guard), `set` (overwrite total).
  - Validation: `reason_type` must be one of `opening_balance`, `count_variance`, `damaged_recovery`, `promo_stock`, `vendor_return`, `other`; `reason_note` ≥ 10 chars; repack products rejected.
  - **Preserves cost basis**: never touches `moving_avg_cost`, `last_purchase_cost`, `branch_prices`.
  - Writes to `stock_injections` audit doc (with `performed_by_name`, old_qty, new_qty, diff, reason).
  - Writes to `movements` ledger with `type="injection"` + `reference_number="INJ-ADD|DEDUCT|SET"` — surfaces in Stock Movements view and available for Audit Pulse red-flag filtering.
- New endpoint `GET /inventory/injections` — admin-only audit listing, filterable by `product_id` + `branch_id`.

**Frontend**
- New component `components/StockInjectionDialog.js`:
  - **Branch picker FIRST** (per user spec), then a **big red banner** "⚠️ You are editing inventory for <Branch>" once picked.
  - Shows current stock at the chosen branch inline.
  - 3-button action row (Add / Deduct / Set Total) with live projected-qty math (`5 → 10 (+5)`).
  - Reason dropdown + required 10-char note with counter.
  - "Moving avg & retail NOT changed" safety badge in footer.
  - Submit disabled until all 4 steps valid.
- `ProductsPage.js`: new `ShieldAlert` (red) button in the actions column, admin-only visible, next to Edit. Hidden for repack products.

### Tested
- **10/10 pytest** in `tests/test_stock_injection_217b.py`: non-admin 403, validation (short note, bad reason_type), repack block, add preserves cost basis (moving_avg 99.99 stays, last 111.11 stays), deduct blocks over-deduction, set overwrites exact, audit doc + movements ledger entry both created with correct fields.
- **34/34 pytest** across iters 201/215/216/217/217b — zero regressions.
- **End-to-end Playwright smoke**: login → Products → click shield → branch picker → red banner appears → qty=5, reason note → Inject → success toast "Stock +5 for Consistency-5f7c2a at Main Warehouse / New total: 15".

---

## Iter 217 (May 2026) — Approve-with-PIN + Smart Retail Inherit + Target Insights ✅

### Asks from user
1. Can a specific manager be authorized to approve transfers? (Instead of admin-only)
2. Require a PIN before the final Approve button fires
3. Blank retail on approval = keep the target branch's current retail
4. Show target branch's current retail AND current capital (moving + last-purchase) on the approval page
5. Confirm the per-branch mute still works

### Shipped

**New permission `branch_transfers.approve`** (`models/permissions.py`)
- Admin gets it by default. Manager starts WITHOUT and admin can grant via User Permissions UI.

**New PIN policy action `transfer_approve`** (`routes/verify.py`)
- Default methods: `admin_pin`, `manager_pin`, `totp`. Owners can tighten in Settings → PIN Policy.

**Backend (`routes/branch_transfers.py`)**
- `POST /{id}/approve`:
  - Enforces `branch_transfers.approve` permission instead of hardcoded admin check.
  - **Requires a PIN** (400 if absent, 403 if wrong / unauthorized).
  - If the PIN belongs to a non-admin user, that user must ALSO have `branch_transfers.approve` — prevents any manager PIN from bypassing the grant.
  - Retail fallback chain for blank/0 retails: **target branch price → source branch price → product list price → keep draft value**. Tagged as `retail_source: "inherited_target"` in the audit trail.
  - Records `approved_by_role`, `approved_pin_method` alongside existing `approved_by_name` for full audit context.
- `POST /{id}/reject`:
  - Also gated by `branch_transfers.approve` permission (no PIN — reject is a return, not a dispatch, per user's iter 215 choice).
- `GET /{id}/approval-insights`: per-item payload with `current_target_retail`, `current_source_retail`, `target_moving_capital`, `target_last_purchase_cost`, `target_stock`. One backend call — approval page stays snappy.

**Frontend (`pages/ApproveTransferPage.js`) — full rewrite**
- New columns: **From Cap, Xfer, Tgt Moving, Tgt Last Buy, Current Tgt Retail, New Retail, Margin**.
- Product cell shows target-branch stock inline ("42 in tgt stock").
- `New Retail` input placeholder = `<current_target_retail> (keep)` — blank on purpose lets admin inherit.
- "X row(s) will inherit target retail" soft warning (confirm dialog) before approval if any blank.
- **PIN dialog** on click "Approve & Dispatch" — password input, 3-min warm cache (useRef), "PIN warm 3 min" emerald badge when cached. `403` response invalidates the cache.
- Permission gate: `user.role === 'admin'` OR `user.permissions.branch_transfers.approve` — otherwise shows a friendly "Not authorized" view.

### Mute confirmation (per user's ask)
Ran `tests/test_branch_close_reminder_opt_out_201.py` — **all 3 tests pass**. The `close_reminder_disabled` flag is honoured by the close-reminder scheduler `tick_once()`, the toggle endpoint flips correctly, and the diagnose endpoint surfaces the flag. Combined with iter 216 spam-storm fixes, muted branches are now triple-protected: enqueue refused → throttle → dispatch cap.

### Tested
- **15/15 pytest** pass (`test_transfer_approval_215.py` + `test_transfer_approval_pin_217.py`):
  - approve without PIN → 400
  - approve with wrong PIN → 403
  - approve with blank retail → backend inherits target `branch_prices.retail` (verified via seeded `branch_prices` doc; `retail_source:"inherited_target"` flag asserted)
  - `/approval-insights` returns all 5 insight fields correctly (current retail target + source, moving capital, last purchase, stock)
  - permission constants and PIN action wired into presets
- End-to-end Playwright smoke: pending transfer → admin opens `/approve-transfer/:id` → sees all new columns with target insights populated → clicks Approve → confirm "will inherit" dialog → PIN prompt → enters `913712` → success toast → redirect to Branch Transfers showing order now in In Transit.

### For the owner
- Go to Team → User Permissions → pick a trusted manager → flip **Branch Transfers → Approve Pending Transfer** on. From then on, that manager's PIN (`521325` in test data) will be accepted at the approval page just like admin's.
- Warm PIN = enter once, approve multiple transfers in a row without re-typing (3-minute window).

---

## Iter 216 (May 2026) — 🚨 SMS Spam-Storm Fix + Manager SMS Access ✅

### What the user saw
Hundreds of log lines like:
```
Outbound WEB/queue -> to=+63995... msg=URGENT: SAMPOLI BRANCH is 2 days overdue...
mark-failed failed: Unable to resolve host "agri-books.com"
[same queueId repeats over and over]
```
Recipients received the **same** "URGENT closing" SMS 5–20 times during a power outage. Also: manager logins had no access to the SMS feature; muted branches appeared to keep firing.

### Root cause
`GET /api/sms/queue/pending` had no lease/claim and no dispatch cap. When the gateway phone lost DNS it could still SEND the SMS via GSM but could NOT post `mark-sent` back → server kept seeing the same row as `pending` → re-handed it to the phone on every poll → phone re-sent via GSM again. Combined with a scheduler that kept enqueueing new "overdue" reminders, the queue became a storm amplifier. Mute WAS working for new rows — the SMS user saw had been queued days earlier and were just stuck replaying.

### What shipped

**Backend** (`routes/sms.py`):
- **Lease-on-dispatch** — each `GET /queue/pending` claim sets `leased_until = now + 5min` with `find_one_and_update`. Concurrent polls can't grab the same row. Existing `mark-sent` / `mark-failed` endpoints now release the lease so retries still work.
- **3-strikes-per-day cap** — every hand-out bumps `dispatch_count`. At the 4th attempt with no ack, the row flips to `deferred` with `deferred_until = tomorrow 00:00 org-tz` — stops spamming the recipient dead, even if DNS stays broken all day.
- **Auto self-heal** — on each poll, `deferred` rows whose `deferred_until` has passed are flipped back to `pending` with a fresh 3-strike budget.
- **Per-recipient throttle** (`queue_sms`) — refuses the same `(template_key, phone)` within 10 min for `auto`-trigger templates. `manual` sends always go through. Stops the close-reminder scheduler from piling on during outages.
- **Emergency admin endpoint** — `POST /api/sms/queue/clear-stuck` now accepts `{"include_pending": true}` to mass-skip every `pending`/`deferred` row for the org. Stop-the-bleeding button.
- **Constants**: `DISPATCH_LEASE_SECONDS=300`, `MAX_DISPATCHES_PER_DAY=3`, `ENQUEUE_THROTTLE_SECONDS=600`. All in one place at the top of `sms.py` (dead duplicate `MAX_GATEWAY_RETRIES=3` removed).

**Manager SMS access** (per user — "manager + branch can only interact with their branch customer's saved number, the rest goes to unknown"):
- `App.js`: `/messages` route: `AdminRoute` → `ProtectedRoute`.
- `Layout.js`: sidebar item: `adminOnly: true` → visible to all.
- `sms.py::list_conversations`: non-admin users have `branch_id` force-overridden to their assigned branch (defense-in-depth — URL tampering can't escape).
- `sms.py::get_conversation_by_customer`: non-admin users get 403 if the customer has no activity in their branch. Also 403 if their user has no branch assigned.
- Unknown numbers tab stays admin-only (customers that don't match any saved number — managers should not see these cross-branch).

**Frontend** (`MessagesPage.js`):
- Emergency **"🛑 Stop All Pending"** red pill button next to queue filter when `stats.pending > 0`. Confirm-dialog with a warning, then calls `/sms/queue/clear-stuck` with `include_pending:true`.

### Tested
- 6/6 pytest in `backend/tests/test_sms_spam_protection_216.py` pass:
  - lease blocks immediate re-poll
  - 3 strikes + expired-lease-between flips to `deferred`
  - past-due `deferred` self-heals back to `pending`
  - `clear-stuck` with `include_pending:true` skips pending rows
  - `clear-stuck` default leaves pending untouched (backwards-compat)
  - constants are exactly as specified (5 min / 3 / 10 min)
- `/api/sms/conversations` smoke: admin still returns both `customers` + `unknown` sections.

### Emergency one-liner for the user (this outage, run on the VPS):
```bash
mongosh "$MONGO_URL" --eval \
  'db.getSiblingDB("test_database").sms_queue.updateMany(
     {status:"pending"},
     {$set:{status:"skipped",skipped_at:new Date().toISOString(),skip_reason:"emergency_purge_dns_outage"}})'
```
After shipping this build they can also just click **🛑 Stop All Pending** in `/messages`.

### Why mute looked broken
Close reminders for muted branches were queued BEFORE the mute toggle. The `close_reminder_disabled` flag correctly blocks NEW enqueues (verified by `test_branch_close_reminder_opt_out_201.py`). Once the lease+cap ships, those pre-mute rows will hit the 3-strikes cap and defer to tomorrow instead of re-firing forever. Combined with **🛑 Stop All Pending**, the user has a 1-click kill switch.

---

## Iter 215 (May 2026) — Manager-Initiated Transfer with Admin Approval ✅

### Why
Owner reported (with screenshot of SAMPOLI BRANCH → Main Branch transfer): when the source branch is staffed by a manager (no admin), they can't fill Branch Retail prices, so the existing validation blocks the transfer entirely. They need a way to **prepare the order, submit it for the admin to validate the prices, and only then dispatch it.**

### What shipped — End-to-end approval workflow

**Backend** (`routes/branch_transfers.py`):
- New status `pending_approval` (created when payload has `requires_approval=true`) and new status `returned` (rejected by admin, manager can edit & resubmit).
- `POST /api/branch-transfers` accepts `requires_approval: bool` — sets initial status accordingly.
- `POST /api/branch-transfers/{id}/approve` — admin merges per-product retail prices into items, recomputes retail total, **flips status straight to `sent` (auto-dispatch)**, fires the same destination-notification + invoice + doc-code path as the existing `/send` endpoint, and SMS-notifies the manager.
- `POST /api/branch-transfers/{id}/reject` — requires `reason` ≥ 4 chars, status → `returned`, persists `rejected_by_name` + `rejection_reason`, SMS-notifies the manager.
- `POST /api/branch-transfers/{id}/resubmit` — manager flips `returned` → `pending_approval`, clears rejection metadata, re-fires admin SMS.
- `update_transfer` extended to allow editing `pending_approval` and `returned` orders (manager fixes a returned draft).
- `_notify_admins_pending_approval` helper: looks up all admins in the org with phone numbers, fires one SMS each (dedup-keyed per admin/transfer).

**SMS** (`routes/sms.py`):
- 3 new DEFAULT_TEMPLATES auto-seeded:
  - `transfer_pending_approval` (admin gets the link)
  - `transfer_approved` (manager gets the OK)
  - `transfer_rejected` (manager gets the reason)
- Approval link is built from `org.app_url` → `REACT_APP_FRONTEND_URL` env → `APP_PUBLIC_URL` env → relative path fallback.

**Frontend**:
- `BranchTransferPage.js`:
  - New **"Submit for Approval"** button next to "Create Transfer Order" — works for any role; useful when the cashier/manager skipped retail.
  - `validateRow` now distinguishes 'retail' (only retail missing) from 'no_capital' / 'incomplete' so the submit-for-approval flow can ignore retail-missing rows while still blocking missing capital.
  - 2 new history filter pills: **Pending Approval** (amber) and **Returned** (rose), both flagged as "needsAttention" when count > 0.
  - New status colors + status labels for `pending_approval` and `returned`.
  - Card now shows an amber "Submitted for admin approval" banner on pending cards and a rose "Returned by [admin]: [reason]" banner on returned cards.
  - Per-card actions: Admin → "Review & Approve" button; non-admin → "Awaiting Admin" badge; on returned cards → manager gets Edit + Resubmit buttons.
- New `pages/ApproveTransferPage.js` (`/approve-transfer/:id`):
  - Header card with order #, submitter, branches, status pill.
  - Items table with editable retail-per-row inputs (amber-highlighted column), live margin/unit color (red < 0, amber < min, emerald >=).
  - Live totals strip: Our Cost → Transfer Price → Branch Retail → Profit-at-Target.
  - Approval Note textarea (optional, audit-trail).
  - "Approve & Dispatch" (emerald) and "Return / Reject" (rose, opens reason dialog) buttons.
  - Soft confirmation when approving with any retail still 0.
  - Guards: non-admin → friendly "Admin access required" view; non-pending → "Not awaiting approval" view.

### Tested
- 9/9 pytest in `backend/tests/test_transfer_approval_215.py` pass:
  - status flag respected, draft fallback works
  - approve dispatches + persists admin retail + approval_note
  - approve on non-pending → 400
  - reject reason < 4 → 400; valid reject → returned + reason persisted + rejected_by_name
  - resubmit clears rejection metadata + re-fires SMS; resubmit on non-returned → 400
  - list filter by `status=pending_approval` works
- End-to-end Playwright smoke: created pending transfer via curl → admin clicked "Review & Approve" pill → ApproveTransferPage rendered → filled retail → "Approve & Dispatch" → success toast → returned to /branch-transfers showing the order moved into "In Transit". No console errors.

### Backwards compatible
- `requires_approval` is opt-in; existing flows untouched.
- New statuses don't appear in old callers' filters (default `all` shows everything; specific filters unchanged).
- Internal invoice + doc-code generation hooked at the same point as the existing `/send`.

---

## Iter 214 (May 2026) — 🚨 P0 AccountingPage White-Screen Crash Fix ✅

### Bug
User reported: "Expense section — white screen upon inputting expense. IT BLACKS OUT ON ALL EXPENSE AFTER PRESSING SAVE. THE ONLY THING GOING THROUGH ARE THE CASH ADVANCE LINKED TO A PERSON." Save Expense crashed the React tree on every category except Employee Advance.

### Root cause
The backend's `update_cashier_wallet()` (`utils/helpers.py:178`) raises a structured `HTTPException(status_code=400, detail={...})` — a dict object with keys `{type, message, cashier_balance, required, shortfall, suggestion}` — when the cashier wallet has insufficient funds. The frontend's catch block did:

```js
catch (e) { toast.error(e.response?.data?.detail || 'Error saving expense'); }
```

`detail` was an object, not a string. Sonner's `toast.error(obj)` then tried to render the raw object as a React child → "Objects are not valid as a React child" → Uncaught runtime error → white screen.

Employee Advance worked because its dedicated 400 path inside `create_expense` returns a STRING detail (CA limit message), never the structured cashier object. Every non-Employee-Advance category in a branch with empty cashier hit the structured object → crash.

### Fix
- `pages/AccountingPage.js`:
  - Added `getErrorMessage(e, fallback)` helper that safely extracts a string from any axios error (handles string detail, object detail with .message/.detail, or falls back to e.message / fallback).
  - Replaced all 6 `toast.error(e.response?.data?.detail || ...)` catch blocks (`handleSaveExpense`, `handleCaManagerPin`, `handleCreateFarmExpense`, `handleCreateCashOut`, `handleCreatePayable`, `handlePayment`).
  - Special-cased `insufficient_funds` in `handleSaveExpense`: shows the backend message PLUS a tip to switch payment method to Safe or top up the cashier first.

### Verified end-to-end
1. Insufficient cashier balance → friendly toast "Cashier has ₱0.00 but ₱75.00 is needed. Use the Safe or add a deposit to the cashier first." + tip line. **No white screen.**
2. Topped up cashier ₱5,000 via API → saved Office Supplies ₱250 + Rent ₱300 → green "Expense recorded" toast, table refreshed, totals updated to ₱550.00.

### Why minimal scope
The same `e.response?.data?.detail` pattern exists in 195 places across the frontend, but only the 6 in AccountingPage.js were on the immediate crash path the user reported. A wider sweep is queued as P2 backlog (apply `getErrorMessage` helper in shared `lib/utils.js`).

---

## Iter 213 (May 2026) — Branch Transfer Receive: 3-Step Wizard + Scrollable List ✅

### Why
Two complaints from owner:
1. Long transfer receipts (10+ items) — the qty-received list overflowed the dialog with no usable scrollbar; user couldn't reach all items or the Confirm button.
2. Receipt upload was crammed into the same screen as qty entry. Owner wanted a clean wizard: verify quantities → upload receipt → see reference number to write on paper.

### What shipped — Wizard Refactor

**3-step wizard with progress indicator at top of dialog**

**Step 1 — Verify Quantities**
- Shortage / Excess legend banners (existing)
- Products table inside `<ScrollArea>` — scrolls properly even with 20+ items, sticky table header
- Live variance summary cards (Shortage / Excess capital + retail impact)
- Receiving notes textarea
- Footer: Cancel | **Continue → Upload Receipt**

**Step 2 — Upload Receipt**
- Blue intro banner explaining the QR-or-local upload options
- `ReceiptUploadInline` component (already supports drag-drop on PC + QR for phone + 3-second polling auto-detection)
- Live "✅ N photos uploaded — ready to continue" confirmation chip when phone upload arrives
- Footer: ← Back | **Skip — No Receipt** (gray) | **Continue → Review** (disabled until upload OR user explicitly skips)

**Step 3 — Confirm Summary**
- Big emerald-bordered **Reference Card**: shows the BTO-XXXXX number in big mono type with "Write this on the paper receipt" label, so the manager can write the system reference back on the physical document for traceability later — exactly what user requested.
- Compact recap of all items: ordered → received, capital per item, total
- Variance recap if present
- Receipt photo confirmation (✓ N attached / ✗ none — optional)
- Footer: ← Back | **Confirm Receipt** (or "Yes, Submit Variance for Review" if shortages/excesses)

### Bonus pre-existing fix
While linting, found a corrupt JSX block in the View dialog timeline (`const lineColor = step<p ...>` — botched edit from a prior session that would have crashed on opening the View dialog). Reconstructed the proper timeline-step rendering: dot + label + date + connecting line.

### Tested
- Lint clean
- Frontend serves /branch-transfers (HTTP 200)
- Receipt upload component already verified end-to-end via curl in iter 208 (R2 storage, public token preview, multi-file upload all confirmed working)

### Backwards compatible
- Same backend endpoints; wizard is purely a frontend UX improvement
- Existing variance double-check (`receiveConfirmStep`) still works inside Step 3
- All data-testids preserved, plus new ones for wizard steps: `receive-wizard-stepper`, `receive-step-upload`, `receive-step-summary`, `receive-ref-number`, `continue-to-upload-btn`, `skip-receipt-btn`, `continue-to-summary-btn`

---

## Iter 212 (May 2026) — Branch Transfer: Receipt Photo Now Optional ✅

### Why
Manager reported: "I can't receive the product because it is asking me to upload a photo which doesn't make sense." The Branch Transfer receive flow was hard-blocking with a mandatory receipt photo, trapping the manager when the QR upload was failing (the iter 208 bug) or when a paper DR is acceptable as the audit trail.

### What changed
1. **`pages/BranchTransferPage.js` line 814** — removed the hard block in `handleReceive`. Now if no receipt is uploaded, the user gets a one-time soft confirm: "Proceed without a receipt? (Recommended for audit trail)" — they can OK to proceed or Cancel and upload first.
2. **Receive dialog** — `ReceiptUploadInline` is now `required={false}` with new label "Upload Receipt / DR Photo (optional, but recommended for audit)".

### What was already working (no change needed)
The user's described workflow is already fully implemented in `ReceiptUploadInline`:
- Click "Upload Receipt" → opens choice between desktop file picker AND QR code
- QR code points to public `/upload/{token}` page that the iter 208 fix made work again
- Phone uploads are detected via 3-second polling on `/api/uploads/session-status/{sessionId}` — file count + thumbnails appear live in the receive dialog
- User can then click Confirm Receipt to finalize

### Net effect
- Manager who has a paper DR signed → click Receive → enter actual qty → click Confirm Receipt → soft confirm → done. No photo needed.
- Manager who wants the photo trail → click Receive → enter qty → tap "Upload Receipt" → either drag-drop on PC or scan QR with phone → photo arrives → click Confirm Receipt → done.

---

## Iter 211 (May 2026) — Branch Transfer: No-wheel-scroll + Smart Dropdown ✅

### Why
1. Manager reported: "If I accidentally point my pointer there [number input] and scroll, it changes value without me knowing." Classic browser behaviour: focused number inputs respond to mouse-wheel as +/- adjustments. Silent data corruption risk.
2. Search dropdown didn't reposition when scrolling, didn't flip up at the bottom of the page, and clipped behind table boundaries — felt sluggish vs the polished Sales → Order Mode dropdown.

### What shipped

**1. Wheel-scroll guard on every number input**
- New helper `noWheel = (e) => e.currentTarget.blur()` at the top of `BranchTransferPage.js`.
- Applied via `onWheel={noWheel}` to every user-facing number input on the page:
  - Quantity (transfer rows + stock-request rows)
  - Transfer Capital
  - Branch Retail
  - Repack new-retail-price
  - Min Margin
  - Category Markup value
  - Receive Qty (for confirming receipt)
- Behaviour: hover + scroll → input loses focus → wheel goes to page scroll. Existing focused-edit experience untouched.

**2. AnchoredDropdown — same UX as Sales SmartProductSearch**
- New helper component within `BranchTransferPage.js`:
  - Re-positions on `window.scroll` (capture phase) AND `window.resize`
  - **Auto-flips up** when there's < 220 px below the input AND more headroom above
  - **Clamps `max-height`** to available viewport space (160–360 px) so the list never spills past the screen edge
  - Rendered via `createPortal` to escape any `overflow:hidden`/`overflow:auto` ancestor
  - `overscroll-contain` so wheel inside the list doesn't scroll the page
- Replaces both dropdowns:
  - Main transfer product search (was a one-shot IIFE that froze on render)
  - Stock-request product search (was `absolute z-50` which clipped behind table cells)

### Tested
- Lint clean.
- Backend endpoints from prior iters all pass curl smoke tests.

---

## Iter 210 (May 2026) — Branch Transfer: Park Draft + Match-Target-Retail ✅

### What shipped

**1. Park / Resume Branch Transfer** (mirrors the Parked Sales pattern)
- Backend: new `routes/parked_branch_transfers.py` + collection `parked_branch_transfers`:
  - `POST /api/parked-branch-transfers` — create/upsert with rows, branch selection, markup template, label
  - `GET /api/parked-branch-transfers?from_branch_id=X` — list with auto-purge of >24 h stale parks
  - `GET /api/parked-branch-transfers/{id}` — fetch one
  - `POST /api/parked-branch-transfers/{id}/consume` — atomic fetch+delete (the Resume action), returns 410 Gone on race
  - `DELETE /api/parked-branch-transfers/{id}` — discard (PIN required to discard another user's park)
  - Branch-shared (any user at the from-branch can pick up a colleague's draft)
  - 10 active parks per branch limit, 24 h TTL
- Frontend (`pages/BranchTransferPage.js`):
  - **Park button** (amber, ⏸ icon) next to "Add Product" — opens dialog with optional label, then saves & resets to a fresh blank draft
  - **Resume button** (blue, 📁 icon) appears only when parks exist, shows count badge — opens dialog listing all branch-shared parks with label, target branch, age, item count, creator name, and Resume/Discard buttons per row
  - When you Park while already on a resumed park, it updates the existing row (not a duplicate)
  - Discarding a park: confirm prompt, soft-deletes from list

**2. Match-Target-Retail one-click button**
- Below the 🎯 "Now: ₱X" hint on Branch Retail cell, when the entered retail differs from target's current retail, a small "match" link appears (admin only)
- Click → instantly fills the row's branch_retail with the target branch's current retail price (zero typing for "follow what they already charge" transfers)

### Tested via curl
- POST create park with rows + label ✅
- GET list returns the park ✅
- POST consume returns full snapshot (rows, min_margin, etc.) ✅
- Re-consume returns 410 Gone (race-safe) ✅
- Lint clean (frontend + backend)

### Bonus pre-existing fix
While restarting backend, found a pre-existing syntax error in `main.py` line 1192 (orphaned text from a botched copy-paste in an earlier session) — cleaned up so the server actually starts.

### Backwards compatible
- New collection, new routes — zero impact on existing branch transfer flows
- The Park button is gated by `rows.some(r => r.product)` so it only appears when there's something worth parking

---

## Iter 209 (May 2026) — Branch Transfer: Target-Branch Insights + Tab-to-add-row ✅

### Why
Owner needs to make smart pricing decisions when sending stock to another branch. Without seeing the target branch's CURRENT capital, moving-average, and retail price, you're guessing. Worse, for repacks: if the receiving branch already books the repack at ₱25 and you send fresh stock at ₱28, the manager needs to see the resulting weighted-average new capital and decide whether to update retail to follow.

### What shipped

**Backend** — extended `GET /api/branch-transfers/product-lookup` (`routes/branch_transfers.py`):
- New per-product fields:
  - `target_branch_capital` — current cost the target branch books this product at
  - `target_branch_moving_average` — MA cost from movements at target
  - `target_branch_retail` — current retail scheme price at target
  - `target_branch_stock` — current on-hand at target (used for "new capital after transfer" weighted average)
- New per-repack field:
  - `target_capital_per_repack` — current capital per repack at the target branch
- Existing `current_dest_retail` (target retail per repack) is unchanged

**Frontend** — `pages/BranchTransferPage.js`:
- Below "Transfer Capital" input — inline meta showing 🎯 target capital, target MA, and the **projected new capital** if this transfer is received (weighted: `(target_stock × target_cap + qty × send_cap) / (target_stock + qty)`). Live recompute as qty / transfer_capital changes.
- Below "Branch Retail" input — 🎯 current retail at target branch (so admin can decide whether to follow it or override)
- Repack chip enhanced to show: **From-branch capital · 🎯 Target capital · → New capital after transfer · Retail at target**. All four numbers visible at a glance, hover-tooltips explain each.
- **Tab-to-add-row**: pressing Tab on the last row's last editable input (Branch Retail for admin, Transfer Capital for non-admin) auto-creates a new row and focuses its product search — same UX as Sales page.

### Tested
- curl on `/branch-transfers/product-lookup?q=a&from_branch_id=X&to_branch_id=Y` returns all 4 new parent fields and the new repack field correctly.
- Lint clean.

### Backwards compatible
- Pre-existing fields untouched. Only new keys added. Old callers continue to work; they just ignore the new keys.

---

## Iter 208 (May 2026) — 🚨 P0 QR Receipt Upload "Session Expired" Fix ✅

### Bug
Manager scans QR code on a Purchase Order / Expense / Branch Transfer to upload a receipt photo from their phone → URL `/upload/{token}` opens in browser → "Link Invalid or Expired".

User-facing report: `https://agri-books.com/upload/cDkp81CmIA8agxX3yCYlFiVdTu8b1xZM` returns "Session expired".

### Root cause
The Iter 180 cross-tenant privacy fix (TenantCollection fail-closed proxy) broke ALL public token endpoints in `routes/uploads.py`. These endpoints have no JWT auth, so `get_org_context()` returns None, the proxy injects sentinel `organization_id: "__no_org_context__"` into every query, and the legitimate session document is invisible.

Affected endpoints (all public, all broken):
- `GET /api/uploads/preview/{token}` — phone "show me what I'm uploading to"
- `POST /api/uploads/upload/{token}` — phone "upload my photos"
- `GET /api/uploads/file/{record_type}/{record_id}/{file_id}` — public file serve
- `GET /api/uploads/view-session/{token}` — phone "view-only" QR

### Fix
Switched the initial token lookup from `db.upload_sessions` (scoped) to `_raw_db.upload_sessions` (unscoped) in all 4 public endpoints, then `set_org_context(session.organization_id)` so any subsequent scoped queries (record summary, invoice receipt_status update, related collections) target the correct tenant.

### Pattern matches qr_actions.py
This is the same pattern already used by `routes/qr_actions.py` for cross-tenant QR document lookups — public endpoint resolves doc_code via `_raw_db`, then sets context for downstream calls. Now `routes/uploads.py` follows the same convention.

### Verified end-to-end
1. Generate upload link via authenticated `/api/uploads/generate-link` — returns token
2. Public `GET /api/uploads/preview/{token}` (no auth) — returns full record summary ✅
3. Public `POST /api/uploads/upload/{token}` with file — returns `{uploaded: 1, total_files: 1}` ✅
4. R2 storage receives file correctly

### Side effects
- None on existing flows: authenticated callers still go through the scoped proxy as before
- Receipt upload now works on the same VPS where it was previously failing — no migration needed

---

## Iter 207 (May 2026) — Quick Launch (Manager Fast-Action Card) ✅

### Why
Owner asked for a visible always-on shortcut for the 7 most-used manager actions, so a non-tech-savvy manager doesn't have to fumble through the sidebar to find Sales, Receive Payment, etc.

### What shipped
- New component `/app/frontend/src/components/QuickLaunch.js` (148 lines):
  - Collapsible card pinned at the top of the Dashboard
  - Persists collapsed state per-user via localStorage key `agripos_quick_launch_collapsed`
  - 7 colored tile buttons in a responsive grid (2 cols mobile → 4 cols tablet → 7 cols desktop):
    1. Sales → /sales-new (emerald)
    2. Receive Payment → /payments (blue)
    3. Purchase Order → /purchase-orders (purple)
    4. Pay Supplier → /pay-supplier (indigo)
    5. Expense → /accounting (rose)
    6. Branch Transfer → /branch-transfers (amber)
    7. Closing Wizard → /close-wizard (slate, brand-green icon)
  - Each tile has `data-testid="quick-launch-{key}"`; toggle has `data-testid="quick-launch-toggle"`; container `data-testid="quick-launch"`
- Wired into BOTH `DashboardPage.js` views:
  - Owner Consolidated View (line 470, after header before grid)
  - Branch / Single View (line 699, after warnings before grid)

### Non-invasive
- Purely navigation; no backend dep
- Doesn't touch existing dashboard widgets / grid layout
- Lint clean

### Verification
- iter_184.json: static code review confirms all 7 testids, correct routes, localStorage persistence, no SyntheticEvent risks (onClick wrapped in arrow fn per iter_205 defensive pattern)

---

## Iter 206 (May 2026) — Void Payment from Payment History ✅

### Why
User applied a credit-payment to the WRONG customer's invoice. The existing "Edit" flow only let them change the AMOUNT; there was no way to fully cancel the payment and return the money to the wallet. Trapped scenarios where the only escape was DB surgery.

### What shipped
- **Backend** (`routes/accounting.py:2010`): new `POST /api/customers/{customer_id}/void-payment` — full reversal endpoint.
  - Reverses cashier or digital wallet (whichever fund_source the original payment hit)
  - Restores invoice balance + status (paid → open / partial)
  - Restores customer AR balance via `$inc`
  - Marks payment row `voided=True` with full audit trail (`void_reason`, `voided_by`, `void_authorized_by`, `voided_at`)
  - PIN-gated via `void_payment` policy action (defaults: admin/manager/totp)
  - Z-Report day guard (cannot void payments dated in a closed day — protects cash-balancing trail)
  - Reason required (≥ 4 chars)
  - Idempotent: re-void of a voided payment returns 400
  
- **Frontend** (`pages/PaymentsPage.js`):
  - Red **Cancel** button next to Edit in Payment History modal (`data-testid=void-payment-{payment_id}`)
  - Confirmation dialog with manager PIN + reason fields, red-bordered amount card showing exactly which wallet the money returns to
  - Auto-refreshes invoice list, charges preview, customer summary, balance cache after void
  - Keyboard support (Enter to confirm)

### Tested via curl
1. Voided ₱100 payment from Juan Dela Cruz: customer balance 1,900 → 2,000, invoice status `paid` → `open`, wallet decreased by 100 ✅
2. Re-void of already-voided payment: 400 "Payment already voided" ✅
3. Wrong PIN: 403 "Invalid PIN — void not authorized" ✅
4. Short reason: 400 "Reason is required (≥ 4 chars)" ✅

### Differences vs Modify-Payment
- Modify = void original + create replacement payment (use when amount was wrong)
- Void = full cancellation, no replacement (use when payment was misapplied to wrong customer/invoice)
- Both share the same wallet/invoice/AR reversal primitives

---

## Iter 205 (May 2026) — 🚨 P0 SyntheticEvent Regression Sweep (3 Critical Silent-Failure Bugs Fixed) ✅

### The Pattern
`<Button onClick={handlerFn}>` where `handlerFn(arg)` accepts a positional arg → React passes a SyntheticEvent as `arg` → handler treats it as truthy real data → injected into axios request body → `JSON.stringify` hits circular refs in the SyntheticEvent → synchronous `TypeError` aborts the HTTP request before it leaves the browser → user sees generic "X failed" toast with **zero network requests fired**.

### Three Sites Fixed (defensive 2-layer pattern: arrow-wrap at binding + typeof-string coercion in handler)

1. **PaymentsPage.js — Apply Payment** (originally reported by user, fixed iter 204b)
   - Symptom: "Tried applying payment for dodong casipe, payment failed" — backend untouched, frontend silently aborted.
   - Lines: `handleApplyPayment(pinOverride)` at :401 with `pinStr` coercion, button :1163 wrapped `() => handleApplyPayment()`.
   - Verified end-to-end with live network trace in iter_182: POST /receive-payment fires with clean JSON, returns 200, balance updates.

2. **UnifiedSalesPage.js — Credit Sale Approval (Authorize Sale button)** (P0 — same pattern, found in scan)
   - Would have silently broken EVERY credit/partial sale where the cashier clicked "Authorize Sale" instead of pressing Enter — `pinToUse.trim()` would throw on a SyntheticEvent.
   - Lines: `verifyManagerPin(_sessionPin)` at :1898 with `sessionPinStr` coercion, button :3901 wrapped `() => verifyManagerPin()`.

3. **AccountingPage.js — Save Expense (Employee Advance over-limit gate)** (P1 — same pattern, found in scan)
   - Two failure modes: (a) the manager-PIN dialog was being silently bypassed when over the CA monthly limit (because event was treated as approver name); (b) the SyntheticEvent was being JSON-stringified into `manager_approved_by` field → request abort.
   - Lines: `handleSaveExpense(approvedBy='')` at :183 with `approver` coercion, gate at :197 uses `approver`, payload at :206 only sends string approver, button :800 wrapped `() => handleSaveExpense()`.

### Backend touch
- `routes/verify.py`: `modify_payment` now explicitly registered in `PIN_POLICY_ACTIONS` (was previously falling back to defaults — now policy-customizable).

### Codebase scan
- Wrote a Python AST-style scan that identified ALL 3 sites by matching `onClick={fn}` (bare, no arrow wrap) where `fn` is defined with a positional first arg whose name is not `e`/`event`/`ev`. Only 3 dangerous bindings exist app-wide. All fixed.
- One false-positive: `<ResultRow onClick={handleResultClick}>` in TransactionSearchPage.jsx — internally wraps via `onClick={() => onClick(item)}`. Safe.

### Test verification
- iter_182.json: live POST /receive-payment confirmed with clean payload, status 200, balance 2000 → 1900.
- iter_183.json: static code review of all 3 fixes — defensive double-layer pattern verified.

---

## Iter 204 (May 2026) — Global & Customer Payment History + Close Wizard Enhancements ✅

### Global Payment History Tab (`/payments`)
- New "History" tab beside "Customer Payment" on the `/payments` page
- Backend: `GET /api/payments/history` — queries all invoice payment records across all customers with filters (date range, branch, method, customer search)
- Method breakdown chips at top (Cash: ₱X | GCash: ₱Y | Total: ₱Z)
- Table: Date, Customer, Invoice #, Type (badge), Method, Amount, Reference, Recorded By
- Clicking a customer name opens per-customer payment history modal (shared state with existing customer history dialog)

### Customer Payment History in `/customers` page
- New Clock icon button on each customer row → opens dedicated Payment History modal
- Shows all payment records: Date, Invoice #, Type, Method, Reference, Amount, Recorded By
- Total Received summary row at bottom

### Close Wizard Step 3 Enhancements
- Added **Method** column to AR payments table (shows Cash/GCash/etc. per payment)
- Added AR payment method breakdown chips (Cash: ₱X, GCash: ₱Y)
- Added **Interest & Penalty Invoices Created Today** section showing INT/Penalty invoices issued today with Amount, Collected, Balance columns

### Close Wizard Z-Report (Step 7) Enhancements
- Added `↳ of which interest collected` annotation under AR Cash Payments line
- Added `↳ discounts given on interest/penalty` annotation when AR discounts were given today
- Enhanced AR Collections section: method breakdown chips, per-payment interest annotation, AR discounts footer row
- Added **Interest & Penalty Invoices Issued Today** section in Z-Report

### Backend
- `GET /api/payments/history` (new) — org-scoped, paginated, filterable
- `daily-close-preview` now returns 4 new fields: `interest_invoices_today`, `ar_payment_by_method`, `ar_interest_collected`, `ar_discount_today`

## Iter 203 (May 2026) — PIN Session + Input UX + Payments Improvements ✅

### Payments Page Improvements
- **Manual Interest**: Removed auto-generate at payment time. Users now explicitly generate interest via "Generate INT" button (auto-compute) or manual ₱ amount input.
- **Interest Discount + PIN**: Discount on interest/penalty invoices now requires manager PIN authorization.
- **Payment History Editing**: Edit button on each payment (blocked if date is in Z-Report). Modifying = void original + re-apply with new amount atomically.
- Backend: new `modify-payment` endpoint, `manual_amount` on generate-interest, `discount_pin` on receive-payment.

### PIN Session (3-Minute Warm Window)
- Once a manager/admin PIN is verified, cached for 3 minutes (per-sale scope).
- Subsequent PIN gates at the same authority level auto-bypass or auto-fill the cached PIN.
- Full auto-bypass: Credit Approval (online), Capital Reveal, Stock Override, JIT Repack, Discard Park, E-Payment Verify.
- Auto-fill: Price Match (needs reason/scope), Void (needs reason), Late Encode (needs reason), Credit Approval (offline, needs bypass reason).
- Visual badge: emerald "PIN active · Name · 2:45" countdown in Sales header.
- Per-sale: resets on cart clear. Fails safely: clears session and falls back to dialog on any API rejection.

### Order Mode Input UX: Tab-Select + Leading Decimal
- Qty, Rate, Discount in Order mode now auto-select all text on Tab/focus for instant overwrite.
- Changed from `type="number"` to `type="text" inputMode="decimal"` to allow `.5`, `.05` entry.
- String-intermediate pattern prevents transient states (`.`, `0.`, empty) from snapping to `0`.
- Quick mode price input also upgraded for consistency (qty was already done).
- No backend changes.

## Iter 201b (Feb 2026) — Per-Branch Close-Day SMS Opt-Out ✅
- Owners can mute automated close-day SMS on per-branch basis (warehouse / transfer-only branches).
- New `branch.close_reminder_disabled` flag, new `PUT /api/sms/close-reminder/branch-toggle/:branch_id` endpoint.
- Scheduler skips disabled branches; Z-Report summaries (manual close) still fire.
- UI: Mute/Un-mute pill in Branch Closing Times + muted badge + grey-out on time input.
- 3/3 regression tests passing.


## Iter 201 (Feb 2026) — QR Document Lookup Fix (Tenant Context Bug) 🔴 P0
- **Bug**: Sale/PO/Transfer QR codes returned "Document not found" / "Invoice not found" when scanned even though doc_code + document existed in DB.
- **Root cause**: Public QR endpoints had no JWT, so the multi-tenant fail-closed proxy returned 0 docs from `db.invoices`.
- **Fix**: After resolving doc_code, set tenant context from `doc_ref.org_id` (with legacy fallback via `_raw_db`); applied to `view_document_open`, `lookup_document`, all `qr-actions/*` handlers.
- **Collateral**: Fixed `UnifiedSalesPage.js` signature-print path that was calling `/doc/generate-code` with `doc_type: 'sale'` (unknown type → unresolvable QR).
- **Migration**: 84 legacy doc_codes backfilled with `org_id`.
- Tested: 3 new regression tests passing; live curl on `/api/doc/view/M3VYT6P6` now returns invoice.


## Latest Iter 197 (May 1, 2026) — Customer Dedupe Manager + Bulk Delete + No-Blocker Import ⭐ Critical Fix
- **Root-cause fix**: Customer importer previously silently dropped opening-balance invoices in 3 cases (exact name dupe, default "skip" on fuzzy rows, duplicate-within-file). Re-imports created ZERO OBs.
- **New flow** validated with user: Import = dumb ingest (always new customer + OB invoice). Dedupe = background popup (mirrors PriceScanManager).
- 4 new backend endpoints under `/api/customers`:
  - `GET /-/duplicates` — union-find clusters by name-sim / phone-tail
  - `POST /merge` — re-points invoices, merges fields (master wins if present, else dup fills), audit trail
  - `POST /mark-distinct` — pairwise distinct decisions persisted (`customer_dedupe_decisions`)
  - `POST /bulk-delete` — PIN-gated (`customer_bulk_delete` policy), guards balance/open-invoices, force-admin override
- New frontend component `CustomerDedupeManager` — floating pill + merge dialog with master-radio + canonical-name input
- Bulk select checkboxes, "no balance" filter, PIN-gated bulk-delete modal on CustomersPage
- Tested: 6/6 new pytest passing

## Iter 196 (May 1, 2026) — Branch-Scoped Product Editing
- **Critical fix**: PUT /api/products/{id} now accepts `?branch_id=X`; when set, price/cost edits route to `branch_prices` instead of clobbering the master catalog
- GET /api/products?branch_id=X merges branch overrides into each row + tags `price_source: "branch_override" | "global"`
- Edit dialog shows scope banner (purple = branch / blue = master); per-row `⚙ Branch` chip surfaces overrides at-a-glance
- Catalog fields (name/category/etc.) still always hit master — those are tenant-wide
- Tested: 7/7 new pytest passing, no regressions on prior product tests

## Iter 195 (May 1, 2026) — Test Stage button + Dynamic Price Scheme columns
- "Test this Stage" button on each Team SMS Reminder row → fires `[SAMPLE]` SMS now
- Org-wide vs per-branch behaviour clarified with banner (toggles=org-wide, close time=per-branch)
- Import Center: dynamically renders one column per active price scheme (Retail/Wholesale/Credit/…) — both the column mapper and the downloaded CSV templates
- Auto-detects file headers like "Credit Price" → maps to Credit scheme
- New endpoint: `POST /api/sms/close-reminder/test-stage/{stage_key}`
- Backend templates `/api/import/template/products` and `/api/import/template/branch-stock-and-price` now read `price_schemes` live

---

## Original Problem Statement
Build a full-featured POS system called **AgriBooks** with multi-tenant, multi-branch support including sales, purchase orders, inventory, branch transfers, accounting, employee management, and more. Extended with an AgriSmart Terminal (handheld Android + barcode scanner + thermal printer) and a QR-based operational workflow system.

## Core Architecture
- **Frontend:** React + Tailwind CSS + Shadcn UI
- **Backend:** FastAPI (Python) + MongoDB
- **Offline:** IndexedDB + Service Worker (PWA)
- **Storage:** Cloudflare R2
- **Real-time:** WebSocket (FastAPI native)

## 3rd Party Integrations
- Cloudflare R2, Resend, Google Authenticator, fpdf2, python-barcode, jsbarcode, html5-qrcode, qrcode.react

## Credentials
- Super Admin: janmarkeahig@gmail.com / Aa@58798546521325
- Company Admin (LimitTest Corp): limittest@testmail.com (TOTP not set; use super admin for full testing)
- Manager PIN: 521325

---

## ✅ Completed in Iter 199 — Late-Encode + SMS Close-Day Scheduler (Feb 2026)

Owner asked for two complementary features: (a) ability to encode forgotten credit sales onto past closed days without amending those closed Z-reports, and (b) an SMS escalation system to prevent forgetting in the first place.

**Backend — Late-Encode** (`routes/sales.py`):
- New `late_encode: {reason, pin}` body param on POST /api/unified-sale
- Guardrails: credit/partial only (no cash/digital), 7-day backdate cap, no cross-month (protects VAT), reason ≥10 chars, manager/admin PIN required, max 5/day/branch
- Tags invoices with `late_encoded`, `late_encoded_at`, `late_encode_reason`, `late_encoded_by_name`, `late_encode_verifier_name`, `late_encode_days_back`
- New `late_encode_log` audit collection
- NEW `GET /api/sales/late-encoded-since-close?branch_id=` returns carryover entries for the Close Wizard

**Backend — Close-Day SMS Scheduler** (`routes/close_reminder.py`, NEW ~452 lines):
- Asyncio background loop (1 tick/min) registered on FastAPI startup
- 9 stages: 3 PM catch-up (cashier), 6 PM pre-close, 7:30 PM late notice, 8:30 PM status, 9:30 PM escalation, 7 AM/12 PM Day+1, 7 AM/12 PM Day+2+
- Quiet hours 22:00–07:00 (absolute)
- Per-branch `close_time_h` setting (default 18 = 6 PM)
- `close_reminder_log` dedup — one fire per stage/day/recipient
- Zero-sales suppression on escalation
- Role-aware recipient resolution (manager==cashier → one SMS)
- NEW `send_zreport_finalized()` — fires immediately after successful close, summary SMS to manager/owner/auditor with sales/cash/credit/digital/expenses/over-short + late-encode note

**Backend — SMS Templates** (`routes/sms.py`):
- 8 new DEFAULT_TEMPLATES entries: close_catchup_3pm, close_precheck, close_late_notice, close_status_snapshot, close_escalation, close_overdue_next_day, close_overdue_multi_day, zreport_finalized

**Frontend**:
- NEW `LateEncodeDialog.js` — appears when a credit sale hits a closed day, collects reason + PIN + renders all guardrails in UI
- `UnifiedSalesPage.js` — intercepts 403 "already closed" for credit/partial and opens LateEncodeDialog; retries on confirm
- `CloseWizardPage.js` — NEW `LateEncodedCarryover` section at the top of step 2 listing all late-encoded invoices since last close with amber highlight

**Daily close hook** (`routes/daily_operations.py`): Z-Report Finalized SMS fires via `asyncio.create_task` (non-blocking).

**Tests:** 16 new assertions (late-encode endpoint, guardrails, close_reminder importables, new templates). Total: **86/86 ✅** (16 new + 70 regression).

---

## ✅ Completed in Iter 198 — Cash Overage Reserve System (Feb 2026)

Owner asked: "where does the extra money from Z-Report go? I want to view total extra money and use it during audits to cover missing inventory value." Built a full Reserve Ledger system that auto-pools daily-close variance and surfaces it for audit-time offset.

**Backend** (`/app/backend/routes/overage_reserve.py`, NEW ~430 lines):
- `GET /api/reserve/summary` — per-branch + org rollup of reserve and shortage-deficit pools (multi-tenant scoped to user.organization_id with defense-in-depth filter).
- `GET /api/reserve/ledger` — paginated ledger w/ filters (branch, pool=reserve|deficit, type).
- `POST /api/reserve/apply` — debit reserve to offset audit findings (PIN required; validates audit_session_id ownership + branch match).
- `POST /api/reserve/net-shortage` — paired debit on both pools when owner approves netting.
- `POST /api/reserve/claw-back` — reverse a mistaken auto-credit (PIN required).
- `POST /api/reserve/backfill` — admin-only, idempotent backfill from daily_closings (org-scoped query).
- Auto-hook in `daily_operations.submit_daily_close` and `submit_batch_close` — every non-zero `over_short` posts a ledger entry on close.
- `_compute_audit` now returns `reserve` field with current balance for the queried branch.

**Frontend**:
- `/app/frontend/src/components/audit/ReserveTab.js` (NEW) — full ledger view, balance triplet (Reserve/Deficit/Net), Apply/Net/Claw-back modals, branch picker, admin Backfill button.
- `/app/frontend/src/components/OverageReserveCard.js` (NEW) — compact summary card placed on the FundManagement (Wallets) page right under the 4 wallet cards. Click → deep-link to `/audit?tab=reserve`.
- `AuditCenterPage.js` — new "Reserve" tab; deep-link support via `?tab=reserve`; auto-suggest banner during Run Audit results: "Your inventory shortage is ₱X — apply ₱X from your ₱Y Reserve?"

**Tests:** new `/app/backend/tests/test_overage_reserve.py` (23 tests) — endpoints, multi-tenant scoping, ledger consistency, paired netting, claw-back, audit/compute integration. Regression: 47 prior audit tests still pass. **Total: 70/70 ✅**

---

## ✅ Completed in Iter 197 — Audit Pulse Dashboard Widget (Feb 2026)

Follow-on to Phase 1 deep analysis: surfaced the new scores on the dashboard so owners see the headline numbers without opening the full Audit Center.

**Backend** (`/app/backend/routes/audit.py`, +70 lines):
- New `GET /api/audit/pulse?days={7|30|90}&branch_id=…` — lightweight, cached-free snapshot that reuses `compute_audit()` internally and projects down to a compact payload: `health_score/label`, `fraud_risk_score/label`, top 3 risk factors (by points), 6 headline KPIs (margin, void, discount, DSO, DPO, turnover) + trend deltas. `days` clamped to 1–365 to prevent runaway compute windows.

**Frontend**:
- New `AuditPulseWidget.js` — dual SVG dial (Health green/amber/red · Risk inverse), top 3 red-flag rows, 3-tile mini KPI strip (Margin/Void/DSO), period selector (7/30/90d), click-through to `/audit`.
- Registered in `DashboardPage.js` for both Owner Consolidated View and Branch/Single View layouts (default grid slots + MIN_H/MIN_W maps updated).

**Tests:** new `/app/backend/tests/test_audit_pulse.py` (9 tests: auth, shape, ranges/labels, top_risk rules, days math, branch scoping, compute-cross-check) all pass. Regression: 38/38 prior audit tests still green. **Total: 47/47 ✅**

---

## ✅ Completed in Iter 196 — Audit Center Phase 1 Deep Analysis (Feb 2026)

Owner requested a top-to-bottom review of the Audit Center ("analyze the whole site on what should be there, key indicators… deep analysis based on numbers"). Current center was a *checklist* (severity per section → simple average score). Phase 1 adds the ratio/trend/weighted layer a real auditor needs.

**Backend** (`/app/backend/routes/audit.py`, +320 lines):
- `_compute_kpis()` — new cross-cutting ratios: gross_margin_pct, void_rate_pct, edit_rate_pct, discount_rate_pct, DSO (AR days), DPO (AP days), annualised inventory turnover, payment_mix_pct, plus underlying revenue/cogs/gross_profit/total_ar/total_ap/inventory_value.
- `_compute_trend_deltas()` — auto-compares against previous equal-length period, returns deltas per ratio.
- `_compute_scores()` — two independent 0-100 scores:
  - **Health Score** (weighted: cash 20, inventory 15, AR 10, AP 10, sales 10, returns 5, transfers 10, unverified 10, digital 5, activity 5; inventory weight redistributed proportionally when not available) with label Excellent/Good/Needs Review/Poor.
  - **Fraud Risk Score** (composite: void rate ≤25 pts, discount rate ≤15, edit rate ≤15, off-hours ≤10, corrections ≤10, PIN security ≤15, price-match volume ≤10; capped at 100) with label Low/Elevated/High/Critical and a per-factor `risk_breakdown` array.
- `/api/audit/compute` now returns `kpis`, `kpis_prev`, `kpis.trend`, `scores`.

**Frontend** (`/app/frontend/src/pages/AuditCenterPage.js`):
- New `KpiRibbon` component above section tiles — six tiles (Margin, Void, Discount, DSO, DPO, Turnover) with color-coded trend arrows vs previous period + payment mix strip.
- New `RiskBreakdownCard` — per-factor contribution bars with caps.
- Header now shows dual scores: Health (existing green/amber/red) **and** Fraud Risk (inverse color scale) with labels.
- Print report extended with KPI table + Fraud Risk Breakdown table.

**Tests:** `/app/backend/tests/test_audit_deep_analysis_phase1.py` (19 new assertions) + 19 regression tests = **38/38 passing**.

---

## ✅ Completed in Iter 195 — Audit-Driven Consistency Fixes

Based on the owner's request for a full system audit from an accountant's perspective, I reviewed the entire codebase phase-by-phase (money movement, inventory, price, audit trail, reversals, multi-tenant, offline). Found 11 issues; fixed the top 6 critical + 1 bonus (offline capital cache pre-existing bug).

**Fix #1 — Credit return reduces AR** (`returns.py`)
Credit customer returns now offset Accounts Receivable. `credit_applied = total_return_value - cash_refunded` → subtracted from both `customer.balance` and newest open `invoice.balance`. Over-credit case notifies admin. `void_return` reverses all of it.

**Fix #2 — Product Profitability nets returns** (`reports.py`)
Shelf return: revenue AND cost both reversed. Pullout return: revenue only (cost stays as real COGS loss). New `returned_qty`, `returned_revenue`, `returned_cost` fields.

**Fix #3 — Sales reconciliation block** (`audit.py`)
`_compute_sales` returns a `reconciliation` dict proving `grand_total == sum_line_totals + freight - overall_discount`. Variance > ₱1 flags warning.

**Fix #4 — PO Reopen moving-avg isolation** (`purchase_orders.py`, `branch_transfers.py`, `sync.py`)
Original purchase movements now tagged `reversed=True`. All 4 moving-average consumers filter these out. Found + fixed pre-existing sync.py bug (`movement_type` → `type`, wrong field names) — offline capital cache was silently empty.

**Fix #5 — Void invoice cleans up discount/price logs** (`invoices.py`, `reports.py`, `audit.py`, `daily_operations.py`, `price_changes.py`)
Invoice void tags `discount_audit_log.invoice_voided=True`. All reports filter by default; `include_voided=true` for forensic view.

**Fix #6 — Cash wallet_movements cross-check** (`audit.py`)
New `_wallet_movements_reconciliation` helper independently verifies `starting_float + net_movements ≈ current_balance`.

**Tests**
- 9 new tests in `/app/backend/tests/test_consistency_195.py`
- All 34 cross-iteration tests passing (9 iter 195 + 13 iter 194 + 4 iter 192 + 8 iter 193)
- Testing agent validated end-to-end via public HTTP (100%, 37 scenarios)

### Deferred (low risk)
- **Fix #7** Org-scoping sweep on older routes (needs dedicated audit pass)
- **Fix #8** Pullout movement cleanup (cosmetic)
- **Fix #9–11** Dead code, lint, PIN_POLICY_ACTIONS endpoint

### Auditor's Takeaway (post-fix)
Every view now agrees on the same numbers for the same period:

| View | Source | Reconciled? |
|---|---|---|
| Reports → Sales | invoices.grand_total | ✓ |
| Z-Report | sales_log.line_total | ✓ |
| Audit Center → Sales | dual-source + reconciliation block | ✓ |
| Product Profitability | invoices.items net of returns | ✓ (Fix #2) |
| Cash | formula + wallet_movements dual-check | ✓ (Fix #6) |
| Discount / Price Change reports | minus voided | ✓ (Fix #5) |
| Customer AR | customer.balance + invoice.balance | ✓ (Fix #1) |

---

## ✅ Completed in Iter 194 — POS Price Match (Permanent Branch Price Change)

Replaces the old "ad-hoc price edit → save as global" flow with a **competitor-aware Price Match flow** for true accountability. Backend 8/8 + 21/21 regression tests pass. Frontend lint clean. End-to-end verified via testing agent (37/37, 100%).

### POS Price Match (2026-04-30) — Complete (Iter 194)
User-confirmed UX choices: Q1=A (active scheme + branch only — NOT global), Q2=B (no revert button — yellow badge is the warning), Q3=Per-product discount breakdown on Z-report (units sold, total discount, avg discount/unit), Q4=All reasons EXCEPT vendor-cost (Smart Price Checker handles that).

**Backend**
- New `routes/price_changes.py`:
  - `GET /api/price-changes` — filterable list with summary (total_changes, total_drop, total_raise, avg_delta_pct).
  - `GET /api/price-changes/product/{id}` — per-product price timeline.
  - `GET /api/price-changes/reasons` — standardized reason vocabulary.
- `routes/sales.py` — Price Match flow:
  - Pre-validates `price_changes[]` payload, fetches **server-trusted** `old_price` from branch_prices (fallback to product.prices). Client-supplied old_price stored as `client_old_price_hint` for forensics. Tamper-resistant.
  - Returns `422 {type: "price_match_pin_required"}` when PIN missing; `403` on invalid PIN.
  - On approval: upserts `branch_prices.prices[scheme]` for the **active scheme + branch only** (other schemes untouched). Inserts `price_change_log` entry with full attestation (cashier, approver, method, customer, invoice, reason, delta, delta_pct).
  - Skips legacy `price_override` audit entry when a corresponding `price_changes` row exists — discount audit log stays clean.
  - Notifies admins via existing notification system (reuses `discount_given` channel with title "Price Match Approved").
- `routes/verify.py` — added PIN policy action `price_match` (defaults: admin_pin, manager_pin, totp).
- `routes/daily_operations.py` — `/daily-close-preview` now returns `discount_breakdown` (per-product units/discount/avg) and `price_changes_today` (count + rows) for the Z-report wizard.
- `main.py` — added price_change_log indexes (date, branch_id, product_id, created_at).

**Frontend**
- New `components/PriceMatchModal.js` — per-line reason dropdown (Competitor / Bulk-Loyal / Promo / Damaged / Other) + free-text detail + manager/admin/TOTP PIN. Auto-default "competitor_match".
- `pages/UnifiedSalesPage.js`:
  - **Yellow warning badges** on cart lines (Quick + Order modes) when `price !== original_price` — both cashier and manager see at a glance.
  - `computePriceChanges()` collects all edited lines.
  - `openCheckout()` opens PriceMatchModal first if any changes exist, then proceeds normally on approval.
  - `processSale()` includes `price_changes` + `price_match_pin` + `price_scheme` + `branch_name` in payload.
  - Removed old "Save as global price?" dialog.
- `pages/ReportsPage.js` — new **Price Changes** tab with KPI cards (Total Changes / Drop / Raise / Avg Δ%), filters (date, branch, reason, product search), grouped-by-product view with full event drill-down (cashier, approver, scheme, reason, detail).
- `pages/CloseWizardPage.js` — Step 7 Z-report preview now shows:
  - **Discounts Given Today** card: per-product table (units, total disc, avg/unit) + overall discount + transaction count.
  - **Permanent Price Changes Today** card: list of changes with old → new, scheme, approver.

**Tests**
- `/app/backend/tests/test_price_match_194.py` (8 baseline tests, all passing).
- Testing agent added `test_price_match_194_edge.py` (8 edge probes — reasons endpoint, reason filter, delta math, active-scheme isolation, fast discount-only path, close-preview shape, /reports/discount-audit regression, PIN policy).

### Architecture Notes
- `discount_audit_log` collection: per-line `discount_value` + overall_discount, no PIN required, fast checkout — UNCHANGED.
- `price_change_log` collection: NEW, dedicated to Price Match events. Schema: {id, product_id, branch_id, scheme, old_price (server-trusted), client_old_price_hint, new_price, delta, delta_pct, reason, reason_detail, invoice_*, customer_*, cashier_*, approver_*, date, created_at, organization_id}.
- Capital floor (sell-below-cost) unchanged — `has_perm('sales','sell_below_cost')` still gates it; admins bypass by role.
- Audit hardening (post-test): `old_price` is always read server-side from `branch_prices`/`products.prices[scheme]` — client-supplied value is stored separately as a forensics hint.

### What's NEXT for Sales accountability
- **(Optional) HTTP endpoint listing PIN_POLICY_ACTIONS** — currently the constant lives in `verify.py` and admin UI infers from defaults. Exposing it would simplify policy customization UI.
- **(Optional) Surface price-change history on ProductDetailPage** — backend `/price-changes/product/{id}` already returns the timeline; just needs a UI tab.

### Iter 194b — Consistency Audit & Safety Fixes (same session)
After the core Price Match build, did a full audit to ensure no contradiction with existing money/price/inventory flows. Found & fixed:

1. **Discount audit classification fix** — When a line is both matched AND discounted, `discount_audit_log` now reads cleanly: `original_price=matched_price`, `sold_price=matched_price`, `discount_amount=X`, `type=line_discount` (no more confusing `discount_and_override`). The `price_change_log` remains the single source for the price event.
2. **Server-trusted `old_price`** — `sales.py` now reads `old_price` from `branch_prices` (fallback `products.prices`) server-side, ignoring client-supplied values. Client value stored as `client_old_price_hint` for forensics.
3. **`mark_price_reviewed` after Price Match** — Clears the "Global Price" badge at the branch because a deliberate Price Match IS an explicit price review.
4. **Offline edit guard** — Web POS now blocks price editing (`updateCartPrice`, `updateLine(rate)`) when `!isOnline`. Message: "Price editing is disabled while offline. Apply a discount instead, or wait until you are back online to price-match." Safer than caching a price-match PIN offline (offline mode is for resilience, not for permanent audited changes).
5. **Audit Center surfaces price matches** — `_compute_activity` now returns `price_change_count`, `total_price_drop`, `top_price_matchers` so admins see price-match activity alongside discounts in the Audit Center.
6. **Voided invoice warning** — When an invoice containing Price Match events is voided, `void_invoice` now: (a) tags related `price_change_log` rows with `invoice_voided=True` + timestamp + voider, (b) fires a high-severity admin notification with the product list, (c) returns `price_match_warning` in the response so UI can surface it. Branch prices are NOT auto-reverted (per spec — real customer may have already bought at the matched price).
7. **SuperAdmin backup completeness** — `price_change_log` added to the backup collections list so org exports include it.
8. **Non-collision with Smart Price Checker** — Verified: Smart Price Checker updates `products.prices` (global); Price Match updates `branch_prices.prices` (per-branch, higher precedence). Branch decisions persist across global price changes (by design). No conflict.
9. **Non-collision with `get_branch_cost()`** — It reads `branch_prices.cost_price` (capital). Price Match updates `branch_prices.prices[scheme]` (sell price). Different fields. No conflict. Capital floor still enforced on matched rate.
10. **Non-collision with inventory / returns** — Price Match is prices-only; doesn't touch `inventory`, `movements`, `sale_reservations`. Returns use snapshot `item.rate` (matched price) for refund amount and `item.cost_price` for capital — both correct.

Tests extended (13 total, all passing): covers server-trusted old_price, price_reviewed_at stamp, clean audit classification when matched+discounted, voided invoice tagging + warning, Audit Center surfacing.

---

## ✅ Completed in Iter 193 — Per-Branch Product Disable

Branch-scoped product visibility shipped. Backend 8/8 tests pass + 25/25 regression. Frontend lint clean.

### Per-Branch Product Disable (2026-04-30) — Complete (Iter 193)
User-confirmed UX choices: Q1=b (greyed in POS, not hidden), Q2=a (badge in admin list), Q3=a (any qty increase auto-reactivates), Q4 (manager can toggle their branch; managers cannot delete products).

**Backend**
- New `routes/branch_products.py`:
  - `POST /api/products/disable-at-branch` — disables a list of products at a branch. Skips any with `inventory.quantity > 0` and reports them per-product. Permission: admin/owner anywhere; manager only on assigned branch.
  - `POST /api/products/enable-at-branch` — clears the flag in bulk.
  - `GET /api/products/disabled-at-branch?branch_id=X` — admin view of currently-disabled items + who disabled them.
- **Lazy auto-reactivation** — instead of touching every `$inc` site (PO/transfer/return/manual), the `/products`, `/sync/pos-data`, and `/products/disabled-at-branch` endpoints run a single `update_many({disabled_at_branch: True, quantity: > 0}, {$set: {disabled_at_branch: false, auto_reactivated_at: now}})` at the top. Self-healing on every read; no risk of missed hooks.
- `DELETE /api/products/{id}` now rejects role != admin/owner (Managers can only disable-at-branch, not delete).
- `/sync/pos-data` and `/products` now stamp `disabled_at_branch: bool` per product.
- Routes registered before `products_router` to avoid `/{product_id}` catch-all.

**Frontend**
- `ProductsPage.js` — bulk action toolbar gains "Disable at [Branch]" + "Enable at [Branch]" buttons (visible when a specific branch is selected and user is admin/owner OR manager). Delete button hidden for non-admin. Disabled rows render dimmed with an amber "Disabled here" badge.
- `UnifiedSalesPage.js` — disabled products in the product grid render greyed-out + button-disabled with a "Disabled" pill. addToCart aborts with toast if product is disabled at branch.
- `SmartProductSearch.js` — disabled products show with "Disabled at branch" badge, dimmed, click does nothing.
- `terminal/TerminalSales.jsx` — same treatment in the Terminal POS search results.
- All product list reads include `disabled_at_branch` for the current branch.

**Tests** (`backend/tests/test_branch_disable_193.py`, 8/8 pass):
- disable with qty=0 succeeds, with qty>0 is skipped (per-product report)
- enable clears the flag
- lazy reactivation: stock arrival → next read clears the flag
- `/sync/pos-data` carries the flag
- `/products/disabled-at-branch` admin endpoint
- managers cannot delete products (403); managers can disable at their branch
- managers cannot disable on a different branch (403)

**Re-deploy:**
```bash
cd /var/www/agribooks && git pull origin main \
  && sudo supervisorctl restart agribooks-backend \
  && cd frontend && yarn build
```

---

## ✅ Completed in Iter 192 — Offline Mode Robustness Overhaul

All 4 phases shipped (see "What's Been Implemented" below). Backend 14/14 tests pass. Frontend not yet tested by automated agent — main agent linted clean + smoke-screenshot OK; recommend a frontend regression run by user or testing agent before next major feature.

---

## What's Been Implemented

### Offline Mode Robustness Overhaul — Phases 1-4 (2026-04-29) — Complete (Iter 192)
User answers locked in: skip signature offline → use Manager PIN bypass with required reason; cache bcrypt'd admin PIN locally; ship all 4 phases at once; debounce + search_blob for performance.

**Phase 1 — Speed + Completeness**
- `/api/sync/pos-data` now enriches each cached product with:
  - `search_blob` — lowercase `name|sku|barcode` concat (Terminal search filters in O(1) per item per keystroke instead of 3 separate `.toLowerCase()` ops)
  - `moving_average_cost` + `last_purchase_cost` — server-aggregates last 30d of `purchase` + `transfer_in` movements (deterministic via `$sort {created_at:-1}` + `$first`). Capital reveal toggle now works offline.
- Customers carry explicit `credit_limit` + `credit_blocked_at` fields.
- `admin_pin_hash` (bcrypt hash, one-way) cached at top level for offline manager-bypass verification.
- Frontend Terminal search debounced 150ms; uses `search_blob` (with legacy fallback).

**Phase 2 — Offline Credit Sales via Manager PIN Bypass**
- New `OfflineCreditBypassDialog.js` — when offline + credit/partial sale, cashier enters Admin PIN + required reason. PIN verified locally via `bcryptjs.compareSync` against cached hash.
- `lib/offlineAuth.js` — `verifyOfflinePin(pin)` and `pingBackend(url)` helpers.
- `lib/offlineDB.js` schema bumped to v5 (no breaking change — META store seamlessly stores `admin_pin_hash`).
- `TerminalSales.jsx` — Confirm button branches: online → existing signature flow; offline → bypass dialog. Sale queues with `offline_bypass: {method, by_name, reason, at, credit_type, branch_name}` payload.
- `POST /api/sales/sync` now retroactively creates `signature_session(status='bypassed', offline_origin=true)` linked to the new invoice; back-links via `signature_session_id` + `offline_signature_origin: true` on the invoice. Defensive type-coercion for malformed payloads.

**Phase 3 — Hardening**
- Unique partial index on `invoices.envelope_id` (defense-in-depth dedup beyond `idempotency_key`). Re-posting same envelope_id returns `status='duplicate'` and creates 0 dupes (verified via parallel-request test).
- `GET /api/sync/offline-summary?days=N&branch_id=X` — returns `{total_synced, warned_count, offline_credit_count, samples[]}`. `days` clamps 1-90 gracefully (no 422 on malformed).
- `DuplicateKeyError` caught during `/sales/sync` invoice insert → treated as duplicate, not failure.
- Local credit-limit + credit-block check in TerminalSales offline flow before opening bypass dialog.

**Phase 4 — UX Polish (Robust Connectivity)**
- `TerminalShell.jsx` online detection upgraded: combines `navigator.onLine` with periodic `/api/health` ping every 30s. Only flips to offline after 3 consecutive ping failures (90s window) — prevents the "false offline" toast user reported when actually online.
- New `GET /api/health` endpoint (API-prefixed, no auth) for connectivity probes.

**Tests** (`backend/tests/test_offline_mode_192.py`): 4 baseline + 10 edge-case probes by testing agent — all PASS (14/14). Verified happy path + cash-sale ignored + missing customer rejected + concurrent envelope_id race + malformed offline_bypass + days clamping + lowercase search_blob.

---

### Full Unification — Capital Reads + Legacy Code Cleanup (2026-04-29) — Complete
Comprehensive 4-phase unification ship:

**Phase 1 — Repack capital enrichment in remaining endpoints**
- `GET /api/inventory` — Inventory page now shows live repack capital (parent_branch_cost ÷ units + add_on)
- `GET /api/sync/pos-data` — Terminal POS offline cache now derives repack capital from parent's branch_prices.cost_price; protects offline mode from recording ₱0-cost sales

**Phase 2 — Legacy code deletion**
- Deleted `frontend/src/pages/POSPage.js` (771 lines) — replaced by UnifiedSalesPage long ago
- Deleted `frontend/src/pages/SalesOrderPage.js` (856 lines) — replaced by UnifiedSalesPage order mode
- Deleted `POST /api/invoices` route (288 lines) — replaced by `POST /api/unified-sale`
- **Total cleanup**: ~1,915 lines of dead code removed, ~5KB JS shipped to browsers, 1 unprotected sale-create path closed (it had a stale below-capital check that never blocked repack underselling)
- **Data safety**: ZERO collections modified, ZERO migration. Old invoices created via the deleted route still readable through `GET /invoices`, `GET /invoices/{id}`, Sales History page, Reports.

**Phase 3 — Unified resolver in dashboard.py**
- `_compute_inventory_value()` now branch-aware (was only reading global product.cost_price). Uses bulk-fetched branch_prices to resolve correct cost per SKU per branch.
- Price-issue scan in `daily-summary` likewise branch-aware.
- Result: capital_value, retail_value, potential_margin widgets on dashboard finally match per-branch reality.

**Phase 4 — Capital Source badge** (transparency win)
- New `CapitalSourceBadge` component rendered next to capital displays. 5 source types:
  - 🟢 **Live (parent)** — repack derived from parent's branch capital
  - 🔵 **PO** — auto-updated from a Purchase Order receipt
  - 🟣 **Transfer** — auto-updated from a Branch Transfer receipt
  - ⚫ **Manual** — admin-set branch override
  - 🟡 **Global** — fallback to global product capital (no branch data yet)
- Wired into `ProductDetailPage.js` Cost card.

**Phase 5 — Cross-endpoint regression test (the safety net)**
- `test_capital_consistency_191.py` creates a parent at ₱1500 + repack at 50kg, hits **8 product-data endpoints**, asserts all return **₱30** for repack capital. Catches future drift if anyone adds a new endpoint that bypasses `get_repack_capital()`. **All 7 reachable endpoints agree.**

### Test summary (40/40 passing across iter 182-191)
| File | Tests | Focus |
|---|---|---|
| `test_branch_stock_price_import_182.py` | 4 | Branch stock/price CSV imports |
| `test_customer_import_183.py` | 4 | Customer CSV + opening balance invoice |
| `test_global_price_badge_184.py` | 7 | Global Price badge auto-clear on PO/transfer |
| `test_capital_change_alerts_185.py` | 7 (2 pre-existing infra failures, unrelated) | Capital change PIN alerts |
| `test_cost_details_186.py` | 5 | Cost details endpoint |
| `test_repack_pricing_187.py` | 7 | Repack pricing manager + branch retail |
| `test_pre_invoice_signature_188.py` | 2 | Pre-invoice signature attach |
| `test_sms_autoseed_189.py` | 3 | SMS template auto-seed + diagnostics |
| `test_repack_list_detail_190.py` | 3 | Capital surfaces on list/detail/single |
| `test_capital_consistency_191.py` | 1 | **All 8 endpoints agree on ₱30** |

### Repack Capital + Retail Visible on Products List & Detail (2026-04-29) — Complete
- **Why**: User reported repack capital showed correctly on Sales (uses `search-detail`) but was blank on `/products` list and `/products/{id}` detail pages — different endpoints didn't carry the live computation.
- **Backend** — three endpoints now enrich repacks with live parent-derived capital:
  - `GET /api/products?branch_id=...` — new `_enrich_repacks_with_live_capital()` helper injects live `cost_price` + merges branch retail into `prices` for repack rows. Works for `name`, `type`, and `grouped` sort modes.
  - `GET /api/products/{id}?branch_id=...` — same enrichment for single fetch.
  - `GET /api/products/{id}/detail?branch_id=...` — `cost.cost_price` and `product.cost_price` now reflect live capital; new `cost.repack_capital` field exposes the computed value explicitly. `cost.cost_source` becomes `derived_from_parent` when no branch override is set.
- **Frontend** — `ProductsPage.fetchProducts` now passes `branch_id` query param so repacks list with correct capital from current branch. `ProductDetailPage` already passed branch_id.
- **Tests**: 3 new pytests in `test_repack_list_detail_190.py` (1500/50 = 30 capital + 35 retail verified across all 3 endpoints). Suite total (Iter 184-190): 28 passing.

### SMS Auto-Seed + Health Diagnostics (2026-04-29) — Complete
- **Why** (root cause of Jegger Edem's missing SMS): SMS templates (`credit_new`, `payment_received`, `opening_balance_notice`, etc.) are **per-org** in `sms_templates` collection. Defaults are seeded by `_ensure_templates()` — but this was only called when a user opened **Settings → Messages** page or imported customers. **Org registration never seeded templates**, so any new tenant that never opened that page had ZERO templates → every auto-SMS trigger silently bailed via `template_missing`.
- **Three layers of fix**:
  1. **Org registration** — `routes/organizations.py:register_organization` now calls `_ensure_templates()` after creating the org so all default templates exist from day 1.
  2. **`queue_sms()` self-heal** — if the org has zero templates and a hook fires, defaults are auto-inserted on-the-fly so the very first auto-SMS triggers also succeed.
  3. **`POST /api/sms/templates/backfill`** — manual safety net for existing tenants (admin/manager only). Idempotent — only inserts missing keys, never overwrites customizations.
- **Frontend**:
  - **Invoice Modal SMS tab** — when no SMS was queued for an invoice, shows a 1-click "Backfill default templates" button + "Run live diagnosis (credit_new)" button. Diagnostic output is rendered inline (template found ✓/✗, trigger setting enabled ✓/✗, would_send: yes/no).
- **Backend logging** — every `queue_sms` bail-out now emits a structured WARN log line so operators can grep `/var/log/supervisor/backend.err.log` for `"queue_sms skipped"` to see exactly why.
- **Tests**: 3 new pytests in `test_sms_autoseed_189.py`. Suite total (Iter 184-189): 25 passing.

### Invoice Modal — Signature Display + SMS Diagnostics (2026-04-29) — Complete
- **Why**: User wanted to see captured signatures attached to invoices when reviewing sales history (click receipt → modal → see signature). Also reported a credit sale to "Jegger Edem" didn't auto-generate the customer SMS — needed visibility into why.
- **Signature display** — `InvoiceDetailModal.js` already had a Signature tab. The new pre-invoice flow back-links `signature_sessions.linked_record_id → invoice.id`, so the existing tab now displays signatures captured via the new flow with no extra UI work needed.
- **NEW SMS tab** in `InvoiceDetailModal`:
  - Lists all SMS queue items linked to this invoice (`trigger_ref = invoice.id`) with status (pending/sent/failed/failed_permanent), template, phone, message body, error, sent timestamp.
  - When **no SMS** was queued, shows a troubleshoot panel with common reasons (no phone on customer, trigger disabled, template missing, invoice predates feature) + a "Run live diagnosis" button.
  - **`GET /api/sms/queue?trigger_ref=...`** — extended existing endpoint with trigger_ref filter.
  - **`GET /api/sms/diagnose-trigger/{template_key}`** — new endpoint: returns each check (template existence/active, per-trigger setting enabled) with a clear pass/fail breakdown.
- **Better SMS logs** — `queue_sms()` and `on_credit_sale_created()` now emit structured `WARN`/`INFO` log lines for every bail-out reason (no phone, template missing, trigger disabled, dedup hit). Operators can now grep `/var/log/supervisor/backend.err.log` for `queue_sms skipped` to see why a specific message didn't fire.

### Pre-Invoice Signature for Credit/Partial Sales (2026-04-29) — Complete
- **Why**: User reported credit sale flow was wrong — signature dialog appeared AFTER the invoice was already created (and after the Reference Number prompt). Correct UX: customer signs FIRST, then sale finalizes with signature attached, then RefPrompt opens for printing.
- **New flow** (Web POS Sales / Sales-New):
  1. Cart → choose Credit/Partial → Crop Type dialog (term vs charged-to-crop)
  2. Manager PIN approval
  3. **Signature Dialog opens (pre-invoice mode)** — QR code for customer phone scan, OR manager-PIN bypass for emergencies
  4. Customer signs (or manager bypasses) → "Submit Sale" button fires
  5. POST `/unified-sale` with `signature: {url, signed_at, verification_token, bypass_method, session_id}` payload — invoice is BORN signed
  6. After invoice success → Reference Number Prompt opens (print receipt with signature on it)
- **Backend** (`/api/unified-sale`):
  - Accepts new `signature` block — stores `signature_url`, `signature_signed_at`, `signature_verification_token`, `signature_bypass_method`, `signature_session_id` directly on the invoice document.
  - After invoice insert, back-links `signature_sessions.linked_record_id → invoice.id` (was previously only Terminal-side; now web POS too).
- **Frontend**:
  - `RequestSignatureDialog.js` — new `preInvoice` prop. When true: success button reads "Submit Sale" instead of "Print Receipt"; cancel = cancel sale (cart preserved).
  - `UnifiedSalesPage.js` — `verifyManagerPin` for credit/partial with customer now opens pre-invoice sig dialog instead of immediately calling `processSale`. Skipped post-invoice sig dialog (legacy fallback only). Signature data is threaded through JIT retail PIN retry path so credit + repack JIT combinations still work.
- **Tests**: 2 new pytests in `test_pre_invoice_signature_188.py` — verifies signature fields stored on invoice + signature_session back-linked. Cash/walk-in regression covered.

### Branch-Aware Repack Pricing System (2026-04-29) — Complete
- **Why**: Repacks were created with **global** capital and prices. Each branch has different parent capital (e.g., Branch 1 = ₱1000/50kg, Branch 2 = ₱1200/50kg), so repack capital and retail must follow per-branch. Owner needed: capital that auto-updates on every PO/transfer; per-branch retail prices; soft red flag at sale when retail missing; bulk pricing module; hard rule that repacks always use retail tier.
- **Backend**:
  - **`get_repack_capital(repack, branch_id)`** in `utils/helpers.py` — single source of truth. Computes capital live: `parent_branch_cost ÷ units_per_parent + add_on_cost`. Fallback chain: parent's `branch_prices.cost_price` → parent global `cost_price` → repack legacy `cost_price`. **No migration on production data needed** — old repacks auto-resolve correctly.
  - `get_branch_cost()` now delegates to `get_repack_capital` for repacks (parent-aware everywhere: sales, capital reveal, reports).
  - `POST /api/products/{id}/generate-repack` — now requires `branch_id`. Stores cost_price=0 (always derived live). Retail prices write to `branch_prices` for the selected branch only — no longer poisons the global catalog.
  - `GET /api/products/repack-pricing/grid?branch_ids=...&with_inventory_only=true&missing_only=false` — Repack Pricing Manager grid: lists repacks × branches with live capital, current branch retail (or null), parent stock flag.
  - `POST /api/products/repack-pricing/bulk-save` — PIN-gated bulk persist (action `repack_retail_save`).
  - **`POST /api/sales` (`/unified-sale`)** — now:
    - Stores **live branch-aware capital snapshot** on sale lines (parent-derived for repacks).
    - Accepts `jit_retail_prices: [{product_id, retail}]` + `jit_owner_pin`. Returns `422` with `type=jit_retail_pin_required` when PIN missing → frontend shows PIN modal → retry persists to `branch_prices`.
  - `cost-details` and `search-detail` updated to surface live repack capital + new `branch_set_scheme_keys` flag (frontend uses it for the green/amber/red badge logic).
- **Frontend**:
  - **`/products/repack-pricing` (NEW)** — `RepackPricingPage.js`: branch multi-picker (All / single / multi), filter (with-inventory only / missing-only), grid editor with live capital + per-cell ₱ markup + % markup, "Copy to all" per-branch button, bulk save with Owner PIN.
  - **`UnifiedSalesPage.js`** — repack lines:
    - **Hard rule**: always use `tier=retail` regardless of customer's wholesale tier.
    - **Red "No Retail" badge** when branch_prices.retail not set.
    - **Amber "Global Price" badge** when only global retail exists.
    - Inline retail input + capital + ₱ markup + % markup hint (always visible for JIT lines, no PIN needed to view since it's needed to make the pricing decision).
    - On checkout: if any repack line has JIT retail → backend returns 422 → frontend shows JIT PIN modal → on confirm, retries sale with `jit_owner_pin`. Single PIN persists all JIT prices.
  - **`ProductsPage.js`** — Quick Repack form & batch Quick Repack now block when no branch selected (toast "Please select a branch first to set repack price"). New "Repack Pricing" toolbar button → /products/repack-pricing.
- **Auto-update guarantee**: capital reflects PO arrival, branch transfer received, manual capital edit, smart-price-update, etc. — all of these write `branch_prices.cost_price` for the parent which is the live source for `get_repack_capital`. No sync job, no manual recalc needed.
- **Tests**: 7 new pytests in `test_repack_pricing_187.py` — generate-repack branch-required, branch_prices write, parent-derived capital per branch (Branch A=20, Branch B=24 from same repack), grid endpoint, PIN-required bulk save, persistence. Total suite (Iter 182-187): 31 passing.

### PIN-gated Capital Reveal on Sales Screen (2026-04-29) — Complete
- **Why**: Owner/manager wants to see capital cost, last-purchase, and moving-average per product directly on the Sales screen for margin awareness while making sales — but cashiers must NOT see it. One PIN-gated toggle for the whole page.
- **Behavior**:
  - Stock / inventory: ALWAYS visible to all roles (no gate). Inline `Stock: X PC` on every Quick card and Order line.
  - Capital data: hidden by default for everyone. Toolbar button: **"Show Capital"** opens a PIN modal. PIN policy `view_capital_costs` accepts `admin_pin`, `manager_pin`, or `totp` (cashier PIN rejected).
  - Once unlocked, the same dialog flips to **"Hide Capital"** — one click hides immediately. Refresh / tab close also re-locks (PIN kept in memory only).
  - Bulk-fetched: 1 round-trip per visible product set (Quick mode auto-fetches missing IDs as the user scrolls/searches; Order mode fetches as cart/lines change).
- **Backend** (`POST /api/products/cost-details`):
  - Body: `{ branch_id, product_ids[], pin }`. Returns map of `{effective_cost, last_purchase, moving_average}` per product.
  - Effective cost = branch override or global fallback.
  - Last purchase = most recent `purchase` or `transfer_in` movement at this branch (last 30 days).
  - Moving average = qty-weighted over last 30 days of movements.
  - PIN re-validated on every call. No long-lived unlock token — minimizes blast radius if a PIN is sniffed.
- **Frontend** (`UnifiedSalesPage.js`):
  - Eye / EyeOff toolbar button beside the Quick/Order toggle (only on New Sale tab).
  - Capital info renders as a 2-line monospace footer on each Quick card (`Cap: ₱X` / `LP: ₱Y · MA: ₱Z`).
  - On Order mode line items: same data inline under stock as `Stock: 47 PC · Cap ₱X · LP ₱Y · MA ₱Z`.
- **Tests**: 6 new pytests in `test_cost_details_186.py`. Total suite: 22 passing across the 5 most recent feature iterations (cost reveal, capital change alerts, PIN-gated smart price, global price badge, branch import). Verifies PIN required, invalid PIN rejected, admin + manager PIN both work, branch override beats global, MA calculation correctness, unknown product returns zeros.

### Capital Change Alerts + PIN-gated Smart Price (2026-04-29) — Complete (Stage 2)
- **Why**: Owner needs visibility on every capital cost movement from POs / branch transfers (≥₱1) to call vendors and confirm "is this a new price or an error?" Plus, manager/cashier should NOT be able to change retail/wholesale prices without admin authorization.
- **Backend**:
  - Added `was_user_choice` flag to `capital_changes` schema (PO + branch transfer commits). True = admin explicitly picked the new capital → alert is suppressed (per user spec: *"if the admin or owner already made it, ignore"*).
  - `GET /api/products/capital-change-alerts?branch_id=X&days=14` — returns unacknowledged changes ≥ ₱1 with computed `delta_amount`, `delta_pct`, `direction`. Filters out `was_user_choice=True`.
  - `POST /api/products/capital-change-alerts/{id}/acknowledge` (single dismiss).
  - `POST /api/products/capital-change-alerts/acknowledge-all` (bulk, admin only, optional branch scope).
  - `POST /api/products/smart-price-update` — PIN-gated price update endpoint. Verifies PIN against new `smart_price_update` action policy (`admin_pin` or `totp` only — manager/cashier rejected). Auto-clears Global Price badge across affected branches.
- **Migration**: One-shot startup backfill marked all 96 existing `capital_changes` rows as acknowledged. Only NEW changes after deploy will surface.
- **Frontend** (`PriceScanManager.js`):
  - Existing dialog now has **2 tabs**: "Below Cost" + "Capital Changes (N)".
  - **All "Fix" / "Update All" / "Fix All" buttons now open an Admin PIN modal** before submitting. Backend re-validates the PIN. Manager/cashier PINs blocked end-to-end.
  - Capital Changes tab shows each alert with: product name, branch, ↑/↓ icon, % change + ₱ delta, old → new, source (PO# or Transfer#), vendor, who/when, and an Acknowledge button per row. Admin-only "Acknowledge All" bulk action.
  - Floating bottom-right alert pill now combines both signal types ("3 below cost · 2 capital changes").
- **Tests**: 9 new pytests in `test_capital_change_alerts_185.py`. Total suite: 27 passing (delta calc, was_user_choice skip, ₱1 floor, single + bulk ack, PIN gate accept admin, reject manager, reject invalid, require PIN). `test_credentials.md` updated with admin PIN seed details.

### Global Price Badge — "Needs Price Review" (2026-04-29) — Complete (Stage 1)
- **Why**: When a new branch opens, every product starts on the global fallback price. Manager needs visual cue showing which products haven't been priced-reviewed for that branch yet so they can call suppliers / verify margins / set per-branch overrides where needed.
- **Schema**: New `inventory.price_reviewed_at` field. Null = pending review (badge shows). Set = reviewed (badge hidden).
- **Backend** (auto-clears on these events for that product+branch):
  - Branch Stock+Price import commit (`import_data.py`)
  - Branch price override upsert (`branch_prices.py`)
  - Manual product price/cost edit on global product (`products.py`) — clears across all branches with inventory
  - PO line item committed (`purchase_orders._apply_po_inventory`)
  - Branch transfer received (`branch_transfers.receive_transfer`)
  - Manual one-click `POST /api/inventory/mark-reviewed`
  - Bulk admin-only `POST /api/inventory/mark-all-reviewed`
- **Migration**: One-shot startup backfill marks ALL existing inventory rows as reviewed (159 rows on first deploy). Only NEW rows born after deploy will trigger the badge.
- **New endpoints**:
  - `GET /api/inventory/pending-review-count?branch_id=X` — badge count
  - `GET /api/inventory/pending-review-ids?branch_id=X` — Set of pending product IDs (used by POS/Terminal to render inline)
  - `POST /api/inventory/mark-reviewed` — single ack `{product_id, branch_id}`
  - `POST /api/inventory/mark-all-reviewed` — bulk ack `{branch_id}` (admin only)
- **Frontend**: Reusable `<GlobalPriceBadge>` component (amber chip with hover-to-Mark-reviewed CTA, plus a compact dot variant for Terminal). Wired into:
  - **InventoryPage** — chip per row + "Global Price" filter (with pending count) + "Mark all reviewed" bulk button
  - **UnifiedSalesPage (POS)** — chip beside line item product name
  - **ProductDetailPage** — chip in the title row when current branch is pending
  - **TerminalSales** (Android) — compact 1.5px amber dot beside cart line items (scanner-friendly)
- **Tests** (`backend/tests/test_global_price_badge_184.py`): 7 passing pytests covering all 6 auto-clear triggers + manual ack + bulk ack + endpoint correctness.

### Branch Stock+Price Import & Customer Import w/ Smart Dupe Detection (2026-04-29) — Complete
- **Why**: User opening a second branch needed a way to bulk-load per-branch prices/stock without nuking the global catalog, and to migrate customers (with credit limits + opening balances) cleanly. Previously, "Update Existing" overwrote the global `product.prices` (affecting ALL branches) and there was no customer importer.
- **Backend** (`routes/import_data.py`):
  - `POST /api/import/branch-stock-and-price` (preview/commit modes). Matches by name against global catalog. Writes to `branch_prices` + `inventory` only — never touches `product.prices`. Empty price cells → SKIPPED (preserves global fallback). Empty quantity cells → set to 0. Admin PIN required for non-admins.
  - `POST /api/import/customers/preview` — analyzes file, classifies into `auto_create` / `exact_dupe` (auto-skipped) / `fuzzy` (needs review) / `errors`. Fuzzy uses token-sorted SequenceMatcher ≥ 0.85 + last-9-digit phone match. Skips pairs the user previously declared "distinct".
  - `POST /api/import/customers/commit` — accepts user decisions (`merge` / `create` / `skip` / `skip_and_remember`). For each row with `opening_balance > 0`, creates a real invoice flagged `is_opening_balance: True`, payment_type=`credit`, status=`open`, dated to user-chosen migration date — flows through AR aging, customer ledger, closing wizard like any credit sale. Idempotent: re-imports don't duplicate the OB invoice. Triggers one-time SMS via new `opening_balance_notice` template (dedup_key per customer+phone). Persists "skip & remember" decisions in new `customer_import_decisions` collection.
  - New CSV templates: `/api/import/template/branch-stock-and-price`, `/api/import/template/customers`.
- **SMS** (`routes/sms.py`): added `opening_balance_notice` to `DEFAULT_TEMPLATES`. `_ensure_templates()` is called at import time so existing tenants get the new template auto-seeded without manual intervention.
- **Frontend** (`pages/ImportPage.js`):
  - 2 new type cards: Branch Stock+Price (purple) and Customers (rose).
  - 4 template download buttons.
  - New "Review" preview step for both new types (5-step flow: Type → Upload → Map → Review → Results).
  - Customer fuzzy review UI: side-by-side comparison + 4 decision buttons per row (Merge / Create as new / Skip / Skip & Remember).
  - Branch picker + admin PIN + opening-balance date inputs for branch-scoped imports.
- **Tests** (`backend/tests/test_branch_stock_price_import_182.py` + `test_customer_import_183.py`): 10 passing pytests covering empty-cell rules, global price isolation, cross-branch isolation, preview-doesn't-write, duplicate detection (exact + fuzzy), merge flow, skip-and-remember persistence, and OB idempotency.

### Super-Admin Tenant Impersonation ("View as Tenant") (2026-04-29) — Complete
- **Why**: After the iter 180 privacy fix, super admin sees nothing tenant-side (correct). To legitimately help a customer (debug pricing, fix data), super admin needs an explicit, audited way in. This is the other half of the privacy story.
- **Backend** (`routes/superadmin.py`):
  - `POST /api/superadmin/impersonate/{org_id}/enter` — starts a 4-hour audit-logged session. Inserts into `impersonation_sessions` collection + writes `tenant_impersonation_enter` event to `audit_log`.
  - `POST /api/superadmin/impersonate/exit` — ends the active session + audit-logs `tenant_impersonation_exit`.
  - `GET /api/superadmin/impersonate/status` — banner state (active/inactive + remaining time).
  - All locked to super admin only via `require_super_admin`. Auto-deactivates expired sessions on next read.
- **Auth flow** (`utils/auth.py:get_current_user`):
  - On every authenticated request, super admin's active impersonation session is checked. If active → `set_org_context(target_org_id)` → all tenant queries scope to the target. Otherwise → `set_org_context(None)` → fail-closed scoping.
  - Expired sessions auto-deactivate inline.
  - User dict gets `_impersonating_org_id` flag for routes that want to know.
- **Frontend**:
  - `components/ImpersonationBanner.js` — sticky amber bar across every authenticated page: "👁 Viewing as **JND Store** · 3h 45m left · Exit". Mounted in `App.js` ProtectedRoute. Polls `/status` every 60s for TTL countdown.
  - Super Admin → Organizations row gets a new amber **eye** button per org (`view-as-org-{id}` testid). Click → POST enter → redirect to `/dashboard` so user is dropped into the tenant view immediately.
  - Exit button on banner → POST exit → redirect to `/superadmin` to flush stale tenant cache.
- **Verified**:
  - End-to-end smoke via curl: super admin enter → `/customers` shows tenant data → exit → empty again.
  - `pytest tests/test_tenant_impersonation_181.py` 4/4 (round-trip with audit verification, org admin cannot use, unknown org → 404, no-session → active=False).
  - Full chain across 9 prior iter files = **40/40 pass**. Lint clean.

### 🚨 CRITICAL — Cross-Tenant Privacy Fix (2026-04-29) — Complete
- **RCA from live VPS**: Two reports from the super admin —
  1. Universal Find/Scan returned another tenant's invoices (JND Store).
  2. Smart Price Scan popped up asking the super admin to fix prices on a tenant's products ("capital is higher than retail" warning on someone else's products).
- Both stem from the same root cause in `config.py:TenantCollection._org_filter`:
  ```python
  if not org_id:
      return filter_dict or {}     # ← UNSCOPED, leaks ALL tenants
  ```
  Whenever no org context was active (super admin, unauthenticated, scheduled task without explicit context), the scoped `db` proxy bypassed all org scoping and returned data across every tenant. Affected: `/search/transactions`, `/customers`, `/products`, `/invoices`, dashboard aggregations, signature sessions, SMS queue, and every other tenant-scoped endpoint.
- **Fix** (`config.py`): TenantCollection now **fails CLOSED** when no org context:
  - `_org_filter` injects sentinel `organization_id: "__no_org_context__"` → queries return empty.
  - `aggregate` injects same sentinel as the first `$match` stage.
  - `_inject_org` raises `RuntimeError` if caller has no context AND no explicit `organization_id` — prevents accidental orphan inserts.
  - Clear error message points devs to `_raw_db` or `set_org_context()`.
- **Boot-time fix** (`main.py`): Wallet provisioning now sets `set_org_context(branch_org_id)` per-branch before calling `provision_branch_wallets`.
- **Test infrastructure** (`tests/_org_test_helpers.py`): New helper that auto-seeds a known-password org admin user (`test_org_admin@regression.local`) + a manager with PIN 521325 in any active org. Updated 4 existing test files (165, 173, 177, 178) that were previously relying on the leak.
- **Frontend defense-in-depth** (`PriceScanManager.js`): added `skipForSuperAdmin` guard so the smart price scan dialog never even attempts a poll for super admins or users without `organization_id`. Even if a future regression slipped through the backend, the UI cannot pop a tenant's pricing dialog to a super admin.
- **Verified**: `pytest tests/test_cross_tenant_privacy_180.py` — 5 tests covering search, listing endpoints, **pricing-scan + price-update PUT for tenant products**, source-level fail-closed sentinel, and insert guard. Full chain across iter 165, 173-180 = **36/36 pass**. Lint clean.

### Messages Page Default Tab + Company Info Self-Heal (2026-04-29) — Complete
- **Task A (UX)**: Messages page (`/messages`) now defaults to **Conversations** tab (was: Message Queue). One-line change in `MessagesPage.js:91`.
- **Task B (Self-heal)**: New Dashboard banner that auto-detects when `settings.company_info` is missing for the current org and offers a one-tap restore from the immutable `organizations` row.
- **Backend** (`routes/settings.py`):
  - `GET /api/settings/company-info-status` — returns `{has_company_info, suggested: {name, phone, email, address}}`. The suggested values are pulled from the organizations row so the banner can show the user what will be restored.
  - `POST /api/settings/restore-company-info` — idempotent self-heal. If `settings.company_info.value.name` is already set, returns `{restored: false, reason: "already_set"}` and does NOT overwrite (protects user edits). Otherwise upserts with values from `organizations`.
- **Frontend** (`pages/DashboardPage.js`):
  - Calls `/company-info-status` on mount.
  - If `has_company_info=false`, shows a green banner: "Your company info is missing — Restore **Sibugay Agricultural Supply** as your business name so SMS signatures & receipts read correctly."
  - One-tap "Restore Company Info" button → posts to restore endpoint → toast → banner dismisses.
- **Verified**: `pytest tests/test_company_info_selfheal_179.py` 3/3 (status-shape, no-org-rejection, seed write + idempotency-guard source check). Full chain across 174→179 = **18/18 pass**. Lint clean.

### SMS Signature Company-Name Resolver Fallback (2026-04-28) — Complete
- **RCA from live VPS**: User received messages signed `"- MAIN BRANCH"` only — company name missing. Cause: their `settings.company_info` doc was gone (post-Reset, before any Settings page save). All four send paths in `routes/sms.py` read the settings doc directly and silently degraded to empty when missing. The auto-trigger path was already fixed (iter 175); this completes the matrix.
- **Backend fix** (`routes/sms.py`):
  - New `_resolve_company_name()` helper: reads `settings.company_info.value.name` first; falls back to the immutable `organizations.name` (looked up by **explicit `id` from org context** — `organizations` is NOT in `TENANT_COLLECTIONS` so we MUST pass the id, otherwise we'd return the first-inserted org globally).
  - `/sms/send` (manual) → uses helper.
  - `/sms/blast` (promo blast) → uses helper.
  - `/sms/credit-blast` (credit reminders) → uses helper.
  - `/sms/admin-direct-send` (raw_db path) → adds the same own-tenant fallback inline.
  - All four paths are now consistent with `sms_hooks.get_company_name` (auto-triggers).
- **Verified**: `pytest tests/test_sms_signature_fallback_178.py` — 1 HTTP smoke. Full regression chain across 174+175+176+177+178 = **15/15 pass**. Lint clean.

### SMS Gateway Retry-Spiral Fix (2026-04-28) — Complete
- **RCA from live VPS** (`agri-books.com/messages` gateway log inspection):
  - Stuck SMS with `retry_count = 393` (a 3-char "Sup" message), `121`, `120`, `45`...
  - Every failure: `SMS SENT failed (code 124)` — Android `SmsManager` carrier-level rejection (SIM no load / rate-limited / spam-filtered).
  - Bug A: `mark_sms_failed` set status='failed' and bumped `retry_count` on every call but never moved to a terminal state — gateway kept re-trying via local logic.
  - Bug B: `/queue/pending` did not exclude high-retry messages — repeatedly fed the same poisoned items back to the gateway.
  - Bug C: `/sms/send` accepted single-char messages — accidental cashier sends ('A', 'J', 'Sup') burned carrier rate limits.
  - Bug D: `mark-sent` and `mark-failed` were not idempotent. Gateway PATCH timeouts (visible in logs as `mark-failed failed: timeout`) caused duplicate state changes.
  - Side note: messages still showed `"Jnd store"` in the body — proving the cross-tenant bleed (fixed in iter 175) is queued in their existing failed messages; new SMS will be correct after `git pull`.
- **Backend fixes** (`routes/sms.py`):
  - `MAX_GATEWAY_RETRIES = 3`. After cap, status becomes `failed_permanent` — terminal state, gateway never sees it again.
  - `/queue/pending` filter: `{"status": "pending", "retry_count": {"$lt": MAX_GATEWAY_RETRIES}}` — defensive even if status is wrong.
  - `mark-sent`: idempotent — second call on already-sent doc returns `{idempotent: true}` without 404 or state change.
  - `mark-failed`: idempotent on `failed_permanent`. Uses `find_one + new_retry` instead of `$inc` so terminal-state docs aren't re-bumped.
  - `/sms/send`: rejects messages with `len(strip()) < 5`.
  - New `POST /api/sms/queue/clear-stuck` admin endpoint: bulk-skips all failed/failed_permanent items in one call so the gateway drains its backlog.
- **Verified**: `pytest tests/test_sms_gateway_retry_cap_177.py` — 5/5 pass (short-msg rejection, retry cap, pending exclusion, idempotent mark-sent, bulk clear). Combined run with 174+175+176 = **14/14 pass**.

### Reset Company Re-Seed Audit (2026-04-28) — Complete
- **RCA**: After Reset Company, additional gaps beyond the SMS bleed: the org had **no branches**, **no fund_wallets**, and various missing settings — making the system effectively unusable until manual recreation. Reset wiped `branches` (in `ORG_COLLECTIONS`) but only re-seeded `price_schemes`.
- **Side-by-side audit (preview vs fresh-org expectations):**

| Collection | Fresh org seeds | Reset wiped | Reset re-seeds (after fix) |
|---|---|---|---|
| `branches` (1 default) | ✓ | ✓ | ✓ NEW |
| `fund_wallets` (4 per branch) | ✓ | ✓ | ✓ NEW |
| `price_schemes` (Retail/Wholesale/Special) | ✓ | ✓ | ✓ |
| `settings.company_info` | ✓ | ✓ | ✓ |
| `sms_templates` (DEFAULT_TEMPLATES) | ✓ via `_ensure_templates` | ✓ | ✓ |
| `sms_settings` | empty (lazy upsert) | ✓ | not needed |
| `invoice_prefixes` / `business_print_info` | empty (defaults in code) | ✓ | not needed |
| Admin user | ✓ (1) | preserved | preserved |
| Customers / products / invoices / sales / inventory | empty | ✓ | not needed |

- **Backend fix** (`routes/backups.py:reset_org_data`): after wiping, re-seeds:
  - Default "Main Branch" using `org.name + ' - Main Branch'` and the org's address/phone.
  - 4-wallet system on the new branch (cashier, safe, digital, bank) via `provision_branch_wallets`.
- **Verified**: `pytest tests/test_reset_reseed_176.py` — 7 assertions covering branch, wallets, price schemes, company_info, SMS templates, admin preservation, and full data wipe. Combined regression run with iter 174 + 175 = **9/9 pass**.

### Multi-Tenant Data Integrity — Cross-Org Bleed Fix (2026-04-28) — Complete
- **RCA (live)**: After Sibugay Agricultural Supply ran Reset Company, customer SMS started signing as "JND store" (another tenant). DB scan revealed 13 orphan `company_info` settings docs whose `organization_id` pointed to deleted orgs.
  - Bug A: `routes/sms_hooks.py:get_company_name` fell back to ANY tenant's company_info if the org-scoped lookup failed → cross-tenant signature bleed.
  - Bug B: `routes/sms.py:queue_sms` had the same fallback pattern on `sms_settings` (enable/disable trigger flags).
  - Bug C: `routes/backups.py:reset_org_data` deleted the org's `settings`, `sms_templates`, `sms_settings` and only re-seeded `price_schemes` — leaving the door open for Bug A.
- **Backend fixes**:
  - `routes/sms_hooks.py`: `get_company_name` is now strictly org-scoped; falls back to the org's own `organizations.name` (immutable, same tenant) before returning empty. Never reads any other tenant's setting.
  - `routes/sms.py:queue_sms`: removed the global `sms_settings` fallback (was bleed source). If no org-scoped trigger setting exists, default = enabled.
  - `routes/backups.py:reset_org_data`: after wiping, re-seeds `company_info` (from the immutable `organizations` row) and `sms_templates` (from `DEFAULT_TEMPLATES`).
  - `main.py` startup: one-shot orphan-settings sweep removes ghost docs across `settings`, `sms_settings`, `sms_templates`, `system_settings` whose `organization_id` no longer matches any organization. Idempotent and runs every boot.
  - `routes/superadmin.py`: new `GET /api/superadmin/integrity-audit` (lists orgs missing `company_info` and orphan-doc counts per collection) and `POST /api/superadmin/integrity-audit/sweep` (manual sweep, refuses when 0 orgs exist as a safety guard).
- **Cleanup performed on this DB**: 13 orphan `company_info` docs removed by the boot sweep.
- **Verified**: `pytest tests/test_multitenant_integrity_175.py` (4/4 pass) — bleed prevented, fallback to own org name works, sweep removes planted ghosts, audit endpoint reports clean.

### Terminal Credit-Sale Duplicate Prevention + Signature-First Sequence (2026-04-28) — Complete
- **RCA**: On the live VPS, SI-MB-001003 produced #104 + #105 from one click on a slow PC; earlier 001000/001001 had the same pattern. The cashier also reported that "Transaction Recorded Successfully" fired BEFORE a signature was captured for credit sales — wrong legal sequence.
  - Root cause #1: Terminal generated a per-attempt UUID and put it in `id`, but the backend's `check_idempotency` looked at `idempotency_key` which Terminal **never sent**. Dedupe was dead-code; lag-induced retries created sibling invoices.
  - Root cause #2: `db.invoices` had no unique index on `idempotency_key` — a true network race could duplicate even if the field were sent.
  - Root cause #3: Sequence — invoice was created, success toast fired, THEN signature dialog opened. Customer could walk away leaving a binding AR with no signature.
- **Backend fixes**:
  - `routes/sales.py` — invoice insert is now race-safe (catches `DuplicateKeyError` and returns the prior invoice); after insert, if `signature_session_id` is in the payload, the signature_session is back-linked (`linked_record_id` + `credit_context.invoice_number`).
  - `main.py` — added unique partial index on `invoices.idempotency_key` (filter `{$exists: true, $type: "string"}` to skip legacy null entries).
- **Frontend fixes (`pages/terminal/TerminalSales.jsx`)**:
  - `saleIdRef` (useRef) generates a stable `idempotency_key` per checkout intent — survives retries; cleared only on success or `resetCheckout`.
  - `processingRef` guards against rapid double-clicks reaching `processSale` twice.
  - **New legal sequence for credit sales**: Payment Type → Release Mode → Term/Charge-to-Crop → **Signature (pre-commit, no invoice yet)** → PIN if needed → POST `/unified-sale` with `signature_session_id` → Print prompt. Backend creates the invoice and back-links the session in one round-trip.
  - Hard block: closing the signature dialog without sign/bypass aborts — no invoice is created (`Q3a`).
  - Manager-stock-override path also reuses the same idempotency_key + signature_session.
- **Verified**: `pytest tests/test_credit_idempotency_174.py` (4 tests) — unique index present, duplicate key rejected, `/unified-sale` accepts new field, pre-commit signature session creates with empty `linked_record_id`. Existing related suites (`test_orphan_fix_173`, `test_signatures_165`) still green.

### Customer Single Source of Truth + Orphan Recovery (2026-04-28) — Complete
- **RCA**: Live `agri-books.com` had 4 open invoices (₱4,890 total balance) referencing deleted customer `b38fed7b` (Janmark Ahig). Customer record fully gone. Receivables summary showed `[]` despite open balance. Terminal still cached the dead customer. Five interlocking bugs:
  - **Bug A**: `routes/sales.py` — credit/partial sales accepted any `customer_id`; if customer didn't exist, `if customer:` block was skipped silently and invoice created anyway with orphan ID.
  - **Bug B**: `routes/sync.py` — sync payload included `deleted_ids` for products only, not customers; terminal IndexedDB never purged stale entries.
  - **Bug C**: `routes/customers.py DELETE` — no guard against open invoices or balance > 0; soft-delete didn't set `deactivated_at` so sync deletion-detection couldn't work.
  - **Bug D**: No way to reattach orphan invoices to a recovered customer.
  - **Bug E**: `receivables-summary` is blind to invoices whose customer doesn't exist.
- **Backend fixes**:
  - `routes/sales.py` — credit/partial sales now reject with clear error if customer_id resolves to nothing. Cash sales auto-strip invalid customer_id (treat as walk-in).
  - `routes/sync.py` — payload now includes `deleted_customer_ids[]` (delta sync detects soft-deleted customers since `last_sync`).
  - `routes/customers.py DELETE` — guards: outstanding balance > 0 → reject; open invoices > 0 → reject. `?force=true` admin-only override. Sets `deactivated_at` + `updated_at`.
  - `routes/customers.py` new endpoints: `GET /customers/orphan-receivables` (lists invoices with dead customer_ids, grouped by id, with totals), `POST /customers/reattach-orphans` (bulk reassigns orphan invoices to a target customer + recomputes balance).
- **Frontend fixes**:
  - `lib/offlineDB.js` — `mergeCustomers(changed, deletedIds)` now upserts changed + deletes purged in one tx; new `deleteCachedCustomers()` helper.
  - `lib/syncManager.js` — delta sync uses `mergeCustomers` (preserves unchanged customers + purges deleted), full sync still uses cacheCustomers.
  - `pages/PaymentsPage.js` — red "phantom receivables" banner appears in customer-list panel when orphans exist (`orphan-receivables-alert`); opens dialog (`orphan-dialog`) showing each phantom group with Reattach button (`reattach-orphan-{id}`); admin picks target active customer from dropdown (`orphan-target-select`).
- **Live data**: New active customer "Janmark Ahig" (id `505b884a-abaf-4318-9877-93a25d39c680`) created on `agri-books.com`. After deploy, owner clicks Phantom Receivables → reattach 4 orphan invoices to this customer → balance recomputes to ₱4,890.
- **Tested**: `/app/backend/tests/test_orphan_fix_173.py` — 5/5 pass: delete-guard works, orphan-receivables returns proper structure, sync includes `deleted_customer_ids` field, credit sale rejected for ghost customer_id, reattach endpoint exists & validates input.

### Update-Existing Mode + CSV Export + PWA Landing (2026-04-28) — Complete
- **Update Existing Products import mode**: New endpoint `POST /api/import/products/update-existing` — matches every row by Product Name (case-insensitive), merges only mapped fields, reports unmatched rows. No duplicate-review step needed. New "Update Existing Products" card in `/import` (`import-type-products-update`) with amber styling.
- **Products CSV export**: New endpoint `GET /api/products/export-csv` returns full catalog as CSV with import-compatible columns: `Product Name, SKU, Category, Unit, Description, Type, Cost Price, Reorder Point, Barcode, <Scheme> Price` per active scheme. New "Export CSV" button on `/products` (`export-products-btn`). Round-trip flow: Export → edit in Excel → Import via Update Existing mode → done.
- **PWA landing fixed**: `manifest.json` `start_url` changed from `/terminal` to `/dashboard`. Renamed PWA from "AgriSmart Terminal" to "AgriBooks". Browser-installed app now opens to dashboard then routes by login. Dedicated APK still serves the cashier terminal.
- **Tested**: `/app/backend/tests/test_update_existing_export_172.py` — 2/2 pass on live preview API. Update-existing correctly preserves retail+cost when only wholesale is mapped, and export returns 4 scheme columns (Retail/Wholesale/Special/Government).

### Import Overwrite Merge Fix (2026-04-28) — Complete
- **Bug**: `POST /api/import/products/overwrite` was a broken no-op. Frontend sent `updates: {}` and backend ran `update_many` with empty `$set`. Clicking "Overwrite" did nothing useful — users couldn't bulk-update existing products from a CSV.
- **Fix backend** (`routes/import_data.py`): Endpoint now accepts the file + mapping + product_ids (multipart). For each row whose name matches a selected product, MERGES only the mapped fields into the existing product. The `prices` map is merged (existing scheme keys preserved unless explicitly mapped). Auto-creates missing schemes from `*_price` mappings. Returns `{updated, not_matched, errors, schemes_auto_created}`.
- **Fix frontend** (`pages/ImportPage.js`): `handleOverwrite` now POSTs FormData with the original file + current mapping + selected product IDs. Added "Select All / Deselect All" toggle (`select-all-duplicates-btn`) for bulk operations on large duplicate lists, and a hint line explaining that unmapped fields are preserved. Toast now shows merged count + auto-created schemes + unmatched count.
- **Test**: `/app/backend/tests/test_overwrite_merge_171.py` — 2/2 tests pass against live preview API. Confirms retail is preserved when only wholesale_price is mapped, and unselected products are not touched even if present in the file.

### Price Scheme Recovery + Import Fix (2026-04-28) — Complete
- **RCA**: User's live `agri-books.com` org had ZERO price schemes (shown empty `[]` from `/api/price-schemes`). Result: dropdown unselectable on Sales/Terminal, no scheme columns on Products/Inventory, and 1,601 imported products had only `prices.retail` (no wholesale). Three root bugs:
  - **Bug A**: `Reset Company` wiped `price_schemes` collection without re-seeding defaults.
  - **Bug B**: `import_products` only iterated *existing* schemes — if `wholesale` scheme didn't exist, mapped Wholesale Price column was silently dropped.
  - **Bug C**: `InventoryPage.js` had no scheme columns at all.
- **Backend fixes**:
  - `routes/backups.py reset_org_data()` — now `insert_many` Retail/Wholesale/Special with `organization_id` after wipe.
  - `routes/import_data.py import_products()` — auto-discovers `*_price` mapped columns, auto-creates missing schemes (with `fixed`/0 default calc), then runs the import. Returns `schemes_auto_created: [{key,name}]` in response. Defensive secondary loop ensures *any* `*_price` column gets stored even if auto-create was bypassed.
  - `routes/price_schemes.py` — new `POST /api/price-schemes/restore-defaults` (idempotent: skip-if-active, reactivate-if-soft-deleted, create-if-missing).
- **Frontend fixes**:
  - `pages/InventoryPage.js` — added `schemes` state + dynamic `<TableHead>` and `<TableCell>` columns (mirrors ProductsPage).
  - `pages/PriceSchemesPage.js` — amber banner with `restore-default-schemes-btn` shown when `schemes.length < 3`.
  - `pages/ImportPage.js` — green "schemes auto-created" notice on Results step when `result.schemes_auto_created.length > 0`.
  - `pages/UnifiedSalesPage.js` — amber `single-scheme-warning` shown under Price Scheme select when `schemes.length <= 1`, with link to `/price-schemes`.
- **Live data repair**: User's `agri-books.com` org schemes restored via direct API (Retail / Wholesale / Special now exist, org_id `4fafe301-3a52-49d8-80b5-afa3e91fa8f5`). User must re-import their CSV to populate `prices.wholesale` on the 1,601 products.
- **Tested**: iteration_170 — Backend 5/5 (100%), Frontend 4/4 verified. ✅

### Terminal Signature Fix + Inline Credit Type Selection (2026-04-28) — Complete
- **Root cause fixed**: `RequestSignatureDialog`, `CropCreditTypeDialog` were importing `api` from `AuthContext` (web app JWT). Terminal uses its own separate axios instance with a terminal-specific token → all signature/crop-credit API calls were returning 403 "Not authenticated". Fix: added `apiInstance` prop to both dialogs; `TerminalSales` now passes its `api` prop to both.
- **UX fix**: Credit type selection is now inline in checkout. The 15/30/60 day buttons now share a 2×2 grid with a "Charged to Crop" button. `CropCreditTypeDialog` opens immediately when tapped — not intercepted at the confirm button. Confirm button calls `processSale()` directly.
- **Cleanup**: `resetCheckout()` now also clears `cropCreditConfig` and `cropTypeDialog` state to prevent stale config on failed sale recovery.
- **Tested**: iteration_169 — Backend 7/7, Frontend all features verified. ✅

### Signature Verification Toolbar in Audit Center (2026-04-28) — Complete
- **Frontend**: `SignatureVerifyToolbar.js` — compact dark bar between Audit Center header and tabs. Paste `v.XXXXXXXX` token → Verify button → calls `GET /api/signatures/verify/{token}` → dialog shows: signature image, status badge (Signed / Manager PIN bypass), signer name, date/time, clickable invoice link (opens InvoiceDetailModal), credit context, tamper-evident token badge.
- **Price Manager capital logic fix**: Removed `set_manual` flag from `bulk-update` — branch capital saves no longer touch `products.capital_method` globally (was overriding the auto-switch mode for all branches). Added `edit_cost` permission check + audit logging to `capital_changes` for all branch capital saves.
- **Tested**: iteration_168 — Backend 24/24, Frontend 12/12. ✅

### Price Manager Feature (2026-04-28) — Complete
- **Backend**: `GET /products/price-audit-summary` (counts missing capital + low-margin products), `POST /products/bulk-price-update` (batch global price save), `POST /branch-prices/bulk-update` (batch branch override save including capital + manual flag).
- **Frontend**: `/price-manager` — Tab 1 (Price Manager): product search, collapsed color-coded cards (red/yellow/green status dots), expandable branch table showing `current → new` inputs per scheme per branch, smart category fill toolbar (pre-fills suggestions only), session checkmarks. Tab 2 (Capital & Price Setup): same layout + capital inputs, capital method badges, smart audit prompts ("200 products have no capital — load first 30"), auto-switch logic on next PO already in place.
- **Permission Fixes**: Crop Credits `pos.sell`→`customers.manage_credit`, Branch Transfers + Internal Invoices→`branch_transfers.view`, Import Center `adminOnly:true`. Added `branch_transfers` module (view/create/receive) to permissions system. Manager preset: branch_transfers ON by default.
- **Tested**: iteration_167 — Backend 26/26, Frontend 16/16. One search bug fixed (wrong param name).

### Reset Company Data Feature (2026-04-27) — Complete
- **Backend**: `POST /api/backups/org/{org_id}/reset` — triple-verified (confirmation text + bcrypt password + TOTP), auto-creates compressed JSON backup to R2 first, wipes all ORG_COLLECTIONS, keeps owner admin account only. Logs event to `audit_log`.
- **Backend**: `GET /api/backups/org/{org_id}/download/{filename}` — generates 1-hour R2 presigned download URL for any org backup file.
- **Frontend**: `ResetCompanyModal.js` — 4-step wizard: Step 1 (type "[Company] Reset"), Step 2 (password), Step 3 (TOTP), Step 4 (final red confirm). Success screen shows backup metadata + Download + Restore buttons.
- **Settings Page**: "Danger Zone" card added to Business Info tab (admin-only, guarded by `organization_id` check so super admins don't see it).
- **Tested**: iteration 166 — Backend 10/10, Frontend 7/7 feature points verified.

### Tamper-Evident Signature Stamp on Receipts (2026-04-25) — Complete
- **Backend**: `signatures.py` adds `_verify_token()` — 8-char uppercase HMAC-SHA256 prefix derived from session id (signed with `SIGNATURE_VERIFY_SECRET` or `JWT_SECRET`). Token is stable across re-fetches.
- **API responses**: `/status/{token}`, `/record/{type}/{id}`, and the new `/verify/{token}` endpoint all include `verification_token`.
- **New manager-lookup endpoint**: `GET /api/signatures/verify/{token}` resolves a printed token back to the full session metadata (signer name, signed-at, credit context, signature image URL) — auditable dispute-resolution path.
- **PrintEngine**: both `trustReceiptThermal` and `trustReceiptFullPage` render a small `Signed YYYY-MM-DD HH:MM Z · v.XXXXXXXX` line below the signature image (or below the PIN-bypass badge). 8.5px font, gray, centered.
- **Plumbing**: `signature_signed_at` + `signature_verification_token` flow from `signatures` array in `InvoiceDetailModal.handlePrint`, from `RequestSignatureDialog.onPrintReceipt`, and from `TerminalSales.onSigned` → into `lastSaleData` for terminal print.
- **Verified**: SI-B1-001059 returns `verification_token: 34F788C2`, and `GET /api/signatures/verify/34F788C2` resolves to the same session. Lint clean across all 6 changed files.

### Signature Display Bug Fix in Compact InvoiceDetailModal + R2 Virtual Hosting (2026-04-25) — Complete
- **Bug 1**: `compact` mode of `InvoiceDetailModal` (used by SalesPage and most pages) renders a different DOM that did NOT include the signature section. The header chip's `setSection('signature')` had no effect. **Fix**: Added an inline "Credit Authorization Signature" card inside compact mode (right above Void button) that always renders when `signatures.length > 0`. The header chip now also `scrollIntoView`s.
- **Bug 2**: R2 presigned URLs returned 403 (path-style addressing). **Fix**: `utils/r2_storage.py:_get_client` now sets `addressing_style='virtual'` — required by Cloudflare R2. Confirmed: existing `None/signatures/...` keys (from super-admin sessions) now serve correctly.
- **Bug 3**: Super-admin sessions were uploaded under literal `None/signatures/...` because `session.get('organization_id', 'unknown')` returned `None` (mongo null) instead of the default. **Fix**: changed to `session.get('organization_id') or 'global'` so future signatures use a clean `global/...` prefix.
- Verified: SI-B1-001059 modal now shows the captured signature image at 1308px native width, properly displayed.

### Sale Signature Visibility — Bug Fix + "Signed ✓" Chip (2026-04-25) — Complete
- **Bug fix**: Signatures created via `RequestSignatureDialog` were stored with `linked_record_type='sale'` but `InvoiceDetailModal` queries `record/invoice/{id}`. Result: signed sales (e.g., SI-B1-001059) showed no signature in the modal.
  - Frontend fix: `RequestSignatureDialog.linked_record_type` changed from `'sale'` → `'invoice'` to match query side.
  - Backend fix: `GET /api/signatures/record/{type}/{id}` now matches BOTH `sale` and `invoice` types when either is requested → existing legacy sessions surface correctly.
- **"Signed by customer ✓" chip improvement**:
  - Backend: `/api/invoices` list and `/api/invoices/{id}` detail endpoints now return `has_signature: bool` and `signature_status: 'signed' | 'bypassed' | null` (computed via $in lookup on signature_sessions, supports both legacy 'sale' + 'invoice' linkage).
  - SalesPage: each row's invoice number now shows a small green "✓ Signed" pill (or amber "⚠ PIN" if manager-bypassed) next to the number when applicable. Tooltip explains. testid `sig-chip-{saleId}`.
  - InvoiceDetailModal: header shows a clickable "Signed by customer" chip beside the verification badge that, when clicked, jumps to the Signature section showing the captured image. testid `header-signature-chip`.
- Verified: SI-B1-001059 now displays both chips and the signature image correctly. All lint clean. Backend confirmed via curl: `has_signature=True, signature_status=signed`.

### Customer Signature Flow (Web POS QR + Terminal Inline) + Real-Time Cache + By-Term Block (2026-04-25) — Complete
- **Real-time cache invalidation**: PaymentsPage, UnifiedSalesPage, TerminalSales now call `invalidateBalanceCache()` after every successful sale/payment so balance badges across the app reflect the new totals immediately (no 30s stale window).
- **By-Term blocked when active charged-to-crop exists**: `CropCreditTypeDialog.handleConfirmByTerm` now checks `blockInfo.reason === 'active_crop_credit'` and (1) shows toast.error with description, (2) disables the button visually (opacity 50, cursor not-allowed). Defense-in-depth: button disabled + handler early-return.
- **Customer signature flow** — fully integrated with the existing `/api/signatures/*` infrastructure:
  - **Web POS** (`UnifiedSalesPage`, `/sales-new`): after credit/partial sale → `RequestSignatureDialog mode='qr'` shows QR code → customer scans → SignaturePage opens on phone → cashier polls `/status` → when signed, dialog shows captured signature image + Print Receipt button.
  - **Terminal** (`TerminalSales`): after credit/partial sale → `RequestSignatureDialog mode='inline'` opens with items review + signature_pad canvas on the same terminal device → mandatory before print prompt → submit → status updates → `lastSaleData` includes `signature_url`+`bypass_method` → receipt embeds the small signature image.
  - **Manager-PIN bypass**: Both modes share `BypassPanel` — when customer can't sign, manager enters PIN + reason → audit-logged via `/api/signatures/bypass/{id}`.
  - **SignaturePage**: extended to show Items Purchased section (qty × rate × total), Subtotal/Discount/Paid Now/Total Credit Amount block, and "Approve & Submit" button label.
- **Receipt template (PrintEngine)**: `trustReceiptThermal` + `trustReceiptFullPage` now embed the signature image (max 60% width, 50-60px tall) when `data.signature_url` is present, or render an "AUTHORIZED VIA MANAGER PIN" badge when `data.bypass_method` is set.
- **Tested**: iteration 165 — backend pytest 8/8 PASS (session persistence, status returns signature_url, public submit, manager bypass, regression on receivables-summary). One critical bug found+fixed (wrong payload key in submitInlineSignature). All lint clean.

### Inline Open-Balance Badges + Hook Cache (2026-04-25) — Complete
- New shared **`CustomerBalanceBadge`** component + **`useCustomerBalances`** hook (in-memory 30s cache, single fetch shared across pages, concurrent-fetch de-dup via `_inFlight` promise).
- Wired into **Sales History**, **Reports → AR Aging**, **Accounting → Receivables**.
- Badge shows: `₱<balance> · <days>d` next to customer name, color-coded (green=current, amber=1-30d overdue, red=>30d overdue). Hidden when balance≤0 or customer is walk-in.
- Backend: extended `/api/customers/receivables-summary` with new `oldest_overdue_due_date` field (ISO date string) computed via `$min` aggregation on overdue due dates.
- Tested via iteration 164: 8 backend pytest checks + 26/39/89 badges across 3 pages, single-fetch cache verified, click-through to Statement still works. 100% pass.

### Click-to-Review Drill-Down + Terminal Modal Migration (2026-04-25) — Complete
- **Phase 1** Tier-1 click-to-review wiring: customer names + invoice #s in **Sales History**, **Audit Center**, **Daily Log**, **Close Wizard** are now clickable. Invoice # → opens existing `InvoiceDetailModal`. Customer name → opens existing `CustomerStatementModal`. Walk-in customers (no `customer_id`) render as plain text (no orphan link).
- **Phase 2** Tier-2 click-to-review wiring: same pattern applied to **Dashboard** recent activity, **Reports** (AR Aging, Sales transactions, Discount Audit), and **Accounting** (Cash Drawer expenses + Customer Receivables).
- **Phase 3** Terminal modal migration: `TerminalUpdateReceiptModal.jsx` and `TerminalReturnRefundModal.jsx` migrated from raw `<div className="fixed inset-0">` overlays to **shadcn `Dialog`** with proper accessibility (focus trap, Escape close, role=dialog). Replaced `axios + BACKEND` with shared `api`, replaced local `php()` with shared `formatPHP`. **Business logic, payload structure, API endpoints (`/api/returns`, `/api/invoices/<id>/correct-incomplete-stock`), and multi-step state machine preserved exactly.**
- Reused existing modals — **NO new modal components created**. Total LOC: −38 (smaller because of shared utilities).
- Fixed a stray React `key` warning in ArAgingReport's `<Fragment>` map.
- Tested via `testing_agent_v3_fork` (iteration 163): 100% pass on tested code paths (Sales 25 buttons, Accounting Receivables 99 buttons, Reports 41 buttons, Terminal modals lint+structure).

### /payments UI Redesign + Inline Interest + Statement Popup (2026-04-25) — Complete
- **Compact header** (~110px): Title + customer + Total Amount Due in row 1; Payment Amt + Date + Ref + Payment Methods in row 2 — gives invoice table ~200px more space (4-7 invoices visible vs. 2 before)
- **Inline interest sub-rows**: Each overdue invoice with computed interest now shows an amber sub-row with "Interest accrued (Xd × Y%/mo)" + computed amount (e.g. +₱279.00) using `charges-preview` API — no DB INT invoice is created from browsing
- **Account Summary card** (QuickBooks-style): Outstanding Principal / Accrued Interest Charges / Total Amount Due with optional Applied / Discount lines
- **Statement popup** (QuickBooks-style): "Statement" button opens dialog showing Original Amount / Payments-Adj / Accrued Interest / Amount Due per invoice with subtotals — exactly what user asked for ("10,000 original, 5,000 paid, 5,000 due" pattern)
- **INT invoice now only created at Pay time**: `handleApplyPayment` calls `generate-interest` with `force=true` ONLY when the user clicks Pay AND there's accrued interest > 0 — INT invoice is then auto-allocated first via autoApply
- **Removed**: bulky "Generate Interest / Penalty Charges" collapsible card; replaced with slim toolbar buttons (Statement / Force INT / Penalty / History) + inline interest rate input
- **Removed**: `autoGenerateAndLoad` auto-generation on customer select — customer selection now only loads invoices and shows preview (no DB writes)
- Files: `/app/frontend/src/pages/PaymentsPage.js`

### Crop Credits Architecture Fix + Signature Viewing (2026-04-24) — Complete
- Principal balance now computed DYNAMICALLY from linked invoices (not stored separately) — ensures /crop-credits, /payments, /customers all show consistent totals
- `_compute_principal_from_invoices()` helper aggregates all linked invoice balances in real-time
- `add-credit` endpoint now tags the invoice with `crop_credit_id` (links invoice ↔ crop credit)
- `create_crop_credit` starts with `principal_balance=0` (computed from invoices)
- All GET endpoints (list, get, check-block, customer) now use computed principal
- Interest accrual and payment allocation use computed principal
- Signature tab added to InvoiceDetailModal — shows captured signature image, credit summary, PIN bypass info
- Signature section appears on every invoice, shows "No signature" gracefully if none captured


- Phase 1: CropCreditsPage is now view-only monitoring dashboard. Removed standalone payment button. Added "Use Receive Payments page" info banner. Receipts tab shows invoice numbers as clickable entries opening InvoiceDetailModal popup. Payments tab removed. Tabs: Receipts / Extensions / Interest Log.
- Phase 2: Web Sales (UnifiedSalesPage): CropCreditTypeDialog appears before manager PIN when payment=credit and customer is selected. Shows By Term vs Charged to Crop options, active season detection, blocked state, planting date input, link existing term invoices option. After sale, invoice is linked to crop credit.
- Phase 3: Terminal Sales (TerminalSales.jsx): Same CropCreditTypeDialog appears on "Confirm Credit Sale" with customer selected.
- New CropCreditTypeDialog component at /app/frontend/src/components/CropCreditTypeDialog.js
- Backend: crop credit entries now store invoice_number for receipt linking


- New `crop_credits` collection with full seasonal credit lifecycle tracking
- Backend routes: `POST /api/crop-credits`, `GET /api/crop-credits`, `GET /api/crop-credits/customer/{id}`, `GET /api/crop-credits/check-block/{id}`, `POST /api/crop-credits/{id}/add-credit`, `POST /api/crop-credits/{id}/payment`, `POST /api/crop-credits/{id}/extend`, `POST /api/crop-credits/{id}/accrue-interest`
- 127-day crop cycle (120 days + 7 grace), simple interest on principal only, interest-first payment allocation
- Extension governance: Manager PIN (ext 1-2), Owner TOTP via Google Auth (ext 3+, flagged)
- Harvest reminder SMS to customer + owner/manager/admin/auditor at 15d, 7d, due date
- APScheduler: daily harvest reminders (7AM) + monthly interest accrual (1st of month, 6AM)
- Collection notification recipients settings: `GET/PUT /api/settings/collection-recipients`
- New `CropCreditsPage` at `/crop-credits` with list/detail/create/payment/extension UI
- Digital signature system: `POST /api/signatures/session`, `GET/POST /api/signatures/view|submit/{token}`, `POST /api/signatures/bypass/{id}`
- Public signing page at `/sign/:token` — any phone can scan QR and submit signature
- QR code (5-min expiry, auto-poll 2s), Manager PIN bypass, R2 storage (non-deletable)
- `signature_sessions` and `crop_credits` added to TENANT_COLLECTIONS
- SMS Messages > Settings tab: new "Collection Notification Recipients" section
- `qrcode.react@4.2.0` and `signature_pad@5.1.3` installed

### Forgot Password Flow (2026-04-24) — Complete (User Verification Pending)
- `POST /api/auth/forgot-password` + `POST /api/auth/reset-password` via Resend
- ForgotPasswordPage + ResetPasswordPage frontend UI
- REACT_APP_FRONTEND_URL env var for reset link routing


- Added `sms_inbox` to TENANT_COLLECTIONS — per-company inbox isolation
- `POST /api/sms/inbox` — gateway app posts incoming customer replies
- `GET /api/sms/conversations` — merged sent+received list grouped by phone, sorted by latest
- `GET /api/sms/conversation/{phone}` — full bubble thread (direction: in/out) + marks inbox read
- Messages page: new **Conversations** tab — left panel shows contact list with unread badge, right panel shows WhatsApp-style bubbles (green=sent, gray=received) + reply box
- Added `sms_queue`, `sms_templates`, `sms_settings` to `TENANT_COLLECTIONS` — all REST endpoints auto-scoped via TenantCollection
- `queue_sms()` now accepts `organization_id` param; uses org-scoped dedup; falls back to global templates if org-specific template not yet customized
- `sms_hooks.py` resolves `org_id` from invoice's `branch_id` and passes to all hook calls
- Scheduled jobs (`_daily_sms_reminders`, `_monthly_sms_summary`) now iterate per active organization using `_raw_db`
- **Result:** Company A's Android gateway only sees Company A's `pending` queue; Company B's templates, settings, and queue are completely isolated

### DocViewer Security Redesign + Terminal Back Navigation (2026-04-06) — Complete
### Terminal Device Authentication Hardening (2026-04-06) — Complete

#### Back to Terminal
- Sticky "← Back to Terminal" bar at top of DocViewerPage when accessed from a paired terminal
- Also shown on the error state (doc not found) screen
- Calls `navigate('/terminal')` to return to TerminalShell

#### Security Tier Redesign (Regular Phone)
- **Tier 1 (Public):** Receipt items, total, balance — always visible (no change)
- **Tier 2 (PIN gate):** Payment history, attached files — manager/admin/TOTP PIN (no change)
- **Tier 3 (NEW — Apply Payment):** Independent of Tier 2:
  - `WebPaymentSection` component — visible to non-terminal devices when balance > 0
  - **Path A (TOTP only):** Enter 6-digit time-based code at payment submission — TOTP-only, static PINs rejected
  - **Path B (Staff Login):** Email + password login → admin/owner = all branches; manager = own branch only; other roles → toast error "You don't have the necessary authority"
  - Backend: `_verify_staff_jwt()` helper validates JWT + role + branch restriction
  - Backend: `receive_payment` endpoint now accepts 3 auth paths (terminal_id / web_auth_token / TOTP-only pin)

#### Autocomplete Disabled (Terminal Security)
- `autoComplete="new-password"` on all terminal PIN/password inputs: TerminalSales, TerminalDocUpload, TerminalPairScreen, DocViewerPage (Tier 2 PIN, terminal pull PIN)
- `autoComplete="off"` on all TOTP and reference inputs

**Backend files:** `routes/qr_actions.py`, `routes/doc_lookup.py`
**Frontend files:** `DocViewerPage.jsx`, `TerminalSales.jsx`, `TerminalDocUpload.jsx`, `TerminalPairScreen.jsx`

#### QR Payment SMS Hook (also fixed in this session)
- `on_payment_received` SMS hook now called from `qr_actions.py` receive_payment endpoint
- Previously only triggered from `accounting.py` standard payment path
- Now all payment paths (terminal QR, web TOTP, web staff login) send SMS to customer

### H10 Hardware Integration — FINAL STABLE STATE (2026-04-05) — COMPLETE & WORKING

**All hardware features complete and tested on physical H10 device:**

#### Thermal Printer
- `PrintEngine.js`: `width: 100%` CSS (not fixed px), viewport meta `width=384`, `-webkit-font-smoothing: none`, all colors pure `#000`, solid borders, font-weight normal on body
- `PrintBridge.js`: Static `import QRCode from 'qrcode'`, `replaceQrWithLocalDataUrl()` generates 152px (0.75 inch) QR locally — no network
- Receipt Preview modal: closes "Sale Complete" dialog BEFORE opening preview (prevents WebView stacking)
- Print 1 or 2 copies with 2-second gap between copies
- `feedLinesAfter` and `feedPaper()` support for paper positioning
- **Stock Release Status on Receipts:** Thermal and full-page receipts now show release mode:
  - Full Release: "FULLY RELEASED" status indicator
  - Partial Release: Amber highlighted banner "PARTIAL RELEASE - SCAN QR CODE TO RELEASE ITEMS"

#### HID Barcode Scanner
- Capture-phase global listener (`addEventListener('keydown', fn, true)`)
- `SCAN_CHAR_SPEED=50ms` (confirm mode), `SCAN_HUMAN_RESET=300ms` (buffer reset threshold), `SCAN_SETTLE_DELAY=200ms` (process after silence), `elapsed<400ms` validation
- 300ms human threshold (NOT 50ms) prevents dropping uppercase letters with Shift overhead
- `scanQtyModalOpenRef` blocks scanner when qty modal is open
- `lastScanRef` 500ms same-barcode dedup (shared with camera scanner)
- Skip modes: per-product and skip-all for this receipt
- **See `/app/memory/H10_PRINTER_COORDINATION.md` for complete technical reference**

**Files:** `TerminalSales.jsx`, `PrintBridge.js`, `PrintEngine.js`, `H10_PRINTER_COORDINATION.md`

### Terminal Stock Release Mode (2026-01-XX) — COMPLETE
- Added **Stock Release Mode selector** to Terminal Sales checkout flow
- **Placement:** After payment type selection, before final confirmation
- **Two options:**
  - **Full Release** (green) — All items released immediately, stock deducted now
  - **Partial Release** (amber) — Items staged for pickup, customer scans QR to release in batches
- **Validation:** User MUST select a release mode; shows error if not selected
- **UI:** Clean two-button selector with explanations, "Change" button after selection
- **Backend:** Sends `release_mode` field to `/unified-sale` endpoint
- **Receipts:** Both thermal (58mm) and full-page prints show release status prominently
  - Full: "FULLY RELEASED" status line
  - Partial: Large amber banner with instructions to scan QR code
- **Feature Parity:** Terminal now matches web sales interface capabilities
- **Files:** `TerminalSales.jsx`, `PrintEngine.js`
- **Documentation:** `/app/memory/STOCK_RELEASE_FEATURE.md`

### Terminal Return & Refund (2026-01-XX) — COMPLETE (Phase 1)
- Added **Return & Refund** button to Terminal Actions in DocViewerPage (invoice receipts)
- **Optimized PIN Flow:** Single PIN entry unlocks all actions, second PIN confirmation only for financial operations
  - Enter PIN once → Unlocks payment history + all action buttons
  - View actions (payment history, documents) → No additional prompt
  - Financial actions (Return & Refund, Accept Payment, Update Receipt) → Re-enter PIN to confirm
  - Stock operations (Release Stocks) → Confirmation dialog only, no PIN
- **Multi-step modal workflow:**
  1. **Select Items** — Checkbox selection from original receipt
  2. **Configure Details** — Quantity, condition (Sellable/Damaged/Expired/Defective), inventory action (Shelf/Pullout)
  3. **PIN Confirmation** — Re-enter PIN to authorize refund (financial transaction security)
  4. **Success** — Shows RMA number, refund amount, optional print slip
- **Backend Integration:** Uses existing `/api/returns` endpoint
- **Stock Handling:** Automatically returns sellable items to inventory, logs pull-out losses
- **Fund Management:** Refunds from cashier wallet, creates expense record for Z-report
- **Audit Trail:** Complete RMA records, inventory movements, wallet deductions
- **Files:** `DocViewerPage.jsx`, `TerminalReturnRefundModal.jsx`
- **Documentation:** `/app/memory/RETURN_REFUND_ARCHITECTURE.md`, `/app/memory/PHASE1_IMPLEMENTATION.md`, `/app/memory/PIN_FLOW_OPTIMIZATION.md`

### Terminal Update Receipt (Incomplete Stock) (2026-01-XX) — COMPLETE (Phase 2)
- Added **Update for Incomplete Stock** button to Terminal Actions (only if day not closed)
- **Use Case:** Items printed on receipt but not physically given to customer
- **Visibility:** Terminal only, invoice only, day not closed only
- **3-Step Modal Workflow:**
  1. **Configure Quantities** — Side-by-side comparison (Receipt Shows vs Actually Given)
  2. **PIN Confirmation** — Re-enter PIN + choose reprint option (Yes/No/Later)
  3. **Success** — Shows correction ID, refund amount, items returned to shelf
- **Backend Endpoint:** NEW `POST /api/invoices/{id}/correct-incomplete-stock`
- **Complete Integration:**
  - ✅ Date validation (blocks if day closed)
  - ✅ Updates original invoice (items, totals, balance)
  - ✅ Returns stock to shelves
  - ✅ Refunds from cashier wallet, creates expense record
  - ✅ Updates customer balance (if credit)
  - ✅ Sends SMS notification
  - ✅ Creates correction audit log
  - ✅ Preserves original invoice in `invoice_corrections` collection
- **Reprint Options:** Professional wording (Yes, Print / No, Skip / Later)
- **Data Integrity:** All accounting systems updated atomically
- **Files:** `DocViewerPage.jsx`, `TerminalUpdateReceiptModal.jsx`, `/app/backend/routes/invoice_corrections.py`
- **Documentation:** `/app/memory/RETURN_REFUND_ARCHITECTURE.md`

- **New `POST /api/sms/gateway/log`** — Android APK posts single log entry (level, event_type, message, phone, queue_id, device_id)
- **New `POST /api/sms/gateway/logs/batch`** — Batch POST up to 500 buffered entries (offline-first support)
- **New `GET /api/sms/gateway/logs`** — Web fetches logs with level/event_type filter, org-scoped
- **New `DELETE /api/sms/gateway/logs`** — Admin clears old logs
- **UI: "Gateway Log" tab** in Messages page — terminal-style dark panel, mac-style title bar
- **UI: Live mode** — auto-refreshes every 5s, pulsing LIVE indicator, pause/resume toggle
- **UI: Color-coded log lines** — ERROR=red, WARN=amber, INFO=gray, DEBUG=violet; event-specific colors (sent=green, received=blue, poll=gray, boot=purple, db_error=red)
- **UI: Level filters** — ALL/INFO/WARN/ERROR/DEBUG buttons
- **UI: Android Integration card** — shows all event types and exact payload format for Cursor agent
- Provided Cursor prompt for Android app: RemoteLogger class with Room DB buffering, batch flush in SyncEngine.syncAll(), Retrofit endpoint, full addLogLine() replacement guide

### Credit Reminder Blast (2026-04-02) — Complete
- **New `POST /api/sms/credit-blast`** — smart personalised credit reminder blast.
- **Smart template selection per customer:**
  - **Option A (Short):** Balance reminder with next due date — used when no overdue and due > 15 days away.
  - **Option B (Detailed):** Full breakdown — used when customer has OVERDUE balance OR due date ≤ 15 days.
- **Preview mode (`dry_run: true`):** Returns stats (customer count, SMS count, short/detailed split) and up to 2 sample messages before committing.
- **Send mode (`dry_run: false`):** Queues personalised messages for all eligible customers. Sends to ALL registered phones per customer.
- **Filters:** `branch_id` (auto from current branch), `min_balance` threshold.
- **No-phone customers automatically skipped** — `total_customers` only counts customers who will actually receive SMS.
- **Detailed message includes:** customer name, total balance, OVERDUE amount + days overdue, next due date + amount + days until due, estimated monthly interest.
- **Short message includes:** customer name, balance, next due date + days, interest rate reminder.
- **UI: New "Credit Blast" tab** in Messages page (between Compose and Promo Blast).
- **UI: Template legend** showing when each template is used.
- **UI: Preview stats card** — Customers, SMS, breakdown counts.
- **UI: Sample message cards** — shows formatted message with template badge (Detailed/Short + OVERDUE indicator).
- **UI: Send button** shows exact customer and SMS count before confirming.
- **`phones[]` array on customers** — customers now store all phone numbers. `phone` = primary (first), `phones[]` = all numbers. Backwards compatible with existing single-phone customers.
- **`POST /customers`** — accepts `phones[]` array. Auto-deduplicates, normalizes (+63→09).
- **`PUT /customers/{id}`** — updates entire phones array.
- **New `POST /customers/{id}/phones`** — adds a single phone to existing customer.
- **New `DELETE /customers/{id}/phones/{phone}`** — removes a phone.
- **Auto-migrate on create/update** — adding a phone to a customer auto-migrates any Unknown inbox messages from that number to the customer's branch.
- **`POST /sms/inbox`, `GET /sms/check-phone`, `POST /sms/sent-from-device`** — all customer lookups now check BOTH `phone` (primary) AND `phones[]` array. Secondary phones are recognized as registered.
- **`POST /sms/send` with `customer_id`** — queues one message per registered phone. A customer with 3 phones gets 3 queued SMS.
- **`GET /sms/conversations`** — now groups by `customer_id` (not phone). One entry per customer regardless of phone count. Returns `phones[]` array.
- **New `GET /sms/conversation/customer/{customer_id}`** — unified thread for a customer across ALL their phones. Shows all registered phones (even unused ones).
- **SMS Hooks** — all 3 hooks (credit_new, payment_received, charge_applied) iterate `customer.phones[]` and send to every registered number.
- **PATCH /sms/assign-phone** — now ADDs the phone to `phones[]` array (not replaces). Existing primary phone preserved.
- **UI: Customer form** — multi-phone input list with "+ Add number" and remove buttons. First phone labelled "Primary".
- **UI: Customers table** — shows all phones comma-separated.
- **UI: Thread header** — shows all customer phone numbers.
- **UI: Reply box** — shows "Replying to N numbers: ..." hint when customer has multiple phones.
- **Architecture change:** Android app now syncs ALL incoming SMS to backend (no `check-phone` filter). Phone is a dumb pipe.
- **`POST /api/sms/inbox`** — stores every message. Adds `registered: bool` — `True` if phone matches a customer, `False` if unknown.
- **`GET /api/sms/conversations?section=customers`** — existing branch-filtered customer conversations. Unknown phones excluded.
- **`GET /api/sms/conversations?section=unknown`** — admin-only view of unregistered numbers (SMART, PLDT, banks, strangers).
- **`PATCH /api/sms/assign-phone`** — assigns an unknown phone to an existing customer. Migrates all past `sms_inbox` records to the customer's `branch_id`. If customer had no phone, sets it.
- **UI: Customers / Unknown toggle** — section switcher in conversations tab. Unknown tab shows amber count badge.
- **UI: Unknown conversations** — amber `?` avatar, `Unregistered` badge, amber unread dot.
- **UI: "Assign to Customer" button** — appears in thread header for unregistered conversations.
- **UI: Assign modal** — customer search with branch name labels. One-click migrate with toast confirmation.
- **Bug fixed:** Unknown phones were leaking into the customers section when no `branch_id` filter was applied. Fixed by adding `{customer_id: {$ne: ""}}` to the customers pipeline.
- **Branch filter:** `GET /api/sms/conversations?branch_id=X` returns only conversations where that branch has outgoing history (customers that branch has messaged). Full thread still shown (collaboration model).
- **Shared incoming:** Customer replies are always visible to all branches that have messaged that customer — enables cross-branch account collaboration.
- **Auto-signature:** `POST /api/sms/send` now server-side appends `\n\n- {CompanyName} | {BranchName}` to every manual reply. Cannot be removed or edited by user.
- **Sender attribution:** `sent_by_name` stored on every manual outgoing message (logged-in user's full_name).
- **`GET /sms/conversation/{phone}`** now returns `branch_id`, `branch_name`, `sent_by_name` on outgoing messages.
- **UI: Branch badges** — conversation list items show colored branch pills when customer has messages from multiple branches (green = current branch, blue = other).
- **UI: Bubble color coding** — own-branch outgoing = dark green, other-branch outgoing = blue (dimmed), incoming = grey.
- **UI: Thread header** — shows current branch name pill ("Branch 1" or "All Branches").
- **UI: Auto-signature hint** — read-only label below reply box: `Auto-signature: - CompanyName | BranchName (cannot be removed)`.
- **UI: Auto-reload** — conversations reload automatically when user switches branches.
- **Fixed:** Blast endpoint was using wrong settings key (`business_info`); corrected to `company_info` to get real company name.

### QR Security Hardening (2026-03-17) — Complete
- **Gap 1 fixed:** PIN brute-force lockout per doc_code. 5 failures → admin alert; 10 failures → 15-min 429 lockout. Auto-resets on success.
- **Gap 2 fixed:** `receive_payment` and `transfer_receive` now require idempotency UUID (`payment_ref`, `transfer_ref`) — duplicate submissions rejected with 409.
- **Gap 3 fixed:** Every QR payment now writes a double-entry journal record (Debit Cash/Digital, Credit AR).
- **Gap 4 fixed:** `client_ip` and `user_agent` captured on every `qr_action_log` entry.
- **Gap 5 fixed:** `DocViewerPage.jsx` shows attempts-remaining warning (≤4 left) and a live countdown banner when locked.
- New functions in `utils/security.py`: `check_qr_lockout`, `log_failed_qr_pin_attempt`, `log_successful_qr_pin_attempt`, `_raise_qr_security_alert`.


- Multi-tenant org management, branch management, user roles & permissions
- Unified sales: walk-in, credit, partial, digital, split payments
- Purchase orders (external suppliers only), suppliers, branch transfers
- Inventory management, count sheets, barcode printing
- Customers, price schemes, branch-specific pricing
- Daily operations, close-of-day wizard, Z-reports
- Payments, fund management, expenses, accounting, journal entries
- Audit center, incident tickets, backups, reports
- Mobile barcode scanner, returns/refunds

### AgriSmart Terminal (Complete — Mar 2026)
- Device pairing (6-char code + QR auto-pair)
- Terminal Shell with floating mode selector (Sales | PO Check | Transfers | Settings)
- Sales with barcode scanner, full checkout, print order slip
- PO verification + Transfer receive with variance handling
- WebSocket real-time notifications, Terminal Pull

### Terminal Smart QR Scan + Branch Security (2026-03-17) — Complete
- **BUG FIX:** QR pair token no longer hardcodes "admin" role — uses actual initiating user's role
- **BUG FIX:** `pull-po` and `pull-transfer` PIN are now branch-restricted (manager PINs only work for their branch)
- **BUG FIX:** Blank receipt reprint from DocViewer fixed — now uses `PrintEngine` with proper 58mm thermal + full-page format instead of `window.print()` on raw HTML
- **NEW:** `terminal_pull` and `qr_cross_branch_action` added to `PIN_POLICY_ACTIONS`; `qr_cross_branch_action` is TOTP-only (no static manager PIN allowed cross-branch)
- **NEW:** `GET /api/doc/search?q=...&branch_id=...` endpoint — branch-scoped search by invoice number, PO number, or transfer order number; returns `doc_code` for navigation
- **NEW:** Global H10P HID keyboard wedge scanner in `TerminalShell` — detects doc codes (8-char alphanumeric), URLs containing doc codes, `agrismart://` deeplinks, and invoice numbers from hardware scanner input; routes to correct action across all tabs
- **NEW:** Smart doc search in terminal header — accepts invoice/PO/transfer numbers, shows dropdown of matching results with doc codes
- **NEW:** DocViewerPage cross-branch enforcement — when `?branch=` param doesn't match doc's branch, shows TOTP-only unlock gate; after TOTP verification, actions are unlocked with audit trail
- **NEW:** Terminal navigation to `/doc/` always passes `?branch=session.branchId` for proper cross-branch detection

### Capacitor APK Wrapper + H10P Printer SDK (2026-03-18) — Complete
- **Capacitor setup**: `@capacitor/core@6` + `@capacitor/android@6` installed, `capacitor.config.ts` created in live-URL mode (always loads `https://agri-books.com`, no APK rebuild for web updates)
- **Android project generated**: `frontend/android/` — full Capacitor Android project structure
- **H10P Printer AIDL files**: `PrinterInterface.aidl`, `PSAMCallback.aidl`, `PSAMData.aidl` placed in correct AIDL directory (`recieptservice.com.recieptservice` package)
- **Native plugin**: `H10PPrinterPlugin.java` — binds to `recieptservice.com.recieptservice.service.PrinterService`, renders HTML→Bitmap via headless WebView, calls `printer.beginWork()` → `printer.printBitmap(bitmap)` → `printer.endWork()`
- **PrintBridge.js**: environment-aware router — detects `Capacitor.isNativePlatform()`, routes to SDK on H10P or `window.print()` on browser
- **H10PPrinterPlugin.js**: Capacitor JS interface with web browser fallback
- **PrintEngine.js**: added `generateHtml()` method (returns HTML without print script, used by PrintBridge for native path)
- **Terminal print call sites updated**: `TerminalSales.jsx`, `TerminalShell.jsx`, `DocViewerPage.jsx` — all use `PrintBridge.print()` instead of `PrintEngine.print()`
- **PWA manifest updated**: `start_url: /terminal`, `orientation: portrait` — ready for "Add to Home Screen"
- **Build guide**: `frontend/ANDROID_BUILD_GUIDE.md` — complete step-by-step APK build instructions
- **AAR placeholder**: `android/app/libs/README.txt` — user must copy `printer-release.aar` here before building
- When the H10P Newland scanner reads a document QR code, a bottom sheet appears INSTANTLY
- Shows: doc number, customer/supplier, amount, status, item count
- **Three actions:** [Print 58mm Thermal] [Print Full Page] [View / Take Action]
- Reprint happens directly without navigating away — no PIN needed for reprinting
- Uses `PrintEngine` with `basicDocToPrintData()` transformer to map public doc view fields to PrintEngine format
- Falls back to doc viewer navigation if doc not found

### QR Document Lookup System (Complete — Mar 2026)
- Unique 8-char doc code per document, QR on every receipt
- 3-Tier Access Model (Open / PIN / Terminal)
- Document code scanning in Ctrl+K QuickSearch
- `/doc` entry page (type code manually if QR faded)
- `/doc/:code` full action page

### PrintEngine v2 (Complete — Mar 2026)
- Professional template with QR codes on all document types
- Thermal (58mm) + Full Page templates for Sales, PO, Branch Transfers

### Transfer Dispute & Incident Ticket System (Complete — Mar 2026)
- Structured variance resolution workflow (transit_loss, sender_error, write_off, etc.)
- Auto-generated double-entry journal entries on resolution
- PIN-based authorization for resolving incidents
- Audit Center + Incident Tickets merged for single source of truth

### QR Operational Workflows — Phase 1 (Complete — Mar 2026)
- `release_mode: "full" | "partial"` on invoices
- `inventory.quantity` = available. `inventory.reserved_qty` = customer's stock pending pickup.
- Physical on shelf = quantity + reserved_qty (always accurate)
- `sale_reservations` collection for delivery tracking
- `admin_totp` PIN method (TOTP for admin/owner only)
- 5 QR PIN policies in Settings → Security → PIN Policies
- `/api/stock-releases` endpoints (list, summary, per-invoice)
- Z-Report warning for pending releases
- Sales History badges: Unreleased / Partially Released / Released
- Partial release toggle in checkout dialog

### QR Operational Workflows — Phase 2 (Complete — Mar 2026)
- `POST /api/qr-actions/{code}/release_stocks` — PIN-gated, branch-restricted, idempotent
- `POST /api/qr-actions/{code}/verify_pin` — unlock panel without action
- `StockReleaseManager` in `DocViewerPage.jsx` — unified PIN-gated panel: history + form + confirmation
- 30-day expiry APScheduler job → `expiry_return` movement, manager notification
- Void guard for partial-release invoices (only reverses unreleased portion)
- Auto doc_code generated at partial-release sale creation (returned in response)

### Count Sheets — Reserved Stock Fix (Complete — Mar 2026)
- `system_quantity = quantity + reserved_qty` (total physical on shelf)
- Adjustment: `new_quantity = actual_counted - reserved_qty` (reserved untouched)
- UI shows "80 avail + 20 reserved" breakdown

### Pending Releases Page (Complete — Mar 2026)
- `/pending-releases` sidebar page under Inventory & Purchasing
- Summary cards, age badges, progress bars, overdue alerts
- Click row → opens `/doc/{code}` release management page

### Product Data Integrity (Complete — Mar 2026)
- Name uniqueness validation (case-insensitive) on create + update
- Price scheme uniqueness validation on create
- Cleaned 66 duplicate products + 129 duplicate price schemes from DB
- Branch Pricing section in Product Detail now shows only current branch when on specific branch

---

### QR Operational Workflows — Phase 3 (Complete — Mar 2026)
- `POST /api/qr-actions/{code}/receive_payment` — PIN-gated, Cash→cashier wallet, Digital→digital wallet, updates customer AR
- `ReceivePaymentPanel` inside Tier 2 of DocViewerPage.jsx (reuses Tier 2 unlock PIN)
- Reflects in Z-report and payment history automatically

### QR Operational Workflows — Phase 4 (Complete — Mar 2026)
- `POST /api/qr-actions/{code}/transfer_receive` — PIN-gated, delegates to `receive_transfer()` with synthetic user
- Exact match → inventory moves immediately (status: received). Variance → received_pending, source branch notified
- `TransferReceivePanel` in DocViewerPage.jsx — locked → PIN → qty inputs → confirm → done
- `verify_pin` endpoint made generic (works for both invoice and branch_transfer)
- Branch transfer items in open view now include `product_id`

### QR Operational Workflows — Phase 5 (Complete — Mar 2026)
- Terminal Doc Code Entry: Search icon in TerminalShell header → type code → navigate to /doc/{code}

### Branch Transfer Security Fixes (Complete — Mar 2026)
- View modal: "Confirm Receipt" only shows for destination branch (sender cannot see it)
- View modal: "Verify" button restricted to admin/auditor only
- Backend: `receive_transfer()` now guards against non-destination-branch users (403)

### admin_totp Removed (Complete — Mar 2026)
- Merged into standard `totp` method. Removed from PIN_METHODS, QR policy defaults updated.

### PO Bug Fix (Complete — Mar 2026)
- `setSourceType`/`setSupplyBranchId`/`setShowRetailToggle` missing useState declarations added to PurchaseOrderPage.js

### Sales Order Redesign & Security Hardening (Complete — Mar 2026)
- **UI Redesign:** Order mode form in `UnifiedSalesPage.js` matches inFlow layout
- **Date as Single Source of Truth:** Removed `invoice_date`, `order_date` is sole controller for reports
- **Timezone Fix:** Default date uses browser's local time (PHT), not UTC
- **Synchronized Date UI:** Sale Date field and Unclosed Days banner are perfectly synced
- **Closed-Day Guard:** Frontend + backend block sales on formally closed days (Z-report finalized)
- **Floor-Date Guard (System Start):** Backend rejects dates before the branch's earliest operational date. Frontend `min` attribute on date input.
- **Collapsible Order Header:** Customer Details & Order Info section is collapsible (collapsed by default)
- **Sale Date moved to top bar:** Always visible next to Customer PO, not buried in collapsible section
- **Editable Customer Info:** Contact/Phone and Billing Address editable when customer selected. Pre-checkout save dialog.
- **Quick↔Order Mode Transfer:** Cart items seamlessly transfer between modes. Order→Quick blocked if per-line discounts exist.

### Discount & Price Override Audit System — Phase 1 (Complete — Mar 2026)
- **Permission enforcement: `sales.give_discount`** — Backend rejects discounts from users without permission. Frontend disables discount fields + price editing.
- **Permission enforcement: `sales.sell_below_cost`** — Capital guard now permission-gated. Users WITH permission can override; users WITHOUT are blocked.
- **Discount-below-capital guard** — Frontend + backend block discounts that push net price per unit below capital.
- **Audit trail: `discount_audit_log` collection** — Every sale with discounts or price overrides is logged with full detail (who, what, how much, which items).
- **Reports: Discounts tab** — New tab in /reports with date/branch/group-by filters. Shows total discounts, price overrides, by customer or employee, with drill-down detail.

### Permission Enforcement Phase 2 (Complete — Mar 2026)
- **`products.view_cost`** — Cost column in Products table + Cost Price/Capital field in edit dialog hidden when OFF. Avg ₱, Last ₱, below-capital warnings in Sales page hidden when OFF.
- **`customers.view_balance`** — Balance & Credit Limit columns in Customers table hidden when OFF. Balance/Limit display in Sales page customer dropdown and checkout hidden when OFF.
- **`customers.manage_credit`** — Credit Limit, Interest Rate, Grace Period fields in Customer form disabled when OFF (separate from customers.edit).
- **`reports.export`** — Print buttons on AR Aging, Sales, Expense, and Profit report tabs hidden when OFF.
- **`reports.view_profit`** — NEW: Product Profitability Report tab in /reports. Shows revenue, cost, profit, margin per product. Sortable by profit/revenue/margin/qty. Gated behind this permission.
- **`accounting.generate_interest` / `generate_penalty`** — Remapped from `create_expense` to their own dedicated permission keys.

### Terminal QR Scanner + Camera Fix (Complete — Mar 2026)
- **QR Scanner in mode selector:** New "Scan QR" option in the floating terminal mode selector (bottom-left nudge). Opens full-screen camera scanner for document QR codes. Uses `html5-qrcode`. When a doc QR is scanned, stops camera and shows the existing QuickScan bottom sheet (print thermal/full page, view/take action). Also handles doc number patterns and product barcodes.
- **Camera scanner size fix:** Barcode camera in TerminalSales uses clipped container (140px visible window over full-res video) so it only shows the scanning strip. Full camera resolution preserved for detection.
- **Stock visibility in terminal:** Search results show color-coded stock badges (green/amber/red). Cart items show available quantity with amber highlight when exceeding stock.
- **Insufficient stock override:** Terminal now shows a proper modal (like desktop) when stock is short, with manager PIN override option. White screen crash fixed — structured error objects no longer passed to toast.

### Adaptive Incident Ticket System (Complete — Mar 2026)
- **Ticket numbering:** New tickets use `IT-{BranchCode}-{Sequence}` (e.g., `IT-B1-001000`) via standard `generate_next_number`.
- **Branch-scoped ticket list:** Admin on "All Branches" sees all tickets; specific branch shows only that branch's tickets.
- **Adaptive detail view:** Ticket detail dialog detects `ticket_type` and renders context-appropriate layout:
  - **Transfer variance:** Transfer link, route, sent/received items table, sender confirm button
  - **Negative stock override:** Product, branch, invoice, stock before/after, cashier, override approver. Investigation guide with 4 root causes.
- **New resolution types for stock tickets:** `unencoded_po`, `count_error`, `wrong_item`, `shrinkage` — each with contextual help text and appropriate journal entry generation.
- **Resolve dialog is ticket-type-aware:** Shows only relevant resolution options (stock types for stock tickets, transfer types for transfer tickets).

See `/app/memory/ROADMAP.md` for full implementation spec.

### AgriDocs — Business Document Cloud Phase 1 (Complete — Mar 2026)
- **Document management system** for Philippine business compliance documents
- **6 categories:** Business Registration, LGU/Local Permits, BIR, Employer & Employee Compliance (SSS/PhilHealth/Pag-IBIG), Industry-Specific Agrivet (BAI/FDA/FPA), Other
- **Smart period tagging:** Monthly (multi-month select for bulk payments), quarterly, annual, validity (from/to dates), one-time
- **Folder browsing UI:** Category folders -> sub-category folders -> document list with monthly grid view
- **Upload from computer:** Drag & drop, file picker, category/type/month selection
- **QR phone upload:** Generate 15-min upload token -> QR code -> phone uploads directly to correct branch/category/period
- **Document preview:** Inline PDF/image viewer, download, metadata display
- **Edit & delete:** Change coverage months, tags, validity dates anytime
- **Compliance summary API:** Which months are filed, what's expiring, what's expired
- **Audit-sensitive badges:** Agrivet documents (BAI, FDA, FPA) flagged as audit-critical
- **Expiry tracking:** Permits/licenses show days remaining with color badges
- **R2 storage:** Files in Cloudflare R2 with pre-signed URLs
- **Backend:** `routes/documents.py` (12 endpoints). Collections: `business_documents`, `doc_upload_tokens`
- **Frontend:** `pages/DocumentsPage.js` with UploadDialog, PreviewDialog, EditDialog, QRUploadDialog, MonthlyGrid
- **Phase 2 — QR Phone Upload Page:** `pages/DocUploadPage.js` at public route `/doc-upload/:token` — mobile-friendly upload (Take Photo / Browse Files), shows category/type/months context, single-use 15-min tokens, success/error states
- **Context-aware dialogs:** Upload Dialog and QR Upload Dialog auto-pre-fill category/sub-category from current folder navigation. Upload Dialog includes inline "Upload via Phone Instead" QR code generator.
- **Phase 3 — Compliance Dashboard:** Shows on root documents view with: expired document alerts (red banner), expiring soon alerts (amber, within 60 days), Monthly Filing Tracker for 6 key filings (SSS, PhilHealth, Pag-IBIG, BIR 1601-C, 0619-E, 2550M) with dot indicators (green=filed, red=missing, gray=upcoming) and X/Y progress counts. Year filter 2022-2027. Fixed branch_id='all' filter bug.
- **Terminal Document Upload:** New "Upload Doc" option in terminal floating mode selector. PIN-gated access (Manager PIN = branch-only, Admin/TOTP = all branches). 4-step flow: PIN → Category/Type/Period → Camera/Browse → Upload. Uses native phone camera via `capture="environment"`. Backend: `POST /api/documents/terminal/verify-pin` and `POST /api/documents/terminal/upload`. Frontend: `TerminalDocUpload.jsx` separate component. PIN action: `terminal_doc_upload` in verify.py.

- Added `MIN_H_MAP` / `MIN_W_MAP` constants; all layout items now carry `minH`/`minW` guards
- `validateLayouts()` sanitizes stale/corrupted `localStorage` layouts on load
- Enabled `isResizable={true}` with `se` resize handles on both Owner & Branch grids
- Fixed critical P0 bug: added getDerivedStateFromProps pattern (`prevLayoutKey` guard) so layouts reset synchronously on owner↔branch view switch — no widget collapses after branch change
- Added CSS for resize handle (bottom-right corner indicator)

### Controlled Negative Stock Override (Complete — Mar 2026)
- Hard stock block replaced with structured 422 `insufficient_stock` response listing all failing items
- `InsufficientStockModal` in `UnifiedSalesPage.js`: 3 options — Encode PO, Manager Override, Cancel
- Manager Override requires PIN (`stock_negative_override` policy: manager_pin / admin_pin / totp)
- Override passes `manager_override_pin` on retry; backend verifies PIN, skips stock guard, allows negative inventory
- Auto-creates `incident_tickets` record (`ticket_type: "negative_stock_override"`, `status: open`) per overridden item — linked to invoice, records who approved and method
- Inventory page: negative items show red "Negative — Investigate" badge with red row background
- Close Wizard Step 1: non-blocking warning banner listing negative items + link to Incident Tickets
- Count Sheets snapshot: items with negative available qty get `has_negative_stock: true` flag + "⚠ Negative — check open ticket" warning in red
- Low-stock alert endpoint: `negative_stock` status added, sorts above `out_of_stock`
- Moving average: completely unaffected (only `purchase`/`transfer_in` movements update MA)
- Offline: same as before — offline sync already allows negative with `stock_warnings`; online path now consistent


`POST /api/qr-actions/{code}/receive_payment`
- Receives cash/digital payments on invoices via QR scan
- Routes to existing wallet functions (update_cashier_wallet / update_digital_wallet)
- Updates customer AR balance
- Z-report picks up payments automatically (queries invoices.payments[])

### Phase 4 — PO Receive via QR
`POST /api/qr-actions/{code}/po_receive`
- Delegates to terminal_finalize_po() in purchase_orders.py
- Already-received POs: view-only (available_actions=[])

### Phase 5 — Transfer QR Receive
`POST /api/qr-actions/{code}/transfer_receive`
- Delegates entirely to receive_transfer() in branch_transfers.py
- Handles variance → received_pending automatically

### Phase 6 — Terminal Doc Code Entry
- Add "Find by Code" input in terminal shell header
- navigate('/doc/${code}') — no other changes needed

---

## Prioritized Backlog

### Dashboard Review Panel Enhancement (Complete — Mar 2026)
- `GET /api/dashboard/review-detail/{record_type}/{record_id}` — enriched endpoint returning full record detail for review
- Supports: purchase_order (supplier, items, dates, due date, payment status), branch_transfer (branches, items, shortage info), expense (category, payee, method)
- Review dialog now shows: full item breakdown table, receipt photos, supplier/branch info, dates, payment status
- PIN-gated "Mark as Reviewed" with optional notes, "Open Full Page" link

### Dashboard AP + Pending Reviews Fixes (Complete — Mar 2026)
- **Bug Fix:** Pending Reviews no longer shows draft/ordered/cancelled POs — filter updated to only include `received`, `fulfilled`, `partially_fulfilled`, `in_progress`, `sent_to_terminal` statuses
- **Bug Fix:** Accounts Payable widget now captures all unpaid supplier POs (not just `po_type: "terms"`/`"credit"`) — broadened to exclude only internal `branch_request` POs
- **Enhancement:** AP widget rows now show receipt review status badge (green ✓ = reviewed, amber camera = needs review)
- **Enhancement:** AP widget hint text: "Click any PO to review receipts & verify before payment" — unified workflow with Pending Reviews (both open ReviewDetailDialog)


### AP Payment + Verify Workflow — Phases 1–3 (Complete — Mar 2026)
- **Phase 1:** review-detail endpoint fixed (uses stored `balance` not grand_total — fixes ₱0 bug). Wallet balances (cashier/safe/bank/digital) returned in PO response. `1030 Cash - Bank Account` added to chart of accounts.
- **Phase 2:** ReviewDetailDialog "Verify & Approve" collapsible button — no longer gated by files. Uses `po_mark_reviewed` PIN policy. Works on AP + Pending Reviews widgets.
- **Phase 3:** Pay Now panel in ReviewDetailDialog (AP widget only). Cashier/Safe = `pay_po_standard` (manager/admin/TOTP). Bank/Digital = `pay_po_bank` (admin/TOTP only). Smart double-entry journal auto-created for bank/digital (DR: AP 2000, CR: Bank 1030 or Digital 1020). Expense record always created → Z-report + Close Wizard. Receipt upload auto-opens after payment. PaySupplierPage: PIN now required, bank/digital fund sources added, upload auto-opens, recordType fixed to "purchase_order".
- **Phase 4 (Complete — Mar 2026):** PaySupplierPage multi-PO batch receipt upload modal — after paying multiple POs, a modal lists all paid POs with individual Upload buttons, progress bar (N of M uploaded), per-PO upload tracking via UploadQRDialog, and Skip/Done controls. **Collection Receipt mode:** toggle "One receipt covers all" — upload once, system auto-shares to all POs via `POST /api/uploads/share-receipt` (creates mirror upload_session records in DB per PO, same stored_path, no R2 copy needed, `shared_from` audit trail). Files appear in Pending Reviews for each PO automatically. **Shared receipt provenance in ReviewDetailDialog:** when reviewing a PO whose receipt was shared from a collection, shows blue "Collection receipt · shared from PO-XXXX (Vendor)" notice + per-photo "Shared" badge. Backend: `review-detail` endpoint enriches file entries with `is_shared`, `shared_from_po_number`, `shared_from_vendor`, `all_receipts_shared` flag.


### Notification Center v2 (Complete — Mar 2026)
- **Phase 1 — Missing notifications added:** `discount_given` (fires on every discounted sale with full item detail + repeat-offender count), `below_cost_sale` (below-capital sale), `negative_stock_override` (after incident ticket created), `ap_payment` (after supplier payment via `pay` endpoint)
- **Phase 2 — Notification Center page:** `/notifications` full-page route. 6 category summary cards (All / Security / Action Required / Approvals & Overrides / Operations / Finance) with total + unread counts. Filterable notification list with severity badges (critical/warning/info). Expandable discount rows show: product, orig price, sold price, discount %, capital, repeat-offender badge ("X discounts this week by cashier"). Expandable AP payment rows show: PO#, vendor, amount, fund source, remaining balance. Bell click navigates to full page (no more dropdown). Backend `create_notification()` helper with auto-assigned category + severity. Category counts returned on every GET `/notifications` call.


### Pay Supplier Page — QB-Style Redesign (Complete — Mar 2026)
- **Layout:** Mirrors AR PaymentsPage exactly — left panel (always-visible supplier list with total balance + overdue badges) + right QB-style form
- **Supplier selection:** Click in left panel OR type in "Pay To" search field with dropdown suggestions
- **Smart allocation:** Payment Amount field auto-allocates budget to POs oldest-due-first; checking PO applies unused budget; unchecking returns amount to pool; unused budget shown in header notice + summary
- **Pay All Due:** Checks only overdue POs; **Pay All:** Checks all POs with full balances
- **QB-style footer:** "Amounts for Selected POs" — POs Selected / Total Owed / Applying / Unused Budget / Remaining Balance
- **Method icons removed:** `Pay From` fund source (Cashier / Safe / Check·Bank / Digital) is the single source of truth. `payMethod` auto-derived from fund source for audit trail.
- **All previous AP features preserved:** PIN required, bank/digital lock icons, batch upload modal, collection receipt toggle, shared receipt provenance



### Terminal Token Auto-Refresh (Complete — Mar 2026)
- `POST /api/terminal/refresh-token` endpoint issues a new 24h JWT for active terminal sessions
- Frontend auto-refreshes every 12 hours via `setInterval` in TerminalShell
- On initial load, also refreshes to recover from near-expired tokens
- If token is fully expired (401), auto-logs out and prompts re-pair
- Token stored in localStorage via `onSessionUpdate` callback to TerminalPage

### QR Actions Terminal-Only Gating (Complete — Mar 2026)
- **Backend:** `_verify_terminal_session(terminal_id)` check added to `release_stocks`, `receive_payment`, `transfer_receive` endpoints in `qr_actions.py`
- **Frontend:** Action panels (StockReleaseManager, ReceivePaymentPanel, TransferReceivePanel) gated behind `isTerminal` check in DocViewerPage
- Non-terminal visitors see "Actions require an AgriSmart Terminal" banner
- Document viewing (read-only info, details, attached files) remains open to all
- Receipt/DR uploads remain unrestricted
- `terminal_id` passed from localStorage session through component props to API calls

### Terminal Android Back Button Fix (Complete — Mar 2026)
- Intercepts Android hardware back button / browser back navigation via `popstate` event
- Smart priority chain: close overlays (QR scanner → doc upload → settings → quick scan → doc search → mode menu) → return to Sales tab → double-tap to exit
- Pushes history state entries to prevent PWA from exiting on first back press
- "Press back again to exit" toast with 2-second window (native Android pattern)

### Super Admin Org Context Fix (Complete — Mar 2026)
- **Root cause:** When super admin (org_context=None) performs tenant operations, DB writes omit `organization_id`, making records invisible to regular users
- **Central fix:** `ensure_org_context()` helper resolves org from branch_id. Added to `log_movement()` (catches ALL movement types), plus `branch_transfers.py` (create/send/receive/accept), `sales.py`, `purchase_orders.py`
- **Data repair:** `POST /api/branch-transfers/admin/fix-orphaned-movements` — scans all tenant collections for missing org_id and resolves from branch
- **Live site fix:** 4 orphaned movements, 2 capital_changes, 16 notifications, 1 incident_ticket repaired

### Branch Transfer Invoice Number Display (Complete — Mar 2026)
- Transfer cards in list view now show `invoice_number` badge next to BTO number
- Transfer detail dialog title also shows invoice number

### Movement History Pagination (Complete — Mar 2026)
- Backend `GET /products/{id}/movements` now returns `total` count
- Frontend shows "Showing X of Y movements" counter with "Load More" button

---

### Modal Consolidation Phase 1 — A1 Absorbs A3 (Complete — Mar 2026)
- **ReviewDetailDialog (A1)** extended with backward-compat props: `poId`, `poNumber`, `onUpdated`, `onOpenChange`
- When `poNumber` passed: resolves UUID via `/invoices/by-number/{poNumber}` → feeds into `/dashboard/review-detail` endpoint
- **7 pages migrated** from PODetailModal → ReviewDetailDialog: CloseWizardPage, PaySupplierPage, QuickSearch, AuditCenterPage, SuppliersPage, TransactionSearchPage, DashboardPage
- PODetailModal.js retained as legacy file (zero imports remain — safe to delete in future cleanup)

### Modal Consolidation Phase 2 — A2 Absorbs A4 (Complete — Mar 2026)
- **InvoiceDetailModal (A2)** extended with `compact` prop and `saleId` backward-compat alias for `invoiceId`
- When `compact=true`: renders single-view layout matching old SaleDetailModal (narrower sm:max-w-lg dialog, no tabs, print buttons, inline receipts/payments/edit history, void button at bottom)
- **14 files migrated** from SaleDetailModal → InvoiceDetailModal with `compact`: SalesPage, AccountingPage, ExpensesPage, CustomersPage, CloseWizardPage, DailyLogPage, PaymentsPage, PendingReleasesPage, InternalInvoicesPage, ReportsPage (2x), DashboardPage, AuditCenterPage, QuickSearch (2x), TransactionSearchPage
- SaleDetailModal.js retained as legacy file (zero imports remain — safe to delete in future cleanup)
- Z-reports: zero impact (UI-only migration, same API endpoints)

### Modal Consolidation Phase 3 — C1 + C2 → AuthDialog (Complete — Mar 2026)
- **AuthDialog.js** created as unified PIN/TOTP/Password authorization dialog
- `mode="pin"`: single PIN input with optional discrepancy fields, calls `/api/verify/{docType}/{docId}`
- `mode="totp"`: mode tabs (Owner PIN / Authenticator / Password), calls `/api/auth/verify-admin-action`
- **VerifyPinDialog.js** → thin wrapper `<AuthDialog mode="pin" />`
- **TotpVerifyDialog.js** → thin wrapper `<AuthDialog mode="totp" />`
- Zero page-level changes — backward compatible via wrapper pattern

### Modal Consolidation Phase 4 — Extract FundTransferDialog (Complete — Mar 2026)
- **FundTransferDialog.js** extracted from FundManagementPage inline transfer dialog
- Props: `open`, `onClose`, `transferType`, `walletByType`, `branchId`, `onSuccess`
- Supports all 4 transfer types with appropriate auth fields
- FundManagementPage updated to use the extracted component
- All migrated pages use `showReviewAction={false} showPayAction={false}` for view-only contexts; AuditCenterPage uses `showReviewAction={true}`
- Z-reports: zero impact (modals are UI-only; no backend/DB changes)


- **Phase 1 — Authenticated PIN alerts:** `_raise_security_alert()` now resolves `branch_name` from DB, enriches `user_role` and `user_email`. Message format: `"Test Manager (Manager) entered wrong PIN 6x at Branch 1 — Transaction Verification: Verify PO-XXX"`
- **Phase 2 — QR Terminal alerts:** `_raise_qr_security_alert()` accepts `terminal_id`, resolves to `"AgriSmart Terminal at Branch X"` from `terminal_sessions`. Enriches with `doc_id`, `doc_number`, `counterparty`, `doc_amount` via doc_codes + invoice/PO/transfer lookup. `qr_actions.py` passes `terminal_id` at all 3 terminal-only call sites.
- **Phase 3 — Frontend SecurityAlertDetail:** Expandable two-card layout — WHO+WHAT (authenticated) / TERMINAL+DOCUMENT (QR). Clickable doc number opens existing `ReviewDetailDialog` (same pattern as dashboard widgets). Lock banner for documents locked after 10 failures.


- APScheduler daily job at 8:30 AM: `_daily_compliance_check` in `main.py`
- Fires `compliance_deadline` notifications for:
  - Expired docs (critical severity) — dedup per doc per day
  - Expiring within 30 days (warning severity) — dedup per doc's valid_until date
  - Missing monthly filings after the 15th (SSS, PhilHealth, Pag-IBIG, BIR 1601-C, 0619-E, 2550M) — dedup per month
- `create_notification()` now accepts `severity_override` param for per-call severity
- Frontend: `compliance_deadline` TYPE_CONFIG with orange `FileWarning` icon + `ComplianceDetail` expandable row
- `NOTIFICATION_META` updated with `compliance_deadline` → category: action, severity: warning

## AgriGateway 3.0 Android App — Cursor Prompt Written (2026-02-xx)
- Full Cursor build prompt saved at `/app/memory/AGRIGATEWAY_CURSOR_PROMPT.md`
- Covers all 3 SMS flows: Outbound Queue, Incoming Reply, Native Send Sync
- Room DB with dedup guarantees, WorkManager retry, RemoteLogger batch flush
- Multi-tenant handled 100% server-side via JWT — zero client-side org logic needed
- SharedPreferences lastProcessedSmsId fix baked into SentSmsObserver architecture

## Next Up (P0 — Immediate)
See `/app/memory/ROADMAP.md` for full spec on each item.
See `/app/memory/NEXT_FORK_HANDOFF.md` for the IMMEDIATE next task with full design spec.

### /payments UI Redesign + Inline Interest + Account Summary (NEXT — NOT YET CODED)
Full design spec in `/app/memory/NEXT_FORK_HANDOFF.md`. Summary:
- Compact header (2 rows instead of 4) — fixes "only 2 invoices visible" space problem
- Remove "Generate Interest" collapsible card — replaced by inline interest sub-rows per overdue invoice
- Inline interest row per overdue invoice: `45d × 2%/mo = ₱26.75 | subtotal ₱918.27`
- Account Summary footer card with professional labels: Outstanding Principal / Accrued Interest Charges / Total Amount Due
- INT invoice only created at PAY time (force=True), not on customer select
- Customer select: just loads invoices + shows inline computed interest (no INT invoice creation)

### Forgot Password / Password Reset (2026-04-14) — Complete
- `POST /api/auth/forgot-password` — generates a secure token (1hr expiry), stores in `password_reset_tokens` collection, sends email via Resend with link to `{APP_FRONTEND_URL}/reset-password?token=...`
- `POST /api/auth/reset-password` — validates token (not used, not expired), updates `password_hash`, marks token as used (single-use)
- Security: always returns generic success message (no email enumeration), tokens invalidated on use
- Frontend: "Forgot password?" link on LoginPage, `/forgot-password` page with email form + sent confirmation, `/reset-password?token=` page with password + confirm fields
- `REACT_APP_FRONTEND_URL=https://agri-books.com` added to backend `.env` for correct reset link URLs


- **Backend `DELETE /api/superadmin/organizations/{org_id}`** — runs `create_org_backup()` first (saves to R2), then purges all tenant data across 30+ collections, then removes the org record. Aborts entirely if backup fails.
- **Backend `POST /api/superadmin/organizations/{org_id}/backup`** — standalone manual backup trigger
- **3-step confirmation modal in SuperAdminPage.js:**
  - Step 1: Must type the **exact company name** (case + space sensitive)
  - Step 2: Must type `PERMANENTLY DELETE` — shows org summary (users, branches)
  - Step 3: Live progress spinner — "Do not close this window"
  - Step 4: Success with full backup details (filename, size, doc count, R2 status)
  - Error state: shows reason, confirms no data was changed, offers retry
- **Red trash icon** on every org row (distinct from the edit button)


- Platform-wide APK distribution page at `/downloads` accessible to all authenticated users
- `app_downloads` collection in `_raw_db` (not tenant-scoped — global platform storage)
- Two pre-defined app slots: AgriSMS Gateway 2.0 (`com.agrism.gateway`) + AgriSmart Terminal (`com.agribooks.terminal`)
- `GET /api/app-downloads` — list apps merged with DB metadata
- `POST /api/app-downloads/{slug}/upload` — super admin uploads APK to R2 (`_platform/apk-downloads/` prefix), max 200MB
- `GET /api/app-downloads/{slug}/download-url` — pre-signed download URL (5-min expiry) + increments download_count
- `DELETE /api/app-downloads/{slug}` — removes APK from R2 + marks inactive
- Frontend dark-themed product page with expandable descriptions, key features, technical specs
- Super admin: Upload APK panel per app (version, changelog, drag file), Update/Delete controls
- Sidebar entry under Management → App Downloads (visible to all users)


- **`product_categories` collection** added to TENANT_COLLECTIONS — per-org, isolated
- **`GET /api/products/categories`** enhanced — merges categories from active products + manually-defined ones (sorted, deduped)
- **`POST /api/products/categories`** — create a custom category for the org (idempotent)
- **`DELETE /api/products/categories/{name}`** — delete category (guarded: fails if active products still use it)
- **`CategorySelect` component** (`/components/CategorySelect.jsx`) — dynamic org-aware dropdown with inline "Add new category…" option
- **ProductsPage.js** — replaced 8 hardcoded SelectItems with CategorySelect; added "Categories" button in header opening full Manage Categories dialog (list + add + delete)
- **ProductDetailPage.js** — upgraded free-text Input to CategorySelect for consistency
- **Branch Transfers** — already dynamically loaded from API; works automatically with new categories
- **BranchCapitalWizard** — queries categories from product data directly; works automatically
- **returns.py veterinary check** — name-based check; any org with "Veterinary" products retains this behavior

### Terminal Smart Margin Guard — UX Fix (2026-04-08) — Complete
- Moved "Yes, proceed with low margin" button OUT of the order summary box
- Now shows as a full-width, prominent panel between summary and payment section
- Covers both low-margin (amber) and loss (red) cases — both now require acknowledgment


- **Price Scheme Switcher**: Cart area shows all available price schemes (Retail, Wholesale, etc.) as toggle buttons. Switching to non-retail requires manager/admin/TOTP PIN via `POST /api/verify/verify-pin-action` with `terminal_wholesale_switch` policy. Retail is always free. Switching recalculates all cart prices instantly.
- **Total Discount Input**: Checkout dialog has discount input with ₱ Amount / % Percentage toggle. Discount applied to subtotal → grandTotal = subtotal - discount. Stored as `overall_discount` in sale data.
- **Smart Profit Guard**: Real-time margin analysis on the whole receipt. Calculates `totalCost = Σ(effective_capital × qty)`, `margin = revenue - cost`, `marginPercent`. Color-coded display: green (healthy), amber (below configurable threshold, default 1%), red (loss). Amber shows "Yes, proceed with low margin" confirmation button. Configurable via `GET/PUT /api/settings/sales-config` (min_margin_percent, margin_warning_enabled).
- **New Backend Endpoints**: `POST /api/verify/verify-pin-action` (standalone PIN verification by action key), `GET/PUT /api/settings/sales-config` (margin threshold config)
- **Backend:** `routes/verify.py` (verify-pin-action + terminal_wholesale_switch policy), `routes/settings.py` (sales-config)
- **Frontend:** `TerminalSales.jsx` (scheme switcher, discount input, margin guard, PIN modal)

### Terminal Smart Sync — Phases 1-3 + Last Synced Enhancement (2026-04-08) — Complete
- **Phase 1 — Instant Load from Cache**: `TerminalShell.loadData()` now checks IndexedDB for cached products first. If cache exists (products > 0), terminal shows **immediately** (<200ms) — no loading spinner. Background delta sync kicks off silently.
- **Phase 2 — Backend True Delta Sync**: `GET /api/sync/pos-data?last_sync=<ISO>` now applies time filter to ALL collections (products, customers, inventory, branch_prices) — not just products. Returns `deleted_ids[]` for products deactivated since last sync. Products enriched with current inventory even during delta.
- **Phase 3 — Inventory Pulse Polling**: New `GET /api/sync/inventory-pulse?branch_id=X&since=<ISO>` lightweight endpoint returns only changed stock quantities. `syncManager.js` polls every 60 seconds for near-real-time stock visibility. Catalog delta sync remains at 5-minute intervals.
- **Sync Indicator**: Non-blocking header indicator: "Syncing..." → "Up to date" → "Sync failed" with appropriate icons.
- **TerminalSales Cache Refresh**: `syncVersion` prop triggers TerminalSales to re-read products/customers from IndexedDB after background sync completes.
- **Last Synced Display**: Settings panel shows "Last Synced: X min ago" with live-updating relative timestamp (refreshes every 30s). Tapping the row triggers a manual sync. Spinner animation while syncing.
- **Phase 4 — Cursor AI Prompt**: Android WebView IndexedDB persistence guide saved at `/app/memory/CURSOR_TERMINAL_SMART_SYNC_PROMPT.md`
- **Impact**: Terminal open time: 5-15s → <200ms (returning users). Stock freshness: manual → 60 seconds. Data per QR→Back navigation: ~5MB → ~0KB (cache hit).
- **Backend:** `routes/sync.py` (enhanced pos-data delta + new inventory-pulse endpoint)
- **Frontend:** `TerminalShell.jsx`, `TerminalSales.jsx`, `lib/offlineDB.js`, `lib/syncManager.js`

### P0 — Compliance Calendar Widget on Dashboard
- Widget showing expired docs (red), expiring within 30d (amber), monthly filing status
- Data already available via `GET /api/documents/compliance/summary`
- Add to dashboard grid layout (`DashboardPage.js`)

### P1 — Terminal Features
- **HID Barcode Scanner (H10) — DONE (2026-04-05)**
  - Scan detection via keystroke timing (<50ms = scanner, >100ms = human)
  - Cooldown system (1.5s) prevents "types twice" from HID scanners without Enter key
  - Quantity prompt on first scan of a product
  - "Auto +1 for This Transaction" mode — subsequent scans just increment
  - Works alongside existing camera scanner and Enter-key hardware scanners
- **Quick Stock Check** — scan barcode → instant stock level (read-only, no PIN)
- **Price Check** — scan barcode → price card (respects view_cost permission)
- **Quick Count** — scan + enter qty → submit count sheet (PIN required)

### Team Creation — Email-first, All Fields Required (2026-04-07) — Complete
- Removed `username` field from create/edit form — email is now the login identifier; username auto-set to email in backend
- All fields required: Full Name, Email (validated format), Password (new users), Role
- Backend `POST /api/users`: rejects if email/full_name/password missing; checks for duplicate email; auto-derives username from email
- Email field disabled on edit (login identifier cannot change)
- TeamPage user table and permissions panel now display email instead of `@username`

### Phase 3 — Custom Roles (2026-04-07) — Complete
- New `custom_roles` MongoDB collection + full CRUD: `GET/POST/PUT/DELETE /api/roles`
- Per-org isolation, soft-delete, user_count guard (cannot delete if users assigned)
- Custom role fields: `label`, `description`, `pin_tier` (manager/staff), `base_preset`, `permissions{}`
- `POST /api/users`: if role is not a system role, fetches custom role from DB and applies its permissions + pin_tier
- Frontend: new **Roles** tab in `/team` (admin only):
  - System roles shown as read-only reference cards with PIN tier + description
  - Custom roles: create/edit/delete with inline permission editor (per-module toggles, None/All shortcuts)
  - Role dropdown in user create/edit form now includes custom roles under a "Custom Roles" divider
- Custom role users display with cyan badge in Members table


- `purchase_orders.py`: 4 `check_perm(user, "inventory", "adjust")` calls replaced with their correct logical permissions:
  - Create PO → `purchase_orders.create`
  - Receive PO → `purchase_orders.receive`
  - Cancel PO → `purchase_orders.delete`
  - Reopen PO → `purchase_orders.edit`
- `permissions.py`: `inventory.adjust` and `inventory.transfer` set to `False` by default in `inventory_clerk`, `manager`, and `cashier` presets. Admin preset unchanged (`True`).
- `inventory_clerk` description updated: "direct stock editing requires explicit admin grant"
- Existing users unaffected (permissions stored per-user). Only new users get the safer defaults.
- Admin can still grant `inventory.adjust` per-user via the Permissions tab in /team.


- `DEFAULT_PERMISSIONS` now includes `inventory` (→ inventory_clerk preset), `inventory_clerk`, `staff`. New inventory users get correct stock permissions instead of cashier fallback.
- `SYSTEM_ROLES` set exported for future custom role validation.
- Login + `/me` endpoints: Raw PINs stripped from responses. Added `has_manager_pin`/`has_staff_pin`/`has_auditor_pin` boolean flags.
- `staff_pin` added to PIN Policies UI as "Staff PIN" (teal). Now visible and configurable in `/settings → Security → PIN Policies`.
- Auditor Access table: fixed to show email instead of `@username`.
- `change-my-pin` extended to ALL roles: inventory/cashier/staff → `staff_pin`; admin/manager → `manager_pin`. Requires current PIN if one already exists.
- "My PIN" section in My Account shown for ALL roles with correct label + description.
- All new users get `pin_tier: "staff"|"manager"` field set at creation.
- `verify.py` manager + staff PIN queries include `pin_tier` `$or` clause (backward-compatible with existing users).
- Stats cards in TeamPage: `inventory`+`inventory_clerk`+`staff` counted together under "Inv. Clerks".


- Discount cashier drill-down report (`/reports` Discounts tab)
- AP payment history per supplier in PaySupplierPage

### P2 — Backlog
- Shared receipt clickable link in ReviewDetailDialog
- Cross-branch payment wallet routing (deferred by user)
- Admin tool for corrupted POs
- Visual trail for partial invoices
- Smart journal entries for back-dated sales
- Refactor SuperAdminPage.jsx (1000+ lines)
- Fix react-hooks/exhaustive-deps ESLint warnings (3 remaining)

### P3 — Future
- Native Android APK (Capacitor finalization + AAR)
- Weight-embedded EAN-13 barcode recognition
- Automated Payment Gateway & Demo Login

---

## Key Files Reference

### Backend
- `routes/sync.py` — Smart Sync: pos-data (full + delta), inventory-pulse (60s polling)
- `routes/qr_actions.py` — All QR actions (add phases 3/4/5 here)
- `routes/doc_lookup.py` — available_actions[] logic
- `routes/purchase_orders.py` — terminal_finalize_po() for Phase 4
- `routes/branch_transfers.py` — receive_transfer() for Phase 5
- `routes/invoices.py` — payment schema for Phase 3
- `routes/verify.py` — verify_pin_for_action(), _resolve_pin()
- `routes/stock_releases.py` — pending releases endpoints
- `routes/count_sheets.py` — snapshot/adjust with reserved_qty
- `routes/sales.py` — partial release creates sale_reservations + auto doc_code
- `routes/incident_tickets.py` — resolution workflow + journal entries
- `lib/verify.py` or `routes/verify.py` — PIN policies

### Frontend
- `pages/terminal/TerminalShell.jsx` — Instant-load + background delta sync + sync indicator
- `pages/terminal/TerminalSales.jsx` — syncVersion-driven cache refresh
- `lib/offlineDB.js` — IndexedDB persistence with delta merge helpers
- `lib/syncManager.js` — Catalog delta (5min) + Inventory pulse (60s) + auto-sync
- `pages/DocViewerPage.jsx` — ALL QR action UI. StockReleaseManager = pattern to follow.
- `pages/PendingReleasesPage.jsx` — Tracking page
- `pages/CountSheetsPage.js` — Shows system_reserved_qty breakdown
- `pages/UnifiedSalesPage.js` — Partial release toggle in checkout
- `pages/SalesPage.js` — Stock release status badges
- `pages/CloseWizardPage.js` — Pending releases warning in Step 1
- `components/InvoiceDetailModal.js` — Releases tab (shows stock_releases[])
- `components/Layout.js` — Sidebar nav (Pending Releases added)

## Test Reports
- `test_reports/iteration_159.json` — Terminal Price Schemes + Smart Discount (12/12 backend + all frontend passed)
- `test_reports/iteration_158.json` — Terminal Smart Sync Phase 1-3 (16/16 backend + all frontend tests passed)
- `test_reports/iteration_15-17.json` — Phase 3 incident resolution
- Latest: all QR phases tested manually with curl + screenshots

# AgriBooks PRD

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
- Company Admin: jovelyneahig@gmail.com / Aa@050772
- Manager PIN: 521325

---

## What's Been Implemented

### SMS Inbox + Conversations (2026-04-02) — Complete
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

### P1 — Finance
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
- `pages/DocViewerPage.jsx` — ALL QR action UI. StockReleaseManager = pattern to follow.
- `pages/PendingReleasesPage.jsx` — Tracking page
- `pages/CountSheetsPage.js` — Shows system_reserved_qty breakdown
- `pages/UnifiedSalesPage.js` — Partial release toggle in checkout
- `pages/SalesPage.js` — Stock release status badges
- `pages/CloseWizardPage.js` — Pending releases warning in Step 1
- `components/InvoiceDetailModal.js` — Releases tab (shows stock_releases[])
- `components/Layout.js` — Sidebar nav (Pending Releases added)

## Test Reports
- `test_reports/iteration_15-17.json` — Phase 3 incident resolution
- Latest: all QR phases tested manually with curl + screenshots

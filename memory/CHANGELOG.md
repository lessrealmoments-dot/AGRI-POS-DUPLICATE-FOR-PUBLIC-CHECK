# AgriBooks Changelog

## May 1, 2026 — Phone Field on Team Users
**Confirmed: SMS reminder roles resolve from the Users collection** (Team page),
not the Collection Recipients. The Collection Recipients in
Messages → Settings is a fallback override for orgs that don't yet have
team users with the right role.

- **Backend** (`routes/users.py`):
  - `POST /api/users` accepts `phone` in the body, stores trimmed value on
    the new user doc.
  - `PUT /api/users/{user_id}` adds `phone` to the editable-field whitelist.
- **Frontend** (`pages/TeamPage.js`):
  - **Mobile Phone** input added to the "Create New User" / "Edit User"
    dialog with helper text explaining it's used by SMS reminders + that
    Collection Recipients is the fallback.
  - Team table row now shows the phone under email, or an amber
    `"No phone — won't receive SMS"` warning when missing — at-a-glance
    spotting of users who'd be silently skipped by the scheduler.
  - `BLANK_FORM`, `openEdit`, and the save payload all include the phone.
- **Verified**: phone set on a Manager user immediately appears in
  `/api/sms/close-reminder/diagnose` under that branch's manager
  `recipient_phones`, so the scheduler now finds the number on the next
  tick.
- New regression suite: `tests/test_user_phone_field_194.py` (4 tests:
  create persists, PUT updates, diagnose reflects, optional phone OK).


## May 1, 2026 — Team SMS Reminders UI (per-stage toggles + per-branch close time)
- **New `sms_close_stages` collection** (per-org overrides) — each stage of
  the close-reminder schedule can be independently enabled/disabled and have
  its recipient roles narrowed. Defaults seed from the in-code `STAGES` list
  so nothing breaks for existing orgs.
- **Scheduler (`routes/close_reminder.py`)** now caches and consults
  per-stage settings on every tick. Disabled stages are skipped; narrowed
  recipient lists replace the in-code defaults before `_dispatch_stage`.
- **Backend endpoints**:
  - `GET  /api/sms/close-reminder/stages` — returns 7 stage rows with
    `label`, `timing`, `enabled`, `recipients`, `default_recipients` +
    `valid_roles` list (cashier/manager/owner/admin/auditor).
  - `PUT  /api/sms/close-reminder/stages/{stage_key}` — admin-only
    upsert. Unknown stage keys ⇒ 404; unknown roles inside `recipients`
    are silently dropped.
  - `PUT  /api/sms/close-reminder/branch-close-time/{branch_id}` — admin
    sets a branch's `close_time_h` (0–24 float). Flows into the scheduler
    on the next tick, no restart.
- **Frontend**: new `components/sms/TeamSmsRemindersCard.js` rendered at
  the top of the Messages → Settings tab:
  - Per-branch "Close Time" input (HH:mm) with Save + Preview toggle so the
    admin picks which branch drives the "Fires at" display.
  - For each stage: toggle, label, timing hint, 5 role chips (tap to
    include/exclude), and a computed "Fires at HH:mm" badge derived from
    the previewed branch's close time.
  - Optimistic updates so toggles feel instant; reconciles to server echo.
- **Regression suite**: `tests/test_team_sms_stages_193.py` — 6 tests
  (defaults return, persist toggle + recipients, unknown stage 404,
  unknown role dropped, branch close-time validation, scheduler reads
  disabled stage). All 11 tests in the timezone + stage suites green.


## May 1, 2026 — Per-Tenant Timezone (Multi-Tenant Aware Scheduler)
- Added **organization-level timezone** setting (`organizations.timezone`
  field, mirrored on `settings.company_info.value.timezone` for legacy
  readers). Default: `Asia/Manila` so existing tenants behave identically.
- **Close-reminder scheduler now runs per-org local time** instead of a
  hardcoded `UTC+8` offset. Each branch is evaluated against its
  organization's wall-clock time, quiet-hours window, and stage trigger
  times. A Philippine tenant gets their 3 PM catch-up at 3 PM PHT, a US
  tenant gets theirs at 3 PM America/New_York, etc.
- New backend endpoints:
  - `GET /api/settings/timezone` — returns `{timezone, choices[]}` with a
    curated list of 40 common IANA zones (Asia/Australia/Americas/Europe/
    Africa + UTC).
  - `PUT /api/settings/timezone` — admin-only, validated against `zoneinfo`
    so the scheduler never gets an unloadable string.
  - `GET /api/sms/close-reminder/diagnose` — admin snapshot of what the
    scheduler sees right now: current local time, quiet-hours flag, per-
    branch next stage fire time, and resolved recipient phone count by
    role. Immediately exposes issues like "all recipient phones empty".
- Frontend (`pages/SettingsPage.js`) — new **Organization Timezone** card
  inside Business Info tab: dropdown of IANA zones + live wall-clock
  preview (updates every 30s) + "Your device is in X" hint if the browser
  zone differs from the saved org zone.
- Frontend (`pages/UnifiedSalesPage.js`) — `localToday()` now reads the org
  TZ cached in `localStorage` (`agribooks.org_tz`, set by AuthContext on
  `/auth/me`) so the default sale date reflects the tenant's local
  calendar, not the browser's.
- New regression suite: `tests/test_org_timezone_192.py` (5 tests)
  covering GET/PUT persistence, invalid-TZ 400, mirror write, and
  scheduler resolver picking up changes without restart.


## May 1, 2026 — Product Search Refinement: Short-Numeric Prefix-of-Word
Tightened the strict pass on both the frontend grid and `/products/search-detail`
so short numeric tokens no longer leak unrelated products via SKU collisions.

**Token rules (now consistent on both layers):**
- 1–3 digit pure numbers (`1`, `14`, `200`) must **prefix-match a whole word
  in the NAME** (anchored on string-start or whitespace/dash/slash/comma).
- Everything else (alphanumeric, longer numbers, alpha words) keeps the
  existing case-insensitive substring match across name + SKU + barcode.

**Behavior changes (verified):**
- `14-14-14` now returns ONLY actual 14-14-14 products. Previously leaked
  any product whose SKU happened to contain `14` (FINEX/BOYOT/etc.).
- `Galimax 1` returns ONLY Galimax 1. Previously leaked Galimax 2 / 3 / 21
  via SKU random-suffix collisions.
- `Galimax 2` correctly returns BOTH Galimax 2 and Galimax 21 (both have a
  name word starting with `2`).
- Ranking refined: name-prefix match > contiguous substring > token-only.
  Tiebreaks by name length so the most specific name surfaces first.

**Files**: `pages/UnifiedSalesPage.js` (frontend grid filter),
`routes/products.py` (server endpoint with Mongo regex `(?:^|[\s\-/,])` anchor).

**New regression suite**: `tests/test_product_short_numeric_search_191.py`
(6 tests). All 11 product-search tests now green.


## Apr 30, 2026 — Typo-Tolerant Product Search Fallback (Sales Quick Mode)
- Built on top of the token-AND search shipped earlier today. Strict pass
  still runs first; the fuzzy fallback only kicks in when strict returns 0.
- **Guardrails** (so search never goes "all over the place"):
  - Tokens shorter than 4 chars OR purely numeric ("1", "2", "20kg") MUST
    match exactly — prevents `Glimax 1` from leaking `Galimax 2` results.
  - Levenshtein ≤ 1 edit for tokens 4–7 chars, ≤ 2 for tokens 8+ chars.
  - Candidate pool capped at the first 200 products to keep typing snappy.
  - Bounded `levenshteinAtMost` helper bails out per-row when the running
    minimum exceeds `maxDist`, so most pairs short-circuit cheaply.
- **UI banner** (`fuzzy-hint-banner`): when fuzzy results are shown, an amber
  banner reads `No exact match for "X" — showing N closest matches. Did you
  mistype?` with a `Clear` button to reset the search. The user is never
  surprised by unexpected products in the grid.
- Verified via REPL simulation:
  - `Glimax` → finds both Galimax products (1 edit each)
  - `Stater Vital` → finds Starter Premium Vital (1 edit)
  - `Galimax 1` → only Galimax 1 (number stays exact, no Galimax 2 leak)
  - `Glimax 1` → only Galimax 1 (typo recovers, number still exact)
  - `vit` → Starter Premium Vital (short token uses exact substring path)
  - `NotInName` → empty (no junk matches)


## Apr 30, 2026 — Smart Token-Based Product Search
- **Frontend Quick-mode product grid** (`pages/UnifiedSalesPage.js`): rewrote
  the filter to split the query on whitespace / dashes / slashes / commas and
  require EVERY token to appear in `name + sku + barcode` (order-independent).
  Results ranked: full-phrase substring hits first, token-only hits next,
  shorter names breaking ties.
- **Backend `/api/products/search-detail`** (`routes/products.py`): same
  token-AND semantics now in the Mongo query itself. Each token compiles to a
  `regex.escape`'d `$or` over name/SKU/barcode; the tokens are `$and`'d
  together. Single-token queries keep the simpler shape so existing index
  usage is unchanged.
- Examples now matching:
  - `Galimax 1 Poultry Feeds Pilmico` → `Galimax 1 Pilmico - Poultry Feeds` ✓
  - `Starter Vital` → `Starter Premium Vital` ✓
  - `Pilmico Galimax` (reverse) → `Galimax 1 Pilmico - Poultry Feeds` ✓
- New regression test: `tests/test_product_token_search_190.py` (5 tests).


## Apr 30, 2026 — Per-Recipient Test SMS + SMS Permission Hardening
- **Per-row "Test" buttons** on `/messages` → Settings → Collection Recipients
  (`pages/MessagesPage.js`). Each phone field (Owner, Admin, Manager-fallback,
  Auditor-fallback, per-branch Manager, per-branch Auditor) gets its own small
  amber "Test" button that sends a tagged `[SAMPLE]` SMS to just that number,
  so admins can verify a single recipient without spamming the whole list.
- New backend endpoint **`POST /api/sms/send-sample-single`** — admin-only via
  `settings.edit`, accepts `{phone, role, branch_id?, branch_name?}` and queues
  one SMS through the same gateway as live notifications.
- **SMS permission gating tightened** — closed three pre-existing gaps where
  cashiers/staff could hit sensitive SMS endpoints:
  - `POST /sms/send` (manual compose) — now requires `customers.edit`
    (admin/manager only by default; blocks cashier/staff/inventory).
  - `POST /sms/templates/backfill` — switched from "admin or manager" role
    check to `settings.edit` (admin-only).
  - `POST /sms/queue/{id}/retry` — added `settings.edit` (was ungated).
- **Frontend route guard** — `/messages` is now wrapped in a new `AdminRoute`
  in `App.js` so a direct URL hit by a cashier/staff bounces to `/dashboard`,
  not just hidden in the sidebar.
- New regression test: `tests/test_sms_permission_gating_190.py` — 5 tests
  covering cashier 403s on `/sms/send`, `/sms/templates/backfill`,
  `/sms/send-sample-single` plus admin happy-path + empty-phone validation.


## Apr 30, 2026 — SMS Template Auto-Upgrade + Restore Company Self-Heal
- **`_ensure_templates` now version-aware** (`routes/sms.py`):
  - Each seeded template carries a `default_body` snapshot. On every call,
    templates whose `body == default_body` (i.e. unedited) are auto-upgraded
    to the latest factory wording. User-customized templates are left alone.
  - `LEGACY_DEFAULT_BODIES` registry tracks known stale wording (incl. the
    pre-Apr-2026 `Sales BLOCKED` close-day templates) so legacy docs that
    pre-date `default_body` can still be safely refreshed.
  - Triggered automatically on `GET /sms/templates`, `GET /sms/settings`,
    `POST /sms/templates/backfill`, and the `queue_sms` self-seed path —
    so existing tenants pick up the corrected wording the next time they
    open Settings → Messages, with zero manual DB intervention.
- **`POST /sms/templates/backfill`** now also reports `upgraded` count and
  per-key list, not just newly-seeded inserts.
- **Restore Company Info self-heal** (`routes/settings.py`):
  - When the user's org row is missing (deleted/orphan tenant), the endpoint
    now recreates the organization as a fresh `<full_name>'s Company` trial
    with a default branch + SMS templates, instead of returning the cryptic
    `404 Organization record missing` toast. Returns `{"recreated": true}`.
  - When the user's JWT carries no `organization_id` at all, the error text
    now nudges them to log out + log back in to refresh the token.
  - Frontend (`pages/DashboardPage.js`) shows a friendlier toast and auto-
    reloads on `recreated:true` so the new tenant context is picked up.
- New regression tests:
  - `tests/test_sms_template_upgrade_190.py` (3 tests) — legacy stale upgrade,
    customized-template-not-clobbered, idempotent re-run.
  - `tests/test_restore_company_self_heal_190.py` (2 tests) — recreate
    missing org + idempotent already-set.



## Apr 30, 2026 — "Send Sample SMS" for Collection Notification Recipients
- New backend endpoint `POST /api/sms/send-sample-recipients` — queues a tagged
  `[SAMPLE]` SMS to every configured recipient (Owner, Admin, Manager/Auditor
  fallback, and each branch-specific Manager/Auditor). De-dupes by phone,
  resolves branch names server-side, requires `settings.edit`.
- `MessagesPage.js` — added "Send Sample SMS" button next to "Save Recipients"
  with a preview dialog listing every recipient (role + branch + phone) and the
  sample message body before dispatch. Uses current UI state so unsaved edits
  can be tested.
- Template key `sample_recipient_test` used for queue tracking; routes through
  the same gateway as live notifications.
- Verified: curl 2/2 recipients queued, empty payload returns 400, dedup
  correctly collapses duplicate phones.


## Mar 31, 2026 — SMS Messages Moved to Main App (Admin-Only)
- **MessagesPage.js** created at `/messages` route — admin-only, 5 tabs:
  - Message Queue (Pending/Sent/Failed/Skipped filters with counts)
  - Compose (customer search + manual message)
  - Promo Blast (filter by min balance, personalize with `<customer_name>`)
  - Templates (edit/enable/disable all 10 templates, clickable placeholder tags)
  - Settings (toggle each SMS trigger on/off)
- Added to sidebar under Management with `adminOnly: true` flag
- Removed Messages from terminal mode selector (terminal stays branch-level: Sales/PO/Transfers)
- Tested: 100% backend (22/22), 100% frontend (iteration_152.json)

## Mar 31, 2026 — SMS Engine Phase 4: Terminal Messages UI
- **TerminalMessages.jsx**: New Messages tab in terminal floating mode selector
  - Queue view: Pending/Sent/Failed tabs with status counts, "Send via SMS App" button (opens native SMS), Mark Sent, Skip, Retry
  - Compose view: Customer search, message textarea (320 char), Queue Message
  - Blast view: Promo blast with min_balance filter, personalization via `<customer_name>`
  - Templates view: All 10 templates with Active/Disabled badges, inline edit, toggle on/off
- TerminalShell.jsx: Added Messages to TABS array with MessageSquare icon
- Tested: 100% backend (22/22), 100% frontend, no regression on Sales/PO/Transfers (iteration_151.json)

## Mar 31, 2026 — SMS Engine Phase 1-3 + Terminal Credential Login
- **SMS Engine**: Full queue-based SMS system with 10 templates, auto-triggers, scheduled reminders
  - `sms_queue`, `sms_templates`, `sms_settings` collections
  - Auto-triggers: credit sale → SMS, payment received → SMS, interest/penalty applied → SMS
  - Scheduled: daily 8AM reminders (15-day, 7-day, overdue), monthly 1st summary
  - Manual: compose single SMS, promo blast with customer filters
  - Gateway API: `GET /pending`, `PATCH /mark-sent`, `PATCH /mark-failed`, retry, skip
- **Terminal Credential Login**: New "Login" tab on terminal pairing screen
  - Manager login → auto-links to assigned branch
  - Admin login → branch selector dropdown
  - `POST /api/terminal/credential-pair` endpoint
- Tested: 100% backend (22/22), 100% frontend (iteration_150.json)

## Mar 31, 2026 — Customer Receivables Left Panel on PaymentsPage
- **New backend endpoint**: `GET /api/customers/receivables-summary` — aggregates open invoices per customer with total balance, overdue balance, invoice count using MongoDB aggregation pipeline
- **PaymentsPage.js rewritten** with left sidebar panel (matching PaySupplierPage pattern)
- **Filter toggle**: "With Balance" (default, 36 customers) / "All" (64 customers including zero-balance)
- **Sort toggle**: By Balance (highest first) / By Name (A-Z)
- **Search filter**: Filters customer list by name in real-time
- **Customer row**: Shows name, total balance (red), invoice count, DUE badge for overdue amounts, interest rate indicator
- **Total receivables**: Displayed at top of customer list (e.g. ₱151,521.94)
- Clicking a customer selects them, loads invoices on right — replaces old search-only workflow
- **Orphaned files deleted**: `PODetailModal.js` and `SaleDetailModal.js` removed (zero imports confirmed)
- Tested: 100% backend (9/9), 100% frontend (iteration_149.json)

## Mar 31, 2026 — Modal Consolidation Phase 4 (Extract FundTransferDialog)
- **FundTransferDialog.js** extracted from FundManagementPage inline transfer dialog
- Accepts `transferType`, `walletByType`, `branchId`, `onSuccess` props
- Supports all 4 transfer types: Cashier→Safe, Safe→Cashier, Safe→Bank, Capital Injection
- Each type shows appropriate auth fields (Manager PIN, TOTP, Owner PIN)
- FundManagementPage updated to use the extracted component
- Tested: 9/9 tests passed including Phase 2-3 regressions (iteration_148.json)

## Mar 31, 2026 — Modal Consolidation Phase 3 (C1 + C2 → AuthDialog)
- **AuthDialog.js** created as unified PIN/TOTP/Password authorization dialog
- `mode="pin"`: single PIN input with discrepancy fields (matches old VerifyPinDialog)
- `mode="totp"`: mode tabs (Owner PIN / Authenticator / Password) matching old TotpVerifyDialog
- `mode="either"`: multi-mode tabs (same as totp)
- **VerifyPinDialog.js** converted to thin wrapper `<AuthDialog mode="pin" />`
- **TotpVerifyDialog.js** converted to thin wrapper `<AuthDialog mode="totp" />`
- Zero page-level changes — backward compatible via wrapper pattern
- Tested: 5/5 tests passed (iteration_147.json)

## Mar 31, 2026 — Modal Consolidation Phase 2 (A2 Absorbs A4)
- **InvoiceDetailModal** (A2) extended with `compact` prop and `saleId` backward-compat alias
- When `compact=true`: renders single-view layout matching old SaleDetailModal (narrower dialog, no tabs, print buttons, inline receipts/payments/edit history, void button)
- **14 files migrated** from SaleDetailModal → InvoiceDetailModal with `compact`: SalesPage, AccountingPage, ExpensesPage, CustomersPage, CloseWizardPage, DailyLogPage, PaymentsPage, PendingReleasesPage, InternalInvoicesPage, ReportsPage (2x), DashboardPage, AuditCenterPage, QuickSearch (2x), TransactionSearchPage
- SaleDetailModal.js now has **zero imports** — orphaned (safe to delete later)
- Z-reports: zero impact (UI-only migration, same API endpoints)
- Tested: 8/8 frontend pages passed (iteration_146.json)

## Mar 31, 2026 — Modal Consolidation Phase 1 + Modal Registry PDF
- **Modal Registry PDF** generated — catalogs all 23 modal/dialog components with screenshots, groups (A-G), redundancy map, quick reference. Saved to R2 at `agribooks-docs/reports/modal-registry-2026-03/`
- **Phase 1:** ReviewDetailDialog (A1) absorbs PODetailModal (A3). Added backward-compat props: `poId`, `poNumber`, `onUpdated`, `onOpenChange`. Resolution: `poNumber` → `/invoices/by-number` → UUID → `/dashboard/review-detail`
- **7 pages migrated:** CloseWizardPage, PaySupplierPage, QuickSearch, AuditCenterPage, SuppliersPage, TransactionSearchPage, DashboardPage — all use ReviewDetailDialog now
- PODetailModal.js now has zero imports (orphaned)

## Mar 31, 2026 — Security Alert Enrichment (Phases 1-3)
- **Phase 1:** Authenticated PIN alerts enriched with user_role, user_email, branch_name. New message: "Manager (Manager) entered wrong PIN 6x at Branch 1 — Action: Context"
- **Phase 2:** QR brute-force alerts replaced "Unknown IP" with "AgriSmart Terminal at Branch X". Full doc enrichment (doc_number, counterparty, amount, doc_id). terminal_id passed through all 3 QR action call sites
- **Phase 3:** SecurityAlertDetail expandable card in NotificationsPage — WHO+WHAT cards (auth) / TERMINAL+DOCUMENT cards (QR). Clickable doc number opens ReviewDetailDialog. Lock banner for locked docs. "View Receipt" button for authenticated PIN alerts with linked doc
- Tested: 28/28 backend, 11/11 frontend (iteration_145.json)

## Mar 31, 2026 — Compliance Deadline Notifications (Phase 5)
- APScheduler daily job at 8:30 AM fires compliance_deadline notifications
- Covers: expired docs (critical), expiring within 30d (warning), missing monthly filings after 15th
- Dedup via metadata.dedup_key. `create_notification()` extended with severity_override param
- Frontend: compliance_deadline type with orange FileWarning icon + ComplianceDetail expandable row
- Tested: 22/23 backend, 100% frontend (iteration_144.json)

## Mar 12, 2026 — Inline Interest Rate Override
- Added editable interest rate input in Receive Payments charges section
- Pre-fills with customer's saved rate; allows override for customers with no rate
- "Save to customer profile" checkbox when rate differs from saved
- Backend `generate-interest` + `charges-preview` accept `rate_override` param
- Interest formula: `principal × (rate/100/30) × days from last_interest_date` (prevents double-charging)
- Testing: 9/9 backend + all frontend UI tests passed

## Mar 12, 2026 — QB-Style Receive Payments Redesign + Discount Feature
- **Redesigned** PaymentsPage.js to match QuickBooks "Customer Payment" layout:
  - Inline customer search with balance display on top-right
  - Payment method as icon buttons (Cash, Check, Bank, GCash, Maya)
  - Invoice table with QB columns: Date, Number, Type, Orig. Amt, Amt. Due, Discount, Payment + Totals row
  - "Amounts for Selected Invoices" summary panel (Amount Due, Applied, Discount, Remaining)
  - Memo + Save & Apply / Clear at bottom
- **Added Discount on Interest/Penalty**: Per-invoice discount input with % and fixed amount toggle
  - Backend records discounts as `method: "Discount"` payment entries (no wallet impact, audit trail)
  - Only available on interest_charge and penalty_charge invoice types
- Testing: 32/32 frontend + 9/10 backend tests passed

## Mar 12, 2026 — Close Wizard "Find & Pay" Panel Fix + Enhancement
- **Fixed 3 bugs** in CloseWizardPage.js Step 3 "Receive payment for a customer (not listed above)" panel:
  1. `overflow-hidden` CSS on container clipped the customer search dropdown
  2. Wrong API endpoint: `/invoices?status=open` → `/customers/{id}/invoices` (now includes "partial" status invoices)
  3. Field name mismatch: dialog expected `remaining_balance` but invoices have `balance`
- **Enhanced** panel into full mini-PaymentsPage:
  - Multi-invoice per-row payment allocation
  - Interest generation (uses customer's configured rate)
  - Penalty generation with configurable percentage
  - Quick total input + "Pay All" auto-apply button
  - Uses proper `/customers/{id}/receive-payment` endpoint for multi-allocation
  - Wizard data auto-refreshes after payment so AR table updates immediately
- Testing: 10/10 frontend tests passed

## Mar 11, 2026 — Critical Accounting Fixes
- Fixed `is_digital_payment` helper: "Partial" and "Split" payments no longer classified as digital
- Fixed starting float calculation for first-ever daily close
- Fixed Sales Log running totals: digital payments and split sale totals now correct
- Created and ran data migration endpoints to fix corrupted invoice + wallet balances
- Established agent communication protocol: explain before coding, ask before creating new modules

## Earlier — Various Bug Fixes
- Checkout payment type tabs fix (Split/Partial/Credit)
- Receipt upload QR code visibility
- Partial payment closing wizard decomposition
- autoComplete fix (48 instances)
- PIN verification audit (all endpoints connected)
- Quick customer picker in checkout dialog
- Digital payment separation in closing formula

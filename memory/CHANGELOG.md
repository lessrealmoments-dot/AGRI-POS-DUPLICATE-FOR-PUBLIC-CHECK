# AgriBooks Changelog

## Feb 2026 — QR Document Lookup Tenant-Context Bug (Iter 201)

**Bug**: Scanning a sale/PO/transfer QR returned **"Document not found"** / **"Invoice not found"** even though the doc_code and document both existed in Mongo.

**Root cause**: Public QR endpoints (`/api/doc/view/:code`, `/api/doc/lookup`, all `/api/qr-actions/:code/*`) come in **without a JWT**. After iter 180 the multi-tenant `db` proxy fails-closed when no org context is set — so `db.invoices.find_one(...)` returned 0 rows even when the doc existed. The `doc_codes` collection itself isn't tenant-scoped, so the code lookup succeeded; only the downstream `invoices` / `branch_transfer_orders` / `customers` / `branches` / `upload_sessions` reads silently returned nothing.

**Fix**:
- **`backend/routes/doc_lookup.py`** — new `_resolve_doc_code_with_context()` helper. Looks up the doc_code, calls `set_org_context(doc_ref.org_id)`, and falls back to reading the source document via `_raw_db` to recover the org_id when legacy entries lack it (then backfills the `doc_codes` row). Used by both `view_document_open` (public) and `lookup_document` (PIN-protected).
- **`backend/routes/qr_actions.py`** — `_resolve_doc()` updated with the same context-hydration logic. Covers `verify_pin`, `release_stocks`, `generate_upload_token`, `receive_payment`, `transfer_receive`.
- **`frontend/src/pages/UnifiedSalesPage.js:3286`** (collateral bug) — signature print flow was calling `/doc/generate-code` with `doc_type: 'sale'` (an unknown type) which created an unresolvable doc_code. Changed to `'invoice'` so it returns the existing code.
- **`backend/scripts/backfill_doc_codes_org_id.py`** — one-shot migration: backfilled `org_id` on **84** legacy doc_codes by reading their referenced documents.
- **`backend/tests/test_qr_doc_view_tenant_context_201.py`** — 3 regression tests (passing): public `/doc/view/:code` resolves invoice w/o auth, `/qr-actions/:code/context` works, unknown code → 404.

**Verified live**: `GET /api/doc/view/M3VYT6P6` now returns the full invoice JSON (was returning `{"detail":"Invoice not found"}` before the fix).


## May 1, 2026 — Layer 2: Branch Switcher UX + Backend Whitelist (Iter 198 cont'd)

A multi-branch manager/auditor can now actively **switch between** their assigned branches inside the app — POS, inventory, reports, close-day all flip to the active branch — while being strictly denied access to any branch they're not assigned to. Per-user `permissions` continue to gate actions independently (a manager assigned to branches 1-3 with `sales.create=false` still cannot sell anywhere).

### Frontend
- **`contexts/AuthContext.js`** — three changes:
  - `canViewAllBranches` is now true for admins, legacy unscoped users, AND multi-branch users (`branch_ids.length >= 2`)
  - `switchBranch` validates the target against `assignedBranchIds` (silently refuses off-list switches)
  - `effectiveBranchId` honors `selectedBranchId` for multi-branch users (was previously locked to legacy `branch_id`); auto-snaps back to `'all'` if `localStorage` holds a stale branch from another account
  - New `assignedBranchIds` exposed via context (memoized: `branch_ids` ∪ legacy `branch_id`)
- **`components/Layout.js`** — header branch dropdown is now filtered to the user's `assignedBranchIds`. Admins still see every branch; managers see only their assigned 2/3/N. Empty assignment falls back to legacy unscoped behaviour.

### Backend (defense-in-depth)
- **`utils/auth.py`** — two new exports:
  - `user_branch_ids(user)` → unified accessor (folds legacy `branch_id` into `branch_ids` list)
  - `assert_branch_access(user, branch_id)` → raises 403 unless `branch_id ∈ user.branch_ids` (admin bypass + legacy unscoped pass-through). Use as a one-liner in any endpoint that takes a client-supplied branch_id.
- **`utils/branch.py`** — `get_user_branches()` rewritten to honor `branch_ids` list. Because every read endpoint already routes through `get_branch_filter` (invoices, inventory, search, dashboard, reports, accounting, count_sheets, purchase_orders, daily_operations), this single helper change extends multi-branch READ protection to **every** endpoint that filters by branch. Cross-branch reads from forged URLs return 403 automatically.
- **`routes/sales.py`** — POST `/unified-sale`: `assert_branch_access(user, branch_id)` after `check_perm`. A manager forging `branch_id` in the request body is rejected before any DB writes.
- **`routes/accounting.py`** — POST `/expenses`: same guard.
- **`routes/daily_operations.py`** — POST `/daily-close`: same guard.
- **`routes/branch_transfers.py`** — POST `/branch-transfers`: guard applied to BOTH `from_branch_id` and `to_branch_id` (a manager can only transfer between branches they're assigned to).

### Tests — `tests/test_branch_switcher_layer2_198.py` (11/11 passing)
- Unit: `assert_branch_access` admin-bypass, legacy-unscoped pass-through, multi-branch whitelist, legacy single-branch lock, no-op for empty/all
- Unit: `user_branch_ids` correctly combines `branch_ids` + legacy `branch_id`
- Unit: `get_user_branches` returns multi-branch list
- E2E: real manager user with `branch_ids=[allowed]` is rejected with 403 on:
  - `POST /api/unified-sale?branch_id=forbidden`
  - `POST /api/expenses?branch_id=forbidden`
  - `POST /api/daily-close?branch_id=forbidden`
- E2E: admin is unaffected (always allowed)

### Two-layer enforcement (the contract)
| Layer | Check | Source |
|---|---|---|
| Branch access | `branch_id ∈ user.branch_ids` (or admin) | `users.branch_ids` (Team page) |
| Module permissions | `user.permissions[module][action] == true` | `users.permissions` (Team → Permissions tab) |

Both must pass. Failing either = 403. Frontend hides denied UI; backend rejects denied requests. No bypass via DevTools.

### Migration / Upgrade notes
- **Zero migration required.** Existing single-branch users (legacy `branch_id` only) continue to work via auto-fold. When an admin edits them on the Team page, `branch_ids` is seeded from their current single branch.
- Layer 2 surface area: ~30 read endpoints get multi-branch protection automatically via `get_user_branches`; 4 high-risk write endpoints (POS sale, expense, close-day, branch transfer) get explicit `assert_branch_access`. Any future write endpoint that takes a client-supplied `branch_id` should add the same one-liner.


## May 1, 2026 — Close-Reminder Scheduler Fix + Multi-Branch Users + Collection-Recipient Fallback (Iter 198) ⭐ Critical Bug Fix

**Root cause fixed**: the scheduled close-day SMS (3 PM catch-up, precheck, late notice, escalation, day+1/day+2 overdue) **never fired** in production. The background scheduler ran every 60 s, but it used the tenant-scoped `db` proxy without an `_current_org_id` ContextVar set. The proxy fails closed by injecting `organization_id: "__no_org_context__"`, so every query matched zero rows — 0 branches, 0 users, 0 SMS queued, and (because `tick_once` never logged anything when it fired nothing) no log lines to reveal the silent failure. Only the synchronous Z-Report SMS (fired from inside an HTTP request) worked.

### Backend
- **`routes/close_reminder.py`** — rewired entire scheduler to use `_raw_db` with explicit `organization_id` filters throughout. `tick_once`, `_dispatch_stage`, `_build_branch_snapshot`, `_already_fired`, `_mark_fired`, `_is_branch_closed_on`, `_is_calendar_closed`, `_load_stage_settings`, `diagnose_for_org`, `send_zreport_finalized` all converted. Added per-tick log line when stages fire so production has visibility.
- **Multi-branch user model**: new `users.branch_ids: list[str]` field. A manager/auditor can now be assigned to multiple branches and receives SMS for all of them. Legacy `users.branch_id` single value is still honored (auto-folded into the list). New helpers `_user_branch_ids()` and `_user_covers_branch()`.
- **`_resolve_recipients` rewritten**:
  - Admin role-key maps to `role=admin` users (covers all branches).
  - Owner role-key aliases to `role=admin` (since there is no separate owner role in the system).
  - Auditor role-key maps to `is_auditor=True` capability flag — NOT `role=auditor`, which never existed.
  - Branch scoping honors `branch_ids` list + legacy `branch_id`.
  - New `include_debug=True` returns per-role `{matched_users, users_without_phone, fallback_used}` for better test-button UX.
- **Collection-Recipient fallback**: when a role resolves to zero users with phones, the matching phone from Settings → Messages → Collection Notification Recipients (`owner_phone`, `admin_phone`, `manager_phone`, `auditor_phone`) is added as a synthetic recipient, respecting per-branch overrides for manager/auditor. Prevents silent misses when the admin hasn't yet filled in team phone numbers.
- **`routes/users.py`** — `/api/users` POST and PUT now accept `branch_ids: list[str]`. Payload normalized via new `_normalize_branch_ids()` helper. `branch_id` stays in sync with first entry for backward compat.
- **`routes/sms.py`** — `/sms/close-reminder/test-stage` response now includes `resolution` (per-role debug breakdown) and `fallback: true/false` on each recipient so admins see exactly why each role resolved to N.

### Frontend
- **`pages/TeamPage.js`** — Branch field in Edit User dialog is now context-sensitive:
  - Admin → read-only "All Branches (administrators are unscoped)" label.
  - Manager / Cashier / Inventory / custom → multi-select checklist of branches. First picked = Home branch. Empty = "All Branches (unscoped)" (legacy fallback) with amber warning.
  - New "Also has Auditor capability" toggle for manager/cashier/inventory (sets `is_auditor=true`).
- User rows in the table now show multi-branch assignments: "All Branches" for admins, "N branches — Main Warehouse +1" tooltip for multi-branch, single name for single-branch.
- **`components/sms/TeamSmsRemindersCard.js`** — Test button toast now shows per-role breakdown (e.g. "admin: 1 user · manager: 0, 2 no-phone, +fallback") and flags fallback recipients explicitly.

### Tests
- `tests/test_close_reminder_scheduler_fix_198.py` — 5 passing tests covering: tick_once no longer fails-closed with no org context; `_resolve_recipients` honors `branch_ids` list; `is_auditor` capability matches auditor role-key; Collection-Recipient fallback kicks in when no team user matches; `/test-stage` endpoint returns `resolution` debug payload.

### Upgrade notes
- **No migration required**: existing users keep working via the legacy `branch_id` fallback. When admin edits a user, the multi-branch list is seeded from their current single branch.
- Admin noise policy unchanged — admin was already only on escalation/day+1/day+2/zreport in `STAGE_DEFAULTS`, which is correct.


## May 1, 2026 — Customer Dedupe Manager + Bulk Delete + No-Blocker Import (Iter 197) ⭐ Critical Bug Fix

**Root cause fixed**: the customer importer previously had THREE silent failure modes where opening-balance invoices were simply dropped — `exact_dupe` rows (same name or phone as an existing customer), `fuzzy` rows left at the default `action=skip`, and `duplicate_within_file` rows. On a re-import of the same file, *every row* fell into `exact_dupe` and **zero** OB invoices were created, which is what the user was seeing.

### New architecture (user-validated)
- **Import = dumb ingest.** Every valid row becomes a new customer + OB invoice (if OB > 0). No duplicate blocking, no decision screen. This guarantees no opening balance is ever silently lost.
- **Dedupe = background popup**, same UX as `PriceScanManager`. Scans the branch for fuzzy + phone-tail clusters on mount, every 5 min, on window focus, and on demand after import. A floating pill "N possible duplicates · M customers" surfaces when matches are found.
- **Merge rule**: master's non-empty fields *win*. If master's field is empty/zero and a duplicate has a value, the duplicate's value is copied over. Phones are merged (master first, dups appended, dedup).

### Backend
- **`routes/import_data.py`** — `/customers/preview` and `/customers/commit` gutted. Preview now returns every row in `auto_create` + `total_opening_balance` + `existing_similar_count` (informational). Commit creates a new customer + OB invoice for every valid row.
- **`routes/customers.py`** — four new endpoints:
  - `GET /api/customers/-/duplicates` — union-find clusters by normalized-name equality, token-sorted similarity ≥0.85, or shared 9-digit phone tail. Excludes pairs marked "distinct". Returns per-member balance/invoice count, sorted with richest cluster first.
  - `POST /api/customers/merge` — `{master_id, duplicate_ids[], canonical_name?}`. Re-points invoices / receivables / `sms_inbox` to master, fills empty master fields from dups, merges phones, soft-deletes dups with `merged_into=<master>`, recomputes balance from open invoices, writes to new `customer_merges` audit collection.
  - `POST /api/customers/mark-distinct` — persists pairwise "distinct" decisions in `customer_dedupe_decisions`, scoped per-branch. Future scans skip those pairs (but WILL re-flag if a *new* third customer joins the cluster — exactly the user's spec).
  - `POST /api/customers/bulk-delete` — **PIN-gated** via new `customer_bulk_delete` PIN policy (admin/manager/TOTP). Refuses customers with balance>0 or open invoices unless `force=true` (admin/owner only). Returns per-id deleted/blocked arrays.
- **`routes/verify.py`** — two new PIN-policy actions: `customer_bulk_delete` and `customer_merge`.
- Route ordering fix: `/duplicates` moved to `/-/duplicates` so it can't be shadowed by the dynamic `/{customer_id}` GET.

### Frontend
- **`components/CustomerDedupeManager.js`** (NEW) — mirrors `PriceScanManager`. Background scan, floating pill, full-screen dialog with per-cluster tables. Radio to pick master (user's choice), optional canonical-name rename, Merge / Different customers / Snooze buttons. Dispatches/listens on the `customer-dedupe-rescan` window event so the ImportPage can trigger an immediate rescan after commit.
- **`pages/ImportPage.js`** — fuzzy review cards removed; replaced with a single blue notice card: "Duplicate review happens AFTER import. N rows look similar to existing customers — you can merge them later via the Duplicate Review popup." Shows total OB ₱ committed. Fires `customer-dedupe-rescan` event on successful import.
- **`pages/CustomersPage.js`** — row checkboxes + select-all header, "Show only customers with no balance (safe to purge)" client-side filter, "Delete Selected (N)" button that opens a PIN-gated dialog with force-delete toggle (admin/owner only) and a per-id result view listing deleted + blocked rows with reasons.
- **`App.js`** — `<CustomerDedupeManager />` mounted alongside `<PriceScanManager />` in both `ProtectedRoute` and `AdminRoute`.

### Tests — `tests/test_customer_dedupe_bulk_delete_197.py` (6/6 passing)
1. Importer creates NEW customer + OB invoice even when same-name customer already exists
2. `/duplicates` clusters "James Ahig" ≡ "Ahig James", excludes unrelated customers
3. `mark-distinct` removes a pair from subsequent scans
4. `merge` moves invoices, merges fields, soft-deletes dups, writes audit trail, recomputes balance
5. Merge rule "master wins if present" — master's non-empty email/address NOT overwritten
6. Bulk-delete needs PIN, respects balance/open-invoice guards without `force`, admin-force works

### Migration note for existing deployments
Old customers that were silently skipped during previous imports (with their OB dropped) can now be re-imported — every row becomes a new customer regardless. The dedupe popup then lets you merge the duplicates together, which preserves both OB invoices on the master.



## May 1, 2026 — Branch-Scoped Product Editing (Iter 196) ⭐ Critical Bug Fix

**Footgun closed**: when a user had a specific branch selected in the sidebar
and edited a product's price, the edit was silently writing to the **master
catalog** (used by every branch). Caused months of accidental cross-branch
clobbering — e.g. user thinks they're fixing Branch 2's Credit price, but
the change applies to Branches 1, 3, 4 too. The Branch Stock + Price
importer had been writing per-branch correctly, so people built mental
models around "this app respects branch context" — but the manual edit
dialog didn't.

### Fix
- **Backend** `routes/products.py`:
  - `PUT /api/products/{id}` accepts `?branch_id=X`. When set (and not "all"):
    - `prices` and `cost_price` route to `branch_prices` (per-branch override)
    - Catalog fields (name, category, description, unit, etc.) still hit
      the master — those are tenant-wide attributes
    - Existing branch overrides are merged per-key (override wins, missing
      keys keep prior values intact)
    - Response reflects the BRANCH view (master ⊕ override) with new
      `price_source: "branch_override" | "global"` field
  - `GET /api/products?branch_id=X` now merges branch_prices into each
    row and tags `price_source` so the frontend can render the override
    chip. Without `branch_id` (or `=all`), behaviour is unchanged.
  - New helper `_enrich_with_branch_overrides()` — same merge logic that
    POS / search-detail already used, just lifted into the list path.

- **Frontend** `pages/ProductsPage.js`:
  - Edit dialog: contextual scope banner appears at the top —
    purple "Editing prices for {Branch} only — master untouched" when
    a branch is selected, blue "Editing master catalog (all branches)"
    on All-Branches view. Eliminates the ambiguity.
  - `handleSave()` passes `branch_id` query param to PUT when on a branch.
  - Toast confirms scope: "Saved to {Branch} only — master catalog untouched".
  - Per-row `⚙ Branch` chip surfaces when a price comes from a per-branch
    override — admins spot at a glance which products are customised at
    this branch vs inherited from master.

### Backwards compatibility
- All-Branches view unchanged — every existing edit flow that omits
  `branch_id` still hits the master.
- Existing branch_prices documents (from months of Branch Stock + Price
  imports) now become **visible** in the Products List when their branch
  is selected. They were always there, just hidden.

### Tests
- `tests/test_branch_scoped_product_edit_196.py` — 7 tests:
  - PUT with branch writes ONLY to branch_prices, master untouched
  - PUT without branch writes to master (legacy path)
  - PUT with `branch_id=all` treated as no branch (backwards compat)
  - Catalog fields always hit master regardless of branch_id
  - GET with branch context merges override + tags `price_source`
  - GET without branch context returns master + tags `global`
  - Branch A override doesn't leak to Branch B
  - Sequential edits on same branch preserve previously-set keys


## May 1, 2026 — Test Stage Button + Dynamic Price Scheme Columns (Iter 195)

**Two improvements**:

### Test Stage button (Team SMS Reminders)
Each stage row now has an inline **"Test"** button that fires a `[SAMPLE]` SMS
*right now* to the stage's currently-configured roles for the branch picked
in **Preview**. Lets admins verify routing without waiting for the real
trigger time.
- **Backend**: `POST /api/sms/close-reminder/test-stage/{stage_key}`
  with `{ branch_id }`. Resolves recipients via the same path the live
  scheduler uses, builds a `[SAMPLE]` body, queues directly to `sms_queue`.
  Bypasses the dedup log so it can be retested freely. Refuses if the stage
  is currently disabled or no users with phones match the roles.
- **Frontend** (`TeamSmsRemindersCard.js`): per-row Test button appears only
  when stage is enabled + a Preview branch is picked + at least one role is
  configured. Spinner during firing, toast with recipient count on success.
- **UI clarifier banner** added: explains stage on/off + role toggles are
  **org-wide**, only close times are per-branch. Fixes the confusion where
  users thought clicking "Preview Branch 1" then toggling roles scoped the
  change to that branch.
- Tests: `tests/test_test_stage_button_195.py` (5 tests: missing branch,
  unknown stage, disabled stage, no recipients with phones, happy-path queue).

### Dynamic price scheme columns (Import Center)
Import Center → Branch Stock + Price (and New Product Catalog) now
automatically show a column for **every active price scheme** — Retail,
Wholesale, Credit, and any future scheme the admin creates. No code change
needed when a new scheme is added.
- **Frontend** (`ImportPage.js`):
  - Loads `/api/price-schemes` on mount, builds `PRODUCT_FIELDS` and
    `BRANCH_STOCK_PRICE_FIELDS` dynamically from the result.
  - "Active price schemes" badge row above the column mapper shows what's
    available.
  - Auto-mapping logic now matches headers like "Credit Price" /
    "credit price" to the matching scheme automatically.
  - Preview table renders one "New {Scheme}" column per active scheme
    (replaces hardcoded New Retail / New Wholesale).
- **Backend** (`routes/import_data.py`):
  - `GET /api/import/template/{products|branch-stock-and-price}` now reads
    active `price_schemes` and emits a `{Scheme} Price` column for each.
    Sample rows updated accordingly.
- Tests: `tests/test_test_stage_button_195.py` covers template inclusion of
  Credit Price column when the scheme exists.


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

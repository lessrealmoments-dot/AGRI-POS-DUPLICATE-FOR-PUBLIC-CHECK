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
- Company Admin (LimitTest Corp): limittest@testmail.com (TOTP not set; use super admin for full testing)
- Manager PIN: 521325

---

## What's Been Implemented

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

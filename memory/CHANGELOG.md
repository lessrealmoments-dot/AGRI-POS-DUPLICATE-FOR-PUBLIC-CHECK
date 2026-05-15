# AgriBooks Changelog


## Feb 15 2026 — Stock Requests Phase 2: Mark Phantom PO Ordered + SMS 🟠 P1

**Gap closed**: After triage, DRAFT phantom POs sat in the supplying branch's list with no easy way to "lock them in" once Branch B negotiated with the supplier. Branch A had no notification that delivery was on the way.

**Backend** — new endpoint `POST /api/stock-requests/{request_id}/po/{po_id}/mark-ordered`:
- PIN-gated via the same supplying-branch policy as triage (`confirm_stock_request` action)
- Cross-request guard: PO must belong to the specified stock request
- Only DRAFT POs can transition (refused for any other status)
- Supports per-item `item_overrides` (final-negotiation pricing): updates `unit_price`, `quantity`, `discount_amount`; recomputes `line_subtotal` / `grand_total` atomically
- Stamps `ordered_at`, `ordered_by`, `ordered_by_name`, `supplier_ref`, `expected_delivery_date`, `ordered_notes`
- Flips `po_type` from "draft" to "terms" so it joins the normal PO payment pipeline
- Updates the linked stock-request item statuses → `ordered`
- Fires SMS to requesting-branch admins + branch managers using new template key `phantom_po_ordered` (variables: po_number, vendor, request_number, grand_total, delivery_date, branch_name, company_name)

**Frontend** (`pages/StockRequestsPage.js`):
- Each DRAFT phantom PO on the request detail dialog gets a teal "Mark Ordered" button (only visible to the supplying branch)
- Inline expandable form: supplier reference, expected delivery date, notes, branch PIN
- After confirmation, the row collapses + shows footnote: "Ordered by Manager · ref SUP-INV-2026-0042 · ETA 2026-02-20"
- `get_request` endpoint now exposes `ordered_by_name`, `supplier_ref`, `expected_delivery_date`, `ordered_notes` for the FE

**Tests** (`test_br_stock_request_mark_ordered.py` — 6 scenarios, all green):
1. Happy path: draft → ordered, all metadata stamped
2. Per-item price override: 2 lines × 2 qty @ ₱100 → grand_total ₱400 ; override line 1 to ₱150 → recomputed to ₱500
3. Cannot mark-ordered a PO already in `ordered` status (refused)
4. PIN required; invalid PIN → 403
5. Cross-request guard: passing PO from a different request → 400
6. SMS notification: `queue_sms` invoked with template `phantom_po_ordered`, requesting-branch manager included as recipient

**Verification**: 232/232 BR tests passing (was 226 + 6 new). Lint clean on both backend and frontend. Smoke screenshot confirms page renders.




## Feb 15 2026 — Stock Requests (multi-doc triage) 🟢 P0 FEATURE

**User pain**: When Branch A requests stock from Branch B, B often has only part of the items and a supplier for the rest. No clean way to fan out one request into multiple downstream docs (BTO + POs) without juggling forms.

**Design** (Option-4 routing, validated with the user across a long discussion thread):
- New top-level **Stock Requests** page sits above Branch Transfer in sidebar.
- Branch A drafts a request (multi-line, with product search showing BOTH branches' on-hand qty so they know what's actually available).
- Branch B opens it, triages each line: **🚚 Personal Transfer** / **📦 Order from Supplier** / **❌ Cannot fulfill**.
- One "Generate Fulfillment Plan" click + one PIN → system spawns:
  - 1 BTO with all transfer lines coalesced (uses existing branch-transfer flow)
  - 1 DRAFT PO per supplier (lines grouped) on Branch A's books, with `phantom_for_branch_id = supplying_branch_id` so Branch B sees them in their list as phantoms (no inventory, no AP)
- Supplier directory used: requesting branch's (PO lives there). Quick-create writes to that directory; reusing a supplier name on a second triage **reuses** the existing doc.
- Cancel cascades to DRAFT POs only — `ordered`+ POs require manual unwind.

**Backend** (`routes/stock_requests.py`):
- `POST/GET /api/stock-requests` (create + list with role=requesting/supplying filters)
- `GET /api/stock-requests/{id}` (hydrates linked BTO + POs)
- `POST /api/stock-requests/{id}/send` (draft → sent)
- `POST /api/stock-requests/{id}/triage` (the heart — PIN-gated, atomic spawn)
- `POST /api/stock-requests/{id}/cancel` (cascades to DRAFT POs)
- `GET /api/stock-requests/products-lookup` (search w/ both branches' inventory)

**Frontend**:
- `pages/StockRequestsPage.js` — new page with Incoming/My Requests/New tabs, triage dialog, supplier picker w/ quick-create
- `components/Layout.js` — sidebar entry above Branch Transfer (under "Branches" section)
- `components/QuickLaunch.js` — new teal "Stock Request" tile on Dashboard Quick Launch
- `pages/PurchaseOrderPage.js` — DRAFT POs from stock requests show a `↩ SR-…` badge next to vendor name on the PO list

**Tests** (`test_br_stock_request_triage.py` — 8 scenarios, all green):
1. Pure transfer triage → 1 BTO, 0 POs
2. 2 suppliers / 3 lines → 0 BTO, 2 POs correctly grouped
3. Mixed (1 transfer + 1 supplier + 1 unfulfilled) → 1 BTO + 1 PO + variance log
4. Quick-create supplier writes to requesting branch's DB; re-using same name reuses doc
5. Phantom flag + source linkage stamped on both BTO and POs
6. Cancel cascades to DRAFT POs but preserves `ordered` ones
7. Re-triage blocked once fulfillment_generated
8. products-lookup returns both branches' inventory in one response

**Verification**: 226/226 BR tests passing (was 218 + 8 new). Testing agent confirmed all backend endpoints work end-to-end via live API curl tests (6/6 pass). Frontend page renders correctly when the user's plan includes `branch_transfers` (shared feature flag — intentional, since Stock Requests *requires* BTO + PO downstream pipelines).



## Feb 15 2026 — PO Capital: Overall-Discount Proration + Backfill 🔴 P0
**User context**: "On our PO we have an overall discount separate from the per-line discount. Also our supplier does 10+1 free." Confirmed that the prior Feb-2026 fix only handled per-line discount; **overall PO discount still only hit AP**, leaving each line's `cost_price` stamped at the pre-overall-discount price. Inflated COGS reappeared whenever a header-level discount was used.

**Fix** (`routes/purchase_orders.py`):
- New pure helper `_effective_unit_cost(item, line_subtotal, overall_disc_amt)` — single source of truth for cost math. Per-value (proportional) allocation: each line absorbs `line_total / line_subtotal × overall_disc_amt`. GAAP landed-cost allocation; never drives cost_price negative on mixed-value baskets.
- `_apply_po_inventory` + `get_capital_preview` both call the helper → preview/apply agreement guaranteed.
- Preview response now exposes the full breakdown: `unit_price`, `line_discount`, `overall_disc_share`, `effective_total` (per item) + `overall_discount_amount`, `line_subtotal` (per PO).
- New admin endpoint `POST /api/purchase-orders/backfill-overall-discount-capital` — walks past received POs with overall_discount > 0 and corrects historical `branch_prices.cost_price` via the same proration. Supports `dry_run=true`. Idempotent.

**Frontend** (`pages/PurchaseOrderPage.js`):
- Smart Capital dialog now shows a sky-blue banner when an overall discount is present, explaining how it's distributed.
- Each item row gets an inline cost-build breakdown beneath the New PO Price: `₱unit − ₱line_disc/pc − ₱share` so managers see exactly where every peso landed.

**Tests** (`test_br_po_capital_overall_discount.py` — 7 scenarios):
1. Mixed-value basket (₱10,000 premium + ₱1,000 cheap, ₱1,100 overall disc) → premium=9000, cheap=90.
2. Combined per-line + overall (10 × 100 less ₱10/pc less ₱100 overall) → 80.
3. "10+1 free" encoded as 2 lines → moving-average = ₱90.91.
4. "10+1 free" + per-line disc → moving-average = ₱81.82.
5. capital-preview ≡ apply (preview returns same `effective_unit` + exposes `overall_disc_share`).
6. Backfill endpoint corrects pre-fix rows then is idempotent on re-run.
7. Helper pure-math contract test (locks formula).

**218 BR tests passing** (was 207 + 11 new = 218 ✓). Lint clean. Full BR suite no longer shows the previously-flaky `test_br_payables_includes_historical` errors on suite runs.




## Feb 14 2026 — Closing Wizard (Group / Batch) Audit + Fix 🔴 P0
**User report**: Batch-closing ~10 days of ₱1.5M cash sales showed `expected_counter = 0`. Single-day close worked fine.

**Audit — 3 independent defects all feeding the same wrong number**:

1. **Anchor lookup mismatch** (the headline bug). Single-day preview used `find_one({"date": {"$lt": target_date}}, sort=[("date", -1)])` — finds ANY most-recent closed day. Batch preview AND batch POST used `find_one({"date": first_date - 1 day})` — exact match only. Group-close exists *specifically* because the user skipped close-days, so the exact-day lookup almost always missed → `starting_float` fell through to `wallet.balance` (which on the reporter's setup had drifted to 0).

2. **`expected_counter` computed but never returned** from `/daily-close-preview/batch`. The frontend silently fell back to 0.

3. **Partial-sale double-count** in batch POST. The `cash_sales_agg` query was missing `"partial_grand_total": {"$exists": False}` filter that single-day and preview paths used. Partial-sale lines were counted both in `total_cash_sales` AND in `total_cash_ar` (via the payments aggregation) — inflating the recorded closing on every batch with partial-paid sales.

**Fix** (`routes/daily_operations.py`):
- Both batch preview + POST now use `find_one({"branch_id": …, "date": {"$lt": first_date}, "status": "closed"}, sort=[("date", -1)])` — mirrors single-day.
- Batch preview returns `expected_counter` + `has_prev_close`.
- Batch POST `expected_counter` formula now respects `has_prev_close` (formula when anchor exists, `wallet.balance` for genuine first-ever close).
- Batch POST `cash_sales_agg` excludes `partial_grand_total` rows.

**Tests**: `test_br_close_wizard_batch.py` — 4 new BR tests:
1. Skipped-days anchor test: reproduces the user's exact "₱1.5M sales / expected=0" scenario; anchor falls back to the close 5 days before the window, formula produces ₱1,505,000.
2. Parity test: batch-with-1-day = single-day preview (same `starting_float`, `expected_counter`, `total_cash_sales`).
3. First-ever-close fallback: no prior close → `wallet.balance` used (preserves correctness for fresh-branch setup).
4. Partial-sale double-count regression: partial-paid invoices excluded from `total_cash_sales`.

**207/207 BR suite passing** (up from 203). Lint clean.



## Feb 14 2026 — Terminal-Only Lock-down on Sensitive Write Actions 🔒
**Threat closed**: previously, the terminal-action UI was hidden for non-terminal scans, but the **backend endpoints accepted any authenticated admin token** — meaning a stolen admin JWT or a direct curl/Postman call could bypass the UI gate and execute payments, refunds, returns, and stock corrections. Camera-only QR scans also exposed `WebPaymentSection` with TOTP/staff-login fallback, an unintended cash-recording surface.

**4 endpoints locked to paired-terminal sessions only**:
- `POST /api/qr-actions/{code}/receive_payment` — dropped Path 2 (web staff JWT) and Path 3 (TOTP-only). **Path 1 (terminal + PIN) is the only allowed flow.**
- `POST /api/invoices/{id}/correct-incomplete-stock` — now requires `terminal_id` + `device_id`.
- `POST /api/returns` — same.
- `POST /api/invoices/{id}/send-pickup-sms` — same.

**How**: new `utils/terminal_guard.py::require_terminal_session` FastAPI dependency that reads credentials from `X-Terminal-Id` / `X-Device-Id` headers (preferred) or JSON body, then defers to the canonical `_verify_terminal_session()` for active-session + device-binding checks. Returns 403 with: *"This action is restricted to the AgriBooks terminal app. Web/camera-scan access is not permitted."*

**Frontend** (`DocViewerPage.jsx`):
- Removed `WebPaymentSection` rendering for non-terminal scans.
- Added elegant amber-gradient banner showing the QR code prominently with a "Copy code to clipboard" affordance + instructions to open in the AgriBooks Terminal app.
- All terminal modals (`TerminalUpdateReceiptModal`, `TerminalReturnRefundModal`, `PickupSmsButton`) now pass `terminal_id` + `device_id` so the gate is satisfied transparently.

**Read paths preserved** ✅: viewing the receipt, payment history (with timestamps), balance, and attached docs still works from any camera scan after Tier 2 PIN unlock. Only **write/financial actions** require the paired app.

**Tests**: `test_br_terminal_guard.py` (6 new BR tests): no-creds rejection, header creds pass, body creds pass, unknown terminal_id, device-binding mismatch, header-precedence-over-body. **201/201 BR suite passing** (up from 195).



## Feb 14 2026 — Pickup-Ready SMS (manual, rate-limited) ✅
**Use case**: Customer pays upfront for an order they'll collect later ("ipreparé na lang, kukunin namin mamaya"). When the cashier finishes physically preparing the order, they tap a new **"Send Pickup Ready SMS"** button on the terminal to notify the customer.

**Surfaces in**: `DocViewerPage` → Terminal Actions → new "PICKUP NOTIFICATIONS" section, visible on every invoice with a linked customer.

**Rate limits (server-enforced)**:
- Max **3 sends per invoice** (lifetime).
- **5-min cooldown** between sends — UI shows live `m:ss` countdown.
- Manual trigger only (`trigger="manual"`).
- Confirmation modal "Are you sure the items are ready?" prevents accidental taps.

**Implementation**:
- New `routes/pickup_sms.py` → `POST /api/invoices/{id}/send-pickup-sms` + `GET /api/invoices/{id}/pickup-sms-status`. 429 with `retry_after_seconds` on cooldown, 429 with `retry_after_seconds=null` on lifetime cap, 400 on missing customer/phone.
- New SMS template `pickup_ready` (Filipino + English mix).
- Counters stored on `invoices.pickup_sms_count`, `pickup_sms_last_sent_at`, `pickup_sms_history[]` (audit trail with who sent + which phones + index).
- `components/PickupSmsButton.jsx` — self-hides for walk-in invoices, hydrates state on mount, shows AlertDialog for confirmation, live countdown ticker.

**Tests**: `test_br_pickup_sms.py` (6 new BR tests covering: first send, cooldown block, lifetime cap, cooldown expiry, walk-in rejection, status hydration). **195/195 BR suite passing**.



## Feb 14 2026 — Phantom-Balance Fix on Incomplete-Stock Corrections 🔴 P0
**Pain point**: Live customer (Sibugay Agrivet) reported SI-MB-001059 (AIZON AGRIVET, ₱185,245 cash sale) showed a phantom ₱1,315 balance after an Incomplete-Stock correction. SMS also told the customer "we received a payment from you" when actually they were being **refunded**.

**Root cause**: Two independent bugs:
1. `invoice_corrections.py` computed corrected line totals as `actual_qty * rate`, silently dropping per-line discounts. For Aizon: 4 discounted lines had aggregate discount of ₱325+650+300+40=**₱1,315** — exactly the phantom balance. Cash refund (₱4,490) correctly debited the drawer but `grand_total` only fell by ₱3,175 (no discount applied to new totals), leaving ₱1,315 AR the customer never owed.
2. Same endpoint reused `on_payment_received` SMS hook with the `payment_received` template, misleadingly telling customers "Salamat, natanggap namin ang P[refund] mo" when money was leaving the store.

**Fixes** (`/app/backend/routes/invoice_corrections.py`):
- Compute new line totals as `actual_qty * rate - prorated_disc` where `prorated_disc = (orig_disc / orig_qty) * actual_qty`. Refund now equals actual line-total drop (`orig_total - new_total`).
- New SMS hook `on_stock_correction_refunded` + new template `stock_correction_refund` with clear refund-style wording: *"Hi [name], na-correct po namin ang invoice [#] dahil hindi naibigay ang ilang items. Refund: cash P[X]. Inilapat sa balance: P[Y]. Remaining balance: P[Z]."* Composes adaptively based on the refund_allocator routing.
- New admin-only `POST /api/invoices/{id}/repair-correction-balance` endpoint to retroactively fix the 1 affected invoice (and any future stragglers): re-applies prorated discounts to corrected line totals, recomputes grand_total + balance + status, decrements customer.balance by the recovered drift. Idempotent.

**Tests**: `test_br_correct_incomplete_line_discount.py` (3 new BR tests) — single-discount-line, mixed Aizon-style repro, no-discount regression. **189/189 BR suite passing** (up from 186).

**User next step**: Deploy via "Save to Github" → then call the repair endpoint with their admin token to fix SI-MB-001059:
```
POST /api/invoices/1762bf1e-4fa6-40e7-894c-eea2d6d3911c/repair-correction-balance
```



## Feb 13 2026 — Dot Matrix Receipt: Visibility Bump (×1.5) ✅

**Pain point**: The Feb-2026 density pass made the dot-matrix receipt fit ~15 lines on one page but the trade-off was that product names, prices, qty, line totals, subtotals, and customer name became hard to read at a glance on carbon copies.

**Fix**: Targeted ×1.5 scale-up on the most-read fields only — letterhead, items header, totals, and grand total — keeping the surrounding chrome (meta-table, footers, terms, QR) untouched so the receipt still fits one page.

Concretely in `frontend/src/lib/PrintEngine.js` (dot-matrix CSS block):
- `.dm-items-table td` (product name, qty, unit price, line total): **11px → 17px**
- `.dm-items-table th` (column headers): **10px → 15px**
- `.dm-items-table .dm-row-num`: **10px → 15px**
- `.dm-total-box td` (subtotals / discount / freight): **11px → 16px**
- `.dm-total-box tr.dm-tb-grand td` (grand total): **13px → 20px**
- `.dm-totals .dm-tot-row` (legacy sales totals): **11px → 16px**
- `.dm-totals .dm-tot-row.dm-grand`: **12px → 18px**
- `.dm-info-box .dm-box-name` (customer name in info box): **12px → 18px**
- `.dm-addr-band .dm-addr-val` (customer name in billing band): **10px → 15px**

Width adjustments to accommodate larger digits without wrap:
- `.dm-total-box` width: 280px → 320px (label/val min-widths 120/110 → 140/140).
- `.dm-totals` label/val min-widths: 120px/110px → 160px/140px.

Everything else (header letterhead, page footer, sig blocks, trust terms, QR codes) untouched. Lint-clean. No backend changes; BR suite still 186/186.

## Feb 13 2026 — Inline Terms Picker on Credit / Partial Checkout ✅

**Pain point**: Users were forgetting to set "Terms" in the order header before checking out, so credit sales shipped with `terms="COD" / terms_days=0` and the wrong due_date got printed on the receipt.

**Fix**: New `TermsPicker.jsx` component injected directly into `CheckoutDialog` when the user picks **Credit** or **Partial** payment type. Renders the same terms list returned by `GET /api/settings/terms-options` (COD / Net 7 / 15 / 30 / 45 / 60 / 90) as one-tap chips, plus a **Custom** chip that reveals a day-count input (`Custom Net N Days`). A live due-date preview (`order_date + days`, localized PH date) sits at the bottom so the cashier sees the date the receipt will print with.

- **NEW** `frontend/src/components/TermsPicker.jsx` (~140 LOC).
- **MOD** `frontend/src/components/CheckoutDialog.jsx` — new props (`terms`, `termsDays`, `termsOptions`, `transactionDate`, `onTermsChange`); picker mounted inside both the `paymentType === 'partial'` and `paymentType === 'credit'` branches when a customer is selected. Both new props are OPTIONAL — old call-sites and tests keep working with no change.
- **MOD** `frontend/src/pages/UnifiedSalesPage.js` — passes `header.terms`, `header.terms_days`, the existing `terms` (options) array, `header.order_date`, and a setter callback that updates header.terms + header.terms_days in one shot.
- Lint-clean. BR suite still 186 passed / 0 failed.

## Feb 13 2026 — Hot Fix: Tenant Scoping on Invoice Corrections ✅

**Bug found** during live-site audit (agri-books.com) on credit invoice `SI-MB-001001`. Curl `POST /api/invoices/{id}/correct-incomplete-stock` returned `{"detail":"Invoice not found"}` even though the invoice exists and the JWT was valid.

**Root cause**: `correct_incomplete_stock` never declared `Depends(get_current_user)`. Without that dep, `set_org_context()` is never called for the request → `db.invoices` (a `TenantCollection` proxy) sees an empty org context → fail-closed → "not found". The route only worked in practice when a prior request on the same uvicorn worker happened to leave its context behind (latent context-bleed; non-deterministic).

**Fix**:
- `backend/routes/invoice_corrections.py` — added `user: dict = Depends(get_current_user)` to the signature. Audit fields `corrected_by_id` / `corrected_by_name` now reflect the real authenticated user (previously hardcoded `"terminal"`).
- BR test invocations now pass `user=tenant["users"]["owner"]` (8 call-sites in `test_br_correct_incomplete_payment_aware.py`).
- BR suite still **186 passed / 0 fail** after the change (453 structured rows).

**Why credit failed visibly when cash didn't (on prod)**: When the route DID happen to find an invoice (worker had bleed-over context), the OLD code path then unconditionally did `update_cashier_wallet(-refund_amount, allow_negative=False)`. For credit invoices, cashier never received that money, so the wallet had insufficient balance → HTTP 400 → "Correction failed on credit". Cash invoices coincidentally had the wallet topped from the original sale so the same code path succeeded. The payment-aware allocator (already merged today) plus this auth fix together resolve both failure modes.

## Feb 13 2026 — Closed-Day Correction Policy: Always Allow ✅

**Decision (owner)**: Both "Update for Incomplete Stock" and "Return & Refund" are allowed on closed days. Audit trail rests on **three pillars** that fire regardless of day status:
1. **Inventory `+qty` reversal** (unconditional). Even if the product row didn't exist, an upsert creates it. Count-sheets will catch any physical-vs-system gap and route the discrepancy to the owner for investigation.
2. **Today-dated `wallet_movements`** for the cash + digital legs. The closed Z-report is never re-touched.
3. **Today-dated `expenses` row** ("Customer Return Refund") for the cash portion.

**Changes**
- `backend/routes/invoice_corrections.py` — removed the `day_closed_cash_refund` HTTP 400 gate. Replaced with an explanatory comment block citing the owner decision.
- `frontend/src/components/TerminalUpdateReceiptModal.jsx` — removed the special-cased toast for `day_closed_cash_refund`. Falls back to the generic correction error.
- `frontend/src/pages/DocViewerPage.jsx` — removed the closed-day notice strip.
- BR test `test_pa_correct_7_day_closed_cash_now_allowed` flipped from rejection to success expectation.
- BR test `test_pa_correct_8_inventory_reversal_always_applies` added — proves inventory upsert + `inventory_movements` log fire even when the product row didn't exist beforehand.
- **BR suite**: 186 passed (was 185 → +1 net after flipping test 7's intent and adding test 8). 453 structured rows pass / 0 fail.

## Feb 13 2026 — Payment-Aware Refund Engine ✅

**Goal**: Stop cashier wallet over-debits when correcting / returning credit, digital, or split-payment invoices. Old code unconditionally debited cashier for refund_amount even though digital/credit/split sales never put that cash in the drawer.

### What was delivered
- **NEW** `backend/utils/refund_allocator.py` — `compute_refund_allocation(invoice, refund_amount)` routes refunds in priority order: **AR → digital channels → cash** (newest payment first for digital). Cash is capped at `summary.cash_paid` so we never debit more than was paid. Surfaces `remaining_unallocated` when refund exceeds grand_total.
- **MOD** `backend/routes/invoice_corrections.py` — `correct_incomplete_stock`:
  - Uses the allocator. Conditional `update_cashier_wallet` (only when `cash_refund > 0`), per-channel `update_digital_wallet`, AR-side reduces `invoice.balance` + `customer.balance` only by the AR portion (kills the previous double-debit).
  - Day-closed gate: **AR-only / digital-only** corrections are now ALLOWED on closed days. Cash refund on closed day raises HTTP 400 with `{type: "day_closed_cash_refund"}`.
  - Subsequent payment rows linked to refunded digital amounts are marked `voided: true`.
  - Response includes `refund_allocation` breakdown + `original_payment_summary` for audit.
- **MOD** `backend/routes/returns.py` — `create_return` & `void_return`:
  - Adds `payment_aware` boolean (default `true` when `invoice_number` is provided).
  - Looks up linked invoice, runs allocator, disburses across digital channels + cash residual.
  - Cashier fund check now uses `cash_refund` (not full retail), unblocking refund-from-cashier-with-zero-balance for credit returns.
  - `void_return` reverses digital wallets in lockstep with cash.
  - Persists `refund_allocation`, `cash_refund_disbursed`, `digital_refund_disbursed` in the `returns` collection.
- **NEW** `frontend/src/lib/refundAllocator.js` — JS mirror of the backend allocator for live FE preview.
- **NEW** `frontend/src/components/RefundAllocationPreview.jsx` — visual routing preview (AR row + per-channel digital rows + cash row + unallocated row). Reusable.
- **MOD** `frontend/src/components/TerminalUpdateReceiptModal.jsx` — preview rendered on step 1 (next to refund summary) AND step 2 (next to PIN). Day-closed-cash-refund error surfaced with a clear toast + inline message.
- **MOD** `frontend/src/components/TerminalReturnRefundModal.jsx` — preview rendered on step 2 (configure) AND step 3 (PIN).
- **MOD** `frontend/src/pages/DocViewerPage.jsx` — removed the `!dayIsClosed` gate that hid Update-for-Incomplete-Stock; backend now handles the gate per-allocation.

### Test coverage
- **NEW** `tests/business_regression/test_br_refund_allocator.py` — 12 pure-math unit tests (pure cash, pure credit, partial credit, pure digital, split within digital, split overshoot, subsequent digital AR payment, drained digital then cash, voided payments ignored, overshoot grand_total, split summary breakdown, zero refund inert).
- **NEW** `tests/business_regression/test_br_correct_incomplete_payment_aware.py` — 7 integration tests through the route handler (cash baseline, credit unpaid no cashier debit, digital routes to digital wallet, split overshoot, partial credit AR-then-cash, day-closed AR-only allowed, day-closed cash rejected).
- **NEW** `tests/business_regression/test_br_returns_payment_aware.py` — 5 integration tests through `/returns` (cash walk-in baseline, credit unpaid AR-only, digital routes to digital, split within digital, split overshoot to cash residual).
- **BR suite total**: 185 passed (was 161 → +24 new tests). 452 structured rows pass / 0 fail.

### Business impact
- Credit invoices: corrections/refunds no longer create phantom cashier shortages. AR is the only thing that moves when the customer hasn't paid yet.
- Digital invoices: refunds route to the original digital channel + ref number → matches physical-world reversal trail.
- Split invoices: digital channels are reversed before cash is touched → preserves end-of-day cash position.
- Day-closed AR-only corrections are unblocked, eliminating the "I forgot but the Z-report is closed" workaround.




## Feb 2026 — Phase 4 Cleanup, useHistoricalCredit() Extraction Pass

**Goal**: Extract the Historical Credit / Notebook AR feature state, trigger flags, and helpers out of `UnifiedSalesPage.js` into a reusable hook. Plus a tiny prerequisite cleanup: replace the page-local `localToday()` duplicate with the shared `lib/dateFormat.js#localTodayStr`.

### Files changed
- **NEW** `frontend/src/lib/useHistoricalCredit.js` (~240 lines, ~150 lines of business logic + 90 lines of comments / type docs).
- **NEW** `frontend/src/lib/useHistoricalCredit.test.js` — 14 focused hook tests, all green.
- **MOD** `frontend/src/pages/UnifiedSalesPage.js` — imports the hook + the shared `localTodayStr`, removed the page-local `localToday()` definition, removed the 12 HC state declarations, the 2 HC trigger memos, and the 4 HC helpers (`buildHistoricalCreditItems`, `buildHistoricalCreditPayload`, `previewHistoricalCredit`, `commitHistoricalCredit`). Replaced 9 `localToday()` call-sites with `localTodayStr()`. Wired the new `hc = useHistoricalCredit({...})` call with explicit `getItems`/`getContext`/`hasCustomer` getters and a single `onCommitted` callback. Updated all banner + dialog JSX consumer sites to read from `hc.*`. Net: ~210 lines removed from UnifiedSalesPage.js.

### Prerequisite micro-cleanup
- Replaced the page-local `localToday()` helper (which was a near-duplicate of `lib/dateFormat.js#localTodayStr` — same `agribooks.org_tz` localStorage read, same Manila fallback, same YYYY-MM-DD output) with the shared utility. 9 call-sites migrated. `lib/nextOpenDate.js` untouched (out of scope).

### State moved into the hook (12 state fields)
`reason`, `proofUrl`, `notebookRef`, `allowInv`, `approvalCode`, `dialogOpen`, `preview`, `previewLoading`, `previewError`, `committing`, `commitError`, plus the constant `HISTORICAL_CREDIT_FLOOR_DAYS = 7` and the memoised flags `isMode` (= `isHistoricalCreditMode`) + `isBackdatedNonCreditBlocked`.

### Functions moved into the hook (4 helpers + 3 new dialog conveniences)
- `buildItems` (mode-aware cart→items mapper)
- `buildPayload` (preview/commit-shared, approval-code-aware)
- `runPreview` (was `previewHistoricalCredit`)
- `commit` (was `commitHistoricalCredit`)
- **New**: `openDialog()` — clears stale preview/error/code and opens; preserves the user-typed reason/proof/notebook so a re-open doesn't lose them.
- **New**: `closeDialog()` — symmetric cleanup on cancel.
- **New**: `reset()` — convenience for tests.

### What intentionally remained in `UnifiedSalesPage.js`
- `isPrivilegedRole` — used by `minAllowedDate` (date-picker widening), not only HC. Passed INTO the hook as an option.
- `daysBack` — used by both HC and the existing date-error banner. Passed IN as an option.
- All cart / lines / customer / branch / header / totals ownership.
- `clearCart`, `setHeader`, `setPendingCreditSale`, `setCheckoutDialog` setters — invoked through `onCommitted` so the page stays in full control.
- All HC JSX (banner with reason/proof/notebook inputs + the commit dialog) — read from `hc.*` for now; presentational extraction is a separate future pass.

### onCommitted wiring
```js
onCommitted: useCallback(() => {
  clearCart();
  setHeader(h => ({ ...h, order_date: localTodayStr() }));
  setPendingCreditSale(null);
  setCheckoutDialog(false);
}, []),
```
Single source of truth for post-commit page cleanup. Hook does NOT touch any of these directly. Errors thrown from `onCommitted` are swallowed inside the hook so the cashier still sees the green toast + reset HC state.

### Tests added / results
- Frontend unit: **37/37 PASS** (17 connectivity + 6 useConnectivity + 14 useHistoricalCredit). Run with `cd /app/frontend && CI=true yarn test --watchAll=false --testPathPattern="connectivity|useConnectivity|useHistoricalCredit"`.
- Backend regression: **28/28 PASS** (Phase 3 + 4A unit).
- Live smoke on deployed preview: page mounts, `connectivity-status[data-status="online"]`, no console errors, no React `pageerror`s, both Quick Sale + Detailed Sale tabs render correctly, Sale Date picker and customer search work, dedupe pill still in bottom-left (Pass 0 fix preserved).
- ESLint clean.

### Behavior change?
None. Same Historical Credit trigger conditions (daysBack > 7 AND credit AND privileged role), same backdated-non-credit block, same approval-code rules, same Owner/Admin TOTP gate (server-side), same `use_regular_late_encode` routing for 1–7 day credit, same 0–7 day late-encode flow via `LateEncodeDialog`, same post-commit cleanup. Both Quick Sale mode and Detailed Sale / Order Mode remain inside the same `UnifiedSalesPage`. POS Terminal untouched. Offline sync untouched. Reports untouched. Pending-sync pill rules untouched.

### Duplicate / overlap discovered during implementation
- The previous near-duplicate `localToday()` was eliminated (replaced with shared `localTodayStr`). `lib/nextOpenDate.js` still has its own copy — flagged for a future tiny cleanup.
- No other duplicates introduced. The hook composes existing leaf modules (`api`, `invalidateBalanceCache`, `localTodayStr`) without overlap.

### Pass landed cleanly?
Yes. Lint green, all unit tests green, all backend regression green, live page renders without errors, both sales modes functional, dedupe-pill/branch-guard pre-fixes from Pass 0 preserved.




## Feb 2026 — Phase 4 Cleanup, Pass 0 + Pass 1

**Goal**: Make the UnifiedSalesPage codebase ready for the eventual HeldSalesQueue (Phase 4A.2) by relocating connectivity behavior into a reusable hook, plus two tiny UX fixes that unblock E2E testing without weakening any business rule.

### Pass 0 — Pre-fixes
1. **Dedupe-pill repositioned** (`frontend/src/components/CustomerDedupeManager.js`). Was `fixed bottom-5 right-5 z-[60]`, which sat ON TOP of the cart's `[data-testid='checkout-btn']` on the right rail and blocked testing-agent clicks. Moved to `fixed bottom-5 left-72 z-[60]`. Same z-index, same visibility, no behaviour change — just out of the cart's click area.
2. **All-Branches checkout guard — clearer, NOT weaker** (`frontend/src/pages/UnifiedSalesPage.js`). Business rule preserved: a sale still cannot be checked out under "All Branches" scope. Improvements:
   - Both Checkout buttons (Quick and Order modes) are now `disabled={cart.length === 0 || !currentBranch?.id}` — the precondition is visible before the click.
   - Button label switches to `"Select a branch to checkout"` when no branch is selected.
   - `title` hover tooltip explains: "A sale must belong to a specific branch. Open the branch picker in the top header and pick one."
   - The fallback `openCheckout` toast carries `id: 'checkout-blocked-no-branch'` so E2E tests can match it deterministically. Message rewritten: "Select a specific branch to checkout. Open the branch picker in the top header and pick one — 'All Branches' is for browsing only."

### Pass 1 — `useConnectivity()` hook extracted
- NEW `frontend/src/lib/useConnectivity.js` (~110 lines). Reuses (does NOT duplicate) the Phase 4A.1 `connectivity.js` helpers and the existing `syncManager` / `offlineDB` modules. Owns:
  - `isOnline`, `connectivityStatus` (`'online' | 'reconnecting' | 'offline'`), `reconnectCountdown`, `pendingCount`.
  - The `goOnline` / `goOffline` window listeners (incl. `toast.success('Back online! Syncing...')`, auto-sync, and the post-sync `onReconnect` callback).
  - The 30s `/api/health` background reachability probe (skipped while `reconnectCountdownRef.current > 0` to avoid colliding with the processSale retry loop).
  - `setConnectivityStatus` and `setReconnectCountdown` setters exposed for the processSale retry-loop integration (escape hatch preserved).
  - `refreshPendingCount()` helper.
- NEW `frontend/src/lib/useConnectivity.test.js` — 6 hook tests via `@testing-library/react`'s `renderHook` + `act`. Covers initial state, setter wiring, browser `offline` event, browser `online` event with `onReconnect` callback, `refreshPendingCount`, and cleanup.
- MOD `frontend/src/pages/UnifiedSalesPage.js`:
  - Imports the new hook.
  - Replaced ~12 lines of inline state (`isOnline`/`connectivityStatus`/`reconnectCountdown`/`reconnectCountdownRef`/`pendingCount`) with a single `useConnectivity({ onReconnect: onReconnectLoadData })` call.
  - Removed the entire 50-line inline `useEffect` that owned the window listeners + 30s probe + auto-sync (now in the hook).
  - Added a tiny mount-once `useEffect` that binds the existing `loadData` function into a ref so the hook can call it after a successful reconnect-sync (preserves the existing post-reconnect product-grid refresh).
  - Net: ~60 lines removed from UnifiedSalesPage.js; behavior identical.
- Reusable pieces reused (no duplicates created):
  - `lib/connectivity.js` (`isTrueNetworkError`, `pingBackendHealth`)
  - `lib/syncManager.js` (`syncPendingSales`, `startAutoSync`, `stopAutoSync`)
  - `lib/offlineDB.js` (`getPendingSaleCount`)
- Dev dep added: `@testing-library/react@^16` + `@testing-library/dom` (no other deps changed).

### What remains inside UnifiedSalesPage.js (deliberately)
- All cart / lines / header / customer / branch / scheme state.
- All Phase 4A historical-credit state and helpers (deferred to a future pass).
- `processSale` (475 lines) and its retry loop — uses the hook's exposed `setConnectivityStatus` and `setReconnectCountdown` setters as before.
- The 3-state connectivity indicator JSX + pending-sync pill JSX — they READ from the hook but render inline in the header. Extracting them is presentational-only and not required for Phase 4A.2.

### Behavior change?
None. Same online/reconnecting/offline indicator, same 10s reconnect grace, same pending-sync pill rules, same auto-sync, same `loadData(true)` after reconnect, same Phase 4A historical-credit flow, same 0–7 day late-encode flow, same Quick + Detailed Sale modes inside UnifiedSalesPage. POS Terminal untouched.

### Tests / Checks
- Frontend unit: **23/23 PASS** (17 existing `connectivity.test.js` + 6 new `useConnectivity.test.js`). Run with `cd /app/frontend && CI=true yarn test --watchAll=false --testPathPattern="connectivity|useConnectivity"`.
- Backend regression: **28/28 PASS** (Phase 3 + 4A unit). Phase 4A.1 unaffected.
- Live smoke on deployed preview:
  - `[data-testid='unified-sales-page']` present ✅
  - `[data-testid='connectivity-status'][data-status='online']` present ✅
  - `[data-testid='checkout-btn']` shows label `"Select a branch to checkout"` and is `disabled` while no branch is selected ✅ — proves Pass 0 #2 (clearer, rule preserved).
  - `[data-testid='dedupe-pill-wrapper']` computed `left: 288px` (was right-side) ✅ — proves Pass 0 #1 fix.
- ESLint clean.

### Duplicate / overlap discovered during implementation
- None. The hook composes existing leaf modules without overlap. `OfflineIndicator` (sidebar prefetch) remains a distinct concern from the in-page 3-state pill — confirmed during the assessment, validated during implementation.




## Feb 2026 — Phase 4A.1.1: Read-only "Waiting to sync" reassurance pill

**Goal**: Make the offline-queue size visible at a glance instead of hidden inside a tiny secondary badge on the connectivity indicator.

**Files changed**:
- MOD `frontend/src/pages/UnifiedSalesPage.js` — added `Clock` to lucide imports; added new `[data-testid='pending-sync-pill']` block right after the connectivity indicator. Visible only when `pendingCount > 0`. Plain amber pill with text `"N sale(s) waiting to sync"`. Read-only — no click target. Title attribute explains: "These sales are saved on this device and will sync automatically once the server is reachable."

**Where the count comes from**: existing `pendingCount` React state (line 691). Source of truth = `getPendingSaleCount()` from `frontend/src/lib/offlineDB.js`, queried:
1. On mount (line 1019).
2. Immediately after every offline save inside `processSale` (lines ~2993 and ~3020).
3. After every successful `syncPendingSales()` via `goOnline` (lines 897–898).
No new state, no new module, no new hooks.

**Updates correctly after offline save?** YES — `processSale` already calls `getPendingSaleCount().then(setPendingCount)` post-save in both the "online-then-fell-back" branch and the "offline-from-start" branch.

**Disappears after sync?** YES — `goOnline` re-queries `getPendingSaleCount()` after `syncPendingSales()`, sets `pendingCount` to the new (typically 0) value, and the conditional `{pendingCount > 0 && (...)}` removes the pill from the DOM.

**Tests / manual checks**:
- ESLint clean (`mcp_lint_javascript` pass).
- Live screenshot on the deployed preview confirms the pill is **absent** when `pendingCount=0` (testid count=0 in DOM, regular `Online` indicator visible alone).
- Phase 4A.1 17 unit tests still pass — no impact on connectivity classifier.
- Phase 3 + 4A backend regression 28/28 still pass — no backend change.




## Feb 2026 — Phase 4A.1 Surgical: Online/Offline Routing + 10s Reconnect Grace

**Goal**: Fix the reported "ONLINE but sale saved OFFLINE" bug. Real backend 4xx/5xx errors must surface the actual error and never auto-fallback. True network interruptions get a single-sale 10-second reconnect grace before falling back to offline.

**Root cause confirmed**: `UnifiedSalesPage.js:2846` `catch` block fell into `addPendingSale(saleData)` for ANY thrown exception except 422 stock / 422 jit-retail / 403 closed-day. Real validation/server errors silently disappeared into the offline queue.

**Old fallback rule** → ANY thrown exception (excluding 3 special cases) → save offline.
**New fallback rule** → ONLY transport-layer failures (`!err.response`, `ERR_NETWORK`, `ECONNABORTED`, "Failed to fetch", "Network Error") → enter 10s reconnect grace → if still unreachable → save offline. **All real HTTP responses (400/401/403/404/409/422/500) → toast the server error, keep checkout open, do NOT save offline.**

**Files changed**:
- Frontend NEW: `frontend/src/lib/connectivity.js` — `isTrueNetworkError(err)` classifier + `pingBackendHealth(timeoutMs)` lightweight reachability probe.
- Frontend NEW: `frontend/src/lib/connectivity.test.js` — 17 unit tests covering 400/401/403/404/409/422/500 NOT classified as network, and ERR_NETWORK / ECONNABORTED / "Failed to fetch" / "Network Error" / no-response classified as network. Plus 4 tests for `pingBackendHealth` against `/api/health`.
- Frontend MOD: `frontend/src/pages/UnifiedSalesPage.js`:
  1. Imports `isTrueNetworkError`, `pingBackendHealth`.
  2. New state `connectivityStatus` ('online' | 'reconnecting' | 'offline'), `reconnectCountdown`, `reconnectCountdownRef`.
  3. `goOnline`/`goOffline` window-event handlers also set `connectivityStatus`.
  4. New 30s backend-reachability probe via `setInterval` keeps the indicator accurate when the LAN is up but the upstream is down.
  5. `processSale` catch block — added two new branches BEFORE the existing offline-save fall-through:
     - Real-server-error branch: surface `e.response.data.detail` as a toast, `setSaving(false)`, return. No offline save. Cart preserved. Checkout stays open.
     - True network failure: 10-second reconnect grace, retry every 3s with health-ping gate. Success → finalize as normal online sale. Server-error during retry → surface error, no offline save. Timeout → fall through to existing offline-save block, **same `envelope_id` preserved** (Phase 2C payment idempotency dedupes a later sync).
  6. Replaced the old `Online`/`Offline` pill with a 3-state `[data-testid='connectivity-status']` indicator (Online / Reconnecting…Ns / Offline), with `data-status` attribute and `[data-testid='reconnect-countdown']` inside it during retry.
- Backend: NO changes. `/api/health` already exists at `backend/main.py:1243`. Phase 2C envelope_id dedup already in place.

**Reconnect behavior**:
- True network failure during `POST /api/unified-sale` → indicator flips to amber `Reconnecting… 10s` (animated).
- Retry every 3s, gated by `pingBackendHealth` (no useless retries when server is still down).
- Success → green toast `Connection restored — Sale {invoiceNum} processed online.`, cart cleared, checkout closed.
- Server error mid-retry → red toast with the real backend message, cart preserved, checkout stays open.
- Timeout (10s exhausted) → silently falls through to the existing offline-save path with the same `envelope_id`. No extra "Save Offline?" prompt. Existing toast `{invoice_number} saved offline (will sync later)` appears.

**Duplicate-submission protection**:
- `envelope_id` is generated once (line ~2628 `newEnvelopeId()`) and stored on `saleData`. Reused for every retry attempt and preserved into the offline IndexedDB record so the eventual sync produces the same idempotency key. Backend Phase 2C dedupes at write time.
- Confirm button's `disabled` prop already includes `saving` (line ~4533). The retry loop holds `saving=true` for the entire grace period, so a double-click during retry is impossible.
- On real server error (HTTP 4xx/5xx), `setSaving(false)` is called so the cashier can fix and resubmit — but submitting again generates a NEW `envelope_id` for what is conceptually a new attempt.

**Tests added/results**:
- Frontend unit: **17/17 PASS** (`yarn test --testPathPattern=connectivity`). Covers HTTP 400/401/403/404/409/422/500 NOT-network + 4 network-error variants + `pingBackendHealth` happy path / 5xx / throw / correct URL.
- Backend regression: **28/28 PASS** (Phase 3 + Phase 4A unit). Live smoke tests are env-gated and previously passed in iter 256.
- Live: connectivity-status indicator renders correctly with `data-status='online'` on the deployed preview.

**Historical Credit / 7-day late encode**:
- Both flows are intact. The classifier branch sits BEFORE the existing offline fall-through; it does NOT touch the special 422 stock / 422 jit-retail / 403 closed-day handlers, so the manager-PIN late-encode dialog still opens correctly. Historical Credit posts to `/api/historical-credit` via its own dedicated commit handler in this fork — never touches `processSale`.

**POS Terminal / offline sync**:
- Untouched. `addPendingSale` / `syncPendingSales` paths are unchanged. The new fall-through still produces the same offline record format with the same `envelope_id`.

**Recommended next step**:
- Phase 4 cleanup — split `UnifiedSalesPage.js` (now ~6,200 lines) into `CheckoutDialog.jsx`, `HistoricalCreditDialog.jsx`, `HistoricalCreditBanner.jsx`, `PaymentTabs.jsx`, `LateEncodeDialog.jsx`. After the split, **Phase 4A.2** can land cleanly: 30-second background held-sales queue, multi-concurrent held sales, decoupled receipt printing, "Sale B while Sale A is on hold" — implemented as a dedicated `HeldSalesQueue` module rather than crammed into the page.




## Feb 2026 — Phase 4A Frontend Integration: Historical Credit / Notebook AR Mode (Iter 256–258)

**Goal**: Integrate the Phase 3 / 4A backend Historical Credit endpoint into UnifiedSalesPage so admins/owners can encode old notebook AR entries that are >7 days back, gated by Owner/Admin TOTP server-side. Plus add a backend soft floor that forces 1–7 day backdated credit through the existing Sales late-encode path (manager PIN), reserving the heavier Historical Credit channel for older entries only.

**Backend changes**:
1. **Soft floor** in `_validate_payload` of `routes/historical_credit.py` — `transaction_date` 1..7 days back now returns `400 {error: "use_regular_late_encode", days_back, soft_floor_days: 7}`. Today and >7 days remain valid.
2. **3 new tests** in `tests/test_phase3_historical_credit.py`:
   - `test_soft_floor_rejects_dates_within_7_days` (loops 1, 3, 7)
   - `test_soft_floor_allows_dates_8_days_or_more`
   - `test_soft_floor_preview_also_rejects_within_window`
3. Existing Phase 3 + Phase 4A tests rebased to use `_days_ago(N)` with N≥8 (since 1–7 are now rejected by the soft floor); count-sheet anchor dates rebased proportionally.

**Frontend changes (UnifiedSalesPage.js)**:
1. New state: `historicalCreditPreview`, `historicalCreditReason`, `historicalCreditProofUrl`, `historicalCreditNotebookRef`, `historicalCreditAllowInv`, `historicalCreditApprovalCode`, `historicalCreditDialog`, plus loading/error flags.
2. New memos: `isPrivilegedRole`, `daysBack`, `isHistoricalCreditMode`, `isBackdatedNonCreditBlocked` — declared **after** `paymentType` state to avoid TDZ ReferenceError.
3. `minAllowedDate` widened to `floorDate` for admin/owner so they can pick dates >7 days back; cashiers still capped at 7 days.
4. Helpers: `buildHistoricalCreditItems`, `buildHistoricalCreditPayload`, `previewHistoricalCredit`, `commitHistoricalCredit`. Approval code is omitted on preview and only attached to commit. 4xx/403 responses are decoded into actionable inline errors.
5. `handleCreditSale` short-circuits to the Historical Credit dialog when `isHistoricalCreditMode`; emits a clear toast and refuses when `isBackdatedNonCreditBlocked`.
6. New banner above product grid: amber "BACKDATED CREDIT / NOTEBOOK AR MODE" with reason textarea (≥20 chars), proof URL input, notebook reference input. Red "Backdated cash/digital/split blocked" banner shown when daysBack>7 AND payment is non-credit.
7. New `<Dialog data-testid="historical-credit-dialog">` — customer-owes snapshot, count-sheet stopper warning + opt-in checkbox, closed-day note, Owner/Admin TOTP code input, commit button.
8. Checkout dialog disables `confirm-payment` when `isBackdatedNonCreditBlocked` or when in historical credit mode without a ≥20-char reason; also relabels the confirm button to "Continue → Historical Credit / Notebook AR".
9. All interactive elements carry kebab-case `data-testid`s: `historical-credit-banner`, `backdated-non-credit-block`, `historical-credit-reason-input`, `historical-credit-proof-url-input`, `historical-credit-notebook-ref-input`, `historical-credit-dialog`, `historical-credit-preview-btn`, `historical-credit-customer-owes-snapshot`, `historical-credit-count-stopper`, `historical-credit-allow-inv-checkbox`, `historical-credit-report-effect`, `historical-credit-approval-code-input`, `historical-credit-cancel-btn`, `historical-credit-commit-btn`, `historical-credit-commit-error`, `historical-credit-preview-error`.

**Files**:
- Backend MOD: `routes/historical_credit.py` (soft floor + constant `LATE_ENCODE_SOFT_FLOOR_DAYS = 7`)
- Backend MOD: `tests/test_phase3_historical_credit.py` (rebased dates + 3 new soft-floor tests; 16 total)
- Backend MOD: `tests/test_phase4a_approval_gate.py` (one date helper rebased)
- Backend NEW: `tests/test_phase4a_live_smoke.py` (testing-agent created — 6 live API smoke tests)
- Frontend MOD: `pages/UnifiedSalesPage.js` (~+300 lines for state, memos, helpers, banner JSX, dialog JSX)

**Result**:
- Backend: **34 / 34** assertions pass = 16 Phase 3 + 12 Phase 4A unit + 6 live smoke (POST /preview empty 400, POST commit empty 400, GET list 200, soft-floor 400 use_regular_late_encode, 400 approval_code_required, 403 approval_invalid, regression /unified-sale today-cash 200).
- Frontend: TDZ fix verified; **FE-1 / FE-3 / FE-9 / FE-10 / FE-11 PASS** (page mounts, banner shows correctly for non-credit backdated, banner absent for today-credit, banner absent for 5-day backdated credit, no TOTP secret/PIN hash leaks in `/api/auth/me`).
- Frontend: **FE-2 / FE-4 / FE-5 / FE-6 / FE-7 / FE-8 BLOCKED** (testing only) by a pre-existing UX precondition unrelated to Phase 4A — when scope = "All Branches", clicking the cart's `[data-testid='checkout-btn']` surfaces `Select a branch` toast and refuses to open the dialog (existing `openCheckout` guard at line 2110 of UnifiedSalesPage.js). Phase 4A code wiring is correct (every testid is mounted and the gating logic compiles cleanly under ESLint). Once a single branch is pre-selected the full flow is reachable.

**Known issue logged (not fixed in this iter)**: "Online status but sale saved offline". The `processSale` catch block at `UnifiedSalesPage.js:2846` falls into `addPendingSale(saleData)` for ANY exception except the explicit 422 stock-override / 422 jit-retail / 403 closed-day branches. So a real backend validation error (e.g., 400 / 422 / 500 from `/unified-sale`) silently flips the sale to offline despite `isOnline === true`. Recommended next bugfix is to scope the offline fallback to true network errors only (e.g., `e.code === 'ERR_NETWORK'` or no `e.response`), and surface real backend errors as toasts.



## Feb 2026 — Z-Report PDF Layout & Math Display Fixes (Iter 254)

**Goal**: Fix 4 visual/math bugs in the closing Z-Report PDF that were making the cash drawer reconciliation hard to audit.

**Bugs fixed**:
1. **"Total Cash In" subtotal removed** — the value displayed (`cash_sales + cash_ar + split_cash`) did NOT match the running sum of the rows above it (Opening Float and Fund Transfers were rendered above the subtotal but excluded from its math). The Cash Drawer Reconciliation block is now a single top-to-bottom math ledger ending in one bolded subtotal: `= Expected in Drawer (Pre-Vault)`.
2. **"Expected in Drawer (Pre-Vault)" labeled explicitly** — eliminates the impression that the value was post-vault while Over/Short used a different basis.
3. **Fund Transfer column overlap** — rebalanced widths from `65/40/35/30` to `38/52/30/50`, switched to a hand-rolled column writer (instead of `detail_row`), and bumped truncation from 25→32 chars (Authorized By) and 20→32 chars (Note). Long names like "Sibugay Agrivet Supply / J. Domunales" no longer bleed into the Amount column.
4. **Expense verification footer** — Normal layout now prints an `Itemized total (N rows)` line at the end of the Expenses section. If the rendered subtotal disagrees with `closing.total_expenses` (typical when an expense is late-encoded onto a closed day AFTER the snapshot was saved), a red warning flags the diff and direction so the auditor isn't left guessing.
5. **End-of-Day Vault Allocation** — both Normal and Detailed layouts now break "To Vault / Safe" and "Float for Tomorrow" into a clearly labeled sub-block AFTER the Over/Short calc, removing visual ambiguity about pre-vault vs post-vault math.

**Files**:
- Backend MOD: `routes/zreport_pdf.py` (~80 lines changed across `render_normal` and `render_detailed`)
- Backend NEW: `tests/test_zreport_pdf_layout_iter254.py` — 6 regression tests, all passing
  - `test_detailed_no_misleading_total_cash_in_subtotal`
  - `test_detailed_expected_minus_actual_equals_over_short`
  - `test_normal_cash_reconciliation_labels_pre_vault`
  - `test_fund_transfers_no_column_overlap`
  - `test_normal_expense_verification_footer_matches_section_total`
  - `test_normal_expense_mismatch_warning_when_stale_snapshot`

**Result**: All 6 new tests pass, pre-existing 14 zreport tests (unicode + share + net sales) still pass.



## Feb 2026 — Z-Report SMS Share Links (Iter 253)

**Goal**: One-tap mobile-friendly Z-Report viewing + Detailed PDF download from the closing SMS, no app login required (the system isn't phone-friendly per user feedback).

**Architecture**:
- Each closing SMS recipient (manager / owner / auditor) gets a unique 32-char token via `secrets.token_urlsafe(24)`. Saved in new `zreport_share_links` collection with `closing_id`, `branch_id`, `recipient_user_id`, `expires_at` (30 days), `unique_ips[]`, `revoked`.
- SMS body now ends with `View Report: https://<host>/zr/<token>` via the new `<zreport_link>` placeholder.
- Frontend public route `/zr/<token>` renders a mobile-first single-column page (4 KPI tiles, Cash Flow detail, Customer Credit, Expenses) with a sticky Download Detailed PDF button at the bottom.
- PDF endpoint reuses existing `/api/z-report-pdf` Detailed renderer; filename `Z-Report <BranchName> <YYYY-MM-DD>.pdf` (spaces preserved per user choice).
- Anomaly auto-revoke at 5 unique IPs per token (forwarding heuristic). Owner gets an alert SMS via new `zreport_share_auto_revoked` template.
- Audit Center → "Z-Report Links" tab lists all tokens with status, recipient, access count, unique-IP indicator, and a Revoke button (admin/owner only).
- All accesses logged to `zreport_share_access_log` with IP + user-agent.

**Files**:
- Backend NEW: `routes/zreport_share.py` (266 lines)
- Backend MOD: `routes/close_reminder.py` (token mint + `_resolve_public_base_url` helper), `routes/sms.py` (new placeholder + auto-revoke template + LEGACY_DEFAULT_BODIES upgrade entry), `main.py` (router registration)
- Frontend NEW: `pages/ZReportSharePage.js` (mobile viewer), `components/audit/ZReportShareLinksTab.js`
- Frontend MOD: `App.js` (public `/zr/:token` route in 2 spots), `pages/AuditCenterPage.js` (Z-Report Links tab)
- Tests NEW: `backend/tests/test_zreport_share_iter253.py` — 4 PASS (idempotent mint, IP tracking, auto-revoke at threshold, 404 on unknown)

**API endpoints**:
- `GET /api/zreport-share/{token}` (public) — view payload
- `GET /api/zreport-share/{token}/pdf` (public) — Detailed PDF download
- `GET /api/zreport-share/links/list` (manager+) — listing
- `POST /api/zreport-share/links/{token}/revoke` (admin/owner) — revoke
- `GET /api/zreport-share/links/{token}/access-log` (admin/owner) — audit trail

**Optional config**: org `settings.public_app_url` OR env `PUBLIC_APP_URL` overrides the default `https://www.agri-books.com` for the SMS-link host.

---



## Feb 2026 — Linked Offline Draft Finalization (Iter 252)

**Problem**: When a Draft Order (status=for_preparation) was finalized while offline OR when the online `/api/unified-sale` call failed mid-flight, the offline-replay path (`/api/sales/sync`) created a brand-new SI-XX-OFF-NNNNNN invoice instead of completing the existing draft. The original draft remained stuck in `for_preparation`, the customer's printed OFF receipt was orphaned from the books, and inventory/payment got recorded against the wrong invoice.

**Fix — Linked offline draft finalization with bidirectional lookup**:
- Offline envelope now carries `kind="draft_finalization_offline"` + `draft_invoice_id` + `draft_invoice_number` + `offline_receipt_number` whenever finalizing while offline / on network failure.
- New backend handler `_finalize_draft_offline` (in `sync.py`) is called when the per-sale sync loop sees `draft_invoice_id`. It locates the canonical draft, applies inventory deduction + payment + status flip in a single atomic update, and stamps `linked_offline_receipt_number` on the canonical record.
- **Option A semantics**: the canonical draft invoice number stays as the official record; the OFF number lives only as `linked_offline_receipt_number` (not in the sequence). One customer/order = one invoice in the books.
- **Idempotency**: re-syncing the same envelope returns `duplicate` — no double inventory deduction / double payment / double customer balance.
- **Doc lookup** (`/api/invoices/by-number/{n}` and `/api/view/{code}`) now resolves either the canonical number or the OFF number to the same canonical record. QR scans + manual lookups work both ways.
- **Conflict handling**: draft already paid / cancelled / not found / items diverged / payment diverged → `offline_reconciliation_queue` entry with full snapshot for manager review (NEW Audit Center → Offline Reconcile tab).
- **Recovery endpoint** for pre-fix duplicates: `POST /api/admin/reconcile-orphan-offline-draft` (admin/owner-only, dry-run by default; requires `confirm=true` + Owner PIN to execute). Reverses OFF inventory, reverses customer balance, voids OFF with audit reason, leaves draft ready for clean re-finalization.

**Frontend**:
- **Draft Orders panel**: IndexedDB overlay (`getPendingDraftCompletions()`) maps draft_id → OFF number. Drafts with a pending offline completion show yellow "Offline Completion Pending — SI-XX-OFF-NNNNNN" badge and have Open/Edit/Pay/Cancel disabled, preventing duplicate finalization.
- **PrintEngine**: orderSlipFullPage renders `OFFLINE RECEIPT — PENDING SYNC` banner before sync (with both numbers visible) and a green `SYNCED — OFFICIAL RECEIPT` banner after sync.
- **DocViewer**: linked-receipt banner shows official invoice + linked OFF number; divergence warnings displayed when items/payment diverged.
- **Audit Center**: new `Offline Reconcile` tab listing queue entries with status filter, manager-resolve action with note.

**Tests**:
- 5 new pytest tests in `/app/backend/tests/test_linked_offline_draft_finalization_iter252.py` — all PASS (happy path, idempotency, draft_already_paid conflict, draft_not_found conflict, doc-lookup resolves both numbers).
- 9/9 backend tests PASS (4 regression from Iter 251 still green).
- Testing agent (iteration_252.json) live-API verified 6/6 + frontend test IDs verified 100%, no critical/minor/UI/design issues.

**API endpoints added/modified**:
- `POST /api/sales/sync` — extended to route by `draft_invoice_id`/`kind`
- `GET /api/sync/offline-reconciliation?status=open|resolved`
- `POST /api/sync/offline-reconciliation/{rec_id}/resolve` (manager+)
- `POST /api/admin/reconcile-orphan-offline-draft` (admin/owner, dry-run + PIN-confirm)
- `GET /api/invoices/by-number/{n}` — resolves both invoice_number and linked_offline_receipt_number
- `GET /api/view/{doc_code}` — payload extended with linked-receipt fields

**DB schema additions on `invoices`**:
- `linked_offline_receipt_number` (str)
- `finalized_from_draft_offline` (bool)
- `offline_completion_synced_at` (iso str)
- `original_draft_invoice_number` (str)
- `offline_items_diverged`, `payment_diverged` (bool, audit flags)

**New collection**: `offline_reconciliation_queue` (snapshots of conflicts for manager review).

---



## Feb 2026 — Close Wizard Credit & AR Refactor (Iter 252)

**Problem**: Same-day partial credit payments were INVISIBLE under "Credit Payment Today" (Step 3) of the Closing Wizard. The AR pipeline filtered out `order_date == today` invoices to avoid double-counting with `total_partial_cash`, which meant the initial cash leg of a partial sale created today (and any same-day top-up payment) only ever showed as `amount_paid` on the partial invoice in Step 1 — never as an auditable payment event. Cross-day payments worked fine; same-day did not. Confusing during cash balancing.

**Fix — Clean separation across Steps 1/2/3 + Z-report**:
- **Step 1 — Cash Sales Today**: now excludes BOTH `credit` and `partial` payment_types from the entries list and category breakdown. Pure cash/digital/split sales only. Added a legend banner explaining where credit & partial flows are now shown.
- **Step 2 — Customer Credit Generated Today**: each row now renders a 3-column grid (Invoice Total / Paid Today / Remaining) plus a status pill (`Unpaid` / `Partially Paid` / `Fully Paid Same-Day`) and a type pill (`Full Credit` / `Partial Credit`). Backend `credit_sales_today` enriched with `paid_today`, `remaining_balance`, `status`.
- **Step 3 — AR / Credit Payments Today**: AR pipeline now matches ALL payments dated today regardless of invoice age. Each payment row tagged `is_same_day` and `is_initial_partial`. UI splits into two labeled sub-sections — "Same-Day Credit Payments" (green) and "Older Credit Payments" (blue) — each with its own subtotal. INITIAL badge marks the initial cash leg of a partial sale.
- **Z-report**: same structure mirrored — "Customer Credit Generated Today" replaces "Credit Extended Today", AR section split into same-day/older sub-rows, "Partial Cash Received" tile removed (now subsumed by AR / Credit Payments Today).

**Math invariant preserved**: `total_cash_in` and `expected_counter` are byte-for-byte identical to the old computation when partial cash is via cashier wallet. Money is reattributed from the `total_partial_cash` bucket → `total_cash_ar` bucket; sum is unchanged. No Z-report regression. (Backed by 3-test pytest suite at `/app/backend/tests/test_close_wizard_credit_refactor_feb2026.py`.)

**Backend changes** (`/app/backend/routes/daily_operations.py`):
- `_closing_summary` (`get_daily_close_preview`): rewrote AR pipeline (drop `order_date != date` filter), enriched `credit_sales_today`, tagged `ar_payments` with `is_same_day` / `is_initial_partial` / `order_date` / `payment_type_invoice` / `invoice_total` / `recorded_by` / `recorded_at`, dropped `total_partial_cash` from `total_cash_in`, exposed `total_ar_same_day`, `total_ar_older`, `total_credit_invoice_value`.
- `get_daily_close_preview_batch`, `close_day`, `close_day_batch (seal)`: same pipeline + math change for parity.

**Frontend changes** (`/app/frontend/src/pages/CloseWizardPage.js`):
- STEPS array updated (new titles & descriptions for steps 1/2/3).
- Step 1 filter excludes `partial`; legend banner added (`step1-credit-legend`).
- Step 2 row rebuilt with 3-column grid + status pill (`credit-status-{idx}`).
- Step 3 split-section IIFE renders `same-day-credit-payments` and `older-credit-payments`.
- Cash Drawer Reconciliation tile + Z-report blocks updated to new structure (`zreport-credit-generated`, `z-ar-same-day`, `z-ar-older`).

**Tests**: `/app/backend/tests/test_close_wizard_credit_refactor_feb2026.py` — 3 PASS (pipeline shape, enrichment, math invariant). Testing agent (iteration_251.json) confirmed live API returns new fields, all new test IDs present in source, no critical/minor/UI/design issues.

---


## Feb 2026 — `today+1` auto-bump rolled out to all date pickers (Iter 251)

Following the Fund Transfer fix, applied the same "if today is closed, default and `max` jump to tomorrow" UX to every other date-bearing page so users are never stuck.

**New shared hook**: `/app/frontend/src/hooks/useDayPlusOne.js` — takes `branchId`, returns `{ todayClosed, defaultDate, maxDate, today, tomorrow }`. Calls `/api/invoices/check-date-closed` once per branch change and is used as the canonical UX driver.

**Wired into**:
- `PurchaseOrderPage.js` → header `purchase_date` field
- `PaySupplierPage.js` → `payDate` field
- `ExpensesPage.js` → all 4 date inputs (Expense, Farm Expense, Customer Cash-Out, Employee Advance)
- `PaymentsPage.js` → `payDate` field
- `FundTransferDialog.js` → already in place from iter 250

Behaviour now identical across Sales, PO, Pay Supplier, Expenses, Receive Payment, Fund Transfer: when today is closed, the date input opens to **tomorrow** and `max=tomorrow`. When today is open, defaults to **today**, `max=today`. Users still have full backdate freedom (within 7-day late-encode cap) by typing or clicking back.

---


## Feb 2026 — Fund Transfer "today closed" unblock (Iter 250)

**Problem**: `Safe → Bank` fund transfer (and the 3 other fund-transfer types) couldn't be recorded if the branch's "today" was already closed. Frontend date picker had `max={today}` (no tomorrow allowed) and defaulted to today; backend rejected closed-day with no escape. User was fully blocked.

**Fix**:
- **Frontend `FundTransferDialog.js`**: detects on-open whether today is closed for the branch via `/api/invoices/check-date-closed`; auto-bumps the default date to tomorrow and extends `max` to tomorrow when today is closed. Helper text under the date input explains the bump.
- **Backend `accounting.py /fund-transfers`**: now calls `enforce_max_date()` from the closed-day-guard helper, so today+1 is automatically the cap when today is closed. Closed-day rejection still applies for past closed days.

Behavior matches the rest of the closed-day-guard rollout (Sales/PO/Pay/Expense/Receive Payment): max = today, or today+1 if today is closed. Verified by curl: forward-dating to 2099 still rejected with "Maximum allowed is <today>".

---


## Feb 2026 — Closed-Day Enforcement & Late-Encode Rollout (Iter 249)

**Audit findings**: Sales already had a robust closed-day + late-encode + forward-date pattern. PO/PaySupplier/Expenses/ReceivePayment had **none** — user could pick any past or future date and bypass every Z-Report. Branch-transfer receive on a closed destination branch silently dropped off the books. Fund Transfers were the only other module that already blocked closed days.

**Shipped**:
- **`/app/backend/utils/closed_day_guard.py`** (NEW, ~380 lines) — single source of truth with `assert_open_day`, `enforce_max_date`, `resolve_late_encode`, `resolve_business_date`. All 7-day cap + cross-month + audit-log behaviors mirror Sales.
- **PO Create**: terms-only late-encode allowed; cash POs hard-block closed days. Forward-date cap with admin override.
- **Pay Supplier**, **Expenses**, **Receive Customer Payment**: full closed-day + late-encode + forward-cap.
- **Branch Transfer Receive**: silently auto-rolls to next-open day if destination is closed (inventory still moves immediately — the order doc gets `received_intended_date`, `received_date` (effective), `receive_late_encoded=true`, and an `audit_log` entry).
- **`zreport_pdf.py`**: late-encoded expense rows print an italic amber `[LATE ENCODE] expense — original date YYYY-MM-DD (closed)` caption underneath, so reviewers see the carryover at a glance.
- **`verify.py`**: added `late_encode` and `forward_date_override` policies (admin/manager/TOTP defaults).
- **Frontend `LateEncodeDialog`** generalized with `moduleLabel` + `paymentRestrictionLabel` + `allowedPaymentTypes` props (backwards-compatible with Sales).
- **Frontend pages** (`PurchaseOrderPage`, `PaySupplierPage`, `ExpensesPage`, `PaymentsPage`): submit handlers retry with `late_encode={pin, reason}` payload after server returns "already closed" 403; PO terms-only restriction enforced in dialog UI.

**Validated**: 16/16 pytest backend cases pass (forward-date cap, closed-day reject + accept-with-PIN, 7-day cap, cross-month block, branch-transfer auto-roll, audit log writes). New regression suite: `tests/test_closed_day_late_encode_249.py`. Frontend smoke OK (LateEncodeDialog testids verified).

---


## Feb 2026 — Z-Report PDF: Company + Branch + Date in name & header (Iter 249)

**Problem**: Z-Report PDFs (download + print from `/daily-ops` archive) and from Close Wizard had a generic filename `ZReport_<Branch>_<Date>.pdf` and an "AgriBooks Z-Report" hard-coded title, with no company name anywhere — so multi-tenant clients couldn't tell which client/business a downloaded PDF belonged to.

**What changed:**
- **Backend `zreport_pdf.py`**:
  - `ZReportPDF` class accepts new `company_name` arg. New 3-line header: (1) Company name in 16pt brand-green, (2) "Z-Report (COMPACT/DETAILED) | Branch | Date" subtitle, (3) "Prepared by: <cashier>".
  - Endpoint resolves company name from `settings.company_info` → fallback to `organizations.name`; renders into header **and** filename.
  - Filename pattern: `<Company>_<Branch>_<YYYY-MM-DD>_ZReport[_DETAILED].pdf` (each part slugified).
- **Frontend `DailyLogPage.js`**: fetches `business_name` from `/api/settings/business-info` once on mount; download filename uses the same `<Company>_<Branch>_<Date>_ZReport[_DETAILED].pdf` pattern. Print uses the backend's Content-Disposition.
- **Frontend `CloseWizardPage.js`**: same filename pattern in the wizard's "Download PDF" button.

**Validated**: backend smoke — filename now starts with company slug (e.g., `MyCompany_BranchA_2026-02-01_ZReport.pdf`); PDF first page header reads "Company Name → Z-Report (COMPACT) | Branch | Date → Prepared by: cashier"; ruff/eslint clean.

---


## Feb 2026 — PO + Pay Supplier Wallet Routing Audit & Fix (Iter 248)

**Problem**: `/purchase-orders` Pay-in-Cash dialog only offered Cashier + Safe (no Digital, no Bank). The Payment Adjustment dialog had the same gap. Backend `POST /purchase-orders` silently fell through to deduct from cashier when Bank/Digital was passed (data-integrity bug). `/pay-supplier` worked for all 4 wallets but used hardcoded labels and didn't mask hidden bank balances. No visible "paying via X" hint.

**What changed:**
- **Backend `purchase_orders.py`**:
  - `_get_fund_balances()` now returns all 4 wallets (cashier, safe, digital, bank) with `*_name`, `*_id`, and `*_available` flags. `GET /fund-balances` masks bank balance + sets `bank_hidden:true` for non-admin roles.
  - `POST /purchase-orders` (po_type=cash) accepts `fund_source ∈ {cashier, safe, digital, bank}`. Bank/Digital require admin PIN or TOTP via `verify_pin_for_action(pin, "pay_po_bank")`. Each path correctly: validates balance, drains from the right wallet (lots for safe, `update_digital_wallet`, direct $inc + `wallet_movements` for bank), writes the expense record, and auto-posts a double-entry journal (Debit COGS / Credit Bank-1030 or Digital-1020) for bank/digital.
  - `POST /{po_id}/adjust-payment` extended with the same 4-wallet support + PIN policy + refund symmetry (lot insertion for safe, helper for digital, $inc + `wallet_movements bank_in` for bank).
- **Frontend `PurchaseOrderPage.js`**:
  - Pay-in-Cash dialog shows 4-card grid (Cashier Drawer, Physical Safe, Digital / E-Wallet, Bank Account), with lock icons on Digital/Bank, masked `••••` balance + "Admin-only balance" caption when bank is hidden, conditional admin PIN input shown only when Bank or Digital is selected, and a clear method/wallet hint in the confirm summary.
  - Pay-Adjustment dialog mirrors the same 4-wallet UX + PIN gate.
- **Frontend `PaySupplierPage.js`**:
  - Replaces hardcoded labels with each wallet's `name` from `/fund-wallets` (defaults to canonical `Cashier Drawer` / `Physical Safe` / `Digital / E-Wallet` / `Bank Account`).
  - Shows masked `••••` balance for bank when `balance_hidden:true`; disables disabled/missing wallets; suppresses the "short" red flag when balance is hidden.
  - Adds visible "Paying via **<Method>** from **<Wallet name>**" hint below the Pay From row.

**Validated by testing agent (iter 248, 16/16 backend passes + frontend smoke):**
- All 4 wallet sources route correctly on PO creation + adjustment.
- Bank/Digital reject without PIN (400) and with invalid PIN (403); succeed with admin PIN 913712.
- Bank balance masked for non-admin roles. Insufficient-funds returns structured `{type: insufficient_funds, shortfall}`. Journal entries posted for bank (1030) + digital (1020).

---


## May 2026 — Request Stock UX upgrade: SmartProductSearch (Iter 247)

**User scenario**: The Request Stock form had a basic typeahead — no keyboard navigation, no smart bubble positioning, no fuzzy fallback. User asked for parity with `/sales-new` Detailed Sales: ↑/↓ arrow keys, Enter-to-select-and-jump-to-next-field, dropdown that auto-flips above/below the input depending on viewport space, highlighted match always visible (centered), and smart search behaviors (fuzzy + offline cache fallback).

**What changed:**
- `SmartProductSearch.js` (the same component powering /sales-new Detailed Sales) now accepts optional `mode='request'`, `alsoBranchId`, and `placeholder` props. In request mode, the dropdown rows are compact 2-line cards (name/SKU + Supply-branch stock + Your-branch stock), and the API call adds `also_branch_id` + `limit=8`. Default 'sales' mode is unchanged.
- `BranchTransferPage.js` Request Stock form rows refactored: each row is either a `<SmartProductSearch>` (when `row.product` is null) or a green-tinted product summary card with X-to-clear (when filled). On product select, the row is filled, the next empty row auto-grows, and qty input auto-focuses (mirrors `UnifiedSalesPage.handleProductSelect`).
- Removed: per-row `search`/`matches` state, custom `handleReqSearch`, the legacy `AnchoredDropdown` usage in this form (component itself still serves the main transfer flow).
- Tab order tightened: trash X on each row uses `tabIndex={-1}` so Tab from qty hops directly to the next row's search input.

**Validated by testing agent (iter 247, 10/10 passes):** typeahead, ArrowDown/ArrowUp navigation, Enter-to-select, qty auto-focus, auto-grow, X-to-clear, smart bubble flip-up/flip-down, fuzzy fallback chip with "No exact match for X — showing closest matches", multi-row keyboard-only flow.

---

## May 2026 — Branch Stock Request: SMS Notifications + Tab UX + Configurable Recipients (Iter 246)

**User scenarios prompting this change:**
1. The Request Stock form on the Branch Transfer page only had a manual "+ Add Product" button — slow data entry compared to the fast `/sales-new` Detailed Sales flow.
2. When a branch fired a stock request, only an in-app notification was created. Owners away from the dashboard had no idea a request was waiting.
3. Concern: if 3-4 staff race to fulfill the same request, who wins? Need view-only links so the link is safe to share, but fulfillment must happen via the authenticated app.

**Backend** (`/app/backend/routes/sms.py`, `purchase_orders.py`, `doc_lookup.py`):
- New SMS template `branch_stock_request` (Body: "Hi <recipient_name>, <requesting_branch> requested stocks from <supply_branch> (PO <po_number>, <items_count> item/s). Top items: <items_summary>. View: <view_link>").
- **Critical auto-seed fix**: `queue_sms()` previously only seeded `DEFAULT_TEMPLATES` if the org had ZERO templates. New keys shipped in subsequent releases never reached existing tenants. Now does per-key `$setOnInsert` upsert when the requested template_key is missing — every new template is reachable for every existing tenant on first send.
- New endpoints `GET /api/sms/recipients/{trigger_key}` and `PUT /api/sms/recipients/{trigger_key}` storing config in `db.sms_recipient_config`. Currently used by `branch_stock_request` with role flags `include_admins`, `include_supply_manager`, `include_supply_auditor`, `include_all_supply_users`.
- New helper `_notify_stock_request_recipients()` in `purchase_orders.py` resolves recipients per the role config, builds the public view link from `auto_generate_doc_code`'s output, and queues SMS via `queue_sms()`. Called from the existing `branch_request` block in `POST /api/purchase-orders` — in-app notifications still fire alongside SMS regardless of role config.
- Public viewer (`GET /api/doc/view/{code}`) now returns `is_branch_request`, `supply_branch_name`, `requesting_branch_name`, `notes`, `fulfillment_started_at/by` for branch_request POs so the existing DocViewer can render them as Stock Requests.

**Concurrency:** `POST /api/purchase-orders/{po_id}/generate-branch-transfer` already enforces a single-fulfillment lock (status flips `requested → in_progress` on first click; subsequent attempts return `400 "Request has already been processed"`). No race for 3-4 viewers.

**Frontend**:
- `BranchTransferPage.js` — Request Stock form rows now mirror Detailed Sales UX: Enter on the search input picks the first dropdown match and auto-focuses qty; Tab on the LAST row's qty (when product + qty present) auto-adds a new empty row and focuses its search field. Tab on non-last rows behaves normally.
- `MessagesPage.js` — Settings tab gained a "Branch Stock Request — Recipients" card with 4 toggles persisting via the new recipients endpoint.
- `DocViewerPage.jsx` — when `is_branch_request==true`, header label becomes "Stock Request", body shows `<requesting_branch> requests from <supply_branch>`, and the action area is a view-only amber notice + "Open in Branch Transfer" button (login-gated).

**Testing**: 7/7 backend pytests at `/app/backend/tests/test_branch_stock_request_sms_245.py` pass clean. Frontend Tab UX, Settings card, and DocViewer behavior all validated by testing agent (iter 246).

---

## May 2026 — Admin Reconciliation Adjustments on Closed Days (Iter 237)

User scenario that prompted this: a branch migrated from a legacy POS had its first few days' sync double-count AR carry-over, producing phantom Over/Short figures like `−₱145,608.40` when the physical drawer was actually `+₱1,746` over. The user needed a way to correct the displayed Net Over/Short **without** rewriting history — Expected/Actual values must remain the raw-as-recorded figures for auditability.

**Design chosen (Option C — bookkeeper-approved):** immutable adjustment entries that shift the *effective* Over/Short. Original closing doc is never mutated. Each adjustment is signed, reason-stamped, and audit-logged.

**Backend** (`/app/backend/routes/daily_operations.py`):
- New collection `daily_closing_adjustments` with fields `closing_id`, `amount` (signed), `reason`, `original_over_short/expected/actual` (frozen at creation time), `created_by_name`, `voided` + `voided_by/reason/at` for history-preserving voids.
- New endpoints:
  - `GET /api/daily-closings/{closing_id}/adjustments` — full history including voided
  - `POST /api/daily-closings/{closing_id}/adjustments` — admin-only; requires non-zero amount + reason (min 3 chars)
  - `POST /api/daily-closings/{closing_id}/adjustments/{adj_id}/void` — admin-only; requires reason
- `GET /api/daily-close/{date}` now also returns `adjustments` (non-voided), `adjustment_total`, and `adjusted_over_short = over_short + adjustment_total`. Original `over_short` stays verbatim.
- `GET /api/daily-variance-history` aggregates non-voided adjustments per closing and attaches `adjustment_count`, `adjustment_total`, `adjusted_over_short` so the archive grid and the Net Over/Short KPI reflect the corrected value.
- Every create/void writes an `audit_log` entry (`closing_adjustment_created` / `closing_adjustment_voided`) with before/after values and the actor.

**Authorization:** strictly admin role (`user.role == 'admin'`). No PIN, no manager bypass. Non-admins get 403.

**Frontend** (`DailyLogPage.js`):
- New `<ClosingAdjustmentsPanel>` component rendered at the top of the Z-Report viewer dialog. Shows a three-column strip (Raw Over/Short → Adjustments → Adjusted Over/Short), a list of all adjustments (voided ones dimmed), and — for admins only — an inline form (Amount + Reason) to apply a new adjustment or void an existing one.
- Z-Report Archive table: uses `adjusted_over_short` for the Over/Short cell, shows an amber `ADJ` pill next to the value when adjustments exist, and renders the raw value below in small grey mono so the paper trail is visible at-a-glance.
- KPI tile "Net Over/Short" now sums `adjusted_over_short` across filtered rows.

**Regression**: `backend/tests/test_closing_adjustments_237.py` — 5 end-to-end tests covering admin create, aggregation in variance history, manager 403, amount/reason validation, void semantics + audit trail.


## May 2026 — Z-Report Detailed: Capital columns next to Product + Last-Purchase fallback (Iter 236)

Follow-up polish to iter 235:

- **Column order**: `Cap MA` + `Last Purchase` now sit directly after the Product name (before From → To). Reading flow becomes Product → what it costs us → what we sold it for → approver.
- **Moving Average is strictly branch-specific** — no fallback. If this branch has zero purchase/transfer-in history for the SKU, MA renders as `—` (previously was silently falling back to `product.cost_price` which understated the per-branch cost basis).
- **Last Purchase fallback**: branch-specific first; if this branch has no acquisition history, fall back to the product's global `cost_price`. The fallback value is shown in italic with an `*` marker and a footnote ("Last Purchase shown is the product's global cost — no branch purchase history yet"), so the owner knows it isn't a real branch receipt.
- Backend now returns `last_purchase_source` per row (`"branch" | "global" | "none"`); both the PDF and on-screen view key off it to render the fallback indicator consistently.

Files touched: `backend/routes/daily_operations.py` · `backend/routes/zreport_pdf.py` · `frontend/src/pages/DailyLogPage.js`. Regression suite (14 tests) still green.


## May 2026 — Z-Report Detailed: Price-Change refinements + Interest reorder (Iter 235)

User feedback on the Detailed Z-Report:
1. Move "Interest & Penalty Invoices Issued Today" directly under "AR Payments Received" (instead of buried near the bottom of the PDF) so the owner can audit collections vs new charges side-by-side.
2. **Permanent Price Changes** improvements:
   - Group rows by **category** (alphabetical), with products inside each category sorted alphabetically.
   - Add **Cap MA** (capital moving average) and **Last Purchase** columns for each row so the owner can see immediately how the new price compares against the cost basis.
   - Color the new price **green** when it went up vs old, **red** when it went down (signal margin compression at a glance).

**Backend** (`/app/backend/routes/daily_operations.py · _get_price_changes_today`):
- Now enriches each `price_change_log` row with `category` (from products), `moving_average_cost` and `last_purchase_cost` (computed branch-specific from `movements` purchase + transfer_in entries; repack-aware).
- Returns rows pre-sorted by `(category.lower(), product_name.lower())`.
- Per-product enrichment is cached per call so multi-line price changes for the same SKU don't re-query.

**Backend PDF** (`/app/backend/routes/zreport_pdf.py · render_detailed`):
- Reordered: Interest & Penalty Invoices block now appears immediately after AR Payments Received (was section #10 → now #3).
- Permanent Price Changes table: grouped by category sub-headers (with row count per category), 7-column layout — Product · From · → · To · Cap MA · Last Pur · OK by — colored by direction (green up / red down).

**Frontend** (`/app/frontend/src/pages/DailyLogPage.js · ZReportDetailed`):
- Same category grouping + Cap MA + Last Pur columns on the on-screen Detailed view, so what's shown matches what's printed.
- Existing on-screen order already had Interest right under AR Collections — no further change needed there.

Regression tests (`backend/tests/test_zreport_pdf_unicode_232.py`) — all 14 pass against the rewritten PDF generator.


## May 2026 — Z-Report PDF preview/print parity + Unicode crash fix (Iter 234)

**User reports**:
1. *"The preview is different from the one being printed"* — clicking Download/Print from the Normal Z-Report view produced a tiny PDF that only had the Cash Drawer Reconciliation block, while the on-screen Normal view also shows Walk-in Sales by Category, Credit Extended Today, AR Payments Received, AR Balance at Close, and Expenses.
2. *"Could not generate Z-Report PDF for Detailed Mode"* — Detailed Mode PDF download threw an error whenever the data contained non-Latin-1 characters.

**Root causes**:
1. PDF Normal layout was hardcoded to a stub (Cash Reconciliation only), whereas the on-screen `<ZReport>` component in `DailyLogPage.js` renders 7 sections.
2. `FPDF`'s default Helvetica font is Latin-1 only — customer names / expense descriptions / transfer notes routinely contain em-dashes (`—`), peso signs (`₱`), curly quotes, bullets, arrows, ellipsis. Any single occurrence raised `FPDFUnicodeEncodingException` and aborted the whole download.

**Fix** (`/app/backend/routes/zreport_pdf.py`, full rewrite):
- Added `_s()` unicode sanitizer that folds common typographic characters to ASCII equivalents (`—`→`-`, `₱`→`P`, `→`→`->`, `•`→`-`, `…`→`...`, curly quotes → straight, `✓`→`OK`) and strips anything still outside Latin-1 instead of crashing. Applied to every `cell()` / `output()` write via `_s(...)`.
- Added `render_normal()` that mirrors the on-screen Normal `<ZReport>` layout 1:1: **Opening** (Safe Balance + Opening Float), **Cash Reconciliation** (Expected/Actual/Over-Short/Vault/Next Float), **Walk-in Sales by Category**, **New Credit Extended Today** (credit_sales_today + ar_credits_today as a table), **AR Payments Received** (credit_collections with Interest/Penalty columns), **AR Balance at Close**, **Expenses** (with Employee Advance CA details + monthly-limit/approver notes).
- PDF header now shows `COMPACT` or `DETAILED` mode label for at-a-glance version identification.
- Endpoint now looks up the `daily_closings` record by `date+branch_id` (previously only by `closing_id`), so closed-day actuals — actual cash, over/short, cash to vault, next float — populate properly even when the frontend calls with just date+branch.
- Detailed layout unchanged functionally; every text write now goes through `_s()` so it's crash-proof.

**Regression**: `backend/tests/test_zreport_pdf_unicode_232.py` — 14 passing tests cover (a) the sanitizer against em-dash/peso/arrow/bullet/ellipsis/curly-quotes/emoji, (b) both Normal and Detailed PDF generation with unicode-heavy customer / employee / description data, (c) the Normal PDF containing all 7 UI sections.


## Feb 2026 — Z-Report PDF respects Normal vs Detailed mode (Iter 233)

**User report**: Whether the user clicked Print/Download from the **Normal** or the **Detailed** Z-Report view, the PDF that came out was always the same (compact). Detailed view's Print/Download was effectively cosmetic — the backend ignored which view triggered it.

**Fix**:
- **Backend** (`/app/backend/routes/zreport_pdf.py`): `GET /api/reports/z-report-pdf` now accepts `detailed: bool = False` query param.
  - `detailed=False` → renders ONLY the Cash Drawer Reconciliation block + footer summary (compact, mirrors on-screen Normal view).
  - `detailed=True` → renders the full Step-7 breakdown: AR Payments, Fund Transfers, Cashier/Safe Expenses, Credit Extended, Digital/E-Wallet, Cash Sales by Category, **+ NEW**: Discounts by Product, Permanent Price Changes, Interest & Penalty Invoices Issued. Mirrors on-screen Detailed view.
  - Filename now includes `_DETAILED` suffix when applicable: `ZReport_DETAILED_<branch>_<date>.pdf` vs `ZReport_<branch>_<date>.pdf`.

- **Frontend** (`DailyLogPage.js` + `CloseWizardPage.js`): The Print and Download PDF buttons now pass `detailed: true|false` based on the active view mode. Toast text updates to `"Preparing detailed PDF..."` when in Detailed mode for clarity.
  - Close Wizard Step 8 always sends `detailed: true` since it's used immediately after the wizard's Step 7 detailed preview.

The PDF print bug from Iter 230 (Radix Dialog blocking browser print) was already worked around by going through the PDF endpoint — this iter fixes the orthogonal bug that the PDF endpoint always rendered the same content regardless of the calling view.


## Feb 2026 — Interest/Penalty Backdate Guard + Retag Audit (Iter 232)

**User report**: Generated interest on 5/4 but dated it as 5/3 (via PaymentsPage date picker), and also collected the payment dated 5/3. The Close Wizard for 5/4 still showed it in "Interest & Penalty Invoices Created Today" because the header/UI trusted the creation timestamp while the ledger used `order_date` — mismatched dates leak across the auditor's view.

**Fix — 2 pieces on top of Iter 231**:

**A. Interest & Penalty generators — closed-day guard** (`routes/accounting.py`):
- `POST /customers/:id/generate-interest` and `POST /customers/:id/generate-penalty` now check the proposed `as_of_date` against `daily_closings`. If that day is already closed for the customer's active branch, return HTTP 400 with a clear error. Escape hatch: `force_backdate_to_closed=true` with manager PIN (for hard corrections).
- Removes the only remaining way to silently land an interest/penalty row with a wrong `order_date` relative to the true insert day.

**B. Backdated-charge detector & retag endpoint**:
- `GET /api/invoices/audit/backdated-charges` — scans every non-voided interest/penalty invoice and returns those where `order_date` is more than one day earlier than the `created_at` date (the "create-with-wrong-date" pattern that Iter 231's `date-moves` audit didn't catch because there's no edit event). Flags closed-day hits with `on_closed_day: true` and sorts worst-first.
- `POST /api/invoices/:id/retag-order-date-to-today` — forward-shifts `order_date` + `invoice_date` to the real creation date (`created_at[:10]`) for interest/penalty invoices. Refuses if the target day is also closed. Writes an `invoice_edits` row with `reason=backdated_charge_retag` for the audit trail. PIN-gated.

**Frontend**: `DateMovesTab.js` now fetches both endpoints in parallel. Renders a second "Backdated Interest / Penalty Invoices" section under the existing Date Moves table with a red-tinted row per backdated charge and a single "Retag to created_at" button per row. Same PIN dialog handles both flows; `data-testid="dmt-retag-btn-<invoice_id>"` for each row.

**Test coverage preserved**: All 5 Iter 231 tests still pass. New audit endpoint smoke-tested via curl against the preview env (returns empty array as expected).


## Feb 2026 — Closed-Day Date-Move Guard + Audit Tab + One-Click Restore (Iter 231)

**User report**: Interest invoices INT-SB-001004/5 created on 4/3/25 (closed day) appeared again on 5/4/25 Z-Report. User suspected "cancellation not properly reversed".

**RCA**: The bug wasn't cancellation — it was an **invoice edit** path (`PUT /api/invoices/:id/edit`) that let anyone change `order_date` / `invoice_date` OUT of a closed day as long as the TARGET date wasn't also closed. The guard at `invoices.py` L462-475 was one-sided — it only blocked moves INTO closed days. Closed-day bookkeeping was therefore mutable: a user clicking "Edit → change date" on a closed-day transaction silently shifted it to another date, producing double counting when the new date was also closed / queried.

**Fix — 3 pieces**:

**A. Backend guard** (`routes/invoices.py`): The `edit_invoice` endpoint now refuses any `order_date` / `invoice_date` change when the invoice CURRENTLY sits on a closed day. Returns 400 with an actionable message pointing to Void + Re-issue. An escape hatch (`force_move_date=True` + manager PIN) exists for hard corrections — every forced move lands in the audit log for the Date Moves tab.

**B. Audit endpoint** (`GET /api/invoices/audit/date-moves?closed_source_only=true|false`): Parses every `invoice_edits.changes` row for `order_date: 'X' → 'Y'` patterns, checks whether the source date was closed at the time, returns a sorted list with the invoice's current date, moves, who edited, when, and reason. Multi-tenant scoped.

**C. One-click restore endpoint** (`POST /api/invoices/:id/restore-date`): Reads the most recent date-move record, rewinds `order_date` / `invoice_date` to the original values, recomputes `due_date` from `terms_days`, writes a new audit entry with `reason=date_move_restore`. Manager PIN required (reuses `verify_pin_for_action`).

**Frontend**: New `DateMovesTab` component added to Audit Center as a dedicated tab. Lists every offending record (rose-tinted for closed-source moves), shows the full from→to move with strikethrough, has a green "Restore" button per row that opens a PIN dialog. Also a "Show all moves" toggle for admins who want the full edit-history audit, not just the dangerous subset.

**Tests** — `tests/test_closed_day_date_move_guard_231.py` (5 passing):
- Edit refuses date move out of closed day
- Edit refuses without PIN when source is closed
- Audit endpoint lists offending records
- Restore endpoint puts dates back with a valid PIN
- Restore refuses without PIN

**For the affected INT-SB-001004/5**: After deploy, go to **Audit Center → Date Moves tab** → both records should appear in red → click **Restore** on each → enter manager PIN → done. 4/3/25 live queries and 5/4/25 Z-Report will both be clean. Closed-day snapshots for 4/3 are already correct (they were frozen at close-time).


## Feb 2026 — Z-Report Print via PDF + Download PDF Button (Iter 230)

**User report**: After the `id="printable-report"` CSS fix, the Z-Report Print button still prints a blank page. Also requested a Download PDF option.

**Root cause (why the CSS fix wasn't enough)**: Radix Dialog renders inside a `DialogPortal` with `position: fixed` and `transform` on the content container. These create a new CSS containing block, so the `#printable-report { position: absolute; left: 0; top: 0 }` rules get trapped inside the modal's transform box — not laid out at page-scale. Even with `visibility: visible`, the print-time layout is broken.

**Fix — ditch browser-print entirely, use the existing PDF endpoint** (`/api/reports/z-report-pdf`, already fully detailed):
- New helpers in `DailyLogPage.js`:
  - `fetchZReportBlob(date, branchId)` — calls the endpoint through the axios `api` instance (with JWT) using `responseType: 'blob'`, returns an object URL.
  - `printZReportPdf(...)` — opens the PDF in a new tab and triggers the browser's print dialog once loaded. Handles popup-blocker gracefully with a clear toast.
  - `downloadZReportPdf(..., branchName)` — saves the PDF with a meaningful filename like `ZReport_MainBranch_2026-05-01.pdf`.
- Both `<ZReport>` and `<ZReportDetailed>` now expose two buttons: **Print** (opens PDF for printing) and **Download PDF**. Since the backend PDF is already the full detailed version, both views produce the same — reliable — printed output.
- `CloseWizardPage.js` Step 8 `Print Z-Report` and `Download PDF` buttons switched from unauthenticated `window.open(url)` (which would silently 401 on a JWT-gated endpoint) to the same authenticated blob flow. Previously broken; now working.

**Why this also makes the earlier `id="printable-report"` change obsolete for normal users**: the CSS still works for any page-level content with that ID, but the Z-Report paths don't rely on browser print anymore. PDFs are consistent across platforms (Windows/Mac/iPad/Android Chrome), respect page margins automatically, and produce archivable files for the accountant / BIR audit trail.

`data-testid` added to all four new buttons: `z-normal-print-btn`, `z-normal-download-btn`, `z-detailed-print-btn`, `z-detailed-download-btn`, plus `wiz-print-z-btn` / `wiz-download-z-btn` on the Close Wizard. Lint clean on both edited files.


## Feb 2026 — Detailed Z-Report Toggle (Iter 229)

**User ask**: "Step 7 preview in the Close Wizard has SO MANY details (price changes, discounts, AR by method, fund transfers, per-category expenses). The regular Z-Report I can print later doesn't show any of that. Give me a Detailed Z-Report next to the Normal one so I can choose which to view & print."

**Fix** (`/app/frontend/src/pages/DailyLogPage.js`):
- New `<ZReportDetailed>` component — renders the same rich breakdown as Close Wizard Step 7 (Cash Drawer Reconciliation, Discount-per-product, Permanent Price Changes, AR Collections with per-method chips, Interest & Penalty Invoices Issued, Fund Transfers, Cashier/Safe Expenses). Uses `id="printable-report"` for print support.
- Z-Report viewer dialog now has a `[Normal]` / `[Detailed]` toggle at the top (`data-testid="z-report-mode-toggle"`). Default = Normal (fast, compact). Clicking Detailed lazy-loads `/daily-close-preview` for that date/branch — same payload Step 7 uses, recomputes from `sales_log` + `invoices` + `fund_transfers` so it works on historical closed days.
- Each view has its own Print button — printing either the Normal or the Detailed view now works end-to-end.

No backend changes (endpoint `/api/daily-close-preview` already handles any date). Lint clean.


## Feb 2026 — Z-Report Print = Blank Page (Iter 228)

**User report**: On `/daily-ops` the Z-Report dialog renders fine on screen, but clicking **Print** produces an entirely blank page preview.

**Root cause**: `/app/frontend/src/index.css` has a global `@media print` block:
```css
body * { visibility: hidden !important; }
#printable-report, #printable-report * { visibility: visible !important; }
#printable-report { position: absolute; left: 0; top: 0; width: 100%; padding: 24px; background: white; }
```
This hides EVERYTHING during print except an element with `id="printable-report"`. But that ID existed nowhere in the codebase — neither the `ZReport` component (`DailyLogPage.js` line 52) nor the Close Wizard Step 8 success view had it. So browser printed a blank page, exactly as the CSS instructed.

**Fix** — added `id="printable-report"` to:
1. `ZReport` component's root `<div>` (`DailyLogPage.js`) — covers both the inline close-day view AND the Z-Report Archive dialog.
2. Close Wizard Step 8 success container (`CloseWizardPage.js`) — covers the "Print Z-Report" button at the end of the wizard.

No other CSS or component changes needed; the existing print rules already layout + paginate the report cleanly once the ID is present.


## Feb 2026 — SMS ONE-SHOT Dispatch Policy (Iter 227) — Carrier-Flag Recovery

**User report**: After the 60-SMS spam flood, the user's **carrier FLAGGED the SIM for spam**. User correctly pointed out: "If the server says pending, the gateway will send it the moment it comes back online. One send is enough. Retries are pointless and cause duplicates." They use the **web gateway** (not Android app), so there's no local app cache to clear.

**Fix — three hard guards** (`/app/backend/routes/sms.py`):

1. **`MAX_DISPATCHES_PER_DAY = 1`** (was 3). Every queue row is dispatched **exactly once** by the server. After that single hand-out the row becomes `sent` (ACK received), `failed` (gateway reported failure), or `deferred` (no ACK within lease).

2. **Auto-revive of deferred rows REMOVED**. Previously the server flipped `deferred` → `pending` with a fresh 3-strike budget every night. That re-sent any SMS whose GSM send succeeded but whose ACK failed — the exact cause of the spam. Now deferred rows sit visibly in the queue forever; admin must manually POST `/sms/queue/{id}/retry` after confirming non-delivery.

3. **Absolute per-phone daily fuse `MAX_SMS_PER_PHONE_PER_DAY = 10`** in `queue_sms`. No matter what templates, triggers, schedulers or retries are firing, a single phone number can never accumulate more than 10 auto-triggered SMS rows in a rolling 24h window. This is the last-line defense that would have prevented the carrier-flag. Manual composes (admin typing a message) bypass this cap.

**Tests updated** (`tests/test_sms_spam_protection_216.py`): old 3-strikes + auto-revive tests replaced with one-shot + no-auto-revive assertions. `test_spam_protection_constants` now asserts the new `MAX_DISPATCHES_PER_DAY=1` and `MAX_SMS_PER_PHONE_PER_DAY=10`. **All 12 SMS regression tests pass** (`test_mute_purge_queue_226.py` + `test_branch_close_reminder_opt_out_201.py` + `test_sms_spam_protection_216.py`).

**Trade-off accepted by user**: if the gateway crashes BEFORE actually sending a row via GSM, that row becomes `deferred` and needs manual `/retry`. Under-delivery is explicitly preferred over carrier-blacklisting.


## Feb 2026 — SMS Mute Purge + Gateway-Replay Storm Protection (Iter 226)

**User report**: Owner got spammed with **60+ duplicate close-day SMS** during a gateway DNS outage. Muting the affected branches via the Team SMS card did nothing — the spam kept coming. User had to log out of the gateway entirely.

**Root cause**:
1. **Mute bug**: The branch mute toggle only set `close_reminder_disabled=true` on the branch record — it did NOT purge queue rows that were already created before the mute. Those rows kept getting handed out to the gateway on every poll.
2. **Gateway replay**: The Kotlin gateway app's local send-worker kept retrying the same ~7 queueIds via GSM because its `mark-sent` HTTP POST kept failing due to DNS (`Unable to resolve host agri-books.com`). Every retry = another real SMS delivered. (Gateway-side fix is out of scope — this repo only controls the server.)

**Fixes** (`/app/backend/routes/sms.py`, `/app/frontend/src/components/sms/TeamSmsRemindersCard.js`):
- **A. Purge-on-mute** — `PUT /api/sms/close-reminder/branch-toggle/:branch_id` with `disabled=true` now cancels every `pending`/`deferred`/`failed` close-reminder row for that branch in the same transaction. Response includes `purged` count; toast shows "🔕 MUTED — N pending close-day SMS cancelled".
- **B. Filter-on-dispatch safety net** — `GET /sms/queue/pending` checks each candidate's branch against the muted list; any close-reminder row belonging to a muted branch is flipped to `cancelled` (`reason=branch_muted_at_dispatch`) and excluded from the returned batch. Catches race conditions and legacy rows.
- **C. Emergency kill-switch** — New endpoint `POST /sms/queue/cancel-pending-close-reminders` + red "Emergency: Stop All Pending Close-Day SMS" button in the Team SMS card. Cancels every pending close-reminder across ALL branches for the caller's org (customer / expense / manual SMS untouched). Owner-only panic button.
- **D. Tighter dispatch cap for close-reminder templates** — `MAX_DISPATCHES_CLOSE_REMINDER=2` (was implicit 3 via `MAX_DISPATCHES_PER_DAY`). A close-reminder row can now only be handed out twice per day before being deferred till tomorrow. Halves the blast radius of any future gateway-ack failure.

**Tests** — `tests/test_mute_purge_queue_226.py` (3 passing):
- Muting a branch purges its pending/deferred/failed close-reminder rows ONLY (other templates + other branches untouched)
- Unmuting does NOT resurrect cancelled rows
- Emergency endpoint cancels all close-reminder rows across branches without touching other templates

**Gateway-side follow-up for the user**: once deployed, the owner needs to force-stop + clear the Kotlin SMS gateway app's local data (~7 stuck queueIds cached on the phone). Server-side alone can't flush a phone-local SQLite queue.


## Feb 2026 — Accounting Page: Customer Typeahead Search in Farm Expense & Cash Out Dialogs

**User request**: On `/accounting`, the Farm Expense and Customer Cash Out dialogs used a `<Select>` dropdown for picking the customer to bill — unusable with 500+ customers. User asked for a typeahead input, and to move **Bill to Customer** to the **top**, above **Service Description**.

**Fix** (`/app/frontend/src/pages/AccountingPage.js`):
- Replaced the `<Select>` dropdown in both the Farm Expense dialog and the Customer Cash Out dialog with a typeahead `<Input>` — matches by `customer.name.toLowerCase().includes(query)`, shows up to 8 suggestions, supports keyboard navigation (`ArrowUp/Down/Enter/Escape`).
- Re-ordered the Farm Expense dialog so **Bill to Customer** is now the first field (above Service Description), matching user-chosen workflow.
- Added typeahead state (`farmCustomerQuery/Highlight/ListOpen`, `cashOutCustomerQuery/Highlight/ListOpen`).
- Shows an AR balance badge on the selected customer (red pill) for quick credit visibility.
- Mirrors the proven pattern already in `ExpensesPage.js` (`data-testid="farm-customer-search"`, `cashout-customer-search`, etc.).


## Feb 2026 — Close Wizard AR Payments: Voided Payments Double-Counted (Iter 224)

**User report**: Close Wizard Step 3 "AR Payments" showed customer DEMAYO ROLANDO's invoice `SI-SB-001029` **twice** — once as ₱42,295 paid, again as ₱38,995 paid (total ₱81,290), against a real collection of only ₱43,105. User rightly suspected "the math is wrong."

### Root cause
The AR-payments / credit-collections aggregation pipelines **unwound every payment on every invoice and matched on date only** — voided payments (created when a cashier uses the Edit/Cancel flow) slipped through silently, double-counting the collection. The same invoice appeared once for the voided ₱42,295 and again for the live ₱38,995.

### Fix
Added `"payments.voided": {"$ne": True}` to the `$match` stage of **every** payment-unwind pipeline across three files:
- `/app/backend/routes/daily_operations.py` — 5 pipelines: single-day preview, batch preview, close day POST, batch close POST, sales-cashflow.
- `/app/backend/routes/audit.py` — 2 pipelines: AR payments detail + aging collections.
- `/app/backend/routes/dashboard.py` — 2 pipelines: today's AR collected + per-branch AR.

### Tests: `tests/test_voided_payments_excluded_224.py` — 2/2 PASS
- Seeds a fake invoice with ONE voided payment (₱5,000) + ONE live payment (₱3,000), both dated today → asserts single-day `/daily-close-preview` returns EXACTLY 1 row with ₱3,000.
- Asserts `/daily-close-preview/batch` `total_ar_received` equals the non-voided aggregate (₱5,000 excluded).


## Feb 2026 — Branch Transfer: Pre-Flight Stock Guard + Audit Trail Fix (Iter 224)

**User report**: Live ticket `BTO-20260503-0003` on agri-books.com — "Created a branch transfer, injected the product from source to main. It says FAILED but stocks got deducted."

### Root causes found in `_apply_receipt`:

1. **Partial-failure inventory drift** — items were processed in a for-loop; each iteration decremented source + incremented destination BEFORE the next item's source-stock check. If item N failed insufficient-stock validation, items 1..N-1 had already had inventory moved while the transfer status was never flipped to `received`. The UI showed "Failed" but inventory was half-moved.

2. **Silent audit corruption** — the `capital_changes` row was being inserted AFTER the `branch_prices` upsert. The code re-read `bp_before` right before the audit write, so `old_capital` always equalled `new_capital`. Any investigator scanning the capital-change ledger would see no actual transitions.

3. **Smart Rule method mislogged** — `choice = capital_choices.get(product_id, "transfer_capital")` was overwriting the earlier Smart-Rule computation right before the audit insert. When Smart Rule auto-switched to `moving_average` (because transfer_capital < current_dest_capital), the log still recorded `transfer_capital`.

### Fix (`/app/backend/routes/branch_transfers.py::_apply_receipt`):
- **Pre-flight loop**: validates source stock for EVERY item and snapshots destination capital BEFORE any mutation. If any item fails stock check, raises `400` with "No inventory changed." — zero partial mutation.
- **Audit write uses the pre-flight snapshot** for `old_capital` — true old→new transition.
- **Preserved Smart-Rule `choice` variable** through to the audit insert — method field is now trustworthy.

### Tests: `tests/test_branch_transfer_preflight_224.py` — 3/3 PASS
- `test_preflight_rollback_on_insufficient_stock` — seeds 2 products, one with enough stock one short; asserts 400 AND NO inventory moved for either.
- `test_capital_change_audit_records_true_old_capital` — seeds dest@8, transfers @15, asserts `old=8, new=15`.
- `test_smart_rule_records_moving_average_when_selected` — transfer_capital=5 < dest_capital=20 → asserts audit method=`moving_average`.


## Feb 2026 — Fund Injection Date + Offline Receipt Numbering + Payment Date Edit (Iter 191)

**User reports**:
- "Closing wizard and Z report didn't include fund injection"
- "Fund Injection should have at least a date that can be added, only possible on open days not on closed days"
- "Sales receipt number on offline is not based on our standard — use our online standard with a postfix that differentiates it from online"
- "On payment history add a change date feature" (already-posted payments)

**Three minor but high-value improvements:**

### 1. Fund Injection Date Field + Closed-Day Guard
- `FundTransferDialog` now has a native date picker (defaults to local today, `max=today`)
- Pre-flight validation via `/invoices/check-date-closed` — Confirm button disabled when the chosen date is already closed; inline message explains why
- Backend `POST /fund-transfers` accepts `date` and rejects with `403` if the date is in a closed Z-Report day
- `GET /daily-close-preview/batch` now returns the missing `fund_transfers_today` list, so batch Z-Reports finally show capital injections
- Per-row `date` field added to both single-day and batch `fund_transfers_today` payloads; Close Wizard renders it next to each transfer
- **Post-creation edit**: new `POST /fund-transfers/{id}/edit-date` endpoint (PIN-gated, `modify_payment` action) lets managers fix a wrongly-dated injection; same closed-day guards on old + new date; `date_edit_history` audit trail on the transfer doc. UI: CalendarDays "Date" button on every row of Recent Transfers in Fund Management, opens a dedicated dialog.

### 2. Offline Sales Receipt Numbering Standard
- New helper `getNextOfflineReceiptNumber(prefix, branchCode)` in `/app/frontend/src/lib/offlineDB.js`
- Format: `{PREFIX}-{BRANCH_CODE}-OFF-{6-digit local seq}` e.g. `SI-MN-OFF-000001`
- Per-device counter persisted in IndexedDB `meta` store — different sequence from online, zero collision risk
- Offline sales attach `invoice_number` before `addPendingSale()`; `/sales/sync` already preserves it (no renumbering)
- Fallback: if a sale starts online and falls back to offline due to network error, a number is minted at the fallback point
- Pre-invoice signature dialog no longer shows `(pending)` in offline mode — uses the real offline number

### 3. Payment History — Change Date Feature
- New backend endpoint: `POST /api/customers/{customer_id}/edit-payment-date`
- PIN-gated (manager PIN, action_key=`modify_payment`), audit-logged via `date_edit_history` array appended to the payment record (from, to, reason, edited_by, authorized_by, edited_at)
- Guards: cannot edit voided payments; both the original and the target date must be open (not in a closed Z-Report)
- UI: `CalendarDays` "Date" button added to both customer-level payment history rows and the global Payment History table (data-testid=`edit-payment-date-{payment_id}` and `hist-edit-date-{i}`)
- Reusable `edit-payment-date-dialog` with old date display, new date picker, optional reason, manager PIN

**Testing**: Iter 191 testing agent — 11/11 pytest backend PASS, frontend UI smoke confirmed (customer-row + global-history "Date" buttons open the dialog; FundTransferDialog date-closed validation visible).


## Feb 2026 — Sales Page Perf Pass: Cache + Debounce (Iter 222)

**User reports**:
- "5–10 second wait every time I leave Customers and come back to Sales"
- "Product search won't search fast"
- "On the 3rd sale (~20 lines) the lag is real" — particularly in Order Mode

**Two surgical changes:**

### 1. Stale-while-revalidate cache (5–10s → ~1s)
- `loadData()` in `UnifiedSalesPage.js` now reads `getProducts() / getCustomers() / getPriceSchemes()` from IndexedDB FIRST and renders immediately
- Then fires the network refresh (`/sync/pos-data` + 5 other endpoints) in the background, swapping React state when it returns
- Previously: every navigation to /sales-new blocked the UI on 6 parallel awaits
- After: usable in <100ms, prices/stock refresh silently in the background

### 2. Search debounce + pre-computed index (per-keystroke 100ms+ → 25ms)
- Added a 150ms-debounced `debouncedSearch` state — heavy filter only runs after typing pauses
- Added a `useMemo`'d `productIndex` that pre-computes lowercase name/sku/barcode + pre-split nameWords/words arrays per product, ONCE per `allProducts` change (not per keystroke)
- Strict + fuzzy passes now iterate the pre-built index instead of running `.toLowerCase()` + 2 regex splits per product per letter
- All search semantics preserved: strict prefix-match ranking, Levenshtein fuzzy fallback, short-numeric prefix-of-word rule

**Files changed:**
- `/app/frontend/src/pages/UnifiedSalesPage.js` (loadData rewrite + debouncedSearch + productIndex + filter useEffect refactor)

**Verified by `testing_agent_v3_fork` (iter 188):**
- Cold load: 1.66s
- Warm paint from cache (after /customers round-trip): 1.07s
- Keystroke latency: 21–25ms (down from ~100ms+)
- Search regression suite: PASS (strict, fuzzy, numeric, empty)
- Checkout flow: PASS (no regression)

## Feb 2026 — Timezone-Aware Timestamp Display (Iter 221)

**Bug**: A sale created at 3pm Philippine time was showing as "07:00" (early morning) on the cashier wallet movements list. Same anti-pattern affected ~30 places across the frontend.

**Root cause**: Frontend was naively slicing UTC ISO strings without converting to local time:
- `m.created_at?.slice(0, 16)?.replace('T', ' ')` → for a 3pm PH sale stored as `2026-02-03T07:00:00+00:00`, this displayed `"2026-02-03 07:00"` — pure UTC wall-clock, no conversion.

**Fix**:
- New centralized helpers in `frontend/src/lib/utils.js`:
  - `fmtDateTime(iso)` → returns `YYYY-MM-DD HH:MM` in the org's configured timezone (read from `localStorage['agribooks.org_tz']`, falls back to browser-local).
  - `fmtDate(iso)` → returns `YYYY-MM-DD` (passes through plain `YYYY-MM-DD` strings unchanged so `order_date` / `purchase_date` aren't double-converted).
  - `fmtTime(iso)` → returns `HH:MM`.
  - All helpers tolerate null / undefined / non-ISO inputs without throwing.
- Replaced ~30 instances of `.slice(0, 16).replace('T', ' ')` and `.slice(0, 10)` patterns on UTC `*_at` fields across:
  - `pages/`: FundManagementPage, UnifiedSalesPage, BranchTransferPage, PurchaseOrderPage, ViewReceiptsPage, AuditCenterPage, ApproveTransferPage, JournalEntriesPage, CustomersPage, IncidentTicketsPage, InternalInvoicesPage, SuppliersPage, DocumentsPage, SettingsPage, terminal/TerminalPOCheck, terminal/TerminalTransfers
  - `components/`: ReceiptGallery, InvoiceDetailModal, TransferDetailModal, ExpenseDetailModal, ReviewDetailDialog, CustomerDedupeManager
- The org timezone is fetched from `/api/settings/timezone` on login and cached locally; default `Asia/Manila` for new orgs.

**Why**: A 3pm sale appearing as 07:00 on audit logs is a serious correctness/compliance bug — staff can't trust timestamps for shift accounting, theft investigation, or close-day reconciliation. This fix is also a one-time correctness payoff: any future date/time display added via the helpers will be timezone-correct by default.

## Feb 2026 — Price Match Double-Click Fix + Signature Bypass Visibility (Iter 220)

**Price Match Modal — single-click confirm fix:**
- `UnifiedSalesPage.js` PriceMatchModal `onConfirm` previously called `setTimeout(() => openCheckout(), 30)` after `setPriceMatchApproved(...)`. The setTimeout closure captured the *stale* `openCheckout` function from the prior render where `priceMatchApproved` was still `null`, so the modal would close, then re-trigger checkout, see no approval, and re-open. Result: user had to click "Update Branch Price" twice.
- Fix: replaced the `setTimeout(...)` with inlined post-approval transitions (`setPaymentType('cash')`, `setAmountTendered(grandTotal)`, `setPartialPayment(0)`, `setReleaseMode('full')`, then `setCustSaveDialog` or `setCheckoutDialog` directly). The price-match approval now flows in a single click, no closure race.

**Signature bypass button — high-visibility restyle:**
- `RequestSignatureDialog.js`: the "Customer can't sign? Manager bypass" button used `text-xs text-slate-500` — barely visible to elderly customers / low-vision staff. Now styled as a full-width amber outline button (`border-2 border-amber-400 bg-amber-50 text-amber-800 font-semibold py-2.5`) labeled **"Customer can't sign? Skip with Manager PIN"** with a `ShieldAlert` icon. Same restyle applied to the inline-terminal mode bypass button (now `Skip (PIN)` outline button, `h-10`).

**Files changed:**
- `/app/frontend/src/pages/UnifiedSalesPage.js` (lines 4649-4690 — onConfirm rewrite)
- `/app/frontend/src/components/RequestSignatureDialog.js` (lines 294-310 QR-mode + lines 350-360 inline-mode bypass buttons)


## May 2026 — Global & Customer Payment History + Close Wizard (Iter 204)

**Global Payment History Tab on /payments:**
- New "History" tab vs "Customer Payment" tab at top of /payments page
- `GET /api/payments/history` backend endpoint — filters: date range, branch, method, customer search
- Method breakdown chips (Cash: ₱X | GCash: ₱Y | Discount: ₱Z | Total: ₱N)
- Table: Date, Customer, Invoice #, Type, Method, Amount, Reference, Recorded By
- Customer name clickable → opens per-customer payment history modal

**Customer Payment History in /customers:**
- Clock icon button on each customer row → dedicated Payment History modal (7-column table + total row)

**Close Wizard Step 3 Enhancements:**
- Added Method column to AR payments table
- AR payment method breakdown chips
- Interest & Penalty Invoices Created Today section

**Close Wizard Z-Report (Step 7) Enhancements:**
- Interest collected + discount annotations under AR Cash Payments line
- Enhanced AR Collections section with method chips + per-payment interest annotation + discount footer
- Interest & Penalty Invoices Issued Today section

**Backend daily-close-preview additions:** `interest_invoices_today`, `ar_payment_by_method`, `ar_interest_collected`, `ar_discount_today`

## May 2026 — Payments Page Improvements (Iter 203)

**Ask**: "Disable auto-generate interest, add manual interest input, make discount inputable with PIN, and add payment history editing."

**1. Manual Interest Generation (auto-generate removed):**
- Removed auto-INT-invoice creation at payment time (was silently creating interest invoices before applying payment)
- Interest invoices are now created **only** when the user explicitly clicks:
  - "Generate INT" button → auto-computes from terms & due dates (existing behavior, renamed from "Force INT")
  - Manual ₱ input field + apply → creates an INT invoice with any user-specified amount (`manual_amount` param on backend)
- Backend: `POST /customers/{id}/generate-interest` now accepts `manual_amount` to skip computation

**2. Interest Discount with Manager PIN:**
- Discount column on interest/penalty invoices now requires **manager PIN** when applying payment
- Frontend shows a PIN prompt dialog before submitting if `totalDiscount > 0`
- Backend: `POST /customers/{id}/receive-payment` validates `discount_pin` via `verify_pin_for_action("interest_discount")`

**3. Payment History — Edit Before Z-Report:**
- Payment history now shows `payment_id`, `invoice_id`, `voided` status, and `is_closed` (Z-Report guard)
- Each non-voided payment has an "Edit" button (disabled with "Closed" badge if the date is in a Z-Report)
- Edit dialog: change amount + enter reason + manager PIN
- Backend: `POST /customers/{id}/modify-payment` — atomically voids the old payment (reverses wallet + AR) and applies a new one with the corrected amount. Blocked if the payment date is in `daily_closings`.


## May 2026 — PIN Session (3-Minute Warm Window) (Iter 203)

**Ask**: "Our sales have too many PIN gates. Once the manager inputs the PIN, continue directly for the next few actions within the same sale — only stop if a different authority level is required."

**User choices**: 3-minute TTL, per-sale scope (resets on cart clear), visual badge.

**Architecture** — `UnifiedSalesPage.js`:
- New `pinSession` state: `{ pin, method, name, at }` — cached plaintext PIN in memory only (never persisted).
- `startPinSession(pin, method, name)` — called on every successful PIN verification.
- `isPinSessionWarm()` — checks if session exists and `Date.now() - at < 3min`.
- `clearPinSession()` — called in `clearCart()` (per-sale scope) and on any failed auto-bypass.
- `pinSessionRef` — ref mirror for non-stale reads in async functions.

**Full auto-bypass** (dialog not shown when warm):
- Credit Approval (online) — calls `verifyManagerPin(sessionPin)` silently; on fail → clears session, shows dialog as fallback.
- Capital Reveal — validates via `/products/cost-details` with cached PIN; on fail → shows dialog.
- Insufficient Stock Override — calls `handleStockOverride(sessionPin, saleData)` directly (passes `saleData` to avoid React state timing issue).
- JIT Repack Retail — feeds cached PIN directly to `processSale(null, pin, sig)`.
- Discard Park (other-cashier) — calls `discardParkedSale(id, { pin })` directly.
- E-Payment Verify — uses cached PIN instead of `prompt()`.

**Auto-fill PIN** (dialog still shown for additional inputs):
- Credit Approval (offline) — auto-fills `managerPin` state via `useEffect`, user still enters bypass reason.
- Price Match Modal — new `warmPin` prop auto-fills the PIN field; green "PIN auto-filled" indicator.
- Void Sale — auto-fills `voidPin` when dialog opens.
- Late Encode — new `warmPin` prop auto-fills PIN; user still enters reason.

**Visual badge** — `PinSessionBadge` component:
- Emerald pill with 🔓 icon: "PIN active · Manager Name · 2:45"
- 1-second countdown timer; auto-clears on expiry.
- Positioned in the Sales header right section.

**PIN session starts** on successful: Credit approval, Void, Capital reveal, Stock override, Price Match confirm, Discard park, E-payment verify.

**No backend changes.**


## May 2026 — Order Mode Input UX: Tab-Select + Leading Decimal (Iter 203)

**Ask**: "When I press Tab on quantity and price in Order mode, I want it to select all text so I can type a new value immediately. Also let me type `.5` or `.05` without having to type `0.5` first."

**Changes — `UnifiedSalesPage.js`:**

**Order mode (lines table):**
- Qty, Rate, and Discount inputs changed from `type="number"` to `type="text" inputMode="decimal"` — allows `.5` entry on all browsers.
- Added `onFocus={e => e.target.select()}` — Tab now selects all text in the field.
- String-intermediate pattern (`_quantityStr`, `_rateStr`, `_discount_valueStr`) — preserves transient strings like `.`, `0.`, empty while typing, finalized to numeric on blur via `finalizeLineField()`.
- `updateLine()` refactored to handle numeric field intermediates (mirrors Quick mode's existing `setCartQty` pattern).

**Quick mode (cart):**
- Price input also upgraded from `type="number"` to `type="text" inputMode="decimal"` with `_priceStr` intermediate + blur finalize (qty was already done previously).
- `updateCartPrice()` updated to match the same intermediate-string logic.

**No backend changes.**


## Feb 2026 — Offline Price Match (Iter 202)

**Ask**: "I want to do the price match even when offline. It only saves the reason offline first and eventually syncs when the internet is back. We require PINs for this regardless in Quick or Order — so we cache the admin and manager PINs to make it work even offline."

**Implemented (frontend + backend + regression):**

**Frontend** — `UnifiedSalesPage.js`:
- Removed the offline-block in `updateCartPrice` (Quick mode) and `updateLine` rate edit (Order mode). Cashiers can now edit prices in cart even with no connection.
- `PriceMatchModal.onConfirm` (`UnifiedSalesPage.js`) now performs a local PIN check via `verifyOfflinePin` (already-cached bcrypt admin hash + branch-scoped manager grants) when `!isOnline` BEFORE accepting the modal. If the PIN is wrong, the modal stays open with an inline error — exactly like the online flow. The plaintext PIN still ships with the queued sale so the server can re-validate on resync.
- `components/PriceMatchModal.js`: added an inline "Offline — PIN is verified locally. The price change will sync when your connection is back" banner so the cashier knows what's happening.

**Backend** — `routes/sync.py` `/sales/sync` endpoint:
- After importing the offline invoice, if the sale carries `price_changes` + `price_match_pin`, the endpoint now re-verifies the PIN against the CURRENT bcrypt hash via `verify_pin_for_action("price_match")`.
- On success: upserts `branch_prices` (skipped when `customer_only=True`, matching the live flow) and writes the audit row to `price_change_log` with `offline_origin=True`, `scope`, `customer_only`, and the same approver metadata.
- On PIN-resync failure (rare — only if the admin/manager PIN was rotated between the offline write and the resync): the sale STILL imports (the goods are already gone), the branch_prices update is silently skipped, and the audit row is flagged `pin_resync_failed=True` so admins can review under Reports → Price Changes.

**Test** — `backend/tests/test_offline_price_match_replay_202.py` (3/3 passing):
1. Branch-permanent replay → `branch_prices.retail` updated to the new price.
2. Customer-only replay → `branch_prices` untouched (audit row still written).
3. Invalid-PIN replay → sale imports, `branch_prices` untouched, log carries `pin_resync_failed=True`.


## Feb 2026 — Order-Mode Product Search: Offline Fallback (Iter 202)

**Ask**: "I tried going offline on Quick mode and the product search worked perfectly, fully offline. My problem is in the advance order page (Order mode) — the product search function disappears."

**Root cause**: `SmartProductSearch.js` (used in Order mode and other places) called `api.get('/products/search-detail')` and on error just set `results=[]`. When `navigator.onLine` mistakenly reports `true` while there's no real connection (well-known browser false-negative), the response interceptor in `AuthContext.js` doesn't fire its IndexedDB fallback, so the request fails silently with empty results. To the cashier this looks like the search "disappears". Quick mode never had this issue because it filters `allProducts` (already in memory) entirely client-side.

**Fix** — `SmartProductSearch.js`:
- New `searchProductsLocal(query, branchId)` helper that mirrors Quick mode's logic exactly — token-AND with the short-numeric prefix rule, then a Levenshtein fuzzy fallback on tokens ≥4 chars at distance 1–2. Reads from `getProducts()` / `getInventoryItem()` / `getBranchPrice()` in IndexedDB and enriches each result with the same shape the API returns.
- The fetch effect now tries the API first; on ANY error (offline, server error, browser misreporting `onLine`) it falls back to `searchProductsLocal()` automatically.
- New offline indicator chip at top of the dropdown: amber bar with WifiOff icon — "Offline — searching cached products. Stock & prices may not reflect the latest changes." — so the cashier knows the data is from cache.
- Local fuzzy hits set the same "Did you mean" chip locally (no more silent zeros for typo'd queries while offline).


## Feb 2026 — Unsaved-Changes Leave Guard (Iter 202)

**Ask**: "I was encoding a sales with over 200 lines already, my mouse suddenly pressed Sales History — it went directly there making my effort all gone. I want a popup: 'Are you sure you want to leave?' on every form where I might lose work."

**User choices**: All tiers, smart per-page dirty checks, **Park & leave** option for Sales specifically, beforeunload warning for tab close.

**Architecture** — `<BrowserRouter>` (legacy declarative router, not a data router) so `useBlocker` was unavailable. Built a custom global guard instead:

- **`contexts/UnsavedChangesContext.js`** (new) — Provider with:
  - In-memory registry of guards keyed by id; pages register via the hook.
  - Document-level click capture interceptor catches every `<a href="…">` click. If any registered guard returns `isDirty=true`, prevents default + opens the confirmation dialog. Properly bypasses external/mailto/tel/`target="_blank"`/Cmd-Cmd-click links and same-route clicks.
  - `beforeunload` listener fires the browser's native warning when any guard is dirty (tab close / refresh).
  - `requestSafe(action)` API for in-page non-route destructive switches like the Sales `New Sale ↔ History` tab toggle.
  - Renders `<UnsavedChangesDialog>` itself once at the Provider level so pages don't have to mount it.
  - `ctxValue` is `useMemo`'d (without this we hit infinite render loops; surfaced + fixed during testing).

- **`lib/useUnsavedChangesGuard.js`** (new) — single-effect hook that registers `{getDirty, onPark, label}` with the Provider. Uses a `latestRef` and getter-style `onPark`/`label` so pages that inline arrow functions for `onPark` don't trigger re-registration on every render.

- **`components/UnsavedChangesDialog.jsx`** (new) — three-button dialog: **Stay** (default), **Park & leave** (only when `onPark` is provided — Sales path), **Leave & lose changes**.

**App.js** wires `<UnsavedChangesProvider>` between `<AuthProvider>` and `<AppRoutes>` so it has access to `useNavigate`.

**Pages wired** (all use the same hook):
- `UnifiedSalesPage.js` — `salesGuard` with `onPark={autoParkForGuard}` (auto-named "{customer} · 2:34 PM — auto-saved on leave"). Tab buttons (`tab-new-sale`, `tab-history`) wrapped in `salesGuard.requestSafe(() => setMainTab(...))`.
- `PurchaseOrderPage.js` — guard fires when any line has `product_id`.
- `BranchTransferPage.js` — fires when any reqRow has a chosen product.
- `ReturnRefundWizard.js` — fires when `returnItems.length > 0`.
- `JournalEntriesPage.js` — guard intentionally NOT gated on `createOpen` (radix Dialog's overlay click closes the modal before our document-level capture runs, so we key purely on form content; second sidebar click then triggers correctly).

**Testing** — three E2E iterations:
- iter_178 → caught `useBlocker must be used within a data router` invariant on every page; pivoted architecture to click-interceptor.
- iter_179 → caught "Maximum update depth exceeded" infinite render loop (Provider value not memoized); testing agent applied `useMemo` fix.
- iter_180 → architecture validated, Sales primary path (add product → tab-history → Stay/Park/Leave) fully passing. Secondary pages mount-clean. JE wiring tweaked (drop `createOpen` from isDirty).


## Feb 2026 — Park Resume = Atomic Consume (Iter 202)

**Ask**: "I parked an invoice and opened it. While editing, I noticed the parked invoice was still in the list — confusing because it makes me think the customer didn't return."

**Fix**: Resume now atomically consumes the park. The row vanishes from the branch list the moment the cashier reopens it.

**Backend** — `routes/parked_sales.py`: new `POST /api/parked-sales/{id}/consume` endpoint. Uses `find_one_and_delete` so fetch+remove is atomic. NO PIN required even when consuming another cashier's park (that's the whole point of the branch-shared model). Returns 410 Gone on second call so race conditions surface clearly instead of confusing 404s.

**Frontend** — `lib/parkedSalesSync.js`: new `consumeParkedSale(parkId)` helper. Online: POSTs `/consume` and removes the local row. 410 race → propagated. Offline: reads the local snapshot, queues a `pending_delete`, returns the snapshot — so resuming offline still works seamlessly.

**`UnifiedSalesPage.js`** `resumeParkedSale()` now calls `consumeParkedSale` first, falls back to the local cached snapshot on 410/network error, and refreshes the parked list so the dialog reflects the new state.

**Test** — extended `test_parked_sales_202.py` with two new cases (now 6/6 passing):
- `consume_atomic_fetch_and_delete` — POST returns snapshot, list no longer contains it, second consume → 410.
- `consume_works_across_users_no_pin` — admin consuming manager's park succeeds without a PIN.


## Feb 2026 — Price Match: "Skip change for now" customer-only scope (Iter 202)

**Ask**: "When we create sales that suddenly change price we usually have something called Price Match as the reason. Add a feature there saying it is meant for this customer only — do not replace current price."

**Refined**: "Make it per-receipt, not per-line. We ask the reason for the discount, ask to update the price because of the reason, OR skip change for now. Both still need PIN management."

**Implemented:**

**`frontend/src/components/PriceMatchModal.js`** (rewritten):
- One reason + one detail input per receipt (not per line) — single decision applied to every changed line.
- New **scope toggle** with two big tile-style buttons:
  - **Update branch price** (amber, default) — permanent, applies to all future sales at this branch (legacy behavior).
  - **Skip change for now** (blue) — applies to this sale / this customer only; branch catalog stays untouched.
- Confirm button colour and label change based on scope ("Update branch price" vs "Apply for this sale only").
- Both paths still require Manager / Admin PIN. The audit reminder banner makes it clear that both options are logged.

**`UnifiedSalesPage.js`**: passes `customerName={selectedCustomer?.name}` so the customer-only tile shows the customer's name when one is selected.

**`backend/routes/sales.py`**:
- `valid_price_changes` now carries a `customer_only` boolean per row (defaults to False for backward compatibility).
- Persist phase wraps the `branch_prices` upsert + `mark_price_reviewed` calls in `if not is_customer_only:` — branch catalog skipped entirely for customer-only overrides.
- `price_change_log` always written (audit trail intact) with two new fields: `customer_only` (bool) and `scope` ('customer_only' | 'branch_permanent').
- Admin notification text now reports the scope split: e.g. "2 branch permanent + 1 customer-only".

**Test** — `backend/tests/test_price_match_customer_only_202.py` (2/2 passing):
1. Customer-only flag → branch_prices unchanged after sale
2. Permanent (default) → branch_prices.retail updated to new price


## Feb 2026 — Park / Draft Sales (offline-capable) (Iter 202)

**Ask**: "I got stuck because a customer wanted to add a product from a far shelf and I had to pause; the next customer ended up waiting too long and left because I couldn't close my sale even though I was temporarily free. Add a Park button on /sales-new and a button to show all parked drafts. Must work offline too."

**Implemented (backend + frontend + regression):**

**Backend** — `routes/parked_sales.py` (new), wired in `main.py`, `parked_sales` added to `TENANT_COLLECTIONS` in `config.py`:
- `POST /api/parked-sales` — upsert (idempotent on `id`, so an offline outbox replay never duplicates).
- `GET /api/parked-sales?branch_id=…` — branch-shared list (so any cashier on duty can resume a colleague's park).
- `GET /api/parked-sales/{id}` — full snapshot.
- `DELETE /api/parked-sales/{id}?pin=…` — owner deletes for free; deleting another user's park requires a manager/admin PIN via `verify_pin_for_action("parked_sale.discard_other")`.
- Limits: 20 active parks per branch (409 on overflow), opportunistic auto-purge of any park older than 24 h on every list call (no extra cron job).

**Frontend offline architecture** — `lib/offlineDB.js` adds `PARKED_SALES` store (DB version bumped to 6); new `lib/parkedSalesSync.js` module orchestrates:
- `parkSale()` — saves locally first as `_sync='pending_create'`; if online, POSTs and flips to `_sync='synced'`. Offline parks are silently queued.
- `discardParkedSale()` — online deletes immediately; PIN errors propagate (don't queue). Offline marks `_sync='pending_delete'` so the row stays hidden but the deletion is replayed later.
- `loadParkedSales(branchId)` — drains the pending queue, fetches the server's canonical list, reconciles with local pending rows, returns the display list. Falls back to cache when offline so the dialog never goes blank.
- `drainSyncQueue()` — walks all pending rows on reconnect; clears 404s automatically (already-deleted on server).

**UnifiedSalesPage.js** — added two toolbar buttons next to Sync:
- **Park** (Pause icon) — opens a small dialog with item count, subtotal, optional custom note (e.g. "guy in red shirt"), and an offline banner when applicable. Auto-tags as `[Customer name|Walk-in] · 2:34 PM`.
- **Parked (N)** (Inbox icon) — opens a list dialog showing every park at the branch with mode badge (Quick/Order), creator, item count, subtotal, age, plus `Resume` and `Discard` actions. Other-cashier discards open a Manager-PIN sub-dialog.
- Resume rehydrates: cart/lines, customer, scheme, header, mode (Quick↔Order round-trip).
- Auto-loads on mount + auto-drains the offline queue on every `online` transition.

**Tests** — `backend/tests/test_parked_sales_202.py` (4/4 passing):
1. Create → list → resume → owner-discard happy path
2. Branch isolation (park at branch A invisible from branch B)
3. Repost with same `id` is idempotent (offline outbox safety)
4. Other-cashier discard without PIN → 403


## Feb 2026 — Sales-new Order-Mode Search + Cart UX (Iter 202)

**Ask**: "/sales-new Order-mode search isn't as accurate as Quick mode. Arrow-down doesn't keep the highlighted result visible — useless when products have similar names like Galimax 1, 2, 3, 4, 5. Also, the Quick cart doesn't auto-scroll, so the latest add isn't visible without manual scrolling."

**Fix** (backend + frontend + regression):
- **`backend/routes/products.py`** — `/products/search-detail` now has a fuzzy fallback. When the strict token-AND pass returns 0 hits AND the query has a fuzzable token (≥4 chars, alpha), it runs **rapidfuzz** (`token_set_ratio` ∨ `partial_ratio`) at an 80% threshold against `name` over up to 1000 active candidates. Numeric / short tokens stay strict so "1 kg" never silently swaps to "2 kg". When fallback fires, every result item is tagged with `_fuzzy_hint = {query, count}` so the dropdown can render a "Did you mean" chip without a response-shape change.
- **`frontend/src/components/SmartProductSearch.js`** — (a) Reads `_fuzzy_hint` from the first result and renders an amber sticky "No exact match — showing closest matches" chip at the top of the dropdown. (b) Pre-selects index 0 on every new query so Enter immediately picks the top match. (c) Switched `scrollIntoView({ block: 'nearest' })` to `'center'` with smooth behavior — guarantees the highlighted row is always visible regardless of the expanded detail card height. (d) Hover now updates active index too, so mouse + keyboard agree on what's selected.
- **`frontend/src/pages/UnifiedSalesPage.js`** — Added `cartEndRef` / `orderLinesEndRef` anchors. `addToCart()` (Quick) and `handleProductSelect()` (Order) both auto-scroll to the newest item via double-RAF after state commits, so the latest add is always visible for price/discount verification.
- **Dependency** — `rapidfuzz==3.14.5` added to `backend/requirements.txt`.
- **Test** — `backend/tests/test_product_search_fuzzy_202.py` (4/4 passing):
  1. Strict match returns no `_fuzzy_hint`
  2. Typo "Promex" fuzzy-matches "Promix Starter" with hint
  3. Garbage query returns []
  4. "Galimax 1" MUST NOT fuzzy-match "Galimax 2" (numeric strict guard)


## Feb 2026 — Per-Branch Close-Day SMS Opt-Out (Iter 201 cont'd)

**Ask**: "Some branches are merely used to transfer stocks, not actual money movement / not credit. Owner handles them personally. If I keep receiving texts for those branches, it becomes noise and I might ignore real alerts."

**Fix** (backend + frontend + regression):
- **`backend/routes/sms.py`** — new `PUT /api/sms/close-reminder/branch-toggle/:branch_id` endpoint. Body `{disabled: bool}`. PIN-equivalent via `check_perm("settings", "edit")`.
- **`backend/routes/close_reminder.py`** — `tick_once()` now checks `branch.close_reminder_disabled` as its first per-branch filter and skips the whole branch before looking at any stage. Added `skipped_disabled` counter to the tick summary and to the `close_reminder` log line. `diagnose_for_org()` now surfaces the flag so admins can confirm state in the diagnostics endpoint.
- **Scope** — only automated stages (approaching-close, overdue, day-after recap) are muted. Z-Report summaries (fired when the user actively closes the day) are NOT muted — the owner opted into that moment by closing.
- **`frontend/src/components/sms/TeamSmsRemindersCard.js`** — each row in "Branch Closing Times" now has a Mute / Un-mute pill button plus a muted badge on the branch name. The time input + Save button auto-grey out while muted. A small italic hint explains what muting does.
- **Test** — `backend/tests/test_branch_close_reminder_opt_out_201.py` (3/3 passing):
  1. HTTP handler flips the flag both ways and persists it
  2. `tick_once()` skips the muted branch (monkey-patched dispatch verifies ZERO calls for the muted id)
  3. `diagnose_for_org()` exposes the flag


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

## Iter 218 — Feb 2026 (Per-Unit Discount + Permission Gating Audit)

### Fixes
- **Sales (Order Mode) amount discount now per-unit × qty** — previously ₱5 discount with qty 10 = only ₱5 off the line; now correctly applies ₱5 × 10 = ₱50 off. Percent discounts unchanged.
  - Backend: `routes/sales.py` line 454 — `disc_amt = round(qty * disc_val, 2)` for amount type.
  - Frontend: `UnifiedSalesPage.js` `lineTotal()` and payload `discount_amount` now multiply by qty.
  - UI: Discount column header updated to "Discount /unit" for clarity.
- **`/api/inventory/admin-adjust` now permission-gated** — previously had no auth check beyond optional PIN. Now requires admin role OR `inventory.adjust` permission (HTTP 403 otherwise).
- **Products page UI permission gating tightened**:
  - "Add Product" button hidden if `!canCreateProduct`
  - "Quick Repack" + per-row Link2 repack button hidden if `!canCreateProduct`
  - Pencil edit button now shows Eye icon and opens **read-only** dialog (fieldset disabled, Save hidden, read-only banner shown) if `!canEditProduct`
  - Inventory Correction accordion inside edit dialog now hidden if `!canAdjustInventory`

### Tests
- NEW: `tests/test_perunit_discount_and_perm_gates_218.py` — 6 tests (all pass)
- NEW: `tests/test_perunit_discount_edges_218.py` — 5 edge-case tests authored by testing agent (all pass)

### Remaining Pages to Audit (backlog)
- `SuppliersPage.js`, `AccountingPage.js`, `InventoryPage.js` — do not destructure `hasPerm`; all backend mutations are gated, but UI buttons shown regardless of permissions.

## Iter 219 — Feb 2026 (Broader UI Permission Gating Audit)

### Scope — Extended the Iter 218 gating pattern to 3 more pages

**SuppliersPage.js**
- Destructured `hasPerm` from `useAuth`; added `canCreateSupplier`, `canEditSupplier` flags.
- Gated "New Supplier" btn, "Edit" (in supplier details panel), "Save as Supplier" btn.

**AccountingPage.js**
- Destructured `hasPerm`; added `canCreateExpense`, `canEditExpense`, `canReceivePayment` flags.
- Gated top-right actions: Customer Cash Out, Farm Expense, Record Expense.
- Gated per-expense row controls: Edit pencil, Upload receipt, Verify, Delete.
- Gated Receivable "Record Payment", Payable "Record Payable", Payable "Record Payment".

**InventoryPage.js**
- No changes needed — was already gating "Mark All Reviewed" via `hasPerm('products', 'edit')`.

### Tests
- Testing agent performed 2-pass Playwright (admin + seeded limited-perm manager) verification.
- 0 UI/integration/backend issues. Admin sees everything; limited manager sees no gated buttons.
- NEW: `tests/seed_limited_manager_iter219.py` (testing-agent authored) seeds a perm-restricted manager.

## Iter 225 — Feb 2026 (Critical: Terminal Price Drift Fix)

### Bug
Cashier ringing up "Galimax 1" on the paired Terminal saw ₱2065 while the
web Sales (`/sales-new`) showed ₱2075 — the freshly-encoded branch retail.
Root cause: the Terminal performs **delta** syncs against `/sync/pos-data`.
When an admin edits only the per-branch price (writes `branch_prices`), the
master `products` document is untouched, so its `updated_at` does not change.
The delta sync therefore emitted only the new `branch_prices` row but not the
product itself — and `TerminalSales.jsx` reads `product.prices[scheme]`
directly, never re-merging branch overrides at runtime. Result: terminal kept
its stale, global cached price. **Data-integrity hazard:** every per-branch
retail change written via the web after the first full sync was invisible to
the terminal until the cashier manually forced a full resync.

### Fix
- `backend/routes/sync.py` (`/sync/pos-data`): on delta + branch context, also
  pull products whose `branch_prices` row was updated since `last_sync` (and
  any repack children whose parent's branch cost changed) and append them
  into the `products[]` payload **before** the existing enrichment loop.
  The merge logic at lines 213-222 then injects the override correctly so the
  terminal upserts the cached row with the new merged retail.

### Tests
- NEW: `tests/test_branch_price_delta_sync_225.py` — fails on the old code,
  passes after the fix. Reproduces the exact "global 2065 vs branch 2075"
  scenario end-to-end.

## Iter 226 — Feb 2026 (QuickBooks-Style Inline Calculator)

### Feature
Every monetary/quantity input now supports a QuickBooks-style inline calculator.
Type a number, press +, −, *, or / and a "tape" bubble appears under the field
showing the live expression and a running total. Press Enter to commit. Escape
to cancel. Invalid expressions silently revert. Backspace strips characters.

User-confirmed scope:
- Operators: + − * / only ("safest")
- Trigger: only after a number is typed and the user presses an operator
- Aesthetic: dark monospace bubble below the field with `1500+250 = 1750  ↵ commit · Esc cancel`
- Must work fully offline (no backend, no eval) — used by Terminal POS

### New
- `frontend/src/components/CalcInput.jsx` — drop-in for shadcn Input.
  - Pure-JS shunting-yard parser (no `eval`, no `Function()`, no deps).
  - Plain mode = controlled text input. Operator key snapshots current value
    and switches to expression mode.
  - 19/19 unit cases pass (digits, precedence, unary minus, div-by-zero,
    garbage, trailing operator, etc.).
  - Props: `value`, `onChange(stringValue)`, `onBlur`, `onKeyDown`, `onFocus`,
    `placeholder`, `disabled`, `selectOnFocus`, `integerOnly`, `data-testid`,
    `className` (matches shadcn Input defaults).
  - Exposes `safeEval` for tests.

### Phase 1 wiring (highest-impact monetary/qty inputs)
- `pages/terminal/TerminalSales.jsx` — discount, amount-tendered, split-cash,
  split-digital, scan-qty (integerOnly).
- `pages/UnifiedSalesPage.js` — Quick-mode cart qty + price, Order-mode line
  qty + rate + discount_value, freight, overall discount, amount-tendered,
  partial amount, split cash/digital.
- `pages/PaymentsPage.js` — receive-amount, edit-payment-amount,
  manual-interest-input, payment-row-* (per invoice), discount-row-* (per
  invoice), rate-prompt-input, interest-rate-input.
- `pages/ExpensesPage.js` — record-expense, farm-expense, customer-cash-out,
  employee-advance amounts.
- `components/FundTransferDialog.js` — transfer/capital-injection amount.
- `components/StockInjectionDialog.js` — quantity.

### Tests
- Reusable smoke at `frontend/src/components/__tests__/CalcInput.smoke.mjs`.
- Live Playwright validated by testing agent (iteration_192) on multiple flows:
  Cash Out: typing `1500+250` then Enter committed `1750`; Escape reverted;
  garbage was silently rejected; calc-active=1 toggled correctly.

### Phase 2 (next, awaiting user green light)
- `PurchaseOrderPage.js` — line qty/cost, freight/discount, terms days.
- `BranchTransferPage.js` — line qty/cost rows.
- `RepackPricingPage.js`, `PriceManagerPage.js` — price/cost editors.
- `CountSheetsPage.js` — physical count entries.
- `CloseWizardPage.js` — cash-count denominations.
- Misc dialogs in `BranchCapitalWizard`, `ReturnRefundWizard`, etc.

## Iter 226 (Phase 2) — Feb 2026 (Calc Sweep Complete)

Following Phase 1 (15 inputs in the daily-money pages), a Python codemod
swept the entire frontend and converted ~140 more numeric inputs to
`<CalcInput>`.

### Codemod (`/app/scripts/calc_codemod.py`)
- Regex-driven, conservative: only matches `<Input type="number" …/>` and
  `<input type="text" inputMode="decimal" …/>`.
- Drops attrs CalcInput owns: type, inputMode, min, max, step, onWheel.
- Rewrites `onChange={e => setX(e.target.value)}` → `onChange={(__cv) => setX(__cv)}`
  using parameter name `__cv` to avoid colliding with inner `const v`/`let v`.
- Auto-inserts `import CalcInput from '<rel>'` at the right place (handles
  multi-line `import { … } from …` blocks correctly).
- 100 % idempotent — re-running it on already-migrated files is a no-op.

### Phase 2 files swept (≈40)
Sales / POS / Inventory: PurchaseOrderPage, BranchTransferPage, CountSheetsPage,
RepackPricingPage, PriceManagerPage, ProductsPage, ProductDetailPage,
ApproveTransferPage, PendingReleasesPage, terminal/TerminalPOCheck,
terminal/TerminalTransfers, TerminalReturnRefundModal,
TerminalUpdateReceiptModal.
Money / accounting: AccountingPage, PaySupplierPage, JournalEntriesPage,
CloseWizardPage, BranchCapitalWizard, BudgetChecker.
Customers / suppliers / employees: CustomersPage, EmployeesPage,
CropCreditsPage.
Misc: SetupWizardPage, DailyLogPage, MessagesPage, IncidentTicketsPage,
PriceSchemesPage, BarcodePrintPage, SuperAdminPage, ReturnRefundWizard,
AuditCenterPage, DocViewerPage, AuthDialog, ExpenseDetailModal,
ReviewDetailDialog, audit/ReserveTab, PriceScanManager, InvoiceDetailModal.

### Manual fixes after codemod
- `PaySupplierPage.js:548` — renamed inner `const v = …` to use `raw` for
  the arrow param so it doesn't shadow.
- `ProductsPage.js:1358 & 1369` — same shadowing fix.
- `PendingReleasesPage.jsx:14` — relocated mis-injected import from inside a
  multi-line `import { … }` block.

### Verification (test report iteration_193)
- 0 compile errors, 0 console errors across 15 touched pages.
- Operator-key trigger + Enter-commit confirmed on PurchaseOrder,
  BranchTransfer, PriceManager, PaySupplier.
- Escape-revert and double-op collapse confirmed.
- `data-calc-active` toggles 0 → 1 → 0 correctly.

### Status
The QuickBooks-style inline calculator is now LIVE on every numeric input
across the application. Total: ~155 inputs migrated.

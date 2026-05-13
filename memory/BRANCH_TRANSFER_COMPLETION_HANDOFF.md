# Branch Transfer Overhaul — Completion Handoff / Runbook

**Status as of Feb 13 2026:** All planned phases (0, 0.5, 1, 1.1, 2, 2.1, 3, 3.1) complete and green. Full business-regression suite at **128/128 pass / 394 rows / 0 fail / zero DB footprint**. Frontend build clean. Deployable.

---

## 1. Executive Summary

### What is now supported, end-to-end
A complete, audit-grade inter-branch stock workflow:

1. **Branch A requests stock** from Branch B (or "Main").
2. **Branch B reviews & confirms** the requested quantities — line-by-line — declaring *intent* without moving stock yet. Confirmation works **both from the web dashboard AND from a QR scan on mobile** (so the warehouse-floor person doesn't have to walk back to a PC).
3. **Branch B (source) generates a Branch Transfer Order (BTO)** prefilled with the approved quantities. The originating stock request flips to *in_progress* and is locked from cancellation.
4. **BTO is sent** — no stock movement yet.
5. **Branch A receives** — `receive_transfer` (exact match) is the **single mutator** of actual inventory.
6. **If there's a variance**, Branch A files a discrepancy. The receipt becomes `received_pending` and **does NOT move stock**.
7. **Owner accepts the variance** with a PIN/TOTP gate (`transfer_variance_accept`, hard threshold ≥ ₱5,000). Acceptance is the second valid mutator path and is symmetrical to the exact-match path.
8. **Internal invoice is rewritten** to reflect the *actually-received* quantities, so AR/AP books match the warehouse. Failure to write the invoice never silently corrupts state — it raises an incident ticket and stamps `invoice_creation_failed=True`.

### Why this matters operationally
- **Zero stock fiction.** A "sent" transfer no longer subtracts stock at source before someone has confirmed receipt. Owners can stop reconciling phantom moves.
- **Single source of truth.** `approved_qty` (intent) and `qty_received` (reality) are stored separately. Variance investigations have a paper trail (`variance_history[]`, `request_approval_log[]`, `incident_tickets`).
- **Mobile-native confirmation.** Warehouse floor staff scan the QR on a request slip and confirm directly on the phone. No PC required.
- **Tenant-scoped by default.** Every BTO writer is now wrapped by a defensive `_assert_org_preserved` tripwire — a future refactor that breaks tenant scoping (the prior prepared-order class of bug) will 500 loudly instead of silently hiding orders.

### What is now safer than before
| Risk class | Before | After |
|---|---|---|
| Phantom stock movement on send | Possible | Impossible (`_apply_receipt` is the sole mutator) |
| Variance acceptance by manager | Allowed via role | Hard PIN/TOTP gate above ₱5,000; admin/TOTP only |
| Internal invoice + warehouse drift | Silent | Invoice rewritten on accept; silent-fail raises incident |
| Tenant-id wipe via PUT payload | Theoretically possible | Tripwire on 5 writers; 500s the request |
| Manager/cashier pushing a sale-price update via Smart Price | Soft check | Hard backend PIN policy `smart_price_update` (admin/TOTP only) |
| Repack capital drift after parent PO | Invisible until next sale | Surfaced inline in the Smart Price Scan capital-change tab |

---

## 2. Completed Phase Inventory

### Phase 0 — Stock Movement Invariants Baseline
- **Goal:** Lock down that inventory mutates **only** inside `_apply_receipt`.
- **Files changed:** Baseline tests only — production code untouched.
- **Behaviour added/fixed:** None (regression net).
- **Tests added:** `tests/business_regression/test_br_branch_transfer_invariants.py` — every life-cycle step verified to be stock-neutral except `/receive` (exact match) and `/accept-receipt` (variance accept).
- **Final result:** Foundation for every later phase — ✅ green.

### Phase 0.5 — Cancel Dead-end + Branch-Context Permissions
- **Goal:** Stop managers from cancelling cross-branch BTOs; tighten the source-branch enforcement for `send`, `update`, and `resubmit`.
- **Files changed:** `routes/branch_transfers.py` (send/update/resubmit gates), `routes/verify.py` (`cancel_po`, `transfer_approve` PIN policies).
- **Behaviour added/fixed:** Source-branch enforcement on `send_transfer` and `update_transfer`. PIN required on cancel.
- **Tests added:** `test_br_bt_permissions_and_pin.py` — covers cross-branch manager 403, admin override, wrong-branch PIN rejected, source-branch PIN accepted.
- **Final result:** ✅ green.

### Phase 1 — Stock Request Confirmation Layer (`approved_qty`)
- **Goal:** Let the supplying branch confirm *line-by-line* what they can actually fulfil before a BTO is generated. Capture this as `approved_qty` distinct from `requested_qty`.
- **Files changed:**
  - `routes/purchase_orders.py` — new `POST /api/purchase-orders/{po_id}/confirm-request` endpoint + `_apply_confirmation` helper.
  - `routes/purchase_orders.py::generate_branch_transfer_from_request` — prefills BTO from `approved_qty` when present.
  - `frontend/src/components/ConfirmQuantitiesDialog.jsx` — web modal.
  - `frontend/src/pages/BranchTransferPage.js` — wired into request cards.
- **Behaviour added/fixed:** Approval is intent-only — no stock moves. PIN policy `confirm_stock_request` (admin or source-branch manager). Excess approval requires a `note`. Approval persists `approved_by_name`, `approved_at`, `approval_status`, `approved_note`, `request_approval_log[]`.
- **Tests added:** `test_br_stock_request_approval.py` (18 cases) — reconfirm, no-duplicate-BTO, source-branch enforcement, generate-prefill, excess-requires-note, notification fired.
- **Final result:** ✅ green.

### Phase 1.1 — Approval Summary Chips on Request Cards
- **Goal:** UI feedback so the requesting branch can see at-a-glance what was approved/short.
- **Files changed:** `frontend/src/pages/BranchTransferPage.js` (chip rendering on request cards).
- **Behaviour added/fixed:** UI-only — total approved vs requested, short/excess chips, last-approver name.
- **Tests added:** None (UI only).
- **Final result:** ✅ green.

### Phase 2 — QR / Mobile Stock Request Confirmation
- **Goal:** Same confirmation flow but driven from a phone scanning the QR on the printed request slip — no PC needed.
- **Files changed:**
  - `routes/qr_actions.py` — new `POST /api/qr-actions/{code}/confirm_stock_request` with idempotent `confirm_ref` handling.
  - `routes/doc_lookup.py` — enriched `view_document_open` for branch-request POs (returns request lines + approval state).
  - `frontend/src/pages/DocViewerPage.jsx` — new `StockRequestConfirmPanel`.
- **Behaviour added/fixed:** Mobile UI mirrors web modal. Double-tap on slow mobile networks returns the cached response (idempotent). Wrong-branch PIN rejected. Admin PIN accepted from any branch. Action disappears once a linked BTO exists.
- **Tests added:** `test_br_stock_request_approval_qr.py` (12 cases) — view resolves, action listed/omitted correctly, no stock movement, no BTO created on confirm, idempotency.
- **Final result:** ✅ green.

### Phase 2.1 — View QR Modal on Web
- **Goal:** Let a desktop user display the QR code for a paper request without going through Print → Save PDF.
- **Files changed:**
  - `frontend/src/components/RequestQRDialog.jsx` (new).
  - `frontend/src/pages/BranchTransferPage.js` — "View QR" button on request cards.
- **Behaviour added/fixed:** Modal renders the `qrcode.react` SVG + the short code. No backend changes (re-uses the existing doc-code generation).
- **Tests added:** None (UI only; backend already covered by Phase 2).
- **Final result:** ✅ green.

### Phase 3 — Variance + Internal Invoice Integrity
- **Goal:** PIN-gate high-value variance acceptance; make the internal invoice line items reflect *actually-received* quantities; ensure invoice creation failure never silently corrupts state.
- **Files changed:**
  - `routes/branch_transfers.py::accept_receipt` — added `transfer_variance_accept` PIN gate (hard threshold `BT_VARIANCE_PIN_THRESHOLD = ₱5,000`), call to `rewrite_invoice_items_to_received`, silent-fail hardening with incident-ticket fallback.
  - `routes/internal_invoices.py` — new `rewrite_invoice_items_to_received` helper.
  - `routes/verify.py` — `transfer_variance_accept` policy (admin/TOTP only).
  - Audit log records every variance accept above threshold.
- **Behaviour added/fixed:**
  - Variance ≥ ₱5,000 requires admin PIN or TOTP. Manager PIN rejected even if role grants it.
  - On accept, the matching internal invoice's line items are rewritten to match `qty_received` (so AR/AP arithmetic matches the warehouse).
  - If invoice creation fails, the BTO is flagged `invoice_creation_failed=True`, an `incident_tickets` row is created with denormalized branch/product context, and `audit_log` row written.
- **Tests added:** `test_br_bt_variance_invoice_integrity.py` (12 cases) — PIN gate, manager PIN rejected, invoice rewrite math, silent-fail tripwire, incident ticket created, threshold boundary.
- **Final result:** ✅ green.

### Phase 3.1 — `update_transfer` organization_id Hardening
- **Goal:** Defensive tripwire so a future refactor that swaps the whitelist `$set` for `{"$set": data}` can't silently break tenant scoping.
- **Files changed:** `routes/branch_transfers.py` — new helper `_assert_org_preserved(transfer_id, prev_org_id)` + 5 call-sites (`update_transfer`, `send_transfer`, `approve_pending_transfer`, `reject_pending_transfer`, `resubmit_returned_transfer`).
- **Behaviour added/fixed:** No-op today (whitelists already prevent the bug). Tripwire raises 500 if `organization_id` changes unexpectedly.
- **Tests added:** `test_br_bt_org_id_hardening.py` (6 cases) — PUT ignores hostile + blank `organization_id`, approve/reject/resubmit preserve org_id, BTO remains tenant-visible after a hostile PUT.
- **Final result:** ✅ green.

---

## 3. Current Branch Transfer Workflow (End-to-End)

```
                  Branch A                                    Branch B / Source
┌──────────────────────────────────────┐      ┌──────────────────────────────────────┐
│ 1. Create stock request              │ ───► │ 2. Receive request notification      │
│    POST /api/purchase-orders         │      │                                      │
│    po_type=branch_request            │      │                                      │
│    status=requested                  │      │                                      │
│    NO STOCK MOVEMENT                 │      │                                      │
└──────────────────────────────────────┘      └──────────────────────────────────────┘
                                                              │
                                                              ▼
                                              ┌──────────────────────────────────────┐
                                              │ 3a. Web confirm (Confirm Quantities  │
                                              │     dialog)                          │
                                              │     POST /confirm-request            │
                                              │     OR                               │
                                              │ 3b. Mobile QR scan → confirm panel   │
                                              │     POST /qr-actions/{code}/         │
                                              │            confirm_stock_request     │
                                              │                                      │
                                              │     PIN: admin OR source-branch mgr  │
                                              │     Writes approved_qty per line     │
                                              │     approval_status: full/short/     │
                                              │       excess/rejected                │
                                              │     request_approval_log[] appended  │
                                              │     NO STOCK MOVEMENT                │
                                              └──────────────────────────────────────┘
                                                              │
                                                              ▼
                                              ┌──────────────────────────────────────┐
                                              │ 4. Generate Branch Transfer          │
                                              │    POST /generate-branch-transfer    │
                                              │    PREFILLED FROM approved_qty       │
                                              │    requested_qty still visible       │
                                              │    PO.status: requested →            │
                                              │       in_progress                    │
                                              │    PO.linked_bto_id + number set     │
                                              │    PO is no longer cancellable       │
                                              │    NO STOCK MOVEMENT                 │
                                              └──────────────────────────────────────┘
                                                              │
                                                              ▼
                                              ┌──────────────────────────────────────┐
                                              │ 5. Send BTO                          │
                                              │    POST /branch-transfers/{id}/send  │
                                              │    OR /approve (if pending_approval) │
                                              │    Status: draft → sent              │
                                              │    NO STOCK MOVEMENT                 │
                                              └──────────────────────────────────────┘
                                                              │
┌──────────────────────────────────────┐                      │
│ 6a. Receive exact                    │ ◄────────────────────┘
│     POST /receive                    │
│     items[].qty_received ==          │
│         items[].qty                  │
│     → _apply_receipt fires ONCE      │
│     STOCK MOVES (the ONE place)      │
│     Invoice generated with sent qty  │
└──────────────────────────────────────┘
        OR
┌──────────────────────────────────────┐
│ 6b. Receive variance                 │
│     POST /receive                    │
│     items[].qty_received !=          │
│         items[].qty                  │
│     Status: received_pending         │
│     shortages[] + excesses[] stamped │
│     total_capital_loss computed      │
│     NO STOCK MOVEMENT YET            │
└──────────────────────────────────────┘
        │
        ▼
┌──────────────────────────────────────┐
│ 7. Accept variance                   │
│    POST /accept-receipt              │
│    If |total_capital_loss| ≥ ₱5,000: │
│        PIN required (admin/TOTP)     │
│        audit_log row written         │
│    Status: received_pending →        │
│            received_complete         │
│    _apply_receipt fires ONCE         │
│    STOCK MOVES                       │
│    Invoice REWRITTEN to qty_received │
│        via rewrite_invoice_items_    │
│        to_received                   │
│    Silent-fail tripwire: if invoice  │
│        write fails →                 │
│        invoice_creation_failed=True  │
│        + incident_tickets row        │
│        + audit_log                   │
└──────────────────────────────────────┘
```

**Critical invariants enforced by tests:**
- `_apply_receipt` is the **only** code path that mutates `db.inventory`.
- It is called from exactly two places: `/receive` (exact match) and `/accept-receipt` (variance accept).
- `approved_qty` is *intent*; `qty_received` is *reality*. They never trigger inventory.

---

## 4. Security / Permissions Model

### Roles
- **Cashier:** zero authority on BTOs.
- **Manager:** can create + edit + send draft transfers from their own source branch; can submit for approval; can receive at their own destination branch. **Cannot** cancel cross-branch BTOs. **Cannot** confirm stock requests for branches they don't belong to.
- **Admin / Owner:** universal override across branches. Their PIN unlocks every gated action.

### Branch-context restrictions
- `send_transfer`, `update_transfer`, `resubmit_returned_transfer`: source-branch enforced — non-admin users must belong to `from_branch_id`.
- `confirm-request` and QR `confirm_stock_request`: the PIN must resolve to a user whose `branch_ids` include the request's `supply_branch_id`. Admin PIN/TOTP always passes.
- `cancel_transfer`: PIN policy `cancel_po`; admins can cancel anywhere, managers only their own branch.

### PIN / TOTP policies (in `routes/verify.py`)
| Policy key | Default accepted | Notes |
|---|---|---|
| `confirm_stock_request` | `admin_pin`, `manager_pin` (source branch only), `totp` | Manager PIN is branch-scoped at verification time. |
| `transfer_approve` | `admin_pin`, `manager_pin`, `totp` | Manager must also hold `branch_transfers.approve=true` permission. |
| `transfer_variance_accept` | `admin_pin`, `totp` | Manager PIN **REJECTED**. Hard gate above ₱5,000 capital loss. |
| `cancel_po` | `admin_pin`, `manager_pin`, `totp` | Manager only if source branch matches. |
| `smart_price_update` | `admin_pin`, `totp` | Used by Smart Price Scan price-update path; manager PIN rejected. |
| `historical_supplier_po_*` | `admin_pin`, `totp` | All three (add/pay/void) reject manager PIN. |

### QR / mobile lockouts
- Wrong-branch manager PIN scanning the request slip is rejected.
- Once a linked BTO exists (`linked_bto_id != null`), the QR `confirm_stock_request` action disappears from the view payload — the request is no longer mutable.
- `confirm_ref` makes duplicate scans (slow mobile networks) idempotent.

### organization_id hardening (Phase 3.1)
- 5 BTO writers wrapped by `_assert_org_preserved` tripwire.
- Tripwire reads `organization_id` before the `$set`, re-reads after, raises HTTP 500 if it changed.
- BR test suite locks the behaviour against hostile and blank payloads.

---

## 5. Data Model — Important Fields

### `purchase_orders` (stock-request POs, `po_type="branch_request"`)
| Field | Purpose |
|---|---|
| `requested_qty` | Per-item: what Branch A originally asked for. Never mutated post-creation. |
| `approved_qty` | Per-item: what Branch B confirmed (intent). Updated on each `/confirm-request`. |
| `approved_note` | Per-item: reason the approved qty differs from requested (mandatory for excess). |
| `approval_status` | One of `full` / `short` / `excess` / `rejected` / `pending`. |
| `approved_by_name` | Last approver display name. |
| `approved_at` | ISO timestamp of last approval. |
| `request_approval_log[]` | Append-only history of every confirmation event (who, when, qtys, source: web/qr). |
| `linked_bto_id`, `linked_bto_number` | Set when a BTO is generated from this request — marks the PO uncancellable. |
| `status` | `requested` → `in_progress` → `fulfilled` (set when the linked BTO is fully received). |

### `branch_transfer_orders` (BTOs)
| Field | Purpose |
|---|---|
| `items[].qty` | Sent quantity (intent at send time). |
| `items[].qty_received` | Actual quantity received (filled by `/receive`). |
| `items[].original_sent_qty` | Snapshot of `qty` at send time — preserved across variance edits for audit. |
| `shortages[]`, `excesses[]` | Per-product deltas computed at receive. |
| `total_capital_loss` | `sum(shortage_qty * cost_at_send)`; drives the variance PIN threshold. |
| `variance_history[]` | Append-only log of accept/dispute events. |
| `invoice_creation_failed` | `True` if invoice rewrite failed silently — surfaces incident-ticket trail. |
| `organization_id` | Tripwire-guarded by `_assert_org_preserved`. |

### `incident_tickets`
- Created when invoice creation or rewrite fails silently. Carries denormalized branch + product context so the operator can resolve without joins.

### `audit_log`
- Every high-value variance accept, invoice creation failure, historical-supplier-PO create/pay/void, and PIN-gated price update writes one row.

### `capital_changes`
- One row per parent capital movement (PO receive, branch transfer accept). Smart Price Scan's Capital Change tab joins this with active repack children to surface derived-cost impacts.

---

## 6. Test Status

### Final BR result
```
========================= 128 passed, 1 warning =========================
business_regression structured report
  rows=394 pass=394 fail=0
```
- **Zero DB footprint** verified — `cleanup_business_tenant(org_id)` purges every collection per file teardown. No `br_*` rows linger after the suite finishes.
- Frontend `yarn build`: clean (~27s).
- ruff + ESLint: clean.

### Branch-Transfer-specific BR test files
| File | Cases | Locks |
|---|---|---|
| `test_br_branch_transfer_invariants.py` | ~30 | Phase 0 — `_apply_receipt` is the only mutator. |
| `test_br_bt_permissions_and_pin.py` | ~14 | Phase 0.5 — cross-branch manager 403, PIN policies. |
| `test_br_stock_request_approval.py` | 18 | Phase 1 — confirm-request, approved_qty, generate prefill. |
| `test_br_stock_request_approval_qr.py` | 12 | Phase 2 — QR view + confirm, idempotency, lockouts. |
| `test_br_bt_variance_invoice_integrity.py` | 12 | Phase 3 — variance PIN, invoice rewrite, silent-fail tripwire. |
| `test_br_bt_org_id_hardening.py` | 6 | Phase 3.1 — hostile/blank org_id ignored across 4 writers + listing visibility. |

### Rerun commands
```bash
# Full BR (8–10s)
cd /app/backend && python3 -m pytest tests/business_regression/ -v

# Just the Branch Transfer suite
cd /app/backend && python3 -m pytest tests/business_regression/test_br_branch_transfer_invariants.py \
                                       tests/business_regression/test_br_bt_permissions_and_pin.py \
                                       tests/business_regression/test_br_stock_request_approval.py \
                                       tests/business_regression/test_br_stock_request_approval_qr.py \
                                       tests/business_regression/test_br_bt_variance_invoice_integrity.py \
                                       tests/business_regression/test_br_bt_org_id_hardening.py -v

# Frontend build (no tests; CRA build is the type/lint check)
cd /app/frontend && yarn build

# Structured report (after any BR run)
cat /app/test_reports/business_regression_latest.json | python3 -m json.tool | head -30
```

**Do NOT** run the root `pytest tests/` — the legacy non-BR suite has hardcoded dates and pollutes DB state (11 known failures unrelated to anything in this handoff).

---

## 7. Manual Smoke Checklist

Run these in order in a fresh tenant (login as admin/owner). Use the credentials in `/app/memory/test_credentials.md`.

1. **Create a request from Branch A → Branch B**
   - Branch Transfer page → "Request Stock" → pick supplier branch B → add a product with qty=10.
   - Expect: a request card appears, status "requested", neither branch's inventory changed.

2. **Confirm quantities from web**
   - Switch context to Branch B → open the same request → "Confirm Quantities".
   - Set approved qty=8, note="short stock". Enter source-branch manager PIN.
   - Expect: chip shows "8/10 approved (SHORT)". `approval_status` = `short`. No stock moved.

3. **Confirm quantities from QR/mobile**
   - Click "View QR" on a different request → scan with phone (or open `/doc/{code}` in a new tab).
   - On the mobile screen, fill approved quantities and PIN → submit.
   - Expect: success toast, action button disappears, request card updates.

4. **View QR modal**
   - Click "View QR" on a request → modal shows the QR image + short code.
   - Expect: code matches what `/doc/{code}` resolves to.

5. **Resume Transfer → verify prefill**
   - On the confirmed request → "Generate Branch Transfer".
   - Expect: BTO draft prefilled with `approved_qty=8` (NOT 10). Original requested qty visible as muted text.

6. **Create + send BTO**
   - Save draft → Send.
   - Expect: status `sent`. Inventory unchanged at BOTH branches. Linked PO now `in_progress` + uncancellable.

7. **Receive exact**
   - Switch to Branch A → Receive page → enter qty_received=8 for the line.
   - Expect: status `received_complete`. Branch A inventory +8. Branch B inventory −8. Internal invoice created with `qty=8` and a sale line.

8. **Receive with variance**
   - For a separate BTO sending 10: at receive, enter qty_received=7.
   - Expect: status `received_pending`. **No** inventory change. `total_capital_loss = 3 * unit_cost`. Variance card shows.

9. **Accept high-value variance with PIN**
   - For a BTO with `total_capital_loss` ≥ ₱5,000: click Accept.
   - Expect: PIN prompt. Manager PIN rejected. Admin PIN or TOTP accepted. After accept: status `received_complete`, inventory moves, invoice rewritten to received qty, `audit_log` row written.

10. **Verify internal invoice line items**
    - Open the internal invoice for step 9's BTO.
    - Expect: line item qty == `qty_received` (not the originally sent qty). Total reflects reality.

11. **Verify request cannot be cancelled once linked BTO exists**
    - On the original PO (now `in_progress`): try Cancel.
    - Expect: 400 / button disabled with "Cancel the linked BTO first."

12. **Verify wrong-branch PIN rejected**
    - On a request supplied by Branch B: try entering a Branch C manager's PIN.
    - Expect: 403 "Invalid PIN or not authorized for this branch."

13. **Verify admin override**
    - On the same request: enter admin PIN from any branch.
    - Expect: confirmation accepted.

---

## 8. Deployment Notes

### Restart requirements
- **Backend:** `sudo supervisorctl restart backend` — only required if `.env` changed OR a new dependency was installed. Hot reload is on. Phase 0–3.1 changed neither, so a clean deploy needs **no manual restart**.
- **Frontend:** `cd /app/frontend && yarn build && sudo supervisorctl restart frontend` — already done; rebuild only if you cherry-pick FE-only changes downstream.

### DB migrations / indexes
- **No migrations required.** All new fields (`approved_qty`, `request_approval_log[]`, `variance_history[]`, `invoice_creation_failed`, `linked_bto_id`, `linked_bto_number`, `original_sent_qty`) are optional and read with `.get()` fallbacks.
- **No new indexes required.** Existing `{organization_id, id}` and `{organization_id, branch_id}` indexes on `branch_transfer_orders` cover every new query path.
- **No production data backfill required.** Old BTOs without `original_sent_qty` etc. continue to work — the variance flow only reads these fields when they exist.

### Health check sequence (post-deploy)
```bash
# 1. Service health
curl -fsS "$REACT_APP_BACKEND_URL/api/health" | python3 -m json.tool

# 2. Tenant-scoped sanity (admin token required)
curl -fsS -H "Authorization: Bearer $TOKEN" \
  "$REACT_APP_BACKEND_URL/api/branch-transfers" | python3 -c "import sys,json;d=json.load(sys.stdin);print('orders:',d.get('total',0))"

# 3. BR suite (only if you have shell access — skip on managed prod)
cd /app/backend && python3 -m pytest tests/business_regression/ -q
```

### Rollback
- All changes are additive. Rolling forward only — no destructive migrations.
- If you must roll back, the platform's "Save to GitHub" + checkpoint-rollback covers it without manual DB work.

---

## 9. Known Remaining Gaps / Backlog

### Branch-Transfer-adjacent
| Priority | Item | Notes |
|---|---|---|
| P1 | Internal invoice print polish | Show `qty (was X)` + "Variance accepted by {verifier}". `variance_history[]` already in DB. ~60 LOC, FE-only. |
| P1 | Variance Log tab | Internal invoice detail page tab reading `variance_history[]`. ~60 LOC. |
| P1 | Tenant-configurable variance threshold | Replace hard-coded `BT_VARIANCE_PIN_THRESHOLD = 5000.0` with org setting. |
| P3 | `invoice_creation_failed` badge on BTO cards | Backend flag exists; FE doesn't render it yet. |
| P3 | `receive` / `accept-receipt` / `dispute-receipt` / `cancel_transfer` org_id guard | Phase 3.1 deferred these 4 writers to keep PR tight. Same pattern, ~4 LOC + 4 BR tests. |
| P3 | Admins see incoming + outgoing requests mixed in "All Branches" view without direction badges | UI polish. |
| P3 | Verifier identity not consistently stamped on PO cancellation | E12 gap. |

### Cross-cutting backlog (not Branch-Transfer but in the queue)
| Priority | Item |
|---|---|
| P0 | Supplier Ledger view (regular + historical POs per supplier). |
| P1 | CSV bulk-import for Historical POs (admin-PIN gated). |
| P2 | Prepared Order Completion SMS (needs Resend/Twilio keys). |
| P2 | Phase 4 — slow-moving stock context + SMS engine. |
| P2 | Phase 4A.2 — Concurrent Held-Sales Queue. |
| P3 | `_finalize_draft_offline` status divergence (`credit` vs `open`) in `sync.py`. |
| P3 | 11 legacy FE unit tests with hardcoded dates (needs `freezegun`). |
| P3 | Rename `/sales` → `/sales-history`. |

---

## 10. Recommended Next Choices

Ranked by best ROI given the current "fully green, deployable" state:

### A. 🟢 Deploy / ship current Branch Transfer improvements *(strongly recommended)*
- Everything is locked behind BR tests.
- Operationally, the gain from shipping NOW (zero phantom stock, mobile QR confirm, variance PIN gate, AR/AP accuracy) is enormous compared to the marginal value of one more polish phase.
- Zero migration risk.

### B. 🟡 Prepared Order Completion SMS *(needs your Resend/Twilio keys)*
- High user-visible value — customer gets a "Your order is ready for pickup" text.
- Blocked on API keys.
- Once keys are in, ~150 LOC + 3 BR tests.

### C. 🟡 Variance Log tab / Internal invoice print polish *(small, polishing)*
- ~60 LOC each.
- Pure FE additions on top of data that's already in DB.
- Closes the loop on Phase 3 visually.

### D. 🔵 Slow-moving stock context (Phase 4)
- Larger scope (sales-movement aggregation + SMS engine).
- Worth waiting until SMS infra is wired up under (B).

### E. 🔵 Tenant-configurable variance threshold
- Trivial code (~40 LOC). Defer until at least one tenant asks.

**My pick:** Ship (A) now, queue (B) once keys arrive.

---

## 11. Suggested Next-Fork Prompt

Copy-paste this verbatim into the next fork:

```
Read /app/memory/BRANCH_TRANSFER_COMPLETION_HANDOFF.md and
/app/memory/PRD.md (top section) before doing anything.

Do NOT code yet.

Step 1 — Confirm BR status by running:
    cd /app/backend && python3 -m pytest tests/business_regression/ -q

Expected: 128/128 pass / 394 rows / 0 fail / zero footprint.

Step 2 — Confirm frontend builds:
    cd /app/frontend && yarn build

Step 3 — Report back:
    a) Whether the BR + build are still green on this fork.
    b) Your recommendation between:
       - Deploy current state (handoff doc Section 10A — recommended).
       - Prepared Order Completion SMS (needs Resend/Twilio keys — ask the
         user if they have them before recommending).
       - Variance Log tab on internal invoice detail page (small, FE-only,
         reads existing variance_history[]).
    c) ONE concrete enhancement question for the user.

Do NOT start any of the three options until the user confirms which one to
pick. Stay in ask-mode until then.
```

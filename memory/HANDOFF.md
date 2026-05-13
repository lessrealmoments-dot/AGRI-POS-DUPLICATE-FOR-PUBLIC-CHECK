# Fork Handoff — Feb 2026 (post-Phase 3.2)

## TL;DR for the next agent

Read `/app/memory/PRD.md` top section first — it has full detail. This file is a 60-second briefing.

### Current state (everything is GREEN)
- **Full Business Regression: 113/113 tests pass / 379 rows / 0 fail**
- Frontend `yarn build`: clean (~27s)
- Zero DB footprint on every BR re-run
- Backend supervisor: running clean
- No outstanding bugs

### Phases delivered in this session (chronological)
| Phase | What | Tests | Status |
|---|---|---|---|
| 1 | Stock Request Confirmation Layer (approved_qty + web modal) | 18 / 31 rows | ✅ |
| 1.1 | Approval summary chips on request cards | UI only | ✅ |
| 2 | QR/Mobile Stock Request Confirmation via `/doc/{code}` | 12 / 23 rows | ✅ |
| 2.1 | View QR modal on request cards | UI only | ✅ |
| 3 | Variance PIN gate + Internal Invoice line-item rewrite + silent-fail hardening + incident ticket polish | 12 / 26 rows | ✅ |
| 3.2 | Historical Supplier PO (pre-system AP carry-forward, admin/TOTP only) | 12 / 20 rows | ✅ |

### Last working item
Phase 3.2 complete — admin-only "Old POs" flow on AP widget. User wanted a way to record pre-system supplier debt repeatedly without polluting current-period expense reports. Solved: dedicated `historical_supplier_pos` collection, admin_pin/TOTP only (no manager_pin), shows on AP dashboard with `kind: "historical"` chip, payments deduct from same fund wallets as regular supplier POs.

## DON'T touch / DON'T regress
- `_apply_receipt` in `branch_transfers.py` — sole stock mutator, Phase 0 invariants guard it.
- The Phase 1 helper `_apply_confirmation` in `purchase_orders.py` — shared by web + QR confirmation. Don't duplicate.
- `routes/verify.py` PIN policies — there are now ~30+ events. Don't remove any; only add new ones at the bottom of the list.
- TENANT_COLLECTIONS in `config.py` — `request_approval_log`, `audit_log`, `incident_tickets`, `historical_supplier_pos` were added this session for auto-org-scoping. Keep them there.

## What was NOT done (deferred / blocked / future)
| Priority | Item | Notes |
|---|---|---|
| P0 next | Supplier Ledger view | Combine regular POs + historical POs into single "Pay {supplier}" page. Natural next step after 3.2. |
| P1 | Phase 3.1 — `update_transfer` org_id defensive hardening | Defensive only — current code uses `$set` with whitelist so the bug is theoretical. ~30 LOC + 3 BR tests. |
| P1 | FE polish for Phase 3 variance | Internal invoice print should show `qty (was X)` + "Variance accepted by {verifier}". `variance_history[]` data is already in DB. |
| P1 | CSV bulk-import for historical POs | Suggested as enhancement in last finish summary. Same admin-PIN gate, ~80 LOC. |
| P1 | Tenant-configurable variance threshold | Currently `BT_VARIANCE_PIN_THRESHOLD = 5000.0` constant in `accept_receipt`. |
| P2 | Prepared Order SMS | Needs Resend/Twilio integration playbook + API keys from user. |
| P2 | Slow-moving stock SMS engine (Phase 4) | Owner deferred. |
| P2 | Concurrent Held-Sales Queue (Phase 4A.2) | |
| P3 | `_finalize_draft_offline` status divergence (`credit` vs `open`) in `sync.py` | |
| P3 | 11 legacy FE unit tests using hardcoded dates | Needs `freezegun`. |
| P3 | Rename `/sales` → `/sales-history` | |
| P3 | Surface `invoice_creation_failed` badge on BTO cards | Backend flag exists; FE doesn't read it yet. |
| P3 | Variance Log tab on internal invoice detail page | Read `variance_history[]` (~60 LOC). |

## Earlier known issues (still open from previous forks)
1. Internal invoice silent-fail (P2) — **FIXED in Phase 3** ✅
2. `update_transfer` PUT can blank `organization_id` (P2/BT-AUD-34) — defensive only, still open. Code uses `$set` + whitelist; no exploit path today.
3. `_finalize_draft_offline` writes `status="credit"` while live `/unified-sale` writes `status="open"` (P3) — still open.
4. Verifier identity not consistently stamped on PO cancellation (E12 gap) — still open.
5. Admins see incoming + outgoing requests mixed in "All Branches" view without direction badges — still open.

## Critical files (current)
### Backend
- `routes/purchase_orders.py` — Stock requests; Phase 1 `confirm-request` endpoint + shared `_apply_confirmation` helper at line ~1660.
- `routes/branch_transfers.py` — BTOs; Phase 3 PIN gate + invoice rewrite call in `accept_receipt`; silent-fail hardening at ~line 467.
- `routes/qr_actions.py` — Phase 2 `POST /qr-actions/{code}/confirm_stock_request`.
- `routes/doc_lookup.py` — Enriched `view_document_open` for branch_request POs.
- `routes/internal_invoices.py` — Phase 3 `rewrite_invoice_items_to_received` helper.
- `routes/historical_supplier_po.py` — Phase 3.2 NEW route file.
- `routes/verify.py` — PIN policies (~30 events).
- `routes/dashboard.py` — `accounts_payable_summary` merges historical POs.
- `config.py` — `TENANT_COLLECTIONS` (includes audit_log, incident_tickets, historical_supplier_pos).

### Frontend
- `pages/BranchTransferPage.js` — Phase 1.1 chips + Phase 1 + 2.1 dialog mounts.
- `pages/DocViewerPage.jsx` — Phase 2 `StockRequestConfirmPanel` (inline panel).
- `components/ConfirmQuantitiesDialog.jsx` — Phase 1 web modal.
- `components/RequestQRDialog.jsx` — Phase 2.1 View QR modal.
- `components/HistoricalSupplierPODialog.jsx` — Phase 3.2 admin-only modal.
- `components/dashboard/AccountsPayableWidget.js` — Phase 3.2 "Old POs" button.

## Test infrastructure
- BR suite at `backend/tests/business_regression/` is the source of truth.
- Each test file uses module-scoped `tenant` fixture from `conftest.py` + `record_result` for structured reporting.
- Zero-footprint cleanup: TENANT_COLLECTIONS members auto-cleaned via `cleanup_business_tenant()` in `_fixtures.py`.
- BR report json at `/app/test_reports/business_regression_latest.json`.
- DO NOT run full `pytest tests/` — many legacy tests have hardcoded dates and pollute DB state. Stick to `tests/business_regression/`.

## Test credentials
See `/app/memory/test_credentials.md` — unchanged this session. All BR-fixture users (`br_sr_conf-*`, `br_sr_qr-*`, `br_bt_var-*`, `br_hs_po-*`) are ephemeral and auto-cleaned per run.

## Run cheat sheet
```bash
# Full BR (4–8s)
cd /app/backend && python3 -m pytest tests/business_regression/ -v

# Single phase
python3 -m pytest tests/business_regression/test_br_historical_supplier_po.py -v

# Frontend
cd /app/frontend && yarn build  # ~27s
```

## Active integrations / env
- Cloudflare R2 / Boto3 (file storage) — user-supplied
- Resend (emails) — user-supplied
- No LLM integrations active this session

# Phase 1 — Stock Request Confirmation Layer — Fork Handoff

> **Fork base**: Phase 0 + Phase 0.5 + POS BIR Dual-Date complete, all green.
> **Next workstream**: Phase 1 — `approved_qty` / Stock Request Confirmation
> (web-only, no QR/mobile yet). Do NOT touch stock movement.
> **Mandatory first response language**: English.

---

## 0. Read-first checklist (in this order)

1. `/app/memory/test_credentials.md` — pins, emails, branch ids.
2. `/app/memory/PRD.md` (last ~120 lines — Phase 0.5 entry).
3. `/app/memory/BRANCH_TRANSFER_FULL_AUDIT.md` (§6 Stock-Request Confirmation Layer design — already drafted).
4. This document (Phase 1 spec).
5. The 3 existing BR files for Branch Transfer (your regression net):
   * `backend/tests/business_regression/test_br_branch_transfer_invariants.py` (Phase 0)
   * `backend/tests/business_regression/test_br_bt_permissions_and_pin.py` (Phase 0.5)
   * `backend/tests/business_regression/test_br_pos_dual_date.py` (sidecar)

---

## 1. Current stable baseline (proven green)

| Metric | Value |
|---|---|
| BR test files | 5 (br1-6, br_iso, br_prep, br_bt invariants, br_bt perm, br_pos_dual_date) |
| BR tests total | **59 pass** |
| BR rows total | **279 pass / 0 fail** |
| Phase 0 stock invariants | 10/10 pass, 46/46 rows |
| Phase 0.5 perm + cancel + branch context | 18/18 pass, 40/40 rows |
| POS dual-date | 4/4 pass, 8/8 rows |
| Frontend build | clean (~27s, yarn build) |
| Production | live @ agri-books.com |
| Zero DB footprint | confirmed (no `br_bt_perm`, `br_pos_dual`, `br_bt[0-9]` rows left) |

### Recently shipped (this fork)

* **Phase 0.5 (2026-02-13)** — lifecycle + branch-context guards
  * `generate-branch-transfer` no longer flips PO to `in_progress`
  * `create_transfer` now stamps `in_progress` + `linked_bto_id/number` when BTO is created from a request
  * `cancel_purchase_order` checks for linked non-cancelled BTO; refuses with actionable detail; branch-enforces requester for non-admin; stamps verifier identity
  * `send_transfer`, `cancel_transfer` enforce source-branch identity
  * `approve_pending_transfer` passes `branch_id=order["from_branch_id"]` to `verify_pin_for_action` → cross-branch manager PIN now rejected
  * Frontend: `handleCancelRequest` no longer hard-blocks `in_progress`; Generate/Resume buttons hidden for wrong-branch non-admin + admin confirm prompt; "Submit for Approval" hidden for admin; "Linked: BTO-XXX" badge.
* **POS BIR Dual-Date (2026-02-13)** — `invoice_date` (BIR sale day, always today) split from `order_date` (book record day, bumped when closed). 6 PrintEngine templates updated. Cashier confirm on first carryover sale.

---

## 2. Phase 1 spec (verbatim from owner)

### Core rules (immutable from Phase 1 PR)

1. `items[].quantity` (the original requested qty) MUST NEVER be overwritten.
2. Add `items[].approved_qty` (float) and `items[].approved_note` (string) per line.
3. PO-level metadata: `approval_status`, `approved_at`, `approved_by_id`, `approved_by_name`, `approval_method`, `approval_note`.
4. Append-only collection `request_approval_log` (one row per confirmation attempt, success or re-confirm).
5. No stock movement. No BTO created. No internal invoice created.
6. `generate-branch-transfer` prefers `approved_qty` for `send_qty` prefill when present; still returns `requested_qty` for visibility.
7. Branch-context: only supply branch (or admin) can confirm. Requester branch manager 403.
8. PIN/TOTP via NEW policy `confirm_stock_request` with `branch_id=supply_branch_id`. PIN identity = `approved_by`.

### Endpoints to add

* `POST /purchase-orders/{po_id}/confirm-request`
  Body: `{ pin, items: [{product_id, approved_qty, approved_note?}], approval_note? }`
  Returns: `{po_id, approval_status, approved_at, approved_by_name, items: [...]}`
  Guards: PO `po_type=branch_request`, status in `{requested}` (re-confirm allowed until BTO exists), no non-cancelled linked BTO, branch enforce, PIN verify with `branch_id=supply_branch_id`.

* `GET /purchase-orders/{po_id}/confirmation`
  Returns: side-by-side ledger `{items: [{product_id, requested_qty, approved_qty, approved_note}], approval_metadata, log: [...]}`. No mutation.

### Endpoint to modify

* `generate_branch_transfer_from_request` (`purchase_orders.py:1596`) — when `items[].approved_qty` is set on the PO line, prefill `qty` (send qty) from it instead of `min(requested, available)`. Keep `requested_qty` and add `approved_qty` to each response item.

### New PIN policy

In `backend/routes/verify.py` `PIN_POLICY_ACTIONS` (line ~179):

```python
"confirm_stock_request": {
    "label": "Confirm Stock Request Quantities",
    "defaults": ["admin_pin", "manager_pin", "totp"],
    "allowed_approver_roles": ["admin", "owner", "manager"],
},
```

### Frontend

* Add `ConfirmQuantitiesDialog.jsx` (new component).
* Wire into `BranchTransferPage.js` incoming-request card (status `requested`):
  * Replace "Generate Transfer" with **two** buttons: `Confirm Quantities` (PIN-gated modal) + `Generate Transfer` (existing — disabled if confirmation_status is `pending` and a soft warning).
* Outgoing-request card: when `approval_status` is `approved`/`partial`, show per-line "Requested X · Approved Y" read-only.
* Composer pre-fill: when opening from `Generate Transfer` on a confirmed PO, pre-fill send_qty from approved_qty (banner: "Pre-filled from owner's confirmed quantities").

### Branch context

Reuse Phase 0.5 pattern (`assert_branch_access(user, supply_branch_id)` for non-admin). Mirror that the verifier (PIN holder) must be admin or have `branch_id == supply_branch_id` — `verify_pin_for_action` already supports `branch_id` parameter.

---

## 3. New test file to create

`backend/tests/business_regression/test_br_stock_request_approval.py` — scenario prefix `br_sr_conf` or `br_phase1`.

### Required tests (sequence matches owner's spec)

| # | Scenario | Behaviour |
|---|---|---|
| 1 | `confirm_request_does_not_move_stock` | After POST `/confirm-request`, source AND destination inventory rows match snapshot. |
| 2 | `requested_qty_remains_immutable` | After confirmation, `items[].quantity` == original; only `approved_qty` differs. |
| 3 | `approved_qty_saved_per_line` | Per-line `approved_qty` matches body. |
| 4 | `approval_metadata_saved` | `approval_status`, `approved_at`, `approved_by_id/name`, `approval_method`, `approval_note` all populated. |
| 5 | `approval_log_appended` | One row in `request_approval_log` per confirmation. Includes per-line diff `{requested, approved, delta}`. |
| 6 | `requesting_branch_manager_cannot_confirm` | 403; PO unchanged. |
| 7 | `supplying_branch_manager_can_confirm` | 200; metadata stamped. |
| 8 | `wrong_branch_pin_rejected` | Caller has perm but PIN owner ≠ supply branch → 403. |
| 9 | `source_branch_pin_accepted` | Caller has perm, PIN owner = supply branch → 200. |
| 10 | `admin_pin_can_confirm_any_branch` | Owner caller succeeds. |
| 11 | `generate_transfer_prefills_approved_qty` | After confirmation, `generate-branch-transfer` response `items[0].qty == approved_qty`. |
| 12 | `generate_transfer_still_shows_requested_qty` | Same response includes `items[0].requested_qty == 100`. |
| 13 | `confirm_rejected_after_linked_bto_exists` | After `create_transfer`, calling `/confirm-request` → 400 with "Cancel the linked transfer draft first" or similar (soft-lock). |
| 14 | `reconfirm_appends_log_and_updates_approved_qty` | Calling confirm twice — second succeeds (while no BTO yet), log has 2 rows, `items[].approved_qty` is the latest. |
| 15 | `no_duplicate_bto_created` | After confirm, no row in `branch_transfer_orders` exists referencing this PO. |
| 16 | Implicit: full BR re-run shows Phase 0 + Phase 0.5 still green. |

Target: ~18 tests, ~50 rows, all green.

---

## 4. Code architecture pointers

### Backend

| File | Why it matters |
|---|---|
| `backend/routes/purchase_orders.py` | Add `POST /{po_id}/confirm-request` + `GET /{po_id}/confirmation`. Modify `generate_branch_transfer_from_request` (line ~1596) to prefer `approved_qty`. Note: existing route is already branch-context-aware after Phase 0.5. |
| `backend/routes/verify.py` | Register new `confirm_stock_request` policy in `PIN_POLICY_ACTIONS` (~line 179). |
| `backend/routes/branch_transfers.py` | DO NOT modify. The fix should be purely additive on the PO side. |
| `backend/utils/auth.py` | Reuse `check_perm`, `assert_branch_access`, `is_privileged`, `user_branch_ids`. |
| `backend/tests/business_regression/_fixtures.py` | `seed_product`. Use same module-scoped `tenant` fixture and module-scoped `extra_users` from `test_br_bt_permissions_and_pin.py` (or replicate the 2-manager seed in the new file). |
| `backend/tests/business_regression/conftest.py` | `record_result`, `cleanup_business_tenant`. Reuse as-is. |

### Frontend

| File | Why it matters |
|---|---|
| `frontend/src/pages/BranchTransferPage.js` | Wire new "Confirm Quantities" button onto incoming-request card (`status === 'requested'`). Reuse existing `currentBranch?.id` / `isAdmin` / `branches[]` props. Outgoing card: show per-line approved when approval_status set. |
| `frontend/src/components/ConfirmQuantitiesDialog.jsx` | NEW. Modal with: per-line editable approved_qty, per-line note, global note, PinDialog reuse for PIN, preview screen. ~250 LOC. |
| `frontend/src/components/PinDialog.jsx` | Reuse existing component (used by sales). Don't write a new one. |
| `frontend/src/lib/PrintEngine.js` | NOT touched in Phase 1. |

---

## 5. Strict no-go list (do NOT do in Phase 1)

* ❌ QR / mobile confirmation flow on `DocViewerPage.jsx` — that's **Phase 2**.
* ❌ "View QR" modal on transfer/request cards — that's a Phase 2 sidecar.
* ❌ Slow-moving stock analytics — that's **Phase 4**.
* ❌ SMS engine work (prepared-order or slow-moving) — paused.
* ❌ Any change to `_apply_receipt`, `receive_transfer`, `accept_receipt`, `dispute_receipt`.
* ❌ Auto-revert `in_progress → requested` on composer-close — out of scope.
* ❌ Active-branch JWT field — soft FE confirm only, defer until needed.
* ❌ `update_transfer` org_id defensive assert (BT-AUD-34) — Phase 3.
* ❌ Internal-invoice silent-fail fix (BT-AUD-30) — Phase 3.

---

## 6. Run requirements (do in order, copy-paste verbatim)

```bash
# 1. New Phase 1 test
cd /app/backend && python3 -m pytest tests/business_regression/test_br_stock_request_approval.py -v

# 2. Same again (idempotency)
cd /app/backend && python3 -m pytest tests/business_regression/test_br_stock_request_approval.py -v

# 3. Phase 0 invariants — must remain green
cd /app/backend && python3 -m pytest tests/business_regression/test_br_branch_transfer_invariants.py -v

# 4. Phase 0.5 perm tests — must remain green
cd /app/backend && python3 -m pytest tests/business_regression/test_br_bt_permissions_and_pin.py -v

# 5. Full BR suite — should be ~77 tests / ~329 rows after Phase 1 (currently 59 / 279)
cd /app/backend && python3 -m pytest tests/business_regression/ -v

# 6. BR report filtered for relevant scenarios
cd /app && python3 tools/br_report.py --scenario br_bt
cd /app && python3 tools/br_report.py --scenario br_sr_conf

# 7. Frontend build
cd /app/frontend && yarn build

# 8. Zero footprint check
cd /app/backend && python3 -c "
import asyncio
from config import _raw_db
async def main():
    counts = {}
    for coll in ['products','inventory','branch_transfer_orders','purchase_orders','request_approval_log','users']:
        c = await _raw_db[coll].count_documents({'\$or':[
            {'name':{'\$regex':'^br_sr_conf'}},
            {'product_name':{'\$regex':'^br_sr_conf'}},
            {'full_name':{'\$regex':'^br_sr_conf'}},
            {'notes':{'\$regex':'^br_sr_conf'}},
        ]})
        if c: counts[coll] = c
    print('Lingering rows:', counts or '(none)')
asyncio.run(main())
"
```

Expected after Phase 1 PR:
* New file: ~18 tests / ~50 rows green
* All existing tests still green
* Full BR: ~77 tests / ~329 rows / 0 fail
* Frontend build clean
* No lingering rows

---

## 7. Suggested implementation order (smallest-first, RED→GREEN)

1. **Write the BR test file FIRST**. Tests 1-5 will FAIL because the endpoint doesn't exist yet. Tests 6-10 require seeded extra users (copy pattern from Phase 0.5 file).
2. **Add `confirm_stock_request` policy** in verify.py (~5 LOC).
3. **Implement `POST /confirm-request`** in `purchase_orders.py` (~100 LOC).
   * Tests 1-5 turn green.
4. **Implement `GET /confirmation`** in `purchase_orders.py` (~40 LOC).
5. **Modify `generate-branch-transfer`** to honor `approved_qty` (~10 LOC).
   * Tests 11, 12 turn green.
6. **Add branch-context + linked-BTO + duplicate-confirm guards** (~30 LOC).
   * Tests 6-10, 13-15 turn green.
7. **Add `ConfirmQuantitiesDialog.jsx`** + wire into BranchTransferPage (~280 LOC).
8. Final run all 6 steps above. Update PRD.md.

Estimated total: ~450-500 LOC including tests. ~1 day.

---

## 8. Debugging checklist if Phase 1 tests fail

* **`organization_id` not propagating to `request_approval_log` writes** — explicitly set it from PO's `organization_id` on insert (don't trust ambient context).
* **`approved_qty` not appearing in `generate-branch-transfer` response** — confirm you're reading the live PO doc after the confirm update, not a cached copy.
* **PIN test 8 false-positive** — check `branch_id` is being passed to `verify_pin_for_action`. Same trap as Phase 0.5 #14.
* **Reconfirm fails the second time** — status guard must allow re-confirm when no BTO exists. The check should be on linked BTO presence, not on `approval_status == "pending"`.
* **Phase 0.5 br_bt_perm.4 breaks** — Phase 0.5 test asserts `status == "in_progress"` after BTO create. Phase 1 should NOT change that — Phase 1 only adds new fields to the PO; the existing in_progress lifecycle stays.

---

## 9. PIN flow recap (so you don't re-investigate)

`verify_pin_for_action(pin, action_key, branch_id=None)` from `backend/routes/verify.py:316`:
* Looks up `system_settings.admin_pin` (hashed) → if matches → `{verifier_id: "system_admin", method: "admin_pin"}`.
* Else looks up TOTP secret on org settings → if 6-digit code valid → `{verifier_id: "totp_user", method: "totp"}`.
* Else looks up `users.manager_pin` plain-text equal → returns `{verifier_id: user.id, verifier_name: user.full_name, method: "manager_pin"}`. **Branch-scoped: when `branch_id` passed, only managers whose `branch_id == branch_id` match.**

Use:
```python
verifier = await verify_pin_for_action(pin, "confirm_stock_request",
                                         branch_id=po["supply_branch_id"])
if not verifier:
    raise HTTPException(403, "Invalid PIN or unauthorized for this supply branch")
```

---

## 10. Anti-patterns to avoid (from prior PRs)

* `try/except: pass` for side effects → use `logger.warning` so failures show in supervisor logs.
* `replace_one` on tenant collections → strips `organization_id` (caused H-1 bug). Always `update_one({"id": x, "organization_id": org_id}, {"$set": ...})`.
* Comparing dicts with different keys in `record_result` → use identical key names on both sides (`expected={"stock": x}, actual={"stock": y}`).
* `_id` leaking into responses → always `find_one(..., {"_id": 0, ...})`.
* `datetime.utcnow()` → use `datetime.now(timezone.utc)` per project rule.

---

## 11. Open questions for owner (do NOT block on these — pick reasonable defaults)

1. **Can `approved_qty` exceed `requested_qty`?** Probably yes (Branch B offers 20 when A asked for 10). Default: ALLOW; let the cashier surprise the customer in a good way.
2. **What's the per-line `confirmation_status` values?** Suggest: derived field, not stored — `full` if `approved == requested`, `partial` if `< requested`, `excess` if `>`, `declined` if `== 0`.
3. **Re-confirm SLA?** No auto-expire. Manager can confirm anytime until BTO is created.
4. **Notification on confirm?** Yes — send in-app notification to the requesting branch's admin/manager when their request is confirmed (template `stock_request_confirmed`). NEW template — register in `routes/notifications.py`.

---

## 12. Files of reference (for the forked agent)

| Path | Why |
|---|---|
| `/app/backend/routes/purchase_orders.py` (lines 1596-1688, 1691-1770) | Where to add new routes and modify existing generate-transfer |
| `/app/backend/routes/branch_transfers.py:377-457` | How `create_transfer` already links BTO to PO via `request_po_id` (don't change) |
| `/app/backend/routes/verify.py:179-280` | Where to register new PIN policy |
| `/app/backend/utils/auth.py:115-230` | `check_perm`, `assert_branch_access`, `is_privileged`, `user_branch_ids` |
| `/app/backend/tests/business_regression/test_br_bt_permissions_and_pin.py` | Reference for seeding `a_mgr_user` / `b_mgr_user` with permissions + PINs |
| `/app/backend/tests/business_regression/test_br_branch_transfer_invariants.py` | Reference for stock-snapshot assertion pattern |
| `/app/frontend/src/pages/BranchTransferPage.js` | Where to add "Confirm Quantities" button on incoming-request card |
| `/app/frontend/src/components/PinDialog.jsx` (if exists; else use sonner-prompt pattern from sales) | PIN entry UI |
| `/app/memory/BRANCH_TRANSFER_FULL_AUDIT.md` §6 | The design that justifies this Phase 1 |

---

## 13. Test credentials (snapshot)

* Super admin: `janmarkeahig@gmail.com` / `Aa@58798546521325`
* Regression org admin: `test_org_admin@regression.local` / `RegressionPass!2026` · owner_pin/admin_pin: `913712`
* Regression manager: `test_org_manager@regression.local` · manager_pin: `521325`
* Production org: agri-books.com — manager_pin 521325 / 587985 · staff PIN 8888

---

## 14. Last 10 user messages (so you can follow the arc)

1. "Get ready for forking..." (this prep)
2. POS dual-date fix shipped + 4 BR tests / 8 rows
3. "Great, proceed with that fix" (BIR dual-date)
4. POS TZ + nextCalendarDay fix shipped
5. "Sales Date and Record Date" clarification (BIR two-date semantics)
6. "Single source of truth based on web settings TZ" (the TZ scan)
7. Phase 0.5 shipped (5 files, 18 tests, 7 RED→GREEN)
8. Phase 0 shipped (10 invariant tests)
9. Permission/branch-context audit (J option 2 picked)
10. Branch Transfer full audit (BT-AUD-01..52)

---

## 15. Recommended opening message for next prompt

> "Proceed with Phase 1 Implementation. Test file FIRST, then backend endpoints + PIN policy, then frontend modal. Follow the order in §7 of `/app/memory/PHASE_1_STOCK_REQUEST_CONFIRMATION_HANDOFF.md`. Do NOT exceed scope (no QR, no View QR, no SMS)."

— End of fork handoff —

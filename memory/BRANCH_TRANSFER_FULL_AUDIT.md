# Branch Transfer — Full System Audit & Redesign Proposal

_Generated: 2026-02-12 · Read-only audit (no code changes)._
_Author: E1 (Emergent) — for owner's external second-opinion review._

Use this document as a standalone briefing. It contains: (1) the live
feature map, (2) every endpoint and state, (3) the issues found, and
(4) a proposed redesign that satisfies the owner's "Branch B scans QR
→ modifies qty → Branch A sees updated qty on resume" request.

---

## 0. TL;DR

The `/branch-transfers` page is **functionally complete but conceptually
fractured**. Two collections (`purchase_orders` for stock-requests,
`branch_transfer_orders` for the actual movement) and 13+ statuses are
exposed across 3 tabs with 1 hidden sub-tab and 1 separate approval
screen, plus 1 view-only QR mobile page. The flow the owner described
(QR scan → modify qty with PIN → preview → confirm → Branch A's resume
view picks up the confirmed numbers) is **not implemented**: today,
Branch B's only way to deviate from Branch A's qty is to open the full
"New Transfer" composer, and Branch A has no visibility into the
"will-send" intent until the transfer is already dispatched.

The redesign in §6 keeps every existing endpoint and adds a thin
"Stock-Request Confirmation" layer that closes that loop, with one new
collection field set (`items[].confirmed_qty`, etc.) and one new public
mobile QR action.

---

## 1. Concepts & data model

There are TWO documents involved in any inter-branch movement:

### 1.1 Stock Request — `purchase_orders` collection, `po_type = "branch_request"`

Created by the **requesting branch (Branch A)** to ask the **supplying
branch (Branch B)** for goods. Has its own PO number (`PO-…`), QR code,
and lifecycle. Statuses observed in code:

| Status | Meaning | Where set |
|---|---|---|
| `requested` | Branch A submitted, Branch B not started | `POST /purchase-orders` (po_type=branch_request) |
| `in_progress` | Branch B opened "Generate Transfer" once (re-entrant) | `POST /purchase-orders/{po_id}/generate-branch-transfer` |
| `fulfilled` | A Branch Transfer Order completed with all qty | After `accept-receipt` in `branch_transfers.py:1567` |
| `partially_fulfilled` | A Branch Transfer Order completed with shortages | Same path |
| `cancelled` | Cancelled by manager (PIN required) | `DELETE /purchase-orders/{po_id}` |

Fields on each PO item: `product_id, product_name, quantity, unit, price` etc.
**There is NO field today for "qty Branch B confirms it will send"** —
only Branch A's requested qty.

### 1.2 Branch Transfer Order (BTO) — `branch_transfer_orders` collection

Created by **Branch B** (the source) once they decide to ship.
`from_branch_id = Branch B (source/supplier)` and
`to_branch_id = Branch A (destination/requester)`. Has its own number
(`BTO-…`), QR code, doc_code, status lifecycle, and per-item triple-price model.

Statuses observed in code (`branch_transfers.py`):

| Status | Meaning |
|---|---|
| `draft` | BTO created, not dispatched |
| `pending_approval` | Manager submitted but needs admin approval |
| `returned` | Admin rejected — bounced to manager to edit + resubmit |
| `sent` | Goods on the way; destination notified |
| `sent_to_terminal` | Locked on an AgriSmart Terminal for receiving |
| `received_pending` | Destination submitted qty; variance present; source must accept/dispute |
| `received` | Inventory moved; transfer complete |
| `disputed` | Source rejected destination's receipt; destination must re-count |
| `cancelled` | Cancelled (only allowed if no inventory moved) |

Fields per item: `product_id, product_name, sku, category, unit, qty,
branch_capital (source cost), transfer_capital (carried cost),
branch_retail (destination sell price), override, override_reason`.

Linkage: BTO carries `request_po_id` and `request_po_number` on create
(line 423-425 of `branch_transfers.py`). One-way link only — the PO
doesn't carry the BTO id until fulfillment.

### 1.3 Adjacent collections

* `branch_transfer_templates` — saved category-markup template per destination branch.
* `branch_transfer_price_memory` — last retail price per product per branch (used for autofill).
* `branch_prices` — branch-specific cost / retail price overrides.
* `parked_branch_transfers` — Park / Resume of an unsaved BTO draft (24 h TTL, 10 per branch).
* `internal_invoices` — accounting mirror of every BTO (silent failure-tolerant).
* `doc_codes` — short code → doc id, used by QR layer.
* `incident_tickets` — opened when source accepts a receipt-with-variance + "create incident".
* `notifications` — in-app notifications (incoming transfer, variance review, accepted, etc.).
* `sms_queue` (via templates `transfer_pending_approval`, `transfer_approved`, `branch_stock_request`) — outbound SMS to admins/managers.

---

## 2. Endpoint map

### 2.1 Stock Request (sits under `routes/purchase_orders.py` because PO collection)

| Method | Path | Purpose | Auth |
|---|---|---|---|
| POST | `/purchase-orders` (po_type=branch_request) | Branch A creates a stock request | JWT |
| GET | `/purchase-orders/incoming-requests?branch_id=…` | Branch B fetches requests aimed at it | JWT |
| GET | `/purchase-orders/outgoing-requests?branch_id=…` | Branch A fetches its own requests | JWT |
| POST | `/purchase-orders/{po_id}/generate-branch-transfer` | Branch B pulls the request into the "New Transfer" composer pre-filled. Re-entrant (idempotent on `in_progress`). | JWT, role manager/admin |
| DELETE | `/purchase-orders/{po_id}` | Cancel a stock request (also handles cash POs). PIN required. | JWT |

### 2.2 Branch Transfer (`routes/branch_transfers.py`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/branch-transfers/markup-template/{to_branch_id}` | Read saved markup template |
| PUT | `/branch-transfers/markup-template/{to_branch_id}` | Save markup template (manager/admin) |
| GET | `/branch-transfers/product-lookup?…` | Search products for the composer |
| GET | `/branch-transfers` | List BTOs (auto-scoped by branch unless admin) |
| POST | `/branch-transfers` | Create BTO (draft or pending_approval) |
| PUT | `/branch-transfers/{id}` | Edit a draft / pending / returned BTO |
| POST | `/branch-transfers/{id}/send` | Manager dispatches a draft BTO |
| POST | `/branch-transfers/{id}/approve` | Admin (or permitted manager) approves with PIN → status `sent` |
| POST | `/branch-transfers/{id}/reject` | Admin rejects → status `returned` |
| POST | `/branch-transfers/{id}/resubmit` | Manager resubmits a returned BTO |
| GET | `/branch-transfers/{id}/approval-insights` | Snapshot of pricing impact for admin |
| POST | `/branch-transfers/{id}/send-to-terminal` | Lock on terminal (status `sent_to_terminal`) |
| POST | `/branch-transfers/{id}/terminal-receive` | Terminal-driven receive (skips receipt photo) |
| GET | `/branch-transfers/{id}/capital-preview` | Preview cost impact at destination |
| POST | `/branch-transfers/{id}/receive` | Destination submits qty (variance → `received_pending`, no variance → `received`) |
| POST | `/branch-transfers/{id}/accept-receipt` | Source accepts destination's claimed qty; can also `accept_with_incident` |
| POST | `/branch-transfers/{id}/dispute-receipt` | Source disputes; destination must re-count |
| DELETE | `/branch-transfers/{id}` | Cancel (blocked if inventory moved) |
| POST | `/branch-transfers/admin/fix-orphaned-movements` | Admin tool — backfill organization_id on legacy movement rows |
| GET | `/branch-transfers/{id}` | Single BTO with resolved branch names |

### 2.3 Parked drafts (`routes/parked_branch_transfers.py`)

| Method | Path | Purpose |
|---|---|---|
| POST | `/parked-branch-transfers` | Save current composer state (label, rows, markup) |
| GET | `/parked-branch-transfers?from_branch_id=…` | List parks for a from-branch (auto-purge ≥ 24 h) |
| GET | `/parked-branch-transfers/{park_id}` | Fetch a park |
| DELETE | `/parked-branch-transfers/{park_id}?pin=…` | Discard (own = no PIN; other = manager PIN) |
| POST | `/parked-branch-transfers/{park_id}/consume` | Atomic resume (delete + return) |

### 2.4 QR + Document Lookup (`routes/doc_lookup.py`, `routes/qr_actions.py`)

| Method | Path | Purpose |
|---|---|---|
| POST | `/doc/generate-code` | Mint a short code for a doc (JWT) |
| GET | `/doc/by-ref/{doc_type}/{doc_id}` | Retrieve existing doc code (JWT) |
| GET | `/doc/view/{code}` | **Public** open view — basic info, action menu, no PIN. Used by mobile QR landing. |
| POST | `/doc/lookup` | PIN-protected full document fetch |
| GET | `/doc/search?q=&branch_id=` | Terminal smart-scanner search by human number |
| GET | `/qr-actions/{code}/context` | Wraps `/doc/view` |
| POST | `/qr-actions/{code}/verify_pin` | Validate PIN against doc-specific policy |
| POST | `/qr-actions/{code}/release_stocks` | Release reserved stock (invoice docs) |
| POST | `/qr-actions/{code}/receive_payment` | Record a payment (invoice docs) |
| POST | `/qr-actions/{code}/transfer_receive` | Receive a BTO via QR (terminal required) |
| POST | `/qr-actions/{code}/update_draft` | Adjust a for_preparation invoice |
| POST | `/qr-actions/{code}/generate-upload-token` | Mint a token to upload receipt photo |

**There is no `/qr-actions/{code}/confirm_stock_request` or
`/qr-actions/{code}/modify_request_qty` today.** Stock-request QR
slips currently land on `DocViewerPage` showing a view-only summary
and the prompt "Open the app and go to Branch Transfer → History →
Requests" (see `DocViewerPage.jsx:2183-2199`).

---

## 3. UI map (frontend)

`/app/frontend/src/pages/BranchTransferPage.js` (3 519 lines)

```
Branch Transfers page
├── Tab: "New Transfer"
│   ├── Editing-draft banner (if editing existing draft)
│   ├── Request-context banner (if generated from stock request)
│   ├── From/To branch picker, min-margin, Save-template button
│   ├── Category Markup Panel (collapsible)
│   ├── Product table (qty, branch_capital, transfer_capital, branch_retail,
│   │                  override+reason)
│   ├── Repack price update strip (for parent/child SKU sales)
│   ├── Action bar: Park, Submit for Approval, Save Draft, Send Now
│
├── Tab: "Request Stock"
│   ├── Your branch + target branch picker
│   ├── Show-retail toggle (whether supplier sees retail price)
│   ├── Product rows (SmartProductSearch w/ supplier+self stock context)
│   ├── Notes + Send Stock Request button
│
└── Tab: "Transfers" (history)
    ├── Status pill bar: All · Requests · Pending Approval · Returned · Drafts
    │                    · In Transit · Terminal · Needs Review · Completed · Disputes
    ├── Status="requests" content:
    │   ├── Incoming / Outgoing toggle
    │   ├── Per request card: Print Full/58mm/Dot, Cancel (PIN), Generate Transfer,
    │   │   Resume Transfer (when in_progress), Fulfilled badge, …
    └── Other statuses: per BTO card with Print + Receive + Send + Dispute + Cancel + View QR

Modals:
├── View BTO detail (read-only)
├── Receive wizard (qty step → upload step → summary step → confirm)
├── Capital preview dialog (price-drop warning)
├── Accept-receipt dialog (variance accepted, optional incident ticket)
├── Dispute-receipt dialog (note required)
├── Park-Resume dialogs
└── BT Upload QR (mobile receipt upload)

Separate routes
├── /approve-transfer/:id  (admin SMS landing)
└── /doc/:code → DocViewerPage
    └── If doc is purchase_order with is_branch_request → view-only "open in app"
```

---

## 4. End-to-end flow today (happy path)

```
                ┌──────────────┐
                │ Branch A     │
                │ (requester)  │
                └──────┬───────┘
                       │ 1. Tab "Request Stock" — pick supplier=Branch B,
                       │    products, qty, notes → POST /purchase-orders
                       │    (po_type=branch_request, status=requested)
                       ▼
        ┌──────────────────────────────────────┐
        │ purchase_orders document             │
        │ status=requested · doc_code=ABC123   │
        └──────┬─────────────────────────┬─────┘
               │                         │ QR on printed slip
               │ SMS auto-fires to       │ → /doc/ABC123 → view-only
               │ branch_stock_request    │ page (no action)
               │ recipients in Branch B  │
               ▼                         │
        ┌──────────────┐                 │
        │ Branch B     │ ◄───────────────┘
        │ (supplier)   │
        └──────┬───────┘
               │ 2. Login → Branch Transfers → Transfers → Requests pill
               │    → Incoming → Click "Generate Transfer"
               │    POST /purchase-orders/{po_id}/generate-branch-transfer
               │    PO status flips to in_progress
               ▼
        ┌─────────────────────────────────────┐
        │ "New Transfer" composer pre-fills:  │
        │   send_qty = min(requested,         │
        │                  available_stock)   │
        │   branch_capital from branch_prices │
        │   transfer_capital = branch_capital │
        │   branch_retail from price-memory   │
        └──────┬──────────────────────────────┘
               │ 3. Manager edits qty / capital / retail / margin
               │    → "Send Now" (or Submit for Approval if toggled)
               │    POST /branch-transfers
               │    (status=draft, then /send → status=sent)
               │       — or status=pending_approval → admin approves → status=sent
               ▼
        ┌──────────────────────────┐
        │ branch_transfer_orders   │
        │ status=sent              │
        │ request_po_id = ABC...   │
        └──────┬───────────────────┘
               │ 4. Notification + SMS to Branch A
               ▼
        ┌──────────────┐
        │ Branch A     │
        │ (destination)│
        └──────┬───────┘
               │ 5. Branch Transfers → Transfers → In Transit → "Receive"
               │    Receive wizard: enter received qty → upload receipt
               │    photo → confirm → POST /branch-transfers/{id}/receive
               ▼
        ┌─────────────────────────────────────┐
        │ Variance ?                          │
        │   no  → status=received,            │
        │         inventory moved at dest,    │
        │         PO marked fulfilled/partial │
        │   yes → status=received_pending,    │
        │         source must Accept/Dispute  │
        └─────────────────────────────────────┘
```

---

## 5. Issues found (audit)

Each issue is tagged `S` (severity: S0=critical, S1=high, S2=medium,
S3=low) and `T` (target: process / ux / data / api). Numbering is
stable so the second AI can reference it back.

### 5.1 Discoverability / mental model

* **BT-AUD-01 — `S1·ux`**: "Requests" lives **inside** the Transfers
  tab's status-pill row, not as a top-level tab. Operators we trained
  consistently miss it. The "Request Stock" tab (which is the WRITE
  side) and "Requests" pill (the READ side) live on opposite ends of
  the page. Pull them into a clear 2-column Inbox/Outbox layout.
* **BT-AUD-02 — `S1·ux`**: Two parallel collections (PO + BTO) with
  separate doc_codes, separate QR slips, separate print pipelines for
  what is one logical transaction. Operators print TWICE today.
* **BT-AUD-03 — `S2·ux`**: 13+ statuses across the two docs. No glossary
  in-app. Status pill labels diverge from raw status fields
  (`sent` → "In Transit", `received_pending` → "Needs Review", etc.).

### 5.2 The owner's requested loop is missing

* **BT-AUD-10 — `S0·process`**: **Branch B cannot, today, confirm
  "I will send these quantities" without opening the full New
  Transfer composer.** The PO has no `confirmed_qty` field. The only
  way to communicate "I can send 8 of the 10 you asked for" is to
  build and dispatch the BTO. There is no intermediate review step.
* **BT-AUD-11 — `S0·process`**: **Scanning the QR on Branch A's
  printed stock-request from Branch B's phone has no action.**
  `DocViewerPage.jsx:2183-2199` shows a view-only summary and tells
  the user to open the app. The owner explicitly wants a mobile
  scan-to-modify flow.
* **BT-AUD-12 — `S0·ux`**: **Branch A has no visibility into "what
  Branch B will send" until the BTO is `sent`.** They see only their
  original request + an opaque "Transfer In Progress" badge — no
  per-line draft from Branch B, no ETA, no shortage hint.
* **BT-AUD-13 — `S1·data`**: If Branch B starts editing in "New
  Transfer" then closes the tab without parking or saving, the PO
  remains `in_progress` and **all edits are lost.** Park-on-leave
  works but doesn't write back to the PO either — Branch A still sees
  nothing.

### 5.3 Process gaps

* **BT-AUD-20 — `S2·process`**: `pending_approval` ↔ `returned` flow is
  optional (flag `requires_approval`). Single-branch shops never need
  it; multi-branch with strict pricing always need it. There's no per-
  org toggle to enforce it.
* **BT-AUD-21 — `S2·process`**: A returned BTO can be edited (PUT
  `/branch-transfers/{id}`) and then `/resubmit`'d. The admin sees the
  resubmission, but the audit trail of what changed between
  submissions is not surfaced in the UI.
* **BT-AUD-22 — `S2·process`**: The accept/dispute receipt flow requires
  the source's manager+ to act. There is no SLA, no escalation, no
  auto-accept after N days. A receipt-pending BTO can sit forever and
  inventory at the destination remains unmoved.
* **BT-AUD-23 — `S1·process`**: Receipt-photo requirement is
  asymmetric: web `/receive` enforces it (400 if missing — soft front-
  end confirm bypasses with `window.confirm`), terminal/QR receive
  silently skips it. A determined operator can side-step it via
  terminal.
* **BT-AUD-24 — `S2·process`**: PO cancel needs manager PIN; BTO
  `cancelled` doesn't. Once a BTO is `pending_approval` and the source
  cancels with no PIN, the linked PO is left dangling on
  `in_progress`. No back-link.
* **BT-AUD-25 — `S2·process`**: When BTO finalizes as
  `partially_fulfilled`, the PO is marked the same — but the operator
  is not prompted to "create a follow-up request for the missing
  qty". Frequent ops complaint.
* **BT-AUD-26 — `S3·process`**: Stock-request notes are SMS-broadcast
  but free-form text — no urgency level, no expected-date. (Branch A
  has no field for "needed by Friday" except in notes.)

### 5.4 Backend issues

* **BT-AUD-30 — `S1·data`**: Internal-invoice create on BTO write is
  wrapped in bare `try/except: pass` (`branch_transfers.py:439-444`).
  An accounting mirror that silently fails is a footgun. At minimum
  log + queue retry.
* **BT-AUD-31 — `S2·api`**: `GET /branch-transfers` doesn't expose a
  "role I play in this transfer" hint (source vs. destination); the
  frontend has to compute it on every render.
* **BT-AUD-32 — `S2·api`**: `accept_receipt` and `dispute_receipt`
  share branch-restriction logic but with opaque double-negative
  (`user_branch and user_branch != order["from_branch_id"]`). Adding
  a helper `assert_is_source_branch(order, user)` would prevent the
  next regression here.
* **BT-AUD-33 — `S2·api`**: `update_transfer` allows editing a
  `pending_approval` BTO without re-triggering admin notification.
  Edit-then-approve is fine, but the admin's SMS link points to a
  pre-edit snapshot.
* **BT-AUD-34 — `S2·data`**: `branch_transfer_orders.organization_id`
  is set on insert, but `update_transfer` (PUT) merges via `$set`
  without re-stamping. Edge case: org-context-less super-admin edit
  can blank it. Same family as the H-1 bug we just patched on
  invoices. Worth a defensive `assert org_id` here.
* **BT-AUD-35 — `S2·api`**: `accept-receipt`'s `accept_with_incident`
  silently creates an `incident_ticket` document — no audit log entry
  identifying which user PIN-bypassed approval (since the route
  doesn't require PIN — `manager/admin` role check only).
* **BT-AUD-36 — `S2·api`**: `receive_transfer` and
  `terminal_receive_transfer` diverge on receipt-check, branch-check,
  and skip flags. Two code paths doing the same thing with subtly
  different invariants.

### 5.5 Frontend issues

* **BT-AUD-40 — `S1·ux`**: New-transfer composer is **1500 lines** of
  inlined logic. Rows store transient + persistent + computed state in
  one shape. Hard to reason about; the recent dot-matrix fix needed
  surgical edits across many places.
* **BT-AUD-41 — `S2·ux`**: "Resume Transfer" (in_progress) button
  silently re-fetches fresh pre-fill from the PO — so any prior edits
  are lost. Button label suggests continuation but it's a fresh start.
* **BT-AUD-42 — `S2·ux`**: 4 separate dialogs for accept/dispute/
  capital-preview/receipt-upload — they can stack. No focus trap; tab
  navigation across them is unreliable on small laptops.
* **BT-AUD-43 — `S3·ux`**: Receipt photo "soft warning" uses native
  `window.confirm` — does not match the design system. Easy to
  click-through accidentally on touch devices.
* **BT-AUD-44 — `S2·ux`**: Receive variance flow asks the destination
  to "double-check" with a hard step but lacks a one-tap "everything
  was as ordered, confirm" path on the same screen.
* **BT-AUD-45 — `S3·ux`**: Stock-request "incoming" cards on the
  Transfers tab don't show estimated capital impact. Branch B can't
  triage which request to fulfill first based on margin / value.

### 5.6 Print / QR issues

* **BT-AUD-50 — `S2·print`**: Stock-request and BTO use **different**
  print pipelines (PO vs. branch_transfer). Layouts diverge slightly —
  for one transaction the operator has to learn two layouts.
* **BT-AUD-51 — `S2·print`**: Dot-matrix layout for stock-request
  inherits from PO template — does not include Branch B's "will-
  send" column (because that column doesn't exist yet).
* **BT-AUD-52 — `S1·qr`**: The QR on a printed stock request links to
  a view-only page. From a logistics standpoint this is wasted
  paper — operators expect to scan it, do something, and go.

---

## 6. Proposed redesign — Stock-Request Confirmation Layer

This satisfies the owner's specific request without rewriting
anything. It adds **one new collection field set on existing
`purchase_orders.items`** and **one new public QR action**. The BTO
side stays exactly as today.

### 6.1 New states + fields

Add to each `purchase_orders` doc with `po_type=branch_request`:

```
confirmation_status: "pending" | "partial" | "full" | "declined"
confirmed_at: ISO8601
confirmed_by: user_id
confirmed_by_name: display string
confirmed_note: free-text reason (optional)
items[].confirmed_qty: float   # what Branch B promises to send
items[].confirmed_note: optional per-line reason
```

PO `status` lifecycle gets one new value:

```
requested → confirmed   (NEW — added when Branch B confirms via mobile or web)
requested → in_progress (legacy path — still allowed; auto-creates a "full" confirmation)
```

`fulfilled` / `partially_fulfilled` semantics unchanged.

### 6.2 New API endpoints

1. **`POST /qr-actions/{code}/confirm_stock_request`** (public, no JWT, PIN-gated)
   ```json
   {
     "pin": "521325",
     "items": [{ "product_id": "p1", "confirmed_qty": 8 }, ...],
     "note": "Out of pinto beans — sending 8 of 10"
   }
   ```
   Validates PIN against new policy `confirm_stock_request` (defaults:
   admin_pin + manager_pin + TOTP — same shape as
   `transfer_approve`). Stores per-line `confirmed_qty`. Sets
   `confirmation_status` to "full" / "partial" / "declined" based on
   compare. Bumps PO `status` to `confirmed`. Notifies Branch A via
   in-app + SMS ("Branch B has confirmed your stock request — see
   confirmed qty inside").

2. **`POST /purchase-orders/{po_id}/confirm-request`** (JWT, role manager/admin)
   Web-side mirror of the QR endpoint so confirmation can happen
   without a phone.

3. **`GET /purchase-orders/{po_id}/confirmation`** (JWT)
   Returns the side-by-side ledger: requested vs. confirmed for each
   line + totals. Used by the preview and by Branch A's outgoing card.

### 6.3 New mobile UX (scan QR → modify → confirm)

`/doc/:code` for a `purchase_order` with `is_branch_request=true`
gains a new action below the view-only summary:

```
[ Modify quantities — manager PIN required ] ◄─ NEW BUTTON
```

Tap → PIN sheet → on success, the page becomes a per-line
editable list:

```
Product             | Requested | Will Send    | Available at me
Pinto Beans 5kg     |    10     | [   8    ]   |   8  ⚠
Urea 50kg           |     2     | [   2    ]   |  12  ✓
Pesticide A         |     5     | [   5    ]   |   5  ✓
                          totals 17  / 15
            ┌────────────────────────────────────────────────┐
            │  Note: Out of pinto beans, sending 8 of 10     │
            └────────────────────────────────────────────────┘
                  [ Preview Confirmation → ]
```

Preview screen (read-only summary):

```
You are confirming:
Pinto Beans 5kg   …  8 of 10  (short 2) ⚠
Urea 50kg         …  2 of 2   (full)    ✓
Pesticide A       …  5 of 5   (full)    ✓
Status: Partial Fulfillment

         [ Edit ]      [ Confirm & Notify Branch A ]
```

On confirm: POST `/qr-actions/{code}/confirm_stock_request` → success
toast → Branch A is SMS'd + in-app notified.

### 6.4 New web UX (no scan — direct confirmation)

On the existing Transfers → Requests → Incoming card, replace the
"Generate Transfer" CTA when status is `requested`:

```
[ Print ] [ 58mm ] [ Dot-Matrix ] [ Cancel ] [ Confirm Quantities → ]
```

"Confirm Quantities" opens a modal with the same side-by-side ledger
as the mobile preview, plus per-line CalcInput. Submit hits
`POST /purchase-orders/{po_id}/confirm-request`.

When status is `confirmed`:

```
[ Print ] [ 58mm ] [ Dot-Matrix ] [ Re-confirm ] [ Generate Transfer → ]
```

"Re-confirm" lets Branch B amend qty before they generate the BTO.
"Generate Transfer" still does what it does today, but now uses
`confirmed_qty` as the pre-fill instead of `requested_qty` —
satisfying the user's exact ask:

> When I go back to the web and click Resume Transfer, the order
> quantity of Branch A vs how much I can give will be visible (the
> updated one).

### 6.5 Branch A visibility (Outgoing card)

The outgoing-request card for a PO in `confirmed` status now shows:

```
PO-MB-001022 · status: Confirmed by Branch B (Partial)
Pinto Beans 5kg  …  Requested 10 · Confirmed 8 (short 2)
Urea 50kg        …  Requested 2  · Confirmed 2 ✓
Pesticide A      …  Requested 5  · Confirmed 5 ✓
Note: Out of pinto beans — sending 8 of 10
ETA: not provided
                                        [ Print | Cancel ]
```

This closes the visibility loop.

### 6.6 Linkage to existing BTO flow

`generate-branch-transfer` (the backend already supports re-entry
when `status=in_progress`) is extended to:

* Pre-fill `send_qty` from `confirmed_qty` when set (else
  `min(requested_qty, available_stock)` as today).
* Mark PO status `in_progress` (unchanged).
* Mark `branch_transfer_orders.from_confirmation = true` so
  reporting can distinguish "confirmed first then dispatched" vs.
  "direct dispatch".

### 6.7 Backwards compatibility

* All existing flows keep working. A PO that never gets confirmed
  flows through `requested → in_progress → fulfilled` exactly as
  today.
* `generate-branch-transfer` checks for `confirmed_qty` and falls
  back to legacy default if missing. No DB migration required for
  legacy POs.
* No BTO endpoints change.
* No new collections needed.

### 6.8 Surgical change inventory

| File | Change |
|---|---|
| `backend/routes/purchase_orders.py` | New: `POST /{po_id}/confirm-request`; new: `GET /{po_id}/confirmation`; minor edit to `generate-branch-transfer` (use `confirmed_qty` if present). |
| `backend/routes/qr_actions.py` | New: `POST /{code}/confirm_stock_request` (PIN-gated, mirrors web endpoint). |
| `backend/routes/sms.py` | New SMS template: `stock_request_confirmed`. |
| `backend/routes/notifications.py` | New notif type: `stock_request_confirmed`. |
| `frontend/src/pages/DocViewerPage.jsx` | Stock-request action block: PIN gate + editable per-line ledger + preview + submit. |
| `frontend/src/pages/BranchTransferPage.js` | Incoming-requests card: "Confirm Quantities" modal + status-aware CTAs. Outgoing-requests card: confirmed-qty display. |
| `backend/tests/business_regression/` | New: `test_br_stock_request_confirmation.py` — covers requested → confirmed full → BTO; requested → confirmed partial → BTO; QR-confirm flow with idempotency. |

Net new files: 1 BR test. Net new endpoints: 3. Net new collection
fields: 5 on `purchase_orders` (already an existing collection).
Roughly **300–450 LOC** including tests.

---

## 7. Open questions for owner / second-opinion AI

These should be answered before any code is written:

1. **Confirmation PIN policy** — should mobile-scan confirmation
   require the same auth tier as `transfer_approve` (admin + manager
   + TOTP) or a softer tier (manager PIN only)? Mobile scans tend to
   happen at receiving dock; we recommend keeping it at
   manager-or-above.
2. **Re-confirmation rule** — can Branch B re-confirm after Branch A
   has seen the confirmation? Recommendation: yes, but Branch A
   should be notified again. Soft-lock once the BTO is generated
   (status `in_progress`+).
3. **"Decline" path** — should Branch B be able to fully decline
   ("we have nothing")? Recommendation: yes — `confirmation_status =
   "declined"`, no `confirmed_qty`, PO closes as `cancelled` with a
   reason, Branch A is notified to find another supplier.
4. **ETA / pickup date** — should the confirmation carry an
   `expected_dispatch_date`? Owner has mentioned customer SMS for
   prepared orders — same UX pattern would help Branch A plan a pickup.
5. **Auto-cancel SLA** — if Branch B doesn't confirm within N days,
   should the PO auto-`cancel`? Recommendation: leave it manual for
   now; revisit after live usage data.
6. **Audit trail** — every confirm should write to `audit_log` (same
   collection used for variance-accept). Confirm.
7. **Does this fold the existing `requires_approval` flow on the BTO
   side?** Recommendation: no. Confirmation is "Branch B → Branch A"
   alignment. Approval is "Manager → Admin" governance. Orthogonal.

---

## 8. Recommended phased rollout

| Phase | Scope | LOC | Risk |
|---|---|---|---|
| **P1** | Backend: confirm endpoint + new fields + BR test. | ~120 | Low — additive only |
| **P2** | Web modal: "Confirm Quantities" on incoming card; outgoing card shows confirmed_qty. | ~150 | Low — net new UI block |
| **P3** | Mobile QR: PIN + ledger + preview + submit on DocViewer. | ~180 | Medium — public endpoint, must be PIN-hardened and rate-limited (reuse `check_qr_lockout`). |
| **P4** | SMS template + notification type. | ~30 | Low |
| **P5** | (Optional) Decline path + auto-cancel SLA + ETA field. | ~80 | Low — additive |

Phases 1–3 deliver the owner's full request; 4–5 are polish.

---

## 9. Appendix — key file pointers

| File | What lives here |
|---|---|
| `/app/backend/routes/branch_transfers.py` (1957 LOC) | All BTO endpoints, capital preview, receipt accept/dispute, terminal hooks, markup templates. |
| `/app/backend/routes/purchase_orders.py` | Stock-request creation, listing, `generate-branch-transfer` (PO → BTO conversion). |
| `/app/backend/routes/parked_branch_transfers.py` (144 LOC) | Park / Resume of composer state. |
| `/app/backend/routes/doc_lookup.py` (612 LOC) | Doc-code generation, lookup, search; public view endpoint with action menus. |
| `/app/backend/routes/qr_actions.py` (1108 LOC) | All public QR actions: release_stocks, receive_payment, transfer_receive, update_draft. **This is where `confirm_stock_request` should live.** |
| `/app/backend/routes/internal_invoices.py` | Accounting mirror of every BTO. |
| `/app/backend/routes/sms.py` | SMS templates + queue. |
| `/app/frontend/src/pages/BranchTransferPage.js` (3519 LOC) | All web UI for transfers + requests. |
| `/app/frontend/src/pages/DocViewerPage.jsx` (2275 LOC) | Mobile QR landing page — invoice, PO, stock-request, BTO. |
| `/app/frontend/src/pages/ApproveTransferPage.js` (448 LOC) | Admin SMS-link approval page. |
| `/app/frontend/src/lib/PrintEngine.js` | Centralized print pipeline (PO, invoice, BTO, dot-matrix, 58mm). |
| `/app/backend/tests/business_regression/test_br3_branch_transfer.py` | Canonical BR3 test — proves the BTO money/inventory invariants. |

---

## 10. What this audit does NOT cover

* Repack × Branch Transfer interaction (deferred BR7 — see
  `PHASE_5_POST_DEPLOY_NEXT_FORK_HANDOFF.md §5`).
* Multi-org tenant isolation of branch transfers (already covered by
  the H-1 fix + `test_br_iso_internal_invoices.py`).
* Performance / pagination (transfer history list returns ≤40 rows by
  default; no observed scale issue yet).
* Mobile responsive design of the composer (it's desktop-first today).

— End of audit —

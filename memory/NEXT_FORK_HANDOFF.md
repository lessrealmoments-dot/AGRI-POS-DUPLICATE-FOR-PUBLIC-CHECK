# Next Fork Handoff — AgriBooks
Last updated: 2026-04-25

---

## IMMEDIATE NEXT TASK (In Progress — NOT YET CODED)

### /payments Page — Full UI Redesign + Inline Interest + Account Summary

**Status: DESIGNED, APPROVED BY USER, NOT YET IMPLEMENTED**

The user approved this plan in the last conversation. Build it exactly as described.

---

### Part 1: Layout Redesign (Space Problem)

Currently only 2 invoices are visible. Root cause: too many `shrink-0` elements above the invoice table eating vertical space.

**Current vertical stack (problem):**
```
Header (shrink-0) ~180px
  - Title + Customer Balance
  - Received From field
  - Payment Amount + Date + Ref
  - Payment Method icons
No-rate prompt (shrink-0) ~80px
Generate Interest card (shrink-0) ~50px
Invoice table (flex-1) ~300px  ← too small, shows only 2 rows
Footer with Pay button ~80px
```

**Target layout:**
```
Compact header (shrink-0) ~100px
  - Row 1: Received From + Balance display (same line)
  - Row 2: Amount + Date + Ref + Payment Methods (same line)
Invoice table (flex-1 ScrollArea) — gets ~200px more
  - Each invoice row: compact 2-line design (~60px)
  - Inline interest sub-row for overdue invoices (~28px)
Account Summary card (shrink-0) ~90px
  - Outstanding Principal    ₱8,500.00
  - Accrued Interest Charges   ₱161.95
  - ──────────────────────────────────
  - Total Amount Due          ₱8,661.95
Pay button footer (shrink-0) ~56px
```

**Key changes:**
1. Compact header: remove extra padding, merge rows, shrink from ~180px to ~100px
2. REMOVE the "Generate Interest / Penalty Charges" collapsible card entirely — replaced by inline interest rows per invoice. Manual force button becomes a small icon/button near the header or in the Account Summary.
3. Invoice rows: more compact 2-line layout instead of current tall rows
4. Account Summary replaces old footer total — shows professional breakdown
5. Net result: 4-6 invoices visible at once instead of 2

---

### Part 2: Inline Interest Per Invoice Row

For each OVERDUE invoice in the list, show a sub-row below it:

```
┌── SI-20260226-0002 ─── Jan Credit Sale ─── Due: Feb 15 (45d overdue) ─── [₱891.52] ──┐
│   └─ Interest: 45d × 2%/mo = ₱26.75 ──────────────────── subtotal: ₱918.27          │
└──────────────────────────────────────────────────────────────────────────────────────┘
┌── SVC-20260226-0001 ─── Farm Expense ─── Not yet due ─────────────────── [₱1,995.00] ┐
│   (no interest sub-row — not overdue)                                                  │
└───────────────────────────────────────────────────────────────────────────────────────┘
```

**Interest computation for inline display:**
- Use `charges-preview` endpoint or compute client-side:
  `interest = principal × (rate/100/30) × days_overdue`
- This is DISPLAY ONLY — no INT invoice created just from browsing
- Only show when: invoice has due_date < today AND customer has interest_rate > 0

**autoApply behavior with inline interest:**
- When staff enters payment amount, autoApply fills invoice amounts
- The inline interest is informational — actual INT invoice is created at payment processing time (see Part 3)

---

### Part 3: Account Summary Footer Card

Replace the current footer totals with a professional summary card:

**Labels (user-approved wording):**
```
Outstanding Principal      ₱8,500.00   ← sum of all open invoice balances (non-interest invoices)
Accrued Interest Charges     ₱161.95   ← sum of computed interest on overdue invoices
────────────────────────────────────
Total Amount Due           ₱8,661.95   ← grand total
```

Optional secondary line (small text):
```
ⓘ Interest is applied first. Remaining balance covers oldest invoices.
```

**Where the interest number comes from:**
- Sum of open INT invoices (if they exist) + computed interest on invoices with no INT yet
- OR use charges-preview API for the computed total
- Updates LIVE as payment amount is entered (showing what remains after payment)

---

### Part 4: INT Invoice Creation — Only at Pay Time

**IMPORTANT ARCHITECTURE DECISION (user-approved):**

- Opening /payments → NO INT invoice created (just display/preview)
- Clicking Pay button → AUTO-GENERATE INT invoice (force=True) BEFORE applying payment
  - Only if: customer has overdue invoices AND interest_rate > 0
  - Call `POST /api/customers/{id}/generate-interest` with `{force: true}` before the payment API call
  - INT invoice appears → autoApply already has it in queue → interest paid first → then principal
- SMS scheduler → keeps 30-day minimum interval guard

**Remove autoGenerateAndLoad from customer select entirely** — it currently auto-generates INT invoice on customer select. Replace with just `loadInvoices` + the inline display.

The `autoGenerateAndLoad` function in PaymentsPage.js should be simplified to just:
1. Load invoices
2. Show rate prompt if overdue + no rate set
3. NO interest generation

---

## Completed in This Session (2026-04-25)

### Security Fixes
- `totp_secret` removed from login/me API response (replaced with `has_totp: bool`)
- Global error handler returns generic message (no exception details to client)
- Hardcoded superadmin credentials moved from `main.py` to `.env`
- Doc lookup endpoint now has brute-force lockout (same as QR actions)
- Legacy files deleted: `server_legacy.py`, `server_backup.py`, `server_modular.py`, `TerminalMessages.jsx`
- Legacy routes `/pos` and `/sales-order` now redirect to `UnifiedSalesPage`

### Crop Credits Balance Fix
- `principal_balance` in /crop-credits now uses `_compute_total_customer_balance()` — ALL invoice types included (regular + INT + penalty) — matches /customers and /payments exactly
- `total_due` = `principal_balance` only (INT invoices carry interest, no double-count with accrued_interest field)
- `accrued_interest` on crop doc is now informational/projected only

### Forgot Password Fix
- Frontend sends `origin: window.location.origin` with forgot-password request
- Backend uses that origin to build reset link — works for both live site and preview environments

### /payments Auto-Generate + No-Rate Prompt
- Customer select auto-generates interest (force=false, 30-day guard)
- No-rate prompt banner shows when customer has overdue invoices but no interest rate set
- Manual "Generate Interest" button passes force=true

### SMS Notification System — Full Reprogramming
- 7 new SMS templates added: `crop_season_started`, `crop_credit_added`, `crop_harvest_15day`, `crop_harvest_7day`, `crop_harvest_due`, `crop_extension`, `crop_overdue_notice`
- Monthly interest accrual scheduler REMOVED (interest via /payments only)
- Daily reminders skip crop credit customers (they get harvest reminders)
- Post-harvest overdue cycle: every 7 days until settled or extended; stops on extension
- `_ensure_templates()` now upserts missing keys (not count=0 only) — fixed missing templates

### CC Rules Implemented
**Term credits:**
- `credit_new` → customer + manager CC
- `charge_applied` → customer + manager CC
- `reminder_7day` → customer + manager CC
- `overdue_notice` → customer every 7d; owner+manager CC on day 7 + every 14 days

**Crop credits:**
- `crop_season_started` → customer + owner CC
- `crop_harvest_15/7d/due` → customer + all staff (owner/admin/manager/auditor)
- `crop_extension` → customer + all staff
- `crop_overdue_notice` → customer every 7d; all staff on day 7 + every 14 days

### Branch-Specific CC (`_get_cc_phones`)
- New central helper in `sms_hooks.py`
- owner/admin: global (all branches)
- manager/auditor: branch-specific (with global fallback)
- Settings UI updated: Global section + Per-Branch section per branch
- Schema: `branch_phones: {branch_id: {manager_phone, auditor_phone}}`

### INT Invoice Accumulation Fix
- `generate_interest_invoice` has 30-day minimum interval guard (force=False)
- Auto-generate on customer select passes force=False → respects guard
- Manual button passes force=True → always generates
- Response includes `skipped: true`, `days_since_last`, `days_until_next` when blocked

### SMS Formula Fixes
- All SMS now use LIVE invoice balance sum (not stale `customer.balance`)
- Overdue SMS uses actual INT invoice balance (not `balance × rate%` estimate)
- Monthly summary uses live aggregation pipeline
- `on_credit_sale_created`, `on_charge_applied` use live invoice sum

---

## Pending Issues (NOT YET FIXED)

### P1 — Thermal Printing Bleeding on 58mm
Apply to `PrintEngine.js` thermalCSS:
1. Reduce QR image to 100-112px
2. Set QR margin: 3 in PrintBridge
3. Change grand total border-top to 1px solid #000
4. Remove background, use solid #000 for partial release rows
5. Reduce .biz-detail font to 10px

### P1 — Crop Credit Status Badge on Customers Page
Show harvest status + days-to-harvest directly on the Customers list

### P1 — Compliance Calendar Widget on Dashboard
Widget showing upcoming document deadlines (red=expired, amber=expiring within 30d)

### P1 — Finance Reports
Discount cashier drill-down + AP payment history per supplier views

### P2 — ESLint Warnings (recurring)
`react-hooks/exhaustive-deps` in TerminalSales.jsx, UnclosedDaysBanner.jsx, PaymentsPage.js, SetupWizardPage.js, SuppliersPage.js

### P2 — Codebase Refactoring
- `SuperAdminPage.js` 1600+ lines — needs splitting
- `main.py` startup 900+ lines — needs splitting

---

## Key Architecture Notes

### Crop Credit Balance Rule
`principal_balance` shown in /crop-credits = sum of ALL customer invoices (regular + INT + penalty)
= SAME number as /customers and /payments
`accrued_interest` on crop doc = informational projection only, NOT added to displayed balance
`total_due` = `principal_balance` (no separate interest added)

### INT Invoice Lifecycle
- Created: by staff (manual force), or by Pay button (auto, force=True), or by scheduler (monthly, force=False)
- 30-day guard: prevents accumulation when force=False
- SMS scheduler: generates before overdue SMS (30-day guard) so SMS balance matches /payments

### _get_cc_phones() — Branch-Specific CC
```python
from routes.sms_hooks import _get_cc_phones
phones = await _get_cc_phones(org_id, branch_id, {"owner", "admin", "manager", "auditor"})
# Returns dict: {role: phone} — only roles with configured phones
# owner/admin: global; manager/auditor: branch-specific with global fallback
```

### SMS Templates
All 17 templates now in DB. `_ensure_templates()` upserts missing keys on every call to /sms/templates.
New crop templates: crop_season_started, crop_credit_added, crop_harvest_15day, crop_harvest_7day, crop_harvest_due, crop_extension, crop_overdue_notice

### generate_interest_invoice API
```
POST /api/customers/{id}/generate-interest
{
  "as_of_date": "YYYY-MM-DD",
  "rate_override": 2.0,
  "force": false,     // false = 30-day guard (auto-generate), true = manual override
  "save_rate": false
}
Response when blocked: {"skipped": true, "days_since_last": 1, "days_until_next": 29, "total_interest": 0}
Response when generated: {"invoice_number": "INT-B1-001003", "total_interest": 138.63}
```

---

## Files Modified in This Session

**Backend:**
- `/app/backend/routes/auth.py` — totp_secret excluded, dynamic reset link
- `/app/backend/routes/crop_credits.py` — balance computation, harvest notifications, hooks
- `/app/backend/routes/sms.py` — 8 new templates, upsert ensure_templates
- `/app/backend/routes/sms_hooks.py` — CC rules, _get_cc_phones, on_crop_season_started, on_crop_credit_added, live balance
- `/app/backend/routes/settings.py` — branch_phones schema in collection-recipients
- `/app/backend/routes/doc_lookup.py` — brute-force lockout
- `/app/backend/routes/accounting.py` — 30-day INT guard, live balance in SMS hooks
- `/app/backend/main.py` — scheduler fixes, CC rules, live balance in SMS, crop customer skip
- `/app/backend/.env` — SUPER_ADMIN_EMAIL, SUPER_ADMIN_PASS added

**Frontend:**
- `/app/frontend/src/pages/PaymentsPage.js` — auto-generate, no-rate prompt, force=true manual, 30-day toast
- `/app/frontend/src/pages/MessagesPage.js` — branch-specific CC settings UI
- `/app/frontend/src/pages/ForgotPasswordPage.js` — sends origin in request
- `/app/frontend/src/App.js` — legacy routes redirect to UnifiedSalesPage

**Deleted:**
- `/app/backend/server_legacy.py`
- `/app/backend/server_backup.py`  
- `/app/backend/server_modular.py`
- `/app/frontend/src/pages/terminal/TerminalMessages.jsx`

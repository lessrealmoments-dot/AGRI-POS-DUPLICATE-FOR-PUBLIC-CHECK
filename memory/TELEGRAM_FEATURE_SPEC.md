# Telegram Bot Integration — Feature Specification

## Overview
Add Telegram bot notifications as an additional channel alongside existing SMS.
Multi-tenant: each tenant sets up their own bot(s) via @BotFather.
Telegram is for MANAGEMENT/STAFF alerts only (not customer-facing).
SMS continues for customer-facing messages.

## Architecture: 5 Specialized Bots per Tenant

| # | Bot Name | What It Sends | Who Cares |
|---|----------|--------------|-----------|
| 1 | **Daily Report Bot** | Z-report summary + PDF, daily closing reminder, day-end recap, unclosed day alerts | Owner, Manager, Auditor |
| 2 | **Credit & AR Bot** | New credit sale, overdue notice, payment received, monthly AR summary, historical credit alerts | Owner, Manager, Collection staff |
| 3 | **Stock & Inventory Bot** | Slow-moving stock alerts, low-stock alerts, stock discrepancy tickets, count sheet variances | Owner, Manager, Auditor |
| 4 | **Purchase & Receiving Bot** | PO received (needs review), supplier payment due, receipt review reminders | Owner, Auditor, Purchasing staff |
| 5 | **Operations Bot** | Branch transfer status, stock request updates, return/refund processed, void notifications | Owner, Manager |

### Key Design Decisions (User-Confirmed)
- One bot token CAN send to multiple GCs, but multiple bots in one GC is better UX (each bot has its own name + avatar — visual segregation)
- Group chat structure: 1 GC per branch + 1 admin/owner GC for cross-branch visibility
- Telegram runs ALONGSIDE SMS (configurable per tenant: SMS only, Telegram only, or both)
- Telegram removes character limits — can send full product lists, PDFs, QR codes, clickable links
- Minimum viable setup: 1 bot + 1 GC = working. Other 4 bots are optional (progressive disclosure)

## Tenant Setup Flow (User-Confirmed: Option A — Auto-detect)
1. Tenant creates bot(s) via @BotFather (we provide step-by-step guide in Settings)
2. Pastes bot token in AgriBooks Settings → Telegram
3. Adds the bot to their group chats
4. AgriBooks auto-detects GC when bot is added (via getUpdates polling)
5. Tenant maps: this GC = Branch X, receives: [Z-Reports, Stock Alerts, Credits]
6. Tenant maps: this GC = Admin/Owner, receives: [Everything]

## Settings UI Vision
```
Daily Report Bot: [token saved check]
  |- Ipil Main GC check  — sends: [check Z-Report] [check Close Reminder] [check Day Recap]
  |- Sibugay GC check    — sends: [check Z-Report] [check Close Reminder]  
  |- Admin GC check      — sends: [check Z-Report] [check Close Reminder] [check Day Recap]

Stock & Inventory Bot: [token saved check]  
  |- Admin GC check      — sends: [check Slow-Moving] [check Low Stock]
  |- Ipil Main GC check  — sends: [check Slow-Moving]

Credit & AR Bot: [not configured]
  -> Set up bot token to enable
```

## What Telegram Can Send That SMS Can't
- Z-Report PDF attachment at end of day
- Clickable links to view invoice/report in AgriBooks
- QR code images for receipts
- Full product lists (no character limit)
- Formatted messages with bold, code blocks, etc.

## Existing SMS Infrastructure to Wire Into

### Key Functions (routes/sms.py — 3,472 lines)
- `queue_sms()` — main queuing function with dedup, throttle, templates, quiet hours
- `render_template()` — `<variable>` placeholder substitution
- `DEFAULT_TEMPLATES` — 30+ template keys auto-seeded per org
- `sms_settings` — per-trigger enable/disable per branch
- `ENQUEUE_THROTTLE_SECONDS = 600` (10 min per-recipient per-template)
- `MAX_SMS_PER_PHONE_PER_DAY = 10`

### SMS Hook Functions (routes/sms_hooks.py — 608 lines)
- `_get_cc_phones()` — resolves owner/admin/manager/auditor phones per branch
- `on_credit_sale_created()`, `on_payment_received()`, `on_charge_applied()`
- `on_crop_season_started()`, `on_crop_credit_added()`
- `on_invoice_voided()`, `on_refund_processed()`, `on_stock_correction_refunded()`

### Scheduled Jobs (main.py)
- `_daily_sms_reminders` — 8:00 AM daily (credit due reminders)
- `_monthly_sms_summary` — 1st of month, 9:00 AM (balance summaries)
- `run_harvest_reminders` — 7:00 AM daily (crop credit harvest)
- Close reminder scheduler — multi-stage (approaching, at, overdue, day-after)

### Existing SMS Template Keys
opening_balance_notice, credit_new, reminder_15day, reminder_7day,
overdue_notice, payment_received, charge_applied, delivery_ready,
promo_blast, monthly_summary, custom, credit_new_staff,
charge_applied_staff, crop_season_started_owner, sale_voided,
refund_processed, stock_correction_refund, pickup_ready,
transfer_pending_approval, transfer_approved, transfer_rejected,
branch_stock_request, phantom_po_ordered, phantom_po_variance,
crop_season_started, crop_credit_added, crop_harvest_15day,
close_overdue_next_day, close_overdue_multi_day,
zreport_finalized, zreport_share_auto_revoked

### Recipient Resolution
`_get_cc_phones(org_id, branch_id, roles)` in sms_hooks.py resolves:
- owner -> global `owner_phone` from system_settings
- admin -> global `admin_phone`
- manager -> branch-specific first, fallback to global `manager_phone`
- auditor -> branch-specific first, fallback to global `auditor_phone`
Config via: system_settings key `collection_notification_recipients`

### Branch Disable Pattern
`close_reminder_disabled` flag on branches document.
Toggle: `PUT /api/sms/close-reminder/{branch_id}/toggle`
Scheduler checks and skips. Purges in-flight SMS on disable.
Mirror this for Telegram per-branch disable.

## Implementation Plan

### Phase 1: Telegram Platform (build first)
1. New route file: `routes/telegram.py`
2. DB collections:
   - `telegram_bots` — {id, org_id, bot_token, bot_username, bot_type, active, created_at}
   - `telegram_group_chats` — {id, org_id, bot_id, chat_id, chat_title, branch_id, report_types: [], active}
   - `telegram_queue` — mirrors sms_queue pattern {id, org_id, bot_id, chat_id, message, format, attachments, status, created_at, sent_at, error}
3. Endpoints:
   - `POST /api/telegram/bots` — save bot token, validate via Telegram API
   - `GET /api/telegram/bots` — list configured bots
   - `DELETE /api/telegram/bots/{bot_id}`
   - `GET /api/telegram/bots/{bot_id}/detect-chats` — poll getUpdates to find GCs
   - `POST /api/telegram/group-chats` — map a detected GC to branch + report types
   - `PUT /api/telegram/group-chats/{gc_id}` — update mapping
   - `DELETE /api/telegram/group-chats/{gc_id}`
   - `POST /api/telegram/send` — internal: queue a message
   - `GET /api/telegram/queue` — admin: view queue
4. Core function: `send_telegram(bot_id, chat_id, message, parse_mode, attachments)`
   - Uses python-telegram-bot or httpx direct to Telegram Bot API
   - Supports: text (Markdown/HTML), photos, documents (PDF)
5. Settings UI: `TelegramSettingsPage.js` or section in existing Messages settings

### Phase 2: Wire Existing Hooks
- Create `telegram_hooks.py` mirroring `sms_hooks.py`
- Each hook checks: "is Telegram configured for this org + branch + report type?"
- If yes, queue Telegram message alongside SMS
- Reuse `render_template()` but with Telegram-flavored formatting (bold, etc.)

### Phase 3: Slow-Moving Stock (first Telegram-native feature)
- Detection engine + report (PO-based, not starting inventory)
- Monthly digest via Telegram (full product list, no character limit)
- First 30-day alert via Telegram

## DB Schema Sketches

### telegram_bots
```
{
  id: str,
  organization_id: str,
  bot_token: str (encrypted at rest),
  bot_username: str,
  bot_type: "daily_report" | "credit_ar" | "stock_inventory" | "purchase_receiving" | "operations" | "universal",
  display_name: str,
  active: bool,
  created_at: str,
  updated_at: str
}
```

### telegram_group_chats
```
{
  id: str,
  organization_id: str,
  bot_id: str,
  telegram_chat_id: int,
  chat_title: str,
  branch_id: str | null,    // null = admin/owner (all branches)
  report_types: [str],       // ["zreport", "close_reminder", "slow_moving", ...]
  active: bool,
  detected_at: str,
  mapped_at: str
}
```

## Dependencies
- `python-telegram-bot` or direct `httpx` calls to https://api.telegram.org/bot{token}/
- No external API keys needed from user beyond the bot token they create themselves

## Testing Approach
- Mock Telegram API calls in tests (don't send real messages)
- Test: bot token validation, GC detection, message queuing, delivery
- Test: hook wiring (credit sale -> Telegram + SMS)
- Test: branch disable skips Telegram
- Test: report type filtering (GC only gets reports it's subscribed to)

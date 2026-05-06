"""
Shared closed-day guard helpers for all date-bearing modules
(Sales, Purchase Orders, Pay Supplier, Expenses, Receive Payment,
Fund Transfers, Branch Transfer receive).

Sales already implemented its own version inline in routes/sales.py;
this module is the canonical version every other endpoint should use.

Three responsibilities:

1. assert_open_day(branch_id, date)
   - Hard 403 if `date` is closed for `branch_id`. Use when the module
     is not allowed to late-encode at all (e.g. cash POs, fund transfers,
     pay-supplier when no PIN flow desired).

2. enforce_max_date(branch_id, date)
   - Caps `date` to today (or today+1 if today is already closed).
   - Used to block "forward-date stock laundering" where a user types
     a future date that never appears on any Z-Report.
   - Returns the (possibly clamped) date as a string OR raises 403 if
     a future date was supplied without an admin override.

3. resolve_late_encode(branch_id, intended_date, pin, reason, *, allow_types=None,
                       payment_type=None, max_days_back=7)
   - For modules that DO support late-encode (terms POs, expenses,
     receive-payment, etc.).
   - If `intended_date` is open: returns the date untouched, late_encoded=False.
   - If `intended_date` is closed: validates manager PIN + reason +
     7-day cap + cross-month block, then returns:
        {
          "effective_date":   <next open day for journal/wallet movement>,
          "intended_date":    <date the user picked>,
          "late_encoded":     True,
          "verifier":         {"verifier_id", "verifier_name", "method"},
          "carryover_label":  "[LATE ENCODE] {kind} — original date {intended} (closed)"
        }

The carryover_label is what the next-day Z-Report should print for the
rolled-over line so reviewers immediately see it was a late entry.

All of the above write a row to `audit_log` for the Audit Center.
"""
from datetime import datetime, date as _date, timedelta
from typing import Optional
from fastapi import HTTPException

from config import db
from utils import new_id, now_iso, today_local


# ── Internal helpers ──────────────────────────────────────────────────────


async def _is_closed(branch_id: str, date: str) -> bool:
    if not branch_id or not date:
        return False
    doc = await db.daily_closings.find_one(
        {"branch_id": branch_id, "date": date, "status": "closed"},
        {"_id": 0, "date": 1},
    )
    return bool(doc)


async def _next_open_day(branch_id: str, after_date: str, organization_id: str = "") -> str:
    """
    Find the next open day for `branch_id` strictly after `after_date`.

    "Open" = no daily_closings doc with status="closed" for that
    (branch_id, date). Capped at 30 lookups so we never spin.
    """
    try:
        cur = datetime.strptime(after_date, "%Y-%m-%d").date()
    except ValueError:
        cur = _date.today()
    today = _date.today()
    if cur < today:
        cur = today  # never roll BEFORE today
    for _ in range(30):
        cur = cur + timedelta(days=1)
        cur_str = cur.strftime("%Y-%m-%d")
        if not await _is_closed(branch_id, cur_str):
            return cur_str
    # Pathological — 30 consecutive closed days. Fall back to today_local
    # so the document still gets a date stamped.
    return await today_local(organization_id or "")


async def _audit(branch_id: str, action: str, user: dict, payload: dict):
    try:
        await db.audit_log.insert_one({
            "id": new_id(),
            "action": action,
            "branch_id": branch_id,
            "user_id": user.get("id"),
            "user_name": user.get("full_name", user.get("username", "")),
            "created_at": now_iso(),
            **payload,
        })
    except Exception:
        # Never fail a real transaction because audit insert hiccupped.
        pass


# ── 1. Strict closed-day block ────────────────────────────────────────────


async def assert_open_day(branch_id: str, date: str, *, label: str = "transaction") -> None:
    """Raise 403 if `date` is a closed business day for `branch_id`."""
    if await _is_closed(branch_id, date):
        raise HTTPException(
            status_code=403,
            detail=f"Day {date} is already closed — {label} cannot be recorded on a closed day.",
        )


# ── 2. Forward-date cap ───────────────────────────────────────────────────


async def enforce_max_date(
    branch_id: str,
    requested_date: str,
    *,
    user: dict,
    organization_id: str = "",
    override_pin: str = "",
    label: str = "transaction",
) -> str:
    """
    Cap `requested_date` to today (or today+1 if today is already closed).

    Same policy Sales uses — prevents typo-driven future-dating that would
    leave a record orphaned forever (no Z-Report covers a 2099 date).

    Admin override path: if the caller passes a non-empty `override_pin`,
    we verify it through `forward_date_override` policy and write to
    audit_log so suspicious patterns can be flagged later.
    """
    org_today = await today_local(organization_id or "")
    today_closed = await _is_closed(branch_id, org_today)
    max_allowed = org_today
    if today_closed:
        try:
            max_allowed = (datetime.strptime(org_today, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        except ValueError:
            max_allowed = org_today

    if requested_date <= max_allowed:
        return requested_date

    # Forward-dated → require admin override PIN
    if not override_pin:
        raise HTTPException(
            status_code=403,
            detail=(
                f"Cannot forward-date {label} to {requested_date}. "
                f"Maximum allowed is {max_allowed}. Forward-dating is blocked to prevent "
                f"{label} records from skipping every Z-Report."
            ),
        )

    from routes.verify import verify_pin_for_action
    verifier = await verify_pin_for_action(override_pin, "forward_date_override", branch_id=branch_id)
    if not verifier:
        raise HTTPException(status_code=403, detail="Invalid manager PIN for forward-date override.")

    await _audit(branch_id, "forward_date_override", user, {
        "label": label,
        "requested_date": requested_date,
        "max_allowed": max_allowed,
        "verifier_id": verifier.get("verifier_id"),
        "verifier_name": verifier.get("verifier_name"),
    })
    return requested_date


# ── 3. Late-encode resolver ───────────────────────────────────────────────


async def resolve_late_encode(
    branch_id: str,
    intended_date: str,
    *,
    user: dict,
    pin: str = "",
    reason: str = "",
    label: str = "entry",
    allow_payment_types: Optional[list] = None,
    payment_type: str = "",
    max_days_back: int = 7,
    organization_id: str = "",
) -> dict:
    """
    Returns a uniform contract every late-encode-aware module can act on:

      {
        "effective_date":  <date wallet movement / journal lands on>,
        "intended_date":   <user's pick — recorded for audit>,
        "late_encoded":    bool,
        "verifier":        {...} or None,
        "carryover_label": str or "",
      }

    Rules
    ─────
    * If `intended_date` is OPEN → no PIN needed; effective_date == intended_date.
    * If CLOSED → require manager PIN + reason; cap at `max_days_back`;
      block cross-month (preserves VAT period); reject future dates;
      respect `allow_payment_types` when supplied (e.g. terms-only).

    Effective date for late-encoded entries is the next open day after
    today — the wallet/journal/Z-Report effects land on a live day so
    reconciliation actually works. The intended_date is preserved on the
    document so the Audit Center / next Z-Report can show "[LATE ENCODE]
    original date 2026-02-04 (closed)".
    """
    if not branch_id or not intended_date:
        # Not enough info — let upstream decide; treat as open.
        return {
            "effective_date": intended_date,
            "intended_date": intended_date,
            "late_encoded": False,
            "verifier": None,
            "carryover_label": "",
        }

    is_closed = await _is_closed(branch_id, intended_date)
    if not is_closed:
        return {
            "effective_date": intended_date,
            "intended_date": intended_date,
            "late_encoded": False,
            "verifier": None,
            "carryover_label": "",
        }

    # Closed-day path → late-encode flow
    if not pin:
        raise HTTPException(
            status_code=403,
            detail=(
                f"Day {intended_date} is already closed. To record a late {label} on this date, "
                f"a manager PIN + reason are required."
            ),
        )
    if not (reason or "").strip():
        raise HTTPException(
            status_code=400,
            detail="Late-encode reason is required (so the Audit Center has context).",
        )

    # Payment-type allowlist (e.g. PO terms-only late-encode)
    if allow_payment_types and payment_type not in allow_payment_types:
        allowed = " / ".join(allow_payment_types)
        raise HTTPException(
            status_code=403,
            detail=(
                f"Late-encode for {label} on a closed day is only permitted for: {allowed}. "
                f"Cash/digital backdating would require moving funds on a closed day."
            ),
        )

    # Date sanity
    try:
        intended_d = datetime.strptime(intended_date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid intended_date format")

    today = _date.today()
    if intended_d > today:
        raise HTTPException(status_code=400, detail="Cannot late-encode a future date.")

    days_back = (today - intended_d).days
    if days_back > max_days_back:
        raise HTTPException(
            status_code=403,
            detail=(
                f"Late-encode limited to the last {max_days_back} days "
                f"({days_back} days back requested). Older entries require a journal adjustment."
            ),
        )

    if intended_d.month != today.month or intended_d.year != today.year:
        raise HTTPException(
            status_code=403,
            detail=(
                "Cross-month late-encode is blocked (protects VAT/financial filings). "
                "Use a journal adjustment in the prior month with your accountant."
            ),
        )

    # Verify manager PIN
    from routes.verify import verify_pin_for_action
    verifier = await verify_pin_for_action(pin, "late_encode", branch_id=branch_id)
    if not verifier:
        # Fall back to sales-late-encode policy if late_encode not configured
        verifier = await verify_pin_for_action(pin, "sales_late_encode", branch_id=branch_id)
    if not verifier:
        raise HTTPException(status_code=403, detail="Invalid manager PIN — late-encode not authorized.")

    # Compute effective date — next open day strictly after today
    org_today = await today_local(organization_id or "")
    if not await _is_closed(branch_id, org_today):
        effective_date = org_today
    else:
        effective_date = await _next_open_day(branch_id, org_today, organization_id)

    carryover_label = f"[LATE ENCODE] {label} — original date {intended_date} (closed)"

    await _audit(branch_id, "late_encode", user, {
        "label": label,
        "intended_date": intended_date,
        "effective_date": effective_date,
        "reason": reason.strip(),
        "payment_type": payment_type,
        "days_back": days_back,
        "verifier_id": verifier.get("verifier_id"),
        "verifier_name": verifier.get("verifier_name"),
        "auth_method": verifier.get("method", ""),
    })

    return {
        "effective_date": effective_date,
        "intended_date": intended_date,
        "late_encoded": True,
        "verifier": verifier,
        "carryover_label": carryover_label,
    }


# ── Convenience: combined date resolver ──────────────────────────────────


async def resolve_business_date(
    branch_id: str,
    requested_date: Optional[str],
    *,
    user: dict,
    organization_id: str = "",
    forward_override_pin: str = "",
    late_encode_pin: str = "",
    late_encode_reason: str = "",
    label: str = "entry",
    allow_late_encode: bool = True,
    allow_payment_types: Optional[list] = None,
    payment_type: str = "",
    max_days_back: int = 7,
) -> dict:
    """
    One-call helper: forward-cap → closed-day → late-encode.

    Most callers should use this. Returns same dict shape as
    `resolve_late_encode` plus already-clamped `requested_date`.
    """
    requested_date = (requested_date or "").strip() or await today_local(organization_id or "")
    requested_date = await enforce_max_date(
        branch_id, requested_date,
        user=user, organization_id=organization_id,
        override_pin=forward_override_pin, label=label,
    )

    if not allow_late_encode:
        await assert_open_day(branch_id, requested_date, label=label)
        return {
            "effective_date": requested_date,
            "intended_date": requested_date,
            "late_encoded": False,
            "verifier": None,
            "carryover_label": "",
        }

    return await resolve_late_encode(
        branch_id, requested_date,
        user=user, pin=late_encode_pin, reason=late_encode_reason,
        label=label,
        allow_payment_types=allow_payment_types,
        payment_type=payment_type,
        max_days_back=max_days_back,
        organization_id=organization_id,
    )

"""
Iter 240 — Admin-gated backfill for the offline-sync line-discount bug.

Purpose: One-shot HTTP endpoint that the platform admin can hit directly
from the deployed UI session to repair invoices that were saved with
inflated totals before the routes/sync.py:418 fix shipped.

  • Bug: offline sync recomputed `line_total = qty * rate` (no discount)
    and overwrote the frontend-correct `item.total`. Effects: per-item
    `total`, invoice `subtotal`, `grand_total`, `balance` all inflated
    by the discount amount on `synced_from_offline=True` invoices.
  • Online (routes/sales.py) sales were never affected.

Endpoint behavior:
  GET  /api/admin/backfill/iter240          → DRY-RUN report (no writes)
  POST /api/admin/backfill/iter240?apply=1  → Apply corrections + report

Security:
  • role=admin required (check_perm 'organizations.update' as a stand-in
    is too broad; we hard-gate on `user.role == "admin"`).
  • Org-scoped via the `db` proxy — admin only sees their own org's
    invoices, never cross-tenant.
  • Idempotent: each fixed invoice is stamped `iter240_backfill_at` so
    re-running never doubles up.

Customer over-payment / wallet implications:
  We deliberately do NOT touch payments, cashier_wallets, or the
  expenses ledger. If the customer over-paid the inflated total in
  cash, the script returns `overpaid_by` per invoice so the admin can
  decide whether to refund manually or leave as a credit.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from datetime import datetime, timezone

from config import db
from utils import get_current_user

router = APIRouter(prefix="/admin/backfill", tags=["Admin Backfill"])


def _r2(x) -> float:
    return round(float(x or 0), 2)


def _expected_line_total(item: dict) -> float:
    qty = float(item.get("quantity", 0))
    rate = float(item.get("rate", item.get("unit_price", item.get("price", 0))))
    disc = float(item.get("discount_amount", 0))
    return _r2(qty * rate - disc)


@router.get("/iter240")
async def iter240_backfill_report(user=Depends(get_current_user)):
    """DRY-RUN report — no DB writes."""
    return await _run_iter240(apply_changes=False, user=user)


@router.post("/iter240")
async def iter240_backfill_apply(
    apply: bool = Query(False, description="Set true to actually persist corrections."),
    user=Depends(get_current_user),
):
    """Apply iter240 corrections to all `synced_from_offline=True` invoices
    in the current org that still have inflated line totals."""
    return await _run_iter240(apply_changes=apply, user=user)


# ════════════════════════════════════════════════════════════════════
# Iter 241 — Day-close ledger trail backfill
# ════════════════════════════════════════════════════════════════════
# Recreates the missing wallet_movements + fund_transfers entries for
# every historical `daily_closings` row that lacks them. Idempotent via
# `daily_close_ref = closing.id` lookup before each insert.
#
# Order of inserts mirrors the live close_day path so the audit trail
# reads identically whether it was created live or replayed.

@router.get("/iter241-close-ledger")
async def iter241_backfill_report(user=Depends(get_current_user)):
    return await _run_iter241(apply_changes=False, user=user)


@router.post("/iter241-close-ledger")
async def iter241_backfill_apply(
    apply: bool = Query(False),
    user=Depends(get_current_user),
):
    return await _run_iter241(apply_changes=apply, user=user)


async def _run_iter241(apply_changes: bool, user: dict) -> dict:
    if (user or {}).get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin role required.")

    cursor = db.daily_closings.find(
        {"status": "closed", "is_batch_member": {"$ne": True}},
        {"_id": 0},
    ).sort("date", 1)

    user_name = user.get("full_name") or user.get("name") or user.get("username") or ""
    fixed_count = 0
    rows_inserted = 0
    affected = []

    async for closing in cursor:
        closing_id = closing.get("id")
        branch_id = closing.get("branch_id")
        date = closing.get("date")
        if not (closing_id and branch_id and date):
            continue

        # Skip if already has ledger entries from a prior backfill / live close
        existing_mov = await db.wallet_movements.count_documents({"daily_close_ref": closing_id})
        existing_xfer = await db.fund_transfers.count_documents({"daily_close_ref": closing_id})
        if existing_mov > 0 or existing_xfer > 0:
            continue

        cashier_wallet = await db.fund_wallets.find_one(
            {"branch_id": branch_id, "type": "cashier", "active": True}, {"_id": 0}
        )
        safe_wallet = await db.fund_wallets.find_one(
            {"branch_id": branch_id, "type": "safe", "active": True}, {"_id": 0}
        )

        cash_to_safe = float(closing.get("cash_to_safe", 0) or 0)
        cash_to_drawer = float(closing.get("cash_to_drawer", 0) or 0)
        actual_cash = float(closing.get("actual_cash", 0) or 0)
        over_short = float(closing.get("over_short", 0) or 0)
        variance_notes = closing.get("variance_notes", "") or ""

        will_insert = 0
        if cashier_wallet:
            if abs(over_short) > 0.005:
                will_insert += 1  # over_short adjust
            if cash_to_safe > 0 and safe_wallet:
                will_insert += 3  # fund_transfer + cashier movement + safe movement
        if will_insert == 0:
            continue

        affected.append({
            "closing_id": closing_id,
            "date": date,
            "branch_id": branch_id,
            "cash_to_safe": cash_to_safe,
            "cash_to_drawer": cash_to_drawer,
            "over_short": _r2(over_short),
            "ledger_entries_to_create": will_insert,
        })

        if apply_changes:
            # Reuse the live helper so the entries look identical to live
            from routes.daily_operations import _write_close_ledger_entries
            inserted = await _write_close_ledger_entries(
                closing_id=closing_id, branch_id=branch_id, date=date,
                cashier_wallet=cashier_wallet, safe_wallet=safe_wallet,
                actual_cash=actual_cash, cash_to_safe=cash_to_safe,
                cash_to_drawer=cash_to_drawer,
                over_short=over_short, variance_notes=variance_notes,
                user={"id": user.get("id", "system"),
                      "full_name": f"{user_name} (iter241 backfill)",
                      "username": user_name},
            )
            rows_inserted += inserted["wallet_movements"] + inserted["fund_transfers"]
        else:
            rows_inserted += will_insert
        fixed_count += 1

    return {
        "mode": "applied" if apply_changes else "dry_run",
        "affected_closings": fixed_count,
        "ledger_rows_created": rows_inserted,
        "details": affected,
    }


async def _run_iter240(apply_changes: bool, user: dict) -> dict:
    # Hard role gate — `db` is already org-scoped via TenantCollection so
    # an admin can never bleed into another tenant, but we still want to
    # block cashiers/managers from poking at this knob.
    if (user or {}).get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin role required.")

    cursor = db.invoices.find(
        {"synced_from_offline": True, "voided": {"$ne": True}},
        {"_id": 0},
    )

    affected = []
    fixed_count = 0
    total_delta = 0.0

    async for inv in cursor:
        items = inv.get("items") or []
        new_items = []
        item_changed = False
        for it in items:
            disc = float(it.get("discount_amount", 0))
            if disc > 0:
                expected = _expected_line_total(it)
                stored = _r2(it.get("total", 0))
                if abs(stored - expected) > 0.005:
                    new_items.append({**it, "total": expected})
                    item_changed = True
                    continue
            new_items.append(it)

        if not item_changed:
            continue

        new_subtotal = _r2(sum(float(it.get("total", 0)) for it in new_items))
        freight = float(inv.get("freight", 0) or 0)
        overall_disc = float(inv.get("overall_discount", 0) or 0)
        new_grand_total = _r2(new_subtotal + freight - overall_disc)
        amount_paid = float(inv.get("amount_paid", 0) or 0)
        new_balance = _r2(max(0, new_grand_total - amount_paid))

        old_grand = _r2(inv.get("grand_total", 0))
        delta = _r2(new_grand_total - old_grand)
        total_delta += delta

        affected.append({
            "invoice_number": inv.get("invoice_number"),
            "customer_name": inv.get("customer_name"),
            "branch_id": inv.get("branch_id"),
            "old_grand_total": old_grand,
            "new_grand_total": new_grand_total,
            "delta": delta,
            "amount_paid": amount_paid,
            "overpaid_by": _r2(max(0, amount_paid - new_grand_total)),
            "items_fixed": [
                {"product_name": it.get("product_name"), "old_total": _r2(orig.get("total", 0)),
                 "new_total": _r2(it.get("total", 0))}
                for orig, it in zip(items, new_items)
                if _r2(orig.get("total", 0)) != _r2(it.get("total", 0))
            ],
        })

        if apply_changes:
            await db.invoices.update_one(
                {"id": inv["id"]},
                {"$set": {
                    "items": new_items,
                    "subtotal": new_subtotal,
                    "grand_total": new_grand_total,
                    "balance": new_balance,
                    "iter240_backfill_at": datetime.now(timezone.utc).isoformat(),
                    "iter240_backfill_old_grand": old_grand,
                }},
            )
            # Also fix sales_log entries for the same invoice — these
            # feed the Close Wizard step 1 and are stored in their own
            # collection. We match on invoice_number + product_id and
            # set line_total to the corrected per-line value.
            for it in new_items:
                await db.sales_log.update_many(
                    {
                        "invoice_number": inv.get("invoice_number"),
                        "product_id": it.get("product_id"),
                    },
                    {"$set": {"line_total": _r2(it.get("total", 0))}},
                )
        fixed_count += 1

    return {
        "mode": "applied" if apply_changes else "dry_run",
        "affected_invoices": fixed_count,
        "net_grand_total_reduction": _r2(-total_delta),
        "details": affected,
    }

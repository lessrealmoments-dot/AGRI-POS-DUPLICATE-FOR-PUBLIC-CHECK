"""
Iter 240 — Backfill script for invoices corrupted by the offline-sync
line-discount bug.

Bug summary (see `routes/sync.py` Iter 240 fix):
  • For invoices created via /api/sales/sync (offline sync replay) before
    the fix, any line item with a per-line discount was persisted with
    `total = qty * rate` (NOT subtracting the discount). The
    corresponding invoice `subtotal` and `grand_total` were inflated by
    the same amount.
  • Online sales (routes/sales.py) were NOT affected.

What this script does:
  1. DRY-RUN by default — finds and prints affected invoices, NEVER writes.
  2. With `--apply`, recomputes and persists the corrected
       item.total = round(qty * rate - discount_amount, 2)
       subtotal     = sum(corrected item totals)
       grand_total  = subtotal + freight - overall_discount
       balance      = max(0, new_grand_total - amount_paid)

CRITICAL behavior:
  • Only touches invoices flagged `synced_from_offline=True`.
  • Only adjusts items where `discount_amount > 0` AND `total` is wrong.
  • Leaves payment ledger / wallet entries untouched. If the customer
    over-paid (because the cashier collected the inflated grand_total in
    cash), that overage stays on the invoice as a credit — nothing
    auto-refunds. The script prints a per-invoice "needs-refund" delta
    so you can refund manually.
  • Skips voided invoices.

Usage:
    cd /app/backend
    python3 scripts/backfill_offline_sync_line_discount_240.py            # DRY-RUN
    python3 scripts/backfill_offline_sync_line_discount_240.py --apply    # Persist
"""
import os
import sys
import asyncio
from datetime import datetime, timezone
from motor.motor_asyncio import AsyncIOMotorClient


MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME   = os.environ.get("DB_NAME", "test_database")


def _r2(x):
    return round(float(x or 0), 2)


def _expected_line_total(item):
    qty = float(item.get("quantity", 0))
    rate = float(item.get("rate", item.get("unit_price", item.get("price", 0))))
    disc = float(item.get("discount_amount", 0))
    return _r2(qty * rate - disc)


async def main():
    apply_changes = "--apply" in sys.argv
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]

    cursor = db.invoices.find({
        "synced_from_offline": True,
        "voided": {"$ne": True},
    }, {"_id": 0})

    fixed_count = 0
    affected = []
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
                    new_it = {**it, "total": expected}
                    new_items.append(new_it)
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
            "branch_id": inv.get("branch_id"),
            "old_grand": old_grand,
            "new_grand": new_grand_total,
            "delta": delta,
            "amount_paid": amount_paid,
            "overpaid_by": _r2(max(0, amount_paid - new_grand_total)),
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
        fixed_count += 1

    mode = "APPLIED" if apply_changes else "DRY-RUN (no writes)"
    print(f"\n=== Iter 240 backfill — {mode} ===")
    print(f"Affected invoices: {fixed_count}")
    print(f"Net grand-total reduction: ₱{-total_delta:,.2f} (negative delta = invoice was overcharged)")
    if affected:
        print(f"\n{'Invoice':<28} {'Old':>10} {'New':>10} {'Δ':>10} {'Overpaid':>10}")
        print("-" * 75)
        for a in affected[:50]:
            print(f"{a['invoice_number']:<28} {a['old_grand']:>10.2f} {a['new_grand']:>10.2f} {a['delta']:>10.2f} {a['overpaid_by']:>10.2f}")
        if len(affected) > 50:
            print(f"... +{len(affected)-50} more")
    if not apply_changes:
        print("\nRe-run with `--apply` to persist these corrections.")


if __name__ == "__main__":
    asyncio.run(main())

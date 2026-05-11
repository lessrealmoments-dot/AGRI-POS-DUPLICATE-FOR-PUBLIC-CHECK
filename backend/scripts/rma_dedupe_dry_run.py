"""
Phase 5 — Deployment Validation: C-8 RMA Duplicate Dry-Run Report

READ-ONLY. Does not mutate any data. Prints:
  • Tenant-scoped (organization_id) groups of duplicate rma_number values
  • For each group: which record would keep the original number (most recent
    by created_at, fallback by created_at-equivalent fields)
  • The proposed suffix each older duplicate would receive (_legacy_1, _legacy_2...)
  • Whether the unique partial index currently exists on `returns.rma_number`
  • Aggregate counts (groups, records affected, organizations affected)

If you decide to apply the cleanup, the same logic must be re-run inside a
write transaction. THIS SCRIPT WRITES NOTHING.
"""
import asyncio
import os
import sys

# Reuse the backend's existing Mongo connection setup
sys.path.insert(0, '/app/backend')
from config import db  # type: ignore


async def main():
    print("=" * 70)
    print("C-8 RMA Duplicate Cleanup — DRY RUN (READ-ONLY)")
    print("=" * 70)

    # ── 1. Index check ────────────────────────────────────────────────
    print("\n[1] Current `returns` collection indexes:")
    idx_cursor = db.returns.list_indexes()
    rma_unique_index = None
    async for idx in idx_cursor:
        keys = idx.get("key", {})
        is_rma = "rma_number" in keys
        marker = "  <-- rma_number index" if is_rma else ""
        print(f"    {idx.get('name'):<35} keys={dict(keys)}  unique={idx.get('unique', False)}  partial={idx.get('partialFilterExpression') is not None}{marker}")
        if is_rma and idx.get("unique"):
            rma_unique_index = idx
    if rma_unique_index:
        print(f"\n  ✓ Unique rma_number index PRESENT: {rma_unique_index['name']}")
    else:
        print("\n  ✗ Unique rma_number index NOT present (likely silently skipped due to existing dupes).")

    # ── 2. Aggregate duplicate groups ─────────────────────────────────
    print("\n[2] Scanning for duplicate rma_number groups (scoped by organization_id)...")
    pipeline = [
        {"$match": {"rma_number": {"$exists": True, "$ne": None, "$type": "string"}}},
        {"$group": {
            "_id": {"org": "$organization_id", "rma": "$rma_number"},
            "count": {"$sum": 1},
            "records": {"$push": {
                "id": "$id",
                "rma_number": "$rma_number",
                "branch_id": "$branch_id",
                "created_at": "$created_at",
                "return_date": "$return_date",
                "customer_name": "$customer_name",
            }},
        }},
        {"$match": {"count": {"$gt": 1}}},
        {"$sort": {"count": -1}},
    ]

    dup_groups = []
    async for g in db.returns.aggregate(pipeline):
        dup_groups.append(g)

    if not dup_groups:
        print("\n  ✓ ZERO duplicate rma_number groups found across all tenants.")
        print("  ✓ Safe to (re)create the unique partial index — no data mutation needed.")
        print("\n" + "=" * 70)
        print("VERDICT: No cleanup required. The unique index will enforce on next backend start.")
        print("=" * 70)
        return

    total_records = sum(g["count"] for g in dup_groups)
    affected_orgs = {g["_id"]["org"] for g in dup_groups}

    print(f"\n  Duplicate groups found: {len(dup_groups)}")
    print(f"  Records affected:       {total_records}")
    print(f"  Organizations touched:  {len(affected_orgs)}")

    # ── 3. Per-group proposed actions ─────────────────────────────────
    print("\n[3] Per-group proposed cleanup actions (DRY-RUN ONLY — nothing is written):")
    print("    Convention: keep the most-recent record (by created_at) at the original")
    print("                rma_number; suffix all older duplicates with `_legacy_N`.\n")

    for i, g in enumerate(dup_groups, 1):
        rma = g["_id"]["rma"]
        org = g["_id"]["org"] or "(no org_id)"
        records = g["records"]
        # Sort by created_at descending; fall back to return_date if created_at missing
        records.sort(
            key=lambda r: (r.get("created_at") or r.get("return_date") or ""),
            reverse=True,
        )
        keeper = records[0]
        olders = records[1:]

        print(f"  Group #{i}: rma_number={rma!r}  org={org[:8]}...  count={len(records)}")
        print(f"    KEEP   id={keeper['id'][:8]}.. created={keeper.get('created_at')!r:.40} customer={keeper.get('customer_name')!r}")
        for j, r in enumerate(olders, 1):
            suffix = f"_legacy_{j}" if len(olders) > 1 else "_legacy"
            new_rma = f"{rma}{suffix}"
            print(f"    RENAME id={r['id'][:8]}.. created={r.get('created_at')!r:.40} customer={r.get('customer_name')!r}  →  {new_rma!r}")
        print()

    print("=" * 70)
    print("DRY-RUN COMPLETE.")
    print(f"Proposed renames: {sum(len(g['records']) - 1 for g in dup_groups)} records across {len(dup_groups)} groups.")
    print("No data was mutated. Awaiting owner approval before running the live cleanup.")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())

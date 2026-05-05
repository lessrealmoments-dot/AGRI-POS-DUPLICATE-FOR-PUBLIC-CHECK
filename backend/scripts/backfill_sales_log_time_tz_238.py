"""
One-shot migration — Iter 238

Backfills the `sales_log.time` display column from UTC HH:MM:SS to the org's
local wall-clock time (defaults to Asia/Manila).

Why: every sale logged before the timezone fix stored `time` via
`datetime.now(timezone.utc).strftime("%H:%M:%S")` — which means an 8 AM
Manila sale shows as "00:00:00" in the Daily Log. Money / stock movements
are unaffected (they reference `created_at` which stays UTC ISO and
displays correctly via the new frontend `formatDateTime` helper).

This script:
  1. Reads each sales_log row.
  2. If row already has `time_tz_migrated=True`, skips.
  3. Converts `timestamp` (UTC ISO) to org-local `HH:MM:SS` and stores in
     `time`. Preserves the original UTC value as `time_utc_legacy` for
     auditability.
  4. Marks the row `time_tz_migrated=True`.

SAFE TO RE-RUN. Idempotent. Does NOT touch:
  • `created_at` / `timestamp` / `voided_at` (UTC ISO transport, unchanged)
  • `date` calendar-day buckets (touching these would retroactively shift
    sales between Z-Reports, corrupting closed-day totals)
  • `running_total` / `quantity` / `total` (money/stock formulas)

Run:
    cd /app/backend && python -m scripts.backfill_sales_log_time_tz_238

Environment vars: same as the app (MONGO_URL, DB_NAME).
"""
import os
import sys
from datetime import datetime, timezone

import pymongo
from zoneinfo import ZoneInfo


def _resolve_tz_for_org(client, db_name, organization_id):
    """Return the IANA timezone configured on the org's company_info, or
    Asia/Manila if none. Mirrors the runtime resolver in close_reminder."""
    db = client[db_name]
    if not organization_id:
        return "Asia/Manila"
    doc = db.settings.find_one(
        {"key": "company_info", "organization_id": organization_id},
        {"_id": 0, "value": 1},
    )
    tz = ((doc or {}).get("value") or {}).get("timezone") or ""
    return tz.strip() or "Asia/Manila"


def main():
    mongo_url = os.environ["MONGO_URL"]
    db_name = os.environ["DB_NAME"]
    client = pymongo.MongoClient(mongo_url)
    db = client[db_name]

    total = db.sales_log.count_documents({})
    print(f"sales_log rows: {total}")

    pending = db.sales_log.count_documents({
        "$or": [
            {"time_tz_migrated": {"$exists": False}},
            {"time_tz_migrated": False},
        ],
    })
    print(f"rows pending migration: {pending}")
    if pending == 0:
        print("✓ Nothing to do.")
        return

    # Cache resolved tz per branch_id (every row in a branch shares an org)
    tz_cache = {}

    fixed = 0
    skipped = 0
    cursor = db.sales_log.find(
        {"$or": [
            {"time_tz_migrated": {"$exists": False}},
            {"time_tz_migrated": False},
        ]},
        {"_id": 0, "id": 1, "branch_id": 1, "timestamp": 1, "time": 1},
        no_cursor_timeout=True,
    )
    try:
        for row in cursor:
            row_id = row.get("id")
            br = row.get("branch_id") or ""
            ts = row.get("timestamp") or ""
            old_time = row.get("time") or ""
            if not ts or not row_id:
                skipped += 1
                continue

            if br not in tz_cache:
                branch = db.branches.find_one({"id": br}, {"_id": 0, "organization_id": 1})
                org_id = (branch or {}).get("organization_id") or ""
                tz_cache[br] = _resolve_tz_for_org(client, db_name, org_id)
            tz_name = tz_cache[br]

            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                local_str = dt.astimezone(ZoneInfo(tz_name)).strftime("%H:%M:%S")
            except Exception:
                skipped += 1
                continue

            db.sales_log.update_one(
                {"id": row_id},
                {"$set": {
                    "time": local_str,
                    "time_utc_legacy": old_time,
                    "time_tz_migrated": True,
                    "time_tz_migrated_at": datetime.now(timezone.utc).isoformat(),
                    "time_tz_used": tz_name,
                }},
            )
            fixed += 1
            if fixed % 1000 == 0:
                print(f"  ... {fixed} rows fixed")
    finally:
        cursor.close()

    print(f"✓ Migration complete: {fixed} fixed, {skipped} skipped (no timestamp).")


if __name__ == "__main__":
    main()

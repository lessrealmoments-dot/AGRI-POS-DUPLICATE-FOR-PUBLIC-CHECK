"""
Backfill `org_id` on legacy doc_codes entries that were created before the
field was stamped consistently. Reads each referenced document via _raw_db,
copies its `organization_id` onto the doc_code entry.

Idempotent: only updates rows where org_id is missing/empty.

Usage:
    cd /app/backend && python -m scripts.backfill_doc_codes_org_id
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import _raw_db


_DOC_TYPE_TO_COLLECTION = {
    "invoice": "invoices",
    "purchase_order": "purchase_orders",
    "branch_transfer": "branch_transfer_orders",
}


async def run():
    db = _raw_db
    cursor = db.doc_codes.find(
        {"$or": [{"org_id": {"$exists": False}}, {"org_id": None}, {"org_id": ""}]},
        {"_id": 0},
    )
    fixed = 0
    skipped = 0
    async for dc in cursor:
        coll = _DOC_TYPE_TO_COLLECTION.get(dc.get("doc_type", ""))
        if not coll:
            skipped += 1
            continue
        doc = await db[coll].find_one(
            {"id": dc.get("doc_id", "")},
            {"_id": 0, "organization_id": 1},
        )
        org_id = (doc or {}).get("organization_id")
        if not org_id:
            skipped += 1
            continue
        await db.doc_codes.update_one(
            {"code": dc["code"]}, {"$set": {"org_id": org_id}}
        )
        fixed += 1

    print(f"Backfilled org_id on {fixed} doc_codes; skipped {skipped} (no source doc / unknown type).")


if __name__ == "__main__":
    asyncio.run(run())

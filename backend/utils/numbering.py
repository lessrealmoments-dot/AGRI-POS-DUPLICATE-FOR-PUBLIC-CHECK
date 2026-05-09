"""
Atomic receipt/transaction numbering system.

Format: {PREFIX}-{BRANCH_CODE}-{SEQUENCE}
Example: SI-MN-001042

Features:
- Atomic MongoDB $inc — race-safe, no duplicates
- Branch-specific sequences — each branch has its own counter per prefix
- Never resets — numbers are unique forever (27+ year capacity at 100/day)
- Starts at 1000 for professional appearance
"""
from pymongo import ReturnDocument
from datetime import datetime, timezone
from config import db, _raw_db


STARTING_SEQUENCE = 999  # $inc adds 1 first, so first issued = 1000


async def get_branch_code(branch_id: str) -> str:
    """
    Get or auto-generate a 2-char branch code for the given branch.
    If no code exists yet, generates one from the branch name and saves it.
    """
    branch = await db.branches.find_one({"id": branch_id}, {"_id": 0})
    if not branch:
        return "XX"

    code = branch.get("branch_code")
    if code:
        return code

    # Auto-generate from name
    name = (branch.get("name") or "Branch").strip()
    words = name.split()
    if len(words) >= 2:
        candidate = (words[0][0] + words[1][0]).upper()
    else:
        candidate = name[:2].upper()

    # Ensure uniqueness among existing branch codes in the same org
    existing_codes = set()
    org_id = branch.get("organization_id")
    code_filter = {"branch_code": {"$exists": True}}
    if org_id:
        code_filter["organization_id"] = org_id
    async for b in db.branches.find(code_filter, {"_id": 0, "branch_code": 1}):
        existing_codes.add(b["branch_code"])

    final_code = candidate
    suffix = 1
    while final_code in existing_codes:
        final_code = f"{candidate[0]}{suffix}"
        suffix += 1
        if suffix > 9:
            final_code = f"{candidate[0]}{chr(64 + suffix)}"

    await db.branches.update_one({"id": branch_id}, {"$set": {"branch_code": final_code}})
    return final_code


async def generate_next_number(prefix: str, branch_id: str) -> str:
    """
    Atomically generate the next transaction number.

    Uses raw MongoDB (not tenant-scoped) with branch_id in the key
    for global uniqueness across organizations.

    Counter key = "{branch_id}:{prefix}" (e.g., "abc-uuid:SI")
    Returns formatted string like "SI-MN-001042"
    """
    branch_code = await get_branch_code(branch_id)
    counter_key = f"{branch_id}:{prefix}"

    # Use _raw_db to bypass tenant scoping — counter keys are globally unique
    result = await _raw_db.counters.find_one_and_update(
        {"_id": counter_key},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )

    seq = result["seq"]
    if seq < STARTING_SEQUENCE + 1:
        # First usage — jump to starting sequence
        await _raw_db.counters.update_one(
            {"_id": counter_key},
            {"$set": {"seq": STARTING_SEQUENCE + 1}}
        )
        seq = STARTING_SEQUENCE + 1

    return f"{prefix}-{branch_code}-{str(seq).zfill(6)}"


async def generate_next_rma_number(branch_id: str, organization_id: str) -> str:
    """C-8 (Audit 2026-02): atomic, org+branch-scoped RMA generator.

    Replaces the racy `count_documents+1` approach in routes/returns.py
    that produced duplicate RMA numbers under concurrent return creation
    AND leaked numbering across tenants. Format remains
    `RTN-YYYYMMDD-NNNN` for backwards compatibility with existing reports.
    """
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    counter_key = f"rma:{organization_id or 'global'}:{branch_id or 'all'}:{today}"
    result = await _raw_db.counters.find_one_and_update(
        {"_id": counter_key},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    seq = result["seq"]
    return f"RTN-{today}-{str(seq).zfill(4)}"


async def check_idempotency(collection_name: str, idempotency_key: str) -> dict | None:
    """
    Check if a transaction with this idempotency key already exists.
    Returns the existing document if found, None otherwise.
    """
    if not idempotency_key:
        return None
    collection = db[collection_name]
    existing = await collection.find_one(
        {"idempotency_key": idempotency_key},
        {"_id": 0}
    )
    return existing

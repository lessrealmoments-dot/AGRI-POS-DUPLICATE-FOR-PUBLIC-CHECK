"""
Overage Reserve Ledger — single source of truth for cash over/short pooling.

Accounting model
----------------
Every daily close that produced a non-zero `over_short` posts one auto entry
to the ledger below. Positive over_short goes to pool="reserve", negative
goes to pool="deficit" (we keep these two pools separated, per owner decision
2c — track separately, net only on explicit approval).

The reserve pool represents an accumulation of "found cash" — typically the
tail of unrecorded sales. When an audit finds an inventory shortage (physical
count < system count), owners may apply some or all of the reserve balance
to offset the shortage capital loss. This matches the classic
  Cash Over/Short → Inventory Shrinkage
journal entry that a human accountant would post.

All entries use SIGNED amounts in the same row:
  amount > 0  ⇢ pool grew by that much (auto_credit, manual_adjust+)
  amount < 0  ⇢ pool shrank by that much (apply_audit, net_shortage, claw_back)
balance_after is the running pool total after this entry.
"""
from fastapi import APIRouter, Depends, HTTPException
from typing import Optional
from config import db
from utils import get_current_user, check_perm, now_iso, new_id

router = APIRouter(prefix="/reserve", tags=["Overage Reserve"])

# ──────────────────────────────────────────────────────────────────────────
#  HELPERS
# ──────────────────────────────────────────────────────────────────────────

LEDGER = "overage_reserve_ledger"

async def _current_balance(branch_id: str, pool: str) -> float:
    """Return the latest running balance for a (branch, pool)."""
    rec = await db[LEDGER].find_one(
        {"branch_id": branch_id, "pool": pool},
        {"_id": 0, "balance_after": 1},
        sort=[("created_at", -1)],
    )
    return round(float(rec["balance_after"]), 2) if rec else 0.0


async def _post_entry(*, branch_id: str, branch_name: str, pool: str, entry_type: str,
                      amount: float, source_type: str, source_id: str, source_ref: str,
                      note: str = "", applied_to: str = "", paired_entry_id: str = "",
                      verifier: Optional[dict] = None, user: Optional[dict] = None,
                      entry_date: Optional[str] = None) -> dict:
    """
    Append a ledger entry. Caller owns the sign of `amount` — positive grows
    the pool, negative shrinks it. We compute the new running balance here
    so the ledger is always consistent without requiring a DB transaction.
    """
    current = await _current_balance(branch_id, pool)
    new_balance = round(current + amount, 2)
    entry = {
        "id": new_id(),
        "branch_id": branch_id,
        "branch_name": branch_name,
        "pool": pool,
        "type": entry_type,
        "amount": round(amount, 2),
        "balance_after": new_balance,
        "source_type": source_type,
        "source_id": source_id,
        "source_ref": source_ref,
        "note": note,
        "applied_to": applied_to,
        "paired_entry_id": paired_entry_id,
        "date": entry_date or now_iso()[:10],
        "created_at": now_iso(),
        "created_by": user["id"] if user else "system",
        "created_by_name": (user or {}).get("full_name") or (user or {}).get("username") or "System",
        "verifier_id": (verifier or {}).get("verifier_id", ""),
        "verifier_name": (verifier or {}).get("verifier_name", ""),
        "verifier_method": (verifier or {}).get("method", ""),
        "reversed": False,
    }
    await db[LEDGER].insert_one(entry)
    entry.pop("_id", None)
    return entry


async def record_daily_close_variance(close_record: dict, user: Optional[dict] = None) -> Optional[dict]:
    """
    Public hook — call from daily_operations.submit_*_close right after the
    close_record is inserted. Idempotent on (source_id, type=auto_credit).
    Returns the new ledger entry or None if nothing to record.
    """
    over_short = round(float(close_record.get("over_short", 0) or 0), 2)
    if over_short == 0:
        return None

    # Idempotency guard — don't duplicate if a backfill already wrote this one.
    existing = await db[LEDGER].find_one(
        {"source_type": "daily_closing", "source_id": close_record["id"],
         "type": "auto_credit"},
        {"_id": 0, "id": 1},
    )
    if existing:
        return None

    branch_id = close_record.get("branch_id", "")
    branch_name = ""
    if branch_id:
        b = await db.branches.find_one({"id": branch_id}, {"_id": 0, "name": 1})
        branch_name = b.get("name", "") if b else ""

    pool = "reserve" if over_short > 0 else "deficit"
    # For the deficit pool, we still track absolute magnitude as a positive
    # "balance_after" — easier to read. Sign trick: both pools grow with
    # positive amounts; only the netting/apply operations use negatives.
    amount = abs(over_short)
    date = close_record.get("date") or now_iso()[:10]
    note = (
        f"Auto-credit from daily close {date} — cash "
        f"{'over' if over_short > 0 else 'short'} by ₱{amount:,.2f}"
    )
    return await _post_entry(
        branch_id=branch_id, branch_name=branch_name,
        pool=pool, entry_type="auto_credit",
        amount=amount, source_type="daily_closing",
        source_id=close_record["id"],
        source_ref=f"Daily close {date}",
        note=note, entry_date=date, user=user,
    )


# ──────────────────────────────────────────────────────────────────────────
#  PUBLIC API
# ──────────────────────────────────────────────────────────────────────────

@router.get("/summary")
async def reserve_summary(
    branch_id: Optional[str] = None,
    user=Depends(get_current_user),
):
    """
    Per-branch and org-wide summary of reserve + deficit pools.
    Non-admin users only see their own branch.
    """
    check_perm(user, "reports", "view")

    # Scope branches — multi-tenant safe
    branch_query = {"active": True}
    org_id = user.get("organization_id")
    if org_id:
        branch_query["organization_id"] = org_id
    branches = await db.branches.find(branch_query, {"_id": 0, "id": 1, "name": 1}).to_list(500)
    if user.get("role") != "admin":
        user_branch = user.get("branch_id")
        if user_branch:
            branches = [b for b in branches if b["id"] == user_branch]
        else:
            branches = []
    if branch_id:
        branches = [b for b in branches if b["id"] == branch_id]

    rows = []
    reserve_total = 0.0
    deficit_total = 0.0
    for b in branches:
        reserve = await _current_balance(b["id"], "reserve")
        deficit = await _current_balance(b["id"], "deficit")
        last_reserve = await db[LEDGER].find_one(
            {"branch_id": b["id"], "pool": "reserve"},
            {"_id": 0, "created_at": 1, "type": 1, "amount": 1},
            sort=[("created_at", -1)],
        )
        last_deficit = await db[LEDGER].find_one(
            {"branch_id": b["id"], "pool": "deficit"},
            {"_id": 0, "created_at": 1, "type": 1, "amount": 1},
            sort=[("created_at", -1)],
        )
        rows.append({
            "branch_id": b["id"],
            "branch_name": b["name"],
            "reserve_balance": reserve,
            "deficit_balance": deficit,
            "net_pool": round(reserve - deficit, 2),
            "last_reserve_at": last_reserve.get("created_at") if last_reserve else None,
            "last_deficit_at": last_deficit.get("created_at") if last_deficit else None,
        })
        reserve_total += reserve
        deficit_total += deficit

    return {
        "branches": rows,
        "totals": {
            "reserve_total": round(reserve_total, 2),
            "deficit_total": round(deficit_total, 2),
            "net_pool": round(reserve_total - deficit_total, 2),
        },
    }


@router.get("/ledger")
async def list_ledger(
    branch_id: Optional[str] = None,
    pool: Optional[str] = None,          # "reserve" | "deficit" | None=both
    entry_type: Optional[str] = None,    # filter by type
    limit: int = 50,
    skip: int = 0,
    user=Depends(get_current_user),
):
    """Paginated ledger list. Non-admin users are scoped to their branch."""
    check_perm(user, "reports", "view")

    query: dict = {}
    if user.get("role") != "admin":
        query["branch_id"] = user.get("branch_id", "__none__")
    if branch_id:
        query["branch_id"] = branch_id
    if pool:
        query["pool"] = pool
    if entry_type:
        query["type"] = entry_type

    total = await db[LEDGER].count_documents(query)
    entries = await db[LEDGER].find(query, {"_id": 0}).sort("created_at", -1).skip(skip).limit(limit).to_list(limit)
    return {"entries": entries, "total": total}


@router.post("/apply")
async def apply_reserve(data: dict, user=Depends(get_current_user)):
    """
    Debit the reserve pool to offset an audit finding (inventory variance,
    cash discrepancy, etc.). Requires a manager/admin PIN.
    """
    from routes.verify import verify_pin_for_action

    branch_id = (data.get("branch_id") or "").strip()
    amount = round(float(data.get("amount") or 0), 2)
    applied_to = (data.get("applied_to") or "inventory_variance").strip()
    reason = (data.get("reason") or "").strip()
    pin = str(data.get("pin", ""))
    audit_session_id = (data.get("audit_session_id") or "").strip()

    if not branch_id:
        raise HTTPException(status_code=400, detail="branch_id is required")
    if amount <= 0:
        raise HTTPException(status_code=400, detail="amount must be positive")
    if applied_to not in ("inventory_variance", "cash_discrepancy", "other"):
        raise HTTPException(status_code=400, detail="invalid applied_to")
    if not reason:
        raise HTTPException(status_code=400, detail="reason is required")

    verifier = await verify_pin_for_action(pin, "transaction_verify", branch_id=branch_id)
    if not verifier:
        raise HTTPException(status_code=403, detail="Invalid PIN — manager/admin required")

    balance = await _current_balance(branch_id, "reserve")
    if amount > balance + 0.01:
        raise HTTPException(
            status_code=400,
            detail=f"Insufficient reserve balance — have ₱{balance:,.2f}, attempted ₱{amount:,.2f}",
        )

    branch = await db.branches.find_one({"id": branch_id}, {"_id": 0, "name": 1}) or {}
    source_ref = f"Audit apply — {applied_to.replace('_', ' ')}"
    if audit_session_id:
        source_ref += f" (session {audit_session_id[:8]})"

    entry = await _post_entry(
        branch_id=branch_id, branch_name=branch.get("name", ""),
        pool="reserve", entry_type="apply_audit",
        amount=-amount,
        source_type="audit_session" if audit_session_id else "manual",
        source_id=audit_session_id or "manual",
        source_ref=source_ref,
        note=reason, applied_to=applied_to, verifier=verifier, user=user,
    )

    # Link to the audit session so future views can pull this up
    if audit_session_id:
        await db.audits.update_one(
            {"id": audit_session_id},
            {"$push": {"reserve_applications": {
                "entry_id": entry["id"], "amount": amount,
                "applied_to": applied_to, "reason": reason,
                "applied_at": entry["created_at"],
                "applied_by_name": user.get("full_name", user.get("username", "")),
            }}}
        )

    new_balance = await _current_balance(branch_id, "reserve")
    return {"entry": entry, "new_reserve_balance": new_balance}


@router.post("/net-shortage")
async def net_shortage(data: dict, user=Depends(get_current_user)):
    """
    Manual netting — owner approves applying reserve against the deficit pool.
    Creates a paired debit in both pools so both balances drop by `amount`.
    """
    from routes.verify import verify_pin_for_action

    branch_id = (data.get("branch_id") or "").strip()
    amount = round(float(data.get("amount") or 0), 2)
    reason = (data.get("reason") or "Net shortage against reserve").strip()
    pin = str(data.get("pin", ""))

    if not branch_id:
        raise HTTPException(status_code=400, detail="branch_id is required")
    if amount <= 0:
        raise HTTPException(status_code=400, detail="amount must be positive")

    verifier = await verify_pin_for_action(pin, "transaction_verify", branch_id=branch_id)
    if not verifier:
        raise HTTPException(status_code=403, detail="Invalid PIN — manager/admin required")

    reserve_bal = await _current_balance(branch_id, "reserve")
    deficit_bal = await _current_balance(branch_id, "deficit")
    cap = min(reserve_bal, deficit_bal)
    if amount > cap + 0.01:
        raise HTTPException(
            status_code=400,
            detail=f"Netting capped at ₱{cap:,.2f} — reserve={reserve_bal:,.2f}, deficit={deficit_bal:,.2f}",
        )

    branch = await db.branches.find_one({"id": branch_id}, {"_id": 0, "name": 1}) or {}

    reserve_entry = await _post_entry(
        branch_id=branch_id, branch_name=branch.get("name", ""),
        pool="reserve", entry_type="net_shortage",
        amount=-amount, source_type="manual", source_id="net_shortage",
        source_ref="Manual net vs deficit", note=reason,
        verifier=verifier, user=user,
    )
    deficit_entry = await _post_entry(
        branch_id=branch_id, branch_name=branch.get("name", ""),
        pool="deficit", entry_type="net_shortage",
        amount=-amount, source_type="manual", source_id="net_shortage",
        source_ref="Manual net vs reserve", note=reason,
        paired_entry_id=reserve_entry["id"],
        verifier=verifier, user=user,
    )
    # Back-fill paired_entry_id on the reserve side
    await db[LEDGER].update_one(
        {"id": reserve_entry["id"]}, {"$set": {"paired_entry_id": deficit_entry["id"]}}
    )

    return {
        "reserve_entry": reserve_entry,
        "deficit_entry": deficit_entry,
        "new_reserve_balance": await _current_balance(branch_id, "reserve"),
        "new_deficit_balance": await _current_balance(branch_id, "deficit"),
    }


@router.post("/claw-back")
async def claw_back(data: dict, user=Depends(get_current_user)):
    """
    Reverse a specific ledger entry (e.g. mistaken auto_credit from a close
    that was later re-keyed). Creates an opposite-sign entry linked via
    paired_entry_id, and marks the original `reversed=True`.
    """
    from routes.verify import verify_pin_for_action

    entry_id = (data.get("entry_id") or "").strip()
    reason = (data.get("reason") or "").strip()
    pin = str(data.get("pin", ""))

    if not entry_id or not reason:
        raise HTTPException(status_code=400, detail="entry_id and reason are required")

    orig = await db[LEDGER].find_one({"id": entry_id}, {"_id": 0})
    if not orig:
        raise HTTPException(status_code=404, detail="Entry not found")
    if orig.get("reversed"):
        raise HTTPException(status_code=400, detail="Entry already reversed")

    verifier = await verify_pin_for_action(pin, "transaction_verify", branch_id=orig.get("branch_id"))
    if not verifier:
        raise HTTPException(status_code=403, detail="Invalid PIN — manager/admin required")

    reversal = await _post_entry(
        branch_id=orig["branch_id"], branch_name=orig.get("branch_name", ""),
        pool=orig["pool"], entry_type="claw_back",
        amount=-orig["amount"], source_type=orig.get("source_type", "manual"),
        source_id=orig.get("source_id", ""),
        source_ref=f"Claw-back of {orig.get('source_ref', '')}",
        note=reason, paired_entry_id=orig["id"],
        verifier=verifier, user=user,
    )

    await db[LEDGER].update_one(
        {"id": orig["id"]}, {"$set": {"reversed": True, "reversed_by_entry_id": reversal["id"]}}
    )

    return {
        "reversal_entry": reversal,
        "new_balance": await _current_balance(orig["branch_id"], orig["pool"]),
    }


@router.post("/backfill")
async def backfill_from_closings(data: Optional[dict] = None, user=Depends(get_current_user)):
    """
    Admin-only one-time backfill that walks every daily_closings record with
    non-zero over_short and posts the corresponding ledger entries.
    Idempotent — the record_daily_close_variance() helper guards against
    duplicates by (source_id, type=auto_credit).
    """
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")

    data = data or {}
    branch_id = data.get("branch_id")
    query = {"status": "closed", "over_short": {"$ne": 0}}
    if branch_id:
        query["branch_id"] = branch_id

    closings = await db.daily_closings.find(query, {"_id": 0}).sort("date", 1).to_list(5000)
    created = 0
    for c in closings:
        res = await record_daily_close_variance(c, user=user)
        if res:
            created += 1
    return {"scanned": len(closings), "created": created}

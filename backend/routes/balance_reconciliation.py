"""
Phase 2A — Customer Balance Reconciliation Report (Audit 2026-02)

READ-ONLY diagnostic endpoint. Compares each customer's stored `balance` (the
canonical AR field) against the ledger-computed balance derived from their
non-voided invoices. No mutations of any kind.

Scope rules (per owner):
  * Tenant + branch scoped (admin/owner/super-admin only)
  * No DB writes
  * Surface drift > ₱0.50 only
  * Sort by absolute drift descending; cap at 500 rows
  * Flag (don't guess) ambiguous cases — e.g. customers with active crop credits

Ledger formula (verified against actual schema before commit):
    ledger_balance(c) = Σ inv.balance
                       for inv in invoices
                       where inv.customer_id == c.id
                         and inv.status NOT IN NON_LEDGER_INVOICE_STATUSES
    drift = round(stored_balance - ledger_balance, 2)

Returns/credit memos/payments are ALREADY reflected in `inv.balance` (they
either decrement it directly or remove the invoice from the open ledger via
status changes). They therefore must NOT be subtracted again — that would
double-count credit. We still surface their COUNTS for review context.

Risk bands (per owner):
    |drift| ≤ 0.50   → OK         (filtered out of response)
    0.50 < |drift| ≤ 100   → Minor Difference
    100 < |drift| ≤ 5,000 → Needs Review
    |drift| > 5,000        → Critical
"""
from fastapi import APIRouter, Depends, HTTPException
from typing import Optional
from datetime import datetime, timezone

from config import db
from utils import get_current_user, now_iso, assert_branch_access


router = APIRouter(prefix="/admin", tags=["Admin · Balance Reconciliation"])


# Statuses that should NOT contribute to a customer's outstanding AR. These
# match the invariants enforced by Phase 1 (`assert_invoice_payable`) plus
# the legacy draft state `cancelled_draft`.
NON_LEDGER_INVOICE_STATUSES = {
    "voided",
    "cancelled",
    "cancelled_draft",
    "deleted",
    "for_preparation",
    "error_partial_write",
}

DRIFT_FLOOR = 0.50           # surface drift strictly greater than this
MAX_ROWS = 500               # response cap

# Risk band thresholds (absolute drift in ₱)
MINOR_BAND = 100.0
REVIEW_BAND = 5_000.0


def _classify_drift(abs_drift: float) -> tuple[str, str]:
    """Return (risk_level, recommended_action)."""
    if abs_drift <= DRIFT_FLOOR:
        return ("OK", "No action — within rounding tolerance.")
    if abs_drift <= MINOR_BAND:
        return (
            "Minor Difference",
            "Spot-check open invoices and recent payments for rounding "
            "or legacy import residue.",
        )
    if abs_drift <= REVIEW_BAND:
        return (
            "Needs Review",
            "Reconcile open invoices, payment history, and returns. Possible "
            "C-7 residue (pre-fix orphan-offline reconcile no-op).",
        )
    return (
        "Critical",
        "Escalate. Do NOT auto-correct. Audit invoice/payment trail before any "
        "manual adjustment; require owner sign-off.",
    )


def _is_admin_like(user: dict) -> bool:
    return bool(
        user
        and (
            user.get("role") in ("admin", "owner")
            or user.get("is_super_admin")
        )
    )


@router.get("/customer-balance-reconciliation")
async def customer_balance_reconciliation(
    branch_id: Optional[str] = None,
    min_drift: float = DRIFT_FLOOR,
    limit: int = MAX_ROWS,
    user=Depends(get_current_user),
):
    """READ-ONLY drift report. Admin / owner / super-admin only.

    Query params:
      * branch_id — optional home-branch filter
      * min_drift — minimum absolute drift to surface in ₱ (default ₱0.50)
      * limit     — row cap (default and hard max 500), sorted by |drift| desc

    NEVER mutates customer.balance, invoices, payments, returns, or any other
    collection. Pure diagnostic.
    """
    # Defensive bounds (route is also reachable via direct invocation in tests)
    if min_drift is None or min_drift < 0:
        min_drift = DRIFT_FLOOR
    if not limit or limit < 1 or limit > MAX_ROWS:
        limit = MAX_ROWS
    # ── AuthZ ─────────────────────────────────────────────────────────────
    if not _is_admin_like(user):
        raise HTTPException(
            status_code=403,
            detail="Admin / owner / super-admin only.",
        )

    # Branch scoping (defence in depth — TenantCollection already org-scopes)
    if branch_id and branch_id != "all":
        assert_branch_access(user, branch_id)

    # ── Pull all customers in scope ───────────────────────────────────────
    cust_query: dict = {}
    if branch_id and branch_id != "all":
        cust_query["branch_id"] = branch_id

    customers = await db.customers.find(
        cust_query,
        {
            "_id": 0,
            "id": 1, "name": 1, "balance": 1, "branch_id": 1,
            "active": 1, "credit_limit": 1, "phone": 1,
        },
    ).to_list(length=None)

    customer_ids = [c["id"] for c in customers]

    # ── Aggregate invoice ledger per customer ─────────────────────────────
    # We need: ledger_balance, open invoice count, voided count, payment count,
    # last transaction date. One pass.
    invoice_pipeline = [
        {"$match": {"customer_id": {"$in": customer_ids}}},
        {"$project": {
            "_id": 0,
            "customer_id": 1,
            "status": 1,
            "balance": {"$ifNull": ["$balance", 0]},
            "payments": {"$ifNull": ["$payments", []]},
            "invoice_date": {"$ifNull": ["$invoice_date", "$order_date"]},
            "created_at": 1,
        }},
        {"$group": {
            "_id": "$customer_id",
            "ledger_balance": {
                "$sum": {
                    "$cond": [
                        {"$in": [
                            "$status",
                            list(NON_LEDGER_INVOICE_STATUSES),
                        ]},
                        0,
                        "$balance",
                    ],
                }
            },
            "open_invoice_count": {
                "$sum": {
                    "$cond": [
                        {"$in": [
                            "$status",
                            list(NON_LEDGER_INVOICE_STATUSES),
                        ]},
                        0,
                        1,
                    ],
                }
            },
            "voided_or_cancelled_count": {
                "$sum": {
                    "$cond": [
                        {"$in": [
                            "$status",
                            list(NON_LEDGER_INVOICE_STATUSES),
                        ]},
                        1,
                        0,
                    ],
                }
            },
            "payment_count": {
                "$sum": {"$size": {"$ifNull": ["$payments", []]}}
            },
            "last_invoice_date": {
                "$max": {
                    "$ifNull": ["$invoice_date", "$created_at"],
                }
            },
        }},
    ]
    inv_rows = await db.invoices.aggregate(invoice_pipeline).to_list(length=None)
    inv_by_cust = {r["_id"]: r for r in inv_rows}

    # ── Returns count per customer (context only — already in inv.balance) ─
    return_rows = await db.returns.aggregate([
        {"$match": {"customer_id": {"$in": customer_ids}}},
        {"$group": {"_id": "$customer_id", "count": {"$sum": 1}}},
    ]).to_list(length=None)
    ret_by_cust = {r["_id"]: r["count"] for r in return_rows}

    # ── Crop credits flag — parallel ledger, ambiguous attribution ─────────
    cc_rows = await db.crop_credits.aggregate([
        {"$match": {
            "customer_id": {"$in": customer_ids},
            "principal_balance": {"$gt": 0},
        }},
        {"$group": {
            "_id": "$customer_id",
            "active_count": {"$sum": 1},
            "total_principal": {"$sum": {"$ifNull": ["$principal_balance", 0]}},
        }},
    ]).to_list(length=None)
    cc_by_cust = {r["_id"]: r for r in cc_rows}

    # ── Recent merge flag (drift may stem from a merge) ────────────────────
    merge_rows = await db.customer_merges.aggregate([
        {"$match": {
            "$or": [
                {"master_id": {"$in": customer_ids}},
                {"merged_ids": {"$in": customer_ids}},
            ],
        }},
        {"$project": {"_id": 0, "master_id": 1, "merged_ids": 1}},
    ]).to_list(length=None)
    merged_cust_ids: set = set()
    for m in merge_rows:
        if m.get("master_id"):
            merged_cust_ids.add(m["master_id"])
        for mid in m.get("merged_ids") or []:
            merged_cust_ids.add(mid)

    # ── Branch name lookup (for output) ───────────────────────────────────
    branch_ids = {c.get("branch_id") for c in customers if c.get("branch_id")}
    branch_name_by_id: dict = {}
    if branch_ids:
        async for b in db.branches.find(
            {"id": {"$in": list(branch_ids)}},
            {"_id": 0, "id": 1, "name": 1},
        ):
            branch_name_by_id[b["id"]] = b.get("name") or ""

    # ── Compose rows ──────────────────────────────────────────────────────
    rows = []
    risk_counts = {"Minor Difference": 0, "Needs Review": 0, "Critical": 0}
    total_abs_drift = 0.0

    for c in customers:
        cust_id = c["id"]
        stored = float(c.get("balance") or 0)
        inv_data = inv_by_cust.get(cust_id, {})
        ledger_balance = round(float(inv_data.get("ledger_balance", 0) or 0), 2)
        drift = round(stored - ledger_balance, 2)
        abs_drift = abs(drift)

        if abs_drift <= float(min_drift):
            continue

        risk_level, action = _classify_drift(abs_drift)

        flags = []
        if cust_id in cc_by_cust:
            cc = cc_by_cust[cust_id]
            flags.append(
                f"active_crop_credits:{cc['active_count']}:"
                f"₱{round(cc['total_principal'], 2)}"
            )
        if cust_id in merged_cust_ids:
            flags.append("involved_in_customer_merge")
        if not c.get("active", True):
            flags.append("customer_inactive")

        rows.append({
            "customer_id": cust_id,
            "customer_name": c.get("name") or "",
            "customer_phone": c.get("phone") or "",
            "branch_id": c.get("branch_id") or "",
            "branch_name": branch_name_by_id.get(c.get("branch_id") or "", ""),
            "stored_balance": round(stored, 2),
            "ledger_balance": ledger_balance,
            "drift": drift,
            "abs_drift": abs_drift,
            "open_invoice_count": int(inv_data.get("open_invoice_count", 0) or 0),
            "voided_or_cancelled_count": int(
                inv_data.get("voided_or_cancelled_count", 0) or 0
            ),
            "payment_count": int(inv_data.get("payment_count", 0) or 0),
            "return_count": int(ret_by_cust.get(cust_id, 0)),
            "last_transaction_date": inv_data.get("last_invoice_date") or "",
            "risk_level": risk_level,
            "recommended_action": action,
            "flags": flags,
        })
        risk_counts[risk_level] += 1
        total_abs_drift += abs_drift

    rows.sort(key=lambda r: r["abs_drift"], reverse=True)
    rows = rows[:limit]

    return {
        "generated_at": now_iso(),
        "branch_filter": branch_id or "all",
        "phase": "2A",
        "read_only": True,
        "ledger_formula": (
            "ledger_balance(customer) = Σ invoice.balance for invoices "
            "where customer_id matches and status NOT IN "
            f"{sorted(NON_LEDGER_INVOICE_STATUSES)}. "
            "Returns/credit memos/payments already reflected in invoice.balance."
        ),
        "non_ledger_invoice_statuses": sorted(NON_LEDGER_INVOICE_STATUSES),
        "drift_floor": float(min_drift),
        "row_cap": limit,
        "summary": {
            "total_customers_scanned": len(customers),
            "total_with_drift": sum(risk_counts.values()),
            "total_abs_drift_amount": round(total_abs_drift, 2),
            "by_risk": risk_counts,
        },
        "rows": rows,
    }

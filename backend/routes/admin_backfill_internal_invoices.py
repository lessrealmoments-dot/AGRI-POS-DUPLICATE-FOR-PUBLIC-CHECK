"""
Phase 5+ B-1 — Admin-gated one-shot backfill for `internal_invoices`
rows that landed with `organization_id=None` while the collection was
missing from `TENANT_COLLECTIONS`.

Resolves the gap by deriving the correct `organization_id` from the
invoice's `from_branch_id` (preferred) or `to_branch_id` (fallback),
looking up the matching `branches` row.

Endpoint behavior
-----------------
  GET  /api/admin/backfill/internal-invoices-org-id          → DRY-RUN report (no writes)
  POST /api/admin/backfill/internal-invoices-org-id?apply=1  → Apply + report

Security
--------
  * `user.role == "admin"` (hard-gated). Non-admin → 403.
  * The endpoint operates on `_raw_db` because the affected rows lack
    a tenant field and therefore become invisible to the proxy by
    design. The admin's own org is recorded as `requested_by_org` so
    audit logs can see who triggered each cross-tenant sweep.
  * Strictly idempotent: rows already carrying a valid `organization_id`
    are left untouched.

Reporting fields (per response)
-------------------------------
  scanned   — total invoice rows considered (organization_id NULL OR missing)
  updated   — rows successfully stamped with derived organization_id
  skipped   — rows already carrying a valid `organization_id`
  unresolved — invoice ids where no branch row could be found to derive org
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from datetime import datetime, timezone

from config import _raw_db
from utils import get_current_user


router = APIRouter(prefix="/admin/backfill", tags=["Admin Backfill"])


async def _resolve_org_for_invoice(inv: dict) -> str | None:
    """Look up organization_id from from_branch_id (preferred) or
    to_branch_id (fallback). Returns None if neither resolves."""
    for key in ("from_branch_id", "to_branch_id"):
        bid = inv.get(key) or ""
        if not bid:
            continue
        branch = await _raw_db.branches.find_one(
            {"id": bid}, {"_id": 0, "organization_id": 1}
        )
        if branch and branch.get("organization_id"):
            return branch["organization_id"]
    return None


async def _sweep(apply: bool, requester: dict) -> dict:
    """Shared body for dry-run and apply paths."""
    scanned = 0
    updated = 0
    skipped = 0
    unresolved: list[dict] = []
    samples: list[dict] = []

    cursor = _raw_db.internal_invoices.find(
        {"$or": [
            {"organization_id": {"$exists": False}},
            {"organization_id": None},
            {"organization_id": ""},
        ]},
        {"_id": 0, "id": 1, "invoice_number": 1, "from_branch_id": 1,
         "to_branch_id": 1, "organization_id": 1},
    )
    async for inv in cursor:
        scanned += 1
        if inv.get("organization_id"):
            skipped += 1
            continue
        derived = await _resolve_org_for_invoice(inv)
        if not derived:
            unresolved.append({
                "invoice_id": inv.get("id"),
                "invoice_number": inv.get("invoice_number"),
                "from_branch_id": inv.get("from_branch_id"),
                "to_branch_id": inv.get("to_branch_id"),
            })
            continue
        if apply:
            await _raw_db.internal_invoices.update_one(
                {"id": inv["id"]},
                {"$set": {
                    "organization_id": derived,
                    "iso_backfill_at": datetime.now(timezone.utc).isoformat(),
                    "iso_backfill_by": requester.get("id", ""),
                }},
            )
        updated += 1
        if len(samples) < 5:
            samples.append({
                "invoice_id": inv.get("id"),
                "invoice_number": inv.get("invoice_number"),
                "derived_org_id": derived,
            })

    return {
        "mode": "apply" if apply else "dry-run",
        "scanned": scanned,
        "updated": updated,
        "skipped": skipped,
        "unresolved_count": len(unresolved),
        "unresolved": unresolved[:50],  # cap response size
        "samples": samples,
        "requested_by": requester.get("id", ""),
        "requested_by_org": requester.get("organization_id", ""),
        "ran_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/internal-invoices-org-id")
async def dry_run_internal_invoices_org_id(user=Depends(get_current_user)):
    """Dry-run: report what WOULD be backfilled. No writes."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    return await _sweep(apply=False, requester=user)


@router.post("/internal-invoices-org-id")
async def apply_internal_invoices_org_id(
    user=Depends(get_current_user),
    apply: int = Query(0, description="Set to 1 to actually write"),
):
    """Apply the backfill. Requires `?apply=1` as a final-confirm guard."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    if not apply:
        raise HTTPException(
            status_code=400,
            detail="Pass ?apply=1 to confirm write. Use GET for dry-run.",
        )
    return await _sweep(apply=True, requester=user)

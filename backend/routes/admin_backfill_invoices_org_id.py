"""
Phase 5+ H-1 (visibility side-effect) — Admin-gated one-shot backfill for
`invoices` rows whose `organization_id` was stripped by the legacy
`replace_one(filter, invoice)` draft-finalize path in `routes/sales.py`
(pre-2026-05-09 / pre-`200b6c1e`).

Symptom on live (sample-site evidence, 2026-05-12):
  * customer.balance shows correctly (the proxied `$inc` survived),
  * but the underlying invoice row is invisible to every tenant-scoped
    read because the proxy filters on `organization_id == ctx`,
  * so /payments, Sales History, /customers/{id}/invoices,
    /customers/{id}/statement, and /search/transactions all return
    "no rows" for the affected customer.

This endpoint scans the RAW `invoices` collection (not the tenant proxy)
for rows where `organization_id` is missing/null/empty, derives the
correct org via three independent paths, and stamps it back. It is
strictly idempotent.

Endpoint behaviour
------------------
  GET  /api/admin/backfill/invoices-org-id           → DRY-RUN (no writes)
  POST /api/admin/backfill/invoices-org-id?apply=1   → APPLY + report

Security
--------
  * `user.role == "admin"` (hard-gated). Non-admin → 403.
  * Runs against `_raw_db` because the affected rows are by definition
    invisible to the tenant proxy. The admin's own org is recorded as
    `requested_by_org` for audit chain.
  * Strictly idempotent. Rows already carrying a non-empty
    `organization_id` are skipped, never overwritten.

Resolution paths (in priority order)
------------------------------------
  1. branch_id    → branches.organization_id
  2. customer_id  → customers.organization_id
  3. cashier_id   → users.organization_id

Rules:
  * If only one path resolves and the others are missing/empty → use it.
  * If multiple paths resolve to the SAME org_id → use it (counts as
    multi-path agreement).
  * If two or more paths resolve to DIFFERENT org_ids → CONFLICT, skip
    (never guess; surface for human review).
  * If no path resolves → UNRESOLVED, skip.

Audit
-----
  Every applied row writes a row to `admin_backfill_log` with:
    {
      "id": new_id(),
      "kind": "invoices_org_id_backfill",
      "invoice_id":     <orphan invoice id>,
      "invoice_number": <orphan invoice number>,
      "resolved_org_id":<derived org>,
      "resolution_source": "branch" | "customer" | "cashier" | "multi",
      "actor_user_id":   <admin id>,
      "actor_org_id":    <admin's org id>,
      "ran_at":          ISO8601 UTC,
    }
"""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from config import _raw_db
from utils import get_current_user, new_id


router = APIRouter(prefix="/admin/backfill", tags=["Admin Backfill"])


# ─── Per-doc resolver ────────────────────────────────────────────────
async def _resolve_org_paths(inv: dict) -> dict:
    """Return a dict {branch: org|None, customer: org|None, cashier: org|None}
    for this orphan invoice. Lookups are case- and field-name-stable."""
    resolved = {"branch": None, "customer": None, "cashier": None}

    bid = (inv.get("branch_id") or "").strip()
    if bid:
        br = await _raw_db.branches.find_one(
            {"id": bid}, {"_id": 0, "organization_id": 1}
        )
        if br and br.get("organization_id"):
            resolved["branch"] = br["organization_id"]

    cid = (inv.get("customer_id") or "").strip()
    if cid:
        cu = await _raw_db.customers.find_one(
            {"id": cid}, {"_id": 0, "organization_id": 1}
        )
        if cu and cu.get("organization_id"):
            resolved["customer"] = cu["organization_id"]

    kid = (inv.get("cashier_id") or "").strip()
    if kid:
        us = await _raw_db.users.find_one(
            {"id": kid}, {"_id": 0, "organization_id": 1}
        )
        if us and us.get("organization_id"):
            resolved["cashier"] = us["organization_id"]

    return resolved


def _decide(resolved: dict) -> tuple[Optional[str], str]:
    """Given the per-path resolution, decide what (if anything) to stamp.

    Returns (org_to_stamp, source_label).
      * org_to_stamp is None when the row must be skipped.
      * source_label is one of: 'branch', 'customer', 'cashier',
        'multi', 'conflict', 'unresolved'.
    """
    distinct = {v for v in resolved.values() if v}
    if not distinct:
        return None, "unresolved"
    if len(distinct) > 1:
        return None, "conflict"
    # Exactly one distinct org across all resolving paths.
    only = next(iter(distinct))
    resolving = [k for k, v in resolved.items() if v == only]
    if len(resolving) >= 2:
        return only, "multi"
    return only, resolving[0]


# ─── Sweep ───────────────────────────────────────────────────────────
async def _sweep(apply: bool, requester: dict) -> dict:
    scanned = 0
    missing_org = 0
    skipped_already_stamped = 0  # rows that matched the filter loosely but
                                  # already carry a valid org_id (defence-in-depth)
    resolved_by_branch = 0
    resolved_by_customer = 0
    resolved_by_cashier = 0
    multi_path_agreed = 0
    multi_path_conflict = 0
    unresolved = 0

    per_tenant: dict[str, dict] = {}
    updated_samples: list[dict] = []
    conflict_samples: list[dict] = []
    unresolved_samples: list[dict] = []

    now_iso = datetime.now(timezone.utc).isoformat()
    actor_id = requester.get("id", "")
    actor_org = requester.get("organization_id", "")

    cursor = _raw_db.invoices.find(
        {"$or": [
            {"organization_id": {"$exists": False}},
            {"organization_id": None},
            {"organization_id": ""},
        ]},
        {
            "_id": 0,
            "id": 1, "invoice_number": 1, "branch_id": 1,
            "customer_id": 1, "customer_name": 1,
            "cashier_id": 1, "status": 1, "balance": 1,
            "organization_id": 1,
        },
    )

    async for inv in cursor:
        scanned += 1
        if inv.get("organization_id"):
            # Filter matched a corner case where the field exists but is
            # falsy in a way pymongo's filter accepted. Defence in depth.
            skipped_already_stamped += 1
            continue
        missing_org += 1

        resolved = await _resolve_org_paths(inv)
        org, source = _decide(resolved)

        if source == "branch":
            resolved_by_branch += 1
        elif source == "customer":
            resolved_by_customer += 1
        elif source == "cashier":
            resolved_by_cashier += 1
        elif source == "multi":
            multi_path_agreed += 1
        elif source == "conflict":
            multi_path_conflict += 1
            if len(conflict_samples) < 25:
                conflict_samples.append({
                    "invoice_id": inv.get("id"),
                    "invoice_number": inv.get("invoice_number"),
                    "branch_id": inv.get("branch_id"),
                    "customer_id": inv.get("customer_id"),
                    "cashier_id": inv.get("cashier_id"),
                    "resolved": resolved,
                })
            continue
        else:  # unresolved
            unresolved += 1
            if len(unresolved_samples) < 25:
                unresolved_samples.append({
                    "invoice_id": inv.get("id"),
                    "invoice_number": inv.get("invoice_number"),
                    "branch_id": inv.get("branch_id"),
                    "customer_id": inv.get("customer_id"),
                    "cashier_id": inv.get("cashier_id"),
                })
            continue

        # Per-tenant breakdown
        slot = per_tenant.setdefault(org, {
            "org_id": org,
            "count": 0,
            "ar_pesos": 0.0,
            "sample_invoice_numbers": [],
        })
        slot["count"] += 1
        try:
            slot["ar_pesos"] += float(inv.get("balance") or 0)
        except (TypeError, ValueError):
            pass
        if len(slot["sample_invoice_numbers"]) < 5 and inv.get("invoice_number"):
            slot["sample_invoice_numbers"].append(inv["invoice_number"])

        # Apply-only side effects
        if apply:
            await _raw_db.invoices.update_one(
                {"id": inv["id"]},
                {"$set": {
                    "organization_id": org,
                    "_org_id_backfilled_at": now_iso,
                    "_org_id_backfilled_by": actor_id,
                    "_org_id_backfilled_source": source,
                }},
            )
            await _raw_db.admin_backfill_log.insert_one({
                "id": new_id(),
                "kind": "invoices_org_id_backfill",
                "invoice_id": inv.get("id"),
                "invoice_number": inv.get("invoice_number"),
                "resolved_org_id": org,
                "resolution_source": source,
                "actor_user_id": actor_id,
                "actor_org_id": actor_org,
                "ran_at": now_iso,
            })

        if len(updated_samples) < 10:
            updated_samples.append({
                "invoice_id": inv.get("id"),
                "invoice_number": inv.get("invoice_number"),
                "resolved_org_id": org,
                "resolution_source": source,
                "balance": inv.get("balance"),
                "status": inv.get("status"),
            })

    # Round per-tenant pesos for clean reports
    for slot in per_tenant.values():
        slot["ar_pesos"] = round(slot["ar_pesos"], 2)

    return {
        "mode": "apply" if apply else "dry-run",
        "scanned": scanned,
        "missing_org_id": missing_org,
        "skipped_already_stamped": skipped_already_stamped,
        "resolved_by_branch": resolved_by_branch,
        "resolved_by_customer": resolved_by_customer,
        "resolved_by_cashier": resolved_by_cashier,
        "multi_path_agreed": multi_path_agreed,
        "multi_path_conflict": multi_path_conflict,
        "unresolved": unresolved,
        "per_tenant_breakdown": list(per_tenant.values()),
        "updated_samples": updated_samples,
        "conflict_samples": conflict_samples,
        "unresolved_samples": unresolved_samples,
        "requested_by": actor_id,
        "requested_by_org": actor_org,
        "ran_at": now_iso,
    }


# ─── Routes ─────────────────────────────────────────────────────────
@router.get("/invoices-org-id")
async def dry_run_invoices_org_id(user=Depends(get_current_user)):
    """Dry-run: report what WOULD be backfilled. No writes."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    return await _sweep(apply=False, requester=user)


@router.post("/invoices-org-id")
async def apply_invoices_org_id(
    user=Depends(get_current_user),
    apply: int = Query(0, description="Set to 1 to actually write"),
):
    """Apply the backfill. Requires `?apply=1` as a final-confirm guard.
    Writes an `admin_backfill_log` row per stamped invoice."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    if not apply:
        raise HTTPException(
            status_code=400,
            detail="Pass ?apply=1 to confirm write. Use GET for dry-run.",
        )
    return await _sweep(apply=True, requester=user)

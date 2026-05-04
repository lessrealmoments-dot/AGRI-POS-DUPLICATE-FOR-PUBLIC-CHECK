"""
Audit Center routes: session management + comprehensive audit computation.
Full audit (admin): inventory via count sheets + all financial sections.
Partial audit (manager): financial sections only, no physical count required.
"""
from fastapi import APIRouter, Depends, HTTPException
from typing import Optional
from datetime import datetime, timezone, timedelta
from config import db
from utils import get_current_user, check_perm, now_iso, new_id

router = APIRouter(prefix="/audit", tags=["Audit"])


# ─────────────────────────────────────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def severity(variance_pct: float) -> str:
    """Traffic-light severity for a variance percentage."""
    if abs(variance_pct) <= 1:
        return "ok"        # 🟢 ≤1%
    if abs(variance_pct) <= 5:
        return "warning"   # 🟡 1–5%
    return "critical"      # 🔴 >5%


def cash_severity(discrepancy: float) -> str:
    if abs(discrepancy) <= 100:
        return "ok"
    if abs(discrepancy) <= 500:
        return "warning"
    return "critical"


# ─────────────────────────────────────────────────────────────────────────────
#  SESSION CRUD
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/sessions")
async def list_audit_sessions(
    branch_id: Optional[str] = None,
    limit: int = 20,
    user=Depends(get_current_user),
):
    """List past audit sessions, most recent first."""
    query = {}
    if branch_id:
        query["branch_id"] = branch_id
    elif user.get("branch_id") and user.get("role") != "admin":
        query["branch_id"] = user["branch_id"]

    sessions = await db.audits.find(query, {"_id": 0}).sort("created_at", -1).limit(limit).to_list(limit)
    return {"sessions": sessions, "total": len(sessions)}


@router.get("/sessions/{session_id}")
async def get_audit_session(session_id: str, user=Depends(get_current_user)):
    session = await db.audits.find_one({"id": session_id}, {"_id": 0})
    if not session:
        raise HTTPException(status_code=404, detail="Audit session not found")
    return session


@router.post("/sessions")
async def create_audit_session(data: dict, user=Depends(get_current_user)):
    """Create a new audit session. Triggers full computation."""
    audit_type = data.get("audit_type", "partial")  # 'partial' | 'full'
    if audit_type == "full" and user.get("role") not in ["admin"]:
        raise HTTPException(status_code=403, detail="Only admins can run a full audit")

    branch_id = data.get("branch_id", user.get("branch_id", ""))
    if not branch_id and user.get("role") != "admin":
        raise HTTPException(status_code=400, detail="Branch required")

    period_from = data.get("period_from", datetime.now(timezone.utc).strftime("%Y-%m-01"))
    period_to = data.get("period_to", datetime.now(timezone.utc).strftime("%Y-%m-%d"))

    branch_doc = await db.branches.find_one({"id": branch_id}, {"_id": 0, "name": 1}) if branch_id else None
    branch_name = branch_doc.get("name", branch_id) if branch_doc else "All Branches"

    session = {
        "id": new_id(),
        "branch_id": branch_id,
        "branch_name": branch_name,
        "audit_type": audit_type,
        "period_from": period_from,
        "period_to": period_to,
        "count_sheet_baseline_id": data.get("count_sheet_baseline_id"),
        "count_sheet_current_id": data.get("count_sheet_current_id"),
        "status": "in_progress",
        "sections_status": {},
        "sections_notes": {},
        "overall_score": None,
        "created_by": user["id"],
        "created_by_name": user.get("full_name", user["username"]),
        "created_at": now_iso(),
        "completed_at": None,
        "completed_by": None,
    }
    await db.audits.insert_one(session)
    del session["_id"]
    return session


@router.put("/sessions/{session_id}")
async def update_audit_session(session_id: str, data: dict, user=Depends(get_current_user)):
    """Save section notes, mark sections complete, update overall score."""
    session = await db.audits.find_one({"id": session_id}, {"_id": 0})
    if not session:
        raise HTTPException(status_code=404, detail="Audit session not found")

    update = {}
    if "sections_status" in data:
        update["sections_status"] = data["sections_status"]
    if "sections_notes" in data:
        update["sections_notes"] = data["sections_notes"]
    if "overall_score" in data:
        update["overall_score"] = data["overall_score"]
    if data.get("status") == "completed":
        update["status"] = "completed"
        update["completed_at"] = now_iso()
        update["completed_by"] = user.get("full_name", user["username"])
    if update:
        await db.audits.update_one({"id": session_id}, {"$set": update})

    return await db.audits.find_one({"id": session_id}, {"_id": 0})


# ─────────────────────────────────────────────────────────────────────────────
#  BULK VERIFY — verify multiple items at once from audit
# ─────────────────────────────────────────────────────────────────────────────

BULK_VERIFY_COLLECTIONS = {
    "expense": "expenses",
    "purchase_order": "purchase_orders",
    "invoice": "invoices",
}

@router.post("/bulk-verify")
async def bulk_verify(data: dict, user=Depends(get_current_user)):
    """
    Verify multiple items at once with a single PIN entry.
    Expects: { pin: str, items: [{ doc_type: str, doc_id: str }] }
    """
    from routes.verify import verify_pin_for_action

    pin = str(data.get("pin", ""))
    items = data.get("items", [])
    if not items:
        raise HTTPException(status_code=400, detail="No items to verify")

    verifier = await verify_pin_for_action(pin, "transaction_verify")
    if not verifier:
        raise HTTPException(status_code=400, detail="Invalid PIN — not recognized as admin PIN, TOTP, or auditor PIN")

    verified_count = 0
    errors = []
    for item in items:
        doc_type = item.get("doc_type", "")
        doc_id = item.get("doc_id", "")
        if doc_type not in BULK_VERIFY_COLLECTIONS:
            errors.append(f"Unknown type: {doc_type}")
            continue

        collection = getattr(db, BULK_VERIFY_COLLECTIONS[doc_type])
        result = await collection.update_one(
            {"id": doc_id},
            {"$set": {
                "verified": True,
                "verified_by_id": verifier["verifier_id"],
                "verified_by_name": verifier["verifier_name"],
                "verified_method": verifier["method"],
                "verified_at": now_iso(),
                "verification_status": "clean",
            }}
        )
        if result.modified_count > 0:
            verified_count += 1

            # Also mark receipt as reviewed if receipts exist
            upload_sessions = await db.upload_sessions.find(
                {"record_type": doc_type, "record_id": doc_id, "is_pending": {"$ne": True}},
                {"_id": 0, "file_count": 1}
            ).to_list(20)
            if sum(s.get("file_count", 0) for s in upload_sessions) > 0:
                await collection.update_one({"id": doc_id}, {"$set": {
                    "receipt_review_status": "reviewed",
                    "receipt_reviewed_by_id": verifier["verifier_id"],
                    "receipt_reviewed_by_name": verifier["verifier_name"],
                    "receipt_reviewed_at": now_iso(),
                }})

    return {
        "verified_count": verified_count,
        "total_requested": len(items),
        "errors": errors,
        "verified_by": verifier["verifier_name"],
    }



# ─────────────────────────────────────────────────────────────────────────────
#  COMPUTE — The audit engine
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/compute")
async def compute_audit(
    branch_id: Optional[str] = None,
    period_from: Optional[str] = None,
    period_to: Optional[str] = None,
    audit_type: str = "partial",          # partial | full
    count_sheet_baseline_id: Optional[str] = None,
    count_sheet_current_id: Optional[str] = None,
    user=Depends(get_current_user),
):
    """
    Compute all audit sections for a given period/branch.
    Returns structured data for each section with discrepancies computed.
    Full audit requires two completed count sheets (baseline + current).
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if not period_from:
        period_from = datetime.now(timezone.utc).strftime("%Y-%m-01")
    if not period_to:
        period_to = today

    b_id = branch_id or user.get("branch_id", "")

    result = {
        "period_from": period_from,
        "period_to": period_to,
        "branch_id": b_id,
        "audit_type": audit_type,
        "computed_at": now_iso(),
    }

    # ── Section 2: Cash Reconciliation ──────────────────────────────────────
    result["cash"] = await _compute_cash(b_id, period_from, period_to)

    # ── Section 3: Sales Audit ───────────────────────────────────────────────
    result["sales"] = await _compute_sales(b_id, period_from, period_to)

    # ── Section 4: AR Audit ──────────────────────────────────────────────────
    result["ar"] = await _compute_ar(b_id, period_from, period_to)

    # ── Section 5: Payables Audit ────────────────────────────────────────────
    result["payables"] = await _compute_payables(b_id, period_from, period_to)

    # ── Section 6: Branch Transfers ──────────────────────────────────────────
    result["transfers"] = await _compute_transfers(b_id, period_from, period_to)

    # ── Section 7: Returns & Losses ──────────────────────────────────────────
    result["returns"] = await _compute_returns(b_id, period_from, period_to)

    # ── Section 8: Digital Payments ──────────────────────────────────────────
    result["digital"] = await _compute_digital(b_id, period_from, period_to)

    # ── Section 9: User Activity ─────────────────────────────────────────────
    result["activity"] = await _compute_activity(b_id, period_from, period_to)

    # ── Section 1: Inventory (full audit — needs count sheets) ───────────────
    if audit_type == "full" and count_sheet_baseline_id and count_sheet_current_id:
        result["inventory"] = await _compute_inventory(
            b_id, count_sheet_baseline_id, count_sheet_current_id
        )
    elif audit_type == "full":
        # Auto-find last two completed count sheets
        cs_list = await db.count_sheets.find(
            {"branch_id": b_id, "status": "completed"},
            {"_id": 0, "id": 1, "count_sheet_number": 1, "completed_at": 1}
        ).sort("completed_at", -1).limit(2).to_list(2)
        if len(cs_list) >= 2:
            result["inventory"] = await _compute_inventory(b_id, cs_list[1]["id"], cs_list[0]["id"])
            result["inventory"]["auto_detected"] = True
            result["inventory"]["baseline_ref"] = cs_list[1]["count_sheet_number"]
            result["inventory"]["current_ref"] = cs_list[0]["count_sheet_number"]
        else:
            result["inventory"] = {
                "available": False,
                "message": "Need at least 2 completed count sheets for a Full Audit inventory comparison.",
                "count_sheets_found": len(cs_list),
            }
    else:
        result["inventory"] = {"available": False, "message": "Inventory comparison requires a Full Audit with count sheets."}

    # ── Section N: Security Flags ────────────────────────────────────────────
    result["security"] = await _compute_security(period_from, period_to)

    # ── Section: Unverified Items ─────────────────────────────────────────
    result["unverified"] = await _compute_unverified(b_id, period_from, period_to)

    # ── Section: KPI Ribbon + Trend (Phase 1 deep analysis) ───────────────
    result["kpis"] = await _compute_kpis(b_id, period_from, period_to, result)

    # Previous period of equal length — for trend deltas
    try:
        pf = datetime.strptime(period_from, "%Y-%m-%d").date()
        pt = datetime.strptime(period_to, "%Y-%m-%d").date()
        span = (pt - pf).days + 1  # inclusive
        prev_to = pf - timedelta(days=1)
        prev_from = prev_to - timedelta(days=span - 1)
        result["kpis_prev"] = await _compute_kpis(
            b_id, prev_from.strftime("%Y-%m-%d"), prev_to.strftime("%Y-%m-%d"), None
        )
        # attach delta summary
        result["kpis"]["trend"] = _compute_trend_deltas(result["kpis"], result["kpis_prev"])
    except ValueError as ex:
        # Bad date string — not a real error, just skip trend comparison
        import logging
        logging.getLogger(__name__).warning("Audit kpis_prev date-parse skipped: %s", ex)
        result["kpis_prev"] = None

    # ── Weighted Health + Fraud Risk scores ──────────────────────────────
    result["scores"] = _compute_scores(result)

    # ── Overage Reserve snapshot (so AuditCenter can show offset hint) ────
    try:
        from routes.overage_reserve import _current_balance
        if b_id:
            result["reserve"] = {
                "branch_id": b_id,
                "reserve_balance": await _current_balance(b_id, "reserve"),
                "deficit_balance": await _current_balance(b_id, "deficit"),
            }
        else:
            # Branchless org-wide compute — explicit null contract
            result["reserve"] = None
    except Exception as ex:
        import logging
        logging.getLogger(__name__).warning("Reserve snapshot in audit/compute failed: %s", ex)
        result["reserve"] = None

    return result


# ─────────────────────────────────────────────────────────────────────────────
#  AUDIT PULSE — condensed dashboard payload for the Audit Pulse widget.
#  Reuses compute_audit and projects down to a small, fast payload.
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/pulse")
async def audit_pulse(
    branch_id: Optional[str] = None,
    days: int = 30,
    user=Depends(get_current_user),
):
    """
    Lightweight snapshot of Health + Fraud Risk for the last `days` days,
    optimised for the Dashboard "Audit Pulse" widget. Returns only what the
    widget needs — no section drill-downs, no per-invoice detail.
    """
    today = datetime.now(timezone.utc).date()
    # Clamp days: must be ≥1, capped at 365 to prevent runaway compute windows.
    days = max(1, min(int(days), 365))
    period_from = (today - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    period_to = today.strftime("%Y-%m-%d")

    full = await compute_audit(
        branch_id=branch_id,
        period_from=period_from,
        period_to=period_to,
        audit_type="partial",
        user=user,
    )

    scores = full.get("scores") or {}
    kpis = full.get("kpis") or {}

    # Top 3 risk factors by points (only those with >0 points)
    top_risks = [r for r in (scores.get("risk_breakdown") or []) if (r.get("points") or 0) > 0]
    top_risks.sort(key=lambda r: r.get("points", 0), reverse=True)
    top_risks = top_risks[:3]

    return {
        "period_from": period_from,
        "period_to": period_to,
        "days": days,
        "branch_id": branch_id or full.get("branch_id"),
        "health_score": scores.get("health_score"),
        "health_label": scores.get("health_label"),
        "fraud_risk_score": scores.get("fraud_risk_score"),
        "fraud_risk_label": scores.get("fraud_risk_label"),
        "top_risk_factors": top_risks,
        "kpis": {
            "revenue": kpis.get("revenue"),
            "gross_margin_pct": kpis.get("gross_margin_pct"),
            "void_rate_pct": kpis.get("void_rate_pct"),
            "discount_rate_pct": kpis.get("discount_rate_pct"),
            "dso_days": kpis.get("dso_days"),
            "dpo_days": kpis.get("dpo_days"),
            "inventory_turnover": kpis.get("inventory_turnover"),
            "total_txns": kpis.get("total_txns"),
            "voided_count": kpis.get("voided_count"),
            "trend": kpis.get("trend"),
        },
        "computed_at": full.get("computed_at"),
    }


# ── Section helpers ────────────────────────────────────────────────────────

async def _compute_security(period_from: str, period_to: str) -> dict:
    """
    Pull failed-PIN brute-force events within the audit period.
    These are always flagged — no acceptable threshold for repeated wrong PIN attempts.
    """
    from_dt = period_from + "T00:00:00"
    to_dt   = period_to   + "T23:59:59"

    events = await db.security_events.find(
        {"created_at": {"$gte": from_dt, "$lte": to_dt}},
        {"_id": 0}
    ).sort("created_at", -1).to_list(500)

    high   = [e for e in events if e.get("severity") == "high"]
    medium = [e for e in events if e.get("severity") == "medium"]

    flag_text = None
    detail_lines = []
    if events:
        flag_text = (
            f"{len(events)} security flag(s): "
            f"{len(high)} HIGH severity, {len(medium)} medium — "
            f"repeated wrong PIN attempts by employees"
        )
        seen_users = {}
        for e in events:
            uid = e.get("user_id")
            if uid not in seen_users:
                seen_users[uid] = {"name": e.get("user_name","?"), "count": 0, "max": 0}
            seen_users[uid]["count"] += 1
            seen_users[uid]["max"] = max(seen_users[uid]["max"], e.get("failure_count", 0))
        for uid, info in seen_users.items():
            detail_lines.append(
                f"{info['name']}: {info['count']} alert(s), up to {info['max']} consecutive wrong PINs per incident"
            )

    return {
        "total_events":   len(events),
        "high_severity":  len(high),
        "medium_severity": len(medium),
        "events":         events,
        "flag":           flag_text,
        "detail":         detail_lines,
        "status":         "critical" if high else ("warning" if medium else "ok"),
    }


@router.get("/security-flags")
async def list_security_flags(
    period_from: Optional[str] = None,
    period_to:   Optional[str] = None,
    user=Depends(get_current_user),
):
    """List all security events (failed PIN brute-force) for the audit trail."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    from_dt = (period_from or "2020-01-01") + "T00:00:00"
    to_dt   = (period_to   or today)        + "T23:59:59"

    events = await db.security_events.find(
        {"created_at": {"$gte": from_dt, "$lte": to_dt}},
        {"_id": 0}
    ).sort("created_at", -1).to_list(500)

    # Enrich with attempt log count
    for e in events:
        window_end   = e["created_at"]
        window_start = (datetime.fromisoformat(window_end.replace("Z","")) - timedelta(minutes=30)).isoformat()
        e["total_attempts_in_window"] = await db.pin_attempt_log.count_documents({
            "user_id":      e["user_id"],
            "attempted_at": {"$gte": window_start, "$lte": window_end},
        })
        # acknowledged flag
        e["acknowledged"] = e.get("acknowledged", False)

    return {"events": events, "total": len(events)}


@router.post("/security-flags/{event_id}/acknowledge")
async def acknowledge_security_flag(event_id: str, data: dict, user=Depends(get_current_user)):
    """Mark a security event as reviewed/acknowledged by the admin."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    await db.security_events.update_one(
        {"id": event_id},
        {"$set": {
            "acknowledged":       True,
            "acknowledged_by":    user.get("full_name", user.get("username", "")),
            "acknowledged_at":    now_iso(),
            "acknowledgement_note": data.get("note", ""),
        }}
    )
    return {"message": "Acknowledged"}


# ── Section helpers ────────────────────────────────────────────────────────

async def _compute_inventory(branch_id: str, baseline_id: str, current_id: str) -> dict:
    """
    Compute inventory variance by comparing expected qty (from movement logs)
    against the physical count in the current count sheet.
    
    Formula: expected_qty = baseline.counted_qty + Σ(movements between sheets)
    Variance = current_sheet.counted_qty - expected_qty
    """
    baseline = await db.count_sheets.find_one({"id": baseline_id}, {"_id": 0})
    current = await db.count_sheets.find_one({"id": current_id}, {"_id": 0})

    if not baseline or not current:
        return {"available": False, "message": "Count sheets not found"}
    if baseline["status"] != "completed" or current["status"] != "completed":
        return {"available": False, "message": "Both count sheets must be completed"}

    baseline_date = baseline["completed_at"]
    current_date = current["completed_at"]

    # Build lookup: product_id → baseline counted qty
    baseline_map = {item["product_id"]: item for item in baseline.get("items", [])}

    items_result = []
    total_expected_value = 0
    total_variance_capital = 0
    total_variance_retail = 0
    items_ok = 0
    items_warning = 0
    items_critical = 0

    for item in current.get("items", []):
        pid = item["product_id"]
        physical_count = item.get("actual_quantity") or 0
        capital_price = item.get("capital_price", 0)
        retail_price = item.get("retail_price", 0)

        # Get baseline qty (from previous count sheet)
        baseline_item = baseline_map.get(pid)
        baseline_qty = baseline_item.get("actual_quantity", 0) if baseline_item else 0

        # Sum all movements between baseline and current count sheet
        movements = await db.movements.aggregate([
            {"$match": {
                "product_id": pid,
                "branch_id": branch_id,
                "created_at": {"$gte": baseline_date, "$lte": current_date}
            }},
            {"$group": {"_id": None, "total": {"$sum": "$quantity_change"}}}
        ]).to_list(1)
        net_movement = movements[0]["total"] if movements else 0.0

        expected_qty = round(baseline_qty + net_movement, 4)
        variance = round(physical_count - expected_qty, 4)
        variance_pct = round((variance / expected_qty * 100), 2) if expected_qty > 0 else 0
        variance_value_capital = round(variance * capital_price, 2)
        variance_value_retail = round(variance * retail_price, 2)

        sev = severity(variance_pct)
        if sev == "ok":
            items_ok += 1
        elif sev == "warning":
            items_warning += 1
        else:
            items_critical += 1

        total_expected_value += expected_qty * capital_price
        total_variance_capital += variance_value_capital
        total_variance_retail += variance_value_retail

        items_result.append({
            "product_id": pid,
            "product_name": item.get("product_name", ""),
            "sku": item.get("sku", ""),
            "category": item.get("category", ""),
            "unit": item.get("unit", ""),
            "baseline_qty": baseline_qty,
            "net_movement": round(net_movement, 4),
            "expected_qty": expected_qty,
            "physical_count": physical_count,
            "variance": variance,
            "variance_pct": variance_pct,
            "capital_price": capital_price,
            "retail_price": retail_price,
            "variance_value_capital": variance_value_capital,
            "variance_value_retail": variance_value_retail,
            "severity": sev,
        })

    # Sort by severity (critical first)
    sev_order = {"critical": 0, "warning": 1, "ok": 2}
    items_result.sort(key=lambda x: (sev_order[x["severity"]], -abs(x["variance_value_capital"])))

    inventory_accuracy = round(items_ok / len(items_result) * 100, 1) if items_result else 100

    return {
        "available": True,
        "baseline_id": baseline_id,
        "current_id": current_id,
        "baseline_date": baseline_date[:10],
        "current_date": current_date[:10],
        "items": items_result,
        "summary": {
            "total_products": len(items_result),
            "items_ok": items_ok,
            "items_warning": items_warning,
            "items_critical": items_critical,
            "total_expected_value": round(total_expected_value, 2),
            "total_variance_capital": round(total_variance_capital, 2),
            "total_variance_retail": round(total_variance_retail, 2),
            "inventory_accuracy_pct": inventory_accuracy,
        },
        "severity": "critical" if items_critical > 0 else ("warning" if items_warning > 0 else "ok"),
    }


async def _compute_cash(branch_id: str, date_from: str, date_to: str) -> dict:
    """
    Compute cash reconciliation for the period.
    Formula matches Closing Wizard: starting_float + cash_in + net_fund_transfers - cashier_expenses
    Where cash_in = cash_sales + partial_cash + split_cash + cash_ar
    """
    # Starting float: last daily_closing before date_from
    prev_close = await db.daily_closings.find_one(
        {"branch_id": branch_id, "date": {"$lt": date_from}},
        {"_id": 0},
        sort=[("date", -1)]
    )
    has_prev_close = bool(prev_close)
    starting_float = float(prev_close.get("cash_to_drawer", 0)) if prev_close else 0.0
    if starting_float == 0 and not prev_close:
        wallet = await db.fund_wallets.find_one({"branch_id": branch_id, "type": "cashier", "active": True}, {"_id": 0})
        starting_float = float(wallet.get("balance", 0)) if wallet else 0.0

    # ── Cash sales (pure cash from sales_log) ─────────────────────────────
    cash_sales_r = await db.sales_log.aggregate([
        {"$match": {"branch_id": branch_id, "date": {"$gte": date_from, "$lte": date_to},
                    "voided": {"$ne": True},
                    "payment_method": {"$regex": "^cash$", "$options": "i"}}},
        {"$group": {"_id": None, "total": {"$sum": "$line_total"}}}
    ]).to_list(1)
    cash_sales = round(cash_sales_r[0]["total"] if cash_sales_r else 0, 2)

    # ── Partial payment cash (amount_paid from partial invoices) ──────────
    partial_invoices = await db.invoices.find(
        {"branch_id": branch_id, "order_date": {"$gte": date_from, "$lte": date_to},
         "payment_type": "partial", "status": {"$ne": "voided"}},
        {"_id": 0, "customer_name": 1, "invoice_number": 1, "amount_paid": 1, "grand_total": 1}
    ).to_list(500)
    total_partial_cash = round(sum(float(inv.get("amount_paid", 0)) for inv in partial_invoices), 2)

    # ── Split payment cash portions ───────────────────────────────────────
    split_invoices = await db.invoices.find(
        {"branch_id": branch_id, "order_date": {"$gte": date_from, "$lte": date_to},
         "fund_source": "split", "status": {"$ne": "voided"}},
        {"_id": 0, "cash_amount": 1, "invoice_number": 1, "customer_name": 1, "grand_total": 1, "digital_amount": 1}
    ).to_list(500)
    total_split_cash = round(sum(float(inv.get("cash_amount", 0)) for inv in split_invoices), 2)

    # ── AR payments — split by fund source (cash vs digital) ──────────────
    # Exclude voided payments (modify/void cycles would otherwise double-count).
    ar_pipeline = [
        {"$match": {"branch_id": branch_id, "status": {"$ne": "voided"}}},
        {"$unwind": "$payments"},
        {"$match": {
            "payments.date": {"$gte": date_from, "$lte": date_to},
            "payments.voided": {"$ne": True},
        }},
        {"$project": {
            "_id": 0, "customer_name": 1, "invoice_number": 1, "customer_id": 1, "balance": 1,
            "payment": "$payments"
        }}
    ]
    ar_payments_raw = await db.invoices.aggregate(ar_pipeline).to_list(500)
    ar_payments = []
    for p in ar_payments_raw:
        pmt = p.get("payment", {})
        amount = float(pmt.get("amount", 0))
        ar_payments.append({
            "customer_name": p.get("customer_name", ""),
            "invoice_number": p.get("invoice_number", ""),
            "amount_paid": round(amount, 2),
            "fund_source": pmt.get("fund_source", "cashier"),
            "method": pmt.get("method", "Cash"),
            "date": pmt.get("date", ""),
        })
    total_ar_received = round(sum(p["amount_paid"] for p in ar_payments), 2)
    total_cash_ar = round(sum(
        p["amount_paid"] for p in ar_payments if p.get("fund_source", "cashier") == "cashier"
    ), 2)
    total_digital_ar = round(total_ar_received - total_cash_ar, 2)

    # ── Fund transfers affecting cashier drawer ───────────────────────────
    ft_query = {"branch_id": branch_id, "$or": [
        {"date": {"$gte": date_from, "$lte": date_to}},
        {"date": {"$exists": False}, "created_at": {"$gte": f"{date_from}T00:00:00", "$lte": f"{date_to}T23:59:59"}}
    ]}
    fund_transfers = await db.fund_transfers.find(ft_query, {"_id": 0}).to_list(500)
    capital_to_cashier = round(sum(
        float(ft.get("amount", 0)) for ft in fund_transfers
        if ft.get("transfer_type") == "capital_add" and ft.get("target_wallet", "cashier") == "cashier"
    ), 2)
    safe_to_cashier = round(sum(
        float(ft.get("amount", 0)) for ft in fund_transfers
        if ft.get("transfer_type") == "safe_to_cashier"
    ), 2)
    cashier_to_safe = round(sum(
        float(ft.get("amount", 0)) for ft in fund_transfers
        if ft.get("transfer_type") == "cashier_to_safe"
    ), 2)
    net_fund_transfers = round(capital_to_cashier + safe_to_cashier - cashier_to_safe, 2)

    fund_transfer_details = [
        {"type": ft["transfer_type"], "amount": float(ft.get("amount", 0)),
         "note": ft.get("note", ""), "authorized_by": ft.get("authorized_by", ""),
         "target_wallet": ft.get("target_wallet", ""),
         "date": ft.get("date", ft.get("created_at", "")[:10])}
        for ft in fund_transfers
        if ft.get("transfer_type") in ("capital_add", "safe_to_cashier", "cashier_to_safe")
    ]

    # ── Expenses — full list with verification status ─────────────────────
    expenses_raw = await db.expenses.find(
        {"branch_id": branch_id, "date": {"$gte": date_from, "$lte": date_to},
         "voided": {"$ne": True}}, {"_id": 0}
    ).sort("date", -1).to_list(500)

    # Enrich expenses with receipt info
    expense_ids = [e["id"] for e in expenses_raw]
    expense_uploads = {}
    if expense_ids:
        upload_sessions = await db.upload_sessions.find(
            {"record_type": "expense", "record_id": {"$in": expense_ids}, "is_pending": {"$ne": True}},
            {"_id": 0, "record_id": 1, "file_count": 1, "files": 1}
        ).to_list(500)
        for s in upload_sessions:
            rid = s["record_id"]
            if rid not in expense_uploads:
                expense_uploads[rid] = []
            expense_uploads[rid].extend(s.get("files", []))

    expenses = []
    for e in expenses_raw:
        files = expense_uploads.get(e["id"], [])
        expenses.append({
            "id": e["id"],
            "category": e.get("category", ""),
            "description": e.get("description", ""),
            "reference_number": e.get("reference_number", ""),
            "amount": float(e.get("amount", 0)),
            "fund_source": e.get("fund_source", "cashier"),
            "date": e.get("date", ""),
            "employee_name": e.get("employee_name", ""),
            "created_by_name": e.get("created_by_name", ""),
            "verified": e.get("verified", False),
            "verified_by_name": e.get("verified_by_name", ""),
            "verified_at": e.get("verified_at", ""),
            "receipt_review_status": e.get("receipt_review_status", ""),
            "has_receipt": len(files) > 0,
            "receipt_count": len(files),
        })

    total_expenses = round(sum(e["amount"] for e in expenses), 2)
    total_cashier_expenses = round(sum(
        e["amount"] for e in expenses if e.get("fund_source", "cashier") != "safe"
    ), 2)
    total_safe_expenses = round(total_expenses - total_cashier_expenses, 2)

    # Expense breakdown by category
    exp_breakdown = await db.expenses.aggregate([
        {"$match": {"branch_id": branch_id, "date": {"$gte": date_from, "$lte": date_to},
                    "voided": {"$ne": True}}},
        {"$group": {"_id": "$category", "total": {"$sum": "$amount"}, "count": {"$sum": 1}}},
        {"$sort": {"total": -1}}
    ]).to_list(50)

    # ── Expected cash (matches Closing Wizard formula) ────────────────────
    total_cash_in = cash_sales + total_partial_cash + total_cash_ar + total_split_cash

    # Current cashier balance (real-time truth)
    cashier = await db.fund_wallets.find_one({"branch_id": branch_id, "type": "cashier", "active": True}, {"_id": 0})
    current_cashier = float(cashier.get("balance", 0)) if cashier else 0.0

    if has_prev_close:
        expected_cash = round(starting_float + total_cash_in + net_fund_transfers - total_cashier_expenses, 2)
    else:
        # No previous close: wallet balance is real-time truth.
        # Reverse-calculate the starting float so the formula adds up correctly:
        # starting_float = wallet_balance - cash_in - net_fund_transfers + cashier_expenses
        expected_cash = round(current_cashier, 2)
        starting_float = round(current_cashier - total_cash_in - net_fund_transfers + total_cashier_expenses, 2)

    # Safe balance
    safe = await db.fund_wallets.find_one({"branch_id": branch_id, "type": "safe", "active": True}, {"_id": 0})
    safe_balance = 0.0
    if safe:
        lots = await db.safe_lots.find({"wallet_id": safe["id"], "remaining_amount": {"$gt": 0}}, {"_id": 0}).to_list(500)
        safe_balance = sum(lot["remaining_amount"] for lot in lots)

    discrepancy = round(current_cashier - expected_cash, 2)
    sev = cash_severity(discrepancy)

    return {
        "starting_float": starting_float,
        "has_prev_close": has_prev_close,
        "cash_sales": cash_sales,
        "total_partial_cash": total_partial_cash,
        "total_split_cash": total_split_cash,
        "total_cash_in": round(total_cash_in, 2),
        "ar_collected": total_cash_ar,
        "total_ar_received": total_ar_received,
        "total_cash_ar": total_cash_ar,
        "total_digital_ar": total_digital_ar,
        "ar_payments": ar_payments[:50],
        "net_fund_transfers": net_fund_transfers,
        "capital_to_cashier": capital_to_cashier,
        "safe_to_cashier": safe_to_cashier,
        "cashier_to_safe": cashier_to_safe,
        "fund_transfer_details": fund_transfer_details,
        "total_expenses": total_expenses,
        "total_cashier_expenses": total_cashier_expenses,
        "total_safe_expenses": total_safe_expenses,
        "expenses": expenses[:100],
        "expected_cash": expected_cash,
        "current_cashier_balance": round(current_cashier, 2),
        "safe_balance": round(safe_balance, 2),
        "total_funds": round(current_cashier + safe_balance, 2),
        "discrepancy": discrepancy,
        "discrepancy_type": "over" if discrepancy > 0 else ("short" if discrepancy < 0 else "balanced"),
        # Wallet movements cross-check (Fix #6) — independent reconciliation
        "wallet_movements_check": await _wallet_movements_reconciliation(
            branch_id, date_from, date_to, starting_float, current_cashier
        ),
        "expense_breakdown": [{"category": r["_id"] or "Other", "total": round(r["total"], 2), "count": r["count"]} for r in exp_breakdown],
        "severity": sev,
        "formula": "Starting Float + Cash Sales + Partial Cash + Split Cash + Cash AR + Net Fund Transfers - Cashier Expenses = Expected Cash",
        # Partial / split invoice details for drill-down
        "partial_invoices": [
            {"invoice_number": inv["invoice_number"], "customer_name": inv.get("customer_name", ""),
             "amount_paid": round(float(inv.get("amount_paid", 0)), 2), "grand_total": round(float(inv.get("grand_total", 0)), 2)}
            for inv in partial_invoices
        ],
        "split_invoices": [
            {"invoice_number": inv["invoice_number"], "customer_name": inv.get("customer_name", ""),
             "cash_amount": round(float(inv.get("cash_amount", 0)), 2), "digital_amount": round(float(inv.get("digital_amount", 0)), 2),
             "grand_total": round(float(inv.get("grand_total", 0)), 2)}
            for inv in split_invoices
        ],
    }


async def _wallet_movements_reconciliation(branch_id: str, date_from: str, date_to: str,
                                           starting_float: float, current_cashier: float) -> dict:
    """
    Independent cross-check of cashier cash using wallet_movements (Fix #6).

    Sums all cashier wallet_movements in the period and verifies:
        starting_float + sum(wallet_movements) ≈ current_cashier_balance

    If this agrees, the computed cash formula in _compute_cash has no drift.
    If NOT, something (sale/void/expense) changed the cashier wallet without
    a matching movement log — an integrity issue.
    """
    cashier_wallet = await db.fund_wallets.find_one(
        {"branch_id": branch_id, "type": "cashier", "active": True}, {"_id": 0, "id": 1}
    )
    if not cashier_wallet:
        return {"supported": False, "reason": "no_cashier_wallet"}

    # Sum wallet_movements for cashier in period
    agg = await db.wallet_movements.aggregate([
        {"$match": {
            "wallet_id": cashier_wallet["id"],
            "created_at": {"$gte": f"{date_from}T00:00:00", "$lte": f"{date_to}T23:59:59.999"},
        }},
        {"$group": {"_id": None, "net": {"$sum": "$amount"}, "count": {"$sum": 1}}}
    ]).to_list(1)
    wallet_net = round(agg[0]["net"] if agg else 0, 2)
    movement_count = agg[0]["count"] if agg else 0

    expected_from_movements = round(starting_float + wallet_net, 2)
    variance = round(current_cashier - expected_from_movements, 2)
    reconciled = abs(variance) < 1.00

    return {
        "supported": True,
        "starting_float": round(starting_float, 2),
        "net_wallet_movements": wallet_net,
        "movement_count": movement_count,
        "expected_from_movements": expected_from_movements,
        "current_cashier_balance": round(current_cashier, 2),
        "variance": variance,
        "reconciled": reconciled,
        "note": "Compares wallet_movements ledger to actual cashier balance. Variance > ₱1 indicates an unlogged change.",
    }



async def _compute_sales(branch_id: str, date_from: str, date_to: str) -> dict:
    """Sales audit: totals, overrides, voided/edited transactions.
    Reconciliation (Fix #3): also returns freight, overall_discount, and
    sum-of-line-totals so auditors can verify:
        grand_total_sales == sum_line_totals + freight - overall_discount
    (within rounding). Any variance flags an integrity issue."""
    # Total invoices in period
    inv_r = await db.invoices.aggregate([
        {"$match": {"branch_id": branch_id, "order_date": {"$gte": date_from, "$lte": date_to},
                    "status": {"$ne": "voided"}}},
        {"$group": {
            "_id": "$payment_type",
            "total": {"$sum": "$grand_total"},
            "count": {"$sum": 1},
            "total_paid": {"$sum": "$amount_paid"},
            "total_balance": {"$sum": "$balance"},
            "freight": {"$sum": "$freight"},
            "overall_discount": {"$sum": "$overall_discount"},
        }}
    ]).to_list(10)

    by_type = {r["_id"] or "cash": {"total": round(r["total"], 2), "count": r["count"]} for r in inv_r}
    grand_total_sales = round(sum(v["total"] for v in by_type.values()), 2)
    total_freight = round(sum(r.get("freight", 0) or 0 for r in inv_r), 2)
    total_overall_disc = round(sum(r.get("overall_discount", 0) or 0 for r in inv_r), 2)

    # Cross-check: sum of sales_log line_totals (non-voided) should equal
    # grand_total_sales - freight + overall_discount.
    sl_sum_r = await db.sales_log.aggregate([
        {"$match": {"branch_id": branch_id, "date": {"$gte": date_from, "$lte": date_to},
                    "voided": {"$ne": True}}},
        {"$group": {"_id": None, "total": {"$sum": "$line_total"}}}
    ]).to_list(1)
    sum_line_totals = round(sl_sum_r[0]["total"], 2) if sl_sum_r else 0.0

    # Expected from invoice side: sum_line_totals should ≈ grand_total - freight + overall_discount
    expected_line_totals = round(grand_total_sales - total_freight + total_overall_disc, 2)
    variance = round(sum_line_totals - expected_line_totals, 2)
    # Allow tiny rounding noise (centavo drift acceptable)
    reconciled = abs(variance) < 1.00

    # Decompose partial into cash_received + credit_balance for clarity
    partial_cash_received = 0
    partial_credit_balance = 0
    for r in inv_r:
        if r["_id"] == "partial":
            partial_cash_received = round(r.get("total_paid", 0), 2)
            partial_credit_balance = round(r.get("total_balance", 0), 2)

    # Voided transactions
    voided = await db.invoices.count_documents({
        "branch_id": branch_id, "order_date": {"$gte": date_from, "$lte": date_to}, "status": "voided"
    })

    # Edited invoices (audit trail)
    edited_r = await db.invoice_edits.find(
        {"edited_at": {"$gte": f"{date_from}T00:00:00", "$lte": f"{date_to}T23:59:59"}},
        {"_id": 0}
    ).to_list(100)

    # Price overrides (items sold below cost — fetched from movements)
    # Simplified: invoices where any item total < item qty * cost
    below_cost_count = 0

    total_txns = sum(v["count"] for v in by_type.values())
    sev = "warning" if (voided > 0 or len(edited_r) > 0 or not reconciled) else "ok"

    return {
        "grand_total_sales": grand_total_sales,
        "total_transactions": total_txns,
        "by_payment_type": by_type,
        "partial_cash_received": partial_cash_received,
        "partial_credit_balance": partial_credit_balance,
        "voided_count": voided,
        "edited_invoices": edited_r[:20],
        "edited_count": len(edited_r),
        # Reconciliation breakdown (Fix #3) — so owner can see all three views agree
        "reconciliation": {
            "grand_total_invoices": grand_total_sales,       # from invoices (Reports view)
            "sum_line_totals": sum_line_totals,              # from sales_log (Z-report view)
            "total_freight": total_freight,
            "total_overall_discount": total_overall_disc,
            "expected_line_totals": expected_line_totals,    # = grand - freight + disc
            "variance": variance,
            "reconciled": reconciled,
            "formula": "grand_total = sum_line_totals + freight - overall_discount",
        },
        "severity": sev,
    }


async def _compute_ar(branch_id: str, date_from: str, date_to: str) -> dict:
    """AR audit: aging, collections efficiency."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    open_inv = await db.invoices.find(
        {"branch_id": branch_id, "balance": {"$gt": 0}, "status": {"$nin": ["voided", "paid"]},
         "customer_id": {"$ne": None}},
        {"_id": 0, "balance": 1, "invoice_date": 1, "customer_name": 1, "invoice_number": 1}
    ).to_list(1000)

    total_ar = round(sum(float(i.get("balance", 0)) for i in open_inv), 2)

    buckets = {"current": 0, "b31_60": 0, "b61_90": 0, "b90plus": 0}
    today_dt = datetime.strptime(today, "%Y-%m-%d").date()
    for inv in open_inv:
        ds = (inv.get("invoice_date") or today)[:10]
        try:
            days = (today_dt - datetime.strptime(ds, "%Y-%m-%d").date()).days
        except ValueError:
            days = 0
        if days <= 30:
            buckets["current"] += float(inv.get("balance", 0))
        elif days <= 60:
            buckets["b31_60"] += float(inv.get("balance", 0))
        elif days <= 90:
            buckets["b61_90"] += float(inv.get("balance", 0))
        else:
            buckets["b90plus"] += float(inv.get("balance", 0))

    # Collections in period (excludes voided payments)
    coll_r = await db.invoices.aggregate([
        {"$unwind": "$payments"},
        {"$match": {
            "branch_id": branch_id,
            "payments.date": {"$gte": date_from, "$lte": date_to},
            "payments.voided": {"$ne": True},
        }},
        {"$group": {"_id": None, "total": {"$sum": "$payments.amount"}}}
    ]).to_list(1)
    collected = round(coll_r[0]["total"] if coll_r else 0, 2)

    sev = "critical" if buckets["b90plus"] > 0 else ("warning" if buckets["b61_90"] > 0 else "ok")

    return {
        "total_outstanding_ar": total_ar,
        "open_invoices_count": len(open_inv),
        "aging": {k: round(v, 2) for k, v in buckets.items()},
        "collected_in_period": collected,
        "severity": sev,
    }


async def _compute_payables(branch_id: str, date_from: str, date_to: str) -> dict:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    unpaid = await db.purchase_orders.find(
        {"branch_id": branch_id, "payment_status": {"$in": ["unpaid", "partial"]}, "status": {"$ne": "cancelled"}},
        {"_id": 0, "id": 1, "po_number": 1, "vendor": 1, "balance": 1, "grand_total": 1, "subtotal": 1,
         "due_date": 1, "purchase_date": 1, "payment_status": 1, "verified": 1, "verified_by_name": 1}
    ).to_list(500)

    total_ap = sum(float(p.get("balance") or p.get("grand_total") or p.get("subtotal", 0)) for p in unpaid)
    overdue = [p for p in unpaid if p.get("due_date") and p["due_date"] < today]

    # Build PO detail list for drill-down
    po_details = []
    for p in unpaid:
        bal = float(p.get("balance") or p.get("grand_total") or p.get("subtotal", 0))
        is_overdue = bool(p.get("due_date") and p["due_date"] < today)
        po_details.append({
            "id": p.get("id", ""),
            "po_number": p.get("po_number", ""),
            "vendor": p.get("vendor", ""),
            "balance": round(bal, 2),
            "grand_total": round(float(p.get("grand_total") or p.get("subtotal", 0)), 2),
            "due_date": p.get("due_date", ""),
            "purchase_date": p.get("purchase_date", ""),
            "payment_status": p.get("payment_status", ""),
            "is_overdue": is_overdue,
            "verified": p.get("verified", False),
        })
    po_details.sort(key=lambda x: (not x["is_overdue"], x.get("due_date", "9999")))

    return {
        "total_outstanding_ap": round(total_ap, 2),
        "unpaid_po_count": len(unpaid),
        "overdue_count": len(overdue),
        "overdue_value": round(sum(float(p.get("balance") or p.get("grand_total") or 0) for p in overdue), 2),
        "po_details": po_details[:50],
        "severity": "critical" if overdue else ("warning" if total_ap > 0 else "ok"),
    }


async def _compute_transfers(branch_id: str, date_from: str, date_to: str) -> dict:
    """Transfers audit: variances, pending requests."""
    query = {
        "$or": [{"from_branch_id": branch_id}, {"to_branch_id": branch_id}],
        "created_at": {"$gte": f"{date_from}T00:00:00", "$lte": f"{date_to}T23:59:59"}
    }
    transfers = await db.branch_transfer_orders.find(query, {"_id": 0}).to_list(500)

    total_received = len([t for t in transfers if t["status"] == "received"])
    with_shortage = len([t for t in transfers if t.get("has_shortage")])
    with_excess = len([t for t in transfers if t.get("has_excess")])
    pending = len([t for t in transfers if t["status"] in ("sent", "received_pending", "disputed")])

    # Pending stock requests directed to this branch
    requests = await db.purchase_orders.count_documents({
        "po_type": "branch_request", "supply_branch_id": branch_id, "status": "requested"
    })

    shortage_value = 0
    for t in transfers:
        if t.get("shortages"):
            shortage_value += sum(s.get("capital_variance", 0) for s in t["shortages"])

    return {
        "total_transfers": len(transfers),
        "received_count": total_received,
        "with_shortage": with_shortage,
        "with_excess": with_excess,
        "pending_count": pending,
        "pending_requests": requests,
        "total_shortage_value": round(shortage_value, 2),
        "severity": "critical" if with_shortage > 0 else ("warning" if pending > 0 or requests > 0 else "ok"),
    }


async def _compute_returns(branch_id: str, date_from: str, date_to: str) -> dict:
    returns = await db.returns.find(
        {"branch_id": branch_id, "return_date": {"$gte": date_from, "$lte": date_to}},
        {"_id": 0}
    ).to_list(500)

    total_refunded = sum(float(r.get("refund_amount", 0)) for r in returns)
    total_loss = sum(float(r.get("total_loss_value", 0)) for r in returns)
    pullout_count = len([r for r in returns if r.get("has_pullout")])

    reasons = {}
    for r in returns:
        reasons[r.get("reason", "Other")] = reasons.get(r.get("reason", "Other"), 0) + 1

    return {
        "total_returns": len(returns),
        "total_refunded": round(total_refunded, 2),
        "total_loss_value": round(total_loss, 2),
        "pullout_count": pullout_count,
        "top_reasons": sorted([{"reason": k, "count": v} for k, v in reasons.items()], key=lambda x: -x["count"]),
        "severity": "warning" if pullout_count > 0 else "ok",
    }


async def _compute_digital(branch_id: str, date_from: str, date_to: str) -> dict:
    """
    Digital payment audit: total digital collected, by platform, with reference tracking.
    Compares against digital wallet balance for discrepancy detection.
    """
    # All digital invoices in period (pure digital + split)
    digital_invs = await db.invoices.find(
        {"branch_id": branch_id, "order_date": {"$gte": date_from, "$lte": date_to},
         "fund_source": {"$in": ["digital", "split"]}, "status": {"$ne": "voided"}},
        {"_id": 0, "invoice_number": 1, "customer_name": 1, "order_date": 1,
         "amount_paid": 1, "digital_amount": 1, "cash_amount": 1,
         "digital_platform": 1, "digital_ref_number": 1, "digital_sender": 1,
         "fund_source": 1, "grand_total": 1}
    ).to_list(1000)

    by_platform: dict = {}
    total_digital = 0.0
    missing_ref = 0
    transactions = []

    for inv in digital_invs:
        is_split = inv.get("fund_source") == "split"
        digital_amt = float(inv.get("digital_amount", 0)) if is_split and inv.get("digital_amount") else float(inv.get("amount_paid", 0))
        platform = inv.get("digital_platform", "Digital") or "Digital"
        ref = inv.get("digital_ref_number", "")
        by_platform[platform] = round(by_platform.get(platform, 0) + digital_amt, 2)
        total_digital = round(total_digital + digital_amt, 2)
        if not ref:
            missing_ref += 1
        transactions.append({
            "invoice_number": inv.get("invoice_number"),
            "customer_name": inv.get("customer_name"),
            "date": inv.get("order_date"),
            "platform": platform,
            "ref_number": ref,
            "sender": inv.get("digital_sender", ""),
            "amount": digital_amt,
            "is_split": is_split,
            "has_ref": bool(ref),
        })

    # Compare against digital wallet balance
    digital_wallet = await db.fund_wallets.find_one(
        {"branch_id": branch_id, "type": "digital", "active": True}, {"_id": 0}
    )
    wallet_balance = float(digital_wallet.get("balance", 0)) if digital_wallet else 0.0

    sev = "critical" if missing_ref > 0 else "ok"

    return {
        "total_digital_collected": round(total_digital, 2),
        "by_platform": by_platform,
        "transaction_count": len(digital_invs),
        "missing_ref_count": missing_ref,
        "digital_wallet_balance": round(wallet_balance, 2),
        "transactions": sorted(transactions, key=lambda x: x.get("date", ""), reverse=True)[:50],
        "severity": sev,
    }


async def _compute_activity(branch_id: str, date_from: str, date_to: str) -> dict:
    """User activity audit: transactions by user, corrections, overrides, discounts."""
    # Sales by cashier in period
    sales_by_user = await db.invoices.aggregate([
        {"$match": {"branch_id": branch_id, "order_date": {"$gte": date_from, "$lte": date_to},
                    "status": {"$ne": "voided"}}},
        {"$group": {"_id": "$cashier_name", "total": {"$sum": "$grand_total"}, "count": {"$sum": 1}}},
        {"$sort": {"count": -1}}
    ]).to_list(20)

    # Inventory corrections in period
    corrections = await db.inventory_corrections.find(
        {"branch_id": branch_id, "created_at": {"$gte": f"{date_from}T00:00:00", "$lte": f"{date_to}T23:59:59"}},
        {"_id": 0}
    ).to_list(100)

    # Invoice edits
    edits = await db.invoice_edits.find(
        {"edited_at": {"$gte": f"{date_from}T00:00:00", "$lte": f"{date_to}T23:59:59"}},
        {"_id": 0}
    ).to_list(100)

    # Transactions outside business hours (before 7am or after 10pm)
    off_hours = await db.invoices.find(
        {"branch_id": branch_id, "order_date": {"$gte": date_from, "$lte": date_to},
         "$or": [
             {"created_at": {"$regex": "T0[0-6]"}},
             {"created_at": {"$regex": "T2[2-3]"}},
         ]},
        {"_id": 0, "invoice_number": 1, "cashier_name": 1, "grand_total": 1, "created_at": 1}
    ).to_list(50)

    # ── Discount & Price Override activity ────────────────────────────────────
    discount_query = {"date": {"$gte": date_from, "$lte": date_to}}
    if branch_id:
        discount_query["branch_id"] = branch_id
    discount_logs = await db.discount_audit_log.find(
        {**discount_query, "invoice_voided": {"$ne": True}}, {"_id": 0}
    ).to_list(500)

    total_discount_amount = round(sum(d.get("total_discount", 0) for d in discount_logs), 2)
    total_price_override = round(sum(d.get("total_price_override_diff", 0) for d in discount_logs), 2)
    discount_count = len(discount_logs)

    # Top discounters by cashier
    cashier_discounts = {}
    for d in discount_logs:
        cn = d.get("cashier_name", "Unknown")
        if cn not in cashier_discounts:
            cashier_discounts[cn] = {"name": cn, "total": 0, "count": 0}
        cashier_discounts[cn]["total"] += d.get("total_discount", 0)
        cashier_discounts[cn]["count"] += 1
    top_discounters = sorted(cashier_discounts.values(), key=lambda x: x["total"], reverse=True)[:5]
    for td in top_discounters:
        td["total"] = round(td["total"], 2)

    # Top discount-receiving customers
    customer_discounts = {}
    for d in discount_logs:
        cn = d.get("customer_name", "Walk-in")
        if cn not in customer_discounts:
            customer_discounts[cn] = {"name": cn, "total": 0, "count": 0}
        customer_discounts[cn]["total"] += d.get("total_discount", 0)
        customer_discounts[cn]["count"] += 1
    top_customers = sorted(customer_discounts.values(), key=lambda x: x["total"], reverse=True)[:5]
    for tc in top_customers:
        tc["total"] = round(tc["total"], 2)

    # ── Price Match (permanent branch price changes) activity ─────────────────
    price_change_query = {"date": {"$gte": date_from, "$lte": date_to}}
    if branch_id:
        price_change_query["branch_id"] = branch_id
    price_change_logs = await db.price_change_log.find(
        {**price_change_query, "invoice_voided": {"$ne": True}}, {"_id": 0}
    ).to_list(500)
    price_change_count = len(price_change_logs)
    total_price_drop = round(sum(
        (pc.get("old_price", 0) - pc.get("new_price", 0))
        for pc in price_change_logs if pc.get("new_price", 0) < pc.get("old_price", 0)
    ), 2)
    # Top price-matchers by cashier
    matcher_counts = {}
    for pc in price_change_logs:
        cn = pc.get("cashier_name", "Unknown")
        if cn not in matcher_counts:
            matcher_counts[cn] = {"name": cn, "count": 0, "total_drop": 0}
        matcher_counts[cn]["count"] += 1
        if pc.get("new_price", 0) < pc.get("old_price", 0):
            matcher_counts[cn]["total_drop"] += pc["old_price"] - pc["new_price"]
    top_matchers = sorted(matcher_counts.values(), key=lambda x: x["total_drop"], reverse=True)[:5]
    for tm in top_matchers:
        tm["total_drop"] = round(tm["total_drop"], 2)

    flags = len(corrections) + len(edits) + len(off_hours) + discount_count + price_change_count
    sev = "critical" if flags > 10 else ("warning" if flags > 0 else "ok")

    return {
        "sales_by_user": [{"user": r["_id"] or "Unknown", "total": round(r["total"], 2), "count": r["count"]} for r in sales_by_user],
        "inventory_corrections_count": len(corrections),
        "inventory_corrections": corrections[:20],
        "invoice_edits_count": len(edits),
        "invoice_edits": edits[:20],
        "off_hours_transactions": off_hours[:20],
        "off_hours_count": len(off_hours),
        "discount_count": discount_count,
        "total_discount_amount": total_discount_amount,
        "total_price_override": total_price_override,
        "top_discounters": top_discounters,
        "top_discount_customers": top_customers,
        # New: POS Price Match activity (permanent branch price changes)
        "price_change_count": price_change_count,
        "total_price_drop": total_price_drop,
        "top_price_matchers": top_matchers,
        "severity": sev,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  AUDIT OFFLINE PACKAGE
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/offline-package")
async def get_offline_package(
    branch_id: Optional[str] = None,
    period_from: Optional[str] = None,
    period_to: Optional[str] = None,
    user=Depends(get_current_user),
):
    """
    Returns all transactions + file metadata for a branch/period.
    Period auto-detected from last two completed count sheets if not provided.
    Used by frontend to cache data for offline audit.
    """
    b_id = branch_id or user.get("branch_id", "")

    # Auto-detect period from count sheets
    auto_detected = False
    if not period_from or not period_to:
        cs_list = await db.count_sheets.find(
            {"branch_id": b_id, "status": "completed"},
            {"_id": 0, "id": 1, "count_sheet_number": 1, "completed_at": 1, "started_at": 1}
        ).sort("completed_at", -1).limit(2).to_list(2)

        if len(cs_list) >= 2:
            # Period = from oldest to newest
            dates = sorted([cs_list[0]["completed_at"][:10], cs_list[1]["completed_at"][:10]])
            period_from = period_from or dates[0]
            period_to = period_to or dates[1]
            auto_detected = True
            cs_refs = {
                "baseline": cs_list[1]["count_sheet_number"],
                "current": cs_list[0]["count_sheet_number"],
            }
        else:
            # Fallback: current month
            today = datetime.now(timezone.utc)
            period_from = period_from or today.strftime("%Y-%m-01")
            period_to = period_to or today.strftime("%Y-%m-%d")
            cs_refs = None
    else:
        cs_refs = None

    # Fetch POs in period
    pos = await db.purchase_orders.find(
        {
            "branch_id": b_id,
            "purchase_date": {"$gte": period_from, "$lte": period_to},
            "status": {"$nin": ["draft", "cancelled"]},
        },
        {"_id": 0, "items": 0, "change_log": 0}
    ).sort("purchase_date", -1).to_list(500)

    # Fetch Expenses in period
    expenses = await db.expenses.find(
        {"branch_id": b_id, "date": {"$gte": period_from, "$lte": period_to}},
        {"_id": 0}
    ).sort("date", -1).to_list(500)

    # Fetch Branch Transfers in period
    transfers = await db.branch_transfer_orders.find(
        {
            "$or": [{"from_branch_id": b_id}, {"to_branch_id": b_id}],
            "created_at": {"$gte": f"{period_from}T00:00:00", "$lte": f"{period_to}T23:59:59"},
        },
        {"_id": 0, "items": 0}
    ).sort("created_at", -1).to_list(500)

    # Collect all record IDs to fetch upload sessions
    all_ids = (
        [("purchase_order", p["id"]) for p in pos] +
        [("expense", e["id"]) for e in expenses] +
        [("branch_transfer", t["id"]) for t in transfers]
    )

    # Fetch upload sessions for all records
    uploads_map = {}
    for rec_type, rec_id in all_ids:
        sessions = await db.upload_sessions.find(
            {"record_type": rec_type, "record_id": rec_id},
            {"_id": 0, "token": 0}
        ).to_list(10)
        if sessions:
            uploads_map[rec_id] = sessions

    # Build file URL list for prefetching
    file_urls = []
    for rec_id, sessions in uploads_map.items():
        for session in sessions:
            for file in session.get("files", []):
                file_urls.append({
                    "record_id": rec_id,
                    "record_type": session.get("record_type"),
                    "file_id": file["id"],
                    "filename": file.get("filename"),
                    "content_type": file.get("content_type"),
                    "size": file.get("size", 0),
                })

    return {
        "branch_id": b_id,
        "period_from": period_from,
        "period_to": period_to,
        "auto_detected": auto_detected,
        "count_sheet_refs": cs_refs,
        "purchase_orders": pos,
        "expenses": expenses,
        "branch_transfers": transfers,
        "uploads_map": uploads_map,
        "file_urls": file_urls,
        "totals": {
            "purchase_orders": len(pos),
            "expenses": len(expenses),
            "branch_transfers": len(transfers),
            "total_files": len(file_urls),
        },
    }



async def _compute_unverified(branch_id: str, date_from: str, date_to: str) -> dict:
    """
    Find expenses, POs, and digital payments in the period that have NOT been
    verified by admin/auditor. Also flags items without receipts.
    """
    # ── Unverified expenses ───────────────────────────────────────────────
    unverified_expenses = await db.expenses.find(
        {"branch_id": branch_id, "date": {"$gte": date_from, "$lte": date_to},
         "voided": {"$ne": True}, "verified": {"$ne": True}},
        {"_id": 0, "id": 1, "category": 1, "description": 1, "amount": 1,
         "fund_source": 1, "date": 1, "employee_name": 1, "created_by_name": 1,
         "reference_number": 1}
    ).sort("amount", -1).to_list(500)

    unverified_ids = [e["id"] for e in unverified_expenses]
    has_receipt_set = set()
    if unverified_ids:
        upload_sessions = await db.upload_sessions.find(
            {"record_type": "expense", "record_id": {"$in": unverified_ids},
             "is_pending": {"$ne": True}, "file_count": {"$gt": 0}},
            {"_id": 0, "record_id": 1}
        ).to_list(500)
        has_receipt_set = {s["record_id"] for s in upload_sessions}

    expenses_result = []
    no_receipt_count = 0
    for e in unverified_expenses:
        has_receipt = e["id"] in has_receipt_set
        if not has_receipt:
            no_receipt_count += 1
        expenses_result.append({
            "id": e["id"],
            "category": e.get("category", ""),
            "description": e.get("description", ""),
            "reference_number": e.get("reference_number", ""),
            "amount": float(e.get("amount", 0)),
            "fund_source": e.get("fund_source", "cashier"),
            "date": e.get("date", ""),
            "employee_name": e.get("employee_name", ""),
            "created_by_name": e.get("created_by_name", ""),
            "has_receipt": has_receipt,
        })
    total_unverified_amount = round(sum(e["amount"] for e in expenses_result), 2)

    # ── Unverified POs (received but not verified) ────────────────────────
    unverified_pos = await db.purchase_orders.find(
        {"branch_id": branch_id,
         "purchase_date": {"$gte": date_from, "$lte": date_to},
         "status": {"$in": ["received", "partial"]},
         "verified": {"$ne": True}},
        {"_id": 0, "id": 1, "po_number": 1, "vendor": 1, "grand_total": 1, "subtotal": 1,
         "purchase_date": 1, "status": 1}
    ).sort("purchase_date", -1).to_list(500)

    po_ids = [p["id"] for p in unverified_pos]
    po_receipt_set = set()
    if po_ids:
        po_uploads = await db.upload_sessions.find(
            {"record_type": "purchase_order", "record_id": {"$in": po_ids},
             "is_pending": {"$ne": True}, "file_count": {"$gt": 0}},
            {"_id": 0, "record_id": 1}
        ).to_list(500)
        po_receipt_set = {s["record_id"] for s in po_uploads}

    pos_result = []
    po_no_receipt = 0
    for p in unverified_pos:
        has_receipt = p["id"] in po_receipt_set
        if not has_receipt:
            po_no_receipt += 1
        pos_result.append({
            "id": p["id"],
            "po_number": p.get("po_number", ""),
            "vendor": p.get("vendor", ""),
            "grand_total": round(float(p.get("grand_total") or p.get("subtotal", 0)), 2),
            "purchase_date": p.get("purchase_date", ""),
            "has_receipt": has_receipt,
        })

    # ── Unverified digital payments (GCash, Maya, etc.) ───────────────────
    # Digital invoices that haven't been verified (receipt_review_status != reviewed)
    unverified_digital = await db.invoices.find(
        {"branch_id": branch_id, "order_date": {"$gte": date_from, "$lte": date_to},
         "fund_source": {"$in": ["digital", "split"]}, "status": {"$ne": "voided"},
         "receipt_review_status": {"$ne": "reviewed"}},
        {"_id": 0, "id": 1, "invoice_number": 1, "customer_name": 1, "order_date": 1,
         "amount_paid": 1, "digital_amount": 1, "cash_amount": 1,
         "digital_platform": 1, "digital_ref_number": 1, "digital_sender": 1,
         "fund_source": 1, "grand_total": 1}
    ).sort("order_date", -1).to_list(500)

    # Check which have receipt uploads
    digital_ids = [d["id"] for d in unverified_digital]
    digital_receipt_set = set()
    if digital_ids:
        digital_uploads = await db.upload_sessions.find(
            {"record_type": "invoice", "record_id": {"$in": digital_ids},
             "is_pending": {"$ne": True}, "file_count": {"$gt": 0}},
            {"_id": 0, "record_id": 1}
        ).to_list(500)
        digital_receipt_set = {s["record_id"] for s in digital_uploads}

    digital_result = []
    digital_no_receipt = 0
    digital_no_ref = 0
    total_unverified_digital = 0.0
    for inv in unverified_digital:
        is_split = inv.get("fund_source") == "split"
        digital_amt = float(inv.get("digital_amount", 0)) if is_split and inv.get("digital_amount") else float(inv.get("amount_paid", 0))
        total_unverified_digital += digital_amt
        has_receipt = inv["id"] in digital_receipt_set
        has_ref = bool(inv.get("digital_ref_number"))
        if not has_receipt:
            digital_no_receipt += 1
        if not has_ref:
            digital_no_ref += 1
        digital_result.append({
            "id": inv["id"],
            "invoice_number": inv.get("invoice_number", ""),
            "customer_name": inv.get("customer_name", ""),
            "date": inv.get("order_date", ""),
            "platform": inv.get("digital_platform", "Digital") or "Digital",
            "ref_number": inv.get("digital_ref_number", ""),
            "sender": inv.get("digital_sender", ""),
            "amount": round(digital_amt, 2),
            "is_split": is_split,
            "has_receipt": has_receipt,
            "has_ref": has_ref,
        })

    total_items = len(expenses_result) + len(pos_result) + len(digital_result)
    sev = "critical" if no_receipt_count > 3 or po_no_receipt > 2 or digital_no_ref > 2 else (
        "warning" if total_items > 0 else "ok"
    )

    return {
        "expenses": expenses_result[:50],
        "expenses_count": len(expenses_result),
        "expenses_no_receipt": no_receipt_count,
        "total_unverified_expense_amount": total_unverified_amount,
        "purchase_orders": pos_result[:50],
        "po_count": len(pos_result),
        "po_no_receipt": po_no_receipt,
        "digital_payments": digital_result[:50],
        "digital_count": len(digital_result),
        "digital_no_receipt": digital_no_receipt,
        "digital_no_ref": digital_no_ref,
        "total_unverified_digital": round(total_unverified_digital, 2),
        "total_items": total_items,
        "severity": sev,
    }


@router.get("/transfer-variances")
async def get_transfer_variances(
    user=Depends(get_current_user),
    branch_id: Optional[str] = None,
    limit: int = 50
):
    """Get transfer variance history with incident ticket links for the Audit Center."""
    check_perm(user, "reports", "view")

    query = {}
    if branch_id:
        query["$or"] = [{"from_branch_id": branch_id}, {"to_branch_id": branch_id}]

    # Transfers with variances (has_shortage or has_excess, and status is received)
    query["$and"] = [
        {"status": "received"},
        {"$or": [{"has_shortage": True}, {"has_excess": True}]}
    ]

    transfers = await db.branch_transfer_orders.find(
        query, {"_id": 0}
    ).sort("received_at", -1).to_list(limit)

    items = []
    total_capital_loss = 0
    total_with_tickets = 0

    for t in transfers:
        shortages = t.get("shortages", [])
        excesses = t.get("excesses", [])
        capital_loss = sum(s.get("capital_variance", 0) for s in shortages)
        total_capital_loss += capital_loss

        from_branch = await db.branches.find_one({"id": t.get("from_branch_id")}, {"_id": 0, "name": 1})
        to_branch = await db.branches.find_one({"id": t.get("to_branch_id")}, {"_id": 0, "name": 1})

        has_ticket = bool(t.get("incident_ticket_id"))
        if has_ticket:
            total_with_tickets += 1

        items.append({
            "transfer_id": t["id"],
            "order_number": t.get("order_number", ""),
            "from_branch_name": from_branch.get("name", "") if from_branch else "",
            "to_branch_name": to_branch.get("name", "") if to_branch else "",
            "shortages_count": len(shortages),
            "excesses_count": len(excesses),
            "capital_loss": round(capital_loss, 2),
            "incident_ticket_id": t.get("incident_ticket_id"),
            "incident_ticket_number": t.get("incident_ticket_number"),
            "accepted_at": t.get("accepted_at") or t.get("received_at", ""),
            "accepted_by_name": t.get("accepted_by_name", ""),
            "accept_note": t.get("accept_note", ""),
            "dispute_note": t.get("dispute_note"),
            "disputed_at": t.get("disputed_at"),
            "status": t["status"],
        })

    # Summary
    open_tickets = await db.incident_tickets.count_documents({"status": {"$in": ["open", "investigating"]}})
    total_unresolved_loss = 0
    unresolved = await db.incident_tickets.find(
        {"status": {"$in": ["open", "investigating"]}}, {"_id": 0, "total_capital_loss": 1}
    ).to_list(500)
    for t in unresolved:
        total_unresolved_loss += t.get("total_capital_loss", 0)

    return {
        "items": items,
        "summary": {
            "total_variance_transfers": len(items),
            "total_capital_loss": round(total_capital_loss, 2),
            "total_with_incident_tickets": total_with_tickets,
            "open_incident_tickets": open_tickets,
            "total_unresolved_loss": round(total_unresolved_loss, 2),
        }
    }


# ─────────────────────────────────────────────────────────────────────────────
#  PHASE 1 DEEP ANALYSIS — KPI ratios, trend deltas, weighted scores
# ─────────────────────────────────────────────────────────────────────────────

async def _compute_kpis(branch_id: str, date_from: str, date_to: str,
                        audit_result: Optional[dict]) -> dict:
    """
    Compute the six cross-cutting financial KPIs that a real auditor looks at.
    These are ratios (not counts), so they stay comparable across branches and
    periods. `audit_result` (if present) lets us reuse already-computed numbers
    to avoid re-aggregating; when computing a prior period we pass None and
    aggregate freshly.
    """

    # ── Revenue + COGS (for gross margin) ─────────────────────────────────
    inv_query = {
        "branch_id": branch_id,
        "order_date": {"$gte": date_from, "$lte": date_to},
        "status": {"$ne": "voided"},
        "sale_type": {"$nin": ["interest_charge", "penalty_charge"]},
    }
    invoices = await db.invoices.find(
        inv_query, {"_id": 0, "items": 1, "grand_total": 1, "payment_type": 1, "fund_source": 1}
    ).to_list(5000)

    revenue = 0.0
    cogs = 0.0
    for inv in invoices:
        for item in inv.get("items", []):
            qty = float(item.get("quantity", 0) or 0)
            revenue += float(item.get("total", 0) or 0)
            cogs += float(item.get("cost_price", 0) or 0) * qty

    # Net out returns (same rules as Product Profitability report)
    returns = await db.returns.find(
        {"branch_id": branch_id, "return_date": {"$gte": date_from, "$lte": date_to},
         "voided": {"$ne": True}},
        {"_id": 0, "items": 1}
    ).to_list(2000)
    for ret in returns:
        for item in ret.get("items", []):
            qty = float(item.get("quantity", 0) or 0)
            rev_back = float(item.get("refund_price", 0) or 0) * qty
            cost_back = float(item.get("cost_price", 0) or 0) * qty
            revenue -= rev_back
            if item.get("inventory_action", "shelf") == "shelf":
                cogs -= cost_back
            # pullout: cost stays (real loss)

    gross_profit = round(revenue - cogs, 2)
    gross_margin_pct = round((gross_profit / revenue) * 100, 2) if revenue > 0 else 0.0

    # ── Void / Edit rates ─────────────────────────────────────────────────
    total_txns = await db.invoices.count_documents({
        "branch_id": branch_id, "order_date": {"$gte": date_from, "$lte": date_to},
    })
    voided = await db.invoices.count_documents({
        "branch_id": branch_id, "order_date": {"$gte": date_from, "$lte": date_to},
        "status": "voided",
    })
    edits = await db.invoice_edits.count_documents({
        "edited_at": {"$gte": f"{date_from}T00:00:00", "$lte": f"{date_to}T23:59:59"},
    })
    void_rate_pct = round((voided / total_txns) * 100, 2) if total_txns > 0 else 0.0
    edit_rate_pct = round((edits / total_txns) * 100, 2) if total_txns > 0 else 0.0

    # ── Discount rate ─────────────────────────────────────────────────────
    discount_r = await db.discount_audit_log.aggregate([
        {"$match": {"branch_id": branch_id, "date": {"$gte": date_from, "$lte": date_to},
                    "invoice_voided": {"$ne": True}}},
        {"$group": {"_id": None, "total": {"$sum": "$total_discount"}}}
    ]).to_list(1)
    total_discount = round(discount_r[0]["total"], 2) if discount_r else 0.0
    # Gross sales = revenue + discount (approximate pre-discount revenue)
    gross_sales = revenue + total_discount
    discount_rate_pct = round((total_discount / gross_sales) * 100, 2) if gross_sales > 0 else 0.0

    # ── DSO (Days Sales Outstanding) ──────────────────────────────────────
    # Outstanding AR / (credit-qualifying sales per day)
    # We use revenue-in-period (non-voided) as the daily sales denominator.
    try:
        pf = datetime.strptime(date_from, "%Y-%m-%d").date()
        pt = datetime.strptime(date_to, "%Y-%m-%d").date()
        days_in_period = max((pt - pf).days + 1, 1)
    except Exception:
        days_in_period = 30

    open_ar_r = await db.invoices.aggregate([
        {"$match": {"branch_id": branch_id, "balance": {"$gt": 0},
                    "status": {"$nin": ["voided", "paid"]}, "customer_id": {"$ne": None}}},
        {"$group": {"_id": None, "total": {"$sum": "$balance"}}}
    ]).to_list(1)
    total_ar = round(open_ar_r[0]["total"], 2) if open_ar_r else 0.0
    daily_sales = revenue / days_in_period if revenue > 0 else 0.0
    dso_days = round(total_ar / daily_sales, 1) if daily_sales > 0 else 0.0

    # ── DPO (Days Payable Outstanding) ────────────────────────────────────
    open_ap_r = await db.purchase_orders.aggregate([
        {"$match": {"branch_id": branch_id,
                    "payment_status": {"$in": ["unpaid", "partial"]},
                    "status": {"$ne": "cancelled"}}},
        {"$group": {"_id": None,
                    "total": {"$sum": {"$ifNull": ["$balance", "$grand_total"]}}}}
    ]).to_list(1)
    total_ap = round(open_ap_r[0]["total"], 2) if open_ap_r else 0.0

    # Purchases in period (for DPO denominator)
    purchases_r = await db.purchase_orders.aggregate([
        {"$match": {"branch_id": branch_id,
                    "purchase_date": {"$gte": date_from, "$lte": date_to},
                    "status": {"$ne": "cancelled"}}},
        {"$group": {"_id": None,
                    "total": {"$sum": {"$ifNull": ["$grand_total", "$subtotal"]}}}}
    ]).to_list(1)
    purchases = round(purchases_r[0]["total"], 2) if purchases_r else 0.0
    daily_purchases = purchases / days_in_period if purchases > 0 else 0.0
    dpo_days = round(total_ap / daily_purchases, 1) if daily_purchases > 0 else 0.0

    # ── Inventory turnover (annualised) ───────────────────────────────────
    # Approximation: current inventory capital value at branch.
    inv_val_r = await db.branch_inventory.aggregate([
        {"$match": {"branch_id": branch_id}},
        {"$group": {"_id": None,
                    "total": {"$sum": {"$multiply": ["$quantity", "$moving_avg_cost"]}}}}
    ]).to_list(1)
    inventory_value = round(inv_val_r[0]["total"], 2) if inv_val_r and inv_val_r[0]["total"] else 0.0
    # turnover = cogs / inventory_value (per period). Annualise to per-year.
    period_turnover = (cogs / inventory_value) if inventory_value > 0 else 0.0
    annualised_turnover = round(period_turnover * (365 / days_in_period), 2) if days_in_period > 0 else 0.0

    # ── Payment mix % ─────────────────────────────────────────────────────
    by_type = {"cash": 0.0, "credit": 0.0, "partial": 0.0, "digital": 0.0, "split": 0.0}
    for inv in invoices:
        ptype = inv.get("payment_type") or "cash"
        fsource = inv.get("fund_source") or ""
        total = float(inv.get("grand_total", 0) or 0)
        if fsource == "digital":
            by_type["digital"] += total
        elif fsource == "split":
            by_type["split"] += total
        elif ptype in by_type:
            by_type[ptype] += total
        else:
            by_type["cash"] += total
    mix_total = sum(by_type.values()) or 1.0
    payment_mix_pct = {k: round((v / mix_total) * 100, 1) for k, v in by_type.items()}

    return {
        "revenue": round(revenue, 2),
        "cogs": round(cogs, 2),
        "gross_profit": gross_profit,
        "gross_margin_pct": gross_margin_pct,
        "void_rate_pct": void_rate_pct,
        "voided_count": voided,
        "edit_rate_pct": edit_rate_pct,
        "edit_count": edits,
        "total_txns": total_txns,
        "discount_rate_pct": discount_rate_pct,
        "total_discount": total_discount,
        "dso_days": dso_days,
        "total_ar": total_ar,
        "dpo_days": dpo_days,
        "total_ap": total_ap,
        "purchases_in_period": purchases,
        "inventory_turnover": annualised_turnover,
        "inventory_value": inventory_value,
        "payment_mix_pct": payment_mix_pct,
        "days_in_period": days_in_period,
    }


def _compute_trend_deltas(current: dict, prev: dict) -> dict:
    """
    Compare current period KPIs vs previous period of equal length.
    Returns % change per KPI (positive = up, negative = down).
    """
    if not prev:
        return {}

    def delta(k: str) -> Optional[float]:
        cur = current.get(k, 0) or 0
        prv = prev.get(k, 0) or 0
        if prv == 0:
            # can't compute % change vs zero — flag as "new" with raw delta
            return None if cur == 0 else 100.0
        return round(((cur - prv) / abs(prv)) * 100, 1)

    return {
        "revenue": delta("revenue"),
        "gross_margin_pct": round((current.get("gross_margin_pct", 0) - prev.get("gross_margin_pct", 0)), 2),
        "void_rate_pct": round((current.get("void_rate_pct", 0) - prev.get("void_rate_pct", 0)), 2),
        "discount_rate_pct": round((current.get("discount_rate_pct", 0) - prev.get("discount_rate_pct", 0)), 2),
        "dso_days": round((current.get("dso_days", 0) - prev.get("dso_days", 0)), 1),
        "dpo_days": round((current.get("dpo_days", 0) - prev.get("dpo_days", 0)), 1),
        "inventory_turnover": round((current.get("inventory_turnover", 0) - prev.get("inventory_turnover", 0)), 2),
    }


def _compute_scores(result: dict) -> dict:
    """
    Two scores, each 0–100:

      HEALTH SCORE — Is the business well-run? (ratios + reconciliations)
        Weights: cash 20 · inventory 15 · AR 10 · AP 10 · sales 10 · returns 5
                 transfers 10 · unverified 10 · digital 5 · activity 5
        Section severity: ok=100, warning=60, critical=20
        Only applies the inventory weight when a full audit ran; otherwise
        the inventory weight is redistributed proportionally.

      FRAUD RISK SCORE — Pattern-based red flags (0 = clean, 100 = dangerous)
        Composed from: void rate, edit rate, discount rate, off-hours count,
        inventory corrections, security flags, price-match activity.
        Each factor contributes up to its cap; totals are clipped to 0–100.
    """
    # ── HEALTH SCORE ──────────────────────────────────────────────────────
    weights = {
        "cash":        0.20,
        "inventory":   0.15,
        "ar":          0.10,
        "payables":    0.10,
        "sales":       0.10,
        "returns":     0.05,
        "transfers":   0.10,
        "unverified":  0.10,
        "digital":     0.05,
        "activity":    0.05,
    }

    def sev_score(sev: Optional[str]) -> int:
        # Unknown/missing severity defaults to 'warning' so new sections without
        # proper severity wiring surface as needing review instead of silently
        # scoring perfect.
        if sev == "ok":
            return 100
        if sev == "critical":
            return 20
        if sev in (None, "", "info"):
            import logging
            logging.getLogger(__name__).debug("Audit section missing severity — defaulting to 60")
            return 60
        return 60  # warning or any other value

    # If inventory not available, redistribute its weight across the rest.
    inv_avail = bool(result.get("inventory", {}).get("available"))
    if not inv_avail:
        w = weights.copy()
        inv_w = w.pop("inventory")
        boost = inv_w / len(w)
        w = {k: v + boost for k, v in w.items()}
    else:
        w = weights

    health_total = 0.0
    section_scores = {}
    for key, weight in w.items():
        sev = (result.get(key) or {}).get("severity")
        s = sev_score(sev)
        section_scores[key] = {"severity": sev or "ok", "score": s, "weight": round(weight * 100, 1)}
        health_total += s * weight
    health_score = round(health_total)

    # ── FRAUD RISK SCORE ──────────────────────────────────────────────────
    # Caps out at 100. Each "cost" is additive up to its max.
    kpis = result.get("kpis") or {}
    activity = result.get("activity") or {}
    security = result.get("security") or {}

    risk = 0.0
    risk_breakdown = []

    # Void rate >2% = bad, >5% = critical. Max 25 pts.
    vr = kpis.get("void_rate_pct", 0) or 0
    vr_pts = min(25, max(0, (vr - 1.0) * 5))  # 1% = 0pt, 2% = 5pts, 6% = 25pts
    risk += vr_pts
    risk_breakdown.append({"factor": "Void rate", "value": f"{vr}%", "points": round(vr_pts, 1), "max": 25})

    # Discount rate >5% mild, >10% critical. Max 15 pts.
    dr = kpis.get("discount_rate_pct", 0) or 0
    dr_pts = min(15, max(0, (dr - 3.0) * 2))
    risk += dr_pts
    risk_breakdown.append({"factor": "Discount rate", "value": f"{dr}%", "points": round(dr_pts, 1), "max": 15})

    # Edit rate >1% mild, >3% critical. Max 15 pts.
    er = kpis.get("edit_rate_pct", 0) or 0
    er_pts = min(15, max(0, (er - 0.5) * 6))
    risk += er_pts
    risk_breakdown.append({"factor": "Invoice edit rate", "value": f"{er}%", "points": round(er_pts, 1), "max": 15})

    # Off-hours transactions. Each = 1 pt, max 10 pts.
    oh = activity.get("off_hours_count", 0) or 0
    oh_pts = min(10, oh)
    risk += oh_pts
    risk_breakdown.append({"factor": "Off-hours transactions", "value": str(oh), "points": oh_pts, "max": 10})

    # Inventory corrections, up to 10 pts.
    ic = activity.get("inventory_corrections_count", 0) or 0
    ic_pts = min(10, ic * 1.5)
    risk += ic_pts
    risk_breakdown.append({"factor": "Inventory corrections", "value": str(ic), "points": round(ic_pts, 1), "max": 10})

    # Security flags (PIN brute-force) — HIGH weighs a lot. Max 15 pts.
    hi = security.get("high_severity", 0) or 0
    md = security.get("medium_severity", 0) or 0
    sec_pts = min(15, hi * 5 + md * 2)
    risk += sec_pts
    risk_breakdown.append({"factor": "PIN brute-force alerts", "value": f"{hi}H / {md}M", "points": sec_pts, "max": 15})

    # Price-match volume — legitimate but watched. Max 10 pts.
    pm = activity.get("price_change_count", 0) or 0
    pm_pts = min(10, pm * 0.5)
    risk += pm_pts
    risk_breakdown.append({"factor": "Price-match volume", "value": str(pm), "points": round(pm_pts, 1), "max": 10})

    fraud_risk = min(100, round(risk))

    # Labels
    def health_label(s):
        if s >= 85: return "Excellent"
        if s >= 70: return "Good"
        if s >= 50: return "Needs Review"
        return "Poor"

    def risk_label(s):
        if s <= 20: return "Low"
        if s <= 50: return "Elevated"
        if s <= 75: return "High"
        return "Critical"

    return {
        "health_score": health_score,
        "health_label": health_label(health_score),
        "fraud_risk_score": fraud_risk,
        "fraud_risk_label": risk_label(fraud_risk),
        "section_scores": section_scores,
        "risk_breakdown": risk_breakdown,
    }

"""
Notifications routes.
In-app alerts for admin/owner users.
v2: structured with severity + category for the notification center.
"""
from fastapi import APIRouter, Depends
from typing import Optional
from config import db
from utils import get_current_user, now_iso, new_id

router = APIRouter(prefix="/notifications", tags=["Notifications"])

# ── Category / Severity mapping (covers old and new notification types) ────────
NOTIFICATION_META = {
    # 🔴 Security
    "security_alert":             {"category": "security",    "severity": "critical"},
    # 🟠 Action Required
    "po_receipt_review":          {"category": "action",      "severity": "warning"},
    "transfer_variance_review":   {"category": "action",      "severity": "warning"},
    "incident_created":           {"category": "action",      "severity": "warning"},
    "pricing_issue":              {"category": "action",      "severity": "warning"},
    "branch_stock_request":       {"category": "action",      "severity": "info"},
    "negative_stock_override":    {"category": "action",      "severity": "warning"},
    "transfer_disputed":          {"category": "action",      "severity": "warning"},
    # 🟡 Approvals & Overrides
    "credit_sale":                {"category": "approvals",   "severity": "info"},
    "price_override":             {"category": "approvals",   "severity": "warning"},
    "discount_given":             {"category": "approvals",   "severity": "warning"},
    "below_cost_sale":            {"category": "approvals",   "severity": "warning"},
    "admin_action":               {"category": "approvals",   "severity": "info"},
    "return_pullout_loss":        {"category": "approvals",   "severity": "warning"},
    "employee_advance":           {"category": "approvals",   "severity": "info"},
    "reservation_expired":        {"category": "approvals",   "severity": "info"},
    # 🔵 Operations
    "transfer_incoming":          {"category": "operations",  "severity": "info"},
    "transfer_accepted":          {"category": "operations",  "severity": "info"},
    "ap_payment":                 {"category": "operations",  "severity": "info"},
    "draft_updated_via_qr":       {"category": "operations",  "severity": "info"},
    # 🟢 Finance
    "internal_invoice_due":       {"category": "finance",     "severity": "warning"},
    "internal_invoice_overdue":   {"category": "finance",     "severity": "critical"},
    "internal_invoice_paid":      {"category": "finance",     "severity": "info"},
    # 🟠 Compliance
    "compliance_deadline":        {"category": "action",      "severity": "warning"},
}

CATEGORY_LABELS = {
    "security":   "Security",
    "action":     "Action Required",
    "approvals":  "Approvals & Overrides",
    "operations": "Operations",
    "finance":    "Finance",
}

def _enrich(n: dict, uid: str) -> dict:
    """Add is_read + category + severity derived fields."""
    n["is_read"] = uid in n.get("read_by", [])
    meta = NOTIFICATION_META.get(n.get("type", ""), {})
    n.setdefault("category",  meta.get("category",  "operations"))
    n.setdefault("severity",  meta.get("severity",  "info"))
    return n


@router.get("")
async def list_notifications(
    user=Depends(get_current_user),
    category: Optional[str] = None,
    unread_only: bool = False,
    limit: int = 50,
    skip: int = 0,
):
    """Get notifications for the current user with optional category filter."""
    query = {}
    if user.get("role") != "admin":
        query["target_user_ids"] = user["id"]
    if unread_only:
        query["read_by"] = {"$not": {"$elemMatch": {"$eq": user["id"]}}}
    if category:
        # Match by stored category field OR compute from type
        types_in_cat = [t for t, m in NOTIFICATION_META.items() if m["category"] == category]
        query["$or"] = [
            {"category": category},
            {"type": {"$in": types_in_cat}},
        ]

    uid = user["id"]
    notifications = await db.notifications.find(query, {"_id": 0}) \
        .sort("created_at", -1).skip(skip).limit(limit).to_list(limit)
    for n in notifications:
        _enrich(n, uid)

    unread_count = await db.notifications.count_documents({
        **{k: v for k, v in query.items() if k != "read_by"},
        "read_by": {"$not": {"$elemMatch": {"$eq": uid}}}
    })

    # Category counts (for the dashboard cards)
    category_counts = {}
    for cat in CATEGORY_LABELS:
        cat_types = [t for t, m in NOTIFICATION_META.items() if m["category"] == cat]
        base = {} if user.get("role") == "admin" else {"target_user_ids": uid}
        total = await db.notifications.count_documents({**base, "$or": [
            {"category": cat}, {"type": {"$in": cat_types}}
        ]})
        unread = await db.notifications.count_documents({**base, "$or": [
            {"category": cat}, {"type": {"$in": cat_types}}
        ], "read_by": {"$not": {"$elemMatch": {"$eq": uid}}}})
        category_counts[cat] = {"total": total, "unread": unread, "label": CATEGORY_LABELS[cat]}

    return {
        "notifications": notifications,
        "unread_count": unread_count,
        "total": await db.notifications.count_documents(query),
        "category_counts": category_counts,
    }


@router.put("/{notification_id}/read")
async def mark_read(notification_id: str, user=Depends(get_current_user)):
    await db.notifications.update_one(
        {"id": notification_id},
        {"$addToSet": {"read_by": user["id"]}}
    )
    return {"message": "Marked as read"}


@router.put("/mark-all-read")
async def mark_all_read(user=Depends(get_current_user)):
    query = {} if user.get("role") == "admin" else {"target_user_ids": user["id"]}
    await db.notifications.update_many(query, {"$addToSet": {"read_by": user["id"]}})
    return {"message": "All marked as read"}


# ── Shared helper: create a structured notification ────────────────────────────
async def create_notification(
    type_key: str,
    title: str,
    message: str,
    target_user_ids: list,
    branch_id: str = "",
    branch_name: str = "",
    metadata: dict = None,
    organization_id: str = None,
    severity_override: str = None,
) -> dict:
    """Create a structured notification with auto-assigned category and severity."""
    meta = NOTIFICATION_META.get(type_key, {"category": "operations", "severity": "info"})
    doc = {
        "id": new_id(),
        "type": type_key,
        "category": meta["category"],
        "severity": severity_override or meta["severity"],
        "title": title,
        "message": message,
        "branch_id": branch_id,
        "branch_name": branch_name,
        "metadata": metadata or {},
        "target_user_ids": target_user_ids,
        "organization_id": organization_id,
        "read_by": [],
        "created_at": now_iso(),
    }
    await db.notifications.insert_one(doc)
    return doc


async def create_pin_notification(context, manager_id: str, manager_name: str):
    """
    Create a notification for admin users when a manager PIN is used.
    context = { type, description, branch_id, branch_name, amount, ... }
    """
    if isinstance(context, str):
        context = {"type": "admin_action", "description": context}

    admins = await db.users.find(
        {"role": "admin", "active": True}, {"_id": 0, "id": 1}
    ).to_list(50)
    target_ids = [a["id"] for a in admins]

    ctx_type = context.get("type", "pin_used")
    type_labels = {
        "credit_sale": "Credit Sale Approved",
        "employee_advance": "Employee Advance Approved",
        "price_override": "Price Override Approved",
        "inventory_seed": "Inventory Seed Authorized",
    }
    title = type_labels.get(ctx_type, "Manager PIN Used")
    description = context.get("description", "A manager PIN was used")
    branch_name = context.get("branch_name", "")
    location = f" at {branch_name}" if branch_name else ""

    await create_notification(
        type_key=ctx_type,
        title=title,
        message=f"{manager_name} approved: {description}{location}",
        target_user_ids=target_ids,
        branch_id=context.get("branch_id", ""),
        branch_name=branch_name,
        metadata={**context, "manager_id": manager_id, "manager_name": manager_name},
        organization_id=context.get("organization_id"),
    )


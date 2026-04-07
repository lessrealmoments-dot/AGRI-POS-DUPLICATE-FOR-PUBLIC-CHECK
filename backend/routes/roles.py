"""
Custom Roles management routes.
Admins can define org-specific roles with custom permission sets and pin_tier.
System roles are static definitions returned alongside custom roles.
"""
from fastapi import APIRouter, Depends, HTTPException
from config import db
from utils import get_current_user, now_iso, new_id
from models import ROLE_PRESETS, SYSTEM_ROLES

router = APIRouter(prefix="/roles", tags=["Roles"])

# Static definitions for system roles — read-only reference for the frontend
SYSTEM_ROLE_DEFINITIONS = [
    {
        "id": "admin",
        "name": "admin",
        "label": "Administrator",
        "description": "Full access to all features and settings. Bypasses all permission checks.",
        "pin_tier": "manager",
        "is_system": True,
    },
    {
        "id": "manager",
        "name": "manager",
        "label": "Branch Manager",
        "description": "Manage branch operations: POs, expenses, reports, close day. Limited admin access.",
        "pin_tier": "manager",
        "is_system": True,
    },
    {
        "id": "cashier",
        "name": "cashier",
        "label": "Cashier",
        "description": "POS sales and basic customer service. No financial admin access.",
        "pin_tier": "staff",
        "is_system": True,
    },
    {
        "id": "inventory",
        "name": "inventory",
        "label": "Inventory Clerk",
        "description": "PO receiving and stock tracking. Direct inventory edits require explicit admin grant.",
        "pin_tier": "staff",
        "is_system": True,
    },
]


@router.get("")
async def list_roles(user=Depends(get_current_user)):
    """List all roles: system (static) + custom roles scoped to this org."""
    org_id = user.get("organization_id")
    query = {"active": True}
    if org_id:
        query["organization_id"] = org_id

    custom = await db.custom_roles.find(query, {"_id": 0}).sort("created_at", 1).to_list(100)

    # Attach user_count to each custom role
    for role in custom:
        role["user_count"] = await db.users.count_documents(
            {"role": role["id"], "active": True}
        )

    return {
        "system_roles": SYSTEM_ROLE_DEFINITIONS,
        "custom_roles": custom,
    }


@router.post("")
async def create_role(data: dict, user=Depends(get_current_user)):
    """Create a custom role. Admin only."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")

    label = (data.get("label") or "").strip()
    if not label:
        raise HTTPException(status_code=400, detail="Role label is required")

    pin_tier = data.get("pin_tier", "staff")
    if pin_tier not in ("manager", "staff"):
        raise HTTPException(status_code=400, detail="pin_tier must be 'manager' or 'staff'")

    # Start from base_preset permissions, allow overrides
    base_key = data.get("base_preset", "cashier")
    # Normalize base_key for inventory alias
    lookup_key = "inventory_clerk" if base_key == "inventory" else base_key
    base_perms = ROLE_PRESETS.get(lookup_key, ROLE_PRESETS["cashier"])["permissions"]
    permissions = data.get("permissions") or base_perms

    role_doc = {
        "id": new_id(),
        "organization_id": user.get("organization_id"),
        "name": label.lower().replace(" ", "_"),
        "label": label,
        "description": (data.get("description") or "").strip(),
        "pin_tier": pin_tier,
        "base_preset": base_key,
        "permissions": permissions,
        "is_system": False,
        "created_by": user["id"],
        "created_by_name": user.get("full_name", user.get("username", "")),
        "created_at": now_iso(),
        "active": True,
    }
    await db.custom_roles.insert_one(role_doc)
    del role_doc["_id"]
    role_doc["user_count"] = 0
    return role_doc


@router.put("/{role_id}")
async def update_role(role_id: str, data: dict, user=Depends(get_current_user)):
    """Update a custom role label, description, pin_tier, or permissions. Admin only."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")

    org_id = user.get("organization_id")
    query = {"id": role_id, "active": True}
    if org_id:
        query["organization_id"] = org_id

    role = await db.custom_roles.find_one(query, {"_id": 0})
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")

    update = {}
    if "label" in data and str(data["label"]).strip():
        update["label"] = data["label"].strip()
        update["name"] = data["label"].strip().lower().replace(" ", "_")
    if "description" in data:
        update["description"] = data["description"]
    if "pin_tier" in data and data["pin_tier"] in ("manager", "staff"):
        update["pin_tier"] = data["pin_tier"]
    if "permissions" in data:
        update["permissions"] = data["permissions"]

    update["updated_at"] = now_iso()
    update["updated_by"] = user["id"]
    await db.custom_roles.update_one({"id": role_id}, {"$set": update})

    updated = await db.custom_roles.find_one({"id": role_id}, {"_id": 0})
    updated["user_count"] = await db.users.count_documents({"role": role_id, "active": True})
    return updated


@router.delete("/{role_id}")
async def delete_role(role_id: str, user=Depends(get_current_user)):
    """Soft-delete a custom role. Blocked if users are currently assigned."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")

    org_id = user.get("organization_id")
    query = {"id": role_id, "active": True}
    if org_id:
        query["organization_id"] = org_id

    role = await db.custom_roles.find_one(query, {"_id": 0})
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")

    assigned = await db.users.count_documents({"role": role_id, "active": True})
    if assigned > 0:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot delete: {assigned} user(s) are assigned this role. Reassign them first."
        )

    await db.custom_roles.update_one(
        {"id": role_id},
        {"$set": {"active": False, "deleted_at": now_iso(), "deleted_by": user["id"]}}
    )
    return {"message": "Role deleted"}

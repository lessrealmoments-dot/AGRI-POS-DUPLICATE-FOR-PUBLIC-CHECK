"""
User management and permissions routes.
"""
from fastapi import APIRouter, Depends, HTTPException
from config import db
from utils import (
    get_current_user, check_perm, has_perm,
    hash_password, now_iso, new_id
)
from models import PERMISSION_MODULES, ROLE_PRESETS, DEFAULT_PERMISSIONS, SYSTEM_ROLES

router = APIRouter(tags=["Users & Permissions"])


# ==================== USER MANAGEMENT ====================
@router.get("/users")
async def list_users(include_inactive: bool = False, user=Depends(get_current_user)):
    """List users. Admin can include inactive users."""
    query = {} if (include_inactive and user.get("role") == "admin") else {"active": True}
    users = await db.users.find(query, {"_id": 0, "password_hash": 0}).to_list(200)
    return users


@router.post("/users")
async def create_user(data: dict, user=Depends(get_current_user)):
    """Create a new user. Email is the primary login identifier (username auto-derived from email)."""
    check_perm(user, "settings", "manage_users")

    email = (data.get("email") or "").strip().lower()
    full_name = (data.get("full_name") or "").strip()
    password = data.get("password") or ""

    if not email:
        raise HTTPException(status_code=400, detail="Email address is required")
    if not full_name:
        raise HTTPException(status_code=400, detail="Full name is required")
    if not password:
        raise HTTPException(status_code=400, detail="Password is required")

    # Use email as the unique username (multi-tenant login identifier)
    username = data.get("username") or email

    existing = await db.users.find_one(
        {"$or": [{"username": username}, {"email": email}]}, {"_id": 0}
    )
    if existing:
        raise HTTPException(status_code=400, detail="A user with this email already exists")

    role = data.get("role", "cashier")

    # Resolve default permissions: prefer ROLE_PRESETS, fallback to DEFAULT_PERMISSIONS
    # Normalise inventory alias → inventory_clerk for preset lookup
    preset_key = "inventory_clerk" if role == "inventory" else role
    default_perms = (
        data.get("permissions")
        or ROLE_PRESETS.get(preset_key, {}).get("permissions")
        or DEFAULT_PERMISSIONS.get(role, DEFAULT_PERMISSIONS["cashier"])
    )

    # Determine pin_tier for future custom-role support
    staff_roles = {"cashier", "staff", "inventory", "inventory_clerk"}
    pin_tier = "staff" if role in staff_roles else "manager"

    new_user = {
        "id": new_id(),
        "username": username,
        "full_name": full_name,
        "email": email,
        "password_hash": hash_password(password),
        "role": role,
        "pin_tier": pin_tier,
        "branch_id": data.get("branch_id"),
        "permissions": default_perms,
        "active": True,
        "created_at": now_iso(),
    }
    await db.users.insert_one(new_user)
    safe = {k: v for k, v in new_user.items() if k not in ("password_hash", "_id")}
    return safe


@router.get("/users/{user_id}")
async def get_user(user_id: str, user=Depends(get_current_user)):
    """Get user details."""
    target = await db.users.find_one({"id": user_id}, {"_id": 0, "password_hash": 0})
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    return target


@router.put("/users/{user_id}")
async def update_user(user_id: str, data: dict, user=Depends(get_current_user)):
    """Update user details."""
    check_perm(user, "settings", "manage_users")
    
    update = {k: v for k, v in data.items() if k in (
        "full_name", "email", "role", "branch_id", "active",
        "is_auditor", "auditor_pin",
    )}
    
    if "password" in data and data["password"]:
        update["password_hash"] = hash_password(data["password"])
    
    if "role" in update:
        update["permissions"] = data.get("permissions", DEFAULT_PERMISSIONS.get(update["role"], DEFAULT_PERMISSIONS["cashier"]))
    
    update["updated_at"] = now_iso()
    await db.users.update_one({"id": user_id}, {"$set": update})
    
    updated = await db.users.find_one({"id": user_id}, {"_id": 0, "password_hash": 0})
    return updated


@router.delete("/users/{user_id}")
async def delete_user(user_id: str, user=Depends(get_current_user)):
    """Soft delete a user (deactivate)."""
    check_perm(user, "settings", "manage_users")
    if user["id"] == user_id:
        raise HTTPException(status_code=400, detail="Cannot deactivate your own account")
    await db.users.update_one({"id": user_id}, {"$set": {"active": False, "updated_at": now_iso()}})
    return {"message": "User deactivated"}


@router.delete("/users/{user_id}/permanent")
async def permanently_delete_user(user_id: str, user=Depends(get_current_user)):
    """Permanently delete a user. Admin only."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Only admins can permanently delete users")
    if user["id"] == user_id:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")
    target = await db.users.find_one({"id": user_id}, {"_id": 0})
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    await db.users.delete_one({"id": user_id})
    return {"message": f"User {target.get('username', user_id)} permanently deleted"}


@router.put("/users/{user_id}/reactivate")
async def reactivate_user(user_id: str, user=Depends(get_current_user)):
    """Reactivate a disabled user. Admin only."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Only admins can reactivate users")
    target = await db.users.find_one({"id": user_id}, {"_id": 0})
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    await db.users.update_one({"id": user_id}, {"$set": {"active": True, "updated_at": now_iso()}})
    return {"message": f"User {target.get('username', user_id)} reactivated"}


@router.put("/users/{user_id}/pin")
async def admin_set_user_pin(user_id: str, data: dict, user=Depends(get_current_user)):
    """Admin sets or clears a manager PIN for any user (with audit trail)."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Only admins can set user PINs")
    
    target = await db.users.find_one({"id": user_id}, {"_id": 0})
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    
    pin = data.get("pin", "")
    if pin and len(str(pin)) < 4:
        raise HTTPException(status_code=400, detail="PIN must be at least 4 digits")

    # Route PIN to the correct field based on role (or pin_tier for custom roles)
    staff_roles = {"cashier", "staff", "inventory", "inventory_clerk"}
    is_staff = target.get("pin_tier") == "staff" or target.get("role") in staff_roles
    pin_field = "staff_pin" if is_staff else "manager_pin"
    other_field = "manager_pin" if pin_field == "staff_pin" else "staff_pin"
    pin_tier = "staff" if is_staff else "manager"

    await db.users.update_one(
        {"id": user_id},
        {"$set": {
            pin_field: str(pin) if pin else None,
            other_field: None,  # Clear the conflicting field to avoid ambiguity
            "pin_tier": pin_tier,  # Ensure pin_tier stays in sync
            "pin_set_by": user["id"],
            "pin_set_by_name": user.get("full_name", user["username"]),
            "pin_set_at": now_iso(),
            "updated_at": now_iso(),
        }}
    )
    return {"message": f"PIN {'set' if pin else 'cleared'} for {target['username']}", "pin_field": pin_field}


# ==================== PERMISSIONS ====================
@router.get("/permissions/modules")
async def get_permission_modules(user=Depends(get_current_user)):
    """Get all permission modules and their actions."""
    return PERMISSION_MODULES


@router.get("/permissions/presets")
async def get_permission_presets(user=Depends(get_current_user)):
    """Get all role presets."""
    return ROLE_PRESETS


@router.get("/permissions/presets/{preset_key}")
async def get_preset_permissions(preset_key: str, user=Depends(get_current_user)):
    """Get permissions for a specific preset role."""
    if preset_key not in ROLE_PRESETS:
        raise HTTPException(status_code=404, detail="Preset not found")
    return ROLE_PRESETS[preset_key]


@router.post("/users/{user_id}/apply-preset")
async def apply_preset_to_user(user_id: str, data: dict, user=Depends(get_current_user)):
    """Apply a preset role's permissions to a user."""
    check_perm(user, "settings", "manage_permissions")
    
    preset_key = data.get("preset")
    if preset_key not in ROLE_PRESETS:
        raise HTTPException(status_code=400, detail="Invalid preset")
    
    permissions = ROLE_PRESETS[preset_key]["permissions"]
    
    await db.users.update_one(
        {"id": user_id},
        {"$set": {
            "permissions": permissions,
            "permission_preset": preset_key,
            "updated_at": now_iso()
        }}
    )
    
    updated = await db.users.find_one({"id": user_id}, {"_id": 0, "password_hash": 0})
    return updated


@router.get("/users/{user_id}/permissions")
async def get_user_permissions(user_id: str, user=Depends(get_current_user)):
    """Get detailed permissions for a specific user."""
    # Allow users to view their own permissions
    if user["id"] != user_id:
        check_perm(user, "settings", "manage_permissions")
    
    target_user = await db.users.find_one({"id": user_id}, {"_id": 0, "password_hash": 0})
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")
    
    return {
        "user_id": user_id,
        "username": target_user.get("username"),
        "role": target_user.get("role"),
        "permission_preset": target_user.get("permission_preset"),
        "permissions": target_user.get("permissions", {}),
    }


@router.put("/users/{user_id}/permissions")
async def update_user_permissions(user_id: str, data: dict, user=Depends(get_current_user)):
    """Update all permissions for a user at once."""
    check_perm(user, "settings", "manage_permissions")
    
    target_user = await db.users.find_one({"id": user_id}, {"_id": 0})
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")
    
    permissions = data.get("permissions", {})
    
    await db.users.update_one(
        {"id": user_id},
        {"$set": {
            "permissions": permissions,
            "permission_preset": "custom",
            "updated_at": now_iso()
        }}
    )
    
    return {"message": "Permissions updated", "user_id": user_id}


@router.put("/users/{user_id}/permissions/module/{module}")
async def update_user_module_permissions(user_id: str, module: str, data: dict, user=Depends(get_current_user)):
    """Update permissions for a specific module for a user."""
    check_perm(user, "settings", "manage_permissions")
    
    if module not in PERMISSION_MODULES:
        raise HTTPException(status_code=400, detail=f"Invalid module: {module}")
    
    target_user = await db.users.find_one({"id": user_id}, {"_id": 0})
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Get current permissions or initialize
    permissions = target_user.get("permissions", {})
    permissions[module] = data.get("actions", {})
    
    await db.users.update_one(
        {"id": user_id},
        {"$set": {
            "permissions": permissions,
            "permission_preset": "custom",
            "updated_at": now_iso()
        }}
    )
    
    return {"message": f"Module {module} permissions updated", "module": module, "actions": permissions[module]}

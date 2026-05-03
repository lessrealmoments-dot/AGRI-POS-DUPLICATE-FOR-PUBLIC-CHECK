"""
Authentication routes: login (email or username), register, password management, TOTP.
"""
from fastapi import APIRouter, Depends, HTTPException
import pyotp
import jwt
import secrets
from datetime import datetime, timezone
from config import db, _raw_db, JWT_SECRET
from utils import (
    hash_password, verify_password, create_token,
    get_current_user, check_perm, now_iso, new_id
)
from models import DEFAULT_PERMISSIONS

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/login")
async def login(data: dict):
    """Authenticate user by email only (username no longer supported for new accounts)."""
    email = (data.get("email") or data.get("username") or "").strip().lower()
    password = data.get("password", "")

    if not email:
        raise HTTPException(status_code=400, detail="Email is required")

    # Super admin — separate portal enforced (but still works here for backward compat)
    user = await _raw_db.users.find_one(
        {"$or": [{"email": email}, {"username": email}], "is_super_admin": True},
        {"_id": 0}
    )

    if not user:
        # Regular user — email only
        user = await _raw_db.users.find_one(
            {"$or": [{"email": email}, {"username": email}], "active": True},
            {"_id": 0}
        )

    if not user or not verify_password(password, user.get("password_hash", "")):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not user.get("active", True):
        raise HTTPException(status_code=401, detail="Account is inactive")

    org_id = user.get("organization_id")
    is_super = user.get("is_super_admin", False)

    # Check org subscription (skip for super admin and demo users)
    if org_id and not is_super:
        org = await _raw_db.organizations.find_one({"id": org_id}, {"_id": 0})
        if org and org.get("plan") == "suspended":
            raise HTTPException(
                status_code=403,
                detail="Your organization's subscription is suspended. Please contact support."
            )

    token = create_token(user["id"], user["role"], org_id=org_id, is_super_admin=is_super)
    _SENSITIVE = ("password_hash", "staff_pin", "manager_pin", "auditor_pin", "totp_secret")
    safe_user = {k: v for k, v in user.items() if k not in _SENSITIVE}
    # Expose PIN/TOTP status flags only (never the raw values)
    safe_user["has_manager_pin"] = bool(user.get("manager_pin"))
    safe_user["has_staff_pin"] = bool(user.get("staff_pin"))
    safe_user["has_auditor_pin"] = bool(user.get("auditor_pin"))
    safe_user["has_totp"] = bool(user.get("totp_secret"))

    # Attach subscription info for the frontend
    subscription = None
    if org_id:
        from routes.organizations import get_effective_plan, PLAN_LIMITS, get_grace_info, get_live_feature_flags
        org = await _raw_db.organizations.find_one({"id": org_id}, {"_id": 0})
        if org:
            effective = get_effective_plan(org)
            grace = get_grace_info(org)
            flags = await get_live_feature_flags()
            subscription = {
                "plan": org.get("plan"),
                "effective_plan": effective,
                "status": org.get("subscription_status"),
                "trial_ends_at": org.get("trial_ends_at"),
                "features": flags.get(effective, flags.get("basic", {})),
                "max_branches": org.get("max_branches", 1),
                "max_users": org.get("max_users", 5),
                "org_name": org.get("name"),
                "grace_info": grace,
            }

    return {"token": token, "user": safe_user, "subscription": subscription}


@router.post("/register")
async def register(data: dict, user=Depends(get_current_user)):
    """Register a new user within the current org (admin only)."""
    check_perm(user, "settings", "manage_users")

    email = data.get("email", "").strip().lower()
    if email:
        existing = await _raw_db.users.find_one({"email": email}, {"_id": 0})
        if existing:
            raise HTTPException(status_code=400, detail="Email already exists")

    existing_uname = await db.users.find_one({"username": data.get("username", "")}, {"_id": 0})
    if existing_uname:
        raise HTTPException(status_code=400, detail="Username already exists")

    role = data.get("role", "cashier")
    new_user = {
        "id": new_id(),
        "username": data.get("username", email.split("@")[0] if email else new_id()[:8]),
        "full_name": data.get("full_name", ""),
        "email": email,
        "password_hash": hash_password(data["password"]),
        "role": role,
        "branch_id": data.get("branch_id"),
        "permissions": data.get("permissions", DEFAULT_PERMISSIONS.get(role, DEFAULT_PERMISSIONS["cashier"])),
        "active": True,
        "created_at": now_iso(),
    }
    await db.users.insert_one(new_user)
    safe = {k: v for k, v in new_user.items() if k not in ("password_hash", "_id")}
    return safe


@router.get("/me")
async def get_me(user=Depends(get_current_user)):
    _SENSITIVE = ("password_hash", "staff_pin", "manager_pin", "auditor_pin", "totp_secret")
    result = {k: v for k, v in user.items() if k not in _SENSITIVE}
    # Expose PIN/TOTP status flags (never the raw values)
    result["has_manager_pin"] = bool(user.get("manager_pin"))
    result["has_staff_pin"] = bool(user.get("staff_pin"))
    result["has_auditor_pin"] = bool(user.get("auditor_pin"))
    result["has_totp"] = bool(user.get("totp_secret"))
    org_id = user.get("organization_id")
    if org_id:
        from routes.organizations import get_effective_plan, PLAN_LIMITS, get_grace_info, get_live_feature_flags
        org = await _raw_db.organizations.find_one({"id": org_id}, {"_id": 0})
        if org:
            effective = get_effective_plan(org)
            grace = get_grace_info(org)
            flags = await get_live_feature_flags()
            result["subscription"] = {
                "plan": org.get("plan"),
                "effective_plan": effective,
                "status": org.get("subscription_status"),
                "trial_ends_at": org.get("trial_ends_at"),
                "features": flags.get(effective, flags.get("basic", {})),
                "max_branches": org.get("max_branches", 1),
                "max_users": org.get("max_users", 5),
                "org_name": org.get("name"),
                "grace_info": grace,
            }
    return result


@router.put("/change-password")
async def change_password(data: dict, user=Depends(get_current_user)):
    if not verify_password(data.get("current_password", ""), user["password_hash"]):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    await db.users.update_one(
        {"id": user["id"]},
        {"$set": {"password_hash": hash_password(data["new_password"])}}
    )
    return {"message": "Password changed"}


@router.put("/update-profile")
async def update_profile(data: dict, user=Depends(get_current_user)):
    """Update email or full_name."""
    update = {}
    if "full_name" in data:
        update["full_name"] = data["full_name"]
    if "email" in data:
        new_email = data["email"].strip().lower()
        existing = await _raw_db.users.find_one(
            {"email": new_email, "id": {"$ne": user["id"]}}, {"_id": 0}
        )
        if existing:
            raise HTTPException(status_code=400, detail="Email already in use")
        update["email"] = new_email
    if update:
        update["updated_at"] = now_iso()
        await db.users.update_one({"id": user["id"]}, {"$set": update})
    return {"message": "Profile updated"}


@router.post("/verify-manager-pin")
async def verify_manager_pin(data: dict, user=Depends(get_current_user)):
    from routes.notifications import create_pin_notification
    from routes.verify import verify_pin_for_action
    import logging
    logger = logging.getLogger("pin_verify")

    pin = data.get("pin", "")
    if not pin:
        raise HTTPException(status_code=400, detail="PIN required")

    action_key = data.get("action_key", "credit_sale_approval")
    logger.info(f"verify-manager-pin called: action={action_key}, user={user.get('full_name', user.get('username', '?'))}, org={user.get('organization_id', 'none')}")

    # Extract branch_id from context for branch-aware PIN checks.
    # NOTE: callers may send `context` as either a dict ({branch_id, type, ...})
    # or a plain descriptive string (e.g. "Daily close 2026-05-01 — Main Warehouse").
    # Previously we called `.get("branch_id")` unconditionally which crashed
    # with AttributeError when the frontend sent a string — observed in the
    # Close Wizard PIN step (reported by user). Guard defensively here.
    raw_context = data.get("context")
    ctx_dict = raw_context if isinstance(raw_context, dict) else {}
    branch_id = ctx_dict.get("branch_id") or data.get("branch_id") or user.get("branch_id")

    verifier = await verify_pin_for_action(pin, action_key, branch_id=branch_id)
    if verifier:
        context = data.get("context")
        if context:
            await create_pin_notification(context, verifier["verifier_id"], verifier["verifier_name"])
        return {
            "valid": True,
            "manager_id": verifier["verifier_id"],
            "manager_name": verifier["verifier_name"],
            "role": verifier.get("method", "admin"),
        }

    return {"valid": False, "detail": "No matching PIN/TOTP found. Check Settings > Security for accepted methods."}


@router.post("/section-override")
async def section_override(data: dict, user=Depends(get_current_user)):
    """
    Grant temporary delegated access to a module for the current user.
    Requires TOTP or admin PIN verification.
    Returns a new JWT token with the delegation embedded.
    """
    module = data.get("module", "")
    pin = data.get("pin", "")

    if not module or not pin:
        raise HTTPException(status_code=400, detail="Module and PIN/TOTP required")

    from routes.verify import verify_pin_for_action
    verifier = await verify_pin_for_action(pin, "section_override")
    if not verifier:
        return {"valid": False, "error": "Invalid PIN or TOTP code"}

    # Build delegation dict: merge existing + new module
    existing_delegations = {}
    # Decode existing token to carry over any prior delegations
    try:
        old_payload = jwt.decode(
            data.get("current_token", ""), JWT_SECRET, algorithms=["HS256"],
            options={"verify_exp": False}
        )
        existing_delegations = old_payload.get("delegations", {})
    except Exception:
        pass

    existing_delegations[module] = {
        "granted_by": verifier["verifier_name"],
        "granted_by_id": verifier["verifier_id"],
        "method": verifier["method"],
        "granted_at": now_iso(),
    }

    # Issue a new token with delegations embedded (same expiry as original)
    org_id = user.get("organization_id")
    is_super = user.get("is_super_admin", False)
    payload = {
        "user_id": user["id"],
        "role": user["role"],
        "exp": datetime.now(timezone.utc).timestamp() + 86400,
        "delegations": existing_delegations,
    }
    if org_id:
        payload["org_id"] = org_id
    if is_super:
        payload["is_super_admin"] = True
    new_token = jwt.encode(payload, JWT_SECRET, algorithm="HS256")

    # Log the delegation
    await db.audit_log.insert_one({
        "id": new_id(),
        "type": "section_override",
        "module": module,
        "user_id": user["id"],
        "user_name": user.get("full_name", user.get("username", "")),
        "granted_by_id": verifier["verifier_id"],
        "granted_by_name": verifier["verifier_name"],
        "method": verifier["method"],
        "created_at": now_iso(),
        "branch_id": user.get("branch_id", ""),
    })

    return {
        "valid": True,
        "token": new_token,
        "module": module,
        "granted_by": verifier["verifier_name"],
        "method": verifier["method"],
        "delegations": existing_delegations,
    }


@router.put("/set-manager-pin")
async def set_manager_pin(data: dict, user=Depends(get_current_user)):
    """Set own PIN — routes to manager_pin or staff_pin based on role."""
    pin = data.get("pin", "")
    if len(pin) < 4:
        raise HTTPException(status_code=400, detail="PIN must be at least 4 digits")
    staff_roles = {"cashier", "staff", "inventory", "inventory_clerk"}
    pin_field = "staff_pin" if user.get("role") in staff_roles else "manager_pin"
    await db.users.update_one({"id": user["id"]}, {"$set": {pin_field: pin}})
    return {"message": "PIN set successfully", "pin_field": pin_field}


@router.put("/change-my-pin")
async def change_my_pin(data: dict, user=Depends(get_current_user)):
    """
    Any user changes their own PIN.
    - admin/manager → manager_pin
    - cashier/inventory/staff → staff_pin
    """
    staff_roles = {"cashier", "staff", "inventory", "inventory_clerk"}
    role = user.get("role", "")
    is_staff_role = role in staff_roles
    is_manager_role = role in {"admin", "manager", "owner"}

    if not is_staff_role and not is_manager_role:
        raise HTTPException(status_code=403, detail="Your role does not support PIN authentication")

    current_pin = data.get("current_pin", "")
    new_pin = data.get("new_pin", "")

    if not new_pin or len(str(new_pin)) < 4:
        raise HTTPException(status_code=400, detail="New PIN must be at least 4 digits")

    pin_field = "staff_pin" if is_staff_role else "manager_pin"
    stored_pin = str(user.get(pin_field, "") or "")

    # If user already has a PIN set, require current PIN to change it
    if stored_pin:
        if not current_pin:
            raise HTTPException(status_code=400, detail="Current PIN is required to change it")
        if current_pin != stored_pin:
            raise HTTPException(status_code=400, detail="Current PIN is incorrect")

    await db.users.update_one(
        {"id": user["id"]},
        {"$set": {
            pin_field: str(new_pin),
            "pin_changed_at": now_iso(),
        }}
    )
    return {"message": "PIN changed successfully", "pin_field": pin_field}


# ── TOTP ──────────────────────────────────────────────────────────────────────

@router.get("/totp/status")
async def get_totp_status(user=Depends(get_current_user)):
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    return {"enabled": bool(user.get("totp_enabled")), "verified": bool(user.get("totp_verified"))}


@router.post("/totp/setup")
async def setup_totp(user=Depends(get_current_user)):
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    secret = pyotp.random_base32()
    totp = pyotp.TOTP(secret)
    provisioning_uri = totp.provisioning_uri(
        name=user.get("email") or user.get("username", "admin"),
        issuer_name="AgriBooks"
    )
    await db.users.update_one(
        {"id": user["id"]},
        {"$set": {"totp_secret": secret, "totp_enabled": False, "totp_verified": False}}
    )
    return {"secret": secret, "qr_uri": provisioning_uri}


@router.post("/totp/verify-setup")
async def verify_totp_setup(data: dict, user=Depends(get_current_user)):
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    code = data.get("code", "")
    fresh = await _raw_db.users.find_one({"id": user["id"]}, {"_id": 0})
    secret = fresh.get("totp_secret") if fresh else None
    if not secret:
        raise HTTPException(status_code=400, detail="No TOTP secret found. Run setup first.")
    totp = pyotp.TOTP(secret)
    if totp.verify(code, valid_window=1):
        await db.users.update_one(
            {"id": user["id"]},
            {"$set": {"totp_enabled": True, "totp_verified": True}}
        )
        return {"verified": True}
    return {"verified": False, "error": "Invalid code — try again"}


@router.delete("/totp/disable")
async def disable_totp(user=Depends(get_current_user)):
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    await db.users.update_one(
        {"id": user["id"]},
        {"$unset": {"totp_secret": "", "totp_enabled": "", "totp_verified": ""}}
    )
    return {"message": "TOTP disabled"}


@router.post("/verify-admin-action")
async def verify_admin_action(data: dict, user=Depends(get_current_user)):
    from routes.notifications import create_pin_notification
    from utils.security import log_failed_pin_attempt
    mode = data.get("mode", "totp")
    code = data.get("code", "")
    context = data.get("context", "")

    if not code:
        raise HTTPException(status_code=400, detail="Code is required")

    admins = await db.users.find({"role": "admin", "active": True}, {"_id": 0}).to_list(20)
    first_admin = admins[0] if admins else None
    admin_name = first_admin.get("full_name", first_admin.get("username", "Admin")) if first_admin else "Admin"
    admin_id = first_admin["id"] if first_admin else "system"

    # ── Owner PIN mode ────────────────────────────────────────────────────────
    if mode == "pin":
        pin_doc = await db.system_settings.find_one({"key": "admin_pin"}, {"_id": 0})
        if pin_doc and pin_doc.get("pin_hash"):
            if verify_password(str(code), pin_doc["pin_hash"]):
                if context and first_admin:
                    await create_pin_notification(context, admin_id, admin_name)
                return {"valid": True, "manager_id": admin_id, "manager_name": admin_name, "mode_used": "pin"}
        await log_failed_pin_attempt(user, context or "Admin action authorization", "admin_action")
        return {"valid": False, "error": "Invalid Owner PIN"}

    for admin in admins:
        # ── TOTP mode ─────────────────────────────────────────────────────────
        if mode == "totp":
            secret = admin.get("totp_secret")
            if secret and admin.get("totp_enabled"):
                totp = pyotp.TOTP(secret)
                if totp.verify(code, valid_window=1):
                    if context:
                        await create_pin_notification(
                            context, admin["id"],
                            admin.get("full_name", admin.get("username", ""))
                        )
                    return {
                        "valid": True,
                        "manager_id": admin["id"],
                        "manager_name": admin.get("full_name", admin.get("username", "")),
                        "mode_used": "totp",
                    }
        # ── Password mode ─────────────────────────────────────────────────────
        elif mode == "password":
            if verify_password(code, admin.get("password_hash", "")):
                if context:
                    await create_pin_notification(
                        context, admin["id"],
                        admin.get("full_name", admin.get("username", ""))
                    )
                return {
                    "valid": True,
                    "manager_id": admin["id"],
                    "manager_name": admin.get("full_name", admin.get("username", "")),
                    "mode_used": "password",
                }

    # All attempts failed — log it
    await log_failed_pin_attempt(user, context or "Admin action authorization", "admin_action")
    if mode == "totp":
        return {"valid": False, "error": "Invalid code — check your authenticator app"}
    return {"valid": False, "error": "Invalid password"}


# ── Password Reset ─────────────────────────────────────────────────────────────

RESET_TOKEN_EXPIRY_SECONDS = 3600  # 1 hour


@router.post("/forgot-password")
async def forgot_password(data: dict):
    """Request a password reset link. Always returns generic success to avoid email enumeration."""
    email = data.get("email", "").strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="Email is required")

    # Use origin sent by the browser (works for both live site and preview environments).
    # Fall back to the env var so server-triggered resets still work.
    request_origin = (data.get("origin") or "").strip().rstrip("/")
    from services.email_service import APP_URL as ENV_APP_URL, send_email, _base
    base_url = request_origin if request_origin else ENV_APP_URL

    user = await _raw_db.users.find_one({"email": email, "active": True}, {"_id": 0})

    if user:
        # Invalidate any existing unexpired tokens for this email
        await _raw_db.password_reset_tokens.delete_many({"email": email, "used": False})

        token = secrets.token_urlsafe(32)
        expires_at = datetime.now(timezone.utc).timestamp() + RESET_TOKEN_EXPIRY_SECONDS

        await _raw_db.password_reset_tokens.insert_one({
            "id": new_id(),
            "token": token,
            "user_id": user["id"],
            "email": email,
            "expires_at": expires_at,
            "used": False,
            "created_at": now_iso(),
        })

        try:
            reset_url = f"{base_url}/reset-password?token={token}"
            html = _base(
                content=f"""
                <h1 style="color:#0f172a;font-size:22px;margin:0 0 8px;">Reset Your Password</h1>
                <p style="color:#475569;font-size:15px;line-height:1.6;">
                  Hi there,<br><br>
                  We received a request to reset the password for your AgriBooks account
                  (<strong>{email}</strong>).<br><br>
                  Click the button below to set a new password. This link is valid for <strong>1 hour</strong>.
                </p>
                <div style="background:#fef9c3;border:1px solid #fde047;border-radius:8px;padding:12px 16px;margin:16px 0;">
                  <p style="color:#854d0e;font-weight:600;margin:0;font-size:13px;">
                    If you did not request a password reset, you can safely ignore this email.
                    Your password will not change.
                  </p>
                </div>
                """,
                cta_url=reset_url,
                cta_label="Reset My Password"
            )
            await send_email(email, "Reset your AgriBooks password", html)
        except Exception:
            import logging
            logging.getLogger("auth").exception("Failed to send password reset email to %s", email)

    # Always return the same message (security: don't reveal if email exists)
    return {"message": "If that email is registered, you'll receive a password reset link shortly."}


@router.post("/reset-password")
async def reset_password(data: dict):
    """Reset a user's password using a valid reset token."""
    token = data.get("token", "").strip()
    new_password = data.get("new_password", "")

    if not token:
        raise HTTPException(status_code=400, detail="Reset token is required")
    if not new_password:
        raise HTTPException(status_code=400, detail="New password is required")
    if len(new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    token_doc = await _raw_db.password_reset_tokens.find_one({"token": token, "used": False})

    if not token_doc:
        raise HTTPException(status_code=400, detail="Invalid or expired reset link. Please request a new one.")

    # Check expiry
    if datetime.now(timezone.utc).timestamp() > token_doc.get("expires_at", 0):
        await _raw_db.password_reset_tokens.delete_one({"token": token})
        raise HTTPException(status_code=400, detail="This reset link has expired. Please request a new one.")

    # Update password
    await _raw_db.users.update_one(
        {"id": token_doc["user_id"]},
        {"$set": {"password_hash": hash_password(new_password), "updated_at": now_iso()}}
    )

    # Consume token (mark as used)
    await _raw_db.password_reset_tokens.update_one(
        {"token": token},
        {"$set": {"used": True, "used_at": now_iso()}}
    )

    return {"message": "Password reset successfully. You can now sign in with your new password."}

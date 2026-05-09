"""
Authentication utilities: password hashing, JWT tokens, permission checking.
"""
import bcrypt
import jwt
from datetime import datetime, timezone
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from config import db, JWT_SECRET, set_org_context

security = HTTPBearer()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


def create_token(user_id: str, role: str, org_id: str = None, is_super_admin: bool = False) -> str:
    payload = {
        "user_id": user_id,
        "role": role,
        "exp": datetime.now(timezone.utc).timestamp() + 86400,  # 24h
    }
    if org_id:
        payload["org_id"] = org_id
    if is_super_admin:
        payload["is_super_admin"] = True
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Dependency: decode JWT, set org context, return user. Also checks for delegation tokens."""
    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

    is_super_admin = payload.get("is_super_admin", False)
    org_id = payload.get("org_id")

    # ── Super-admin tenant impersonation ───────────────────────────────────
    # If a super-admin has an active "View as Tenant" session (set via
    # POST /superadmin/impersonate/{org_id}/enter), scope this request to
    # the target tenant. Auto-expires after 4 hours. Audit-logged.
    impersonating_org_id = None
    if is_super_admin:
        try:
            from config import _raw_db
            from datetime import datetime, timezone
            sess = await _raw_db.impersonation_sessions.find_one(
                {"super_admin_user_id": payload["user_id"], "active": True},
                {"_id": 0},
            )
            if sess:
                expires = sess.get("expires_at", "")
                if expires and expires > datetime.now(timezone.utc).isoformat():
                    impersonating_org_id = sess.get("target_org_id")
                else:
                    # Expired — auto-deactivate
                    await _raw_db.impersonation_sessions.update_one(
                        {"id": sess.get("id")},
                        {"$set": {"active": False, "ended_at": datetime.now(timezone.utc).isoformat(), "ended_reason": "expired"}},
                    )
        except Exception:
            pass  # impersonation table may not exist yet — fall through to super-admin scope

    # Set org context for tenant isolation:
    #   - impersonating super admin → scope to target org (legitimate cross-tenant support)
    #   - normal super admin        → None (fail-closed in TenantCollection)
    #   - regular user              → their own org
    if impersonating_org_id:
        set_org_context(impersonating_org_id)
    else:
        set_org_context(None if is_super_admin else org_id)

    # Find user — login uses no context so this is an unscoped lookup by id
    from config import _raw_db
    user = await _raw_db.users.find_one({"id": payload["user_id"]}, {"_id": 0})
    if not user or not user.get("active", True):
        raise HTTPException(status_code=401, detail="User not found or inactive")

    # ── C-1 related (Audit 2026-02): JWT org cross-check ──────────────────
    # If the user record's organization_id has changed since the token was
    # issued (org migration, manual move, soft-revoke), refuse the token.
    # Super-admins are exempt (they intentionally float).
    if not is_super_admin:
        token_org = payload.get("org_id")
        user_org = user.get("organization_id")
        # Allow legacy tokens that didn't carry org_id IF the user has no
        # org assignment either (single-tenant mode).
        if token_org or user_org:
            if token_org != user_org:
                raise HTTPException(
                    status_code=401,
                    detail="Session organization mismatch — please log in again.",
                )

    # Surface impersonation state to routes (read-only — they cannot change it)
    if impersonating_org_id:
        user["_impersonating_org_id"] = impersonating_org_id

    # Check for delegated module access from JWT payload
    if payload.get("delegations"):
        user["_delegations"] = payload["delegations"]

    return user


def check_perm(user: dict, module: str, action: str):
    if user.get("role") == "admin" or user.get("is_super_admin"):
        return
    module_map = {"pos": "sales"}
    actual_module = module_map.get(module, module)
    action_map = {
        ("pos", "sell"): ("sales", "create"),
        ("accounting", "create"): ("accounting", "receive_payment"),
    }
    if (module, action) in action_map:
        actual_module, action = action_map[(module, action)]
    perms = user.get("permissions", {})
    if not perms.get(actual_module, {}).get(action, False):
        # Check if user has a delegation override for this module
        delegations = user.get("_delegations", {})
        if actual_module in delegations:
            return  # Delegated access granted
        raise HTTPException(status_code=403, detail=f"No permission: {actual_module}.{action}")


def has_perm(user: dict, module: str, action: str) -> bool:
    if user.get("role") == "admin" or user.get("is_super_admin"):
        return True
    module_map = {"pos": "sales"}
    actual_module = module_map.get(module, module)
    perms = user.get("permissions", {})
    return perms.get(actual_module, {}).get(action, False)



def user_branch_ids(user: dict) -> list:
    """Return the branch whitelist for this user.

    Honors the new `branch_ids` list (multi-branch assignment) AND the
    legacy single `branch_id` for backward compat. Empty list means
    "no assignment" — treated as legacy unscoped (full org access),
    matching pre-multi-branch behaviour for users that haven't been
    re-assigned yet.
    """
    if not user:
        return []
    ids = user.get("branch_ids") or []
    if not isinstance(ids, list):
        ids = []
    ids = [b for b in ids if isinstance(b, str) and b.strip()]
    legacy = (user.get("branch_id") or "").strip() if isinstance(user.get("branch_id"), str) else ""
    if legacy and legacy not in ids:
        ids.append(legacy)
    return ids


def assert_branch_access(user: dict, branch_id) -> None:
    """Raise 403 if `user` is not allowed to operate on `branch_id`.

    Rules:
      * admins (role=admin) and super-admins → allowed everywhere
      * users with no branch assignment at all (legacy unscoped) → allowed
        everywhere within their org (preserves pre-multi-branch behaviour)
      * everyone else → branch_id MUST be in `user.branch_ids` (or equal
        to legacy `user.branch_id`)
      * empty / None / 'all' branch_id → no-op (caller is asking for a
        consolidated view; the org-scope tenant proxy will still gate
        cross-org access)

    Use this in any endpoint that accepts a branch_id from the client
    (query param, body field, or path) and would otherwise expose another
    branch's data on a forged request.
    """
    if not branch_id or branch_id == "all":
        return
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    if user.get("role") == "admin" or user.get("is_super_admin"):
        return
    allowed = user_branch_ids(user)
    if not allowed:
        # Legacy unscoped user — no assignment list. Don't block; org
        # tenant proxy already prevents cross-org leak.
        return
    if branch_id not in allowed:
        raise HTTPException(
            status_code=403,
            detail="You're not assigned to this branch. "
                   "Ask an administrator to add it under Team → Edit User.",
        )

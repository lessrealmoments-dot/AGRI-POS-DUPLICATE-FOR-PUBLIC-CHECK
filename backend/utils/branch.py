"""
Multi-Branch utilities: branch context, data isolation, owner views.
"""
from fastapi import Depends, HTTPException
from typing import Optional, List
from config import db


async def get_user_branches(user: dict) -> List[str]:
    """
    Get list of branch IDs the user can access.
    - Admin/Owner: All active branches
    - Multi-branch user (branch_ids list): every branch in their list
    - Single-branch user (legacy branch_id): their one assigned branch
    - Otherwise: empty (caller decides whether to error)
    """
    if user.get("role") == "admin" or user.get("is_owner"):
        branches = await db.branches.find({"active": True}, {"_id": 0, "id": 1}).to_list(100)
        return [b["id"] for b in branches]

    # Multi-branch assignment (new model). Honor `branch_ids` list AND fold
    # in the legacy single `branch_id` so historical data isn't lost.
    ids = user.get("branch_ids") or []
    if not isinstance(ids, list):
        ids = []
    ids = [b for b in ids if isinstance(b, str) and b.strip()]
    legacy = user.get("branch_id")
    if legacy and isinstance(legacy, str) and legacy.strip() and legacy not in ids:
        ids.append(legacy)
    return ids


async def get_branch_filter(user: dict, requested_branch_id: Optional[str] = None) -> dict:
    """
    Build a MongoDB query filter for branch isolation.
    
    Args:
        user: Current authenticated user
        requested_branch_id: Optional specific branch to filter by
    
    Returns:
        Query filter dict to include in MongoDB queries
    """
    allowed_branches = await get_user_branches(user)
    
    if not allowed_branches:
        raise HTTPException(status_code=403, detail="No branch access assigned")
    
    # If specific branch requested, verify access
    if requested_branch_id:
        if requested_branch_id not in allowed_branches:
            raise HTTPException(status_code=403, detail="No access to this branch")
        return {"branch_id": requested_branch_id}
    
    # Admin with all branches - no filter needed for cross-branch views
    if user.get("role") == "admin" or user.get("is_owner"):
        return {}  # No branch restriction
    
    # Regular user - filter to their branch only
    if len(allowed_branches) == 1:
        return {"branch_id": allowed_branches[0]}
    
    return {"branch_id": {"$in": allowed_branches}}


def apply_branch_filter(base_query: dict, branch_filter: dict) -> dict:
    """
    Merge branch filter into an existing query.
    """
    if not branch_filter:
        return base_query
    return {**base_query, **branch_filter}


async def ensure_branch_access(user: dict, branch_id: str) -> bool:
    """
    Check if user has access to a specific branch.
    Raises HTTPException if no access.
    """
    allowed = await get_user_branches(user)
    if branch_id not in allowed:
        raise HTTPException(status_code=403, detail="No access to this branch")
    return True


async def get_default_branch(user: dict) -> Optional[str]:
    """
    Get the user's default branch ID.
    - For regular users: their assigned branch
    - For admin: first active branch
    """
    if user.get("branch_id"):
        return user["branch_id"]
    
    if user.get("role") == "admin":
        branch = await db.branches.find_one({"active": True}, {"_id": 0, "id": 1})
        return branch["id"] if branch else None
    
    return None


# Constants for branch scope
BRANCH_SCOPED_COLLECTIONS = [
    "invoices", "sales", "inventory", "purchase_orders", 
    "expenses", "receivables", "fund_wallets", "movements",
    "sales_log", "daily_closings", "safe_lots", "payables"
]

GLOBAL_COLLECTIONS = [
    "products", "price_schemes", "settings"
]

# Customer scope is configurable - can be global or branch-specific
CUSTOMER_SCOPE = "branch"  # "global" or "branch"

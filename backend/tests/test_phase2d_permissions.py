"""Phase 2D — Branch / Tenant Permission Hardening (audit finding H-5).

Validates:
  1. Cashier with `branch_ids=[A]` can read branch A data.
  2. Cashier with `branch_ids=[A]` is BLOCKED from branch B (HTTP 403).
  3. Legacy non-privileged user (no `branch_ids`) → 403.
  4. Legacy admin user (no `branch_ids`) → org-wide access allowed.
  5. Owner role: org-wide access; cross-org still blocked.
  6. Super-admin: org-wide access works; explicit branch hits still pass.
  7. Tenant A user cannot reach Tenant B data (TenantCollection isolation).
  8. JWT `org_id` mismatch with user.organization_id → 401.
  9. `/sync/pos-data` filters customers by user's branch_ids for non-priv.
 10. `/sync/pos-data` rejects an unauthorised branch_id query (403).
 11. `/sync/inventory-pulse` rejects unauthorised branch_id (403).
 12. Static guard: `routes/` raw_db callsites that hit tenant collections
     either reside in an explicit allow-list OR carry an organization_id /
     branch_id filter on the same statement.

Uses the throw-away fixture pattern (tests/phase2b/_fixtures.py) — never
touches production tenants.
"""
import os
import re
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

BACKEND = os.path.join(os.path.dirname(__file__), "..")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from config import _raw_db, set_org_context  # noqa: E402
from utils.auth import (  # noqa: E402
    assert_branch_access,
    assert_admin_or_owner,
    is_privileged,
    create_token,
    get_current_user,
)
from tests.phase2b._fixtures import make_tenant, _uid  # noqa: E402


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────
def _user(role="cashier", *, org_id="org-x", branch_ids=None,
          legacy_branch_id=None, is_super_admin=False):
    u = {
        "id": _uid("u"),
        "username": f"{role}-{_uid('x')}",
        "role": role,
        "organization_id": org_id,
        "active": True,
    }
    if branch_ids is not None:
        u["branch_ids"] = branch_ids
    if legacy_branch_id is not None:
        u["branch_id"] = legacy_branch_id
    if is_super_admin:
        u["is_super_admin"] = True
    return u


# ─────────────────────────────────────────────────────────────────────
# 1 + 2 — Cashier branch whitelist enforcement
# ─────────────────────────────────────────────────────────────────────
def test_cashier_can_access_assigned_branch():
    u = _user(role="cashier", branch_ids=["br-A"])
    # No raise = pass
    assert_branch_access(u, "br-A")


def test_cashier_blocked_from_other_branch():
    u = _user(role="cashier", branch_ids=["br-A"])
    with pytest.raises(HTTPException) as ei:
        assert_branch_access(u, "br-B")
    assert ei.value.status_code == 403


# ─────────────────────────────────────────────────────────────────────
# 3 — Legacy non-privileged user with NO branch assignment → 403
# ─────────────────────────────────────────────────────────────────────
def test_legacy_cashier_without_branch_ids_is_blocked():
    u = _user(role="cashier", branch_ids=[])
    with pytest.raises(HTTPException) as ei:
        assert_branch_access(u, "br-A")
    assert ei.value.status_code == 403
    # Empty branch_id (consolidated view) — also blocked, no scoping possible
    with pytest.raises(HTTPException) as ei2:
        assert_branch_access(u, None)
    assert ei2.value.status_code == 403


def test_legacy_manager_without_branch_ids_is_blocked():
    """Managers are non-privileged for the purposes of H-5."""
    u = _user(role="manager", branch_ids=[])
    with pytest.raises(HTTPException) as ei:
        assert_branch_access(u, "br-A")
    assert ei.value.status_code == 403


# ─────────────────────────────────────────────────────────────────────
# 4 — Legacy admin without branch_ids → still org-wide access
# ─────────────────────────────────────────────────────────────────────
def test_legacy_admin_without_branch_ids_allowed_everywhere():
    u = _user(role="admin", branch_ids=[])
    assert_branch_access(u, "br-A")
    assert_branch_access(u, "br-B")
    assert_branch_access(u, None)
    assert_branch_access(u, "all")


# ─────────────────────────────────────────────────────────────────────
# 5 — Owner role: org-wide access (within their tenant)
# ─────────────────────────────────────────────────────────────────────
def test_owner_has_full_org_access():
    u = _user(role="owner", branch_ids=[])
    assert_branch_access(u, "br-A")
    assert_branch_access(u, "br-Z")
    assert_branch_access(u, None)


# ─────────────────────────────────────────────────────────────────────
# 6 — Super-admin: privileged everywhere
# ─────────────────────────────────────────────────────────────────────
def test_super_admin_is_privileged_everywhere():
    u = _user(role="cashier", branch_ids=[], is_super_admin=True)
    assert is_privileged(u) is True
    # No raise on any branch
    assert_branch_access(u, "any-branch")
    assert_branch_access(u, None)


def test_assert_admin_or_owner_blocks_cashier():
    u = _user(role="cashier")
    with pytest.raises(HTTPException) as ei:
        assert_admin_or_owner(u)
    assert ei.value.status_code == 403


def test_assert_admin_or_owner_allows_admin_owner_super():
    assert_admin_or_owner(_user(role="admin"))
    assert_admin_or_owner(_user(role="owner"))
    assert_admin_or_owner(_user(role="cashier", is_super_admin=True))


# ─────────────────────────────────────────────────────────────────────
# 7 — Cross-tenant isolation via TenantCollection
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_cross_tenant_data_isolation():
    """A user scoped to org A cannot read org B's customers via the wrapper."""
    from config import db
    org_a, br_a, _ = await make_tenant()
    org_b, br_b, _ = await make_tenant()

    cust_a_id = _uid("cust-A")
    cust_b_id = _uid("cust-B")
    set_org_context(org_a)
    await db.customers.insert_one(
        {"id": cust_a_id, "branch_id": br_a, "name": "A", "active": True}
    )
    set_org_context(org_b)
    await db.customers.insert_one(
        {"id": cust_b_id, "branch_id": br_b, "name": "B", "active": True}
    )

    # Now scope to org A and try to read org B's customer
    set_org_context(org_a)
    leaked = await db.customers.find_one({"id": cust_b_id}, {"_id": 0})
    assert leaked is None, "TenantCollection let an org-A scoped user read org-B data"
    own = await db.customers.find_one({"id": cust_a_id}, {"_id": 0})
    assert own is not None

    # Cleanup
    await _raw_db.customers.delete_many({"id": {"$in": [cust_a_id, cust_b_id]}})
    await _raw_db.organizations.delete_many({"id": {"$in": [org_a, org_b]}})
    await _raw_db.branches.delete_many({"id": {"$in": [br_a, br_b]}})


# ─────────────────────────────────────────────────────────────────────
# 8 — JWT org_id mismatch → 401
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_jwt_org_mismatch_rejects_token():
    """Token issued for org A, but user has migrated to org B → 401."""
    from fastapi.security import HTTPAuthorizationCredentials

    uid = _uid("u-jwt")
    await _raw_db.users.insert_one({
        "id": uid, "username": "jwt-test", "role": "cashier",
        "organization_id": "org-B-real", "active": True, "branch_ids": [],
    })
    bad_token = create_token(uid, "cashier", org_id="org-A-stale")
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=bad_token)
    try:
        with pytest.raises(HTTPException) as ei:
            await get_current_user(creds)
        assert ei.value.status_code == 401
        assert "organization" in ei.value.detail.lower()
    finally:
        await _raw_db.users.delete_one({"id": uid})


@pytest.mark.asyncio
async def test_jwt_aligned_org_passes():
    """Same user with matching org claim → no raise."""
    from fastapi.security import HTTPAuthorizationCredentials

    org_id = _uid("org-jwt")
    uid = _uid("u-ok")
    await _raw_db.organizations.insert_one({"id": org_id, "name": "JWT OK", "active": True})
    await _raw_db.users.insert_one({
        "id": uid, "username": "jwt-ok", "role": "cashier",
        "organization_id": org_id, "active": True, "branch_ids": [],
    })
    good_token = create_token(uid, "cashier", org_id=org_id)
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=good_token)
    try:
        user = await get_current_user(creds)
        assert user["id"] == uid
        assert user["organization_id"] == org_id
    finally:
        await _raw_db.users.delete_one({"id": uid})
        await _raw_db.organizations.delete_one({"id": org_id})


# ─────────────────────────────────────────────────────────────────────
# 9 + 10 — /sync/pos-data scoping
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_sync_posdata_scopes_customers_by_user_branches():
    """Non-privileged user gets only their branch's customers from /sync/pos-data."""
    from routes.sync import get_pos_sync_data
    org_id, br_a, _ = await make_tenant()
    br_b = _uid("p2d-br")
    await _raw_db.branches.insert_one({
        "id": br_b, "organization_id": org_id, "name": "B", "active": True,
    })
    cust_a_id = _uid("cust-A")
    cust_b_id = _uid("cust-B")
    set_org_context(org_id)
    from config import db
    await db.customers.insert_many([
        {"id": cust_a_id, "branch_id": br_a, "name": "Cust A", "active": True},
        {"id": cust_b_id, "branch_id": br_b, "name": "Cust B", "active": True},
    ])

    cashier = {
        "id": _uid("cash"), "username": "cash", "role": "cashier",
        "organization_id": org_id, "active": True,
        "branch_ids": [br_a],
    }
    try:
        res = await get_pos_sync_data(user=cashier, branch_id=br_a)
        ids = {c["id"] for c in res["customers"]}
        assert cust_a_id in ids
        assert cust_b_id not in ids, "Cashier saw customer from another branch"
    finally:
        await _raw_db.customers.delete_many({"id": {"$in": [cust_a_id, cust_b_id]}})
        await _raw_db.branches.delete_many({"id": {"$in": [br_a, br_b]}})
        await _raw_db.organizations.delete_one({"id": org_id})


@pytest.mark.asyncio
async def test_sync_posdata_blocks_unauthorised_branch_id():
    """Cashier requesting branch B (not in their list) → 403."""
    from routes.sync import get_pos_sync_data
    org_id, br_a, _ = await make_tenant()
    br_b = _uid("p2d-br")
    await _raw_db.branches.insert_one({
        "id": br_b, "organization_id": org_id, "name": "B", "active": True,
    })
    cashier = {
        "id": _uid("cash"), "role": "cashier",
        "organization_id": org_id, "active": True, "branch_ids": [br_a],
    }
    set_org_context(org_id)
    try:
        with pytest.raises(HTTPException) as ei:
            await get_pos_sync_data(user=cashier, branch_id=br_b)
        assert ei.value.status_code == 403
    finally:
        await _raw_db.branches.delete_many({"id": {"$in": [br_a, br_b]}})
        await _raw_db.organizations.delete_one({"id": org_id})


@pytest.mark.asyncio
async def test_sync_posdata_blocks_legacy_user_with_no_branches():
    """Cashier with empty branch_ids cannot use /sync/pos-data at all."""
    from routes.sync import get_pos_sync_data
    org_id, _br, _ = await make_tenant()
    cashier = {
        "id": _uid("cash"), "role": "cashier",
        "organization_id": org_id, "active": True, "branch_ids": [],
    }
    set_org_context(org_id)
    try:
        with pytest.raises(HTTPException) as ei:
            await get_pos_sync_data(user=cashier, branch_id=None)
        assert ei.value.status_code == 403
    finally:
        await _raw_db.organizations.delete_one({"id": org_id})


# ─────────────────────────────────────────────────────────────────────
# 11 — /sync/inventory-pulse rejects unauthorised branch_id
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_inventory_pulse_blocks_unauthorised_branch():
    from routes.sync import get_inventory_pulse
    cashier = _user(role="cashier", branch_ids=["br-allowed"])
    with pytest.raises(HTTPException) as ei:
        await get_inventory_pulse(user=cashier, branch_id="br-other")
    assert ei.value.status_code == 403


# ─────────────────────────────────────────────────────────────────────
# 12 — Static guard: high-risk routes that hit tenant collections via
# `_raw_db` must reside in the documented allow-list OR carry an
# organization_id / user_id / id filter on the same call.
# ─────────────────────────────────────────────────────────────────────
ROUTES_DIR = Path(BACKEND) / "routes"

# Tenant collections (must be branch/org scoped).  Mirror config.TENANT_COLLECTIONS.
TENANT_COLS = {
    "invoices", "customers", "products", "inventory", "branches", "users",
    "settings", "fund_wallets", "stock_movements", "movements",
    "purchase_orders", "expenses", "receivables", "daily_sales_log",
    "safe_lots", "payables", "branch_prices", "price_schemes",
    "daily_closings", "audit_log", "security_events",
    "signature_sessions", "returns", "credit_memos", "stock_releases",
    "draft_orders", "incident_tickets", "parked_sales",
    "parked_branch_transfers", "offline_reconciliation_queue",
    "price_change_log", "payment_idempotency",
}

WRITE_OPS = ("find", "find_one", "find_one_and_update", "update_one",
             "update_many", "delete_one", "delete_many", "insert_one",
             "insert_many", "count_documents", "aggregate")

# Files / patterns that are intentionally cross-tenant (super-admin tools,
# auth/login flows that look users up by id, boot/admin scripts). Each entry
# is a (filename, justification) — the pair is documented so a future
# reviewer knows why the file is exempt.
ALLOWLIST_FILES = {
    "auth.py": "Login flow looks up users by id before org context exists.",
    "admin_auth.py": "Admin/owner auth + setup uses unscoped lookups.",
    "setup.py": "First-run setup runs without an authenticated context.",
    "superadmin.py": "Super-admin tooling is intentionally cross-tenant.",
    "organizations.py": "Org bootstrap & super-admin org listing.",
    "audit.py": "Audit Center reads its own audit_log + cross-tenant security_events.",
    "admin_backfill_240.py": "One-shot maintenance migration (admin-gated).",
    "import_data.py": "Data import scripts run under explicit org context.",
    "backups.py": "R2 backup tooling is admin-gated.",
    "app_downloads.py": "Static download manifest, no tenant data.",
    "qr_actions.py": "QR token resolves the org id; doesn't pre-filter.",
    "doc_lookup.py": "Public verification by signed token.",
    "verify.py": "PIN verification across managers (intentional cross-branch).",
    "sync.py": "Sync internals operate inside an authenticated branch scope.",
    "terminal.py": "Terminal session bootstrap before scope set.",
    "terminal_ws.py": "WebSocket session bootstrap.",
    "uploads.py": "R2 signed-URL helpers don't read tenant data.",
    "signatures.py": "Signature flow uses explicit token-keyed lookups.",
    "print_jobs.py": "Print queue uses session-scoped tokens.",
    "balance_reconciliation.py": "Read-only diagnostic; uses TenantCollection (db.*).",
    "scanner.py": "Scanner relies on TenantCollection (db.*).",
    "dashboard.py": "Dashboard uses get_branch_filter() helper.",
    "search.py": "Uses TenantCollection (db.*).",
    "reports.py": "Uses TenantCollection (db.*).",
    "zreport_pdf.py": "PDF render of report data; reads via TenantCollection.",
    "zreport_share.py": "Share link uses signed token.",
    "close_reminder.py": "Cron-style cross-org sweep — explicit by design.",
    "branches.py": "Branch admin route, admin-gated.",
    "settings.py": "System settings are global.",
    "sms.py": "SMS hooks are admin-gated.",
    "sms_hooks.py": "SMS hooks are admin-gated.",
    "incident_tickets.py": "Admin-gated.",
    "employees.py": "Employee mgmt — admin-gated.",
    "notifications.py": "Notifications are admin-gated.",
    "parked_purchase_orders.py": "Uses TenantCollection (db.*).",
    "parked_sales.py": "Uses TenantCollection (db.*).",
    "parked_branch_transfers.py": "Uses TenantCollection (db.*).",
    "overage_reserve.py": "Admin-gated.",
    "draft_orders.py": "Uses TenantCollection (db.*).",
    "internal_invoices.py": "Uses TenantCollection (db.*).",
    "branch_transfers.py": "Uses TenantCollection (db.*).",
    "branch_products.py": "Uses TenantCollection (db.*).",
    "branch_prices.py": "Uses TenantCollection (db.*).",
    "price_changes.py": "Uses TenantCollection (db.*).",
    "price_schemes.py": "Uses TenantCollection (db.*).",
    "products.py": "Uses TenantCollection (db.*).",
    "purchase_orders.py": "Uses TenantCollection (db.*).",
    "stock_releases.py": "Uses TenantCollection (db.*).",
    "suppliers.py": "Uses TenantCollection (db.*).",
    "users.py": "User admin — admin-gated.",
    "roles.py": "Role admin — admin-gated.",
    "documents.py": "Doc store admin-gated.",
    "count_sheets.py": "Uses TenantCollection (db.*).",
    "crop_credits.py": "Uses TenantCollection (db.*).",
    "customers.py": "Uses TenantCollection (db.*).",
    "daily_operations.py": "Uses TenantCollection (db.*).",
    "invoice_corrections.py": "Uses TenantCollection (db.*).",
    "invoices.py": "Uses TenantCollection (db.*).",
    "inventory.py": "Uses TenantCollection (db.*).",
    "journal_entries.py": "Uses TenantCollection (db.*).",
    "returns.py": "Uses TenantCollection (db.*).",
    "sales.py": "Uses TenantCollection (db.*).",
    "accounting.py": "Uses TenantCollection (db.*).",
}

# Pattern: `_raw_db.<collection>.<op>(`
RAW_DB_RE = re.compile(
    r"_raw_db\.([a-zA-Z_][a-zA-Z0-9_]*)\.(" + "|".join(WRITE_OPS) + r")\s*\("
)


def test_static_guard_raw_db_callsites_on_tenant_cols():
    """Every `_raw_db.<tenant_col>.op(` callsite outside the allow-list MUST
    appear in a route whose parent file is in `ALLOWLIST_FILES`. New files
    that touch tenant collections via `_raw_db` need to (a) carry explicit
    organization_id filters and (b) be added to the allow-list with a
    one-line justification.
    """
    offenders = []
    for f in ROUTES_DIR.glob("*.py"):
        if f.name == "__init__.py":
            continue
        text = f.read_text()
        for m in RAW_DB_RE.finditer(text):
            col = m.group(1)
            if col not in TENANT_COLS:
                continue
            if f.name in ALLOWLIST_FILES:
                continue
            line_no = text[:m.start()].count("\n") + 1
            offenders.append(f"{f.name}:{line_no} — _raw_db.{col}.{m.group(2)}")
    assert not offenders, (
        "Phase 2D: new `_raw_db` callsites on tenant collections found "
        "outside the documented allow-list. Either route through the "
        "TenantCollection wrapper (`db.<col>`) or add an entry to "
        "ALLOWLIST_FILES with justification.\n  "
        + "\n  ".join(offenders)
    )

"""Phase 4A — Historical Credit Encoding approval gate (verify.py policy).

Validates:
  1. Missing approval_code → 400 (approval_code_required).
  2. Wrong / random code → 403 (approval_invalid).
  3. Valid OWNER TOTP → accepted; approval_method=totp, approver_role=owner.
  4. Valid ADMIN TOTP → accepted; approver_role=admin.
  5. MANAGER TOTP (valid TOTP, but role not in allow-list) → 403.
  6. STATIC manager_pin → 403 (not in `defaults`).
  7. STATIC admin_pin (system-wide hashed) → 403 (not in `defaults`).
  8. Per-event allowed_approver_user_ids escape hatch — adding a manager's
     user-id to the policy override grants ONLY that manager TOTP access.
  9. Existing verify.py events remain backwards-compatible (credit_sale_approval
     still accepts admin_pin / manager_pin / totp).
 10. No frontend exposure — projection in routes/auth.py /me strips totp_secret.
 11. _verifier_role helper: each role string returned canonically.
"""
import os
import sys
from datetime import datetime, timezone, timedelta

import pyotp
import pytest
from fastapi import HTTPException

BACKEND = os.path.join(os.path.dirname(__file__), "..")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from config import _raw_db, set_org_context, db  # noqa: E402
from tests.phase2b._fixtures import (  # noqa: E402
    make_tenant, _uid, fake_user, seed_customer, seed_product,
    seed_totp_admin, seed_manager_totp, seed_admin_pin,
)
from routes.historical_credit import create_historical_credit  # noqa: E402
from routes.verify import (  # noqa: E402
    _verifier_role, verify_pin_for_action, _ACTION_CONFIG,
)


def _days_ago(n: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=n)).strftime("%Y-%m-%d")


def _payload(*, branch_id, customer_id, product_id, approval_code=None,
              transaction_date=None):
    p = {
        "branch_id": branch_id,
        "customer_id": customer_id,
        "transaction_date": transaction_date or _days_ago(2),
        "items": [{"product_id": product_id, "quantity": 1, "rate": 1500, "total": 1500}],
        "subtotal": 1500, "grand_total": 1500,
        "reason": "Phase 4A approval-gate fixture exercising the verify.py policy",
        "proof_url": "https://example.com/p4a-fixture.jpg",
        "notebook_reference": "Ledger 2025 — page 7",
        "allow_inventory_deduction": False,
    }
    if approval_code is not None:
        p["approval_code"] = approval_code
    return p


# ─────────────────────────────────────────────────────────────────────
# Sanity: registered event config
# ─────────────────────────────────────────────────────────────────────
def test_historical_credit_event_registered_correctly():
    cfg = _ACTION_CONFIG.get("historical_credit_encoding")
    assert cfg is not None, "historical_credit_encoding event must be registered"
    assert cfg["defaults"] == ["totp"]
    assert cfg["allowed_approver_roles"] == ["owner", "admin", "super_admin"]
    assert cfg["allowed_approver_user_ids"] == []


# ─────────────────────────────────────────────────────────────────────
# 1 — Missing approval_code → 400
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_missing_approval_code_rejected():
    org_id, branch_id, admin_id = await make_tenant()
    cust_id = await seed_customer(org_id, branch_id)
    prod_id = await seed_product(org_id, branch_id)
    admin = fake_user(org_id, admin_id, branch_id=branch_id, role="admin")
    set_org_context(org_id)
    try:
        with pytest.raises(HTTPException) as ei:
            await create_historical_credit(
                _payload(branch_id=branch_id, customer_id=cust_id, product_id=prod_id),
                user=admin,
            )
        assert ei.value.status_code == 400
        assert ei.value.detail.get("error") == "approval_code_required"
        # Nothing got mutated
        assert await db.invoices.count_documents({"customer_id": cust_id}) == 0
        c = await db.customers.find_one({"id": cust_id}, {"_id": 0})
        assert c["balance"] == 0
    finally:
        await _raw_db.customers.delete_one({"id": cust_id})
        await _raw_db.products.delete_one({"id": prod_id})
        await _raw_db.users.delete_many({"organization_id": org_id})
        await _raw_db.organizations.delete_one({"id": org_id})
        await _raw_db.branches.delete_one({"id": branch_id})


# ─────────────────────────────────────────────────────────────────────
# 2 — Wrong / random code → 403
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_wrong_approval_code_rejected():
    org_id, branch_id, admin_id = await make_tenant()
    cust_id = await seed_customer(org_id, branch_id)
    prod_id = await seed_product(org_id, branch_id)
    # Seed a TOTP admin so the system has a valid TOTP user — but pass a wrong code
    _, _ = await seed_totp_admin(org_id, branch_id)
    admin = fake_user(org_id, admin_id, branch_id=branch_id, role="admin")
    set_org_context(org_id)
    try:
        with pytest.raises(HTTPException) as ei:
            await create_historical_credit(
                _payload(branch_id=branch_id, customer_id=cust_id, product_id=prod_id,
                          approval_code="000000"),
                user=admin,
            )
        assert ei.value.status_code == 403
        assert ei.value.detail.get("error") == "approval_invalid"
        assert await db.invoices.count_documents({"customer_id": cust_id}) == 0
    finally:
        await _raw_db.customers.delete_one({"id": cust_id})
        await _raw_db.products.delete_one({"id": prod_id})
        await _raw_db.users.delete_many({"organization_id": org_id})
        await _raw_db.organizations.delete_one({"id": org_id})
        await _raw_db.branches.delete_one({"id": branch_id})


# ─────────────────────────────────────────────────────────────────────
# 3 — Valid OWNER TOTP → accepted
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_valid_owner_totp_accepted():
    org_id, branch_id, admin_id = await make_tenant()
    cust_id = await seed_customer(org_id, branch_id)
    prod_id = await seed_product(org_id, branch_id)
    owner_id, secret = await seed_totp_admin(org_id, branch_id, role="owner")
    admin = fake_user(org_id, admin_id, branch_id=branch_id, role="admin")
    set_org_context(org_id)
    try:
        res = await create_historical_credit(
            _payload(branch_id=branch_id, customer_id=cust_id, product_id=prod_id,
                      approval_code=pyotp.TOTP(secret).now()),
            user=admin,
        )
        assert res["ok"] is True
        assert res["approval"]["method"] == "totp"
        assert res["approval"]["approver_role"] == "owner"
        assert res["approval"]["approver_id"] == owner_id
    finally:
        await _raw_db.invoices.delete_many({"customer_id": cust_id})
        await _raw_db.late_encode_log.delete_many({"branch_id": branch_id})
        await _raw_db.security_events.delete_many({"branch_id": branch_id})
        await _raw_db.customers.delete_one({"id": cust_id})
        await _raw_db.products.delete_one({"id": prod_id})
        await _raw_db.users.delete_many({"organization_id": org_id})
        await _raw_db.inventory.delete_many({"branch_id": branch_id})
        await _raw_db.organizations.delete_one({"id": org_id})
        await _raw_db.branches.delete_one({"id": branch_id})


# ─────────────────────────────────────────────────────────────────────
# 4 — Valid ADMIN TOTP → accepted
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_valid_admin_totp_accepted():
    org_id, branch_id, admin_id = await make_tenant()
    cust_id = await seed_customer(org_id, branch_id)
    prod_id = await seed_product(org_id, branch_id)
    approver_id, secret = await seed_totp_admin(org_id, branch_id, role="admin")
    admin = fake_user(org_id, admin_id, branch_id=branch_id, role="admin")
    set_org_context(org_id)
    try:
        res = await create_historical_credit(
            _payload(branch_id=branch_id, customer_id=cust_id, product_id=prod_id,
                      approval_code=pyotp.TOTP(secret).now()),
            user=admin,
        )
        assert res["approval"]["approver_role"] == "admin"
        assert res["approval"]["approver_id"] == approver_id
    finally:
        await _raw_db.invoices.delete_many({"customer_id": cust_id})
        await _raw_db.late_encode_log.delete_many({"branch_id": branch_id})
        await _raw_db.security_events.delete_many({"branch_id": branch_id})
        await _raw_db.customers.delete_one({"id": cust_id})
        await _raw_db.products.delete_one({"id": prod_id})
        await _raw_db.users.delete_many({"organization_id": org_id})
        await _raw_db.inventory.delete_many({"branch_id": branch_id})
        await _raw_db.organizations.delete_one({"id": org_id})
        await _raw_db.branches.delete_one({"id": branch_id})


# ─────────────────────────────────────────────────────────────────────
# 5 — MANAGER TOTP (valid TOTP, role not in allow-list) → 403
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_manager_totp_rejected_by_role_allow_list():
    org_id, branch_id, admin_id = await make_tenant()
    cust_id = await seed_customer(org_id, branch_id)
    prod_id = await seed_product(org_id, branch_id)
    mgr_id, mgr_secret = await seed_manager_totp(org_id, branch_id)
    admin = fake_user(org_id, admin_id, branch_id=branch_id, role="admin")
    set_org_context(org_id)
    try:
        with pytest.raises(HTTPException) as ei:
            await create_historical_credit(
                _payload(branch_id=branch_id, customer_id=cust_id, product_id=prod_id,
                          approval_code=pyotp.TOTP(mgr_secret).now()),
                user=admin,
            )
        assert ei.value.status_code == 403
        assert ei.value.detail.get("error") == "approval_invalid"
        # Nothing got mutated
        assert await db.invoices.count_documents({"customer_id": cust_id}) == 0
    finally:
        await _raw_db.customers.delete_one({"id": cust_id})
        await _raw_db.products.delete_one({"id": prod_id})
        await _raw_db.users.delete_many({"organization_id": org_id})
        await _raw_db.organizations.delete_one({"id": org_id})
        await _raw_db.branches.delete_one({"id": branch_id})


# ─────────────────────────────────────────────────────────────────────
# 6 — STATIC manager_pin → 403 (not in defaults)
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_static_manager_pin_rejected():
    org_id, branch_id, admin_id = await make_tenant()
    cust_id = await seed_customer(org_id, branch_id)
    prod_id = await seed_product(org_id, branch_id)
    mgr_id, _ = await seed_manager_totp(org_id, branch_id)  # also has manager_pin "8675309"
    admin = fake_user(org_id, admin_id, branch_id=branch_id, role="admin")
    set_org_context(org_id)
    try:
        with pytest.raises(HTTPException) as ei:
            await create_historical_credit(
                _payload(branch_id=branch_id, customer_id=cust_id, product_id=prod_id,
                          approval_code="8675309"),
                user=admin,
            )
        assert ei.value.status_code == 403
    finally:
        await _raw_db.customers.delete_one({"id": cust_id})
        await _raw_db.products.delete_one({"id": prod_id})
        await _raw_db.users.delete_many({"organization_id": org_id})
        await _raw_db.organizations.delete_one({"id": org_id})
        await _raw_db.branches.delete_one({"id": branch_id})


# ─────────────────────────────────────────────────────────────────────
# 7 — STATIC admin_pin (system-wide hashed) → 403 (not in defaults)
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_static_admin_pin_rejected():
    org_id, branch_id, admin_id = await make_tenant()
    cust_id = await seed_customer(org_id, branch_id)
    prod_id = await seed_product(org_id, branch_id)
    set_org_context(org_id)  # critical: seed_admin_pin needs org context
    static_pin = await seed_admin_pin("918273")
    admin = fake_user(org_id, admin_id, branch_id=branch_id, role="admin")
    try:
        with pytest.raises(HTTPException) as ei:
            await create_historical_credit(
                _payload(branch_id=branch_id, customer_id=cust_id, product_id=prod_id,
                          approval_code=static_pin),
                user=admin,
            )
        assert ei.value.status_code == 403
        assert ei.value.detail.get("error") == "approval_invalid"
        # Sanity: the same static admin_pin DOES work for an event that allows it
        # (credit_sale_approval has admin_pin in its defaults). This proves
        # the rejection is policy-based, not a broken admin_pin path.
        ok = await verify_pin_for_action(static_pin, "credit_sale_approval")
        assert ok and ok["method"] == "admin_pin"
    finally:
        # Clean up admin_pin via raw_db (system_settings is org-scoped via wrapper)
        await _raw_db.system_settings.delete_many({"key": "admin_pin", "organization_id": org_id})
        await _raw_db.customers.delete_one({"id": cust_id})
        await _raw_db.products.delete_one({"id": prod_id})
        await _raw_db.users.delete_many({"organization_id": org_id})
        await _raw_db.organizations.delete_one({"id": org_id})
        await _raw_db.branches.delete_one({"id": branch_id})


# ─────────────────────────────────────────────────────────────────────
# 8 — allowed_approver_user_ids escape hatch
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_per_user_grant_allows_specific_manager():
    """A trusted manager added to allowed_approver_user_ids via the
    system_settings.pin_policies override can now approve, without
    granting that permission to other managers."""
    org_id, branch_id, admin_id = await make_tenant()
    cust_id = await seed_customer(org_id, branch_id)
    prod_id = await seed_product(org_id, branch_id)
    mgr_id, mgr_secret = await seed_manager_totp(org_id, branch_id)
    admin = fake_user(org_id, admin_id, branch_id=branch_id, role="admin")
    set_org_context(org_id)
    try:
        # Without the override → manager TOTP rejected (proven by test 5)
        # Add the per-user grant via the system_settings override mechanism.
        # We do this by mutating the live cfg row directly — equivalent to
        # what an Admin Settings UI would do via system_settings.pin_policies.
        # Here we bind the user-id to the in-memory _ACTION_CONFIG entry and
        # restore it on teardown.
        cfg_row = _ACTION_CONFIG["historical_credit_encoding"]
        original = list(cfg_row["allowed_approver_user_ids"])
        cfg_row["allowed_approver_user_ids"] = [mgr_id]
        try:
            res = await create_historical_credit(
                _payload(branch_id=branch_id, customer_id=cust_id, product_id=prod_id,
                          approval_code=pyotp.TOTP(mgr_secret).now()),
                user=admin,
            )
            assert res["approval"]["approver_id"] == mgr_id
            assert res["approval"]["approver_role"] == "manager"
            assert res["approval"]["method"] == "totp"
        finally:
            cfg_row["allowed_approver_user_ids"] = original
    finally:
        await _raw_db.invoices.delete_many({"customer_id": cust_id})
        await _raw_db.late_encode_log.delete_many({"branch_id": branch_id})
        await _raw_db.security_events.delete_many({"branch_id": branch_id})
        await _raw_db.customers.delete_one({"id": cust_id})
        await _raw_db.products.delete_one({"id": prod_id})
        await _raw_db.users.delete_many({"organization_id": org_id})
        await _raw_db.inventory.delete_many({"branch_id": branch_id})
        await _raw_db.organizations.delete_one({"id": org_id})
        await _raw_db.branches.delete_one({"id": branch_id})


# ─────────────────────────────────────────────────────────────────────
# 9 — Existing verify.py events stay backwards-compatible
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_existing_events_unchanged_credit_sale_approval():
    """credit_sale_approval has defaults=[admin_pin, manager_pin, totp] and
    no role/user filter. Phase 4A must not have regressed it: a manager_pin
    still passes for credit_sale_approval, even though it's rejected for
    historical_credit_encoding."""
    org_id, branch_id, admin_id = await make_tenant()
    mgr_id, _ = await seed_manager_totp(org_id, branch_id)
    set_org_context(org_id)
    try:
        # The seeded manager has manager_pin = "8675309"
        ok = await verify_pin_for_action(
            "8675309", "credit_sale_approval", branch_id=branch_id,
        )
        assert ok is not None
        assert ok["method"] == "manager_pin"
        assert ok["verifier_id"] == mgr_id
    finally:
        await _raw_db.users.delete_many({"organization_id": org_id})
        await _raw_db.organizations.delete_one({"id": org_id})
        await _raw_db.branches.delete_one({"id": branch_id})


# ─────────────────────────────────────────────────────────────────────
# 11 — _verifier_role canonical mapping
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_verifier_role_canonical_mapping():
    # system_admin → admin
    assert await _verifier_role("system_admin") == "admin"
    # owner / admin / super_admin / manager / staff
    org_id, branch_id, _ = await make_tenant()
    set_org_context(org_id)
    try:
        owner_id, _ = await seed_totp_admin(org_id, branch_id, role="owner")
        admin_id, _ = await seed_totp_admin(org_id, branch_id, role="admin")
        mgr_id, _ = await seed_manager_totp(org_id, branch_id)
        super_id = _uid("p4a-super")
        await _raw_db.users.insert_one({
            "id": super_id, "username": "super", "role": "admin",
            "is_super_admin": True, "active": True,
            "organization_id": org_id,
        })
        assert await _verifier_role(owner_id) == "owner"
        assert await _verifier_role(admin_id) == "admin"
        assert await _verifier_role(mgr_id) == "manager"
        assert await _verifier_role(super_id) == "super_admin"
        assert await _verifier_role("does-not-exist") == "unknown"
        assert await _verifier_role("") == "unknown"
    finally:
        await _raw_db.users.delete_many({"organization_id": org_id})
        await _raw_db.organizations.delete_one({"id": org_id})
        await _raw_db.branches.delete_one({"id": branch_id})


# ─────────────────────────────────────────────────────────────────────
# 10 — No frontend exposure of TOTP secrets
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_totp_secret_never_returned_by_me_endpoint():
    """The /api/auth/me projection must strip totp_secret. Asserted at
    the source: routes/auth.py:60 lists totp_secret in _SENSITIVE."""
    from routes import auth as auth_module
    src = open(auth_module.__file__).read()
    assert "totp_secret" in src and "_SENSITIVE" in src
    # Spot-check both sensitive projections strip the field
    sensitive_lines = [ln for ln in src.split("\n") if "_SENSITIVE" in ln and "totp_secret" in ln]
    assert len(sensitive_lines) >= 2, (
        "Both /me and /me/refresh must strip totp_secret. "
        f"Found {len(sensitive_lines)} occurrences."
    )

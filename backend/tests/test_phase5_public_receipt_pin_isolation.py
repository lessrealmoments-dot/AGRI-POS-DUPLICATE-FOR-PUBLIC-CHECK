"""Phase 5 — Public Receipt PIN cross-tenant isolation regression.

Locks in the security guarantee surfaced by the Feb-2026 deployment-readiness
audit on `routes/qr_actions.py` + `routes/doc_lookup.py` + `routes/verify.py`:

  Given Company A has issued a receipt with a QR `doc_code`, a PIN belonging
  to Company B (even if structurally similar to A's PIN) MUST NOT unlock
  Company A's PIN-gated receipt data. Conversely, Company A's own valid PIN
  MUST unlock A's receipt. The open (no-PIN) view MUST still expose basic
  receipt info as documented and MUST NOT leak the PIN-gated fields
  (payments, customer balance, attached files).

  This test exercises the live request flow through `routes.doc_lookup`:
    1. lookup_document() with Company B's admin_pin    → 403
    2. lookup_document() with Company B's manager_pin  → 403
    3. lookup_document() with a totally random PIN     → 403
    4. lookup_document() with Company A's admin_pin    → 200 + payments + customer
    5. view_document_open() (no PIN)                   → 200 + invoice fields, NO payments

Uses the throw-away phase2b fixture pattern; never touches production data.
"""
import os
import sys
import uuid

import pytest
from fastapi import HTTPException

BACKEND = os.path.join(os.path.dirname(__file__), "..")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from config import _raw_db, set_org_context  # noqa: E402
from utils import hash_password, now_iso, new_id  # noqa: E402
from tests.phase2b._fixtures import _uid  # noqa: E402

from routes.doc_lookup import lookup_document, view_document_open  # noqa: E402


# ─────────────────────────────────────────────────────────────────────
# Fixture helpers — bypass tenant proxy with _raw_db inserts so each
# tenant's PIN config is unambiguously scoped to its own org.
# ─────────────────────────────────────────────────────────────────────
async def _make_org_with_pin(*, admin_pin_value: str, manager_pin_value: str):
    """Create one org + branch + manager user + system_settings admin_pin row.
    Returns (org_id, branch_id, invoice_id, doc_code, manager_user_id).
    """
    org_id = _uid("p5pin-org")
    branch_id = _uid("p5pin-br")
    invoice_id = _uid("p5pin-inv")
    invoice_number = f"INV-{uuid.uuid4().hex[:6].upper()}"
    mgr_id = _uid("p5pin-mgr")
    doc_code = f"P5PIN{uuid.uuid4().hex[:5].upper()}"

    await _raw_db.organizations.insert_one({
        "id": org_id, "name": f"P5 Tenant {org_id[-4:]}", "active": True,
    })
    await _raw_db.branches.insert_one({
        "id": branch_id, "organization_id": org_id,
        "name": f"P5 Branch {branch_id[-4:]}", "active": True,
    })
    # Manager user with manager_pin set
    await _raw_db.users.insert_one({
        "id": mgr_id, "username": f"p5-mgr-{mgr_id[-4:]}",
        "full_name": f"P5 Manager {mgr_id[-4:]}",
        "organization_id": org_id, "role": "manager",
        "active": True, "branch_ids": [branch_id], "branch_id": branch_id,
        "manager_pin": manager_pin_value,
    })
    # System admin_pin row (hashed) — must be tenant-scoped
    await _raw_db.system_settings.insert_one({
        "id": new_id(),
        "organization_id": org_id,
        "key": "admin_pin",
        "pin_hash": hash_password(admin_pin_value),
        "set_at": now_iso(),
    })
    # Customer + invoice with payments (the sensitive fields under test)
    customer_id = _uid("p5pin-cust")
    await _raw_db.customers.insert_one({
        "id": customer_id, "organization_id": org_id, "branch_id": branch_id,
        "name": f"P5 Customer {customer_id[-4:]}", "active": True,
        "balance": 500.0, "phone": "09171234567",
    })
    payment_id = new_id()
    await _raw_db.invoices.insert_one({
        "id": invoice_id, "organization_id": org_id, "branch_id": branch_id,
        "invoice_number": invoice_number,
        "customer_id": customer_id,
        "customer_name": f"P5 Customer {customer_id[-4:]}",
        "items": [
            {"product_id": "p5-prd-1", "product_name": "Test Sack",
             "quantity": 2, "rate": 500.0, "total": 1000.0,
             "discount_type": "amount", "discount_value": 0,
             "discount_amount": 0},
        ],
        "subtotal": 1000.0, "freight": 0.0, "overall_discount": 0.0,
        "grand_total": 1000.0, "amount_paid": 500.0, "balance": 500.0,
        "status": "partial", "payment_status": "partial",
        "payment_method": "Cash", "payment_type": "cash",
        "release_mode": "full", "stock_release_status": "na",
        "created_at": now_iso(), "order_date": "2026-02-01",
        # The PIN-gated fields under test:
        "payments": [{
            "id": payment_id, "amount": 500.0, "method": "Cash",
            "date": "2026-02-01", "fund_source": "cashier",
            "recorded_by": "p5-cashier", "recorded_at": now_iso(),
        }],
    })
    # Doc code (collection NOT in TENANT_COLLECTIONS — safe via _raw_db)
    await _raw_db.doc_codes.insert_one({
        "id": new_id(),
        "code": doc_code,
        "doc_type": "invoice",
        "doc_id": invoice_id,
        "org_id": org_id,
        "created_at": now_iso(),
        "created_by": "p5-test",
    })
    return {
        "org_id": org_id, "branch_id": branch_id,
        "invoice_id": invoice_id, "invoice_number": invoice_number,
        "doc_code": doc_code, "manager_id": mgr_id,
        "customer_id": customer_id,
        "admin_pin": admin_pin_value, "manager_pin": manager_pin_value,
    }


async def _cleanup_org(org_id: str, doc_code: str):
    await _raw_db.doc_codes.delete_many({"code": doc_code})
    await _raw_db.invoices.delete_many({"organization_id": org_id})
    await _raw_db.customers.delete_many({"organization_id": org_id})
    await _raw_db.users.delete_many({"organization_id": org_id})
    await _raw_db.system_settings.delete_many({"organization_id": org_id})
    await _raw_db.branches.delete_many({"organization_id": org_id})
    await _raw_db.organizations.delete_many({"id": org_id})
    # Clean up any pin attempt log rows created during the test runs
    await _raw_db.pin_attempt_log.delete_many({"organization_id": org_id})


# ─────────────────────────────────────────────────────────────────────
# Main regression test
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_cross_tenant_pin_cannot_unlock_other_tenant_receipt():
    """Company B's PIN MUST NOT unlock Company A's PIN-gated receipt data.

    Worst-case scenario rehearsed: Company A and Company B use *different*
    PINs, but the public endpoint receives B's PIN against A's doc_code.
    The TenantCollection fail-closed proxy + `_resolve_doc_code_with_context`
    must scope `_resolve_pin` to Company A's `system_settings` and
    `users` only — so B's admin_pin / manager_pin can never match.
    """
    # Two tenants, intentionally with DIFFERENT PINs so we can prove
    # tenant scoping (not coincidental equality).
    A = await _make_org_with_pin(
        admin_pin_value="AAAA1111",  # only valid in tenant A
        manager_pin_value="MGRA-77",
    )
    B = await _make_org_with_pin(
        admin_pin_value="BBBB2222",  # only valid in tenant B
        manager_pin_value="MGRB-99",
    )

    try:
        # ── 1. Open view of A's receipt must succeed and NOT leak PIN-gated
        #       sensitive fields (payments, customer, attached files).
        set_org_context(None)  # simulate public request — no JWT yet
        open_view = await view_document_open(A["doc_code"])
        assert open_view["doc_type"] == "invoice"
        assert open_view["number"] == A["invoice_number"]
        assert open_view["grand_total"] == 1000.0
        # Open view exposes line items + totals (by design)
        assert any(i["name"] == "Test Sack" for i in open_view["items"])
        # …but MUST NOT expose payment history or customer balance
        assert "payments" not in open_view
        assert "attached_files" not in open_view
        # The "document" raw dump should not surface either
        assert "document" not in open_view

        # ── 2. Tenant B's admin_pin used against tenant A's receipt → 403
        set_org_context(None)
        with pytest.raises(HTTPException) as ei:
            await lookup_document({"code": A["doc_code"], "pin": B["admin_pin"]})
        assert ei.value.status_code == 403, (
            f"Cross-tenant admin_pin should be rejected, got {ei.value.status_code}: {ei.value.detail}"
        )

        # ── 3. Tenant B's manager_pin used against tenant A's receipt → 403
        set_org_context(None)
        with pytest.raises(HTTPException) as ei:
            await lookup_document({"code": A["doc_code"], "pin": B["manager_pin"]})
        assert ei.value.status_code == 403

        # ── 4. Random PIN that doesn't exist in either tenant → 403
        set_org_context(None)
        with pytest.raises(HTTPException) as ei:
            await lookup_document({"code": A["doc_code"], "pin": "9999XYZ"})
        assert ei.value.status_code == 403

        # ── 5. Tenant A's OWN admin_pin used against tenant A's receipt
        #       → 200 with the PIN-gated sensitive payload (proves the
        #       happy path still works, ruling out a false-negative from
        #       a totally-broken endpoint).
        set_org_context(None)
        result = await lookup_document({"code": A["doc_code"], "pin": A["admin_pin"]})
        assert result["doc_type"] == "invoice"
        assert result["document"]["invoice_number"] == A["invoice_number"]
        # PIN-gated sensitive fields are now present
        assert isinstance(result.get("payments"), list)
        assert len(result["payments"]) == 1
        assert result["payments"][0]["amount"] == 500.0
        assert result["customer"] is not None
        assert result["customer"]["balance"] == 500.0
        assert result["verifier_method"] == "admin_pin"

        # ── 6. Tenant A's OWN manager_pin used against tenant A's receipt → 200
        set_org_context(None)
        result2 = await lookup_document({"code": A["doc_code"], "pin": A["manager_pin"]})
        assert result2["doc_type"] == "invoice"
        assert result2["verifier_method"] == "manager_pin"

        # ── 7. Reverse symmetry: A's PIN against B's receipt must also fail.
        set_org_context(None)
        with pytest.raises(HTTPException) as ei:
            await lookup_document({"code": B["doc_code"], "pin": A["admin_pin"]})
        assert ei.value.status_code == 403
        set_org_context(None)
        with pytest.raises(HTTPException) as ei:
            await lookup_document({"code": B["doc_code"], "pin": A["manager_pin"]})
        assert ei.value.status_code == 403

    finally:
        await _cleanup_org(A["org_id"], A["doc_code"])
        await _cleanup_org(B["org_id"], B["doc_code"])

"""
br_iso (internal_invoices) — tenant-isolation regression for B-1.

Defect (B-1) observed during br3:
  * `internal_invoices` is NOT registered in `TENANT_COLLECTIONS`,
    so `db.internal_invoices` resolves to the raw collection and the
    TenantCollection proxy is bypassed. Rows are inserted with
    `organization_id=None` and reads filter by `branch_id` only.
  * Concrete leak paths: `GET /internal-invoices/{id}`,
    `GET /internal-invoices/by-transfer/{transfer_id}`, the totals row
    of `GET /internal-invoices/profitability`, and the cross-tenant
    `db.internal_invoices.find(...)` calls in `routes/search.py`.

This file proves the defect BEFORE the fix and locks it down after.

Design notes:
  * Bypasses the module-scoped `tenant` fixture from conftest.py — this
    test needs TWO tenants in the same module to demonstrate cross-tenant
    leakage. Cleanup of both happens explicitly in `finally`.
  * Goes through the real route handlers (`create_transfer`) so the
    actual production-code path is exercised, not a mocked shortcut.
"""
import os
import sys

import pytest

BACKEND = os.path.join(os.path.dirname(__file__), "..", "..")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from config import _raw_db, set_org_context  # noqa: E402
from tests.business_regression._fixtures import (  # noqa: E402
    make_business_day_tenant, cleanup_business_tenant,
    seed_product,
)
from routes.branch_transfers import create_transfer  # noqa: E402
from routes.internal_invoices import (  # noqa: E402
    get_internal_invoice, get_invoice_by_transfer,
)


def _xfer_payload(from_branch_id, to_branch_id, product_id, name):
    return {
        "from_branch_id": from_branch_id,
        "to_branch_id": to_branch_id,
        "items": [{
            "product_id": product_id, "product_name": name,
            "sku": f"SKU-{product_id[-6:]}", "unit": "pc",
            "qty": 1, "branch_capital": 50.0,
            "transfer_capital": 50.0, "branch_retail": 80.0,
        }],
        "min_margin": 20, "notes": "br-iso regression",
    }


@pytest.mark.asyncio
async def test_br_iso_internal_invoices_tenant_scoping(record_result):
    """Two-tenant cross-read of `internal_invoices`.

    POST-FIX expectations:
      1. The internal_invoice row carries the correct `organization_id`.
      2. From tenant A's org-context, fetching tenant B's invoice by id
         OR by transfer_id MUST 404 (fail-closed via tenant proxy).
    """
    tenant_a = await make_business_day_tenant()
    tenant_b = await make_business_day_tenant()
    org_a, org_b = tenant_a["org_id"], tenant_b["org_id"]
    try:
        # ── Seed one product per tenant, then create a transfer per tenant.
        # `make_business_day_tenant` leaves the most recently created tenant
        # as the current context, so we explicitly flip back to A first.
        set_org_context(org_a)
        prod_a = await seed_product(
            org_a, tenant_a["branches"]["main"],
            name="BRISO Product A", price=80, stock=5, cost=50,
        )
        xfer_a = await create_transfer(
            _xfer_payload(tenant_a["branches"]["main"],
                          tenant_a["branches"]["b2"],
                          prod_a, "BRISO Product A"),
            user=tenant_a["users"]["owner"],
        )

        set_org_context(org_b)
        prod_b = await seed_product(
            org_b, tenant_b["branches"]["main"],
            name="BRISO Product B", price=80, stock=5, cost=50,
        )
        xfer_b = await create_transfer(
            _xfer_payload(tenant_b["branches"]["main"],
                          tenant_b["branches"]["b2"],
                          prod_b, "BRISO Product B"),
            user=tenant_b["users"]["owner"],
        )

        # ── State on disk (read via _raw_db — bypass any proxy filter so
        #    we see exactly what was persisted).
        inv_a = await _raw_db.internal_invoices.find_one(
            {"transfer_id": xfer_a["id"]}, {"_id": 0}
        ) or {}
        inv_b = await _raw_db.internal_invoices.find_one(
            {"transfer_id": xfer_b["id"]}, {"_id": 0}
        ) or {}

        assert inv_a, "br-iso setup: tenant A internal_invoice not persisted"
        assert inv_b, "br-iso setup: tenant B internal_invoice not persisted"

        # ── Assertion 1: rows carry the correct organization_id
        record_result(
            scenario="br_iso.internal_invoices_carry_org_id",
            step="tenant_A_invoice_has_org_a",
            expected={"organization_id": org_a},
            actual={"organization_id": inv_a.get("organization_id")},
            evidence={"invoice_id": inv_a.get("id"),
                      "transfer_id": xfer_a["id"]},
        )
        record_result(
            scenario="br_iso.internal_invoices_carry_org_id",
            step="tenant_B_invoice_has_org_b",
            expected={"organization_id": org_b},
            actual={"organization_id": inv_b.get("organization_id")},
            evidence={"invoice_id": inv_b.get("id"),
                      "transfer_id": xfer_b["id"]},
        )
        assert inv_a.get("organization_id") == org_a, (
            f"br-iso B-1: tenant A internal_invoice should carry "
            f"organization_id={org_a!r}, got {inv_a.get('organization_id')!r}"
        )
        assert inv_b.get("organization_id") == org_b

        # ── Assertion 2: cross-tenant fetch via the real route handler
        #    must NOT return another tenant's invoice. We run the read
        #    under tenant A's org context and request tenant B's ids.
        #    With the fix in place, db.internal_invoices is proxy-filtered
        #    by org_a → no match → HTTPException 404.
        set_org_context(org_a)
        user_a = tenant_a["users"]["owner"]

        from fastapi import HTTPException as _HTTP
        cross_by_id_status = None
        try:
            await get_internal_invoice(inv_b["id"], user=user_a)
        except _HTTP as e:
            cross_by_id_status = e.status_code

        cross_by_transfer_status = None
        try:
            await get_invoice_by_transfer(xfer_b["id"], user=user_a)
        except _HTTP as e:
            cross_by_transfer_status = e.status_code

        record_result(
            scenario="br_iso.cross_tenant_read_blocked",
            step="get_internal_invoice_by_id_blocked",
            expected={"status_code": 404, "blocked": True},
            actual={"status_code": cross_by_id_status,
                    "blocked": cross_by_id_status == 404},
            evidence={"tenant_A_org": org_a, "tenant_B_org": org_b,
                      "tenant_B_invoice_id": inv_b["id"]},
        )
        record_result(
            scenario="br_iso.cross_tenant_read_blocked",
            step="get_invoice_by_transfer_blocked",
            expected={"status_code": 404, "blocked": True},
            actual={"status_code": cross_by_transfer_status,
                    "blocked": cross_by_transfer_status == 404},
            evidence={"tenant_A_org": org_a, "tenant_B_org": org_b,
                      "tenant_B_transfer_id": xfer_b["id"]},
        )

        assert cross_by_id_status == 404, (
            f"br-iso LEAK: tenant A read tenant B's invoice by id "
            f"(got status {cross_by_id_status})"
        )
        assert cross_by_transfer_status == 404, (
            f"br-iso LEAK: tenant A read tenant B's invoice by transfer_id "
            f"(got status {cross_by_transfer_status})"
        )

        # ── Assertion 3: tenant A's own read still works under context A.
        own = await get_internal_invoice(inv_a["id"], user=user_a)
        record_result(
            scenario="br_iso.same_tenant_read_still_works",
            step="tenant_A_can_read_own_invoice",
            expected={"id": inv_a["id"]},
            actual={"id": own.get("id")},
            evidence={"invoice_number": own.get("invoice_number")},
        )
        assert own.get("id") == inv_a["id"], (
            "br-iso REGRESSION: tenant A cannot read its own invoice"
        )

    finally:
        await cleanup_business_tenant(org_a)
        await cleanup_business_tenant(org_b)

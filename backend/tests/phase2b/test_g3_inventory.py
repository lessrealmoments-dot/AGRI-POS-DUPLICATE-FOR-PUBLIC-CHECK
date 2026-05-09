"""
Phase 2B Group 3 — PRODUCT IN/OUT (STOCK MOVEMENT) verification.

Verifies stock-out invariants on the path that has the most exposure
(unified-sale) and stock-in invariants on PO receiving.
"""
import pytest
import sys
import os

BACKEND = os.path.join(os.path.dirname(__file__), "..", "..")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from config import _raw_db, set_org_context  # noqa: E402
from tests.phase2b._fixtures import (  # noqa: E402
    make_tenant, seed_wallets, seed_product, seed_customer, fake_user,
    snapshot_inventory, snapshot_wallet, base_sale_payload,
)
from routes.sales import create_unified_sale  # noqa: E402


# ───────────────────────────────────────────────────────────────────────────
# 3.1 Cash sale → inventory deducted exactly once
# ───────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_g3_cash_sale_single_deduction():
    org_id, branch_id, admin_id = await make_tenant()
    await seed_wallets(org_id, branch_id)
    pid = await seed_product(org_id, branch_id, stock=30, price=100)
    user = fake_user(org_id, admin_id, branch_id=branch_id)

    payload = {**base_sale_payload(branch_id=branch_id, product_id=pid, qty=7, rate=100),
               "payment_type": "cash", "amount_paid": 700.0}
    await create_unified_sale(payload, user=user)

    assert await snapshot_inventory(branch_id, pid) == 23


# ───────────────────────────────────────────────────────────────────────────
# 3.2 Sale failure → inventory untouched (Phase 1 C-4 reaffirmation)
# ───────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_g3_failed_sale_leaves_inventory_intact():
    """If the inventory check or any pre-commit step rejects, no stock change."""
    from fastapi import HTTPException
    org_id, branch_id, admin_id = await make_tenant()
    await seed_wallets(org_id, branch_id)
    pid = await seed_product(org_id, branch_id, stock=3, price=100)
    user = fake_user(org_id, admin_id, branch_id=branch_id)
    qty_before = await snapshot_inventory(branch_id, pid)

    payload = {**base_sale_payload(branch_id=branch_id, product_id=pid, qty=10, rate=100),
               "payment_type": "cash", "amount_paid": 1000.0}
    with pytest.raises(HTTPException):
        await create_unified_sale(payload, user=user)

    assert await snapshot_inventory(branch_id, pid) == qty_before


# ───────────────────────────────────────────────────────────────────────────
# 3.3 Stock movement log recorded for sale
# ───────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_g3_sale_creates_movement_log():
    org_id, branch_id, admin_id = await make_tenant()
    await seed_wallets(org_id, branch_id)
    pid = await seed_product(org_id, branch_id, stock=20, price=100)
    user = fake_user(org_id, admin_id, branch_id=branch_id)

    payload = {**base_sale_payload(branch_id=branch_id, product_id=pid, qty=5, rate=100),
               "payment_type": "cash", "amount_paid": 500.0}
    await create_unified_sale(payload, user=user)

    # The unified-sale route logs to multiple places. Check at least ONE
    # movement-trail collection captured the sale (sales_log per `log_sale_items`).
    sales_log = await _raw_db.sales_log.count_documents({
        "branch_id": branch_id, "product_id": pid,
    })
    assert sales_log >= 1, (
        "P2B G3 OBSERVATION: sales_log entry not created — sale movement is "
        "harder to audit. Verify log_sale_items pipeline."
    )


# ───────────────────────────────────────────────────────────────────────────
# 3.4 Branch isolation — inventory deduction stays in seller branch
# ───────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_g3_branch_isolated_deduction():
    """A sale in branch A must not touch branch B's inventory row."""
    org_id, branch_a, admin_id = await make_tenant()
    await seed_wallets(org_id, branch_a)
    branch_b = "p2b-br-other-" + branch_a[-4:]
    await _raw_db.branches.insert_one({
        "id": branch_b, "organization_id": org_id, "name": "Other", "active": True,
    })
    # Same product_id but separate inventory rows per branch
    pid = await seed_product(org_id, branch_a, stock=20, price=100)
    await _raw_db.inventory.insert_one({
        "id": "p2b-bb-inv", "organization_id": org_id, "branch_id": branch_b,
        "product_id": pid, "quantity": 50, "cost_price": 60,
    })
    user = fake_user(org_id, admin_id, branch_id=branch_a)

    payload = {**base_sale_payload(branch_id=branch_a, product_id=pid, qty=4, rate=100),
               "payment_type": "cash", "amount_paid": 400.0}
    await create_unified_sale(payload, user=user)

    assert await snapshot_inventory(branch_a, pid) == 16
    assert await snapshot_inventory(branch_b, pid) == 50, (
        "P2B G3 BUG: sale in branch A also touched branch B's inventory row"
    )


# ───────────────────────────────────────────────────────────────────────────
# 3.5 Idempotent retry → inventory unchanged the second time
# ───────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_g3_idempotent_sale_no_double_deduction():
    org_id, branch_id, admin_id = await make_tenant()
    await seed_wallets(org_id, branch_id)
    pid = await seed_product(org_id, branch_id, stock=20, price=100)
    user = fake_user(org_id, admin_id, branch_id=branch_id)

    payload = {**base_sale_payload(branch_id=branch_id, product_id=pid, qty=4, rate=100),
               "payment_type": "cash", "amount_paid": 400.0,
               "idempotency_key": "p2b-g3-" + branch_id[-4:]}
    await create_unified_sale(payload, user=user)
    await create_unified_sale(payload, user=user)
    assert await snapshot_inventory(branch_id, pid) == 16


# ───────────────────────────────────────────────────────────────────────────
# 3.6 Negative-stock observation (no idempotency_key + repeat cash sale)
# ───────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_g3_repeat_no_idem_key_drains_stock():
    """Two identical sales without idempotency_key both succeed; without
    a key the route legitimately treats them as separate sales. This is
    NOT a bug, but documents the cashier-side risk: client UIs MUST send a
    fresh idempotency_key per sale and reuse only on retry."""
    org_id, branch_id, admin_id = await make_tenant()
    await seed_wallets(org_id, branch_id)
    pid = await seed_product(org_id, branch_id, stock=10, price=100)
    user = fake_user(org_id, admin_id, branch_id=branch_id)

    payload = {**base_sale_payload(branch_id=branch_id, product_id=pid, qty=4, rate=100),
               "payment_type": "cash", "amount_paid": 400.0}
    await create_unified_sale(payload, user=user)
    await create_unified_sale(payload, user=user)
    assert await snapshot_inventory(branch_id, pid) == 2  # 10 − 4 − 4

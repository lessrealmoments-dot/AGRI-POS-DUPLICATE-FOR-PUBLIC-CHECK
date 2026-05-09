"""
Phase 2B Group 7 — Quick POS / Advanced POS / POS Terminal / Offline Sync comparison.

The unified-sale route (POST /unified-sale) is THE ONE backend path used by:
  * Quick POS (UnifiedSalesPage.js mode='quick')
  * Advanced POS / Order Mode (mode='order')
  * POS Terminal online (TerminalShell.jsx → /unified-sale)
  * POS Terminal offline → /sales/sync → unifies via _create_unified_sale

This shared-route property is the single biggest risk-reducer in the POS
codebase. Group 7 verifies that the same logical scenario produces the
SAME invoice contract regardless of `mode`, that offline sync is
idempotent (Phase 1 C-5), and that split sales decompose correctly.
"""
import pytest
import sys
import os

BACKEND = os.path.join(os.path.dirname(__file__), "..", "..")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from config import _raw_db, set_org_context  # noqa: E402
from tests.phase2b._fixtures import (  # noqa: E402
    make_tenant, seed_wallets, seed_product, fake_user,
    snapshot_inventory, snapshot_wallet, base_sale_payload,
)
from routes.sales import create_unified_sale  # noqa: E402


# ───────────────────────────────────────────────────────────────────────────
# 7.1 Quick POS vs Advanced POS — same backend route, same invoice contract
# ───────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_g7_quick_vs_advanced_same_invoice_shape():
    """Two sales with identical items but different `mode` tags produce
    structurally identical invoices (only `mode` and id/inv_number differ)."""
    org_id, branch_id, admin_id = await make_tenant()
    await seed_wallets(org_id, branch_id)
    pid = await seed_product(org_id, branch_id, stock=30, price=100)
    user = fake_user(org_id, admin_id, branch_id=branch_id)

    body = {**base_sale_payload(branch_id=branch_id, product_id=pid, qty=2, rate=100),
            "payment_type": "cash", "amount_paid": 200.0}

    quick = await create_unified_sale({**body, "mode": "quick"}, user=user)
    order = await create_unified_sale({**body, "mode": "order"}, user=user)

    quick_inv = await _raw_db.invoices.find_one({"id": quick["id"]}, {"_id": 0})
    order_inv = await _raw_db.invoices.find_one({"id": order["id"]}, {"_id": 0})

    # Each sale records its own mode
    assert quick_inv["mode"] == "quick"
    assert order_inv["mode"] == "order"
    # All financial fields equal
    for f in ("grand_total", "amount_paid", "balance", "status", "payment_type"):
        assert quick_inv[f] == order_inv[f], (
            f"P2B G7 BUG: field `{f}` differs between Quick and Advanced POS"
        )


# ───────────────────────────────────────────────────────────────────────────
# 7.2 Split sale: amount_paid == cash + digital
# ───────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_g7_split_amount_paid_equals_cash_plus_digital():
    org_id, branch_id, admin_id = await make_tenant()
    await seed_wallets(org_id, branch_id)
    pid = await seed_product(org_id, branch_id, stock=20, price=100)
    user = fake_user(org_id, admin_id, branch_id=branch_id)

    payload = {
        **base_sale_payload(branch_id=branch_id, product_id=pid, qty=4, rate=100),
        "payment_type": "split",
        "amount_paid": 400.0,
        "cash_amount": 150.0,
        "digital_amount": 250.0,
        "payment_method": "Cash",
        "digital_platform": "GCash",
        "digital_ref_number": "G-001",
    }
    res = await create_unified_sale(payload, user=user)
    inv = await _raw_db.invoices.find_one({"id": res["id"]}, {"_id": 0})
    assert inv["amount_paid"] == 400.0
    payments_sum = sum(p["amount"] for p in inv["payments"])
    assert abs(payments_sum - 400.0) < 0.01, (
        f"P2B G7 BUG: payments sum {payments_sum} != amount_paid 400"
    )
    assert await snapshot_wallet(branch_id, "cashier") == 150.0
    assert await snapshot_wallet(branch_id, "digital") == 250.0


# ───────────────────────────────────────────────────────────────────────────
# 7.3 Offline-sync idempotency (Phase 1 C-5 reaffirmation)
# ───────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_g7_offline_sync_envelope_idempotent():
    """Two unified-sale calls with the same idempotency_key must produce one
    invoice and one inventory deduction (Phase 1 C-5)."""
    org_id, branch_id, admin_id = await make_tenant()
    await seed_wallets(org_id, branch_id)
    pid = await seed_product(org_id, branch_id, stock=20, price=100)
    user = fake_user(org_id, admin_id, branch_id=branch_id)

    payload = {
        **base_sale_payload(branch_id=branch_id, product_id=pid, qty=3, rate=100),
        "payment_type": "cash", "amount_paid": 300.0,
        "idempotency_key": "p2b-g7-offline-" + branch_id[-4:],
    }
    res1 = await create_unified_sale(payload, user=user)
    res2 = await create_unified_sale(payload, user=user)
    assert res1["id"] == res2["id"]
    assert await snapshot_inventory(branch_id, pid) == 17  # 20 - 3, NOT 14

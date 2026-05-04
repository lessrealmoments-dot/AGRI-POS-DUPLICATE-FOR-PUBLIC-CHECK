"""
Iter 224 — Branch Transfer receive: pre-flight stock validation + audit trail fix.

Regression from live report BTO-20260503-0003:
  - When receive_transfer was called with an item whose source stock was
    insufficient, _apply_receipt raised HTTPException mid-loop AFTER prior
    items had already had their inventory decremented from source and
    incremented at destination. Transfer status never flipped to "received"
    → UI showed "Failed" → but inventory was partially mutated.
  - Capital_changes audit log was reading bp_before AFTER the upsert,
    so old_capital was always equal to new_capital. Useless trail.
  - Smart-Rule "choice" was being overwritten by `choice = capital_choices
    .get(product_id, "transfer_capital")` right before the audit insert,
    so moving_average selections logged as transfer_capital.

This test file validates:
  1. Pre-flight insufficient stock → raises 400 AND leaves NO inventory changed.
  2. Happy path → old_capital != new_capital in capital_changes.
  3. Smart-Rule branch (transfer_capital < current_dest_capital) → audit log
     records method="moving_average" (not "transfer_capital").
"""
import os
import sys
import pytest
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests._org_test_helpers import ensure_org_admin_token  # noqa: E402

BASE_URL = os.environ.get("BACKEND_BASE_URL", "http://localhost:8001")
API = f"{BASE_URL}/api"


@pytest.fixture(scope="module")
def headers():
    token, _user = ensure_org_admin_token()
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def mongo_db():
    """Direct DB handle for seed/cleanup."""
    import asyncio
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    yield db, asyncio.get_event_loop() if asyncio._get_running_loop() else asyncio.new_event_loop()
    client.close()


def _run(coro):
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _org_branches():
    """Fetch the two regression-org branches directly from the DB."""
    import asyncio
    from motor.motor_asyncio import AsyncIOMotorClient

    async def _inner():
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        org_id = os.environ.get("REGRESSION_ORG_ID")
        if not org_id:
            # Fallback: find regression admin's org
            u = await db.users.find_one(
                {"email": "test_org_admin@regression.local"}, {"_id": 0, "organization_id": 1}
            )
            org_id = u["organization_id"] if u else None
        branches = await db.branches.find(
            {"organization_id": org_id}, {"_id": 0, "id": 1, "name": 1}
        ).to_list(5)
        client.close()
        return org_id, branches

    return _run(_inner())


def _seed_product_with_stock(org_id, branch_id, qty, cost=10.0, retail=20.0):
    """Create a one-off product + seed inventory on the given branch."""
    import uuid
    from motor.motor_asyncio import AsyncIOMotorClient
    from datetime import datetime, timezone

    async def _inner():
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        pid = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        await db.products.insert_one({
            "id": pid, "organization_id": org_id,
            "name": f"Test Product {pid[:6]}", "sku": f"SKU-{pid[:6]}",
            "unit": "pc", "active": True, "created_at": now,
        })
        await db.inventory.insert_one({
            "id": str(uuid.uuid4()), "organization_id": org_id,
            "product_id": pid, "branch_id": branch_id, "quantity": qty,
            "updated_at": now,
        })
        await db.branch_prices.insert_one({
            "product_id": pid, "branch_id": branch_id,
            "organization_id": org_id,
            "cost_price": cost, "prices": {"retail": retail},
            "updated_at": now,
        })
        client.close()
        return pid

    return _run(_inner())


def _get_inventory_qty(product_id, branch_id):
    from motor.motor_asyncio import AsyncIOMotorClient

    async def _inner():
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        inv = await db.inventory.find_one(
            {"product_id": product_id, "branch_id": branch_id}, {"_id": 0, "quantity": 1}
        )
        client.close()
        return float(inv["quantity"]) if inv else 0.0

    return _run(_inner())


def _get_capital_change(order_number, product_id):
    from motor.motor_asyncio import AsyncIOMotorClient

    async def _inner():
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        rec = await db.capital_changes.find_one(
            {"source_ref": order_number, "product_id": product_id}, {"_id": 0}
        )
        client.close()
        return rec

    return _run(_inner())


def _delete_product_and_data(pid):
    from motor.motor_asyncio import AsyncIOMotorClient

    async def _inner():
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        await db.products.delete_many({"id": pid})
        await db.inventory.delete_many({"product_id": pid})
        await db.branch_prices.delete_many({"product_id": pid})
        await db.capital_changes.delete_many({"product_id": pid})
        client.close()

    _run(_inner())


# ──────────────────────────────────────────────────────────────────────────
# Test 1 — Pre-flight: if ANY item has insufficient source stock, NO
# inventory should move for ANY item in the transfer.
# ──────────────────────────────────────────────────────────────────────────
def test_preflight_rollback_on_insufficient_stock(headers):
    org_id, branches = _org_branches()
    assert len(branches) >= 2, "Need at least 2 branches in regression org"
    from_b, to_b = branches[0]["id"], branches[1]["id"]

    # Seed 2 products on source: one OK, one short.
    p_ok = _seed_product_with_stock(org_id, from_b, qty=100, cost=10.0, retail=20.0)
    p_short = _seed_product_with_stock(org_id, from_b, qty=1, cost=15.0, retail=30.0)

    try:
        src_qty_ok_before = _get_inventory_qty(p_ok, from_b)
        src_qty_short_before = _get_inventory_qty(p_short, from_b)
        dest_qty_ok_before = _get_inventory_qty(p_ok, to_b)
        dest_qty_short_before = _get_inventory_qty(p_short, to_b)

        # Create draft transfer — ok product qty=5 (fine), short product qty=10 (fail)
        r = requests.post(f"{API}/branch-transfers", headers=headers, json={
            "from_branch_id": from_b,
            "to_branch_id": to_b,
            "items": [
                {"product_id": p_ok, "product_name": "OK", "qty": 5,
                 "transfer_capital": 10.0, "branch_retail": 20.0, "unit": "pc"},
                {"product_id": p_short, "product_name": "Short", "qty": 10,
                 "transfer_capital": 15.0, "branch_retail": 30.0, "unit": "pc"},
            ],
        })
        assert r.status_code == 200, r.text
        transfer_id = r.json()["id"]

        # Send (draft → sent)
        r = requests.post(f"{API}/branch-transfers/{transfer_id}/send", headers=headers)
        assert r.status_code == 200, r.text

        # Receive with exact match → triggers _apply_receipt
        r = requests.post(
            f"{API}/branch-transfers/{transfer_id}/receive",
            headers=headers,
            json={"skip_receipt_check": True, "items": []},
        )
        assert r.status_code == 400, f"Expected 400, got {r.status_code}: {r.text}"
        assert "Insufficient stock" in r.json()["detail"]
        assert "No inventory changed" in r.json()["detail"]

        # CRITICAL: verify NO inventory moved for either product
        assert _get_inventory_qty(p_ok, from_b) == src_qty_ok_before, \
            "p_ok source inventory was mutated despite failure"
        assert _get_inventory_qty(p_short, from_b) == src_qty_short_before, \
            "p_short source inventory was mutated despite failure"
        assert _get_inventory_qty(p_ok, to_b) == dest_qty_ok_before, \
            "p_ok destination inventory was mutated despite failure"
        assert _get_inventory_qty(p_short, to_b) == dest_qty_short_before, \
            "p_short destination inventory was mutated despite failure"
    finally:
        _delete_product_and_data(p_ok)
        _delete_product_and_data(p_short)


# ──────────────────────────────────────────────────────────────────────────
# Test 2 — Capital change audit captures REAL old→new transition.
# ──────────────────────────────────────────────────────────────────────────
def test_capital_change_audit_records_true_old_capital(headers):
    org_id, branches = _org_branches()
    from_b, to_b = branches[0]["id"], branches[1]["id"]

    # Source: 50pcs @ cost=12. Destination pre-existing capital = 8 (different).
    p = _seed_product_with_stock(org_id, from_b, qty=50, cost=12.0, retail=24.0)
    # Pre-seed destination with a DIFFERENT capital so we can detect audit drift.
    from motor.motor_asyncio import AsyncIOMotorClient
    from datetime import datetime, timezone

    async def _seed_dest():
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        now = datetime.now(timezone.utc).isoformat()
        await db.branch_prices.insert_one({
            "product_id": p, "branch_id": to_b,
            "organization_id": org_id,
            "cost_price": 8.0, "prices": {"retail": 18.0},
            "updated_at": now,
        })
        await db.inventory.insert_one({
            "id": "test-inv-dest-" + p, "organization_id": org_id,
            "product_id": p, "branch_id": to_b, "quantity": 2,
            "updated_at": now,
        })
        client.close()

    _run(_seed_dest())

    try:
        # transfer_capital=15 > 8 (dest) → Smart Rule picks "transfer_capital"
        r = requests.post(f"{API}/branch-transfers", headers=headers, json={
            "from_branch_id": from_b, "to_branch_id": to_b,
            "items": [{
                "product_id": p, "product_name": "Audit product", "qty": 10,
                "transfer_capital": 15.0, "branch_retail": 30.0, "unit": "pc",
            }],
        })
        transfer_id = r.json()["id"]
        order_number = r.json()["order_number"]
        requests.post(f"{API}/branch-transfers/{transfer_id}/send", headers=headers)
        r = requests.post(
            f"{API}/branch-transfers/{transfer_id}/receive", headers=headers,
            json={"skip_receipt_check": True, "items": []},
        )
        assert r.status_code == 200, r.text

        cc = _get_capital_change(order_number, p)
        assert cc is not None, "capital_changes row missing"
        # Before fix: old_capital == new_capital == 15 (bp_before read after upsert).
        assert cc["old_capital"] == 8.0, f"Expected old=8.0, got {cc['old_capital']}"
        assert cc["new_capital"] == 15.0, f"Expected new=15.0, got {cc['new_capital']}"
        assert cc["method"] == "transfer_capital"
    finally:
        _delete_product_and_data(p)


# ──────────────────────────────────────────────────────────────────────────
# Test 3 — Smart Rule respected in audit log (moving_average path).
# ──────────────────────────────────────────────────────────────────────────
def test_smart_rule_records_moving_average_when_selected(headers):
    org_id, branches = _org_branches()
    from_b, to_b = branches[0]["id"], branches[1]["id"]
    # Source qty=30 @ cost=5 (low). Destination existing capital = 20 (high).
    # transfer_capital (5) < current_dest_capital (20) → Smart Rule picks moving_average.
    p = _seed_product_with_stock(org_id, from_b, qty=30, cost=5.0, retail=10.0)
    from motor.motor_asyncio import AsyncIOMotorClient
    from datetime import datetime, timezone

    async def _seed_dest():
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        now = datetime.now(timezone.utc).isoformat()
        await db.branch_prices.insert_one({
            "product_id": p, "branch_id": to_b, "organization_id": org_id,
            "cost_price": 20.0, "prices": {"retail": 30.0},
            "updated_at": now,
        })
        await db.inventory.insert_one({
            "id": "test-inv-dest-sr-" + p, "organization_id": org_id,
            "product_id": p, "branch_id": to_b, "quantity": 5, "updated_at": now,
        })
        client.close()

    _run(_seed_dest())

    try:
        r = requests.post(f"{API}/branch-transfers", headers=headers, json={
            "from_branch_id": from_b, "to_branch_id": to_b,
            "items": [{
                "product_id": p, "product_name": "SR product", "qty": 5,
                "transfer_capital": 5.0, "branch_retail": 10.0, "unit": "pc",
            }],
        })
        transfer_id = r.json()["id"]
        order_number = r.json()["order_number"]
        requests.post(f"{API}/branch-transfers/{transfer_id}/send", headers=headers)
        r = requests.post(
            f"{API}/branch-transfers/{transfer_id}/receive", headers=headers,
            json={"skip_receipt_check": True, "items": []},
        )
        assert r.status_code == 200, r.text

        cc = _get_capital_change(order_number, p)
        assert cc is not None
        # Before fix: audit log would record "transfer_capital" because of the
        # `choice = capital_choices.get(product_id, "transfer_capital")`
        # overwrite right before the insert.
        assert cc["method"] == "moving_average", \
            f"Expected method=moving_average, got {cc['method']}"
        assert cc["old_capital"] == 20.0
    finally:
        _delete_product_and_data(p)

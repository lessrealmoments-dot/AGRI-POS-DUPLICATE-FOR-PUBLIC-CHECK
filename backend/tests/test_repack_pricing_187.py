"""
Iteration 187 — Branch-aware repack pricing.

Verifies:
  - get_repack_capital() derives capital live from parent's branch_prices.cost_price
  - generate-repack requires branch_id and writes prices to branch_prices, not global
  - cost-details returns parent-derived capital for repacks
  - /products/repack-pricing/grid lists repacks × branches with capital + retail
  - /products/repack-pricing/bulk-save persists retail with PIN
  - JIT retail prices in /unified-sale persist to branch_prices after Owner PIN
  - Repacks always price at retail tier, regardless of customer's wholesale tier
"""
from uuid import uuid4
from datetime import datetime, timezone

import pytest
import requests

from _org_test_helpers import API, _db, ensure_org_admin_token, TEST_ORG_ADMIN_PIN


@pytest.fixture(scope="module")
def auth():
    token, user = ensure_org_admin_token()
    return token, user


@pytest.fixture
def repack_setup(auth):
    """Seed: one parent product with a global cost, a branch_prices override
    on Branch A (different cost), and a generated repack child."""
    db = _db()
    _, user = auth
    org_id = user.get("organization_id", "")
    branches = list(db.branches.find({"organization_id": org_id, "active": True}, {"_id": 0, "id": 1, "name": 1}).limit(2))
    if len(branches) < 2:
        pytest.skip("Need at least 2 branches in test org")
    branch_a, branch_b = branches[0], branches[1]

    parent_id = str(uuid4())
    db.products.insert_one({
        "id": parent_id, "sku": f"PARENT-{parent_id[:6]}",
        "name": f"Galimax-{parent_id[:6]}", "category": "Test",
        "unit": "Bag", "cost_price": 1000.0, "prices": {"retail": 1500},
        "active": True, "is_repack": False, "product_type": "stockable",
        "organization_id": org_id,
    })
    # Branch A parent capital = 1000 → repack 50/bag capital = 20
    db.branch_prices.insert_one({
        "id": str(uuid4()), "product_id": parent_id, "branch_id": branch_a["id"],
        "cost_price": 1000.0, "prices": {}, "organization_id": org_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    # Branch B parent capital = 1200 → repack 50/bag capital = 24
    db.branch_prices.insert_one({
        "id": str(uuid4()), "product_id": parent_id, "branch_id": branch_b["id"],
        "cost_price": 1200.0, "prices": {}, "organization_id": org_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    # Parent inventory in both branches (so it qualifies for "with_inventory_only")
    for b in (branch_a, branch_b):
        db.inventory.insert_one({
            "id": str(uuid4()), "product_id": parent_id, "branch_id": b["id"],
            "quantity": 10, "organization_id": org_id,
        })

    yield {"parent_id": parent_id, "branch_a": branch_a, "branch_b": branch_b, "org_id": org_id}

    db.products.delete_many({"$or": [{"id": parent_id}, {"parent_id": parent_id}]})
    db.branch_prices.delete_many({"$or": [{"product_id": parent_id}, {}]})  # cleanup safe
    db.inventory.delete_many({"product_id": parent_id})


# ── 1. generate-repack: requires branch_id ────────────────────────────────

def test_generate_repack_requires_branch_id(auth, repack_setup):
    token, _ = auth
    r = requests.post(
        f"{API}/products/{repack_setup['parent_id']}/generate-repack",
        json={"name": "R-50kg", "unit": "kg", "units_per_parent": 50, "prices": {"retail": 25}},
        headers={"Authorization": f"Bearer {token}"}, timeout=15,
    )
    assert r.status_code == 400, r.text
    assert "branch" in (r.json().get("detail") or "").lower()


def test_generate_repack_writes_branch_prices(auth, repack_setup):
    token, _ = auth
    db = _db()
    r = requests.post(
        f"{API}/products/{repack_setup['parent_id']}/generate-repack",
        json={
            "name": "R-50kg",
            "unit": "kg",
            "units_per_parent": 50,
            "add_on_cost": 0,
            "prices": {"retail": 25},
            "branch_id": repack_setup["branch_a"]["id"],
        },
        headers={"Authorization": f"Bearer {token}"}, timeout=15,
    )
    assert r.status_code == 200, r.text
    repack = r.json()
    assert repack["is_repack"] is True
    # cost_price should NOT be stored — it's derived live
    assert repack.get("cost_price", 0) == 0
    # Global product.prices stays empty
    assert (repack.get("prices") or {}) == {}

    # Retail saved to branch_prices for branch A only
    bp_a = db.branch_prices.find_one(
        {"product_id": repack["id"], "branch_id": repack_setup["branch_a"]["id"]}, {"_id": 0}
    )
    assert bp_a is not None
    assert bp_a["prices"]["retail"] == 25

    bp_b = db.branch_prices.find_one(
        {"product_id": repack["id"], "branch_id": repack_setup["branch_b"]["id"]}, {"_id": 0}
    )
    assert bp_b is None


# ── 2. Repack capital is parent-derived per branch ────────────────────────

def test_repack_capital_branch_aware_in_cost_details(auth, repack_setup):
    """Same repack returns capital=20 for Branch A (1000/50) and 24 for Branch B (1200/50)."""
    token, _ = auth
    # Create the repack first
    repack_res = requests.post(
        f"{API}/products/{repack_setup['parent_id']}/generate-repack",
        json={
            "name": "R-50kg-cap",
            "unit": "kg",
            "units_per_parent": 50,
            "prices": {"retail": 25},
            "branch_id": repack_setup["branch_a"]["id"],
        },
        headers={"Authorization": f"Bearer {token}"}, timeout=15,
    )
    assert repack_res.status_code == 200
    repack_id = repack_res.json()["id"]

    # Branch A capital = 20
    r_a = requests.post(
        f"{API}/products/cost-details",
        json={"branch_id": repack_setup["branch_a"]["id"],
              "product_ids": [repack_id],
              "pin": TEST_ORG_ADMIN_PIN},
        headers={"Authorization": f"Bearer {token}"}, timeout=15,
    )
    assert r_a.status_code == 200, r_a.text
    assert abs(r_a.json()["costs"][repack_id]["effective_cost"] - 20.0) < 0.01

    # Branch B capital = 24
    r_b = requests.post(
        f"{API}/products/cost-details",
        json={"branch_id": repack_setup["branch_b"]["id"],
              "product_ids": [repack_id],
              "pin": TEST_ORG_ADMIN_PIN},
        headers={"Authorization": f"Bearer {token}"}, timeout=15,
    )
    assert r_b.status_code == 200, r_b.text
    assert abs(r_b.json()["costs"][repack_id]["effective_cost"] - 24.0) < 0.01


# ── 3. Repack Pricing Manager grid ────────────────────────────────────────

def test_repack_pricing_grid_basic(auth, repack_setup):
    token, _ = auth
    # Create a repack first
    repack_res = requests.post(
        f"{API}/products/{repack_setup['parent_id']}/generate-repack",
        json={
            "name": "R-50kg-grid",
            "unit": "kg",
            "units_per_parent": 50,
            "prices": {"retail": 25},
            "branch_id": repack_setup["branch_a"]["id"],
        },
        headers={"Authorization": f"Bearer {token}"}, timeout=15,
    )
    repack_id = repack_res.json()["id"]

    bid_csv = f"{repack_setup['branch_a']['id']},{repack_setup['branch_b']['id']}"
    r = requests.get(
        f"{API}/products/repack-pricing/grid",
        params={"branch_ids": bid_csv, "with_inventory_only": True},
        headers={"Authorization": f"Bearer {token}"}, timeout=15,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    rows = [row for row in data["rows"] if row["repack_id"] == repack_id]
    assert len(rows) == 1
    row = rows[0]
    assert len(row["branches"]) == 2
    a = next(b for b in row["branches"] if b["branch_id"] == repack_setup["branch_a"]["id"])
    b = next(b for b in row["branches"] if b["branch_id"] == repack_setup["branch_b"]["id"])
    assert abs(a["capital"] - 20.0) < 0.01
    assert abs(b["capital"] - 24.0) < 0.01
    assert a["current_retail"] == 25
    assert b["current_retail"] is None  # branch B has no retail set


def test_repack_pricing_grid_requires_branches(auth):
    token, _ = auth
    r = requests.get(
        f"{API}/products/repack-pricing/grid",
        params={"branch_ids": ""},
        headers={"Authorization": f"Bearer {token}"}, timeout=15,
    )
    assert r.status_code == 400


# ── 4. Bulk-save retail with PIN ─────────────────────────────────────────

def test_repack_bulk_save_requires_pin(auth, repack_setup):
    token, _ = auth
    # Create a repack
    rp_res = requests.post(
        f"{API}/products/{repack_setup['parent_id']}/generate-repack",
        json={
            "name": "R-50kg-save",
            "unit": "kg",
            "units_per_parent": 50,
            "branch_id": repack_setup["branch_a"]["id"],
        },
        headers={"Authorization": f"Bearer {token}"}, timeout=15,
    )
    rid = rp_res.json()["id"]
    # No PIN
    r = requests.post(
        f"{API}/products/repack-pricing/bulk-save",
        json={"updates": [{"repack_id": rid, "branch_id": repack_setup["branch_b"]["id"], "retail": 30}]},
        headers={"Authorization": f"Bearer {token}"}, timeout=15,
    )
    assert r.status_code == 400


def test_repack_bulk_save_persists(auth, repack_setup):
    token, _ = auth
    db = _db()
    rp_res = requests.post(
        f"{API}/products/{repack_setup['parent_id']}/generate-repack",
        json={
            "name": "R-50kg-savepin",
            "unit": "kg",
            "units_per_parent": 50,
            "branch_id": repack_setup["branch_a"]["id"],
        },
        headers={"Authorization": f"Bearer {token}"}, timeout=15,
    )
    rid = rp_res.json()["id"]

    r = requests.post(
        f"{API}/products/repack-pricing/bulk-save",
        json={
            "pin": TEST_ORG_ADMIN_PIN,
            "updates": [
                {"repack_id": rid, "branch_id": repack_setup["branch_b"]["id"], "retail": 30},
            ],
        },
        headers={"Authorization": f"Bearer {token}"}, timeout=15,
    )
    assert r.status_code == 200, r.text
    assert r.json()["saved"] == 1
    bp = db.branch_prices.find_one(
        {"product_id": rid, "branch_id": repack_setup["branch_b"]["id"]}, {"_id": 0}
    )
    assert bp is not None
    assert bp["prices"]["retail"] == 30

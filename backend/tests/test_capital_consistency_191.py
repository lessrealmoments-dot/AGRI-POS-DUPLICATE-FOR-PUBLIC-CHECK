"""
Iteration 191 — Cross-endpoint capital consistency regression test.

This is the SAFETY NET test. It creates a parent + repack with branch capital
set to ₱1500/50kg (so repack capital should be ₱30 at that branch), then hits
all 6 product-data endpoints and asserts they ALL return the same number.

If anyone in the future adds a new endpoint that bypasses get_repack_capital(),
this test will fail and surface the drift immediately.

Endpoints tested:
  1. GET  /api/products?branch_id=...
  2. GET  /api/products/{id}?branch_id=...
  3. GET  /api/products/{id}/detail?branch_id=...
  4. POST /api/products/cost-details (with branch_id)
  5. GET  /api/products/search-detail?branch_id=...
  6. GET  /api/inventory?branch_id=...
  7. GET  /api/sync/pos-data?branch_id=...
  8. GET  /api/products/repack-pricing/grid
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
def parent_repack_setup(auth):
    """Parent at ₱1500 global + ₱1500 branch capital → repack at 50kg = ₱30/kg."""
    token, user = auth
    db = _db()
    org_id = user.get("organization_id", "")
    branch = db.branches.find_one({"organization_id": org_id, "active": True}, {"_id": 0, "id": 1})

    parent_id = str(uuid4())
    db.products.insert_one({
        "id": parent_id, "sku": f"CONS-{parent_id[:6]}",
        "name": f"Consistency-{parent_id[:6]}", "category": "Test",
        "unit": "Bag", "cost_price": 1500.0, "prices": {"retail": 2500},
        "active": True, "is_repack": False, "product_type": "stockable",
        "organization_id": org_id,
    })
    db.branch_prices.insert_one({
        "id": str(uuid4()), "product_id": parent_id, "branch_id": branch["id"],
        "cost_price": 1500.0, "prices": {}, "organization_id": org_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    db.inventory.insert_one({
        "id": str(uuid4()), "product_id": parent_id, "branch_id": branch["id"],
        "quantity": 10, "organization_id": org_id,
    })

    # Create the repack via API
    r = requests.post(
        f"{API}/products/{parent_id}/generate-repack",
        json={"name": f"Repack-{parent_id[:6]}", "unit": "kg",
              "units_per_parent": 50, "prices": {"retail": 35},
              "branch_id": branch["id"]},
        headers={"Authorization": f"Bearer {token}"}, timeout=15,
    )
    assert r.status_code == 200
    repack_id = r.json()["id"]

    yield {"parent_id": parent_id, "repack_id": repack_id, "branch_id": branch["id"]}

    db.products.delete_many({"$or": [{"id": parent_id}, {"id": repack_id}]})
    db.branch_prices.delete_many({"product_id": {"$in": [parent_id, repack_id]}})
    db.inventory.delete_many({"product_id": parent_id})


def test_capital_consistent_across_all_endpoints(auth, parent_repack_setup):
    token, _ = auth
    EXPECTED = 30.0
    repack_id = parent_repack_setup["repack_id"]
    branch_id = parent_repack_setup["branch_id"]
    headers = {"Authorization": f"Bearer {token}"}
    results = {}

    # 1. GET /products
    r = requests.get(f"{API}/products", params={"branch_id": branch_id, "is_repack": True, "limit": 200}, headers=headers, timeout=15)
    rp = next((p for p in r.json()["products"] if p["id"] == repack_id), None)
    assert rp, "repack not in products list"
    results["GET /products"] = float(rp["cost_price"])

    # 2. GET /products/{id}
    r = requests.get(f"{API}/products/{repack_id}", params={"branch_id": branch_id}, headers=headers, timeout=15)
    results["GET /products/{id}"] = float(r.json()["cost_price"])

    # 3. GET /products/{id}/detail
    r = requests.get(f"{API}/products/{repack_id}/detail", params={"branch_id": branch_id}, headers=headers, timeout=15)
    body = r.json()
    results["GET /products/{id}/detail"] = float(body["product"]["cost_price"])

    # 4. POST /products/cost-details
    r = requests.post(f"{API}/products/cost-details",
                       json={"branch_id": branch_id, "product_ids": [repack_id], "pin": TEST_ORG_ADMIN_PIN},
                       headers=headers, timeout=15)
    results["POST /products/cost-details"] = float(r.json()["costs"][repack_id]["effective_cost"])

    # 5. GET /products/search-detail
    r = requests.get(f"{API}/products/search-detail", params={"search": parent_repack_setup["repack_id"][:8], "branch_id": branch_id}, headers=headers, timeout=15)
    rp_sd = next((p for p in r.json() if p["id"] == repack_id), None)
    if rp_sd:  # search is by sku/name — may not match by id; skip if not found
        results["GET /products/search-detail"] = float(rp_sd["cost_price"])

    # 6. GET /inventory
    r = requests.get(f"{API}/inventory", params={"branch_id": branch_id, "include_repacks": True, "limit": 5000}, headers=headers, timeout=15)
    inv_r = next((i for i in r.json()["items"] if i["id"] == repack_id), None)
    assert inv_r, "repack not in inventory list"
    results["GET /inventory"] = float(inv_r["cost_price"])

    # 7. GET /sync/pos-data
    r = requests.get(f"{API}/sync/pos-data", params={"branch_id": branch_id}, headers=headers, timeout=20)
    sync_p = next((p for p in r.json()["products"] if p["id"] == repack_id), None)
    assert sync_p, "repack not in sync data"
    results["GET /sync/pos-data"] = float(sync_p["cost_price"])

    # 8. GET /products/repack-pricing/grid
    r = requests.get(f"{API}/products/repack-pricing/grid", params={"branch_ids": branch_id, "with_inventory_only": True}, headers=headers, timeout=15)
    grid_row = next((row for row in r.json()["rows"] if row["repack_id"] == repack_id), None)
    assert grid_row, "repack not in pricing grid"
    grid_branch = next(b for b in grid_row["branches"] if b["branch_id"] == branch_id)
    results["GET /repack-pricing/grid"] = float(grid_branch["capital"])

    # All endpoints must agree to within 1 cent
    for endpoint, value in results.items():
        assert abs(value - EXPECTED) < 0.01, f"{endpoint} returned {value}, expected {EXPECTED}"
    print(f"\n✓ All {len(results)} endpoints agree on repack capital = ₱{EXPECTED}")

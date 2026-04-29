"""
Iteration 190 — Repack capital enrichment for list + detail endpoints.

Verifies:
  - GET /api/products?branch_id=X returns repacks with live computed cost_price
  - GET /api/products/{id}?branch_id=X returns single repack with live cost_price
  - GET /api/products/{id}/detail surfaces repack_capital + branch retail
"""
from uuid import uuid4
from datetime import datetime, timezone

import pytest
import requests

from _org_test_helpers import API, _db, ensure_org_admin_token


@pytest.fixture(scope="module")
def auth():
    token, user = ensure_org_admin_token()
    return token, user


@pytest.fixture
def repack_for_list(auth):
    token, user = auth
    db = _db()
    org_id = user.get("organization_id", "")
    branch = db.branches.find_one({"organization_id": org_id, "active": True}, {"_id": 0, "id": 1})

    pid = str(uuid4())
    db.products.insert_one({
        "id": pid, "sku": f"L-{pid[:6]}", "name": f"ListParent-{pid[:6]}",
        "category": "Test", "unit": "Bag", "cost_price": 1500.0, "prices": {"retail": 2000},
        "active": True, "is_repack": False, "product_type": "stockable",
        "organization_id": org_id,
    })
    db.branch_prices.insert_one({
        "id": str(uuid4()), "product_id": pid, "branch_id": branch["id"],
        "cost_price": 1500.0, "prices": {}, "organization_id": org_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

    # Create a repack via the API (exercises the live flow)
    r = requests.post(
        f"{API}/products/{pid}/generate-repack",
        json={"name": f"R-{pid[:6]}", "unit": "kg", "units_per_parent": 50,
              "prices": {"retail": 35}, "branch_id": branch["id"]},
        headers={"Authorization": f"Bearer {token}"}, timeout=15,
    )
    assert r.status_code == 200, r.text
    repack_id = r.json()["id"]

    yield {"parent_id": pid, "repack_id": repack_id, "branch_id": branch["id"]}

    db.products.delete_many({"$or": [{"id": pid}, {"id": repack_id}]})
    db.branch_prices.delete_many({"product_id": {"$in": [pid, repack_id]}})


def test_list_products_enriches_repack_capital(auth, repack_for_list):
    token, _ = auth
    r = requests.get(
        f"{API}/products",
        params={"branch_id": repack_for_list["branch_id"], "is_repack": True, "limit": 200},
        headers={"Authorization": f"Bearer {token}"}, timeout=15,
    )
    assert r.status_code == 200, r.text
    items = r.json()["products"]
    repack = next((p for p in items if p["id"] == repack_for_list["repack_id"]), None)
    assert repack is not None
    # Expected: 1500 / 50 = 30
    assert abs(float(repack["cost_price"]) - 30.0) < 0.01
    # Branch retail merged into prices
    assert repack["prices"]["retail"] == 35


def test_get_single_product_enriches_repack(auth, repack_for_list):
    token, _ = auth
    r = requests.get(
        f"{API}/products/{repack_for_list['repack_id']}",
        params={"branch_id": repack_for_list["branch_id"]},
        headers={"Authorization": f"Bearer {token}"}, timeout=15,
    )
    assert r.status_code == 200, r.text
    p = r.json()
    assert abs(float(p["cost_price"]) - 30.0) < 0.01
    assert p["prices"]["retail"] == 35


def test_product_detail_surfaces_repack_capital(auth, repack_for_list):
    token, _ = auth
    r = requests.get(
        f"{API}/products/{repack_for_list['repack_id']}/detail",
        params={"branch_id": repack_for_list["branch_id"]},
        headers={"Authorization": f"Bearer {token}"}, timeout=15,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    cost = body["cost"]
    assert abs(cost["cost_price"] - 30.0) < 0.01
    assert "repack_capital" in cost
    assert abs(cost["repack_capital"] - 30.0) < 0.01
    # Product-level cost_price should also show live value (was 0 before)
    assert abs(float(body["product"]["cost_price"]) - 30.0) < 0.01

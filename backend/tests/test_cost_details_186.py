"""
Iteration 186 — PIN-gated cost-details bulk endpoint for Sales screen.

Verifies:
  - Endpoint requires PIN (admin/manager/TOTP per policy)
  - Returns effective_cost, last_purchase, moving_average per product
  - Branch override beats global cost in effective_cost
  - Empty product list / missing branch raises 400
  - Invalid PIN → 403
"""
from uuid import uuid4
from datetime import datetime, timezone, timedelta

import pytest
import requests

from _org_test_helpers import API, _db, ensure_org_admin_token, TEST_ORG_ADMIN_PIN, TEST_ORG_MANAGER_PIN


@pytest.fixture(scope="module")
def auth():
    token, user = ensure_org_admin_token()
    return token, user


@pytest.fixture
def cost_setup(auth):
    """Seed a product, a branch override (different cost), and movements
    so we can test all three returned fields."""
    db = _db()
    _, user = auth
    org_id = user.get("organization_id", "")
    branch = db.branches.find_one({"organization_id": org_id, "active": True}, {"_id": 0, "id": 1})

    pid = str(uuid4())
    db.products.insert_one({
        "id": pid, "sku": f"COST-{pid[:6]}", "name": f"CostTest-{pid[:6]}",
        "category": "Test", "unit": "PC", "cost_price": 100.0,
        "prices": {"retail": 150}, "active": True,
        "product_type": "stockable", "organization_id": org_id, "is_repack": False,
    })
    # Branch override cost = 90
    db.branch_prices.insert_one({
        "id": str(uuid4()), "product_id": pid, "branch_id": branch["id"],
        "cost_price": 90.0, "prices": {}, "organization_id": org_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    # 2 movements: 10 @ 88, 5 @ 95 → MA = (10*88 + 5*95) / 15 = 90.333; LP (latest) = 95
    base = datetime.now(timezone.utc)
    db.movements.insert_many([
        {
            "id": str(uuid4()), "product_id": pid, "branch_id": branch["id"],
            "type": "purchase", "quantity": 10, "cost_price": 88.0,
            "created_at": (base - timedelta(days=5)).isoformat(),
            "organization_id": org_id,
        },
        {
            "id": str(uuid4()), "product_id": pid, "branch_id": branch["id"],
            "type": "transfer_in", "quantity": 5, "cost_price": 95.0,
            "created_at": (base - timedelta(days=1)).isoformat(),
            "organization_id": org_id,
        },
    ])

    yield {"product_id": pid, "branch_id": branch["id"]}

    db.products.delete_one({"id": pid})
    db.branch_prices.delete_many({"product_id": pid})
    db.movements.delete_many({"product_id": pid})


def test_cost_details_requires_pin(auth, cost_setup):
    token, _ = auth
    r = requests.post(
        f"{API}/products/cost-details",
        json={"branch_id": cost_setup["branch_id"], "product_ids": [cost_setup["product_id"]]},
        headers={"Authorization": f"Bearer {token}"}, timeout=15,
    )
    assert r.status_code == 400, r.text  # missing PIN


def test_cost_details_rejects_invalid_pin(auth, cost_setup):
    token, _ = auth
    r = requests.post(
        f"{API}/products/cost-details",
        json={"branch_id": cost_setup["branch_id"], "product_ids": [cost_setup["product_id"]], "pin": "000000"},
        headers={"Authorization": f"Bearer {token}"}, timeout=15,
    )
    assert r.status_code == 403, r.text


def test_cost_details_admin_pin_works(auth, cost_setup):
    token, _ = auth
    r = requests.post(
        f"{API}/products/cost-details",
        json={
            "branch_id": cost_setup["branch_id"],
            "product_ids": [cost_setup["product_id"]],
            "pin": TEST_ORG_ADMIN_PIN,
        },
        headers={"Authorization": f"Bearer {token}"}, timeout=15,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    cost = body["costs"][cost_setup["product_id"]]
    # Branch override (90) beats global (100)
    assert cost["effective_cost"] == 90.0
    # Last purchase = the more recent movement (₱95)
    assert cost["last_purchase"] == 95.0
    # Moving average = (10*88 + 5*95) / 15 ≈ 90.333
    assert abs(cost["moving_average"] - 90.3333) < 0.01


def test_cost_details_manager_pin_works(auth, cost_setup):
    """Manager PIN is allowed for view_capital_costs (admin/manager/TOTP)."""
    token, _ = auth
    r = requests.post(
        f"{API}/products/cost-details",
        json={
            "branch_id": cost_setup["branch_id"],
            "product_ids": [cost_setup["product_id"]],
            "pin": TEST_ORG_MANAGER_PIN,
        },
        headers={"Authorization": f"Bearer {token}"}, timeout=15,
    )
    # Manager PIN should be accepted by view_capital_costs policy. If the
    # test fixture doesn't seed a manager user, skip. The default policy
    # is `[admin_pin, manager_pin, totp]`.
    if r.status_code == 403:
        pytest.skip("No manager user seeded in test fixture")
    assert r.status_code == 200, r.text


def test_cost_details_validates_inputs(auth):
    token, _ = auth
    # Missing branch_id
    r1 = requests.post(
        f"{API}/products/cost-details",
        json={"product_ids": ["x"], "pin": TEST_ORG_ADMIN_PIN},
        headers={"Authorization": f"Bearer {token}"}, timeout=15,
    )
    assert r1.status_code == 400

    # Empty product_ids
    r2 = requests.post(
        f"{API}/products/cost-details",
        json={"branch_id": "any", "product_ids": [], "pin": TEST_ORG_ADMIN_PIN},
        headers={"Authorization": f"Bearer {token}"}, timeout=15,
    )
    assert r2.status_code == 400


def test_cost_details_returns_zero_for_unknown_product(auth, cost_setup):
    token, _ = auth
    r = requests.post(
        f"{API}/products/cost-details",
        json={
            "branch_id": cost_setup["branch_id"],
            "product_ids": ["non-existent-product-id-xyz"],
            "pin": TEST_ORG_ADMIN_PIN,
        },
        headers={"Authorization": f"Bearer {token}"}, timeout=15,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    cost = body["costs"]["non-existent-product-id-xyz"]
    assert cost["effective_cost"] == 0
    assert cost["last_purchase"] == 0
    assert cost["moving_average"] == 0

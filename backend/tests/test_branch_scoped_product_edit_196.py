"""
Iteration 196 — Branch-scoped product editing + read-merge.

Critical contract this test enforces:
  • PUT /api/products/{id}?branch_id=X with `prices` body writes ONLY to
    branch_prices for branch X — master `products.prices` stays untouched.
  • PUT /api/products/{id} (no branch_id) writes to master, as before.
  • GET /api/products?branch_id=X merges the branch override into each row
    (override wins per-key) and tags `price_source: "branch_override"`.
  • GET /api/products without branch_id returns master values, tagged
    `price_source: "global"`.
  • Catalog fields (name, category) hit master regardless of branch_id.

This is the fix for the "I edited Branch 2 and accidentally clobbered
every other branch" footgun that an admin reported on iter 195.
"""
import pytest
import requests
from uuid import uuid4

from _org_test_helpers import API, _db, ensure_org_admin_token


@pytest.fixture(scope="module")
def auth():
    return ensure_org_admin_token()


@pytest.fixture
def fresh_product(auth):
    _, user = auth
    db = _db()
    pid = str(uuid4())
    sku = f"BST-{pid[:6].upper()}"
    db.products.insert_one({
        "id": pid,
        "organization_id": user["organization_id"],
        "name": f"BranchScopedTest-{pid[:6]}",
        "sku": sku,
        "category": "Test",
        "unit": "Piece",
        "cost_price": 100.0,
        "prices": {"retail": 150.0, "wholesale": 130.0},
        "active": True,
        "is_repack": False,
        "product_type": "stockable",
    })
    yield pid
    db.products.delete_one({"id": pid})
    db.branch_prices.delete_many({"product_id": pid})


@pytest.fixture
def fresh_branch(auth):
    _, user = auth
    db = _db()
    bid = str(uuid4())
    db.branches.insert_one({
        "id": bid,
        "organization_id": user["organization_id"],
        "name": f"BranchScopeTestBr-{bid[:6]}",
        "active": True,
        "close_time_h": 18,
    })
    yield bid
    db.branches.delete_one({"id": bid})


def test_put_with_branch_only_writes_to_branch_prices(auth, fresh_product, fresh_branch):
    token, _ = auth
    db = _db()

    # Master before
    master_before = db.products.find_one({"id": fresh_product}, {"_id": 0, "prices": 1, "cost_price": 1})

    # PUT with branch_id query param — should write to branch_prices only
    r = requests.put(
        f"{API}/products/{fresh_product}",
        params={"branch_id": fresh_branch},
        headers={"Authorization": f"Bearer {token}"},
        json={"prices": {"credit": 175.0}, "cost_price": 90.0},
        timeout=10,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # Response reflects the BRANCH view (master ⊕ override)
    assert body["price_source"] == "branch_override"
    assert body["prices"]["credit"] == 175.0
    assert body["prices"]["retail"] == 150.0  # master retail still visible
    assert body["cost_price"] == 90.0

    # Master is untouched
    master_after = db.products.find_one({"id": fresh_product}, {"_id": 0, "prices": 1, "cost_price": 1})
    assert master_after == master_before, f"Master got clobbered! before={master_before} after={master_after}"

    # branch_prices got the override
    bp = db.branch_prices.find_one(
        {"product_id": fresh_product, "branch_id": fresh_branch}, {"_id": 0}
    )
    assert bp is not None
    assert bp["prices"]["credit"] == 175.0
    assert bp["cost_price"] == 90.0


def test_put_without_branch_writes_to_master(auth, fresh_product):
    token, _ = auth
    db = _db()
    r = requests.put(
        f"{API}/products/{fresh_product}",
        headers={"Authorization": f"Bearer {token}"},
        json={"prices": {"retail": 999.0}},
        timeout=10,
    )
    assert r.status_code == 200, r.text

    master = db.products.find_one({"id": fresh_product}, {"_id": 0, "prices": 1})
    assert master["prices"]["retail"] == 999.0


def test_put_with_branch_id_all_writes_to_master(auth, fresh_product):
    """Backwards-compat: branch_id="all" is treated as no branch context."""
    token, _ = auth
    db = _db()
    r = requests.put(
        f"{API}/products/{fresh_product}",
        params={"branch_id": "all"},
        headers={"Authorization": f"Bearer {token}"},
        json={"prices": {"retail": 888.0}},
        timeout=10,
    )
    assert r.status_code == 200
    master = db.products.find_one({"id": fresh_product}, {"_id": 0, "prices": 1})
    assert master["prices"]["retail"] == 888.0


def test_catalog_fields_always_hit_master(auth, fresh_product, fresh_branch):
    """Name / category go to master even when branch_id is set."""
    token, _ = auth
    db = _db()
    new_name = f"RenamedFromBranch-{uuid4().hex[:6]}"
    r = requests.put(
        f"{API}/products/{fresh_product}",
        params={"branch_id": fresh_branch},
        headers={"Authorization": f"Bearer {token}"},
        json={"name": new_name, "category": "RenamedCat",
              "prices": {"credit": 200.0}},
        timeout=10,
    )
    assert r.status_code == 200, r.text
    master = db.products.find_one({"id": fresh_product}, {"_id": 0})
    assert master["name"] == new_name
    assert master["category"] == "RenamedCat"
    # But master prices are untouched
    assert master["prices"]["retail"] == 150.0
    assert "credit" not in master["prices"]
    # Branch override has the credit price
    bp = db.branch_prices.find_one(
        {"product_id": fresh_product, "branch_id": fresh_branch}, {"_id": 0}
    )
    assert bp["prices"]["credit"] == 200.0


def test_get_products_merges_branch_override(auth, fresh_product, fresh_branch):
    """GET /products?branch_id=X must surface the branch override and tag
    each row with price_source so the UI can render the override chip."""
    token, _ = auth
    db = _db()
    # Seed a branch override directly so we test the read path in isolation
    db.branch_prices.insert_one({
        "id": str(uuid4()),
        "organization_id": db.products.find_one({"id": fresh_product})["organization_id"],
        "product_id": fresh_product,
        "branch_id": fresh_branch,
        "prices": {"credit": 222.0, "retail": 250.0},  # override retail
        "cost_price": 88.0,
        "updated_at": "2026-01-01T00:00:00",
    })

    # With branch context
    r = requests.get(
        f"{API}/products",
        params={"branch_id": fresh_branch, "search": "BranchScopedTest"},
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    assert r.status_code == 200, r.text
    items = r.json()["products"]
    row = next((x for x in items if x["id"] == fresh_product), None)
    assert row is not None, f"Product not found in scoped list: {[x['name'] for x in items]}"
    assert row["price_source"] == "branch_override"
    assert row["prices"]["credit"] == 222.0
    assert row["prices"]["retail"] == 250.0  # override wins
    assert row["prices"]["wholesale"] == 130.0  # master fallback
    assert row["cost_price"] == 88.0

    # Without branch context — master values, tagged global
    r2 = requests.get(
        f"{API}/products",
        params={"search": "BranchScopedTest"},
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    assert r2.status_code == 200
    items2 = r2.json()["products"]
    row2 = next((x for x in items2 if x["id"] == fresh_product), None)
    assert row2 is not None
    assert row2["price_source"] == "global"
    assert row2["prices"]["retail"] == 150.0  # master, not override
    assert "credit" not in row2["prices"]
    assert row2["cost_price"] == 100.0


def test_branch_override_does_not_leak_to_other_branch(auth, fresh_product, fresh_branch):
    """Editing under branch A must not affect branch B."""
    token, user = auth
    db = _db()
    # Make a second branch
    other_bid = str(uuid4())
    db.branches.insert_one({
        "id": other_bid,
        "organization_id": user["organization_id"],
        "name": f"OtherBr-{other_bid[:6]}",
        "active": True,
    })
    try:
        # Edit under fresh_branch
        r = requests.put(
            f"{API}/products/{fresh_product}",
            params={"branch_id": fresh_branch},
            headers={"Authorization": f"Bearer {token}"},
            json={"prices": {"credit": 333.0}},
            timeout=10,
        )
        assert r.status_code == 200

        # Check the OTHER branch sees master values, not the override
        r2 = requests.get(
            f"{API}/products",
            params={"branch_id": other_bid, "search": "BranchScopedTest"},
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        row = next((x for x in r2.json()["products"] if x["id"] == fresh_product), None)
        assert row is not None
        assert row["price_source"] == "global"
        assert "credit" not in (row.get("prices") or {})
    finally:
        db.branches.delete_one({"id": other_bid})


def test_branch_override_merge_preserves_existing_overrides(auth, fresh_product, fresh_branch):
    """Subsequent edits to the same branch should ADD/UPDATE keys, not wipe
    other override keys that were already saved."""
    token, _ = auth
    db = _db()
    # First edit: set credit
    requests.put(
        f"{API}/products/{fresh_product}",
        params={"branch_id": fresh_branch},
        headers={"Authorization": f"Bearer {token}"},
        json={"prices": {"credit": 100.0}},
        timeout=10,
    )
    # Second edit: set wholesale (must NOT wipe credit)
    r = requests.put(
        f"{API}/products/{fresh_product}",
        params={"branch_id": fresh_branch},
        headers={"Authorization": f"Bearer {token}"},
        json={"prices": {"wholesale": 110.0}},
        timeout=10,
    )
    assert r.status_code == 200
    bp = db.branch_prices.find_one(
        {"product_id": fresh_product, "branch_id": fresh_branch}, {"_id": 0}
    )
    assert bp["prices"]["credit"] == 100.0
    assert bp["prices"]["wholesale"] == 110.0

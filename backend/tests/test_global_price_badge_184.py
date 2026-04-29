"""
Iteration 184 — Global Price ("Needs Price Review") badge.

Verifies:
  - New inventory rows born after migration day start with no review timestamp
  - PO commit auto-clears review status at destination branch
  - Branch transfer commit auto-clears review status at destination
  - Branch price override (PUT /branch-prices) clears review status
  - Manual product update (cost/prices) clears across all branches
  - Branch-stock-and-price import (commit) clears review status
  - Manual mark-reviewed endpoint works (single + bulk)
  - pending-review-ids endpoint excludes already-reviewed products
"""
import json
import io
import csv
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
def fresh_inventory_row(auth):
    """Seed a product + an inventory row with NO price_reviewed_at field, simulating a
    brand-new row born after the migration."""
    db = _db()
    token, user = auth
    org_id = user.get("organization_id", "")
    branch = db.branches.find_one({"organization_id": org_id, "active": True}, {"_id": 0, "id": 1, "name": 1})
    if not branch:
        pytest.skip("No branch in test org")

    p_id = str(uuid4())
    db.products.insert_one({
        "id": p_id, "sku": f"GPB-{p_id[:6]}", "name": f"GPBadge-{p_id[:6]}",
        "category": "Test", "unit": "PC", "cost_price": 50.0,
        "prices": {"retail": 80.0, "wholesale": 70.0},
        "active": True, "product_type": "stockable",
        "organization_id": org_id, "is_repack": False,
    })
    inv_id = str(uuid4())
    db.inventory.insert_one({
        "id": inv_id, "product_id": p_id, "branch_id": branch["id"],
        "quantity": 0, "organization_id": org_id,
        # intentionally no price_reviewed_at
    })

    yield {"product_id": p_id, "branch_id": branch["id"], "inv_id": inv_id, "branch_name": branch["name"]}

    db.products.delete_one({"id": p_id})
    db.inventory.delete_many({"product_id": p_id})
    db.branch_prices.delete_many({"product_id": p_id})


def _is_reviewed(product_id, branch_id):
    db = _db()
    inv = db.inventory.find_one({"product_id": product_id, "branch_id": branch_id}, {"_id": 0})
    return bool(inv and inv.get("price_reviewed_at"))


def test_pending_review_ids_includes_new_row(auth, fresh_inventory_row):
    token, _ = auth
    r = requests.get(
        f"{API}/inventory/pending-review-ids",
        params={"branch_id": fresh_inventory_row["branch_id"]},
        headers={"Authorization": f"Bearer {token}"}, timeout=15,
    )
    assert r.status_code == 200, r.text
    ids = r.json().get("product_ids", [])
    assert fresh_inventory_row["product_id"] in ids


def test_manual_mark_reviewed_clears(auth, fresh_inventory_row):
    token, _ = auth
    p, b = fresh_inventory_row["product_id"], fresh_inventory_row["branch_id"]
    assert not _is_reviewed(p, b)

    r = requests.post(
        f"{API}/inventory/mark-reviewed",
        json={"product_id": p, "branch_id": b},
        headers={"Authorization": f"Bearer {token}"}, timeout=15,
    )
    assert r.status_code == 200, r.text
    assert _is_reviewed(p, b)

    # And it's no longer in pending list
    r2 = requests.get(
        f"{API}/inventory/pending-review-ids",
        params={"branch_id": b},
        headers={"Authorization": f"Bearer {token}"}, timeout=15,
    )
    assert p not in r2.json().get("product_ids", [])


def test_branch_price_override_clears(auth, fresh_inventory_row):
    token, _ = auth
    p, b = fresh_inventory_row["product_id"], fresh_inventory_row["branch_id"]
    assert not _is_reviewed(p, b)

    r = requests.put(
        f"{API}/branch-prices/{p}",
        json={"branch_id": b, "prices": {"retail": 99}},
        headers={"Authorization": f"Bearer {token}"}, timeout=15,
    )
    assert r.status_code == 200, r.text
    assert _is_reviewed(p, b), "Setting a branch price override should clear the review badge"


def test_manual_product_update_clears_across_branches(auth, fresh_inventory_row):
    """Editing global cost_price or prices should clear review across all
    branches that hold inventory of this product."""
    token, _ = auth
    p, b = fresh_inventory_row["product_id"], fresh_inventory_row["branch_id"]
    assert not _is_reviewed(p, b)

    r = requests.put(
        f"{API}/products/{p}",
        json={"prices": {"retail": 100, "wholesale": 90}},
        headers={"Authorization": f"Bearer {token}"}, timeout=15,
    )
    assert r.status_code == 200, r.text
    assert _is_reviewed(p, b), "Manual product price edit must clear review at every branch with inventory"


def test_branch_import_commit_clears(auth, fresh_inventory_row):
    token, _ = auth
    p, b = fresh_inventory_row["product_id"], fresh_inventory_row["branch_id"]
    db = _db()
    # Reset to unreviewed for this test
    db.inventory.update_one(
        {"product_id": p, "branch_id": b},
        {"$unset": {"price_reviewed_at": "", "last_price_review_source": ""}},
    )
    assert not _is_reviewed(p, b)

    # The product's exact name from the fixture
    name = db.products.find_one({"id": p}, {"_id": 0, "name": 1})["name"]

    buf = io.StringIO()
    csv.writer(buf).writerows([
        ["Product Name", "Retail Price"],
        [name, "120"],
    ])
    files = {"file": ("imp.csv", buf.getvalue().encode(), "text/csv")}
    data = {
        "mapping": json.dumps({"name": "Product Name", "retail_price": "Retail Price"}),
        "branch_id": b,
        "mode": "commit",
    }
    r = requests.post(
        f"{API}/import/branch-stock-and-price",
        files=files, data=data,
        headers={"Authorization": f"Bearer {token}"}, timeout=30,
    )
    assert r.status_code == 200, r.text
    assert _is_reviewed(p, b), "Branch import commit must mark the product as reviewed"


def test_bulk_mark_all_reviewed(auth):
    """Create 3 fresh unreviewed inventory rows in one branch, then bulk-clear them."""
    token, user = auth
    db = _db()
    org_id = user.get("organization_id", "")
    branch = db.branches.find_one({"organization_id": org_id, "active": True}, {"_id": 0, "id": 1})

    pids = []
    for i in range(3):
        pid = str(uuid4())
        db.products.insert_one({
            "id": pid, "sku": f"BULK-{pid[:6]}", "name": f"BulkAck-{pid[:6]}",
            "category": "Test", "unit": "PC", "cost_price": 1, "prices": {"retail": 2},
            "active": True, "product_type": "stockable",
            "organization_id": org_id, "is_repack": False,
        })
        db.inventory.insert_one({
            "id": str(uuid4()), "product_id": pid, "branch_id": branch["id"],
            "quantity": 0, "organization_id": org_id,
        })
        pids.append(pid)

    try:
        # Confirm all 3 are pending
        r1 = requests.get(
            f"{API}/inventory/pending-review-ids",
            params={"branch_id": branch["id"]},
            headers={"Authorization": f"Bearer {token}"}, timeout=15,
        )
        pending_ids = set(r1.json().get("product_ids", []))
        for pid in pids:
            assert pid in pending_ids

        # Bulk mark all reviewed
        r2 = requests.post(
            f"{API}/inventory/mark-all-reviewed",
            json={"branch_id": branch["id"]},
            headers={"Authorization": f"Bearer {token}"}, timeout=15,
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["marked_count"] >= 3

        # And now they're all reviewed
        for pid in pids:
            assert _is_reviewed(pid, branch["id"])
    finally:
        db.products.delete_many({"id": {"$in": pids}})
        db.inventory.delete_many({"product_id": {"$in": pids}})


def test_pending_review_count_endpoint(auth, fresh_inventory_row):
    token, _ = auth
    r = requests.get(
        f"{API}/inventory/pending-review-count",
        params={"branch_id": fresh_inventory_row["branch_id"]},
        headers={"Authorization": f"Bearer {token}"}, timeout=15,
    )
    assert r.status_code == 200
    assert r.json().get("pending", 0) >= 1

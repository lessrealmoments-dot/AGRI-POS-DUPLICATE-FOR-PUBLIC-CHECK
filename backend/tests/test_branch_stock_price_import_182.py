"""
Iteration 182 — Branch-Specific Stock + Price Import.

Verifies that:
  - Empty price cells are SKIPPED (global price untouched)
  - Empty quantity cells are treated as 0
  - Writes go to branch_prices + inventory ONLY (never to product.prices)
  - Other branches are unaffected by branch-specific imports
"""
import io
import csv
from uuid import uuid4

import pytest
import requests

from _org_test_helpers import API, _db, ensure_org_admin_token


def _csv_bytes(rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerows(rows)
    return buf.getvalue().encode()


@pytest.fixture(scope="module")
def auth():
    token, user = ensure_org_admin_token()
    return token, user


@pytest.fixture
def seeded_products(auth):
    """Create two products with global prices we can later verify weren't touched."""
    db = _db()
    token, user = auth
    org_id = user.get("organization_id", "")
    products = []
    for name, retail in [("BSP-Test-A", 100.0), ("BSP-Test-B", 200.0)]:
        p = {
            "id": str(uuid4()),
            "sku": f"BSP-{name}",
            "name": name,
            "category": "Test",
            "unit": "PC",
            "cost_price": 50.0,
            "prices": {"retail": retail, "wholesale": retail * 0.9},
            "active": True,
            "product_type": "stockable",
            "organization_id": org_id,
        }
        db.products.insert_one(p)
        products.append(p)
    yield products
    db.products.delete_many({"id": {"$in": [p["id"] for p in products]}})
    db.branch_prices.delete_many({"product_id": {"$in": [p["id"] for p in products]}})
    db.inventory.delete_many({"product_id": {"$in": [p["id"] for p in products]}})


def _pick_two_branches(user):
    db = _db()
    org_id = user.get("organization_id", "")
    branches = list(db.branches.find({"organization_id": org_id, "active": True}, {"_id": 0, "id": 1, "name": 1}).limit(2))
    if len(branches) < 2:
        pytest.skip("Need at least 2 branches in the test org")
    return branches[0], branches[1]


def test_empty_price_skipped_empty_qty_skipped(auth, seeded_products):
    token, user = auth
    b1, _ = _pick_two_branches(user)

    # Row 1: full prices + qty.   Row 2: only qty (empty prices).
    csv_data = _csv_bytes([
        ["Product Name", "Cost Price", "Retail Price", "Wholesale Price", "Quantity"],
        ["BSP-Test-A", "60", "150", "135", "30"],
        ["BSP-Test-B", "", "", "", ""],   # empty everything → ALL skipped (no qty zero-out)
    ])
    mapping = {
        "name": "Product Name",
        "cost_price": "Cost Price",
        "retail_price": "Retail Price",
        "wholesale_price": "Wholesale Price",
        "quantity": "Quantity",
    }

    files = {"file": ("bsp.csv", csv_data, "text/csv")}
    data = {
        "mapping": __import__("json").dumps(mapping),
        "branch_id": b1["id"],
        "mode": "commit",
    }
    r = requests.post(
        f"{API}/import/branch-stock-and-price",
        files=files, data=data,
        headers={"Authorization": f"Bearer {token}"}, timeout=30,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "commit"
    assert body["prices_updated"] >= 1  # only A had non-empty prices
    assert body["qty_updated"] == 1     # only A — B's empty cell was skipped

    # Verify in DB: branch_prices for A has new prices, B has NO override doc OR no prices
    db = _db()
    a = next(p for p in seeded_products if p["name"] == "BSP-Test-A")
    b = next(p for p in seeded_products if p["name"] == "BSP-Test-B")

    bp_a = db.branch_prices.find_one({"product_id": a["id"], "branch_id": b1["id"]}, {"_id": 0})
    assert bp_a is not None
    assert bp_a["prices"]["retail"] == 150
    assert bp_a["prices"]["wholesale"] == 135
    assert bp_a["cost_price"] == 60

    bp_b = db.branch_prices.find_one({"product_id": b["id"], "branch_id": b1["id"]}, {"_id": 0})
    # B should have no override doc (all prices were empty)
    assert bp_b is None or not bp_b.get("prices")

    # Inventory: only A is present. B was untouched (empty quantity cell skipped).
    inv_a = db.inventory.find_one({"product_id": a["id"], "branch_id": b1["id"]}, {"_id": 0})
    inv_b = db.inventory.find_one({"product_id": b["id"], "branch_id": b1["id"]}, {"_id": 0})
    assert inv_a["quantity"] == 30
    assert inv_b is None, "Empty quantity cell must NOT create a zero-stock entry"


def test_explicit_zero_quantity_is_written(auth, seeded_products):
    """If user types 0 explicitly, stock should be set to 0 (zero is intentional, blank is not)."""
    token, user = auth
    b1, _ = _pick_two_branches(user)
    a = next(p for p in seeded_products if p["name"] == "BSP-Test-A")

    csv_data = _csv_bytes([
        ["Product Name", "Quantity"],
        ["BSP-Test-A", "0"],
    ])
    mapping = {"name": "Product Name", "quantity": "Quantity"}
    r = requests.post(
        f"{API}/import/branch-stock-and-price",
        files={"file": ("z.csv", csv_data, "text/csv")},
        data={"mapping": __import__("json").dumps(mapping), "branch_id": b1["id"], "mode": "commit"},
        headers={"Authorization": f"Bearer {token}"}, timeout=30,
    )
    assert r.status_code == 200, r.text
    db = _db()
    inv = db.inventory.find_one({"product_id": a["id"], "branch_id": b1["id"]}, {"_id": 0})
    assert inv is not None
    assert inv["quantity"] == 0


def test_global_product_prices_not_touched(auth, seeded_products):
    """Branch-stock-price import must NEVER mutate product.prices."""
    db = _db()
    token, user = auth
    b1, _ = _pick_two_branches(user)
    a = next(p for p in seeded_products if p["name"] == "BSP-Test-A")
    original_prices = (db.products.find_one({"id": a["id"]}, {"_id": 0, "prices": 1}) or {}).get("prices") or {}

    csv_data = _csv_bytes([
        ["Product Name", "Retail Price", "Quantity"],
        ["BSP-Test-A", "9999", "5"],
    ])
    mapping = {"name": "Product Name", "retail_price": "Retail Price", "quantity": "Quantity"}
    r = requests.post(
        f"{API}/import/branch-stock-and-price",
        files={"file": ("x.csv", csv_data, "text/csv")},
        data={"mapping": __import__("json").dumps(mapping), "branch_id": b1["id"], "mode": "commit"},
        headers={"Authorization": f"Bearer {token}"}, timeout=30,
    )
    assert r.status_code == 200, r.text

    # Product.prices is unchanged
    after = (db.products.find_one({"id": a["id"]}, {"_id": 0, "prices": 1}) or {}).get("prices") or {}
    assert after == original_prices, f"Global prices were mutated: {after} != {original_prices}"


def test_other_branch_unaffected(auth, seeded_products):
    """Writing to branch1 must not appear in branch2's overrides."""
    db = _db()
    token, user = auth
    b1, b2 = _pick_two_branches(user)
    a = next(p for p in seeded_products if p["name"] == "BSP-Test-A")

    csv_data = _csv_bytes([
        ["Product Name", "Retail Price"],
        ["BSP-Test-A", "555"],
    ])
    mapping = {"name": "Product Name", "retail_price": "Retail Price"}
    r = requests.post(
        f"{API}/import/branch-stock-and-price",
        files={"file": ("y.csv", csv_data, "text/csv")},
        data={"mapping": __import__("json").dumps(mapping), "branch_id": b1["id"], "mode": "commit"},
        headers={"Authorization": f"Bearer {token}"}, timeout=30,
    )
    assert r.status_code == 200

    bp_b2 = db.branch_prices.find_one({"product_id": a["id"], "branch_id": b2["id"]}, {"_id": 0})
    assert bp_b2 is None, "Branch 2 should have no override after a branch-1 import"


def test_preview_mode_does_not_write(auth, seeded_products):
    db = _db()
    token, user = auth
    b1, _ = _pick_two_branches(user)
    a = next(p for p in seeded_products if p["name"] == "BSP-Test-A")

    # Snapshot existing inventory + branch_prices for A in b1
    before_bp = db.branch_prices.find_one({"product_id": a["id"], "branch_id": b1["id"]}, {"_id": 0})
    before_inv = db.inventory.find_one({"product_id": a["id"], "branch_id": b1["id"]}, {"_id": 0})

    csv_data = _csv_bytes([
        ["Product Name", "Retail Price", "Quantity"],
        ["BSP-Test-A", "777", "999"],
    ])
    mapping = {"name": "Product Name", "retail_price": "Retail Price", "quantity": "Quantity"}
    r = requests.post(
        f"{API}/import/branch-stock-and-price",
        files={"file": ("p.csv", csv_data, "text/csv")},
        data={"mapping": __import__("json").dumps(mapping), "branch_id": b1["id"], "mode": "preview"},
        headers={"Authorization": f"Bearer {token}"}, timeout=30,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "preview"
    assert body["matched_count"] >= 1

    # Confirm DB unchanged
    after_bp = db.branch_prices.find_one({"product_id": a["id"], "branch_id": b1["id"]}, {"_id": 0})
    after_inv = db.inventory.find_one({"product_id": a["id"], "branch_id": b1["id"]}, {"_id": 0})
    if before_bp:
        assert (after_bp or {}).get("prices") == (before_bp or {}).get("prices")
    if before_inv:
        assert (after_inv or {}).get("quantity") == (before_inv or {}).get("quantity")


def test_unknown_product_reported_not_errored(auth, seeded_products):
    token, user = auth
    b1, _ = _pick_two_branches(user)

    csv_data = _csv_bytes([
        ["Product Name", "Retail Price"],
        ["This-Product-Does-Not-Exist-9999", "100"],
    ])
    mapping = {"name": "Product Name", "retail_price": "Retail Price"}
    r = requests.post(
        f"{API}/import/branch-stock-and-price",
        files={"file": ("u.csv", csv_data, "text/csv")},
        data={"mapping": __import__("json").dumps(mapping), "branch_id": b1["id"], "mode": "preview"},
        headers={"Authorization": f"Bearer {token}"}, timeout=30,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert any(u["name"].startswith("This-Product-Does-Not-Exist") for u in body["unmatched"])
    assert body["matched_count"] == 0

"""
Iteration 191 — Refined product search: short-numeric prefix-of-word.

Verifies that the strict pass on `/api/products/search-detail` no longer
leaks unrelated products via SKU collisions on short numeric tokens:

  • "14-14-14" must NOT match a product whose SKU happens to contain "14"
    but whose NAME does not start a word with "14".
  • "Galimax 1" must match only Galimax 1, NOT Galimax 2 / 3 / 21
    (because their NAME has no word starting with "1").
  • "Galimax 2" must match BOTH Galimax 2 and Galimax 21 (both have a name
    word starting with "2").
"""
import pytest
import requests
from datetime import datetime, timezone
from uuid import uuid4

from _org_test_helpers import API, _db, ensure_org_admin_token

SEED_SKU_PREFIX = "P-TOKSEARCH191-"


@pytest.fixture(scope="module")
def auth_and_seed():
    token, user = ensure_org_admin_token()
    org_id = user["organization_id"]
    db = _db()

    db.products.delete_many({"sku": {"$regex": f"^{SEED_SKU_PREFIX}"}})
    now = datetime.now(timezone.utc).isoformat()

    seeds = [
        ("14-14-14 ATLAS",                        f"{SEED_SKU_PREFIX}141414AT"),
        ("14-14-14 PHILPHOS",                     f"{SEED_SKU_PREFIX}141414PH"),
        ("Galimax 1 Pilmico - Poultry Feeds",     f"{SEED_SKU_PREFIX}GMX1"),
        ("Galimax 2 Plus",                        f"{SEED_SKU_PREFIX}GMX2"),
        ("Galimax 3",                             f"{SEED_SKU_PREFIX}GMX3"),
        ("Galimax 21",                            f"{SEED_SKU_PREFIX}GMX21"),
        # Decoy 1: name unrelated, SKU contains "1" digits — must NOT match
        # short-numeric "1" queries.
        ("FINEX Decoy",                           f"{SEED_SKU_PREFIX}FINEX1ABFA"),
        # Decoy 2: name unrelated, SKU contains "14" digits — must NOT match
        # "14-14-14" queries.
        ("BOYOT Decoy",                           f"{SEED_SKU_PREFIX}141ABF"),
    ]
    db.products.insert_many([
        {"id": str(uuid4()), "name": name, "sku": sku, "barcode": "",
         "active": True, "organization_id": org_id, "prices": {},
         "unit": "sack", "created_at": now}
        for name, sku in seeds
    ])

    yield token, user

    db.products.delete_many({"sku": {"$regex": f"^{SEED_SKU_PREFIX}"}})


def _names(token, q):
    r = requests.get(
        f"{API}/products/search-detail",
        params={"q": q},
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    assert r.status_code == 200, r.text
    return [p["name"] for p in r.json()]


def test_141414_does_not_leak_via_sku_substring(auth_and_seed):
    token, _ = auth_and_seed
    names = _names(token, "14-14-14")
    assert "14-14-14 ATLAS" in names
    assert "14-14-14 PHILPHOS" in names
    # Decoys with "14" only in SKU must be excluded
    assert "FINEX Decoy" not in names
    assert "BOYOT Decoy" not in names


def test_galimax_1_does_not_leak_other_grades(auth_and_seed):
    token, _ = auth_and_seed
    names = _names(token, "Galimax 1")
    assert names == ["Galimax 1 Pilmico - Poultry Feeds"], names


def test_galimax_2_includes_galimax_21(auth_and_seed):
    token, _ = auth_and_seed
    names = _names(token, "Galimax 2")
    assert "Galimax 2 Plus" in names
    assert "Galimax 21" in names
    # Galimax 1 / 3 must NOT appear
    assert "Galimax 1 Pilmico - Poultry Feeds" not in names
    assert "Galimax 3" not in names


def test_galimax_21_is_specific(auth_and_seed):
    token, _ = auth_and_seed
    names = _names(token, "Galimax 21")
    assert names == ["Galimax 21"], names


def test_alpha_only_query_returns_all_galimax(auth_and_seed):
    token, _ = auth_and_seed
    names = _names(token, "Galimax")
    assert {"Galimax 1 Pilmico - Poultry Feeds", "Galimax 2 Plus",
            "Galimax 3", "Galimax 21"}.issubset(set(names))


def test_name_prefix_outranks_substring(auth_and_seed):
    """When the query is a name-prefix and a substring of multiple products,
    the prefix match must come first."""
    token, _ = auth_and_seed
    names = _names(token, "Galimax 2")
    # "Galimax 2 Plus" starts with the query → rank 0
    # "Galimax 21" starts with the query → rank 0, tiebreak by name length
    # so "Galimax 21" (10 chars) wins over "Galimax 2 Plus" (14)
    assert names[0] == "Galimax 21"

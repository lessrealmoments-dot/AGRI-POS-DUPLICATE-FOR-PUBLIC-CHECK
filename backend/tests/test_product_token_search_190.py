"""
Iteration 190 — Token-based product search.

Verifies that `/api/products/search-detail` matches words in any order:
  • "Galimax 1 Poultry Feeds Pilmico" finds "Galimax 1 Pilmico - Poultry Feeds"
  • "Starter Vital" finds "Starter Premium Vital"
  • Reverse / partial token sets still match
  • A query with at least one non-existent token returns no hits (AND semantics)
"""
import pytest
import requests
from datetime import datetime, timezone
from uuid import uuid4

from _org_test_helpers import API, _db, ensure_org_admin_token


@pytest.fixture(scope="module")
def auth_and_seed():
    token, user = ensure_org_admin_token()
    org_id = user["organization_id"]
    db = _db()

    skus = ["P-TOKSEARCH-GMX1", "P-TOKSEARCH-STR-VIT"]
    db.products.delete_many({"sku": {"$in": skus}})
    now = datetime.now(timezone.utc).isoformat()
    db.products.insert_many([
        {"id": str(uuid4()), "name": "Galimax 1 Pilmico - Poultry Feeds",
         "sku": skus[0], "barcode": "", "active": True,
         "organization_id": org_id, "prices": {}, "unit": "sack", "created_at": now},
        {"id": str(uuid4()), "name": "Starter Premium Vital",
         "sku": skus[1], "barcode": "", "active": True,
         "organization_id": org_id, "prices": {}, "unit": "sack", "created_at": now},
    ])

    yield token, user

    db.products.delete_many({"sku": {"$in": skus}})


def _search(token, q):
    r = requests.get(
        f"{API}/products/search-detail",
        params={"q": q},
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    assert r.status_code == 200, r.text
    return [p["name"] for p in r.json()]


def test_out_of_order_tokens_match(auth_and_seed):
    token, _ = auth_and_seed
    names = _search(token, "Galimax 1 Poultry Feeds Pilmico")
    assert "Galimax 1 Pilmico - Poultry Feeds" in names


def test_skipped_middle_word_matches(auth_and_seed):
    token, _ = auth_and_seed
    names = _search(token, "Starter Vital")
    assert "Starter Premium Vital" in names


def test_reverse_partial_tokens_match(auth_and_seed):
    token, _ = auth_and_seed
    names = _search(token, "Pilmico Galimax")
    assert "Galimax 1 Pilmico - Poultry Feeds" in names


def test_unknown_token_excludes_result(auth_and_seed):
    """Token-AND semantics: if any token cannot be matched, the row is out."""
    token, _ = auth_and_seed
    names = _search(token, "Galimax NotInName")
    assert "Galimax 1 Pilmico - Poultry Feeds" not in names


def test_dash_separator_treated_as_token_break(auth_and_seed):
    token, _ = auth_and_seed
    # User types the product name verbatim with the dash — should still match
    names = _search(token, "Galimax - Pilmico")
    assert "Galimax 1 Pilmico - Poultry Feeds" in names

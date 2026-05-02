"""
Regression: /api/products/search-detail fuzzy fallback (typo tolerance).

Verifies:
  1. Strict pass returns clean results without `_fuzzy_hint`.
  2. Typo query (Levenshtein distance 1-2 on a name word) triggers the
     rapidfuzz fallback at ≥80% similarity and tags `_fuzzy_hint` on every
     returned item.
  3. Garbage queries return [] (no false positives).
  4. Numeric/short tokens still match strictly — "1 kg" must NOT be
     fuzzy-matched to "2 kg".
"""
import os
import requests
import uuid


BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://sms-close-scheduler-1.preview.emergentagent.com")
API = f"{BASE_URL}/api"


def _login():
    r = requests.post(
        f"{API}/auth/login",
        json={"email": "test_org_admin@regression.local", "password": "RegressionPass!2026"},
        timeout=15,
    )
    r.raise_for_status()
    body = r.json()
    return body.get("token") or body.get("access_token")


def _seed_product(token, name):
    r = requests.post(
        f"{API}/products",
        json={
            "name": name,
            "sku": f"FUZZ-{uuid.uuid4().hex[:6]}",
            "category": "Fuzzy Test",
            "unit": "pc",
            "prices": {"retail": 100, "wholesale": 90, "dealer": 80},
            "cost_price": 70,
        },
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    if r.status_code not in (200, 201):
        return None
    return r.json().get("id")


def test_fuzzy_strict_match_no_hint():
    token = _login()
    suffix = uuid.uuid4().hex[:6]
    pid = _seed_product(token, f"Promix Starter {suffix}")
    assert pid, "seed failed"
    try:
        r = requests.get(
            f"{API}/products/search-detail",
            params={"q": "Promix"},
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        assert len(data) >= 1
        # Strict hits: no fuzzy hint
        assert all("_fuzzy_hint" not in p for p in data), "strict pass should NOT carry _fuzzy_hint"
    finally:
        requests.delete(f"{API}/products/{pid}", headers={"Authorization": f"Bearer {token}"})


def test_fuzzy_typo_triggers_hint():
    token = _login()
    suffix = uuid.uuid4().hex[:6]
    pid = _seed_product(token, f"Promix Starter {suffix}")
    assert pid, "seed failed"
    try:
        # "Promex" (e→i swap) should not strict-match but should fuzzy-match Promix
        r = requests.get(
            f"{API}/products/search-detail",
            params={"q": "Promex"},
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        assert len(data) >= 1, "fuzzy fallback should return at least the typo'd Promix product"
        hint = data[0].get("_fuzzy_hint")
        assert hint is not None, "first item should carry _fuzzy_hint"
        assert hint.get("query") == "Promex"
        assert hint.get("count", 0) >= 1
    finally:
        requests.delete(f"{API}/products/{pid}", headers={"Authorization": f"Bearer {token}"})


def test_fuzzy_garbage_query_returns_empty():
    token = _login()
    r = requests.get(
        f"{API}/products/search-detail",
        params={"q": "zzzqqqxxxnoresult"},
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    r.raise_for_status()
    assert r.json() == [], "garbage query must not fuzzy-match anything"


def test_fuzzy_numeric_token_stays_strict():
    """Critical safety: '1 kg' must NEVER fuzzy-match '2 kg' — these are
    different SKUs in real life and silently swapping them ruins
    inventory + sales."""
    token = _login()
    suffix = uuid.uuid4().hex[:6]
    p1 = _seed_product(token, f"Galimax 1 {suffix}")
    p2 = _seed_product(token, f"Galimax 2 {suffix}")
    assert p1 and p2
    try:
        r = requests.get(
            f"{API}/products/search-detail",
            params={"q": f"Galimax 1 {suffix}"},
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        r.raise_for_status()
        names = [p.get("name") for p in r.json()]
        assert any(f"Galimax 1 {suffix}" == n for n in names), "should match Galimax 1"
        assert not any(f"Galimax 2 {suffix}" == n for n in names), \
            "MUST NOT match Galimax 2 — short numeric token must stay strict"
    finally:
        for pid in (p1, p2):
            requests.delete(f"{API}/products/{pid}", headers={"Authorization": f"Bearer {token}"})


if __name__ == "__main__":
    test_fuzzy_strict_match_no_hint()
    print("PASS: strict_match_no_hint")
    test_fuzzy_typo_triggers_hint()
    print("PASS: typo_triggers_hint")
    test_fuzzy_garbage_query_returns_empty()
    print("PASS: garbage_returns_empty")
    test_fuzzy_numeric_token_stays_strict()
    print("PASS: numeric_token_stays_strict")
    print("\nAll fuzzy search regression tests passed.")

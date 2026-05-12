"""
Iter 171: verify /api/import/products/overwrite merges mapped fields without
clobbering unmapped data (especially preserves prices.retail when only
wholesale_price is mapped).
"""
import io
import csv
import os
import time
import requests

API = os.environ.get("API_URL", "https://phase5-audit-fix.preview.emergentagent.com").rstrip("/") + "/api"
EMAIL = os.environ.get("TEST_EMAIL", "janmarkeahig@gmail.com")
PASSWORD = os.environ.get("TEST_PASSWORD", "Aa@58798546521325")


def _login():
    r = requests.post(f"{API}/auth/login", json={"email": EMAIL, "password": PASSWORD}, timeout=15)
    r.raise_for_status()
    return r.json()["token"]


def _headers(t):
    return {"Authorization": f"Bearer {t}"}


def _csv_bytes(rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    for row in rows:
        w.writerow(row)
    return buf.getvalue().encode()


def test_overwrite_merges_wholesale_without_dropping_retail():
    t = _login()
    h = _headers(t)
    ts = int(time.time())
    name = f"TEST_Merge_{ts}"

    # Step 1: import a product with retail=100, wholesale=85
    csv1 = _csv_bytes([
        ["Product Name", "Retail Price", "Wholesale Price"],
        [name, "100", "85"],
    ])
    files = {"file": ("seed.csv", csv1, "text/csv")}
    data = {"mapping": '{"name":"Product Name","retail_price":"Retail Price","wholesale_price":"Wholesale Price"}'}
    r = requests.post(f"{API}/import/products", headers=h, files=files, data=data, timeout=30)
    r.raise_for_status()
    assert r.json()["imported"] >= 1

    # Find the product
    r = requests.get(f"{API}/products", headers=h, params={"search": name, "limit": 5}, timeout=15)
    products = r.json()["products"]
    p = next(x for x in products if x["name"] == name)
    pid = p["id"]
    assert p["prices"].get("retail") == 100, f"expected retail 100, got {p['prices']}"
    assert p["prices"].get("wholesale") == 85, f"expected wholesale 85, got {p['prices']}"

    # Step 2: overwrite with ONLY wholesale_price mapped, new value 75
    csv2 = _csv_bytes([
        ["Product Name", "Wholesale Price"],
        [name, "75"],
    ])
    files = {"file": ("over.csv", csv2, "text/csv")}
    data = {
        "mapping": '{"name":"Product Name","wholesale_price":"Wholesale Price"}',
        "product_ids": f'["{pid}"]',
    }
    r = requests.post(f"{API}/import/products/overwrite", headers=h, files=files, data=data, timeout=30)
    r.raise_for_status()
    body = r.json()
    assert body["updated"] == 1, f"expected 1 updated, got {body}"

    # Step 3: verify retail PRESERVED, wholesale UPDATED
    r = requests.get(f"{API}/products", headers=h, params={"search": name, "limit": 5}, timeout=15)
    p = next(x for x in r.json()["products"] if x["name"] == name)
    assert p["prices"].get("retail") == 100, f"retail clobbered! prices={p['prices']}"
    assert p["prices"].get("wholesale") == 75, f"wholesale not updated. prices={p['prices']}"
    print(f"PASS · merged prices: {p['prices']}")

    # Cleanup
    requests.delete(f"{API}/products/{pid}", headers=h, timeout=10)


def test_overwrite_does_not_match_other_products():
    """Selecting only product A but file has rows for A and B → only A updated."""
    t = _login()
    h = _headers(t)
    ts = int(time.time())
    name_a = f"TEST_OnlyA_{ts}"
    name_b = f"TEST_OnlyB_{ts}"

    # Seed both
    csv1 = _csv_bytes([
        ["Product Name", "Retail Price"],
        [name_a, "200"],
        [name_b, "300"],
    ])
    files = {"file": ("seed.csv", csv1, "text/csv")}
    data = {"mapping": '{"name":"Product Name","retail_price":"Retail Price"}'}
    r = requests.post(f"{API}/import/products", headers=h, files=files, data=data, timeout=30)
    r.raise_for_status()

    r = requests.get(f"{API}/products", headers=h, params={"search": "TEST_Only", "limit": 10}, timeout=15)
    by_name = {p["name"]: p["id"] for p in r.json()["products"]}
    pid_a = by_name[name_a]
    pid_b = by_name[name_b]

    # Overwrite — file has BOTH but we only select A
    csv2 = _csv_bytes([
        ["Product Name", "Wholesale Price"],
        [name_a, "150"],
        [name_b, "150"],
    ])
    files = {"file": ("over.csv", csv2, "text/csv")}
    data = {
        "mapping": '{"name":"Product Name","wholesale_price":"Wholesale Price"}',
        "product_ids": f'["{pid_a}"]',
    }
    r = requests.post(f"{API}/import/products/overwrite", headers=h, files=files, data=data, timeout=30)
    r.raise_for_status()
    body = r.json()
    assert body["updated"] == 1, body
    assert body["not_matched"] == 1, body  # B was in file but not selected

    # Verify
    r = requests.get(f"{API}/products", headers=h, params={"search": "TEST_Only", "limit": 10}, timeout=15)
    products = {p["name"]: p for p in r.json()["products"]}
    assert products[name_a]["prices"].get("wholesale") == 150
    assert "wholesale" not in products[name_b]["prices"], f"B should not have wholesale: {products[name_b]['prices']}"
    print(f"PASS · A merged, B untouched")

    requests.delete(f"{API}/products/{pid_a}", headers=h, timeout=10)
    requests.delete(f"{API}/products/{pid_b}", headers=h, timeout=10)


if __name__ == "__main__":
    test_overwrite_merges_wholesale_without_dropping_retail()
    test_overwrite_does_not_match_other_products()
    print("\nAll tests passed.")

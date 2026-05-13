"""
Iter 172: tests for
  1. POST /api/import/products/update-existing — match-by-name + merge
  2. GET  /api/products/export-csv — header & row sanity
"""
import io
import csv
import os
import time
import requests

API = os.environ.get("API_URL", "https://supplier-sync-23.preview.emergentagent.com").rstrip("/") + "/api"
EMAIL = os.environ.get("TEST_EMAIL", "janmarkeahig@gmail.com")
PASSWORD = os.environ.get("TEST_PASSWORD", "Aa@58798546521325")


def _login():
    r = requests.post(f"{API}/auth/login", json={"email": EMAIL, "password": PASSWORD}, timeout=15)
    r.raise_for_status()
    return r.json()["token"]


def _h(t): return {"Authorization": f"Bearer {t}"}


def _csv_bytes(rows):
    buf = io.StringIO()
    csv.writer(buf).writerows(rows)
    return buf.getvalue().encode()


def test_update_existing_merges_only_mapped_fields():
    t = _login(); h = _h(t); ts = int(time.time())
    name = f"TEST_UpdEx_{ts}"

    # Seed
    files = {"file": ("seed.csv", _csv_bytes([
        ["Product Name", "Retail Price", "Cost Price"],
        [name, "500", "400"],
    ]), "text/csv")}
    data = {"mapping": '{"name":"Product Name","retail_price":"Retail Price","cost_price":"Cost Price"}'}
    r = requests.post(f"{API}/import/products", headers=h, files=files, data=data, timeout=30)
    r.raise_for_status()

    p = next(x for x in requests.get(f"{API}/products", headers=h, params={"search": name, "limit": 5}).json()["products"] if x["name"] == name)
    pid = p["id"]
    assert p["prices"]["retail"] == 500 and p["cost_price"] == 400

    # Run update-existing with only Wholesale mapped
    files = {"file": ("upd.csv", _csv_bytes([
        ["Product Name", "Wholesale Price"],
        [name, "450"],
        [f"NONEXISTENT_PRODUCT_{ts}", "999"],
    ]), "text/csv")}
    data = {"mapping": '{"name":"Product Name","wholesale_price":"Wholesale Price"}'}
    r = requests.post(f"{API}/import/products/update-existing", headers=h, files=files, data=data, timeout=30)
    r.raise_for_status()
    body = r.json()
    assert body["updated"] == 1, body
    assert len(body["not_found"]) == 1, body
    assert body["not_found"][0]["name"].startswith("NONEXISTENT_PRODUCT"), body

    # Verify retail+cost preserved, wholesale added
    p = next(x for x in requests.get(f"{API}/products", headers=h, params={"search": name, "limit": 5}).json()["products"] if x["name"] == name)
    assert p["prices"]["retail"] == 500, f"retail clobbered! {p['prices']}"
    assert p["prices"]["wholesale"] == 450, f"wholesale not set! {p['prices']}"
    assert p["cost_price"] == 400, f"cost clobbered! {p}"
    print(f"PASS · update-existing merged correctly: {p['prices']}")

    requests.delete(f"{API}/products/{pid}", headers=h, timeout=10)


def test_export_csv_returns_compatible_format():
    t = _login(); h = _h(t)
    r = requests.get(f"{API}/products/export-csv", headers=h, timeout=30)
    r.raise_for_status()
    assert "text/csv" in r.headers.get("content-type", ""), r.headers
    txt = r.content.decode("utf-8-sig")
    reader = csv.reader(io.StringIO(txt))
    header = next(reader)
    # Required base columns
    for col in ["Product Name", "SKU", "Category", "Unit", "Cost Price"]:
        assert col in header, f"missing {col} in {header}"
    # At least one scheme column (Retail Price normally)
    scheme_cols = [c for c in header if c.endswith(" Price") and c not in ("Cost Price",)]
    assert len(scheme_cols) >= 1, f"no scheme columns in export: {header}"
    rows = list(reader)
    print(f"PASS · export-csv returned {len(rows)} rows, header includes {len(scheme_cols)} scheme columns: {scheme_cols}")


if __name__ == "__main__":
    test_update_existing_merges_only_mapped_fields()
    test_export_csv_returns_compatible_format()
    print("\nAll tests passed.")

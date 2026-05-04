"""
Test (Iteration 225): Delta sync must include products whose `branch_prices`
row was updated since `last_sync`, even if the master products row was not
modified.

Scenario reproduced:
    1) Initial full sync caches product P with global retail = 2065.
    2) Admin sets branch retail override for branch B to 2075 (writes
       branch_prices, but the products document is untouched).
    3) Terminal performs a delta sync passing last_sync = T1.
       BUG: the response did not include P, so the terminal kept its
       cached `prices.retail = 2065`.
       FIX: P must appear in `products[]` with the merged
            `prices.retail = 2075`.
"""
import os
import time
import requests
from datetime import datetime, timezone, timedelta

from _org_test_helpers import (
    ensure_org_admin_token,
    API,
    _db,
)


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def test_delta_sync_includes_products_with_changed_branch_prices():
    token, user = ensure_org_admin_token()
    headers = {"Authorization": f"Bearer {token}"}
    db = _db()
    org_id = user.get("organization_id")
    assert org_id, "Test admin missing organization_id"

    # Pick any branch
    br = requests.get(f"{API}/branches", headers=headers).json()
    assert br, "No branches available for test"
    branch_id = br[0]["id"]

    # Create a fresh test product (must be tenant-scoped for TenantCollection)
    pid = f"test-galimax-225-{int(time.time())}"
    db.products.insert_one({
        "id": pid,
        "organization_id": org_id,
        "name": "Galimax 1 (test 225)",
        "sku": pid,
        "active": True,
        "is_repack": False,
        "prices": {"retail": 2065.0, "wholesale": 2050.0},
        "cost_price": 2000.0,
        "unit": "sack",
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    })

    try:
        # Step 1 — full sync to establish a last_sync timestamp.
        full = requests.get(f"{API}/sync/pos-data",
                            headers=headers,
                            params={"branch_id": branch_id})
        assert full.status_code == 200, full.text
        last_sync = full.json()["sync_time"]
        # Sanity: full sync sees the global retail
        full_p = next((p for p in full.json()["products"] if p["id"] == pid), None)
        assert full_p is not None, "Test product missing from full sync"
        assert full_p["prices"]["retail"] == 2065.0

        # Step 2 — write a branch override AFTER last_sync.
        time.sleep(1.1)  # ensure updated_at > last_sync at second granularity
        db.branch_prices.update_one(
            {"product_id": pid, "branch_id": branch_id},
            {"$set": {
                "id": f"bp-{pid}",
                "organization_id": org_id,
                "product_id": pid,
                "branch_id": branch_id,
                "prices": {"retail": 2075.0},
                "updated_at": _now_iso(),
                "created_at": _now_iso(),
            }},
            upsert=True,
        )

        # Step 3 — delta sync. Product master was NOT updated, only
        # branch_prices. The product MUST still appear in the delta with the
        # merged price.
        delta = requests.get(f"{API}/sync/pos-data",
                             headers=headers,
                             params={"branch_id": branch_id, "last_sync": last_sync})
        assert delta.status_code == 200, delta.text
        body = delta.json()
        assert body["is_delta"] is True

        delta_p = next((p for p in body["products"] if p["id"] == pid), None)
        assert delta_p is not None, (
            "BUG: delta sync did not include product whose branch_prices changed. "
            "Terminal cache will keep the stale global retail price."
        )
        assert delta_p["prices"]["retail"] == 2075.0, (
            f"BUG: branch override not merged into delta payload — "
            f"got {delta_p['prices']['retail']}, expected 2075.0"
        )
        # wholesale should fall through to the global value (override only set retail)
        assert delta_p["prices"].get("wholesale") == 2050.0
    finally:
        db.products.delete_one({"id": pid})
        db.branch_prices.delete_one({"product_id": pid, "branch_id": branch_id})


if __name__ == "__main__":
    test_delta_sync_includes_products_with_changed_branch_prices()
    print("OK")

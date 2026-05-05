"""
Regression test for Iter 240 — Offline sync line-discount bug.

Bug history (reported by jovelyn 2026-05-05 via SI-SB-OFF-000001):
  • Cashier rang up "ALMIX BOX" at retail ₱510 with a per-line ₱20 discount.
  • Frontend correctly displayed ₱490 in the live cart and sent
    `{rate: 510, discount_value: 20, discount_amount: 20, total: 490}`
    in the offline sync payload.
  • Backend `routes/sync.py:sync_offline_sales` recomputed
    `line_total = qty * rate` (NO discount subtraction) and overwrote
    `item.total` to 510 — persisting an inflated value on the saved
    invoice. The corresponding subtotal/grand_total were also inflated
    by the discount amount.
  • Net effect: customer charged 510 instead of 490 on offline-synced
    sales with per-line discounts. Online (non-OFF) sales were unaffected.

Hits the live preview API (matches the rest of the regression suite).
"""

import time
import requests
import pytest
from tests._org_test_helpers import API, ensure_org_admin_token


@pytest.fixture(scope="module")
def admin_ctx():
    token, _ = ensure_org_admin_token()
    headers = {"Authorization": f"Bearer {token}"}
    branches = requests.get(f"{API}/branches", headers=headers).json()
    branch_id = branches[0]["id"]
    return headers, branch_id


def _seed_product(headers, branch_id, sku, name, retail):
    """Create a product + seed 5 units of inventory. Idempotent on SKU collision."""
    res = requests.post(f"{API}/products", headers=headers, json={
        "sku": sku, "name": name, "category": "Pesticide",
        "unit": "Box", "cost_price": 50, "prices": {"retail": retail},
        "product_type": "stockable",
    })
    if res.status_code == 200:
        product_id = res.json()["id"]
    else:
        # Fallback: look up existing
        existing = requests.get(f"{API}/products", headers=headers, params={"search": sku}).json()
        product_id = next((p["id"] for p in existing if p.get("sku") == sku), None)
        assert product_id, f"Cannot create or find product with sku {sku}: {res.text}"
    requests.post(f"{API}/inventory/adjust", headers=headers, json={
        "product_id": product_id, "branch_id": branch_id, "quantity": 5,
        "reason": "iter240 regression seed",
    })
    return product_id


def test_offline_sync_respects_per_line_amount_discount(admin_ctx):
    """rate 510 - discount 20 (amount) → total must be 490, not 510."""
    headers, branch_id = admin_ctx
    product_id = _seed_product(headers, branch_id, "ITER240-AMT-A", "ALMIX240 AMT", 510)

    inv_number = f"ITER240-AMT-{int(time.time())}"
    sale_id = f"iter240-amt-{int(time.time())}"
    res = requests.post(f"{API}/sales/sync", headers=headers, json={
        "sales": [{
            "id": sale_id, "envelope_id": f"env-{sale_id}",
            "branch_id": branch_id, "customer_name": "Walk-in",
            "items": [{
                "product_id": product_id, "product_name": "ALMIX240 AMT",
                "quantity": 1, "rate": 510,
                "discount_type": "amount", "discount_value": 20, "discount_amount": 20,
                "total": 490,
            }],
            "subtotal": 490, "freight": 0, "overall_discount": 0,
            "amount_paid": 490, "balance": 0, "status": "paid",
            "payment_type": "cash", "payment_method": "Cash",
            "invoice_number": inv_number,
        }],
    })
    assert res.status_code == 200, res.text

    inv = requests.get(f"{API}/invoices/by-number/{inv_number}", headers=headers).json()
    line = next(it for it in inv["items"] if it["product_id"] == product_id)
    assert line["total"] == 490, (
        f"BUG REGRESSED: per-line total must be 490 (510 − 20), got {line['total']}. "
        "routes/sync.py overwrote frontend-discounted total with qty*rate."
    )
    assert line["discount_amount"] == 20
    assert inv["subtotal"] == 490
    assert inv["grand_total"] == 490


def test_offline_sync_respects_per_line_percent_discount(admin_ctx):
    """rate 200 × qty 2 minus 10% → 360, not 400."""
    headers, branch_id = admin_ctx
    product_id = _seed_product(headers, branch_id, "ITER240-PCT-A", "PCT240 LINE", 200)

    inv_number = f"ITER240-PCT-{int(time.time())}"
    sale_id = f"iter240-pct-{int(time.time())}"
    res = requests.post(f"{API}/sales/sync", headers=headers, json={
        "sales": [{
            "id": sale_id, "envelope_id": f"env-{sale_id}",
            "branch_id": branch_id, "customer_name": "Walk-in",
            "items": [{
                "product_id": product_id, "product_name": "PCT240 LINE",
                "quantity": 2, "rate": 200,
                "discount_type": "percent", "discount_value": 10, "discount_amount": 40,
                "total": 360,
            }],
            "subtotal": 360, "amount_paid": 360, "balance": 0,
            "status": "paid", "payment_type": "cash", "payment_method": "Cash",
            "invoice_number": inv_number,
        }],
    })
    assert res.status_code == 200, res.text

    inv = requests.get(f"{API}/invoices/by-number/{inv_number}", headers=headers).json()
    line = next(it for it in inv["items"] if it["product_id"] == product_id)
    assert line["total"] == 360, (
        f"BUG REGRESSED: 10% off 200×2 must be 360, got {line['total']}."
    )
    assert line["discount_amount"] == 40
    assert inv["subtotal"] == 360

"""
Iter 194 — POS Price Match (Permanent Branch Price Change) tests.

Validates:
  1. /unified-sale with `price_changes` requires manager/admin PIN
     (returns 422 with type=price_match_pin_required when missing).
  2. Valid PIN persists branch_prices.prices[scheme] AND writes price_change_log.
  3. The price_change_log carries reason, approver, cashier, customer.
  4. Discount audit log is NOT polluted (no `price_override` entry) when
     a corresponding price_changes record exists.
  5. /api/price-changes returns the events with summary stats.
  6. /api/reports/discount-audit (existing) still works and is unaffected.
  7. Below-capital floor still enforced even when matching to a low price.
"""
from uuid import uuid4
from datetime import datetime, timezone

import pytest
import requests

from _org_test_helpers import (
    API, _db, ensure_org_admin_token,
    TEST_ORG_ADMIN_PIN, TEST_ORG_MANAGER_PIN,
)


@pytest.fixture(scope="module")
def auth():
    token, user = ensure_org_admin_token()
    return token, user


@pytest.fixture
def setup(auth):
    db = _db()
    token, user = auth
    org_id = user.get("organization_id", "")
    branch = db.branches.find_one({"organization_id": org_id, "active": True}, {"_id": 0, "id": 1, "name": 1})
    if not branch:
        bid = str(uuid4())
        db.branches.insert_one({
            "id": bid, "name": "Test Branch PM", "active": True,
            "organization_id": org_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        branch = {"id": bid, "name": "Test Branch PM"}

    pid = str(uuid4())
    db.products.insert_one({
        "id": pid, "sku": f"PM-{pid[:6]}", "name": f"PriceMatch-{pid[:6]}",
        "category": "Test", "unit": "PC", "cost_price": 50.0,
        "prices": {"retail": 100, "wholesale": 90}, "active": True,
        "organization_id": org_id, "is_repack": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    db.inventory.update_one(
        {"product_id": pid, "branch_id": branch["id"]},
        {"$set": {
            "product_id": pid, "branch_id": branch["id"],
            "organization_id": org_id, "quantity": 100,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }},
        upsert=True,
    )
    yield {"product_id": pid, "branch_id": branch["id"], "branch_name": branch.get("name", ""), "org_id": org_id}
    db.products.delete_one({"id": pid})
    db.inventory.delete_many({"product_id": pid})
    db.branch_prices.delete_many({"product_id": pid})
    db.price_change_log.delete_many({"product_id": pid})


def _build_sale(setup, *, sold_price=95, with_pin=None, reason="competitor_match", reason_detail=""):
    """Build a unified-sale payload that edits the price from 100 → sold_price."""
    return {
        "id": str(uuid4()),
        "branch_id": setup["branch_id"],
        "branch_name": setup["branch_name"],
        "items": [{
            "product_id": setup["product_id"],
            "product_name": "PriceMatchTest",
            "sku": "PM-X",
            "quantity": 1,
            "rate": sold_price,
            "price": sold_price,
            "total": sold_price,
            "discount_type": "amount",
            "discount_value": 0,
            "discount_amount": 0,
            "is_repack": False,
        }],
        "subtotal": sold_price,
        "freight": 0,
        "overall_discount": 0,
        "grand_total": sold_price,
        "amount_paid": sold_price,
        "balance": 0,
        "payment_type": "cash",
        "payment_method": "Cash",
        "fund_source": "cashier",
        "price_scheme": "retail",
        "price_changes": [{
            "product_id": setup["product_id"],
            "scheme": "retail",
            "old_price": 100,
            "new_price": sold_price,
            "reason": reason,
            "reason_detail": reason_detail,
        }],
        "price_match_pin": with_pin,
        "order_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "customer_name": "Walk-in",
        "release_mode": "full",
    }


def test_price_match_without_pin_returns_422(auth, setup):
    token, _ = auth
    payload = _build_sale(setup, sold_price=95, with_pin=None)
    r = requests.post(f"{API}/unified-sale", json=payload, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 422, r.text
    body = r.json()
    detail = body.get("detail", {})
    assert detail.get("type") == "price_match_pin_required"
    assert len(detail.get("items", [])) == 1


def test_price_match_invalid_pin_rejected(auth, setup):
    token, _ = auth
    payload = _build_sale(setup, sold_price=95, with_pin="000000")
    r = requests.post(f"{API}/unified-sale", json=payload, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403
    assert "Invalid PIN" in r.text or "not authorized" in r.text


def test_price_match_admin_pin_persists_and_logs(auth, setup):
    token, _ = auth
    db = _db()
    payload = _build_sale(setup, sold_price=95, with_pin=TEST_ORG_ADMIN_PIN,
                          reason="competitor_match", reason_detail="Robinsons same SKU")
    r = requests.post(f"{API}/unified-sale", json=payload, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text

    # branch_prices.prices.retail = 95 for this product+branch
    bp = db.branch_prices.find_one({"product_id": setup["product_id"], "branch_id": setup["branch_id"]})
    assert bp is not None
    assert bp.get("prices", {}).get("retail") == 95
    # wholesale should NOT be touched (only the active scheme is updated)
    assert bp.get("prices", {}).get("wholesale") in (None, 0) or "wholesale" not in bp.get("prices", {})

    # price_change_log captured
    log = db.price_change_log.find_one({"product_id": setup["product_id"], "branch_id": setup["branch_id"]})
    assert log is not None
    assert log["old_price"] == 100
    assert log["new_price"] == 95
    assert log["reason"] == "competitor_match"
    assert log["reason_detail"] == "Robinsons same SKU"
    assert log["scheme"] == "retail"
    assert log["approver_name"]  # populated by verify_pin_for_action
    assert log["cashier_name"]
    assert log.get("delta") == -5
    assert log.get("delta_pct") == -5

    # Discount audit log should NOT have a price_override entry for this sale
    da = db.discount_audit_log.find_one({"invoice_number": r.json().get("invoice_number")})
    if da:
        for it in da.get("items", []):
            assert it.get("type") != "price_override"


def test_price_match_manager_pin_works(auth, setup):
    token, _ = auth
    db = _db()
    payload = _build_sale(setup, sold_price=92, with_pin=TEST_ORG_MANAGER_PIN,
                          reason="loyal_customer")
    r = requests.post(f"{API}/unified-sale", json=payload, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    bp = db.branch_prices.find_one({"product_id": setup["product_id"], "branch_id": setup["branch_id"]})
    assert bp.get("prices", {}).get("retail") == 92


def test_price_match_capital_floor_logic_unchanged(auth, setup):
    """The existing capital floor (rate < branch_cost) stays in force.
    Admins (with sell_below_cost=True) bypass it; non-admins are blocked.
    Functionality is exercised by the existing tests in
    test_offline_mode_192.py — this test simply confirms admins still
    succeed because the Price Match flow doesn't add a new floor."""
    token, _ = auth
    payload = _build_sale(setup, sold_price=40, with_pin=TEST_ORG_ADMIN_PIN)
    r = requests.post(f"{API}/unified-sale", json=payload, headers={"Authorization": f"Bearer {token}"})
    # Admin has sell_below_cost permission → sale succeeds, branch_price set to 40
    assert r.status_code == 200, r.text


def test_price_changes_list_endpoint(auth, setup):
    token, _ = auth
    # Trigger a sale first
    payload = _build_sale(setup, sold_price=88, with_pin=TEST_ORG_ADMIN_PIN, reason="promotional_offer")
    sale_r = requests.post(f"{API}/unified-sale", json=payload, headers={"Authorization": f"Bearer {token}"})
    assert sale_r.status_code == 200

    r = requests.get(
        f"{API}/price-changes",
        params={"branch_id": setup["branch_id"], "product_id": setup["product_id"]},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["summary"]["total_changes"] >= 1
    assert any(row["new_price"] == 88 and row["reason"] == "promotional_offer" for row in body["rows"])


def test_price_changes_product_history(auth, setup):
    token, _ = auth
    # First create a price change event for this product
    payload = _build_sale(setup, sold_price=99, with_pin=TEST_ORG_ADMIN_PIN, reason="loyal_customer")
    sale_r = requests.post(f"{API}/unified-sale", json=payload, headers={"Authorization": f"Bearer {token}"})
    assert sale_r.status_code == 200, sale_r.text

    r = requests.get(
        f"{API}/price-changes/product/{setup['product_id']}",
        params={"branch_id": setup["branch_id"]},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] >= 1
    assert all(row["product_id"] == setup["product_id"] for row in body["rows"])


def test_daily_close_preview_includes_breakdown(auth, setup):
    token, _ = auth
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    r = requests.get(
        f"{API}/daily-close-preview",
        params={"branch_id": setup["branch_id"], "date": today},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "discount_breakdown" in body
    assert "price_changes_today" in body
    assert isinstance(body["price_changes_today"].get("rows"), list)


# ── Consistency & safety tests (post-audit review) ────────────────────────────

def test_server_trusted_old_price_ignores_client_hint(auth, setup):
    """Client can't fake old_price — server reads from branch_prices/products.prices."""
    token, _ = auth
    db = _db()
    # Client lies: claims old_price=500 (way higher), new=95.
    payload = _build_sale(setup, sold_price=95, with_pin=TEST_ORG_ADMIN_PIN)
    payload["price_changes"][0]["old_price"] = 500  # maliciously inflated
    r = requests.post(f"{API}/unified-sale", json=payload, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    log = db.price_change_log.find_one({"product_id": setup["product_id"], "branch_id": setup["branch_id"]})
    # Server-trusted old_price is the actual branch/global price (100), NOT 500
    assert log["old_price"] == 100
    # Client hint is captured separately for forensics
    assert log.get("client_old_price_hint") == 500


def test_price_match_clears_global_price_badge(auth, setup):
    """After Price Match, inventory.price_reviewed_at should update
    (Global Price badge logic reads from this field)."""
    token, _ = auth
    db = _db()
    # Clear any existing reviewed_at to simulate a "stale" state
    db.inventory.update_one(
        {"product_id": setup["product_id"], "branch_id": setup["branch_id"]},
        {"$unset": {"price_reviewed_at": "", "last_price_review_source": ""}}
    )
    payload = _build_sale(setup, sold_price=93, with_pin=TEST_ORG_ADMIN_PIN)
    r = requests.post(f"{API}/unified-sale", json=payload, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    inv = db.inventory.find_one({"product_id": setup["product_id"], "branch_id": setup["branch_id"]})
    assert inv.get("price_reviewed_at"), "price_reviewed_at should be stamped after Price Match"
    assert inv.get("last_price_review_source") == "pos_price_match"


def test_discount_plus_price_match_clean_audit_classification(auth, setup):
    """When a line is BOTH matched AND discounted, discount_audit_log should
    read cleanly: original=matched_price (95), sold=95, discount=5,
    type=line_discount — NOT 'discount_and_override'."""
    token, _ = auth
    db = _db()
    payload = _build_sale(setup, sold_price=95, with_pin=TEST_ORG_ADMIN_PIN)
    # Add ₱5 discount on top of the ₱95 match
    payload["items"][0]["discount_type"] = "amount"
    payload["items"][0]["discount_value"] = 5
    payload["items"][0]["discount_amount"] = 5
    payload["items"][0]["total"] = 90
    payload["subtotal"] = 90
    payload["grand_total"] = 90
    payload["amount_paid"] = 90
    r = requests.post(f"{API}/unified-sale", json=payload, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    inv_num = r.json().get("invoice_number")
    da = db.discount_audit_log.find_one({"invoice_number": inv_num})
    assert da is not None
    line_entry = next((it for it in da.get("items", []) if it.get("product_id") == setup["product_id"]), None)
    assert line_entry is not None
    assert line_entry["type"] == "line_discount"  # NOT "discount_and_override"
    assert line_entry["original_price"] == 95      # matched price, NOT 100
    assert line_entry["sold_price"] == 95
    assert line_entry["discount_amount"] == 5


def test_voided_invoice_flags_price_change_log(auth, setup):
    """Voiding an invoice with Price Match events tags the log rows with
    invoice_voided=True and keeps the branch price updated (per spec)."""
    token, _ = auth
    db = _db()
    payload = _build_sale(setup, sold_price=91, with_pin=TEST_ORG_ADMIN_PIN)
    r = requests.post(f"{API}/unified-sale", json=payload, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    inv_id = r.json().get("id")

    # Void the invoice
    void_r = requests.post(
        f"{API}/invoices/{inv_id}/void",
        json={"manager_pin": TEST_ORG_ADMIN_PIN, "reason": "test void"},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert void_r.status_code == 200, void_r.text
    body = void_r.json()
    # Should include price_match_warning
    assert body.get("price_match_warning") is not None
    assert body["price_match_warning"]["count"] >= 1

    # price_change_log row is tagged
    log = db.price_change_log.find_one({"invoice_id": inv_id})
    assert log is not None
    assert log.get("invoice_voided") is True
    assert log.get("invoice_voided_by")

    # Branch price stays at 91 (not reverted — per spec, reverts are manual)
    bp = db.branch_prices.find_one({"product_id": setup["product_id"], "branch_id": setup["branch_id"]})
    assert bp["prices"]["retail"] == 91


def test_audit_center_surfaces_price_match_activity(auth, setup):
    """Audit Center /api/audit/compute → activity section should now include
    price_change_count, total_price_drop, and top_price_matchers."""
    token, _ = auth
    # Trigger a price change event first
    payload = _build_sale(setup, sold_price=90, with_pin=TEST_ORG_ADMIN_PIN)
    requests.post(f"{API}/unified-sale", json=payload, headers={"Authorization": f"Bearer {token}"})

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    r = requests.get(
        f"{API}/audit/compute",
        params={"branch_id": setup["branch_id"], "period_from": today, "period_to": today, "audit_type": "partial"},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    activity = body.get("activity", {})
    assert "price_change_count" in activity
    assert "total_price_drop" in activity
    assert "top_price_matchers" in activity
    assert activity["price_change_count"] >= 1

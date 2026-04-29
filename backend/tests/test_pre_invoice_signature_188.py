"""
Iteration 188 — Pre-invoice signature attach for credit/partial sales.

Verifies:
  - /unified-sale accepts `signature` field and stores it on the invoice
  - signature_session_id (top-level OR inside `signature`) back-links
    the signature_session document to the new invoice id
"""
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
def sale_setup(auth):
    db = _db()
    _, user = auth
    org_id = user.get("organization_id", "")
    branch = db.branches.find_one({"organization_id": org_id, "active": True}, {"_id": 0, "id": 1})

    pid = str(uuid4())
    db.products.insert_one({
        "id": pid, "sku": f"SIG-{pid[:6]}", "name": f"SigTest-{pid[:6]}",
        "category": "Test", "unit": "PC", "cost_price": 50.0,
        "prices": {"retail": 100}, "active": True,
        "product_type": "stockable", "organization_id": org_id, "is_repack": False,
    })
    db.inventory.insert_one({
        "id": str(uuid4()), "product_id": pid, "branch_id": branch["id"],
        "quantity": 10, "organization_id": org_id,
    })

    cust_id = str(uuid4())
    db.customers.insert_one({
        "id": cust_id, "name": f"SigCust-{cust_id[:6]}", "phone": "9171234567",
        "active": True, "organization_id": org_id,
    })

    yield {"product_id": pid, "branch_id": branch["id"], "customer_id": cust_id, "org_id": org_id}

    db.products.delete_one({"id": pid})
    db.inventory.delete_many({"product_id": pid})
    db.customers.delete_one({"id": cust_id})


def test_unified_sale_attaches_signature_fields(auth, sale_setup):
    """Posting a credit sale with a `signature` block stores all fields on the invoice."""
    token, _ = auth
    db = _db()
    sig_session_id = str(uuid4())
    # Pre-create the signature session so the back-link can find it
    db.signature_sessions.insert_one({
        "id": sig_session_id,
        "token": "tok-" + sig_session_id[:6],
        "linked_record_type": "draft",
        "linked_record_id": "",
        "status": "signed",
        "credit_context": {},
        "organization_id": sale_setup["org_id"],
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

    payload = {
        "branch_id": sale_setup["branch_id"],
        "customer_id": sale_setup["customer_id"],
        "customer_name": "SigCust Test",
        "items": [{
            "product_id": sale_setup["product_id"],
            "product_name": "SigTest", "quantity": 1, "rate": 100, "price": 100,
        }],
        "subtotal": 100, "freight": 0, "overall_discount": 0,
        "grand_total": 100, "amount_paid": 0, "balance": 100,
        "payment_type": "credit", "payment_method": "Credit",
        "fund_source": "cashier",
        "terms": "30 Days", "terms_days": 30,
        "order_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "mode": "quick", "release_mode": "full",
        # Signature block (new pre-invoice flow)
        "signature": {
            "url": "https://example.com/sig.png",
            "signed_at": "2026-04-29T10:00:00+00:00",
            "verification_token": "ABC12345",
            "session_id": sig_session_id,
        },
    }
    r = requests.post(
        f"{API}/unified-sale", json=payload,
        headers={"Authorization": f"Bearer {token}"}, timeout=20,
    )
    assert r.status_code == 200, r.text
    inv = r.json()
    inv_id = inv["id"]

    # Verify signature fields stored on invoice
    saved = db.invoices.find_one({"id": inv_id}, {"_id": 0})
    assert saved.get("signature_url") == "https://example.com/sig.png"
    assert saved.get("signature_signed_at") == "2026-04-29T10:00:00+00:00"
    assert saved.get("signature_verification_token") == "ABC12345"
    assert saved.get("signature_session_id") == sig_session_id

    # Verify signature_session was back-linked to invoice
    sess = db.signature_sessions.find_one({"id": sig_session_id}, {"_id": 0})
    assert sess["linked_record_type"] == "invoice"
    assert sess["linked_record_id"] == inv_id

    # Cleanup
    db.invoices.delete_one({"id": inv_id})
    db.signature_sessions.delete_one({"id": sig_session_id})
    db.movements.delete_many({"product_id": sale_setup["product_id"]})


def test_unified_sale_works_without_signature(auth, sale_setup):
    """Cash sale without signature data still works (no regression)."""
    token, _ = auth
    db = _db()
    payload = {
        "branch_id": sale_setup["branch_id"],
        "customer_name": "Walk-in",
        "items": [{
            "product_id": sale_setup["product_id"],
            "product_name": "SigTest", "quantity": 1, "rate": 100, "price": 100,
        }],
        "subtotal": 100, "freight": 0, "overall_discount": 0,
        "grand_total": 100, "amount_paid": 100, "balance": 0,
        "payment_type": "cash", "payment_method": "Cash",
        "fund_source": "cashier",
        "order_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "mode": "quick", "release_mode": "full",
    }
    r = requests.post(
        f"{API}/unified-sale", json=payload,
        headers={"Authorization": f"Bearer {token}"}, timeout=20,
    )
    assert r.status_code == 200, r.text
    inv = r.json()
    saved = db.invoices.find_one({"id": inv["id"]}, {"_id": 0})
    # signature fields should NOT be set
    assert saved.get("signature_url") is None or "signature_url" not in saved
    db.invoices.delete_one({"id": inv["id"]})
    db.movements.delete_many({"product_id": sale_setup["product_id"]})

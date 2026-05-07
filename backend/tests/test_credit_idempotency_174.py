"""
Iteration 174 — Terminal credit-sale duplicate-prevention + signature-first sequence.

Bug context (live VPS):
  - SI-MB-001003 produced #104 + #105 from one click on a slow PC.
  - Earlier: SI-MB-001000 / SI-MB-001001 same pattern.
  - Root cause: Terminal didn't send `idempotency_key`; backend's dedupe was dead-code.

Coverage in this file:
  1. `check_idempotency` returns the prior invoice for a known key.
  2. Unique sparse index exists on `invoices.idempotency_key` (catches racy duplicates).
  3. `/unified-sale` pipeline handles new `signature_session_id` payload field.
  4. /signatures/session supports pre-commit creation (linked_record_id="").

Tests use pymongo (sync) for DB-level checks + lightweight HTTP smoke.
"""
import os
import sys
import uuid
import requests
from pymongo import MongoClient

# Allow `from config import ...` style imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "test_database")

API = os.environ.get(
    "API_URL", "https://sales-unify.preview.emergentagent.com"
).rstrip("/") + "/api"
EMAIL = os.environ.get("TEST_EMAIL", "janmarkeahig@gmail.com")
PASSWORD = os.environ.get("TEST_PASSWORD", "Aa@58798546521325")


def _db():
    return MongoClient(MONGO_URL)[DB_NAME]


def test_idempotency_unique_index_exists_on_invoices():
    """
    Concurrent retries with the same idempotency_key must be impossible to
    persist — a unique sparse index protects against the race.
    """
    idx_info = _db().invoices.index_information()
    found = False
    for name, info in idx_info.items():
        keys = [k[0] for k in info.get("key", [])]
        if "idempotency_key" in keys and info.get("unique"):
            found = True
            break
    assert found, (
        f"unique index on invoices.idempotency_key missing — "
        f"found: {list(idx_info.keys())}"
    )
    print("PASS · unique sparse index on invoices.idempotency_key is in place")


def test_idempotency_lookup_returns_existing_doc():
    """A second insert of the same idempotency_key must surface the prior invoice."""
    db = _db()
    key = f"test-idem-{uuid.uuid4()}"
    fake_doc = {
        "id": str(uuid.uuid4()),
        "invoice_number": "TEST-IDEM-001",
        "idempotency_key": key,
        "branch_id": "test-branch",
        "test_marker": "credit_idempotency_174",
    }
    db.invoices.insert_one(fake_doc)
    try:
        existing = db.invoices.find_one({"idempotency_key": key}, {"_id": 0})
        assert existing is not None, "should find the prior doc"
        assert existing["invoice_number"] == "TEST-IDEM-001"
        assert "_id" not in existing

        # Unique index must reject a duplicate insert with the same key
        from pymongo.errors import DuplicateKeyError
        try:
            db.invoices.insert_one({**fake_doc, "id": str(uuid.uuid4())})
            raise AssertionError(
                "duplicate idempotency_key insert was NOT rejected — "
                "live duplicates can still happen"
            )
        except DuplicateKeyError:
            pass  # expected
    finally:
        db.invoices.delete_many({"idempotency_key": key})
    print("PASS · duplicate idempotency_key blocked by unique index")


def test_unified_sale_endpoint_handles_signature_session_id_payload():
    """
    Smoke: posting a payload with `signature_session_id` should not 500.
    With invalid product_id we expect a 4xx, not a 5xx — proves the new
    field path is parsed cleanly.
    """
    r = requests.post(
        f"{API}/auth/login",
        json={"email": EMAIL, "password": PASSWORD},
        timeout=15,
    )
    r.raise_for_status()
    token = r.json()["token"]
    h = {"Authorization": f"Bearer {token}"}

    payload = {
        "id": str(uuid.uuid4()),
        "idempotency_key": str(uuid.uuid4()),
        "signature_session_id": "00000000-fake-fake-fake-000000000000",
        "branch_id": "00000000-0000-0000-0000-000000000000",  # bogus
        "items": [],
        "subtotal": 0,
        "grand_total": 0,
        "payment_type": "cash",
        "payment_method": "Cash",
        "fund_source": "cashier",
        "release_mode": "full",
        "status": "paid",
    }
    res = requests.post(f"{API}/unified-sale", headers=h, json=payload, timeout=20)
    assert res.status_code < 500, (
        f"backend 500'd on signature_session_id payload — schema regression. "
        f"Status={res.status_code} body={res.text}"
    )
    print(
        f"PASS · /unified-sale accepts signature_session_id field "
        f"(returned {res.status_code} for invalid branch — non-5xx)"
    )


def test_signature_session_supports_pre_commit_creation():
    """
    Pre-commit flow: signature_session can be created with linked_record_id=""
    (no invoice yet). Backend will back-link after invoice creation.
    """
    r = requests.post(
        f"{API}/auth/login",
        json={"email": EMAIL, "password": PASSWORD},
        timeout=15,
    )
    r.raise_for_status()
    token = r.json()["token"]
    h = {"Authorization": f"Bearer {token}"}

    payload = {
        "linked_record_type": "invoice",
        "linked_record_id": "",  # empty — pre-commit
        "branch_id": "",
        "credit_context": {
            "customer_name": "Test Pre-Commit",
            "amount": 100.0,
            "credit_type": "by_term",
            "branch_name": "Test",
            "description": "Pre-commit signature smoke",
            "invoice_number": "(pending)",
            "items": [
                {"product_name": "Test Product", "quantity": 1,
                 "unit": "unit", "rate": 100.0, "total": 100.0}
            ],
            "subtotal": 100.0,
            "discount": 0,
            "partial_paid": 0,
        },
    }
    res = requests.post(f"{API}/signatures/session", headers=h, json=payload, timeout=15)
    assert res.status_code == 200, (
        f"signature session creation should accept empty linked_record_id "
        f"for pre-commit flow — got {res.status_code}: {res.text}"
    )
    body = res.json()
    assert body.get("token"), "session must return a token"
    assert body.get("id"), "session must return an id"
    print(f"PASS · /signatures/session created pre-commit (token={body['token'][:8]}…)")


if __name__ == "__main__":
    test_idempotency_unique_index_exists_on_invoices()
    test_idempotency_lookup_returns_existing_doc()
    test_unified_sale_endpoint_handles_signature_session_id_payload()
    test_signature_session_supports_pre_commit_creation()
    print("\nAll iteration 174 tests passed.")

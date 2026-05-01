"""
Regression test for: QR scan returns "Document not found" / "Invoice not found"
even though the doc_code and document both exist.

Root cause: `/api/doc/view/:code` and `/api/qr-actions/:code/*` are public
(no JWT), so the multi-tenant `db` proxy fails-closed and returns 0 docs.

Fix: After resolving the doc_code, set tenant context from doc_ref.org_id
(with a legacy fallback that reads the document via _raw_db).

This test:
  1. Picks any invoice that has a doc_code in the DB.
  2. Hits the public GET /api/doc/view/:code endpoint without auth.
  3. Asserts the response is 200 and matches the invoice number.

Also covers the qr-actions/:code/context alias path.
"""
import os
import asyncio
import pytest
import requests
from motor.motor_asyncio import AsyncIOMotorClient


BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL") or "http://localhost:8001").rstrip("/")
MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]


def _pick_invoice_with_doc_code():
    """Find any invoice with a non-empty doc_code in the live DB (sync)."""
    async def _go():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        # Prefer doc_codes entry that has org_id stamped (post-fix flow).
        async for dc in db.doc_codes.find(
            {"doc_type": "invoice", "org_id": {"$nin": [None, ""]}}, {"_id": 0}
        ):
            inv = await db.invoices.find_one(
                {"id": dc["doc_id"]}, {"_id": 0, "invoice_number": 1, "organization_id": 1}
            )
            if inv and inv.get("organization_id") == dc.get("org_id"):
                return dc["code"], inv["invoice_number"]
        return None, None

    return asyncio.get_event_loop().run_until_complete(_go())


def test_public_doc_view_resolves_invoice_without_auth():
    code, expected_number = _pick_invoice_with_doc_code()
    if not code:
        pytest.skip("No invoice doc_code with matching organization_id available")

    r = requests.get(f"{BASE_URL}/api/doc/view/{code}", timeout=10)
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    body = r.json()
    assert body.get("doc_type") == "invoice"
    assert body.get("number") == expected_number, (
        f"Expected invoice number {expected_number}, got {body.get('number')}"
    )


def test_public_qr_context_resolves_without_auth():
    code, expected_number = _pick_invoice_with_doc_code()
    if not code:
        pytest.skip("No invoice doc_code available")

    r = requests.get(f"{BASE_URL}/api/qr-actions/{code}/context", timeout=10)
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    body = r.json()
    assert body.get("doc_type") == "invoice"
    assert body.get("number") == expected_number


def test_public_doc_view_unknown_code_returns_404():
    r = requests.get(f"{BASE_URL}/api/doc/view/ZZZZZZZZ", timeout=10)
    assert r.status_code == 404

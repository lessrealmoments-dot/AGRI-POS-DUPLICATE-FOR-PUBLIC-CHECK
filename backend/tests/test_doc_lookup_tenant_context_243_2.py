"""
Iter 243.2 — Regression: /api/doc/lookup returned 500 for ANY PIN entry on
the QR doc viewer page, blocking every QR action (process return, receive
payment) at the very first step.

Reported by jovelyn 2026-05-05:
  > "I tried putting the pin to process return and payment it told me to
  >  contact the admin or there was an error."
  > "An unexpected server error occurred. Please try again or contact support"

Root cause
----------
`/api/doc/lookup` is a PUBLIC endpoint (no JWT). It used to call
`_resolve_pin(pin)` and `log_failed_qr_pin_attempt(...)` BEFORE setting the
tenant context. The tenant proxy fails closed when no org context is set:

  • `db.users.find(...)` returned zero rows → no PIN could ever match
  • `db.pin_attempt_log.insert_one(...)` → RuntimeError ("refusing to insert
    without organization_id") → 500 Unhandled Exception → generic 500 message.

Fix
---
Resolve the doc_code FIRST (which derives org_id and calls
set_org_context), then run lockout check + PIN verification + log writes.
This matches what `/api/qr-actions/{code}/*` already does.

Test
----
Hits `/api/doc/lookup` directly with valid + invalid PINs and asserts:
  1. Invalid PIN → 403 (NOT 500). Pre-fix this returned 500.
  2. The response body is structured (HTTPException), not the generic
     "An unexpected server error occurred" wrapper.
"""
import os
from uuid import uuid4
from datetime import datetime, timezone

import pytest
import requests
from pymongo import MongoClient

from tests._org_test_helpers import API, MONGO_URL, DB_NAME, ensure_org_admin_token

_db = MongoClient(MONGO_URL)[DB_NAME]


@pytest.fixture(scope="module")
def doc_code():
    """Find any existing invoice with a doc_code in the org we'll test."""
    token, user = ensure_org_admin_token()
    org_id = user.get("organization_id")
    inv = _db.invoices.find_one(
        {"organization_id": org_id, "doc_code": {"$exists": True, "$ne": ""}},
        {"_id": 0, "doc_code": 1},
    )
    if not inv:
        # Synthesize one so the test is self-contained
        from secrets import choice
        import string
        alphabet = string.ascii_uppercase + string.digits
        code = "".join(choice(alphabet) for _ in range(8))
        inv_id = f"iter243-2-{uuid4().hex[:8]}"
        _db.invoices.insert_one({
            "id": inv_id,
            "organization_id": org_id,
            "doc_code": code,
            "invoice_number": f"TST-{code}",
            "branch_id": "test-branch",
            "order_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "grand_total": 1, "amount_paid": 0, "balance": 1,
            "status": "open", "items": [],
            "payments": [],
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        _db.doc_codes.insert_one({
            "id": f"dc-{uuid4().hex[:8]}",
            "code": code,
            "doc_type": "invoice",
            "doc_id": inv_id,
            "org_id": org_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        yield code
        _db.invoices.delete_one({"id": inv_id})
        _db.doc_codes.delete_one({"code": code})
    else:
        yield inv["doc_code"]


def test_invalid_pin_returns_403_not_500(doc_code):
    """The exact bug: any PIN attempt returned 500 'unexpected server error'.

    After fix: invalid PINs return a clean 403 with a structured message.
    The 500 path was a RuntimeError from the tenant proxy because the
    failed-attempt log row was missing organization_id (no tenant context
    was set before the log insert).
    """
    res = requests.post(
        f"{API}/doc/lookup",
        json={"code": doc_code, "pin": "000000"},  # deliberately wrong
        timeout=15,
    )
    assert res.status_code != 500, (
        f"Expected non-500 (the original bug). Got 500: {res.text}\n"
        f"This means /api/doc/lookup is still hitting the unhandled-exception "
        f"path. Check that _resolve_doc_code_with_context() runs before "
        f"_resolve_pin() in lookup_document()."
    )
    # Should be 403 (invalid PIN) or 429 (locked out from prior failures)
    assert res.status_code in (403, 429), f"Got {res.status_code}: {res.text}"
    # And it must NOT be the generic global-handler message
    assert "unexpected server error" not in res.text.lower(), (
        f"Response body suggests the global exception handler fired. "
        f"That handler returns 500 — but if the status code is something else "
        f"the message is leaking from elsewhere. Body: {res.text}"
    )
    print(f"✅ Invalid PIN on {doc_code} → status {res.status_code} (clean error)")


def test_missing_pin_still_400_not_500(doc_code):
    """Empty PIN must still produce a clean 400, not a 500."""
    res = requests.post(
        f"{API}/doc/lookup",
        json={"code": doc_code, "pin": ""},
        timeout=15,
    )
    assert res.status_code == 400, f"Expected 400, got {res.status_code}: {res.text}"
    print(f"✅ Empty PIN → 400 (validation error)")


def test_missing_code_returns_400():
    """Empty doc code → 400, not 500."""
    res = requests.post(
        f"{API}/doc/lookup",
        json={"code": "", "pin": "521325"},
        timeout=15,
    )
    assert res.status_code == 400, f"Expected 400, got {res.status_code}"
    print(f"✅ Empty code → 400")


def test_unknown_code_returns_404(doc_code):
    """An unknown but well-formed code must return 404, not 500."""
    res = requests.post(
        f"{API}/doc/lookup",
        json={"code": "ZZZZZZZZ", "pin": "521325"},
        timeout=15,
    )
    assert res.status_code == 404, f"Expected 404, got {res.status_code}: {res.text}"
    print(f"✅ Unknown code → 404")

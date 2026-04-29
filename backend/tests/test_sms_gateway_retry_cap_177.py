"""
Iteration 177 — SMS gateway retry-spiral and accidental short-send fixes.

Live RCA (agri-books.com):
  Gateway log showed `SMS SENT failed (code 124)` repeating endlessly with
  retry_count climbing to 393 on a single message ("Sup", 3 chars). Two
  separate code bugs amplified the carrier-side problem:

  Bug A: `/queue/{id}/mark-failed` set status='failed' and bumped retry_count
         on EVERY call but never moved the doc to a terminal state. The Android
         gateway kept seeing the same item and re-trying it via local retry
         logic.
  Bug B: `/queue/pending` did not exclude messages with high retry_count, so
         even messages that should have been abandoned were polled back.
  Bug C: `/sms/send` accepted single-character messages — accidental 'A'/'J'
         sends piled up in the queue and burned carrier rate limits.

Fixes:
  - MAX_GATEWAY_RETRIES = 3. After that, status='failed_permanent' and
    /queue/pending excludes the doc.
  - mark-sent / mark-failed are idempotent on terminal states (no double-bump
    when the gateway PATCH times out and is retried).
  - /sms/send rejects messages < 5 chars.
  - New /queue/clear-stuck admin endpoint to drain a poisoned queue.
"""
import os
import sys
import uuid
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

API = os.environ.get(
    "API_URL", "https://offline-robustness.preview.emergentagent.com"
).rstrip("/") + "/api"
EMAIL = os.environ.get("TEST_EMAIL", "janmarkeahig@gmail.com")
PASSWORD = os.environ.get("TEST_PASSWORD", "Aa@58798546521325")


def _login():
    """Use a real org admin (super admin can no longer touch tenant data after
    the privacy fix in config.TenantCollection)."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _org_test_helpers import ensure_org_admin_token
    token, _ = ensure_org_admin_token()
    return token


def _h(t):
    return {"Authorization": f"Bearer {t}"}


def _seed_pending(token, message="Test minimum length OK", phone="09000000000"):
    """Insert a pending SMS directly via the manual-send endpoint."""
    r = requests.post(
        f"{API}/sms/send",
        headers=_h(token),
        json={"phone": phone, "message": message, "branch_id": ""},
        timeout=15,
    )
    r.raise_for_status()
    body = r.json()
    return body.get("id") or body.get("queued") and body


def test_send_rejects_short_messages():
    """Accidental single-char messages from the cashier are blocked."""
    token = _login()
    for short in ["A", "J", "Sup", "Hi"]:
        r = requests.post(
            f"{API}/sms/send",
            headers=_h(token),
            json={"phone": "09000000000", "message": short},
            timeout=15,
        )
        assert r.status_code == 400, (
            f"expected 400 for short msg '{short}', got {r.status_code}: {r.text}"
        )
        assert "too short" in r.text.lower() or "5 characters" in r.text.lower()
    print("PASS · /sms/send rejects messages with <5 chars")


def test_mark_failed_caps_retries_at_three():
    """
    Send 4 mark-failed PATCHes for the same SMS and expect status to flip to
    'failed_permanent' after the 3rd, then stay idempotent on the 4th.
    """
    token = _login()
    sid = _seed_pending(token, message=f"Retry-cap regression {uuid.uuid4()}")
    assert sid, "could not seed test SMS"

    for attempt in range(1, 4):
        r = requests.patch(
            f"{API}/sms/queue/{sid}/mark-failed",
            headers=_h(token),
            json={"error": f"attempt {attempt} (code 124)"},
            timeout=15,
        )
        assert r.status_code == 200, f"attempt {attempt}: {r.text}"
        body = r.json()
        if attempt < 3:
            assert body["status"] == "failed", (
                f"attempt {attempt}: expected 'failed', got {body}"
            )
        else:
            assert body["status"] == "failed_permanent", (
                f"attempt 3 should flip to failed_permanent, got {body}"
            )
        assert body["retry_count"] == attempt, body

    # 4th call → idempotent, no retry_count bump
    r = requests.patch(
        f"{API}/sms/queue/{sid}/mark-failed",
        headers=_h(token),
        json={"error": "should not bump"},
        timeout=15,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("idempotent") is True, (
        f"4th call should be idempotent, body={body}"
    )
    print(f"PASS · mark-failed caps at 3, then idempotent. id={sid[:8]}")


def test_pending_endpoint_excludes_capped_messages():
    """
    A message that already hit retry_count=3 must NOT be returned by
    /queue/pending even if its status field somehow says 'pending'.
    Defensive against any code path that resets status without retry_count.
    """
    token = _login()
    sid = _seed_pending(token, message=f"Cap-exclude regression {uuid.uuid4()}")

    # Force retry_count to 3 via 3 mark-failed calls
    for _ in range(3):
        requests.patch(
            f"{API}/sms/queue/{sid}/mark-failed",
            headers=_h(token), json={"error": "test"}, timeout=15,
        )

    # Now list pending — capped message must not appear
    r = requests.get(f"{API}/sms/queue/pending?limit=200", headers=_h(token), timeout=15)
    assert r.status_code == 200
    items = r.json() if isinstance(r.json(), list) else r.json().get("items", [])
    ids = [i.get("id") for i in items]
    assert sid not in ids, (
        f"capped message {sid} still in pending list — gateway would re-pull it"
    )
    print(f"PASS · /queue/pending excludes capped (retry>=3) messages")


def test_mark_sent_is_idempotent():
    """
    Gateway timeouts cause duplicate mark-sent PATCHes. Second call must NOT
    error or change anything (otherwise we'd bounce the queue UI).
    """
    token = _login()
    sid = _seed_pending(token, message=f"Idempotent mark-sent {uuid.uuid4()}")

    r1 = requests.patch(f"{API}/sms/queue/{sid}/mark-sent", headers=_h(token), timeout=15)
    assert r1.status_code == 200, r1.text
    assert r1.json()["status"] == "sent"

    # Second call — must succeed idempotently
    r2 = requests.patch(f"{API}/sms/queue/{sid}/mark-sent", headers=_h(token), timeout=15)
    assert r2.status_code == 200, f"second mark-sent should not 404: {r2.text}"
    assert r2.json().get("idempotent") is True
    print("PASS · mark-sent is idempotent (timeouts won't break the queue)")


def test_clear_stuck_endpoint_drains_failed_queue():
    """
    Admin endpoint must move failed/failed_permanent messages to 'skipped'
    so the gateway forgets them and stops the carrier-spam spiral.
    """
    token = _login()
    sid = _seed_pending(token, message=f"Clear-stuck test {uuid.uuid4()}")
    # Push to failed
    requests.patch(
        f"{API}/sms/queue/{sid}/mark-failed", headers=_h(token),
        json={"error": "stuck"}, timeout=15,
    )

    r = requests.post(f"{API}/sms/queue/clear-stuck", headers=_h(token), timeout=15)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("cleared", 0) >= 1, (
        f"clear-stuck should have moved at least 1 doc, body={body}"
    )
    print(f"PASS · /queue/clear-stuck drained {body['cleared']} stuck messages")


if __name__ == "__main__":
    test_send_rejects_short_messages()
    test_mark_failed_caps_retries_at_three()
    test_pending_endpoint_excludes_capped_messages()
    test_mark_sent_is_idempotent()
    test_clear_stuck_endpoint_drains_failed_queue()
    print("\nIteration 177 SMS gateway retry-cap tests passed.")

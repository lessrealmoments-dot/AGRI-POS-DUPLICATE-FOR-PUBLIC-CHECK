"""
br_terminal_guard — pin the terminal-only enforcement on sensitive
write endpoints.

After the Feb 2026 lock-down, four endpoints reject any request that
doesn't carry a valid paired-terminal session:
  • POST /api/qr-actions/{code}/receive_payment
  • POST /api/invoices/{id}/correct-incomplete-stock
  • POST /api/returns
  • POST /api/invoices/{id}/send-pickup-sms

These tests exercise `utils.terminal_guard.require_terminal_session`
directly with synthetic ASGI requests, so they're fast and don't depend
on FastAPI's TestClient or the live server.
"""
import json
import pytest
from fastapi import HTTPException

from config import _raw_db
from utils.terminal_guard import require_terminal_session


class _FakeRequest:
    """Minimal stand-in for starlette.Request — supports `.headers.get()`
    and `.body()` exactly as `require_terminal_session` needs them."""
    def __init__(self, headers=None, body_bytes=b""):
        self.headers = _Headers(headers or {})
        self._body = body_bytes

    async def body(self):
        return self._body


class _Headers(dict):
    def get(self, key, default=None):
        # Headers are case-insensitive in HTTP; mirror that.
        for k, v in self.items():
            if k.lower() == key.lower():
                return v
        return default


async def _seed_terminal_session(*, terminal_id, device_id=""):
    await _raw_db.terminal_sessions.update_one(
        {"terminal_id": terminal_id},
        {"$set": {
            "terminal_id": terminal_id,
            "device_id":   device_id,
            "status":      "active",
        }},
        upsert=True,
    )


# ═════════════════════════════════════════════════════════════════════
# Test 1 — No credentials at all → 403 with clear message.
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_tg_1_no_credentials_rejected(record_result):
    req = _FakeRequest()
    raised = False
    detail = None
    try:
        await require_terminal_session(req)
    except HTTPException as e:
        raised = True
        detail = e.detail
        status = e.status_code

    record_result(
        scenario="br_terminal_guard.1_no_creds",
        step="empty_request_rejected",
        expected={"raised": True, "status": 403,
                  "msg_mentions_app": True},
        actual={"raised": raised, "status": status if raised else None,
                "msg_mentions_app": "AgriBooks" in (detail or "")},
    )
    assert raised
    assert status == 403
    assert "AgriBooks" in detail


# ═════════════════════════════════════════════════════════════════════
# Test 2 — Header credentials with active session → returns dict.
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_tg_2_header_credentials_pass(record_result):
    await _seed_terminal_session(terminal_id="term-tg-2", device_id="dev-tg-2")
    req = _FakeRequest(headers={
        "X-Terminal-Id": "term-tg-2",
        "X-Device-Id":   "dev-tg-2",
    })

    result = await require_terminal_session(req)

    record_result(
        scenario="br_terminal_guard.2_headers",
        step="header_creds_accepted",
        expected={"terminal_id": "term-tg-2", "device_id": "dev-tg-2"},
        actual=result,
    )
    assert result["terminal_id"] == "term-tg-2"
    assert result["device_id"] == "dev-tg-2"


# ═════════════════════════════════════════════════════════════════════
# Test 3 — Body credentials with active session → returns dict.
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_tg_3_body_credentials_pass(record_result):
    await _seed_terminal_session(terminal_id="term-tg-3", device_id="")
    body = json.dumps({"terminal_id": "term-tg-3", "device_id": "", "foo": "bar"}).encode()
    req = _FakeRequest(body_bytes=body)

    result = await require_terminal_session(req)

    record_result(
        scenario="br_terminal_guard.3_body",
        step="body_creds_accepted",
        expected={"terminal_id": "term-tg-3"},
        actual={"terminal_id": result["terminal_id"]},
    )
    assert result["terminal_id"] == "term-tg-3"


# ═════════════════════════════════════════════════════════════════════
# Test 4 — Invalid / non-existent terminal_id → 403.
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_tg_4_unknown_terminal_id_rejected(record_result):
    req = _FakeRequest(headers={"X-Terminal-Id": "term-DOES-NOT-EXIST"})

    raised = False
    try:
        await require_terminal_session(req)
    except HTTPException as e:
        raised = True
        status = e.status_code

    record_result(
        scenario="br_terminal_guard.4_unknown_terminal",
        step="unknown_id_403",
        expected={"raised": True, "status": 403},
        actual={"raised": raised, "status": status if raised else None},
    )
    assert raised
    assert status == 403


# ═════════════════════════════════════════════════════════════════════
# Test 5 — Device-binding mismatch → 403 (terminal exists but caller
# came from a different device).
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_tg_5_device_mismatch_rejected(record_result):
    await _seed_terminal_session(terminal_id="term-tg-5", device_id="phone-A")
    req = _FakeRequest(headers={
        "X-Terminal-Id": "term-tg-5",
        "X-Device-Id":   "phone-B",  # different device
    })

    raised = False
    try:
        await require_terminal_session(req)
    except HTTPException as e:
        raised = True
        status = e.status_code

    record_result(
        scenario="br_terminal_guard.5_device_mismatch",
        step="different_device_403",
        expected={"raised": True, "status": 403},
        actual={"raised": raised, "status": status if raised else None},
    )
    assert raised
    assert status == 403


# ═════════════════════════════════════════════════════════════════════
# Test 6 — Header takes precedence over body when both are present.
# (Documents the resolution order so future agents don't change it
# accidentally and break clients that rely on headers.)
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_tg_6_header_takes_precedence_over_body(record_result):
    await _seed_terminal_session(terminal_id="term-tg-6a", device_id="")
    await _seed_terminal_session(terminal_id="term-tg-6b", device_id="")

    body = json.dumps({"terminal_id": "term-tg-6b"}).encode()
    req = _FakeRequest(
        headers={"X-Terminal-Id": "term-tg-6a"},
        body_bytes=body,
    )

    result = await require_terminal_session(req)

    record_result(
        scenario="br_terminal_guard.6_header_precedence",
        step="header_wins_over_body",
        expected={"terminal_id": "term-tg-6a"},
        actual={"terminal_id": result["terminal_id"]},
    )
    assert result["terminal_id"] == "term-tg-6a"

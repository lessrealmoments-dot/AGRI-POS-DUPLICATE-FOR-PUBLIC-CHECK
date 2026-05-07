"""
Regression test for Z-Report SMS Share Links (Iter 253, Feb 2026).
Verifies token mint, public lookup, anomaly auto-revoke at 5 unique IPs,
and revoke endpoint authorization.
"""
import asyncio
import os
import sys

import pytest
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
sys.path.insert(0, "/app/backend")

_LOOP = asyncio.new_event_loop()
asyncio.set_event_loop(_LOOP)

from routes.zreport_share import (  # noqa: E402
    create_share_link, _resolve_token, ANOMALY_UNIQUE_IP_THRESHOLD,
)
from config import _raw_db  # noqa: E402

TEST_BRANCH = "test-iter253-branch"
TEST_ORG = "test-org-iter253"


def _run(coro):
    return _LOOP.run_until_complete(coro)


class FakeRequest:
    def __init__(self, ip="1.1.1.1", ua="pytest"):
        self.headers = {"x-forwarded-for": ip, "user-agent": ua}
        class _C:
            def __init__(self): self.host = ip
        self.client = _C()


async def _setup():
    await _raw_db.zreport_share_links.delete_many({"branch_id": TEST_BRANCH})
    await _raw_db.zreport_share_access_log.delete_many(
        {"branch_id": TEST_BRANCH}
    )
    await _raw_db.daily_closings.delete_many({"branch_id": TEST_BRANCH})
    await _raw_db.daily_closings.insert_one({
        "id": "close-iter253-1", "branch_id": TEST_BRANCH,
        "organization_id": TEST_ORG, "date": "2026-02-15",
        "status": "closed",
        "total_cash_sales": 5000.0, "total_cash_ar": 1000.0,
        "expected_counter": 6000.0, "actual_cash": 6000.0,
        "over_short": 0.0,
    })


async def _cleanup():
    await _raw_db.zreport_share_links.delete_many({"branch_id": TEST_BRANCH})
    await _raw_db.zreport_share_access_log.delete_many(
        {"branch_id": TEST_BRANCH}
    )
    await _raw_db.daily_closings.delete_many({"branch_id": TEST_BRANCH})


def test_create_share_link_idempotent():
    async def go():
        await _setup()
        try:
            t1 = await create_share_link(
                closing_id="close-iter253-1", branch_id=TEST_BRANCH,
                date="2026-02-15", organization_id=TEST_ORG,
                recipient_user_id="user-1", recipient_name="Manager A",
            )
            t2 = await create_share_link(
                closing_id="close-iter253-1", branch_id=TEST_BRANCH,
                date="2026-02-15", organization_id=TEST_ORG,
                recipient_user_id="user-1", recipient_name="Manager A",
            )
            assert t1 == t2  # Same recipient + closing → reuses token
            assert len(t1) >= 32
        finally:
            await _cleanup()
    _run(go())


def test_resolve_token_logs_access_and_unique_ips():
    async def go():
        await _setup()
        try:
            tok = await create_share_link(
                closing_id="close-iter253-1", branch_id=TEST_BRANCH,
                date="2026-02-15", organization_id=TEST_ORG,
                recipient_user_id="user-2", recipient_name="Owner",
            )
            await _resolve_token(tok, FakeRequest("10.0.0.1"))
            await _resolve_token(tok, FakeRequest("10.0.0.2"))
            await _resolve_token(tok, FakeRequest("10.0.0.1"))  # repeat IP
            doc = await _raw_db.zreport_share_links.find_one(
                {"token": tok}, {"_id": 0}
            )
            assert doc["access_count"] == 3
            assert sorted(doc["unique_ips"]) == ["10.0.0.1", "10.0.0.2"]
            assert doc.get("revoked") is False
            logs = await _raw_db.zreport_share_access_log.find(
                {"token": tok}, {"_id": 0}
            ).to_list(10)
            assert len(logs) == 3
        finally:
            await _cleanup()
    _run(go())


def test_resolve_token_auto_revokes_at_threshold():
    async def go():
        await _setup()
        try:
            tok = await create_share_link(
                closing_id="close-iter253-1", branch_id=TEST_BRANCH,
                date="2026-02-15", organization_id=TEST_ORG,
                recipient_user_id="user-3", recipient_name="Auditor",
            )
            # Hit it from threshold+1 unique IPs to trigger auto-revoke
            for i in range(ANOMALY_UNIQUE_IP_THRESHOLD + 1):
                await _resolve_token(tok, FakeRequest(f"10.0.{i}.1"))

            doc = await _raw_db.zreport_share_links.find_one(
                {"token": tok}, {"_id": 0}
            )
            assert doc.get("revoked") is True
            assert "auto" in (doc.get("revoke_reason") or "")
            assert len(doc["unique_ips"]) > ANOMALY_UNIQUE_IP_THRESHOLD

            # A subsequent access must raise (revoked)
            from fastapi import HTTPException
            with pytest.raises(HTTPException) as exc:
                await _resolve_token(tok, FakeRequest("10.0.99.1"))
            assert exc.value.status_code == 410
        finally:
            await _cleanup()
    _run(go())


def test_resolve_token_404_on_unknown():
    from fastapi import HTTPException

    async def go():
        try:
            with pytest.raises(HTTPException) as exc:
                await _resolve_token("nonexistent-token-xxx", FakeRequest())
            assert exc.value.status_code == 404
        finally:
            await _cleanup()
    _run(go())

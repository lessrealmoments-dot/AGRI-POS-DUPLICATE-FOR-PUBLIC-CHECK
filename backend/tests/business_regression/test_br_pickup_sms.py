"""
br_pickup_sms — rate-limit + abuse-prevention tests for the manual
"Send Pickup SMS" terminal action.

Policy under test (mirrors `routes/pickup_sms.py`):
  • Maximum 3 sends per invoice (lifetime).
  • 5-minute cooldown between consecutive sends.
  • Manual trigger only (`trigger="manual"` on the queued SMS row).
  • Requires customer_id + at least one phone on file; 400 otherwise.
"""
import pytest
from datetime import datetime, timezone, timedelta

from config import _raw_db
from routes.pickup_sms import send_pickup_sms, get_pickup_sms_status, MAX_SENDS


async def _seed_invoice(*, org_id, branch_id, customer_id, tag=""):
    import uuid
    iid = f"inv-pkp-{tag or uuid.uuid4().hex[:6]}"
    doc = {
        "id": iid, "organization_id": org_id, "branch_id": branch_id,
        "invoice_number": f"INV-{iid[-6:]}",
        "customer_id": customer_id, "customer_name": "Customer X",
        "order_date": "2026-01-01", "invoice_date": "2026-01-01",
        "created_at": "2026-01-01T08:00:00+00:00",
        "payment_type": "cash", "payment_method": "Cash",
        "grand_total": 1000.0, "amount_paid": 1000.0, "balance": 0.0,
        "status": "paid", "items": [], "subtotal": 1000.0,
        "release_mode": "partial", "stock_release_status": "not_released",
    }
    await _raw_db.invoices.insert_one(doc)
    return iid


# ═════════════════════════════════════════════════════════════════════
# Test 1 — Happy path: first send succeeds, status reports remaining=2.
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_pickup_1_first_send_success(tenant, record_result):
    org_id = tenant["org_id"]
    branch_id = tenant["branches"]["main"]
    cust_id = tenant["customers"]["credit"]
    await _raw_db.customers.update_one(
        {"id": cust_id}, {"$set": {"phone": "09171234567"}},
    )
    inv_id = await _seed_invoice(org_id=org_id, branch_id=branch_id, customer_id=cust_id, tag="ok")

    res = await send_pickup_sms(inv_id, user=tenant["users"]["owner"])

    record_result(
        scenario="br_pickup.1_first_send",
        step="counter_increments_remaining_decrements",
        expected={"sent_count": 1, "remaining": MAX_SENDS - 1, "phones_len": 1},
        actual={"sent_count": res["sent_count"], "remaining": res["remaining"],
                "phones_len": len(res["phones"])},
    )
    assert res["sent_count"] == 1
    assert res["remaining"] == MAX_SENDS - 1
    assert res["phones"] == ["09171234567"]

    # Invoice doc carries the counters now.
    inv = await _raw_db.invoices.find_one({"id": inv_id}, {"_id": 0})
    assert inv["pickup_sms_count"] == 1
    assert inv["pickup_sms_last_sent_at"]
    assert len(inv["pickup_sms_history"]) == 1


# ═════════════════════════════════════════════════════════════════════
# Test 2 — Cooldown enforced: second send within 5 min returns 429.
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_pickup_2_cooldown_blocks_immediate_resend(tenant, record_result):
    from fastapi import HTTPException
    org_id = tenant["org_id"]
    branch_id = tenant["branches"]["main"]
    cust_id = tenant["customers"]["credit"]
    await _raw_db.customers.update_one(
        {"id": cust_id}, {"$set": {"phone": "09181111111"}},
    )
    inv_id = await _seed_invoice(org_id=org_id, branch_id=branch_id, customer_id=cust_id, tag="cd")

    await send_pickup_sms(inv_id, user=tenant["users"]["owner"])

    raised = False
    detail = None
    try:
        await send_pickup_sms(inv_id, user=tenant["users"]["owner"])
    except HTTPException as e:
        raised = True
        detail = e.detail
        status_code = e.status_code

    record_result(
        scenario="br_pickup.2_cooldown",
        step="second_send_within_5min_blocked",
        expected={"raised": True, "status": 429,
                  "remaining": MAX_SENDS - 1, "has_retry_after": True},
        actual={"raised": raised, "status": status_code if raised else None,
                "remaining": detail.get("remaining") if detail else None,
                "has_retry_after": bool(detail and detail.get("retry_after_seconds"))},
    )
    assert raised
    assert status_code == 429
    assert detail["remaining"] == MAX_SENDS - 1
    assert detail["retry_after_seconds"] > 0


# ═════════════════════════════════════════════════════════════════════
# Test 3 — Lifetime cap: after 3 sends, the 4th returns 429 with
# retry_after_seconds = None (never recoverable).
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_pickup_3_lifetime_cap_3_sends(tenant, record_result):
    from fastapi import HTTPException
    org_id = tenant["org_id"]
    branch_id = tenant["branches"]["main"]
    cust_id = tenant["customers"]["credit"]
    await _raw_db.customers.update_one(
        {"id": cust_id}, {"$set": {"phone": "09182222222"}},
    )
    inv_id = await _seed_invoice(org_id=org_id, branch_id=branch_id, customer_id=cust_id, tag="cap")

    # Force-cap by writing the counter directly + back-dating last_sent
    # past the cooldown so cooldown isn't the rejection cause.
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    await _raw_db.invoices.update_one(
        {"id": inv_id},
        {"$set": {"pickup_sms_count": MAX_SENDS,
                  "pickup_sms_last_sent_at": past}},
    )

    raised = False
    detail = None
    try:
        await send_pickup_sms(inv_id, user=tenant["users"]["owner"])
    except HTTPException as e:
        raised = True
        detail = e.detail
        status_code = e.status_code

    record_result(
        scenario="br_pickup.3_lifetime_cap",
        step="fourth_send_blocked_permanently",
        expected={"raised": True, "status": 429,
                  "remaining": 0, "retry_after_None": True},
        actual={"raised": raised, "status": status_code if raised else None,
                "remaining": detail.get("remaining") if detail else None,
                "retry_after_None": (detail.get("retry_after_seconds") is None) if detail else None},
    )
    assert raised
    assert status_code == 429
    assert detail["remaining"] == 0
    assert detail["retry_after_seconds"] is None


# ═════════════════════════════════════════════════════════════════════
# Test 4 — Cooldown expires: stale last_sent_at allows a second send.
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_pickup_4_cooldown_expires_allows_resend(tenant, record_result):
    org_id = tenant["org_id"]
    branch_id = tenant["branches"]["main"]
    cust_id = tenant["customers"]["credit"]
    await _raw_db.customers.update_one(
        {"id": cust_id}, {"$set": {"phone": "09183333333"}},
    )
    inv_id = await _seed_invoice(org_id=org_id, branch_id=branch_id, customer_id=cust_id, tag="expire")

    await send_pickup_sms(inv_id, user=tenant["users"]["owner"])
    # Back-date last_sent to simulate 6 minutes ago.
    past = (datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat()
    await _raw_db.invoices.update_one(
        {"id": inv_id},
        {"$set": {"pickup_sms_last_sent_at": past}},
    )

    res = await send_pickup_sms(inv_id, user=tenant["users"]["owner"])

    record_result(
        scenario="br_pickup.4_cooldown_expires",
        step="resend_succeeds_after_5min",
        expected={"sent_count": 2, "remaining": MAX_SENDS - 2},
        actual={"sent_count": res["sent_count"], "remaining": res["remaining"]},
    )
    assert res["sent_count"] == 2
    assert res["remaining"] == MAX_SENDS - 2


# ═════════════════════════════════════════════════════════════════════
# Test 5 — Validation: no customer attached → 400 (not 429, distinct
# UX: "this invoice can't ever pickup-SMS" vs "wait/limit").
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_pickup_5_no_customer_rejects(tenant, record_result):
    from fastapi import HTTPException
    org_id = tenant["org_id"]
    branch_id = tenant["branches"]["main"]
    inv_id = await _seed_invoice(org_id=org_id, branch_id=branch_id, customer_id="", tag="walkin")

    raised = False
    try:
        await send_pickup_sms(inv_id, user=tenant["users"]["owner"])
    except HTTPException as e:
        raised = True
        status_code = e.status_code

    record_result(
        scenario="br_pickup.5_no_customer",
        step="walkin_invoice_400",
        expected={"raised": True, "status": 400},
        actual={"raised": raised, "status": status_code if raised else None},
    )
    assert raised
    assert status_code == 400


# ═════════════════════════════════════════════════════════════════════
# Test 6 — Status endpoint hydrates UI: reports remaining/cooldown
# without sending anything (used on terminal page-load).
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_pickup_6_status_endpoint_hydrates(tenant, record_result):
    org_id = tenant["org_id"]
    branch_id = tenant["branches"]["main"]
    cust_id = tenant["customers"]["credit"]
    await _raw_db.customers.update_one(
        {"id": cust_id}, {"$set": {"phone": "09184444444"}},
    )
    inv_id = await _seed_invoice(org_id=org_id, branch_id=branch_id, customer_id=cust_id, tag="hydrate")

    await send_pickup_sms(inv_id, user=tenant["users"]["owner"])
    status = await get_pickup_sms_status(inv_id, user=tenant["users"]["owner"])

    record_result(
        scenario="br_pickup.6_status_hydrate",
        step="after_one_send_status_reports_state",
        expected={"sent_count": 1, "remaining": MAX_SENDS - 1,
                  "max_sends": MAX_SENDS, "has_customer": True,
                  "retry_after_positive": True},
        actual={"sent_count": status["sent_count"],
                "remaining": status["remaining"],
                "max_sends": status["max_sends"],
                "has_customer": status["has_customer"],
                "retry_after_positive": status["retry_after_seconds"] > 0},
    )
    assert status["sent_count"] == 1
    assert status["remaining"] == MAX_SENDS - 1
    assert status["max_sends"] == MAX_SENDS
    assert status["has_customer"] is True
    assert status["retry_after_seconds"] > 0

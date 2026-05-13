"""br_historical_credit_sms_term — Locks the new SMS + future-term
support on `historical_credit.create_historical_credit`.

Phase: Historical Credit "Send SMS at encode + allow future term".

Locks:
  * Payload without `due_date` keeps the legacy behaviour
    (invoice.due_date == transaction_date).
  * Payload with `due_date` is honoured verbatim on the invoice.
  * One `sms_queue` row with template_key="opening_balance_notice" is
    enqueued per encode. The dedup_key prevents duplicate sends.
  * Cadence is weekly-only (no daily bombing): _validate_payload puts
    due_date in the future when operator specifies a term, so the
    existing _daily_sms_reminders scheduler (every 7 days post-due)
    is the ONLY post-due driver.
"""
import os
import re
import sys
from datetime import datetime, timezone, timedelta

import pyotp
import pytest
import pytest_asyncio

BACKEND = os.path.join(os.path.dirname(__file__), "..", "..")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from config import _raw_db, set_org_context                      # noqa: E402
from tests.phase2b._fixtures import (                            # noqa: E402
    _uid, fake_user, seed_customer, seed_product, seed_totp_admin,
)
from routes.historical_credit import (                           # noqa: E402
    create_historical_credit, _validate_payload,
)


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _days_ago(n: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=n)).strftime("%Y-%m-%d")


def _fresh_totp(secret: str) -> str:
    return pyotp.TOTP(secret).now()


def _payload(*, branch_id, customer_id, product_id,
             transaction_date, grand_total=1500, due_date=None,
             approval_code=None):
    p = {
        "branch_id": branch_id,
        "customer_id": customer_id,
        "transaction_date": transaction_date,
        "items": [{
            "product_id": product_id, "quantity": 1,
            "rate": grand_total, "total": grand_total,
        }],
        "subtotal": grand_total,
        "grand_total": grand_total,
        "reason": "BR test — opening-balance SMS + future term lock. "
                  "Customer signed off on the carry-forward 2026-02-13.",
        "proof_url": "https://example.com/notebook.jpg",
        "notebook_reference": "BR Ledger / Row 1",
        "allow_inventory_deduction": False,
    }
    if due_date is not None:
        p["due_date"] = due_date
    if approval_code is not None:
        p["approval_code"] = approval_code
    return p


@pytest_asyncio.fixture(scope="module")
async def hc_admin(tenant):
    """Seed a TOTP-enabled admin so we can call the commit endpoint."""
    org_id = tenant["org_id"]
    main = tenant["branches"]["main"]
    admin_id, secret = await seed_totp_admin(org_id, main)
    set_org_context(org_id)
    yield {
        "user": fake_user(org_id, admin_id, branch_id=main, role="admin"),
        "secret": secret,
    }


# ═════════════════════════════════════════════════════════════════════
# Test 1 — _validate_payload accepts an explicit due_date
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_br_hc_sms_1_validate_payload_accepts_due_date(record_result):
    today = _today()
    future = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%d")
    p = {
        "customer_id": "x", "branch_id": "b",
        "transaction_date": _days_ago(60),
        "due_date": future,
        "grand_total": 100.0,
        "items": [{"product_id": "p", "quantity": 1, "rate": 100, "total": 100}],
        "reason": "BR validate-payload due_date lock — at least 10 chars.",
    }
    out = _validate_payload(p, today)
    record_result(
        scenario="br_hc_sms.1_validate_accepts_due_date",
        step="explicit_due_date_returned",
        expected={"due_date": future},
        actual={"due_date": out.get("due_date")},
        evidence={"transaction_date": p["transaction_date"]},
    )
    assert out["due_date"] == future


# ═════════════════════════════════════════════════════════════════════
# Test 2 — _validate_payload defaults due_date to transaction_date
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_br_hc_sms_2_validate_payload_defaults_due_to_txn(record_result):
    today = _today()
    txn = _days_ago(45)
    p = {
        "customer_id": "x", "branch_id": "b",
        "transaction_date": txn,
        # NOTE: due_date omitted on purpose.
        "grand_total": 100.0,
        "items": [{"product_id": "p", "quantity": 1, "rate": 100, "total": 100}],
        "reason": "BR validate-payload default due — at least 10 chars.",
    }
    out = _validate_payload(p, today)
    record_result(
        scenario="br_hc_sms.2_validate_default_due",
        step="due_falls_back_to_txn_date",
        expected={"due_date": txn},
        actual={"due_date": out.get("due_date")},
        evidence={"transaction_date": txn},
    )
    assert out["due_date"] == txn


# ═════════════════════════════════════════════════════════════════════
# Test 3 — Encoding queues exactly ONE opening_balance_notice SMS
# (best-effort hook with dedup_key:`opening_balance:{invoice_id}`)
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_br_hc_sms_3_encode_queues_opening_balance_sms(
    tenant, hc_admin, record_result
):
    org_id = tenant["org_id"]
    main = tenant["branches"]["main"]
    set_org_context(org_id)
    cust_id = await seed_customer(org_id, main, balance=0)
    await _raw_db.customers.update_one(
        {"id": cust_id}, {"$set": {"phone": "+639171234001"}}
    )
    prod_id = await seed_product(org_id, main)
    queue_before = await _raw_db.sms_queue.count_documents({
        "template_key": "opening_balance_notice",
        "customer_id": cust_id,
    })
    res = await create_historical_credit(
        _payload(
            branch_id=main, customer_id=cust_id, product_id=prod_id,
            transaction_date=_days_ago(60),
            due_date=(datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%d"),
            grand_total=2500.0,
            approval_code=_fresh_totp(hc_admin["secret"]),
        ),
        user=hc_admin["user"],
    )
    inv_id = res["invoice"]["id"]
    queue_after = await _raw_db.sms_queue.find({
        "template_key": "opening_balance_notice",
        "customer_id": cust_id,
        "trigger_ref": inv_id,
    }).to_list(10)
    record_result(
        scenario="br_hc_sms.3_encode_queues_sms",
        step="exactly_one_opening_balance_row_per_encode",
        expected={"queue_delta": 1, "dedup_key_matches": True},
        actual={"queue_delta": len(queue_after) - queue_before,
                "dedup_key_matches":
                    bool(queue_after) and queue_after[0].get("dedup_key", "")
                                            .startswith("opening_balance:")},
        evidence={"invoice_id": inv_id,
                  "dedup_key": queue_after[0].get("dedup_key") if queue_after else None,
                  "phone": queue_after[0].get("phone") if queue_after else None},
    )
    assert len(queue_after) - queue_before == 1
    assert queue_after[0]["dedup_key"] == f"opening_balance:{inv_id}"
    assert queue_after[0]["customer_name"]
    # Surface that the encode reported the SMS as queued.
    assert res.get("sms_queued") is True


# ═════════════════════════════════════════════════════════════════════
# Test 4 — Invoice carries the operator's due_date (future term)
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_br_hc_sms_4_invoice_has_operator_due_date(
    tenant, hc_admin, record_result
):
    org_id = tenant["org_id"]
    main = tenant["branches"]["main"]
    set_org_context(org_id)
    cust_id = await seed_customer(org_id, main, balance=0)
    await _raw_db.customers.update_one(
        {"id": cust_id}, {"$set": {"phone": "+639171234002"}}
    )
    prod_id = await seed_product(org_id, main)
    future = (datetime.now(timezone.utc) + timedelta(days=45)).strftime("%Y-%m-%d")
    res = await create_historical_credit(
        _payload(
            branch_id=main, customer_id=cust_id, product_id=prod_id,
            transaction_date=_days_ago(120),
            due_date=future,
            grand_total=4321.0,
            approval_code=_fresh_totp(hc_admin["secret"]),
        ),
        user=hc_admin["user"],
    )
    inv = res["invoice"]
    record_result(
        scenario="br_hc_sms.4_invoice_due_date_honoured",
        step="invoice_due_date_matches_operator_term",
        expected={"due_date": future, "txn_date_neq_due": True},
        actual={"due_date": inv.get("due_date"),
                "txn_date_neq_due":
                    inv.get("due_date") != inv.get("order_date")},
        evidence={"invoice_id": inv["id"],
                  "txn_date": inv.get("order_date")},
    )
    assert inv["due_date"] == future
    assert inv["due_date"] != inv["order_date"]


# ═════════════════════════════════════════════════════════════════════
# Test 5 — Customer with no phone: encode still succeeds, no SMS
# enqueued (best-effort hook should never block the encode).
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_br_hc_sms_5_no_phone_no_sms_no_crash(
    tenant, hc_admin, record_result
):
    org_id = tenant["org_id"]
    main = tenant["branches"]["main"]
    set_org_context(org_id)
    cust_id = await seed_customer(org_id, main, balance=0)
    # Explicitly leave `phone` unset on the customer doc so the SMS hook
    # has nothing to dispatch.
    prod_id = await seed_product(org_id, main)
    res = await create_historical_credit(
        _payload(
            branch_id=main, customer_id=cust_id, product_id=prod_id,
            transaction_date=_days_ago(60),
            grand_total=999.0,
            approval_code=_fresh_totp(hc_admin["secret"]),
        ),
        user=hc_admin["user"],
    )
    inv_id = res["invoice"]["id"]
    queue_after = await _raw_db.sms_queue.count_documents({
        "template_key": "opening_balance_notice",
        "trigger_ref": inv_id,
    })
    record_result(
        scenario="br_hc_sms.5_no_phone_no_sms",
        step="encode_succeeds_no_sms_when_phone_blank",
        expected={"encode_ok": True, "sms_enqueued": 0},
        actual={"encode_ok": bool(res.get("ok")),
                "sms_enqueued": queue_after},
        evidence={"invoice_id": inv_id},
    )
    assert res.get("ok") is True
    assert queue_after == 0
    assert res.get("sms_queued") is False

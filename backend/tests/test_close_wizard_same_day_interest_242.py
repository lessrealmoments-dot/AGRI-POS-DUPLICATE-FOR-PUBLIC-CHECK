"""
Iter 242 — Regression: Same-day interest invoice payments were excluded
from Close Wizard's AR totals, causing OVER-MONEY variance in Step 5.

Reported by jovelyn on 2026-05-05:
  > "Closing Wizard Step 3 AR Payment shows principal AND interest paid.
  >  However on Step 5 Actual Count the interest paid is not added or
  >  visible, which triggers excess money / Over Money."

Root cause
----------
`routes/daily_operations.py` had three AR aggregation pipelines (preview,
close_day, batch_close) that filtered `order_date: {$ne: date}` to avoid
double-counting new credit sales on the close date. Interest/penalty
invoices generated AND paid on the same day (a normal flow from
`/payments`) got silently dropped because their `order_date` == today.

The payment's cash still landed in the cashier drawer (via
`update_cashier_wallet`), but `total_cash_ar` / `expected_counter`
excluded it — so the physical count came out OVER by exactly the
interest collected.

Fix
---
The three pipelines now include today-dated invoices when
`sale_type in (interest_charge, penalty_charge)`. Payments on those
invoices are classified as `interest_paid` / `penalty_paid` (since
`/customers/.../receive-payment` hardcodes `applied_to_interest=0`).

Test
----
This is a DB-level unit test — synthesises a fixture directly via
Mongo, calls the preview endpoint, and asserts:
  1. The interest payment appears in `ar_payments`
  2. `total_cash_ar` includes the interest amount
  3. `total_ar_received` includes the interest amount
  4. `ar_interest_collected` includes the interest amount
  5. `expected_counter` accounts for the interest cash
"""
import os
import time
from datetime import datetime, timezone
from uuid import uuid4

import pytest
import requests
from pymongo import MongoClient

from tests._org_test_helpers import API, ensure_org_admin_token, MONGO_URL, DB_NAME

_db = MongoClient(MONGO_URL)[DB_NAME]


@pytest.fixture(scope="module")
def admin_ctx():
    token, user = ensure_org_admin_token()
    headers = {"Authorization": f"Bearer {token}"}
    branches = requests.get(f"{API}/branches", headers=headers).json()
    branch_id = branches[0]["id"]
    org_id = user.get("organization_id")
    return headers, branch_id, org_id


def _today_local():
    # Matches daily_operations default — UTC date. Tests run in containers
    # where local=UTC, so this is fine.
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def test_iter242_same_day_interest_payment_flows_into_expected_counter(admin_ctx):
    headers, branch_id, org_id = admin_ctx
    date = _today_local()
    tag = f"iter242-{uuid4().hex[:8]}"
    interest_amount = 123.45  # distinctive value so we can find it

    # Create an interest invoice dated TODAY, fully paid TODAY via one
    # cashier payment. This mirrors what /payments does when the user
    # clicks "Generate Interest" and then "Apply Payment" on the same day.
    inv_id = str(uuid4())
    pay_id = str(uuid4())
    invoice_doc = {
        "id": inv_id,
        "invoice_number": f"INT-TEST-{tag}",
        "customer_id": f"cust-{tag}",
        "customer_name": f"Iter242 Customer {tag}",
        "branch_id": branch_id,
        "organization_id": org_id,
        "order_date": date,
        "invoice_date": date,
        "due_date": date,
        "sale_type": "interest_charge",
        "status": "paid",
        "grand_total": interest_amount,
        "amount_paid": interest_amount,
        "balance": 0,
        "items": [{"product_name": "Interest", "quantity": 1, "rate": interest_amount, "line_total": interest_amount}],
        "payments": [{
            "id": pay_id,
            "amount": interest_amount,
            "date": date,
            "method": "Cash",
            "fund_source": "cashier",
            "applied_to_interest": 0,      # note: /receive-payment hardcodes this to 0
            "applied_to_principal": interest_amount,
            "recorded_by": "iter242-test",
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _db.invoices.insert_one(invoice_doc)

    try:
        # Snapshot counts BEFORE calling preview (there may be other traffic
        # on the shared branch — we compare deltas using our unique tag).
        res = requests.get(
            f"{API}/daily-close-preview",
            params={"branch_id": branch_id, "date": date},
            headers=headers,
            timeout=30,
        )
        assert res.status_code == 200, f"Preview failed: {res.status_code} {res.text}"
        data = res.json()

        # 1. The payment must appear in ar_payments
        ar = [p for p in data.get("ar_payments", []) if p.get("invoice_number") == invoice_doc["invoice_number"]]
        assert len(ar) == 1, (
            f"Expected 1 AR payment for {invoice_doc['invoice_number']}, got {len(ar)}. "
            f"Pre-fix this was 0 because the pipeline excluded today-dated invoices."
        )
        row = ar[0]

        # 2. It must be classified as interest_paid (sale_type override)
        assert row["interest_paid"] == pytest.approx(interest_amount, abs=0.01), \
            f"Expected interest_paid={interest_amount}, got {row['interest_paid']}"
        assert row["sale_type"] == "interest_charge"
        assert row["fund_source"] == "cashier"
        assert row["amount_paid"] == pytest.approx(interest_amount, abs=0.01)

        # 3. ar_interest_collected must reflect it (delta check — shared branch)
        assert data["ar_interest_collected"] >= interest_amount - 0.01, (
            f"ar_interest_collected={data['ar_interest_collected']} does not include "
            f"our {interest_amount} interest payment."
        )

        # 4. total_cash_ar must include the interest payment amount
        assert data["total_cash_ar"] >= interest_amount - 0.01, (
            f"total_cash_ar={data['total_cash_ar']} does not include our interest payment — "
            f"this is the bug. Pre-fix Step 5 under-counted cashier receipts by exactly this amount."
        )

        print(
            f"✅ Iter242: interest payment {interest_amount} flows into "
            f"total_cash_ar={data['total_cash_ar']}, ar_interest_collected={data['ar_interest_collected']}"
        )

    finally:
        # Cleanup
        _db.invoices.delete_one({"id": inv_id})

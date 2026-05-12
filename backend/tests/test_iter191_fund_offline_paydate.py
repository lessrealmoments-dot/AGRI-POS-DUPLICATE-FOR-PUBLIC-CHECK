"""
Iteration 191 backend tests:
  1. POST /accounting/fund-transfers — `date` field accepted, stored, and validated
     against daily_closings (closed-day rejection).
  2. /accounting/daily-close-preview and /daily-close-preview/batch — fund_transfers_today
     includes the new `date` field per row.
  3. POST /sales/sync — offline `invoice_number` (SI-MN-OFF-000001) preserved.
  4. POST /accounting/customers/{customer_id}/edit-payment-date — happy path + all guards.
"""
import os
import sys
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
import requests
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(__file__))
from _org_test_helpers import ensure_org_admin_token, TEST_ORG_MANAGER_PIN  # noqa: E402

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "test_database")
BASE = os.environ.get(
    "REACT_APP_BACKEND_URL",
    "https://regression-suite-p5.preview.emergentagent.com",
).rstrip("/")
API = BASE + "/api"

OWNER_PIN = "913712"
MAIN_WAREHOUSE_BRANCH = "772808fd-d534-404e-a347-48fcc4e2fc8a"


@pytest.fixture(scope="module")
def auth():
    token, user = ensure_org_admin_token()
    return {
        "token": token,
        "user": user,
        "headers": {"Authorization": f"Bearer {token}"},
        "org_id": user.get("organization_id"),
    }


@pytest.fixture(scope="module")
def db():
    return MongoClient(MONGO_URL)[DB_NAME]


def _today():
    return datetime.utcnow().strftime("%Y-%m-%d")


def _date_offset(days):
    return (datetime.utcnow() + timedelta(days=days)).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# 1. Fund transfers — date field
# ---------------------------------------------------------------------------
class TestFundTransferDate:
    def test_capital_add_with_date_field(self, auth, db):
        target_date = _today()
        body = {
            "branch_id": MAIN_WAREHOUSE_BRANCH,
            "transfer_type": "capital_add",
            "amount": 12.34,
            "note": "TEST iter191 capital with date",
            "owner_pin": OWNER_PIN,
            "target_wallet": "cashier",
            "date": target_date,
        }
        r = requests.post(f"{API}/fund-transfers", json=body, headers=auth["headers"], timeout=30)
        assert r.status_code == 200, f"{r.status_code} {r.text}"
        data = r.json()
        assert "transfer" in data
        assert data["transfer"]["date"] == target_date
        ft_id = data["transfer"]["id"]

        # Verify present in /fund-transfers list
        lst = requests.get(
            f"{API}/fund-transfers?branch_id={MAIN_WAREHOUSE_BRANCH}&limit=50",
            headers=auth["headers"],
            timeout=15,
        )
        assert lst.status_code == 200, lst.text
        rows = lst.json() if isinstance(lst.json(), list) else lst.json().get("transfers", [])
        match = next((r for r in rows if r.get("id") == ft_id), None)
        assert match is not None, "Created fund transfer not in list"
        assert match.get("date") == target_date

        # Cleanup
        db.fund_transfers.delete_one({"id": ft_id})

    def test_capital_add_on_closed_day_rejected(self, auth, db):
        closed_date = _date_offset(-30)
        # Seed daily_closings closed record
        seed_id = f"TEST-iter191-{uuid4().hex[:8]}"
        db.daily_closings.insert_one({
            "id": seed_id,
            "branch_id": MAIN_WAREHOUSE_BRANCH,
            "date": closed_date,
            "status": "closed",
            "organization_id": auth["org_id"],
        })
        try:
            r = requests.post(
                f"{API}/fund-transfers",
                json={
                    "branch_id": MAIN_WAREHOUSE_BRANCH,
                    "transfer_type": "capital_add",
                    "amount": 5,
                    "note": "TEST iter191 closed-day reject",
                    "owner_pin": OWNER_PIN,
                    "target_wallet": "cashier",
                    "date": closed_date,
                },
                headers=auth["headers"],
                timeout=15,
            )
            assert r.status_code == 403, f"expected 403 got {r.status_code} {r.text}"
            assert "already closed" in r.json().get("detail", "").lower(), r.text
        finally:
            db.daily_closings.delete_one({"id": seed_id})


# ---------------------------------------------------------------------------
# 2. Daily-close-preview — fund_transfers_today with date
# ---------------------------------------------------------------------------
class TestDailyClosePreviewFundTransfers:
    def test_preview_includes_fund_transfer_with_date(self, auth, db):
        target_date = _today()
        # Seed a capital_add via API so it lives in DB legitimately
        body = {
            "branch_id": MAIN_WAREHOUSE_BRANCH,
            "transfer_type": "capital_add",
            "amount": 7.77,
            "note": "TEST iter191 preview seed",
            "owner_pin": OWNER_PIN,
            "target_wallet": "cashier",
            "date": target_date,
        }
        cr = requests.post(f"{API}/fund-transfers", json=body, headers=auth["headers"], timeout=20)
        assert cr.status_code == 200, cr.text
        ft_id = cr.json()["transfer"]["id"]

        try:
            r = requests.get(
                f"{API}/daily-close-preview?branch_id={MAIN_WAREHOUSE_BRANCH}&date={target_date}",
                headers=auth["headers"],
                timeout=15,
            )
            assert r.status_code == 200, r.text
            j = r.json()
            assert "fund_transfers_today" in j, "fund_transfers_today key missing in preview"
            ft_list = j["fund_transfers_today"]
            assert isinstance(ft_list, list)
            mine = next((x for x in ft_list if x.get("note") == "TEST iter191 preview seed"), None)
            assert mine is not None, f"capital_add not in fund_transfers_today; got {ft_list}"
            assert mine.get("date") == target_date, f"date missing/wrong in preview row: {mine}"
        finally:
            db.fund_transfers.delete_one({"id": ft_id})

    def test_batch_preview_includes_fund_transfers_today(self, auth, db):
        d_to = _today()
        d_mid = _date_offset(-1)
        d_from = _date_offset(-2)
        dates_csv = f"{d_from},{d_mid},{d_to}"
        body = {
            "branch_id": MAIN_WAREHOUSE_BRANCH,
            "transfer_type": "capital_add",
            "amount": 3.14,
            "note": "TEST iter191 batch preview seed",
            "owner_pin": OWNER_PIN,
            "target_wallet": "cashier",
            "date": d_to,
        }
        cr = requests.post(f"{API}/fund-transfers", json=body, headers=auth["headers"], timeout=20)
        assert cr.status_code == 200, cr.text
        ft_id = cr.json()["transfer"]["id"]
        try:
            r = requests.get(
                f"{API}/daily-close-preview/batch?branch_id={MAIN_WAREHOUSE_BRANCH}&dates={dates_csv}",
                headers=auth["headers"],
                timeout=20,
            )
            assert r.status_code == 200, r.text
            j = r.json()
            assert "fund_transfers_today" in j, "fund_transfers_today missing in batch preview"
            assert isinstance(j["fund_transfers_today"], list)
            mine = next((x for x in j["fund_transfers_today"] if x.get("note") == "TEST iter191 batch preview seed"), None)
            assert mine is not None, f"Seeded capital_add not in batch fund_transfers_today: {j['fund_transfers_today']}"
            assert mine.get("date") == d_to
        finally:
            db.fund_transfers.delete_one({"id": ft_id})


# ---------------------------------------------------------------------------
# 3. /sales/sync preserves offline invoice_number
# ---------------------------------------------------------------------------
class TestOfflineSalesSyncPreservesInvoiceNumber:
    def test_offline_invoice_number_preserved(self, auth, db):
        sale_id = f"TEST-iter191-{uuid4().hex[:8]}"
        offline_inv = f"SI-MN-OFF-{uuid4().hex[:6].upper()}"
        payload = {
            "sales": [{
                "id": sale_id,
                "envelope_id": sale_id,
                "branch_id": MAIN_WAREHOUSE_BRANCH,
                "customer_name": "TEST Walk-in iter191",
                "invoice_number": offline_inv,
                "prefix": "SI",
                "items": [],
                "subtotal": 0,
                "amount_paid": 0,
                "balance": 0,
                "status": "paid",
                "payment_type": "cash",
                "payments": [],
                "date": _today(),
            }]
        }
        r = requests.post(f"{API}/sales/sync", json=payload, headers=auth["headers"], timeout=30)
        assert r.status_code == 200, r.text
        try:
            invoice = db.invoices.find_one({"id": sale_id}, {"_id": 0})
            assert invoice is not None, "invoice not created from sync"
            assert invoice.get("invoice_number") == offline_inv, (
                f"invoice_number renumbered! expected {offline_inv} got {invoice.get('invoice_number')}"
            )
        finally:
            db.invoices.delete_one({"id": sale_id})


# ---------------------------------------------------------------------------
# 4. edit-payment-date endpoint
# ---------------------------------------------------------------------------
@pytest.fixture(scope="class")
def seeded_invoice_with_payment(auth, db):
    """Find or seed an invoice with a non-voided payment in the auth org."""
    org_id = auth["org_id"]
    inv = db.invoices.find_one(
        {"organization_id": org_id, "payments": {"$exists": True, "$ne": []}},
        {"_id": 0},
    )
    if not inv:
        # Create a minimal invoice doc directly with one payment row
        inv_id = f"TEST-INV-{uuid4().hex[:8]}"
        cust_id = f"TEST-CUST-{uuid4().hex[:8]}"
        pay_id = f"TEST-PAY-{uuid4().hex[:8]}"
        seed = {
            "id": inv_id,
            "invoice_number": f"SI-MN-{uuid4().hex[:6].upper()}",
            "customer_id": cust_id,
            "customer_name": "TEST iter191 Customer",
            "branch_id": MAIN_WAREHOUSE_BRANCH,
            "organization_id": org_id,
            "items": [],
            "subtotal": 100, "grand_total": 100,
            "amount_paid": 100, "balance": 0,
            "status": "paid",
            "payments": [{
                "id": pay_id,
                "amount": 100,
                "date": _date_offset(-5),
                "method": "cash",
                "voided": False,
            }],
            "created_at": datetime.utcnow().isoformat(),
        }
        db.invoices.insert_one(seed)
        return {"created": True, "invoice_id": inv_id, "payment_id": pay_id, "customer_id": cust_id}

    # Use the first non-voided payment
    pay = next((p for p in inv["payments"] if not p.get("voided") and not p.get("is_discount")), None)
    if not pay:
        # Fallback: seed a minimal invoice
        inv_id = f"TEST-INV-{uuid4().hex[:8]}"
        cust_id = f"TEST-CUST-{uuid4().hex[:8]}"
        pay_id = f"TEST-PAY-{uuid4().hex[:8]}"
        seed = {
            "id": inv_id,
            "invoice_number": f"SI-MN-{uuid4().hex[:6].upper()}",
            "customer_id": cust_id,
            "customer_name": "TEST iter191 Customer",
            "branch_id": MAIN_WAREHOUSE_BRANCH,
            "organization_id": org_id,
            "items": [], "subtotal": 100, "grand_total": 100,
            "amount_paid": 100, "balance": 0, "status": "paid",
            "payments": [{"id": pay_id, "amount": 100, "date": _date_offset(-5),
                          "method": "cash", "voided": False}],
            "created_at": datetime.utcnow().isoformat(),
        }
        db.invoices.insert_one(seed)
        return {"created": True, "invoice_id": inv_id, "payment_id": pay_id, "customer_id": cust_id}
    return {
        "created": False,
        "invoice_id": inv["id"],
        "payment_id": pay["id"],
        "customer_id": inv.get("customer_id", ""),
        "original_date": pay.get("date", ""),
    }


class TestEditPaymentDate:
    def test_happy_path(self, auth, db, seeded_invoice_with_payment):
        ctx = seeded_invoice_with_payment
        original = db.invoices.find_one({"id": ctx["invoice_id"]}, {"_id": 0})
        orig_pay = next(p for p in original["payments"] if p["id"] == ctx["payment_id"])
        old_date = orig_pay.get("date", "")
        new_date = _date_offset(-1)
        if new_date == old_date:
            new_date = _date_offset(-2)

        r = requests.post(
            f"{API}/customers/{ctx['customer_id']}/edit-payment-date",
            json={
                "invoice_id": ctx["invoice_id"],
                "payment_id": ctx["payment_id"],
                "new_date": new_date,
                "manager_pin": TEST_ORG_MANAGER_PIN,
                "reason": "TEST iter191 happy path",
            },
            headers=auth["headers"],
            timeout=20,
        )
        assert r.status_code == 200, f"{r.status_code} {r.text}"
        body = r.json()
        assert body["new_date"] == new_date
        assert body["old_date"] == old_date
        assert "→" in body["message"], body["message"]

        # Verify persistence
        upd = db.invoices.find_one({"id": ctx["invoice_id"]}, {"_id": 0})
        upd_pay = next(p for p in upd["payments"] if p["id"] == ctx["payment_id"])
        assert upd_pay["date"] == new_date
        hist = upd_pay.get("date_edit_history", [])
        assert len(hist) >= 1
        last = hist[-1]
        assert last["from"] == old_date
        assert last["to"] == new_date
        assert last["reason"] == "TEST iter191 happy path"
        assert "authorized_by" in last
        assert "edited_at" in last

        # Restore for downstream tests
        db.invoices.update_one(
            {"id": ctx["invoice_id"], "payments.id": ctx["payment_id"]},
            {"$set": {"payments.$.date": old_date}},
        )

    def test_missing_fields(self, auth, seeded_invoice_with_payment):
        ctx = seeded_invoice_with_payment
        # Missing manager_pin
        r = requests.post(
            f"{API}/customers/{ctx['customer_id']}/edit-payment-date",
            json={"invoice_id": ctx["invoice_id"], "payment_id": ctx["payment_id"], "new_date": _today()},
            headers=auth["headers"],
            timeout=15,
        )
        assert r.status_code == 400
        # Missing invoice_id
        r2 = requests.post(
            f"{API}/customers/{ctx['customer_id']}/edit-payment-date",
            json={"payment_id": ctx["payment_id"], "new_date": _today(), "manager_pin": TEST_ORG_MANAGER_PIN},
            headers=auth["headers"],
            timeout=15,
        )
        assert r2.status_code == 400

    def test_invalid_pin(self, auth, seeded_invoice_with_payment):
        ctx = seeded_invoice_with_payment
        r = requests.post(
            f"{API}/customers/{ctx['customer_id']}/edit-payment-date",
            json={
                "invoice_id": ctx["invoice_id"],
                "payment_id": ctx["payment_id"],
                "new_date": _today(),
                "manager_pin": "000000",
            },
            headers=auth["headers"],
            timeout=15,
        )
        assert r.status_code == 403
        assert "not authorized" in r.json().get("detail", "").lower()

    def test_reject_voided_payment(self, auth, db, seeded_invoice_with_payment):
        ctx = seeded_invoice_with_payment
        # Inject a voided payment row
        voided_id = f"TEST-VOID-{uuid4().hex[:6]}"
        db.invoices.update_one(
            {"id": ctx["invoice_id"]},
            {"$push": {"payments": {
                "id": voided_id, "amount": 1, "date": _today(), "method": "cash", "voided": True,
            }}},
        )
        try:
            r = requests.post(
                f"{API}/customers/{ctx['customer_id']}/edit-payment-date",
                json={
                    "invoice_id": ctx["invoice_id"],
                    "payment_id": voided_id,
                    "new_date": _date_offset(-1),
                    "manager_pin": TEST_ORG_MANAGER_PIN,
                },
                headers=auth["headers"],
                timeout=15,
            )
            assert r.status_code == 400, r.text
            assert "voided" in r.json().get("detail", "").lower()
        finally:
            db.invoices.update_one(
                {"id": ctx["invoice_id"]},
                {"$pull": {"payments": {"id": voided_id}}},
            )

    def test_reject_when_original_date_closed(self, auth, db, seeded_invoice_with_payment):
        ctx = seeded_invoice_with_payment
        inv = db.invoices.find_one({"id": ctx["invoice_id"]}, {"_id": 0})
        pay = next(p for p in inv["payments"] if p["id"] == ctx["payment_id"])
        original_date = pay.get("date")
        branch_id = inv.get("branch_id")
        if not original_date or not branch_id:
            pytest.skip("payment lacks date or branch_id")
        seed_id = f"TEST-iter191-{uuid4().hex[:8]}"
        db.daily_closings.insert_one({
            "id": seed_id,
            "branch_id": branch_id,
            "date": original_date,
            "status": "closed",
            "organization_id": auth["org_id"],
        })
        try:
            r = requests.post(
                f"{API}/customers/{ctx['customer_id']}/edit-payment-date",
                json={
                    "invoice_id": ctx["invoice_id"],
                    "payment_id": ctx["payment_id"],
                    "new_date": _date_offset(-99),
                    "manager_pin": TEST_ORG_MANAGER_PIN,
                },
                headers=auth["headers"],
                timeout=15,
            )
            assert r.status_code == 400, r.text
            assert "z-report" in r.json().get("detail", "").lower()
        finally:
            db.daily_closings.delete_one({"id": seed_id})

    def test_reject_when_new_date_closed(self, auth, db, seeded_invoice_with_payment):
        ctx = seeded_invoice_with_payment
        inv = db.invoices.find_one({"id": ctx["invoice_id"]}, {"_id": 0})
        branch_id = inv.get("branch_id")
        if not branch_id:
            pytest.skip("no branch_id")
        target_new_date = _date_offset(-77)
        seed_id = f"TEST-iter191-{uuid4().hex[:8]}"
        db.daily_closings.insert_one({
            "id": seed_id,
            "branch_id": branch_id,
            "date": target_new_date,
            "status": "closed",
            "organization_id": auth["org_id"],
        })
        try:
            r = requests.post(
                f"{API}/customers/{ctx['customer_id']}/edit-payment-date",
                json={
                    "invoice_id": ctx["invoice_id"],
                    "payment_id": ctx["payment_id"],
                    "new_date": target_new_date,
                    "manager_pin": TEST_ORG_MANAGER_PIN,
                },
                headers=auth["headers"],
                timeout=15,
            )
            assert r.status_code == 400, r.text
            assert "target date" in r.json().get("detail", "").lower() or "already closed" in r.json().get("detail", "").lower()
        finally:
            db.daily_closings.delete_one({"id": seed_id})


def teardown_module(_module):
    # Best-effort cleanup of any TEST_ rows left behind
    db = MongoClient(MONGO_URL)[DB_NAME]
    db.fund_transfers.delete_many({"note": {"$regex": "^TEST iter191"}})
    db.daily_closings.delete_many({"id": {"$regex": "^TEST-iter191-"}})
    db.invoices.delete_many({"id": {"$regex": "^TEST-INV-"}})

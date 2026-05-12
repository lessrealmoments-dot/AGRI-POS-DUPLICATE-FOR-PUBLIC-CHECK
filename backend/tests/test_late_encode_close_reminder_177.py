"""
Iteration 177 — Late-Encode Closed-Day Sales + Close-Reminder SMS Scheduler

Covers:
  * POST /api/unified-sale closed-day guard (with & without late_encode)
  * Late-encode validations (cash/digital reject, 7-day cap, cross-month,
    short reason, missing/invalid PIN, daily cap = 5)
  * Late-encode tagging on invoice + late_encode_log entry creation
  * GET /api/sales/late-encoded-since-close shape & filter behaviour
  * routes.close_reminder importables: STAGES, tick_once, _in_quiet_hours,
    _build_branch_snapshot, send_zreport_finalized
  * routes.sms.DEFAULT_TEMPLATES contains the 8 new keys
"""
import os
import sys
import asyncio
import uuid
from datetime import datetime, timedelta, date, timezone

import pytest
import requests
from pymongo import MongoClient

# Make backend importable
sys.path.insert(0, "/app/backend")
sys.path.insert(0, "/app/backend/tests")

from _org_test_helpers import ensure_org_admin_token, TEST_ORG_MANAGER_PIN  # noqa: E402

def _read_frontend_env_var(key: str) -> str:
    try:
        with open("/app/frontend/.env") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith(f"{key}="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return ""


BASE_URL = (
    os.environ.get("REACT_APP_BACKEND_URL")
    or _read_frontend_env_var("REACT_APP_BACKEND_URL")
    or "https://br-suite-phase5.preview.emergentagent.com"
).rstrip("/")
API = f"{BASE_URL}/api"
MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "test_database")


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def auth():
    token, user = ensure_org_admin_token()
    return {"token": token, "user": user, "headers": {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }}


@pytest.fixture(scope="module")
def mdb():
    return MongoClient(MONGO_URL)[DB_NAME]


@pytest.fixture(scope="module")
def org_id(auth, mdb):
    return auth["user"].get("organization_id") or mdb.users.find_one(
        {"email": "test_org_admin@regression.local"}, {"_id": 0, "organization_id": 1}
    )["organization_id"]


@pytest.fixture(scope="module")
def branch_id(auth, mdb, org_id):
    br = mdb.branches.find_one({"organization_id": org_id, "active": {"$ne": False}}, {"_id": 0, "id": 1})
    if not br:
        br = mdb.branches.find_one({"active": {"$ne": False}}, {"_id": 0, "id": 1})
    assert br, "No branch available for tests"
    return br["id"]


@pytest.fixture(scope="module")
def stocked_product(auth, branch_id):
    r = requests.get(f"{API}/inventory?branch_id={branch_id}", headers=auth["headers"], timeout=20)
    assert r.status_code == 200, r.text
    items = r.json().get("items", [])
    for it in items:
        if (it.get("total_stock", 0) or 0) > 5 and not it.get("is_repack"):
            return it
    for it in items:
        if (it.get("total_stock", 0) or 0) > 1:
            return it
    pytest.skip("No stocked product available")


@pytest.fixture(scope="module")
def customer(auth, mdb, branch_id):
    r = requests.get(f"{API}/customers?limit=10", headers=auth["headers"], timeout=15)
    assert r.status_code == 200
    cs = r.json().get("customers", [])
    if cs:
        return cs[0]
    pytest.skip("No customer available")


@pytest.fixture
def closed_day_setup(mdb, branch_id, org_id):
    """Create a closed-day fixture for `yesterday` and clean up after."""
    yesterday = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
    closing_id = str(uuid.uuid4())
    mdb.daily_closings.delete_many({"branch_id": branch_id, "date": yesterday})
    mdb.daily_closings.insert_one({
        "id": closing_id,
        "branch_id": branch_id,
        "organization_id": org_id,
        "date": yesterday,
        "status": "closed",
        "closed_at": (datetime.now(timezone.utc) - timedelta(hours=12)).isoformat(),
        "actual_cash": 0,
        "expected_cash": 0,
    })
    yield {"date": yesterday, "id": closing_id}
    mdb.daily_closings.delete_many({"id": closing_id})


@pytest.fixture
def cleanup_late_invoices(mdb, branch_id):
    yield
    # Clean any TEST_ late-encode invoices created during this run
    test_inv_ids = [d["id"] for d in mdb.invoices.find(
        {"branch_id": branch_id, "late_encoded": True, "customer_name": {"$regex": "^TEST_"}},
        {"_id": 0, "id": 1},
    )]
    if test_inv_ids:
        mdb.invoices.delete_many({"id": {"$in": test_inv_ids}})
        mdb.late_encode_log.delete_many({"invoice_id": {"$in": test_inv_ids}})


def _build_sale_body(branch_id, product, customer, payment_type, order_date,
                     late_encode=None, fund_source="cashier"):
    body = {
        "branch_id": branch_id,
        "order_date": order_date,
        "payment_type": payment_type,
        "fund_source": fund_source,
        "customer_id": (customer or {}).get("id"),
        "customer_name": "TEST_LateEncode_" + (customer or {}).get("name", "X"),
        "items": [{
            "product_id": product["id"],
            "name": product.get("name", "Item"),
            "quantity": 1,
            "unit_price": float(product.get("selling_price") or product.get("price") or 100.0),
            "cost_price": float(product.get("cost_price") or 0),
            "subtotal": float(product.get("selling_price") or product.get("price") or 100.0),
        }],
        "subtotal": float(product.get("selling_price") or product.get("price") or 100.0),
        "discount": 0,
        "grand_total": float(product.get("selling_price") or product.get("price") or 100.0),
        "amount_paid": 0 if payment_type == "credit" else
                       float(product.get("selling_price") or product.get("price") or 100.0),
        "idempotency_key": f"test_le_{uuid.uuid4()}",
    }
    if late_encode is not None:
        body["late_encode"] = late_encode
    return body


# ── Closed-day guard tests ───────────────────────────────────────────────────

class TestClosedDayGuard:
    """POST /api/unified-sale on a closed day — guard semantics."""

    def test_closed_day_without_late_encode_returns_403(
        self, auth, branch_id, stocked_product, customer, closed_day_setup
    ):
        body = _build_sale_body(
            branch_id, stocked_product, customer, "credit",
            closed_day_setup["date"],
        )
        r = requests.post(f"{API}/unified-sale", json=body, headers=auth["headers"], timeout=20)
        assert r.status_code == 403, f"Expected 403, got {r.status_code}: {r.text}"
        msg = r.json().get("detail", "")
        assert "already closed" in msg.lower()
        assert "encode for past date" in msg.lower() or "past date" in msg.lower()

    def test_late_encode_cash_rejected(
        self, auth, branch_id, stocked_product, customer, closed_day_setup
    ):
        body = _build_sale_body(
            branch_id, stocked_product, customer, "cash",
            closed_day_setup["date"],
            late_encode={"reason": "Forgot to encode this", "pin": TEST_ORG_MANAGER_PIN},
        )
        r = requests.post(f"{API}/unified-sale", json=body, headers=auth["headers"], timeout=20)
        assert r.status_code == 403
        assert "credit" in r.json().get("detail", "").lower() or \
               "only" in r.json().get("detail", "").lower()

    def test_late_encode_digital_rejected(
        self, auth, branch_id, stocked_product, customer, closed_day_setup
    ):
        body = _build_sale_body(
            branch_id, stocked_product, customer, "credit",
            closed_day_setup["date"], fund_source="digital",
            late_encode={"reason": "Forgot to encode credit", "pin": TEST_ORG_MANAGER_PIN},
        )
        r = requests.post(f"{API}/unified-sale", json=body, headers=auth["headers"], timeout=20)
        assert r.status_code == 403
        assert "digital" in r.json().get("detail", "").lower() or \
               "credit" in r.json().get("detail", "").lower()

    def test_late_encode_days_back_over_7(
        self, auth, branch_id, stocked_product, customer, mdb, org_id
    ):
        old = (date.today() - timedelta(days=10)).strftime("%Y-%m-%d")
        cid = str(uuid.uuid4())
        mdb.daily_closings.delete_many({"branch_id": branch_id, "date": old})
        mdb.daily_closings.insert_one({
            "id": cid, "branch_id": branch_id, "date": old, "status": "closed",
            "organization_id": org_id,
            "closed_at": (datetime.now(timezone.utc) - timedelta(days=10)).isoformat(),
        })
        try:
            body = _build_sale_body(
                branch_id, stocked_product, customer, "credit", old,
                late_encode={"reason": "Forgot for over a week", "pin": TEST_ORG_MANAGER_PIN},
            )
            r = requests.post(f"{API}/unified-sale", json=body, headers=auth["headers"], timeout=20)
            # Could be 403 (>7 day cap) OR 403 (cross-month) — both acceptable
            assert r.status_code == 403, r.text
            d = r.json().get("detail", "").lower()
            assert ("7 days" in d) or ("month" in d), f"Unexpected detail: {d}"
        finally:
            mdb.daily_closings.delete_many({"id": cid})

    def test_late_encode_short_reason(
        self, auth, branch_id, stocked_product, customer, closed_day_setup
    ):
        body = _build_sale_body(
            branch_id, stocked_product, customer, "credit",
            closed_day_setup["date"],
            late_encode={"reason": "short", "pin": TEST_ORG_MANAGER_PIN},
        )
        r = requests.post(f"{API}/unified-sale", json=body, headers=auth["headers"], timeout=20)
        assert r.status_code == 400
        assert "10 characters" in r.json().get("detail", "")

    def test_late_encode_missing_pin(
        self, auth, branch_id, stocked_product, customer, closed_day_setup
    ):
        body = _build_sale_body(
            branch_id, stocked_product, customer, "credit",
            closed_day_setup["date"],
            late_encode={"reason": "Customer credit forgotten yesterday", "pin": ""},
        )
        r = requests.post(f"{API}/unified-sale", json=body, headers=auth["headers"], timeout=20)
        assert r.status_code in (400, 403)
        assert "pin" in r.json().get("detail", "").lower()

    def test_late_encode_invalid_pin(
        self, auth, branch_id, stocked_product, customer, closed_day_setup
    ):
        body = _build_sale_body(
            branch_id, stocked_product, customer, "credit",
            closed_day_setup["date"],
            late_encode={"reason": "Customer credit forgotten yesterday", "pin": "000000"},
        )
        r = requests.post(f"{API}/unified-sale", json=body, headers=auth["headers"], timeout=20)
        assert r.status_code == 403
        assert "pin" in r.json().get("detail", "").lower() or "invalid" in r.json().get("detail", "").lower()

    def test_late_encode_success_creates_invoice_and_log(
        self, auth, branch_id, stocked_product, customer, closed_day_setup, mdb,
        cleanup_late_invoices,
    ):
        body = _build_sale_body(
            branch_id, stocked_product, customer, "credit",
            closed_day_setup["date"],
            late_encode={"reason": "Customer paid cash but I forgot to encode it",
                         "pin": TEST_ORG_MANAGER_PIN},
        )
        r = requests.post(f"{API}/unified-sale", json=body, headers=auth["headers"], timeout=25)
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
        data = r.json()
        inv_id = data.get("id") or data.get("invoice", {}).get("id")
        assert inv_id, f"No invoice id in response: {data}"

        # Verify DB state
        inv = mdb.invoices.find_one({"id": inv_id}, {"_id": 0})
        assert inv is not None
        assert inv.get("late_encoded") is True
        assert inv.get("late_encoded_at")
        assert inv.get("late_encode_reason") == "Customer paid cash but I forgot to encode it"
        assert inv.get("late_encoded_by_name")
        assert inv.get("late_encode_verifier_name")
        assert inv.get("late_encode_days_back") == 1

        # late_encode_log entry created
        log_entry = mdb.late_encode_log.find_one({"invoice_id": inv_id}, {"_id": 0})
        assert log_entry is not None
        assert log_entry.get("days_back") == 1
        assert log_entry.get("verifier_name")

    def test_late_encode_daily_cap(
        self, auth, branch_id, stocked_product, customer, closed_day_setup, mdb,
        org_id, cleanup_late_invoices,
    ):
        """Pre-seed 5 late-encoded invoices today to trip the cap."""
        today_str = date.today().strftime("%Y-%m-%d")
        seed_ids = []
        for _ in range(5):
            iid = str(uuid.uuid4())
            mdb.invoices.insert_one({
                "id": iid,
                "branch_id": branch_id,
                "organization_id": org_id,
                "late_encoded": True,
                "late_encoded_at": f"{today_str}T10:00:00",
                "status": "active",
                "customer_name": "TEST_DailyCapSeed",
                "payment_type": "credit",
                "grand_total": 50.0,
            })
            seed_ids.append(iid)
        try:
            body = _build_sale_body(
                branch_id, stocked_product, customer, "credit",
                closed_day_setup["date"],
                late_encode={"reason": "Should be blocked by daily cap rule",
                             "pin": TEST_ORG_MANAGER_PIN},
            )
            r = requests.post(f"{API}/unified-sale", json=body, headers=auth["headers"], timeout=20)
            assert r.status_code == 403, r.text
            assert "5/day" in r.json().get("detail", "") or \
                   "limit" in r.json().get("detail", "").lower()
        finally:
            mdb.invoices.delete_many({"id": {"$in": seed_ids}})


# ── /api/sales/late-encoded-since-close ──────────────────────────────────────

class TestLateEncodedSinceCloseEndpoint:
    def test_returns_correct_shape(self, auth, branch_id):
        r = requests.get(
            f"{API}/sales/late-encoded-since-close?branch_id={branch_id}",
            headers=auth["headers"], timeout=15,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        for k in ("last_close_at", "entries", "count", "total_amount"):
            assert k in data, f"Missing key {k}"
        assert isinstance(data["entries"], list)
        assert isinstance(data["count"], int)
        assert data["count"] == len(data["entries"])

    def test_missing_branch_id_400(self, auth):
        r = requests.get(
            f"{API}/sales/late-encoded-since-close",
            headers=auth["headers"], timeout=15,
        )
        assert r.status_code in (400, 422)


# ── Module imports & in-process unit-style checks ────────────────────────────

class TestCloseReminderModule:
    def test_imports(self):
        from routes.close_reminder import (
            tick_once, _build_branch_snapshot, _in_quiet_hours,
            send_zreport_finalized, STAGES,
        )
        assert callable(tick_once)
        assert callable(_build_branch_snapshot)
        assert callable(_in_quiet_hours)
        assert callable(send_zreport_finalized)
        assert isinstance(STAGES, list) and len(STAGES) >= 7

    def test_stages_contents(self):
        from routes.close_reminder import STAGES
        keys = [s["key"] for s in STAGES]
        for required in [
            "close_catchup_3pm", "close_precheck", "close_late_notice",
            "close_status_snapshot", "close_escalation",
            "close_overdue_next_day", "close_overdue_multi_day",
        ]:
            assert required in keys, f"Missing stage {required}"

        catchup = next(s for s in STAGES if s["key"] == "close_catchup_3pm")
        assert catchup.get("offset_h") == -3.0
        assert "cashier" in catchup["recipients"]

        precheck = next(s for s in STAGES if s["key"] == "close_precheck")
        assert precheck.get("offset_h") == 0.0

        late = next(s for s in STAGES if s["key"] == "close_late_notice")
        assert late.get("offset_h") == 1.5

        snap = next(s for s in STAGES if s["key"] == "close_status_snapshot")
        assert snap.get("offset_h") == 2.5

        esc = next(s for s in STAGES if s["key"] == "close_escalation")
        assert esc.get("offset_h") == 3.5
        assert "owner" in esc["recipients"] or "admin" in esc["recipients"]

        nd = next(s for s in STAGES if s["key"] == "close_overdue_next_day")
        assert nd.get("fixed_hour") == 7
        assert nd.get("day_offset") == 1

        md = next(s for s in STAGES if s["key"] == "close_overdue_multi_day")
        assert md.get("fixed_hour") == 12
        assert md.get("day_offset") == 1

    def test_in_quiet_hours(self):
        from routes.close_reminder import _in_quiet_hours
        base = datetime(2026, 1, 15, 0, 0, tzinfo=timezone.utc)
        for h in (22, 23, 0, 1, 6):
            assert _in_quiet_hours(base.replace(hour=h)) is True, f"Expected quiet at {h}"
        for h in (7, 8, 12, 18, 21):
            assert _in_quiet_hours(base.replace(hour=h)) is False, f"Expected NON-quiet at {h}"

    def test_build_branch_snapshot_shape(self, branch_id):
        from routes.close_reminder import _build_branch_snapshot
        today = date.today().strftime("%Y-%m-%d")
        snap = asyncio.run(_build_branch_snapshot(branch_id, today))
        for k in ("sales_count", "sales_total", "cash_total", "credit_total",
                  "digital_total", "credit_count", "expense_count",
                  "expense_total", "cash_expected", "pending_credits"):
            assert k in snap, f"Snapshot missing key {k}"
        assert isinstance(snap["sales_count"], int)
        assert isinstance(snap["credit_count"], int)


# ── SMS DEFAULT_TEMPLATES contains 8 new keys ────────────────────────────────

class TestSmsTemplates:
    def test_default_templates_contains_new_keys(self):
        from routes.sms import DEFAULT_TEMPLATES
        keys = {t["key"] for t in DEFAULT_TEMPLATES}
        required = {
            "close_catchup_3pm", "close_precheck", "close_late_notice",
            "close_status_snapshot", "close_escalation",
            "close_overdue_next_day", "close_overdue_multi_day",
            "zreport_finalized",
        }
        missing = required - keys
        assert not missing, f"Missing templates: {missing}"

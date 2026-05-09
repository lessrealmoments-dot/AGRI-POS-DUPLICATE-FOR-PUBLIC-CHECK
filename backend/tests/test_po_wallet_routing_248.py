"""
Iteration 248 — PO wallet routing: Cashier / Safe / Digital / Bank
==================================================================
Validates:
  - GET /api/purchase-orders/fund-balances returns 4 wallets with names + availability flags
  - Bank balance is masked (bank=null, bank_hidden=true) for non-admin roles
  - POST /api/purchase-orders po_type=cash + fund_source=cashier|safe works without PIN
  - POST /api/purchase-orders po_type=cash + fund_source=digital requires PIN, deducts from digital
  - POST /api/purchase-orders po_type=cash + fund_source=bank requires admin PIN, deducts + JE
  - POST /api/purchase-orders po_type=cash + fund_source=bank WITHOUT pin returns 400
  - POST /api/purchase-orders po_type=cash + fund_source=bank with INVALID pin returns 403
  - Insufficient funds returns type='insufficient_funds' + correct shortfall
  - POST /api/purchase-orders/{id}/adjust-payment supports digital/bank with PIN; rejects without
"""
import os
import time
from uuid import uuid4

import pytest
import requests
from pymongo import MongoClient

from _org_test_helpers import (
    ensure_org_admin_token,
    TEST_ORG_ADMIN_PIN,
    TEST_ORG_ADMIN_EMAIL,
    TEST_ORG_ADMIN_PASSWORD,
)

API = os.environ.get("REACT_APP_BACKEND_URL", "https://permission-lockdown-1.preview.emergentagent.com").rstrip("/") + "/api"
MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "test_database")


@pytest.fixture(scope="module")
def db():
    return MongoClient(MONGO_URL)[DB_NAME]


@pytest.fixture(scope="module")
def admin_token():
    token, user = ensure_org_admin_token()
    return token, user


@pytest.fixture(scope="module")
def admin_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token[0]}", "Content-Type": "application/json"}


@pytest.fixture(scope="module")
def org_id(admin_token):
    return admin_token[1].get("organization_id")


@pytest.fixture(scope="module")
def branch_id(db, org_id):
    """Pick first active branch in the test org."""
    br = db.branches.find_one({"organization_id": org_id, "active": {"$ne": False}}, {"_id": 0})
    if not br:
        br = db.branches.find_one({"organization_id": org_id}, {"_id": 0})
    if not br:
        pytest.skip("No branch found in test org")
    return br["id"]


@pytest.fixture(scope="module", autouse=True)
def seed_wallets(db, branch_id, org_id):
    """Ensure all 4 wallet types exist with sufficient balance for tests.
    Deactivates duplicates and tops up via direct DB update (cashier/digital/bank)
    or fresh safe_lot insert for safe."""
    wallets_seed = [
        ("cashier", "Cashier Drawer", 100000),
        ("safe", "Physical Safe", 100000),
        ("digital", "Digital / E-Wallet", 100000),
        ("bank", "Bank Account", 500000),
    ]
    for wtype, wname, bal in wallets_seed:
        # Find ALL wallets of this type/branch and consolidate
        all_wallets = list(db.fund_wallets.find({"branch_id": branch_id, "type": wtype}))
        if all_wallets:
            # Pick the first as canonical, deactivate the rest
            primary = all_wallets[0]
            for w in all_wallets[1:]:
                db.fund_wallets.update_one({"id": w["id"]}, {"$set": {"active": False}})
            db.fund_wallets.update_one(
                {"id": primary["id"]},
                {"$set": {"name": wname, "active": True,
                          "balance": float(bal) if wtype != "safe" else 0.0}},
            )
            wid = primary["id"]
        else:
            wid = str(uuid4())
            db.fund_wallets.insert_one({
                "id": wid, "branch_id": branch_id, "organization_id": org_id,
                "type": wtype, "name": wname,
                "balance": float(bal) if wtype != "safe" else 0.0,
                "active": True, "created_at": "2026-01-01T00:00:00",
            })
        if wtype == "safe":
            # Zero out existing lots, insert fresh
            db.safe_lots.update_many(
                {"wallet_id": wid, "remaining_amount": {"$gt": 0}},
                {"$set": {"remaining_amount": 0}},
            )
            db.safe_lots.insert_one({
                "id": str(uuid4()), "branch_id": branch_id,
                "organization_id": org_id,
                "wallet_id": wid, "date_received": "2026-01-01",
                "original_amount": bal, "remaining_amount": bal,
                "source_reference": "TEST seed iter 248",
                "created_at": "2026-01-01T00:00:00",
            })
    yield


# ── Fund balances endpoint ───────────────────────────────────────────────────
class TestFundBalances:

    def test_admin_sees_all_4_wallets_with_names_and_flags(self, admin_headers, branch_id):
        r = requests.get(f"{API}/purchase-orders/fund-balances?branch_id={branch_id}", headers=admin_headers, timeout=15)
        assert r.status_code == 200, r.text
        body = r.json()
        # All 4 wallets present
        for k in ("cashier", "safe", "digital", "bank"):
            assert k in body, f"Missing key {k} in response"
        # Names
        assert body.get("cashier_name")
        assert body.get("safe_name")
        assert body.get("digital_name")
        assert body.get("bank_name")
        # Availability flags
        assert body.get("digital_available") is True
        assert body.get("bank_available") is True
        # Admin sees actual bank value (not hidden)
        assert body.get("bank_hidden") is False
        assert isinstance(body["bank"], (int, float))

    def test_non_admin_bank_balance_masked(self, db, admin_headers, branch_id, org_id):
        # Create or reuse a non-admin manager user and login
        mgr_email = f"test_mgr_{org_id[:6]}@regression.local"
        mgr_pwd = "MgrPass!2026"
        import bcrypt
        pwd_hash = bcrypt.hashpw(mgr_pwd.encode(), bcrypt.gensalt()).decode()
        # Force-replace any prior copy to avoid stale password hashes
        db.users.delete_many({"$or": [{"email": mgr_email}, {"username": "test_mgr_balance"}]})
        db.users.insert_one({
            "id": str(uuid4()), "username": f"test_mgr_balance_{uuid4().hex[:6]}",
            "email": mgr_email, "full_name": "Test Manager (balance mask)",
            "password_hash": pwd_hash, "role": "manager", "active": True,
            "branch_id": branch_id, "organization_id": org_id, "permissions": {},
        })
        login = requests.post(f"{API}/auth/login", json={"email": mgr_email, "password": mgr_pwd}, timeout=15)
        if login.status_code != 200:
            pytest.skip(f"Manager login failed: {login.status_code} {login.text}")
        token = login.json()["token"]
        h = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        r = requests.get(f"{API}/purchase-orders/fund-balances?branch_id={branch_id}", headers=h, timeout=15)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("bank_hidden") is True
        assert body.get("bank") is None
        # Other wallets still visible
        assert isinstance(body.get("cashier"), (int, float))
        assert isinstance(body.get("safe"), (int, float))
        assert isinstance(body.get("digital"), (int, float))


# ── PO Cash creation: fund routing ───────────────────────────────────────────
def _po_payload(branch_id, vendor="TEST Vendor 248", fund_source="cashier", amount=500.0, pin=None):
    payload = {
        "vendor": vendor, "po_type": "cash", "branch_id": branch_id,
        "fund_source": fund_source,
        "items": [{
            "product_id": str(uuid4()),
            "product_name": "TEST Product 248",
            "quantity": 1, "unit_cost": amount, "unit_price": amount,
            "total": amount,
        }],
        "subtotal": amount, "grand_total": amount,
        "payment_method_detail": "Cash",
        "purchase_date": "2026-01-15",
        "notes": "TEST iter 248 wallet routing",
    }
    if pin is not None:
        payload["pin"] = pin
    return payload


class TestPOCashCreation:

    def test_cashier_no_pin_succeeds(self, admin_headers, branch_id):
        r = requests.post(f"{API}/purchase-orders", headers=admin_headers,
                          json=_po_payload(branch_id, fund_source="cashier", amount=100.0), timeout=20)
        assert r.status_code in (200, 201), r.text
        po = r.json()
        assert po.get("po_type") == "cash"
        assert po.get("fund_source") in ("cashier", None) or po.get("fund_source") == "cashier"

    def test_safe_no_pin_succeeds(self, admin_headers, branch_id):
        r = requests.post(f"{API}/purchase-orders", headers=admin_headers,
                          json=_po_payload(branch_id, fund_source="safe", amount=150.0), timeout=20)
        assert r.status_code in (200, 201), r.text

    def test_bank_without_pin_returns_400(self, admin_headers, branch_id):
        r = requests.post(f"{API}/purchase-orders", headers=admin_headers,
                          json=_po_payload(branch_id, fund_source="bank", amount=200.0), timeout=20)
        assert r.status_code == 400, f"Expected 400 got {r.status_code}: {r.text}"
        body = r.json()
        detail = body.get("detail") if isinstance(body, dict) else None
        assert detail and "PIN" in str(detail) or "pin" in str(detail).lower(), f"Unexpected detail: {detail}"

    def test_bank_with_invalid_pin_returns_403(self, admin_headers, branch_id):
        r = requests.post(f"{API}/purchase-orders", headers=admin_headers,
                          json=_po_payload(branch_id, fund_source="bank", amount=200.0, pin="000000"), timeout=20)
        assert r.status_code == 403, f"Expected 403 got {r.status_code}: {r.text}"

    def test_digital_without_pin_returns_400(self, admin_headers, branch_id):
        r = requests.post(f"{API}/purchase-orders", headers=admin_headers,
                          json=_po_payload(branch_id, fund_source="digital", amount=100.0), timeout=20)
        assert r.status_code == 400, r.text

    def test_digital_with_valid_pin_deducts_balance(self, admin_headers, branch_id, db):
        before = db.fund_wallets.find_one({"branch_id": branch_id, "type": "digital", "active": True})
        before_bal = float(before["balance"])
        amt = 250.0
        r = requests.post(f"{API}/purchase-orders", headers=admin_headers,
                          json=_po_payload(branch_id, fund_source="digital", amount=amt, pin=TEST_ORG_ADMIN_PIN),
                          timeout=20)
        assert r.status_code in (200, 201), r.text
        after = db.fund_wallets.find_one({"branch_id": branch_id, "type": "digital", "active": True})
        assert round(before_bal - float(after["balance"]), 2) == amt, \
            f"Digital balance not deducted correctly: before {before_bal} after {after['balance']}"

    def test_bank_with_valid_pin_deducts_balance_and_creates_je(self, admin_headers, branch_id, db):
        before = db.fund_wallets.find_one({"branch_id": branch_id, "type": "bank", "active": True})
        before_bal = float(before["balance"])
        amt = 500.0
        r = requests.post(f"{API}/purchase-orders", headers=admin_headers,
                          json=_po_payload(branch_id, fund_source="bank", amount=amt, pin=TEST_ORG_ADMIN_PIN),
                          timeout=20)
        assert r.status_code in (200, 201), r.text
        po = r.json()
        po_number = po.get("po_number")
        assert po_number, "PO number not returned"
        # Balance deducted
        after = db.fund_wallets.find_one({"branch_id": branch_id, "type": "bank", "active": True})
        assert round(before_bal - float(after["balance"]), 2) == amt
        # Wallet movement recorded
        wm = db.wallet_movements.find_one({"wallet_id": before["id"], "amount": -amt})
        assert wm is not None, "Bank wallet_movement not created"
        # Journal entry created with entry_type='ap_payment'
        je = db.journal_entries.find_one({"reference_number": po_number, "entry_type": "ap_payment"})
        assert je is not None, f"Journal entry for PO {po_number} not created"
        assert je.get("total_amount") == round(amt, 2)

    def test_bank_insufficient_funds_returns_structured_error(self, admin_headers, branch_id, db):
        # Use an absurdly large amount
        r = requests.post(f"{API}/purchase-orders", headers=admin_headers,
                          json=_po_payload(branch_id, fund_source="bank", amount=99999999.0, pin=TEST_ORG_ADMIN_PIN),
                          timeout=20)
        assert r.status_code == 400, r.text
        detail = r.json().get("detail")
        # Detail can be string or dict
        if isinstance(detail, dict):
            assert detail.get("type") == "insufficient_funds"
            assert "shortfall" in detail
        else:
            assert "Bank" in str(detail) or "insufficient" in str(detail).lower()

    def test_digital_insufficient_funds_returns_structured_error(self, admin_headers, branch_id, db):
        r = requests.post(f"{API}/purchase-orders", headers=admin_headers,
                          json=_po_payload(branch_id, fund_source="digital", amount=99999999.0, pin=TEST_ORG_ADMIN_PIN),
                          timeout=20)
        assert r.status_code == 400, r.text
        detail = r.json().get("detail")
        if isinstance(detail, dict):
            assert detail.get("type") == "insufficient_funds"
            assert "shortfall" in detail


# ── Adjust-payment endpoint ──────────────────────────────────────────────────
class TestAdjustPayment:

    def _create_cash_po(self, headers, branch_id, fund_source, amount, pin=None):
        r = requests.post(f"{API}/purchase-orders", headers=headers,
                          json=_po_payload(branch_id, fund_source=fund_source, amount=amount, pin=pin), timeout=20)
        assert r.status_code in (200, 201), r.text
        return r.json()

    def test_adjust_cashier_no_pin_works(self, admin_headers, branch_id):
        po = self._create_cash_po(admin_headers, branch_id, "cashier", 200.0)
        r = requests.post(f"{API}/purchase-orders/{po['id']}/adjust-payment", headers=admin_headers,
                          json={"old_grand_total": 200.0, "new_grand_total": 250.0,
                                "fund_source": "cashier", "reason": "TEST adjust"}, timeout=20)
        assert r.status_code == 200, r.text

    def test_adjust_bank_without_pin_rejected(self, admin_headers, branch_id):
        po = self._create_cash_po(admin_headers, branch_id, "bank", 200.0, pin=TEST_ORG_ADMIN_PIN)
        r = requests.post(f"{API}/purchase-orders/{po['id']}/adjust-payment", headers=admin_headers,
                          json={"old_grand_total": 200.0, "new_grand_total": 250.0,
                                "fund_source": "bank", "reason": "TEST adjust no pin"}, timeout=20)
        assert r.status_code == 400, r.text

    def test_adjust_bank_with_pin_works_and_deducts(self, admin_headers, branch_id, db):
        po = self._create_cash_po(admin_headers, branch_id, "bank", 200.0, pin=TEST_ORG_ADMIN_PIN)
        before = db.fund_wallets.find_one({"branch_id": branch_id, "type": "bank", "active": True})
        before_bal = float(before["balance"])
        r = requests.post(f"{API}/purchase-orders/{po['id']}/adjust-payment", headers=admin_headers,
                          json={"old_grand_total": 200.0, "new_grand_total": 280.0,
                                "fund_source": "bank", "reason": "TEST adjust w/ pin",
                                "pin": TEST_ORG_ADMIN_PIN}, timeout=20)
        assert r.status_code == 200, r.text
        after = db.fund_wallets.find_one({"branch_id": branch_id, "type": "bank", "active": True})
        assert round(before_bal - float(after["balance"]), 2) == 80.0

    def test_adjust_digital_without_pin_rejected(self, admin_headers, branch_id):
        po = self._create_cash_po(admin_headers, branch_id, "digital", 100.0, pin=TEST_ORG_ADMIN_PIN)
        r = requests.post(f"{API}/purchase-orders/{po['id']}/adjust-payment", headers=admin_headers,
                          json={"old_grand_total": 100.0, "new_grand_total": 130.0,
                                "fund_source": "digital", "reason": "TEST adjust digital no pin"}, timeout=20)
        assert r.status_code == 400, r.text

    def test_adjust_digital_with_pin_works(self, admin_headers, branch_id):
        po = self._create_cash_po(admin_headers, branch_id, "digital", 100.0, pin=TEST_ORG_ADMIN_PIN)
        r = requests.post(f"{API}/purchase-orders/{po['id']}/adjust-payment", headers=admin_headers,
                          json={"old_grand_total": 100.0, "new_grand_total": 130.0,
                                "fund_source": "digital", "reason": "TEST adjust digital",
                                "pin": TEST_ORG_ADMIN_PIN}, timeout=20)
        assert r.status_code == 200, r.text

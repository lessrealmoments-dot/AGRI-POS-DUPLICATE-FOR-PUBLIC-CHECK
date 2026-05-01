"""
Iteration 194 — Phone field on team users.

Confirms the new `phone` field on the Users API and that it flows into the
close-reminder recipient resolver so the SMS scheduler can find the number.
"""
import pytest
import requests

from _org_test_helpers import API, _db, ensure_org_admin_token


@pytest.fixture(scope="module")
def auth():
    return ensure_org_admin_token()


@pytest.fixture
def created_user(auth):
    token, _ = auth
    db = _db()
    db.users.delete_many({"email": "phone194@regression.local"})

    r = requests.post(
        f"{API}/users",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "full_name": "Phone194 Manager",
            "email": "phone194@regression.local",
            "phone": "09171234567",
            "role": "manager",
            "password": "Phone194Pw!2026",
        },
        timeout=15,
    )
    assert r.status_code == 200, r.text
    user_id = r.json()["id"]

    yield user_id

    requests.delete(f"{API}/users/{user_id}",
                    headers={"Authorization": f"Bearer {token}"}, timeout=10)
    db.users.delete_many({"email": "phone194@regression.local"})


def test_create_user_persists_phone(auth, created_user):
    token, _ = auth
    db = _db()
    user = db.users.find_one({"id": created_user}, {"_id": 0, "phone": 1, "role": 1})
    assert user["phone"] == "09171234567"
    assert user["role"] == "manager"

    # And the listing API echoes it back
    r = requests.get(f"{API}/users", headers={"Authorization": f"Bearer {token}"}, timeout=10)
    found = next((u for u in r.json() if u["id"] == created_user), None)
    assert found and found["phone"] == "09171234567"


def test_update_user_phone(auth, created_user):
    token, _ = auth
    r = requests.put(
        f"{API}/users/{created_user}",
        headers={"Authorization": f"Bearer {token}"},
        json={"phone": "09180000000"},
        timeout=10,
    )
    assert r.status_code == 200, r.text
    assert r.json()["phone"] == "09180000000"


def test_phone_flows_into_diagnose(auth, created_user):
    """The /sms/close-reminder/diagnose endpoint should pick up the manager's
    phone from the Users collection — proving the team-page phone is the
    canonical source for SMS recipient resolution."""
    token, _ = auth
    # Update phone to a known value
    requests.put(
        f"{API}/users/{created_user}",
        headers={"Authorization": f"Bearer {token}"},
        json={"phone": "09190000000"},
        timeout=10,
    )

    r = requests.get(
        f"{API}/sms/close-reminder/diagnose",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # Find at least one branch that surfaces our phone in the manager list
    found = any(
        "09190000000" in (br.get("recipient_phones", {}).get("manager") or [])
        for br in body.get("branches", [])
    )
    assert found, f"Manager phone not found in diagnose output: {body}"


def test_create_user_without_phone_still_works(auth):
    """Phone is optional — omitting it should still create the user with
    empty phone string (resolver simply skips that user)."""
    token, _ = auth
    db = _db()
    db.users.delete_many({"email": "phone194_nophone@regression.local"})
    r = requests.post(
        f"{API}/users",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "full_name": "NoPhone Cashier",
            "email": "phone194_nophone@regression.local",
            "role": "cashier",
            "password": "NoPhonePw!2026",
        },
        timeout=15,
    )
    assert r.status_code == 200, r.text
    assert r.json().get("phone", "") == ""

    requests.delete(f"{API}/users/{r.json()['id']}",
                    headers={"Authorization": f"Bearer {token}"}, timeout=10)
    db.users.delete_many({"email": "phone194_nophone@regression.local"})

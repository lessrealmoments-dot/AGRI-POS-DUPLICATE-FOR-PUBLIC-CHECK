"""
Phase 1A regression tests — C-1, C-2, C-3 (Audit 2026-02).

These tests use the running backend at REACT_APP_BACKEND_URL (matches the
existing test pattern in this repo) and the credentials from
/app/memory/test_credentials.md.

Run:
    pytest -xvs backend/tests/test_phase1a_security.py
"""
import os
import json
import pathlib
import pytest
import httpx


def _backend_url() -> str:
    env_path = pathlib.Path("/app/frontend/.env")
    for line in env_path.read_text().splitlines():
        if line.startswith("REACT_APP_BACKEND_URL="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("REACT_APP_BACKEND_URL not found in frontend/.env")


BACKEND = _backend_url()
ADMIN_EMAIL = os.environ.get("TEST_EMAIL", "janmarkeahig@gmail.com")
ADMIN_PASSWORD = os.environ.get("TEST_PASSWORD", "Aa@58798546521325")


@pytest.fixture(scope="module")
def admin_token() -> str:
    with httpx.Client(base_url=BACKEND, timeout=30) as c:
        r = c.post("/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
        r.raise_for_status()
        return r.json()["token"]


# ───────────────────────────────────────────────────────────────────────────
# C-1: /api/sync/pos-data must NOT ship plain PINs or admin_pin_hash
# ───────────────────────────────────────────────────────────────────────────
def test_c1_sync_payload_does_not_leak_pins(admin_token):
    with httpx.Client(base_url=BACKEND, timeout=30) as c:
        r = c.get(
            "/api/sync/pos-data",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        r.raise_for_status()
        body = r.json()

    # admin_pin_hash either absent or explicitly null — never a real hash
    assert body.get("admin_pin_hash") in (None, "", []), (
        "C-1 regression: admin_pin_hash must not be exposed to the client"
    )

    # Every grant entry must NOT carry a `pin` field
    grants = body.get("offline_pin_grants") or []
    for g in grants:
        assert "pin" not in g, (
            f"C-1 regression: offline_pin_grants entry leaked a `pin` field: {g}"
        )

    # Sanity — identity directory still present so UI can render labels
    if grants:
        assert all("verifier_id" in g and "verifier_name" in g for g in grants), (
            "Identity directory must still ship verifier_id + verifier_name"
        )


def test_c1_sync_payload_unauthenticated_rejected():
    with httpx.Client(base_url=BACKEND, timeout=30) as c:
        r = c.get("/api/sync/pos-data")
    assert r.status_code in (401, 403), (
        f"/api/sync/pos-data must require auth, got {r.status_code}"
    )


# ───────────────────────────────────────────────────────────────────────────
# C-2: /api/setup/reset must require super-admin + env flag + password
# ───────────────────────────────────────────────────────────────────────────
def test_c2_setup_reset_unauthenticated_rejected():
    with httpx.Client(base_url=BACKEND, timeout=30) as c:
        r = c.post(
            "/api/setup/reset",
            json={"confirm_text": "DELETE ALL DATA"},
        )
    assert r.status_code in (401, 403), (
        f"/api/setup/reset must reject unauthenticated callers, got {r.status_code}"
    )


def test_c2_setup_reset_admin_token_blocked_without_env_flag(admin_token):
    """Even an authenticated admin must be blocked unless ALLOW_DB_RESET is set."""
    with httpx.Client(base_url=BACKEND, timeout=30) as c:
        r = c.post(
            "/api/setup/reset",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "confirm_text": "DELETE ALL DATA",
                "password": ADMIN_PASSWORD,
            },
        )
    # Should be 403 because ALLOW_DB_RESET is not set in this environment
    # (and even if the user is super-admin, the env flag must gate the wipe).
    assert r.status_code == 403, (
        f"/api/setup/reset must be blocked without ALLOW_DB_RESET, got {r.status_code}: {r.text}"
    )
    assert "ALLOW_DB_RESET" in r.text or "disabled" in r.text.lower()


# ───────────────────────────────────────────────────────────────────────────
# C-3: /api/admin-auth/setup-totp must refuse re-enrolment after first login
# ───────────────────────────────────────────────────────────────────────────
def test_c3_setup_totp_blocked_when_totp_already_enabled():
    """
    Use the regular admin login to obtain a pending_token, then attempt to
    call /api/admin-auth/setup-totp with it. Because the production super-admin
    has TOTP enabled (or has logged in before), the new C-3 guard MUST refuse.
    """
    with httpx.Client(base_url=BACKEND, timeout=30) as c:
        # Step 1: admin-auth/login → pending_token
        r = c.post(
            "/api/admin-auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        )
        if r.status_code != 200:
            pytest.skip(
                f"admin-auth/login not available on this env "
                f"({r.status_code}: {r.text[:120]})"
            )
        pending = r.json().get("pending_token")
        assert pending, "expected pending_token in response"

        # Step 2: attempt to enrol new TOTP secret — must be refused
        r2 = c.post(
            "/api/admin-auth/setup-totp",
            json={"pending_token": pending},
        )

    # Either 403 (already enabled / post-first-login) is the protected outcome.
    # 401 is acceptable if the env's super-admin has expired the pending token,
    # but the protective code path on a real account is 403.
    assert r2.status_code in (401, 403), (
        f"/api/admin-auth/setup-totp must refuse re-enrolment, got {r2.status_code}"
    )


# ───────────────────────────────────────────────────────────────────────────
# Related JWT org cross-check (H-8 hardening that supports C-1)
# ───────────────────────────────────────────────────────────────────────────
def test_jwt_org_cross_check_health_still_ok(admin_token):
    """Sanity: the org cross-check must not break a normal authed call."""
    with httpx.Client(base_url=BACKEND, timeout=30) as c:
        r = c.get(
            "/api/health",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    assert r.status_code == 200

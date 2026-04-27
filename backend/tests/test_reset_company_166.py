"""
Test suite for Reset Company feature (iteration 166).
Tests: POST /api/backups/org/{org_id}/reset and GET /api/backups/org/{org_id}/download/{filename}
Focus: auth guards, password verification, confirmation text validation.
Note: Full end-to-end reset is not tested (requires live TOTP).
"""
import pytest
import requests
import os

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")

# Real org from DB: AgriBooks (Default)
TEST_ORG_ID = "bdf13cc0-05fb-47b1-845b-b17f0b01d7c4"
TEST_ORG_NAME = "AgriBooks (Default)"
EXPECTED_CONFIRMATION = f"{TEST_ORG_NAME} Reset"

SUPER_ADMIN_EMAIL = "janmarkeahig@gmail.com"
SUPER_ADMIN_PASSWORD = "Aa@58798546521325"


@pytest.fixture(scope="module")
def super_admin_token():
    """Get super admin JWT token."""
    resp = requests.post(f"{BASE_URL}/api/auth/login", json={
        "email": SUPER_ADMIN_EMAIL,
        "password": SUPER_ADMIN_PASSWORD
    })
    assert resp.status_code == 200, f"Super admin login failed: {resp.text}"
    return resp.json().get("token")


@pytest.fixture
def auth_headers(super_admin_token):
    return {"Authorization": f"Bearer {super_admin_token}", "Content-Type": "application/json"}


# ── POST /api/backups/org/{org_id}/reset ──────────────────────────────────────

class TestResetEndpointAuth:
    """Test authentication guards for reset endpoint."""

    def test_reset_without_token_returns_403(self):
        """No auth token → 403 Not authenticated."""
        resp = requests.post(
            f"{BASE_URL}/api/backups/org/{TEST_ORG_ID}/reset",
            json={"confirmation": "test", "password": "test", "totp_code": "123456"}
        )
        assert resp.status_code == 403, f"Expected 403, got {resp.status_code}"
        data = resp.json()
        assert "not authenticated" in data.get("detail", "").lower(), \
            f"Expected 'Not authenticated', got: {data.get('detail')}"
        print("PASS: Reset without token returns 403 Not authenticated")

    def test_reset_with_invalid_token_returns_403(self):
        """Invalid JWT token → 403."""
        resp = requests.post(
            f"{BASE_URL}/api/backups/org/{TEST_ORG_ID}/reset",
            headers={"Authorization": "Bearer invalid_token_xyz"},
            json={"confirmation": "test", "password": "test", "totp_code": "123456"}
        )
        assert resp.status_code in [401, 403], f"Expected 401/403, got {resp.status_code}"
        print(f"PASS: Reset with invalid token returns {resp.status_code}")


class TestResetEndpointValidation:
    """Test input validation for reset endpoint."""

    def test_wrong_confirmation_text_returns_400(self, auth_headers):
        """Wrong confirmation text → 400 with 'Confirmation must be exactly' message."""
        resp = requests.post(
            f"{BASE_URL}/api/backups/org/{TEST_ORG_ID}/reset",
            headers=auth_headers,
            json={
                "confirmation": "wrong text here",
                "password": "some_password",
                "totp_code": "123456"
            }
        )
        assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"
        data = resp.json()
        detail = data.get("detail", "")
        assert "confirmation must be exactly" in detail.lower(), \
            f"Expected 'Confirmation must be exactly', got: {detail}"
        # Check it includes the expected text
        assert EXPECTED_CONFIRMATION in detail, \
            f"Expected detail to contain '{EXPECTED_CONFIRMATION}', got: {detail}"
        print(f"PASS: Wrong confirmation returns 400: {detail}")

    def test_partial_confirmation_text_returns_400(self, auth_headers):
        """Partial match → still 400."""
        resp = requests.post(
            f"{BASE_URL}/api/backups/org/{TEST_ORG_ID}/reset",
            headers=auth_headers,
            json={
                "confirmation": TEST_ORG_NAME,  # Missing " Reset"
                "password": "some_password",
                "totp_code": "123456"
            }
        )
        assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"
        print("PASS: Partial confirmation text returns 400")

    def test_empty_confirmation_returns_400(self, auth_headers):
        """Empty confirmation → 400."""
        resp = requests.post(
            f"{BASE_URL}/api/backups/org/{TEST_ORG_ID}/reset",
            headers=auth_headers,
            json={
                "confirmation": "",
                "password": "some_password",
                "totp_code": "123456"
            }
        )
        assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"
        print(f"PASS: Empty confirmation returns 400")

    def test_wrong_password_returns_403(self, auth_headers):
        """Correct confirmation text but wrong password → 403 Incorrect password."""
        resp = requests.post(
            f"{BASE_URL}/api/backups/org/{TEST_ORG_ID}/reset",
            headers=auth_headers,
            json={
                "confirmation": EXPECTED_CONFIRMATION,
                "password": "completely_wrong_password_xyz",
                "totp_code": "123456"
            }
        )
        assert resp.status_code == 403, f"Expected 403, got {resp.status_code}: {resp.text}"
        data = resp.json()
        detail = data.get("detail", "")
        assert "incorrect password" in detail.lower(), \
            f"Expected 'Incorrect password', got: {detail}"
        print(f"PASS: Wrong password returns 403: {detail}")

    def test_missing_totp_returns_400(self, auth_headers):
        """Missing TOTP code → 400."""
        resp = requests.post(
            f"{BASE_URL}/api/backups/org/{TEST_ORG_ID}/reset",
            headers=auth_headers,
            json={
                "confirmation": EXPECTED_CONFIRMATION,
                "password": SUPER_ADMIN_PASSWORD,
                "totp_code": ""
            }
        )
        assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"
        print(f"PASS: Missing TOTP returns 400")

    def test_nonexistent_org_returns_404(self, auth_headers):
        """Nonexistent org_id → 404."""
        resp = requests.post(
            f"{BASE_URL}/api/backups/org/00000000-0000-0000-0000-000000000000/reset",
            headers=auth_headers,
            json={
                "confirmation": "Fake Org Reset",
                "password": "some_password",
                "totp_code": "123456"
            }
        )
        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"
        print(f"PASS: Nonexistent org returns 404")


# ── GET /api/backups/org/{org_id}/download/{filename} ────────────────────────

class TestDownloadEndpoint:
    """Test download endpoint authentication."""

    def test_download_without_auth_returns_403(self):
        """No auth → 403 Not authenticated."""
        resp = requests.get(
            f"{BASE_URL}/api/backups/org/{TEST_ORG_ID}/download/test_backup.json"
        )
        assert resp.status_code == 403, f"Expected 403, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert "not authenticated" in data.get("detail", "").lower(), \
            f"Expected 'Not authenticated', got: {data.get('detail')}"
        print("PASS: Download without auth returns 403 Not authenticated")

    def test_download_with_auth_requires_r2_config(self, auth_headers):
        """Auth provided but R2 not configured → 503 or 404 (not 403)."""
        resp = requests.get(
            f"{BASE_URL}/api/backups/org/{TEST_ORG_ID}/download/test_backup.json",
            headers=auth_headers
        )
        # Should not be 403 (that's auth failure), should be 503 (R2 not configured) or 500
        assert resp.status_code != 403, f"Should not be 403 with valid auth, got: {resp.status_code}"
        print(f"PASS: Download with auth returns {resp.status_code} (not 403 auth error)")

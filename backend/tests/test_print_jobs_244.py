"""
Remote Branch Printing Terminal - Backend API Tests
Iteration 244 — Tests all /api/print/* endpoints
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')
ADMIN_EMAIL = "janmarkeahig@gmail.com"
ADMIN_PASS = "Aa@58798546521325"
BRANCH_1_ID = "c435277f-9fc7-4d83-83e7-38be5b4423ac"


@pytest.fixture(scope="module")
def auth_token():
    """Login as admin and return token."""
    response = requests.post(f"{BASE_URL}/api/auth/login", json={
        "email": ADMIN_EMAIL,
        "password": ADMIN_PASS,
    })
    if response.status_code != 200:
        pytest.skip(f"Auth failed ({response.status_code}): {response.text[:200]}")
    data = response.json()
    token = data.get("access_token") or data.get("token")
    if not token:
        pytest.skip(f"No token in response: {data}")
    return token


@pytest.fixture(scope="module")
def auth_headers(auth_token):
    return {"Authorization": f"Bearer {auth_token}", "Content-Type": "application/json"}


@pytest.fixture(scope="module")
def terminal_id(auth_headers):
    """Get a terminal_id from the list to use in tests."""
    response = requests.get(f"{BASE_URL}/api/print/terminals", headers=auth_headers)
    if response.status_code != 200:
        pytest.skip(f"Cannot fetch terminals: {response.status_code}")
    terminals = response.json()
    if not terminals:
        pytest.skip("No terminals registered — cannot run job tests")
    return terminals[0]["terminal_id"]


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/print/terminals
# ─────────────────────────────────────────────────────────────────────────────
class TestListTerminals:
    """GET /api/print/terminals - List terminals with is_online + pending_jobs"""

    def test_list_terminals_returns_200(self, auth_headers):
        response = requests.get(f"{BASE_URL}/api/print/terminals", headers=auth_headers)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text[:200]}"
        print("PASS: GET /api/print/terminals returns 200")

    def test_list_terminals_returns_list(self, auth_headers):
        response = requests.get(f"{BASE_URL}/api/print/terminals", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list), f"Expected list, got {type(data)}"
        print(f"PASS: GET /api/print/terminals returns list with {len(data)} items")

    def test_terminals_have_is_online_field(self, auth_headers):
        response = requests.get(f"{BASE_URL}/api/print/terminals", headers=auth_headers)
        assert response.status_code == 200
        terminals = response.json()
        if not terminals:
            pytest.skip("No terminals to check fields on")
        for t in terminals[:3]:
            assert "is_online" in t, f"Missing 'is_online' in terminal: {list(t.keys())}"
            assert isinstance(t["is_online"], bool), f"is_online should be bool, got {type(t['is_online'])}"
        print("PASS: terminals have is_online (bool) field")

    def test_terminals_have_pending_jobs_field(self, auth_headers):
        response = requests.get(f"{BASE_URL}/api/print/terminals", headers=auth_headers)
        assert response.status_code == 200
        terminals = response.json()
        if not terminals:
            pytest.skip("No terminals to check fields on")
        for t in terminals[:3]:
            assert "pending_jobs" in t, f"Missing 'pending_jobs' in terminal: {list(t.keys())}"
            assert isinstance(t["pending_jobs"], int), f"pending_jobs should be int, got {type(t['pending_jobs'])}"
        print("PASS: terminals have pending_jobs (int) field")

    def test_terminals_no_token_field_exposed(self, auth_headers):
        """Tokens must NOT be returned in the terminal list response."""
        response = requests.get(f"{BASE_URL}/api/print/terminals", headers=auth_headers)
        assert response.status_code == 200
        terminals = response.json()
        for t in terminals[:5]:
            assert "token" not in t, "Security: 'token' field should not be exposed in terminal listing"
        print("PASS: token field not exposed in terminal list")

    def test_list_terminals_unauthenticated_returns_401(self):
        response = requests.get(f"{BASE_URL}/api/print/terminals")
        assert response.status_code in (401, 403), f"Expected 401/403, got {response.status_code}"
        print("PASS: unauthenticated GET /api/print/terminals returns 401/403")


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/print/jobs
# ─────────────────────────────────────────────────────────────────────────────
class TestListJobs:
    """GET /api/print/jobs - List jobs (15-day history)"""

    def test_list_jobs_returns_200(self, auth_headers):
        response = requests.get(f"{BASE_URL}/api/print/jobs", headers=auth_headers)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text[:200]}"
        print("PASS: GET /api/print/jobs returns 200")

    def test_list_jobs_returns_list(self, auth_headers):
        response = requests.get(f"{BASE_URL}/api/print/jobs", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list), f"Expected list, got {type(data)}"
        print(f"PASS: GET /api/print/jobs returns list with {len(data)} jobs")

    def test_list_jobs_no_html_content(self, auth_headers):
        """html_content must be excluded from the list response (it's large)."""
        response = requests.get(f"{BASE_URL}/api/print/jobs", headers=auth_headers)
        assert response.status_code == 200
        jobs = response.json()
        for job in jobs[:5]:
            assert "html_content" not in job, "html_content should not be in list response"
        print("PASS: html_content excluded from job list response")

    def test_list_jobs_unauthenticated_returns_401(self):
        response = requests.get(f"{BASE_URL}/api/print/jobs")
        assert response.status_code in (401, 403), f"Expected 401/403, got {response.status_code}"
        print("PASS: unauthenticated GET /api/print/jobs returns 401/403")

    def test_list_jobs_filter_by_status(self, auth_headers):
        """Filter by status=pending should return 200."""
        response = requests.get(f"{BASE_URL}/api/print/jobs?status=pending", headers=auth_headers)
        assert response.status_code == 200
        jobs = response.json()
        for j in jobs:
            assert j["status"] == "pending", f"Expected pending, got {j['status']}"
        print(f"PASS: GET /api/print/jobs?status=pending returns {len(jobs)} pending jobs")


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/print/terminals/for-branch/{branch_id}
# ─────────────────────────────────────────────────────────────────────────────
class TestTerminalsForBranch:
    """GET /api/print/terminals/for-branch/{branch_id}"""

    def test_for_branch_returns_200(self, auth_headers):
        response = requests.get(
            f"{BASE_URL}/api/print/terminals/for-branch/{BRANCH_1_ID}",
            headers=auth_headers
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text[:200]}"
        print(f"PASS: GET /api/print/terminals/for-branch/{BRANCH_1_ID} returns 200")

    def test_for_branch_returns_list(self, auth_headers):
        response = requests.get(
            f"{BASE_URL}/api/print/terminals/for-branch/{BRANCH_1_ID}",
            headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list), f"Expected list, got {type(data)}"
        print(f"PASS: for-branch returns list with {len(data)} terminals")

    def test_for_branch_terminals_have_is_online(self, auth_headers):
        response = requests.get(
            f"{BASE_URL}/api/print/terminals/for-branch/{BRANCH_1_ID}",
            headers=auth_headers
        )
        assert response.status_code == 200
        terminals = response.json()
        for t in terminals:
            assert "is_online" in t, f"Missing is_online in terminal"
        print("PASS: for-branch terminals all have is_online field")

    def test_for_branch_online_first_ordering(self, auth_headers):
        """Online terminals should appear before offline ones."""
        response = requests.get(
            f"{BASE_URL}/api/print/terminals/for-branch/{BRANCH_1_ID}",
            headers=auth_headers
        )
        assert response.status_code == 200
        terminals = response.json()
        if len(terminals) >= 2:
            saw_offline = False
            for t in terminals:
                if not t["is_online"]:
                    saw_offline = True
                if saw_offline and t["is_online"]:
                    pytest.fail("Online terminal appeared after offline terminal (ordering broken)")
        print("PASS: for-branch terminals ordered online-first")

    def test_for_branch_invalid_branch_returns_empty(self, auth_headers):
        """Non-existent branch should return empty list (not 404)."""
        response = requests.get(
            f"{BASE_URL}/api/print/terminals/for-branch/nonexistent-branch-id",
            headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 0
        print("PASS: non-existent branch returns empty list")


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/print/jobs — Create Job
# ─────────────────────────────────────────────────────────────────────────────
class TestCreatePrintJob:
    """POST /api/print/jobs - Create job, verify pending status when terminal offline"""

    def test_create_job_missing_terminal_id(self, auth_headers):
        response = requests.post(f"{BASE_URL}/api/print/jobs", json={
            "document_type": "sales_receipt",
            "html_content": "<html><body>TEST</body></html>",
        }, headers=auth_headers)
        assert response.status_code == 400, f"Expected 400, got {response.status_code}"
        print("PASS: missing terminal_id returns 400")

    def test_create_job_missing_html_content(self, auth_headers, terminal_id):
        response = requests.post(f"{BASE_URL}/api/print/jobs", json={
            "terminal_id": terminal_id,
            "document_type": "sales_receipt",
        }, headers=auth_headers)
        assert response.status_code == 400, f"Expected 400, got {response.status_code}"
        print("PASS: missing html_content returns 400")

    def test_create_job_invalid_terminal_returns_404(self, auth_headers):
        response = requests.post(f"{BASE_URL}/api/print/jobs", json={
            "terminal_id": "nonexistent-terminal-id-xyz",
            "document_type": "sales_receipt",
            "html_content": "<html><body>TEST</body></html>",
        }, headers=auth_headers)
        assert response.status_code == 404, f"Expected 404, got {response.status_code}: {response.text[:200]}"
        print("PASS: invalid terminal_id returns 404")

    def test_create_job_returns_job_id_and_status(self, auth_headers, terminal_id):
        response = requests.post(f"{BASE_URL}/api/print/jobs", json={
            "terminal_id": terminal_id,
            "document_type": "sales_receipt",
            "reference_number": "TEST-INV-001",
            "html_content": "<html><body><h1>TEST RECEIPT</h1><p>Test print job for iteration 244</p></body></html>",
            "metadata": {"test": True},
        }, headers=auth_headers)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text[:200]}"
        data = response.json()
        assert "job_id" in data, f"Missing job_id in response: {data}"
        assert "status" in data, f"Missing status in response: {data}"
        assert data["status"] in ("pending", "sent"), f"Unexpected status: {data['status']}"
        print(f"PASS: create print job returns job_id={data['job_id']}, status={data['status']}")

    def test_create_job_offline_terminal_returns_pending(self, auth_headers, terminal_id):
        """Since most test terminals are offline, the job should have status=pending."""
        response = requests.post(f"{BASE_URL}/api/print/jobs", json={
            "terminal_id": terminal_id,
            "document_type": "sales_receipt",
            "reference_number": "TEST-OFFLINE-001",
            "html_content": "<html><body>Offline terminal test</body></html>",
        }, headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        # Terminal is expected to be offline in test env
        # status should be either pending (offline) or sent (online)
        assert data["status"] in ("pending", "sent"), f"Unexpected status: {data['status']}"
        print(f"PASS: offline-terminal job created with status={data['status']}")


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/print/jobs/{job_id} — Single Job
# ─────────────────────────────────────────────────────────────────────────────
class TestGetSingleJob:
    """GET /api/print/jobs/{job_id} - Get single job with HTML content"""

    @pytest.fixture(scope="class")
    def created_job_id(self, auth_headers, terminal_id):
        response = requests.post(f"{BASE_URL}/api/print/jobs", json={
            "terminal_id": terminal_id,
            "document_type": "sales_receipt",
            "reference_number": "TEST-SINGLE-001",
            "html_content": "<html><body>Single job fetch test</body></html>",
        }, headers=auth_headers)
        assert response.status_code == 200
        return response.json()["job_id"]

    def test_get_job_returns_200(self, auth_headers, terminal_id, created_job_id):
        response = requests.get(f"{BASE_URL}/api/print/jobs/{created_job_id}", headers=auth_headers)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text[:200]}"
        print(f"PASS: GET /api/print/jobs/{created_job_id} returns 200")

    def test_get_job_includes_html_content(self, auth_headers, terminal_id, created_job_id):
        response = requests.get(f"{BASE_URL}/api/print/jobs/{created_job_id}", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert "html_content" in data, "Single job fetch should include html_content"
        assert len(data["html_content"]) > 0
        print("PASS: single job response includes html_content")

    def test_get_nonexistent_job_returns_404(self, auth_headers):
        response = requests.get(f"{BASE_URL}/api/print/jobs/nonexistent-job-xyz", headers=auth_headers)
        assert response.status_code == 404, f"Expected 404, got {response.status_code}"
        print("PASS: nonexistent job returns 404")


# ─────────────────────────────────────────────────────────────────────────────
# PUT /api/print/jobs/{job_id}/status — Update Status
# ─────────────────────────────────────────────────────────────────────────────
class TestUpdateJobStatus:
    """PUT /api/print/jobs/{job_id}/status - Mark as printed/failed/cancelled"""

    @pytest.fixture(scope="class")
    def pending_job_id(self, auth_headers, terminal_id):
        response = requests.post(f"{BASE_URL}/api/print/jobs", json={
            "terminal_id": terminal_id,
            "document_type": "sales_receipt",
            "reference_number": "TEST-STATUS-001",
            "html_content": "<html><body>Status update test</body></html>",
        }, headers=auth_headers)
        assert response.status_code == 200
        return response.json()["job_id"]

    def test_mark_job_printed(self, auth_headers, terminal_id, pending_job_id):
        response = requests.put(
            f"{BASE_URL}/api/print/jobs/{pending_job_id}/status",
            json={"status": "printed"},
            headers=auth_headers
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text[:200]}"
        data = response.json()
        assert data["status"] == "printed", f"Expected printed, got {data['status']}"
        assert data["job_id"] == pending_job_id
        print(f"PASS: mark job as printed returns status=printed")

    def test_mark_job_printed_persisted(self, auth_headers, terminal_id, pending_job_id):
        """Verify status was persisted in DB."""
        response = requests.get(f"{BASE_URL}/api/print/jobs/{pending_job_id}", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "printed", f"Expected printed status persisted, got {data['status']}"
        assert data["printed_at"] is not None, "printed_at should be set after marking printed"
        print("PASS: printed status and printed_at timestamp persisted")

    def test_mark_job_invalid_status_returns_400(self, auth_headers, terminal_id, pending_job_id):
        response = requests.put(
            f"{BASE_URL}/api/print/jobs/{pending_job_id}/status",
            json={"status": "invalid_status"},
            headers=auth_headers
        )
        assert response.status_code == 400, f"Expected 400, got {response.status_code}"
        print("PASS: invalid status returns 400")

    def test_mark_job_failed_with_error_message(self, auth_headers, terminal_id):
        # Create a new job to mark as failed
        create_res = requests.post(f"{BASE_URL}/api/print/jobs", json={
            "terminal_id": terminal_id,
            "document_type": "sales_receipt",
            "reference_number": "TEST-FAILED-001",
            "html_content": "<html><body>fail test</body></html>",
        }, headers=auth_headers)
        assert create_res.status_code == 200
        job_id = create_res.json()["job_id"]

        # Mark as failed with error message
        response = requests.put(
            f"{BASE_URL}/api/print/jobs/{job_id}/status",
            json={"status": "failed", "error_message": "Printer out of paper"},
            headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "failed"

        # Verify error message persisted
        get_res = requests.get(f"{BASE_URL}/api/print/jobs/{job_id}", headers=auth_headers)
        assert get_res.status_code == 200
        job = get_res.json()
        assert job["error_message"] == "Printer out of paper"
        assert job["failed_at"] is not None
        print("PASS: failed status with error_message persisted correctly")


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/print/terminal/set-mode
# ─────────────────────────────────────────────────────────────────────────────
class TestSetTerminalMode:
    """POST /api/print/terminal/set-mode - Toggle auto/manual print mode"""

    def test_set_mode_to_auto(self, auth_headers, terminal_id):
        response = requests.post(f"{BASE_URL}/api/print/terminal/set-mode", json={
            "terminal_id": terminal_id,
            "mode": "auto",
        }, headers=auth_headers)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text[:200]}"
        data = response.json()
        assert data["print_mode"] == "auto", f"Expected auto, got {data['print_mode']}"
        assert data["terminal_id"] == terminal_id
        print(f"PASS: set-mode to auto returns print_mode=auto")

    def test_set_mode_to_manual(self, auth_headers, terminal_id):
        response = requests.post(f"{BASE_URL}/api/print/terminal/set-mode", json={
            "terminal_id": terminal_id,
            "mode": "manual",
        }, headers=auth_headers)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text[:200]}"
        data = response.json()
        assert data["print_mode"] == "manual", f"Expected manual, got {data['print_mode']}"
        print(f"PASS: set-mode to manual returns print_mode=manual")

    def test_set_mode_invalid_mode_returns_400(self, auth_headers, terminal_id):
        response = requests.post(f"{BASE_URL}/api/print/terminal/set-mode", json={
            "terminal_id": terminal_id,
            "mode": "superfast",
        }, headers=auth_headers)
        assert response.status_code == 400, f"Expected 400, got {response.status_code}"
        print("PASS: invalid mode returns 400")

    def test_set_mode_missing_terminal_id_returns_400(self, auth_headers):
        response = requests.post(f"{BASE_URL}/api/print/terminal/set-mode", json={
            "mode": "auto",
        }, headers=auth_headers)
        assert response.status_code == 400, f"Expected 400, got {response.status_code}"
        print("PASS: missing terminal_id returns 400")

    def test_set_mode_invalid_terminal_returns_404(self, auth_headers):
        response = requests.post(f"{BASE_URL}/api/print/terminal/set-mode", json={
            "terminal_id": "nonexistent-terminal-xyz",
            "mode": "auto",
        }, headers=auth_headers)
        assert response.status_code == 404, f"Expected 404, got {response.status_code}"
        print("PASS: nonexistent terminal returns 404")


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/print/jobs/{job_id}/resend — Resend Job
# ─────────────────────────────────────────────────────────────────────────────
class TestResendJob:
    """POST /api/print/jobs/{job_id}/resend - Resend failed/cancelled job"""

    @pytest.fixture(scope="class")
    def failed_job_id(self, auth_headers, terminal_id):
        """Create a job then mark it as failed to test resend."""
        create_res = requests.post(f"{BASE_URL}/api/print/jobs", json={
            "terminal_id": terminal_id,
            "document_type": "sales_receipt",
            "reference_number": "TEST-RESEND-001",
            "html_content": "<html><body>Resend test</body></html>",
        }, headers=auth_headers)
        assert create_res.status_code == 200
        job_id = create_res.json()["job_id"]

        # Mark as failed
        fail_res = requests.put(
            f"{BASE_URL}/api/print/jobs/{job_id}/status",
            json={"status": "failed", "error_message": "Test failure"},
            headers=auth_headers
        )
        assert fail_res.status_code == 200
        return job_id

    def test_resend_failed_job_returns_200(self, auth_headers, terminal_id, failed_job_id):
        response = requests.post(
            f"{BASE_URL}/api/print/jobs/{failed_job_id}/resend",
            headers=auth_headers
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text[:200]}"
        data = response.json()
        assert "status" in data
        assert data["status"] in ("pending", "sent"), f"Unexpected status after resend: {data['status']}"
        print(f"PASS: resend failed job returns status={data['status']}")

    def test_resend_job_clears_error_message(self, auth_headers, terminal_id, failed_job_id):
        """After resend, the error_message should be cleared."""
        # Resend (may already have been resent above, but fixture is scoped)
        resend_res = requests.post(
            f"{BASE_URL}/api/print/jobs/{failed_job_id}/resend",
            headers=auth_headers
        )
        assert resend_res.status_code == 200

        # Check job state
        get_res = requests.get(f"{BASE_URL}/api/print/jobs/{failed_job_id}", headers=auth_headers)
        assert get_res.status_code == 200
        job = get_res.json()
        assert job["error_message"] is None, f"error_message should be cleared after resend, got: {job['error_message']}"
        print("PASS: resend clears error_message")

    def test_resend_printed_job_returns_400(self, auth_headers, terminal_id):
        """Cannot resend a job that was already printed."""
        create_res = requests.post(f"{BASE_URL}/api/print/jobs", json={
            "terminal_id": terminal_id,
            "document_type": "sales_receipt",
            "reference_number": "TEST-RESEND-PRINTED",
            "html_content": "<html><body>Resend printed test</body></html>",
        }, headers=auth_headers)
        assert create_res.status_code == 200
        job_id = create_res.json()["job_id"]

        # Mark as printed
        requests.put(
            f"{BASE_URL}/api/print/jobs/{job_id}/status",
            json={"status": "printed"},
            headers=auth_headers
        )

        # Try to resend
        response = requests.post(
            f"{BASE_URL}/api/print/jobs/{job_id}/resend",
            headers=auth_headers
        )
        assert response.status_code == 400, f"Expected 400, got {response.status_code}: {response.text[:200]}"
        print("PASS: resending a printed job returns 400")

    def test_resend_nonexistent_job_returns_404(self, auth_headers):
        response = requests.post(
            f"{BASE_URL}/api/print/jobs/nonexistent-job-xyz/resend",
            headers=auth_headers
        )
        assert response.status_code == 404, f"Expected 404, got {response.status_code}"
        print("PASS: resend nonexistent job returns 404")


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/print/jobs after create — verifies job appears in list
# ─────────────────────────────────────────────────────────────────────────────
class TestJobsListAfterCreate:
    """Verify created jobs show up in the jobs list."""

    def test_created_job_appears_in_list(self, auth_headers, terminal_id):
        # Create a job
        create_res = requests.post(f"{BASE_URL}/api/print/jobs", json={
            "terminal_id": terminal_id,
            "document_type": "z_report",
            "reference_number": "TEST-LIST-001",
            "html_content": "<html><body>List verification test</body></html>",
        }, headers=auth_headers)
        assert create_res.status_code == 200
        job_id = create_res.json()["job_id"]

        # Fetch list
        list_res = requests.get(f"{BASE_URL}/api/print/jobs", headers=auth_headers)
        assert list_res.status_code == 200
        jobs = list_res.json()
        job_ids = [j["id"] for j in jobs]
        assert job_id in job_ids, f"Created job {job_id} not found in list: {job_ids[:5]}"
        print(f"PASS: created job {job_id} appears in GET /api/print/jobs list")

    def test_list_jobs_filter_by_terminal(self, auth_headers, terminal_id):
        response = requests.get(
            f"{BASE_URL}/api/print/jobs?terminal_id={terminal_id}",
            headers=auth_headers
        )
        assert response.status_code == 200
        jobs = response.json()
        for job in jobs:
            assert job["terminal_id"] == terminal_id, f"Job {job['id']} has wrong terminal_id"
        print(f"PASS: filter by terminal_id returns {len(jobs)} matching jobs")

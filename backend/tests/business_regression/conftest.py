"""
conftest for business_regression — fresh tenant per module + JSON report.

Why module-scoped (not function-scoped):
  Each business-regression file is a single end-to-end story. The
  reconciliation snapshot test only makes sense if every step in the file
  shares the same tenant. Per-test isolation would make BR pointless.

Why module-scoped (not session-scoped):
  Sharing across files would re-introduce the cross-file DB pollution
  problem the Phase 5 handoff explicitly flags (`asyncio_default_fixture_loop_scope = session`
  + hardcoded ids). Module scope is the sweet spot: each file gets its
  own clean tenant, and cleanup runs immediately after the file finishes.

What it provides to every test in the folder:
  * `tenant` fixture — the dict returned by `make_business_day_tenant()`
  * `record_result(scenario, step, expected, actual, evidence)` fixture
      — appends a structured row to a per-session report.
  * On session teardown, dumps the collected rows as JSON to
      `/app/test_reports/business_regression_<ts>.json`.
"""
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
import pytest_asyncio

BACKEND = os.path.join(os.path.dirname(__file__), "..", "..")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from tests.business_regression._fixtures import (  # noqa: E402
    make_business_day_tenant, cleanup_business_tenant,
)
from config import set_org_context  # noqa: E402


# ─────────────────────────────────────────────────────────────────────
# Per-module tenant.
# ─────────────────────────────────────────────────────────────────────
@pytest_asyncio.fixture(scope="module")
async def tenant():
    """Build a fresh business-day tenant once per file; tear it down
    at file teardown."""
    t = await make_business_day_tenant()
    # IMPORTANT — set_org_context here only seeds the very first call.
    # Most route handlers re-read context from JWT (`user["organization_id"]`),
    # so this is a safety net for any helper that goes through the
    # tenant proxy directly.
    set_org_context(t["org_id"])
    try:
        yield t
    finally:
        # Always clean up by org_id, even on failure. This is the line
        # that keeps the shared test DB sane across CI runs.
        await cleanup_business_tenant(t["org_id"])


# ─────────────────────────────────────────────────────────────────────
# Structured result collector.
#
# Tests call `record_result(...)` to append one row of expected-vs-actual
# evidence. At session end, we dump every collected row to a single JSON
# report. This is intentionally lightweight: we do NOT replace pytest's
# normal pass/fail reporting — we ADD a business-readable trace next
# to it.
# ─────────────────────────────────────────────────────────────────────
_REPORT_ROWS: list[dict] = []


@pytest.fixture
def record_result():
    """Yields a callable `record(scenario, step, expected, actual, evidence=None)`.

    `expected` and `actual` should be small JSON-serialisable dicts.
    `evidence` is an optional dict carrying invoice_id, branch_id,
    product_id, customer_id, starting_qty, etc.
    """
    def _record(scenario, step, expected, actual, evidence=None):
        status = "PASS" if expected == actual else "FAIL"
        _REPORT_ROWS.append({
            "scenario":  scenario,
            "step":      step,
            "expected":  expected,
            "actual":    actual,
            "status":    status,
            "evidence":  evidence or {},
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        })
        return status
    return _record


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Dump the structured BR report to disk after the test session.

    Writes TWO files:
      * Timestamped:  business_regression_<ts>.json  — historical record.
      * Stable:       business_regression_latest.json — convenience pointer
        for CI / dashboards that always want the most recent run.
    """
    if not _REPORT_ROWS:
        return
    out_dir = Path("/app/test_reports")
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / f"business_regression_{ts}.json"
    latest_path = out_dir / "business_regression_latest.json"
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pytest_exitstatus": exitstatus,
        "row_count": len(_REPORT_ROWS),
        "pass_count": sum(1 for r in _REPORT_ROWS if r["status"] == "PASS"),
        "fail_count": sum(1 for r in _REPORT_ROWS if r["status"] == "FAIL"),
        "rows": _REPORT_ROWS,
    }
    payload = json.dumps(summary, indent=2, default=str)
    out_path.write_text(payload)
    latest_path.write_text(payload)
    terminalreporter.write_sep("=", "business_regression structured report")
    terminalreporter.write_line(
        f"  rows={summary['row_count']} pass={summary['pass_count']} "
        f"fail={summary['fail_count']}"
    )
    terminalreporter.write_line(f"  timestamped → {out_path}")
    terminalreporter.write_line(f"  latest      → {latest_path}")

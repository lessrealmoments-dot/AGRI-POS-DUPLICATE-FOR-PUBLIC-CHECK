"""
Backend regression — GET /api/audit/pulse (Audit Pulse widget endpoint)

Covers the new thin-wrapper endpoint that projects compute_audit() into a
compact dashboard payload.

Asserts:
- Auth required (401 without bearer)
- Shape of response (all documented keys present)
- Score ranges and labels
- top_risk_factors: only >0 points, sorted DESC, <=3 items, each has
  {factor,value,points,max}
- Period window math for days=7 / 30 / 90
- branch_id scoping
- Sanity: health_score + fraud_risk_score match /api/audit/compute for the
  same period/branch (the widget must never show numbers that disagree with
  the Audit Center).
"""
import os
import datetime as dt
import pytest
import requests

import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _org_test_helpers import ensure_org_admin_token  # noqa: E402

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    try:
        with open(os.path.join(os.path.dirname(__file__), "..", "..", "frontend", ".env")) as _f:
            for _line in _f:
                if _line.startswith("REACT_APP_BACKEND_URL="):
                    BASE_URL = _line.split("=", 1)[1].strip().rstrip("/")
                    break
    except Exception:
        pass
API = f"{BASE_URL}/api"


@pytest.fixture(scope="module")
def org_admin():
    token, user = ensure_org_admin_token()
    return token, user


@pytest.fixture(scope="module")
def headers(org_admin):
    token, _ = org_admin
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


@pytest.fixture(scope="module")
def branch_id(headers):
    r = requests.get(f"{API}/branches", headers=headers, timeout=15)
    assert r.status_code == 200, f"branches fetch failed: {r.text}"
    branches = r.json()
    assert len(branches) > 0, "no branches available"
    return branches[0]["id"]


# ── Auth ────────────────────────────────────────────────────────────────────
class TestAuth:
    def test_pulse_requires_auth(self):
        r = requests.get(f"{API}/audit/pulse", timeout=10)
        assert r.status_code in (401, 403), f"expected 401/403, got {r.status_code}"


# ── Payload shape + score ranges ────────────────────────────────────────────
class TestPulsePayload:
    def test_pulse_default_days_30_shape(self, headers):
        r = requests.get(f"{API}/audit/pulse", headers=headers, timeout=60)
        assert r.status_code == 200, f"pulse failed: {r.status_code} {r.text}"
        d = r.json()

        # Top-level keys
        for k in [
            "period_from", "period_to", "days", "health_score", "health_label",
            "fraud_risk_score", "fraud_risk_label", "top_risk_factors", "kpis",
            "computed_at",
        ]:
            assert k in d, f"missing key '{k}'"

        # Period math: days default = 30, period_to = today (UTC)
        today = dt.datetime.utcnow().date()
        assert d["period_to"] == today.strftime("%Y-%m-%d")
        expected_from = (today - dt.timedelta(days=29)).strftime("%Y-%m-%d")
        assert d["period_from"] == expected_from, (
            f"expected {expected_from}, got {d['period_from']}"
        )
        assert d["days"] == 30

        # Scores in valid range + non-empty labels
        hs = d["health_score"]
        fs = d["fraud_risk_score"]
        assert isinstance(hs, (int, float)) and 0 <= hs <= 100
        assert isinstance(fs, (int, float)) and 0 <= fs <= 100
        assert d["health_label"] in ("Excellent", "Good", "Needs Review", "Poor")
        assert d["fraud_risk_label"] in ("Low", "Elevated", "High", "Critical")

    def test_pulse_kpis_block(self, headers):
        r = requests.get(f"{API}/audit/pulse", headers=headers, timeout=60)
        assert r.status_code == 200
        kpis = r.json()["kpis"]
        for k in [
            "revenue", "gross_margin_pct", "void_rate_pct", "discount_rate_pct",
            "dso_days", "dpo_days", "inventory_turnover", "total_txns",
            "voided_count", "trend",
        ]:
            assert k in kpis, f"kpis missing '{k}'"
        # trend is an object (may be None when no prior period) — if present, dict
        if kpis["trend"] is not None:
            assert isinstance(kpis["trend"], dict)

    def test_top_risk_factors_rules(self, headers):
        r = requests.get(f"{API}/audit/pulse", headers=headers, timeout=60)
        assert r.status_code == 200
        factors = r.json()["top_risk_factors"]
        assert isinstance(factors, list)
        assert len(factors) <= 3, f"top_risk_factors must be <=3, got {len(factors)}"
        prev_points = None
        for f in factors:
            assert "factor" in f and "value" in f and "points" in f and "max" in f
            assert (f.get("points") or 0) > 0, "only >0 point factors allowed"
            assert f.get("max") and f["max"] > 0
            if prev_points is not None:
                assert f["points"] <= prev_points, "not sorted points DESC"
            prev_points = f["points"]


# ── Days window ─────────────────────────────────────────────────────────────
class TestDaysWindow:
    @pytest.mark.parametrize("days,expected_delta", [(7, 6), (30, 29), (90, 89)])
    def test_days_param(self, headers, days, expected_delta):
        r = requests.get(
            f"{API}/audit/pulse", params={"days": days}, headers=headers, timeout=60
        )
        assert r.status_code == 200, f"days={days} failed: {r.text}"
        d = r.json()
        assert d["days"] == days
        today = dt.datetime.utcnow().date()
        assert d["period_to"] == today.strftime("%Y-%m-%d")
        expected_from = (today - dt.timedelta(days=expected_delta)).strftime("%Y-%m-%d")
        assert d["period_from"] == expected_from


# ── Branch scoping ──────────────────────────────────────────────────────────
class TestBranchScope:
    def test_branch_id_scopes_response(self, headers, branch_id):
        r = requests.get(
            f"{API}/audit/pulse",
            params={"branch_id": branch_id, "days": 30},
            headers=headers,
            timeout=60,
        )
        assert r.status_code == 200, f"branch-scoped pulse failed: {r.text}"
        d = r.json()
        assert d["branch_id"] == branch_id


# ── Consistency with /api/audit/compute ─────────────────────────────────────
class TestConsistencyWithCompute:
    def test_scores_match_compute(self, headers, branch_id):
        today = dt.datetime.utcnow().date()
        pf = (today - dt.timedelta(days=29)).strftime("%Y-%m-%d")
        pt = today.strftime("%Y-%m-%d")

        # Pulse
        r1 = requests.get(
            f"{API}/audit/pulse",
            params={"branch_id": branch_id, "days": 30},
            headers=headers,
            timeout=60,
        )
        assert r1.status_code == 200
        pulse = r1.json()

        # Compute (same period, partial type — matches pulse internals)
        r2 = requests.get(
            f"{API}/audit/compute",
            params={
                "branch_id": branch_id,
                "period_from": pf,
                "period_to": pt,
                "audit_type": "partial",
            },
            headers=headers,
            timeout=90,
        )
        assert r2.status_code == 200, f"compute failed: {r2.text}"
        compute = r2.json()

        scores = compute.get("scores") or {}
        assert pulse["health_score"] == scores.get("health_score"), (
            f"health_score mismatch: pulse={pulse['health_score']} compute={scores.get('health_score')}"
        )
        assert pulse["fraud_risk_score"] == scores.get("fraud_risk_score"), (
            f"fraud_risk_score mismatch: pulse={pulse['fraud_risk_score']} compute={scores.get('fraud_risk_score')}"
        )
        assert pulse["health_label"] == scores.get("health_label")
        assert pulse["fraud_risk_label"] == scores.get("fraud_risk_label")

        # Top risk factors must be a subset (same items, same order by points) of compute.risk_breakdown >0
        rb = [x for x in (scores.get("risk_breakdown") or []) if (x.get("points") or 0) > 0]
        rb.sort(key=lambda x: x.get("points", 0), reverse=True)
        rb = rb[:3]
        assert pulse["top_risk_factors"] == rb, "top_risk_factors must equal sorted/capped risk_breakdown>0"

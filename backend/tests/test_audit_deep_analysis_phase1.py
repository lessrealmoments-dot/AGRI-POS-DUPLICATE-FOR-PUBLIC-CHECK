"""
Phase 1 Deep Analysis — /api/audit/compute new fields regression tests.

Covers:
- kpis ratio block (revenue, cogs, gross_margin_pct, void_rate_pct, edit_rate_pct,
  discount_rate_pct, dso_days, dpo_days, inventory_turnover, payment_mix_pct)
- kpis_prev (previous equal-length period)
- kpis.trend deltas
- scores.health_score / health_label
- scores.fraud_risk_score / fraud_risk_label
- scores.risk_breakdown (7 factors)
- scores.section_scores weighted (inventory redistribution when absent)
- Regression: pre-existing sections still present
- Regression: /api/audit/sessions CRUD still works

Uses regression org admin (auto-seeded).
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
    # Fallback: read from frontend/.env so pytest invoked without env still works
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


@pytest.fixture(scope="module")
def compute_response(headers, branch_id):
    """Compute for current month."""
    today = dt.date.today()
    pf = today.replace(day=1).strftime("%Y-%m-%d")
    pt = today.strftime("%Y-%m-%d")
    r = requests.get(
        f"{API}/audit/compute",
        params={
            "branch_id": branch_id,
            "period_from": pf,
            "period_to": pt,
            "audit_type": "partial",
        },
        headers=headers,
        timeout=60,
    )
    assert r.status_code == 200, f"compute failed: {r.status_code} {r.text[:500]}"
    return r.json()


# ── KPI ribbon ────────────────────────────────────────────────────────────
class TestKpiRibbon:
    def test_kpis_block_present(self, compute_response):
        assert "kpis" in compute_response
        assert isinstance(compute_response["kpis"], dict)

    def test_kpis_core_numeric_fields(self, compute_response):
        kpis = compute_response["kpis"]
        numeric_fields = [
            "revenue", "cogs", "gross_profit", "gross_margin_pct",
            "void_rate_pct", "edit_rate_pct", "discount_rate_pct",
            "dso_days", "dpo_days", "inventory_turnover",
            "total_txns", "voided_count", "total_ar", "total_ap",
            "inventory_value", "days_in_period",
        ]
        for f in numeric_fields:
            assert f in kpis, f"kpis missing field: {f}"
            assert isinstance(kpis[f], (int, float)), f"kpis.{f} not numeric: {kpis[f]!r}"

    def test_kpis_payment_mix_structure(self, compute_response):
        pm = compute_response["kpis"].get("payment_mix_pct")
        assert isinstance(pm, dict)
        # expected mix keys
        for k in ["cash", "credit", "partial", "digital", "split"]:
            assert k in pm, f"payment_mix_pct missing {k}"
            assert isinstance(pm[k], (int, float))
        # should approximately sum to 100 (or 0 if no txns)
        total = sum(pm.values())
        assert 0 <= total <= 100.5, f"payment_mix_pct sum out of range: {total}"

    def test_kpis_gross_margin_matches_revenue_cogs(self, compute_response):
        k = compute_response["kpis"]
        if k["revenue"] > 0:
            expected = round((k["revenue"] - k["cogs"]) / k["revenue"] * 100, 2)
            # allow 0.5 abs tolerance for rounding across code path
            assert abs(k["gross_margin_pct"] - expected) <= 0.5

    def test_kpis_void_rate_matches(self, compute_response):
        k = compute_response["kpis"]
        if k["total_txns"] > 0:
            expected = round(k["voided_count"] / k["total_txns"] * 100, 2)
            assert abs(k["void_rate_pct"] - expected) <= 0.5


# ── Previous-period + trend ───────────────────────────────────────────────
class TestKpisPrevAndTrend:
    def test_kpis_prev_present(self, compute_response):
        assert "kpis_prev" in compute_response
        # may be None on rare error path; when present must be dict
        prev = compute_response["kpis_prev"]
        assert prev is None or isinstance(prev, dict)

    def test_kpis_trend_deltas(self, compute_response):
        trend = compute_response["kpis"].get("trend")
        assert isinstance(trend, dict), "kpis.trend missing"
        # expected delta keys
        expected_keys = [
            "revenue", "gross_margin_pct", "void_rate_pct",
            "discount_rate_pct", "dso_days", "dpo_days", "inventory_turnover",
        ]
        for k in expected_keys:
            assert k in trend, f"trend missing {k}"
            assert isinstance(trend[k], (int, float))


# ── Scores: health + fraud risk ──────────────────────────────────────────
class TestScores:
    def test_scores_block_present(self, compute_response):
        assert "scores" in compute_response
        s = compute_response["scores"]
        assert isinstance(s, dict)

    def test_health_score_range_and_label(self, compute_response):
        s = compute_response["scores"]
        assert "health_score" in s
        hs = s["health_score"]
        assert isinstance(hs, int)
        assert 0 <= hs <= 100
        assert s.get("health_label") in {"Excellent", "Good", "Needs Review", "Poor"}

    def test_fraud_risk_score_range_and_label(self, compute_response):
        s = compute_response["scores"]
        assert "fraud_risk_score" in s
        fr = s["fraud_risk_score"]
        assert isinstance(fr, int)
        assert 0 <= fr <= 100
        assert s.get("fraud_risk_label") in {"Low", "Elevated", "High", "Critical"}

    def test_risk_breakdown_structure(self, compute_response):
        rb = compute_response["scores"].get("risk_breakdown")
        assert isinstance(rb, list)
        # Required 7 factors
        factor_names = {e.get("factor") for e in rb}
        expected = {
            "Void rate", "Discount rate", "Invoice edit rate",
            "Off-hours transactions", "Inventory corrections",
            "PIN brute-force alerts", "Price-match volume",
        }
        missing = expected - factor_names
        assert not missing, f"risk_breakdown missing factors: {missing}"
        # each entry has the 4 keys
        for e in rb:
            assert {"factor", "value", "points", "max"} <= set(e.keys())
            assert isinstance(e["max"], (int, float))
            assert isinstance(e["points"], (int, float))
            assert 0 <= e["points"] <= e["max"] + 0.001

    def test_risk_breakdown_caps(self, compute_response):
        """Fraud-risk cap enforcement per factor."""
        caps = {
            "Void rate": 25, "Discount rate": 15, "Invoice edit rate": 15,
            "Off-hours transactions": 10, "Inventory corrections": 10,
            "PIN brute-force alerts": 15, "Price-match volume": 10,
        }
        rb = compute_response["scores"]["risk_breakdown"]
        for e in rb:
            assert e["max"] == caps[e["factor"]], (
                f"{e['factor']} cap mismatch: got {e['max']} expected {caps[e['factor']]}"
            )
        # Sum of max should be exactly 100
        assert sum(e["max"] for e in rb) == 100

    def test_fraud_risk_score_matches_breakdown(self, compute_response):
        s = compute_response["scores"]
        points_sum = sum(e["points"] for e in s["risk_breakdown"])
        # capped at 100
        expected = min(int(round(points_sum)), 100)
        assert abs(s["fraud_risk_score"] - expected) <= 1, (
            f"fraud_risk_score {s['fraud_risk_score']} vs sum {points_sum}"
        )

    def test_section_scores_weighted(self, compute_response):
        ss = compute_response["scores"].get("section_scores")
        assert isinstance(ss, dict)
        # Should include major sections (inventory may be absent on partial)
        required = {"cash", "ar", "payables", "sales", "returns",
                    "transfers", "unverified", "digital", "activity"}
        assert required <= set(ss.keys()), (
            f"section_scores missing sections: {required - set(ss.keys())}"
        )
        for sec, meta in ss.items():
            assert "severity" in meta
            assert "weight" in meta
            assert isinstance(meta["weight"], (int, float))
            assert meta["severity"] in {"ok", "warning", "critical", "info"}

    def test_section_weights_sum_to_100(self, compute_response):
        """Weights should sum ≈100 (allow small rounding)."""
        ss = compute_response["scores"]["section_scores"]
        total = sum(v["weight"] for v in ss.values())
        assert 99.0 <= total <= 101.0, f"section weights sum off: {total}"


# ── Regression: existing sections still present ──────────────────────────
class TestRegressionExistingSections:
    def test_all_preexisting_sections_present(self, compute_response):
        for sec in [
            "cash", "sales", "ar", "payables", "transfers", "returns",
            "digital", "activity", "inventory", "security", "unverified",
        ]:
            assert sec in compute_response, f"regression: section '{sec}' missing"

    def test_cash_formula_still_there(self, compute_response):
        cash = compute_response["cash"]
        for field in [
            "starting_float", "cash_sales", "total_partial_cash",
            "total_split_cash", "total_cash_ar", "net_fund_transfers",
            "total_cashier_expenses", "expected_cash", "formula",
        ]:
            assert field in cash, f"cash section missing {field}"

    def test_unverified_section_intact(self, compute_response):
        u = compute_response["unverified"]
        for f in ["expenses", "expenses_count", "purchase_orders", "po_count",
                  "total_items", "severity"]:
            assert f in u


# ── Regression: /api/audit/sessions CRUD ─────────────────────────────────
class TestAuditSessionsCrud:
    def test_create_update_complete_session(self, headers, branch_id):
        today = dt.date.today()
        pf = today.replace(day=1).strftime("%Y-%m-%d")
        pt = today.strftime("%Y-%m-%d")

        # Create
        r = requests.post(
            f"{API}/audit/sessions",
            json={
                "branch_id": branch_id,
                "period_from": pf,
                "period_to": pt,
                "audit_type": "partial",
                "title": "TEST_phase1_session",
            },
            headers=headers,
            timeout=30,
        )
        assert r.status_code in (200, 201), f"create failed: {r.status_code} {r.text[:300]}"
        sid = r.json().get("id") or r.json().get("session_id")
        assert sid

        # Update overall_score
        r2 = requests.put(
            f"{API}/audit/sessions/{sid}",
            json={"overall_score": 77},
            headers=headers,
            timeout=30,
        )
        assert r2.status_code in (200, 204), f"update failed: {r2.status_code} {r2.text[:300]}"

        # Mark completed
        r3 = requests.put(
            f"{API}/audit/sessions/{sid}",
            json={"status": "completed"},
            headers=headers,
            timeout=30,
        )
        assert r3.status_code in (200, 204), f"complete failed: {r3.status_code} {r3.text[:300]}"

        # GET verify
        r4 = requests.get(f"{API}/audit/sessions/{sid}", headers=headers, timeout=30)
        assert r4.status_code == 200
        data = r4.json()
        assert data.get("overall_score") == 77
        assert data.get("status") == "completed"

        # Cleanup
        requests.delete(f"{API}/audit/sessions/{sid}", headers=headers, timeout=30)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

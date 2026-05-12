#!/usr/bin/env python3
"""
br_report.py — read-only summarizer for the business_regression JSON report.

Purpose
-------
The `business_regression` pytest suite writes a structured JSON evidence
file (see backend/tests/business_regression/conftest.py::pytest_terminal_summary).
That JSON is great for machines but unfriendly for stakeholders/deploy
reviews. This tool renders a one-screen, terminal-friendly summary and
surfaces failed rows in detail.

Scope guarantees
----------------
* Read-only. Does not write to disk, does not touch DB, does not import
  application code.
* Stdlib only (argparse, json, sys, pathlib).
* Does not modify production code, test code, or the report schema.

Usage
-----
    python tools/br_report.py
    python tools/br_report.py /path/to/report.json
    python tools/br_report.py --failures-only
    python tools/br_report.py --scenario br6

Exit codes
----------
    0  all rows pass
    1  at least one row failed
    2  report file missing or invalid
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DEFAULT_REPORT = Path("/app/test_reports/business_regression_latest.json")


def _load(path: Path) -> dict:
    if not path.exists():
        print(f"ERROR: report file not found: {path}", file=sys.stderr)
        sys.exit(2)
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        print(f"ERROR: report file is not valid JSON: {path} ({exc})", file=sys.stderr)
        sys.exit(2)
    if not isinstance(data, dict) or "rows" not in data or not isinstance(data["rows"], list):
        print(f"ERROR: report file missing 'rows' array: {path}", file=sys.stderr)
        sys.exit(2)
    return data


def _filter_rows(rows: list[dict], scenario_prefix: str | None) -> list[dict]:
    if not scenario_prefix:
        return rows
    return [r for r in rows if str(r.get("scenario", "")).startswith(scenario_prefix)]


def _group_by_scenario(rows: list[dict]) -> dict[str, dict[str, int]]:
    grouped: dict[str, dict[str, int]] = {}
    for r in rows:
        scen = str(r.get("scenario", "<unknown>"))
        bucket = grouped.setdefault(scen, {"total": 0, "pass": 0, "fail": 0})
        bucket["total"] += 1
        if r.get("status") == "PASS":
            bucket["pass"] += 1
        else:
            bucket["fail"] += 1
    return grouped


def _fmt_pct(num: int, denom: int) -> str:
    if denom == 0:
        return "  0.0%"
    return f"{(100.0 * num / denom):5.1f}%"


def _print_header(report_path: Path, data: dict, rows: list[dict], scenario_prefix: str | None) -> None:
    total = len(rows)
    passed = sum(1 for r in rows if r.get("status") == "PASS")
    failed = total - passed
    width = 72
    print("+" + "-" * (width - 2) + "+")
    print(f"| Business Regression Report".ljust(width - 1) + "|")
    print("+" + "-" * (width - 2) + "+")
    print(f"| Source      : {report_path}".ljust(width - 1) + "|")
    print(f"| Generated   : {data.get('generated_at', '<missing>')}".ljust(width - 1) + "|")
    print(f"| Pytest exit : {data.get('pytest_exitstatus', '<missing>')}".ljust(width - 1) + "|")
    if scenario_prefix:
        print(f"| Filter      : scenario startswith '{scenario_prefix}'".ljust(width - 1) + "|")
    print(f"| Total rows  : {total}".ljust(width - 1) + "|")
    print(f"| Pass        : {passed}  ({_fmt_pct(passed, total)})".ljust(width - 1) + "|")
    print(f"| Fail        : {failed}".ljust(width - 1) + "|")
    print("+" + "-" * (width - 2) + "+")


def _print_scenario_table(rows: list[dict]) -> None:
    grouped = _group_by_scenario(rows)
    if not grouped:
        print("(no scenarios match filter)")
        return
    print(f"\nScenarios ({len(grouped)}):")
    name_width = max(len(s) for s in grouped) + 2
    for scen in sorted(grouped):
        b = grouped[scen]
        marker = "ok " if b["fail"] == 0 else "FAIL"
        line = f"  [{marker}] {scen.ljust(name_width)} {b['pass']}/{b['total']:<3}"
        if b["fail"]:
            line += f"   {b['fail']} failed"
        print(line)


def _print_failed_rows(rows: list[dict]) -> None:
    failed = [r for r in rows if r.get("status") != "PASS"]
    if not failed:
        print("\n(No failed rows.)")
        return
    print(f"\nFailed rows ({len(failed)}):")
    print("-" * 72)
    for r in failed:
        scen = r.get("scenario", "<unknown>")
        step = r.get("step", "<unknown>")
        print(f"\n  X  {scen} :: {step}")
        print(f"     expected : {json.dumps(r.get('expected'), default=str)}")
        print(f"     actual   : {json.dumps(r.get('actual'),   default=str)}")
        evidence = r.get("evidence") or {}
        if evidence:
            ev_str = json.dumps(evidence, default=str)
            if len(ev_str) > 400:
                ev_str = ev_str[:397] + "..."
            print(f"     evidence : {ev_str}")
        recorded_at = r.get("recorded_at")
        if recorded_at:
            print(f"     at       : {recorded_at}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Summarize a business_regression JSON report.",
    )
    parser.add_argument(
        "report_path",
        nargs="?",
        default=str(DEFAULT_REPORT),
        help=f"Path to report JSON (default: {DEFAULT_REPORT})",
    )
    parser.add_argument(
        "--failures-only",
        action="store_true",
        help="Skip summary table; print only failed rows.",
    )
    parser.add_argument(
        "--scenario",
        metavar="PREFIX",
        default=None,
        help="Filter rows whose scenario starts with PREFIX (e.g. 'br6').",
    )
    args = parser.parse_args(argv)

    report_path = Path(args.report_path)
    data = _load(report_path)
    rows = _filter_rows(data.get("rows", []), args.scenario)

    if args.failures_only:
        failed = [r for r in rows if r.get("status") != "PASS"]
        # Minimal header so caller still has context.
        print(f"Report      : {report_path}")
        print(f"Generated   : {data.get('generated_at', '<missing>')}")
        if args.scenario:
            print(f"Filter      : scenario startswith '{args.scenario}'")
        print(f"Failed rows : {len(failed)} (of {len(rows)} in scope)")
        _print_failed_rows(rows)
    else:
        _print_header(report_path, data, rows, args.scenario)
        _print_scenario_table(rows)
        _print_failed_rows(rows)

    any_failed = any(r.get("status") != "PASS" for r in rows)
    return 1 if any_failed else 0


if __name__ == "__main__":
    sys.exit(main())

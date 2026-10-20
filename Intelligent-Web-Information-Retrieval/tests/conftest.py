"""Pytest configuration.

Registers the iteration-2 benchmark report hook so every test run writes
``reports/benchmark_iter2.json`` with pass/fail counts and rates.
"""

from __future__ import annotations

import json
from pathlib import Path


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Write a benchmark report aggregating truth-set case outcomes.

    Triggered by pytest after all tests in a session. Detects which cases
    were truth-set cases (``tests.test_iter2_benchmark.test_truth_case``)
    and emits:
      - per-case status (PASS / FAIL)
      - pass_rate, exact_match_rate, refusal_precision
    """
    reports_dir = Path(__file__).resolve().parent.parent / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    for rep in (
        list(terminalreporter.stats.get("passed", []))
        + list(terminalreporter.stats.get("failed", []))
    ):
        tid = getattr(rep, "nodeid", "")
        if "test_truth_case" not in tid:
            continue
        name = tid.split("[")[-1].rstrip("]")
        target = "refusal" if (
            "refuses" in name or "meaning" in name
        ) else "exact"
        rows.append({
            "name": name,
            "status": "PASS" if rep.outcome == "passed" else "FAIL",
            "target_type": target,
        })
    total = len(rows)
    passed = sum(1 for r in rows if r["status"] == "PASS")
    exact_total = sum(1 for r in rows if r["target_type"] == "exact")
    refusal_total = sum(1 for r in rows if r["target_type"] == "refusal")
    exact_pass = sum(1 for r in rows
                     if r["status"] == "PASS" and r["target_type"] == "exact")
    refusal_pass = sum(1 for r in rows
                       if r["status"] == "PASS" and r["target_type"] == "refusal")
    summary = {
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": round(passed / max(1, total), 3),
        "exact_match_total": exact_total,
        "exact_match_pass": exact_pass,
        "exact_match_rate": round(exact_pass / max(1, exact_total), 3),
        "refusal_total": refusal_total,
        "refusal_pass": refusal_pass,
        "refusal_precision": round(refusal_pass / max(1, refusal_total), 3),
        "rows": rows,
    }
    out = reports_dir / "benchmark_iter2.json"
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)
    terminalreporter.write_sep(
        "-",
        f"benchmark_iter2.json: {passed}/{total} pass "
        f"({summary['pass_rate']*100:.0f}%); "
        f"exact={summary['exact_match_rate']}; "
        f"refusal_precision={summary['refusal_precision']}",
        yellow=True,
    )
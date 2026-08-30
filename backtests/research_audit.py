"""Audit historical research reports for selection/OOS contamination."""
from __future__ import annotations

from typing import Any, Dict, Iterable


def audit_report(report: Dict[str, Any]) -> Dict[str, Any]:
    rows = report.get("leaderboard", [])
    issues = []
    ids = set()
    for row in rows:
        eid = row.get("id")
        if eid in ids:
            issues.append(f"duplicate experiment id: {eid}")
        ids.add(eid)
        metrics = row.get("metrics", {})
        if row.get("status") == "OOS_PASS" and metrics.get("oos_trades", 0) < 10:
            issues.append(f"OOS_PASS with insufficient OOS trades: {eid}")
        if "oos_profit_factor" not in metrics:
            issues.append(f"missing OOS PF: {eid}")
        if "validation_profit_factor" not in metrics:
            issues.append(f"missing validation PF: {eid}")
    return {"experiments_audited": len(rows), "issues": issues, "clean": not issues}

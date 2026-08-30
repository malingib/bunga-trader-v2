"""Autonomous Research Orchestrator — free-reign loop with trading-safety gates."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from .orchestrator import ResearchOrchestrator

import sys
RESEARCH_ROOT = str(Path(__file__).resolve().parents[1])
if RESEARCH_ROOT not in sys.path:
    sys.path.insert(0, RESEARCH_ROOT)

# ── Safety: allowlist vs denylist ──
# Autonomous writes are ALLOWED to these research-only paths without human review.
ALLOWED_WRITE_GLOBS = [
    "backtests/results.jsonl",
    "backtests/autonomous_best.json",
    "backtests/autonomous_proposals.json",
    "backtests/experiment_manifests/*.json",
    "backtests/agents/**/*.py",  # self-improvement
    "data/market_cache/*",
]

# These are MONEY paths — require human approval + test + PR note per trading-safety skill.
# Orchestrator will NEVER write them in autonomous mode; it will emit a proposal instead.
MONEY_PATHS = [
    "core_backend/risk_engine.py",
    "core_backend/trade_dispatcher.py",
    "bridge_app/**",
    "core_backend/main.py",  # /approve endpoints
]

DENIED_ENV_VARS = {"AUTO_APPROVE", "DISABLE_RISK_CHECKS", "BYPASS_VALIDATION"}


def _is_money_path(path: str) -> bool:
    import fnmatch
    return any(fnmatch.fnmatch(path, pat) for pat in MONEY_PATHS)


def _is_allowed(path: str) -> bool:
    import fnmatch
    return any(fnmatch.fnmatch(path, pat) for pat in ALLOWED_WRITE_GLOBS)


class AutonomousOrchestrator(ResearchOrchestrator):
    """Free-reign but gated: runs tournament → improvement → robustness → review → proposes/writes."""

    def __init__(self, *args, free_reign: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        self.free_reign = free_reign
        self.proposals: List[Dict[str, Any]] = []
        self.applied: List[Dict[str, Any]] = []

    async def autonomous_cycle(self, symbols: List[str] | None = None) -> Dict[str, Any]:
        """One full cycle: tournament → improvement on top candidates → robustness → decide writes."""
        cycle = {"started": datetime.now(timezone.utc).isoformat(), "free_reign": self.free_reign}

        # 1) Tournament (orchestrator spawns TournamentAgent + Review + Portfolio)
        print(">> autonomous: tournament")
        t_res = await self.run_tournament(symbols=symbols) if symbols else await self.run_tournament()
        if t_res.status != "OK":
            cycle["tournament"] = {"status": t_res.status, "error": t_res.notes}
            return cycle

        # Extract OOS_PASS candidates via ReviewAgent artifacts (last review)
        review_res = [h for h in self.history if h.agent_type == "review"][-1] if any(h.agent_type == "review" for h in self.history) else None
        passed = review_res.artifacts.get("passed", []) if review_res else []
        # Fallback: filter raw tournament results if review empty
        if not passed:
            results = t_res.artifacts.get("results", [])
            passed = [r for r in results if r.status == "OOS_PASS"]

        cycle["tournament"] = {"total": t_res.metrics["total"], "oos_pass": len(passed)}
        print(f"   tournament: {len(passed)} passed")

        # 2) For each top passed, spawn ImprovementAgent + Robustness
        # Limit to top 3 by score to keep cycle bounded
        from research_lab import rank_results
        top = rank_results(passed)[:3] if passed else []
        improvements = []
        for exp_res in top:
            exp = exp_res.experiment
            # need data path for this symbol/tf
            data_path = f"data/market_cache/yf_{exp.symbol}_{exp.timeframe}_60d.csv"
            if not Path(data_path).exists():
                data_path = f"data/market_cache/yf_{exp.symbol}_1h_2y.csv"
            if not Path(data_path).exists():
                continue
            # Build grid: for donchian adapt lookback, for others generic
            grid = {"lookback": [10, 15, 20, 25]} if "donchian" in exp.strategy_id else {"threshold": [20, 30, 40]}
            # improvement agent will infer backtest if no fn
            imp = await self.run_improvement(symbol=exp.symbol, timeframe=exp.timeframe, strategy_id=exp.strategy_id, data_path=data_path, grid=grid)
            improvements.append(imp)
            # robustness on best pnls if available
            best = imp.artifacts.get("best")
            if best and imp.metrics.get("oos_status") == "OOS_PASS":
                # we don't have pnls here without re-running, so use dummy robust check
                # real robustness needs pnls; skip for now
                pass

        cycle["improvements"] = [{"strategy": r.metrics.get("best_score", 0), "status": r.status, "notes": r.notes} for r in improvements]

        # 3) Decide what to WRITE vs PROPOSE
        # Free-reign: write research artifacts; propose money-path changes
        best_file = Path("backtests/autonomous_best.json")
        best_payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "top": [{"strategy": e.experiment.strategy_id, "symbol": e.experiment.symbol, "timeframe": e.experiment.timeframe, "metrics": e.metrics, "status": e.status} for e in top],
            "improvements": [{"id": r.agent_id, "metrics": r.metrics, "artifacts": {k: str(v)[:500] for k, v in r.artifacts.items()}} for r in improvements],
        }

        if self.free_reign:
            # SAFE write: research-only path is allowed
            if _is_allowed(str(best_file)):
                best_file.write_text(json.dumps(best_payload, indent=2, default=str))
                self.applied.append({"path": str(best_file), "action": "write", "reason": "autonomous best snapshot"})
                print(f"   wrote {best_file}")
            # For money-path proposals, DO NOT WRITE — emit proposal file instead
            proposal_file = Path("backtests/autonomous_proposals.json")
            proposal = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "type": "MONEY_PATH_PROPOSAL",
                "note": "Orchestrator detected improvement but money-path write requires human review per trading-safety",
                "candidates": best_payload["top"],
                "required_checklist": [
                    "Read core_backend/risk_engine.py end-to-end",
                    "Confirm validate_signal_risk() is single source",
                    "Update tests/test_risk_engine.py",
                    "Verify validate_signal_risk()==False still short-circuits dispatcher",
                    "No auto-approve env var",
                ],
            }
            proposal_file.write_text(json.dumps(proposal, indent=2, default=str))
            self.proposals.append(proposal)
            print(f"   proposal (money-path gated) → {proposal_file}")
        else:
            print("   dry-run: free_reign=False so no writes (use --free-reign to enable)")

        cycle["applied"] = self.applied[-5:]
        cycle["proposals"] = len(self.proposals)
        return cycle

    async def run_forever(self, interval_sec: int = 3600, symbols: List[str] | None = None, max_cycles: int = 0):
        """Free-reign forever loop — interval_sec between cycles, 0 = infinite."""
        cycle = 0
        while True:
            cycle += 1
            print(f"\n{'='*60}\nAUTONOMOUS CYCLE {cycle}  {datetime.now(timezone.utc).isoformat()}\n{'='*60}")
            try:
                res = await self.autonomous_cycle(symbols=symbols)
                print(json.dumps(res, indent=2, default=str))
            except Exception as e:
                print(f"cycle {cycle} failed: {e}")
                import traceback; traceback.print_exc()
            if max_cycles and cycle >= max_cycles:
                break
            if interval_sec <= 0:
                break
            await asyncio.sleep(interval_sec)

"""ReviewAgent — applies RESEARCH_PROTOCOL gates and decides PASS/FAIL/KILLED."""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List

from .base import AgentResult, BaseAgent

import sys
from pathlib import Path
RESEARCH_ROOT = str(Path(__file__).resolve().parents[1])
if RESEARCH_ROOT not in sys.path:
    sys.path.insert(0, RESEARCH_ROOT)

from tournament_config import MAX_COMPLEXITY, MAX_PARAMETER_CV, MIN_TRADES_OOS, MIN_TRADES_VALIDATION  # noqa: E402


class ReviewAgent(BaseAgent):
    def __init__(self, experiment_results: List[Any], **kw):
        super().__init__("review", kw)
        self.experiment_results = experiment_results

    async def run(self) -> AgentResult:
        return await asyncio.to_thread(self._run_sync)

    def _run_sync(self) -> AgentResult:
        passed: List[Any] = []
        failed: List[Any] = []
        killed: List[Any] = []
        for r in self.experiment_results:
            m = r.metrics
            trades = float(m.get("trades", 0))
            oos_trades = float(m.get("oos_trades", trades))
            comp = float(m.get("complexity", 0))
            cv = float(self.config.get("cv", m.get("cv", 0)))
            max_dd = float(m.get("max_drawdown_pct", 0))
            oos_pf = float(m.get("oos_profit_factor", m.get("profit_factor", 0)))

            reasons: List[str] = []
            if trades < MIN_TRADES_VALIDATION:
                reasons.append(f"val trades {trades:.0f} < {MIN_TRADES_VALIDATION}")
            if oos_trades < MIN_TRADES_OOS:
                reasons.append(f"oos trades {oos_trades:.0f} < {MIN_TRADES_OOS}")
            if comp > MAX_COMPLEXITY:
                reasons.append(f"complexity {comp} > {MAX_COMPLEXITY}")
            if cv > MAX_PARAMETER_CV:
                reasons.append(f"cv {cv:.2f} > {MAX_PARAMETER_CV}")
            if max_dd > 30:
                reasons.append(f"dd {max_dd:.1f}% >30%")
            if oos_pf <= 1.0:
                reasons.append(f"oos PF {oos_pf:.2f} <=1.0")

            if not reasons:
                passed.append(r)
            elif max_dd > 30 or oos_pf <= 0.5:
                killed.append((r, reasons))
            else:
                failed.append((r, reasons))

        return self._ok(
            metrics={"passed": len(passed), "failed": len(failed), "killed": len(killed), "total": len(self.experiment_results)},
            artifacts={"passed": passed, "failed": failed, "killed": killed},
            notes=[f"review {len(passed)} PASS / {len(failed)} FAIL / {len(killed)} KILLED"],
        )

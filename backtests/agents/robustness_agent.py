"""RobustnessAgent — Monte Carlo + cost + regime checks."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, List

from .base import AgentResult, BaseAgent

import sys
RESEARCH_ROOT = str(Path(__file__).resolve().parents[1])
if RESEARCH_ROOT not in sys.path:
    sys.path.insert(0, RESEARCH_ROOT)

from robustness import cost_sensitivity, monte_carlo_trade_paths  # noqa: E402


class RobustnessAgent(BaseAgent):
    def __init__(self, pnls: List[float], gross: float | None = None, trade_count: int | None = None, **kw):
        super().__init__("robustness", kw)
        self.pnls = list(pnls)
        self.gross = gross
        self.trade_count = trade_count if trade_count is not None else len(pnls)

    async def run(self) -> AgentResult:
        return await asyncio.to_thread(self._run_sync)

    def _run_sync(self) -> AgentResult:
        if len(self.pnls) < 2:
            return self._fail("need >=2 pnls", metrics={"pnls": len(self.pnls)})
        mc = monte_carlo_trade_paths(self.pnls, iterations=2000, seed=42)
        costs = [0, 0.3, 0.6, 1.0, 2.0]
        gross = self.gross if self.gross is not None else sum(p for p in self.pnls if p > 0)
        cost_tbl = cost_sensitivity(gross, self.trade_count, costs)
        # bounded robustness score
        from robustness import robustness_score
        # derive inputs: need oos pf, expectancy, dd, cv, mc dd p95
        # if not supplied, use defaults that keep score informative
        score = robustness_score(oos_pf=self.config.get("oos_pf", 1.0), oos_expectancy=self.config.get("oos_exp", 0.0),
                                 max_dd=self.config.get("max_dd", 10.0), stability_cv=self.config.get("cv", 0.5), mc_dd_p95=mc.get("drawdown_p95", 10))
        return self._ok(metrics={"mc": mc, "cost": cost_tbl, "robustness_score": score}, notes=[f"mc p50 {mc.get('terminal_p50'):.1f} cost0 {cost_tbl.get('cost_0'):.1f} score {score:.1f}"])

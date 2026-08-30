"""PortfolioAgent — correlation / diversification report."""
from __future__ import annotations

import asyncio
from typing import Dict

import pandas as pd

from .base import AgentResult, BaseAgent

import sys
from pathlib import Path
RESEARCH_ROOT = str(Path(__file__).resolve().parents[1])
if RESEARCH_ROOT not in sys.path:
    sys.path.insert(0, RESEARCH_ROOT)

from portfolio_lab import align_equity_curves, correlation_matrix  # noqa: E402
from portfolio_research import diversification_report  # noqa: E402


class PortfolioAgent(BaseAgent):
    def __init__(self, curves: Dict[str, pd.Series], **kw):
        super().__init__("portfolio", kw)
        self.curves = curves

    async def run(self) -> AgentResult:
        return await asyncio.to_thread(self._run_sync)

    def _run_sync(self) -> AgentResult:
        if not self.curves:
            return self._fail("no curves")
        try:
            corr = correlation_matrix(self.curves)
            mean_corr = float(corr.where(~corr.isin([1.0])).stack().mean()) if not corr.empty else 0.0
            # need list[float] version for diversification_report
            curves_list = {k: v.dropna().tolist() for k, v in self.curves.items()}
            div = diversification_report(curves_list)
            return self._ok(metrics={"mean_corr": mean_corr, "diversification": div, "n": len(self.curves)}, artifacts={"corr": corr, "curves": self.curves})
        except Exception as e:
            return self._fail(str(e))

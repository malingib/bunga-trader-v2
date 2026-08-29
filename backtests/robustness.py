"""Robustness diagnostics for research candidates."""
from __future__ import annotations

from typing import Dict, Iterable, Sequence
import numpy as np


def monte_carlo_trade_paths(pnls: Sequence[float], iterations: int = 2000, seed: int = 42) -> Dict[str, float]:
    """Bootstrap trade order to estimate terminal-return/drawdown ranges."""
    if len(pnls) < 2:
        return {"iterations": 0.0}
    rng = np.random.default_rng(seed)
    values = np.asarray(pnls, dtype=float)
    terminals, drawdowns = [], []
    for _ in range(iterations):
        path = rng.choice(values, size=len(values), replace=True)
        equity = np.cumsum(path)
        peak = np.maximum.accumulate(equity)
        dd = float(np.max(peak - equity))
        terminals.append(float(equity[-1]))
        drawdowns.append(dd)
    return {
        "iterations": float(iterations),
        "terminal_p05": float(np.quantile(terminals, .05)),
        "terminal_p50": float(np.quantile(terminals, .50)),
        "terminal_p95": float(np.quantile(terminals, .95)),
        "drawdown_p50": float(np.quantile(drawdowns, .50)),
        "drawdown_p95": float(np.quantile(drawdowns, .95)),
    }


def cost_sensitivity(pnl_gross: float, trade_count: int, costs: Iterable[float]) -> Dict[str, float]:
    return {f"cost_{c:g}": float(pnl_gross - trade_count*c) for c in costs}


def robustness_score(*, oos_pf: float, oos_expectancy: float, max_dd: float, stability_cv: float, mc_dd_p95: float) -> float:
    """Bounded 0-100 score for comparing research candidates, not predicting profit."""
    score = 50.0
    score += min(max(oos_pf - 1.0, -1.0), 1.0) * 20
    score += min(max(oos_expectancy, -1.0), 1.0) * 15
    score -= min(max_dd, 60.0) * 0.20
    score -= min(stability_cv, 2.0) * 7
    score -= min(mc_dd_p95, 100.0) * 0.05
    return float(max(0.0, min(100.0, score)))

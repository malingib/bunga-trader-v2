"""Historical quantitative research primitives for Bunga Trader."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Sequence
import pandas as pd


@dataclass(frozen=True)
class ResearchSplit:
    train: pd.DataFrame
    validation: pd.DataFrame
    test: pd.DataFrame


@dataclass(frozen=True)
class Experiment:
    experiment_id: str
    strategy_id: str
    version: str
    symbol: str
    timeframe: str
    parameters: Dict[str, Any] = field(default_factory=dict)
    parents: Sequence[str] = field(default_factory=tuple)
    hypothesis: str = ""


@dataclass
class ExperimentResult:
    experiment: Experiment
    metrics: Dict[str, float]
    status: str
    notes: List[str] = field(default_factory=list)


def chronological_split(data: pd.DataFrame, train_ratio: float = .60, validation_ratio: float = .20) -> ResearchSplit:
    if not 0 < train_ratio < 1 or not 0 <= validation_ratio < 1 or train_ratio + validation_ratio >= 1:
        raise ValueError("invalid split ratios")
    if len(data) < 30:
        raise ValueError("at least 30 observations are required")
    frame = data.sort_index().copy()
    n = len(frame); train_end = int(n * train_ratio); validation_end = int(n * (train_ratio + validation_ratio))
    return ResearchSplit(frame.iloc[:train_end].copy(), frame.iloc[train_end:validation_end].copy(), frame.iloc[validation_end:].copy())


def combine_signals(signals: Iterable[pd.Series], mode: str = "all") -> pd.Series:
    items = [s.astype(bool) for s in signals]
    if not items: raise ValueError("at least one signal is required")
    frame = pd.concat(items, axis=1).fillna(False)
    if mode == "all": return frame.all(axis=1)
    if mode == "any": return frame.any(axis=1)
    raise ValueError("mode must be 'all' or 'any'")


def score_result(metrics: Dict[str, float], complexity: float = 0.0, include_oos: bool = False) -> float:
    """Score validation by default. OOS is opt-in so it cannot influence selection accidentally."""
    pf = float(metrics.get("profit_factor", 0.0)); expectancy = float(metrics.get("expectancy_r", 0.0))
    sharpe = float(metrics.get("sharpe", 0.0)); dd = abs(float(metrics.get("max_drawdown_pct", 100.0)))
    trades = float(metrics.get("trades", 0.0)); oos_pf = float(metrics.get("oos_profit_factor", pf))
    return .30*pf + .25*expectancy + .15*sharpe + (.20*oos_pf if include_oos else 0) + .10*min(trades/300,1) - .004*dd - .05*complexity


def rank_results(results: Sequence[ExperimentResult], include_oos: bool = False) -> List[ExperimentResult]:
    return sorted(results, key=lambda r: score_result(r.metrics, r.metrics.get("complexity",0), include_oos), reverse=True)


def parameter_stability(metrics_by_variant: Dict[str, Dict[str, float]], metric: str = "profit_factor") -> Dict[str, float]:
    values = [float(m[metric]) for m in metrics_by_variant.values() if metric in m]
    if len(values) < 2: return {"count": float(len(values)), "mean": values[0] if values else 0.0, "cv": 0.0}
    s = pd.Series(values); mean = float(s.mean()); std = float(s.std(ddof=1))
    return {"count": float(len(values)), "mean": mean, "std": std, "cv": float(std/abs(mean)) if mean else float("inf"), "min": float(s.min()), "max": float(s.max())}


class StrategyLab:
    def __init__(self): self.strategies: Dict[str, Callable[..., Any]] = {}; self.experiments: List[ExperimentResult] = []
    def register(self, strategy_id: str, strategy_fn: Callable[..., Any]) -> None:
        if strategy_id in self.strategies: raise ValueError(f"strategy already registered: {strategy_id}")
        self.strategies[strategy_id] = strategy_fn
    def available_strategies(self) -> List[str]: return sorted(self.strategies)
    def add_result(self, result: ExperimentResult) -> None: self.experiments.append(result)
    def leaderboard(self) -> List[ExperimentResult]: return rank_results(self.experiments)

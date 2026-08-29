"""Bunga Trader Research Lab.

Research-only orchestration layer. It deliberately has no approval, AI,
broker, or execution dependencies.

The lab treats every strategy change as an experiment and keeps TRAIN,
VALIDATION and FINAL OOS periods conceptually separate. It can run multiple
strategy families, parameter variants and combinations over historical data.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

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


def chronological_split(
    data: pd.DataFrame,
    train_ratio: float = 0.60,
    validation_ratio: float = 0.20,
) -> ResearchSplit:
    """Create a chronological train/validation/final-OOS split."""
    if not 0 < train_ratio < 1 or not 0 <= validation_ratio < 1:
        raise ValueError("invalid split ratios")
    if train_ratio + validation_ratio >= 1:
        raise ValueError("train + validation must leave final OOS data")
    if len(data) < 30:
        raise ValueError("at least 30 observations are required")

    frame = data.sort_index().copy()
    n = len(frame)
    train_end = int(n * train_ratio)
    validation_end = int(n * (train_ratio + validation_ratio))
    return ResearchSplit(
        frame.iloc[:train_end].copy(),
        frame.iloc[train_end:validation_end].copy(),
        frame.iloc[validation_end:].copy(),
    )


def combine_signals(signals: Iterable[pd.Series], mode: str = "all") -> pd.Series:
    """Combine strategy signals without introducing future information."""
    items = [s.astype(bool) for s in signals]
    if not items:
        raise ValueError("at least one signal is required")
    frame = pd.concat(items, axis=1).fillna(False)
    if mode == "all":
        return frame.all(axis=1)
    if mode == "any":
        return frame.any(axis=1)
    raise ValueError("mode must be 'all' or 'any'")


def score_result(metrics: Dict[str, float], complexity: float = 0.0) -> float:
    """Conservative research score; return, stability and drawdown matter."""
    pf = float(metrics.get("profit_factor", 0.0))
    expectancy = float(metrics.get("expectancy_r", 0.0))
    sharpe = float(metrics.get("sharpe", 0.0))
    oos_pf = float(metrics.get("oos_profit_factor", pf))
    drawdown = abs(float(metrics.get("max_drawdown_pct", 100.0)))
    trades = float(metrics.get("trades", 0.0))
    sample_bonus = min(trades / 300.0, 1.0)
    return (
        0.25 * pf
        + 0.20 * expectancy
        + 0.15 * sharpe
        + 0.25 * oos_pf
        + 0.10 * sample_bonus
        - 0.004 * drawdown
        - 0.05 * complexity
    )


def rank_results(results: Sequence[ExperimentResult]) -> List[ExperimentResult]:
    """Rank experiments without changing or refitting their results."""
    return sorted(
        results,
        key=lambda r: score_result(r.metrics, r.metrics.get("complexity", 0.0)),
        reverse=True,
    )


def parameter_stability(
    metrics_by_variant: Dict[str, Dict[str, float]],
    metric: str = "profit_factor",
) -> Dict[str, float]:
    """Measure sensitivity across nearby parameter variants.

    A robust strategy should not depend on one isolated parameter point.
    """
    values = [float(m[metric]) for m in metrics_by_variant.values() if metric in m]
    if len(values) < 2:
        return {"count": float(len(values)), "mean": values[0] if values else 0.0, "cv": 0.0}
    series = pd.Series(values)
    mean = float(series.mean())
    return {
        "count": float(len(values)),
        "mean": mean,
        "std": float(series.std(ddof=1)),
        "cv": float(series.std(ddof=1) / abs(mean)) if mean else float("inf"),
        "min": float(series.min()),
        "max": float(series.max()),
    }


class StrategyLab:
    """Registry and experiment coordinator for historical research."""

    def __init__(self) -> None:
        self.strategies: Dict[str, Callable[..., Any]] = {}
        self.experiments: List[ExperimentResult] = []

    def register(self, strategy_id: str, strategy_fn: Callable[..., Any]) -> None:
        if strategy_id in self.strategies:
            raise ValueError(f"strategy already registered: {strategy_id}")
        self.strategies[strategy_id] = strategy_fn

    def available_strategies(self) -> List[str]:
        return sorted(self.strategies)

    def add_result(self, result: ExperimentResult) -> None:
        self.experiments.append(result)

    def leaderboard(self) -> List[ExperimentResult]:
        return rank_results(self.experiments)


__all__ = [
    "Experiment",
    "ExperimentResult",
    "ResearchSplit",
    "StrategyLab",
    "chronological_split",
    "combine_signals",
    "parameter_stability",
    "rank_results",
    "score_result",
]

"""Batch research runner for the Bunga Strategy Research Lab.

This module intentionally performs research only. It does not import or call
execution, approval, Telegram, AI, or broker services.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from typing import Any, Callable, Dict, Iterable, List, Sequence

import pandas as pd

from experiment_manifest import ExperimentManifest
from research_lab import Experiment, ExperimentResult, chronological_split, parameter_stability, rank_results


BacktestFn = Callable[[str, Any], Any]


@dataclass(frozen=True)
class ParameterVariant:
    values: Dict[str, Any]


def parameter_grid(grid: Dict[str, Sequence[Any]]) -> List[ParameterVariant]:
    """Build deterministic parameter variants."""
    if not grid:
        return [ParameterVariant({})]
    keys = list(grid)
    return [ParameterVariant(dict(zip(keys, vals))) for vals in product(*(grid[k] for k in keys))]


def _metric(result: Any, name: str, default: float = 0.0) -> float:
    value = getattr(result, name, default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def summarize_result(result: Any) -> Dict[str, float]:
    return {
        "return_pct": _metric(result, "ret_pct"),
        "trades": _metric(result, "trades"),
        "win_pct": _metric(result, "win_pct"),
        "max_drawdown_pct": _metric(result, "max_dd_pct"),
        "profit_factor": _metric(result, "profit_factor"),
        "expectancy_r": _metric(result, "avg_r", _metric(result, "expectancy") / 100.0),
        "sharpe": _metric(result, "sharpe"),
    }


def run_parameter_research(
    *,
    strategy_id: str,
    version: str,
    symbol: str,
    timeframe: str,
    data: pd.DataFrame,
    backtest: Callable[[pd.DataFrame, Dict[str, Any]], Any],
    grid: Dict[str, Sequence[Any]],
    min_trades: int = 30,
) -> List[ExperimentResult]:
    """Run variants on TRAIN/VALIDATION and preserve every experiment.

    The FINAL OOS set is deliberately not used here. A caller should freeze
    the selected validation candidate and run it once against split.test.
    """
    split = chronological_split(data)
    results: List[ExperimentResult] = []
    for number, variant in enumerate(parameter_grid(grid), start=1):
        train_raw = backtest(split.train, variant.values)
        validation_raw = backtest(split.validation, variant.values)
        train = summarize_result(train_raw)
        validation = summarize_result(validation_raw)
        metrics = {
            **{f"train_{k}": v for k, v in train.items()},
            **{f"validation_{k}": v for k, v in validation.items()},
            "profit_factor": validation["profit_factor"],
            "expectancy_r": validation["expectancy_r"],
            "sharpe": validation["sharpe"],
            "max_drawdown_pct": validation["max_drawdown_pct"],
            "trades": validation["trades"],
            "complexity": float(len(variant.values)),
        }
        status = "VALIDATION_REJECT" if validation["trades"] < min_trades else "VALIDATION_CANDIDATE"
        experiment = Experiment(
            experiment_id=f"{strategy_id}-{version}-{symbol}-{number:04d}",
            strategy_id=strategy_id,
            version=version,
            symbol=symbol,
            timeframe=timeframe,
            parameters=variant.values,
        )
        results.append(ExperimentResult(experiment=experiment, metrics=metrics, status=status))
    return results


def select_validation_candidates(results: Sequence[ExperimentResult], top_n: int = 5) -> List[ExperimentResult]:
    """Select candidates using validation only."""
    eligible = [r for r in results if r.status == "VALIDATION_CANDIDATE"]
    return rank_results(eligible)[:top_n]


def freeze_oos_candidate(
    candidate: ExperimentResult,
    *,
    data: pd.DataFrame,
    backtest: Callable[[pd.DataFrame, Dict[str, Any]], Any],
) -> ExperimentResult:
    """Run a frozen candidate once on final OOS and return its result."""
    split = chronological_split(data)
    raw = backtest(split.test, candidate.experiment.parameters)
    oos = summarize_result(raw)
    metrics = dict(candidate.metrics)
    metrics.update({f"oos_{k}": v for k, v in oos.items()})
    status = "OOS_PASS" if oos["trades"] >= 10 and oos["profit_factor"] > 1.0 and oos["expectancy_r"] > 0 else "OOS_FAIL"
    return ExperimentResult(candidate.experiment, metrics, status, list(candidate.notes))


def stability_report(results: Iterable[ExperimentResult], metric: str = "validation_profit_factor") -> Dict[str, float]:
    by_id = {r.experiment.experiment_id: {metric: r.metrics.get(metric, 0.0)} for r in results}
    return parameter_stability(by_id, metric)

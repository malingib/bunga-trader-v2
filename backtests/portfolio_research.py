"""Portfolio-level research utilities."""
from __future__ import annotations

from typing import Dict, Iterable, List
import math
import pandas as pd


def equity_frame(curves: Dict[str, List[float]]) -> pd.DataFrame:
    """Align strategy equity curves by bar index for research."""
    if not curves:
        return pd.DataFrame()
    return pd.DataFrame({k: pd.Series(v, dtype=float) for k, v in curves.items()}).ffill().dropna(how="all")


def correlation_matrix(curves: Dict[str, List[float]]) -> pd.DataFrame:
    frame = equity_frame(curves)
    if frame.empty:
        return frame
    returns = frame.pct_change().replace([math.inf, -math.inf], float("nan"))
    return returns.corr()


def equal_weight_portfolio(curves: Dict[str, List[float]], initial_equity: float = 1000.0) -> List[float]:
    """Combine normalized strategy curves at equal weights."""
    frame = equity_frame(curves)
    if frame.empty:
        return []
    normalized = frame.div(frame.iloc[0]).fillna(1.0)
    combined = normalized.mean(axis=1)
    return (combined * initial_equity).tolist()


def max_drawdown(equity: Iterable[float]) -> float:
    peak = None
    worst = 0.0
    for value in equity:
        value = float(value)
        if peak is None or value > peak:
            peak = value
        if peak and (peak - value) / peak > worst:
            worst = (peak - value) / peak
    return worst * 100.0


def diversification_report(curves: Dict[str, List[float]]) -> Dict[str, object]:
    portfolio = equal_weight_portfolio(curves)
    corr = correlation_matrix(curves)
    return {
        "strategies": list(curves),
        "portfolio_max_drawdown_pct": round(max_drawdown(portfolio), 2),
        "average_pairwise_correlation": round(
            float(corr.where(~pd.api.types.is_bool_dtype(corr)).stack().mean()), 3
        ) if not corr.empty else 0.0,
        "correlation": corr.to_dict() if not corr.empty else {},
    }

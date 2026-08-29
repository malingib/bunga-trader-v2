"""Portfolio-level research utilities."""
from __future__ import annotations

from typing import Dict
import pandas as pd


def align_equity_curves(curves: Dict[str, pd.Series]) -> pd.DataFrame:
    return pd.concat(curves, axis=1).sort_index().ffill()


def correlation_matrix(curves: Dict[str, pd.Series]) -> pd.DataFrame:
    frame = align_equity_curves(curves)
    returns = frame.pct_change().replace([float("inf"), -float("inf")], pd.NA)
    return returns.corr()


def portfolio_curve(curves: Dict[str, pd.Series], weights: Dict[str, float] | None = None) -> pd.Series:
    frame = align_equity_curves(curves)
    if weights is None:
        weights = {k: 1.0 / len(frame.columns) for k in frame.columns}
    total = sum(weights.get(k, 0.0) for k in frame.columns)
    if total <= 0:
        raise ValueError("portfolio weights must have positive total")
    w = pd.Series({k: weights.get(k, 0.0) / total for k in frame.columns})
    return frame.mul(w, axis=1).sum(axis=1)


def portfolio_drawdown(curve: pd.Series) -> float:
    peak = curve.cummax()
    return float(((peak - curve) / peak.replace(0, pd.NA)).max() * 100)

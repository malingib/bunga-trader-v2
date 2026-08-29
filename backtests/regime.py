"""Market-regime features used to condition strategy research."""
from __future__ import annotations

import pandas as pd

from strategy_library import adx, atr, sma


def volatility_regime(data: pd.DataFrame, period: int = 14, lookback: int = 100) -> pd.Series:
    a = atr(data, period)
    pct = a / data["Close"].replace(0, pd.NA)
    q = pct.shift(1).rolling(lookback, min_periods=lookback)
    return pd.cut(pct, [-float("inf"), q.quantile(.33), q.quantile(.67), float("inf")], labels=["LOW", "MID", "HIGH"]) 


def trend_regime(data: pd.DataFrame, fast: int = 50, slow: int = 200, adx_period: int = 14) -> pd.Series:
    fast_ma, slow_ma = sma(data, fast), sma(data, slow)
    strength = adx(data, adx_period)
    out = pd.Series("RANGE", index=data.index, dtype="object")
    out[(fast_ma > slow_ma) & (strength >= 20)] = "UPTREND"
    out[(fast_ma < slow_ma) & (strength >= 20)] = "DOWNTREND"
    return out


def regime_mask(regimes: pd.Series, allowed) -> pd.Series:
    return regimes.isin(list(allowed)).fillna(False)

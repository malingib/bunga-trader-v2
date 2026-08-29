"""Minimal common interface for research strategies."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Protocol

import pandas as pd


@dataclass(frozen=True)
class StrategySpec:
    strategy_id: str
    family: str
    version: str = "1.0"
    description: str = ""
    parameters: Dict[str, Any] = field(default_factory=dict)
    complexity: int = 1


class ResearchStrategy(Protocol):
    spec: StrategySpec

    def signals(self, data: pd.DataFrame) -> pd.Series:
        """Return boolean entry/setup signals indexed like data."""
        ...

    def exits(self, data: pd.DataFrame, entries: pd.Series) -> pd.DataFrame:
        """Return research exit instructions for the backtest adapter."""
        ...


def validate_ohlcv(data: pd.DataFrame) -> pd.DataFrame:
    """Validate and normalize historical OHLCV data before strategy research."""
    required = {"Open", "High", "Low", "Close"}
    missing = required.difference(data.columns)
    if missing:
        raise ValueError(f"missing OHLC columns: {sorted(missing)}")
    frame = data.copy()
    frame = frame.sort_index()
    if frame.index.has_duplicates:
        raise ValueError("historical data contains duplicate timestamps")
    numeric = [c for c in ("Open", "High", "Low", "Close", "Volume") if c in frame.columns]
    frame[numeric] = frame[numeric].apply(pd.to_numeric, errors="coerce")
    frame = frame.dropna(subset=["Open", "High", "Low", "Close"])
    invalid = (frame["High"] < frame[["Open", "Close"]].max(axis=1)) | (frame["Low"] > frame[["Open", "Close"]].min(axis=1))
    if invalid.any():
        raise ValueError("historical OHLC data contains invalid bars")
    return frame

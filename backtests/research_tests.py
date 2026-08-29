"""Small regression tests for research invariants."""
from __future__ import annotations

import pandas as pd
import pytest

from research_lab import chronological_split, combine_signals
from strategy_interface import validate_ohlcv
from robustness import monte_carlo_trade_paths


def test_split_is_chronological_and_exhaustive():
    idx = pd.date_range("2024-01-01", periods=100, freq="h")
    data = pd.DataFrame({"Open": 1.0, "High": 2.0, "Low": 0.5, "Close": 1.5}, index=idx)
    split = chronological_split(data)
    assert len(split.train) == 60
    assert len(split.validation) == 20
    assert len(split.test) == 20
    assert split.train.index[-1] < split.validation.index[0] < split.test.index[0]


def test_ohlcv_rejects_invalid_bars():
    data = pd.DataFrame({"Open": [2], "High": [1], "Low": [0], "Close": [1]})
    with pytest.raises(ValueError):
        validate_ohlcv(data)


def test_signal_combination_requires_all_for_all_mode():
    idx = pd.RangeIndex(3)
    a = pd.Series([True, True, False], index=idx)
    b = pd.Series([True, False, True], index=idx)
    assert combine_signals([a, b], "all").tolist() == [True, False, False]


def test_monte_carlo_is_reproducible():
    a = monte_carlo_trade_paths([1, -1, 2, -0.5] * 10, iterations=100, seed=7)
    b = monte_carlo_trade_paths([1, -1, 2, -0.5] * 10, iterations=100, seed=7)
    assert a == b

from __future__ import annotations

import pandas as pd
import pytest

from backtests.research_lab import chronological_split, combine_signals, parameter_stability
from backtests.robustness import monte_carlo_trade_paths
from backtests.strategy_interface import validate_ohlcv


def test_split_is_chronological_and_exhaustive():
    idx = pd.date_range("2024-01-01", periods=100, freq="h")
    data = pd.DataFrame({"Open": 1.0, "High": 2.0, "Low": 0.5, "Close": 1.5}, index=idx)
    split = chronological_split(data)
    assert [len(x) for x in (split.train, split.validation, split.test)] == [60, 20, 20]
    assert split.train.index[-1] < split.validation.index[0] < split.test.index[0]


def test_invalid_ohlcv_is_rejected():
    data = pd.DataFrame({"Open": [2], "High": [1], "Low": [0], "Close": [1]})
    with pytest.raises(ValueError):
        validate_ohlcv(data)


def test_signal_combination_all():
    idx = pd.RangeIndex(3)
    a = pd.Series([True, True, False], index=idx)
    b = pd.Series([True, False, True], index=idx)
    assert combine_signals([a, b], "all").tolist() == [True, False, False]


def test_parameter_stability_detects_spread():
    report = parameter_stability({str(i): {"profit_factor": x} for i, x in enumerate([1.2, 1.3, 1.1])})
    assert report["count"] == 3
    assert report["min"] == 1.1
    assert report["max"] == 1.3
    assert report["cv"] >= 0


def test_monte_carlo_is_reproducible():
    pnls = [1, -1, 2, -0.5] * 10
    assert monte_carlo_trade_paths(pnls, iterations=100, seed=7) == monte_carlo_trade_paths(pnls, iterations=100, seed=7)

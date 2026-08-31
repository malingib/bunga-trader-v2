import pandas as pd
import pytest

from research_lab import chronological_split, combine_signals
from robustness import monte_carlo_trade_paths
from strategy_interface import validate_ohlcv


def sample_ohlcv(n=100):
    idx = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    close = pd.Series(range(100, 100 + n), index=idx, dtype=float)
    return pd.DataFrame({"Open": close - 0.2, "High": close + 0.5, "Low": close - 0.5, "Close": close, "Volume": 1000.0}, index=idx)


def test_split_is_chronological_and_exhaustive():
    data = sample_ohlcv()
    split = chronological_split(data)
    assert len(split.train) == 60
    assert len(split.validation) == 20
    assert len(split.test) == 20
    assert split.train.index[-1] < split.validation.index[0] < split.test.index[0]


def test_ohlcv_rejects_invalid_bar():
    bad = pd.DataFrame({"Open": [2.0], "High": [1.0], "Low": [0.0], "Close": [1.0]})
    with pytest.raises(ValueError):
        validate_ohlcv(bad)


def test_signal_combination_all():
    idx = pd.RangeIndex(3)
    a = pd.Series([True, True, False], index=idx)
    b = pd.Series([True, False, True], index=idx)
    assert combine_signals([a, b], "all").tolist() == [True, False, False]


def test_monte_carlo_is_reproducible():
    trades = [1.0, -1.0, 2.0, -0.5] * 10
    assert monte_carlo_trade_paths(trades, iterations=100, seed=7) == monte_carlo_trade_paths(trades, iterations=100, seed=7)

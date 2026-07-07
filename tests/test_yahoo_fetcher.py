"""Tests for core_backend.market_context.yahoo — Yahoo GC=F gold fetcher.

yfinance is mocked (no network, no real gold fetch). We verify the symbol
mapping (XAUUSD -> GC=F), the CSV cache format, and that non-gold symbols are
rejected with YahooFetchError.
"""

from datetime import datetime
from pathlib import Path
from unittest import mock

import pandas as pd
import pytest

import core_backend.market_context.yahoo as yf_mod


@pytest.fixture(autouse=True)
def _reset_cache():
    for p in Path("data/market_cache").glob("fmp_XAUUSD_*.csv"):
        p.unlink()
    yield
    for p in Path("data/market_cache").glob("fmp_XAUUSD_*.csv"):
        p.unlink()


def _fake_df():
    """yfinance 1.5+ style frame: MultiIndex columns, tz-aware index."""
    idx = pd.to_datetime(
        ["2024-01-01 00:01:00", "2024-01-01 00:02:00"]
    ).tz_localize("UTC")
    cols = pd.MultiIndex.from_tuples(
        [("Open", "GC=F"), ("High", "GC=F"), ("Low", "GC=F"),
         ("Close", "GC=F"), ("Volume", "GC=F")]
    )
    data = [
        [2050.0, 2051.0, 2049.0, 2050.5, 100],
        [2050.5, 2052.0, 2050.0, 2051.2, 120],
    ]
    return pd.DataFrame(data, index=idx, columns=cols)


def test_xauusd_maps_to_gcf_and_caches_csv():
    import yfinance as yf

    with mock.patch.object(yf, "download", return_value=_fake_df()) as dl:
        candles = yf_mod.fetch_historical_1min("XAUUSD")
    # called with the gold ticker (GC=F), not the symbol name (XAUUSD)
    assert dl.call_args.args[0] == "GC=F"
    assert len(candles) == 2
    assert candles[0].close == 2050.5
    # cache written in fmp-compatible format
    p = yf_mod.cache_path("XAUUSD")
    assert p.exists()
    text = p.read_text().splitlines()
    assert text[0] == "date,open,high,low,close,volume"
    assert "2050.5" in text[1]


def test_non_gold_symbol_rejected():
    with pytest.raises(yf_mod.YahooFetchError):
        yf_mod.fetch_historical_1min("EURUSD")


def test_empty_yahoo_response_raises():
    import yfinance as yf

    with mock.patch.object(yf, "download", return_value=pd.DataFrame()):
        with pytest.raises(yf_mod.YahooFetchError):
            yf_mod.fetch_historical_1min("XAUUSD", force=True)


def test_fetch_all_to_cache_returns_path():
    import yfinance as yf

    with mock.patch.object(yf, "download", return_value=_fake_df()):
        paths = yf_mod.fetch_all_to_cache(symbols=("XAUUSD",), force=True)
    assert "XAUUSD" in paths
    assert paths["XAUUSD"].name == "fmp_XAUUSD_1min.csv"

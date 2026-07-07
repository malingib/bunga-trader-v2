"""Tests for core_backend.market_context.fmp — FMP 1-min bar fetcher.

Network is mocked (no live FMP calls, no API key needed). Pure logic
(parse, block detection, caching) is exercised directly.
"""

import importlib
from datetime import datetime
from pathlib import Path
from unittest import mock

import pytest

import core_backend.market_context.fmp as fmp


# ──────────────────────────────────────────────
# Fixtures: reset module-level cache/key state
# ──────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_module():
    # clear any prior cache file for XAUUSD between tests
    for p in Path("data/market_cache").glob("fmp_XAUUSD_*.csv"):
        p.unlink()
    yield
    for p in Path("data/market_cache").glob("fmp_XAUUSD_*.csv"):
        p.unlink()


# ──────────────────────────────────────────────
# Block / paywall detection
# ──────────────────────────────────────────────

def test_detect_block_error_message():
    assert fmp._detect_block({"Error Message": "Invalid symbol"}) is not None


def test_detect_block_premium_note():
    assert fmp._detect_block({"Note": "premium endpoint"}) is not None


def test_detect_block_empty_list_allowed():
    # an empty list is a valid (if useless) payload, not a block
    assert fmp._detect_block([]) is None


def test_detect_block_real_list_allowed():
    assert fmp._detect_block([{"date": "2024-01-01 00:00:00"}]) is None


# ──────────────────────────────────────────────
# Parsing
# ──────────────────────────────────────────────

def test_parse_candles_sorts_chronologically():
    payload = [
        {"date": "2024-01-01 00:01:00", "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10},
        {"date": "2024-01-01 00:00:00", "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10},
    ]
    candles = fmp._parse_candles(payload)
    assert len(candles) == 2
    assert candles[0].time < candles[1].time  # sorted ascending


def test_parse_candles_skips_malformed():
    payload = [
        {"date": "bad-date", "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5},
        {"date": "2024-01-01 00:00:00", "open": "not-a-float", "high": 2.0, "low": 0.5, "close": 1.5},
        {"date": "2024-01-01 00:00:00", "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 7},
    ]
    candles = fmp._parse_candles(payload)
    assert len(candles) == 1
    assert candles[0].volume == 7.0


# ──────────────────────────────────────────────
# Date-range filtering
# ──────────────────────────────────────────────

def _mk(ts: str) -> fmp.Candle:
    t = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
    return fmp.Candle(t, 1.0, 2.0, 0.5, 1.5, 0.0)


def test_filter_range_window():
    candles = [_mk("2024-01-01 00:00:00"), _mk("2024-01-15 00:00:00"), _mk("2024-02-01 00:00:00")]
    out = fmp._filter_range(candles, fmp._as_date("2024-01-10"), fmp._as_date("2024-01-31"))
    assert len(out) == 1
    assert out[0].time.day == 15


# ──────────────────────────────────────────────
# Cache round-trip (no network)
# ──────────────────────────────────────────────

def test_cache_write_then_load():
    candles = [_mk("2024-01-01 00:00:00"), _mk("2024-01-01 00:01:00")]
    fmp._write_csv("XAUUSD", candles)
    loaded = fmp.load_cached_csv("XAUUSD")
    assert loaded is not None
    assert len(loaded) == 2
    assert loaded[0].close == 1.5


# ──────────────────────────────────────────────
# Network path (mocked) — caching + block handling
# ──────────────────────────────────────────────

def _fake_bars_payload():
    return [
        {"date": "2024-01-01 00:00:00", "open": 2050.0, "high": 2051.0, "low": 2049.0, "close": 2050.5, "volume": 123},
        {"date": "2024-01-01 00:01:00", "open": 2050.5, "high": 2052.0, "low": 2050.0, "close": 2051.0, "volume": 99},
    ]


def test_fetch_historical_caches_and_returns():
    payload = _fake_bars_payload()
    fake_resp = mock.Mock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = payload

    with mock.patch.dict("os.environ", {"FMP_API_KEY": "test-key"}), \
            mock.patch.object(fmp.httpx, "get", return_value=fake_resp) as m_get:
        bars = fmp.fetch_historical_1min("XAUUSD")

    assert m_get.called
    assert len(bars) == 2
    assert bars[0].time < bars[1].time
    # second call should hit cache, not network
    with mock.patch.dict("os.environ", {"FMP_API_KEY": "test-key"}), \
            mock.patch.object(fmp.httpx, "get") as m_get2:
        bars2 = fmp.fetch_historical_1min("XAUUSD")
    assert not m_get2.called
    assert bars2 == bars


def test_fetch_historical_raises_on_missing_key():
    with mock.patch.dict("os.environ", {}, clear=True):
        with pytest.raises(fmp.FMPFreeTierError):
            fmp.fetch_historical_1min("XAUUSD")


def test_fetch_historical_raises_on_block():
    fake_resp = mock.Mock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {"Note": "You have exceeded your API key quota"}

    with mock.patch.dict("os.environ", {"FMP_API_KEY": "test-key"}), \
            mock.patch.object(fmp.httpx, "get", return_value=fake_resp):
        with pytest.raises(fmp.FMPFreeTierError):
            fmp.fetch_historical_1min("XAUUSD")


def test_fetch_historical_raises_on_403():
    fake_resp = mock.Mock()
    fake_resp.status_code = 403

    with mock.patch.dict("os.environ", {"FMP_API_KEY": "test-key"}), \
            mock.patch.object(fmp.httpx, "get", return_value=fake_resp):
        with pytest.raises(fmp.FMPFreeTierError):
            fmp.fetch_historical_1min("XAUUSD")


# ──────────────────────────────────────────────
# fetch_all_to_cache — public driver for the loop
# ──────────────────────────────────────────────

def test_fetch_all_to_cache_returns_paths():
    payload = _fake_bars_payload()
    fake = mock.Mock(); fake.status_code = 200; fake.json.return_value = payload
    with mock.patch.dict("os.environ", {"FMP_API_KEY": "test-key"}), \
            mock.patch.object(fmp.httpx, "get", return_value=fake):
        paths = fmp.fetch_all_to_cache(
            symbols=("XAUUSD",), force=True
        )
    assert "XAUUSD" in paths
    assert paths["XAUUSD"].exists()
    assert paths["XAUUSD"].name == "fmp_XAUUSD_1min.csv"


def test_fetch_all_to_cache_raises_on_block():
    fake = mock.Mock(); fake.status_code = 200
    fake.json.return_value = {"Error Message": "premium"}
    with mock.patch.dict("os.environ", {"FMP_API_KEY": "test-key"}), \
            mock.patch.object(fmp.httpx, "get", return_value=fake):
        with pytest.raises(fmp.FMPFreeTierError):
            fmp.fetch_all_to_cache(symbols=("XAUUSD",), force=True)


def test_cache_path_accessor():
    p = fmp.cache_path("EURUSD")
    assert p.name == "fmp_EURUSD_1min.csv"


# ──────────────────────────────────────────────
# apikey is attached to every request (intraday + EOD fallback)
# ──────────────────────────────────────────────

def test_apikey_sent_on_intraday_request():
    payload = _fake_bars_payload()
    fake = mock.Mock(); fake.status_code = 200; fake.json.return_value = payload
    with mock.patch.dict("os.environ", {"FMP_API_KEY": "SECRET_KEY_XYZ"}), \
            mock.patch.object(fmp.httpx, "get", return_value=fake) as spy:
        fmp.fetch_historical_1min("XAUUSD", force=True)
    assert spy.called
    _, kwargs = spy.call_args
    assert kwargs.get("params", {}).get("apikey") == "SECRET_KEY_XYZ"
    assert kwargs["params"]["symbol"] == "XAUUSD"


def test_apikey_sent_on_eod_fallback_request():
    # intraday blocked -> fallback to EOD daily; both calls must carry apikey
    blocked = mock.Mock(); blocked.status_code = 200
    blocked.json.return_value = {"Error Message": "premium"}
    eod_payload = [{"date": "2024-01-01 00:00:00", "open": 1, "high": 2, "low": 0,
                    "close": 1, "volume": 5}]
    eod = mock.Mock(); eod.status_code = 200; eod.json.return_value = eod_payload

    calls = {"n": 0}

    def fake_get(url, params=None, timeout=None):
        calls["n"] += 1
        # first = intraday (blocked), second = eod (ok)
        return eod if calls["n"] > 1 else blocked

    with mock.patch.dict("os.environ", {"FMP_API_KEY": "SECRET_KEY_123"}), \
            mock.patch.object(fmp.httpx, "get", side_effect=fake_get) as spy:
        fmp.fetch_historical_1min("XAUUSD", force=True)
    assert spy.call_count == 2
    for _, kwargs in spy.call_args_list:
        assert kwargs.get("params", {}).get("apikey") == "SECRET_KEY_123"

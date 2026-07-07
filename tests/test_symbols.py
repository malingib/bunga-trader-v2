"""Symbol helper tests."""
from core_backend.symbols import (
    mt5_candidates,
    is_supported_symbol,
    normalize_signal_symbol,
    get_all_supported_symbols,
)


def test_normalize_gold_alias_to_xauusd():
    assert normalize_signal_symbol("gold") == "XAUUSD"


def test_normalize_eurusd():
    assert normalize_signal_symbol("EUR/USD") == "EURUSD"
    assert normalize_signal_symbol("eurusd") == "EURUSD"


def test_normalize_gbpusd():
    assert normalize_signal_symbol("GBP/USD") == "GBPUSD"
    assert normalize_signal_symbol("gbpusd") == "GBPUSD"


def test_supports_all_three():
    assert is_supported_symbol("GOLD") is True
    assert is_supported_symbol("XAUUSD") is True
    assert is_supported_symbol("EURUSD") is True
    assert is_supported_symbol("GBPUSD") is True


def test_rejects_unsupported():
    assert is_supported_symbol("USDJPY") is False
    assert is_supported_symbol("BTCUSD") is False
    assert is_supported_symbol("AAPL") is False


def test_gold_mt5_candidates():
    candidates = mt5_candidates("GOLD")
    assert candidates[0] == "XAUUSD"
    assert "XAUUSD" in candidates


def test_forex_mt5_candidates():
    eur = mt5_candidates("EURUSD")
    assert eur[0] == "EURUSD"

    gbp = mt5_candidates("GBPUSD")
    assert gbp[0] == "GBPUSD"


def test_get_all_supported_symbols():
    symbols = get_all_supported_symbols()
    assert "XAUUSD" in symbols
    assert "EURUSD" in symbols
    assert "GBPUSD" in symbols
    assert len(symbols) == 3

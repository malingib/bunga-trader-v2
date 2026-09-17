"""Tests for hard risk-geometry guardrails."""
from core_backend.models import ParsedSignal
from core_backend.risk_engine import validate_signal_risk


def signal(**kwargs):
    values = {"action": "BUY", "symbol": "EURUSD", "raw_text": "test", "entry_price": 1.10, "sl": 1.09, "tp": 1.12}
    values.update(kwargs)
    return ParsedSignal(**values)


def test_buy_requires_stop_below_entry():
    ok, reason = validate_signal_risk(signal(sl=1.10), 10_000)
    assert not ok
    assert "below entry" in reason


def test_sell_requires_stop_above_entry():
    ok, reason = validate_signal_risk(signal(action="SELL", sl=1.10, tp=1.08), 10_000)
    assert not ok
    assert "above entry" in reason


def test_buy_targets_must_be_above_entry():
    ok, reason = validate_signal_risk(signal(tp=1.09), 10_000)
    assert not ok
    assert "take profits" in reason


def test_non_finite_prices_are_rejected():
    ok, reason = validate_signal_risk(signal(tp=float("nan")), 10_000)
    assert not ok
    assert "non-finite" in reason


def test_sell_good_geometry_is_accepted():
    ok, reason = validate_signal_risk(signal(action="SELL", sl=1.11, tp=1.08), 10_000)
    assert ok
    assert reason is None

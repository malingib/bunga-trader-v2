"""Bunga Trader - Risk Engine Unit Tests"""
import pytest
from core_backend.risk_engine import (
    calculate_lot_size,
    get_pip_value_per_lot,
    get_instrument_type,
    validate_signal_risk,
)
from core_backend.models import ParsedSignal

class TestInstrumentType:
    def test_forex_major(self):
        assert get_instrument_type("EURUSD") == "FOREX"

    def test_jpy_pair(self):
        assert get_instrument_type("USDJPY") == "JPY"

    def test_gold(self):
        assert get_instrument_type("XAUUSD") == "GOLD"

    def test_crypto(self):
        assert get_instrument_type("BTCUSD") == "CRYPTO"

class TestPipValue:
    def test_eurusd(self):
        val = get_pip_value_per_lot("EURUSD")
        assert val == 10.0

    def test_gold(self):
        val = get_pip_value_per_lot("XAUUSD")
        assert val == 1.0

class TestLotCalculation:
    def test_standard_forex(self, risk_db_session):
        lot, error = calculate_lot_size(symbol="EURUSD", entry_price=1.1000, sl_price=1.0950, account_balance=10000.0, risk_percent=1.0)
        assert error is None
        assert lot > 0
        assert lot <= 1.0

    def test_jpy_pair(self, risk_db_session):
        lot, error = calculate_lot_size(symbol="USDJPY", entry_price=150.00, sl_price=149.50, account_balance=10000.0, risk_percent=1.0)
        assert error is None
        assert lot > 0

    def test_no_sl(self):
        lot, error = calculate_lot_size(symbol="EURUSD", entry_price=1.1000, sl_price=None, account_balance=10000.0)
        assert error is not None
        assert lot == 0.0

    def test_tight_sl(self, risk_db_session):
        lot, error = calculate_lot_size(symbol="EURUSD", entry_price=1.1000, sl_price=1.0999, account_balance=10000.0)
        assert error is not None


def _signal(**kwargs) -> ParsedSignal:
    defaults = {
        "raw_signal_id": 1,
        "action": "BUY",
        "symbol": "EURUSD",
        "raw_text": "test",
    }
    defaults.update(kwargs)
    return ParsedSignal(**defaults)


class TestValidateSignalRisk:
    def test_rejects_poor_rr(self):
        signal = _signal(entry_price=1.1000, sl=1.0950, tp=1.1020)
        valid, reason = validate_signal_risk(signal, 10000.0)
        assert valid is False
        assert reason is not None
        assert "R:R" in reason

    def test_accepts_good_rr(self):
        signal = _signal(entry_price=1.1000, sl=1.0950, tp=1.1100)
        valid, reason = validate_signal_risk(signal, 10000.0)
        assert valid is True
        assert reason is None

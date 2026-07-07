"""Bunga Trader - Parser Unit Tests"""
import pytest
from contextlib import contextmanager

from core_backend.models import ParsedSignal, RawSignal
from core_backend.parser import clean_text, parse_signal_text, process_raw_signal

class TestParser:
    def test_standard_format(self):
        text = "BUY XAUUSD 2350.00 SL 2335.00 TP 2380.00"
        result = parse_signal_text(text)
        assert result is not None
        assert result["action"] == "BUY"
        assert result["symbol"] == "XAUUSD"
        assert result["entry"] == 2350.0
        assert result["sl"] == 2335.0
        assert result["tp"] == 2380.0

    def test_at_colon_format(self):
        text = "SELL GOLD @ 2350.00 SL: 2360.00 TP: 2330.00"
        result = parse_signal_text(text)
        assert result is not None
        assert result["action"] == "SELL"
        assert result["symbol"] == "XAUUSD"

    def test_labeled_format(self):
        text = "BUY GOLD\nEntry: 2350.00\nSL: 2335.00\nTP: 2380.00"
        result = parse_signal_text(text)
        assert result is not None
        assert result["action"] == "BUY"

    def test_no_entry_format(self):
        text = "BUY GOLD SL 2335.00 TP 2380.00"
        result = parse_signal_text(text)
        assert result is not None
        assert result["entry"] is None
        assert result["sl"] == 2335.0
        assert result["tp"] == 2380.0

    def test_multi_tp_format(self):
        text = "BUY GOLD 2350.00 SL 2335.00 TP1 2360.00 TP2 2380.00 TP3 2400.00"
        result = parse_signal_text(text)
        assert result is not None
        assert result["tp"] == 2360.0
        assert result["tp2"] == 2380.0
        assert result["tp3"] == 2400.0

    def test_gold_symbol(self):
        text = "SELL XAUUSD 2000.50 SL 2010.00 TP 1990.00"
        result = parse_signal_text(text)
        assert result is not None
        assert result["symbol"] == "XAUUSD"

    def test_gold_alias_symbol(self):
        text = "BUY GOLD 2350.00 SL 2335.00 TP 2380.00"
        result = parse_signal_text(text)
        assert result is not None
        assert result["symbol"] == "XAUUSD"

    def test_invalid_signal(self):
        text = "Hello everyone, how are you today?"
        result = parse_signal_text(text)
        assert result is None

    def test_buy_limit(self):
        text = "BUY LIMIT GOLD 2350.00 SL 2335.00 TP 2380.00"
        result = parse_signal_text(text)
        assert result is not None
        assert result["action"] == "BUY_LIMIT"

    def test_non_gold_signal_is_ignored(self):
        """Unsupported symbols are still ignored."""
        text = "BUY USDJPY 150.00 SL 149.50 TP 150.50"
        result = parse_signal_text(text)
        assert result is None

class TestCleanText:
    def test_normalize_whitespace(self):
        text = "BUY   EURUSD   1.2500"
        cleaned = clean_text(text)
        assert "  " not in cleaned

    def test_uppercase(self):
        text = "buy eurusd 1.2500"
        cleaned = clean_text(text)
        assert cleaned == "BUY EURUSD 1.2500"


def test_process_raw_signal_parses_and_marks_processed(risk_db_session, monkeypatch):
    raw = RawSignal(
        channel_id="test",
        message_id=99,
        text="BUY GOLD 2350.00 SL 2335.00 TP 2380.00",
        processed=0,
    )
    risk_db_session.add(raw)
    risk_db_session.commit()
    risk_db_session.refresh(raw)

    @contextmanager
    def _get_db():
        try:
            yield risk_db_session
            risk_db_session.commit()
        except Exception:
            risk_db_session.rollback()
            raise

    monkeypatch.setattr("core_backend.parser.get_db", _get_db)

    created = process_raw_signal(raw.id)

    assert created is True
    refreshed_raw = risk_db_session.query(RawSignal).filter(RawSignal.id == raw.id).first()
    assert refreshed_raw is not None
    assert refreshed_raw.processed == 1
    parsed = risk_db_session.query(ParsedSignal).filter(ParsedSignal.raw_signal_id == raw.id).first()
    assert parsed is not None
    assert parsed.action == "BUY"
    assert parsed.symbol == "XAUUSD"

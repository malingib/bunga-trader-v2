"""Unit tests for the Opening Range Breakout strategy."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core_backend.strategies.opening_range_breakout import (
    OpeningRangeBreakoutConfig,
    OpeningRangeBreakoutStrategy,
)


NY_OPEN = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)


def _series(rows):
    times = [row[0] for row in rows]
    opens = [row[1] for row in rows]
    highs = [row[2] for row in rows]
    lows = [row[3] for row in rows]
    closes = [row[4] for row in rows]
    return times, opens, highs, lows, closes


def _cfg() -> OpeningRangeBreakoutConfig:
    return OpeningRangeBreakoutConfig(
        session="new_york",
        bar_minutes=1.0,
        opening_range_minutes=15,
        tick_size=0.01,
        min_or_width_ticks=10,
        require_retest=True,
        breakout_mode="close",
        rejection_mode="close_or_wick",
        min_quality_score=60.0,
    )


def _pre_session_rows(count: int = 200):
    rows = []
    for i in range(count):
        ts = NY_OPEN - timedelta(minutes=count - i)
        price = 100.0 + (0.02 if i % 2 == 0 else -0.02)
        rows.append((ts, price, price + 0.1, price - 0.1, price + 0.01))
    return rows


def _opening_range_rows():
    rows = [
        (100.0, 100.5, 99.9, 100.2),
        (100.2, 102.0, 100.1, 101.5),
        (101.5, 101.8, 99.0, 100.0),
    ]
    for i in range(12):
        price = 100.0 + (0.1 if i % 2 == 0 else -0.1)
        rows.append((price, price + 0.2, price - 0.2, price + 0.05))

    out = []
    for idx, (o, h, l, c) in enumerate(rows):
        out.append((NY_OPEN + timedelta(minutes=idx), o, h, l, c))
    return out


def _long_rows():
    rows = _pre_session_rows() + _opening_range_rows()
    after = [
        (100.1, 103.2, 102.4, 103.0),
        (102.9, 103.0, 102.1, 102.03),
        (102.0, 102.9, 101.95, 102.8),
    ]
    for idx, (o, h, l, c) in enumerate(after, start=15):
        rows.append((NY_OPEN + timedelta(minutes=idx), o, h, l, c))
    return rows


def _short_rows():
    rows = _pre_session_rows() + _opening_range_rows()
    after = [
        (98.6, 98.65, 97.8, 98.0),
        (98.1, 98.9, 98.0, 98.85),
        (98.9, 99.05, 98.0, 98.2),
    ]
    for idx, (o, h, l, c) in enumerate(after, start=15):
        rows.append((NY_OPEN + timedelta(minutes=idx), o, h, l, c))
    return rows


class TestOpeningRangeBreakout:
    def test_long_breakout_retest_rejection_emits_buy(self):
        times, opens, highs, lows, closes = _series(_long_rows())
        signals = OpeningRangeBreakoutStrategy(_cfg()).generate(
            opens, highs, lows, closes, times, symbol="XAUUSD"
        )

        assert len(signals) == 1
        sig = signals[0]
        assert sig["symbol"] == "XAUUSD"
        assert sig["action"] == "BUY"
        assert sig["signal_source"] == "opening_range_breakout"
        assert sig["entry_price"] == 102.8
        assert sig["sl"] < sig["entry_price"] < sig["tp"]
        assert sig["metadata"]["or_high"] == 102.0
        assert sig["metadata"]["or_low"] == 99.0
        assert sig["metadata"]["retest_index"] == 216

    def test_short_breakout_retest_rejection_emits_sell(self):
        times, opens, highs, lows, closes = _series(_short_rows())
        signals = OpeningRangeBreakoutStrategy(_cfg()).generate(
            opens, highs, lows, closes, times, symbol="XAUUSD"
        )

        assert len(signals) == 1
        sig = signals[0]
        assert sig["action"] == "SELL"
        assert sig["tp"] < sig["entry_price"] < sig["sl"]
        assert sig["metadata"]["broken_level"] == 99.0

    def test_no_signal_when_retest_never_happens(self):
        rows = _pre_session_rows() + _opening_range_rows()
        after = [
            (103.0, 104.0, 102.6, 103.8),
            (103.8, 104.5, 103.4, 104.2),
        ]
        for i in range(35):
            price = 104.0 + i * 0.01
            after.append((price, price + 0.2, price + 0.1, price + 0.15))
        for idx, (o, h, l, c) in enumerate(after, start=15):
            rows.append((NY_OPEN + timedelta(minutes=idx), o, h, l, c))

        times, opens, highs, lows, closes = _series(rows)
        signals = OpeningRangeBreakoutStrategy(_cfg()).generate(
            opens, highs, lows, closes, times, symbol="XAUUSD"
        )
        assert signals == []

    def test_pending_retest_cannot_enter_after_entry_cutoff(self):
        cfg = _cfg()
        cfg.max_entry_minutes = 90
        rows = _pre_session_rows() + _opening_range_rows()

        # Break out before the cutoff, then retest exactly at the cutoff.
        for minute in range(15, 90):
            rows.append(
                (
                    NY_OPEN + timedelta(minutes=minute),
                    102.8,
                    103.2,
                    102.6,
                    103.0,
                )
            )
        rows.append(
            (
                NY_OPEN + timedelta(minutes=90),
                102.9,
                103.0,
                102.0,
                102.8,
            )
        )

        times, opens, highs, lows, closes = _series(rows)
        signals = OpeningRangeBreakoutStrategy(cfg).generate(
            opens, highs, lows, closes, times, symbol="XAUUSD"
        )
        assert signals == []

    def test_check_latest_only_returns_signal_on_latest_bar(self):
        rows = _long_rows()
        times, opens, highs, lows, closes = _series(rows)
        strat = OpeningRangeBreakoutStrategy(_cfg())

        latest = strat.check_latest(opens, highs, lows, closes, symbol="XAUUSD", times=times)
        assert latest is not None
        assert latest["action"] == "BUY"

        shorter = rows[:-1]
        times2, opens2, highs2, lows2, closes2 = _series(shorter)
        assert strat.check_latest(opens2, highs2, lows2, closes2, symbol="XAUUSD", times=times2) is None

    def test_quality_gate_rejects_below_threshold(self):
        # The quality gate must actually filter. A strong breakout that the
        # old code scored at the 95 cap (>= min_quality_score, emitted) must
        # now score below a high threshold and be suppressed.
        cfg = _cfg()
        cfg.min_quality_score = 95.0  # above any realistic additive score
        times, opens, highs, lows, closes = _series(_long_rows())
        signals = OpeningRangeBreakoutStrategy(cfg).generate(
            opens, highs, lows, closes, times, symbol="XAUUSD"
        )
        assert signals == []

    def test_requires_timestamps(self):
        rows = _long_rows()
        _, opens, highs, lows, closes = _series(rows)
        strat = OpeningRangeBreakoutStrategy(_cfg())
        assert strat.check_latest(opens, highs, lows, closes, symbol="XAUUSD", times=None) is None

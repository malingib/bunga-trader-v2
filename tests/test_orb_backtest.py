"""Tests for the ORB backtest harness."""

from __future__ import annotations

from datetime import timedelta

from backtests.engine_corrected import Bars
from backtests.engine_orb import run_orb_backtest
from tests.test_opening_range_breakout import NY_OPEN, _long_rows, _series


def _bars_from_rows(rows) -> Bars:
    times, opens, highs, lows, closes = _series(rows)
    return Bars(
        o=opens,
        h=highs,
        l=lows,
        c=closes,
        date=[t.isoformat() for t in times],
    )


def test_run_orb_backtest_closes_signal_with_next_bar_entry():
    rows = _long_rows()
    last_ts = rows[-1][0]
    for i in range(1, 131):
        ts = last_ts + timedelta(minutes=i)
        price = 103.0 + i * 0.001
        rows.append((ts, price, price + 0.2, price - 0.1, price + 0.05))

    bars = _bars_from_rows(rows)
    res = run_orb_backtest(
        "XAUUSD",
        bars,
        session="new_york",
        opening_range_minutes=15,
        tick_size=0.01,
        min_or_width_ticks=10,
        min_quality_score=60.0,
        spread_points=0.0,
        slippage_points=0.0,
        entry_offset_bars=1,
    )

    assert res.trades == 1
    assert res.wins == 1
    assert res.final_equity > 1000.0
    assert "signals=1" in res.notes

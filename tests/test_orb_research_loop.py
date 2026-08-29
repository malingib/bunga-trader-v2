"""Tests for the ORB research loop's validation safeguards."""

from __future__ import annotations

from backtests.engine_corrected import Bars
from backtests.orb_research_loop import _review, split_bars


def _bars(days: int = 4, bars_per_day: int = 3) -> Bars:
    dates = []
    for day in range(days):
        for minute in range(bars_per_day):
            dates.append(f"2026-01-{day + 1:02d} 09:{minute:02d}:00")
    values = [100.0 + i for i in range(len(dates))]
    return Bars(o=values, h=[v + 1 for v in values], l=[v - 1 for v in values], c=values, date=dates)


def test_split_bars_uses_day_boundary():
    train, test = split_bars(_bars(), train_fraction=0.5)
    assert train.date[-1].startswith("2026-01-02")
    assert test.date[0].startswith("2026-01-03")


def test_review_rejects_train_only_edge():
    class Result:
        trades = 10
        profit_factor = 1.8
        avg_r = 0.2
        max_dd_pct = 5.0

    test = Result()
    test.profit_factor = 0.8
    test.avg_r = -0.1
    review = _review("XAUUSD", {}, Result(), test, min_trades=5)
    assert review.verdict == "overfit_reject"


def test_review_rejects_test_only_edge():
    class Result:
        trades = 10
        profit_factor = 0.8
        avg_r = -0.1
        max_dd_pct = 5.0

    test = Result()
    test.profit_factor = 1.4
    test.avg_r = 0.2
    review = _review("XAUUSD", {}, Result(), test, min_trades=5)
    assert review.verdict == "reject"

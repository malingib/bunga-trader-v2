"""P&L sign correctness — the single source of truth in risk_engine.compute_pnl.

Regression guard for a bug where SELL P&L was negated AFTER an abs(),
producing a wrong (often inverted) sign that corrupted per-symbol stats,
daily P&L, and consecutive-loss risk gating.
"""
import os

os.environ.setdefault("TG_API_ID", "1")
os.environ.setdefault("TG_API_HASH", "x")
os.environ.setdefault("TG_PHONE", "+100****0000")
os.environ.setdefault("SIGNAL_CHANNELS", "test")
os.environ.setdefault("GOOGLE_API_KEY", "test")

import pytest

from core_backend.risk_engine import compute_pnl


def test_buy_win_positive():
    # Gold: pip_size 0.01, pip_value 1.0. +10 price, 0.1 lot.
    pnl = compute_pnl("XAUUSD", "BUY", 2000.0, 2010.0, 0.1)
    assert pnl == pytest.approx(100.0), pnl
    assert pnl > 0


def test_buy_loss_negative():
    pnl = compute_pnl("XAUUSD", "BUY", 2000.0, 1990.0, 0.1)
    assert pnl == pytest.approx(-100.0), pnl
    assert pnl < 0


def test_sell_win_positive_price_fell():
    # SELL at 2000, exit 1990 (price fell) must be a WIN (+P&L).
    pnl = compute_pnl("XAUUSD", "SELL", 2000.0, 1990.0, 0.1)
    assert pnl == pytest.approx(100.0), pnl
    assert pnl > 0


def test_sell_loss_negative_price_rose():
    pnl = compute_pnl("XAUUSD", "SELL", 2000.0, 2010.0, 0.1)
    assert pnl == pytest.approx(-100.0), pnl
    assert pnl < 0


def test_buy_limit_and_sell_stop_directions():
    # Pending order variants must map to the same direction as their side.
    assert compute_pnl("XAUUSD", "BUY_LIMIT", 2000.0, 2010.0, 0.1) > 0
    assert compute_pnl("XAUUSD", "SELL_STOP", 2000.0, 1990.0, 0.1) > 0


def test_index_pnl_scales_with_pip_value():
    # SP500: pip_size 1.0, pip_value 50.0. +50 points, 0.1 lot -> 250.
    pnl = compute_pnl("SP500", "BUY", 5000.0, 5050.0, 0.1)
    assert pnl == pytest.approx(250.0), pnl

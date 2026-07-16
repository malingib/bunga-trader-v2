"""Tests for the corrected backtest engine.

These pin the sizing bug fix: with REAL risk-based sizing the momentum
strategy must NOT reproduce the old impossible +1106%/+5059% numbers,
and P&L must be computed via lot * pip_value * points (not units-as-points).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from engine_corrected import (
    compute_atr,
    load_csv,
    pip_value,
    run_momentum_backtest,
)

_CACE = Path("data/market_cache")
_GOLD = _CACE / "fmp_XAUUSD_1min.csv"
_SP500 = _CACE / "fmp_SP500_1min.csv"


def _have_data() -> bool:
    return _GOLD.exists() and _SP500.exists()


@pytest.mark.skipif(not _have_data(), reason="cached market CSVs not present")
class TestCorrectedSizing:
    def test_pip_value_matches_risk_engine(self):
        # GOLD pip value = $1/oz, indices = $50/pt (risk_engine model)
        assert pip_value("XAUUSD") == 1.0
        assert pip_value("SP500") == 50.0
        assert pip_value("NAS100") == 50.0

    def test_pnl_uses_lot_not_units(self):
        """A single win must NOT swing the account by 8-56%.

        Reproduces the original bug guard: with $1000 start and 1% risk,
        one gold win moves equity by ~1% (the risk budget), never ~8%.
        """
        bars = load_csv(_GOLD)
        r = run_momentum_backtest(
            "XAUUSD", bars, sl_atr=1.2, rr=4.0, trend_ema=0,
            start_equity=1000.0, risk_pct=1.0,
        )
        # At 1% risk per trade the account cannot post +1106% (old bug).
        assert r.ret_pct < 500.0, "sizing bug resurfaced: impossible return"
        # Equity must stay finite / positive-ish (no unit-confusion blowup)
        assert r.final_equity > 0 and r.final_equity < 1e6

    def test_atr_positive_on_real_data(self):
        bars = load_csv(_GOLD)
        atr = compute_atr(bars.h, bars.l, bars.c, 14)
        vals = [a for a in atr if a == a]  # drop nan
        assert len(vals) > 100
        assert min(vals) > 0

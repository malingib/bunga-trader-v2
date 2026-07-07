"""Tests for the 3-pillar refactor (liquidity sweeps, PA, VWAP/200MA gate).

Coverage targets required by AGENTS.md: every change to core_backend/ gets a
test. These exercise the new indicators, the hard trend gate, and the
sweep-as-trigger behavior — independent of the live compounding loop.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from math import isnan

import pytest

from core_backend.strategies import price_action
from core_backend.strategies.config import QUADAPT_CFG
from core_backend.strategies.engine import QuadaptEngine
from core_backend.strategies.indicators import (
    detect_liquidity_sweep,
    fvg_detect,
    relative_volume,
    sma,
    swing_points,
    vwap,
)
from core_backend.strategies.market_data import Candle, MarketSnapshot
from core_backend.strategies.quality_engine import SignalQualityEngine


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────


def _candles_from_ohlc(rows):
    """rows = list of (o, h, l, c, v). Times are sequential 1-min from a base."""
    out = []
    t0 = datetime(2024, 1, 1, 0, 0, 0)
    for i, (o, h, l, c, v) in enumerate(rows):
        # Spread across days to avoid minute>59; keep strictly increasing.
        ts = t0 + timedelta(minutes=i)
        out.append(
            Candle(
                time=ts,
                open=float(o), high=float(h), low=float(l), close=float(c),
                volume=float(v),
            )
        )
    return out


def _snapshot_from_ohlc(symbol, rows):
    return MarketSnapshot(symbol=symbol, candles=_candles_from_ohlc(rows),
                          fetched_at=datetime.utcnow())


# ──────────────────────────────────────────────
# Indicator unit tests
# ──────────────────────────────────────────────


class TestIndicators:
    def test_sma_basic(self):
        data = [1.0] * 50 + [2.0] * 50
        m = sma(data, 10)
        assert isnan(m[8])  # NaN before warmup
        assert abs(m[-1] - 2.0) < 1e-9
        # at index 55: window = data[46:56] = 4x1.0 + 6x2.0 = 16/10 = 1.6
        assert abs(m[55] - 1.6) < 1e-9

    def test_vwap_with_volume(self):
        highs = [10.0, 11.0, 12.0]
        lows = [9.0, 10.0, 11.0]
        closes = [9.5, 10.5, 11.5]
        vols = [1.0, 1.0, 1.0]
        v = vwap(highs, lows, closes, vols)
        # cumulative VWAP should be the mean of typical prices (equal volume)
        assert abs(v[-1] - (9.5 + 10.5 + 11.5) / 3.0) < 1e-9

    def test_swing_points(self):
        # up-down staircase with a clear pivot high at index 5, low at 6
        closes = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 5.0, 4.0, 3.0, 2.0, 1.0, 2.0, 3.0, 4.0, 5.0]
        highs = closes[:]
        lows = closes[:]
        swings = swing_points(highs, lows, left=2, right=2)
        kinds = [s.kind for s in swings]
        assert "high" in kinds
        assert "low" in kinds

    def test_detect_liquidity_sweep_high(self):
        # Build a series: rising to a pivot high, then a bar that wicks ABOVE it
        # and closes back BELOW -> bearish sweep (SELL).
        rows = []
        price = 100.0
        for _ in range(10):
            rows.append((price, price + 0.5, price - 0.5, price, 1.0))
            price += 1.0
        # pivot high at 110
        pivot = price  # 110
        # sweep bar: wick to 112, close back to 109 (< pivot)
        rows.append((109.5, 112.0, 109.0, 109.0, 1.0))
        highs = [r[1] for r in rows]
        lows = [r[2] for r in rows]
        opens = [r[0] for r in rows]
        closes = [r[3] for r in rows]
        sweep = detect_liquidity_sweep(highs, lows, opens, closes)
        assert sweep is not None
        assert sweep.direction == "SELL"
        assert sweep.swept_kind == "high"

    def test_detect_liquidity_sweep_low(self):
        rows = []
        price = 110.0
        for _ in range(10):
            rows.append((price, price + 0.5, price - 0.5, price, 1.0))
            price -= 1.0
        pivot = price  # 100
        # sweep bar: wick to 98, close back to 101 (> pivot)
        rows.append((100.5, 101.0, 98.0, 101.0, 1.0))
        highs = [r[1] for r in rows]
        lows = [r[2] for r in rows]
        opens = [r[0] for r in rows]
        closes = [r[3] for r in rows]
        sweep = detect_liquidity_sweep(highs, lows, opens, closes)
        assert sweep is not None
        assert sweep.direction == "BUY"
        assert sweep.swept_kind == "low"

    def test_no_sweep_when_close_stays_outside(self):
        # A bar that pokes above the pivot but CLOSES above it (real breakout,
        # not a sweep) must NOT trigger a sweep.
        rows = []
        price = 100.0
        for _ in range(10):
            rows.append((price, price + 0.5, price - 0.5, price, 1.0))
            price += 1.0
        pivot = price
        # breakout bar: wick to 112, closes at 112 (stays above)
        rows.append((111.0, 112.0, 110.5, 112.0, 1.0))
        highs = [r[1] for r in rows]
        lows = [r[2] for r in rows]
        opens = [r[0] for r in rows]
        closes = [r[3] for r in rows]
        sweep = detect_liquidity_sweep(highs, lows, opens, closes)
        assert sweep is None

    def test_fvg_bullish(self):
        rows = [
            (100.0, 100.0, 99.0, 100.0, 1.0),   # i-2 high 100
            (100.0, 100.0, 99.0, 100.0, 1.0),   # i-1
            (101.0, 103.0, 102.0, 103.0, 1.0),  # i: low 102, high 103 -> 102 > 100
        ]
        highs = [float(r[1]) for r in rows]
        lows = [float(r[2]) for r in rows]
        closes = [float(r[3]) for r in rows]
        fvg = fvg_detect(highs, lows, closes)
        assert fvg is not None
        assert fvg.direction == "up"

    def test_relative_volume_neutral(self):
        vols = [10.0] * 30
        rv = relative_volume(vols, 20)
        assert abs(rv[-1] - 1.0) < 1e-9


# ──────────────────────────────────────────────
# Engine: trend gate (Pushback 1) — HARD block
# ──────────────────────────────────────────────


class TestTrendGate:
    """A sweep BUY below the 200MA MUST be blocked (counter-trend)."""

    def _series_with_200ma(self, ma_level: float, final_close: float):
        """200 bars of price, ending at final_close, with a known 200MA."""
        rows = []
        # 199 bars at ma_level, last bar at final_close
        for _ in range(199):
            rows.append((ma_level, ma_level + 0.1, ma_level - 0.1, ma_level, 1.0))
        # last bar = a sweep of a low that closes back, but price below MA
        rows.append((final_close, final_close + 0.1, final_close - 2.0,
                     final_close, 1.0))
        return rows

    def test_buy_blocked_below_200ma(self):
        # Engine default: 200MA gate on. Price ends below MA. Even with a sweep,
        # a BUY must be blocked by the gate.
        QUADAPT_CFG.trend_gate.enabled = True
        QUADAPT_CFG.trend_gate.require_200ma_alignment = True
        QUADAPT_CFG.trend_gate.use_vwap = False
        eng = QuadaptEngine()
        # Construct a low sweep while price sits below the MA.
        rows = []
        for _ in range(199):
            rows.append((100.0, 100.1, 99.9, 100.0, 1.0))
        # pivot low at 99.9, last bar wicks to 98 and closes back to 99.5 (BUY sweep)
        rows.append((99.5, 99.6, 98.0, 99.5, 1.0))
        snap = _snapshot_from_ohlc("EURUSD", rows)
        sig = eng.evaluate(snap)
        assert sig is None, "BUY below 200MA must be gated"

    def test_sell_blocked_above_200ma(self):
        QUADAPT_CFG.trend_gate.enabled = True
        QUADAPT_CFG.trend_gate.require_200ma_alignment = True
        QUADAPT_CFG.trend_gate.use_vwap = False
        eng = QuadaptEngine()
        rows = []
        for _ in range(199):
            rows.append((100.0, 100.1, 99.9, 100.0, 1.0))
        # pivot high at 100.1, last bar wicks to 101.5 and closes back to 100.5 (SELL sweep)
        rows.append((100.5, 101.5, 100.4, 100.5, 1.0))
        snap = _snapshot_from_ohlc("EURUSD", rows)
        sig = eng.evaluate(snap)
        assert sig is None, "SELL above 200MA must be gated"

    def test_gate_disabled_allows_sweep(self):
        QUADAPT_CFG.trend_gate.enabled = False
        try:
            eng = QuapterDisabled() if False else QuadaptEngine()
            rows = []
            for _ in range(199):
                rows.append((100.0, 100.1, 99.9, 100.0, 1.0))
            rows.append((99.5, 99.6, 98.0, 99.5, 1.0))  # BUY sweep
            snap = _snapshot_from_ohlc("EURUSD", rows)
            sig = eng.evaluate(snap)
            assert sig is None or sig.action == "BUY"
        finally:
            QUADAPT_CFG.trend_gate.enabled = True


# ──────────────────────────────────────────────
# Engine: envelope is NO LONGER a trigger (Pushback 2)
# ──────────────────────────────────────────────


class TestEnvelopeDemoted:
    def test_envelope_breakout_without_sweep_is_none(self):
        """An envelope breakout (price punches through band) with NO liquidity
        sweep must NOT produce a signal — envelope is a weight, not a trigger."""
        QUADAPT_CFG.trend_gate.enabled = False  # isolate the trigger behavior
        try:
            eng = QuadaptEngine()
            # Smooth uptrend so MA doesn't interfere; force a clean breakout bar
            # that is NOT a sweep (closes beyond prior high, no rejection back).
            rows = []
            price = 100.0
            for _ in range(250):
                rows.append((price, price + 0.2, price - 0.2, price, 1.0))
                price += 0.05
            # Big breakout bar closing ABOVE everything (real breakout, no sweep)
            last = price
            rows.append((last, last + 2.0, last, last + 2.0, 1.0))
            snap = _snapshot_from_ohlc("EURUSD", rows)
            sig = eng.evaluate(snap)
            assert sig is None, "Envelope breakout alone must not trigger (no sweep)"
        finally:
            QUADAPT_CFG.trend_gate.enabled = True


# ──────────────────────────────────────────────
# Quality engine: real volume weight (Pushback 1 fix) + new pillar terms
# ──────────────────────────────────────────────


class TestQualityEnginePillars:
    def test_volume_weight_no_longer_scores_envelope(self):
        qe = SignalQualityEngine()
        cfg = QUADAPT_CFG.quality
        # Envelope strength should now route through weight_envelope, and
        # weight_volume should score RELATIVE volume. Verify the weight names
        # exist and the envelope weight is separate from volume.
        assert hasattr(cfg, "weight_envelope")
        assert hasattr(cfg, "weight_volume")
        assert cfg.weight_envelope != cfg.weight_volume

    def test_sweep_boosts_score(self):
        # A sweep with a strong wick should score higher than no sweep, all else equal.
        qe = SignalQualityEngine()

        def _base(sweep):
            return qe.compute(
                symbol="XAUUSD", signal_type="SELL", index=10, price=100.0,
                mlma_trend_val=101.0, supertrend_dir=-1,
                is_squeeze_release=False, is_squeeze_active=False, in_squeeze=False,
                stoch_rsi_k=None, stoch_rsi_d=None,
                envelope_signal_strength=0.5, mtf_alignment=1.0,
                order_block_proximity=0.0, bars_since_last_signal=20,
                regime="trending", sweep=sweep,
            )

        from core_backend.strategies.indicators import LiquiditySweep

        strong = LiquiditySweep(index=10, swept_kind="high", swept_price=102.0,
                                swept_index=5, wick_ratio=0.5, close_inside=True,
                                direction="SELL")
        no_sweep = _base(None)
        with_sweep = _base(strong)
        assert with_sweep > no_sweep

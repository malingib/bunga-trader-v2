"""Tests for the new strategy engine modules.

Mocks market data so tests stay offline and deterministic.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from core_backend.strategies.engine import QuadaptEngine, StrategySignal, detect_regime
from core_backend.strategies.quality_engine import SignalQualityEngine
from core_backend.strategies.config import QUADAPT_CFG
from core_backend.strategies.indicators import LiquiditySweep
from core_backend.strategies.price_action import MarketStructure


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────


def _make_candle(close: float, open_: float | None = None) -> MagicMock:
    c = MagicMock()
    c.close = float(close)
    c.open = float(open_ or close)
    c.high = float(close) * 1.001
    c.low = float(close) * 0.999
    c.volume = 100.0
    c.time = datetime.now(timezone.utc).replace(tzinfo=None)
    return c


def _snapshot(symbol: str, closes, highs, lows):
    from core_backend.strategies.market_data import Candle, MarketSnapshot
    from datetime import timezone

    candles = []
    for i in range(len(closes)):
        c = Candle(
            time=datetime.now(timezone.utc).replace(tzinfo=None),
            open=float(highs[i] + lows[i]) / 2.0,
            high=float(highs[i]),
            low=float(lows[i]),
            close=float(closes[i]),
            volume=100.0,
        )
        candles.append(c)

    return MarketSnapshot(symbol=symbol, candles=candles, fetched_at=datetime.now(timezone.utc).replace(tzinfo=None))


def _snapshot_with_times(symbol: str, rows):
    from core_backend.strategies.market_data import Candle, MarketSnapshot

    candles = []
    for ts, o, h, l, c in rows:
        candles.append(
            Candle(
                time=ts,
                open=float(o),
                high=float(h),
                low=float(l),
                close=float(c),
                volume=100.0,
            )
        )
    return MarketSnapshot(
        symbol=symbol,
        candles=candles,
        fetched_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )


# ──────────────────────────────────────────────
# Market regime detection
# ──────────────────────────────────────────────


class TestMarketRegime:
    def test_trending_when_volatile(self):
        closes = [1.0] + [1.0 + (0.02 if i % 2 == 0 else -0.02) for i in range(50)]
        assert detect_regime(closes, lookback=50) == "trending"

    def test_ranging_when_small_changes(self):
        closes = [1.0 + i * 0.00005 for i in range(60)]
        assert detect_regime(closes, lookback=50) == "ranging"

    def test_fewer_candles_than_lookback_defaults_ranging(self):
        closes = [1.0, 1.0001]
        assert detect_regime(closes, lookback=50) == "ranging"


# ──────────────────────────────────────────────
# Quality engine
# ──────────────────────────────────────────────


class TestQualityEngine:
    def test_perfect_alignment_scores_high(self):
        qe = SignalQualityEngine()
        # Post 3-pillar refactor: a quality signal REQUIRES a liquidity sweep.
        # Build a strong sweep + CHoCH-aligned PA (the primary-edge path).
        sweep = LiquiditySweep(
            index=10,
            swept_kind="low",
            swept_price=1.095,
            swept_index=5,
            wick_ratio=0.5,  # strong stop-hunt wick
            close_inside=True,
            direction="BUY",
        )
        struct = MarketStructure(bias="bullish", last_choch="BUY", last_bos="BUY")
        score = qe.compute(
            symbol="EURUSD",
            signal_type="BUY",
            index=10,
            price=1.1,
            mlma_trend_val=1.08,
            supertrend_dir=1,
            is_squeeze_release=True,
            is_squeeze_active=False,
            in_squeeze=False,
            stoch_rsi_k=22.0,
            stoch_rsi_d=24.0,
            envelope_signal_strength=1.0,
            mtf_alignment=1.0,
            order_block_proximity=1.0,
            bars_since_last_signal=20,
            regime="trending",
            sweep=sweep,
            pa_displacement=2.0,  # conviction candle >= 1.5x ATR
            pa_structure=struct,
            has_fvg=True,
            rel_volume=2.0,
        )
        assert score >= 60.0

    def test_meets_threshold_reflects_config(self):
        qe = SignalQualityEngine()
        assert qe.meets_threshold(qe.cfg.min_quality_score - 0.1) is False
        assert qe.meets_threshold(qe.cfg.min_quality_score) is True


# ──────────────────────────────────────────────
# Engine pipeline
# ──────────────────────────────────────────────


class TestEnginePipeline:
    def test_returns_none_when_too_few_candles(self):
        engine = QuadaptEngine()
        closes = [1.0, 1.0001]
        highs = [1.001, 1.0011]
        lows = [0.999, 0.9991]
        snapshot = _snapshot("EURUSD", closes, highs, lows)
        assert engine.evaluate(snapshot) is None

    def test_rate_limit_blocks_fetch(self):
        engine = QuadaptEngine()
        engine.cfg.market_data.poll_interval_seconds = 60
        with patch(
            "core_backend.strategies.engine.fetch_market_data",
            side_effect=RuntimeError("rate limit hit"),
        ):
            signals = engine.run_poll()
            assert signals == []


class TestORBPerSymbolConfig:
    """require_retest is resolved per-symbol by _run_orb_poll via the same
    sym_cfg.get(...) pattern. Verify the live config honors per-symbol overrides
    (XAUUSD/NAS100 -> retest=True, SP500 -> retest=False) and falls back to the
    global default for symbols without an explicit override (EURUSD/GBPUSD)."""

    def _resolved(self, symbol: str) -> bool:
        oc = QUADAPT_CFG.orb
        sym_cfg = oc.defaults.get(symbol, {})
        return sym_cfg.get("require_retest", oc.require_retest)

    def test_xauusd_requires_retest(self):
        assert self._resolved("XAUUSD") is True

    def test_nas100_requires_retest(self):
        assert self._resolved("NAS100") is True

    def test_sp500_skips_retest(self):
        assert self._resolved("SP500") is False

    def test_unknown_symbol_falls_back_to_global_default(self):
        # No override -> inherits the global require_retest (currently True).
        assert self._resolved("BTCUSD") is QUADAPT_CFG.orb.require_retest


class TestORBPolling:
    def test_run_poll_routes_to_orb_when_enabled(self):
        engine = QuadaptEngine()
        engine.cfg.orb.enabled = True
        engine.cfg.orb.warmup = 1
        engine.cfg.momentum.enabled = False
        engine.cfg.market_data.symbols = ["XAUUSD"]

        bar_time = datetime.now(timezone.utc).replace(tzinfo=None)
        snapshot = _snapshot_with_times("XAUUSD", [(bar_time, 100.0, 101.0, 99.0, 100.5)])
        fake_result = {
            "symbol": "XAUUSD",
            "action": "BUY",
            "entry_price": 100.5,
            "sl": 99.0,
            "tp": 102.75,
            "quality_score": 82.0,
            "signal_source": "opening_range_breakout",
            "confidence": "high",
            "generated_at": bar_time.isoformat(),
            "hold_bars": 120,
            "metadata": {"bar_time": bar_time.isoformat()},
        }

        class _FakeOrbStrategy:
            def __init__(self, cfg):
                self.cfg = cfg

            def check_latest(self, opens, highs, lows, closes, symbol="XAUUSD", times=None):
                return fake_result

        with patch(
            "core_backend.strategies.engine.fetch_market_data",
            return_value=snapshot,
        ), patch(
            "core_backend.strategies.engine.OpeningRangeBreakoutStrategy",
            _FakeOrbStrategy,
        ):
            signals = engine.run_poll()

        assert len(signals) == 1
        assert signals[0].signal_source == "opening_range_breakout"
        assert signals[0].action == "BUY"

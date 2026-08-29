"""Quadapt ML Trader — Main Orchestration Engine.

Ties together: market data → indicators → envelope signals
→ quality scoring (MTF, StochRSI, Supertrend, Squeeze, Order Blocks)
→ risk (TP/SL) → output trade signal.
"""

from __future__ import annotations

import math
import os
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .config import QUADAPT_CFG
from .indicators import (
    atr,
    bars_since,
    crossover,
    crossunder,
    detect_order_blocks,
    detect_liquidity_sweep,
    envelope_bands,
    fvg_detect,
    heikin_ashi,
    mlma_trend,
    relative_volume,
    rsi,
    sma,
    stoch_rsi,
    supertrend,
    swing_points,
    ttm_squeeze,
    vwap,
)
from . import price_action
from .market_data import MarketSnapshot, fetch_market_data
from .momentum_breakout import MomentumBreakoutStrategy, MomentumConfig as MomCfg
from .opening_range_breakout import (
    OpeningRangeBreakoutStrategy,
    OpeningRangeBreakoutConfig as OrbCfg,
)
from .quality_engine import SignalQualityEngine
from .risk import RiskCalculator
from ..logger import setup_logger

logger = setup_logger("QuadaptEngine")


# ──────────────────────────────────────────────
# Output signal model
# ──────────────────────────────────────────────


class StrategySignal:
    """A trade signal produced by the strategy engine."""

    def __init__(
        self,
        *,
        symbol: str,
        action: str,
        entry_price: float,
        sl: float,
        tp: float,
        tp2: float = 0.0,
        tp3: float = 0.0,
        quality_score: float,
        signal_source: str,
        confidence: str = "medium",
        generated_at: Optional[datetime] = None,
        hold_bars: int = 0,
        metadata: Optional[dict] = None,
    ):
        self.symbol = symbol
        self.action = action  # BUY or SELL
        self.entry_price = entry_price
        self.sl = sl
        self.tp = tp
        self.tp2 = tp2
        self.tp3 = tp3
        self.quality_score = quality_score
        self.signal_source = signal_source
        self.confidence = confidence
        self.generated_at = generated_at or datetime.now(timezone.utc).replace(tzinfo=None)
        # For time-based (mean-reversion) exits: hold this many bars, then close
        # at market. 0 = use SL/TP only (no time exit).
        self.hold_bars = hold_bars
        self.metadata = metadata or {}

    def to_dict(self) -> dict:
        """Serialize for pipeline input and ML logging."""
        return {
            "symbol": self.symbol,
            "action": self.action,
            "entry_price": self.entry_price,
            "sl": self.sl,
            "tp": self.tp,
            "tp2": self.tp2,
            "tp3": self.tp3,
            "quality_score": self.quality_score,
            "confidence": self.confidence,
            "signal_source": self.signal_source,
            "generated_at": self.generated_at.isoformat(),
            "metadata": self.metadata,
        }

    def to_parsed_signal_dict(self) -> dict:
        """Convert to the format expected by Bunga's ParsedSignal."""
        return {
            "symbol": self.symbol,
            "action": self.action,
            "entry_price": self.entry_price,
            "sl": self.sl,
            "tp": self.tp,
            "tp2": self.tp2,
            "tp3": self.tp3,
            "raw_text": f"[Strategy] {self.signal_source}: {self.action} {self.symbol} "
            f"@{self.entry_price} | SL: {self.sl} TP: {self.tp} | Score: {self.quality_score}",
            "ai_score": self.quality_score / 100.0,
        }


# ──────────────────────────────────────────────
# Market Regime Detection
# ──────────────────────────────────────────────


def detect_regime(closes: List[float], lookback: int = 50) -> str:
    """Classify market regime as 'trending' or 'ranging'."""
    if len(closes) < lookback:
        return "ranging"

    window = closes[-lookback:]
    changes = [abs(window[i] - window[i - 1]) / window[i - 1] for i in range(1, len(window))]
    avg_change = sum(changes) / len(changes) if changes else 0

    # Simple heuristic: high avg change = trending, low = ranging
    if avg_change > 0.0015:  # ~0.15% per bar average
        return "trending"
    return "ranging"


# ──────────────────────────────────────────────
# Quadapt Engine
# ──────────────────────────────────────────────


class QuadaptEngine:
    """Main strategy engine — runs the full Quadapt pipeline on market data."""

    def __init__(self) -> None:
        self.cfg = QUADAPT_CFG
        self.quality_engine = SignalQualityEngine()
        self.risk_calc = RiskCalculator()

        # Track last signal per symbol (for clustering prevention)
        self._last_signal: Dict[str, Tuple[datetime, float]] = {}
        # Per-direction dedup for momentum path (symbol+action -> generated_at)
        self._last_momentum_signal: Dict[str, datetime] = {}
        # Per-bar dedup for ORB path (symbol+action+bar_time -> generated_at)
        self._last_orb_signal: Dict[str, datetime] = {}

    def _mean_reversion_trigger(
        self,
        closes: List[float],
        highs: List[float],
        lows: List[float],
        opens: List[float],
    ) -> Optional[Tuple[str, int, float]]:
        """Mean-reversion entry trigger — the proven edge on XAUUSD 1-min.

        The edge is a **StochRSI %K cross out of an extreme**:
          - BUY  when %K crosses UP through %D while %K < stoch_rsi_oversold
          - SELL when %K crosses DOWN through %D while %K > stoch_rsi_overbought

        This waits for the actual momentum TURN (not just an extreme reading),
        which is what survives realistic next-bar-open fill. The exit is
        time-based (close at market after `hold_bars`), which avoids 1-min
        stop-clipping that kills fixed SL/TP reversion. A wide protective stop
        guards runaway gaps.

        Returns (direction, hold_bars, protective_sl_atr) or None.
        """
        cfg = self.cfg.trigger
        n = len(closes)
        if n < cfg.stoch_rsi_rsi_length + cfg.stoch_rsi_stoch_length + 5:
            return None
        i = n - 1

        k_line, d_line = stoch_rsi(
            closes, cfg.stoch_rsi_rsi_length, cfg.stoch_rsi_stoch_length,
            cfg.stoch_rsi_smooth_k, cfg.stoch_rsi_smooth_d,
        )
        k, k_prev = k_line[i], k_line[i - 1]
        d, d_prev = d_line[i], d_line[i - 1]
        if math.isnan(k) or math.isnan(d) or math.isnan(k_prev) or math.isnan(d_prev):
            return None
        crossed_up = (k_prev <= d_prev) and (k > d)
        crossed_down = (k_prev >= d_prev) and (k < d)

        # Optional raw-RSI filter: require RSI also on the extreme side.
        if cfg.require_rsi_filter:
            r_vals = rsi(closes, cfg.rsi_period)
            r = r_vals[i]
            if r is None or math.isnan(r):
                return None
            if crossed_up and r >= 50:
                return None
            if crossed_down and r <= 50:
                return None

        # Optional range-stretch: price must be stretched vs 20-bar mean.
        if cfg.require_range_stretch:
            look = min(20, n)
            mean = sum(closes[-look:]) / look
            atr_vals = atr(highs, lows, closes, 14)
            a = atr_vals[-1] if atr_vals and not math.isnan(atr_vals[-1]) else 0.0
            if a <= 0:
                a = closes[-1] * 0.005
            dist = abs(closes[-1] - mean)
            if dist < cfg.range_stretch_pct * a:
                return None

        if crossed_up and k < cfg.stoch_rsi_oversold:
            return ("BUY", cfg.hold_bars, cfg.protective_sl_atr)
        if crossed_down and k > cfg.stoch_rsi_overbought:
            return ("SELL", cfg.hold_bars, cfg.protective_sl_atr)
        return None

    def evaluate(self, snapshot: MarketSnapshot) -> Optional[StrategySignal]:
        """Run the full strategy pipeline on a market snapshot.

        Returns a StrategySignal if conditions are met, None otherwise.
        """
        symbol = snapshot.symbol
        candles = snapshot.candles

        if len(candles) < 70:  # minimum bars for any meaningful signal
            logger.warning(f"Too few candles for {symbol}: {len(candles)}")
            return None

        # ── Extract OHLC ──
        opens = [c.open for c in candles]
        highs = [c.high for c in candles]
        lows = [c.low for c in candles]
        closes = [c.close for c in candles]
        volumes = [c.volume for c in candles]

        # ── Step 1: Heikin Ashi smoothing (stolen from Scalping Pullback) ──
        if self.cfg.envelopes.use_heikin_ashi:
            ha_opens, ha_highs, ha_lows, ha_closes = heikin_ashi(
                opens, highs, lows, closes
            )
            env_data = ha_closes
            env_highs = ha_highs
            env_lows = ha_lows
        else:
            env_data = closes
            env_highs = highs
            env_lows = lows

        # ── Step 2: ATR (needed by envelopes + risk) ──
        atr_vals = atr(highs, lows, closes, self.cfg.envelopes.atr_period)
        avg_atr = sum(atr_vals[-15:]) / 15 if len(atr_vals) >= 15 else atr_vals[-1]
        last_close = closes[-1]
        envelope_mult_primary = self.cfg.envelopes.envelope_mult_primary
        if self.cfg.envelopes.adaptive_envelope_widening:
            atr_perc = avg_atr / last_close if last_close else 0.0
            if atr_perc > self.cfg.envelopes.volatility_widening_atr_threshold:
                envelope_mult_primary = min(
                    self.cfg.envelopes.max_envelope_multiplier, envelope_mult_primary * 1.5
                )

        # ── Step 3: TRIGGER (selected by cfg.trigger.mode) ──
        # Envelope is DEMOTED to a confluence weight (it is NO LONGER a trigger).
        # Sweeps/PA/gate run on RAW candles; only the envelope weight uses HA.
        i = len(closes) - 1  # current index

        # PA/score scratch defaults (set properly in Step 3c / 3d).
        disp: Optional[float] = None
        struct = None
        fvg = None
        sweep = None
        reversion = False  # True when the mean-reversion trigger fired

        # 200MA (pillar ③) computed on RAW closes — gates counter-trend entries.
        ma200 = sma(closes, self.cfg.trend_gate.ma_period)
        ma200_val = ma200[i] if i < len(ma200) and not math.isnan(ma200[i]) else None

        # Envelope strength kept ONLY as a weight (not a trigger).
        _, upper_p, lower_p = envelope_bands(
            env_data,
            self.cfg.envelopes.length_primary,
            envelope_mult_primary,
            atr_vals,
            self.cfg.envelopes.min_envelope_pct,
        )
        env_buy = (
            not math.isnan(upper_p[i]) and not math.isnan(lower_p[i])
            and last_close > upper_p[i]
        )
        env_sell = (
            not math.isnan(upper_p[i]) and not math.isnan(lower_p[i])
            and last_close < lower_p[i]
        )
        if env_buy and env_sell:
            envelope_strength = 1.0
        elif env_buy or env_sell:
            envelope_strength = 0.7
        else:
            envelope_strength = 0.5

        # ── DISPATCH on trigger mode ──
        rev_hold_bars = 0
        rev_protective_sl = 0.0
        if self.cfg.trigger.mode == "mean_reversion":
            rev = self._mean_reversion_trigger(closes, highs, lows, opens)
            if rev is not None:
                signal_type, rev_hold_bars, rev_protective_sl = rev
                reversion = True
                swings = swing_points(
                    highs, lows, self.cfg.sweep.swing_left, self.cfg.sweep.swing_right
                )
            else:
                signal_type = None
                swings = None
        else:
            # ── Liquidity sweep trigger on RAW OHLC (sweeps are real-price events) ──
            swings = swing_points(highs, lows, self.cfg.sweep.swing_left, self.cfg.sweep.swing_right)
            if self.cfg.sweep.enabled:
                sweep = detect_liquidity_sweep(
                    highs, lows, opens, closes,
                    swings=swings,
                    swing_lookback=self.cfg.sweep.swing_lookback,
                    min_wick_ratio=self.cfg.sweep.min_wick_ratio,
                )
            signal_type = sweep.direction if sweep else None

        if signal_type is None:
            # No trigger -> no trade. The envelope is a weight, not a trigger.
            return None

        # ── Step 3b: HARD trend GATE (pillar ③) — blocks counter-trend entries ──
        # In mean-reversion mode the gate is SKIPPED on purpose: a reversion BUY
        # is *supposed* to fire when price is stretched below the MA/VWAP.
        if not reversion and self.cfg.trend_gate.enabled and ma200_val is not None:
            band = self.cfg.trend_gate.vwap_band_pct * last_close
            if self.cfg.trend_gate.require_200ma_alignment:
                if signal_type == "BUY" and last_close < ma200_val:
                    logger.debug(
                        f"{symbol}: 200MA gate blocked BUY "
                        f"(price {last_close:.5f} < MA {ma200_val:.5f})"
                    )
                    return None
                if signal_type == "SELL" and last_close > ma200_val:
                    logger.debug(
                        f"{symbol}: 200MA gate blocked SELL "
                        f"(price {last_close:.5f} > MA {ma200_val:.5f})"
                    )
                    return None
            if self.cfg.trend_gate.use_vwap:
                vw = vwap(highs, lows, closes, volumes)
                vw_val = vw[i] if i < len(vw) and not math.isnan(vw[i]) else None
                if vw_val is not None:
                    if signal_type == "BUY" and last_close < vw_val - band:
                        logger.debug(f"{symbol}: VWAP gate blocked BUY (below VWAP)")
                        return None
                    if signal_type == "SELL" and last_close > vw_val + band:
                        logger.debug(f"{symbol}: VWAP gate blocked SELL (above VWAP)")
                        return None

        # ── Step 3c: PA confirmation (pillar ②) — tightens the entry ──
        pa = self.cfg.price_action
        # Always compute PA artifacts for scoring, even if a sub-gate is off.
        disp = price_action.displacement(
            opens, closes, highs, lows,
            atr_period=self.cfg.envelopes.atr_period,
            mult=pa.displacement_mult,
        )
        struct = price_action.classify_structure(highs, lows, swings=swings)
        fvg = fvg_detect(highs, lows, closes) if pa.use_fvg_boost else None
        if pa.enabled:
            # In mean-reversion mode the PA hard-gates are skipped: a reversion
            # fade fires ON a stretched/weak candle, so displacement + CHoCH
            # filters would reject exactly the setups that have edge.
            if not reversion:
                if pa.require_displacement and disp < pa.displacement_mult:
                    logger.debug(f"{symbol}: PA displacement too weak ({disp:.2f}xATR)")
                    return None
                if pa.require_choch_alignment and struct.last_choch:
                    if signal_type == "BUY" and struct.last_choch == "SELL":
                        logger.debug(f"{symbol}: PA CHoCH bias contradicts BUY")
                        return None
                    if signal_type == "SELL" and struct.last_choch == "BUY":
                        logger.debug(f"{symbol}: PA CHoCH bias contradicts SELL")
                        return None
            if pa.min_rejection_wick > 0:
                tail, _ = price_action.wick_ratio(highs, lows, opens, closes)
                if tail < pa.min_rejection_wick:
                    logger.debug(f"{symbol}: rejection wick too small ({tail:.2f})")
                    return None

        # ── Step 3d: Volume confirm (pillar ③, advisory — OFF for FX w/ no real vol) ──
        rel_vol = None
        if self.cfg.trend_gate.require_volume_spike:
            rv = relative_volume(volumes, self.cfg.trend_gate.volume_sma_period)
            rel_vol = rv[i] if i < len(rv) and not math.isnan(rv[i]) else None
            if rel_vol is not None and rel_vol < self.cfg.trend_gate.volume_spike_mult:
                logger.debug(f"{symbol}: volume spike gate blocked ({rel_vol:.2f}x)")
                return None

        # ── Step 4: Barssince guard (stolen from Scalping Pullback) ──
        if self.cfg.envelopes.use_barssince_guard:
            # Was price inside the envelope recently?
            inside_primary = [
                not math.isnan(upper_p[j])
                and not math.isnan(lower_p[j])
                and lower_p[j] <= env_data[j] <= upper_p[j]
                for j in range(len(env_data))
            ]
            bars_in = bars_since(inside_primary, self.cfg.envelopes.barssince_lookback + 1)
            if bars_in > self.cfg.envelopes.barssince_lookback:
                logger.debug(f"{symbol}: barssince guard blocked — last inside was {bars_in} bars ago")
                return None

        # ── Step 5: MLMA trend ──
        mlma_val = None
        if self.cfg.mlma.enabled:
            mlma_line = mlma_trend(
                env_data, self.cfg.mlma.length, self.cfg.mlma.kernel, self.cfg.mlma.gamma
            )
            mlma_val = mlma_line[i] if not math.isnan(mlma_line[i]) else None

        # ── Step 6: Supertrend direction (stolen from StochRSI+Supertrend) ──
        st_dir = None
        if self.cfg.supertrend.enabled:
            _, st_direction = supertrend(
                highs,
                lows,
                closes,
                self.cfg.supertrend.atr_period,
                self.cfg.supertrend.factor,
            )
            st_dir = st_direction[i] if i < len(st_direction) else None

        # ── Step 7: StochRSI (stolen from StochRSI+Supertrend) ──
        stoch_k = None
        stoch_d = None
        if self.cfg.stoch_rsi.enabled:
            k_line, d_line = stoch_rsi(
                closes,
                self.cfg.stoch_rsi.rsi_length,
                self.cfg.stoch_rsi.stoch_length,
                self.cfg.stoch_rsi.smooth_k,
                self.cfg.stoch_rsi.smooth_d,
            )
            stoch_k = k_line[i] if not math.isnan(k_line[i]) else None
            stoch_d = d_line[i] if not math.isnan(d_line[i]) else None

        # ── Step 8: TTM Squeeze (stolen from TTM Squeeze) ──
        sq_active = False
        sq_released = False
        in_squeeze = False
        if self.cfg.ttm.enabled:
            squeeze_active, _, squeeze_released = ttm_squeeze(
                highs,
                lows,
                closes,
                self.cfg.ttm.bb_length,
                self.cfg.ttm.bb_mult,
                self.cfg.ttm.kc_length,
                self.cfg.ttm.kc_mult,
            )
            sq_active = squeeze_active[i] if i < len(squeeze_active) else False
            sq_released = squeeze_released[i] if i < len(squeeze_released) else False
            in_squeeze = sq_active or False

        # ── Step 9: Order blocks ──
        ob_proximity = 0.0
        ob_high = None
        ob_low = None
        if self.cfg.order_blocks.enabled:
            blocks = detect_order_blocks(
                highs,
                lows,
                opens,
                closes,
                volumes,
                self.cfg.order_blocks.lookback,
                self.cfg.order_blocks.min_block_strength,
            )
            # Find nearest order block
            for ob in blocks:
                if signal_type == "BUY" and ob.block_type == "bullish":
                    if ob.high >= last_close >= ob.low:
                        ob_proximity = max(ob_proximity, ob.strength)
                        if ob_high is None:
                            ob_high, ob_low = ob.high, ob.low
                elif signal_type == "SELL" and ob.block_type == "bearish":
                    if ob.high >= last_close >= ob.low:
                        ob_proximity = max(ob_proximity, ob.strength)
                        if ob_high is None:
                            ob_high, ob_low = ob.high, ob.low

        # ── Step 10: MTF alignment (simplified — compare with longer/short timeframes) ──
        # Since we only have one resolution, we estimate MTF alignment from
        # how many other timeframe-like signals agree
        mtf_score = 0.5  # neutral default
        agreement = 0
        total = 0

        # Check: Supertrend agrees with signal direction
        if st_dir is not None:
            total += 1
            if (signal_type == "BUY" and st_dir == 1) or (
                signal_type == "SELL" and st_dir == -1
            ):
                agreement += 1

        # Check: MLMA agrees
        if mlma_val is not None:
            total += 1
            if (signal_type == "BUY" and last_close > mlma_val) or (
                signal_type == "SELL" and last_close < mlma_val
            ):
                agreement += 1

        if total > 0:
            mtf_score = agreement / total

        # ── Step 11: Market regime ──
        regime = detect_regime(closes, self.cfg.adaptive.regime_lookback)
        logger.debug(f"{symbol} regime: {regime}")

        # ── Step 12: Clustering prevention ──
        bars_since_last = 999
        if symbol in self._last_signal:
            last_time, last_price = self._last_signal[symbol]
            bars_since_last = bars_since(
                [c.time >= last_time for c in candles],
                self.cfg.adaptive.cluster_lookback_bars + 1,
            )
            # Also check price distance
            if bars_since_last < self.cfg.adaptive.cluster_lookback_bars:
                price_dist = abs(last_close - last_price) / last_price * 100
                if price_dist < self.cfg.adaptive.cluster_price_distance_pct:
                    logger.debug(
                        f"{symbol}: cluster prevention blocked — only {bars_since_last} bars "
                        f"since last signal at {last_price}"
                    )
                    return None

        # ── Step 13: Compute quality score ──
        # envelope_strength is already computed at Step 3 (demoted weight).

        quality_score = self.quality_engine.compute(
            symbol=symbol,
            signal_type=signal_type,
            index=i,
            price=last_close,
            mlma_trend_val=mlma_val,
            supertrend_dir=st_dir,
            is_squeeze_release=sq_released,
            is_squeeze_active=sq_active,
            in_squeeze=in_squeeze,
            stoch_rsi_k=stoch_k,
            stoch_rsi_d=stoch_d,
            envelope_signal_strength=envelope_strength,
            mtf_alignment=mtf_score,
            order_block_proximity=ob_proximity,
            bars_since_last_signal=bars_since_last,
            regime=regime,
            sweep=sweep,
            pa_displacement=disp,
            pa_structure=struct,
            has_fvg=bool(fvg),
            rel_volume=rel_vol,
            reversion_signal=reversion,
        )

        if not self.quality_engine.meets_threshold(quality_score):
            logger.info(
                f"{symbol}: signal quality {quality_score} below threshold "
                f"{self.cfg.quality.min_quality_score} — SKIPPED"
            )
            return None

        # ── Step 14: Calculate entry price ──
        entry_price = last_close

        # ── Step 15: Calculate SL ──
        if reversion and rev_protective_sl > 0:
            # Reversion uses a WIDE protective stop (time-exit is the real exit).
            # This guards runaway gaps; it is never meant to be the primary exit.
            a = atr_vals[-1] if atr_vals and not math.isnan(atr_vals[-1]) else entry_price * 0.005
            prot = a * rev_protective_sl
            sl_price = entry_price - prot if signal_type == "BUY" else entry_price + prot
        else:
            sl_price = self.risk_calc.calculate_sl(
                signal_type=signal_type,
                entry_price=entry_price,
                highs=highs,
                lows=lows,
                closes=closes,
                order_block_high=ob_high,
                order_block_low=ob_low,
                sweep_level=sweep.swept_price if sweep else None,
            )

        # ── Step 16: Calculate TP levels ──
        if reversion:
            # No fixed TP — the trade is closed at market after hold_bars.
            tp1 = tp2 = tp3 = 0.0
        else:
            tp_levels = self.risk_calc.calculate_tp_levels(
                signal_type=signal_type,
                entry_price=entry_price,
                sl_price=sl_price,
                highs=highs,
                lows=lows,
                closes=closes,
                sweep_level=sweep.swept_price if sweep else None,
            )
            tp1 = tp_levels[0] if len(tp_levels) > 0 else 0.0
            tp2 = tp_levels[1] if len(tp_levels) > 1 else 0.0
            tp3 = tp_levels[2] if len(tp_levels) > 2 else 0.0

        # ── Step 17: Final RR sanity check ──
        # Skipped for reversion (time-exit model, no fixed RR).
        if not reversion and tp1 > 0:
            rr = self.risk_calc.calculate_rr(entry_price, sl_price, tp1)
            if rr < self.cfg.risk.min_rr_ratio:
                logger.info(
                    f"{symbol}: RR {rr} below {self.cfg.risk.min_rr_ratio} — SKIPPED"
                )
                return None

        # ── Step 18: Build signal ──
        confidence = "high" if quality_score >= 80 else "medium"
        signal = StrategySignal(
            symbol=symbol,
            action=signal_type,
            entry_price=entry_price,
            sl=sl_price,
            tp=tp1,
            tp2=tp2,
            tp3=tp3,
            quality_score=quality_score,
            signal_source="Quadapt_ML_Trader",
            confidence=confidence,
            hold_bars=rev_hold_bars,
            metadata={
                "mlma_value": mlma_val,
                "supertrend_dir": st_dir,
                "stoch_rsi_k": stoch_k,
                "stoch_rsi_d": stoch_d,
                "squeeze_release": sq_released,
                "squeeze_active": sq_active,
                "order_block_proximity": ob_proximity,
                "mtf_alignment": mtf_score,
                "regime": regime,
                "envelope_strength": envelope_strength,
                "bars_since_last_signal": bars_since_last,
                "atr": atr_vals[-1] if atr_vals else None,
                # NEW pillar diagnostics (for calibration / verification)
                "sweep_direction": sweep.direction if sweep else None,
                "sweep_wick_ratio": sweep.wick_ratio if sweep else None,
                "pa_choch": struct.last_choch if struct else None,
                "pa_displacement": disp,
                "has_fvg": bool(fvg),
                "rel_volume": rel_vol,
                "ma200": ma200_val,
            },
        )

        # ── Track last signal ──
        self._last_signal[symbol] = (signal.generated_at, signal.entry_price)

        logger.info(
            f"✨ {symbol} {signal_type} @ {entry_price:.5f} "
            f"| SL: {sl_price:.5f} TP: {tp1:.5f} "
            f"| Score: {quality_score} | {confidence}"
        )

        return signal

    def run_poll(self) -> List[StrategySignal]:
        """Fetch market data for all symbols and evaluate.

        Returns list of generated signals (one per symbol at most per poll).
        """
        # Use ORB breakout strategy when configured; it takes precedence over
        # momentum so the two 1-min breakout paths are not polled simultaneously.
        if self.cfg.orb.enabled:
            return self._run_orb_poll()

        # Use momentum breakout strategy when configured
        if self.cfg.momentum.enabled:
            return self._run_momentum_poll()

        signals: List[StrategySignal] = []
        for symbol in self.cfg.market_data.symbols:
            try:
                snapshot = fetch_market_data(symbol, interval=self.cfg.market_data.interval)
                signal = self.evaluate(snapshot)
                if signal:
                    signals.append(signal)
            except Exception as e:
                logger.error(f"Error evaluating {symbol}: {e}")
                continue
        return signals

    def _run_momentum_poll(self) -> List[StrategySignal]:
        """Run momentum breakout strategy for all configured symbols."""
        mc = self.cfg.momentum
        signals: List[StrategySignal] = []
        for symbol in self.cfg.market_data.symbols:
            try:
                snapshot = fetch_market_data(symbol, interval=self.cfg.market_data.interval)
                if not snapshot or len(snapshot.closes) < mc.warmup:
                    continue
                # Build per-symbol config
                sym_cfg = mc.defaults.get(symbol, {})
                cfg = MomCfg(
                    lookback=mc.lookback,
                    sl_atr=sym_cfg.get("sl_atr", 1.2),
                    rr=sym_cfg.get("rr", 1.5),
                    max_hold=mc.max_hold,
                    atr_period=mc.atr_period,
                    trend_ema=sym_cfg.get("trend_ema", 0),
                    warmup=mc.warmup,
                )
                strat = MomentumBreakoutStrategy(cfg)
                result = strat.check_latest(
                    snapshot.opens, snapshot.highs,
                    snapshot.lows, snapshot.closes,
                    symbol=symbol,
                )
                if result is None:
                    continue
                sig = StrategySignal(
                    symbol=result["symbol"],
                    action=result["action"],
                    entry_price=result["entry_price"],
                    sl=result["sl"],
                    tp=result["tp"],
                    quality_score=result["quality_score"],
                    signal_source=result["signal_source"],
                    confidence=result["confidence"],
                    hold_bars=result["hold_bars"],
                    generated_at=datetime.fromisoformat(result["generated_at"]),
                    metadata=result.get("metadata"),
                )

                # Per-direction dedup: skip same symbol+direction within 120s
                dedup_key = f"{symbol}:{sig.action}"
                if dedup_key in self._last_momentum_signal:
                    elapsed = (sig.generated_at - self._last_momentum_signal[dedup_key]).total_seconds()
                    if elapsed < 120:
                        continue

                signals.append(sig)
                self._last_momentum_signal[dedup_key] = sig.generated_at
                # Store same format as evaluate() so cluster prevention works cross-path
                self._last_signal[symbol] = (sig.generated_at, sig.entry_price)

                logger.info(
                    f"✨ [Momentum] {symbol} {sig.action} @ {sig.entry_price:.2f} "
                    f"| SL: {sig.sl:.2f} TP: {sig.tp:.2f}"
                )
            except Exception as e:
                logger.error(f"Error in momentum poll for {symbol}: {e}")
                continue
        return signals

    def _run_orb_poll(self) -> List[StrategySignal]:
        """Run opening-range breakout strategy for all configured symbols."""
        oc = self.cfg.orb
        signals: List[StrategySignal] = []
        for symbol in self.cfg.market_data.symbols:
            try:
                snapshot = fetch_market_data(symbol, interval=self.cfg.market_data.interval)
                if not snapshot or len(snapshot.closes) < oc.warmup:
                    continue

                sym_cfg = oc.defaults.get(symbol, {})
                cfg = OrbCfg(
                    session=sym_cfg.get("session", oc.session),
                    bar_minutes=sym_cfg.get("bar_minutes", oc.bar_minutes),
                    opening_range_minutes=sym_cfg.get("opening_range_minutes", oc.opening_range_minutes),
                    breakout_buffer_pct=sym_cfg.get("breakout_buffer_pct", oc.breakout_buffer_pct),
                    breakout_atr_mult=sym_cfg.get("breakout_atr_mult", oc.breakout_atr_mult),
                    retest_tolerance_pct=sym_cfg.get("retest_tolerance_pct", oc.retest_tolerance_pct),
                    retest_or_width_pct=sym_cfg.get("retest_or_width_pct", oc.retest_or_width_pct),
                    retest_atr_mult=sym_cfg.get("retest_atr_mult", oc.retest_atr_mult),
                    retest_window_minutes=sym_cfg.get("retest_window_minutes", oc.retest_window_minutes),
                    rejection_window_minutes=sym_cfg.get("rejection_window_minutes", oc.rejection_window_minutes),
                    max_entry_minutes=sym_cfg.get("max_entry_minutes", oc.max_entry_minutes),
                    max_trades_per_session=sym_cfg.get("max_trades_per_session", oc.max_trades_per_session),
                    sl_atr=sym_cfg.get("sl_atr", oc.sl_atr),
                    stop_atr_mult=sym_cfg.get("stop_atr_mult", oc.stop_atr_mult),
                    rr=sym_cfg.get("rr", oc.rr),
                    max_hold_minutes=sym_cfg.get("max_hold_minutes", oc.max_hold_minutes),
                    atr_period=sym_cfg.get("atr_period", oc.atr_period),
                    tick_size=sym_cfg.get("tick_size", oc.tick_size),
                    min_or_width_ticks=sym_cfg.get("min_or_width_ticks", oc.min_or_width_ticks),
                    min_or_width_atr=sym_cfg.get("min_or_width_atr", oc.min_or_width_atr),
                    max_or_width_atr=sym_cfg.get("max_or_width_atr", oc.max_or_width_atr),
                    require_retest=sym_cfg.get("require_retest", oc.require_retest),
                    breakout_mode=sym_cfg.get("breakout_mode", oc.breakout_mode),
                    rejection_mode=sym_cfg.get("rejection_mode", oc.rejection_mode),
                    min_quality_score=sym_cfg.get("min_quality_score", oc.min_quality_score),
                    max_quality_score=sym_cfg.get("max_quality_score", oc.max_quality_score),
                )
                strat = OpeningRangeBreakoutStrategy(cfg)
                times = [candle.time for candle in snapshot.candles]
                result = strat.check_latest(
                    snapshot.opens,
                    snapshot.highs,
                    snapshot.lows,
                    snapshot.closes,
                    symbol=symbol,
                    times=times,
                )
                if result is None:
                    continue

                sig = StrategySignal(
                    symbol=result["symbol"],
                    action=result["action"],
                    entry_price=result["entry_price"],
                    sl=result["sl"],
                    tp=result["tp"],
                    quality_score=result["quality_score"],
                    signal_source=result["signal_source"],
                    confidence=result["confidence"],
                    hold_bars=result["hold_bars"],
                    generated_at=datetime.fromisoformat(result["generated_at"]),
                    metadata=result.get("metadata"),
                )

                bar_time = (result.get("metadata") or {}).get("bar_time", sig.generated_at.isoformat())
                dedup_key = f"{symbol}:{sig.action}:{bar_time}"
                if dedup_key in self._last_orb_signal:
                    continue

                if len(self._last_orb_signal) > 1000:
                    self._last_orb_signal.clear()

                signals.append(sig)
                self._last_orb_signal[dedup_key] = sig.generated_at
                self._last_signal[symbol] = (sig.generated_at, sig.entry_price)

                logger.info(
                    f"✨ [ORB] {symbol} {sig.action} @ {sig.entry_price:.5f} "
                    f"| SL: {sig.sl:.5f} TP: {sig.tp:.5f} | Score: {sig.quality_score}"
                )
            except Exception as e:
                logger.error(f"Error in ORB poll for {symbol}: {e}")
                continue
        return signals

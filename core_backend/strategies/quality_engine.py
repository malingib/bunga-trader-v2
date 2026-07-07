"""Signal quality scoring engine.

Computes a 0-100 quality score for each potential signal by evaluating:
  - Trend alignment (MLMA + Supertrend agreement)
  - MTF agreement (multi-timeframe direction consensus)
  - Squeeze release boost (TTM)
  - Momentum timing (StochRSI cross)
  - Order block support/resistance proximity
  - Clustering penalty (too close to previous signal)
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

from .config import QUADAPT_CFG
from .indicators import LiquiditySweep
from .price_action import MarketStructure


class SignalQualityEngine:
    """Computes quality scores for potential trade signals."""

    def __init__(self) -> None:
        cfg = QUADAPT_CFG.quality
        self.cfg = cfg

    def compute(
        self,
        *,
        symbol: str,
        signal_type: str,  # "BUY" or "SELL"
        index: int,
        price: float,
        mlma_trend_val: Optional[float],
        supertrend_dir: Optional[int],
        is_squeeze_release: bool,
        is_squeeze_active: bool,
        in_squeeze: bool,
        stoch_rsi_k: Optional[float],
        stoch_rsi_d: Optional[float],
        envelope_signal_strength: float,
        mtf_alignment: float,  # 0.0 - 1.0
        order_block_proximity: float,  # 0.0 - 1.0
        bars_since_last_signal: int,
        regime: str,
        # NEW pillar inputs
        sweep: Optional[LiquiditySweep] = None,
        pa_displacement: Optional[float] = None,
        pa_structure: Optional[MarketStructure] = None,
        has_fvg: bool = False,
        rel_volume: Optional[float] = None,
    ) -> float:
        """Compute quality score 0-100 for a potential signal.

        Higher = more confidence.  Threshold is configurable (default 60).
        """
        score = 0.0
        max_score = self.cfg.max_quality_score
        weights = self.cfg
        total_weight = 0.0

        # ── 1. Trend alignment (MLMA + Supertrend) ──
        trend_aligned = 0.0
        if mlma_trend_val is not None:
            if signal_type == "BUY" and price > mlma_trend_val:
                trend_aligned += 0.5
            elif signal_type == "SELL" and price < mlma_trend_val:
                trend_aligned += 0.5
        if supertrend_dir is not None:
            if signal_type == "BUY" and supertrend_dir == 1:
                trend_aligned += 0.5
            elif signal_type == "SELL" and supertrend_dir == -1:
                trend_aligned += 0.5
        score += trend_aligned * 100.0 * weights.weight_trend_alignment
        total_weight += weights.weight_trend_alignment

        # ── 2. MTF alignment ──
        score += mtf_alignment * 100.0 * weights.weight_mtf_alignment
        total_weight += weights.weight_mtf_alignment

        # ── 3. Squeeze release boost (volatility expansion) ──
        vol_boost = 0.0
        if is_squeeze_release:
            vol_boost = 1.0  # full boost
        elif not in_squeeze:
            vol_boost = 0.5  # already expanded, moderate boost
        score += vol_boost * 100.0 * weights.weight_volatility
        total_weight += weights.weight_volatility

        # ── 4. Momentum timing (StochRSI) ──
        mom_score = 0.0
        if stoch_rsi_k is not None and stoch_rsi_d is not None:
            if signal_type == "BUY" and stoch_rsi_k < 30 and stoch_rsi_k > stoch_rsi_d:
                mom_score = 1.0
            elif (
                signal_type == "BUY" and stoch_rsi_k < 20
            ):
                mom_score = 0.7  # oversold but no cross yet
            elif signal_type == "SELL" and stoch_rsi_k > 70 and stoch_rsi_k < stoch_rsi_d:
                mom_score = 1.0
            elif (
                signal_type == "SELL" and stoch_rsi_k > 80
            ):
                mom_score = 0.7
        score += mom_score * 100.0 * weights.weight_momentum
        total_weight += weights.weight_momentum

        # ── 5. Order block proximity ──
        score += order_block_proximity * 100.0 * weights.weight_order_block
        total_weight += weights.weight_order_block

        # ── 6. Envelope signal strength (DEMOTED: weight, not trigger) ──
        score += min(envelope_signal_strength, 1.0) * 100.0 * weights.weight_envelope
        total_weight += weights.weight_envelope

        # ── 7. Liquidity sweep (pillar ①) — primary edge ──
        sweep_score = 0.0
        if sweep is not None:
            # Base: a valid sweep-with-rejection is high conviction.
            sweep_score = 0.7
            # Stronger rejection wick (bigger stop-hunt) = higher quality.
            if sweep.wick_ratio >= 0.4:
                sweep_score = 1.0
            elif sweep.wick_ratio >= 0.3:
                sweep_score = 0.85
        score += sweep_score * 100.0 * weights.weight_liquidity_sweep
        total_weight += weights.weight_liquidity_sweep

        # ── 8. Price action structure (pillar ②) ──
        pa_score = 0.0
        if pa_displacement is not None and pa_displacement >= 1.5:
            pa_score += 0.5  # conviction candle
        if pa_structure is not None and pa_structure.last_choch is not None:
            # CHoCH agrees with the trade direction -> continuation/flip in our favour.
            if (signal_type == "BUY" and pa_structure.last_choch == "BUY") or (
                signal_type == "SELL" and pa_structure.last_choch == "SELL"
            ):
                pa_score += 0.5
        if has_fvg:
            pa_score = min(pa_score + 0.25, 1.0)  # FVG in play = extra tailwind
        pa_score = min(pa_score, 1.0)
        score += pa_score * 100.0 * weights.weight_pa_structure
        total_weight += weights.weight_pa_structure

        # ── 9. REAL volume term (FIX: was mis-scoring envelope strength) ──
        # rel_volume is None when volume is unavailable (FX spot) -> neutral,
        # never blocks. When available (gold), a volume spike confirms the sweep.
        vol_score = 0.5  # neutral default (no data / FX)
        if rel_volume is not None and not math.isnan(rel_volume):
            vol_score = min(rel_volume / 2.0, 1.0)  # 2x avg vol -> full score
        score += vol_score * 100.0 * weights.weight_volume
        total_weight += weights.weight_volume

        # ── 10. Clustering penalty ──
        if bars_since_last_signal < weights.signal_clustering_bars:
            # Linear penalty: more recent = harsher
            penalty_ratio = 1.0 - (
                bars_since_last_signal / weights.signal_clustering_bars
            )
            score -= penalty_ratio * 30.0  # flat 30-point penalty
            total_weight += weights.weight_clustering

        # ── Normalise ──
        if total_weight > 0:
            score /= total_weight

        # ── Regime bonus/penalty ──
        if regime == "trending":
            # Trending favours directional signals
            pass  # already accounted in trend alignment
        elif regime == "ranging":
            # Ranging — penalise strong directional signals slightly
            if trend_aligned > 0.8:
                score *= 0.85

        # ── Cap ──
        score = max(0.0, min(score, max_score))

        return round(score, 1)

    def meets_threshold(self, score: float) -> bool:
        """Check if a quality score passes the minimum threshold."""
        return score >= self.cfg.min_quality_score

    def timeframes(self) -> List[int]:
        return self.cfg.timeframes

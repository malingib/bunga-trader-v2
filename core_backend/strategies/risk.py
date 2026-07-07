"""Risk management — Fibonacci TP / SL calculation.

Port of the Quadapt Pine Script's risk system:
  - 4 SL methods: ATR, Swing, Order Block, Percentage
  - 4 TP methods: Dynamic ATR, Swing-Based, Adaptive Swing, Heuristic
  - 4 Fibonacci TP levels (1.272, 1.618, 2.618, 4.236)
  - TP spacing multiplier
"""

from __future__ import annotations

import math
from typing import List, Optional, Tuple

from .config import QUADAPT_CFG
from .indicators import atr


class RiskCalculator:
    """Calculates stop loss and take profit levels."""

    def __init__(self) -> None:
        self.cfg = QUADAPT_CFG.risk

    def calculate_sl(
        self,
        *,
        signal_type: str,  # "BUY" or "SELL"
        entry_price: float,
        highs: List[float],
        lows: List[float],
        closes: List[float],
        order_block_high: Optional[float] = None,
        order_block_low: Optional[float] = None,
        sweep_level: Optional[float] = None,
    ) -> float:
        """Calculate stop loss price using configured method.

        If `sweep_level` is provided (the swept liquidity extreme from a
        liquidity-sweep trigger), the SL is placed BEYOND that level with an
        ATR buffer — the correct ICT risk model. A fixed ATR SL otherwise sits
        inside the liquidity pool and gets taken out by the very stop-hunt that
        triggered the trade.
        """
        if sweep_level is not None:
            atr_vals = atr(highs, lows, closes, self.cfg.sl_atr_period)
            cur_atr = atr_vals[-1] if atr_vals else 0.0
            if math.isnan(cur_atr) or cur_atr <= 0:
                cur_atr = entry_price * 0.005
            buf = max(cur_atr * 0.5, entry_price * 0.0003)
            if signal_type == "BUY":
                # swept a low -> SL below the swept low
                sl = min(sweep_level, lows[-1]) - buf
            else:
                # swept a high -> SL above the swept high
                sl = max(sweep_level, highs[-1]) + buf
            return round(sl, 5)

        method = self.cfg.sl_method
        if method == "ATR":
            return self._sl_atr(signal_type, entry_price, highs, lows, closes)
        elif method == "Swing":
            return self._sl_swing(signal_type, entry_price, highs, lows)
        elif method == "Order Block" and order_block_high is not None:
            return self._sl_order_block(
                signal_type, order_block_high, order_block_low
            )
        else:  # Percentage (fallback)
            return self._sl_percent(signal_type, entry_price)

    def _sl_atr(
        self,
        signal_type: str,
        entry: float,
        highs: List[float],
        lows: List[float],
        closes: List[float],
    ) -> float:
        """ATR-based stop loss.

        SL = entry ± ATR * multiplier
        """
        atr_vals = atr(highs, lows, closes, self.cfg.sl_atr_period)
        current_atr = atr_vals[-1] if atr_vals else 0.0
        if math.isnan(current_atr) or current_atr <= 0:
            current_atr = entry * 0.005  # fallback: 0.5% of price

        atr_distance = current_atr * self.cfg.atr_sl_multiplier

        if signal_type == "BUY":
            sl = entry - atr_distance
        else:
            sl = entry + atr_distance
        return round(sl, 5)

    def _sl_swing(
        self,
        signal_type: str,
        entry: float,
        highs: List[float],
        lows: List[float],
    ) -> float:
        """Swing-based stop loss.

        BUY  → SL = lowest low of last N bars
        SELL → SL = highest high of last N bars
        """
        lookback = min(self.cfg.sl_swing_lookback, len(highs), len(lows))
        if lookback < 2:
            return self._sl_percent(signal_type, entry)

        if signal_type == "BUY":
            sl = min(lows[-lookback:])
        else:
            sl = max(highs[-lookback:])
        return round(sl, 5)

    def _sl_order_block(
        self,
        signal_type: str,
        ob_high: float,
        ob_low: float,
    ) -> float:
        """Order block-based stop loss.

        BUY  → SL just below the bullish OB low
        SELL → SL just above the bearish OB high
        """
        if signal_type == "BUY":
            sl = ob_low - (ob_high - ob_low) * 0.02  # 2% buffer below OB
        else:
            sl = ob_high + (ob_high - ob_low) * 0.02
        return round(sl, 5)

    def _sl_percent(
        self,
        signal_type: str,
        entry: float,
    ) -> float:
        """Percentage-based stop loss (fallback)."""
        pct = self.cfg.sl_percent / 100.0
        if signal_type == "BUY":
            sl = entry * (1.0 - pct)
        else:
            sl = entry * (1.0 + pct)
        return round(sl, 5)

    # ──────────────────────────────────────────────
    # Take Profit (Fibonacci extensions)
    # ──────────────────────────────────────────────

    def calculate_tp_levels(
        self,
        *,
        signal_type: str,
        entry_price: float,
        sl_price: float,
        highs: List[float],
        lows: List[float],
        closes: List[float],
        sweep_level: Optional[float] = None,
    ) -> List[float]:
        """Calculate Fibonacci take profit levels.

        Uses configured method and levels list. When `sweep_level` is set
        (a liquidity-sweep trade), the SL is placed BEYOND the swept liquidity
        pool, which is wide — so TP is computed from the RISK distance (heuristic)
        rather than a fixed ATR distance. This preserves a sensible RR ratio for
        sweep trades, which the ATR-based TP would otherwise destroy.
        """
        method = self.cfg.tp_method
        # Sweep trades: risk-scaled TP (fib extensions of the SL distance).
        if sweep_level is not None:
            method = "Heuristic"

        if method == "Dynamic ATR":
            range_distance = self._tp_atr_distance(signal_type, highs, lows, closes)
        elif method == "Swing-Based":
            range_distance = self._tp_swing_distance(signal_type, highs, lows)
        elif method == "Adaptive Swing":
            range_distance = self._tp_adaptive_distance(
                signal_type, highs, lows, closes
            )
        else:  # Heuristic (also used for sweep trades)
            range_distance = self._tp_heuristic_distance(entry_price, sl_price)

        if range_distance <= 0:
            range_distance = abs(entry_price - sl_price) * 2.0  # fallback

        tps: List[float] = []
        for i, fib_level in enumerate(self.cfg.tp_levels):
            if i >= self.cfg.max_tp_levels:
                break

            # Apply spacing multiplier for progressive levels
            effective_distance = range_distance * (1.0 + (i * self.cfg.tp_spacing_multiplier))

            if signal_type == "BUY":
                tp = entry_price + effective_distance * fib_level * 2.0
            else:
                tp = entry_price - effective_distance * fib_level * 2.0

            tps.append(round(tp, 5))

        return tps

    def _tp_atr_distance(
        self,
        signal_type: str,
        highs: List[float],
        lows: List[float],
        closes: List[float],
    ) -> float:
        """Dynamic ATR-based TP distance."""
        atr_vals = atr(highs, lows, closes, 14)
        current_atr = atr_vals[-1] if atr_vals else 0.0
        if math.isnan(current_atr) or current_atr <= 0:
            current_atr = 0.005
        return current_atr * self.cfg.atr_tp_multiplier

    def _tp_swing_distance(
        self,
        signal_type: str,
        highs: List[float],
        lows: List[float],
    ) -> float:
        """Swing-based TP distance from recent volatility."""
        lookback = min(20, len(highs), len(lows))
        if lookback < 5:
            return 0.0
        recent_high = max(highs[-lookback:])
        recent_low = min(lows[-lookback:])
        return (recent_high - recent_low) * 0.5

    def _tp_adaptive_distance(
        self,
        signal_type: str,
        highs: List[float],
        lows: List[float],
        closes: List[float],
    ) -> float:
        """Adaptive swing distance — blends ATR and swing range."""
        atr_dist = self._tp_atr_distance(signal_type, highs, lows, closes)
        swing_dist = self._tp_swing_distance(signal_type, highs, lows)
        if swing_dist <= 0:
            return atr_dist
        return (atr_dist + swing_dist) / 2.0

    def _tp_heuristic_distance(
        self,
        entry_price: float,
        sl_price: float,
    ) -> float:
        """Heuristic: use (entry - SL) * 2 as the base distance."""
        return abs(entry_price - sl_price) * 2.0

    def calculate_rr(
        self, entry_price: float, sl_price: float, tp_price: float
    ) -> float:
        """Calculate risk-reward ratio."""
        risk = abs(entry_price - sl_price)
        reward = abs(tp_price - entry_price)
        if risk <= 0:
            return 0.0
        return round(reward / risk, 2)

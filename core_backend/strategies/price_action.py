"""Price action structure — market structure, displacement, rejection wicks.

Pillars ② of the 3-pillar refactor. Provides the confirmation layer that
tightens entries AFTER the liquidity-sweep trigger (in indicators.py) has fired.

Definitions used:
  - HH/HL/LH/LL: higher-high / higher-low / lower-high / lower-low pivots.
  - CHoCH (Change of Character): price breaks the prior opposite swing, signalling
    a trend flip — used as bias.
  - BOS (Break of Structure): price breaks the prior same-direction swing,
    signalling trend continuation.
  - Displacement: a large-range candle (>= `mult` x ATR) in the trade direction —
    conviction that real participants are moving price.
  - Rejection wick: the sweep candle's tail beyond the liquidity level is long
    (stop-hunt signature).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .indicators import atr, swing_points, SwingPoint


@dataclass
class MarketStructure:
    """Bias + last structure break for the window ending at the last bar."""

    bias: str  # "bullish" | "bearish" | "neutral"
    last_choch: Optional[str]  # "BUY" | "SELL" | None
    last_bos: Optional[str]  # "BUY" | "SELL" | None


def classify_structure(
    highs: List[float],
    lows: List[float],
    swings: Optional[List[SwingPoint]] = None,
) -> MarketStructure:
    """Classify market structure bias from swing points.

    Bias is bullish if the last two swing pivots of each kind trend up
    (HH + HL), bearish if they trend down (LH + LL). Neutral otherwise.
    CHoCH/BOS are inferred from the most recent swing breaks.
    """
    if swings is None:
        swings = swing_points(highs, lows, left=4, right=4)

    if len(swings) < 2:
        return MarketStructure(bias="neutral", last_choch=None, last_bos=None)

    # Walk the swing sequence, tracking the prior high/low to flag breaks.
    last_high: Optional[float] = None
    last_low: Optional[float] = None
    hh_hl = False
    lh_ll = False
    last_choch: Optional[str] = None
    last_bos: Optional[str] = None

    for s in swings:
        if s.kind == "high":
            if last_high is not None:
                if s.price > last_high:
                    last_bos = "BUY"  # broke prior high -> continuation up
                else:
                    last_choch = "SELL"  # lower high -> character change down
            last_high = s.price if last_high is None else max(last_high, s.price)
        else:  # low
            if last_low is not None:
                if s.price < last_low:
                    last_bos = "SELL"  # broke prior low -> continuation down
                else:
                    last_choch = "BUY"  # higher low -> character change up
            last_low = s.price if last_low is None else min(last_low, s.price)

    # Final two swings decide bias.
    if len(swings) >= 4:
        h = [s for s in swings if s.kind == "high"][-2:]
        l = [s for s in swings if s.kind == "low"][-2:]
        if len(h) == 2 and len(l) == 2:
            hh_hl = h[1].price > h[0].price and l[1].price > l[0].price
            lh_ll = h[1].price < h[0].price and l[1].price < l[0].price

    bias = "bullish" if hh_hl else "bearish" if lh_ll else "neutral"
    return MarketStructure(bias=bias, last_choch=last_choch, last_bos=last_bos)


def displacement(
    opens: List[float],
    closes: List[float],
    highs: List[float],
    lows: List[float],
    atr_period: int = 14,
    mult: float = 1.5,
) -> float:
    """Return the displacement ratio of the LAST bar vs ATR.

    Ratio = |close - open| / ATR. >= `mult` means a conviction candle.
    NaN if ATR unavailable; 0.0 if no range.
    """
    i = len(closes) - 1
    if i < 1:
        return 0.0
    atr_vals = atr(highs, lows, closes, atr_period)
    cur = atr_vals[-1] if atr_vals else float("nan")
    if cur is None or math.isnan(cur) or cur <= 0:
        return 0.0
    body = abs(closes[i] - opens[i])
    return body / cur


def wick_ratio(
    highs: List[float],
    lows: List[float],
    opens: List[float],
    closes: List[float],
) -> Tuple[float, str]:
    """Return (tail_ratio, tail_side) of the LAST bar's rejection wick.

    tail_side = "upper" (long upper wick -> rejection of highs) or
    "lower" (long lower wick -> rejection of lows). Tail ratio is the dominant
    wick as a fraction of total range. 0.0 if no range.
    """
    i = len(closes) - 1
    if i < 0:
        return 0.0, "none"
    c_high, c_low = highs[i], lows[i]
    c_open, c_close = opens[i], closes[i]
    rng = c_high - c_low
    if rng <= 0:
        return 0.0, "none"
    upper_wick = c_high - max(c_open, c_close)
    lower_wick = min(c_open, c_close) - c_low
    if upper_wick >= lower_wick:
        return upper_wick / rng, "upper"
    return lower_wick / rng, "lower"

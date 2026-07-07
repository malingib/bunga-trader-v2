"""Technical indicator calculations — core math.

All functions operate on plain Python lists (float) so they work
with any data source (Alpha Vantage, synthetic, etc.).
No library dependencies beyond math + statistics.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple


# ══════════════════════════════════════════════
# 1. Moving Averages
# ══════════════════════════════════════════════


def sma(data: List[float], period: int) -> List[float]:
    """Simple moving average."""
    result: List[float] = []
    for i in range(len(data)):
        if i < period - 1:
            result.append(float("nan"))
        else:
            result.append(sum(data[i - period + 1 : i + 1]) / period)
    return result


def ema(data: List[float], period: int) -> List[float]:
    """Exponential moving average."""
    result: List[float] = []
    multiplier = 2.0 / (period + 1)
    for i in range(len(data)):
        if i == 0:
            result.append(data[i])
        else:
            result.append((data[i] - result[-1]) * multiplier + result[-1])
    return result


def rma(data: List[float], period: int) -> List[float]:
    """Moving average used by RSI (Wilder smoothing)."""
    result: List[float] = []
    alpha = 1.0 / period
    for i in range(len(data)):
        if i == 0:
            result.append(data[i])
        else:
            result.append(alpha * data[i] + (1 - alpha) * result[-1])
    return result


# ══════════════════════════════════════════════
# 2. ATR (Average True Range)
# ══════════════════════════════════════════════


def true_range(
    highs: List[float], lows: List[float], closes: List[float]
) -> List[float]:
    """Compute True Range for each bar."""
    tr: List[float] = []
    for i in range(len(closes)):
        if i == 0:
            tr.append(highs[i] - lows[i])
        else:
            hl = highs[i] - lows[i]
            hc = abs(highs[i] - closes[i - 1])
            lc = abs(lows[i] - closes[i - 1])
            tr.append(max(hl, hc, lc))
    return tr


def atr(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    period: int = 14,
) -> List[float]:
    """Average True Range (RMA smoothing)."""
    tr = true_range(highs, lows, closes)
    return rma(tr, period)


# ══════════════════════════════════════════════
# 3. Heikin Ashi Candles  (from Scalping Pullback)
# ══════════════════════════════════════════════


def heikin_ashi(
    opens: List[float],
    highs: List[float],
    lows: List[float],
    closes: List[float],
) -> Tuple[List[float], List[float], List[float], List[float]]:
    """Convert OHLC to Heikin Ashi candles.

    HA-Close  = (open + high + low + close) / 4
    HA-Open   = (prev_HA_open + prev_HA_close) / 2
    HA-High   = max(high, HA_open, HA_close)
    HA-Low    = min(low, HA_open, HA_close)
    """
    ha_opens: List[float] = []
    ha_highs: List[float] = []
    ha_lows: List[float] = []
    ha_closes: List[float] = []

    for i in range(len(closes)):
        ha_close = (opens[i] + highs[i] + lows[i] + closes[i]) / 4.0

        if i == 0:
            ha_open = (opens[i] + closes[i]) / 2.0
        else:
            ha_open = (ha_opens[-1] + ha_closes[-1]) / 2.0

        ha_high = max(highs[i], ha_open, ha_close)
        ha_low = min(lows[i], ha_open, ha_close)

        ha_opens.append(ha_open)
        ha_highs.append(ha_high)
        ha_lows.append(ha_low)
        ha_closes.append(ha_close)

    return ha_opens, ha_highs, ha_lows, ha_closes


# ══════════════════════════════════════════════
# 4. RSI
# ══════════════════════════════════════════════


def rsi(data: List[float], period: int = 14) -> List[float]:
    """Relative Strength Index."""
    deltas: List[float] = []
    for i in range(1, len(data)):
        deltas.append(data[i] - data[i - 1])

    gains: List[float] = [max(d, 0) for d in deltas]
    losses: List[float] = [abs(min(d, 0)) for d in deltas]

    avg_gain = rma(gains, period)
    avg_loss = rma(losses, period)

    result: List[float] = [float("nan")] * len(data)
    for i in range(period, len(data)):
        if avg_loss[i - 1] == 0:
            result[i] = 100.0
        else:
            rs = avg_gain[i - 1] / avg_loss[i - 1]
            result[i] = 100.0 - 100.0 / (1.0 + rs)
    return result


# ══════════════════════════════════════════════
# 5. StochRSI  (from StochRSI+Supertrend)
# ══════════════════════════════════════════════


def stoch_rsi(
    data: List[float],
    rsi_length: int = 14,
    stoch_length: int = 14,
    smooth_k: int = 3,
    smooth_d: int = 3,
) -> Tuple[List[float], List[float]]:
    """StochRSI: K and D lines for entry timing.

    Returns (k_line, d_line) where each is a list aligned to input data.
    """
    rsi_vals = rsi(data, rsi_length)

    k_raw: List[float] = []
    for i in range(len(rsi_vals)):
        if i < stoch_length - 1 or math.isnan(rsi_vals[i]):
            k_raw.append(float("nan"))
        else:
            window = rsi_vals[i - stoch_length + 1 : i + 1]
            low = min(w for w in window if not math.isnan(w))
            high = max(w for w in window if not math.isnan(w))
            if high == low:
                k_raw.append(50.0)
            else:
                k_raw.append((rsi_vals[i] - low) / (high - low) * 100.0)

    # Smooth K with SMA
    k_line = sma([v if not math.isnan(v) else 50.0 for v in k_raw], smooth_k)
    d_line = sma([v if not math.isnan(v) else 50.0 for v in k_line], smooth_d)

    return k_line, d_line


# ══════════════════════════════════════════════
# 6. Supertrend  (from StochRSI+Supertrend)
# ══════════════════════════════════════════════


def supertrend(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    period: int = 11,
    multiplier: float = 2.0,
) -> Tuple[List[float], List[int]]:
    """Supertrend indicator.

    Returns (supertrend_line, direction):
        direction[i] == 1  → uptrend (green)
        direction[i] == -1 → downtrend (red)
    """
    atr_vals = atr(highs, lows, closes, period)
    hl2 = [(h + l) / 2.0 for h, l in zip(highs, lows)]

    # Upper/lower bands
    upper: List[float] = []
    lower: List[float] = []

    for i in range(len(closes)):
        if math.isnan(atr_vals[i]):
            upper.append(float("nan"))
            lower.append(float("nan"))
        else:
            upper.append(hl2[i] + multiplier * atr_vals[i])
            lower.append(hl2[i] - multiplier * atr_vals[i])

    # Direction and supertrend values
    st: List[float] = []
    direction: List[int] = []

    for i in range(len(closes)):
        if i < period:
            st.append(float("nan"))
            direction.append(1)
            continue

        # Determine direction
        if i == period:
            dir_val = 1 if closes[i] > upper[i] else -1
        else:
            if closes[i] > upper[i] and closes[i - 1] <= upper[i - 1]:
                dir_val = 1
            elif closes[i] < lower[i] and closes[i - 1] >= lower[i - 1]:
                dir_val = -1
            elif closes[i] > lower[i] and direction[-1] == 1:
                dir_val = 1
            elif closes[i] < upper[i] and direction[-1] == -1:
                dir_val = -1
            else:
                dir_val = direction[-1]

        # Supertrend line
        if dir_val == 1:
            st_val = max(lower[i], st[-1] if len(st) > i else lower[i])
        else:
            st_val = min(upper[i], st[-1] if len(st) > i else upper[i])

        st.append(st_val)
        direction.append(dir_val)

    return st, direction


# ══════════════════════════════════════════════
# 7. TTM Squeeze  (from TTM Squeeze)
# ══════════════════════════════════════════════


def ttm_squeeze(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    bb_length: int = 20,
    bb_mult: float = 2.0,
    kc_length: int = 20,
    kc_mult: float = 1.5,
) -> Tuple[List[bool], List[float], List[float]]:
    """TTM Squeeze detection.

    Returns (is_squeeze, oscillator, squeeze_released):
        is_squeeze[i]      → True when BB is inside KC (contraction)
        oscillator[i]      → linear regression momentum value
        squeeze_released[i] → True when squeeze just ended (expansion)
    """
    # Bollinger Bands
    bb_mid = sma(closes, bb_length)
    bb_std: List[float] = []
    for i in range(len(closes)):
        if i < bb_length - 1:
            bb_std.append(float("nan"))
        else:
            window = closes[i - bb_length + 1 : i + 1]
            bb_std.append(statistics.stdev(window) if len(window) > 1 else 0.0)

    bb_upper: List[float] = []
    bb_lower: List[float] = []
    for i in range(len(closes)):
        if math.isnan(bb_mid[i]) or math.isnan(bb_std[i]):
            bb_upper.append(float("nan"))
            bb_lower.append(float("nan"))
        else:
            bb_upper.append(bb_mid[i] + bb_mult * bb_std[i])
            bb_lower.append(bb_mid[i] - bb_mult * bb_std[i])

    # Keltner Channels (EMA-based)
    kc_mid = ema(closes, kc_length)
    tr = true_range(highs, lows, closes)
    kc_tr = ema(tr, kc_length)

    kc_upper: List[float] = []
    kc_lower: List[float] = []
    for i in range(len(closes)):
        if math.isnan(kc_mid[i]) or math.isnan(kc_tr[i]):
            kc_upper.append(float("nan"))
            kc_lower.append(float("nan"))
        else:
            kc_upper.append(kc_mid[i] + kc_mult * kc_tr[i])
            kc_lower.append(kc_mid[i] - kc_mult * kc_tr[i])

    # Squeeze = BB upper < KC upper AND BB lower > KC lower
    is_squeeze: List[bool] = []
    for i in range(len(closes)):
        if math.isnan(bb_upper[i]) or math.isnan(kc_upper[i]):
            is_squeeze.append(False)
        else:
            squeeze = bb_upper[i] < kc_upper[i] and bb_lower[i] > kc_lower[i]
            is_squeeze.append(squeeze)

    # Squeeze release = was squeezed prev bar, not squeezed now
    squeeze_released: List[bool] = []
    for i in range(len(closes)):
        if i == 0:
            squeeze_released.append(False)
        else:
            released = is_squeeze[i - 1] and not is_squeeze[i]
            squeeze_released.append(released)

    # Linear regression oscillator (simplified version)
    # Uses Ehlers-inspired: (highest+lowest)/2 + sma(close)
    half_period = bb_length // 2
    osc: List[float] = []
    for i in range(len(closes)):
        if i < half_period:
            osc.append(0.0)
        else:
            # Linear regression slope over half_period
            n = half_period
            x = list(range(n))
            y = closes[i - n + 1 : i + 1]
            x_mean = (n - 1) / 2.0
            y_mean = sum(y) / n
            num = sum((x[j] - x_mean) * (y[j] - y_mean) for j in range(n))
            den = sum((x[j] - x_mean) ** 2 for j in range(n))
            slope = num / den if den != 0 else 0.0
            osc.append(slope)

    return is_squeeze, osc, squeeze_released


# ══════════════════════════════════════════════
# 8. Envelope Bands (core Quadapt signal)
# ══════════════════════════════════════════════


def envelope_bands(
    data: List[float],
    period: int,
    multiplier: float,
    atr_values: List[float],
    min_envelope_pct: float = 0.001,
) -> Tuple[List[float], List[float], List[float]]:
    """EMA-based envelope bands with ATR-based width.

    Returns (ema_line, upper_band, lower_band).
    """
    ema_line = ema(data, period)

    upper: List[float] = []
    lower: List[float] = []

    for i in range(len(data)):
        if math.isnan(ema_line[i]) or math.isnan(atr_values[i]):
            upper.append(float("nan"))
            lower.append(float("nan"))
        else:
            # ATR-based width, clamped to minimum percentage
            width = max(atr_values[i] * multiplier, data[i] * min_envelope_pct)
            upper.append(ema_line[i] + width)
            lower.append(ema_line[i] - width)

    return ema_line, upper, lower


# ══════════════════════════════════════════════
# 9. Order Block Detection
# ══════════════════════════════════════════════


@dataclass
class OrderBlock:
    """Detected order block."""

    index: int
    block_type: str  # "bullish" or "bearish"
    high: float
    low: float
    strength: float  # 0-1
    is_mitigated: bool = False
    retest_count: int = 0


def detect_order_blocks(
    highs: List[float],
    lows: List[float],
    opens: List[float],
    closes: List[float],
    volumes: List[float],
    lookback: int = 50,
    min_strength: float = 0.3,
) -> List[OrderBlock]:
    """Volatility-based order block detection.

    Bullish OB = strong down candle followed by up close above its midpoint
    Bearish OB = strong up candle followed by down close below its midpoint
    """
    blocks: List[OrderBlock] = []
    atr_vals = atr(highs, lows, closes, 14)

    for i in range(1, min(lookback, len(closes))):
        idx = len(closes) - 1 - i
        if idx < 2 or math.isnan(atr_vals[idx]):
            continue

        total_range = highs[idx] - lows[idx]
        if total_range == 0:
            continue

        # Candle strength as fraction of ATR
        candle_strength = total_range / atr_vals[idx] if atr_vals[idx] > 0 else 0
        if candle_strength < min_strength:
            continue

        # Bullish OB: bearish candle followed by bullish close above midpoint
        if closes[idx] < opens[idx] and idx + 1 < len(closes):
            midpoint = (highs[idx] + lows[idx]) / 2
            if closes[idx + 1] > midpoint:
                blocks.append(
                    OrderBlock(
                        index=idx,
                        block_type="bullish",
                        high=highs[idx],
                        low=lows[idx],
                        strength=min(candle_strength / 3.0, 1.0),
                    )
                )

        # Bearish OB: bullish candle followed by bearish close below midpoint
        if closes[idx] > opens[idx] and idx + 1 < len(closes):
            midpoint = (highs[idx] + lows[idx]) / 2
            if closes[idx + 1] < midpoint:
                blocks.append(
                    OrderBlock(
                        index=idx,
                        block_type="bearish",
                        high=highs[idx],
                        low=lows[idx],
                        strength=min(candle_strength / 3.0, 1.0),
                    )
                )

    return blocks[:10]  # cap at 10


# ══════════════════════════════════════════════
# 10. MLMA (Kernel Regression Trend)
# ══════════════════════════════════════════════


def mlma_trend(
    data: List[float],
    period: int = 34,
    kernel: str = "RBF",
    gamma: float = 0.5,
) -> List[float]:
    """ML Moving Average — kernel regression trend line.

    Simplified implementation: kernel-weighted moving average.
    """
    if len(data) < period:
        return [float("nan")] * len(data)

    result: List[float] = [float("nan")] * len(data)

    for i in range(period - 1, len(data)):
        window = data[i - period + 1 : i + 1]
        weights: List[float] = []

        for j, val in enumerate(window):
            dist = abs(val - window[-1]) / (max(window) - min(window) + 1e-10)

            if kernel == "Linear":
                w = max(0, 1 - dist)
            elif kernel == "Polynomial":
                w = (1 - dist) ** 2
            else:  # RBF / default
                w = math.exp(-gamma * dist * dist)

            # Time decay (more recent = higher weight)
            time_weight = (j + 1) / period
            weights.append(w * time_weight)

        total_weight = sum(weights)
        if total_weight > 0:
            weighted_val = sum(val * w for val, w in zip(window, weights)) / total_weight
        else:
            weighted_val = window[-1]

        result[i] = weighted_val

    return result


# ══════════════════════════════════════════════
# 11. Barssince Helper  (from Scalping Pullback)
# ══════════════════════════════════════════════


def bars_since(condition_list: List[bool], max_lookback: int = 100) -> int:
    """Count bars since the last True in condition_list.

    Returns max_lookback if never true or not found.
    """
    for i in range(1, min(max_lookback, len(condition_list))):
        if condition_list[-i]:
            return i - 1
    return max_lookback


# ══════════════════════════════════════════════
# 12. Cross / Crossunder / Crossover helpers
# ══════════════════════════════════════════════


def crossover(a: List[float], b: List[float]) -> List[bool]:
    """True at index i when a[i-1] < b[i-1] and a[i] > b[i]."""
    result: List[bool] = []
    for i in range(len(a)):
        if i == 0:
            result.append(False)
        else:
            result.append(a[i - 1] < b[i - 1] and a[i] > b[i])
    return result


def crossunder(a: List[float], b: List[float]) -> List[bool]:
    """True at index i when a[i-1] > b[i-1] and a[i] < b[i]."""
    result: List[bool] = []
    for i in range(len(a)):
        if i == 0:
            result.append(False)
        else:
            result.append(a[i - 1] > b[i - 1] and a[i] < b[i])
    return result


def cross(a: List[float], b: List[float]) -> List[bool]:
    """True at index i when a and b lines cross."""
    return crossover(a, b) or crossunder(a, b)


# ══════════════════════════════════════════════
# 13. VWAP (anchored, session-agnostic cumulative)
# ══════════════════════════════════════════════


def vwap(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    volumes: List[float],
) -> List[float]:
    """Cumulative volume-weighted average price over the passed window.

    Anchored at the first bar of the window (we replay a sliding window, so
    this is a rolling session VWAP). Returns NaN until a non-zero volume exists.
    """
    result: List[float] = []
    cum_pv = 0.0
    cum_vol = 0.0
    for i in range(len(closes)):
        typical = (highs[i] + lows[i] + closes[i]) / 3.0
        vol = volumes[i] if volumes[i] is not None else 0.0
        cum_pv += typical * vol
        cum_vol += vol
        if cum_vol > 0:
            result.append(cum_pv / cum_vol)
        else:
            result.append(float("nan"))
    return result


# ══════════════════════════════════════════════
# 14. Swing points + Liquidity Sweeps (ICT)
# ══════════════════════════════════════════════


@dataclass
class SwingPoint:
    """A pivot high (HH/LH) or low (HL/LL)."""

    index: int
    price: float
    kind: str  # "high" or "low"


@dataclass
class LiquiditySweep:
    """A liquidity sweep: a wick beyond a prior swing extreme, then close back."""

    index: int
    swept_kind: str  # "high" (BSL grabbed) or "low" (SSL grabbed)
    swept_price: float
    swept_index: int
    wick_ratio: float  # wick beyond extreme / candle range
    close_inside: bool
    direction: str  # "SELL" (swept a high -> bearish) or "BUY" (swept low -> bullish)


def swing_points(
    highs: List[float],
    lows: List[float],
    left: int = 5,
    right: int = 5,
) -> List[SwingPoint]:
    """Detect pivot highs/lows with `left`/`right` bars of confirmation.

    A pivot high at i requires highs[i] >= highs[i-k] for k in 1..left and
    highs[i] >= highs[i+k] for k in 1..right. Same mirrored for lows.
    """
    swings: List[SwingPoint] = []
    n = len(highs)
    for i in range(left, n - right):
        is_high = all(highs[i] >= highs[j] for j in range(i - left, i + right + 1) if j != i)
        is_low = all(lows[i] <= lows[j] for j in range(i - left, i + right + 1) if j != i)
        if is_high:
            swings.append(SwingPoint(index=i, price=highs[i], kind="high"))
        elif is_low:
            swings.append(SwingPoint(index=i, price=lows[i], kind="low"))
    return swings


def detect_liquidity_sweep(
    highs: List[float],
    lows: List[float],
    opens: List[float],
    closes: List[float],
    swings: Optional[List[SwingPoint]] = None,
    swing_lookback: int = 100,
    min_wick_ratio: float = 0.1,
) -> Optional[LiquiditySweep]:
    """Detect a liquidity sweep on the LAST bar (index -1).

    A sweep = the current bar's wick pokes BEYOND the most recent liquidity
    level (BSL = highest high, SSL = lowest low of the prior `swing_lookback`
    bars) by at least `min_wick_ratio` of its range, then price CLOSES BACK
    INSIDE that level (the stop-hunt reversal signature).

    We use the recent extreme (not only confirmed pivots) because that is where
    resting buy/sell-side liquidity actually sits, and fast 1-min series rarely
    form 4-bar-confirmed pivots. `swings` is still consulted as a refinement:
    if a confirmed pivot extreme is tighter than the raw extreme, it is used.

    Returns the LiquiditySweep for the last bar, or None.
    """
    i = len(closes) - 1
    if i < 2 or not highs or not lows:
        return None
    c_high = highs[i]
    c_low = lows[i]
    c_open = opens[i]
    c_close = closes[i]
    rng = c_high - c_low
    if rng <= 0:
        return None

    lo = max(0, i - swing_lookback)
    # Raw recent extremes over a SHORT window only (last 50 bars) — the actual
    # near-term liquidity pool, NOT the global min/max of the whole lookback
    # (which would include stale series-start levels and never get swept).
    short = max(0, i - 50)
    recent_high = max(highs[short:i]) if i > short else float("nan")
    recent_low = min(lows[short:i]) if i > short else float("nan")

    # Confirmed swing pivots are the precise liquidity pools (BSL above a pivot
    # high, SSL below a pivot low). Prefer the most recent such pivot; fall back
    # to the short-window raw extreme only when no pivot exists.
    if swings is None:
        swings = swing_points(highs, lows, left=4, right=4)
    prior = [s for s in swings if s.index < i]
    pivot_high = next(
        (s.price for s in reversed(prior) if s.kind == "high"), None
    )
    pivot_low = next(
        (s.price for s in reversed(prior) if s.kind == "low"), None
    )
    liq_high = pivot_high if pivot_high is not None else (
        recent_high if not math.isnan(recent_high) else None
    )
    liq_low = pivot_low if pivot_low is not None else (
        recent_low if not math.isnan(recent_low) else None
    )

    # ATR for ATR-normalised penetration (a real sweep pokes beyond the level
    # by a meaningful amount vs normal volatility, independent of the spike bar's
    # own range — which for a stop-hunt is large, making a range-ratio metric fail).
    atr_vals = atr(highs, lows, closes, 14)
    cur_atr = atr_vals[-1] if atr_vals else float("nan")
    if cur_atr is None or math.isnan(cur_atr) or cur_atr <= 0:
        cur_atr = rng  # degenerate fallback: use the bar range

    # Sweep of a HIGH (BSL grabbed): high pokes above the liquidity high, then
    # closes back below it -> bearish (sell the liquidity grab).
    if liq_high is not None and not math.isnan(liq_high):
        if c_high > liq_high and c_close < liq_high:
            wick = c_high - liq_high
            # Penetration beyond the level, normalised by ATR (ICT stop-hunt
            # signature: a real grab extends beyond the pool by a non-trivial
            # fraction of volatility).
            wick_ratio = wick / cur_atr
            if wick_ratio >= min_wick_ratio:
                return LiquiditySweep(
                    index=i,
                    swept_kind="high",
                    swept_price=liq_high,
                    swept_index=lo,
                    wick_ratio=round(wick_ratio, 3),
                    close_inside=True,
                    direction="SELL",
                )
    # Sweep of a LOW (SSL grabbed): low pokes below the liquidity low, then
    # closes back above it -> bullish.
    if liq_low is not None and not math.isnan(liq_low):
        if c_low < liq_low and c_close > liq_low:
            wick = liq_low - c_low
            wick_ratio = wick / cur_atr
            if wick_ratio >= min_wick_ratio:
                return LiquiditySweep(
                    index=i,
                    swept_kind="low",
                    swept_price=liq_low,
                    swept_index=lo,
                    wick_ratio=round(wick_ratio, 3),
                    close_inside=True,
                    direction="BUY",
                )
    return None


# ══════════════════════════════════════════════
# 15. Fair Value Gap (3-candle imbalance)
# ══════════════════════════════════════════════


@dataclass
class FairValueGap:
    index: int
    direction: str  # "up" (bullish FVG) or "down"
    top: float
    bottom: float


def fvg_detect(
    highs: List[float],
    lows: List[float],
    closes: List[float],
) -> Optional[FairValueGap]:
    """Detect a Fair Value Gap ending at the last bar (index -1).

    Bullish FVG (3 candles): candle[i-2].high < candle[i].low -> gap up.
    Bearish FVG: candle[i-2].low > candle[i].high -> gap down.
    """
    i = len(closes) - 1
    if i < 2:
        return None
    if highs[i - 2] < lows[i]:
        return FairValueGap(
            index=i, direction="up", top=lows[i], bottom=highs[i - 2]
        )
    if lows[i - 2] > highs[i]:
        return FairValueGap(
            index=i, direction="down", top=lows[i - 2], bottom=highs[i]
        )
    return None


# ══════════════════════════════════════════════
# 16. Volume helpers
# ══════════════════════════════════════════════


def volume_sma(volumes: List[float], period: int = 20) -> List[float]:
    """Simple moving average of volume (handles None)."""
    v = [v if v is not None else 0.0 for v in volumes]
    return sma(v, period)


def relative_volume(volumes: List[float], period: int = 20) -> List[float]:
    """Ratio of current volume to its SMA. 1.0 = average. NaN if no history."""
    vsma = volume_sma(volumes, period)
    out: List[float] = []
    for i in range(len(volumes)):
        base = vsma[i]
        cur = volumes[i] if volumes[i] is not None else 0.0
        if base and not math.isnan(base) and base > 0:
            out.append(cur / base)
        else:
            out.append(float("nan"))
    return out

"""Opening Range Breakout (ORB) strategy with retest/rejection entries.

Designed for lower-timeframe execution (default: 1-minute bars). The strategy
uses bar timestamps to build a session opening range, waits for a close-based
breakout, then looks for a retest and rejection of the broken level.

This module only produces strategy signals. Position sizing, broker execution,
and money-path risk checks remain in the existing approval/risk pipeline.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, time as dtime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from ..logger import setup_logger
from .momentum_breakout import compute_atr

logger = setup_logger("OpeningRangeBreakout")


@dataclass
class OpeningRangeBreakoutConfig:
    """Strategy-local ORB parameters."""

    session: str = "auto"
    bar_minutes: float = 1.0
    opening_range_minutes: int = 15

    breakout_buffer_pct: float = 0.0001
    breakout_atr_mult: float = 0.25

    retest_tolerance_pct: float = 0.0001
    retest_or_width_pct: float = 0.05
    retest_atr_mult: float = 0.20
    retest_window_minutes: int = 30
    rejection_window_minutes: int = 15

    max_entry_minutes: int = 90
    max_trades_per_session: int = 1

    sl_atr: float = 1.0
    stop_atr_mult: float = 0.10
    rr: float = 1.5
    max_hold_minutes: int = 120
    atr_period: int = 14

    tick_size: float = 0.01
    min_or_width_ticks: int = 10
    min_or_width_atr: float = 0.0
    max_or_width_atr: float = 8.0

    require_retest: bool = True
    breakout_mode: str = "close"
    rejection_mode: str = "close_or_wick"

    min_quality_score: float = 65.0
    max_quality_score: float = 95.0


_SESSION_SPECS: Dict[str, Tuple[str, dtime]] = {
    "new_york": ("America/New_York", dtime(9, 30)),
    "london": ("Europe/London", dtime(8, 0)),
    "utc_day": ("UTC", dtime(0, 0)),
}

_SYMBOL_SESSIONS: Dict[str, str] = {
    "SP500": "new_york",
    "US500": "new_york",
    "NAS100": "new_york",
    "US100": "new_york",
    "XAUUSD": "new_york",
    "GOLD": "new_york",
    "EURUSD": "london",
    "GBPUSD": "london",
    "BTCUSD": "utc_day",
    "ETHUSD": "utc_day",
}


@dataclass
class _SessionState:
    session_start: datetime
    or_high: float = -math.inf
    or_low: float = math.inf
    bars_in_range: int = 0
    complete: bool = False
    pending_side: Optional[str] = None
    pending_level: Optional[float] = None
    breakout_index: Optional[int] = None
    touched: bool = False
    touch_index: Optional[int] = None
    retest_extreme: Optional[float] = None
    trades: int = 0


def _normalize_ts(ts: Optional[datetime]) -> Optional[datetime]:
    if ts is None:
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _naive_utc(ts: datetime) -> datetime:
    return _normalize_ts(ts).replace(tzinfo=None)  # type: ignore[union-attr]


def _zone(name: str):
    try:
        return ZoneInfo(name)
    except Exception:
        return timezone.utc


def _resolve_session(symbol: str, configured_session: str) -> str:
    if configured_session != "auto":
        return configured_session if configured_session in _SESSION_SPECS else "new_york"
    return _SYMBOL_SESSIONS.get(symbol.upper(), "new_york")


def _session_open_utc(ts: datetime, session: str, symbol: str) -> datetime:
    resolved = _resolve_session(symbol, session)
    tz_name, open_time = _SESSION_SPECS.get(resolved, _SESSION_SPECS["new_york"])
    tz = _zone(tz_name)
    local = ts.astimezone(tz)
    candidate = datetime.combine(local.date(), open_time, tzinfo=tz)
    if local < candidate:
        candidate -= timedelta(days=1)
    return candidate.astimezone(timezone.utc)


def _minutes_to_bars(minutes: float, bar_minutes: float) -> int:
    if bar_minutes <= 0:
        return max(1, int(minutes))
    return max(1, int(math.ceil(minutes / bar_minutes)))


def _valid_atr(value: float) -> bool:
    return value > 0 and not math.isnan(value)


def _breakout_buffer(price: float, atr_val: float, cfg: OpeningRangeBreakoutConfig) -> float:
    values = [cfg.tick_size, abs(price) * cfg.breakout_buffer_pct]
    if _valid_atr(atr_val):
        values.append(atr_val * cfg.breakout_atr_mult)
    return max(values)


def _retest_tolerance(
    level: float,
    or_width: float,
    atr_val: float,
    cfg: OpeningRangeBreakoutConfig,
) -> float:
    values = [
        cfg.tick_size,
        abs(level) * cfg.retest_tolerance_pct,
        max(0.0, or_width) * cfg.retest_or_width_pct,
    ]
    if _valid_atr(atr_val):
        values.append(atr_val * cfg.retest_atr_mult)
    return max(values)


def _stop_buffer(atr_val: float, cfg: OpeningRangeBreakoutConfig) -> float:
    if _valid_atr(atr_val):
        return max(cfg.tick_size, atr_val * cfg.stop_atr_mult)
    return cfg.tick_size


def _rejection_quality(
    side: str,
    open_: float,
    high: float,
    low: float,
    close: float,
    cfg: OpeningRangeBreakoutConfig,
) -> bool:
    if cfg.rejection_mode == "close":
        return True

    rng = max(high - low, cfg.tick_size)
    body_low = min(open_, close)
    body_high = max(open_, close)
    lower_wick = body_low - low
    upper_wick = high - body_high
    close_position = (close - low) / rng if rng > 0 else 0.5

    if side == "BUY":
        if cfg.rejection_mode == "wick":
            return lower_wick / rng >= 0.30
        return close_position >= 0.60 or lower_wick / rng >= 0.30

    if cfg.rejection_mode == "wick":
        return upper_wick / rng >= 0.30
    return close_position <= 0.40 or upper_wick / rng >= 0.30


def _quality_score(
    state: _SessionState,
    side: str,
    index: int,
    opens: List[float],
    highs: List[float],
    lows: List[float],
    closes: List[float],
    atr_val: float,
    ts: datetime,
    cfg: OpeningRangeBreakoutConfig,
) -> float:
    # Base starts LOW on purpose: the gate must be able to REJECT weak setups.
    # Older code started at 70.0 and only added, so every valid signal scored
    # >= 70 >= min_quality_score (default 65) and the gate was a no-op.
    score = 50.0
    or_width = state.or_high - state.or_low

    if or_width >= cfg.min_or_width_ticks * cfg.tick_size * 1.5:
        score += 5.0

    if side == "BUY" and closes[index] > opens[index]:
        score += 3.0
    elif side == "SELL" and closes[index] < opens[index]:
        score += 3.0

    rng = max(highs[index] - lows[index], cfg.tick_size)
    body = abs(closes[index] - opens[index])

    if side == "BUY":
        lower_wick = min(opens[index], closes[index]) - lows[index]
        if lower_wick / rng >= 0.30:
            score += 8.0
    else:
        upper_wick = highs[index] - max(opens[index], closes[index])
        if upper_wick / rng >= 0.30:
            score += 8.0

    if _valid_atr(atr_val) and body >= 1.2 * atr_val:
        score += 5.0

    minutes = (ts - state.session_start).total_seconds() / 60.0
    if minutes <= 45:
        score += 5.0
    elif minutes <= 60:
        score += 2.0

    return min(cfg.max_quality_score, max(0.0, score))


class OpeningRangeBreakoutStrategy:
    """Opening Range Breakout with optional retest/rejection confirmation."""

    def __init__(self, config: Optional[OpeningRangeBreakoutConfig] = None):
        self.cfg = config or OpeningRangeBreakoutConfig()

    def generate(
        self,
        opens: List[float],
        highs: List[float],
        lows: List[float],
        closes: List[float],
        times: List[Optional[datetime]],
        symbol: str = "XAUUSD",
    ) -> List[dict]:
        """Generate historical ORB signals from timestamped bars."""
        n = min(len(opens), len(highs), len(lows), len(closes), len(times))
        min_bars = max(self.cfg.atr_period + 1, _minutes_to_bars(self.cfg.opening_range_minutes, self.cfg.bar_minutes) + 2)
        if n < min_bars:
            return []

        atr_vals = compute_atr(highs[:n], lows[:n], closes[:n], self.cfg.atr_period)
        signals: List[dict] = []
        state: Optional[_SessionState] = None

        for i in range(n):
            ts = _normalize_ts(times[i])
            if ts is None:
                continue

            session_start = _session_open_utc(ts, self.cfg.session, symbol)
            if state is None or state.session_start != session_start:
                state = _SessionState(session_start=session_start)

            or_end = session_start + timedelta(minutes=self.cfg.opening_range_minutes)
            if ts < or_end:
                state.or_high = max(state.or_high, highs[i])
                state.or_low = min(state.or_low, lows[i])
                state.bars_in_range += 1
                continue

            if not state.complete:
                if state.bars_in_range == 0:
                    continue
                state.complete = True

            or_width = state.or_high - state.or_low
            atr_val = atr_vals[i] if i < len(atr_vals) else float("nan")
            if not _valid_atr(atr_val):
                continue

            if or_width <= 0 or or_width < self.cfg.min_or_width_ticks * self.cfg.tick_size:
                continue
            if self.cfg.min_or_width_atr > 0 and or_width < self.cfg.min_or_width_atr * atr_val:
                continue
            if self.cfg.max_or_width_atr > 0 and or_width > self.cfg.max_or_width_atr * atr_val:
                continue

            if state.pending_side is not None:
                # A breakout may be detected before the entry cutoff, but its
                # retest/rejection must also complete before that cutoff.
                # Otherwise a setup can enter after the configured session
                # window simply because it remained pending.
                cutoff = session_start + timedelta(minutes=self.cfg.max_entry_minutes)
                if ts >= cutoff:
                    self._reset_pending(state)
                    continue
                signal = self._process_pending(state, i, opens, highs, lows, closes, atr_val, ts, symbol)
                if signal is not None:
                    if state.trades < self.cfg.max_trades_per_session:
                        signals.append(signal)
                        state.trades += 1
                    self._reset_pending(state)
                continue

            cutoff = session_start + timedelta(minutes=self.cfg.max_entry_minutes)
            if state.trades >= self.cfg.max_trades_per_session or ts >= cutoff:
                continue

            buf = _breakout_buffer(closes[i], atr_val, self.cfg)
            if self.cfg.breakout_mode == "wick":
                broke_high = highs[i] > state.or_high + buf
                broke_low = lows[i] < state.or_low - buf
            else:
                broke_high = closes[i] > state.or_high + buf
                broke_low = closes[i] < state.or_low - buf

            if broke_high:
                state.pending_side = "BUY"
                state.pending_level = state.or_high
                state.breakout_index = i
                state.touched = False
                state.touch_index = None
                state.retest_extreme = lows[i]
                if not self.cfg.require_retest:
                    signal = self._make_signal(state, i, opens, highs, lows, closes, atr_val, ts, symbol)
                    if signal is not None and state.trades < self.cfg.max_trades_per_session:
                        signals.append(signal)
                        state.trades += 1
                    self._reset_pending(state)
            elif broke_low:
                state.pending_side = "SELL"
                state.pending_level = state.or_low
                state.breakout_index = i
                state.touched = False
                state.touch_index = None
                state.retest_extreme = highs[i]
                if not self.cfg.require_retest:
                    signal = self._make_signal(state, i, opens, highs, lows, closes, atr_val, ts, symbol)
                    if signal is not None and state.trades < self.cfg.max_trades_per_session:
                        signals.append(signal)
                        state.trades += 1
                    self._reset_pending(state)

        return signals

    def check_latest(
        self,
        opens: List[float],
        highs: List[float],
        lows: List[float],
        closes: List[float],
        symbol: str = "XAUUSD",
        times: Optional[List[Optional[datetime]]] = None,
    ) -> Optional[dict]:
        """Return a signal only if the latest bar completed an ORB setup."""
        if times is None or not times:
            return None

        signals = self.generate(opens, highs, lows, closes, times, symbol=symbol)
        if not signals:
            return None

        latest = _normalize_ts(times[-1])
        if latest is None:
            return None
        latest_key = latest.replace(tzinfo=None).isoformat()

        for signal in reversed(signals):
            if signal.get("metadata", {}).get("bar_time") == latest_key:
                return signal
        return None

    def _process_pending(
        self,
        state: _SessionState,
        i: int,
        opens: List[float],
        highs: List[float],
        lows: List[float],
        closes: List[float],
        atr_val: float,
        ts: datetime,
        symbol: str,
    ) -> Optional[dict]:
        side = state.pending_side
        level = state.pending_level
        if side is None or level is None or state.breakout_index is None:
            self._reset_pending(state)
            return None

        buf = _breakout_buffer(closes[i], atr_val, self.cfg)
        tol = _retest_tolerance(level, state.or_high - state.or_low, atr_val, self.cfg)
        retest_window = _minutes_to_bars(self.cfg.retest_window_minutes, self.cfg.bar_minutes)
        rejection_window = _minutes_to_bars(self.cfg.rejection_window_minutes, self.cfg.bar_minutes)

        if not state.touched:
            if side == "BUY":
                if lows[i] <= level + tol:
                    state.touched = True
                    state.touch_index = i
                    state.retest_extreme = lows[i]
                elif closes[i] < level - buf:
                    self._reset_pending(state)
                    return None
                elif i - state.breakout_index > retest_window:
                    self._reset_pending(state)
                    return None
                else:
                    return None
            else:
                if highs[i] >= level - tol:
                    state.touched = True
                    state.touch_index = i
                    state.retest_extreme = highs[i]
                elif closes[i] > level + buf:
                    self._reset_pending(state)
                    return None
                elif i - state.breakout_index > retest_window:
                    self._reset_pending(state)
                    return None
                else:
                    return None

        if side == "BUY":
            if state.retest_extreme is None:
                state.retest_extreme = lows[i]
            else:
                state.retest_extreme = min(state.retest_extreme, lows[i])

            if closes[i] >= level + buf and _rejection_quality(side, opens[i], highs[i], lows[i], closes[i], self.cfg):
                return self._make_signal(state, i, opens, highs, lows, closes, atr_val, ts, symbol)

            if closes[i] < level - buf:
                self._reset_pending(state)
                return None

            if state.touch_index is not None and i - state.touch_index > rejection_window:
                self._reset_pending(state)
                return None
        else:
            if state.retest_extreme is None:
                state.retest_extreme = highs[i]
            else:
                state.retest_extreme = max(state.retest_extreme, highs[i])

            if closes[i] <= level - buf and _rejection_quality(side, opens[i], highs[i], lows[i], closes[i], self.cfg):
                return self._make_signal(state, i, opens, highs, lows, closes, atr_val, ts, symbol)

            if closes[i] > level + buf:
                self._reset_pending(state)
                return None

            if state.touch_index is not None and i - state.touch_index > rejection_window:
                self._reset_pending(state)
                return None

        return None

    def _make_signal(
        self,
        state: _SessionState,
        i: int,
        opens: List[float],
        highs: List[float],
        lows: List[float],
        closes: List[float],
        atr_val: float,
        ts: datetime,
        symbol: str,
    ) -> Optional[dict]:
        side = state.pending_side
        level = state.pending_level
        if side is None or level is None:
            return None

        entry = closes[i]
        fallback_atr = abs(entry) * 0.005
        a = atr_val if _valid_atr(atr_val) else fallback_atr
        stop_buf = _stop_buffer(a, self.cfg)

        if side == "BUY":
            retest_low = state.retest_extreme if state.retest_extreme is not None else lows[i]
            sl = min(retest_low - stop_buf, level - self.cfg.sl_atr * a)
            if sl >= entry:
                sl = entry - max(self.cfg.tick_size, self.cfg.sl_atr * a)
            risk = entry - sl
            if risk <= 0:
                return None
            tp = entry + self.cfg.rr * risk
        else:
            retest_high = state.retest_extreme if state.retest_extreme is not None else highs[i]
            sl = max(retest_high + stop_buf, level + self.cfg.sl_atr * a)
            if sl <= entry:
                sl = entry + max(self.cfg.tick_size, self.cfg.sl_atr * a)
            risk = sl - entry
            if risk <= 0:
                return None
            tp = entry - self.cfg.rr * risk

        score = _quality_score(state, side, i, opens, highs, lows, closes, a, ts, self.cfg)
        if score < self.cfg.min_quality_score:
            return None

        confidence = "high" if score >= 80.0 else "medium"
        bar_time = _naive_utc(ts)
        minutes_into_session = (ts - state.session_start).total_seconds() / 60.0
        hold_bars = _minutes_to_bars(self.cfg.max_hold_minutes, self.cfg.bar_minutes)

        return {
            "symbol": symbol,
            "action": side,
            "entry_price": round(entry, 5),
            "sl": round(sl, 5),
            "tp": round(tp, 5),
            "quality_score": round(score, 1),
            "signal_source": "opening_range_breakout",
            "confidence": confidence,
            "generated_at": bar_time.isoformat(),
            "hold_bars": hold_bars,
            "metadata": {
                "strategy": "orb",
                "session": _resolve_session(symbol, self.cfg.session),
                "session_start": _naive_utc(state.session_start).isoformat(),
                "or_high": round(state.or_high, 5),
                "or_low": round(state.or_low, 5),
                "or_width": round(state.or_high - state.or_low, 5),
                "broken_level": round(level, 5),
                "breakout_index": state.breakout_index,
                "retest_index": state.touch_index,
                "retest_extreme": round(state.retest_extreme, 5) if state.retest_extreme is not None else None,
                "bar_index": i,
                "bar_time": bar_time.isoformat(),
                "minutes_into_session": round(minutes_into_session, 1),
                "atr": round(a, 5),
                "rr": self.cfg.rr,
                "require_retest": self.cfg.require_retest,
                "breakout_mode": self.cfg.breakout_mode,
                "rejection_mode": self.cfg.rejection_mode,
            },
        }

    @staticmethod
    def _reset_pending(state: _SessionState) -> None:
        state.pending_side = None
        state.pending_level = None
        state.breakout_index = None
        state.touched = False
        state.touch_index = None
        state.retest_extreme = None

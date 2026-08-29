"""ORB backtest harness with realistic next-bar-open fills and risk sizing."""

from __future__ import annotations

import math
import statistics
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

BACKTEST_DIR = Path(__file__).resolve().parent
ROOT = BACKTEST_DIR.parent
for p in (str(ROOT), str(BACKTEST_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from core_backend.strategies.opening_range_breakout import (
    OpeningRangeBreakoutConfig,
    OpeningRangeBreakoutStrategy,
)
from engine_corrected import Bars, BacktestResult, pip_value


@dataclass
class ORBTrade:
    entry_index: int
    exit_index: int
    entry_time: str
    exit_time: str
    side: str
    entry: float
    exit: float
    sl: float
    tp: float
    lot: float
    pnl: float
    r_multiple: float
    hold_bars: int
    metadata: dict = field(default_factory=dict)


@dataclass
class ORBBacktestResult(BacktestResult):
    trades_log: List[ORBTrade] = field(default_factory=list)
    profit_factor: float = 0.0
    expectancy: float = 0.0
    avg_r: float = 0.0
    sharpe: float = 0.0
    trades_per_day: float = 0.0


def _parse_datetime(value: str) -> Optional[datetime]:
    if not value:
        return None
    s = str(value).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        pass

    formats = (
        "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
    )
    for fmt in formats:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _profit_factor(pnls: List[float]) -> float:
    gross_win = sum(p for p in pnls if p > 0)
    gross_loss = abs(sum(p for p in pnls if p < 0))
    if gross_loss > 0:
        return gross_win / gross_loss
    if gross_win > 0:
        return 99.0
    return 0.0


def _sharpe(equity: List[float]) -> float:
    if len(equity) < 3:
        return 0.0
    rets = [
        (equity[i] - equity[i - 1]) / equity[i - 1]
        for i in range(1, len(equity))
        if equity[i - 1] > 0
    ]
    if len(rets) < 2:
        return 0.0
    sd = statistics.pstdev(rets)
    if sd <= 0:
        return 0.0
    return statistics.mean(rets) / sd * math.sqrt(252.0)


def _trading_days(bars: Bars) -> float:
    if not bars.date:
        return 1.0
    first = _parse_datetime(bars.date[0])
    last = _parse_datetime(bars.date[-1])
    if first is None or last is None:
        return 1.0
    if first.tzinfo is None and last.tzinfo is not None:
        first = first.replace(tzinfo=last.tzinfo)
    if last.tzinfo is None and first.tzinfo is not None:
        last = last.replace(tzinfo=first.tzinfo)
    days = abs((last - first).total_seconds()) / 86400.0
    return max(1.0, days)


def run_orb_backtest(
    symbol: str,
    bars: Bars,
    *,
    start_equity: float = 1000.0,
    risk_pct: float = 1.0,
    max_dd_pct: float = 40.0,
    spread_points: float = 0.0,
    slippage_points: float = 0.0,
    entry_offset_bars: int = 1,
    breakeven_at_r: float = 0.0,
    trailing_atr_mult: float = 0.0,
    label: str = "",
    **orb_params,
) -> ORBBacktestResult:
    """Backtest ORB signals using next-bar-open entries and fixed-% risk sizing."""
    n = len(bars.c)
    times: List[Optional[datetime]] = [_parse_datetime(d) for d in bars.date]
    cfg = OpeningRangeBreakoutConfig(**orb_params)
    signals = OpeningRangeBreakoutStrategy(cfg).generate(
        bars.o, bars.h, bars.l, bars.c, times, symbol=symbol
    )

    signal_by_entry: Dict[int, dict] = {}
    for sig in signals:
        idx = (sig.get("metadata") or {}).get("bar_index")
        if idx is None:
            continue
        entry_idx = int(idx) + entry_offset_bars
        if 0 <= entry_idx < n and entry_idx not in signal_by_entry:
            signal_by_entry[entry_idx] = sig

    pip_size = 0.01 if symbol.upper() in ("XAUUSD", "GOLD") else 1.0
    pv = pip_value(symbol)
    cost = spread_points + slippage_points

    bal = start_equity
    peak = bal
    trades = wins = 0
    pos = None
    killed = False
    equity = [bal]
    trade_pnls: List[float] = []
    trades_log: List[ORBTrade] = []

    def _lot(entry: float, sl: float) -> float:
        sl_pips = abs(entry - sl) / pip_size
        if sl_pips <= 0:
            return 0.0
        risk_amount = bal * (risk_pct / 100.0)
        return max(0.001, risk_amount / (sl_pips * pv))

    for i in range(n):
        if killed:
            equity.append(bal)
            continue

        if pos is not None:
            exit_px = None
            if pos["side"] == "BUY":
                if bars.l[i] <= pos["sl"]:
                    exit_px = pos["sl"]
                elif bars.h[i] >= pos["tp"]:
                    exit_px = pos["tp"]
            else:
                if bars.h[i] >= pos["sl"]:
                    exit_px = pos["sl"]
                elif bars.l[i] <= pos["tp"]:
                    exit_px = pos["tp"]

            if exit_px is None and (i - pos["idx"]) >= pos["hold_bars"]:
                exit_px = bars.c[i]

            if exit_px is not None:
                adjusted_exit = exit_px - cost if pos["side"] == "BUY" else exit_px + cost
                if pos["side"] == "BUY":
                    pnl = (adjusted_exit - pos["entry"]) / pip_size * pv * pos["lot"]
                else:
                    pnl = (pos["entry"] - adjusted_exit) / pip_size * pv * pos["lot"]
                bal += pnl
                trades += 1
                trade_pnls.append(pnl)
                if pnl > 0:
                    wins += 1

                risk_distance = abs(pos["entry"] - pos["sl"])
                if pos["side"] == "BUY":
                    r_multiple = (adjusted_exit - pos["entry"]) / risk_distance if risk_distance > 0 else 0.0
                else:
                    r_multiple = (pos["entry"] - adjusted_exit) / risk_distance if risk_distance > 0 else 0.0

                trades_log.append(
                    ORBTrade(
                        entry_index=pos["idx"],
                        exit_index=i,
                        entry_time=bars.date[pos["idx"]] if pos["idx"] < len(bars.date) else "",
                        exit_time=bars.date[i] if i < len(bars.date) else "",
                        side=pos["side"],
                        entry=round(pos["entry"], 5),
                        exit=round(adjusted_exit, 5),
                        sl=round(pos["sl"], 5),
                        tp=round(pos["tp"], 5),
                        lot=round(pos["lot"], 5),
                        pnl=round(pnl, 2),
                        r_multiple=round(r_multiple, 3),
                        hold_bars=i - pos["idx"],
                        metadata=pos.get("metadata", {}),
                    )
                )

                if bal > peak:
                    peak = bal
                if peak > 0 and (peak - bal) / peak * 100 >= max_dd_pct:
                    killed = True
                pos = None

            equity.append(bal)
        else:
            equity.append(bal)

        if pos is not None or killed:
            continue

        sig = signal_by_entry.get(i)
        if sig is None:
            continue

        side = sig["action"]
        entry = bars.o[i] + cost if side == "BUY" else bars.o[i] - cost
        sl = float(sig["sl"])
        rr = float((sig.get("metadata") or {}).get("rr", cfg.rr))

        if side == "BUY":
            if entry <= sl:
                continue
            tp = entry + rr * (entry - sl)
        else:
            if entry >= sl:
                continue
            tp = entry - rr * (sl - entry)

        lot = _lot(entry, sl)
        if lot <= 0:
            continue

        hold_bars = int(sig.get("hold_bars") or max(1, cfg.max_hold_minutes / max(cfg.bar_minutes, 1.0)))
        pos = {
            "side": side,
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "lot": lot,
            "idx": i,
            "hold_bars": hold_bars,
            "risk": abs(entry - sl),
            "atr": float((sig.get("metadata") or {}).get("atr", 0.0) or 0.0),
            "metadata": sig.get("metadata") or {},
        }

        if pos["side"] == "BUY":
            if breakeven_at_r > 0 and bars.h[i] >= entry + breakeven_at_r * pos["risk"]:
                pos["sl"] = max(pos["sl"], entry)
            if trailing_atr_mult > 0 and pos["atr"] > 0:
                pos["sl"] = max(pos["sl"], bars.h[i] - trailing_atr_mult * pos["atr"])
        else:
            if breakeven_at_r > 0 and bars.l[i] <= entry - breakeven_at_r * pos["risk"]:
                pos["sl"] = min(pos["sl"], entry)
            if trailing_atr_mult > 0 and pos["atr"] > 0:
                pos["sl"] = min(pos["sl"], bars.l[i] + trailing_atr_mult * pos["atr"])

    max_dd = 0.0
    cp = start_equity
    for e in equity:
        if e > cp:
            cp = e
        dd = (cp - e) / cp * 100 if cp > 0 else 0.0
        if dd > max_dd:
            max_dd = dd

    pf = _profit_factor(trade_pnls)
    expectancy = statistics.mean(trade_pnls) if trade_pnls else 0.0
    avg_r = statistics.mean([t.r_multiple for t in trades_log]) if trades_log else 0.0
    sharpe = _sharpe(equity)
    days = _trading_days(bars)
    trades_per_day = trades / days if days > 0 else 0.0

    return ORBBacktestResult(
        symbol=symbol if not label else f"{symbol}/{label}",
        ret_pct=round((bal / start_equity - 1.0) * 100, 2),
        trades=trades,
        wins=wins,
        win_pct=round(wins / trades * 100, 1) if trades else 0.0,
        max_dd_pct=round(max_dd, 2),
        final_equity=round(bal, 2),
        max_equity_seen=round(max(equity) if equity else start_equity, 2),
        min_equity_seen=round(min(equity) if equity else start_equity, 2),
        killed_by_dd=killed,
        notes=(
            f"signals={len(signals)}; "
            f"PF={pf:.2f}; "
            f"expectancy={expectancy:.2f}; "
            f"avgR={avg_r:.2f}; "
            f"sharpe={sharpe:.2f}; "
            f"trades/day={trades_per_day:.2f}; "
            f"entry_offset={entry_offset_bars}; "
            f"round_trip_cost={2 * cost:g}"
        ),
        equity=equity,
        trade_pnls=trade_pnls,
        trades_log=trades_log,
        profit_factor=round(pf, 2),
        expectancy=round(expectancy, 2),
        avg_r=round(avg_r, 2),
        sharpe=round(sharpe, 2),
        trades_per_day=round(trades_per_day, 2),
    )

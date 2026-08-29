"""Backtest engine — shared, correct risk-based sizing.

This module fixes the sizing bug found in the original 20-day backtest
(`twenty_day_run.py`, now superseded). That harness treated "1.0 unit" as
1.0 * price-points of P&L, so a single gold win (~$4) moved a $50 account
by ~8% and 2,500 trades reported +1106%. With gold at ~$4000, 1.0 unit is
NOT 1 price point — it is a position of 1.0 standard lot (100 oz), whose P&L
per point is $100. The "+1106%" was a units-vs-contracts confusion, not a
real edge.

Sizing here mirrors the production risk engine (risk_engine.calculate_lot_size):
  - risk a fixed % of account equity per trade (default 1%)
  - lot = risk_amount / (sl_pips * pip_value_per_lot)
  - pip_value_per_lot: GOLD=1.0/oz, INDICES=$50/pt (per risk_engine.py)
  - equity compounds: each closed trade adds/subtracts cash
  - a max-drawdown kill-switch stops trading if equity < (1 - max_dd) * start

This produces realistic, comparable returns in % terms.
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

CACHE_DIR = Path("data/market_cache")


@dataclass
class Bars:
    """Typed OHLC container (dates kept separate so math lists are pure float)."""
    o: List[float] = field(default_factory=list)
    h: List[float] = field(default_factory=list)
    l: List[float] = field(default_factory=list)
    c: List[float] = field(default_factory=list)
    date: List[str] = field(default_factory=list)


# ── Instrument pip-value model (mirrors core_backend.risk_engine) ──
# risk_engine.get_pip_value_per_lot: GOLD -> 1.0, INDICES -> 50.0
PIP_VALUE_PER_LOT = {
    "XAUUSD": 1.0,   # $1 per 0.01 move per 1.0 lot (1 oz)
    "GOLD": 1.0,
    "SP500": 50.0,   # $50 per index point per 1.0 lot
    "NAS100": 50.0,
    "US500": 50.0,
    "US100": 50.0,
}


def pip_value(symbol: str) -> float:
    return PIP_VALUE_PER_LOT.get(symbol.upper(), 1.0)


def load_csv(path: Path) -> Bars:
    """Load OHLC from the cached market CSV into a typed Bars object.

    Accepts either a `date` or `Datetime` column. Aligns all lists by
    appending inside try/except so a bad row can't desync o/h/l/c/date.
    """
    bars = Bars()
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            try:
                o = float(r["Open"])
                h = float(r["High"])
                l = float(r["Low"])
                c = float(r["Close"])
                d = r.get("Datetime") or r.get("date") or r.get("Date") or ""
            except (KeyError, ValueError, TypeError):
                continue
            bars.o.append(o)
            bars.h.append(h)
            bars.l.append(l)
            bars.c.append(c)
            bars.date.append(d)
    return bars


def sma(data, p):
    if len(data) < p:
        return [float("nan")] * len(data)
    out, s = [], sum(data[:p])
    out.append(s / p)
    for i in range(p, len(data)):
        s += data[i] - data[i - p]
        out.append(s / p)
    return [float("nan")] * (p - 1) + out


def compute_atr(highs, lows, closes, period=14):
    tr = [0.0]
    for i in range(1, len(closes)):
        tr.append(max(highs[i] - lows[i],
                      abs(highs[i] - closes[i - 1]),
                      abs(lows[i] - closes[i - 1])))
    return sma(tr, period)


@dataclass
class BacktestResult:
    symbol: str
    ret_pct: float
    trades: int
    wins: int
    win_pct: float
    max_dd_pct: float
    final_equity: float
    max_equity_seen: float
    min_equity_seen: float
    killed_by_dd: bool = False
    notes: str = ""
    equity: List[float] = field(default_factory=list)   # full equity curve (per bar)
    trade_pnls: List[float] = field(default_factory=list)  # per closed-trade P&L ($)
    trades_log: List[dict] = field(default_factory=list)  # per-trade {entry_index, side, pnl}


def run_momentum_backtest(
    symbol: str,
    bars: Bars,
    *,
    sl_atr: float = 1.2,
    rr: float = 4.0,
    trend_ema: int = 0,
    start_equity: float = 1000.0,
    risk_pct: float = 1.0,
    max_dd_pct: float = 40.0,
    max_hold: int = 15,
    warmup: int = 200,
    max_consec_losses: int = 0,
    cost: float = 0.0,
    label: str = "",
) -> BacktestResult:
    """Bar-by-bar momentum breakout with CORRECT risk-based sizing.

    P&L is computed in currency via lot * pip_value * (points moved), where
    points = price distance / pip_size. For gold pip_size=0.01, for indices
    pip_size=1.0 (a 'pip' = 1 index point, matching pip_value=50/pt).

    max_consec_losses: if >0, sit out new entries after N consecutive losing
    trades, resuming only after a win. A drawdown circuit-breaker on the
    strategy itself (robustifies OOS tails without changing the edge).
    """
    d = bars
    n = len(d.c)
    atr = compute_atr(d.h, d.l, d.c, 14)
    ma = sma(d.c, trend_ema) if trend_ema > 0 else None

    # pip size: gold uses 0.01, indices use 1.0
    pip_size = 0.01 if symbol.upper() in ("XAUUSD", "GOLD") else 1.0
    pv = pip_value(symbol)

    def _lot(entry: float, sl: float) -> float:
        sl_dist = abs(entry - sl)
        sl_pips = sl_dist / pip_size
        if sl_pips <= 0:
            return 0.0
        risk_amount = start_equity * (risk_pct / 100.0)
        lot = risk_amount / (sl_pips * pv)
        return max(0.001, lot)

    bal = start_equity
    peak = bal
    trades = wins = 0
    pos = None
    killed = False
    paused = False
    consec_losses = 0
    equity = [bal]
    trade_pnls: List[float] = []
    trades_log: List[dict] = []

    for i in range(max(warmup, 2), n):
        if killed:
            equity.append(bal)
            continue
        # ── manage open position ──
        if pos is not None:
            exit_px = None
            if pos["side"] == "BUY":
                if d.l[i] <= pos["sl"]:
                    exit_px = pos["sl"]
                elif d.h[i] >= pos["tp"]:
                    exit_px = pos["tp"]
            else:
                if d.h[i] >= pos["sl"]:
                    exit_px = pos["sl"]
                elif d.l[i] <= pos["tp"]:
                    exit_px = pos["tp"]
            if exit_px is None and (i - pos["idx"]) >= max_hold:
                exit_px = d.c[i]
            if exit_px is not None:
                # Round-trip transaction cost (entry+exit). Default 0 keeps the
                # original frictionless behaviour; ORB comparison passes the same
                # round_trip_cost so the two engines are directly comparable.
                adj_exit = exit_px - cost if pos["side"] == "BUY" else exit_px + cost
                points = abs(adj_exit - pos["entry"]) / pip_size
                pnl = points * pv * pos["lot"]
                if pos["side"] == "SELL":
                    pnl = -pnl
                bal += pnl
                trades += 1
                trade_pnls.append(pnl)
                if pnl > 0:
                    wins += 1
                    consec_losses = 0
                    paused = False  # a win clears the consecutive-loss pause
                else:
                    consec_losses += 1
                    if max_consec_losses > 0 and consec_losses >= max_consec_losses:
                        paused = True
                if bal > peak:
                    peak = bal
                # kill if drawdown breach
                if (peak - bal) / peak * 100 >= max_dd_pct:
                    killed = True
                trades_log.append(dict(entry_index=pos["idx"], side=pos["side"], pnl=pnl))
                pos = None
            equity.append(bal)
        else:
            equity.append(bal)
        if pos is not None:
            continue
        if killed:
            continue
        if paused:
            continue
        a = atr[i]
        if a <= 0 or math.isnan(a) or i < 2:
            continue
        sl_d = a * sl_atr
        tp_d = sl_d * rr
        if ma is not None and not math.isnan(ma[i]):
            if d.c[i] > ma[i]:
                can_long, can_short = True, False
            elif d.c[i] < ma[i]:
                can_long, can_short = False, True
            else:
                can_long = can_short = False
        else:
            can_long = can_short = True
        prev_h = max(d.h[i - 1], d.h[i - 2])
        prev_l = min(d.l[i - 1], d.l[i - 2])
        if can_long and d.c[i] > prev_h and d.c[i] > d.o[i]:
            entry = d.c[i]
            sl = entry - sl_d
            tp = entry + tp_d
            lot = _lot(entry, sl)
            if lot > 0:
                pos = dict(side="BUY", entry=entry, sl=sl, tp=tp, lot=lot, idx=i)
        elif can_short and d.c[i] < prev_l and d.c[i] < d.o[i]:
            entry = d.c[i]
            sl = entry + sl_d
            tp = entry - tp_d
            lot = _lot(entry, sl)
            if lot > 0:
                pos = dict(side="SELL", entry=entry, sl=sl, tp=tp, lot=lot, idx=i)

    # stats
    max_dd = 0.0
    cp = start_equity
    min_eq = start_equity
    for e in equity:
        if e > cp:
            cp = e
        if e < min_eq:
            min_eq = e
        dd = (cp - e) / cp * 100
        if dd > max_dd:
            max_dd = dd
    ret = (bal / start_equity - 1) * 100
    win_pct = (wins / trades * 100) if trades else 0.0
    return BacktestResult(
        symbol=symbol,
        ret_pct=round(ret, 2),
        trades=trades,
        wins=wins,
        win_pct=round(win_pct, 1),
        max_dd_pct=round(max_dd, 1),
        final_equity=round(bal, 2),
        max_equity_seen=round(peak, 2),
        min_equity_seen=round(min_eq, 2),
        killed_by_dd=killed,
        notes=label,
        equity=equity,
        trade_pnls=trade_pnls,
        trades_log=trades_log,
    )

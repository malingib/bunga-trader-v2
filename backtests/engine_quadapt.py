"""Quadapt ML Trader backtest harness — FAITHFUL replay of the production
QuadaptEngine.evaluate() on historical 1-min candles.

Principle (trading-backtest-loop skill): replay the SOURCE-OF-TRUTH
`evaluate()`, do NOT re-derive signal logic. We feed `evaluate()` a
MarketSnapshot built from a sliding window of the FMP bars, take the signal it
would have emitted on that bar, then simulate the trade lifecycle ourselves
(evaluate() has no exit logic of its own).

Exit model mirrors engine.py exactly:
  - mean_reversion (default): wide protective SL + TIME EXIT at market after
    `hold_bars` (480); no fixed TP.
  - liquidity_sweep: risk_calc SL + tp1 Fibonacci (first TP hit wins).

Money-safety: SIMULATION ONLY. Never calls the dispatcher or approves trades.

Usage:
  .venv/bin/python backtests/engine_quadapt.py --symbol XAUUSD
  .venv/bin/python backtests/engine_quadapt.py --all
"""

from __future__ import annotations

import argparse
import logging
import math
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = Path(__file__).resolve().parent
for p in (str(ROOT), str(BACKEND_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from engine_corrected import Bars, load_csv, pip_value
from core_backend.strategies.engine import QuadaptEngine
from core_backend.strategies.market_data import Candle, MarketSnapshot

CSV_DIR = ROOT / "data" / "market_cache"

# How many trailing 1-min bars we hand evaluate() per call. Needs >= 200 for the
# 200MA gate + warmup; 250 is comfortable headroom.
WINDOW = 250


def _build_snapshot(symbol: str, bars: Bars, end: int) -> MarketSnapshot:
    start = max(0, end - WINDOW + 1)
    candles: List[Candle] = []
    for j in range(start, end + 1):
        ts = datetime.fromisoformat(bars.date[j]) if bars.date[j] else datetime(2000, 1, 1)
        # Strip tzinfo: live yfinance candles are offset-NAIVE, and the engine's
        # cluster-prevention compares candle.time (naive) against
        # signal.generated_at (naive). FMP cache has tz-aware strings; normalize.
        if ts.tzinfo is not None:
            ts = ts.replace(tzinfo=None)
        candles.append(
            Candle(
                time=ts,
                open=bars.o[j],
                high=bars.h[j],
                low=bars.l[j],
                close=bars.c[j],
                volume=0.0,
            )
        )
    return MarketSnapshot(symbol=symbol, candles=candles, fetched_at=datetime.now())


def run_quadapt_backtest(
    symbol: str,
    bars: Bars,
    *,
    start_equity: float = 1000.0,
    risk_pct: float = 1.0,
    max_dd_pct: float = 40.0,
    cost: float = 0.30,
    mode: str = "mean_reversion",
) -> Dict:
    """Replay QuadaptEngine.evaluate() bar-by-bar, simulate exits, return metrics."""
    engine = QuadaptEngine()
    engine.cfg.trigger.mode = mode
    engine.cfg.orb.enabled = False
    engine.cfg.momentum.enabled = False

    n = len(bars.c)
    pip_size = 0.01 if symbol.upper() in ("XAUUSD", "GOLD") else 1.0
    pv = pip_value(symbol)

    def _lot(entry: float, sl: float) -> float:
        sl_pips = abs(entry - sl) / pip_size
        if sl_pips <= 0:
            return 0.0
        return max(0.001, (start_equity * (risk_pct / 100.0)) / (sl_pips * pv))

    bal = start_equity
    peak = bal
    trades = wins = 0
    killed = False
    equity = [bal]
    pnls: List[float] = []
    trades_log: List[dict] = []
    pos: Optional[dict] = None

    min_bars = WINDOW
    for i in range(min_bars, n):
        if killed:
            equity.append(bal)
            continue

        if pos is not None:
            side = pos["side"]
            exit_px = None
            if mode == "mean_reversion":
                # Time exit: close at next bar's open after hold_bars.
                if (i - pos["idx"]) >= pos["hold_bars"]:
                    exit_px = bars.o[i]
            else:
                if side == "BUY":
                    if bars.l[i] <= pos["sl"]:
                        exit_px = pos["sl"]
                    elif bars.h[i] >= pos["tp"]:
                        exit_px = pos["tp"]
                else:
                    if bars.h[i] >= pos["sl"]:
                        exit_px = pos["sl"]
                    elif bars.l[i] <= pos["tp"]:
                        exit_px = pos["tp"]

            if exit_px is not None:
                adj = exit_px - cost if side == "BUY" else exit_px + cost
                pnl = (adj - pos["entry"]) / pip_size * pv * pos["lot"] if side == "BUY" else (pos["entry"] - adj) / pip_size * pv * pos["lot"]
                bal += pnl
                trades += 1
                pnls.append(pnl)
                if pnl > 0:
                    wins += 1
                if bal > peak:
                    peak = bal
                if peak > 0 and (peak - bal) / peak * 100 >= max_dd_pct:
                    killed = True
                trades_log.append(dict(entry_index=pos["idx"], side=side, pnl=pnl))
                pos = None
            equity.append(bal)
            if pos is None:
                pass
            else:
                continue

        if pos is not None or killed:
            continue

        snap = _build_snapshot(symbol, bars, i)
        sig = engine.evaluate(snap)
        if sig is None:
            equity.append(bal)
            continue

        side = sig.action
        entry = bars.o[i + 1] if i + 1 < n else bars.c[i]  # next-bar-open entry
        sl = float(sig.sl)
        tp = float(sig.tp)
        lot = _lot(entry, sl)
        if lot <= 0 or (side == "BUY" and entry <= sl) or (side == "SELL" and entry >= sl):
            equity.append(bal)
            continue
        pos = dict(
            side=side, entry=entry, sl=sl, tp=tp, lot=lot, idx=i,
            hold_bars=sig.hold_bars or WINDOW,
        )
        equity.append(bal)

    # metrics
    max_dd = 0.0
    cp = start_equity
    for e in equity:
        if e > cp:
            cp = e
        dd = (cp - e) / cp * 100 if cp > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
    gw = sum(p for p in pnls if p > 0)
    gl = abs(sum(p for p in pnls if p < 0))
    pf = round(gw / gl, 2) if gl > 0 else (99.0 if gw > 0 else 0.0)
    return dict(
        symbol=symbol, mode=mode, ret_pct=round((bal / start_equity - 1) * 100, 2),
        trades=trades, win_pct=round(wins / trades * 100, 1) if trades else 0.0,
        profit_factor=pf, max_dd_pct=round(max_dd, 2), final_equity=round(bal, 2),
        killed_by_dd=killed, total_cost=round(trades * cost, 2),
        trades_log=trades_log,
    )


def _print(r: Dict) -> None:
    print(
        f"  Quadapt({r['mode']:14}) {r['symbol']:7} ret={r['ret_pct']:>7.2f}% "
        f"tr={r['trades']:>4} win={r['win_pct']:>5.1f}% PF={r['profit_factor']:>5.2f} "
        f"dd={r['max_dd_pct']:>5.1f}% cost=${r['total_cost']:>7.2f}"
    )


def main() -> None:
    logging.getLogger("QuadaptEngine").setLevel(logging.WARNING)
    ap = argparse.ArgumentParser(description="Quadapt ML Trader faithful backtest")
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--mode", choices=["mean_reversion", "liquidity_sweep"], default="mean_reversion")
    ap.add_argument("--cost", type=float, default=0.30)
    ap.add_argument("--symbols", nargs="+", default=["XAUUSD", "SP500", "NAS100"])
    ap.add_argument("--ledger", default=str(BACKEND_DIR / "results.jsonl"))
    args = ap.parse_args()

    symbols = args.symbols if args.all else [args.symbol]
    rows = []
    for symbol in symbols:
        path = CSV_DIR / f"fmp_{symbol}_1min.csv"
        if not path.exists():
            print(f"! missing {path}")
            continue
        bars = load_csv(path)
        res = run_quadapt_backtest(symbol, bars, cost=args.cost, mode=args.mode)
        _print(res)
        rows.append({"ts": datetime.now(timezone.utc).isoformat(), "loop_tag": "compare-ab",
                     "strategy": f"Quadapt/{args.mode}", "symbol": symbol,
                     "ret_pct": res["ret_pct"], "trades": res["trades"], "win_pct": res["win_pct"],
                     "profit_factor": res["profit_factor"], "max_dd_pct": res["max_dd_pct"],
                     "final_equity": res["final_equity"], "killed_by_dd": res["killed_by_dd"],
                     "total_cost": res["total_cost"], "params": f"mode={args.mode}"})

    if rows and args.ledger:
        from pathlib import Path as _P
        _lp = _P(args.ledger)
        _lp.parent.mkdir(parents=True, exist_ok=True)
        import json
        with open(_lp, "a") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        print(f"\nAppended {len(rows)} rows to {_lp} (tag=compare-ab)")


if __name__ == "__main__":
    main()

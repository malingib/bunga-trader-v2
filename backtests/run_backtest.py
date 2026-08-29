"""Run the momentum-breakout backtest across all three symbols.

Feeds the shared engine (engine_corrected.run_momentum_backtest) from the
free data loaders (data_loader.load). Example:

  python backtests/run_backtest.py --source yfinance --interval 1h --period 2y
  python backtests/run_backtest.py --source twelvedata --interval 1min --outputsize 20000
"""
from __future__ import annotations

import argparse
from pathlib import Path

from engine_corrected import run_momentum_backtest
from data_loader import load, INTERNAL_SYMBOLS

DEFAULTS = dict(
    sl_atr=2.0, rr=3.0, trend_ema=100, start_equity=1000.0,
    risk_pct=1.0, max_dd_pct=30.0, max_hold=30, warmup=200,
)


def run_all(source: str, interval: str, period: str, outputsize: int) -> None:
    results = []
    for sym in INTERNAL_SYMBOLS:
        try:
            bars = load(sym, source=source, interval=interval, period=period, outputsize=outputsize)
        except Exception as e:
            print(f"  ! {sym}: load failed: {e}")
            continue
        if len(bars.c) < 250:
            print(f"  ! {sym}: only {len(bars.c)} bars, skipping")
            continue
        res = run_momentum_backtest(sym, bars, **DEFAULTS, label=f"{source}/{interval}")
        results.append(res)

    if not results:
        print("No results.")
        return

    print(f"\n=== Momentum breakout backtest  ({source}, interval={interval}) ===")
    print(f"{'Symbol':7} {'Ret%':>9} {'Trades':>7} {'Win%':>6} {'MaxDD%':>7} {'Final$':>10} {'Killed':>7}")
    print("-" * 56)
    for r in results:
        print(f"{r.symbol:7} {r.ret_pct:>9} {r.trades:>7} {r.win_pct:>6} "
              f"{r.max_dd_pct:>7} {r.final_equity:>10} {str(r.killed_by_dd):>7}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Backtest momentum breakout on free data")
    ap.add_argument("--source", choices=["yfinance", "twelvedata"], default="yfinance")
    ap.add_argument("--interval", default="1h")
    ap.add_argument("--period", default="2y")
    ap.add_argument("--outputsize", type=int, default=5000)
    args = ap.parse_args()
    run_all(args.source, args.interval, args.period, args.outputsize)


if __name__ == "__main__":
    # Run from repo root so data/market_cache resolves.
    import os
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    main()

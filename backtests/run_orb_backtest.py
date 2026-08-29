"""Run the Opening Range Breakout backtest.

Examples:
  python backtests/run_orb_backtest.py --csv data/market_cache/fmp_XAUUSD_1min.csv --symbol XAUUSD
  python backtests/run_orb_backtest.py --source yfinance --interval 1m --period 7d --symbol NAS100
"""

from __future__ import annotations

import argparse
from pathlib import Path

from engine_corrected import load_csv
from engine_orb import run_orb_backtest


def _params(args: argparse.Namespace) -> dict:
    return {
        "session": args.session,
        "bar_minutes": args.bar_minutes,
        "opening_range_minutes": args.opening_range_minutes,
        "retest_window_minutes": args.retest_window_minutes,
        "rejection_window_minutes": args.rejection_window_minutes,
        "max_entry_minutes": args.max_entry_minutes,
        "max_trades_per_session": args.max_trades_per_session,
        "sl_atr": args.sl_atr,
        "rr": args.rr,
        "max_hold_minutes": args.max_hold_minutes,
        "tick_size": args.tick_size,
        "min_or_width_ticks": args.min_or_width_ticks,
        "require_retest": not args.no_retest,
        "breakout_mode": args.breakout_mode,
        "rejection_mode": args.rejection_mode,
        "min_quality_score": args.min_quality_score,
        "max_or_width_atr": args.max_or_width_atr,
        "min_or_width_atr": args.min_or_width_atr,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Backtest Opening Range Breakout")
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--csv", help="load a cached OHLC CSV instead of fetching data")
    ap.add_argument("--source", choices=["yfinance", "twelvedata"], default="yfinance")
    ap.add_argument("--interval", default="1m")
    ap.add_argument("--period", default="7d")
    ap.add_argument("--outputsize", type=int, default=20000)

    ap.add_argument("--session", default="auto")
    ap.add_argument("--bar-minutes", type=float, default=1.0)
    ap.add_argument("--opening-range-minutes", type=int, default=15)
    ap.add_argument("--retest-window-minutes", type=int, default=30)
    ap.add_argument("--rejection-window-minutes", type=int, default=15)
    ap.add_argument("--max-entry-minutes", type=int, default=90)
    ap.add_argument("--max-trades-per-session", type=int, default=1)
    ap.add_argument("--sl-atr", type=float, default=1.0)
    ap.add_argument("--rr", type=float, default=1.5)
    ap.add_argument("--max-hold-minutes", type=int, default=120)
    ap.add_argument("--tick-size", type=float, default=0.01)
    ap.add_argument("--min-or-width-ticks", type=int, default=10)
    ap.add_argument("--no-retest", action="store_true")
    ap.add_argument("--breakout-mode", choices=["close", "wick"], default="close")
    ap.add_argument("--rejection-mode", choices=["close", "wick", "close_or_wick"], default="close_or_wick")
    ap.add_argument("--min-quality-score", type=float, default=65.0)
    ap.add_argument("--min-or-width-atr", type=float, default=0.0)
    ap.add_argument("--max-or-width-atr", type=float, default=8.0)

    ap.add_argument("--start-equity", type=float, default=1000.0)
    ap.add_argument("--risk-pct", type=float, default=1.0)
    ap.add_argument("--max-dd-pct", type=float, default=40.0)
    ap.add_argument("--spread-points", type=float, default=0.0)
    ap.add_argument("--slippage-points", type=float, default=0.0)
    ap.add_argument("--entry-offset-bars", type=int, default=1)
    ap.add_argument("--breakeven-at-r", type=float, default=0.0)
    ap.add_argument("--trailing-atr-mult", type=float, default=0.0)

    args = ap.parse_args()

    if args.csv:
        bars = load_csv(Path(args.csv))
    else:
        from data_loader import load

        bars = load(
            args.symbol,
            source=args.source,
            interval=args.interval,
            period=args.period,
            outputsize=args.outputsize,
        )

    if len(bars.c) < 250:
        print(f"! only {len(bars.c)} bars, skipping")
        return

    res = run_orb_backtest(
        args.symbol,
        bars,
        start_equity=args.start_equity,
        risk_pct=args.risk_pct,
        max_dd_pct=args.max_dd_pct,
        spread_points=args.spread_points,
        slippage_points=args.slippage_points,
        entry_offset_bars=args.entry_offset_bars,
        breakeven_at_r=args.breakeven_at_r,
        trailing_atr_mult=args.trailing_atr_mult,
        label=f"{args.opening_range_minutes}m-or",
        **_params(args),
    )

    print(f"\n=== ORB backtest: {res.symbol} ===")
    print(f"Return%: {res.ret_pct}")
    print(f"Trades:  {res.trades}")
    print(f"Win%:    {res.win_pct}")
    print(f"PF:      {res.profit_factor}")
    print(f"Exp$:    {res.expectancy}")
    print(f"AvgR:    {res.avg_r}")
    print(f"Sharpe:  {res.sharpe}")
    print(f"Tr/day:  {res.trades_per_day}")
    print(f"MaxDD%:  {res.max_dd_pct}")
    print(f"Final$:  {res.final_equity}")
    print(f"Killed:  {res.killed_by_dd}")
    print(f"Notes:   {res.notes}")


if __name__ == "__main__":
    import os

    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    main()

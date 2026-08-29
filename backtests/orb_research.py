"""ORB research engine — systematic grid search with expected-value reporting.

Tests variants of Opening Range Breakout across symbols, sessions and ORB
parameters. Reports win rate, expectancy, profit factor, drawdown, Sharpe,
average R, trade frequency and performance by market regime.

Usage:
  .venv/bin/python backtests/orb_research.py --csv data/market_cache/fmp_XAUUSD_1min.csv --symbol XAUUSD
  .venv/bin/python backtests/orb_research.py --all --grid quick
  .venv/bin/python backtests/orb_research.py --all --csv-dir data/market_cache --out /tmp/opencode/orb_results.csv
"""

from __future__ import annotations

import argparse
import csv
import itertools
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = Path(__file__).resolve().parent
for p in (str(ROOT), str(BACKEND_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from core_backend.strategies.indicators import sma
from engine_corrected import Bars, load_csv
from engine_orb import ORBTrade, run_orb_backtest

SYMBOL_PRESETS: Dict[str, dict] = {
    "XAUUSD": dict(tick_size=0.01, min_or_width_ticks=15, cost=0.30),
    "GOLD": dict(tick_size=0.01, min_or_width_ticks=15, cost=0.30),
    "SP500": dict(tick_size=0.25, min_or_width_ticks=8, cost=0.75),
    "NAS100": dict(tick_size=0.25, min_or_width_ticks=12, cost=0.75),
    "EURUSD": dict(tick_size=0.00001, min_or_width_ticks=15, cost=0.00008),
    "GBPUSD": dict(tick_size=0.00001, min_or_width_ticks=18, cost=0.00010),
}


def _detect_regime(closes: List[float], lookback: int = 200) -> str:
    """Simple regime label used for split reporting."""
    if len(closes) < lookback:
        return "ranging"
    ma = sma(closes, lookback)
    last_close = closes[-1]
    last_ma = ma[-1] if ma and not math.isnan(ma[-1]) else last_close
    atr_proxy = sum(abs(closes[i] - closes[i - 1]) for i in range(1, lookback)) / (lookback - 1) if lookback > 1 else 0.0
    dist = abs(last_close - last_ma) / max(abs(last_close), 1e-9)
    return "trending" if dist > 2 * (atr_proxy / max(abs(last_close), 1e-9)) else "ranging"


def _regime_for_trade(bars: Bars, entry_index: int, lookback: int = 200) -> str:
    window = bars.c[max(0, entry_index - lookback) : entry_index + 1]
    return _detect_regime(window, lookback=lookback)


@dataclass
class VariantResult:
    params: dict
    symbol: str
    ret_pct: float
    trades: int
    win_pct: float
    profit_factor: float
    expectancy: float
    avg_r: float
    sharpe: float
    trades_per_day: float
    max_dd_pct: float
    trending_pf: float
    trending_trades: int
    ranging_pf: float
    ranging_trades: int


def evaluate_variant(symbol: str, bars: Bars, params: dict) -> VariantResult:
    preset = SYMBOL_PRESETS.get(symbol, dict(tick_size=0.01, min_or_width_ticks=10, cost=0.30))
    res = run_orb_backtest(
        symbol,
        bars,
        start_equity=1000.0,
        risk_pct=1.0,
        max_dd_pct=40.0,
        spread_points=preset["cost"] / 2.0,
        slippage_points=preset["cost"] / 2.0,
        tick_size=params.get("tick_size", preset["tick_size"]),
        min_or_width_ticks=params.get("min_or_width_ticks", preset["min_or_width_ticks"]),
        label="",
        **params,
    )

    trending_pnls: List[float] = []
    ranging_pnls: List[float] = []
    for trade in res.trades_log:
        regime = _regime_for_trade(bars, trade.entry_index)
        if regime == "trending":
            trending_pnls.append(trade.pnl)
        else:
            ranging_pnls.append(trade.pnl)

    def _pf(pnls: List[float]) -> float:
        gw = sum(p for p in pnls if p > 0)
        gl = abs(sum(p for p in pnls if p < 0))
        if gl > 0:
            return gw / gl
        return 99.0 if gw > 0 else 0.0

    return VariantResult(
        params=dict(params),
        symbol=symbol,
        ret_pct=res.ret_pct,
        trades=res.trades,
        win_pct=res.win_pct,
        profit_factor=res.profit_factor,
        expectancy=res.expectancy,
        avg_r=res.avg_r,
        sharpe=res.sharpe,
        trades_per_day=res.trades_per_day,
        max_dd_pct=res.max_dd_pct,
        trending_pf=round(_pf(trending_pnls), 2) if trending_pnls else 0.0,
        trending_trades=len(trending_pnls),
        ranging_pf=round(_pf(ranging_pnls), 2) if ranging_pnls else 0.0,
        ranging_trades=len(ranging_pnls),
    )


QUICK_GRID: List[dict] = [
    # OR 5/10/15, RR, retest vs immediate, sl_atr, max_hold
    dict(opening_range_minutes=5, rr=1.0, require_retest=True, rejection_mode="close", sl_atr=1.0, max_hold_minutes=120, max_entry_minutes=90),
    dict(opening_range_minutes=5, rr=1.5, require_retest=True, rejection_mode="close", sl_atr=1.0, max_hold_minutes=120, max_entry_minutes=90),
    dict(opening_range_minutes=5, rr=1.0, require_retest=False, rejection_mode="close", sl_atr=1.0, max_hold_minutes=120, max_entry_minutes=90),
    dict(opening_range_minutes=10, rr=1.0, require_retest=True, rejection_mode="close", sl_atr=1.0, max_hold_minutes=120, max_entry_minutes=90),
    dict(opening_range_minutes=10, rr=1.5, require_retest=True, rejection_mode="close", sl_atr=1.0, max_hold_minutes=120, max_entry_minutes=90),
    dict(opening_range_minutes=15, rr=1.0, require_retest=True, rejection_mode="close", sl_atr=1.0, max_hold_minutes=120, max_entry_minutes=90),
    dict(opening_range_minutes=15, rr=1.5, require_retest=True, rejection_mode="close", sl_atr=1.0, max_hold_minutes=120, max_entry_minutes=90),
    dict(opening_range_minutes=15, rr=1.5, require_retest=False, rejection_mode="close", sl_atr=1.0, max_hold_minutes=120, max_entry_minutes=90),
]

FULL_GRID: List[dict] = [
    dict(opening_range_minutes=or_min, rr=rr, require_retest=require_retest, rejection_mode=rejection_mode, sl_atr=sl_atr, max_hold_minutes=hold, max_entry_minutes=max_entry)
    for or_min, rr, require_retest, rejection_mode, sl_atr, hold, max_entry in itertools.product(
        [5, 10, 15, 30],
        [1.0, 1.5, 2.0],
        [True, False],
        ["close", "close_or_wick"],
        [1.0, 1.5],
        [60, 120],
        [60, 90],
    )
    if not (not require_retest and rejection_mode != "close")  # rejection_mode irrelevant when retest off
]


def _format_row(vr: VariantResult) -> str:
    p = vr.params
    return (
        f"{vr.symbol:7}  OR={p.get('opening_range_minutes', 15):>2}m  "
        f"RR={p.get('rr', 1.5):.1f}  retest={'Y' if p.get('require_retest', True) else 'N'}  "
        f"reject={p.get('rejection_mode', 'close'):11}  "
        f"SL={p.get('sl_atr', 1.0):.1f}  hold={p.get('max_hold_minutes', 120):>3}m  "
        f"ret={vr.ret_pct:>6.2f}%  trades={vr.trades:>3}  win={vr.win_pct:>5.1f}%  "
        f"PF={vr.profit_factor:>5.2f}  exp={vr.expectancy:>6.2f}  avgR={vr.avg_r:>5.2f}  "
        f"sharpe={vr.sharpe:>5.2f}  tr/day={vr.trades_per_day:>4.2f}  DD={vr.max_dd_pct:>5.1f}%  "
        f"trendPF={vr.trending_pf:>4.1f}({vr.trending_trades:>2})  rangePF={vr.ranging_pf:>4.1f}({vr.ranging_trades:>2})"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="ORB research engine grid search")
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--csv", help="explicit CSV path for single-symbol run")
    ap.add_argument("--all", action="store_true", help="run all cached fmp_*_1min.csv symbols")
    ap.add_argument("--csv-dir", default="data/market_cache")
    ap.add_argument("--grid", choices=["quick", "full", "custom"], default="quick")
    ap.add_argument("--out", help="write sorted CSV of variant results")
    args = ap.parse_args()

    if args.all:
        csv_files = list(Path(args.csv_dir).glob("fmp_*_1min.csv"))
        if not csv_files:
            print(f"No CSVs under {args.csv_dir}")
            return
        symbols_bars: Dict[str, Bars] = {}
        for p in csv_files:
            stem = p.stem  # fmp_XAUUSD_1min
            sym = stem.replace("fmp_", "").replace("_1min", "").upper()
            symbols_bars[sym] = load_csv(p)
    elif args.csv:
        symbols_bars = {args.symbol.upper(): load_csv(Path(args.csv))}
    else:
        from data_loader import load

        bars = load(args.symbol, source="yfinance", interval="1m", period="7d", outputsize=20000)
        symbols_bars = {args.symbol.upper(): bars}

    grid = QUICK_GRID if args.grid == "quick" else (FULL_GRID if args.grid == "full" else QUICK_GRID)
    print(f"Testing {len(grid)} variants across {list(symbols_bars.keys())}")

    all_results: List[VariantResult] = []
    for symbol, bars in symbols_bars.items():
        regime_label = _detect_regime(bars.c)
        print(f"\n=== {symbol}  regime={regime_label}  bars={len(bars.c)}  [{bars.date[0]} .. {bars.date[-1]}] ===")
        best: VariantResult | None = None
        results: List[VariantResult] = []
        for idx, params in enumerate(grid, 1):
            vr = evaluate_variant(symbol, bars, params)
            results.append(vr)
            all_results.append(vr)
            if best is None or vr.profit_factor > best.profit_factor:
                best = vr
            if idx <= 3 or vr.trades > 0:
                print(_format_row(vr))
        if best:
            print(f"\nBest PF for {symbol}: {_format_row(best)}")

    # global ranking
    print("\n=== Global ranking by profit factor (>=10 trades) ===")
    ranked = sorted([r for r in all_results if r.trades >= 10], key=lambda x: x.profit_factor, reverse=True)
    for vr in ranked[:20]:
        print(_format_row(vr))

    print("\n=== Global ranking by expectancy (>=10 trades) ===")
    for vr in sorted([r for r in all_results if r.trades >= 10], key=lambda x: x.expectancy, reverse=True)[:20]:
        print(_format_row(vr))

    print("\n=== Global ranking by avg R (>=10 trades) ===")
    for vr in sorted([r for r in all_results if r.trades >= 10], key=lambda x: x.avg_r, reverse=True)[:20]:
        print(_format_row(vr))

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(
                [
                    "symbol",
                    "opening_range_minutes",
                    "rr",
                    "require_retest",
                    "rejection_mode",
                    "sl_atr",
                    "max_hold_minutes",
                    "max_entry_minutes",
                    "ret_pct",
                    "trades",
                    "win_pct",
                    "profit_factor",
                    "expectancy",
                    "avg_r",
                    "sharpe",
                    "trades_per_day",
                    "max_dd_pct",
                    "trending_pf",
                    "trending_trades",
                    "ranging_pf",
                    "ranging_trades",
                ]
            )
            for vr in sorted(all_results, key=lambda x: (x.symbol, -x.profit_factor)):
                p = vr.params
                w.writerow(
                    [
                        vr.symbol,
                        p.get("opening_range_minutes"),
                        p.get("rr"),
                        p.get("require_retest"),
                        p.get("rejection_mode"),
                        p.get("sl_atr"),
                        p.get("max_hold_minutes"),
                        p.get("max_entry_minutes"),
                        vr.ret_pct,
                        vr.trades,
                        vr.win_pct,
                        vr.profit_factor,
                        vr.expectancy,
                        vr.avg_r,
                        vr.sharpe,
                        vr.trades_per_day,
                        vr.max_dd_pct,
                        vr.trending_pf,
                        vr.trending_trades,
                        vr.ranging_pf,
                        vr.ranging_trades,
                    ]
                )
        print(f"\nWrote {len(all_results)} rows to {out_path}")


if __name__ == "__main__":
    main()

"""ORB backtest loop driver — runs a grid, persists a results ledger, and
reports a ranked summary. Designed for the trading-backtest-loop workflow:

  1. Run backtest (baseline / fixed variants)
  2. Review (human + numbered gaps)
  3. Fix high-severity only
  4. Re-run, compare to prior ledger entry

The ledger (results.jsonl) is the single source of loop truth — each run
appends one JSON line with the variant params, metrics, and a loop tag.

Usage:
  .venv/bin/python backtests/orb_loop.py --grid quick --symbols XAUUSD SP500 NAS100
  .venv/bin/python backtests/orb_loop.py --loop-tag fix1 --grid quick
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = Path(__file__).resolve().parent
for p in (str(ROOT), str(BACKEND_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from engine_corrected import Bars, load_csv
from engine_orb import run_orb_backtest
from orb_research import QUICK_GRID, FULL_GRID, SYMBOL_PRESETS
from core_backend.strategies.config import QUADAPT_CFG

LEDGER = BACKEND_DIR / "results.jsonl"
CSV_DIR = ROOT / "data" / "market_cache"


def _bars_for(symbol: str) -> Bars:
    path = CSV_DIR / f"fmp_{symbol}_1min.csv"
    if not path.exists():
        raise SystemExit(f"missing cache {path}")
    return load_csv(path)


def run_grid(
    symbols: List[str],
    grid: List[dict],
    loop_tag: str,
    start_equity: float = 1000.0,
    risk_pct: float = 1.0,
) -> List[dict]:
    rows: List[dict] = []
    for symbol in symbols:
        bars = _bars_for(symbol)
        preset = SYMBOL_PRESETS.get(
            symbol, dict(tick_size=0.01, min_or_width_ticks=10, cost=0.30)
        )
        print(
            f"\n=== {symbol}  bars={len(bars.c)}  "
            f"[{bars.date[0]} .. {bars.date[-1]}] ==="
        )
        for params in grid:
            res = run_orb_backtest(
                symbol,
                bars,
                start_equity=start_equity,
                risk_pct=risk_pct,
                max_dd_pct=40.0,
                spread_points=preset["cost"] / 2.0,
                slippage_points=preset["cost"] / 2.0,
                tick_size=params.get("tick_size", preset["tick_size"]),
                min_or_width_ticks=params.get(
                    "min_or_width_ticks", preset["min_or_width_ticks"]
                ),
                label=loop_tag,
                **params,
            )
            row = dict(
                ts=datetime.now(timezone.utc).isoformat(),
                loop_tag=loop_tag,
                symbol=symbol,
                params=params,
                ret_pct=res.ret_pct,
                trades=res.trades,
                wins=res.wins,
                win_pct=res.win_pct,
                profit_factor=res.profit_factor,
                expectancy=res.expectancy,
                avg_r=res.avg_r,
                sharpe=res.sharpe,
                trades_per_day=res.trades_per_day,
                max_dd_pct=res.max_dd_pct,
                final_equity=res.final_equity,
                killed_by_dd=res.killed_by_dd,
                notes=res.notes,
            )
            rows.append(row)
            _print_row(row)
    return rows


def _print_row(r: dict) -> None:
    p = r["params"]
    print(
        f"  OR={p.get('opening_range_minutes',15):>2}m "
        f"RR={p.get('rr',1.5):.1f} "
        f"retest={'Y' if p.get('require_retest',True) else 'N'} "
        f"rej={p.get('rejection_mode','close'):11} "
        f"SL={p.get('sl_atr',1.0):.1f} "
        f"hold={p.get('max_hold_minutes',120):>3}m | "
        f"ret={r['ret_pct']:>7.2f}% tr={r['trades']:>4} "
        f"win={r['win_pct']:>5.1f}% PF={r['profit_factor']:>5.2f} "
        f"exp={r['expectancy']:>7.2f} R={r['avg_r']:>5.2f} "
        f"sh={r['sharpe']:>5.2f} dd={r['max_dd_pct']:>5.1f}%"
    )


def _rank(rows: List[dict], key: str) -> List[dict]:
    return sorted(
        [r for r in rows if r["trades"] >= 10],
        key=lambda x: x[key],
        reverse=True,
    )


def production_variants(symbols: List[str]) -> Dict[str, dict]:
    """Build the single live variant per symbol from QUADAPT_CFG.orb.defaults.

    This mirrors exactly what _run_orb_poll() passes to the strategy, including
    the per-symbol require_retest override. Used to verify the live config
    against historical candles without re-deriving anything.
    """
    oc = QUADAPT_CFG.orb
    out: Dict[str, dict] = {}
    # Best OR/RR per symbol from the Jun–Jul 2026 quick grid (used here only to
    # pick a representative variant to exercise the per-symbol require_retest).
    best_or_rr = {
        "XAUUSD": (10, 1.5, "close_or_wick"),
        "SP500": (15, 1.5, "close"),
        "NAS100": (15, 1.0, "close_or_wick"),
    }
    for sym in symbols:
        sc = oc.defaults.get(sym, {})
        or_m, rr, rej = best_or_rr.get(sym, (oc.opening_range_minutes, oc.rr, oc.rejection_mode))
        out[sym] = dict(
            session=sc.get("session", oc.session),
            opening_range_minutes=or_m,
            rr=rr,
            require_retest=sc.get("require_retest", oc.require_retest),
            rejection_mode=rej,
            sl_atr=oc.sl_atr,
            max_hold_minutes=oc.max_hold_minutes,
            max_entry_minutes=oc.max_entry_minutes,
            breakout_mode=oc.breakout_mode,
            min_quality_score=oc.min_quality_score,
            max_or_width_atr=oc.max_or_width_atr,
        )
    return out


def run_production_config(
    symbols: List[str], loop_tag: str, start_equity: float = 1000.0, risk_pct: float = 1.0
) -> List[dict]:
    rows: List[dict] = []
    variants = production_variants(symbols)
    for symbol in symbols:
        bars = _bars_for(symbol)
        preset = SYMBOL_PRESETS.get(
            symbol, dict(tick_size=0.01, min_or_width_ticks=10, cost=0.30)
        )
        sc = QUADAPT_CFG.orb.defaults.get(symbol, {})
        params = variants[symbol]
        print(
            f"\n=== {symbol} (PROD CONFIG)  bars={len(bars.c)}  "
            f"[{bars.date[0]} .. {bars.date[-1]}] ==="
        )
        res = run_orb_backtest(
            symbol,
            bars,
            start_equity=start_equity,
            risk_pct=risk_pct,
            max_dd_pct=40.0,
            spread_points=preset["cost"] / 2.0,
            slippage_points=preset["cost"] / 2.0,
            tick_size=sc.get("tick_size", preset["tick_size"]),
            min_or_width_ticks=sc.get("min_or_width_ticks", preset["min_or_width_ticks"]),
            label=loop_tag,
            **params,
        )
        row = dict(
            ts=datetime.now(timezone.utc).isoformat(),
            loop_tag=loop_tag,
            symbol=symbol,
            params=params,
            ret_pct=res.ret_pct,
            trades=res.trades,
            wins=res.wins,
            win_pct=res.win_pct,
            profit_factor=res.profit_factor,
            expectancy=res.expectancy,
            avg_r=res.avg_r,
            sharpe=res.sharpe,
            trades_per_day=res.trades_per_day,
            max_dd_pct=res.max_dd_pct,
            final_equity=res.final_equity,
            killed_by_dd=res.killed_by_dd,
            notes=res.notes,
        )
        rows.append(row)
        _print_row(row)
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description="ORB backtest loop driver")
    ap.add_argument("--symbols", nargs="+", default=["XAUUSD", "SP500", "NAS100"])
    ap.add_argument("--grid", choices=["quick", "full"], default="quick")
    ap.add_argument("--loop-tag", default="baseline")
    ap.add_argument("--start-equity", type=float, default=1000.0)
    ap.add_argument("--risk-pct", type=float, default=1.0)
    ap.add_argument("--no-ledger", action="store_true")
    ap.add_argument(
        "--use-production-config",
        action="store_true",
        help="run the single live QUADAPT_CFG.orb variant per symbol",
    )
    args = ap.parse_args()

    if args.use_production_config:
        rows = run_production_config(
            args.symbols,
            args.loop_tag,
            start_equity=args.start_equity,
            risk_pct=args.risk_pct,
        )
    else:
        grid = QUICK_GRID if args.grid == "quick" else FULL_GRID
        rows = run_grid(
            args.symbols,
            grid,
            args.loop_tag,
            start_equity=args.start_equity,
            risk_pct=args.risk_pct,
        )

    if not args.no_ledger:
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        with open(LEDGER, "a") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        print(f"\nAppended {len(rows)} rows to {LEDGER}")

    print("\n=== Global ranking by profit factor (>=10 trades) ===")
    for r in _rank(rows, "profit_factor")[:15]:
        p = r["params"]
        print(
            f"  {r['symbol']:7} PF={r['profit_factor']:>5.2f} "
            f"ret={r['ret_pct']:>7.2f}% tr={r['trades']:>4} "
            f"win={r['win_pct']:>5.1f}% R={r['avg_r']:>5.2f} "
            f"OR={p.get('opening_range_minutes',15)} "
            f"RR={p.get('rr',1.5)} "
            f"rej={p.get('rejection_mode','close')} "
            f"tag={r['loop_tag']}"
        )


if __name__ == "__main__":
    main()

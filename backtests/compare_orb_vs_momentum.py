"""Fair A/B: ORB vs Momentum on the SAME 1-min bars, SAME cost model,
SAME metric math.

Both engines now deduct an identical round-trip cost (entry+exit) so the
comparison is apples-to-apples. Runs the production per-symbol ORB config
(require_retest per symbol) against the live momentum defaults
(MomentumConfig.defaults). Appends rows to results.jsonl with strategy tag.

Usage:
  .venv/bin/python backtests/compare_orb_vs_momentum.py
"""

from __future__ import annotations

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
from engine_orb import run_orb_backtest, ORBBacktestResult
from orb_research import SYMBOL_PRESETS
from core_backend.strategies.config import QUADAPT_CFG

CSV_DIR = ROOT / "data" / "market_cache"
LEDGER = BACKEND_DIR / "results.jsonl"


def _bars_for(symbol: str) -> Bars:
    path = CSV_DIR / f"fmp_{symbol}_1min.csv"
    if not path.exists():
        raise SystemExit(f"missing cache {path}")
    return load_csv(path)


def _orb_variant_for(symbol: str) -> dict:
    oc = QUADAPT_CFG.orb
    sc = oc.defaults.get(symbol, {})
    best_or_rr = {
        "XAUUSD": (10, 1.5, "close_or_wick"),
        "SP500": (15, 1.5, "close"),
        "NAS100": (15, 1.0, "close_or_wick"),
    }
    or_m, rr, rej = best_or_rr.get(symbol, (oc.opening_range_minutes, oc.rr, oc.rejection_mode))
    return dict(
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


def _pf(pnls) -> float:
    gw = sum(p for p in pnls if p > 0)
    gl = abs(sum(p for p in pnls if p < 0))
    if gl > 0:
        return round(gw / gl, 2)
    return 99.0 if gw > 0 else 0.0


def _row(strategy: str, symbol: str, res, cost: float, loop_tag: str) -> dict:
    pnls = getattr(res, "trade_pnls", [])
    return dict(
        ts=datetime.now(timezone.utc).isoformat(),
        loop_tag=loop_tag,
        strategy=strategy,
        symbol=symbol,
        cost_round_trip=cost,
        trades=res.trades,
        wins=res.wins,
        win_pct=res.win_pct,
        ret_pct=res.ret_pct,
        profit_factor=_pf(pnls),
        expectancy=round(sum(pnls) / len(pnls), 2) if pnls else 0.0,
        max_dd_pct=res.max_dd_pct,
        final_equity=res.final_equity,
        killed_by_dd=res.killed_by_dd,
        total_cost=round(res.trades * cost, 2),
        params=getattr(res, "notes", ""),
    )


def main() -> None:
    symbols = ["XAUUSD", "SP500", "NAS100"]
    loop_tag = "compare-ab"
    rows: List[dict] = []

    # Momentum live defaults
    mom_cfg = QUADAPT_CFG.momentum
    mom_defaults = mom_cfg.defaults

    print(f"Fair A/B — same {len(symbols)} symbols, 1-min FMP cache, identical cost model\n")
    for symbol in symbols:
        bars = _bars_for(symbol)
        preset = SYMBOL_PRESETS.get(
            symbol, dict(tick_size=0.01, min_or_width_ticks=10, cost=0.30)
        )
        cost = preset["cost"]  # round-trip; matches ORB's 2*spread/2+2*slippage/2
        print(
            f"--- {symbol}  bars={len(bars.c)}  "
            f"[{bars.date[0]} .. {bars.date[-1]}]  cost(rt)={cost} ---"
        )

        # ORB (production per-symbol config)
        orb_params = _orb_variant_for(symbol)
        sc = QUADAPT_CFG.orb.defaults.get(symbol, {})
        orb_res = run_orb_backtest(
            symbol, bars,
            start_equity=1000.0, risk_pct=1.0, max_dd_pct=40.0,
            spread_points=cost / 2.0, slippage_points=cost / 2.0,
            tick_size=sc.get("tick_size", preset["tick_size"]),
            min_or_width_ticks=sc.get("min_or_width_ticks", preset["min_or_width_ticks"]),
            label=loop_tag, **orb_params,
        )
        rows.append(_row("ORB", symbol, orb_res, cost, loop_tag))
        print(
            f"  ORB    : ret={orb_res.ret_pct:>7.2f}% tr={orb_res.trades:>4} "
            f"win={orb_res.win_pct:>5.1f}% PF={orb_res.profit_factor:>5.2f} "
            f"R={orb_res.avg_r:>5.2f} dd={orb_res.max_dd_pct:>5.1f}%"
        )

        # Momentum (live defaults) with matching cost
        md = mom_defaults.get(symbol, dict(sl_atr=1.2, rr=1.5, trend_ema=0))
        from engine_corrected import run_momentum_backtest

        mom_res = run_momentum_backtest(
            symbol, bars,
            sl_atr=md.get("sl_atr", 1.2), rr=md.get("rr", 1.5),
            trend_ema=md.get("trend_ema", 0),
            start_equity=1000.0, risk_pct=1.0, max_dd_pct=40.0,
            max_hold=mom_cfg.max_hold, warmup=mom_cfg.warmup,
            cost=cost, label=loop_tag,
        )
        rows.append(_row("Momentum", symbol, mom_res, cost, loop_tag))
        print(
            f"  Moment : ret={mom_res.ret_pct:>7.2f}% tr={mom_res.trades:>4} "
            f"win={mom_res.win_pct:>5.1f}% PF={_pf(mom_res.trade_pnls):>5.2f} "
            f"dd={mom_res.max_dd_pct:>5.1f}% cost=${mom_res.trades*cost:>6.2f}"
        )

    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with open(LEDGER, "a") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    print(f"\nAppended {len(rows)} rows to {LEDGER} (tag={loop_tag})")

    # Verdict
    print("\n=== Head-to-head (PF>1 = profitable; cost = total round-trip $) ===")
    for r in rows:
        flag = "✅ WIN" if r["profit_factor"] > 1.0 else "❌ LOSS"
        print(
            f"  {r['strategy']:9} {r['symbol']:7} {flag}  "
            f"ret={r['ret_pct']:>7.2f}%  PF={r['profit_factor']:>5.2f}  "
            f"win={r['win_pct']:>5.1f}%  dd={r['max_dd_pct']:>5.1f}%  "
            f"tr={r['trades']:>4}  cost=${r['total_cost']:>7.2f}"
        )


if __name__ == "__main__":
    main()

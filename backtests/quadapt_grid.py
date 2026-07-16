"""Quadapt mean_reversion grid search — iteration 2.

Iteration 1 showed: with the engine's 4xATR protective SL and a TP set at
1xSL-distance (=4xATR away), the TP is unreachable and win% stays 0% — every
trade dies at the wide stop.

Iteration 2 fixes the exit geometry:
  - protective_sl_atr: 1.0 / 1.5 / 2.0  (tight reversion stop)
  - profit_target_mult: 0.3 / 0.5 / 0.7  (TP as a FRACTION of SL distance,
    so it sits at the realistic reversion pullback, not 4xATR out)
  - hold_bars: 30 / 60 / 120             (time-exit safety cap)

Finds the config that turns the losing reversion into a validated edge.

Run: env -u PYTHONPATH .venv/bin/python backtests/quadapt_grid.py
"""

from __future__ import annotations

import json
import sys
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

logging.getLogger("QuadaptEngine").setLevel(logging.CRITICAL)
logging.getLogger("RiskEngine").setLevel(logging.CRITICAL)
logging.getLogger("MarketData").setLevel(logging.CRITICAL)

from engine_corrected import load_csv  # noqa: E402
from quadapt_core import run_quadapt_core, SYMBOLS, CACHE_DIR  # noqa: E402

SL_GRID = [1.0, 1.5, 2.0]
PT_GRID = [0.3, 0.5, 0.7]
HOLD_GRID = [30, 60, 120]


def main():
    cache = {s: load_csv(CACHE_DIR / f"fmp_{s}_1min.csv") for s in SYMBOLS}
    # Precompute indicator arrays ONCE per symbol (they don't depend on
    # SL/PT/hold). Reusing across the 27 configs cuts runtime ~10x.
    pre_cache = {}
    for s in SYMBOLS:
        from quadapt_core import _precompute
        from core_backend.strategies.config import QUADAPT_CFG as _C
        pre_cache[s] = _precompute(cache[s], _C)
    rows = []
    total = len(SL_GRID) * len(PT_GRID) * len(HOLD_GRID)
    done = 0
    for sl in SL_GRID:
        for pt in PT_GRID:
            for hb in HOLD_GRID:
                agg = dict(ret=0.0, trades=0, wins=0.0, dd=0.0, killed=False)
                detail = {}
                for s in sorted(SYMBOLS):
                    r = run_quadapt_core(s, cache[s], mode="mean_reversion",
                                         hold_bars=hb, profit_target_mult=pt,
                                         protective_sl_atr=sl, pre=pre_cache[s])
                    agg["ret"] += r["ret_pct"]
                    agg["trades"] += r["trades"]
                    agg["wins"] += (r["win_pct"] / 100.0) * r["trades"]
                    agg["dd"] = max(agg["dd"], r["max_dd_pct"])
                    agg["killed"] = agg["killed"] or r["killed"]
                    detail[s] = r
                avg_ret = agg["ret"] / len(SYMBOLS)
                win_pct = (agg["wins"] / agg["trades"] * 100) if agg["trades"] else 0.0
                rows.append(dict(sl=sl, pt=pt, hold=hb, avg_ret=round(avg_ret, 2),
                                 total_trades=agg["trades"], win_pct=round(win_pct, 1),
                                 max_dd=round(agg["dd"], 1), killed=agg["killed"],
                                 detail=detail))
                done += 1
                print(f"[{done}/{total}] sl={sl} pt={pt} hb={hb:>3} | "
                      f"avg_ret={avg_ret:+7.2f}% trades={agg['trades']:>4} "
                      f"win={win_pct:>5.1f}% maxDD={agg['dd']:>5.1f}% killed={agg['killed']}",
                      flush=True)

    rows.sort(key=lambda x: x["avg_ret"], reverse=True)
    best = rows[0]
    print("\n=== BEST (by avg_ret) ===")
    print(f"sl={best['sl']} pt={best['pt']} hold={best['hold']} "
          f"avg_ret={best['avg_ret']}% win={best['win_pct']}% maxDD={best['max_dd']}%")
    out = Path(__file__).resolve().parent / "quadapt_grid_results.json"
    with open(out, "w") as f:
        json.dump(rows, f, indent=2)
    print(f"Saved -> {out}")


if __name__ == "__main__":
    main()

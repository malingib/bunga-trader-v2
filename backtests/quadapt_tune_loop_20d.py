"""Full re-validation on the ~20-session window (task c).

Re-runs the per-symbol mean_reversion tuning loop on the longer window
(data/market_cache/fmp_*_1min.csv, ~21 days) and reports the best
independent config per symbol. Mirrors quadapt_tune_loop.py but on the
extended data so the edge (or lack of it) is statistically stronger.

Run: env -u PYTHONPATH .venv/bin/python backtests/quadapt_tune_loop_20d.py
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
logging.getLogger().setLevel(logging.CRITICAL)
logging.disable(logging.CRITICAL)

from engine_corrected import load_csv  # noqa: E402
from quadapt_core import run_quadapt_core, SYMBOLS, CACHE_DIR, _precompute  # noqa: E402
from core_backend.strategies.config import QUADAPT_CFG as C  # noqa: E402

GRID = {
    "XAUUSD": dict(sl=[1.0, 2.0, 3.0], tp=[1.0, 2.0, 3.0, 4.0], hold=[30, 60, 120]),
    "SP500":  dict(sl=[2.0, 4.0, 6.0, 8.0, 10.0, 15.0], tp=[1.0, 2.0, 3.0, 4.0, 6.0, 8.0], hold=[30, 60, 120]),
    "NAS100": dict(sl=[3.0, 5.0, 8.0, 12.0, 16.0], tp=[3.0, 5.0, 8.0, 12.0], hold=[30, 60, 120]),
}
THR = 50
STRETCH = True


def main():
    cache = {s: load_csv(CACHE_DIR / f"fmp_{s}_1min.csv") for s in SYMBOLS}
    pre = {s: _precompute(cache[s], C) for s in SYMBOLS}
    best_per = {}
    for sym in sorted(SYMBOLS):
        g = GRID[sym]
        rows = []
        for sl in g["sl"]:
            for tp in g["tp"]:
                for hb in g["hold"]:
                    r = run_quadapt_core(
                        sym, cache[sym], mode="mean_reversion", hold_bars=hb,
                        protective_sl_atr=None, sl_points=sl, tp_points=tp,
                        min_quality_score=THR, require_stretch=STRETCH, pre=pre[sym],
                    )
                    rows.append((sl, tp, hb, r))
        rows.sort(key=lambda x: x[3]["ret_pct"], reverse=True)
        best = rows[0]
        best_per[sym] = dict(
            sl_points=best[0], tp_points=best[1], hold_bars=best[2],
            ret_pct=best[3]["ret_pct"], trades=best[3]["trades"],
            win_pct=best[3]["win_pct"], max_dd_pct=best[3]["max_dd_pct"],
            final_equity=best[3]["final_equity"], killed=best[3]["killed"],
        )
        print(f"\n### {sym} ({len(cache[sym].c)} bars) — top 3 ###")
        for sl, tp, hb, r in rows[:3]:
            print(f"  sl={sl} tp={tp} hb={hb} ret={r['ret_pct']:+.2f}% "
                  f"win={r['win_pct']}% trades={r['trades']} DD={r['max_dd_pct']}%")
        print(f">>> BEST {sym}: sl={best[0]} tp={best[1]} hb={best[2]} "
              f"ret={best[3]['ret_pct']:+.2f}% win={best[3]['win_pct']}% "
              f"DD={best[3]['max_dd_pct']}% trades={best[3]['trades']}", flush=True)
    out = Path(__file__).resolve().parent / "quadapt_tune_loop_20d_results.json"
    with open(out, "w") as f:
        json.dump(best_per, f, indent=2)
    print(f"\nSaved -> {out}", flush=True)


if __name__ == "__main__":
    main()

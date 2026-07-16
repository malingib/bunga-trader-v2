"""SP500-only finer tuning grid (task b).

SP500's first pass was weak (best -35% at sl=4/tp=1). The reversion on
SP500 may need WIDER stops and a larger TP than the coarse grid tried.
This searches a finer, larger range and reports the best config.

Expects data in data/market_cache/fmp_SP500_1min.csv (run fetch_20d.py
first for a longer window).

Run: env -u PYTHONPATH .venv/bin/python backtests/tune_sp500.py
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
from quadapt_core import run_quadapt_core, CACHE_DIR, _precompute  # noqa: E402
from core_backend.strategies.config import QUADAPT_CFG as C  # noqa: E402

SYM = "SP500"
SL = [2.0, 4.0, 6.0, 8.0, 10.0, 15.0]
TP = [1.0, 2.0, 3.0, 4.0, 6.0, 8.0]
HOLD = [30, 60, 120]
THR = 50
STRETCH = True


def main():
    bars = load_csv(CACHE_DIR / f"fmp_{SYM}_1min.csv")
    pre = _precompute(bars, C)
    print(f"=== {SYM} finer grid ({len(bars.c)} bars) ===", flush=True)
    rows = []
    for sl in SL:
        for tp in TP:
            for hb in HOLD:
                r = run_quadapt_core(
                    SYM, bars, mode="mean_reversion", hold_bars=hb,
                    protective_sl_atr=None, sl_points=sl, tp_points=tp,
                    min_quality_score=THR, require_stretch=STRETCH, pre=pre,
                )
                rows.append((sl, tp, hb, r))
    rows.sort(key=lambda x: x[3]["ret_pct"], reverse=True)
    print(f"{'sl':>6} {'tp':>6} {'hb':>4} {'ret%':>9} {'trades':>7} {'win%':>6} {'DD%':>6} {'End$':>10}")
    for sl, tp, hb, r in rows:
        print(f"{sl:>6} {tp:>6} {hb:>4} {r['ret_pct']:>+8.2f} "
              f"{r['trades']:>7} {r['win_pct']:>6.1f} {r['max_dd_pct']:>6.1f} "
              f"{r['final_equity']:>10.2f}", flush=True)
    best = rows[0]
    print(f">>> BEST {SYM}: sl={best[0]} tp={best[1]} hb={best[2]} "
          f"ret={best[3]['ret_pct']:+.2f}% win={best[3]['win_pct']}% "
          f"DD={best[3]['max_dd_pct']}% trades={best[3]['trades']}", flush=True)
    out = Path(__file__).resolve().parent / "quadapt_sp500_tune.json"
    json.dump([dict(sl=a, tp=b, hb=c, **d) for (a, b, c, d) in rows],
              open(out, "w"), indent=2)
    print(f"Saved -> {out}", flush=True)


if __name__ == "__main__":
    main()

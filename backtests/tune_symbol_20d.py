"""Per-symbol 20-session tuning (parallel-friendly).

Usage: env -u PYTHONPATH .venv/bin/python backtests/tune_symbol_20d.py XAUUSD
Writes backtests/quadapt_<SYM>_20d.json
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

GRID = {
    "XAUUSD": dict(sl=[1.0, 2.0, 3.0], tp=[1.0, 2.0, 3.0, 4.0], hold=[30, 60, 120]),
    "SP500":  dict(sl=[2.0, 4.0, 6.0, 8.0, 10.0, 15.0], tp=[1.0, 2.0, 3.0, 4.0, 6.0, 8.0], hold=[30, 60, 120]),
    "NAS100": dict(sl=[3.0, 5.0, 8.0, 12.0, 16.0], tp=[3.0, 5.0, 8.0, 12.0], hold=[30, 60, 120]),
}
THR = 50
STRETCH = True


def main(sym: str):
    g = GRID[sym]
    bars = load_csv(CACHE_DIR / f"fmp_{sym}_1min.csv")
    pre = _precompute(bars, C)
    print(f"=== {sym} ({len(bars.c)} bars) ===", flush=True)
    rows = []
    for sl in g["sl"]:
        for tp in g["tp"]:
            for hb in g["hold"]:
                r = run_quadapt_core(
                    sym, bars, mode="mean_reversion", hold_bars=hb,
                    protective_sl_atr=None, sl_points=sl, tp_points=tp,
                    min_quality_score=THR, require_stretch=STRETCH, pre=pre,
                )
                rows.append((sl, tp, hb, r))
                print(f"  sl={sl} tp={tp} hb={hb} ret={r['ret_pct']:+.2f}% "
                      f"win={r['win_pct']}% trades={r['trades']} DD={r['max_dd_pct']}%", flush=True)
    rows.sort(key=lambda x: x[3]["ret_pct"], reverse=True)
    best = rows[0]
    print(f">>> BEST {sym}: sl={best[0]} tp={best[1]} hb={best[2]} "
          f"ret={best[3]['ret_pct']:+.2f}% win={best[3]['win_pct']}% "
          f"DD={best[3]['max_dd_pct']}% trades={best[3]['trades']}", flush=True)
    out = Path(__file__).resolve().parent / f"quadapt_{sym}_20d.json"
    json.dump([dict(sl=a, tp=b, hb=c, **d) for (a, b, c, d) in rows], open(out, "w"), indent=2)
    print(f"Saved -> {out}", flush=True)


if __name__ == "__main__":
    main(sys.argv[1])

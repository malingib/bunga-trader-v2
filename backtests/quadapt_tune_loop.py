"""Per-symbol Quadapt mean_reversion tuning loop.

Separate grid per symbol so each can be tuned to its OWN reversion
magnitude (absolute points), not a shared ATR multiple. The shared
ATR stop was ~5x the actual edge on indices -> 0% win.

For each symbol we sweep:
  sl_points : protective stop in price points
  tp_points : profit target in price points (reversion pullback)
  hold_bars : time-exit safety cap (1-min bars)

Picks the best (max avg return) config per symbol independently.

Run: env -u PYTHONPATH .venv/bin/python backtests/quadapt_tune_loop.py
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
logging.getLogger().setLevel(logging.CRITICAL)  # silence root (engine adds its own handler)
logging.disable(logging.CRITICAL)  # nuclear: disable ALL log records regardless of handler

from engine_corrected import load_csv  # noqa: E402
from quadapt_core import run_quadapt_core, SYMBOLS, CACHE_DIR, _precompute  # noqa: E402
from core_backend.strategies.config import QUADAPT_CFG as C  # noqa: E402

# Per-symbol search ranges, scaled to each instrument's typical reversion size.
# (XAUUSD moves ~$1-4/bar; SP500 ~1-5pts; NAS100 ~5-20pts on 1-min.)
GRID = {
    "XAUUSD": dict(sl=[1.0, 2.0, 3.0], tp=[1.0, 2.0, 3.0], hold=[30, 60, 120]),
    "SP500":  dict(sl=[1.0, 2.0, 3.0, 4.0], tp=[1.0, 2.0, 3.0, 4.0], hold=[30, 60, 120]),
    "NAS100": dict(sl=[3.0, 5.0, 8.0, 12.0], tp=[3.0, 5.0, 8.0, 12.0], hold=[30, 60, 120]),
}
THR = 50          # quality threshold (engine default 62 is unreachable)
STRETCH = True    # only fade genuinely-stretched prices


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
                        sym, cache[sym], mode="mean_reversion",
                        hold_bars=hb, protective_sl_atr=None,
                        sl_points=sl, tp_points=tp,
                        min_quality_score=THR, require_stretch=STRETCH,
                        pre=pre[sym],
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
        # print top 5 for this symbol
        print(f"\n### {sym} — top configs (thr={THR}, stretch={STRETCH}) ###")
        print(f"{'sl':>6} {'tp':>6} {'hb':>4} {'ret%':>9} {'trades':>7} {'win%':>6} {'DD%':>6} {'End$':>10}")
        for sl, tp, hb, r in rows[:5]:
            print(f"{sl:>6} {tp:>6} {hb:>4} {r['ret_pct']:>+8.2f} "
                  f"{r['trades']:>7} {r['win_pct']:>6.1f} {r['max_dd_pct']:>6.1f} "
                  f"{r['final_equity']:>10.2f}")
        print(f">>> BEST {sym}: sl={best[0]} tp={best[1]} hb={best[2]} "
              f"ret={best[3]['ret_pct']:+.2f}% win={best[3]['win_pct']}% "
              f"DD={best[3]['max_dd_pct']}% trades={best[3]['trades']}", flush=True)

    out = Path(__file__).resolve().parent / "quadapt_tune_loop_results.json"
    with open(out, "w") as f:
        json.dump(best_per, f, indent=2)
    print(f"\nSaved -> {out}")


if __name__ == "__main__":
    main()

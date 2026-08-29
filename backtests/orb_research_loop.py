"""Reproducible ORB review -> research -> improvement loop.

The loop selects candidates on an earlier chronological segment and validates
the selected candidate on a later segment.  It writes a report only; live
configuration is never changed.

Usage:
    env -u PYTHONPATH .venv/bin/python backtests/orb_research_loop.py
    env -u PYTHONPATH .venv/bin/python backtests/orb_research_loop.py --grid full
    env -u PYTHONPATH .venv/bin/python backtests/orb_research_loop.py --report /tmp/orb.json
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
BACKTEST_DIR = Path(__file__).resolve().parent
for path in (str(ROOT), str(BACKTEST_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

from engine_corrected import Bars, load_csv
from engine_orb import run_orb_backtest
from orb_research import FULL_GRID, QUICK_GRID, SYMBOL_PRESETS

CSV_DIR = ROOT / "data" / "market_cache"


@dataclass
class Review:
    symbol: str
    params: dict
    train_trades: int
    train_pf: float
    train_avg_r: float
    train_dd_pct: float
    test_trades: int
    test_pf: float
    test_avg_r: float
    test_dd_pct: float
    verdict: str
    reasons: List[str]


def split_bars(bars: Bars, train_fraction: float = 0.70) -> Tuple[Bars, Bars]:
    """Split at a calendar-day boundary to avoid partial-session leakage."""
    if not 0.5 <= train_fraction < 1.0:
        raise ValueError("train_fraction must be in [0.5, 1.0)")
    if len(bars.c) < 2:
        raise ValueError("at least two bars are required")
    target = max(1, min(len(bars.c) - 1, int(len(bars.c) * train_fraction)))
    target_day = bars.date[target][:10]
    day_start = target
    while day_start > 0 and bars.date[day_start - 1][:10] == target_day:
        day_start -= 1
    day_end = target
    while day_end < len(bars.date) and bars.date[day_end][:10] == target_day:
        day_end += 1
    boundary = day_start if target - day_start <= day_end - target else day_end
    if boundary <= 0 or boundary >= len(bars.c):
        raise ValueError("could not find a valid chronological split")

    def sliced(start: int, end: int) -> Bars:
        return Bars(
            o=bars.o[start:end], h=bars.h[start:end], l=bars.l[start:end],
            c=bars.c[start:end], date=bars.date[start:end],
        )

    return sliced(0, boundary), sliced(boundary, len(bars.c))


def _run(symbol: str, bars: Bars, params: dict):
    preset = SYMBOL_PRESETS.get(symbol, {"tick_size": 0.01, "min_or_width_ticks": 10, "cost": 0.30})
    return run_orb_backtest(
        symbol, bars, start_equity=1000.0, risk_pct=1.0, max_dd_pct=40.0,
        spread_points=preset["cost"] / 2.0, slippage_points=preset["cost"] / 2.0,
        tick_size=params.get("tick_size", preset["tick_size"]),
        min_or_width_ticks=params.get("min_or_width_ticks", preset["min_or_width_ticks"]),
        **params,
    )


def _selection_score(result) -> float:
    """Prefer expectancy and sample size, with a small drawdown penalty."""
    return result.avg_r * (result.trades ** 0.5) - result.max_dd_pct * 0.01


def _review(symbol: str, params: dict, train, test, min_trades: int) -> Review:
    reasons: List[str] = []
    if train.trades < min_trades:
        verdict = "insufficient_train_sample"
        reasons.append(f"training trades {train.trades} < {min_trades}")
    elif test.trades < min_trades:
        verdict = "insufficient_test_sample"
        reasons.append(f"test trades {test.trades} < {min_trades}")
    elif train.profit_factor >= 1.2 and test.profit_factor < 1.0:
        verdict = "overfit_reject"
        reasons.append("profitable in training but losing out of sample")
    elif (
        train.profit_factor > 1.0
        and train.avg_r > 0
        and test.profit_factor > 1.0
        and test.avg_r > 0
    ):
        verdict = "candidate"
        reasons.append("positive expectancy in both chronological periods")
    else:
        verdict = "reject"
        reasons.append("out-of-sample expectancy is not positive")

    if test.max_dd_pct > 20.0:
        reasons.append("out-of-sample drawdown exceeds 20%")
        if verdict == "candidate":
            verdict = "high_drawdown_reject"

    return Review(
        symbol=symbol, params=params, train_trades=train.trades,
        train_pf=train.profit_factor, train_avg_r=train.avg_r,
        train_dd_pct=train.max_dd_pct, test_trades=test.trades,
        test_pf=test.profit_factor, test_avg_r=test.avg_r,
        test_dd_pct=test.max_dd_pct, verdict=verdict, reasons=reasons,
    )


def research_symbol(symbol: str, bars: Bars, grid: Iterable[dict], min_trades: int) -> dict:
    train_bars, test_bars = split_bars(bars)
    evaluated = []
    for params in grid:
        train = _run(symbol, train_bars, params)
        evaluated.append((
            _selection_score(train), params, train,
        ))

    # Rank on train only.  The test segment is intentionally untouched until
    # after selection, preventing the usual best-variant leakage.
    evaluated.sort(key=lambda item: item[0], reverse=True)
    reviews: List[Review] = []
    for _, params, train in evaluated[:10]:
        test = _run(symbol, test_bars, params)
        reviews.append(_review(symbol, params, train, test, min_trades))

    accepted = [r for r in reviews if r.verdict == "candidate"]
    selected = max(accepted, key=lambda r: (r.test_pf, r.test_avg_r), default=None)
    return {
        "symbol": symbol,
        "bars": len(bars.c),
        "date_start": bars.date[0] if bars.date else "",
        "date_end": bars.date[-1] if bars.date else "",
        "train_bars": len(train_bars.c),
        "test_bars": len(test_bars.c),
        "reviewed": [asdict(r) for r in reviews],
        "recommendation": asdict(selected) if selected else None,
        "status": "candidate_found" if selected else "no_validated_candidate",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="ORB review/research/improvement loop")
    parser.add_argument("--symbols", nargs="+", default=["XAUUSD", "SP500", "NAS100"])
    parser.add_argument("--grid", choices=["quick", "full"], default="quick")
    parser.add_argument("--min-trades", type=int, default=5)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    grid = QUICK_GRID if args.grid == "quick" else FULL_GRID
    report: Dict[str, object] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "grid": args.grid,
        "min_trades": args.min_trades,
        "selection": "train-only; chronological 70/30 split",
        "live_config_changed": False,
        "symbols": [],
    }

    for symbol in args.symbols:
        path = CSV_DIR / f"fmp_{symbol}_1min.csv"
        if not path.exists():
            print(f"[{symbol}] missing {path}")
            continue
        result = research_symbol(symbol, load_csv(path), grid, args.min_trades)
        report["symbols"].append(result)
        rec = result["recommendation"]
        if rec:
            print(f"{symbol}: CANDIDATE test PF={rec['test_pf']:.2f} avgR={rec['test_avg_r']:.2f} params={rec['params']}")
        else:
            print(f"{symbol}: NO VALIDATED CANDIDATE")

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")
        print(f"Wrote review report to {args.report}")


if __name__ == "__main__":
    main()

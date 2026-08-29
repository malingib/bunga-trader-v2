"""Self-improvement loop for Bunga Trader v2 — BACKTEST-DRIVEN, CROSS-ENGINE.

Why this shape (instead of the old live-DB ML pipeline):
  - The live bunga.db has 0 trades and a schema gap; user chose NOT to migrate it.
  - The loop must be fireable from chat ("run loop") and never auto-execute.
  - So we learn from HISTORICAL backtests, not live outcomes. Same skill
    shape (signal features -> outcome -> model -> proposed config), but the
    "outcome" source is the faithful replay in engine_*.py / engine_orb.py /
    engine_quadapt.py.

What it does:
  1. Replays EVERY strategy on the 1-min FMP cache:
       - ORB (per-symbol prod config)
       - Momentum (live defaults)
       - Quadapt mean_reversion (the "proven edge" default)
       - Quadapt liquidity_sweep
     with a shared cost model.
  2. For each closed backtest trade, records features (strategy, symbol,
     regime, atr_pct, quality_score, session_minutes, retest_flag/mode) +
     outcome (win/loss) into one dataset.
  3. Trains a Logistic Regression on that dataset (sklearn if present, else
     numpy fallback) to rank which features predict wins.
  4. Emits a PROPOSAL (does NOT auto-apply): which engine/regime to favour,
     and suggested nudges. Appends a ledger row.

Money-safety: pure simulation. No dispatcher, no risk_engine changes, no DB.

Usage:
  .venv/bin/python backtests/self_improvement_loop.py
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = Path(__file__).resolve().parent
for p in (str(ROOT), str(BACKEND_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from engine_corrected import Bars, load_csv, run_momentum_backtest
from engine_orb import run_orb_backtest
from orb_research import SYMBOL_PRESETS
from engine_quadapt import run_quadapt_backtest
from core_backend.strategies.config import QUADAPT_CFG

CSV_DIR = ROOT / "data" / "market_cache"
LEDGER = BACKEND_DIR / "results.jsonl"

try:
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import train_test_split
    from sklearn.metrics import accuracy_score, precision_score, recall_score
    SKLEARN = True
except Exception:
    SKLEARN = False


# ──────────────────────────────────────────────────────────────────────────
# Shared feature helpers
# ──────────────────────────────────────────────────────────────────────────
def _regime_at(bars: Bars, idx: int) -> str:
    from core_backend.strategies.indicators import sma
    look = 200
    start = max(0, idx - look)
    window = bars.c[start:idx + 1]
    if len(window) < look:
        return "ranging"
    ma = sma(window, look)
    last = window[-1]
    last_ma = ma[-1] if ma and not math.isnan(ma[-1]) else last
    atr_proxy = sum(abs(window[i] - window[i - 1]) for i in range(1, len(window))) / (len(window) - 1)
    dist = abs(last - last_ma) / max(abs(last), 1e-9)
    return "trending" if dist > 2 * (atr_proxy / max(abs(last), 1e-9)) else "ranging"


def _atr_pct(bars: Bars, idx: int) -> float:
    from core_backend.strategies.indicators import atr
    a = atr(bars.h, bars.l, bars.c, 14)
    v = a[idx] if idx < len(a) and not math.isnan(a[idx]) else 0.0
    return round(v / max(bars.c[idx], 1e-9) * 100, 3) if v > 0 else 0.0


def _session_minutes(bars: Bars, idx: int) -> float:
    # Approximate "minutes into session" via bar spacing from open.
    # We don't have a true session anchor here; use index-of-day proxy:
    # bars are ~1-min; use (idx mod 1440) as a coarse intraday position.
    return float(idx % 1440)


# ──────────────────────────────────────────────────────────────────────────
# Per-engine collectors  (each returns List[dict] of trade features)
# ──────────────────────────────────────────────────────────────────────────
def collect_orb(symbol: str, bars: Bars) -> List[dict]:
    oc = QUADAPT_CFG.orb
    sc = oc.defaults.get(symbol, {})
    preset = SYMBOL_PRESETS.get(symbol, dict(tick_size=0.01, min_or_width_ticks=10, cost=0.30))
    best_or_rr = {"XAUUSD": (10, 1.5, "close_or_wick"), "SP500": (15, 1.5, "close"), "NAS100": (15, 1.0, "close_or_wick")}
    or_m, rr, rej = best_or_rr.get(symbol, (oc.opening_range_minutes, oc.rr, oc.rejection_mode))
    params = dict(session=sc.get("session", oc.session), opening_range_minutes=or_m, rr=rr,
                  require_retest=sc.get("require_retest", oc.require_retest), rejection_mode=rej,
                  sl_atr=oc.sl_atr, max_hold_minutes=oc.max_hold_minutes, max_entry_minutes=oc.max_entry_minutes,
                  breakout_mode=oc.breakout_mode, min_quality_score=oc.min_quality_score, max_or_width_atr=oc.max_or_width_atr)
    res = run_orb_backtest(symbol, bars, start_equity=1000.0, risk_pct=1.0, max_dd_pct=40.0,
                           spread_points=preset["cost"] / 2.0, slippage_points=preset["cost"] / 2.0,
                           tick_size=sc.get("tick_size", preset["tick_size"]),
                           min_or_width_ticks=sc.get("min_or_width_ticks", preset["min_or_width_ticks"]),
                           label="self-improve", **params)
    out = []
    for t in res.trades_log:
        idx = t.entry_index
        qs = (t.metadata or {}).get("quality_score", 0.0)
        out.append(dict(
            strategy="ORB", symbol=symbol, regime=_regime_at(bars, idx),
            atr_pct=_atr_pct(bars, idx), quality_score=float(qs),
            session_minutes=_session_minutes(bars, idx),
            retest_flag=1 if params["require_retest"] else 0,
            outcome=("win" if t.pnl > 0 else ("loss" if t.pnl < 0 else "breakeven")),
        ))
    return out


def collect_momentum(symbol: str, bars: Bars) -> List[dict]:
    mc = QUADAPT_CFG.momentum
    md = mc.defaults.get(symbol, dict(sl_atr=1.2, rr=1.5, trend_ema=0))
    preset = SYMBOL_PRESETS.get(symbol, dict(cost=0.30))
    res = run_momentum_backtest(
        symbol, bars, sl_atr=md.get("sl_atr", 1.2), rr=md.get("rr", 1.5),
        trend_ema=md.get("trend_ema", 0), start_equity=1000.0, risk_pct=1.0,
        max_dd_pct=40.0, max_hold=mc.max_hold, warmup=mc.warmup,
        cost=preset["cost"], label="self-improve",
    )
    out = []
    for t in res.trades_log:
        idx = t["entry_index"]
        out.append(dict(
            strategy="Momentum", symbol=symbol, regime=_regime_at(bars, idx),
            atr_pct=_atr_pct(bars, idx), quality_score=0.0,
            session_minutes=_session_minutes(bars, idx),
            retest_flag=0,
            outcome=("win" if t["pnl"] > 0 else ("loss" if t["pnl"] < 0 else "breakeven")),
        ))
    return out


def collect_quadapt(symbol: str, bars: Bars, mode: str) -> List[dict]:
    preset = SYMBOL_PRESETS.get(symbol, dict(cost=0.30))
    res = run_quadapt_backtest(symbol, bars, cost=preset["cost"], mode=mode)
    out = []
    for t in res.get("trades_log", []):
        idx = t["entry_index"]
        out.append(dict(
            strategy=f"Quadapt/{mode}", symbol=symbol, regime=_regime_at(bars, idx),
            atr_pct=_atr_pct(bars, idx), quality_score=0.0,
            session_minutes=_session_minutes(bars, idx),
            retest_flag=0,
            outcome=("win" if t["pnl"] > 0 else ("loss" if t["pnl"] < 0 else "breakeven")),
        ))
    return out


# ──────────────────────────────────────────────────────────────────────────
# Learning
# ──────────────────────────────────────────────────────────────────────────
FEATURE_NAMES = ["atr_pct", "quality_score", "session_minutes", "retest_flag"]


def _encode(rec: dict) -> List[float]:
    return [float(rec["atr_pct"]), float(rec["quality_score"]),
            float(rec["session_minutes"]), float(rec["retest_flag"])]


def train(features: List[dict]) -> dict:
    labelled = [r for r in features if r["outcome"] in ("win", "loss")]
    if len(labelled) < 20:
        return {"status": "insufficient", "labelled": len(labelled),
                "msg": f"need >=20 labelled trades, have {len(labelled)}"}
    X = [_encode(r) for r in labelled]
    y = [1 if r["outcome"] == "win" else 0 for r in labelled]
    wins = sum(y)
    out = {"status": "trained", "labelled": len(labelled), "wins": wins,
           "losses": len(labelled) - wins, "feature_weights": {}, "accuracy": None}
    if SKLEARN:
        Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)
        m = LogisticRegression(max_iter=1000, random_state=42).fit(Xtr, ytr)
        pred = m.predict(Xte)
        out["accuracy"] = round(accuracy_score(yte, pred), 4)
        out["precision"] = round(precision_score(yte, pred, zero_division=0), 4)
        out["recall"] = round(recall_score(yte, pred, zero_division=0), 4)
        out["feature_weights"] = dict(zip(FEATURE_NAMES, [round(c, 4) for c in m.coef_[0]]))
    else:
        import numpy as np
        arr, lab = np.array(X), np.array(y)
        wins_a = arr[lab == 1]; losses_a = arr[lab == 0]
        if len(wins_a) and len(losses_a):
            diffs = (wins_a.mean(0) - losses_a.mean(0))
            mx = np.max(np.abs(diffs)) or 1.0
            out["feature_weights"] = dict(zip(FEATURE_NAMES, (diffs / mx).round(4).tolist()))
        out["accuracy"] = "n/a (numpy fallback)"
    return out


def propose(features: List[dict], model: dict) -> List[str]:
    recs: List[str] = []
    if model.get("status") != "trained":
        recs.append(f"[INFO] Not enough labelled trades ({model.get('labelled')}) to learn yet — keep collecting via backtests.")
        return recs
    w = model.get("feature_weights", {})
    if w.get("quality_score", 0) > 0:
        recs.append("[PROPOSAL] quality_score predicts wins (+%.3f). Raise min_quality_score threshold to filter weaker setups." % w["quality_score"])
    else:
        recs.append("[PROPOSAL] quality_score does NOT predict wins (%.3f). The threshold may be mis-calibrated; consider widening it." % w["quality_score"])
    if w.get("retest_flag", 0) != 0:
        recs.append("[PROPOSAL] retest_flag has predictive weight (%.3f). Keep retest per-symbol, not global." % w["retest_flag"])

    # Engine-level win rates
    by_strat = defaultdict(lambda: [0, 0])
    for r in features:
        if r["outcome"] in ("win", "loss"):
            by_strat[r["strategy"]][0 if r["outcome"] == "win" else 1] += 1
    recs.append("[RANK] Engine win-rates (this window):")
    for s, (w_, l_) in sorted(by_strat.items(), key=lambda kv: -(kv[1][0] / (kv[1][0] + kv[1][1]) if (kv[1][0] + kv[1][1]) else 0)):
        tot = w_ + l_
        recs.append(f"    {s:22} {w_/tot:.0%} ({w_}/{tot})" + ("  <- favour" if w_/tot > 0.5 else ""))

    # Regime win-rate split
    by_regime = defaultdict(lambda: [0, 0])
    for r in features:
        if r["outcome"] in ("win", "loss"):
            by_regime[r["regime"]][0 if r["outcome"] == "win" else 1] += 1
    for reg, (w_, l_) in by_regime.items():
        tot = w_ + l_
        if tot >= 10:
            recs.append(f"[PROPOSAL] {reg} regime win-rate = {w_/tot:.0%} ({w_}/{tot}) — " +
                        ("favour trades in this regime" if w_/tot > 0.5 else "down-weight trades in this regime"))
    return recs


# ──────────────────────────────────────────────────────────────────────────
# Orchestration
# ──────────────────────────────────────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser(description="Self-improvement loop (backtest-driven, chat-fired)")
    ap.add_argument("--symbols", nargs="+", default=["XAUUSD", "SP500", "NAS100"])
    ap.add_argument("--no-ledger", action="store_true")
    args = ap.parse_args()

    all_features: List[dict] = []
    print("=== Self-improvement loop: replay all engines + learn ===")
    print(f"(sklearn={'yes' if SKLEARN else 'no (numpy fallback)'})\n")
    for symbol in args.symbols:
        path = CSV_DIR / f"fmp_{symbol}_1min.csv"
        if not path.exists():
            continue
        bars = load_csv(path)
        collectors = [
            ("ORB", collect_orb(symbol, bars)),
            ("Momentum", collect_momentum(symbol, bars)),
            ("Quadapt/mean_reversion", collect_quadapt(symbol, bars, "mean_reversion")),
            ("Quadapt/liquidity_sweep", collect_quadapt(symbol, bars, "liquidity_sweep")),
        ]
        for name, feats in collectors:
            all_features.extend(feats)
            nw = sum(1 for f in feats if f["outcome"] == "win")
            print(f"  {symbol:7} {name:24} {len(feats):>3} trades ({nw} wins)")

    model = train(all_features)
    print(f"\n=== Model ({model.get('status')}) ===")
    print(json.dumps({k: v for k, v in model.items() if k != "msg"}, indent=2, default=str))
    if model.get("msg"):
        print(model["msg"])

    recs = propose(all_features, model)
    print("\n=== Proposals (REVIEW ONLY — not applied) ===")
    for r in recs:
        print("  " + r)

    if not args.no_ledger:
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        row = dict(
            ts=datetime.now(timezone.utc).isoformat(), loop_tag="self-improve",
            strategy="ALL", symbol=",".join(args.symbols),
            n_trades=len(all_features), model_status=model.get("status"),
            labelled=model.get("labelled"), accuracy=model.get("accuracy"),
            feature_weights=model.get("feature_weights"), proposals=recs,
        )
        with open(LEDGER, "a") as f:
            f.write(json.dumps(row) + "\n")
        print(f"\nAppended self-improve row to {LEDGER}")


if __name__ == "__main__":
    main()

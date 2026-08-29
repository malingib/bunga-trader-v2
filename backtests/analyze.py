"""Backtest analysis + strategy-improvement harness.

Builds on engine_corrected.run_momentum_backtest. Adds proper performance
metrics (profit factor, Sharpe, expectancy, avg win/loss, hold-time) and an
in/out-of-sample split so we can tune the strategy WITHOUT overfitting to the
whole window.

Usage:
  .venv/bin/python backtests/analyze.py                 # baseline + a few variants
  .venv/bin/python backtests/analyze.py --tune          # grid-search params
"""
from __future__ import annotations

import argparse
import statistics
from pathlib import Path

from engine_corrected import run_momentum_backtest
from data_loader import load, INTERNAL_SYMBOLS

START = 1000.0


def _metrics(res, start=START):
    """Derive richer stats from a BacktestResult."""
    pnls = res.trade_pnls
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_win = sum(wins)
    gross_loss = -sum(losses)
    pf = (gross_win / gross_loss) if gross_loss > 0 else float("inf") if gross_win > 0 else 0.0
    avg_win = statistics.mean(wins) if wins else 0.0
    avg_loss = statistics.mean(losses) if losses else 0.0
    expectancy = (statistics.mean(pnls) if pnls else 0.0)
    # Sharpe on per-bar equity returns (annualized-ish proxy: /sqrt(bars))
    eq = res.equity
    if len(eq) > 2:
        rets = [(eq[i] - eq[i - 1]) / eq[i - 1] for i in range(1, len(eq)) if eq[i - 1] > 0]
        sd = statistics.pstdev(rets) if len(rets) > 1 else 0.0
        sharpe = (statistics.mean(rets) / sd * (252 ** 0.5)) if sd > 0 else 0.0
    else:
        sharpe = 0.0
    return {
        "ret_pct": res.ret_pct,
        "trades": res.trades,
        "win_pct": res.win_pct,
        "profit_factor": round(pf, 2) if pf != float("inf") else 99.0,
        "expectancy": round(expectancy, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "sharpe": round(sharpe, 2),
        "max_dd": res.max_dd_pct,
        "killed": res.killed_by_dd,
        "final": res.final_equity,
    }


def _print(res, tag=""):
    m = _metrics(res)
    print(
        f"  {tag:22} ret={m['ret_pct']:>7}%  trades={m['trades']:>5}  "
        f"win={m['win_pct']:>5}%  PF={m['profit_factor']:>6}  "
        f"exp={m['expectancy']:>7}  sharpe={m['sharpe']:>5}  DD={m['max_dd']:>5}%"
    )
    return m


def run_symbol(symbol, params, source="yfinance", interval="1h", period="2y", out_of_sample=False):
    bars = load(symbol, source=source, interval=interval, period=period)
    res = run_momentum_backtest(symbol, bars, start_equity=START, **params)
    if not out_of_sample:
        return res
    # Split: train on first 70% of bars, test on last 30%.
    split = int(len(bars.c) * 0.7)
    train = type(bars)(bars.o[:split], bars.h[:split], bars.l[:split], bars.c[:split], bars.date[:split])
    test = type(bars)(bars.o[split:], bars.h[split:], bars.l[split:], bars.c[split:], bars.date[split:])
    r_train = run_momentum_backtest(symbol, train, start_equity=START, **params, label="train")
    r_test = run_momentum_backtest(symbol, test, start_equity=START, **params, label="test")
    return res, r_train, r_test


BASELINE = dict(sl_atr=1.2, rr=4.0, trend_ema=0, risk_pct=1.0, max_dd_pct=40.0, max_hold=15, warmup=200)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="yfinance")
    ap.add_argument("--interval", default="1h")
    ap.add_argument("--period", default="2y")
    ap.add_argument("--oos", action="store_true", help="show in/out-of-sample split")
    ap.add_argument("--tune", action="store_true", help="grid-search a few param sets")
    args = ap.parse_args()

    variants = {"baseline": BASELINE}
    if args.tune:
        variants.update({
            "rr2.5_em20": dict(sl_atr=1.5, rr=2.5, trend_ema=20, risk_pct=1.0, max_dd_pct=40.0, max_hold=20, warmup=200),
            "rr3_em50": dict(sl_atr=1.5, rr=3.0, trend_ema=50, risk_pct=1.0, max_dd_pct=35.0, max_hold=25, warmup=200),
            "sl2_em100": dict(sl_atr=2.0, rr=3.0, trend_ema=100, risk_pct=1.0, max_dd_pct=30.0, max_hold=30, warmup=200),
            "tight_em200": dict(sl_atr=1.0, rr=2.0, trend_ema=200, risk_pct=0.8, max_dd_pct=25.0, max_hold=10, warmup=200),
            # v2: sl2_em100 + consecutive-loss pause (drawdown circuit-breaker)
            "v2_pause3": dict(sl_atr=2.0, rr=3.0, trend_ema=100, risk_pct=1.0, max_dd_pct=30.0, max_hold=30, warmup=200, max_consec_losses=3),
            "v2_pause4": dict(sl_atr=2.0, rr=3.0, trend_ema=100, risk_pct=1.0, max_dd_pct=30.0, max_hold=30, warmup=200, max_consec_losses=4),
            "v2_pause5": dict(sl_atr=2.0, rr=3.0, trend_ema=100, risk_pct=1.0, max_dd_pct=30.0, max_hold=30, warmup=200, max_consec_losses=5),
            "v2_pause6": dict(sl_atr=2.0, rr=3.0, trend_ema=100, risk_pct=1.0, max_dd_pct=30.0, max_hold=30, warmup=200, max_consec_losses=6),
        })

    for sym in INTERNAL_SYMBOLS:
        print(f"\n=== {sym} ===")
        for tag, params in variants.items():
            if args.oos:
                full, tr, te = run_symbol(sym, params, args.source, args.interval, args.period, out_of_sample=True)
                _print(full, f"{tag} (full)")
                _print(tr, f"{tag} (train 70%)")
                _print(te, f"{tag} (test 30%)")
            else:
                res = run_symbol(sym, params, args.source, args.interval, args.period)
                _print(res, tag)


if __name__ == "__main__":
    import os
    os.chdir(Path(__file__).resolve().parents[1])
    main()

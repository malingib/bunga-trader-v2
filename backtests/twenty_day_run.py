"""CORRECTED 20-day / multi-session backtest — replaces the buggy twenty_day_run.py.

The original harness reported impossible returns (+1106% XAUUSD, +5059% NAS100
on a $50 account) because it sized P&L as (exit-entry) * 1.0 where
"1.0 unit" was treated as 1.0 * price-points. With gold at ~$4000,
1.0 unit is actually 1.0 standard lot (100 oz) whose P&L per point is
$100 — so one win moved the account ~8-56%, and 2500 trades
compounded to nonsense.

This version uses the SAME risk model as production
(core_backend.risk_engine.calculate_lot_size -> 1% account risk, pip-value
per instrument) via engine_corrected.run_momentum_backtest. Equity compounds;
a max-drawdown kill-switch stops trading if equity breaches it.

Because Yahoo now caps 1-min history at ~7 sessions, the input window here
is the real 7-session pull (data/market_cache/fmp_*_1min.csv). The
sizing math (not the window length) was the bug.
"""

from __future__ import annotations

from pathlib import Path

from engine_corrected import load_csv, run_momentum_backtest

CACHE_DIR = Path("data/market_cache")
RESULTS = Path(__file__).resolve().parent / "twenty_day_results.txt"

# Per-symbol validated configs (from backtests/explore_params_results.txt)
SYMBOLS = {
    "XAUUSD": dict(sl=1.2, rr=4.0, tf=0),
    "SP500": dict(sl=1.2, rr=1.5, tf=0),
    "NAS100": dict(sl=1.2, rr=1.5, tf=200),
}


def main() -> None:
    out = []
    log = out.append

    log("=" * 78)
    log("CORRECTED BACKTEST — momentum breakout, REAL risk-based sizing")
    log("=" * 78)
    log("Sizing: 1% account risk/trade (matches risk_engine.calculate_lot_size).")
    log("Equity compounds; kill-switch at 40% max drawdown.")
    log("Window: real 1-min pull (Yahoo caps history at ~7 sessions).")
    log("")
    log("THE OLD 'twenty_day' NUMBERS WERE A BUG, not an edge:")
    log("  old code: pnl = (exit-entry) * 1.0  -> '1 unit' = 1 price-point.")
    log("  gold ~$4000, 1.0 lot P&L/pt = $100 -> one win swung the $50 acct ~8%.")
    log("  +1106% / +5059% were units-vs-contracts confusion. Now fixed.")
    log("")

    results = []
    for sym, cfg in SYMBOLS.items():
        bars = load_csv(CACHE_DIR / f"fmp_{sym}_1min.csv")
        r = run_momentum_backtest(
            sym, bars,
            sl_atr=cfg["sl"], rr=cfg["rr"], trend_ema=cfg["tf"],
            start_equity=1000.0, risk_pct=1.0, max_dd_pct=40.0,
        )
        results.append(r)
        log(f"── {sym} (SL={cfg['sl']}× RR={cfg['rr']}×"
             f"{' +200MA' if cfg['tf'] else ''}) ──")
        log(f"   Bars: {len(bars.c)}  Close: {bars.c[0]:.2f} → {bars.c[-1]:.2f}")
        log(f"   Return: {r.ret_pct:+.2f}%   Trades: {r.trades}  "
             f"Win%: {r.win_pct}  MaxDD: {r.max_dd_pct}%")
        log(f"   Final: ${r.final_equity:.2f}   Min equity: ${r.min_equity_seen:.2f}")
        log(f"   Killed by DD: {r.killed_by_dd}")
        log("")

    log("-" * 78)
    log(f"{'Symbol':<10s} {'Ret%':>9s} {'Trades':>7s} {'Win%':>6s} "
         f"{'MaxDD%':>8s} {'Final$':>10s}")
    log("-" * 78)
    for r in results:
        log(f"{r.symbol:<10s} {r.ret_pct:>+8.2f} {r.trades:>7} "
             f"{r.win_pct:>6.1f} {r.max_dd_pct:>8.1f} {r.final_equity:>10.2f}")
    log("")
    log("INTERPRETATION: at HONEST sizing the momentum edge is NOT present")
    log("on this 7-session window — all three symbols lose money once 1% risk")
    log("and compounding are applied. The >5% target is currently UNMET for")
    log("live trading. Re-validate on a longer window before enabling auto-trade.")
    log("=" * 78)

    with open(RESULTS, "w") as f:
        f.write("\n".join(out) + "\n")
    print("\n".join(out))
    print(f"\nSaved -> {RESULTS}")


if __name__ == "__main__":
    main()

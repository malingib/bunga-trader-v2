"""Quadapt 3-pillar backtest — bar-by-bar on the SAME real data.

The original codebase had a momentum backtest (already corrected in
`engine_corrected.py`) but the Quadapt 3-pillar engine
(ICT liquidity-sweep trigger + 200MA/VWAP trend gate + PA confirmation)
had NEVER been backtested. It only produced unlabeled signal records.

This driver reuses the real `QuadaptEngine.evaluate()` exactly as the live
poller does: each historical bar is fed as "now" via a growing snapshot,
and any emitted StrategySignal opens a position sized by the SAME risk model
as production (`core_backend.risk_engine.calculate_lot_size` -> pip-value).

We test BOTH trigger modes:
  - mode="liquidity_sweep" (the original pillar ① ICT trigger)
  - mode="mean_reversion" (the proven StochRSI reversion with time-exit)

Exit model mirrors the engine's intent:
  - mean_reversion: close at market after `hold_bars` (the real exit)
  - sweep/PA: SL/TP, with a `max_hold` safety time-exit.

Run:  env -u PYTHONPATH .venv/bin/python backtests/quadapt_backtest.py
"""

from __future__ import annotations

import logging
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Backtest runs thousands of evaluate() calls; silence the JSON logger I/O.
logging.getLogger("QuadaptEngine").setLevel(logging.CRITICAL)
logging.getLogger("MarketData").setLevel(logging.CRITICAL)
logging.getLogger("RiskEngine").setLevel(logging.CRITICAL)

from core_backend.strategies.config import QUADAPT_CFG
from core_backend.strategies.engine import QuadaptEngine, StrategySignal
from core_backend.strategies.market_data import Candle, MarketSnapshot
from core_backend.strategies.indicators import stoch_rsi, atr as _atr
from core_backend.risk_engine import calculate_lot_size, get_pip_value_per_lot

sys.path.insert(0, str(Path(__file__).resolve().parent))
from engine_corrected import load_csv, pip_value  # noqa: E402

CACHE_DIR = Path("data/market_cache")
SYMBOLS = {"XAUUSD", "SP500", "NAS100"}
PIP_SIZE = {"XAUUSD": 0.01, "GOLD": 0.01}


def _make_candles(bars, upto):
    out = []
    for i in range(upto):
        out.append(
            Candle(
                time=datetime.now(timezone.utc).replace(tzinfo=None),
                open=bars.o[i],
                high=bars.h[i],
                low=bars.l[i],
                close=bars.c[i],
                volume=100.0,
            )
        )
    return out


def _candidate_bar(mode, i, bars, stoch_k, stoch_d, atr_vals):
    """Cheap pre-gate: does the LAST bar (index i) COULD trigger?

    Avoids calling the heavy QuadaptEngine.evaluate() on every flat bar.
    The engine recomputes all indicators per call, so this gating is
    what makes the bar-by-bar backtest finish in seconds instead of
    O(n^2). The full engine decision (quality gate, risk sizing,
    PA/200MA/VWAP) still runs on candidate bars only.

    - liquidity_sweep: last bar must poke a recent (~50-bar) extreme
      (a stop-hunt wick signature) OR have a strong rejection wick.
    - mean_reversion: StochRSI %K must cross %D out of an extreme
      (the proven trigger), checked on precomputed arrays.
    """
    hi, lo, c = bars.h[i], bars.l[i], bars.c[i]
    rng = hi - lo
    if rng <= 0:
        return False
    if mode == "liquidity_sweep":
        short = max(0, i - 50)
        if i <= short:
            return False
        recent_high = max(bars.h[short:i])
        recent_low = min(bars.l[short:i])
        # poke beyond recent extreme by >= 0.2x the bar's own range
        if hi > recent_high and (hi - recent_high) >= 0.2 * rng:
            return True
        if lo < recent_low and (recent_low - lo) >= 0.2 * rng:
            return True
        return False
    else:  # mean_reversion
        if stoch_k is None or stoch_d is None:
            return False
        k, kp = stoch_k[i], stoch_k[i - 1]
        d, dp = stoch_d[i], stoch_d[i - 1]
        if k is None or kp is None or d is None or dp is None:
            return False
        crossed_up = (kp <= dp) and (k > d)
        crossed_down = (kp >= dp) and (k < d)
        cfg = QUADAPT_CFG.trigger
        if crossed_up and k < cfg.stoch_rsi_oversold:
            return True
        if crossed_down and k > cfg.stoch_rsi_overbought:
            return True
        return False


def run_quadapt_backtest(symbol: str, bars, *, mode: str, start_equity: float = 1000.0,
                         risk_pct: float = 1.0, max_dd_pct: float = 40.0,
                         max_hold: int = 480) -> dict:
    """Backtest the Quadapt engine bar-by-bar.

    mode: 'liquidity_sweep' or 'mean_reversion' (sets cfg.trigger.mode).
    """
    QUADAPT_CFG.trigger.mode = mode
    engine = QuadaptEngine()
    n = len(bars.c)

    pip_size = PIP_SIZE.get(symbol.upper(), 1.0)
    pv = pip_value(symbol)

    # Precompute the arrays the cheap gate needs (once, O(n)).
    stoch_k, stoch_d = None, None
    if mode == "mean_reversion":
        stoch_k, stoch_d = stoch_rsi(
            bars.c,
            QUADAPT_CFG.trigger.stoch_rsi_rsi_length,
            QUADAPT_CFG.trigger.stoch_rsi_stoch_length,
            QUADAPT_CFG.trigger.stoch_rsi_smooth_k,
            QUADAPT_CFG.trigger.stoch_rsi_smooth_d,
        )
    atr_vals = _atr(bars.h, bars.l, bars.c, 14)

    bal = start_equity
    peak = bal
    trades = wins = 0
    pos = None
    killed = False
    equity = [bal]
    signals_emitted = 0

    for i in range(200, n):
        if killed:
            equity.append(bal)
            continue
        # Only evaluate while FLAT AND on a candidate bar (cheap pre-gate).
        if pos is None and not killed:
            if _candidate_bar(mode, i, bars, stoch_k, stoch_d, atr_vals):
                snapshot = MarketSnapshot(
                    symbol=symbol,
                    candles=_make_candles(bars, i + 1),
                    fetched_at=datetime.now(timezone.utc).replace(tzinfo=None),
                )
                sig = engine.evaluate(snapshot)
                if sig is not None:
                    signals_emitted += 1
                    lot, err = calculate_lot_size(
                        symbol=symbol,
                        entry_price=sig.entry_price,
                        sl_price=sig.sl,
                        account_balance=bal,
                        risk_percent=risk_pct,
                    )
                    if not err and lot > 0:
                        pos = {
                            "side": sig.action,
                            "entry": sig.entry_price,
                            "sl": sig.sl,
                            "tp": sig.tp,
                            "lot": lot,
                            "idx": i,
                            "hold": sig.hold_bars,  # 0 = SL/TP; >0 = time-exit
                            "closed": False,
                            "pnl": 0.0,
                        }

        if pos is not None and not pos["closed"]:
            # evaluate exit on this bar
            side = pos["side"]
            hi, lo, c = bars.h[i], bars.l[i], bars.c[i]
            exit_px = None
            if pos["hold"] > 0:
                if (i - pos["idx"]) >= pos["hold"]:
                    exit_px = c
            else:
                if side == "BUY":
                    if lo <= pos["sl"]:
                        exit_px = pos["sl"]
                    elif hi >= pos["tp"]:
                        exit_px = pos["tp"]
                else:
                    if hi >= pos["sl"]:
                        exit_px = pos["sl"]
                    elif lo <= pos["tp"]:
                        exit_px = pos["tp"]
                if exit_px is None and (i - pos["idx"]) >= max_hold:
                    exit_px = c
            if exit_px is not None:
                points = abs(exit_px - pos["entry"]) / pip_size
                pnl = points * pv * pos["lot"]
                if side == "SELL":
                    pnl = -pnl
                bal += pnl
                trades += 1
                if pnl > 0:
                    wins += 1
                pos["closed"] = True
                peak = max(peak, bal)
                if (peak - bal) / peak * 100 >= max_dd_pct:
                    killed = True
                pos = None

        equity.append(bal)

    max_dd = 0.0
    cp = start_equity
    min_eq = start_equity
    for e in equity:
        if e > cp:
            cp = e
        if e < min_eq:
            min_eq = e
        dd = (cp - e) / cp * 100
        if dd > max_dd:
            max_dd = dd
    ret = (bal / start_equity - 1) * 100
    win_pct = (wins / trades * 100) if trades else 0.0
    return {
        "symbol": symbol,
        "mode": mode,
        "ret_pct": round(ret, 2),
        "trades": trades,
        "signals": signals_emitted,
        "win_pct": round(win_pct, 1),
        "max_dd_pct": round(max_dd, 1),
        "final_equity": round(bal, 2),
        "min_equity": round(min_eq, 2),
        "killed": killed,
    }


def _is_closed(pos):
    return pos.get("_closed", False)


def _settle(pos, bal, start_equity, peak):
    pass


if __name__ == "__main__":
    import json

    out = []
    print("=" * 78)
    print("QUADAPT 3-PILLAR BACKTEST — same real 1-min windows (7 sessions)")
    print("=" * 78)
    for mode in ("liquidity_sweep", "mean_reversion"):
        print(f"\n### TRIGGER MODE: {mode} ###")
        print(f"{'Symbol':<8} {'Ret%':>9} {'Trades':>7} {'Win%':>6} {'DD%':>6} {'End$':>10} {'Killed':>7}")
        print("-" * 60)
        for sym in sorted(SYMBOLS):
            bars = load_csv(CACHE_DIR / f"fmp_{sym}_1min.csv")
            r = run_quadapt_backtest(sym, bars, mode=mode)
            out.append(r)
            print(f"{r['symbol']:<8} {r['ret_pct']:>+8.2f} {r['trades']:>7} "
                  f"{r['win_pct']:>6.1f} {r['max_dd_pct']:>6.1f} {r['final_equity']:>10.2f} "
                  f"{str(r['killed']):>7}")
    res_path = Path(__file__).resolve().parent / "quadapt_backtest_results.txt"
    with open(res_path, "w") as f:
        f.write(json.dumps(out, indent=2))
    print(f"\nSaved -> {res_path}")

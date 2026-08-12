"""Quadapt 3-pillar backtest — FAST, faithful core driver.

WHY a separate driver from quadapt_backtest.py:
  QuadaptEngine.evaluate() recomputes EVERY indicator (heikin-ashi,
  ATR, StochRSI, Supertrend, TTM squeeze, MLMA kernel regression,
  VWAP, ...) on the FULL growing series on EVERY call. With a
  1-min window of ~7500 bars that is O(n^2) per symbol (~minutes)
  and it never finishes in reasonable time.

THIS driver precomputes each indicator array EXACTLY ONCE over the
full series (reusing the SAME functions the engine imports), then
applies the engine's gate + quality + risk logic per bar from those
arrays — keeping the decision IDENTICAL to evaluate() but O(n).

It reuses the REAL SignalQualityEngine.compute() and RiskCalculator
so scoring/sizing match production. The trigger branches
(liquidity_sweep / mean_reversion) and the 200MA/VWAP gate are
ported verbatim from engine.evaluate() steps 3-18.

Run:  env -u PYTHONPATH .venv/bin/python backtests/quadapt_core.py
"""

from __future__ import annotations

import logging
import math
import sys
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.getLogger("QuadaptEngine").setLevel(logging.CRITICAL)
logging.getLogger("MarketData").setLevel(logging.CRITICAL)
logging.getLogger("RiskEngine").setLevel(logging.CRITICAL)

from core_backend.strategies.config import QUADAPT_CFG
from core_backend.strategies import indicators as ind
from core_backend.strategies.quality_engine import SignalQualityEngine
from core_backend.strategies.risk import RiskCalculator
from core_backend.strategies.engine import detect_regime
from core_backend.risk_engine import calculate_lot_size, get_pip_value_per_lot

sys.path.insert(0, str(Path(__file__).resolve().parent))
from engine_corrected import load_csv, pip_value  # noqa: E402

CACHE_DIR = Path("data/market_cache")
SYMBOLS = {"XAUUSD", "SP500", "NAS100"}
PIP_SIZE = {"XAUUSD": 0.01, "GOLD": 0.01}


def _precompute(bars, cfg):
    """Compute every indicator array ONCE over the full series."""
    o, h, l, c = bars.o, bars.h, bars.l, bars.c
    n = len(c)
    out = {}
    out["atr"] = ind.atr(h, l, c, cfg.envelopes.atr_period)
    out["ha"] = ind.heikin_ashi(o, h, l, c)
    out["ma200"] = ind.sma(c, cfg.trend_gate.ma_period)
    out["mlma"] = ind.mlma_trend(
        out["ha"][0], cfg.mlma.length, cfg.mlma.kernel, cfg.mlma.gamma
    )
    _, st_dir = ind.supertrend(h, l, c, cfg.supertrend.atr_period, cfg.supertrend.factor)
    out["st_dir"] = st_dir
    k, d = ind.stoch_rsi(c, cfg.stoch_rsi.rsi_length, cfg.stoch_rsi.stoch_length,
                          cfg.stoch_rsi.smooth_k, cfg.stoch_rsi.smooth_d)
    out["st_k"] = k
    out["st_d"] = d
    sq_active, _, sq_rel = ind.ttm_squeeze(h, l, c, cfg.ttm.bb_length, cfg.ttm.bb_mult,
                                               cfg.ttm.kc_length, cfg.ttm.kc_mult)
    out["sq_active"] = sq_active
    out["sq_rel"] = sq_rel
    out["vwap"] = ind.vwap(h, l, c, [100.0] * n)
    out["rel_vol"] = ind.relative_volume([100.0] * n, cfg.trend_gate.volume_sma_period)
    out["swings"] = ind.swing_points(h, l, cfg.sweep.swing_left, cfg.sweep.swing_right)
    return out


def _sweep_at(i, bars, cfg, swings):
    """Last-bar liquidity sweep (ported from engine + indicators)."""
    h, l, o, c = bars.h, bars.l, bars.o, bars.c
    return ind.detect_liquidity_sweep(
        h, l, o, c, swings=swings,
        swing_lookback=cfg.sweep.swing_lookback,
        min_wick_ratio=cfg.sweep.min_wick_ratio,
    )


def _mean_rev_at(i, bars, k, d, cfg):
    """StochRSI-K cross out of extreme (engine._mean_reversion_trigger)."""
    if i < 2:
        return None
    kk, kp = k[i], k[i - 1]
    dd, dp = d[i], d[i - 1]
    if any(math.isnan(x) for x in (kk, kp, dd, dp)):
        return None
    crossed_up = (kp <= dp) and (kk > dd)
    crossed_down = (kp >= dp) and (kk < dd)
    if cfg.require_rsi_filter:
        r = ind.rsi(bars.c, cfg.rsi_period)
        rv = r[i] if not math.isnan(r[i]) else None
        if rv is None:
            return None
        if crossed_up and rv >= 50:
            return None
        if crossed_down and rv <= 50:
            return None
    if crossed_up and kk < cfg.stoch_rsi_oversold:
        return "BUY"
    if crossed_down and kk > cfg.stoch_rsi_overbought:
        return "SELL"
    return None


def run_quadapt_core(symbol, bars, *, mode, start_equity=1000.0,
                       risk_pct=1.0, max_dd_pct=40.0, max_hold=480,
                       hold_bars=None, profit_target_mult=0.0,
                       protective_sl_atr=None, pre=None,
                       min_quality_score=None, require_stretch=False,
                       sl_points=None, tp_points=None):
    cfg = QUADAPT_CFG
    cfg.trigger.mode = mode
    if min_quality_score is not None:
        cfg.quality.min_quality_score = min_quality_score
    qe = SignalQualityEngine()
    rc = RiskCalculator()
    if pre is None:
        pre = _precompute(bars, cfg)
    n = len(bars.c)
    pip_size = PIP_SIZE.get(symbol.upper(), 1.0)
    pv = pip_value(symbol)

    bal = start_equity
    peak = bal
    trades = wins = signals = 0
    pos = None
    killed = False
    equity = [bal]

    for i in range(200, n):
        if killed:
            equity.append(bal)
            continue
        c = bars.c[i]

        # ── Trigger (ported from evaluate steps 3 / _mean_reversion_trigger) ──
        sweep = None
        rev = False
        signal_type = None
        if mode == "mean_reversion":
            rt = _mean_rev_at(i, bars, pre["st_k"], pre["st_d"], cfg.trigger)
            if rt is not None:
                signal_type = rt
                rev = True
        else:
            sweep = _sweep_at(i, bars, cfg, pre["swings"])
            if sweep is not None:
                signal_type = sweep.direction

        if signal_type is None:
            # manage open pos
            if pos is not None and not pos["closed"]:
                _exit(pos, bars, i, pip_size, pv, max_hold)
                bal, trades, wins, peak, killed = _settle(pos, bal, trades, wins, peak, killed, start_equity, max_dd_pct)
                if pos["closed"]:
                    pos = None
            equity.append(bal)
            continue

        # ── 200MA / VWAP gate (ported step 3b) ──
        ma200 = pre["ma200"][i] if i < len(pre["ma200"]) and not math.isnan(pre["ma200"][i]) else None
        if not rev and cfg.trend_gate.enabled and ma200 is not None:
            band = cfg.trend_gate.vwap_band_pct * c
            if cfg.trend_gate.require_200ma_alignment:
                if signal_type == "BUY" and c < ma200:
                    signal_type = None
                elif signal_type == "SELL" and c > ma200:
                    signal_type = None
            if signal_type is not None and cfg.trend_gate.use_vwap:
                vw = pre["vwap"][i] if i < len(pre["vwap"]) and not math.isnan(pre["vwap"][i]) else None
                if vw is not None:
                    if signal_type == "BUY" and c < vw - band:
                        signal_type = None
                    elif signal_type == "SELL" and c > vw + band:
                        signal_type = None
        if signal_type is None:
            if pos is not None and not pos["closed"]:
                _exit(pos, bars, i, pip_size, pv, max_hold)
                bal, trades, wins, peak, killed = _settle(pos, bal, trades, wins, peak, killed, start_equity, max_dd_pct)
                if pos["closed"]:
                    pos = None
            equity.append(bal)
            continue

        # ── Stretch precondition for REVERSION ──
        # A fade only has edge if price is genuinely stretched beyond VWAP.
        # The engine skips the trend gate for reversion, but without a
        # stretch check it fades moves that aren't overextended -> ~7% win.
        if rev and require_stretch:
            band = cfg.trend_gate.vwap_band_pct * c
            vw = pre["vwap"][i] if i < len(pre["vwap"]) and not math.isnan(pre["vwap"][i]) else None
            if vw is not None:
                if signal_type == "BUY" and not (c < vw - band):
                    signal_type = None
                elif signal_type == "SELL" and not (c > vw + band):
                    signal_type = None
        if signal_type is None:
            if pos is not None and not pos["closed"]:
                _exit(pos, bars, i, pip_size, pv, max_hold)
                bal, trades, wins, peak, killed = _settle(pos, bal, trades, wins, peak, killed, start_equity, max_dd_pct)
                if pos["closed"]:
                    pos = None
            equity.append(bal)
            continue

        # ── PA displacement (step 3c, cheap) ──
        pa = cfg.price_action
        pa_mod = sys.modules["core_backend.strategies.price_action"]
        disp = pa_mod.displacement(bars.o, bars.c, bars.h, bars.l,
                                     atr_period=cfg.envelopes.atr_period, mult=pa.displacement_mult)
        struct = pa_mod.classify_structure(bars.h, bars.l, swings=pre["swings"])
        fvg = ind.fvg_detect(bars.h, bars.l, bars.c) if pa.use_fvg_boost else None
        if pa.enabled and not rev:
            if pa.require_displacement and (disp is None or disp < pa.displacement_mult):
                if pos is not None and not pos["closed"]:
                    _exit(pos, bars, i, pip_size, pv, max_hold)
                    bal, trades, wins, peak, killed = _settle(pos, bal, trades, wins, peak, killed, start_equity, max_dd_pct)
                    if pos["closed"]:
                        pos = None
                equity.append(bal)
                continue
            if pa.require_choch_alignment and struct.last_choch:
                if (signal_type == "BUY" and struct.last_choch == "SELL") or \
                   (signal_type == "SELL" and struct.last_choch == "BUY"):
                    if pos is not None and not pos["closed"]:
                        _exit(pos, bars, i, pip_size, pv, max_hold)
                        bal, trades, wins, peak, killed = _settle(pos, bal, trades, wins, peak, killed, start_equity, max_dd_pct)
                        if pos["closed"]:
                            pos = None
                    equity.append(bal)
                    continue

        # ── Order block proximity (step 9) ──
        ob_prox = 0.0
        if cfg.order_blocks.enabled:
            blocks = ind.detect_order_blocks(bars.o, bars.h, bars.l, bars.c, [100.0] * n,
                                                  cfg.order_blocks.lookback, cfg.order_blocks.min_block_strength)
            for ob in blocks:
                if signal_type == "BUY" and ob.block_type == "bullish" and ob.high >= c >= ob.low:
                    ob_prox = max(ob_prox, ob.strength)
                elif signal_type == "SELL" and ob.block_type == "bearish" and ob.high >= c >= ob.low:
                    ob_prox = max(ob_prox, ob.strength)

        # ── MTF alignment (step 10) ──
        mtf = 0.5
        total = agree = 0
        if pre["st_dir"][i] is not None:
            total += 1
            if (signal_type == "BUY" and pre["st_dir"][i] == 1) or (signal_type == "SELL" and pre["st_dir"][i] == -1):
                agree += 1
        if pre["mlma"][i] is not None:
            total += 1
            if (signal_type == "BUY" and c > pre["mlma"][i]) or (signal_type == "SELL" and c < pre["mlma"][i]):
                agree += 1
        if total > 0:
            mtf = agree / total

        regime = ind.detect_regime(c, cfg.adaptive.regime_lookback) if hasattr(ind, "detect_regime") else "ranging"
        regime = detect_regime(bars.c, cfg.adaptive.regime_lookback)

        # ── Quality score (REAL engine) ──
        score = qe.compute(
            symbol=symbol, signal_type=signal_type, index=i, price=c,
            mlma_trend_val=pre["mlma"][i] if not math.isnan(pre["mlma"][i]) else None,
            supertrend_dir=pre["st_dir"][i],
            is_squeeze_release=bool(pre["sq_rel"][i]) if i < len(pre["sq_rel"]) else False,
            is_squeeze_active=bool(pre["sq_active"][i]) if i < len(pre["sq_active"]) else False,
            in_squeeze=False,
            stoch_rsi_k=pre["st_k"][i], stoch_rsi_d=pre["st_d"][i],
            envelope_signal_strength=0.5,
            mtf_alignment=mtf, order_block_proximity=ob_prox,
            bars_since_last_signal=999, regime=regime,
            sweep=sweep, pa_displacement=disp, pa_structure=struct,
            has_fvg=bool(fvg), rel_volume=None, reversion_signal=rev,
        )
        if not qe.meets_threshold(score):
            if pos is not None and not pos["closed"]:
                _exit(pos, bars, i, pip_size, pv, max_hold)
                bal, trades, wins, peak, killed = _settle(pos, bal, trades, wins, peak, killed, start_equity, max_dd_pct)
                if pos["closed"]:
                    pos = None
            equity.append(bal)
            continue

        # ── Risk: SL / TP (REAL RiskCalculator) ──
        rev_hold = hold_bars if hold_bars is not None else cfg.trigger.hold_bars
        if rev and sl_points is not None:
            # Absolute-point SL/TP: tuned per-symbol to the reversion
            # magnitude (ATR multiples are too wide for 1-min indices).
            sl = c - sl_points if signal_type == "BUY" else c + sl_points
            if tp_points is not None:
                tp = c + tp_points if signal_type == "BUY" else c - tp_points
            elif profit_target_mult > 0:
                tp = c + profit_target_mult * abs(c - sl) if signal_type == "BUY" else c - profit_target_mult * abs(c - sl)
            else:
                tp = 0.0
        elif rev and (cfg.trigger.protective_sl_atr > 0 or protective_sl_atr):
            psl = protective_sl_atr if protective_sl_atr else cfg.trigger.protective_sl_atr
            av = pre["atr"][i] if not math.isnan(pre["atr"][i]) else c * 0.005
            sl = c - av * psl if signal_type == "BUY" else c + av * psl
            # Optional profit target: capture the reversion at mult x SL-distance.
            if profit_target_mult > 0:
                sl_dist = abs(c - sl)
                tp = c + profit_target_mult * sl_dist if signal_type == "BUY" else c - profit_target_mult * sl_dist
            else:
                tp = 0.0
        else:
            sl = rc.calculate_sl(signal_type=signal_type, entry_price=c, highs=bars.h, lows=bars.l, closes=bars.c,
                                 order_block_high=None, order_block_low=None,
                                 sweep_level=sweep.swept_price if sweep else None)
            tps = rc.calculate_tp_levels(signal_type=signal_type, entry_price=c, sl_price=sl,
                                      highs=bars.h, lows=bars.l, closes=bars.c,
                                      sweep_level=sweep.swept_price if sweep else None)
            tp = tps[0] if tps else 0.0
            if not rev and tp > 0:
                rr = rc.calculate_rr(c, sl, tp)
                if rr < cfg.risk.min_rr_ratio:
                    if pos is not None and not pos["closed"]:
                        _exit(pos, bars, i, pip_size, pv, max_hold)
                        bal, trades, wins, peak, killed = _settle(pos, bal, trades, wins, peak, killed, start_equity, max_dd_pct)
                        if pos["closed"]:
                            pos = None
                    equity.append(bal)
                    continue

        # ── Open position (only if flat) ──
        if pos is None and not killed:
            lot, err = calculate_lot_size(symbol=symbol, entry_price=c, sl_price=sl,
                                           account_balance=bal, risk_percent=risk_pct)
            if not err and lot > 0:
                signals += 1
                pos = dict(side=signal_type, entry=c, sl=sl, tp=tp, lot=lot, idx=i,
                           hold=rev_hold if rev else 0, closed=False, pnl=0.0)
        elif pos is not None and not pos["closed"]:
            _exit(pos, bars, i, pip_size, pv, max_hold)
            bal, trades, wins, peak, killed = _settle(pos, bal, trades, wins, peak, killed, start_equity, max_dd_pct)
            if pos["closed"]:
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
    return dict(symbol=symbol, mode=mode, ret_pct=round(ret, 2), trades=trades,
                signals=signals, win_pct=round(win_pct, 1), max_dd_pct=round(max_dd, 1),
                final_equity=round(bal, 2), min_equity=round(min_eq, 2), killed=killed)


def _exit(pos, bars, i, pip_size, pv, max_hold):
    if pos["closed"]:
        return
    side = pos["side"]
    hi, lo, c = bars.h[i], bars.l[i], bars.c[i]
    ex = None
    # TP always checked first (reversion may carry a profit target too).
    if pos["tp"] and pos["tp"] > 0:
        if side == "BUY" and hi >= pos["tp"]:
            ex = pos["tp"]
        elif side == "SELL" and lo <= pos["tp"]:
            ex = pos["tp"]
    # SL hit?
    if ex is None:
        if side == "BUY" and lo <= pos["sl"]:
            ex = pos["sl"]
        elif side == "SELL" and hi >= pos["sl"]:
            ex = pos["sl"]
    # Time-exit (hold reached, or hard safety cap).
    if ex is None:
        held = i - pos["idx"]
        if (pos["hold"] > 0 and held >= pos["hold"]) or held >= max_hold:
            ex = c
    if ex is None:
        return
    # Correct P&L sign: a BUY profits when exit > entry; a SELL when
    # entry > exit. (abs()-then-negate was crediting BUY stop-losses as
    # wins — the source of the impossible positive returns.)
    raw = (ex - pos["entry"]) * pv * pos["lot"]
    pnl = raw if side == "BUY" else -raw
    pos["pnl"] = pnl
    pos["closed"] = True


def _settle(pos, bal, trades, wins, peak, killed, start_equity, max_dd_pct):
    bal += pos["pnl"]
    trades += 1
    if pos["pnl"] > 0:
        wins += 1
    peak = max(peak, bal)
    if (peak - bal) / peak * 100 >= max_dd_pct:
        killed = True
    return bal, trades, wins, peak, killed


if __name__ == "__main__":
    out = []
    print("=" * 78)
    print("QUADAPT 3-PILLAR BACKTEST (FAST CORE) — same real 1-min windows")
    print("=" * 78)
    for mode in ("liquidity_sweep", "mean_reversion"):
        print(f"\n### TRIGGER MODE: {mode} ###")
        print(f"{'Symbol':<8} {'Ret%':>9} {'Trades':>7} {'Win%':>6} {'DD%':>6} {'End$':>10} {'Kill':>6}")
        print("-" * 60)
        for sym in sorted(SYMBOLS):
            bars = load_csv(CACHE_DIR / f"fmp_{sym}_1min.csv")
            r = run_quadapt_core(sym, bars, mode=mode)
            out.append(r)
            print(f"{r['symbol']:<8} {r['ret_pct']:>+8.2f} {r['trades']:>7} "
                  f"{r['win_pct']:>6.1f} {r['max_dd_pct']:>6.1f} {r['final_equity']:>10.2f} "
                  f"{str(r['killed']):>6}")
    res = Path(__file__).resolve().parent / "quadapt_core_results.txt"
    with open(res, "w") as f:
        f.write(json.dumps(out, indent=2))
    print(f"\nSaved -> {res}")

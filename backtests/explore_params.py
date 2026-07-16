"""Multi-symbol parameter exploration for Momentum Breakout strategy.

Phases:
  A — Broad grid search (SL=0.8-2.0, RR=1.5-4.0) — finds best config per symbol
  B — Per-symbol walk-forward on each best config
  C — Top configs + 200MA trend filter variant
"""

import csv, math, sys, time
from pathlib import Path

SYMBOLS = {
    "USOIL":  "data/market_cache/fmp_USOIL_1min.csv",
    "SP500":  "data/market_cache/fmp_SP500_1min.csv",
    "NAS100": "data/market_cache/fmp_NAS100_1min.csv",
    "XAUUSD": "data/market_cache/fmp_XAUUSD_1min.csv",
}
RESULTS = Path(__file__).resolve().parent / "explore_params_results.txt"
MIN_TRADES = 200  # skip configs with fewer trades (statistical noise)

out = []
def log(s=""):
    out.append(s); print(s)

def sma(data, p):
    if len(data) < p: return [float("nan")]*len(data)
    s = sum(data[:p])
    out = [s/p]
    for i in range(p, len(data)):
        s += data[i] - data[i-p]
        out.append(s/p)
    return [float("nan")]*(p-1) + out

def load_data(path):
    d = {"o":[],"h":[],"l":[],"c":[]}
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            d["o"].append(float(r["open"]))
            d["h"].append(float(r["high"]))
            d["l"].append(float(r["low"]))
            d["c"].append(float(r["close"]))
    return d, len(d["c"])

def compute_atr(d, n):
    tr = [0.0]
    for i in range(1, n):
        tr.append(max(d["h"][i]-d["l"][i],
                      abs(d["h"][i]-d["c"][i-1]),
                      abs(d["l"][i]-d["c"][i-1])))
    return sma(tr, 14)

def backtest(d, n, atr, sl_atr, rr, trend_filter=False, ma200=None, start=0, end=None):
    if end is None: end = n
    bal = 1000.0
    trades = wins = 0
    pos = None
    peak = bal
    equity = [bal]
    si = max(200, start)

    for i in range(si, min(end, n)):
        if pos is not None:
            exit_px = None
            if pos["side"] == "BUY":
                if d["l"][i] <= pos["sl"]: exit_px = pos["sl"]
                elif d["h"][i] >= pos["tp"]: exit_px = pos["tp"]
            else:
                if d["h"][i] >= pos["sl"]: exit_px = pos["sl"]
                elif d["l"][i] <= pos["tp"]: exit_px = pos["tp"]
            if exit_px is None and (i - pos["idx"]) >= 15:
                exit_px = d["c"][i]
            if exit_px is not None:
                pnl = (exit_px - pos["entry"]) * 1.0 if pos["side"] == "BUY" else \
                      (pos["entry"] - exit_px) * 1.0
                bal += pnl; trades += 1
                if pnl > 0: wins += 1
                equity.append(bal)
                if bal > peak: peak = bal
                pos = None
            else:
                equity.append(bal)
        else:
            equity.append(bal)
        if pos is not None:
            continue

        a = atr[i]
        if a <= 0 or math.isnan(a) or i < 2: continue

        # Trend filter: only take longs above MA, shorts below MA
        if trend_filter and ma200 is not None:
            ma = ma200[i]
            if math.isnan(ma): continue
            if d["c"][i] > max(d["h"][i-1], d["h"][i-2]) and d["c"][i] > d["o"][i]:
                # Bullish breakout — only take if price above 200MA
                if d["c"][i] < ma: continue
            elif d["c"][i] < min(d["l"][i-1], d["l"][i-2]) and d["c"][i] < d["o"][i]:
                # Bearish breakout — only take if price below 200MA
                if d["c"][i] > ma: continue
            else:
                continue

        sl_d = a * sl_atr
        tp_d = sl_d * rr
        if d["c"][i] > max(d["h"][i-1], d["h"][i-2]) and d["c"][i] > d["o"][i]:
            pos = {"side":"BUY","entry":d["c"][i],
                   "sl":d["c"][i]-sl_d,"tp":d["c"][i]+tp_d,"idx":i}
        elif d["c"][i] < min(d["l"][i-1], d["l"][i-2]) and d["c"][i] < d["o"][i]:
            pos = {"side":"SELL","entry":d["c"][i],
                   "sl":d["c"][i]+sl_d,"tp":d["c"][i]-tp_d,"idx":i}

    # Max drawdown
    max_dd = 0
    cp = 1000.0
    for e in equity:
        if e > cp: cp = e
        dd = (cp-e)/cp*100
        if dd > max_dd: max_dd = dd

    wr = wins/trades*100 if trades else 0
    return {"ret":round((bal/1000-1)*100,2),"trades":trades,
            "win%":round(wr,1),"dd":round(max_dd,1),"equity":equity}


# ─────────────────────────────────────────────
#  PHASE A: Broad grid search (no trend filter)
# ─────────────────────────────────────────────
log("=" * 72)
log("PHASE A: BROAD GRID SEARCH — all symbols")
log("=" * 72)

SL_VALUES = [0.8, 1.0, 1.2, 1.4, 1.6, 1.8, 2.0]
RR_VALUES = [1.5, 2.0, 2.5, 3.0, 3.5, 4.0]

all_best = {}  # symbol -> list of (ret, sl, rr, trades, dd)

for sym, datapath in SYMBOLS.items():
    log(f"\n── {sym} ──")
    d, n = load_data(datapath)
    atr = compute_atr(d, n)
    log(f"  Candles: {n}  ({d['c'][0]:.2f} → {d['c'][-1]:.2f}, "
        f"{(d['c'][-1]/d['c'][0]-1)*100:+.2f}%)")

    results = []
    for sl in SL_VALUES:
        for rr in RR_VALUES:
            r = backtest(d, n, atr, sl, rr)
            if r["trades"] < MIN_TRADES:
                continue
            results.append((r["ret"], sl, rr, r["trades"], r["dd"], r))

    results.sort(key=lambda x: x[0], reverse=True)

    log(f"  Top 10 configs (sorted by return):")
    log(f"  {'SL':>5s} {'RR':>5s} {'Return':>9s} {'Win%':>5s} {'Trades':>6s} {'DD':>6s}")
    log(f"  {'-'*42}")
    for ret, sl, rr, trades, dd, r in results[:10]:
        log(f"  {sl:>4.1f}× {rr:>4.1f}× {ret:>+8.2f}% {r['win%']:>5.1f} {trades:>5d} {dd:>5.1f}%")

    all_best[sym] = results

log("\n" + "=" * 72)
log("PHASE A SUMMARY — Best config per symbol (no trend filter):")
log(f"  {'Symbol':<8s} {'SL':>5s} {'RR':>5s} {'Return':>9s} {'Win%':>5s} {'Trades':>6s} {'DD':>6s}")
log(f"  {'-'*50}")
for sym in SYMBOLS:
    best = all_best[sym][0]
    ret, sl, rr, trades, dd, r = best
    log(f"  {sym:<8s} {sl:>4.1f}× {rr:>4.1f}× {ret:>+8.2f}% {r['win%']:>5.1f} {trades:>5d} {dd:>5.1f}%")


# ─────────────────────────────────────────────
#  PHASE B: Walk-forward on each best config
# ─────────────────────────────────────────────
log("\n" + "=" * 72)
log("PHASE B: PER-SYMBOL WALK-FORWARD (best config from grid)")
log("=" * 72)

for sym, datapath in SYMBOLS.items():
    d, n = load_data(datapath)
    atr = compute_atr(d, n)
    best = all_best[sym][0]
    ret, sl, rr, trades, dd, r_full_grid = best

    log(f"\n── {sym} (SL={sl}×, RR={rr}×) ──")
    qf = n // 4
    logs_q = []
    for q in range(4):
        s, e = q*qf, (q+1)*qf if q < 3 else n
        r = backtest(d, n, atr, sl, rr, start=s, end=e)
        logs_q.append(r)
        log(f"  Q{q+1}: Return={r['ret']:>+8.2f}% Trades={r['trades']:>4d} Win%={r['win%']:>5.1f} DD={r['dd']:>5.1f}%")

    neg = sum(1 for r in logs_q if r["ret"] < 0)
    avg_q = round(sum(r["ret"] for r in logs_q) / 4, 2)
    min_bal = min(r_full_grid["equity"])
    log(f"  ──────────────────────────────────────")
    log(f"  Full: {r_full_grid['ret']:+.2f}% DD={r_full_grid['dd']}% Trades={r_full_grid['trades']}")
    log(f"  Quarters: {neg}/4 negative | Avg quarterly: {avg_q:+.2f}%")
    log(f"  Min equity: ${min_bal:.2f} | Target >5%: {'✅ MET' if r_full_grid['ret'] > 5 else '❌ NOT MET'}")


# ─────────────────────────────────────────────
#  PHASE C: 200MA trend filter on top configs
# ─────────────────────────────────────────────
log("\n" + "=" * 72)
log("PHASE C: TREND FILTER (200MA gate) — top 5 configs per symbol")
log("=" * 72)

for sym, datapath in SYMBOLS.items():
    d, n = load_data(datapath)
    atr = compute_atr(d, n)
    ma200 = sma(d["c"], 200)

    log(f"\n── {sym} (with 200MA trend filter) ──")
    log(f"  {'SL':>5s} {'RR':>5s} {'Return':>9s} {'Win%':>5s} {'Trades':>6s} {'DD':>6s}")
    log(f"  {'-'*42}")

    best_filtered = []
    for sl in SL_VALUES:
        for rr in RR_VALUES:
            r = backtest(d, n, atr, sl, rr, trend_filter=True, ma200=ma200)
            if r["trades"] < MIN_TRADES:
                continue
            best_filtered.append((r["ret"], sl, rr, r["trades"], r["dd"], r))

    best_filtered.sort(key=lambda x: x[0], reverse=True)
    for ret, sl, rr, trades, dd, r in best_filtered[:5]:
        name_nf = next((x for x in all_best[sym] if x[1]==sl and x[2]==rr), None)
        delta = ""
        if name_nf:
            delta = f" (Δ vs no-filter: {ret - name_nf[0]:+.2f}%)"
        log(f"  {sl:>4.1f}× {rr:>4.1f}× {ret:>+8.2f}% {r['win%']:>5.1f} {trades:>5d} {dd:>5.1f}%{delta}")


# ─────────────────────────────────────────────
#  FINAL RECOMMENDATION
# ─────────────────────────────────────────────
log("\n" + "=" * 72)
log("FINAL RECOMMENDATIONS")
log("=" * 72)

for sym in SYMBOLS:
    best_raw = all_best[sym][0]
    ret_r, sl_r, rr_r, trades_r, dd_r, r_raw = best_raw
    target = "✅ MET" if ret_r > 5 else "❌ NOT MET"

    log(f"\n  {sym}:")
    log(f"    Best raw config:    SL={sl_r}×, RR={rr_r}× → {ret_r:+.2f}% (DD={dd_r}%, Win={r_raw['win%']}%")
    log(f"    Target >5%:         {target}")

with open(RESULTS, "w") as f:
    f.write("\n".join(out))
log(f"\n\nFull results saved to {RESULTS}")

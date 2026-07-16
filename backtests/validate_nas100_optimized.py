"""Verify NAS100 best config: SL=1.2, RR=1.5 with 200MA trend filter.

The grid showed +250.43% with 39.6% DD — need to validate walk-forward stability.
"""
import csv, math
from pathlib import Path

DATA = "data/market_cache/fmp_NAS100_1min.csv"
RESULTS = Path(__file__).resolve().parent / "validate_nas100_optimized.txt"

d = {"o":[],"h":[],"l":[],"c":[]}
with open(DATA, newline="") as f:
    for r in csv.DictReader(f):
        d["o"].append(float(r["open"]))
        d["h"].append(float(r["high"]))
        d["l"].append(float(r["low"]))
        d["c"].append(float(r["close"]))
n = len(d["c"])

def sma(data, p):
    if len(data) < p: return [float("nan")]*len(data)
    s, out = sum(data[:p]), [sum(data[:p])/p]
    for i in range(p, len(data)):
        s += data[i]-data[i-p]; out.append(s/p)
    return [float("nan")]*(p-1)+out

tr = [0.0]
for i in range(1, n):
    tr.append(max(d["h"][i]-d["l"][i], abs(d["h"][i]-d["c"][i-1]), abs(d["l"][i]-d["c"][i-1])))
atr = sma(tr, 14)
ma200 = sma(d["c"], 200)

def backtest(sl_atr, rr, trend_filter=True, start=0, end=None):
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
        if pos is not None: continue
        a = atr[i]
        if a <= 0 or math.isnan(a) or i < 2: continue

        if trend_filter:
            ma = ma200[i]
            if math.isnan(ma): continue

        sl_d = a * sl_atr
        tp_d = sl_d * rr

        if d["c"][i] > max(d["h"][i-1], d["h"][i-2]) and d["c"][i] > d["o"][i]:
            if trend_filter and d["c"][i] < ma: continue
            pos = {"side":"BUY","entry":d["c"][i],
                   "sl":d["c"][i]-sl_d,"tp":d["c"][i]+tp_d,"idx":i}
        elif d["c"][i] < min(d["l"][i-1], d["l"][i-2]) and d["c"][i] < d["o"][i]:
            if trend_filter and d["c"][i] > ma: continue
            pos = {"side":"SELL","entry":d["c"][i],
                   "sl":d["c"][i]+sl_d,"tp":d["c"][i]-tp_d,"idx":i}

    max_dd = 0
    cp = 1000.0
    for e in equity:
        if e > cp: cp = e
        dd = (cp-e)/cp*100
        if dd > max_dd: max_dd = dd

    wr = wins/trades*100 if trades else 0
    return {"ret":round((bal/1000-1)*100,2),"trades":trades,
            "win%":round(wr,1),"dd":round(max_dd,1),"equity":equity}

out = []
def log(s):
    out.append(s); print(s)

log("=" * 70)
log("OPTIMIZED — Momentum Breakout (NAS100 1-min) + 200MA Trend Filter")
log("=" * 70)
log(f"  Data: {n} candles (${d['c'][0]:.2f} → ${d['c'][-1]:.2f}, {(d['c'][-1]/d['c'][0]-1)*100:+.2f}%)")
log(f"  Method: 2-bar breakout + 200MA trend filter + bullish/bearish bar")
log(f"  Exit: ATR(14)-based SL/TP + 15-bar time exit")
log(f"  Sizing: Fixed 1.0 units")
log("")

# Scan 6 configs WITH trend filter
configs = [
    ("SL=1.2 RR=1.5",  1.2, 1.5),
    ("SL=1.2 RR=2.0",  1.2, 2.0),
    ("SL=1.0 RR=1.5",  1.0, 1.5),
    ("SL=1.0 RR=2.0",  1.0, 2.0),
    ("SL=1.4 RR=1.5",  1.4, 1.5),
    ("SL=1.4 RR=2.0",  1.4, 2.0),
]
log(f"{'Config':<25s} {'Return':>9s} {'Win%':>5s} {'Trades':>6s} {'DD':>7s}")
log("-" * 60)
for name, sl, rr in configs:
    r = backtest(sl, rr, trend_filter=True)
    log(f"  {name:<23s} {r['ret']:>+8.2f}% {r['win%']:>5.1f} {r['trades']:>5d} {r['dd']:>5.1f}%")

# Walk-forward on best trend-filtered config
best_sl, best_rr = 1.2, 1.5
log("")
log("─" * 60)
log(f"WALK-FORWARD (4 quarters, SL={best_sl} RR={best_rr} + 200MA filter):")
qf = n // 4
r_full = backtest(best_sl, best_rr, trend_filter=True)
logs_q = []
for q in range(4):
    s, e = q*qf, (q+1)*qf if q<3 else n
    r = backtest(best_sl, best_rr, trend_filter=True, start=s, end=e)
    logs_q.append(r)
    log(f"  Q{q+1}: Return={r['ret']:>+8.2f}% Trades={r['trades']:>4d} Win%={r['win%']:>5.1f} DD={r['dd']:>5.1f}%")
neg = sum(1 for r in logs_q if r["ret"] < 0)
avg_q = round(sum(r["ret"] for r in logs_q) / 4, 2)
log(f"  ──────────────────────────────────────")
log(f"  Full: +{r_full['ret']}% DD={r_full['dd']}% Trades={r_full['trades']}")
log(f"  Quarters: {neg}/4 negative | Avg quarterly: {avg_q:+.2f}%")
log(f"  Min equity: ${min(r_full['equity']):.2f} ({(1000-min(r_full['equity']))/1000*100:.1f}% max dd)")

log("")
log("=" * 70)
log("CONCLUSION:" if r_full["ret"] > 5 else "BELOW TARGET")
log(f"  Best config (SL={best_sl}×, RR={best_rr}× +200MA): {r_full['ret']}% on NAS100 1-min")
log(f"  Walk-forward: {4-neg}/4 positive quarters")
log(f"  Average quarterly: {avg_q:+.2f}%")
log(f"  {'>5% target MET ✓' if r_full['ret'] > 5 else '<5% target NOT MET'}")
log("=" * 70)

with open(RESULTS, "w") as f:
    f.write("\n".join(out))
log(f"\nResults saved to {RESULTS}")

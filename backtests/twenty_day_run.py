"""20-TRADING-DAY BACKTEST: $50 start, optimized configs.

Symbols: XAUUSD (SL=1.2×, RR=4.0×)
         SP500  (SL=1.2×, RR=1.5×)
         NAS100 (SL=1.2×, RR=1.5× + 200MA filter)

USOIL excluded — no edge found.

Timeline: last 20 trading days (Mon-Fri)
Starting balance: $50.00
Sizing: Fixed 1.0 units
"""
import csv, math
from pathlib import Path

SYMBOLS = {
    "XAUUSD": {"file": "data/market_cache/fmp_XAUUSD_1min.csv", "sl": 1.2, "rr": 4.0, "filter": False},
    "SP500":  {"file": "data/market_cache/fmp_SP500_1min.csv",  "sl": 1.2, "rr": 1.5, "filter": False},
    "NAS100": {"file": "data/market_cache/fmp_NAS100_1min.csv", "sl": 1.2, "rr": 1.5, "filter": True},
}

RESULTS = Path(__file__).resolve().parent / "twenty_day_results.txt"
START_BAL = 50.0
TRADING_DAYS = 20

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

def load_last_n_days(path, days):
    """Load the last N trading days of 1-min candles."""
    d_dates = {"o":[],"h":[],"l":[],"c":[],"date":[]}
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            d_dates["o"].append(float(r["open"]))
            d_dates["h"].append(float(r["high"]))
            d_dates["l"].append(float(r["low"]))
            d_dates["c"].append(float(r["close"]))
            d_dates["date"].append(r["date"])

    # Find unique trading days in order
    seen = set()
    uniq_days = []
    for dt in d_dates["date"]:
        d = dt[:10]
        if d not in seen:
            seen.add(d)
            uniq_days.append(d)

    # Keep only the last N trading days
    keep = set(uniq_days[-days:])
    d = {"o":[],"h":[],"l":[],"c":[],"date":[]}
    for i in range(len(d_dates["date"])):
        if d_dates["date"][i][:10] in keep:
            d["o"].append(d_dates["o"][i])
            d["h"].append(d_dates["h"][i])
            d["l"].append(d_dates["l"][i])
            d["c"].append(d_dates["c"][i])
            d["date"].append(d_dates["date"][i])

    return d, len(d["c"]), uniq_days[-days:]

def backtest(d, n, sl_atr, rr, trend_filter=False):
    tr_raw = [0.0]
    for i in range(1, n):
        tr_raw.append(max(d["h"][i]-d["l"][i],
                          abs(d["h"][i]-d["c"][i-1]),
                          abs(d["l"][i]-d["c"][i-1])))
    atr = sma(tr_raw, 14)

    ma200 = sma(d["c"], 200)  # always computed, only used when trend_filter=True

    bal = START_BAL
    trades = wins = 0
    pos = None
    peak = bal
    equity = [bal]
    si = max(200, 0)  # 200-bar warmup

    for i in range(si, n):
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

        # Trend filter: skip trades against the 200MA
        if trend_filter:
            ma_val = ma200[i]
            if math.isnan(ma_val):
                continue
        else:
            ma_val = 0.0  # unused

        sl_d = a * sl_atr
        tp_d = sl_d * rr
        if d["c"][i] > max(d["h"][i-1], d["h"][i-2]) and d["c"][i] > d["o"][i]:
            if trend_filter and d["c"][i] < ma_val: continue
            pos = {"side":"BUY","entry":d["c"][i],
                   "sl":d["c"][i]-sl_d,"tp":d["c"][i]+tp_d,"idx":i}
        elif d["c"][i] < min(d["l"][i-1], d["l"][i-2]) and d["c"][i] < d["o"][i]:
            if trend_filter and d["c"][i] > ma_val: continue
            pos = {"side":"SELL","entry":d["c"][i],
                   "sl":d["c"][i]+sl_d,"tp":d["c"][i]-tp_d,"idx":i}

    max_dd = 0
    cp = START_BAL
    for e in equity:
        if e > cp: cp = e
        dd = (cp-e)/cp*100
        if dd > max_dd: max_dd = dd
    wr = wins/trades*100 if trades else 0
    return {"ret":round((bal/START_BAL-1)*100,2),"trades":trades,
            "win%":round(wr,1),"dd":round(max_dd,1),
            "final":round(bal,2)}


log("=" * 72)
log("20-DAY BACKTEST — $50 start, optimized per-symbol")
log("=" * 72)
log("")

results = []
for sym, cfg in SYMBOLS.items():
    d, n, days_used = load_last_n_days(cfg["file"], TRADING_DAYS)
    tf_label = " + 200MA" if cfg["filter"] else ""
    log(f"── {sym} (SL={cfg['sl']}×, RR={cfg['rr']}×{tf_label}) ──")
    log(f"   Data: {n} candles ({days_used[0]} → {days_used[-1]}, {len(days_used)} days)")
    log(f"   Start: ${START_BAL:.2f}")

    r = backtest(d, n, cfg["sl"], cfg["rr"], trend_filter=cfg["filter"])
    results.append((sym, r))

    ret_pct = (r["final"] / START_BAL - 1) * 100
    log(f"   End:   ${r['final']:.2f} ({ret_pct:+.2f}%)")
    log(f"   Trades: {r['trades']} | Win%: {r['win%']}% | DD: {r['dd']}%")
    log("")

log("-" * 72)
log("FINAL ACROSS 3 SYMBOLS ($50 each, fixed 1.0 unit, 20 trading days):")
log("")
log(f"  {'Symbol':<10s} {'Config':<25s} {'Start':>7s} {'End':>9s} {'Return':>8s} {'Trades':>6s} {'DD':>5s}")
log(f"  {'-'*70}")
total_end = 0
for sym, r in results:
    ret_pct = (r["final"] / START_BAL - 1) * 100
    cfg = SYMBOLS[sym]
    label = f"SL={cfg['sl']}×,RR={cfg['rr']}×{' +200MA' if cfg['filter'] else ''}"
    log(f"  {sym:<10s} {label:<25s} ${START_BAL:>5.2f} ${r['final']:>7.2f} {ret_pct:>+7.2f}% {r['trades']:>5d} {r['dd']:>4.1f}%")
    total_end += r["final"]

log(f"  {'-'*70}")
log(f"  Combined: ${total_end:.2f} on ${START_BAL*3:.2f} invested "
    f"({(total_end/(START_BAL*3)-1)*100:+.2f}%)")
log("")
log("=" * 72)

with open(RESULTS, "w") as f:
    f.write("\n".join(out))
log(f"\nSaved to {RESULTS}")

"""Cost-aware validation for the XAUUSD momentum research strategy."""
import argparse
import csv
import math
from pathlib import Path

DATA = Path("data/market_cache/fmp_XAUUSD_1min.csv")


def load_prices(path):
    with path.open() as f:
        rows = list(csv.DictReader(f))
    return ([float(r["open"]) for r in rows], [float(r["high"]) for r in rows],
            [float(r["low"]) for r in rows], [float(r["close"]) for r in rows])


def sma(values, period):
    if len(values) < period:
        return [math.nan] * len(values)
    total = sum(values[:period])
    out = [math.nan] * (period - 1) + [total / period]
    for i in range(period, len(values)):
        total += values[i] - values[i - period]
        out.append(total / period)
    return out


def run(opens, highs, lows, closes, sl_atr=1.2, rr=4.0, spread=0.0,
        slippage=0.0, commission=0.0, start=200, end=None):
    end = len(closes) if end is None else min(end, len(closes))
    tr = [0.0]
    for i in range(1, len(closes)):
        tr.append(max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1])))
    atr = sma(tr, 14)
    balance = 1000.0
    position = None
    trades = wins = 0
    peak = balance
    max_dd = 0.0
    for i in range(max(start, 200), end):
        if position:
            exit_px = None
            if position["side"] == "BUY":
                if lows[i] <= position["sl"]: exit_px = position["sl"] - slippage
                elif highs[i] >= position["tp"]: exit_px = position["tp"] - slippage
            else:
                if highs[i] >= position["sl"]: exit_px = position["sl"] + slippage
                elif lows[i] <= position["tp"]: exit_px = position["tp"] + slippage
            if exit_px is None and i - position["idx"] >= 15:
                exit_px = closes[i] - slippage if position["side"] == "BUY" else closes[i] + slippage
            if exit_px is not None:
                pnl = (exit_px - position["entry"]) if position["side"] == "BUY" else (position["entry"] - exit_px)
                pnl -= commission
                balance += pnl
                trades += 1
                wins += pnl > 0
                position = None
        if position is None:
            a = atr[i]
            if not math.isfinite(a) or a <= 0:
                continue
            distance = a * sl_atr
            entry_cost = spread / 2 + slippage
            if closes[i] > max(highs[i-1], highs[i-2]) and closes[i] > opens[i]:
                entry = closes[i] + entry_cost
                position = {"side":"BUY", "entry":entry, "sl":entry-distance, "tp":entry+distance*rr, "idx":i}
            elif closes[i] < min(lows[i-1], lows[i-2]) and closes[i] < opens[i]:
                entry = closes[i] - entry_cost
                position = {"side":"SELL", "entry":entry, "sl":entry+distance, "tp":entry-distance*rr, "idx":i}
        peak = max(peak, balance)
        max_dd = max(max_dd, (peak - balance) / peak * 100)
    return {"return_pct": (balance / 1000 - 1) * 100, "trades": trades,
            "win_rate_pct": wins / trades * 100 if trades else 0.0, "max_dd_pct": max_dd}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--spread", type=float, default=0.0)
    p.add_argument("--slippage", type=float, default=0.0)
    p.add_argument("--commission", type=float, default=0.0)
    p.add_argument("--oos-start", type=float, default=0.75)
    args = p.parse_args()
    o, h, l, c = load_prices(DATA)
    split = int(len(c) * args.oos_start)
    print("Research-only cost-aware validation")
    for label, s, e in (("DEVELOPMENT", 200, split), ("OOS", split, len(c))):
        result = run(o, h, l, c, spread=args.spread, slippage=args.slippage,
                     commission=args.commission, start=s, end=e)
        print(label, result)


if __name__ == "__main__":
    main()

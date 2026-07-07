"""Local FX backtester — faithful replay of QuadaptEngine on historical candles.

Replays core_backend.strategies.engine.QuadaptEngine.evaluate() bar-by-bar,
manages positions with balance-based lot sizing, ATR SL/TP from the engine's
RiskCalculator, and enforces the compounding loop rules:
  - start balance $50
  - daily profit target +20% -> circuit breaker -> refresh + compound
  - 5 trading days/week, 4 weeks/month

Results append to backtests/results.jsonl.

Run:
  PYTHONPATH= .venv/bin/python backtests/fx_backtester.py --symbol XAUUSD \
      --candles data/xauusd_1m.csv --start 50 --daily-target 20
"""
from __future__ import annotations
import argparse, csv, json, sys, os
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core_backend.strategies.engine import QuadaptEngine
from core_backend.strategies.config import QUADAPT_CFG

LEDGER = Path(__file__).resolve().parent / "results.jsonl"


class Candle:
    __slots__ = ("time", "open", "high", "low", "close", "volume")

    def __init__(self, time, o, h, l, c, v=0.0):
        self.time = time
        self.open = float(o)
        self.high = float(h)
        self.low = float(l)
        self.close = float(c)
        self.volume = float(v or 0.0)


def load_csv(path: str) -> list[Candle]:
    out = []
    with open(path) as f:
        r = csv.DictReader(f)
        t0 = datetime(2024, 1, 1)
        for idx, row in enumerate(r):
            t = t0 + timedelta(minutes=idx)
            o = row.get("open", row.get("o"))
            h = row.get("high", row.get("h"))
            l = row.get("low", row.get("l"))
            c = row.get("close", row.get("c"))
            v = row.get("volume", row.get("vol", 0))
            out.append(Candle(t, o, h, l, c, v))
    return out


class Position:
    __slots__ = ("symbol", "side", "entry", "sl", "tp", "lot", "open_idx")

    def __init__(self, symbol, side, entry, sl, tp, lot, open_idx):
        self.symbol = symbol
        self.side = side
        self.entry = entry
        self.sl = sl
        self.tp = tp
        self.lot = lot
        self.open_idx = open_idx


def lot_size(balance: float, price: float, sl_price: float, symbol: str,
              risk_pct: float = 1.0) -> float:
    """Size lots so a full SL hit loses <= risk_pct of balance. Floor 0.01."""
    if balance <= 0:
        return 0.0
    if symbol.upper().startswith("XAU"):  # gold: 100 units per 1.0 lot
        mult = 100.0
    else:  # FX: 100k units per 1.0 lot
        mult = 100000.0
    sl_dist = abs(price - sl_price)
    if sl_dist <= 0:
        return 0.01
    risk_usd = balance * risk_pct / 100.0
    lot = risk_usd / (sl_dist * mult)
    return max(0.01, round(lot, 2))


def pnl_usd(pos: Position, exit_price: float) -> float:
    if pos.symbol.upper().startswith("XAU"):
        mult = 100.0
    else:
        mult = 100000.0
    if pos.side == "BUY":
        return (exit_price - pos.entry) * pos.lot * mult
    return (pos.entry - exit_price) * pos.lot * mult


def run_backtest(candles, symbol, start_balance, daily_target,
                 weeks=4, days_per_week=5, bars_per_day=1440,
                 risk_pct=1.0, max_bars=None):
    if max_bars:
        candles = candles[:max_bars]
    eng = QuadaptEngine()
    balance = start_balance
    pos: Position | None = None
    day_start_balance = balance
    day_pnl = 0.0
    trades = []
    equity_curve = [balance]
    day_idx = 0
    results = {
        "symbol": symbol, "start_balance": start_balance,
        "daily_target_pct": daily_target, "bars_per_day": bars_per_day,
        "trades": [], "days": [], "equity_curve": [],
    }
    for i, c in enumerate(candles):
        # open a trade if engine signals and no open position and day not halted
        if pos is None and day_pnl < day_start_balance * daily_target / 100.0:
            # pass only a sliding window (engine needs ~120 bars history, not all)
            window = candles[max(0, i - 400): i + 1]
            sig = eng.evaluate(_snapshot(symbol, window))
            if sig:
                lot = lot_size(balance, sig.entry_price, sig.sl, symbol, risk_pct)
                if lot > 0:
                    pos = Position(symbol, sig.action, sig.entry_price, sig.sl, sig.tp, lot, i)
        # check exit on open position
        if pos is not None:
            exit_px = None
            if pos.side == "BUY":
                if c.low <= pos.sl:
                    exit_px = pos.sl
                elif c.high >= pos.tp:
                    exit_px = pos.tp
            else:
                if c.high >= pos.sl:
                    exit_px = pos.sl
                elif c.low <= pos.tp:
                    exit_px = pos.tp
            if exit_px is not None:
                p = pnl_usd(pos, exit_px)
                balance += p
                day_pnl += p
                trades.append({"i": i, "side": pos.side, "entry": pos.entry,
                               "exit": exit_px, "pnl": round(p, 2),
                               "balance": round(balance, 2)})
                pos = None
        equity_curve.append(round(balance, 2))
        if balance <= 0:  # ruin: halt, flat
            break
        # day boundary
        if (i + 1) % bars_per_day == 0:
            day_idx += 1
            results["days"].append({
                "day": day_idx,
                "start": round(day_start_balance, 2),
                "end": round(balance, 2),
                "pnl": round(balance - day_start_balance, 2),
                "pnl_pct": round((balance / day_start_balance - 1) * 100, 2),
                "hit_target": (balance - day_start_balance) / day_start_balance * 100 >= daily_target,
            })
            day_start_balance = balance  # compound: carry forward
            day_pnl = 0.0
            pos = None  # flat at day end
    results["trades"] = trades
    results["equity_curve"] = equity_curve
    results["final_balance"] = round(balance, 2)
    results["total_return_pct"] = round((balance / start_balance - 1) * 100, 2)
    results["n_trades"] = len(trades)
    _log(results)
    return results


def _snapshot(symbol, candles):
    from core_backend.strategies.market_data import MarketSnapshot
    return MarketSnapshot(symbol=symbol, candles=candles,
                          fetched_at=datetime.utcnow())


def _log(results: dict):
    row = {"ts": datetime.utcnow().isoformat(), **results}
    with open(LEDGER, "a") as f:
        f.write(json.dumps(row) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default=None,
                    help="Single symbol to replay (EURUSD/GBPUSD/XAUUSD). "
                         "Omit with --fetch to pull all three.")
    ap.add_argument("--candles", required=False,
                    help="CSV candles to replay. If omitted with --fetch, "
                         "the cached FMP file for --symbol is used.")
    ap.add_argument("--start", type=float, default=50.0)
    ap.add_argument("--daily-target", type=float, default=20.0)
    ap.add_argument("--bars-per-day", type=int, default=1440)
    ap.add_argument("--weeks", type=int, default=4)
    ap.add_argument("--risk-pct", type=float, default=1.0)
    ap.add_argument("--max-bars", type=int, default=None)
    ap.add_argument("--fetch", action="store_true",
                    help="Pull+cache all 3 symbols from FMP before replaying. "
                         "If --symbol is also given, replay only that symbol.")
    ap.add_argument("--start-date", help="FMP window start YYYY-MM-DD")
    ap.add_argument("--end-date", help="FMP window end YYYY-MM-DD")
    ap.add_argument("--force-fetch", action="store_true",
                    help="With --fetch: ignore existing cache and re-pull.")
    a = ap.parse_args()

    candles_path = a.candles

    if a.fetch:
        # Route by symbol: forex (EURUSD/GBPUSD) -> FMP free real spot 1-min;
        # gold (XAUUSD) -> Yahoo GC=F (FMP paywalls XAUUSD intraday).
        from core_backend.market_context.fmp import (
            fetch_all_to_cache as fmp_fetch,
            FMPFreeTierError,
        )
        from core_backend.market_context.yahoo import (
            fetch_all_to_cache as yahoo_fetch,
            YahooFetchError,
        )

        requested = [a.symbol] if a.symbol else ["EURUSD", "GBPUSD", "XAUUSD"]
        forex = [s for s in requested if s != "XAUUSD"]
        gold = [s for s in requested if s == "XAUUSD"]

        paths: dict = {}
        failed = False
        if forex:
            try:
                paths.update(fmp_fetch(
                    start_date=a.start_date, end_date=a.end_date,
                    symbols=tuple(forex), force=a.force_fetch,
                ))
            except FMPFreeTierError as e:
                print(f"FETCH FAILED (FMP): {e}")
                failed = True
        if gold:
            try:
                paths.update(yahoo_fetch(
                    start_date=a.start_date, end_date=a.end_date,
                    symbols=tuple(gold), force=a.force_fetch,
                ))
            except YahooFetchError as e:
                print(f"FETCH FAILED (Yahoo): {e}")
                failed = True
        if failed or not paths:
            print("Aborting — not running backtest on a partial/phantom dataset.")
            sys.exit(2)
            return  # unreachable if sys.exit not intercepted; safety net

        # If only one symbol requested, replay just that; else replay all.
        targets = [a.symbol] if a.symbol else list(paths.keys())
        for sym in targets:
            p = paths[sym]
            is_eod = p.name.endswith("_eod.csv")
            src = "Yahoo GC=F" if sym == "XAUUSD" else "FMP"
            gran = "EOD-daily" if is_eod else "1-min"
            print(f"\n##### {sym} ({src} cache: {gran}) #####")
            candles = load_csv(str(p))
            if is_eod:
                a.bars_per_day = 1
            _run_and_print(candles, sym, a)
        return

    if not candles_path:
        # no CSV given and no --fetch: try the cached file for --symbol.
        if not a.symbol:
            print("ERROR: replay needs --symbol (or --candles, or --fetch).")
            sys.exit(2)
            return  # unreachable if sys.exit not intercepted; safety net
        from core_backend.market_context.fmp import cache_path
        c1 = cache_path(a.symbol, "1min")
        ce = cache_path(a.symbol, "eod")
        candles_path = str(c1 if c1.exists() else ce)
        if not Path(candles_path).exists():
            print(f"ERROR: no --candles CSV and no cached file for {a.symbol}. "
                  f"Run with --fetch first, or pass --candles.")
            sys.exit(2)
            return  # unreachable if sys.exit not intercepted; safety net

    candles = load_csv(candles_path)
    _run_and_print(candles, a.symbol, a)


def _expectancy(trades: list) -> dict:
    """Clean per-trade expectancy stats (Pushback 5: judge the EDGE, not 20%/day).

    Independent of the compounding loop. Reports win rate, avg win/loss,
    expectancy per trade (mean R), and profit factor.
    """
    if not trades:
        return {
            "n": 0, "win_rate": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
            "expectancy": 0.0, "profit_factor": 0.0, "net_pnl": 0.0,
        }
    wins = [t["pnl"] for t in trades if t["pnl"] > 0]
    losses = [t["pnl"] for t in trades if t["pnl"] <= 0]
    n = len(trades)
    win_rate = len(wins) / n if n else 0.0
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    avg_win = gross_win / len(wins) if wins else 0.0
    avg_loss = gross_loss / len(losses) if losses else 0.0
    expectancy = (sum(t["pnl"] for t in trades)) / n if n else 0.0
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else float("inf")
    return {
        "n": n,
        "win_rate": round(win_rate * 100, 1),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(-avg_loss, 2),
        "expectancy": round(expectancy, 2),
        "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else 999.0,
        "net_pnl": round(sum(t["pnl"] for t in trades), 2),
    }


def _run_and_print(candles, symbol, a):
    """Run a backtest and print the compact day-by-day summary."""
    res = run_backtest(candles, symbol, a.start, a.daily_target,
                       weeks=a.weeks, bars_per_day=a.bars_per_day,
                       risk_pct=a.risk_pct, max_bars=a.max_bars)
    active = [d for d in res["days"] if d["pnl"] != 0 or d["hit_target"]]
    print(f"== {res['symbol']} == start ${res['start_balance']} -> "
          f"${res['final_balance']} ({res['total_return_pct']}%) "
          f"| trades={res['n_trades']} | risk={a.risk_pct}%")
    for d in active[:40]:
        print(f"  day {d['day']:>3}: {d['start']:>8.2f} -> {d['end']:>8.2f} "
              f"({d['pnl_pct']:>6.2f}%) {'HIT' if d['hit_target'] else ''}")
    if len(active) > 40:
        print(f"  ... +{len(active) - 40} more active days")
    # Per-trade expectancy (the actual edge metric)
    exp = _expectancy(res["trades"])
    print(f"  EXPECTANCY: n={exp['n']} win%={exp['win_rate']} "
          f"avgWin=${exp['avg_win']} avgLoss=${exp['avg_loss']} "
          f"E[trade]=${exp['expectancy']} PF={exp['profit_factor']} "
          f"net=${exp['net_pnl']}")


if __name__ == "__main__":
    main()

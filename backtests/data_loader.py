"""Free historical data loaders for backtests.

Two sources, both feeding the shared ``Bars`` container used by
``engine_corrected.run_momentum_backtest``:

  (A) load_yfinance()  — Yahoo Finance via yfinance. NO API key. Daily is
      unlimited; hourly reaches ~2y; 15m ~60d; 1m is a 7-day rolling window
      (Yahoo's intraday cap). Best choice for keyless backtests.

  (B) load_twelvedata() — Twelve Data REST API. Needs a FREE api key
      (https://twelvedata.com, 800 req/day on the free tier) exported as
      TWELVEDATA_API_KEY. Gives true 1-minute history for FOREX/CRYPTO and
      many equities. CAVEATS on the free tier (verified 2026-08):
        * Cash indices SPX/NDX are NOT covered -> use ETF proxies SPY (S&P
          500) / QQQ (Nasdaq-100) via TWELVE_TICKERS.
        * 1-min outputsize is capped low and the tier is rate-limited
          (HTTP 429 per-minute; 400 on oversized requests). Keep outputsize
          modest and rely on the CSV cache between runs.
        * Net: fine for spot-checking XAUUSD 1-min, not for deep index
          backtests. yfinance (A) is the keyless default for real history.

Symbol mapping (Yahoo futures proxies / Twelve Data tickers):
  XAUUSD -> GC=F  (yfinance) | XAU/USD (twelvedata)
  SP500  -> ES=F  (yfinance) | SPX  (twelvedata, cash index)
  NAS100 -> NQ=F  (yfinance) | NDX  (twelvedata, cash index)

Run:  python backtests/data_loader.py --source yfinance --interval 1h --period 2y
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import pandas as pd

# Local import (this file lives in backtests/, engine is a sibling).
from engine_corrected import Bars

CACHE_DIR = Path("data/market_cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Our internal symbol -> provider tickers.
# NOTE: Twelve Data's FREE tier covers forex/crypto + many equities but NOT
# the cash indices SPX/NDX (those 404). Use the ETF proxies SPY (S&P 500)
# and QQQ (Nasdaq-100) instead. yfinance uses the futures contracts.
YAHOO_TICKERS = {"XAUUSD": "GC=F", "SP500": "ES=F", "NAS100": "NQ=F"}
TWELVE_TICKERS = {"XAUUSD": "XAU/USD", "SP500": "SPY", "NAS100": "QQQ"}

INTERNAL_SYMBOLS = ["XAUUSD", "SP500", "NAS100"]


def _df_to_bars(df: pd.DataFrame, date_col: str = "Date") -> Bars:
    """Convert a tidy OHLCV DataFrame (Date index or column) into Bars."""
    bars = Bars()
    # yfinance returns a MultiIndex column like ('Close', 'GC=F'); flatten.
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        df.columns = [c[0] for c in df.columns]
    if date_col in df.columns:
        df = df.set_index(date_col)
    elif df.index.name is None and len(df.columns) >= 5:
        # Cache written via reset_index(): the date landed in column 0.
        df = df.set_index(df.columns[0])
    for idx, row in df.iterrows():
        try:
            bars.o.append(float(row["Open"]))
            bars.h.append(float(row["High"]))
            bars.l.append(float(row["Low"]))
            bars.c.append(float(row["Close"]))
        except (KeyError, ValueError, TypeError):
            continue
        bars.date.append(str(idx))
    return bars


# ──────────────────────────────────────────────────────────────
# (A) yfinance — no key
# ──────────────────────────────────────────────────────────────
def load_yfinance(
    symbol: str,
    interval: str = "1h",
    period: str = "2y",
    cache: bool = True,
) -> Bars:
    """Load OHLC bars from Yahoo Finance (no API key).

    interval: '1d' | '1h' | '15m' | '5m' | '1m'  (1m is 7-day capped)
    period:   '2y' | '1y' | '6mo' | '60d' | '7d'  (passed straight to yf)
    """
    import yfinance as yf

    if symbol not in YAHOO_TICKERS:
        raise ValueError(f"Unknown symbol {symbol!r}; expected one of {INTERNAL_SYMBOLS}")
    ticker = YAHOO_TICKERS[symbol]

    cache_file = CACHE_DIR / f"yf_{symbol}_{interval}_{period}.csv"
    if cache and cache_file.exists():
        df = pd.read_csv(cache_file)
        return _df_to_bars(df)

    df = yf.download(
        ticker, period=period, interval=interval,
        progress=False, auto_adjust=True,
    )
    if df is None or df.empty:
        raise RuntimeError(f"yfinance returned no data for {ticker}")
    if cache:
        # Cache as CSV (no pyarrow/fastparquet dependency needed).
        out = df.copy()
        if isinstance(out.columns, pd.MultiIndex):
            out.columns = [c[0] for c in out.columns]
        out.reset_index().to_csv(cache_file, index=False)
    return _df_to_bars(df)


# ──────────────────────────────────────────────────────────────
# (B) Twelve Data — free key (env TWELVEDATA_API_KEY)
# ──────────────────────────────────────────────────────────────
def load_twelvedata(
    symbol: str,
    interval: str = "1min",
    outputsize: int = 2000,
    cache_ttl_sec: int = 86_400,
) -> Bars:
    """Load OHLC bars from Twelve Data (FREE key via TWELVEDATA_API_KEY).

    Returns true 1-minute (or other interval) history for forex/crypto and
    many equities (NOT cash indices — use SPY/QQQ proxies). The free tier
    caps outputsize and is rate-limited; keep outputsize modest (<=2000 for
    1min) and rely on the on-disk cache between runs. Caches to CSV with a
    TTL so re-runs don't burn the 800 req/day (and per-minute) quota.
    """
    import urllib.request
    import urllib.error

    if symbol not in TWELVE_TICKERS:
        raise ValueError(f"Unknown symbol {symbol!r}; expected one of {INTERNAL_SYMBOLS}")
    ticker = TWELVE_TICKERS[symbol]
    api_key = os.getenv("TWELVEDATA_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "TWELVEDATA_API_KEY not set. Get a free key at https://twelvedata.com "
            "and export it (e.g. `export TWELVEDATA_API_KEY=xxx`)."
        )

    cache_file = CACHE_DIR / f"td_{symbol}_{interval}_{outputsize}.csv"
    if cache_file.exists() and (time.time() - cache_file.stat().st_mtime) < cache_ttl_sec:
        return _csv_to_bars(cache_file)

    url = (
        "https://api.twelvedata.com/time_series"
        f"?symbol={ticker}&interval={interval}&outputsize={outputsize}"
        f"&format=JSON&apikey={api_key}"
    )
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise RuntimeError(
                "Twelve Data rate limit (429). Free tier is throttled per-minute "
                "and 800 req/day. Wait a minute and re-run; cached data is used "
                "when fresh."
            )
        raise

    if "values" not in payload:
        raise RuntimeError(f"Twelve Data error: {payload.get('status', payload)}")
    # values are newest-first; reverse to oldest-first for the engine.
    rows = list(reversed(payload["values"]))
    out_file = CACHE_DIR / f"td_{symbol}_{interval}_{outputsize}.csv"
    with open(out_file, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Date", "Open", "High", "Low", "Close"])
        for r in rows:
            w.writerow([r["datetime"], r["open"], r["high"], r["low"], r["close"]])
    return _csv_to_bars(out_file)


def _csv_to_bars(path: Path) -> Bars:
    return _df_to_bars(pd.read_csv(path), date_col="Date")


def load(
    symbol: str,
    source: str = "yfinance",
    interval: str = "1h",
    period: str = "2y",
    outputsize: int = 5000,
) -> Bars:
    """Dispatch to the requested source and return a Bars object."""
    if source == "yfinance":
        return load_yfinance(symbol, interval=interval, period=period)
    if source == "twelvedata":
        # map yfinance-style interval to Twelve Data format
        td_interval = interval.replace("1m", "1min").replace("15m", "15min").replace("5m", "5min").replace("1h", "1h").replace("1d", "1day")
        return load_twelvedata(symbol, interval=td_interval, outputsize=outputsize)
    raise ValueError(f"Unknown source {source!r}; expected 'yfinance' or 'twelvedata'")


def main() -> None:
    ap = argparse.ArgumentParser(description="Fetch free historical data for backtests")
    ap.add_argument("--source", choices=["yfinance", "twelvedata"], default="yfinance")
    ap.add_argument("--symbol", choices=INTERNAL_SYMBOLS, default="XAUUSD")
    ap.add_argument("--interval", default="1h", help="yfinance: 1d|1h|15m|5m|1m; twelvedata: 1min|15min|1h|1day")
    ap.add_argument("--period", default="2y", help="yfinance only: 2y|1y|6mo|60d|7d")
    ap.add_argument("--outputsize", type=int, default=5000, help="twelvedata only: bars to fetch")
    args = ap.parse_args()

    bars = load(
        args.symbol, source=args.source,
        interval=args.interval, period=args.period, outputsize=args.outputsize,
    )
    print(f"{args.symbol} ({args.source}) -> {len(bars.c)} bars "
          f"[{bars.date[0]} .. {bars.date[-1]}]")


if __name__ == "__main__":
    main()

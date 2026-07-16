"""Fetch ~20 sessions of 1-min OHLC per symbol via Yahoo pagination.

Yahoo caps 1m history at 8 days/request, so we walk backward in 6-day
chunks and stitch. Saves to data/market_cache/fmp_<SYM>_1min.csv
(overwriting the 7-session files) for the backtests to consume.

Run: env -u PYTHONPATH .venv/bin/python backtests/fetch_20d.py
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import yfinance as yf
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from engine_corrected import CACHE_DIR  # noqa: E402

SYMS = {"XAUUSD": "GC=F", "SP500": "ES=F", "NAS100": "NQ=F"}
CHUNK_DAYS = 6
WINDOW_DAYS = 26  # ~20 trading sessions


def fetch_symbol(sym: str, ticker: str) -> pd.DataFrame:
    end = datetime.now()
    start = end - timedelta(days=WINDOW_DAYS)
    frames = []
    cur_end = end
    while cur_end > start:
        cur_start = cur_end - timedelta(days=CHUNK_DAYS)
        if cur_start < start:
            cur_start = start
        df = None
        for attempt in range(4):  # retry transient Yahoo rate-limits
            try:
                df = yf.download(
                    ticker, start=cur_start.strftime("%Y-%m-%d"),
                    end=cur_end.strftime("%Y-%m-%d"), interval="1m",
                    progress=False, auto_adjust=True,
                )
                if df is not None and len(df):
                    break
            except Exception as e:  # noqa: BLE001
                print(f"  {sym}: attempt {attempt+1} err {cur_start.date()}->"
                      f"{cur_end.date()}: {e}", flush=True)
            time.sleep(1.5 * (attempt + 1))
        if df is not None and len(df):
            frames.append(df)
            print(f"  {sym}: {cur_start.date()}->{cur_end.date()} -> {len(df)} rows", flush=True)
        else:
            print(f"  {sym}: {cur_start.date()}->{cur_end.date()} -> empty after retries", flush=True)
        cur_end = cur_start
        time.sleep(1.0)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames)
    out = out[~out.index.duplicated(keep="first")]
    out = out.sort_index()
    return out


def main():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    for sym, ticker in SYMS.items():
        print(f"=== {sym} ({ticker}) ===", flush=True)
        df = fetch_symbol(sym, ticker)
        if df is None or len(df) == 0:
            print(f"  {sym}: NO DATA — skipping", flush=True)
            continue
        # Flatten multi-index columns if present
        if isinstance(df.columns, pd.MultiIndex):
            df = df.droplevel(1, axis=1)
        df = df.rename(columns=str).loc[:, ["Open", "High", "Low", "Close"]]
        df = df.dropna()
        path = CACHE_DIR / f"fmp_{sym}_1min.csv"
        df.to_csv(path)
        print(f"  {sym}: SAVED {len(df)} rows -> {path}", flush=True)


if __name__ == "__main__":
    main()

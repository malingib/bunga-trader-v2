"""Yahoo Finance (yfinance) historical bar fetcher — gold proxy.

FMP's free tier paywalls XAUUSD (spot gold) intraday (HTTP 402). Yahoo
Finance serves 1-min bars for gold *futures* (GC=F) with no API key and no
rate-limit token, so this module closes the XAUUSD gap for the compounding
backtest at zero cost.

Caveats (kept honest in logs, never silent):
  * GC=F is a FUTURES contract, not spot XAUUSD — price differs slightly.
  * Yahoo caps 1-min history to ~7-30 days, so gold replays shorter windows
    than the FMP forex legs. We window to the most recent `period` available.
  * No auth needed.

Interface mirrors core_backend.market_context.fmp so the backtester can treat
both sources through one path: fetch_historical_1min(), fetch_all_to_cache(),
cache_path(), load_cached_csv(), Candle, and the same CSV cache format
(columns: date, open, high, low, close, volume) consumed by
backtests/fx_backtester.py::load_csv.

Symbol mapping: the backtester asks for "XAUUSD"; we fetch "GC=F" and cache
it as fmp_XAUUSD_1min.csv so the rest of the pipeline is source-agnostic.
"""

from __future__ import annotations

import csv
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional

# Lazy logger (same pattern as fmp.py): importing ..logger forces the full app
# config load (sys.exit on missing Telegram/LLM keys), which breaks standalone
# use. Catch BaseException because config.load_config() calls sys.exit(1).
try:  # pragma: no cover - exercised when imported inside the app
    from ..logger import setup_logger

    logger = setup_logger("YAHOO")
except BaseException:  # pragma: no cover - standalone / missing app config
    import logging

    logger = logging.getLogger("YAHOO")
    if not logger.handlers:
        logging.basicConfig(level=logging.INFO)

# Backtester symbol -> Yahoo ticker. Forex is covered by FMP (real spot, free);
# this module exists for gold only.
YAHOO_TICKERS = {"XAUUSD": "GC=F"}

CACHE_DIR = Path("data/market_cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Yahoo rate-limits dynamically per-IP; a small throttle avoids bursts.
_THROTTLE_SECONDS = 0.5
_last_call = 0.0


class YahooFetchError(RuntimeError):
    """Raised when yfinance returns no usable data (empty / blocked)."""


@dataclass
class Candle:
    """One OHLCV bar."""

    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


def _throttle() -> None:
    global _last_call
    elapsed = time.time() - _last_call
    if elapsed < _THROTTLE_SECONDS:
        time.sleep(_THROTTLE_SECONDS - elapsed)
    _last_call = time.time()


def _cache_path(symbol: str, interval: str = "1min") -> Path:
    # Same filename scheme as fmp.py so the backtester is source-agnostic.
    return CACHE_DIR / f"fmp_{symbol}_{interval}.csv"


def _parse_df(df) -> List[Candle]:
    """Convert a yfinance DataFrame (tz-aware index, multi-col) to Candle list."""
    candles: List[Candle] = []
    if df is None or len(df) == 0:
        return candles
    # yfinance 1.5+ returns a MultiIndex column frame; flatten to single level.
    if hasattr(df.columns, "levels"):
        flat = df.copy()
        flat.columns = [c[0] for c in df.columns]
    else:
        flat = df
    for ts, row in flat.iterrows():
        try:
            dt = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else datetime.fromisoformat(str(ts))
            if dt.tzinfo is not None:
                dt = dt.replace(tzinfo=None)
            candles.append(
                Candle(
                    time=dt,
                    open=float(row["Open"]),
                    high=float(row["High"]),
                    low=float(row["Low"]),
                    close=float(row["Close"]),
                    volume=float(row.get("Volume", 0) or 0),
                )
            )
        except (KeyError, ValueError, TypeError) as e:
            logger.warning("Skipping malformed Yahoo candle at %s: %s", ts, e)
            continue
    candles.sort(key=lambda c: c.time)
    return candles


def _filter_range(
    candles: List[Candle],
    start: Optional[date],
    end: Optional[date],
) -> List[Candle]:
    if start is None and end is None:
        return candles
    out: List[Candle] = []
    for c in candles:
        d = c.time.date()
        if start and d < start:
            continue
        if end and d > end:
            continue
        out.append(c)
    return out


def _write_csv(symbol: str, candles: List[Candle], interval: str = "1min") -> Path:
    path = _cache_path(symbol, interval)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "open", "high", "low", "close", "volume"])
        for c in candles:
            w.writerow(
                [
                    c.time.strftime("%Y-%m-%d %H:%M:%S"),
                    c.open,
                    c.high,
                    c.low,
                    c.close,
                    c.volume,
                ]
            )
    logger.info("Cached %d %s bars -> %s", len(candles), symbol, path)
    return path


def load_cached_csv(symbol: str, interval: str = "1min") -> Optional[List[Candle]]:
    """Load previously cached candles for a symbol, or None if absent."""
    path = _cache_path(symbol, interval)
    if not path.exists():
        return None
    candles: List[Candle] = []
    with open(path) as f:
        for row in csv.DictReader(f):
            try:
                candles.append(
                    Candle(
                        time=datetime.strptime(row["date"], "%Y-%m-%d %H:%M:%S"),
                        open=float(row["open"]),
                        high=float(row["high"]),
                        low=float(row["low"]),
                        close=float(row["close"]),
                        volume=float(row.get("volume", 0) or 0),
                    )
                )
            except (KeyError, ValueError, TypeError):
                continue
    return candles or None


def fetch_historical_1min(
    symbol: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    interval: str = "1min",
    force: bool = False,
) -> List[Candle]:
    """Fetch 1-min bars for a symbol via Yahoo and cache to CSV.

    Only gold (XAUUSD -> GC=F) is supported here; forex comes from FMP.

    Raises:
        YahooFetchError: if the symbol isn't mapped or Yahoo returns nothing.
    """
    ticker = YAHOO_TICKERS.get(symbol.upper())
    if ticker is None:
        raise YahooFetchError(
            f"Yahoo source only covers gold (XAUUSD -> GC=F); got {symbol!r}. "
            "Use FMP for forex."
        )

    if not force:
        cached = load_cached_csv(symbol, interval)
        if cached is not None:
            logger.info("Using cached %s bars (%d) for %s", interval, len(cached), symbol)
            return _filter_range(cached, _as_date(start_date), _as_date(end_date))

    try:
        import yfinance as yf
    except ImportError as e:
        raise YahooFetchError("yfinance not installed (pip install yfinance)") from e

    # Yahoo 1-min history is capped (~7-30 days). Use an explicit window when
    # both bounds are given; otherwise default to the last 7 days (reliable).
    _throttle()
    logger.info("Fetching %s (Yahoo %s) 1-min bars", symbol, ticker)
    try:
        if start_date and end_date:
            df = yf.download(
                ticker, start=start_date, end=end_date,
                interval="1m", progress=False, auto_adjust=False,
            )
        else:
            df = yf.download(
                ticker, period="7d", interval="1m",
                progress=False, auto_adjust=False,
            )
    except Exception as e:  # yfinance raises various network errors
        raise YahooFetchError(f"Yahoo request failed for {ticker}: {e}") from e

    candles = _parse_df(df)
    if not candles:
        raise YahooFetchError(
            f"Yahoo returned 0 usable 1-min bars for {ticker} "
            f"(symbol {symbol}). Gold is GC=F futures, not spot XAUUSD."
        )
    _write_csv(symbol, candles, interval)
    return _filter_range(candles, _as_date(start_date), _as_date(end_date))


def _as_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    return datetime.strptime(s, "%Y-%m-%d").date()


def cache_path(symbol: str, interval: str = "1min") -> Path:
    """Public accessor mirroring fmp.cache_path (used by the backtester)."""
    return _cache_path(symbol, interval)


def fetch_all_to_cache(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    symbols=("XAUUSD",),
    force: bool = False,
) -> dict[str, Path]:
    """Fetch + cache gold bars, returning {symbol: csv_path}.

    Mirrors fmp.fetch_all_to_cache so the backtester can call either uniformly.
    Raises YahooFetchError if any requested symbol fails (no partial/phantom
    dataset).
    """
    out: dict[str, Path] = {}
    for sym in symbols:
        fetch_historical_1min(sym, start_date, end_date, "1min", force=force)
        out[sym] = _cache_path(sym, "1min")
    return out

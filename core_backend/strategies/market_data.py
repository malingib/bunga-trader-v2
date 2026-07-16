"""Live market data provider — yfinance (no API key, runs everywhere).

Symbol mappings (Yahoo futures proxies):
  XAUUSD → GC=F  (gold futures)
  SP500  → ES=F  (S&P 500 E-mini futures)
  NAS100 → NQ=F  (Nasdaq 100 E-mini futures)

Caches to disk so polling every 60s doesn't hammer Yahoo.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import yfinance as yf

from .config import QUADAPT_CFG
from ..logger import setup_logger

logger = setup_logger("MarketData")

# ──────────────────────────────────────────────
# Candle model
# ──────────────────────────────────────────────


@dataclass
class Candle:
    """OHLCV candle."""

    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class MarketSnapshot:
    """Latest market data snapshot for one symbol."""

    symbol: str
    candles: List[Candle]
    fetched_at: datetime

    @property
    def latest(self) -> Optional[Candle]:
        return self.candles[-1] if self.candles else None

    @property
    def closes(self) -> List[float]:
        return [c.close for c in self.candles]

    @property
    def highs(self) -> List[float]:
        return [c.high for c in self.candles]

    @property
    def lows(self) -> List[float]:
        return [c.low for c in self.candles]

    @property
    def opens(self) -> List[float]:
        return [c.open for c in self.candles]


# ──────────────────────────────────────────────
# Symbol mapping
# ──────────────────────────────────────────────

YAHOO_TICKERS = {
    "XAUUSD": "GC=F",
    "SP500": "ES=F",
    "NAS100": "NQ=F",
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
}

# Cache directory
CACHE_DIR = Path("data/market_cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Rate limit: yfinance has no hard limit, but be polite
_last_fetch: Dict[str, float] = {}
_MIN_INTERVAL = 10.0  # seconds between fetches per symbol


def _rate_limit(key: str):
    elapsed = time.time() - _last_fetch.get(key, 0.0)
    if elapsed < _MIN_INTERVAL:
        time.sleep(_MIN_INTERVAL - elapsed)
    _last_fetch[key] = time.time()


# ──────────────────────────────────────────────
# Fetch + cache helper
# ──────────────────────────────────────────────


def _fetch_ohlcv(symbol: str, interval: str, count: int) -> Optional[List[Candle]]:
    """Try cache first, then yfinance. Returns None on failure."""
    cache_file = CACHE_DIR / f"yahoo_{symbol}_{interval}.parquet"
    cache_age = (
        time.time() - cache_file.stat().st_mtime if cache_file.exists() else 9999
    )

    # Cache valid for 120 seconds (longer than poll interval to avoid thrash)
    if cache_file.exists() and cache_age < 120:
        try:
            df = pd.read_parquet(cache_file)
            candles = _df_to_candles(df)
            if candles is not None:
                logger.debug(f"Cache HIT {symbol} ({len(candles)} candles)")
                return candles
        except Exception:
            cache_file.unlink(missing_ok=True)

    # Fetch from yfinance
    try:
        ticker = YAHOO_TICKERS.get(symbol, symbol)
        _rate_limit(symbol)

        # yfinance uses "1m", not "1min"
        yf_interval = interval.replace("1min", "1m")

        # Determine period: request enough data for warmup + recent
        # yfinance 1-min data max is 7 days (10080 bars)
        df = yf.download(
            tickers=ticker,
            period="7d",
            interval=yf_interval,
            progress=False,
            auto_adjust=True,
        )
    except Exception as e:
        logger.error(f"yfinance fetch failed for {symbol}: {e}")
        # Fall back to cache
        if cache_file.exists():
            df = pd.read_parquet(cache_file)
            return _df_to_candles(df)
        return None

    if df is None or df.empty:
        logger.warning(f"Empty response from yfinance for {symbol}")
        if cache_file.exists():
            df = pd.read_parquet(cache_file)
            return _df_to_candles(df)
        return None

    candles = _yf_df_to_candles(df)
    if not candles:
        return None

    # Trim to requested count
    candles = candles[-count:]

    # Write cache
    try:
        _candles_to_parquet(candles, cache_file)
    except Exception as e:
        logger.debug(f"Cache write failed: {e}")

    return candles


def _yf_df_to_candles(df: pd.DataFrame) -> List[Candle]:
    """Convert a yfinance DataFrame (single ticker) to Candle list."""
    # yfinance returns MultiIndex columns when ticker has a label
    # e.g. ('Open', 'GC=F'). Flatten to simple names.
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    candles: List[Candle] = []
    for idx, row in df.iterrows():
        try:
            ts = idx.to_pydatetime() if hasattr(idx, "to_pydatetime") else idx
            if isinstance(ts, pd.Timestamp):
                ts = ts.to_pydatetime()
            candles.append(
                Candle(
                    time=ts,
                    open=float(row["Open"]),
                    high=float(row["High"]),
                    low=float(row["Low"]),
                    close=float(row["Close"]),
                    volume=int(row["Volume"]) if "Volume" in row else 0,
                )
            )
        except (KeyError, ValueError, TypeError) as e:
            logger.debug(f"Skipping bad yfinance row: {e}")
            continue
    return candles


def _df_to_candles(df: pd.DataFrame) -> Optional[List[Candle]]:
    """Read cached parquet DataFrame to Candle list."""
    candles: List[Candle] = []
    for _, row in df.iterrows():
        try:
            candles.append(
                Candle(
                    time=row["time"],
                    open=row["open"],
                    high=row["high"],
                    low=row["low"],
                    close=row["close"],
                    volume=row.get("volume", 0),
                )
            )
        except (KeyError, ValueError, TypeError):
            continue
    return candles if candles else None


def _candles_to_parquet(candles: List[Candle], path: Path):
    """Write Candle list to parquet cache."""
    df = pd.DataFrame(
        {
            "time": [c.time for c in candles],
            "open": [c.open for c in candles],
            "high": [c.high for c in candles],
            "low": [c.low for c in candles],
            "close": [c.close for c in candles],
            "volume": [c.volume for c in candles],
        }
    )
    df.to_parquet(path)


# ──────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────


def fetch_market_data(
    symbol: str,
    interval: str = "1m",
    count: int = 2000,
) -> MarketSnapshot:
    """Fetch latest OHLCV data for a symbol via yfinance.

    Uses disk cache to stay polite to Yahoo. Returns stale cache
    on network failure rather than blowing up.
    """
    symbol = symbol.upper()

    candles = _fetch_ohlcv(symbol, interval, count)
    if not candles:
        logger.error(f"No market data available for {symbol}")
        return MarketSnapshot(
            symbol=symbol,
            candles=[],
            fetched_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )

    logger.info(
        f"Fetched {len(candles)} candles for {symbol} "
        f"(latest: {candles[-1].close:.2f} @ {candles[-1].time})"
    )
    return MarketSnapshot(
        symbol=symbol,
        candles=candles,
        fetched_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )


def fetch_all() -> Dict[str, MarketSnapshot]:
    """Fetch data for all configured symbols."""
    results: Dict[str, MarketSnapshot] = {}
    for sym in QUADAPT_CFG.market_data.symbols:
        logger.info(f"Fetching market data for {sym}...")
        results[sym] = fetch_market_data(sym)
    return results

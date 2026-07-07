"""Free market data provider for XAUUSD, EURUSD, GBPUSD.

Uses Alpha Vantage free tier (5 calls/min, 500/day).
Can swap to Twelve Data or Finnhub with config change.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import httpx

from .config import QUADAPT_CFG
from ..logger import setup_logger

logger = setup_logger("MarketData")

# ──────────────────────────────────────────────
# Candle model (matches what strategy engine needs)
# ──────────────────────────────────────────────


@dataclass
class Candle:
    """OHLCV candle."""

    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float  # tick volume (Alpha Vantage doesn't give real volume for FX)


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


# ──────────────────────────────────────────────
# Alpha Vantage — Forex FX_INTRADAY
# ──────────────────────────────────────────────

ALPHA_VANTAGE_URL = "https://www.alphavantage.co/query"

# Map our symbols to AV format (they use _ separator)
SYMBOL_MAP = {
    "XAUUSD": "XAUUSD",
    "EURUSD": "EURUSD",
    "GBPUSD": "GBPUSD",
}

# Cache directory for rate-limit compliance
CACHE_DIR = Path("data/market_cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Rate limit: 5 calls/min for free tier
_last_call: Dict[str, float] = {}


def _rate_limit(key: str = "av"):
    """Ensure minimum 12s between calls to AV free tier."""
    last = _last_call.get(key, 0.0)
    elapsed = time.time() - last
    if elapsed < 12.0:
        sleep = 12.0 - elapsed
        logger.debug(f"Rate limit: sleeping {sleep:.1f}s")
        time.sleep(sleep)
    _last_call[key] = time.time()


def _cached_or_fetch(symbol: str, interval: str = "5min") -> Optional[dict]:
    """Try disk cache first, then fetch from Alpha Vantage."""
    cache_file = CACHE_DIR / f"{symbol}_{interval}.json"

    # Cache valid for 4 minutes (matches 5min interval)
    if cache_file.exists():
        age = time.time() - cache_file.stat().st_mtime
        if age < 240:  # 4 minutes
            with open(cache_file) as f:
                return json.load(f)

    api_key = os.getenv("ALPHA_VANTAGE_API_KEY", "")
    if not api_key:
        logger.warning("ALPHA_VANTAGE_API_KEY not set, using cached data if available")
        if cache_file.exists():
            with open(cache_file) as f:
                return json.load(f)
        return None

    av_symbol = SYMBOL_MAP.get(symbol, symbol)
    params = {
        "function": "FX_INTRADAY",
        "from_symbol": av_symbol[:3] if av_symbol != "XAUUSD" else "XAU",
        "to_symbol": av_symbol[3:] if av_symbol != "XAUUSD" else "USD",
        "interval": interval,
        "apikey": api_key,
        "outputsize": "full",
        "datatype": "json",
    }

    _rate_limit()
    try:
        resp = httpx.get(ALPHA_VANTAGE_URL, params=params, timeout=15.0)
        resp.raise_for_status()
        data = resp.json()

        if "Time Series FX" not in data and "Meta Data" not in data:
            logger.error(f"Alpha Vantage error for {symbol}: {data}")
            # Fall back to cache
            if cache_file.exists():
                with open(cache_file) as f:
                    return json.load(f)
            return None

        # Cache it
        with open(cache_file, "w") as f:
            json.dump(data, f)
        return data

    except Exception as e:
        logger.error(f"Failed to fetch {symbol}: {e}")
        if cache_file.exists():
            with open(cache_file) as f:
                return json.load(f)
        return None


def _parse_av_candles(data: dict, interval: str = "5min") -> List[Candle]:
    """Parse Alpha Vantage FX_INTRADAY JSON into Candle list."""
    series_key = f"Time Series FX ({interval})"
    series = data.get(series_key, {})
    if not series:
        return []

    candles: List[Candle] = []
    for ts_str, ohlcv in sorted(series.items()):
        try:
            ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
            candle = Candle(
                time=ts,
                open=float(ohlcv["1. open"]),
                high=float(ohlcv["2. high"]),
                low=float(ohlcv["3. low"]),
                close=float(ohlcv["4. close"]),
                volume=0.0,  # AV doesn't give FX volume
            )
            candles.append(candle)
        except (KeyError, ValueError, TypeError) as e:
            logger.warning(f"Skipping bad candle at {ts_str}: {e}")
            continue

    return candles


def _fallback_synthetic_data(symbol: str, count: int = 200) -> List[Candle]:
    """Generate synthetic data when no API key or network available.

    This is for development/testing only. In production, configure
    ALPHA_VANTAGE_API_KEY in .env.
    """
    logger.warning(f"Generating synthetic data for {symbol} — DEV MODE")
    now = datetime.utcnow()
    candles: List[Candle] = []
    base_price = {
        "XAUUSD": 2650.0,
        "EURUSD": 1.0850,
        "GBPUSD": 1.2650,
    }.get(symbol, 100.0)

    price = base_price
    for i in range(count):
        ts = now - timedelta(minutes=5 * (count - i))
        import random

        change = random.gauss(0, base_price * 0.001)  # ~0.1% volatility
        price += change
        hi = price + abs(change) * 1.5 + random.random() * base_price * 0.0005
        lo = price - abs(change) * 1.5 - random.random() * base_price * 0.0005
        candles.append(
            Candle(
                time=ts,
                open=price - change,
                high=hi,
                low=lo,
                close=price,
                volume=random.randint(100, 1000),
            )
        )
    return candles


# ──────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────


def fetch_market_data(
    symbol: str,
    interval: str = "5min",
    count: int = 200,
) -> MarketSnapshot:
    """Fetch latest OHLCV data for a symbol.

    Tries Alpha Vantage → falls back to synthetic data if no API key.
    Caches aggressively to stay within rate limits.
    """
    symbol = symbol.upper()

    raw = _cached_or_fetch(symbol, interval)
    if raw:
        candles = _parse_av_candles(raw, interval)
        if candles:
            # Trim to requested count
            candles = candles[-count:]
            logger.info(
                f"Fetched {len(candles)} candles for {symbol} "
                f"(latest: {candles[-1].close:.2f} @ {candles[-1].time})"
            )
            return MarketSnapshot(
                symbol=symbol,
                candles=candles,
                fetched_at=datetime.utcnow(),
            )

    # Fallback
    candles = _fallback_synthetic_data(symbol, count)
    return MarketSnapshot(
        symbol=symbol,
        candles=candles,
        fetched_at=datetime.utcnow(),
    )


def fetch_all() -> Dict[str, MarketSnapshot]:
    """Fetch data for all configured symbols."""
    results: Dict[str, MarketSnapshot] = {}
    for sym in QUADAPT_CFG.market_data.symbols:
        logger.info(f"Fetching market data for {sym}...")
        results[sym] = fetch_market_data(sym)
    return results

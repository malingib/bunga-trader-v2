"""Financial Modeling Prep (FMP) historical bar fetcher.

Pulls real historical OHLCV for our three traded instruments
(EURUSD, GBPUSD, XAUUSD) and caches to CSV under data/market_cache/.

Uses FMP's current `stable` base URL (https://financialmodelingprep.com/stable)
with the ?symbol= form.

Primary path: 1-min intraday via /historical-chart/1min. If the free tier
paywalls intraday (FMP returns an "Error Message" JSON instead of a bar
list), we automatically fall back to EOD daily (/historical-price-eod/full)
and log a loud warning — the backtest will then run on daily granularity,
never on a phantom/empty dataset.

Why FMP over yfinance: yfinance only serves 1-min bars for the last 7-30
days. Our compounding backtest replays *weeks-to-months* of candles, so we
need genuine historical depth.

Free-tier guard: any block (paywall, bad key, 429 quota, 401/403) raises
FMPFreeTierError loudly rather than silently returning empty data.

Caching: each (symbol, interval) is fetched once and written to CSV.
Re-runs read from disk — this keeps us well under the 250 req/day cap.

CSV format is compatible with backtests/fx_backtester.py::load_csv
(columns: date, open, high, low, close, volume).
"""

from __future__ import annotations

import csv
import os
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional

import httpx

# Lazy logger: `from ..logger import CONFIG` would force-load the full app
# config (Telegram/LLM keys) at import time, which breaks this module when
# run standalone (e.g. `python -m core_backend.market_context.fmp`). We only
# need a logger, so fall back to stdlib logging if the app config isn't set.
# Note: config.load_config() calls sys.exit(1) on missing keys, which raises
# SystemExit (a BaseException) — so we must catch BaseException here.
try:  # pragma: no cover - exercised when imported inside the app
    from ..logger import setup_logger

    logger = setup_logger("FMP")
except BaseException:  # pragma: no cover - standalone / missing app config
    import logging

    logger = logging.getLogger("FMP")
    if not logger.handlers:
        logging.basicConfig(level=logging.INFO)

FMP_BASE = "https://financialmodelingprep.com/stable"

# Symbols the backtest loop trades. FMP uses these names directly for both
# forex (EURUSD) and commodity spot (XAUUSD).
DEFAULT_SYMBOLS = ("EURUSD", "GBPUSD", "XAUUSD")

# Cache directory for rate-limit / quota compliance.
CACHE_DIR = Path("data/market_cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Free tier: 250 req/day. Each historical fetch is ONE request covering a
# whole date range, so we don't need aggressive throttling — but we add a
# tiny guard so a tight loop can't burn the daily quota in seconds.
_THROTTLE_SECONDS = 1.0
_last_call = 0.0


class FMPFreeTierError(RuntimeError):
    """Raised when FMP blocks the call (paywalled / bad key / quota)."""


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


def _api_key() -> str:
    key = os.getenv("FMP_API_KEY", "").strip()
    if not key:
        raise FMPFreeTierError(
            "FMP_API_KEY not set. Get a free key at "
            "https://financialmodelingprep.com and put it in .env"
        )
    return key


def _cache_path(symbol: str, interval: str = "1min") -> Path:
    return CACHE_DIR / f"fmp_{symbol}_{interval}.csv"


def _detect_block(payload: object) -> Optional[str]:
    """Return an error message if FMP returned an error object, else None."""
    if isinstance(payload, list):
        return None
    if isinstance(payload, dict):
        for key in ("Error Message", "Information", "Note", "message", "error"):
            val = payload.get(key)
            if val:
                return str(val)
        # Premium / paywall markers FMP sometimes uses
        if any("premium" in str(v).lower() for v in payload.values() if v):
            return "Endpoint requires a premium FMP plan"
    return None


def _parse_candles(payload: List[dict], interval: str = "1min") -> List[Candle]:
    candles: List[Candle] = []
    for row in payload:
        try:
            ts = datetime.strptime(row["date"], "%Y-%m-%d %H:%M:%S")
        except (KeyError, ValueError, TypeError):
            logger.warning("Skipping candle with bad date: %r", row.get("date"))
            continue
        try:
            candles.append(
                Candle(
                    time=ts,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row.get("volume", 0) or 0),
                )
            )
        except (KeyError, ValueError, TypeError) as e:
            logger.warning("Skipping malformed candle at %s: %s", ts, e)
            continue
    # FMP returns newest-first; backtests need chronological order.
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
    """Fetch 1-min historical bars for a symbol and cache to CSV.

    Args:
        symbol: e.g. "EURUSD", "GBPUSD", "XAUUSD".
        start_date / end_date: "YYYY-MM-DD" strings to window the result.
        interval: FMP intraday interval ("1min", "5min", ...).
        force: ignore an existing cache file and re-fetch.

    Returns:
        Chronologically sorted list of Candle.

    Raises:
        FMPFreeTierError: if the key is missing, the endpoint is paywalled,
            or the response is not a bar list.
    """
    symbol = symbol.upper()
    if not force:
        cached = load_cached_csv(symbol, interval)
        if cached is not None:
            logger.info("Using cached %s bars (%d) for %s", interval, len(cached), symbol)
            return _filter_range(cached, _as_date(start_date), _as_date(end_date))

    key = _api_key()

    # stable API uses ?symbol= form; intraday is /historical-chart/{interval}
    url = f"{FMP_BASE}/historical-chart/{interval}"
    _throttle()
    try:
        resp = httpx.get(url, params={"apikey": key, "symbol": symbol}, timeout=30.0)
    except httpx.HTTPError as e:
        raise FMPFreeTierError(f"FMP request failed: {e}") from e

    if resp.status_code == 401:
        # Bad/invalid key — no point falling back.
        raise FMPFreeTierError(
            f"FMP rejected the request (HTTP 401) — check FMP_API_KEY"
        )
    if resp.status_code in (402, 403):
        # 402 = endpoint requires a paid plan; 403 = forbidden. For intraday
        # bars this means the free tier can't serve them — fall back to EOD
        # daily (the free-tier-compatible endpoint) instead of hard-failing.
        if interval == "1min":
            logger.warning(
                "FMP intraday unavailable for %s (HTTP %d) — trying EOD daily fallback",
                symbol, resp.status_code,
            )
            return _fetch_eod_daily(symbol, start_date, end_date, force=force)
        raise FMPFreeTierError(f"FMP HTTP {resp.status_code}: {resp.text[:200]}")
    if resp.status_code >= 400:
        raise FMPFreeTierError(f"FMP HTTP {resp.status_code}: {resp.text[:200]}")

    try:
        payload = resp.json()
    except ValueError as e:
        raise FMPFreeTierError(f"FMP returned non-JSON body: {resp.text[:200]}") from e

    block = _detect_block(payload)
    if block:
        # Intraday may be paywalled on free tier — fall back to EOD daily,
        # which the docs explicitly expose (/historical-price-eod/full).
        # Only do this automatically when the caller asked for 1-min bars.
        if interval == "1min":
            logger.warning(
                "FMP intraday blocked for %s (%s) — trying EOD daily fallback",
                symbol, block,
            )
            return _fetch_eod_daily(symbol, start_date, end_date, force=force)
        raise FMPFreeTierError(f"FMP blocked {symbol}: {block}")

    if not isinstance(payload, list):
        raise FMPFreeTierError(
            f"FMP returned unexpected payload for {symbol}: {type(payload)}"
        )

    candles = _parse_candles(payload, interval)
    if not candles:
        # Intraday empty -> fall back to EOD daily for 1-min callers
        if interval == "1min":
            logger.warning("FMP intraday empty for %s — trying EOD daily fallback", symbol)
            return _fetch_eod_daily(symbol, start_date, end_date, force=force)
        raise FMPFreeTierError(f"FMP returned 0 usable bars for {symbol}")

    _write_csv(symbol, candles, interval)
    return _filter_range(candles, _as_date(start_date), _as_date(end_date))


def _fetch_eod_daily(
    symbol: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    force: bool = False,
) -> List[Candle]:
    """Fallback: FMP /historical-price-eod/full (daily OHLCV).

    Returns daily candles labelled with a 1-min interval tag so the cache
    file and backtest loader still work — but the caller MUST know these are
    daily bars, not 1-min. We log a clear warning and tag the interval
    'eod' in the cache filename to avoid confusion.
    """
    interval = "eod"
    cache_file = _cache_path(symbol, interval)
    if not force and cache_file.exists():
        cached = load_cached_csv(symbol, interval)
        if cached is not None:
            return _filter_range(cached, _as_date(start_date), _as_date(end_date))

    key = _api_key()
    url = f"{FMP_BASE}/historical-price-eod/full"
    _throttle()
    try:
        resp = httpx.get(url, params={"apikey": key, "symbol": symbol}, timeout=30.0)
    except httpx.HTTPError as e:
        raise FMPFreeTierError(f"FMP EOD request failed: {e}") from e

    if resp.status_code in (401, 403):
        raise FMPFreeTierError(
            f"FMP rejected EOD request (HTTP {resp.status_code}) — check FMP_API_KEY"
        )
    if resp.status_code >= 400:
        raise FMPFreeTierError(f"FMP EOD HTTP {resp.status_code}: {resp.text[:200]}")

    try:
        payload = resp.json()
    except ValueError as e:
        raise FMPFreeTierError(f"FMP EOD returned non-JSON: {resp.text[:200]}") from e

    block = _detect_block(payload)
    if block:
        raise FMPFreeTierError(f"FMP EOD blocked {symbol}: {block}")
    if not isinstance(payload, list):
        raise FMPFreeTierError(f"FMP EOD unexpected payload: {type(payload)}")

    candles = _parse_candles(payload, interval)
    if not candles:
        raise FMPFreeTierError(f"FMP EOD returned 0 usable bars for {symbol}")

    _write_csv(symbol, candles, interval)
    logger.warning(
        "EOD daily fallback used for %s (%d daily bars) — NOT 1-min. "
        "Backtest will run on daily granularity.", symbol, len(candles),
    )
    return _filter_range(candles, _as_date(start_date), _as_date(end_date))


def _as_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    return datetime.strptime(s, "%Y-%m-%d").date()


def fetch_all_1min(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    symbols=DEFAULT_SYMBOLS,
    force: bool = False,
) -> dict[str, List[Candle]]:
    """Fetch 1-min bars for all default symbols. Returns {symbol: [Candle]}."""
    out: dict[str, List[Candle]] = {}
    for sym in symbols:
        logger.info("Fetching FMP 1-min for %s ...", sym)
        out[sym] = fetch_historical_1min(sym, start_date, end_date, force=force)
    return out


def cache_path(symbol: str, interval: str = "1min") -> Path:
    """Public accessor for the CSV cache path of a symbol/interval."""
    return _cache_path(symbol, interval)


def fetch_all_to_cache(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    symbols=DEFAULT_SYMBOLS,
    force: bool = False,
) -> dict[str, Path]:
    """Pull-and-cache all symbols, returning {symbol: csv_path}.

    Raises FMPFreeTierError if any symbol fails (e.g. intraday AND eod both
    blocked), so a backtest run never proceeds on a partial/phantom dataset.

    Note: if FMP paywalls 1-min intraday, each symbol's path will point to
    its *_eod.csv fallback instead of *_1min.csv — callers should surface
    that distinction (it is logged loudly when it happens).
    """
    paths: dict[str, Path] = {}
    for sym in symbols:
        fetch_historical_1min(sym, start_date, end_date, force=force)
        # pick the freshest available cache (eod fallback if 1min missing)
        p1 = _cache_path(sym, "1min")
        pe = _cache_path(sym, "eod")
        path = p1 if p1.exists() else pe
        if not path or not path.exists():
            raise FMPFreeTierError(f"No cached bars produced for {sym}")
        paths[sym] = path
    return paths


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Fetch FMP 1-min historical bars")
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--start", help="YYYY-MM-DD")
    ap.add_argument("--end", help="YYYY-MM-DD")
    ap.add_argument("--interval", default="1min")
    ap.add_argument("--force", action="store_true", help="re-fetch ignoring cache")
    args = ap.parse_args()

    bars = fetch_historical_1min(
        args.symbol, args.start, args.end, args.interval, force=args.force
    )
    print(f"{args.symbol}: {len(bars)} bars "
          f"({bars[0].time} -> {bars[-1].time})" if bars else f"{args.symbol}: no bars")

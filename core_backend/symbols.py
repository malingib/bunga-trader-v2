"""Symbol helpers for multi-instrument signal normalisation.

Supports XAUUSD, SP500, NAS100 (and EURUSD/GBPUSD).
Yahoo Finance ticker mapping for live data fetching.

USOIL was dropped: backtests on its 1-min window showed no edge
(best config +0.65%, target >5% NOT MET). See backtests/explore_params_results.txt.
"""

from __future__ import annotations
from typing import Dict, List

# Supported instruments (canonical broker-friendly symbols)
SUPPORTED_SYMBOLS: Dict[str, str] = {
    "GOLD": "XAUUSD",
    "XAUUSD": "XAUUSD",
    "SP500": "SP500",
    "NAS100": "NAS100",
    "EURUSD": "EURUSD",
    "GBPUSD": "GBPUSD",
}

# Aliases that map to canonical symbols
ALIASES: Dict[str, str] = {
    "GOLD": "XAUUSD",
    "XAUUSD": "XAUUSD",
    "SP500": "SP500",
    "SPX": "SP500",
    "NAS100": "NAS100",
    "US100": "NAS100",
    "EURUSD": "EURUSD",
    "GBPUSD": "GBPUSD",
    "EUR/USD": "EURUSD",
    "GBP/USD": "GBPUSD",
    "XAU/USD": "XAUUSD",
}

# Yahoo Finance ticker symbols per canonical symbol
YAHOO_TICKERS: Dict[str, str] = {
    "XAUUSD": "GC=F",
    "SP500": "ES=F",
    "NAS100": "NQ=F",
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
}


def normalize_signal_symbol(symbol: str) -> str:
    """Normalise signal symbols to canonical broker-friendly values."""
    cleaned = (symbol or "").strip().upper()
    if cleaned in ALIASES:
        return ALIASES[cleaned]
    return cleaned


def is_supported_symbol(symbol: str) -> bool:
    """Return True if the symbol is in our supported set."""
    normalized = normalize_signal_symbol(symbol)
    return normalized in SUPPORTED_SYMBOLS.values()


def yahoo_ticker(symbol: str) -> str:
    """Return Yahoo Finance ticker for a canonical symbol."""
    normalized = normalize_signal_symbol(symbol)
    return YAHOO_TICKERS.get(normalized, normalized)


def get_all_supported_symbols() -> List[str]:
    """Return list of all canonical supported symbols (deduplicated)."""
    return list(dict.fromkeys(SUPPORTED_SYMBOLS.values()))

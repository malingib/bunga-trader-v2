"""Symbol helpers for multi-instrument signal normalisation.

Supports GOLD (XAUUSD), EURUSD, GBPUSD with canonical names
and MT5 broker symbol candidates per instrument.
"""

from __future__ import annotations

from typing import Dict, List

# Supported instruments
SUPPORTED_SYMBOLS: Dict[str, str] = {
    "GOLD": "XAUUSD",
    "XAUUSD": "XAUUSD",
    "EURUSD": "EURUSD",
    "GBPUSD": "GBPUSD",
}

# Aliases that map to canonical symbols
ALIASES: Dict[str, str] = {
    "GOLD": "XAUUSD",
    "XAUUSD": "XAUUSD",
    "EURUSD": "EURUSD",
    "GBPUSD": "GBPUSD",
    "EUR/USD": "EURUSD",
    "GBP/USD": "GBPUSD",
    "XAU/USD": "XAUUSD",
}

# MT5 symbol candidates per canonical symbol (ordered by preference)
MT5_CANDIDATES: Dict[str, List[str]] = {
    "XAUUSD": ["XAUUSD", "GOLD", "XAUUSDm", "GOLDm", "XAUUSD.", "GOLD."],
    "EURUSD": ["EURUSD", "EURUSDm", "EURUSD.", "EUR/USD"],
    "GBPUSD": ["GBPUSD", "GBPUSDm", "GBPUSD.", "GBP/USD"],
}


def normalize_signal_symbol(symbol: str) -> str:
    """Normalise signal symbols to canonical broker-friendly values."""
    cleaned = (symbol or "").strip().upper()
    # Direct match
    if cleaned in ALIASES:
        return ALIASES[cleaned]
    # Try alias mapping
    return cleaned


def is_supported_symbol(symbol: str) -> bool:
    """Return True if the symbol is in our supported set."""
    normalized = normalize_signal_symbol(symbol)
    return normalized in SUPPORTED_SYMBOLS.values()


def mt5_candidates(symbol: str) -> List[str]:
    """Return MT5 symbol candidates for a symbol, ordered by preference."""
    normalized = normalize_signal_symbol(symbol)
    if normalized in MT5_CANDIDATES:
        return MT5_CANDIDATES[normalized]
    return [normalized]


def get_all_supported_symbols() -> List[str]:
    """Return list of all canonical supported symbols (deduplicated)."""
    return list(dict.fromkeys(SUPPORTED_SYMBOLS.values()))

"""Research strategy catalog.

These are research hypotheses, not claims of profitability. The catalog is
intentionally broad so the lab can test families and combinations rather
than optimizing a single strategy forever.
"""

STRATEGY_CATALOG = {
    "trend": ["ema_cross", "sma_cross", "donchian_trend", "adx_trend", "supertrend", "time_series_momentum"],
    "breakout": ["opening_range_breakout", "donchian_breakout", "range_expansion", "nr4_nr7", "session_breakout", "breakout_retest"],
    "momentum": ["roc_momentum", "multi_period_momentum", "volume_momentum", "volatility_adjusted_momentum"],
    "mean_reversion": ["rsi_reversion", "bollinger_reversion", "zscore_reversion", "vwap_reversion", "atr_deviation"],
    "price_action": ["inside_bar", "engulfing", "pin_bar", "swing_break_retest", "liquidity_sweep"],
    "volatility": ["atr_regime", "volatility_breakout", "volatility_compression", "range_regime"],
    "crypto": ["funding_extremes", "cross_sectional_momentum", "cross_sectional_reversion", "btc_lead_lag", "market_breadth", "basis_carry"],
}

SYMBOL_UNIVERSE = {
    "forex": ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF"],
    "metals": ["XAUUSD", "XAGUSD"],
    "indices": ["NAS100", "US500", "US30", "GER40", "UK100"],
    "crypto": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "ADAUSDT", "AVAXUSDT", "DOGEUSDT"],
}

TIMEFRAMES = ["5m", "15m", "30m", "1h", "4h", "1d"]

# Research combinations to test. The engine should reject combinations that
# add complexity without improving validation/OOS robustness.
COMBINATION_TEMPLATES = [
    ("trend", "momentum"),
    ("trend", "breakout"),
    ("trend", "mean_reversion"),
    ("breakout", "volatility"),
    ("momentum", "volatility"),
    ("mean_reversion", "volatility"),
    ("price_action", "trend"),
    ("breakout", "trend", "volatility"),
    ("trend", "momentum", "volatility"),
    ("regime_switch", "trend", "mean_reversion"),
]


def all_strategies():
    return [strategy for family in STRATEGY_CATALOG.values() for strategy in family]


def all_symbols():
    return [symbol for symbols in SYMBOL_UNIVERSE.values() for symbol in symbols]

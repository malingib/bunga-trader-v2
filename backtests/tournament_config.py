"""Default research tournament configuration.

This is intentionally a bounded first pass; expand the universe as historical
data coverage is added rather than hiding missing-data assumptions.
"""
from strategy_catalog import SYMBOL_UNIVERSE, TIMEFRAMES, STRATEGY_CATALOG

DEFAULT_FAMILIES = tuple(STRATEGY_CATALOG)
DEFAULT_SYMBOLS = tuple(SYMBOL_UNIVERSE["forex"] + SYMBOL_UNIVERSE["metals"] + SYMBOL_UNIVERSE["indices"] + SYMBOL_UNIVERSE["crypto"])
DEFAULT_TIMEFRAMES = ("15m", "1h", "4h")

MIN_TRADES_TRAIN = 50
MIN_TRADES_VALIDATION = 30
MIN_TRADES_OOS = 20

# Reject candidates that only win because of an isolated parameter point.
MAX_PARAMETER_CV = 0.50
MAX_COMPLEXITY = 4

# Conservative default research cost assumptions. Instrument-specific loaders
# should override these from actual historical bid/ask/fee data when present.
DEFAULT_COST_MODEL = {
    "spread_points": 0.0,
    "slippage_points": 0.0,
    "commission_per_trade": 0.0,
    "crypto_fee_bps": 10.0,
    "crypto_funding_bps_per_8h": 0.0,
}

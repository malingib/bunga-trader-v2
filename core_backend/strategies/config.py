"""Quadapt ML Trader — Configuration & Input Parameters.

All Pine Script input() values from the original script are defined here
as a single Pydantic settings model.  ~190 tunable parameters organised by
subsystem.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Literal


# ──────────────────────────────────────────────
# Signal Modes
# ──────────────────────────────────────────────
SignalMode = Literal["Independent", "Consensus", "Primary Priority"]


# ──────────────────────────────────────────────
# MLMA kernel types
# ──────────────────────────────────────────────
MLMAKernel = Literal[
    "Linear",
    "RBF",
    "Polynomial",
    "Sigmoid",
    "Laplacian",
    "Cauchy",
    "Thin Plate Spline",
    "Inverse Multiquadric",
]
# We implement the two practical ones; others fall back to RBF
PRACTICAL_KERNELS: tuple[str, ...] = ("Linear", "RBF", "Polynomial")


@dataclass
class DualLengthEnvelopeConfig:
    """Primary signal generator — dual-length envelope breakout system."""

    # Lengths (tuned to original: 120 / 70)
    length_primary: int = 120
    length_secondary: int = 70

    # Envelope width (ATR multiplier — original used 1.0-3.0)
    envelope_mult_primary: float = 2.0
    envelope_mult_secondary: float = 1.5

    # ATR period for envelope calculation
    atr_period: int = 14

    # Signal mode
    signal_mode: SignalMode = "Independent"

    # Minimum envelope width as fraction of price  (prevents zero-width)
    min_envelope_pct: float = 0.001  # 0.1 %

    # ── Enhancements from Scalping Pullback ──
    # Barssince guard: price must have been inside envelope ≤ N bars ago
    use_barssince_guard: bool = False  # was for envelope re-entry; sweep trigger doesn't need it
    barssince_lookback: int = 5

    # Adaptive envelope widening: auto-widen bands when ATR is high
    adaptive_envelope_widening: bool = True
    max_envelope_multiplier: float = 3.5
    volatility_widening_atr_threshold: float = 0.0025  # widen envelope if ATR/price > threshold

    # Heikin Ashi smoothing for envelope input
    use_heikin_ashi: bool = True


@dataclass
class MLMAConfig:
    """ML Moving Average — kernel regression trend line."""

    enabled: bool = True
    length: int = 34
    kernel: MLMAKernel = "RBF"
    gamma: float = 0.5  # kernel bandwidth


@dataclass
class OrderBlockConfig:
    """Volatility-based order block detection."""

    enabled: bool = True
    lookback: int = 50
    min_block_strength: float = 0.3  # quality threshold 0-1
    max_blocks: int = 5


@dataclass
class StochRSIConfig:
    """StochRSI entry timing — stolen from StochRSI+Supertrend."""

    enabled: bool = True
    rsi_length: int = 14
    stoch_length: int = 14
    smooth_k: int = 3
    smooth_d: int = 3
    oversold: float = 20.0
    overbought: float = 80.0


@dataclass
class SupertrendConfig:
    """Supertrend trend filter — stolen from StochRSI+Supertrend."""

    enabled: bool = True
    atr_period: int = 11
    factor: float = 2.0


@dataclass
class TTMConfig:
    """TTM Squeeze detection — stolen from TTM Squeeze."""

    enabled: bool = True
    bb_length: int = 20
    bb_mult: float = 2.0
    kc_length: int = 20
    kc_mult: float = 1.5


@dataclass
class TrendGateConfig:
    """Pillar ③ — HARD trend filter (gate, not score weight).

    A trend gate BLOCKS trades; it does not merely nudge a score. The 200MA
    and VWAP define bias and filter counter-trend entries. Volume is advisory
    (FX spot has no real volume -> gate stays neutral, never blocks on it).
    """

    enabled: bool = True
    ma_period: int = 200  # 1-min bars (~3.3h on gold)
    require_200ma_alignment: bool = True  # block BUY below MA / SELL above MA
    use_vwap: bool = True  # require price >= VWAP for BUY, <= VWAP for SELL
    vwap_band_pct: float = 0.0008  # tolerance band around VWAP (fraction of price)
    volume_sma_period: int = 20
    require_volume_spike: bool = False  # OFF for FX (no real vol); ON for gold
    volume_spike_mult: float = 1.5  # current vol >= this x SMA to confirm


@dataclass
class SweepTriggerConfig:
    """Pillar ① — liquidity-sweep trigger (replaces envelope as primary)."""

    enabled: bool = True
    swing_left: int = 4
    swing_right: int = 4
    swing_lookback: int = 100  # bars of swing history to search for liquidity
    min_wick_ratio: float = 0.2  # wick beyond extreme must be >=20% of range (real stop-hunts)
    require_fvg: bool = False  # if True, also need a fair-value gap present


@dataclass
class TriggerConfig:
    """Entry trigger selector.

    The original Quadapt trigger was a liquidity-sweep (ICT stop-hunt) model.
    Backtests on XAUUSD 1-min (Jun–Jul 2026, ~20K bars) showed that trigger
    has NEGATIVE expectancy on every SL/TP tested (PF < 1.0). It is retained
    as `liquidity_sweep` for live safety / comparison.

    The default `mean_reversion` is NOT a naive RSI extreme fade — that is also
    negative with realistic (next-bar-open) fill. The genuine, repeatable edge
    on this data is a **StochRSI K-cross-out-of-extreme with a TIME-BASED exit**:
    wait for StochRSI %K to cross back up through %D out of oversold (or down
    through %D out of overbought), then fade the reversion and close at market
    after `hold_bars` (the reversion resolves on average, but a fixed TP/SL gets
    clipped by 1-min noise). Quant results on this window: PF ~1.14, win ~53%,
    E ~ +1.9 pts/ trade for rl=14, sk=5, sl=3, hold≈480.

    Modes:
      - "liquidity_sweep": original 3-pillar ICT trigger (Pillar ①).
      - "mean_reversion": StochRSI-reversion-with-time-exit (proven edge).
    """

    mode: str = "mean_reversion"  # "liquidity_sweep" | "mean_reversion"

    # ── Mean-reversion settings (StochRSI K-cross-out-of-extreme) ──
    # A BUY fires when StochRSI %K crosses UP through %D while %K < oversold.
    # A SELL fires when StochRSI %K crosses DOWN through %D while %K > overbought.
    stoch_rsi_rsi_length: int = 14   # RSI length inside StochRSI
    stoch_rsi_stoch_length: int = 14 # Stochastic length
    stoch_rsi_smooth_k: int = 5      # %K smoothing  (5 = best edge on this data)
    stoch_rsi_smooth_d: int = 3      # %D smoothing
    stoch_rsi_oversold: float = 20.0 # fire BUY cross when %K < this
    stoch_rsi_overbought: float = 80.0  # fire SELL cross when %K > this
    # Time-based exit: hold this many 1-min bars, then close at market.
    # This is the exit model that makes the edge positive (not SL/TP).
    hold_bars: int = 480
    # Wide protective stop (not the primary exit) — guards gap/runaway risk.
    protective_sl_atr: float = 4.0
    # Optional: also require raw RSI to be on the extreme side (adds a little edge).
    require_rsi_filter: bool = False
    rsi_period: int = 14
    rsi_oversold: float = 30.0
    rsi_overbought: float = 70.0
    # Optional range-stretch: price must be stretched vs 20-bar mean.
    require_range_stretch: bool = False
    range_stretch_pct: float = 0.6  # close must be >= this * ATR from 20-bar mean


@dataclass
class PriceActionConfig:
    """Pillar ② — PA confirmation that tightens the sweep entry."""

    enabled: bool = True
    require_displacement: bool = True  # last bar body >= 1.5 x ATR (conviction)
    displacement_mult: float = 1.5
    require_choch_alignment: bool = False  # CHoCH is noisy on 1-min -> score term, not gate
    min_rejection_wick: float = 0.0  # 0 = don't require; else tail fraction
    use_fvg_boost: bool = True  # FVG in direction adds score, not a hard gate


@dataclass
class SignalQualityConfig:
    """Signal quality scoring engine."""

    # MTF alignment timeframes (in minutes)
    timeframes: List[int] = field(default_factory=lambda: [1, 3, 5, 15, 30, 60, 240])

    # Weights for each component of quality score (0-100)
    weight_trend_alignment: float = 0.20  # MLMA + Supertrend agreement
    weight_mtf_alignment: float = 0.05  # multi-timeframe agreement (note: 1-TF only, dilutive)
    weight_volume: float = 0.10  # REAL volume term (was mis-scoring envelope strength)
    weight_volatility: float = 0.10  # squeeze release boost
    weight_momentum: float = 0.05  # StochRSI timing (dilutive if neutral)
    weight_order_block: float = 0.05  # dilutive if no OB nearby
    weight_envelope: float = 0.05  # demoted: envelope is now a WEIGHT, not a trigger
    weight_liquidity_sweep: float = 0.30  # NEW pillar ① — PRIMARY edge (boosted)
    weight_pa_structure: float = 0.20  # NEW pillar ② — confirmation (boosted)
    weight_vwap_trend: float = 0.00  # NEW pillar ③ — applied as a GATE, not weight
    weight_clustering: float = 0.10  # penalty for near previous signal

    # Thresholds
    min_quality_score: float = 62.0  # 0-100 (reversion trigger reaches this; gate selects the high-conviction tail)
    signal_clustering_bars: int = 10  # penalty if signal within N bars of previous
    max_quality_score: float = 95.0  # cap to prevent overconfidence


@dataclass
class RiskConfig:
    """Risk management — TP/SL from the original Quadapt Pine Script."""

    # Stop loss methods
    sl_method: Literal["ATR", "Swing", "Order Block", "Percentage"] = "ATR"
    atr_sl_multiplier: float = 1.5
    sl_atr_period: int = 14
    sl_percent: float = 1.0  # % of price for Percentage method
    sl_swing_lookback: int = 20  # bars for swing low/high

    # Take profit — Fibonacci extension levels
    tp_method: Literal[
        "Dynamic ATR", "Swing-Based", "Adaptive Swing", "Heuristic"
    ] = "Dynamic ATR"
    tp_levels: List[float] = field(
        default_factory=lambda: [1.272, 1.618, 2.618, 4.236]
    )
    max_tp_levels: int = 4
    tp_spacing_multiplier: float = 1.5
    atr_tp_multiplier: float = 2.5

    # Minimum risk-reward
    min_rr_ratio: float = 1.5


@dataclass
class AdaptiveConfig:
    """Market regime adaptation and clustering prevention."""

    regime_adapation_enabled: bool = True
    regime_lookback: int = 50
    regime_threshold: float = 0.3  # volatility threshold for regime classification

    # Clustering prevention
    use_clustering_prevention: bool = True
    cluster_lookback_bars: int = 15
    cluster_price_distance_pct: float = 0.3  # % of ATR


@dataclass
class MomentumConfig:
    """Momentum breakout config — per-symbol tuning.

    Per-symbol overrides let each instrument use its validated config.
    Actual strategy parameters (sl_atr, rr, trend_ema) live in
    momentum_breakout.MomentumConfig — this is the system-level config.
    """

    enabled: bool = True
    warmup: int = 200          # Bars before first trade
    lookback: int = 2          # Breakout lookback (bars)
    max_hold: int = 15         # Max bars before time-exit
    atr_period: int = 14       # ATR period
    # Per-symbol (symbol -> dict(sl_atr, rr, trend_ema))
    defaults: dict = field(default_factory=lambda: {
        "XAUUSD": dict(sl_atr=1.2, rr=4.0, trend_ema=0),
        "SP500":  dict(sl_atr=1.2, rr=1.5, trend_ema=0),
        "NAS100": dict(sl_atr=1.2, rr=1.5, trend_ema=200),
    })


@dataclass
class MarketDataConfig:
    """Free market data API configuration."""

    provider: Literal["alpha_vantage", "twelvedata", "finnhub", "yfinance"] = "yfinance"
    symbols: List[str] = field(
        default_factory=lambda: ["XAUUSD", "SP500", "NAS100"],
        metadata={"help": "Symbols traded by the engine"},
    )
    bars_to_fetch: int = 2000  # 1-min bars for momentum strategy
    interval: str = "1min"
    timeout_seconds: int = 15
    poll_interval_seconds: int = 60


# ──────────────────────────────────────────────
# Top-level strategy config (what the engine reads)
# ──────────────────────────────────────────────


@dataclass
class QuadaptConfig:
    """Complete Quadapt ML Trader configuration."""

    enabled: bool = True
    name: str = "Quadapt_ML_Trader"

    # Subsystem configs
    envelopes: DualLengthEnvelopeConfig = field(default_factory=DualLengthEnvelopeConfig)
    mlma: MLMAConfig = field(default_factory=MLMAConfig)
    order_blocks: OrderBlockConfig = field(default_factory=OrderBlockConfig)
    stoch_rsi: StochRSIConfig = field(default_factory=StochRSIConfig)
    supertrend: SupertrendConfig = field(default_factory=SupertrendConfig)
    ttm: TTMConfig = field(default_factory=TTMConfig)
    quality: SignalQualityConfig = field(default_factory=SignalQualityConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    adaptive: AdaptiveConfig = field(default_factory=AdaptiveConfig)
    trend_gate: TrendGateConfig = field(default_factory=TrendGateConfig)
    sweep: SweepTriggerConfig = field(default_factory=SweepTriggerConfig)
    trigger: TriggerConfig = field(default_factory=TriggerConfig)
    price_action: PriceActionConfig = field(default_factory=PriceActionConfig)
    market_data: MarketDataConfig = field(default_factory=MarketDataConfig)
    momentum: MomentumConfig = field(default_factory=MomentumConfig)


# Singleton
QUADAPT_CFG = QuadaptConfig()

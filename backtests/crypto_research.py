"""Crypto-specific research helpers.

These utilities remain data-driven: no assumption is made that funding,
breadth or lead/lag signals are profitable until historical data validates it.
"""
from __future__ import annotations

import pandas as pd


def funding_extreme_signal(funding: pd.Series, z_window: int = 90, z: float = 2.0) -> pd.Series:
    mean = funding.rolling(z_window, min_periods=z_window).mean()
    std = funding.rolling(z_window, min_periods=z_window).std()
    return ((funding - mean) / std.replace(0, pd.NA)).abs().gt(z).fillna(False)


def cross_sectional_momentum(prices: pd.DataFrame, lookback: int = 20, top_fraction: float = .2) -> pd.DataFrame:
    returns = prices.pct_change(lookback)
    ranks = returns.rank(axis=1, pct=True)
    return ranks.ge(1.0 - top_fraction)


def cross_sectional_reversion(prices: pd.DataFrame, lookback: int = 20, bottom_fraction: float = .2) -> pd.DataFrame:
    returns = prices.pct_change(lookback)
    ranks = returns.rank(axis=1, pct=True)
    return ranks.le(bottom_fraction)


def btc_lead_lag(btc: pd.Series, asset: pd.Series, lag: int = 1, lookback: int = 20) -> pd.Series:
    btc_return = btc.pct_change(lookback).shift(lag)
    asset_return = asset.pct_change(lookback)
    return (btc_return > 0) & (asset_return > 0)


def market_breadth(prices: pd.DataFrame, ma_period: int = 50, threshold: float = .6) -> pd.Series:
    above = prices.gt(prices.rolling(ma_period, min_periods=ma_period).mean())
    return (above.mean(axis=1) >= threshold).fillna(False)

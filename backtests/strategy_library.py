"""Pure historical-data strategy signal library.

Signals are deliberately simple, deterministic and look-ahead safe. They are
research building blocks; profitability must be established by the lab.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _close(d): return d["Close"].astype(float)
def _high(d): return d["High"].astype(float)
def _low(d): return d["Low"].astype(float)
def _volume(d): return d["Volume"].astype(float) if "Volume" in d else pd.Series(1.0, index=d.index)


def ema(d, period): return _close(d).ewm(span=period, adjust=False, min_periods=period).mean()
def sma(d, period): return _close(d).rolling(period, min_periods=period).mean()
def atr(d, period=14):
    c, h, l = _close(d), _high(d), _low(d)
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=period).mean()
def rsi(d, period=14):
    delta = _close(d).diff()
    up, down = delta.clip(lower=0), -delta.clip(upper=0)
    rs = up.ewm(alpha=1/period, adjust=False, min_periods=period).mean() / down.ewm(alpha=1/period, adjust=False, min_periods=period).mean()
    return 100 - 100/(1+rs)
def adx(d, period=14):
    h, l, c = _high(d), _low(d), _close(d)
    up, dn = h.diff(), -l.diff()
    plus = up.where((up > dn) & (up > 0), 0.0)
    minus = dn.where((dn > up) & (dn > 0), 0.0)
    a = atr(d, period)
    pdi, mdi = 100*plus.ewm(alpha=1/period, adjust=False).mean()/a, 100*minus.ewm(alpha=1/period, adjust=False).mean()/a
    dx = 100*(pdi-mdi).abs()/(pdi+mdi).replace(0, np.nan)
    return dx.ewm(alpha=1/period, adjust=False, min_periods=period).mean()


def ema_trend(d, fast=20, slow=50): return (ema(d, fast) > ema(d, slow)).fillna(False)
def momentum(d, lookback=20): return (_close(d).pct_change(lookback) > 0).fillna(False)
def donchian_breakout(d, lookback=20): return (_close(d) > _high(d).shift(1).rolling(lookback).max()).fillna(False)
def rsi_reversion(d, threshold=30): return (rsi(d) < threshold).fillna(False)
def bollinger_reversion(d, period=20, z=2.0):
    m = sma(d, period); s = _close(d).rolling(period).std()
    return (_close(d) < m-z*s).fillna(False)
def vwap_reversion(d, lookback=20):
    v = _volume(d)
    vw = (_close(d)*v).rolling(lookback).sum()/v.rolling(lookback).sum()
    return (_close(d) < vw).fillna(False)
def atr_expansion(d, period=14, quantile=0.7, window=100):
    a = atr(d, period)
    threshold = a.shift(1).rolling(window, min_periods=window).quantile(quantile)
    return (a > threshold).fillna(False)
def range_expansion(d, period=20, multiple=1.5):
    r = _high(d)-_low(d)
    return (r > r.shift(1).rolling(period).mean()*multiple).fillna(False)
def inside_bar(d):
    return ((_high(d) < _high(d).shift(1)) & (_low(d) > _low(d).shift(1))).fillna(False)
def volume_confirmation(d, period=20):
    return (_volume(d) > _volume(d).shift(1).rolling(period).mean()).fillna(False)


def combine(*signals, mode="all"):
    frame = pd.concat([s.astype(bool) for s in signals], axis=1)
    return frame.all(axis=1) if mode == "all" else frame.any(axis=1)


STRATEGIES = {
    "ema_trend": ema_trend,
    "momentum": momentum,
    "donchian_breakout": donchian_breakout,
    "rsi_reversion": rsi_reversion,
    "bollinger_reversion": bollinger_reversion,
    "vwap_reversion": vwap_reversion,
    "atr_expansion": atr_expansion,
    "range_expansion": range_expansion,
    "inside_bar": inside_bar,
    "volume_confirmation": volume_confirmation,
}

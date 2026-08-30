"""Pure historical-data strategy signal library.

Signals are deterministic research building blocks. They do not place orders.
"""
from __future__ import annotations
import numpy as np
import pandas as pd


def _close(d): return d["Close"].astype(float)
def _high(d): return d["High"].astype(float)
def _low(d): return d["Low"].astype(float)
def _open(d): return d["Open"].astype(float)
def _volume(d): return d["Volume"].astype(float) if "Volume" in d else pd.Series(1.0, index=d.index)
def ema(d, period): return _close(d).ewm(span=period, adjust=False, min_periods=period).mean()
def sma(d, period): return _close(d).rolling(period, min_periods=period).mean()
def atr(d, period=14):
    c,h,l=_close(d),_high(d),_low(d)
    tr=pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
    return tr.rolling(period,min_periods=period).mean()
def rsi(d, period=14):
    delta=_close(d).diff(); up=delta.clip(lower=0); down=-delta.clip(upper=0)
    rs=up.ewm(alpha=1/period,adjust=False,min_periods=period).mean()/down.ewm(alpha=1/period,adjust=False,min_periods=period).mean()
    return 100-100/(1+rs)
def adx(d, period=14):
    h,l,c=_high(d),_low(d),_close(d); up,dn=h.diff(),-l.diff()
    plus=up.where((up>dn)&(up>0),0.0); minus=dn.where((dn>up)&(dn>0),0.0); a=atr(d,period)
    pdi=100*plus.ewm(alpha=1/period,adjust=False).mean()/a; mdi=100*minus.ewm(alpha=1/period,adjust=False).mean()/a
    dx=100*(pdi-mdi).abs()/(pdi+mdi).replace(0,np.nan)
    return dx.ewm(alpha=1/period,adjust=False,min_periods=period).mean()

def ema_trend(d, fast=20, slow=50): return (ema(d,fast)>ema(d,slow)).fillna(False)
def sma_trend(d, fast=50, slow=200): return (sma(d,fast)>sma(d,slow)).fillna(False)
def momentum(d, lookback=20): return (_close(d).pct_change(lookback)>0).fillna(False)
def roc_momentum(d, lookback=12, threshold=0.0): return (_close(d).pct_change(lookback)>threshold).fillna(False)
def time_series_momentum(d, lookback=60): return (_close(d).pct_change(lookback)>0).fillna(False)
def volatility_adjusted_momentum(d, lookback=20, atr_period=14, threshold=1.0): return ((_close(d).pct_change(lookback)*_close(d)/atr(d,atr_period))>threshold).fillna(False)
def donchian_breakout(d, lookback=20): return (_close(d)>_high(d).shift(1).rolling(lookback).max()).fillna(False)
def breakout_retest(d, lookback=20):
    prior=_high(d).shift(1).rolling(lookback).max(); return (_close(d)>prior).fillna(False) & (_low(d)<=prior)
def rsi_reversion(d, threshold=30): return (rsi(d)<threshold).fillna(False)
def bollinger_reversion(d, period=20, z=2.0):
    m=sma(d,period); s=_close(d).rolling(period).std(); return (_close(d)<m-z*s).fillna(False)
def zscore_reversion(d, period=30, z=-2.0):
    c=_close(d); m=c.rolling(period).mean(); s=c.rolling(period).std(); return ((c-m)/s<z).fillna(False)
def vwap_reversion(d, lookback=20):
    v=_volume(d); vw=( _close(d)*v).rolling(lookback).sum()/v.rolling(lookback).sum(); return (_close(d)<vw).fillna(False)
def atr_expansion(d, period=14, quantile=0.7, window=100):
    a=atr(d,period); threshold=a.shift(1).rolling(window,min_periods=window).quantile(quantile); return (a>threshold).fillna(False)
def range_expansion(d, period=20, multiple=1.5):
    r=_high(d)-_low(d); return (r>r.shift(1).rolling(period).mean()*multiple).fillna(False)
def nr7(d):
    r=_high(d)-_low(d); return (r==r.rolling(7,min_periods=7).min()).fillna(False)
def inside_bar(d): return ((_high(d)<_high(d).shift(1))&(_low(d)>_low(d).shift(1))).fillna(False)
def engulfing_bull(d): return ((_close(d)>_open(d))&(_close(d).shift(1)<_open(d).shift(1))&(_close(d)>=_open(d).shift(1))&(_open(d)<=_close(d).shift(1))).fillna(False)
def pin_bar(d, wick_ratio=2.0):
    body=(_close(d)-_open(d)).abs(); lower=_open(d).combine(_close(d),min)-_low(d); return (lower>body*wick_ratio).fillna(False)
def adx_trend(d, period=14, threshold=20): return (adx(d,period)>threshold).fillna(False)
def volume_confirmation(d, period=20): return (_volume(d)>_volume(d).shift(1).rolling(period).mean()).fillna(False)
def combine(*signals, mode="all"):
    frame=pd.concat([s.astype(bool) for s in signals],axis=1); return frame.all(axis=1) if mode=="all" else frame.any(axis=1)

STRATEGIES={
    "ema_trend":ema_trend,"sma_trend":sma_trend,"momentum":momentum,"roc_momentum":roc_momentum,
    "time_series_momentum":time_series_momentum,"volatility_adjusted_momentum":volatility_adjusted_momentum,
    "donchian_breakout":donchian_breakout,"breakout_retest":breakout_retest,"rsi_reversion":rsi_reversion,
    "bollinger_reversion":bollinger_reversion,"zscore_reversion":zscore_reversion,"vwap_reversion":vwap_reversion,
    "atr_expansion":atr_expansion,"range_expansion":range_expansion,"nr7":nr7,"inside_bar":inside_bar,
    "engulfing_bull":engulfing_bull,"pin_bar":pin_bar,"adx_trend":adx_trend,"volume_confirmation":volume_confirmation,
}

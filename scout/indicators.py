"""Göstergeler. KURAL: t. satırdaki hiçbir değer t'den sonraki veriye bağlı olamaz.

Geçmişe bakan her referans (kırılım seviyesi, medyan hacim, taban ATR) shift(1) ile
bir önceki mumdan başlar; böylece sinyal mumunun kendisi kendi eşiğini şişiremez.
tests/test_indicators.py bu kuralı otomatik doğrular.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import StrategyParams

REQUIRED_COLUMNS = ["ts", "open", "high", "low", "close", "volume"]


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def true_range(df: pd.DataFrame) -> pd.Series:
    pc = df["close"].shift(1)
    return pd.concat(
        [df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1
    ).max(axis=1)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """Wilder ATR."""
    return true_range(df).ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def prepare(df: pd.DataFrame, p: StrategyParams) -> pd.DataFrame:
    """OHLCV -> strateji özellikleri. Girdi: ts, open, high, low, close, volume (ts = ms)."""
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"Eksik sütun(lar): {missing}")
    d = df[REQUIRED_COLUMNS].copy().reset_index(drop=True)

    d["atr"] = atr(d, p.atr_n)
    d["atr_prev"] = d["atr"].shift(1)
    d["atr_pct"] = d["atr_prev"] / d["close"].shift(1)

    atr_hist = d["atr"].shift(1)
    d["atr_base"] = atr_hist.rolling(p.atr_base_n).median()
    d["atr_pre"] = atr_hist.rolling(p.pre_bars).mean()
    d["pre_ratio"] = d["atr_pre"] / d["atr_base"]

    d["don_hi"] = d["high"].shift(1).rolling(p.donchian_n).max()
    d["don_lo"] = d["low"].shift(1).rolling(p.donchian_n).min()

    d["vol_med"] = d["volume"].shift(1).rolling(p.vol_lookback).median()
    d["vol_mult"] = d["volume"] / d["vol_med"].replace(0, np.nan)

    rng = (d["high"] - d["low"]).replace(0, np.nan)
    d["range_atr"] = rng / d["atr_prev"]
    d["body_ratio"] = (d["close"] - d["open"]).abs() / rng
    d["close_loc"] = (d["close"] - d["low"]) / rng  # 1.0 = tepede kapanış
    d["run24"] = d["close"] / d["close"].shift(24) - 1.0
    return d


def trend_state(close: pd.Series, n: int = 50) -> str:
    """Basit trend etiketi: kapanış EMA(n) üstünde/altında (±%0.3 tampon)."""
    if len(close) < n + 5:
        return "NEUTRAL"
    e = ema(close, n).iloc[-1]
    c = close.iloc[-1]
    dev = c / e - 1.0
    if dev > 0.003:
        return "BULLISH"
    if dev < -0.003:
        return "BEARISH"
    return "NEUTRAL"


def resample_4h(df_1h: pd.DataFrame) -> pd.DataFrame:
    t = df_1h.copy()
    t["dt"] = pd.to_datetime(t["ts"], unit="ms", utc=True)
    t = t.set_index("dt")
    out = t.resample("4h").agg(
        {"ts": "first", "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna(subset=["open", "close"])
    return out.reset_index(drop=True)


def btc_regime_series(btc_1h: pd.DataFrame) -> pd.Series:
    """+1 / 0 / -1 BTC rejimi (EMA100 sapması), indeks = ts. Backtest filtresi için."""
    c = btc_1h["close"]
    dev = c / ema(c, 100) - 1.0
    reg = np.where(dev > 0.003, 1, np.where(dev < -0.003, -1, 0))
    return pd.Series(reg, index=btc_1h["ts"].to_numpy())
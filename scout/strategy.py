"""Sinyal üretimi. Backtest ve canlı tarama AYNI fonksiyonu kullanır (tek doğru kaynak).

Strateji (LONG; SHORT ayna görüntüsü):
  1. Önceki volatilite 'normal' seviyeden fazla yüksek değil (hareket zaten patlamamış).
  2. 1 saatlik mum, önceki N saatlik tepeyi KAPANIŞLA kırıyor.
  3. Mum güçlü: boyu >= min_range_atr x ATR, gövde büyük, tepeye yakın kapanış.
  4. Hacim patlaması: mum hacmi >= min_vol_mult x son 24 mumun medyanı.
  5. Kovalama yok: seviyenin çok ötesinde kapanmamış, son 24 saatte zaten çok koşmamış.
  6. Coin hareket edebilecek kadar oynak (1s ATR / fiyat >= min_atr_pct).
Giriş: sonraki mumun açılışı. Stop/çıkış: scout/exits.py.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import StrategyParams


def compute_signals(d: pd.DataFrame, p: StrategyParams, btc_regime: np.ndarray | None = None) -> pd.DataFrame:
    """prepare() çıktısından her mum için long/short bayrakları üretir.

    Satır t, yalnızca t ve öncesi veriyle karar verir. Son satır (kapanmış son mum) canlıda kullanılır.
    """
    common = (
        (d["range_atr"] >= p.min_range_atr)
        & (d["body_ratio"] >= p.min_body_ratio)
        & (d["vol_mult"] >= p.min_vol_mult)
        & (d["pre_ratio"] <= p.max_pre_atr_ratio)
        & (d["atr_pct"] >= p.min_atr_pct)
    )
    up_dist = (d["close"] - d["don_hi"]) / d["atr_prev"]
    dn_dist = (d["don_lo"] - d["close"]) / d["atr_prev"]

    long_ = (
        common
        & (d["close"] > d["don_hi"])
        & (d["close"] > d["open"])
        & (d["close_loc"] >= p.min_close_loc)
        & (up_dist <= p.max_chase_atr)
        & (d["run24"] <= p.max_run_24h)
    )
    short_ = (
        common
        & (d["close"] < d["don_lo"])
        & (d["close"] < d["open"])
        & (d["close_loc"] <= 1.0 - p.min_close_loc)
        & (dn_dist <= p.max_chase_atr)
        & (-d["run24"] <= p.max_run_24h)
    )

    if p.use_btc_filter and btc_regime is not None:
        reg = np.asarray(btc_regime)
        long_ = long_ & (reg >= 0)    # BTC düşüş rejimindeyse LONG yok
        short_ = short_ & (reg <= 0)  # BTC yükseliş rejimindeyse SHORT yok

    out = pd.DataFrame(
        {
            "long": long_.fillna(False).to_numpy(dtype=bool),
            "short": short_.fillna(False).to_numpy(dtype=bool),
            # Şeffaf güç ölçüsü: uydurma "olasılık" değil. Backtest tercile tablosu işe yarayıp yaramadığını gösterir.
            "strength": (d["vol_mult"] * d["range_atr"]).to_numpy(dtype=float),
        },
        index=d.index,
    )
    return out
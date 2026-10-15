"""Pozisyon yönetimi (çıkış) durum makinesi. Backtest ve canlı takip AYNI kodu kullanır.

Mum içi sıralama belirsizliği her zaman KÖTÜMSER çözülür:
  * Aynı mumda hem stop hem hedef değerse -> önce stop.
  * Açılış stopun ötesindeyse (boşluk) -> açılış fiyatından çıkış (stop fiyatından değil).
  * TP1 sonrası stop girişe çekilir; aynı mumun dibi/tepesi girişe değdiyse çıkış girişte olur.
  * İz süren stop, mum KAPANDIKTAN sonra güncellenir; ancak bir sonraki mumdan itibaren geçerli.
Sonuçlar R cinsindendir (1R = başlangıç stop mesafesi). Maliyet burada DEĞİL, backtest'te eklenir.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Optional

from .config import StrategyParams


@dataclass
class OpenTrade:
    side: int                 # +1 LONG, -1 SHORT
    entry: float
    init_stop: float
    stop: float               # güncel stop (iz süren / BE sonrası)
    risk: float               # fiyat birimiyle 1R
    tp1: float
    remaining: float = 1.0    # kalan pozisyon oranı
    realized_r: float = 0.0   # kapanan kısımlardan gelen brüt R
    tp1_done: bool = False
    extreme: float = 0.0      # girişten beri en lehe fiyat
    worst: float = 0.0        # girişten beri en aleyhe fiyat
    bars: int = 0
    mfe_pct: float = 0.0      # en yüksek lehe hareket (%, girişe göre)
    mae_pct: float = 0.0      # en yüksek aleyhe hareket (%)
    done: bool = False
    exit_reason: str = ""
    exit_price: Optional[float] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "OpenTrade":
        return OpenTrade(**d)


def new_trade(side: int, entry: float, atr_signal: float, p: StrategyParams) -> OpenTrade:
    risk = p.stop_atr * atr_signal
    stop = entry - side * risk
    tp1 = entry + side * p.tp1_r * risk
    return OpenTrade(side=side, entry=entry, init_stop=stop, stop=stop, risk=risk, tp1=tp1,
                     extreme=entry, worst=entry)


def _close(t: OpenTrade, price: float, reason: str) -> OpenTrade:
    t.realized_r += t.remaining * t.side * (price - t.entry) / t.risk
    t.remaining = 0.0
    t.done = True
    t.exit_reason = reason
    t.exit_price = price
    return t


def step(t: OpenTrade, o: float, h: float, l: float, c: float, atr_now: float, p: StrategyParams) -> OpenTrade:
    """Tek bir KAPANMIŞ mumu işler. Giriş mumu da dahil her mum için çağrılır."""
    if t.done:
        return t
    s = t.side
    t.bars += 1

    # 1) Boşluk: açılış stopun ötesinde.
    if (o - t.stop) * s <= 0:
        return _close(t, o, "STOP_GAP")

    # 2) Stop (aynı mumdaki hedeften ÖNCE değerlendirilir).
    stop_hit = (l <= t.stop) if s > 0 else (h >= t.stop)
    if stop_hit:
        return _close(t, t.stop, "STOP" if t.stop == t.init_stop else "TRAIL")

    # 3) TP1.
    if not t.tp1_done:
        tp_hit = (h >= t.tp1) if s > 0 else (l <= t.tp1)
        if tp_hit:
            t.realized_r += p.tp1_frac * s * (t.tp1 - t.entry) / t.risk
            t.remaining -= p.tp1_frac
            t.tp1_done = True
            if p.be_after_tp1:
                t.stop = max(t.stop, t.entry) if s > 0 else min(t.stop, t.entry)
                be_hit = (l <= t.stop) if s > 0 else (h >= t.stop)
                if be_hit:  # sıra bilinmiyor -> kötümser: girişte çık
                    return _close(t, t.stop, "BE_AFTER_TP1")

    # 4) Uç noktalar (MFE/MAE).
    if s > 0:
        t.extreme = max(t.extreme, h)
        t.worst = min(t.worst, l)
    else:
        t.extreme = min(t.extreme, l)
        t.worst = max(t.worst, h)
    t.mfe_pct = max(t.mfe_pct, s * (t.extreme - t.entry) / t.entry * 100.0)
    t.mae_pct = max(t.mae_pct, -s * (t.worst - t.entry) / t.entry * 100.0)

    # 5) İz süren stop (bir sonraki mumdan itibaren).
    if math.isfinite(atr_now):
        trail = t.extreme - s * p.trail_atr * atr_now
        t.stop = max(t.stop, trail) if s > 0 else min(t.stop, trail)

    # 6) Zaman stopu.
    if t.bars >= p.time_stop_bars:
        return _close(t, c, "TIME")
    return t
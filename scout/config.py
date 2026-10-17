"""Tüm ayarlar tek yerde. Hiçbir sihirli sayı başka bir dosyada yaşamaz."""
from __future__ import annotations

import os
from dataclasses import dataclass, fields, replace


@dataclass(frozen=True)
class StrategyParams:
    """Volatilite-genişlemesi kırılım stratejisi (1 saatlik KAPANMIŞ mumlar)."""

    # --- giriş: sıkışmadan sonra hacimli, güçlü kırılım ---
    donchian_n: int = 48             # kaç saatlik tepe/dip kırılmalı (48 = 2 gün)
    atr_n: int = 14
    atr_base_n: int = 120            # "normal" volatilite tabanı (medyan ATR)
    pre_bars: int = 12               # kırılımdan önceki kaç mumun ATR ortalaması
    max_pre_atr_ratio: float = 1.15  # önceki volatilite normalin bu katından fazlaysa hareket zaten patlamış
    vol_lookback: int = 24
    min_vol_mult: float = 2.0        # kırılım mumu hacmi / önceki 24 mumun medyan hacmi
    min_range_atr: float = 1.3       # kırılım mumunun boyu / önceki ATR
    min_body_ratio: float = 0.5      # gövde / mum boyu (uzun fitil = red)
    min_close_loc: float = 0.7       # LONG: mumun üst %30'unda kapanmalı (SHORT'ta ayna)
    max_chase_atr: float = 1.5       # kırılım seviyesinin en fazla bu kadar ATR ötesinde kapanmış olmalı
    max_run_24h: float = 0.25        # son 24 saatte yön yönünde zaten bu kadar koşmuşsa girme
    min_atr_pct: float = 0.010       # 1s ATR / fiyat tabanı: coin hareket edebilecek kadar oynak olmalı
    use_btc_filter: bool = False     # LONG'u BTC düşüşünde / SHORT'u BTC yükselişinde engelle

    # --- risk ve çıkış ---
    stop_atr: float = 2.0            # başlangıç stopu = giriş -/+ stop_atr x ATR
    max_stop_pct: float = 0.06       # stop mesafesi fiyatın %12'sinden büyükse sinyal yok
    tp1_r: float = 1.5               # TP1 = +1.5R
    tp1_frac: float = 0.33           # TP1'de pozisyonun bu kadarı kapanır
    be_after_tp1: bool = True        # TP1'den sonra stop girişe çekilir
    trail_atr: float = 3.0           # kalan pozisyon: en uç fiyattan 3xATR geride iz süren stop
    time_stop_bars: int = 72         # 72 saat sonra kapat
    cooldown_bars: int = 12          # zararla biten işlemden sonra aynı coine bu kadar mum girme


@dataclass(frozen=True)
class Costs:
    """Gidiş-dönüş maliyet. Funding hesaba katılmaz (kısa vadede küçük)."""

    fee_per_side: float = 0.0006        # taker komisyon (tek yön)
    slippage_per_side: float = 0.0008   # kayma tahmini (tek yön); az likit coinlerde daha yüksek olabilir

    @property
    def round_trip(self) -> float:
        return 2.0 * (self.fee_per_side + self.slippage_per_side)


@dataclass(frozen=True)
class LiveConfig:
    timeframe: str = "1h"
    min_quote_volume: float = 1_500_000.0   # 24s USDT hacmi tabanı (likidite)
    history_bars: int = 1000
    max_alerts_per_run: int = 5
    alert_cooldown_hours: int = 24          # aynı coin+yön için tekrar bildirim aralığı
    max_price_drift_atr: float = 0.50       # lehe kaçmışsa giriş kovalanmaz
    max_adverse_drift_atr: float = 0.35    # sinyalden ters yönde fazla uzaklaştıysa iptal
    workers: int = 3
    min_request_interval: float = 0.12


# Kripto olmayan / anlamsız semboller: hisse, emtia, stabil coinler.
EXCLUDE_BASE_EXACT = {
    "USDC", "USDT", "BUSD", "TUSD", "FDUSD", "DAI", "USDE", "USDD", "EUR", "USD",
    "XAU", "XAG", "XAUT", "PAXG", "UKOIL", "USOIL", "WTI", "BRENT",
}
EXCLUDE_BASE_SUFFIX = ("STOCK", "3L", "3S", "5L", "5S")


def _env_override(obj, prefix: str):
    """<PREFIX><ALAN> ortam değişkenleriyle dataclass alanlarını ezer (tip korunur)."""
    changes = {}
    for f in fields(obj):
        raw = os.getenv(f"{prefix}{f.name.upper()}")
        if raw is None or raw == "":
            continue
        cur = getattr(obj, f.name)
        if isinstance(cur, bool):
            changes[f.name] = raw.strip().lower() in ("1", "true", "yes", "evet")
        elif isinstance(cur, int):
            changes[f.name] = int(float(raw))
        else:
            changes[f.name] = float(raw)
    return replace(obj, **changes) if changes else obj


def strategy_from_env() -> StrategyParams:
    return _env_override(StrategyParams(), "SCOUT_")


def costs_from_env() -> Costs:
    return _env_override(Costs(), "SCOUT_COST_")


def live_from_env() -> LiveConfig:
    return _env_override(LiveConfig(), "SCOUT_LIVE_")


def is_excluded_base(base: str) -> bool:
    b = base.upper()
    return b in EXCLUDE_BASE_EXACT or b.endswith(EXCLUDE_BASE_SUFFIX)
"""Early-warning setup scoring on closed daily candles; this is not an entry signal."""
from __future__ import annotations

import numpy as np
import pandas as pd


def _trend(close: pd.Series, n: int = 50) -> str:
    if len(close) < n + 3:
        return "YETERSİZ VERİ"
    ema = close.ewm(span=n, adjust=False).mean().iloc[-1]
    dev = float(close.iloc[-1] / ema - 1.0)
    return "YUKARI" if dev > 0.003 else "AŞAĞI" if dev < -0.003 else "YATAY"


def score_daily_setup(df: pd.DataFrame) -> dict | None:
    """Return a scored pre-breakout watch candidate from daily OHLCV.

    Scores describe chart conditions, not a probability or prediction. Requires >=90 daily
    candles so 20/60-day structure and a long-term context are not inferred from a tiny sample.
    """
    required = {"ts", "open", "high", "low", "close", "volume"}
    if not required.issubset(df.columns) or len(df) < 90:
        return None
    d = df.sort_values("ts").drop_duplicates("ts").tail(400).reset_index(drop=True)
    close = d["close"].astype(float)
    high = d["high"].astype(float)
    low = d["low"].astype(float)
    volume = d["volume"].astype(float)
    if (close <= 0).any() or not np.isfinite(close.to_numpy()).all():
        return None

    c = float(close.iloc[-1])
    hi20, lo20 = float(high.iloc[-20:].max()), float(low.iloc[-20:].min())
    hi60, lo60 = float(high.iloc[-60:].max()), float(low.iloc[-60:].min())
    width20 = (hi20 - lo20) / c
    width60 = (hi60 - lo60) / c
    if width60 <= 0:
        return None
    compression = width20 / width60
    near_high = c >= hi20 * 0.965
    near_low = c <= lo20 * 1.035
    higher_lows = float(low.iloc[-5:].min()) > float(low.iloc[-10:-5].min())
    lower_highs = float(high.iloc[-5:].max()) < float(high.iloc[-10:-5].max())
    vol_base = float(volume.iloc[-20:-1].median())
    vol_ratio = float(volume.iloc[-5:].median() / vol_base) if vol_base > 0 else 1.0
    def horizon_return(days: int) -> str:
        if len(close) <= days:
            return "YETERSİZ"
        pct = float(close.iloc[-1] / close.iloc[-1-days] - 1.0) * 100.0
        return f"{pct:+.1f}%"
    trend_1w = horizon_return(7)
    trend_1m = horizon_return(30)
    trend_3m = horizon_return(90)
    trend_1y = horizon_return(365)
    trend_3m_state = _trend(close, 60)

    options = []
    # Long setup: a compressed base near resistance with higher lows and volume starting to build.
    long_score = 0.0
    long_reasons = []
    if compression <= 0.60:
        long_score += 1.5; long_reasons.append("20g aralık, 60g aralığa göre sıkışmış")
    if near_high:
        long_score += 1.5; long_reasons.append("20g direncine yakın")
    if higher_lows:
        long_score += 1.0; long_reasons.append("kısa vadeli dipler yükseliyor")
    if vol_ratio >= 1.15:
        long_score += 1.0; long_reasons.append(f"son 5g hacim tabanı ×{vol_ratio:.2f}")
    if trend_3m_state == "YUKARI":
        long_score += 0.5; long_reasons.append("3 aylık eğilim yukarı")
    if near_high and compression <= 0.85:
        options.append((long_score, "LONG", long_reasons, hi20, lo20))

    # Short setup is symmetric: compression near support, lower highs and/or building volume.
    short_score = 0.0
    short_reasons = []
    if compression <= 0.60:
        short_score += 1.5; short_reasons.append("20g aralık, 60g aralığa göre sıkışmış")
    if near_low:
        short_score += 1.5; short_reasons.append("20g desteğine yakın")
    if lower_highs:
        short_score += 1.0; short_reasons.append("kısa vadeli tepeler alçalıyor")
    if vol_ratio >= 1.15:
        short_score += 1.0; short_reasons.append(f"son 5g hacim tabanı ×{vol_ratio:.2f}")
    if trend_3m_state == "AŞAĞI":
        short_score += 0.5; short_reasons.append("3 aylık eğilim aşağı")
    if near_low and compression <= 0.85:
        options.append((short_score, "SHORT", short_reasons, hi20, lo20))

    if not options:
        return None
    score, side, reasons, resistance, support = max(options, key=lambda x: x[0])
    if score < 3.0:
        return None
    return {
        "side": side, "score": round(float(score), 2), "close": c,
        "support": support, "resistance": resistance, "compression": round(compression, 3),
        "vol_ratio": round(vol_ratio, 2), "trend_1w": trend_1w,
        "trend_1m": trend_1m, "trend_3m": trend_3m, "trend_1y": trend_1y, "reasons": reasons,
    }



def format_early_candidate(symbol: str, c: dict, btc_context: dict, eth_context: dict) -> str:
    """Human-readable watchlist alert; explicitly not an instruction to enter."""
    direction = "YUKARI ADAYI" if c["side"] == "LONG" else "AŞAĞI ADAYI"
    reasons = "\n".join(f"• {reason}" for reason in c["reasons"]) or "• Sınırlı yapısal ipucu"
    return (
        f"🔎 ERKEN ADAY · {symbol} · {direction}\n"
        f"Yapı puanı: {c['score']:.1f}/5.5 (olasılık/kârlılık yüzdesi değildir)\n"
        f"Fiyat: {c['close']:.6g}\n"
        f"20g destek/direnç: {c['support']:.6g} / {c['resistance']:.6g}\n"
        f"Sıkışma oranı (20g/60g): {c['compression']:.2f} · Hacim tabanı: ×{c['vol_ratio']:.2f}\n"
        f"Getiri bağlamı: 1h {c['trend_1w']} · 1a {c['trend_1m']} · 3a {c['trend_3m']} · 1y {c['trend_1y']}\n"
        f"BTC: 1s {btc_context.get('btc_1h', 'NA')} / 4s {btc_context.get('btc_4h', 'NA')} / 1g {btc_context.get('btc_1d', 'NA')}\n"
        f"ETH: 1s {eth_context.get('eth_1h', 'NA')} / 4s {eth_context.get('eth_4h', 'NA')}\n"
        f"İpuçları:\n{reasons}\n"
        f"İptal seviyesi: {'20g desteği altı' if c['side'] == 'LONG' else '20g direnci üstü'} "
        f"({c['support'] if c['side'] == 'LONG' else c['resistance']:.6g})\n"
        "⚠️ ERKEN İZLEME LİSTESİDİR, giriş sinyali değildir. Kırılım/kapanış ve risk kontrolü beklenmeli."
    )

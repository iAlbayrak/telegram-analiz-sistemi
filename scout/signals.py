"""Sinyal adayı oluşturma, mesaj biçimlendirme ve tekrar-bildirim engeli (saf Python, ağ yok)."""
from __future__ import annotations

from typing import Dict, List

import pandas as pd

from .config import LiveConfig, StrategyParams

TREND_TR = {"BULLISH": "▲ yükseliş", "BEARISH": "▼ düşüş", "NEUTRAL": "▬ yatay"}


def _fp(x: float) -> str:
    return f"{x:.6g}"


def candidate_from_row(symbol: str, d: pd.DataFrame, i: int, side: int, strength: float,
                       p: StrategyParams, cfg: LiveConfig) -> dict:
    row = d.iloc[i]
    close = float(row["close"])
    atr_sig = float(row["atr"])
    risk = p.stop_atr * atr_sig
    return {
        "symbol": symbol,
        "side": "LONG" if side > 0 else "SHORT",
        "signal_ts": int(row["ts"]),
        "close": close,
        "atr": atr_sig,
        "atr_pct": float(row["atr_pct"]),
        "level": float(row["don_hi"] if side > 0 else row["don_lo"]),
        "stop": close - side * risk,
        "stop_pct": risk / close * 100.0,
        "tp1": close + side * p.tp1_r * risk,
        "tp1_pct": p.tp1_r * risk / close * 100.0,
        "entry_limit": close + side * cfg.max_price_drift_atr * atr_sig,
        "ref10": close * (1.0 + side * 0.10),
        "vol_mult": float(row["vol_mult"]),
        "range_atr": float(row["range_atr"]),
        "strength": float(strength),
    }


def format_one(c: dict, p: StrategyParams) -> str:
    icon = "🟢" if c["side"] == "LONG" else "🔴"
    way = "tepesini" if c["side"] == "LONG" else "dibini"
    sgn = "−" if c["side"] == "LONG" else "+"
    lines = [
        f"{icon} {c['side']} · {c['symbol']}",
        f"1s mum {p.donchian_n} saatlik {way} hacimle kırdı (hacim ×{c['vol_mult']:.1f}, mum {c['range_atr']:.1f} ATR, 1s ATR %{c['atr_pct']*100:.1f})",
        f"Referans giriş: {_fp(c['close'])}  (en fazla {_fp(c['entry_limit'])} civarı; daha uzaktaysa bekle)",
        f"Stop: {_fp(c['stop'])}  ({sgn}%{c['stop_pct']:.1f})",
        f"TP1: {_fp(c['tp1'])}  ({'+' if c['side']=='LONG' else '−'}%{c['tp1_pct']:.1f}) → pozisyonun {int(round(p.tp1_frac*100))}%'ini kapat"
        + (", stopu girişe çek" if p.be_after_tp1 else ""),
        f"Kalan: iz süren stop ({p.trail_atr:g}×ATR). Sabit üst hedef yok; fiyat gittikçe stop da onu izler.",
        f"Referans: %10 hareket = {_fp(c['ref10'])}",
    ]
    return "\n".join(lines)


def format_message(cands: List[dict], ctx: Dict[str, str], p: StrategyParams, extra: int = 0) -> str:
    head = (f"BTC 1s: {TREND_TR.get(ctx.get('btc_1h'), '?')} | BTC 4s: {TREND_TR.get(ctx.get('btc_4h'), '?')}")
    body = "\n\n".join(format_one(c, p) for c in cands)
    note = (f"\n\n(+{extra} aday daha vardı; sinyal gücüne göre ilk {len(cands)} gösterildi. "
            f"Çok sayıda aynı anda sinyal genelde tek bir piyasa hareketidir: toplam riski sınırlayın.)") if extra > 0 else ""
    foot = "Yatırım tavsiyesi değildir. Kaldıraçta tüm teminat kaybedilebilir; stopsuz pozisyon açmayın."
    return f"{head}\n\n{body}{note}\n\n{foot}"


def should_alert(state: dict, c: dict, cooldown_ms: int) -> bool:
    last = state.get("alerts", {}).get(f"{c['symbol']}:{c['side']}")
    return last is None or c["signal_ts"] - int(last) >= cooldown_ms


def record_alert(state: dict, c: dict) -> None:
    state.setdefault("alerts", {})[f"{c['symbol']}:{c['side']}"] = int(c["signal_ts"])
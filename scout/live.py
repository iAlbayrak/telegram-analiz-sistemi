"""Canlı tarama: kapanmış 1 saatlik mumlar -> strateji -> tekrar-engeli -> Telegram -> ileriye dönük takip.

Hiçbir emir göndermez, API anahtarı gerektirmez (yalnızca herkese açık piyasa verisi).
Saatte bir (mum kapanışından birkaç dakika sonra) çalıştırılması yeterlidir.
"""
from __future__ import annotations

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from .config import costs_from_env, live_from_env, strategy_from_env
from .indicators import atr, btc_regime_series, prepare, resample_4h, trend_state
from .early import format_early_candidate, score_daily_setup
from .notify import send_telegram
from .signals import candidate_from_row, format_message, record_alert, should_alert
from .strategy import compute_signals
from .tracker import advance, new_record

TF = "1h"
TF_MS = 3_600_000


def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _save(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def _forward_stats(closed: list) -> str:
    if not closed:
        return "İleriye dönük takip: henüz sonuçlanmış sinyal yok."
    from .backtest import summarize
    s = summarize(pd.DataFrame(closed))
    return (f"İLERİYE DÖNÜK SONUÇ (canlı sinyaller): n={s['n']} | kazanma %{s['win_rate']:.0f} | "
            f"beklenti {s['avg_net_r']:+.3f}R (net) | toplam {s['total_net_r']:+.1f}R | "
            f"≥%10 hareket yakalama %{s['hit10']:.0f}")


def main() -> int:
    from . import data as dm

    p, costs, cfg = strategy_from_env(), costs_from_env(), live_from_env()
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN", ""), os.getenv("TELEGRAM_CHAT_ID", "")
    state_dir = Path(os.getenv("SCOUT_STATE_DIR", "state"))
    state = _load(state_dir / "alert_state.json", {"alerts": {}})
    early_state = _load(state_dir / "early_alert_state.json", {"alerts": {}})
    open_recs = _load(state_dir / "open_signals.json", [])
    closed_recs = _load(state_dir / "closed_signals.json", [])
    now_ms = int(time.time() * 1000)

    ex = dm.make_exchange()
    universe = dm.list_universe(ex, cfg.min_quote_volume)
    print(f"Evren: {len(universe)} likit USDT vadeli sözleşme (24s hacim >= {cfg.min_quote_volume:,.0f})", flush=True)

    # --- BTC bağlamı ---
    btc = dm.fetch_recent(ex, "BTC/USDT:USDT", TF, 1000)
    btc4 = resample_4h(btc)
    btc_dt = btc.copy()
    btc_dt["dt"] = pd.to_datetime(btc_dt["ts"], unit="ms", utc=True)
    btc1d = btc_dt.set_index("dt").resample("1D").agg({"close": "last"}).dropna()["close"]
    try:
        eth = dm.fetch_recent(ex, "ETH/USDT:USDT", TF, 1000)
        eth4 = resample_4h(eth)
        eth_1h = trend_state(eth["close"])
        eth_4h = trend_state(eth4["close"], n=20)
    except Exception as exc:
        # ETH context is useful but must never disable the entire scan if its feed is unavailable.
        print(f"ETH bağlamı alınamadı: {type(exc).__name__}")
        eth_1h = eth_4h = "NEUTRAL"
    ctx = {
        "btc_1h": trend_state(btc["close"]), "btc_4h": trend_state(btc4["close"], n=20),
        "btc_1d": trend_state(btc1d, n=20), "eth_1h": eth_1h, "eth_4h": eth_4h,
    }
    reg_last = int(btc_regime_series(btc).iloc[-1]) if p.use_btc_filter else 0
    print(f"BTC 1s={ctx['btc_1h']} 4s={ctx['btc_4h']}", flush=True)

    # --- Erken aday taraması: en likit sözleşmelerde günlük baz oluşumu ---
    early_hits = []
    early_cooldown_ms = cfg.early_alert_cooldown_hours * 3_600_000
    for sym, _quote_volume in universe[:cfg.early_scan_symbols]:
        try:
            daily = dm.fetch_recent(ex, sym, "1d", 400)
            if len(daily) < 90:
                continue
            if now_ms - (int(daily["ts"].iloc[-1]) + 86_400_000) > 3 * 86_400_000:
                continue
            setup = score_daily_setup(daily)
            if not setup:
                continue
            key = f"{sym}:{setup['side']}"
            last = int(early_state.get("alerts", {}).get(key, 0))
            if now_ms - last < early_cooldown_ms:
                continue
            early_hits.append({"symbol": sym, **setup, "key": key})
        except Exception as exc:
            print(f"  erken tarama hatası {sym}: {type(exc).__name__}: {str(exc)[:80]}")
    early_hits.sort(key=lambda x: x["score"], reverse=True)
    early_hits = early_hits[:3]
    if early_hits:
        early_messages = [
            format_early_candidate(x["symbol"], x, ctx, ctx)
            for x in early_hits
        ]
        early_msg = "📡 ERKEN UYARI — GİRİŞ SİNYALİ DEĞİL\n\n" + "\n\n".join(early_messages)
        early_ok = (send_telegram(early_msg, token, chat) if (token and chat)
                    else (print("TELEGRAM AYARLI DEĞİL — erken aday mesajı:\n" + early_msg) or True))
        if early_ok:
            for x in early_hits:
                early_state.setdefault("alerts", {})[x["key"]] = now_ms
            print(f"{len(early_hits)} erken aday bildirimi işlendi.", flush=True)
    else:
        print("Erken tarama: yeni uygun aday yok.", flush=True)

    skipped = {"kısa_veri": 0, "bayat": 0, "hata": 0}

    def analyze(sym: str):
        try:
            df = dm.fetch_recent(dm.thread_exchange(), sym, TF, cfg.history_bars)
            if len(df) < 200:
                skipped["kısa_veri"] += 1
                return None
            if now_ms - (int(df["ts"].iloc[-1]) + TF_MS) > 2 * TF_MS:   # işlem görmüyor / veri bayat
                skipped["bayat"] += 1
                return None
            d = prepare(df, p)
            sig = compute_signals(d, p, np.full(len(d), reg_last) if p.use_btc_filter else None)
            i = len(d) - 1
            d4 = resample_4h(df)
            dt = df.copy()
            dt["dt"] = pd.to_datetime(dt["ts"], unit="ms", utc=True)
            d1 = dt.set_index("dt").resample("1D").agg({"close": "last"}).dropna()["close"]
            if sig["long"].iloc[i]:
                c = candidate_from_row(sym, d, i, +1, sig["strength"].iloc[i], p, cfg)
            elif sig["short"].iloc[i]:
                c = candidate_from_row(sym, d, i, -1, sig["strength"].iloc[i], p, cfg)
            else:
                return None
            c["trend_4h"] = trend_state(d4["close"], n=20)
            c["trend_1d"] = trend_state(d1, n=20)
            return c
        except Exception as exc:
            skipped["hata"] += 1
            print(f"  hata {sym}: {type(exc).__name__}: {str(exc)[:100]}")
        return None

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=cfg.workers) as pool:
        results = [r for r in pool.map(analyze, [s for s, _ in universe]) if r]
    print(f"Tarama {time.time()-t0:.0f}s | aday: {len(results)} | atlanan: {skipped}", flush=True)

    # --- tekrar engeli + canlı fiyat kontrolü ---
    cooldown_ms = cfg.alert_cooldown_hours * 3_600_000
    fresh = [c for c in results if should_alert(state, c, cooldown_ms)]
    fresh.sort(key=lambda c: c["strength"], reverse=True)
    sendable = []
    unchecked = 0
    for idx, c in enumerate(fresh):
        if len(sendable) >= cfg.max_alerts_per_run:
            unchecked = len(fresh) - idx      # sınır aşıldı: kalanlar kullanıcıya SAYI olarak bildirilir
            break
        try:
            live = dm.fetch_last_price(ex, c["symbol"])
        except Exception as exc:
            print(f"  canlı fiyat alınamadı {c['symbol']}: {type(exc).__name__}")
            continue
        side = 1 if c["side"] == "LONG" else -1
        drift_atr = side * (live - c["close"]) / c["atr"]
        if c["stop_pct"] / 100.0 > p.max_stop_pct:
            print(f"  ELENDİ {c['symbol']} {c['side']}: stop mesafesi %{c['stop_pct']:.2f}, izin verilen %{p.max_stop_pct*100:.1f}")
            continue
        if drift_atr > cfg.max_price_drift_atr:
            print(f"  ELENDİ {c['symbol']} {c['side']}: fiyat kaçtı ({drift_atr:.2f} ATR lehe)")
            continue
        if drift_atr < -cfg.max_adverse_drift_atr:
            print(f"  ELENDİ {c['symbol']} {c['side']}: sinyal sonrası ters hareket ({drift_atr:.2f} ATR)")
            continue
        if side * (live - c["stop"]) <= 0:
            print(f"  ELENDİ {c['symbol']} {c['side']}: fiyat zaten stopun ötesinde")
            continue
        sendable.append(c)

    if sendable:
        msg = format_message(sendable, ctx, p, extra=unchecked)
        ok = send_telegram(msg, token, chat) if (token and chat) else (print("TELEGRAM AYARLI DEĞİL — mesaj:\n" + msg) or True)
        if ok:
            for c in sendable:
                record_alert(state, c)
                open_recs.append(new_record(c))
            print(f"{len(sendable)} sinyal gönderildi.", flush=True)
        else:
            print("Telegram gönderilemedi; sinyaller bir sonraki turda yeniden denenecek.", flush=True)
    else:
        print("Bu turda gönderilecek yeni sinyal yok.", flush=True)

    # --- ileriye dönük takip ---
    still_open = []
    ex_t = dm.make_exchange()
    for rec in open_recs:
        try:
            df = dm.fetch_recent(ex_t, rec["symbol"], TF, cfg.history_bars)
            d = df.copy()
            d["atr"] = atr(d, p.atr_n)
            rec = advance(rec, d, p, costs)
        except Exception as exc:
            print(f"  takip hatası {rec['symbol']}: {type(exc).__name__}")
        if rec["status"] == "CLOSED":
            closed_recs.append(rec)
        elif rec["status"] in ("PENDING", "OPEN"):
            still_open.append(rec)
        # SKIPPED kayıtları atılır
    print(_forward_stats(closed_recs), flush=True)

    horizon = now_ms - 7 * 86_400_000          # 7 günden eski tekrar-engeli kayıtlarını temizle
    state["alerts"] = {k: v for k, v in state.get("alerts", {}).items() if int(v) >= horizon}
    _save(state_dir / "alert_state.json", state)
    _save(state_dir / "early_alert_state.json", early_state)
    _save(state_dir / "open_signals.json", still_open)
    _save(state_dir / "closed_signals.json", closed_recs[-1000:])
    return 0


if __name__ == "__main__":
    sys.exit(main())
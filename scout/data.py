"""Borsa verisi (MEXC, ccxt). Ağ gerektiren TEK modül; strateji/backtest bunu içe aktarmaz."""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import List, Tuple

import pandas as pd

from .config import is_excluded_base

COLS = ["ts", "open", "high", "low", "close", "volume"]
TF_MS = {"15m": 900_000, "1h": 3_600_000, "4h": 14_400_000}
MIN_INTERVAL = float(os.getenv("SCOUT_MIN_REQUEST_INTERVAL", "0.12"))

_lock = threading.Lock()
_last_call = 0.0
_tls = threading.local()


def make_exchange():
    import ccxt
    return ccxt.mexc({"enableRateLimit": True, "options": {"defaultType": "swap"}})


def thread_exchange():
    """ccxt nesneleri thread-safe değildir: her thread kendi nesnesini kullanır."""
    ex = getattr(_tls, "ex", None)
    if ex is None:
        ex = make_exchange()
        _tls.ex = ex
    return ex


def paced(fn, *args, retries: int = 4, backoff: float = 1.5, **kwargs):
    """Tüm thread'ler için ortak hız sınırı + ağ hatalarında üstel geri çekilme."""
    import ccxt
    global _last_call
    for attempt in range(retries + 1):
        with _lock:
            wait = MIN_INTERVAL - (time.monotonic() - _last_call)
            if wait > 0:
                time.sleep(wait)
            _last_call = time.monotonic()
        try:
            return fn(*args, **kwargs)
        except ccxt.NetworkError:
            if attempt >= retries:
                raise
            time.sleep(backoff * (2 ** attempt))


def rows_to_df(rows) -> pd.DataFrame:
    df = pd.DataFrame(rows or [], columns=COLS)
    if df.empty:
        return df
    for c in COLS:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna().sort_values("ts").drop_duplicates("ts", keep="last")
    df["ts"] = df["ts"].astype("int64")
    return df.reset_index(drop=True)


def drop_forming(df: pd.DataFrame, tf: str, now_ms: int | None = None) -> pd.DataFrame:
    """Henüz kapanmamış son mumu at (strateji yalnızca KAPANMIŞ mumlarla çalışır)."""
    now_ms = now_ms or int(time.time() * 1000)
    return df[df["ts"] + TF_MS[tf] <= now_ms].reset_index(drop=True)


def list_universe(ex, min_quote_volume: float) -> List[Tuple[str, float]]:
    markets = ex.load_markets()
    tickers = paced(ex.fetch_tickers)
    out = []
    for sym, m in markets.items():
        if not (m.get("active") and m.get("swap") and m.get("quote") == "USDT"):
            continue
        if is_excluded_base(str(m.get("base") or "")):
            continue
        t = tickers.get(sym) or {}
        q = t.get("quoteVolume")
        if q is None and t.get("baseVolume") and t.get("last"):
            q = float(t["baseVolume"]) * float(t["last"])
        try:
            q = float(q or 0.0)
        except (TypeError, ValueError):
            q = 0.0
        if q >= min_quote_volume:
            out.append((sym, q))
    return sorted(out, key=lambda x: x[1], reverse=True)


def fetch_recent(ex, symbol: str, tf: str, limit: int) -> pd.DataFrame:
    rows = paced(ex.fetch_ohlcv, symbol, tf, limit=limit)
    return drop_forming(rows_to_df(rows), tf)


def fetch_last_price(ex, symbol: str) -> float:
    t = paced(ex.fetch_ticker, symbol)
    v = t.get("last") or ((t.get("bid") or 0) + (t.get("ask") or 0)) / 2.0
    return float(v)


def load_history(ex, symbol: str, tf: str, days: int, cache_dir: str = "data_cache") -> pd.DataFrame:
    """Sayfalayarak geçmiş mum indirir; CSV önbellekle artımlı güncellenir."""
    tfms = TF_MS[tf]
    now_ms = int(time.time() * 1000)
    start = now_ms - days * 86_400_000
    path = Path(cache_dir) / f"{symbol.replace('/', '_').replace(':', '_')}_{tf}.csv"
    cached = pd.read_csv(path) if path.exists() else pd.DataFrame(columns=COLS)
    since = int(cached["ts"].max()) + tfms if len(cached) else start
    rows = []
    while since < now_ms:
        chunk = paced(ex.fetch_ohlcv, symbol, tf, since=since, limit=1000)
        if not chunk:
            break
        rows += chunk
        nxt = int(chunk[-1][0]) + tfms
        if nxt <= since:
            break
        since = nxt
    df = pd.concat([cached, rows_to_df(rows)], ignore_index=True) if rows else cached
    df = rows_to_df(df.values.tolist()) if len(df) else df
    df = drop_forming(df, tf, now_ms)
    df = df[df["ts"] >= start].reset_index(drop=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return df
"""Sentetik piyasa üreticisi (testler ve --demo için). Gerçek veri DEĞİLDİR."""
from __future__ import annotations

import numpy as np
import pandas as pd

TS0 = 1_700_000_000_000
HOUR = 3_600_000


def make_series(n: int, seed: int, sigma: float = 0.012, price0: float = 1.0, plants=None, cont_len: int = 40,
                cont_drift: float = 0.35) -> pd.DataFrame:
    """Rastgele yürüyüş OHLCV. plants = [(bar_index, +1/-1), ...]: o mumda hacimli kırılım + devam eden sürüklenme.

    Hacim, |getiri| ile ilişkilidir (gerçek piyasa gibi); plant'siz seri hiçbir kenar (edge) içermez.
    """
    rng = np.random.default_rng(seed)
    plants = dict(plants or [])
    cont_until, cont_dir = -1, 0
    o = np.empty(n); h = np.empty(n); l = np.empty(n); c = np.empty(n); v = np.empty(n)
    prev = price0
    for i in range(n):
        s_i = sigma
        if any(0 < (k - i) <= 12 for k in plants):          # kırılım öncesi sıkışma
            s_i = sigma * 0.5
        r = rng.normal(0.0, s_i)
        vol_boost = 1.0
        if i in plants:
            d = plants[i]
            lo = max(0, i - 48)
            if d > 0:
                level = np.max(h[lo:i]) if i > lo else prev
                need = np.log(level * (1 + 0.4 * sigma) / prev)
                r = max(3.5 * sigma, need)
            else:
                level = np.min(l[lo:i]) if i > lo else prev
                need = np.log(level * (1 - 0.4 * sigma) / prev)
                r = min(-3.5 * sigma, need)
            vol_boost = 4.0
            cont_until, cont_dir = i + cont_len, d
        elif i <= cont_until and cont_dir != 0:
            r = cont_dir * cont_drift * sigma + rng.normal(0.0, sigma)
        op = prev
        cl = op * np.exp(r)
        w_up, w_dn = np.abs(rng.normal(0.0, sigma * 0.4, 2))
        if i in plants:
            w_up, w_dn = (w_up * 0.2, w_dn) if plants[i] > 0 else (w_up, w_dn * 0.2)
        hi = max(op, cl) * (1 + w_up)
        lo_ = min(op, cl) * (1 - w_dn)
        base_vol = 1000.0 * np.exp(rng.normal(0.0, 0.4)) * (1.0 + 2.0 * abs(r) / sigma)
        o[i], h[i], l[i], c[i], v[i] = op, hi, lo_, cl, base_vol * vol_boost
        prev = cl
    return pd.DataFrame({"ts": TS0 + np.arange(n) * HOUR, "open": o, "high": h, "low": l, "close": c, "volume": v})


def make_universe(n_sym: int = 30, bars: int = 3000, seed: int = 1, edge: bool = False):
    out = {}
    for j in range(n_sym):
        plants = None
        if edge:
            rng = np.random.default_rng(10_000 + seed * 100 + j)
            idxs = np.arange(250, bars - 60, 140) + rng.integers(0, 40, len(np.arange(250, bars - 60, 140)))
            plants = [(int(k), int(rng.choice([-1, 1]))) for k in idxs]
        out[f"S{j:02d}/USDT:USDT"] = make_series(bars, seed * 1000 + j, plants=plants)
    return out
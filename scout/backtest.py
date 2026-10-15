"""Backtest motoru: mum mum simülasyon, maliyetli, kötümser mum-içi sıralama.

Dürüstlük kuralları (rapora da yazılır):
  * Giriş: sinyal mumunu izleyen mumun AÇILIŞI. Sinyal, kapanmış mumla üretilir.
  * Maliyet: komisyon + kayma, gidiş-dönüş, R cinsine çevrilerek düşülür.
  * Güven aralığı GÜN-KÜMELİ bootstrap ile hesaplanır: aynı gün üretilen sinyaller
    birbirine bağımlıdır (hepsi aynı piyasa rejimini paylaşır); bağımsız sayılmaz.
  * Evren = bugünün likit coinleri (hayatta kalma yanlılığı var) -> sonuçlara şüpheyle bakın.
"""
from __future__ import annotations

import argparse
import itertools
import os
import sys
from dataclasses import replace
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional

import numpy as np
import pandas as pd

from .config import Costs, StrategyParams
from .exits import new_trade, step
from .indicators import btc_regime_series, prepare
from .strategy import compute_signals

MIN_BARS = 200


# --------------------------------------------------------------------------- simülasyon
def simulate_symbol(symbol: str, d: pd.DataFrame, sig: pd.DataFrame, p: StrategyParams, costs: Costs) -> List[dict]:
    ts = d["ts"].to_numpy()
    o = d["open"].to_numpy(float)
    h = d["high"].to_numpy(float)
    l = d["low"].to_numpy(float)
    c = d["close"].to_numpy(float)
    atr_a = d["atr"].to_numpy(float)
    longs = sig["long"].to_numpy(bool)
    shorts = sig["short"].to_numpy(bool)
    strength = sig["strength"].to_numpy(float)
    vol_mult = d["vol_mult"].to_numpy(float)
    range_atr = d["range_atr"].to_numpy(float)
    n = len(d)

    out: List[dict] = []
    free_from = 0
    for t in np.flatnonzero(longs | shorts):
        if t < free_from or t + 1 >= n:
            continue
        side = 1 if longs[t] else -1
        entry = o[t + 1]
        a = atr_a[t]
        if not (np.isfinite(entry) and np.isfinite(a) and a > 0 and entry > 0):
            continue
        if p.stop_atr * a / entry > p.max_stop_pct:
            continue
        tr = new_trade(side, float(entry), float(a), p)
        k = t + 1
        while k < n:
            step(tr, o[k], h[k], l[k], c[k], atr_a[k], p)
            if tr.done:
                break
            k += 1
        if not tr.done:
            break  # veri bitti, işlem sonuçlanmadı: istatistiğe katma
        cost_r = costs.round_trip * tr.entry / tr.risk
        net = tr.realized_r - cost_r
        out.append(
            dict(
                symbol=symbol, side="LONG" if side > 0 else "SHORT",
                signal_ts=int(ts[t]), entry_ts=int(ts[t + 1]), exit_ts=int(ts[k]),
                entry=tr.entry, stop=tr.init_stop, risk_pct=tr.risk / tr.entry * 100.0,
                gross_r=tr.realized_r, cost_r=cost_r, net_r=net,
                bars=tr.bars, reason=tr.exit_reason, tp1_done=tr.tp1_done,
                mfe_pct=tr.mfe_pct, mae_pct=tr.mae_pct,
                strength=float(strength[t]), vol_mult=float(vol_mult[t]), range_atr=float(range_atr[t]),
            )
        )
        free_from = k + (p.cooldown_bars if net < 0 else 0)
    return out


def run_backtest(
    data: Dict[str, pd.DataFrame], p: StrategyParams, costs: Costs, btc_df: Optional[pd.DataFrame] = None
) -> pd.DataFrame:
    btc_reg = btc_regime_series(btc_df) if (p.use_btc_filter and btc_df is not None) else None
    rows: List[dict] = []
    for sym, df in data.items():
        if len(df) < MIN_BARS:
            continue
        d = prepare(df, p)
        reg = None
        if btc_reg is not None:
            reg = btc_reg.reindex(d["ts"].to_numpy(), method="ffill").fillna(0).to_numpy()
        sig = compute_signals(d, p, reg)
        rows.extend(simulate_symbol(sym, d, sig, p, costs))
    cols = ["symbol", "side", "signal_ts", "entry_ts", "exit_ts", "entry", "stop", "risk_pct", "gross_r",
            "cost_r", "net_r", "bars", "reason", "tp1_done", "mfe_pct", "mae_pct", "strength", "vol_mult", "range_atr"]
    trades = pd.DataFrame(rows, columns=cols)
    if len(trades):
        trades = trades.sort_values("entry_ts").reset_index(drop=True)
    return trades


# --------------------------------------------------------------------------- istatistik
def _cluster_bootstrap_ci(values: np.ndarray, clusters: np.ndarray, n_boot: int = 3000, seed: int = 7):
    rng = np.random.default_rng(seed)
    uniq = np.unique(clusters)
    if len(uniq) < 2:
        return (float("nan"), float("nan"))
    groups = [values[clusters == u] for u in uniq]
    means = np.empty(n_boot)
    for i in range(n_boot):
        pick = rng.integers(0, len(groups), len(groups))
        means[i] = np.concatenate([groups[j] for j in pick]).mean()
    return float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))


def summarize(trades: pd.DataFrame) -> dict:
    if trades is None or len(trades) == 0:
        return {"n": 0}
    r = trades["net_r"].to_numpy(float)
    wins, losses = r[r > 0], r[r <= 0]
    days = (trades["entry_ts"].to_numpy() // 86_400_000)
    lo, hi = _cluster_bootstrap_ci(r, days)
    equity = np.cumsum(trades.sort_values("exit_ts")["net_r"].to_numpy(float))
    peak = np.maximum.accumulate(equity)
    return {
        "n": int(len(r)),
        "win_rate": float((r > 0).mean() * 100),
        "avg_net_r": float(r.mean()),
        "median_net_r": float(np.median(r)),
        "total_net_r": float(r.sum()),
        "avg_gross_r": float(trades["gross_r"].mean()),
        "avg_cost_r": float(trades["cost_r"].mean()),
        "profit_factor": float(wins.sum() / abs(losses.sum())) if losses.sum() < 0 else float("inf"),
        "avg_win_r": float(wins.mean()) if len(wins) else 0.0,
        "avg_loss_r": float(losses.mean()) if len(losses) else 0.0,
        "max_dd_r": float((peak - equity).max()),
        "avg_bars": float(trades["bars"].mean()),
        "hit5": float((trades["mfe_pct"] >= 5).mean() * 100),
        "hit10": float((trades["mfe_pct"] >= 10).mean() * 100),
        "hit20": float((trades["mfe_pct"] >= 20).mean() * 100),
        "ci_lo": lo, "ci_hi": hi,
        "n_days": int(len(np.unique(days))),
    }


def split_time(trades: pd.DataFrame, frac: float = 0.6):
    if len(trades) == 0:
        return trades, trades
    t0, t1 = trades["entry_ts"].min(), trades["entry_ts"].max()
    cut = t0 + frac * (t1 - t0)
    return trades[trades["entry_ts"] <= cut], trades[trades["entry_ts"] > cut]


def group_table(trades: pd.DataFrame, key: str, bins: Optional[Iterable[float]] = None, labels=None) -> pd.DataFrame:
    if len(trades) == 0:
        return pd.DataFrame()
    t = trades.copy()
    if bins is not None:
        t["grp"] = pd.cut(t[key], bins=list(bins), labels=labels, include_lowest=True)
    elif key == "month":
        t["grp"] = pd.to_datetime(t["entry_ts"], unit="ms", utc=True).dt.strftime("%Y-%m")
    elif key == "tercile":
        t["grp"] = pd.qcut(t["strength"], 3, labels=["düşük", "orta", "yüksek"], duplicates="drop")
    else:
        t["grp"] = t[key]
    g = t.groupby("grp", observed=True)
    return pd.DataFrame(
        {"n": g.size(), "kazanma_%": g["net_r"].apply(lambda s: (s > 0).mean() * 100),
         "ort_net_R": g["net_r"].mean(), "toplam_net_R": g["net_r"].sum(),
         "hit10_%": g["mfe_pct"].apply(lambda s: (s >= 10).mean() * 100)}
    ).round(2)


# --------------------------------------------------------------------------- grid
def grid_search(data, base: StrategyParams, costs: Costs, grid: Dict[str, list], btc_df=None, top_k: int = 10):
    names = list(grid)
    results = []
    for combo in itertools.product(*[grid[k] for k in names]):
        p = replace(base, **dict(zip(names, combo)))
        tr = run_backtest(data, p, costs, btc_df)
        is_tr, oos_tr = split_time(tr)
        a, b = summarize(is_tr), summarize(oos_tr)
        results.append({**dict(zip(names, combo)),
                        "n_is": a.get("n", 0), "R_is": a.get("avg_net_r", np.nan),
                        "n_oos": b.get("n", 0), "R_oos": b.get("avg_net_r", np.nan),
                        "hit10_oos": b.get("hit10", np.nan)})
    df = pd.DataFrame(results)
    return df


# --------------------------------------------------------------------------- rapor
def _fmt(x, nd=3):
    return "n/a" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x:.{nd}f}"


def format_report(trades, p: StrategyParams, costs: Costs, meta: dict, grid_df: Optional[pd.DataFrame] = None) -> str:
    s = summarize(trades)
    L: List[str] = []
    L.append(f"# Backtest raporu — {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC\n")
    L.append("## Kurulum")
    for k, v in meta.items():
        L.append(f"- {k}: {v}")
    L.append(f"- Maliyet (gidiş-dönüş): %{costs.round_trip*100:.2f} (komisyon {costs.fee_per_side*100:.3f}% + kayma {costs.slippage_per_side*100:.3f}% / yön)")
    L.append("- Parametreler: " + ", ".join(f"{k}={v}" for k, v in p.__dict__.items()))
    L.append("")
    if s["n"] == 0:
        L.append("**Hiç işlem üretilmedi.** Filtreler çok sıkı olabilir ya da veri yetersiz.")
        return "\n".join(L)
    L.append("## Genel sonuç (maliyetler düşülmüş)")
    L.append(f"- İşlem: **{s['n']}** ({s['n_days']} farklı günde)")
    L.append(f"- Kazanma oranı: %{s['win_rate']:.1f} | Ort. kazanç {s['avg_win_r']:+.2f}R | Ort. kayıp {s['avg_loss_r']:+.2f}R")
    L.append(f"- **Beklenti (işlem başına net R): {s['avg_net_r']:+.3f}** | medyan {s['median_net_r']:+.3f} | toplam {s['total_net_r']:+.1f}R")
    L.append(f"- %95 güven aralığı (gün-kümeli bootstrap): [{_fmt(s['ci_lo'])}, {_fmt(s['ci_hi'])}]")
    L.append(f"- Brüt R {s['avg_gross_r']:+.3f}, maliyet {s['avg_cost_r']:.3f}R/işlem | Profit factor {_fmt(s['profit_factor'],2)} | Maks. düşüş {s['max_dd_r']:.1f}R")
    L.append(f"- Ortalama süre {s['avg_bars']:.1f} saat")
    L.append(f"- **Stoplanmadan önce hedefe ulaşan oran (en iyi lehe hareket):** >=%5: %{s['hit5']:.1f} | >=%10: %{s['hit10']:.1f} | >=%20: %{s['hit20']:.1f}")
    verdict = ("İstatistiksel kanıt VAR (alt sınır > 0)." if s["ci_lo"] > 0 else
               "İstatistiksel kanıt YOK (alt sınır <= 0): sonuç şans olabilir.")
    L.append(f"- **Yorum:** {verdict}")
    L.append("")
    is_tr, oos_tr = split_time(trades)
    a, b = summarize(is_tr), summarize(oos_tr)
    L.append("## Zaman bölmesi (ilk %60 / son %40) — tutarlılık testi")
    L.append(f"- İlk %60: n={a.get('n',0)}, beklenti {_fmt(a.get('avg_net_r'))}R")
    L.append(f"- Son %40: n={b.get('n',0)}, beklenti {_fmt(b.get('avg_net_r'))}R")
    L.append("- İki dönem aynı işaretteyse ve benzer büyüklükteyse sonuç daha güvenilirdir.\n")
    for title, tbl in [
        ("Yöne göre", group_table(trades, "side")),
        ("Aya göre", group_table(trades, "month")),
        ("Çıkış nedenine göre", group_table(trades, "reason")),
        ("Güç ölçüsüne göre (hacim x mum boyu)", group_table(trades, "tercile")),
        ("Stop mesafesine göre (%)", group_table(trades, "risk_pct", bins=[0, 2, 3, 4, 6, 100])),
    ]:
        if len(tbl):
            L.append(f"## {title}\n")
            L.append(tbl.to_markdown() if _has_tabulate() else "```\n" + tbl.to_string() + "\n```")
            L.append("")
    if grid_df is not None and len(grid_df):
        L.append("## Parametre dayanıklılık taraması\n")
        L.append("Tepeyi değil PLATOYU arayın: komşu değerler de iyiyse parametre sağlamdır. Tek bir parlak hücre büyük olasılıkla şanstır.\n")
        L.append(f"- Tarama hücre sayısı: {len(grid_df)} | OOS beklentisi > 0 olan hücre oranı: %{(grid_df['R_oos'] > 0).mean()*100:.0f} | OOS medyan: {grid_df['R_oos'].median():+.3f}R")
        top = grid_df.sort_values("R_is", ascending=False).head(10).round(3)
        L.append("\nİlk %60'a göre en iyi 10 hücre ve son %40 karşılıkları:\n")
        L.append("```\n" + top.to_string(index=False) + "\n```")
    return "\n".join(L)


def _has_tabulate() -> bool:
    try:
        import tabulate  # noqa: F401
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------- CLI
def _demo_data(n_sym=40, bars=3000, seed=1):
    from tests.helpers import make_universe
    return make_universe(n_sym, bars, seed)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Scout backtest")
    ap.add_argument("--days", type=int, default=120)
    ap.add_argument("--top", type=int, default=150, help="hacme göre ilk N sembol")
    ap.add_argument("--min-volume", type=float, default=1_500_000)
    ap.add_argument("--grid", action="store_true")
    ap.add_argument("--btc-filter", action="store_true")
    ap.add_argument("--out", default="reports")
    ap.add_argument("--demo", action="store_true", help="sentetik veriyle çalıştır (ağ gerekmez)")
    args = ap.parse_args(argv)

    from .config import costs_from_env, strategy_from_env
    p = strategy_from_env()
    if args.btc_filter:
        p = replace(p, use_btc_filter=True)
    costs = costs_from_env()

    if args.demo:
        data = _demo_data()
        btc = next(iter(data.values()))
        meta = {"Veri": "SENTETİK rastgele yürüyüş (gerçek piyasa değil) — yalnızca boru hattı denemesi", "Sembol": len(data)}
    else:
        from . import data as dm
        ex = dm.make_exchange()
        uni = dm.list_universe(ex, args.min_volume)[: args.top]
        print(f"Evren: {len(uni)} sembol; {args.days} gün 1s veri indiriliyor...", flush=True)
        data = {}
        for i, (sym, qv) in enumerate(uni, 1):
            try:
                data[sym] = dm.load_history(ex, sym, "1h", args.days)
            except Exception as e:  # tek sembol patlarsa devam
                print(f"  atlandı {sym}: {type(e).__name__}: {str(e)[:80]}")
            if i % 25 == 0:
                print(f"  ... {i}/{len(uni)}", flush=True)
        btc = dm.load_history(ex, "BTC/USDT:USDT", "1h", args.days)
        meta = {"Veri": f"MEXC USDT vadeli, 1 saatlik, son {args.days} gün", "Sembol": len(data),
                "Evren": f"24s hacmi >= {args.min_volume:,.0f} USDT, hacme göre ilk {args.top} (bugünün evreni: hayatta kalma yanlılığı)"}

    trades = run_backtest(data, p, costs, btc)
    grid_df = None
    if args.grid:
        grid = {"donchian_n": [24, 48, 96], "min_vol_mult": [1.5, 2.0, 3.0], "stop_atr": [1.5, 2.0, 3.0], "trail_atr": [2.5, 3.0, 4.0]}
        grid_df = grid_search(data, p, costs, grid, btc)
    report = format_report(trades, p, costs, meta, grid_df)
    os.makedirs(args.out, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    with open(os.path.join(args.out, f"backtest_{stamp}.md"), "w", encoding="utf-8") as f:
        f.write(report)
    with open(os.path.join(args.out, "latest.md"), "w", encoding="utf-8") as f:
        f.write(report)
    trades.to_csv(os.path.join(args.out, "trades_latest.csv"), index=False)
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
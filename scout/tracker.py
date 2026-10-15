"""Canlı sinyallerin ileriye dönük takibi. Backtest ile AYNI çıkış kodunu (exits.step) kullanır;
böylece canlı sonuçlar backtest ile doğrudan karşılaştırılabilir."""
from __future__ import annotations

import pandas as pd

from .config import Costs, StrategyParams
from .exits import OpenTrade, new_trade, step


def new_record(c: dict) -> dict:
    return {
        "symbol": c["symbol"], "side": 1 if c["side"] == "LONG" else -1,
        "signal_ts": int(c["signal_ts"]), "atr_signal": float(c["atr"]),
        "strength": float(c.get("strength", 0.0)),
        "status": "PENDING", "trade": None, "last_ts": int(c["signal_ts"]),
    }


def advance(rec: dict, d: pd.DataFrame, p: StrategyParams, costs: Costs) -> dict:
    """d: KAPANMIŞ mumlar; sütunlar ts, open, high, low, close, atr. Yeni mumları işler."""
    if rec["status"] in ("CLOSED", "SKIPPED"):
        return rec
    tr = OpenTrade.from_dict(rec["trade"]) if rec["trade"] else None
    for row in d[d["ts"] > rec["last_ts"]].itertuples(index=False):
        if tr is None:
            if row.ts <= rec["signal_ts"]:
                continue
            entry = float(row.open)
            if p.stop_atr * rec["atr_signal"] / entry > p.max_stop_pct:
                rec["status"] = "SKIPPED"
                rec["last_ts"] = int(row.ts)
                return rec
            tr = new_trade(rec["side"], entry, rec["atr_signal"], p)
            rec["entry_ts"] = int(row.ts)
            rec["status"] = "OPEN"
        step(tr, float(row.open), float(row.high), float(row.low), float(row.close), float(row.atr), p)
        rec["last_ts"] = int(row.ts)
        if tr.done:
            rec["status"] = "CLOSED"
            rec["exit_ts"] = int(row.ts)
            rec["gross_r"] = tr.realized_r
            rec["cost_r"] = costs.round_trip * tr.entry / tr.risk
            rec["net_r"] = tr.realized_r - rec["cost_r"]
            rec["reason"] = tr.exit_reason
            rec["bars"] = tr.bars
            rec["mfe_pct"] = tr.mfe_pct
            rec["entry"] = tr.entry
            break
    rec["trade"] = tr.to_dict() if tr else None
    return rec
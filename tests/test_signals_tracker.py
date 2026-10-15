import unittest

import numpy as np

from scout.backtest import simulate_symbol
from scout.config import Costs, LiveConfig, StrategyParams
from scout.indicators import prepare
from scout.signals import candidate_from_row, format_message, record_alert, should_alert
from scout.strategy import compute_signals
from scout.tracker import advance, new_record
from tests.helpers import make_series

P, C, CFG = StrategyParams(), Costs(), LiveConfig()


def first_signal_series():
    for seed in range(40):
        df = make_series(900, seed=300 + seed, plants=[(500, 1)])
        d = prepare(df, P)
        sig = compute_signals(d, P)
        if sig["long"].iloc[500]:
            return df, d, sig
    raise RuntimeError("plant sinyali üretilemedi")


class TestSignalsTracker(unittest.TestCase):
    def test_message_contains_actionable_levels(self):
        df, d, sig = first_signal_series()
        c = candidate_from_row("TEST/USDT:USDT", d, 500, +1, sig["strength"].iloc[500], P, CFG)
        msg = format_message([c], {"btc_1h": "BULLISH", "btc_4h": "NEUTRAL"}, P)
        for needle in ("LONG", "TEST/USDT:USDT", "Stop:", "TP1:", "iz süren", "BTC 1s"):
            self.assertIn(needle, msg)
        self.assertLess(c["stop"], c["close"]); self.assertGreater(c["tp1"], c["close"])
        self.assertAlmostEqual(c["stop"], c["close"] - P.stop_atr * c["atr"])

    def test_dedupe_cooldown(self):
        df, d, sig = first_signal_series()
        c = candidate_from_row("X/USDT:USDT", d, 500, +1, 1.0, P, CFG)
        st = {}
        self.assertTrue(should_alert(st, c, 24 * 3_600_000))
        record_alert(st, c)
        self.assertFalse(should_alert(st, c, 24 * 3_600_000))
        later = dict(c, signal_ts=c["signal_ts"] + 25 * 3_600_000)
        self.assertTrue(should_alert(st, later, 24 * 3_600_000))

    def test_tracker_matches_backtest_exactly(self):
        """Canlı takip ile backtest AYNI sinyal için AYNI sonucu vermeli."""
        df, d, sig = first_signal_series()
        bt = simulate_symbol("T", d, sig.iloc[:560].reindex(d.index, fill_value=False).assign(
            strength=sig["strength"]), P, C)
        self.assertTrue(len(bt) >= 1)
        t0 = bt[0]
        i = int(np.flatnonzero(d["ts"].to_numpy() == t0["signal_ts"])[0])
        side = 1 if t0["side"] == "LONG" else -1
        c = candidate_from_row("T", d, i, side, 1.0, P, CFG)
        rec = advance(new_record(c), d, P, C)
        self.assertEqual(rec["status"], "CLOSED")
        self.assertAlmostEqual(rec["net_r"], t0["net_r"], places=9)
        self.assertEqual(rec["reason"], t0["reason"])

    def test_tracker_is_incremental(self):
        """Mumlar parça parça gelse de sonuç, hepsi bir anda gelmiş gibi aynı olmalı."""
        df, d, sig = first_signal_series()
        c = candidate_from_row("T", d, 500, +1, 1.0, P, CFG)
        whole = advance(new_record(c), d, P, C)
        rec = new_record(c)
        for end in range(505, len(d) + 25, 25):
            rec = advance(rec, d.iloc[: min(end, len(d))], P, C)
        self.assertEqual(rec["status"], whole["status"])
        if whole["status"] == "CLOSED":
            self.assertAlmostEqual(rec["net_r"], whole["net_r"], places=9)


if __name__ == "__main__":
    unittest.main()
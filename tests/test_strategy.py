import unittest

import numpy as np

from scout.config import StrategyParams
from scout.indicators import prepare
from scout.strategy import compute_signals
from tests.helpers import make_series

P = StrategyParams()


class TestStrategy(unittest.TestCase):
    def test_signals_have_no_lookahead(self):
        df = make_series(1500, seed=11, plants=[(500, 1), (900, -1), (1200, 1)])
        full = compute_signals(prepare(df, P), P)
        for k in (520, 910, 1210, 1400):
            cut = compute_signals(prepare(df.iloc[: k + 1], P), P)
            for col in ("long", "short"):
                self.assertTrue(np.array_equal(full[col].to_numpy()[: k + 1], cut[col].to_numpy()),
                                f"{col}: k={k} sinyaller gelecekle değişti")

    def test_planted_long_breakout_is_detected(self):
        found = 0
        for seed in range(6):
            df = make_series(900, seed=100 + seed, plants=[(500, 1)])
            sig = compute_signals(prepare(df, P), P)
            found += int(sig["long"].iloc[500])
            self.assertFalse(sig["long"].iloc[499], "kırılımdan ÖNCEKİ mumda sinyal olmamalı")
        self.assertGreaterEqual(found, 4, "plant edilen LONG kırılımların çoğu yakalanmalı")

    def test_planted_short_breakout_is_detected(self):
        found = 0
        for seed in range(6):
            df = make_series(900, seed=200 + seed, plants=[(500, -1)])
            sig = compute_signals(prepare(df, P), P)
            found += int(sig["short"].iloc[500])
        self.assertGreaterEqual(found, 4)

    def test_never_long_and_short_on_same_bar(self):
        df = make_series(3000, seed=21)
        sig = compute_signals(prepare(df, P), P)
        self.assertFalse((sig["long"] & sig["short"]).any())

    def test_btc_filter_blocks_counter_trend(self):
        from dataclasses import replace
        pf = replace(P, use_btc_filter=True)
        df = make_series(900, seed=100, plants=[(500, 1)])
        d = prepare(df, pf)
        base = compute_signals(d, P)
        if not base["long"].iloc[500]:
            self.skipTest("bu tohumda sinyal oluşmadı")
        bear = np.full(len(d), -1)
        self.assertFalse(compute_signals(d, pf, bear)["long"].iloc[500])
        bull = np.full(len(d), 1)
        self.assertTrue(compute_signals(d, pf, bull)["long"].iloc[500])


if __name__ == "__main__":
    unittest.main()
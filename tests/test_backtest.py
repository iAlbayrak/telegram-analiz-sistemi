import unittest

import numpy as np

from scout.backtest import run_backtest, summarize, split_time
from scout.config import Costs, StrategyParams
from tests.helpers import make_universe

P = StrategyParams()
C = Costs()


class TestBacktest(unittest.TestCase):
    def test_costs_are_applied_in_r(self):
        tr = run_backtest(make_universe(10, 2500, 1), P, C)
        self.assertGreater(len(tr), 20)
        expected = C.round_trip * tr["entry"] / (tr["entry"] - tr["stop"]).abs()
        self.assertTrue(np.allclose(tr["cost_r"], expected))
        self.assertTrue(np.allclose(tr["net_r"], tr["gross_r"] - tr["cost_r"]))

    def test_null_market_has_no_free_lunch(self):
        """Kenarı olmayan rastgele piyasada brüt beklenti ~0, net beklenti <= ~0 olmalı.
        Belirgin pozitif çıkarsa motorda gelecek-sızıntısı vardır."""
        gross, net = [], []
        for seed in (1, 2, 3):
            s = summarize(run_backtest(make_universe(30, 3000, seed), P, C))
            self.assertGreater(s["n"], 300)
            gross.append(s["avg_gross_r"]); net.append(s["avg_net_r"])
        self.assertLess(abs(np.mean(gross)), 0.15)
        self.assertLess(np.mean(net), 0.05)

    def test_planted_edge_is_found(self):
        s = summarize(run_backtest(make_universe(30, 3000, 1, edge=True), P, C))
        self.assertGreater(s["avg_net_r"], 0.5)
        self.assertGreater(s["ci_lo"], 0.0)
        self.assertGreater(s["hit10"], 20.0)

    def test_deterministic(self):
        a = run_backtest(make_universe(8, 2000, 4), P, C)
        b = run_backtest(make_universe(8, 2000, 4), P, C)
        self.assertTrue(a.equals(b))

    def test_one_position_per_symbol_at_a_time(self):
        tr = run_backtest(make_universe(10, 3000, 2), P, C)
        for _, g in tr.groupby("symbol"):
            g = g.sort_values("entry_ts")
            self.assertTrue((g["entry_ts"].to_numpy()[1:] >= g["exit_ts"].to_numpy()[:-1]).all())

    def test_time_split(self):
        tr = run_backtest(make_universe(10, 2500, 1), P, C)
        a, b = split_time(tr)
        self.assertEqual(len(a) + len(b), len(tr))
        self.assertLess(a["entry_ts"].max(), b["entry_ts"].min() + 1)


if __name__ == "__main__":
    unittest.main()
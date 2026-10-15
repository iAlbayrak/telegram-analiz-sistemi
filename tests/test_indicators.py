import unittest

import numpy as np
import pandas as pd

from scout.config import StrategyParams
from scout.indicators import atr, prepare
from tests.helpers import make_series

P = StrategyParams()


class TestIndicators(unittest.TestCase):
    def test_atr_matches_manual_wilder(self):
        df = make_series(300, seed=3)
        n = 14
        pc = df["close"].shift(1)
        tr = pd.concat([df.high - df.low, (df.high - pc).abs(), (df.low - pc).abs()], axis=1).max(axis=1).to_numpy()
        manual = np.empty(len(tr)); manual[0] = tr[0]
        for i in range(1, len(tr)):
            manual[i] = manual[i - 1] * (1 - 1 / n) + tr[i] / n
        got = atr(df, n).to_numpy()
        self.assertTrue(np.allclose(got[n - 1:], manual[n - 1:], rtol=1e-9))

    def test_features_have_no_lookahead(self):
        """Satır k'nin özellikleri, k'den sonraki veriyi silince DEĞİŞMEMELİ."""
        df = make_series(700, seed=5)
        full = prepare(df, P)
        cols = [c for c in full.columns if c != "ts"]
        for k in (300, 450, 650):
            cut = prepare(df.iloc[: k + 1], P)
            a = full.iloc[k][cols].to_numpy(float)
            b = cut.iloc[k][cols].to_numpy(float)
            self.assertTrue(np.allclose(a, b, equal_nan=True), f"k={k} gelecek veri sızıntısı")

    def test_missing_columns(self):
        with self.assertRaises(ValueError):
            prepare(pd.DataFrame({"ts": [1], "open": [1]}), P)


if __name__ == "__main__":
    unittest.main()
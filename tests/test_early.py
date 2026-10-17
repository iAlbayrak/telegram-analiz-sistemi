import unittest

import numpy as np
import pandas as pd

from scout.early import score_daily_setup


class TestEarlySetup(unittest.TestCase):
    def make_base(self, n=400):
        ts = np.arange(n, dtype=np.int64) * 86_400_000
        close = np.linspace(80.0, 100.0, n)
        high = close * 1.02
        low = close * 0.98
        volume = np.full(n, 100.0)
        # Recent base tightens close to resistance with rising lows and volume pickup.
        high[-60:] = 110.0
        low[-60:] = 95.0
        close[-20:] = np.linspace(104.0, 105.7, 20)
        high[-20:] = 106.0
        low[-20:] = np.linspace(101.0, 103.0, 20)
        volume[-5:] = 140.0
        return pd.DataFrame({
            "ts": ts, "open": close * 0.999, "high": high, "low": low,
            "close": close, "volume": volume,
        })

    def test_daily_base_near_resistance_is_watch_candidate_not_prediction(self):
        result = score_daily_setup(self.make_base())
        self.assertIsNotNone(result)
        self.assertEqual(result["side"], "LONG")
        self.assertGreaterEqual(result["score"], 3.0)
        self.assertIn("trend_1w", result)
        self.assertIn("trend_1m", result)
        self.assertIn("trend_3m", result)
        self.assertIn("trend_1y", result)
        self.assertLess(result["support"], result["resistance"])

    def test_insufficient_daily_history_is_rejected(self):
        self.assertIsNone(score_daily_setup(self.make_base(60)))

    def test_no_lookahead_for_same_daily_prefix(self):
        df = self.make_base()
        full = score_daily_setup(df.iloc[:150])
        changed_future = df.copy()
        changed_future.loc[150:, "close"] *= 2.0
        prefix = score_daily_setup(changed_future.iloc[:150])
        self.assertEqual(full, prefix)


if __name__ == "__main__":
    unittest.main()

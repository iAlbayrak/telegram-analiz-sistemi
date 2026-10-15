import unittest

from scout.config import StrategyParams
from scout.exits import new_trade, step

P = StrategyParams(stop_atr=2.0, tp1_r=1.5, tp1_frac=0.33, trail_atr=3.0, time_stop_bars=72, be_after_tp1=True)


def long_trade():
    return new_trade(+1, 100.0, 1.0, P)   # risk=2, stop=98, tp1=103


def short_trade():
    return new_trade(-1, 100.0, 1.0, P)   # risk=2, stop=102, tp1=97


class TestExits(unittest.TestCase):
    def test_setup_numbers(self):
        t = long_trade()
        self.assertEqual((t.init_stop, t.tp1, t.risk), (98.0, 103.0, 2.0))

    def test_stop_hit(self):
        t = step(long_trade(), 100, 100.5, 97.5, 98.2, 1.0, P)
        self.assertTrue(t.done); self.assertEqual(t.exit_reason, "STOP"); self.assertAlmostEqual(t.realized_r, -1.0)

    def test_gap_through_stop_exits_at_open(self):
        t = step(long_trade(), 95, 96, 94, 95.5, 1.0, P)
        self.assertEqual(t.exit_reason, "STOP_GAP"); self.assertAlmostEqual(t.realized_r, -2.5)

    def test_same_bar_stop_and_target_is_stop_first(self):
        t = step(long_trade(), 100, 104, 97, 101, 1.0, P)
        self.assertEqual(t.exit_reason, "STOP"); self.assertAlmostEqual(t.realized_r, -1.0)

    def test_tp1_then_conservative_breakeven_same_bar(self):
        t = step(long_trade(), 100, 103.5, 99.5, 103, 1.0, P)
        self.assertTrue(t.done); self.assertEqual(t.exit_reason, "BE_AFTER_TP1")
        self.assertAlmostEqual(t.realized_r, 0.33 * 1.5, places=6)

    def test_tp1_then_trail_exit(self):
        t = step(long_trade(), 100, 103.5, 100.5, 103, 1.0, P)
        self.assertFalse(t.done); self.assertTrue(t.tp1_done)
        self.assertAlmostEqual(t.stop, 100.5)             # extreme 103.5 - 3*1.0
        t = step(t, 103, 104, 100.4, 101, 1.0, P)
        self.assertTrue(t.done); self.assertEqual(t.exit_reason, "TRAIL")
        self.assertAlmostEqual(t.realized_r, 0.33 * 1.5 + 0.67 * (0.5 / 2.0), places=6)

    def test_trail_applies_from_next_bar_only(self):
        # Tepe 106 -> iz süren stop 103 olur; ama mumun dibi (100.5) 103'ün altında: bu mumda ÇIKIŞ OLMAMALI.
        t = step(long_trade(), 100, 106, 100.5, 105, 1.0, P)
        self.assertFalse(t.done)
        self.assertAlmostEqual(t.stop, 103.0)
        t = step(t, 105, 105.5, 102.9, 103.5, 1.0, P)          # sonraki mum: artık 103 geçerli, dip 102.9 vurur
        self.assertTrue(t.done); self.assertEqual(t.exit_reason, "TRAIL")

    def test_time_stop(self):
        p = StrategyParams(stop_atr=2.0, time_stop_bars=3)
        t = new_trade(+1, 100.0, 1.0, p)
        for _ in range(3):
            step(t, 100, 100.5, 99.5, 100.2, 1.0, p)
        self.assertEqual(t.exit_reason, "TIME"); self.assertAlmostEqual(t.realized_r, 0.1)

    def test_short_mirror(self):
        t = step(short_trade(), 100, 102.5, 99, 101, 1.0, P)
        self.assertEqual(t.exit_reason, "STOP"); self.assertAlmostEqual(t.realized_r, -1.0)
        t = short_trade()
        t = step(t, 100, 100.5, 96.5, 97.5, 1.0, P)       # TP1=97 değdi, BE=100 değmedi (tepe 100.5 > 100 -> BE vurulur)
        self.assertEqual(t.exit_reason, "BE_AFTER_TP1")

    def test_excursions(self):
        t = step(long_trade(), 100, 102, 99, 101, 1.0, P)
        self.assertAlmostEqual(t.mfe_pct, 2.0); self.assertAlmostEqual(t.mae_pct, 1.0)


if __name__ == "__main__":
    unittest.main()
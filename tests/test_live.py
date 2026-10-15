"""Ağsız uçtan uca test: sahte borsa verisiyle live.main() akışı (mesaj, dedupe, takip)."""
import contextlib
import io
import os
import tempfile
import time
import unittest
from unittest import mock

import pandas as pd

from scout import live
from tests.helpers import HOUR, make_series


def _retime(df, last_open_ts):
    d = df.copy()
    d["ts"] = last_open_ts - (len(d) - 1 - pd.RangeIndex(len(d))) * HOUR
    return d.reset_index(drop=True)


class TestLive(unittest.TestCase):
    def _run(self, store, tmp, cap="50"):
        buf = io.StringIO()
        env = {"SCOUT_STATE_DIR": tmp, "TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": "",
               "SCOUT_LIVE_MAX_ALERTS_PER_RUN": cap}
        ex = object()
        with mock.patch.dict(os.environ, env), \
             mock.patch("scout.data.make_exchange", return_value=ex), \
             mock.patch("scout.data.thread_exchange", return_value=ex), \
             mock.patch("scout.data.list_universe", return_value=[(s, 5e6) for s in store if s != "BTC/USDT:USDT"]), \
             mock.patch("scout.data.fetch_recent", side_effect=lambda e, s, tf, lim: store[s].copy()), \
             mock.patch("scout.data.fetch_last_price", side_effect=lambda e, s: float(store[s]["close"].iloc[-1])), \
             contextlib.redirect_stdout(buf):
            live.main()
        return buf.getvalue()

    def test_end_to_end(self):
        now = int(time.time() * 1000)
        last_open = (now // HOUR) * HOUR - HOUR            # en son KAPANMIŞ saatlik mumun açılışı
        n = 420
        store = {"BTC/USDT:USDT": _retime(make_series(n, 1), last_open)}
        for j in range(8):
            store[f"A{j}/USDT:USDT"] = _retime(make_series(n, 50 + j, plants=[(n - 1, 1 if j % 2 == 0 else -1)]), last_open)
        with tempfile.TemporaryDirectory() as tmp:
            out1 = self._run(store, tmp)
            self.assertIn("sinyal gönderildi", out1 + "TELEGRAM AYARLI DEĞİL")  # token yok -> mesaj stdout'a yazılır
            self.assertTrue(("LONG" in out1) or ("SHORT" in out1), out1[-800:])
            self.assertIn("Stop:", out1)
            import json, pathlib
            st = json.loads(pathlib.Path(tmp, "alert_state.json").read_text())
            n_alerts = len(st["alerts"])
            self.assertGreaterEqual(n_alerts, 1)
            opens = json.loads(pathlib.Path(tmp, "open_signals.json").read_text())
            self.assertEqual(len(opens), n_alerts)
            # ikinci tur: aynı veri -> tekrar bildirim OLMAMALI
            out2 = self._run(store, tmp)
            self.assertIn("yeni sinyal yok", out2)
            st2 = json.loads(pathlib.Path(tmp, "alert_state.json").read_text())
            self.assertEqual(st2["alerts"], st["alerts"])

    def test_cap_is_disclosed_not_silent(self):
        now = int(time.time() * 1000)
        last_open = (now // HOUR) * HOUR - HOUR
        n = 420
        store = {"BTC/USDT:USDT": _retime(make_series(n, 1), last_open)}
        for j in range(8):
            store[f"A{j}/USDT:USDT"] = _retime(make_series(n, 50 + j, plants=[(n - 1, 1 if j % 2 == 0 else -1)]), last_open)
        with tempfile.TemporaryDirectory() as tmp:
            out = self._run(store, tmp, cap="2")
        self.assertIn("aday daha vardı", out)


if __name__ == "__main__":
    unittest.main()
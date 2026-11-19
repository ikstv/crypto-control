import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from algorithm import Algorithm
from backtest import DAY
from shadow import Shadow


class ShadowTests(unittest.TestCase):
    def test_crossovers_and_outage_at_utc_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            algorithm = Algorithm(Path(directory) / "test.sqlite")
            algorithm.change("activate")
            shadow = Shadow(algorithm)
            start = 100 * DAY
            prices = [100] * 30 + [130, 130, 30]
            rows = [[start + i * DAY, p, p, p, p, 1, 1]
                    for i, p in enumerate(prices)]

            def fetch(begin, end):
                return [row for row in rows if begin <= row[0] <= end + DAY]

            for index, expected in enumerate(("BUY", "HOLD", "SELL"), 31):
                end = start + index * DAY
                shadow.poll(fetch, end)
                row = shadow.history()[0]
                self.assertEqual(row["candle_open"], end - DAY)
                self.assertEqual(row["decision"], expected)
                self.assertEqual(Decimal(row["sma10"]), Decimal(sum(prices[index-10:index])) / 10)
                self.assertEqual(Decimal(row["sma30"]), Decimal(sum(prices[index-30:index])) / 30)
            shadow.poll(fetch, end + DAY - 1)
            self.assertEqual(len(shadow.history()), 3)

            def offline(*_):
                raise OSError("offline")

            shadow.poll(offline, end + DAY)
            self.assertEqual(shadow.history()[0]["reason"], "WhiteBIT candles unavailable")
            self.assertIsNone(shadow.history()[0]["decision"])

    def test_restart_gap_and_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            algorithm = Algorithm(Path(directory) / "test.sqlite")
            end = 100 * DAY
            rows = [[t, 100, 100, 100, 100, 1, 1]
                    for t in range(end - 31 * DAY, end, DAY)]
            rows[-1][2:4] = [110, 110]
            rows.append([end, 1, 1, 1, 1, 1, 1])
            shadow = Shadow(algorithm)
            fetch = lambda *_: rows
            shadow.poll(fetch, end)
            self.assertEqual(shadow.history(), [])
            algorithm.change("activate")
            shadow.poll(lambda *_: rows[1:], end)
            self.assertEqual(shadow.history()[0]["kind"], "skip")
            shadow.poll(fetch, end)
            reopened = Shadow(Algorithm(algorithm.path))
            reopened.poll(fetch, end)
            self.assertEqual([row["decision"] for row in reopened.history()], ["BUY", None])
            algorithm.change("emergency")
            reopened.poll(fetch, end + DAY)
            self.assertEqual(len(reopened.history()), 2)

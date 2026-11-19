import tempfile
import unittest
from pathlib import Path

from algorithm import Algorithm
from backtest import DAY
from shadow import Shadow


class ShadowTests(unittest.TestCase):
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

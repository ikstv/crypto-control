import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from algorithm import Algorithm, Blocked


class AlgorithmTests(unittest.TestCase):
    def test_persistent_lock_and_explicit_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.sqlite"
            mode = Algorithm(path)
            with self.assertRaises(Blocked):
                mode.run(lambda db: self.fail("Stopped operation executed"))
            mode.change("activate")
            self.assertEqual(mode.run(lambda db: 42), 42)
            mode.change("emergency")
            reopened = Algorithm(path)
            with self.assertRaises(Blocked):
                reopened.run(lambda db: self.fail("Blocked operation executed"))
            with self.assertRaises(Blocked):
                reopened.change("activate")
            with self.assertRaises(Blocked):
                reopened.change("reset")
            self.assertEqual(reopened.change("reset", confirmed=True), "stopped")

    def test_concurrent_emergency_requests_log_one_transition(self):
        with tempfile.TemporaryDirectory() as directory:
            mode = Algorithm(Path(directory) / "test.sqlite")
            with ThreadPoolExecutor(4) as pool:
                self.assertEqual(list(pool.map(mode.change, ["emergency"] * 8)),
                                 ["blocked"] * 8)
            self.assertEqual(len(mode.history()), 1)

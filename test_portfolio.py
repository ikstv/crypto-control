import tempfile
import unittest
from pathlib import Path

from portfolio import initialize, read_portfolio


class PortfolioTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.db = Path(directory.name) / "test.sqlite3"
        initialize(self.db)

    def test_demo_allocations(self):
        portfolio = read_portfolio(self.db)
        self.assertEqual(portfolio["initial_capital_minor"], 1_000_000)
        self.assertEqual(portfolio["allocated_minor"], 1_000_000)
        self.assertEqual(portfolio["unallocated_minor"], 0)
        self.assertEqual(
            {p["symbol"]: p["allocation_minor"] for p in portfolio["positions"]},
            dict.fromkeys(("BTC", "ETH", "SOL", "XRP", "LINK", "LTC", "ADA", "TRX"), 100_000)
            | {"BNB": 200_000},
        )

    def test_restart_preserves_portfolio_and_history(self):
        from sqlite3 import connect
        from contextlib import closing

        with closing(connect(self.db)) as db, db:
            db.execute("UPDATE positions SET allocation_minor = 90000 WHERE symbol = 'BTC'")
        initialize(self.db)
        self.assertEqual(read_portfolio(self.db)["unallocated_minor"], 10_000)
        with closing(connect(self.db)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM history").fetchone()[0], 1)

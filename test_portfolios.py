import sqlite3
import tempfile
import unittest
from pathlib import Path
from contextlib import closing
from decimal import Decimal

from portfolio import (
    connect,
    create_portfolio,
    initialize,
    list_portfolios,
    migrate_portfolios,
    portfolio_observations,
    record_portfolio_observation,
    read_portfolio_for,
    migrate_ledger_schema,
)
from ledger import Ledger


class PortfolioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "portfolio.sqlite3"
        initialize(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def test_migration_is_idempotent_and_preserves_legacy_snapshot(self):
        before = read_portfolio_for(self.path, None)
        first = migrate_portfolios(self.path)
        second = migrate_portfolios(self.path)
        self.assertEqual(first["legacy_id"], second["legacy_id"])
        legacy = read_portfolio_for(self.path, first["legacy_id"])
        self.assertEqual(legacy["currency"], before["currency"])
        self.assertEqual(legacy["initial_capital_minor"], before["initial_capital_minor"])
        self.assertEqual(len(list_portfolios(self.path)), 1)

    def test_create_uhah_and_usdt_portfolios_uses_currency_scales(self):
        migrate_portfolios(self.path)
        uah = create_portfolio(self.path, "Experiment", "UAH", "123.45", "u1")
        usdt = create_portfolio(self.path, "BTC test", "USDT", "1.123456", "u2")
        self.assertEqual(uah["initial_capital_minor"], 12345)
        self.assertEqual(usdt["initial_capital_minor"], 1123456)
        self.assertEqual(uah["currency"], "UAH")
        self.assertEqual(usdt["currency"], "USDT")

    def test_idempotency_key_changed_parameters_conflict(self):
        migrate_portfolios(self.path)
        create_portfolio(self.path, "Experiment", "UAH", "10", "same")
        with self.assertRaises(ValueError):
            create_portfolio(self.path, "Other", "UAH", "10", "same")

    def test_foreign_keys_are_enforced(self):
        migrate_portfolios(self.path)
        with closing(connect(self.path)) as db, db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(
                    "INSERT INTO portfolio_observations "
                    "(portfolio_id, slot, status) VALUES (999, 'x', 'unavailable')"
                )

    def test_observations_are_isolated_and_idempotent_by_portfolio_and_slot(self):
        migrate_portfolios(self.path)
        other = create_portfolio(self.path, "Other", "USDT", "1", "other")
        self.assertTrue(record_portfolio_observation(self.path, 1, "slot", "unavailable", "legacy"))
        self.assertFalse(record_portfolio_observation(self.path, 1, "slot", "recorded"))
        self.assertTrue(record_portfolio_observation(self.path, other["id"], "slot", "unavailable", "other"))
        self.assertEqual(len(portfolio_observations(self.path, 1)), 1)
        self.assertEqual(portfolio_observations(self.path, other["id"])[0]["reason"], "other")

    def test_ledger_schema_migration_preserves_legacy_rows_and_is_repeatable(self):
        Ledger(self.path).initialize()
        with closing(connect(self.path)) as db, db:
            db.execute("INSERT INTO paper_start VALUES (1, ?)", ('{"cash_uah":"10000"}',))
            db.execute("INSERT INTO paper_operations VALUES (1, ?)", ('{"symbol":"BTC","debit_uah":"100"}',))
            db.execute("INSERT INTO paper_sales VALUES ('sale-1', 'BTC', ?)", ('{"net_uah":"101"}',))
            db.execute("INSERT INTO paper_risk VALUES (1, ?)", ('{"paused":false}',))
            db.execute("INSERT INTO paper_equity (observed_at, day, equity_uah) VALUES (?, ?, ?)", ('t', '2026-01-01', '10000'))
            db.execute("INSERT INTO paper_observations VALUES (?, ?, ?, ?, ?, ?, ?)", ('slot', 't', 'recorded', None, '10000', '0', '0'))
        migrate_ledger_schema(self.path)
        migrate_ledger_schema(self.path)
        with closing(connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM paper_start WHERE portfolio_id=1").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM paper_operations WHERE portfolio_id=1").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM paper_sales WHERE portfolio_id=1").fetchone()[0], 1)
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_migrated_tables_keep_two_portfolios_separate(self):
        migrate_portfolios(self.path)
        other = create_portfolio(self.path, "Other", "USDT", "1", "other")
        Ledger(self.path).initialize()
        migrate_ledger_schema(self.path)
        with closing(connect(self.path)) as db, db:
            db.execute("INSERT INTO paper_risk (portfolio_id, config) VALUES (?, ?)", (1, '{}'))
            db.execute("INSERT INTO paper_risk (portfolio_id, config) VALUES (?, ?)", (other["id"], '{}'))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM paper_risk WHERE portfolio_id=1").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM paper_risk WHERE portfolio_id=?", (other["id"],)).fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()

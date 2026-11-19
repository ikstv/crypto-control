import sqlite3
import tempfile
import unittest
from pathlib import Path
from contextlib import closing
from decimal import Decimal
from unittest.mock import patch

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
    read_portfolio,
)
from ledger import Ledger, LedgerSchemaMismatch, MarketUnavailable
from test_ledger import feed


class PortfolioTests(unittest.TestCase):
    def test_migration_failure_restores_complete_source_and_allows_retry(self):
        markers = ("INSERT INTO portfolios", 'ALTER TABLE "positions"',
                   "CREATE INDEX idx_sales_portfolio", "PRAGMA integrity_check", "PRAGMA foreign_key_check")
        for index, marker in enumerate(markers):
            with self.subTest(marker=marker):
                path = self.path.with_name(f"fault-{index}.db")
                initialize(path)
                ledger = Ledger(path, feed)
                ledger.start()
                receipt = ledger.sell("BTC", "before", ledger.preview("BTC")["fingerprint"])
                ledger.record_valuation()
                def snapshot():
                    with closing(connect(path)) as db:
                        self.assertEqual(db.execute("PRAGMA integrity_check").fetchall(), [("ok",)])
                        self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
                        return list(db.iterdump())

                original = snapshot()
                state = ledger.history()
                real_connect = sqlite3.connect

                class FaultConnection(sqlite3.Connection):
                    def execute(self, sql, *args):
                        if sql.startswith(marker):
                            raise sqlite3.OperationalError("injected migration failure")
                        return super().execute(sql, *args)

                with patch("portfolio.sqlite3.connect", side_effect=lambda *a, **kw:
                           real_connect(*a, factory=FaultConnection, **kw)):
                    with self.assertRaisesRegex(sqlite3.OperationalError, "injected"):
                        migrate_ledger_schema(path)
                self.assertEqual(snapshot(), original)
                self.assertEqual(ledger.history(), state)
                self.assertEqual(migrate_ledger_schema(path)["status"], "migrated")
                migrated = snapshot()
                self.assertEqual(migrate_ledger_schema(path)["status"], "already_migrated")
                self.assertEqual(snapshot(), migrated)
                self.assertEqual(ledger.sell("BTC", "before", "ignored"), receipt)
                for key in ("cash_uah", "holdings", "operations", "realized_pnl_uah", "observations"):
                    self.assertEqual(ledger.history()[key], state[key])

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

    def test_legacy_ledger_refuses_migrated_schema_instead_of_mixing_data(self):
        migrate_ledger_schema(self.path)
        with self.assertRaises(LedgerSchemaMismatch):
            Ledger(self.path, portfolio_id=1).initialize()

    def test_read_only_ledger_history_isolated_for_two_migrated_portfolios(self):
        migrate_portfolios(self.path)
        other = create_portfolio(self.path, "USDT", "USDT", "1", "usdt")
        Ledger(self.path).initialize()
        migrate_ledger_schema(self.path)
        with closing(connect(self.path)) as db, db:
            db.execute("INSERT INTO settings (portfolio_id, capital_minor) VALUES (?, ?)", (other["id"], 1000000))
            db.execute("INSERT INTO positions VALUES (?, ?, ?)", (other["id"], "BTC", 1000000))
            db.execute("INSERT INTO paper_start VALUES (?, ?)", (1, '{"cash_uah":"10000","initial_uah":"10000","initial_usdt":"250"}'))
            db.execute("INSERT INTO paper_start VALUES (?, ?)", (other["id"], '{"cash_usdt":"1","initial_usdt":"1"}'))
            db.execute("INSERT INTO paper_operations VALUES (?, ?, ?)", (1, 1, '{"symbol":"BTC"}'))
            db.execute("INSERT INTO paper_operations VALUES (?, ?, ?)", (2, other["id"], '{"symbol":"BTC"}'))
            db.execute("INSERT INTO paper_observations (portfolio_id, slot, observed_at, status, reason) VALUES (?, ?, ?, ?, ?)", (1, "a", "t", "recorded", "legacy"))
            db.execute("INSERT INTO paper_observations (portfolio_id, slot, observed_at, status, reason) VALUES (?, ?, ?, ?, ?)", (other["id"], "a", "t", "recorded", "other"))
        self.assertEqual(read_portfolio(self.path, 1)["currency"], "UAH")
        self.assertEqual(read_portfolio(self.path, other["id"])["currency"], "USDT")
        self.assertEqual([op["symbol"] for op in Ledger(self.path, portfolio_id=1).history()["operations"]], ["BTC"])
        self.assertEqual([op["symbol"] for op in Ledger(self.path, portfolio_id=other["id"]).history()["operations"]], ["BTC"])
        self.assertEqual(Ledger(self.path, portfolio_id=1).observation_history()[0]["reason"], "legacy")
        self.assertEqual(Ledger(self.path, portfolio_id=other["id"]).observation_history()[0]["reason"], "other")

    def test_read_only_ledger_rejects_unknown_migrated_portfolio(self):
        migrate_ledger_schema(self.path)
        with self.assertRaises(ValueError):
            Ledger(self.path, portfolio_id=999).history()

    def test_migrated_ledger_rejects_sale_without_position(self):
        migrate_ledger_schema(self.path)
        ledger = Ledger(self.path, portfolio_id=1)
        with self.assertRaises(MarketUnavailable):
            ledger.sell("BTC", "sale", "0" * 64)

    def _migrated_with_usdt(self):
        migrate_portfolios(self.path)
        other = create_portfolio(self.path, "USDT", "USDT", "1000", "usdt-start")
        Ledger(self.path).initialize()
        migrate_ledger_schema(self.path)
        with closing(connect(self.path)) as db, db:
            db.execute("INSERT INTO settings (portfolio_id, capital_minor) VALUES (?, ?)", (other["id"], 1000000000))
            db.execute("INSERT INTO positions VALUES (?, ?, ?)", (other["id"], "BTC", 1000000000))
        return other["id"]

    def test_migrated_start_isolated_for_uah_and_usdt(self):
        other_id = self._migrated_with_usdt()
        uah = Ledger(self.path, feed, portfolio_id=1).start()
        usdt = Ledger(self.path, feed, portfolio_id=other_id).start()
        self.assertTrue(uah["started"])
        self.assertTrue(usdt["started"])
        self.assertEqual(Ledger(self.path, feed, portfolio_id=1).start(), uah)
        self.assertEqual(Ledger(self.path, feed, portfolio_id=other_id).start(), usdt)
        self.assertEqual(len(Ledger(self.path, portfolio_id=1).history()["operations"]), 1)
        self.assertEqual(len(Ledger(self.path, portfolio_id=other_id).history()["operations"]), 1)

    def test_migrated_start_unknown_portfolio_does_not_write(self):
        self._migrated_with_usdt()
        with self.assertRaises(ValueError):
            Ledger(self.path, feed, portfolio_id=999).start()
        with closing(connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM paper_start").fetchone()[0], 0)

    def test_migrated_usdt_start_does_not_require_fx(self):
        other_id = self._migrated_with_usdt()
        no_fx = lambda endpoint: feed(endpoint, fx=False)
        result = Ledger(self.path, no_fx, portfolio_id=other_id).start()
        self.assertTrue(result["started"])

    def test_migrated_uah_start_rejects_insufficient_fx_depth_without_writes(self):
        self._migrated_with_usdt()
        with closing(connect(self.path)) as db, db:
            db.execute("UPDATE positions SET allocation_minor=100000 WHERE portfolio_id=1 AND symbol='BTC'")
        def thin_fx(endpoint):
            data = feed(endpoint)
            if endpoint.startswith("orderbook/USDT_UAH"):
                data["asks"] = [["40", "1"], ["41", "1"]]
            return data

        with self.assertRaises(MarketUnavailable):
            Ledger(self.path, thin_fx, portfolio_id=1).start()
        self.assertFalse(Ledger(self.path, portfolio_id=1).history()["started"])
        with closing(connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM paper_operations").fetchone()[0], 0)

    def test_explicit_uah_sale_uses_only_its_own_fx_depth(self):
        other_id = self._migrated_with_usdt()
        ledger = Ledger(self.path, feed, portfolio_id=1)
        ledger.start()
        other = Ledger(self.path, feed, portfolio_id=other_id).history()

        def thin_fx(endpoint):
            data = feed(endpoint)
            if "USDT_UAH" in endpoint:
                data["bids"] = [["40", "5"]]
            return data

        ledger.fetch = thin_fx
        before = ledger.history()
        for quantity in (None, Decimal(".1")):
            with self.assertRaises(MarketUnavailable):
                ledger.preview("BTC", quantity=quantity)
        for quantity in map(Decimal, ("0", "-1", "1")):
            with self.assertRaises(ValueError):
                ledger.preview("BTC", quantity=quantity)
        self.assertEqual(ledger.history(), before)
        quantity = Decimal(".05")
        quote = ledger.preview("BTC", quantity=quantity)
        receipt = ledger.sell("BTC", "small-fx", quote["fingerprint"], quantity)
        for key in ("quantity", "net_currency", "realized_pnl", "fx_fee_uah"):
            self.assertEqual(receipt["sales"][0][key], quote["sales"][0][key])
        reopened = Ledger(self.path, thin_fx, portfolio_id=1)
        self.assertEqual(reopened.sell("BTC", "small-fx", "ignored", quantity), receipt)
        self.assertEqual(Decimal(reopened.history()["cash_uah"]), Decimal(before["cash_uah"]) + Decimal("197.60"))
        self.assertEqual(Ledger(self.path, feed, portfolio_id=other_id).history(), other)

    def test_migrated_usdt_partial_and_full_sales_isolated(self):
        other_id = self._migrated_with_usdt()
        ledger = Ledger(self.path, feed, portfolio_id=other_id)
        ledger.start()
        preview = ledger.preview("BTC")
        self.assertGreater(Decimal(preview["sales"][0]["quantity"]), 0)
        partial = Decimal(preview["sales"][0]["quantity"]) / 2
        preview = ledger.preview("BTC", quantity=partial)
        first = ledger.sell("BTC", "sale-1", preview["fingerprint"], quantity=partial)
        again = ledger.sell("BTC", "sale-1", preview["fingerprint"], quantity=partial)
        self.assertEqual(first, again)
        self.assertGreater(Decimal(first["sales"][0]["remaining_quantity"]), 0)
        second_preview = ledger.preview("BTC")
        second = ledger.sell("BTC", "sale-2", second_preview["fingerprint"])
        self.assertEqual(Decimal(second["sales"][0]["remaining_quantity"]), 0)
        self.assertGreater(Decimal(ledger.history()["cash_usdt"]), Decimal("1"))
        self.assertEqual(len(Ledger(self.path, portfolio_id=1).history()["operations"]), 0)

    def test_migrated_uah_sale_uses_fx_and_realizes_pnl(self):
        self._migrated_with_usdt()
        ledger = Ledger(self.path, feed, portfolio_id=1)
        ledger.start()
        preview = ledger.preview("BTC")
        receipt = ledger.sell("BTC", "uah-sale", preview["fingerprint"])
        self.assertEqual(receipt["currency"], "UAH")
        self.assertIn("realized_pnl", receipt)

    def test_migrated_observations_and_drawdown_are_isolated_by_portfolio(self):
        other_id = self._migrated_with_usdt()
        uah = Ledger(self.path, feed, portfolio_id=1)
        usdt = Ledger(self.path, feed, portfolio_id=other_id)
        uah.start()
        usdt.start()
        slot = "2026-11-19T12:00:00+00:00"
        first_uah = uah.record_valuation(slot=slot)
        first_usdt = usdt.record_valuation(slot=slot)
        duplicate = usdt.record_valuation(slot=slot)
        self.assertEqual(first_uah["status"], "recorded")
        self.assertEqual(first_usdt["status"], "recorded")
        self.assertEqual(duplicate["status"], "duplicate")
        self.assertEqual(len(uah.observation_history()), 1)
        self.assertEqual(len(usdt.observation_history()), 1)
        reopened = Ledger(self.path, portfolio_id=other_id)
        self.assertEqual(len(reopened.observation_history()), 1)


if __name__ == "__main__":
    unittest.main()

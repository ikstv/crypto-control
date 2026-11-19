"""Paper ledger reconciliation using isolated databases and a synthetic BTC book."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from unittest import TestCase
from unittest.mock import patch

from ledger import Ledger, OperationConflict
from portfolio import connect, create_portfolio, initialize, migrate_ledger_schema
from test_ledger import feed


class SaleJournalTests(TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "paper.sqlite3"
        initialize(self.path)
        self.stamp = datetime.now(timezone.utc).replace(microsecond=0)
        clock = patch("ledger.now", return_value=self.stamp)
        clock.start()
        self.addCleanup(clock.stop)

    def book(self, endpoint):
        data = feed(endpoint)
        if endpoint != "markets":
            data["timestamp"] = int(self.stamp.timestamp())
        return data

    def usdt_ledger(self, name="USDT test"):
        portfolio = create_portfolio(self.path, name, "USDT", "1000", name, migrate=False)
        with closing(connect(self.path)) as db, db:
            db.execute("INSERT INTO positions VALUES (?, 'BTC', 1000000000)", (portfolio["id"],))
        ledger = Ledger(self.path, self.book, portfolio_id=portfolio["id"])
        ledger.start()
        return ledger

    def test_full_close_with_reverse_sorting_ids_survives_reopen(self):
        migrate_ledger_schema(self.path)
        ledger = self.usdt_ledger()
        other = self.usdt_ledger("Second USDT")
        other_before = other.history()
        preview = ledger.preview("BTC", quantity=Decimal("1"))
        first = ledger.sell("BTC", "z-first", preview["fingerprint"], Decimal("1"))
        preview = ledger.preview("BTC")
        last = ledger.sell("BTC", "a-last", preview["fingerprint"])
        self.assertEqual(Decimal(last["sales"][0]["remaining_quantity"]), 0)
        self.assertEqual(first["sales"][0]["time"], last["sales"][0]["time"])

        for current in (ledger, Ledger(self.path, self.book, portfolio_id=ledger.portfolio_id)):
            journal = current.history()
            self.assertEqual(journal["holdings"], [])
            self.assertEqual(Decimal(journal["cash_usdt"]), Decimal("988.020990"))
            self.assertEqual(Decimal(journal["realized_pnl_usdt"]), Decimal("-11.979010"))
            self.assertEqual(journal["revision"], 2)
            self.assertEqual([op.get("operation_id") for op in journal["operations"]],
                             [None, "z-first", "a-last"])
            self.assertEqual(current.sell("BTC", "z-first", "expired", Decimal("1.0")), first)
            self.assertEqual(current.sell("BTC", "a-last", "expired"), last)
            self.assertEqual(current.history(), journal)
        self.assertEqual(other.history(), other_before)
        other.sell("BTC", "a-last", other.preview("BTC")["fingerprint"])
        self.assertEqual(other.history()["revision"], 1)
        self.assertEqual(ledger.history()["revision"], 2)

    def test_partial_receipt_matches_executed_quantity_and_retry_intent(self):
        migrate_ledger_schema(self.path)
        ledger = self.usdt_ledger()
        receipt = ledger.sell("BTC", "part", ledger.preview("BTC", quantity=Decimal("1"))["fingerprint"], Decimal("1"))
        sale, = receipt["sales"]
        self.assertEqual(Decimal(sale["fee_usdt"]), Decimal("0.099"))
        self.assertEqual(Decimal(sale["net_usdt"]), Decimal("98.901"))
        self.assertEqual(Decimal(sale["net_currency"]), Decimal("98.901000"))
        self.assertEqual(Decimal(sale["remaining_basis"]), Decimal("899.899900"))
        self.assertEqual(Decimal(sale["realized_pnl"]), Decimal("-1.199100"))
        before = ledger.history()
        for symbol, quantity in (("all", Decimal("1")), ("BTC", Decimal("2")), ("BTC", None)):
            with self.subTest(symbol=symbol, quantity=quantity), self.assertRaises(OperationConflict):
                ledger.sell(symbol, "part", "expired", quantity)
        self.assertEqual(ledger.history(), before)

    def test_native_uah_partial_sale_releases_cost_in_kopiykas(self):
        migrate_ledger_schema(self.path)
        ledger = Ledger(self.path, lambda endpoint: feed(endpoint, bid="100"))
        ledger.start()
        sale, = ledger.sell("BTC", "part", ledger.preview("BTC", quantity=Decimal(".1"))["fingerprint"],
                            Decimal("0.1"))["sales"]
        self.assertEqual(Decimal(sale["net_currency"]), Decimal("399.20"))
        self.assertEqual(Decimal(sale["cost_basis"]), Decimal("400.80"))
        self.assertEqual(Decimal(sale["remaining_basis"]), Decimal("599.00"))
        self.assertEqual(Decimal(sale["realized_pnl"]), Decimal("-1.60"))

    def test_inconsistent_historical_receipt_cannot_restore_sold_coins(self):
        migrate_ledger_schema(self.path)
        ledger = self.usdt_ledger()
        ledger.sell("BTC", "final", ledger.preview("BTC")["fingerprint"])
        with closing(connect(self.path)) as db, db:
            db.execute("""UPDATE paper_sales SET receipt =
                          json_set(receipt, '$.sales[0].remaining_quantity', '8.99')
                          WHERE portfolio_id=?""", (ledger.portfolio_id,))
        with self.assertRaises(OperationConflict):
            ledger.history()

    def test_legacy_partial_sale_and_subsequent_close_match_after_migration(self):
        legacy = Ledger(self.path, self.book)
        legacy.start()

        def limited_bid(endpoint):
            data = feed(endpoint, bid="100")
            if endpoint.startswith("orderbook/BTC_USDT"):
                data["bids"] = [["100", "0.10"]]
            return data

        legacy.fetch = limited_bid
        first = legacy.sell("BTC", "z-first", legacy.preview("BTC")["fingerprint"])
        with closing(connect(self.path)) as db, db:
            db.execute("UPDATE paper_sales SET rowid=17 WHERE operation_id='z-first'")
        before = Ledger(self.path).history()
        self.assertEqual(Decimal(before["holdings"][0]["quantity"]), Decimal("0.1494503"))
        self.assertEqual(Decimal(before["holdings"][0]["basis_uah"]), Decimal("599.00"))
        self.assertEqual(Decimal(before["cash_uah"]), Decimal("9399.40"))
        self.assertEqual(Decimal(before["realized_pnl_uah"]), Decimal("-1.60"))

        copy = self.path.with_name("migrated.sqlite3")
        with closing(connect(self.path)) as source, closing(connect(copy)) as destination:
            source.backup(destination)
        migrate_ledger_schema(copy)
        migrate_ledger_schema(copy)
        migrated = Ledger(copy, portfolio_id=1)
        after = migrated.history()
        for key in ("holdings", "operations", "cash_uah", "realized_pnl_uah", "revision"):
            self.assertEqual(after[key], before[key], key)
        self.assertEqual(migrated.sell("BTC", "z-first", "expired"), first)

        def finer_bid(endpoint):
            data = feed(endpoint, bid="100")
            if endpoint == "markets":
                data[0]["stockPrec"] = "8"
            return data

        for path in (self.path, copy):
            current = Ledger(path, finer_bid, portfolio_id=1)
            current.sell("BTC", "a-final", current.preview("BTC")["fingerprint"])
            final = Ledger(path, portfolio_id=1).history()
            self.assertEqual(final["holdings"], [])
            self.assertEqual(Decimal(final["cash_uah"]), Decimal("9996.00"))
            self.assertEqual(Decimal(final["realized_pnl_uah"]), Decimal("-4.00"))
            self.assertEqual([op.get("operation_id") for op in final["operations"]],
                             [None, "z-first", "a-final"])
        with closing(connect(self.path)) as source, closing(connect(copy)) as destination:
            sql = "SELECT rowid, receipt FROM paper_sales WHERE operation_id='z-first'"
            self.assertEqual(source.execute(sql).fetchone(), destination.execute(sql).fetchone())
            self.assertEqual(destination.execute("PRAGMA integrity_check").fetchall(), [("ok",)])
            self.assertEqual(destination.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_older_migrated_copy_replays_existing_receipt_without_rewriting_it(self):
        migrate_ledger_schema(self.path)
        ledger = self.usdt_ledger()
        # Actual pre-7B.1 layout and booked values for a 1 BTC partial sale.
        first = dict(operation_id="z-first", portfolio_id=ledger.portfolio_id, currency="USDT",
                     net_currency="98.901000", realized_pnl="-1.199100",
                     sales=[dict(symbol="BTC", side="sell", quantity="1", remaining_quantity="8.990",
                                 fee_usdt="0.989010", net_usdt="988.020990", net_currency="98.901000",
                                 cost_basis="100.100100", remaining_basis="899.899900", realized_pnl="-1.199100")])
        stored = json.dumps(first)
        with closing(connect(self.path)) as db, db:
            db.execute("DROP TABLE paper_sales")
            db.execute("""CREATE TABLE paper_sales (
                portfolio_id INTEGER NOT NULL REFERENCES portfolios(id), operation_id TEXT NOT NULL,
                symbol TEXT NOT NULL, receipt TEXT NOT NULL, PRIMARY KEY (portfolio_id, operation_id))""")
            db.execute("INSERT INTO paper_sales VALUES (?, 'z-first', 'BTC', ?)", (ledger.portfolio_id, stored))
        self.assertEqual(ledger.sell("BTC", "z-first", "expired", Decimal("1")), first)
        ledger.sell("BTC", "a-final", ledger.preview("BTC")["fingerprint"])
        reopened = Ledger(self.path, portfolio_id=ledger.portfolio_id).history()
        self.assertEqual(reopened["holdings"], [])
        self.assertEqual(Decimal(reopened["cash_usdt"]), Decimal("988.020990"))
        self.assertEqual(Decimal(reopened["realized_pnl_usdt"]), Decimal("-11.979010"))
        with closing(connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT receipt FROM paper_sales WHERE operation_id='z-first'").fetchone(),
                             (stored,))
            self.assertNotIn("sale_sequence", [row[1] for row in db.execute("PRAGMA table_info(paper_sales)")])

    def test_concurrent_confirmations_use_one_position_revision(self):
        migrate_ledger_schema(self.path)
        for keys in (("same", "same"), ("first", "second")):
            with self.subTest(keys=keys):
                ledger = self.usdt_ledger(keys[0])
                fingerprint = ledger.preview("BTC")["fingerprint"]
                quotes_requested = Barrier(2)

                def simultaneous_book(endpoint):
                    if endpoint == "markets":
                        quotes_requested.wait(timeout=5)
                    return self.book(endpoint)

                def confirm(key):
                    client = Ledger(self.path, simultaneous_book, portfolio_id=ledger.portfolio_id)
                    try:
                        return client.sell("BTC", key, fingerprint)
                    except OperationConflict:
                        return None

                with ThreadPoolExecutor(max_workers=2) as pool:
                    results = list(pool.map(confirm, keys))
                if keys[0] == keys[1]:
                    self.assertIsNotNone(results[0])
                    self.assertEqual(results[0], results[1])
                else:
                    self.assertEqual(results.count(None), 1)
                state = ledger.history()
                self.assertEqual(state["revision"], 1)
                self.assertEqual(state["holdings"], [])
                self.assertEqual(Decimal(state["cash_usdt"]), Decimal("988.020990"))

    def test_failed_receipt_insert_leaves_the_whole_journal_unchanged(self):
        migrate_ledger_schema(self.path)
        ledger = self.usdt_ledger()
        before = ledger.history()
        fingerprint = ledger.preview("BTC")["fingerprint"]
        with closing(connect(self.path)) as db, db:
            db.execute("""CREATE TRIGGER reject_receipt AFTER INSERT ON paper_sales
                          BEGIN SELECT RAISE(ABORT, 'simulated storage failure'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            ledger.sell("BTC", "retry", fingerprint)
        self.assertEqual(Ledger(self.path, portfolio_id=ledger.portfolio_id).history(), before)
        with closing(connect(self.path)) as db, db:
            db.execute("DROP TRIGGER reject_receipt")
        ledger.sell("BTC", "retry", fingerprint)
        self.assertEqual(ledger.history()["revision"], 1)

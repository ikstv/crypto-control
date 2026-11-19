"""Valuations against a timed synthetic book on isolated legacy and scoped databases."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from ledger import Ledger
from portfolio import connect, create_portfolio, initialize, migrate_ledger_schema
from test_ledger import feed


class EquityTests(TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "test.sqlite3"
        initialize(self.path)
        self.stamp = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
        self.bid = "99"
        self.fx = True
        self.stale = False
        self.precision = "4"
        clock = patch("ledger.now", side_effect=lambda: self.stamp)
        clock.start()
        self.addCleanup(clock.stop)

    def book(self, endpoint):
        result = feed(endpoint, bid=self.bid, fx=self.fx)
        if endpoint == "markets":
            result[0]["stockPrec"] = self.precision
        else:
            result["timestamp"] = int(self.stamp.timestamp()) - (120 if self.stale else 0)
        return result

    def usdt(self):
        portfolio = create_portfolio(self.path, "BTC test", "USDT", "1000", "btc-test", migrate=False)
        with closing(connect(self.path)) as db, db:
            db.execute("INSERT INTO positions VALUES (?, 'BTC', 1000000000)", (portfolio["id"],))
        return Ledger(self.path, self.book, portfolio_id=portfolio["id"])

    def test_closed_usdt_equity_and_daily_pnl_reconcile_without_quotes(self):
        migrate_ledger_schema(self.path)
        ledger = self.usdt()
        ledger.start()
        ledger.record_valuation()
        self.stamp += timedelta(minutes=4)
        ledger.sell("BTC", "part", ledger.preview("BTC", quantity=D("1"))["fingerprint"], quantity=D("1"))
        self.stamp += timedelta(minutes=1)
        partial = ledger.state()["valuation"]
        ledger.record_valuation()
        self.stamp += timedelta(minutes=4)
        ledger.sell("BTC", "close", ledger.preview("BTC")["fingerprint"])
        self.stamp += timedelta(minutes=1)

        def offline(endpoint):
            self.fail(f"All BTC has been sold, unexpected quote request: {endpoint}")

        reopened = Ledger(self.path, offline, portfolio_id=ledger.portfolio_id)
        record = reopened.record_valuation()
        self.assertEqual(record["status"], "recorded")
        self.assertIsNotNone(partial)
        self.assertEqual(D(partial["realized_pnl_usdt"]), D("-1.199100"))
        self.assertEqual(D(partial["unrealized_pnl_usdt"]), D("-10.779910"))
        self.assertEqual(D(partial["net_usdt"]), D("988.020990"))
        state = reopened.state()
        self.assertEqual(state["holdings"], [])
        self.assertEqual(state["cash_usdt"], "988.020990")
        self.assertEqual(state["valuation"]["net_usdt"], "988.020990")
        self.assertEqual(D(state["valuation"]["realized_pnl_usdt"]), D("-11.979010"))
        self.assertEqual(D(state["valuation"]["unrealized_pnl_usdt"]), 0)
        final = reopened.observation_history()[-1]
        self.assertEqual(final["equity"], "988.020990")
        self.assertIsNone(final.get("equity_uah"))
        self.assertEqual(final["currency"], "USDT")
        self.assertEqual(D(final["daily_pnl"]), 0)
        self.assertEqual(D(final["daily_realized_pnl"]), D("-11.979010"))
        self.assertEqual(D(final["daily_unrealized_pnl"]), D("11.979010"))
        self.assertEqual(len(reopened.observation_history()), 3)

    def test_peak_to_trough_survives_recovery_full_sale_restart_and_migration(self):
        ledger = Ledger(self.path, self.book)
        ledger.start()
        self.bid = "200"
        ledger.record_valuation()
        self.stamp += timedelta(minutes=5)
        self.bid = "100"
        ledger.record_valuation()
        peak, trough = ledger.observation_history()
        self.assertEqual(D(peak["equity_uah"]), D("10991.41"))
        self.assertEqual(D(trough["equity_uah"]), D("9995.80"))
        expected = (D("10991.41") - D("9995.80")) / D("10991.41") * 100
        self.assertEqual(D(trough["max_drawdown_percent"]), expected)
        self.assertEqual(D(trough["current_drawdown_percent"]), expected)
        self.assertEqual(D(trough["high_water_mark"]), D("10991.41"))
        self.assertEqual(D(trough["daily_pnl"]), D("-995.61"))
        self.assertEqual(D(trough["daily_pnl_percent"]), -expected)

        # Older workers only stored legacy observations and cached incorrect percentages.
        with closing(connect(self.path)) as db, db:
            db.execute("DELETE FROM paper_equity")
            db.execute("UPDATE paper_observations SET max_drawdown_percent='0.042', daily_pnl_percent='0'")
        copy = self.path.with_name("migrated.sqlite3")
        with closing(connect(self.path)) as source, closing(connect(copy)) as dest:
            source.backup(dest)
        migrate_ledger_schema(copy)
        for path in (self.path, copy):
            with self.subTest(path=path.name):
                reopened = Ledger(path, self.book)
                self.assertEqual(reopened.observation_history()[-1], trough)
                self.stamp += timedelta(minutes=5)
                self.bid = "210"
                self.precision = "8"
                reopened.sell("BTC", "close", reopened.preview("BTC")["fingerprint"])
                self.stamp += timedelta(minutes=5)
                recovery = reopened.record_valuation()
                self.assertEqual(recovery["status"], "recorded")
                value = Ledger(path, self.book).state()["valuation"]
                self.assertEqual(D(value["current_drawdown_percent"]), 0)
                self.assertEqual(D(value["high_water_mark"]), D(value["net_uah"]))
                self.assertGreater(D(value["net_uah"]), D("10991.41"))
                self.assertEqual(D(value["max_drawdown_percent"]), expected)
                self.assertEqual(D(value["unrealized_pnl_uah"]), 0)
                self.assertEqual(D(value["net_pnl_uah"]), D(value["realized_pnl_uah"]))
                with closing(connect(path)) as db:
                    self.assertEqual(db.execute("PRAGMA integrity_check").fetchone(), ("ok",))
                    self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_utc_day_with_only_skips_has_no_opening_equity_or_daily_return(self):
        ledger = Ledger(self.path, self.book)
        ledger.start()
        self.stamp = self.stamp.replace(hour=23, minute=55)
        ledger.record_valuation()
        self.stamp += timedelta(minutes=5)
        self.stale = True
        self.assertEqual(ledger.record_valuation()["status"], "stale")
        gap, = ledger.observation_history()[1:]
        self.assertIsNone(gap["equity_uah"])
        self.assertIsNone(gap["daily_pnl_percent"])
        self.stale = False
        self.stamp += timedelta(days=1, minutes=10)
        self.bid = "200"
        live = ledger.state()["valuation"]
        self.assertIsNone(live["daily_pnl_percent"])
        self.assertIsNone(live["daily_open_at"])
        self.assertEqual(len(ledger.observation_history()), 2)
        first = ledger.record_valuation()
        self.assertEqual(first["daily_open_at"], self.stamp.isoformat())
        self.assertEqual(D(first["daily_pnl"]), 0)
        self.stamp += timedelta(minutes=5)
        self.bid = "100"
        second = ledger.record_valuation()
        self.assertEqual(D(second["daily_pnl"]), D("-995.61"))
        self.assertEqual(second["daily_open_at"], first["daily_open_at"])

    def test_scoped_currency_fx_isolation_and_duplicate_observations(self):
        migrate_ledger_schema(self.path)
        uah = Ledger(self.path, self.book)
        usdt = self.usdt()
        self.assertFalse(uah.state()["started"])
        self.assertEqual(uah.record_valuation()["status"], "unavailable")
        self.assertIsNone(uah.observation_history()[0]["equity_uah"])
        uah.start()
        usdt.start()
        self.stamp += timedelta(minutes=5)
        self.fx = False
        self.assertIsNone(uah.state()["valuation"])
        self.assertEqual(uah.record_valuation()["status"], "unavailable")
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: usdt.record_valuation(), range(2)))
        self.assertCountEqual([r["status"] for r in results], ["recorded", "duplicate"])
        self.assertEqual(len(usdt.observation_history()), 1)
        self.stamp += timedelta(minutes=5)
        self.fx = True
        self.bid = "200"
        uah.record_valuation()
        self.bid = "99"
        usdt.record_valuation()
        self.assertEqual(D(usdt.state()["valuation"]["high_water_mark"]), D("1000"))
        self.assertEqual(D(uah.observation_history()[-1]["high_water_mark"]), D("10991.41"))
        self.assertEqual(D(usdt.observation_history()[-1]["daily_pnl"]), 0)
        self.assertTrue(all(r["currency"] == "UAH" for r in uah.observation_history()))
        with closing(connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM paper_equity WHERE portfolio_id=?",
                                        (usdt.portfolio_id,)).fetchone()[0], 2)

    def test_older_equity_rows_contribute_to_risk_without_fabricating_observations(self):
        ledger = Ledger(self.path, self.book)
        ledger.start()
        older = self.stamp - timedelta(days=1)
        with closing(connect(self.path)) as db, db:
            db.execute("INSERT INTO paper_equity (observed_at, day, equity_uah) VALUES (?, ?, '12000.00')",
                       (older.isoformat(), older.date().isoformat()))
        self.bid = "100"
        result = ledger.record_valuation()
        self.assertEqual(D(result["high_water_mark"]), D("12000.00"))
        self.assertEqual(D(result["daily_pnl"]), 0)
        self.assertEqual(len(ledger.observation_history()), 1)
        self.assertEqual(D(ledger.observation_history()[0]["max_drawdown_percent"]),
                         (D("12000") - D("9995.80")) / D("12000") * 100)

    def test_sale_while_pricing_cannot_commit_an_old_position_mark(self):
        migrate_ledger_schema(self.path)
        ledger = self.usdt()
        ledger.start()
        seller = Ledger(self.path, self.book, portfolio_id=ledger.portfolio_id)
        fingerprint = seller.preview("BTC")["fingerprint"]

        def sell_during_quote(endpoint):
            if endpoint == "markets":
                seller.sell("BTC", "close", fingerprint)
            return self.book(endpoint)

        ledger.fetch = sell_during_quote
        result = ledger.record_valuation()
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("changed", result["reason"].lower())
        self.assertIsNone(ledger.observation_history()[0]["equity"])
        self.stamp += timedelta(minutes=5)
        self.assertEqual(ledger.record_valuation()["equity"], "988.020990")

    def test_live_preview_does_not_create_a_historical_drawdown(self):
        ledger = Ledger(self.path, self.book)
        ledger.start()
        preview = ledger.state()["valuation"]
        self.assertEqual(D(preview["current_drawdown_percent"]), D("0.1416"))
        self.assertEqual(D(preview["max_drawdown_percent"]), 0)
        self.assertEqual(ledger.observation_history(), [])
        recorded = ledger.record_valuation()
        self.assertEqual(D(recorded["max_drawdown_percent"]), D("0.1416"))
        self.assertEqual(Ledger(self.path, self.book).state()["valuation"]["max_drawdown_percent"],
                         recorded["max_drawdown_percent"])

    def test_undated_historical_fill_does_not_invent_a_daily_pnl_split(self):
        migrate_ledger_schema(self.path)
        ledger = self.usdt()
        ledger.start()
        ledger.record_valuation()
        self.stamp += timedelta(minutes=4)
        ledger.sell("BTC", "old", ledger.preview("BTC", quantity=D("1"))["fingerprint"], quantity=D("1"))
        with closing(connect(self.path)) as db, db:
            db.execute("UPDATE paper_sales SET receipt=json_remove(receipt, '$.sales[0].time')")
        self.stamp += timedelta(minutes=1)
        mark = ledger.record_valuation()
        self.assertEqual(D(mark["daily_pnl"]), 0)
        self.assertIsNone(mark["daily_realized_pnl"])
        self.assertIsNone(mark["daily_unrealized_pnl"])
        self.assertEqual(D(ledger.state()["valuation"]["realized_pnl_usdt"]), D("-1.199100"))
        # A later dated sale bounds the undated fill. Subsequent days can be split again.
        self.stamp += timedelta(minutes=5)
        ledger.sell("BTC", "close", ledger.preview("BTC")["fingerprint"])
        self.stamp += timedelta(days=1)
        ledger.record_valuation()
        self.stamp += timedelta(minutes=5)
        mark = ledger.record_valuation()
        self.assertIsNone(mark["daily_realized_pnl"])
        self.assertIsNone(mark["daily_unrealized_pnl"])

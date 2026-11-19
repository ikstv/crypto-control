from decimal import Decimal
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest import TestCase

from ledger import Ledger, MarketUnavailable, OperationConflict
from portfolio import initialize
from test_ledger import feed


class SaleTests(TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "ledger.sqlite3"
        initialize(self.path)
        self.ledger = Ledger(self.path, feed)

    def test_pause_blocks_start_and_allows_close(self):
        self.ledger.configure(paused=True)
        with self.assertRaises(MarketUnavailable):
            self.ledger.start()
        self.ledger.configure(paused=False)
        self.ledger.start()
        self.ledger.configure(paused=True)
        self.sale("all")

    def sale(self, symbol="BTC", key="example"):
        preview = self.ledger.preview(symbol)
        return self.ledger.sell(symbol, key, preview["fingerprint"])

    def test_sale_fees_retry_and_statistics(self):
        self.ledger.start()
        self.ledger.fetch = lambda endpoint: feed(endpoint, bid="120")
        sale = self.sale()
        self.assertEqual(sale["sales"][0]["remaining_quantity"], "0.0000503")
        self.assertEqual(sale, self.ledger.sell("BTC", "example", "ignored"))
        state = self.ledger.state()
        self.assertEqual(len(state["operations"]), 2)
        value = state["valuation"]
        self.assertGreater(Decimal(value["realized_pnl_uah"]), 0)
        self.assertEqual(Decimal(value["realized_pnl_uah"]) + Decimal(value["unrealized_pnl_uah"]),
                         Decimal(value["net_pnl_uah"]))

    def test_full_close_reconciles_cost_cash_and_profit(self):
        def finer_sell_step(endpoint, bid="120"):
            response = feed(endpoint, bid=bid)
            if endpoint == "markets":
                response[0]["stockPrec"] = "8"
            return response

        self.ledger.start()
        self.ledger.state()
        self.ledger.fetch = finer_sell_step
        sale, = self.sale("all")["sales"]
        self.assertEqual(Decimal(sale["remaining_quantity"]), 0)
        self.assertEqual(sale["fee_usdt"], "0.0299340360")
        self.assertEqual(sale["net_uah"], "1194.96")
        self.assertEqual(sale["realized_pnl_uah"], "195.16")
        value = self.ledger.state()["valuation"]
        self.assertEqual(value["net_uah"], "10195.16")
        self.assertEqual(value["unrealized_pnl_uah"], "0.00")
        self.assertEqual(Decimal(value["max_drawdown_percent"]), Decimal("0.1416"))
        self.assertFalse(self.ledger.history()["holdings"])

    def test_thin_depth_sells_part_then_blocks_below_minimum(self):
        self.ledger.start()
        def thin(endpoint):
            data = feed(endpoint)
            if "BTC_USDT" in endpoint:
                data["bids"] = [["99", "0.06"], ["98", "0.04"]]
            return data

        self.ledger.fetch = thin
        sale, = self.sale()["sales"]
        self.assertEqual(Decimal(sale["quantity"]), Decimal("0.10"))
        self.assertEqual(Decimal(sale["remaining_quantity"]), Decimal("0.1494503"))
        self.assertEqual(len(sale["fills"]), 2)

        def no_depth(endpoint):
            data = feed(endpoint)
            if "BTC_USDT" in endpoint:
                data["bids"] = [["99", "0.00001"]]
            return data

        self.ledger.fetch = no_depth
        with self.assertRaises(MarketUnavailable):
            self.sale(key="no-fill")
        self.assertEqual(len(self.ledger.history()["operations"]), 2)

    def test_changed_or_stale_quotes_do_not_write_sales(self):
        self.ledger.start()
        preview = self.ledger.preview("BTC")
        self.ledger.fetch = lambda endpoint: feed(endpoint, bid="98")
        with self.assertRaises(OperationConflict):
            self.ledger.sell("BTC", "changed", preview["fingerprint"])

        def stale(endpoint):
            data = feed(endpoint)
            if "USDT_UAH" in endpoint:
                data["timestamp"] -= 60
            return data

        self.ledger.fetch = stale
        with self.assertRaises(MarketUnavailable):
            self.ledger.sell("BTC", "stale", preview["fingerprint"])
        self.assertEqual(len(self.ledger.history()["operations"]), 1)

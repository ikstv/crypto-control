import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from ledger import Ledger, MarketUnavailable
from portfolio import initialize


def feed(endpoint, bid="99", fx=True):
    rules = dict(tradesEnabled=True, type="spot", stockPrec="4", moneyPrec="2",
                 minAmount="0.0001", minTotal="1", maxTotal="0", takerFee="0.1")
    if endpoint == "markets":
        return [dict(rules, name="BTC_USDT", stock="BTC", money="USDT")] + (
            [dict(rules, name="USDT_UAH", stock="USDT", money="UAH")] if fx else [])
    pair = endpoint.split("/")[1].split("?")[0]
    return dict(ticker_id=pair, timestamp=int(datetime.now(timezone.utc).timestamp()),
                asks=[["40" if pair == "USDT_UAH" else str(max(100, int(bid))), "1000"]],
                bids=[["40" if pair == "USDT_UAH" else bid, "1000"]])


class LedgerTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "db"
        initialize(self.path)

    def test_no_fx_means_no_operations(self):
        ledger = Ledger(self.path, lambda endpoint: feed(endpoint, fx=False))
        with self.assertRaises(MarketUnavailable):
            ledger.start()
        self.assertFalse(ledger.history()["started"])
        self.assertEqual(ledger.history()["operations"], [])

    def test_fees_loss_price_change_and_persistence(self):
        ledger = Ledger(self.path, feed)
        self.assertEqual(ledger.history()["cash_uah"], "10000")
        first = ledger.start()
        operation, = first["operations"]
        self.assertEqual(operation["quantity"], "0.2494503")
        self.assertEqual(operation["fee_base"], "0.0002497")
        self.assertEqual(first["cash_uah"], "9000.20")
        reopened = Ledger(self.path, feed)
        self.assertEqual(reopened.start(), first)
        self.assertEqual(reopened.state()["valuation"]["net_pnl_uah"], "-14.16")
        higher = Ledger(self.path, lambda endpoint: feed(endpoint, bid="199"))
        self.assertGreater(float(higher.state()["valuation"]["net_pnl_uah"]), 0)
        self.assertTrue(higher.state()["valuation"]["target_reached"])

    def test_stale_fx_blocks_valuation_without_altering_history(self):
        first = Ledger(self.path, feed).start()

        def stale(endpoint):
            data = feed(endpoint)
            if "USDT_UAH" in endpoint:
                data["timestamp"] -= 120
            return data

        state = Ledger(self.path, stale).state()
        self.assertIsNone(state["valuation"])
        self.assertIn("stale", state["warning"])
        self.assertEqual(state["operations"], first["operations"])

    def test_purchase_uses_more_than_one_ask_level(self):
        def depth(endpoint):
            data = feed(endpoint)
            if "BTC_USDT" in endpoint:
                data["asks"] = [["100", "0.10"], ["101", "1"]]
            return data

        operation, = Ledger(self.path, depth).start()["operations"]
        self.assertEqual(operation["fills"], [
            {"price": "100", "quantity": "0.10"},
            {"price": "101", "quantity": "0.1482"},
        ])

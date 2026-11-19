import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
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
    def test_single_level_fx_purchase_reconciles_cash_and_fees(self):
        result = Ledger(self.path, feed).start()
        purchase, = result["operations"]
        self.assertEqual(Decimal(result["cash_uah"]), Decimal("9000.20"))
        self.assertEqual(Decimal(purchase["debit_uah"]), Decimal("999.80"))
        self.assertEqual(Decimal(purchase["fx_received_usdt"]), Decimal("24.97"))
        self.assertEqual(Decimal(purchase["fx_fee_usdt"]), Decimal("24.97") / Decimal("0.999") - Decimal("24.97"))
        self.assertEqual(Decimal(purchase["quantity"]), Decimal("0.2494503"))
        self.assertEqual(Decimal(purchase["fee_base"]), Decimal("0.0002497"))

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

    def test_purchase_rejects_full_target_when_second_ask_exceeds_budget(self):
        def depth(endpoint):
            data = feed(endpoint)
            if "BTC_USDT" in endpoint:
                data["asks"] = [["100", "0.10"], ["101", "1"]]
            return data

        ledger = Ledger(self.path, depth)
        before = ledger.state()
        with self.assertRaisesRegex(MarketUnavailable, "exceeds the USDT budget"):
            ledger.start()
        self.assertEqual(Ledger(self.path, depth).state(), before)

    def test_uah_start_prices_the_entire_fx_conversion_across_ask_levels(self):
        def depth(endpoint):
            data = feed(endpoint)
            if endpoint.startswith("orderbook/USDT_UAH"):
                data["asks"] = [["40", "10"], ["41", "1000"]]
            return data

        result = Ledger(self.path, depth).start()
        spent = sum((Decimal(op["debit_uah"]) for op in result["operations"]), Decimal(0))
        self.assertEqual(spent + Decimal(result["cash_uah"]), Decimal("10000"))
        self.assertEqual(Decimal(result["operations"][0]["debit_uah"]), Decimal("999.61"))
        self.assertEqual(Decimal(result["operations"][0]["fx_acquired_usdt"]), Decimal("24.60") / Decimal("0.999"))
        self.assertEqual(Decimal(result["operations"][0]["fx_received_usdt"]), Decimal("24.60"))
        self.assertEqual(Decimal(result["operations"][0]["total_usdt"]), Decimal("24.60"))
        self.assertEqual(Decimal(result["operations"][0]["unused_usdt"]), Decimal("0"))
        self.assertGreater(Decimal(result["operations"][0]["fx_worst_price"]), Decimal("40"))

    def test_uah_start_rejects_insufficient_multilevel_fx_depth_atomically(self):
        def thin_fx(endpoint):
            data = feed(endpoint)
            if endpoint.startswith("orderbook/USDT_UAH"):
                data["asks"] = [["40", "0.5"], ["41", "0.5"]]
            return data

        with self.assertRaises(MarketUnavailable):
            Ledger(self.path, thin_fx).start()
        self.assertFalse(Ledger(self.path).history()["started"])
        self.assertEqual(Ledger(self.path).history()["operations"], [])

    def test_uah_start_rejects_insufficient_crypto_depth_atomically(self):
        def thin_crypto(endpoint):
            data = feed(endpoint)
            if endpoint.startswith("orderbook/BTC_USDT"):
                data["asks"] = [["100", "0.01"]]
            return data

        ledger = Ledger(self.path, thin_crypto)
        before = ledger.state()
        with self.assertRaisesRegex(MarketUnavailable, "Insufficient crypto ask depth"):
            ledger.start()
        self.assertEqual(Ledger(self.path, thin_crypto).state(), before)

    def test_record_valuation_is_idempotent_and_history_is_read_only(self):
        ledger = Ledger(self.path, feed)
        ledger.start()
        before = ledger.observation_history()
        first = ledger.record_valuation(slot="2026-11-19T12:00:00Z")
        second = ledger.record_valuation(slot="2026-11-19T12:00:00Z")
        self.assertEqual(first["status"], "recorded")
        self.assertEqual(second["status"], "duplicate")
        self.assertEqual(len(ledger.observation_history()), len(before) + 1)
        ledger.state()
        self.assertEqual(len(ledger.observation_history()), len(before) + 1)

    def test_record_skip_preserves_gap_reason(self):
        ledger = Ledger(self.path, feed)
        ledger.start()
        result = ledger.record_skip("WhiteBIT unavailable", slot="2026-11-19T12:05:00Z")
        self.assertEqual(result, {"status": "unavailable", "reason": "WhiteBIT unavailable"})
        row = ledger.observation_history()[-1]
        self.assertEqual(row["status"], "unavailable")
        self.assertEqual(row["reason"], "WhiteBIT unavailable")

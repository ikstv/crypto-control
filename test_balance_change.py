from decimal import Decimal as D
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from balance_change import explain, snapshot
from ledger import Ledger
from portfolio import initialize
from test_ledger import feed


def market_feed(mark=99, rate=40, thin=False, stale=False):
    def fetch(endpoint):
        data = feed(endpoint, bid=str(mark))
        if "USDT_UAH" in endpoint:
            data["asks"][0][0] = str(rate)
            data["bids"][0][0] = str(rate)
            if stale:
                data["timestamp"] -= 120
        elif "BTC_USDT" in endpoint and thin:
            data["bids"][0][1] = "0.06"
        return data
    return fetch


class BalanceChangeTests(TestCase):
    def test_each_factor_and_rounding_to_the_cent(self):
        def observed(bid, ask, fx_bid, fx_ask, proceeds, fee, fx_fee, net):
            row = dict(available_quantity=D(2), bid=D(bid), ask=D(ask),
                       gross_usdt=D(proceeds), fee_usdt=D(fee), fx_fee_uah=D(fx_fee))
            value = dict(fx=dict(bid=D(fx_bid), ask=D(fx_ask)), positions=[row],
                         assets_usdt=2 * D(bid), net_uah=D(net))
            return snapshot(dict(revision=0), value, "2025-01-01T00:00:00Z")

        before = observed(99, 101, 40, 42, 198, ".198", "7.91208", "7904.16")
        after = observed(108, 112, 41, 45, 180, ".36", "7.36524", "7357.87")
        result = explain(before, after)
        self.assertEqual(result["effects"], {
            "crypto": D("820"), "fx": D("440"), "spread": D("-324"),
            "liquidity": D("-1476"), "fees": D("-6.29"), "rounding": D("0.00"),
        })
        self.assertEqual(sum(result["effects"].values()), result["total_uah"])

    def test_rounding_reconciles_independently_rounded_components(self):
        before = dict(at="before", revision=0, net=D("1.00"), assets=D("1"), mid=D("1"),
                      spread=D(0), liquidity=D(0), fees=D(0))
        after = dict(before, at="after", assets=D("1.004"), mid=D("1.004"), net=D("1.01"))
        result = explain(before, after)
        self.assertEqual(result["effects"]["rounding"], D(".01"))
        self.assertEqual(sum(result["effects"].values()), result["total_uah"])

    def test_sale_between_valuations_is_not_attributed_to_market(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "test.sqlite3"
            initialize(path)
            ledger = Ledger(path, market_feed())
            ledger.start()
            before = ledger.state()["valuation"]
            self.assertIsNone(before["balance_change"])
            preview = ledger.preview("BTC")
            ledger.sell("BTC", "example-sale", preview["fingerprint"])
            after = ledger.state()["valuation"]
            change = after["balance_change"]
            self.assertTrue(change["operations_between"])
            self.assertIsNone(change["effects"])
            self.assertEqual(D(change["total_uah"]), D(after["net_uah"]) - D(before["net_uah"]))
            self.assertFalse(ledger.state()["valuation"]["balance_change"]["operations_between"])

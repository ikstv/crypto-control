import unittest
from urllib.error import HTTPError, URLError
from unittest.mock import Mock

from quotes import Quotes


def market(symbol="BTC", enabled=True, kind="spot"):
    return dict(name=f"{symbol}_USDT", stock=symbol, money="USDT",
                tradesEnabled=enabled, type=kind)


class QuoteTests(unittest.TestCase):
    def test_filters_markets_and_preserves_decimal_precision(self):
        fetch = Mock(side_effect=[
            [market(), market("ETH", False), market("SOL", kind="futures")],
            {f"{s}_USDT": {"last_price": "1.000000000000000001"} for s in ("BTC", "ETH", "SOL")},
        ])
        quotes = Quotes(fetch)
        quotes.refresh()
        snapshot = quotes.snapshot()
        self.assertEqual([call.args[0] for call in fetch.call_args_list], ["markets", "ticker"])
        self.assertEqual(len(snapshot["quotes"]), 9)
        self.assertEqual(snapshot["quotes"][0]["price"], "1.000000000000000001")
        self.assertTrue(snapshot["quotes"][0]["received_at"])
        self.assertTrue(all(row["price"] is None for row in snapshot["quotes"][1:]))

    def test_failure_keeps_price_and_timestamp(self):
        for failure, status in [(TimeoutError(), "timeout"), (URLError("offline"), "offline"),
                                (HTTPError("", 429, "", {"Retry-After": "90"}, None), "rate_limited"),
                                (ValueError(), "invalid_response")]:
            with self.subTest(status=status):
                quotes = Quotes(Mock(side_effect=[[market()], {"BTC_USDT": {"last_price": "12"}}, failure]))
                quotes.refresh()
                before = quotes.snapshot()["quotes"][0]
                delay = quotes.refresh()
                snapshot = quotes.snapshot()
                self.assertEqual(snapshot["connection"], status)
                self.assertEqual(snapshot["quotes"][0], dict(before, status="stale"))
                if status == "rate_limited":
                    self.assertEqual(delay, 90)

    def test_partial_invalid_prices_and_removed_market(self):
        quotes = Quotes(Mock(side_effect=[
            [market(), market("ETH")], {"BTC_USDT": {"last_price": "12"}, "ETH_USDT": {"last_price": "4"}},
            [market()], {"BTC_USDT": {"last_price": "NaN"}},
        ]))
        quotes.refresh()
        quotes.refresh()
        btc, eth, *_ = quotes.snapshot()["quotes"]
        self.assertEqual(btc["status"], "stale")
        self.assertEqual(eth["status"], "market_unavailable")
        self.assertIsNone(eth["price"])

import unittest

from whitebit_demo_policy import DemoPolicyError, validate_private_request


HOST = "https://whitebit.com"


def request(path, **body):
    return validate_private_request(
        "POST", HOST + path, {"request": path, "nonce": 1, **body}
    )


class WhitebitDemoPolicyTests(unittest.TestCase):
    def test_allows_only_demo_balance_and_pair_scoped_order_reads(self):
        self.assertIsNone(request("/api/v4/trade-account/balance", ticker="DBTC"))
        self.assertIsNone(request("/api/v4/trade-account/balance", ticker="DUSDT"))
        self.assertIsNone(request("/api/v4/orders", market="DBTC_DUSDT"))
        self.assertIsNone(request(
            "/api/v4/trade-account/order/history", market="DBTC_DUSDT"
        ))
        self.assertIsNone(request(
            "/api/v4/trade-account/executed-history", market="DBTC_DUSDT"
        ))

    def test_allows_only_spot_limit_orders_for_the_demo_pair(self):
        self.assertIsNone(request(
            "/api/v4/order/new", market="DBTC_DUSDT", side="buy",
            amount="0.001", price="60000"
        ))

    def test_allows_cancel_only_when_market_is_the_demo_pair(self):
        self.assertIsNone(request(
            "/api/v4/order/cancel", market="DBTC_DUSDT", orderId=123
        ))

    def test_rejects_other_markets_and_non_post_requests(self):
        path = "/api/v4/order/new"
        for market in ("BTC_USDT", "DBTC_BTC", "dbtc_dusdt", "DBTC_DUSDT "):
            with self.subTest(market=market), self.assertRaises(DemoPolicyError):
                request(path, market=market, side="buy", amount="0.01", price="1")
        with self.assertRaises(DemoPolicyError):
            validate_private_request("GET", HOST + "/api/v4/orders", {
                "request": "/api/v4/orders", "nonce": 1, "market": "DBTC_DUSDT"
            })

    def test_rejects_non_whitebit_hosts_queries_and_non_https_urls(self):
        for url in (
            "http://whitebit.com/api/v4/orders",
            "https://whitebit.com.evil.test/api/v4/orders",
            "https://evil.test/api/v4/orders",
            "https://whitebit.com/api/v4/orders?market=DBTC_DUSDT",
        ):
            with self.subTest(url=url), self.assertRaises(DemoPolicyError):
                validate_private_request("POST", url, {
                    "request": "/api/v4/orders", "nonce": 1, "market": "DBTC_DUSDT"
                })

    def test_signed_request_path_must_match_url_and_nonce_must_be_present(self):
        url = HOST + "/api/v4/trade-account/balance"
        for payload in (
            {"request": "/api/v4/order/new", "nonce": 1, "ticker": "DBTC"},
            {"request": "/api/v4/trade-account/balance", "ticker": "DBTC"},
        ):
            with self.subTest(payload=payload), self.assertRaises(DemoPolicyError):
                validate_private_request("POST", url, payload)

    def test_rejects_transfers_withdrawals_margin_futures_and_unknown_endpoints(self):
        for path in (
            "/api/v4/main-account/transfer", "/api/v4/main-account/withdraw",
            "/api/v4/collateral/order/new", "/api/v4/order/market",
            "/api/v4/futures/order/new", "/api/v4/convert/confirm",
        ):
            with self.subTest(path=path), self.assertRaises(DemoPolicyError):
                request(path, market="DBTC_DUSDT")

    def test_rejects_unfiltered_reads_or_non_demo_balances(self):
        with self.assertRaises(DemoPolicyError):
            request("/api/v4/trade-account/balance")
        with self.assertRaises(DemoPolicyError):
            request("/api/v4/trade-account/balance", ticker="BTC")
        with self.assertRaises(DemoPolicyError):
            request("/api/v4/orders")

    def test_rejects_ambiguous_cancel_and_non_spot_order_payloads(self):
        for identifiers in ({}, {"orderId": 1, "clientOrderId": "x"}):
            with self.subTest(identifiers=identifiers), self.assertRaises(DemoPolicyError):
                request("/api/v4/order/cancel", market="DBTC_DUSDT", **identifiers)
        with self.assertRaises(DemoPolicyError):
            request("/api/v4/order/new", market="DBTC_DUSDT", side="buy",
                    amount="1", price="10", type="futures")
        with self.assertRaises(DemoPolicyError):
            request("/api/v4/order/new", market="DBTC_DUSDT", side="borrow",
                    amount="1", price="10")
        for amount in ("0", "-1", "NaN", "Infinity", True):
            with self.subTest(amount=amount), self.assertRaises(DemoPolicyError):
                request("/api/v4/order/new", market="DBTC_DUSDT", side="buy",
                        amount=amount, price="10")


if __name__ == "__main__":
    unittest.main()

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from whitebit_demo_api import (
    DemoCredentialsError,
    WhitebitAPIError,
    WhitebitDemoClient,
    load_credentials,
    main,
    sign_payload,
)
from whitebit_demo_policy import DemoPolicyError


class WhitebitDemoApiTests(unittest.TestCase):
    def test_signing_matches_whitebit_hmac_sha512_wire_format(self):
        payload = '{"request":"/api/v4/trade-account/balance","nonce":1700000000000,"ticker":"DBTC"}'
        encoded, signature = sign_payload(payload, "test-secret")
        self.assertEqual(
            encoded,
            "eyJyZXF1ZXN0IjoiL2FwaS92NC90cmFkZS1hY2NvdW50L2JhbGFuY2UiLCJub25jZSI6MTcwMDAwMDAwMDAwMCwidGlja2VyIjoiREJUQyJ9",
        )
        self.assertEqual(
            signature,
            "013c04ef6c8824ed8cca77ebcba7173d9fd1742c167dadff6da73ce5cfa1b2ce92d0b80156b6dd128f1019a6cb4d27e09857c09fe260811f1e2af4c5f3d82175",
        )

    def test_demo_status_reads_only_pair_balance_orders_and_execution_history(self):
        calls = []

        def transport(method, url, headers, body, timeout):
            calls.append((method, url, headers, body))
            if method == "GET":
                return 200, b'{"time":1700000000}'
            payload = json.loads(body)
            path = payload["request"]
            if path == "/api/v4/trade-account/balance":
                ticker = payload["ticker"]
                balance = {"available": "12.5", "freeze": "1.25"}
                return 200, json.dumps(balance if ticker == "DBTC" else {ticker: balance}).encode()
            if path == "/api/v4/trade-account/order/history":
                return 200, b'{"DBTC_DUSDT":[{"orderId":6,"market":"DBTC_DUSDT","status":"FILLED"}]}'
            if path == "/api/v4/trade-account/executed-history":
                return 200, b'[{"id":5,"market":"DBTC_DUSDT","side":"buy","dealMoney":"60"}]'
            raise AssertionError(f"unexpected route: {path}")

        result = WhitebitDemoClient("public-key", "secret-key", transport=transport).demo_status()

        self.assertEqual(result["pair"], "DBTC_DUSDT")
        self.assertEqual(result["balances"]["DBTC"], {"available": "12.5", "freeze": "1.25"})
        self.assertEqual(result["balances"]["DUSDT"]["available"], "12.5")
        self.assertEqual(result["order_history_count"], 1)
        self.assertEqual(result["executed_deals_count"], 1)
        self.assertEqual(result["pnl"]["status"], "unavailable")
        self.assertIn("same WhiteBIT account", result["account_scope_note"])
        self.assertEqual(sum(call[0] == "POST" for call in calls), 4)
        self.assertTrue(all(call[0] != "POST" or call[1].startswith("https://whitebit.com/api/v4/") for call in calls))
        self.assertTrue(all("X-TXC-APIKEY" not in call[2] or call[2]["X-TXC-APIKEY"] == "public-key" for call in calls))
        paths = [json.loads(call[3])["request"] for call in calls if call[0] == "POST"]
        self.assertEqual(paths, [
            "/api/v4/trade-account/balance", "/api/v4/trade-account/balance",
            "/api/v4/trade-account/order/history",
            "/api/v4/trade-account/executed-history",
        ])

    def test_write_and_unallowlisted_requests_are_rejected_before_transport(self):
        calls = []
        client = WhitebitDemoClient(
            "public-key", "secret-key", transport=lambda *args: calls.append(args)
        )
        with self.assertRaises(DemoPolicyError):
            client._private("/api/v4/order/new", {
                "market": "DBTC_DUSDT", "side": "buy", "amount": "1", "price": "1"
            })
        with self.assertRaises(DemoPolicyError):
            client._private("/api/v4/main-account/withdraw", {})
        with self.assertRaises(DemoPolicyError):
            client._private("/api/v4/orders", {"market": "DBTC_DUSDT"})
        with self.assertRaises(DemoPolicyError):
            client._private("/api/v4/orders", {"market": "BTC_USDT"})
        self.assertEqual(calls, [])

    def test_api_key_and_secret_are_loaded_from_systemd_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "whitebit_demo_api_key").write_text("read-key\n", encoding="utf-8")
            Path(directory, "whitebit_demo_api_secret").write_text("read-secret\n", encoding="utf-8")
            key, secret = load_credentials({"CREDENTIALS_DIRECTORY": directory})
        self.assertEqual((key, secret), ("read-key", "read-secret"))

    def test_environment_credentials_are_supported_without_persisting_them(self):
        key, secret = load_credentials({
            "WHITEBIT_DEMO_API_KEY": "read-key", "WHITEBIT_DEMO_API_SECRET": "read-secret"
        })
        self.assertEqual((key, secret), ("read-key", "read-secret"))

    def test_missing_credentials_have_a_clear_error_without_echoing_secrets(self):
        with self.assertRaises(DemoCredentialsError) as caught:
            load_credentials({})
        self.assertNotIn("actual-secret-value", str(caught.exception))

    def test_private_http_error_does_not_echo_credentials_or_response_body(self):
        def transport(method, url, headers, body, timeout):
            if method == "GET":
                return 200, b'{"time":1700000000}'
            return 403, b'{"message":"denied secret-key"}'

        client = WhitebitDemoClient("public-key", "secret-key", transport=transport)
        with self.assertRaises(WhitebitAPIError) as caught:
            client.read_balance("DBTC")
        self.assertNotIn("secret-key", str(caught.exception))
        self.assertNotIn("public-key", str(caught.exception))

    def test_demo_status_cli_is_read_only_and_json_output_has_no_credentials(self):
        with patch.dict(os.environ, {
            "WHITEBIT_DEMO_API_KEY": "hidden-public", "WHITEBIT_DEMO_API_SECRET": "hidden-secret"
        }), patch("whitebit_demo_api.WhitebitDemoClient.demo_status", return_value={
            "status": "ok", "pair": "DBTC_DUSDT", "pnl": {"status": "unavailable"}
        }), patch("sys.stdout") as stdout:
            result = main(["demo-status"])
        self.assertEqual(result, 0)
        output = "".join(call.args[0] for call in stdout.write.call_args_list if call.args)
        self.assertIn('"pair": "DBTC_DUSDT"', output)
        self.assertNotIn("hidden-public", output)
        self.assertNotIn("hidden-secret", output)

    def test_demo_status_reports_partial_scopes_without_requesting_write_scope(self):
        requested_paths = []

        def transport(method, url, headers, body, timeout):
            if method == "GET":
                return 200, b'{"time":1700000000}'
            request = json.loads(body)
            path = request["request"]
            requested_paths.append(path)
            if path == "/api/v4/trade-account/balance":
                ticker = request["ticker"]
                return 200, json.dumps({ticker: {"available": "2", "freeze": "0"}}).encode()
            return 403, b'{"message":"permission denied"}'

        result = WhitebitDemoClient("read-key", "read-secret", transport=transport).demo_status()

        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["balances"]["DBTC"]["available"], "2")
        self.assertEqual(result["balances"]["DUSDT"]["available"], "2")
        self.assertIsNone(result["order_history"])
        self.assertIsNone(result["executed_deals"])
        self.assertEqual(set(result["errors"]), {"order_history", "executed_deals"})
        self.assertEqual(requested_paths, [
            "/api/v4/trade-account/balance",
            "/api/v4/trade-account/balance",
            "/api/v4/trade-account/order/history",
            "/api/v4/trade-account/executed-history",
        ])
        self.assertFalse(any(path in {
            "/api/v4/orders", "/api/v4/order/new", "/api/v4/order/cancel"
        } for path in requested_paths))

    def test_empty_balance_stays_unavailable(self):
        def transport(method, url, headers, body, timeout):
            return 200, b'{"time":1700000000}' if method == "GET" else b'{}'

        client = WhitebitDemoClient("test-key", "test-secret", transport=transport)
        with self.assertRaisesRegex(WhitebitAPIError, "no DBTC trade balance entry"):
            client.read_balance("DBTC")

    def test_total_private_failure_keeps_safe_route_diagnostics(self):
        def transport(method, url, headers, body, timeout):
            if method == "GET":
                return 200, b'{"time":1700000000}'
            return 401, b'{"message":"sensitive response"}'

        result = WhitebitDemoClient("test-key", "test-secret", transport=transport).demo_status()

        self.assertEqual(result["status"], "error")
        self.assertEqual(set(result["errors"]), {
            "balance_DBTC", "balance_DUSDT", "order_history", "executed_deals",
        })
        self.assertEqual(set(result["errors"].values()), {"WhiteBIT private read returned HTTP 401"})
        self.assertNotIn("sensitive response", json.dumps(result))

    def test_demo_status_cli_reports_missing_credentials_clearly(self):
        with patch.dict(os.environ, {}, clear=True), patch("sys.stderr") as stderr:
            result = main(["demo-status"])
        self.assertEqual(result, 2)
        output = "".join(call.args[0] for call in stderr.write.call_args_list if call.args)
        self.assertIn('"status": "not_configured"', output)
        self.assertIn("WHITEBIT_DEMO_API_KEY", output)


if __name__ == "__main__":
    unittest.main()

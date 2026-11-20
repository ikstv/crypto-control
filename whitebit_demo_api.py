"""Read-only WhiteBIT Demo Token account status.

No write methods are exposed. Every authenticated request passes the existing
fail-closed request policy immediately before transport dispatch.
"""
import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from whitebit_demo_policy import (
    DEMO_ASSETS,
    DEMO_MARKET,
    DemoPolicyError,
    validate_private_request,
)


API_BASE = "https://whitebit.com"
TIME_PATH = "/api/v4/public/time"
BALANCE_PATH = "/api/v4/trade-account/balance"
ORDER_HISTORY_PATH = "/api/v4/trade-account/order/history"
EXECUTION_HISTORY_PATH = "/api/v4/trade-account/executed-history"
READ_ONLY_PATHS = frozenset({
    BALANCE_PATH, ORDER_HISTORY_PATH, EXECUTION_HISTORY_PATH,
})
PAGE_SIZE = 100


class DemoCredentialsError(RuntimeError):
    """Read-only API credentials are not configured."""


class WhitebitAPIError(RuntimeError):
    """A WhiteBIT read-only request failed without exposing its response body."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def sign_payload(payload_json, api_secret):
    """Return WhiteBIT's Base64 body and HMAC-SHA512 signature."""
    encoded = base64.b64encode(payload_json.encode("utf-8")).decode("ascii")
    signature = hmac.new(
        api_secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha512
    ).hexdigest()
    return encoded, signature


def load_credentials(environ=None):
    """Load secrets from systemd credentials, or explicit process env vars.

    A configured systemd credential directory is authoritative: missing files
    there do not silently fall back to possibly stale environment variables.
    """
    source = os.environ if environ is None else environ
    credentials_dir = source.get("CREDENTIALS_DIRECTORY")
    if credentials_dir:
        from pathlib import Path

        directory = Path(credentials_dir)
        key_path = directory / "whitebit_demo_api_key"
        secret_path = directory / "whitebit_demo_api_secret"
        try:
            key = key_path.read_text(encoding="utf-8").strip()
            secret = secret_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise DemoCredentialsError(
                "WhiteBIT Demo API credential files are unavailable"
            ) from None
    else:
        key = source.get("WHITEBIT_DEMO_API_KEY", "").strip()
        secret = source.get("WHITEBIT_DEMO_API_SECRET", "").strip()
    if not key or not secret:
        raise DemoCredentialsError(
            "Set WHITEBIT_DEMO_API_KEY and WHITEBIT_DEMO_API_SECRET, or provide "
            "systemd credential files whitebit_demo_api_key and whitebit_demo_api_secret"
        )
    return key, secret


def _http_transport(method, url, headers, body, timeout):
    request = Request(url, data=body, headers=headers or {}, method=method)
    opener = build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.status, response.read()
    except HTTPError as error:
        return error.code, error.read()
    except (URLError, TimeoutError, OSError):
        raise WhitebitAPIError("Could not reach WhiteBIT API") from None


class WhitebitDemoClient:
    """Read-only client for DBTC/DUSDT balances and spot order activity."""

    def __init__(self, api_key, api_secret, *, transport=None, timeout=10):
        if not api_key or not api_secret:
            raise DemoCredentialsError("WhiteBIT Demo API credentials are required")
        self._api_key = str(api_key)
        self._api_secret = str(api_secret)
        self._transport = transport or _http_transport
        self._timeout = timeout
        self._nonce_lock = threading.Lock()
        self._clock_offset_ms = None
        self._last_nonce = 0

    def _send(self, method, url, headers=None, body=None):
        try:
            status, response_body = self._transport(
                method, url, headers or {}, body, self._timeout
            )
        except WhitebitAPIError:
            raise
        except Exception:
            raise WhitebitAPIError("WhiteBIT request transport failed") from None
        if not isinstance(status, int) or not isinstance(response_body, (bytes, bytearray)):
            raise WhitebitAPIError("WhiteBIT transport returned an invalid response")
        if status < 200 or status >= 300:
            raise WhitebitAPIError(f"WhiteBIT API returned HTTP {status}")
        try:
            result = json.loads(response_body)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
            raise WhitebitAPIError("WhiteBIT API returned invalid JSON") from None
        if isinstance(result, dict) and (
            result.get("success") is False or "errors" in result
        ):
            code = result.get("code")
            suffix = f" (code {code})" if isinstance(code, (int, str)) else ""
            raise WhitebitAPIError(f"WhiteBIT rejected the read-only request{suffix}")
        return result

    def _sync_server_clock(self):
        before_ms = time.time_ns() // 1_000_000
        data = self._send("GET", API_BASE + TIME_PATH)
        after_ms = time.time_ns() // 1_000_000
        try:
            server_ms = int(data["time"]) * 1000
        except (KeyError, TypeError, ValueError, OverflowError):
            raise WhitebitAPIError("WhiteBIT server-time response is invalid") from None
        midpoint = (before_ms + after_ms) // 2
        self._clock_offset_ms = server_ms - midpoint

    def _next_nonce(self):
        with self._nonce_lock:
            if self._clock_offset_ms is None:
                self._sync_server_clock()
            now_ms = time.time_ns() // 1_000_000
            candidate = now_ms + self._clock_offset_ms
            self._last_nonce = max(candidate, self._last_nonce + 1)
            return self._last_nonce

    def _private(self, path, fields):
        if path not in READ_ONLY_PATHS:
            raise DemoPolicyError("WhiteBIT Demo client is read-only")
        if not isinstance(fields, dict) or {"request", "nonce", "nonceWindow"} & fields.keys():
            raise DemoPolicyError("private request fields cannot override authentication data")
        url = API_BASE + path
        # Reject path, pair, and payload errors before even the public clock sync.
        validate_private_request(
            "POST", url, {"request": path, "nonce": 1, **fields}
        )
        payload = {"request": path, "nonce": self._next_nonce(), **fields}
        validate_private_request("POST", url, payload)
        body_text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        body = body_text.encode("utf-8")
        encoded, signature = sign_payload(body_text, self._api_secret)
        headers = {
            "Content-Type": "application/json",
            "X-TXC-APIKEY": self._api_key,
            "X-TXC-PAYLOAD": encoded,
            "X-TXC-SIGNATURE": signature,
        }
        return self._send("POST", url, headers, body)

    def read_balance(self, ticker):
        if ticker not in DEMO_ASSETS:
            raise DemoPolicyError("balance reads are limited to DBTC and DUSDT")
        response = self._private(BALANCE_PATH, {"ticker": ticker})
        try:
            row = response[ticker]
            return {"available": str(row["available"]), "freeze": str(row["freeze"])}
        except (KeyError, TypeError):
            raise WhitebitAPIError(f"WhiteBIT returned an invalid {ticker} balance") from None

    def read_order_history(self):
        response = self._private(ORDER_HISTORY_PATH, {
            "market": DEMO_MARKET, "limit": PAGE_SIZE, "offset": 0,
            "status": "ALL",
        })
        if not isinstance(response, dict) or not isinstance(response.get(DEMO_MARKET, []), list):
            raise WhitebitAPIError("WhiteBIT returned an invalid order-history response")
        return response.get(DEMO_MARKET, [])

    def read_executed_deals(self):
        response = self._private(EXECUTION_HISTORY_PATH, {
            "market": DEMO_MARKET, "limit": PAGE_SIZE, "offset": 0,
        })
        if not isinstance(response, list):
            raise WhitebitAPIError("WhiteBIT returned an invalid execution-history response")
        return response

    def demo_status(self):
        errors = {}
        balances = {}
        for ticker in ("DBTC", "DUSDT"):
            try:
                balances[ticker] = self.read_balance(ticker)
            except WhitebitAPIError as error:
                balances[ticker] = None
                errors[f"balance_{ticker}"] = str(error)
        reads = (
            ("order_history", self.read_order_history),
            ("executed_deals", self.read_executed_deals),
        )
        activity = {}
        for name, reader in reads:
            try:
                activity[name] = reader()
            except WhitebitAPIError as error:
                activity[name] = None
                errors[name] = str(error)

        success_count = sum(value is not None for value in balances.values())
        success_count += sum(value is not None for value in activity.values())
        if success_count == 0:
            raise WhitebitAPIError(
                "No read-only WhiteBIT endpoint succeeded; check key permissions, "
                "credentials, and connectivity"
            )

        order_history = activity["order_history"]
        executed_deals = activity["executed_deals"]
        return {
            "status": "ok" if not errors else "partial",
            "mode": "read_only",
            "pair": DEMO_MARKET,
            "balances": balances,
            "order_history_count": len(order_history) if order_history is not None else None,
            "order_history": order_history,
            "executed_deals_count": len(executed_deals) if executed_deals is not None else None,
            "executed_deals": executed_deals,
            "pnl": {
                "status": "unavailable",
                "reason": "These read endpoints do not provide an account PnL figure; "
                          "no cost-basis estimate is inferred.",
            },
            "account_scope_note": (
                "This is account-wide DBTC_DUSDT activity from the same WhiteBIT account; "
                "it may include the existing WhiteBIT Grid bot and is not attributable "
                "to Crypto Control."
            ),
            "errors": errors,
        }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("demo-status",))
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args(argv)
    try:
        key, secret = load_credentials()
        result = WhitebitDemoClient(key, secret, timeout=args.timeout).demo_status()
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result["status"] == "ok" else 1
    except DemoCredentialsError as error:
        print(json.dumps({"status": "not_configured", "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2
    except (WhitebitAPIError, DemoPolicyError) as error:
        print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

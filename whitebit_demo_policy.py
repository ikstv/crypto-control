"""Fail-closed request policy for a future WhiteBIT Demo Token adapter.

This module does not send requests or authenticate. Call it immediately before
dispatching every private WhiteBIT request; unknown endpoints are rejected.
"""
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit


DEMO_MARKET = "DBTC_DUSDT"
DEMO_ASSETS = frozenset({"DBTC", "DUSDT"})
WHITEBIT_HOST = "whitebit.com"

_AUTH_FIELDS = frozenset({"request", "nonce", "nonceWindow"})
_ORDER_FIELDS = frozenset({
    "market", "side", "amount", "price", "clientOrderId", "postOnly", "ioc",
    "bboRole", "stp", "rpi", "retail", "request", "nonce", "nonceWindow",
})
_ENDPOINTS = frozenset({
    "/api/v4/trade-account/balance",
    "/api/v4/orders",
    "/api/v4/trade-account/order/history",
    "/api/v4/trade-account/executed-history",
    "/api/v4/order/new",
    "/api/v4/order/cancel",
})


class DemoPolicyError(ValueError):
    """A request falls outside the fixed WhiteBIT Demo Token policy."""


def _positive_decimal(value, field):
    if isinstance(value, bool):
        raise DemoPolicyError(f"{field} must be a positive decimal")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise DemoPolicyError(f"{field} must be a positive decimal") from exc
    if not number.is_finite() or number <= 0:
        raise DemoPolicyError(f"{field} must be a positive decimal")


def validate_private_request(method, url, payload):
    """Validate the request target and body before any private API dispatch.

    Only HTTPS requests to WhiteBIT's global host and a small, explicit list of
    v4 Spot endpoints are accepted. No credential or network access is used.
    """
    parsed = urlsplit(str(url))
    if (parsed.scheme != "https" or parsed.hostname != WHITEBIT_HOST
            or parsed.username is not None or parsed.password is not None
            or parsed.port not in (None, 443) or parsed.query or parsed.fragment):
        raise DemoPolicyError("only HTTPS requests to whitebit.com are allowed")
    if str(method).upper() != "POST":
        raise DemoPolicyError("only documented POST endpoints are allowed")
    path = parsed.path
    if path not in _ENDPOINTS:
        raise DemoPolicyError("private endpoint is not allowlisted")
    if not isinstance(payload, Mapping):
        raise DemoPolicyError("request body must be an object")
    if payload.get("request") != path:
        raise DemoPolicyError("signed request path must match the URL path")
    nonce = payload.get("nonce")
    if isinstance(nonce, bool) or not isinstance(nonce, int) or nonce <= 0:
        raise DemoPolicyError("nonce must be a positive integer")
    if "nonceWindow" in payload and not isinstance(payload["nonceWindow"], bool):
        raise DemoPolicyError("nonceWindow must be boolean")

    if path == "/api/v4/trade-account/balance":
        if set(payload) - (_AUTH_FIELDS | {"ticker"}):
            raise DemoPolicyError("balance request contains unsupported fields")
        if payload.get("ticker") not in DEMO_ASSETS:
            raise DemoPolicyError("balance reads are limited to DBTC and DUSDT")
        return

    if path in {
        "/api/v4/orders",
        "/api/v4/trade-account/order/history",
        "/api/v4/trade-account/executed-history",
    }:
        allowed = _AUTH_FIELDS | {"market", "limit", "offset", "startDate", "endDate", "status"}
        if set(payload) - allowed:
            raise DemoPolicyError("order query contains unsupported fields")
        if payload.get("market") != DEMO_MARKET:
            raise DemoPolicyError("order reads must be filtered to DBTC_DUSDT")
        return

    if path == "/api/v4/order/new":
        if set(payload) - _ORDER_FIELDS:
            raise DemoPolicyError("order request contains unsupported fields")
        if payload.get("market") != DEMO_MARKET:
            raise DemoPolicyError("orders are limited to DBTC_DUSDT")
        if payload.get("side") not in {"buy", "sell"}:
            raise DemoPolicyError("only spot buy or sell orders are allowed")
        if not {"amount", "price"}.issubset(payload):
            raise DemoPolicyError("only priced limit orders are allowed")
        _positive_decimal(payload["amount"], "amount")
        _positive_decimal(payload["price"], "price")
        return

    if path == "/api/v4/order/cancel":
        allowed = _AUTH_FIELDS | {"market", "orderId", "clientOrderId"}
        if set(payload) - allowed:
            raise DemoPolicyError("cancel request contains unsupported fields")
        if payload.get("market") != DEMO_MARKET:
            raise DemoPolicyError("cancellations are limited to DBTC_DUSDT")
        identifiers = ("orderId" in payload, "clientOrderId" in payload)
        if sum(identifiers) != 1:
            raise DemoPolicyError("cancel requires exactly one order identifier")
        if "orderId" in payload and (isinstance(payload["orderId"], bool)
                                      or not str(payload["orderId"]).isdigit()
                                      or int(payload["orderId"]) <= 0):
            raise DemoPolicyError("orderId must be a positive integer")
        if "clientOrderId" in payload and not payload["clientOrderId"]:
            raise DemoPolicyError("clientOrderId must be non-empty")

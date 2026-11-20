"""Public WhiteBIT prices and an in-memory cache for one polling worker."""

import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from portfolio import SYMBOLS

INTERVAL = 30
PAIRS = {f"{symbol}_USDT": symbol for symbol in SYMBOLS}


def fetch_public_bytes(endpoint):
    request = Request(
        f"https://whitebit.com/api/v4/public/{endpoint}",
        headers={"User-Agent": "CryptoControl/1.0", "Accept": "application/json"},
    )
    with urlopen(request, timeout=5) as response:
        return response.read()


def fetch_public(endpoint):
    return json.loads(fetch_public_bytes(endpoint), parse_float=Decimal)


def retry_delay(header):
    try:
        return max(INTERVAL, int(header))
    except (ValueError, TypeError):
        try:
            return max(INTERVAL, (parsedate_to_datetime(header) -
                                 datetime.now(timezone.utc)).total_seconds())
        except (ValueError, TypeError, OverflowError):
            return 2 * INTERVAL


def read_price(ticker):
    if not isinstance(ticker, dict) or ticker.get("isFrozen") is True:
        return None
    raw = ticker.get("last_price")
    if not isinstance(raw, (str, Decimal, int)) or isinstance(raw, bool):
        return None
    try:
        price = Decimal(raw)
        return price if price.is_finite() and price > 0 else None
    except InvalidOperation:
        return None


class Quotes:
    def __init__(self, fetch=fetch_public):
        self.fetch = fetch
        self._state = ("loading", None, {}, set())

    def refresh(self):
        _, active, previous, _ = self._state
        delay = INTERVAL
        try:
            markets = self.fetch("markets")
            if not isinstance(markets, list) or any(
                not isinstance(row, dict)
                or not isinstance(row.get("name"), str)
                or not isinstance(row.get("type"), str)
                or not isinstance(row.get("tradesEnabled"), bool)
                for row in markets
            ):
                raise ValueError("Invalid markets response")
            active = {
                row["name"] for row in markets
                if row["name"] in PAIRS and row["stock"] == PAIRS[row["name"]]
                and row["money"] == "USDT" and row["type"] == "spot"
                and row["tradesEnabled"]
            }
            previous = {pair: quote for pair, quote in previous.items() if pair in active}
            tickers = self.fetch("ticker")
            if not isinstance(tickers, dict) or any(not isinstance(row, dict) for row in tickers.values()):
                raise ValueError("Invalid ticker response")
            received = datetime.now(timezone.utc)
            fresh = {}
            for pair in active:
                price = read_price(tickers.get(pair))
                if price is not None:
                    fresh[pair] = (price, received)
            self._state = ("connected", active, previous | fresh, fresh.keys())
            return delay
        except HTTPError as error:
            connection = "rate_limited" if error.code == 429 else "http_error"
            if error.code == 429:
                delay = retry_delay(error.headers.get("Retry-After") if error.headers else None)
            error.close()
        except (URLError, OSError, HTTPException) as error:
            connection = "timeout" if isinstance(getattr(error, "reason", error), TimeoutError) else "offline"
        except (ValueError, KeyError):
            connection = "invalid_response"
        self._state = (connection, active, previous, set())
        return delay

    def snapshot(self):
        connection, active, cached, fresh = self._state
        now = datetime.now(timezone.utc)
        rows = []
        for pair, symbol in PAIRS.items():
            price, received = cached.get(pair, (None, None))
            if received is not None:
                current = pair in fresh and (now - received).total_seconds() <= 2 * INTERVAL
                status = "fresh" if current else "stale"
            else:
                status = "market_unavailable" if active is not None and pair not in active else "no_quote"
            rows.append(dict(
                pair=pair, symbol=symbol, status=status,
                price=str(price) if price is not None else None,
                received_at=received.isoformat() if received is not None else None,
            ))
        return {"source": "WhiteBIT", "currency": "USDT", "connection": connection, "quotes": rows}

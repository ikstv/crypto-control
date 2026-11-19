"""Long-only paper ledger. All monetary calculations use Decimal."""

import json
import hashlib
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_UP
from http.client import HTTPException

from portfolio import SYMBOLS, read_portfolio
from quotes import fetch_public

FX = "USDT_UAH"
MAX_AGE = 45
CENT = Decimal("0.01")
ZERO = Decimal(0)
DEFAULT_RISK = dict(paused=False, daily_loss_percent="3", drawdown_percent="5", target_percent="2")


class MarketUnavailable(Exception):
    pass


class OperationConflict(Exception):
    pass


def now():
    return datetime.now(timezone.utc)


def number(raw):
    value = Decimal(str(raw))
    if not value.is_finite() or value < 0:
        raise ValueError("Invalid decimal")
    return value


def floor_step(value, step):
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def buy_levels(levels, budget, step, price_step):
    fills = []
    for price, size in levels:
        if price % price_step:
            raise ValueError("Ask does not match price precision")
        quantity = floor_step(min(size, budget / price), step)
        if quantity:
            fills.append((price, quantity))
            budget -= price * quantity
        if budget < price * step:
            break
    return fills


def exact_json(data):
    return json.dumps(data, default=str)


def decode(data):
    return json.loads(exact_json(data))


def fresh(timestamp):
    return -5 <= (now() - datetime.fromtimestamp(timestamp, timezone.utc)).total_seconds() <= MAX_AGE


class Ledger:
    def __init__(self, path, fetch=fetch_public, fee_override=None):
        self.path = path
        self.fetch = fetch
        self.fee_override = number(fee_override) if fee_override is not None else None
        if self.fee_override is not None and self.fee_override >= 100:
            raise ValueError("Taker fee percent must be less than 100")

    def initialize(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""CREATE TABLE IF NOT EXISTS paper_start (
                id INTEGER PRIMARY KEY CHECK (id = 1), snapshot TEXT NOT NULL
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS paper_operations (
                id INTEGER PRIMARY KEY, operation TEXT NOT NULL
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS paper_sales (
                operation_id TEXT PRIMARY KEY NOT NULL,
                symbol TEXT NOT NULL, receipt TEXT NOT NULL
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS paper_risk (
                id INTEGER PRIMARY KEY CHECK (id = 1), config TEXT NOT NULL
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS paper_equity (
                id INTEGER PRIMARY KEY, observed_at TEXT NOT NULL,
                day TEXT NOT NULL, equity_uah TEXT NOT NULL
            )""")

    @staticmethod
    def config(db):
        row = db.execute("SELECT config FROM paper_risk WHERE id = 1").fetchone()
        return json.loads(row[0]) if row else DEFAULT_RISK.copy()

    def configure(self, **changes):
        if set(changes) - DEFAULT_RISK.keys():
            raise ValueError("Unknown risk setting")
        for key, value in changes.items():
            if key == "paused":
                if not isinstance(value, bool):
                    raise ValueError("Pause must be boolean")
            elif not ZERO < number(value) <= 100:
                raise ValueError("Risk thresholds must be above zero and at most 100 percent")
        self.initialize()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            config = self.config(db)
            config.update(changes)
            db.execute("INSERT OR REPLACE INTO paper_risk VALUES (1, ?)", (exact_json(config),))
        return config

    def history(self):
        self.initialize()
        capital = Decimal(read_portfolio(self.path)["initial_capital_minor"]) / 100
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("BEGIN")
            start = db.execute("SELECT snapshot FROM paper_start WHERE id = 1").fetchone()
            operations = [json.loads(row[0]) for row in db.execute(
                "SELECT operation FROM paper_operations ORDER BY id"
            )]
            receipts = [json.loads(row[0]) for row in db.execute(
                "SELECT receipt FROM paper_sales ORDER BY rowid"
            )]
            risk = self.config(db)
        extra = dict(risk=risk, revision=len(receipts), realized_pnl_uah="0",
                     holdings=[], operations=operations)
        if start is None:
            return dict(started=False, cash_uah=str(capital), **extra)
        snapshot = json.loads(start[0])
        cash = number(snapshot["cash_uah"])
        holdings = {op["symbol"]: dict(op, basis_uah=op["debit_uah"]) for op in operations}
        realized = ZERO
        for receipt in receipts:
            for sale in receipt["sales"]:
                holding = holdings[sale["symbol"]]
                holding["quantity"] = sale["remaining_quantity"]
                holding["basis_uah"] = sale["remaining_basis_uah"]
                realized += Decimal(sale["realized_pnl_uah"])
                cash += number(sale["net_uah"])
                operations.append(sale)
        snapshot["cash_uah"] = str(cash)
        extra.update(realized_pnl_uah=str(realized),
                     holdings=[op for op in holdings.values() if number(op["quantity"]) > 0])
        return dict(started=True, **snapshot, **extra)

    def markets(self):
        try:
            return {
                row["name"]: row for row in self.fetch("markets")
                if row["type"] == "spot" and row["tradesEnabled"] is True
                and row["name"] == f'{row["stock"]}_{row["money"]}'
            }
        except (OSError, HTTPException, ValueError, TypeError, KeyError) as error:
            raise MarketUnavailable("WhiteBIT markets unavailable") from error

    def fee(self, market):
        published = number(market["takerFee"])
        if published >= 100:
            raise ValueError("Invalid published fee")
        # The configured rate can make the simulation more conservative.
        return max(published, self.fee_override or ZERO) / 100

    def book(self, pair):
        endpoint = f"orderbook/{pair}?limit={1 if pair == FX else 50}"
        try:
            book = self.fetch(endpoint)
            stamp = int(book["timestamp"])
            asks = sorted(tuple(map(number, level)) for level in book["asks"])
            bids = sorted((tuple(map(number, level)) for level in book["bids"]), reverse=True)
            ask, ask_size = asks[0]
            bid, bid_size = bids[0]
            if (book["ticker_id"] != pair or not fresh(stamp)
                    or not (bid <= ask) or any(price <= 0 or size <= 0 for price, size in asks + bids)):
                raise ValueError("Invalid or stale book")
            return dict(ask=ask, ask_size=ask_size, bid=bid, bid_size=bid_size,
                        **(dict(asks=asks, bids=bids) if pair != FX else {}),
                        timestamp=stamp, received_at=now().isoformat(),
                        source=f"https://whitebit.com/api/v4/public/{endpoint}")
        except (OSError, HTTPException, ValueError, InvalidOperation, TypeError,
                KeyError, IndexError, OverflowError) as error:
            raise MarketUnavailable(f"{pair}: order book unavailable or stale") from error

    def start(self):
        existing = self.history()
        if existing["started"]:
            return existing
        if existing["risk"]["paused"]:
            raise MarketUnavailable("Virtual purchases are paused")
        plan = read_portfolio(self.path)
        capital = Decimal(plan["initial_capital_minor"]) / 100
        cash = capital
        markets = self.markets()
        fx_market = markets.get(FX)
        if fx_market is None:
            raise MarketUnavailable("USDT/UAH spot market unavailable")
        fx = self.book(FX)
        try:
            fx_fee = self.fee(fx_market)
        except (KeyError, ValueError, InvalidOperation) as error:
            raise MarketUnavailable("USDT/UAH fee unavailable") from error
        fx_cost = fx["ask"] / (1 - fx_fee)
        operations = []
        skipped = {}
        fx_used = ZERO
        for position in plan["positions"]:
            symbol = position["symbol"]
            pair = f"{symbol}_USDT"
            budget = min(cash, Decimal(position["allocation_minor"]) / 100)
            try:
                market = markets[pair]
                book = self.book(pair)
                fee = self.fee(market)
                amount_step = Decimal(1).scaleb(-int(market["stockPrec"]))
                price_step = Decimal(1).scaleb(-int(market["moneyPrec"]))
                amount_step = max(amount_step, number(market.get("stepSize") or "0"))
                price_step = max(price_step, number(market.get("tickSize") or "0"))
                fills = buy_levels(book["asks"], budget / fx_cost, amount_step, price_step)
                quantity = sum((units for _, units in fills), ZERO)
                total = sum((price * units for price, units in fills), ZERO)
                max_total = number(market["maxTotal"])
                if (quantity <= 0 or quantity < number(market["minAmount"])
                        or total < number(market["minTotal"]) or (max_total and total > max_total)):
                    raise ValueError("Order is outside market size limits")
                fx_gross = total / (1 - fx_fee)
                if fx_used + fx_gross > fx["ask_size"]:
                    raise ValueError("Insufficient liquidity at USDT/UAH best ask")
                debit = (fx_gross * fx["ask"]).quantize(CENT, rounding=ROUND_UP)
                if debit > budget:
                    raise ValueError("Debit exceeds allocation")
            except (MarketUnavailable, KeyError, ValueError, InvalidOperation, OverflowError) as error:
                skipped[symbol] = str(error)
                continue
            fx_used += fx_gross
            cash -= debit
            base_fee = quantity * fee
            operations.append(dict(
                time=now().isoformat(), side="buy", symbol=symbol, pair=pair, price=total / quantity,
                fills=[dict(price=price, quantity=units) for price, units in fills],
                gross_quantity=quantity, quantity=quantity - base_fee, fee_base=base_fee,
                fee_rate=fee, fee_asset=symbol, published_fee_percent=market["takerFee"],
                total_usdt=total, debit_uah=debit, cash_after_uah=cash,
                fx_fee_usdt=fx_gross - total, fx_gross_usdt=fx_gross,
                fx_rounding_uah=debit - fx_gross * fx["ask"],
                quote_timestamp=book["timestamp"], quote_received_at=book["received_at"],
                source=book["source"],
            ))
        if not operations:
            raise MarketUnavailable("No eligible purchases: " + json.dumps(skipped))
        if not fresh(fx["timestamp"]) or any(not fresh(op["quote_timestamp"]) for op in operations):
            raise MarketUnavailable("Quotes expired before saving")
        snapshot = dict(cash_uah=cash, initial_uah=capital, initial_usdt=capital / fx["ask"],
                        skipped=skipped,
                        fx=dict(fx, pair=FX, direction="UAH_to_USDT", fee_rate=fx_fee))
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if self.config(db)["paused"]:
                raise MarketUnavailable("Virtual purchases are paused")
            if not fresh(fx["timestamp"]) or any(not fresh(op["quote_timestamp"]) for op in operations):
                raise MarketUnavailable("Quotes expired before saving")
            if db.execute("INSERT OR IGNORE INTO paper_start VALUES (1, ?)",
                          (exact_json(snapshot),)).rowcount:
                db.executemany("INSERT INTO paper_operations (operation) VALUES (?)",
                               [(exact_json(op),) for op in operations])
        return self.history()

    def state(self):
        journal = self.history()
        if not journal["started"]:
            return dict(journal, valuation=None, warning=None)
        try:
            valuation = self.value(journal)
            valuation.update(self.record_equity(journal, valuation["net_uah"]))
        except (MarketUnavailable, OperationConflict, InvalidOperation, ValueError, KeyError) as error:
            return dict(journal, valuation=None, warning=str(error))
        return dict(journal, valuation=decode(valuation), warning=None)

    def value(self, journal):
        if not journal["holdings"]:
            cash = number(journal["cash_uah"])
            return self.totals(journal, cash, ZERO, ZERO, None, ZERO, [])
        markets = self.markets()
        fx_fee = self.fee(markets[FX])
        fx = self.book(FX)
        details = []
        gross = net = ZERO
        for holding in journal["holdings"]:
            pair = holding["pair"]
            book = self.book(pair)
            sale = self.sale_quote(holding, markets[pair], book, fx, fx_fee, fx["bid_size"] - net)
            gross += sale["available_quantity"] * book["bid"]
            net += sale["net_usdt"]
            details.append(sale)
        if not fresh(fx["timestamp"]) or any(not fresh(row["quote_timestamp"]) for row in details):
            raise MarketUnavailable("Valuation quotes expired")
        return self.totals(journal, number(journal["cash_uah"]), gross, net, fx, fx_fee, details)

    def totals(self, journal, cash, gross, net, fx, fx_fee, details):
        rate = fx["bid"] if fx else ZERO
        uah_fee = net * rate * fx_fee
        gross_uah = cash + gross * rate
        net_uah = cash + sum((row["net_uah"] for row in details), ZERO)
        initial = number(journal["initial_uah"])
        cash_usdt = cash / rate if fx else None
        pnl = net_uah - initial
        realized = Decimal(journal["realized_pnl_uah"])
        return dict(
            assets_uah=gross * rate, assets_usdt=gross, cash_usdt=cash_usdt,
            gross_uah=gross_uah, gross_usdt=cash_usdt + gross if fx else None,
            net_uah=net_uah, net_usdt=cash_usdt + net if fx else None,
            net_pnl_uah=pnl, net_pnl_usdt=cash_usdt + net - number(journal["initial_usdt"]) if fx else None,
            realized_pnl_uah=realized, unrealized_pnl_uah=pnl - realized,
            net_pnl_percent=pnl / initial * 100,
            target_reached=pnl >= initial * number(journal["risk"]["target_percent"]) / 100,
            fx=dict(fx, pair=FX, direction="USDT_to_UAH", fee_rate=fx_fee, fee_uah=uah_fee) if fx else None,
            positions=details,
        )

    def record_equity(self, journal, equity):
        stamp = now()
        # The daily boundary is explicitly UTC on every Windows installation.
        day = stamp.date().isoformat()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            revision = db.execute("SELECT COUNT(*) FROM paper_sales").fetchone()[0]
            if revision != journal["revision"]:
                raise OperationConflict("Portfolio changed during valuation. Refresh the estimate.")
            rows = db.execute("SELECT day, equity_uah FROM paper_equity ORDER BY id").fetchall()
            db.execute("INSERT INTO paper_equity (observed_at, day, equity_uah) VALUES (?, ?, ?)",
                       (stamp.isoformat(), day, str(equity)))
        peak = number(journal["initial_uah"])
        maximum = ZERO
        daily_open = next((number(value) for date, value in rows if date == day), equity)
        for _, value in rows + [(day, str(equity))]:
            peak = max(peak, number(value))
            maximum = max(maximum, (peak - number(value)) / peak * 100)
        daily_pnl = (equity - daily_open) / daily_open * 100 if daily_open else ZERO
        risk = journal["risk"]
        return dict(max_drawdown_percent=maximum, daily_pnl_percent=daily_pnl,
                    daily_loss_alert=daily_pnl <= -number(risk["daily_loss_percent"]),
                    drawdown_alert=maximum >= number(risk["drawdown_percent"]))

    def preview(self, symbol):
        journal = self.history()
        holdings = [op for op in journal["holdings"] if symbol in ("all", op["symbol"])]
        if not holdings:
            raise MarketUnavailable("No virtual position to sell")
        markets = self.markets()
        try:
            fx_fee = self.fee(markets[FX])
            fx = self.book(FX)
            fx_left = fx["bid_size"]
            sales = []
            for holding in holdings:
                pair = holding["pair"]
                try:
                    sale = self.sale_quote(holding, markets[pair], self.book(pair), fx, fx_fee, fx_left)
                except (MarketUnavailable, KeyError, ValueError, InvalidOperation) as error:
                    sale = dict(symbol=holding["symbol"], pair=pair, quantity=ZERO, net_uah=ZERO,
                                available_quantity=holding["quantity"], remaining_quantity=holding["quantity"],
                                warning=str(error))
                fx_left -= sale.get("net_usdt", ZERO)
                sales.append(sale)
        except (KeyError, ValueError, InvalidOperation) as error:
            raise MarketUnavailable("USDT/UAH market or fee unavailable") from error
        net = sum((sale["net_uah"] for sale in sales), ZERO)
        terms = [[sale["symbol"], sale["quantity"], sale["net_uah"], sale.get("price"),
                  sale.get("fee_usdt"), sale.get("fx_fee_uah")] for sale in sales]
        fingerprint = hashlib.sha256(exact_json([journal["revision"], symbol, fx["bid"], terms]).encode()).hexdigest()
        return decode(dict(symbol=symbol, revision=journal["revision"], sales=sales, net_uah=net,
                           fx=dict(fx, pair=FX, direction="USDT_to_UAH", fee_rate=fx_fee),
                           fingerprint=fingerprint))

    def sale_quote(self, holding, market, book, fx, fx_fee, fx_left):
        available = number(holding["quantity"])
        fee = self.fee(market)
        step = max(Decimal(1).scaleb(-int(market["stockPrec"])), number(market.get("stepSize") or "0"))
        price_step = max(Decimal(1).scaleb(-int(market["moneyPrec"])), number(market.get("tickSize") or "0"))
        remaining = floor_step(available, step)
        max_total = number(market["maxTotal"])
        budget = min(fx_left / (1 - fee), max_total) if max_total else fx_left / (1 - fee)
        fills = []
        for bid, size in book["bids"]:
            if bid % price_step:
                raise ValueError("Bid does not match market precision")
            quantity = floor_step(min(size, remaining, budget / bid), step)
            if quantity:
                fills.append(dict(price=bid, quantity=quantity))
                remaining -= quantity
                budget -= quantity * bid
            if not remaining:
                break
        quantity = sum((row["quantity"] for row in fills), ZERO)
        total = sum((row["price"] * row["quantity"] for row in fills), ZERO)
        warning = None
        if not quantity or quantity < number(market["minAmount"]) or total < number(market["minTotal"]):
            quantity = total = ZERO
            fills = []
            warning = "Недостатньо ліквідності або обсяг нижчий за мінімум пари"
        elif quantity < available:
            warning = "Після продажу залишиться частина позиції"
        sell_fee = total * fee
        net = total - sell_fee
        conversion_fee = net * fx["bid"] * fx_fee
        uah = (net * fx["bid"] - conversion_fee).quantize(CENT, rounding=ROUND_DOWN)
        basis = number(holding["basis_uah"])
        sold_basis = basis if quantity == available else (basis * quantity / available).quantize(CENT, rounding=ROUND_DOWN)
        return dict(symbol=holding["symbol"], pair=holding["pair"], side="sell",
                    available_quantity=available, quantity=quantity, remaining_quantity=available - quantity,
                    bid=book["bid"], ask=book["ask"], spread=book["ask"] - book["bid"],
                    best_bid_size=book["bid_size"], bid_levels=len(book["bids"]),
                    bid_depth=sum(size for _, size in book["bids"]), fills=fills,
                    price=total / quantity if quantity else None, gross_usdt=total,
                    fee_rate=fee, fee_usdt=sell_fee, net_usdt=net, fx_fee_uah=conversion_fee,
                    net_uah=uah, cost_basis_uah=sold_basis, remaining_basis_uah=basis - sold_basis,
                    realized_pnl_uah=uah - sold_basis, warning=warning,
                    quote_timestamp=book["timestamp"], received_at=book["received_at"], source=book["source"])

    def sell(self, symbol, operation_id, fingerprint):
        self.initialize()
        with closing(sqlite3.connect(self.path)) as db:
            existing = self.sale_receipt(db, operation_id, symbol)
        if existing:
            return existing
        preview = self.preview(symbol)
        if preview["fingerprint"] != fingerprint:
            raise OperationConflict("Sale terms changed. Request a new preview.")
        sales = [sale for sale in preview["sales"] if number(sale["quantity"]) > 0]
        if not sales:
            raise MarketUnavailable("No quantity can be sold at current liquidity and market limits")
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            existing = self.sale_receipt(db, operation_id, symbol)
            if existing:
                return existing
            if db.execute("SELECT COUNT(*) FROM paper_sales").fetchone()[0] != preview["revision"]:
                raise OperationConflict("Portfolio changed. Request a new preview.")
            if not fresh(preview["fx"]["timestamp"]) or any(not fresh(sale["quote_timestamp"]) for sale in sales):
                raise MarketUnavailable("Sale quotes expired before saving")
            cash = number(json.loads(db.execute("SELECT snapshot FROM paper_start").fetchone()[0])["cash_uah"])
            cash += sum((number(json.loads(row[0])["net_uah"])
                         for row in db.execute("SELECT receipt FROM paper_sales")), ZERO)
            stamp = now().isoformat()
            for sale in sales:
                cash += number(sale["net_uah"])
                sale.update(time=stamp, operation_id=operation_id,
                            cash_after_uah=str(cash), fx=preview["fx"])
            receipt = dict(operation_id=operation_id, sales=sales, net_uah=preview["net_uah"],
                           skipped=[sale for sale in preview["sales"] if not number(sale["quantity"])])
            db.execute("INSERT INTO paper_sales VALUES (?, ?, ?)", (operation_id, symbol, exact_json(receipt)))
        return receipt

    @staticmethod
    def sale_receipt(db, operation_id, symbol):
        row = db.execute("SELECT symbol, receipt FROM paper_sales WHERE operation_id = ?", (operation_id,)).fetchone()
        if row:
            if row[0] != symbol:
                raise OperationConflict("Operation identifier already used for another sale")
            return json.loads(row[1])
        return None

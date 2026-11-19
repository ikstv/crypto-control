"""Long-only paper ledger. All monetary calculations use Decimal."""

import json
import hashlib
import sqlite3
from threading import Lock
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_UP
from http.client import HTTPException

from portfolio import SYMBOLS, connect, read_portfolio
from quotes import fetch_public
from balance_change import explain, snapshot
from equity import equity_history

FX = "USDT_UAH"
MAX_AGE = 45
CENT = Decimal("0.01")
USDT_UNIT = Decimal("0.000001")
ZERO = Decimal(0)
DEFAULT_RISK = dict(paused=False, daily_loss_percent="3", drawdown_percent="5", target_percent="2")


class MarketUnavailable(Exception):
    pass


class OperationConflict(Exception):
    pass


class LedgerSchemaMismatch(Exception):
    """Raised when the legacy Ledger is used with the migrated multi-portfolio schema."""

    pass


def now():
    return datetime.now(timezone.utc)


def observation_slot(stamp):
    return stamp.replace(minute=stamp.minute // 5 * 5, second=0, microsecond=0).isoformat()


def number(raw):
    value = Decimal(str(raw))
    if not value.is_finite() or value < 0:
        raise ValueError("Invalid decimal")
    return value


def floor_step(value, step):
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


class InsufficientFxDepth(MarketUnavailable):
    pass


def convert_sale_usdt(bids, net, fee_rate, used):
    gross = ZERO
    remaining = net
    for raw_price, raw_size in bids:
        price, size = number(raw_price), number(raw_size)
        skipped = min(used, size)
        used -= skipped
        fill = min(size - skipped, remaining)
        gross += price * fill
        remaining -= fill
        if not remaining:
            break
    if remaining:
        raise InsufficientFxDepth("Insufficient USDT/UAH bid depth")
    fee = gross * fee_rate
    return (gross - fee).quantize(CENT, rounding=ROUND_DOWN), fee


def sale_proceeds(units, bid, fee_rate, fx, used=ZERO):
    gross = units * bid
    fee = gross * fee_rate
    net = gross - fee
    if fx is None:
        credit = net.quantize(USDT_UNIT, rounding=ROUND_DOWN)
        fx_fee = ZERO
    else:
        credit, fx_fee = convert_sale_usdt(fx["bids"], net, number(fx["fee_rate"]), used)
    return fee, net, credit, fx_fee


def sold_basis(basis, units, available, quantum):
    # A final fill releases the exact remainder, including any historical precision.
    if units == available:
        return basis
    return (basis * units / available).quantize(quantum, rounding=ROUND_DOWN)


def buy_levels(levels, budget, step, price_step):
    target = floor_step(budget / levels[0][0], step)
    remaining = target
    fills = []
    for price, size in levels:
        if price % price_step:
            raise ValueError("Ask does not match price precision")
        quantity = min(floor_step(size, step), remaining)
        if quantity:
            fills.append((price, quantity))
            remaining -= quantity
        if not remaining:
            break
    if remaining:
        raise MarketUnavailable("Insufficient crypto ask depth for the target quantity")
    if sum((price * quantity for price, quantity in fills), ZERO) > budget:
        raise MarketUnavailable("Target quantity exceeds the USDT budget at executable prices")
    return fills


def buy_with_fx_depth(asks, budget, amount_step, price_step, levels, fx_fee, fx_step):
    if not levels:
        raise MarketUnavailable("Insufficient USDT/UAH ask depth")
    remaining = budget
    affordable = ZERO
    for rate, available in levels:
        if rate % fx_step:
            raise ValueError("FX ask does not match market precision")
        units = min(available, remaining / rate)
        affordable += units
        remaining -= units * rate
        if units < available:
            break
    else:
        # Probe the next affordable lot at the last visible price. Actual
        # fills below must be covered entirely by visible FX depth.
        affordable += remaining / levels[-1][0]
    fills = buy_levels(asks, affordable * (1 - fx_fee), amount_step, price_step)
    received = sum((price * units for price, units in fills), ZERO)
    acquired = received / (1 - fx_fee)
    remaining = acquired
    fx_fills = []
    cost = ZERO
    for rate, available in levels:
        units = min(available, remaining)
        if not units:
            break
        fx_fills.append((rate, units))
        cost += rate * units
        remaining -= units
    if remaining:
        raise MarketUnavailable("Insufficient USDT/UAH ask depth")
    return fills, dict(requested=received, received=received, acquired=acquired,
                      fee=acquired - received, cost_uah=cost, debit_uah=cost,
                      fills=fx_fills, worst_price=fx_fills[-1][0] if fx_fills else levels[0][0])


def consume_quote_depth(levels, fills):
    available = [amount for _, amount in levels]
    for _, filled in fills:
        for index, amount in enumerate(available):
            consumed = min(amount, filled)
            available[index] -= consumed
            filled -= consumed
            if filled == 0:
                break
        if filled:
            raise MarketUnavailable("FX ask depth changed during simulated purchases")
    levels[:] = [(price, amount) for (price, _), amount in zip(levels, available) if amount]


def exact_json(data):
    return json.dumps(data, default=str)


def decode(data):
    return json.loads(exact_json(data))


def fresh(timestamp):
    return -5 <= (now() - datetime.fromtimestamp(timestamp, timezone.utc)).total_seconds() <= MAX_AGE


class Ledger:
    def __init__(self, path, fetch=fetch_public, fee_override=None, portfolio_id=1):
        self.path = path
        self.fetch = fetch
        self.portfolio_id = portfolio_id
        self._balance_lock = Lock()
        self._previous_balance = None
        self.fee_override = number(fee_override) if fee_override is not None else None
        if self.fee_override is not None and self.fee_override >= 100:
            raise ValueError("Taker fee percent must be less than 100")

    def _migrated(self):
        with closing(sqlite3.connect(self.path)) as db:
            return "portfolio_id" in {row[1] for row in db.execute("PRAGMA table_info(settings)")}

    def _ensure_legacy_writable(self):
        if self._migrated():
            raise LedgerSchemaMismatch(
                "Write operations require the portfolio-aware Ledger adapter; migrated schema is read-only here"
            )

    def _read_only_history(self, db):
        plan = read_portfolio(self.path, self.portfolio_id)
        start = db.execute("SELECT snapshot FROM paper_start WHERE portfolio_id = ?", (self.portfolio_id,)).fetchone()
        operations = [json.loads(row[0]) for row in db.execute(
            "SELECT operation FROM paper_operations WHERE portfolio_id = ? ORDER BY id", (self.portfolio_id,)
        )]
        # rowid is the append sequence (also on older migrated copies).
        receipts = [(key, json.loads(data)) for key, data in db.execute(
            "SELECT operation_id, receipt FROM paper_sales WHERE portfolio_id = ? ORDER BY rowid", (self.portfolio_id,)
        )]
        risk_row = db.execute("SELECT config FROM paper_risk WHERE portfolio_id = ?", (self.portfolio_id,)).fetchone()
        risk = json.loads(risk_row[0]) if risk_row else DEFAULT_RISK.copy()
        currency = plan["currency"]
        extra = dict(risk=risk, revision=len(receipts), holdings=[], operations=operations,
                     realized_pnl_uah="0" if currency == "UAH" else None,
                     realized_pnl_usdt="0" if currency == "USDT" else None)
        if start is None:
            cash_key = "cash_usdt" if plan["currency"] == "USDT" else "cash_uah"
            return dict(started=False, currency=plan["currency"], **{cash_key: str(Decimal(plan["initial_capital_minor"]) / (Decimal(10) ** plan["scale"]))}, **extra)
        snapshot = json.loads(start[0])
        snapshot["currency"] = currency
        basis_key = "basis_uah" if plan["currency"] == "UAH" else "basis_usdt"
        debit_key = "debit_uah" if plan["currency"] == "UAH" else "debit_usdt"
        cash_key = "cash_uah" if plan["currency"] == "UAH" else "cash_usdt"
        holdings = {op["symbol"]: dict(op, **{basis_key: op.get(debit_key, "0")}) for op in operations}
        cash = number(snapshot[cash_key])
        realized = ZERO
        for operation_id, receipt in receipts:
            for sale in receipt["sales"]:
                # Migration copies receipts verbatim. Legacy UAH receipts carry
                # the booked cost and proceeds in currency-suffixed fields.
                if "remaining_basis" in sale:
                    remaining = sale["remaining_basis"]
                    cost, credit, pnl = sale["cost_basis"], sale["net_currency"], sale["realized_pnl"]
                elif plan["currency"] == "UAH" and "remaining_basis_uah" in sale:
                    remaining = sale["remaining_basis_uah"]
                    cost, credit, pnl = sale["cost_basis_uah"], sale["net_uah"], sale["realized_pnl_uah"]
                else:
                    raise OperationConflict(f"Unrecognized receipt for sale {operation_id}")
                holding = holdings.get(sale["symbol"])
                if (holding is None
                        or number(holding["quantity"]) != number(sale["quantity"]) + number(sale["remaining_quantity"])
                        or number(holding[basis_key]) != number(cost) + number(remaining)
                        or number(credit) - number(cost) != Decimal(pnl)):
                    raise OperationConflict(f"Stored sale {operation_id} does not reconcile with the journal")
                holding["quantity"] = sale["remaining_quantity"]
                holding[basis_key] = remaining
                cash += number(credit)
                realized += Decimal(pnl)
                operations.append(dict(sale, operation_id=operation_id))
        snapshot[cash_key] = str(cash)
        extra[f"realized_pnl_{currency.lower()}"] = str(realized)
        extra["holdings"] = [op for op in holdings.values() if number(op.get("quantity", "0")) > 0]
        return dict(started=True, **snapshot, **extra)

    def initialize(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            columns = {row[1] for row in db.execute("PRAGMA table_info(settings)")}
            if "portfolio_id" in columns:
                raise LedgerSchemaMismatch(
                    "Ledger requires portfolio-aware adapter for migrated schema; no automatic migration is performed"
                )
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
            db.execute("""CREATE TABLE IF NOT EXISTS paper_observations (
                slot TEXT PRIMARY KEY, observed_at TEXT NOT NULL,
                status TEXT NOT NULL, reason TEXT, equity_uah TEXT,
                max_drawdown_percent TEXT, daily_pnl_percent TEXT
            )""")

    @staticmethod
    def config(db, portfolio_id=None):
        key = "id" if portfolio_id is None else "portfolio_id"
        row = db.execute(f"SELECT config FROM paper_risk WHERE {key} = ?",
                         (1 if portfolio_id is None else portfolio_id,)).fetchone()
        return json.loads(row[0]) if row else DEFAULT_RISK.copy()

    def configure(self, **changes):
        migrated = self._migrated()
        if set(changes) - DEFAULT_RISK.keys():
            raise ValueError("Unknown risk setting")
        for key, value in changes.items():
            if key == "paused":
                if not isinstance(value, bool):
                    raise ValueError("Pause must be boolean")
            elif not ZERO < number(value) <= 100:
                raise ValueError("Risk thresholds must be above zero and at most 100 percent")
        if not migrated:
            self.initialize()
        with closing(connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            config = self.config(db, self.portfolio_id if migrated else None)
            config.update(changes)
            db.execute("INSERT OR REPLACE INTO paper_risk VALUES (?, ?)",
                       (self.portfolio_id if migrated else 1, exact_json(config)))
        return config

    def history(self):
        return self._journal(include_observations=True)

    def _journal(self, *, include_observations=False):
        migrated = self._migrated()
        if not migrated:
            self.initialize()
        with closing(connect(self.path)) as db:
            db.execute("BEGIN")
            journal = self._read_only_history(db) if migrated else self._legacy_journal(db)
            if include_observations:
                _, rows = equity_history(db, self.portfolio_id if migrated else None, journal, include_rows=True)
                journal["observations"] = decode(rows)
        return journal

    def _legacy_journal(self, db):
        capital = Decimal(read_portfolio(self.path)["initial_capital_minor"]) / 100
        start = db.execute("SELECT snapshot FROM paper_start WHERE id = 1").fetchone()
        operations = [json.loads(row[0]) for row in db.execute("SELECT operation FROM paper_operations ORDER BY id")]
        receipts = [json.loads(row[0]) for row in db.execute("SELECT receipt FROM paper_sales ORDER BY rowid")]
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
        endpoint = f"orderbook/{pair}?limit=50"
        try:
            book = self.fetch(endpoint)
            stamp = int(book["timestamp"])
            asks = sorted(tuple(map(number, level)) for level in book["asks"])
            bids = sorted((tuple(map(number, level)) for level in book["bids"]), reverse=True)
            ask, ask_size = asks[0]
            bid, bid_size = bids[0]
            if not fresh(stamp):
                raise MarketUnavailable(f"{pair}: stale order book")
            if (book["ticker_id"] != pair
                    or not (bid <= ask) or any(price <= 0 or size <= 0 for price, size in asks + bids)):
                raise ValueError("Invalid or stale book")
            return dict(ask=ask, ask_size=ask_size, bid=bid, bid_size=bid_size,
                        asks=asks, bids=bids,
                        timestamp=stamp, received_at=now().isoformat(),
                        source=f"https://whitebit.com/api/v4/public/{endpoint}")
        except (OSError, HTTPException, ValueError, InvalidOperation, TypeError,
                KeyError, IndexError, OverflowError) as error:
            raise MarketUnavailable(f"{pair}: order book unavailable") from error

    def start(self, *, legacy=False):
        migrated = self._migrated()
        if migrated and not legacy:
            return self._start_migrated()
        scope = 1 if migrated else None
        existing = self.history()
        if existing["started"]:
            return existing
        if existing["risk"]["paused"]:
            raise MarketUnavailable("Virtual purchases are paused")
        plan = read_portfolio(self.path, scope)
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
        fx_step = max(Decimal(1).scaleb(-int(fx_market["moneyPrec"])),
                      number(fx_market.get("tickSize") or "0"))
        fx_remaining = list(fx["asks"])
        operations = []
        skipped = {}
        for position in plan["positions"]:
            symbol = position["symbol"]
            pair = f"{symbol}_USDT"
            budget = min(cash, Decimal(position["allocation_minor"]) / 100)
            if not position["allocation_minor"]:
                continue
            try:
                market = markets[pair]
                book = self.book(pair)
                fee = self.fee(market)
                amount_step = Decimal(1).scaleb(-int(market["stockPrec"]))
                price_step = Decimal(1).scaleb(-int(market["moneyPrec"]))
                amount_step = max(amount_step, number(market.get("stepSize") or "0"))
                price_step = max(price_step, number(market.get("tickSize") or "0"))
                fills, conversion = buy_with_fx_depth(
                    book["asks"], budget, amount_step, price_step, fx_remaining, fx_fee, fx_step
                )
                total = sum((price * units for price, units in fills), ZERO)
                quote_left = conversion["received"] - total
                quantity = sum((units for _, units in fills), ZERO)
                max_total = number(market["maxTotal"])
                if (quantity <= 0 or quantity < number(market["minAmount"])
                        or total < number(market["minTotal"]) or (max_total and total > max_total)):
                    raise ValueError("Order is outside market size limits")
                debit = conversion["debit_uah"].quantize(CENT, rounding=ROUND_UP)
                if debit > budget or quote_left < 0:
                    raise ValueError("Debit exceeds allocation")
            except (MarketUnavailable, KeyError, ValueError, InvalidOperation, OverflowError) as error:
                if pair in markets:
                    raise MarketUnavailable(str(error)) from error
                skipped[symbol] = str(error)
                continue
            cash -= debit
            consume_quote_depth(fx_remaining, conversion["fills"])
            base_fee = quantity * fee
            operations.append(dict(
                time=now().isoformat(), side="buy", symbol=symbol, pair=pair, price=total / quantity,
                fills=[dict(price=price, quantity=units) for price, units in fills],
                gross_quantity=quantity, quantity=quantity - base_fee, fee_base=base_fee,
                fee_rate=fee, fee_asset=symbol, published_fee_percent=market["takerFee"],
                total_usdt=total, debit_uah=debit, cash_after_uah=cash,
                fx_requested_usdt=conversion["requested"], fx_acquired_usdt=conversion["acquired"],
                fx_fee_usdt=conversion["fee"], fx_received_usdt=conversion["received"],
                fx_cost_uah=conversion["cost_uah"], fx_gross_uah=conversion["debit_uah"],
                fx_fills=[dict(price=price, quantity=units) for price, units in conversion["fills"]],
                fx_worst_price=conversion["worst_price"], unused_usdt=quote_left,
                fx_rounding_uah=debit - conversion["debit_uah"],
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
        with closing(connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if self.config(db, scope)["paused"]:
                raise MarketUnavailable("Virtual purchases are paused")
            if not fresh(fx["timestamp"]) or any(not fresh(op["quote_timestamp"]) for op in operations):
                raise MarketUnavailable("Quotes expired before saving")
            if db.execute("INSERT OR IGNORE INTO paper_start VALUES (1, ?)",
                          (exact_json(snapshot),)).rowcount:
                if migrated:
                    db.executemany("INSERT INTO paper_operations (portfolio_id, operation) VALUES (1, ?)",
                                   [(exact_json(op),) for op in operations])
                else:
                    db.executemany("INSERT INTO paper_operations (operation) VALUES (?)",
                                   [(exact_json(op),) for op in operations])
        return self.history()

    def _start_migrated(self):
        """Start one migrated portfolio; sales and valuation remain read-only."""
        plan = read_portfolio(self.path, self.portfolio_id)
        existing = self.history()
        if existing["started"]:
            return existing
        currency = plan["currency"]
        scale = plan["scale"]
        capital = Decimal(plan["initial_capital_minor"]) / (Decimal(10) ** scale)
        cash = capital
        markets = self.markets()
        fx = fx_market = fx_fee = None
        if currency == "UAH":
            fx_market = markets.get(FX)
            if fx_market is None:
                raise MarketUnavailable("USDT/UAH spot market unavailable")
            fx = self.book(FX)
            fx_fee = self.fee(fx_market)
            fx_step = max(Decimal(1).scaleb(-int(fx_market["moneyPrec"])),
                          number(fx_market.get("tickSize") or "0"))
            fx_remaining = list(fx["asks"])
        operations = []
        for position in plan["positions"]:
            symbol = position["symbol"]
            pair = f"{symbol}_USDT"
            if not position["allocation_minor"]:
                continue
            try:
                market = markets[pair]
                book = self.book(pair)
                fee = self.fee(market)
                amount_step = max(Decimal(1).scaleb(-int(market["stockPrec"])), number(market.get("stepSize") or "0"))
                price_step = max(Decimal(1).scaleb(-int(market["moneyPrec"])), number(market.get("tickSize") or "0"))
                allocation = Decimal(position["allocation_minor"]) / (Decimal(10) ** scale)
                budget = min(cash, allocation)
                conversion = None
                if fx is not None:
                    fills, conversion = buy_with_fx_depth(
                        book["asks"], budget, amount_step, price_step, fx_remaining, fx_fee, fx_step
                    )
                    total = sum((price * units for price, units in fills), ZERO)
                    quote_left = conversion["received"] - total
                else:
                    fills = buy_levels(book["asks"], budget, amount_step, price_step)
                    total = sum((price * units for price, units in fills), ZERO)
                    quote_left = budget - total
                quantity = sum((units for _, units in fills), ZERO)
                if (quantity <= 0 or quantity < number(market["minAmount"])
                        or total < number(market["minTotal"])
                        or (number(market["maxTotal"]) and total > number(market["maxTotal"]))):
                    raise ValueError("Order is outside market size limits")
                if currency == "UAH":
                    debit = conversion["debit_uah"].quantize(CENT, rounding=ROUND_UP)
                else:
                    debit = total.quantize(Decimal("0.000001"), rounding=ROUND_UP)
                if debit > budget:
                    raise ValueError("Debit exceeds allocation")
            except (KeyError, MarketUnavailable, ValueError, InvalidOperation, OverflowError) as error:
                if pair in markets:
                    raise MarketUnavailable(str(error)) from error
                continue
            cash -= debit
            if conversion is not None:
                consume_quote_depth(fx_remaining, conversion["fills"])
            base_fee = quantity * fee
            operation = dict(time=now().isoformat(), side="buy", symbol=symbol, pair=pair,
                             price=total / quantity, fills=[dict(price=p, quantity=q) for p, q in fills],
                             gross_quantity=quantity, quantity=quantity - base_fee, fee_base=base_fee,
                             fee_rate=fee, fee_asset=symbol, total_usdt=total,
                             cash_after=(cash), quote_timestamp=book["timestamp"],
                             quote_received_at=book["received_at"], source=book["source"])
            operation["debit_uah" if currency == "UAH" else "debit_usdt"] = debit
            if conversion is not None:
                operation.update(fx_requested_usdt=conversion["requested"],
                                 fx_acquired_usdt=conversion["acquired"],
                                 fx_fee_usdt=conversion["fee"],
                                 fx_received_usdt=conversion["received"],
                                 fx_cost_uah=conversion["cost_uah"],
                                 fx_gross_uah=conversion["debit_uah"],
                                 fx_fills=[dict(price=p, quantity=q) for p, q in conversion["fills"]],
                                 fx_worst_price=conversion["worst_price"], unused_usdt=quote_left)
            operations.append(operation)
        if not operations:
            raise MarketUnavailable("No eligible purchases")
        if (fx is not None and not fresh(fx["timestamp"])) or any(
                not fresh(op["quote_timestamp"]) for op in operations):
            raise MarketUnavailable("Quotes expired before saving")
        snapshot = {"currency": currency, "cash_uah" if currency == "UAH" else "cash_usdt": cash,
                    "initial_uah" if currency == "UAH" else "initial_usdt": capital,
                    "skipped": {}}
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM paper_start WHERE portfolio_id = ?", (self.portfolio_id,)).fetchone():
                return self.history()
            allocations = db.execute(
                "SELECT symbol, allocation_minor FROM positions WHERE portfolio_id=? ORDER BY rowid",
                (self.portfolio_id,),
            ).fetchall()
            if allocations != [(row["symbol"], row["allocation_minor"]) for row in plan["positions"]]:
                raise OperationConflict("Allocations changed during start. Retry with the saved allocations.")
            if (fx is not None and not fresh(fx["timestamp"])) or any(
                    not fresh(op["quote_timestamp"]) for op in operations):
                raise MarketUnavailable("Quotes expired before saving")
            db.execute("INSERT INTO paper_start (portfolio_id, snapshot) VALUES (?, ?)",
                       (self.portfolio_id, exact_json(snapshot)))
            db.executemany("INSERT INTO paper_operations (portfolio_id, operation) VALUES (?, ?)",
                           [(self.portfolio_id, exact_json(op)) for op in operations])
        return self.history()

    def state(self):
        journal = self.history()
        if not journal["started"]:
            return dict(journal, valuation=None, warning=None)
        try:
            with self._balance_lock:
                valuation = self.value(journal)
                valuation.update(self.record_equity(journal, valuation["equity"], persist=False, valuation=valuation))
                if valuation["currency"] == "UAH":
                    current = snapshot(journal, valuation, now().isoformat())
                    previous = self._previous_balance
                    valuation["balance_change"] = explain(previous, current) if previous else None
                    self._previous_balance = current
        except (MarketUnavailable, OperationConflict, InvalidOperation, ValueError, KeyError) as error:
            return dict(journal, valuation=None, warning=str(error))
        return dict(journal, valuation=decode(valuation), warning=None)

    def observation_history(self):
        return self.history()["observations"]

    def record_skip(self, reason, slot=None, status="unavailable"):
        stamp = now()
        slot = slot or observation_slot(stamp)
        migrated = self._migrated()
        columns = "portfolio_id, " if migrated else ""
        prefix = "?, " if migrated else ""
        args = (self.portfolio_id,) if migrated else ()
        with closing(connect(self.path)) as db, db:
            inserted = db.execute(f"""INSERT OR IGNORE INTO paper_observations
                ({columns}slot, observed_at, status, reason) VALUES ({prefix}?, ?, ?, ?)""",
                (*args, slot, stamp.isoformat(), status, reason)).rowcount
        return {"status": status if inserted else "duplicate", "reason": reason}

    def record_valuation(self, slot=None):
        slot = slot or observation_slot(now())
        journal = self._journal()
        if not journal["started"]:
            return self.record_skip("Virtual trading has not started", slot)
        try:
            valuation = self.value(journal)
            result = self.record_equity(journal, valuation["equity"], slot=slot, valuation=valuation)
            return decode(result)
        except (MarketUnavailable, OperationConflict, InvalidOperation, ValueError, KeyError) as error:
            reason = str(error)
            status = "stale" if "stale" in reason.lower() or "expired" in reason.lower() else "unavailable"
            return self.record_skip(reason, slot, status)

    def value(self, journal):
        if journal.get("currency") == "USDT":
            cash = number(journal["cash_usdt"])
            sales = self._preview_migrated("all", journal)["sales"] if journal["holdings"] else []
            assets = sum((number(p["available_quantity"]) * number(p["bid"]) for p in sales), ZERO)
            equity = cash + sum((number(p["net_currency"]) for p in sales), ZERO)
            initial = number(journal["initial_usdt"])
            pnl = equity - initial
            realized = Decimal(journal["realized_pnl_usdt"])
            return dict(currency="USDT", cash_usdt=cash, assets_usdt=assets, gross_usdt=cash + assets,
                        equity=equity, net_usdt=equity, net_pnl_usdt=pnl, realized_pnl_usdt=realized,
                        unrealized_pnl_usdt=pnl - realized, net_pnl_percent=pnl / initial * 100,
                        target_reached=pnl >= initial * number(journal["risk"]["target_percent"]) / 100,
                        fx=None, positions=sales)
        if not journal["holdings"]:
            cash = number(journal["cash_uah"])
            return self.totals(journal, cash, ZERO, ZERO, None, ZERO, [])
        markets = self.markets()
        if FX not in markets:
            raise MarketUnavailable("USDT/UAH spot market unavailable")
        fx_fee = self.fee(markets[FX])
        fx = self.book(FX)
        details = []
        gross = net = ZERO
        for holding in journal["holdings"]:
            pair = holding["pair"]
            book = self.book(pair)
            sale = self.sale_quote(holding, markets[pair], book, fx, fx_fee, net)
            gross += sale["available_quantity"] * book["bid"]
            net += sale["net_usdt"]
            details.append(sale)
        if not fresh(fx["timestamp"]) or any(not fresh(row["quote_timestamp"]) for row in details):
            raise MarketUnavailable("Valuation quotes expired")
        return self.totals(journal, number(journal["cash_uah"]), gross, net, fx, fx_fee, details)

    def totals(self, journal, cash, gross, net, fx, fx_fee, details):
        rate = fx["bid"] if fx else ZERO
        uah_fee = sum((row["fx_fee_uah"] for row in details), ZERO)
        gross_uah = cash + gross * rate
        net_uah = cash + sum((row["net_uah"] for row in details), ZERO)
        initial = number(journal["initial_uah"])
        cash_usdt = cash / rate if fx else None
        pnl = net_uah - initial
        realized = Decimal(journal["realized_pnl_uah"])
        return dict(
            currency="UAH", equity=net_uah,
            assets_uah=gross * rate, assets_usdt=gross, cash_usdt=cash_usdt,
            gross_uah=gross_uah, gross_usdt=cash_usdt + gross if fx else None,
            net_uah=net_uah, net_usdt=cash_usdt + net if fx else None,
            net_pnl_uah=pnl, net_pnl_usdt=(cash_usdt + net - number(journal["initial_usdt"])
                                           if fx and journal.get("initial_usdt") is not None else None),
            realized_pnl_uah=realized, unrealized_pnl_uah=pnl - realized,
            net_pnl_percent=pnl / initial * 100,
            target_reached=pnl >= initial * number(journal["risk"]["target_percent"]) / 100,
            fx=dict(fx, pair=FX, direction="USDT_to_UAH", fee_rate=fx_fee, fee_uah=uah_fee) if fx else None,
            positions=details,
        )

    def record_equity(self, journal, equity, persist=True, *, slot=None, valuation=None):
        migrated = self._migrated()
        scope = " WHERE portfolio_id=?" if migrated else ""
        args = (self.portfolio_id,) if migrated else ()
        columns, prefix = ("portfolio_id, ", "?, ") if migrated else ("", "")
        currency = journal.get("currency", "UAH")
        with closing(connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE" if persist else "BEGIN")
            if slot is not None:
                prior = db.execute("SELECT 1 FROM paper_observations WHERE slot=?" +
                                   (" AND portfolio_id=?" if migrated else ""), (slot, *args)).fetchone()
                if prior:
                    return {"status": "duplicate"}
            revision = db.execute("SELECT COUNT(*) FROM paper_sales" + scope, args).fetchone()[0]
            if revision != journal["revision"]:
                raise OperationConflict("Portfolio changed during valuation. Refresh the estimate.")
            if valuation is not None:
                fx = valuation.get("fx")
                if ((fx and not fresh(fx["timestamp"])) or
                        any(not fresh(p["quote_timestamp"]) for p in valuation["positions"])):
                    raise MarketUnavailable("Valuation quotes expired")
            stamp = now()
            curve, _ = equity_history(db, self.portfolio_id if migrated else None, journal, until=stamp)
            prior_maximum = curve.maximum
            stats = curve.mark(stamp, equity, recorded=persist)
            if not persist:
                stats["max_drawdown_percent"] = prior_maximum
            if persist:
                if slot is not None:
                    daily = stats["daily_pnl_percent"]
                    db.execute(f"""INSERT INTO paper_observations
                        ({columns}slot, observed_at, status, equity_uah, max_drawdown_percent, daily_pnl_percent)
                        VALUES ({prefix}?, ?, 'recorded', ?, ?, ?)""",
                        (*args, slot, stamp.isoformat(), str(equity), str(stats["max_drawdown_percent"]),
                         str(daily) if daily is not None else None))
                fields = ", equity_currency" if migrated else ""
                values = ", ?" if migrated else ""
                db.execute(f"""INSERT INTO paper_equity ({columns}observed_at, day, equity_uah{fields})
                    VALUES ({prefix}?, ?, ?{values})""", (*args, stamp.isoformat(), stamp.date().isoformat(),
                    str(equity), *((currency,) if migrated else ())))
        risk = journal["risk"]
        daily = stats["daily_pnl_percent"]
        stats.update(daily_loss_alert=daily is not None and daily <= -number(risk["daily_loss_percent"]),
                     drawdown_alert=stats["max_drawdown_percent"] >= number(risk["drawdown_percent"]))
        if slot is not None:
            stats.update(status="recorded", currency=currency, equity=equity)
            stats[f"equity_{currency.lower()}"] = equity
        return stats

    def preview(self, symbol, quantity=None, *, legacy=False):
        if self._migrated() and not legacy:
            return self._preview_migrated(symbol, requested=quantity)
        journal = self.history()
        holdings = [op for op in journal["holdings"] if symbol in ("all", op["symbol"])]
        if not holdings:
            raise MarketUnavailable("No virtual position to sell")
        markets = self.markets()
        try:
            fx_fee = self.fee(markets[FX])
            fx = self.book(FX)
            fx_used = ZERO
            sales = []
            for holding in holdings:
                pair = holding["pair"]
                try:
                    sale = self.sale_quote(holding, markets[pair], self.book(pair), fx, fx_fee, fx_used)
                except InsufficientFxDepth:
                    raise
                except (MarketUnavailable, KeyError, ValueError, InvalidOperation) as error:
                    sale = dict(symbol=holding["symbol"], pair=pair, quantity=ZERO, net_uah=ZERO,
                                available_quantity=holding["quantity"], remaining_quantity=holding["quantity"],
                                warning=str(error))
                fx_used += sale.get("net_usdt", ZERO)
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

    def _preview_migrated(self, symbol, journal=None, requested=None):
        if requested is not None:
            requested = number(requested).normalize()
            if not requested or symbol == "all":
                raise ValueError("Specify a positive quantity for one asset")
        if journal is None:
            journal = self._journal()
        holdings = [op for op in journal["holdings"] if symbol in ("all", op["symbol"])]
        if not holdings:
            raise MarketUnavailable("No virtual position to sell")
        currency = journal["currency"]
        markets = self.markets()
        fx = None
        if currency == "UAH":
            fx_market = markets.get(FX)
            if fx_market is None:
                raise MarketUnavailable("USDT/UAH spot market unavailable")
            fx = self.book(FX)
            fx["fee_rate"] = self.fee(fx_market)
        sales = []
        fx_used = ZERO
        for holding in holdings:
            market = markets[holding["pair"]]
            book = self.book(holding["pair"])
            fee = self.fee(market)
            step = max(Decimal(1).scaleb(-int(market["stockPrec"])), number(market.get("stepSize") or "0"))
            available = number(holding["quantity"])
            if requested is not None and requested > available:
                raise ValueError("Quantity exceeds the position balance")
            if requested is not None and requested % step:
                raise ValueError("Quantity does not match the market step")
            target = available if requested is None else requested
            quantity = floor_step(min(target, book["bid_size"]), step)
            total = quantity * book["bid"]
            if quantity < number(market["minAmount"]) or total < number(market["minTotal"]):
                quantity = ZERO
            fee_amount, net, net_currency, conversion_fee = sale_proceeds(quantity, book["bid"], fee, fx, fx_used)
            fx_used += net
            if currency == "UAH":
                cost = number(holding.get("basis_uah", "0"))
            else:
                cost = number(holding.get("basis_usdt", holding.get("debit_usdt", "0")))
            cost_for_fill = sold_basis(cost, quantity, available, CENT if fx else USDT_UNIT)
            sales.append(dict(symbol=holding["symbol"], pair=holding["pair"], side="sell",
                              available_quantity=available, quantity=quantity,
                              remaining_quantity=available - quantity, bid=book["bid"],
                              ask=book["ask"], spread=book["ask"] - book["bid"],
                              fee_rate=fee, fee_usdt=fee_amount, net_usdt=net, fx_fee_uah=conversion_fee,
                              net_currency=net_currency, cost_basis=cost,
                              realized_pnl=net_currency - cost_for_fill,
                              quote_timestamp=book["timestamp"], received_at=book["received_at"],
                              source=book["source"], warning=None if quantity == available else "Partial fill"))
        terms = [[sale["symbol"], sale["quantity"], sale["net_currency"]] for sale in sales]
        fingerprint = hashlib.sha256(exact_json([journal["revision"], symbol, requested, terms]).encode()).hexdigest()
        return decode(dict(symbol=symbol, revision=journal["revision"], sales=sales,
                           currency=currency, fx=fx, fingerprint=fingerprint))

    def sale_quote(self, holding, market, book, fx, fx_fee, fx_used):
        available = number(holding["quantity"])
        fee = self.fee(market)
        step = max(Decimal(1).scaleb(-int(market["stockPrec"])), number(market.get("stepSize") or "0"))
        price_step = max(Decimal(1).scaleb(-int(market["moneyPrec"])), number(market.get("tickSize") or "0"))
        remaining = floor_step(available, step)
        max_total = number(market["maxTotal"])
        budget = max_total if max_total else None
        fills = []
        for bid, size in book["bids"]:
            if bid % price_step:
                raise ValueError("Bid does not match market precision")
            limit = min(size, remaining)
            if budget is not None:
                limit = min(limit, budget / bid)
            quantity = floor_step(limit, step)
            if quantity:
                fills.append(dict(price=bid, quantity=quantity))
                remaining -= quantity
                if budget is not None:
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
        uah, conversion_fee = convert_sale_usdt(fx["bids"], net, fx_fee, fx_used)
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

    def sell(self, symbol, operation_id, fingerprint, quantity=None, *, legacy=False):
        migrated = self._migrated()
        if migrated and not legacy:
            return self._sell_migrated(symbol, operation_id, fingerprint, quantity)
        scope = 1 if migrated else None
        if not migrated:
            self.initialize()
        with closing(connect(self.path)) as db:
            existing = self.sale_receipt(db, operation_id, symbol, scope)
        if existing:
            return existing
        preview = self.preview(symbol, legacy=legacy)
        if preview["fingerprint"] != fingerprint:
            raise OperationConflict("Sale terms changed. Request a new preview.")
        sales = [sale for sale in preview["sales"] if number(sale["quantity"]) > 0]
        if not sales:
            raise MarketUnavailable("No quantity can be sold at current liquidity and market limits")
        with closing(connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            existing = self.sale_receipt(db, operation_id, symbol, scope)
            if existing:
                return existing
            journal = self._read_only_history(db) if migrated else self._legacy_journal(db)
            if journal["revision"] != preview["revision"]:
                raise OperationConflict("Portfolio changed. Request a new preview.")
            if not fresh(preview["fx"]["timestamp"]) or any(not fresh(sale["quote_timestamp"]) for sale in sales):
                raise MarketUnavailable("Sale quotes expired before saving")
            cash = number(journal["cash_uah"])
            stamp = now().isoformat()
            for sale in sales:
                cash += number(sale["net_uah"])
                sale.update(time=stamp, operation_id=operation_id,
                            cash_after_uah=str(cash), fx=preview["fx"])
            receipt = dict(operation_id=operation_id, sales=sales, net_uah=preview["net_uah"],
                           skipped=[sale for sale in preview["sales"] if not number(sale["quantity"])])
            if migrated:
                db.execute("INSERT INTO paper_sales (portfolio_id, operation_id, symbol, receipt) VALUES (1, ?, ?, ?)",
                           (operation_id, symbol, exact_json(receipt)))
            else:
                db.execute("INSERT INTO paper_sales VALUES (?, ?, ?)", (operation_id, symbol, exact_json(receipt)))
        return receipt

    def _sell_migrated(self, symbol, operation_id, fingerprint, quantity=None):
        quantity = None if quantity is None else number(quantity)
        with closing(connect(self.path)) as db, db:
            def prior_receipt():
                return self.sale_receipt(db, operation_id, symbol, self.portfolio_id, quantity)

            existing = prior_receipt()
            if existing:
                return existing
            try:
                preview = self.preview(symbol, quantity=quantity) if quantity is not None else self.preview(symbol)
            except MarketUnavailable:
                # A concurrent retry may already have closed the last position.
                existing = prior_receipt()
                if existing:
                    return existing
                raise

            db.execute("BEGIN IMMEDIATE")
            existing = prior_receipt()
            if existing:
                return existing
            revision = db.execute("SELECT COUNT(*) FROM paper_sales WHERE portfolio_id=?",
                                  (self.portfolio_id,)).fetchone()[0]
            if revision != preview["revision"] or fingerprint != preview["fingerprint"]:
                raise OperationConflict("Sale terms changed. Request a new preview.")
            fx = preview["fx"]
            if fx and not fresh(fx["timestamp"]):
                raise MarketUnavailable("Sale quotes expired before saving")
            stamp = now().isoformat()
            sales = []
            fx_used = ZERO
            for sale in preview["sales"]:
                units = number(sale["quantity"])
                if units <= 0:
                    continue
                if not fresh(sale["quote_timestamp"]):
                    raise MarketUnavailable("Sale quotes expired before saving")
                available_total = number(sale["available_quantity"])
                basis = number(sale["cost_basis"])
                cost = sold_basis(basis, units, available_total, CENT if fx else USDT_UNIT)
                fee, net, credit, conversion_fee = sale_proceeds(
                    units, number(sale["bid"]), number(sale["fee_rate"]), fx, fx_used
                )
                fx_used += net
                sales.append(dict(sale, time=stamp, operation_id=operation_id, quantity=units,
                                  remaining_quantity=available_total - units, fee_usdt=fee, net_usdt=net,
                                  net_currency=credit, fx_fee_uah=conversion_fee,
                                  cost_basis=cost, remaining_basis=basis - cost,
                                  realized_pnl=credit - cost))
            if not sales:
                raise MarketUnavailable("No quantity can be sold")
            receipt = dict(operation_id=operation_id, requested_quantity=quantity, portfolio_id=self.portfolio_id,
                           currency=preview["currency"], sales=sales,
                           realized_pnl=sum((sale["realized_pnl"] for sale in sales), ZERO),
                           net_currency=sum((sale["net_currency"] for sale in sales), ZERO))
            db.execute("INSERT INTO paper_sales (portfolio_id, operation_id, symbol, receipt) VALUES (?, ?, ?, ?)",
                       (self.portfolio_id, operation_id, symbol, exact_json(receipt)))
        return decode(receipt)

    @staticmethod
    def sale_receipt(db, operation_id, symbol, portfolio_id=None, quantity=None):
        if portfolio_id is None:
            row = db.execute("SELECT symbol, receipt FROM paper_sales WHERE operation_id=?", (operation_id,)).fetchone()
        else:
            row = db.execute("SELECT symbol, receipt FROM paper_sales WHERE portfolio_id=? AND operation_id=?",
                             (portfolio_id, operation_id)).fetchone()
        if row is None:
            return None
        if row[0] != symbol:
            raise OperationConflict("Operation identifier already used for another sale")
        receipt = json.loads(row[1])
        # Older receipts did not retain the requested quantity. Replay the
        # original result for their symbol without booking another fill.
        if "requested_quantity" in receipt:
            requested = receipt["requested_quantity"]
            if quantity != (None if requested is None else number(requested)):
                raise OperationConflict("Operation identifier already used with another quantity")
        return receipt

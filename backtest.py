"""Standalone BTC/USDT daily backtest. Never opens the paper portfolio DB."""

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlencode
from urllib.request import Request, urlopen

DAY = 86_400
SOURCE = "https://whitebit.com/api/v1/public/kline"
DEFAULT_CACHE = Path(__file__).resolve().parent / "data" / "whitebit_btc_usdt_1d.json"
D = Decimal


@dataclass(frozen=True)
class Candle:
    timestamp: int
    open: Decimal
    close: Decimal


@dataclass(frozen=True)
class Costs:
    fee_percent: Decimal
    spread_bps: Decimal
    slippage_bps: Decimal

    def rates(self):
        if any(not value.is_finite() or value < 0 for value in asdict(self).values()):
            raise ValueError("Costs must be finite and nonnegative")
        fee = self.fee_percent / 100
        impact = (self.spread_bps / 2 + self.slippage_bps) / 10_000
        if fee >= 1 or impact >= 1:
            raise ValueError("Fee and per-side price impact must be below 100%")
        return fee, impact


def fetch_candles(start, end):
    # WhiteBIT's daily response ends before the UTC bucket containing its end
    # parameter. Advance that boundary one bucket and validate returned starts.
    params = urlencode(dict(market="BTC_USDT", interval="1d", start=start,
                            end=end + DAY - 1, limit=(end - start) // DAY))
    request = Request(f"{SOURCE}?{params}", headers={"User-Agent": "CryptoControl/1.0"})
    with urlopen(request, timeout=15) as response:
        payload = json.load(response)
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise ValueError("WhiteBIT rejected the candle request")
    rows = payload.get("result")
    if not isinstance(rows, list):
        raise ValueError("WhiteBIT returned an invalid candle list")
    return rows


def validate(rows, start, end):
    if not isinstance(rows, list):
        raise ValueError("Expected a list of candle rows")
    indexed = {}
    for row in rows:
        if not isinstance(row, list) or len(row) != 7:
            raise ValueError("Invalid candle row")
        timestamp = row[0]
        if type(timestamp) is not int or timestamp % DAY or not start <= timestamp < end:
            raise ValueError(f"Candle outside the requested UTC grid: {timestamp}")
        if timestamp in indexed:
            raise ValueError(f"Duplicate candle: {timestamp}")
        try:
            open_, close, high, low, base_volume, quote_volume = map(D, map(str, row[1:]))
        except InvalidOperation as error:
            raise ValueError("Invalid candle number") from error
        if (not all(v.is_finite() for v in (open_, close, high, low, base_volume, quote_volume))
                or not 0 < low <= min(open_, close) <= max(open_, close) <= high
                or min(base_volume, quote_volume) <= 0):
            raise ValueError(f"Invalid OHLC or volume: {timestamp}")
        indexed[timestamp] = Candle(timestamp, open_, close)
    for timestamp in range(start, end, DAY):
        if timestamp not in indexed:
            missing = datetime.fromtimestamp(timestamp, timezone.utc).date()
            raise ValueError(f"Missing candle for {missing} UTC")
    return [indexed[timestamp] for timestamp in sorted(indexed)]


def load_candles(path, start, end, fetch=fetch_candles):
    if start % DAY or end % DAY or not start < end or end > int(datetime.now(timezone.utc).timestamp()):
        raise ValueError("Use a nonempty range of completed UTC days")
    raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if not isinstance(raw, dict):
        raise ValueError("Invalid candle cache")
    cursor = start
    changed = False
    while cursor < end:
        if str(cursor) in raw:
            cursor += DAY
            continue
        stop = cursor + DAY
        while stop < min(end, cursor + 365 * DAY) and str(stop) not in raw:
            stop += DAY
        rows = fetch(cursor, stop)
        validate(rows, cursor, stop)
        raw.update((str(row[0]), row) for row in rows)
        changed = True
        cursor = stop
    candles = validate([raw[str(t)] for t in range(start, end, DAY)], start, end)
    if changed:
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as temp:
            temp_path = Path(temp.name)
            json.dump(raw, temp, separators=(",", ":"))
        try:
            temp_path.replace(path)
        finally:
            temp_path.unlink(missing_ok=True)
    return candles


def simulate(candles, capital, costs, strategy, *, fast=10, slow=30, tp=D(2), sl=D(1)):
    if (not candles or not capital.is_finite() or capital <= 0 or not 0 < fast < slow
            or not tp.is_finite() or not sl.is_finite() or not 0 < sl < 100 or tp <= 0
            or strategy not in {"hold", "ma", "tp_sl"}):
        raise ValueError("Invalid simulation parameters")
    fee, impact = costs.rates()
    cash, units, entry, basis = capital, D(0), D(0), D(0)
    peak, drawdown = capital, D(0)
    trades = wins = 0
    pending = "buy" if strategy != "ma" else None
    closes = []
    previous_above = None

    def sell(price):
        nonlocal cash, units, trades, wins
        cash = units * price * (1 - impact) * (1 - fee)
        units = D(0)
        trades += 1
        wins += int(cash > basis)

    for index, candle in enumerate(candles):
        if pending == "buy":
            entry = candle.open * (1 + impact)
            basis = cash
            units = cash / entry * (1 - fee)
            cash = D(0)
        elif pending == "sell":
            sell(candle.open)
        pending = None

        # Both segments liquidate on their predeclared final close.
        if index == len(candles) - 1 and units:
            sell(candle.close)
        equity = cash + units * candle.close * (1 - impact) * (1 - fee)
        peak = max(peak, equity)
        drawdown = max(drawdown, (peak - equity) / peak * 100)

        closes.append(candle.close)
        if strategy == "ma" and len(closes) >= slow:
            above = sum(closes[-fast:]) / fast > sum(closes[-slow:]) / slow
            if above and previous_above is False and not units:
                pending = "buy"
            elif not above and previous_above is True and units:
                pending = "sell"
            previous_above = above
        elif strategy == "tp_sl":
            if not units:
                pending = "buy"
            elif candle.close >= entry * (1 + tp / 100) or candle.close <= entry * (1 - sl / 100):
                pending = "sell"
    return dict(balance=cash, return_percent=(cash / capital - 1) * 100,
                drawdown_percent=drawdown, trades=trades,
                win_percent=D(wins) / trades * 100 if trades else None)


def utc_day(value):
    day = date.fromisoformat(value)
    return int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ("start", "split", "end"):
        parser.add_argument(f"--{field}", required=True, type=utc_day, metavar="YYYY-MM-DD")
    parser.add_argument("--capital", type=D, default=D(1000))
    parser.add_argument("--fee", type=D, default=D("0.1"), help="fee percent per side")
    parser.add_argument("--spread-bps", type=D, default=D(10), help="full spread")
    parser.add_argument("--slippage-bps", type=D, default=D(5), help="per side")
    parser.add_argument("--fast", type=int, default=10)
    parser.add_argument("--slow", type=int, default=30)
    parser.add_argument("--tp-sl", nargs=2, type=D, action="append", metavar=("TP_PERCENT", "SL_PERCENT"))
    args = parser.parse_args()
    costs = Costs(args.fee, args.spread_bps, args.slippage_bps)
    try:
        if not args.start < args.split < args.end:
            raise ValueError("Expected start < split < end")
        candles = load_candles(DEFAULT_CACHE, args.start, args.end)
        training = [c for c in candles if c.timestamp < args.split]
        holdout = [c for c in candles if c.timestamp >= args.split]
        if min(len(training), len(holdout)) < args.slow + 2:
            raise ValueError("Each segment needs at least slow + 2 daily candles")
        choices = args.tp_sl or [(D(2), D(1)), (D(4), D(2)), (D(6), D(3))]

        def run(segment, strategy, **params):
            return simulate(segment, args.capital, costs, strategy, fast=args.fast, slow=args.slow, **params)

        candidates = [dict(tp=tp, sl=sl, **run(training, "tp_sl", tp=tp, sl=sl)) for tp, sl in choices]
        best = max(candidates, key=lambda row: row["balance"])
        report = dict(
            source=SOURCE, market="BTC_USDT", interval="1d UTC", currency="USDT",
            start=datetime.fromtimestamp(args.start, timezone.utc).date().isoformat(),
            split=datetime.fromtimestamp(args.split, timezone.utc).date().isoformat(),
            end_exclusive=datetime.fromtimestamp(args.end, timezone.utc).date().isoformat(),
            capital=args.capital, costs_assumed=asdict(costs), ma_windows=[args.fast, args.slow],
            quality=dict(candles=len(candles), missing=0, duplicates=0),
            tp_sl_training_candidates=candidates, selected_tp_sl=[best["tp"], best["sl"]],
            training=dict(candles=len(training), hold=run(training, "hold"), ma=run(training, "ma"), tp_sl=best),
            holdout=dict(candles=len(holdout), hold=run(holdout, "hold"), ma=run(holdout, "ma"),
                         tp_sl=run(holdout, "tp_sl", tp=best["tp"], sl=best["sl"])),
        )
        print(json.dumps(report, indent=2, default=str))
    except (OSError, ValueError, InvalidOperation) as error:
        parser.exit(1, f"Backtest failed: {error}\n")


if __name__ == "__main__":
    main()

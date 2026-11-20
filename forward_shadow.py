"""Forward-only daily observations. All times are UTC Unix seconds."""
import argparse
import json
import os
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal as D, InvalidOperation
from http.client import HTTPException
from pathlib import Path

from backtest import Costs, DAY, fetch_candles
from quotes import fetch_public
from shadow import daily_record

DATABASE = Path(__file__).resolve().parent / "research" / "forward-sma-v1.sqlite3"
LAST_RESEARCH_CLOSE = 1795046400
FEE, IMPACT = Costs(D(".1"), D(10), D(5)).rates()


def market_mid(snapshot, received):
    stamp = int(snapshot["timestamp"])
    bid, ask = (D(str(snapshot[key][0][0])) for key in ("bids", "asks"))
    if (snapshot["ticker_id"] != "BTC_USDT" or not bid.is_finite() or not ask.is_finite()
            or not 0 < bid <= ask or not 0 <= received - stamp <= 60):
        raise ValueError("Invalid or stale order book")
    return stamp, (bid + ask) / 2


def initialize(db, now):
    with db:
        db.execute("CREATE TABLE IF NOT EXISTS experiment(id INTEGER PRIMARY KEY CHECK(id=1), t0 REAL NOT NULL)")
        db.execute("INSERT OR IGNORE INTO experiment VALUES(1,?)", (now,))
        db.execute("""CREATE TABLE IF NOT EXISTS days(
            end INTEGER PRIMARY KEY, kind TEXT, signal TEXT, close TEXT, fast TEXT, slow TEXT,
            reason TEXT, observed REAL, price TEXT, market_time INTEGER, received REAL)""")


def cutoff(db):
    return max(LAST_RESEARCH_CLOSE, db.execute("SELECT t0 FROM experiment WHERE id=1").fetchone()[0])


def poll(db, fetch=fetch_candles, book=fetch_public, clock=time.time):
    end = int(clock()) // DAY * DAY
    if end <= cutoff(db):
        return
    known = db.execute("SELECT kind,price FROM days WHERE end=?", (end,)).fetchone()
    if not known or known[0] == "skip":
        record = daily_record(end, fetch)
        observed = clock()
        if observed >= end + DAY:
            return
        with db:
            db.execute("""INSERT INTO days(end,kind,signal,close,fast,slow,reason,observed)
                VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(end) DO UPDATE SET kind=excluded.kind,
                signal=excluded.signal,close=excluded.close,fast=excluded.fast,slow=excluded.slow,
                reason=excluded.reason,observed=excluded.observed WHERE days.kind='skip'""",
                       (end, *record, observed))
    with db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT observed FROM days WHERE end=? AND kind='signal' AND price IS NULL",
                         (end,)).fetchone()
        if not row:
            return
        try:
            snapshot = book("orderbook/BTC_USDT?limit=50")
            received = clock()
            stamp, price = market_mid(snapshot, received)
            if not row[0] < stamp or received >= end + DAY:
                return
        except (HTTPException, OSError, ValueError, TypeError, KeyError, IndexError, InvalidOperation):
            return
        db.execute("UPDATE days SET price=?,market_time=?,received=? WHERE end=?",
                   (str(price), stamp, received, end))


def report(db, now):
    cash, btc = D(1000), D(0)
    valid = True
    for end in range((int(cutoff(db)) // DAY + 1) * DAY, int(now) + 1, DAY):
        row = db.execute("SELECT kind,signal,close,reason,observed,price,market_time,received FROM days WHERE end=?",
                         (end,)).fetchone()
        kind, signal, close, reason, observed, raw, stamp, received = row or (
            "missing", None, None, "No observation", None, None, None, None)
        price = D(raw) if raw else None
        pnl = None
        if kind != "signal" or (price is None and ((signal == "BUY" and cash) or (signal == "SELL" and btc))):
            valid = False
        if valid and price:
            if signal == "BUY" and cash:
                btc, cash = cash / (price * (1 + IMPACT)) * (1 - FEE), D(0)
            elif signal == "SELL" and btc:
                cash, btc = btc * price * (1 - IMPACT) * (1 - FEE), D(0)
            pnl = str(cash + btc * price * (1 - IMPACT) * (1 - FEE) - 1000)
        yield dict(candle_day=str(datetime.fromtimestamp(end-DAY, timezone.utc).date()),
                   status=kind, signal=signal, close=close, reason=reason, observed=observed,
                   spot_mid=raw, market_time=stamp, received=received, pnl_usdt=pnl,
                   risk="observation only")


def observe(db, book=fetch_public, clock=time.time):
    # No strategy is admitted. Record abstention and the evidence available at the time.
    db.execute("""CREATE TABLE IF NOT EXISTS observations(
        slot INTEGER PRIMARY KEY, observed INTEGER NOT NULL, market_time INTEGER,
        mid TEXT, decision TEXT NOT NULL, reason TEXT NOT NULL)""")
    slot = int(clock()) // 300
    if not db.execute("SELECT 1 FROM observations WHERE slot=?", (slot,)).fetchone():
        try:
            snapshot = book("orderbook/BTC_USDT?limit=5")
            observed = int(clock())
            stamp, price = market_mid(snapshot, observed)
            entry = (stamp, str(price), "ABSTAIN", "no_candidate")
        except (HTTPException, OSError, ValueError, TypeError, KeyError, IndexError, InvalidOperation):
            observed = int(clock())
            entry = (None, None, "SKIP", "no_fresh_quote")
        with db:
            db.execute("INSERT OR IGNORE INTO observations VALUES(?,?,?,?,?,?)",
                       (slot, observed, *entry))
    db.row_factory = sqlite3.Row
    rows = db.execute("SELECT * FROM observations ORDER BY slot DESC LIMIT 30").fetchall()
    first, count = db.execute("SELECT MIN(slot), COUNT(*) FROM observations").fetchone()
    return dict(entries=[dict(row) for row in rows], observed=rows[0]["observed"],
                missing=rows[0]["slot"] - first + 1 - count,
                position_btc="0", pnl_usdt="0", max_drawdown_pct="0")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run", "report", "observe"))
    parser.add_argument("--database", type=Path, default=DATABASE)
    parser.add_argument("--snapshot", type=Path)
    args = parser.parse_args()
    action, database = args.action, args.database.resolve()
    if action == "observe" and not args.snapshot:
        parser.error("observe requires --snapshot")
    if action == "report":
        with closing(sqlite3.connect(database.as_uri()+"?mode=ro", uri=True)) as db:
            for day in report(db, time.time()):
                print(json.dumps(day))
        return
    database.parent.mkdir(exist_ok=True)
    with closing(sqlite3.connect(database)) as db:
        initialize(db, time.time())
        while True:
            if action == "observe":
                snapshot = observe(db)
                staging = args.snapshot.with_suffix(".tmp")
                staging.write_text(json.dumps(snapshot), encoding="utf-8")
                os.chmod(staging, 0o644)
                staging.replace(args.snapshot)
                time.sleep(300 - time.time() % 300)
            else:
                poll(db)
                time.sleep(300)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass

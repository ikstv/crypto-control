"""BTC/USDT daily crossover observations. This module only writes its own log."""

import sqlite3
import time
from contextlib import closing
from http.client import HTTPException

from algorithm import Blocked
from backtest import DAY, SOURCE, fetch_candles, validate

CONFIG = "WhiteBIT:BTC_USDT:1d:SMA10/30:v1"
SCHEMA = """CREATE TABLE IF NOT EXISTS shadow_log (
    id INTEGER PRIMARY KEY, config TEXT NOT NULL, candle_open INTEGER NOT NULL,
    observed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    kind TEXT NOT NULL, decision TEXT, close TEXT, sma10 TEXT, sma30 TEXT,
    reason TEXT NOT NULL, UNIQUE(config, candle_open, kind)
)"""


class Shadow:
    def __init__(self, algorithm):
        self.algorithm = algorithm

    def history(self):
        with closing(sqlite3.connect(self.algorithm.path)) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='shadow_log'").fetchone():
                return []
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute(
                "SELECT * FROM shadow_log ORDER BY candle_open DESC, id DESC LIMIT 100")]

    def poll(self, fetch=fetch_candles, timestamp=None):
        if self.algorithm.status() != "active":
            return
        end = int(time.time() if timestamp is None else timestamp) // DAY * DAY
        start = end - 31 * DAY
        try:
            rows = fetch(start, end)
            # Never allow the in-progress UTC bucket into either moving average.
            rows = [row for row in rows if not (
                isinstance(row, list) and row and type(row[0]) is int and row[0] >= end)]
            closes = [c.close for c in validate(rows, start, end)]
            fast_before, slow_before = sum(closes[-11:-1]) / 10, sum(closes[:-1]) / 30
            fast, slow = sum(closes[-10:]) / 10, sum(closes[-30:]) / 30
            if fast_before <= slow_before and fast > slow:
                decision, reason = "BUY", "SMA10 crossed above SMA30"
            elif fast_before >= slow_before and fast < slow:
                decision, reason = "SELL", "SMA10 crossed below SMA30"
            else:
                decision, reason = "HOLD", "No SMA10/SMA30 crossover"
            record = ("signal", decision, str(closes[-1]), str(fast), str(slow), reason)
        except (OSError, HTTPException):
            record = ("skip", None, None, None, None, "WhiteBIT candles unavailable")
        except (ValueError, TypeError) as error:
            record = ("skip", None, None, None, None, str(error))

        def save(db):
            db.execute(SCHEMA)
            if record[0] == "skip" and db.execute(
                "SELECT 1 FROM shadow_log WHERE config=? AND candle_open=? AND kind='signal'",
                (CONFIG, end - DAY),
            ).fetchone():
                return
            db.execute("""INSERT OR IGNORE INTO shadow_log
                (config,candle_open,kind,decision,close,sma10,sma30,reason)
                VALUES (?,?,?,?,?,?,?,?)""", (CONFIG, end - DAY, *record))

        try:
            self.algorithm.run(save)
        except Blocked:
            pass

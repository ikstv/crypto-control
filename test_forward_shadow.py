import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from decimal import Decimal
from pathlib import Path

from forward_shadow import DAY, LAST_RESEARCH_CLOSE, initialize, observe, poll, report


class ForwardTest(unittest.TestCase):
    def test_observation_only_restart_and_stale_book(self):
        now = LAST_RESEARCH_CLOSE
        def book(_):
            return json.dumps(dict(ticker_id="BTC_USDT", bids=[["99","1"]],
                                   asks=[["101","1"]], timestamp=now)).encode()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "shadow.db"
            with closing(sqlite3.connect(path)) as db:
                first = observe(db, book, lambda: now)
                self.assertEqual(first["entries"][0]["mid"], "100")
                row = db.execute("SELECT bid,ask,spread,raw,sha256 FROM book_evidence").fetchone()
                self.assertEqual(tuple(row), ("99","101","2",book(None),hashlib.sha256(book(None)).hexdigest()))
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute("DELETE FROM book_evidence")
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(observe(db, lambda _: self.fail(), lambda: now), first)
                later = observe(db, book, lambda: now + 600)
                self.assertEqual(later["missing"], 1)
                self.assertEqual([r["decision"] for r in later["entries"]], ["SKIP", "ABSTAIN"])
                self.assertIsNone(later["entries"][0]["mid"])
                self.assertEqual(later["pnl_usdt"], "0")

    def test_forward_replay(self):
        end = LAST_RESEARCH_CLOSE + DAY
        def candles(start, stop):
            return [[t, p, p, p, p, 1, 1] for t in range(start, stop, DAY)
                    for p in [130 if t == stop-DAY else 100]]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.sqlite3"
            with closing(sqlite3.connect(path)) as db:
                initialize(db, end-1)
                poll(db, lambda *_: self.fail(), clock=lambda: end-1)
                now = end+5
                def book(_):
                    return dict(ticker_id="BTC_USDT", bids=[["99","1"]], asks=[["101","1"]], timestamp=now)
                poll(db, candles, book, lambda: now)
                self.assertIsNone(list(report(db,now))[0]["pnl_usdt"])
                now += 1
                poll(db, candles, book, lambda: now)
                poll(db, lambda *_: self.fail(), lambda *_: self.fail(), lambda: now)
                first, missing = report(db,end+DAY)
                self.assertEqual((first["signal"], first["spot_mid"], missing["status"]), ("BUY","100","missing"))
                expected = Decimal(1000)/Decimal("1.001")*Decimal(".999")**3-1000
                self.assertAlmostEqual(Decimal(first["pnl_usdt"]),expected)
                def outage(*_):
                    raise OSError()
                poll(db,outage,book,lambda: end+2*DAY)
                saved = list(report(db,end+2*DAY))
                self.assertEqual(saved[-1]["status"],"skip")
            with closing(sqlite3.connect(path)) as db:
                initialize(db,end+3*DAY)
                self.assertEqual(list(report(db,end+2*DAY)),saved)

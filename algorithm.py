"""Persistent paper algorithm controls and transactional execution gate."""

import sqlite3
from contextlib import closing, contextmanager


class Blocked(Exception):
    pass


class Algorithm:
    def __init__(self, path):
        self.path = path

    @contextmanager
    def transaction(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""CREATE TABLE IF NOT EXISTS algorithm_log (
                id INTEGER PRIMARY KEY, at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                previous TEXT NOT NULL, mode TEXT NOT NULL
            )""")
            yield db

    @staticmethod
    def mode(db):
        row = db.execute("SELECT mode FROM algorithm_log ORDER BY id DESC LIMIT 1").fetchone()
        return row[0] if row else "stopped"

    def status(self):
        with self.transaction() as db:
            return self.mode(db)

    def history(self):
        with self.transaction() as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute(
                "SELECT * FROM algorithm_log ORDER BY id DESC LIMIT 100")]

    def change(self, action, confirmed=False):
        target = dict(activate="active", stop="stopped", emergency="blocked", reset="stopped")[action]
        with self.transaction() as db:
            previous = self.mode(db)
            if action == "reset" and (previous != "blocked" or not confirmed):
                raise Blocked("Confirm reset of the emergency lock")
            if previous == "blocked" and action not in ("reset", "emergency"):
                raise Blocked("Emergency lock requires a confirmed reset")
            if target != previous:
                db.execute("INSERT INTO algorithm_log(previous, mode) VALUES (?, ?)",
                           (previous, target))
            return target

    def run(self, operation):
        """Future automated writes must use this connection without committing it."""
        with self.transaction() as db:
            if self.mode(db) != "active":
                raise Blocked("Automatic operation is disabled")
            return operation(db)

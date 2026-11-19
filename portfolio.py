"""SQLite storage and portfolio calculations in integer kopiykas."""

import sqlite3
from contextlib import closing

SYMBOLS = ("BTC", "ETH", "SOL", "XRP", "BNB", "LINK", "LTC", "ADA", "TRX")


def initialize(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("""CREATE TABLE IF NOT EXISTS settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            capital_minor INTEGER NOT NULL CHECK (capital_minor >= 0)
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS positions (
            symbol TEXT PRIMARY KEY NOT NULL,
            allocation_minor INTEGER NOT NULL CHECK (allocation_minor >= 0)
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY,
            event TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        seeded = db.execute("INSERT OR IGNORE INTO settings VALUES (1, 1000000)")
        if seeded.rowcount:
            db.executemany(
                "INSERT INTO positions VALUES (?, ?)",
                [(symbol, 200_000 if symbol == "BNB" else 100_000) for symbol in SYMBOLS],
            )
            db.execute("INSERT INTO history (event) VALUES ('demo_initialized')")


def read_portfolio(path):
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("BEGIN")
        capital = db.execute("SELECT capital_minor FROM settings WHERE id = 1").fetchone()[0]
        positions = [
            {"symbol": symbol, "allocation_minor": amount}
            for symbol, amount in db.execute(
                "SELECT symbol, allocation_minor FROM positions ORDER BY rowid"
            )
        ]
    allocated = sum(position["allocation_minor"] for position in positions)
    return {
        "currency": "UAH",
        "initial_capital_minor": capital,
        "allocated_minor": allocated,
        "unallocated_minor": capital - allocated,
        "positions": positions,
    }

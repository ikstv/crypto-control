"""SQLite storage and portfolio calculations in integer kopiykas."""

import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN

SYMBOLS = ("BTC", "ETH", "SOL", "XRP", "BNB", "LINK", "LTC", "ADA", "TRX")


def connect(path):
    db = sqlite3.connect(path)
    db.execute("PRAGMA foreign_keys = ON")
    return db


def ledger_schema_migrated(path):
    with closing(connect(path)) as db:
        return "portfolio_id" in {row[1] for row in db.execute("PRAGMA table_info(settings)")}


def existing_portfolio_ids(path):
    with closing(connect(path)) as db:
        return [row[0] for row in db.execute("SELECT id FROM portfolios ORDER BY id")]


def initialize(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(connect(path)) as db, db:
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


def read_portfolio(path, portfolio_id=None):
    with closing(connect(path)) as db, db:
        db.execute("BEGIN")
        settings_columns = {row[1] for row in db.execute("PRAGMA table_info(settings)")}
        if "portfolio_id" in settings_columns:
            if portfolio_id is None:
                raise ValueError("portfolio_id is required for migrated schema")
            row = db.execute("SELECT capital_minor FROM settings WHERE portfolio_id = ?", (portfolio_id,)).fetchone()
            if row is None:
                raise ValueError("Unknown portfolio")
            capital = row[0]
            positions = [
                {"symbol": symbol, "allocation_minor": amount}
                for symbol, amount in db.execute(
                    "SELECT symbol, allocation_minor FROM positions WHERE portfolio_id = ? ORDER BY rowid",
                    (portfolio_id,),
                )
            ]
            currency = db.execute("SELECT currency FROM portfolios WHERE id = ?", (portfolio_id,)).fetchone()
            if currency is None:
                raise ValueError("Unknown portfolio")
            currency = currency[0]
        else:
            capital = db.execute("SELECT capital_minor FROM settings WHERE id = 1").fetchone()[0]
            positions = [
                {"symbol": symbol, "allocation_minor": amount}
                for symbol, amount in db.execute(
                    "SELECT symbol, allocation_minor FROM positions ORDER BY rowid"
                )
            ]
            currency = "UAH"
    allocated = sum(position["allocation_minor"] for position in positions)
    return {
        "currency": currency,
        "scale": 2 if currency == "UAH" else 6,
        "initial_capital_minor": capital,
        "allocated_minor": allocated,
        "unallocated_minor": capital - allocated,
        "positions": positions,
    }


def _money_minor(raw, currency):
    scale = 2 if currency == "UAH" else 6 if currency == "USDT" else None
    if scale is None:
        raise ValueError("Currency must be UAH or USDT")
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError) as error:
        raise ValueError("Invalid initial capital") from error
    quantum = Decimal(1).scaleb(-scale)
    if not value.is_finite() or value <= 0 or value.quantize(quantum, rounding=ROUND_DOWN) != value:
        raise ValueError(f"Initial capital must have at most {scale} decimal places")
    return int(value * (10 ** scale))


def migrate_portfolios(path):
    """Create the portfolio registry and map the legacy demo portfolio once."""
    initialize(path)
    with closing(connect(path)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        return _create_portfolio_registry(db)


def _create_portfolio_registry(db):
    db.execute("""CREATE TABLE IF NOT EXISTS portfolios (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 100),
        currency TEXT NOT NULL CHECK (currency IN ('UAH', 'USDT')),
        initial_capital_minor INTEGER NOT NULL CHECK (initial_capital_minor > 0),
        scale INTEGER NOT NULL CHECK (scale IN (2, 6)),
        idempotency_key TEXT UNIQUE,
        created_at TEXT NOT NULL,
        CHECK ((currency = 'UAH' AND scale = 2) OR (currency = 'USDT' AND scale = 6))
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS portfolio_observations (
        portfolio_id INTEGER NOT NULL REFERENCES portfolios(id) ON DELETE CASCADE,
        slot TEXT NOT NULL,
        observed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        status TEXT NOT NULL,
        reason TEXT,
        equity_minor INTEGER,
        PRIMARY KEY (portfolio_id, slot)
    )""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_portfolio_observations_portfolio ON portfolio_observations(portfolio_id, slot)")
    row = db.execute("SELECT id FROM portfolios WHERE id = 1").fetchone()
    if row is None:
        capital = db.execute("SELECT capital_minor FROM settings WHERE id = 1").fetchone()[0]
        db.execute("""INSERT INTO portfolios
            (id, name, currency, initial_capital_minor, scale, idempotency_key, created_at)
            VALUES (1, 'Main portfolio', 'UAH', ?, 2, 'legacy-main', ?)""",
            (capital, datetime.now(timezone.utc).isoformat()))
    return {"legacy_id": 1}


def list_portfolios(path):
    migrate_portfolios(path)
    with closing(connect(path)) as db:
        rows = db.execute("""SELECT id, name, currency, initial_capital_minor,
            scale, created_at FROM portfolios ORDER BY id""").fetchall()
    keys = ("id", "name", "currency", "initial_capital_minor", "scale", "created_at")
    return [dict(zip(keys, row)) for row in rows]


def list_existing_portfolios(path):
    with closing(connect(path)) as db:
        if not ledger_schema_migrated(path):
            return []
        rows = db.execute("""SELECT id, name, currency, initial_capital_minor,
            scale, created_at FROM portfolios ORDER BY id""").fetchall()
    keys = ("id", "name", "currency", "initial_capital_minor", "scale", "created_at")
    return [dict(zip(keys, row)) for row in rows]


def create_portfolio(path, name, currency, initial_capital, idempotency_key, migrate=True):
    if migrate:
        migrate_portfolios(path)
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 100:
        raise ValueError("Portfolio name is required")
    if not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 200:
        raise ValueError("Idempotency key is required")
    minor = _money_minor(initial_capital, currency)
    scale = 2 if currency == "UAH" else 6
    with closing(connect(path)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute("SELECT id, name, currency, initial_capital_minor, scale FROM portfolios WHERE idempotency_key = ?", (idempotency_key,)).fetchone()
        if existing:
            if existing[1:] != (name.strip(), currency, minor, scale):
                raise ValueError("Idempotency key already used with different parameters")
            return dict(zip(("id", "name", "currency", "initial_capital_minor", "scale"), existing))
        cursor = db.execute("""INSERT INTO portfolios
            (name, currency, initial_capital_minor, scale, idempotency_key, created_at)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (name.strip(), currency, minor, scale, idempotency_key, datetime.now(timezone.utc).isoformat()))
        columns = {row[1] for row in db.execute("PRAGMA table_info(settings)")}
        if "portfolio_id" in columns:
            db.execute("INSERT INTO settings (portfolio_id, capital_minor) VALUES (?, ?)",
                       (cursor.lastrowid, minor))
        return {"id": cursor.lastrowid, "name": name.strip(), "currency": currency,
                "initial_capital_minor": minor, "scale": scale}


def read_portfolio_for(path, portfolio_id):
    if portfolio_id is None:
        return read_portfolio(path)
    migrate_portfolios(path)
    with closing(connect(path)) as db:
        row = db.execute("SELECT id, name, currency, initial_capital_minor, scale FROM portfolios WHERE id = ?", (portfolio_id,)).fetchone()
    if row is None:
        raise ValueError("Unknown portfolio")
    return dict(id=row[0], name=row[1], currency=row[2], initial_capital_minor=row[3], scale=row[4])


def portfolio_ids(path):
    return [portfolio["id"] for portfolio in list_portfolios(path)]


def record_portfolio_observation(path, portfolio_id, slot, status, reason=None, equity_minor=None):
    migrate_portfolios(path)
    with closing(connect(path)) as db, db:
        inserted = db.execute("""INSERT OR IGNORE INTO portfolio_observations
            (portfolio_id, slot, status, reason, equity_minor)
            VALUES (?, ?, ?, ?, ?)""", (portfolio_id, slot, status, reason, equity_minor)).rowcount
    return inserted == 1


def portfolio_observations(path, portfolio_id):
    migrate_portfolios(path)
    with closing(connect(path)) as db:
        rows = db.execute("""SELECT portfolio_id, slot, observed_at, status, reason, equity_minor
            FROM portfolio_observations WHERE portfolio_id = ? ORDER BY slot""", (portfolio_id,)).fetchall()
    keys = ("portfolio_id", "slot", "observed_at", "status", "reason", "equity_minor")
    return [dict(zip(keys, row)) for row in rows]


def migrate_ledger_schema(path):
    """Rebuild legacy ledger tables with portfolio foreign keys on a copied DB.

    This function is deliberately not called by application startup. Callers must
    create and verify a backup/copy before invoking it.
    """
    raw = sqlite3.connect(path.resolve().as_uri() + "?mode=rw", uri=True)
    try:
        # Table renames temporarily detach ledger rows from their final schema.
        # Check all foreign keys on the rebuilt schema before committing.
        raw.execute("PRAGMA foreign_keys=OFF")
        raw.execute("BEGIN IMMEDIATE")
        tables = {row[0] for row in raw.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "settings" not in tables:
            raise ValueError("Missing legacy settings table")
        columns = {row[1] for row in raw.execute("PRAGMA table_info(settings)")}
        if "portfolio_id" in columns:
            equity_columns = {row[1] for row in raw.execute("PRAGMA table_info(paper_equity)")} if "paper_equity" in tables else set()
            if "equity_currency" not in equity_columns and "paper_equity" in tables:
                raw.execute("ALTER TABLE paper_equity ADD COLUMN equity_currency TEXT NOT NULL DEFAULT 'UAH'")
            _check_migration(raw)
            raw.commit()
            return {"status": "already_migrated"}

        _create_portfolio_registry(raw)
        renamed = set()
        for table in ("settings", "positions", "history", "paper_start", "paper_operations",
                      "paper_sales", "paper_risk", "paper_equity", "paper_observations"):
            if table in tables:
                raw.execute(f'ALTER TABLE "{table}" RENAME TO "__legacy_{table}"')
                renamed.add(table)

        raw.execute("""CREATE TABLE settings (
            portfolio_id INTEGER PRIMARY KEY REFERENCES portfolios(id),
            capital_minor INTEGER NOT NULL CHECK (capital_minor >= 0)
        )""")
        raw.execute("""CREATE TABLE positions (
            portfolio_id INTEGER NOT NULL REFERENCES portfolios(id),
            symbol TEXT NOT NULL,
            allocation_minor INTEGER NOT NULL CHECK (allocation_minor >= 0),
            PRIMARY KEY (portfolio_id, symbol)
        )""")
        raw.execute("""CREATE TABLE history (
            id INTEGER PRIMARY KEY,
            portfolio_id INTEGER NOT NULL REFERENCES portfolios(id),
            event TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        raw.execute("""CREATE TABLE paper_start (
            portfolio_id INTEGER PRIMARY KEY REFERENCES portfolios(id), snapshot TEXT NOT NULL
        )""")
        raw.execute("""CREATE TABLE paper_operations (
            id INTEGER PRIMARY KEY, portfolio_id INTEGER NOT NULL REFERENCES portfolios(id), operation TEXT NOT NULL
        )""")
        raw.execute("""CREATE TABLE paper_sales (
            sale_sequence INTEGER PRIMARY KEY,
            portfolio_id INTEGER NOT NULL REFERENCES portfolios(id), operation_id TEXT NOT NULL,
            symbol TEXT NOT NULL, receipt TEXT NOT NULL, UNIQUE (portfolio_id, operation_id)
        )""")
        raw.execute("""CREATE TABLE paper_risk (
            portfolio_id INTEGER PRIMARY KEY REFERENCES portfolios(id), config TEXT NOT NULL
        )""")
        raw.execute("""CREATE TABLE paper_equity (
            id INTEGER PRIMARY KEY, portfolio_id INTEGER NOT NULL REFERENCES portfolios(id),
            observed_at TEXT NOT NULL, day TEXT NOT NULL, equity_uah TEXT NOT NULL,
            equity_currency TEXT NOT NULL CHECK (equity_currency IN ('UAH', 'USDT'))
        )""")
        raw.execute("""CREATE TABLE paper_observations (
            portfolio_id INTEGER NOT NULL REFERENCES portfolios(id), slot TEXT NOT NULL,
            observed_at TEXT NOT NULL, status TEXT NOT NULL, reason TEXT, equity_uah TEXT,
            max_drawdown_percent TEXT, daily_pnl_percent TEXT,
            PRIMARY KEY (portfolio_id, slot)
        )""")
        raw.execute("CREATE INDEX idx_positions_portfolio ON positions(portfolio_id)")
        raw.execute("CREATE INDEX idx_history_portfolio ON history(portfolio_id, id)")
        raw.execute("CREATE INDEX idx_operations_portfolio ON paper_operations(portfolio_id, id)")
        raw.execute("CREATE INDEX idx_sales_portfolio ON paper_sales(portfolio_id, sale_sequence)")
        raw.execute("CREATE INDEX idx_equity_portfolio ON paper_equity(portfolio_id, id)")
        raw.execute("CREATE INDEX idx_observations_portfolio ON paper_observations(portfolio_id, slot)")

        raw.execute("INSERT INTO settings SELECT 1, capital_minor FROM __legacy_settings WHERE id=1")
        raw.execute("INSERT INTO positions SELECT 1, symbol, allocation_minor FROM __legacy_positions")
        raw.execute("INSERT INTO history SELECT id, 1, event, created_at FROM __legacy_history")
        if "paper_start" in renamed:
            raw.execute("INSERT INTO paper_start SELECT 1, snapshot FROM __legacy_paper_start")
        if "paper_operations" in renamed:
            raw.execute("INSERT INTO paper_operations SELECT id, 1, operation FROM __legacy_paper_operations")
        if "paper_sales" in renamed:
            # The original ledger replays append order, even when IDs or times sort differently.
            raw.execute("""INSERT INTO paper_sales (sale_sequence, portfolio_id, operation_id, symbol, receipt)
                           SELECT rowid, 1, operation_id, symbol, receipt
                           FROM __legacy_paper_sales ORDER BY rowid""")
        if "paper_risk" in renamed:
            raw.execute("INSERT INTO paper_risk SELECT 1, config FROM __legacy_paper_risk")
        if "paper_equity" in renamed:
            raw.execute("INSERT INTO paper_equity (id, portfolio_id, observed_at, day, equity_uah, equity_currency) SELECT id, 1, observed_at, day, equity_uah, 'UAH' FROM __legacy_paper_equity")
        if "paper_observations" in renamed:
            raw.execute("""INSERT INTO paper_observations
                SELECT 1, slot, observed_at, status, reason, equity_uah,
                       max_drawdown_percent, daily_pnl_percent FROM __legacy_paper_observations""")
        for table in ("settings", "positions", "history", "paper_start", "paper_operations",
                      "paper_sales", "paper_risk", "paper_equity", "paper_observations"):
            if table in renamed:
                raw.execute(f'DROP TABLE "__legacy_{table}"')
        _check_migration(raw)
        raw.commit()
    except Exception:
        raw.rollback()
        raise
    finally:
        raw.execute("PRAGMA foreign_keys=ON")
        raw.close()
    return {"status": "migrated", "legacy_id": 1}


def _check_migration(db):
    if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise ValueError("SQLite integrity check failed after ledger migration")
    if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise ValueError("Foreign key check failed after ledger migration")

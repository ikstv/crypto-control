"""Create and restore local Crypto Control portfolio snapshots."""

import argparse
import hashlib
import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from ledger import Ledger
from portfolio import existing_portfolio_ids, ledger_schema_migrated, read_portfolio

ROOT = Path(__file__).resolve().parent
DATABASE = ROOT / "data" / "crypto_control.sqlite3"
BACKUPS = ROOT / "backups"


def content_digest(db):
    """Check SQLite and hash the schema and actual stored rows, including journal JSON."""
    if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise ValueError("SQLite integrity check failed")
    if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise ValueError("SQLite foreign key check failed")
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
    if not {"settings", "positions", "history"}.issubset(tables):
        raise ValueError("Expected a Crypto Control portfolio database")
    columns = {row[1] for row in db.execute("PRAGMA table_info(settings)")}
    key = "portfolio_id" if "portfolio_id" in columns else "id"
    if db.execute(f"SELECT capital_minor FROM settings WHERE {key} = 1").fetchone() is None:
        raise ValueError("Missing initial portfolio settings")
    digest = hashlib.sha256()
    for pragma in ("application_id", "user_version"):
        value = db.execute(f"PRAGMA {pragma}").fetchone()[0]
        digest.update(f"{pragma}={value}\n".encode("ascii"))
    for statement in db.iterdump():
        digest.update(statement.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def copy_snapshot(source, destination, expected=None):
    destination.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm", "-journal"):
        if os.path.lexists(str(destination) + suffix):
            raise FileExistsError(f"Restore or backup destination is occupied: {destination}{suffix}")

    source_uri = source.resolve().as_uri() + "?mode=ro"
    with TemporaryDirectory(prefix=".crypto-copy-", dir=destination.parent) as staging:
        staged = Path(staging) / "snapshot.sqlite3"
        with closing(sqlite3.connect(source_uri, uri=True)) as src:
            # The digest and the Backup API see the same committed transaction, including WAL.
            src.execute("BEGIN")
            digest = content_digest(src)
            if expected is not None and digest != expected:
                raise ValueError("Backup contents differ from the saved checksum")
            with closing(sqlite3.connect(staged)) as dest:
                src.backup(dest)
                dest.execute("PRAGMA journal_mode=DELETE")
                if content_digest(dest) != digest:
                    raise ValueError("Restored schema or data differ from the source")
        # Publish a complete file without overwriting a concurrent restore.
        if os.name == "nt":
            staged.rename(destination)
        else:
            os.link(staged, destination)
    return digest


def checksum_file(backup):
    return backup.with_name(backup.name + ".sha256")


def create_backup(source=DATABASE, directory=BACKUPS):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = directory / f"crypto-control-{stamp}.sqlite3"
    digest = copy_snapshot(source, backup)
    try:
        with checksum_file(backup).open("x", encoding="ascii") as checksum:
            checksum.write(digest + "\n")
    except BaseException:
        backup.unlink(missing_ok=True)
        raise
    return backup


def restore_backup(backup, destination):
    expected = checksum_file(backup).read_text(encoding="ascii").strip()
    copy_snapshot(backup, destination, expected)


def portfolio_summary(path, portfolio_id=None):
    plan = read_portfolio(path, portfolio_id)
    state = Ledger(path, portfolio_id=portfolio_id or 1).history()
    currency = plan.get("currency", "UAH").lower()
    return {
        "initial_capital_minor": plan["initial_capital_minor"],
        "operations": len(state["operations"]),
        f"cash_{currency}": state[f"cash_{currency}"],
        "holdings": {row["symbol"]: row["quantity"] for row in state["holdings"]},
        f"realized_pnl_{currency}": state[f"realized_pnl_{currency}"],
    }


def verify_backup(backup):
    with TemporaryDirectory(prefix="crypto-control-restore-") as temp:
        restored = Path(temp) / "restored.sqlite3"
        restore_backup(backup, restored)
        # Older portfolios can be initialized by Ledger.history(); do this only on the test copy.
        migrated = ledger_schema_migrated(restored)
        with closing(sqlite3.connect(restored)) as db:
            events = db.execute("SELECT COUNT(*) FROM history").fetchone()[0]
        result = dict(integrity="ok", history_events=events,
                      **portfolio_summary(restored, 1 if migrated else None))
        if migrated:
            result["portfolios"] = {str(key): portfolio_summary(restored, key)
                                    for key in existing_portfolio_ids(restored)}
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    create = commands.add_parser("create", help="Create a checked SQLite snapshot and checksum")
    create.add_argument("--database", type=Path, default=DATABASE)
    create.add_argument("--directory", type=Path, default=BACKUPS)
    verify = commands.add_parser("verify", help="Restore into a temporary directory and read the ledger")
    verify.add_argument("backup", type=Path)
    restore = commands.add_parser("restore", help="Restore to a new database file")
    restore.add_argument("backup", type=Path)
    restore.add_argument("--to", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.action == "create":
            print(create_backup(args.database, args.directory).resolve())
        elif args.action == "verify":
            print(json.dumps(verify_backup(args.backup), indent=2))
        else:
            restore_backup(args.backup, args.to)
            print(args.to.resolve())
    except (OSError, sqlite3.Error, ValueError, KeyError) as error:
        parser.exit(1, f"Backup operation failed: {error}\n")


if __name__ == "__main__":
    main()

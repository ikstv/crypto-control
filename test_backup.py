import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from backup import create_backup, restore_backup, verify_backup
from ledger import Ledger
from portfolio import create_portfolio, initialize, migrate_ledger_schema, read_portfolio
from test_ledger import feed


class BackupTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root / "data" / "portfolio.sqlite3"
        initialize(self.source)

    def create(self):
        return create_backup(self.source, self.root / "backups")

    def test_process_exit_during_backup_publication(self):
        script = """
import os
import sys
from pathlib import Path
from unittest.mock import patch
from backup import create_backup

source, directory = map(Path, sys.argv[1:3])
phase, timing = sys.argv[3:]
publish = Path.rename if os.name == "nt" else os.link

def interrupted(src, dest, *args, **kwargs):
    dest = Path(dest)
    kind = ("checksum" if dest.suffix == ".sha256" else "database") if dest.parent == directory else "staging"
    if kind == phase and timing == "before":
        os._exit(73)
    result = publish(src, dest, *args, **kwargs)
    if kind == phase and timing == "after":
        os._exit(73)
    return result

with patch.object(Path if os.name == "nt" else os, "rename" if os.name == "nt" else "link", interrupted):
    create_backup(source, directory)
"""
        for phase, timing in (("staging", "before"), ("staging", "after"),
                              ("checksum", "before"), ("checksum", "after"),
                              ("database", "before"), ("database", "after")):
            with self.subTest(phase=phase, timing=timing):
                directory = self.root / f"{phase}-{timing}"
                previous = create_backup(self.source, directory)
                result = subprocess.run(
                    [sys.executable, "-B", "-c", script, str(self.source), str(directory), phase, timing],
                    capture_output=True, timeout=20,
                )
                published = [path for path in directory.glob("crypto-control-*.sqlite3") if path != previous]
                if (phase, timing) == ("database", "after"):
                    self.assertEqual(len(published), 1)
                    self.assertEqual(verify_backup(published[0])["integrity"], "ok")
                else:
                    self.assertEqual(published, [])
                self.assertEqual(result.returncode, 73, result.stderr.decode(errors="replace"))
                self.assertEqual(verify_backup(previous)["integrity"], "ok")
                self.assertEqual(verify_backup(create_backup(self.source, directory))["integrity"], "ok")

    def test_migrated_wal_restores_portfolios_and_sale_receipts(self):
        migrate_ledger_schema(self.source)
        other = create_portfolio(self.source, "USD test", "USDT", "1000", "backup-other", migrate=False)
        with closing(sqlite3.connect(self.source)) as writer:
            writer.execute("PRAGMA journal_mode=WAL")
            with writer:
                writer.execute("INSERT INTO positions VALUES (?, 'BTC', 1000000000)", (other["id"],))
            expected = {}
            receipts = {}
            for key in (1, other["id"]):
                ledger = Ledger(self.source, feed, portfolio_id=key)
                ledger.start()
                receipts[key] = ledger.sell("BTC", "same-id", ledger.preview("BTC")["fingerprint"])
                self.assertEqual(ledger.record_valuation()["status"], "recorded")
                expected[key] = ledger.history()

            writer.execute("UPDATE settings SET capital_minor=0 WHERE portfolio_id=1")
            backup = self.create()
            self.assertTrue(writer.in_transaction)
            self.assertEqual(writer.execute("SELECT capital_minor FROM settings WHERE portfolio_id=1").fetchone(), (0,))
            writer.rollback()
            self.assertNotEqual(self.create(), backup)

        verified = subprocess.run(
            [sys.executable, "-B", str(Path(__file__).with_name("backup.py")), "verify", str(backup)],
            check=True, capture_output=True, text=True, timeout=20,
        )
        summary = json.loads(verified.stdout)
        self.assertEqual(summary["integrity"], "ok")
        self.assertEqual(summary["portfolios"][str(other["id"])]["cash_usdt"],
                         expected[other["id"]]["cash_usdt"])
        restored = self.root / "restored.sqlite3"
        restore_backup(backup, restored)
        with closing(sqlite3.connect(restored)) as db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchall(), [("ok",)])
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
            with closing(sqlite3.connect(self.source)) as source:
                self.assertEqual(list(db.iterdump()), list(source.iterdump()))
        for key, state in expected.items():
            ledger = Ledger(restored, portfolio_id=key)
            self.assertEqual(ledger.history(), state)
            self.assertEqual(ledger.sell("BTC", "same-id", "ignored"), receipts[key])

    def test_open_wal_partial_sale_and_exact_history_survive_restore(self):
        with closing(sqlite3.connect(self.source)) as writer:
            self.assertEqual(writer.execute("PRAGMA journal_mode=WAL").fetchone()[0], "wal")
            writer.execute("BEGIN")
            writer.execute("SELECT capital_minor FROM settings").fetchone()
            ledger = Ledger(self.source, feed)
            ledger.start()
            ledger.sell("BTC", "sample-sale", ledger.preview("BTC")["fingerprint"])
            ledger.configure(paused=True)
            ledger.state()
            expected = ledger.history()
            self.assertEqual(len(expected["operations"]), 2)
            self.assertTrue(Path(str(self.source) + "-wal").stat().st_size > 0)
            backup = self.create()
            ledger.configure(paused=False)

        checked = verify_backup(backup)
        self.assertEqual(checked["operations"], 2)
        self.assertEqual(checked["cash_uah"], expected["cash_uah"])
        self.assertEqual(checked["holdings"], {"BTC": "0.0000503"})
        restored = self.root / "recovered" / "portfolio.sqlite3"
        restore_backup(backup, restored)
        self.assertEqual(Ledger(restored).history(), expected)
        self.assertEqual(read_portfolio(restored), read_portfolio(self.source))

    def test_initial_schema_is_not_migrated_on_backup_or_verify(self):
        original = self.source.read_bytes()
        backup = self.create()
        self.assertEqual(verify_backup(backup)["cash_uah"], "10000")
        self.assertEqual(self.source.read_bytes(), original)
        with closing(sqlite3.connect(backup)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM sqlite_schema WHERE name LIKE 'paper_%'").fetchone()[0], 0)

    def test_valid_sqlite_with_changed_data_fails_verification(self):
        backup = self.create()
        with closing(sqlite3.connect(backup)) as db, db:
            db.execute("UPDATE settings SET capital_minor = capital_minor - 1")
        target = self.root / "restore.sqlite3"
        with self.assertRaises(ValueError):
            restore_backup(backup, target)
        self.assertFalse(target.exists())

    def test_missing_source_and_occupied_restore_paths_are_safe(self):
        missing = self.root / "missing.sqlite3"
        with self.assertRaises((OSError, sqlite3.Error)):
            create_backup(missing, self.root / "backups")
        self.assertFalse(missing.exists())
        backup = self.create()
        original = self.source.read_bytes()
        with self.assertRaises(FileExistsError):
            restore_backup(backup, self.source)
        self.assertEqual(self.source.read_bytes(), original)
        missing.with_suffix(".sqlite3-wal").write_bytes(b"occupied")
        with self.assertRaises(FileExistsError):
            restore_backup(backup, missing)
        self.assertFalse(missing.exists())

    def test_backup_failure_does_not_publish_a_snapshot(self):
        blocked = self.root / "blocked"
        blocked.touch()
        with self.assertRaises(OSError):
            create_backup(self.source, blocked)
        self.assertTrue(blocked.is_file())

        empty = self.root / "empty.sqlite3"
        empty.touch()
        directory = self.root / "empty-backups"
        with self.assertRaisesRegex(ValueError, "Expected a Crypto Control"):
            create_backup(empty, directory)
        self.assertEqual(list(directory.iterdir()), [])

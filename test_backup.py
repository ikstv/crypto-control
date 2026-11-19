import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from backup import create_backup, restore_backup, verify_backup
from ledger import Ledger
from portfolio import initialize, read_portfolio
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

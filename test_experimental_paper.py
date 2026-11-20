import io
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from algorithm import Algorithm
import experimental_paper
from experimental_paper import build_parser, database_path, exclusive, setup, step


class ExperimentalPaperPlatformTests(unittest.TestCase):
    def test_cli_uses_explicit_data_directory_for_persistent_database(self):
        with tempfile.TemporaryDirectory() as directory:
            args = build_parser().parse_args(["run", "--data-dir", directory])
            path = database_path(args.data_dir, args.action)

            self.assertEqual(path, Path(directory) / "experiment.sqlite3")
            self.assertNotIn("research", path.parts)

    def test_lock_prevents_a_second_process_from_acquiring_same_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            lock_path = Path(directory) / "experiment.sqlite3"
            child = """
import sys
from pathlib import Path
from experimental_paper import exclusive
try:
    with exclusive(Path(sys.argv[1])):
        print('ACQUIRED')
except OSError:
    print('LOCKED')
    raise SystemExit(23)
"""
            with exclusive(lock_path):
                result = subprocess.run(
                    [sys.executable, "-c", child, str(lock_path)],
                    cwd=Path(__file__).parent,
                    capture_output=True,
                    text=True,
                    timeout=15,
                )

            self.assertEqual(result.returncode, 23, result.stderr)
            self.assertIn("LOCKED", result.stdout)

    def test_isolated_database_setup_is_repeatable_and_recovers_after_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "experiment.sqlite3"
            setup(path)
            setup(path)

            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
                self.assertEqual(db.execute("SELECT COUNT(*) FROM signals").fetchone()[0], 0)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM paper_operations").fetchone()[0], 0)

            with closing(sqlite3.connect(path)) as reopened:
                self.assertEqual(
                    reopened.execute("SELECT COUNT(*) FROM portfolios").fetchone()[0], 1
                )
                self.assertTrue(
                    any(row[1] == "portfolio_id" for row in reopened.execute(
                        "PRAGMA table_info(settings)"
                    ))
                )

    def test_replayed_signal_after_reopen_does_not_repeat_a_paper_operation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "experiment.sqlite3"
            setup(path)
            Algorithm(path).change("activate")
            with closing(sqlite3.connect(path)) as db, db:
                db.execute("INSERT INTO signals(slot, decision) VALUES(?, ?)", (123, "BUY"))

            # The persisted slot must be recognized before any quote request or ledger write.
            step(path, 123, "BUY", lambda *_: self.fail("duplicate signal fetched market data"))

            with closing(sqlite3.connect(path)) as reopened:
                self.assertEqual(reopened.execute("SELECT COUNT(*) FROM signals").fetchone()[0], 1)
                self.assertEqual(reopened.execute("SELECT COUNT(*) FROM paper_operations").fetchone()[0], 0)
                self.assertEqual(reopened.execute("SELECT COUNT(*) FROM portfolios").fetchone()[0], 1)

    def test_status_on_missing_database_does_not_initialize_or_activate_runner(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "experiment.sqlite3"
            output = io.StringIO()
            with patch.object(sys, "argv", ["experimental_paper.py", "status", "--data-dir", directory]), \
                    patch.object(experimental_paper, "daily_record") as daily_record, \
                    redirect_stdout(output):
                experimental_paper.main()

            self.assertFalse(path.exists())
            self.assertFalse(daily_record.called)
            self.assertEqual(json.loads(output.getvalue()), {
                "mode": "stopped", "status": "not_initialized"
            })

    def test_status_on_initialized_database_does_not_create_control_tables(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "experiment.sqlite3"
            setup(path)
            with closing(sqlite3.connect(path)) as db:
                before = {row[0] for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )}
                self.assertNotIn("algorithm_log", before)

            output = io.StringIO()
            with patch.object(sys, "argv", ["experimental_paper.py", "status", "--data-dir", directory]), \
                    redirect_stdout(output):
                experimental_paper.main()

            with closing(sqlite3.connect(path)) as db:
                after = {row[0] for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )}
            self.assertEqual(after, before)
            self.assertEqual(json.loads(output.getvalue())["mode"], "stopped")


if __name__ == "__main__":
    unittest.main()

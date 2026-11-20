import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from mobile_status import authorized, collect, decision_log, engine_status, whitebit_view


class MobileStatusTests(unittest.TestCase):
    def test_only_configured_password_is_authorized(self):
        self.assertTrue(authorized("Basic YWRtaW46cGFzcw==", "pass"))
        self.assertFalse(authorized("Basic YWRtaW46cGFzcw==", "wrong"))
        self.assertFalse(authorized("Basic !!!", "pass"))

    def test_balance_zero_is_preserved_and_errors_stay_private(self):
        result = whitebit_view(dict(
            status="partial", balances={"DBTC": {"available": "0", "freeze": "0"}},
            errors={"DUSDT": "private response"}, order_history_count=0,
        ))
        self.assertEqual(result["balances"]["DBTC"]["available"], "0")
        self.assertIsNone(result["balances"]["DUSDT"])
        self.assertNotIn("private response", str(result))

    def test_decisions_read_existing_database_without_creating_one(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "experiment.sqlite3"
            self.assertEqual(decision_log(path)["status"], "not_initialized")
            self.assertFalse(path.exists())
            with closing(sqlite3.connect(path)) as db, db:
                db.execute("CREATE TABLE signals(slot INTEGER PRIMARY KEY, decision TEXT)")
                db.executemany("INSERT INTO signals VALUES(?,?)", [(86400, "BUY"), (172800, "HOLD")])
            self.assertEqual([r["decision"] for r in decision_log(path)["entries"]], ["HOLD", "BUY"])

    def test_failed_refresh_retains_last_success_without_inventing_balances(self):
        class Client:
            def demo_status(self):
                return {"status": "error"}

        previous = "2026-11-20T10:00:00+00:00"
        with patch("mobile_status.engine_status"), patch("mobile_status.decision_log"):
            result = collect(Client(), previous)
        self.assertEqual(result["last_success_at"], previous)
        self.assertIsNone(result["whitebit"]["balances"]["DBTC"])

    def test_engine_status_falls_back_to_read_only_state_when_bus_is_unavailable(self):
        denied = subprocess.CalledProcessError(1, "systemctl")
        offline = subprocess.CompletedProcess("systemctl", 1, stdout="disabled\n")
        with patch("mobile_status.subprocess.run", side_effect=[denied, offline]), patch(
            "mobile_status.PAPER_CGROUP_EVENTS"
        ) as events:
            events.read_text.side_effect = FileNotFoundError
            self.assertEqual(engine_status(), {"active": "not_running", "startup": "disabled"})

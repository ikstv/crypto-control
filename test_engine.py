import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parent


@unittest.skipUnless(os.name == "nt", "engine launcher is Windows-specific")
class EngineTests(unittest.TestCase):
    def setUp(self):
        if os.environ.get("CODEX_SANDBOX_NETWORK_DISABLED") == "1":
            self.skipTest("subprocess loopback tests require a normal Windows network sandbox")
        self.temp = tempfile.TemporaryDirectory(prefix="crypto-engine-")
        self.root = Path(self.temp.name)
        for name in ("engine.py", "app.py", "portfolio.py", "ledger.py", "quotes.py",
                     "balance_change.py", "index.html"):
            shutil.copy2(ROOT / name, self.root / name)
        (self.root / "data").mkdir()
        self.port = self.free_port()
        try:
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", self.port))
        except OSError as error:
            self.skipTest(f"loopback sockets unavailable in this environment: {error}")
        self.env = dict(os.environ, CRYPTO_CONTROL_ENGINE_PORT=str(self.port),
                        PYTHONUNBUFFERED="1")
        self.processes = []

    def tearDown(self):
        for process in self.processes:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            if process.stdout:
                process.stdout.close()
        self.temp.cleanup()

    @staticmethod
    def free_port():
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    def start(self):
        process = subprocess.Popen([sys.executable, "engine.py"], cwd=self.root,
                                env=self.env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True)
        self.processes.append(process)
        return process

    def wait_status(self, process):
        deadline = time.monotonic() + 15
        url = f"http://127.0.0.1:{self.port}/api/v1/status"
        while time.monotonic() < deadline:
            try:
                with urlopen(url, timeout=1) as response:
                    return json.load(response)
            except OSError:
                if process.poll() is not None:
                    output = process.stdout.read() if process.stdout else ""
                    if "forbidden" in output.lower() or "permission" in output.lower():
                        self.skipTest(f"subprocess loopback bind unavailable: {output[-300:]}")
                    self.fail(f"engine exited early with {process.returncode}: {output[-300:]}")
            time.sleep(.1)
        self.fail("engine did not become ready")

    def stop(self, process):
        (self.root / "data" / "engine-stopped").touch()
        process.wait(timeout=10)
        self.assertEqual(process.returncode, 0)

    def test_start_stop_restart_and_mutex(self):
        first = self.start()
        self.assertEqual(self.wait_status(first)["status"], "ready")
        second = self.start()
        self.assertNotEqual(second.wait(timeout=5), 0)
        self.stop(first)
        with self.assertRaises(OSError):
            urlopen(f"http://127.0.0.1:{self.port}/api/v1/status", timeout=1)
        (self.root / "data" / "engine-stopped").unlink()
        restarted = self.start()
        self.assertEqual(self.wait_status(restarted)["status"], "ready")
        self.stop(restarted)

    def test_port_conflict_does_not_kill_foreign_process(self):
        with socket.socket() as foreign:
            foreign.bind(("127.0.0.1", self.port))
            foreign.listen()
            process = self.start()
            process.wait(timeout=10)
            self.assertNotEqual(process.returncode, 0)
            self.assertIsNotNone(foreign.fileno())

"""Loopback dashboard for read-only Crypto Control status."""

import base64
import binascii
import hmac
import json
import os
import sqlite3
import subprocess
import threading
import time
from contextlib import closing
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from whitebit_demo_api import WhitebitDemoClient, load_credentials


ASSETS = Path(__file__).resolve().with_name("mobile")
PAPER_DB = Path("/var/lib/crypto-control/experiment.sqlite3")
PAPER_UNIT = "crypto-control-paper.service"
PAPER_CGROUP_EVENTS = Path("/sys/fs/cgroup/system.slice") / PAPER_UNIT / "cgroup.events"
PORT = 18091
REFRESH_SECONDS = 60
ROUTES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/sw.js": ("sw.js", "text/javascript; charset=utf-8"),
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
    "/icon-192.png": ("icon-192.png", "image/png"),
    "/icon-512.png": ("icon-512.png", "image/png"),
}


def utc_time(timestamp=None):
    now = datetime.now(timezone.utc) if timestamp is None else datetime.fromtimestamp(timestamp, timezone.utc)
    return now.isoformat(timespec="seconds")


def authorized(header, password):
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "basic" or len(token) > 1024:
        return False
    try:
        presented = base64.b64decode(token, validate=True)
    except (ValueError, binascii.Error):
        return False
    return hmac.compare_digest(presented, ("admin:" + password).encode())


def decision_log(path):
    try:
        path.stat()
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1)) as db:
            rows = db.execute(
                "SELECT slot, decision FROM signals ORDER BY slot DESC LIMIT 30"
            ).fetchall()
        return {"status": "ok", "entries": [
            {"time": utc_time(slot), "decision": decision} for slot, decision in rows
            if decision in {"BUY", "SELL", "HOLD"}
        ]}
    except FileNotFoundError:
        return {"status": "not_initialized", "entries": []}
    except (sqlite3.Error, OSError, ValueError, OverflowError):
        return {"status": "unavailable", "entries": []}


def engine_status():
    try:
        result = subprocess.run(
            ["systemctl", "show", PAPER_UNIT,
             "-p", "LoadState", "-p", "ActiveState", "-p", "UnitFileState"],
            capture_output=True, text=True, timeout=3, check=True,
        )
        values = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        if values.get("LoadState") == "loaded":
            return {key: values.get(source, "unknown") for key, source in
                    (("active", "ActiveState"), ("startup", "UnitFileState"))}
    except (OSError, subprocess.SubprocessError):
        pass
    # A DynamicUser can be refused by the system bus. Read the unit's
    # on-disk startup state and process presence without bus privileges.
    try:
        result = subprocess.run(
            ["systemctl", "--root=/", "is-enabled", PAPER_UNIT],
            capture_output=True, text=True, timeout=3,
        )
        startup = result.stdout.strip()
        if startup not in {"enabled", "disabled", "static", "masked"}:
            return {"active": "unknown", "startup": "unknown"}
        try:
            events = dict(line.split() for line in PAPER_CGROUP_EVENTS.read_text().splitlines())
        except FileNotFoundError:
            return {"active": "not_running", "startup": startup}
        active = {"0": "not_running", "1": "process_present"}.get(events.get("populated"), "unknown")
        return {"active": active, "startup": startup}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {"active": "unknown", "startup": "unknown"}


def whitebit_view(result):
    balances = {}
    for asset in ("DBTC", "DUSDT"):
        row = (result.get("balances") or {}).get(asset)
        balances[asset] = {key: row[key] for key in ("available", "freeze")} if row else None
    return {
        "status": result["status"],
        "balances": balances,
        "order_history_count": result.get("order_history_count"),
        "executed_deals_count": result.get("executed_deals_count"),
    }


def collect(client, last_success_at=None):
    try:
        exchange = whitebit_view(client.demo_status())
    except Exception:
        exchange = whitebit_view({"status": "error"})
    updated_at = utc_time()
    try:
        shadow = json.loads(Path("/run/crypto-control-shadow/status.json").read_text())
    except (OSError, ValueError):
        shadow = None
    return {
        "updated_at": updated_at,
        "last_success_at": updated_at if exchange["status"] == "ok" else last_success_at,
        "whitebit": exchange, "engine": engine_status(),
        "decisions": decision_log(PAPER_DB),
        "shadow": shadow,
    }


class StatusHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, code, kind, body, *, challenge=False):
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'")
        if challenge:
            self.send_header("WWW-Authenticate", 'Basic realm="Crypto Control"')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not authorized(self.headers.get("Authorization", ""), self.server.panel_password):
            return self.reply(401, "text/plain", b"Authentication required", challenge=True)
        if self.path == "/api/status":
            snapshot = self.server.snapshot
            if snapshot is None:
                return self.reply(503, "application/json", b'{"status":"starting"}')
            return self.reply(200, "application/json", json.dumps(snapshot).encode())
        if self.path in ROUTES:
            file, kind = ROUTES[self.path]
            return self.reply(200, kind, (ASSETS / file).read_bytes())
        self.reply(404, "text/plain", b"Not found")


def main():
    directory = Path(os.environ["CREDENTIALS_DIRECTORY"])
    password = (directory / "mobile_password").read_text(encoding="utf-8").strip()
    if len(password) < 24 or not password.isascii():
        raise SystemExit("Configure an ASCII panel password of at least 24 characters")
    client = WhitebitDemoClient(*load_credentials(), timeout=5)
    with ThreadingHTTPServer(("127.0.0.1", PORT), StatusHandler) as server:
        server.panel_password = password
        server.snapshot = None

        def refresh():
            last_success_at = None
            while True:
                server.snapshot = collect(client, last_success_at)
                last_success_at = server.snapshot["last_success_at"]
                time.sleep(REFRESH_SECONDS)

        threading.Thread(target=refresh, daemon=True).start()
        server.serve_forever()


if __name__ == "__main__":
    main()

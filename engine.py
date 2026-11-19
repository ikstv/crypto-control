"""Single-instance Windows launcher for the local FastAPI engine."""
import ctypes
import hashlib
import logging
import os
import sys
import threading
import time
import socket
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parent
MUTEX_NAME = "Local\\CryptoControl-" + hashlib.sha256(str(ROOT.resolve()).lower().encode()).hexdigest()[:24]
ERROR_ALREADY_EXISTS = 183


def main():
    if os.name != "nt":
        raise SystemExit("Windows is required")
    kernel = ctypes.windll.kernel32
    kernel.CreateMutexW.restype = ctypes.c_void_p
    handle = kernel.CreateMutexW(None, False, MUTEX_NAME)
    if not handle:
        raise SystemExit("Cannot create engine mutex")
    if kernel.GetLastError() == ERROR_ALREADY_EXISTS:
        kernel.CloseHandle(handle)
        raise SystemExit("Crypto Control Engine is already running")
    log_dir = ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    logging.basicConfig(filename=log_dir / "engine.log", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    logging.info("Engine starting")
    try:
        os.chdir(ROOT)
        port = int(os.environ.get("CRYPTO_CONTROL_ENGINE_PORT", "8000"))
        if not 1 <= port <= 65535:
            raise ValueError("CRYPTO_CONTROL_ENGINE_PORT must be between 1 and 65535")
        # Fail fast on a busy/unavailable loopback port instead of leaving the
        # supervisor waiting on uvicorn's startup path.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(("127.0.0.1", port))
        config = uvicorn.Config("app:app", host="127.0.0.1", port=port,
                                workers=1, access_log=False)
        server = uvicorn.Server(config)
        marker = ROOT / "data" / "engine-stopped"
        def watch_stop():
            while not server.should_exit:
                if marker.exists():
                    server.should_exit = True
                    break
                time.sleep(0.5)
        threading.Thread(target=watch_stop, daemon=True).start()
        server.run()
        if not server.started:
            logging.error("Engine failed to bind or initialize")
            return 1
        logging.info("Engine stopped")
        return 0
    except BaseException:
        logging.exception("Engine crashed")
        return 1
    finally:
        kernel.CloseHandle(handle)


if __name__ == "__main__":
    sys.exit(main())

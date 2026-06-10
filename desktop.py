"""Paper Reader desktop shell: embedded server + the best available window.

If Google Chrome is installed, the app opens as a chromeless Chrome app window
(`--app=URL`) — Chrome exposes the Mac's premium/enhanced voices to the Web
Speech API, which Apple's WKWebView deliberately hides from web content.
Without Chrome we fall back to a native WKWebView window.

Lifecycle in Chrome mode: the page sends a heartbeat to /ping every few
seconds; when heartbeats stop (window closed), the server exits.
"""

import socket
import subprocess
import threading
import time
from pathlib import Path

import uvicorn

from backend import main as backend_main
from backend.main import app

CHROME_BIN = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
HEARTBEAT_TIMEOUT = 45  # seconds without a /ping before the server exits


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_server(port: int) -> uvicorn.Server:
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    return server


def run_chrome_app(url: str, server: uvicorn.Server) -> None:
    # Direct binary invocation: if Chrome is already running this hands the
    # window off to the existing instance and the spawned process exits, so
    # the heartbeat — not the subprocess — defines the app's lifetime.
    subprocess.Popen([str(CHROME_BIN), f"--app={url}"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    while time.time() - backend_main.last_ping < HEARTBEAT_TIMEOUT:
        time.sleep(2)
    server.should_exit = True


def run_native_window(url: str, server: uvicorn.Server) -> None:
    import webview

    webview.create_window("Paper Reader", url, width=1100, height=820,
                          min_size=(700, 500))
    webview.start()  # blocks until the window is closed
    server.should_exit = True


def main() -> None:
    port = _free_port()
    server = _start_server(port)
    backend_main.last_ping = time.time()
    url = f"http://127.0.0.1:{port}"
    if CHROME_BIN.exists():
        run_chrome_app(url, server)
    else:
        run_native_window(url, server)


if __name__ == "__main__":
    main()

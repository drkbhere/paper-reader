"""Paper Reader desktop shell: embedded server + native macOS window."""

import socket
import threading
import time

import uvicorn
import webview

from backend.main import app


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> None:
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()

    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)

    webview.create_window(
        "Paper Reader",
        f"http://127.0.0.1:{port}",
        width=1100,
        height=820,
        min_size=(700, 500),
    )
    webview.start()          # blocks until the window is closed
    server.should_exit = True


if __name__ == "__main__":
    main()

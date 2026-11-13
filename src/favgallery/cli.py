"""favgallery CLI: run the local web app and open it in the system browser."""

from __future__ import annotations

import argparse
import socket
import threading
import time
from collections.abc import Sequence

import uvicorn

from favgallery.paths import default_library_root
from favgallery.server import create_app


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="favgallery",
        description="Browse and sync your X (Twitter) liked-media archive.",
    )
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8766, help="Port to listen on (default: 8766).")
    p.add_argument("--library", type=str, default=str(default_library_root()))
    p.add_argument(
        "--no-window",
        action="store_true",
        help="Headless: only run the local server, don't open the browser.",
    )
    p.add_argument(
        "--auto-sync",
        action="store_true",
        help="Run xlikes.exe sync at startup. Off by default — sync only when "
        "the user clicks the sidebar 'sync' button.",
    )
    return p


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_server(host: str, port: int, *, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    from pathlib import Path

    port = args.port
    app = create_app(library_root=Path(args.library))

    if args.auto_sync:
        runner = app.state.sync_runner
        if runner.is_runnable():
            runner.start()

    server = uvicorn.Server(uvicorn.Config(app, host=args.host, port=port, log_level="warning"))

    def serve() -> None:
        server.run()

    server_thread = threading.Thread(target=serve, daemon=True)
    server_thread.start()

    url = f"http://{args.host}:{port}/"

    if args.no_window:
        # Block until interrupted.
        server_thread.join()
        return 0

    if not _wait_for_server(args.host, port, timeout=15):
        print(f"Server did not come up on {url}")
        return 2

    # Default: open in the system browser, keep server running until Ctrl+C.
    import webbrowser

    webbrowser.open(url)
    server_thread.join()
    return 0


if __name__ == "__main__":  # pragma: no cover
    import sys

    sys.exit(main())

"""xlikes-viewer CLI: launch the local web app inside a desktop window."""

from __future__ import annotations

import argparse
import io
import os
import socket
import sys
import threading
import time
from collections.abc import Sequence


def _ensure_stdio() -> None:
    """When PyInstaller is built --windowed, stdout/stderr can be None; uvicorn's
    logger then crashes on the first emit. Substitute /dev/null streams."""
    if sys.stdout is None:
        sys.stdout = io.TextIOWrapper(open(os.devnull, "wb"), encoding="utf-8")  # noqa: SIM115
    if sys.stderr is None:
        sys.stderr = io.TextIOWrapper(open(os.devnull, "wb"), encoding="utf-8")  # noqa: SIM115


_ensure_stdio()

import uvicorn  # noqa: E402

from favgallery.paths import default_library_root  # noqa: E402
from favgallery.server import create_app  # noqa: E402


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="favgallery",
        description="Browse and sync your X (Twitter) liked-media archive.",
    )
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8766, help="Port to listen on (default: 8766).")
    p.add_argument("--library", type=str, default=str(default_library_root()))
    p.add_argument(
        "--window",
        action="store_true",
        help="Open in a desktop window (pywebview) instead of the system browser.",
    )
    p.add_argument(
        "--no-window",
        action="store_true",
        help="Headless: only run the local server, don't open any window.",
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

    if args.window:
        # Desktop window — pywebview wraps Edge WebView2 on Windows.
        import webview

        webview.create_window(
            "FavGallery",
            url,
            width=1400,
            height=900,
            maximized=True,
        )
        webview.start()

        # When the window closes, shut the server down too.
        server.should_exit = True
        server_thread.join(timeout=3)
        return 0

    # Default: open in the system browser, keep server running until Ctrl+C.
    import webbrowser

    webbrowser.open(url)
    server_thread.join()
    return 0


if __name__ == "__main__":  # pragma: no cover
    import sys

    sys.exit(main())

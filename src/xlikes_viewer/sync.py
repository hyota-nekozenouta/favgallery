"""Trigger and observe `xlikes.exe` runs from the viewer process."""

from __future__ import annotations

import os
import subprocess
import threading
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from time import time


def _default_exe() -> Path:
    from xlikes_viewer.paths import default_xlikes_exe

    return default_xlikes_exe()


XLIKES_EXE = _default_exe()
LOG_RING_SIZE = 200


@dataclass
class SyncState:
    """Live state shared between the HTTP layer and the worker thread."""

    running: bool = False
    started_at: float | None = None
    finished_at: float | None = None
    last_return_code: int | None = None
    last_error: str | None = None
    log_lines: deque[str] = field(default_factory=lambda: deque(maxlen=LOG_RING_SIZE))


class SyncRunner:
    """Single-flight runner: at most one xlikes.exe child at a time."""

    def __init__(self, exe_path: Path = XLIKES_EXE) -> None:
        self.exe_path = exe_path
        self.state = SyncState()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._proc: subprocess.Popen[str] | None = None

    def is_runnable(self) -> bool:
        return self.exe_path.exists()

    def start(self, *, extra_args: list[str] | None = None) -> bool:
        """Kick off a sync if none is in flight. Returns False if already running."""
        with self._lock:
            if self.state.running:
                return False
            if not self.is_runnable():
                self.state.last_error = f"xlikes.exe not found at {self.exe_path}"
                return False
            self.state.running = True
            self.state.started_at = time()
            self.state.finished_at = None
            self.state.last_return_code = None
            self.state.last_error = None
            self.state.log_lines.clear()
            self._thread = threading.Thread(
                target=self._worker, args=(extra_args or [],), daemon=True
            )
            self._thread.start()
        return True

    def stop(self) -> bool:
        """Best-effort stop of the running sync."""
        with self._lock:
            proc = self._proc
        if proc and proc.poll() is None:
            proc.terminate()
            return True
        return False

    def _worker(self, extra_args: list[str]) -> None:
        cmd = [str(self.exe_path), *extra_args]
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        try:
            with subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ) as proc:
                with self._lock:
                    self._proc = proc
                assert proc.stdout is not None
                for line in proc.stdout:
                    self.state.log_lines.append(line.rstrip())
                rc = proc.wait()
        except Exception as exc:
            with self._lock:
                self.state.running = False
                self.state.finished_at = time()
                self.state.last_return_code = -1
                self.state.last_error = f"{type(exc).__name__}: {exc}"
                self._proc = None
            return

        with self._lock:
            self.state.running = False
            self.state.finished_at = time()
            self.state.last_return_code = rc
            self._proc = None

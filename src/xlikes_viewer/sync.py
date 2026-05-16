"""Trigger and observe gallery-dl runs from the viewer process."""

from __future__ import annotations

import logging
import threading
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from time import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from xlikes_viewer.db import Database
    from xlikes_viewer.r2 import R2Client

LOG_RING_SIZE = 200
_MY_USERNAME_KEY = "my_username"
_MEDIA_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".mp4", ".mov", ".webm", ".webp"}


@dataclass
class SyncState:
    """Live state shared between the HTTP layer and the worker thread."""

    running: bool = False
    started_at: float | None = None
    finished_at: float | None = None
    last_return_code: int | None = None
    last_error: str | None = None
    log_lines: deque[str] = field(default_factory=lambda: deque(maxlen=LOG_RING_SIZE))


class _DequeHandler(logging.Handler):
    """Logging handler that appends formatted records to a deque."""

    def __init__(self, target: deque[str]) -> None:
        super().__init__()
        self._target = target

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._target.append(self.format(record))
        except Exception:
            self.handleError(record)


class _NullContext:
    """No-op context manager used when no external lock is provided."""

    def __enter__(self) -> _NullContext:
        return self

    def __exit__(self, *_: Any) -> None:
        pass


class SyncRunner:
    """Single-flight runner: at most one gallery-dl sync at a time."""

    def __init__(
        self,
        config_path: Path,
        db: Database,
        *,
        gdl_lock: threading.Lock | None = None,
        library_root: Path | None = None,
        r2_client: R2Client | None = None,
        on_complete: Any | None = None,
    ) -> None:
        self.config_path = config_path
        self._db = db
        # Optional external lock to serialize gallery-dl's global config writes
        # with other gallery-dl callers in the same process.
        self._gdl_lock = gdl_lock
        self._library_root = library_root
        self._r2_client = r2_client
        self._on_complete = on_complete  # callable() invoked after successful sync
        self.state = SyncState()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def is_runnable(self) -> bool:
        """gallery-dl is always available (installed via pip on Railway)."""
        return True

    def start(self, *, extra_args: list[str] | None = None) -> bool:
        """Kick off a sync if none is in flight. Returns False if already running."""
        with self._lock:
            if self.state.running:
                return False
            if not self.is_runnable():
                return False
            self.state.running = True
            self.state.started_at = time()
            self.state.finished_at = None
            self.state.last_return_code = None
            self.state.last_error = None
            self.state.log_lines.clear()
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._worker, daemon=True)
            self._thread.start()
        return True

    def stop(self) -> bool:
        """Signal the running sync to stop at the next opportunity."""
        with self._lock:
            running = self.state.running
        if running:
            self._stop_event.set()
            return True
        return False

    def _upload_library_to_r2(self) -> None:
        """Upload any media file in library_root that is not yet in R2.

        Uses the relative path (forward-slashed, relative to library_root) as
        the R2 object key so that ``/api/media/{rel_path}`` can retrieve it.
        Skips ``.json`` sidecars and ``thumbs/`` subdirectories.
        After upload, deletes successfully uploaded AND already-in-R2 files to
        free Railway volume space.
        """
        assert self._r2_client is not None  # noqa: S101 — caller guarantee
        assert self._library_root is not None  # noqa: S101 — caller guarantee
        library = self._library_root

        # Fetch the full R2 key set once (paginated list) instead of per-file HEAD
        # requests — orders of magnitude faster for large libraries.
        self.state.log_lines.append("[r2] fetching key list from R2...")
        r2_keys = self._r2_client.list_all_keys()
        self.state.log_lines.append(f"[r2] {len(r2_keys)} keys in bucket")

        uploaded_paths: list[Path] = []
        already_in_r2: list[Path] = []
        for media_path in library.rglob("*"):
            if not media_path.is_file():
                continue
            rel = media_path.relative_to(library)
            if media_path.suffix.lower() not in _MEDIA_EXTENSIONS:
                continue
            # Skip thumbnails stored under any thumbs/ subdirectory.
            if "thumbs" in rel.parts[:-1]:
                continue
            key = rel.as_posix()
            if key in r2_keys:
                # Already in R2 — mark for local deletion to free Railway volume space.
                already_in_r2.append(media_path)
            else:
                try:
                    self._r2_client.upload_file(media_path, key)
                    uploaded_paths.append(media_path)
                except Exception as exc:
                    self.state.log_lines.append(f"[r2] upload failed for {key}: {exc}")

        uploaded = len(uploaded_paths)
        skipped = len(already_in_r2)
        self.state.log_lines.append(
            f"[r2] upload complete: {uploaded} uploaded, {skipped} already present"
        )
        # Delete successfully uploaded files AND files already in R2 to reclaim Railway volume.
        # Files that failed to upload are intentionally kept.
        deleted = 0
        for path in uploaded_paths + already_in_r2:
            try:
                path.unlink()
                deleted += 1
            except OSError as exc:
                self.state.log_lines.append(
                    f"[r2] local delete failed for {path.name}: {exc}"
                )
        if deleted:
            self.state.log_lines.append(f"[r2] local cleanup: {deleted} files deleted")

    def cleanup_local(self) -> dict[str, int]:
        """Delete local media files that are already present in R2.

        Called by ``/api/admin/cleanup-local`` to reclaim disk space on demand.
        Safe to run at any time; only removes files confirmed to exist in R2.
        Uses a single paginated list_objects_v2 call instead of per-file HEAD
        requests for efficiency.
        Returns counts: ``{"deleted": n, "checked": n, "errors": n}``.
        """
        if self._r2_client is None or self._library_root is None:
            return {"deleted": 0, "checked": 0, "errors": 0}
        library = self._library_root
        r2_keys = self._r2_client.list_all_keys()
        deleted = 0
        checked = 0
        errors = 0
        for media_path in library.rglob("*"):
            if not media_path.is_file():
                continue
            rel = media_path.relative_to(library)
            if media_path.suffix.lower() not in _MEDIA_EXTENSIONS:
                continue
            if "thumbs" in rel.parts[:-1]:
                continue
            key = rel.as_posix()
            checked += 1
            if key not in r2_keys:
                continue
            try:
                media_path.unlink()
                deleted += 1
            except OSError:
                errors += 1
        return {"deleted": deleted, "checked": checked, "errors": errors}

    def _worker(self) -> None:
        from gallery_dl import job as gdl_job  # type: ignore[import-untyped]

        from xlikes_viewer.gallerydl import prepare_config

        gdl_logger = logging.getLogger("gallery_dl")
        handler = _DequeHandler(self.state.log_lines)
        handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        gdl_logger.addHandler(handler)

        rc = 0
        err: str | None = None
        try:
            username = (self._db.get_setting(_MY_USERNAME_KEY) or "").strip()
            if not username:
                raise RuntimeError(
                    "X username not set — configure it via POST /api/me"
                )
            if self._stop_event.is_set():
                rc = 130  # interrupted before start
                return

            url = f"https://x.com/{username}/likes"
            self.state.log_lines.append(f"[sync] starting: {url}")

            ctx: Any = self._gdl_lock if self._gdl_lock is not None else _NullContext()
            with ctx:
                prepare_config(self.config_path, archive=..., twitter_retweets=False)
                gdl_job.DownloadJob(url).run()

            self.state.log_lines.append("[sync] complete")
            if self._r2_client is not None and self._library_root is not None:
                self._upload_library_to_r2()
            if self._on_complete is not None:
                try:
                    self._on_complete()
                except Exception:
                    logging.getLogger(__name__).warning("on_complete callback failed", exc_info=True)
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
            rc = -1
        finally:
            gdl_logger.removeHandler(handler)
            with self._lock:
                self.state.running = False
                self.state.finished_at = time()
                self.state.last_return_code = rc
                self.state.last_error = err

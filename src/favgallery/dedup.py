"""Detect and delete duplicate media in the local archive.

Two strategies are supported:

SHA-256 (byte-identical)
    Fast, exact. Catches files that are bitwise identical regardless of filename.

pHash (perceptual hash)
    Catches visually-identical images even when the source URL / encoding
    differs (e.g. the same art tweeted by two different accounts at slightly
    different JPEG quality). Uses the ``imagehash`` library (DCT-based 64-bit
    hash). Two images are considered duplicates when their Hamming distance is
    ≤ ``PHASH_THRESHOLD``. Video files are skipped (images only).
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from xlikes_viewer.db import Database

log = logging.getLogger("xlikes_viewer.dedup")

MEDIA_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".mp4", ".mov", ".webm"}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
HASH_CHUNK = 64 * 1024
PHASH_THRESHOLD = 5  # Hamming distance; ≤5 ≈ visually identical


@dataclass
class DedupState:
    running: bool = False
    started_at: float | None = None
    finished_at: float | None = None
    files_total: int = 0
    files_hashed: int = 0
    duplicates_deleted: int = 0
    bytes_freed: int = 0
    last_error: str | None = None


@dataclass
class VisualDedupState:
    running: bool = False
    started_at: float | None = None
    finished_at: float | None = None
    files_total: int = 0
    files_indexed: int = 0
    duplicates_deleted: int = 0
    bytes_freed: int = 0
    last_error: str | None = None


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(HASH_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _iter_media_files(library_root: Path) -> Iterable[Path]:
    """Yield media files under the library, skipping the thumb cache."""
    for p in library_root.rglob("*"):
        if not p.is_file():
            continue
        if ".thumb-cache" in p.parts:
            continue
        if p.suffix.lower() in MEDIA_SUFFIXES:
            yield p


def index_hashes(
    library_root: Path,
    db: Database,
    *,
    on_progress: Callable[[int, int], None] | None = None,
) -> tuple[int, int]:
    """Compute hashes for files not yet in `media_hashes` (or whose stat changed).

    Returns ``(total_seen, newly_hashed)``.
    """
    files = list(_iter_media_files(library_root))
    total = len(files)
    hashed = 0
    library_root_resolved = library_root.resolve()
    for i, path in enumerate(files):
        rel = path.resolve().relative_to(library_root_resolved).as_posix()
        try:
            stat = path.stat()
        except OSError:
            continue
        existing = db.known_hash(rel)
        if existing and existing[1] == stat.st_size and existing[2] == int(stat.st_mtime_ns):
            continue
        try:
            sha = _file_sha256(path)
        except OSError as exc:
            log.warning("hash failed for %s: %s", rel, exc)
            continue
        db.upsert_hash(
            rel_path=rel,
            sha256=sha,
            size=stat.st_size,
            mtime=int(stat.st_mtime_ns),
            indexed_at=int(time.time()),
        )
        hashed += 1
        if on_progress is not None and (i % 50 == 0 or i == total - 1):
            on_progress(i + 1, total)
    if on_progress is not None:
        on_progress(total, total)
    return total, hashed


def delete_duplicates(library_root: Path, db: Database) -> tuple[int, int]:
    """For each duplicate group, keep the earliest entry and delete the rest.

    Returns ``(files_deleted, bytes_freed)``. Sidecar ``.json`` files are
    deleted alongside their media. Hash rows for deleted files are removed
    so subsequent scans don't trip over stale state.
    """
    groups = db.duplicate_groups()
    deleted = 0
    freed = 0
    now = int(time.time())
    for group in groups:
        if len(group) < 2:
            continue
        keeper_rel, _ = group[0]
        keeper_hash = db.known_hash(keeper_rel)
        sha = keeper_hash[0] if keeper_hash else ""
        for rel, _idx_at in group[1:]:
            target = library_root / rel
            try:
                size = target.stat().st_size
            except OSError:
                size = 0
            sidecar = target.with_suffix(target.suffix + ".json")
            try:
                target.unlink()
            except OSError as exc:
                log.warning("could not unlink %s: %s", target, exc)
                continue
            with contextlib.suppress(OSError):
                sidecar.unlink()
            db.forget_hash(rel)
            db.log_dedup(deleted_path=rel, kept_path=keeper_rel, sha256=sha, when=now)
            deleted += 1
            freed += size
    return deleted, freed


def _compute_phash(path: Path) -> str | None:
    try:
        import imagehash
        from PIL import Image

        with Image.open(path) as img:
            return str(imagehash.phash(img))
    except Exception:
        return None


def _hamming(h1: str, h2: str) -> int:
    return bin(int(h1, 16) ^ int(h2, 16)).count("1")


def index_phashes(
    library_root: Path,
    db: Database,
    *,
    on_progress: Callable[[int, int], None] | None = None,
) -> int:
    """Compute pHash for image files not yet indexed. Returns total image files seen."""
    files = [
        p
        for p in _iter_media_files(library_root)
        if p.suffix.lower() in IMAGE_SUFFIXES
    ]
    total = len(files)
    library_root_resolved = library_root.resolve()
    already_indexed = {r[0] for r in db.all_phashes()}
    for i, path in enumerate(files):
        rel = path.resolve().relative_to(library_root_resolved).as_posix()
        if rel in already_indexed:
            if on_progress and i % 50 == 0:
                on_progress(i + 1, total)
            continue
        if db.known_hash(rel) is None:
            # SHA-256 not yet computed; skip until SHA-256 indexing runs first.
            if on_progress and i % 50 == 0:
                on_progress(i + 1, total)
            continue
        h = _compute_phash(path)
        if h is not None:
            db.upsert_phash(rel_path=rel, phash=h)
            already_indexed.add(rel)
        if on_progress and (i % 50 == 0 or i == total - 1):
            on_progress(i + 1, total)
    if on_progress:
        on_progress(total, total)
    return total


def delete_visual_duplicates(library_root: Path, db: Database) -> tuple[int, int]:
    """Find visually-similar images (Hamming ≤ PHASH_THRESHOLD) and delete duplicates.

    Within each duplicate group the earliest-indexed file is kept.
    Returns (files_deleted, bytes_freed).
    """
    rows = db.all_phashes()  # [(rel_path, phash, indexed_at), ...]
    if len(rows) < 2:
        return 0, 0

    # Union-Find for grouping
    parent = list(range(len(rows)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        parent[find(x)] = find(y)

    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            if _hamming(rows[i][1], rows[j][1]) <= PHASH_THRESHOLD:
                union(i, j)

    # Build groups: {root_idx -> [(rel_path, indexed_at), ...]}
    groups: dict[int, list[tuple[str, int]]] = {}
    for i, (rel, _, idx_at) in enumerate(rows):
        root = find(i)
        groups.setdefault(root, []).append((rel, idx_at))

    deleted = 0
    freed = 0
    now = int(time.time())
    library_root_resolved = library_root.resolve()

    for group in groups.values():
        if len(group) < 2:
            continue
        group.sort(key=lambda x: x[1])  # earliest indexed = keeper
        keeper_rel = group[0][0]
        for rel, _ in group[1:]:
            target = library_root_resolved / rel
            try:
                size = target.stat().st_size
            except OSError:
                size = 0
            sidecar = target.with_suffix(target.suffix + ".json")
            try:
                target.unlink()
            except OSError as exc:
                log.warning("could not unlink %s: %s", target, exc)
                continue
            with contextlib.suppress(OSError):
                sidecar.unlink()
            db.forget_hash(rel)
            db.log_dedup(deleted_path=rel, kept_path=keeper_rel, sha256="phash", when=now)
            deleted += 1
            freed += size

    return deleted, freed


class DedupRunner:
    """Single-flight orchestration: hash + delete in a background thread."""

    def __init__(self, db: Database, library_root: Path) -> None:
        self.db = db
        self.library_root = library_root
        self.state = DedupState()
        self._lock = threading.Lock()

    def is_running(self) -> bool:
        with self._lock:
            return self.state.running

    def start(self) -> bool:
        with self._lock:
            if self.state.running:
                return False
            self.state = DedupState(
                running=True, started_at=time.time(), files_total=0, files_hashed=0
            )
        threading.Thread(target=self._worker, daemon=True).start()
        return True

    def _on_progress(self, done: int, total: int) -> None:
        with self._lock:
            self.state.files_hashed = done
            self.state.files_total = total

    def _worker(self) -> None:
        try:
            total, _ = index_hashes(self.library_root, self.db, on_progress=self._on_progress)
            deleted, freed = delete_duplicates(self.library_root, self.db)
            with self._lock:
                self.state.duplicates_deleted = deleted
                self.state.bytes_freed = freed
                self.state.files_total = total
        except Exception as exc:
            log.exception("dedup failed")
            with self._lock:
                self.state.last_error = f"{type(exc).__name__}: {exc}"
        finally:
            with self._lock:
                self.state.running = False
                self.state.finished_at = time.time()


class VisualDedupRunner:
    """Single-flight orchestration: pHash index + visual-duplicate delete."""

    def __init__(self, db: Database, library_root: Path) -> None:
        self.db = db
        self.library_root = library_root
        self.state = VisualDedupState()
        self._lock = threading.Lock()

    def is_running(self) -> bool:
        with self._lock:
            return self.state.running

    def start(self) -> bool:
        with self._lock:
            if self.state.running:
                return False
            self.state = VisualDedupState(running=True, started_at=time.time())
        threading.Thread(target=self._worker, daemon=True).start()
        return True

    def _on_progress(self, done: int, total: int) -> None:
        with self._lock:
            self.state.files_indexed = done
            self.state.files_total = total

    def _worker(self) -> None:
        try:
            total = index_phashes(self.library_root, self.db, on_progress=self._on_progress)
            deleted, freed = delete_visual_duplicates(self.library_root, self.db)
            with self._lock:
                self.state.duplicates_deleted = deleted
                self.state.bytes_freed = freed
                self.state.files_total = total
        except Exception as exc:
            log.exception("visual dedup failed")
            with self._lock:
                self.state.last_error = f"{type(exc).__name__}: {exc}"
        finally:
            with self._lock:
                self.state.running = False
                self.state.finished_at = time.time()

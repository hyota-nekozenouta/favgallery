"""Detect duplicate *books* and skip them at import time.

Mirrors the perceptual-hash approach used for likes media (``dedup.py``) but at
book granularity. A book's fingerprint is:

- ``cover_phash``  — pHash of page 1 (the cover)
- ``sample_phashes`` — pHashes of a few sampled interior pages (25/50/75% + last)

Two books are considered the same when their ``page_count`` is equal AND the
cover matches within ``BOOK_PHASH_THRESHOLD`` AND every comparable sampled page
matches. Matching is intentionally conservative: the worst outcome is a
*false-positive skip* (silently dropping a genuinely new book), so we require
exact page_count, a stricter Hamming threshold than likes dedup, and agreement
across multiple sampled pages before declaring a duplicate.
"""

from __future__ import annotations

import contextlib
import logging
import os
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from favgallery.db import Database
from favgallery.dedup import _compute_phash, _hamming

if TYPE_CHECKING:
    from favgallery.r2 import R2Client

log = logging.getLogger("favgallery.book_dedup")

# Stricter than likes dedup's PHASH_THRESHOLD (5): a wrong book skip is silent
# data loss, so we bias toward not-a-duplicate.
BOOK_PHASH_THRESHOLD = 4


def sample_page_nums(page_count: int) -> list[int]:
    """1-based interior page numbers to fingerprint (excludes the cover, page 1).

    Deterministic so the same book always samples the same pages — both the
    new import and the already-stored book pick identical pages to compare.
    """
    if page_count <= 1:
        return []
    nums: set[int] = set()
    for frac in (0.25, 0.5, 0.75, 1.0):
        pn = max(1, min(page_count, round(frac * page_count)))
        if pn >= 2:
            nums.add(pn)
    nums.add(page_count)  # always include the last page
    return sorted(nums)


def fingerprint_from_paths(
    cover_path: Path | None,
    sampled: list[tuple[int, Path]],
) -> tuple[str | None, list[tuple[int, str]]]:
    """Compute (cover_phash, [(page_num, phash), ...]) from local image paths."""
    cover_phash = _compute_phash(cover_path) if cover_path is not None else None
    samples: list[tuple[int, str]] = []
    for page_num, path in sampled:
        h = _compute_phash(path)
        if h is not None:
            samples.append((page_num, h))
    return cover_phash, samples


def fingerprint_for_ordered_files(
    files: list[Path],
) -> tuple[int, str | None, list[tuple[int, str]]]:
    """(page_count, cover_phash, sample_phashes) from an ordered page-file list (1-based)."""
    page_count = len(files)
    cover = files[0] if files else None
    sampled = [(pn, files[pn - 1]) for pn in sample_page_nums(page_count) if 1 <= pn <= page_count]
    cover_phash, samples = fingerprint_from_paths(cover, sampled)
    return page_count, cover_phash, samples


def fingerprint_matches(
    new_pc: int,
    new_cover: str | None,
    new_samples: list[tuple[int, str]],
    cand_pc: int,
    cand_cover: str | None,
    cand_samples: list[tuple[int, str]],
    *,
    threshold: int = BOOK_PHASH_THRESHOLD,
) -> bool:
    """Conservative AND-match. Any clear mismatch => not a duplicate."""
    if new_pc != cand_pc:
        return False
    if not new_cover or not cand_cover:
        return False  # can't verify the cover -> refuse to call it a duplicate
    if _hamming(new_cover, cand_cover) > threshold:
        return False
    cand_map = dict(cand_samples)
    matched = 1  # the cover
    for page_num, phash in new_samples:
        other = cand_map.get(page_num)
        if other is None:
            continue  # one side failed to hash this page -> not comparable
        if _hamming(phash, other) > threshold:
            return False  # a sampled page clearly differs -> different book
        matched += 1
    # Require enough agreeing evidence (cover + samples). Adaptive so small
    # books (few interior pages) can still match on everything they have.
    return matched >= min(3, new_pc)


def find_duplicate_book(
    db: Database,
    page_count: int,
    cover_phash: str | None,
    samples: list[tuple[int, str]],
) -> tuple[int, str] | None:
    """Return (book_id, title) of an existing duplicate, or None."""
    if not cover_phash:
        return None
    for book_id, title, cand_cover, cand_samples in db.candidate_books_by_page_count(page_count):
        if fingerprint_matches(
            page_count, cover_phash, samples, page_count, cand_cover, cand_samples
        ):
            return (book_id, title)
    return None


@dataclass
class BookIndexState:
    running: bool = False
    started_at: float | None = None
    finished_at: float | None = None
    books_total: int = 0
    books_indexed: int = 0
    last_error: str | None = None


class BookIndexRunner:
    """Single-flight background backfill of fingerprints for existing books.

    Idempotent: only processes books that have no fingerprint yet. Each book is
    isolated in try/except so one failure never aborts the whole run. Runs off
    the request path so R2 latency never blocks startup or imports.
    """

    def __init__(self, db: Database, library_root: Path, r2_client: R2Client | None) -> None:
        self.db = db
        self.library_root = library_root
        self.r2_client = r2_client
        self.state = BookIndexState()
        self._lock = threading.Lock()

    def is_running(self) -> bool:
        with self._lock:
            return self.state.running

    def start(self) -> bool:
        with self._lock:
            if self.state.running:
                return False
            self.state = BookIndexState(running=True, started_at=time.time())
        threading.Thread(target=self._worker, daemon=True).start()
        return True

    def _phash_for_rel(self, rel_path: str) -> str | None:
        """pHash for a book page, reading from local disk if present else R2."""
        local = self.library_root / rel_path
        if local.is_file():
            return _compute_phash(local)
        if self.r2_client is not None:
            try:
                _, _, body_iter = self.r2_client.stream_object(rel_path)
                raw = b"".join(body_iter)
            except Exception:
                return None
            fd, tmp = tempfile.mkstemp(suffix=Path(rel_path).suffix or ".jpg")
            try:
                with os.fdopen(fd, "wb") as fh:
                    fh.write(raw)
                return _compute_phash(Path(tmp))
            finally:
                with contextlib.suppress(Exception):
                    os.unlink(tmp)
        return None

    def index_book(self, book_id: int) -> None:
        """Compute and store one book's fingerprint (used by backfill + on-demand)."""
        pages = self.db.book_pages(book_id)
        page_count = len(pages)
        by_num = {p.page_num: p.rel_path for p in pages}
        cover_rel = by_num.get(1) or (pages[0].rel_path if pages else None)
        cover_phash = self._phash_for_rel(cover_rel) if cover_rel else None
        samples: list[tuple[int, str]] = []
        for page_num in sample_page_nums(page_count):
            rel = by_num.get(page_num)
            if not rel:
                continue
            h = self._phash_for_rel(rel)
            if h is not None:
                samples.append((page_num, h))
        self.db.upsert_book_hash(
            book_id=book_id,
            page_count=page_count,
            cover_phash=cover_phash,
            sample_phashes=samples,
            indexed_at=int(time.time()),
        )

    def _worker(self) -> None:
        try:
            ids = self.db.book_ids_without_hash()
            with self._lock:
                self.state.books_total = len(ids)
            for book_id in ids:
                try:
                    self.index_book(book_id)
                except Exception:
                    log.exception("book index failed for book %s", book_id)
                finally:
                    with self._lock:
                        self.state.books_indexed += 1
        except Exception as exc:
            log.exception("book index run failed")
            with self._lock:
                self.state.last_error = f"{type(exc).__name__}: {exc}"
        finally:
            with self._lock:
                self.state.running = False
                self.state.finished_at = time.time()

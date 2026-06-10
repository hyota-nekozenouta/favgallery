"""Book import queue: URL -> gallery-dl (or HTML scrape fallback) -> bookshelf.

Single-flight queue that processes one URL at a time in a background thread,
self-chaining to the next pending item. Extracted from server.create_app so the
worker can be unit-tested with stubbed subprocess / scraper.
"""

from __future__ import annotations

import contextlib
import json
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from xlikes_viewer.book_dedup import find_duplicate_book, fingerprint_for_ordered_files
from xlikes_viewer.gallerydl_config import build_book_import_config
from xlikes_viewer.scrapers import scrape_images_from_html

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".bmp"}


def _natural_key(name: str) -> list:
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r"(\d+)", name)]


class BookImportQueue:
    """Manages the book-import work queue + worker. Deps injected for testing."""

    def __init__(self, *, db: Any, library_root: Path, r2_client: Any, books_dir: str) -> None:
        self._db = db
        self._library_root = library_root
        self._r2_client = r2_client
        self._books_dir = books_dir
        self._queue: list[dict] = []
        self._lock = threading.Lock()
        self._next_id = 0

    def snapshot(self) -> list[dict]:
        """Return a shallow copy of the queue (for /api/books/import/status)."""
        with self._lock:
            return list(self._queue)

    def enqueue(self, url: str) -> dict:
        """Add a URL to the queue and kick processing if idle. Returns the item."""
        with self._lock:
            self._next_id += 1
            item = {
                "id": self._next_id,
                "url": url,
                "status": "pending",
                "error": None,
                "book_id": None,
                "title": "",
                "progress": "待機中...",
                "matched_book_id": None,
                "matched_title": "",
            }
            self._queue.append(item)
            running = any(i["status"] == "running" for i in self._queue)
        if not running:
            self._process_next()
        return item

    def _process_next(self) -> None:
        """Start the next pending item in the queue, if any."""
        with self._lock:
            pending = [item for item in self._queue if item["status"] == "pending"]
            if not pending:
                return
            item = pending[0]
            item["status"] = "running"
            item["progress"] = "開始中..."
        threading.Thread(target=self._worker, args=(item,), daemon=True).start()

    def _worker(self, item: dict) -> None:
        url = item["url"]
        tmp_dir = None
        try:
            tmp_dir = tempfile.mkdtemp(prefix="book_import_")
            tmp_path = Path(tmp_dir)

            # Try gallery-dl first. Its success/failure isn't inspected directly
            # — we decide purely on whether image files landed in tmp_dir, and
            # fall back to HTML scraping when none did.
            cfg = build_book_import_config(tmp_dir)
            cfg_path = Path(tmp_dir) / "gdl_config.json"
            cfg_path.write_text(json.dumps(cfg), encoding="utf-8")

            cmd = ["gallery-dl", "--config", str(cfg_path), url]
            with self._lock:
                item["progress"] = "ダウンロード中..."

            with contextlib.suppress(subprocess.TimeoutExpired, FileNotFoundError):
                subprocess.run(cmd, capture_output=True, text=True, timeout=300)

            all_files: list[Path] = [
                f
                for f in sorted(tmp_path.rglob("*"))
                if f.is_file() and f.suffix.lower() in _IMAGE_EXTS
            ]

            # Fallback: HTML scraping when gallery-dl produced no images.
            if not all_files:
                with self._lock:
                    item["progress"] = "HTML から画像を取得中..."
                with contextlib.suppress(Exception):
                    all_files = scrape_images_from_html(url, tmp_path)

            if not all_files:
                with self._lock:
                    item["error"] = "画像が見つかりませんでした"
                    item["status"] = "error"
                return

            # Extract a title from gallery-dl metadata, else derive from the URL.
            title = "Imported"
            for f in tmp_path.rglob("*.json"):
                try:
                    meta = json.loads(f.read_text(encoding="utf-8"))
                    if isinstance(meta, dict):
                        title = (
                            meta.get("gallery", {}).get("title", "")
                            or meta.get("title", "")
                            or title
                        )
                        if title != "Imported":
                            break
                except Exception:
                    pass

            if title == "Imported":
                from urllib.parse import unquote

                parts = unquote(url).rsplit("/", 1)[-1].rsplit(".", 1)[0]
                title = re.sub(r"-\d+$", "", parts).replace("-", " ").strip() or "Imported"

            with self._lock:
                item["title"] = title
                item["progress"] = "本棚に登録中..."

            all_files.sort(key=lambda f: _natural_key(f.name))

            # Skip if this import duplicates a book already on the shelf. The tmp
            # files still exist here (before create + R2 upload), so we can
            # fingerprint and bail out cleanly without touching the library.
            _pc, _cover_phash, _samples = fingerprint_for_ordered_files(all_files)
            _dup = find_duplicate_book(self._db, _pc, _cover_phash, _samples)
            if _dup is not None:
                with self._lock:
                    item["status"] = "skipped"
                    item["matched_book_id"] = _dup[0]
                    item["matched_title"] = _dup[1]
                    item["progress"] = "重複のためスキップ"
                return  # finally clause cleans tmp_dir + advances the queue

            book = self._db.create_book(title=title, cover_path=None, page_count=len(all_files))
            book_dir = self._library_root / self._books_dir / str(book.id)
            book_dir.mkdir(parents=True, exist_ok=True)

            pages: list[tuple[int, str, int | None, int | None]] = []
            cover_rel: str | None = None

            for i, src_file in enumerate(all_files, start=1):
                ext = src_file.suffix.lower() or ".jpg"
                filename = f"{i:04d}{ext}"
                dest = book_dir / filename
                shutil.copy2(src_file, dest)

                rel = f"{self._books_dir}/{book.id}/{filename}"
                if i == 1:
                    cover_rel = rel

                w, h = None, None
                try:
                    from PIL import Image

                    img = Image.open(dest)
                    w, h = img.size
                    img.close()
                except Exception:
                    pass

                pages.append((i, rel, w, h))

            self._db.add_book_pages(book.id, pages)

            if cover_rel:
                with self._db._lock:
                    self._db._conn.execute(
                        "UPDATE books SET cover_path = ? WHERE id = ?", (cover_rel, book.id)
                    )

            # Persist the fingerprint so future imports can detect duplicates.
            self._db.upsert_book_hash(
                book_id=book.id,
                page_count=_pc,
                cover_phash=_cover_phash,
                sample_phashes=_samples,
                indexed_at=int(time.time()),
            )

            # Upload to R2 if configured.
            if self._r2_client is not None:
                for _page_num, rel, _w, _h in pages:
                    local_path = self._library_root / rel
                    if local_path.is_file():
                        try:
                            self._r2_client.upload_file(local_path, rel)
                            local_path.unlink()
                        except Exception:
                            pass
                if book_dir.exists() and not any(book_dir.iterdir()):
                    book_dir.rmdir()

            with self._lock:
                item["book_id"] = book.id
                item["status"] = "done"
                item["progress"] = "完了"

        except Exception as exc:
            with self._lock:
                item["error"] = f"{type(exc).__name__}: {exc}"
                item["status"] = "error"
        finally:
            if tmp_dir:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            self._process_next()

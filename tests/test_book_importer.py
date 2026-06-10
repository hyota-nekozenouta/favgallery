"""Tests for favgallery.book_importer.BookImportQueue.

Exercises the queue worker with gallery-dl stubbed out (so the HTML-scrape
fallback path runs) and the scraper stubbed to produce real images. Locks in
the queue drain + dedup-skip behaviour after extraction from server.create_app.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from PIL import Image

from favgallery.book_importer import BookImportQueue
from favgallery.db import Database

_TERMINAL = {"done", "error", "skipped"}


def _no_gallery_dl(*_a: object, **_kw: object) -> None:
    raise FileNotFoundError("gallery-dl not available in tests")


def _make_scraper(num_pages: int):
    def _scrape(url: str, tmp_dir: Path) -> list[Path]:
        files = []
        for i in range(1, num_pages + 1):
            p = Path(tmp_dir) / f"{i:04d}.png"
            Image.new("RGB", (64, 64), color=(i * 30 % 255, 80, 120)).save(p)
            files.append(p)
        return files
    return _scrape


def _wait_terminal(queue: BookImportQueue, item_id: int, timeout: float = 15.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        snap = {i["id"]: i for i in queue.snapshot()}
        if snap.get(item_id, {}).get("status") in _TERMINAL:
            return snap[item_id]
        time.sleep(0.05)
    raise AssertionError(f"item {item_id} never reached a terminal status")


def _queue(tmp_path: Path) -> tuple[BookImportQueue, Database, Path]:
    lib = tmp_path / "lib"
    lib.mkdir()
    db = Database(lib / "x.sqlite")
    q = BookImportQueue(db=db, library_root=lib, r2_client=None, books_dir="_books")
    return q, db, lib


@pytest.mark.integration
def test_import_creates_book_via_scrape_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("favgallery.book_importer.subprocess.run", _no_gallery_dl)
    monkeypatch.setattr("favgallery.book_importer.scrape_images_from_html", _make_scraper(2))

    q, db, lib = _queue(tmp_path)
    item = q.enqueue("https://example.test/gallery/my-cool-book")
    final = _wait_terminal(q, item["id"])

    assert final["status"] == "done"
    assert final["book_id"] is not None
    books = db.books()
    assert len(books) == 1
    assert books[0].page_count == 2
    # Pages were copied under the books dir.
    assert (lib / "_books" / str(books[0].id) / "0001.png").exists()


@pytest.mark.integration
def test_import_no_images_marks_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("favgallery.book_importer.subprocess.run", _no_gallery_dl)
    monkeypatch.setattr("favgallery.book_importer.scrape_images_from_html", _make_scraper(0))

    q, db, _lib = _queue(tmp_path)
    item = q.enqueue("https://example.test/empty")
    final = _wait_terminal(q, item["id"])

    assert final["status"] == "error"
    assert final["error"] == "画像が見つかりませんでした"
    assert db.books() == []


@pytest.mark.integration
def test_queue_drains_two_urls_and_skips_duplicate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Both URLs yield identical images, so the second must be detected as a
    # duplicate. Crucially, BOTH must reach a terminal status (queue self-chains).
    monkeypatch.setattr("favgallery.book_importer.subprocess.run", _no_gallery_dl)
    monkeypatch.setattr("favgallery.book_importer.scrape_images_from_html", _make_scraper(2))

    q, db, _lib = _queue(tmp_path)
    item1 = q.enqueue("https://example.test/a")
    item2 = q.enqueue("https://example.test/b")

    f1 = _wait_terminal(q, item1["id"])
    f2 = _wait_terminal(q, item2["id"])

    assert f1["status"] == "done"
    assert f2["status"] == "skipped"  # identical fingerprint -> dedup
    assert len(db.books()) == 1

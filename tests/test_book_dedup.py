"""Tests for favgallery.book_dedup (duplicate-book detection)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from favgallery.book_dedup import (
    BookIndexRunner,
    find_duplicate_book,
    fingerprint_for_ordered_files,
    fingerprint_matches,
    sample_page_nums,
)
from favgallery.db import Database


@pytest.fixture
def db(tmp_path: Path) -> Database:
    return Database(tmp_path / "x.sqlite")


def _make_img(path: Path, seed: int, size: int = 128) -> None:
    """A smooth, low-frequency, seed-distinct pattern that survives JPEG q85.

    Low spatial frequency (~1-2 cycles across the image) keeps the DCT-based
    pHash stable under JPEG re-encoding, like real photographic content, while
    seed-varying orientation + phase keeps different books well-separated.
    """
    xv, yv = np.meshgrid(np.linspace(0, 1, size), np.linspace(0, 1, size))
    ang = seed * 0.6
    u = xv * np.cos(ang) + yv * np.sin(ang)
    r = 128 + 100 * np.sin(2 * np.pi * (u + 0.05 * seed))
    g = 128 + 100 * np.sin(2 * np.pi * (1.5 * u + 0.1 * seed))
    b = 128 + 100 * np.cos(2 * np.pi * (yv + 0.07 * seed))
    arr = np.stack([r, g, b], axis=-1).clip(0, 255).astype(np.uint8)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(arr, "RGB").save(path)


def _make_book_on_disk(db: Database, lib: Path, title: str, seeds: list[int]) -> int:
    book = db.create_book(title, None, len(seeds))
    pages: list[tuple[int, str, int | None, int | None]] = []
    for i, seed in enumerate(seeds, start=1):
        rel = f"_books/{book.id}/{i:04d}.png"
        _make_img(lib / rel, seed)
        pages.append((i, rel, 128, 128))
    db.add_book_pages(book.id, pages)
    return book.id


# ---------------------------------------------------------------------------
# sample_page_nums
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_sample_page_nums_empty_for_tiny_books() -> None:
    assert sample_page_nums(0) == []
    assert sample_page_nums(1) == []


@pytest.mark.unit
def test_sample_page_nums_includes_last_and_excludes_cover() -> None:
    for pc in (2, 3, 4, 10, 37, 100):
        nums = sample_page_nums(pc)
        assert nums == sorted(set(nums)), "must be sorted + unique"
        assert all(2 <= n <= pc for n in nums), "no cover (1), within range"
        assert pc in nums, "always include the last page"


# ---------------------------------------------------------------------------
# fingerprint_matches
# ---------------------------------------------------------------------------

Z = "0000000000000000"


@pytest.mark.unit
def test_matches_identical() -> None:
    cover = "f0f0f0f0f0f0f0f0"
    samples = [(2, "aaaaaaaaaaaaaaaa"), (3, "bbbbbbbbbbbbbbbb"), (4, "cccccccccccccccc")]
    assert fingerprint_matches(5, cover, samples, 5, cover, samples) is True


@pytest.mark.unit
def test_matches_within_threshold() -> None:
    # 0x...3 = 2 bits set -> Hamming 2 <= 4
    assert fingerprint_matches(1, Z, [], 1, "0000000000000003", []) is True


@pytest.mark.unit
def test_no_match_just_beyond_threshold() -> None:
    # 0x1f = 5 bits set -> Hamming 5 > 4
    assert fingerprint_matches(1, Z, [], 1, "000000000000001f", []) is False


@pytest.mark.unit
def test_no_match_different_page_count() -> None:
    assert fingerprint_matches(5, Z, [], 6, Z, []) is False


@pytest.mark.unit
def test_no_match_when_cover_missing() -> None:
    s = [(2, Z), (3, Z), (4, Z)]
    assert fingerprint_matches(5, None, s, 5, Z, s) is False
    assert fingerprint_matches(5, Z, s, 5, None, s) is False


@pytest.mark.unit
def test_no_match_when_one_sample_differs() -> None:
    new_s = [(2, Z), (3, Z), (4, Z)]
    cand_s = [(2, Z), (3, "ffffffffffffffff"), (4, Z)]
    assert fingerprint_matches(5, Z, new_s, 5, Z, cand_s) is False


@pytest.mark.unit
def test_small_book_matches_on_cover_alone() -> None:
    # page_count=1 -> only the cover exists; min(3, 1) = 1 evidence is enough.
    assert fingerprint_matches(1, "1234123412341234", [], 1, "1234123412341234", []) is True


# ---------------------------------------------------------------------------
# DB round-trip + find_duplicate_book
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_book_hash_roundtrip(db: Database) -> None:
    book = db.create_book("t", None, 5)
    db.upsert_book_hash(
        book_id=book.id,
        page_count=5,
        cover_phash="abc",
        sample_phashes=[(2, "d1"), (3, "d2")],
        indexed_at=123,
    )
    assert db.get_book_hash(book.id) == (5, "abc", [(2, "d1"), (3, "d2")])
    # upsert overwrites
    db.upsert_book_hash(
        book_id=book.id, page_count=6, cover_phash="xyz",
        sample_phashes=[], indexed_at=200,
    )
    assert db.get_book_hash(book.id) == (6, "xyz", [])


@pytest.mark.unit
def test_book_ids_without_hash(db: Database) -> None:
    b1 = db.create_book("a", None, 1)
    b2 = db.create_book("b", None, 1)
    db.upsert_book_hash(
        book_id=b1.id, page_count=1, cover_phash="x", sample_phashes=[], indexed_at=1
    )
    assert db.book_ids_without_hash() == [b2.id]


@pytest.mark.unit
def test_find_duplicate_book(db: Database) -> None:
    book = db.create_book("orig", None, 5)
    cover = Z
    samples = [(2, "1111111111111111"), (3, "2222222222222222"), (4, "3333333333333333")]
    db.upsert_book_hash(
        book_id=book.id, page_count=5, cover_phash=cover, sample_phashes=samples, indexed_at=1
    )

    assert find_duplicate_book(db, 5, cover, samples) == (book.id, "orig")
    assert find_duplicate_book(db, 6, cover, samples) is None          # different page_count
    assert find_duplicate_book(db, 5, "ffffffffffffffff", samples) is None  # different cover
    assert find_duplicate_book(db, 5, None, samples) is None           # no cover to verify


# ---------------------------------------------------------------------------
# Real images: index_book + fingerprint_for_ordered_files
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_real_image_book_dedup_end_to_end(tmp_path: Path, db: Database) -> None:
    lib = tmp_path / "library"
    seeds = [1, 2, 3, 4, 5]
    book_id = _make_book_on_disk(db, lib, "A", seeds)

    BookIndexRunner(db, lib, None).index_book(book_id)
    assert db.get_book_hash(book_id) is not None

    # An import of the identical pages is detected as a duplicate.
    files = [lib / "_books" / str(book_id) / f"{i:04d}.png" for i in range(1, 6)]
    pc, cover, samples = fingerprint_for_ordered_files(files)
    assert find_duplicate_book(db, pc, cover, samples) == (book_id, "A")

    # A genuinely different book (same page count) is NOT skipped.
    other = tmp_path / "other"
    ofiles = []
    for i, seed in enumerate([51, 52, 53, 54, 55], start=1):
        p = other / f"{i:04d}.png"
        _make_img(p, seed)
        ofiles.append(p)
    pc2, cover2, samples2 = fingerprint_for_ordered_files(ofiles)
    assert find_duplicate_book(db, pc2, cover2, samples2) is None


@pytest.mark.unit
def test_reencoded_jpeg_still_matches(tmp_path: Path, db: Database) -> None:
    lib = tmp_path / "library"
    book_id = _make_book_on_disk(db, lib, "B", [3, 6, 9, 12, 15])
    BookIndexRunner(db, lib, None).index_book(book_id)

    # Re-encode the same pages as JPEG q85 — pHash should survive.
    jdir = tmp_path / "jpg"
    jfiles = []
    for i in range(1, 6):
        src = lib / "_books" / str(book_id) / f"{i:04d}.png"
        im = Image.open(src).convert("RGB")
        out = jdir / f"{i:04d}.jpg"
        out.parent.mkdir(parents=True, exist_ok=True)
        im.save(out, "JPEG", quality=85)
        jfiles.append(out)
    pc, cover, samples = fingerprint_for_ordered_files(jfiles)
    assert find_duplicate_book(db, pc, cover, samples) == (book_id, "B")


@pytest.mark.unit
def test_index_runner_backfills_only_unindexed(tmp_path: Path, db: Database) -> None:
    lib = tmp_path / "library"
    b1 = _make_book_on_disk(db, lib, "one", [1, 2, 3])
    b2 = _make_book_on_disk(db, lib, "two", [4, 5, 6])
    # Pre-index b1 so the runner should only process b2.
    BookIndexRunner(db, lib, None).index_book(b1)
    assert db.book_ids_without_hash() == [b2]
    runner = BookIndexRunner(db, lib, None)
    runner._worker()  # run synchronously
    assert db.book_ids_without_hash() == []
    assert runner.state.books_total == 1
    assert runner.state.books_indexed == 1

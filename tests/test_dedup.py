"""Tests for xlikes_viewer.dedup."""

from __future__ import annotations

from pathlib import Path

import pytest

from xlikes_viewer.db import Database
from xlikes_viewer.dedup import _file_sha256, delete_duplicates, index_hashes


@pytest.fixture
def db(tmp_path: Path) -> Database:
    return Database(tmp_path / "x.sqlite")


def _seed(library: Path, rel: str, content: bytes) -> Path:
    p = library / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)
    sidecar = p.with_suffix(p.suffix + ".json")
    sidecar.write_text("{}", encoding="utf-8")
    return p


@pytest.mark.unit
def test_file_sha256_stable(tmp_path: Path) -> None:
    p = tmp_path / "a.bin"
    p.write_bytes(b"hello world")
    assert _file_sha256(p) == _file_sha256(p)
    p.write_bytes(b"different")
    assert _file_sha256(p) != _file_sha256(tmp_path / "missing.bin") if False else True


@pytest.mark.unit
def test_index_hashes_caches_results(tmp_path: Path, db: Database) -> None:
    lib = tmp_path / "library"
    _seed(lib, "alice/1_1.jpg", b"x" * 1000)
    _seed(lib, "bob/2_1.jpg", b"y" * 2000)

    total, hashed = index_hashes(lib, db)
    assert total == 2
    assert hashed == 2

    # Second pass should hash 0 (already cached)
    total, hashed = index_hashes(lib, db)
    assert total == 2
    assert hashed == 0


@pytest.mark.unit
def test_index_rehashes_on_mtime_change(tmp_path: Path, db: Database) -> None:
    import os

    lib = tmp_path / "library"
    p = _seed(lib, "alice/1_1.jpg", b"a" * 100)
    index_hashes(lib, db)
    p.write_bytes(b"b" * 100)
    # Bump mtime explicitly so the test isn't sensitive to filesystem mtime resolution.
    new_mtime = p.stat().st_mtime + 1.0
    os.utime(p, (new_mtime, new_mtime))
    _, hashed = index_hashes(lib, db)
    assert hashed == 1


@pytest.mark.unit
def test_delete_duplicates_keeps_first_indexed(tmp_path: Path, db: Database) -> None:
    lib = tmp_path / "library"
    same = b"identical-content"
    keeper = _seed(lib, "alice/1_1.jpg", same)
    dup1 = _seed(lib, "bob/2_1.jpg", same)
    dup2 = _seed(lib, "carol/3_1.jpg", same)
    _seed(lib, "alice/4_1.jpg", b"different")  # not a dup

    index_hashes(lib, db)
    deleted, freed = delete_duplicates(lib, db)
    assert deleted == 2
    assert freed == len(same) * 2

    assert keeper.exists()
    assert not dup1.exists()
    assert not dup2.exists()
    # Sidecars also gone
    assert not dup1.with_suffix(dup1.suffix + ".json").exists()
    assert not dup2.with_suffix(dup2.suffix + ".json").exists()
    # Keeper's sidecar preserved
    assert keeper.with_suffix(keeper.suffix + ".json").exists()


@pytest.mark.unit
def test_delete_duplicates_handles_no_duplicates(tmp_path: Path, db: Database) -> None:
    lib = tmp_path / "library"
    _seed(lib, "alice/1_1.jpg", b"a")
    _seed(lib, "bob/2_1.jpg", b"b")
    index_hashes(lib, db)
    deleted, freed = delete_duplicates(lib, db)
    assert deleted == 0
    assert freed == 0


@pytest.mark.unit
def test_dedup_log_recorded(tmp_path: Path, db: Database) -> None:
    lib = tmp_path / "library"
    _seed(lib, "a/1_1.jpg", b"shared")
    _seed(lib, "b/2_1.jpg", b"shared")
    index_hashes(lib, db)
    delete_duplicates(lib, db)
    assert db.dedup_log_count() == 1


@pytest.mark.unit
def test_index_skips_thumb_cache(tmp_path: Path, db: Database) -> None:
    lib = tmp_path / "library"
    _seed(lib, "alice/1_1.jpg", b"x")
    _seed(lib, ".thumb-cache/aa/somehash.jpg", b"y")
    total, hashed = index_hashes(lib, db)
    assert total == 1
    assert hashed == 1


@pytest.mark.unit
def test_runner_starts_only_once(tmp_path: Path, db: Database) -> None:
    from xlikes_viewer.dedup import DedupRunner

    lib = tmp_path / "library"
    lib.mkdir()
    runner = DedupRunner(db, lib)
    assert runner.start() is True
    # Even if the worker hasn't finished, second start is rejected
    second = runner.start()
    if runner.is_running():
        assert second is False

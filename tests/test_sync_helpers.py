"""Characterization tests for SyncRunner's R2 sweep helpers.

sync.py had no dedicated tests; these pin the observable behaviour of
cleanup_local / _upload_library_to_r2 (key derivation, what gets uploaded,
what gets deleted locally) so later refactors can't silently change it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from xlikes_viewer.db import Database
from xlikes_viewer.sync import SyncRunner


class _FakeR2:
    """Minimal R2 stand-in: tracks uploads, reports a fixed key set."""

    def __init__(self, existing_keys: tuple[str, ...] = ()) -> None:
        self._keys: set[str] = set(existing_keys)
        self.uploaded: list[str] = []

    def list_all_keys(self) -> set[str]:
        return set(self._keys)

    def upload_file(self, path: Path, key: str) -> None:
        self.uploaded.append(key)
        self._keys.add(key)


def _make_library(tmp_path: Path) -> Path:
    lib = tmp_path / "lib"
    (lib / "alice").mkdir(parents=True)
    (lib / "alice" / "1.jpg").write_bytes(b"a")
    (lib / "alice" / "2.jpg").write_bytes(b"b")
    (lib / "alice" / "1.jpg.json").write_text("{}", encoding="utf-8")  # sidecar
    (lib / "thumbs").mkdir()
    (lib / "thumbs" / "t.jpg").write_bytes(b"t")  # thumbnail
    return lib


def _runner(tmp_path: Path, lib: Path, fake: _FakeR2 | None) -> SyncRunner:
    return SyncRunner(
        config_path=tmp_path / "gdl.json",
        db=Database(tmp_path / "x.sqlite"),
        library_root=lib,
        r2_client=fake,
    )


@pytest.mark.unit
def test_cleanup_local_deletes_only_files_present_in_r2(tmp_path: Path) -> None:
    lib = _make_library(tmp_path)
    fake = _FakeR2(existing_keys=("alice/1.jpg",))  # 1.jpg in R2, 2.jpg not
    result = _runner(tmp_path, lib, fake).cleanup_local()

    assert result == {"deleted": 1, "checked": 2, "errors": 0}
    assert not (lib / "alice" / "1.jpg").exists()  # in R2 -> removed locally
    assert (lib / "alice" / "2.jpg").exists()  # not in R2 -> kept
    assert (lib / "alice" / "1.jpg.json").exists()  # sidecar untouched
    assert (lib / "thumbs" / "t.jpg").exists()  # thumbnail untouched


@pytest.mark.unit
def test_cleanup_local_noop_without_r2(tmp_path: Path) -> None:
    lib = _make_library(tmp_path)
    result = _runner(tmp_path, lib, None).cleanup_local()
    assert result == {"deleted": 0, "checked": 0, "errors": 0}
    assert (lib / "alice" / "1.jpg").exists()


@pytest.mark.unit
def test_upload_library_uploads_new_and_clears_local(tmp_path: Path) -> None:
    lib = _make_library(tmp_path)
    fake = _FakeR2(existing_keys=("alice/2.jpg",))  # 2.jpg already uploaded
    _runner(tmp_path, lib, fake)._upload_library_to_r2()

    assert fake.uploaded == ["alice/1.jpg"]  # only the missing one is uploaded
    # Uploaded AND already-present media are removed locally to reclaim space.
    assert not (lib / "alice" / "1.jpg").exists()
    assert not (lib / "alice" / "2.jpg").exists()
    # Sidecars + thumbnails are never uploaded nor deleted.
    assert (lib / "alice" / "1.jpg.json").exists()
    assert (lib / "thumbs" / "t.jpg").exists()

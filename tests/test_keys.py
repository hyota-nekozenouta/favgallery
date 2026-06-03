"""Tests for xlikes_viewer.keys (R2 key derivation + media predicates)."""

from __future__ import annotations

from pathlib import Path

import pytest

from xlikes_viewer.keys import (
    MEDIA_EXTENSIONS,
    is_media_file,
    iter_media_keys,
    r2_key_for_path,
)


@pytest.mark.unit
def test_r2_key_is_library_relative_posix(tmp_path: Path) -> None:
    lib = tmp_path / "lib"
    media = lib / "alice" / "1001_1.jpg"
    assert r2_key_for_path(media, lib) == "alice/1001_1.jpg"


@pytest.mark.unit
def test_is_media_file_case_insensitive() -> None:
    assert is_media_file(Path("a/b.jpg"))
    assert is_media_file(Path("a/b.MP4"))
    assert not is_media_file(Path("a/b.jpg.json"))
    assert not is_media_file(Path("a/b.txt"))


@pytest.mark.unit
def test_media_extensions_cover_image_and_video() -> None:
    assert ".jpg" in MEDIA_EXTENSIONS
    assert ".mp4" in MEDIA_EXTENSIONS
    assert ".json" not in MEDIA_EXTENSIONS


@pytest.mark.unit
def test_iter_media_keys_filters_sidecars_and_thumbs(tmp_path: Path) -> None:
    lib = tmp_path / "lib"
    (lib / "alice").mkdir(parents=True)
    (lib / "alice" / "1.jpg").write_bytes(b"x")
    (lib / "alice" / "1.jpg.json").write_text("{}", encoding="utf-8")  # sidecar: skip
    (lib / "thumbs").mkdir()
    (lib / "thumbs" / "t.jpg").write_bytes(b"x")  # thumbnail: skip
    (lib / "alice" / "note.txt").write_text("x", encoding="utf-8")  # non-media: skip

    found = {key for _path, key in iter_media_keys(lib)}
    assert found == {"alice/1.jpg"}

"""Tests for xlikes_viewer.scanner."""

from __future__ import annotations

from pathlib import Path

import pytest

from xlikes_viewer.scanner import scan_library
from xlikes_viewer.x_helpers import extract_hashtags


@pytest.mark.unit
def test_scan_returns_all_posts(fake_library: Path) -> None:
    idx = scan_library(fake_library)
    assert len(idx.posts) == 4


@pytest.mark.unit
def test_scan_orders_newest_first(fake_library: Path) -> None:
    idx = scan_library(fake_library)
    dates = [p.date for p in idx.posts]
    assert dates == sorted(dates, reverse=True)


@pytest.mark.unit
def test_scan_groups_authors_by_count(fake_library: Path) -> None:
    idx = scan_library(fake_library)
    names = list(idx.authors.keys())
    assert names[0] == "alice"  # 2 posts
    assert idx.authors["alice"].post_count == 2
    assert idx.authors["alice"].nick == "アリス"


@pytest.mark.unit
def test_scan_extracts_hashtags(fake_library: Path) -> None:
    idx = scan_library(fake_library)
    assert idx.tags["cat"] == 2
    assert "pixiv" in idx.tags
    assert "vlog" in idx.tags


@pytest.mark.unit
def test_filter_by_author(fake_library: Path) -> None:
    idx = scan_library(fake_library)
    assert len(idx.filter(author="alice")) == 2
    assert len(idx.filter(author="bob")) == 1


@pytest.mark.unit
def test_filter_by_tag(fake_library: Path) -> None:
    idx = scan_library(fake_library)
    assert len(idx.filter(tag="cat")) == 2
    assert len(idx.filter(tag="vlog")) == 1


@pytest.mark.unit
def test_filter_by_media_type(fake_library: Path) -> None:
    idx = scan_library(fake_library)
    assert len(idx.filter(media_type="video")) == 1
    assert len(idx.filter(media_type="photo")) == 3


@pytest.mark.unit
def test_filter_by_query_matches_content(fake_library: Path) -> None:
    idx = scan_library(fake_library)
    assert len(idx.filter(query="hello")) == 1
    assert len(idx.filter(query="アリス")) == 2  # nick


@pytest.mark.unit
def test_scan_missing_library_returns_empty(tmp_path: Path) -> None:
    idx = scan_library(tmp_path / "nope")
    assert idx.posts == []


@pytest.mark.unit
def testextract_hashtags_dedupes_and_sorts() -> None:
    assert extract_hashtags("#a #b #a") == ("a", "b")


@pytest.mark.unit
def testextract_hashtags_handles_japanese() -> None:
    tags = extract_hashtags("#日本語 hello #英語")
    assert "日本語" in tags
    assert "英語" in tags

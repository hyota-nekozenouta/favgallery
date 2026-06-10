"""Tests for favgallery.scanner."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from favgallery.db import Database
from favgallery.scanner import ingest_to_db, scan_library
from favgallery.x_helpers import extract_hashtags


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


# --- ingest_to_db: incremental ingest ------------------------------------


@pytest.mark.unit
def test_ingest_inserts_all_on_empty_db(fake_library: Path, tmp_path: Path) -> None:
    db = Database(tmp_path / "x.sqlite")
    n = ingest_to_db(fake_library, db)
    assert n == 4
    assert db.posts_count() == 4


@pytest.mark.unit
def test_ingest_skips_already_ingested(
    fake_library: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second ingest of an unchanged library must re-process nothing.

    Regression for the post-sync slowdown: ingest_to_db used to re-upsert every
    sidecar on every sync (O(library size) -> ~9 min at 12k posts). It must now
    skip posts already in the DB without upserting them.
    """
    db = Database(tmp_path / "x.sqlite")
    assert ingest_to_db(fake_library, db) == 4

    calls = {"n": 0}
    real_upsert = db.upsert_post

    def spy(**kw: object) -> None:
        calls["n"] += 1
        return real_upsert(**kw)  # type: ignore[arg-type]

    monkeypatch.setattr(db, "upsert_post", spy)
    assert ingest_to_db(fake_library, db) == 0  # nothing new
    assert calls["n"] == 0  # and no redundant upserts
    assert db.posts_count() == 4


@pytest.mark.unit
def test_ingest_picks_up_only_new_sidecar(fake_library: Path, tmp_path: Path) -> None:
    db = Database(tmp_path / "x.sqlite")
    ingest_to_db(fake_library, db)

    new_dir = fake_library / "dave"
    new_dir.mkdir(parents=True, exist_ok=True)
    (new_dir / "4001_1.jpg.json").write_text(
        json.dumps(
            {
                "extension": "jpg",
                "type": "photo",
                "tweet_id": 4001,
                "num": 1,
                "date": "2026-01-01 00:00:00",
                "author": {"name": "dave", "nick": "Dave"},
                "content": "brand new like",
                "favorite_count": 1,
                "view_count": 1,
            }
        ),
        encoding="utf-8",
    )

    assert ingest_to_db(fake_library, db) == 1  # only the new one
    assert db.posts_count() == 5

"""Tests for favgallery.db."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from favgallery.db import Database, TimelinePost


@pytest.fixture
def db(tmp_path: Path) -> Database:
    return Database(tmp_path / "x.sqlite")


@pytest.mark.unit
def test_create_list_returns_summary(db: Database) -> None:
    row = db.create_list("お気に入り")
    assert row.id > 0
    assert row.name == "お気に入り"
    assert row.item_count == 0


@pytest.mark.unit
def test_lists_listed_in_creation_order(db: Database) -> None:
    a = db.create_list("a")
    b = db.create_list("b")
    rows = db.lists()
    assert [r.name for r in rows] == ["a", "b"]
    assert rows[0].id == a.id
    assert rows[1].id == b.id


@pytest.mark.unit
def test_create_list_rejects_blank(db: Database) -> None:
    with pytest.raises(ValueError):
        db.create_list("   ")


def _upsert(db: Database, tweet_id: str, num: int) -> None:
    db.upsert_post(
        tweet_id=tweet_id,
        num=num,
        rel_media=f"a/{tweet_id}_{num}.jpg",
        media_type="photo",
        extension="jpg",
        width=1,
        height=1,
        date="2026-01-01 00:00:00",
        author_name="a",
        author_nick="",
        content="",
        favorite_count=0,
        view_count=0,
        sensitive=False,
        lang="ja",
        hashtags=(),
    )


@pytest.mark.unit
def test_all_post_keys_returns_inserted_keys(db: Database) -> None:
    _upsert(db, "1001", 1)
    _upsert(db, "1001", 2)
    assert db.all_post_keys() == {("1001", 1), ("1001", 2)}


@pytest.mark.unit
def test_all_post_keys_empty_when_no_posts(db: Database) -> None:
    assert db.all_post_keys() == set()


@pytest.mark.unit
def test_create_list_unique_constraint(db: Database) -> None:
    import sqlite3

    db.create_list("dup")
    with pytest.raises(sqlite3.IntegrityError):
        db.create_list("dup")


@pytest.mark.unit
def test_add_item_idempotent(db: Database) -> None:
    lst = db.create_list("a")
    assert db.add_item(lst.id, "111", 1) is True
    assert db.add_item(lst.id, "111", 1) is False
    assert db.lists()[0].item_count == 1


@pytest.mark.unit
def test_remove_item(db: Database) -> None:
    lst = db.create_list("a")
    db.add_item(lst.id, "111", 1)
    assert db.remove_item(lst.id, "111", 1) is True
    assert db.remove_item(lst.id, "111", 1) is False


@pytest.mark.unit
def test_lists_for_post(db: Database) -> None:
    a = db.create_list("a")
    b = db.create_list("b")
    db.add_item(a.id, "111", 1)
    db.add_item(b.id, "111", 1)
    db.add_item(b.id, "222", 1)
    assert sorted(db.lists_for_post("111", 1)) == sorted([a.id, b.id])
    assert db.lists_for_post("222", 1) == [b.id]


@pytest.mark.unit
def test_posts_in_list(db: Database) -> None:
    a = db.create_list("a")
    db.add_item(a.id, "111", 1)
    db.add_item(a.id, "111", 2)
    keys = db.posts_in_list(a.id)
    assert ("111", 1) in keys
    assert ("111", 2) in keys


@pytest.mark.unit
def test_delete_list_cascades_items(db: Database) -> None:
    a = db.create_list("a")
    db.add_item(a.id, "111", 1)
    assert db.delete_list(a.id) is True
    assert db.lists() == []
    # subsequent add to dead list should be ignored or raise; either way no rows leak
    assert db.lists_for_post("111", 1) == []


@pytest.mark.unit
def test_timeline_upsert_replaces(db: Database) -> None:
    p = TimelinePost(
        tweet_id="111",
        num=1,
        fetched_at=10,
        date="2026-05-06 10:00:00",
        author_name="alice",
        author_nick="アリス",
        author_avatar_url="",
        content="hi #cat",
        media_url="https://pbs.twimg.com/media/abc.jpg",
        thumb_url="https://pbs.twimg.com/media/abc.jpg?name=small",
        media_type="photo",
        width=320,
        height=240,
        favorite_count=1,
        view_count=2,
        hashtags=("cat",),
    )
    db.upsert_timeline_post(p)
    db.upsert_timeline_post(dataclasses.replace(p, content="updated"))
    total, posts = db.list_timeline_posts(limit=10, offset=0)
    assert total == 1
    assert posts[0].content == "updated"


@pytest.mark.unit
def test_timeline_listed_newest_first(db: Database) -> None:
    older = TimelinePost(
        tweet_id="1",
        num=1,
        fetched_at=1,
        date="2025-01-01 00:00:00",
        author_name="a",
        author_nick="",
        author_avatar_url="",
        content="",
        media_url="https://pbs.twimg.com/media/a.jpg",
        thumb_url="https://pbs.twimg.com/media/a.jpg",
        media_type="photo",
        width=None,
        height=None,
        favorite_count=0,
        view_count=0,
        hashtags=(),
    )
    newer = TimelinePost(
        tweet_id="2",
        num=1,
        fetched_at=2,
        date="2026-05-06 10:00:00",
        author_name="a",
        author_nick="",
        author_avatar_url="",
        content="",
        media_url="https://pbs.twimg.com/media/b.jpg",
        thumb_url="https://pbs.twimg.com/media/b.jpg",
        media_type="photo",
        width=None,
        height=None,
        favorite_count=0,
        view_count=0,
        hashtags=(),
    )
    db.upsert_timeline_post(older)
    db.upsert_timeline_post(newer)
    _, posts = db.list_timeline_posts(limit=10, offset=0)
    assert posts[0].tweet_id == "2"


# --- Pragmas (Phase 1 / 2026-06-10 perf) -------------------------------------


def test_wal_mode_and_pragmas_enabled(tmp_path: Path) -> None:
    """WAL + NORMAL + busy_timeout: 並行読み書きと再起動耐性のため (perf Phase 1)。"""
    db = Database(tmp_path / "p.sqlite")
    assert db._conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert db._conn.execute("PRAGMA synchronous").fetchone()[0] == 1  # NORMAL
    assert db._conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000


def test_timeline_media_type_index_exists(tmp_path: Path) -> None:
    """/api/timeline?media_type= の SQL フィルタ用インデックス (perf Phase 1)。"""
    db = Database(tmp_path / "p.sqlite")
    names = {
        r[0] for r in db._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()
    }
    assert "timeline_posts_media_type" in names


def test_book_tags_bulk_groups_by_book(tmp_path: Path) -> None:
    """N+1 根治: /api/books 用の一括タグ取得 (perf Phase 1)。"""
    db = Database(tmp_path / "p.sqlite")
    b1 = db.create_book("one", None, 1)
    b2 = db.create_book("two", None, 1)
    b3 = db.create_book("three", None, 1)
    db.set_book_tags(b1.id, ["a", "b"])
    db.set_book_tags(b2.id, ["b"])
    bulk = db.book_tags_bulk()
    assert bulk[b1.id] == ["a", "b"]
    assert bulk[b2.id] == ["b"]
    assert bulk.get(b3.id, []) == []

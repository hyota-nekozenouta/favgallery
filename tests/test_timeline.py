"""Tests for favgallery.timeline."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from gallery_dl import exception as gdle

from favgallery.db import Database
from favgallery.timeline import (
    DEFAULT_TIMELINE_URL,
    REFRESH_COOLDOWN_SECONDS,
    TimelineRefresher,
    _post_from_meta,
)


@pytest.fixture
def db(tmp_path: Path) -> Database:
    return Database(tmp_path / "x.sqlite")


@pytest.mark.unit
def test_post_from_meta_basic() -> None:
    meta = {
        "tweet_id": 111,
        "num": 1,
        "type": "photo",
        "date": "2026-05-06 10:00:00",
        "author": {"name": "alice", "nick": "アリス", "profile_image": "u"},
        "content": "hi #cat",
        "width": 800,
        "height": 600,
        "favorite_count": 5,
        "view_count": 10,
    }
    post = _post_from_meta(
        "https://pbs.twimg.com/media/abc.jpg?name=large",
        meta,
        100,
    )
    assert post is not None
    assert post.tweet_id == "111"
    assert post.num == 1
    assert post.author_name == "alice"
    assert post.author_nick == "アリス"
    assert post.media_type == "photo"
    assert "cat" in post.hashtags
    assert post.thumb_url.endswith("name=small")


@pytest.mark.unit
def test_post_from_meta_skips_when_tweet_id_missing() -> None:
    assert _post_from_meta("u", {"num": 1}, 0) is None


@pytest.mark.unit
def test_refresher_cooldown(db: Database, tmp_path: Path) -> None:
    r = TimelineRefresher(db, tmp_path / "config.json")
    r.state.last_finished = 1e12  # far in the future relative to now
    ok, reason = r.can_start()
    assert ok is False
    assert "cooldown" in (reason or "")


@pytest.mark.unit
def test_refresher_runs_via_mocked_fetch(db: Database, tmp_path: Path) -> None:
    r = TimelineRefresher(db, tmp_path / "config.json")
    fake = [
        (
            "https://pbs.twimg.com/media/aaa.jpg?name=large",
            {
                "tweet_id": 1,
                "num": 1,
                "type": "photo",
                "date": "2026-05-06 10:00:00",
                "author": {"name": "a", "nick": "ア"},
                "content": "x",
                "width": 100,
                "height": 100,
                "favorite_count": 0,
                "view_count": 0,
            },
        ),
        (
            "https://video.twimg.com/v.mp4",
            {
                "tweet_id": 2,
                "num": 1,
                "type": "video",
                "date": "2026-05-06 11:00:00",
                "author": {"name": "b", "nick": ""},
                "content": "vid",
            },
        ),
    ]
    with patch("favgallery.timeline.fetch_timeline_metadata", return_value=fake):
        ok = r.start()
        assert ok is True
        # block until the worker finishes
        for _ in range(50):
            if not r.state.running:
                break
            import time

            time.sleep(0.05)
    total, posts = db.list_timeline_posts(limit=10, offset=0)
    # Both photo and video are displayable media types.
    assert total == 2
    tweet_ids = {p.tweet_id for p in posts}
    assert "1" in tweet_ids
    assert "2" in tweet_ids
    assert r.state.last_added == 2
    assert r.state.last_error is None


@pytest.mark.unit
def test_refresher_flags_auth_error_when_cookies_rejected(
    db: Database, tmp_path: Path
) -> None:
    r = TimelineRefresher(db, tmp_path / "config.json")

    def _fake_fetch(_config_path: Path, *, url: str, range_spec: str) -> list:
        # DataJob stores the auth failure on job.exception; fetch_timeline_metadata
        # re-raises it so the refresher can flag it (it does NOT silently return []).
        raise gdle.AuthRequired(("auth_token", "cookies"), "timeline")

    with patch("favgallery.timeline.fetch_timeline_metadata", side_effect=_fake_fetch):
        r._worker(DEFAULT_TIMELINE_URL, "1-300")

    assert r.state.auth_error is True
    assert r.state.last_added == 0
    assert r.state.last_error  # user-facing message set


@pytest.mark.unit
def test_refresher_flags_auth_error_on_expired_cookie_abort(
    db: Database, tmp_path: Path
) -> None:
    # Expired-but-present cookies surface as AbortExtraction (NOT an auth type);
    # only the message identifies it. The refresher must still flag auth_error.
    r = TimelineRefresher(db, tmp_path / "config.json")

    def _fake_fetch(_config_path: Path, *, url: str, range_spec: str) -> list:
        raise gdle.AbortExtraction("Unable to retrieve Tweets from this timeline")

    with patch("favgallery.timeline.fetch_timeline_metadata", side_effect=_fake_fetch):
        r._worker(DEFAULT_TIMELINE_URL, "1-300")

    assert r.state.auth_error is True
    assert r.state.last_added == 0


@pytest.mark.unit
def test_refresher_clean_run_has_no_auth_error(db: Database, tmp_path: Path) -> None:
    r = TimelineRefresher(db, tmp_path / "config.json")
    with patch("favgallery.timeline.fetch_timeline_metadata", return_value=[]):
        r._worker(DEFAULT_TIMELINE_URL, "1-300")

    assert r.state.auth_error is False
    assert r.state.last_added == 0
    assert r.state.last_error is None


@pytest.mark.unit
def test_constants_sane() -> None:
    assert REFRESH_COOLDOWN_SECONDS >= 30

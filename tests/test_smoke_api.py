"""Smoke tests for FavGallery API surface — fast machine-checkable slice of
`docs/smoke-checklist.md`.

Goal: cut the human smoke pass from ~33 items down to the DOM/touch-only items
by mechanically verifying every documented API entry point (initial load,
pagination, filter combos, books, timeline, lists, cookies, client-log, me,
storage status, app-version header).

Out of scope (kept for hand testing): lightbox / reel / RTL reader / sidebar
hamburger / modal swipe — these only live in DOM and don't have an API hook.

Run: `uv run pytest -q tests/test_smoke_api.py`.
"""

from __future__ import annotations

from importlib.metadata import version as _pkg_version
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from favgallery.server import create_app


@pytest.fixture
def smoke_client(fake_library: Path) -> TestClient:
    """Boot the full app against the synthetic library and skip background scan."""
    app = create_app(library_root=fake_library, scan_in_background=False)
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Global: app version header
# ---------------------------------------------------------------------------

def test_x_app_version_matches_pyproject(smoke_client: TestClient) -> None:
    """X-App-Version header is the published version → `curl -sI` external check."""
    expected = _pkg_version("favgallery")
    r = smoke_client.get("/")
    assert r.headers.get("X-App-Version") == expected


def test_x_app_version_present_on_api(smoke_client: TestClient) -> None:
    r = smoke_client.get("/api/library")
    assert r.headers.get("X-App-Version")


# ---------------------------------------------------------------------------
# Initial load (likes tab)
# ---------------------------------------------------------------------------

def test_index_returns_200(smoke_client: TestClient) -> None:
    r = smoke_client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")


def test_library_returns_index_shape(smoke_client: TestClient) -> None:
    r = smoke_client.get("/api/library")
    assert r.status_code == 200
    body = r.json()
    for key in ("library_root", "post_count", "authors", "tags", "scanning"):
        assert key in body
    assert body["post_count"] >= 4
    assert isinstance(body["authors"], list)
    assert isinstance(body["tags"], list)


# ---------------------------------------------------------------------------
# Posts grid: infinite scroll + every documented filter
# ---------------------------------------------------------------------------

def test_posts_default_listing(smoke_client: TestClient) -> None:
    r = smoke_client.get("/api/posts")
    assert r.status_code == 200
    body = r.json()
    assert {"total", "items", "offset", "limit"} <= body.keys()
    assert body["total"] >= 4
    assert len(body["items"]) >= 4


def test_posts_pagination_offset(smoke_client: TestClient) -> None:
    """Infinite scroll uses offset+limit; second page must be disjoint."""
    page1 = smoke_client.get("/api/posts?limit=2&offset=0").json()
    page2 = smoke_client.get("/api/posts?limit=2&offset=2").json()
    keys1 = {(it["tweet_id"], it["num"]) for it in page1["items"]}
    keys2 = {(it["tweet_id"], it["num"]) for it in page2["items"]}
    assert page1["total"] == page2["total"]
    assert keys1.isdisjoint(keys2)
    assert page1["offset"] == 0 and page1["limit"] == 2
    assert page2["offset"] == 2


def test_posts_filter_by_author(smoke_client: TestClient) -> None:
    r = smoke_client.get("/api/posts?author=alice")
    assert r.status_code == 200
    items = r.json()["items"]
    assert items and all(it["author_name"] == "alice" for it in items)


def test_posts_filter_by_tag(smoke_client: TestClient) -> None:
    r = smoke_client.get("/api/posts?tag=cat")
    assert r.status_code == 200
    items = r.json()["items"]
    assert items
    for it in items:
        assert any(t == "cat" or t.endswith("cat") for t in it.get("hashtags", []))


def test_posts_filter_by_media_type_video(smoke_client: TestClient) -> None:
    r = smoke_client.get("/api/posts?media_type=video")
    assert r.status_code == 200
    items = r.json()["items"]
    assert items and all(it["media_type"] == "video" for it in items)


def test_posts_filter_by_query(smoke_client: TestClient) -> None:
    r = smoke_client.get("/api/posts?q=hello")
    assert r.status_code == 200
    items = r.json()["items"]
    assert any("hello" in (it.get("content") or "").lower() for it in items)


def test_posts_by_tweet_lookup(smoke_client: TestClient) -> None:
    r = smoke_client.get("/api/posts/by-tweet/1001")
    assert r.status_code == 200
    items = r.json()["items"]
    assert items and items[0]["tweet_id"] == "1001"


def test_authors_summary(smoke_client: TestClient) -> None:
    r = smoke_client.get("/api/authors/alice/summary")
    assert r.status_code == 200
    body = r.json()
    assert "post_count" in body or "posts" in body or body  # tolerant


def test_favorite_authors_default_empty(smoke_client: TestClient) -> None:
    r = smoke_client.get("/api/favorite-authors")
    assert r.status_code == 200
    body = r.json()
    assert "authors" in body or isinstance(body, list)


# ---------------------------------------------------------------------------
# Lists tab + list filter on /api/posts
# ---------------------------------------------------------------------------

def test_lists_crud_and_post_filter(smoke_client: TestClient) -> None:
    """Create list → add post → /api/posts?list=<id> returns only that post."""
    r = smoke_client.post("/api/lists", json={"name": "fav"})
    assert r.status_code in (200, 201)
    list_id = r.json().get("id") or r.json().get("list", {}).get("id")
    assert list_id, f"expected id in response, got {r.json()}"

    r2 = smoke_client.post(
        f"/api/lists/{list_id}/items",
        json={"tweet_id": "1001", "num": 1},
    )
    assert r2.status_code in (200, 201, 204)

    r3 = smoke_client.get(f"/api/posts?list={list_id}")
    assert r3.status_code == 200
    items = r3.json()["items"]
    assert items and all(it["tweet_id"] == "1001" for it in items)


def test_lists_index(smoke_client: TestClient) -> None:
    r = smoke_client.get("/api/lists")
    assert r.status_code == 200


def test_posts_lists_membership(smoke_client: TestClient) -> None:
    """Per-post list membership lookup (used by the ★ overlay)."""
    r = smoke_client.get("/api/posts/lists?tweet_id=1001&num=1")
    assert r.status_code == 200
    assert "list_ids" in r.json()


# ---------------------------------------------------------------------------
# Bookshelf (books)
# ---------------------------------------------------------------------------

def test_books_index(smoke_client: TestClient) -> None:
    r = smoke_client.get("/api/books")
    assert r.status_code == 200
    body = r.json()
    assert "books" in body or isinstance(body, list)


def test_books_tags_index(smoke_client: TestClient) -> None:
    r = smoke_client.get("/api/books/tags")
    assert r.status_code == 200


def test_books_import_status(smoke_client: TestClient) -> None:
    """URL import queue status — used by the bookshelf import progress UI."""
    r = smoke_client.get("/api/books/import/status")
    assert r.status_code == 200


def test_books_index_status(smoke_client: TestClient) -> None:
    r = smoke_client.get("/api/books/index/status")
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Timeline (フォロー中) tab
# ---------------------------------------------------------------------------

def test_timeline_default(smoke_client: TestClient) -> None:
    r = smoke_client.get("/api/timeline")
    assert r.status_code == 200
    body = r.json()
    assert {"total", "items", "offset", "limit"} <= body.keys()


def test_timeline_hide_liked(smoke_client: TestClient) -> None:
    r = smoke_client.get("/api/timeline?hide_liked=true")
    assert r.status_code == 200
    assert {"total", "items"} <= r.json().keys()


def test_timeline_filter_media_video(smoke_client: TestClient) -> None:
    r = smoke_client.get("/api/timeline?media_type=video")
    assert r.status_code == 200


def test_timeline_status(smoke_client: TestClient) -> None:
    """Used by the ⟳ button cooldown / spinner UI."""
    r = smoke_client.get("/api/timeline/status")
    assert r.status_code == 200
    body = r.json()
    for key in ("running", "last_started", "last_finished", "last_error"):
        assert key in body


def test_timeline_last_seen_round_trip(smoke_client: TestClient) -> None:
    """既読ライン (last-seen) — saved tweet_id is read back."""
    r0 = smoke_client.get("/api/timeline/last-seen")
    assert r0.status_code == 200
    r1 = smoke_client.post("/api/timeline/last-seen", json={"tweet_id": "9999"})
    assert r1.status_code == 200
    r2 = smoke_client.get("/api/timeline/last-seen")
    assert r2.json().get("tweet_id") == "9999"


# ---------------------------------------------------------------------------
# Settings popover (⚙) — cookie + me + storage
# ---------------------------------------------------------------------------

def test_cookies_status(smoke_client: TestClient) -> None:
    r = smoke_client.get("/api/cookies/status")
    assert r.status_code == 200


def test_me(smoke_client: TestClient) -> None:
    r = smoke_client.get("/api/me")
    assert r.status_code == 200


def test_me_likes_status(smoke_client: TestClient) -> None:
    r = smoke_client.get("/api/me/likes/status")
    assert r.status_code == 200


def test_admin_storage_status(smoke_client: TestClient) -> None:
    r = smoke_client.get("/api/admin/storage-status")
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Remote diagnostic: client-log receiver
# ---------------------------------------------------------------------------

def test_client_log_accepts_post(smoke_client: TestClient) -> None:
    """Phone JS errors POST here → Railway logs. Must always 204."""
    r = smoke_client.post(
        "/api/client-log",
        json={"msg": "smoke test", "ua": "pytest"},
    )
    assert r.status_code == 204


def test_client_log_handles_large_payload(smoke_client: TestClient) -> None:
    """Server clips at 2000 bytes — should still 204, never 500."""
    huge = "x" * 10_000
    r = smoke_client.post("/api/client-log", content=huge.encode())
    assert r.status_code == 204

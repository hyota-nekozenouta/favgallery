"""Tests for post favorite endpoints and listing payloads."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from favgallery.server import create_app


@pytest.fixture
def client(fake_library: Path) -> TestClient:
    return TestClient(create_app(library_root=fake_library, scan_in_background=False))


@pytest.mark.integration
def test_toggle_post_favorite_flips_state(client: TestClient) -> None:
    assert client.post("/api/posts/1001/1/favorite").json() == {"favorite": True}
    assert client.post("/api/posts/1001/1/favorite").json() == {"favorite": False}


@pytest.mark.integration
def test_posts_payload_marks_favorite(client: TestClient) -> None:
    client.post("/api/posts/1001/1/favorite")

    items = client.get("/api/posts").json()["items"]
    favorites = {
        (item["tweet_id"], item["num"]): item["is_favorite"] for item in items
    }
    assert favorites[("1001", 1)] is True
    assert all(value is False for key, value in favorites.items() if key != ("1001", 1))


@pytest.mark.integration
def test_posts_filter_by_favorite(client: TestClient) -> None:
    client.post("/api/posts/1001/1/favorite")

    favorites = client.get("/api/posts?favorite=1").json()
    assert favorites["total"] == 1
    assert [(item["tweet_id"], item["num"]) for item in favorites["items"]] == [
        ("1001", 1)
    ]

    all_posts = client.get("/api/posts").json()
    assert all_posts["total"] == 4


@pytest.mark.integration
def test_by_tweet_payload_carries_favorite(client: TestClient) -> None:
    """by-tweet も is_favorite を返すこと。

    回帰防止: ここが欠けるとライトボックス(複数枚ツイート)の★が常にグレーになり、
    タップで既存のお気に入りが黙って外れていた (2026-07-28)。"""
    client.post("/api/posts/1001/1/favorite?on=1")

    items = client.get("/api/posts/by-tweet/1001").json()["items"]
    assert items
    target = next(i for i in items if i["num"] == 1)
    assert target["is_favorite"] is True

    other = client.get("/api/posts/by-tweet/1002").json()["items"]
    assert all(i["is_favorite"] is False for i in other)


@pytest.mark.integration
def test_favorite_explicit_on_is_idempotent(client: TestClient) -> None:
    """on=1/0 は冪等. 表示ズレが原因でお気に入りを壊さないための保証."""
    assert client.post("/api/posts/1001/1/favorite?on=1").json() == {"favorite": True}
    assert client.post("/api/posts/1001/1/favorite?on=1").json() == {"favorite": True}
    assert client.get("/api/posts?favorite=1").json()["total"] == 1

    assert client.post("/api/posts/1001/1/favorite?on=0").json() == {"favorite": False}
    assert client.post("/api/posts/1001/1/favorite?on=0").json() == {"favorite": False}
    assert client.get("/api/posts?favorite=1").json()["total"] == 0


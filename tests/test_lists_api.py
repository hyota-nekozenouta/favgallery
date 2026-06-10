"""Tests for the /api/lists endpoints."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from favgallery.server import create_app


@pytest.fixture
def client(fake_library: Path) -> TestClient:
    return TestClient(create_app(library_root=fake_library, scan_in_background=False))


@pytest.mark.integration
def test_lists_empty_initially(client: TestClient) -> None:
    r = client.get("/api/lists")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.integration
def test_create_list_and_list(client: TestClient) -> None:
    r = client.post("/api/lists", json={"name": "favs"})
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "favs"
    rows = client.get("/api/lists").json()
    assert len(rows) == 1
    assert rows[0]["name"] == "favs"


@pytest.mark.integration
def test_create_list_rejects_blank(client: TestClient) -> None:
    r = client.post("/api/lists", json={"name": "   "})
    assert r.status_code == 400


@pytest.mark.integration
def test_add_and_remove_item(client: TestClient) -> None:
    lid = client.post("/api/lists", json={"name": "a"}).json()["id"]
    r = client.post(f"/api/lists/{lid}/items", json={"tweet_id": "1001", "num": 1})
    assert r.status_code == 200 and r.json()["added"] is True
    r2 = client.post(f"/api/lists/{lid}/items", json={"tweet_id": "1001", "num": 1})
    assert r2.json()["added"] is False  # idempotent
    rows = client.get("/api/lists").json()
    assert rows[0]["count"] == 1
    rd = client.delete(f"/api/lists/{lid}/items/1001/1")
    assert rd.json()["removed"] is True


@pytest.mark.integration
def test_post_lists_endpoint(client: TestClient) -> None:
    a = client.post("/api/lists", json={"name": "a"}).json()["id"]
    b = client.post("/api/lists", json={"name": "b"}).json()["id"]
    client.post(f"/api/lists/{a}/items", json={"tweet_id": "1001", "num": 1})
    client.post(f"/api/lists/{b}/items", json={"tweet_id": "1001", "num": 1})
    r = client.get("/api/posts/lists", params={"tweet_id": "1001", "num": 1})
    ids = sorted(r.json()["list_ids"])
    assert ids == sorted([a, b])


@pytest.mark.integration
def test_posts_filtered_by_list(client: TestClient) -> None:
    lid = client.post("/api/lists", json={"name": "a"}).json()["id"]
    client.post(f"/api/lists/{lid}/items", json={"tweet_id": "1001", "num": 1})
    r = client.get("/api/posts", params={"list": lid})
    data = r.json()
    assert data["total"] == 1
    assert data["items"][0]["tweet_id"] == "1001"


@pytest.mark.integration
def test_delete_list_cascades_items(client: TestClient) -> None:
    lid = client.post("/api/lists", json={"name": "a"}).json()["id"]
    client.post(f"/api/lists/{lid}/items", json={"tweet_id": "1001", "num": 1})
    rd = client.delete(f"/api/lists/{lid}")
    assert rd.json()["deleted"] is True
    r = client.get("/api/posts/lists", params={"tweet_id": "1001", "num": 1})
    assert r.json()["list_ids"] == []

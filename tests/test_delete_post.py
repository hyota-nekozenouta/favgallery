"""Tests for the DELETE /api/posts/{tweet_id}/{num} endpoint."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from xlikes_viewer.server import create_app


@pytest.fixture
def client(fake_library: Path) -> TestClient:
    return TestClient(create_app(library_root=fake_library, scan_in_background=False))


@pytest.mark.integration
def test_delete_removes_files(client: TestClient, fake_library: Path) -> None:
    media = fake_library / "alice" / "1001_1.jpg"
    sidecar = fake_library / "alice" / "1001_1.jpg.json"
    assert media.exists() and sidecar.exists()

    r = client.delete("/api/posts/1001/1")
    assert r.status_code == 200
    assert r.json() == {"deleted": True}
    assert not media.exists()
    assert not sidecar.exists()


@pytest.mark.integration
def test_delete_drops_post_from_index(client: TestClient) -> None:
    before = client.get("/api/posts").json()["total"]
    r = client.delete("/api/posts/1001/1")
    assert r.status_code == 200
    after = client.get("/api/posts").json()["total"]
    assert after == before - 1
    # Specific post is gone
    rows = client.get("/api/posts", params={"author": "alice"}).json()
    assert all(it["tweet_id"] != "1001" for it in rows["items"])


@pytest.mark.integration
def test_delete_removes_from_lists(client: TestClient) -> None:
    list_id = client.post("/api/lists", json={"name": "favs"}).json()["id"]
    client.post(f"/api/lists/{list_id}/items", json={"tweet_id": "1001", "num": 1})
    assert client.get("/api/posts/lists", params={"tweet_id": "1001", "num": 1}).json()[
        "list_ids"
    ] == [list_id]

    client.delete("/api/posts/1001/1")
    after = client.get("/api/posts/lists", params={"tweet_id": "1001", "num": 1}).json()
    assert after["list_ids"] == []


@pytest.mark.integration
def test_delete_unknown_post_returns_404(client: TestClient) -> None:
    r = client.delete("/api/posts/999999/1")
    assert r.status_code == 404


@pytest.mark.integration
def test_delete_does_not_touch_archive_db(client: TestClient, fake_library: Path) -> None:
    """Whatever lives next to the library beside our managed sqlite is not
    touched. (The real gallery-dl archive.sqlite is the protective entry that
    keeps the next sync from re-downloading; we only manage xlikes.sqlite.)"""
    archive = fake_library / "archive.sqlite"
    archive.write_bytes(b"fake archive")
    client.delete("/api/posts/1001/1")
    assert archive.exists()
    assert archive.read_bytes() == b"fake archive"

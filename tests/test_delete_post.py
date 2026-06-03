"""Tests for the DELETE /api/posts/{tweet_id}/{num} endpoint."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from xlikes_viewer.server import create_app


@pytest.fixture
def client(fake_library: Path) -> TestClient:
    return TestClient(create_app(library_root=fake_library, scan_in_background=False))


class _FakeR2:
    """Records delete_object calls so a delete can be asserted to purge R2."""

    def __init__(self) -> None:
        self.deleted: list[str] = []

    def delete_object(self, key: str) -> None:
        self.deleted.append(key)


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
def test_delete_does_not_resurrect_after_reingest(
    client: TestClient, fake_library: Path
) -> None:
    """A deleted post must stay gone even after the index re-ingests sidecars.

    Regression for the DB-index migration: if the sidecar survives the delete,
    the next _refresh_index() re-ingests it and the post comes back.
    """
    client.delete("/api/posts/1001/1")
    # Force another ingest+rebuild cycle (mirrors what restart / sync triggers).
    r = client.post("/api/library/refresh")
    assert r.status_code == 200
    after = client.get("/api/posts").json()
    assert all(it["tweet_id"] != "1001" for it in after["items"])


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


@pytest.mark.integration
def test_delete_purges_r2_object(fake_library: Path) -> None:
    """When R2 backs the media, delete must purge the R2 object too — otherwise
    the media (uploaded then deleted locally) lingers as an orphan in R2."""
    fake = _FakeR2()
    client = TestClient(
        create_app(library_root=fake_library, scan_in_background=False, r2_client=fake)
    )
    r = client.delete("/api/posts/1001/1")
    assert r.status_code == 200
    # R2 object key == media path relative to library_root, posix-style.
    assert fake.deleted == ["alice/1001_1.jpg"]


@pytest.mark.integration
def test_delete_unknown_does_not_touch_r2(fake_library: Path) -> None:
    """A 404 (no such post) must not issue any R2 deletion."""
    fake = _FakeR2()
    client = TestClient(
        create_app(library_root=fake_library, scan_in_background=False, r2_client=fake)
    )
    r = client.delete("/api/posts/999999/1")
    assert r.status_code == 404
    assert fake.deleted == []

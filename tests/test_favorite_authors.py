"""Characterization tests for /api/favorite-authors (previously untested)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from favgallery.server import create_app


@pytest.fixture
def client(fake_library: Path) -> TestClient:
    return TestClient(create_app(library_root=fake_library, scan_in_background=False))


@pytest.mark.integration
def test_favorite_authors_empty_by_default(client: TestClient) -> None:
    assert client.get("/api/favorite-authors").json() == []


@pytest.mark.integration
def test_favorite_authors_set_then_get_roundtrips(client: TestClient) -> None:
    r = client.post("/api/favorite-authors", json={"authors": ["alice", "bob"]})
    assert r.status_code == 200
    assert r.json() == {"saved": True}
    assert client.get("/api/favorite-authors").json() == ["alice", "bob"]

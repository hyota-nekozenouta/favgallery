"""Integration tests: book duplicate-skip on import + immutable cache headers."""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from favgallery.server import create_app


def _png_bytes(seed: int, size: int = 128) -> bytes:
    xv, yv = np.meshgrid(np.linspace(0, 1, size), np.linspace(0, 1, size))
    ang = seed * 0.6
    u = xv * np.cos(ang) + yv * np.sin(ang)
    r = 128 + 100 * np.sin(2 * np.pi * (u + 0.05 * seed))
    g = 128 + 100 * np.sin(2 * np.pi * (1.5 * u + 0.1 * seed))
    b = 128 + 100 * np.cos(2 * np.pi * (yv + 0.07 * seed))
    arr = np.stack([r, g, b], axis=-1).clip(0, 255).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr, "RGB").save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    lib = tmp_path / "library"
    lib.mkdir()
    app = create_app(library_root=lib, scan_in_background=False)
    return TestClient(app)


def _upload_book(client: TestClient, title: str, seeds: list[int]):
    files = [
        ("files", (f"{i:04d}.png", _png_bytes(s), "image/png"))
        for i, s in enumerate(seeds, start=1)
    ]
    return client.post("/api/books", data={"title": title}, files=files)


@pytest.mark.integration
def test_duplicate_multipart_upload_is_skipped(client: TestClient) -> None:
    r1 = _upload_book(client, "Doujin A", [1, 2, 3, 4, 5])
    assert r1.status_code == 201
    first_id = r1.json()["id"]

    r2 = _upload_book(client, "Doujin A (again)", [1, 2, 3, 4, 5])
    assert r2.status_code == 200
    body = r2.json()
    assert body.get("skipped") is True
    assert body.get("matched_book_id") == first_id

    books = client.get("/api/books").json()
    assert len(books) == 1


@pytest.mark.integration
def test_distinct_book_not_skipped(client: TestClient) -> None:
    assert _upload_book(client, "A", [1, 2, 3, 4, 5]).status_code == 201
    r2 = _upload_book(client, "B", [20, 21, 22, 23, 24])
    assert r2.status_code == 201
    assert r2.json().get("skipped") is None
    assert len(client.get("/api/books").json()) == 2


@pytest.mark.integration
def test_media_immutable_cache_headers_and_304(client: TestClient) -> None:
    r = _upload_book(client, "A", [1, 2, 3, 4, 5])
    assert r.status_code == 201
    book_id = r.json()["id"]
    rel = client.get(f"/api/books/{book_id}").json()["pages"][0]["rel_path"]

    m = client.get(f"/api/media/{rel}")
    assert m.status_code == 200
    assert "immutable" in m.headers.get("cache-control", "")
    etag = m.headers.get("etag")
    assert etag

    m2 = client.get(f"/api/media/{rel}", headers={"If-None-Match": etag})
    assert m2.status_code == 304


@pytest.mark.integration
def test_thumb_cache_headers_and_304(client: TestClient) -> None:
    r = _upload_book(client, "A", [1, 2, 3, 4, 5])
    book_id = r.json()["id"]
    rel = client.get(f"/api/books/{book_id}").json()["pages"][0]["rel_path"]

    t = client.get(f"/thumb/{rel}", params={"size": 200})
    assert t.status_code == 200
    assert "immutable" in t.headers.get("cache-control", "")
    etag = t.headers.get("etag")
    assert etag

    t2 = client.get(f"/thumb/{rel}", params={"size": 200}, headers={"If-None-Match": etag})
    assert t2.status_code == 304

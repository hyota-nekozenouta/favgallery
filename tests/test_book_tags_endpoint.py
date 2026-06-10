"""Endpoint tests for the bookshelf tag/favorite/patch/delete routes.

Regression focus: GET /api/books/tags must not be shadowed by the
/api/books/{book_id} (int) route. The original create_app registered the
parametrized route first, so a request to /api/books/tags was matched as
book_id="tags" and rejected with 422 before reaching api_book_tags.
"""

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
def test_get_book_tags_is_not_shadowed_by_book_id_route(client: TestClient) -> None:
    """GET /api/books/tags resolves to api_book_tags, not /api/books/{book_id}."""
    r = client.get("/api/books/tags")
    assert r.status_code == 200, f"expected 200, got {r.status_code}: {r.text}"
    assert r.json() == {"tags": []}


@pytest.mark.integration
def test_set_tags_then_listed_in_tag_index_and_on_book(client: TestClient) -> None:
    book_id = _upload_book(client, "Tagged", [1, 2, 3]).json()["id"]

    r = client.put(f"/api/books/{book_id}/tags", json={"tags": ["cat", "dog"]})
    assert r.status_code == 200
    assert r.json() == {"ok": True}

    # Tag index (the route that was previously 422-shadowed).
    tags = client.get("/api/books/tags").json()["tags"]
    by_name = {t["name"]: t["count"] for t in tags}
    assert by_name == {"cat": 1, "dog": 1}

    # Tags are echoed on the book listing.
    book = next(b for b in client.get("/api/books").json() if b["id"] == book_id)
    assert sorted(book["tags"]) == ["cat", "dog"]


@pytest.mark.integration
def test_toggle_book_favorite_flips_state(client: TestClient) -> None:
    book_id = _upload_book(client, "Fav", [1, 2, 3]).json()["id"]

    assert client.post(f"/api/books/{book_id}/favorite").json() == {"favorite": True}
    assert client.post(f"/api/books/{book_id}/favorite").json() == {"favorite": False}


@pytest.mark.integration
def test_patch_book_title(client: TestClient) -> None:
    book_id = _upload_book(client, "Old Title", [1, 2, 3]).json()["id"]

    r = client.patch(f"/api/books/{book_id}", json={"title": "New Title"})
    assert r.status_code == 200
    assert r.json()["title"] == "New Title"
    assert client.get(f"/api/books/{book_id}").json()["title"] == "New Title"


@pytest.mark.integration
def test_delete_book_removes_record_and_detail_404s(client: TestClient) -> None:
    book_id = _upload_book(client, "Doomed", [1, 2, 3]).json()["id"]

    assert client.delete(f"/api/books/{book_id}").json() == {"deleted": True}
    assert client.get("/api/books").json() == []
    assert client.get(f"/api/books/{book_id}").status_code == 404


@pytest.mark.integration
@pytest.mark.parametrize(
    "method,path,kwargs",
    [
        ("delete", "/api/books/999999", {}),
        ("patch", "/api/books/999999", {"json": {"title": "x"}}),
        ("put", "/api/books/999999/tags", {"json": {"tags": ["x"]}}),
        ("post", "/api/books/999999/favorite", {}),
    ],
)
def test_book_mutations_on_missing_book_return_404(
    client: TestClient, method: str, path: str, kwargs: dict
) -> None:
    r = getattr(client, method)(path, **kwargs)
    assert r.status_code == 404

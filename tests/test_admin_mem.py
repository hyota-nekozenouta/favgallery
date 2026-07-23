"""Memory measurement endpoint — Before/After effect of memory-reduction work.

`GET /api/admin/meminfo` is a read-only diagnostic surface: RSS + DB counts +
which heavy libraries are resident in this process + GC stats. Never 500s,
even on platforms where `/proc` or `malloc_trim` are unavailable (Windows).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from favgallery.server import create_app


@pytest.fixture
def mem_client(fake_library: Path) -> TestClient:
    app = create_app(library_root=fake_library, scan_in_background=False)
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.integration
def test_meminfo_returns_200_with_expected_shape(mem_client: TestClient) -> None:
    r = mem_client.get("/api/admin/meminfo")
    assert r.status_code == 200
    body = r.json()

    assert isinstance(body["posts_count"], int)
    assert body["posts_count"] >= 4  # fake_library seeds 4 posts

    assert isinstance(body["loaded_heavy"], dict)
    for name in ("gallery_dl", "numpy", "imagehash", "PIL", "scipy", "pywt"):
        assert name in body["loaded_heavy"]
        assert isinstance(body["loaded_heavy"][name], bool)

    assert isinstance(body["gc"], dict)
    assert "count" in body["gc"]
    assert "objects" in body["gc"]
    assert isinstance(body["gc"]["objects"], int)


@pytest.mark.integration
def test_meminfo_trim_query_never_500s(mem_client: TestClient) -> None:
    r = mem_client.get("/api/admin/meminfo?trim=1")
    assert r.status_code == 200
    body = r.json()
    assert "trim" in body
    # Windows / non-glibc: malloc_trim unavailable -> supported: false.
    assert isinstance(body["trim"], dict)


@pytest.mark.unit
def test_meminfo_tracemalloc_top_is_none_when_disabled(
    monkeypatch: pytest.MonkeyPatch, mem_client: TestClient
) -> None:
    monkeypatch.delenv("FAVGALLERY_TRACEMALLOC", raising=False)
    r = mem_client.get("/api/admin/meminfo")
    assert r.status_code == 200
    assert r.json()["tracemalloc_top"] is None

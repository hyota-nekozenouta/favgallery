"""Characterization tests for the /api/admin/* endpoints (previously untested)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from favgallery.server import create_app


@pytest.fixture
def client(fake_library: Path) -> TestClient:
    return TestClient(create_app(library_root=fake_library, scan_in_background=False))


@pytest.mark.integration
def test_cleanup_local_requires_r2(client: TestClient) -> None:
    r = client.post("/api/admin/cleanup-local")
    assert r.status_code == 503  # no R2 configured in tests


@pytest.mark.integration
def test_storage_status_reports_local_and_r2(client: TestClient) -> None:
    r = client.get("/api/admin/storage-status")
    assert r.status_code == 200
    body = r.json()
    assert body["r2_configured"] is False
    assert body["local_file_count"] >= 1
    assert body["local_size_bytes"] >= 0
    assert "local_size_mb" in body


@pytest.mark.integration
def test_reset_archive_db_deletes_then_reports_absent(
    client: TestClient, fake_library: Path
) -> None:
    archive = fake_library / "archive.sqlite"
    archive.write_bytes(b"fake archive")

    r1 = client.post("/api/admin/reset-archive-db")
    assert r1.status_code == 200
    assert r1.json()["deleted"] is True
    assert not archive.exists()

    r2 = client.post("/api/admin/reset-archive-db")
    assert r2.json()["deleted"] is False

"""Characterization tests for the /api/dedup/* endpoints (previously untested)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from xlikes_viewer.server import create_app


@pytest.fixture
def client(fake_library: Path) -> TestClient:
    return TestClient(create_app(library_root=fake_library, scan_in_background=False))


@pytest.mark.integration
def test_dedup_status_shape(client: TestClient) -> None:
    body = client.get("/api/dedup/status").json()
    for key in (
        "running",
        "files_total",
        "files_hashed",
        "duplicates_deleted",
        "bytes_freed",
        "lifetime_deleted",
    ):
        assert key in body


@pytest.mark.integration
def test_visual_dedup_status_shape(client: TestClient) -> None:
    body = client.get("/api/dedup/visual/status").json()
    for key in ("running", "files_total", "files_indexed", "duplicates_deleted"):
        assert key in body


@pytest.mark.integration
def test_dedup_run_starts_and_returns_started_flag(client: TestClient) -> None:
    r = client.post("/api/dedup/run")
    assert r.status_code in (200, 409)
    assert "started" in r.json()

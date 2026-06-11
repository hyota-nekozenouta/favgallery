"""Sensitive-file guard on the path-serving routes (/api/media, /media, /thumb).

cookies.txt (a full X account-takeover credential) and the SQLite databases live
INSIDE library_root by design (the Railway volume mount), which means the three
rel_path-serving routes could stream them verbatim to any Basic-auth'd client:
GET /api/media/cookies.txt etc. The guard lives in AppContext.validate_rel_path /
resolve_under_library so every current and future rel_path route inherits it.
Sensitive paths return 404 (not 403) so their existence is not advertised.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from favgallery.context import is_sensitive_name
from favgallery.server import create_app

VALID_COOKIES = (
    "# Netscape HTTP Cookie File\n"
    + "\t".join([".x.com", "TRUE", "/", "TRUE", "9999999999", "auth_token", "deadbeef"])
    + "\n"
)


@pytest.fixture
def client(fake_library: Path) -> TestClient:
    return TestClient(create_app(library_root=fake_library, scan_in_background=False))


# --- unit: the name predicate -------------------------------------------------


@pytest.mark.unit
def test_sensitive_names_blocked() -> None:
    assert is_sensitive_name("cookies.txt") is True
    assert is_sensitive_name("COOKIES.TXT") is True  # case-insensitive (FS may be)
    assert is_sensitive_name("xlikes.sqlite") is True
    assert is_sensitive_name("xlikes.sqlite-wal") is True  # sqlite sidecar
    assert is_sensitive_name("xlikes.sqlite-shm") is True
    assert is_sensitive_name("archive.sqlite") is True
    assert is_sensitive_name("library.db") is True
    assert is_sensitive_name(".cookies.abc123.tmp") is True  # atomic-write temp
    assert is_sensitive_name(".hidden") is True


@pytest.mark.unit
def test_media_names_allowed() -> None:
    assert is_sensitive_name("1001_1.jpg") is False
    assert is_sensitive_name("2001_1.png") is False
    assert is_sensitive_name("3001_1.mp4") is False
    assert is_sensitive_name("1001_1.jpg.json") is False  # metadata sidecar
    assert is_sensitive_name("0001.webp") is False


# --- integration: the three routes refuse to serve cookies.txt -----------------


@pytest.mark.integration
def test_api_media_does_not_serve_cookies(client: TestClient, fake_library: Path) -> None:
    client.post("/api/cookies", json={"content": VALID_COOKIES})
    assert (fake_library / "cookies.txt").exists()  # the file IS there...
    r = client.get("/api/media/cookies.txt")
    assert r.status_code == 404  # ...but must not be served
    assert "deadbeef" not in r.text


@pytest.mark.integration
def test_media_does_not_serve_cookies(client: TestClient, fake_library: Path) -> None:
    client.post("/api/cookies", json={"content": VALID_COOKIES})
    r = client.get("/media/cookies.txt")
    assert r.status_code == 404
    assert "deadbeef" not in r.text


@pytest.mark.integration
def test_thumb_does_not_serve_cookies(client: TestClient, fake_library: Path) -> None:
    client.post("/api/cookies", json={"content": VALID_COOKIES})
    r = client.get("/thumb/cookies.txt")
    assert r.status_code == 404
    assert "deadbeef" not in r.text


@pytest.mark.integration
def test_api_media_does_not_serve_database(client: TestClient, fake_library: Path) -> None:
    assert (fake_library / "xlikes.sqlite").exists()  # created by create_app
    r = client.get("/api/media/xlikes.sqlite")
    assert r.status_code == 404


# --- integration: legitimate media still serves --------------------------------


@pytest.mark.integration
def test_legit_media_still_served(client: TestClient) -> None:
    assert client.get("/api/media/alice/1001_1.jpg").status_code == 200
    assert client.get("/media/alice/1001_1.jpg").status_code == 200
    assert client.get("/thumb/alice/1001_1.jpg").status_code == 200


@pytest.mark.integration
def test_traversal_still_rejected(client: TestClient) -> None:
    r = client.get("/api/media/..%2F..%2Fetc%2Fpasswd")
    assert r.status_code in (400, 404)

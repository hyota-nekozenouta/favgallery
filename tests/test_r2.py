"""Tests for favgallery.r2 and R2-related server behaviour."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from favgallery.r2 import R2Client, R2Config, r2_config_from_env
from favgallery.server import create_app

# ---------------------------------------------------------------------------
# r2_config_from_env
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_r2_config_from_env_returns_none_when_vars_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for var in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET_NAME"):
        monkeypatch.delenv(var, raising=False)
    assert r2_config_from_env() is None


@pytest.mark.unit
def test_r2_config_from_env_returns_none_when_partial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("R2_ACCOUNT_ID", "abc")
    for var in ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET_NAME"):
        monkeypatch.delenv(var, raising=False)
    assert r2_config_from_env() is None


@pytest.mark.unit
def test_r2_config_from_env_returns_config_when_all_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("R2_ACCOUNT_ID", "myaccount")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "mykey")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "mysecret")
    monkeypatch.setenv("R2_BUCKET_NAME", "mybucket")
    cfg = r2_config_from_env()
    assert cfg is not None
    assert cfg.account_id == "myaccount"
    assert cfg.bucket_name == "mybucket"
    assert cfg.endpoint_url == "https://myaccount.r2.cloudflarestorage.com"


# ---------------------------------------------------------------------------
# R2Client (with mocked boto3)
# ---------------------------------------------------------------------------


def _make_r2_client() -> tuple[R2Client, MagicMock]:
    """Return (R2Client, mock_s3_client) with boto3 mocked out."""
    cfg = R2Config(
        account_id="acc",
        access_key_id="key",
        secret_access_key="secret",
        bucket_name="bucket",
    )
    mock_s3 = MagicMock()
    with patch("favgallery.r2.R2Client.__init__", lambda self, c: None):
        client = R2Client.__new__(R2Client)
        client._config = cfg  # type: ignore[attr-defined]
        client._client = mock_s3  # type: ignore[attr-defined]
        client._bucket = "bucket"  # type: ignore[attr-defined]
    return client, mock_s3


@pytest.mark.unit
def test_r2_client_upload_file(tmp_path: Path) -> None:
    client, mock_s3 = _make_r2_client()
    media = tmp_path / "img.jpg"
    media.write_bytes(b"\xff\xd8\xff")
    client.upload_file(media, "alice/1001_1.jpg")
    mock_s3.upload_file.assert_called_once_with(str(media), "bucket", "alice/1001_1.jpg")


@pytest.mark.unit
def test_r2_client_object_exists_true() -> None:
    client, mock_s3 = _make_r2_client()
    mock_s3.head_object.return_value = {}
    assert client.object_exists("alice/1001_1.jpg") is True
    mock_s3.head_object.assert_called_once_with(Bucket="bucket", Key="alice/1001_1.jpg")


@pytest.mark.unit
def test_r2_client_object_exists_false() -> None:
    client, mock_s3 = _make_r2_client()
    mock_s3.head_object.side_effect = Exception("NoSuchKey")
    assert client.object_exists("missing.jpg") is False


@pytest.mark.unit
def test_r2_client_stream_object() -> None:
    client, mock_s3 = _make_r2_client()
    mock_body = MagicMock()
    mock_body.iter_chunks.return_value = [b"chunk1", b"chunk2"]
    mock_s3.get_object.return_value = {
        "Body": mock_body,
        "ContentLength": 12,
        "ContentType": "image/jpeg",
    }
    length, ctype, it = client.stream_object("alice/1001_1.jpg")
    assert length == 12
    assert ctype == "image/jpeg"
    assert list(it) == [b"chunk1", b"chunk2"]


@pytest.mark.unit
def test_r2_client_generate_presigned_get_url() -> None:
    client, mock_s3 = _make_r2_client()
    mock_s3.generate_presigned_url.return_value = (
        "https://acc.r2.cloudflarestorage.com/bucket/alice/1001_1.jpg?X-Amz-Signature=abc"
    )
    url = client.generate_presigned_get_url("alice/1001_1.jpg")
    assert "X-Amz-Signature" in url
    mock_s3.generate_presigned_url.assert_called_once_with(
        "get_object",
        Params={"Bucket": "bucket", "Key": "alice/1001_1.jpg"},
        ExpiresIn=600,
    )


@pytest.mark.unit
def test_r2_client_generate_presigned_get_url_custom_ttl() -> None:
    client, mock_s3 = _make_r2_client()
    mock_s3.generate_presigned_url.return_value = "https://example/signed"
    client.generate_presigned_get_url("alice/1001_1.jpg", ttl_seconds=120)
    mock_s3.generate_presigned_url.assert_called_once_with(
        "get_object",
        Params={"Bucket": "bucket", "Key": "alice/1001_1.jpg"},
        ExpiresIn=120,
    )


@pytest.mark.unit
def test_r2_client_raises_import_error_without_boto3() -> None:
    cfg = R2Config(
        account_id="a", access_key_id="k", secret_access_key="s", bucket_name="b"
    )
    with patch.dict("sys.modules", {"boto3": None}), pytest.raises(ImportError, match="boto3"):
        R2Client(cfg)


# ---------------------------------------------------------------------------
# /api/media endpoint — local fallback (R2 not configured)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_api_media_serves_local_file_when_r2_not_configured(
    monkeypatch: pytest.MonkeyPatch, fake_library: Path
) -> None:
    for var in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET_NAME"):
        monkeypatch.delenv(var, raising=False)
    app = create_app(library_root=fake_library, scan_in_background=False)
    client = TestClient(app)
    posts = client.get("/api/posts").json()["items"]
    media_url = posts[0]["media_url"]
    assert media_url.startswith("/api/media/")
    r = client.get(media_url)
    assert r.status_code == 200


@pytest.mark.integration
def test_api_media_rejects_path_escape(
    monkeypatch: pytest.MonkeyPatch, fake_library: Path
) -> None:
    for var in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET_NAME"):
        monkeypatch.delenv(var, raising=False)
    app = create_app(library_root=fake_library, scan_in_background=False)
    client = TestClient(app)
    r = client.get("/api/media/../../etc/passwd")
    assert r.status_code in (400, 404)


# ---------------------------------------------------------------------------
# /api/media endpoint — R2 path
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_api_media_redirects_to_r2_presigned(
    monkeypatch: pytest.MonkeyPatch, fake_library: Path
) -> None:
    """R2 path returns 302 → presigned URL so bytes never traverse Railway."""
    monkeypatch.setenv("R2_ACCOUNT_ID", "acc")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "key")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("R2_BUCKET_NAME", "bucket")

    presigned_url = (
        "https://acc.r2.cloudflarestorage.com/bucket/alice/1001_1.jpg"
        "?X-Amz-Signature=abc&X-Amz-Expires=600"
    )
    mock_r2 = MagicMock()
    mock_r2.generate_presigned_get_url.return_value = presigned_url

    with patch("favgallery.server.R2Client", return_value=mock_r2):
        app = create_app(library_root=fake_library, scan_in_background=False)
    client = TestClient(app)

    posts = client.get("/api/posts").json()["items"]
    media_url = posts[0]["media_url"]
    r = client.get(media_url, follow_redirects=False)

    assert r.status_code == 302
    assert r.headers["location"] == presigned_url
    # `private` (not `public`) so CF edge does NOT cache the 302.
    assert r.headers["cache-control"] == "private, max-age=300"
    # ETag preserved so the next request can short-circuit at 304.
    assert r.headers.get("etag")
    mock_r2.generate_presigned_get_url.assert_called_once()


@pytest.mark.integration
def test_api_media_falls_back_to_local_when_r2_raises(
    monkeypatch: pytest.MonkeyPatch, fake_library: Path
) -> None:
    """If presigned issuance raises, fall back to local filesystem (200)."""
    monkeypatch.setenv("R2_ACCOUNT_ID", "acc")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "key")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("R2_BUCKET_NAME", "bucket")

    mock_r2 = MagicMock()
    mock_r2.generate_presigned_get_url.side_effect = Exception("AccessDenied")

    with patch("favgallery.server.R2Client", return_value=mock_r2):
        app = create_app(library_root=fake_library, scan_in_background=False)
    client = TestClient(app)

    posts = client.get("/api/posts").json()["items"]
    media_url = posts[0]["media_url"]
    r = client.get(media_url)
    # Local file exists, so fallback must succeed.
    assert r.status_code == 200


@pytest.mark.integration
def test_api_media_304_short_circuit_skips_r2_call(
    monkeypatch: pytest.MonkeyPatch, fake_library: Path
) -> None:
    """If-None-Match short-circuit must NOT issue a presigned URL (strongest opt)."""
    monkeypatch.setenv("R2_ACCOUNT_ID", "acc")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "key")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("R2_BUCKET_NAME", "bucket")

    mock_r2 = MagicMock()
    mock_r2.generate_presigned_get_url.return_value = "https://example/signed"

    with patch("favgallery.server.R2Client", return_value=mock_r2):
        app = create_app(library_root=fake_library, scan_in_background=False)
    client = TestClient(app)

    posts = client.get("/api/posts").json()["items"]
    media_url = posts[0]["media_url"]
    # First request: pick up the ETag.
    first = client.get(media_url, follow_redirects=False)
    etag = first.headers["etag"]

    # Reset the mock so the assert below is unambiguous.
    mock_r2.generate_presigned_get_url.reset_mock()

    r = client.get(media_url, headers={"If-None-Match": etag}, follow_redirects=False)
    assert r.status_code == 304
    mock_r2.generate_presigned_get_url.assert_not_called()

"""Tests for the in-app cookie management endpoints + env seed-once behaviour.

Covers the fix for "いいね/新しい投稿の更新ができない" — root cause was that the
deployed container had no cookies.txt (GALLERY_DL_COOKIES env unset and no way to
provision cookies from the running web app). These endpoints let cookies be set /
updated / verified from the UI, persisted to the volume.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from favgallery.routers import cookies as cookies_router
from favgallery.server import _write_cookies_from_env, create_app

# A minimal but realistic Netscape-format cookies.txt with the X auth cookie.
VALID_COOKIES = (
    "# Netscape HTTP Cookie File\n"
    + "\t".join([".x.com", "TRUE", "/", "TRUE", "9999999999", "auth_token", "deadbeef"])
    + "\n"
    + "\t".join([".x.com", "TRUE", "/", "TRUE", "9999999999", "ct0", "abc123"])
    + "\n"
)


@pytest.fixture
def client(fake_library: Path) -> TestClient:
    return TestClient(create_app(library_root=fake_library, scan_in_background=False))


def _cookies_path(fake_library: Path) -> Path:
    # server.py: cookies_file = library_root / "cookies.txt" (inside the volume)
    return fake_library / "cookies.txt"


# --- GET /api/cookies/status -------------------------------------------------


@pytest.mark.integration
def test_status_unset_when_no_file(client: TestClient) -> None:
    r = client.get("/api/cookies/status")
    assert r.status_code == 200
    d = r.json()
    assert d["configured"] is False


@pytest.mark.integration
def test_status_configured_after_post(client: TestClient, fake_library: Path) -> None:
    client.post("/api/cookies", json={"content": VALID_COOKIES})
    d = client.get("/api/cookies/status").json()
    assert d["configured"] is True
    assert d["looks_valid"] is True
    assert d["size"] > 0
    assert d["updated_at"] is not None


# --- POST /api/cookies -------------------------------------------------------


@pytest.mark.integration
def test_post_writes_file_to_volume(client: TestClient, fake_library: Path) -> None:
    r = client.post("/api/cookies", json={"content": VALID_COOKIES})
    assert r.status_code == 200
    saved = _cookies_path(fake_library)
    assert saved.exists()
    assert "auth_token" in saved.read_text(encoding="utf-8")


@pytest.mark.integration
def test_post_rejects_empty(client: TestClient) -> None:
    r = client.post("/api/cookies", json={"content": "   \n  "})
    assert r.status_code == 400


@pytest.mark.integration
def test_post_rejects_non_cookie_garbage(client: TestClient) -> None:
    r = client.post("/api/cookies", json={"content": "this is not a cookies file"})
    assert r.status_code == 400


@pytest.mark.integration
def test_status_never_leaks_cookie_content(client: TestClient) -> None:
    client.post("/api/cookies", json={"content": VALID_COOKIES})
    body = client.get("/api/cookies/status").text
    # The secret cookie values must never appear in the status response.
    assert "deadbeef" not in body
    assert "abc123" not in body


# --- _write_cookies_from_env: seed-once (docstring said "preserved") ---------


def test_env_seeds_cookies_when_file_absent(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GALLERY_DL_COOKIES", "# from env\n")
    target = tmp_path / "cookies.txt"
    assert not target.exists()
    _write_cookies_from_env(target)
    assert target.read_text(encoding="utf-8") == "# from env\n"


def test_env_does_not_overwrite_existing_cookies(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GALLERY_DL_COOKIES", "# from env\n")
    target = tmp_path / "cookies.txt"
    target.write_text("# user-set via UI\n", encoding="utf-8")
    _write_cookies_from_env(target)
    # Existing file (e.g. set via the in-app UI) must survive a restart.
    assert target.read_text(encoding="utf-8") == "# user-set via UI\n"


# --- POST /api/cookies/verify ------------------------------------------------


@pytest.mark.integration
def test_verify_ok_when_probe_returns_ids(
    client: TestClient, monkeypatch
) -> None:
    client.post("/api/cookies", json={"content": VALID_COOKIES})
    client.post("/api/me", json={"username": "hyota"})
    monkeypatch.setattr(
        cookies_router, "fetch_my_liked_tweet_ids", lambda *a, **k: ["1234567890"]
    )
    d = client.post("/api/cookies/verify").json()
    assert d["ok"] is True
    assert d["auth_error"] is False


@pytest.mark.integration
def test_verify_flags_auth_error(client: TestClient, monkeypatch) -> None:
    client.post("/api/cookies", json={"content": VALID_COOKIES})
    client.post("/api/me", json={"username": "hyota"})

    def _boom(*a, **k):
        raise RuntimeError("AuthRequired: Login required")

    monkeypatch.setattr(cookies_router, "fetch_my_liked_tweet_ids", _boom)
    d = client.post("/api/cookies/verify").json()
    assert d["ok"] is False
    assert d["auth_error"] is True


@pytest.mark.integration
def test_verify_reports_when_username_missing(client: TestClient) -> None:
    client.post("/api/cookies", json={"content": VALID_COOKIES})
    # no /api/me username set
    d = client.post("/api/cookies/verify").json()
    assert d["ok"] is False
    assert d["auth_error"] is False
    assert d["message"]


@pytest.mark.integration
def test_verify_reports_rate_limit_without_auth_nag(
    client: TestClient, monkeypatch
) -> None:
    """X 側レート制限は「cookie 失効」と区別して伝える (2026-06-10 接続テスト無反応 bug)。"""
    client.post("/api/cookies", json={"content": VALID_COOKIES})
    client.post("/api/me", json={"username": "hyota"})

    def _limited(*a, **k):
        raise RuntimeError("AbortExtraction: '429 Too Many Requests' rate limit reached")

    monkeypatch.setattr(cookies_router, "fetch_my_liked_tweet_ids", _limited)
    d = client.post("/api/cookies/verify").json()
    assert d["ok"] is False
    assert d["auth_error"] is False
    assert "レート制限" in d["message"]


@pytest.mark.integration
def test_verify_returns_busy_when_gdl_lock_held(
    client: TestClient, monkeypatch
) -> None:
    """同期/タイムライン実行中はロック待ちで固まらず即「実行中」を返す。"""
    client.post("/api/cookies", json={"content": VALID_COOKIES})
    client.post("/api/me", json={"username": "hyota"})
    monkeypatch.setattr(cookies_router, "_GDL_LOCK_TIMEOUT_S", 0.2)
    ctx = client.app.state.context
    assert ctx.gdl_lock.acquire(blocking=False)
    try:
        d = client.post("/api/cookies/verify").json()
    finally:
        ctx.gdl_lock.release()
    assert d["ok"] is False
    assert d["auth_error"] is False
    assert "実行中" in d["message"]


def test_prepare_config_fast_fail_aborts_on_rate_limit(tmp_path: Path) -> None:
    """fast_fail=True で gallery-dl がレート制限を「待たずに中断」する設定になる。"""
    from favgallery.gallerydl import prepare_config

    cfg_file = tmp_path / "gallery-dl.json"
    cfg_file.write_text("{}", encoding="utf-8")
    gdl_config = prepare_config(cfg_file, fast_fail=True)
    assert gdl_config.get(("extractor", "twitter"), "ratelimit") == "abort"
    assert gdl_config.get(("extractor",), "retries") == 1
    assert gdl_config.get(("extractor",), "timeout") == 10.0


def test_prepare_config_does_not_leak_overrides_between_calls(tmp_path: Path) -> None:
    """gallery-dl の global config はマージ式 — 前回呼び出しの file-range / fast_fail が
    次の呼び出し (例: 同期) に残留しないこと (2026-06-10 発見の潜在バグ根治)。

    残留すると verify の file-range "1-1" が次の同期に漏れ、同期が 1 件しか
    取得しなくなる。
    """
    from favgallery.gallerydl import prepare_config

    cfg_file = tmp_path / "gallery-dl.json"
    cfg_file.write_text("{}", encoding="utf-8")
    prepare_config(cfg_file, file_range="1-1", fast_fail=True)
    gdl_config = prepare_config(cfg_file)  # 同期相当: range/fast_fail 指定なし
    assert gdl_config.get(("extractor",), "file-range") is None
    assert gdl_config.get(("extractor", "twitter"), "ratelimit") is None
    assert gdl_config.get(("extractor",), "retries") is None

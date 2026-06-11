"""Tests for favgallery.server using FastAPI's TestClient."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from favgallery.server import create_app
from tests.conftest import _write_post

# ---------------------------------------------------------------------------
# Basic auth tests
# ---------------------------------------------------------------------------

def _basic_header(user: str, password: str) -> str:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return f"Basic {token}"


@pytest.mark.integration
def test_basic_auth_returns_401_when_configured(
    monkeypatch: pytest.MonkeyPatch, fake_library: Path
) -> None:
    monkeypatch.setenv("ARCHIVE_USER", "testuser")
    monkeypatch.setenv("ARCHIVE_PASSWORD", "testpass")
    app = create_app(library_root=fake_library, scan_in_background=False)
    client = TestClient(app, raise_server_exceptions=False)
    r = client.get("/", headers={})
    assert r.status_code == 401
    assert "Basic" in r.headers.get("WWW-Authenticate", "")


@pytest.mark.integration
def test_basic_auth_accepts_valid_credentials(
    monkeypatch: pytest.MonkeyPatch, fake_library: Path
) -> None:
    monkeypatch.setenv("ARCHIVE_USER", "testuser")
    monkeypatch.setenv("ARCHIVE_PASSWORD", "testpass")
    app = create_app(library_root=fake_library, scan_in_background=False)
    client = TestClient(app, raise_server_exceptions=False)
    r = client.get("/", headers={"Authorization": _basic_header("testuser", "testpass")})
    assert r.status_code == 200


@pytest.mark.integration
def test_basic_auth_rejects_wrong_password(
    monkeypatch: pytest.MonkeyPatch, fake_library: Path
) -> None:
    monkeypatch.setenv("ARCHIVE_USER", "testuser")
    monkeypatch.setenv("ARCHIVE_PASSWORD", "testpass")
    app = create_app(library_root=fake_library, scan_in_background=False)
    client = TestClient(app, raise_server_exceptions=False)
    r = client.get("/", headers={"Authorization": _basic_header("testuser", "wrongpass")})
    assert r.status_code == 401


@pytest.mark.integration
def test_basic_auth_disabled_when_env_vars_absent(
    monkeypatch: pytest.MonkeyPatch, fake_library: Path
) -> None:
    monkeypatch.delenv("ARCHIVE_USER", raising=False)
    monkeypatch.delenv("ARCHIVE_PASSWORD", raising=False)
    app = create_app(library_root=fake_library, scan_in_background=False)
    client = TestClient(app, raise_server_exceptions=False)
    # No Authorization header — should still succeed (dev / local mode)
    r = client.get("/")
    assert r.status_code == 200


@pytest.mark.integration
def test_basic_auth_accepts_favgallery_env_vars(
    monkeypatch: pytest.MonkeyPatch, fake_library: Path
) -> None:
    """New FAVGALLERY_* env vars configure Basic auth (rename, 2026-06-10)."""
    monkeypatch.delenv("ARCHIVE_USER", raising=False)
    monkeypatch.delenv("ARCHIVE_PASSWORD", raising=False)
    monkeypatch.setenv("FAVGALLERY_USER", "newuser")
    monkeypatch.setenv("FAVGALLERY_PASSWORD", "newpass")
    app = create_app(library_root=fake_library, scan_in_background=False)
    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/").status_code == 401
    r = client.get("/", headers={"Authorization": _basic_header("newuser", "newpass")})
    assert r.status_code == 200


@pytest.mark.integration
def test_basic_auth_favgallery_env_wins_over_archive_env(
    monkeypatch: pytest.MonkeyPatch, fake_library: Path
) -> None:
    """When both old and new env vars are set, FAVGALLERY_* takes precedence."""
    monkeypatch.setenv("ARCHIVE_USER", "olduser")
    monkeypatch.setenv("ARCHIVE_PASSWORD", "oldpass")
    monkeypatch.setenv("FAVGALLERY_USER", "newuser")
    monkeypatch.setenv("FAVGALLERY_PASSWORD", "newpass")
    app = create_app(library_root=fake_library, scan_in_background=False)
    client = TestClient(app, raise_server_exceptions=False)
    r = client.get("/", headers={"Authorization": _basic_header("newuser", "newpass")})
    assert r.status_code == 200
    r_old = client.get("/", headers={"Authorization": _basic_header("olduser", "oldpass")})
    assert r_old.status_code == 401


@pytest.mark.integration
def test_basic_auth_mixed_old_new_env_pair(
    monkeypatch: pytest.MonkeyPatch, fake_library: Path
) -> None:
    """Mid-migration state: each var resolves independently, so a new USER
    paired with a legacy PASSWORD still configures auth correctly."""
    monkeypatch.delenv("ARCHIVE_USER", raising=False)
    monkeypatch.delenv("FAVGALLERY_PASSWORD", raising=False)
    monkeypatch.setenv("FAVGALLERY_USER", "mixeduser")
    monkeypatch.setenv("ARCHIVE_PASSWORD", "mixedpass")
    app = create_app(library_root=fake_library, scan_in_background=False)
    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/").status_code == 401
    r = client.get("/", headers={"Authorization": _basic_header("mixeduser", "mixedpass")})
    assert r.status_code == 200


@pytest.mark.unit
def test_env_first_returns_first_non_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    from favgallery.server import _env_first

    monkeypatch.delenv("FAVGALLERY_TEST_A", raising=False)
    monkeypatch.setenv("FAVGALLERY_TEST_B", "fallback-value")
    assert _env_first("FAVGALLERY_TEST_A", "FAVGALLERY_TEST_B") == "fallback-value"
    monkeypatch.setenv("FAVGALLERY_TEST_A", "primary-value")
    assert _env_first("FAVGALLERY_TEST_A", "FAVGALLERY_TEST_B") == "primary-value"
    monkeypatch.delenv("FAVGALLERY_TEST_A", raising=False)
    monkeypatch.delenv("FAVGALLERY_TEST_B", raising=False)
    assert _env_first("FAVGALLERY_TEST_A", "FAVGALLERY_TEST_B") == ""


@pytest.fixture
def client(fake_library: Path) -> TestClient:
    app = create_app(library_root=fake_library, scan_in_background=False)
    return TestClient(app)


@pytest.mark.integration
def test_index_html_served(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert "favgallery" in r.text.lower()


@pytest.mark.integration
def test_index_injects_app_version(client: TestClient) -> None:
    """__APP_VERSION__ プレースホルダは配信時に実バージョンへ置換される
    (端末側にどの版が出ているか表示する遠隔診断 / 2026-06-10)。
    __ASSET_VERSION__ も同時に置換され、生プレースホルダが残らないこと。"""
    from favgallery.server import APP_VERSION

    r = client.get("/")
    assert "__APP_VERSION__" not in r.text
    assert "__ASSET_VERSION__" not in r.text
    assert f"v{APP_VERSION}" in r.text


@pytest.mark.integration
def test_asset_version_decoupled_from_app_version() -> None:
    """キャッシュバスト用 ASSET_VERSION は app version と独立したコンテンツハッシュ。
    純粋リファクタで版を上げずにキャッシュを更新するための分離 (2026-06-11
    ひょーたさん「バージョンバンプはミス」)。"""
    from favgallery.server import APP_VERSION, ASSET_VERSION

    # 12 桁の 16 進トークン
    assert len(ASSET_VERSION) == 12
    assert all(c in "0123456789abcdef" for c in ASSET_VERSION)
    # 版表示 (例 "0.4.3" / "dev") とは別物 = 分離できている
    assert ASSET_VERSION != APP_VERSION


@pytest.mark.integration
def test_client_log_endpoint_records_and_returns_204(client: TestClient) -> None:
    r = client.post("/api/client-log", json={"kind": "error", "message": "boom"})
    assert r.status_code == 204


@pytest.mark.integration
def test_index_is_never_cached(client: TestClient) -> None:
    """SPA シェルはキャッシュ無効で配信 — デプロイ後にスマホが古い JS を
    使い回して「直したのに挙動が変わらない」が再発しないこと (2026-06-10)。"""
    r = client.get("/")
    assert r.headers.get("Cache-Control") == "no-cache"


@pytest.mark.integration
def test_all_responses_carry_app_version_header(
    monkeypatch: pytest.MonkeyPatch, fake_library: Path
) -> None:
    """X-App-Version を全応答に付与 — 認証前の 401 にも付くので、本番にどの
    版が出ているか外部から確認できる (デプロイ検証の盲点解消 / 2026-06-10)。"""
    from favgallery.server import APP_VERSION

    assert APP_VERSION and APP_VERSION != ""
    monkeypatch.setenv("FAVGALLERY_USER", "u")
    monkeypatch.setenv("FAVGALLERY_PASSWORD", "p")
    app = create_app(library_root=fake_library, scan_in_background=False)
    client = TestClient(app, raise_server_exceptions=False)
    r401 = client.get("/")
    assert r401.status_code == 401
    assert r401.headers.get("X-App-Version") == APP_VERSION
    r200 = client.get("/", headers={"Authorization": _basic_header("u", "p")})
    assert r200.status_code == 200
    assert r200.headers.get("X-App-Version") == APP_VERSION


@pytest.mark.integration
def test_library_endpoint_lists_authors_and_tags(client: TestClient) -> None:
    r = client.get("/api/library")
    assert r.status_code == 200
    data = r.json()
    assert data["post_count"] == 4
    names = [a["name"] for a in data["authors"]]
    assert "alice" in names
    assert "carol" in names
    tag_names = [t["name"] for t in data["tags"]]
    assert "cat" in tag_names


@pytest.mark.integration
def test_posts_endpoint_returns_paginated(client: TestClient) -> None:
    r = client.get("/api/posts", params={"limit": 2})
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 4
    assert len(data["items"]) == 2
    item = data["items"][0]
    assert item["media_url"].startswith("/api/media/")
    assert item["thumb_url"].startswith("/thumb/")
    assert item["tweet_url"].startswith("https://x.com/")


@pytest.mark.integration
def test_posts_filter_by_author(client: TestClient) -> None:
    r = client.get("/api/posts", params={"author": "alice"})
    data = r.json()
    assert data["total"] == 2
    assert all(it["author_name"] == "alice" for it in data["items"])


@pytest.mark.integration
def test_posts_filter_by_tag(client: TestClient) -> None:
    r = client.get("/api/posts", params={"tag": "cat"})
    data = r.json()
    assert data["total"] == 2


@pytest.mark.integration
def test_posts_filter_combines_author_and_tag(client: TestClient) -> None:
    r = client.get("/api/posts", params={"author": "alice", "tag": "pixiv"})
    data = r.json()
    assert data["total"] == 1


@pytest.mark.integration
def test_media_endpoint_serves_file(client: TestClient) -> None:
    posts = client.get("/api/posts").json()["items"]
    media_url = posts[0]["media_url"]
    assert media_url.startswith("/api/media/")
    r = client.get(media_url)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith(("image/", "video/", "application/"))


@pytest.mark.integration
def test_media_endpoint_rejects_path_escape(client: TestClient) -> None:
    r = client.get("/api/media/../../etc/passwd")
    assert r.status_code in (400, 404)


@pytest.mark.integration
def test_thumb_endpoint_returns_jpeg_for_image(client: TestClient) -> None:
    posts = client.get("/api/posts", params={"media_type": "photo"}).json()["items"]
    rel = posts[0]["thumb_url"].removeprefix("/thumb/")
    r = client.get(f"/thumb/{rel}")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"


@pytest.mark.integration
def test_sync_status_reports_exe_presence(client: TestClient) -> None:
    r = client.get("/api/sync/status")
    assert r.status_code == 200
    data = r.json()
    assert "running" in data
    assert "exe_present" in data


@pytest.mark.integration
def test_sync_status_exposes_added_and_auth_error(client: TestClient) -> None:
    data = client.get("/api/sync/status").json()
    # The UI needs these to distinguish "cookies expired" from "nothing new".
    assert "last_added" in data
    assert "auth_error" in data
    assert data["auth_error"] is False  # fresh app: no failure yet


@pytest.mark.integration
def test_timeline_status_exposes_auth_error(client: TestClient) -> None:
    data = client.get("/api/timeline/status").json()
    assert "last_added" in data
    assert "auth_error" in data
    assert data["auth_error"] is False


@pytest.mark.integration
def test_library_refresh_works(client: TestClient) -> None:
    r = client.post("/api/library/refresh")
    assert r.status_code == 200
    assert r.json()["post_count"] == 4


@pytest.mark.integration
def test_posts_by_tweet_returns_all_nums_in_order(fake_library: Path) -> None:
    # Add a multi-image tweet (insert out of order to verify sorting).
    for n in (3, 1, 2):
        _write_post(
            fake_library,
            author="dave",
            nick="デーブ",
            tweet_id=4001,
            num=n,
            extension="jpg",
            content="multi",
            date="2025-12-27 10:00:00",
        )
    app = create_app(library_root=fake_library, scan_in_background=False)
    client = TestClient(app)
    r = client.get("/api/posts/by-tweet/4001")
    assert r.status_code == 200
    items = r.json()["items"]
    assert [it["num"] for it in items] == [1, 2, 3]
    assert all(it["tweet_id"] == "4001" for it in items)
    assert all(it["author_name"] == "dave" for it in items)


@pytest.mark.integration
def test_posts_by_tweet_unknown_returns_empty(client: TestClient) -> None:
    r = client.get("/api/posts/by-tweet/9999999999")
    assert r.status_code == 200
    assert r.json() == {"items": []}


@pytest.mark.integration
def test_author_summary_known(client: TestClient) -> None:
    r = client.get("/api/authors/alice/summary")
    assert r.status_code == 200
    data = r.json()
    assert data["author"] == "alice"
    assert data["nick"] == "アリス"
    assert data["counts"]["total"] == 2
    assert data["counts"]["photo"] == 2


@pytest.mark.integration
def test_author_summary_unknown_returns_empty_counts(client: TestClient) -> None:
    r = client.get("/api/authors/no_such_user/summary")
    assert r.status_code == 200
    data = r.json()
    assert data["author"] == "no_such_user"
    assert data["nick"] == ""
    assert data["counts"] == {"total": 0}


@pytest.mark.integration
def test_author_summary_video(client: TestClient) -> None:
    r = client.get("/api/authors/carol/summary")
    data = r.json()
    assert data["counts"]["total"] == 1
    assert data["counts"]["video"] == 1


@pytest.mark.integration
def test_author_unliked_filters_local_archive(
    monkeypatch: pytest.MonkeyPatch, fake_library: Path
) -> None:
    from favgallery.db import TimelinePost

    def make(tweet_id: str) -> TimelinePost:
        return TimelinePost(
            tweet_id=tweet_id, num=1, fetched_at=0, date="2026-01-01 00:00:00",
            author_name="alice", author_nick="アリス", author_avatar_url="",
            content="x", media_url=f"https://pbs.twimg.com/media/{tweet_id}.jpg",
            thumb_url=f"https://pbs.twimg.com/media/{tweet_id}.jpg?name=small",
            media_type="photo", width=100, height=100,
            favorite_count=0, view_count=0, hashtags=(),
        )

    # tweet_id 1001 already exists in fake_library; 9999 is brand new.
    fake_posts = [make("1001"), make("9999")]

    def fake_fetch(_cfg: object, author: str, *, range_spec: str) -> list[TimelinePost]:
        assert author == "alice"
        return fake_posts

    monkeypatch.setattr("favgallery.routers.posts.fetch_author_media_posts", fake_fetch)
    app = create_app(library_root=fake_library, scan_in_background=False)
    client = TestClient(app)
    r = client.get("/api/authors/alice/unliked")
    assert r.status_code == 200
    data = r.json()
    assert data["author"] == "alice"
    assert data["fetched"] == 2
    assert len(data["items"]) == 1
    assert data["items"][0]["tweet_id"] == "9999"


@pytest.mark.integration
def test_author_unliked_propagates_gallerydl_failure(
    monkeypatch: pytest.MonkeyPatch, fake_library: Path
) -> None:

    def boom(*_a: object, **_kw: object) -> None:
        raise RuntimeError("auth missing")

    monkeypatch.setattr("favgallery.routers.posts.fetch_author_media_posts", boom)
    app = create_app(library_root=fake_library, scan_in_background=False)
    client = TestClient(app)
    r = client.get("/api/authors/alice/unliked")
    assert r.status_code == 502
    assert "auth missing" in r.json()["detail"]


@pytest.mark.integration
def test_author_unliked_filters_x_side_favorited(
    monkeypatch: pytest.MonkeyPatch, fake_library: Path
) -> None:
    from favgallery.db import TimelinePost

    def make(tweet_id: str, *, favorited: bool) -> TimelinePost:
        return TimelinePost(
            tweet_id=tweet_id, num=1, fetched_at=0, date="2026-01-01 00:00:00",
            author_name="alice", author_nick="アリス", author_avatar_url="",
            content="x", media_url=f"https://pbs.twimg.com/media/{tweet_id}.jpg",
            thumb_url=f"https://pbs.twimg.com/media/{tweet_id}.jpg?name=small",
            media_type="photo", width=100, height=100,
            favorite_count=0, view_count=0, hashtags=(),
            favorited=favorited,
        )

    fake_posts = [
        make("8001", favorited=True),   # already liked on X — filter out
        make("8002", favorited=False),  # truly unliked — keep
    ]

    def fake_fetch(_cfg: object, _author: str, *, range_spec: str) -> list[TimelinePost]:
        return fake_posts

    monkeypatch.setattr("favgallery.routers.posts.fetch_author_media_posts", fake_fetch)
    app = create_app(library_root=fake_library, scan_in_background=False)
    client = TestClient(app)
    r = client.get("/api/authors/alice/unliked")
    assert r.status_code == 200
    data = r.json()
    assert [it["tweet_id"] for it in data["items"]] == ["8002"]


@pytest.mark.integration
def test_author_unliked_pagination_offset(
    monkeypatch: pytest.MonkeyPatch, fake_library: Path
) -> None:
    """Offset must translate into a 1-based gallery-dl range."""

    captured: dict[str, str] = {}

    def fake_fetch(_cfg: object, _author: str, *, range_spec: str) -> list:
        captured["range_spec"] = range_spec
        return []

    monkeypatch.setattr("favgallery.routers.posts.fetch_author_media_posts", fake_fetch)
    app = create_app(library_root=fake_library, scan_in_background=False)
    client = TestClient(app)

    client.get("/api/authors/alice/unliked?limit=60&offset=0")
    assert captured["range_spec"] == "1-60"
    client.get("/api/authors/alice/unliked?limit=60&offset=60")
    assert captured["range_spec"] == "61-120"
    client.get("/api/authors/alice/unliked?limit=20&offset=40")
    assert captured["range_spec"] == "41-60"


@pytest.mark.integration
def test_timeline_by_tweet_returns_all_nums_in_order(client: TestClient) -> None:
    from favgallery.db import TimelinePost

    def post(num: int) -> TimelinePost:
        return TimelinePost(
            tweet_id="7777", num=num, fetched_at=0, date="2026-01-01 00:00:00",
            author_name="alice", author_nick="アリス", author_avatar_url="",
            content="multi", media_url=f"https://pbs.twimg.com/media/7777_{num}.jpg",
            thumb_url=f"https://pbs.twimg.com/media/7777_{num}.jpg?name=small",
            media_type="photo", width=100, height=100,
            favorite_count=0, view_count=0, hashtags=(),
        )

    db = client.app.state.db  # type: ignore[attr-defined]
    for n in (3, 1, 2):
        db.upsert_timeline_post(post(n))

    r = client.get("/api/timeline/by-tweet/7777")
    assert r.status_code == 200
    items = r.json()["items"]
    assert [it["num"] for it in items] == [1, 2, 3]
    assert all(it["tweet_id"] == "7777" for it in items)


@pytest.mark.integration
def test_timeline_by_tweet_unknown_returns_empty(client: TestClient) -> None:
    r = client.get("/api/timeline/by-tweet/999999")
    assert r.status_code == 200
    assert r.json() == {"items": []}


@pytest.mark.integration
def test_me_set_and_get_username(client: TestClient) -> None:
    r = client.get("/api/me")
    assert r.status_code == 200
    assert r.json()["username"] == ""

    r = client.post("/api/me", json={"username": "@hyota_n"})
    assert r.status_code == 200
    assert r.json()["username"] == "hyota_n"

    r = client.get("/api/me")
    assert r.json()["username"] == "hyota_n"


@pytest.mark.integration
def test_me_rejects_invalid_username(client: TestClient) -> None:
    r = client.post("/api/me", json={"username": "not a handle!"})
    assert r.status_code == 400


@pytest.mark.integration
def test_me_likes_sync_requires_username(client: TestClient) -> None:
    r = client.post("/api/me/likes/sync")
    assert r.status_code == 400


@pytest.mark.integration
def test_unliked_filters_out_my_likes(
    monkeypatch: pytest.MonkeyPatch, fake_library: Path
) -> None:
    from favgallery.db import TimelinePost

    def make(tweet_id: str) -> TimelinePost:
        return TimelinePost(
            tweet_id=tweet_id, num=1, fetched_at=0, date="2026-01-01 00:00:00",
            author_name="alice", author_nick="アリス", author_avatar_url="",
            content="x", media_url=f"https://pbs.twimg.com/media/{tweet_id}.jpg",
            thumb_url=f"https://pbs.twimg.com/media/{tweet_id}.jpg?name=small",
            media_type="photo", width=100, height=100,
            favorite_count=0, view_count=0, hashtags=(),
        )

    monkeypatch.setattr(
        "favgallery.routers.posts.fetch_author_media_posts",
        lambda *_a, **_kw: [make("5001"), make("5002"), make("5003")],
    )
    app = create_app(library_root=fake_library, scan_in_background=False)
    client = TestClient(app)
    # Pretend we have already liked 5002 on X (cached via my_likes).
    db = client.app.state.db  # type: ignore[attr-defined]
    db.upsert_my_likes(["5002"])
    r = client.get("/api/authors/alice/unliked")
    assert r.status_code == 200
    assert sorted(it["tweet_id"] for it in r.json()["items"]) == ["5001", "5003"]


# ---------------------------------------------------------------------------
# GALLERY_DL_COOKIES env var and /api/sync/start cookies check
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_gallery_dl_cookies_env_writes_cookies_file(
    monkeypatch: pytest.MonkeyPatch, fake_library: Path
) -> None:
    """GALLERY_DL_COOKIES env var content must be written to cookies.txt at startup."""
    cookies_path = fake_library / "cookies.txt"
    assert not cookies_path.exists()
    cookie_content = (
        "# Netscape HTTP Cookie File\nexample.com\tFALSE\t/\tFALSE\t0\tsession\tabc123\n"
    )
    monkeypatch.setenv("GALLERY_DL_COOKIES", cookie_content)
    create_app(library_root=fake_library, scan_in_background=False)
    assert cookies_path.exists()
    assert "abc123" in cookies_path.read_text(encoding="utf-8")


@pytest.mark.integration
def test_gallery_dl_cookies_env_empty_does_not_overwrite(
    monkeypatch: pytest.MonkeyPatch, fake_library: Path
) -> None:
    """Unset / empty GALLERY_DL_COOKIES must not touch existing cookies.txt."""
    cookies_path = fake_library / "cookies.txt"
    original = "# existing cookies\n"
    cookies_path.write_text(original, encoding="utf-8")
    monkeypatch.delenv("GALLERY_DL_COOKIES", raising=False)
    create_app(library_root=fake_library, scan_in_background=False)
    assert cookies_path.read_text(encoding="utf-8") == original


@pytest.mark.integration
def test_sync_start_returns_400_when_cookies_missing(
    monkeypatch: pytest.MonkeyPatch, fake_library: Path
) -> None:
    """POST /api/sync/start must return 400 when cookies.txt does not exist."""
    monkeypatch.delenv("GALLERY_DL_COOKIES", raising=False)
    cookies_path = fake_library / "cookies.txt"
    assert not cookies_path.exists()
    app = create_app(library_root=fake_library, scan_in_background=False)
    client = TestClient(app)
    r = client.post("/api/sync/start")
    assert r.status_code == 400
    assert "cookies" in r.json()["reason"].lower()


@pytest.mark.integration
def test_sync_start_not_400_when_cookies_present(
    monkeypatch: pytest.MonkeyPatch, fake_library: Path
) -> None:
    """POST /api/sync/start must not return 400 when cookies.txt exists."""
    monkeypatch.delenv("GALLERY_DL_COOKIES", raising=False)
    cookies_path = fake_library / "cookies.txt"
    cookies_path.write_text("# cookies\n", encoding="utf-8")
    app = create_app(library_root=fake_library, scan_in_background=False)
    client = TestClient(app)
    r = client.post("/api/sync/start")
    # cookies.txt present → 400 must NOT be returned
    # gallery-dl is always available so sync starts (200) or fails with 409 if already running
    assert r.status_code != 400


@pytest.mark.integration
def test_cookies_file_lives_inside_library_root(fake_library: Path) -> None:
    """cookies.txt must live INSIDE library_root (the Railway volume mount),
    not one level above it.

    Regression: the Railway volume is mounted at ``/data/library`` (==
    FAVGALLERY_LIBRARY_ROOT, legacy ARCHIVE_LIBRARY_ROOT), so a cookies file
    at ``library_root.parent`` (``/data``) sat on ephemeral storage, wiped on every
    redeploy — silently dropping the X-sync auth. Keeping it next to the DB
    (which demonstrably persists) makes it survive redeploys regardless of
    where the volume happens to be mounted.
    """
    app = create_app(library_root=fake_library, scan_in_background=False)
    assert app.state.context.cookies_file == fake_library / "cookies.txt"


@pytest.mark.integration
def test_legacy_cookies_migrated_into_library_root(
    monkeypatch: pytest.MonkeyPatch, fake_library: Path
) -> None:
    """A cookies.txt left at the old (pre-volume-fix) location is moved into
    library_root at startup so the transition loses no cookie."""
    monkeypatch.delenv("GALLERY_DL_COOKIES", raising=False)
    legacy = fake_library.parent / "cookies.txt"
    new_path = fake_library / "cookies.txt"
    legacy.write_text(
        "# legacy\n.x.com\tTRUE\t/\tTRUE\t0\tauth_token\tTOK\n", encoding="utf-8"
    )
    assert not new_path.exists()
    create_app(library_root=fake_library, scan_in_background=False)
    assert new_path.exists(), "legacy cookie must be migrated into library_root"
    assert "auth_token" in new_path.read_text(encoding="utf-8")
    assert not legacy.exists(), "legacy cookie must be moved, not left behind"


# ---------------------------------------------------------------------------
# gallery-dl.json Railway generation (ENG-106)
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_ensure_gallerydl_config_written_in_nonportable_env(
    monkeypatch: pytest.MonkeyPatch, fake_library: Path, tmp_path: Path
) -> None:
    """_ensure_gallerydl_config writes gallery-dl.json when portable_root() is None (Railway)."""
    from favgallery import server as server_module
    from favgallery.server import _ensure_gallerydl_config

    monkeypatch.setattr(server_module, "portable_root", lambda: None)
    config_path = tmp_path / "config" / "gallery-dl.json"
    _ensure_gallerydl_config(config_path, fake_library)

    assert config_path.exists(), "gallery-dl.json must be created in Railway mode"
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    assert cfg["downloader"]["ffmpeg-location"] == "ffmpeg"
    cookies_in_cfg = cfg["extractor"]["twitter"]["cookies"]
    expected_cookies = str(fake_library / "cookies.txt").replace("\\", "/")
    assert cookies_in_cfg == expected_cookies
    base_dir = cfg["extractor"]["base-directory"]
    assert base_dir.endswith("/")
    assert fake_library.name in base_dir


@pytest.mark.integration
def test_gallerydl_config_path_is_under_data_in_nonportable_env(
    fake_library: Path
) -> None:
    """Non-frozen env (Railway-equivalent): gallery-dl.json sits at library_root.parent/config/."""
    # portable_root() naturally returns None in the test environment (not frozen).
    app = create_app(library_root=fake_library, scan_in_background=False)
    cfg_path: Path = app.state.timeline_refresher.gallerydl_config_path  # type: ignore[attr-defined]
    assert cfg_path == fake_library.parent / "config" / "gallery-dl.json"
    assert cfg_path.exists(), "config must be written at startup"


@pytest.mark.integration
def test_like_and_save_records_my_like(
    monkeypatch: pytest.MonkeyPatch, fake_library: Path
) -> None:
    from favgallery import like as like_module
    from favgallery import save_one as save_module

    monkeypatch.setattr(
        "favgallery.routers.timeline.like_tweet",
        lambda *_a, **_kw: like_module.LikeResult(ok=True, status_code=200, message="ok"),
    )
    monkeypatch.setattr(
        "favgallery.routers.timeline.save_tweet",
        lambda *_a, **_kw: save_module.SaveResult(ok=True, return_code=0, message="ok"),
    )
    app = create_app(library_root=fake_library, scan_in_background=False)
    client = TestClient(app)
    r = client.post(
        "/api/timeline/like-and-save",
        json={"tweet_id": "12345", "author_name": "alice"},
    )
    assert r.status_code == 200
    assert r.json()["liked"] is True
    db = client.app.state.db  # type: ignore[attr-defined]
    assert "12345" in db.my_likes_ids()


@pytest.mark.integration
def test_media_and_thumb_etags_are_strong(client: TestClient) -> None:
    """immutable メディアの ETag は strong (W/ なし) — ブラウザキャッシュ強化
    (perf Phase 1 / 2026-06-10)。304 round-trip も維持。"""
    posts = client.get("/api/posts").json()["items"]
    media_url = posts[0]["media_url"]
    r = client.get(media_url)
    etag = r.headers.get("ETag", "")
    assert etag and not etag.startswith("W/")
    r304 = client.get(media_url, headers={"If-None-Match": etag})
    assert r304.status_code == 304


@pytest.mark.integration
def test_prebuilt_css_served_with_long_cache(client: TestClient) -> None:
    """Phase 3: 事前生成 CSS は ?v= 付き参照なので immutable 長期キャッシュ。
    それ以外の /static (将来の lib/*.js 含む) は no-cache (v0.2.3 の教訓:
    ES module の深い import は ?v= を運べない)。"""
    r = client.get("/static/style.css")
    assert r.status_code == 200
    assert r.headers.get("Cache-Control") == "public, max-age=31536000, immutable"


@pytest.mark.integration
def test_index_references_versioned_stylesheet(client: TestClient) -> None:
    # 資産 URL の ?v= は app version ではなくコンテンツハッシュ (ASSET_VERSION)。
    # 純粋リファクタで版を上げずにキャッシュバストするため (2026-06-11 分離)。
    from favgallery.server import ASSET_VERSION

    html = client.get("/").text
    assert f"/static/style.css?v={ASSET_VERSION}" in html
    assert "cdn.tailwindcss.com" not in html


@pytest.mark.integration
def test_static_css_401_is_not_long_cached(
    monkeypatch: pytest.MonkeyPatch, fake_library: Path
) -> None:
    """認証前の 401 に immutable を付けない (401 が 1 年キャッシュされる事故防止)。"""
    monkeypatch.setenv("FAVGALLERY_USER", "u")
    monkeypatch.setenv("FAVGALLERY_PASSWORD", "p")
    app = create_app(library_root=fake_library, scan_in_background=False)
    c = TestClient(app, raise_server_exceptions=False)
    r = c.get("/static/style.css")
    assert r.status_code == 401
    assert r.headers.get("Cache-Control") == "no-cache"


@pytest.mark.integration
def test_main_js_module_served_no_cache(client: TestClient) -> None:
    """Phase 4: 分割した JS モジュールは no-cache 配信 (深い import は ?v= を
    運べないため長期キャッシュ禁止 / v0.2.3 の教訓)。"""
    r = client.get("/static/lib/main.js")
    assert r.status_code == 200
    assert r.headers.get("Cache-Control") == "no-cache"


@pytest.mark.integration
def test_index_references_versioned_module_entry(client: TestClient) -> None:
    # モジュール URL の ?v= も ASSET_VERSION (コンテンツハッシュ)。
    from favgallery.server import ASSET_VERSION

    html = client.get("/").text
    assert f'<script type="module" src="/static/lib/main.js?v={ASSET_VERSION}"></script>' in html
    # bootstrap (エラーレポータ + APP_VERSION) は inline に残っていること
    assert "window.APP_VERSION" in html
    assert "reportClientError" in html

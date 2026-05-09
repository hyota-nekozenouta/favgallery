"""FastAPI app: gallery API + media + sync orchestration + lists + timeline."""

from __future__ import annotations

import base64
import contextlib
import dataclasses
import json as _json
import os
import secrets
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from xlikes_viewer.db import Database, TimelinePost
from xlikes_viewer.dedup import DedupRunner, VisualDedupRunner
from xlikes_viewer.like import like_tweet
from xlikes_viewer.proxy import CdnProxy, is_allowed
from xlikes_viewer.r2 import R2Client, r2_config_from_env
from xlikes_viewer.save_one import save_tweet
from xlikes_viewer.scanner import DEFAULT_LIBRARY, Index, Post, scan_library
from xlikes_viewer.sync import SyncRunner
from xlikes_viewer.thumbs import thumbnail_bytes
from xlikes_viewer.timeline import (
    TimelineRefresher,
    fetch_author_media_posts,
    fetch_my_liked_tweet_ids,
)
from xlikes_viewer.paths import portable_root
from xlikes_viewer.x_helpers import tweet_url


class _ListCreateBody(BaseModel):
    name: str


class _FavAuthorsBody(BaseModel):
    authors: list[str]


class _ListItemBody(BaseModel):
    tweet_id: str
    num: int


class _LikeAndSaveBody(BaseModel):
    tweet_id: str
    author_name: str


class _LastSeenBody(BaseModel):
    tweet_id: str


class _MeBody(BaseModel):
    username: str


_LAST_SEEN_KEY = "last_seen_timeline_tweet_id"
_MY_USERNAME_KEY = "my_username"


def _write_cookies_from_env(cookies_path: Path) -> None:
    """Write GALLERY_DL_COOKIES env var content to cookies_path if set.

    Called at startup so Railway Variables can supply cookies without file
    transfers or CLI commands.  An empty / unset var is silently ignored so
    existing cookies.txt files are preserved.
    """
    content = os.environ.get("GALLERY_DL_COOKIES", "")
    if not content:
        return
    cookies_path.parent.mkdir(parents=True, exist_ok=True)
    cookies_path.write_text(content, encoding="utf-8")


def _ensure_gallerydl_config(config_path: Path, library_root: Path) -> None:
    """Write gallery-dl.json with current absolute paths.

    Called at startup so moving the portable folder to another PC automatically
    fixes stale paths (cookies, ffmpeg, base-directory, archive).
    Runs in both portable (Windows) and server (Railway) layouts.
    """
    def _fwd(p: Path) -> str:
        return str(p).replace("\\", "/")

    data_dir = library_root.parent
    if portable_root() is not None:
        bundle_root = library_root.parent.parent
        ffmpeg_exe = bundle_root / "ffmpeg" / "bin" / "ffmpeg.exe"
        ffmpeg_location: str = _fwd(ffmpeg_exe) if ffmpeg_exe.exists() else "ffmpeg"
    else:
        # Railway: ffmpeg is on PATH
        ffmpeg_location = "ffmpeg"

    desired: dict = {
        "extractor": {
            "base-directory": _fwd(library_root) + "/",
            "archive": _fwd(library_root / "archive.sqlite"),
            "twitter": {
                "cookies": _fwd(data_dir / "cookies.txt"),
                "videos": True,
                "retweets": False,
                "text-tweets": False,
                "filename": "{tweet_id}_{num}.{extension}",
                "directory": ["{user[name]}"],
                "postprocessors": [{"name": "metadata", "mode": "json"}],
            },
        },
        "downloader": {
            "mtime": True,
            "ffmpeg-location": ffmpeg_location,
        },
        "output": {"progress": True},
    }

    config_path.parent.mkdir(parents=True, exist_ok=True)
    if config_path.exists():
        try:
            if _json.loads(config_path.read_text(encoding="utf-8")) == desired:
                return
        except (OSError, _json.JSONDecodeError):
            pass
    config_path.write_text(_json.dumps(desired, indent=2, ensure_ascii=False), encoding="utf-8")


def _base_payload(
    *,
    tweet_id: str,
    num: int,
    media_url: str,
    thumb_url: str,
    media_type: str,
    extension: str,
    width: int | None,
    height: int | None,
    date: str,
    author_name: str,
    author_nick: str,
    content: str,
    favorite_count: int,
    view_count: int,
    sensitive: bool,
    lang: str,
    hashtags: tuple[str, ...],
    extra: dict | None = None,
) -> dict:
    payload = {
        "tweet_id": tweet_id,
        "num": num,
        "media_url": media_url,
        "thumb_url": thumb_url,
        "media_type": media_type,
        "extension": extension,
        "width": width,
        "height": height,
        "date": date,
        "author_name": author_name,
        "author_nick": author_nick,
        "content": content,
        "favorite_count": favorite_count,
        "view_count": view_count,
        "sensitive": sensitive,
        "lang": lang,
        "hashtags": list(hashtags),
        "tweet_url": tweet_url(author_name, tweet_id),
    }
    if extra:
        payload.update(extra)
    return payload


def _post_payload(p: Post) -> dict:
    return _base_payload(
        tweet_id=p.tweet_id,
        num=p.num,
        media_url=f"/api/media/{p.rel_media}",
        thumb_url=f"/thumb/{p.rel_media}",
        media_type=p.media_type,
        extension=p.extension,
        width=p.width,
        height=p.height,
        date=p.date,
        author_name=p.author_name,
        author_nick=p.author_nick,
        content=p.content,
        favorite_count=p.favorite_count,
        view_count=p.view_count,
        sensitive=p.sensitive,
        lang=p.lang,
        hashtags=p.hashtags,
    )


def _timeline_payload(p: TimelinePost) -> dict:
    return _base_payload(
        tweet_id=p.tweet_id,
        num=p.num,
        media_url=f"/api/timeline/proxy?url={p.media_url}",
        thumb_url=f"/api/timeline/proxy?url={p.thumb_url}",
        media_type=p.media_type,
        extension="",
        width=p.width,
        height=p.height,
        date=p.date,
        author_name=p.author_name,
        author_nick=p.author_nick,
        content=p.content,
        favorite_count=p.favorite_count,
        view_count=p.view_count,
        sensitive=False,
        lang="",
        hashtags=p.hashtags,
        extra={"raw_media_url": p.media_url, "author_avatar_url": p.author_avatar_url},
    )


def _make_basic_auth_middleware():
    """Return an HTTP Basic auth middleware if ARCHIVE_USER/ARCHIVE_PASSWORD are set.

    When the env vars are absent the middleware is a no-op, so development /
    local-desktop use works without configuration.
    Authentication uses secrets.compare_digest to prevent timing attacks.
    """

    async def basic_auth_middleware(request: Request, call_next):
        archive_user = os.environ.get("ARCHIVE_USER", "")
        archive_password = os.environ.get("ARCHIVE_PASSWORD", "")

        if not archive_user or not archive_password:
            # Auth not configured — pass through (local / dev use).
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Basic "):
            return Response(
                content="Unauthorized",
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="Archive"'},
            )

        try:
            decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
            req_user, req_pass = decoded.split(":", 1)
        except Exception:
            return Response(
                content="Unauthorized",
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="Archive"'},
            )

        user_ok = secrets.compare_digest(req_user, archive_user)
        pass_ok = secrets.compare_digest(req_pass, archive_password)
        if not (user_ok and pass_ok):
            return Response(
                content="Unauthorized",
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="Archive"'},
            )

        return await call_next(request)

    return basic_auth_middleware


def create_app(
    library_root: Path = DEFAULT_LIBRARY,
    *,
    scan_in_background: bool = True,
) -> FastAPI:
    app = FastAPI(title="xlikes Viewer", version="0.2.0")
    app.middleware("http")(_make_basic_auth_middleware())
    # Heavy `scan_library` walk goes on a background thread by default so the
    # window can paint immediately; the frontend polls `scanning=True` via
    # /api/library until done. Tests pass `scan_in_background=False` to keep
    # behavior deterministic.
    state: dict[str, object] = {
        "index": Index(library_root=library_root),
        "scanning": scan_in_background,
    }
    state_lock = threading.Lock()

    def _initial_scan() -> None:
        idx = scan_library(library_root)
        with state_lock:
            state["index"] = idx
            state["scanning"] = False

    if scan_in_background:
        threading.Thread(target=_initial_scan, daemon=True).start()
    else:
        _initial_scan()

    app.state.library_root = library_root

    db = Database(library_root / "xlikes.sqlite")
    cookies_file = library_root.parent / "cookies.txt"  # data/cookies.txt
    _write_cookies_from_env(cookies_file)

    r2_cfg = r2_config_from_env()
    r2_client: R2Client | None = R2Client(r2_cfg) if r2_cfg is not None else None
    app.state.r2_client = r2_client
    cdn_proxy = CdnProxy(cookies_file)
    gallerydl_config_path = (
        library_root.parent.parent / "config" / "gallery-dl.json"
        if portable_root() is not None
        else library_root.parent / "config" / "gallery-dl.json"
    )
    _fav_authors_path = library_root.parent.parent / "config" / "favorite_authors.json"
    _ensure_gallerydl_config(gallerydl_config_path, library_root)
    timeline_refresher = TimelineRefresher(db, gallerydl_config_path)
    dedup_runner = DedupRunner(db, library_root)
    visual_dedup_runner = VisualDedupRunner(db, library_root)
    app.state.db = db
    app.state.cdn_proxy = cdn_proxy
    app.state.timeline_refresher = timeline_refresher
    app.state.dedup_runner = dedup_runner
    app.state.visual_dedup_runner = visual_dedup_runner

    static_dir = Path(__file__).resolve().parent / "static"

    def _index() -> Index:
        with state_lock:
            return state["index"]  # type: ignore[return-value]

    def _refresh_index() -> Index:
        idx = scan_library(library_root)
        with state_lock:
            state["index"] = idx
        return idx

    @app.get("/", response_class=HTMLResponse)
    def root() -> HTMLResponse:
        html = (static_dir / "index.html").read_text(encoding="utf-8")
        return HTMLResponse(html)

    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/api/library")
    def api_library() -> JSONResponse:
        idx = _index()
        with state_lock:
            scanning = bool(state.get("scanning", False))
        return JSONResponse(
            {
                "library_root": str(idx.library_root),
                "post_count": len(idx.posts),
                "authors": [dataclasses.asdict(a) for a in idx.authors.values()],
                "tags": [{"name": k, "count": v} for k, v in list(idx.tags.items())[:200]],
                "scanning": scanning,
            }
        )

    @app.post("/api/library/refresh")
    def api_refresh() -> JSONResponse:
        idx = _refresh_index()
        return JSONResponse({"post_count": len(idx.posts)})

    @app.get("/api/posts")
    def api_posts(
        author: str | None = None,
        tag: str | None = None,
        media_type: str | None = None,
        q: str | None = None,
        list_id: int | None = Query(default=None, alias="list"),
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> JSONResponse:
        idx = _index()
        filtered = idx.filter(author=author, tag=tag, media_type=media_type, query=q)
        if list_id is not None:
            keys = db.posts_in_list(list_id)
            filtered = [p for p in filtered if (p.tweet_id, p.num) in keys]
        total = len(filtered)
        page = filtered[offset : offset + limit]
        return JSONResponse(
            {
                "total": total,
                "items": [_post_payload(p) for p in page],
                "offset": offset,
                "limit": limit,
            }
        )

    @app.get("/api/posts/by-tweet/{tweet_id}")
    def api_posts_by_tweet(tweet_id: str) -> JSONResponse:
        idx = _index()
        items = sorted(
            (p for p in idx.posts if p.tweet_id == tweet_id),
            key=lambda p: p.num,
        )
        return JSONResponse({"items": [_post_payload(p) for p in items]})

    @app.get("/api/authors/{author}/summary")
    def api_author_summary(author: str) -> JSONResponse:
        idx = _index()
        posts = [p for p in idx.posts if p.author_name == author]
        counts: dict[str, int] = {"total": len(posts)}
        for p in posts:
            counts[p.media_type] = counts.get(p.media_type, 0) + 1
        nick = posts[0].author_nick if posts else ""
        return JSONResponse({"author": author, "nick": nick, "counts": counts})

    # Serialize gallery-dl invocations: prepare_config touches global state.
    unliked_lock = threading.Lock()

    # SyncRunner shares the same serialization lock so prepare_config calls
    # from concurrent gallery-dl users don't trample each other.
    sync_runner = SyncRunner(
        config_path=gallerydl_config_path,
        db=db,
        gdl_lock=unliked_lock,
        library_root=library_root,
        r2_client=r2_client,
    )
    app.state.sync_runner = sync_runner

    @app.get("/api/authors/{author}/unliked")
    def api_author_unliked(
        author: str,
        limit: int = Query(default=60, ge=1, le=200),
        offset: int = Query(default=0, ge=0, le=10000),
    ) -> JSONResponse:
        idx = _index()
        local_tweet_ids = {p.tweet_id for p in idx.posts if p.author_name == author}
        my_liked_ids = db.my_likes_ids()
        # gallery-dl uses 1-based inclusive ranges (`file-range`).
        start = offset + 1
        end = offset + limit
        try:
            with unliked_lock:
                posts = fetch_author_media_posts(
                    gallerydl_config_path,
                    author,
                    range_spec=f"{start}-{end}",
                )
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"gallery-dl failed: {type(exc).__name__}: {exc}",
            ) from exc
        # "Unliked" = on X, but: not in my_likes cache (own liked tweets),
        # not in local likes archive, and not flagged as `favorited` in the
        # raw metadata (defensive — gallery-dl currently does not surface it).
        unliked = [
            p for p in posts
            if not p.favorited
            and p.tweet_id not in local_tweet_ids
            and p.tweet_id not in my_liked_ids
        ]
        # has_more is heuristic: gallery-dl returns up to `limit` items per
        # batch, so if we got a full page we assume there might be more.
        return JSONResponse(
            {
                "author": author,
                "fetched": len(posts),
                "offset": offset,
                "limit": limit,
                "has_more": len(posts) >= limit,
                "items": [_timeline_payload(p) for p in unliked],
            }
        )

    library_root_resolved = library_root.resolve()

    @app.delete("/api/posts/{tweet_id}/{num}")
    def delete_post(tweet_id: str, num: int) -> JSONResponse:
        idx = _index()
        target = next(
            (p for p in idx.posts if p.tweet_id == tweet_id and p.num == num),
            None,
        )
        if target is None:
            raise HTTPException(status_code=404, detail="post not found")

        media = target.media_path.resolve()
        try:
            media.relative_to(library_root_resolved)
        except ValueError as e:
            raise HTTPException(status_code=400, detail="path escape") from e

        with contextlib.suppress(FileNotFoundError):
            media.unlink()
        with contextlib.suppress(FileNotFoundError):
            target.json_path.unlink()

        # Lists + hash cache: cascade cleanup so they don't outlive the file.
        # The gallery-dl archive.sqlite is intentionally untouched so the next
        # sync does not re-download this tweet's media.
        db.remove_item_from_all_lists(tweet_id, num)
        rel = media.relative_to(library_root_resolved).as_posix()
        db.forget_hash(rel)
        _refresh_index()
        return JSONResponse({"deleted": True})

    # --- Lists ---------------------------------------------------------

    @app.get("/api/favorite-authors")
    def fav_authors_get() -> JSONResponse:
        import json as _json
        if _fav_authors_path.exists():
            try:
                return JSONResponse(_json.loads(_fav_authors_path.read_text(encoding="utf-8")))
            except Exception:
                pass
        return JSONResponse([])

    @app.post("/api/favorite-authors")
    def fav_authors_set(body: _FavAuthorsBody) -> JSONResponse:
        import json as _json
        _fav_authors_path.parent.mkdir(parents=True, exist_ok=True)
        _fav_authors_path.write_text(
            _json.dumps(body.authors, ensure_ascii=False), encoding="utf-8"
        )
        return JSONResponse({"saved": True})

    @app.get("/api/lists")
    def lists_index() -> JSONResponse:
        return JSONResponse(
            [
                {"id": x.id, "name": x.name, "count": x.item_count, "created_at": x.created_at}
                for x in db.lists()
            ]
        )

    @app.post("/api/lists")
    def lists_create(body: _ListCreateBody) -> JSONResponse:
        name = body.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="name required")
        try:
            row = db.create_list(name)
        except Exception as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return JSONResponse(
            {"id": row.id, "name": row.name, "count": 0, "created_at": row.created_at}
        )

    @app.delete("/api/lists/{list_id}")
    def lists_delete(list_id: int) -> JSONResponse:
        ok = db.delete_list(list_id)
        if not ok:
            raise HTTPException(status_code=404, detail="not found")
        return JSONResponse({"deleted": True})

    @app.post("/api/lists/{list_id}/items")
    def lists_add_item(list_id: int, body: _ListItemBody) -> JSONResponse:
        if not body.tweet_id or body.num <= 0:
            raise HTTPException(status_code=400, detail="tweet_id and num required")
        added = db.add_item(list_id, body.tweet_id, body.num)
        return JSONResponse({"added": added})

    @app.delete("/api/lists/{list_id}/items/{tweet_id}/{num}")
    def lists_remove_item(list_id: int, tweet_id: str, num: int) -> JSONResponse:
        removed = db.remove_item(list_id, tweet_id, num)
        return JSONResponse({"removed": removed})

    @app.get("/api/posts/lists")
    def post_lists(tweet_id: str, num: int) -> JSONResponse:
        return JSONResponse({"list_ids": db.lists_for_post(tweet_id, num)})

    # --- Timeline ------------------------------------------------------

    @app.get("/api/timeline")
    def timeline_index(
        media_type: str | None = None,
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        hide_liked: bool = Query(default=False),
    ) -> JSONResponse:
        total, posts = db.list_timeline_posts(
            limit=limit, offset=offset, media_type=media_type, exclude_liked=hide_liked
        )
        return JSONResponse(
            {
                "total": total,
                "items": [_timeline_payload(p) for p in posts],
                "offset": offset,
                "limit": limit,
            }
        )

    @app.get("/api/timeline/by-tweet/{tweet_id}")
    def timeline_by_tweet(tweet_id: str) -> JSONResponse:
        posts = db.select_timeline_posts_by_tweet(tweet_id)
        return JSONResponse({"items": [_timeline_payload(p) for p in posts]})

    # --- My-likes cache (own X likes set) ------------------------------
    # Single-flight orchestrator state for /api/me/likes/sync. gallery-dl's
    # global config makes parallel runs unsafe, so we serialize through this.
    me_likes_state: dict[str, object] = {
        "running": False,
        "last_started": None,
        "last_finished": None,
        "last_error": None,
        "last_added": 0,
    }
    me_likes_lock = threading.Lock()

    def _me_username() -> str:
        return (db.get_setting(_MY_USERNAME_KEY) or "").strip()

    @app.get("/api/me")
    def api_me_get() -> JSONResponse:
        return JSONResponse(
            {
                "username": _me_username(),
                "my_likes_count": db.my_likes_count(),
            }
        )

    @app.post("/api/me")
    def api_me_set(body: _MeBody) -> JSONResponse:
        name = body.username.strip().lstrip("@")
        if not name:
            db.set_setting(_MY_USERNAME_KEY, "")
            return JSONResponse({"username": ""})
        # Permissive validator: X handles are 1–15 chars of [A-Za-z0-9_].
        import re
        if not re.fullmatch(r"[A-Za-z0-9_]{1,15}", name):
            raise HTTPException(status_code=400, detail="invalid X username")
        db.set_setting(_MY_USERNAME_KEY, name)
        return JSONResponse({"username": name})

    @app.get("/api/me/likes/status")
    def api_me_likes_status() -> JSONResponse:
        with me_likes_lock:
            snapshot = dict(me_likes_state)
        snapshot["count"] = db.my_likes_count()
        snapshot["username"] = _me_username()
        return JSONResponse(snapshot)

    def _me_likes_worker(username: str, range_spec: str) -> None:
        added = 0
        try:
            with unliked_lock:  # share gallery-dl serialization with /unliked
                tweet_ids = fetch_my_liked_tweet_ids(
                    gallerydl_config_path, username, range_spec=range_spec
                )
            added = db.upsert_my_likes(tweet_ids)
        except Exception as exc:
            with me_likes_lock:
                me_likes_state["last_error"] = f"{type(exc).__name__}: {exc}"
        finally:
            with me_likes_lock:
                me_likes_state["running"] = False
                me_likes_state["last_finished"] = time.time()
                me_likes_state["last_added"] = added

    @app.post("/api/me/likes/sync")
    def api_me_likes_sync(
        range_spec: str = Query(default="1-200", alias="range"),
    ) -> JSONResponse:
        username = _me_username()
        if not username:
            raise HTTPException(status_code=400, detail="set username first via POST /api/me")
        with me_likes_lock:
            if me_likes_state["running"]:
                return JSONResponse({"started": False, "reason": "already running"}, status_code=409)
            me_likes_state["running"] = True
            me_likes_state["last_started"] = time.time()
            me_likes_state["last_error"] = None
            me_likes_state["last_added"] = 0
        threading.Thread(
            target=_me_likes_worker, args=(username, range_spec), daemon=True
        ).start()
        return JSONResponse({"started": True})

    @app.delete("/api/me/likes")
    def api_me_likes_clear() -> JSONResponse:
        db.clear_my_likes()
        return JSONResponse({"cleared": True})

    @app.post("/api/timeline/refresh")
    def timeline_refresh() -> JSONResponse:
        ok, reason = timeline_refresher.can_start()
        if not ok:
            return JSONResponse(
                {"started": False, "reason": reason}, status_code=429 if reason else 409
            )
        timeline_refresher.start()
        return JSONResponse({"started": True})

    @app.get("/api/timeline/status")
    def timeline_status() -> JSONResponse:
        s = timeline_refresher.state
        return JSONResponse(
            {
                "running": s.running,
                "last_started": s.last_started,
                "last_finished": s.last_finished,
                "last_added": s.last_added,
                "last_error": s.last_error,
            }
        )

    @app.get("/api/timeline/last-seen")
    def timeline_last_seen() -> JSONResponse:
        return JSONResponse({"tweet_id": db.get_setting(_LAST_SEEN_KEY) or ""})

    @app.post("/api/timeline/last-seen")
    def timeline_set_last_seen(body: _LastSeenBody) -> JSONResponse:
        db.set_setting(_LAST_SEEN_KEY, body.tweet_id)
        return JSONResponse({"tweet_id": body.tweet_id})

    @app.post("/api/timeline/like-and-save")
    def timeline_like_and_save(body: _LikeAndSaveBody) -> JSONResponse:
        like_result = like_tweet(cookies_file, body.tweet_id)
        save_result = None
        if like_result.ok:
            # Record it in the my_likes cache so the "未いいね" filter hides
            # this tweet on subsequent fetches even if save_tweet fails.
            db.upsert_my_likes([body.tweet_id])
            save_result = save_tweet(
                gallerydl_config_path,
                author_name=body.author_name,
                tweet_id=body.tweet_id,
            )
        return JSONResponse(
            {
                "liked": like_result.ok,
                "like_status": like_result.status_code,
                "like_message": like_result.message,
                "saved": save_result.ok if save_result else False,
                "save_message": save_result.message if save_result else "",
            }
        )

    @app.get("/api/timeline/proxy")
    async def timeline_proxy(request: Request, url: str) -> Response:
        if not is_allowed(url):
            raise HTTPException(status_code=400, detail="disallowed host")
        parsed = urlparse(url)
        if parsed.scheme != "https":
            raise HTTPException(status_code=400, detail="https only")
        range_header = request.headers.get("range")
        try:
            status, headers, body_iter = await cdn_proxy.stream(url, range_header=range_header)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"upstream error: {exc}") from exc
        return StreamingResponse(body_iter, status_code=status, headers=headers)

    # --- Sync (xlikes downloader) -------------------------------------

    @app.get("/api/sync/status")
    def sync_status() -> JSONResponse:
        s = sync_runner.state
        return JSONResponse(
            {
                "running": s.running,
                "started_at": s.started_at,
                "finished_at": s.finished_at,
                "return_code": s.last_return_code,
                "error": s.last_error,
                "exe_present": True,  # gallery-dl is always available
                "log_tail": list(s.log_lines)[-40:],
            }
        )

    @app.post("/api/sync/start")
    def sync_start() -> JSONResponse:
        if not cookies_file.exists():
            return JSONResponse(
                {"started": False, "reason": "cookies.txt not found — set GALLERY_DL_COOKIES env var"},
                status_code=400,
            )
        ok = sync_runner.start()
        if not ok:
            return JSONResponse(
                {"started": False, "reason": sync_runner.state.last_error or "already running"},
                status_code=409,
            )
        return JSONResponse({"started": True})

    @app.post("/api/sync/stop")
    def sync_stop() -> JSONResponse:
        return JSONResponse({"stopped": sync_runner.stop()})

    # --- Admin --------------------------------------------------------

    @app.post("/api/admin/cleanup-local")
    def admin_cleanup_local() -> JSONResponse:
        """Delete local media files that are already present in R2.

        Protected by the existing Basic auth middleware (ARCHIVE_USER / ARCHIVE_PASSWORD).
        Safe to call after a sync completes or any time R2 is populated.
        Returns ``{"deleted": n, "checked": n, "errors": n}``.
        """
        if r2_client is None:
            raise HTTPException(status_code=503, detail="R2 not configured")
        result = sync_runner.cleanup_local()
        return JSONResponse(result)

    # --- Dedup --------------------------------------------------------

    @app.post("/api/dedup/run")
    def dedup_run() -> JSONResponse:
        if not dedup_runner.start():
            return JSONResponse({"started": False, "reason": "already running"}, status_code=409)
        return JSONResponse({"started": True})

    @app.get("/api/dedup/status")
    def dedup_status() -> JSONResponse:
        s = dedup_runner.state
        return JSONResponse(
            {
                "running": s.running,
                "started_at": s.started_at,
                "finished_at": s.finished_at,
                "files_total": s.files_total,
                "files_hashed": s.files_hashed,
                "duplicates_deleted": s.duplicates_deleted,
                "bytes_freed": s.bytes_freed,
                "last_error": s.last_error,
                "lifetime_deleted": db.dedup_log_count(),
            }
        )

    @app.post("/api/dedup/visual/run")
    def visual_dedup_run() -> JSONResponse:
        if not visual_dedup_runner.start():
            return JSONResponse({"started": False, "reason": "already running"}, status_code=409)
        return JSONResponse({"started": True})

    @app.get("/api/dedup/visual/status")
    def visual_dedup_status() -> JSONResponse:
        s = visual_dedup_runner.state
        return JSONResponse(
            {
                "running": s.running,
                "started_at": s.started_at,
                "finished_at": s.finished_at,
                "files_total": s.files_total,
                "files_indexed": s.files_indexed,
                "duplicates_deleted": s.duplicates_deleted,
                "bytes_freed": s.bytes_freed,
                "last_error": s.last_error,
            }
        )

    # --- Media (likes archive) ----------------------------------------

    def _validate_rel_path(rel_path: str) -> None:
        """Raise HTTPException if rel_path attempts to escape library_root."""
        target = (library_root / rel_path).resolve()
        try:
            target.relative_to(library_root_resolved)
        except ValueError as e:
            raise HTTPException(status_code=400, detail="path escape") from e

    def _resolve_under_library(rel_path: str) -> Path:
        target = (library_root / rel_path).resolve()
        try:
            target.relative_to(library_root_resolved)
        except ValueError as e:
            raise HTTPException(status_code=400, detail="path escape") from e
        if not target.is_file():
            raise HTTPException(status_code=404, detail="not found")
        return target

    @app.get("/api/media/{rel_path:path}")
    async def api_media(rel_path: str) -> Response:
        """Serve media from R2 when configured, otherwise from the local library."""
        _validate_rel_path(rel_path)
        if r2_client is not None:
            try:
                content_length, content_type, body_iter = r2_client.stream_object(rel_path)
                headers = {"content-length": str(content_length)} if content_length else {}
                return StreamingResponse(body_iter, media_type=content_type, headers=headers)
            except Exception:
                # Fall through to local filesystem if key is absent in R2.
                pass
        target = (library_root / rel_path).resolve()
        if not target.is_file():
            raise HTTPException(status_code=404, detail="not found")
        return FileResponse(target)

    @app.get("/media/{rel_path:path}")
    def media(rel_path: str) -> FileResponse:
        return FileResponse(_resolve_under_library(rel_path))

    @app.get("/thumb/{rel_path:path}")
    def thumb(rel_path: str, size: int = Query(default=400, ge=64, le=1600)) -> Response:
        target = _resolve_under_library(rel_path)
        data = thumbnail_bytes(target, size=size)
        if data is None:
            return FileResponse(target)
        return Response(content=data, media_type="image/jpeg")

    return app


# ---------------------------------------------------------------------------
# Module-level ASGI app for direct uvicorn invocation:
#   uvicorn xlikes_viewer.server:app --host 0.0.0.0 --port $PORT
#
# Library root is resolved from the ARCHIVE_LIBRARY_ROOT env var when set
# (Railway production), falling back to the default portable/installed path.
# ---------------------------------------------------------------------------
def _module_level_app() -> FastAPI:
    library_root_env = os.environ.get("ARCHIVE_LIBRARY_ROOT", "")
    if library_root_env:
        library_root = Path(library_root_env)
    else:
        from xlikes_viewer.paths import default_library_root
        library_root = default_library_root()
    return create_app(library_root=library_root)


app = _module_level_app()

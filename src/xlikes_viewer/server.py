"""FastAPI app: gallery API + media + sync orchestration + lists + timeline."""

from __future__ import annotations

import base64
import contextlib
import dataclasses
import hashlib
import json as _json
import os
import secrets
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from xlikes_viewer.book_dedup import (
    BookIndexRunner,
    find_duplicate_book,
    fingerprint_for_ordered_files,
)
from xlikes_viewer.context import AppContext
from xlikes_viewer.db import Database
from xlikes_viewer.dedup import DedupRunner, VisualDedupRunner
from xlikes_viewer.gallerydl_config import build_book_import_config, write_gallerydl_config
from xlikes_viewer.keys import r2_key_for_path
from xlikes_viewer.like import like_tweet
from xlikes_viewer.paths import portable_root
from xlikes_viewer.payloads import _post_payload, _timeline_payload
from xlikes_viewer.proxy import CdnProxy, is_allowed
from xlikes_viewer.r2 import R2Client, r2_config_from_env
from xlikes_viewer.routers import admin as admin_router
from xlikes_viewer.routers import dedup as dedup_router
from xlikes_viewer.routers import lists as lists_router
from xlikes_viewer.routers import sync as sync_router
from xlikes_viewer.save_one import save_tweet
from xlikes_viewer.scanner import (
    DEFAULT_LIBRARY,
    Index,
    build_index_from_db,
    ingest_to_db,
)
from xlikes_viewer.sync import SyncRunner
from xlikes_viewer.thumbs import thumbnail_bytes, thumbnail_bytes_from_raw
from xlikes_viewer.timeline import (
    TimelineRefresher,
    fetch_author_media_posts,
    fetch_my_liked_tweet_ids,
)


class _FavAuthorsBody(BaseModel):
    authors: list[str]


class _LikeAndSaveBody(BaseModel):
    tweet_id: str
    author_name: str


class _LastSeenBody(BaseModel):
    tweet_id: str


class _MeBody(BaseModel):
    username: str


class _BookImportBody(BaseModel):
    url: str


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
    """Backwards-compatible shim — see gallerydl_config.write_gallerydl_config."""
    write_gallerydl_config(config_path, library_root)


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
    r2_client: R2Client | None = None,
) -> FastAPI:
    app = FastAPI(title="Archive", version="0.2.0")
    app.middleware("http")(_make_basic_auth_middleware())
    # Index is built from DB (persistent). On first run or after sync,
    # local JSON sidecars are ingested into the DB. The frontend polls
    # `scanning=True` via /api/library until the initial load completes.
    state: dict[str, object] = {
        "index": Index(library_root=library_root),
        "scanning": scan_in_background,
    }
    state_lock = threading.Lock()

    app.state.library_root = library_root

    db = Database(library_root / "xlikes.sqlite")
    cookies_file = library_root.parent / "cookies.txt"  # data/cookies.txt
    _write_cookies_from_env(cookies_file)

    # Allow tests / callers to inject an R2 client; otherwise build from env.
    # Production behaviour is unchanged (no injection -> env-derived client).
    if r2_client is None:
        r2_cfg = r2_config_from_env()
        r2_client = R2Client(r2_cfg) if r2_cfg is not None else None
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
    book_index_runner = BookIndexRunner(db, library_root, r2_client)
    app.state.db = db
    app.state.cdn_proxy = cdn_proxy
    app.state.timeline_refresher = timeline_refresher
    app.state.dedup_runner = dedup_runner
    app.state.visual_dedup_runner = visual_dedup_runner
    app.state.book_index_runner = book_index_runner

    static_dir = Path(__file__).resolve().parent / "static"

    def _index() -> Index:
        with state_lock:
            return state["index"]  # type: ignore[return-value]

    def _refresh_index() -> Index:
        # Ingest any new local JSON sidecars into DB, then rebuild from DB.
        ingest_to_db(library_root, db)
        idx = build_index_from_db(db, library_root)
        with state_lock:
            state["index"] = idx
        return idx

    def _initial_scan() -> None:
        # If DB has posts, build from DB (fast). Ingest local JSON
        # sidecars only if DB is empty (first run or after wipe).
        try:
            existing = db.posts_count()
            if existing == 0:
                ingest_to_db(library_root, db)
            idx = build_index_from_db(db, library_root)
            with state_lock:
                state["index"] = idx
        except Exception:
            import traceback
            traceback.print_exc()
        finally:
            with state_lock:
                state["scanning"] = False

    if scan_in_background:
        threading.Thread(target=_initial_scan, daemon=True).start()
    else:
        _initial_scan()

    # Backfill perceptual fingerprints for existing books so duplicate detection
    # works against the current shelf. start() spawns its own daemon thread and
    # returns immediately; idempotent (only un-indexed books are processed).
    book_index_runner.start()

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
        listed_keys = db.all_listed_post_keys()
        items = [_post_payload(p) for p in page]
        for item, p in zip(items, page):
            item["in_any_list"] = (p.tweet_id, p.num) in listed_keys
        return JSONResponse(
            {
                "total": total,
                "items": items,
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

    def _after_sync() -> None:
        """Refresh index and auto-run dedup after a successful sync."""
        _refresh_index()
        dedup_runner.start()

    # SyncRunner shares the same serialization lock so prepare_config calls
    # from concurrent gallery-dl users don't trample each other.
    sync_runner = SyncRunner(
        config_path=gallerydl_config_path,
        db=db,
        gdl_lock=unliked_lock,
        library_root=library_root,
        r2_client=r2_client,
        on_complete=_after_sync,
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

        # Sidecar lives next to the media file as "<media>.json". Derive it from
        # the media path rather than target.json_path: the DB-backed index sets
        # json_path == media_path as a placeholder (unused when serving from R2),
        # so target.json_path is unreliable here.
        sidecar = media.with_name(media.name + ".json")
        rel = r2_key_for_path(media, library_root_resolved)

        with contextlib.suppress(FileNotFoundError):
            media.unlink()
        with contextlib.suppress(FileNotFoundError):
            sidecar.unlink()

        # In R2-backed deployments the media lives in R2 (the local copy is
        # deleted after upload), so unlink() above is a no-op there. Purge the
        # R2 object too — otherwise every delete leaks an orphaned media file in
        # the bucket. The object key is the library-relative posix path (== how
        # the sync/upload path derives keys). A failure here must NOT fail the
        # user's delete: the DB row + local files are already gone, so the post
        # has left the index regardless; an orphan is recoverable later.
        if r2_client is not None:
            try:
                r2_client.delete_object(rel)
            except Exception as exc:  # degrade gracefully like other R2 calls
                print(f"[r2] delete_object failed for {rel}: {exc}")

        # Drop the DB row so the post leaves the index for good — otherwise a
        # later sidecar re-ingest (_refresh_index) resurrects it. Lists + hash
        # cache: cascade cleanup so they don't outlive the file. The gallery-dl
        # archive.sqlite is intentionally untouched so the next sync does not
        # re-download this tweet's media.
        db.delete_post(tweet_id, num)
        db.remove_item_from_all_lists(tweet_id, num)
        db.forget_hash(rel)
        _refresh_index()
        return JSONResponse({"deleted": True})

    # --- Lists ---------------------------------------------------------

    @app.get("/api/favorite-authors")
    def fav_authors_get() -> JSONResponse:
        return JSONResponse(db.get_favorite_authors())

    @app.post("/api/favorite-authors")
    def fav_authors_set(body: _FavAuthorsBody) -> JSONResponse:
        db.set_favorite_authors(body.authors)
        return JSONResponse({"saved": True})


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
            if save_result.ok:
                # Refresh the in-memory index so the new file appears in /api/posts
                _refresh_index()
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

    # --- Admin --------------------------------------------------------


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

    # Media/book pages are immutable: a given rel_path always maps to the same
    # bytes (filenames are never reused). So we can cache aggressively and serve
    # cheap 304s without touching R2 — the single biggest reader-speed/R2-cost win.
    _IMMUTABLE_CACHE = "public, max-age=31536000, immutable"

    def _weak_etag(*parts: object) -> str:
        raw = "|".join(str(p) for p in parts)
        return 'W/"' + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16] + '"'

    @app.get("/api/media/{rel_path:path}")
    async def api_media(rel_path: str, request: Request) -> Response:
        """Serve media from R2 when configured, otherwise from the local library."""
        _validate_rel_path(rel_path)
        etag = _weak_etag("media", rel_path)
        # rel_path uniquely identifies immutable content, so the If-None-Match
        # short-circuit needs no R2/disk read at all.
        if request.headers.get("if-none-match") == etag:
            return Response(
                status_code=304,
                headers={"ETag": etag, "Cache-Control": _IMMUTABLE_CACHE},
            )
        if r2_client is not None:
            try:
                content_length, content_type, body_iter = r2_client.stream_object(rel_path)
                headers = {"Cache-Control": _IMMUTABLE_CACHE, "ETag": etag}
                if content_length:
                    headers["content-length"] = str(content_length)
                return StreamingResponse(body_iter, media_type=content_type, headers=headers)
            except Exception:
                # Fall through to local filesystem if key is absent in R2.
                pass
        target = (library_root / rel_path).resolve()
        if not target.is_file():
            raise HTTPException(status_code=404, detail="not found")
        # FileResponse.set_stat_headers uses setdefault, so our ETag is preserved.
        return FileResponse(target, headers={"Cache-Control": _IMMUTABLE_CACHE, "ETag": etag})

    @app.get("/media/{rel_path:path}")
    def media(rel_path: str) -> FileResponse:
        return FileResponse(
            _resolve_under_library(rel_path),
            headers={"Cache-Control": _IMMUTABLE_CACHE},
        )

    @app.get("/thumb/{rel_path:path}")
    def thumb(
        rel_path: str, request: Request, size: int = Query(default=400, ge=64, le=1600)
    ) -> Response:
        _validate_rel_path(rel_path)
        # Thumbnail bytes are deterministic for (rel_path, size) since the source
        # page is immutable; include size so different ?size= values don't collide.
        etag = _weak_etag("thumb", rel_path, size)
        cache_headers = {"Cache-Control": _IMMUTABLE_CACHE, "ETag": etag}
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=cache_headers)
        target = library_root / rel_path
        # Try local file first (fast path, works during sync before R2 upload).
        if target.is_file():
            data = thumbnail_bytes(target.resolve(), size=size)
            if data is not None:
                return Response(content=data, media_type="image/jpeg", headers=cache_headers)
            return FileResponse(target, headers=cache_headers)
        # Local file is gone (uploaded to R2 and deleted) — generate from R2 stream.
        if r2_client is not None:
            try:
                _, _, body_iter = r2_client.stream_object(rel_path)
                raw = b"".join(body_iter)
                data = thumbnail_bytes_from_raw(raw, size=size)
                if data is not None:
                    return Response(content=data, media_type="image/jpeg", headers=cache_headers)
                # Not an image (e.g. video) — serve the raw bytes directly.
                return Response(
                    content=raw, media_type="application/octet-stream", headers=cache_headers
                )
            except Exception:
                pass
        raise HTTPException(status_code=404, detail="not found")

    # --- Books (bookshelf) ------------------------------------------------

    _BOOKS_DIR = "_books"

    @app.get("/api/books")
    def api_books() -> JSONResponse:
        items = db.books()
        fav_ids = db.book_favorite_ids()
        all_tags = {bid: [] for bid in [b.id for b in items]}
        for b in items:
            all_tags[b.id] = db.book_tags(b.id)
        return JSONResponse([
            {"id": b.id, "title": b.title, "cover_path": b.cover_path,
             "page_count": b.page_count, "created_at": b.created_at,
             "is_favorite": b.id in fav_ids, "tags": all_tags[b.id]}
            for b in items
        ])

    @app.get("/api/books/{book_id}")
    def api_book_detail(book_id: int) -> JSONResponse:
        book = db.get_book(book_id)
        if book is None:
            raise HTTPException(status_code=404, detail="book not found")
        pages = db.book_pages(book_id)
        return JSONResponse({
            "id": book.id, "title": book.title, "cover_path": book.cover_path,
            "page_count": book.page_count, "created_at": book.created_at,
            "pages": [{"page_num": p.page_num, "rel_path": p.rel_path,
                       "width": p.width, "height": p.height} for p in pages],
        })

    @app.post("/api/books")
    async def api_create_book(
        title: str = Form(...),
        files: list[UploadFile] = File(...),
    ) -> JSONResponse:
        if not files:
            raise HTTPException(status_code=400, detail="no files provided")

        # Sort files by filename for natural page order
        import re
        def _natural_key(name: str) -> list:
            return [int(c) if c.isdigit() else c.lower() for c in re.split(r'(\d+)', name)]

        sorted_files = sorted(files, key=lambda f: _natural_key(f.filename or ""))

        # Create book record first to get ID
        book = db.create_book(title=title, cover_path=None, page_count=len(sorted_files))

        # Save files to disk
        book_dir = library_root / _BOOKS_DIR / str(book.id)
        book_dir.mkdir(parents=True, exist_ok=True)

        pages: list[tuple[int, str, int | None, int | None]] = []
        cover_rel: str | None = None

        for i, f in enumerate(sorted_files, start=1):
            ext = Path(f.filename or "page.jpg").suffix.lower() or ".jpg"
            filename = f"{i:04d}{ext}"
            dest = book_dir / filename
            content = await f.read()
            dest.write_bytes(content)

            rel = f"{_BOOKS_DIR}/{book.id}/{filename}"
            if i == 1:
                cover_rel = rel

            # Try to get dimensions
            w, h = None, None
            try:
                import io

                from PIL import Image
                img = Image.open(io.BytesIO(content))
                w, h = img.size
            except Exception:
                pass

            pages.append((i, rel, w, h))

        db.add_book_pages(book.id, pages)

        # Update cover path
        if cover_rel:
            with db._lock:
                db._conn.execute("UPDATE books SET cover_path = ? WHERE id = ?", (cover_rel, book.id))

        # Duplicate check (files are still on local disk, nothing uploaded yet).
        import shutil
        ordered_files = [library_root / rel for (_pn, rel, _w, _h) in pages]
        page_count, cover_phash, samples = fingerprint_for_ordered_files(ordered_files)
        dup = find_duplicate_book(db, page_count, cover_phash, samples)
        if dup is not None:
            # Roll back the just-created book + its files; report the match.
            db.delete_book(book.id)  # cascades book_pages/tags/favorites/hashes
            shutil.rmtree(book_dir, ignore_errors=True)
            return JSONResponse(
                {"skipped": True, "matched_book_id": dup[0], "matched_title": dup[1]},
                status_code=200,
            )
        # Not a duplicate: persist its fingerprint so future imports can match it.
        db.upsert_book_hash(
            book_id=book.id,
            page_count=page_count,
            cover_phash=cover_phash,
            sample_phashes=samples,
            indexed_at=int(time.time()),
        )

        # Upload to R2 if configured, then delete local copies
        if r2_client is not None:
            for i, f in enumerate(sorted_files, start=1):
                ext = Path(f.filename or "page.jpg").suffix.lower() or ".jpg"
                filename = f"{i:04d}{ext}"
                local_path = book_dir / filename
                key = f"{_BOOKS_DIR}/{book.id}/{filename}"
                try:
                    r2_client.upload_file(local_path, key)
                    local_path.unlink()
                except Exception:
                    pass  # Keep local if R2 fails
            # Remove empty directory
            if book_dir.exists() and not any(book_dir.iterdir()):
                book_dir.rmdir()

        return JSONResponse({"id": book.id, "title": book.title, "page_count": len(pages)}, status_code=201)

    @app.delete("/api/books/{book_id}")
    def api_delete_book(book_id: int) -> JSONResponse:
        # Delete files from disk
        book_dir = library_root / _BOOKS_DIR / str(book_id)
        if book_dir.exists():
            import shutil
            shutil.rmtree(book_dir, ignore_errors=True)
        deleted = db.delete_book(book_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="book not found")
        return JSONResponse({"deleted": True})

    class _BookPatchBody(BaseModel):
        title: str | None = None

    @app.patch("/api/books/{book_id}")
    def api_patch_book(book_id: int, body: _BookPatchBody) -> JSONResponse:
        if body.title is not None:
            if not db.update_book_title(book_id, body.title.strip()):
                raise HTTPException(status_code=404, detail="book not found")
        book = db.get_book(book_id)
        return JSONResponse({"id": book.id, "title": book.title, "page_count": book.page_count})

    # --- Book Tags & Favorites ---

    @app.get("/api/books/tags")
    def api_book_tags() -> JSONResponse:
        tags = db.all_book_tags()
        return JSONResponse({"tags": [{"name": t[0], "count": t[1]} for t in tags]})

    class _BookTagsBody(BaseModel):
        tags: list[str]

    @app.put("/api/books/{book_id}/tags")
    def api_set_book_tags(book_id: int, body: _BookTagsBody) -> JSONResponse:
        if db.get_book(book_id) is None:
            raise HTTPException(status_code=404, detail="book not found")
        db.set_book_tags(book_id, body.tags)
        return JSONResponse({"ok": True})

    @app.post("/api/books/{book_id}/favorite")
    def api_toggle_book_favorite(book_id: int) -> JSONResponse:
        if db.get_book(book_id) is None:
            raise HTTPException(status_code=404, detail="book not found")
        new_state = db.toggle_book_favorite(book_id)
        return JSONResponse({"favorite": new_state})

    # --- Book Import (URL → gallery-dl → bookshelf) -----------------------

    import_queue: list[dict] = []  # [{id, url, status, error, book_id, title, progress}]
    import_queue_lock = threading.Lock()
    _import_id_counter = [0]

    def _process_next_in_queue() -> None:
        """Start the next pending item in the queue, if any."""
        with import_queue_lock:
            pending = [item for item in import_queue if item["status"] == "pending"]
            if not pending:
                return
            item = pending[0]
            item["status"] = "running"
            item["progress"] = "開始中..."
        threading.Thread(
            target=_import_worker, args=(item,), daemon=True
        ).start()

    # --- Site-specific scrapers ---

    def _scrape_doujin_freee(url: str, tmp_dir: Path) -> list[Path]:
        """doujin-freee.cc 専用: img_gage スライダーから全画像URLを生成して取得"""
        import requests as _requests
        from bs4 import BeautifulSoup

        _headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }

        resp = _requests.get(url, timeout=30, headers=_headers)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # img_gage から画像生成パラメータを抽出
        gage = soup.select_one(".img_gage, [class*=img_gage]")
        if not gage:
            return []

        max_el = gage.select_one("input[type='range']")
        img_b_el = gage.select_one("#img_b")
        img_s_el = gage.select_one("#img_s")
        year_el = gage.select_one("#post_year")
        month_el = gage.select_one("#post_month")

        if not all([max_el, img_b_el, img_s_el, year_el, month_el]):
            return []

        max_pages = int(max_el.get("max", "1"))
        img_b = img_b_el.get("value", "")
        img_s = img_s_el.get("value", "")
        post_year = year_el.get("value", "")
        post_month = month_el.get("value", "")

        # URL パターン: https://img.doujin-freee.cc/thumb640/{year}{month}/{img_b}{img_s}/{img_s}-{page:03d}-640.jpg
        img_urls = [
            f"https://img.doujin-freee.cc/thumb640/{post_year}{post_month}/{img_b}{img_s}/{img_s}-{i:03d}-640.jpg"
            for i in range(1, max_pages + 1)
        ]

        # ダウンロード
        files: list[Path] = []
        for i, img_url in enumerate(img_urls, 1):
            try:
                r = _requests.get(img_url, timeout=30, headers={
                    **_headers, "Referer": url,
                })
                if r.status_code != 200:
                    continue
                dest = tmp_dir / f"{i:04d}.jpg"
                dest.write_bytes(r.content)
                files.append(dest)
            except Exception:
                continue
        return files

    def _scrape_images_from_html(url: str, tmp_dir: Path) -> list[Path]:
        """サイトに応じたスクレイパーを選択して実行"""
        from urllib.parse import urlparse
        host = urlparse(url).netloc.lower()

        # サイト別ルーティング
        if "doujin-freee" in host:
            return _scrape_doujin_freee(url, tmp_dir)

        # 未対応サイト: 汎用フォールバック（1ページ目のみ）
        from urllib.parse import urljoin

        import requests as _requests
        from bs4 import BeautifulSoup

        _headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        resp = _requests.get(url, timeout=30, headers=_headers)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        imgs = soup.select("article img, .entry-content img, .post-content img")
        img_urls = []
        for img in imgs:
            src = img.get("data-src") or img.get("data-lazy-src") or img.get("src")
            if src and not src.split("?")[0].endswith((".svg", ".gif", ".ico")):
                if not src.startswith("http"):
                    src = urljoin(url, src)
                img_urls.append(src)

        seen: set[str] = set()
        files: list[Path] = []
        for i, img_url in enumerate(img_urls, 1):
            if img_url in seen:
                continue
            seen.add(img_url)
            try:
                r = _requests.get(img_url, timeout=30, headers={**_headers, "Referer": url})
                if r.status_code != 200:
                    continue
                ext = Path(img_url.split("?")[0]).suffix.lower() or ".jpg"
                if ext not in {".jpg", ".jpeg", ".png", ".webp", ".avif", ".bmp"}:
                    ext = ".jpg"
                dest = tmp_dir / f"{i:04d}{ext}"
                dest.write_bytes(r.content)
                files.append(dest)
            except Exception:
                continue
        return files

    def _import_worker(item: dict) -> None:
        import re
        import subprocess
        import tempfile

        url = item["url"]
        tmp_dir = None
        try:
            tmp_dir = tempfile.mkdtemp(prefix="book_import_")
            tmp_path = Path(tmp_dir)

            # Try gallery-dl first
            gdl_failed = False
            cfg = build_book_import_config(tmp_dir)
            cfg_path = Path(tmp_dir) / "gdl_config.json"
            cfg_path.write_text(_json.dumps(cfg), encoding="utf-8")

            cmd = ["gallery-dl", "--config", str(cfg_path), url]
            with import_queue_lock:
                item["progress"] = "ダウンロード中..."

            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
                if result.returncode != 0:
                    gdl_failed = True
            except (subprocess.TimeoutExpired, FileNotFoundError):
                gdl_failed = True

            image_exts = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".bmp"}
            all_files: list[Path] = []
            for f in sorted(tmp_path.rglob("*")):
                if f.is_file() and f.suffix.lower() in image_exts:
                    all_files.append(f)

            # Fallback: HTML scraping if gallery-dl failed or found no images
            if not all_files and (gdl_failed or True):
                with import_queue_lock:
                    item["progress"] = "HTML から画像を取得中..."
                try:
                    all_files = _scrape_images_from_html(url, tmp_path)
                except Exception:
                    pass  # fall through to empty check below

            if not all_files:
                with import_queue_lock:
                    item["error"] = "画像が見つかりませんでした"
                    item["status"] = "error"
                return

            # Extract title
            title = "Imported"
            for f in tmp_path.rglob("*.json"):
                try:
                    meta = _json.loads(f.read_text(encoding="utf-8"))
                    if isinstance(meta, dict):
                        title = meta.get("gallery", {}).get("title", "") or meta.get("title", "") or title
                        if title != "Imported":
                            break
                except Exception:
                    pass

            if title == "Imported":
                from urllib.parse import unquote
                parts = unquote(url).rsplit("/", 1)[-1].rsplit(".", 1)[0]
                title = re.sub(r'-\d+$', '', parts).replace('-', ' ').strip() or "Imported"

            with import_queue_lock:
                item["title"] = title
                item["progress"] = "本棚に登録中..."

            def _natural_key(name: str) -> list:
                return [int(c) if c.isdigit() else c.lower() for c in re.split(r'(\d+)', name)]

            all_files.sort(key=lambda f: _natural_key(f.name))

            # Skip if this import duplicates a book already on the shelf. The tmp
            # files still exist here (before create + R2 upload), so we can
            # fingerprint and bail out cleanly without touching the library.
            _pc, _cover_phash, _samples = fingerprint_for_ordered_files(all_files)
            _dup = find_duplicate_book(db, _pc, _cover_phash, _samples)
            if _dup is not None:
                with import_queue_lock:
                    item["status"] = "skipped"
                    item["matched_book_id"] = _dup[0]
                    item["matched_title"] = _dup[1]
                    item["progress"] = "重複のためスキップ"
                return  # finally clause cleans tmp_dir + advances the queue

            book = db.create_book(title=title, cover_path=None, page_count=len(all_files))
            book_dir = library_root / _BOOKS_DIR / str(book.id)
            book_dir.mkdir(parents=True, exist_ok=True)

            pages: list[tuple[int, str, int | None, int | None]] = []
            cover_rel: str | None = None

            for i, src_file in enumerate(all_files, start=1):
                ext = src_file.suffix.lower() or ".jpg"
                filename = f"{i:04d}{ext}"
                dest = book_dir / filename
                import shutil
                shutil.copy2(src_file, dest)

                rel = f"{_BOOKS_DIR}/{book.id}/{filename}"
                if i == 1:
                    cover_rel = rel

                w, h = None, None
                try:
                    from PIL import Image
                    img = Image.open(dest)
                    w, h = img.size
                    img.close()
                except Exception:
                    pass

                pages.append((i, rel, w, h))

            db.add_book_pages(book.id, pages)

            if cover_rel:
                with db._lock:
                    db._conn.execute(
                        "UPDATE books SET cover_path = ? WHERE id = ?", (cover_rel, book.id)
                    )

            # Persist the fingerprint (computed above from tmp files) so future
            # imports can detect duplicates of this book.
            db.upsert_book_hash(
                book_id=book.id,
                page_count=_pc,
                cover_phash=_cover_phash,
                sample_phashes=_samples,
                indexed_at=int(time.time()),
            )

            # Upload to R2 if configured
            if r2_client is not None:
                for _page_num, rel, _w, _h in pages:
                    local_path = library_root / rel
                    if local_path.is_file():
                        try:
                            r2_client.upload_file(local_path, rel)
                            local_path.unlink()
                        except Exception:
                            pass
                if book_dir.exists() and not any(book_dir.iterdir()):
                    book_dir.rmdir()

            with import_queue_lock:
                item["book_id"] = book.id
                item["status"] = "done"
                item["progress"] = "完了"

        except Exception as exc:
            with import_queue_lock:
                item["error"] = f"{type(exc).__name__}: {exc}"
                item["status"] = "error"
        finally:
            if tmp_dir:
                import shutil
                shutil.rmtree(tmp_dir, ignore_errors=True)
            _process_next_in_queue()

    @app.post("/api/books/import")
    def api_import_book(body: _BookImportBody) -> JSONResponse:
        if not body.url.strip():
            raise HTTPException(status_code=400, detail="URL is required")
        with import_queue_lock:
            _import_id_counter[0] += 1
            item = {
                "id": _import_id_counter[0],
                "url": body.url.strip(),
                "status": "pending",
                "error": None,
                "book_id": None,
                "title": "",
                "progress": "待機中...",
                "matched_book_id": None,
                "matched_title": "",
            }
            import_queue.append(item)
            # Start processing if nothing is currently running
            running = any(i["status"] == "running" for i in import_queue)
        if not running:
            _process_next_in_queue()
        return JSONResponse({"started": True, "id": item["id"]})

    @app.get("/api/books/import/status")
    def api_import_status() -> JSONResponse:
        with import_queue_lock:
            return JSONResponse({"queue": list(import_queue)})

    @app.get("/api/books/index/status")
    def api_book_index_status() -> JSONResponse:
        s = book_index_runner.state
        return JSONResponse(
            {
                "running": s.running,
                "books_total": s.books_total,
                "books_indexed": s.books_indexed,
                "last_error": s.last_error,
            }
        )

    # All shared state + collaborators are now constructed; gather them into the
    # AppContext that the extracted routers reach via Depends(get_context).
    # (Inline handlers above still close over the locals; they migrate to
    # routers/ group by group, each switching to ctx.)
    app.state.context = AppContext(
        library_root=library_root,
        library_root_resolved=library_root_resolved,
        cookies_file=cookies_file,
        gallerydl_config_path=gallerydl_config_path,
        fav_authors_path=_fav_authors_path,
        static_dir=static_dir,
        books_dir=_BOOKS_DIR,
        db=db,
        r2_client=r2_client,
        cdn_proxy=cdn_proxy,
        timeline_refresher=timeline_refresher,
        dedup_runner=dedup_runner,
        visual_dedup_runner=visual_dedup_runner,
        book_index_runner=book_index_runner,
        sync_runner=sync_runner,
        _state=state,
        state_lock=state_lock,
        gdl_lock=unliked_lock,
        me_likes_lock=me_likes_lock,
        me_likes_state=me_likes_state,
    )

    app.include_router(sync_router.router)
    app.include_router(admin_router.router)
    app.include_router(dedup_router.router)
    app.include_router(lists_router.router)

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

"""FastAPI app: gallery API + media + sync orchestration + lists + timeline."""

from __future__ import annotations

import base64
import logging
import os
import secrets
import shutil
import threading
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from favgallery.book_dedup import BookIndexRunner
from favgallery.book_importer import BookImportQueue
from favgallery.context import AppContext
from favgallery.db import Database
from favgallery.dedup import DedupRunner, VisualDedupRunner
from favgallery.gallerydl_config import write_gallerydl_config
from favgallery.paths import portable_root
from favgallery.proxy import CdnProxy
from favgallery.r2 import R2Client, r2_config_from_env
from favgallery.routers import admin as admin_router
from favgallery.routers import books as books_router
from favgallery.routers import cookies as cookies_router
from favgallery.routers import dedup as dedup_router
from favgallery.routers import lists as lists_router
from favgallery.routers import me as me_router
from favgallery.routers import media as media_router
from favgallery.routers import posts as posts_router
from favgallery.routers import sync as sync_router
from favgallery.routers import timeline as timeline_router
from favgallery.scanner import (
    DEFAULT_LIBRARY,
    Index,
    build_index_from_db,
    ingest_to_db,
)
from favgallery.sync import SyncRunner
from favgallery.timeline import (
    TimelineRefresher,
)


def _write_cookies_from_env(cookies_path: Path) -> None:
    """Seed cookies.txt from the GALLERY_DL_COOKIES env var, **once**.

    Called at startup so Railway Variables can supply cookies without file
    transfers or CLI commands.  Seed-once semantics: an empty / unset var is
    ignored, and an existing cookies.txt is **never overwritten** — so cookies
    set via the in-app UI (POST /api/cookies, written to the persistent volume)
    survive a container restart instead of being clobbered by a now-stale env
    value. The env var only provisions the file on a fresh volume.
    """
    content = os.environ.get("GALLERY_DL_COOKIES", "")
    if not content:
        return
    if cookies_path.exists():
        return  # UI-managed / volume cookies win; don't clobber on restart.
    cookies_path.parent.mkdir(parents=True, exist_ok=True)
    cookies_path.write_text(content, encoding="utf-8")


def _migrate_legacy_cookies(old_path: Path, new_path: Path) -> None:
    """Move a cookies.txt left at the pre-volume-fix location into ``new_path``.

    Earlier builds stored cookies at ``library_root.parent / cookies.txt`` — one
    level *above* the Railway volume mount (``/data/library``) — so the file sat
    on ephemeral container storage and was wiped on every redeploy, silently
    dropping the X-sync auth. The cookie now lives inside ``library_root`` (on
    the volume); move any legacy file so the transition loses nothing. Best
    effort: a cross-device/permission error just means the user re-sets the
    cookie once. Skipped when ``new_path`` already exists (the volume cookie wins).
    """
    if new_path.exists() or not old_path.exists():
        return
    try:
        new_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(old_path), str(new_path))
    except OSError:
        pass


def _ensure_gallerydl_config(config_path: Path, library_root: Path) -> None:
    """Backwards-compatible shim — see gallerydl_config.write_gallerydl_config."""
    write_gallerydl_config(config_path, library_root)


def _resolve_app_version() -> str:
    """Installed package version — single source of truth is pyproject.toml."""
    try:
        from importlib.metadata import version

        return version("favgallery")
    except Exception:  # PackageNotFoundError 等 — 開発ツリー直実行
        return "dev"


#: Sent as X-App-Version on every response (401 含む) so the deployed version
#: is externally verifiable without credentials (2026-06-10 デプロイ検証盲点).
APP_VERSION = _resolve_app_version()


def _env_first(*names: str) -> str:
    """Return the first non-empty value among the given env var names.

    Rename transition (2026-06-10): new FAVGALLERY_* vars take precedence,
    legacy ARCHIVE_* vars keep working until Railway env is migrated.
    """
    for name in names:
        value = os.environ.get(name, "")
        if value:
            return value
    return ""


def _make_basic_auth_middleware():
    """Return an HTTP Basic auth middleware if FAVGALLERY_USER/FAVGALLERY_PASSWORD
    (or legacy ARCHIVE_USER/ARCHIVE_PASSWORD) are set.

    When the env vars are absent the middleware is a no-op, so development /
    local-desktop use works without configuration.
    Authentication uses secrets.compare_digest to prevent timing attacks.
    """

    async def basic_auth_middleware(request: Request, call_next):
        auth_user = _env_first("FAVGALLERY_USER", "ARCHIVE_USER")
        auth_password = _env_first("FAVGALLERY_PASSWORD", "ARCHIVE_PASSWORD")

        if not auth_user or not auth_password:
            # Auth not configured — pass through (local / dev use).
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Basic "):
            return Response(
                content="Unauthorized",
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="FavGallery"'},
            )

        try:
            decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
            req_user, req_pass = decoded.split(":", 1)
        except Exception:
            return Response(
                content="Unauthorized",
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="FavGallery"'},
            )

        user_ok = secrets.compare_digest(req_user, auth_user)
        pass_ok = secrets.compare_digest(req_pass, auth_password)
        if not (user_ok and pass_ok):
            return Response(
                content="Unauthorized",
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="FavGallery"'},
            )

        return await call_next(request)

    return basic_auth_middleware


def _register_http_shell_middleware(app: FastAPI) -> None:
    """Version stamp + /static cache policy (Phase 6 で create_app から分離).

    Outermost middleware (last added runs first): version-stamp every response,
    including the auth middleware's 401s. /static のキャッシュ方針:
    - style.css は ?v=__APP_VERSION__ 付き参照のため immutable 長期キャッシュ可
    - それ以外 (lib/*.js 含む) は no-cache — ES module の深い import は ?v= を
      運べず、スマホ古キャッシュ事故 (v0.2.3) を構造的に再発させないため
    """

    @app.middleware("http")
    async def add_version_header(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-App-Version"] = APP_VERSION
        path = request.url.path
        if path.startswith("/static/"):
            # 成功応答のみ長期キャッシュ可。401 等に immutable を付けると
            # 認証失敗がキャッシュされ続ける事故になりうる (2026-06-10)。
            ok = response.status_code in (200, 304)
            if path == "/static/style.css" and ok:
                response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            else:
                response.headers["Cache-Control"] = "no-cache"
        return response


def _register_shell_routes(app: FastAPI, static_dir: Path) -> None:
    """SPA シェル配信 + 遠隔診断口 + /static mount (Phase 6 で create_app から分離)."""

    @app.get("/", response_class=HTMLResponse)
    def root() -> HTMLResponse:
        html = (static_dir / "index.html").read_text(encoding="utf-8")
        # 端末側に表示中バージョンを出す (スマホ古キャッシュ診断 / 2026-06-10)
        html = html.replace("__APP_VERSION__", APP_VERSION)
        # no-cache: SPA シェルを端末キャッシュさせない。デプロイ後にスマホが
        # 古い JS を使い回し「直したのに変わらない」が起きた (2026-06-10)。
        return HTMLResponse(html, headers={"Cache-Control": "no-cache"})

    @app.post("/api/client-log")
    async def client_log(request: Request) -> Response:
        """端末 (主にスマホ) の JS エラーを受けてサーバーログに出す遠隔診断口。

        DevTools を開けない端末の「押しても何も起きない」を Railway logs から
        特定するため (2026-06-10)。内容は記録するだけ — 解析も保存もしない。
        """
        try:
            body = (await request.body())[:2000].decode("utf-8", errors="replace")
        except Exception:
            body = "<unreadable>"
        logging.getLogger("favgallery.client").warning("client-log: %s", body)
        return Response(status_code=204)

    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=static_dir), name="static")


def create_app(
    library_root: Path = DEFAULT_LIBRARY,
    *,
    scan_in_background: bool = True,
    r2_client: R2Client | None = None,
) -> FastAPI:
    app = FastAPI(title="FavGallery", version=APP_VERSION)
    app.middleware("http")(_make_basic_auth_middleware())

    _register_http_shell_middleware(app)

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
    # cookies.txt lives INSIDE library_root (the Railway volume mount at
    # FAVGALLERY_LIBRARY_ROOT / legacy ARCHIVE_LIBRARY_ROOT), next to the DB,
    # so the UI-set cookie survives redeploys. The old location
    # (library_root.parent) sat above the volume on ephemeral container
    # storage and was wiped on every redeploy.
    cookies_file = library_root / "cookies.txt"
    _migrate_legacy_cookies(library_root.parent / "cookies.txt", cookies_file)
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
    _ensure_gallerydl_config(gallerydl_config_path, library_root)
    # Serialize gallery-dl invocations: prepare_config touches global state.
    # Shared by SyncRunner, TimelineRefresher, and the unliked author fetch so
    # concurrent runs don't trample the config or mix captured log lines.
    unliked_lock = threading.Lock()
    timeline_refresher = TimelineRefresher(
        db, gallerydl_config_path, gdl_lock=unliked_lock
    )
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

    _register_shell_routes(app, static_dir)

    def _after_sync() -> None:
        """Settle sync results into the DB/index (must run before added is counted)."""
        _refresh_index()

    def _on_new_items(added: int) -> None:
        """Run dedup ONLY when the sync actually added items.

        以前は毎同期 (=毎ページロード) で全ライブラリの SHA-256 + imagehash
        走査が無条件に走っていた (20〜90 秒 CPU / 2026-06-10 Phase 2)。
        visual dedup はフロントが毎回 POST していたものをサーバー所有に移管。
        """
        dedup_runner.start()
        visual_dedup_runner.start()

    # SyncRunner shares the same serialization lock so prepare_config calls
    # from concurrent gallery-dl users don't trample each other.
    sync_runner = SyncRunner(
        config_path=gallerydl_config_path,
        db=db,
        gdl_lock=unliked_lock,
        library_root=library_root,
        r2_client=r2_client,
        on_complete=_after_sync,
        on_new_items=_on_new_items,
    )
    app.state.sync_runner = sync_runner

    library_root_resolved = library_root.resolve()

    # Single-flight orchestrator state for /api/me/likes/sync (in routers/me.py).
    # gallery-dl's global config makes parallel runs unsafe, so the me router
    # serializes through this shared state + lock (held on the AppContext).
    me_likes_state: dict[str, object] = {
        "running": False,
        "last_started": None,
        "last_finished": None,
        "last_error": None,
        "last_added": 0,
    }
    me_likes_lock = threading.Lock()

    # --- Books (bookshelf) — handlers live in routers/books.py -------------
    books_dir = "_books"
    book_import_queue = BookImportQueue(
        db=db,
        library_root=library_root,
        r2_client=r2_client,
        books_dir=books_dir,
    )

    # All shared state + collaborators are now constructed; gather them into the
    # AppContext that the extracted routers reach via Depends(get_context).
    app.state.context = AppContext(
        library_root=library_root,
        library_root_resolved=library_root_resolved,
        cookies_file=cookies_file,
        gallerydl_config_path=gallerydl_config_path,
        static_dir=static_dir,
        books_dir=books_dir,
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
        book_import_queue=book_import_queue,
    )

    app.include_router(sync_router.router)
    app.include_router(admin_router.router)
    app.include_router(dedup_router.router)
    app.include_router(lists_router.router)
    app.include_router(posts_router.router)
    app.include_router(media_router.router)
    app.include_router(timeline_router.router)
    app.include_router(me_router.router)
    app.include_router(books_router.router)
    app.include_router(cookies_router.router)

    return app


# ---------------------------------------------------------------------------
# Module-level ASGI app for direct uvicorn invocation:
#   uvicorn favgallery.server:app --host 0.0.0.0 --port $PORT
#
# Library root is resolved from the FAVGALLERY_LIBRARY_ROOT env var when set
# (Railway production; legacy ARCHIVE_LIBRARY_ROOT still works as fallback),
# falling back to the default portable/installed path.
# ---------------------------------------------------------------------------
def _module_level_app() -> FastAPI:
    library_root_env = _env_first("FAVGALLERY_LIBRARY_ROOT", "ARCHIVE_LIBRARY_ROOT")
    if library_root_env:
        library_root = Path(library_root_env)
    else:
        from favgallery.paths import default_library_root
        library_root = default_library_root()
    return create_app(library_root=library_root)


app = _module_level_app()

"""FastAPI app: gallery API + media + sync orchestration + lists + timeline."""

from __future__ import annotations

import base64
import os
import secrets
import threading
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from xlikes_viewer.book_dedup import BookIndexRunner
from xlikes_viewer.book_importer import BookImportQueue
from xlikes_viewer.context import AppContext
from xlikes_viewer.db import Database
from xlikes_viewer.dedup import DedupRunner, VisualDedupRunner
from xlikes_viewer.gallerydl_config import write_gallerydl_config
from xlikes_viewer.paths import portable_root
from xlikes_viewer.proxy import CdnProxy
from xlikes_viewer.r2 import R2Client, r2_config_from_env
from xlikes_viewer.routers import admin as admin_router
from xlikes_viewer.routers import books as books_router
from xlikes_viewer.routers import dedup as dedup_router
from xlikes_viewer.routers import lists as lists_router
from xlikes_viewer.routers import me as me_router
from xlikes_viewer.routers import media as media_router
from xlikes_viewer.routers import posts as posts_router
from xlikes_viewer.routers import sync as sync_router
from xlikes_viewer.routers import timeline as timeline_router
from xlikes_viewer.scanner import (
    DEFAULT_LIBRARY,
    Index,
    build_index_from_db,
    ingest_to_db,
)
from xlikes_viewer.sync import SyncRunner
from xlikes_viewer.timeline import (
    TimelineRefresher,
)


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
        fav_authors_path=_fav_authors_path,
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

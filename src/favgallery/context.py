"""Shared application context.

Gathers the dependencies and index state that route handlers need into one
object, so routers can pull them via ``Depends(get_context)`` instead of
closing over ``create_app`` locals. This is the seam that lets ``server.py``'s
god-function be split into ``routers/`` modules without changing behaviour.
"""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException, Request

from favgallery.scanner import Index, build_index_from_db, ingest_to_db

if TYPE_CHECKING:
    from favgallery.db import Database
    from favgallery.proxy import CdnProxy
    from favgallery.r2 import R2Client

_MY_USERNAME_KEY = "my_username"


def is_sensitive_name(name: str) -> bool:
    """True if a filename must never be served by the rel_path media routes.

    cookies.txt (full X account-takeover credential), the SQLite databases and
    their -wal/-shm sidecars, dotfiles (incl. the .cookies.*.tmp atomic-write
    temp) all live INSIDE library_root by design — the volume mount is the only
    persistent disk — so path-traversal checks alone cannot keep them private.
    """
    lowered = name.lower()
    if lowered == "cookies.txt" or lowered.startswith("."):
        return True
    return any(
        part in ("sqlite", "sqlite3", "db", "tmp") or part.startswith("sqlite-")
        for part in lowered.split(".")[1:]
    )


@dataclass
class AppContext:
    """Single source of the shared state + collaborators for all routers."""

    # --- paths --------------------------------------------------------------
    library_root: Path
    library_root_resolved: Path
    cookies_file: Path
    gallerydl_config_path: Path
    static_dir: Path
    books_dir: str

    # --- collaborators ------------------------------------------------------
    db: Database
    r2_client: R2Client | None
    cdn_proxy: CdnProxy
    timeline_refresher: Any
    dedup_runner: Any
    visual_dedup_runner: Any
    book_index_runner: Any
    sync_runner: Any

    # --- index state (private: access only via the helpers below) -----------
    _state: dict[str, object]
    state_lock: threading.Lock

    # --- serialization / transient state ------------------------------------
    gdl_lock: threading.Lock  # serializes gallery-dl global-config writes
    me_likes_lock: threading.Lock
    me_likes_state: dict[str, object]

    # --- book import (set once book_importer is wired; None until then) ------
    book_import_queue: Any = None

    # --- constants ----------------------------------------------------------
    immutable_cache: str = "public, max-age=31536000, immutable"

    # --- index helpers ------------------------------------------------------
    def get_index(self) -> Index:
        with self.state_lock:
            return self._state["index"]  # type: ignore[return-value]

    def get_scanning(self) -> bool:
        with self.state_lock:
            return bool(self._state.get("scanning", False))

    def set_index(self, idx: Index) -> None:
        with self.state_lock:
            self._state["index"] = idx

    def refresh_index(self) -> Index:
        """Ingest new local JSON sidecars into the DB, then rebuild the index."""
        ingest_to_db(self.library_root, self.db)
        idx = build_index_from_db(self.db, self.library_root)
        self.set_index(idx)
        return idx

    # --- listed-keys cache (perf Phase 1 / 2026-06-10) -----------------------
    def get_listed_keys(self) -> set[tuple[str, int]]:
        """Cached list-membership keys for /api/posts ``in_any_list``.

        Was a full ``list_items`` table read on EVERY /api/posts request.
        Must be invalidated by every mutation that changes membership:
        list item add/remove, list deletion, post deletion."""
        with self.state_lock:
            cached = self._state.get("listed_keys")
            if cached is None:
                cached = self.db.all_listed_post_keys()
                self._state["listed_keys"] = cached
            return cached  # type: ignore[return-value]

    def invalidate_listed_keys(self) -> None:
        with self.state_lock:
            self._state.pop("listed_keys", None)

    # --- misc helpers -------------------------------------------------------
    def me_username(self) -> str:
        return (self.db.get_setting(_MY_USERNAME_KEY) or "").strip()

    def validate_rel_path(self, rel_path: str) -> None:
        """Raise HTTPException(400) if rel_path tries to escape the library root.

        Sensitive files (cookies.txt / DBs / dotfiles) return 404 — not 403 —
        so their existence is not advertised to the client.
        """
        target = (self.library_root / rel_path).resolve()
        try:
            target.relative_to(self.library_root_resolved)
        except ValueError as e:
            raise HTTPException(status_code=400, detail="path escape") from e
        if is_sensitive_name(target.name):
            raise HTTPException(status_code=404, detail="not found")

    def resolve_under_library(self, rel_path: str) -> Path:
        """Resolve rel_path under the library, guarding against escapes + 404s."""
        target = (self.library_root / rel_path).resolve()
        try:
            target.relative_to(self.library_root_resolved)
        except ValueError as e:
            raise HTTPException(status_code=400, detail="path escape") from e
        if is_sensitive_name(target.name):
            raise HTTPException(status_code=404, detail="not found")
        if not target.is_file():
            raise HTTPException(status_code=404, detail="not found")
        return target

    @staticmethod
    def strong_etag(*parts: object) -> str:
        """Strong ETag for immutable content (media / thumbs).

        Was weak (``W/"…"``) which forces conditional revalidation semantics;
        strong lets browsers cache harder (2026-06-10 perf Phase 1). Old weak
        values held by clients miss once → one full refetch, then re-converge.
        """
        raw = "|".join(str(p) for p in parts)
        return '"' + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16] + '"'


def get_context(request: Request) -> AppContext:
    """FastAPI dependency: the AppContext stored on ``app.state``."""
    return request.app.state.context

"""Fetch X home / following timeline metadata via gallery-dl's Python API."""

from __future__ import annotations

import io
import logging
import re
import threading
import time
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path

from xlikes_viewer.db import Database, TimelinePost
from xlikes_viewer.gallerydl import prepare_config
from xlikes_viewer.gdl_errors import (
    AUTH_FAILURE_MESSAGE,
    capture_gdl_logs,
    detect_auth_failure,
    is_auth_failure,
)
from xlikes_viewer.x_helpers import DISPLAYABLE_MEDIA_TYPES, extract_hashtags

log = logging.getLogger("xlikes_viewer.timeline")

DEFAULT_TIMELINE_URL = "https://x.com/home/following"
REFRESH_COOLDOWN_SECONDS = 60


@dataclass
class RefreshState:
    running: bool = False
    last_started: float | None = None
    last_finished: float | None = None
    last_error: str | None = None
    last_added: int = 0
    auth_error: bool = False  # gallery-dl logged an auth failure (cookies expired)


def _post_from_meta(url: str, meta: dict, fetched_at: int) -> TimelinePost | None:
    """Convert one gallery-dl metadata record into a TimelinePost."""
    tweet_id = meta.get("tweet_id")
    if not tweet_id:
        return None
    author = meta.get("author") or {}
    content = str(meta.get("content", "") or "")
    media_type = str(meta.get("type", "photo") or "photo")
    media_url = url or ""
    thumb_url = media_url
    if media_url and "pbs.twimg.com/media/" in media_url:
        thumb_url = re.sub(r"\?name=\w+", "?name=small", media_url)
        if "?name=" not in thumb_url:
            thumb_url = thumb_url + ("&" if "?" in thumb_url else "?") + "name=small"
    return TimelinePost(
        tweet_id=str(tweet_id),
        num=int(meta.get("num", 1) or 1),
        fetched_at=fetched_at,
        date=str(meta.get("date", "") or ""),
        author_name=str(author.get("name", "") or ""),
        author_nick=str(author.get("nick", "") or ""),
        author_avatar_url=str(author.get("profile_image", "") or ""),
        content=content,
        media_url=media_url,
        thumb_url=thumb_url,
        media_type=media_type,
        width=meta.get("width"),
        height=meta.get("height"),
        favorite_count=int(meta.get("favorite_count", 0) or 0),
        view_count=int(meta.get("view_count", 0) or 0),
        hashtags=extract_hashtags(content),
        favorited=bool(meta.get("favorited", False)),
    )


def fetch_my_liked_tweet_ids(
    gallerydl_config_path: Path,
    self_username: str,
    *,
    range_spec: str = "1-200",
) -> list[str]:
    """Scrape https://x.com/<self>/likes and return distinct tweet IDs.

    Used by /api/me/likes/sync to populate the my_likes cache, which is then
    consulted by the "未いいね" filter so already-liked tweets disappear even
    when they are not in the local archive.
    """
    url = f"https://x.com/{self_username}/likes"
    pairs = fetch_timeline_metadata(
        gallerydl_config_path, url=url, range_spec=range_spec
    )
    seen: list[str] = []
    seen_set: set[str] = set()
    for _u, meta in pairs:
        tid = meta.get("tweet_id")
        if not tid:
            continue
        s = str(tid)
        if s in seen_set:
            continue
        seen.append(s)
        seen_set.add(s)
    return seen


def fetch_author_media_posts(
    gallerydl_config_path: Path,
    author_name: str,
    *,
    range_spec: str = "1-60",
) -> list[TimelinePost]:
    """Fetch media metadata from https://x.com/{author}/media (download-free).

    Returns parsed TimelinePost objects, filtered to displayable types
    (videos are skipped, mirroring the home-timeline path).
    """
    url = f"https://x.com/{author_name}/media"
    pairs = fetch_timeline_metadata(
        gallerydl_config_path, url=url, range_spec=range_spec
    )
    now = int(time.time())
    posts: list[TimelinePost] = []
    for u, meta in pairs:
        post = _post_from_meta(u, meta, now)
        if post is None:
            continue
        if post.media_type not in DISPLAYABLE_MEDIA_TYPES:
            continue
        posts.append(post)
    return posts


def fetch_timeline_metadata(
    gallerydl_config_path: Path,
    *,
    url: str = DEFAULT_TIMELINE_URL,
    range_spec: str = "1-300",
) -> list[tuple[str, dict]]:
    """Run gallery-dl in data-only mode and return (url, kwdict) pairs.

    The gallery-dl CLI's ``--range`` maps to the ``file-range`` config key
    (NOT ``range``). The home/following timeline is mostly retweets, so
    ``twitter.retweets=true`` is required or the extractor paginates forever.
    """
    from gallery_dl import job  # type: ignore[import-untyped]

    prepare_config(
        gallerydl_config_path,
        file_range=range_spec,
        post_range=range_spec,
        archive=None,
        twitter_retweets=True,
    )

    sink = io.StringIO()
    data_job = job.DataJob(url, file=sink)
    data_job.run()
    # DataJob.run() swallows exceptions onto job.exception (it does not log or
    # re-raise). Surface auth failures (missing OR expired cookies — the latter
    # is a message-only AbortExtraction) so callers fail loud instead of
    # silently returning an empty list.
    if is_auth_failure(getattr(data_job, "exception", None)):
        raise data_job.exception
    pairs: list[tuple[str, dict]] = []
    for u, m in zip(data_job.data_urls, data_job.data_meta, strict=False):
        if isinstance(u, str) and isinstance(m, dict):
            pairs.append((u, m))
    return pairs


class TimelineRefresher:
    """Single-flight orchestrator for /api/timeline/refresh."""

    def __init__(
        self,
        db: Database,
        gallerydl_config_path: Path,
        *,
        gdl_lock: threading.Lock | None = None,
    ) -> None:
        self.db = db
        self.gallerydl_config_path = gallerydl_config_path
        self.state = RefreshState()
        self._lock = threading.Lock()
        # Shared with SyncRunner / unliked fetch: serializes gallery-dl global
        # config writes and scopes log capture so concurrent runs don't mix.
        self._gdl_lock = gdl_lock

    def _can_start_locked(self) -> tuple[bool, str | None]:
        if self.state.running:
            return False, "already running"
        if self.state.last_finished is not None:
            age = time.time() - self.state.last_finished
            if age < REFRESH_COOLDOWN_SECONDS:
                wait = int(REFRESH_COOLDOWN_SECONDS - age)
                return False, f"cooldown, retry in {wait}s"
        return True, None

    def can_start(self) -> tuple[bool, str | None]:
        with self._lock:
            return self._can_start_locked()

    def start(self, *, url: str = DEFAULT_TIMELINE_URL, range_spec: str = "1-300") -> bool:
        with self._lock:
            ok, _reason = self._can_start_locked()
            if not ok:
                return False
            self.state.running = True
            self.state.last_started = time.time()
            self.state.last_error = None
            self.state.last_added = 0
            self.state.auth_error = False
        threading.Thread(
            target=self._worker,
            args=(url, range_spec),
            daemon=True,
        ).start()
        return True

    def _worker(self, url: str, range_spec: str) -> None:
        added = 0
        auth = False
        gdl_ctx = self._gdl_lock if self._gdl_lock is not None else nullcontext()
        try:
            # Hold gdl_lock around the gallery-dl run so a concurrent sync can't
            # race the shared config write or leak log lines into our capture.
            with gdl_ctx, capture_gdl_logs() as gdl_logs:
                pairs = fetch_timeline_metadata(
                    self.gallerydl_config_path, url=url, range_spec=range_spec
                )
            auth = detect_auth_failure(gdl_logs)
            now = int(time.time())
            for u, meta in pairs:
                post = _post_from_meta(u, meta, now)
                if post is None:
                    continue
                if post.media_type not in DISPLAYABLE_MEDIA_TYPES:
                    continue
                self.db.upsert_timeline_post(post)
                added += 1
        except Exception as exc:
            # fetch_timeline_metadata re-raises gallery-dl auth failures
            # (DataJob stores them silently otherwise) — flag them for the UI,
            # by type (AuthRequired) or message (expired-cookie AbortExtraction).
            auth = auth or is_auth_failure(exc)
            log.exception("timeline refresh failed")
            with self._lock:
                self.state.last_error = f"{type(exc).__name__}: {exc}"
        finally:
            with self._lock:
                self.state.running = False
                self.state.last_finished = time.time()
                self.state.last_added = added
                self.state.auth_error = auth
                if auth and not self.state.last_error:
                    self.state.last_error = AUTH_FAILURE_MESSAGE

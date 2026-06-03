"""Walk the X-Likes folder, parse gallery-dl JSON, and build an in-memory index."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from xlikes_viewer.x_helpers import extract_hashtags

if TYPE_CHECKING:
    from xlikes_viewer.db import Database


def _resolve_default_library() -> Path:
    from xlikes_viewer.paths import default_library_root

    return default_library_root()


DEFAULT_LIBRARY = _resolve_default_library()


@dataclass(frozen=True)
class Post:
    """One downloaded media file with its metadata."""

    tweet_id: str  # str so JS can handle big ints losslessly
    num: int
    media_path: Path  # absolute path to the .jpg/.mp4/etc.
    json_path: Path  # absolute path to the sidecar .json
    media_type: str  # "photo" / "video" / "animated_gif"
    extension: str
    width: int | None
    height: int | None
    date: str  # ISO-ish "YYYY-MM-DD HH:MM:SS"
    author_name: str  # X handle, e.g. "butcha_u"
    author_nick: str  # display name
    content: str
    favorite_count: int
    view_count: int
    sensitive: bool
    lang: str
    hashtags: tuple[str, ...]
    rel_media: str  # path relative to library root, forward-slashed (for URLs)


@dataclass(frozen=True)
class AuthorSummary:
    name: str
    nick: str
    post_count: int


@dataclass
class Index:
    library_root: Path
    posts: list[Post] = field(default_factory=list)
    authors: dict[str, AuthorSummary] = field(default_factory=dict)
    tags: dict[str, int] = field(default_factory=dict)  # tag -> post count

    def filter(
        self,
        *,
        author: str | None = None,
        tag: str | None = None,
        media_type: str | None = None,
        query: str | None = None,
    ) -> list[Post]:
        out = self.posts
        if author:
            out = [p for p in out if p.author_name == author]
        if tag:
            out = [p for p in out if tag in p.hashtags]
        if media_type:
            if media_type == "video":
                out = [p for p in out if p.extension in ("mp4", "mov", "webm")]
            elif media_type == "photo":
                out = [p for p in out if p.extension not in ("mp4", "mov", "webm")]
            else:
                out = [p for p in out if p.media_type == media_type]
        if query:
            q = query.lower()
            out = [
                p
                for p in out
                if q in p.content.lower()
                or q in p.author_name.lower()
                or q in p.author_nick.lower()
                or q in " ".join(p.hashtags).lower()
            ]
        return out


def _parse_json(json_path: Path) -> Post | None:
    """Parse one *.json sidecar into a Post, or return None on malformed input."""
    try:
        raw = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    extension = raw.get("extension")
    tweet_id = raw.get("tweet_id")
    num = raw.get("num", 1)
    if not extension or not tweet_id:
        return None

    media_path = json_path.parent / f"{tweet_id}_{num}.{extension}"
    # Media file may be absent locally when stored in R2 — do not skip.
    author = raw.get("author") or {}
    content = str(raw.get("content", "") or "")
    rel = media_path.relative_to(json_path.parents[1]).as_posix()

    return Post(
        tweet_id=str(tweet_id),
        num=int(num),
        media_path=media_path,
        json_path=json_path,
        media_type=raw.get("type", "photo"),
        extension=str(extension).lower(),
        width=raw.get("width"),
        height=raw.get("height"),
        date=str(raw.get("date", "")),
        author_name=str(author.get("name", "unknown")),
        author_nick=str(author.get("nick", "")),
        content=content,
        favorite_count=int(raw.get("favorite_count", 0) or 0),
        view_count=int(raw.get("view_count", 0) or 0),
        sensitive=bool(raw.get("sensitive", False)),
        lang=str(raw.get("lang", "")),
        hashtags=extract_hashtags(content),
        rel_media=rel,
    )


def scan_library(root: Path = DEFAULT_LIBRARY) -> Index:
    """Walk `root` recursively for *.json sidecars and assemble an Index."""
    index = Index(library_root=root)
    if not root.exists():
        return index

    for json_path in root.rglob("*.json"):
        post = _parse_json(json_path)
        if post is None:
            continue
        index.posts.append(post)

    # Sort newest-first (date string is sortable).
    index.posts.sort(key=lambda p: p.date, reverse=True)

    author_counts: dict[str, int] = {}
    author_nicks: dict[str, str] = {}
    tag_counts: dict[str, int] = {}
    for p in index.posts:
        author_counts[p.author_name] = author_counts.get(p.author_name, 0) + 1
        if p.author_nick and p.author_name not in author_nicks:
            author_nicks[p.author_name] = p.author_nick
        for t in p.hashtags:
            tag_counts[t] = tag_counts.get(t, 0) + 1

    index.authors = {
        name: AuthorSummary(
            name=name,
            nick=author_nicks.get(name, ""),
            post_count=count,
        )
        for name, count in sorted(author_counts.items(), key=lambda kv: -kv[1])
    }
    index.tags = dict(sorted(tag_counts.items(), key=lambda kv: -kv[1]))
    return index


_SIDECAR_KEY_RE = re.compile(r"^(\d+)_(\d+)\.[^.]+\.json$")


def ingest_to_db(root: Path, db: Database) -> int:
    """Walk local JSON sidecars and upsert *new* posts into the DB.

    Incremental: posts already present in the DB are recognised from the
    sidecar filename (``{tweet_id}_{num}.{ext}.json``) and skipped without
    reading or upserting the file. This keeps the post-sync refresh O(new
    files) instead of O(library size) — a full re-ingest of ~12k unchanged
    sidecars on a network volume took ~9 min on every sync before this. Files
    whose name doesn't match the expected pattern still fall back to a parse +
    key check, so correctness never depends on the filename shortcut.

    Returns the number of newly ingested posts.
    """
    if not root.exists():
        return 0
    existing = db.all_post_keys()
    count = 0
    for json_path in root.rglob("*.json"):
        m = _SIDECAR_KEY_RE.match(json_path.name)
        if m is not None and (m.group(1), int(m.group(2))) in existing:
            continue  # already ingested — skip the file read + upsert entirely
        post = _parse_json(json_path)
        if post is None:
            continue
        if (post.tweet_id, post.num) in existing:
            continue  # name didn't match but the parsed key is known — skip
        db.upsert_post(
            tweet_id=post.tweet_id,
            num=post.num,
            rel_media=post.rel_media,
            media_type=post.media_type,
            extension=post.extension,
            width=post.width,
            height=post.height,
            date=post.date,
            author_name=post.author_name,
            author_nick=post.author_nick,
            content=post.content,
            favorite_count=post.favorite_count,
            view_count=post.view_count,
            sensitive=post.sensitive,
            lang=post.lang,
            hashtags=post.hashtags,
        )
        existing.add((post.tweet_id, post.num))
        count += 1
    return count


def build_index_from_db(db: Database, library_root: Path) -> Index:
    """Build an Index from the DB posts table instead of filesystem scan."""
    index = Index(library_root=library_root)
    rows = db.all_posts()
    for r in rows:
        tweet_id, num, rel_media, media_type, extension, width, height, \
            date, author_name, author_nick, content, \
            favorite_count, view_count, sensitive, lang, hashtags_json = r
        hashtags = tuple(json.loads(hashtags_json or "[]"))
        media_path = library_root / rel_media.replace("/", os.sep)
        post = Post(
            tweet_id=str(tweet_id),
            num=int(num),
            media_path=media_path,
            json_path=media_path,  # not used when serving from R2
            media_type=media_type or "photo",
            extension=(extension or "").lower(),
            width=width,
            height=height,
            date=date or "",
            author_name=author_name or "unknown",
            author_nick=author_nick or "",
            content=content or "",
            favorite_count=int(favorite_count or 0),
            view_count=int(view_count or 0),
            sensitive=bool(sensitive),
            lang=lang or "",
            hashtags=hashtags,
            rel_media=rel_media,
        )
        index.posts.append(post)

    # posts already sorted by date DESC from SQL
    author_counts: dict[str, int] = {}
    author_nicks: dict[str, str] = {}
    tag_counts: dict[str, int] = {}
    for p in index.posts:
        author_counts[p.author_name] = author_counts.get(p.author_name, 0) + 1
        if p.author_nick and p.author_name not in author_nicks:
            author_nicks[p.author_name] = p.author_nick
        for t in p.hashtags:
            tag_counts[t] = tag_counts.get(t, 0) + 1

    index.authors = {
        name: AuthorSummary(
            name=name,
            nick=author_nicks.get(name, ""),
            post_count=count,
        )
        for name, count in sorted(author_counts.items(), key=lambda kv: -kv[1])
    }
    index.tags = dict(sorted(tag_counts.items(), key=lambda kv: -kv[1]))
    return index

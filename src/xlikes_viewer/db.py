"""SQLite layer for lists and the home-timeline cache."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS lists (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS list_items (
    list_id INTEGER NOT NULL REFERENCES lists(id) ON DELETE CASCADE,
    tweet_id TEXT NOT NULL,
    num INTEGER NOT NULL,
    added_at INTEGER NOT NULL,
    PRIMARY KEY (list_id, tweet_id, num)
);
CREATE INDEX IF NOT EXISTS list_items_post ON list_items(tweet_id, num);

CREATE TABLE IF NOT EXISTS timeline_posts (
    tweet_id TEXT NOT NULL,
    num INTEGER NOT NULL,
    fetched_at INTEGER NOT NULL,
    date TEXT,
    author_name TEXT,
    author_nick TEXT,
    author_avatar_url TEXT,
    content TEXT,
    media_url TEXT,
    thumb_url TEXT,
    media_type TEXT,
    width INTEGER, height INTEGER,
    favorite_count INTEGER, view_count INTEGER,
    hashtags_json TEXT,
    PRIMARY KEY (tweet_id, num)
);
CREATE INDEX IF NOT EXISTS timeline_posts_date ON timeline_posts(date DESC);

CREATE TABLE IF NOT EXISTS media_hashes (
    rel_path TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL,
    size INTEGER NOT NULL,
    mtime INTEGER NOT NULL,
    indexed_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS media_hashes_sha ON media_hashes(sha256);

CREATE TABLE IF NOT EXISTS dedup_log (
    id INTEGER PRIMARY KEY,
    deleted_path TEXT NOT NULL,
    kept_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    deleted_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS dedup_log_when ON dedup_log(deleted_at DESC);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Tweet IDs the authenticated user has already liked on X. Populated by
-- POST /api/me/likes/sync (gallery-dl scrape of https://x.com/<self>/likes).
-- Used to suppress already-liked posts from the "未いいね" feed.
CREATE TABLE IF NOT EXISTS my_likes (
    tweet_id TEXT PRIMARY KEY,
    fetched_at INTEGER NOT NULL
);
"""


@dataclass(frozen=True)
class ListSummary:
    id: int
    name: str
    created_at: int
    item_count: int


@dataclass(frozen=True)
class TimelinePost:
    tweet_id: str
    num: int
    fetched_at: int
    date: str
    author_name: str
    author_nick: str
    author_avatar_url: str
    content: str
    media_url: str
    thumb_url: str
    media_type: str
    width: int | None
    height: int | None
    favorite_count: int
    view_count: int
    hashtags: tuple[str, ...]
    # Whether the authenticated user has already liked this tweet on X. Only
    # populated by ad-hoc author/media fetches (the home-timeline path leaves
    # this False because it is not stored in DB). Used to hide already-liked
    # posts from the "未いいね" feed.
    favorited: bool = False


class Database:
    """Thread-safe SQLite wrapper. One connection guarded by a lock."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            path,
            detect_types=sqlite3.PARSE_DECLTYPES,
            check_same_thread=False,
            isolation_level=None,  # autocommit; use explicit transactions below
        )
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """Apply additive schema migrations that cannot go in SCHEMA (ALTER TABLE)."""
        import contextlib
        with self._lock:
            with contextlib.suppress(Exception):
                self._conn.execute("ALTER TABLE media_hashes ADD COLUMN phash TEXT")
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS media_hashes_phash ON media_hashes(phash)"
            )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # --- Lists ---------------------------------------------------------

    def lists(self) -> list[ListSummary]:
        sql = """
            SELECT l.id, l.name, l.created_at, COUNT(li.tweet_id) AS n
              FROM lists l
              LEFT JOIN list_items li ON li.list_id = l.id
          GROUP BY l.id, l.name, l.created_at
          ORDER BY l.created_at ASC
        """
        with self._lock:
            rows = self._conn.execute(sql).fetchall()
        return [ListSummary(id=r[0], name=r[1], created_at=r[2], item_count=r[3]) for r in rows]

    def create_list(self, name: str) -> ListSummary:
        name = name.strip()
        if not name:
            raise ValueError("name must be non-empty")
        now = int(time.time())
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO lists (name, created_at) VALUES (?, ?)", (name, now)
            )
            list_id = cur.lastrowid
        return ListSummary(id=int(list_id or 0), name=name, created_at=now, item_count=0)

    def delete_list(self, list_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM lists WHERE id = ?", (list_id,))
            return cur.rowcount > 0

    def add_item(self, list_id: int, tweet_id: str, num: int) -> bool:
        now = int(time.time())
        with self._lock:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO list_items (list_id, tweet_id, num, added_at) "
                "VALUES (?, ?, ?, ?)",
                (list_id, tweet_id, num, now),
            )
            return cur.rowcount > 0

    def remove_item(self, list_id: int, tweet_id: str, num: int) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM list_items WHERE list_id = ? AND tweet_id = ? AND num = ?",
                (list_id, tweet_id, num),
            )
            return cur.rowcount > 0

    def remove_item_from_all_lists(self, tweet_id: str, num: int) -> int:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM list_items WHERE tweet_id = ? AND num = ?",
                (tweet_id, num),
            )
            return cur.rowcount

    def lists_for_post(self, tweet_id: str, num: int) -> list[int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT list_id FROM list_items WHERE tweet_id = ? AND num = ?",
                (tweet_id, num),
            ).fetchall()
        return [r[0] for r in rows]

    def posts_in_list(self, list_id: int) -> set[tuple[str, int]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT tweet_id, num FROM list_items WHERE list_id = ?", (list_id,)
            ).fetchall()
        return {(r[0], int(r[1])) for r in rows}

    # --- Timeline cache ------------------------------------------------

    def upsert_timeline_post(self, post: TimelinePost) -> None:
        sql = """
            INSERT INTO timeline_posts
              (tweet_id, num, fetched_at, date, author_name, author_nick, author_avatar_url,
               content, media_url, thumb_url, media_type, width, height,
               favorite_count, view_count, hashtags_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(tweet_id, num) DO UPDATE SET
                fetched_at=excluded.fetched_at,
                date=excluded.date,
                author_name=excluded.author_name,
                author_nick=excluded.author_nick,
                author_avatar_url=excluded.author_avatar_url,
                content=excluded.content,
                media_url=excluded.media_url,
                thumb_url=excluded.thumb_url,
                media_type=excluded.media_type,
                width=excluded.width,
                height=excluded.height,
                favorite_count=excluded.favorite_count,
                view_count=excluded.view_count,
                hashtags_json=excluded.hashtags_json
        """
        params = (
            post.tweet_id,
            post.num,
            post.fetched_at,
            post.date,
            post.author_name,
            post.author_nick,
            post.author_avatar_url,
            post.content,
            post.media_url,
            post.thumb_url,
            post.media_type,
            post.width,
            post.height,
            post.favorite_count,
            post.view_count,
            json.dumps(list(post.hashtags), ensure_ascii=False),
        )
        with self._lock:
            self._conn.execute(sql, params)

    # --- Media hashes / dedup ------------------------------------------

    def known_hash(self, rel_path: str) -> tuple[str, int, int] | None:
        """Return (sha256, size, mtime) for a previously-hashed path, or None."""
        with self._lock:
            row = self._conn.execute(
                "SELECT sha256, size, mtime FROM media_hashes WHERE rel_path = ?",
                (rel_path,),
            ).fetchone()
        return (row[0], int(row[1]), int(row[2])) if row else None

    def upsert_hash(
        self, *, rel_path: str, sha256: str, size: int, mtime: int, indexed_at: int
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO media_hashes (rel_path, sha256, size, mtime, indexed_at) "
                "VALUES (?,?,?,?,?) "
                "ON CONFLICT(rel_path) DO UPDATE SET "
                "sha256=excluded.sha256, size=excluded.size, "
                "mtime=excluded.mtime, indexed_at=excluded.indexed_at",
                (rel_path, sha256, size, mtime, indexed_at),
            )

    def upsert_phash(self, *, rel_path: str, phash: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE media_hashes SET phash=? WHERE rel_path=?",
                (phash, rel_path),
            )

    def all_phashes(self) -> list[tuple[str, str, int]]:
        """Return [(rel_path, phash, indexed_at), ...] for all rows with a phash."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT rel_path, phash, indexed_at FROM media_hashes "
                "WHERE phash IS NOT NULL ORDER BY indexed_at ASC"
            ).fetchall()
        return [(r[0], r[1], int(r[2])) for r in rows]

    def forget_hash(self, rel_path: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM media_hashes WHERE rel_path = ?", (rel_path,))

    def duplicate_groups(self) -> list[list[tuple[str, int]]]:
        """Return [[(rel_path, indexed_at), ...], ...] for every sha256 with > 1 entry."""
        sql = """
            SELECT sha256, rel_path, indexed_at
              FROM media_hashes
             WHERE sha256 IN (
                 SELECT sha256 FROM media_hashes
                  GROUP BY sha256 HAVING COUNT(*) > 1
             )
          ORDER BY sha256, indexed_at ASC, rel_path ASC
        """
        with self._lock:
            rows = self._conn.execute(sql).fetchall()
        groups: dict[str, list[tuple[str, int]]] = {}
        for sha, rel, idx_at in rows:
            groups.setdefault(sha, []).append((rel, int(idx_at)))
        return list(groups.values())

    def log_dedup(self, *, deleted_path: str, kept_path: str, sha256: str, when: int) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO dedup_log (deleted_path, kept_path, sha256, deleted_at) "
                "VALUES (?, ?, ?, ?)",
                (deleted_path, kept_path, sha256, when),
            )

    def dedup_log_count(self) -> int:
        with self._lock:
            (n,) = self._conn.execute("SELECT COUNT(*) FROM dedup_log").fetchone()
        return int(n)

    # --- Settings ------------------------------------------------------

    def get_setting(self, key: str) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_setting(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    # --- My-likes cache ------------------------------------------------

    def upsert_my_likes(self, tweet_ids: list[str]) -> int:
        """Bulk-insert tweet IDs into my_likes; return the count newly added."""
        if not tweet_ids:
            return 0
        now = int(time.time())
        added = 0
        with self._lock:
            for tid in tweet_ids:
                cur = self._conn.execute(
                    "INSERT OR IGNORE INTO my_likes (tweet_id, fetched_at) VALUES (?, ?)",
                    (str(tid), now),
                )
                if cur.rowcount > 0:
                    added += 1
        return added

    def my_likes_ids(self) -> set[str]:
        with self._lock:
            rows = self._conn.execute("SELECT tweet_id FROM my_likes").fetchall()
        return {r[0] for r in rows}

    def my_likes_count(self) -> int:
        with self._lock:
            (n,) = self._conn.execute("SELECT COUNT(*) FROM my_likes").fetchone()
        return int(n)

    def clear_my_likes(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM my_likes")

    def select_timeline_posts_by_tweet(self, tweet_id: str) -> list[TimelinePost]:
        """All timeline rows for a given tweet, sorted by num ascending."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT tweet_id, num, fetched_at, date, author_name, author_nick, "
                "author_avatar_url, content, media_url, thumb_url, media_type, "
                "width, height, favorite_count, view_count, hashtags_json "
                "FROM timeline_posts "
                "WHERE tweet_id = ? "
                "ORDER BY num ASC",
                (tweet_id,),
            ).fetchall()
        return [
            TimelinePost(
                tweet_id=r[0],
                num=r[1],
                fetched_at=r[2],
                date=r[3] or "",
                author_name=r[4] or "",
                author_nick=r[5] or "",
                author_avatar_url=r[6] or "",
                content=r[7] or "",
                media_url=r[8] or "",
                thumb_url=r[9] or "",
                media_type=r[10] or "photo",
                width=r[11],
                height=r[12],
                favorite_count=r[13] or 0,
                view_count=r[14] or 0,
                hashtags=tuple(json.loads(r[15] or "[]")),
            )
            for r in rows
        ]

    def list_timeline_posts(
        self,
        *,
        limit: int,
        offset: int,
        media_type: str | None = None,
        exclude_liked: bool = False,
    ) -> tuple[int, list[TimelinePost]]:
        clauses: list[str] = []
        args_count: list[object] = []
        args_page: list[object] = []
        if media_type == "video":
            clauses.append("media_type = 'video'")
        elif media_type:
            clauses.append("media_type = ?")
            args_count.append(media_type)
            args_page.append(media_type)
        if exclude_liked:
            clauses.append("tweet_id NOT IN (SELECT tweet_id FROM my_likes)")
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        args_page += [limit, offset]
        with self._lock:
            (total,) = self._conn.execute(
                f"SELECT COUNT(*) FROM timeline_posts {where}", args_count
            ).fetchone()
            rows = self._conn.execute(
                "SELECT tweet_id, num, fetched_at, date, author_name, author_nick, "
                "author_avatar_url, content, media_url, thumb_url, media_type, "
                "width, height, favorite_count, view_count, hashtags_json "
                f"FROM timeline_posts {where} "
                "ORDER BY CAST(tweet_id AS INTEGER) DESC, num ASC "
                "LIMIT ? OFFSET ?",
                args_page,
            ).fetchall()
        posts = [
            TimelinePost(
                tweet_id=r[0],
                num=r[1],
                fetched_at=r[2],
                date=r[3] or "",
                author_name=r[4] or "",
                author_nick=r[5] or "",
                author_avatar_url=r[6] or "",
                content=r[7] or "",
                media_url=r[8] or "",
                thumb_url=r[9] or "",
                media_type=r[10] or "photo",
                width=r[11],
                height=r[12],
                favorite_count=r[13] or 0,
                view_count=r[14] or 0,
                hashtags=tuple(json.loads(r[15] or "[]")),
            )
            for r in rows
        ]
        return int(total), posts

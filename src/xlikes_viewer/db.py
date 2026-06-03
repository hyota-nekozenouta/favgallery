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

CREATE TABLE IF NOT EXISTS posts (
    tweet_id TEXT NOT NULL,
    num INTEGER NOT NULL,
    rel_media TEXT NOT NULL,
    media_type TEXT NOT NULL,
    extension TEXT NOT NULL,
    width INTEGER,
    height INTEGER,
    date TEXT,
    author_name TEXT NOT NULL,
    author_nick TEXT,
    content TEXT,
    favorite_count INTEGER DEFAULT 0,
    view_count INTEGER DEFAULT 0,
    sensitive INTEGER DEFAULT 0,
    lang TEXT,
    hashtags_json TEXT,
    PRIMARY KEY (tweet_id, num)
);
CREATE INDEX IF NOT EXISTS posts_date ON posts(date DESC);
CREATE INDEX IF NOT EXISTS posts_author ON posts(author_name);

CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    cover_path TEXT,
    page_count INTEGER DEFAULT 0,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS book_pages (
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    page_num INTEGER NOT NULL,
    rel_path TEXT NOT NULL,
    width INTEGER,
    height INTEGER,
    PRIMARY KEY (book_id, page_num)
);

CREATE TABLE IF NOT EXISTS book_tags (
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    tag TEXT NOT NULL,
    PRIMARY KEY (book_id, tag)
);
CREATE INDEX IF NOT EXISTS book_tags_tag ON book_tags(tag);

CREATE TABLE IF NOT EXISTS book_favorites (
    book_id INTEGER PRIMARY KEY REFERENCES books(id) ON DELETE CASCADE,
    added_at REAL NOT NULL
);

-- Per-book perceptual fingerprint, used to skip duplicate books at import time.
-- cover_phash = pHash of page 1; sample_phashes = JSON [[page_num, phash], ...]
-- for a few sampled interior pages. Independent of media_hashes because book
-- pages are uploaded to R2 and deleted locally (never enter media_hashes).
CREATE TABLE IF NOT EXISTS book_hashes (
    book_id        INTEGER PRIMARY KEY REFERENCES books(id) ON DELETE CASCADE,
    page_count     INTEGER NOT NULL,
    cover_phash    TEXT,
    sample_phashes TEXT NOT NULL,
    indexed_at     INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS book_hashes_pc ON book_hashes(page_count);
"""


@dataclass(frozen=True)
class BookSummary:
    id: int
    title: str
    cover_path: str | None
    page_count: int
    created_at: float


@dataclass(frozen=True)
class BookPage:
    book_id: int
    page_num: int
    rel_path: str
    width: int | None
    height: int | None


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

    def all_listed_post_keys(self) -> set[tuple[str, int]]:
        """Return the set of (tweet_id, num) that appear in any list."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT tweet_id, num FROM list_items"
            ).fetchall()
        return {(r[0], int(r[1])) for r in rows}

    def get_favorite_authors(self) -> list[str]:
        """Return the saved favorite author list (stored in settings table)."""
        import json as _json
        raw = self.get_setting("favorite_authors")
        if raw:
            try:
                return _json.loads(raw)
            except Exception:
                pass
        return []

    def set_favorite_authors(self, authors: list[str]) -> None:
        """Persist the favorite author list to the settings table."""
        import json as _json
        self.set_setting("favorite_authors", _json.dumps(authors, ensure_ascii=False))

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

    # --- Posts (likes index) ------------------------------------------------

    def upsert_post(
        self,
        *,
        tweet_id: str,
        num: int,
        rel_media: str,
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
        hashtags: tuple[str, ...] | list[str],
    ) -> None:
        sql = """
            INSERT INTO posts
              (tweet_id, num, rel_media, media_type, extension, width, height,
               date, author_name, author_nick, content,
               favorite_count, view_count, sensitive, lang, hashtags_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(tweet_id, num) DO UPDATE SET
                rel_media=excluded.rel_media,
                media_type=excluded.media_type,
                extension=excluded.extension,
                width=excluded.width,
                height=excluded.height,
                date=excluded.date,
                author_name=excluded.author_name,
                author_nick=excluded.author_nick,
                content=excluded.content,
                favorite_count=excluded.favorite_count,
                view_count=excluded.view_count,
                sensitive=excluded.sensitive,
                lang=excluded.lang,
                hashtags_json=excluded.hashtags_json
        """
        with self._lock:
            self._conn.execute(sql, (
                tweet_id, num, rel_media, media_type, extension,
                width, height, date, author_name, author_nick, content,
                favorite_count, view_count, int(sensitive), lang,
                json.dumps(list(hashtags), ensure_ascii=False),
            ))

    def all_posts(self) -> list[tuple]:
        """Return all posts as raw tuples for Index construction."""
        sql = """
            SELECT tweet_id, num, rel_media, media_type, extension,
                   width, height, date, author_name, author_nick, content,
                   favorite_count, view_count, sensitive, lang, hashtags_json
            FROM posts ORDER BY date DESC
        """
        with self._lock:
            return self._conn.execute(sql).fetchall()

    def posts_count(self) -> int:
        with self._lock:
            (n,) = self._conn.execute("SELECT COUNT(*) FROM posts").fetchone()
        return int(n)

    def all_post_keys(self) -> set[tuple[str, int]]:
        """Return the set of ``(tweet_id, num)`` keys already in the index.

        A cheap membership source for incremental ingest: the sidecar walk can
        skip posts already present without reading or upserting every file.
        Only two columns are fetched, so this stays fast even for large
        libraries (where a full re-ingest was the post-sync bottleneck).
        """
        with self._lock:
            rows = self._conn.execute("SELECT tweet_id, num FROM posts").fetchall()
        return {(str(tweet_id), int(num)) for tweet_id, num in rows}

    def delete_post(self, tweet_id: str, num: int) -> bool:
        """Remove a single post row. Returns True if a row was deleted.

        Needed so a delete leaves the DB-backed index for good — without it,
        build_index_from_db keeps returning the post (and a later sidecar
        re-ingest would resurrect it).
        """
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM posts WHERE tweet_id = ? AND num = ?", (tweet_id, num)
            )
            return cur.rowcount > 0

    # --- Books (bookshelf) ------------------------------------------------

    def books(self) -> list[BookSummary]:
        sql = (
            "SELECT id, title, cover_path, page_count, created_at "
            "FROM books ORDER BY created_at DESC"
        )
        with self._lock:
            rows = self._conn.execute(sql).fetchall()
        return [
            BookSummary(id=r[0], title=r[1], cover_path=r[2], page_count=r[3], created_at=r[4])
            for r in rows
        ]

    def get_book(self, book_id: int) -> BookSummary | None:
        sql = "SELECT id, title, cover_path, page_count, created_at FROM books WHERE id = ?"
        with self._lock:
            r = self._conn.execute(sql, (book_id,)).fetchone()
        if r is None:
            return None
        return BookSummary(id=r[0], title=r[1], cover_path=r[2], page_count=r[3], created_at=r[4])

    def create_book(self, title: str, cover_path: str | None, page_count: int) -> BookSummary:
        now = time.time()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO books (title, cover_path, page_count, created_at) VALUES (?, ?, ?, ?)",
                (title, cover_path, page_count, now),
            )
            book_id = cur.lastrowid
        return BookSummary(
            id=book_id,
            title=title,
            cover_path=cover_path,
            page_count=page_count,
            created_at=now,
        )

    def delete_book(self, book_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM books WHERE id = ?", (book_id,))
        return cur.rowcount > 0

    def update_book_title(self, book_id: int, title: str) -> bool:
        with self._lock:
            cur = self._conn.execute("UPDATE books SET title = ? WHERE id = ?", (title, book_id))
        return cur.rowcount > 0

    def add_book_pages(
        self, book_id: int, pages: list[tuple[int, str, int | None, int | None]]
    ) -> None:
        """Insert pages. Each tuple: (page_num, rel_path, width, height)."""
        with self._lock:
            self._conn.executemany(
                "INSERT INTO book_pages (book_id, page_num, rel_path, width, height) "
                "VALUES (?, ?, ?, ?, ?)",
                [(book_id, p[0], p[1], p[2], p[3]) for p in pages],
            )

    def book_pages(self, book_id: int) -> list[BookPage]:
        sql = (
            "SELECT book_id, page_num, rel_path, width, height "
            "FROM book_pages WHERE book_id = ? ORDER BY page_num"
        )
        with self._lock:
            rows = self._conn.execute(sql, (book_id,)).fetchall()
        return [
            BookPage(book_id=r[0], page_num=r[1], rel_path=r[2], width=r[3], height=r[4])
            for r in rows
        ]

    # --- Book Tags & Favorites ------------------------------------------------

    def book_tags(self, book_id: int) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT tag FROM book_tags WHERE book_id = ?", (book_id,)
            ).fetchall()
        return [r[0] for r in rows]

    def all_book_tags(self) -> list[tuple[str, int]]:
        sql = "SELECT tag, COUNT(*) as cnt FROM book_tags GROUP BY tag ORDER BY cnt DESC, tag"
        with self._lock:
            return self._conn.execute(sql).fetchall()

    def set_book_tags(self, book_id: int, tags: list[str]) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM book_tags WHERE book_id = ?", (book_id,))
            if tags:
                self._conn.executemany(
                    "INSERT OR IGNORE INTO book_tags (book_id, tag) VALUES (?, ?)",
                    [(book_id, t.strip()) for t in tags if t.strip()],
                )

    def toggle_book_favorite(self, book_id: int) -> bool:
        with self._lock:
            existing = self._conn.execute(
                "SELECT 1 FROM book_favorites WHERE book_id = ?", (book_id,)
            ).fetchone()
            if existing:
                self._conn.execute("DELETE FROM book_favorites WHERE book_id = ?", (book_id,))
                return False
            else:
                self._conn.execute(
                    "INSERT INTO book_favorites (book_id, added_at) VALUES (?, ?)",
                    (book_id, time.time()),
                )
                return True

    def book_favorite_ids(self) -> set[int]:
        with self._lock:
            rows = self._conn.execute("SELECT book_id FROM book_favorites").fetchall()
        return {r[0] for r in rows}

    # --- Book fingerprints (duplicate detection) --------------------------

    def get_book_hash(
        self, book_id: int
    ) -> tuple[int, str | None, list[tuple[int, str]]] | None:
        """Return (page_count, cover_phash, [(page_num, phash), ...]) or None."""
        with self._lock:
            r = self._conn.execute(
                "SELECT page_count, cover_phash, sample_phashes FROM book_hashes WHERE book_id = ?",
                (book_id,),
            ).fetchone()
        if r is None:
            return None
        samples = [(int(pn), ph) for pn, ph in json.loads(r[2])]
        return (int(r[0]), r[1], samples)

    def upsert_book_hash(
        self,
        *,
        book_id: int,
        page_count: int,
        cover_phash: str | None,
        sample_phashes: list[tuple[int, str]],
        indexed_at: int,
    ) -> None:
        payload = json.dumps([[int(pn), ph] for pn, ph in sample_phashes])
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO book_hashes
                    (book_id, page_count, cover_phash, sample_phashes, indexed_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(book_id) DO UPDATE SET
                    page_count = excluded.page_count,
                    cover_phash = excluded.cover_phash,
                    sample_phashes = excluded.sample_phashes,
                    indexed_at = excluded.indexed_at
                """,
                (book_id, page_count, cover_phash, payload, indexed_at),
            )

    def book_ids_without_hash(self) -> list[int]:
        """Book IDs that have no fingerprint yet (drives idempotent backfill)."""
        sql = """
            SELECT b.id FROM books b
            LEFT JOIN book_hashes h ON h.book_id = b.id
            WHERE h.book_id IS NULL
            ORDER BY b.id
        """
        with self._lock:
            rows = self._conn.execute(sql).fetchall()
        return [r[0] for r in rows]

    def candidate_books_by_page_count(
        self, page_count: int
    ) -> list[tuple[int, str, str | None, list[tuple[int, str]]]]:
        """Fingerprinted books with a matching page_count (cheap match prefilter)."""
        sql = """
            SELECT h.book_id, b.title, h.cover_phash, h.sample_phashes
              FROM book_hashes h
              JOIN books b ON b.id = h.book_id
             WHERE h.page_count = ?
        """
        with self._lock:
            rows = self._conn.execute(sql, (page_count,)).fetchall()
        out: list[tuple[int, str, str | None, list[tuple[int, str]]]] = []
        for book_id, title, cover, samples_json in rows:
            samples = [(int(pn), ph) for pn, ph in json.loads(samples_json)]
            out.append((int(book_id), title, cover, samples))
        return out

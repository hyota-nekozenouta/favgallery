"""ホット API の応答時間ベースライン計測（2026-06-10 Phase 0 / リファクタ前後比較用）。

合成ライブラリ（既定 3,000 posts + 1,500 timeline + 60 books × 20 tags）を tmp に組み、
TestClient でホットエンドポイントを N 回叩いて中央値 ms を出す。
本番 DB には一切触れない。実行: `uv run python scripts/perf_baseline.py`
"""

from __future__ import annotations

import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fastapi.testclient import TestClient  # noqa: E402

from favgallery.db import Database, TimelinePost  # noqa: E402
from favgallery.server import create_app  # noqa: E402

POSTS = 3000
TIMELINE = 1500
BOOKS = 60
ROUNDS = 30


def _seed(library_root: Path) -> None:
    db = Database(library_root / "xlikes.sqlite")
    for i in range(POSTS):
        db.upsert_post(
            tweet_id=str(2_000_000_000 + i), num=1,
            rel_media=f"author{i % 40}/{2_000_000_000 + i}_1.jpg",
            media_type=("photo", "video", "gif")[i % 3], extension="jpg",
            width=1200, height=900, date=f"2026-{(i % 12) + 1:02d}-01 00:00:00",
            author_name=f"author{i % 40}", author_nick=f"Author {i % 40}",
            content=f"sample content {i} #tag{i % 25}", favorite_count=i % 500,
            view_count=i * 3, sensitive=False, lang="ja", hashtags=(f"tag{i % 25}",),
        )
    for i in range(TIMELINE):
        db.upsert_timeline_post(TimelinePost(
            tweet_id=str(3_000_000_000 + i), num=1, fetched_at=1_700_000_000 + i,
            date=f"2026-{(i % 12) + 1:02d}-02 00:00:00",
            author_name=f"tl_author{i % 30}", author_nick="", author_avatar_url="",
            content=f"timeline {i}", media_url=f"https://pbs.twimg.com/media/x{i}.jpg",
            thumb_url=f"https://pbs.twimg.com/media/x{i}.jpg?name=small",
            media_type=("photo", "video")[i % 2], width=1200, height=900,
            favorite_count=0, view_count=0, hashtags=[], favorited=False,
        ))
    for b in range(BOOKS):
        book = db.create_book(f"book {b}", f"books/{b}/0001.jpg", 20)
        db.add_book_pages(
            book.id, [(p, f"books/{b}/{p:04d}.jpg", 1200, 1700) for p in range(1, 21)]
        )
        db.set_book_tags(book.id, [f"btag{(b + t) % 12}" for t in range(3)])


def _bench(client: TestClient, label: str, path: str) -> None:
    times: list[float] = []
    for _ in range(ROUNDS):
        t0 = time.perf_counter()
        r = client.get(path)
        times.append((time.perf_counter() - t0) * 1000)
        assert r.status_code == 200, f"{path} -> {r.status_code}"
    print(f"{label:34s} p50={statistics.median(times):7.2f}ms  "
          f"p95={sorted(times)[int(len(times) * 0.95) - 1]:7.2f}ms")


def main() -> None:
    # ignore_cleanup_errors: Windows では開いたままの SQLite 接続が unlink を阻む
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp) / "library"
        root.mkdir()
        _seed(root)
        app = create_app(library_root=root, scan_in_background=False)
        client = TestClient(app)
        print(f"synthetic library: posts={POSTS} timeline={TIMELINE} books={BOOKS} rounds={ROUNDS}")
        _bench(client, "GET /api/library", "/api/library")
        _bench(client, "GET /api/posts?limit=60", "/api/posts?limit=60")
        _bench(client, "GET /api/posts?author=author1", "/api/posts?author=author1&limit=60")
        _bench(client, "GET /api/posts?q=sample", "/api/posts?q=sample&limit=60")
        _bench(client, "GET /api/timeline", "/api/timeline?limit=60")
        _bench(client, "GET /api/timeline?media_type=video", "/api/timeline?media_type=video&limit=60")
        _bench(client, "GET /api/timeline?hide_liked=true", "/api/timeline?hide_liked=true&limit=60")
        _bench(client, "GET /api/books", "/api/books")
        _bench(client, "GET /api/books/tags", "/api/books/tags")
        _bench(client, "GET / (index)", "/")


if __name__ == "__main__":
    main()

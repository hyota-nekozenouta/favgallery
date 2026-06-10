# Perf ベースライン（2026-06-10 / v0.2.6-pre-refactor）

リファクタ・最適化（plan: sharded-strolling-sloth）前後の比較用。
計測: `uv run python scripts/perf_baseline.py`（合成 3,000 posts / 1,500 timeline / 60 books・TestClient・30 回中央値）

## API 応答時間（リファクタ前）

| endpoint | p50 | p95 |
|---|---|---|
| GET /api/library | 38.98ms | 82.91ms |
| GET /api/posts?limit=60 | 38.38ms | 80.80ms |
| GET /api/posts?author=author1 | 53.66ms | 96.39ms |
| GET /api/posts?q=sample | 21.90ms | 60.76ms |
| GET /api/timeline | 29.65ms | 50.82ms |
| GET /api/timeline?media_type=video | 24.93ms | 30.64ms |
| GET /api/timeline?hide_liked=true | 26.05ms | 42.61ms |
| GET /api/books | 17.77ms | 29.09ms |
| GET /api/books/tags | 7.70ms | 9.79ms |
| GET / (index) | 9.59ms | 14.84ms |

## 構造的コスト（リファクタ前・コード検証で確定）

- ページロードごとに自動同期（gallery-dl フルスクレイプ）+ 重複チェック 2 種（SHA-256 全走査 + imagehash）= 変化ゼロでも 20〜90 秒の background CPU
- Tailwind CDN 実行時 JIT: ~100-150KB DL + 解析を毎ロード
- /api/posts は毎回 `all_listed_post_keys()` で DB 全読み / /api/books は books × book_tags の N+1
- メディア・サムネは weak ETag（毎回 revalidation）
- SQLite jounal_mode=DELETE（WAL 未設定）

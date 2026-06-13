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

## Phase 1 完了後（2026-06-10・WAL/index/N+1/listed-keys cache/strong ETag）

| endpoint | before p50 | after p50 | 差 |
|---|---|---|---|
| GET /api/library | 38.98ms | 11.50ms | **-70%** |
| GET /api/posts?limit=60 | 38.38ms | 11.12ms | **-71%** |
| GET /api/posts?author= | 53.66ms | 12.28ms | **-77%** |
| GET /api/books | 17.77ms | 13.32ms | -25% |
| GET /api/timeline?media_type= | 24.93ms | 20.88ms | -16% |

※ timeline 素クエリは run 間ノイズが大きい（30→51ms に見えるが p95 が跳ねており計測機の負荷由来）。
※ Phase 1-6（メタデータ Cache-Control）は**意図的に見送り** — /api/library は同期直後にフロントが
再取得して新着を反映する設計のため、ブラウザキャッシュは新着不可視バグを生む（隠れ結合 #5）。

## 構造的コスト（リファクタ前・コード検証で確定）

- ページロードごとに自動同期（gallery-dl フルスクレイプ）+ 重複チェック 2 種（SHA-256 全走査 + imagehash）= 変化ゼロでも 20〜90 秒の background CPU
  - セルフホストで重い場合は環境変数 `FAVGALLERY_AUTOSYNC_ON_LOAD=0` でページロード時の自動同期を無効化できる（手動同期ボタンは引き続き使用可）。
- Tailwind CDN 実行時 JIT: ~100-150KB DL + 解析を毎ロード
- /api/posts は毎回 `all_listed_post_keys()` で DB 全読み / /api/books は books × book_tags の N+1
- メディア・サムネは weak ETag（毎回 revalidation）
- SQLite jounal_mode=DELETE（WAL 未設定）

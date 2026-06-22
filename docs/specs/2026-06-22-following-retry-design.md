# フォロー中タブ ⟳ 再試行設計

- **Status**: OPEN（実装は X 側状態を見て GO 判断）
- **作成日**: 2026-06-22
- **由来**: `.company/secretary/todos/2026-06-11.md:20` 「時間を置いて再試行（X 側フラグ確認・失敗が続くなら作者巡回方式の設計へ）」
- **対象版**: v0.6.1 以降（M7 候補・実装は v0.7.0 minor で出す想定）

## 1. なぜ書くか

フォロー中タブの ⟳ ボタン（`POST /api/timeline/refresh`）は現状 **X の `/home/following` を一括スクレイプ** する単発フローのため、X 側で

- レート制限（短時間連打）
- cookie 失効・セッション切れ
- ホーム TL の DOM 構造変化（gallery-dl の対応漏れ）
- 単一作者の重い動画ポストでパーサが詰まる

のいずれかが起きると **全件取得失敗** で空のタイムラインが返る。再試行も同じ経路を叩くだけで構造的に弱い。

todos の指示は「時間を置いて再試行で復帰する **か**、復帰しないなら作者巡回方式へ」の二段構え。前半は運用判断（時間置けば直る場合は何もしない）、後半は設計判断（直らないならフォールバック経路を持つ）。本 spec は後半の **作者巡回方式の設計**。

## 2. 現状

### 2.1 経路図

```
UI ⟳ クリック
  └─ POST /api/timeline/refresh   (src/favgallery/routers/timeline.py:60)
       └─ TimelineRefresher.start()  (src/favgallery/timeline.py:173)
            └─ Thread worker
                 └─ fetch_timeline_metadata(
                       url="https://x.com/home/following",
                       range_spec="1-300",
                       twitter_retweets=True,
                    )                            (src/favgallery/timeline.py:133)
                 └─ gallery_dl.job.DataJob.run()  ← ここが単一障害点
                 └─ for (url, meta) in pairs: db.upsert_timeline_post(...)
            └─ state.{running,last_error,auth_error} を更新
  └─ GET /api/timeline/status        (UI が polling して ⟳ のスピン / 通知バナーを切替)
```

### 2.2 失敗の現れ方（実装ベースで確認）

| 失敗種別 | 検出箇所 | UI 表現 |
|---|---|---|
| cookie 失効 / Auth 不在 | `is_auth_failure(exc)` + `detect_auth_failure(gdl_logs)` | `auth_error=true` + `last_error=AUTH_FAILURE_MESSAGE`（モーダル誘導） |
| 429 / クールダウン | `_can_start_locked()` の 60s ガード | `started:false, reason="cooldown, retry in Xs"`（429） |
| 一般例外（パース / DOM / network） | `_worker` の `except Exception` | `last_error=f"{type(exc).__name__}: {exc}"`（穏当バナー） |
| 部分失敗（一部作者のポストだけ落ちる） | **検出されない**（DataJob が pair 単位で集約） | 静かにスキップ → 「件数が少ない」だけが UI に現れる |

最後の「部分失敗が静かにスキップされる」が現方式の最大の盲点。

## 3. 代替案: 作者巡回方式

### 3.1 アイデア

`https://x.com/home/following` を 1 リクエストで取りに行く代わりに、**フォロー中作者リストを取得 → 各作者のユーザータイムラインを 1 人ずつ巡回 → 結果を時間軸でマージ** する。

```
UI ⟳ クリック
  └─ POST /api/timeline/refresh?mode=author-walk
       └─ TimelineRefresher.start_author_walk()
            └─ Step 1: fetch_following_users(me)      ← 新規（gallery-dl `/{me}/following`）
                       → list[author_handle]
            └─ Step 2: for each handle (並列度 N=2〜3):
                         fetch_user_timeline(handle, range_spec, since=last_seen[handle])
                         → list[(url, meta)]
            └─ Step 3: db.upsert_timeline_post 群（時間軸ソート不要・DB 側で order）
            └─ state.{running, partial_failures, total_fetched} を更新
```

### 3.2 部分失敗の扱い

各 `fetch_user_timeline` が独立 → 1 人失敗しても他は通る。`state.partial_failures = [(handle, error_msg), ...]` を露出し、UI 側で「✓ 28 / 30 作者から取得 / 2 件失敗」と出す。**全滅 vs 部分失敗** の区別が初めて可視化される。

### 3.3 進捗 streaming（任意・後段）

巡回が長引くため、`/api/timeline/status` に `progress: {done, total, current_handle}` を追加すると UI のスピナーが「29/120 作者…」と表示できる。SSE まで作り込まなくても polling で十分。

## 4. 既存方式との比較

| 観点 | 現方式（home/following 一括） | 作者巡回方式 |
|---|---|---|
| **取得時間** | 1 リクエスト・〜30s | フォロー数 × 1〜3s（並列度 2〜3）。100 人で〜60s / 500 人で〜5min |
| **レート消費** | 低（1 HTTP 系列） | 高（フォロー数ぶん）。X のレート閾値に当たりやすい |
| **部分失敗耐性** | なし（全滅 or 全成功） | あり（per-handle 独立） |
| **新着検出** | 直近 300 件から拾う | per-handle で `since=last_seen` を持てる＝過不足なく拾える |
| **DOM 変化耐性** | home/following の 1 箇所のパース崩れで全滅 | user timeline は安定（変化頻度低い） |
| **リツイート** | retweets=true で取れる | 別途設定（X はユーザー TL でも retweet 表示は user 設定依存） |
| **cookie 必須度** | 必須（フォロー中＝認証 TL） | 必須（フォロー作者リスト取得自体が認証必要） |
| **実装コスト** | 既存 | `fetch_following_users` + `fetch_user_timeline` 新規 / マージ / 状態拡張 |
| **デバッグ容易度** | 失敗時の切り分けが困難 | per-handle ログで原因特定が容易 |

## 5. 失敗モード対策の対応表

| 失敗 | 現方式 | 作者巡回方式 |
|---|---|---|
| cookie 失効 | 検出 → モーダル誘導 | 同左（最初の `fetch_following_users` で確実に出る） |
| レート制限 | 60s クールダウン | + per-handle 並列度を下げる / `since` 差分取得で軽量化 |
| DOM 変化 | 全滅 | 該当作者のみ skip + 残りは継続 |
| 部分失敗 | **見えない** | partial_failures で可視化 |
| 重い作者で詰まる | 全滅 | timeout per-handle で skip 可 |

## 6. 実装フェーズ案（GO 後）

### Phase 1 — オプトイン経路として並走（minor bump 候補 v0.7.0）

- `fetch_following_users(config_path, me_handle) -> list[str]` 新規（`src/favgallery/timeline.py`）
- `fetch_user_timeline(config_path, handle, *, since, range_spec) -> list[(url, meta)]` 新規
- `TimelineRefresher.start_author_walk()` 新規（既存 `start()` は残す）
- `POST /api/timeline/refresh?mode=author-walk` に分岐追加（既定は従来の `mode=home`）
- `RefreshState.partial_failures: list[tuple[str, str]]` フィールド追加
- ⚙ メニューに「フォロー中の取得方式」トグル（home / author-walk）
- pytest 追加（mock gallery-dl で `fetch_user_timeline` 単体 / 部分失敗の整合性）

### Phase 2 — 評価ベースで既定切替（GO 判断）

Phase 1 を 2 週間運用 → 失敗率 / 取得件数 / レスポンス時間を比較 → 既定を author-walk に切替するか判断。

### Phase 3 — 既定切替後のクリーンアップ（patch）

home モードを「fallback」として薄く残し、author-walk を一次経路に。⚙ メニューのトグルは「実験モード」表記に変える。

## 7. 着手前の確認事項

GO 判断時に再確認:

- X の `/{me}/following` を gallery-dl の現行版で安定取得できるか（DataJob で試走）
- 作者リスト 1 人あたりの所要時間と、フォロー 100/300/500 人のシナリオでの総時間
- レート制限の閾値（短時間連打で 429 / temp-lock するか）
- 既存 cookie だけで完結するか（追加スコープが要らないか）

## 8. 関連

- 現方式: `src/favgallery/timeline.py:133` `fetch_timeline_metadata`
- ⟳ エンドポイント: `src/favgallery/routers/timeline.py:60`
- 状態構造体: `src/favgallery/timeline.py:30` `RefreshState`
- 単発失敗の判定: `src/favgallery/gdl_errors.py` `is_auth_failure` / `detect_auth_failure`
- 関連 todos: `.company/secretary/todos/2026-06-11.md:20`
- ⚙ メニュー UI: `src/favgallery/static/lib/popovers.js`（モード切替トグルの想定設置箇所）

## 9. 決定待ち

- [ ] 現方式の失敗率（実運用での体感）— 数週間運用して頻発するなら GO
- [ ] フォロー数の体感値（100 / 300 / 500 のどれくらいか）— 巡回時間の妥当性判断
- [ ] gallery-dl 側で `/{me}/following` の作者一覧取得が安定しているかの検証（実走）
- [ ] M7 として版数計画に組み込むか（v0.7.0 minor 候補）

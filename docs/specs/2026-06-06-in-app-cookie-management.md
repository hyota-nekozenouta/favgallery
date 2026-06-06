# In-app cookie management — design

- Status: APPROVED (2026-06-06)
- Product: Archive (xlikes-viewer)
- Author: 開発HALO #2

## 問題 / 症状

公開版 `archive.hyota.cloud` で「いいね」と「新しい投稿（timeline）」の更新が一切できない。
同期実行時、画面に **"cookies.txt not found — set GALLERY_DL_COOKIES env var"**（`/api/sync/start` の 400 枝）が出る。

## 根本原因（確定）

Railway コンテナ上に `/data/cookies.txt` が**存在しない**。

- いいね同期（`SyncRunner` → `DownloadJob(/likes)`）も新着取得（`TimelineRefresher` → `DataJob(/home/following)`）も、
  どちらも gallery-dl + X cookies に依存している。cookies が無ければ両方とも取得できない。
- cookies は現状 `GALLERY_DL_COOKIES` 環境変数からのみ供給され、しかも `_write_cookies_from_env()` が
  **起動時に一度書き出すだけ**。env が未設定（空）だと何も書かれず、ファイルは生成されない。
- gallery-dl は 1.32.1（2026-05-03 リリースの最新版）で extractor 自体は健全。**コードのバグではなく cookies の未プロビジョニング**。
- 読み書きのパスは一致しており（`library_root.parent / "cookies.txt"` = Railway では `/data/cookies.txt`）、パスずれバグは無い。

## ゴール

1. **Web 画面から X cookies を設定・更新できる**ようにする（Railway ダッシュボード・再デプロイ不要）。
2. cookies は **永続ボリューム**（`/data/cookies.txt`）に保存し、コンテナ再起動後も残る。
3. cookies は X 側で**定期的に失効する消耗品**なので、失効時も画面から貼り替えるだけで復旧できる（再発防止）。
4. 「貼ったけど効いてる？」に即答できる**接続テスト**を備える。

## 非ゴール（YAGNI）

- cookie 本文の読み出し API（漏洩面を作らない）。
- ブラウザからの自動 cookie 抽出（X ログイン代行などはやらない。cookies.txt の書き出しは利用者が行う前提）。
- cookies の暗号化保管（単一利用者・Basic 認証内側・ボリューム内のため現状スコープ外。必要になれば別途）。

## 設計

### バックエンド — 新規 `routers/cookies.py`（3 エンドポイント）

すべて既存の Basic 認証ミドルウェアの内側。cookies の保存先は `ctx.cookies_file`（= `library_root.parent / "cookies.txt"`）。

| メソッド / パス | 役割 | レスポンス |
|---|---|---|
| `GET /api/cookies/status` | 設定状態の確認。**本文は返さない** | `{configured: bool, updated_at: float|null, size: int, looks_valid: bool}` |
| `POST /api/cookies` body `{content: str}` | cookies をボリュームへ**アトミック書き込み** | 成功時 `200` + status と同形。空/形式不正は `400` |
| `POST /api/cookies/verify` | 軽い実接続テスト（自分のいいねを 1 件取得して認証可否を判定） | `{ok: bool, auth_error: bool, message: str}` |

- `looks_valid`: Netscape cookie 形式（タブ区切り行）であること、かつ X の重要 cookie（`auth_token`）行が含まれることの**形式チェックのみ**（実通信はしない）。
- `POST /api/cookies` の書き込みは temp ファイル + `os.replace()` でアトミックに。`content` は末尾改行を正規化。空文字・明らかに cookie でない入力は `400 {detail}`。
- `verify` は `fetch_my_liked_tweet_ids`（range 1-1）を `gdl_lock` 下で呼び、`is_auth_failure` で失効判定。例外・空結果でも落とさず `{ok, auth_error, message}` に正規化。

### フロント — `static/index.html`

- `#optionsPopover`（⚙「マイいいね設定」）に **cookie 状態行**と「🔑 cookies 設定」ボタンを追加。
  - 状態行: `✅ cookies 設定済み`（`updated_at` を相対表示） / `⚠️ cookies 未設定` を `GET /api/cookies/status` で描画。
- 「🔑 cookies 設定」→ **cookie modal**（既存の book アップロード modal と同じ実装パターン）:
  - テキストエリア（貼り付け）
  - 「ファイルを選択」→ 選んだ `.txt` の中身をテキストエリアに読み込む（FileReader）
  - 「保存」→ `POST /api/cookies` → 成功で modal を閉じ状態行を更新
  - 「接続テスト」→ `POST /api/cookies/verify` → `✅ 認証OK` / `❌ 失効・無効` を表示
  - 形式チェックNG・保存失敗は modal 内にメッセージ表示（sticky）

### ついでに直す小バグ（同じ cookie 系・意図と実装のドリフト）

`_write_cookies_from_env()` は docstring に「**existing cookies.txt files are preserved**」とあるが、
実装は env が非空である限り**起動毎に無条件上書き**しており、コメントと食い違っている。
これを「**cookies ファイルが存在しないときだけ env で初期投入する**」（seed-once）に修正する。

- 効果: 一度 UI で設定した cookies が、コンテナ再起動時に（古い）env 値で**上書きされて消える事故**を防ぐ。UI を正とする。
- 今回 env は未設定のため現状の実害は無いが、UI 機能と env が併存する将来の踏み抜きを構造的に塞ぐ（原則 8 / 9）。

### セキュリティ / 品質

- 全エンドポイントは Basic 認証の内側。cookie 本文を返す API は作らない（`status` は有無・サイズ・形式妥当性のみ）。
- 書き込みはアトミック（temp + `os.replace`）。
- gallery-dl は実行毎に config のパスから cookies を読むため、保存後は**再起動なしで次回同期に反映**。

## テスト（TDD・`tests/test_cookies_api.py`）

- `GET /api/cookies/status`: 未設定 → `configured=false` / 設定後 → `configured=true, looks_valid=true`。
- `POST /api/cookies`: 正常書き込みでファイル生成・内容一致 / 空・非 cookie 入力で `400`。
- 本文がレスポンスに混入しないこと（status は中身を返さない）。
- `_write_cookies_from_env`: ファイル不在のとき env で seed する / **既存ファイルがあるとき上書きしない**（回帰テスト）。
- `POST /api/cookies/verify`: gallery-dl をモックして `ok` / `auth_error` 双方の分岐。

## 由来 / 意図（原則 9）

- 「いいねと新しい投稿の更新ができない」（2026-06-06 ひょーたさん）の根本解決。
- 当初は「gallery-dl が古い」を疑ったが、最新版（1.32.1）と判明し棄却 → 体系的デバッグで cookies 未プロビジョニングに収束。
- env 一発設定（即復旧）ではなく**アプリ内 cookie 管理**を選択した理由: cookies は失効を繰り返す消耗品で、
  env 方式だと失効のたびに Railway 操作＋再デプロイが必要になり再発し続ける。画面から貼り替え可能にして再発を断つ（原則 18 / 8）。

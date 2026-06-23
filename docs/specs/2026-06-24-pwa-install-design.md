# PWA 化設計 — iOS / Android / Windows 同時対応

**Status**: Phase A 実装完了 (v0.6.3) / Phase B は実機観測待ち / Phase C は条件付
**作成日**: 2026-06-24
**対象版**: v0.6.3 (Phase A) → v0.7.0 (Phase B 起動時 minor) → patch (Phase C 起動時)
**メイン plan**: `~/.claude/plans/web-floofy-lagoon.md`

---

## 1. 背景

ひょーたさん要望「スマホと Windows アプリ化したい」。Q&A で方向性確定：

- **対象 OS**: iOS + Android + Windows の 3 プラットフォーム同時
- **配信方式**: **ストア配信なし**。ブラウザの「ホーム画面に追加 / アプリとしてインストール」で standalone 起動
- **オフライン閲覧**: 不要（Railway 稼働前提 OK）
- **追加課金**: ゼロ（Apple Developer 等の年額不要 = 原則 17 該当なし）

### なぜ PWA か

- ストア配信なし + 3 プラットフォーム同時という制約に対し、PWA は唯一の単一コードベース解
- FavGallery は既に Tailwind mobile-first / `env(safe-area-inset-*)` / HTTPS が整備済 = 骨組み健全
- Tauri 化は Windows 単体ならアリだが iOS/Android で WebView + ストア配信になり要件と矛盾

### 既存資産の流用前提

- `_resolve_asset_version` (`server.py:109`) によるコンテンツハッシュバスト
- `_register_shell_routes` (`server.py:242`) の root 配下ファイル配信パターン
- `_register_http_shell_middleware:add_version_header` (`server.py:225`) の Cache-Control 制御（401 時 no-cache fallback 含む）
- `?v=__ASSET_VERSION__` プレースホルダの `root()` 内置換 (`server.py:251`)

---

## 2. 段階構成

### Phase A: 最小 install 可能化 (v0.6.3・実装完了)

ブラウザの「ホーム画面に追加 / アプリとしてインストール」がそれぞれ成立する最小要件のみ満たす。UI は何も変えない。

**追加・変更**:

- `src/favgallery/static/manifest.webmanifest`（新規・name/start_url/scope/display/colors/icons）
- `src/favgallery/static/icons/icon-192.png` `icon-512.png` `icon-512-maskable.png` `apple-touch-icon.png`（新規・Pillow 自動生成）
- `scripts/gen_pwa_icons.py`（新規・黒背景 + 白文字 FG を Fraunces Bold で焼く・追加依存ゼロ）
- `src/favgallery/static/index.html` `<head>`（PWA meta + manifest link + iOS meta + apple-touch-icon link + theme-color + application-name）
- `src/favgallery/server.py`:
  - `_resolve_asset_version` に `manifest.webmanifest` + `icons/*.png` を hash 集計対象として追加
  - `_register_http_shell_middleware` の `is_versioned_asset` に `/static/icons/` を追加
  - `_register_shell_routes` に `/manifest.webmanifest` 専用 route 追加（`application/manifest+json` + `Cache-Control: no-cache`）
- `tests/test_server.py`（PWA 回帰テスト 7 件追加）
- `pyproject.toml` + `uv.lock`（version 0.6.2 → 0.6.3）

**配色・命名（CSS `:root` 実測値より決定）**:

| 項目 | 値 | 根拠 |
|---|---|---|
| `background_color` | `#000000` | `--bg: #000`（スプラッシュ画面用） |
| `theme_color` | `#1d9bf0` | `--accent: #1d9bf0`（X ブルー・Android アドレスバー / Windows タイトルバー） |
| iOS status bar | `black-translucent` | 既存黒背景 UI と整合 |
| `name` / `short_name` | `FavGallery` | 既存 `.wordmark` クラスと整合 |
| アイコン | 黒背景 + 白文字「FG」 | `gen_pwa_icons.py` で自動生成（中央 50% 高さで描画 = 中央 60% 安全領域内に自然に収まる） |

**なぜ manifest を `/manifest.webmanifest` (root) に配置するか**:

- Web App Manifest 仕様で `scope: "/"` との整合が直感的
- StaticFiles の MIME 判定は OS の mimetypes レジストリ依存（Windows で `application/octet-stream` になる既知バグあり）→ FastAPI route で `media_type` 明示が安全
- Cache-Control を `no-cache` に明示できる（manifest は常に最新を取らせる方針 / index.html と同方針）

---

### Phase B: iOS Basic 認証問題対応 (v0.7.0・条件付・未着手)

**起動条件**: Phase A 実機検証で「iOS standalone 起動時に Basic 認証ダイアログが毎回出る / または 401 で白画面」が観測された場合のみ。観測されなければ skip。

iOS Safari の PWA standalone モードは Basic 認証クレデンシャルを保持しない既知挙動がある。先回り対応せず、Phase A 完了後の実機観測で GO/NO-GO 判断する。

**対応方針（GO 時）**: Basic を Cookie に薄くラップ

- `/auth/login` (POST: user/pass) → 検証 → `HttpOnly + Secure + SameSite=Lax` の署名付き session cookie
- middleware は Cookie 優先・Basic フォールバック並走（既存 Basic ユーザーの API クライアントを壊さない）
- cookie 失効時のみ `/login`（ログインフォーム 1 ページ）へリダイレクト
- 署名は `secrets.token_urlsafe + HMAC` で自作（依存ゼロ）

**version 判定**: minor 0.6.3 → 0.7.0（認証経路を増やす = 外向き挙動変化）

---

### Phase C: SW 最小追加 (条件付・patch・未着手)

**起動条件**: Phase A 後、Android Chrome で「アプリをインストール」が install bar に **出ない** 場合のみ。Android の install criteria は fetch handler を持つ SW を要求するため。

**内容（GO 時）**:

- `src/favgallery/static/sw.js`（fetch event を pass-through で登録のみ・キャッシュなし）
- index.html bootstrap で `navigator.serviceWorker.register('/sw.js')`
- `/sw.js` 配信 route を `_register_shell_routes` に追加（manifest と同じパターン）
- SW 自体は `no-cache` 配信（更新伝播失敗で PWA が古いまま固まる事故を予防）

オフライン不要要件なので fetch を捕捉してもキャッシュには入れない（pass-through のみ）。

---

## 3. テスト戦略

### 自動テスト（Phase A・実装済）

`tests/test_server.py` に 7 件追加（既存 287 → 294 件 PASS / ruff clean）:

1. `test_pwa_manifest_served_with_correct_mime` — MIME `application/manifest+json` + 必須要素（display=standalone / start_url=/ / scope=/ / colors / icons 配列・maskable purpose 含む）
2. `test_pwa_manifest_no_cache` — Cache-Control: no-cache
3. `test_index_has_pwa_meta` — index.html に manifest link / iOS meta / theme-color / apple-touch-icon link が含まれる
4. `test_apple_touch_icon_served` — 180x180 PNG が image/png で配信
5. `test_pwa_icons_long_cached` — `/static/icons/*.png` が `public, max-age=31536000, immutable`
6. `test_pwa_icons_401_is_not_long_cached` — 401 時の no-cache fallback（style.css / lib/*.js と対称）
7. `test_asset_version_includes_manifest_and_icons` — manifest / icon 差し替えで `_resolve_asset_version` の hash が変わる

全件 FastAPI TestClient で完結 = **playwright 不要**（OSS 軽量主義継続）。

### 実機検証（手動・smoke-checklist 連動）

`docs/smoke-checklist.md` の「PWA インストール」セクション参照。iOS / Android / Windows それぞれで「ホーム画面に追加 → standalone 起動 → 既存 smoke 回帰なし」を確認。

---

## 4. リスク・注意点

1. **Cloudflare の manifest キャッシュ**: v0.6.2 で CF 側 Browser Cache TTL を `Respect Existing Headers` に変更済（`~/.claude/plans/railway-zany-oasis.md` 段階 4 完走）。デプロイ後 `curl -sI` で `cf-cache-status` を必ず確認
2. **maskable icon の安全領域**: `gen_pwa_icons.py` は文字高 50% で描画するので、中央 60% 安全領域内に自然に収まる
3. **iOS Safari の theme_color 解釈**: status bar 色は `apple-mobile-web-app-status-bar-style` が優先で `theme_color` は一部しか見られない → 既存黒背景 UI と整合する `black-translucent` を採用
4. **`_resolve_asset_version` の集計範囲拡張**: icons ディレクトリ未存在状態（fresh clone 直後）でも動く `if icons_dir.exists()` ガード必須（実装済）
5. **`/manifest.webmanifest` root 配置の副作用**: SPA ルーティング（`/` / `/api/*` / `/static/*` の 3 経路）と衝突しないか確認 → 専用パスなので衝突なし
6. **Phase B 起動時のデータ移行なし**: 既存ユーザーは初回 cookie 取得が必要（ログインフォーム 1 回を通すだけ）

---

## 5. デプロイ後検証手順

1. `git push origin main` → Railway 自動デプロイ完了確認
2. `curl -sI https://<domain>/ | grep X-App-Version` で `0.6.3` を確認
3. `curl https://<domain>/manifest.webmanifest | jq .` で JSON 妥当性確認
4. `curl -sI https://<domain>/manifest.webmanifest` で `cf-cache-status` 確認（`MISS`/`DYNAMIC` なら OK / `HIT` で固まったら CF page rule 要確認）
5. `docs/smoke-checklist.md`「PWA インストール」セクションを iOS / Android / Windows で実機実行
6. iOS Basic 認証の挙動結果次第で Phase B GO/NO-GO 判断
7. Android install bar 出現結果次第で Phase C GO/NO-GO 判断

---

## 6. 関連

- メイン plan: `~/.claude/plans/web-floofy-lagoon.md`
- 関連 plan: `~/.claude/plans/railway-zany-oasis.md`（Cloudflare キャッシュ設定の前段）
- 既存スモーク: `docs/smoke-checklist.md`「PWA インストール」セクション
- 流用 spec パターン: `docs/specs/2026-06-22-following-retry-design.md`
- 版バンプ手順: `reference_favgallery_version_bump_uvlock.md`（pyproject + uv lock 同期必須）

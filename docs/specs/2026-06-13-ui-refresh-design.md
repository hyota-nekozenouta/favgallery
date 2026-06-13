# FavGallery UI 刷新 設計書（アイコン統一 + トーン整理 + スマホ最適化）

Status: OPEN（2026-06-13 / brainstorming 承認済み・実装計画はこれから）

> **For agentic workers:** この spec を元に `superpowers:writing-plans` で実装計画（`docs/plans/`）を作成する。実装は機能を変えず、見た目・操作感のみを刷新する。各段で pytest 全件 + JS 検証 3 層（check_js / check_load / check_modules）を緑に保つこと。

## Goal

現行 UI の「アイコンが絵文字・記号の寄せ集めでちぐはぐ／素っ気ない」「どこに何があるか分かりにくい」「スマホで押しにくい」を、**機能を一切変えずに**見た目・操作感の刷新で解消する。方向性は今の「Lights Out」（黒基調・X ライク）の **正常進化**。

## 背景・由来

2026-06-13 ひょーたさんから「アイコンのデザインとか UI がちょっと使いにくいから刷新したい」。Visual Companion ブレストで以下を決定：

- 刷新スコープ＝**全部**（A: アイコンの見た目 / B: 配置・分かりにくさ / C: 押しやすさ・スマホ / D: 全体のトーン）
- 方向性＝**案1「Refined Lights Out」**（黒基調・X っぽさは維持して洗練。変化を最小に・安全に）
- アイコン＝**無彩色で統一**（色を付けない）
- 差し色＝**A 案「アイコンは無彩色＋差し色は残す」**。インディゴを **選択中タブ・主要ボタン・リンク** だけに効かせる

ブレストのモック: `.superpowers/brainstorm/36265-1781347356/content/`（current-ui-audit / directions / icon-mapping / mono / final-look）。

## Non-Goals（やらないこと）

- 機能追加・機能変更（同期ロジック・DB・ルーター・取得処理は触らない）
- レイアウト構造の作り直し（サイドバー＋タブ＋マソンリーの骨格は維持）
- ライブラリ追加（Tailwind 事前生成・外部 CSS/JS 依存は増やさない。アイコンは SVG をリポジトリに同梱）
- ダーク以外のテーマ（ライトモードは対象外）

---

## A. アイコン体系

### 方針

絵文字・記号を全廃し、**Lucide**（MIT ライセンス / 24×24・stroke ベース・線幅と角丸が統一）の線アイコンへ統一する。SVG ソースをリポジトリに同梱し、**外部 CDN・追加依存はゼロ**。すべて `stroke="currentColor"` で描き、色は親要素の文字色を継承（＝無彩色が既定、差し色は親が持つときだけ乗る）。

### 配信方式（アーキテクチャ）

1. **SVG スプライト**を `index.html` の `<body>` 先頭に 1 つだけ inline で置く（`<svg style="display:none"><symbol id="ic-…">…</symbol></svg>`）。アイコン定義の唯一の出どころ。
2. 静的な HTML ボタンは、グリフ文字を `<svg class="icon" aria-hidden="true"><use href="#ic-name"/></svg>` に置換。
3. JS が動的生成する要素（posts.js / popovers.js / lightbox.js 等）用に、新モジュール **`icons.js`** を追加。`icon(name)` が `<svg class="icon"><use href="#ic-name"/></svg>` 文字列を返す単一ヘルパー。import map に登録。
4. `.icon` の CSS（サイズ・stroke 既定・`vertical-align`）を `style.css` に追加（Tailwind ソース → 事前生成を再ビルド）。

> 設計意図: 「アイコン定義はスプライト 1 箇所」「生成は icons.js 1 ヘルパー」に集約することで、後からの差し替え・追加が 1 ファイルで完結する（調整しやすさ・修復しやすさ）。

### アクセシビリティ

- アイコン単体ボタンは `aria-label` を必須化（既存 `title` と揃える）。装飾 SVG は `aria-hidden="true"`。
- ラベル付きボタン（タブ等）の SVG は `aria-hidden="true"`、テキストで意味を担保。

### 差し替えマップ（現状グリフ → Lucide 名）

| # | 場所 / 要素 | 現状 | Lucide アイコン | 備考 |
|---|---|---|---|---|
| 1 | サイドバー `manualSyncBtn` | ⟳ | `refresh-cw` | 全体同期 |
| 2 | サイドバー `optionsBtn` | ⚙ | `settings` | |
| 3 | サイドバー `loadingIndicator` | ⏳ | `loader-circle` | CSS で回転アニメ |
| 4 | ツールバー `hamburger-btn` | ☰ | `menu` | |
| 5 | タブ `tabLikes` | ♥ | `heart` | |
| 6 | タブ `tabTimeline` | 👁 | `eye` | |
| 7 | タブ `tabBookshelf` | 📚 | `book-open` | |
| 8 | ツールバー `layoutToggle` | ▦ | `layout-grid` ⇄ `rows-3` | 現モードに応じてアイコンを入替（任意・UX 向上） |
| 9 | ツールバー `markSeenBtn` | 📍 | `check-check` | 「ここまで既読」 |
| 10 | ツールバー `timelineRefreshBtn` | ⟳ | `download` | 新着取得（同期と区別） |
| 11 | サイドバー見出し「リスト」 | 📋 | `list` | |
| 12 | サイドバー見出し「タグ」 | 🏷 | `tag` | |
| 13 | 本棚 `bookFavFilter` | ⭐ | `star` | |
| 14 | 設定 `cookieSetBtn` | 🔑 | `key` | |
| 15 | `newListBtn` / `sidebarAddBookBtn` | ＋ | `plus` | |
| 16 | `lbClose` / `readerCloseBtn` | × | `x` | |
| 17 | `lbPrev` | ◀ | `chevron-left` | |
| 18 | `lbNext` | ▶ | `chevron-right` | |
| 19 | 折りたたみ `authorSingleIcon` / `tagToggleIcon` | ▶ | `chevron-right` → 展開時 `chevron-down`（回転で表現可） | |
| 20 | 設定 `meSyncBtn`（ラベル付き） | ♥ | `heart` | 「マイいいね同期」 |
| (任意) | `searchBox` 左 | （なし） | `search` | 検索欄に内包すると意味が明快（小改善） |

必要なユニーク Lucide アイコン（21 種・うち `rows-3` と `search` は任意改善）: `refresh-cw, settings, loader-circle, menu, heart, eye, book-open, layout-grid, rows-3, check-check, download, list, tag, star, key, plus, x, chevron-left, chevron-right, chevron-down, search`。

---

## B. 配置・分かりやすさ

骨格は維持しつつ整える：

- **アイコンボタンの統一**: サイズ・余白・角丸・ホバー（`hover:bg`）・フォーカスを 1 セットの共通クラスに集約。バラバラな `p-1` / `p-1.5` / `w-8 h-8` を統一。
- **意味の区別**: 同期(`refresh-cw`) と 新着取得(`download`) を別アイコンに（現状はどちらも ⟳ で紛らわしい）。
- **ツールチップ/ラベルの整備**: 全アイコン単体ボタンに `title` + `aria-label` を必ず付与。
- **ツールバーのグルーピング**: タブ群／表示切替／取得・既読アクション／件数 の視覚的なまとまりを `gap` と区切りで明確化（並び順自体は維持）。

---

## C. 押しやすさ・スマホ

- **タップ領域**: タッチ環境（`@media (pointer: coarse)` または既存のモバイルブレークポイント）でアイコンボタン・タブの最小ヒット領域を **44px** 以上に拡大。
- **タブ**: スマホでは 3 タブを全幅・等幅（1/3 ずつ）で大きく。
- **サイドバー**: 既存のオーバーレイ式スライド開閉（`#sidebar-overlay` / `#app-sidebar`）の余白・行高を広げ、開閉トランジションを滑らかに。
- デスクトップは現状の密度を維持（情報量優先）。サイズ拡大はタッチ環境にのみ適用。

---

## D. トーン・質感

「Lights Out」を維持して精度を上げる：

- **配色**: 黒基調（`zinc-950/900/800` 系）維持。差し色は **インディゴ**（`indigo-500/600`）。アイコン自体は無彩色（`zinc` 系の文字色を継承）。
- **差し色の適用範囲**: 選択中タブ（`indigo` 背景 + 白文字/白アイコン）・主要ボタン（`+ 新規` / `+ 漫画を追加` / `マイいいね同期`）・リンク（`+ 新規` 等）のみ。それ以外のアイコンは差し色を乗せない。
- **境界・角丸**: ボーダー（`zinc-800`）・角丸（カード/ボタンで一貫した `rounded-md` 系）・余白スケールを整理。
- **フォーカスリング**: キーボード操作時の `focus-visible` リング（インディゴ）を全インタラクティブ要素に統一。

---

## コンポーネント別の変更点

| 領域 | ファイル | 変更 |
|---|---|---|
| SPA シェル | `static/index.html` | SVG スプライト追加 / 各ボタンのグリフ → `<use>` 置換 / `aria-label` 付与 / import map に `icons` 追加 |
| アイコン生成 | `static/lib/icons.js`（新規） | `icon(name)` ヘルパー。動的生成箇所が参照 |
| 投稿グリッド | `static/lib/posts.js` | カード内のアクション等で生成しているグリフを `icon()` 化 |
| ポップオーバー | `static/lib/popovers.js` | リスト操作等のグリフを `icon()` 化 |
| ライトボックス | `static/lib/lightbox.js` | 閉じる/送りのグリフ（HTML 側 `lbClose`/`lbPrev`/`lbNext`）と整合 |
| 本棚 | `static/lib/bookshelf.js` | お気に入り/追加等のグリフを `icon()` 化 |
| リーダー | `static/lib/reader.js` | 閉じる等の整合 |
| スタイル | `static/style.css`（Tailwind 再生成） | `.icon` クラス / ボタン共通クラス / タップ領域 media query / focus-visible |

> 実際にグリフを動的生成している箇所は実装時に `rg` で全 JS を走査して洗い出す（漏れ防止）。HTML 側の静的グリフは本 spec の表が網羅。

## テスト戦略

- **回帰ゼロ**: 既存 pytest 全件（250 件）を各コミットで緑に保つ。
- **JS 検証 3 層**: `scripts/check_js` / `check_load` / `check_modules.mjs` を通す（import 漏れ・構文・読み込み順）。新規 `icons.js` は import map と check_modules の両方に正しく載ること。
- **アイコン網羅チェック**: スプライトの `symbol id` 一覧と、HTML/JS から参照される `#ic-*` 名の差分を検出する軽い検査を追加（未定義参照＝壊れアイコンを防ぐ）。実装簡易なら `scripts/` に 1 本足す。
- **目視スモーク**: `docs/smoke-checklist.md` に沿って PC + スマホ実機で全アイコン・タブ・ライトボックス・リーダー・設定パネルを確認。
- **バージョン**: 見た目変更につき SemVer MINOR 想定（`halo-version-bump` 判定）。`__ASSET_VERSION__`（資産ハッシュ）は CSS/JS 変更で自動更新。

## 実装順（フェーズ）

1. **アイコン基盤**: `icons.js` + SVG スプライト + `.icon` CSS（Tailwind 再生成）+ 網羅チェック。表示は既存グリフのまま並走可。
2. **静的グリフ置換**: `index.html` の全ボタンを `<use>` 化 + `aria-label`。
3. **動的グリフ置換**: 各 JS モジュールのグリフを `icon()` 化（`rg` で洗い出し）。
4. **トーン整理（D）**: 配色トークン・境界・角丸・差し色範囲・focus-visible を整える。
5. **配置整理（B）**: アイコンボタン共通クラス・ツールバーのグルーピング・同期/取得の区別。
6. **スマホ最適化（C）**: タップ領域 44px+・タブ全幅・サイドバー開閉の調整。
7. **検証 + バージョン bump + デプロイ**: 全テスト緑 → 実機スモーク → version bump → GitHub main push（Railway 自動デプロイ）。

各フェーズ末で全テスト + JS 検証を回し、1 フェーズ＝1 コミット（または論理単位で分割）。

## オープンクエスチョン

- なし（ブレストで方向性・アイコン・差し色まで確定）。実装中に動的グリフ箇所で判断が要る場合は都度確認。

# FavGallery UI 刷新 Implementation Plan

> ✅ **完了**: 本計画は **v0.5.0（2026-06-13）で全 20 タスク・全フェーズ出荷済み**（スプライト21種 / icons.js / check_icons.mjs / 無彩色+差し色 / スマホ最適化 / タブ刷新）。以下のチェックは出荷実態に合わせて遡及記入したもの。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 絵文字・記号アイコンを統一された Lucide 系の無彩色線アイコンへ全置換し、「Lights Out」トーンを維持したまま配置・押しやすさ・質感を磨く（機能変更なし）。

**Architecture:** ① アイコン基盤（index.html に inline SVG スプライト + `icons.js` ヘルパー + `.icon`/`.icon-btn` CSS + 未定義参照ガード `check_icons.mjs`）→ ② 静的 HTML のグリフ置換 → ③ 動的 JS のグリフ置換 → ④ トーン/トークン整理 → ⑤ 配置整理 → ⑥ スマホ最適化、の順で 1 フェーズ＝1〜数コミット。各タスクで JS 検証 4 本 + pytest + CSS 再生成を緑に保つ。

**Tech Stack:** Python 3.12 / FastAPI / uv / pytest / Tailwind v3（事前生成 CSS）/ ES Modules（import map）/ Node 製静的チェッカー

**設計の正典:** `docs/specs/2026-06-13-ui-refresh-design.md`

---

## 前提・共通コマンド

- **CSS 再生成**（input.css / config を触ったら必須）:
  ```bash
  npx --yes tailwindcss@3.4.17 -c tailwind.config.js -i scripts/tailwind.input.css -o src/favgallery/static/style.css --minify
  ```
- **JS 静的検証 4 本**:
  ```bash
  node scripts/check_js.mjs && node scripts/check_load.mjs && node scripts/check_modules.mjs && node scripts/check_icons.mjs
  ```
- **バックエンド回帰**: `uv run pytest -q`（250 件・各フェーズ末で緑を確認）
- **目視スモーク**: `docs/smoke-checklist.md`（PC + スマホ実機）

## 重要な事実（実装前に必ず把握）

1. **差し色 = X ブルー（#1d9bf0）**。`tailwind.config.js` で `indigo` パレットを X ブルーに再定義済み。`indigo-*` ユーティリティと CSS 変数 `--accent` がそのまま差し色。色は触らない（無彩色アイコン＋既存アクセントを維持）。
2. **アイコンは `stroke="currentColor"` で文字色を継承** → 親が無彩色なら無彩色、選択中タブ等で親が白/差し色ならそれに乗る。これで「アイコン無彩色＋差し色は状態のみ」が自動で成立。
3. **タブは既にレスポンシブ**（`tailwind.input.css` の `.tab-icon{display:none}` で PC は非表示、`@media (max-width:767px)` で `.tab-label` を隠す）。本計画では承認モック通り **PC でアイコン＋ラベル両方** を出す（Task 17 で CSS 変更）。
4. **`${p.width}×${p.height}`（lightbox.js:86）の「×」は乗算記号** — アイコンではない。**絶対に置換しない**。
5. **check_modules.mjs** は「他モジュールの export を import せず参照」を検出する。`icon()` を使う各 JS は必ず `import { icon } from "icons";` を入れること（入れ忘れると CI 相当で落ちる）。

## ファイル構成（新規 / 変更）

| 種別 | パス | 責務 |
|---|---|---|
| 新規 | `src/favgallery/static/lib/icons.js` | `icon(name, extraClass?)` ヘルパー（SVG 文字列を返す唯一の生成口） |
| 新規 | `scripts/check_icons.mjs` | スプライト未定義 `#ic-*` 参照を静的検出するガード |
| 変更 | `src/favgallery/static/index.html` | SVG スプライト追加 / import map に `icons` 追加 / 全静的グリフ置換 / `aria-label` 付与 |
| 変更 | `src/favgallery/static/lib/{posts,library,lightbox,bookshelf,cookies,timeline,mylikes,sync,main}.js` | 動的グリフを `icon()` 化 + import 追加 |
| 変更 | `scripts/tailwind.input.css` | `.icon` / `.icon-btn` / `.icon-spin` / `.icon-chevron` / タブ CSS / タップ領域 |
| 生成物 | `src/favgallery/static/style.css` | input.css から再生成（手編集しない） |

---

# Phase 0 — アイコン基盤

### Task 1: アイコンスプライトを index.html に追加

**Files:** Modify `src/favgallery/static/index.html`（`<body class="min-h-screen">` 直後・10 行目 `<div id="sidebar-overlay">` の前）

- [x] **Step 1: スプライトを `<body>` 先頭に挿入**

`<body class="min-h-screen">` の直後に以下を追加（`presentation 属性は付けない` — 色・線幅は `.icon` CSS が制御）:

```html
  <!-- アイコンスプライト（唯一のアイコン定義元）。stroke/fill は .icon CSS が currentColor で制御。 -->
  <svg width="0" height="0" aria-hidden="true" style="position:absolute" xmlns="http://www.w3.org/2000/svg"><defs>
    <symbol id="ic-refresh" viewBox="0 0 24 24"><path d="M3 12a9 9 0 0 1 15-6.7L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-15 6.7L3 16"/><path d="M8 21H3v-5"/></symbol>
    <symbol id="ic-settings" viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V15z"/></symbol>
    <symbol id="ic-loader" viewBox="0 0 24 24"><path d="M12 2v4"/><path d="m16.2 7.8 2.9-2.9"/><path d="M18 12h4"/><path d="m16.2 16.2 2.9 2.9"/><path d="M12 18v4"/><path d="m4.9 19.1 2.9-2.9"/><path d="M2 12h4"/><path d="m4.9 4.9 2.9 2.9"/></symbol>
    <symbol id="ic-menu" viewBox="0 0 24 24"><path d="M4 6h16"/><path d="M4 12h16"/><path d="M4 18h16"/></symbol>
    <symbol id="ic-heart" viewBox="0 0 24 24"><path d="M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4.05 3 5.5l7 7Z"/></symbol>
    <symbol id="ic-eye" viewBox="0 0 24 24"><path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/></symbol>
    <symbol id="ic-book" viewBox="0 0 24 24"><path d="M12 7v14"/><path d="M3 18a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h5a4 4 0 0 1 4 4 4 4 0 0 1 4-4h5a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1h-6a3 3 0 0 0-3 3 3 3 0 0 0-3-3z"/></symbol>
    <symbol id="ic-grid" viewBox="0 0 24 24"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/></symbol>
    <symbol id="ic-rows" viewBox="0 0 24 24"><path d="M3 6h18"/><path d="M3 12h18"/><path d="M3 18h18"/></symbol>
    <symbol id="ic-search" viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/></symbol>
    <symbol id="ic-list" viewBox="0 0 24 24"><path d="M8 6h13"/><path d="M8 12h13"/><path d="M8 18h13"/><path d="M3 6h.01"/><path d="M3 12h.01"/><path d="M3 18h.01"/></symbol>
    <symbol id="ic-tag" viewBox="0 0 24 24"><path d="M12.6 2.6A2 2 0 0 0 11.2 2H4a2 2 0 0 0-2 2v7.2a2 2 0 0 0 .6 1.4l8.7 8.7a2.4 2.4 0 0 0 3.4 0l6.6-6.6a2.4 2.4 0 0 0 0-3.4z"/><circle cx="7.5" cy="7.5" r="1.2"/></symbol>
    <symbol id="ic-star" viewBox="0 0 24 24"><path d="M11.5 2.8a.55.55 0 0 1 1 0l2.4 5 5.3.8a.5.5 0 0 1 .3.86l-3.9 3.8.92 5.4a.5.5 0 0 1-.73.53L12 18.9l-4.8 2.5a.5.5 0 0 1-.73-.53l.92-5.4-3.9-3.8a.5.5 0 0 1 .3-.86l5.3-.8z"/></symbol>
    <symbol id="ic-key" viewBox="0 0 24 24"><path d="m15.5 7.5 3 3L22 7l-3-3"/><path d="m21 2-9.6 9.6"/><circle cx="7.5" cy="15.5" r="5.5"/></symbol>
    <symbol id="ic-plus" viewBox="0 0 24 24"><path d="M12 5v14"/><path d="M5 12h14"/></symbol>
    <symbol id="ic-x" viewBox="0 0 24 24"><path d="M18 6 6 18"/><path d="M6 6l12 12"/></symbol>
    <symbol id="ic-chevron-left" viewBox="0 0 24 24"><path d="m15 18-6-6 6-6"/></symbol>
    <symbol id="ic-chevron-right" viewBox="0 0 24 24"><path d="m9 18 6-6-6-6"/></symbol>
    <symbol id="ic-chevron-down" viewBox="0 0 24 24"><path d="m6 9 6 6 6-6"/></symbol>
    <symbol id="ic-check" viewBox="0 0 24 24"><path d="M18 6 7 17l-5-5"/><path d="m22 10-7.5 7.5L13 16"/></symbol>
    <symbol id="ic-download" viewBox="0 0 24 24"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="M7 10l5 5 5-5"/><path d="M12 15V3"/></symbol>
  </defs></svg>
```

- [x] **Step 2: 確認** — `uv run favgallery`（または `python scripts/demo_preview.py`）で起動し、ページが今まで通り表示される（スプライトは不可視・既存グリフは未変更）。コンソールエラーが出ないこと。

- [x] **Step 3: Commit**

```bash
git add src/favgallery/static/index.html
git commit -m "feat(front): アイコンスプライト(SVG symbol 21種)を追加"
```

### Task 2: `.icon` 系 CSS を追加して再生成

**Files:** Modify `scripts/tailwind.input.css`（`:focus-visible` 定義の直後・39 行目あたり）/ 生成 `src/favgallery/static/style.css`

- [x] **Step 1: CSS を追加**

`:focus-visible { ... }` の行の直後に以下を追加:

```css
/* ---- アイコン（統一線アイコン / SVG スプライト参照） ---- */
.icon {
  width: 1.25em; height: 1.25em; display: inline-block; vertical-align: -0.18em;
  flex: none; fill: none; stroke: currentColor; stroke-width: 2;
  stroke-linecap: round; stroke-linejoin: round; pointer-events: none;
}
.icon-spin { animation: icon-spin 1s linear infinite; transform-origin: center; }
@keyframes icon-spin { to { transform: rotate(360deg); } }
.icon-chevron { transition: transform .2s ease; }
.icon-chevron.open { transform: rotate(90deg); }

/* ---- 共通アイコンボタン（同期/設定/メニュー/表示切替 等の単体ボタン） ---- */
.icon-btn {
  display: inline-flex; align-items: center; justify-content: center;
  width: 34px; height: 34px; border-radius: 8px; color: var(--ink-dim);
  background: transparent; border: 0; cursor: pointer;
  transition: background .15s, color .15s;
}
.icon-btn:hover { background: #1d1f23; color: var(--ink); }
.icon-btn .icon { width: 19px; height: 19px; }
@media (pointer: coarse) { .icon-btn { width: 44px; height: 44px; } }
```

- [x] **Step 2: CSS 再生成**

Run: `npx --yes tailwindcss@3.4.17 -c tailwind.config.js -i scripts/tailwind.input.css -o src/favgallery/static/style.css --minify`
Expected: エラーなく `style.css` が更新される。

- [x] **Step 3: Commit**

```bash
git add scripts/tailwind.input.css src/favgallery/static/style.css
git commit -m "feat(front): .icon/.icon-btn 共通スタイルを追加"
```

### Task 3: `icons.js` ヘルパーを作成し import map に登録

**Files:** Create `src/favgallery/static/lib/icons.js` / Modify `src/favgallery/static/index.html`（importmap・205-219 行）

- [x] **Step 1: `icons.js` を作成**

```js
// アイコン生成の唯一の口。SVG スプライト (#ic-NAME) を参照する <svg> 文字列を返す。
// 色・線は .icon CSS (currentColor) が制御するので、無彩色アイコン＋親の差し色が自動成立。
export function icon(name, extraClass = "") {
  const cls = extraClass ? `icon ${extraClass}` : "icon";
  return `<svg class="${cls}" aria-hidden="true"><use href="#ic-${name}"/></svg>`;
}
```

- [x] **Step 2: import map に登録** — index.html の importmap `"imports"` 末尾（`"mylikes": ...` の後）にカンマ区切りで追加:

```json
    "mylikes": "/static/lib/mylikes.js?v=__ASSET_VERSION__",
    "icons": "/static/lib/icons.js?v=__ASSET_VERSION__"
```

（`mylikes` 行末にカンマを足すのを忘れない）

- [x] **Step 3: 検証** — `node scripts/check_load.mjs && node scripts/check_modules.mjs`
Expected: 両方 `✓`（icons.js は単独で読み込め、まだ誰も import していないので漏れ報告も出ない）。

- [x] **Step 4: Commit**

```bash
git add src/favgallery/static/lib/icons.js src/favgallery/static/index.html
git commit -m "feat(front): icon() ヘルパー + import map 登録"
```

### Task 4: 未定義アイコン参照ガード `check_icons.mjs`

**Files:** Create `scripts/check_icons.mjs`

- [x] **Step 1: チェッカーを作成（先に書く＝以降の置換ミスを赤で捕まえる）**

```js
// スプライト未定義の #ic-* 参照を静的検出する。
// 参照元: index.html の <use href="#ic-NAME"> と、HTML/JS 中の icon('NAME').
// 定義元: index.html の <symbol id="ic-NAME">。未定義参照があれば exit 1。
import { readFileSync, readdirSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const staticDir = join(root, "src", "favgallery", "static");
const html = readFileSync(join(staticDir, "index.html"), "utf-8");

const defined = new Set([...html.matchAll(/<symbol\s+id="ic-([a-z0-9-]+)"/g)].map((m) => m[1]));
const refs = new Map(); // name -> where[]
const add = (name, where) => { (refs.get(name) || refs.set(name, []).get(name)).push(where); };

for (const m of html.matchAll(/href="#ic-([a-z0-9-]+)"/g)) add(m[1], "index.html(<use>)");
const libDir = join(staticDir, "lib");
const files = ["index.html", ...readdirSync(libDir).map((n) => `lib/${n}`)];
for (const rel of files) {
  const src = readFileSync(join(staticDir, rel), "utf-8");
  for (const m of src.matchAll(/\bicon\(\s*['"]([a-z0-9-]+)['"]/g)) add(m[1], `${rel}(icon())`);
}

let failed = false;
for (const [name, where] of refs) {
  if (!defined.has(name)) { console.error(`✗ "#ic-${name}" 未定義 — 参照: ${where.join(", ")}`); failed = true; }
}
if (!failed) console.log(`✓ アイコン参照 ${refs.size} 種すべて解決 (定義 ${defined.size} 種)`);
process.exit(failed ? 1 : 0);
```

- [x] **Step 2: 実行して緑を確認**

Run: `node scripts/check_icons.mjs`
Expected: `✓ アイコン参照 N 種すべて解決 (定義 21 種)`（この時点の参照は <use> 0〜少数）。

- [x] **Step 3: Commit**

```bash
git add scripts/check_icons.mjs
git commit -m "test(front): 未定義アイコン参照ガード check_icons.mjs を追加"
```

---

# Phase 1 — 静的 HTML のグリフ置換（index.html）

> 各ボタンは「グリフ文字 → `<svg class="icon"><use href="#ic-NAME"/></svg>`」へ。単体アイコンボタンは `class` を `.icon-btn` に寄せ、`aria-label` を付与。**1 タスク = 関連する数行 → 1 コミット**。各タスク後に `node scripts/check_icons.mjs` を実行。

### Task 5: サイドバー上部（同期 / 設定 / 読込中）

**Files:** Modify `src/favgallery/static/index.html:19-21`

- [x] **Step 1: 置換**

```html
<!-- L19 before --><span id="loadingIndicator" class="hidden text-base leading-none select-none">⏳</span>
<!-- L19 after  --><span id="loadingIndicator" class="hidden"><svg class="icon icon-spin" aria-hidden="true"><use href="#ic-loader"/></svg></span>

<!-- L20 before --><button id="manualSyncBtn" class="text-zinc-500 hover:text-zinc-200 text-base p-1 rounded hover:bg-zinc-800" title="今すぐ同期">⟳</button>
<!-- L20 after  --><button id="manualSyncBtn" class="icon-btn" title="今すぐ同期" aria-label="今すぐ同期"><svg class="icon" aria-hidden="true"><use href="#ic-refresh"/></svg></button>

<!-- L21 before --><button id="optionsBtn" class="text-zinc-500 hover:text-zinc-200 text-base p-1 rounded hover:bg-zinc-800" title="設定">⚙</button>
<!-- L21 after  --><button id="optionsBtn" class="icon-btn" title="設定" aria-label="設定"><svg class="icon" aria-hidden="true"><use href="#ic-settings"/></svg></button>
```

- [x] **Step 2: 確認** `node scripts/check_icons.mjs` → ✓ / ローカル起動で 3 要素が線アイコン表示・同期/設定が押せる。
- [x] **Step 3: Commit** `git commit -am "feat(front): サイドバー上部アイコン(同期/設定/読込)を線アイコン化"`

### Task 6: サイドバー見出し・本棚（リスト / タグ / お気に入り / 折りたたみ）

**Files:** Modify `src/favgallery/static/index.html:40,54,62,76,82`

- [x] **Step 1: 置換**

```html
<!-- L40 before --><div class="text-xs uppercase tracking-wider text-zinc-500">📋 リスト</div>
<!-- L40 after  --><div class="text-xs uppercase tracking-wider text-zinc-500 flex items-center gap-1"><svg class="icon" aria-hidden="true"><use href="#ic-list"/></svg> リスト</div>

<!-- L54 before -->1件のみ <span id="authorSingleIcon">▶</span>
<!-- L54 after  -->1件のみ <span id="authorSingleIcon"><svg class="icon icon-chevron" aria-hidden="true"><use href="#ic-chevron-right"/></svg></span>

<!-- L62 before -->タグ <span id="tagToggleIcon">▶</span>
<!-- L62 after  -->タグ <span id="tagToggleIcon"><svg class="icon icon-chevron" aria-hidden="true"><use href="#ic-chevron-right"/></svg></span>

<!-- L76 before --><span>⭐</span> <span>お気に入り</span>
<!-- L76 after  --><svg class="icon" aria-hidden="true"><use href="#ic-star"/></svg> <span>お気に入り</span>

<!-- L82 before --><div class="text-xs uppercase tracking-wider text-zinc-500">🏷 タグ</div>
<!-- L82 after  --><div class="text-xs uppercase tracking-wider text-zinc-500 flex items-center gap-1"><svg class="icon" aria-hidden="true"><use href="#ic-tag"/></svg> タグ</div>
```

> 注: L54/L62 の chevron は textContent ではなく `.open` クラスのトグルで回転させる（JS 側は Task 11 / Task 12 で対応）。

- [x] **Step 2: 確認** `node scripts/check_icons.mjs` → ✓
- [x] **Step 3: Commit** `git commit -am "feat(front): サイドバー見出し/本棚アイコンを線アイコン化"`

### Task 7: ツールバー（メニュー / タブ / 表示切替 / 既読 / 取得）

**Files:** Modify `src/favgallery/static/index.html:94,96-98,101,104-105`

- [x] **Step 1: 置換**

```html
<!-- L94 before -->          title="メニュー">☰</button>
<!-- L94 after  -->          title="メニュー" aria-label="メニュー"><svg class="icon" aria-hidden="true"><use href="#ic-menu"/></svg></button>

<!-- L96 before --><button id="tabLikes" class="tab-btn active"><span class="tab-icon">♥</span><span class="tab-label">いいね</span></button>
<!-- L96 after  --><button id="tabLikes" class="tab-btn active"><span class="tab-icon"><svg class="icon" aria-hidden="true"><use href="#ic-heart"/></svg></span><span class="tab-label">いいね</span></button>

<!-- L97 before --><button id="tabTimeline" class="tab-btn"><span class="tab-icon">👁</span><span class="tab-label">フォロー中</span></button>
<!-- L97 after  --><button id="tabTimeline" class="tab-btn"><span class="tab-icon"><svg class="icon" aria-hidden="true"><use href="#ic-eye"/></svg></span><span class="tab-label">フォロー中</span></button>

<!-- L98 before --><button id="tabBookshelf" class="tab-btn"><span class="tab-icon">📚</span><span class="tab-label">本棚</span></button>
<!-- L98 after  --><button id="tabBookshelf" class="tab-btn"><span class="tab-icon"><svg class="icon" aria-hidden="true"><use href="#ic-book"/></svg></span><span class="tab-label">本棚</span></button>

<!-- L100-101 before -->        <button id="layoutToggle" title="レイアウト切替"
          class="p-1.5 rounded text-zinc-400 hover:text-white hover:bg-zinc-700 transition-colors text-lg leading-none">▦</button>
<!-- L100-101 after  -->        <button id="layoutToggle" title="レイアウト切替" aria-label="レイアウト切替"
          class="icon-btn"><svg class="icon" aria-hidden="true"><use href="#ic-grid"/></svg></button>

<!-- L104 before --><button id="markSeenBtn" class="hidden bg-zinc-800 hover:bg-zinc-700 text-zinc-200 text-xs rounded-md px-3 py-1.5" title="現在いちばん上のツイートまで「ここまで見た」マークを移動">📍 ここまで既読</button>
<!-- L104 after  --><button id="markSeenBtn" class="hidden items-center gap-1 bg-zinc-800 hover:bg-zinc-700 text-zinc-200 text-xs rounded-md px-3 py-1.5" title="現在いちばん上のツイートまで「ここまで見た」マークを移動"><svg class="icon" aria-hidden="true"><use href="#ic-check"/></svg> ここまで既読</button>

<!-- L105 before --><button id="timelineRefreshBtn" class="hidden bg-indigo-600 hover:bg-indigo-500 text-white text-xs rounded-md px-3 py-1.5">⟳ 取得</button>
<!-- L105 after  --><button id="timelineRefreshBtn" class="hidden items-center gap-1 bg-indigo-600 hover:bg-indigo-500 text-white text-xs rounded-md px-3 py-1.5"><svg id="timelineRefreshIcon" class="icon" aria-hidden="true"><use href="#ic-download"/></svg> <span id="timelineRefreshLabel">取得</span></button>
```

> `markSeenBtn` / `timelineRefreshBtn` は `hidden` クラスで非表示制御されている（JS が `.hidden` を外す）。表示時に flex で並ぶよう `items-center gap-1` を追加。`hidden` のままで OK（`.hidden{display:none}` が `items-center` に優先）。`timelineRefreshBtn` はアイコンとラベルを分離（Task 14 で JS がラベル/アイコンだけ更新する前提）。

- [x] **Step 2: 確認** `node scripts/check_icons.mjs` → ✓
- [x] **Step 3: Commit** `git commit -am "feat(front): ツールバー(メニュー/タブ/表示切替/既読/取得)を線アイコン化"`

### Task 8: ライトボックス / リーダー / 設定パネル（閉じる・送り・cookies・マイいいね）

**Files:** Modify `src/favgallery/static/index.html:125-127,136,161,167`

- [x] **Step 1: 置換**

```html
<!-- L125 before --><div class="absolute top-4 right-6 text-zinc-400 hover:text-white text-3xl cursor-pointer select-none" id="lbClose">×</div>
<!-- L125 after  --><div class="absolute top-4 right-6 text-zinc-400 hover:text-white cursor-pointer select-none" id="lbClose" role="button" aria-label="閉じる"><svg class="icon" aria-hidden="true" style="width:28px;height:28px"><use href="#ic-x"/></svg></div>

<!-- L126 before --><div id="lbPrev" class="lb-nav-btn lb-prev hidden" title="前の画像 (←)">◀</div>
<!-- L126 after  --><div id="lbPrev" class="lb-nav-btn lb-prev hidden" title="前の画像 (←)" role="button" aria-label="前の画像"><svg class="icon" aria-hidden="true" style="width:24px;height:24px"><use href="#ic-chevron-left"/></svg></div>

<!-- L127 before --><div id="lbNext" class="lb-nav-btn lb-next hidden" title="次の画像 (→)">▶</div>
<!-- L127 after  --><div id="lbNext" class="lb-nav-btn lb-next hidden" title="次の画像 (→)" role="button" aria-label="次の画像"><svg class="icon" aria-hidden="true" style="width:24px;height:24px"><use href="#ic-chevron-right"/></svg></div>

<!-- L136 before --><div id="readerCloseBtn" class="absolute top-4 right-6 text-zinc-400 hover:text-white text-3xl cursor-pointer z-20">×</div>
<!-- L136 after  --><div id="readerCloseBtn" class="absolute top-4 right-6 text-zinc-400 hover:text-white cursor-pointer z-20" role="button" aria-label="閉じる"><svg class="icon" aria-hidden="true" style="width:28px;height:28px"><use href="#ic-x"/></svg></div>

<!-- L161 before --><button id="meSyncBtn" class="w-full bg-rose-700 hover:bg-rose-600 text-white rounded px-2 py-1 text-xs">♥ マイいいね同期</button>
<!-- L161 after  --><button id="meSyncBtn" class="w-full inline-flex items-center justify-center gap-1 bg-rose-700 hover:bg-rose-600 text-white rounded px-2 py-1 text-xs"><svg class="icon" aria-hidden="true"><use href="#ic-heart"/></svg> マイいいね同期</button>

<!-- L167 before --><button id="cookieSetBtn" class="w-full bg-zinc-800 hover:bg-zinc-700 text-zinc-200 rounded px-2 py-1 text-xs">🔑 cookies を設定 / 更新</button>
<!-- L167 after  --><button id="cookieSetBtn" class="w-full inline-flex items-center justify-center gap-1 bg-zinc-800 hover:bg-zinc-700 text-zinc-200 rounded px-2 py-1 text-xs"><svg class="icon" aria-hidden="true"><use href="#ic-key"/></svg> cookies を設定 / 更新</button>
```

- [x] **Step 2: 確認** `node scripts/check_icons.mjs` → ✓ / ライトボックス・リーダー・設定ポップオーバーを開いて目視。
- [x] **Step 3: Commit** `git commit -am "feat(front): ライトボックス/リーダー/設定の記号を線アイコン化"`

---

# Phase 2 — 動的 JS のグリフ置換

> 各ファイル冒頭に `import { icon } from "icons";` を追加（既存の import 行群に並べる）。`icon('x')` の挿入は HTML 文字列内なので `${icon('x')}` でテンプレートに埋め込む。各タスク後に **`node scripts/check_modules.mjs && node scripts/check_icons.mjs`** を実行（import 漏れ + 未定義参照の二重ガード）。

### Task 9: posts.js（いいねボタン・件数・既読ライン・取得中）

**Files:** Modify `src/favgallery/static/lib/posts.js`（冒頭 import 群 / L74,156,277,280,573,584,597,600,608 / L372,376 のヒント文）

- [x] **Step 1: import 追加** — 既存 import 群の末尾に `import { icon } from "icons";`

- [x] **Step 2: 置換**（`♥` like-btn は 3 箇所同型 / `♥ count` は 3 箇所同型 / `📍` は 2 箇所）

```js
// L74  before: `<div class="seen-divider seen-divider-barrier">📍 ここまで見た</div>`
// L74  after : `<div class="seen-divider seen-divider-barrier">${icon('check')} ここまで見た</div>`

// L156 before: ...innerHTML = `<div class="text-zinc-400 text-sm p-6">⏳ X から @${escapeHtml(author)} の投稿を取得中… (10〜20秒かかることがあります)</div>`;
// L156 after : ...innerHTML = `<div class="text-zinc-400 text-sm p-6">${icon('loader','icon-spin')} X から @${escapeHtml(author)} の投稿を取得中… (10〜20秒かかることがあります)</div>`;

// L277 / L573 / L597 before: `<div class="like-btn" title="X でいいね + 保存">♥</div>`
// L277 / L573 / L597 after : `<div class="like-btn" title="X でいいね + 保存">${icon('heart')}</div>`

// L280 / L584 before: `<div class="text-zinc-400 text-[11px]">♥ ${p.favorite_count.toLocaleString()}</div>`
// L280 / L584 after : `<div class="text-zinc-400 text-[11px] inline-flex items-center gap-1">${icon('heart')} ${p.favorite_count.toLocaleString()}</div>`

// L608 before: `<div class="opacity-60 text-[11px]">♥ ${p.favorite_count.toLocaleString()}</div>`
// L608 after : `<div class="opacity-60 text-[11px] inline-flex items-center gap-1">${icon('heart')} ${p.favorite_count.toLocaleString()}</div>`

// L600 before: const seenBadge = isSeenBoundary ? `<div class="reel-seen-badge">📍 ここまで見た</div>` : '';
// L600 after : const seenBadge = isSeenBoundary ? `<div class="reel-seen-badge">${icon('check')} ここまで見た</div>` : '';

// L372 before: <div class="hint">右上の「⟳ 取得」でフォロー中の投稿を読み込めます</div>
// L372 after : <div class="hint">右上の「取得」ボタンでフォロー中の投稿を読み込めます</div>

// L376 before: <div class="hint">ヘッダーの ⟳ ボタンで X のいいねを同期できます</div>
// L376 after : <div class="hint">ヘッダーの「同期」ボタンで X のいいねを同期できます</div>
```

- [x] **Step 3: 確認** `node scripts/check_modules.mjs && node scripts/check_icons.mjs` → ✓
- [x] **Step 4: Commit** `git commit -am "feat(front): posts.js のグリフを線アイコン化"`

### Task 10: lightbox.js（いいねボタン・件数・処理中）

**Files:** Modify `src/favgallery/static/lib/lightbox.js`（冒頭 import / L63,84,85,125）

- [x] **Step 1: import 追加** — `import { icon } from "icons";`

- [x] **Step 2: 置換**

```js
// L63 before: ? `<button id="lbLikeBtn" class="bg-rose-600 hover:bg-rose-500 text-white text-sm rounded px-3 py-1">♥ いいね & 保存</button>`
// L63 after : ? `<button id="lbLikeBtn" class="inline-flex items-center gap-1 bg-rose-600 hover:bg-rose-500 text-white text-sm rounded px-3 py-1">${icon('heart')} いいね & 保存</button>`

// L84 before: ${p.favorite_count ? `<span>♥ ${p.favorite_count.toLocaleString()}</span>` : ''}
// L84 after : ${p.favorite_count ? `<span class="inline-flex items-center gap-1">${icon('heart')} ${p.favorite_count.toLocaleString()}</span>` : ''}

// L85 before: ${p.view_count ? `<span>👁 ${p.view_count.toLocaleString()}</span>` : ''}
// L85 after : ${p.view_count ? `<span class="inline-flex items-center gap-1">${icon('eye')} ${p.view_count.toLocaleString()}</span>` : ''}

// L86 — 触らない（${p.width}×${p.height} の × は乗算記号）

// L125 before: lbLike.textContent = '⏳ いいね & 保存中…';
// L125 after : lbLike.innerHTML = `${icon('loader','icon-spin')} いいね & 保存中…`;
```

- [x] **Step 3: 確認** `node scripts/check_modules.mjs && node scripts/check_icons.mjs` → ✓ / ライトボックスで heart/eye と処理中表示を目視。
- [x] **Step 4: Commit** `git commit -am "feat(front): lightbox.js のグリフを線アイコン化(×乗算は保持)"`

### Task 11: library.js（リスト操作・チップ・トグル chevron）

**Files:** Modify `src/favgallery/static/lib/library.js`（冒頭 import / L20,97,140,183,264,274）

- [x] **Step 1: import 追加** — `import { icon } from "icons";`

- [x] **Step 2: 置換**

```js
// L20 before: const label = data.scanning ? `📚 スキャン中… ${data.post_count.toLocaleString()}` : `${data.post_count.toLocaleString()} posts`;
// L20 after : const label = data.scanning ? `スキャン中… ${data.post_count.toLocaleString()}` : `${data.post_count.toLocaleString()} posts`;
//   （postCount は <span> の textContent。SVG を混ぜると崩れるため絵文字は外しテキストのみに。）

// L140 before: <button class="list-del text-zinc-500 hover:text-rose-400 px-1" title="削除" data-list-id="${l.id}">×</button>
// L140 after : <button class="list-del text-zinc-500 hover:text-rose-400 px-1" title="削除" aria-label="削除" data-list-id="${l.id}">${icon('x')}</button>

// L183 before: chips.push(`<span class="chip active rounded px-3 py-1 text-sm cursor-pointer" data-clear="list_id">📋 ${escapeHtml(l.name)} ✕</span>`);
// L183 after : chips.push(`<span class="chip active rounded px-3 py-1 text-sm cursor-pointer inline-flex items-center gap-1" data-clear="list_id">${icon('list')} ${escapeHtml(l.name)} ${icon('x')}</span>`);
```

chevron トグル（L97 / L264 / L274）は textContent 切替を `.open` クラス切替に変更:

```js
// L97 before: btn.querySelector('span').textContent = btn.dataset.open === '1' ? '▼' : '▶';
// L97 after : btn.querySelector('span').firstElementChild.classList.toggle('open', btn.dataset.open === '1');

// L264 before: $('#tagToggleIcon').textContent = collapsed ? '▶' : '▼';
// L264 after : $('#tagToggleIcon').firstElementChild.classList.toggle('open', !collapsed);

// L274 before: btn.querySelector('span').textContent = opening ? '▶' : '▼';
// L274 after : btn.querySelector('span').firstElementChild.classList.toggle('open', opening);
```

> 前提: これらの `span` は Task 6（index.html）または JS 生成側で chevron SVG（`.icon.icon-chevron`）を内包していること。JS 生成のリスト行トグル（L97/L274 が指す `span`）は、生成テンプレート側のグリフ（`▶`）も `${icon('chevron-right','icon-chevron')}` に置換する必要がある。実装時に該当テンプレート行（`data-open` を持つ行生成箇所）を `rg "data-open" src/favgallery/static/lib/library.js` で特定し、初期グリフを SVG に置換すること。`.open` クラスで 90° 回転（既定 chevron-right が下向きに）。

- [x] **Step 3: 確認** `node scripts/check_modules.mjs && node scripts/check_icons.mjs` → ✓ / リスト削除・フィルタチップ・作者/タグの折りたたみ開閉を目視（chevron が回る）。
- [x] **Step 4: Commit** `git commit -am "feat(front): library.js のグリフ/トグルを線アイコン化"`

### Task 12: bookshelf.js / cookies.js（お気に入り・DL中・閉じる）

**Files:** Modify `src/favgallery/static/lib/bookshelf.js`（import / L65,323）, `src/favgallery/static/lib/cookies.js`（import / L38）

- [x] **Step 1: import 追加** — 両ファイル冒頭に `import { icon } from "icons";`

- [x] **Step 2: 置換**

```js
// bookshelf.js L65 before: <button class="fav-btn ${favClass}" data-fav-book="${b.id}" title="お気に入り">♥</button>
// bookshelf.js L65 after : <button class="fav-btn ${favClass}" data-fav-book="${b.id}" title="お気に入り" aria-label="お気に入り">${icon('heart')}</button>

// bookshelf.js L323 before: badge = '<span class="text-indigo-400">⏳ ' + escapeHtml(item.progress || 'DL中') + '</span>';
// bookshelf.js L323 after : badge = '<span class="text-indigo-400 inline-flex items-center gap-1">' + icon('loader','icon-spin') + ' ' + escapeHtml(item.progress || 'DL中') + '</span>';

// cookies.js L38 before: <button id="cookieCloseBtn" class="text-zinc-400 hover:text-white text-xl leading-none">×</button>
// cookies.js L38 after : <button id="cookieCloseBtn" class="text-zinc-400 hover:text-white leading-none" aria-label="閉じる">${icon('x')}</button>
```

- [x] **Step 3: 確認** `node scripts/check_modules.mjs && node scripts/check_icons.mjs` → ✓
- [x] **Step 4: Commit** `git commit -am "feat(front): bookshelf.js/cookies.js のグリフを線アイコン化"`

### Task 13: main.js（表示切替アイコンの動的スワップ）

**Files:** Modify `src/favgallery/static/lib/main.js`（import / L53）

- [x] **Step 1: import 追加** — `import { icon } from "icons";`

- [x] **Step 2: 置換**

```js
// L53 before: $('#layoutToggle').textContent = mode === 'reel' ? '▤' : '▦';
// L53 after : $('#layoutToggle').innerHTML = icon(mode === 'reel' ? 'rows' : 'grid');
```

> 意味: マソンリー時はグリッド(`#ic-grid`)、リール時は行(`#ic-rows`)を表示し「今のモード」を示す（現状の ▦/▤ と同じ対応）。

- [x] **Step 3: 確認** `node scripts/check_modules.mjs && node scripts/check_icons.mjs` → ✓ / 表示切替ボタンを押してアイコンが切り替わる。
- [x] **Step 4: Commit** `git commit -am "feat(front): main.js 表示切替アイコンの動的スワップ"`

### Task 14: timeline.js / mylikes.js / sync.js（取得ボタン・同期中・ローダー）

**Files:** Modify `src/favgallery/static/lib/timeline.js`（import / L80,96）, `mylikes.js`（import / L25）, `sync.js`（L20,26）

- [x] **Step 1: import 追加** — timeline.js と mylikes.js 冒頭に `import { icon } from "icons";`（sync.js は後述の通り icon 不使用なら不要）

- [x] **Step 2: timeline.js — アイコンとラベルを分離して更新**（Task 7 で `#timelineRefreshIcon` と `#timelineRefreshLabel` を用意済み）

```js
// L80 before: $('#timelineRefreshBtn').textContent = '⏳ 取得中…';
// L80 after :
//   $('#timelineRefreshIcon').outerHTML = icon('loader', 'icon-spin');  // ※ outerHTML 置換で id を保てないため下記方式を採用
```

実装は **id を保つため innerHTML ではなくクラス/参照で切替** する。L80 と L96 を次のヘルパーに集約:

```js
// timeline.js 内に追加（モジュール内のローカル関数）
function setRefreshState(loading, tag = '') {
  const ic = document.querySelector('#timelineRefreshIcon use');
  const label = document.getElementById('timelineRefreshLabel');
  const svg = document.getElementById('timelineRefreshIcon');
  if (ic) ic.setAttribute('href', loading ? '#ic-loader' : '#ic-download');
  if (svg) svg.classList.toggle('icon-spin', loading);
  if (label) label.textContent = loading ? '取得中…' : `取得${tag}`;
}
// L80 相当: setRefreshState(true);
// L96 相当: setRefreshState(false, tag);
```

L80・L96 の元の textContent 代入をこの呼び出しに置換する。

- [x] **Step 3: mylikes.js — 同期中行**

```js
// L25 before: if (s.running) lines.push('⏳ 同期中…');
// L25 after : if (s.running) lines.push(`${icon('loader','icon-spin')} 同期中…`);
//   ※ lines が innerHTML として結合される場合のみ。textContent 結合なら絵文字を外し '同期中…' だけにする。
//      実装時に lines の描画先を確認（rg "lines" src/favgallery/static/lib/mylikes.js）。
```

- [x] **Step 4: sync.js — 砂時計フリップを CSS 回転ローダーへ簡素化**

`loadingIndicator`（Task 5 で `#ic-loader` + `.icon-spin` 済み）は CSS で回転するので、sync.js の `⏳`/`⌛` テキスト切替は不要。表示/非表示の制御だけ残す:

```js
// L20 before: el.textContent = _hourglassFlip ? '⌛' : '⏳';
// L26 before: el.textContent = '⏳';
// → 両方とも textContent 代入を削除。表示制御は既存の .hidden トグル（el.classList.add/remove('hidden')）に一本化。
//   _hourglassFlip 用の setInterval があれば撤去（回転は CSS が担当）。実装時に sync.js 全体を読み、
//   表示=remove('hidden') / 非表示=add('hidden') だけ残す。
```

- [x] **Step 5: 確認** `node scripts/check_modules.mjs && node scripts/check_icons.mjs && node scripts/check_js.mjs` → ✓ / 同期・取得を実行してローダー回転と「取得中…」表示を目視。
- [x] **Step 6: Commit** `git commit -am "feat(front): timeline/mylikes/sync のローダー・取得表示を線アイコン化"`

### Task 15: 残グリフの全消し確認（回帰ガード）

**Files:** 走査のみ

- [x] **Step 1: 対象グリフが残っていないか全走査**

Run:
```bash
rg -n "⟳|⚙|⏳|⌛|☰|👁|📚|▦|▤|📍|📋|🏷|⭐|🔑|◀|▶|▼|▲" src/favgallery/static/index.html src/favgallery/static/lib/
```
Expected: ヒット 0（コメント中の説明文に残る分は可。UI 文字列のグリフが 0 であること）。`♥` は `.like-btn`/`fav-btn`/件数を置換済みのはず → `rg -n "♥" src/favgallery/static` でも UI 箇所 0 を確認。`×` は乗算記号（lightbox.js:86）と、もし見落としがあれば確認。

- [x] **Step 2: `list-star`（黄色 ⭐ 表示）の有無を確認** — `rg -n "list-star" src/favgallery/static` で発生源を特定。グリフ ⭐ をテキストで置いている場合は `${icon('star')}` に置換し、`.list-star` の `color:#eab308` を `color: var(--ink)` へ（無彩色化）。該当なしならスキップ。

- [x] **Step 3: 全チェック緑** `node scripts/check_js.mjs && node scripts/check_load.mjs && node scripts/check_modules.mjs && node scripts/check_icons.mjs && uv run pytest -q`
- [x] **Step 4: Commit**（修正があれば）`git commit -am "fix(front): 残グリフ・色付き星の無彩色化"`

---

# Phase 3 — トーン / トークン整理（D）+ 配置（B）

### Task 16: フォーカスリング・ボタン共通感の統一

**Files:** Modify `scripts/tailwind.input.css` → 再生成

- [x] **Step 1:** 既存 `:focus-visible`（L39）はグローバル定義済み。`.icon-btn` にもフォーカス時の視認性を担保するため、`.icon-btn:focus-visible { background:#1d1f23; color:var(--ink); }` を `.icon-btn:hover` の直後に追加。
- [x] **Step 2:** CSS 再生成（共通コマンド）。
- [x] **Step 3:** 確認 — キーボード Tab で各アイコンボタンにリングが出る。
- [x] **Step 4: Commit** `git commit -am "feat(front): アイコンボタンの focus-visible を統一"`

### Task 17: タブを PC でもアイコン＋ラベル表示に

**Files:** Modify `scripts/tailwind.input.css`（`.tab-icon` 周辺 L200-205）→ 再生成

- [x] **Step 1: CSS 変更**

```css
/* before */
.tab-icon { display: none; }
@media (max-width: 767px) {
  .tab-btn { padding: 6px 10px; font-size: 16px; }
  .tab-label { display: none; }
  .tab-icon { display: inline; }
}
/* after */
.tab-btn { display: inline-flex; align-items: center; gap: 6px; }
.tab-icon { display: inline-flex; }
.tab-icon .icon { width: 16px; height: 16px; }
@media (max-width: 767px) {
  .tab-btn { padding: 8px 12px; font-size: 16px; }   /* タップ領域拡大 */
  .tab-label { display: none; }                       /* スマホはアイコンのみ */
}
```

- [x] **Step 2:** CSS 再生成。
- [x] **Step 3:** 確認 — PC でタブが「アイコン＋ラベル」、スマホ幅でアイコンのみ・選択中タブだけ差し色（自動）。
- [x] **Step 4: Commit** `git commit -am "feat(front): タブを PC でアイコン+ラベル表示に(承認モック準拠)"`

---

# Phase 4 — スマホ最適化（C）

### Task 18: タップ領域とサイドバー余白の調整

**Files:** Modify `scripts/tailwind.input.css` → 再生成

- [x] **Step 1:** `@media (pointer: coarse)` で `.icon-btn` を 44px 化（Task 2 で実施済み）。加えて、サイドバーのリスト/作者行（`.s-row` 相当の実セレクタ）に十分な行高があるか確認し、必要なら `@media (max-width: 767px)` 内で行の `padding` を増やす。具体セレクタは実装時に index.html のサイドバー行クラスを確認して対象を決める（`#listSidebar`/`#authorList` の行）。
- [x] **Step 2:** ライトボックスのナビ（`.lb-nav-btn` 46px）・閉じる（28px アイコン）はタッチで十分。`@media (pointer: coarse)` で `.list-del`（×）のヒット領域を `min-width:44px;min-height:44px` に拡大。
- [x] **Step 3:** CSS 再生成。
- [x] **Step 4:** 確認 — スマホ実機 or DevTools デバイスモードでタブ・アイコンボタン・リスト行・削除が押しやすい。
- [x] **Step 5: Commit** `git commit -am "feat(front): スマホのタップ領域・サイドバー行高を調整"`

---

# Phase 5 — 検証・バージョン・デプロイ

### Task 19: 全検証 + 目視スモーク

- [x] **Step 1: 静的＋回帰**
```bash
node scripts/check_js.mjs && node scripts/check_load.mjs && node scripts/check_modules.mjs && node scripts/check_icons.mjs
uv run pytest -q
```
Expected: 全 ✓ / pytest 250 件 PASS。

- [x] **Step 2: CSS 最終再生成**（input.css 変更が漏れなく反映されているか）
```bash
npx --yes tailwindcss@3.4.17 -c tailwind.config.js -i scripts/tailwind.input.css -o src/favgallery/static/style.css --minify
git diff --stat src/favgallery/static/style.css
```

- [x] **Step 3: 目視スモーク** — `docs/smoke-checklist.md` に沿って PC + スマホで全アイコン・タブ切替・ライトボックス・リーダー・設定・同期/取得・リスト操作・折りたたみを確認。色付きが残っていない／無彩色＋差し色のみであること。

### Task 20: バージョン bump + デプロイ

- [x] **Step 1: SemVer 判定** — 見た目の刷新（機能維持）につき **MINOR**。`pyproject.toml` の version を `0.4.7` → `0.5.0` に。
- [x] **Step 2: プロダクト spec 同期** — `.company/products/projects/personal/favgallery.md` の version 記述・マイルストーン（M5: UI 刷新）を更新（メインHALO 側 / halo-version-bump 連動。dev-HALO は report で通知）。
- [x] **Step 3: Commit**
```bash
git add pyproject.toml
git commit -m "chore(version): 0.5.0 — UI 刷新(アイコン統一/無彩色+差し色/スマホ最適化)"
```
- [x] **Step 4: デプロイ** — GitHub main へ push（Railway 自動デプロイ）。`curl -sI https://favgallery.hyota.cloud` の `X-App-Version` が `0.5.0` になるのを確認。本番でアイコン表示・キャッシュバスト（`?v=` 資産ハッシュ更新）を確認。

---

## Self-Review（この計画の自己点検）

**1. Spec coverage（spec の各節 → タスク対応）:**
- A アイコン体系（スプライト/icons.js/差し替えマップ 21種）→ Task 1-4, 5-8, 9-14 ✅
- B 配置・分かりやすさ（同期↔取得の区別 / aria-label / 共通ボタン）→ Task 7（download/refresh 分離）, 全置換タスクで aria-label, Task 2/16（.icon-btn）✅
- C 押しやすさ・スマホ（44px / タブ全幅 / サイドバー）→ Task 2, 17, 18 ✅
- D トーン（無彩色＋差し色 / 境界 / focus）→ currentColor 設計（Task 3）, Task 16, 15-Step2（色付き星）✅
- テスト戦略（check 4本 + pytest + 未定義参照ガード）→ Task 4, 各タスク検証, Task 19 ✅
- 実装順（7 フェーズ）→ Phase 0-5 に対応 ✅

**2. Placeholder scan:** 「実装時に確認」は library.js のトグル span 生成箇所 / mylikes.js の描画先 / sync.js の表示制御 / list-star の 4 点のみ。いずれも「`rg` で特定する具体手順」を併記済み（純粋なプレースホルダではなく、コードに依存する分岐の明示）。それ以外に TBD/TODO なし。

**3. Type/名前整合:** `icon(name, extraClass)` のシグネチャは Task 3 で定義し、Task 9-14 で同形で使用。スプライト `#ic-*` 名（21種）と差し替えマップ・各 `<use>`/`icon()` 呼び出しが一致（check_icons.mjs が機械検証）。`#timelineRefreshIcon`/`#timelineRefreshLabel` は Task 7 で定義し Task 14 で参照。

> 残務メモ: Task 11 / 14 の「実装時 rg 特定」箇所は、サブエージェント実行時に該当行を読んでから確定する（仕様は上記で固定済み）。

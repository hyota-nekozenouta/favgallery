# Contributing to FavGallery

FavGallery への貢献を歓迎します。Issue / PR は気軽にどうぞ。

## 開発環境

Python 3.12+ と [uv](https://docs.astral.sh/uv/) が必要です。

```bash
git clone https://github.com/<your-username>/favgallery.git
cd favgallery
uv sync
PYTHONPATH=src uv run uvicorn favgallery.server:app --reload --port 8000
```

## テスト

```bash
uv run python -m pytest -q
uv run ruff check
```

フロントエンド（ES Modules）の静的検証:

```bash
node scripts/check_js.mjs       # 構文
node scripts/check_load.mjs     # モジュール読み込みグラフ
node scripts/check_modules.mjs  # import 漏れ検出
node scripts/check_icons.mjs    # SVG アイコン参照の解決
```

## CSS（Tailwind 事前生成）

CSS は CDN ではなく事前生成（`src/favgallery/static/style.css`・コミット済み）。
クラスを追加・変更したら再生成してコミットする:

```bash
npx --yes tailwindcss@3.4.17 -c tailwind.config.js -i scripts/tailwind.input.css -o src/favgallery/static/style.css --minify
```

## プルリクエスト

- 1 PR = 1 つの論理変更。コミットメッセージは conventional commits 風（`feat:` / `fix:` / `docs:` / `refactor:` / `test:` / `chore:`）。
- PR 前に上記のテスト・JS 検証・ruff がすべて緑であることを確認してください。
- 機能追加・UI 変更には簡単な説明（できればスクリーンショット）を添えてください。

## ライセンス

貢献は本プロジェクトのライセンス（AGPL-3.0-or-later）の下で受け入れられます。

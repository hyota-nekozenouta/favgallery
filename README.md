# FavGallery

X(Twitter) でいいねした画像・動画を、自分のサーバーに保存して快適に見返す **セルフホスト型メディアアーカイブ**。漫画（画像シーケンス）リーダー内蔵。

> Self-hosted archive & viewer for your own X (Twitter) liked media — with a built-in manga reader.

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE)

## できること

- X のいいね画像・動画をアーカイブ（手動同期 + 10 分クールダウン付き自動同期）
- マソンリー / リール表示、作者・タグ・リスト・検索フィルタ
- 漫画リーダー（本棚 + RTL スワイプ）
- ローカル保存 or Cloudflare R2（任意）
- 画像の重複排除（Pillow + imagehash）

> スクリーンショット: `docs/screenshot.png`（後で追加）

## クイックスタート

どちらも **ゼロ設定で起動**します（認証なし・R2 なし・ローカル保存）。起動後、⚙ 設定 → 🔑 から X の cookies を貼り付けると同期が有効になります。

### Docker（推奨）

```bash
git clone https://github.com/<your-username>/favgallery.git
cd favgallery
docker compose up --build
# → http://localhost:8000   （データは ./data に永続）
```

### uv（開発・ローカル）

```bash
git clone https://github.com/<your-username>/favgallery.git
cd favgallery
uv sync
PYTHONPATH=src uv run uvicorn favgallery.server:app --host 0.0.0.0 --port 8000
# → http://localhost:8000   （ライブラリは ./data/library に自動作成）
```

## 設定（環境変数）

すべて任意。`.env.example` をコピーして `.env` を作成（Docker は `docker-compose.yml` のコメント参照）。

| 変数 | 説明 |
|---|---|
| `FAVGALLERY_USER` / `FAVGALLERY_PASSWORD` | Basic 認証。**両方設定しないと認証は無効**（下の警告参照） |
| `FAVGALLERY_LIBRARY_ROOT` | ライブラリ保存先（既定: `./data/library`） |
| `R2_ACCOUNT_ID` / `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` / `R2_BUCKET_NAME` | Cloudflare R2 保存（4 つ揃って有効・任意） |
| `GALLERY_DL_COOKIES` | 起動時に `cookies.txt` を初期投入（任意・通常は UI 設定でOK） |
| `FAVGALLERY_AUTOSYNC_ON_LOAD` | ページ読み込み時の自動同期（既定 on・`0` で無効） |

> ⚠️ **セキュリティ**: `FAVGALLERY_USER` と `FAVGALLERY_PASSWORD` の **両方** を設定しない限り認証は無効です。インターネットに公開する場合は必ず両方を設定してください。設定しないと、URL を知る誰もがあなたのライブラリと cookies にアクセスできます。

## X cookies の設定

1. ブラウザ拡張などで X の `cookies.txt`（Netscape 形式）を書き出す
2. アプリの ⚙ 設定 → 🔑「cookies を設定 / 更新」に貼り付ける
3. 同期ボタンでいいねを取り込む

cookies は **あなたの instance 内にのみ** 保存され、外部へ送信されません。

## 自分のサーバーへ公開

Railway + Cloudflare での公開手順は [DEPLOY.md](DEPLOY.md) を参照。Docker が動く VPS なら `docker compose` だけでも公開できます。**公開時は必ず Basic 認証（上記）を設定**してください。

## 開発

```bash
uv sync
uv run python -m pytest -q     # テスト
# JS 静的検証
node scripts/check_js.mjs && node scripts/check_modules.mjs && node scripts/check_load.mjs && node scripts/check_icons.mjs
```

CSS（Tailwind 事前生成）を変更したら再生成:

```bash
npx --yes tailwindcss@3.4.17 -c tailwind.config.js -i scripts/tailwind.input.css -o src/favgallery/static/style.css --minify
```

詳細は [CONTRIBUTING.md](CONTRIBUTING.md)。

## 技術スタック

Python 3.12 / FastAPI / uvicorn / SQLite / gallery-dl / Pillow + imagehash / Cloudflare R2（任意）/ Tailwind（事前生成）/ ES Modules。

## 免責・利用上の注意

- 本ソフトの利用にあたり、**X(Twitter) の利用規約および各メディアの著作権を遵守する責任は利用者自身にあります**。スクレイピング・保存・閲覧が各サービスの規約や各国法に適合するかは、利用者ご自身でご判断ください。
- 取得・保存するメディアには第三者の著作物が含まれます。私的利用の範囲を超える公開・再配布は行わないでください。
- 本ソフトは **無保証** で提供されます（AGPL-3.0）。利用により生じたいかなる損害についても作者は責任を負いません。

## ライセンス

[AGPL-3.0-or-later](LICENSE)。改変版をネットワーク経由のサービスとして提供する場合、利用者にソースコードを提供する義務があります。

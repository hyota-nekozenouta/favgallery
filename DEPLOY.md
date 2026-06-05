# Archive — Railway + Cloudflare デプロイガイド

## 概要

Archive（xlikes-viewer）を Railway に WEB アプリとしてデプロイし、Cloudflare でカスタムドメインを設定する手順。

## 前提

- Railway アカウント（railway.app）
- Cloudflare アカウント（カスタムドメインを管理している）
- GitHub リポジトリに HALO/projects/archive/ がある状態

---

## 1. Railway デプロイ

### 1-1. プロジェクト作成

1. Railway ダッシュボード（railway.app）→ **New Project** → **Deploy from GitHub repo**
2. リポジトリを選択
3. **Root Directory** を `projects/archive` に設定
   （`railpack.json` / `Procfile` / `pyproject.toml` / `uv.lock` がこの階層にある。`src/` はその下のパッケージ。
   ここを `projects/archive/src` にすると `railpack.json` / `Procfile` が見つからずビルドが壊れる）

### 1-2. ビルド設定

Railway は **Railpack**（Nixpacks の後継ビルダー）で自動ビルドする。Root Directory 直下を解析し、
`.python-version` / `pyproject.toml` から Python + uv を検出して、**`uv sync --locked`**（`uv.lock` で固定された
依存）をインストールし、起動コマンドは `Procfile` の `web:` 行を使う。

- **依存の正典は `pyproject.toml` + `uv.lock`**。依存を足したら `uv lock` で `uv.lock` を更新すること
  （`uv sync --locked` は lock がズレているとビルドが失敗する＝ズレを早期に検知できる）。
- **システムパッケージ（ffmpeg 等）は `railpack.json` の `deploy.aptPackages` で追加する**（gallery-dl が
  実行時に ffmpeg を呼ぶため。動画の取りこぼし・画質低下を防ぐ）。
- `nixpacks.toml` は使わない（Railpack は読まない）。誤って置くと実態とズレて混乱の元になる。

> このリポジトリに `Dockerfile` は同梱していない。Docker ビルドを使いたい場合は別途用意する必要がある。

### 1-3. ボリューム設定（メディアデータ永続化）

1. Railway プロジェクト → **Volumes** → **Add Volume**
2. Mount Path: `/data`

> **注意**: ボリュームを付けないとコンテナ再起動でメディアデータが消える。

### 1-4. 環境変数の設定

Railway ダッシュボード → **Variables** タブで以下を設定する:

| 変数名 | 値 | 説明 |
|-------|-----|------|
| `ARCHIVE_USER` | （ユーザー名） | Basic 認証のユーザー名 |
| `ARCHIVE_PASSWORD` | （パスワード） | Basic 認証のパスワード（強いパスワードを使うこと） |
| `ARCHIVE_LIBRARY_ROOT` | `/data/library` | メディアライブラリの保存先（ボリューム内） |
| `R2_ACCOUNT_ID` | （Cloudflare アカウント ID） | R2 ストレージ用。4 変数すべて揃って初めて R2 が有効になる |
| `R2_ACCESS_KEY_ID` | （R2 アクセスキー ID） | 同上 |
| `R2_SECRET_ACCESS_KEY` | （R2 シークレットアクセスキー） | 同上 |
| `R2_BUCKET_NAME` | （R2 バケット名） | 同上 |
| `GALLERY_DL_COOKIES` | （cookies.txt の中身） | X 同期用。起動時に cookies.txt へ書き出される（§5 参照） |

> `ARCHIVE_USER` と `ARCHIVE_PASSWORD` のどちらかが空の場合、認証なしで動作する。
> 本番環境では必ず両方を設定すること。

> `R2_*` 4 変数はいずれか 1 つでも欠けると R2 保存が無音で無効化され、メディアはボリュームにのみ
> 保存される。漫画アップロードや同期メディアを R2 に保存するなら 4 つすべて設定すること。

`PORT` は Railway が自動で注入するので設定不要。

### 1-5. デプロイ確認

デプロイ完了後、Railway が発行した URL（例: `https://archive-xxxx.up.railway.app`）にアクセス。
Basic 認証のダイアログが表示されれば成功。

---

## 2. Cloudflare カスタムドメイン設定

### 2-1. Railway 側でカスタムドメインを追加

1. Railway プロジェクト → **Settings** → **Networking** → **Add Custom Domain**
2. 使用するドメイン（例: `archive.example.com`）を入力
3. Railway が CNAME レコードの向き先を表示する（例: `xyz.railway.app`）

### 2-2. Cloudflare DNS 設定

1. Cloudflare ダッシュボード → 対象ドメイン → **DNS** タブ
2. **Add Record**:
   - Type: `CNAME`
   - Name: `archive`（サブドメイン部分）
   - Target: Railway が指示した CNAME ターゲット
   - Proxy status: **Proxied（オレンジ雲）** に設定（Cloudflare 経由になる）

### 2-3. SSL/TLS 設定

Cloudflare の SSL/TLS モードを確認する:

- **Full (strict)** 推奨: Cloudflare ↔ Railway 間も暗号化
- Railway は自動で TLS 証明書を発行するので **Full (strict)** で問題なし

設定: Cloudflare → **SSL/TLS** → **Overview** → **Full (strict)** を選択

### 2-4. 動作確認

`https://archive.example.com` にアクセスして Basic 認証が表示されることを確認する。

---

## 3. データの取り込み（初期セットアップ）

Railway 上では Windows 向けの `xlikes.exe` は動かない。
メディアの同期・ダウンロードは現状デスクトップ版（Windows）で行い、
ライブラリデータ（`data/library/` 配下）をサーバに転送して使う形になる。

### ライブラリを Railway にアップロードする方法

Railway ボリューム内にファイルを転送するには Railway CLI を使う:

```bash
# Railway CLI インストール
npm install -g @railway/cli

# ログイン
railway login

# プロジェクトに接続
railway link <project-id>

# シェルでファイルをコピー（サービス内に入る）
railway run --service archive rsync -avz ./data/library/ /data/library/
```

または `scp` や rsync を Railway の SSH 機能（有料プラン）で使う方法もある。

---

## 4. ffmpeg について

- デスクトップ版: `projects/archive/ffmpeg/bin/ffmpeg.exe`（Windows 専用同梱バイナリ）
- Railway（Linux）: `railpack.json` の `deploy.aptPackages` で `ffmpeg` を apt からインストール
- gallery-dl は自動で PATH 上の `ffmpeg` コマンドを使用する

---

## 5. cookies.txt について

gallery-dl による X(Twitter) アクセスには `cookies.txt` が必要。

**推奨（Railway）**: `GALLERY_DL_COOKIES` 環境変数に cookies.txt の中身をそのまま貼り付ける。
アプリ起動時に内容が `cookies.txt` へ書き出されるため、ファイル転送や CLI は不要。
空 / 未設定なら既存の `cookies.txt` をそのまま使う（上書きしない）。

**代替**: `cookies.txt` をボリューム内に直接配置する。
現状の gallery-dl 設定パス: `/data/cookies.txt`（ポータブルレイアウト外の場合）

---

## ローカルでの動作確認

このリポジトリに `Dockerfile` は無いので、ローカルでは uv で直接起動して確認する
（本番の Railway は Railpack でビルドする）:

```bash
cd projects/archive

# 依存をインストール
uv sync

# 起動（認証あり・ポート 8000）
ARCHIVE_USER=admin ARCHIVE_PASSWORD=mysecretpassword \
  PYTHONPATH=src uv run uvicorn xlikes_viewer.server:app --host 0.0.0.0 --port 8000

# ブラウザで http://localhost:8000 にアクセス → Basic 認証ダイアログが表示される
```

> Windows PowerShell では行頭の `VAR=値` 形式が使えない。`$env:ARCHIVE_USER="admin"` のように
> 事前に設定してから `uv run ...` を実行する。

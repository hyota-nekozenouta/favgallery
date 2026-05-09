# Archive — Railway + Cloudflare デプロイガイド

## 概要

Archive（xlikes-viewer）を Railway に WEB アプリとしてデプロイし、Cloudflare でカスタムドメインを設定する手順。

## 前提

- Railway アカウント（railway.app）
- Cloudflare アカウント（カスタムドメインを管理している）
- GitHub リポジトリに HALO/projects/archive/src/ がある状態

---

## 1. Railway デプロイ

### 1-1. プロジェクト作成

1. Railway ダッシュボード（railway.app）→ **New Project** → **Deploy from GitHub repo**
2. リポジトリを選択
3. **Root Directory** を `projects/archive/src` に設定

### 1-2. ビルド設定

Railway は自動的に `nixpacks.toml` を検出して使用する（推奨）。
Docker を使いたい場合は **Settings → Builder** で `Dockerfile` を選択可。

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

> `ARCHIVE_USER` と `ARCHIVE_PASSWORD` のどちらかが空の場合、認証なしで動作する。
> 本番環境では必ず両方を設定すること。

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
- Railway（Linux）: nixpacks または Dockerfile で `ffmpeg` を apt/nixpkgs からインストール済み
- gallery-dl は自動で PATH 上の `ffmpeg` コマンドを使用する

---

## 5. cookies.txt について

gallery-dl による X(Twitter) アクセスには `cookies.txt` が必要。
Railway 上でタイムラインリフレッシュ・同期機能を使う場合は、
`cookies.txt` をボリューム内の適切な場所に配置する必要がある。

現状の gallery-dl 設定パス: `/data/cookies.txt`（ポータブルレイアウト外の場合）

---

## ローカル Docker での動作確認

```bash
cd projects/archive/src

# ビルド
docker build -t archive .

# 起動（認証あり・ポート 8080）
docker run -p 8080:8000 \
  -e ARCHIVE_USER=admin \
  -e ARCHIVE_PASSWORD=mysecretpassword \
  -e PORT=8000 \
  archive

# ブラウザで http://localhost:8080 にアクセス → Basic 認証ダイアログが表示される
```

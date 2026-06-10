# FavGallery (favgallery)

X(Twitter) のいいねした画像・動画を保存・閲覧する個人向けメディアアーカイブアプリ。
漫画（画像シーケンス）のリーダー機能も持つ。
旧名: Archive / xlikes-viewer（2026-06-10 リネーム）。

- **バックエンド**: FastAPI + uvicorn
- **メディア取得**: gallery-dl（+ サイト別 HTML スクレイピング fallback）
- **ストレージ**: ローカル / Cloudflare R2（S3 互換）
- **重複排除**: Pillow + imagehash

## 開発

```bash
cd projects/archive
uv sync
uv run pytest          # テスト
PYTHONPATH=src uv run uvicorn favgallery.server:app --reload  # ローカル起動
```

## デプロイ

Railway + Cloudflare での公開手順は [DEPLOY.md](DEPLOY.md) を参照。
公開 URL: https://archive.hyota.cloud （新ドメイン favgallery.* へ移行予定・インフラ側タスク）

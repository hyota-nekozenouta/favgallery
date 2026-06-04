# Archive (xlikes-viewer)

X(Twitter) のいいねした画像・動画を保存・閲覧する個人向けメディアアーカイブアプリ。
漫画（画像シーケンス）のリーダー機能も持つ。

- **バックエンド**: FastAPI + uvicorn
- **メディア取得**: gallery-dl（+ サイト別 HTML スクレイピング fallback）
- **ストレージ**: ローカル / Cloudflare R2（S3 互換）
- **重複排除**: Pillow + imagehash

## 開発

```bash
cd projects/archive
uv sync
uv run pytest          # テスト
PYTHONPATH=src uv run uvicorn xlikes_viewer.server:app --reload  # ローカル起動
```

## デプロイ

Railway + Cloudflare での公開手順は [DEPLOY.md](DEPLOY.md) を参照。
公開 URL: https://archive.hyota.cloud

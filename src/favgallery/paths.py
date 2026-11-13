"""Resolve runtime paths."""

from __future__ import annotations

from pathlib import Path


def default_library_root() -> Path:
    # 既定のライブラリの置き場。環境変数 FAVGALLERY_LIBRARY_ROOT が
    # 設定されていればそちらが優先される (server.py / cli.py)。ここはゼロ設定時の
    # フォールバックで、クローンしたリポジトリ直下の ./data/library を使う。
    return Path.cwd() / "data" / "library"

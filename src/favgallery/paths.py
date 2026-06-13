"""Resolve runtime paths so the viewer works in both installed and portable layouts."""

from __future__ import annotations

import sys
from pathlib import Path


def portable_root() -> Path | None:
    """The folder containing the viewer .exe, when launched from a portable bundle.

    Detection: PyInstaller-frozen interpreter + a sibling `data/` directory.
    Returns None when running from source or from a non-portable install.
    """
    if not getattr(sys, "frozen", False):
        return None
    exe_dir = Path(sys.executable).resolve().parent
    if (exe_dir / "data").is_dir():
        return exe_dir
    return None


def default_library_root() -> Path:
    p = portable_root()
    if p is not None:
        return p / "data" / "library"
    # 非ポータブル/サーバー実行時の既定。環境変数 FAVGALLERY_LIBRARY_ROOT が
    # 設定されていればそちらが優先される (server.py / cli.py)。ここはゼロ設定時の
    # フォールバックで、クローンしたリポジトリ直下の ./data/library を使う。
    return Path.cwd() / "data" / "library"

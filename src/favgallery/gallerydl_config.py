"""gallery-dl JSON config builders (single source of truth).

Two configs live here so their paths/postprocessor settings can't drift:
- the persistent *sync* config, written at startup with absolute paths
  (:func:`write_gallerydl_config`), and
- the ephemeral *book-import* config used per URL by the import worker
  (:func:`build_book_import_config`).
"""

from __future__ import annotations

import json
from pathlib import Path

from favgallery.paths import portable_root


def _fwd(p: Path) -> str:
    """Forward-slash a path (gallery-dl wants POSIX-style paths on Windows too)."""
    return str(p).replace("\\", "/")


def build_sync_config(library_root: Path, ffmpeg_location: str) -> dict:
    """Build the gallery-dl config dict for the X likes sync (pure)."""
    return {
        "extractor": {
            "base-directory": _fwd(library_root) + "/",
            "archive": _fwd(library_root / "archive.sqlite"),
            "twitter": {
                # cookies.txt lives INSIDE library_root (the Railway volume mount),
                # next to the DB, so it survives redeploys. Must match the path
                # computed in server.create_app (ctx.cookies_file).
                "cookies": _fwd(library_root / "cookies.txt"),
                "videos": True,
                "retweets": False,
                "text-tweets": False,
                "filename": "{tweet_id}_{num}.{extension}",
                "directory": ["{user[name]}"],
                "postprocessors": [{"name": "metadata", "mode": "json"}],
            },
        },
        "downloader": {
            "mtime": True,
            "ffmpeg-location": ffmpeg_location,
        },
        "output": {"progress": True},
    }


def _resolve_ffmpeg_location(library_root: Path) -> str:
    """Portable bundle: prefer the bundled ffmpeg.exe; Railway: rely on PATH."""
    if portable_root() is not None:
        bundle_root = library_root.parent.parent
        ffmpeg_exe = bundle_root / "ffmpeg" / "bin" / "ffmpeg.exe"
        return _fwd(ffmpeg_exe) if ffmpeg_exe.exists() else "ffmpeg"
    return "ffmpeg"


def write_gallerydl_config(config_path: Path, library_root: Path) -> None:
    """Write gallery-dl.json with current absolute paths (idempotent).

    Called at startup so moving the portable folder to another PC automatically
    fixes stale paths (cookies, ffmpeg, base-directory, archive). No-op when the
    file already matches the desired config.
    """
    desired = build_sync_config(library_root, _resolve_ffmpeg_location(library_root))
    config_path.parent.mkdir(parents=True, exist_ok=True)
    if config_path.exists():
        try:
            if json.loads(config_path.read_text(encoding="utf-8")) == desired:
                return
        except (OSError, json.JSONDecodeError):
            pass
    config_path.write_text(json.dumps(desired, indent=2, ensure_ascii=False), encoding="utf-8")


def build_book_import_config(tmp_dir: str | Path) -> dict:
    """Build the ephemeral gallery-dl config for one book import (pure).

    Downloads into ``tmp_dir`` with zero-padded sequential filenames and a JSON
    metadata sidecar per page.
    """
    return {
        "extractor": {
            "base-directory": str(tmp_dir).replace("\\", "/") + "/",
            "directory": [],
            "filename": "{num:>04}.{extension}",
            "postprocessors": [{"name": "metadata", "mode": "json"}],
        }
    }

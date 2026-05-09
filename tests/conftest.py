"""Shared fixtures: synthesize a fake X-Likes library for tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from PIL import Image


def _write_post(
    library: Path,
    *,
    author: str,
    nick: str,
    tweet_id: int,
    num: int,
    extension: str,
    content: str,
    date: str,
    favorite_count: int = 0,
    view_count: int = 0,
    media_type: str = "photo",
    sensitive: bool = False,
    lang: str = "ja",
) -> Path:
    user_dir = library / author
    user_dir.mkdir(parents=True, exist_ok=True)
    media_path = user_dir / f"{tweet_id}_{num}.{extension}"
    if extension in ("jpg", "png"):
        Image.new("RGB", (320, 240), color=(50, 60, 70)).save(media_path)
    else:
        media_path.write_bytes(b"\x00\x00\x00 ftypisom")  # dummy mp4-ish
    sidecar = user_dir / f"{tweet_id}_{num}.{extension}.json"
    payload: dict[str, Any] = {
        "filename": f"file_{tweet_id}",
        "extension": extension,
        "type": media_type,
        "width": 320,
        "height": 240,
        "tweet_id": tweet_id,
        "num": num,
        "date": date,
        "author": {"id": 1, "name": author, "nick": nick},
        "user": {"id": 999, "name": "viewer"},
        "lang": lang,
        "sensitive": sensitive,
        "favorite_count": favorite_count,
        "view_count": view_count,
        "content": content,
    }
    sidecar.write_text(json.dumps(payload), encoding="utf-8")
    return media_path


@pytest.fixture
def fake_library(tmp_path: Path) -> Path:
    lib = tmp_path / "library"
    lib.mkdir()
    _write_post(
        lib,
        author="alice",
        nick="アリス",
        tweet_id=1001,
        num=1,
        extension="jpg",
        content="hello world #cat #pixiv",
        date="2025-12-31 10:00:00",
        favorite_count=42,
    )
    _write_post(
        lib,
        author="alice",
        nick="アリス",
        tweet_id=1002,
        num=1,
        extension="jpg",
        content="another one #cat",
        date="2025-12-30 09:00:00",
        favorite_count=99,
    )
    _write_post(
        lib,
        author="bob",
        nick="ボブ",
        tweet_id=2001,
        num=1,
        extension="png",
        content="just a photo",
        date="2025-12-29 08:00:00",
        favorite_count=5,
    )
    _write_post(
        lib,
        author="carol",
        nick="キャロル",
        tweet_id=3001,
        num=1,
        extension="mp4",
        content="video time #vlog",
        date="2025-12-28 07:00:00",
        media_type="video",
    )
    return lib

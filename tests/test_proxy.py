"""Tests for xlikes_viewer.proxy."""

from __future__ import annotations

from pathlib import Path

import pytest

from xlikes_viewer.proxy import _load_cookies, is_allowed


@pytest.mark.unit
def test_is_allowed_only_twimg() -> None:
    assert is_allowed("https://pbs.twimg.com/media/abc.jpg") is True
    assert is_allowed("https://video.twimg.com/ext_tw_video/abc.mp4") is True
    assert is_allowed("https://example.com/abc.jpg") is False
    assert is_allowed("https://malicious.pbs.twimg.com.evil.com/x") is False
    assert is_allowed("not a url") is False


@pytest.mark.unit
def test_load_cookies_handles_missing_file(tmp_path: Path) -> None:
    cookies = _load_cookies(tmp_path / "missing.txt")
    assert len(list(cookies.jar)) == 0


@pytest.mark.unit
def test_load_cookies_parses_netscape(tmp_path: Path) -> None:
    p = tmp_path / "cookies.txt"
    p.write_text(
        "# Netscape HTTP Cookie File\n.x.com\tTRUE\t/\tTRUE\t1812605621\tauth_token\tabc123\n",
        encoding="utf-8",
    )
    cookies = _load_cookies(p)
    names = {c.name for c in cookies.jar}
    assert "auth_token" in names

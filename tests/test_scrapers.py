"""Tests for xlikes_viewer.scrapers (book-import HTML fallback)."""

from __future__ import annotations

from pathlib import Path

import pytest

from xlikes_viewer.scrapers import scrape_images_from_html


class _FakeResp:
    def __init__(self, *, text: str = "", content: bytes = b"", status_code: int = 200) -> None:
        self.text = text
        self.content = content
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


@pytest.mark.unit
def test_routes_doujin_freee_to_dedicated_scraper(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = {}

    def _fake_doujin(url: str, tmp_dir: Path) -> list[Path]:
        seen["url"] = url
        return []

    monkeypatch.setattr("xlikes_viewer.scrapers.scrape_doujin_freee", _fake_doujin)
    scrape_images_from_html("https://doujin-freee.cc/works/123", tmp_path)
    assert seen["url"] == "https://doujin-freee.cc/works/123"


@pytest.mark.unit
def test_generic_scrape_downloads_article_images(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    html = (
        '<article><img src="https://img.test/a.jpg">'
        '<img src="/rel/b.png"><img src="https://img.test/c.gif"></article>'
    )

    def _fake_get(url: str, **_kw: object) -> _FakeResp:
        if url.endswith((".jpg", ".png")):
            return _FakeResp(content=b"\xff\xd8\xff\xe0", status_code=200)
        return _FakeResp(text=html, status_code=200)

    monkeypatch.setattr("requests.get", _fake_get)
    files = scrape_images_from_html("https://blog.test/post", tmp_path)

    # .gif is filtered out; the relative URL is resolved + downloaded.
    names = sorted(f.name for f in files)
    assert names == ["0001.jpg", "0002.png"]
    assert all(f.read_bytes() == b"\xff\xd8\xff\xe0" for f in files)

"""HTML image scrapers used as the book-import fallback when gallery-dl can't
handle a site. Site-specific routing lives in ``scrape_images_from_html``.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urljoin, urlparse

_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


def scrape_doujin_freee(url: str, tmp_dir: Path) -> list[Path]:
    """doujin-freee.cc 専用: img_gage スライダーから全画像URLを生成して取得。"""
    import requests as _requests
    from bs4 import BeautifulSoup

    resp = _requests.get(url, timeout=30, headers=_HEADERS)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    # img_gage から画像生成パラメータを抽出
    gage = soup.select_one(".img_gage, [class*=img_gage]")
    if not gage:
        return []

    max_el = gage.select_one("input[type='range']")
    img_b_el = gage.select_one("#img_b")
    img_s_el = gage.select_one("#img_s")
    year_el = gage.select_one("#post_year")
    month_el = gage.select_one("#post_month")

    if not all([max_el, img_b_el, img_s_el, year_el, month_el]):
        return []

    max_pages = int(max_el.get("max", "1"))
    img_b = img_b_el.get("value", "")
    img_s = img_s_el.get("value", "")
    post_year = year_el.get("value", "")
    post_month = month_el.get("value", "")

    # URL: https://img.doujin-freee.cc/thumb640/{year}{month}/{img_b}{img_s}/{img_s}-{page:03d}-640.jpg
    img_urls = [
        f"https://img.doujin-freee.cc/thumb640/{post_year}{post_month}/"
        f"{img_b}{img_s}/{img_s}-{i:03d}-640.jpg"
        for i in range(1, max_pages + 1)
    ]

    files: list[Path] = []
    for i, img_url in enumerate(img_urls, 1):
        try:
            r = _requests.get(img_url, timeout=30, headers={**_HEADERS, "Referer": url})
            if r.status_code != 200:
                continue
            dest = tmp_dir / f"{i:04d}.jpg"
            dest.write_bytes(r.content)
            files.append(dest)
        except Exception:
            continue
    return files


def scrape_images_from_html(url: str, tmp_dir: Path) -> list[Path]:
    """サイトに応じたスクレイパーを選択して実行。"""
    host = urlparse(url).netloc.lower()

    # サイト別ルーティング
    if "doujin-freee" in host:
        return scrape_doujin_freee(url, tmp_dir)

    # 未対応サイト: 汎用フォールバック
    import requests as _requests
    from bs4 import BeautifulSoup

    resp = _requests.get(url, timeout=30, headers=_HEADERS)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    imgs = soup.select("article img, .entry-content img, .post-content img")
    img_urls = []
    for img in imgs:
        src = img.get("data-src") or img.get("data-lazy-src") or img.get("src")
        if src and not src.split("?")[0].endswith((".svg", ".gif", ".ico")):
            if not src.startswith("http"):
                src = urljoin(url, src)
            img_urls.append(src)

    seen: set[str] = set()
    files: list[Path] = []
    for i, img_url in enumerate(img_urls, 1):
        if img_url in seen:
            continue
        seen.add(img_url)
        try:
            r = _requests.get(img_url, timeout=30, headers={**_HEADERS, "Referer": url})
            if r.status_code != 200:
                continue
            ext = Path(img_url.split("?")[0]).suffix.lower() or ".jpg"
            if ext not in {".jpg", ".jpeg", ".png", ".webp", ".avif", ".bmp"}:
                ext = ".jpg"
            dest = tmp_dir / f"{i:04d}{ext}"
            dest.write_bytes(r.content)
            files.append(dest)
        except Exception:
            continue
    return files

"""Shared helpers across the viewer: hashtags, media types, X URLs, cookies."""

from __future__ import annotations

import contextlib
import http.cookiejar
import re
from pathlib import Path

HASHTAG_RE = re.compile(r"#([\w_぀-ヿ一-鿿＀-￯]+)")

MEDIA_TYPE_PHOTO = "photo"
MEDIA_TYPE_VIDEO = "video"
MEDIA_TYPE_GIF = "animated_gif"
DISPLAYABLE_MEDIA_TYPES = (MEDIA_TYPE_PHOTO, MEDIA_TYPE_GIF, MEDIA_TYPE_VIDEO)

X_HOST = "https://x.com"


def extract_hashtags(content: str) -> tuple[str, ...]:
    if not content:
        return ()
    return tuple(sorted({m.group(1) for m in HASHTAG_RE.finditer(content)}))


def tweet_url(author_name: str, tweet_id: str) -> str:
    return f"{X_HOST}/{author_name}/status/{tweet_id}"


def load_cookie_jar(cookies_file: Path) -> http.cookiejar.MozillaCookieJar:
    """Open a Netscape cookies.txt as a MozillaCookieJar. Tolerates missing/garbage files."""
    jar = http.cookiejar.MozillaCookieJar(str(cookies_file))
    if not cookies_file.exists():
        return jar
    with contextlib.suppress(OSError):
        jar.load(ignore_discard=True, ignore_expires=True)
    return jar

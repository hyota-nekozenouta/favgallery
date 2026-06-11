"""Proxy X CDN media through our local server with auth cookies attached."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from urllib.parse import urlparse

import httpx

from favgallery.x_helpers import load_cookie_jar

ALLOWED_HOSTS = {
    "pbs.twimg.com",
    "video.twimg.com",
    "abs.twimg.com",
    "ton.twimg.com",
}

PASSED_HEADERS = (
    "content-type",
    "content-length",
    "content-range",
    "accept-ranges",
    "cache-control",
)


def _load_cookies(cookies_file: Path) -> httpx.Cookies:
    """Load Netscape cookies.txt into an httpx.Cookies jar."""
    cookies = httpx.Cookies()
    for c in load_cookie_jar(cookies_file):
        cookies.set(c.name, c.value or "", domain=c.domain or "", path=c.path or "/")
    return cookies


def is_allowed(url: str) -> bool:
    try:
        host = urlparse(url).hostname or ""
    except ValueError:
        return False
    return host.lower() in ALLOWED_HOSTS


class CdnProxy:
    """Holds the cookie jar and a long-lived AsyncClient for streaming proxy reads."""

    def __init__(self, cookies_file: Path) -> None:
        self.cookies_file = cookies_file
        self._cookies = _load_cookies(cookies_file)
        self._client: httpx.AsyncClient | None = None
        # Generation counter: bumped by reload_cookies() (sync, from the cookie
        # paste endpoint), consumed by _ensure_client() (async, in the event
        # loop). The stale client was the "re-pasted cookies but proxied media
        # still 403s until restart" bug — the jar was baked into the client at
        # startup and never refreshed.
        self._generation = 0
        self._client_generation = 0

    def reload_cookies(self) -> None:
        """Re-read cookies.txt; the next request rebuilds the client with it."""
        self._cookies = _load_cookies(self.cookies_file)
        self._generation += 1

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is not None and self._client_generation != self._generation:
            stale, self._client = self._client, None
            # May briefly cut a stream that is mid-flight at paste time; that
            # request was using the dead cookie anyway.
            await stale.aclose()
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0, connect=10.0),
                follow_redirects=True,
                cookies=self._cookies,
                headers={"User-Agent": "Mozilla/5.0 (favgallery)"},
            )
            self._client_generation = self._generation
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def stream(
        self, url: str, *, range_header: str | None = None
    ) -> tuple[int, dict[str, str], AsyncIterator[bytes]]:
        """Open an upstream connection and return (status, headers, byte iterator).

        Caller is responsible for finishing iteration so the upstream connection closes.
        """
        client = await self._ensure_client()
        headers: dict[str, str] = {}
        if range_header:
            headers["Range"] = range_header
        request = client.build_request("GET", url, headers=headers)
        response = await client.send(request, stream=True)

        out_headers = {k: v for k, v in response.headers.items() if k.lower() in PASSED_HEADERS}

        async def body() -> AsyncIterator[bytes]:
            try:
                async for chunk in response.aiter_bytes():
                    yield chunk
            finally:
                await response.aclose()

        return response.status_code, out_headers, body()

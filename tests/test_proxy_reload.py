"""CdnProxy must pick up cookies pasted via the UI without a container restart.

Regression guard for the stale-jar bug: CdnProxy loaded cookies.txt once in
__init__ and baked them into a long-lived AsyncClient, so a cookie re-paste via
POST /api/cookies never reached proxied media requests until redeploy — exactly
the flow the in-app cookie UI exists to avoid.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from favgallery.proxy import CdnProxy
from favgallery.server import create_app


def _write_cookies(path: Path, token: str) -> None:
    path.write_text(
        "# Netscape HTTP Cookie File\n"
        + "\t".join([".x.com", "TRUE", "/", "TRUE", "9999999999", "auth_token", token])
        + "\n",
        encoding="utf-8",
    )


def _jar_token(proxy: CdnProxy) -> str | None:
    for c in proxy._cookies.jar:
        if c.name == "auth_token":
            return c.value
    return None


@pytest.mark.unit
def test_reload_cookies_swaps_jar(tmp_path: Path) -> None:
    cookies_file = tmp_path / "cookies.txt"
    _write_cookies(cookies_file, "old-token")
    proxy = CdnProxy(cookies_file)
    assert _jar_token(proxy) == "old-token"

    _write_cookies(cookies_file, "new-token")
    proxy.reload_cookies()
    assert _jar_token(proxy) == "new-token"


@pytest.mark.unit
def test_reload_rebuilds_client_with_fresh_cookies(tmp_path: Path) -> None:
    cookies_file = tmp_path / "cookies.txt"
    _write_cookies(cookies_file, "old-token")
    proxy = CdnProxy(cookies_file)

    async def scenario() -> tuple[object, object]:
        first = await proxy._ensure_client()
        _write_cookies(cookies_file, "new-token")
        proxy.reload_cookies()
        second = await proxy._ensure_client()
        await proxy.aclose()
        return first, second

    first, second = asyncio.run(scenario())
    assert first is not second  # stale client was replaced
    assert second.cookies.get("auth_token") == "new-token"


@pytest.mark.unit
def test_ensure_client_reuses_without_reload(tmp_path: Path) -> None:
    cookies_file = tmp_path / "cookies.txt"
    _write_cookies(cookies_file, "tok")
    proxy = CdnProxy(cookies_file)

    async def scenario() -> bool:
        first = await proxy._ensure_client()
        second = await proxy._ensure_client()
        await proxy.aclose()
        return first is second

    assert asyncio.run(scenario()) is True


@pytest.mark.integration
def test_post_cookies_refreshes_cdn_proxy(fake_library: Path) -> None:
    client = TestClient(create_app(library_root=fake_library, scan_in_background=False))
    proxy = client.app.state.context.cdn_proxy
    assert _jar_token(proxy) is None  # no cookies.txt yet at startup

    body = (
        "# Netscape HTTP Cookie File\n"
        + "\t".join([".x.com", "TRUE", "/", "TRUE", "9999999999", "auth_token", "pasted"])
        + "\n"
    )
    r = client.post("/api/cookies", json={"content": body})
    assert r.status_code == 200
    assert _jar_token(proxy) == "pasted"  # paste reached the live proxy jar

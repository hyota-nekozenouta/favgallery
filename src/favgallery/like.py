"""POST a 'like' to X via the user's session cookies.

This calls X's GraphQL ``FavoriteTweet`` endpoint with the same Bearer token
the X web client uses, plus the user's auth_token + ct0 from cookies.txt.
The action is initiated by the user from the viewer UI.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import httpx

from xlikes_viewer.x_helpers import load_cookie_jar

log = logging.getLogger("xlikes_viewer.like")

# X web client public Bearer token (visible in X's web app source). Required by
# X's GraphQL gateway alongside the per-user auth_token + ct0 cookies.
WEB_BEARER = (
    "Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs"
    "%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
)
FAVORITE_QUERY_ID = "lI07N6Otwv1PhnEgXILM7A"
FAVORITE_URL = f"https://x.com/i/api/graphql/{FAVORITE_QUERY_ID}/FavoriteTweet"


@dataclass(frozen=True)
class LikeResult:
    ok: bool
    status_code: int
    message: str = ""


def _read_cookies(cookies_file: Path) -> dict[str, str]:
    """Pull auth_token and ct0 out of the Netscape cookies.txt file."""
    out: dict[str, str] = {}
    for c in load_cookie_jar(cookies_file):
        domain_ok = c.domain.endswith("x.com") or c.domain.endswith("twitter.com")
        if domain_ok and c.name in ("auth_token", "ct0"):
            out[c.name] = c.value or ""
    return out


def like_tweet(cookies_file: Path, tweet_id: str) -> LikeResult:
    """Send a FavoriteTweet GraphQL POST. Returns LikeResult."""
    if not tweet_id:
        return LikeResult(ok=False, status_code=0, message="tweet_id required")
    creds = _read_cookies(cookies_file)
    if "auth_token" not in creds or "ct0" not in creds:
        return LikeResult(
            ok=False, status_code=0, message="auth_token / ct0 missing from cookies.txt"
        )
    headers = {
        "authorization": WEB_BEARER,
        "x-csrf-token": creds["ct0"],
        "x-twitter-auth-type": "OAuth2Session",
        "x-twitter-active-user": "yes",
        "content-type": "application/json",
        "user-agent": "Mozilla/5.0 (xlikes-viewer)",
        "origin": "https://x.com",
        "referer": "https://x.com/",
    }
    body = {
        "variables": {"tweet_id": str(tweet_id)},
        "queryId": FAVORITE_QUERY_ID,
    }
    cookies = {
        "auth_token": creds["auth_token"],
        "ct0": creds["ct0"],
    }
    try:
        with httpx.Client(timeout=httpx.Timeout(15.0)) as client:
            r = client.post(FAVORITE_URL, headers=headers, json=body, cookies=cookies)
    except Exception as exc:
        log.exception("like_tweet failed")
        return LikeResult(ok=False, status_code=0, message=f"{type(exc).__name__}: {exc}")

    if r.status_code == 200:
        # X returns 200 with a "data.favorite_tweet": "Done" payload on success,
        # and a 200 with errors[] on already-liked. Treat both as success.
        return LikeResult(ok=True, status_code=200)
    if r.status_code == 403:
        return LikeResult(ok=False, status_code=403, message="forbidden — cookies expired?")
    return LikeResult(ok=False, status_code=r.status_code, message=r.text[:200])

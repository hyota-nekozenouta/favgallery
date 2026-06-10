"""Download a single X tweet's media into the local archive via gallery-dl."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from favgallery.gallerydl import prepare_config
from favgallery.x_helpers import tweet_url

log = logging.getLogger("favgallery.save_one")


@dataclass(frozen=True)
class SaveResult:
    ok: bool
    return_code: int
    message: str = ""


def save_tweet(
    gallerydl_config_path: Path,
    *,
    author_name: str,
    tweet_id: str,
) -> SaveResult:
    """Download all media for the given tweet via gallery-dl as a library call.

    The shared archive DB ensures already-saved media is skipped. ``retweets``
    is forced on so a save still works when the visible tweet IS a retweet.
    """
    if not author_name or not tweet_id:
        return SaveResult(ok=False, return_code=-1, message="author and tweet_id required")

    from gallery_dl import job  # type: ignore[import-untyped]

    prepare_config(gallerydl_config_path, twitter_retweets=True)

    try:
        rc = job.DownloadJob(tweet_url(author_name, tweet_id)).run()
    except Exception as exc:
        log.exception("save_tweet failed")
        return SaveResult(ok=False, return_code=-1, message=f"{type(exc).__name__}: {exc}")
    return SaveResult(ok=(int(rc) == 0), return_code=int(rc))

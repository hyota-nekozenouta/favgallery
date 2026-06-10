"""Lazy gallery-dl config initialiser shared by timeline + per-tweet save."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def prepare_config(
    config_path: Path,
    *,
    file_range: str | None = None,
    post_range: str | None = None,
    archive: str | None | object = ...,
    twitter_retweets: bool | None = None,
    fast_fail: bool = False,
) -> Any:
    """Load gallery-dl's JSON config and apply common runtime overrides.

    Parameters use ``...`` (Ellipsis) as "leave alone" sentinel for `archive`
    so callers can explicitly pass `None` to disable the archive DB.
    Returns the gallery-dl ``config`` module so callers can apply further tweaks.

    ``fast_fail=True`` is for interactive probes (cookie 接続テスト): gallery-dl
    waits silently until X's rate-limit window resets by default — minutes of
    no response, which reads as "ボタンが効かない" in the UI. Abort instead and
    keep network retries/timeouts tight so the caller can answer within seconds
    (2026-06-10 接続テスト無反応 bug).
    """
    from gallery_dl import config as gdl_config  # type: ignore[import-untyped]

    # clear() necessary: gallery-dl's global config MERGES on load(), so a prior
    # caller's runtime override (e.g. verify の file-range "1-1" / fast_fail)
    # would silently persist into the next sync and cap it at 1 file
    # (2026-06-10 実測確認・潜在 cross-contamination bug の根治).
    gdl_config.clear()
    gdl_config.load([str(config_path)])
    if file_range is not None:
        gdl_config.set(("extractor",), "file-range", file_range)
    if post_range is not None:
        gdl_config.set(("extractor",), "post-range", post_range)
    if archive is not ...:
        gdl_config.set(("extractor",), "archive", archive)
    if twitter_retweets is not None:
        gdl_config.set(("extractor", "twitter"), "retweets", twitter_retweets)
    if fast_fail:
        gdl_config.set(("extractor", "twitter"), "ratelimit", "abort")
        gdl_config.set(("extractor",), "retries", 1)
        gdl_config.set(("extractor",), "timeout", 10.0)
    return gdl_config

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
) -> Any:
    """Load gallery-dl's JSON config and apply common runtime overrides.

    Parameters use ``...`` (Ellipsis) as "leave alone" sentinel for `archive`
    so callers can explicitly pass `None` to disable the archive DB.
    Returns the gallery-dl ``config`` module so callers can apply further tweaks.
    """
    from gallery_dl import config as gdl_config  # type: ignore[import-untyped]

    gdl_config.load([str(config_path)])
    if file_range is not None:
        gdl_config.set(("extractor",), "file-range", file_range)
    if post_range is not None:
        gdl_config.set(("extractor",), "post-range", post_range)
    if archive is not ...:
        gdl_config.set(("extractor",), "archive", archive)
    if twitter_retweets is not None:
        gdl_config.set(("extractor", "twitter"), "retweets", twitter_retweets)
    return gdl_config

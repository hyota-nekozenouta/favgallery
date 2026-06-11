"""Tests for the gallery-dl config builders (favgallery.gallerydl_config).

Regression guard for the recurring cookie-corruption bug: gallery-dl's
``cookies-update`` option defaults to True, which rewrites the live session
cookie jar back to cookies.txt after every extraction. On a datacenter IP
(Railway) X hands back a degraded/short-lived session, so that write-back
silently clobbered the pristine cookie the user pasted via the UI — making
auth "break again" minutes after it was fixed. The sync config must disable
the write-back so the UI stays the single source of truth for X auth.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from favgallery.gallerydl_config import build_sync_config


@pytest.mark.unit
def test_sync_config_disables_gallery_dl_cookie_writeback() -> None:
    config = build_sync_config(Path("/data/library"), "ffmpeg")
    twitter = config["extractor"]["twitter"]
    # gallery-dl must NOT rewrite cookies.txt with the live (degraded) session jar.
    assert twitter["cookies-update"] is False

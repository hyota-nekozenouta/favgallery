"""Library-relative path <-> R2 object key derivation + media-file predicates.

Single source of truth for how a local media file maps to its R2 object key:
the key is the file's path relative to the library root, forward-slashed
(POSIX). This is shared by the sync upload sweeps (``sync.py``) and the
per-post delete purge (``server.py``) so the two never drift.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

# Media extensions uploaded to / served from R2. JSON sidecars and thumbnails
# are intentionally excluded (sidecars stay local for re-ingest; thumbs are
# regenerated on demand).
MEDIA_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".mp4", ".mov", ".webm", ".webp"}


def r2_key_for_path(path: Path, library_root: Path) -> str:
    """Return the R2 object key for *path*: its library-relative POSIX path."""
    return path.relative_to(library_root).as_posix()


def is_media_file(path: Path) -> bool:
    """True if *path* has a media extension we store in R2."""
    return path.suffix.lower() in MEDIA_EXTENSIONS


def iter_media_keys(library_root: Path) -> Iterator[tuple[Path, str]]:
    """Yield ``(media_path, r2_key)`` for every uploadable media file under root.

    Skips non-files, non-media extensions, anything under a ``thumbs/``
    subdirectory, and anything under a dot-prefixed directory. The key is the
    library-relative POSIX path (see :func:`r2_key_for_path`). Centralises the
    filter that the three sync sweeps (full upload, on-demand cleanup,
    streaming upload) previously duplicated.

    The dot-directory rule is deny-by-default on purpose: these sweeps **upload
    and then delete** what they yield, so any internal working directory that
    happens to contain ``.jpg`` files (e.g. a thumbnail cache) would be shipped
    to R2 and wiped. ``dedup.py`` already carried its own ``.thumb-cache``
    guard; the rule belongs here, where the sweeps actually read it.
    """
    for media_path in library_root.rglob("*"):
        if not media_path.is_file():
            continue
        if not is_media_file(media_path):
            continue
        rel = media_path.relative_to(library_root)
        dirs = rel.parts[:-1]
        if "thumbs" in dirs:
            continue
        if any(part.startswith(".") for part in dirs):
            continue
        yield media_path, rel.as_posix()

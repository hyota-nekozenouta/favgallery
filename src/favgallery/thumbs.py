"""On-the-fly thumbnail generator with disk cache."""

from __future__ import annotations

import contextlib
import hashlib
import os
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps

from favgallery.paths import default_library_root


def _thumb_cache_root() -> Path:
    """Where generated thumbnails are cached: NEXT TO the library, never inside it.

    Two constraints pull in opposite directions:

    * It must live on the Railway volume, so the cache survives a redeploy.
      ``default_library_root()`` ignores FAVGALLERY_LIBRARY_ROOT and resolves to
      ``cwd/data/library`` (= ``/app/...``), which is container-local scratch.
    * It must NOT live under ``library_root``: the R2 sync sweeps walk that tree
      with ``keys.iter_media_keys`` and **upload then delete** every ``.jpg``
      they find, so a cache inside it would be shipped to R2 and wiped on every
      sync (and the objects would never be purged).

    The volume is mounted one level above the library (``/data`` for
    ``/data/library``), so the sibling directory satisfies both.
    """
    root = (
        os.environ.get("FAVGALLERY_LIBRARY_ROOT")
        or os.environ.get("ARCHIVE_LIBRARY_ROOT")  # legacy name, still live in prod
    )
    base = Path(root).expanduser() if root else default_library_root()
    return base.parent / ".thumb-cache"


THUMB_CACHE_ROOT = _thumb_cache_root()


def _cache_key(media: Path, size: int) -> Path:
    digest = hashlib.sha1(
        f"{media.resolve()}|{media.stat().st_mtime_ns}|{size}".encode()
    ).hexdigest()
    return THUMB_CACHE_ROOT / digest[:2] / f"{digest}.jpg"


def _generate_image_thumb(media: Path, size: int) -> bytes:
    with Image.open(media) as im:
        im = ImageOps.exif_transpose(im)
        im.thumbnail((size, size), Image.Resampling.LANCZOS)
        if im.mode in ("RGBA", "LA", "P"):
            bg = Image.new("RGB", im.size, (24, 24, 28))
            bg.paste(im, mask=im.split()[-1] if "A" in im.mode else None)
            im = bg
        elif im.mode != "RGB":
            im = im.convert("RGB")
        buf = BytesIO()
        im.save(buf, "JPEG", quality=82, optimize=True)
        return buf.getvalue()


def thumbnail_bytes(media: Path, size: int = 400) -> bytes | None:
    """Return JPEG bytes for the thumbnail, or None if media isn't an image we can read."""
    if not media.exists():
        return None

    cache = _cache_key(media, size)
    try:
        if cache.exists():
            cached = cache.read_bytes()
            # A zero-length entry means a previous write was cut short (OOM kill,
            # restart, full disk). Treat it as a miss instead of serving an empty
            # image forever — the key is stable, so it would never self-heal.
            if cached:
                return cached
    except OSError:
        pass  # unreadable cache entry — regenerate below

    suffix = media.suffix.lower().lstrip(".")
    if suffix not in {"jpg", "jpeg", "png", "gif", "webp", "bmp"}:
        return None

    try:
        data = _generate_image_thumb(media, size)
    except Exception:
        return None

    # Caching is best effort: a full/read-only disk must not turn a working
    # thumbnail into a 500. Serve the bytes we already generated either way.
    # Write via a temp file + atomic replace so a crash mid-write can never
    # leave a truncated JPEG behind on the volume.
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_name(f"{cache.name}.{os.getpid()}.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, cache)
    except OSError as exc:
        print(f"[thumbs] cache write failed for {cache}: {exc}")
        with contextlib.suppress(OSError):
            tmp.unlink()
    return data


def thumbnail_bytes_from_raw(raw: bytes, size: int = 400) -> bytes | None:
    """Return JPEG thumbnail bytes generated from raw image bytes (e.g. streamed from R2).

    Returns None if the data is not a supported image format or generation fails.
    """
    try:
        with Image.open(BytesIO(raw)) as im:
            im = ImageOps.exif_transpose(im)
            im.thumbnail((size, size), Image.Resampling.LANCZOS)
            if im.mode in ("RGBA", "LA", "P"):
                bg = Image.new("RGB", im.size, (24, 24, 28))
                bg.paste(im, mask=im.split()[-1] if "A" in im.mode else None)
                im = bg
            elif im.mode != "RGB":
                im = im.convert("RGB")
            buf = BytesIO()
            im.save(buf, "JPEG", quality=82, optimize=True)
            return buf.getvalue()
    except Exception:
        return None

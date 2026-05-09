"""On-the-fly thumbnail generator with disk cache."""

from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps

from xlikes_viewer.paths import default_library_root

THUMB_CACHE_ROOT = default_library_root() / ".thumb-cache"


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
    if cache.exists():
        return cache.read_bytes()

    suffix = media.suffix.lower().lstrip(".")
    if suffix not in {"jpg", "jpeg", "png", "gif", "webp", "bmp"}:
        return None

    try:
        data = _generate_image_thumb(media, size)
    except Exception:
        return None

    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(data)
    return data

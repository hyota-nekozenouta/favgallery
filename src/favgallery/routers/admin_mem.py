"""Memory measurement endpoint — Before/After bookkeeping for memory-reduction
work.

Read-only diagnostic surface: process RSS, DB row counts, which heavy
libraries (gallery_dl / numpy / imagehash / PIL / scipy / pywt) are currently
resident in this process, and GC stats. Designed to never raise — every
platform-dependent probe (``/proc``, ``resource``, ``malloc_trim``,
``tracemalloc``) degrades to ``None`` / a skip flag instead of a 500, since
this endpoint's whole point is to be safe to poll on any environment
(Railway/Linux prod, Windows dev box).
"""

from __future__ import annotations

import ctypes
import gc
import os
import sys
import tracemalloc

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from favgallery.context import AppContext, get_context

router = APIRouter()

_HEAVY_MODULES = ("gallery_dl", "numpy", "imagehash", "PIL", "scipy", "pywt")


def _rss_mb() -> float | None:
    """Current resident set size in MB — Linux ``/proc`` first, else best effort."""
    try:
        with open("/proc/self/status", encoding="utf-8") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    kb = int(line.split()[1])
                    return kb / 1024
    except OSError:
        pass
    try:
        import resource

        ru_maxrss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # Linux reports ru_maxrss in KB, macOS in bytes. We only reach here
        # when /proc is unavailable (i.e. not Linux), so treat as bytes.
        return ru_maxrss / (1024 * 1024)
    except Exception:
        return None


def _malloc_trim(before_mb: float | None) -> dict[str, object]:
    """Best-effort glibc malloc_trim(0) — releases free arenas back to the OS."""
    try:
        libc = ctypes.CDLL("libc.so.6")
        libc.malloc_trim(0)
    except (OSError, AttributeError):
        return {"supported": False}
    return {"rss_before_mb": before_mb, "rss_after_mb": _rss_mb()}


@router.get("/api/admin/meminfo")
def admin_meminfo(
    trim: int = 0, ctx: AppContext = Depends(get_context)
) -> JSONResponse:
    body: dict[str, object] = {}

    body["rss_mb"] = _rss_mb()

    try:
        body["posts_count"] = ctx.db.posts_count()
    except Exception:
        body["posts_count"] = None

    body["loaded_heavy"] = {name: (name in sys.modules) for name in _HEAVY_MODULES}

    try:
        body["gc"] = {"count": gc.get_count(), "objects": len(gc.get_objects())}
    except Exception:
        body["gc"] = None

    if os.environ.get("FAVGALLERY_TRACEMALLOC") == "1" and tracemalloc.is_tracing():
        try:
            stats = tracemalloc.take_snapshot().statistics("lineno")[:20]
            body["tracemalloc_top"] = [
                {
                    "file": s.traceback[0].filename,
                    "line": s.traceback[0].lineno,
                    "size_kb": round(s.size / 1024, 1),
                }
                for s in stats
            ]
        except Exception:
            body["tracemalloc_top"] = None
    else:
        body["tracemalloc_top"] = None

    if trim:
        try:
            body["trim"] = _malloc_trim(body["rss_mb"])
        except Exception:
            body["trim"] = {"supported": False}

    return JSONResponse(body)

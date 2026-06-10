"""Media + thumbnail serving (R2-backed, with local fallback + immutable cache)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, StreamingResponse

from favgallery.context import AppContext, get_context
from favgallery.thumbs import thumbnail_bytes, thumbnail_bytes_from_raw

router = APIRouter()


@router.get("/api/media/{rel_path:path}")
async def api_media(
    rel_path: str, request: Request, ctx: AppContext = Depends(get_context)
) -> Response:
    """Serve media from R2 when configured, otherwise from the local library."""
    ctx.validate_rel_path(rel_path)
    etag = ctx.strong_etag("media", rel_path)
    # rel_path uniquely identifies immutable content, so the If-None-Match
    # short-circuit needs no R2/disk read at all.
    if request.headers.get("if-none-match") == etag:
        return Response(
            status_code=304,
            headers={"ETag": etag, "Cache-Control": ctx.immutable_cache},
        )
    if ctx.r2_client is not None:
        try:
            content_length, content_type, body_iter = ctx.r2_client.stream_object(rel_path)
            headers = {"Cache-Control": ctx.immutable_cache, "ETag": etag}
            if content_length:
                headers["content-length"] = str(content_length)
            return StreamingResponse(body_iter, media_type=content_type, headers=headers)
        except Exception:
            # Fall through to local filesystem if key is absent in R2.
            pass
    target = (ctx.library_root / rel_path).resolve()
    if not target.is_file():
        raise HTTPException(status_code=404, detail="not found")
    # FileResponse.set_stat_headers uses setdefault, so our ETag is preserved.
    return FileResponse(target, headers={"Cache-Control": ctx.immutable_cache, "ETag": etag})


@router.get("/media/{rel_path:path}")
def media(rel_path: str, ctx: AppContext = Depends(get_context)) -> FileResponse:
    return FileResponse(
        ctx.resolve_under_library(rel_path),
        headers={"Cache-Control": ctx.immutable_cache},
    )


@router.get("/thumb/{rel_path:path}")
def thumb(
    rel_path: str,
    request: Request,
    size: int = Query(default=400, ge=64, le=1600),
    ctx: AppContext = Depends(get_context),
) -> Response:
    ctx.validate_rel_path(rel_path)
    # Thumbnail bytes are deterministic for (rel_path, size) since the source
    # page is immutable; include size so different ?size= values don't collide.
    etag = ctx.strong_etag("thumb", rel_path, size)
    cache_headers = {"Cache-Control": ctx.immutable_cache, "ETag": etag}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=cache_headers)
    target = ctx.library_root / rel_path
    # Try local file first (fast path, works during sync before R2 upload).
    if target.is_file():
        data = thumbnail_bytes(target.resolve(), size=size)
        if data is not None:
            return Response(content=data, media_type="image/jpeg", headers=cache_headers)
        return FileResponse(target, headers=cache_headers)
    # Local file is gone (uploaded to R2 and deleted) — generate from R2 stream.
    if ctx.r2_client is not None:
        try:
            _, _, body_iter = ctx.r2_client.stream_object(rel_path)
            raw = b"".join(body_iter)
            data = thumbnail_bytes_from_raw(raw, size=size)
            if data is not None:
                return Response(content=data, media_type="image/jpeg", headers=cache_headers)
            # Not an image (e.g. video) — serve the raw bytes directly.
            return Response(
                content=raw, media_type="application/octet-stream", headers=cache_headers
            )
        except Exception:
            pass
    raise HTTPException(status_code=404, detail="not found")

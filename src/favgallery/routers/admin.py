"""Admin maintenance endpoints (local cleanup, storage status, archive reset).

All protected by the Basic-auth middleware registered in server.create_app.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from favgallery.context import AppContext, get_context

router = APIRouter()


@router.post("/api/admin/cleanup-local")
def admin_cleanup_local(ctx: AppContext = Depends(get_context)) -> JSONResponse:
    """Delete local media files already present in R2. Returns delete counts."""
    if ctx.r2_client is None:
        raise HTTPException(status_code=503, detail="R2 not configured")
    return JSONResponse(ctx.sync_runner.cleanup_local())


@router.get("/api/admin/storage-status")
def admin_storage_status(ctx: AppContext = Depends(get_context)) -> JSONResponse:
    """Report local library file count/size and whether R2 is configured."""
    media_files = (
        [f for f in ctx.library_root.rglob("*") if f.is_file() and not f.name.startswith(".")]
        if ctx.library_root.exists()
        else []
    )
    total_bytes = sum(f.stat().st_size for f in media_files)
    return JSONResponse(
        {
            "r2_configured": ctx.r2_client is not None,
            "local_file_count": len(media_files),
            "local_size_bytes": total_bytes,
            "local_size_mb": round(total_bytes / 1024 / 1024, 1),
        }
    )


@router.post("/api/admin/reset-archive-db")
def admin_reset_archive_db(ctx: AppContext = Depends(get_context)) -> JSONResponse:
    """Delete gallery-dl's archive.sqlite so the next sync re-downloads everything."""
    archive_db = ctx.library_root / "archive.sqlite"
    if archive_db.exists():
        archive_db.unlink()
        return JSONResponse({"deleted": True, "path": str(archive_db)})
    return JSONResponse({"deleted": False, "path": str(archive_db)})

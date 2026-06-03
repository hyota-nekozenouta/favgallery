"""Duplicate-detection endpoints (exact-hash dedup + visual/perceptual dedup)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from xlikes_viewer.context import AppContext, get_context

router = APIRouter()


@router.post("/api/dedup/run")
def dedup_run(ctx: AppContext = Depends(get_context)) -> JSONResponse:
    if not ctx.dedup_runner.start():
        return JSONResponse({"started": False, "reason": "already running"}, status_code=409)
    return JSONResponse({"started": True})


@router.get("/api/dedup/status")
def dedup_status(ctx: AppContext = Depends(get_context)) -> JSONResponse:
    s = ctx.dedup_runner.state
    return JSONResponse(
        {
            "running": s.running,
            "started_at": s.started_at,
            "finished_at": s.finished_at,
            "files_total": s.files_total,
            "files_hashed": s.files_hashed,
            "duplicates_deleted": s.duplicates_deleted,
            "bytes_freed": s.bytes_freed,
            "last_error": s.last_error,
            "lifetime_deleted": ctx.db.dedup_log_count(),
        }
    )


@router.post("/api/dedup/visual/run")
def visual_dedup_run(ctx: AppContext = Depends(get_context)) -> JSONResponse:
    if not ctx.visual_dedup_runner.start():
        return JSONResponse({"started": False, "reason": "already running"}, status_code=409)
    return JSONResponse({"started": True})


@router.get("/api/dedup/visual/status")
def visual_dedup_status(ctx: AppContext = Depends(get_context)) -> JSONResponse:
    s = ctx.visual_dedup_runner.state
    return JSONResponse(
        {
            "running": s.running,
            "started_at": s.started_at,
            "finished_at": s.finished_at,
            "files_total": s.files_total,
            "files_indexed": s.files_indexed,
            "duplicates_deleted": s.duplicates_deleted,
            "bytes_freed": s.bytes_freed,
            "last_error": s.last_error,
        }
    )

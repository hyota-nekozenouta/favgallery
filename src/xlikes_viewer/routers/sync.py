"""Sync orchestration endpoints (gallery-dl run control)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from xlikes_viewer.context import AppContext, get_context

router = APIRouter()


@router.get("/api/sync/status")
def sync_status(ctx: AppContext = Depends(get_context)) -> JSONResponse:
    s = ctx.sync_runner.state
    return JSONResponse(
        {
            "running": s.running,
            "started_at": s.started_at,
            "finished_at": s.finished_at,
            "return_code": s.last_return_code,
            "error": s.last_error,
            "exe_present": True,  # gallery-dl is always available
            "log_tail": list(s.log_lines)[-40:],
        }
    )


@router.post("/api/sync/start")
def sync_start(ctx: AppContext = Depends(get_context)) -> JSONResponse:
    if not ctx.cookies_file.exists():
        return JSONResponse(
            {"started": False, "reason": "cookies.txt not found — set GALLERY_DL_COOKIES env var"},
            status_code=400,
        )
    ok = ctx.sync_runner.start()
    if not ok:
        return JSONResponse(
            {"started": False, "reason": ctx.sync_runner.state.last_error or "already running"},
            status_code=409,
        )
    return JSONResponse({"started": True})


@router.post("/api/sync/stop")
def sync_stop(ctx: AppContext = Depends(get_context)) -> JSONResponse:
    return JSONResponse({"stopped": ctx.sync_runner.stop()})

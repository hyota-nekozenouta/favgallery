"""Sync orchestration endpoints (gallery-dl run control)."""

from __future__ import annotations

from time import time

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from favgallery.context import AppContext, get_context

router = APIRouter()

# ページロード自動同期のクールダウン秒数 (Phase 2B / 2026-06-10 ひょーたさん承認
# 「10 分クールダウン」)。開くたびのフルスクレイプが X のレート制限を招いていた。
# 手動 (auto なし) は常に即時 — timeline.py の REFRESH_COOLDOWN と同型の設計。
AUTO_SYNC_COOLDOWN_SECONDS = 600.0

# Shown verbatim by the frontend (prefixed "同期エラー: ") when a sync is started
# before cookies exist. Points at the in-app cookie UI (⚙ → 🔑), which superseded
# the old GALLERY_DL_COOKIES env-var provisioning. The word "cookies" must stay —
# the frontend shows it as-is and a test asserts the reason mentions cookies.
_MSG_NO_COOKIES = "cookies が未設定です。⚙ 設定 → 🔑 から登録してください。"


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
            "last_added": s.last_added,
            "auth_error": s.auth_error,
            "exe_present": True,  # gallery-dl is always available
            "log_tail": list(s.log_lines)[-40:],
        }
    )


@router.post("/api/sync/start")
def sync_start(
    auto: bool = Query(default=False),
    ctx: AppContext = Depends(get_context),
) -> JSONResponse:
    if not ctx.cookies_file.exists():
        return JSONResponse(
            {"started": False, "reason": _MSG_NO_COOKIES},
            status_code=400,
        )
    if auto:
        s = ctx.sync_runner.state
        last = s.finished_at or s.started_at
        if last is not None and (time() - last) < AUTO_SYNC_COOLDOWN_SECONDS:
            remain = int(AUTO_SYNC_COOLDOWN_SECONDS - (time() - last))
            return JSONResponse(
                {"started": False, "reason": f"クールダウン中 (残り {remain} 秒)"},
                status_code=429,
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

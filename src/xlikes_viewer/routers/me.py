"""Own-account ("me") endpoints: X username + my-likes cache sync."""

from __future__ import annotations

import re
import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from xlikes_viewer.context import AppContext, get_context
from xlikes_viewer.timeline import fetch_my_liked_tweet_ids

router = APIRouter()

_MY_USERNAME_KEY = "my_username"


class _MeBody(BaseModel):
    username: str


@router.get("/api/me")
def api_me_get(ctx: AppContext = Depends(get_context)) -> JSONResponse:
    return JSONResponse(
        {
            "username": ctx.me_username(),
            "my_likes_count": ctx.db.my_likes_count(),
        }
    )


@router.post("/api/me")
def api_me_set(body: _MeBody, ctx: AppContext = Depends(get_context)) -> JSONResponse:
    name = body.username.strip().lstrip("@")
    if not name:
        ctx.db.set_setting(_MY_USERNAME_KEY, "")
        return JSONResponse({"username": ""})
    # Permissive validator: X handles are 1-15 chars of [A-Za-z0-9_].
    if not re.fullmatch(r"[A-Za-z0-9_]{1,15}", name):
        raise HTTPException(status_code=400, detail="invalid X username")
    ctx.db.set_setting(_MY_USERNAME_KEY, name)
    return JSONResponse({"username": name})


@router.get("/api/me/likes/status")
def api_me_likes_status(ctx: AppContext = Depends(get_context)) -> JSONResponse:
    with ctx.me_likes_lock:
        snapshot = dict(ctx.me_likes_state)
    snapshot["count"] = ctx.db.my_likes_count()
    snapshot["username"] = ctx.me_username()
    return JSONResponse(snapshot)


def _me_likes_worker(ctx: AppContext, username: str, range_spec: str) -> None:
    added = 0
    try:
        with ctx.gdl_lock:  # share gallery-dl serialization with /unliked
            tweet_ids = fetch_my_liked_tweet_ids(
                ctx.gallerydl_config_path, username, range_spec=range_spec
            )
        added = ctx.db.upsert_my_likes(tweet_ids)
    except Exception as exc:
        with ctx.me_likes_lock:
            ctx.me_likes_state["last_error"] = f"{type(exc).__name__}: {exc}"
    finally:
        with ctx.me_likes_lock:
            ctx.me_likes_state["running"] = False
            ctx.me_likes_state["last_finished"] = time.time()
            ctx.me_likes_state["last_added"] = added


@router.post("/api/me/likes/sync")
def api_me_likes_sync(
    range_spec: str = Query(default="1-200", alias="range"),
    ctx: AppContext = Depends(get_context),
) -> JSONResponse:
    username = ctx.me_username()
    if not username:
        raise HTTPException(status_code=400, detail="set username first via POST /api/me")
    with ctx.me_likes_lock:
        if ctx.me_likes_state["running"]:
            return JSONResponse({"started": False, "reason": "already running"}, status_code=409)
        ctx.me_likes_state["running"] = True
        ctx.me_likes_state["last_started"] = time.time()
        ctx.me_likes_state["last_error"] = None
        ctx.me_likes_state["last_added"] = 0
    threading.Thread(
        target=_me_likes_worker, args=(ctx, username, range_spec), daemon=True
    ).start()
    return JSONResponse({"started": True})


@router.delete("/api/me/likes")
def api_me_likes_clear(ctx: AppContext = Depends(get_context)) -> JSONResponse:
    ctx.db.clear_my_likes()
    return JSONResponse({"cleared": True})

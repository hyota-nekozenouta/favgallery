"""Timeline feed endpoints: list/by-tweet, refresh/status, last-seen,
like-and-save, and the CDN media proxy.
"""

from __future__ import annotations

from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from xlikes_viewer.context import AppContext, get_context
from xlikes_viewer.like import like_tweet
from xlikes_viewer.payloads import _timeline_payload
from xlikes_viewer.proxy import is_allowed
from xlikes_viewer.save_one import save_tweet

router = APIRouter()

_LAST_SEEN_KEY = "last_seen_timeline_tweet_id"


class _LastSeenBody(BaseModel):
    tweet_id: str


class _LikeAndSaveBody(BaseModel):
    tweet_id: str
    author_name: str


@router.get("/api/timeline")
def timeline_index(
    media_type: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    hide_liked: bool = Query(default=False),
    ctx: AppContext = Depends(get_context),
) -> JSONResponse:
    total, posts = ctx.db.list_timeline_posts(
        limit=limit, offset=offset, media_type=media_type, exclude_liked=hide_liked
    )
    return JSONResponse(
        {
            "total": total,
            "items": [_timeline_payload(p) for p in posts],
            "offset": offset,
            "limit": limit,
        }
    )


@router.get("/api/timeline/by-tweet/{tweet_id}")
def timeline_by_tweet(tweet_id: str, ctx: AppContext = Depends(get_context)) -> JSONResponse:
    posts = ctx.db.select_timeline_posts_by_tweet(tweet_id)
    return JSONResponse({"items": [_timeline_payload(p) for p in posts]})


@router.post("/api/timeline/refresh")
def timeline_refresh(ctx: AppContext = Depends(get_context)) -> JSONResponse:
    ok, reason = ctx.timeline_refresher.can_start()
    if not ok:
        return JSONResponse(
            {"started": False, "reason": reason}, status_code=429 if reason else 409
        )
    ctx.timeline_refresher.start()
    return JSONResponse({"started": True})


@router.get("/api/timeline/status")
def timeline_status(ctx: AppContext = Depends(get_context)) -> JSONResponse:
    s = ctx.timeline_refresher.state
    return JSONResponse(
        {
            "running": s.running,
            "last_started": s.last_started,
            "last_finished": s.last_finished,
            "last_added": s.last_added,
            "last_error": s.last_error,
        }
    )


@router.get("/api/timeline/last-seen")
def timeline_last_seen(ctx: AppContext = Depends(get_context)) -> JSONResponse:
    return JSONResponse({"tweet_id": ctx.db.get_setting(_LAST_SEEN_KEY) or ""})


@router.post("/api/timeline/last-seen")
def timeline_set_last_seen(
    body: _LastSeenBody, ctx: AppContext = Depends(get_context)
) -> JSONResponse:
    ctx.db.set_setting(_LAST_SEEN_KEY, body.tweet_id)
    return JSONResponse({"tweet_id": body.tweet_id})


@router.post("/api/timeline/like-and-save")
def timeline_like_and_save(
    body: _LikeAndSaveBody, ctx: AppContext = Depends(get_context)
) -> JSONResponse:
    like_result = like_tweet(ctx.cookies_file, body.tweet_id)
    save_result = None
    if like_result.ok:
        # Record it in the my_likes cache so the "未いいね" filter hides this
        # tweet on subsequent fetches even if save_tweet fails.
        ctx.db.upsert_my_likes([body.tweet_id])
        save_result = save_tweet(
            ctx.gallerydl_config_path,
            author_name=body.author_name,
            tweet_id=body.tweet_id,
        )
        if save_result.ok:
            # Refresh the in-memory index so the new file appears in /api/posts.
            ctx.refresh_index()
    return JSONResponse(
        {
            "liked": like_result.ok,
            "like_status": like_result.status_code,
            "like_message": like_result.message,
            "saved": save_result.ok if save_result else False,
            "save_message": save_result.message if save_result else "",
        }
    )


@router.get("/api/timeline/proxy")
async def timeline_proxy(
    request: Request, url: str, ctx: AppContext = Depends(get_context)
) -> Response:
    if not is_allowed(url):
        raise HTTPException(status_code=400, detail="disallowed host")
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise HTTPException(status_code=400, detail="https only")
    range_header = request.headers.get("range")
    try:
        status, headers, body_iter = await ctx.cdn_proxy.stream(url, range_header=range_header)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"upstream error: {exc}") from exc
    return StreamingResponse(body_iter, status_code=status, headers=headers)

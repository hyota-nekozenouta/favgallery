"""Custom lists (collections) endpoints + per-post list membership."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from xlikes_viewer.context import AppContext, get_context

router = APIRouter()


class _ListCreateBody(BaseModel):
    name: str


class _ListItemBody(BaseModel):
    tweet_id: str
    num: int


@router.get("/api/lists")
def lists_index(ctx: AppContext = Depends(get_context)) -> JSONResponse:
    return JSONResponse(
        [
            {"id": x.id, "name": x.name, "count": x.item_count, "created_at": x.created_at}
            for x in ctx.db.lists()
        ]
    )


@router.post("/api/lists")
def lists_create(body: _ListCreateBody, ctx: AppContext = Depends(get_context)) -> JSONResponse:
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name required")
    try:
        row = ctx.db.create_list(name)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return JSONResponse({"id": row.id, "name": row.name, "count": 0, "created_at": row.created_at})


@router.delete("/api/lists/{list_id}")
def lists_delete(list_id: int, ctx: AppContext = Depends(get_context)) -> JSONResponse:
    if not ctx.db.delete_list(list_id):
        raise HTTPException(status_code=404, detail="not found")
    return JSONResponse({"deleted": True})


@router.post("/api/lists/{list_id}/items")
def lists_add_item(
    list_id: int, body: _ListItemBody, ctx: AppContext = Depends(get_context)
) -> JSONResponse:
    if not body.tweet_id or body.num <= 0:
        raise HTTPException(status_code=400, detail="tweet_id and num required")
    added = ctx.db.add_item(list_id, body.tweet_id, body.num)
    return JSONResponse({"added": added})


@router.delete("/api/lists/{list_id}/items/{tweet_id}/{num}")
def lists_remove_item(
    list_id: int, tweet_id: str, num: int, ctx: AppContext = Depends(get_context)
) -> JSONResponse:
    removed = ctx.db.remove_item(list_id, tweet_id, num)
    return JSONResponse({"removed": removed})


@router.get("/api/posts/lists")
def post_lists(tweet_id: str, num: int, ctx: AppContext = Depends(get_context)) -> JSONResponse:
    return JSONResponse({"list_ids": ctx.db.lists_for_post(tweet_id, num)})

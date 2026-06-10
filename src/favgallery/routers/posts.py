"""Core posts endpoints: library metadata, search, per-tweet, author summary,
delete, favorite-authors, and the "unliked from author" gallery-dl lookup.
"""

from __future__ import annotations

import contextlib
import dataclasses

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from xlikes_viewer.context import AppContext, get_context
from xlikes_viewer.keys import r2_key_for_path
from xlikes_viewer.payloads import _post_payload, _timeline_payload
from xlikes_viewer.timeline import fetch_author_media_posts

router = APIRouter()


class _FavAuthorsBody(BaseModel):
    authors: list[str]


@router.get("/api/library")
def api_library(ctx: AppContext = Depends(get_context)) -> JSONResponse:
    idx = ctx.get_index()
    return JSONResponse(
        {
            "library_root": str(idx.library_root),
            "post_count": len(idx.posts),
            "authors": [dataclasses.asdict(a) for a in idx.authors.values()],
            "tags": [{"name": k, "count": v} for k, v in list(idx.tags.items())[:200]],
            "scanning": ctx.get_scanning(),
        }
    )


@router.post("/api/library/refresh")
def api_refresh(ctx: AppContext = Depends(get_context)) -> JSONResponse:
    idx = ctx.refresh_index()
    return JSONResponse({"post_count": len(idx.posts)})


@router.get("/api/posts")
def api_posts(
    author: str | None = None,
    tag: str | None = None,
    media_type: str | None = None,
    q: str | None = None,
    list_id: int | None = Query(default=None, alias="list"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    ctx: AppContext = Depends(get_context),
) -> JSONResponse:
    idx = ctx.get_index()
    filtered = idx.filter(author=author, tag=tag, media_type=media_type, query=q)
    if list_id is not None:
        keys = ctx.db.posts_in_list(list_id)
        filtered = [p for p in filtered if (p.tweet_id, p.num) in keys]
    total = len(filtered)
    page = filtered[offset : offset + limit]
    listed_keys = ctx.db.all_listed_post_keys()
    items = [_post_payload(p) for p in page]
    for item, p in zip(items, page, strict=False):
        item["in_any_list"] = (p.tweet_id, p.num) in listed_keys
    return JSONResponse(
        {
            "total": total,
            "items": items,
            "offset": offset,
            "limit": limit,
        }
    )


@router.get("/api/posts/by-tweet/{tweet_id}")
def api_posts_by_tweet(tweet_id: str, ctx: AppContext = Depends(get_context)) -> JSONResponse:
    idx = ctx.get_index()
    items = sorted(
        (p for p in idx.posts if p.tweet_id == tweet_id),
        key=lambda p: p.num,
    )
    return JSONResponse({"items": [_post_payload(p) for p in items]})


@router.get("/api/authors/{author}/summary")
def api_author_summary(author: str, ctx: AppContext = Depends(get_context)) -> JSONResponse:
    idx = ctx.get_index()
    posts = [p for p in idx.posts if p.author_name == author]
    counts: dict[str, int] = {"total": len(posts)}
    for p in posts:
        counts[p.media_type] = counts.get(p.media_type, 0) + 1
    nick = posts[0].author_nick if posts else ""
    return JSONResponse({"author": author, "nick": nick, "counts": counts})


@router.get("/api/authors/{author}/unliked")
def api_author_unliked(
    author: str,
    limit: int = Query(default=60, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=10000),
    ctx: AppContext = Depends(get_context),
) -> JSONResponse:
    idx = ctx.get_index()
    local_tweet_ids = {p.tweet_id for p in idx.posts if p.author_name == author}
    my_liked_ids = ctx.db.my_likes_ids()
    # gallery-dl uses 1-based inclusive ranges (`file-range`).
    start = offset + 1
    end = offset + limit
    try:
        with ctx.gdl_lock:
            posts = fetch_author_media_posts(
                ctx.gallerydl_config_path,
                author,
                range_spec=f"{start}-{end}",
            )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"gallery-dl failed: {type(exc).__name__}: {exc}",
        ) from exc
    # "Unliked" = on X, but: not in my_likes cache (own liked tweets), not in
    # local likes archive, and not flagged as `favorited` in the raw metadata.
    unliked = [
        p
        for p in posts
        if not p.favorited
        and p.tweet_id not in local_tweet_ids
        and p.tweet_id not in my_liked_ids
    ]
    # has_more is heuristic: a full page implies there might be more.
    return JSONResponse(
        {
            "author": author,
            "fetched": len(posts),
            "offset": offset,
            "limit": limit,
            "has_more": len(posts) >= limit,
            "items": [_timeline_payload(p) for p in unliked],
        }
    )


@router.delete("/api/posts/{tweet_id}/{num}")
def delete_post(tweet_id: str, num: int, ctx: AppContext = Depends(get_context)) -> JSONResponse:
    idx = ctx.get_index()
    target = next(
        (p for p in idx.posts if p.tweet_id == tweet_id and p.num == num),
        None,
    )
    if target is None:
        raise HTTPException(status_code=404, detail="post not found")

    media = target.media_path.resolve()
    try:
        media.relative_to(ctx.library_root_resolved)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="path escape") from e

    # Sidecar lives next to the media file as "<media>.json". Derive it from the
    # media path rather than target.json_path: the DB-backed index sets
    # json_path == media_path as a placeholder (unused when serving from R2).
    sidecar = media.with_name(media.name + ".json")
    rel = r2_key_for_path(media, ctx.library_root_resolved)

    with contextlib.suppress(FileNotFoundError):
        media.unlink()
    with contextlib.suppress(FileNotFoundError):
        sidecar.unlink()

    # In R2-backed deployments the media lives in R2 (the local copy is deleted
    # after upload), so unlink() above is a no-op there. Purge the R2 object too
    # — otherwise every delete leaks an orphaned media file. A failure here must
    # NOT fail the delete: the DB row + local files are already gone.
    if ctx.r2_client is not None:
        try:
            ctx.r2_client.delete_object(rel)
        except Exception as exc:  # degrade gracefully like other R2 calls
            print(f"[r2] delete_object failed for {rel}: {exc}")

    # Drop the DB row so the post leaves the index for good — otherwise a later
    # sidecar re-ingest resurrects it. Lists + hash cache cascade-cleaned. The
    # gallery-dl archive.sqlite is intentionally untouched so the next sync does
    # not re-download this tweet's media.
    ctx.db.delete_post(tweet_id, num)
    ctx.db.remove_item_from_all_lists(tweet_id, num)
    ctx.db.forget_hash(rel)
    ctx.refresh_index()
    return JSONResponse({"deleted": True})


@router.get("/api/favorite-authors")
def fav_authors_get(ctx: AppContext = Depends(get_context)) -> JSONResponse:
    return JSONResponse(ctx.db.get_favorite_authors())


@router.post("/api/favorite-authors")
def fav_authors_set(
    body: _FavAuthorsBody, ctx: AppContext = Depends(get_context)
) -> JSONResponse:
    ctx.db.set_favorite_authors(body.authors)
    return JSONResponse({"saved": True})

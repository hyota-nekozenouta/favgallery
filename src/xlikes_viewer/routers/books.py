"""Bookshelf endpoints: CRUD, tags/favorites, multipart upload, URL import.

Literal routes (e.g. /api/books/tags) MUST be registered before the
parametrized /api/books/{book_id} (int) route. Otherwise FastAPI matches
/api/books/tags as book_id="tags" and rejects it with 422 (int_parsing)
before reaching api_book_tags.
"""

from __future__ import annotations

import io
import re
import shutil
import time
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from xlikes_viewer.book_dedup import find_duplicate_book, fingerprint_for_ordered_files
from xlikes_viewer.context import AppContext, get_context

router = APIRouter()


class _BookPatchBody(BaseModel):
    title: str | None = None


class _BookTagsBody(BaseModel):
    tags: list[str]


class _BookImportBody(BaseModel):
    url: str


def _natural_key(name: str) -> list:
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r"(\d+)", name)]


@router.get("/api/books")
def api_books(ctx: AppContext = Depends(get_context)) -> JSONResponse:
    items = ctx.db.books()
    fav_ids = ctx.db.book_favorite_ids()
    all_tags = {b.id: ctx.db.book_tags(b.id) for b in items}
    return JSONResponse(
        [
            {
                "id": b.id,
                "title": b.title,
                "cover_path": b.cover_path,
                "page_count": b.page_count,
                "created_at": b.created_at,
                "is_favorite": b.id in fav_ids,
                "tags": all_tags[b.id],
            }
            for b in items
        ]
    )


@router.get("/api/books/tags")
def api_book_tags(ctx: AppContext = Depends(get_context)) -> JSONResponse:
    tags = ctx.db.all_book_tags()
    return JSONResponse({"tags": [{"name": t[0], "count": t[1]} for t in tags]})


@router.get("/api/books/{book_id}")
def api_book_detail(book_id: int, ctx: AppContext = Depends(get_context)) -> JSONResponse:
    book = ctx.db.get_book(book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="book not found")
    pages = ctx.db.book_pages(book_id)
    return JSONResponse(
        {
            "id": book.id,
            "title": book.title,
            "cover_path": book.cover_path,
            "page_count": book.page_count,
            "created_at": book.created_at,
            "pages": [
                {
                    "page_num": p.page_num,
                    "rel_path": p.rel_path,
                    "width": p.width,
                    "height": p.height,
                }
                for p in pages
            ],
        }
    )


@router.post("/api/books")
async def api_create_book(
    title: str = Form(...),
    files: list[UploadFile] = File(...),
    ctx: AppContext = Depends(get_context),
) -> JSONResponse:
    if not files:
        raise HTTPException(status_code=400, detail="no files provided")

    sorted_files = sorted(files, key=lambda f: _natural_key(f.filename or ""))

    # Create book record first to get ID.
    book = ctx.db.create_book(title=title, cover_path=None, page_count=len(sorted_files))

    book_dir = ctx.library_root / ctx.books_dir / str(book.id)
    book_dir.mkdir(parents=True, exist_ok=True)

    pages: list[tuple[int, str, int | None, int | None]] = []
    cover_rel: str | None = None

    for i, f in enumerate(sorted_files, start=1):
        ext = Path(f.filename or "page.jpg").suffix.lower() or ".jpg"
        filename = f"{i:04d}{ext}"
        dest = book_dir / filename
        content = await f.read()
        dest.write_bytes(content)

        rel = f"{ctx.books_dir}/{book.id}/{filename}"
        if i == 1:
            cover_rel = rel

        w, h = None, None
        try:
            from PIL import Image

            img = Image.open(io.BytesIO(content))
            w, h = img.size
        except Exception:
            pass

        pages.append((i, rel, w, h))

    ctx.db.add_book_pages(book.id, pages)

    if cover_rel:
        with ctx.db._lock:
            ctx.db._conn.execute(
                "UPDATE books SET cover_path = ? WHERE id = ?", (cover_rel, book.id)
            )

    # Duplicate check (files are still on local disk, nothing uploaded yet).
    ordered_files = [ctx.library_root / rel for (_pn, rel, _w, _h) in pages]
    page_count, cover_phash, samples = fingerprint_for_ordered_files(ordered_files)
    dup = find_duplicate_book(ctx.db, page_count, cover_phash, samples)
    if dup is not None:
        # Roll back the just-created book + its files; report the match.
        ctx.db.delete_book(book.id)  # cascades book_pages/tags/favorites/hashes
        shutil.rmtree(book_dir, ignore_errors=True)
        return JSONResponse(
            {"skipped": True, "matched_book_id": dup[0], "matched_title": dup[1]},
            status_code=200,
        )
    # Not a duplicate: persist its fingerprint so future imports can match it.
    ctx.db.upsert_book_hash(
        book_id=book.id,
        page_count=page_count,
        cover_phash=cover_phash,
        sample_phashes=samples,
        indexed_at=int(time.time()),
    )

    # Upload to R2 if configured, then delete local copies.
    if ctx.r2_client is not None:
        for i, f in enumerate(sorted_files, start=1):
            ext = Path(f.filename or "page.jpg").suffix.lower() or ".jpg"
            filename = f"{i:04d}{ext}"
            local_path = book_dir / filename
            key = f"{ctx.books_dir}/{book.id}/{filename}"
            try:
                ctx.r2_client.upload_file(local_path, key)
                local_path.unlink()
            except Exception:
                pass  # Keep local if R2 fails
        if book_dir.exists() and not any(book_dir.iterdir()):
            book_dir.rmdir()

    return JSONResponse(
        {"id": book.id, "title": book.title, "page_count": len(pages)}, status_code=201
    )


@router.delete("/api/books/{book_id}")
def api_delete_book(book_id: int, ctx: AppContext = Depends(get_context)) -> JSONResponse:
    book_dir = ctx.library_root / ctx.books_dir / str(book_id)
    if book_dir.exists():
        shutil.rmtree(book_dir, ignore_errors=True)
    if not ctx.db.delete_book(book_id):
        raise HTTPException(status_code=404, detail="book not found")
    return JSONResponse({"deleted": True})


@router.patch("/api/books/{book_id}")
def api_patch_book(
    book_id: int, body: _BookPatchBody, ctx: AppContext = Depends(get_context)
) -> JSONResponse:
    if body.title is not None and not ctx.db.update_book_title(book_id, body.title.strip()):
        raise HTTPException(status_code=404, detail="book not found")
    book = ctx.db.get_book(book_id)
    return JSONResponse({"id": book.id, "title": book.title, "page_count": book.page_count})


@router.put("/api/books/{book_id}/tags")
def api_set_book_tags(
    book_id: int, body: _BookTagsBody, ctx: AppContext = Depends(get_context)
) -> JSONResponse:
    if ctx.db.get_book(book_id) is None:
        raise HTTPException(status_code=404, detail="book not found")
    ctx.db.set_book_tags(book_id, body.tags)
    return JSONResponse({"ok": True})


@router.post("/api/books/{book_id}/favorite")
def api_toggle_book_favorite(
    book_id: int, ctx: AppContext = Depends(get_context)
) -> JSONResponse:
    if ctx.db.get_book(book_id) is None:
        raise HTTPException(status_code=404, detail="book not found")
    new_state = ctx.db.toggle_book_favorite(book_id)
    return JSONResponse({"favorite": new_state})


@router.post("/api/books/import")
def api_import_book(body: _BookImportBody, ctx: AppContext = Depends(get_context)) -> JSONResponse:
    if not body.url.strip():
        raise HTTPException(status_code=400, detail="URL is required")
    item = ctx.book_import_queue.enqueue(body.url.strip())
    return JSONResponse({"started": True, "id": item["id"]})


@router.get("/api/books/import/status")
def api_import_status(ctx: AppContext = Depends(get_context)) -> JSONResponse:
    return JSONResponse({"queue": ctx.book_import_queue.snapshot()})


@router.get("/api/books/index/status")
def api_book_index_status(ctx: AppContext = Depends(get_context)) -> JSONResponse:
    s = ctx.book_index_runner.state
    return JSONResponse(
        {
            "running": s.running,
            "books_total": s.books_total,
            "books_indexed": s.books_indexed,
            "last_error": s.last_error,
        }
    )

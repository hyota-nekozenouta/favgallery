"""In-app X cookie management: set / inspect / verify the gallery-dl cookies.

Root cause of the "いいね・新しい投稿の更新ができない" bug: the deployed container
had no ``cookies.txt`` (``GALLERY_DL_COOKIES`` env unset and no way to provision
cookies from the running web app). These endpoints let cookies be pasted/uploaded
from the UI, persisted to the volume (``ctx.cookies_file``), and verified — so
re-auth on cookie expiry is self-serve and needs no Railway access or redeploy.

Security: all routes sit behind the existing Basic-auth middleware. The status
endpoint never returns the raw cookie content (only presence / size / format
validity) so secrets can't leak back out.
"""

from __future__ import annotations

import contextlib
import os
import tempfile

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from xlikes_viewer.context import AppContext, get_context
from xlikes_viewer.gdl_errors import is_auth_failure
from xlikes_viewer.timeline import fetch_my_liked_tweet_ids

router = APIRouter()

# User-facing messages (kept as constants so the route bodies stay readable and
# under the line-length limit, and so the wording lives in one place).
_MSG_BAD_FORMAT = (
    "cookies.txt の形式に見えません。Netscape 形式の cookies "
    "[タブ区切り・auth_token を含む] を貼り付けてください。"
)
_MSG_NOT_SET = "cookies が未設定です。"
_MSG_NO_USERNAME = (
    "X ユーザー名が未設定です。ユーザー名を保存してから接続テストしてください。"
)
_MSG_AUTH_FAIL = "cookie が失効・無効です。X に再ログインして cookies を更新してください。"
_MSG_OK = "認証OK。cookie は有効です。"


class _CookiesBody(BaseModel):
    content: str


def _looks_like_cookies(text: str) -> bool:
    """Heuristic check that ``text`` is a Netscape cookies.txt for X.

    Lenient enough not to reject a valid paste, strict enough to reject
    obviously-not-cookies input (so a stray paste can't blank out a working
    cookies file). A real X cookies.txt has tab-separated rows and the X
    session/auth cookies.
    """
    if not text or not text.strip():
        return False
    if "\t" not in text:
        return False
    lowered = text.lower()
    return any(tok in lowered for tok in ("auth_token", "x.com", "twitter.com"))


def _status_payload(ctx: AppContext) -> dict:
    """Presence / size / format of the cookies file — never the content itself."""
    path = ctx.cookies_file
    if not path.exists():
        return {"configured": False, "updated_at": None, "size": 0, "looks_valid": False}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        text = ""
    try:
        stat = path.stat()
        size, mtime = stat.st_size, stat.st_mtime
    except OSError:
        size, mtime = 0, None
    return {
        "configured": bool(text.strip()),
        "updated_at": mtime,
        "size": size,
        "looks_valid": _looks_like_cookies(text),
    }


@router.get("/api/cookies/status")
def cookies_status(ctx: AppContext = Depends(get_context)) -> JSONResponse:
    return JSONResponse(_status_payload(ctx))


@router.post("/api/cookies")
def cookies_set(body: _CookiesBody, ctx: AppContext = Depends(get_context)) -> JSONResponse:
    content = body.content
    if not _looks_like_cookies(content):
        return JSONResponse({"detail": _MSG_BAD_FORMAT}, status_code=400)
    # Atomic write: a crash mid-write must not leave a half-written cookies.txt
    # that breaks every subsequent sync.
    normalised = content if content.endswith("\n") else content + "\n"
    path = ctx.cookies_file
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".cookies.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(normalised)
        os.replace(tmp, path)
    except OSError as exc:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        return JSONResponse({"detail": f"保存に失敗しました: {exc}"}, status_code=500)
    return JSONResponse(_status_payload(ctx))


@router.post("/api/cookies/verify")
def cookies_verify(ctx: AppContext = Depends(get_context)) -> JSONResponse:
    """Lightweight live probe: fetch one of the user's own likes to confirm the
    cookies actually authenticate (vs. present-but-expired)."""
    if not ctx.cookies_file.exists():
        return JSONResponse({"ok": False, "auth_error": False, "message": _MSG_NOT_SET})
    username = ctx.me_username()
    if not username:
        return JSONResponse(
            {"ok": False, "auth_error": False, "message": _MSG_NO_USERNAME}
        )
    try:
        with ctx.gdl_lock:
            fetch_my_liked_tweet_ids(
                ctx.gallerydl_config_path, username, range_spec="1-1"
            )
    except Exception as exc:
        if is_auth_failure(exc):
            return JSONResponse(
                {"ok": False, "auth_error": True, "message": _MSG_AUTH_FAIL}
            )
        msg = f"確認に失敗しました: {type(exc).__name__}: {exc}"
        return JSONResponse({"ok": False, "auth_error": False, "message": msg})
    return JSONResponse({"ok": True, "auth_error": False, "message": _MSG_OK})

"""JSON payload builders shared by the post / timeline / me routers.

Lives in its own module so routers don't have to import ``server`` (which would
create an import cycle, since ``server`` imports the routers).
"""

from __future__ import annotations

from favgallery.db import TimelinePost
from favgallery.scanner import Post
from favgallery.x_helpers import tweet_url


def _base_payload(
    *,
    tweet_id: str,
    num: int,
    media_url: str,
    thumb_url: str,
    media_type: str,
    extension: str,
    width: int | None,
    height: int | None,
    date: str,
    author_name: str,
    author_nick: str,
    content: str,
    favorite_count: int,
    view_count: int,
    sensitive: bool,
    lang: str,
    hashtags: tuple[str, ...],
    favorite_keys: set[tuple[str, int]] | None = None,
    extra: dict | None = None,
) -> dict:
    payload = {
        "tweet_id": tweet_id,
        "num": num,
        # 常に含める。欠けていると UI が「未登録」と誤認し、★タップで既存の
        # お気に入りを消してしまう (2026-07-28 の回帰を防ぐための不変条件)。
        "is_favorite": (tweet_id, num) in favorite_keys if favorite_keys else False,
        "media_url": media_url,
        "thumb_url": thumb_url,
        "media_type": media_type,
        "extension": extension,
        "width": width,
        "height": height,
        "date": date,
        "author_name": author_name,
        "author_nick": author_nick,
        "content": content,
        "favorite_count": favorite_count,
        "view_count": view_count,
        "sensitive": sensitive,
        "lang": lang,
        "hashtags": list(hashtags),
        "tweet_url": tweet_url(author_name, tweet_id),
    }
    if extra:
        payload.update(extra)
    return payload


def _post_payload(p: Post, favorite_keys: set[tuple[str, int]] | None = None) -> dict:
    return _base_payload(
        favorite_keys=favorite_keys,
        tweet_id=p.tweet_id,
        num=p.num,
        media_url=f"/api/media/{p.rel_media}",
        thumb_url=f"/thumb/{p.rel_media}",
        media_type=p.media_type,
        extension=p.extension,
        width=p.width,
        height=p.height,
        date=p.date,
        author_name=p.author_name,
        author_nick=p.author_nick,
        content=p.content,
        favorite_count=p.favorite_count,
        view_count=p.view_count,
        sensitive=p.sensitive,
        lang=p.lang,
        hashtags=p.hashtags,
    )


def _timeline_payload(
    p: TimelinePost, favorite_keys: set[tuple[str, int]] | None = None
) -> dict:
    return _base_payload(
        favorite_keys=favorite_keys,
        tweet_id=p.tweet_id,
        num=p.num,
        media_url=f"/api/timeline/proxy?url={p.media_url}",
        thumb_url=f"/api/timeline/proxy?url={p.thumb_url}",
        media_type=p.media_type,
        extension="",
        width=p.width,
        height=p.height,
        date=p.date,
        author_name=p.author_name,
        author_nick=p.author_nick,
        content=p.content,
        favorite_count=p.favorite_count,
        view_count=p.view_count,
        sensitive=False,
        lang="",
        hashtags=p.hashtags,
        extra={"raw_media_url": p.media_url, "author_avatar_url": p.author_avatar_url},
    )

"""JSON payload builders shared by the post / timeline / me routers.

Lives in its own module so routers don't have to import ``server`` (which would
create an import cycle, since ``server`` imports the routers).
"""

from __future__ import annotations

from xlikes_viewer.db import TimelinePost
from xlikes_viewer.scanner import Post
from xlikes_viewer.x_helpers import tweet_url


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
    extra: dict | None = None,
) -> dict:
    payload = {
        "tweet_id": tweet_id,
        "num": num,
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


def _post_payload(p: Post) -> dict:
    return _base_payload(
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


def _timeline_payload(p: TimelinePost) -> dict:
    return _base_payload(
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

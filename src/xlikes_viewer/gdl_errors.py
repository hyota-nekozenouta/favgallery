"""Detect gallery-dl authentication failures (expired/invalid X cookies).

gallery-dl never raises to *our* caller on an auth failure — it handles the
error internally — so a sync/timeline refresh otherwise looks like "succeeded
with nothing new". There are two real paths (verified against gallery-dl 1.32.x):

1. ``DownloadJob`` (the likes sync): ``Job.run()`` catches the
   ``AuthorizationError`` / ``AuthRequired`` and logs it with
   ``log.error("%s: %s", ClassName, exc)`` on the *extractor* logger. The
   extractor's logger name is its category — ``"twitter"`` — NOT ``"gallery_dl"``
   (``extractor/common.py``: ``self.log = logging.getLogger(self.category)``).
   → detect by scanning captured ``twitter`` log lines (:func:`detect_auth_failure`).

2. ``DataJob`` (the timeline refresh): stores the exception on ``job.exception``
   and logs nothing at all.
   → detect by exception type (:func:`is_auth_exception`).

Because the extractor logs to ``"twitter"`` (which propagates to root) we capture
from the *root* logger and filter to the gallery-dl/extractor logger names.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Iterator
from contextlib import contextmanager

# Signatures of an auth/cookie failure. Kept specific: the class-name tokens are
# what Job.run() logs, the phrase tokens are real X/extractor messages, and the
# HTTP codes only match the canonical reason phrase ("401 Unauthorized") so a
# bare "-> 403" rate-limit line or the word "forbidden" inside tweet text does
# NOT nag the user to re-login.
_AUTH_FAILURE_RE = re.compile(
    r"authrequired"
    r"|authorizationerror"
    r"|authenticationerror"
    r"|could not authenticate"
    r"|needed to access"
    r"|login required"
    r"|requires login"
    r"|not logged ?in"
    r"|temporarily locked"
    r"|use browser cookies"
    # Expired-but-present cookies: the GraphQL likes/timeline path aborts with
    # this exact message (twitter.py: "Unable to retrieve Tweets from ...").
    r"|unable to retrieve tweets"
    r"|\b40[13]\s+(?:unauthorized|forbidden)\b",
    re.IGNORECASE,
)

#: User-facing message shown when an auth failure is detected. Actionable: tells
#: ひょーたさん exactly what to do (re-login and refresh cookies).
AUTH_FAILURE_MESSAGE = (
    "X の cookie が失効している可能性があります。"
    "認証エラーのため、再ログインして cookies を更新してください。"
)

# gallery-dl exception classes that mean "auth/cookies problem". Matched by name
# across the MRO so we don't have to import gallery_dl here (keeps this module
# import-light) and so subclasses (AuthRequired < AuthorizationError) are covered.
_AUTH_EXC_NAMES = frozenset(
    {"AuthorizationError", "AuthRequired", "AuthenticationError"}
)

# gallery-dl's own logger plus the extractor category loggers we care about.
_GDL_LOGGER_NAMES = ("gallery_dl", "gallery-dl", "twitter")


def detect_auth_failure(lines: Iterable[str]) -> bool:
    """Return True if any captured gallery-dl log line looks like an auth failure."""
    return any(_AUTH_FAILURE_RE.search(line) for line in lines)


def is_auth_exception(exc: BaseException | None) -> bool:
    """Return True if ``exc`` is (or subclasses) a gallery-dl auth exception."""
    if exc is None:
        return False
    return any(cls.__name__ in _AUTH_EXC_NAMES for cls in type(exc).__mro__)


def is_auth_failure(exc: BaseException | None) -> bool:
    """Return True if ``exc`` represents an auth/cookie failure — by type OR message.

    Expired-but-present cookies don't raise an auth-typed exception; gallery-dl
    raises a generic ``AbortExtraction`` whose *message* ("Unable to retrieve
    Tweets ...", "Could not authenticate you.") is the only signal. So we check
    both the exception type and its stringified message.
    """
    if exc is None:
        return False
    return is_auth_exception(exc) or detect_auth_failure([str(exc)])


def _is_gdl_record(record: logging.LogRecord) -> bool:
    name = record.name
    return name in _GDL_LOGGER_NAMES or name.startswith(("gallery_dl.", "twitter."))


class _GdlCaptureHandler(logging.Handler):
    """Appends formatted gallery-dl / extractor records to a sink (list or deque)."""

    def __init__(self, sink: list[str]) -> None:
        super().__init__()
        self._sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        if not _is_gdl_record(record):
            return
        try:
            self._sink.append(self.format(record))
        except Exception:
            self.handleError(record)


@contextmanager
def capture_gdl_logs(
    sink: list[str] | None = None, level: int = logging.WARNING
) -> Iterator[list[str]]:
    """Capture gallery-dl / extractor log records into ``sink`` for the block.

    Attaches to the *root* logger (the ``twitter`` extractor logger propagates
    there) and filters to gallery-dl-family logger names so unrelated app logs
    are not collected. Pass an existing list/deque as ``sink`` to append into it.
    """
    if sink is None:
        sink = []
    root = logging.getLogger()
    handler = _GdlCaptureHandler(sink)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    handler.setLevel(level)
    root.addHandler(handler)
    try:
        yield sink
    finally:
        root.removeHandler(handler)

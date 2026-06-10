"""Tests for detecting gallery-dl auth failures.

gallery-dl does NOT raise to the caller on expired/invalid X cookies. Two real
code paths exist (verified against the installed gallery-dl 1.32.x source):

* DownloadJob (the likes sync) — Job.run() catches the AuthorizationError /
  AuthRequired and logs it via the *extractor* logger, whose name is the
  category ``"twitter"`` (extractor/common.py: ``self.log = getLogger(category)``).
  So detection here is by scanning captured ``twitter`` log lines.
* DataJob (the timeline refresh) — stores the exception on ``job.exception``
  and logs nothing. So detection there is by exception type.

These tests pin both: the regex/log path and the exception-type path, plus the
false-positive cases that must NOT trip a "re-login" nag.
"""

from __future__ import annotations

import logging

import pytest
from gallery_dl import exception as gdle

from favgallery.gdl_errors import (
    AUTH_FAILURE_MESSAGE,
    capture_gdl_logs,
    detect_auth_failure,
    is_auth_exception,
    is_auth_failure,
)


@pytest.mark.unit
@pytest.mark.parametrize(
    "line",
    [
        # Job.run() logs "%s: %s" % (ClassName, exc) on the 'twitter' logger.
        "ERROR AuthRequired: auth_token or cookies needed to access this likes",
        "ERROR AuthorizationError: Insufficient privileges to access this resource",
        "ERROR AuthenticationError: Invalid login credentials",
        # X API error bodies surfaced by the extractor.
        "ERROR 'Could not authenticate you.'",
        "WARNING Account temporarily locked",
        "ERROR Login with username & password is no longer supported. Use browser cookies instead.",
        # HttpError canonical reason phrases.
        "ERROR HttpError: '401 Unauthorized'",
        "WARNING [twitter] 403 Forbidden",
        # EXPIRED-but-present cookies: the GraphQL timeline path aborts with this
        # (twitter.py:2171) — logged via Job.run()'s AbortExtraction branch.
        "ERROR Unable to retrieve Tweets from this timeline",
    ],
)
def test_detects_real_auth_failure_lines(line: str) -> None:
    assert detect_auth_failure([line]) is True


@pytest.mark.unit
@pytest.mark.parametrize(
    "lines",
    [
        [],
        ["INFO [twitter] 1002_1.jpg"],
        ["INFO downloaded 5 files", "INFO [sync] download complete"],
        ["WARNING file already exists, skipping"],
        ["ERROR ffmpeg not found on PATH"],
        # A 4xx that is NOT a cookie/auth problem must not nag the user:
        ["WARNING [twitter] rate-limit hit https://api.twitter.com/... -> 403"],
        ["INFO status=403 geo-block region=JP"],
        # Tweet text / bios echoed in WARNING logs containing the bare words:
        ["WARNING [twitter] 'This is expressly forbidden by our terms' (1234567890)"],
        ["WARNING u42: 'sharing unauthorized leaks here'"],
        # Must NOT match: gallery-dl config deprecation (not an auth failure):
        ["WARNING config key 'foo' is no longer supported"],
        # Must NOT match: rate-limit/security holds that aren't a cookie problem:
        ["WARNING [twitter] account actions locked due to rate limit"],
    ],
)
def test_no_false_positive(lines: list[str]) -> None:
    assert detect_auth_failure(lines) is False


@pytest.mark.unit
def test_capture_collects_twitter_logger(monkeypatch: pytest.MonkeyPatch) -> None:
    # The real extractor logs to the 'twitter' logger, NOT 'gallery_dl'. The
    # capture must see it (it propagates to root).
    with capture_gdl_logs() as logs:
        logging.getLogger("twitter").error(
            "AuthRequired: auth_token or cookies needed to access this likes"
        )
    assert detect_auth_failure(logs) is True


@pytest.mark.unit
def test_capture_ignores_unrelated_loggers() -> None:
    with capture_gdl_logs() as logs:
        logging.getLogger("uvicorn.error").error("401 Unauthorized from a proxy")
    assert logs == []  # not a gallery-dl/extractor record


@pytest.mark.unit
@pytest.mark.parametrize(
    "exc",
    [
        gdle.AuthRequired(("auth_token", "cookies"), "likes"),
        gdle.AuthorizationError(),
        gdle.AuthenticationError(),
    ],
)
def test_is_auth_exception_true_for_auth_errors(exc: Exception) -> None:
    assert is_auth_exception(exc) is True


@pytest.mark.unit
@pytest.mark.parametrize("exc", [ValueError("x"), RuntimeError("y"), OSError("z")])
def test_is_auth_exception_false_for_others(exc: Exception) -> None:
    assert is_auth_exception(exc) is False


@pytest.mark.unit
def test_is_auth_failure_catches_expired_cookie_abortextraction() -> None:
    # Expired-but-present cookies: gallery-dl raises AbortExtraction (NOT an auth
    # exception type) whose MESSAGE is the only signal. is_auth_failure must
    # catch it via the message, where is_auth_exception alone would not.
    exc = gdle.AbortExtraction("Unable to retrieve Tweets from this timeline")
    assert is_auth_exception(exc) is False  # type alone misses it
    assert is_auth_failure(exc) is True  # message catches it


@pytest.mark.unit
def test_is_auth_failure_catches_auth_exception_types() -> None:
    assert is_auth_failure(gdle.AuthRequired(("auth_token", "cookies"), "likes")) is True
    assert is_auth_failure(gdle.AuthorizationError()) is True


@pytest.mark.unit
@pytest.mark.parametrize("exc", [None, ValueError("nope"), RuntimeError("boom")])
def test_is_auth_failure_false_for_non_auth(exc: Exception | None) -> None:
    assert is_auth_failure(exc) is False


@pytest.mark.unit
def test_auth_failure_message_is_user_facing() -> None:
    assert AUTH_FAILURE_MESSAGE
    assert "cookie" in AUTH_FAILURE_MESSAGE.lower()

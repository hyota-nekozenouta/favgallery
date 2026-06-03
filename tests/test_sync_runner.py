"""Behavioural tests for SyncRunner._worker's failure-surfacing.

The bug these pin: gallery-dl does not raise when the X cookies are expired, so
a sync used to finish with return_code 0 and no way to tell "auth failed" apart
from "nothing new". The worker must now flag ``auth_error`` and report how many
new posts (``last_added``) were actually ingested.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from xlikes_viewer.db import Database
from xlikes_viewer.scanner import ingest_to_db
from xlikes_viewer.sync import SyncRunner


def _fake_downloadjob(*records: tuple[int, str]) -> type:
    """Build a stand-in for gallery_dl.job.DownloadJob that logs to the REAL
    logger gallery-dl's twitter extractor uses (category name = "twitter").
    Job.run() catches AuthorizationError and logs it there; it does not raise.
    """

    class _Job:
        def __init__(self, url: str) -> None:
            self.url = url

        def run(self) -> None:
            logger = logging.getLogger("twitter")
            for level, msg in records:
                logger.log(level, msg)

    return _Job


@pytest.fixture(autouse=True)
def _no_real_gallerydl(monkeypatch: pytest.MonkeyPatch) -> None:
    # prepare_config writes/merges gallery-dl's global config — stub it out.
    monkeypatch.setattr("xlikes_viewer.gallerydl.prepare_config", lambda *a, **k: None)


def _runner_with_username(
    tmp_path: Path, *, library_root: Path | None = None, on_complete=None
) -> SyncRunner:
    db = Database(tmp_path / "x.sqlite")
    db.set_setting("my_username", "me")
    return SyncRunner(
        config_path=tmp_path / "gdl.json",
        db=db,
        library_root=library_root,
        r2_client=None,
        on_complete=on_complete,
    )


@pytest.mark.unit
def test_worker_flags_auth_error_when_cookies_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "gallery_dl.job.DownloadJob",
        _fake_downloadjob(
            (logging.ERROR, "AuthRequired: auth_token or cookies needed to access this likes")
        ),
    )
    runner = _runner_with_username(tmp_path)

    runner._worker()  # run synchronously

    assert runner.state.auth_error is True
    assert runner.state.last_added == 0
    assert runner.state.last_error  # a user-facing message is set
    assert runner.state.running is False


@pytest.mark.unit
def test_worker_clean_run_has_no_auth_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "gallery_dl.job.DownloadJob",
        _fake_downloadjob((logging.INFO, "[twitter] 1002_1.jpg")),
    )
    runner = _runner_with_username(tmp_path)

    runner._worker()

    assert runner.state.auth_error is False
    assert runner.state.last_added == 0
    assert runner.state.last_error is None


@pytest.mark.unit
def test_worker_reports_new_post_count(
    tmp_path: Path, fake_library: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "gallery_dl.job.DownloadJob",
        _fake_downloadjob((logging.INFO, "[twitter] downloaded")),
    )
    runner = _runner_with_username(tmp_path, library_root=fake_library)
    # on_complete mirrors production after_sync: ingest the freshly downloaded
    # sidecars into the DB.
    runner._on_complete = lambda: ingest_to_db(fake_library, runner._db)

    runner._worker()

    assert runner.state.auth_error is False
    assert runner.state.last_added == 4  # fake_library has 4 posts

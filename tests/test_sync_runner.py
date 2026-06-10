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

from favgallery.db import Database
from favgallery.scanner import ingest_to_db
from favgallery.sync import SyncRunner


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
    monkeypatch.setattr("favgallery.gallerydl.prepare_config", lambda *a, **k: None)


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


@pytest.mark.unit
def test_on_new_items_fires_only_when_posts_added(
    tmp_path: Path, fake_library: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """dedup 起動条件の本丸: 新着ゼロなら on_new_items は呼ばれない (Phase 2)。"""
    monkeypatch.setattr(
        "gallery_dl.job.DownloadJob",
        _fake_downloadjob((logging.INFO, "[twitter] downloaded")),
    )
    calls: list[int] = []
    runner = _runner_with_username(tmp_path, library_root=fake_library)
    runner._on_complete = lambda: ingest_to_db(fake_library, runner._db)
    runner._on_new_items = calls.append

    runner._worker()
    assert calls == [4]  # fake_library の 4 posts が ingest された

    # 2 回目: 既に ingest 済み = 新着 0 → 呼ばれない
    runner._worker()
    assert calls == [4]
    assert runner.state.last_added == 0


# --- 自動同期クールダウン (Phase 2B / 2026-06-10) ------------------------------


@pytest.mark.integration
def test_auto_sync_cooldown(fake_library: Path) -> None:
    """auto=1 は前回同期から 10 分以内なら 429、手動は常に通る。"""
    from time import time as now

    from fastapi.testclient import TestClient

    from favgallery.server import create_app

    app = create_app(library_root=fake_library, scan_in_background=False)
    client = TestClient(app)
    (fake_library / "cookies.txt").write_text("# c\n.x.com\tTRUE\t/\tTRUE\t9\tauth_token\tx\n")
    runner = app.state.sync_runner
    runner.state.running = True  # 実 gallery-dl を起動させない (start() は 409 になる)

    # 前回完了が 100 秒前 → auto はクールダウン 429
    runner.state.finished_at = now() - 100
    r = client.post("/api/sync/start?auto=1")
    assert r.status_code == 429
    assert "クールダウン" in r.json()["reason"]

    # 手動 (フラグなし) はクールダウン対象外 → 実行系へ到達 (running なので 409)
    r2 = client.post("/api/sync/start")
    assert r2.status_code == 409

    # 11 分経過後は auto も実行系へ到達 (running なので 409)
    runner.state.finished_at = now() - 660
    r3 = client.post("/api/sync/start?auto=1")
    assert r3.status_code == 409

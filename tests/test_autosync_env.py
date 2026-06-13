"""FAVGALLERY_AUTOSYNC_ON_LOAD: ページロード自動同期の opt-out (セルフホスト配慮)。"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from favgallery.server import create_app


@pytest.fixture
def client(fake_library: Path) -> TestClient:
    return TestClient(create_app(library_root=fake_library, scan_in_background=False))


@pytest.mark.integration
def test_autosync_disabled_short_circuits(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """flag=0 のとき auto=1 同期は即 no-op で返る (cookies チェックより前に短絡)。"""
    monkeypatch.setenv("FAVGALLERY_AUTOSYNC_ON_LOAD", "0")
    r = client.post("/api/sync/start?auto=1")
    assert r.status_code == 200
    body = r.json()
    assert body["started"] is False
    assert body["reason"] == "autosync disabled"


@pytest.mark.integration
def test_autosync_flag_does_not_gate_manual_sync(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """flag=0 でも手動同期 (auto なし) はフラグに阻まれず通常フロー (cookies 未設定→400) に進む。"""
    monkeypatch.setenv("FAVGALLERY_AUTOSYNC_ON_LOAD", "0")
    r = client.post("/api/sync/start")  # auto=False
    assert r.status_code == 400  # fake_library に cookies.txt が無い
    assert "cookies" in r.json()["reason"]


@pytest.mark.integration
def test_autosync_enabled_by_default(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """flag 未設定 (既定 on) のとき auto=1 はフラグで止まらず cookies チェックへ進む。"""
    monkeypatch.delenv("FAVGALLERY_AUTOSYNC_ON_LOAD", raising=False)
    r = client.post("/api/sync/start?auto=1")
    assert r.status_code == 400  # cookies 未設定で 400 (= "autosync disabled" ではない)
    assert r.json()["reason"] != "autosync disabled"

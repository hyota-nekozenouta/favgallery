# Archive → FavGallery リネーム Implementation Plan

Status: COMPLETED (2026-06-10)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** プロダクト名を Archive（パッケージ名 xlikes-viewer / xlikes_viewer）から **FavGallery**（パッケージ名 favgallery）へ全層リネームする。本番（Railway）を壊さないよう、env 変数は新名優先 + 旧名 fallback の後方互換にする。

**Architecture:** ① Python パッケージの物理リネーム（git mv + import 一括置換）→ ② 配布名（pyproject / PyInstaller）→ ③ env 変数の後方互換レイヤー（TDD）→ ④ 画面ブランド表記 → ⑤ ドキュメント、の順で 1 コミットずつ進める。各段で全テスト（pytest 22 ファイル）を回して回帰ゼロを確認する。

**Tech Stack:** Python 3.12 / FastAPI / uv / pytest / PyInstaller

**決定済みの名前（2026-06-10 ひょーたさん決定）:**
| 用途 | 旧 | 新 |
|---|---|---|
| ブランド表記 | Archive | **FavGallery** |
| Python パッケージ | `xlikes_viewer` | `favgallery` |
| 配布名 / CLI / exe | `xlikes-viewer` | `favgallery` |
| env 変数 | `ARCHIVE_USER` / `ARCHIVE_PASSWORD` / `ARCHIVE_LIBRARY_ROOT` | `FAVGALLERY_USER` / `FAVGALLERY_PASSWORD` / `FAVGALLERY_LIBRARY_ROOT`（旧名 fallback 維持） |

**⛔ リネームしないもの（意図的・データ互換とスコープ境界）:**
| 対象 | 理由 |
|---|---|
| `xlikes.sqlite`（DB ファイル名） | 本番 Railway ボリューム上の実データ。リネームすると既存 DB が読めなくなる |
| `xlikes.exe` への参照（`paths.py::default_xlikes_exe` 等） | 別ビルドの同期用バイナリ。本リポジトリの成果物ではない |
| `C:\Users\hyota\Pictures\X-Likes`（ローカル既定パス） | ひょーたさんのローカル実データ位置 |
| `docs/specs/2026-06-06-*.md`（過去 spec） | 歴史的記録。当時の名前のまま保存 |
| `archive.hyota.cloud`（公開 URL） | ドメイン移行はインフラ側タスク（メインHALO / ひょーたさん）。移行完了まで現 URL が事実 |
| `R2_*` / `GALLERY_DL_COOKIES` env | プロダクト名と無関係な命名 |

**スコープ外（メインHALO / インフラ側に引き継ぐ）:**
- favgallery.com / .app / .io のドメイン取得（**3 つとも RDAP 未登録確認済み 2026-06-10**。取得は課金 → ひょーたさん承認）
- Railway 環境変数の `FAVGALLERY_*` への張り替え（コード側は fallback があるのでいつでも安全に実施可）
- Cloudflare DNS / カスタムドメイン切替
- GitHub リポジトリ名 `archive.git` / HALO submodule パス `projects/archive` の変更
- HALO 側ドキュメント（`.company/products/projects/personal/archive.md` / INDEX.md 等）

---

### Task 0: ブランチ作成

**Files:** なし（git 操作のみ）

- [x] **Step 1: feature ブランチを切る**

```bash
cd projects/archive
git checkout -b feat/rename-favgallery
```

- [x] **Step 2: ベースライン確認 — 全テストが green であること**

Run: `uv run pytest`
Expected: 全テスト PASS（既存 suite が green でない場合はリネーム着手前に停止して報告）

### Task 1: Python パッケージ物理リネーム（xlikes_viewer → favgallery）

**Files:**
- Rename: `src/xlikes_viewer/` → `src/favgallery/`（git mv・配下 28 ファイル）
- Modify: `src/favgallery/**/*.py` 全ファイル + `tests/*.py` 全 22 ファイル（import 文・docstring 内の `xlikes_viewer`）
- Modify: `pyproject.toml`（scripts / coverage source / ruff per-file-ignores）
- Modify: `Procfile`（uvicorn モジュールパス）
- Modify: `xlikes-viewer.spec`（datas / Analysis パス）

- [x] **Step 1: ディレクトリを git mv**

```bash
git mv src/xlikes_viewer src/favgallery
```

- [x] **Step 2: `xlikes_viewer` を全ファイルで `favgallery` に一括置換**

```bash
grep -rl "xlikes_viewer" src/favgallery tests pyproject.toml Procfile xlikes-viewer.spec \
  | xargs sed -i 's/xlikes_viewer/favgallery/g'
```

対象は import 文（`from xlikes_viewer.x import y`）、docstring、`Procfile` の `uvicorn xlikes_viewer.server:app`、spec の `src\\xlikes_viewer\\static` パス等。**`xlikes.sqlite` / `xlikes.exe` / `X-Likes` は `xlikes_viewer` にマッチしないため安全**（アンダースコア付きパターンのみ置換するのが本 Task の肝）。

- [x] **Step 3: 置換漏れゼロを確認**

Run: `grep -rn "xlikes_viewer" src tests pyproject.toml Procfile *.spec | grep -v __pycache__`
Expected: 0 件

- [x] **Step 4: 全テスト実行**

Run: `uv run pytest`
Expected: 全 PASS（import エラーが出たら置換漏れ。Step 3 に戻る）

- [x] **Step 5: 古い __pycache__ / egg-info を掃除して再実行（stale バイトコード対策）**

```bash
find src tests -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null
uv sync --reinstall-package xlikes-viewer 2>/dev/null || uv sync
uv run pytest
```

Expected: 全 PASS

- [x] **Step 6: Commit**

```bash
git add -A src/favgallery src/xlikes_viewer tests pyproject.toml Procfile xlikes-viewer.spec
git commit -m "refactor(rename): xlikes_viewer package -> favgallery

Python パッケージ名を新ブランド FavGallery に合わせて一括リネーム。
DB ファイル名 xlikes.sqlite / 同期バイナリ xlikes.exe への参照は
データ互換のため意図的に維持。"
```

### Task 2: 配布名リネーム（xlikes-viewer → favgallery）

**Files:**
- Modify: `pyproject.toml`（`[project] name` / `[project.scripts]`）
- Modify: `src/favgallery/cli.py`（argparse `prog=`）
- Rename: `xlikes-viewer.spec` → `favgallery.spec`（exe `name=` も変更）

- [x] **Step 1: pyproject.toml の配布名を変更**

`pyproject.toml`:
```toml
[project]
name = "favgallery"
```

```toml
[project.scripts]
favgallery = "favgallery.cli:main"
```

（`[tool.coverage.run] source = ["favgallery"]` と ruff per-file-ignores の `"src/favgallery/routers/*.py"` は Task 1 の sed で置換済みのはず。未置換なら合わせて修正）

- [x] **Step 2: cli.py の prog 名を変更**

`src/favgallery/cli.py`:
```python
    p = argparse.ArgumentParser(
        prog="favgallery",
        description="Browse and sync your X (Twitter) liked-media archive.",
    )
```

- [x] **Step 3: PyInstaller spec をリネームして exe 名を変更**

```bash
git mv xlikes-viewer.spec favgallery.spec
```

`favgallery.spec` 内:
```python
exe = EXE(
    ...
    name='favgallery',
    ...
)
```

（datas / Analysis の `src\\favgallery\\...` パスは Task 1 で置換済みのはず。確認のこと）

- [x] **Step 4: lock 再生成 + テスト**

```bash
uv sync          # name 変更で uv.lock が更新される
uv run pytest
```

Expected: 全 PASS。`uv.lock` に `name = "favgallery"` が入る

- [x] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock src/favgallery/cli.py favgallery.spec
git commit -m "refactor(rename): dist/CLI/exe name xlikes-viewer -> favgallery"
```

### Task 3: env 変数の後方互換リネーム（TDD）

**Files:**
- Test: `tests/test_server.py`（新テスト 3 本を Basic auth テスト群の直後に追加）
- Modify: `src/favgallery/server.py`（`_env_first` ヘルパー新設 + 3 箇所の読み替え）

**設計:** `FAVGALLERY_*` を優先で読み、空なら `ARCHIVE_*` に fallback。Railway の env が旧名のままでもデプロイが壊れない（インフラ側はいつでも好きなタイミングで張り替え可能）。既存の `ARCHIVE_*` テストはそのまま残す = fallback の回帰テストになる。

- [x] **Step 1: 失敗するテストを書く**

`tests/test_server.py` の `test_basic_auth_disabled_when_env_vars_absent` の直後に追加:

```python
@pytest.mark.integration
def test_basic_auth_accepts_favgallery_env_vars(
    monkeypatch: pytest.MonkeyPatch, fake_library: Path
) -> None:
    """New FAVGALLERY_* env vars configure Basic auth (rename, 2026-06-10)."""
    monkeypatch.delenv("ARCHIVE_USER", raising=False)
    monkeypatch.delenv("ARCHIVE_PASSWORD", raising=False)
    monkeypatch.setenv("FAVGALLERY_USER", "newuser")
    monkeypatch.setenv("FAVGALLERY_PASSWORD", "newpass")
    app = create_app(library_root=fake_library, scan_in_background=False)
    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/").status_code == 401
    r = client.get("/", headers={"Authorization": _basic_header("newuser", "newpass")})
    assert r.status_code == 200


@pytest.mark.integration
def test_basic_auth_favgallery_env_wins_over_archive_env(
    monkeypatch: pytest.MonkeyPatch, fake_library: Path
) -> None:
    """When both old and new env vars are set, FAVGALLERY_* takes precedence."""
    monkeypatch.setenv("ARCHIVE_USER", "olduser")
    monkeypatch.setenv("ARCHIVE_PASSWORD", "oldpass")
    monkeypatch.setenv("FAVGALLERY_USER", "newuser")
    monkeypatch.setenv("FAVGALLERY_PASSWORD", "newpass")
    app = create_app(library_root=fake_library, scan_in_background=False)
    client = TestClient(app, raise_server_exceptions=False)
    r = client.get("/", headers={"Authorization": _basic_header("newuser", "newpass")})
    assert r.status_code == 200
    r_old = client.get("/", headers={"Authorization": _basic_header("olduser", "oldpass")})
    assert r_old.status_code == 401


@pytest.mark.unit
def test_env_first_returns_first_non_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    from favgallery.server import _env_first

    monkeypatch.delenv("FAVGALLERY_TEST_A", raising=False)
    monkeypatch.setenv("FAVGALLERY_TEST_B", "fallback-value")
    assert _env_first("FAVGALLERY_TEST_A", "FAVGALLERY_TEST_B") == "fallback-value"
    monkeypatch.setenv("FAVGALLERY_TEST_A", "primary-value")
    assert _env_first("FAVGALLERY_TEST_A", "FAVGALLERY_TEST_B") == "primary-value"
    monkeypatch.delenv("FAVGALLERY_TEST_A", raising=False)
    monkeypatch.delenv("FAVGALLERY_TEST_B", raising=False)
    assert _env_first("FAVGALLERY_TEST_A", "FAVGALLERY_TEST_B") == ""
```

- [x] **Step 2: テストが落ちることを確認**

Run: `uv run pytest tests/test_server.py -k "favgallery or env_first" -v`
Expected: FAIL（`ImportError: cannot import name '_env_first'` / 401 にならず 200 等）

- [x] **Step 3: `_env_first` ヘルパーを実装して 3 箇所を読み替え**

`src/favgallery/server.py` — `_make_basic_auth_middleware` の直前にヘルパーを追加:

```python
def _env_first(*names: str) -> str:
    """Return the first non-empty value among the given env var names.

    Rename transition (2026-06-10): new FAVGALLERY_* vars take precedence,
    legacy ARCHIVE_* vars keep working until Railway env is migrated.
    """
    for name in names:
        value = os.environ.get(name, "")
        if value:
            return value
    return ""
```

`_make_basic_auth_middleware` 内（旧 100-101 行）:
```python
        auth_user = _env_first("FAVGALLERY_USER", "ARCHIVE_USER")
        auth_password = _env_first("FAVGALLERY_PASSWORD", "ARCHIVE_PASSWORD")
```
（以降の `archive_user` / `archive_password` ローカル変数参照もすべて `auth_user` / `auth_password` に改名。docstring の「if ARCHIVE_USER/ARCHIVE_PASSWORD are set」は「if FAVGALLERY_USER/FAVGALLERY_PASSWORD (or legacy ARCHIVE_*) are set」に更新）

`_module_level_app`（旧 331 行）:
```python
    library_root_env = _env_first("FAVGALLERY_LIBRARY_ROOT", "ARCHIVE_LIBRARY_ROOT")
```
（直上のモジュールコメント「Library root is resolved from the ARCHIVE_LIBRARY_ROOT env var」も FAVGALLERY_LIBRARY_ROOT 主・ARCHIVE_LIBRARY_ROOT fallback の記述へ更新。server.py:160 付近の「the Railway volume mount at ARCHIVE_LIBRARY_ROOT」コメントも同様）

- [x] **Step 4: テスト一式 green を確認**

Run: `uv run pytest`
Expected: 全 PASS（既存 ARCHIVE_* テスト = fallback 検証もそのまま PASS）

- [x] **Step 5: Commit**

```bash
git add src/favgallery/server.py tests/test_server.py
git commit -m "feat(rename): FAVGALLERY_* env vars with ARCHIVE_* fallback

新 env 名を優先で読み、旧名に fallback する後方互換レイヤー。
Railway 側の env 張り替え前にデプロイしても認証・library root が壊れない。"
```

### Task 4: 画面ブランド表記（Archive → FavGallery）

**Files:**
- Modify: `src/favgallery/static/index.html`（6 行目 title / 189 行目ヘッダー）
- Modify: `src/favgallery/cli.py`（pywebview ウィンドウタイトル）
- Modify: `src/favgallery/server.py`（Basic 認証 realm）

- [x] **Step 1: index.html の 2 箇所**

```html
  <title>FavGallery</title>
```
```html
          <div class="text-lg font-semibold tracking-tight">FavGallery</div>
```

- [x] **Step 2: cli.py のデスクトップウィンドウタイトル**

```python
        webview.create_window(
            "FavGallery",
            url,
            width=1400,
            height=900,
            maximized=True,
        )
```

- [x] **Step 3: server.py の Basic realm**

```python
                headers={"WWW-Authenticate": 'Basic realm="FavGallery"'},
```

- [x] **Step 4: ユーザー向け "Archive" 表記の残存ゼロを確認 + テスト**

Run: `grep -rn '"Archive"\|>Archive<\|realm="Archive"' src/favgallery; uv run pytest`
Expected: grep 0 件 / pytest 全 PASS

- [x] **Step 5: Commit**

```bash
git add src/favgallery/static/index.html src/favgallery/cli.py src/favgallery/server.py
git commit -m "feat(rename): user-facing brand Archive -> FavGallery (title/header/window/realm)"
```

### Task 5: ドキュメント更新（README.md / DEPLOY.md）

**Files:**
- Modify: `README.md`（タイトル・起動コマンド例・公開 URL 注記）
- Modify: `DEPLOY.md`（タイトル・env 変数表・ローカル確認コマンド例）

- [x] **Step 1: README.md**

冒頭を:
```markdown
# FavGallery (favgallery)

X(Twitter) のいいねした画像・動画を保存・閲覧する個人向けメディアアーカイブアプリ。
漫画（画像シーケンス）のリーダー機能も持つ。
旧名: Archive / xlikes-viewer（2026-06-10 リネーム）。
```

開発セクションの起動例を:
```bash
PYTHONPATH=src uv run uvicorn favgallery.server:app --reload  # ローカル起動
```

公開 URL 行を:
```markdown
公開 URL: https://archive.hyota.cloud （新ドメイン favgallery.* へ移行予定・インフラ側タスク）
```

- [x] **Step 2: DEPLOY.md**

タイトル・概要を `FavGallery — Railway + Cloudflare デプロイガイド` / `FavGallery（favgallery・旧 Archive/xlikes-viewer）を…` に変更。

env 変数表（52-54 行）を新名主・旧名注記に:
```markdown
| `FAVGALLERY_USER` | （ユーザー名） | Basic 認証のユーザー名 |
| `FAVGALLERY_PASSWORD` | （パスワード） | Basic 認証のパスワード（強いパスワードを使うこと） |
| `FAVGALLERY_LIBRARY_ROOT` | `/data/library` | メディアライブラリの保存先（ボリューム内） |

> 旧名 `ARCHIVE_USER` / `ARCHIVE_PASSWORD` / `ARCHIVE_LIBRARY_ROOT` も fallback として動作する
> （2026-06-10 リネーム後方互換。新名が設定されていれば新名が優先）。
```

61 行目の注記・148 行目の cookies 説明・174 / 180 行のローカル実行例も `FAVGALLERY_*` 表記へ置換:
```bash
FAVGALLERY_USER=admin FAVGALLERY_PASSWORD=mysecretpassword \
```
```powershell
$env:FAVGALLERY_USER="admin"
```

`Procfile` 言及の `xlikes_viewer.server:app` が文中にあれば `favgallery.server:app` へ。

- [x] **Step 3: Commit**

```bash
git add README.md DEPLOY.md
git commit -m "docs(rename): README/DEPLOY を FavGallery 表記へ（env 新名主・旧名 fallback 注記）"
```

### Task 6: 最終監査 + バージョン

**Files:**
- Modify: `pyproject.toml`（version 0.1.0 → 0.2.0）

- [x] **Step 1: 残存 `xlikes` の全数監査 — 意図的残置のみであること**

Run: `grep -rn "xlikes" src tests pyproject.toml Procfile *.spec README.md DEPLOY.md 2>/dev/null | grep -v __pycache__`
Expected: ヒットは以下カテゴリのみ — ① `xlikes.sqlite`（DB 名）② `xlikes.exe` / `default_xlikes_exe`（同期バイナリ）③ `X-Likes`（ローカルパス）④ 「旧名: xlikes-viewer」等の歴史注記。**import / パッケージ参照 / 配布名としての残存は 0 件**

- [x] **Step 2: lint + 全テスト**

Run: `uv run ruff check src tests && uv run pytest`
Expected: ruff clean / 全 PASS

- [x] **Step 3: version bump（リネーム = 互換性に触る minor）**

`pyproject.toml`:
```toml
version = "0.2.0"
```

- [x] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "chore(version): 0.2.0 — Archive -> FavGallery リネーム一式"
```

- [x] **Step 5: main へマージ（push はひょーたさん / メインHALO の deploy 判断と合わせる）**

```bash
git checkout main
git merge --no-ff feat/rename-favgallery -m "merge: Archive -> FavGallery rename (v0.2.0)"
```

push（= Railway 自動デプロイ発火の可能性）は、env fallback により安全だが、本番に出る変更なので報告とセットで実施判断。

---

## 2026-06-11 追補: スコープ外として残していた統一の消化

ひょーたさん GO（「全部やっていいよ」）により、開発HALO #2 が以下を実施:

- [x] GitHub リポ rename: `hyota-nekozenouta/archive` → `hyota-nekozenouta/favgallery`（gh repo rename・旧 URL は GitHub が自動リダイレクト）
- [x] HALO submodule パス rename: `projects/archive` → `projects/favgallery`（.gitmodules path/url 更新 + module config の worktree/origin 配線修正。`.git/modules/projects/archive` の内部名は git の流儀に従い据え置き）
- [x] HALO `.gitignore` のパス追従
- [x] README / DEPLOY.md の旧パス・旧 URL 記述を実態へ更新（Root Directory はリポルートが正・「HALO/projects/archive を Root Directory に」は旧構成の残骸だった）
- [ ] Cloudflare `archive` CNAME 削除（API token 不在のためひょーたさんへ依頼）
- [x] HALO 側ドキュメント（products spec）の FavGallery 化 — `favgallery.md` を v0.6.0 + M5/M6 へ更新済み（2026-06-14 開発HALO #3）。INDEX はメインHALO session-end が自動再生成

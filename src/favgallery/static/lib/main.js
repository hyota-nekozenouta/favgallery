// エントリポイント: タブ切替 / 同期ポーリング / マイいいね / 起動処理 / グローバル UI。
// 機能本体は各モジュールに分離済み (Phase 4B)。index.html の import map が
// bare specifier を ?v=__ASSET_VERSION__ 付き URL へ解決する。
import { state } from 'state';
import { $, $$, escapeHtml } from 'dom';
import { showNotice, notifyAuthFailure } from 'notices';
import { closeReader } from 'reader';
import { fetchBooks, loadBookTags } from 'bookshelf';
import { fetchPosts, loadMoreUnliked, enterUnlikedMode, exitUnlikedMode } from 'posts';
import { loadLibrary, loadLists, loadFavoriteAuthors, renderFilterChips } from 'library';
import { loadLastSeen, setupSeenObserver } from 'timeline';

// --- Loading indicator (hourglass) --------------------------------
let _syncActive = false, _dedupActive = false, _visualDedupActive = false;
let _hourglassTimer = null, _hourglassFlip = false;
function updateLoadingState() {
  const active = _syncActive || _dedupActive || _visualDedupActive;
  const el = $('#loadingIndicator');
  if (!el) return;
  if (active && !_hourglassTimer) {
    el.classList.remove('hidden');
    _hourglassTimer = setInterval(() => {
      _hourglassFlip = !_hourglassFlip;
      el.textContent = _hourglassFlip ? '⌛' : '⏳';
    }, 600);
  } else if (!active && _hourglassTimer) {
    clearInterval(_hourglassTimer);
    _hourglassTimer = null;
    _hourglassFlip = false;
    el.textContent = '⏳';
    el.classList.add('hidden');
  }
}

// --- Tabs ----------------------------------------------------------
function switchTab(tab) {
  if (state.tab === tab) return;
  state.tab = tab;
  $('#tabLikes').classList.toggle('active', tab === 'likes');
  $('#tabTimeline').classList.toggle('active', tab === 'timeline');
  $('#tabBookshelf').classList.toggle('active', tab === 'bookshelf');
  $('#timelineRefreshBtn').classList.toggle('hidden', tab !== 'timeline');
  $('#markSeenBtn').classList.toggle('hidden', tab !== 'timeline');
  // Toggle sidebar sections
  $('#sidebarPosts').classList.toggle('hidden', tab === 'bookshelf');
  $('#sidebarBooks').classList.toggle('hidden', tab !== 'bookshelf');
  $('#sidebarPostsHeader').classList.toggle('hidden', tab === 'bookshelf');
  exitUnlikedMode();
  state.offset = 0; state.posts = []; state.dividerInserted = false;
  setupSeenObserver();          // turn observer on for timeline, off otherwise
  if (tab === 'bookshelf') {
    $('#masonry').innerHTML = '';
    fetchBooks();
    loadBookTags();
  } else {
    fetchPosts();
  }
}
$('#tabLikes').addEventListener('click', () => switchTab('likes'));
$('#tabTimeline').addEventListener('click', () => switchTab('timeline'));
$('#tabBookshelf').addEventListener('click', () => switchTab('bookshelf'));
// 本棚サイドバーのバインディングは bookshelf.js へ分離 (Phase 4B)

function updateReelHeight() {
  const h = document.getElementById('grid').clientHeight;
  document.documentElement.style.setProperty('--reel-height', h + 'px');
}

function setLayout(mode) {
  state.layout = mode;
  $('#grid').classList.toggle('reel-mode', mode === 'reel');
  $('#layoutToggle').textContent = mode === 'reel' ? '▤' : '▦';
  if (mode === 'reel') updateReelHeight();
  state.offset = 0; state.posts = [];
  fetchPosts();
}

window.addEventListener('resize', () => {
  if (state.layout === 'reel') updateReelHeight();
});
$('#layoutToggle').addEventListener('click', () => {
  setLayout(state.layout === 'masonry' ? 'reel' : 'masonry');
});

// --- Search / media-type chips -------------------------------------
let searchTimer = null;
$('#searchBox').addEventListener('input', (e) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    state.filter.q = e.target.value.trim();
    state.offset = 0; state.posts = [];
    renderFilterChips(); fetchPosts();
  }, 200);
});
$$('.mt-btn').forEach(b => b.addEventListener('click', () => {
  $$('.mt-btn').forEach(x => x.classList.remove('active'));
  b.classList.add('active');
  state.filter.media_type = b.dataset.mt;
  state.offset = 0; state.posts = [];
  renderFilterChips(); fetchPosts();
}));

// --- Refresh / Sync (likes archive) -------------------------------

async function pollSync() {
  const r = await fetch('/api/sync/status');
  const s = await r.json();
  if (s.running) {
    state.syncRunning = true;
    _syncActive = true; updateLoadingState();
    setTimeout(pollSync, 2000);
  } else if (state.syncRunning) {
    state.syncRunning = false;
    _syncActive = false; updateLoadingState();
    // Surface the outcome so a silent cookie-expiry failure becomes visible,
    // and so newly-synced likes actually appear without a manual reload.
    if (s.auth_error) {
      // verify で裏取りしてから警告（固定文言・backend の生エラーは出さない）
      notifyAuthFailure();
    } else if (s.last_added > 0) {
      showNotice(`新着 ${s.last_added} 件を取り込みました`, { kind: 'info' });
      await loadLibrary();
      if (state.tab === 'likes') { state.offset = 0; state.posts = []; await fetchPosts(); }
    } else {
      showNotice('同期完了：新着なし', { kind: 'info' });
    }
    // dedup の起動はサーバー側 _after_sync が「新着があった時だけ」行う
    // (2026-06-10 Phase 2 — フロントからの毎回 POST を廃止)。ここは進捗
    // ポーリングのみ: 未起動なら status が即 running:false で砂時計も消える。
    pollDedup({ refreshAfter: true });
    pollVisualDedup({ refreshAfter: true });
  } else {
    _syncActive = false; updateLoadingState();
  }
}

async function pollDedup({ refreshAfter = false } = {}) {
  const r = await fetch('/api/dedup/status');
  const s = await r.json();
  if (s.running) {
    _dedupActive = true; updateLoadingState();
    setTimeout(() => pollDedup({ refreshAfter }), 1500);
    return;
  }
  _dedupActive = false; updateLoadingState();
  if (s.last_error) { console.warn('dedup error:', s.last_error); return; }
  if (refreshAfter && s.duplicates_deleted > 0) {
    await fetch('/api/library/refresh', { method: 'POST' });
    await loadLibrary();
    if (state.tab === 'likes') {
      state.offset = 0; state.posts = [];
      await fetchPosts();
    }
  }
}

async function pollVisualDedup({ refreshAfter = false } = {}) {
  const r = await fetch('/api/dedup/visual/status');
  const s = await r.json();
  if (s.running) {
    _visualDedupActive = true; updateLoadingState();
    setTimeout(() => pollVisualDedup({ refreshAfter }), 1500);
    return;
  }
  _visualDedupActive = false; updateLoadingState();
  if (s.last_error) { console.warn('visual dedup error:', s.last_error); return; }
  if (refreshAfter && s.duplicates_deleted > 0) {
    await fetch('/api/library/refresh', { method: 'POST' });
    await loadLibrary();
    if (state.tab === 'likes') {
      state.offset = 0; state.posts = [];
      await fetchPosts();
    }
  }
}


// --- モバイルサイドバー制御 ----------------------------------------
(function() {
  const sidebar   = document.getElementById('app-sidebar');
  const overlay   = document.getElementById('sidebar-overlay');
  const hamburger = document.getElementById('hamburger-btn');
  function openSidebar()  { sidebar.classList.add('open');    overlay.classList.add('active'); }
  function closeSidebar() { sidebar.classList.remove('open'); overlay.classList.remove('active'); }
  hamburger.addEventListener('click', () =>
    sidebar.classList.contains('open') ? closeSidebar() : openSidebar()
  );
  overlay.addEventListener('click', closeSidebar);
  sidebar.addEventListener('click', (e) => {
    if (window.innerWidth < 768 && e.target.closest('[data-author], [data-tag], .list-btn'))
      closeSidebar();
  });
})();

// --- My-likes cache --------------------------------------------------
async function loadMe() {
  try {
    const r = await fetch('/api/me');
    const d = await r.json();
    if (d.username) $('#meUsername').value = d.username;
    renderMeStatus(d);
  } catch { /* ignore */ }
}

function renderMeStatus(s) {
  const lines = [];
  if (s.username) lines.push(`@${escapeHtml(s.username)}`);
  if (typeof s.count === 'number' || typeof s.my_likes_count === 'number') {
    const n = (s.count ?? s.my_likes_count) || 0;
    lines.push(`${n.toLocaleString()} 件キャッシュ`);
  } else if (typeof s.my_likes_count === 'number') {
    lines.push(`${s.my_likes_count.toLocaleString()} 件キャッシュ`);
  }
  if (s.running) lines.push('⏳ 同期中…');
  else if (s.last_error) lines.push(`✗ ${escapeHtml(s.last_error)}`);
  else if (s.last_added) lines.push(`✓ +${s.last_added} 件追加`);
  $('#meStatus').textContent = lines.join(' / ');
}

$('#meSaveBtn').addEventListener('click', async () => {
  const name = $('#meUsername').value.trim().replace(/^@/, '');
  try {
    const r = await fetch('/api/me', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ username: name }),
    });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      $('#meStatus').textContent = `✗ ${d.detail || 'failed'}`;
      return;
    }
    await loadMe();
  } catch (e) { $('#meStatus').textContent = '✗ ' + e.message; }
});

let meSyncPoll = null;
$('#meSyncBtn').addEventListener('click', async () => {
  // Save the username field first so the user doesn't have to click two
  // buttons in a row.
  const name = $('#meUsername').value.trim().replace(/^@/, '');
  if (name) {
    await fetch('/api/me', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ username: name }),
    });
  }
  try {
    const r = await fetch('/api/me/likes/sync?range=1-1000', { method: 'POST' });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      $('#meStatus').textContent = `✗ ${d.detail || d.reason || 'failed to start'}`;
      return;
    }
    if (meSyncPoll) clearInterval(meSyncPoll);
    meSyncPoll = setInterval(pollMeSyncStatus, 2000);
    pollMeSyncStatus();
  } catch (e) { $('#meStatus').textContent = '✗ ' + e.message; }
});

async function pollMeSyncStatus() {
  try {
    const r = await fetch('/api/me/likes/status');
    const s = await r.json();
    renderMeStatus(s);
    if (!s.running) {
      if (meSyncPoll) { clearInterval(meSyncPoll); meSyncPoll = null; }
      // If unliked view is up, refresh it so newly-cached likes vanish.
      if (state.unliked.active) {
        const author = state.unliked.author;
        exitUnlikedMode();
        enterUnlikedMode(author);
      }
    }
  } catch { /* ignore */ }
}

// X cookies 管理 (loadCookieStatus / openCookieModal) は cookies.js へ分離 (Phase 4B)

// --- Infinite scroll ----------------------------------------------
const sentinel = $('#sentinel');
const io = new IntersectionObserver((entries) => {
  if (!entries[0].isIntersecting) return;
  if (state.tab === 'bookshelf') return;
  if (state.unliked.active) {
    loadMoreUnliked();
    return;
  }
  if (state.posts.length < state.total) fetchPosts();
});
io.observe(sentinel);

// --- Init ----------------------------------------------------------
(async () => {
  await Promise.all([loadLibrary(), loadLists(), loadLastSeen(), loadMe(), loadFavoriteAuthors()]);
  setupSeenObserver();
  await fetchPosts();
  // Auto-sync on page load — runs in background, does not block UI.
  // ?auto=1: サーバー側 10 分クールダウン対象 (Phase 2B)。429 = クールダウン中
  // は正常系なので無言スキップ (409 = 実行中も同様)。
  try {
    const syncRes = await fetch('/api/sync/start?auto=1', { method: 'POST' });
    if (!syncRes.ok && syncRes.status !== 409 && syncRes.status !== 429) {
      const d = await syncRes.json().catch(() => ({}));
      const reason = d.reason || `HTTP ${syncRes.status}`;
      showNotice('同期エラー: ' + reason, { kind: 'error', sticky: true });
    }
  } catch { /* network error during sync start — ignore */ }
  await pollSync();
})();

// 手動同期 (Phase 2B): クールダウン導入で「リロード = 強制同期」が消えるため、
// いつでも即時同期できる入口をヘッダーに常設。
document.getElementById('manualSyncBtn')?.addEventListener('click', async () => {
  try {
    const r = await fetch('/api/sync/start', { method: 'POST' });
    if (r.ok) {
      showNotice('同期を開始しました', { kind: 'info' });
      pollSync();
    } else if (r.status === 409) {
      showNotice('同期は既に実行中です', { kind: 'info' });
      pollSync();
    } else {
      const d = await r.json().catch(() => ({}));
      showNotice('同期エラー: ' + (d.reason || `HTTP ${r.status}`), { kind: 'error', sticky: true });
    }
  } catch (e) {
    showNotice('同期エラー: ' + e.message, { kind: 'error' });
  }
});

// --- Phase 5 スマホ/キーボード操作性 -------------------------------------
// Esc は「最前面のもの」から 1 つずつ閉じる (lightbox は既存ハンドラが処理)
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  const cookie = document.getElementById('cookieModal');
  if (cookie) { cookie.remove(); return; }
  const upload = document.getElementById('bookUploadModal');
  if (upload) { upload.remove(); return; }
  const reader = document.getElementById('readerModal');
  if (reader && !reader.classList.contains('hidden')) { closeReader(); return; }
  const lp = document.getElementById('listPopover');
  if (lp && !lp.classList.contains('hidden')) { lp.classList.add('hidden'); return; }
  const op = document.getElementById('optionsPopover');
  if (op && !op.classList.contains('hidden')) { op.classList.add('hidden'); }
});

// モーダル/サイドバー/リーダー/ライトボックス表示中は背面スクロールをロック。
// 呼び出し箇所に依存しないよう MutationObserver で物理監視 (open/close 漏れゼロ)
function syncScrollLock() {
  const locked = !!(document.getElementById('cookieModal')
    || document.getElementById('bookUploadModal')
    || (document.getElementById('readerModal') && !document.getElementById('readerModal').classList.contains('hidden'))
    || (document.getElementById('lightbox') && !document.getElementById('lightbox').classList.contains('hidden'))
    || (document.getElementById('app-sidebar') && document.getElementById('app-sidebar').classList.contains('open')));
  document.body.classList.toggle('scroll-locked', locked);
}
const _lockWatch = new MutationObserver(syncScrollLock);
_lockWatch.observe(document.body, { childList: true });
for (const id of ['app-sidebar', 'readerModal', 'lightbox']) {
  const n = document.getElementById(id);
  if (n) _lockWatch.observe(n, { attributes: true, attributeFilter: ['class'] });
}

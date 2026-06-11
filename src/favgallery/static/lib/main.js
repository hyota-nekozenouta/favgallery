// エントリポイント: タブ切替 / 起動処理 / グローバル UI (サイドバー・無限スクロール・
// Esc・スクロールロック)。機能本体は各モジュールに分離済み (Phase 4B)。
// index.html の import map が bare specifier を ?v= 資産ハッシュ付き URL へ解決する。
import { state } from 'state';
import { $, $$ } from 'dom';
import { showNotice } from 'notices';
import { closeReader } from 'reader';
import { fetchBooks, loadBookTags } from 'bookshelf';
import { fetchPosts, loadMoreUnliked, exitUnlikedMode } from 'posts';
import { loadLibrary, loadLists, loadFavoriteAuthors, renderFilterChips } from 'library';
import { loadLastSeen, setupSeenObserver } from 'timeline';
import { pollSync } from 'sync';
import { loadMe } from 'mylikes';

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

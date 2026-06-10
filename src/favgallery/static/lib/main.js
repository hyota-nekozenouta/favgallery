const state = {
  tab: 'likes',                      // 'likes' | 'timeline'
  layout: 'masonry',                 // 'masonry' | 'reel'
  authors: [],
  tags: [],
  lists: [],                          // [{id, name, count}]
  filter: { author: null, tag: null, media_type: '', q: '', list_id: null },
  hideLiked: true,
  posts: [],
  total: 0,
  offset: 0,
  limit: 60,
  loading: false,
  syncRunning: false,
  lastSeenTimeline: '',               // tweet_id; '' = no marker yet
  dividerInserted: false,             // current page's divider rendered?
  lb: { items: [], pos: 0 },          // lightbox group: posts sharing tweet_id
  authorSummary: null,                // cached { author, nick, counts } for the current filter
  unliked: { active: false, author: null, items: [], offset: 0, limit: 60, hasMore: false, loading: false, loadingMore: false, error: '' },
  favoriteAuthors: new Set(),
};

const $ = (sel) => document.querySelector(sel);

// --- Video thumbnail capture (Canvas API) -------------------------
const videoCapObserver = new IntersectionObserver((entries) => {
  entries.forEach(e => {
    if (!e.isIntersecting) return;
    const img = e.target;
    if (img.dataset.vcap) return;
    img.dataset.vcap = '1';
    videoCapObserver.unobserve(img);
    _captureVideoFrame(img.dataset.videoSrc, img);
  });
}, { rootMargin: '400px' });

// --- Bookshelf cover lazy-load (start loading ~300px before viewport) ---
const bookCoverObserver = new IntersectionObserver((entries) => {
  entries.forEach(e => {
    if (!e.isIntersecting) return;
    const img = e.target;
    bookCoverObserver.unobserve(img);
    if (img.dataset.cover) { img.src = img.dataset.cover; delete img.dataset.cover; }
  });
}, { rootMargin: '300px' });

function _captureVideoFrame(src, imgEl) {
  const v = document.createElement('video');
  v.muted = true;
  v.preload = 'metadata';
  v.addEventListener('loadeddata', () => { v.currentTime = 0; }, { once: true });
  v.addEventListener('seeked', () => {
    try {
      const c = document.createElement('canvas');
      c.width = v.videoWidth || 640;
      c.height = v.videoHeight || 360;
      c.getContext('2d').drawImage(v, 0, 0);
      imgEl.src = c.toDataURL('image/jpeg', 0.85);
    } catch { /* keep blank */ }
    v.removeAttribute('src');
    v.load();
  }, { once: true });
  v.src = src;
}

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
const $$ = (sel) => Array.from(document.querySelectorAll(sel));
const escapeHtml = (s) => (s ?? '').toString()
  .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
  .replace(/"/g,'&quot;').replace(/'/g,'&#39;');

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

// Bookshelf sidebar event listeners
$('#sidebarAddBookBtn').addEventListener('click', () => openBookUpload());
$('#bookFavFilter').addEventListener('click', () => {
  if (bookFilter.type === 'favorite') {
    bookFilter = { type: null, tag: null };
  } else {
    bookFilter = { type: 'favorite', tag: null };
  }
  renderBooks();
  loadBookTags();
  $('#bookFavFilter').classList.toggle('bg-zinc-800', bookFilter.type === 'favorite');
});

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

// --- Library / Authors / Tags -------------------------------------
async function loadLibrary() {
  let data;
  try {
    const r = await fetch('/api/library');
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    data = await r.json();
  } catch (e) {
    showNotice('ライブラリ情報の読み込みに失敗しました', { kind: 'error' });
    return;
  }
  state.authors = data.authors;
  state.tags = data.tags;
  const label = data.scanning ? `📚 スキャン中… ${data.post_count.toLocaleString()}` : `${data.post_count.toLocaleString()} posts`;
  $('#postCount').textContent = label;
  renderAuthors(); renderTags();
  // If the initial library scan is still running, poll until done and then
  // refresh the grid so the user sees the full archive without hitting reload.
  if (data.scanning) {
    setTimeout(async () => {
      await loadLibrary();
      if (state.tab !== 'bookshelf') {
        state.offset = 0; state.posts = [];
        await fetchPosts();
      }
    }, 2000);
  }
}

function authorRowHtml(a) {
  const isActive = state.filter.author === a.name;
  const isFav = state.favoriteAuthors.has(a.name);
  return `<div class="flex items-center gap-0.5">
    <button class="author-btn flex-1 min-w-0 text-left px-2 py-1.5 rounded text-sm flex items-center justify-between ${isActive ? 'bg-indigo-600/20 text-indigo-300' : 'hover:bg-zinc-800 text-zinc-300'}"
            data-author="${escapeHtml(a.name)}">
      <span class="truncate min-w-0">
        ${isFav ? '<span class="text-yellow-400 text-xs mr-0.5">★</span>' : ''}
        <span class="font-medium">${escapeHtml(a.nick || a.name)}</span>
        <span class="text-zinc-500 text-xs ml-1">@${escapeHtml(a.name)}</span>
      </span>
      <span class="text-xs text-zinc-500 ml-2 shrink-0">${a.post_count}</span>
    </button>
    <button class="fav-btn shrink-0 text-sm px-1 py-1 rounded hover:bg-zinc-800 ${isFav ? 'text-yellow-400' : 'text-zinc-600 hover:text-yellow-400'}"
            data-author="${escapeHtml(a.name)}" title="${isFav ? 'お気に入り解除' : 'お気に入り登録'}">★</button>
  </div>`;
}

function bindAuthorEvents(container) {
  container.querySelectorAll('.author-btn').forEach(b => b.addEventListener('click', () => {
    const name = b.dataset.author;
    state.filter.author = state.filter.author === name ? null : name;
    state.offset = 0; state.posts = [];
    renderAuthors(); renderFilterChips(); fetchPosts();
  }));
  container.querySelectorAll('.fav-btn').forEach(b => b.addEventListener('click', (e) => {
    e.stopPropagation();
    const name = b.dataset.author;
    if (state.favoriteAuthors.has(name)) {
      state.favoriteAuthors.delete(name);
    } else {
      state.favoriteAuthors.add(name);
    }
    fetch('/api/favorite-authors', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ authors: [...state.favoriteAuthors] }),
    });
    renderAuthors();
  }));
}

function renderAuthors() {
  const favSet = state.favoriteAuthors;
  const multi = state.authors.filter(a => a.post_count > 1);
  const single = state.authors.filter(a => a.post_count === 1);
  const sortGroup = (arr) => [
    ...arr.filter(a => favSet.has(a.name)),
    ...arr.filter(a => !favSet.has(a.name)),
  ];
  $('#authorTotal').textContent = `${state.authors.length} 人`;

  const mainEl = $('#authorList');
  mainEl.innerHTML = sortGroup(multi).map(authorRowHtml).join('');
  bindAuthorEvents(mainEl);

  const singleSection = $('#authorSingleSection');
  const singleList = $('#authorSingleList');
  if (single.length > 0) {
    singleSection.classList.remove('hidden');
    const btn = $('#authorSingleToggle');
    btn.querySelector('span').textContent = btn.dataset.open === '1' ? '▼' : '▶';
    singleList.innerHTML = sortGroup(single).map(authorRowHtml).join('');
    bindAuthorEvents(singleList);
  } else {
    singleSection.classList.add('hidden');
  }
}

function renderTags() {
  $('#tagList').innerHTML = state.tags.slice(0, 80).map(t => {
    const isActive = state.filter.tag === t.name;
    return `<button class="tag-btn chip rounded px-2 py-1 text-xs ${isActive ? 'active' : ''}" data-tag="${escapeHtml(t.name)}">
              #${escapeHtml(t.name)} <span class="text-zinc-500 ml-1">${t.count}</span>
            </button>`;
  }).join('');
  $$('.tag-btn').forEach(b => b.addEventListener('click', () => {
    const t = b.dataset.tag;
    state.filter.tag = state.filter.tag === t ? null : t;
    state.offset = 0; state.posts = [];
    renderTags(); renderFilterChips(); fetchPosts();
  }));
}

// --- Lists ---------------------------------------------------------
async function loadLists() {
  const r = await fetch('/api/lists');
  state.lists = await r.json();
  renderListSidebar();
}

function renderListSidebar() {
  if (!state.lists.length) {
    $('#listSidebar').innerHTML = '<div class="text-xs text-zinc-600 px-1 py-1">まだ無し</div>';
    return;
  }
  $('#listSidebar').innerHTML = state.lists.map(l => {
    const active = state.filter.list_id === l.id;
    return `<div class="flex items-center gap-1">
      <button class="list-btn flex-1 text-left px-2 py-1.5 rounded text-sm flex items-center justify-between ${active ? 'bg-indigo-600/20 text-indigo-300' : 'hover:bg-zinc-800 text-zinc-300'}"
              data-list-id="${l.id}">
        <span class="truncate">${escapeHtml(l.name)}</span>
        <span class="text-xs text-zinc-500 ml-2">${l.count}</span>
      </button>
      <button class="list-del text-zinc-500 hover:text-rose-400 px-1" title="削除" data-list-id="${l.id}">×</button>
    </div>`;
  }).join('');
  $$('.list-btn').forEach(b => b.addEventListener('click', () => {
    const id = parseInt(b.dataset.listId, 10);
    state.filter.list_id = state.filter.list_id === id ? null : id;
    state.offset = 0; state.posts = [];
    renderListSidebar(); renderFilterChips(); fetchPosts();
  }));
  $$('.list-del').forEach(b => b.addEventListener('click', async (e) => {
    e.stopPropagation();
    const id = parseInt(b.dataset.listId, 10);
    const l = state.lists.find(x => x.id === id);
    if (!l) return;
    if (!confirm(`リスト「${l.name}」を削除しますか？`)) return;
    await fetch(`/api/lists/${id}`, { method: 'DELETE' });
    if (state.filter.list_id === id) {
      state.filter.list_id = null;
      state.offset = 0; state.posts = [];
    }
    await loadLists();
    renderFilterChips();
    fetchPosts();
  }));
}

$('#newListBtn').addEventListener('click', async () => {
  const name = prompt('リスト名:');
  if (!name) return;
  const r = await fetch('/api/lists', {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ name }),
  });
  if (!r.ok) { alert('作成失敗 (同名のリストが既にある可能性)'); return; }
  await loadLists();
});

// --- Filter chips --------------------------------------------------
function renderFilterChips() {
  const f = state.filter;
  const chips = [];
  if (f.list_id) {
    const l = state.lists.find(x => x.id === f.list_id);
    if (l) chips.push(`<span class="chip active rounded px-3 py-1 text-sm cursor-pointer" data-clear="list_id">📋 ${escapeHtml(l.name)} ✕</span>`);
  }
  if (f.author) chips.push(`<span class="chip active rounded px-3 py-1 text-sm cursor-pointer" data-clear="author">@${escapeHtml(f.author)} ✕</span>`);
  if (f.tag)    chips.push(`<span class="chip active rounded px-3 py-1 text-sm cursor-pointer" data-clear="tag">#${escapeHtml(f.tag)} ✕</span>`);
  if (f.media_type) chips.push(`<span class="chip active rounded px-3 py-1 text-sm cursor-pointer" data-clear="media_type">${escapeHtml(f.media_type)} ✕</span>`);
  if (f.q)      chips.push(`<span class="chip active rounded px-3 py-1 text-sm cursor-pointer" data-clear="q">"${escapeHtml(f.q)}" ✕</span>`);
  $('#filterChips').innerHTML = chips.join('');
  $$('#filterChips [data-clear]').forEach(el => el.addEventListener('click', () => {
    const k = el.dataset.clear;
    state.filter[k] = (k === 'media_type') ? '' : null;
    if (k === 'q') { $('#searchBox').value = ''; state.filter.q = ''; }
    state.offset = 0; state.posts = [];
    renderAuthors(); renderTags(); renderListSidebar(); renderFilterChips(); fetchPosts();
  }));
}

// Author page header: show name + media-type tabs whenever an author filter is
// active on the likes tab. Tabs are toggle-style buttons that drive media_type.
async function renderAuthorHeader() {
  const author = state.filter.author;
  const header = $('#authorHeader');
  if (!author || state.tab !== 'likes') {
    header.classList.add('hidden');
    state.authorSummary = null;
    return;
  }
  if (!state.authorSummary || state.authorSummary.author !== author) {
    // Optimistic placeholder so the header doesn't flicker between authors.
    state.authorSummary = { author, nick: '', counts: { total: 0 } };
    try {
      const r = await fetch(`/api/authors/${encodeURIComponent(author)}/summary`);
      if (r.ok) {
        const data = await r.json();
        if (state.filter.author === author) state.authorSummary = data;
      }
    } catch { /* keep placeholder */ }
  }
  const sum = state.authorSummary;
  $('#authorHeaderName').textContent = sum.nick || author;
  $('#authorHeaderHandle').textContent = `@${author}`;
  const c = sum.counts || {};
  const tabs = [{ kind: 'mt', mt: '', label: 'すべて', n: c.total ?? 0 }];
  if ((c.photo ?? 0) > 0) tabs.push({ kind: 'mt', mt: 'photo', label: '写真', n: c.photo });
  if ((c.video ?? 0) > 0) tabs.push({ kind: 'mt', mt: 'video', label: '動画', n: c.video });
  if ((c.animated_gif ?? 0) > 0) tabs.push({ kind: 'mt', mt: 'animated_gif', label: 'GIF', n: c.animated_gif });
  // The "未いいね" tab is special: it switches to a remote-fetch view.
  const unlikedActive = state.unliked.active && state.unliked.author === author;
  const unlikedCount = unlikedActive ? state.unliked.items.length : null;
  tabs.push({ kind: 'unliked', label: '未いいね', n: unlikedCount });
  $('#authorMediaTabs').innerHTML = tabs.map(t => {
    const active = t.kind === 'unliked'
      ? unlikedActive
      : (!unlikedActive && (state.filter.media_type || '') === t.mt);
    const data = t.kind === 'unliked' ? `data-unliked="1"` : `data-mt="${escapeHtml(t.mt)}"`;
    const count = t.n == null ? '' : ` <span class="ml-1 text-zinc-500">${t.n.toLocaleString()}</span>`;
    return `<button class="author-mt-btn tab-btn ${active ? 'active' : ''}" ${data}>${escapeHtml(t.label)}${count}</button>`;
  }).join('');
  $$('#authorMediaTabs .author-mt-btn').forEach(b => b.addEventListener('click', () => {
    if (b.dataset.unliked) {
      enterUnlikedMode(author);
      return;
    }
    const mt = b.dataset.mt || '';
    const wasUnliked = state.unliked.active;
    if (!wasUnliked && (state.filter.media_type || '') === mt) return;
    exitUnlikedMode();
    state.filter.media_type = mt;
    state.offset = 0; state.posts = [];
    // Keep sidebar media-type chips in sync.
    $$('.mt-btn').forEach(x => x.classList.toggle('active', (x.dataset.mt || '') === mt));
    renderFilterChips();
    renderAuthorHeader();
    fetchPosts();
  }));
  header.classList.remove('hidden');
}

// --- Unliked-author mode -------------------------------------------
const UNLIKED_PAGE_SIZE = 60;

async function enterUnlikedMode(author) {
  if (state.unliked.active && state.unliked.author === author) return;
  state.unliked = {
    active: true, author, items: [],
    offset: 0, limit: UNLIKED_PAGE_SIZE,
    hasMore: false, loading: true, loadingMore: false, error: '',
  };
  // Replace grid contents with a loading placeholder.
  $('#masonry').innerHTML = `<div class="text-zinc-400 text-sm p-6">⏳ X から @${escapeHtml(author)} の投稿を取得中… (10〜20秒かかることがあります)</div>`;
  $('#resultCount').textContent = '取得中…';
  renderAuthorHeader();
  try {
    const data = await fetchUnlikedPage(author, 0);
    if (!state.unliked.active || state.unliked.author !== author) return;
    state.unliked.items = data.items || [];
    state.unliked.offset = data.offset + data.limit;
    state.unliked.hasMore = !!data.has_more;
    state.unliked.loading = false;
    renderUnlikedGrid();
    renderAuthorHeader();
  } catch (err) {
    if (!state.unliked.active || state.unliked.author !== author) return;
    state.unliked.loading = false;
    state.unliked.error = err.message || String(err);
    $('#masonry').innerHTML = `<div class="text-red-400 text-sm p-6">取得失敗: ${escapeHtml(state.unliked.error)}</div>`;
    $('#resultCount').textContent = '';
  }
}

async function loadMoreUnliked() {
  const u = state.unliked;
  if (!u.active || u.loading || u.loadingMore || !u.hasMore) return;
  u.loadingMore = true;
  updateUnlikedCountLabel();
  const author = u.author;
  try {
    const data = await fetchUnlikedPage(author, u.offset);
    if (!state.unliked.active || state.unliked.author !== author) return;
    const newItems = data.items || [];
    const before = state.unliked.items.length;
    state.unliked.items.push(...newItems);
    state.unliked.offset = data.offset + data.limit;
    state.unliked.hasMore = !!data.has_more;
    appendUnlikedTiles(newItems, before);
  } catch {
    // soft fail; user can retry by scrolling further
  } finally {
    state.unliked.loadingMore = false;
    updateUnlikedCountLabel();
  }
}

async function fetchUnlikedPage(author, offset) {
  const r = await fetch(
    `/api/authors/${encodeURIComponent(author)}/unliked?limit=${UNLIKED_PAGE_SIZE}&offset=${offset}`,
  );
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

function exitUnlikedMode() {
  if (!state.unliked.active) return;
  state.unliked = {
    active: false, author: null, items: [],
    offset: 0, limit: UNLIKED_PAGE_SIZE,
    hasMore: false, loading: false, loadingMore: false, error: '',
  };
}

function updateUnlikedCountLabel() {
  const u = state.unliked;
  const suffix = u.loadingMore ? '・読み込み中…' : (u.hasMore ? '・スクロールで続き' : '・全件表示');
  $('#resultCount').textContent = `${u.items.length.toLocaleString()} 件 (未いいね${suffix})`;
}

function renderUnlikedGrid() {
  const items = state.unliked.items;
  const m = $('#masonry');
  if (items.length === 0 && !state.unliked.hasMore) {
    m.innerHTML = `<div class="text-zinc-400 text-sm p-6">未いいねの投稿はありません。</div>`;
    $('#resultCount').textContent = '0 件 (未いいね)';
    return;
  }
  m.innerHTML = '';
  appendUnlikedTiles(items, 0);
  updateUnlikedCountLabel();
}

function appendUnlikedTiles(newItems, startIdx) {
  if (newItems.length === 0) return;
  const html = newItems.map((p, i) => unlikedTileHtml(p, startIdx + i)).join('');
  $('#masonry').insertAdjacentHTML('beforeend', html);
  $$('#masonry .tile:not([data-unliked-bound])').forEach(el => {
    el.dataset.unlikedBound = '1';
    el.addEventListener('click', (e) => {
      if (e.target.classList.contains('like-btn')) return;
      openUnlikedLightbox(parseInt(el.dataset.idx, 10));
    });
    const lb = el.querySelector('.like-btn');
    if (lb) lb.addEventListener('click', async (e) => {
      e.stopPropagation();
      const i = parseInt(el.dataset.idx, 10);
      await likeAndSavePost(state.unliked.items[i], lb);
    });
  });
}

function dropUnlikedByTweetId(tweetId) {
  const u = state.unliked;
  if (!u.active) return;
  let removed = 0;
  for (let i = u.items.length - 1; i >= 0; i--) {
    if (u.items[i].tweet_id !== tweetId) continue;
    const tile = document.querySelector(`#masonry .tile[data-idx="${i}"], #masonry .reel-item[data-idx="${i}"]`);
    if (tile && tile.parentNode) tile.parentNode.removeChild(tile);
    u.items.splice(i, 1);
    removed++;
  }
  if (removed === 0) return;
  // Re-number remaining tiles so their data-idx still maps to items[].
  $$('#masonry .tile, #masonry .reel-item').forEach((node, i) => { node.dataset.idx = i; });
  updateUnlikedCountLabel();
  // Top up if we just emptied the visible list but more pages exist.
  if (u.items.length === 0 && u.hasMore) loadMoreUnliked();
}

function unlikedTileHtml(p, idx) {
  return `<div class="tile cursor-pointer" data-idx="${idx}">
    <img src="${p.thumb_url}" loading="lazy" decoding="async" alt="" />
    <div class="like-btn" title="X でいいね + 保存">♥</div>
    <div class="info">
      <div class="truncate">${escapeHtml(p.author_nick || p.author_name)} <span class="text-zinc-400">@${escapeHtml(p.author_name)}</span></div>
      ${p.favorite_count ? `<div class="text-zinc-400 text-[11px]">♥ ${p.favorite_count.toLocaleString()}</div>` : ''}
    </div>
  </div>`;
}

function openUnlikedLightbox(idx) {
  // Reuse the lightbox plumbing — items become the navigation set.
  const items = state.unliked.items;
  const p = items[idx];
  if (!p) return;
  state.lb = { items: items.slice(), pos: idx, gridIdx: -1, source: 'unliked' };
  $('#lightbox').classList.remove('hidden');
  $('#lightbox').classList.add('flex');
  renderLightbox();
}

// --- Posts fetch ---------------------------------------------------
async function fetchPosts() {
  if (state.tab === 'bookshelf') return;
  if (state.loading) return;
  state.loading = true;
  // Keep the author header in sync with whatever author filter is active.
  renderAuthorHeader();
  let endpoint, params = new URLSearchParams();
  if (state.tab === 'timeline') {
    endpoint = '/api/timeline';
    if (state.filter.media_type) params.set('media_type', state.filter.media_type);
    if (state.hideLiked) params.set('hide_liked', 'true');
    params.set('limit', state.limit); params.set('offset', state.offset);
  } else {
    endpoint = '/api/posts';
    if (state.filter.author) params.set('author', state.filter.author);
    if (state.filter.tag) params.set('tag', state.filter.tag);
    if (state.filter.media_type) params.set('media_type', state.filter.media_type);
    if (state.filter.q) params.set('q', state.filter.q);
    if (state.filter.list_id) params.set('list', state.filter.list_id);
    params.set('limit', state.limit); params.set('offset', state.offset);
  }
  // 初回 / フィルタ変更直後 (グリッドが空) はスケルトンを敷いて体感を埋める
  // (Phase 3 / 2026-06-10。真っ白画面の不安をなくす)
  const masonryEl = document.getElementById('masonry');
  const showSkeleton = state.offset === 0 && masonryEl && !masonryEl.children.length;
  if (showSkeleton) {
    masonryEl.innerHTML = Array.from({ length: 12 }, (_, i) =>
      `<div class="tile-skeleton" style="height:${160 + (i % 4) * 60}px"></div>`
    ).join('');
  }
  try {
    const r = await fetch(`${endpoint}?${params}`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();
    const items = data.items || [];
    state.total = data.total ?? 0;
    state.posts.push(...items);
    $('#resultCount').textContent = `${state.total.toLocaleString()} 件`;
    appendTiles(items);  // offset===0 なら appendTiles が skeleton ごとクリアする
    state.offset += items.length;
    state.loading = false;
  } catch (e) {
    if (showSkeleton) masonryEl.innerHTML = '';
    // Used to fail silently (empty grid). Surface it instead. Keep state.loading
    // true briefly so the infinite-scroll sentinel doesn't hammer a failing
    // endpoint, then clear it so a transient blip can still resume (don't pin
    // state.total, which would permanently stop pagination).
    showNotice('投稿の読み込みに失敗しました: ' + (e.message || e), { kind: 'error' });
    setTimeout(() => { state.loading = false; }, 5000);
  }
}

function compareIds(a, b) {
  // Twitter snowflakes are monotonically increasing; compare as BigInt for safety.
  try {
    const la = BigInt(a), lb = BigInt(b);
    return la < lb ? -1 : la > lb ? 1 : 0;
  } catch {
    return a < b ? -1 : a > b ? 1 : 0;
  }
}

function dividerHtml() {
  return `<div class="seen-divider">📍 ここまで見た</div>`;
}

function emptyStateHtml() {
  // Phase 5: 結果ゼロの真っ白画面を案内に変える
  const filtered = !!(state.filter.author || state.filter.tag || state.filter.q
    || state.filter.media_type || state.filter.list_id);
  if (filtered) {
    return `<div class="empty-state"><div class="glyph">∅</div>
      <div class="title">この条件に合う投稿はありません</div>
      <div class="hint">フィルタを外すか、別の条件をお試しください</div></div>`;
  }
  if (state.tab === 'timeline') {
    return `<div class="empty-state"><div class="glyph">✦</div>
      <div class="title">タイムラインはまだ空です</div>
      <div class="hint">右上の「⟳ 取得」でフォロー中の投稿を読み込めます</div></div>`;
  }
  return `<div class="empty-state"><div class="glyph">✦</div>
    <div class="title">まだ投稿がありません</div>
    <div class="hint">ヘッダーの ⟳ ボタンで X のいいねを同期できます</div></div>`;
}

function appendTiles(items) {
  const m = $('#masonry');
  if (state.offset === 0) {
    m.innerHTML = '';
    state.dividerInserted = false;
  }
  if (!items.length && state.posts.length === 0) {
    m.innerHTML = emptyStateHtml();
    return;
  }
  const lastSeen = state.lastSeenTimeline;
  let html = '';
  for (let i = 0; i < items.length; i++) {
    const p = items[i];
    const idx = state.posts.length - items.length + i;
    let isSeenBoundary = false;
    if (
      state.tab === 'timeline' && lastSeen && !state.dividerInserted &&
      compareIds(p.tweet_id, lastSeen) <= 0
    ) {
      if (state.layout !== 'reel') {
        html += dividerHtml();
      } else {
        isSeenBoundary = true;
      }
      state.dividerInserted = true;
    }
    html += state.layout === 'reel' ? reelItemHtml(p, idx, isSeenBoundary) : tileHtml(p, idx);
  }
  m.insertAdjacentHTML('beforeend', html);
  const tileSelector = state.layout === 'reel'
    ? '#masonry .reel-item:not([data-bound])'
    : '#masonry .tile:not([data-bound])';
  const newTiles = $$(tileSelector);
  if (state.tab === 'timeline') observeNewTiles(newTiles);
  if (state.layout === 'reel') setupReelObserver();
  $$('#masonry img[data-video-src]:not([data-vcap])').forEach(img => videoCapObserver.observe(img));
  newTiles.forEach(el => {
    el.dataset.bound = '1';
    el.addEventListener('click', (e) => {
      if (e.target.classList.contains('add-btn')) return;
      if (e.target.classList.contains('like-btn')) return;
      if (e.target.classList.contains('del-btn')) return;
      openLightbox(parseInt(el.dataset.idx, 10));
    });
    const ab = el.querySelector('.add-btn');
    if (ab) ab.addEventListener('click', (e) => {
      e.stopPropagation();
      openListPopover(parseInt(el.dataset.idx, 10), ab);
    });
    const lb = el.querySelector('.like-btn');
    if (lb) lb.addEventListener('click', async (e) => {
      e.stopPropagation();
      const idx = parseInt(el.dataset.idx, 10);
      await likeAndSave(idx, lb);
    });
    const db = el.querySelector('.del-btn');
    if (db) db.addEventListener('click', async (e) => {
      e.stopPropagation();
      await deletePost(parseInt(el.dataset.idx, 10), el);
    });
  });
}

async function deletePost(idx, tileEl) {
  const p = state.posts[idx];
  if (!p) return;
  if (!confirm(`この画像を削除しますか？\n@${p.author_name} / ${p.tweet_id}\n\n次回の同期でも再ダウンロードされません。`)) return;
  const r = await fetch(`/api/posts/${encodeURIComponent(p.tweet_id)}/${p.num}`, { method: 'DELETE' });
  if (!r.ok) {
    alert('削除に失敗しました: ' + r.status);
    return;
  }
  // Remove from in-memory list and from the DOM, keep scroll position.
  state.posts.splice(idx, 1);
  state.total = Math.max(0, state.total - 1);
  $('#resultCount').textContent = `${state.total.toLocaleString()} 件`;
  if (tileEl && tileEl.parentNode) tileEl.parentNode.removeChild(tileEl);
  // Reindex remaining tiles' data-idx so subsequent clicks still find the right post.
  $$('#masonry .tile, #masonry .reel-item').forEach((node, i) => { node.dataset.idx = i; });
}

async function likeAndSave(idx, btn) {
  const p = state.posts[idx];
  if (!p) return;
  await likeAndSavePost(p, btn);
}

async function likeAndSavePost(p, btn) {
  if (!p) return;
  btn.classList.add('busy');
  btn.title = 'X いいね + 保存中…';
  try {
    const r = await fetch('/api/timeline/like-and-save', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ tweet_id: p.tweet_id, author_name: p.author_name }),
    });
    const d = await r.json();
    btn.classList.remove('busy');
    if (d.liked) {
      btn.classList.add('done');
      btn.title = d.saved ? '✓ いいね & 保存済み' : `✓ いいね (保存失敗: ${d.save_message || '?'})`;
      markLikedEverywhere(p.tweet_id, d.saved);
    } else {
      btn.title = `✗ ${d.like_message || 'failed'}`;
      btn.style.background = '#7c2d3a';
    }
  } catch (e) {
    btn.classList.remove('busy');
    btn.title = '✗ ' + e.message;
    btn.style.background = '#7c2d3a';
  }
}

// Mark every visible occurrence of `tweet_id` as liked: grid tiles, lightbox
// like button, and the "未いいね" view (if active) drops the post entirely.
function markLikedEverywhere(tweetId, saved) {
  const title = saved ? '✓ いいね & 保存済み' : '✓ いいね (保存失敗)';
  state.posts.forEach((p, idx) => {
    if (p.tweet_id !== tweetId) return;
    const tile = document.querySelector(`#masonry .tile[data-idx="${idx}"], #masonry .reel-item[data-idx="${idx}"]`);
    if (!tile) return;
    const lb = tile.querySelector('.like-btn');
    if (lb) {
      lb.classList.remove('busy');
      lb.classList.add('done');
      lb.title = title;
    }
  });
  // Sync the lightbox's like button if it's currently showing this tweet.
  if (!$('#lightbox').classList.contains('hidden')) {
    const cur = state.lb.items[state.lb.pos];
    if (cur && cur.tweet_id === tweetId) {
      const lbLike = $('#lbLikeBtn');
      if (lbLike) {
        lbLike.disabled = true;
        lbLike.textContent = title;
        lbLike.classList.remove('bg-rose-600', 'hover:bg-rose-500');
        lbLike.classList.add('bg-rose-700');
      }
    }
  }
  // If the unliked-author view is active, drop matching posts from it
  // (DOM-level removal preserves scroll position).
  dropUnlikedByTweetId(tweetId);
}

function tileHtml(p, idx) {
  const isVideo = p.media_type === 'video' || p.extension === 'mp4';
  const thumbSrc = `${p.thumb_url}${p.thumb_url.includes('?') ? '&' : '?'}size=600`;
  const dims = (p.width && p.height) ? ` width="${p.width}" height="${p.height}"` : '';
  const media = isVideo
    ? `<img src="" alt="" data-video-src="${p.media_url}"${dims} style="background:#111;min-height:80px" />`
    : `<img src="${thumbSrc}" loading="lazy" decoding="async" alt=""${dims} />`;
  const sideBtn = state.tab === 'timeline'
    ? `<div class="like-btn" title="X でいいね + 保存">♥</div>`
    : `<div class="del-btn" title="削除 (再ダウンロード防止)">🗑</div>`;
  const addBtn = `<div class="add-btn" title="リストに追加">+</div>`;
  const starBadge = p.in_any_list ? `<div class="list-star">★</div>` : '';
  return `<div class="tile cursor-pointer" data-idx="${idx}">
    ${media}
    ${sideBtn}
    ${addBtn}
    ${starBadge}
    <div class="info">
      <div class="truncate">${escapeHtml(p.author_nick || p.author_name)} <span class="text-zinc-400">@${escapeHtml(p.author_name)}</span></div>
      ${p.favorite_count ? `<div class="text-zinc-400 text-[11px]">♥ ${p.favorite_count.toLocaleString()}</div>` : ''}
    </div>
  </div>`;
}

function reelItemHtml(p, idx, isSeenBoundary = false) {
  const isVideo = p.media_type === 'video' || p.extension === 'mp4';
  const thumbSrc = `${p.thumb_url}${p.thumb_url.includes('?') ? '&' : '?'}size=600`;
  const media = isVideo
    ? `<img src="" alt="" class="reel-poster" data-video-src="${p.media_url}" />
       <video data-src="${p.media_url}" preload="none" muted loop playsinline class="reel-media"></video>`
    : `<img src="${p.media_url}" loading="lazy" decoding="async" alt="" />`;
  const sideBtn = state.tab === 'timeline'
    ? `<div class="like-btn" title="X でいいね + 保存">♥</div>`
    : `<div class="del-btn" title="削除 (再ダウンロード防止)">🗑</div>`;
  const addBtn = `<div class="add-btn" title="リストに追加">+</div>`;
  const seenBadge = isSeenBoundary ? `<div class="reel-seen-badge">📍 ここまで見た</div>` : '';
  return `<div class="reel-item cursor-pointer" data-idx="${idx}">
    ${seenBadge}
    ${media}
    ${sideBtn}
    ${addBtn}
    <div class="reel-info">
      <div class="truncate">${escapeHtml(p.author_nick || p.author_name)} <span class="opacity-60">@${escapeHtml(p.author_name)}</span></div>
      ${p.favorite_count ? `<div class="opacity-60 text-[11px]">♥ ${p.favorite_count.toLocaleString()}</div>` : ''}
    </div>
  </div>`;
}

// --- Reel: 1-item-at-a-time scroll control --------------------------
let _reelScrollLocked = false;
let _reelCurrentIndex = 0;
let _reelTouchStartY = 0;
let _reelWheelHandler = null;
let _reelTouchStartHandler = null;
let _reelTouchMoveHandler = null;
let _reelTouchEndHandler = null;

function _reelScrollBy(direction) {
  if (_reelScrollLocked) return;
  const grid = $('#grid');
  const items = grid.querySelectorAll('.reel-item');
  if (!items.length) return;
  _reelCurrentIndex = Math.max(0, Math.min(items.length - 1, _reelCurrentIndex + direction));
  _reelScrollLocked = true;
  grid.scrollTo({ top: _reelCurrentIndex * grid.clientHeight, behavior: 'smooth' });
  setTimeout(() => { _reelScrollLocked = false; }, 550);
}

function setupReelScrollControl() {
  const grid = $('#grid');
  // Clean up previous handlers
  if (_reelWheelHandler) grid.removeEventListener('wheel', _reelWheelHandler);
  if (_reelTouchStartHandler) grid.removeEventListener('touchstart', _reelTouchStartHandler);
  if (_reelTouchMoveHandler) grid.removeEventListener('touchmove', _reelTouchMoveHandler);
  if (_reelTouchEndHandler) grid.removeEventListener('touchend', _reelTouchEndHandler);
  _reelWheelHandler = null; _reelTouchStartHandler = null; _reelTouchMoveHandler = null; _reelTouchEndHandler = null;

  if (state.layout !== 'reel') return;

  _reelCurrentIndex = 0;

  _reelWheelHandler = (e) => {
    e.preventDefault();
    _reelScrollBy(e.deltaY >= 0 ? 1 : -1);
  };
  _reelTouchStartHandler = (e) => {
    _reelTouchStartY = e.touches[0].clientY;
  };
  _reelTouchMoveHandler = (e) => {
    e.preventDefault(); // prevent native inertia scroll stacking
  };
  _reelTouchEndHandler = (e) => {
    const dy = _reelTouchStartY - e.changedTouches[0].clientY;
    if (Math.abs(dy) < 30) return;
    _reelScrollBy(dy > 0 ? 1 : -1);
  };

  grid.addEventListener('wheel', _reelWheelHandler, { passive: false });
  grid.addEventListener('touchstart', _reelTouchStartHandler, { passive: true });
  grid.addEventListener('touchmove', _reelTouchMoveHandler, { passive: false });
  grid.addEventListener('touchend', _reelTouchEndHandler, { passive: true });
}

let reelObserver = null;
function setupReelObserver() {
  if (reelObserver) reelObserver.disconnect();
  if (state.layout !== 'reel') { reelObserver = null; setupReelScrollControl(); return; }
  setupReelScrollControl();
  reelObserver = new IntersectionObserver(entries => {
    entries.forEach(e => {
      const video = e.target.querySelector('video.reel-media');
      if (!video) return;
      if (e.isIntersecting) {
        if (video.dataset.src && !video.src) {
          video.src = video.dataset.src;
          video.load();
          const poster = e.target.querySelector('.reel-poster');
          video.addEventListener('canplay', () => {
            if (poster) poster.style.display = 'none';
          }, { once: true });
        }
        video.play().catch(() => {});
      } else {
        video.pause();
      }
    });
  }, { root: $('#grid'), threshold: 0.6 });
  $$('#masonry .reel-item').forEach(el => reelObserver.observe(el));
}

// --- List popover --------------------------------------------------
async function openListPopover(idx, anchor) {
  const p = state.posts[idx];
  if (!p) return;
  const memR = await fetch(`/api/posts/lists?tweet_id=${encodeURIComponent(p.tweet_id)}&num=${p.num}`);
  const mem = (await memR.json()).list_ids;
  const memSet = new Set(mem);
  const pop = $('#listPopover');
  pop.innerHTML = state.lists.map(l => `
    <label>
      <input type="checkbox" data-list-id="${l.id}" ${memSet.has(l.id) ? 'checked' : ''}/>
      <span class="truncate">${escapeHtml(l.name)}</span>
      <span class="ml-auto text-zinc-500 text-xs">${l.count}</span>
    </label>`).join('') + `
    <div class="new-row">
      <input type="text" id="popNewName" placeholder="新規リスト名" />
      <button class="add" id="popNewBtn">追加</button>
    </div>`;
  const rect = anchor.getBoundingClientRect();
  const popH = 220;
  const spaceBelow = window.innerHeight - rect.bottom;
  const useAbove = spaceBelow < popH + 8 && rect.top > popH + 8;
  pop.style.top = useAbove ? (rect.top - popH - 4) + 'px' : (rect.bottom + 4) + 'px';
  pop.style.left = Math.max(8, Math.min(rect.right - 220, window.innerWidth - 230)) + 'px';
  pop.classList.remove('hidden');

  async function likeIfTimeline() {
    if (state.tab !== 'timeline') return;
    const dummyBtn = { classList: { add: () => {}, remove: () => {} }, title: '', style: {} };
    await likeAndSavePost(p, dummyBtn);
  }

  pop.querySelectorAll('input[type=checkbox]').forEach(cb => {
    cb.addEventListener('change', async () => {
      const id = parseInt(cb.dataset.listId, 10);
      if (cb.checked) {
        await likeIfTimeline();
        await fetch(`/api/lists/${id}/items`, {
          method: 'POST', headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ tweet_id: p.tweet_id, num: p.num }),
        });
        await loadLists();
        closeListPopover();
      } else {
        await fetch(`/api/lists/${id}/items/${encodeURIComponent(p.tweet_id)}/${p.num}`, {
          method: 'DELETE',
        });
        await loadLists();
      }
    });
  });
  pop.querySelector('#popNewBtn').addEventListener('click', async () => {
    const name = pop.querySelector('#popNewName').value.trim();
    if (!name) return;
    await likeIfTimeline();
    const r = await fetch('/api/lists', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ name }),
    });
    if (!r.ok) return;
    const newList = await r.json();
    await fetch(`/api/lists/${newList.id}/items`, {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ tweet_id: p.tweet_id, num: p.num }),
    });
    await loadLists();
    closeListPopover();
  });
}

function closeListPopover() {
  $('#listPopover').classList.add('hidden');
}
document.addEventListener('click', (e) => {
  const pop = $('#listPopover');
  if (pop.classList.contains('hidden')) return;
  if (pop.contains(e.target)) return;
  if (e.target.classList.contains('add-btn') || e.target.id === 'lbAddBtn') return;
  closeListPopover();
});

// --- Lightbox ------------------------------------------------------
// state.lb.items holds every post sharing the same tweet_id (sorted by num).
// state.lb.pos is the index within items currently shown.
// state.lb.gridIdx is the original index in state.posts (kept for delete/tile sync).
async function openLightbox(idx) {
  const p = state.posts[idx];
  if (!p) return;
  // Show the modal immediately with the clicked post; fetch the rest async so
  // the user never sees a blank flash on slow disks.
  const source = state.tab === 'timeline' ? 'timeline' : 'likes';
  state.lb = { items: [p], pos: 0, gridIdx: idx, source };
  $('#lightbox').classList.remove('hidden');
  $('#lightbox').classList.add('flex');
  renderLightbox();
  // Likes tab pulls siblings from the local Index; timeline tab pulls from the
  // cached timeline DB rows. Both endpoints return rows with proxy/local URLs
  // already baked in by the server.
  const endpoint = state.tab === 'timeline'
    ? `/api/timeline/by-tweet/${encodeURIComponent(p.tweet_id)}`
    : `/api/posts/by-tweet/${encodeURIComponent(p.tweet_id)}`;
  try {
    const r = await fetch(endpoint);
    if (!r.ok) return;
    const data = await r.json();
    const items = (data.items || []);
    if (items.length <= 1) return;                // nothing to switch between
    if ($('#lightbox').classList.contains('hidden')) return; // user closed it
    const pos = Math.max(0, items.findIndex(it => it.num === p.num));
    state.lb.items = items;
    state.lb.pos = pos;
    renderLightbox();
  } catch { /* keep single-item view */ }
}

function renderLightbox() {
  const { items, pos } = state.lb;
  const p = items[pos];
  if (!p) return;
  const isVideo = p.media_type === 'video' || p.extension === 'mp4';
  const media = isVideo
    ? `<video src="${p.media_url}" controls autoplay loop class="max-h-[80vh] max-w-[92vw]"></video>`
    : `<img src="${p.media_url}" decoding="async" class="max-h-[80vh] max-w-[92vw] object-contain" />`;
  $('#lbMedia').innerHTML = media;

  const hasGroup = items.length > 1;
  $('#lbPrev').classList.toggle('hidden', !hasGroup);
  $('#lbNext').classList.toggle('hidden', !hasGroup);
  const pager = hasGroup
    ? `<span class="lb-page-indicator">${pos + 1} / ${items.length}</span>`
    : '';

  const tags = p.hashtags.map(t => `<span class="chip rounded px-2 py-0.5 text-xs cursor-pointer" data-tag="${escapeHtml(t)}">#${escapeHtml(t)}</span>`).join(' ');
  const fromUnliked = state.lb.source === 'unliked';
  const showLikeBtn = state.tab === 'timeline' || fromUnliked;
  const lbActionBtn = showLikeBtn
    ? `<button id="lbLikeBtn" class="bg-rose-600 hover:bg-rose-500 text-white text-sm rounded px-3 py-1">♥ いいね & 保存</button>`
    : `<button id="lbDelBtn" class="bg-red-700 hover:bg-red-600 text-white text-sm rounded px-3 py-1">🗑 削除</button>`;
  // List membership only makes sense for posts already in the local archive.
  const lbAddBtn = fromUnliked
    ? ''
    : `<button id="lbAddBtn" class="bg-indigo-600 hover:bg-indigo-500 text-white text-sm rounded px-3 py-1">+ リスト</button>`;
  $('#lbMeta').innerHTML = `
    <div class="flex items-center justify-between mb-2">
      <div class="flex items-center gap-3">
        ${lbActionBtn}
        ${lbAddBtn}
        <span class="font-semibold">${escapeHtml(p.author_nick || p.author_name)}</span>
        <button type="button" class="lb-author-link" data-author="${escapeHtml(p.author_name)}">@${escapeHtml(p.author_name)}</button>
        ${pager}
      </div>
      <a href="${p.tweet_url}" target="_blank" class="text-indigo-400 text-xs">元ツイートを開く ↗</a>
    </div>
    ${p.content ? `<div class="text-zinc-300 whitespace-pre-wrap mb-2">${escapeHtml(p.content)}</div>` : ''}
    ${tags ? `<div class="flex flex-wrap gap-1 mb-2">${tags}</div>` : ''}
    <div class="text-zinc-500 text-xs flex gap-3 flex-wrap">
      <span>${escapeHtml(p.date)}</span>
      ${p.favorite_count ? `<span>♥ ${p.favorite_count.toLocaleString()}</span>` : ''}
      ${p.view_count ? `<span>👁 ${p.view_count.toLocaleString()}</span>` : ''}
      ${p.width ? `<span>${p.width}×${p.height}</span>` : ''}
    </div>`;

  $$('#lbMeta [data-tag]').forEach(el => el.addEventListener('click', () => {
    closeLightbox();
    state.filter.tag = el.dataset.tag;
    state.offset = 0; state.posts = [];
    renderTags(); renderFilterChips(); fetchPosts();
  }));
  $$('#lbMeta [data-author]').forEach(el => el.addEventListener('click', () => {
    const name = el.dataset.author;
    closeLightbox();
    state.filter.author = name;
    state.offset = 0; state.posts = [];
    renderAuthors(); renderFilterChips(); fetchPosts();
  }));
  const lbAdd = $('#lbAddBtn');
  if (lbAdd) lbAdd.addEventListener('click', (e) => {
    e.stopPropagation();
    if (state.lb.gridIdx < 0) return;
    openListPopover(state.lb.gridIdx, lbAdd);
  });
  const lbDel = $('#lbDelBtn');
  if (lbDel) lbDel.addEventListener('click', async (e) => {
    e.stopPropagation();
    // Delete acts on the post currently shown — find the matching grid tile by
    // tweet_id+num so we stay correct after navigating within the group.
    const cur = state.lb.items[state.lb.pos];
    const gridIdx = state.posts.findIndex(x => x.tweet_id === cur.tweet_id && x.num === cur.num);
    const tile = gridIdx >= 0
      ? document.querySelector(`#masonry .tile[data-idx="${gridIdx}"], #masonry .reel-item[data-idx="${gridIdx}"]`)
      : null;
    closeLightbox();
    if (gridIdx >= 0) await deletePost(gridIdx, tile);
  });
  const lbLike = $('#lbLikeBtn');
  if (lbLike) lbLike.addEventListener('click', async (e) => {
    e.stopPropagation();
    lbLike.disabled = true;
    lbLike.textContent = '⏳ いいね & 保存中…';
    try {
      const r = await fetch('/api/timeline/like-and-save', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ tweet_id: p.tweet_id, author_name: p.author_name }),
      });
      const d = await r.json();
      lbLike.disabled = false;
      if (d.liked) {
        lbLike.textContent = d.saved ? '✓ いいね & 保存済み' : `✓ いいね (保存失敗)`;
        lbLike.classList.remove('bg-rose-600', 'hover:bg-rose-500');
        lbLike.classList.add('bg-rose-700');
        markLikedEverywhere(p.tweet_id, d.saved);
      } else {
        lbLike.textContent = `✗ ${d.like_message || 'failed'}`;
        lbLike.classList.remove('bg-rose-600', 'hover:bg-rose-500');
        lbLike.classList.add('bg-zinc-700');
      }
    } catch (err) {
      lbLike.disabled = false;
      lbLike.textContent = '✗ ' + err.message;
    }
  });
}

function lbGo(delta) {
  const n = state.lb.items.length;
  if (n <= 1) return;
  state.lb.pos = (state.lb.pos + delta + n) % n;
  renderLightbox();
}

function closeLightbox() {
  $('#lightbox').classList.add('hidden');
  $('#lightbox').classList.remove('flex');
  $('#lbMedia').innerHTML = '';
  $('#lbPrev').classList.add('hidden');
  $('#lbNext').classList.add('hidden');
  state.lb = { items: [], pos: 0 };
}
$('#lbClose').addEventListener('click', closeLightbox);
$('#lbPrev').addEventListener('click', (e) => { e.stopPropagation(); lbGo(-1); });
$('#lbNext').addEventListener('click', (e) => { e.stopPropagation(); lbGo(+1); });
$('#lightbox').addEventListener('click', (e) => { if (e.target.id === 'lightbox') closeLightbox(); });
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') { closeLightbox(); closeListPopover(); return; }
  if ($('#lightbox').classList.contains('hidden')) return;
  if (e.key === 'ArrowLeft')  { e.preventDefault(); lbGo(-1); }
  if (e.key === 'ArrowRight') { e.preventDefault(); lbGo(+1); }
});

// --- Tag section toggle -------------------------------------------
$('#tagToggle').addEventListener('click', () => {
  const list = $('#tagList');
  const collapsed = list.classList.toggle('hidden');
  $('#tagToggleIcon').textContent = collapsed ? '▶' : '▼';
});

// --- Author single-count section toggle ---------------------------
document.addEventListener('click', (e) => {
  if (e.target.closest('#authorSingleToggle')) {
    const btn = $('#authorSingleToggle');
    const list = $('#authorSingleList');
    const opening = list.classList.toggle('hidden');
    btn.dataset.open = opening ? '0' : '1';
    btn.querySelector('span').textContent = opening ? '▶' : '▼';
  }
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

// --- Notification banner (sync/timeline results + errors) ----------
// One shared banner. Errors (e.g. expired cookies) stay until clicked; info
// notices auto-dismiss. Surfaces outcomes that used to fail silently.
function showNotice(message, { kind = 'info', sticky = false, onClick = null } = {}) {
  // Phase 5 統一トースト: 見た目は .app-toast (style.css) に集約。
  // シグネチャと onClick/クリック消滅の挙動は従来どおり (20+ 呼び出し箇所無修正)。
  document.getElementById('appNotice')?.remove();
  const el = document.createElement('div');
  el.id = 'appNotice';
  el.className = 'app-toast' + (kind === 'error' ? ' toast-error' : '');
  const icon = document.createElement('span');
  icon.className = 'toast-icon';
  icon.textContent = kind === 'error' ? '⚠' : '✦';
  const text = document.createElement('span');
  text.textContent = message;
  el.append(icon, text);
  el.title = 'クリックで閉じる';
  el.onclick = () => { if (onClick) onClick(); el.remove(); };
  document.body.appendChild(el);
  if (!sticky) setTimeout(() => { if (el.isConnected) el.remove(); }, 8000);
}

const COOKIE_EXPIRED_MSG = 'X の cookie が失効している可能性があります。再ログインして cookies を更新してください。';

// verify-before-alarm: sync/timeline の auth_error はX側の一時 401 でも立つ
// （backend は gallery-dl ログの正規表現スキャン）。怖い sticky バナーを出す前に
// 軽量 verify（自分のいいね 1 件取得）で裏取りし、一過性なら控えめな通知に落とす。
let _authNoticeShown = false;  // 同期+タイムライン同時失敗でバナー2枚重なるのを防ぐ
async function notifyAuthFailure() {
  if (_authNoticeShown) return;
  _authNoticeShown = true;
  try {
    const r = await fetch('/api/cookies/verify', { method: 'POST' });
    const v = await r.json();
    if (v.ok) {
      // cookie は生きている = 一過性の失敗（X の一時 401 / 再起動直後など）
      showNotice('同期が一時的に失敗しました（cookie は有効です）。次回また自動で試します。', { kind: 'info' });
      _authNoticeShown = false;
      return;
    }
  } catch { /* verify 自体が通信失敗 → 従来どおり警告側に倒す */ }
  // 本当に失効 or 判定不能 → 従来の sticky バナー（タップで設定モーダルを開く）
  showNotice(COOKIE_EXPIRED_MSG + '（タップで設定を開く）', {
    kind: 'error', sticky: true, onClick: openCookieModal,
  });
}

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

// --- Options popover -----------------------------------------------
$('#optionsBtn').addEventListener('click', (e) => {
  e.stopPropagation();
  const pop = $('#optionsPopover');
  if (!pop.classList.contains('hidden')) { pop.classList.add('hidden'); return; }
  const rect = e.currentTarget.getBoundingClientRect();
  pop.style.top = (rect.bottom + 4 + window.scrollY) + 'px';
  // ビューポートクランプ (Phase 5 スマホ操作性): 右端の ⚙ から開くと
  // 280px 幅のポップオーバーが画面外へはみ出していた
  pop.style.left = Math.max(8, Math.min(rect.left, window.innerWidth - 288)) + 'px';
  pop.classList.remove('hidden');
  loadCookieStatus();
});
document.addEventListener('click', (e) => {
  const pop = $('#optionsPopover');
  if (pop.classList.contains('hidden')) return;
  if (pop.contains(e.target) || e.target.id === 'optionsBtn') return;
  pop.classList.add('hidden');
});

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

// --- X cookies management ------------------------------------------
async function loadCookieStatus() {
  const el = $('#cookieStatus');
  if (!el) return;
  try {
    const r = await fetch('/api/cookies/status');
    const d = await r.json();
    if (d.configured) {
      const when = d.updated_at ? new Date(d.updated_at * 1000).toLocaleString() : '';
      el.innerHTML = `✅ cookies 設定済み${d.looks_valid ? '' : '・形式要確認'}`
        + (when ? `<span class="text-zinc-600"> · ${escapeHtml(when)}</span>` : '');
    } else {
      el.textContent = '⚠️ cookies 未設定 — 同期するには設定が必要です';
    }
  } catch { el.textContent = '状態を取得できませんでした'; }
}

function openCookieModal() {
  document.getElementById('cookieModal')?.remove();
  const modal = document.createElement('div');
  modal.id = 'cookieModal';
  // overflow-y-auto + 子の m-auto: カードが画面より縦長でもスクロールで
  // 保存/テストボタンに必ず届く (2026-06-10 スマホでボタン押せない bug。
  // items-center だけだと flexbox 中央寄せがはみ出し分を両端クリップする)
  modal.className = 'fixed inset-0 bg-black/80 flex z-[70] p-4 overflow-y-auto';
  modal.innerHTML = `
    <div class="bg-zinc-900 border border-zinc-800 rounded-lg w-full max-w-lg p-4 space-y-3 m-auto">
      <div class="flex items-center justify-between">
        <div class="text-sm text-zinc-200 font-medium">X cookies の設定 <span class="text-zinc-600 text-xs">v__APP_VERSION__</span></div>
        <button id="cookieCloseBtn" class="text-zinc-400 hover:text-white text-xl leading-none">×</button>
      </div>
      <div id="cookieCurStatus" class="text-xs text-zinc-400">状態を確認中…</div>
      <p class="text-xs text-zinc-500 leading-relaxed">
        ブラウザ拡張などで書き出した <code>cookies.txt</code>（Netscape 形式）の中身を貼り付けるか、
        ファイルを選択してください。保存先は永続ストレージで再デプロイ不要。失効したらここで貼り替えれば復旧します。
        <span class="text-zinc-600">※ セキュリティのため保存済みの中身は表示されません（入力欄は常に空です）。</span>
      </p>
      <textarea id="cookieText" rows="7" spellcheck="false"
        class="w-full bg-zinc-950 border border-zinc-800 rounded px-2 py-1.5 text-xs font-mono focus:outline-none focus:border-indigo-500"
        placeholder="# Netscape HTTP Cookie File&#10;.x.com&#9;TRUE&#9;/&#9;TRUE&#9;...&#9;auth_token&#9;..."></textarea>
      <div class="flex items-center gap-2">
        <label class="bg-zinc-800 hover:bg-zinc-700 text-zinc-200 rounded px-2 py-1 text-xs cursor-pointer">
          ファイルを選択
          <input id="cookieFile" type="file" accept=".txt,text/plain" class="hidden" />
        </label>
        <span id="cookieFileName" class="text-xs text-zinc-500"></span>
      </div>
      <div id="cookieMsg" class="text-xs min-h-[1rem]"></div>
      <div class="flex items-center justify-end gap-2 pt-1">
        <button id="cookieVerifyBtn" class="bg-zinc-800 hover:bg-zinc-700 text-zinc-200 rounded px-3 py-1.5 text-xs">接続テスト</button>
        <button id="cookieSaveBtn" class="bg-indigo-600 hover:bg-indigo-500 text-white rounded px-3 py-1.5 text-xs">保存</button>
      </div>
    </div>`;
  document.body.appendChild(modal);

  const close = () => modal.remove();
  modal.querySelector('#cookieCloseBtn').addEventListener('click', close);
  modal.addEventListener('click', (e) => { if (e.target === modal) close(); });

  // サーバー側の保存状態を表示（入力欄は秘密保護のため常に空 = 未保存と誤解しやすい）
  (async () => {
    const el = modal.querySelector('#cookieCurStatus');
    try {
      const s = await (await fetch('/api/cookies/status')).json();
      if (s.configured) {
        const when = s.updated_at ? new Date(s.updated_at * 1000).toLocaleString('ja-JP') : '不明';
        el.textContent = `✅ サーバーに cookies 保存済み（更新: ${when}）` + (s.looks_valid ? '' : ' ⚠️ 形式が不正の可能性');
        el.style.color = '#86efac';
      } else {
        el.textContent = '⚠️ サーバーに cookies 未保存 — 下に貼り付けて保存してください';
        el.style.color = '#fcd34d';
      }
    } catch { el.textContent = '状態の取得に失敗しました'; }
  })();

  const msg = modal.querySelector('#cookieMsg');
  const setMsg = (text, kind) => {
    msg.textContent = text;
    msg.style.color = kind === 'error' ? '#fca5a5' : (kind === 'ok' ? '#86efac' : '#a1a1aa');
  };

  modal.querySelector('#cookieFile').addEventListener('change', (e) => {
    const file = e.target.files && e.target.files[0];
    if (!file) return;
    modal.querySelector('#cookieFileName').textContent = file.name;
    const reader = new FileReader();
    reader.onload = () => { modal.querySelector('#cookieText').value = String(reader.result || ''); };
    reader.readAsText(file);
  });

  modal.querySelector('#cookieSaveBtn').addEventListener('click', async () => {
    const content = modal.querySelector('#cookieText').value;
    if (!content.trim()) { setMsg('cookies を貼り付けてください', 'error'); return; }
    setMsg('保存中…');
    try {
      const r = await fetch('/api/cookies', {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ content }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) {
        setMsg('✗ ' + (d.detail || '保存に失敗しました'), 'error');
        showNotice('cookies の保存に失敗: ' + (d.detail || ''), { kind: 'error' });
        return;
      }
      setMsg('✅ 保存しました。接続テストで有効性を確認できます', 'ok');
      // モーダル内の小さい結果行はスマホで見落とされる — 上部バナーでも通知し、
      // 冒頭の保存状態行も即更新する (2026-06-10 「押しても何も起きない」誤認)
      showNotice('✅ cookies を保存しました', { kind: 'info' });
      const cur = modal.querySelector('#cookieCurStatus');
      if (cur) { cur.textContent = '✅ サーバーに cookies 保存済み（たった今）'; cur.style.color = '#86efac'; }
      loadCookieStatus();
    } catch (e) {
      setMsg('✗ ' + e.message, 'error');
      showNotice('cookies の保存に失敗: ' + e.message, { kind: 'error' });
    }
  });

  modal.querySelector('#cookieVerifyBtn').addEventListener('click', async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    setMsg('接続テスト中…（最大 30 秒）');
    // クライアント側の保険タイムアウト。サーバー側は lock 5s + probe fast_fail で
    // 通常十数秒以内に返るが、万一の無応答でもボタンが固まったままにしない。
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 30000);
    try {
      const r = await fetch('/api/cookies/verify', { method: 'POST', signal: ctrl.signal });
      const d = await r.json().catch(() => ({}));
      setMsg((d.ok ? '✅ ' : '✗ ') + (d.message || ''), d.ok ? 'ok' : 'error');
      // スマホで見落とされない位置 (上部バナー) にも同じ結果を出す (2026-06-10)
      showNotice('接続テスト: ' + (d.ok ? '✅ ' : '') + (d.message || ''), { kind: d.ok ? 'info' : 'error' });
    } catch (err) {
      setMsg(err.name === 'AbortError'
        ? '✗ 時間切れ: サーバーの応答がありません。少し待ってから再試行してください'
        : '✗ ' + err.message, 'error');
    } finally {
      clearTimeout(timer);
      btn.disabled = false;
    }
  });
}

document.getElementById('cookieSetBtn')?.addEventListener('click', openCookieModal);

// --- Timeline last-seen marker ------------------------------------
async function loadLastSeen() {
  try {
    const r = await fetch('/api/timeline/last-seen');
    const d = await r.json();
    state.lastSeenTimeline = d.tweet_id || '';
  } catch { state.lastSeenTimeline = ''; }
}

let lastSeenSaveTimer = null;
function saveLastSeenSoon() {
  // Debounce: many tiles may flip "seen" during a single scroll burst.
  if (lastSeenSaveTimer) clearTimeout(lastSeenSaveTimer);
  lastSeenSaveTimer = setTimeout(async () => {
    if (!state.lastSeenTimeline) return;
    try {
      await fetch('/api/timeline/last-seen', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ tweet_id: state.lastSeenTimeline }),
      });
    } catch {}
  }, 1500);
}

let seenObserver = null;
function setupSeenObserver() {
  if (seenObserver) { seenObserver.disconnect(); seenObserver = null; }
  if (state.tab !== 'timeline') return;
  seenObserver = new IntersectionObserver((entries) => {
    let advanced = false;
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      const idx = parseInt(entry.target.dataset.idx, 10);
      const post = state.posts[idx];
      if (!post) continue;
      if (!state.lastSeenTimeline || compareIds(post.tweet_id, state.lastSeenTimeline) > 0) {
        state.lastSeenTimeline = post.tweet_id;
        advanced = true;
      }
    }
    if (advanced) saveLastSeenSoon();
  }, { threshold: 0.5 });
}

function observeNewTiles(tiles) {
  if (!seenObserver) return;
  for (const tile of tiles) seenObserver.observe(tile);
}

$('#markSeenBtn').addEventListener('click', async () => {
  if (state.tab !== 'timeline' || !state.posts.length) return;
  const top = state.posts[0].tweet_id;
  await fetch('/api/timeline/last-seen', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ tweet_id: top }),
  });
  state.lastSeenTimeline = top;
  // Re-render so the divider moves to the new boundary.
  state.offset = 0; state.posts = []; state.dividerInserted = false;
  await fetchPosts();
});

// --- Timeline refresh ---------------------------------------------
async function triggerTimelineRefresh() {
  // Honored quietly: cooldown 429 is not a user-facing error.
  try {
    const r = await fetch('/api/timeline/refresh', { method: 'POST' });
    if (r.status === 429) return;       // cooldown — fine, will fire again later
    if (!r.ok) return;
  } catch { return; }
  $('#timelineRefreshBtn').textContent = '⏳ 取得中…';
  pollTimeline();
}

$('#timelineRefreshBtn').addEventListener('click', async () => {
  await triggerTimelineRefresh();
});

async function pollTimeline() {
  const r = await fetch('/api/timeline/status');
  const s = await r.json();
  if (s.running) {
    setTimeout(pollTimeline, 2000);
    return;
  }
  const tag = s.last_added ? ` (+${s.last_added})` : '';
  $('#timelineRefreshBtn').textContent = `⟳ 取得${tag}`;
  $('#timelineRefreshBtn').disabled = false;
  if (s.auth_error) {
    notifyAuthFailure();
  } else if (s.last_added > 0) {
    showNotice(`タイムライン 新着 ${s.last_added} 件`, { kind: 'info' });
  }
  if (state.tab === 'timeline') {
    state.offset = 0; state.posts = [];
    fetchPosts();
  }
  if (s.last_error && !s.auth_error) console.warn('timeline refresh error:', s.last_error);
}

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
async function loadFavoriteAuthors() {
  try {
    const r = await fetch('/api/favorite-authors');
    const list = await r.json();
    state.favoriteAuthors = new Set(list);
  } catch { /* keep empty */ }
}

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

// --- Bookshelf ---------------------------------------------------
state.books = [];

async function fetchBooks() {
  const r = await fetch('/api/books');
  state.books = await r.json();
  renderBooks();
}

// Bookshelf filter state
let bookFilter = { type: null, tag: null }; // type: 'favorite' | 'tag' | null

function renderBooks() {
  const m = $('#masonry');
  const addBtn = `<div class="tile cursor-pointer flex items-center justify-center border-2 border-dashed border-zinc-700 hover:border-indigo-500 rounded-lg" style="min-height:200px" id="bookAddTile">
    <div class="text-center text-zinc-500 hover:text-indigo-400">
      <div class="text-4xl mb-2">+</div>
      <div class="text-sm">漫画を追加</div>
    </div>
  </div>`;

  // Apply filter
  let filtered = state.books;
  if (bookFilter.type === 'favorite') {
    filtered = filtered.filter(b => b.is_favorite);
  } else if (bookFilter.type === 'tag' && bookFilter.tag) {
    filtered = filtered.filter(b => b.tags && b.tags.includes(bookFilter.tag));
  }

  const tiles = filtered.map(b => {
    const cover = b.cover_path ? `/thumb/${b.cover_path}?size=400` : '';
    const favClass = b.is_favorite ? 'text-yellow-400' : 'text-zinc-600 hover:text-yellow-400';
    return `<div class="tile cursor-pointer" data-book-id="${b.id}">
      ${cover ? `<img data-cover="${cover}" loading="lazy" decoding="async" alt="" />` : '<div class="bg-zinc-800 w-full" style="min-height:200px"></div>'}
      <button class="del-btn" data-del-book="${b.id}" title="削除">🗑</button>
      <button class="fav-btn ${favClass}" data-fav-book="${b.id}" title="お気に入り">♥</button>
      <div class="info" style="opacity:1;position:relative;padding:8px 10px;background:rgba(0,0,0,.7)">
        <div class="truncate font-medium">${escapeHtml(b.title)}</div>
        <div class="text-zinc-400 text-xs">${b.page_count} ページ</div>
      </div>
    </div>`;
  }).join('');
  m.innerHTML = addBtn + tiles;

  // Lazy-load covers slightly before they enter the viewport.
  m.querySelectorAll('img[data-cover]').forEach(img => bookCoverObserver.observe(img));

  // Add button click
  document.getElementById('bookAddTile')?.addEventListener('click', openBookUpload);

  // Delete buttons
  m.querySelectorAll('[data-del-book]').forEach(btn => {
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      if (!confirm('この漫画を削除しますか？')) return;
      await fetch(`/api/books/${btn.dataset.delBook}`, { method: 'DELETE' });
      await fetchBooks();
    });
  });

  // Favorite buttons
  m.querySelectorAll('[data-fav-book]').forEach(btn => {
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      const bookId = btn.dataset.favBook;
      const res = await fetch(`/api/books/${bookId}/favorite`, { method: 'POST' });
      const data = await res.json();
      // Update local state
      const book = state.books.find(b => b.id == bookId);
      if (book) book.is_favorite = data.favorite;
      renderBooks();
      loadBookTags();
    });
  });

  // Book tile clicks — open tag editor on right-click, reader on left-click
  m.querySelectorAll('.tile[data-book-id]').forEach(el => {
    el.addEventListener('click', () => openReader(parseInt(el.dataset.bookId, 10)));
    el.addEventListener('contextmenu', (e) => {
      e.preventDefault();
      openTagEditor(parseInt(el.dataset.bookId, 10));
    });
  });
}

function openTagEditor(bookId) {
  const book = state.books.find(b => b.id === bookId);
  if (!book) return;
  const currentTags = (book.tags || []).join(', ');
  const input = prompt(`タグ編集（カンマ区切り）\n「${book.title}」`, currentTags);
  if (input === null) return;
  const tags = input.split(',').map(t => t.trim()).filter(Boolean);
  fetch(`/api/books/${bookId}/tags`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ tags })
  }).then(() => {
    book.tags = tags;
    renderBooks();
    loadBookTags();
  });
}

async function loadBookTags() {
  const res = await fetch('/api/books/tags');
  const data = await res.json();
  const tagList = $('#bookTagList');
  const favCount = state.books.filter(b => b.is_favorite).length;
  $('#bookFavCount').textContent = favCount;

  tagList.innerHTML = data.tags.map(t =>
    `<button class="book-tag-btn w-full flex items-center gap-2 text-sm text-zinc-300 hover:text-white py-1 px-2 rounded hover:bg-zinc-800 transition-colors ${bookFilter.type === 'tag' && bookFilter.tag === t.name ? 'bg-zinc-800 text-white' : ''}" data-book-tag="${escapeHtml(t.name)}">
      <span class="truncate">${escapeHtml(t.name)}</span>
      <span class="ml-auto text-xs text-zinc-500">${t.count}</span>
    </button>`
  ).join('');

  // Tag click handlers
  tagList.querySelectorAll('.book-tag-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const tag = btn.dataset.bookTag;
      if (bookFilter.type === 'tag' && bookFilter.tag === tag) {
        bookFilter = { type: null, tag: null };
      } else {
        bookFilter = { type: 'tag', tag };
      }
      renderBooks();
      loadBookTags();
    });
  });
}

// --- Book Upload ---
function openBookUpload() {
  const modal = document.createElement('div');
  modal.id = 'bookUploadModal';
  // 同上: スマホで縦長時もボタンに届くようスクロール可能に (2026-06-10)
  modal.className = 'fixed inset-0 bg-black/80 flex z-50 p-4 overflow-y-auto';
  modal.innerHTML = `
    <div class="bg-zinc-900 rounded-xl p-6 w-full max-w-md shadow-2xl m-auto">
      <h2 class="text-lg font-semibold mb-4">漫画を追加</h2>
      <div class="flex gap-2 mb-4">
        <button id="modeFile" class="px-3 py-1.5 rounded text-sm bg-indigo-600 text-white">ファイル</button>
        <button id="modeUrl" class="px-3 py-1.5 rounded text-sm bg-zinc-700 text-zinc-300 hover:bg-zinc-600">URLから取得</button>
      </div>
      <div id="panelFile">
        <label class="block text-sm text-zinc-400 mb-1">タイトル</label>
        <input id="bookTitle" type="text" class="w-full bg-zinc-800 border border-zinc-700 rounded px-3 py-2 mb-4 text-white" placeholder="タイトルを入力" />
        <label class="block text-sm text-zinc-400 mb-1">画像ファイル（複数選択）</label>
        <input id="bookFiles" type="file" multiple accept="image/*" class="w-full text-sm text-zinc-300 mb-2 file:mr-3 file:rounded file:border-0 file:bg-indigo-600 file:px-3 file:py-1.5 file:text-white file:cursor-pointer" />
        <p class="text-xs text-zinc-500 mb-4">フォルダ内の画像をまとめて選択してください。ファイル名順にページが並びます。</p>
      </div>
      <div id="panelUrl" class="hidden">
        <label class="block text-sm text-zinc-400 mb-1">URL</label>
        <div class="flex gap-2 mb-2">
          <input id="bookUrl" type="url" class="flex-1 bg-zinc-800 border border-zinc-700 rounded px-3 py-2 text-white text-sm" placeholder="https://hitomi.la/..." />
          <button id="bookAddUrlBtn" class="px-3 py-2 rounded bg-indigo-600 hover:bg-indigo-500 text-sm text-white whitespace-nowrap">追加</button>
        </div>
        <p class="text-xs text-zinc-500 mb-3">URLを入力して「追加」→ キューに積まれます。複数OK。</p>
        <div id="importQueueList" class="space-y-1 max-h-48 overflow-y-auto mb-3"></div>
      </div>
      <div id="bookUploadProgress" class="hidden mb-4">
        <div class="w-full bg-zinc-700 rounded-full h-2"><div id="bookProgressBar" class="bg-indigo-500 h-2 rounded-full transition-all" style="width:0%"></div></div>
        <p class="text-xs text-zinc-400 mt-1" id="bookProgressText">処理中...</p>
      </div>
      <div class="flex gap-3 justify-end">
        <button id="bookCancelBtn" class="px-4 py-2 rounded bg-zinc-700 hover:bg-zinc-600 text-sm">閉じる</button>
        <button id="bookSubmitBtn" class="px-4 py-2 rounded bg-indigo-600 hover:bg-indigo-500 text-sm font-medium">追加</button>
      </div>
    </div>`;
  document.body.appendChild(modal);

  // Tab switching
  let mode = 'file';
  modal.querySelector('#modeFile').addEventListener('click', () => {
    mode = 'file';
    modal.querySelector('#modeFile').className = 'px-3 py-1.5 rounded text-sm bg-indigo-600 text-white';
    modal.querySelector('#modeUrl').className = 'px-3 py-1.5 rounded text-sm bg-zinc-700 text-zinc-300 hover:bg-zinc-600';
    modal.querySelector('#panelFile').classList.remove('hidden');
    modal.querySelector('#panelUrl').classList.add('hidden');
  });
  modal.querySelector('#modeUrl').addEventListener('click', () => {
    mode = 'url';
    modal.querySelector('#modeUrl').className = 'px-3 py-1.5 rounded text-sm bg-indigo-600 text-white';
    modal.querySelector('#modeFile').className = 'px-3 py-1.5 rounded text-sm bg-zinc-700 text-zinc-300 hover:bg-zinc-600';
    modal.querySelector('#panelUrl').classList.remove('hidden');
    modal.querySelector('#panelFile').classList.add('hidden');
  });

  modal.querySelector('#bookCancelBtn').addEventListener('click', () => {
    modal.remove();
    if (state._importPoll) { clearInterval(state._importPoll); state._importPoll = null; }
    fetchBooks();
  });
  modal.addEventListener('click', (e) => {
    if (e.target === modal) {
      modal.remove();
      if (state._importPoll) { clearInterval(state._importPoll); state._importPoll = null; }
      fetchBooks();
    }
  });
  modal.querySelector('#bookSubmitBtn').addEventListener('click', () => {
    if (mode === 'file') submitBook();
  });
  // URL mode: "追加" button adds to queue
  modal.querySelector('#bookAddUrlBtn')?.addEventListener('click', () => addUrlToQueue());
  // Enter key in URL input
  modal.querySelector('#bookUrl')?.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); addUrlToQueue(); }
  });
}

async function submitBook() {
  const title = document.getElementById('bookTitle').value.trim();
  const fileInput = document.getElementById('bookFiles');
  if (!title) { alert('タイトルを入力してください'); return; }
  if (!fileInput.files.length) { alert('ファイルを選択してください'); return; }

  const form = new FormData();
  form.append('title', title);
  for (const f of fileInput.files) form.append('files', f);

  document.getElementById('bookUploadProgress').classList.remove('hidden');
  document.getElementById('bookSubmitBtn').disabled = true;

  try {
    const r = await fetch('/api/books', { method: 'POST', body: form });
    if (!r.ok) { const e = await r.json(); alert(e.detail || 'エラー'); return; }
    const data = await r.json().catch(() => ({}));
    document.getElementById('bookUploadModal')?.remove();
    if (data.skipped) {
      alert('既存の本と重複していたため追加しませんでした' + (data.matched_title ? '：' + data.matched_title : ''));
      return;
    }
    await fetchBooks();
  } catch (e) {
    alert('アップロード失敗: ' + e.message);
  }
}

async function addUrlToQueue() {
  const input = document.getElementById('bookUrl');
  const url = input.value.trim();
  if (!url) return;
  input.value = '';

  try {
    const r = await fetch('/api/books/import', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url }),
    });
    if (!r.ok) {
      const e = await r.json().catch(() => ({}));
      alert(String(e.detail || e.reason || 'エラー'));
      return;
    }
  } catch (e) {
    alert('リクエスト失敗: ' + e.message);
    return;
  }

  // Start polling if not already
  if (!state._importPoll) {
    state._importPoll = setInterval(pollImportQueue, 1500);
    pollImportQueue();
  }
}

async function pollImportQueue() {
  try {
    const r = await fetch('/api/books/import/status');
    const data = await r.json();
    const queue = data.queue || [];
    renderImportQueue(queue);

    // If all done/error, stop polling
    const active = queue.some(i => i.status === 'running' || i.status === 'pending');
    if (!active && queue.length > 0) {
      clearInterval(state._importPoll);
      state._importPoll = null;
    }
  } catch { /* ignore network blip */ }
}

function renderImportQueue(queue) {
  const el = document.getElementById('importQueueList');
  if (!el) return;
  if (!queue.length) { el.innerHTML = ''; return; }

  el.innerHTML = queue.map(item => {
    const shortUrl = item.url.length > 40 ? item.url.slice(0, 37) + '...' : item.url;
    let badge = '';
    if (item.status === 'running') badge = '<span class="text-indigo-400">⏳ ' + escapeHtml(item.progress || 'DL中') + '</span>';
    else if (item.status === 'done') badge = '<span class="text-green-400">✓ 完了</span>';
    else if (item.status === 'skipped') badge = '<span class="text-amber-400">⊘ 重複スキップ' + (item.matched_title ? '（' + escapeHtml(item.matched_title) + '）' : '') + '</span>';
    else if (item.status === 'error') badge = '<span class="text-red-400">✗ ' + escapeHtml((item.error || '').slice(0, 30)) + '</span>';
    else badge = '<span class="text-zinc-500">待機中</span>';

    const titlePart = item.title ? ` <span class="text-zinc-300">${escapeHtml(item.title)}</span>` : '';
    return `<div class="flex items-center justify-between text-xs py-1 px-2 bg-zinc-800 rounded">
      <div class="truncate flex-1 text-zinc-400" title="${escapeHtml(item.url)}">${escapeHtml(shortUrl)}${titlePart}</div>
      <div class="ml-2 whitespace-nowrap">${badge}</div>
    </div>`;
  }).join('');
}

state._importPoll = null;

// --- Book Reader ---
state.reader = { bookId: null, pages: [], pos: 0 };

// Warm the browser cache for upcoming pages so navigation feels instant.
// Pages are served with immutable cache headers, so a warmed image is reused on real nav.
const _prefetchCache = new Set();
function prefetchPageImages(startPos, count, direction) {
  const { pages } = state.reader;
  if (!pages || !pages.length) return;
  const step = direction >= 0 ? 1 : -1;
  const targets = [];
  for (let k = 1; k <= count; k++) targets.push(startPos + step * k);
  targets.push(startPos - step); // one behind, for quick back-nav
  for (const i of targets) {
    if (i < 0 || i >= pages.length) continue;
    const rel = pages[i].rel_path;
    if (_prefetchCache.has(rel)) continue;
    _prefetchCache.add(rel);
    const im = new Image();
    im.src = `/api/media/${rel}`;
  }
}

async function openReader(bookId) {
  const r = await fetch(`/api/books/${bookId}`);
  const data = await r.json();
  state.reader = { bookId, pages: data.pages, pos: 0 };
  const modal = document.getElementById('readerModal');
  modal.classList.remove('hidden');
  modal.classList.add('flex');
  paintPanes();              // also recenters the track
  prefetchPageImages(0, 3, 1);
}

function closeReader() {
  const modal = document.getElementById('readerModal');
  modal.classList.add('hidden');
  modal.classList.remove('flex');
  _readerResetTrack();
  _prefetchCache.clear();
}

// --- Reader slide track ---------------------------------------------------
// Manga RTL: drag RIGHT = next page, drag LEFT = prev. Pane layout (left->right)
// = [next, current, prev]; rest state shows the centered current pane.
function _readerTrackEl() { return document.getElementById('readerTrack'); }
function _readerW() {
  const vp = document.getElementById('readerViewport');
  return (vp && vp.clientWidth) || window.innerWidth;
}
function _readerSetX(px, animate) {
  const t = _readerTrackEl();
  if (!t) return;
  t.classList.toggle('reader-animating', !!animate);
  t.style.transform = `translateX(${px}px)`;
}
function _readerResetTrack() { _readerSetX(-_readerW(), false); }

// Paint the 3 panes from state.reader for the current pos, then snap the track
// back to centered (no animation). RTL slot mapping: 0=next, 1=current, 2=prev.
function paintPanes() {
  const { pages, pos } = state.reader;
  const imgs = _readerTrackEl()?.querySelectorAll('img[data-slot]');
  if (!imgs) return;
  const srcFor = (i) => (i >= 0 && i < pages.length) ? `/api/media/${pages[i].rel_path}` : '';
  const map = [pos + 1, pos, pos - 1];
  imgs.forEach((img, slot) => {
    const want = srcFor(map[slot]);
    if (want) { if (img.getAttribute('src') !== want) img.src = want; }
    else img.removeAttribute('src');
  });
  document.getElementById('readerPageNum').textContent = pages.length ? `${pos + 1} / ${pages.length}` : '0 / 0';
  _readerResetTrack();
}

function readerGo(delta) {
  const n = state.reader.pages.length;
  if (n === 0) return;
  const prev = state.reader.pos;
  state.reader.pos = Math.max(0, Math.min(n - 1, prev + delta));
  paintPanes();
  if (state.reader.pos !== prev) prefetchPageImages(state.reader.pos, 3, delta >= 0 ? 1 : -1);
}

// Reader keyboard — RTL: ArrowLeft = next, ArrowRight = prev (manga). Flip for LTR.
document.addEventListener('keydown', (e) => {
  if (document.getElementById('readerModal').classList.contains('hidden')) return;
  if (e.key === 'Escape') { closeReader(); e.preventDefault(); }
  if (e.key === 'ArrowLeft') { readerGo(1); e.preventDefault(); }    // RTL: left = next
  if (e.key === 'ArrowRight') { readerGo(-1); e.preventDefault(); }  // RTL: right = prev
});

// Reader drag — Pointer Events unify mouse + touch with finger-follow slide.
(function() {
  const COMMIT_RATIO = 0.18, AXIS_LOCK = 8, TAP_MAX = 10, ANIM_MS = 320;
  let dragging = false, startX = 0, startY = 0, lastDX = 0, axis = null, moved = false, pid = null, W = 0;
  let animToken = 0;
  const vp = () => document.getElementById('readerViewport');
  const isOpen = () => !document.getElementById('readerModal').classList.contains('hidden');

  function canGo(delta) {
    const { pos, pages } = state.reader;
    const target = pos + delta;
    return target >= 0 && target < pages.length;
  }

  function onDown(e) {
    if (!isOpen()) return;
    animToken++;             // invalidate any in-flight commit animation
    dragging = true; moved = false; axis = null;
    startX = e.clientX; startY = e.clientY; lastDX = 0; pid = e.pointerId; W = _readerW();
    try { vp().setPointerCapture(pid); } catch (_) {}
    _readerTrackEl()?.classList.remove('reader-animating');  // follow finger 1:1
  }

  function onMove(e) {
    if (!dragging) return;
    const dx = e.clientX - startX, dy = e.clientY - startY;
    if (axis === null && (Math.abs(dx) > AXIS_LOCK || Math.abs(dy) > AXIS_LOCK)) {
      axis = Math.abs(dx) > Math.abs(dy) ? 'x' : 'y';
    }
    if (axis === 'y') return;
    if (axis === 'x') moved = true;
    lastDX = dx;
    // Rubber-band when there's no page to reveal that way
    // (drag right reveals next, drag left reveals prev).
    let eff = dx;
    if ((dx > 0 && !canGo(1)) || (dx < 0 && !canGo(-1))) eff = dx * 0.25;
    _readerSetX(-W + eff, false);
  }

  function onUp(e) {
    if (!dragging) return;
    dragging = false;
    try { vp().releasePointerCapture(pid); } catch (_) {}
    const dx = lastDX;
    if (!moved || Math.abs(dx) < TAP_MAX) { tapNav(e.clientX); return; }
    if (axis === 'x' && Math.abs(dx) >= W * COMMIT_RATIO) {
      commitTurn(dx > 0 ? 1 : -1);   // RTL: drag right = next, drag left = prev
    } else {
      snapBack();
    }
  }

  function commitTurn(delta) {
    if (!canGo(delta)) { snapBack(); return; }
    const t = _readerTrackEl();
    const target = delta > 0 ? 0 : -2 * W;   // reveal left (next) or right (prev) pane
    const myToken = ++animToken;
    let finished = false;
    const finish = () => {
      if (finished || myToken !== animToken) return;
      finished = true;
      t.removeEventListener('transitionend', finish);
      readerGo(delta);   // update pos + repaint panes + recenter (no transition)
    };
    t.addEventListener('transitionend', finish);
    setTimeout(finish, ANIM_MS);   // fallback if transitionend is missed
    _readerSetX(target, true);
  }

  function snapBack() { animToken++; _readerSetX(-W, true); }

  // RTL click zones: left half = next, right half = prev.
  function tapNav(clientX) {
    const w = _readerW();
    if (clientX < w / 2) readerGo(1);   // left = next
    else readerGo(-1);                  // right = prev
  }

  const v = vp();
  if (v) {
    v.addEventListener('pointerdown', onDown);
    v.addEventListener('pointermove', onMove);
    v.addEventListener('pointerup', onUp);
    v.addEventListener('pointercancel', () => { if (dragging) { dragging = false; snapBack(); } });
  }
})();

// onclick=closeReader() の module 化代替 (Phase 4 / module scope はグローバル非公開)
document.getElementById('readerCloseBtn')?.addEventListener('click', () => closeReader());


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

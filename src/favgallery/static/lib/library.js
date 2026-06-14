// サイドバーのライブラリ情報: 作者/タグ/リスト一覧 + フィルタチップ + 作者ヘッダー。
// (Phase 4B: main.js から分離。posts.js とは相互参照 — ES module 循環 import で解決)
import { state } from 'state';
import { $, $$, escapeHtml } from 'dom';
import { showNotice } from 'notices';
import { fetchPosts, enterUnlikedMode, exitUnlikedMode } from 'posts';
import { icon } from 'icons';

export async function loadLibrary() {
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
  const label = data.scanning ? `スキャン中… ${data.post_count.toLocaleString()}` : `${data.post_count.toLocaleString()} posts`;
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
    <button class="author-btn flex-1 min-w-0 text-left px-2 py-1.5 rounded text-sm flex items-center justify-between ${isActive ? 'author-active bg-indigo-600/20 text-indigo-300' : 'hover:bg-zinc-800 text-zinc-300'}"
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

// 作者リストのクリックは「イベント委譲」でコンテナに1回だけバインドする。
// 旧実装は renderAuthors のたびに全ボタンへ addEventListener を貼り直していたため、
// 作者数が多いと1クリックごとに「全行 DOM の破棄→再生成 + 数千リスナー再バインド」が
// 同期実行されて激重だった（2026-06-14 perf 修正）。
let _authorEventsBound = false;
function initAuthorEvents() {
  if (_authorEventsBound) return;
  for (const sel of ['#authorList', '#authorSingleList']) {
    const el = $(sel);
    if (el) el.addEventListener('click', onAuthorListClick);
  }
  _authorEventsBound = true;
}

function onAuthorListClick(e) {
  const favBtn = e.target.closest('.fav-btn');
  if (favBtn) {
    e.stopPropagation();
    const name = favBtn.dataset.author;
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
    renderAuthors(); // お気に入りは並び替え（上に集約）が要るので全再生成。頻度は低い。
    return;
  }
  const authBtn = e.target.closest('.author-btn');
  if (authBtn) {
    const name = authBtn.dataset.author;
    state.filter.author = state.filter.author === name ? null : name;
    state.offset = 0; state.posts = [];
    markActiveAuthor(); // 全再生成せず、ハイライトだけ差し替え（激重の根治）
    renderFilterChips();
    fetchPosts();
  }
}

// 作者行のアクティブ表示だけを切り替える（DOM 破棄・リスナー再バインドなし）。
function setAuthorBtnActive(btn, active) {
  btn.classList.toggle('author-active', active);
  btn.classList.toggle('bg-indigo-600/20', active);
  btn.classList.toggle('text-indigo-300', active);
  btn.classList.toggle('hover:bg-zinc-800', !active);
  btn.classList.toggle('text-zinc-300', !active);
}

export function markActiveAuthor() {
  // 直前にアクティブだった行（最大1つ）を非アクティブへ戻す。
  $$('.author-btn.author-active').forEach(b => setAuthorBtnActive(b, false));
  const name = state.filter.author;
  if (!name) return;
  for (const b of $$('.author-btn')) {
    if (b.dataset.author === name) { setAuthorBtnActive(b, true); break; }
  }
}

export function renderAuthors() {
  initAuthorEvents(); // 委譲リスナーを一度だけ設置（冪等）
  const favSet = state.favoriteAuthors;
  const multi = state.authors.filter(a => a.post_count > 1);
  const single = state.authors.filter(a => a.post_count === 1);
  const sortGroup = (arr) => [
    ...arr.filter(a => favSet.has(a.name)),
    ...arr.filter(a => !favSet.has(a.name)),
  ];
  $('#authorTotal').textContent = `${state.authors.length} 人`;

  $('#authorList').innerHTML = sortGroup(multi).map(authorRowHtml).join('');

  const singleSection = $('#authorSingleSection');
  const singleList = $('#authorSingleList');
  if (single.length > 0) {
    singleSection.classList.remove('hidden');
    const btn = $('#authorSingleToggle');
    btn.querySelector('.icon-chevron')?.classList.toggle('open', btn.dataset.open === '1');
    singleList.innerHTML = sortGroup(single).map(authorRowHtml).join('');
  } else {
    singleSection.classList.add('hidden');
  }
}

export function renderTags() {
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
export async function loadLists() {
  const r = await fetch('/api/lists');
  state.lists = await r.json();
  renderListSidebar();
}

export function renderListSidebar() {
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
      <button class="list-del text-zinc-500 hover:text-rose-400 px-1" title="削除" aria-label="削除" data-list-id="${l.id}">${icon('x')}</button>
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
export function renderFilterChips() {
  const f = state.filter;
  const chips = [];
  if (f.list_id) {
    const l = state.lists.find(x => x.id === f.list_id);
    if (l) chips.push(`<span class="chip active rounded px-3 py-1 text-sm cursor-pointer inline-flex items-center gap-1" data-clear="list_id">${icon('list')} ${escapeHtml(l.name)} ${icon('x')}</span>`);
  }
  if (f.author) chips.push(`<span class="chip active rounded px-3 py-1 text-sm cursor-pointer inline-flex items-center gap-1" data-clear="author">@${escapeHtml(f.author)} ${icon('x')}</span>`);
  if (f.tag)    chips.push(`<span class="chip active rounded px-3 py-1 text-sm cursor-pointer inline-flex items-center gap-1" data-clear="tag">#${escapeHtml(f.tag)} ${icon('x')}</span>`);
  if (f.media_type) chips.push(`<span class="chip active rounded px-3 py-1 text-sm cursor-pointer inline-flex items-center gap-1" data-clear="media_type">${escapeHtml(f.media_type)} ${icon('x')}</span>`);
  if (f.q)      chips.push(`<span class="chip active rounded px-3 py-1 text-sm cursor-pointer inline-flex items-center gap-1" data-clear="q">"${escapeHtml(f.q)}" ${icon('x')}</span>`);
  $('#filterChips').innerHTML = chips.join('');
  $$('#filterChips [data-clear]').forEach(el => el.addEventListener('click', () => {
    const k = el.dataset.clear;
    state.filter[k] = (k === 'media_type') ? '' : null;
    if (k === 'q') { $('#searchBox').value = ''; state.filter.q = ''; }
    state.offset = 0; state.posts = [];
    markActiveAuthor(); renderTags(); renderListSidebar(); renderFilterChips(); fetchPosts();
  }));
}

// Author page header: show name + media-type tabs whenever an author filter is
// active on the likes tab. Tabs are toggle-style buttons that drive media_type.
export async function renderAuthorHeader() {
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

// --- Tag section toggle -------------------------------------------
$('#tagToggle').addEventListener('click', () => {
  const list = $('#tagList');
  const collapsed = list.classList.toggle('hidden');
  $('#tagToggleIcon').querySelector('.icon-chevron')?.classList.toggle('open', !collapsed);
});

// --- Author single-count section toggle ---------------------------
document.addEventListener('click', (e) => {
  if (e.target.closest('#authorSingleToggle')) {
    const btn = $('#authorSingleToggle');
    const list = $('#authorSingleList');
    const opening = list.classList.toggle('hidden');
    btn.dataset.open = opening ? '0' : '1';
    btn.querySelector('.icon-chevron')?.classList.toggle('open', !opening);
  }
});

// --- Favorite authors (init data) ----------------------------------
export async function loadFavoriteAuthors() {
  try {
    const r = await fetch('/api/favorite-authors');
    const list = await r.json();
    state.favoriteAuthors = new Set(list);
  } catch { /* keep empty */ }
}

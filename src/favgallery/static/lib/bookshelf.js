// 本棚タブ: 漫画の一覧/カバー遅延読込/お気に入り・タグフィルタ/アップロード/URLインポート。
// (Phase 4B: main.js から分離。state / dom / reader のみ依存のリーフ)
import { state } from 'state';
import { $, escapeHtml } from 'dom';
import { openReader } from 'reader';

state.books = [];
state._importPoll = null;

// Bookshelf filter state
let bookFilter = { type: null, tag: null }; // type: 'favorite' | 'tag' | null

// --- Bookshelf cover lazy-load (start loading ~300px before viewport) ---
const bookCoverObserver = new IntersectionObserver((entries) => {
  entries.forEach(e => {
    if (!e.isIntersecting) return;
    const img = e.target;
    bookCoverObserver.unobserve(img);
    if (img.dataset.cover) { img.src = img.dataset.cover; delete img.dataset.cover; }
  });
}, { rootMargin: '300px' });

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

export async function fetchBooks() {
  const r = await fetch('/api/books');
  state.books = await r.json();
  renderBooks();
}

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

export async function loadBookTags() {
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

// 投稿グリッド: fetch/タイル描画/削除/いいね&保存 + 未いいねビュー + リールモード +
// 動画サムネ取得。(Phase 4B: main.js から分離。library/lightbox/popovers/timeline と
// 相互参照 — ES module 循環 import で解決)
import { state } from 'state';
import { $, $$, escapeHtml } from 'dom';
import { showNotice } from 'notices';
import { renderAuthorHeader } from 'library';
import { openLightbox, renderLightbox } from 'lightbox';
import { openListPopover } from 'popovers';
import { observeNewTiles } from 'timeline';

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

// --- Unliked-author mode -------------------------------------------
const UNLIKED_PAGE_SIZE = 60;

export async function enterUnlikedMode(author) {
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

export async function loadMoreUnliked() {
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

export function exitUnlikedMode() {
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
export async function fetchPosts() {
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

export function compareIds(a, b) {
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

export async function deletePost(idx, tileEl) {
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

export async function likeAndSavePost(p, btn) {
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
export function markLikedEverywhere(tweetId, saved) {
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

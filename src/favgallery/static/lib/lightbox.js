// ライトボックス: 同一ツイートのメディア群を切替表示 + メタ情報 + いいね/削除/リスト操作。
// (Phase 4B: main.js から分離。posts/library/popovers と相互参照 — ES module 循環 import)
import { state } from 'state';
import { $, $$, escapeHtml } from 'dom';
import { renderAuthors, renderTags, renderFilterChips } from 'library';
import { fetchPosts, deletePost, markLikedEverywhere } from 'posts';
import { openListPopover, closeListPopover } from 'popovers';
import { icon } from 'icons';

// state.lb.items holds every post sharing the same tweet_id (sorted by num).
// state.lb.pos is the index within items currently shown.
// state.lb.gridIdx is the original index in state.posts (kept for delete/tile sync).
export async function openLightbox(idx) {
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

export function renderLightbox() {
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
    ? `<button id="lbLikeBtn" class="inline-flex items-center gap-1 bg-rose-600 hover:bg-rose-500 text-white text-sm rounded px-3 py-1">${icon('heart')} いいね & 保存</button>`
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
      ${p.favorite_count ? `<span class="inline-flex items-center gap-1">${icon('heart')} ${p.favorite_count.toLocaleString()}</span>` : ''}
      ${p.view_count ? `<span class="inline-flex items-center gap-1">${icon('eye')} ${p.view_count.toLocaleString()}</span>` : ''}
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
    lbLike.innerHTML = `${icon('loader','icon-spin')} いいね & 保存中…`;
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

export function closeLightbox() {
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

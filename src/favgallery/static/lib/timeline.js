// タイムライン: 既読 (last-seen) マーカー + ⟳ 取得 + 進捗ポーリング。
// (Phase 4B: main.js から分離。posts.js とは相互参照 — ES module 循環 import)
import { state } from 'state';
import { $ } from 'dom';
import { showNotice, notifyAuthFailure } from 'notices';
import { compareIds, fetchPosts } from 'posts';

// --- Timeline last-seen marker ------------------------------------
export async function loadLastSeen() {
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
export function setupSeenObserver() {
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

export function observeNewTiles(tiles) {
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

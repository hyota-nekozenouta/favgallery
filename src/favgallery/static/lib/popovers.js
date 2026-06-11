// ポップオーバー: リスト追加ポップオーバー + ⚙ オプションポップオーバー。
// (Phase 4B: main.js から分離。posts/library と相互参照 — ES module 循環 import)
import { state } from 'state';
import { $, escapeHtml } from 'dom';
import { likeAndSavePost } from 'posts';
import { loadLists } from 'library';
import { loadCookieStatus } from 'cookies';

// --- List popover --------------------------------------------------
export async function openListPopover(idx, anchor) {
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

export function closeListPopover() {
  $('#listPopover').classList.add('hidden');
}
document.addEventListener('click', (e) => {
  const pop = $('#listPopover');
  if (pop.classList.contains('hidden')) return;
  if (pop.contains(e.target)) return;
  if (e.target.classList.contains('add-btn') || e.target.id === 'lbAddBtn') return;
  closeListPopover();
});

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

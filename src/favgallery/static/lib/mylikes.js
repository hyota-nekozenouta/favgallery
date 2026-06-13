// マイいいねキャッシュ: ⚙ メニュー内のユーザー名保存 + マイいいね同期 + 進捗表示。
// (Phase 4B: main.js から分離)
import { state } from 'state';
import { $, escapeHtml } from 'dom';
import { enterUnlikedMode, exitUnlikedMode } from 'posts';

export async function loadMe() {
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
  if (s.running) lines.push('同期中…');
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

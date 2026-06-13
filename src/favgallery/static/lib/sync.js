// いいね同期 + 重複チェックの進捗ポーリングと砂時計表示 + 手動同期ボタン。
// (Phase 4B: main.js から分離)
import { state } from 'state';
import { $ } from 'dom';
import { showNotice, notifyAuthFailure } from 'notices';
import { loadLibrary } from 'library';
import { fetchPosts } from 'posts';

// --- Loading indicator (hourglass) --------------------------------
let _syncActive = false, _dedupActive = false, _visualDedupActive = false;
// ローディング表示は #loadingIndicator 内の SVG ローダー (.icon-spin) が CSS で回転する。
// ここでは表示/非表示の切替だけ行う (旧: ⏳/⌛ を textContent でフリップ → SVG を上書きしてしまうため廃止)。
function updateLoadingState() {
  const active = _syncActive || _dedupActive || _visualDedupActive;
  const el = $('#loadingIndicator');
  if (!el) return;
  el.classList.toggle('hidden', !active);
}

export async function pollSync() {
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

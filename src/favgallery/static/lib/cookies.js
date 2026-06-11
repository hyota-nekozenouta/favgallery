// X cookies の状態表示 + 設定モーダル。(Phase 4B: main.js から分離)
// notices.js とは相互参照 (showNotice ←→ openCookieModal) — ES module 循環 import で解決。
import { $, escapeHtml } from 'dom';
import { showNotice } from 'notices';

export async function loadCookieStatus() {
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

export function openCookieModal() {
  document.getElementById('cookieModal')?.remove();
  const modal = document.createElement('div');
  modal.id = 'cookieModal';
  // overflow-y-auto + 子の m-auto: カードが画面より縦長でもスクロールで
  // 保存/テストボタンに必ず届く (2026-06-10 スマホでボタン押せない bug。
  // items-center だけだと flexbox 中央寄せがはみ出し分を両端クリップする)
  modal.className = 'fixed inset-0 bg-black/80 flex z-[70] p-4 overflow-y-auto';
  // 版表示は window.APP_VERSION (index.html inline で配信時置換) を使う。
  // APP_VERSION プレースホルダ (アンダースコア囲み) はサーバーが index.html しか
  // 置換しないため、静的配信の lib/*.js に書くと生表示される
  // (Phase 4A からの生表示バグ修正 2026-06-11。regression テストが literal を検出する)
  modal.innerHTML = `
    <div class="bg-zinc-900 border border-zinc-800 rounded-lg w-full max-w-lg p-4 space-y-3 m-auto">
      <div class="flex items-center justify-between">
        <div class="text-sm text-zinc-200 font-medium">X cookies の設定 <span class="text-zinc-600 text-xs">v${window.APP_VERSION}</span></div>
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

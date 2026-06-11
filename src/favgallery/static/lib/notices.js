// 通知バナー（同期/タイムライン結果 + エラー）と auth 失敗の verify-before-alarm。
// (Phase 4B: main.js から分離。cookies.js とは相互参照 — ES module の循環 import で解決)
import { openCookieModal } from 'cookies';

// One shared banner. Errors (e.g. expired cookies) stay until clicked; info
// notices auto-dismiss. Surfaces outcomes that used to fail silently.
export function showNotice(message, { kind = 'info', sticky = false, onClick = null } = {}) {
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
export async function notifyAuthFailure() {
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
    if (!v.auth_error) {
      // 失効と「確認できていない」だけ（同期実行中 / X レート制限 / probe 失敗）。
      // ok 以外を全部「失効」赤バナーに倒していたのを修正 — 失効と実確認できた
      // (auth_error:true) 時だけ脅す (2026-06-11 誤案内根治。verify の message を
      // そのまま流す: busy/レート制限の文言はサーバー側が一元管理)
      showNotice('取得に失敗しました。' + (v.message || '時間を置いて再試行してください。'), { kind: 'info' });
      _authNoticeShown = false;
      return;
    }
  } catch { /* verify 自体が通信失敗 → 従来どおり警告側に倒す */ }
  // 失効を実確認 or 判定不能 → 従来の sticky バナー（タップで設定モーダルを開く）
  showNotice(COOKIE_EXPIRED_MSG + '（タップで設定を開く）', {
    kind: 'error', sticky: true, onClick: openCookieModal,
  });
}

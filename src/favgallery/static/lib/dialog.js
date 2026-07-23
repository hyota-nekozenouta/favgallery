// アプリ内ダイアログ (confirm / prompt)。OS 標準の confirm()/prompt() を置換し、
// 見た目とキーボード操作を他モーダルと統一する。Promise ベースで await 可能。
// - スクロールロックは main.js の syncScrollLock が #appDialog の有無を見て掛ける。
// - Esc/Enter/Tab は capture フェーズで処理し、main.js の Esc 連鎖より先に食う
//   (stopPropagation で二重クローズを防ぐ)。フォーカスは開いた要素へ復帰する。

const HOST_ID = 'appDialog';

function openDialog(opts) {
  return new Promise((resolve) => {
    document.getElementById(HOST_ID)?.remove();
    const prevFocus = document.activeElement;

    const host = document.createElement('div');
    host.id = HOST_ID;
    host.className = 'dialog-overlay';

    const box = document.createElement('div');
    box.className = 'dialog';
    box.setAttribute('role', 'dialog');
    box.setAttribute('aria-modal', 'true');

    if (opts.title) {
      const titleEl = document.createElement('div');
      titleEl.className = 'dialog-title';
      titleEl.id = 'appDialogTitle';
      titleEl.textContent = opts.title;
      box.setAttribute('aria-labelledby', 'appDialogTitle');
      box.appendChild(titleEl);
    }

    const bodyEl = document.createElement('div');
    bodyEl.className = 'dialog-body';
    if (opts.message) {
      const msg = document.createElement('div');
      msg.className = 'dialog-message';
      msg.textContent = opts.message;   // pre-line で改行を保持 (XSS 安全)
      bodyEl.appendChild(msg);
    }
    let input = null;
    if (opts.withInput) {
      input = document.createElement('input');
      input.type = 'text';
      input.className = 'dialog-input';
      input.value = opts.inputValue || '';
      if (opts.placeholder) input.placeholder = opts.placeholder;
      bodyEl.appendChild(input);
    }
    box.appendChild(bodyEl);

    const actions = document.createElement('div');
    actions.className = 'dialog-actions';
    const cancelBtn = document.createElement('button');
    cancelBtn.type = 'button';
    cancelBtn.className = 'dialog-btn dialog-cancel';
    cancelBtn.textContent = opts.cancelLabel || 'キャンセル';
    const okBtn = document.createElement('button');
    okBtn.type = 'button';
    okBtn.className = 'dialog-btn dialog-confirm' + (opts.danger ? ' danger' : '');
    okBtn.textContent = opts.confirmLabel || 'OK';
    actions.append(cancelBtn, okBtn);
    box.appendChild(actions);

    host.appendChild(box);
    document.body.appendChild(host);

    let done = false;
    function cleanup() {
      document.removeEventListener('keydown', onKey, true);
      host.remove();
      if (prevFocus && typeof prevFocus.focus === 'function' && prevFocus.isConnected) {
        prevFocus.focus();
      }
    }
    function settle(val) {
      if (done) return;
      done = true;
      cleanup();
      resolve(val);
    }
    const onCancel = () => settle(opts.withInput ? null : false);
    const onConfirm = () => settle(opts.withInput ? (input ? input.value : '') : true);

    cancelBtn.addEventListener('click', onCancel);
    okBtn.addEventListener('click', onConfirm);
    host.addEventListener('mousedown', (e) => { if (e.target === host) onCancel(); });

    function focusables() {
      return Array.from(box.querySelectorAll('button, input, [tabindex]:not([tabindex="-1"])'))
        .filter((el) => !el.disabled && el.offsetParent !== null);
    }
    function onKey(e) {
      if (e.key === 'Escape') { e.stopPropagation(); e.preventDefault(); onCancel(); return; }
      if (e.key === 'Enter') {
        // 入力欄フォーカス中の Enter は確定。ボタン上の Enter はネイティブ動作に任せる。
        if (input && document.activeElement === input) {
          e.stopPropagation(); e.preventDefault(); onConfirm();
        }
        return;
      }
      if (e.key === 'Tab') {
        const f = focusables();
        if (!f.length) return;
        const first = f[0], last = f[f.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      }
    }
    document.addEventListener('keydown', onKey, true);

    (input || okBtn).focus();
    if (input) input.select();
  });
}

// 確認ダイアログ。OK で true / キャンセル・Esc・背面クリックで false を返す。
export function confirmDialog({
  title = '確認', message = '', confirmLabel = 'OK', cancelLabel = 'キャンセル', danger = false,
} = {}) {
  return openDialog({ title, message, confirmLabel, cancelLabel, danger, withInput: false });
}

// 入力ダイアログ。OK で入力文字列 / キャンセル・Esc・背面クリックで null を返す。
export function promptDialog({
  title = '入力', message = '', label = '', value = '', placeholder = '',
  confirmLabel = 'OK', cancelLabel = 'キャンセル',
} = {}) {
  const msg = [message, label].filter(Boolean).join('\n');
  return openDialog({ title, message: msg, confirmLabel, cancelLabel, withInput: true, inputValue: value, placeholder });
}

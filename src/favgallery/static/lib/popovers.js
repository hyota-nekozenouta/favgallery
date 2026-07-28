// ポップオーバー: ⚙ オプションポップオーバー。
// (旧「リスト追加ポップオーバー」は お気に入り★ ワンタップ化に伴い廃止)
import { $ } from 'dom';
import { loadCookieStatus } from 'cookies';

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

// 漫画リーダー (3 ペインのスライドトラック + RTL ドラッグ/タップ/キーボードナビ)。
// (Phase 4B: main.js から分離。state のみ依存のリーフ)
import { state } from 'state';

state.reader = { bookId: null, pages: [], pos: 0 };

// Warm the browser cache for upcoming pages so navigation feels instant.
// Pages are served with immutable cache headers, so a warmed image is reused on real nav.
const _prefetchCache = new Set();
function prefetchPageImages(startPos, count, direction) {
  const { pages } = state.reader;
  if (!pages || !pages.length) return;
  const step = direction >= 0 ? 1 : -1;
  const targets = [];
  for (let k = 1; k <= count; k++) targets.push(startPos + step * k);
  targets.push(startPos - step); // one behind, for quick back-nav
  for (const i of targets) {
    if (i < 0 || i >= pages.length) continue;
    const rel = pages[i].rel_path;
    if (_prefetchCache.has(rel)) continue;
    _prefetchCache.add(rel);
    const im = new Image();
    im.src = `/api/media/${rel}`;
  }
}

export async function openReader(bookId) {
  const r = await fetch(`/api/books/${bookId}`);
  const data = await r.json();
  state.reader = { bookId, pages: data.pages, pos: 0 };
  const modal = document.getElementById('readerModal');
  modal.classList.remove('hidden');
  modal.classList.add('flex');
  paintPanes();              // also recenters the track
  prefetchPageImages(0, 3, 1);
}

export function closeReader() {
  const modal = document.getElementById('readerModal');
  modal.classList.add('hidden');
  modal.classList.remove('flex');
  _readerResetTrack();
  _prefetchCache.clear();
}

// --- Reader slide track ---------------------------------------------------
// Manga RTL: drag RIGHT = next page, drag LEFT = prev. Pane layout (left->right)
// = [next, current, prev]; rest state shows the centered current pane.
function _readerTrackEl() { return document.getElementById('readerTrack'); }
function _readerW() {
  const vp = document.getElementById('readerViewport');
  return (vp && vp.clientWidth) || window.innerWidth;
}
function _readerSetX(px, animate) {
  const t = _readerTrackEl();
  if (!t) return;
  t.classList.toggle('reader-animating', !!animate);
  t.style.transform = `translateX(${px}px)`;
}
function _readerResetTrack() { _readerSetX(-_readerW(), false); }

// Paint the 3 panes from state.reader for the current pos, then snap the track
// back to centered (no animation). RTL slot mapping: 0=next, 1=current, 2=prev.
function paintPanes() {
  const { pages, pos } = state.reader;
  const imgs = _readerTrackEl()?.querySelectorAll('img[data-slot]');
  if (!imgs) return;
  const srcFor = (i) => (i >= 0 && i < pages.length) ? `/api/media/${pages[i].rel_path}` : '';
  const map = [pos + 1, pos, pos - 1];
  imgs.forEach((img, slot) => {
    const want = srcFor(map[slot]);
    if (want) { if (img.getAttribute('src') !== want) img.src = want; }
    else img.removeAttribute('src');
  });
  document.getElementById('readerPageNum').textContent = pages.length ? `${pos + 1} / ${pages.length}` : '0 / 0';
  _readerResetTrack();
}

function readerGo(delta) {
  const n = state.reader.pages.length;
  if (n === 0) return;
  const prev = state.reader.pos;
  state.reader.pos = Math.max(0, Math.min(n - 1, prev + delta));
  paintPanes();
  if (state.reader.pos !== prev) prefetchPageImages(state.reader.pos, 3, delta >= 0 ? 1 : -1);
}

// Reader keyboard — RTL: ArrowLeft = next, ArrowRight = prev (manga). Flip for LTR.
document.addEventListener('keydown', (e) => {
  if (document.getElementById('readerModal').classList.contains('hidden')) return;
  if (e.key === 'Escape') { closeReader(); e.preventDefault(); }
  if (e.key === 'ArrowLeft') { readerGo(1); e.preventDefault(); }    // RTL: left = next
  if (e.key === 'ArrowRight') { readerGo(-1); e.preventDefault(); }  // RTL: right = prev
});

// Reader drag — Pointer Events unify mouse + touch with finger-follow slide.
(function() {
  const COMMIT_RATIO = 0.18, AXIS_LOCK = 8, TAP_MAX = 10, ANIM_MS = 320;
  let dragging = false, startX = 0, startY = 0, lastDX = 0, axis = null, moved = false, pid = null, W = 0;
  let animToken = 0;
  const vp = () => document.getElementById('readerViewport');
  const isOpen = () => !document.getElementById('readerModal').classList.contains('hidden');

  function canGo(delta) {
    const { pos, pages } = state.reader;
    const target = pos + delta;
    return target >= 0 && target < pages.length;
  }

  function onDown(e) {
    if (!isOpen()) return;
    animToken++;             // invalidate any in-flight commit animation
    dragging = true; moved = false; axis = null;
    startX = e.clientX; startY = e.clientY; lastDX = 0; pid = e.pointerId; W = _readerW();
    try { vp().setPointerCapture(pid); } catch (_) {}
    _readerTrackEl()?.classList.remove('reader-animating');  // follow finger 1:1
  }

  function onMove(e) {
    if (!dragging) return;
    const dx = e.clientX - startX, dy = e.clientY - startY;
    if (axis === null && (Math.abs(dx) > AXIS_LOCK || Math.abs(dy) > AXIS_LOCK)) {
      axis = Math.abs(dx) > Math.abs(dy) ? 'x' : 'y';
    }
    if (axis === 'y') return;
    if (axis === 'x') moved = true;
    lastDX = dx;
    // Rubber-band when there's no page to reveal that way
    // (drag right reveals next, drag left reveals prev).
    let eff = dx;
    if ((dx > 0 && !canGo(1)) || (dx < 0 && !canGo(-1))) eff = dx * 0.25;
    _readerSetX(-W + eff, false);
  }

  function onUp(e) {
    if (!dragging) return;
    dragging = false;
    try { vp().releasePointerCapture(pid); } catch (_) {}
    const dx = lastDX;
    if (!moved || Math.abs(dx) < TAP_MAX) { tapNav(e.clientX); return; }
    if (axis === 'x' && Math.abs(dx) >= W * COMMIT_RATIO) {
      commitTurn(dx > 0 ? 1 : -1);   // RTL: drag right = next, drag left = prev
    } else {
      snapBack();
    }
  }

  function commitTurn(delta) {
    if (!canGo(delta)) { snapBack(); return; }
    const t = _readerTrackEl();
    const target = delta > 0 ? 0 : -2 * W;   // reveal left (next) or right (prev) pane
    const myToken = ++animToken;
    let finished = false;
    const finish = () => {
      if (finished || myToken !== animToken) return;
      finished = true;
      t.removeEventListener('transitionend', finish);
      readerGo(delta);   // update pos + repaint panes + recenter (no transition)
    };
    t.addEventListener('transitionend', finish);
    setTimeout(finish, ANIM_MS);   // fallback if transitionend is missed
    _readerSetX(target, true);
  }

  function snapBack() { animToken++; _readerSetX(-W, true); }

  // RTL click zones: left half = next, right half = prev.
  function tapNav(clientX) {
    const w = _readerW();
    if (clientX < w / 2) readerGo(1);   // left = next
    else readerGo(-1);                  // right = prev
  }

  const v = vp();
  if (v) {
    v.addEventListener('pointerdown', onDown);
    v.addEventListener('pointermove', onMove);
    v.addEventListener('pointerup', onUp);
    v.addEventListener('pointercancel', () => { if (dragging) { dragging = false; snapBack(); } });
  }
})();

// onclick=closeReader() の module 化代替 (Phase 4 / module scope はグローバル非公開)
document.getElementById('readerCloseBtn')?.addEventListener('click', () => closeReader());

// DOM ショートカット + HTML エスケープ。副作用なしの純粋ヘルパー (Phase 4B 分離)。
export const $ = (sel) => document.querySelector(sel);
export const $$ = (sel) => Array.from(document.querySelectorAll(sel));
export const escapeHtml = (s) => (s ?? '').toString()
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;').replace(/'/g, '&#39;');

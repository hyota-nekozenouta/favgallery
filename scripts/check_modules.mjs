// import 漏れ静的検出（check_load の穴埋め）。
// check_load はグラフを実ロードするが、クリックハンドラ等「遅延実行されるコード内のみ」で
// 使う free 変数は load 時に発火しないため拾えない。このチェッカーは各モジュールの
// 「他モジュールの export を import せずに参照している箇所」を静的に検出する。
//
// 仕組み: 各 lib/*.js の export 名と「利用可能な名前」(import + モジュール直下の宣言) を
// 集計 → あるモジュールが「他モジュールの export 名」を利用可能でないのに識別子として
// 参照していたら import 漏れ候補として報告。クロスモジュール参照は openLightbox /
// fetchPosts のような distinctive な関数名なので、短いローカル変数との誤検出は低い。
//
// 使い方: node scripts/check_modules.mjs   (exit 0 = OK / 非 0 = import 漏れ候補あり)

import { readFileSync, readdirSync, existsSync } from "node:fs";
import { join, dirname, basename } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const staticDir = join(root, "src", "favgallery", "static");
const libDir = join(staticDir, "lib");

if (!existsSync(libDir)) { console.log("(no lib/ dir)"); process.exit(0); }

const files = readdirSync(libDir).filter((n) => n.endsWith(".js"));

// コメント/文字列を雑に除去（識別子スキャンの誤検出を減らす）
function stripNoise(src) {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, " ")       // block comments
    .replace(/(^|[^:])\/\/[^\n]*/g, "$1 ")   // line comments (URL の // は概ね : 直後)
    .replace(/`(?:\\.|[^`\\])*`/g, "``")      // template literals
    .replace(/'(?:\\.|[^'\\])*'/g, "''")      // single-quoted
    .replace(/"(?:\\.|[^"\\])*"/g, '""');     // double-quoted
}

const mod = {}; // file -> { exports:Set, available:Set, code:string }
for (const f of files) {
  const raw = readFileSync(join(libDir, f), "utf-8");
  const code = stripNoise(raw);
  const exports = new Set();
  const available = new Set();

  // export 宣言
  for (const m of code.matchAll(/\bexport\s+(?:async\s+)?function\s+([A-Za-z0-9_$]+)/g)) exports.add(m[1]);
  for (const m of code.matchAll(/\bexport\s+(?:const|let|var|class)\s+([A-Za-z0-9_$]+)/g)) exports.add(m[1]);
  for (const m of code.matchAll(/\bexport\s*\{([^}]*)\}/g)) {
    for (const part of m[1].split(",")) {
      const name = part.trim().split(/\s+as\s+/)[0].trim();
      if (name) exports.add(name);
    }
  }

  // import で入ってくるローカル束縛名
  for (const m of code.matchAll(/\bimport\s+([A-Za-z0-9_$]+)\s*,?\s*(?:\{[^}]*\})?\s*from/g)) available.add(m[1]); // default
  for (const m of code.matchAll(/\bimport\s*\*\s*as\s+([A-Za-z0-9_$]+)\s+from/g)) available.add(m[1]); // namespace
  for (const m of code.matchAll(/\bimport\s*\{([^}]*)\}\s*from/g)) {
    for (const part of m[1].split(",")) {
      const name = part.trim().split(/\s+as\s+/).pop().trim(); // `a as b` -> local b
      if (name) available.add(name);
    }
  }

  // モジュール直下 + 任意スコープの宣言 (free 変数判定を緩め: ローカル宣言も available 扱い)
  for (const m of code.matchAll(/\b(?:async\s+)?function\s+([A-Za-z0-9_$]+)/g)) available.add(m[1]);
  for (const m of code.matchAll(/\b(?:const|let|var)\s+([A-Za-z0-9_$]+)/g)) available.add(m[1]);
  for (const m of code.matchAll(/\bclass\s+([A-Za-z0-9_$]+)/g)) available.add(m[1]);

  mod[f] = { exports, available, code };
}

// export 名 -> その export を持つファイル群
const exportOwners = {};
for (const f of files) for (const e of mod[f].exports) (exportOwners[e] ||= []).push(f);

let failed = false;
for (const f of files) {
  const { available, code, exports } = mod[f];
  const flagged = new Set();
  for (const [name, owners] of Object.entries(exportOwners)) {
    if (owners.includes(f)) continue;        // 自分が export している
    if (available.has(name)) continue;        // import 済み or ローカル宣言済み
    if (exports.has(name)) continue;
    // f のコード中で識別子として使われているか (プロパティアクセス .name は除外)
    const re = new RegExp(`(?<![.\\w$])${name.replace(/[$]/g, "\\$")}(?![\\w$])`);
    if (re.test(code)) flagged.add(`${name} (export of ${owners.join("/")})`);
  }
  if (flagged.size) {
    failed = true;
    console.error(`✗ ${f}: import 漏れ候補 — ${[...flagged].join(", ")}`);
  } else {
    console.log(`✓ ${f}`);
  }
}

process.exit(failed ? 1 : 0);

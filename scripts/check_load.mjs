// モジュールグラフのロード検証ハーネス（Phase 4B の ReferenceError 検出網）
// node --check は「ファイル単体の構文」しか見ないため、import 漏れ / export 名ミス /
// 解決不能な specifier は通ってしまう（本番 = Cloudflare 4h キャッシュ越しで初めて壊れる）。
// このハーネスは Node 本物の module linker で graph を実ロードし、それらを load 時に捕える。
//
// 仕組み:
//  1. index.html の <script type="importmap"> を読み、bare specifier → lib ファイル名へ
//  2. lib/*.js を一時ディレクトリへコピー。bare import を相対パス (./file.js) へ書換え
//     (Node は HTML import map を解さないため。?v= クエリは剥がす)
//  3. ブラウザ global (document/window/IntersectionObserver…) を万能 Proxy でスタブ
//  4. エントリ (main.js) を import() → linker が graph 全体を解決。以下を捕捉:
//     - "does not provide an export named X" (export 漏れ / 名前ミス)
//     - "Cannot find module" (解決不能 specifier)
//     - トップレベル評価 + 起動 IIFE 実行中の "X is not defined" (import 漏れ free 変数)
//  ※ クリックハンドラ等「遅延実行されるコード内のみ」の free 変数は load では発火しない
//     (そこは check_modules.mjs のヒューリスティック + 規律で補完)
//
// 使い方: node scripts/check_load.mjs   (exit 0 = OK / 非 0 = ロード失敗)

import {
  readFileSync, writeFileSync, mkdtempSync, readdirSync, existsSync, rmSync,
} from "node:fs";
import { join, dirname, basename } from "node:path";
import { tmpdir } from "node:os";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const staticDir = join(root, "src", "favgallery", "static");
const libDir = join(staticDir, "lib");

if (!existsSync(libDir)) {
  console.log("(no lib/ dir — nothing to load-check)");
  process.exit(0);
}

// 1) import map (無ければ空 = bare 書換えなし。単一 main.js の現状でも動く)
const html = readFileSync(join(staticDir, "index.html"), "utf-8");
const mapMatch = html.match(/<script\s+type=["']importmap["']\s*>([\s\S]*?)<\/script>/i);
const specToFile = {};
if (mapMatch) {
  let imports = {};
  try {
    imports = JSON.parse(mapMatch[1]).imports || {};
  } catch (e) {
    console.error("✗ importmap の JSON 解析に失敗:", e.message);
    process.exit(1);
  }
  for (const [spec, url] of Object.entries(imports)) {
    specToFile[spec] = basename(String(url).split("?")[0]); // "/static/lib/state.js?v=.." -> "state.js"
  }
}

// 2) 一時ディレクトリへ bare specifier を相対に書換えてコピー
const tmp = mkdtempSync(join(tmpdir(), "fgcheck-"));
const libFiles = readdirSync(libDir).filter((n) => n.endsWith(".js"));
const rewriteSpec = (full, pre, q, spec) =>
  specToFile[spec] ? `${pre}${q}./${specToFile[spec]}${q}` : full;
for (const f of libFiles) {
  let src = readFileSync(join(libDir, f), "utf-8");
  // `from 'spec'` / `import 'spec'` / `export {..} from 'spec'` の bare specifier を相対化
  src = src.replace(/((?:from|import)\s*)(["'])([^"']+)\2/g, rewriteSpec);
  writeFileSync(join(tmp, f), src, "utf-8");
}

// 3) ブラウザ global スタブ（万能 Proxy: 呼べる・new できる・任意プロパティで自身を返す）
const U = new Proxy(function stub() {}, {
  get(_t, p) {
    if (p === "then") return undefined;             // thenable 扱いされない (await でハングしない)
    if (p === Symbol.toPrimitive) return () => 0;   // 数値/文字列強制で例外を出さない
    if (p === Symbol.iterator) return undefined;    // 非 iterable (Array.from -> [])
    if (p === "length") return 0;
    return U;
  },
  apply: () => U,
  construct: () => U,
  set: () => true,
  has: () => true,
});
const BROWSER_GLOBALS = [
  "document", "navigator", "location", "localStorage", "sessionStorage",
  "IntersectionObserver", "MutationObserver", "ResizeObserver", "FileReader",
  "Image", "getComputedStyle", "matchMedia", "alert", "confirm", "prompt",
  "requestAnimationFrame", "cancelAnimationFrame", "scrollTo",
];
const defineGlobal = (k, v) => {
  // 一部 global (navigator 等) は Node 24 で読取専用 getter。set 不能なら
  // Node 実体をそのまま使う（navigator.userAgent 等は実在するので無害）。
  try { globalThis[k] = v; return; } catch {}
  try { Object.defineProperty(globalThis, k, { value: v, configurable: true, writable: true }); } catch {}
};
for (const k of BROWSER_GLOBALS) defineGlobal(k, U);
globalThis.window = globalThis;
defineGlobal("innerWidth", 1280);
defineGlobal("innerHeight", 800);
defineGlobal("scrollY", 0);
defineGlobal("addEventListener", () => {});
defineGlobal("removeEventListener", () => {});
defineGlobal("fetch", () => Promise.resolve(U)); // 起動 IIFE が await fetch しても解決させる

// 起動 IIFE 内の非同期 free 変数も拾う
const asyncErrors = [];
process.on("unhandledRejection", (e) => asyncErrors.push(e));

function classify(err) {
  const msg = String(err && err.message || err);
  if (/does not provide an export named/.test(msg)) return ["export 漏れ/名前ミス", msg];
  if (/Cannot find (module|package)/.test(msg)) return ["解決不能 import", msg];
  if (/is not defined/.test(msg)) return ["import 漏れ (free 変数)", msg];
  return ["ロード時エラー", msg];
}

const entryFile = specToFile["main"] || (libFiles.includes("main.js") ? "main.js" : libFiles[0]);
const entryUrl = pathToFileURL(join(tmp, entryFile)).href;

let failed = false;
try {
  await import(entryUrl);
  // 起動 IIFE の同期パートが走る猶予を与える（await 連鎖は U が即解決なのでマイクロタスクのみ）
  await new Promise((r) => setTimeout(r, 50));
} catch (err) {
  failed = true;
  const [kind, msg] = classify(err);
  console.error(`✗ [${kind}] ${msg}`);
}
for (const e of asyncErrors) {
  // 起動 IIFE 由来の free 変数 / export 漏れのみ拾う（U スタブ起因の雑音は除外）
  const [kind, msg] = classify(e);
  if (kind === "ロード時エラー") continue;
  failed = true;
  console.error(`✗ [${kind}] (起動処理中) ${msg}`);
}

try { rmSync(tmp, { recursive: true, force: true }); } catch {}

if (failed) {
  process.exit(1);
} else {
  console.log(`✓ module graph loads cleanly (entry: ${entryFile}, ${libFiles.length} module(s))`);
  process.exit(0);
}

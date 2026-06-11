// JS 構文チェックハーネス（JS テスト不在の安全網・2026-06-10 Phase 0）
// - index.html の inline <script>（src= なし）を抽出して node --check
// - src/favgallery/static/lib/*.js（モジュール分割後）を node --check
// 使い方: node scripts/check_js.mjs   （exit 0 = OK / 非 0 = 構文エラー）
import { readFileSync, writeFileSync, unlinkSync, readdirSync, existsSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const staticDir = join(root, "src", "favgallery", "static");
let failed = false;

function check(file, asModule = false) {
  // --input-type=module は --check と併用不可 (Node 制約) → .mjs 一時コピーで
  // 拡張子ベースの module パースをさせる
  let target = file;
  let tmp = null;
  if (asModule) {
    tmp = file + ".check.mjs";
    writeFileSync(tmp, readFileSync(file, "utf-8"), "utf-8");
    target = tmp;
  }
  const r = spawnSync(process.execPath, ["--check", target], { encoding: "utf-8" });
  if (tmp) unlinkSync(tmp);
  if (r.status !== 0) {
    failed = true;
    console.error(`✗ ${file}\n${r.stderr}`);
  } else {
    console.log(`✓ ${file}`);
  }
}

// 1) index.html の inline script (src= なし かつ JS 種別のものだけ)
//    type="importmap" 等の JSON ブロックは JS ではないので除外する
const html = readFileSync(join(staticDir, "index.html"), "utf-8");
const JS_TYPES = new Set(["", "module", "text/javascript", "application/javascript"]);
const scripts = [];
for (const m of html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/g)) {
  const attrs = m[1];
  if (/\bsrc\s*=/.test(attrs)) continue; // 外部スクリプト
  const typeMatch = attrs.match(/\btype\s*=\s*["']([^"']*)["']/i);
  const type = typeMatch ? typeMatch[1].toLowerCase() : "";
  if (!JS_TYPES.has(type)) continue; // importmap / application/json 等
  scripts.push(m[2]);
}
if (scripts.length) {
  const tmp = join(root, ".tmp_check_inline.js");
  writeFileSync(tmp, scripts.join("\n;\n"), "utf-8");
  check(tmp);
  unlinkSync(tmp);
}

// 2) 分割後モジュール（存在すれば）
const libDir = join(staticDir, "lib");
if (existsSync(libDir)) {
  for (const f of readdirSync(libDir).filter((n) => n.endsWith(".js"))) {
    check(join(libDir, f), true);
  }
}

process.exit(failed ? 1 : 0);

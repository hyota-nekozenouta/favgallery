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
  const args = asModule ? ["--input-type=module", "--check", file] : ["--check", file];
  const r = spawnSync(process.execPath, args, { encoding: "utf-8" });
  if (r.status !== 0) {
    failed = true;
    console.error(`✗ ${file}\n${r.stderr}`);
  } else {
    console.log(`✓ ${file}`);
  }
}

// 1) index.html の inline script
const html = readFileSync(join(staticDir, "index.html"), "utf-8");
const scripts = [...html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g)].map((m) => m[1]);
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

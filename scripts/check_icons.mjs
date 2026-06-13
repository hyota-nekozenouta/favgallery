// スプライト未定義の #ic-* 参照を静的検出する。
// 参照元: index.html の <use href="#ic-NAME"> と、HTML/JS 中の icon('NAME').
// 定義元: index.html の <symbol id="ic-NAME">。未定義参照があれば exit 1。
import { readFileSync, readdirSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const staticDir = join(root, "src", "favgallery", "static");
const html = readFileSync(join(staticDir, "index.html"), "utf-8");

const defined = new Set([...html.matchAll(/<symbol\s+id="ic-([a-z0-9-]+)"/g)].map((m) => m[1]));
const refs = new Map(); // name -> where[]
const add = (name, where) => {
  if (!refs.has(name)) refs.set(name, []);
  refs.get(name).push(where);
};

for (const m of html.matchAll(/href="#ic-([a-z0-9-]+)"/g)) add(m[1], "index.html(<use>)");
const libDir = join(staticDir, "lib");
const files = ["index.html", ...readdirSync(libDir).map((n) => `lib/${n}`)];
for (const rel of files) {
  const src = readFileSync(join(staticDir, rel), "utf-8");
  for (const m of src.matchAll(/\bicon\(\s*['"]([a-z0-9-]+)['"]/g)) add(m[1], `${rel}(icon())`);
}

let failed = false;
for (const [name, where] of refs) {
  if (!defined.has(name)) {
    console.error(`✗ "#ic-${name}" 未定義 — 参照: ${where.join(", ")}`);
    failed = true;
  }
}
if (!failed) console.log(`✓ アイコン参照 ${refs.size} 種すべて解決 (定義 ${defined.size} 種)`);
process.exit(failed ? 1 : 0);

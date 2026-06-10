/** Tailwind v3 config — Phase 3 (2026-06-10).
 * 生成: npx tailwindcss@3.4.17 -c tailwind.config.js -i scripts/tailwind.input.css -o src/favgallery/static/style.css --minify
 * content の lib glob は Phase 4 (JS モジュール分割) の先回り — JS テンプレ内の
 * クラスが purge で消えないため。
 */
module.exports = {
  content: [
    "./src/favgallery/static/index.html",
    "./src/favgallery/static/lib/**/*.js",
  ],
  theme: { extend: {} },
};

/** Tailwind v3 config — Phase 3 (2026-06-10) / Phase 5 デザイン刷新で拡張.
 * 生成: npx tailwindcss@3.4.17 -c tailwind.config.js -i scripts/tailwind.input.css -o src/favgallery/static/style.css --minify
 * content の lib glob: JS テンプレ内のクラスが purge で消えないため。
 *
 * デザイン刷新の核 (Phase 5 / 2026-06-10・v2 X-like):
 * indigo パレットを X ブルー (#1d9bf0) に再定義 — マークアップに散在する
 * 既存 indigo-* クラスを 1 行も書き換えずに、アプリ全体の差し色を一斉切替する
 * テーマ側レバー (ひょーたさんフィードバック「Xっぽい雰囲気」2026-06-10)。
 */
module.exports = {
  content: [
    "./src/favgallery/static/index.html",
    "./src/favgallery/static/lib/**/*.js",
  ],
  theme: {
    extend: {
      colors: {
        indigo: {
          50:  "#eaf6fe",
          100: "#d3ecfd",
          200: "#a7d9fb",
          300: "#8ecdf8",
          400: "#4db2f5",
          500: "#3aa9f4",
          600: "#1d9bf0",
          700: "#1a8cd8",
          800: "#1471ae",
          900: "#0f5585",
          950: "#0a3a5c",
        },
      },
      fontFamily: {
        display: ['"Fraunces"', "Georgia", "serif"],
      },
    },
  },
};

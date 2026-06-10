/** Tailwind v3 config — Phase 3 (2026-06-10) / Phase 5 デザイン刷新で拡張.
 * 生成: npx tailwindcss@3.4.17 -c tailwind.config.js -i scripts/tailwind.input.css -o src/favgallery/static/style.css --minify
 * content の lib glob: JS テンプレ内のクラスが purge で消えないため。
 *
 * デザイン刷新の核 (Phase 5 / 2026-06-10):
 * indigo パレットを「ギャラリー・ゴールド」に再定義 — マークアップに散在する
 * 既存 indigo-* クラスを 1 行も書き換えずに、アプリ全体の差し色を
 * 「夜の美術館のスポットライト」に一斉切替するためのテーマ側レバー。
 */
module.exports = {
  content: [
    "./src/favgallery/static/index.html",
    "./src/favgallery/static/lib/**/*.js",
  ],
  theme: {
    extend: {
      colors: {
        // zinc も warm 灰 (stone 系) に再定義 — 夜の美術館の暖かい暗色へ全体を
        // 一斉シフト (マークアップ無変更のテーマ側レバー)
        zinc: {
          50:  "#fafaf9",
          100: "#f5f5f4",
          200: "#e7e5e4",
          300: "#d6d3d1",
          400: "#a8a29e",
          500: "#78716c",
          600: "#57534e",
          700: "#44403c",
          800: "#292524",
          900: "#1c1917",
          950: "#0e0c0a",
        },
        indigo: {
          50:  "#faf6ee",
          100: "#f3ead8",
          200: "#ecd9b8",
          300: "#e0c293",
          400: "#cfa86b",
          500: "#b98c4f",
          600: "#97713c",
          700: "#7a5a30",
          800: "#5c4325",
          900: "#43311a",
          950: "#2a1e0f",
        },
      },
      fontFamily: {
        display: ['"Fraunces"', "Georgia", "serif"],
      },
    },
  },
};

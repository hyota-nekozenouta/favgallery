// アイコン生成の唯一の口。SVG スプライト (#ic-NAME) を参照する <svg> 文字列を返す。
// 色・線は .icon CSS (currentColor) が制御するので、無彩色アイコン＋親の差し色が自動成立。
export function icon(name, extraClass = "") {
  const cls = extraClass ? `icon ${extraClass}` : "icon";
  return `<svg class="${cls}" aria-hidden="true"><use href="#ic-${name}"/></svg>`;
}

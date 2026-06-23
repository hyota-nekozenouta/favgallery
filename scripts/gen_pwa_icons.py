"""PWA アイコン生成 (黒背景 + 白文字 FG を 4 サイズ書き出し).

実行: `uv run python scripts/gen_pwa_icons.py`

出力先: `src/favgallery/static/icons/`
- icon-192.png (Android 標準)
- icon-512.png (Android / Windows)
- icon-512-maskable.png (Android adaptive icon・中央 60% 安全領域)
- apple-touch-icon.png (180x180・iOS 専用・角丸なし不透明背景)

フォントは Fraunces Bold (既存 wordmark と整合) を優先探索し、無ければ
Segoe UI Bold / Arial Bold / Pillow default の順にフォールバックする。
文字を高さ 50% で描画するので、全 4 サイズで中央 60% 安全領域に収まる。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT_DIR = Path(__file__).resolve().parent.parent / "src" / "favgallery" / "static" / "icons"

SIZES: list[tuple[str, int]] = [
    ("icon-192.png", 192),
    ("icon-512.png", 512),
    ("icon-512-maskable.png", 512),
    ("apple-touch-icon.png", 180),
]

FONT_CANDIDATES = [
    "C:/Windows/Fonts/Fraunces-Bold.ttf",
    "/usr/share/fonts/truetype/fraunces/Fraunces-Bold.ttf",
    "/Library/Fonts/Fraunces-Bold.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]


def _load_font(size: int) -> ImageFont.ImageFont:
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def make_icon(size: int, out_path: Path) -> None:
    img = Image.new("RGB", (size, size), "#000000")
    draw = ImageDraw.Draw(img)
    text = "FG"
    font = _load_font(int(size * 0.5))
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    x = (size - text_w) // 2 - bbox[0]
    y = (size - text_h) // 2 - bbox[1]
    draw.text((x, y), text, fill="#ffffff", font=font)
    img.save(out_path, format="PNG", optimize=True)
    print(f"  wrote {out_path.name} ({size}x{size})")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"writing {len(SIZES)} icons → {OUT_DIR}")
    for filename, size in SIZES:
        make_icon(size, OUT_DIR / filename)


if __name__ == "__main__":
    main()

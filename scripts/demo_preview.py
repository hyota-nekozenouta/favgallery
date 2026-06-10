"""デザインプレビュー用デモサーバー (Phase 5a / 2026-06-10)。

架空のデモライブラリ (PIL 生成画像 80 枚 + 漫画 6 冊) を .demo-preview/ に組んで
ローカル起動する。本番・実データには一切触れない。
実行: `uv run python scripts/demo_preview.py` → http://127.0.0.1:8123
"""

from __future__ import annotations

import colorsys
import json
import random
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PIL import Image, ImageDraw  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent / ".demo-preview"
LIB = ROOT / "library"

AUTHORS = [
    ("aoi_paints", "葵 / 油彩とデジタル"),
    ("kuro_streetphoto", "クロ街撮り"),
    ("hanon_illust", "はのん"),
    ("midori_3dcg", "ミドリCG"),
    ("yoru_no_neko", "夜猫"),
]
TAGS = ["イラスト", "風景", "ポートレート", "CG", "ラフ", "夜景"]
RATIOS = [(3, 4), (2, 3), (1, 1), (16, 9), (9, 16), (4, 5), (3, 2)]
random.seed(20260610)


def _art(path: Path, w: int, h: int, seed: int) -> None:
    rnd = random.Random(seed)
    hue = rnd.random()
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img, "RGBA")
    # 縦グラデーション
    top = tuple(int(c * 255) for c in colorsys.hsv_to_rgb(hue, 0.55, 0.32))
    bot = tuple(int(c * 255) for c in colorsys.hsv_to_rgb((hue + 0.08) % 1, 0.45, 0.12))
    for y in range(h):
        t = y / h
        d.line([(0, y), (w, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(top, bot)))
    # 構図: 円・帯・対角線
    for _ in range(rnd.randint(2, 5)):
        kind = rnd.choice(["circle", "band", "diag"])
        ch = (hue + rnd.uniform(0.05, 0.5)) % 1
        col = tuple(int(c * 255) for c in colorsys.hsv_to_rgb(ch, rnd.uniform(0.3, 0.8), rnd.uniform(0.5, 0.95)))
        a = rnd.randint(60, 160)
        if kind == "circle":
            r = rnd.randint(min(w, h) // 6, min(w, h) // 2)
            cx, cy = rnd.randint(0, w), rnd.randint(0, h)
            d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=col + (a,))
        elif kind == "band":
            y0 = rnd.randint(0, h)
            d.rectangle([0, y0, w, y0 + rnd.randint(h // 12, h // 4)], fill=col + (a,))
        else:
            d.polygon([(0, rnd.randint(0, h)), (w, rnd.randint(0, h)), (w, h), (0, h)], fill=col + (a // 2,))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "JPEG", quality=88)


def build() -> None:
    if ROOT.exists():
        shutil.rmtree(ROOT)
    LIB.mkdir(parents=True)

    # ---- posts: 画像 + sidecar JSON (起動時 ingest される) ----
    tid = 1_900_000_000
    for i in range(80):
        author, nick = AUTHORS[i % len(AUTHORS)]
        rw, rh = random.choice(RATIOS)
        w = random.choice([900, 1080, 1200])
        h = int(w * rh / rw)
        tid += random.randint(3, 9)
        rel = f"{author}/{tid}_1.jpg"
        _art(LIB / rel, w, h, seed=tid)
        tag = random.choice(TAGS)
        sidecar = {
            "tweet_id": tid, "num": 1, "author": {"name": author, "nick": nick},
            "content": f"デモ作品 {i + 1} #{tag}",
            "date": f"2026-{random.randint(1, 6):02d}-{random.randint(1, 28):02d} "
                    f"{random.randint(0, 23):02d}:{random.randint(0, 59):02d}:00",
            "type": "photo", "width": w, "height": h, "extension": "jpg",
            "favorite_count": random.randint(10, 4200),
            "view_count": random.randint(500, 90000),
            "sensitive": False, "lang": "ja",
        }
        (LIB / rel).with_suffix(".json").write_text(
            json.dumps(sidecar, ensure_ascii=False), encoding="utf-8"
        )

    # ---- books: 表紙+ページ画像 + DB 行 ----
    from favgallery.db import Database

    db = Database(LIB / "xlikes.sqlite")
    titles = ["夜想曲 1 巻", "硝子の街", "ねこと暮らす", "ラフスケッチ集", "青の時代", "旅の断片"]
    for bi, title in enumerate(titles):
        pages = []
        for pn in range(1, 13):
            rel = f"_books/demo{bi}/{pn:04d}.jpg"
            _art(LIB / rel, 840, 1188, seed=7000 + bi * 50 + pn)
            pages.append((pn, rel, 840, 1188))
        book = db.create_book(title, f"_books/demo{bi}/0001.jpg", len(pages))
        db.add_book_pages(book.id, pages)
        db.set_book_tags(book.id, random.sample(["漫画", "画集", "同人", "資料"], k=2))
    db.close()
    print(f"demo library built: {LIB}")


def main() -> None:
    build()
    import uvicorn

    from favgallery.server import create_app

    app = create_app(library_root=LIB, scan_in_background=False)
    print("preview: http://127.0.0.1:8123")
    uvicorn.run(app, host="127.0.0.1", port=8123, log_level="warning")


if __name__ == "__main__":
    main()

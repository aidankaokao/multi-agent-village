"""產生瀏覽器頁籤 icon（像素小屋）：frontend/public/favicon.svg、favicon.png（32px）、apple-touch-icon.png（180px）。

用法：uv run --with pillow python scripts/gen_favicon.py
"""

from pathlib import Path

from PIL import Image

OUT = Path(__file__).resolve().parent.parent / "frontend" / "public"

# 16×16 像素圖：. 透明
ART = """
................
.......RR.......
......RRRR......
.....RRRRRR.....
....RRRRRRRR....
...RRRRRRRRRR...
..RRRRRRRRRRRR..
.KKKKKKKKKKKKKK.
..WWWWWWWWWWWW..
..WBBWWWWWWBBW..
..WBBWWDDWWBBW..
..WWWWWDDWWWWW..
..WWWWWDDWWWWW..
GGGGGGGDDGGGGGGG
GGGGGGGGGGGGGGGG
................
"""

COLORS = {
    "R": (214, 92, 72),   # 屋頂
    "K": (122, 52, 44),   # 屋簷
    "W": (240, 220, 170), # 牆
    "B": (110, 180, 230), # 窗
    "D": (130, 84, 50),   # 門
    "G": (92, 168, 84),   # 草地
}

rows = [r for r in ART.strip().splitlines()]
assert len(rows) == 16 and all(len(r) == 16 for r in rows)


def svg() -> str:
    rects = [
        f'<rect x="{x}" y="{y}" width="1" height="1" fill="#{"%02x%02x%02x" % COLORS[c]}"/>'
        for y, r in enumerate(rows) for x, c in enumerate(r) if c != "."
    ]
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" shape-rendering="crispEdges">'
            + "".join(rects) + "</svg>\n")


def png(size: int, bg: tuple[int, int, int] | None = None) -> Image.Image:
    img = Image.new("RGBA", (16, 16), (*bg, 255) if bg else (0, 0, 0, 0))
    for y, r in enumerate(rows):
        for x, c in enumerate(r):
            if c != ".":
                img.putpixel((x, y), (*COLORS[c], 255))
    return img.resize((size, size), Image.NEAREST)


OUT.mkdir(parents=True, exist_ok=True)
(OUT / "favicon.svg").write_text(svg())
png(32).save(OUT / "favicon.png")
png(180, bg=(40, 44, 62)).save(OUT / "apple-touch-icon.png")  # iOS 不吃透明背景
print("已產生", OUT / "favicon.svg", OUT / "favicon.png", OUT / "apple-touch-icon.png")

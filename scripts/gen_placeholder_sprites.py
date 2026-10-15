"""產生村落用的佔位像素角色四向圖（placeholder sprites）。

每個角色輸出一張 sprite sheet：
  - 單格 32x32，3 欄 x 4 列 = 96x128
  - 列順序（RPG Maker 慣例）：0=down, 1=left, 2=right, 3=up
  - 欄順序：0=踏步A, 1=站立, 2=踏步B（走路動畫用 0-1-2-1 循環）

另外輸出 UI 狀態泡泡（頭頂運作中動畫）與一張放大預覽圖。

用法：uv run --with pillow python scripts/gen_placeholder_sprites.py
之後若改用 AI 生成或手繪的正式素材，只要維持相同的 sheet 規格即可直接替換。
"""

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

ROOT = Path(__file__).resolve().parent.parent
OUT_CHAR = ROOT / "frontend/public/assets/sprites/characters"
OUT_UI = ROOT / "frontend/public/assets/sprites/ui"
OUT_PREVIEW = ROOT / "docs/images/sprites_preview.png"

FW = FH = 32
DIRS = ["down", "left", "right", "up"]
OUTLINE = (27, 27, 42, 255)
SKIN = (246, 205, 166, 255)
EYE = (40, 30, 40, 255)
BLUSH = (240, 150, 150, 255)


def hexc(h: str) -> tuple:
    h = h.lstrip("#")
    return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4)) + (255,)


def r(d: ImageDraw.ImageDraw, x0, y0, x1, y1, c):
    d.rectangle([x0, y0, x1, y1], fill=c)


def px(d: ImageDraw.ImageDraw, pts, c):
    for p in pts:
        d.point(p, fill=c)


def add_outline(img: Image.Image) -> Image.Image:
    src = img.load()
    out = img.copy()
    dst = out.load()
    for y in range(FH):
        for x in range(FW):
            if src[x, y][3] != 0:
                continue
            for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if 0 <= nx < FW and 0 <= ny < FH and src[nx, ny][3] != 0:
                    dst[x, y] = OUTLINE
                    break
    return out


def add_shadow(img: Image.Image) -> Image.Image:
    shadow = Image.new("RGBA", (FW, FH), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).ellipse([9, 28, 22, 31], fill=(0, 0, 0, 70))
    shadow.alpha_composite(img)
    return shadow


# ---------------------------------------------------------------- 基本身體


def draw_body(d, view, frame, pal):
    """view: down / left / up（right 由 left 鏡射）"""
    dy = 0 if frame == 1 else 1
    hair, cloth, cloth2, shoe = pal["hair"], pal["cloth"], pal["cloth2"], pal["shoe"]

    # 腿
    if view in ("down", "up"):
        ll = 28 - (1 if frame == 0 else 0)
        rl = 28 - (1 if frame == 2 else 0)
        r(d, 12, 25, 14, ll, cloth2)
        r(d, 12, ll, 14, ll, shoe)
        r(d, 17, 25, 19, rl, cloth2)
        r(d, 17, rl, 19, rl, shoe)
    else:
        if frame == 1:
            r(d, 14, 25, 17, 28, cloth2)
            r(d, 13, 28, 17, 28, shoe)
        else:
            fx, bx = (11, 17) if frame == 0 else (17, 11)
            r(d, bx, 25, bx + 3, 27, cloth2)
            r(d, bx, 27, bx + 3, 27, shoe)
            r(d, fx, 25, fx + 3, 28, cloth2)
            r(d, fx - 1, 28, fx + 3, 28, shoe)

    # 身體
    if view in ("down", "up"):
        r(d, 10, 18 + dy, 21, 25, cloth)
        la = -1 if frame == 0 else 0
        ra = -1 if frame == 2 else 0
        r(d, 8, 19 + dy + la, 9, 23 + dy + la, cloth)
        r(d, 8, 24 + dy + la, 9, 24 + dy + la, SKIN)
        r(d, 22, 19 + dy + ra, 23, 23 + dy + ra, cloth)
        r(d, 22, 24 + dy + ra, 23, 24 + dy + ra, SKIN)
    else:
        r(d, 11, 18 + dy, 20, 25, cloth)
        sx = {0: -2, 1: 0, 2: 2}[frame]
        r(d, 14 + sx, 19 + dy, 16 + sx, 23 + dy, cloth2)
        r(d, 14 + sx, 24 + dy, 16 + sx, 24 + dy, SKIN)

    # 頭
    if view == "down":
        r(d, 10, 6 + dy, 21, 17 + dy, SKIN)
        r(d, 9, 4 + dy, 22, 8 + dy, hair)
        r(d, 9, 9 + dy, 10, 13 + dy, hair)
        r(d, 21, 9 + dy, 22, 13 + dy, hair)
        r(d, 13, 12 + dy, 13, 13 + dy, EYE)
        r(d, 18, 12 + dy, 18, 13 + dy, EYE)
        px(d, [(12, 15 + dy), (19, 15 + dy)], BLUSH)
        r(d, 15, 15 + dy, 16, 15 + dy, (190, 110, 100, 255))
    elif view == "up":
        r(d, 10, 6 + dy, 21, 17 + dy, SKIN)
        r(d, 9, 4 + dy, 22, 16 + dy, hair)
    else:  # left
        r(d, 10, 6 + dy, 20, 17 + dy, SKIN)
        px(d, [(9, 13 + dy)], SKIN)
        r(d, 9, 4 + dy, 21, 8 + dy, hair)
        r(d, 15, 9 + dy, 21, 15 + dy, hair)
        r(d, 12, 12 + dy, 12, 13 + dy, EYE)
        px(d, [(13, 15 + dy)], BLUSH)
    return dy


# ---------------------------------------------------------------- 各角色配件


def acc_poet(d, view, frame, dy):
    beret, quill = hexc("#7a3fa0"), hexc("#f4f4f4")
    r(d, 8, 2 + dy, 21, 5 + dy, beret)
    px(d, [(15, 1 + dy), (16, 1 + dy)], beret)
    if view == "down":
        px(d, [(24, 16 + dy), (24, 17 + dy), (23, 18 + dy), (23, 19 + dy), (25, 15 + dy)], quill)
    elif view == "left":
        px(d, [(12, 16 + dy), (12, 17 + dy), (13, 18 + dy), (11, 15 + dy)], quill)


def acc_jester(d, view, frame, dy):
    red, yel, bell = hexc("#d8343c"), hexc("#f2c230"), hexc("#ffe680")
    for x in range(9, 23):
        px(d, [(x, 5 + dy), (x, 6 + dy)], red if (x // 2) % 2 == 0 else yel)
    px(d, [(9, 4 + dy), (8, 3 + dy), (7, 2 + dy), (8, 4 + dy)], red)
    px(d, [(6, 1 + dy)], bell)
    px(d, [(22, 4 + dy), (23, 3 + dy), (24, 2 + dy), (23, 4 + dy)], yel)
    px(d, [(25, 1 + dy)], bell)
    if view in ("down", "up"):
        for x in range(10, 22):
            if x % 2 == 0:
                px(d, [(x, 18 + dy)], yel)
        r(d, 16, 19 + dy, 21, 25, yel)


def acc_translator(d, view, frame, dy):
    frame_c, lens, book = hexc("#2a2a3a"), hexc("#cfe3ff"), hexc("#8a4b2a")
    if view == "down":
        for x0 in (12, 17):
            r(d, x0, 11 + dy, x0 + 2, 13 + dy, lens)
            px(d, [(x0 + 1, 12 + dy)], EYE)
            px(d, [(x0, 11 + dy), (x0 + 1, 11 + dy), (x0 + 2, 11 + dy)], frame_c)
        px(d, [(15, 12 + dy), (16, 12 + dy)], frame_c)
        r(d, 12, 20 + dy, 19, 23 + dy, book)
        r(d, 15, 20 + dy, 16, 23 + dy, hexc("#f1e6c8"))
    elif view == "left":
        r(d, 10, 11 + dy, 13, 13 + dy, lens)
        px(d, [(12, 12 + dy)], EYE)
        r(d, 10, 11 + dy, 13, 11 + dy, frame_c)
        r(d, 9, 20 + dy, 12, 23 + dy, book)


def acc_mayor(d, view, frame, dy):
    gold, beard, staff, gem = hexc("#f2c230"), hexc("#f4f4f4"), hexc("#7a4e2a"), hexc("#4fd6e0")
    r(d, 10, 2 + dy, 21, 4 + dy, gold)
    px(d, [(10, 1 + dy), (15, 1 + dy), (16, 1 + dy), (21, 1 + dy)], gold)
    px(d, [(15, 3 + dy), (16, 3 + dy)], hexc("#d8343c"))
    if view == "down":
        r(d, 11, 15 + dy, 20, 19 + dy, beard)
        r(d, 13, 20 + dy, 18, 21 + dy, beard)
        r(d, 15, 22 + dy, 16, 25, gold)
        r(d, 25, 8, 25, 28, staff)
        px(d, [(25, 7), (24, 7), (26, 7), (25, 6)], gem)
    elif view == "left":
        r(d, 9, 15 + dy, 14, 19 + dy, beard)
        r(d, 7, 8, 7, 28, staff)
        px(d, [(7, 7), (6, 7), (8, 7), (7, 6)], gem)
    else:
        r(d, 25, 8, 25, 28, staff)
        px(d, [(25, 7), (24, 7), (26, 7), (25, 6)], gem)


def acc_reviewer(d, view, frame, dy):
    cap, visor, badge, band = hexc("#22305a"), hexc("#11182e"), hexc("#f2c230"), hexc("#d8343c")
    r(d, 9, 2 + dy, 22, 6 + dy, cap)
    if view == "down":
        r(d, 9, 7 + dy, 22, 7 + dy, visor)
        px(d, [(15, 4 + dy), (16, 4 + dy)], badge)
        px(d, [(18, 20 + dy), (19, 20 + dy), (18, 21 + dy), (19, 21 + dy)], badge)
        r(d, 8, 21 + dy, 9, 21 + dy, band)
    elif view == "left":
        r(d, 6, 7 + dy, 14, 7 + dy, visor)
        px(d, [(11, 4 + dy)], badge)
        r(d, 14, 21 + dy, 16, 21 + dy, band)


CHARACTERS = {
    # id: (中文名, 調色盤, 配件)
    "poet": ("詩人", dict(hair="#5a3a22", cloth="#3f8f5a", cloth2="#2c6640", shoe="#3a2a20"), acc_poet),
    "jester": ("笑話師", dict(hair="#e08a2c", cloth="#d8343c", cloth2="#7a2a8a", shoe="#3a2a20"), acc_jester),
    "translator": ("翻譯官", dict(hair="#1e1e2a", cloth="#3a6fc4", cloth2="#28477d", shoe="#2a2a2a"), acc_translator),
    "mayor": ("村長", dict(hair="#c8c8c8", cloth="#a8323e", cloth2="#6e1f28", shoe="#2a1a1a"), acc_mayor),
    "reviewer": ("審核員", dict(hair="#3b2a1e", cloth="#2b3a67", cloth2="#1b2442", shoe="#111111"), acc_reviewer),
}


def render_frame(pal, acc, view, frame) -> Image.Image:
    img = Image.new("RGBA", (FW, FH), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    dy = draw_body(d, view, frame, pal)
    acc(d, view, frame, dy)
    return add_shadow(add_outline(img))


def build_sheet(pal_hex, acc) -> Image.Image:
    pal = {k: hexc(v) for k, v in pal_hex.items()}
    sheet = Image.new("RGBA", (FW * 3, FH * 4), (0, 0, 0, 0))
    for row, direction in enumerate(DIRS):
        for col in range(3):
            if direction == "right":
                f = ImageOps.mirror(render_frame(pal, acc, "left", col))
            else:
                f = render_frame(pal, acc, direction, col)
            sheet.alpha_composite(f, (col * FW, row * FH))
    return sheet


# ---------------------------------------------------------------- 頭頂狀態泡泡


def build_status_bubbles() -> Image.Image:
    """16x16 一格。第 0 列：working 動畫 4 格；第 1 列：error / done / thinking / idle(zzz)"""
    S = 16
    sheet = Image.new("RGBA", (S * 4, S * 2), (0, 0, 0, 0))
    white, gray, dark = (255, 255, 255, 255), (170, 170, 190, 255), (40, 40, 60, 255)

    def bubble():
        im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        r(d, 2, 1, 13, 10, white)
        r(d, 1, 2, 14, 9, white)
        px(d, [(4, 11), (5, 11), (3, 12)], white)
        return im, d

    def finish(im):
        src = im.load()
        out = im.copy()
        dst = out.load()
        for y in range(S):
            for x in range(S):
                if src[x, y][3] == 0 and any(
                    0 <= nx < S and 0 <= ny < S and src[nx, ny][3] and src[nx, ny] == white
                    for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1))
                ):
                    dst[x, y] = dark
        return out

    for i in range(4):
        im, d = bubble()
        for j, x in enumerate((3, 7, 11)):
            c = dark if (i == 3 or j == i) else gray
            oy = -1 if j == i else 0
            r(d, x, 5 + oy, x + 1, 6 + oy, c)
        sheet.alpha_composite(finish(im), (i * S, 0))

    im, d = bubble()  # error
    r(d, 7, 2, 8, 6, (216, 52, 60, 255))
    r(d, 7, 8, 8, 8, (216, 52, 60, 255))
    sheet.alpha_composite(finish(im), (0, S))
    im, d = bubble()  # done
    px(d, [(4, 6), (5, 7), (6, 8), (7, 7), (8, 6), (9, 5), (10, 4), (11, 3)], (60, 170, 90, 255))
    px(d, [(4, 5), (5, 6), (6, 7), (7, 6), (8, 5), (9, 4), (10, 3)], (60, 170, 90, 255))
    sheet.alpha_composite(finish(im), (S, S))
    im, d = bubble()  # thinking (?)
    px(d, [(6, 3), (7, 2), (8, 2), (9, 3), (9, 4), (8, 5), (7, 6), (7, 8)], (58, 111, 196, 255))
    sheet.alpha_composite(finish(im), (2 * S, S))
    im, d = bubble()  # idle zzz
    px(d, [(4, 4), (5, 4), (6, 4), (5, 5), (4, 6), (5, 6), (6, 6)], gray)
    px(d, [(8, 3), (9, 3), (10, 3), (9, 4), (8, 5), (9, 5), (10, 5)], dark)
    sheet.alpha_composite(finish(im), (3 * S, S))
    return sheet


def main():
    OUT_CHAR.mkdir(parents=True, exist_ok=True)
    OUT_UI.mkdir(parents=True, exist_ok=True)
    OUT_PREVIEW.parent.mkdir(parents=True, exist_ok=True)

    manifest = {
        "frameWidth": FW,
        "frameHeight": FH,
        "rows": DIRS,
        "walkCycle": [0, 1, 2, 1],
        "characters": {},
        "ui": {"status_bubbles": {"path": "ui/status_bubbles.png", "frameWidth": 16, "frameHeight": 16,
                                  "working": [0, 1, 2, 3], "error": 4, "done": 5, "thinking": 6, "idle": 7}},
    }
    sheets = []
    for cid, (name, pal, acc) in CHARACTERS.items():
        sheet = build_sheet(pal, acc)
        sheet.save(OUT_CHAR / f"{cid}.png")
        manifest["characters"][cid] = {"name": name, "path": f"characters/{cid}.png"}
        sheets.append(sheet)

    bubbles = build_status_bubbles()
    bubbles.save(OUT_UI / "status_bubbles.png")
    (OUT_CHAR.parent / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))

    # 預覽：所有角色並排 + 泡泡，放大 4 倍
    gap = 8
    w = sum(s.width for s in sheets) + gap * (len(sheets) + 1)
    h = FH * 4 + gap * 3 + bubbles.height
    prev = Image.new("RGBA", (w, h), (120, 170, 110, 255))
    x = gap
    for s in sheets:
        prev.alpha_composite(s, (x, gap))
        x += s.width + gap
    prev.alpha_composite(bubbles, (gap, FH * 4 + gap * 2))
    prev.resize((w * 4, h * 4), Image.NEAREST).save(OUT_PREVIEW)
    print(f"wrote {len(sheets)} character sheets, status bubbles, manifest, preview -> {OUT_PREVIEW}")


if __name__ == "__main__":
    main()
